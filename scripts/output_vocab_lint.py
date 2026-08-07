#!/usr/bin/env python3
"""산출물 어휘 lint — 엔진 내부 용어가 사용자 문서로 새는 것을 막는다.

wiki `tools/proxy_advise_before_meeting.md` 「산출물 표기 규칙」:
    산출물은 **사람이 읽는 문서**다 — 엔진 내부 식별자는 나오지 않는다.

규칙은 있었는데 강제 장치가 없어서 지켜지지 않았다(260808 전수 스윕: 127건). 그리고 이건 용어를
잘못 고른 문제가 아니다 — **어휘 경계가 계층으로 없어서** 생긴다. facts 라벨 사전(93개)을 거치는
필드는 유출이 0인데, 판정 사유·표 헤더처럼 코드가 한글 문장을 직접 쓰는 자리에서만 샌다.

**래칫 방식**: 지금 남은 건수를 파일별로 박아두고(BASELINE), **늘면 실패**한다. 고치면 baseline 을
내리라고 알려준다. 그래서 127건을 다 고치기 전에도 재발이 멈춘다.

사용:
    python3 scripts/output_vocab_lint.py            # 현황
    python3 scripts/output_vocab_lint.py --detail   # 유출 문자열 전체
    python3 scripts/output_vocab_lint.py --baseline # baseline 갱신용 dict 출력
"""
from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "open_proxy_mcp"
BASELINE_PATH = Path(__file__).resolve().parent.parent / "tests" / "output_vocab_baseline.json"

#: 사용자가 아는 도메인 약어 — 유출이 아니다.
ALLOWED = {
    "dart", "opm", "krx", "kind", "esg", "roe", "roa", "eps", "bps", "per", "pbr",
    "ebitda", "fcf", "cagr", "yoy", "qoq", "ifrs", "kospi", "kosdaq", "reit", "reits",
    "cb", "bw", "eb", "rcps", "rsu", "ipo", "ai", "llm", "pdf", "xml", "url", "csr",
    "kam", "vs", "ok", "no", "id", "ceo", "cfo", "esop", "k-vote", "fy",
}
#: 엔진 내부 용어 — 사용자 문서에 나오면 안 된다.
BANNED = {
    "raw", "clean", "detect", "hit", "fallback", "layer", "scope", "payload", "status",
    "flag", "signal", "threshold", "sub-factor", "sub_factor", "reference", "detail",
    "default", "logic", "enum", "parse", "parsing", "cache", "upstream", "override",
    "none", "null", "true", "false", "dict", "key", "field", "param", "red_flag",
    "case_by_case", "no_data", "review", "against", "normal", "conditional",
    "alternative", "procedural", "withdrawn", "partial", "full", "summary", "yearly",
    "roster", "tenure", "eval", "schema", "index", "meta", "tag", "score", "match",
    "filter", "gate", "rung", "span", "diff", "stub", "todo", "fixme",
    # 「회계 risk 이력」처럼 한글 문장에 그대로 박혀 나가던 것 — 우리말이 있는데 영문을 쓸 이유가 없다.
    "risk", "strict", "overlap", "detect", "trigger", "band", "note", "ref",
}
#: 로그·예외는 사용자에게 안 나간다. **먼저 걸러야 한다** — 안 그러면 「sqlite master load 실패」
#: 같은 내부 로그가 유출로 잡혀 숫자가 부풀고, 그 숫자로 잘못된 결론을 내게 된다(260808 실제 오측정).
NOT_USER_FACING = (
    "logger.", "log.debug", "log.info", "log.warning", "log.error",
    "print(", "raise ", "warnings.warn", "logging.",
    # `next_actions` 는 호출측 LLM 채널이다. 렌더러가 사람에게 찍는 곳은 law_lookup 하나뿐이고,
    # 거기 담긴 `scope="metrics"` 같은 건 **API 계약 그 자체**라 한글로 바꾸면 쓸모가 없어진다
    # (모델이 그걸 읽고 다음 호출을 만든다). 규칙 대상에서 뺀다 — 다만 이 자리에도 사람이 읽을
    # 문장을 쓰지 않도록, 파라미터 안내 외의 산문은 넣지 말 것.
    "actions.append", "next_actions",
)
HANGUL = re.compile(r"[가-힣]")
TOKEN = re.compile(r"[A-Za-z][A-Za-z_\-]*")


def _non_docstring_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """docstring 이 아닌 문자열만. docstring 은 LLM·개발자 채널이라 규칙 대상이 아니다."""
    docs: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            docs.add(id(body[0].value))
    return [
        (n.lineno, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
    ]


def scan_file(path: Path) -> list[tuple[int, str, list[str]]]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return []
    lines = source.split("\n")
    out = []
    for lineno, text in _non_docstring_literals(tree):
        if not HANGUL.search(text):
            continue  # 한글이 없으면 사용자에게 보이는 문장이 아니다
        if any(m in ln for ln in lines[max(0, lineno - 4):lineno] for m in NOT_USER_FACING):
            continue
        # 백틱 안은 **식별자를 인용한 것**이다(`scope="detail"` 로 원문 확인). 파라미터 이름을
        # 한글로 바꾸면 안내가 쓸모없어진다 — 산문에 녹아든 것(「scope을 individually 호출」)만 잡는다.
        prose = re.sub(r"`[^`]*`", " ", text)
        bad = sorted({t.lower() for t in TOKEN.findall(prose)
                      if t.lower() in BANNED and t.lower() not in ALLOWED})
        if bad:
            out.append((lineno, " ".join(text.split())[:120], bad))
    return out


def scan_all() -> dict[str, list[tuple[int, str, list[str]]]]:
    found: dict[str, list] = {}
    for path in sorted(ROOT.rglob("*.py")):
        if "_archive" in path.parts:
            continue
        hits = scan_file(path)
        if hits:
            found[str(path.relative_to(ROOT))] = hits
    return found


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def check() -> list[str]:
    """baseline 대비 **늘어난** 파일만 실패로 본다(래칫)."""
    current = {f: len(h) for f, h in scan_all().items()}
    baseline = load_baseline()
    problems = []
    for f, n in sorted(current.items()):
        allowed = baseline.get(f, 0)
        if n > allowed:
            problems.append(f"{f}: 유출 {n}건 (허용 {allowed}건) — 새 문장에 엔진 내부 용어가 들어갔습니다")
    for f, allowed in sorted(baseline.items()):
        n = current.get(f, 0)
        if n < allowed:
            problems.append(f"{f}: 유출 {n}건으로 줄었습니다 — baseline 을 {n}로 낮추세요(래칫 유지)")
    return problems


def main() -> None:
    hits = scan_all()
    total = sum(len(v) for v in hits.values())
    terms: dict[str, int] = defaultdict(int)
    for v in hits.values():
        for _, _, bad in v:
            for b in bad:
                terms[b] += 1

    if "--baseline" in sys.argv:
        print(json.dumps({f: len(v) for f, v in sorted(hits.items())}, indent=2, ensure_ascii=False))
        return

    print(f"■ 사용자 문서에 새는 엔진 용어 {total}건 · 파일 {len(hits)}개 · 용어 {len(terms)}종\n")
    for f, v in sorted(hits.items(), key=lambda x: -len(x[1])):
        print(f"  {len(v):>4}  {f}")
    print("\n■ 용어별")
    for t, n in sorted(terms.items(), key=lambda x: -x[1]):
        print(f"  {n:>4}  {t}")

    if "--detail" in sys.argv:
        print("\n■ 상세")
        for f, v in sorted(hits.items(), key=lambda x: -len(x[1])):
            print(f"\n── {f}")
            for lineno, text, bad in v:
                print(f"  {lineno:>5} [{','.join(bad)}] {text}")

    problems = check()
    if problems:
        print("\n■ 래칫 위반")
        for p in problems:
            print(f"  ✗ {p}")
        sys.exit(1)


if __name__ == "__main__":
    main()
