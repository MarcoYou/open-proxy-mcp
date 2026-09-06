"""매출(top line) 계정 선택 — DART 재무제표 행에서 「이 회사의 매출은 어느 행인가」를 고른다.

왜 따로 있나 (260906 실측, 리파인·티움바이오·삼성생명·KB금융·현대건설·롯데렌탈):
- 「매출액」이라는 계정명을 안 쓰는 회사가 많다. 정보서비스(리파인)·바이오(티움바이오)는
  「영업수익」, 보험은 「보험수익」·「보험서비스수익」(1117호)·「보험영업수익」(1104호), 건설은
  「수익(매출액)」·「공사수익」, 은행·지주는 매출 행 자체가 없고 「이자수익」만 있다.
- 주요계정(fnlttSinglAcnt) 템플릿에는 그 회사가 「매출액」을 안 쓰면 **행이 아예 없다**
  (리파인 2024: IS 가 영업이익부터 시작). 전체 재무제표(fnlttSinglAcntAll)에는 있지만
  account_id 가 `-표준계정코드 미사용-` 이라 코드로는 못 잡는다.
- 부분문자열 매칭은 「기타영업수익」·「출재보험서비스수익」·「렌탈 및 기타수익」에 올라탄다. DART 응답은
  ord 순서도 아니어서(리파인 2023 「영업수익」이 ord 11 로 영업이익 뒤) 첫 매칭에 기댈 수 없다.

원칙:
1. **account_id 정확매칭이 1순위** — `ifrs-full_Revenue` 는 업종 불문 그 회사의 매출이다.
   (총차입금과 같은 교훈: substring 금지, 전체명 정확매칭. wiki financial_metrics 「총차입금」 절)
2. 코드가 없을 때만 계정명. **접두 매칭 + 배제 목록**(provisional_financial_statement 와 같은 방식).
3. **업종(KSIC)은 게이트가 아니라 우선순위**다 — 어느 이름을 먼저 볼지만 정한다. 어휘 전체는
   항상 검사하니 업종이 틀리거나 없어도 「매출액」이 있는 회사는 잡힌다.
   - 65(보험): 보험수익 계열 → 영업수익
   - 64·66(은행·여신·증권·금융지주): 영업수익 → 순영업수익 → 이자수익
   - 41·42(건설): 매출액 계열 → 공사·분양수익 → 영업수익
   - 그 외: 매출액 계열 → 영업수익. **이자수익·보험수익으로는 안 내려간다** — 제조업의
     이자수익은 금융수익이지 매출이 아니다.
4. 무엇을 골랐는지 항상 돌려준다(`account_nm`·`account_id`·`standard`). 「보험수익 기준」인지
   「매출액 기준」인지는 읽는 쪽이 봐야 한다 — 값만 채우고 출처를 지우지 않는다.

이 모듈은 금액을 읽지 않는다. 행만 고르고, 당기/누적/전기 중 무엇을 읽을지는 호출자의 규칙이다
(financial_metrics 는 분기보고서에서 thstrm_add 를 누적으로 읽는다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

__all__ = ["RevenuePick", "pick_revenue_row", "is_standard_revenue_label", "revenue_family", "match_revenue_label", "FAMILY_KR"]

#: 업종 불문 매출로 인정하는 표준 코드. 순서 = 우선순위.
_REVENUE_IDS_ANY = ("ifrs-full_Revenue",)

#: 보험 top line 코드. IFRS 1117호 「보험수익」(InsuranceRevenue)이 표준이고,
#: `dart_OperatingIncomeInsurance` 는 재보험수익까지 더한 DART 집계다
#: (KB금융 2024: 11.456조 = 보험수익 11.017조 + 재보험수익 0.439조. 삼성생명도 같은 구조).
_REVENUE_IDS_INSURANCE = ("ifrs-full_InsuranceRevenue", "dart_OperatingIncomeInsurance")

#: 은행·여신 최후 폴백 — 총이자수익. 영업수익 행이 없는 은행지주(KB금융)에서만 쓴다.
_REVENUE_IDS_INTEREST = ("ifrs-full_RevenueFromInterest",)

#: 계정명 후보 — **접두** 매칭. 공백·항목번호를 뗀 뒤 비교한다.
_NAMES_SALES = ("매출액", "수익(매출액)", "영업수익(매출액)", "매출")
_NAMES_OPERATING = ("영업수익",)
_NAMES_INSURANCE = ("보험수익", "보험서비스수익", "보험영업수익")
_NAMES_CONSTRUCTION = ("공사수익", "공사매출", "분양수익", "분양매출")
_NAMES_BANK_NET = ("순영업수익",)
_NAMES_INTEREST = ("이자수익",)

#: 접두 매칭이라도 올라타는 것들. 이익형만 적으면 적자 회사의 손실형이 새므로 같이 적는다
#: (영풍 「매출총손실」 — provisional_financial_statement 교훈).
_EXCLUDE_PREFIX = (
    "매출원가", "매출총이익", "매출총손실", "매출총손익",
    "매출채권", "매출할인", "매출에누리", "매출환입", "매출액또는",
    "영업수익원가", "보험영업비용", "보험서비스비용",
)

#: 정확 일치로만 받는 이름 — 접두로 열면 「이자수익」이 「이자수익(유효이자율)」 세부행에,
#: 「수익」이 온갖 세부 수익에 걸린다.
_EXACT_ONLY = {"이자수익", "수익"}

# 항목번호 제거 — provisional_financial_statement._strip_item_marker 와 같은 규칙.
# 구분자를 필수로 둔다: 문자만 보고 떼면 「자산총계」에서 「자」가 떨어진다.
_ITEM_MARKER = re.compile(
    r"^(?:"
    r"[\(（][^)）]{1,4}[\)）]"                                  # (1) (가) (Ⅰ)
    r"|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVXivxl0-9가-힣]{1,4}\s*[.．:：]"          # Ⅰ. 1. l. 가.
    r"|[\-–—·ㆍ]"                                              # - 영업수익
    r")\s*"
)

_NO_CODE = "-표준계정코드 미사용-"

#: family → 사람이 읽는 기준 이름. 원문 계정명은 회사마다 달라(삼성생명은 IFRS17 보험수익 행을
#: 「일반보험서비스수익」이라 적는다) 라벨로 쓰면 부문 소계처럼 읽힌다 — 값·원문명은 그대로 두고
#: 표기만 표준 개념으로 한다(260906 결정).
FAMILY_KR = {"sales": "매출액", "operating": "영업수익", "insurance": "보험수익",
             "construction": "공사수익", "bank_net": "순영업수익", "interest": "이자수익"}


@dataclass(frozen=True)
class RevenuePick:
    row: dict[str, Any]
    account_nm: str            # DART 원문 계정명 (공백만 정리)
    account_id: str | None     # 표준코드. `-표준계정코드 미사용-` 이면 None
    method: str                # "account_id" | "account_nm"
    family: str                # "sales" | "operating" | "insurance" | "construction" | "bank_net" | "interest"

    @property
    def standard(self) -> bool:
        """「매출액」과 같은 뜻으로 읽어도 되는가. 보험수익·이자수익 기준이면 False."""
        return self.family in ("sales", "operating", "construction")

    @property
    def basis(self) -> str:
        """사람용 기준 라벨 — 원문 계정명(「일반보험서비스수익」 등)이 아니라 표준 개념명(「보험수익」)."""
        return FAMILY_KR[self.family]


def revenue_family(induty_code: str | None) -> str:
    """KSIC → 우선순위 묶음. 판별이 아니라 **순서**를 고르는 용도다."""
    ind = str(induty_code or "").strip()
    two = ind[:2]
    if two == "65":
        return "insurance"
    if two in ("64", "66"):
        return "finance"
    if two in ("41", "42"):
        return "construction"
    return "general"


def is_standard_revenue_label(account_nm: str | None) -> bool:
    nm = _clean(account_nm or "")
    return any(nm.startswith(p) for p in _NAMES_SALES + _NAMES_OPERATING)


def _clean(s: str) -> str:
    s = re.sub(r"\s+", "", s or "")
    return _ITEM_MARKER.sub("", s)


def _name_matches(clean: str, patterns: Iterable[str]) -> bool:
    if not clean or clean.startswith(_EXCLUDE_PREFIX):
        return False
    for p in patterns:
        p = p.replace(" ", "")
        if p in _EXACT_ONLY:
            if clean == p:
                return True
        elif clean.startswith(p):
            return True
    return False


def _ord(row: dict[str, Any]) -> int:
    try:
        return int(str(row.get("ord") or "").strip())
    except ValueError:
        return 10**9


def _priority(family: str) -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    """(family 라벨, method, ids, names) 순서. 앞이 이긴다."""
    sales_id = ("sales", "account_id", _REVENUE_IDS_ANY, ())
    sales_nm = ("sales", "account_nm", (), _NAMES_SALES)
    oper_nm = ("operating", "account_nm", (), _NAMES_OPERATING)
    ins_id = ("insurance", "account_id", _REVENUE_IDS_INSURANCE, ())
    ins_nm = ("insurance", "account_nm", (), _NAMES_INSURANCE)
    con_nm = ("construction", "account_nm", (), _NAMES_CONSTRUCTION)
    bank_nm = ("bank_net", "account_nm", (), _NAMES_BANK_NET)
    int_id = ("interest", "account_id", _REVENUE_IDS_INTEREST, ())
    int_nm = ("interest", "account_nm", (), _NAMES_INTEREST)
    if family == "insurance":
        return [sales_id, ins_id, ins_nm, sales_nm, oper_nm]
    if family == "finance":
        return [sales_id, sales_nm, oper_nm, bank_nm, int_id, int_nm]
    if family == "construction":
        return [sales_id, sales_nm, con_nm, oper_nm]
    return [sales_id, sales_nm, oper_nm]


def pick_revenue_row(rows: Iterable[dict[str, Any]], induty_code: str | None = None) -> RevenuePick | None:
    """손익계산서 행들에서 매출 행 하나를 고른다. 없으면 None (0 이나 미상으로 채우지 않는다).

    같은 우선순위에 여러 행이면 DART `ord` 가 작은 것(재무제표에서 위에 있는 것). 응답 순서는
    ord 순이 아니므로 순서에 기대지 않는다. 금액이 없는 행(빈 문자열)은 건너뛴다.
    """
    cands: list[dict[str, Any]] = []
    for r in rows:
        if str(r.get("sj_div") or "").strip().upper() not in ("IS", "CIS"):
            continue
        amt = str(r.get("thstrm_amount") if r.get("thstrm_amount") is not None else "").strip()
        if amt in ("", "-"):
            continue
        cands.append(r)
    if not cands:
        return None
    cands.sort(key=_ord)

    def _nm(r: dict[str, Any]) -> str:
        return re.sub(r"\s+", " ", str(r.get("account_nm") or "")).strip()

    def _aid(r: dict[str, Any]) -> str:
        return str(r.get("account_id") or "").strip()

    for fam, method, ids, names in _priority(revenue_family(induty_code)):
        if method == "account_id":
            # 코드 우선순위가 ord 보다 앞선다 — KB금융은 dart_OperatingIncomeInsurance(재보험 포함,
            # ord 12)가 ifrs-full_InsuranceRevenue(ord 14)보다 위에 온다.
            for wanted in ids:
                for r in cands:
                    if _aid(r) == wanted:
                        return RevenuePick(r, _nm(r), wanted, "account_id", fam)
        else:
            for r in cands:
                if _name_matches(_clean(_nm(r)), names):
                    aid = _aid(r)
                    return RevenuePick(r, _nm(r), None if aid in ("", _NO_CODE) else aid, "account_nm", fam)
    return None


def match_revenue_label(label: str | None) -> str | None:
    """짧은 표 라벨(잠정실적·손익구조 변동 서식) 하나가 매출인가 — family 를 돌려주고 아니면 None.

    `pick_revenue_row` 와 같은 어휘·배제 규칙이지만 **업종 없이** 쓴다: 잠정실적 표는 회사가 top line
    한 줄만 적으므로(전체 재무제표처럼 이자수익·보험수익이 나란히 있지 않다) 보험수익·순영업수익도
    그 자체로 매출이다. 이자수익은 여기서도 받지 않는다.
    """
    clean = _clean(label or "")
    for fam, names in (("sales", _NAMES_SALES), ("operating", _NAMES_OPERATING),
                       ("insurance", _NAMES_INSURANCE), ("construction", _NAMES_CONSTRUCTION),
                       ("bank_net", _NAMES_BANK_NET)):
        if _name_matches(clean, names):
            return fam
    return None
