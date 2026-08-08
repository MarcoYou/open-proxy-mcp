"""financial_metrics — DART 재무 4 endpoint 통합 facade.

Phase 1: 6 scope (summary / yearly / quarterly / yoy / qoq / audit_opinion).

한국 표준:
- 연결 (CFS) default — 지배주주 귀속
- 분모 0 / 음수 / None graceful
- 모든 금액 raw KRW int (`_krw` suffix), % float (`_pct`), decimal (`_ratio`)
- render에서만 사람가독 변환 (조/억)
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
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
}

_REPRT_BUSINESS = "11011"  # 사업보고서 (연간)
_QUARTER_REPRT_CODES = ("11013", "11012", "11014", "11011")  # Q1, Q2(반기), Q3, Q4(사업)


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
}

_IS_ACCOUNT_PATTERNS = {
    "revenue": ("매출액", "수익(매출액)", "영업수익"),
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
    "operating_revenue": ("매출액", "수익(매출액)", "영업수익"),
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


def normalize_pct(raw: Any) -> float | None:
    """DART 지표 값 → float (% 형식, 11.5 = 11.5%).

    fnlttSinglIndx의 idx_val은 string. None/공란 graceful.
    """
    if raw is None:
        return None
    s = str(raw).strip().rstrip("%").replace(",", "")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _match_account(account_nm: str, patterns: tuple[str, ...]) -> bool:
    if not account_nm:
        return False
    nm = account_nm.strip().replace(" ", "")
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
def _period_months(reprt_code: str | None, *, cumulative: bool) -> int:
    """누적이면 보고시점까지 개월(1Q=3·반기=6·3Q=9·연간=12), 당기면 분기=3·연간=12."""
    rc = reprt_code or "11011"
    if rc == "11011":  # 사업보고서(연간) — 누적=당기=12
        return 12
    if not cumulative:
        return 3  # 분기/반기/3분기 standalone = 3개월
    return {"11013": 3, "11012": 6, "11014": 9}.get(rc, 12)


def _build_account_map(
    rows: list[dict[str, Any]],
    bs_patterns: dict[str, tuple[str, ...]] = _BS_ACCOUNT_PATTERNS,
    is_patterns: dict[str, tuple[str, ...]] = _IS_ACCOUNT_PATTERNS,
    period: str = "thstrm",
    cumulative_is: bool = False,
) -> dict[str, int | None]:
    """fnlttSinglAcnt rows → 표준 키 매핑 dict (BS + IS).

    같은 키에 여러 행이 매칭되면 첫 매칭만 사용 (DART 응답 순서 = 사업보고서 순서).
    cumulative_is=True: IS는 누적(thstrm_add 우선)으로, BS는 잔액(thstrm)으로 — 분기/반기
    보고서에서 손익을 '당기 3개월'이 아닌 '누적'으로 읽기 위함. BS는 잔액이라 기간 무관.
    """
    out: dict[str, int | None] = {k: None for k in {**bs_patterns, **is_patterns}}
    for row in rows:
        sj_div = _strip(row.get("sj_div"))
        account_nm = _strip(row.get("account_nm"))
        if sj_div == "BS":
            for key, patterns in bs_patterns.items():
                if out[key] is None and _match_account(account_nm, patterns):
                    out[key] = _extract_period_amount(row, period)  # BS는 항상 잔액
                    break
        elif sj_div == "IS":
            for key, patterns in is_patterns.items():
                if out[key] is None and _match_account(account_nm, patterns):
                    out[key] = _extract_cumulative_is(row) if cumulative_is else _extract_period_amount(row, period)
                    break
    return out


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
    """mkt_fundamentals(Postgres)에서 induty 조회 — DART 콜 0. 미설정/장애/미수록이면 None."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=8) as c:
            if stock_code:
                row = c.execute("SELECT induty FROM mkt_fundamentals WHERE isu_cd=%s", (stock_code,)).fetchone()
                if row and row[0]:
                    return str(row[0])
            row = c.execute("SELECT induty FROM mkt_fundamentals WHERE corp_code=%s", (corp_code,)).fetchone()
            return str(row[0]) if row and row[0] else None
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("induty 조회 실패: %s", exc)
        return None


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


def _build_account_map_all(
    rows: list[dict[str, Any]],
    period: str = "thstrm",
    cumulative_is: bool = False,
) -> dict[str, int | None]:
    """fnlttSinglAcntAll rows → 표준 키 매핑 (CF + IS detail + BS detail).

    sj_div: BS / IS / CIS / CF / SCE.
    cumulative_is=True: IS/CIS 손익(cogs·gross_profit 등)을 누적으로 읽는다. BS(잔액)·CF
    (thstrm이 이미 누적)는 그대로 thstrm. 분기/반기 손익을 누적 기준으로 맞추기 위함.
    """
    out: dict[str, int | None] = {}
    out.update({k: None for k in _BS_ACCOUNT_PATTERNS})
    out.update({k: None for k in _IS_ACCOUNT_PATTERNS})
    out.update({k: None for k in _IS_DETAIL_PATTERNS})
    out.update({k: None for k in _CF_ACCOUNT_PATTERNS})

    for row in rows:
        sj_div = _strip(row.get("sj_div"))
        account_nm = _strip(row.get("account_nm"))
        account_id = _strip(row.get("account_id"))
        amount = _extract_period_amount(row, period)  # BS(잔액)·CF(누적 native)용
        is_amount = _extract_cumulative_is(row) if cumulative_is else amount  # IS/CIS 손익용

        if sj_div == "BS":
            # 지배주주 귀속 자본은 account_id로 매칭 — 회사마다 account_nm이 달라(예: "지배기업의
            # 소유주에게…") substring 매칭이 실패하면 전체자본으로 fallback돼 ROE가 틀어짐.
            if out.get("controlling_equity") is None and account_id and \
                    "EquityAttributableToOwnersOfParent" in account_id:
                out["controlling_equity"] = amount
                continue
            for key, patterns in _BS_ACCOUNT_PATTERNS.items():
                if out[key] is None and _match_account(account_nm, patterns):
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

    revenue = bs_is.get("revenue")
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
    _ctrl_equity = detail.get("controlling_equity")
    if _ctrl_equity is None:
        _ctrl_equity = bs_is.get("controlling_equity")
    # 지배지분을 못 구하면 자본총계로 물러나되, 어느 기준을 썼는지 남긴다(별도재무제표는 원래 같다).
    impairment_equity = _ctrl_equity if _ctrl_equity is not None else total_equity
    capital_impairment_basis = "controlling" if _ctrl_equity is not None else "total"
    capital_impairment_ratio_pct = None  # 잠식률 (% — 양수 = 잠식 진행, 음수 = 정상)
    capital_impairment_status = None  # "normal" / "partial" / "partial_50plus" / "full"
    if capital_stock is not None and capital_stock > 0 and impairment_equity is not None:
        ratio = (capital_stock - impairment_equity) / capital_stock * 100
        capital_impairment_ratio_pct = round(ratio, 2)
        if impairment_equity <= 0:
            capital_impairment_status = "full"
        elif ratio >= 50:
            capital_impairment_status = "partial_50plus"
        elif ratio > 0:
            capital_impairment_status = "partial"
        else:
            capital_impairment_status = "normal"

    # 평균값 (BS 전기 데이터 있으면)
    avg_assets = _avg(total_assets, (bs_is_prev or {}).get("total_assets")) if bs_is_prev else total_assets

    # ROE 분모 = 평균 '지배주주 귀속 자본' (FnGuide/한국 표준). 주요계정엔 지배자본이 없어
    # fnlttSinglAcntAll(detail)에서 가져온다. 지배자본 없으면(별도재무·결손) 자본총계로 fallback.
    controlling_equity = detail.get("controlling_equity")
    if controlling_equity is None:
        controlling_equity = bs_is.get("controlling_equity")
    prev_controlling_equity = detail_prev.get("controlling_equity")
    if prev_controlling_equity is None:
        prev_controlling_equity = (bs_is_prev or {}).get("controlling_equity")
    equity_for_roe = controlling_equity if controlling_equity is not None else total_equity
    prev_equity_for_roe = (
        prev_controlling_equity if prev_controlling_equity is not None
        else (bs_is_prev or {}).get("total_equity")
    )
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
    net_income_yoy_pct = _yoy_pct(net_income_controlling, prev_net_income_controlling)

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
    # (총자산은 전체 자본·부채로 조달 → 분자도 전체 순이익이 정합적. FnGuide 동일 규약).
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
    accruals_gap_pct = None
    if operating_profit is not None and cfo is not None and operating_profit != 0:
        accruals_gap_pct = round((operating_profit - cfo) / operating_profit * 100, 2)
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
    bps_krw = None  # Phase 2 — stockTotqySttus 호출 통합 시 채움

    # ── 지배구조 cross-check ──
    # subsidiary_count: 종속회사 수. DART OpenAPI 4 endpoint 어디에도 직접 반환 X.
    # 사업보고서 본문 (XML/PDF) "종속회사 명단" 섹션 파싱 필요 — Phase 2 (3-tier fallback 추가).
    subsidiary_count = None

    return {
        # ── 수익성 ──
        "revenue_krw": revenue,
        "gross_profit_krw": gross_profit,
        "operating_profit_krw": operating_profit,
        "operating_margin_pct": operating_margin_pct,
        "gross_margin_pct": gross_margin_pct,
        "ebitda_krw": ebitda_krw,
        "ebitda_margin_pct": ebitda_margin_pct,
        "net_income_krw": net_income_controlling,  # 지배주주 귀속 (한국 표준)
        "net_income_total_krw": net_income,  # 합계 (참고용)
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
        "ar_to_revenue_pct": ar_to_revenue_pct,
        "inv_to_revenue_pct": inv_to_revenue_pct,
        # ── 배당/유보 ──
        "dividend_paid_krw": dividend_paid_krw,
        "payout_ratio_pct": payout_ratio_pct,
        "dividend_to_fcf_pct": dividend_to_fcf_pct,
        "retained_earnings_krw": retained_earnings,
        # ── NAV/주식 ──
        "nav_krw": nav_krw,
        "bps_krw": bps_krw,  # Phase 2 — None until stockTotqySttus 통합
        "capital_stock_krw": capital_stock,
        # ── 자본잠식 (KOSDAQ 관리/폐지 사유 detect) ──
        "capital_impairment_ratio_pct": capital_impairment_ratio_pct,
        "capital_impairment_status": capital_impairment_status,
        # 어느 자기자본으로 쟀는지 — 규정은 비지배지분 제외이고, 못 구하면 자본총계로 물러난다.
        "capital_impairment_basis": capital_impairment_basis,
        # ── 지배구조 cross-check ──
        "subsidiary_count": subsidiary_count,  # Phase 2 — 사업보고서 본문 파싱 필요
        # ── DART 산출 지표 (보조) ──
        "dart_indx": indx_map,
    }


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
    if acc is not None and abs(acc) > 30:
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
        return [], f"fnlttSinglAcnt({reprt_code}, {fs_div}) 실패: {exc.status} {exc}"


async def _safe_fetch_acnt_all(corp_code: str, year: int, reprt_code: str, fs_div: str) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        data = await client.get_fnltt_singl_acnt_all(corp_code, str(year), reprt_code, fs_div)
        return data.get("list", []) or [], None
    except DartClientError as exc:
        if exc.status == "013":
            return [], "no_filing"
        return [], f"fnlttSinglAcntAll({reprt_code}, {fs_div}) 실패: {exc.status} {exc}"


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
    return {
        "rcept_no": top.get("rcept_no", ""),
        "rcept_dt": (top.get("rcept_dt") or "").strip(),
        "report_nm": (top.get("report_nm") or "").strip(),
    }


async def _safe_fetch_indx(corp_code: str, year: int, reprt_code: str) -> dict[str, float | None]:
    """4개 idx_cl_code 모두 호출 → 통합 dict (idx_nm: idx_val)."""
    client = get_dart_client()
    out: dict[str, float | None] = {}
    for cl_code in ("M210000", "M220000", "M230000", "M240000"):
        try:
            data = await client.get_fnltt_singl_indx(corp_code, str(year), reprt_code, cl_code)
            for row in (data.get("list") or []):
                key = _strip(row.get("idx_nm"))
                if key and key not in out:
                    out[key] = normalize_pct(row.get("idx_val"))
        except DartClientError:
            continue
    return out


async def _safe_fetch_audit(corp_code: str, year: int) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        data = await client.get_audit_opinion(corp_code, str(year), "11011")
        rows = data.get("list", []) or []
        return rows, None
    except DartClientError as exc:
        if exc.status == "013":
            return [], "no_filing"
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

async def _fetch_acnt_with_fallback(
    corp_code: str,
    year: int,
    fs_div: str,
) -> tuple[list[dict[str, Any]], str, str | None]:
    """사업보고서(11011) → 3분기(11014) → 반기(11012) → 1분기(11013) 순서로 fallback.

    가장 최근에 가용한 분기 보고서를 사용 (사업보고서 미공시 시).

    return (rows, used_reprt_code, warning_or_None)
    """
    fallback_order = ("11011", "11014", "11012", "11013")
    last_err = None
    for rc in fallback_order:
        rows, err = await _safe_fetch_acnt(corp_code, year, rc, fs_div)
        if rows:
            return rows, rc, None
        if err == "no_filing":
            last_err = f"{year}년 reprt_code={rc} no_filing"
            continue
        if err:
            last_err = err
    return [], "11011", last_err or f"{year}년 모든 reprt_code (사업/반기/분기) 미공시"


async def _fetch_year_metrics(
    corp_code: str,
    year: int,
    fs_div: str,
    *,
    include_prev: bool = True,
    allow_quarterly_fallback: bool = True,
    induty_code: str | None = None,
    is_reit: bool = False,
) -> tuple[dict[str, Any], list[str], int]:
    """단일 사업연도 metrics. 당기+전기 fnlttSinglAcnt를 모두 호출.

    Phase 1 v2 최적화 (iteration 11):
    - fnlttSinglIndx 호출 제거 (DART 산출 지표는 자체 계산값 우선이라 사실상 미사용).
    - 당기/전기 × acnt/acntAll = 4 호출을 asyncio.gather로 병렬화.
    - 결과: 8 sequential → (1 sequential fallback + 3 parallel) — 평균 응답 시간 ~3-4배 단축.

    allow_quarterly_fallback=True (default): 사업보고서 미공시 시 분기/반기 보고서로 fallback.
    return (metrics, warnings, evidence_count)
    """
    warnings: list[str] = []
    # 1단계: 당기 fnlttSinglAcnt — fallback 발생 가능성 있어 sequential 유지.
    if allow_quarterly_fallback:
        rows_curr, used_rc, fb_err = await _fetch_acnt_with_fallback(corp_code, year, fs_div)
        if not rows_curr:
            return {}, [fb_err or f"{year}년 데이터 미공시"], 0
        if used_rc != _REPRT_BUSINESS:
            warnings.append(f"{year}년 사업보고서 미공시 — reprt_code={used_rc}로 대체 (반기/분기)")
    else:
        rows_curr, err_curr = await _safe_fetch_acnt(corp_code, year, _REPRT_BUSINESS, fs_div)
        if err_curr == "no_filing":
            return {}, [f"{year}년 사업보고서 미공시 (fnlttSinglAcnt no_filing)"], 0
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
    if include_prev:
        tasks.append(_safe_fetch_acnt(corp_code, year - 1, used_rc, fs_div))
        task_keys.append("prev_acnt")
    tasks.append(_safe_fetch_acnt_all(corp_code, year, used_rc, fs_div))
    task_keys.append("curr_acnt_all")
    if include_prev:
        tasks.append(_safe_fetch_acnt_all(corp_code, year - 1, used_rc, fs_div))
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

    # ── PRIMARY = 누적(period-to-date) basis ──
    # 분기/반기 보고서면 손익을 누적(thstrm_add)으로 읽어 CF(누적)와 기간을 맞춘다.
    # 사업보고서·1분기는 누적=당기라 동일. BS(잔액)는 기간 무관.
    bs_is = _build_account_map(rows_curr, cumulative_is=True) if rows_curr else {}
    bs_is_prev = _build_account_map(rows_prev, cumulative_is=True) if rows_prev else None
    detail = _build_account_map_all(rows_detail, cumulative_is=True) if rows_detail else None
    detail_prev = _build_account_map_all(rows_detail_prev, cumulative_is=True) if rows_detail_prev else None

    if not bs_is:
        return {}, warnings + [f"{year}년 BS/IS 핵심 데이터 파싱 실패"], 0

    # ── TTM(최근 4분기) 회전일수 분모 — 분기/반기면 직전 FY 1건 추가 fetch ──
    # TTM = 직전 FY + 당기 YTD(누적) − 전년 YTD(누적). AR/재고 평균은 이미 (당기말+전년동기말)/2라
    # TTM 정합. 단일분기 연환산 왜곡(AR 3배인데 DSO 하락) 제거. 실패 시 기간일치로 fallback.
    ttm_revenue = ttm_cogs = None
    if used_rc != _REPRT_BUSINESS:
        fy_rows_all, _fy_err = await _safe_fetch_acnt_all(corp_code, year - 1, _REPRT_BUSINESS, actual_fs)
        fy_map = _build_account_map_all(fy_rows_all) if fy_rows_all else {}
        ytd_rev_c, ytd_rev_p = (bs_is or {}).get("revenue"), (bs_is_prev or {}).get("revenue")
        if None not in (fy_map.get("revenue"), ytd_rev_c, ytd_rev_p):
            ttm_revenue = fy_map["revenue"] + ytd_rev_c - ytd_rev_p
        ytd_cogs_c, ytd_cogs_p = (detail or {}).get("cogs"), (detail_prev or {}).get("cogs")
        if None not in (fy_map.get("cogs"), ytd_cogs_c, ytd_cogs_p):
            ttm_cogs = fy_map["cogs"] + ytd_cogs_c - ytd_cogs_p
        if ttm_revenue is None:
            warnings.append(f"{year}년 TTM 회전일수 분모 계산 실패(직전 FY 미조회) — 분기 기간일치로 대체.")

    pm_cum = _period_months(used_rc, cumulative=True)
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
    metrics["fs_div"] = actual_fs  # 요청값이 아니라 실제 사용된 기준 (CFS 미작성 시 OFS)
    metrics["reprt_code"] = used_rc
    metrics["period_basis"] = "annual" if used_rc == _REPRT_BUSINESS else f"cumulative_{pm_cum}m"
    # 배당성향은 귀속 기준(DART 사업보고서 '현금배당성향')으로 주입 — 연간보고서에서만 의미 있음.
    # 분기/누적 기간엔 해당 연도 배당이 미확정이라 None(정직). CF dividend_paid_krw는 배당/FCF 전용.
    if used_rc == _REPRT_BUSINESS:
        metrics["payout_ratio_pct"] = await _accrual_payout_pct(corp_code, year, is_reit=is_reit)
        if is_reit:
            metrics["payout_ratio_note"] = "REIT — 순이익 기준 배당성향 미표시(배당가능이익 분배). DPS·시가배당률 참고"
    else:
        metrics["payout_ratio_pct"] = None
    if pm_cum < 12:
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
            bs_is_std = _build_account_map(rows_curr)  # thstrm: IS=당기 3개월, BS=잔액
            bs_is_prev_std = _build_account_map(rows_prev) if rows_prev else None
            detail_std = _build_account_map_all(rows_detail) if rows_detail else None
            prior_detail = _build_account_map_all(prior_detail_rows)
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
                detail_prev=detail_prev,  # NWC YoY는 잔액 기반이라 기간 무관 — 누적 detail_prev 재사용 OK
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
    _tb = {"ttm": "TTM(최근 4분기)", "annual": "연간", "period_matched": "기간보정(분기)"}.get(
        metrics.get("turnover_basis"), metrics.get("turnover_basis"))
    if used_rc == _REPRT_BUSINESS:
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


async def _build_yearly(corp_code: str, end_year: int, years: int, fs_div: str, induty_code: str | None = None, is_reit: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    year_list = list(range(end_year - years + 1, end_year + 1))
    tasks = [_fetch_year_metrics(corp_code, y, fs_div, include_prev=True, induty_code=induty_code, is_reit=is_reit) for y in year_list]
    results = await asyncio.gather(*tasks)
    out: list[dict[str, Any]] = []
    warnings: list[str] = []
    for metrics, ws, _ev in results:
        warnings.extend(ws)
        if metrics:
            out.append(metrics)
    return out, warnings


_QUARTERLY_IS_KEYS = ("revenue", "operating_profit", "net_income")


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


async def _build_quarterly(corp_code: str, end_year: int, fs_div: str, num_quarters: int = 12) -> tuple[list[dict[str, Any]], list[str]]:
    """4Q × 3년 = 12분기 standalone 손익. fnlttSinglAcnt + reprt_code 4개 × 3년 = 12 호출.

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
    years = list(range(end_year - 2, end_year + 1))
    quarter_labels = {
        "11013": "Q1",
        "11012": "Q2",
        "11014": "Q3",
        "11011": "Q4",
    }
    tasks = []
    keys = []
    for y in years:
        for rc, label in quarter_labels.items():
            tasks.append(_safe_fetch_acnt(corp_code, y, rc, fs_div))
            keys.append((y, rc, label))
    results = await asyncio.gather(*tasks)

    cum9_by_year: dict[int, dict[str, int | None]] = {}  # Q3 보고서의 9개월 누적 (Q4 차분용)
    q_standalone_by_year: dict[int, dict[str, dict[str, int | None]]] = {}
    fs_seen: set[str] = set()  # 실제 사용된 fs_div 추적 (CFS/OFS 폴백·혼재 감지)
    for (year, rc, label), (rows, err) in zip(keys, results):
        if err == "no_filing":
            continue
        if err:
            warnings.append(err)
            continue
        if not rows:
            continue
        actual = _actual_fs_div(rows)
        if actual:
            fs_seen.add(actual)
        bs_is = _build_account_map(rows)
        if not bs_is:
            continue
        if rc == "11014":
            cum9_by_year[year] = _build_account_map(rows, period="thstrm_add")
        if rc != "11011":
            q_standalone_by_year.setdefault(year, {})[label] = bs_is
        out.append({
            "year": year,
            "quarter": label,
            "reprt_code": rc,
            "revenue_krw": bs_is.get("revenue"),
            "operating_profit_krw": bs_is.get("operating_profit"),
            "net_income_krw": bs_is.get("net_income"),
            "total_assets_krw": bs_is.get("total_assets"),
            "total_equity_krw": bs_is.get("total_equity"),
            "total_liabilities_krw": bs_is.get("total_liabilities"),
        })

    # CFS 요청인데 일부/전부 분기가 OFS면 경고 (조용한 폴백·분기 간 기준 혼재 방지).
    if fs_div == "CFS" and "OFS" in fs_seen:
        if "CFS" in fs_seen:
            warnings.append("일부 분기에 연결재무제표(CFS)가 없어 별도(OFS)로 대체됨 — 분기 간 기준 혼재 주의.")
        else:
            warnings.append("연결재무제표(CFS) 미작성 — 별도(OFS) 기준 분기 산출. 연결 기준 수치 아님.")

    # Q4 손익 차분: 연간 − 9개월 누적(Q3 add 우선, 없으면 Q1+Q2+Q3 standalone 합)
    for row in out:
        if row["quarter"] != "Q4":
            row["basis"] = "standalone_3m"
            continue
        year = row["year"]
        cum9 = cum9_by_year.get(year, {})
        qs = q_standalone_by_year.get(year, {})
        failed_keys = []
        derived_count = 0
        for key in _QUARTERLY_IS_KEYS:
            annual = row[f"{key}_krw"]
            if annual is None:
                continue  # 계정 자체 부재 (금융사 매출액 등) — 차분 실패가 아님
            nine = cum9.get(key)
            if nine is None:
                parts = [qs.get(q, {}).get(key) for q in ("Q1", "Q2", "Q3")]
                nine = sum(parts) if all(p is not None for p in parts) else None
            if nine is not None:
                row[f"{key}_krw"] = annual - nine
                row[f"annual_{key}_krw"] = annual
                derived_count += 1
            else:
                failed_keys.append(key)
        if failed_keys:
            row["basis"] = "annual_cumulative"
            warnings.append(
                f"{year}-Q4 {'/'.join(failed_keys)}은 연간 누적치 — 분기 보고서 결측으로 standalone 차분 불가. QoQ 해석 주의."
            )
        else:
            row["basis"] = "standalone_3m_derived"  # 연간 − 3분기 누적 차분

    for row in out:
        row["operating_margin_pct"] = _safe_pct(row.get("operating_profit_krw"), row.get("revenue_krw"))
        row["net_profit_margin_pct"] = _safe_pct(row.get("net_income_krw"), row.get("revenue_krw"))

    out.sort(key=lambda x: (x["year"], list(quarter_labels.values()).index(x["quarter"])))

    # 정합성 점검: 4분기 standalone 합 ≠ 연간이면 기중 연결범위 변동·재작성 가능성
    # (한화에어로스페이스 2024 실측 — 인적분할로 Q1·Q2 당시 보고치와 연간 재작성치 불일치).
    # Q4 = 연간 − Q3누적이라 Q4 자체는 최신 기준 정확. 차이는 Q1~Q3가 당시 보고 기준인 탓.
    _GAP_LABELS = {"revenue": "매출", "operating_profit": "영업이익", "net_income": "순이익"}
    for y in sorted({r["year"] for r in out}):
        yr_rows = [r for r in out if r["year"] == y]
        q4 = next((r for r in yr_rows if r["quarter"] == "Q4"), None)
        if not q4 or len(yr_rows) != 4:
            continue
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
    index = {(r["year"], r["quarter"]): r for r in out}
    for i, row in enumerate(out):
        prev_q = out[i - 1] if i > 0 else None
        prev_y = index.get((row["year"] - 1, row["quarter"]))
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
    return out[-num_quarters:], warnings


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
import time as _time_mod
_FM_CACHE: dict[tuple, tuple[float, dict[str, Any]]] = {}
_FM_CACHE_TTL = 300.0  # 5분


def _fm_cache_get(key: tuple) -> dict[str, Any] | None:
    entry = _FM_CACHE.get(key)
    if not entry:
        return None
    ts, payload = entry
    if _time_mod.time() - ts > _FM_CACHE_TTL:
        _FM_CACHE.pop(key, None)
        return None
    return payload


def _fm_cache_set(key: tuple, payload: dict[str, Any]) -> None:
    _FM_CACHE[key] = (_time_mod.time(), payload)


async def build_financial_metrics_payload(
    company_query: str,
    *,
    scope: str = "summary",
    year: int | None = None,
    years: int = 3,
    consolidated: bool = True,
) -> dict[str, Any]:
    total_started_at = time.perf_counter()
    timings_ms: dict[str, int] = {}

    def _mark(stage: str, started_at: float) -> None:
        timings_ms[stage] = int((time.perf_counter() - started_at) * 1000)

    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    # F2 cache check
    cache_key = (company_query, scope, year, years, consolidated)
    cached = _fm_cache_get(cache_key)
    if cached is not None:
        return cached

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
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
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
    # 금융사 판별 2차 신호(KSIC 업종) — mkt_fundamentals에서 DART 콜 없이 조회. 수신 없어(예수부채 無)
    # BS신호가 놓치는 카드·캐피탈·VC(삼성카드·미래에셋벤처투자 등) 보완. DB 미설정/미수록이면 None(무해).
    induty_code = await asyncio.to_thread(_lookup_induty_code, corp_code, selected.get("stock_code", ""))
    # REIT는 순이익 기준 배당성향이 무의미(배당가능이익 분배) → 이름 기반 판정해 성향 억제 + 안내.
    is_reit = _is_reit(selected.get("corp_name", ""))
    # 분기 인지형 디폴트: quarterly/qoq는 이미 제출된 최신 분기(예: 당해 1분기는 5월 공시)를
    # 봐야 하므로 디폴트 end_year를 당해 연도로. _build_quarterly는 end_year-2..end_year를
    # 받고 미공시 분기는 graceful skip이라 호출 수(12) 변동 없이 최신 분기가 포함된다.
    # summary/yearly/yoy는 연간(사업보고서) 기준이라 _default_recent_year(전년) 유지.
    if year:
        target_year = year
    elif scope in ("quarterly", "qoq"):
        from datetime import date as _date
        target_year = _date.today().year
    else:
        target_year = _default_recent_year()

    warnings: list[str] = []
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
        "available_scopes": sorted(_SUPPORTED_SCOPES),
    }

    parsing_failures = 0
    filing_count = 0

    stage_started_at = time.perf_counter()
    if scope == "summary":
        metrics, ws, ev_count = await _fetch_year_metrics(corp_code, target_year, fs_div, include_prev=True, induty_code=induty_code, is_reit=is_reit)
        warnings.extend(ws)
        if metrics:
            data["summary"] = metrics
            filing_count = 1
            ref = await _periodic_filing_ref(corp_code, target_year, metrics.get("reprt_code"))
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
        rows, ws = await _build_yearly(corp_code, target_year, years, fs_div, induty_code, is_reit=is_reit)
        warnings.extend(ws)
        data["yearly"] = rows
        filing_count = len(rows)
        if rows:
            ref = await _periodic_filing_ref(corp_code, rows[-1]["year"], "11011")
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
        rows, ws = await _build_quarterly(corp_code, target_year, fs_div)
        warnings.extend(ws)
        data["quarterly"] = rows
        filing_count = len(rows)
        if rows:
            ref = await _periodic_filing_ref(corp_code, rows[-1]["year"], rows[-1].get("reprt_code"))
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
        curr_t, prev_t, audit_t = await asyncio.gather(
            _fetch_year_metrics(corp_code, target_year, fs_div, include_prev=True, induty_code=induty_code),
            _fetch_year_metrics(corp_code, target_year - 1, fs_div, include_prev=True, induty_code=induty_code),
            _build_audit_opinion_data(corp_code, target_year, years_back=2),
        )
        curr, ws_curr, _ev1 = curr_t
        prev, ws_prev, _ev2 = prev_t
        audit_data, audit_ws, _audit_ev = audit_t
        warnings.extend(ws_curr)
        warnings.extend(ws_prev)
        warnings.extend(audit_ws)
        audit_curr = audit_data.get("opinions", [{}])[0] if audit_data.get("opinions") else None
        audit_prev = audit_data.get("opinions", [{}, {}])[1] if len(audit_data.get("opinions", [])) >= 2 else None

        signals = _detect_yoy_signals(curr or {}, prev or {}, audit_curr, audit_prev) if curr else []
        data["yoy"] = {
            "current": curr,
            "prior": prev,
            "alerts": signals,
            "audit_opinion": {
                "current": audit_curr,
                "prior": audit_prev,
            },
        }
        filing_count = (1 if curr else 0) + (1 if prev else 0)
        if curr:
            ref = await _periodic_filing_ref(corp_code, target_year, curr.get("reprt_code"))
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
        rows, ws = await _build_quarterly(corp_code, target_year, fs_div, num_quarters=4)
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
            evidence_refs.append(EvidenceRef(
                evidence_id=f"ev_fm_qoq_{corp_code}_{target_year}",
                source_type=SourceType.DART_API,
                rcept_no=(ref or {}).get("rcept_no", ""),
                rcept_dt=(ref or {}).get("rcept_dt", ""),
                report_nm=(ref or {}).get("report_nm", ""),
                section=f"전분기 대비 (최근 {len(rows)}분기, 최신: {rows[-1]['year']}-{rows[-1]['quarter']})",
                note=f"{selected.get('corp_name', '')} 전분기 비교",
            ))

    elif scope == "audit_opinion":
        audit_data, audit_ws, audit_ev = await _build_audit_opinion_data(corp_code, target_year, years_back=years)
        warnings.extend(audit_ws)
        data["audit_opinion"] = audit_data
        filing_count = audit_data.get("summary", {}).get("history_years", 0)
        evidence_refs.extend(audit_ev)
    _mark(f"scope.{scope}", stage_started_at)

    filing_meta = build_filing_meta(filing_count=filing_count, parsing_failures=parsing_failures)
    if filing_meta["no_filing"]:
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
    _fm_cache_set(cache_key, payload)  # F2 — TTL 5분 cache
    return payload


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


def _default_recent_year() -> int:
    """현재 시점 기준 가장 최근 완료된 사업연도.

    DART 사업보고서는 결산일 90일 이내 (3월말까지) 제출 의무.
    오늘이 4월 이후면 전년도 사업보고서 가용, 그 전이면 전전년.
    """
    from datetime import date
    today = date.today()
    if today.month >= 4:
        return today.year - 1
    return today.year - 2
