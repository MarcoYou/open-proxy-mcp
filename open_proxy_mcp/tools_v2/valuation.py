"""v2 valuation public tool — DART(공시)+KRX(공식시세) 상대가치 배수."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.valuation import build_valuation_payload

_STATUS_TITLE = {
    "invalid": "입력 오류",
    "not_found": "조회 결과 없음",
    "unlisted": "비상장 — 시장배수 산출 불가",
    "no_financials": "재무 데이터 미확정",
}


def _render_status(payload: dict[str, Any]) -> str:
    """ok가 아닌 상태(invalid/not_found/unlisted/no_financials) 렌더."""
    status = payload.get("status", "error")
    title = _STATUS_TITLE.get(status, status)
    lines = [f"# valuation: {payload.get('subject', '')} — {title}", ""]
    for w in payload.get("warnings", []):
        lines.append(f"- {w}")
    if not payload.get("warnings"):
        lines.append(f"- status=`{status}`")
    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def valuation(company: str, format: str = "md") -> str:
        """desc: DART(공시)+KRX(공식시세) 상대가치 배수 — PER(FY0·TTM)·PBR(MRQ)·배당수익률. 한국 표준(연결, 지배주주 귀속). 비KRW 기능통화 자동 KRW 환산(한국은행 ECOS 매매기준율). 스케일오류 가드 + N/M 게이팅 + 식별 status 4단.
        when: 밸류에이션·상대가치("PER/PBR 얼마"·"싼가 비싼가"·"배당수익률")·주가 대비 재무 배수. 재무 펀더멘탈 자체(수익성·현금흐름·듀퐁·회계risk)는 financial_metrics, 배당 상세는 dividend.
        rule: multiples = 지배주주 귀속 기준. EPS(FY0)=DART 공시 기본주당이익(가중평균 주식수·우선주 배분 반영, 없으면 지배순이익÷보통주 폴백), EPS(TTM)=TTM 지배순이익÷보통주(시점), BPS=지배자본(MRQ 우선)÷합계주식수. TTM=연간+1Q당해−1Q전년. ⚠ EPS(FY0)와 EPS(TTM)은 분모 기준이 달라 두 PER 직접비교는 주의. PER/PBR/배당수익률 분모≤0 또는 완전자본잠식이면 N/M(null) — 적자를 숫자 배수로 내보내지 않음. 스케일 항등식(자산=부채+자본)만은 총자본(지배+비지배). 비KRW 기능통화(두산밥캣=USD·중국기업=CNY 등)는 회계기말 환율로 순이익·자본 환산 후 배수 산출(경고 부착). 금융·지주(KSIC 64/65/66)는 EV/EBITDA·PSR·FCF 범주 부적합(N/A) — PBR·PER·배당·ROE 중심. 값 raw KRW int(_krw), % float(_pct). source = financial_metrics(요약) + fnlttSinglAcntAll(재무원장) + stockTotqySttus(유통주식수) + KRX bydd_trd(시세) + alotMatter(배당) + ECOS(환율).
        status: ok / invalid(빈입력) / not_found(미존재·우선주는 보통주 코드로 조회) / unlisted(비상장, 주가 없어 배수 불가) / no_financials(재무 미확정).
        note: lean v1 — RIM·EV/EBITDA·PSR·FCF·5년밴드·PIT·peer 랭킹은 v1.1.
        ref: financial_metrics, dividend, corp_gov_report, evidence
        """
        payload = await build_valuation_payload(company, format=format)
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ok":
            return payload.get("markdown") or _render_status(payload)
        return _render_status(payload)
