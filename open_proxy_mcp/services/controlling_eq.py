"""지배자본(지배기업 소유주 귀속 자본) 줄 고르기 — 분기·연간 수집과 서버 실시간 firm·결산기 칸이 함께 쓰는 한 규칙(261020 p1f eq-suspects).

DART 전체 재무제표(fnlttSinglAcntAll) 응답의 재무상태표(BS) 행에서 **지배자본 기말 잔액**을 고른다. 트리는 분기 수집
(`scripts/market_fund_quarterly.extract_controlling_eq`)이 쓰던 그대로다 — 지배자본 계정 줄(BS_ID) → 이름 줄(BS_NM) → 자본총계 − 비지배지분
(TOTAL_MINUS_MINORITY) → 자본총계(TOTAL). 이 모듈이 더한 것은 **지배자본 줄로 믿지 않을 줄**의 판정(`ctrl_line_rejected`)이다.

왜(261020 p1f eq-suspects — 원문 캐시 7,513보고서 · 운영 칸): 회사가 지배자본 계정(`…EquityAttributableToOwnersOfParent`)을 엉뚱한 줄에 붙인 보고서가
「자본금」 줄(261019 `_capital_line` 이 이미 거름) 말고도 있었다.
  · 자본 구성요소 줄 — 082660 2021 3분기 「기타자본구성요소」 75.55억(자본총계 257.05억 · 비지배 0.15억 → 지배자본 256.90억).
  · 이름은 지배자본인데 값이 자본금 — 065370 2023 3분기 「지배기업의소유주에게귀속되는자본」 36.92억(자본총계 323.72억, 비지배 없음,
    다른 구성요소 합 286.80억 = 323.72 − 36.92 — 자본금 줄이 따로 없다). `_capital_line` 은 같은 보고서에 자본금 계정 줄이 있어야 값으로 잡는다.
  · 일부만 더한 소계 — 060230 FY2018 「지배기업의소유주에게귀속되는자본」 −66.02억 = 자본금 253.61억 + 결손금 −319.64억(주식발행초과금 515.98억 등 빠짐),
    비지배 0 · 자본총계 324.48억.
  그리고 이름 규칙(BS_NM — 「지배」가 든 줄)이 자본이 아닌 줄을 골랐다 — 082800 2019 1분기 「지배지분손익」 −778.49억(= 결손금 줄 값, 자본총계 677.67억).
  같은 이름 규칙은 「관계기업및공동지배기업투자주식」(자산)·「지배기업의소유주에게귀속되지않는자본」(비지배) 같은 줄도 고를 수 있었다.
  서버 실시간 firm(`price_multiple_data._ctrl_equity`)·결산기 칸(`period_cells.ctrl_eq`)은 지배자본 계정 줄을 그대로 썼다 — 자본금 줄을 거르는 규칙조차
  없어 317240·050090·419050 같은 보고서가 최신이면 실시간 PBR 이 그 값으로 나갔다.

판정(`ctrl_line_rejected` — 값이 있는 지배자본 후보 줄 하나):
  CAPLINE  — 이름이 자본금·납입자본·보통주자본금이거나 값이 같은 재무제표의 자본금 계정(`…_IssuedCapital`) 줄과 원 단위로 같다(261019 그대로).
  COMPLINE — 이름이 자본 구성요소(자본금·잉여금·결손금·기타자본·자본조정·기타포괄손익누계액·적립금·자기주식·신종자본증권 …)이고 지배·소유주·귀속·
             총계 같은 말이 없다.
  OFFTOTAL — 같은 재무상태표의 자본총계가 있고, 그 줄 값이 자본총계 − 비지배지분(비지배 줄이 없으면 0)과 부호가 다르거나 1.6배 넘게 다르다.
             원문 캐시의 지배자본 계정 줄 12,673칸(당기·전기) 가운데 이 항등식이 0.5% 밖으로 어긋난 66칸을 다 보니, 1.6배를 넘는 것은 모두 위의
             잘못 붙인 줄이었고(자본금·구성요소·부분 소계 — 18칸), 맞는 줄이 어긋난 경우(비지배 계정 없이 비지배를 둔 회사 143240 · 부호를 바꿔
             적은 비지배 145720 · 소계 8억 차 091340)는 모두 1% 안이었다. 그래서 작은 어긋남은 종전대로 계정 줄을 믿는다.
거른 줄은 지배자본 줄로 보지 않고 다음 규칙으로 간다. 케이스 끝에 까닭을 붙인다(예 `TOTAL_MINUS_MINORITY_CAPLINE` · `TOTAL_OFFTOTAL`). 다른 길이
없으면 값 없음(`CTRL_IS_CAPITAL_LINE` · `CTRL_LINE_REJECTED`).
"""
from __future__ import annotations

import re

from open_proxy_mcp.services.controlling_ni import _ENUM_PREFIX, _first, _nm, _ordv
from open_proxy_mcp.services.financial_metrics import normalize_amount

EQ_CTRL_ID = "EquityAttributableToOwnersOfParent"

#: 261019: 지배자본 계정을 붙인 납입자본 줄의 이름(번호 머리를 뗀 뒤 전체 일치).
CAPITAL_LINE_NM = re.compile(r"^(?:납입자본|자본금|보통주자본금)$")
#: 자본 구성요소 줄 이름(부분 일치) — 이 말이 들고 아래 `_CTRL_WORD` 가 없으면 지배자본 소계가 아니다.
COMPONENT_NM = re.compile(r"자본금|납입자본|불입자본|잉여금|결손금|기타자본|자본조정|포괄손익|적립금|자기주식|신종자본|주식선택권|전환권대가")
_CTRL_WORD = re.compile(r"지배|소유주|귀속|총계|총자본")
#: 이름 규칙(BS_NM)에서 뺄 말 — 비지배·공동지배기업(투자 자산)·손익·「귀속되지 않는」 줄.
_NOT_CTRL_NM = re.compile(r"비지배|공동지배|관계기업|투자|손익|이익|손실|되지않|아닌|부채")
#: OFFTOTAL 문턱 — 자본총계 − 비지배와 이만큼(배) 넘게 다르면 그 줄을 믿지 않는다.
OFF_TOTAL_RATIO = 1.6


def amount(r, field: str = "thstrm_amount") -> float | None:
    v = normalize_amount(r.get(field))
    return float(v) if v is not None else None


def total_eq(rows, val) -> float | None:
    """자본총계 — `…_Equity` 계정(정확히 그 끝 — 부채와자본총계 `…EquityAndLiabilities` 는 아니다) → 없으면 이름 「자본총계」."""
    v = _first(rows, lambda r: r.get("sj_div") == "BS" and (r.get("account_id") or "").endswith("_Equity"), val)
    if v is not None:
        return v
    return _first(rows, lambda r: r.get("sj_div") == "BS" and _nm(r) == "자본총계", val)


def minority_eq(rows, val) -> float | None:
    """비지배지분(자본). 261019: 이름에 「부채」가 든 줄은 뺀다 — 「비지배지분부채」(펀드 출자자 몫 같은 부채 계정)를 비지배 **자본**으로 읽어
    지배자본 = 자본총계 − 그 부채가 되어 음수가 됐다(10-19 운영 100790 2023 3분기~2026 반기 9칸 −2,602억~−3,895억, 같은 보고서 지배자본 3,563억)."""
    v = _first(rows, lambda r: r.get("sj_div") == "BS" and "NoncontrollingInterests" in (r.get("account_id") or "")
               and "부채" not in _nm(r), val)
    if v is not None:
        return v
    return _first(rows, lambda r: r.get("sj_div") == "BS" and "비지배" in _nm(r) and "부채" not in _nm(r), val)


def capital_line(r, rows, val) -> bool:
    """지배자본 계정이 붙은 줄 `r` 이 실은 납입자본(자본금) 줄인가 — 이름이 자본금·납입자본이거나, 값이 같은 재무제표의 자본금 계정
    (`…_IssuedCapital`) 줄과 원 단위로 같다. 지배자본이 자본금과 원 단위까지 같은 회사는 없다(이익잉여금·자본잉여금이 붙는다) —
    혹 그렇더라도 다음 규칙(총계)이 같은 값을 낸다. (261019 — `market_fund_quarterly._capital_line` 에서 옮김)"""
    if CAPITAL_LINE_NM.match(_ENUM_PREFIX.sub("", _nm(r), count=1)):
        return True
    v = val(r)
    if v is None:
        return False
    for x in rows:
        if x is r or x.get("sj_div") != r.get("sj_div") or not (x.get("account_id") or "").endswith("_IssuedCapital"):
            continue
        c = val(x)
        if c is not None and abs(c - v) < 0.5:
            return True
    return False


def component_line(r) -> bool:
    """이름이 자본 구성요소(잉여금·기타자본구성요소·자본조정·기타포괄손익누계액 …)이고 지배자본 소계를 뜻하는 말이 없다."""
    nm = _ENUM_PREFIX.sub("", _nm(r), count=1)
    return bool(COMPONENT_NM.search(nm)) and not _CTRL_WORD.search(nm)


def off_total(v: float | None, rows, val) -> bool:
    """값 `v` 가 같은 재무상태표의 자본총계 − 비지배지분(없으면 0)과 부호가 다르거나 `OFF_TOTAL_RATIO` 배 넘게 다른가. 자본총계가 없으면 False(볼 수 없음)."""
    if v is None:
        return False
    tot = total_eq(rows, val)
    if tot is None:
        return False
    return off_pair(v, tot - (minority_eq(rows, val) or 0.0))


def off_pair(v: float, d: float) -> bool:
    """지배자본 후보 `v` 와 자본총계 − 비지배 `d` 가 부호가 다르거나 `OFF_TOTAL_RATIO` 배 넘게 다른가(1원 밑은 1원으로 본다)."""
    if v == d:
        return False
    if (v > 0 and d < 0) or (v < 0 and d > 0):
        return True
    a, b = abs(v), abs(d)
    return max(a, b) > OFF_TOTAL_RATIO * max(min(a, b), 1.0)


def ctrl_line_rejected(r, rows, val) -> str | None:
    """지배자본 후보 줄 `r` 을 믿지 않을 까닭 — 'CAPLINE' · 'COMPLINE' · 'OFFTOTAL' 또는 None(믿는다). `val` = 줄 → 값(칸 고르기)."""
    if capital_line(r, rows, val):
        return "CAPLINE"
    if component_line(r):
        return "COMPLINE"
    if off_total(val(r), rows, val):
        return "OFFTOTAL"
    return None


def ctrl_name(r) -> bool:
    """이름 규칙(BS_NM) 후보 — 이름에 「지배」가 들고 비지배·공동지배기업·손익·「귀속되지 않는」 같은 말이 없다."""
    nm = _nm(r)
    return "지배" in nm and not _NOT_CTRL_NM.search(nm)


_RANK = ("CAPLINE", "COMPLINE", "OFFTOTAL")


def controlling_eq(rows, field: str = "thstrm_amount") -> tuple[float | None, str]:
    """지배자본 기말잔액 → (값, 케이스). 트리:
      NODATA · BS_ID/BS_NM — 명시 지배자본행(값이 있고 `ctrl_line_rejected` 가 None 인 줄, ord 가 가장 앞선 것)
      TOTAL_MINUS_MINORITY — 명시 지배행 없으나 자본총계 − 비지배지분 [유한양행형: 서브토탈 부재]
      TOTAL                — 지배·비지배 자본 둘 다 부재 → 자본총계=지배 [카카오뱅크 별도·001340]
      MINORITY_NO_CTRL / NO_EQUITY_ROW — 유도 불가(플래그) → None. 분기 수집(`fetch`)은 이때 같은 보고서의 XBRL 원본을 본다.
    거른 줄이 있으면 케이스 끝에 `_CAPLINE`·`_COMPLINE`·`_OFFTOTAL`(앞선 것 하나)을 붙인다. 다른 길이 없으면 `CTRL_IS_CAPITAL_LINE`(자본금 줄)·
    `CTRL_LINE_REJECTED`(그 밖) — 값 없음. 261018: 지배자본 행이 있는데 칸이 빈 보고서는 값이 있는 행만 지배자본 행으로 본다(빈 행은 다음 규칙)."""
    val = lambda r: amount(r, field)  # noqa: E731
    if not any(r.get("sj_div") == "BS" for r in rows):
        return None, "NODATA"
    rej: dict[int, str] = {}
    hit = [r for r in rows if r.get("sj_div") == "BS" and EQ_CTRL_ID in (r.get("account_id") or "") and val(r) is not None]
    for r in hit:
        why = ctrl_line_rejected(r, rows, val)
        if why:
            rej[id(r)] = why
    keep = [r for r in hit if id(r) not in rej]
    if keep:
        return val(min(keep, key=_ordv)), "BS_ID" + _sfx(rej)
    cands = [r for r in rows if r.get("sj_div") == "BS" and EQ_CTRL_ID not in (r.get("account_id") or "") and ctrl_name(r)
             and val(r) is not None]
    for r in cands:
        why = ctrl_line_rejected(r, rows, val)
        if why:
            rej[id(r)] = why
    cands = [r for r in cands if id(r) not in rej]
    if cands:
        return val(min(cands, key=_ordv)), "BS_NM" + _sfx(rej)
    tot, mino = total_eq(rows, val), minority_eq(rows, val)
    if tot is not None:
        return (tot - mino, "TOTAL_MINUS_MINORITY" + _sfx(rej)) if mino is not None else (tot, "TOTAL" + _sfx(rej))
    if rej:
        return None, ("CTRL_IS_CAPITAL_LINE" if "CAPLINE" in rej.values() else "CTRL_LINE_REJECTED")
    return (None, "MINORITY_NO_CTRL") if mino is not None else (None, "NO_EQUITY_ROW")


def _sfx(rej: dict) -> str:
    if not rej:
        return ""
    got = set(rej.values())
    return "_" + next(k for k in _RANK if k in got)


def ctrl_id_value(rows, field: str = "thstrm_amount", prefixes: tuple[str, ...] = ("ifrs-full_",)) -> tuple[float | None, str | None]:
    """지배자본 계정 줄(정확 일치 — `prefixes` 순서로)의 값과, 그 줄을 믿지 않을 까닭. 줄이 없으면 (None, None).
    서버 실시간 firm·결산기 칸·연간 수집처럼 「계정 줄 → 없으면 총계 쪽」 규칙을 쓰는 곳이 계정 줄을 쓰기 전에 부른다."""
    val = lambda r: amount(r, field)  # noqa: E731
    for p in prefixes:
        row = next((r for r in rows if r.get("sj_div") == "BS" and r.get("account_id") == p + EQ_CTRL_ID and str(r.get(field) or "") != ""),
                   None)
        if row is not None:
            return val(row), ctrl_line_rejected(row, rows, val)
    return None, None
