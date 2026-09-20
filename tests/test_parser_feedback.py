"""평가기 자체의 실패 조건. 합성 DART 경계 입력이며 실제 시장 정답은 아니다."""
from __future__ import annotations

from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from scripts import parser_feedback as feedback


RECEIPT = "20260301000001"
PERSON = {"name": "홍길동", "birthDate": "1970.03.01", "roleType": "사내이사"}
# 정답은 아래 합성 원문에 독립적으로 적었다. 파서 출력으로 생성하지 않는다.
HTML = """<SECTION-2><TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY><SECTION-3><TITLE>□ 이사의 선임</TITLE>
<P>제2호 의안: 사내이사 홍길동 선임의 건</P>
<P>가. 후보자의 성명ㆍ생년월일ㆍ추천인ㆍ최대주주와의 관계</P>
<TABLE><TR><TH>후보자성명</TH><TH>생년월일</TH><TH>직위</TH></TR>
<TR><TD>홍길동</TD><TD>1970.03.01</TD><TD>사내이사</TD></TR></TABLE>
<P>나. 후보자의 주된직업ㆍ세부경력ㆍ해당법인과의 최근3년간 거래내역</P>
</SECTION-3></LIBRARY></SECTION-2>"""


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def corpus(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    raw = json.dumps({"html": HTML, "text": "합성 공시", "images": []}, ensure_ascii=False).encode()
    (cache / f"{RECEIPT}.json").write_bytes(raw)
    case = {
        "id": "synthetic-director", "rcept_no": RECEIPT, "company_group": "synthetic-a",
        "split": "development", "source_sha256": hashlib.sha256(raw).hexdigest(),
        "review": {"reviewer": "independent-fixture-author", "source_locator": "후보자 표 첫 행"},
        "expected": [deepcopy(PERSON)],
    }
    manifest = tmp_path / "manifest.json"
    save_json(manifest, {"schema_version": 1, "cases": [case]})
    return {"cache": cache, "raw": raw, "case": case, "manifest": manifest, "tmp": tmp_path}


def run(corpus, baseline=None):
    return feedback.evaluate(corpus["manifest"], corpus["cache"], baseline)


def rewrite(corpus, cases=None, **fields):
    value = {"schema_version": 1, "cases": cases if cases is not None else [corpus["case"]]}
    value.update(fields)
    save_json(corpus["manifest"], value)


def fake_parser(monkeypatch, candidates):
    monkeypatch.setattr(feedback, "_load_parser", lambda root: lambda html: {
        "appointments": [{"action": "선임", "candidates": deepcopy(candidates)}], "summary": {}})


def codes(report):
    return {e["code"] for e in report["errors"]} | {
        e["code"] for case in report["cases"] for e in case["errors"]}


@pytest.mark.parametrize("compressed", [False, True])
def test_real_production_parser_reads_boundary_and_preserves_input(corpus, compressed):
    path = corpus["cache"] / f"{RECEIPT}.json"
    if compressed:
        path.unlink()
        path = corpus["cache"] / f"{RECEIPT}.json.gz"
        path.write_bytes(gzip.compress(corpus["raw"]))
    before = path.read_bytes()
    report = run(corpus)
    assert report["status"] == "pass", report
    assert report["summary"]["passed"] == 1
    assert report["cases"][0]["actual_count"] == 1
    assert report["cases"][0]["observed_sha256"] == corpus["case"]["source_sha256"]
    assert report["network_attempts"] == 0
    assert report["summary"]["splits"]["holdout"]["cases"] == 0
    assert path.read_bytes() == before
    rendered = json.dumps(report, ensure_ascii=False)
    assert "<SECTION" not in rendered
    assert PERSON["name"] not in rendered


@pytest.mark.parametrize("actual,missing,extra", [
    ([], 1, 0), ([PERSON, PERSON], 0, 1),
    ([dict(PERSON, name="김길동")], 1, 1),
    ([dict(PERSON, birthDate="1970-03-01")], 1, 1),
    ([dict(PERSON, roleType="감사")], 1, 1),
    ([dict(PERSON, name="홍 길 동")], 1, 1),
])
def test_exact_multiset_not_name_only_or_set(corpus, monkeypatch, actual, missing, extra):
    fake_parser(monkeypatch, actual)
    report = run(corpus)
    assert report["status"] == "fail"
    result = report["cases"][0]
    assert result["status"] == "fail"
    assert result["missing_count"] == missing
    assert result["unexpected_count"] == extra
    assert "CANDIDATE_MISMATCH" in codes(report)


def test_multiset_preserves_expected_multiplicity_and_ignores_order(corpus, monkeypatch):
    auditor = dict(PERSON, roleType="감사위원")
    corpus["case"]["expected"] = [PERSON, PERSON, auditor]
    rewrite(corpus)
    fake_parser(monkeypatch, [auditor, PERSON, PERSON])
    assert run(corpus)["status"] == "pass"
    fake_parser(monkeypatch, [auditor, PERSON])
    assert run(corpus)["cases"][0]["missing_count"] == 1


def test_missing_optional_parser_fields_equal_explicit_gold_null(corpus, monkeypatch):
    corpus["case"]["expected"] = [{"name": "홍길동", "birthDate": None, "roleType": None}]
    rewrite(corpus)
    fake_parser(monkeypatch, [{"name": "홍길동", "career": "not part of contract"}])
    assert run(corpus)["status"] == "pass"


@pytest.mark.parametrize("problem,code", [
    ("missing", "CACHE_MISSING"), ("empty", "CACHE_EMPTY"),
    ("bad_json", "CACHE_JSON"), ("bad_gzip", "CACHE_READ"),
    ("duplicate", "CACHE_DUPLICATE"), ("hash", "SOURCE_HASH_MISMATCH"),
    ("no_html", "CACHE_SCHEMA"), ("empty_html", "CACHE_SCHEMA"),
    ("wrong_html", "CACHE_SCHEMA"), ("json_list", "CACHE_SCHEMA"),
    ("duplicate_key", "CACHE_JSON"), ("nan", "CACHE_JSON"),
    ("not_file", "CACHE_READ"),
])
def test_bad_cache_is_error_never_skip(corpus, problem, code):
    path = corpus["cache"] / f"{RECEIPT}.json"
    if problem == "missing":
        path.unlink()
    elif problem == "empty":
        path.write_bytes(b"")
    elif problem == "bad_json":
        path.write_bytes(b"{")
        corpus["case"]["source_sha256"] = hashlib.sha256(b"{").hexdigest()
        rewrite(corpus)
    elif problem == "bad_gzip":
        path.unlink()
        (corpus["cache"] / f"{RECEIPT}.json.gz").write_bytes(b"not gzip")
    elif problem == "duplicate":
        (corpus["cache"] / f"{RECEIPT}.json.gz").write_bytes(gzip.compress(corpus["raw"]))
    elif problem == "hash":
        path.write_bytes(corpus["raw"] + b"\n")
    elif problem == "not_file":
        path.unlink()
        path.mkdir()
    else:
        raw = {
            "no_html": b'{"text":"only text"}', "empty_html": b'{"html":"  "}',
            "wrong_html": b'{"html":42}', "json_list": b'[]',
            "duplicate_key": b'{"html":"first","html":"second"}',
            "nan": b'{"html":"x","other":NaN}',
        }[problem]
        path.write_bytes(raw)
        corpus["case"]["source_sha256"] = hashlib.sha256(raw).hexdigest()
        rewrite(corpus)
    report = run(corpus)
    assert report["status"] == "fail"
    assert report["summary"]["cases"] == 1
    assert report["summary"]["errors"] == 1
    assert code in codes(report)


@pytest.mark.parametrize("value", [[], None, {}, {"schema_version": True, "cases": []},
                                  {"schema_version": 1, "cases": []},
                                  {"schema_version": 2, "cases": []},
                                  {"schema_version": 1, "cases": "wrong"}])
def test_bad_top_level_manifest(corpus, value):
    save_json(corpus["manifest"], value)
    assert run(corpus)["status"] == "fail"


@pytest.mark.parametrize("raw", [b"", b"{", b'{"schema_version":1,"schema_version":1,"cases":[]}',
                                 b'{"schema_version":NaN,"cases":[]}'])
def test_bad_manifest_json(corpus, raw):
    corpus["manifest"].write_bytes(raw)
    report = run(corpus)
    assert report["status"] == "fail"
    assert "MANIFEST_READ" in codes(report)


@pytest.mark.parametrize("key,value", [
    ("id", ""), ("id", 1), ("rcept_no", "../../secrets"), ("rcept_no", 20260301000001),
    ("company_group", " "), ("split", "test"), ("source_sha256", "f"),
    ("expected", []), ("expected", None), ("expected", [PERSON, None]),
    ("expected", [dict(PERSON, name="")]), ("expected", [dict(PERSON, birthDate=1970)]),
    ("expected", [dict(PERSON, roleType={})]), ("expected", [{"name": "홍길동"}]),
    ("review", {}), ("review", {"reviewer": "", "source_locator": "table"}),
    ("review", {"reviewer": "reader", "source_locator": []}),
    ("typo_field", "x"),
])
def test_bad_case_schema_keeps_case_error(corpus, key, value):
    corpus["case"][key] = value
    rewrite(corpus)
    report = run(corpus)
    assert report["status"] == "fail"
    assert len(report["cases"]) == 1
    assert "CASE_SCHEMA" in codes(report)


def test_malformed_case_does_not_hide_good_case(corpus):
    rewrite(corpus, [None, corpus["case"]])
    report = run(corpus)
    assert report["summary"]["cases"] == 2
    assert report["summary"]["passed"] == 1
    assert report["summary"]["errors"] == 1


@pytest.mark.parametrize("problem,code", [
    ("id", "DUPLICATE_CASE_ID"), ("receipt", "DUPLICATE_RECEIPT"),
    ("split", "COMPANY_SPLIT_LEAKAGE"),
])
def test_duplicate_cases_and_company_split_leakage(corpus, problem, code):
    other = deepcopy(corpus["case"])
    other.update(id="other", rcept_no="20260301000002", company_group="synthetic-b")
    if problem == "id":
        other["id"] = corpus["case"]["id"]
    elif problem == "receipt":
        other["rcept_no"] = RECEIPT
    else:
        other.update(company_group="synthetic-a", split="holdout")
    rewrite(corpus, [corpus["case"], other])
    report = run(corpus)
    assert report["summary"]["errors"] == 2
    assert all(code in {e["code"] for e in c["errors"]} for c in report["cases"])


@pytest.mark.parametrize("payload", [None, [], {}, {"appointments": None},
    {"appointments": [None]}, {"appointments": [{}]},
    {"appointments": [{"candidates": None}]},
    {"appointments": [{"action": "선임", "candidates": [None]}]},
    {"appointments": [{"action": "선임", "candidates": [{"name": 42}]}]},
    {"appointments": [{"action": "선임", "candidates": [dict(PERSON, birthDate=[])]}]},
])
def test_parser_schema_errors(corpus, monkeypatch, payload):
    monkeypatch.setattr(feedback, "_load_parser", lambda root: lambda html: payload)
    assert "PARSER_SCHEMA" in codes(run(corpus))


def test_parser_exception_does_not_expose_exception_or_printed_body(corpus, monkeypatch, capsys):
    def explode(html):
        print(html)
        print("SECRET_SENTINEL", file=sys.stderr)
        raise ValueError("SECRET_SENTINEL")
    monkeypatch.setattr(feedback, "_load_parser", lambda root: explode)
    report = run(corpus)
    assert "PARSER_EXCEPTION" in codes(report)
    assert "SECRET_SENTINEL" not in json.dumps(report)
    captured = capsys.readouterr()
    assert not captured.out and not captured.err


@pytest.mark.parametrize("swallow", [False, True])
def test_network_blocked_even_if_parser_swallows_exception(corpus, monkeypatch, swallow):
    original = socket.socket
    def attempt(html):
        try:
            socket.getaddrinfo("example.invalid", 443)
        except Exception:
            if not swallow:
                raise
        return {"appointments": [{"action": "선임", "candidates": [PERSON]}]}
    monkeypatch.setattr(feedback, "_load_parser", lambda root: attempt)
    report = run(corpus)
    assert "NETWORK_BLOCKED" in codes(report)
    assert report["network_attempts"] == 1
    assert socket.socket is original


def test_socket_construction_and_import_are_guarded(corpus, monkeypatch):
    def importing(root):
        socket.socket()
    monkeypatch.setattr(feedback, "_load_parser", importing)
    report = run(corpus)
    assert report["network_attempts"] == 1
    assert "NETWORK_BLOCKED" in codes(report)


def test_baseline_regression_and_improvement(corpus, monkeypatch):
    baseline = corpus["tmp"] / "baseline.json"
    save_json(baseline, run(corpus))
    fake_parser(monkeypatch, [])
    after = run(corpus, baseline)
    assert after["comparison"]["compatible"] is True
    assert after["comparison"]["regressions"] == [corpus["case"]["id"]]
    assert after["summary"]["regressions"] == 1
    save_json(baseline, after)
    fake_parser(monkeypatch, [PERSON])
    improved = run(corpus, baseline)
    assert improved["status"] == "pass"
    assert improved["comparison"]["improvements"] == [corpus["case"]["id"]]


@pytest.mark.parametrize("problem", ["gold", "review", "hash", "drop", "duplicate", "status", "counts", "version", "fingerprint"])
def test_incompatible_or_corrupt_baseline_fails(corpus, problem):
    baseline = corpus["tmp"] / "baseline.json"
    before = run(corpus)
    if problem == "gold":
        corpus["case"]["expected"][0]["name"] = "김길동"
        rewrite(corpus)
    elif problem == "review":
        corpus["case"]["review"]["reviewer"] = "another-reader"
        rewrite(corpus)
    elif problem == "hash":
        before["cases"][0]["source_sha256"] = "a" * 64
    elif problem == "drop":
        before["cases"] = []
    elif problem == "duplicate":
        before["cases"].append(deepcopy(before["cases"][0]))
    elif problem == "status":
        before["cases"][0]["status"] = "skip"
    elif problem == "counts":
        before["summary"]["passed"] = 42
    elif problem == "version":
        before["report_schema_version"] = True
    else:
        before["manifest_sha256"] = "not-a-hash"
    save_json(baseline, before)
    report = run(corpus, baseline)
    assert report["status"] == "fail"
    assert report["comparison"]["compatible"] is False
    assert any(code.startswith("BASELINE_") for code in codes(report))


def test_cli_writes_private_report_nonzero_for_failed_case(corpus):
    corpus["case"]["expected"][0]["name"] = "김길동"
    rewrite(corpus)
    out = corpus["tmp"] / "private" / "report.json"
    command = [sys.executable, str(Path(feedback.__file__)), "--manifest", str(corpus["manifest"]),
               "--cache-dir", str(corpus["cache"]), "--out", str(out)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stderr
    report = json.loads(out.read_text())
    assert report["summary"]["failed"] == 1
    assert "<SECTION" not in result.stdout + result.stderr
    assert out.stat().st_mode & 0o077 == 0


def test_cli_empty_manifest_still_writes_failure_report(corpus):
    rewrite(corpus, [])
    out = corpus["tmp"] / "report.json"
    assert feedback.main(["--manifest", str(corpus["manifest"]), "--cache-dir", str(corpus["cache"]),
                          "--out", str(out)]) == 1
    assert json.loads(out.read_text())["status"] == "fail"


@pytest.mark.parametrize("target", ["manifest", "cache", "baseline", "repository"])
def test_cli_cannot_overwrite_input_or_write_report_into_repository(corpus, target):
    baseline = corpus["tmp"] / "baseline.json"
    save_json(baseline, run(corpus))
    out = {"manifest": corpus["manifest"], "cache": corpus["cache"] / f"{RECEIPT}.json",
           "baseline": baseline, "repository": Path(feedback.__file__).resolve().parent / "report.json"}[target]
    before = out.read_bytes() if out.exists() else None
    assert feedback.main(["--manifest", str(corpus["manifest"]), "--cache-dir", str(corpus["cache"]),
                          "--out", str(out), "--baseline", str(baseline)]) != 0
    assert (out.read_bytes() if out.exists() else None) == before


def test_dismissals_are_excluded_but_counted(corpus, monkeypatch):
    action = "해임"
    payload = {"appointments": [
        {"action": "선임", "candidates": [PERSON]},
        {"action": action, "candidates": [dict(PERSON, name="김선회"), PERSON]},
    ]}
    monkeypatch.setattr(feedback, "_load_parser", lambda root: lambda html: payload)
    report = run(corpus)
    assert report["status"] == "pass"
    case = report["cases"][0]
    assert case["actual_count"] == 1 and case["excluded_count"] == 2
    assert case["action_counts"][action] == {"appointments": 1, "candidates": 2}
    assert case["action_counts_complete"] is True
    assert report["summary"]["excluded_count"] == 2
    assert report["summary"]["action_counts"][action]["candidates"] == 2
    assert report["evaluation_scope"]["included_actions"] == ["선임", "재선임", "중임", "연임"]


@pytest.mark.parametrize("action", ["재선임", "중임", "연임"])
def test_renewal_candidates_are_included_without_changing_multiset(corpus, monkeypatch, action):
    payload = {"appointments": [{"action": action, "candidates": [PERSON]}]}
    monkeypatch.setattr(feedback, "_load_parser", lambda root: lambda html: payload)
    report = run(corpus)
    assert report["status"] == "pass"
    assert report["cases"][0]["actual_count"] == 1
    assert report["cases"][0]["excluded_count"] == 0
    assert report["cases"][0]["action_counts"][action] == {"appointments": 1, "candidates": 1}


@pytest.mark.parametrize("action", [None, "", "알수없음", 1, {}, [], True])
def test_unknown_action_is_not_silently_discarded(corpus, monkeypatch, action):
    payload = {"appointments": [{"action": action, "candidates": []}]}
    monkeypatch.setattr(feedback, "_load_parser", lambda root: lambda html: payload)
    report = run(corpus)
    assert "PARSER_ACTION_SCHEMA" in codes(report)
    assert report["cases"][0]["status"] == "error"
    assert report["summary"]["action_counts_incomplete_cases"] == 1


def test_malformed_excluded_candidate_is_still_schema_error(corpus, monkeypatch):
    payload = {"appointments": [{"action": "해임", "candidates": [None]}]}
    monkeypatch.setattr(feedback, "_load_parser", lambda root: lambda html: payload)
    assert "PARSER_SCHEMA" in codes(run(corpus))


def test_empty_parser_output_is_missing_candidates_not_success(corpus, monkeypatch):
    monkeypatch.setattr(feedback, "_load_parser", lambda root: lambda html: {"appointments": []})
    report = run(corpus)
    assert report["status"] == "fail"
    assert report["cases"][0]["missing_count"] == 1
    assert report["cases"][0]["action_counts_complete"] is True


@pytest.mark.parametrize("field", ["evaluation_scope", "evaluator", "harness_sha256"])
def test_baseline_evaluator_contract_must_match(corpus, field):
    baseline = corpus["tmp"] / "baseline.json"
    before = run(corpus)
    before[field] = "old-contract"
    save_json(baseline, before)
    report = run(corpus, baseline)
    assert report["status"] == "fail"
    assert report["comparison"]["compatible"] is False


def test_whitespace_and_object_key_order_do_not_change_manifest_fingerprint(corpus):
    baseline = corpus["tmp"] / "baseline.json"
    before = run(corpus)
    save_json(baseline, before)
    value = json.loads(corpus["manifest"].read_text())
    corpus["manifest"].write_text(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=4))
    after = run(corpus, baseline)
    assert after["status"] == "pass"
    assert after["manifest_sha256"] == before["manifest_sha256"]


@pytest.mark.parametrize("operation", ["constructor", "connect", "connect_ex", "sendto", "getaddrinfo"])
def test_offline_guard_covers_existing_socket_alias(corpus, monkeypatch, operation):
    old_socket_type = socket.socket
    sock = old_socket_type()
    def attempt(html):
        if operation == "constructor":
            socket.socket()
        elif operation == "getaddrinfo":
            socket.getaddrinfo("example.invalid", 443)
        elif operation == "sendto":
            sock.sendto(b"not-sent", ("127.0.0.1", 9))
        else:
            getattr(sock, operation)(("127.0.0.1", 9))
    monkeypatch.setattr(feedback, "_load_parser", lambda root: attempt)
    try:
        report = run(corpus)
    finally:
        sock.close()
    assert report["network_attempts"] == 1
    assert "NETWORK_BLOCKED" in codes(report)


def test_missing_code_root_is_explicit_case_error(corpus):
    report = feedback.evaluate(corpus["manifest"], corpus["cache"], code_root=corpus["tmp"] / "absent")
    assert "CODE_ROOT" in codes(report)
    assert report["status"] == "fail"


def test_cli_code_root_selects_source_in_fresh_process(corpus):
    alternate = corpus["tmp"] / "alternate-checkout"
    source = alternate / feedback.PARSER_PATH
    source.parent.mkdir(parents=True)
    (alternate / "open_proxy_mcp" / "__init__.py").write_text("")
    (alternate / "open_proxy_mcp" / "services" / "__init__.py").write_text("")
    # 파서 의존성 없이 코드 위치 선택만 검증하는 별도 프로세스용 더미 패키지.
    source.write_text('def parse_personnel_xml(html):\n    return {"appointments": []}\n')
    out = corpus["tmp"] / "alternate-report.json"
    result = subprocess.run([sys.executable, str(Path(feedback.__file__)),
        "--manifest", str(corpus["manifest"]), "--cache-dir", str(corpus["cache"]),
        "--code-root", str(alternate), "--out", str(out)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    report = json.loads(out.read_text())
    assert report["cases"][0]["status"] == "fail"  # import error가 아니라 선택한 파서의 빈 출력.
    assert report["code"]["root"] == str(alternate.resolve())
    assert report["code"]["parser_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_loaded_other_checkout_is_not_mixed(corpus):
    feedback._load_parser(feedback.ROOT)
    with pytest.raises(ImportError):
        feedback._load_parser(corpus["tmp"])


def test_source_change_during_run_invalidates_report(corpus, monkeypatch):
    identity = feedback._code_identity(feedback.ROOT)
    readings = iter([identity, dict(identity, parser_sha256="0" * 64)])
    monkeypatch.setattr(feedback, "_code_identity", lambda root: next(readings))
    report = run(corpus)
    assert report["cases"][0]["status"] == "pass"
    assert report["status"] == "fail"
    assert "CODE_CHANGED" in codes(report)
