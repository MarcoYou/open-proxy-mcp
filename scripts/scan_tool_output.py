# -*- coding: utf-8 -*-
"""도구 산출물에 엔진 내부 식별자가 새는지 도구별로 집계한다. DART 호출 0 — 결과 파일만 읽는다.

사용법: python3 scripts/scan_tool_output.py <live_results.json>
  입력: [{"tool": ..., "company": ..., "text": <마크다운 산출물>}, ...]

왜 있나 (260728): 눈으로 훑으면 놓친다. LG화학 proxy_advise 1건을 육안 검토했을 땐 6종만
보였는데 정규식으로 훑으니 95건이었고, 15개 도구로 넓히니 204건이었다.
`fy_current_revenue_krw`·`cmp_005930`·`registry_overlap` 같은 건 호출측 AI 가 다듬어줄 수
없는 종류의 결함이다 — 애초에 나오면 안 된다.

오탐 주의: 회사·학교 실명(adMarketplace·aSSIST), 공시 원문 인용의 「경업금지의무」,
큰 재무 수치(19,913,255)는 결함이 아니다. 이 스크립트는 그런 것을 이미 제외해 두었다.
"""
import json, re, sys
from collections import Counter, defaultdict

SKIP_SNAKE = {"dsaf001"}
# 사람용 이름 뒤 괄호로 병기한 도구·옵션 이름은 결함이 아니다 — 산출물의 독자는 사람이자
# **다음 호출을 고르는 AI** 다(260728 에이전트 2인 공통 지적). 「의결권 자문 도구
# (proxy_advise_before_meeting)」처럼 한글 이름과 함께 있을 때만 허용한다.
_INTENTIONAL = re.compile(
    r"[가-힣]\s*\((?:proxy_advise_before_meeting|treasury_share|order_contracts|risk_events|"
    r"business_details|financial_metrics|shareholder_meeting_results|include_details)[^)]*\)"
    r"|include_details 옵션")
CHECKS = {
    "내부식별자": lambda t: [m for m in re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b",
                                              _INTENTIONAL.sub(" ", t))
                        if m not in SKIP_SNAKE],
    "도구시그니처": lambda t: re.findall(r"[a-z_]{4,}\((?:fields|bsns_year|company|reprt_code|scope)=", t),
    "LLM지시": lambda t: re.findall(r"하지 마시오|하지 마세요|하지 말 것|LLM (?:주의|직접|분석)|READ BEFORE", t),
    "원시금액": lambda t: [m for m in re.findall(r"(?<![\d,\-/=])\d{10,}(?![\d,])", t)
                       if not re.fullmatch(r"\d{14}", m)],
    "과도한소수": lambda t: re.findall(r"\d+\.\d{4,}", t),
    "연도쉼표": lambda t: re.findall(r"\b[12],\d{3}년", t),
}


def broken(t):
    bad, expect = [], None
    for ln in t.splitlines():
        if not ln.startswith("|"):
            expect = None; continue
        n = ln.count("|")
        if expect is None: expect = n
        elif n != expect: bad.append(ln[:60])
    return bad


rows = json.loads(open(sys.argv[1], encoding="utf-8").read())
per = defaultdict(Counter); samples = defaultdict(lambda: defaultdict(set)); fail = Counter()
for r in rows:
    t, tool = r["text"], r["tool"]
    if "[JSONRPC-ERROR]" in t or "Traceback" in t:
        fail[tool] += 1; continue
    hits = {k: fn(t) for k, fn in CHECKS.items()}
    hits["표깨짐"] = broken(t)
    for k, v in hits.items():
        if v:
            per[tool][k] += len(v)
            samples[tool][k].update(list(dict.fromkeys(v))[:5])
print(f"{'도구':28s} {'자수(평균)':>10}  결함")
for tool, _ in sorted({r['tool']: 1 for r in rows}.items()):
    ts = [len(r["text"]) for r in rows if r["tool"] == tool and "[JSONRPC" not in r["text"]]
    avg = sum(ts)//len(ts) if ts else 0
    d = per[tool]
    mark = " · ".join(f"{k}{v}" for k, v in d.most_common()) or "✓ clean"
    if fail[tool]: mark += f"  (호출실패 {fail[tool]})"
    print(f"{tool:28s} {avg:>10,}  {mark}")
    for k in d:
        print(f"      └ {k}: {sorted(samples[tool][k])[:6]}")
print("\n총계:", dict(sum(per.values(), Counter())))
