"""financial_metrics — DART 재무 4 endpoint 통합 facade.

Phase 1: 6 scope (summary / yearly / quarterly / yoy / qoq / audit_opinion).

한국 표준:
- 연결 (CFS) default — 지배주주 귀속
- 분모 0 / 음수 / None graceful
- 모든 금액 raw KRW int (`_krw` suffix), % float (`_pct`), decimal (`_ratio`)
- render에서만 사람가독 변환 (조/억)
"""

from __future__ import annotations
from open_proxy_mcp.clock import today_kst

import asyncio
import contextvars
import os
import re
import time
from datetime import date, timedelta
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.dart.client import note_degradation
from open_proxy_mcp.dart.as_of import get_strict_as_of
from open_proxy_mcp.dart.fx import (FALLBACK_WARNING, SRC_YAHOO, fiscal_year_end_date, fx_source, fx_to_krw,
                                  statement_currency)
from open_proxy_mcp.services.scale_guard import check_balance_identity, reconcile_report_scales, rescale_rows
from open_proxy_mcp.services.company import _company_id, resolve_company_query, _safe_company_info
from open_proxy_mcp.services.company import company_not_found_warning
from open_proxy_mcp.services.revenue_account import pick_revenue_row
from open_proxy_mcp.services.fiscal_period import (
    fiscal_period_label,
    fiscal_quarter_from_end,
    fiscal_year_from_end,
    fiscal_year_span,
    normalize_period_end,
)
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    build_filing_meta,
    build_usage,
)


_SUPPORTED_SCOPES = {
    "summary",
    "yearly",
    "quarterly",
    "yoy",
    "qoq",
    "audit_opinion",
    "accounts",
}

#: 금액인데 `_krw` 접미어가 없는 필드 — 키가 canonical id 라 이름 규칙이 안 통하는 자리.
#: 이름 기반 환산의 예외는 여기 한 곳뿐이고, 늘어나면 반드시 이 집합에 등록한다.
_AMOUNT_MAP_FIELDS = {"by_canonical_id"}

#: 재무제표 공시 순서 — 알파벳순으로 뒤섞지 않는다.
_SJ_ORDER = {"BS": 0, "IS": 1, "CIS": 2, "CF": 3, "SCE": 4}

#: accounts scope 가 원행에서 남기는 필드. 파생값을 만들지 않는다 — 계정 원문·기준만 그대로.
_ACCOUNT_ROW_FIELDS = (
    "sj_div", "sj_nm", "account_id", "account_nm", "account_detail",
    "thstrm_amount", "thstrm_add_amount", "frmtrm_amount", "bfefrmtrm_amount",
    "ord", "currency", "fs_div",
)

_REPRT_BUSINESS = "11011"  # 사업보고서 (연간)
_REPRT_LABEL = {"11011": "사업보고서", "11012": "반기보고서", "11013": "1분기보고서", "11014": "3분기보고서"}

#: `years` 상한 — 연도당 최대 4콜이라 상한이 없으면 한 요청이 분당 910 캡을 민다.
_MAX_YEARS = 10


# DART 사업보고서 fnlttSinglAcnt 표준 account_nm 매칭 키워드.
# fnlttSinglAcntAll에는 더 세분화된 account_nm이 들어 있어 별도 패턴.
_BS_ACCOUNT_PATTERNS = {
    "current_assets": ("유동자산",),
    "non_current_assets": ("비유동자산",),
    "total_assets": ("자산총계",),
    "current_liabilities": ("유동부채",),
    "non_current_liabilities": ("비유동부채",),
    "total_liabilities": ("부채총계",),
    "capital_stock": ("자본금",),
    "retained_earnings": ("이익잉여금",),
    "total_equity": ("자본총계",),
    # 지배주주 귀속 자본 — ROE 분모(평균 지배자본)용. 주요계정엔 없고 fnlttSinglAcntAll BS에만 존재
    # (account_nm "지배기업 소유주지분" = ifrs-full_EquityAttributableToOwnersOfParent).
    "controlling_equity": ("지배기업 소유주지분", "지배기업소유주지분"),
    # 한글 매칭은 폴백일 뿐이다 — 위 루프가 account_id(`ifrs-full_NoncontrollingInterests`)로
    # 먼저 잡는다. 부분문자열이라 「비지배지분부채」·「비지배지분율」이 걸리므로 아래에서 막는다.
    "nci": ("비지배지분", "비지배주주지분"),
}

_IS_ACCOUNT_PATTERNS = {
    # revenue 는 여기서 매칭하지 않는다 — 회사·업종마다 top line 이름이 달라(영업수익·보험수익·
    # 수익(매출액)…) `revenue_account.pick_revenue_row` 가 account_id 우선 + KSIC 순서로 고른다.
    # 키만 남겨 결과 dict 모양을 유지한다.
    "revenue": (),
    "operating_profit": ("영업이익", "영업이익(손실)"),
    "income_before_tax": ("법인세차감전 순이익", "법인세비용차감전순이익", "법인세차감전순이익"),
    "net_income": ("당기순이익(손실)", "당기순이익", "분기순이익", "반기순이익"),
    "comprehensive_income": ("총포괄손익",),
}

# fnlttSinglAcntAll 전용 패턴 (현금흐름표 + 추가 IS 항목).
_CF_ACCOUNT_PATTERNS = {
    "cfo": ("영업활동현금흐름", "영업활동으로 인한 현금흐름", "영업활동으로인한현금흐름"),
    "cfi": ("투자활동현금흐름", "투자활동으로 인한 현금흐름"),
    "cff": ("재무활동현금흐름", "재무활동으로 인한 현금흐름"),
    "capex": (
        "유형자산의 취득",
        "유형자산의취득",
        "유형자산취득",
    ),
    "depreciation": (
        "감가상각비",
        "유형자산감가상각비",
        "감가상각비와무형자산상각비",  # 일부 회사는 유·무형 합산 한 줄
        "유무형자산상각비",
    ),
    "amortization": (
        "무형자산상각비",
        "무형자산 상각비",
    ),
    "interest_paid": (
        # 느슨한 "이자지급"은 "신종자본증권 이자지급"(0원 행)에 선매칭돼 분모를 0으로 오염
        # (POSCO홀딩스 실측) — substring은 "이자의 지급"만, 나머지 변형은
        # _INTEREST_PAID_EXACT(정확 일치)로 처리 (412사 audit: 미추출 153사 probe).
        "이자의 지급",
    ),
    "dividends_paid": (
        "배당금의 지급",
        "배당금지급",
        "배당금 지급",
    ),
}

# CF 이자지급 변형 — 정확 일치만 (substring이면 "신종자본증권이자지급" 류 FP).
# 412사 probe 실측 빈도: 이자지급(영업) 15 / 이자지급 8 / 이자비용 5 / 이자비용지급 4 / 이자납부 1.
# CF의 "이자비용" 행은 간접법 조정 발생액 — 이자보상 분모로 사용 가능.
_INTEREST_PAID_EXACT = {
    "이자의지급", "이자지급", "이자지급(영업)", "이자의지급(영업)",
    "이자비용지급", "이자비용의지급", "이자납부", "이자비용",
}

# fnlttSinglAcntAll IS/CIS 추가 항목.
_IS_DETAIL_PATTERNS = {
    "gross_profit": ("매출총이익", "매출총이익(손실)"),
    "cogs": ("매출원가",),
    # "금융비용" fallback 금지 — 환손실·평가손 포함 총액이 잡혀 이자보상배율 왜곡
    # (SK하이닉스 실측: 금융비용 12.5조 vs 실제 이자지급 0.94조 → 3.77배 vs ~50배).
    # 이자비용 행이 없으면 CF '이자의 지급'(interest_paid)으로 fallback (사용처에서 처리).
    "interest_expense": ("이자비용",),
    "minority_interest_income": ("비지배지분", "비지배주주지분 순이익", "비지배지분순이익"),
    "controlling_interest_income": ("지배기업 소유주지분", "지배기업소유주지분", "지배주주지분 순이익"),
    "diluted_eps": ("희석주당이익", "희석주당순이익", "희석주당이익(손실)"),
    "basic_eps": ("기본주당이익", "기본주당순이익", "주당이익", "주당순이익"),
    "accounts_receivable": ("매출채권", "매출채권 및 기타채권"),
    "inventory": ("재고자산",),
    "accounts_payable": ("매입채무", "매입채무 및 기타채무"),
    "cash_and_equivalents": ("현금및현금성자산", "현금 및 현금성자산"),
    # 총차입금(short_term_debt/long_term_debt/total_debt)은 더 이상 account_nm 키워드로 잡지
    # 않는다 — account_id(IFRS local-name) 전체명 정확매칭 사전 `_compute_borrowings`로 이관
    # (260713 KOSPI200 전수검증: 키워드 substring은 유동성장기차입금·전환사채·비유동차입을
    # 전량 누락해 총차입 중앙 -50% 과소계상. 회계·CFO·스튜어드십 5인 패널 검토 반영).
}

# 귀속 순이익은 account_nm이 총포괄손익 귀속과 동일("지배기업 소유주지분"/"비지배지분")이라
# account_nm substring으로는 둘을 구분 못 한다(응답 순서상 ComprehensiveIncome 귀속이 먼저 와서
# 잘못 잡힘 → 순이익·ROE가 총포괄손익으로 오염). fnlttSinglAcntAll은 account_id(IFRS)를 주므로
# account_id로 '당기순이익 귀속'만 정확히 매칭한다. (총포괄=ComprehensiveIncomeAttributable…는 배제)
_IS_ATTRIBUTION_ACCOUNT_ID = {
    "controlling_interest_income": "ProfitLossAttributableToOwnersOfParent",
    "minority_interest_income": "ProfitLossAttributableToNoncontrollingInterests",
}

# EPS는 **account_id**로만 잡는다 — nm "기본주당이익"이 "1우선주기본주당이익"·"계속영업…"에
# 부분일치하고 우선주 행이 먼저 와서 보통주 EPS 대신 우선주 EPS를 집는 버그(현대차 37851 vs 36088).
# 보통주 total(ifrs-full_Basic/DilutedEarningsLossPerShare) 우선, total 행이 없으면(분리공시)
# 계속영업(...FromContinuingOperations)으로 fallback. 우선주(dart_*PreferredStock)·중단영업은 제외.


def _strip(s: Any) -> str:
    return ("" if s is None else str(s)).strip()


def normalize_amount(raw: Any) -> int | None:
    """DART 응답 금액 → int (원).

    DART OpenAPI fnlttSinglAcnt / fnlttSinglAcntAll 응답은 항상 **원 단위 raw + 콤마 포맷**
    으로 표준화. 별도 unit 필드(백만원/천원)는 응답에 없음 (currency 필드만 KRW 표기).
    → 콤마 strip + 괄호 음수 + None graceful만 처리하면 충분.

    처리:
    - None / "" / "-" → None
    - "227,062,266,000,000" → 227_062_266_000_000
    - "(500)" → -500 (괄호 음수, 한국 회계 관행 — T19 fix 패턴)
    - 부호 prefix "-500" → -500
    - 잘못된 포맷 → None
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in ("-", "—", "–"):
        return None
    # 괄호 음수
    is_neg = False
    if s.startswith("(") and s.endswith(")"):
        is_neg = True
        s = s[1:-1].strip()
    s = s.replace(",", "").replace(" ", "")
    if not s:
        return None
    try:
        n = int(float(s))
    except (ValueError, TypeError):
        return None
    return -n if is_neg else n


#: 부분문자열 매칭이라 **다른 계정에 올라타는 조합**이 있다. 지금 이것들이 안 터지는 유일한
#: 이유는 DART 가 표준 순서(유동→비유동, 자본총계→부채및자본총계)로 행을 주고 첫 매칭이
#: 이기기 때문이다. 순서가 달라지면 조용히 뒤바뀐다 — 「유동자산」은 「비유동자산」의
#: 부분문자열이라, 비유동이 먼저 오면 유동자산 자리에 그 값이 들어가고 비유동자산은 빈 채로
#: 남는다. 「자본총계」가 「부채및자본총계」에 걸리면 자기자본 자리에 **자산총계**가 들어간다.
#: 순서에 기대지 않도록 계정별로 명시적으로 막는다.
_BS_ACCOUNT_EXCLUDE: dict[str, tuple[str, ...]] = {
    "current_assets": ("비유동자산",),
    "current_liabilities": ("비유동부채",),
    "total_equity": ("부채및자본총계", "부채와자본총계", "자본과부채총계"),
    # 우선주·보통주 자본금은 자본금의 **일부**다. 단독으로 쓰면 자본금이 과소 계상되고
    # 자본잠식률은 그만큼 과대해진다. 부모 행이 없으면 아래에서 합산한다.
    "capital_stock": ("우선주자본금", "보통주자본금"),
    # **부채 계정이 자본 계정보다 먼저 나온다** — 실측 KT&G 연결 BS 는 부채 섹션의
    # 「비지배지분부채」가 자본 섹션의 「비지배지분」보다 앞이라, 막지 않으면 8.7배 틀린 값을
    # 집는다. 그 값으로 지배지분을 차감 산출하면 자기자본이 부풀어 잠식이 과소 판정된다.
    "nci": ("비지배지분부채", "비지배지분율", "비지배지분의장부금액", "비지배지분에",
            "비지배지분으로", "비지배지분의증감", "비지배주주지분(부채", "비지배주주지분(자본"),
}

#: 부모 「자본금」 행이 없고 종류주 행만 있는 표를 위한 합산 대상.
#: 실측(소집공고): 삼성전자는 「Ⅰ.자본금 897,514 = 우선주 119,467 + 보통주 778,047」로 부모가
#: 있지만, 부모 없이 종류별로만 적는 표가 존재한다. 그때 하나만 집으면 값이 반쪽이 된다.
_CAPITAL_STOCK_PARTS = ("우선주자본금", "보통주자본금")


def compute_capital_impairment(
    *,
    capital_stock: int | float | None,
    controlling_equity: int | float | None,
    total_equity: int | float | None,
    nci: int | float | None = None,
    controlling_basis: str = "controlling",
) -> dict[str, Any]:
    """자본잠식 판정 — **계산식이 사는 유일한 자리.**

    잠식률 = (자본금 − 자기자본) / 자본금 × 100
      — 코스닥시장 공시·상장관리 해설서 「자본잠식률[(자본금-자기자본)/자본금*100]이 50% 이상」

    **자기자본에서 비지배지분을 뺀다.** 같은 해설서 「적용기준 ① 연결재무제표 작성대상법인의
    경우에는 연결재무제표를 기준으로 하되 **자기자본에서 비지배지분을 제외**」. 규정마다 다르다는
    점이 중요하다 — 바로 옆 「법인세비용차감전계속사업손실」 기준은 「연결 기준, **비지배지분
    포함**」이다. 일부러 갈라놓은 것이라 한쪽 관행을 다른 쪽에 쓰면 안 된다. 비지배지분을
    포함하면 자회사 소수주주 몫만큼 자기자본이 부풀어 **잠식률이 과소 산정**된다.

    자기자본을 구하는 순서(`basis` 로 남긴다):
      `controlling`  지배지분 계정을 직접 읽음
      `derived`      자본총계 − 비지배지분 — 지배지분 소계를 아예 안 적는 표가 있다(실측
                     고려아연·비덴트·미래에셋증권). 여기서 자본총계로 물러나면 비지배 몫만큼
                     자기자본이 부풀어 **규정이 금지한 바로 그 과소 산정**이 된다.
      `total`        둘 다 없음 — 별도재무제표(비지배지분 개념이 없어 자본총계 = 지배지분)이거나
                     연결인데 구분 표시가 없는 경우. 문장은 이 값을 읽어 갈라 써야 한다.

    상태: 자기자본 ≤ 0 완전 / 50%↑ 관리종목(2년 연속이면 상장폐지) / 0~50% 부분 / 그 외 정상.

    `controlling_basis`: `controlling_equity` 를 준 경우 그 값이 어느 길로 나왔는지(`basis` 에 그대로 남는다). 기본은 지배지분 계정을 직접 읽은
    `controlling`; 공용 규칙(`controlling_eq`)이 자본총계 − 비지배(`derived`)·자본총계(`total`)로 구한 값이면 그렇게 적는다.
    """
    equity = controlling_equity
    basis = controlling_basis
    if equity is None:
        if total_equity is not None and nci is not None:
            equity, basis = total_equity - nci, "derived"
        else:
            equity, basis = total_equity, "total"

    out: dict[str, Any] = {
        "equity_used": equity, "basis": basis,
        "ratio_pct": None, "status": None, "ratio_total_pct": None,
    }
    if capital_stock is None or capital_stock <= 0:
        return out

    # 비지배지분을 **포함한** 값도 함께 남긴다 — 판정 기준은 아니지만, 두 값의 간격이 그 회사의
    # 자회사 구조를 말해주고(간격이 크면 소수주주 몫이 크다), 다른 자료와 대조할 때 필요하다.
    if total_equity is not None:
        out["ratio_total_pct"] = round(
            (capital_stock - total_equity) / capital_stock * 100, 2)
    if equity is None:
        return out

    ratio = (capital_stock - equity) / capital_stock * 100
    out["ratio_pct"] = round(ratio, 2)
    if equity <= 0:
        out["status"] = "full"
    elif ratio >= 50:
        out["status"] = "partial_50plus"
    elif ratio > 0:
        out["status"] = "partial"
    else:
        out["status"] = "normal"
    return out


def _match_account(account_nm: str, patterns: tuple[str, ...],
                   exclude: tuple[str, ...] = ()) -> bool:
    if not account_nm:
        return False
    nm = account_nm.strip().replace(" ", "")
    for x in exclude:
        if x.replace(" ", "") in nm:
            return False
    for p in patterns:
        if p.replace(" ", "") in nm:
            return True
    return False


def _extract_period_amount(row: dict[str, Any], period: str = "thstrm") -> int | None:
    """row에서 특정 기간 금액 추출. period: thstrm(당기) / frmtrm(전기) / bfefrmtrm(전전기)."""
    return normalize_amount(row.get(f"{period}_amount"))


def _extract_cumulative_is(row: dict[str, Any]) -> int | None:
    """IS 누적값: thstrm_add(당기 누적) 우선, 없으면(사업보고서·1분기) thstrm.

    분기·반기 보고서의 손익 thstrm은 '당기 3개월(standalone)'이고 누적은 thstrm_add다.
    사업보고서(연간)·1분기는 thstrm_add가 비어 thstrm이 곧 누적과 동일하다.
    """
    v = normalize_amount(row.get("thstrm_add_amount"))
    return v if v is not None else normalize_amount(row.get("thstrm_amount"))


# reprt_code별 기간(개월) — 누적(period-to-date) / 당기(standalone)
def _period_months(reprt_code: str | None, *, cumulative: bool, annual_months: int | None = None) -> int:
    """누적이면 보고시점까지 개월(1Q=3·반기=6·3Q=9·연간=12), 당기면 분기=3·연간=12.

    `annual_months` — 사업보고서(11011) 한 결산기의 **실제 개월**(`_report_period`). 반기·분기 결산 회사(리츠)는 결산기마다 사업보고서를 따로 내서
    그 보고서의 당기가 6·3개월이다(롯데리츠 FY2025 = 2025.07~12). 종전엔 사업보고서면 무조건 12 라 6개월 순이익이 「연간 12개월」로 적혔다
    (재무 지표 도구·밸류업 도구 ROE 가 절반). 못 읽으면(None) 종전대로 12.
    """
    rc = reprt_code or "11011"
    if rc == "11011":  # 사업보고서 — 누적=당기=그 결산기. 보통 12개월
        return annual_months if annual_months else 12
    if not cumulative:
        return 3  # 분기/반기/3분기 standalone = 3개월
    return {"11013": 3, "11012": 6, "11014": 9}.get(rc, 12)


#: 주요계정 손익 줄의 당기 기간 표기 「2025.07.01 ~ 2025.12.31」(재무상태표 줄은 「2025.12.31 현재」).
_DT_RANGE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})\s*~\s*(\d{4})\.(\d{2})\.(\d{2})")


def _report_period(rows: list[dict[str, Any]] | None, field: str = "thstrm_dt") -> dict[str, Any] | None:
    """보고서 손익 줄의 기간 표기(`thstrm_dt`) → `{start, end, months}`. 읽지 못하면 None(호출부는 종전대로 12개월).

    개월 = 일수 ÷ 30.4375 반올림 — 52·53주 결산(364·371일)도 12. 1~24개월만 믿는다(그 밖은 날짜 오류로 보고 None).
    """
    for r in rows or []:
        if (r.get("sj_div") or "").upper() not in ("IS", "CIS"):
            continue
        m = _DT_RANGE.search(str(r.get(field) or ""))
        if not m:
            continue
        try:
            start = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            end = date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
        except ValueError:
            continue
        months = round(((end - start).days + 1) / 30.4375)
        if end < start or not 1 <= months <= 24:
            continue
        return {"start": start.isoformat(), "end": end.isoformat(), "months": months}
    return None


def _month_gap(a_end: str, b_end: str) -> int:
    """a_end − b_end (달 수, ISO 날짜 앞 7자리 기준)."""
    return (int(a_end[:4]) * 12 + int(a_end[5:7])) - (int(b_end[:4]) * 12 + int(b_end[5:7]))


def _build_account_map(
    rows: list[dict[str, Any]],
    bs_patterns: dict[str, tuple[str, ...]] = _BS_ACCOUNT_PATTERNS,
    is_patterns: dict[str, tuple[str, ...]] = _IS_ACCOUNT_PATTERNS,
    period: str = "thstrm",
    cumulative_is: bool = False,
    induty_code: str | None = None,
) -> dict[str, Any]:
    """fnlttSinglAcnt rows → 표준 키 매핑 dict (BS + IS).

    같은 키에 여러 행이 매칭되면 첫 매칭만 사용 (DART 응답 순서 = 사업보고서 순서).
    cumulative_is=True: IS는 누적(thstrm_add 우선)으로, BS는 잔액(thstrm)으로 — 분기/반기
    보고서에서 손익을 '당기 3개월'이 아닌 '누적'으로 읽기 위함. BS는 잔액이라 기간 무관.
    revenue 만은 첫 매칭이 아니라 `pick_revenue_row`(account_id 우선 + KSIC 순서)로 고르고,
    무엇을 골랐는지 `revenue_account_nm`·`revenue_account_id`·`revenue_standard` 에 남긴다.
    """
    out: dict[str, Any] = {k: None for k in {**bs_patterns, **is_patterns}}
    capital_parts: list[int] = []
    _apply_revenue_pick(out, rows, induty_code, period=period, cumulative_is=cumulative_is)
    for row in rows:
        sj_div = _strip(row.get("sj_div"))
        account_nm = _strip(row.get("account_nm"))
        if sj_div == "BS":
            if _match_account(account_nm, _CAPITAL_STOCK_PARTS):
                part = _extract_period_amount(row, period)
                if part is not None:
                    capital_parts.append(part)
            for key, patterns in bs_patterns.items():
                if out[key] is None and _match_account(
                        account_nm, patterns, _BS_ACCOUNT_EXCLUDE.get(key, ())):
                    out[key] = _extract_period_amount(row, period)  # BS는 항상 잔액
                    break
        elif sj_div == "IS":
            for key, patterns in is_patterns.items():
                if key == "revenue":
                    continue  # 위에서 pick_revenue_row 로 결정
                if out[key] is None and _match_account(account_nm, patterns):
                    out[key] = _extract_cumulative_is(row) if cumulative_is else _extract_period_amount(row, period)
                    break
    # 부모 「자본금」 행이 없고 종류주 행만 있는 표 — 합쳐야 자본금이다. 하나만 집으면 반쪽이고,
    # 자본금은 자본잠식률의 **분모**라 반쪽이면 잠식률이 두 배로 부풀어 판정이 뒤집힌다.
    if out.get("capital_stock") is None and capital_parts:
        out["capital_stock"] = sum(capital_parts)
    return out


def _apply_revenue_pick(
    out: dict[str, Any],
    rows: list[dict[str, Any]],
    induty_code: str | None,
    *,
    period: str,
    cumulative_is: bool,
) -> None:
    """매출 행을 고르고 out 에 값 + 출처를 적는다. 못 고르면 revenue=None 그대로(0 으로 안 채움)."""
    out.setdefault("revenue", None)
    out["revenue_account_nm"] = None
    out["revenue_account_id"] = None
    out["revenue_standard"] = None
    out["revenue_basis"] = None
    pick = pick_revenue_row(rows, induty_code)
    if pick is None:
        return
    out["revenue_basis"] = pick.basis
    out["revenue"] = _extract_cumulative_is(pick.row) if cumulative_is else _extract_period_amount(pick.row, period)
    out["revenue_account_nm"] = pick.account_nm
    out["revenue_account_id"] = pick.account_id
    out["revenue_standard"] = pick.standard


# ═══════════════════════════════════════════════════════════════════════════
# 총차입금 분류 — account_id/account_nm 전체명 정확매칭 (키워드 substring 금지)
# 260713 KOSPI200 전수검증 + KICPA·AICPA·DART·스튜어드십·CFO 5인 패널 반영.
# 원칙: ① substring 금지 → 전체명 사전 정확매칭 ② 미등록=REVIEW 표면화(합산 제외)
#       ③ id↔nm 모순=CONFLICT(제외+플래그) ④ first-match break 금지 → sum-all.
# 사전 열거 근거·측정: wiki/lessons (financial-metrics-borrowings-260713).
# ═══════════════════════════════════════════════════════════════════════════

def _cf(d: dict[str, str]) -> dict[str, str]:
    return {k.casefold(): v for k, v in d.items()}

# ① account_id local-name(접두 ifrs-full_/dart_ 제거, casefold) → 자체 canonical id [HIGH]
_BORROW_ID_MAP = _cf({
    "ShorttermBorrowings": "OPM_ST", "ShortTermBorrowings": "OPM_ST",
    "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings": "OPM_ST",
    "OtherCurrentBorrowingsAndCurrentPortionOfOtherNoncurrentBorrowings": "OPM_ST",
    "CurrentLoansReceivedAndCurrentPortionOfNoncurrentLoansReceived": "OPM_ST",
    "CurrentPortionOfLongtermBorrowings": "OPM_LT_CURR",
    "CurrentPortionOfLongTermBorrowings": "OPM_LT_CURR",
    "LongtermBorrowings": "OPM_LT", "LongTermBorrowings": "OPM_LT",
    "LongTermBorrowingsGross": "OPM_LT",
    "NoncurrentPortionOfOtherNoncurrentBorrowings": "OPM_LT",
    "NoncurrentPortionOfNoncurrentLoansReceived": "OPM_LT",
    "NoncurrentPortionOfNoncurrentSecuredBankLoansReceived": "OPM_LT",
    "OtherBorrowings": "OPM_BORROW", "Borrowings": "OPM_BORROW", "LoansReceived": "OPM_BORROW",
    "BondsIssued": "OPM_BOND", "NoncurrentPortionOfNoncurrentBondsIssued": "OPM_BOND",
    "BondsIssuedNominalValue": "OPM_BOND",
    "CurrentPortionOfBonds": "OPM_BOND_CURR",
    "CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued": "OPM_BOND_CURR",
    "ConvertibleBonds": "OPM_CONV", "ConvertibleBondsNet": "OPM_CONV",
    "CurrentPortionOfConvertibleBonds": "OPM_CONV",
    "BondWithWarrant": "OPM_CONV", "BondWithWarrantNet": "OPM_CONV",
    "CurrentPortionOfBondWithWarrant": "OPM_CONV", "CurrentPortionOfExchangeableBond": "OPM_CONV",
})
# Loan/Bond 문자열을 포함하나 차입 아님 → 명시 배제(자산/자본/매입채무). CONFLICT 판정에 사용.
_BORROW_ID_NOTBORROW = _cf({
    "ShortTermLoansNet": "asset", "LongTermLoansNet": "asset", "ShortTermLoans": "asset",
    "LongTermLoansGross": "asset", "LoansAtAmortisedCost": "asset",
    "CurrentLoansAndReceivables": "asset", "NoncurrentLoansAndReceivables": "asset",
    "LongTermTradeAndOtherNonCurrentPayables": "payable",
})
# ② account_nm 전체명(공백제거) → canonical id [MED] — 비표준 account_id일 때만
_BORROW_NM_MAP = {
    "단기차입금": "OPM_ST", "유동차입금": "OPM_ST", "단기차입금및유동성장기부채": "OPM_ST",
    "기업어음": "OPM_ST", "전자단기사채": "OPM_ST", "단기사채": "OPM_ST",  # CP/전단채(패널 E)
    "유동성장기차입금": "OPM_LT_CURR", "비유동차입금의유동성대체부분": "OPM_LT_CURR",
    "장기차입금": "OPM_LT", "비유동차입금": "OPM_LT",
    "자산유동화차입금": "OPM_BORROW", "프로젝트금융차입금": "OPM_BORROW", "PF차입금": "OPM_BORROW",  # ABS 만기중립
    "차입금": "OPM_BORROW", "차입부채": "OPM_BORROW", "차입금및사채": "OPM_COMBINED",
    "전환사채": "OPM_CONV", "비유동전환사채": "OPM_CONV",
    "유동성교환사채": "OPM_CONV", "교환사채": "OPM_CONV", "사채": "OPM_BOND",
    # KOSDAQ 소형주 '채무'·'유동화' 표기 변형 (260713 KOSPI+KOSDAQ 298사 전수검증 — 비표준코드라
    # 침묵누락하던 이자부 차입. '장기유동화채무'는 두산 표준코드 NoncurrentPortionOfOtherNoncurrentBorrowings
    # 매핑과 정합. 전수상 이 명칭들은 전부 차입 계정에서만 등장 — 오탐 없음 확인).
    "유동성장기차입채무": "OPM_LT_CURR", "유동차입부채": "OPM_ST", "비유동차입부채": "OPM_LT",
    "유동화채무": "OPM_BORROW", "장기유동화채무": "OPM_LT",
    "단기차입금및사채": "OPM_ST", "유동성전환사채": "OPM_CONV",
    "유동성신주인수권부사채": "OPM_CONV", "사채(비유동)": "OPM_BOND",
}
# nm '신종자본증권'(비표준코드로 dart_HybridBonds id를 못 단 경우) → hybrid 버킷 인식 (총차입 제외,
# IFRS 자본성). 포스코퓨처엠 5,994억 등 hybrid_capital_krw 침묵누락 방지.
_HYBRID_NM = ("신종자본증권",)
_BORROW_NM_NOTBORROW = {"전환사채상환할증금"}  # 상환할증금은 별도 REVIEW로 표면화(패널 이견 → 조용히 안 삼킴)
# REVIEW 표면화용(합산 제외) — 미지의 차입 변형을 silent EXCL이 아니라 사람검토로 승격.
# '차입채무'·'유동화채무'는 전수상 전부 차입 계정에서만 등장(오탐 없음, 260713 검증).
_BORROW_TOKENS = ("차입금", "차입부채", "차입채무", "사채", "기업어음", "전자단기사채", "유동화채무")
# canonical id → 만기 버킷 (short = 1년내 만기). total = short + long + convertible + other.
_OPM_SHORT = {"OPM_ST", "OPM_LT_CURR", "OPM_BOND_CURR"}
_OPM_LONG = {"OPM_LT", "OPM_BOND"}
# 연결 BS에 뜨면 금융사(영업조달) 신호 — 은행 예수부채·보험계약부채·증권 고객예탁금.
# 예수금(원천징수)·예수보험료는 제조사에도 흔해 제외(260713 CFO/DART 지적: 삼성전자·SKT 오탐).
# 금융지주는 은행·보험 자회사 연결로 이 계정이 뜨고, 일반지주(SK·LG)는 안 떠 자동 분리됨.
_FIN_BS_SIGNAL = ("예수부채", "보험계약부채", "책임준비금", "고객예탁금", "매도파생결합")


def _ksic_is_financial(induty_code: str | None) -> bool:
    """KSIC 업종코드로 금융 영업사 판별 — BS신호(예수부채 등)를 보완하는 2차 신호.

    64(은행·여신·신탁)·65(보험)·66(증권·금융지원)은 금융업. 단 64992(지주회사)는 KSIC상
    금융지주와 일반지주(SK·LG 등)가 섞여 못 가르므로 **제외** — 금융지주는 연결 BS 예수부채로
    이미 잡히고, 일반지주는 정상 산출돼야 한다. 이 규칙은 수신 없어(예수부채 無) BS신호가 놓치는
    카드·캐피탈·벤처캐피탈(삼성카드 64913·미래에셋벤처투자 649 등)을 보완한다 (260713 검증).
    """
    ind = str(induty_code or "").strip()
    return ind[:2] in ("64", "65", "66") and ind != "64992"


def _lookup_induty_code(corp_code: str, stock_code: str = "") -> str | None:
    """dart_fundamentals(Postgres)에서 induty 조회 — DART 콜 0. 미설정/장애/미수록이면 None.

    공용 접속(`db.pg_rows` 풀)으로 **한 번** 묻는다(종전: 질의마다 새 접속 + 종목코드 → 법인코드 두 번).
    종목코드로 맞는 행을 우선하고, 없을 때만 법인코드로 맞는 행을 쓴다 — 종전 두 단계와 같은 순서다.
    """
    from open_proxy_mcp import db
    rows = db.pg_rows(
        "SELECT induty FROM dart_fundamentals "
        "WHERE (ticker = %s OR corp_code = %s) AND induty IS NOT NULL AND induty <> '' "
        "ORDER BY CASE WHEN ticker = %s THEN 0 ELSE 1 END LIMIT 1",
        # 종목코드가 없으면 NULL 로 — 「ticker = ''」 가 빈 종목코드 행을 고르지 않게(법인코드로만 맞춘다).
        (stock_code or None, corp_code, stock_code or None))
    return str(rows[0][0]) if rows else None


def _borrow_local(account_id: str) -> str | None:
    """account_id → casefold local-name (ifrs-full_/dart_ 접두 제거). 비표준코드면 None."""
    aid = account_id or ""
    if aid.startswith(("ifrs", "dart")):
        loc = aid.split("_", 1)[1] if "_" in aid else aid
        return loc.casefold()
    return None


def _classify_borrow_row(account_id: str, account_nm: str) -> tuple[str | None, str]:
    """차입행 분류 → (canonical_id|None, kind).

    kind: borrow(합산) / lease / hybrid / convertible_premium / conflict / review / exclude.
    """
    n = (account_nm or "").strip().replace(" ", "")
    loc = _borrow_local(account_id)
    if (loc and "leaseliabilit" in loc) or "리스부채" in n or "리스채무" in n:
        return None, "lease"
    if (loc and "hybridbonds" in loc) or any(h in n for h in _HYBRID_NM):
        return None, "hybrid"
    if n in _BORROW_NM_NOTBORROW:
        return None, "review"  # 상환할증금 등 — 표면화만
    if loc in _BORROW_ID_NOTBORROW:
        if n in _BORROW_NM_MAP:  # id=비차입인데 nm=차입 → 모순
            return None, "conflict"
        return None, "exclude"
    if loc in _BORROW_ID_MAP:
        return _BORROW_ID_MAP[loc], "borrow"
    if loc is not None:  # 표준 account_id인데 사전 미등록
        if n in _BORROW_NM_MAP:
            return _BORROW_NM_MAP[n], "review"  # nm은 확실 → 표면화(합산 제외)
        if any(t in n for t in _BORROW_TOKENS):
            return None, "review"
        return None, "exclude"
    # 비표준 account_id → nm 전체명 정확매칭 [MED]
    if n in _BORROW_NM_MAP:
        return _BORROW_NM_MAP[n], "borrow"
    if any(t in n for t in _BORROW_TOKENS):
        return None, "review"  # 미지의 차입계열 nm — 조용히 누락 말고 표면화
    return None, "exclude"


def _compute_borrowings(rows: list[dict[str, Any]], period: str = "thstrm") -> dict[str, Any]:
    """BS 행 → 총차입금 구조화 산출 (sum-all, 만기분해, 리스·신종자본 별도, 신뢰도·경고).

    반환: total_debt / short_term_debt / long_term_debt / convertible_debt_krw /
    other_borrowings_krw / lease_liabilities_krw / hybrid_capital_krw / total_debt_incl_lease /
    by_canonical / is_financial_bs / confidence / conflicts / reviews / data_quality_flags /
    no_borrowing_rows.
    """
    bs_rows = [r for r in rows if _strip(r.get("sj_div")) == "BS"]
    by_canon: dict[str, int] = {}
    borrow_rows: list[tuple[str, int, str, bool]] = []  # (nm, amt, canonical, is_subtotal)
    lease = hybrid = 0
    conflicts: list[dict[str, str]] = []
    reviews: list[dict[str, str]] = []
    tiers: set[str] = set()
    is_fin_bs = False

    for r in bs_rows:
        nm = _strip(r.get("account_nm"))
        nm_c = nm.replace(" ", "")
        if any(sig in nm_c for sig in _FIN_BS_SIGNAL):
            is_fin_bs = True
        canonical, kind = _classify_borrow_row(_strip(r.get("account_id")), nm)
        amt = _extract_period_amount(r, period)
        if kind == "lease":
            if amt and amt > 0:
                lease += amt
            continue
        if kind == "hybrid":
            if amt and amt > 0:
                hybrid += amt
            continue
        if kind == "conflict":
            conflicts.append({"account_nm": nm, "account_id": _strip(r.get("account_id"))})
            tiers.add("CONFLICT")
            continue
        if kind == "review":
            reviews.append({"account_nm": nm, "account_id": _strip(r.get("account_id"))})
            tiers.add("REVIEW")
            continue
        if kind != "borrow" or canonical is None:
            continue
        if amt is None or amt <= 0:
            continue
        is_sub = nm_c.endswith(("총액", "합계"))  # 진성 소계만(패널: 사채포함 토큰은 정상 leaf)
        borrow_rows.append((nm_c, amt, canonical, is_sub))
        tiers.add("MED" if _borrow_local(_strip(r.get("account_id"))) is None else "HIGH")

    if not bs_rows:
        return {"total_debt": None, "short_term_debt": None, "long_term_debt": None,
                "convertible_debt_krw": None, "other_borrowings_krw": None,
                "lease_liabilities_krw": None, "hybrid_capital_krw": None,
                "total_debt_incl_lease": None, "by_canonical": {}, "is_financial_bs": False,
                "confidence": None, "conflicts": [], "reviews": [], "data_quality_flags": [],
                "no_borrowing_rows": False}

    # 소계행 이중계상 보정: nm 접미 '총액/합계' 행이 leaf합의 ±2%면 제외(엄격).
    leaves = [b for b in borrow_rows if not b[3]]
    leaf_sum = sum(b[1] for b in leaves)
    subtotal_removed = 0
    flags: list[str] = []
    kept: list[tuple[str, int, str, bool]] = []
    for nm_c, amt, canonical, is_sub in borrow_rows:
        if is_sub and leaf_sum > 0 and 0.98 <= amt / leaf_sum <= 1.02:
            subtotal_removed += amt
            flags.append(f"소계행 이중계상 보정: '{nm_c}' {amt/1e12:.2f}조 제외(leaf합과 ±2% 일치)")
            continue
        kept.append((nm_c, amt, canonical, is_sub))
    for nm_c, amt, canonical, _ in kept:
        by_canon[canonical] = by_canon.get(canonical, 0) + amt

    short = sum(v for k, v in by_canon.items() if k in _OPM_SHORT) or None
    longt = sum(v for k, v in by_canon.items() if k in _OPM_LONG) or None
    conv = by_canon.get("OPM_CONV") or None
    other = sum(v for k, v in by_canon.items() if k in ("OPM_BORROW", "OPM_COMBINED")) or None
    total = sum(by_canon.values())
    no_rows = not by_canon and not conflicts and not reviews

    confidence = ("CONFLICT" if "CONFLICT" in tiers else
                  "REVIEW" if "REVIEW" in tiers else
                  "MED" if "MED" in tiers else "HIGH")
    if conflicts:
        flags.append(f"id↔nm 모순 {len(conflicts)}건 — 사람검토 필요(합산 제외): "
                     + ", ".join(c["account_nm"] for c in conflicts))
    if reviews:
        flags.append(f"사전 미등록 차입계열 {len(reviews)}건 — 표면화(합산 제외): "
                     + ", ".join(r["account_nm"] for r in reviews))

    return {
        "total_debt": total if by_canon else (0 if no_rows else total),
        "short_term_debt": short, "long_term_debt": longt,
        "convertible_debt_krw": conv, "other_borrowings_krw": other,
        "lease_liabilities_krw": lease or None, "hybrid_capital_krw": hybrid or None,
        "total_debt_incl_lease": (total + lease) if (by_canon or lease) else None,
        "by_canonical": by_canon, "is_financial_bs": is_fin_bs,
        "confidence": confidence, "conflicts": conflicts, "reviews": reviews,
        "data_quality_flags": flags, "no_borrowing_rows": no_rows,
    }


def _shared_ctrl_equity(rows: list[dict[str, Any]], field: str = "thstrm_amount") -> tuple[int | None, str | None]:
    """지배자본 기말잔액과 고른 길(케이스) — `price_multiple_data._ctrl_equity` 와 같은 식(261021 fin-eq).

    지배자본 계정 줄(`ifrs-full_EquityAttributableToOwnersOfParent`)을 믿을 수 있으면(`ctrl_line_rejected` 가 None) 그 값 → 케이스 `BS_ID`.
    그 줄을 「자본금」·자본 구성요소 줄에 붙였거나 자본총계 − 비지배와 크게 어긋나면 수집과 같은 다음 규칙(`controlling_eq` — 이름 줄 →
    자본총계 − 비지배 `TOTAL_MINUS_MINORITY` → 자본총계 `TOTAL`)으로 간다. 값을 못 내면 (None, 까닭) — 부르는 쪽이 자본총계 쪽으로 물러난다.
    `controlling_eq` 가 이 파일의 `normalize_amount` 를 가져오므로 순환 가져오기를 피해 함수 안에서 가져온다(`controlling_ni` 와 같다)."""
    from open_proxy_mcp.services import controlling_eq as _ceq
    eq, why = _ceq.ctrl_id_value(rows, field)
    if eq is not None and why is None:
        return int(eq), "BS_ID"
    v, case = _ceq.controlling_eq(rows, field)
    return (int(v) if v is not None else None), case


def _ctrl_basis(case: str | None) -> str:
    """공용 지배자본 케이스 → 자본잠식 `basis`. 지배 줄/이름 줄이면 `controlling`, 자본총계 − 비지배면 `derived`, 자본총계 그대로면 `total`."""
    c = case or ""
    if c.startswith("TOTAL_MINUS_MINORITY"):
        return "derived"
    if c.startswith("TOTAL"):
        return "total"
    return "controlling"


def _build_account_map_all(
    rows: list[dict[str, Any]],
    period: str = "thstrm",
    cumulative_is: bool = False,
    induty_code: str | None = None,
) -> dict[str, Any]:
    """fnlttSinglAcntAll rows → 표준 키 매핑 (CF + IS detail + BS detail).

    sj_div: BS / IS / CIS / CF / SCE.
    cumulative_is=True: IS/CIS 손익(cogs·gross_profit 등)을 누적으로 읽는다. BS(잔액)·CF
    (thstrm이 이미 누적)는 그대로 thstrm. 분기/반기 손익을 누적 기준으로 맞추기 위함.
    revenue 는 `pick_revenue_row` — 주요계정(fnlttSinglAcnt)에 매출 행이 없는 회사(영업수익·
    보험수익·이자수익 top line)의 폴백 소스가 이 맵이다.
    """
    out: dict[str, Any] = {}
    out.update({k: None for k in _BS_ACCOUNT_PATTERNS})
    out.update({k: None for k in _IS_ACCOUNT_PATTERNS})
    out.update({k: None for k in _IS_DETAIL_PATTERNS})
    out.update({k: None for k in _CF_ACCOUNT_PATTERNS})
    capital_parts: list[int] = []
    _apply_revenue_pick(out, rows, induty_code, period=period, cumulative_is=cumulative_is)

    for row in rows:
        sj_div = _strip(row.get("sj_div"))
        account_nm = _strip(row.get("account_nm"))
        account_id = _strip(row.get("account_id"))
        amount = _extract_period_amount(row, period)  # BS(잔액)·CF(누적 native)용
        is_amount = _extract_cumulative_is(row) if cumulative_is else amount  # IS/CIS 손익용

        if sj_div == "BS":
            # 지배주주 귀속 자본 줄은 여기서 값으로 채택하지 않는다 — 루프 뒤에서 수집·배수 도구와 **같은 공용 규칙**
            # (`_shared_ctrl_equity` → `services/controlling_eq`)이 고른다. 종전엔 이 계정 id 가 든 첫 줄을 그대로 써서
            # 회사가 그 계정을 「자본금」 줄에 붙인 보고서(065680 · 069960 · 000040 · 056360)의 ROE 분모·자본잠식이 틀렸다.
            if account_id and "EquityAttributableToOwnersOfParent" in account_id:
                continue
            # 비지배지분 — 지배지분 계정을 아예 안 적는 회사가 있어(실측 고려아연·비덴트·
            # 미래에셋증권) 자본총계에서 이걸 빼야 지배지분이 나온다. 자본총계로 물러나면
            # 비지배 몫만큼 자기자본이 부풀어 자본잠식이 과소 판정된다.
            # account_id 는 **정확 매칭** — 접두로 보면 다른 개념에 올라탄다(260704 사고).
            if out.get("nci") is None and account_id == "ifrs-full_NoncontrollingInterests":
                out["nci"] = amount
                continue
            if _match_account(account_nm, _CAPITAL_STOCK_PARTS) and amount is not None:
                capital_parts.append(amount)
            for key, patterns in _BS_ACCOUNT_PATTERNS.items():
                if out[key] is None and _match_account(
                        account_nm, patterns, _BS_ACCOUNT_EXCLUDE.get(key, ())):
                    out[key] = amount
                    break
            for key, patterns in _IS_DETAIL_PATTERNS.items():
                if key in {"accounts_receivable", "inventory", "accounts_payable",
                           "cash_and_equivalents"}:
                    if out[key] is None and _match_account(account_nm, patterns):
                        out[key] = amount
                        break
            # 총차입금(short_term_debt/long_term_debt/generic)은 루프 후 _compute_borrowings로
            # account_id 정확매칭 일괄 산출 — 여기서 키워드 매칭하지 않는다.
        elif sj_div in ("IS", "CIS"):
            # 귀속 순이익(지배/비지배)은 account_id로만 매칭 — account_nm이 총포괄손익 귀속과
            # 동일("지배기업 소유주지분"/"비지배지분")이라 substring으로는 구분 불가.
            attr_key = next((k for k, frag in _IS_ATTRIBUTION_ACCOUNT_ID.items()
                             if account_id and frag in account_id), None)
            if attr_key is not None:
                if out.get(attr_key) is None:
                    out[attr_key] = is_amount
                continue  # 귀속 순이익 행 — 다른 key로 재매칭 금지(총포괄손익 행 오염 방지)
            # EPS: 보통주 기본/희석을 account_id로만. total 우선, 없으면 계속영업 fallback(루프 후 채움).
            if account_id == "ifrs-full_BasicEarningsLossPerShare":
                if out.get("basic_eps") is None:
                    out["basic_eps"] = is_amount
                continue
            if account_id == "ifrs-full_DilutedEarningsLossPerShare":
                if out.get("diluted_eps") is None:
                    out["diluted_eps"] = is_amount
                continue
            if account_id.startswith("ifrs-full_BasicEarningsLossPerShareFromContinuing"):
                if out.get("_basic_eps_cont") is None:
                    out["_basic_eps_cont"] = is_amount
                continue
            if account_id.startswith("ifrs-full_DilutedEarningsLossPerShareFromContinuing"):
                if out.get("_diluted_eps_cont") is None:
                    out["_diluted_eps_cont"] = is_amount
                continue
            for key, patterns in _IS_ACCOUNT_PATTERNS.items():
                if key == "revenue":
                    continue  # pick_revenue_row 로 결정
                if out[key] is None and _match_account(account_nm, patterns):
                    out[key] = is_amount
                    break
            for key, patterns in _IS_DETAIL_PATTERNS.items():
                if key in {"gross_profit", "cogs", "interest_expense"}:  # EPS는 위 account_id 전용
                    if out[key] is None and _match_account(account_nm, patterns):
                        out[key] = is_amount
                        break
        elif sj_div == "CF":
            # 이자지급 변형은 정확 일치 우선 (substring FP 방지 — _INTEREST_PAID_EXACT 주석 참조)
            if out.get("interest_paid") is None and account_nm and account_nm.replace(" ", "") in _INTEREST_PAID_EXACT:
                if amount is not None:
                    out["interest_paid"] = amount
                continue
            for key, patterns in _CF_ACCOUNT_PATTERNS.items():
                if out[key] is None and _match_account(account_nm, patterns):
                    out[key] = amount
                    break
    # 261021(fin-eq): 지배자본도 지배 순이익처럼 수집 배치·price_multiple_data(firm)와 **같은 규칙**으로 고른다.
    out["controlling_equity"], out["controlling_equity_case"] = _shared_ctrl_equity(rows, f"{period}_amount")
    # 261020(p1e firm-ctrl-ni): 지배 순이익은 수집 배치·price_multiple_data(firm)와 **같은 규칙**(`services/controlling_ni` — 값 구조 검사)으로 고른다.
    #   위 루프는 지배 귀속 순이익 계정 id 가 든 첫 줄을 그대로 썼고(없으면 아래 요약에서 주요계정 당기순이익 총계 — 비지배 포함), 그래서 그 줄에
    #   총포괄이익의 지배 몫을 적은 회사(075180 FY2025 27.68억 — 당기순이익은 −5.15억)·표준 계정코드 없이 이름으로 지배·비지배를 나눈 회사(비지배
    #   포함 총계)에서 ROE·순이익률·순이익 전년비가 firm·DB 와 달랐다. 손익 칸은 위와 같다(누적이면 줄마다 당기 누적 → 없으면 당기).
    from open_proxy_mcp.services.controlling_ni import controlling_ni as _shared_ctrl_ni   # 순환 가져오기를 피해 여기서
    _ni_c = _shared_ctrl_ni(rows)[0] if cumulative_is else _shared_ctrl_ni(rows, f"{period}_amount")[0]
    out["controlling_interest_income"] = int(round(_ni_c)) if _ni_c is not None else None
    # EPS: total 행이 없으면 계속영업 EPS로 채움 (분리공시 회사 — 한화에어로·효성중공업 등)
    if out.get("basic_eps") is None and out.get("_basic_eps_cont") is not None:
        out["basic_eps"] = out["_basic_eps_cont"]
    if out.get("diluted_eps") is None and out.get("_diluted_eps_cont") is not None:
        out["diluted_eps"] = out["_diluted_eps_cont"]
    # 총차입금 — account_id 전체명 정확매칭 일괄 산출(BS 잔액 기준, cumulative_is 무관)
    borrow = _compute_borrowings(rows, period=period)
    out["_borrowing"] = borrow
    out["short_term_debt"] = borrow["short_term_debt"]
    out["long_term_debt"] = borrow["long_term_debt"]
    out["borrowings_generic"] = borrow["other_borrowings_krw"]
    # 부모 「자본금」 행이 없고 종류주 행만 있는 표 — 합쳐야 자본금이다(위 _build_account_map 과 동일).
    if out.get("capital_stock") is None and capital_parts:
        out["capital_stock"] = sum(capital_parts)
    return out


def _safe_div(
    numer: int | float | None,
    denom: int | float | None,
    *,
    positive_denom_only: bool = False,
) -> float | None:
    """0 분모 / None graceful.

    positive_denom_only=True: 분모가 음수면 None 반환 (ROE/ROA/배당성향 등 — 자본 음수 = 채무초과 회사).
    positive_denom_only=False (default): 분모 부호 보존 (이자보상배율 — 영업이익 음수면 음수 ratio가 의미 있음).
    """
    if numer is None or denom is None:
        return None
    if denom == 0:
        return None
    if positive_denom_only and denom < 0:
        return None
    return numer / denom


def _safe_pct(
    numer: int | float | None,
    denom: int | float | None,
    *,
    positive_denom_only: bool = False,
) -> float | None:
    """비율을 % (×100) 로. round 2자리."""
    r = _safe_div(numer, denom, positive_denom_only=positive_denom_only)
    if r is None:
        return None
    return round(r * 100, 2)


def _safe_ratio(
    numer: int | float | None,
    denom: int | float | None,
    *,
    positive_denom_only: bool = False,
) -> float | None:
    """비율을 decimal 그대로. round 4자리."""
    r = _safe_div(numer, denom, positive_denom_only=positive_denom_only)
    if r is None:
        return None
    return round(r, 4)


def _avg(a: int | float | None, b: int | float | None) -> float | None:
    if a is None or b is None:
        return None
    return (a + b) / 2


def _turnover_days(
    balance: int | float | None,
    denom: int | float | None,
    period_months: int = 12,
) -> float | None:
    """회전일수 = 평균 balance / 기간 flow * 기간일수.

    denom(매출·COGS)이 분기/반기 flow면 period_months로 기간을 맞춘다 (분기=3 → ×91.25일).
    기존엔 ×365 고정이라 분기 데이터에 적용 시 ~4배 과대됐다 (DIO 511일 등).
    금융업/지주사처럼 분모 계정이 없거나 0 이하인 경우 무리해서 산출하지 않는다.
    """
    r = _safe_div(balance, denom, positive_denom_only=True)
    if r is None:
        return None
    return round(r * 365 * period_months / 12, 1)


def _compute_metrics(
    *,
    bs_is: dict[str, int | None],
    bs_is_prev: dict[str, int | None] | None,
    detail: dict[str, int | None] | None,
    detail_prev: dict[str, int | None] | None,
    indx_map: dict[str, float | None] | None,
    period_months: int = 12,
    ttm_revenue: int | None = None,
    ttm_cogs: int | None = None,
    induty_code: str | None = None,
) -> dict[str, Any]:
    """단일 사업연도 metrics 계산 (수익성/안정성/현금흐름/운전자본/회계risk/배당유보/NAV).

    bs_is: 당기 fnlttSinglAcnt 매핑.
    bs_is_prev: 전기 fnlttSinglAcnt 매핑 (평균자산/평균자본 계산용. None이면 thstrm 단독 사용).
    detail: 당기 fnlttSinglAcntAll 매핑 (CF + 세부 IS/BS).
    detail_prev: 전기 fnlttSinglAcntAll 매핑 (NWC 변동 계산용).
    indx_map: fnlttSinglIndx에서 추출한 DART 산출 지표 (보조 — 자체 계산 우선).
    """
    detail = detail or {}
    detail_prev = detail_prev or {}
    indx_map = indx_map or {}

    # 매출: 주요계정(fnlttSinglAcnt)에 있으면 그것, 없으면 전체 재무제표(fnlttSinglAcntAll)에서
    # 고른 행. 리파인·티움바이오(영업수익)·보험(보험수익)·은행(이자수익)은 주요계정에 매출 행이
    # 없어 여기서만 잡힌다. 어느 계정을 썼는지 같이 내보낸다 — 「보험수익 기준」 영업이익률을
    # 제조업 마진처럼 읽으면 안 되니까.
    revenue_src = bs_is if bs_is.get("revenue") is not None else (detail if detail.get("revenue") is not None else None)
    revenue = revenue_src.get("revenue") if revenue_src else None
    revenue_account_nm = revenue_src.get("revenue_account_nm") if revenue_src else None
    revenue_account_id = revenue_src.get("revenue_account_id") if revenue_src else None
    revenue_standard = revenue_src.get("revenue_standard") if revenue_src else None
    revenue_basis = revenue_src.get("revenue_basis") if revenue_src else None
    revenue_source = None if revenue_src is None else ("fnlttSinglAcnt" if revenue_src is bs_is else "fnlttSinglAcntAll")
    operating_profit = bs_is.get("operating_profit")
    net_income = bs_is.get("net_income")
    total_assets = bs_is.get("total_assets")
    total_equity = bs_is.get("total_equity")
    total_liabilities = bs_is.get("total_liabilities")
    current_assets = bs_is.get("current_assets")
    current_liabilities = bs_is.get("current_liabilities")
    retained_earnings = bs_is.get("retained_earnings")
    capital_stock = bs_is.get("capital_stock")  # 자본금 (액면가 × 발행주식수)

    # ── 자본잠식 (Capital Impairment) ──
    # 잠식률 = (자본금 − **자기자본**) / 자본금 × 100
    #   — 코스닥시장 공시·상장관리 해설서 「자본잠식률[(자본금-자기자본)/자본금*100]이 50% 이상」
    #
    # **자기자본에서 비지배지분을 뺀다.** 같은 해설서 「적용기준 ① 연결재무제표 작성대상법인의
    # 경우에는 연결재무제표를 기준으로 하되 **자기자본에서 비지배지분을 제외**」.
    # 규정마다 다르다는 점이 중요하다 — 바로 옆 「법인세비용차감전계속사업손실」 기준은
    # 「연결 기준, **비지배지분 포함**」이다. 일부러 갈라놓은 것이라 한쪽 관행을 다른 쪽에 쓰면 안 된다.
    # 비지배지분을 포함하면 자회사 소수주주 몫만큼 자기자본이 부풀어 **잠식률이 과소 산정**된다.
    #
    # - 0% 미만: 정상 / 0~50%: 부분 / 50%↑: 관리종목(2년 연속이면 상장폐지) / 자기자본 ≤ 0: 완전
    #
    # 261021(fin-eq): 지배자본은 수집·배수 도구와 **같은 공용 규칙**(`_shared_ctrl_equity`)이 고른 값이다 — 지배자본 계정 줄을 「자본금」 줄에
    # 붙인 보고서(000040 FY2019: 지배자본 계정이 납입자본 줄 1,054.57억 — 자본총계 352.10억 − 비지배 166.69억 = 지배자본 185.41억)는 그 줄을 믿지 않고 자본총계 − 비지배로 간다. 공용 규칙이 값을 못 내면
    # (전체 재무제표가 없거나 자본총계 줄이 없음) 종전처럼 주요계정 값 → 자본총계 − 비지배 → 자본총계 순.
    _ctrl_equity = detail.get("controlling_equity")
    _ctrl_case = detail.get("controlling_equity_case") if _ctrl_equity is not None else None
    if _ctrl_equity is None:
        _ctrl_equity = bs_is.get("controlling_equity")
        _ctrl_case = "ACNT_NAME" if _ctrl_equity is not None else None
    _imp = compute_capital_impairment(
        capital_stock=capital_stock,
        controlling_equity=_ctrl_equity,
        total_equity=total_equity,
        nci=detail.get("nci") if isinstance(detail, dict) else None,
        controlling_basis=_ctrl_basis(_ctrl_case),
    )
    impairment_equity = _imp["equity_used"]
    capital_impairment_basis = _imp["basis"]
    capital_impairment_ratio_pct = _imp["ratio_pct"]
    capital_impairment_status = _imp["status"]
    capital_impairment_ratio_total_pct = _imp["ratio_total_pct"]

    # 평균값 (BS 전기 데이터 있으면)
    avg_assets = _avg(total_assets, (bs_is_prev or {}).get("total_assets")) if bs_is_prev else total_assets

    # ROE 분모 = 평균 '지배주주 귀속 자본' (국내 표준). 주요계정엔 지배자본이 없어
    # fnlttSinglAcntAll(detail)에서 가져온다. 지배자본 없으면(별도재무·결손) 자본총계로 fallback.
    #
    # 261021(fin-eq): 분모는 위 자본잠식과 같은 **공용 지배자본**이다. 지배자본 줄이 없는 보고서(419050 FY2025)에서 종전엔 비지배를
    # 품은 자본총계를 분모로 써 ROE 가 낮게 나왔다 — 이제 공용 규칙의 자본총계 − 비지배, 전체 재무제표가 없어 그것도 못 구할 때만 주요계정 자본총계에서
    # 비지배(알면)를 뺀 값 → 자본총계 순이다. 어느 길로 골랐는지 `controlling_equity_case` 에 남긴다.
    def _roe_equity(det: dict, acnt: dict | None, total: int | float | None) -> tuple[int | float | None, str]:
        v = det.get("controlling_equity")
        if v is not None:
            return v, det.get("controlling_equity_case") or "BS_ID"
        v = (acnt or {}).get("controlling_equity")
        if v is not None:
            return v, "ACNT_NAME"
        nci_ = det.get("nci")
        if total is not None and nci_ is not None:
            return total - nci_, "TOTAL_MINUS_NCI"
        return total, "TOTAL_FALLBACK" if total is not None else "NO_EQUITY"

    equity_for_roe, controlling_equity_case = _roe_equity(detail, bs_is, total_equity)
    prev_equity_for_roe, _prev_case = _roe_equity(detail_prev, bs_is_prev, (bs_is_prev or {}).get("total_equity"))
    avg_equity = _avg(equity_for_roe, prev_equity_for_roe) if bs_is_prev else equity_for_roe

    # detail (fnlttSinglAcntAll)
    gross_profit = detail.get("gross_profit")
    cogs = detail.get("cogs")
    if gross_profit is None and revenue is not None and cogs is not None:
        gross_profit = revenue - cogs

    cfo = detail.get("cfo")
    capex = detail.get("capex")
    depreciation = detail.get("depreciation")
    amortization = detail.get("amortization")
    da = None
    if depreciation is not None or amortization is not None:
        da = (depreciation or 0) + (amortization or 0)
    # IS에 '이자비용' 행이 없는 회사(SK하이닉스 등)는 CF '이자의 지급'으로 fallback —
    # '금융비용' 총액(환손·평가손 포함)을 쓰면 이자보상배율이 수십 배 왜곡된다.
    interest_expense = detail.get("interest_expense")
    if interest_expense is None:
        interest_expense = detail.get("interest_paid")
    cash_and_equivalents = detail.get("cash_and_equivalents")
    borrow = detail.get("_borrowing") or {}
    short_term_debt = borrow.get("short_term_debt")
    long_term_debt = borrow.get("long_term_debt")
    accounts_receivable = detail.get("accounts_receivable")
    inventory = detail.get("inventory")
    accounts_payable = detail.get("accounts_payable")
    prev_accounts_receivable = detail_prev.get("accounts_receivable")
    prev_inventory = detail_prev.get("inventory")
    prev_accounts_payable = detail_prev.get("accounts_payable")
    diluted_eps_per_share = detail.get("diluted_eps")  # 원/주
    basic_eps_per_share = detail.get("basic_eps")
    controlling_ni = detail.get("controlling_interest_income")

    # net_income은 한국 표준 = 지배주주 귀속. fnlttSinglAcnt의 "당기순이익(손실)"은 보통 합계.
    # detail에서 controlling 분리되면 우선 사용.
    if controlling_ni is not None:
        net_income_controlling = controlling_ni
    else:
        net_income_controlling = net_income

    # ── prev year (yoy 계산용) — 260505 ralph precision iter 2 ──
    prev_revenue = (bs_is_prev or {}).get("revenue") if bs_is_prev else None
    if prev_revenue is None and detail_prev:
        prev_revenue = detail_prev.get("revenue")  # 당기와 같은 폴백 — 전년비가 한쪽만 비지 않게
    prev_operating_profit = (bs_is_prev or {}).get("operating_profit") if bs_is_prev else None
    prev_net_income_total = (bs_is_prev or {}).get("net_income") if bs_is_prev else None
    prev_controlling_ni = (detail_prev or {}).get("controlling_interest_income") if detail_prev else None
    prev_net_income_controlling = prev_controlling_ni if prev_controlling_ni is not None else prev_net_income_total

    def _yoy_pct(curr, prev):
        if curr is None or prev is None or prev == 0:
            return None
        return round((curr - prev) / abs(prev) * 100, 2)

    revenue_yoy_pct = _yoy_pct(revenue, prev_revenue)
    operating_profit_yoy_pct = _yoy_pct(operating_profit, prev_operating_profit)
    # 순이익 기준 표시 (DATA-NI2 의 연간 변형) — 지배 순이익을 못 구하면(전체 재무제표 조회 실패·MINORITY_NO_CTRL·NO_INCOME_ROW 꼴) 위에서 조용히 총계(비지배 포함)로 물러난다.
    # 당기와 전기가 따로 물러나면 전년비가 기준이 섞인 값이 되므로(당기 총계 100 대 전기 지배 50 → +100%, 같은 기준이면 +20%) 기준이 다를 때는 전년비를 내지 않는다.
    net_income_basis = (NET_INCOME_BASIS_CONTROLLING if controlling_ni is not None
                        else NET_INCOME_BASIS_TOTAL if net_income is not None else None)
    prev_net_income_basis = (NET_INCOME_BASIS_CONTROLLING if prev_controlling_ni is not None
                             else NET_INCOME_BASIS_TOTAL if prev_net_income_total is not None else None)
    ni_basis_mixed = (net_income_basis is not None and prev_net_income_basis is not None
                      and net_income_basis != prev_net_income_basis)
    net_income_yoy_pct = None if ni_basis_mixed else _yoy_pct(net_income_controlling, prev_net_income_controlling)
    net_income_basis_note = None
    if ni_basis_mixed:
        net_income_basis_note = (f"전기·당기 순이익 기준이 달라 전년비를 내지 않음 (당기 {net_income_basis} · 전기 {prev_net_income_basis}) — "
                                 f"지배 순이익을 구하지 못한 해는 비지배 포함 총계로 적혀 있다.")
    elif net_income_basis == NET_INCOME_BASIS_TOTAL and detail.get("nci") != 0:
        # 비지배지분이 0 이면 총계 = 지배라 안전하다. 있거나 모르면 ROE(분자 총계 ÷ 지배자본)·순이익률이 지배 기준보다 높게 나올 수 있다.
        net_income_basis_note = ("지배 순이익을 구하지 못해 비지배 포함 총계 순이익으로 ROE·순이익률을 냈다 — "
                                 "비지배지분이 있으면 지배 기준보다 높게 나온다.")

    # 총차입금 = _compute_borrowings sum-all(단기+장기+전환+기타차입). account_id 정확매칭.
    # 리스부채·신종자본증권은 별도(리스=IFRS16 별도, 신종자본=IFRS 자본).
    total_debt = borrow.get("total_debt")
    # 금융사 = 연결 BS 신호(예수부채 등) OR KSIC 업종(수신 없는 카드·캐피탈·VC 보완)
    is_financial_company = bool(borrow.get("is_financial_bs")) or _ksic_is_financial(induty_code)
    total_debt_confidence = borrow.get("confidence")

    # 순현금
    net_cash = None
    if cash_and_equivalents is not None and total_debt is not None:
        net_cash = cash_and_equivalents - total_debt

    # ── 수익성 ──
    operating_margin_pct = _safe_pct(operating_profit, revenue)
    gross_margin_pct = _safe_pct(gross_profit, revenue)
    net_profit_margin_pct = _safe_pct(net_income_controlling, revenue)
    # EBITDA는 D&A가 실제로 추출됐을 때만 산출. 삼성전자류는 연결 CF에 감가상각비를
    # '조정' 합계로만 공시(상세는 주석)해 da=None — 이때 OP+0=OP로 내보내면
    # "EBITDA = 영업이익"이라는 무의미한 값이 그럴듯하게 표시됨 (6사 audit서 5사 실측).
    ebitda_krw = None
    if operating_profit is not None and da is not None:
        ebitda_krw = operating_profit + da
    ebitda_margin_pct = _safe_pct(ebitda_krw, revenue)

    # ROE / ROA — 평균자산/평균자본 (전기 없으면 기말 단독). 분모 음수(채무초과) 시 None.
    # ROE = 지배순이익 / 평균 지배자본 (주주 귀속 기준). ROA = 전체순이익 / 평균자산
    # (총자산은 전체 자본·부채로 조달 → 분자도 전체 순이익이 정합적. 국내 데이터 공급처와 같은 규약).
    net_income_total_for_roa = net_income if net_income is not None else net_income_controlling
    roe_pct = _safe_pct(net_income_controlling, avg_equity, positive_denom_only=True)
    roa_pct = _safe_pct(net_income_total_for_roa, avg_assets, positive_denom_only=True)

    # ROIC = NOPAT / 투하자본. 단순 근사: 영업이익 × (1 - 0.22 평균법인세율) / (자본 + 총차입)
    nopat = None
    invested_capital = None
    roic_pct = None
    if operating_profit is not None:
        nopat = operating_profit * (1 - 0.22)  # 한국 평균 법인세 22%
    if total_equity is not None and total_debt is not None:
        invested_capital = total_equity + total_debt
    # 투하자본도 음수면 None (자본+차입이 동시에 음수 = 비정상)
    roic_pct = _safe_pct(nopat, invested_capital, positive_denom_only=True)

    # ── 듀퐁 3단 ──
    asset_turnover_ratio = _safe_ratio(revenue, avg_assets, positive_denom_only=True)
    # 평균자본 음수 (채무초과) 시 equity_multiplier는 None — ROE 분해 의미 없음
    equity_multiplier = _safe_ratio(avg_assets, avg_equity, positive_denom_only=True)
    # ROE 검증 (3단 곱)
    roe_dupont_pct = None
    if net_profit_margin_pct is not None and asset_turnover_ratio is not None and equity_multiplier is not None:
        roe_dupont_pct = round(
            (net_profit_margin_pct / 100) * asset_turnover_ratio * equity_multiplier * 100, 2
        )

    # ── 안정성 ──
    # 부채비율 — 분모 자본 양수 가정 (채무초과 시 비율 의미 X → None)
    debt_ratio_pct = _safe_pct(total_liabilities, total_equity, positive_denom_only=True)
    current_ratio_pct = _safe_pct(current_assets, current_liabilities, positive_denom_only=True)
    # 이자보상배율 — 영업이익(분자) 음수면 ratio 음수가 의미 있음 (적자 가시성). 분모만 양수 요구.
    interest_coverage_ratio = _safe_ratio(operating_profit, interest_expense, positive_denom_only=True) if (
        interest_expense is not None and interest_expense > 0
    ) else None
    # 금융사(은행·보험·증권·금융지주)는 예수부채 등 영업조달이 섞여 총차입/총자산 비율이
    # 무의미 → None + 상태필드. 일반지주(SK·LG 등, 연결 예수부채 없음)는 정상 산출.
    if is_financial_company:
        debt_dependency_pct = None
        debt_dependency_status = "n/a_financial"
    else:
        debt_dependency_pct = _safe_pct(total_debt, total_assets, positive_denom_only=True)
        debt_dependency_status = "computed" if debt_dependency_pct is not None else "no_data"

    # ── 현금흐름 ──
    fcf_krw = None
    if cfo is not None or capex is not None:
        # capex는 보통 음수 (현금유출). 절대값 처리.
        fcf_krw = (cfo or 0) - abs(capex or 0)
        if cfo is None and capex is None:
            fcf_krw = None
    fcf_margin_pct = _safe_pct(fcf_krw, revenue)
    cfo_to_op_ratio = _safe_ratio(cfo, operating_profit)
    cfo_to_net_income_ratio = _safe_ratio(cfo, net_income_controlling)
    capex_to_da_ratio = None
    if capex is not None and da:
        capex_to_da_ratio = _safe_ratio(abs(capex), abs(da))

    # ── 운전자본 ──
    working_capital_krw = None
    if current_assets is not None and current_liabilities is not None:
        working_capital_krw = current_assets - current_liabilities
    nwc_krw = None
    if accounts_receivable is not None or inventory is not None or accounts_payable is not None:
        nwc_krw = (accounts_receivable or 0) + (inventory or 0) - (accounts_payable or 0)
        # 모두 None이면 진짜 없음
        if accounts_receivable is None and inventory is None and accounts_payable is None:
            nwc_krw = None
    nwc_change_yoy_krw = None
    if nwc_krw is not None and detail_prev:
        prev_ar = detail_prev.get("accounts_receivable")
        prev_inv = detail_prev.get("inventory")
        prev_ap = detail_prev.get("accounts_payable")
        if prev_ar is not None or prev_inv is not None or prev_ap is not None:
            prev_nwc = (prev_ar or 0) + (prev_inv or 0) - (prev_ap or 0)
            nwc_change_yoy_krw = nwc_krw - prev_nwc
    nwc_to_revenue_pct = _safe_pct(nwc_krw, revenue)
    avg_accounts_receivable = (
        _avg(accounts_receivable, prev_accounts_receivable)
        if prev_accounts_receivable is not None
        else accounts_receivable
    )
    avg_inventory = (
        _avg(inventory, prev_inventory)
        if prev_inventory is not None
        else inventory
    )
    avg_accounts_payable = (
        _avg(accounts_payable, prev_accounts_payable)
        if prev_accounts_payable is not None
        else accounts_payable
    )
    # 회전일수 분모: TTM(최근 4분기) flow가 있으면 우선 — 분기 단일 flow를 연환산하며 생기는
    # 호황/급변 왜곡(예: AR 3배인데 DSO 하락)을 제거해 연간과 직접 비교 가능. TTM이면 ×365.
    # 없으면 기간일치(period_months)로 fallback.
    _use_ttm_rev = ttm_revenue is not None and ttm_revenue > 0
    _use_ttm_cogs = ttm_cogs is not None and ttm_cogs > 0
    days_sales_outstanding = (
        _turnover_days(avg_accounts_receivable, ttm_revenue, 12) if _use_ttm_rev
        else _turnover_days(avg_accounts_receivable, revenue, period_months)
    )
    days_inventory_outstanding = (
        _turnover_days(avg_inventory, ttm_cogs, 12) if _use_ttm_cogs
        else _turnover_days(avg_inventory, cogs, period_months)
    )
    days_payable_outstanding = (
        _turnover_days(avg_accounts_payable, ttm_cogs, 12) if _use_ttm_cogs
        else _turnover_days(avg_accounts_payable, cogs, period_months)
    )
    turnover_basis = "ttm" if _use_ttm_rev else ("annual" if period_months == 12 else "period_matched")
    cash_conversion_cycle_days = None
    if (
        days_sales_outstanding is not None
        and days_inventory_outstanding is not None
        and days_payable_outstanding is not None
    ):
        cash_conversion_cycle_days = round(
            days_sales_outstanding + days_inventory_outstanding - days_payable_outstanding,
            1,
        )

    # ── 회계 risk ──
    # 비율만 주면 분모(영업이익)가 적자·0 근처일 때 부호가 뒤집히고 크기가 뻥튀기된다.
    # 하이퍼코퍼레이션 2025 실측 — 영업이익 -42.8억 / CFO -82.9억이라 현금이 이익보다 40억
    # **더** 빠졌는데 비율은 -93.67%(= 현금이 더 좋아 보이는 부호)로 나온다. 금액차를 같이
    # 내고 비율의 신뢰도를 표시한다(260828 U 지적 D-9).
    accruals_gap_pct = None
    accruals_gap_krw = None
    accruals_gap_reliability = None
    if operating_profit is not None and cfo is not None:
        accruals_gap_krw = operating_profit - cfo
        if operating_profit != 0:
            accruals_gap_pct = round((operating_profit - cfo) / operating_profit * 100, 2)
        if operating_profit is None or operating_profit <= 0:
            accruals_gap_reliability = "negative_op"
        elif revenue and abs(operating_profit) < abs(revenue) * 0.02:
            accruals_gap_reliability = "thin_op"
        else:
            accruals_gap_reliability = "ok"
    ar_to_revenue_pct = _safe_pct(accounts_receivable, revenue)
    inv_to_revenue_pct = _safe_pct(inventory, revenue)

    # ── 배당 / 유보 ──
    # 배당총액 = -dividends_paid (CF는 음수). 별도 alotMatter 호출은 dividend tool 책임.
    dividend_paid_krw = None
    dp = detail.get("dividends_paid")
    if dp is not None:
        dividend_paid_krw = abs(dp)
    # 배당성향(payout_ratio_pct)은 여기(CF '배당금지급' ÷ 순이익)서 계산하지 않는다. CF 배당지급은
    # 현금지급 타이밍(대개 전년 결산배당) + 연결 전체(지배+비지배) 기준이라, 귀속 배당성향과 분자의
    # 연도·주체가 어긋난다(260716 검증: 54사 |diff| 중앙 6.4%p·최대 127%p, >100% 뻥튀기·금융주 과소).
    # 실제 값은 _fetch_year_metrics에서 DART 사업보고서 '현금배당성향'(귀속·연결, dividend 툴과 동일
    # SSOT)으로 주입한다. dividend_paid_krw는 아래 배당/FCF(현금 coverage) 지표 전용으로만 유지.
    payout_ratio_pct = None
    dividend_to_fcf_pct = _safe_pct(dividend_paid_krw, fcf_krw) if (
        fcf_krw is not None and fcf_krw > 0
    ) else None

    # ── NAV / 주식 ──
    nav_krw = total_equity  # 자본총계 = 자산-부채
    # BPS는 발행주식수 필요 — fnltt에는 없으므로 stockTotqySttus 별도 호출 필요 (Phase 2).
    # detail에 basic_eps_per_share / diluted_eps_per_share가 있으면 그대로 사용 (원/주).
    eps_krw = basic_eps_per_share
    diluted_eps_krw = diluted_eps_per_share
    bps_krw = None  # 이 tool 은 BPS 를 내지 않는다(항상 None). BPS·PBR 은 price_multiple_data 가 계산.

    # ── 지배구조 cross-check ──
    # subsidiary_count: 종속회사 수. DART OpenAPI 4 endpoint 어디에도 직접 반환 X.
    # 사업보고서 본문 "종속회사 명단" 섹션 파싱이 필요해 미구현 — 항상 None.
    subsidiary_count = None

    return {
        # ── 수익성 ──
        "revenue_krw": revenue,
        # 매출을 어느 계정에서 읽었나 — 「매출액」이 아니면(영업수익·보험수익·이자수익) standard=False.
        "revenue_account_nm": revenue_account_nm,
        "revenue_account_id": revenue_account_id,
        "revenue_standard": revenue_standard,
        "revenue_basis": revenue_basis,  # 사람용 기준명(매출액/영업수익/보험수익/이자수익…) — 원문명은 revenue_account_nm
        "revenue_source": revenue_source,  # fnlttSinglAcnt(주요계정) / fnlttSinglAcntAll(전체 재무제표)
        "gross_profit_krw": gross_profit,
        "operating_profit_krw": operating_profit,
        "operating_margin_pct": operating_margin_pct,
        "gross_margin_pct": gross_margin_pct,
        "ebitda_krw": ebitda_krw,
        "ebitda_margin_pct": ebitda_margin_pct,
        "net_income_krw": net_income_controlling,  # 지배주주 귀속 (한국 표준)
        "net_income_total_krw": net_income,  # 합계 (참고용)
        # 새 키 — net_income_krw 가 실제로 어느 기준 값인가(지배 순이익을 못 구하면 total_incl_nci). 옛 키·값은 그대로.
        "net_income_basis": net_income_basis,
        "prev_net_income_basis": prev_net_income_basis,
        "net_income_basis_note": net_income_basis_note,
        "net_profit_margin_pct": net_profit_margin_pct,
        # ── prev year + yoy (260505 ralph precision iter 2 — 흑자+yoy<0 trigger 활성화용) ──
        "prev_revenue_krw": prev_revenue,
        "prev_operating_profit_krw": prev_operating_profit,
        "prev_net_income_krw": prev_net_income_controlling,
        "revenue_yoy_pct": revenue_yoy_pct,
        "operating_profit_yoy_pct": operating_profit_yoy_pct,
        "net_income_yoy_pct": net_income_yoy_pct,
        "eps_krw": eps_krw,
        "diluted_eps_krw": diluted_eps_krw,
        "roe_pct": roe_pct,
        "roa_pct": roa_pct,
        "roic_pct": roic_pct,
        # ── 듀퐁 ──
        "asset_turnover_ratio": asset_turnover_ratio,
        "equity_multiplier": equity_multiplier,
        "roe_dupont_pct": roe_dupont_pct,  # 검증값 — roe_pct와 일치 확인용
        # ── 안정성 ──
        "total_assets_krw": total_assets,
        "total_liabilities_krw": total_liabilities,
        "total_equity_krw": total_equity,
        "current_assets_krw": current_assets,
        "current_liabilities_krw": current_liabilities,
        "debt_ratio_pct": debt_ratio_pct,
        "current_ratio_pct": current_ratio_pct,
        "interest_coverage_ratio": interest_coverage_ratio,
        "debt_dependency_pct": debt_dependency_pct,
        "debt_dependency_status": debt_dependency_status,
        "total_debt_krw": total_debt,
        "total_debt_confidence": total_debt_confidence,
        "total_debt_incl_lease_krw": borrow.get("total_debt_incl_lease"),
        "short_term_debt_krw": short_term_debt,
        "long_term_debt_krw": long_term_debt,
        "convertible_debt_krw": borrow.get("convertible_debt_krw"),
        "lease_liabilities_krw": borrow.get("lease_liabilities_krw"),
        "hybrid_capital_krw": borrow.get("hybrid_capital_krw"),
        "is_financial_company": is_financial_company,
        "borrowing_detail": {
            "by_canonical_id": borrow.get("by_canonical") or {},
            "convertible_included": bool(borrow.get("convertible_debt_krw")),
            "conflicts": borrow.get("conflicts") or [],
            "reviews": borrow.get("reviews") or [],
            "data_quality_flags": borrow.get("data_quality_flags") or [],
            "no_borrowing_rows": bool(borrow.get("no_borrowing_rows")),
        },
        "net_cash_krw": net_cash,
        "cash_and_equivalents_krw": cash_and_equivalents,
        # ── 현금흐름 ──
        "cfo_krw": cfo,
        "capex_krw": capex,
        "fcf_krw": fcf_krw,
        "fcf_margin_pct": fcf_margin_pct,
        "cfo_to_op_ratio": cfo_to_op_ratio,
        "cfo_to_net_income_ratio": cfo_to_net_income_ratio,
        "capex_to_da_ratio": capex_to_da_ratio,
        # ── 운전자본 ──
        "working_capital_krw": working_capital_krw,
        "nwc_krw": nwc_krw,
        "nwc_change_yoy_krw": nwc_change_yoy_krw,
        "nwc_to_revenue_pct": nwc_to_revenue_pct,
        "days_sales_outstanding": days_sales_outstanding,
        "days_inventory_outstanding": days_inventory_outstanding,
        "days_payable_outstanding": days_payable_outstanding,
        "cash_conversion_cycle_days": cash_conversion_cycle_days,
        "turnover_basis": turnover_basis,  # ttm / annual / period_matched
        # 기간 메타 — 회전일수는 ttm(우선) 또는 period_months로 기간 보정됨. ROE/ROA/자산회전율은
        # 이 기간(분기/반기) flow 기준이라 12 미만이면 '연환산 아님'(분기값)으로 해석할 것.
        "period_months": period_months,
        "is_annualized_basis": period_months == 12,
        # ── 회계 risk ──
        "accruals_gap_pct": accruals_gap_pct,
        "accruals_gap_krw": accruals_gap_krw,          # 영업이익 − 영업CF (부호 왜곡 없는 원본)
        "accruals_gap_reliability": accruals_gap_reliability,  # ok / negative_op / thin_op
        "ar_to_revenue_pct": ar_to_revenue_pct,
        "inv_to_revenue_pct": inv_to_revenue_pct,
        # ── 배당/유보 ──
        "dividend_paid_krw": dividend_paid_krw,
        "payout_ratio_pct": payout_ratio_pct,
        "dividend_to_fcf_pct": dividend_to_fcf_pct,
        "retained_earnings_krw": retained_earnings,
        # ── NAV/주식 ──
        "nav_krw": nav_krw,
        "bps_krw": bps_krw,  # 항상 None — price_multiple_data 참조
        "capital_stock_krw": capital_stock,
        # ── 자본잠식 (KOSDAQ 관리/폐지 사유 detect) ──
        "capital_impairment_ratio_pct": capital_impairment_ratio_pct,
        "capital_impairment_status": capital_impairment_status,
        # 어느 자기자본으로 쟀는지 — 규정은 비지배지분 제외이고, 못 구하면 자본총계로 물러난다.
        "capital_impairment_basis": capital_impairment_basis,
        "capital_impairment_ratio_total_pct": capital_impairment_ratio_total_pct,
        # ── ROE 분모의 지배자본 (261021 fin-eq) ──
        # 기말 지배자본(= ROE·자본잠식의 자기자본)과 고른 길 — `BS_ID`(지배자본 계정 줄)·`BS_NM`(이름 줄)·`TOTAL_MINUS_MINORITY`(자본총계 − 비지배)·
        # `TOTAL`(비지배 없음) 또는 줄을 믿지 않았다는 꼬리표 `_CAPLINE`·`_COMPLINE`·`_OFFTOTAL`; 공용 규칙이 못 낸 경우만 `ACNT_NAME`·`TOTAL_MINUS_NCI`·
        # `TOTAL_FALLBACK`. ROE 는 (이 값 + 전기 값) / 2 가 분모다.
        "controlling_equity_krw": equity_for_roe,
        "controlling_equity_case": controlling_equity_case,
        "prev_controlling_equity_krw": prev_equity_for_roe if bs_is_prev else None,
        # ── 지배구조 cross-check ──
        "subsidiary_count": subsidiary_count,  # Phase 2 — 사업보고서 본문 파싱 필요
        # ── DART 산출 지표 (보조) ──
        "dart_indx": indx_map,
    }


#: 두 값의 **기간**이 다르면(당기 누적 N개월 대 전기 연간 12개월) 거짓으로 켜지는 경고 — DATA-Q6.
#: 매출·ROE·회전(매출 분모)·배당 지급액은 모두 기간 합계라 기간이 다르면 비교가 무의미하다.
_PERIOD_SENSITIVE_ALERTS = frozenset({
    "revenue_decline", "roe_decline_margin_driven", "roe_decline_turnover_driven",
    "receivables_surge", "inventory_surge", "dividend_halt",
})


def _period_ko(m: dict[str, Any]) -> str:
    """metrics 한 벌의 손익 기간을 사람 말로 — 「연간 12개월」·「9개월 누적」."""
    pb = str(m.get("period_basis") or "")
    if pb.startswith("cumulative_") and pb.endswith("m"):
        return f"{pb[len('cumulative_'):-1]}개월 누적"
    if pb == "quarter_3m":
        return "3개월"
    return "연간 12개월"


def _detect_yoy_signals(curr: dict[str, Any], prev: dict[str, Any] | None,
                        audit_curr: dict[str, Any] | None = None,
                        audit_prev: dict[str, Any] | None = None) -> list[str]:
    """전년 대비 alerts 자동 detect.

    curr/prev: _compute_metrics 결과.
    audit_curr/audit_prev: audit_opinion scope 결과 ({adt_opinion: ...}).
    """
    alerts: list[str] = []

    ni_curr = curr.get("net_income_krw")
    ni_prev = prev.get("net_income_krw") if prev else None
    op_curr = curr.get("operating_profit_krw")
    rev_curr = curr.get("revenue_krw")
    rev_prev = prev.get("revenue_krw") if prev else None

    # 수익성
    if ni_curr is not None and ni_prev is not None:
        if ni_prev > 0 and ni_curr < 0:
            alerts.append("loss_conversion")
        if ni_prev < 0 and ni_curr > 0:
            alerts.append("turnaround")
        if ni_prev < 0 and ni_curr < 0:
            alerts.append("continued_loss")
    if op_curr is not None and op_curr < 0:
        alerts.append("operating_loss")
    if rev_curr is not None and rev_prev is not None and rev_prev > 0:
        if (rev_prev - rev_curr) / rev_prev > 0.30:
            alerts.append("revenue_decline")

    # 부채/유동성
    debt_curr = curr.get("total_liabilities_krw")
    debt_prev = prev.get("total_liabilities_krw") if prev else None
    if debt_curr is not None and debt_prev is not None and debt_prev > 0:
        if (debt_curr - debt_prev) / debt_prev > 0.30:
            alerts.append("debt_surge")
    icov = curr.get("interest_coverage_ratio")
    if icov is not None and icov < 2:
        alerts.append("interest_coverage_low")

    # 자본잠식 (KOSDAQ 관리종목 / 상장폐지 사유 detect)
    cap_status = curr.get("capital_impairment_status")
    if cap_status == "full":
        alerts.append("capital_impairment_full")  # 자본총계 ≤ 0, KOSDAQ 상장폐지 사유
    elif cap_status == "partial_50plus":
        alerts.append("capital_impairment_50plus")  # 잠식률 50%↑, KOSDAQ 관리종목 사유
    elif cap_status == "partial":
        alerts.append("capital_impairment_partial")  # 잠식률 0~50%, 조기 경고

    # 현금흐름
    cfo_quality = curr.get("cfo_to_op_ratio")
    if cfo_quality is not None and cfo_quality < 0.7:
        alerts.append("cfo_quality_red")
    fcf = curr.get("fcf_krw")
    if fcf is not None and fcf < 0:
        alerts.append("negative_fcf")
    div_to_fcf = curr.get("dividend_to_fcf_pct")
    if div_to_fcf is not None and 0 < div_to_fcf < 20:
        alerts.append("low_dividend_capacity_use")

    # 운전자본
    nwc_change = curr.get("nwc_change_yoy_krw")
    nwc_prev = prev.get("nwc_krw") if prev else None
    if nwc_change is not None and nwc_prev is not None and nwc_prev > 0:
        if nwc_change / nwc_prev > 0.30:
            alerts.append("nwc_surge")
    nwc_eff = curr.get("nwc_to_revenue_pct")
    if nwc_eff is not None and nwc_eff > 25:
        alerts.append("nwc_efficiency_low")

    # 듀퐁 — 레버리지 의존도
    em = curr.get("equity_multiplier")
    if em is not None and em > 2.0:  # 자기자본 비중 50% 미만 = 부채 의존
        alerts.append("roe_driven_by_leverage")
    # ROE decline 분해
    roe_curr = curr.get("roe_pct")
    roe_prev = prev.get("roe_pct") if prev else None
    if roe_curr is not None and roe_prev is not None and roe_curr < roe_prev:
        npm_curr = curr.get("net_profit_margin_pct")
        npm_prev = prev.get("net_profit_margin_pct") if prev else None
        ato_curr = curr.get("asset_turnover_ratio")
        ato_prev = prev.get("asset_turnover_ratio") if prev else None
        margin_drop = (npm_prev or 0) - (npm_curr or 0) if (npm_prev is not None and npm_curr is not None) else 0
        ato_drop = (ato_prev or 0) - (ato_curr or 0) if (ato_prev is not None and ato_curr is not None) else 0
        if margin_drop > 0 and margin_drop * 5 > ato_drop:
            alerts.append("roe_decline_margin_driven")
        elif ato_drop > 0:
            alerts.append("roe_decline_turnover_driven")

    # 회계 risk
    acc = curr.get("accruals_gap_pct")
    acc_rel = curr.get("accruals_gap_reliability")
    if acc_rel in ("negative_op", "thin_op"):
        # 분모가 적자·0 근처 — 비율은 뒤집히거나 뻥튀기된다. red 로 단정하지 않고 금액차를 보게 한다.
        alerts.append("accruals_ratio_unreliable")
    elif acc is not None and abs(acc) > 30:
        alerts.append("accruals_red")
    ar = curr.get("ar_to_revenue_pct")
    ar_prev_pct = prev.get("ar_to_revenue_pct") if prev else None
    if ar is not None and ar_prev_pct is not None and ar_prev_pct > 0:
        if (ar - ar_prev_pct) / ar_prev_pct > 0.30:
            alerts.append("receivables_surge")
    inv = curr.get("inv_to_revenue_pct")
    inv_prev_pct = prev.get("inv_to_revenue_pct") if prev else None
    if inv is not None and inv_prev_pct is not None and inv_prev_pct > 0:
        if (inv - inv_prev_pct) / inv_prev_pct > 0.30:
            alerts.append("inventory_surge")

    # 감사의견
    if audit_curr:
        op = (audit_curr.get("adt_opinion") or "").strip()
        if op and "적정" not in op:
            alerts.append("non_clean_audit_opinion")
        if audit_prev:
            prev_op = (audit_prev.get("adt_opinion") or "").strip()
            if op and prev_op and op != prev_op and "적정" in prev_op and "적정" not in op:
                alerts.append("audit_opinion_change")

    # 배당
    div_curr = curr.get("dividend_paid_krw")
    div_prev = prev.get("dividend_paid_krw") if prev else None
    if div_prev is not None and div_prev > 0 and (div_curr is None or div_curr == 0):
        alerts.append("dividend_halt")

    return sorted(set(alerts))


# ── DART 호출 헬퍼 (try/except + AnalysisStatus mapping) ──

#: 이 요청에서 DART 재무 조회가 **실패**(013 「자료 없음」이 아닌 오류 — 020 한도·800 점검·전송 오류 등)한 횟수.
#: `build_financial_metrics_payload` 가 요청마다 새 상자를 건다(같은 요청의 하위 Task 는 컨텍스트를 복사하므로 같은 상자를
#: 본다). 261018: 종전엔 재무 조회가 전부 실패해도 0건이 「사업보고서 재무 공시 미존재 (정상 …)」·status=no_filing 으로
#: 나갔고 그 응답이 5분 캐시에 들어갔다 — 실패를 「없다」로 내보내지 않으려고 센다. 상자가 없으면(다른 서비스가 부를 때) 무시.
_FETCH_FAILURES: contextvars.ContextVar[dict | None] = contextvars.ContextVar("fm_fetch_failures", default=None)


def _note_fetch_failure() -> None:
    box = _FETCH_FAILURES.get()
    if box is not None:
        box["n"] += 1


async def _safe_fetch_acnt(corp_code: str, year: int, reprt_code: str, fs_div: str) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        data = await client.get_fnltt_singl_acnt(corp_code, str(year), reprt_code, fs_div)
        rows = data.get("list", []) or []
        # fnlttSinglAcnt는 fs_div 파라미터와 무관하게 CFS+OFS 행을 함께 반환한다 (KB금융 실측).
        # 기존 first-match는 DART 행 순서(CFS 먼저)에 의존 — 요청 fs_div로 명시 필터해 순서 의존 제거.
        wanted = [r for r in rows if (r.get("fs_div") or "").upper() == fs_div.upper()]
        if wanted:
            return wanted, None
        return rows, None  # 연결 미작성(단일 재무제표) 회사는 OFS만 존재 → 그대로 사용
    except DartClientError as exc:
        if exc.status == "013":
            return [], "no_filing"
        _note_fetch_failure()
        return [], f"fnlttSinglAcnt({reprt_code}, {fs_div}) 실패: {exc.status} {exc}"


async def _safe_fetch_acnt_all(corp_code: str, year: int, reprt_code: str, fs_div: str) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        data = await client.get_fnltt_singl_acnt_all(corp_code, str(year), reprt_code, fs_div)
        return data.get("list", []) or [], None
    except DartClientError as exc:
        if exc.status == "013":
            return [], "no_filing"
        _note_fetch_failure()
        return [], f"fnlttSinglAcntAll({reprt_code}, {fs_div}) 실패: {exc.status} {exc}"


async def _build_accounts(
    corp_code: str, year: int, fs_div: str, sj_div: list[str] | None,
) -> tuple[dict[str, Any], list[str]]:
    """계정 원행 그대로 — 요약 스칼라가 버리는 수백 행을 원문 순서(`ord`)로 돌려준다.

    요약과 **같은 endpoint**(`fnlttSinglAcntAll`)를 보되 이 경로는 자기 호출을 따로 한다.
    실측 콜 수는 요약 8 대 이 경로 2 — 계정만 필요하면 이쪽이 싸다(요약에 얹어 공짜로 나오는 게 아니다).
    파생지표는 만들지 않는다 — 「표를 더하되 원문을 대체하지 않는다」이고 계정 해석은 읽는 쪽이 한다.
    """
    warnings: list[str] = []
    rows, err = await _safe_fetch_acnt_all(corp_code, year, _REPRT_BUSINESS, fs_div)
    used_rc = _REPRT_BUSINESS
    if not rows and fs_div == "CFS":
        # fnlttSinglAcntAll 은 요청 fs_div 를 그대로 쓴다 — 연결 미작성 회사에 CFS 로 부르면
        # 013(없음)이 되어 계정이 통째로 빈다(요약 경로가 actual_fs 로 부르는 이유와 같다).
        rows, err = await _safe_fetch_acnt_all(corp_code, year, _REPRT_BUSINESS, "OFS")
        if rows:
            warnings.append(f"{year}년 연결(CFS) 미작성 — 별도(OFS) 기준 계정이다.")
    if not rows:
        # 사업보고서 미공시 연도 — 요약 경로와 같은 폴백 사슬을 탄다.
        for rc in ("11014", "11012", "11013"):
            rows, err = await _safe_fetch_acnt_all(corp_code, year, rc, fs_div)
            if rows:
                used_rc = rc
                # 요약 경로(_fetch_year_metrics)는 이걸 세는데 여기만 안 셌다 — 「에러가 아니라
                # 대체로 나타나는 고장」은 세지 않으면 발생률을 영영 모른다.
                note_degradation("report_substituted")
                warnings.append(f"{year}년 사업보고서 미공시 — reprt_code={rc}(분기/반기)로 대체.")
                break
    if not rows:
        return {}, [err if err and err != "no_filing" else f"{year}년 재무제표 계정 미공시"]

    actual_fs = _actual_fs_div(rows) or fs_div
    if fs_div == "CFS" and actual_fs == "OFS":
        warnings.append(f"{year}년 연결(CFS) 미작성 — 별도(OFS) 기준 계정이다.")

    want = {s.upper() for s in (sj_div or [])} or None
    kept = [{k: r.get(k) for k in _ACCOUNT_ROW_FIELDS}
            for r in rows if not want or (r.get("sj_div") or "").upper() in want]
    # 재무제표 순서는 알파벳순이 아니라 공시 순서(BS→IS→CIS→CF→SCE)다. `ord` 는 문자열이고
    # 드물게 비숫자가 와서, 정렬 하나로 tool 호출 전체가 죽지 않도록 안전 파싱한다.
    def _ord(r: dict[str, Any]) -> tuple[int, int]:
        raw = str(r.get("ord") or "").strip()
        return (_SJ_ORDER.get((r.get("sj_div") or "").upper(), 99),
                int(raw) if raw.lstrip("-").isdigit() else 10**6)

    kept.sort(key=_ord)

    cur = statement_currency(rows)
    if cur != "KRW":
        warnings.append(f"기능통화 {cur} — 아래 금액은 **{cur} 원문 그대로이고 환산하지 않았다**. "
                        f"원화로 환산된 요약 지표는 같은 도구의 요약에서 기말환율로 제공한다.")
    if used_rc != _REPRT_BUSINESS:
        warnings.append("분기/반기 보고서의 손익 계정은 누적(YTD)이다 — `thstrm_add_amount` 가 있으면 그 쪽이 누적, "
                        "`thstrm_amount` 는 당기 3개월일 수 있다. 두 열을 함께 읽을 것.")
    return {
        "rows": kept,
        "row_count": len(kept),
        "functional_currency": cur,
        "fs_div": actual_fs,
        "reprt_code": used_rc,
        "sj_div_present": sorted({(r.get("sj_div") or "").upper() for r in kept if r.get("sj_div")},
                                 key=lambda x: _SJ_ORDER.get(x, 99)),
    }, warnings


def _exclude_unconverted_amounts(metrics: dict[str, Any]) -> None:
    """Do not let unconverted foreign amounts masquerade as KRW thresholds."""
    for key, value in list(metrics.items()):
        if key in _AMOUNT_MAP_FIELDS and isinstance(value, dict):
            metrics[key] = {
                name: None if isinstance(amount, (int, float)) and not isinstance(amount, bool) else amount
                for name, amount in value.items()
            }
        elif isinstance(value, dict):
            _exclude_unconverted_amounts(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _exclude_unconverted_amounts(item)
        elif key.endswith("_krw") and isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics[key] = None


async def _normalize_currency(
    metrics: dict[str, Any],
    rows: list[dict[str, Any]],
    period_end: str | None,
    year: int,
) -> list[str]:
    """기능통화가 KRW 가 아니면 금액 필드를 KRW 로 환산하고 기준을 값 옆에 붙인다.

    왜 이름 기반인가 — 이 dict 의 금액 필드는 예외 없이 `_krw` 로 끝나고 비율·배수·건수는 아니다.
    그래서 「_krw 로 끝나는 수치만 환산」이 곧 「금액만 환산」이고, 지표가 늘어도 규칙이 따라온다
    (개별 필드를 나열하면 다음에 추가되는 지표가 조용히 환산에서 빠진다).

    전기(prev_*)도 **같은 환율**로 환산한다. 서로 다른 환율을 쓰면 yoy 에 환율 변동이 섞여
    「기능통화 기준 성장률」이 아니게 된다 — 이미 계산된 `*_yoy_pct` 와도 어긋난다.
    그 대신 그 사실을 basis 로 명시한다.

    환산 실패(환율 조회 불가)면 값을 건드리지 않고 **경고만** 남긴다 — 조용히 틀린 라벨을
    남기느니 「환산 못 했다」를 말한다.
    """
    cur = statement_currency(rows)
    metrics["functional_currency"] = cur
    if cur == "KRW":
        return []

    # 환율 기준일 = 회계기말. period_end('2024-12-31')가 있으면 그날, 없으면 연말 근사.
    fx_date = (period_end or f"{year}-12-31").replace("-", "")[:8] or f"{year}1231"
    rate = await fx_to_krw(cur, fx_date)
    if not rate or rate == 1.0:
        metrics["fx_rate_to_krw"] = None
        if get_strict_as_of():
            _exclude_unconverted_amounts(metrics)
            return [f"기능통화 {cur} — 기준시점의 환율 공개 여부를 확인할 수 없어 원화 금액을 제외했습니다. "
                    "공시 원문과 통화에 영향받지 않는 비율은 유지하며 원화 임계치 평가는 건너뜁니다."]
        return [f"⚠️ 기능통화 {cur} 인데 환율 조회 실패 — 아래 `*_krw` 금액은 **{cur} 단위 그대로**이고 "
                f"원화가 아니다. 원화 기준 비교·시총 대비 계산에 그대로 쓰지 말 것."]

    def _scale(d: dict[str, Any]) -> None:
        for k, v in list(d.items()):
            if k in _AMOUNT_MAP_FIELDS and isinstance(v, dict):
                # 접미어 규칙의 유일한 예외 — 키가 canonical id(OPM_ST 등)라 `_krw` 가 안 붙는데
                # 값은 금액이다. 여기를 빼면 sum(by_canonical_id) 과 total_debt_krw 가 환율배 어긋난다.
                d[k] = {ck: (round(cv * rate) if isinstance(cv, (int, float))
                             and not isinstance(cv, bool) else cv) for ck, cv in v.items()}
            elif isinstance(v, dict):        # standalone 등 중첩 dict 도 같은 규칙으로
                _scale(v)
            elif k.endswith("_krw") and isinstance(v, (int, float)) and not isinstance(v, bool):
                d[k] = round(v * rate)

    _scale(metrics)
    metrics["fx_rate_to_krw"] = round(rate, 2)
    metrics["fx_source"] = fx_source(cur, fx_date)      # 261019: "ecos"(한국은행 매매기준율) / "yahoo"(대체값 — 저장 안 함)
    metrics["fx_basis"] = (
        f"{fx_date} 기말환율 — 이 행의 금액(전기 포함)은 전부 이 환율. "
        f"다년 표에서는 **행마다 그 해 기말환율**이라 금액 추이에 환율 변동이 섞이고, "
        f"`*_yoy_pct` 는 환산 전 {cur} 기준이라 환율이 빠진 성장률이다 — 두 열의 기준이 다르다.")
    out = [f"기능통화 {cur} — 금액을 {fx_date} 기말환율 {rate:,.1f}원/{cur} 로 KRW 환산했다. "
           f"순이익은 원칙상 평균환율이라 수% 오차가 있고, 전기도 같은 환율을 써서 "
           f"yoy 는 환율 변동이 빠진 {cur} 기준 성장률이다."]
    if metrics["fx_source"] == SRC_YAHOO:
        out.append(f"⚠️ {fx_date} 환율: {FALLBACK_WARNING}")
    return out


def _actual_fs_div(rows: list[dict[str, Any]]) -> str | None:
    """rows에서 실제 사용된 fs_div(다수결). CFS 요청인데 OFS가 오면 연결 미작성 폴백 감지용.

    _safe_fetch_acnt는 CFS가 있으면 CFS만, 없으면(연결 미작성) 전체(OFS)를 반환한다.
    그래서 반환 rows의 fs_div를 보면 실제로 무슨 기준이 쓰였는지 알 수 있다.
    """
    counts: dict[str, int] = {}
    for r in rows:
        fv = (r.get("fs_div") or "").upper()
        if fv:
            counts[fv] = counts.get(fv, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: counts[k])


# reprt_code → 정기보고서명 키워드 (list.json report_nm 매칭용)
_REPRT_NAME_KW = {"11011": "사업보고서", "11012": "반기보고서", "11013": "분기보고서", "11014": "분기보고서"}

#: 「사업보고서 (2025.12)」 — 괄호 안은 **결산기 말**이지 사업연도 번호가 아니다.
_ANNUAL_PERIOD = re.compile(r"\((\d{4})\.(\d{2})\)")


async def latest_annual_report_before(corp_code: str, as_of: str) -> dict[str, Any] | None:
    """`as_of`(YYYYMMDD) 시점에 **이미 제출돼 있던** 가장 최근 사업보고서.

    주총에서 볼 수 있는 확정 재무제표는 그때까지 제출된 것뿐이다. 「주총 N년이면 FY(N-2)」는
    3월 정기주총 일정에서 나온 어림이라 — 그때는 FY(N-1) 사업보고서가 아직 안 나왔다 — 연중에
    열리는 임시주총에서는 한 해 과하게 보수적이다. 8월 임시주총은 그해 3월에 제출된 FY(N-1)
    사업보고서를 이미 볼 수 있다.

    `fiscal_year` 는 **12월 결산일 때만** 채운다. 결산기가 다른 법인은 사업보고서가 3~6개월마다
    나오고(실측 SK리츠 2025.03·06·09·12 / 신한알파리츠 2024.03·09) 라벨의 연도가 사업연도
    번호와 같다는 보장이 없다. 모르는 것을 추측하느니 비워서 호출측이 물러나게 한다.
    """
    if not corp_code or len(as_of) != 8 or not as_of.isdigit():
        return None
    try:
        data = await get_dart_client().search_filings(
            corp_code=corp_code, bgn_de=f"{int(as_of[:4]) - 2}0101", end_de=as_of, pblntf_ty="A",
        )
    except Exception:
        return None                                   # 조회 실패는 「없음」이 아니다 — 호출측이 물러난다
    rows = [
        r for r in (data.get("list") or [])
        if "사업보고서" in (r.get("report_nm") or "") and (r.get("rcept_dt") or "").strip() <= as_of
    ]
    if not rows:
        return None
    rows.sort(key=lambda r: (r.get("rcept_dt") or ""), reverse=True)
    top = rows[0]
    report_nm = (top.get("report_nm") or "").strip()
    period = _ANNUAL_PERIOD.search(report_nm)
    fiscal_year = int(period.group(1)) if period and period.group(2) == "12" else None
    return {
        "rcept_no": top.get("rcept_no", ""),
        "rcept_dt": (top.get("rcept_dt") or "").strip(),
        "report_nm": report_nm,
        "fiscal_year": fiscal_year,
    }


#: DART 정정 보고서는 보고서명 앞에 `[기재정정]`·`[첨부정정]`·`[첨부추가]` 등 대괄호 머리가 붙는다.
_CORRECTION_NM = re.compile(r"\[[^\]]*(정정|추가|보완)[^\]]*\]")


def _is_correction_report(report_nm: str | None) -> bool:
    """보고서명이 정정본인지. 숫자가 정정 전인지 후인지 모르면 인용을 못 한다(260828 U 지적 A-3)."""
    return bool(_CORRECTION_NM.search(report_nm or ""))


async def _periodic_filing_ref(corp_code: str, year: int, reprt_code: str | None = None) -> dict[str, str] | None:
    """해당 연도 정기보고서(정기공시)의 rcept 메타 — evidence 원문 링크용.

    fnlttSinglAcnt(재무 집계 API)는 rcept_no를 돌려주지 않아 evidence 링크가 비어 있었다.
    list.json(정기공시 A)을 1회 조회해 실제 제출된 보고서의 rcept_no/접수일/보고서명을 붙인다.
    연간(사업보고서)은 결산 다음 해 제출이라 end를 year+1 9월까지 잡는다.
    실패 시 None(graceful) — evidence는 기존처럼 합성 마커만 유지.
    """
    try:
        data = await get_dart_client().search_filings(
            corp_code=corp_code, bgn_de=f"{year}0101", end_de=f"{year + 1}0930", pblntf_ty="A",
        )
    except Exception:
        return None
    items = [i for i in (data.get("list") or []) if i.get("rcept_no")]
    if not items:
        return None
    kw = _REPRT_NAME_KW.get(reprt_code or "")
    narrowed = [i for i in items if kw and kw in (i.get("report_nm") or "")]
    cand = narrowed or items
    cand.sort(key=lambda x: x.get("rcept_dt", ""), reverse=True)
    top = cand[0]
    top_nm = (top.get("report_nm") or "").strip()
    top_dt = (top.get("rcept_dt") or "").strip()
    is_corr = _is_correction_report(top_nm)
    # 「같은 날 함께 정정된 정기보고서」는 이미 받아온 목록(pblntf_ty=A) 안에서만 센다 —
    # 추가 조회 없이 공짜다. 정기공시 밖(수시공시) 정정은 여기 안 잡힌다.
    same_day = 0
    if is_corr and top_dt:
        same_day = sum(
            1 for i in items
            if (i.get("rcept_dt") or "").strip() == top_dt
            and _is_correction_report(i.get("report_nm") or "")
            and i.get("rcept_no") != top.get("rcept_no")
        )
    return {
        "rcept_no": top.get("rcept_no", ""),
        "rcept_dt": top_dt,
        "report_nm": top_nm,
        "is_correction": is_corr,
        "correction_dt": top_dt if is_corr else "",
        "same_day_corrections": same_day,
    }


async def _safe_fetch_audit(corp_code: str, year: int) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        data = await client.get_audit_opinion(corp_code, str(year), "11011")
        rows = data.get("list", []) or []
        return rows, None
    except DartClientError as exc:
        if exc.status == "013":
            return [], "no_filing"
        _note_fetch_failure()
        return [], f"accnutAdtorNmNdAdtOpinion 실패: {exc.status} {exc}"


def _is_reit(name: str | None) -> bool:
    """이름 기반 REIT 판정 — KSIC(68)는 부동산 개발사(SK디앤디·자이에스앤디 등 정상 배당성향)까지
    잡아 과억제하므로 이름의 '리츠'를 신뢰 신호로 쓴다(260717 404사 스캔·QA 검증: 신한글로벌
    액티브리츠(ksic 64201)처럼 KSIC 오분류 REIT도 이름으로 잡힘)."""
    return "리츠" in (name or "")


async def _accrual_payout_pct(corp_code: str, year: int, *, is_reit: bool = False) -> float | None:
    """배당성향 = DART 사업보고서 '현금배당성향(%)' (해당 사업연도 귀속·연결 기준).

    dividend 툴과 동일한 alotMatter 다년 로직을 재사용해 SSOT를 일원화한다. CF '배당금지급'
    기반 계산(연도·주체 불일치)을 대체(260716 검증). 무배당/미기재/미확정 연도는 None.
    과거 확정연도 alotMatter는 client에 영구 캐시되어 dividend 툴과 중복 호출 시 0콜.

    is_reit=True면 None을 반환한다 — REIT는 회계 순이익 대비 배당성향이 무의미하다
    (배당가능이익≥90% 분배 구조라 100~2000%·음수가 정상. 260717 404사 스캔: 미래에셋글로벌
    리츠 421%·신한알파 231%·이지스밸류 −415%). 성향 대신 DPS·시가배당률로 봐야 한다.
    """
    if is_reit:
        return None
    # 지연 import (모듈 로드 순서 무관 + 순환 회피)
    from open_proxy_mcp.services.dividend import (
        _alot_multiyear_summaries,
        _annual_summary,
    )
    try:
        latest, _err = await _annual_summary(corp_code, year)
    except Exception:
        return None
    if not latest:
        return None
    row = _alot_multiyear_summaries(latest).get(year)
    if not row:
        return None
    return row.get("payout_ratio_dart")


# ── scope dispatchers ──

class _FetchMemo:
    """한 요청 안에서 같은 (연도·보고서·기준) 조회를 한 번만 한다.

    다년 조회는 연도마다 「당기 + 전기」를 부르므로 인접 연도가 겹친다 — `years=10` 이면
    40 콜 중 18 콜(45%)이 같은 조회다. DART 는 분당 910 을 넘으면 **그 키가 2~3시간 막히므로**
    헛콜은 그 자체로 가용성 문제다.

    결과 캐시만으로는 부족하다 — `_build_yearly` 가 연도들을 `gather` 로 **동시에** 던지므로
    둘 다 캐시 미스로 출발한다. 그래서 결과가 아니라 **진행 중인 Task** 를 공유한다.
    반환 리스트는 얕은 복사로 끊는다(같은 리스트를 여러 호출자가 들고 고치면 서로를 오염시킨다 —
    이 레포는 screener 스캔 캐시에서 이미 그 사고를 겪었다).
    """

    def __init__(self) -> None:
        self._inflight: dict[tuple, Any] = {}

    def _share(self, key: tuple, factory):
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(factory())
            self._inflight[key] = task
        return task

    async def acnt(self, corp_code: str, year: int, rc: str, fs_div: str):
        rows, err = await self._share(
            ("acnt", corp_code, year, rc, fs_div),
            lambda: _safe_fetch_acnt(corp_code, year, rc, fs_div))
        return list(rows), err

    async def acnt_all(self, corp_code: str, year: int, rc: str, fs_div: str):
        rows, err = await self._share(
            ("all", corp_code, year, rc, fs_div),
            lambda: _safe_fetch_acnt_all(corp_code, year, rc, fs_div))
        return list(rows), err


class _NoMemo:
    """단건 조회 경로 — 공유할 상대가 없으므로 그대로 부른다(분기 하나로 통일하려고 둔 얇은 껍데기)."""

    @staticmethod
    async def acnt(corp_code: str, year: int, rc: str, fs_div: str):
        return await _safe_fetch_acnt(corp_code, year, rc, fs_div)

    @staticmethod
    async def acnt_all(corp_code: str, year: int, rc: str, fs_div: str):
        return await _safe_fetch_acnt_all(corp_code, year, rc, fs_div)


_NO_MEMO = _NoMemo()


def _fetch(memo: "_FetchMemo | None"):
    return memo or _NO_MEMO


# ── 보고서 사이 단위 가리기 (261021 fin-eq) ──────────────────────────────────────────────────────────────
#
# 회사가 단위를 잘못 적어 사업보고서가 **통째로** 10ⁿ 배인 경우(065150 FY2023 1,000배 — 자산 56.7조 · 032680 FY2022 100만 배)에는 그 보고서
# 안의 검사(자산 = 부채 + 자본)가 다 맞는다. 같은 날짜의 같은 숫자를 낸 **다른 보고서**와 대야 드러난다 — 사업보고서 N 의 재무상태표 전기 칸은
# 사업보고서 N−1 의 당기 칸이다(`scale_guard.check_prior_period_mismatch`). 어느 쪽이 틀렸는지는 이어 붙인 보고서들의 다수결로 가린다
# (`scale_guard.reconcile_report_scales` — 배수 도구 firm·연간 수집과 같은 함수, 분기 수집은 아직 옛 규칙 `check_prior_period_mismatch` — DATA-SC4). 틀린 보고서는 금액 칸만 10ⁿ 으로 나눠 쓰고(주당 금액은 그대로)
# 근거를 경고·`data_quality.scale_fix` 에 적는다. **이미 받은 응답끼리만** 대므로 DART 콜은 늘지 않는다(다년 조회는 같은 요청의 `_FetchMemo` 가 겹치는
# 조회를 이미 하나로 합친다). 두 보고서뿐(요약·단년)이라 다수결이 안 서면 값은 원문 그대로 두고 경고만 한다.
_SCALE_LINES = (("자본총계", "EQ"), ("자산총계", "AS"))
_SCALE_LINES_BY_TAG = tuple((tag, nm) for nm, tag in _SCALE_LINES)


def _date8(raw: Any) -> str:
    """'2023.12.31 현재' → '20231231'(숫자 8자리). 못 읽으면 ''."""
    return "".join(ch for ch in str(raw or "") if ch.isdigit())[:8]


def _annual_scale_obs(year: int, rows: list[dict[str, Any]]) -> list[tuple]:
    """사업보고서 하나(주요계정 rows)가 낸 같은 날짜의 같은 숫자 관측 — 당기 말과 전기 말의 자본총계·자산총계.

    `reconcile_report_scales` 의 관측 꼴 (보고서 라벨, 열쇠, 값, 순서). 열쇠 = 그 숫자가 가리키는 날짜(rows 의 `thstrm_dt`·`frmtrm_dt` — 결산월을 바꾼 회사에서
    사업연도 표기만으로 짝을 짓지 않으려고)에 통화와 줄(자본/자산)을 붙인 것. 날짜를 못 읽으면 사업연도 표기(당기=year, 전기=year−1)로 짝짓는다."""
    ccy = statement_currency(rows)
    out: list[tuple] = []
    for nm, tag in _SCALE_LINES:
        row = next((r for r in rows if (r.get("sj_div") or "").strip() == "BS"
                    and _strip(r.get("account_nm")).replace(" ", "") == nm), None)
        if row is None:
            continue
        for field, dt_field, lag in (("thstrm_amount", "thstrm_dt", 0), ("frmtrm_amount", "frmtrm_dt", 1)):
            v = normalize_amount(row.get(field))
            if not v:
                continue
            d8 = _date8(row.get(dt_field)) or (_date8(row.get("stlm_dt")) if lag == 0 else "")
            key = ("D", d8, ccy, tag) if d8 else ("Y", year - lag, ccy, tag)
            out.append((f"FY{year}", key, float(v), year - lag))
    return out


async def _common_mktcap(stock_code: str | None) -> float | None:
    """상장사의 KRX 보통주 시총(원) — 운영 DB `price_latest`(종목마다 가장 늦은 거래일 한 줄)에서 읽는다. DART 0콜, DB 1회.
    단위 가리기(`reconcile_report_scales`)의 시총 신호용이다(DATA-SC1). 최근 3주 안 행만 쓴다(상장폐지·장기 거래정지의 낡은 시총으로 PBR 을 재지 않게).
    상장사가 아니거나 DB 미설정·장애·행 없음이면 None — 호출자는 시총 신호 없이 종전대로 간다."""
    if not stock_code:
        return None
    try:
        from open_proxy_mcp.db import pg_rows
        cutoff = (today_kst() - timedelta(days=21)).strftime("%Y%m%d")
        rows = await asyncio.to_thread(pg_rows, "SELECT mktcap FROM price_latest WHERE ticker=%s AND price_dd>=%s", (stock_code, cutoff))
    except Exception:  # noqa: BLE001 — 시총은 보조 신호다: 읽기가 깨져도 종전 응답은 나간다
        return None
    try:
        v = float(rows[0][0]) if rows and rows[0][0] is not None else None
    except (TypeError, ValueError):
        return None
    return v if v and v > 0 else None


def _reconcile_annual_scales(reports: dict[int, list[dict[str, Any]]], mktcap: float | None = None) -> dict[str, Any]:
    """사업보고서들(연도 → 주요계정 rows)끼리 대 단위가 어긋난 보고서를 가린다 → `reconcile_report_scales` 결과에 연도별 정보를 얹은 dict.

    {"status": clean|fixed|undecided|conflict, "divisors": {연도: 나눌 배수}, "method", "pairs": [대조 근거 — 연도로 풀어 둠], "names": {연도: 이름},
     "rcept": {연도: 접수번호}}. 보고서가 둘 미만이면 clean(대 볼 상대가 없다)."""
    usable = {y: r for y, r in reports.items() if r}
    out: dict[str, Any] = {"status": "clean", "divisors": {}, "method": None, "pairs": [],
                           "names": {y: f"{y} 사업보고서" for y in usable},
                           "rcept": {y: next((str(r.get("rcept_no")) for r in rows if r.get("rcept_no")), None)
                                     for y, rows in usable.items()}}
    if len(usable) < 2:
        return out
    obs = [o for y, rows in usable.items() for o in _annual_scale_obs(y, rows)]
    # 보고서가 둘뿐이면(요약·years=1) 다수결이 서지 않는다 — 시총을 제3 신호로 쓴다(DATA-SC1). 셋 이상이면 다수결이 가리므로 시총을 더해
    # 낡은 시총이 다수결을 `undecided` 로 막는 일이 없게 한다(두 신호가 엇갈리면 고치지 않는 규칙).
    res = reconcile_report_scales(obs, mktcap=mktcap if len(usable) <= 2 else None)
    to_year = lambda lab: int(str(lab)[2:])  # noqa: E731  ('FY2023' → 2023)
    out["status"] = res["status"]
    out["method"] = res.get("method")
    out["divisors"] = {to_year(k): v for k, v in (res.get("divisors") or {}).items()}
    out["pairs"] = [{"a": to_year(p["a"]), "b": to_year(p["b"]), "a_value": p["a_value"], "b_value": p["b_value"], "power": p["power"],
                     "line": dict(_SCALE_LINES_BY_TAG).get(p["key"][-1], p["key"][-1])}
                    for p in res.get("pairs") or [] if p["power"]]
    return out


async def _annual_scale_plan(corp_code: str, years: list[int], fs_div: str, memo: "_FetchMemo",
                             mktcap: "float | asyncio.Future | None" = None) -> dict[str, Any]:
    """다년 조회(`_build_yearly`·yoy)가 읽을 사업보고서들의 단위 가리기. `years` 는 요청한 사업연도들 — 각 연도의 당기 보고서와 가장 이른 연도의 전기
    보고서까지 대 본다. `memo` 가 이미 같은 조회를 하나로 합치므로 이 단계가 새로 부르는 DART 는 없다(어차피 `_fetch_year_metrics` 가 부르는 조회다):
    각 연도의 사업보고서는 `_fetch_acnt_with_fallback` 의 첫 조회이고, 가장 이른 연도의 전기 보고서는 그 연도가 사업보고서를 냈을 때만 읽는다."""
    ys = sorted(set(years))
    got = await asyncio.gather(*[_fetch(memo).acnt(corp_code, y, _REPRT_BUSINESS, fs_div) for y in ys])
    reports = {y: rows for y, (rows, _err) in zip(ys, got) if rows}
    first = ys[0]
    if first in reports:
        rows, _err = await _fetch(memo).acnt(corp_code, first - 1, _REPRT_BUSINESS, fs_div)
        if rows:
            reports[first - 1] = rows
    if mktcap is not None and not isinstance(mktcap, (int, float)):
        mktcap = await mktcap
    return _reconcile_annual_scales(reports, mktcap=mktcap)


def _scale_note(plan: dict[str, Any] | None, year: int) -> tuple[list[dict], dict | None, list[str]]:
    """연도 `year` 의 지표(당기 보고서 `year` + 전기 보고서 `year−1`)에 걸린 단위 가리기 → (scale_fix 목록, scale_cross_report 또는 None, 경고).
    바로잡은 보고서가 없고 어긋남도 없으면 모두 비어 응답은 종전 그대로다."""
    if not plan or plan.get("status") == "clean":
        return [], None, []
    used = {year, year - 1}
    names, rcept = plan["names"], plan["rcept"]
    pairs = [p for p in plan["pairs"] if p["a"] in used or p["b"] in used]
    fixes: list[dict] = []
    warns: list[str] = []
    if plan["status"] == "fixed":
        for y, d in sorted(plan["divisors"].items()):
            if y not in used:
                continue
            ev = [{"line": p["line"], "compared_with": names.get(p["b"] if p["a"] == y else p["a"]),
                   "value_this_report": p["a_value"] if p["a"] == y else p["b_value"],
                   "value_other_report": p["b_value"] if p["a"] == y else p["a_value"]}
                  for p in sorted(plan["pairs"], key=lambda q: q["line"] != "자본총계") if y in (p["a"], p["b"])]
            fixes.append({"report": names.get(y), "rcept_no": rcept.get(y), "divisor": d, "method": plan["method"], "evidence": ev})
            how = f"÷{d:,.0f}" if d >= 1 else f"×{round(1 / d):,}"
            first = ev[0]
            # 짝마다 날짜가 다르다(앞 해 보고서와는 전기 말, 뒤 해 보고서와는 당기 말) — 이 보고서 값도 짝마다 함께 적는다.
            cmp_ = " · ".join(f"이 보고서 {e['value_this_report']:,.0f} 대 {e['compared_with']} {e['value_other_report']:,.0f}"
                              for e in ev if e["line"] == first["line"])
            warns.append(f"🔧 DART 원문 단위 오류를 바로잡았다 — {names.get(y)}(접수 {rcept.get(y) or '?'})의 {first['line']} 값이 "
                         f"같은 날짜를 적은 다른 보고서 칸과 10의 거듭제곱 배로 어긋나({cmp_}) 그 보고서의 금액을 {how} 해 썼다(주당 금액은 그대로, 가린 근거: {plan['method']}). "
                         "회사가 단위를 잘못 적은 보고서다 — 이 행의 금액·ROE 분모·전년비는 바로잡은 값이다.")
        return fixes, None, warns
    if not pairs:
        return [], None, []
    pairs = sorted(pairs, key=lambda q: q["line"] != "자본총계")
    cross = {"status": plan["status"], "method": plan.get("method"),
             "pairs": [{"line": p["line"], "report": names.get(p["a"]), "value": p["a_value"], "other_report": names.get(p["b"]),
                        "other_value": p["b_value"], "power": p["power"]} for p in pairs]}
    cmp_ = " · ".join(f"{names.get(p['a'])} {p['a_value']:,.0f} vs {names.get(p['b'])} {p['b_value']:,.0f}"
                      for p in pairs if p["line"] == pairs[0]["line"])
    warns.append(f"🚨 보고서 사이 단위 어긋남 — 같은 날짜의 {pairs[0]['line']} 값이 보고서마다 10의 거듭제곱 배로 다르다({cmp_}). 어느 보고서가 맞는지 "
                 "가리지 못해(이미 받은 보고서가 둘뿐이거나 서로 엇갈림) 값은 원문 그대로다 — 금액·ROE·전년비를 쓰기 전에 원문 확인 요망. "
                 "연간 추이 조회(`years` 3 이상)는 앞뒤 해 보고서까지 대어 가린다.")
    return [], cross, warns


def _dedupe_scale_warnings(warnings: list[str]) -> list[str]:
    """같은 보고서의 단위 경고는 그 보고서를 쓰는 두 해(당기·전기)가 같은 문장을 낸다 — 그 경고(🔧·🚨)만 한 번으로, 다른 경고는 종전 그대로."""
    seen: set[str] = set()
    out: list[str] = []
    for w in warnings:
        if w.startswith(("🔧", "🚨")):
            if w in seen:
                continue
            seen.add(w)
        out.append(w)
    return out


def _apply_scale(rows: list[dict[str, Any]], plan: dict[str, Any] | None, year: int) -> list[dict[str, Any]]:
    """`year` 보고서 rows 의 금액 칸을 plan 의 나눌 배수로 바로잡은 사본(없으면 rows 그대로). 주당 금액 줄은 그대로 둔다."""
    d = (plan or {}).get("divisors", {}).get(year) if (plan or {}).get("status") == "fixed" else None
    return rescale_rows(rows, d) if d and rows else rows


async def _fetch_acnt_with_fallback(
    corp_code: str,
    year: int,
    fs_div: str,
    memo: "_FetchMemo | None" = None,
) -> tuple[list[dict[str, Any]], str, str | None]:
    """사업보고서(11011) → 3분기(11014) → 반기(11012) → 1분기(11013) 순서로 fallback.

    가장 최근에 가용한 분기 보고서를 사용 (사업보고서 미공시 시).

    return (rows, used_reprt_code, warning_or_None)
    """
    fallback_order = ("11011", "11014", "11012", "11013")
    last_err = None
    for rc in fallback_order:
        rows, err = await _fetch(memo).acnt(corp_code, year, rc, fs_div)
        if rows:
            return rows, rc, None
        if err == "no_filing":
            # DART 013 = 그 보고서가 없다 — 실패가 아니라 다음 보고서로 내려간다. 261018: 종전엔 여기서
            # 「{year}년 reprt_code=11013 no_filing」(마지막으로 본 보고서 코드 + 엔진 상태어)을 사유로 남겨,
            # 네 보고서가 모두 없을 때 사용자 경고에 그 문장이 그대로 실렸다(합병사 2026년 실측).
            continue
        if err:
            last_err = err          # 진짜 조회 실패는 사유를 남긴다(아래에서 「없음」보다 앞선다)
    return [], "11011", last_err or f"{year}년 정기보고서(사업·3분기·반기·1분기) 재무제표 미공시"


#: 저장된 결산기 칸 근거 글(`period_cells`)에서 사업연도 칸의 결산기 범위를 읽는다 — 「사업연도 2025 = 202506~202512 결산기 2개 합」.
_STORED_SPAN = re.compile(r"(\d{6})~(\d{6}) 결산기 (\d+)개 합")
#: 분기 칸의 「다음 결산기 누적(202606)」·「누적(202603+202606)」 — 그 칸의 누적에 든 결산기들. 마지막이 누적이 끝나는 결산기다.
_STORED_CUM_TERMS = re.compile(r"누적\(([0-9+]+)\)")


def _twelve_month_unavailable(reason: str) -> dict[str, Any]:
    return {"rule": "stored_period_cells", "status": "unavailable", "reason": reason}


async def _period_cells_12m(stock_code: str | None, period_end: str, months: int,
                            equity_end: float | None = None) -> dict[str, Any]:
    """반기·분기 결산 회사의 **12개월 결산기 합** 순이익과 ROE — 수집기가 저장한 「결산기 12개월」 칸(`period_cells`)에서 읽는다.

    사업보고서 한 권의 당기는 한 결산기(3·6개월)라 `net_income_krw`·`roe_pct` 가 12개월의 절반(분기 결산이면 4분의 1)이다. 12개월 값은
    실시간 firm(`price_multiple_data`)·주간 곡선·업종 비교가 이미 쓰는 같은 칸이다 — 이 도구가 따로 12개월 규칙을 두지 않는다.
      · 결산기 말이 12월 = 그 사업연도 연간 칸(`dart_finstat_q` quarter=4, 마지막 결산기에서 거꾸로 12개월 지배순이익 합).
      · 그 밖(3·6·9월 말) = 직전 연간 + 그 달 누적 − 전년 같은 달 누적 (읽는 쪽 모두의 같은 TTM 식). 칸이 끝나는 결산기가 보고서 결산기와 같아야 쓴다.
      · ROE = 12개월 순이익 ÷ 평균 지배자본(결산기 말과 12개월 전 같은 달 말). 12개월 전 자본이 없으면 결산기 말 자본 단독.
    반환 `status` = "ok" | "unavailable"(까닭 `reason`). DB 미설정·장애(None)와 칸 없음([])을 가른다. 12개월 전체가 안 만들어지면 일부 조각으로 때우지 않는다.
    """
    if not stock_code:
        return _twelve_month_unavailable("종목코드가 없어 저장된 결산기 칸을 찾지 못함")
    try:
        y, mo = int(period_end[:4]), int(period_end[5:7])
    except (TypeError, ValueError):
        return _twelve_month_unavailable("사업보고서 결산기 말을 읽지 못함")
    if not 1 <= mo <= 12:
        return _twelve_month_unavailable("사업보고서 결산기 말을 읽지 못함")
    q = (mo + 2) // 3               # 칸은 달력 분기로 묶는다 — 그 분기 말 이하 가장 늦은 결산기가 칸의 끝이다(11월 결산기 → 4분기 칸)
    end6 = f"{y}{mo:02d}"
    try:
        from open_proxy_mcp.db import pg_rows
        from open_proxy_mcp.services import period_cells as _pc
        rows = await asyncio.to_thread(
            pg_rows,
            "SELECT fy, quarter, ni_cum_restated, eq_restated, restate_why FROM dart_finstat_q "
            "WHERE ticker=%s AND fy IN (%s, %s) AND quarter IN (4, %s) AND restate_why LIKE %s",
            (stock_code, y, y - 1, q, _pc.MARK_LIKE))
    except Exception as exc:  # noqa: BLE001 — 12개월 값은 덧붙임이다: 읽기가 깨져도 종전 응답은 나간다
        return _twelve_month_unavailable(f"DB 조회 실패({type(exc).__name__})")
    if rows is None:
        return _twelve_month_unavailable("DB 를 읽지 못함(미설정 또는 장애) — 잠시 뒤 다시 조회하면 채워질 수 있음")
    cells = {(int(r[0]), int(r[1])): r for r in rows}
    cur = cells.get((y, q))
    if cur is None:
        return _twelve_month_unavailable(f"저장된 결산기 12개월 칸이 없음({y}년 {q}분기 칸) — 수집 대상 밖이거나 아직 수집 전")
    if cur[2] is None:
        return _twelve_month_unavailable(f"저장된 {y}년 {q}분기 칸의 12개월 순이익 합이 비어 있음(결산기 가운데 순이익을 못 읽은 것이 있음)")
    if q == 4:
        m = _STORED_SPAN.search(str(cur[4]))
        if not m or m.group(2) != end6:
            return _twelve_month_unavailable(f"저장된 {y}년 연간 칸의 마지막 결산기가 이 사업보고서 결산기({end6})와 다름")
        ni12 = float(cur[2])
        terms = f"{m.group(1)}~{m.group(2)} 결산기 {m.group(3)}개 합"
    else:
        annual, prev_q = cells.get((y - 1, 4)), cells.get((y - 1, q))
        m = _STORED_CUM_TERMS.search(str(cur[4]))
        if not m or m.group(1).split("+")[-1] != end6:
            return _twelve_month_unavailable(f"저장된 {y}년 {q}분기 칸의 누적이 이 사업보고서 결산기({end6})에서 끝나지 않음")
        if annual is None or annual[2] is None or prev_q is None or prev_q[2] is None:
            return _twelve_month_unavailable(f"직전 연간 칸이나 전년 같은 달 칸이 없어 {end6} 말 12개월 합을 못 맞춤")
        ni12 = float(annual[2]) + float(cur[2]) - float(prev_q[2])
        terms = f"{y - 1}년 연간 + {y}년 {end6[4:]}월 누적 − {y - 1}년 같은 달 누적"
    prev_eq_row = cells.get((y - 1, q))
    eq_end = equity_end if equity_end is not None else (float(cur[3]) if cur[3] is not None else None)
    eq_prev = float(prev_eq_row[3]) if prev_eq_row is not None and prev_eq_row[3] is not None else None
    roe = None
    if eq_end is not None:
        avg = (eq_end + eq_prev) / 2 if eq_prev is not None else eq_end
        if avg > 0:
            roe = round(ni12 / avg * 100, 2)
    return {
        "rule": "stored_period_cells", "status": "ok",
        "window": f"{_month_back(end6, 11)}~{end6[:4]}-{end6[4:]}", "terms": terms, "period_months": months,
        "net_income_krw": round(ni12), "equity_end_krw": round(eq_end) if eq_end is not None else None,
        "equity_prev_krw": round(eq_prev) if eq_prev is not None else None, "roe_pct": roe,
    }


def _month_back(yyyymm: str, n: int) -> str:
    """YYYYMM 에서 n 달 앞 (YYYY-MM)."""
    t = int(yyyymm[:4]) * 12 + int(yyyymm[4:6]) - 1 - n
    return f"{t // 12}-{t % 12 + 1:02d}"


async def _fetch_year_metrics(
    corp_code: str,
    year: int,
    fs_div: str,
    *,
    include_prev: bool = True,
    allow_quarterly_fallback: bool = True,
    induty_code: str | None = None,
    is_reit: bool = False,
    stock_code: str | None = None,
    memo: "_FetchMemo | None" = None,
    reprt_code: str | None = None,
    scale_plan: "dict[str, Any] | asyncio.Future | None" = None,
    mktcap: "float | asyncio.Future | None" = None,
) -> tuple[dict[str, Any], list[str], int]:
    """단일 사업연도 metrics. 당기+전기 fnlttSinglAcnt를 모두 호출.

    `memo` 를 주면 같은 요청 안의 다른 연도와 조회를 공유한다(다년 조회의 인접 연도 중복 제거).
    `scale_plan` 은 다년 조회가 앞뒤 해 보고서까지 대어 가린 단위 오류 보고서 목록(`_annual_scale_plan`) 또는 그것을 낼 Task — 없으면 이 연도의 당기·전기 두 보고서끼리만 대 본다
    (둘뿐이면 다수결이 안 서므로 어긋남을 경고만 한다). Task 는 이 연도가 자기 조회를 마친 **뒤에** 기다린다 — 다년 조회의 연도별 병렬이 가리기 때문에 늦춰지지 않는다.

    `reprt_code` 를 주면 그 보고서(11011·11012·11013·11014)만 본다 — 사업보고서→3분기→반기→1분기 순서의
    대체(fallback)를 하지 않는다. `yoy` 가 올해 값(예: 3분기 누적)과 견줄 전년 값을 **같은 보고서**로 다시
    셀 때 쓴다(DATA-Q6). 그 보고서가 없으면 빈 결과(미공시)로 돌려준다.

    Phase 1 v2 최적화 (iteration 11):
    - fnlttSinglIndx 호출 제거 (DART 산출 지표는 자체 계산값 우선이라 사실상 미사용).
    - 당기/전기 × acnt/acntAll = 4 호출을 asyncio.gather로 병렬화.
    - 결과: 8 sequential → (1 sequential fallback + 3 parallel) — 평균 응답 시간 ~3-4배 단축.

    allow_quarterly_fallback=True (default): 사업보고서 미공시 시 분기/반기 보고서로 fallback.
    return (metrics, warnings, evidence_count)
    """
    warnings: list[str] = []
    # 1단계: 당기 fnlttSinglAcnt — fallback 발생 가능성 있어 sequential 유지.
    if reprt_code is not None:
        rows_curr, err_curr = await _fetch(memo).acnt(corp_code, year, reprt_code, fs_div)
        if not rows_curr:
            if err_curr and err_curr != "no_filing":
                return {}, [err_curr], 0
            return {}, [f"{year}년 {_REPRT_LABEL.get(reprt_code, reprt_code)} 미공시"], 0
        used_rc = reprt_code   # 대체 경고·계수는 당기 쪽이 이미 냈다 — 같은 대체를 두 번 세지 않는다
    elif allow_quarterly_fallback:
        rows_curr, used_rc, fb_err = await _fetch_acnt_with_fallback(corp_code, year, fs_div, memo=memo)
        if not rows_curr:
            return {}, [fb_err or f"{year}년 데이터 미공시"], 0
        if used_rc != _REPRT_BUSINESS:
            note_degradation("report_substituted")
            warnings.append(f"{year}년 사업보고서 미공시 — reprt_code={used_rc}로 대체 (반기/분기)")
    else:
        rows_curr, err_curr = await _fetch(memo).acnt(corp_code, year, _REPRT_BUSINESS, fs_div)
        if err_curr == "no_filing":
            return {}, [f"{year}년 사업보고서 미공시"], 0
        if err_curr:
            warnings.append(err_curr)
        used_rc = _REPRT_BUSINESS

    # 연결(CFS) 요청인데 실제로 별도(OFS)가 왔으면 = 연결 미작성 폴백. 조용히 넘기지 않고 경고.
    actual_fs = _actual_fs_div(rows_curr) or fs_div
    if fs_div == "CFS" and actual_fs == "OFS":
        warnings.append(f"{year}년 연결재무제표(CFS) 미작성 — 별도(OFS) 기준으로 산출됨. 연결 기준 수치 아님.")

    # 2단계: 나머지 3 호출 병렬 (전기 acnt + 당기 acntAll + 전기 acntAll).
    # used_rc는 당기에서 결정된 reprt_code 그대로 사용 (전기도 같은 code로 비교).
    tasks: list[Any] = []
    task_keys: list[str] = []
    # used_rc 전파 — 당기가 분기/반기 fallback이면 CF·상세(acnt_all)와 전기 비교도
    # 같은 reprt_code로 맞춘다. 기존엔 acnt_all이 11011 고정이라 fallback 연도에서
    # 사업보고서 미공시 → CFO/CapEx/FCF 전체 결측 (SK하이닉스 2026 실측).
    # 전체 재무제표는 **실제 기준(actual_fs)** 으로 부른다. 주요계정(fnlttSinglAcnt)은 CFS 가 없으면
    # OFS 행을 알아서 돌려주지만 fnlttSinglAcntAll 은 요청 fs_div 그대로라, 연결 미작성 회사에
    # CFS 로 부르면 013(없음) — CF·매출 폴백·세부 IS 가 통째로 비었다(리파인 2022~2025 실측, 260906).
    if include_prev:
        tasks.append(_fetch(memo).acnt(corp_code, year - 1, used_rc, fs_div))
        task_keys.append("prev_acnt")
    tasks.append(_fetch(memo).acnt_all(corp_code, year, used_rc, actual_fs))
    task_keys.append("curr_acnt_all")
    if include_prev:
        tasks.append(_fetch(memo).acnt_all(corp_code, year - 1, used_rc, actual_fs))
        task_keys.append("prev_acnt_all")

    parallel_results = await asyncio.gather(*tasks, return_exceptions=False)
    by_key: dict[str, tuple[list[dict[str, Any]], str | None]] = dict(zip(task_keys, parallel_results))

    rows_prev, err_prev = by_key.get("prev_acnt", ([], None))
    if err_prev and err_prev != "no_filing":
        warnings.append(err_prev)

    rows_detail, err_detail = by_key.get("curr_acnt_all", ([], None))
    if err_detail and err_detail != "no_filing":
        warnings.append(err_detail)

    rows_detail_prev, err_dp = by_key.get("prev_acnt_all", ([], None))
    if err_dp and err_dp != "no_filing":
        warnings.append(err_dp)

    # ── 보고서 사이 단위 가리기 (261021 fin-eq) — 사업보고서끼리만(분기 보고서의 전기 칸은 사업연도 말이라 전년 동기 보고서와 짝이 아니다) ──
    scale_fix: list[dict] = []
    scale_cross: dict | None = None
    if used_rc == _REPRT_BUSINESS:
        if scale_plan is not None and not isinstance(scale_plan, dict):
            scale_plan = await scale_plan
        if scale_plan is None and mktcap is not None and not isinstance(mktcap, (int, float)):
            mktcap = await mktcap       # 요약(단년): 보고서가 둘뿐이라 시총이 제3 신호다 — DB 한 번(DART 0콜)
        plan = scale_plan if scale_plan is not None else _reconcile_annual_scales({year: rows_curr, year - 1: rows_prev}, mktcap=mktcap)
        scale_fix, scale_cross, _scale_ws = _scale_note(plan, year)
        warnings.extend(_scale_ws)
        if scale_fix:
            rows_curr, rows_detail = _apply_scale(rows_curr, plan, year), _apply_scale(rows_detail, plan, year)
            rows_prev, rows_detail_prev = _apply_scale(rows_prev, plan, year - 1), _apply_scale(rows_detail_prev, plan, year - 1)

    # ── PRIMARY = 누적(period-to-date) basis ──
    # 분기/반기 보고서면 손익을 누적(thstrm_add)으로 읽어 CF(누적)와 기간을 맞춘다.
    # 사업보고서·1분기는 누적=당기라 동일. BS(잔액)는 기간 무관.
    bs_is = _build_account_map(rows_curr, cumulative_is=True, induty_code=induty_code) if rows_curr else {}
    bs_is_prev = _build_account_map(rows_prev, cumulative_is=True, induty_code=induty_code) if rows_prev else None
    detail = _build_account_map_all(rows_detail, cumulative_is=True, induty_code=induty_code) if rows_detail else None
    detail_prev = _build_account_map_all(rows_detail_prev, cumulative_is=True, induty_code=induty_code) if rows_detail_prev else None

    # ── 전기 보고서의 실제 재무제표 기준이 당기와 다르면 섞어 견주지 않는다 (DATA-Q5 의 연간 변형) ──
    # 전기 주요계정은 요청 fs_div 로 불러 연결이 없으면 별도 행이 그대로 오고(`_safe_fetch_acnt`), 전기 전체 재무제표는 당기 actual_fs 로 따로 부른다.
    # 연결 신규 작성(당기 CFS · 전기 OFS)·연결 중단(당기 OFS · 전기 CFS)이면 전년비·평균자본이 다른 기준끼리 견줘졌다.
    # 사업보고서면 **당기 보고서의 전기 칸**(당기와 같은 기준으로 다시 적은 값)을 전기로 쓴다. 그 칸을 못 쓰는 분기·반기 보고서는 전기를 버린다.
    prev_fs_div: str | None = None
    prev_dropped = False
    _fs_prev = _actual_fs_div(rows_prev) if rows_prev else None
    if _fs_prev and _fs_prev != actual_fs:
        prev_fs_div = _fs_prev
        if used_rc == _REPRT_BUSINESS and rows_curr:
            bs_is_prev = _build_account_map(rows_curr, period="frmtrm", induty_code=induty_code)
            detail_prev = _build_account_map_all(rows_detail, period="frmtrm", induty_code=induty_code) if rows_detail else None
            warnings.append(f"{year - 1}년 보고서 재무제표 기준({_fs_prev})이 {year}년({actual_fs})과 달라, 전기 값은 {year}년 사업보고서의 전기 칸"
                            f"(당기와 같은 기준으로 다시 적은 값)을 썼다 — prev_fs_div={_fs_prev}.")
        else:
            bs_is_prev, detail_prev, prev_dropped = None, None, True
            warnings.append(f"{year - 1}년 보고서 재무제표 기준({_fs_prev})이 {year}년({actual_fs})과 달라 전기 값을 쓰지 않았다 — "
                            f"전년비와 평균자본·평균자산 기준 ROE·ROA 를 내지 않음(prev_fs_div={_fs_prev}). 총자산회전율·자기자본 배수·회전일수는 "
                            f"평균 대신 기말 값으로 계산돼 나간다(전기가 없을 때와 같은 한계).")

    if not bs_is:
        return {}, warnings + [f"{year}년 BS/IS 핵심 데이터 파싱 실패"], 0

    # ── TTM(최근 4분기) 회전일수 분모 — 분기/반기면 직전 FY 1건 추가 fetch ──
    # TTM = 직전 FY + 당기 YTD(누적) − 전년 YTD(누적). AR/재고 평균은 이미 (당기말+전년동기말)/2라
    # TTM 정합. 단일분기 연환산 왜곡(AR 3배인데 DSO 하락) 제거. 실패 시 기간일치로 fallback.
    ttm_revenue = ttm_cogs = None
    if used_rc != _REPRT_BUSINESS:
        fy_rows_all, _fy_err = await _fetch(memo).acnt_all(corp_code, year - 1, _REPRT_BUSINESS, actual_fs)
        fy_map = _build_account_map_all(fy_rows_all, induty_code=induty_code) if fy_rows_all else {}
        # 당기 YTD 매출도 폴백(전체 재무제표)을 거친 값으로 — 주요계정에 매출 행이 없는 회사는 bs_is 가 None.
        ytd_rev_c = (bs_is or {}).get("revenue")
        if ytd_rev_c is None and detail:
            ytd_rev_c = detail.get("revenue")
        ytd_rev_p = (bs_is_prev or {}).get("revenue")
        if ytd_rev_p is None and detail_prev:
            ytd_rev_p = detail_prev.get("revenue")
        if None not in (fy_map.get("revenue"), ytd_rev_c, ytd_rev_p):
            ttm_revenue = fy_map["revenue"] + ytd_rev_c - ytd_rev_p
        ytd_cogs_c, ytd_cogs_p = (detail or {}).get("cogs"), (detail_prev or {}).get("cogs")
        if None not in (fy_map.get("cogs"), ytd_cogs_c, ytd_cogs_p):
            ttm_cogs = fy_map["cogs"] + ytd_cogs_c - ytd_cogs_p
        if ttm_revenue is None:
            warnings.append(f"{year}년 TTM 회전일수 분모 계산 실패(직전 FY 미조회) — 분기 기간일치로 대체.")

    # 사업보고서 한 결산기의 실제 개월 — 반기·분기 결산 회사(리츠)는 6·3개월이다(못 읽으면 12 = 종전).
    rep_curr = _report_period(rows_curr) if used_rc == _REPRT_BUSINESS else None
    pm_cum = _period_months(used_rc, cumulative=True, annual_months=rep_curr["months"] if rep_curr else None)
    short_annual = used_rc == _REPRT_BUSINESS and pm_cum != 12
    metrics = _compute_metrics(
        bs_is=bs_is,
        bs_is_prev=bs_is_prev,
        detail=detail,
        detail_prev=detail_prev,
        indx_map=None,  # fnlttSinglIndx 제거 — 자체 계산값 우선이라 미사용
        period_months=pm_cum,
        ttm_revenue=ttm_revenue,
        ttm_cogs=ttm_cogs,
        induty_code=induty_code,
    )
    metrics["year"] = year
    if prev_fs_div:
        metrics["prev_fs_div"] = prev_fs_div     # 새 키 — 전기 보고서의 재무제표 기준이 당기와 달랐을 때만
        if prev_dropped:   # 전기를 버렸으면 평균 대신 기말로 떨어진 비율을 내지 않는다
            for _k in ("roe_pct", "roa_pct", "roe_dupont_pct"):
                metrics[_k] = None
    if metrics.get("net_income_basis_note"):
        warnings.append(f"{year}년 {metrics['net_income_basis_note']}")
    if scale_fix:
        metrics.setdefault("data_quality", {})["scale_fix"] = scale_fix
    elif scale_cross:
        metrics.setdefault("data_quality", {})["scale_cross_report"] = scale_cross
    # 매출을 표준 「매출액」이 아닌 계정에서 읽었으면 그 사실을 사람용 경고로도 남긴다.
    if metrics.get("revenue_krw") is not None and metrics.get("revenue_standard") is False:
        warnings.append(
            f"{year}년 매출 = {metrics.get('revenue_basis')} 기준(원문 계정 「{metrics.get('revenue_account_nm')}」, "
            f"업종 특성상 매출액 계정 없음) — 영업이익률·회전일수를 제조업 매출 기준과 같이 읽지 말 것."
        )
    elif metrics.get("revenue_source") == "fnlttSinglAcntAll":
        warnings.append(
            f"{year}년 매출은 주요계정에 없어 전체 재무제표 「{metrics.get('revenue_account_nm')}」 행에서 읽음."
        )
    period_end = next((str(r.get("thstrm_dt") or r.get("stlm_dt") or "").replace(".", "-")
                       for r in rows_curr if r.get("thstrm_dt") or r.get("stlm_dt")), None)
    metrics["period_end"] = period_end
    metrics["fiscal_year"] = fiscal_year_from_end(period_end) or year
    metrics["fs_div"] = actual_fs  # 요청값이 아니라 실제 사용된 기준 (CFS 미작성 시 OFS)
    metrics["reprt_code"] = used_rc
    metrics["period_basis"] = (f"period_{pm_cum}m" if short_annual else "annual") if used_rc == _REPRT_BUSINESS else f"cumulative_{pm_cum}m"
    if short_annual and rep_curr:
        metrics["period_span"] = {"start": rep_curr["start"], "end": rep_curr["end"], "months": pm_cum}
    # 배당성향은 귀속 기준(DART 사업보고서 '현금배당성향')으로 주입 — 연간보고서에서만 의미 있음.
    # 분기/누적 기간엔 해당 연도 배당이 미확정이라 None(정직). CF dividend_paid_krw는 배당/FCF 전용.
    if used_rc == _REPRT_BUSINESS:
        metrics["payout_ratio_pct"] = await _accrual_payout_pct(corp_code, year, is_reit=is_reit)
        if is_reit:
            metrics["payout_ratio_note"] = "REIT — 순이익 기준 배당성향 미표시(배당가능이익 분배). DPS·시가배당률 참고"
    else:
        metrics["payout_ratio_pct"] = None
    if short_annual:
        _span_txt = f"({rep_curr['start']}~{rep_curr['end']})" if rep_curr else ""
        warnings.append(
            f"{year}년 사업보고서의 이 결산기는 {pm_cum}개월{_span_txt}이다 — 손익·ROE·ROA·자산회전율은 "
            f"연간(12개월)이 아니라 이 {pm_cum}개월 값이고 연환산이 아니다(회전일수는 기간 보정됨)."
        )
    elif pm_cum < 12:
        warnings.append(
            f"{year}년 {pm_cum}개월 누적 기준 — 회전일수는 기간 보정됨, ROE/ROA/자산회전율은 "
            f"연환산 아닌 {pm_cum}개월값(연간 비교 시 주의)."
        )
    # 총차입금 진단(신뢰도·모순·리스·전환) top-level 승격 — 조용히 틀린 값보다 표면화(패널 합의).
    _bd = metrics.get("borrowing_detail") or {}
    for _f in _bd.get("data_quality_flags") or []:
        warnings.append(f"{year}년 [총차입금] {_f}")
    if metrics.get("is_financial_company"):
        warnings.append(f"{year}년 [총차입금] 금융업(예수부채 등 영업조달) 판정 — 차입금의존도 미산출(n/a).")
    if metrics.get("lease_liabilities_krw"):
        warnings.append(f"{year}년 총차입금에 리스부채는 빠져 있습니다(IFRS16 별도) — 리스 포함 총차입도 함께 표시했습니다.")
    if metrics.get("convertible_debt_krw"):
        warnings.append(f"{year}년 [총차입금] 전환사채·BW·EB 포함 — 잠재 지분희석은 dilutive_issuance 참조.")

    # ── 당기(standalone) basis — 반기/3분기는 누적−직전 누적으로 차분, CF는 직전 보고서 필요 ──
    metrics["standalone"] = None
    if used_rc in ("11012", "11014"):
        prior_rc = {"11012": "11013", "11014": "11012"}[used_rc]
        prior_detail_rows, prior_err = await _safe_fetch_acnt_all(corp_code, year, prior_rc, actual_fs)
        if prior_detail_rows:
            bs_is_std = _build_account_map(rows_curr, induty_code=induty_code)  # thstrm: IS=당기 3개월, BS=잔액
            bs_is_prev_std = _build_account_map(rows_prev, induty_code=induty_code) if rows_prev else None
            detail_std = _build_account_map_all(rows_detail, induty_code=induty_code) if rows_detail else None
            prior_detail = _build_account_map_all(prior_detail_rows, induty_code=induty_code)
            # 전년 같은 분기도 3개월 값으로 — 누적 맵(detail_prev)을 그대로 쓰면 3개월 당기를 6·9개월 누적 전기와
            # 비교하게 된다(079160 2021 반기: 지배 순이익 전년비 +64.59% 는 −845억 대 −2,387억(상반기 누적), 같은 3개월끼리는 +40.97%).
            # 같은 행(rows_detail_prev)을 cumulative_is=False 로 다시 읽을 뿐이라 DART 콜은 늘지 않는다. BS(잔액)는 두 맵이 같다.
            detail_prev_std = _build_account_map_all(rows_detail_prev, induty_code=induty_code) if rows_detail_prev else None
            if detail_std:
                # CF는 누적이므로 당기 = 당기누적 − 직전누적 (cfo/capex/이자지급 등 전 CF 키)
                for k in _CF_ACCOUNT_PATTERNS:
                    cur_v, pri_v = detail_std.get(k), prior_detail.get(k)
                    if cur_v is not None and pri_v is not None:
                        detail_std[k] = cur_v - pri_v
            standalone = _compute_metrics(
                bs_is=bs_is_std,
                bs_is_prev=bs_is_prev_std,
                detail=detail_std,
                detail_prev=detail_prev_std,  # 3개월 전년 값(IS) — 잔액(BS·NWC)은 누적 맵과 같다
                indx_map=None,
                period_months=3,
                ttm_revenue=ttm_revenue,  # 회전일수는 basis 무관 — 두 벌 모두 동일 TTM 사용
                ttm_cogs=ttm_cogs,
                induty_code=induty_code,
            )
            standalone["period_basis"] = "quarter_3m"
            metrics["standalone"] = standalone
        else:
            warnings.append(f"{year}년 당기(standalone) 분해 실패 — 직전 보고서({prior_rc}) 미조회, 누적만 제공.")
    elif used_rc == "11013":
        # 1분기는 누적=당기 → primary가 곧 당기 (별도 분해 불필요)
        metrics["period_basis"] = "quarter_3m"

    # ── 기준 항상 명시 (사람용 한 줄) — 손익이 당기/누적/연간 중 무엇인지 + 회전일수 기준 ──
    _tb = {"ttm": "TTM(최근 4분기)", "annual": "연간",
           "period_matched": f"기간보정({pm_cum}개월)" if short_annual else "기간보정(분기)"}.get(
        metrics.get("turnover_basis"), metrics.get("turnover_basis"))
    if short_annual:
        # 반기·분기 결산 회사(리츠) — 사업보고서가 결산기마다 나와 한 권의 당기가 12개월이 아니다. 「연간 12개월」이라고 적지 않는다.
        metrics["basis_note"] = (
            f"손익=사업보고서 한 결산기 {pm_cum}개월" + (f"({rep_curr['start']}~{rep_curr['end']})" if rep_curr else "")
            + f"(연간 12개월 아님·연환산 아님), 회전일수={_tb} 기준. 12개월 값은 net_income_12m_krw·roe_12m_pct 참조.")
    elif used_rc == _REPRT_BUSINESS:
        metrics["basis_note"] = "손익=연간(사업보고서, 12개월), 회전일수=연간 기준."
    elif used_rc == "11013":
        metrics["basis_note"] = f"손익=당기 1분기(3개월), 회전일수={_tb} 기준."
    else:
        metrics["basis_note"] = (
            f"손익=누적 {pm_cum}개월(YTD) 기준, 회전일수={_tb} 기준"
            f"{' · 당기 분기(3개월)는 standalone 참조' if metrics.get('standalone') else ''}."
        )
    if metrics.get("standalone"):
        metrics["standalone"]["basis_note"] = f"손익=당기 분기(3개월, standalone), 회전일수={_tb} 기준."

    # 자산 = 부채 + 자본 검산. 다른 단건 tool(price_multiple_data·financial_notes)은 이미 하는데
    # 이 tool 만 세 값을 손에 쥐고도 안 했다 — 새 정책이 아니라 **정합화**다.
    # 값은 고치지 않는다(DART 원문이 그렇다는 사실 자체가 신호다) — 경고만 붙인다.
    # 선행 조건: 세 값이 같은 기준(actual_fs)에서 왔을 때만 본다. 폴백으로 기준이 섞이면 위양성.
    _bal = check_balance_identity(metrics.get("total_assets_krw"),
                                  metrics.get("total_liabilities_krw"),
                                  metrics.get("total_equity_krw"))
    if _bal.get("triggered"):
        metrics["balance_identity_diff_pct"] = round(_bal["diff_pct"], 4)
        warnings.append(
            f"{year}년 자산총계 ≠ 부채총계 + 자본총계 (차이 {_bal['diff_pct']:.2f}%) — "
            f"공시 원문이 그렇다. 값은 그대로 두었으니 인용 전 원문을 확인할 것.")

    # ── 반기·분기 결산 회사 — 12개월 결산기 합 순이익·ROE (새 키; 옛 net_income_krw·roe_pct 는 한 결산기 값 그대로) ──
    if short_annual:
        if statement_currency(rows_detail or rows_curr) != "KRW":
            metrics["twelve_month_basis"] = _twelve_month_unavailable("원화가 아닌 보고서 — 저장된 결산기 칸은 원화 기준")
            tw = metrics["twelve_month_basis"]
        else:
            _eq_end_rows = (detail or {}).get("controlling_equity")
            if _eq_end_rows is None:
                _eq_end_rows = bs_is.get("controlling_equity")
            if _eq_end_rows is None:
                _eq_end_rows = bs_is.get("total_equity")
            tw = await _period_cells_12m(stock_code, rep_curr["end"] if rep_curr else "", pm_cum, _eq_end_rows)
            metrics["twelve_month_basis"] = tw
        metrics["net_income_12m_krw"] = tw.get("net_income_krw") if tw.get("status") == "ok" else None
        metrics["roe_12m_pct"] = tw.get("roe_pct") if tw.get("status") == "ok" else None
        if tw.get("status") == "ok":
            warnings.append(
                f"{year}년 {pm_cum}개월 결산기 — 12개월 값은 net_income_12m_krw({tw['net_income_krw']:,}원)·roe_12m_pct"
                f"({tw['roe_pct'] if tw['roe_pct'] is not None else 'N/M'}%)다: {tw['window']} 결산기 합(저장된 결산기 칸 — 시장 밸류 도구와 같은 값). "
                f"net_income_krw·roe_pct 는 이 {pm_cum}개월 한 결산기 값 그대로다.")
        else:
            warnings.append(
                f"{year}년 {pm_cum}개월 결산기라 12개월 순이익·ROE(net_income_12m_krw·roe_12m_pct)를 비웠다 — {tw['reason']}. "
                f"net_income_krw·roe_pct 는 {pm_cum}개월 한 결산기 값이라 연간과 비교하지 말 것.")

    # ── 전기 보고서가 같은 기간의 1년 전이 아니면 전년비(*_yoy_pct)가 아니다 ──
    # 반기 결산 리츠의 사업보고서는 6개월마다 나온다 — 올해 2026.01~06 의 「전기」(year-1 사업보고서)는 2025.07~12, 곧 직전 결산기이지 1년 전이 아니다.
    if include_prev and rows_prev and used_rc == _REPRT_BUSINESS:
        rep_prev = _report_period(rows_prev)
        if rep_curr and rep_prev:
            _gap = _month_gap(rep_curr["end"], rep_prev["end"])
            if rep_prev["months"] != pm_cum or _gap != 12:
                metrics["yoy_comparable"] = False
                metrics["prior_period_span"] = {"start": rep_prev["start"], "end": rep_prev["end"], "months": rep_prev["months"]}
                metrics["yoy_basis_note"] = (
                    f"전기 보고서 기간 {rep_prev['start']}~{rep_prev['end']}({rep_prev['months']}개월)은 이 결산기"
                    f"({rep_curr['start']}~{rep_curr['end']}, {pm_cum}개월)의 1년 전이 아니다 — *_yoy_pct 는 전년비가 아니라 직전 기간 대비다.")
                warnings.append(f"{year}년 " + metrics["yoy_basis_note"])

    # 통화 정규화는 **모든 금액 주입이 끝난 뒤** 마지막에 한 번 — 중간에 두면 그 뒤에 붙는
    # standalone·배당 금액이 조용히 환산에서 빠진다(그게 원래 결함의 모양이었다).
    warnings.extend(await _normalize_currency(metrics, rows_detail or rows_curr, period_end, year))

    return metrics, warnings, 1


def _audit_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """accnutAdtorNmNdAdtOpinion row → 표준화 dict."""
    bsns_label = _strip(row.get("bsns_year"))  # "제56기\n(당기)"
    period_tag = ""
    if "(당기)" in bsns_label:
        period_tag = "current"
    elif "(전기)" in bsns_label:
        period_tag = "prior"
    elif "(전전기)" in bsns_label:
        period_tag = "prior_prior"
    return {
        "bsns_year_raw": bsns_label,
        "period_tag": period_tag,
        "stlm_dt": _strip(row.get("stlm_dt")),
        "adtor": _strip(row.get("adtor")),
        "adt_opinion": _strip(row.get("adt_opinion")),
        "adt_reprt_spcmnt_matter": _strip(row.get("adt_reprt_spcmnt_matter")),
        "emphs_matter": _strip(row.get("emphs_matter")),
        "core_adt_matter": _strip(row.get("core_adt_matter")),
        "rcept_no": _strip(row.get("rcept_no")),
    }


async def _build_audit_opinion_data(
    corp_code: str,
    end_year: int,
    years_back: int = 3,
) -> tuple[dict[str, Any], list[str], list[EvidenceRef]]:
    """감사의견 N년 추이 (end_year 기준 최근 사업보고서 1건이 3년치 반환)."""

    warnings: list[str] = []
    rows, err = await _safe_fetch_audit(corp_code, end_year)
    if err == "no_filing":
        return (
            {"opinions": [], "summary": {"latest_opinion": None, "all_clean": None, "history_years": 0}},
            [f"{end_year}년 감사의견 미공시"],
            [],
        )
    if err:
        warnings.append(err)
        return (
            {"opinions": [], "summary": {"latest_opinion": None, "all_clean": None, "history_years": 0}},
            warnings,
            [],
        )

    # rows는 (당기/전기/전전기) × (CFS+OFS) → 6개. 중복 제거: bsns_year + period_tag 우선, CFS 우선.
    parsed = [_audit_row_to_dict(r) for r in rows]
    # 같은 stlm_dt에서 중복 — 첫 번째만 유지 (DART 응답 순서가 CFS 우선).
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for p in parsed:
        key = f"{p['stlm_dt']}|{p['adt_opinion']}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)

    # 최신 → 과거 순 정렬
    deduped.sort(key=lambda x: x.get("stlm_dt", ""), reverse=True)

    latest = deduped[0] if deduped else None
    all_clean = all("적정" in p.get("adt_opinion", "") for p in deduped) if deduped else None

    evidence: list[EvidenceRef] = []
    if latest and latest.get("rcept_no"):
        evidence.append(EvidenceRef(
            evidence_id=f"ev_audit_{corp_code}_{end_year}",
            source_type=SourceType.DART_API,
            rcept_no=latest["rcept_no"],
            section="회계감사인의 감사의견 (사업보고서)",
            note=f"{latest.get('adt_opinion', '-')} / 감사인 {latest.get('adtor', '-')}",
        ))

    return (
        {
            "opinions": deduped,
            "summary": {
                "latest_opinion": latest.get("adt_opinion") if latest else None,
                "latest_auditor": latest.get("adtor") if latest else None,
                "latest_emphs_matter": latest.get("emphs_matter") if latest else None,
                "latest_kam": latest.get("core_adt_matter") if latest else None,
                "all_clean": all_clean,
                # **행 수가 아니라 사업연도 수다.** 같은 결산일에 서로 다른 의견이 여러 행으로
                # 오는 회사가 있어(셀리버리 2022-12-31 = 의견거절/적정/해당사항없음 3행) 행을 세면
                # 「3개 사업연도를 추적했다」고 말하게 된다 — 1개 연도인데.
                "history_years": len({p.get("stlm_dt") for p in deduped if p.get("stlm_dt")}),
            },
        },
        warnings,
        evidence,
    )


async def _build_yearly(corp_code: str, end_year: int, years: int, fs_div: str, induty_code: str | None = None, is_reit: bool = False,
                        stock_code: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    year_list = list(range(end_year - years + 1, end_year + 1))
    # 연도마다 「당기+전기」를 부르므로 인접 연도가 겹친다 — memo 하나를 나눠 주면 그 겹침이 사라진다.
    memo = _FetchMemo()
    # 261021(fin-eq): 앞뒤 해 사업보고서끼리 대 단위가 틀린 보고서를 가린다(DART 콜 0 — memo 가 같은 조회를 합친다). 연도별 조회를 막지 않도록 Task 로 두고
    # 각 연도가 자기 조회를 마친 뒤에 받는다.
    # years=1 이면 보고서가 당기·전기 둘뿐이라 다수결이 서지 않는다 — 시총을 제3 신호로 쓴다(DATA-SC1). 상장사만, DB 1회.
    mk_task = asyncio.ensure_future(_common_mktcap(stock_code)) if len(year_list) == 1 and stock_code else None
    plan = asyncio.ensure_future(_annual_scale_plan(corp_code, year_list, fs_div, memo, mktcap=mk_task))
    tasks = [_fetch_year_metrics(corp_code, y, fs_div, include_prev=True, induty_code=induty_code,
                                 is_reit=is_reit, stock_code=stock_code, memo=memo, scale_plan=plan) for y in year_list]
    results = await asyncio.gather(*tasks)
    out: list[dict[str, Any]] = []
    warnings: list[str] = []
    for metrics, ws, _ev in results:
        warnings.extend(ws)
        if metrics:
            out.append(metrics)
    return out, _dedupe_scale_warnings(warnings)


_QUARTERLY_IS_KEYS = ("revenue", "operating_profit", "net_income")
#: 세전이익은 설명용 칸이다 — Q4 차분·통화 환산은 따라가지만 분기 합 점검·QoQ 대상은 아니다(위 3키만).
_QUARTERLY_PRETAX_KEY = "income_before_tax"
#: 분기 순이익(총계)이 영업이익의 이 배수를 넘으면 영업외손익·법인세 설명 줄을 단다(DATA-NI4 — 영업이익 > 0 인 분기만).
NI_OVER_OPERATING_RATIO = 1.3

#: `quarterly`·`qoq` 행의 `net_income_krw` 기준 — 비지배지분을 포함한 총계(summary·yearly·yoy 의 `net_income_krw` 는 지배).
NET_INCOME_BASIS_TOTAL = "total_incl_nci"
NET_INCOME_BASIS_CONTROLLING = "controlling"   # 연간 net_income_krw 가 지배 순이익일 때 (NI2 연간 변형 — _compute_metrics 가 쓴다)


def _qchg_pct(curr: int | None, prev: int | None) -> float | None:
    """분기 증감률(%). 전기가 0 이하(적자·결측)면 % 비교가 무의미 — None."""
    if curr is None or prev is None or prev <= 0:
        return None
    return round((curr - prev) / prev * 100, 2)


def _pp_diff(curr: float | None, prev: float | None) -> float | None:
    """비율(마진 등)의 변화는 증감률(%)이 아니라 %포인트 차이로 본다 (음수 전기에도 유효)."""
    if curr is None or prev is None:
        return None
    return round(curr - prev, 2)


async def _build_quarterly(corp_code: str, end_year: int, fs_div: str,
                           num_quarters: int = 12, fiscal_month: int | None = None,
                           induty_code: str | None = None,
                           failed_out: set[tuple[int, str]] | None = None,
                           stock_code: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """4Q × 3년 = 12분기 standalone 손익. fnlttSinglAcnt + reprt_code 4개 × 3년 = 12 호출.

    `failed_out` 을 주면 조회가 **실패**한(013 「자료 없음」이 아닌 020 한도·800 점검·전송 오류) (사업연도, 보고서코드)를
    거기에 담는다 — `_quarterly_status` 가 그 분기를 「미제출」이 아니라 「조회 실패(미확인)」로 적게 한다(DATA-API1).

    DART 필드 실측 (SK하이닉스 2025, 2026-06-12):
    - Q1/Q2/Q3 보고서의 thstrm_amount = 당기 3개월(standalone), thstrm_add_amount = 누적
    - 사업보고서(11011)의 thstrm_amount = 연간 누적 → Q4 행에 그대로 쓰면
      QoQ 비교·alert가 전부 왜곡 (연간치 vs 분기치)
    → Q4 손익은 연간 − 3분기 누적(thstrm_add)으로 차분 (dividend 누적차분과 동일 패턴).
      BS 항목(자산·부채·자본)은 시점값이라 차분하지 않는다.
    전 행에 QoQ/YoY 증감률 기본 동봉 — 호출자가 재계산할 필요 없게.
    """

    warnings: list[str] = []
    out: list[dict[str, Any]] = []
    # DART bsns_year와 fiscal_year는 결산월이 3·6월인 회사에서 어긋난다.
    # FY-3까지 수집해야 FY-2 Q1부터 FY Q4까지 12개 fiscal quarter를 복원할 수 있다.
    years = list(range(end_year - 3, end_year + 1))
    quarter_labels = {"11013": "Q1", "11012": "Q2", "11014": "Q3", "11011": "Q4"}
    tasks = []
    keys = []
    for y in years:
        for rc, label in quarter_labels.items():
            tasks.append(_safe_fetch_acnt(corp_code, y, rc, fs_div))
            keys.append((y, rc, label))
    results = await asyncio.gather(*tasks)

    # 요청한 DART bsns_year가 사업연도와 같다는 보장이 없다(3·6월 결산).
    # 이후 모든 grouping은 요청연도가 아니라 실제 period_end에서 계산한 FY/Q를 쓴다.
    cum9_by_year: dict[int, dict[str, int | None]] = {}  # Q3 보고서의 9개월 누적 (Q4 차분용)
    q_standalone_by_year: dict[int, dict[str, dict[str, int | None]]] = {}
    fs_seen: set[str] = set()  # 실제 사용된 fs_div 추적 (CFS/OFS 폴백·혼재 감지)
    # 보고 통화는 **보고서마다** 잡는다. 예전엔 첫 보고서(FY-3 Q1)의 통화 하나를 12분기 전부에 썼다 —
    # 글로벌에스엠(900070)은 2023 CNY → 2024~ USD 라 USD 금액을 CNY 환율로 환산해 약 6.8배 과소였다.
    # 통화별 분기 누적(Q4 차분 재료)도 그 보고서의 통화를 같이 기억한다.
    cum9_cur: dict[int, str] = {}
    qs_cur: dict[tuple[int, str], str] = {}
    last_period_end = ""
    meta: list[tuple[int, str, int, int, str]] = []  # out 과 같은 순서: (bsns_year, reprt_code, fiscal_quarter, fiscal_year, actual_fs)
    for (year, rc, label), (rows, err) in zip(keys, results):
        if err == "no_filing":
            continue
        if err:
            warnings.append(err)
            if failed_out is not None:
                failed_out.add((year, rc))
            continue
        if not rows:
            continue
        row_cur = statement_currency(rows)
        _pe = next((str(r.get("thstrm_dt") or "") for r in rows if r.get("thstrm_dt")), "")
        _pe = "".join(ch for ch in _pe if ch.isdigit())[:8]
        if _pe > last_period_end:
            last_period_end = _pe
        actual = _actual_fs_div(rows)
        if actual:
            fs_seen.add(actual)
        bs_is = _build_account_map(rows, induty_code=induty_code)
        if not bs_is:
            continue
        period_end_raw = next((str(r.get("thstrm_dt") or r.get("stlm_dt") or "")
                           for r in rows if r.get("thstrm_dt") or r.get("stlm_dt")), None)
        period_end = normalize_period_end(period_end_raw)
        fiscal_year = fiscal_year_from_end(period_end, fiscal_month) or year
        fiscal_quarter = fiscal_quarter_from_end(period_end, fiscal_month)
        if fiscal_quarter is None:
            # 레거시/fixture 응답에 기간 종료일이 없을 때만 API 요청연도·코드로 fallback.
            fiscal_quarter = int(label[1:])
            warnings.append(f"{year}-{label}: period_end 없음 — 요청연도 기준으로 임시 분류")
        fiscal_label = f"Q{fiscal_quarter}"
        if fiscal_quarter == 3:
            cum9_by_year[fiscal_year] = _build_account_map(rows, period="thstrm_add", induty_code=induty_code)
            cum9_cur[fiscal_year] = row_cur
        if fiscal_quarter != 4:
            q_standalone_by_year.setdefault(fiscal_year, {})[fiscal_label] = bs_is
            qs_cur[(fiscal_year, fiscal_label)] = row_cur
        out.append({
            "_cur": row_cur,   # 이 보고서의 통화 — 반환 전에 functional_currency 로 바꿔 단다
            # year/quarter는 호환 필드지만 이제 실제 사업연도·분기와 동일하게 유지한다.
            "year": fiscal_year,
            "fiscal_year": fiscal_year,
            "quarter": fiscal_label,
            "fiscal_quarter": fiscal_label,
            "fiscal_year_end_month": fiscal_month or 12,
            "period_end": period_end,
            "reprt_code": rc,
            "revenue_krw": bs_is.get("revenue"),
            "revenue_account_nm": bs_is.get("revenue_account_nm"),
            "revenue_standard": bs_is.get("revenue_standard"),
            "revenue_basis": bs_is.get("revenue_basis"),
            "operating_profit_krw": bs_is.get("operating_profit"),
            "net_income_krw": bs_is.get("net_income"),
            # 법인세차감전 순이익(주요계정, 3개월) — 순이익 > 영업이익 분기의 영업외손익·법인세 설명에 쓴다(DATA-NI4). 못 읽으면 None.
            "income_before_tax_krw": bs_is.get("income_before_tax"),
            # 분기 표의 순이익은 주요계정 「당기순이익」= **비지배지분을 포함한 총계**다(요약·yoy 의 지배 순이익과 다른 기준).
            # 000230 2023 3분기: 총계 −574.4억 · 지배 −255.5억. 값은 그대로 두고 기준을 행에 싣는다(DATA-NI2).
            "net_income_basis": NET_INCOME_BASIS_TOTAL,
            "total_assets_krw": bs_is.get("total_assets"),
            "total_equity_krw": bs_is.get("total_equity"),
            "total_liabilities_krw": bs_is.get("total_liabilities"),
            "_fs": actual or fs_div,   # 이 보고서의 실제 재무제표 기준 — 지배 순이익 DB 칸과 기준이 같을 때만 쓴다(반환 전에 뗀다)
        })
        meta.append((year, rc, fiscal_quarter, fiscal_year, actual or fs_div))

    # ── 매출 lazy 폴백: 주요계정에 매출 행이 없는 분기만 전체 재무제표를 한 번 더 부른다 ──
    # 리파인·티움바이오 「영업수익」, 보험 「보험수익」, 은행 「이자수익」 — 연간(yearly)과 같은 규칙.
    # 호출 수는 매출이 빈 분기 수만큼(최대 12). 매출이 있는 회사는 0.
    # Q4 = 연간 − 9개월 누적 차분이라 둘을 **같은 소스**(전체 재무제표)에서 읽어야 gap 이 안 생긴다 —
    # Q3 의 누적(thstrm_add)도 여기서 같이 채운다. 분기보고서의 thstrm=당기 3개월·thstrm_add=누적은
    # fnlttSinglAcntAll 도 동일(리파인 2025 1Q·반기·3Q 실측, 260906).
    missing = [(i, *m) for i, m in enumerate(meta) if out[i]["revenue_krw"] is None]
    if missing:
        fills = await asyncio.gather(*[_safe_fetch_acnt_all(corp_code, y, rc, actual)
                                       for (_i, y, rc, _fq, _fy, actual) in missing])
        filled_labels: set[str] = set()
        for (i, y, rc, fq, fy, actual), (rows_all, err_all) in zip(missing, fills):
            if err_all and err_all != "no_filing":
                warnings.append(err_all)
            if not rows_all:
                continue
            m = _build_account_map_all(rows_all, induty_code=induty_code)  # thstrm: 분기=당기 3개월, 연간=12개월
            if m.get("revenue") is None:
                continue
            out[i]["revenue_krw"] = m["revenue"]
            out[i]["revenue_account_nm"] = m.get("revenue_account_nm")
            out[i]["revenue_standard"] = m.get("revenue_standard")
            out[i]["revenue_basis"] = m.get("revenue_basis")
            filled_labels.add(m.get("revenue_account_nm") or "")
            if fq == 3:
                cum9_by_year.setdefault(fy, {})["revenue"] = _build_account_map_all(
                    rows_all, period="thstrm_add", induty_code=induty_code).get("revenue")
            if fq != 4:
                q_standalone_by_year.setdefault(fy, {}).setdefault(f"Q{fq}", {})["revenue"] = m["revenue"]
        if filled_labels:
            labels = "」·「".join(sorted(l for l in filled_labels if l))
            warnings.append(f"매출은 주요계정에 없어 전체 재무제표 「{labels}」 행에서 읽음 ({len(missing)}개 분기 추가 조회).")

    # 요청한 fiscal year 밖의 자료는 최신 12행을 채우기 위해 수집했더라도 버린다.
    # 기존에는 3·6월 결산사의 다음 FY-Q1이 out[-12:]에 섞일 수 있었다.
    target_min_year = end_year - 2
    out = [r for r in out if target_min_year <= r["fiscal_year"] <= end_year]

    # ── 보고 통화 — 남은(표시 대상) 분기들만 본다 ──
    # 한 통화뿐이면 예전과 같은 단일 환율 경로(아래 끝). 표시 창 **안에서** 통화가 바뀌었으면(예: 2023 CNY·
    # 2024 USD) 금액을 각 분기 자기 통화로 읽어 **같은 날**(마지막 분기말) 환율로 지금 원화로 바꿔 둔다 —
    # 그래야 Q4 차분·분기 합 점검·QoQ/YoY 가 한 단위에서 계산된다(통화가 다른 값을 빼거나 나누지 않는다).
    row_curs = {r["_cur"] for r in out}
    stmt_cur = next(iter(row_curs)) if len(row_curs) == 1 else "KRW"
    mixed_cur = len(row_curs) > 1
    mixed_converted = False
    fx_date = last_period_end or f"{end_year}1231"
    fx_by_cur: dict[str, float | None] = {}
    if mixed_cur:
        _years_in = {r["fiscal_year"] for r in out}
        _need = set(row_curs) | {c for fy, c in cum9_cur.items() if fy in _years_in} \
            | {c for (fy, _q), c in qs_cur.items() if fy in _years_in}
        for c in sorted(_need):
            _r = await fx_to_krw(c, fx_date)
            fx_by_cur[c] = _r if (c == "KRW" or (_r and _r != 1.0)) else None
        if all(fx_by_cur.values()):
            def _scale_map(m: dict[str, Any], rate: float) -> None:
                for k in _QUARTERLY_IS_KEYS + (_QUARTERLY_PRETAX_KEY, "total_assets", "total_equity", "total_liabilities"):
                    v = m.get(k)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        m[k] = round(v * rate)
            for row in out:
                rate = fx_by_cur[row["_cur"]] or 1.0
                for k, v in list(row.items()):
                    if k.endswith("_krw") and isinstance(v, (int, float)) and not isinstance(v, bool):
                        row[k] = round(v * rate)
            for fy, m in cum9_by_year.items():
                if fy in _years_in and fy in cum9_cur:
                    _scale_map(m, fx_by_cur[cum9_cur[fy]] or 1.0)
            for fy, qmap in q_standalone_by_year.items():
                if fy not in _years_in:
                    continue
                for ql, m in qmap.items():
                    if (fy, ql) in qs_cur:
                        _scale_map(m, fx_by_cur[qs_cur[(fy, ql)]] or 1.0)
            mixed_converted = True

    def _nine_cur(fy: int) -> str | None:
        """Q4 차분에 쓰는 9개월 누적의 통화 — Q3 누적이 있으면 그것, 없으면 Q1~Q3 가 한 통화일 때만."""
        if fy in cum9_cur:
            return cum9_cur[fy]
        cs = {qs_cur.get((fy, q)) for q in ("Q1", "Q2", "Q3")}
        return next(iter(cs)) if len(cs) == 1 else None

    # CFS 요청인데 일부/전부 분기가 OFS면 경고 (조용한 폴백·분기 간 기준 혼재 방지).
    if fs_div == "CFS" and "OFS" in fs_seen:
        if "CFS" in fs_seen:
            note_degradation("statement_basis")
            warnings.append("일부 분기에 연결재무제표(CFS)가 없어 별도(OFS)로 대체됨 — 분기 간 기준 혼재 주의.")
        else:
            warnings.append("연결재무제표(CFS) 미작성 — 별도(OFS) 기준 분기 산출. 연결 기준 수치 아님.")

    # Q4 손익 차분: 연간 − 9개월 누적(Q3 add 우선, 없으면 Q1+Q2+Q3 standalone 합)
    for row in out:
        if row["quarter"] != "Q4":
            row["basis"] = "standalone_3m"
            continue
        year = row["fiscal_year"]
        cum9 = cum9_by_year.get(year, {})
        qs = q_standalone_by_year.get(year, {})
        failed_keys = []
        derived_count = 0
        # 그 해 안에서 통화가 바뀌었는데 원화로 못 바꿨으면(환율 실패) 차분하지 않는다 — 단위가 다른 값을 빼게 된다.
        cur_clash = mixed_cur and not mixed_converted and _nine_cur(year) != row["_cur"]
        for key in _QUARTERLY_IS_KEYS:
            annual = row[f"{key}_krw"]
            if annual is None:
                continue  # 계정 자체 부재 (금융사 매출액 등) — 차분 실패가 아님
            nine = cum9.get(key)
            if nine is None:
                parts = [qs.get(q, {}).get(key) for q in ("Q1", "Q2", "Q3")]
                nine = sum(parts) if all(p is not None for p in parts) else None
            if nine is not None and not cur_clash:
                row[f"{key}_krw"] = annual - nine
                row[f"annual_{key}_krw"] = annual
                derived_count += 1
            else:
                failed_keys.append(key)
        # 세전이익: 연간 − 9개월 누적. 못 구하면 연간치를 분기 값으로 두지 않고 비운다(설명 줄만 빠진다 — basis 는 위 3키로 정한다).
        _pre_annual = row.get(f"{_QUARTERLY_PRETAX_KEY}_krw")
        if _pre_annual is not None:
            _pre_nine = cum9.get(_QUARTERLY_PRETAX_KEY)
            if _pre_nine is None:
                _pre_parts = [qs.get(q, {}).get(_QUARTERLY_PRETAX_KEY) for q in ("Q1", "Q2", "Q3")]
                _pre_nine = sum(_pre_parts) if all(p is not None for p in _pre_parts) else None
            row[f"{_QUARTERLY_PRETAX_KEY}_krw"] = (
                _pre_annual - _pre_nine if (_pre_nine is not None and not cur_clash) else None)
        if failed_keys:
            row["basis"] = "annual_cumulative"
            why = ("그 해 안에서 보고 통화가 바뀌었는데 환율을 못 구해" if cur_clash
                   else "분기 보고서 결측으로")
            warnings.append(
                f"{year}-Q4 {'/'.join(failed_keys)}은 연간 누적치 — {why} standalone 차분 불가. QoQ 해석 주의."
            )
        else:
            row["basis"] = "standalone_3m_derived"  # 연간 − 3분기 누적 차분

    for row in out:
        row["operating_margin_pct"] = _safe_pct(row.get("operating_profit_krw"), row.get("revenue_krw"))
        row["net_profit_margin_pct"] = _safe_pct(row.get("net_income_krw"), row.get("revenue_krw"))

    out.sort(key=lambda x: (x["fiscal_year"], int(str(x["fiscal_quarter"]).lstrip("Q"))))

    # 같은 fiscal period가 여러 API 요청에서 들어오면 최신/연간 우선으로 1건만 남긴다.
    deduped: dict[tuple[int, str], dict[str, Any]] = {}
    for row in out:
        key = (row["fiscal_year"], row["fiscal_quarter"])
        old = deduped.get(key)
        if old is None or (row["reprt_code"] == "11011" and old["reprt_code"] != "11011"):
            deduped[key] = row
    out = sorted(deduped.values(), key=lambda x: (x["fiscal_year"], int(str(x["fiscal_quarter"]).lstrip("Q"))))

    # 정합성 점검: 4분기 standalone 합 ≠ 연간이면 기중 연결범위 변동·재작성 가능성
    # (한화에어로스페이스 2024 실측 — 인적분할로 Q1·Q2 당시 보고치와 연간 재작성치 불일치).
    # Q4 = 연간 − Q3누적이라 Q4 자체는 최신 기준 정확. 차이는 Q1~Q3가 당시 보고 기준인 탓.
    _GAP_LABELS = {"revenue": "매출", "operating_profit": "영업이익", "net_income": "순이익"}
    for y in sorted({r["year"] for r in out}):
        yr_rows = [r for r in out if r["year"] == y]
        q4 = next((r for r in yr_rows if r["quarter"] == "Q4"), None)
        if not q4 or len(yr_rows) != 4:
            continue
        if mixed_cur and not mixed_converted and len({r["_cur"] for r in yr_rows}) > 1:
            continue   # 통화가 다른 분기를 더할 수 없다(환율 실패 경로)
        # 매출만이 아니라 손익 3개 키 전부 검사 — 루닛 실측: 매출 합은 일치하는데
        # 영업이익·순이익만 불일치 (중단영업 재분류 류는 손익 하단에만 영향).
        gaps: dict[str, float] = {}
        big_gaps: list[str] = []
        for key in _QUARTERLY_IS_KEYS:
            annual = q4.get(f"annual_{key}_krw")
            vals = [r.get(f"{key}_krw") for r in yr_rows]
            if annual and all(v is not None for v in vals):
                gap = sum(vals) - annual
                gap_pct = abs(gap) / abs(annual) * 100
                # flag(기계용)는 미세 재작성(>0.01%)도 기록 — 모델이 합산 검산 시 혼란 방지.
                if abs(gap) > 1_000_000 and gap_pct > 0.01:
                    gaps[key] = round(gap_pct, 2)
                # warning(사람용)은 해석에 영향 주는 0.5% 초과만.
                if gap_pct > 0.5:
                    big_gaps.append(f"{_GAP_LABELS[key]} {gap_pct:.1f}%")
        if gaps:
            q4["quarters_sum_gaps"] = gaps
            q4["quarters_sum_gap_pct"] = max(gaps.values())
        if big_gaps:
            warnings.append(
                f"⚠ {y}년 분기 합이 연간과 차이 ({', '.join(big_gaps)}) — 기중 분할·연결범위 변동·재작성으로 "
                f"Q1~Q3(당시 보고 기준)와 연간 재작성치가 다를 수 있다. 연간 추이는 yearly scope가 정확."
            )

    # 금융사(은행·지주)는 매출액 계정이 없다 (이자수익·수수료손익 구조) — 해석 안내
    if out and all(r.get("revenue_krw") is None for r in out) and any(
        r.get("operating_profit_krw") is not None or r.get("net_income_krw") is not None for r in out
    ):
        warnings.append("매출액 계정 없음 — 금융사(이자수익 구조)로 추정. 영업이익·순이익 기준으로 해석할 것.")

    # QoQ/YoY 기본 동봉 (slice 전 전체 이력 기준 — 표시 첫 분기도 직전·전년 비교 가능)
    index = {(r["fiscal_year"], r["fiscal_quarter"]): r for r in out}
    for i, row in enumerate(out):
        q_no = int(str(row["fiscal_quarter"]).lstrip("Q"))
        prev_key = (row["fiscal_year"], f"Q{q_no - 1}") if q_no > 1 else (row["fiscal_year"] - 1, "Q4")
        prev_q = index.get(prev_key)
        prev_y = index.get((row["fiscal_year"] - 1, row["fiscal_quarter"]))
        row["qoq_pct"] = {
            "revenue": _qchg_pct(row.get("revenue_krw"), prev_q.get("revenue_krw") if prev_q else None),
            "operating_profit": _qchg_pct(row.get("operating_profit_krw"), prev_q.get("operating_profit_krw") if prev_q else None),
            "net_income": _qchg_pct(row.get("net_income_krw"), prev_q.get("net_income_krw") if prev_q else None),
            # 마진은 증감률(%)이 아니라 %포인트(pp) 차이로 — 손익(원)은 위 3키, 마진은 _pp 키.
            "operating_margin_pp": _pp_diff(row.get("operating_margin_pct"), prev_q.get("operating_margin_pct") if prev_q else None),
            "net_profit_margin_pp": _pp_diff(row.get("net_profit_margin_pct"), prev_q.get("net_profit_margin_pct") if prev_q else None),
        }
        row["yoy_pct"] = {
            "revenue": _qchg_pct(row.get("revenue_krw"), prev_y.get("revenue_krw") if prev_y else None),
            "operating_profit": _qchg_pct(row.get("operating_profit_krw"), prev_y.get("operating_profit_krw") if prev_y else None),
            "net_income": _qchg_pct(row.get("net_income_krw"), prev_y.get("net_income_krw") if prev_y else None),
            "operating_margin_pp": _pp_diff(row.get("operating_margin_pct"), prev_y.get("operating_margin_pct") if prev_y else None),
            "net_profit_margin_pp": _pp_diff(row.get("net_profit_margin_pct"), prev_y.get("net_profit_margin_pct") if prev_y else None),
        }
        if mixed_cur and not mixed_converted:
            # 환율 실패 경로 — 통화가 다른 분기 사이 금액 증감률은 뜻이 없다(마진 pp 는 비율이라 그대로 둔다).
            for _pct, _prev in (("qoq_pct", prev_q), ("yoy_pct", prev_y)):
                if _prev is not None and _prev["_cur"] != row["_cur"]:
                    for _k in _QUARTERLY_IS_KEYS:
                        row[_pct][_k] = None
    if mixed_cur:
        # 표시 창 안에서 보고 통화가 바뀐 회사 — 위에서 이미 분기마다 자기 통화로 원화 환산했다(같은 날 환율).
        _span = {c: sorted(f"{r['fiscal_year']}{r['fiscal_quarter']}" for r in out if r["_cur"] == c)
                 for c in sorted(row_curs)}
        _span_txt = " · ".join(f"{c} {v[0]}~{v[-1]}" for c, v in _span.items() if v)
        for row in out:
            row["functional_currency"] = row["_cur"]
            _r = fx_by_cur.get(row["_cur"])
            row["fx_rate_to_krw"] = round(_r, 2) if (mixed_converted and _r) else (
                1.0 if row["_cur"] == "KRW" else None)
            if mixed_converted and _r and row["_cur"] != "KRW":
                row["fx_source"] = fx_source(row["_cur"], fx_date)
            if not mixed_converted and row["_cur"] != "KRW" and get_strict_as_of():
                _exclude_unconverted_amounts(row)
        if mixed_converted:
            _rates = " · ".join(f"{c} {fx_by_cur[c]:,.1f}원" for c in sorted(row_curs) if c != "KRW")
            warnings.append(
                f"보고 통화가 기간 중 바뀌었다({_span_txt}) — 각 분기 금액을 그 분기 보고 통화로 읽어 "
                f"{fx_date} 환율({_rates})로 KRW 환산했다(12분기 같은 날 환율 — Q4 차분·증감률도 원화로 계산).")
            if any(fx_source(c, fx_date) == SRC_YAHOO for c in row_curs if c != "KRW"):
                warnings.append(f"⚠️ {fx_date} 환율: {FALLBACK_WARNING}")
        elif get_strict_as_of():
            warnings.append(
                f"보고 통화가 기간 중 바뀌었다({_span_txt}) — 기준시점의 환율 공개 여부를 확인할 수 없어 외화 분기의 "
                "원화 금액을 제외했습니다. 통화가 다른 분기 사이 금액 증감률은 비웠습니다.")
        else:
            warnings.append(
                f"⚠️ 보고 통화가 기간 중 바뀌었는데({_span_txt}) 환율 조회 실패 — 아래 분기 금액은 **각 행의 "
                f"functional_currency 단위 그대로**이고 원화가 아니다. 통화가 다른 분기 사이 금액 증감률은 비웠다.")
    # 통화 정규화 — 12분기 **전체에 단일 환율**을 쓴다. 분기마다 다른 환율을 쓰면
    # Q4 차분(연간 − 3분기 누적)이 서로 다른 환율의 값을 빼게 되어 Q4 가 오염된다.
    elif stmt_cur != "KRW":
        rate = await fx_to_krw(stmt_cur, last_period_end or f"{end_year}1231")
        _src = fx_source(stmt_cur, last_period_end or f"{end_year}1231")
        if rate and rate != 1.0:
            for row in out:
                for k, v in list(row.items()):
                    if k.endswith("_krw") and isinstance(v, (int, float)) and not isinstance(v, bool):
                        row[k] = round(v * rate)
                row["functional_currency"] = stmt_cur
                row["fx_rate_to_krw"] = round(rate, 2)
                row["fx_source"] = _src
            warnings.append(
                f"기능통화 {stmt_cur} — 분기 금액을 {last_period_end} 환율 {rate:,.1f}원/{stmt_cur} 로 "
                f"KRW 환산했다(12분기 단일환율 — 분기마다 다른 환율을 쓰면 Q4 차분이 오염된다).")
            if _src == SRC_YAHOO:
                warnings.append(f"⚠️ {last_period_end or f'{end_year}1231'} 환율: {FALLBACK_WARNING}")
        else:
            for row in out:
                row["functional_currency"] = stmt_cur
                row["fx_rate_to_krw"] = None
                if get_strict_as_of():
                    _exclude_unconverted_amounts(row)
            if get_strict_as_of():
                warnings.append(
                    f"기능통화 {stmt_cur} — 기준시점의 환율 공개 여부를 확인할 수 없어 분기 원화 금액을 제외했습니다. "
                    "통화에 영향받지 않는 비율은 유지하며 원화 임계치 평가는 건너뜁니다.")
            else:
                warnings.append(
                    f"⚠️ 기능통화 {stmt_cur} 인데 환율 조회 실패 — 아래 분기 금액은 **{stmt_cur} 단위 그대로**이고 "
                    f"원화가 아니다. 원화 기준 비교에 그대로 쓰지 말 것.")
    _attach_ni_over_operating(out)
    await _attach_controlling_ni(out, stock_code, warnings)
    for row in out:
        row.pop("_cur", None)
        row.pop("_fs", None)
    return out[-num_quarters:], warnings


def _attach_ni_over_operating(rows: list[dict[str, Any]]) -> None:
    """순이익(총계)/영업이익 > 1.3배인 분기에 영업외손익·법인세를 단다(DATA-NI4). 값은 고치지 않는다 — 원문과 같은 값의 설명이다.

    영업외손익 = 법인세차감전 순이익 − 영업이익(금융손익·지분법·기타를 모두 포함한 순액), 법인세 = 세전이익 − 순이익. 그래서
    영업이익 + 영업외손익 − 법인세 = 순이익으로 닫힌다. 영업이익이 0 이하이거나 세전이익을 못 읽었거나 Q4 연간 누적(차분 불가) 행은 달지 않는다.
    SK하이닉스 2026-Q2: 영업이익 60.54조 · 세전 122.71조 · 순이익 93.92조 → 영업외 +62.17조 · 법인세 28.79조(반기보고서 원문과 같다)."""
    for row in rows:
        op, ni, pre = row.get("operating_profit_krw"), row.get("net_income_krw"), row.get("income_before_tax_krw")
        if row.get("basis") == "annual_cumulative" or op is None or ni is None or pre is None or op <= 0:
            continue
        ratio = ni / op
        if ratio <= NI_OVER_OPERATING_RATIO:
            continue
        row["net_to_operating_ratio"] = round(ratio, 2)
        row["non_operating_net_krw"] = pre - op
        row["income_tax_krw"] = pre - ni


def _prev_cell_key(period_end: str, quarter: int) -> tuple[int, int] | None:
    """한 분기 앞 칸의 DART 표기 `(fy, quarter)` — 기간 말에서 석 달 앞(그 달 말)의 달력 연도와 직전 순번. 1분기는 앞 칸이 없다(누적 자신이 3개월 값)."""
    if quarter <= 1:
        return None
    try:
        d = date.fromisoformat(str(period_end)[:10])
    except ValueError:
        return None
    y, m = (d.year, d.month - 3) if d.month > 3 else (d.year - 1, d.month + 9)
    return y, quarter - 1


async def _attach_controlling_ni(rows: list[dict[str, Any]], stock_code: str | None, warnings: list[str]) -> None:
    """분기 행에 새 키 `net_income_controlling_krw`(지배 순이익 3개월)·`net_income_controlling_source` 를 단다(DATA-NI2). 옛 `net_income_krw`(총계)·`net_income_basis` 는 그대로.

    값 = 운영 `dart_finstat_q.ni_cum`(수집이 공용 `controlling_ni` 규칙으로 저장한 **누적** 지배 순이익)의 차분: 1분기는 그 누적 자체, 2·3분기·4분기는 이 칸 누적 − 한 분기 앞 칸 누적.
    DART 호출은 늘지 않는다(분기마다 `fnlttSinglAcntAll` 을 더 부르면 하루 한도를 쓴다) — DB 1회. 칸의 키는 DART 표기 `(기간 말 달력 연도, 보고서 순번)`(report_calendar).
    · 보고 통화가 KRW 가 아닌 행(환산된 값)은 비운다 — 저장 칸과 통화·환율 기준이 달라 섞지 않는다.
    · 두 칸의 재무제표 기준(`fs`)이 서로 다르거나 이 보고서의 기준과 다르면 비운다(DATA-Q5). 앞 칸이 없으면 비운다(추정하지 않는다).
    · 정정 칸(`ni_cum_restated`)이 아니라 원본 `ni_cum` 을 쓴다 — 이 행의 총계(`net_income_krw`)도 그 분기 보고서 값이라 같은 시점의 값끼리다.
    · 종목코드가 없거나 DB 를 못 읽으면 값은 비고(키는 있다) DB 를 못 읽었을 때만 경고한다."""
    for row in rows:
        row["net_income_controlling_krw"] = None
        row["net_income_controlling_source"] = None
    if not stock_code or not rows:
        return
    try:
        from open_proxy_mcp.db import pg_rows
        got = await asyncio.to_thread(
            pg_rows,
            "SELECT fy, quarter, to_jsonb(dart_finstat_q)->>'fs', ni_cum FROM dart_finstat_q WHERE ticker=%s AND ni_cum IS NOT NULL",
            (stock_code,))
    except Exception:  # noqa: BLE001 — 덧붙임이다: 읽기가 깨져도 종전 응답은 나간다
        got = None
    if got is None:
        warnings.append("분기 지배 순이익(net_income_controlling_krw)을 DB 에서 읽지 못해 비웠다(미설정 또는 장애) — net_income_krw 는 비지배 포함 총계다.")
        return
    cells: dict[tuple[int, int], tuple[str | None, float]] = {}
    for fy, q, fs, ni in got:
        try:
            cells[(int(fy), int(q))] = ((str(fs).upper() if fs else None), float(ni))
        except (TypeError, ValueError):
            continue
    for row in rows:
        if row.get("_cur", "KRW") != "KRW":
            continue
        try:
            q = int(str(row["fiscal_quarter"]).lstrip("Q"))
            pe = date.fromisoformat(str(row.get("period_end"))[:10])
        except (KeyError, TypeError, ValueError):
            continue
        cur = cells.get((pe.year, q))
        if cur is None:
            continue
        rfs = str(row.get("_fs") or "").upper()
        if (cur[0] and rfs and cur[0] != rfs):
            continue
        value = cur[1]
        if q > 1:
            pk = _prev_cell_key(str(row.get("period_end")), q)
            prev = cells.get(pk) if pk else None
            if prev is None or (prev[0] and cur[0] and prev[0] != cur[0]):
                continue
            value = cur[1] - prev[1]
        row["net_income_controlling_krw"] = int(round(value))
        row["net_income_controlling_source"] = "db_ni_cum_diff"


_QUARTER_REPRT = {"Q1": "11013", "Q2": "11012", "Q3": "11014", "Q4": "11011"}
STATUS_UNSUBMITTED = "미제출"
STATUS_FETCH_FAILED = "조회 실패(미확인)"


def _quarter_fetch_failed(failed: set[tuple[int, str]] | None, fiscal_year: int, label: str,
                          fiscal_month: int | None) -> bool:
    """그 분기 보고서의 조회가 실패한 적이 있나. 12월 결산이면 DART 사업연도 = 회계연도라 정확히 맞춘다.
    그 밖의 결산월은 DART 사업연도와 회계연도가 한 해 어긋날 수 있어 앞뒤 한 해까지 의심한다 — 「미제출」이라고
    단정하는 것보다 「확인 못 했다」가 안전한 쪽이다."""
    if not failed:
        return False
    rc = _QUARTER_REPRT[label]
    if fiscal_month in (None, 12):
        return (fiscal_year, rc) in failed
    return any(r == rc and abs(y - fiscal_year) <= 1 for y, r in failed)


def _quarter_submit_due(period_end: date, quarter: int, fiscal_month: int, ticker: str | None) -> date:
    """그 분기 보고서의 **법정 제출 기한을 지난 첫 날** — `as_of` 가 이 날 이상이어야 「미제출」이라 적는다(DATA-API1).

    `report_calendar.cell_available` 이 기준이다(분기·반기 45일 · 사업보고서 90일 · 외국 상장사 60·120일). 그 함수는 사업보고서는 기한 다음 날(12월 결산 4/1)을,
    분기·반기는 기한 날 자체(11/14)를 돌려주므로 분기·반기만 하루 더한다 — 기한 당일은 아직 낼 수 있어 「미제출」이라 단정하지 않는다.
    DART 표기 사업연도 = 기간 말이 속한 달력 연도(report_calendar 머리말)."""
    from open_proxy_mcp.services import report_calendar as rcal

    avail = rcal.cell_available(period_end.year, quarter, rcal.fiscal(fiscal_month, ticker))
    return avail if quarter == 4 else avail + timedelta(days=1)


def _quarterly_status(rows: list[dict[str, Any]], fiscal_year: int,
                      fiscal_month: int | None, as_of: date | None = None,
                      failed: set[tuple[int, str]] | None = None,
                      ticker: str | None = None) -> dict[str, Any]:
    """대상 FY의 분기 공시 상태를 데이터 누락과 미제출·제출기한 전·조회 실패로 구분한다.

    `failed` = 조회가 실패한 (사업연도, 보고서코드) — 그 분기는 보고서가 없는 게 아니라 **읽지 못한** 것이라
    「미제출」로 적지 않고 「조회 실패(미확인)」로 적는다(DATA-API1·DATA-U3).

    결산일이 지났어도 법정 제출 기한 전이면 「제출기한 전」이다(DATA-API1 — 종전엔 결산 다음 날부터 「미제출」이라
    12월 결산 모든 회사의 3분기가 11월 중순 전까지 거짓 미제출이었다). 기한은 `report_calendar.cell_available`
    (분기·반기 45일 · 사업보고서 90일 · `ticker` 가 9로 시작하는 외국 상장사 60·120일) — 결산월을 바꾼 회사의 옛 결산월은 반영하지 않는다."""
    as_of = as_of or today_kst()
    present = {r.get("fiscal_quarter") for r in rows if r.get("fiscal_year") == fiscal_year}
    result: dict[str, Any] = {"fiscal_year": fiscal_year, "missing": []}
    if not fiscal_month:
        return result

    for q in range(1, 5):
        label = f"Q{q}"
        if label in present:
            continue
        end_month = ((fiscal_month + (3 * q) - 1) % 12) + 1
        end_year = fiscal_year if end_month <= fiscal_month else fiscal_year - 1
        # 월말을 정확히 계산하지 않아도 제출기한 판정에는 충분하다.
        next_month = date(end_year + (1 if end_month == 12 else 0),
                          1 if end_month == 12 else end_month + 1, 1)
        period_end = next_month - timedelta(days=1)
        if period_end > as_of:
            # 아직 결산일이 오지 않은 분기는 누락이나 미제출로 표시하지 않는다.
            continue
        due = _quarter_submit_due(period_end, q, fiscal_month, ticker)
        item = {
            "fiscal_quarter": label,
            "period_end": period_end.isoformat(),
            "status": STATUS_UNSUBMITTED if as_of >= due else "제출기한 전",
        }
        if _quarter_fetch_failed(failed, fiscal_year, label, fiscal_month):
            item["status"] = STATUS_FETCH_FAILED
            item["reprt_code"] = _QUARTER_REPRT[label]
        result["missing"].append(item)
    return result


# ── public payload builder ──

def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    return ToolEnvelope(
        tool="financial_metrics",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope는 아직 지원하지 않는다."],
        data={"query": company_query, "scope": scope, "available_scopes": sorted(_SUPPORTED_SCOPES)},
    ).to_dict()


# ── Phase 3 F2 — 응답 caching (TTL 5분) ──
# 같은 (company, scope, year, consolidated) 조합 재호출 시 동일 결과 보장.
# advise_vote의 3 run 호출 시 모든 run에서 동일 fm_payload 반환 → cash_dividend 결정 결정성.
from open_proxy_mcp.dart.client import LruByteCache, _env_mb
_FM_CACHE_TTL = 300.0
# Payloads are immutable after insertion. The shared registered cache participates in reclamation.
_FM_CACHE = LruByteCache(_env_mb("OPM_FINANCIAL_CACHE_MB", 16), _FM_CACHE_TTL, "financial_metrics")


def _fm_cache_get(key: tuple) -> dict[str, Any] | None:
    from open_proxy_mcp.dart.as_of import get_strict_as_of
    if get_strict_as_of() is not None:
        return None
    return _FM_CACHE.get(key)


def _fm_cache_set(key: tuple, payload: dict[str, Any]) -> None:
    from open_proxy_mcp.dart.as_of import get_strict_as_of
    if get_strict_as_of() is None:
        _FM_CACHE.put(key, payload)


async def build_financial_metrics_payload(
    company_query: str,
    *,
    scope: str = "summary",
    year: int | None = None,
    years: int = 3,
    consolidated: bool = True,
    sj_div: list[str] | None = None,
) -> dict[str, Any]:
    total_started_at = time.perf_counter()
    timings_ms: dict[str, int] = {}

    def _mark(stage: str, started_at: float) -> None:
        timings_ms[stage] = int((time.perf_counter() - started_at) * 1000)

    # `years` 에 상한이 없으면 한 요청이 분당 910 캡을 통째로 밀 수 있다(연도당 최대 4콜).
    # memo 로 인접 연도 중복은 사라졌지만 상한 자체는 여전히 필요하다.
    _years_req = years
    years = max(1, min(int(years or 3), _MAX_YEARS))

    def _note_clamp(payload: dict[str, Any]) -> dict[str, Any]:
        """`years` 를 줄였다는 사실을 반환 직전에 붙인다.

        캐시 키는 **클램프된** years 라 years=30 과 10 이 같은 항목을 맞는다(그게 옳다 — 같은
        요청이다). 다만 30 을 물은 사람은 캐시 적중일 때도 잘렸다는 걸 들어야 하므로, 캐시에
        넣는 payload 가 아니라 **내보내는 사본**에만 붙인다.
        """
        if not (_years_req and int(_years_req) > years):
            return payload
        out = dict(payload)
        out["warnings"] = list(out.get("warnings") or []) + [
            f"years={_years_req} 요청을 상한 {years}년으로 줄였다 — 연도당 DART 호출이 붙어 "
            f"한 요청이 분당 한도를 밀지 않게 한다. 더 긴 추이는 기간을 나눠 부른다."]
        return out

    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    # F2 cache check
    cache_key = (company_query, scope, year, years, consolidated)
    cached = _fm_cache_get(cache_key)
    if cached is not None:
        return _note_clamp(cached)
    fetch_failures = {"n": 0}
    _FETCH_FAILURES.set(fetch_failures)

    fs_div = "CFS" if consolidated else "OFS"
    client = get_dart_client()
    calls_start = client.api_call_snapshot()

    stage_started_at = time.perf_counter()
    resolution = await resolve_company_query(company_query)
    _mark("resolve_company", stage_started_at)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
        return ToolEnvelope(
            tool="financial_metrics",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[company_not_found_warning(company_query)],
            data={
                "query": company_query,
                "scope": scope,
                "usage": build_usage(client.api_call_snapshot() - calls_start),
                "timings_ms": timings_ms,
            },
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
        return ToolEnvelope(
            tool="financial_metrics",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 재무 데이터를 자동 선택하지 않았다."],
            data={
                "query": company_query,
                "scope": scope,
                "candidates": [
                    {
                        "company_id": _company_id(corp),
                        "corp_name": corp.get("corp_name", ""),
                        "ticker": corp.get("stock_code", ""),
                        "corp_code": corp.get("corp_code", ""),
                    }
                    for corp in resolution.candidates[:10]
                ],
                "usage": build_usage(client.api_call_snapshot() - calls_start),
                "timings_ms": timings_ms,
            },
        ).to_dict()

    selected = resolution.selected
    corp_code = selected["corp_code"]
    company_info, company_info_warning = await _safe_company_info(corp_code)
    fiscal_month_raw = company_info.get("acc_mt") or ""
    try:
        fiscal_month = int(fiscal_month_raw)
    except (TypeError, ValueError):
        fiscal_month = None
    # KSIC 업종 — ① 매출 계정 선택 순서(보험=보험수익, 은행=영업수익→이자수익…) ② 금융사 판별 2차 신호
    # (수신 없어 예수부채 無인 카드·캐피탈·VC 보완). 방금 부른 company.json 에 `induty_code` 가 이미
    # 있으니 그것부터(DART 콜 0 추가) — 없을 때만 mkt_fundamentals(Postgres) 조회. 둘 다 없으면 None(무해).
    induty_code = str(company_info.get("induty_code") or "").strip() or None
    if induty_code is None:
        induty_code = await asyncio.to_thread(_lookup_induty_code, corp_code, selected.get("stock_code", ""))
    # REIT는 순이익 기준 배당성향이 무의미(배당가능이익 분배) → 이름 기반 판정해 성향 억제 + 안내.
    is_reit = _is_reit(selected.get("corp_name", ""))
    # 분기 인지형 디폴트: quarterly/qoq는 이미 제출된 최신 분기(예: 당해 1분기는 5월 공시)를
    # 봐야 하므로 디폴트 end_year를 당해 연도로. _build_quarterly는 end_year-2..end_year를
    # 받고 미공시 분기는 graceful skip이라 호출 수(12) 변동 없이 최신 분기가 포함된다.
    # summary/yearly/yoy는 연간(사업보고서) 기준이라 _default_recent_year — 261019: 결산월로(12월 결산이면 종전 「4월 이후 전년」 그대로).
    if year:
        target_year = year
    elif scope in ("quarterly", "qoq"):
        from datetime import date as _date
        target_year = today_kst().year
    else:
        target_year = _default_recent_year(fiscal_month, selected.get("stock_code"))
        if is_reit:
            target_year = await _reit_default_year(corp_code, target_year)

    warnings: list[str] = []
    if company_info_warning:
        warnings.append(f"회사 결산월 조회 실패 — 분기 라벨은 DART 조회연도 기준: {company_info_warning}")
    evidence_refs: list[EvidenceRef] = []
    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": corp_code,
        },
        "scope": scope,
        "year": target_year,
        "fs_div": fs_div,
        "consolidated": consolidated,
        "fiscal_year_end_month": fiscal_month,
        "available_scopes": sorted(_SUPPORTED_SCOPES),
    }
    # 「사업연도 N」이 어느 12개월인지 라벨에 박는다 — 6월 결산 회사에서 1년을 통째로
    # 오독하는 자리다(260828 U 지적 B-5). 결산월을 못 얻으면 붙이지 않는다(추측 금지).
    _span = fiscal_year_span(target_year, fiscal_month)
    if _span:
        data["fiscal_period"] = {
            "start": _span[0],
            "end": _span[1],
            "fiscal_year_end_month": fiscal_month,
            "label": fiscal_period_label(target_year, fiscal_month),
        }

    parsing_failures = 0
    filing_count = 0

    stage_started_at = time.perf_counter()
    if scope == "summary":
        # 요약(단년)은 보고서가 당기·전기 둘뿐이라 단위 어긋남을 다수결로 못 가린다 — 상장사면 KRX 보통주 시총을 제3 신호로 쓴다(DATA-SC1, DB 1회·DART 0콜).
        _mk = asyncio.ensure_future(_common_mktcap(selected.get("stock_code"))) if selected.get("stock_code") else None
        try:
            metrics, ws, ev_count = await _fetch_year_metrics(corp_code, target_year, fs_div, include_prev=True, induty_code=induty_code, is_reit=is_reit,
                                                             stock_code=selected.get("stock_code"), mktcap=_mk)
        finally:
            # 시총은 사업보고서 갈래에서만 기다린다 — 분기·반기로 물러난 해나 자료 없음이면 쓰지 않았다: 남겨 두지 않고 거둔다(끝난 것은 그대로).
            if _mk is not None and not _mk.done():
                _mk.cancel()
        warnings.extend(ws)
        if metrics:
            data["summary"] = metrics
            _override_fiscal_period(data, metrics, fiscal_month)
            filing_count = 1
            ref = await _periodic_filing_ref(corp_code, target_year, metrics.get("reprt_code"))
            data["source_report"] = ref or {}
            evidence_refs.append(EvidenceRef(
                evidence_id=f"ev_fm_summary_{corp_code}_{target_year}",
                source_type=SourceType.DART_API,
                rcept_no=(ref or {}).get("rcept_no", ""),
                rcept_dt=(ref or {}).get("rcept_dt", ""),
                report_nm=(ref or {}).get("report_nm", ""),
                section=f"사업보고서 ({target_year}) 단일회사 주요계정 + 전체재무제표 + 주요지표",
                note=f"{selected.get('corp_name', '')} {target_year}년 {metrics.get('fs_div', fs_div)}",
            ))
        else:
            parsing_failures = 1

    elif scope == "yearly":
        rows, ws = await _build_yearly(corp_code, target_year, years, fs_div, induty_code, is_reit=is_reit,
                                     stock_code=selected.get("stock_code"))
        warnings.extend(ws)
        data["yearly"] = rows
        filing_count = len(rows)
        if rows:
            ref = await _periodic_filing_ref(corp_code, rows[-1]["year"], "11011")
            data["source_report"] = ref or {}
            evidence_refs.append(EvidenceRef(
                evidence_id=f"ev_fm_yearly_{corp_code}_{target_year}",
                source_type=SourceType.DART_API,
                rcept_no=(ref or {}).get("rcept_no", ""),
                rcept_dt=(ref or {}).get("rcept_dt", ""),
                report_nm=(ref or {}).get("report_nm", ""),
                section=f"사업보고서 ({rows[0]['year']}~{rows[-1]['year']}) {len(rows)}년 추이",
                note=f"{selected.get('corp_name', '')} 연간 추이 (최신 보고서 기준)",
            ))

    elif scope == "quarterly":
        failed_fetches: set[tuple[int, str]] = set()
        rows, ws = await _build_quarterly(corp_code, target_year, fs_div, fiscal_month=fiscal_month,
                                          induty_code=induty_code, failed_out=failed_fetches,
                                          stock_code=selected.get("stock_code"))
        warnings.extend(ws)
        data["quarterly"] = rows
        data["quarterly_status"] = _quarterly_status(rows, target_year, fiscal_month, failed=failed_fetches,
                                                     ticker=selected.get("stock_code"))
        for missing in data["quarterly_status"]["missing"]:
            if missing["status"] == STATUS_UNSUBMITTED:
                warnings.append(
                    f"{target_year}-{missing['fiscal_quarter']} ({missing['period_end']})는 법정 제출 기한이 지났는데 공시가 없어 미제출로 표시함."
                )
            elif missing["status"] == STATUS_FETCH_FAILED:
                warnings.append(
                    f"{target_year}-{missing['fiscal_quarter']} ({missing['period_end']}) 보고서 조회가 DART 오류로 실패해(020 하루 한도·800 점검 등) "
                    f"제출 여부를 확인하지 못했다 — 「미제출」이 아니다. 잠시 뒤 다시 조회한다."
                )
        filing_count = len(rows)
        if rows:
            ref = await _periodic_filing_ref(corp_code, rows[-1]["year"], rows[-1].get("reprt_code"))
            data["source_report"] = ref or {}
            evidence_refs.append(EvidenceRef(
                evidence_id=f"ev_fm_quarterly_{corp_code}_{target_year}",
                source_type=SourceType.DART_API,
                rcept_no=(ref or {}).get("rcept_no", ""),
                rcept_dt=(ref or {}).get("rcept_dt", ""),
                report_nm=(ref or {}).get("report_nm", ""),
                section=f"분기/반기/사업보고서 {len(rows)}분기 추이 (최신: {rows[-1]['year']}-{rows[-1]['quarter']})",
                note=f"{selected.get('corp_name', '')} 분기 추이",
            ))

    elif scope == "yoy":
        # 당기 + 전기 metrics + 감사의견 cross-check — 3 독립 호출 병렬화 (5-8초 ↓)
        # memo 하나를 나눠 당기가 부르는 전기 조회와 전기 쪽 당기 조회가 겹칠 때 한 번만 부른다.
        # 261021(fin-eq): 두 해가 겹치는 보고서를 한 번만 받고(memo), 앞뒤 세 해 사업보고서끼리 대 단위 오류 보고서를 가린다 — yearly 와 같은 값.
        yoy_memo = _FetchMemo()
        _plan = asyncio.ensure_future(_annual_scale_plan(corp_code, [target_year - 1, target_year], fs_div, yoy_memo))
        curr_t, prev_t, audit_t = await asyncio.gather(
            _fetch_year_metrics(corp_code, target_year, fs_div, include_prev=True, induty_code=induty_code,
                                stock_code=selected.get("stock_code"), memo=yoy_memo, scale_plan=_plan),
            _fetch_year_metrics(corp_code, target_year - 1, fs_div, include_prev=True, induty_code=induty_code,
                                stock_code=selected.get("stock_code"), memo=yoy_memo, scale_plan=_plan),
            _build_audit_opinion_data(corp_code, target_year, years_back=2),
        )
        curr, ws_curr, _ev1 = curr_t
        prev, ws_prev, _ev2 = prev_t
        audit_data, audit_ws, _audit_ev = audit_t
        warnings.extend(ws_curr)
        audit_curr = audit_data.get("opinions", [{}])[0] if audit_data.get("opinions") else None
        audit_prev = audit_data.get("opinions", [{}, {}])[1] if len(audit_data.get("opinions", [])) >= 2 else None

        # 기간 맞춤(DATA-Q6): 올해(미공시 사업보고서 → 3분기·반기·1분기 대체)를 보면 당기는 누적 N개월인데 전기는
        # 사업보고서 12개월이라 revenue_decline 등 신호가 거짓으로 켜진다. 전기를 **당기와 같은 보고서**로 다시 센다.
        period_aligned = True
        period_note: str | None = None
        if curr and prev and curr.get("reprt_code") != prev.get("reprt_code"):
            same_rc = curr.get("reprt_code")
            prev_same, ws_same, _ev3 = await _fetch_year_metrics(
                corp_code, target_year - 1, fs_div, include_prev=True, induty_code=induty_code,
                stock_code=selected.get("stock_code"), memo=yoy_memo, reprt_code=same_rc, scale_plan=_plan)
            if prev_same and prev_same.get("reprt_code") == same_rc:
                prev = prev_same
                ws_prev = ws_same
                period_note = (f"{target_year}년은 {_REPRT_LABEL.get(same_rc, same_rc)} 기준({_period_ko(curr)})이라 "
                               f"전기도 {target_year - 1}년 같은 보고서({_REPRT_LABEL.get(same_rc, same_rc)})로 다시 계산했다 — "
                               f"기간이 같은 값끼리의 전년 비교다.")
            else:
                period_aligned = False
                warnings.extend(ws_same)   # 같은 보고서를 못 읽은 까닭(미공시 · 020 한도 등)을 그대로 싣는다
                period_note = (f"{target_year}년은 {_REPRT_LABEL.get(same_rc, same_rc)} 기준({_period_ko(curr)})인데 "
                               f"{target_year - 1}년 같은 보고서를 찾지 못해 전기는 {_period_ko(prev)} 이다 — "
                               f"기간이 다른 값끼리라 기간에 민감한 경고({', '.join(sorted(_PERIOD_SENSITIVE_ALERTS))})는 계산하지 않았다.")
            warnings.append(period_note)
        # 같은 사업보고서의 단위 경고(🔧·🚨)는 두 해가 같은 문장을 낸다 — 당기 쪽에 이미 실렸으면 한 번만(fin-eq _dedupe_scale_warnings).
        _scale_seen = {w for w in ws_curr if w.startswith(("🔧", "🚨"))}
        warnings.extend(w for w in _dedupe_scale_warnings(ws_prev) if not (w.startswith(("🔧", "🚨")) and w in _scale_seen))
        warnings.extend(audit_ws)

        signals = _detect_yoy_signals(curr or {}, prev or {}, audit_curr, audit_prev) if curr else []
        if not period_aligned:
            signals = [a for a in signals if a not in _PERIOD_SENSITIVE_ALERTS]
        data["yoy"] = {
            "current": curr,
            "prior": prev,
            "alerts": signals,
            "period_aligned": period_aligned,
            "audit_opinion": {
                "current": audit_curr,
                "prior": audit_prev,
            },
        }
        if period_note:
            data["yoy"]["period_note"] = period_note
        if not period_aligned:
            data["yoy"]["alerts_not_evaluated"] = sorted(_PERIOD_SENSITIVE_ALERTS)
        filing_count = (1 if curr else 0) + (1 if prev else 0)
        if curr:
            _override_fiscal_period(data, curr, fiscal_month)
        if curr and curr.get("yoy_comparable") is False:
            data["yoy"]["period_comparison"] = {"comparable": False, "note": curr.get("yoy_basis_note"),
                                                "current": curr.get("period_span"), "prior": curr.get("prior_period_span")}
        if curr:
            ref = await _periodic_filing_ref(corp_code, target_year, curr.get("reprt_code"))
            data["source_report"] = ref or {}
            evidence_refs.append(EvidenceRef(
                evidence_id=f"ev_fm_yoy_{corp_code}_{target_year}",
                source_type=SourceType.DART_API,
                rcept_no=(ref or {}).get("rcept_no", ""),
                rcept_dt=(ref or {}).get("rcept_dt", ""),
                report_nm=(ref or {}).get("report_nm", ""),
                section=f"전년 대비 ({target_year - 1} → {target_year}) — alerts {len(signals)}개",
                note=", ".join(signals[:5]) if signals else "alerts 없음",
            ))

    elif scope == "qoq":
        rows, ws = await _build_quarterly(corp_code, target_year, fs_div, num_quarters=4,
                                           fiscal_month=fiscal_month, induty_code=induty_code,
                                           stock_code=selected.get("stock_code"))
        warnings.extend(ws)
        # 직전 분기 vs 당기 비교
        if len(rows) >= 2:
            curr_q = rows[-1]
            prev_q = rows[-2]
            data["qoq"] = {
                "current": curr_q,
                "prior": prev_q,
                "alerts": _detect_qoq_alerts(curr_q, prev_q),
            }
        else:
            data["qoq"] = {"current": rows[-1] if rows else None, "prior": None, "alerts": []}
        filing_count = len(rows)
        if rows:
            ref = await _periodic_filing_ref(corp_code, rows[-1]["year"], rows[-1].get("reprt_code"))
            data["source_report"] = ref or {}
            evidence_refs.append(EvidenceRef(
                evidence_id=f"ev_fm_qoq_{corp_code}_{target_year}",
                source_type=SourceType.DART_API,
                rcept_no=(ref or {}).get("rcept_no", ""),
                rcept_dt=(ref or {}).get("rcept_dt", ""),
                report_nm=(ref or {}).get("report_nm", ""),
                section=f"전분기 대비 (최근 {len(rows)}분기, 최신: {rows[-1]['year']}-{rows[-1]['quarter']})",
                note=f"{selected.get('corp_name', '')} 전분기 비교",
            ))

    elif scope == "accounts":
        acc_data, acc_ws = await _build_accounts(corp_code, target_year, fs_div, sj_div)
        warnings.extend(acc_ws)
        data["accounts"] = acc_data
        if acc_data:
            filing_count = 1
            ref = await _periodic_filing_ref(corp_code, target_year, acc_data.get("reprt_code"))
            data["source_report"] = ref or {}
            evidence_refs.append(EvidenceRef(
                evidence_id=f"ev_fm_accounts_{corp_code}_{target_year}",
                source_type=SourceType.DART_API,
                rcept_no=(ref or {}).get("rcept_no", ""),
                rcept_dt=(ref or {}).get("rcept_dt", ""),
                report_nm=(ref or {}).get("report_nm", ""),
                section=f"전체 재무제표 계정 원행 ({target_year}) fnlttSinglAcntAll",
                note=f"{selected.get('corp_name', '')} {target_year}년 {acc_data.get('fs_div', fs_div)} {acc_data.get('row_count')}행",
            ))
        else:
            parsing_failures = 1

    elif scope == "audit_opinion":
        audit_data, audit_ws, audit_ev = await _build_audit_opinion_data(corp_code, target_year, years_back=years)
        warnings.extend(audit_ws)
        data["audit_opinion"] = audit_data
        filing_count = audit_data.get("summary", {}).get("history_years", 0)
        evidence_refs.extend(audit_ev)
    _mark(f"scope.{scope}", stage_started_at)

    # 모든 확정치 scope가 잠정실적과 같은 회계기간 메타데이터를 공유하도록 한다.
    def _tag_fiscal(row: dict[str, Any] | None) -> None:
        if row is not None:
            row.setdefault("fiscal_year_end_month", fiscal_month)
            row.setdefault("fiscal_year", row.get("year", target_year))

    _tag_fiscal(data.get("summary"))
    for row in data.get("yearly", []) or []:
        _tag_fiscal(row)
    for branch in (data.get("yoy"), data.get("qoq")):
        if isinstance(branch, dict):
            _tag_fiscal(branch.get("current"))
            _tag_fiscal(branch.get("prior"))

    # 0건인데 재무 조회가 **실패**한 적이 있으면 「없다」가 아니라 「모른다」다 — no_filing·「미존재 (정상)」으로 적지 않는다.
    filing_meta = build_filing_meta(filing_count=filing_count, parsing_failures=parsing_failures,
                                    fetch_failed=fetch_failures["n"] > 0)
    if filing_meta["filing_status"] == "fetch_failed":
        status = AnalysisStatus.ERROR
        warnings.append(f"{target_year}년 재무 조회가 DART 오류로 실패해(실패 {fetch_failures['n']}건) 공시가 있는지 "
                        "확인하지 못했다 — 「미존재」가 아니다. 잠시 뒤 다시 조회한다.")
    elif filing_meta["no_filing"]:
        status = AnalysisStatus.NO_FILING
        warnings.append(f"{target_year}년 사업보고서 재무 공시 미존재 (정상 — 비상장/신규/폐업 회사 가능성)")
    elif parsing_failures > 0:
        status = AnalysisStatus.PARTIAL
    else:
        status = AnalysisStatus.EXACT

    data.update(filing_meta)
    # 시그널 부여(audit w0qo5hfse): 핵심필드 None + exact → quality_flags.
    # 금융업/지주는 매출 개념 없이 영업이익·순익만 존재(이자·수수료 수익) → revenue None이 '정당 N/A'.
    # ※ revenue는 data['summary']['revenue_krw']에 있음(최상위 아님). cfo는 summary scope에 없음.
    #   검증: 삼성전자 rev=333조·현대차 186조(있음) vs KB금융·신한·삼성생명·메리츠·하나금융 None(op/ni만).
    if scope == "summary" and status == AnalysisStatus.EXACT:
        _qf: list[str] = []
        _sm = data.get("summary") or {}
        _rev = _sm.get("revenue_krw")
        _op = _sm.get("operating_profit_krw")
        _ni = _sm.get("net_income_krw")
        if _rev is None and (_op is not None or _ni is not None):
            _qf.append("매출액이 없고 영업이익·순이익만 있습니다 — 금융업·지주회사에서는 정상입니다")
        elif _rev is None and _op is None and _ni is None:
            _qf.append("core_field_null: 핵심 재무 전부 미파싱 — 진짜 실패 의심")
        if _qf:
            data["quality_flags"] = _qf
            warnings.extend(_qf)
    data["usage"] = build_usage(client.api_call_snapshot() - calls_start)
    timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
    data["timings_ms"] = timings_ms

    payload = ToolEnvelope(
        tool="financial_metrics",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=_next_actions(scope, data),
    ).to_dict()
    if status != AnalysisStatus.ERROR:   # 조회 실패 응답은 5분 동안 굳히지 않는다(다음 호출이 다시 받는다)
        _fm_cache_set(cache_key, payload)  # F2 — TTL 5분 cache (클램프 고지는 캐시에 넣지 않는다)
    return _note_clamp(payload)


def _detect_qoq_alerts(curr: dict[str, Any], prev: dict[str, Any]) -> list[str]:
    """전분기 대비 alerts (간소화 — 분기 데이터는 비교 의미 제한적)."""
    alerts: list[str] = []
    op = curr.get("operating_profit_krw")
    op_prev = prev.get("operating_profit_krw")
    if op is not None and op < 0 and (op_prev is None or op_prev >= 0):
        alerts.append("operating_loss_quarter")
    # 순이익도 영업이익과 대칭으로 적자전환 감지 (기존엔 누락 — 영업이익/매출만 봤음).
    ni = curr.get("net_income_krw")
    ni_prev = prev.get("net_income_krw")
    if ni is not None and ni < 0 and (ni_prev is None or ni_prev >= 0):
        alerts.append("net_loss_quarter")
    # 영업이익 흑자인데 순이익만 적자 = 영업외(평가손·금융비용·일회성) 주도 — 질 점검 신호.
    if ni is not None and ni < 0 and op is not None and op > 0:
        alerts.append("net_income_below_operating")
    rev = curr.get("revenue_krw")
    rev_prev = prev.get("revenue_krw")
    if rev is not None and rev_prev is not None and rev_prev > 0:
        chg = (rev - rev_prev) / rev_prev
        if chg < -0.20:
            alerts.append("revenue_decline_qoq")
    return alerts


def _next_actions(scope: str, data: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    if scope == "summary":
        actions.append("scope=`yearly`로 최근 3년 추이 비교")
        actions.append("scope=`audit_opinion`으로 감사인 변경/한정의견 추적")
    if scope == "yoy":
        alerts = (data.get("yoy", {}) or {}).get("alerts", [])
        if "operating_loss" in alerts or "loss_conversion" in alerts:
            actions.append("적자 원인 분석: scope=`quarterly` + 사업보고서 본문 (corp_gov_report)")
        if "accruals_red" in alerts or "receivables_surge" in alerts:
            actions.append("회계 risk 강화 cross-check: 분기 영업CF 추세 + 감사의견 KAM")
    if scope == "audit_opinion":
        opinions = (data.get("audit_opinion", {}) or {}).get("opinions", [])
        if any("적정" not in o.get("adt_opinion", "") for o in opinions):
            actions.append("non-clean 의견: 후보 사외이사 재직 시점 cross-check (이사 회계 risk 이력 검증)")
    return actions


async def _reit_default_year(corp_code: str, default_year: int) -> int:
    """리츠의 기본 사업연도 — 이미 제출된 **가장 새 사업보고서**의 표기 연도(결산월 12 규칙의 연도보다 새 것이 있으면 그 연도).

    반기·분기 결산 리츠는 사업보고서가 결산기마다(3·6개월) 나와 결산월 하나로 「최신 사업보고서」를 정하는 `_default_recent_year` 가 틀린다
    (롯데리츠 결산월 12 → 10월 기본 연도 2025 인데 2026.06 사업보고서(제15기)가 9/21 에 이미 제출돼 있다 — 12월 결산이 아닌 회사가 최신 보고서를
    건너뛰던 JTC(#142)와 같은 꼴). DART 목록 1콜. 목록을 못 읽거나 새 것이 없으면 기본 연도 그대로(fail-open).
    """
    try:
        latest = await latest_annual_report_before(corp_code, today_kst().strftime("%Y%m%d"))
    except Exception:  # noqa: BLE001
        return default_year
    m = _ANNUAL_PERIOD.search((latest or {}).get("report_nm") or "")
    return max(default_year, int(m.group(1))) if m else default_year


def _override_fiscal_period(data: dict[str, Any], metrics: dict[str, Any], fiscal_month: int | None) -> None:
    """사업보고서 한 권의 당기가 12개월이 아니면(반기·분기 결산 리츠) `fiscal_period` 를 **그 보고서의 실제 기간**으로 바꾼다.

    결산월로 만든 기본 표기는 「사업연도 = 그 해 12개월」이라 롯데리츠 FY2026(제15기 2026.01~06)을 2026-01-01~2026-12-31 로 적었다.
    12개월 보고서는 손대지 않는다(응답 그대로).
    """
    span = metrics.get("period_span")
    if not span:
        return
    data["fiscal_period"] = {
        "start": span["start"],
        "end": span["end"],
        "fiscal_year_end_month": fiscal_month,
        "label": f"{span['start']}~{span['end']} · {span['months']}개월 결산기",
        "period_months": span["months"],
    }


def _default_recent_year(fiscal_month: int | None = None, ticker: str | None = None) -> int:
    """현재 시점 기준 공시돼 있어야 할 최신 사업보고서의 사업연도 표기(DART `bsns_year`).

    DART 사업보고서는 결산일 90일 이내 제출 의무 — 12월 결산이면 오늘이 4월 이후면 전년, 그 전이면 전전년(종전 규칙 그대로).

    261019: **결산월을 안다**(`report_calendar.latest_annual` — 분기·연간 재무 수집·배수 도구와 같은 규칙). 종전엔 모든 회사에 12월 결산
    규칙을 써서, 12월 결산이 아닌 회사는 이미 낸 최신 사업보고서를 건너뛰고 한 해 앞 것을 요약했다 — 10-19 운영 JTC(2월 결산) 요약이
    2026.02 사업보고서(5/28 제출)를 두고 2025.02(FY2025)였다. DART 는 사업연도 표기를 그 보고서 기간 말이 속한 해로 붙인다(2월 결산
    2026 = 2026.02). 12월 결산이 아닌 외국 상장사(종목코드 9…)는 제출 기한이 길어(사업보고서 120일) 그 기한으로 본다.
    결산월을 모르면(회사 개황 실패) 12월 — 종전과 같다.
    """
    from open_proxy_mcp.services import report_calendar as rcal
    return rcal.latest_annual(today_kst(), rcal.fiscal(fiscal_month or 12, ticker))
