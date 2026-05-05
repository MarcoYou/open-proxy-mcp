"""주총 1호 안건 (재무제표 승인) 본문에서 잠정 재무제표 raw 추출.

DART 주총소집공고 본문에 1호 안건으로 첨부되는 잠정 재무제표:
- 사업보고서 제출 전 회사 자가 공시
- DART API fnlttSinglAcnt (사업보고서 확정치)와 source 다름 — 잠정치
- 표 구조 그대로 노출 (parsing 깔끔, 텍스트 통째 X)

return shape (parse_financials_xml 그대로):
    {
      "consolidated": {
        "balance_sheet": {"unit": "원", "period_labels": [...], "columns": [...], "rows": [...]},
        "income_statement": {...}
      },
      "separate": {...}
    }

각 rows = list of [account, (note?), current, prior] — 사용자/LLM이 raw 표 보고 직접 판단.

Layer: data tool (parsing + computation, 판단 X). Action tool (proxy_advise)에서 정량 metric은 별도
helper (`extract_metrics`)로 추출하여 facts evidence 활용.

(이전 `agm_first_agenda_fy.py` 정규식 텍스트 파서 폐기 — 표 구조 파싱이 더 정확.)
"""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.tools.parser import parse_financials_xml

# 정량 metric 추출용 키워드 (income_statement rows account 매칭)
_METRIC_KEYWORDS = {
    "net_income_krw": ("당기순이익(손실)", "당기순이익", "당기 순이익", "당기손익"),
    "revenue_krw": ("매출액", "수익(매출액)", "영업수익"),
    "operating_profit_krw": ("영업이익(손실)", "영업이익", "영업손익"),
    "total_assets_krw": ("자산총계", "자산 총계"),
    "total_liabilities_krw": ("부채총계", "부채 총계"),
    "total_equity_krw": ("자본총계", "자본 총계"),
}


def parse_provisional_financial_statement(html: str) -> dict[str, Any]:
    """주총 소집공고 본문 HTML → 잠정 재무제표 표 구조.

    parse_financials_xml의 wrapper — return shape 동일.
    """
    return parse_financials_xml(html)


def _parse_amount(text: str) -> int | None:
    """숫자 문자열 → int (콤마 제거, 괄호 음수, 단위 적용 X — caller 책임)."""
    if not text:
        return None
    s = text.strip().replace(",", "").replace(" ", "")
    if not s or s in ("-", "—"):
        return None
    is_negative = False
    if s.startswith("(") and s.endswith(")"):
        is_negative = True
        s = s[1:-1]
    if s.startswith("-"):
        is_negative = True
        s = s[1:]
    try:
        v = int(float(s))
    except (ValueError, TypeError):
        return None
    return -v if is_negative else v


def _scale_factor(unit: str | None) -> int:
    """unit 문자열 → krw 환산 계수 (백만원 → 1_000_000)."""
    if not unit:
        return 1
    u = unit.replace(" ", "")
    if "백만원" in u:
        return 1_000_000
    if "천원" in u:
        return 1_000
    if "억원" in u:
        return 100_000_000
    return 1


def extract_metrics(parsed: dict[str, Any], prefer: str = "consolidated") -> dict[str, Any]:
    """parse_provisional_financial_statement 결과 → 정량 metric flat dict.

    proxy_advise facts evidence용. 우선 연결, 없으면 별도.

    return:
        {
          "fy_current_net_income_krw": int | None,
          "fy_prior_net_income_krw": int | None,
          "fy_current_revenue_krw": int | None,
          ...
          "extraction_status": "success" | "partial" | "no_data",
          "scope_used": "consolidated" | "separate" | None,
        }
    """
    out: dict[str, Any] = {"extraction_status": "no_data", "scope_used": None}

    scope_order = (prefer, "separate" if prefer == "consolidated" else "consolidated")
    for scope in scope_order:
        scope_data = parsed.get(scope, {}) or {}
        if not scope_data:
            continue
        # income_statement에서 손익 metrics
        income = scope_data.get("income_statement")
        # balance_sheet에서 BS metrics
        balance = scope_data.get("balance_sheet")
        if not income and not balance:
            continue

        out["scope_used"] = scope
        n_extracted = 0

        for table in (income, balance):
            if not table or not table.get("rows"):
                continue
            unit = table.get("unit") or ""
            scale = _scale_factor(unit)
            cols = table.get("columns") or []
            try:
                acc_idx = cols.index("account")
                cur_idx = cols.index("current")
                prior_idx = cols.index("prior")
            except ValueError:
                continue

            for row in table["rows"]:
                if len(row) <= max(acc_idx, cur_idx, prior_idx):
                    continue
                account = (row[acc_idx] or "").strip()
                if not account:
                    continue
                account_clean = account.replace(" ", "")

                for metric_key, keywords in _METRIC_KEYWORDS.items():
                    cur_key = f"fy_current_{metric_key}"
                    prior_key = f"fy_prior_{metric_key}"
                    if cur_key in out:
                        continue  # 이미 추출
                    if any(kw.replace(" ", "") in account_clean for kw in keywords):
                        cur_val = _parse_amount(row[cur_idx])
                        prior_val = _parse_amount(row[prior_idx])
                        if cur_val is not None:
                            out[cur_key] = cur_val * scale
                            n_extracted += 1
                        if prior_val is not None:
                            out[prior_key] = prior_val * scale

        if n_extracted >= 3:
            out["extraction_status"] = "success"
            return out
        elif n_extracted > 0:
            out["extraction_status"] = "partial"
            # partial은 다음 scope 시도

    return out
