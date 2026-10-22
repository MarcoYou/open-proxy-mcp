"""지배 귀속 순이익 줄 고르기(값 구조 검사) — 분기·연간 수집과 서버 실시간 경로가 함께 쓰는 한 규칙(261020 p1e firm-ctrl-ni).

DART 전체 재무제표(fnlttSinglAcntAll) 응답 행에서 **지배주주 귀속 당기순이익**을 고른다. 규칙은 분기 수집(`scripts/market_fund_quarterly.py`)에서
#152 로 다듬은 것 그대로다(아래 261020 주석) — 후보 줄마다 값 구조(지배 몫 + 비지배 몫 = 당기순이익)를 보고, 총포괄이익의 지배 몫·주당이익·
공동지배기업 손익·계속영업 몫만 같은 줄은 고르지 않는다.

왜 한 곳에(261020 p1e firm-ctrl-ni): 같은 계정 고르기를 네 곳이 따로 했다 — 분기 수집(`extract_controlling_ni_cum`), 연간 수집
(`market_val_series.ctrl_ni` — 지배 계정 줄 → 없으면 총계), 서버 실시간 firm(`price_multiple_data._ctrl_ni` — 지배 계정 줄 → 총계 − 비지배 계정),
재무 지표(`financial_metrics` — 지배 계정 줄 첫 줄 → 없으면 주요계정 당기순이익 총계), 결산기 칸(`period_cells.ctrl_ni`). #152 가 배치만 고쳐서
운영 075180 firm 응답이 net_income_fy0 27.68억(총포괄 지배 몫)·per_fy0 19.84 인데 같은 응답 경고는 「적자(FY0 EPS≤0) — PER N/M」이었고,
TTM 도 DB 경로(102.6억)와 달랐다(270.6억). 이제 모두 이 모듈의 `controlling_ni` 를 부른다.

값 칸: `controlling_ni(rows)`(field 없음) = 손익 누적 — 줄마다 thstrm_add_amount(당기 누적)가 있으면 그것, 없으면 thstrm_amount(1분기·사업보고서는
이게 누적). `controlling_ni(rows, "frmtrm_amount")` 처럼 칸 이름을 주면 그 칸만 본다(사업보고서의 전기·전전기 칸, 분기보고서의 3개월 칸 등).
"""
from __future__ import annotations

import re

from open_proxy_mcp.services.financial_metrics import normalize_amount

# 지배주주 귀속 추출(260705 QA 후 정정 · 261020 값 구조 검사는 `_ctrl_in_sj` 위): account_id **substring**으로 net income 귀속만 —
# "ProfitLossAttributableToOwnersOfParent"는 총포괄 "ComprehensiveIncomeAttributable…"와 문자열이
# 구분되므로 substring이 안전(dart_* 접두 변형도 포착). account_id가 아예 없는(-표준계정코드 미사용-)
# 종목(LG그룹 등 다수)은 account_nm 폴백 — 단 **IS/BS만**(CIS 총포괄 제외, 값이 달라 오염). 총계
# (ifrs-full_ProfitLoss·Equity) 폴백은 **금지**(비지배 포함 총계를 지배로 오저장 — QA 실측 LG화학).
_NI_CTRL_ID = "ProfitLossAttributableToOwnersOfParent"


def _cum_is(r):
    """IS 누적: thstrm_add(당기누적) 우선, 없으면 thstrm(1Q·사업보고서는 이게 누적)."""
    v = normalize_amount(r.get("thstrm_add_amount"))
    return float(v) if v is not None else (
        float(normalize_amount(r.get("thstrm_amount"))) if normalize_amount(r.get("thstrm_amount")) is not None else None)



def _ordv(r):
    try:
        return int(r.get("ord") or 0)
    except (TypeError, ValueError):
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# 경우의수 로직 트리 — 각 firm-quarter 응답이 어느 케이스로 지배 순이익·자본을 얻는지 명시 분류.
# 각 추출 함수는 (값, 케이스코드)를 반환. 케이스코드로 전 종목 분포를 집계해 exhaustive 검증.
#
# 재무제표 조합: IS(손익)·CIS(포괄손익) 중 하나 또는 둘 · CFS(연결)/OFS(별도) — fetch가 CFS→OFS.
# 지배/비지배 분해: 표준 account_id / 비표준(-표준계정코드 미사용- nm) / 분해 없음(무소수지분·별도).
# ─────────────────────────────────────────────────────────────────────────────
def _nm(r):
    return (r.get("account_nm") or "").replace(" ", "")


# 261020(지배 순이익 줄 고르기 — 값 구조 검사): 종전 `_ctrl_in_sj` 는 ① 계정 id 에 지배 귀속 순이익(`ProfitLossAttributableToOwnersOfParent`)이 든 줄
# → ② 이름에 「지배」가 든 줄(비지배·총포괄 계정 id 제외) 가운데 ord 가 가장 앞선 줄을 지배 순이익으로 골랐다. 이 규칙이 **총포괄이익의 지배 몫**이나
# 순이익이 아닌 줄을 고르는 경우가 실제로 여럿 있었다(10-20 원문 캐시 약 3,270건 · 운영 칸):
#   · 순이익 귀속 줄에 총포괄 계정 id 를 붙인 회사 — 그 줄은 ② 에서 빠지고 아래 「당기총포괄손익의 귀속 → 지배기업지분」(표준코드 없음)이 뽑혔다
#     (056360 FY2021 사업보고서: 순이익 지배 몫 7,012,006,146 인데 6,855,546,389 = 총포괄 지배 몫).
#   · 계정을 id 알파벳 순으로 내준 보고서 — 총포괄(ComprehensiveIncome) 아래 지배 줄이 당기순이익(ProfitLoss) 아래 지배 줄보다 ord 가 앞선다.
#   · 지배 순이익 계정 id 줄에 총포괄 값을 적은 회사(① 이 그대로 집음 — 336570 2021·2022 반기 등).
#   · 「지배」가 든 다른 줄 — 주당이익(원/주)·「종속기업, 공동지배기업과 관계기업으로부터의 기타이익」(별도 손익 항목)·계속영업 몫만·빈 줄(ord 가 앞선 빈 줄을
#     집으면 그 표 전체를 건너뛰어 비지배를 빼지 않은 총계로 갔다).
# 이제 후보 줄마다 **값 구조**를 본다: 지배 몫 + 비지배 몫 = 당기순이익 총계(비지배가 없으면 지배 몫 = 총계)면 순이익 줄(`ni`), 그 식이 당기순이익이 아니라
# 총포괄손익 총계(없으면 당기순이익 + 기타포괄손익)에만 맞으면 총포괄 줄(`ci` — 버린다). 둘 다 아니면 가를 수 없는 줄(비지배 줄이 응답에 빠진 회사 등) — 종전처럼 쓴다.
# 순서: 지배 순이익 id 줄 → 이름 줄(「포괄」이 든 이름은 뒤로) → 계속영업 몫 + 중단영업 몫 → 총포괄 id 줄(값 구조가 순이익일 때만 — 056360 꼴) 가운데
# 처음 `ni` 인 줄, 없으면 처음 가를 수 없는 줄(총포괄 id 줄 빼고), 그것도 없으면 아래 총계 규칙(지배 줄을 총포괄 몫이라 버렸으면 케이스 끝에 `_CIREJ`).
_CI_CTRL_ID = "ComprehensiveIncomeAttributableToOwnersOfParent"
#: 지배 귀속 순이익이 될 수 없는 줄 — 주당이익(원/주 · 「기본주당이익 계산에 사용되는 … 이익」 같은 금액 줄은 아니다) · 공동지배기업 손익 · 지배력 상실 손익
#: 261020 p1f ctrl-scan: 「주당」 뒤에 「지배·계속·중단」 같은 말이 오는 주당이익 이름(「기본주당지배회사이익」 · 「지배기업주당계속영업이익」 ·
#: 「지배기업지분에 대한 기본및희석주당중단영업손실」)도 주당이익이다 — 종전 꼴(주당 바로 뒤 이익·손실)만 걸러서 10-20 원문 086060 2021 사업보고서는 지배 id 줄
#: (총포괄 몫)을 버린 뒤 「기본주당지배회사이익」 −56(원/주)을 지배 순이익으로 골랐다(참값 = 비지배 없는 당기순이익 −4.84억). 「지배주주당기순이익」처럼
#: 「주주 + 당기」로 읽히는 「주당기」, 「기본주당이익 계산에 사용되는 …」 같은 금액 줄은 주당이익이 아니다.
_EPS_NM = re.compile(r"주당(?:순)?(?:이익|손익|손실)(?!계산)|주당(?!기)(?![^계]*계산)[가-힣]*?(?:이익|손익|손실)(?:\((?:이익|손실)\))?$")
_NOT_CTRL_NM = ("공동지배", "지배력")
#: 계속·중단영업 몫 — 하나만으론 합계가 아니다. 두 몫을 더해 한 후보로 본다(중단영업 몫이 없으면 계속영업 몫 = 합계).
_CONT_NM = ("계속영업", "계속사업")
_DISC_NM = ("중단영업", "중단사업")
#: 비지배 줄 이름 — 「비재배」는 원문 오타(10-20 운영 130740 2023 3분기~2024 3분기 「비재배지분귀속당기순이익」 · 캐시 1건)
_NCI_NM = ("비지배", "비재배")
#: 261020 p1f ctrl-scan: 총계 이름 앞의 「연결」(연결당기순이익 · 연결총포괄이익 · 연결기타포괄손익 — 표준 계정코드를 안 쓴 연결 보고서, 10-20 원문 003720
#: 2023 사업보고서)도 총계다. 종전엔 총계를 못 찾아 지배 줄의 값 구조를 볼 수 없었다(가를 수 없는 줄 — ord 첫 줄을 그대로 씀).
_CI_TOTAL_NM = re.compile(r"^(?:연결)?(?:당기|당반기|반기|당분기|분기)?총포괄(?:이익|손익|손실)(?:\((?:이익|손실)\))?$")
_OCI_TOTAL_NM = re.compile(r"^(?:연결)?(?:총|당기|반기|분기)?기타포괄(?:이익|손익|손실)(?:\((?:이익|손실)\))?$")


def _amt_tol(x: float) -> float:
    """원 단위 합 대조의 허용 오차 — 원문 표시 단위(원·천 원·백만 원) 반올림 한 단위 반. 단위는 값 끝의 0 개수로 본다(최대 백만)."""
    n, unit = abs(int(round(x))), 1
    while unit < 1_000_000 and n and n % (unit * 10) == 0:
        unit *= 10
    return max(2.0, 1.5 * unit, abs(x) * 1e-6)


def _sj_first(rows, pred):
    """IS 다음 CIS — 조건에 맞고 값이 있는 줄 가운데 ord 최선두의 누적 값."""
    for sj in ("IS", "CIS"):
        v = _first(rows, lambda r: r.get("sj_div") == sj and _cum_is(r) is not None and pred(r), _cum_is)
        if v is not None:
            return v
    return None


def _ni_shape(rows):
    """값 구조 대조 재료 — (당기순이익 총계, 총포괄손익 총계, [(비지배 줄, 값)]). 총계는 코드(`_ProfitLoss`·`_ComprehensiveIncome`) 다음 이름.
    총포괄 총계 줄이 없으면 당기순이익 + 기타포괄손익 총계(코드 `_OtherComprehensiveIncome` 다음 이름)."""
    tot = _total_ni(rows)
    if tot is None:
        tot = _total_ni_nm(rows)
    ci = _sj_first(rows, lambda r: (r.get("account_id") or "").endswith("_ComprehensiveIncome"))
    if ci is None:
        ci = _sj_first(rows, lambda r: bool(_CI_TOTAL_NM.match(_ENUM_PREFIX.sub("", _nm(r), count=1))))
    if ci is None and tot is not None:
        oci = _sj_first(rows, lambda r: (r.get("account_id") or "").endswith("_OtherComprehensiveIncome"))
        if oci is None:
            oci = _sj_first(rows, lambda r: bool(_OCI_TOTAL_NM.match(_ENUM_PREFIX.sub("", _nm(r), count=1))))
        ci = tot + oci if oci is not None else None
    nci = [(r, _cum_is(r)) for r in rows if r.get("sj_div") in ("IS", "CIS") and _cum_is(r) is not None
           and (any(k in _nm(r) for k in _NCI_NM) or "Noncontrolling" in (r.get("account_id") or ""))]
    return tot, ci, nci


def _fits(v, total, nci) -> bool:
    if v is None or total is None:
        return False
    tol = _amt_tol(total)
    return abs(v - total) <= tol or any(abs(v + y - total) <= tol for _, y in nci)


def _fits_nci_pair(v, shape) -> bool:
    """두 지배 줄의 합 `v` 가 **두 비지배 줄의 합**과 더해 당기순이익 총계가 되나 — 계속·중단영업을 지배·비지배로 각각 나눠 적은 보고서(261020 p1f ctrl-scan).
    10-20 원문: 002880 2019 사업보고서 「계속영업이익 → 지배지분귀속 101.83억 · 비지배지분귀속 77.75억」, 「중단영업이익 → 지배 −0.60억 · 비지배 −0.11억」,
    당기순이익 178.87억 — 지배 순이익 = 101.23억(종전: 첫 이름 줄 101.83억 · DB 는 총계 178.87억). 072770 2023 사업보고서는 총포괄 계속·중단 쌍이 순이익 쌍보다
    앞서 네 쌍이 같은 이름 — 순이익 쌍(−80.50억 + −6.50억, 비지배 −2.23억 + −2.05억 = 당기순이익 −91.27억)만 맞는다. 두 줄의 합에만 쓴다(한 줄 후보는 종전 그대로)."""
    tot, _ci, nci = shape
    if v is None or tot is None or len(nci) < 2:
        return False
    tol = _amt_tol(tot)
    ys = [y for _r, y in nci if y]
    return any(abs(v + ys[i] + ys[j] - tot) <= tol for i in range(len(ys)) for j in range(i + 1, len(ys)))


def _total_only_fit(v, shape) -> bool:
    """후보 값 `v` 가 「비지배 0」으로만 맞나 — `v` = 당기순이익 총계인데 같은 보고서에 0 이 아닌 비지배 줄이 있다(261020 p1j ctrl-rule-068290).
    그 읽기는 보고서 스스로와 어긋난다(비지배 몫이 있는데 지배 몫 = 총계). 아래 `_nci_fit` 인 다른 후보가 있으면 그쪽이 지배 순이익이다."""
    tot, _ci, nci = shape
    if v is None or tot is None:
        return False
    tol = _amt_tol(tot)
    return abs(v - tot) <= tol and any(abs(y) > tol for _r, y in nci)


def _nci_fit(v, shape) -> bool:
    """후보 값 `v` 가 **0 이 아닌** 비지배 줄(하나 또는 두 줄의 합)과 더해 당기순이익 총계가 되나 — `v` 자신은 총계가 아니다(261020 p1j)."""
    tot, _ci, nci = shape
    if v is None or tot is None:
        return False
    tol = _amt_tol(tot)
    if abs(v - tot) <= tol:
        return False
    return any(abs(y) > tol and abs(v + y - tot) <= tol for _r, y in nci) or _fits_nci_pair(v, shape)


def ni_attrib_side(v, shape) -> str | None:
    """지배 몫 후보 값 `v` 가 어느 쪽 구조에 맞나 — 'ni'(지배 + 비지배 = 당기순이익) · 'ci'(당기순이익엔 안 맞고 총포괄손익 총계에만 맞음 —
    총포괄이익의 지배 몫) · None(가를 수 없다: 총계가 없거나 어느 쪽에도 안 맞음). 기타포괄손익이 허용 오차 안이면 두 값이 같아 'ni'."""
    tot, ci, nci = shape
    if _fits(v, tot, nci):
        return "ni"
    if tot is not None and ci is not None and abs(ci - tot) > _amt_tol(tot) and _fits(v, ci, nci):
        return "ci"
    return None


def _ci_row(r) -> bool:
    """총포괄 쪽 줄 — 계정 id 에 총포괄(`ComprehensiveIncome`)이 들었거나 이름에 「포괄」."""
    return "ComprehensiveIncome" in (r.get("account_id") or "") or "포괄" in _nm(r)


def _is_nci_row(r) -> bool:
    """머리 줄 짝이 될 순이익 쪽 비지배 줄 — 이름에 「비지배」 또는 순이익 비지배 계정 id, 총포괄 쪽 줄은 빼고. 계정 id 만으로는 보지 않는다 — 총포괄 계속영업
    비지배 id 를 엉뚱한 줄에 붙인 보고서가 있다(10-20 원문 065150 2025 사업보고서 「계속영업이익(손실)」 줄)."""
    return (any(k in _nm(r) for k in _NCI_NM) or "ProfitLossAttributableToNoncontrolling" in (r.get("account_id") or "")) and not _ci_row(r)


def _is_owner_row(r) -> bool:
    """머리 줄 짝이 될 순이익 쪽 지배 줄 — 이름에 「지배」 또는 지배 순이익 계정 id(주당이익·비지배·총포괄 쪽 줄 빼고)."""
    aid, nm = r.get("account_id") or "", _nm(r)
    if any(k in nm for k in _NCI_NM) or "Noncontrolling" in aid or "PerShare" in aid or _EPS_NM.search(nm) or _ci_row(r):
        return False
    return "지배" in nm or _NI_CTRL_ID in aid


def _attrib_header(r, seq, val, shape=None) -> bool:
    """지배 이름·id 를 붙인 **귀속 머리 줄**인가 — 바로 다음 두 줄(같은 표 · 값이 있는 줄 · ord 순)이 지배 줄 하나와 0 이 아닌 비지배 줄 하나이고
    이 줄 값 = 그 둘의 합(비지배를 품은 소계)이다(261020 p1j ctrl-rule-068290). 그런 줄은 지배 몫 후보(지배 id · 이름 · 계속·중단영업 몫)가 아니다.
    종전엔 머리 줄을 지배 몫으로 읽어 비지배를 품은 값이 들어갔다. 10-20 원문 캐시 15,915건에서 나온 꼴:
      · 068290 2022 사업보고서 — 「중단영업순손익의 귀속」 자리에 「지배기업소유주지분의 귀속」 129.50억 → 다음 줄 「지배기업소유주지분의 귀속」 82.99억 ·
        「비지배지분 귀속」 46.51억(전기 칸 51.76 = 24.04 + 27.72 · 전전기 칸 0.90 = 0.42 + 0.48 도 같은 꼴). 당기 칸 종전 164.42억(비지배 포함 총계) → 117.91억.
      · 014130 2025 반기·3분기 — 「계속영업반기순이익의 귀속」(지배 기업 id) −76.12억 → 「지배기업 소유주지분」 −71.37억 · 「비지배지분」 −4.75억. 종전은 이 머리 줄을
        계속영업 지배 몫으로 써 반기 누적 −79.91억(= 반기순이익) → −75.16억(자본변동표 지배 몫과 같음).
      · 080160 2025 사업보고서 전전기 칸 — 「중단영업 당기순이익(손실)의 귀속」 57.13억 → 지배 27.89억 · 비지배 29.24억. 132.02억 → 102.78억(2023 사업보고서 값).
      · 417860 2026 1분기·반기 — 지배 순이익 id 줄 「반기순이익(손실)의 귀속」 = 다음 줄 「비지배지분」 8,363원 + 「지배기업소유주지분」.
    짝 두 줄은 순이익 쪽 줄만(총포괄 쪽 줄 빼고 — `_is_owner_row`·`_is_nci_row`). 이 줄 자신이 0 이 아닌 비지배와 짝이 맞으면(지배 + 비지배 = 당기순이익,
    `_nci_fit`) 머리 줄이 아니다 — 지배 몫 줄이 우연히 「비지배 + 다음 지배 줄」과 같은 꼴(반올림 허용 오차가 큰 백만 원 단위 보고서)을 막는다.
    261020 p1j 검토: 비지배는 허용 오차가 아니라 값 자체로 0 이 아닌지 보고, 대신 합은 비지배 반보다 촘촘하게 맞아야 한다(오차 ≤ min(허용 오차, |비지배|/2)) —
    허용 오차(총계의 1e-6)보다 작은 비지배를 0 으로 읽어 머리 줄을 놓쳤다(10-20 원문 417860 2026 반기 3개월 칸: 머리 줄 4,322,513,765 = 지배 4,322,512,149 +
    비지배 1,616, 허용 오차 4,322 — 누적 차와 1,616원 어긋난 총계를 골랐다). 비지배가 0 인 줄(지배 줄 = 머리 줄 값)은 여전히 머리 줄이 아니다."""
    v = val(r)
    if not v:
        return False
    i = next((k for k, x in enumerate(seq) if x is r), None)
    if i is None or i + 2 >= len(seq):
        return False
    a, b = seq[i + 1], seq[i + 2]
    tol = _amt_tol(v)
    if not any(_is_owner_row(own) and _is_nci_row(nci) and val(nci) and abs(val(own) + val(nci) - v) <= min(tol, abs(val(nci)) / 2)
               for own, nci in ((a, b), (b, a))):
        return False
    return not (shape is not None and _nci_fit(v, shape))


def _ctrl_cands(rows, sj, val, shape=None):
    """한 재무제표(sj) 안 지배 몫 후보 → [(순위, ord, 값, 서브케이스, 줄)] 순위순, 값이 빈 줄은 뺀다.
    순위 0 = 지배 순이익 id · 1 = 이름 · 2 = 이름에 「포괄」 · 3 = 계속영업 몫 + 중단영업 몫(`PARTS`) 또는 계속영업 몫 줄 없는 중단영업 지배 몫
    (`DISC` — 「두 줄의 합」에만 쓴다) · 4 = 총포괄 id(`CIID`). 귀속 머리 줄(`_attrib_header` — `shape` 는 그 값 구조 재료)은 뺀다(261020 p1j)."""
    out, cont, disc = [], [], []
    seq = sorted((r for r in rows if r.get("sj_div") == sj and val(r) is not None), key=_ordv)
    for r in rows:
        if r.get("sj_div") != sj or val(r) is None:
            continue
        aid, nm = r.get("account_id") or "", _nm(r)
        if _NI_CTRL_ID in aid:
            if not _attrib_header(r, seq, val, shape):
                out.append((0, _ordv(r), val(r), "ID", r))
            continue
        if any(k in nm for k in _NCI_NM) or "PerShare" in aid or _EPS_NM.search(nm) or any(k in nm for k in _NOT_CTRL_NM):
            continue
        if _attrib_header(r, seq, val, shape):
            continue
        if "ComprehensiveIncome" in aid or "포괄" in nm and ("계속" in nm or "중단" in nm):
            if _CI_CTRL_ID in aid:
                out.append((4, _ordv(r), val(r), "CIID", r))
            continue
        if "Discontinued" in aid or any(k in nm for k in _DISC_NM):
            if "지배" in nm or "OwnersOfParent" in aid or "ParentEntity" in aid:
                disc.append(r)
        elif "ContinuingOperations" in aid or any(k in nm for k in _CONT_NM):
            if "지배" in nm or "OwnersOfParent" in aid or "ParentEntity" in aid:
                cont.append(r)
        elif "지배" in nm:
            out.append((2 if "포괄" in nm else 1, _ordv(r), val(r), "NM", r))
    if cont:
        c = min(cont, key=_ordv)
        d = min(disc, key=_ordv) if disc else None
        out.append((3, _ordv(c), val(c) + (val(d) if d is not None else 0.0), "PARTS", c))
    elif disc:
        # 261020 p1f ctrl-scan: 계속영업 몫 줄이 따로 없이 「지배 몫」(이름 줄) 아래 「중단영업 지배 몫」만 적은 보고서 — 중단 몫을 혼자 쓰지 않고 아래 「두 줄의 합」
        # 후보로만 낸다(10-20 원문 00197759 2023 3분기: 「당기순이익의 귀속 → 지배기업의 소유주지분」 −106.69억 + 「중단영업 지배기업의 소유주주지분」 −26.07억
        # = 당기순이익 −132.76억, 비지배 없음). 종전엔 주당이익 줄(「…기본주당계속영업이익」 −470원/주)을 계속영업 몫으로 잘못 짝지어 우연히 맞았다.
        # 서브케이스 `DISC` — `_ctrl_in_sj` 가 혼자서는 돌려주지 않는다(혼자 값 구조가 맞거나 가를 수 없는 줄로 남아도 — 261020 검토).
        for d in disc:
            out.append((3, _ordv(d), val(d), "DISC", d))
    return sorted(out, key=lambda x: (x[0], x[1]))


def _cum_src(r) -> bool:
    """이 줄의 누적 값이 당기 누적 칸(thstrm_add)에서 왔나 — 아니면 당기 칸(분기보고서면 3개월)."""
    return normalize_amount(r.get("thstrm_add_amount")) is not None


def _tot_corroborated(rows, tot) -> bool:
    """당기순이익 총계가 같은 보고서의 총포괄손익 총계 − 기타포괄손익 총계(둘 다 줄이 있을 때)와 맞는가 — 총계 줄 자체를 믿을 수 있는지.
    10-20 원문 049180 2019 반기: 옛 분류체계 당기순이익 줄(`ifrs_ProfitLoss`)에 법인세차감전 값(−35.30억)을 적었고 참 순이익은 이름 줄 −43.68억
    (= 총포괄 −22.19억 − 기타포괄 21.49억) — 이때 총계 − 비지배로 물러나면 틀린다(지배 id 줄 −44.35억 + 비지배 0.67억 = −43.68억이 맞다)."""
    if tot is None:
        return False
    ci = _sj_first(rows, lambda r: (r.get("account_id") or "").endswith("_ComprehensiveIncome"))
    if ci is None:
        ci = _sj_first(rows, lambda r: bool(_CI_TOTAL_NM.match(_ENUM_PREFIX.sub("", _nm(r), count=1))))
    oci = _sj_first(rows, lambda r: (r.get("account_id") or "").endswith("_OtherComprehensiveIncome"))
    if oci is None:
        oci = _sj_first(rows, lambda r: bool(_OCI_TOTAL_NM.match(_ENUM_PREFIX.sub("", _nm(r), count=1))))
    return ci is not None and oci is not None and abs(ci - oci - tot) <= _amt_tol(tot)


def _id_off_nci(v, r, shape, side) -> bool:
    """지배 순이익 계정(id) 줄이 같은 보고서의 **순이익 쪽 비지배 계정 줄**(`…ProfitLossAttributableToNoncontrolling…`)과 짝이 안 맞는가
    — 지배 + 그 비지배 ≠ 당기순이익 총계. 그 지배 줄은 쓰지 않는다(빈 줄 — 가를 수 없는 줄로도 쓰지 않는다): 다음 후보, 없으면 총계 − 그 비지배.
    (261020 p1e firm-ctrl-ni — 결산기 칸 규칙 `period_cells.ctrl_ni` 의 「구조가 어긋난 지배 줄은 건너뛴다」를 한 규칙으로 합치며 옮김.)

    두 꼴: ① 값 구조가 「비지배 0」으로만 맞은 줄(지배 줄 = 당기순이익)인데 비지배 줄이 0 이 아니다 — 10-20 원문 신한알파리츠(293940) 2020.09 사업보고서
    전기 칸: 지배 줄 57.22억 = 당기순이익, 비지배 −0.64억, 지배 총포괄 57.86억(기타포괄 0) — 참값 57.86억.
    ② 어느 구조에도 안 맞는 줄(종전엔 「가를 수 없는 줄 — 종전처럼 쓴다」) — 원문 캐시 6,572건에서 12건: 347860 2025 사업보고서 전전기 칸 지배 줄
    −26.8억(자릿수 빠짐 — 당기순이익 −267.76억 − 비지배 0.04억 = −267.80억) · 298000 2024 1분기 지배 줄 +739.0억(당기순이익 −739.0억, 비지배 0 — 부호) ·
    01685251 2026 반기 지배 줄 73.5억(당기순이익 41.7억 − 비지배 −19.7억 = 61.4억, 지배 총포괄 64.0억) · 014940 2026 반기·100220 2024 3분기·246720 2022 3분기
    (지배 줄만 3개월 값 꼴, 비지배 0) 등 — 판단할 수 있는 칸은 모두 총계 − 비지배 쪽이 맞았다.
    당기순이익 총계 줄이 총포괄 − 기타포괄과 맞을 때만(`_tot_corroborated` — 총계 줄도 틀린 보고서가 있다, 049180 2019 반기) 버린다.
    이름 줄(`NM`)은 종전 그대로다(가를 수 없는 이름 줄은 종전 값 — #152 의 결정). 비지배 줄의 값이 누적 칸이 아니라 3개월 칸에서 왔으면(줄마다 누적 칸이
    비어 있는 보고서) 짝으로 보지 않는다(10-20 원문 s6c 01307593 2026 반기 — 비지배 줄에 3개월 값만)."""
    tot = shape[0]
    if tot is None or v is None:
        return False
    tol = _amt_tol(tot)
    if side == "ni" and abs(v - tot) > tol:
        return False                     # 지배 + 비지배 = 당기순이익으로 맞은 줄 — 그대로
    if side == "ci":
        return False                     # 총포괄 몫 — 위 규칙이 버린다
    ys = [y for y_row, y in shape[2] if "ProfitLossAttributableToNoncontrolling" in (y_row.get("account_id") or "")
          and _cum_src(y_row) == _cum_src(r)]
    if not ys or any(abs(v + y - tot) <= tol for y in ys):
        return False
    return side is None or any(abs(y) > tol for y in ys)


def _id_nci_negated(v, r, shape) -> bool:
    """지배 순이익 계정(id) 줄과 순이익 쪽 비지배 계정 줄이 **둘 다 부호가 뒤집혀** 적힌 보고서인가 — (−지배) + (−비지배) = 당기순이익,
    비지배 ≠ 0(비지배가 0 이면 −지배 = 당기순이익 = 총계 − 0 이라 가를 필요가 없다). `_id_off_nci` 로 버린 지배 id 줄에만 본다(총계 줄은
    `_tot_corroborated` 로 믿을 수 있을 때). 261020 p1f ctrl-scan 검토: 손실 회사가 귀속 두 줄을 양수로 적은 꼴 — 10-20 원문 289220 2022 사업보고서
    당기순손실 −126.76억 · 「지배기업주주지분」 +122.72억 · 「비지배주주지분」 +4.04억, 자본변동표 지배 몫 −122.72억 · 비지배 −4.04억. 종전(총계 − 비지배)은
    뒤집힌 비지배를 빼서 −130.80억(참값보다 비지배 두 배만큼 어긋남). 원문 캐시 9,499건에서 이 꼴 5칸(289220 2021·2022, 284620 2021) — 5칸 모두
    자본변동표 지배 몫 = −지배 줄."""
    tot = shape[0]
    if tot is None or not v:
        return False
    tol = _amt_tol(tot)
    ys = [y for y_row, y in shape[2] if "ProfitLossAttributableToNoncontrolling" in (y_row.get("account_id") or "")
          and _cum_src(y_row) == _cum_src(r)]
    return any(abs(y) > tol and abs(-v - y - tot) <= tol for y in ys)


# ─────────────────────────────────────────────────────────────────────────────
# 261021 ni-sce: 자본변동표(SCE) 지배 몫 대조. 손익계산서 줄의 값 구조(지배 + 비지배 = 당기순이익)만으로는 못 가르는 꼴이 남았다
# (10-20 원문 캐시 · 운영 칸 — 「값 구조 확인 못 함」 칸 84칸·34종목):
#   · 가를 수 없는 줄로 물러남 — 086520 2021 반기: 「계속영업 반기순이익의 귀속 → 지배기업 소유주지분」 27.60억(계속영업 몫만 — 중단영업 몫 귀속 줄이
#     없다)을 그대로 썼다. 같은 응답의 자본변동표 지배 몫 5,613.54억 + 비지배 165.57억 = 반기순이익 5,779.11억.
#   · 지배·비지배 값을 서로 바꿔 적은 원문 — 값 구조(v + y = 총계)는 대칭이라 못 가린다. 009070 2022 사업보고서: 「지배기업의 소유주지분」 이름 줄에
#     비지배 id, 「비지배지분」 이름 줄에 지배 id(18.08억) — 자본변동표 지배 몫 262.89억. 004590 2023 사업보고서 전기 칸 −724.5만(참 117.90억).
#   · 총계 규칙으로 물러난 길 — 지배 줄을 총포괄 몫이라 버리고(`_CIREJ`) 비지배를 빼지 않거나(052770 2021 반기 · 347860 2023 1분기), 비지배 id 줄
#     값이 틀려 지배 id 줄을 버리고 총계 − 그 비지배로 간 꼴(`_IDX` — 145270 2025 사업보고서: 손익계산서 비지배 2.97억 vs 자본변동표 2.91억 = 재무상태표
#     비지배지분 증가 2.91억), 「비지배지분」 이름 줄에 기타포괄 값을 적은 꼴(196490 2020 사업보고서).
# 자본변동표는 손익계산서 줄 고르기와 무관한 다른 표라 독립 확인이 된다. 다만 자본변동표도 틀리게 적은 보고서가 있어(합계 열에 3개월 값 · 손실을 양수로
# 적는 관행 · 외화 표시) **같은 응답의 당기순이익 총계와 맞을 때만** 쓴다(`_sce_ctrl_ni` — SCE 지배 몫 + SCE 비지배 몫 = 손익계산서 당기순이익).
#: 자본변동표 합계 열(멤버 하나짜리 경로)
_SCE_TOTAL_MEMBERS = ("연결재무제표", "별도재무제표")
_MEMBER_SFX = re.compile(r"\[(?:member|구성요소)\]$", re.IGNORECASE)


def _sce_member(p: str) -> str:
    return _MEMBER_SFX.sub("", (p or "").replace(" ", ""))


def _sce_ni_row(r) -> bool:
    """자본변동표의 당기순이익 줄 — 계정 id `…_ProfitLoss`(새·옛 접두어) 또는 표준코드 없는 총계 이름(「당기순이익(손실)」·「반기순이익」·「연결당기순이익」).
    다른 계정 id 를 붙인 「당기순이익」 이름 줄(외화환산 등 id 를 잘못 붙인 줄)은 보지 않는다."""
    if r.get("sj_div") != "SCE":
        return False
    aid = r.get("account_id") or ""
    if aid.endswith("_ProfitLoss"):
        return True
    if aid and not aid.startswith("-표준계정코드"):
        return False
    return _ni_total_name(r.get("account_nm"))


#: 중단영업 순이익 줄 이름(손익계산서·자본변동표) — 「중단영업반기순이익」·「중단사업 당기순손익」·「중단영업이익(손실)」. 주당이익·귀속·지배 몫 줄은 걸리지 않는다(이름 전체가 이 꼴일 때만).
_DISC_NI_NM = re.compile(r"^(?:당기|당반기|반기|당분기|분기)?중단(?:영업|사업)(?:당기|당반기|반기|당분기|분기)?(?:순)?(?:이익|손실|손익)(?:\((?:이익|손실)\))?$")


def _disc_id(aid) -> bool:
    """중단영업 순이익 계정 id — `…ProfitLossFromDiscontinuedOperations` · `…LossFromDiscontinuedOperations` (주당이익·세금 줄 id 는 아니다).
    총포괄·현금흐름 조정 줄 id 도 아니다 — 10-20 원문 캐시 손익 표에 `ifrs-full_ComprehensiveIncomeFromDiscontinuedOperations`(중단영업 총포괄) 29줄 ·
    `dart_AdjustmentsFor…FromDiscontinuedOperations`(현금흐름표 조정 줄을 손익 표에 둔 보고서) 2줄이 있다."""
    aid = aid or ""
    return aid.endswith("FromDiscontinuedOperations") and not any(k in aid for k in ("PerShare", "Tax", "ComprehensiveIncome", "Adjustments"))


def _disc_ni_name(r) -> bool:
    return bool(_DISC_NI_NM.match(_ENUM_PREFIX.sub("", _nm(r), count=1)))


def _sce_disc_row(r) -> bool:
    """자본변동표의 중단영업 순이익 줄 — 계정 id `…FromDiscontinuedOperations` 또는 표준코드 없는 중단영업 순이익 이름(089470 「중단영업반기순이익」)."""
    if r.get("sj_div") != "SCE":
        return False
    aid = r.get("account_id") or ""
    if aid and not aid.startswith("-표준계정코드"):
        return _disc_id(aid)
    return _disc_ni_name(r)


def _tot_row(rows):
    """`_ni_shape` 의 당기순이익 총계 줄 — 코드(`_ProfitLoss`) 줄, 없으면 이름 줄(IS 다음 CIS · ord 최선두)."""
    for pred in (lambda r: (r.get("account_id") or "").endswith("_ProfitLoss"), lambda r: _ni_total_name(r.get("account_nm"))):
        for sj in ("IS", "CIS"):
            c = [r for r in rows if r.get("sj_div") == sj and pred(r)]
            if c and _cum_is(min(c, key=_ordv)) is not None:
                return min(c, key=_ordv)
    return None


def _tot_cols_disagree(rows) -> bool:
    """1분기·사업보고서인데 당기순이익 총계 줄의 당기 칸과 누적 칸이 서로 다른가 — 두 칸은 같은 값이어야 한다(3개월 = 누적, 한 해 = 누적).
    261021 ni-sce-fp: 081580 2022 1분기(접수 20220516001078) 「당기순이익」 줄 당기 칸 24.76억(= 세전 25.39억 − 법인세 0.63억) · 누적 칸 30.90억(오기)."""
    row = _tot_row(rows)
    if row is None or str(row.get("reprt_code") or "") not in ("11013", "11011"):
        return False
    a, b = normalize_amount(row.get("thstrm_amount")), normalize_amount(row.get("thstrm_add_amount"))
    return a is not None and b is not None and abs(float(a) - float(b)) > _amt_tol(float(a))


def _tot_pbt_fit(rows, tot) -> bool:
    """당기순이익 총계가 같은 보고서의 법인세차감전 이익 − 법인세비용(+ 중단영업 순이익)과 맞는가 — 법인세비용 줄이 없으면 확인 못 함(False)."""
    if tot is None:
        return False
    tol = _amt_tol(tot)
    pbt = _sj_first(rows, lambda r: (r.get("account_id") or "").endswith("_ProfitLossBeforeTax"))
    tax = _sj_first(rows, lambda r: "IncomeTaxExpense" in (r.get("account_id") or ""))
    if pbt is None or tax is None:
        return False
    disc = _sj_first(rows, lambda r: _disc_id(r.get("account_id"))) or 0.0
    return any(abs(pbt + sg * tax + dv - tot) <= tol for sg in (1, -1) for dv in (0.0, disc))


def _disc_ni(rows) -> float | None:
    """당기순이익 총계 줄이 **계속영업 몫뿐**(이름에 「계속영업」·「계속사업」)이고 0 이 아닌 중단영업 순이익 줄이 따로 있으면 그 값(IS 다음 CIS, 누적 우선), 아니면 None.
    261021 ni-sce-fp: 089470 2023 반기 — `ProfitLoss` 줄 「계속영업반기순이익」 166.84억 · 「중단영업반기순이익」 30.60억은 `ProfitLoss` 계정이 아니다."""
    row = _tot_row(rows)
    if row is None or not any(k in _nm(row) for k in _CONT_NM):
        return None
    d = _sj_first(rows, lambda r: _disc_id(r.get("account_id")) or (
        (not (r.get("account_id") or "") or (r.get("account_id") or "").startswith("-표준계정코드")) and _disc_ni_name(r)))
    return d or None


def _sce_one(bucket) -> tuple[float | None, bool]:
    """경로 → 값들 묶음에서 가장 얕은 경로들의 값이 하나로 모이면 (그 값, True), 비었으면 (None, True), 여럿이면 (None, False — 애매)."""
    if not bucket:
        return None, True
    d = min(len(p) for p in bucket)
    vals = set().union(*(vs for p, vs in bucket.items() if len(p) == d))
    return (float(vals.pop()), True) if len(vals) == 1 else (None, False)


def _sce_owner(rows, field, tot, nci_pairs, row_pred) -> float | None:
    """자본변동표 순이익 줄(`row_pred`)의 지배 몫 — 열 가르기·대조 문은 `_sce_ctrl_ni` 설명 그대로. `tot` = 맞출 당기순이익, `nci_pairs` = 손익계산서 비지배 줄들(`_ni_shape`[2])."""
    owner, nci, total, comp = {}, {}, {}, {}
    for r in rows or []:
        if not row_pred(r):
            continue
        v = normalize_amount(r.get(field))
        if v is None:
            continue
        path = tuple(_sce_member(p) for p in (r.get("account_detail") or "").split("|") if p.strip())
        if not path:
            continue
        last = path[-1]
        if any(k in last for k in _NCI_NM):
            b = nci
        elif "지배" in last and "공동지배" not in last:
            b = owner
        elif len(path) == 1 and last in _SCE_TOTAL_MEMBERS:
            b = total
        else:
            b = comp
        b.setdefault(path, set()).add(int(round(float(v))))
    if any(len(vs) > 1 for b in (owner, nci, total, comp) for vs in b.values()):
        return None                                   # 같은 열에 값이 여럿 — 애매
    o, ok = _sce_one(owner)
    if not ok:
        return None
    n, ok = _sce_one(nci)
    if not ok:
        return None
    if o is None:
        # 지배 멤버 바로 아래 구성요소 열의 합(지배 몫 합계 열을 안 낸 보고서)
        kids = {p: vs for p, vs in comp.items() if len(p) >= 2 and "지배" in p[-2] and not any(k in p[-2] for k in _NCI_NM)}
        if kids:
            if len({p[:-1] for p in kids}) != 1:
                return None
            o = float(sum(next(iter(vs)) for vs in kids.values()))
        elif not any("지배" in q and not any(k in q for k in _NCI_NM) for p in comp for q in p):
            t, ok = _sce_one(total)
            if not ok or t is None or n is None:
                return None
            o = t - n                                 # 지배 멤버 없는 보고서 — 합계 열 − 비지배 열
        else:
            return None
    tol = _amt_tol(tot)
    if n is None:
        if any(abs(y) > tol for _r, y in nci_pairs):
            return None                               # 손익계산서엔 비지배가 있는데 자본변동표엔 비지배 열이 없다
        n = 0.0
    sols = {so * o for so in (1, -1) for sn in (1, -1) if abs(so * o + sn * n - tot) <= tol}
    if abs(o + n - tot) <= tol:
        return o
    return sols.pop() if len(sols) == 1 else None




def _sce_ctrl_ni(rows, field: str = "thstrm_amount", shape=None) -> float | None:
    """같은 응답 자본변동표(sj_div=SCE)의 당기순이익 줄에서 **지배기업 소유주 몫**을 읽는다 — 손익계산서 당기순이익 총계와 맞을 때만, 아니면 None.

    열(account_detail 경로의 마지막 멤버)로 가른다: 「…지배기업의 소유주에게 귀속되는 자본/지분」 = 지배 몫 합계 열 · 「비지배지분」 = 비지배 열 ·
    「연결재무제표」·「별도재무제표」 = 합계 열 · 나머지 = 구성요소 열. 지배 몫 = ① 지배 몫 합계 열(값이 하나로 모일 때) → ② 없으면 지배 멤버 바로 아래
    구성요소 열의 합 → ③ 지배 멤버가 아예 없으면 합계 열 − 비지배 열.
    맞춤(대조 문): 지배 몫 + 비지배 몫(열이 없으면 0) = 손익계산서 당기순이익 총계(`_amt_tol` 안). 손실을 양수로 적는 자본변동표 관행이 있어 두 값의
    부호를 뒤집어 맞춰 보되 답이 하나로 모일 때만 쓴다(10-20 원문 196490 2020 사업보고서: 지배 몫 +296.05억 · 비지배 +1.18억 · 당기순이익 −294.87억 →
    −296.05억). 비지배 열이 없는데 손익계산서에 0 이 아닌 비지배 줄이 있으면(두 표가 어긋남) 쓰지 않는다. 같은 경로에 값이 여럿이거나(정정 전·후 줄 등)
    지배 몫 합계 열 값이 여럿이면 None(애매 — 자본변동표 대조 후보에는 원문 자체가 틀린 오탐이 섞여 있다).
    `field` = 볼 자본변동표 칸(사업보고서 thstrm·frmtrm·bfefrmtrm · 분기보고서 thstrm = 당기 누적 — DART 응답의 분기 자본변동표엔 당기 칸만 있고 3개월 칸은 없다).
    대조 문이 거짓으로 맞는 두 꼴은 쓰지 않는다(261021 ni-sce-fp, DATA-NI3):
      ① 1분기·사업보고서인데 당기순이익 줄의 당기 칸과 누적 칸이 다르면(`_tot_cols_disagree`) 그 총계 줄을 못 믿는다 — 총포괄 − 기타포괄(`_tot_corroborated`)이나
        세전 − 법인세(+ 중단영업)(`_tot_pbt_fit`)로 총계가 확인될 때만 쓴다. 081580 2022 1분기: 누적 칸 오기 30.90억(당기 칸 24.76억)에 자본변동표 지배 열 오기
        33.26억 + 비지배 −2.36억이 우연히 맞았다(참값 27.12억 = 반기 보고서 누적 53.56억 − 반기 3개월 26.43억).
      ② 당기순이익 줄이 계속영업 몫뿐(이름에 「계속영업」)이고 0 이 아닌 중단영업 순이익 줄이 따로 있으면(`_disc_ni`) 자본변동표 계속영업 줄 + 중단영업 줄 두 줄이
        각각 자기 총계와 맞을 때만 그 합을 쓴다(중단영업 줄이 자본변동표에 없거나 안 맞으면 None). 089470 2023 반기: 계속 183.45억만 맞추면 중단 30.60억이 빠진다
        (참값 214.05억 = 2024 반기 보고서 전년 동기 칸)."""
    shape = shape or _ni_shape(rows)
    tot = shape[0]
    if tot is None:
        return None
    if _tot_cols_disagree(rows) and not (_tot_corroborated(rows, tot) or _tot_pbt_fit(rows, tot)):
        # 261021 ni-sce-fp ①: 1분기·사업보고서인데 당기순이익 줄의 당기 칸과 누적 칸이 다르면 총계 줄 자체를 못 믿는다 — 그 총계에 맞춘 자본변동표도 못 믿는다
        # (081580 2022 1분기: 누적 칸 오기 30.90억에 자본변동표 지배 열 오기 33.26억 + 비지배 −2.36억이 우연히 맞아 떨어졌다. 참 당기순이익 24.76억).
        # 총포괄 − 기타포괄 또는 세전 − 법인세(+ 중단영업)로 총계가 확인될 때만 쓴다.
        return None
    d = _disc_ni(rows)
    if d is None:
        return _sce_owner(rows, field, tot, shape[2], _sce_ni_row)
    # 261021 ni-sce-fp ②: 총계 줄이 계속영업 몫뿐인데 중단영업 순이익 줄이 따로 있다 — 계속영업 줄만 맞추면 중단영업 몫이 빠진다(089470 2023 반기: 계속 183.45억 +
    # 중단 30.60억 = 214.05억 인데 계속 줄만 쓰면 183.45억). 계속영업(총계 줄)·중단영업(중단영업 순이익 줄) 두 줄이 각각 맞을 때만, 합을 쓴다.
    o1 = _sce_owner(rows, field, tot, shape[2], _sce_ni_row)
    o2 = _sce_owner(rows, field, d, [], _sce_disc_row) if o1 is not None else None
    return None if o1 is None or o2 is None else o1 + o2


def _near(a, b) -> bool:
    return a is not None and b is not None and abs(a - b) <= _amt_tol(b)


def _ctrl_in_sj(rows, sj, val, shape=None, sce=None):
    """한 재무제표(sj) 안 지배 귀속 순이익 → (값, 서브케이스 'ID'|'IDNEG'|'NM'|'PARTS'|'CIID'|'SCE'|'…_SCE'|None, 총포괄 몫이라 버린 줄 [(순위, 줄)],
    비지배와 짝이 안 맞아 버린 지배 id 줄 수). 값 구조 검사는 위 261020 주석. `shape` = `_ni_shape(rows)`(없으면 계산). IS/CIS 는 ord 시퀀스 별개 → sj 단위.
    `sce` = 같은 응답 자본변동표 지배 몫(`_sce_ctrl_ni` — 없으면 None): 값 구조로 못 가르는 두 곳에서만 본다(261021 ni-sce, 위 주석) —
    가를 수 없는 줄로 물러날 때(그 줄이 SCE 와 다르면 SCE 와 같은 후보 → 없으면 SCE 값 `SCE`), 지배·비지배 짝이 대칭이라 못 가릴 때
    (고른 줄 ≠ SCE 인데 짝 비지배 줄 = SCE → 그 줄 `SWAP_SCE`)."""
    shape = shape or _ni_shape(rows)
    tot = shape[0]
    fallback, rejected, unk, dropped, total_only = None, [], [], 0, None
    subs = []                             # 261021: 가를 수 없는 후보 (값, 서브케이스) — SCE 대조용
    tot_ok = None                         # 총계 줄을 믿을 수 있나(`_tot_corroborated`) — 필요할 때 한 번만 센다
    for rank, _o, v, sub, r in _ctrl_cands(rows, sj, val, shape):
        if sub == "DISC":
            # 261020 p1f ctrl-scan 검토: 계속영업 몫 줄 없는 「중단영업 지배 몫」 줄은 혼자서는 지배 순이익이 아니다 — 아래 「두 줄의 합」에만 넣는다
            # (가를 수 없는 줄로 남아도 물러나 쓰지 않는다: 지배 id 줄을 총포괄 몫이라 버린 보고서에서 중단 몫만 돌려주던 길을 막는다 — 그때는 총계 − 비지배).
            if v:
                unk.append(v)
            continue
        side = ni_attrib_side(v, shape)
        if rank == 0 and _id_off_nci(v, r, shape, side) and (tot_ok if tot_ok is not None else (tot_ok := _tot_corroborated(rows, tot))):
            # 261020 p1e firm-ctrl-ni: 비지배 줄과 짝이 안 맞는 지배 id 줄(`_id_off_nci`) — 혼자서는 쓰지 않는다. 다만 계속·중단영업 몫을 나눠 적은 회사의
            # 한 몫일 수 있어 아래 「두 줄의 합」에는 넣는다(10-20 원문 049630 2024 3분기: 「지배기업의 소유주(계속)」 27.78억 이 id 줄,
            # 「지배기업의 소유주(중단)」 −3.45억 이 이름 줄 — 합 24.34억 + 비지배 1.21억 = 당기순이익 25.55억).
            if _id_nci_negated(v, r, shape):
                # 261020 p1f ctrl-scan 검토: 지배·비지배 두 귀속 줄이 함께 부호가 뒤집힌 꼴(`_id_nci_negated`) — 지배 몫 = −지배 줄.
                return -v, "IDNEG", rejected, dropped
            dropped += 1
            unk.append(v)
            continue
        if side == "ni":
            if _total_only_fit(v, shape):
                # 261020 p1j ctrl-rule-068290: 「비지배 0」으로만 맞는 줄(= 당기순이익 총계, 그런데 0 이 아닌 비지배 줄이 있다)은 바로 쓰지 않는다 —
                # 지배 줄에 총계를 적은 보고서다. 0 이 아닌 비지배와 짝이 맞는 후보(뒤의 한 줄 · 아래 두 줄의 합)가 없을 때만 쓴다(그때는 종전과
                # 같은 값·케이스·버린 줄). 10-20 원문 073540 2022 반기: 지배 id 줄 「지배기업의 소유주에게 귀속되는 당기순이익(손실)」 −14.85억 =
                # 반기순이익인데 앞의 「지배기업 소유주지분」 −14.62억 + 「비지배지분」 −0.22억 = 반기순이익(자본변동표 지배 몫 −14.62억 · 2023 반기
                # 보고서 전년 동기 칸도 −14.62억). 머리 줄 꼴(`_attrib_header`)이 아니라 위 머리 줄 규칙으로는 못 가른다.
                total_only = total_only or (v, sub, list(rejected), dropped)
                continue
            if sce is not None and not _near(v, sce):
                # 261021 ni-sce: 지배·비지배 짝이 대칭이다 — v + y = 당기순이익이면 y + v 도 그렇다. 고른 줄 ≠ 자본변동표 지배 몫인데 짝 비지배 줄 =
                # 자본변동표 지배 몫이면 원문이 두 값을 바꿔 적은 것이다(009070 2022 사업보고서 · 004590 2023 사업보고서 당기·전기 칸).
                y = next((y for _r, y in shape[2] if abs(y) > _amt_tol(tot) and abs(v + y - tot) <= _amt_tol(tot) and _near(y, sce)), None)
                if y is not None:
                    return y, "SWAP_SCE", rejected, dropped
            return v, sub, rejected, dropped
        if side == "ci":
            rejected.append((rank, r))
        elif rank <= 3 and not (v == 0 and tot is not None and abs(tot) > _amt_tol(tot)):
            # 가를 수 없는 줄 — 종전처럼 쓴다(총포괄 id 줄은 값 구조가 맞을 때만). 당기순이익이 있는데 지배 몫이 0 인 줄은 빈 줄로 본다
            # (10-20 운영 045660 2025 1분기: 「당기순이익 → 지배기업소유주지분」 0, 당기순이익 165.7억 · 비지배 없음).
            unk.append(v)
            subs.append((v, sub))
            fallback = fallback or (v, sub)
    # 두 줄의 합이 순이익 구조에 맞으면 계속·중단영업 몫을 이름 없이 나눠 적은 회사다(머리 줄 「계속영업분기순이익」·「중단영업분기순이익」 아래
    # 「지배기업소유주지분」 두 줄 — 10-20 운영 101530 2020 3분기). 한 줄만 쓰면 한 몫만 들어간다.
    # 261020 p1j ctrl-rule-068290: 맞는 두 줄 합이 여럿이면 0 이 아닌 비지배와 짝이 맞는 합(`_nci_fit`)을 「비지배 0」으로만 맞는 합(= 당기순이익
    # 총계)보다 먼저 쓰고, 「비지배 0」으로만 맞는 한 줄(위 `total_only`)보다도 먼저 쓴다. 10-20 원문 068290 2022 사업보고서: 「계속영업순손익의 귀속 →
    # 지배기업소유주지분의 귀속」 34.92억 · 「비지배지분 귀속」 0, 다음 줄 「지배기업소유주지분의 귀속」 129.50억(실은 중단영업 몫 총계) →
    # 「지배기업소유주지분의 귀속」 82.99억 · 「비지배지분 귀속」 46.51억, 당기순이익 164.42억. 종전은 앞선 합 34.92 + 129.50 = 164.42억(비지배 0 줄로
    # 맞음 — 비지배 포함 총계)을 골랐다. 참값 117.91억(34.92 + 82.99, + 비지배 46.51 = 164.42 · 다음 해 보고서 전기 칸과 같음). 129.50억 줄은 머리 줄
    # 규칙(`_attrib_header`)도 뺀다 — 이 순서는 머리 줄이 아닌 꼴(다음 두 줄이 지배·비지배 짝이 아닌 소계)에 대한 두 번째 그물이다.
    pairs = [unk[i] + unk[j] for i in range(len(unk)) for j in range(i + 1, len(unk))
             if unk[i] and unk[j] and (ni_attrib_side(unk[i] + unk[j], shape) == "ni" or _fits_nci_pair(unk[i] + unk[j], shape))]
    if total_only is not None or any(_total_only_fit(s, shape) for s in pairs):
        s = next((s for s in pairs if _nci_fit(s, shape)), None)
        if s is not None:
            return s, "PARTS", rejected, 0
        if total_only is not None:
            return total_only
    if pairs:
        return pairs[0], "PARTS", rejected, 0
    if fallback is not None and not dropped:
        # 지배 id 줄을 비지배와 짝이 안 맞아 버렸으면 가를 수 없는 이름 줄로도 물러나지 않는다 — 총계 − 비지배(값 구조가 맞는 길)로 간다.
        if sce is not None and not _near(fallback[0], sce):
            # 261021 ni-sce: 가를 수 없는 줄이 자본변동표 지배 몫과 다르다 — SCE 와 같은 후보, 없으면 SCE 값(SCE 지배 + 비지배 = 당기순이익은
            # `_sce_ctrl_ni` 가 이미 맞춰 봤다). 10-20 원문 086520 2021 반기: 첫 이름 줄 27.60억(계속영업 몫) → 5,613.54억.
            hit = next(((v, s) for v, s in subs if _near(v, sce)), None)
            return (hit[0], f"{hit[1]}_SCE", rejected, dropped) if hit else (sce, "SCE", rejected, dropped)
        return fallback[0], fallback[1], rejected, dropped
    return None, None, rejected, dropped


def _first(rows, pred, val):
    """조건 pred 만족 행 중 ord 최선두 → val(r), 없으면 None. (id/taxonomy 무관 매칭용)"""
    cands = [r for r in rows if pred(r)]
    return val(min(cands, key=_ordv)) if cands else None


def _total_ni(rows):
    """당기순이익 총계(지배+비지배). 구·신 taxonomy(ifrs_ProfitLoss / ifrs-full_ProfitLoss) 모두."""
    for sj in ("IS", "CIS"):
        v = _first(rows, lambda r: r.get("sj_div") == sj and (r.get("account_id") or "").endswith("_ProfitLoss"), _cum_is)
        if v is not None:
            return v
    return None


def _minority_ni(rows, skip=frozenset()):
    """비지배 당기순이익(총포괄 비지배는 ComprehensiveIncome id로 배제). 261020: `skip` = 버린 총포괄 지배 몫과 짝이 맞는 비지배 줄(id) — 총포괄 쪽 비지배."""
    for sj in ("IS", "CIS"):
        v = _first(rows, lambda r: r.get("sj_div") == sj and "ProfitLossAttributableToNoncontrolling" in (r.get("account_id") or ""), _cum_is)
        if v is not None:
            return v
    for sj in ("IS", "CIS"):
        v = _first(rows, lambda r: r.get("sj_div") == sj and "비지배" in _nm(r) and "ComprehensiveIncome" not in (r.get("account_id") or "")
                   and id(r) not in skip, _cum_is)
        if v is not None:
            return v
    return None


# 261018(재설계 6단계 — 수집 파서 공백): 당기순이익 총계 행을 **이름으로** 찾는 마지막 단계. 표준 계정코드를 안 쓴
# 회사(`-표준계정코드 미사용-`)는 총계 행 이름이 「Ⅸ.당기순손실」·「Ⅸ.반기순손실」·「VII. 당기순이익(손실)」·「분기순이익(손실)」 꼴이라
# 코드로는 못 찾았다(NO_INCOME_ROW — 10-18 운영 036480 2026 1·2분기 등 40행). 이름 전체가 이 꼴일 때만 — 「법인세비용차감전순이익」·
# 「계속영업이익」·「총포괄손익」·「지배기업 소유주지분 순이익」 같은 다른 행은 걸리지 않는다. 같은 보고서의 XBRL 원본 ProfitLoss 가
# 있는 행은 값이 전부 같았다(10-18 실측 26행).
_ENUM_PREFIX = re.compile(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+|[IVX]+|\d+|\(\d+\))[.)]?")
_NI_TOTAL_NM = re.compile(r"^(?:연결)?(?:당기|당반기|반기|당분기|분기)?순(?:이익|손실|손익)(?:\((?:이익|손실)\))?$")


def _ni_total_name(nm) -> bool:
    s = (nm or "").replace(" ", "")
    return bool(_NI_TOTAL_NM.match(_ENUM_PREFIX.sub("", s, count=1)))


def _total_ni_nm(rows):
    """당기순이익 총계 — 이름으로(`_ni_total_name`), IS 다음 CIS, 값이 있는 행 가운데 ord 최선두. 코드로 못 찾았을 때만 쓴다."""
    for sj in ("IS", "CIS"):
        v = _first(rows, lambda r: r.get("sj_div") == sj and _ni_total_name(r.get("account_nm")) and _cum_is(r) is not None,
                   _cum_is)
        if v is not None:
            return v
    return None


def extract_controlling_ni_cum(rows):
    """지배 당기순이익 누적 → (값, 케이스). 트리:
      NODATA · IS_ID/IS_NM · CIS_ID/CIS_NM — 명시 지배행 [LG화학·삼성·기아]
      {IS,CIS}_PARTS       — 계속영업 몫 + 중단영업 몫(또는 이름 없이 나눈 두 지배 줄의 합, 값 구조가 순이익) (261020)
                             261020 p1f ctrl-scan: 두 지배 줄의 합은 비지배 두 줄의 합과도 맞춰 본다(계속·중단을 각각 지배·비지배로 나눈 보고서) ·
                             「중단영업 지배 몫」 줄만 있으면 그 줄도 두 줄 합 후보(혼자서는 쓰지 않는다 — 검토)
      {IS,CIS}_IDNEG       — 지배 순이익 id 줄과 순이익 쪽 비지배 id 줄이 둘 다 부호가 뒤집혀 적힌 보고서 → −지배 줄(261020 p1f ctrl-scan 검토,
                             `_id_nci_negated`)
      {IS,CIS}_CIID        — 총포괄 계정 id 를 붙인 줄인데 값 구조가 순이익(순이익 귀속 줄에 총포괄 id — 056360) (261020)
      TOTAL_MINUS_MINORITY — 명시 지배행 없으나 당기순이익 총계 − 비지배순이익 [유한양행형: 서브토탈 부재]
      TOTAL                — 지배·비지배 귀속 둘 다 부재 → 당기순이익 총계=지배 [001340·NAVER 무소수지분]
      TOTAL_SCE            — TOTAL 인데 같은 응답 자본변동표가 지배 + 비지배 = 당기순이익으로 맞고 비지배 몫이 0 이 아니라 총계에 비지배가 들어 있음 → 자본변동표 지배 몫
                             (261022 DATA-NI3 — 217500 2026 1분기 −4.05억 → −594.3만 · 081580 FY2023 −4.90억 → −1.62억)
      TOTAL_NM[_MINUS_MINORITY] — 위와 같은데 총계 행을 코드가 아니라 이름으로 찾음(261018, 표준 계정코드 미사용 회사)
      …_CIREJ              — 위 총계 규칙인데 지배 줄(id·이름)이 총포괄이익의 지배 몫이라 버린 보고서(261020)
      …_CINCI              — 순이익 쪽 비지배 줄이 없어 총포괄 쪽 비지배 줄(버린 지배 몫과 짝)로 뺀 보고서(261020 검토 — 근사, 비지배 몫 기타포괄만큼 어긋남.
                             기타포괄손익이 그 비지배와 같은 부호로 그만큼 이상이면 빼지 않는다 — 2차 검토 부호 가드)
      …_IDX                — 지배 순이익 계정 id 줄이 순이익 쪽 비지배 계정 줄과 짝이 안 맞아(지배 + 비지배 ≠ 당기순이익) 버린 보고서(261020 p1e firm-ctrl-ni,
                             `_id_off_nci` — 결산기 칸 규칙에서 합침)
      {IS,CIS}_SCE         — 값 구조로 못 가른 길(가를 수 없는 줄로 물러남 · 아래 총계 규칙의 `_CIREJ`·`_CINCI`·`_IDX`)이 같은 응답 자본변동표 지배 몫과
                             다르고 그 값과 같은 후보도 없어 자본변동표 지배 몫을 그대로 씀(261021 ni-sce, `_sce_ctrl_ni` — SCE 지배 + 비지배 = 당기순이익일 때만)
      {IS,CIS}_{ID,NM,…}_SCE · TOTAL…_SCE — 같은 길에서 자본변동표 지배 몫과 같은 다른 후보를 고름(가를 수 없는 다른 줄 · 총계 − 비지배 줄 · 버린 지배 줄)
      {IS,CIS}_SWAP_SCE    — 지배 줄 + 비지배 줄 = 당기순이익(대칭)인데 지배 줄 ≠ 자본변동표 지배 몫, 비지배 줄 = 자본변동표 지배 몫 → 비지배 이름 줄 값
                             (원문이 지배·비지배 값을 바꿔 적음 — 009070 2022 · 004590 2023 사업보고서)
      MINORITY_NO_CTRL / NO_INCOME_ROW — 유도 불가(플래그) → None. 수집(`fetch`)은 이때 같은 보고서의 XBRL 원본을 본다.
    261020 p1j ctrl-rule-068290(케이스 이름은 그대로): 지배 이름·id 를 붙인 귀속 머리 줄(= 다음 지배 줄 + 비지배 줄 — `_attrib_header`)은 후보에서 빼고,
    「비지배 0」으로만 맞는 후보(= 당기순이익 총계인데 0 이 아닌 비지배 줄이 있다 — `_total_only_fit`)는 0 이 아닌 비지배와 짝이 맞는 후보가 없을 때만 쓴다.
    지배 줄 고르기의 값 구조 검사(지배 + 비지배 = 당기순이익)는 `_ctrl_in_sj` 위 261020 주석."""
    if not any(r.get("sj_div") in ("IS", "CIS") for r in rows):
        return None, "NODATA"
    shape = _ni_shape(rows)
    # 261021 ni-sce: 자본변동표 지배 몫(당기 칸 — `controlling_ni(rows, field)` 는 `_field_rows` 가 같은 기간 칸을 이 자리에 옮겨 둔다).
    # SCE 지배 + 비지배 = 이 행들의 당기순이익일 때만 쓴다. 전년 동기 칸을 넘기는 호출(`scripts/market_fund_quarterly._as_prior_rows`)은 손익 줄과 함께 자본변동표
    # 순이익·중단영업 줄도 전년 동기 누적 칸(분기 자본변동표의 `frmtrm_q_amount`)을 당기 칸 자리에 옮겨 넘긴다 — 같은 기간 칸끼리라 같은 대조가 된다
    # (261022 DATA-NI3R3: 종전엔 자본변동표가 당기 것인 채라 대조가 안 맞아 전기 칸만 `TOTAL` 로 남았다. 274400 2022 반기 −57.12억 → 지배 −55.81억).
    sce = _sce_ctrl_ni(rows, "thstrm_amount", shape)
    rejected, dropped, dropped_v = [], 0, []
    for sj in ("IS", "CIS"):
        v, sub, rej, drp = _ctrl_in_sj(rows, sj, _cum_is, shape, sce)
        dropped += drp
        if v is not None:
            return v, f"{sj}_{sub}" + ("_IDX" if dropped else "")
        rejected += rej
        if drp:
            dropped_v += [_cum_is(r) for r in rows if r.get("sj_div") == sj and _NI_CTRL_ID in (r.get("account_id") or "")
                          and _cum_is(r) is not None]
    # 261020: 버린 총포괄 지배 몫과 「지배 + 비지배 = 총포괄손익」 짝이 맞는 비지배 줄은 총포괄 쪽 비지배라 빼고 찾는다.
    ci_tot = shape[1]
    skip = frozenset(id(y) for y, yv in shape[2] for _rk, c in rejected
                     if ci_tot is not None and abs(_cum_is(c) + yv - ci_tot) <= _amt_tol(ci_tot))
    tot, mino = _total_ni(rows), _minority_ni(rows, skip)
    ci_nci = ""
    if mino is None and skip:
        # 261020 검토: 순이익 쪽 비지배 줄이 아예 없고 총포괄 쪽 비지배만 낸 보고서 — 빼지 않으면 지배 몫이 비지배 순이익 전부만큼 커진다.
        # 총포괄 쪽 비지배로 빼면 비지배 몫의 기타포괄만큼만 어긋난다(10-20 원문 캐시에서 네 줄이 다 있는 1,636건: 비지배 기타포괄 / 비지배 순이익
        # 중앙값 1.1% · 이쪽이 더 가까운 보고서 87% · 다음 해 보고서 전기 칸으로 맞춰 본 6칸 가운데 5칸 — 238090 2021 반기 총계 17.7억 → 23.3억,
        # 참값 23.3억). 값 0 인 줄은 빼지 않는다(종전 표시 그대로). 케이스 끝에 `_CINCI`.
        # 261020 2차 검토(부호 가드): 기타포괄손익이 총포괄 쪽 비지배와 **같은 부호이고 그만큼 이상**이면 그 비지배 몫이 통째로 기타포괄일 수 있다
        # (비지배 순이익 ≈ 0) — 빼면 지배 순이익이 비지배 기타포괄만큼 크게 틀린다(10-20 원문 캐시 044480 2020 1분기: 총계 −1.92억, 기타포괄 9.76억,
        # 총포괄 비지배 9.13억 → 뺀 값 −11.04억). 그때는 빼지 않는다(종전 총계). 반대 부호거나 기타포괄이 더 작으면 뺀 값이 총계와 참값 사이에 있거나
        # 기타포괄 안쪽으로 맞는다. 네 줄이 다 있는 원문 1,668건: 참값 5% 넘게 나아진 칸 605 → 516, 5% 넘게 나빠진 칸 80 → 9.
        oci = (ci_tot - shape[0]) if (ci_tot is not None and shape[0] is not None) else None
        for sj in ("IS", "CIS"):
            v = _first(rows, lambda r: r.get("sj_div") == sj and id(r) in skip and _cum_is(r) is not None, _cum_is)
            if v is not None:
                if v and not (oci is not None and oci * v > 0 and abs(oci) >= abs(v)):
                    mino, ci_nci = v, "_CINCI"
                break
    nm = ""
    if tot is None:                       # 261018: 코드로 못 찾은 총계만 이름으로 — 종전에 값이 나던 행은 그대로다
        tot = _total_ni_nm(rows)
        nm = "_NM" if tot is not None else ""
    # 종전 규칙이 썼을 줄(지배 순이익 id·이름)을 총포괄 몫이라 버렸을 때만 표시 — 총포괄 id 줄은 원래 후보가 아니었다
    rj = "_CIREJ" if any(rk <= 3 for rk, _c in rejected) else ""
    if tot is not None and mino is not None and abs(tot) > _amt_tol(tot) and abs(tot - mino) <= _amt_tol(tot):
        # 261020: 「비지배」 줄 값이 당기순이익 총계와 같다 = 지배 몫 0 — 지배 몫 줄에 비지배 이름을 붙인 회사(10-20 운영 089970 2025 3분기:
        # 「당기순이익 → 비지배지분」 149.7억 = 당기순이익, 총포괄 쪽은 지배 몫 = 총포괄). 비지배를 빼지 않는다.
        mino = None
    if (mino is not None and tot is not None and ci_tot is not None
            and any(abs(_cum_is(c) - ci_tot) <= _amt_tol(ci_tot) for _rk, c in rejected)
            and abs(mino) > abs(ci_tot - tot) + _amt_tol(tot)):
        # 261020: 버린 총포괄 지배 몫 = 총포괄 총계(총포괄의 비지배 몫 0)인데 비지배 순이익이 기타포괄손익보다 크다 — 그 비지배 줄도 잘못 적힌 값이다
        # (10-20 운영 263860 2026 1분기: 지배·비지배 순이익 줄 둘 다 총포괄 19.8억, 당기순이익 19.7억). 비지배를 빼지 않는다.
        mino = None
    ix = "_IDX" if dropped else ""
    if tot is not None:
        v, case = (tot - mino, f"TOTAL{nm}_MINUS_MINORITY{rj}{ci_nci}{ix}") if mino is not None else (tot, f"TOTAL{nm}{rj}{ix}")
        if sce is not None and (rj or ci_nci or ix) and not _near(v, sce):
            # 261021 ni-sce: 지배 줄을 버리고 총계 규칙으로 물러난 길(총포괄 몫이라 버림 `_CIREJ` · 총포괄 쪽 비지배로 뺀 근사 `_CINCI` · 비지배와 짝이 안 맞아
            # 버림 `_IDX`)이 자본변동표 지배 몫과 다르다 — 같은 응답의 후보(총계 − 비지배 줄 하나 · 버린 지배 줄 · 총계) 가운데 SCE 와 같은 값, 없으면 SCE 값.
            #   · 052770 2021 반기(`TOTAL_CIREJ`): 비지배 −23만 원을 총포괄 쪽이라 안 뺐다 −4,734,426,483 → 총계 − 비지배 −4,734,195,889(= SCE).
            #   · 347860 2023 1분기(`TOTAL_CIREJ`): 지배 id 줄에 총포괄 총계 → −88.48억 → 총계 − 비지배 −87.85억(= SCE).
            #   · 145270 2025 사업보고서(`TOTAL_MINUS_MINORITY_IDX`): 손익계산서 비지배 줄 2.97억이 틀려(자본변동표·재무상태표 증가 2.91억) 지배 id 줄을 버렸다
            #     62.10억 → 지배 id 줄 62.16억(= SCE).
            #   · 196490 2020 사업보고서(`TOTAL_MINUS_MINORITY_IDX`): 「비지배지분」 줄 = 기타포괄 −1.90억 · 지배 id 줄 = 비지배 1.18억 → −292.96억 → SCE −296.05억.
            cands = [(tot - y, f"{case}_SCE") for _r, y in shape[2]]
            cands += [(_cum_is(c), f"{case}_SCE") for _rk, c in rejected] + [(x, f"{case}_SCE") for x in dropped_v] + [(tot, f"{case}_SCE")]
            hit = next(((x, c) for x, c in cands if _near(x, sce)), None)
            if hit is not None:
                return hit
            sj_tot = "IS" if any(r.get("sj_div") == "IS" for r in rows) else "CIS"
            return sce, f"{sj_tot}_SCE"
        if (sce is not None and mino is None and not (nm or rj or ci_nci or ix) and not shape[2]
                and not _near(tot, sce)):
            # 261022 DATA-NI3: 순수 TOTAL 길 — 손익계산서에 지배·비지배 귀속 줄이 하나도 없어 총계 = 지배로 읽었는데, 같은 응답의 자본변동표가
            # 지배 몫 + 비지배 몫 = 당기순이익(`_sce_ctrl_ni` 가 그때만 값을 낸다)으로 맞고 지배 몫이 총계와 다르다 = 비지배 몫이 0 이 아니다 → 총계에는 비지배가 들어 있다.
            # 자본변동표 지배 몫을 쓴다(케이스 `TOTAL_SCE`). 운영 217500 2026 1분기 −4.05억 → −594.3만(자본변동표 지배 −594.3만 · 비지배 −3.99억),
            # 081580 FY2023 −4.90억 → −1.62억. 이름으로 찾은 총계(`_NM`)·버린 줄 길(`_CIREJ`·`_CINCI`·`_IDX`)은 위 분기가 이미 다룬다.
            return sce, "TOTAL_SCE"
        return v, case
    return (None, "MINORITY_NO_CTRL") if mino is not None else (None, "NO_INCOME_ROW")


def _sce_field(field: str, reprt_code) -> str | None:
    """손익 칸 → 같은 기간의 자본변동표 칸(261021 ni-sce). 자본변동표는 누적 칸만 있다 — 사업보고서는 당기·전기·전전기, 분기보고서는 당기 누적
    (`frmtrm_add_amount` 짝은 DART 응답의 분기 자본변동표에서 비어 있다 — 전년 동기 누적은 `frmtrm_q_amount` 칸이고, 그 칸은 `market_fund_quarterly._as_prior_rows` 가 직접 읽는다). 반기·3분기 보고서의 당기·전기 칸(`thstrm_amount`·
    `frmtrm_amount` — 3개월)은 맞는 칸이 없다(None). 1분기는 3개월 = 누적."""
    q3m = str(reprt_code or "") in ("11012", "11014")
    return {"thstrm_add_amount": "thstrm_amount", "frmtrm_add_amount": "frmtrm_amount",
            "thstrm_amount": None if q3m else "thstrm_amount", "frmtrm_amount": None if q3m else "frmtrm_amount",
            "bfefrmtrm_amount": "bfefrmtrm_amount"}.get(field)


#: 칸 이름 → 그 칸을 누적 칸 자리에 옮겨 보는 행(아래 `controlling_ni`). 손익 줄만 옮기고 재무상태표 줄은 그대로 둔다.
#: 261021 ni-sce: 자본변동표 당기순이익 줄은 같은 기간의 자본변동표 칸(`_sce_field`)을 당기 칸 자리에 옮기고 다른 칸은 비운다(맞는 칸이 없으면 다 비운다).
def _field_rows(rows, field: str):
    out = []
    for r in rows or []:
        if r.get("sj_div") in ("IS", "CIS"):
            out.append({**r, "thstrm_amount": r.get(field) or "", "thstrm_add_amount": ""})
        elif r.get("sj_div") == "SCE" and (_sce_ni_row(r) or _sce_disc_row(r)):
            col = _sce_field(field, r.get("reprt_code"))
            out.append({**r, "thstrm_amount": (r.get(col) or "") if col else "", "frmtrm_amount": "", "bfefrmtrm_amount": ""})
        else:
            out.append(r)
    return out


def controlling_ni(rows, field: str | None = None) -> tuple[float | None, str]:
    """지배 귀속 순이익 → (값, 케이스) — 케이스 트리는 `extract_controlling_ni_cum`. `field` 가 없으면 손익 누적(줄마다 당기 누적 → 없으면 당기),
    있으면 그 칸(`thstrm_amount`·`frmtrm_amount`·`bfefrmtrm_amount`·`thstrm_add_amount` …)만 본다."""
    if field is None:
        return extract_controlling_ni_cum(rows or [])
    return extract_controlling_ni_cum(_field_rows(rows, field))
