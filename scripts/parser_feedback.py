#!/usr/bin/env python3
"""고정된 DART 응답과 독립 정답으로 인사 후보 파서를 오프라인 회귀 평가한다.

수집·정답 생성·파서 수정·배포는 하지 않는다. 운영 방법은 docs/PARSER_FEEDBACK.md.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import gzip
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
from unittest.mock import patch
import zlib


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("name", "birthDate", "roleType")
SPLITS = ("development", "holdout")
EVALUATOR = "personnel-appointment-candidates-v2"
INCLUDED_ACTIONS = ("선임", "재선임", "중임", "연임")
KNOWN_ACTIONS = ("선임", "해임", "재선임", "중임", "연임")
PARSER_PATH = Path("open_proxy_mcp/services/shareholder_meeting_parser.py")
HASH = re.compile(r"[0-9a-f]{64}\Z")
RECEIPT = re.compile(r"[0-9]{14}\Z")
CASE_KEYS = {"id", "rcept_no", "company_group", "split", "source_sha256", "review", "expected"}

# 오류에는 외부 예외 메시지·원문·경로·정답을 넣지 않는다. 이 고정 문구만 공개한다.
MESSAGES = {
    "MANIFEST_READ": "평가 목록 파일을 읽을 수 없거나 엄격한 JSON 형식이 아닙니다.",
    "MANIFEST_SCHEMA": "평가 목록의 구조 또는 버전이 올바르지 않습니다.",
    "MANIFEST_EMPTY": "평가 대상이 비어 있습니다.",
    "CASE_SCHEMA": "사례의 필수 필드·형식 또는 독립 검토 기록이 올바르지 않습니다.",
    "DUPLICATE_CASE_ID": "평가 목록에서 사례 식별자가 중복되었습니다.",
    "DUPLICATE_RECEIPT": "평가 목록에서 공시 접수번호가 중복되었습니다.",
    "COMPANY_SPLIT_LEAKAGE": "같은 회사 그룹이 개발 표본과 별도 검증 표본에 겹칩니다.",
    "CACHE_MISSING": "지정된 접수번호의 응답 캐시가 없습니다. 수집으로 대체하지 않습니다.",
    "CACHE_DUPLICATE": "같은 접수번호의 압축본과 비압축본이 함께 있습니다.",
    "CACHE_READ": "캐시를 읽거나 압축을 풀 수 없습니다.",
    "CACHE_EMPTY": "압축을 푼 캐시 파일이 비어 있습니다.",
    "CACHE_JSON": "캐시가 엄격한 JSON 형식이 아닙니다.",
    "CACHE_SCHEMA": "캐시에는 비어 있지 않은 html 문자열이 필요합니다.",
    "SOURCE_HASH_MISMATCH": "압축을 푼 원본 바이트의 해시가 고정된 정답 목록과 다릅니다.",
    "PARSER_IMPORT": "현재 작업 폴더의 운영 파서를 불러오지 못했습니다.",
    "CODE_ROOT": "지정한 코드 폴더에서 운영 파서 소스를 읽을 수 없습니다.",
    "CODE_CHANGED": "평가 도중 운영 파서 소스가 바뀌었습니다. 고정된 코드로 다시 실행하세요.",
    "PARSER_EXCEPTION": "운영 파서 실행 중 예외가 발생했습니다. 예외 본문은 기록하지 않습니다.",
    "PARSER_SCHEMA": "운영 파서의 후보 목록 구조 또는 필드 형식이 올바르지 않습니다.",
    "PARSER_ACTION_SCHEMA": "인사 안건의 동작이 없거나 알려진 동작 목록에 없습니다.",
    "NETWORK_BLOCKED": "평가 중 네트워크 사용 시도가 차단되었습니다.",
    "CANDIDATE_MISMATCH": "후보의 이름·생년월일·역할 또는 등장 횟수가 독립 정답과 다릅니다.",
    "BASELINE_READ": "이전 보고서를 읽을 수 없거나 엄격한 JSON 형식이 아닙니다.",
    "BASELINE_SCHEMA": "이전 보고서의 구조·버전·판정 또는 집계가 올바르지 않습니다.",
    "BASELINE_INCOMPATIBLE": "이전 보고서와 평가 목록 지문 또는 사례·원문 해시 집합이 다릅니다.",
}


class InputError(ValueError):
    """외부 입력 내용을 노출하지 않는 고정 오류 코드."""


class NetworkForbidden(RuntimeError):
    pass


def _error(code):
    return {"code": code, "message": MESSAGES[code]}


def _nonempty(value):
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _sha(value):
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def _receipt(value):
    return isinstance(value, str) and RECEIPT.fullmatch(value) is not None


def _pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON key")
        obj[key] = value
    return obj


def _constant(value):
    raise ValueError("non-finite JSON number")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite JSON number")
    return result


def _decode(raw, code):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=_constant, parse_float=_float)
    except (ValueError, RecursionError):
        raise InputError(code) from None


def _read_json(path, code):
    try:
        return _decode(Path(path).read_bytes(), code)
    except OSError:
        raise InputError(code) from None


def _fingerprint(manifest):
    # JSON의 서식·키 순서만 제외한다. 배열 순서·정답·검토 기록 변경은 별도 버전이다.
    data = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    try:
        return hashlib.sha256(data.encode("utf-8")).hexdigest()
    except (UnicodeError, RecursionError):
        raise InputError("MANIFEST_SCHEMA") from None


def _candidate(candidate, *, expected):
    if not isinstance(candidate, dict) or (expected and set(candidate) != set(FIELDS)):
        raise ValueError("candidate schema")
    if not _nonempty(candidate.get("name")):
        raise ValueError("candidate name")
    for key in FIELDS[1:]:
        value = candidate.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError("candidate field type")
    # 정규화·중복 제거 없이 공백, 날짜 구분자, 역할 원문 표기까지 그대로 비교한다.
    return tuple(candidate.get(key) for key in FIELDS)


def _valid_case(case):
    if not isinstance(case, dict) or set(case) != CASE_KEYS:
        return False
    if not all(_nonempty(case[k]) for k in ("id", "company_group")):
        return False
    if not _receipt(case["rcept_no"]) or not _sha(case["source_sha256"]) or case["split"] not in SPLITS:
        return False
    review = case["review"]
    if not isinstance(review, dict) or set(review) != {"reviewer", "source_locator"}:
        return False
    if not all(_nonempty(v) for v in review.values()):
        return False
    expected = case["expected"]
    if not isinstance(expected, list) or not expected:
        return False
    try:
        for candidate in expected:
            _candidate(candidate, expected=True)
    except ValueError:
        return False
    return True


def _new_result(index, case):
    # 검증이 끝나지 않은 입력을 보고서에 그대로 복사하지 않는다.
    valid = _valid_case(case)
    return {
        "index": index, "id": case["id"] if valid else None,
        "rcept_no": case["rcept_no"] if valid else None,
        "split": case["split"] if valid else None,
        "source_sha256": case["source_sha256"] if valid else None,
        "observed_sha256": None, "status": "error", "errors": [],
        "expected_count": len(case["expected"]) if valid else None,
        "actual_count": None, "matched_count": None, "missing_count": None, "unexpected_count": None,
        "excluded_count": None, "action_counts": None, "action_counts_complete": False,
    }


def _validate_manifest(manifest, report):
    if not isinstance(manifest, dict):
        report["errors"].append(_error("MANIFEST_SCHEMA"))
        return []
    top_valid = (set(manifest) == {"schema_version", "cases"}
                 and type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1
                 and isinstance(manifest.get("cases"), list))
    if not top_valid:
        report["errors"].append(_error("MANIFEST_SCHEMA"))
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        return []
    if not cases:
        report["errors"].append(_error("MANIFEST_EMPTY"))
    report["cases"] = [_new_result(i, case) for i, case in enumerate(cases)]
    for case, result in zip(cases, report["cases"]):
        if not _valid_case(case):
            result["errors"].append(_error("CASE_SCHEMA"))
        elif not top_valid:
            result["errors"].append(_error("MANIFEST_SCHEMA"))
    valid_pairs = [(case, result) for case, result in zip(cases, report["cases"]) if not result["errors"]]
    for field, code in (("id", "DUPLICATE_CASE_ID"), ("rcept_no", "DUPLICATE_RECEIPT")):
        counts = Counter(case[field] for case, _ in valid_pairs)
        for case, result in valid_pairs:
            if counts[case[field]] > 1:
                result["errors"].append(_error(code))
    groups = {}
    for case, _ in valid_pairs:
        groups.setdefault(case["company_group"], set()).add(case["split"])
    for case, result in valid_pairs:
        if len(groups[case["company_group"]]) > 1:
            result["errors"].append(_error("COMPANY_SPLIT_LEAKAGE"))
    if top_valid:
        report["manifest_sha256"] = _fingerprint(manifest)
    return cases


@contextmanager
def _offline():
    """이 독립 실행 프로세스의 Python 소켓·DNS를 차단하고 삼킨 시도도 센다."""
    state = {"attempts": 0}
    original_socket = socket.socket

    def deny(*args, **kwargs):
        state["attempts"] += 1
        raise NetworkForbidden("offline evaluation")

    class BlockedSocket(original_socket):
        def __new__(cls, *args, **kwargs):
            return deny()

    with ExitStack() as stack:
        # 이미 import된 별칭·소켓도 연결/전송할 수 없게 원 클래스 메서드까지 막는다.
        for name in ("connect", "connect_ex", "send", "sendall", "sendto", "sendmsg", "bind", "listen"):
            if hasattr(original_socket, name):
                stack.enter_context(patch.object(original_socket, name, deny))
        for name in ("create_connection", "create_server", "socketpair", "fromfd", "fromshare",
                     "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo"):
            if hasattr(socket, name):
                stack.enter_context(patch.object(socket, name, deny))
        stack.enter_context(patch.object(socket, "socket", BlockedSocket))
        yield state


@contextmanager
def _quiet_parser():
    # 파서/의존성의 진단 출력에도 원문·키가 있을 수 있어 외부로 내보내지 않는다.
    previous = logging.root.manager.disable
    logging.disable(sys.maxsize)
    try:
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
            yield
    finally:
        logging.disable(previous)


def _load_parser(code_root):
    # 서로 다른 checkout을 같은 Python 프로세스에서 섞지 않는다.
    for name, loaded in list(sys.modules.items()):
        if name == "open_proxy_mcp" or name.startswith("open_proxy_mcp."):
            filename = getattr(loaded, "__file__", None)
            if filename and not Path(filename).resolve().is_relative_to(code_root):
                raise ImportError("parser checkout mismatch; use a fresh process")
    sys.path.insert(0, str(code_root))
    from open_proxy_mcp.services.shareholder_meeting_parser import parse_personnel_xml
    # 다른 checkout의 editable install을 실수로 평가하지 않는다.
    module = sys.modules[parse_personnel_xml.__module__]
    if Path(module.__file__).resolve() != (code_root / PARSER_PATH).resolve():
        raise ImportError("parser checkout mismatch")
    return parse_personnel_xml


def _code_identity(code_root):
    path = code_root / PARSER_PATH
    try:
        if not path.resolve().is_relative_to(code_root):
            raise InputError("CODE_ROOT")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise InputError("CODE_ROOT") from None
    return {"root": str(code_root), "parser_path": PARSER_PATH.as_posix(),
            "parser_sha256": digest, "python_version": sys.version.split()[0]}


def _cache_document(cache_dir, case, result):
    root = Path(cache_dir)
    choices = [root / (case["rcept_no"] + ext) for ext in (".json", ".json.gz")]
    # 깨진 심링크·디렉터리도 '없음'으로 건너뛰지 않고 읽기 실패로 노출한다.
    try:
        present = [path for path in choices if path.exists() or path.is_symlink()]
    except OSError:
        raise InputError("CACHE_READ") from None
    if not present:
        raise InputError("CACHE_MISSING")
    if len(present) != 1:
        raise InputError("CACHE_DUPLICATE")
    path = present[0]
    try:
        raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    except (OSError, EOFError, ValueError, zlib.error):
        raise InputError("CACHE_READ") from None
    if not raw:
        raise InputError("CACHE_EMPTY")
    result["observed_sha256"] = hashlib.sha256(raw).hexdigest()
    if result["observed_sha256"] != case["source_sha256"]:
        raise InputError("SOURCE_HASH_MISMATCH")
    document = _decode(raw, "CACHE_JSON")
    if not isinstance(document, dict) or not _nonempty(document.get("html")):
        raise InputError("CACHE_SCHEMA")
    return document


def _extract_candidates(payload, result):
    if not isinstance(payload, dict) or not isinstance(payload.get("appointments"), list):
        raise InputError("PARSER_SCHEMA")
    actions = {action: {"appointments": 0, "candidates": 0} for action in KNOWN_ACTIONS}
    result.update(action_counts=actions, excluded_count=0)
    candidates = []
    for appointment in payload["appointments"]:
        if not isinstance(appointment, dict) or not isinstance(appointment.get("candidates"), list):
            raise InputError("PARSER_SCHEMA")
        action = appointment.get("action")
        if action not in KNOWN_ACTIONS:
            raise InputError("PARSER_ACTION_SCHEMA")
        actions[action]["appointments"] += 1
        for candidate in appointment["candidates"]:
            try:
                value = _candidate(candidate, expected=False)
            except ValueError:
                raise InputError("PARSER_SCHEMA") from None
            actions[action]["candidates"] += 1
            if action in INCLUDED_ACTIONS:
                candidates.append(value)
            else:
                result["excluded_count"] += 1
    result["action_counts_complete"] = True
    return Counter(candidates)


def _evaluate_case(case, result, cache_dir, parser, network):
    before = network["attempts"]
    try:
        document = _cache_document(cache_dir, case, result)
        try:
            with _quiet_parser():
                payload = parser(document["html"])
        except Exception:
            raise InputError("PARSER_EXCEPTION") from None
        actual = _extract_candidates(payload, result)
        expected = Counter(_candidate(c, expected=True) for c in case["expected"])
        result.update(actual_count=sum(actual.values()), matched_count=sum((expected & actual).values()),
                      missing_count=sum((expected - actual).values()),
                      unexpected_count=sum((actual - expected).values()))
        result["status"] = "pass" if actual == expected else "fail"
        if result["status"] == "fail":
            result["errors"].append(_error("CANDIDATE_MISMATCH"))
    except InputError as exc:
        result["errors"].append(_error(exc.args[0]))
    finally:
        if network["attempts"] != before:
            result["status"] = "error"
            result["errors"].append(_error("NETWORK_BLOCKED"))


def _counts(cases):
    return {"cases": len(cases), "passed": sum(c["status"] == "pass" for c in cases),
            "failed": sum(c["status"] == "fail" for c in cases),
            "errors": sum(c["status"] == "error" for c in cases)}


def _baseline_case_valid(case):
    if (not isinstance(case, dict) or not _nonempty(case.get("id"))
            or not _receipt(case.get("rcept_no")) or not _sha(case.get("source_sha256"))
            or case.get("split") not in SPLITS or case.get("status") not in ("pass", "fail", "error")
            or not isinstance(case.get("errors"), list)
            or type(case.get("expected_count")) is not int or case["expected_count"] < 1):
        return False
    errors = case["errors"]
    if any(not isinstance(e, dict) or not isinstance(e.get("code"), str) or e["code"] not in MESSAGES for e in errors):
        return False
    actual_fields = ("actual_count", "matched_count", "missing_count", "unexpected_count")
    observed = case.get("observed_sha256")
    if observed is not None and not _sha(observed):
        return False
    if all(case.get(k) is None for k in actual_fields):
        return case["status"] == "error" and bool(errors)
    if any(type(case.get(k)) is not int or case[k] < 0 for k in actual_fields):
        return False
    if (case["matched_count"] + case["missing_count"] != case["expected_count"]
            or case["matched_count"] + case["unexpected_count"] != case["actual_count"]):
        return False
    if case["status"] == "error":
        return bool(errors)
    if observed != case["source_sha256"]:
        return False
    if case["status"] == "pass":
        return not errors and case["missing_count"] == case["unexpected_count"] == 0
    return (case["missing_count"] + case["unexpected_count"] > 0
            and any(e["code"] == "CANDIDATE_MISMATCH" for e in errors))


def _compare_baseline(report, baseline_path):
    comparison = report["comparison"]
    comparison.update(requested=True, compatible=False)
    try:
        baseline = _read_json(baseline_path, "BASELINE_READ")
        if (not isinstance(baseline, dict) or type(baseline.get("report_schema_version")) is not int
                or baseline["report_schema_version"] != 1 or baseline.get("evaluator") != EVALUATOR
                or baseline.get("evaluation_scope") != report["evaluation_scope"]
                or not _sha(baseline.get("harness_sha256"))
                or not _sha(baseline.get("manifest_sha256"))
                or not isinstance(baseline.get("cases"), list) or not baseline["cases"]
                or not isinstance(baseline.get("summary"), dict)):
            raise InputError("BASELINE_SCHEMA")
        previous = {}
        receipts = set()
        for case in baseline["cases"]:
            if (not _baseline_case_valid(case) or case["id"] in previous or case["rcept_no"] in receipts):
                raise InputError("BASELINE_SCHEMA")
            previous[case["id"]] = case
            receipts.add(case["rcept_no"])
        for key, value in _counts(baseline["cases"]).items():
            if type(baseline["summary"].get(key)) is not int or baseline["summary"][key] != value:
                raise InputError("BASELINE_SCHEMA")
        inventory = lambda cases: {(c["id"], c["rcept_no"], c["source_sha256"], c["split"]) for c in cases}
        if (baseline["manifest_sha256"] != report["manifest_sha256"]
                or baseline["harness_sha256"] != report["harness_sha256"]
                or inventory(baseline["cases"]) != inventory(report["cases"])):
            raise InputError("BASELINE_INCOMPATIBLE")
        comparison["compatible"] = True
        for case in report["cases"]:
            old_status = previous[case["id"]]["status"]
            if old_status == "pass" and case["status"] != "pass":
                comparison["regressions"].append(case["id"])
            elif old_status != "pass" and case["status"] == "pass":
                comparison["improvements"].append(case["id"])
    except InputError as exc:
        report["errors"].append(_error(exc.args[0]))


def evaluate(manifest_path, cache_dir, baseline_path=None, *, code_root=None):
    """보고서를 반환한다. 읽기 전용이며 소켓 차단은 파서 import 이전부터 유지한다."""
    report = {
        "report_schema_version": 1, "evaluator": EVALUATOR, "manifest_sha256": None,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "status": "fail", "network_attempts": 0, "errors": [], "cases": [], "code": None,
        "evaluation_scope": {"included_actions": list(INCLUDED_ACTIONS), "known_actions": list(KNOWN_ACTIONS),
                             "candidate_fields": list(FIELDS)},
        "comparison": {"requested": False, "compatible": None, "regressions": [], "improvements": []},
    }
    code_root = Path(code_root or ROOT).resolve()
    with _offline() as network:
        try:
            cases = _validate_manifest(_read_json(manifest_path, "MANIFEST_READ"), report)
        except InputError as exc:
            report["errors"].append(_error(exc.args[0]))
            cases = []
        pending = [(case, result) for case, result in zip(cases, report["cases"]) if not result["errors"]]
        if pending:
            before = network["attempts"]
            try:
                report["code"] = _code_identity(code_root)
                with _quiet_parser():
                    parser = _load_parser(code_root)
                if network["attempts"] != before:
                    raise NetworkForbidden()
            except InputError as exc:
                for _, result in pending:
                    result["errors"].append(_error(exc.args[0]))
            except Exception:
                code = "NETWORK_BLOCKED" if network["attempts"] != before else "PARSER_IMPORT"
                for _, result in pending:
                    result["errors"].append(_error(code))
            else:
                for case, result in pending:
                    _evaluate_case(case, result, cache_dir, parser, network)
                try:
                    if _code_identity(code_root) != report["code"]:
                        report["errors"].append(_error("CODE_CHANGED"))
                except InputError:
                    report["errors"].append(_error("CODE_CHANGED"))
        report["network_attempts"] = network["attempts"]
        if baseline_path is not None:
            _compare_baseline(report, baseline_path)
    summary = _counts(report["cases"])
    summary.update(global_errors=len(report["errors"]), regressions=len(report["comparison"]["regressions"]),
                   unmeasured_cases=sum(c["actual_count"] is None for c in report["cases"]),
                   splits={split: _counts([c for c in report["cases"] if c["split"] == split]) for split in SPLITS})
    for field in ("expected_count", "actual_count", "matched_count", "missing_count", "unexpected_count", "excluded_count"):
        summary[field] = sum(c[field] or 0 for c in report["cases"])
    summary["action_counts"] = {
        action: {key: sum((c["action_counts"] or {}).get(action, {}).get(key, 0) for c in report["cases"])
                 for key in ("appointments", "candidates")} for action in KNOWN_ACTIONS}
    summary["action_counts_incomplete_cases"] = sum(not c["action_counts_complete"] for c in report["cases"])
    report["summary"] = summary
    if summary["cases"] and summary["passed"] == summary["cases"] and not report["errors"]:
        report["status"] = "pass"
    return report


def _write_report(out, report, manifest, cache_dir, baseline, code_root):
    path = Path(out).resolve()
    inputs = {Path(manifest).resolve()}
    if baseline is not None:
        inputs.add(Path(baseline).resolve())
    if (path in inputs or path.is_relative_to(Path(cache_dir).resolve()) or path.is_relative_to(ROOT)
            or path.is_relative_to(Path(code_root).resolve())):
        raise ValueError("unsafe report destination")
    path.parent.mkdir(parents=True, exist_ok=True)
    # 완료된 보고서만 보이게 교체하고 기본 권한을 소유자 읽기/쓰기로 제한한다.
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temp_name = stream.name
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path, help="독립 원문 검토 정답 JSON")
    parser.add_argument("--cache-dir", required=True, type=Path, help="DART 응답 .json/.json.gz 폴더")
    parser.add_argument("--out", required=True, type=Path, help="공개 저장소 밖의 비공개/임시 보고서 경로")
    parser.add_argument("--baseline", type=Path, help="같은 평가 목록의 이전 보고서")
    parser.add_argument("--code-root", type=Path, default=ROOT,
                        help="평가할 OPM 저장소 경로. 기본값은 하네스가 속한 저장소")
    args = parser.parse_args(argv)
    report = evaluate(args.manifest, args.cache_dir, args.baseline, code_root=args.code_root)
    try:
        _write_report(args.out, report, args.manifest, args.cache_dir, args.baseline, args.code_root)
    except (OSError, ValueError):
        print("보고서 저장 실패: 비공개 경로·권한·입력 파일과의 중복을 확인하세요.", file=sys.stderr)
        return 2
    counts = report["summary"]
    print(json.dumps({"status": report["status"], **{k: counts[k] for k in
                     ("cases", "passed", "failed", "errors", "global_errors", "regressions")}}, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
