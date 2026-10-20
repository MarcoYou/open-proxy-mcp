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
_EPS_NM = re.compile(r"주당(?:순)?(?:이익|손익|손실)(?!계산)")
_NOT_CTRL_NM = ("공동지배", "지배력")
#: 계속·중단영업 몫 — 하나만으론 합계가 아니다. 두 몫을 더해 한 후보로 본다(중단영업 몫이 없으면 계속영업 몫 = 합계).
_CONT_NM = ("계속영업", "계속사업")
_DISC_NM = ("중단영업", "중단사업")
#: 비지배 줄 이름 — 「비재배」는 원문 오타(10-20 운영 130740 2023 3분기~2024 3분기 「비재배지분귀속당기순이익」 · 캐시 1건)
_NCI_NM = ("비지배", "비재배")
_CI_TOTAL_NM = re.compile(r"^(?:당기|당반기|반기|당분기|분기)?총포괄(?:이익|손익|손실)(?:\((?:이익|손실)\))?$")
_OCI_TOTAL_NM = re.compile(r"^(?:총|당기|반기|분기)?기타포괄(?:이익|손익|손실)(?:\((?:이익|손실)\))?$")


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


def ni_attrib_side(v, shape) -> str | None:
    """지배 몫 후보 값 `v` 가 어느 쪽 구조에 맞나 — 'ni'(지배 + 비지배 = 당기순이익) · 'ci'(당기순이익엔 안 맞고 총포괄손익 총계에만 맞음 —
    총포괄이익의 지배 몫) · None(가를 수 없다: 총계가 없거나 어느 쪽에도 안 맞음). 기타포괄손익이 허용 오차 안이면 두 값이 같아 'ni'."""
    tot, ci, nci = shape
    if _fits(v, tot, nci):
        return "ni"
    if tot is not None and ci is not None and abs(ci - tot) > _amt_tol(tot) and _fits(v, ci, nci):
        return "ci"
    return None


def _ctrl_cands(rows, sj, val):
    """한 재무제표(sj) 안 지배 몫 후보 → [(순위, ord, 값, 서브케이스, 줄)] 순위순, 값이 빈 줄은 뺀다.
    순위 0 = 지배 순이익 id · 1 = 이름 · 2 = 이름에 「포괄」 · 3 = 계속영업 몫 + 중단영업 몫(`PARTS`) · 4 = 총포괄 id(`CIID`)."""
    out, cont, disc = [], [], []
    for r in rows:
        if r.get("sj_div") != sj or val(r) is None:
            continue
        aid, nm = r.get("account_id") or "", _nm(r)
        if _NI_CTRL_ID in aid:
            out.append((0, _ordv(r), val(r), "ID", r))
            continue
        if any(k in nm for k in _NCI_NM) or "PerShare" in aid or _EPS_NM.search(nm) or any(k in nm for k in _NOT_CTRL_NM):
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


def _ctrl_in_sj(rows, sj, val, shape=None):
    """한 재무제표(sj) 안 지배 귀속 순이익 → (값, 서브케이스 'ID'|'NM'|'PARTS'|'CIID'|None, 총포괄 몫이라 버린 줄 [(순위, 줄)], 비지배와 짝이 안 맞아 버린 지배 id 줄 수).
    값 구조 검사는 위 261020 주석. `shape` = `_ni_shape(rows)`(없으면 계산). IS/CIS 는 ord 시퀀스 별개 → sj 단위."""
    shape = shape or _ni_shape(rows)
    tot = shape[0]
    fallback, rejected, unk, dropped = None, [], [], 0
    tot_ok = None                         # 총계 줄을 믿을 수 있나(`_tot_corroborated`) — 필요할 때 한 번만 센다
    for rank, _o, v, sub, r in _ctrl_cands(rows, sj, val):
        side = ni_attrib_side(v, shape)
        if rank == 0 and _id_off_nci(v, r, shape, side) and (tot_ok if tot_ok is not None else (tot_ok := _tot_corroborated(rows, tot))):
            # 261020 p1e firm-ctrl-ni: 비지배 줄과 짝이 안 맞는 지배 id 줄(`_id_off_nci`) — 혼자서는 쓰지 않는다. 다만 계속·중단영업 몫을 나눠 적은 회사의
            # 한 몫일 수 있어 아래 「두 줄의 합」에는 넣는다(10-20 원문 049630 2024 3분기: 「지배기업의 소유주(계속)」 27.78억 이 id 줄,
            # 「지배기업의 소유주(중단)」 −3.45억 이 이름 줄 — 합 24.34억 + 비지배 1.21억 = 당기순이익 25.55억).
            dropped += 1
            unk.append(v)
            continue
        if side == "ni":
            return v, sub, rejected, dropped
        if side == "ci":
            rejected.append((rank, r))
        elif rank <= 3 and not (v == 0 and tot is not None and abs(tot) > _amt_tol(tot)):
            # 가를 수 없는 줄 — 종전처럼 쓴다(총포괄 id 줄은 값 구조가 맞을 때만). 당기순이익이 있는데 지배 몫이 0 인 줄은 빈 줄로 본다
            # (10-20 운영 045660 2025 1분기: 「당기순이익 → 지배기업소유주지분」 0, 당기순이익 165.7억 · 비지배 없음).
            unk.append(v)
            fallback = fallback or (v, sub)
    # 두 줄의 합이 순이익 구조에 맞으면 계속·중단영업 몫을 이름 없이 나눠 적은 회사다(머리 줄 「계속영업분기순이익」·「중단영업분기순이익」 아래
    # 「지배기업소유주지분」 두 줄 — 10-20 운영 101530 2020 3분기). 한 줄만 쓰면 한 몫만 들어간다.
    for i in range(len(unk)):
        for j in range(i + 1, len(unk)):
            if unk[i] and unk[j] and ni_attrib_side(unk[i] + unk[j], shape) == "ni":
                return unk[i] + unk[j], "PARTS", rejected, 0
    if fallback is not None and not dropped:
        # 지배 id 줄을 비지배와 짝이 안 맞아 버렸으면 가를 수 없는 이름 줄로도 물러나지 않는다 — 총계 − 비지배(값 구조가 맞는 길)로 간다.
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
_NI_TOTAL_NM = re.compile(r"^(?:당기|당반기|반기|당분기|분기)?순(?:이익|손실|손익)(?:\((?:이익|손실)\))?$")


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
      {IS,CIS}_CIID        — 총포괄 계정 id 를 붙인 줄인데 값 구조가 순이익(순이익 귀속 줄에 총포괄 id — 056360) (261020)
      TOTAL_MINUS_MINORITY — 명시 지배행 없으나 당기순이익 총계 − 비지배순이익 [유한양행형: 서브토탈 부재]
      TOTAL                — 지배·비지배 귀속 둘 다 부재 → 당기순이익 총계=지배 [001340·NAVER 무소수지분]
      TOTAL_NM[_MINUS_MINORITY] — 위와 같은데 총계 행을 코드가 아니라 이름으로 찾음(261018, 표준 계정코드 미사용 회사)
      …_CIREJ              — 위 총계 규칙인데 지배 줄(id·이름)이 총포괄이익의 지배 몫이라 버린 보고서(261020)
      …_CINCI              — 순이익 쪽 비지배 줄이 없어 총포괄 쪽 비지배 줄(버린 지배 몫과 짝)로 뺀 보고서(261020 검토 — 근사, 비지배 몫 기타포괄만큼 어긋남.
                             기타포괄손익이 그 비지배와 같은 부호로 그만큼 이상이면 빼지 않는다 — 2차 검토 부호 가드)
      …_IDX                — 지배 순이익 계정 id 줄이 순이익 쪽 비지배 계정 줄과 짝이 안 맞아(지배 + 비지배 ≠ 당기순이익) 버린 보고서(261020 p1e firm-ctrl-ni,
                             `_id_off_nci` — 결산기 칸 규칙에서 합침)
      MINORITY_NO_CTRL / NO_INCOME_ROW — 유도 불가(플래그) → None. 수집(`fetch`)은 이때 같은 보고서의 XBRL 원본을 본다.
    지배 줄 고르기의 값 구조 검사(지배 + 비지배 = 당기순이익)는 `_ctrl_in_sj` 위 261020 주석."""
    if not any(r.get("sj_div") in ("IS", "CIS") for r in rows):
        return None, "NODATA"
    shape = _ni_shape(rows)
    rejected, dropped = [], 0
    for sj in ("IS", "CIS"):
        v, sub, rej, drp = _ctrl_in_sj(rows, sj, _cum_is, shape)
        dropped += drp
        if v is not None:
            return v, f"{sj}_{sub}" + ("_IDX" if dropped else "")
        rejected += rej
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
        return (tot - mino, f"TOTAL{nm}_MINUS_MINORITY{rj}{ci_nci}{ix}") if mino is not None else (tot, f"TOTAL{nm}{rj}{ix}")
    return (None, "MINORITY_NO_CTRL") if mino is not None else (None, "NO_INCOME_ROW")


#: 칸 이름 → 그 칸을 누적 칸 자리에 옮겨 보는 행(아래 `controlling_ni`). 손익 줄만 옮기고 재무상태표 줄은 그대로 둔다.
def _field_rows(rows, field: str):
    return [{**r, "thstrm_amount": r.get(field) or "", "thstrm_add_amount": ""} if r.get("sj_div") in ("IS", "CIS") else r
            for r in rows or []]


def controlling_ni(rows, field: str | None = None) -> tuple[float | None, str]:
    """지배 귀속 순이익 → (값, 케이스) — 케이스 트리는 `extract_controlling_ni_cum`. `field` 가 없으면 손익 누적(줄마다 당기 누적 → 없으면 당기),
    있으면 그 칸(`thstrm_amount`·`frmtrm_amount`·`bfefrmtrm_amount`·`thstrm_add_amount` …)만 본다."""
    if field is None:
        return extract_controlling_ni_cum(rows or [])
    return extract_controlling_ni_cum(_field_rows(rows, field))
