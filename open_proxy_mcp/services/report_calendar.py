"""DART 정기보고서 칸 ↔ 실제 기간 말·공시 시점 — 회사 결산월을 아는 매핑(261018 재설계 6단계).

분기 재무 표(`dart_finstat_q`)·연간 재무 표(`dart_finstat_y`)의 칸 `(fy, quarter)` 는 DART 의 `(bsns_year, reprt_code)` 를 그대로 담는다
(quarter 1 = 1분기보고서 11013 · 2 = 반기보고서 11012 · 3 = 3분기보고서 11014 · 4 = 사업보고서 11011). DART 는 사업연도 표기를
**그 보고서 기준일(기간 말)이 속한 달력 연도**로 붙인다 — 2026-10-18 DART 직접 대조(결산월 2·3·6·8·9·12월 6개사, 칸마다 접수번호 → 보고서 이름):

    결산월 12월(삼성전자):     2026 1분기 = 2026.03 · 반기 = 2026.06 · 3분기 = 2026.09 · 사업 = 2026.12
    결산월  3월(동원모빌리티): 2026 1분기 = 2026.06 · 반기 = 2026.09 · 3분기 = 2026.12 · 사업 = 2026.03
    결산월  6월(MH머트리얼즈): 2026 1분기 = 2026.09 · 반기 = 2026.12 · 3분기 = 2026.03 · 사업 = 2026.06
    결산월  9월(방림):         2026 1분기 = 2026.12 · 반기 = 2026.03 · 3분기 = 2026.06 · 사업 = 2026.09
    (2월·8월도 같은 규칙 — 칸의 기간 말 달 = 결산월 + 3 × 보고서 순번, 해 = 그 사업연도 표기)

그래서 12월 결산이 아니면 같은 `fy` 안의 네 칸이 서로 다른 회계연도·달력 분기를 가리킨다. 종전 읽는 쪽은 칸을 **달력 분기**(1분기 = 3월 말 …)로
읽어, 아직 안 나온 보고서를 앞당겨 쓰고(3월 결산의 「2026 반기」 칸은 9월 말 기준이라 11/14 마감인데 8/14 부터 쓰려 했다) TTM 의 직전 연간 값이
한 해 어긋났다. 이 모듈이 칸 → 실제 기간 말 → 공시 시점을 결산월로 계산한다.

공시 시점(그 날부터 보고서가 나와 있다고 본다 — look-ahead 방지):
  · 분기·반기보고서: 기간 말 + 45일(법정 제출 기한). 12월 결산이면 5/15 · 8/14 · 11/14 — 종전 규칙과 같다.
  · 사업보고서: 결산월 다음 넷째 달 1일(제출 기한 결산 뒤 90일의 다음 달 초). 12월 결산이면 이듬해 4/1 — 종전 규칙과 같다.

12월 결산(`fye=12`, 결산월을 모를 때의 기본값)은 종전 함수와 **결과가 같다** — `pit_cell`·`pit_annual` 은 12월이면 종전 식을 그대로 쓰고,
일반 식이 12월에서 종전 식과 같은지는 시험이 2016~2031년 모든 날로 대조한다.

외국 상장사(종목코드 9로 시작)는 제출 기한이 길다 — 분기·반기 60일, 사업보고서 120일(2024~2026 DART 접수일 실측: 씨엑스아이·GRT 사업보고서
결산 뒤 115~120일, 분기 59~62일). 12월 결산이 아닌 외국 상장사는 그 기한으로 본다(분기 기간 말 + 60일, 사업보고서 결산월 다음 다섯째 달 1일) —
45·90일로 보면 아직 안 낸 보고서 칸을 골라 값이 비었다. **12월 결산 외국 상장사는 종전 그대로**(45·90일 — 12월 결산 응답을 바꾸지 않는다).

결산월을 바꾼 회사(261021, DATA-FY2): 회사마다 결산월이 **하나**가 아니다. `Fiscal.month` = 지금(가장 최근 사업보고서) 결산월, `prev` = 바꾸기 전 옛 결산월,
`since` = 새 결산월의 첫 사업연도 표기(전환기 사업보고서의 표기). 사업연도 표기 `fy` 의 결산월은 `month_of(fy)` — `fy < since` 면 옛 결산월, 아니면 새 결산월이다.
`cell_end`·`cell_available`·`pit_cell`·`pit_annual`·`prior_annual`·`latest_annual` 이 모두 이 규칙을 따른다. 결산월을 안 바꾼 회사(`since` 가 비었다)는 종전 식 그대로다.

바꾼 해(`since`)의 사업보고서는 **전환기**다 — 12개월이 아니다(334970: 2023.01~06 6개월 · 154030: 2025.10~12 3개월 · 222160: 2024.04~12 9개월). 그 칸의 순이익은 연간 값이
아니라서 연간 칸에 넣지 않고 읽는 쪽도 가린다(`is_transition`). 자본은 그 날의 잔액이라 그대로 쓴다. 새 결산월이 옛 결산월보다 늦은 회사(9→12·3→12)는 같은 사업연도 표기에
사업보고서가 둘(옛 결산기 · 전환기)이라 DART 가 늦은 것(전환기)만 준다 — 옛 결산기 12개월 값은 이 표기의 칸으로 받을 수 없다(`prior_columns` 가 전기·전전기 칸의 자리를 가른다).
한계: 결산월을 두 번 이상 바꾼 회사는 가장 최근 변경만 안다. 바꾼 해의 1~3분기 칸(`fy == since`)은 옛 결산월로 낸 분기·반기보고서가 새 결산월의 같은 표기 칸에 들어 있을 수
있어(154030 2025 반기 칸 = 2025.03 옛 결산월 반기) 기간 말이 틀릴 수 있다 — 틀려도 아직 안 나온 보고서를 앞당겨 쓰지는 않는다(공시 시점이 더 늦게 잡힌다).
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from functools import lru_cache
from typing import NamedTuple

#: 분기 표 quarter → 결산월에서 몇 달 뒤가 그 칸의 기간 말인가(사업보고서 = 결산월 그 자체).
_STEP = {1: 3, 2: 6, 3: 9, 4: 0}
#: 분기·반기보고서 제출 기한(기간 말 뒤 일수).
QUARTERLY_LAG_DAYS = 45
#: 사업보고서: 결산월 뒤 이 달 수만큼 지난 달의 1일부터 본다(결산 뒤 90일 + 다음 달 초 — 12월 결산 4/1).
ANNUAL_LAG_MONTHS = 4
#: 외국 상장사(12월 결산이 아닐 때만): 분기·반기 60일 · 사업보고서 120일(+ 다음 달 초).
FOREIGN_QUARTERLY_LAG_DAYS = 60
FOREIGN_ANNUAL_LAG_MONTHS = 5


class Fiscal(NamedTuple):
    """회사의 공시 달력 — 결산월과 외국 상장사 여부(제출 기한). 결산월 자리에 int 대신 넘길 수 있다.

    261020(p1e): `cells` = 반기·분기 결산 회사(리츠 등)의 **결산기 칸** `((fy, quarter, 공시일 YYYYMMDD), …)` — 분기 재무 표에서 근거 칸 머리가
    `period_cells.MARK` 인 칸(결산기 합으로 쓴 칸)과 그 칸에 쓴 보고서의 최초 접수일. 있으면 `pit_cell` 이 이 칸들 가운데 그날까지 공시된
    가장 늦은 칸을 고른다(공시 기한 규칙 대신 실제 접수일 — 결산기 사업보고서는 결산 뒤 90일 안이라 45일 규칙이면 미래 정보다).
    `pit_annual`(FY0 의 사업연도)은 달력 규칙 그대로다(firm 의 FY0 와 같은 정의).

    261021(DATA-FY2): `prev`·`since` = 결산월을 바꾼 회사의 옛 결산월과 새 결산월(`month`)의 첫 사업연도 표기. 둘 다 0 이면 안 바꾼 회사(종전 식 그대로).
    `month_of(fy)` 가 표기마다 맞는 결산월을 돌려준다."""
    month: int = 12
    foreign: bool = False
    cells: tuple = ()
    prev: int = 0
    since: int = 0


def _norm_change(month: int, prev, since) -> tuple[int, int]:
    """(옛 결산월, 새 결산월 첫 사업연도) — 둘 다 올바를 때만. 아니면 (0, 0) = 안 바꾼 회사. 결산월이 같거나 범위 밖이면 변경으로 보지 않는다."""
    try:
        p, s = int(prev), int(since)
    except (TypeError, ValueError):
        return 0, 0
    return (p, s) if 1 <= p <= 12 and p != month and 1900 <= s <= 2200 else (0, 0)


def fiscal(acc_mt, ticker: str | None = None, prev=None, since=None) -> Fiscal:
    """`dart_fundamentals.acc_mt` + 종목코드 → 공시 달력. 외국 상장사 기한은 12월 결산이 아닐 때만(12월 결산은 종전 그대로).

    261021: `prev`·`since` = 결산월을 바꾼 회사의 옛 결산월·새 결산월 첫 사업연도(`dart_fundamentals.acc_mt_prev`·`acc_mt_since`) — 둘 다 올바를 때만 쓴다.
    `acc_mt` 자리에 `Fiscal`(`load_fye_map` 이 결산월을 바꾼 회사에 주는 값)을 넘기면 그 변경을 이어받는다."""
    if isinstance(acc_mt, Fiscal):
        m = acc_mt.month
        prev = acc_mt.prev if prev is None else prev
        since = acc_mt.since if since is None else since
        cells = acc_mt.cells
    else:
        m, cells = fye_month(acc_mt), ()
    p, s = _norm_change(m, prev, since)
    return Fiscal(m, bool(ticker) and str(ticker).startswith("9") and m != 12, cells, p, s)


def _as_fiscal(v) -> Fiscal:
    if isinstance(v, Fiscal):
        return v if v.month != 12 else Fiscal(12, False, v.cells, v.prev, v.since)
    return Fiscal(fye_month(v), False)


def with_cells(fye, cells) -> Fiscal:
    """공시 달력에 결산기 칸을 붙인다(261020 p1e) — `cells` = {(fy, quarter): 공시일} 또는 ((fy, quarter, 공시일), …). 비었으면 그대로."""
    f = fye if isinstance(fye, Fiscal) else Fiscal(fye_month(fye), False)
    if not cells:
        return f
    items = cells.items() if isinstance(cells, dict) else ((c[0], c[1]) if len(c) == 2 else ((c[0], c[1]), c[2]) for c in cells)
    norm = tuple(sorted((int(k[0]), int(k[1]), str(a)) for k, a in items if a))
    return f._replace(cells=norm) if norm else f


def cell_open(fy: int, quarter: int, dd, fye) -> bool:
    """칸 `(fy, quarter)` 를 그날 쓸 수 있나 — 결산기 칸(`Fiscal.cells`)이 있는 회사는 그 칸의 공시일, 없으면 공시 기한 규칙(`cell_available`)."""
    f = _as_fiscal(fye)
    t = _as_date(dd).strftime("%Y%m%d")
    if f.cells:
        return any(c[0] == fy and c[1] == quarter and c[2] <= t for c in f.cells)
    return cell_available(fy, quarter, f) <= _as_date(dd)


def fye_month(v) -> int:
    """결산월(1~12). 비었거나 이상하면 12(상장사 대다수 · 종전 가정). `Fiscal` 이면 그 결산월."""
    if isinstance(v, Fiscal):
        return v.month
    try:
        m = int(str(v).strip())
    except (TypeError, ValueError):
        return 12
    return m if 1 <= m <= 12 else 12


def month_of(fy: int, fye=12) -> int:
    """사업연도 표기 `fy` 의 결산월 — 결산월을 바꾼 회사는 새 결산월의 첫 사업연도(`since`) 앞 표기를 옛 결산월로 읽는다. 안 바꾼 회사·int 는 `fye_month`."""
    if isinstance(fye, Fiscal):
        return fye.prev if (fye.since and fy < fye.since) else fye.month
    return fye_month(fye)


def is_transition(fy: int, fye=12) -> bool:
    """사업연도 표기 `fy` 의 사업보고서가 결산월을 바꾼 **전환기**(12개월이 아님)인가 — 새 결산월의 첫 사업보고서(`since`). 안 바꾼 회사는 늘 False."""
    return bool(isinstance(fye, Fiscal) and fye.since and fy == fye.since)


def mask_transition(fin, fye=12):
    """연간 재무 `{표기: (순이익, 자본)}`(`restated_pit.PitFin` 이어도 된다)의 **전환기 표기 순이익을 가린다**(제자리에서 바꾸고 돌려준다) — 결산월을 바꾼 첫 사업보고서는
    12개월이 아니라 연간 순이익이 아니다(`is_transition`). 자본은 그 날 잔액이라 둔다. 정정 칸의 문(출처 공시 전 값)도 같은 순이익을 가린다. 안 바꾼 회사는 그대로."""
    if not isinstance(fye, Fiscal) or not fye.since or fye.since not in fin:
        return fin
    k = fye.since
    fin[k] = (None, fin[k][1])
    g = getattr(fin, "gates", {}).get(k)
    if g is not None:
        fin.gate(k, (g[0], (None, g[1][1])))
    return fin


def _last_day(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _add_months(y: int, m: int, n: int) -> tuple[int, int]:
    k = (m - 1) + n
    return y + k // 12, k % 12 + 1


def cell_month(quarter: int, fye: int = 12) -> int:
    """그 칸 기간 말의 달."""
    return (fye + _STEP[quarter] - 1) % 12 + 1


@lru_cache(maxsize=4096)
def cell_end(fy: int, quarter: int, fye=12) -> date:
    """칸 `(fy, quarter)` 의 기간 말(그 달 말일). 해는 DART 표기 그대로 `fy`. 결산월은 그 표기의 결산월(`month_of`)."""
    return _last_day(fy, cell_month(quarter, month_of(fy, fye)))


@lru_cache(maxsize=4096)
def cell_available(fy: int, quarter: int, fye=12) -> date:
    """칸 `(fy, quarter)` 의 보고서를 쓸 수 있게 되는 날(이 날 포함). `fye` = 결산월 또는 `Fiscal`(외국 상장사 기한 · 결산월 변경)."""
    f = _as_fiscal(fye)
    if quarter == 4:
        y, m = _add_months(fy, month_of(fy, f), FOREIGN_ANNUAL_LAG_MONTHS if f.foreign else ANNUAL_LAG_MONTHS)
        return date(y, m, 1)
    return cell_end(fy, quarter, f) + timedelta(days=FOREIGN_QUARTERLY_LAG_DAYS if f.foreign else QUARTERLY_LAG_DAYS)


def _as_date(dd) -> date:
    if isinstance(dd, date):
        return dd
    s = str(dd)
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _pit_cell_dec(y: int, m: int, d: int) -> tuple[int, int]:
    """12월 결산 — 종전 `price_multiple_data._pit_quarter` 식 그대로."""
    md = m * 100 + d
    if md >= 1114:
        return (y, 3)
    if md >= 814:
        return (y, 2)
    if md >= 515:
        return (y, 1)
    if m >= 4:
        return (y - 1, 4)
    return (y - 1, 3)


def _pit_cell_any(t: date, fye) -> tuple[int, int]:
    """일반 식 — 그 날까지 쓸 수 있게 된 칸 가운데 기간 말이 가장 늦은 칸."""
    best, best_end = None, None
    for fy in (t.year - 2, t.year - 1, t.year):
        for q in (1, 2, 3, 4):
            if cell_available(fy, q, fye) <= t:
                e = cell_end(fy, q, fye)
                if best_end is None or e > best_end:
                    best, best_end = (fy, q), e
    return best


@lru_cache(maxsize=65536)
def _pit_cell_cached(dd: str, fye: Fiscal) -> tuple[int, int]:
    t = _as_date(dd)
    if fye.cells:
        # 261020(p1e): 결산기 칸 — 그날까지 공시된 칸 가운데 기간 말이 가장 늦은 칸. 하나도 없으면(첫 결산기 공시 전) 공시 기한 규칙.
        best, best_end = None, None
        for fy, q, av in fye.cells:
            if av <= dd:
                e = cell_end(fy, q, fye)
                if best_end is None or e > best_end:
                    best, best_end = (fy, q), e
        if best is not None:
            return best
    if fye.month == 12 and not fye.since:
        return _pit_cell_dec(t.year, t.month, t.day)
    return _pit_cell_any(t, fye)


def pit_cell(dd, fye=12) -> tuple[int, int]:
    """그 날(`YYYYMMDD` 또는 date)까지 공시된 최신 칸 `(fy, quarter)` — 사업보고서면 quarter 4."""
    return _pit_cell_cached(_as_date(dd).strftime("%Y%m%d"), _as_fiscal(fye))


def _pit_annual_any(t: date, fye) -> int:
    fy = t.year
    while cell_available(fy, 4, fye) > t:
        fy -= 1
    return fy


def pit_annual(dd, fye=12) -> int:
    """그 날까지 공시된 최신 사업보고서의 사업연도 표기 — 12월 결산이면 종전 `_pit_fy`(4월 이후 전년, 아니면 전전년)."""
    t = _as_date(dd)
    fye = _as_fiscal(fye)
    if fye.month == 12 and not fye.since:
        return t.year - 1 if t.month >= 4 else t.year - 2
    return _pit_annual_any(t, fye)


def prior_annual(fy: int, quarter: int, fye=12) -> int:
    """칸 `(fy, quarter)` 직전 회계연도(그 칸 기간 말보다 앞선 가장 최근 결산)의 사업보고서 표기.

    TTM = 직전 회계연도 연간 + 이번 누적 − 1년 전 같은 칸 누적. 12월 결산이면 언제나 `fy - 1`(종전 식).
    3월 결산의 2026 1분기(2026.06)면 직전 결산 2026.03 → 2026. 6월 결산의 2026 3분기(2026.03)면 직전 결산 2025.06 → 2025.
    261021: 결산월을 바꾼 회사는 표기마다 결산월이 다르므로 그 칸 기간 말보다 앞선 사업보고서 칸(`cell_end(y, 4)`) 가운데 가장 늦은 것의 표기."""
    if quarter == 4:
        return fy - 1
    f = _as_fiscal(fye)
    if f.since:
        e = cell_end(fy, quarter, f)
        best = None
        for y in (e.year - 1, e.year):
            end = cell_end(y, 4, f)
            if end < e and (best is None or end > best[1]):
                best = (y, end)
        return best[0] if best else fy - 1
    fm = f.month
    return fy if fm < cell_month(quarter, fm) else fy - 1


def latest_annual(today: date | None = None, fye=12) -> int:
    """오늘 공시돼 있어야 할 최신 사업보고서 표기(수집 대상 상한) — 12월 결산이면 종전 `_latest_annual_fy`(4월 이후 전년, 아니면 전전년)."""
    if today is None:
        from open_proxy_mcp.clock import today_kst
        today = today_kst()
    return pit_annual(today, fye)


def quarter_labels_after(fy_annual: int, fye=12) -> dict[int, int]:
    """사업보고서 `fy_annual` 다음 회계연도의 분기 칸 표기 `{quarter: fy}` — 12월 결산이면 모두 `fy_annual + 1`.
    3월 결산이면 모두 같은 해(6·9·12월), 6월 결산이면 1분기·반기는 같은 해(9·12월)·3분기는 다음 해(3월).
    261021: 그 사업보고서의 결산월(`month_of(fy_annual)`)에서 센다 — 다음 회계연도의 분기 말은 그 결산일에서 3·6·9개월 뒤다."""
    fye = month_of(fy_annual, fye)
    return {q: fy_annual + (1 if fye + _STEP[q] > 12 else 0) for q in (1, 2, 3)}


def _prev_annual_end(e: date, f: Fiscal) -> date:
    """결산일 `e` 의 바로 앞 결산일(사업연도 하나 전). 결산월을 바꾼 회사는 전환기 결산일(`since` 해 새 결산월)의 앞이 옛 마지막 결산일이다."""
    if not f.since:
        return _last_day(e.year - 1, e.month)
    trans = _last_day(f.since, f.month)
    old_last = _last_day(f.since if f.prev < f.month else f.since - 1, f.prev)    # 새 결산월이 늦으면 같은 해(옛 결산기가 먼저), 이르면 앞 해
    if e == trans:
        return old_last
    if e <= old_last:
        return _last_day(e.year - 1, f.prev)
    return _last_day(e.year - 1, f.month)


def prior_columns(report_fy: int, fye=12) -> list[tuple[int, bool, str]]:
    """사업보고서(표기 `report_fy`)의 전기·전전기 칸이 **어느 표기 칸의 자리**인가 → `[(표기, 쓸 수 있나, 까닭)]` 둘(전기, 전전기).

    종전 읽기는 전기 = `report_fy − 1`·전전기 = `report_fy − 2` 로 가정했다. 결산월을 바꾼 회사는 맞지 않는다: DART 표기는 결산기 끝의 달력 연도라
    전환기 사업보고서(표기 `since`)의 전기 칸(옛 마지막 결산기)이 새 결산월이 옛 결산월보다 늦으면 같은 표기(`since`)에 놓이고, 그 보고서의 전전기 칸은 `since - 1`
    이 아니라 그 앞 해다. 그 칸의 자리가 맞는 건 **그 결산기가 그 표기 칸을 차지한 결산기**일 때(`month_of` 의 결산월 끝)뿐이고, 전환기(12개월이 아님)의 값은
    연간 값이 아니라 쓰지 않는다. 까닭: "" = 쓸 수 있다 · "collision" = 그 표기 칸은 다른 결산기(전환기)가 차지했다 · "transition" = 전환기.
    안 바꾼 회사는 늘 `[(report_fy-1, True, ""), (report_fy-2, True, "")]`(종전 식)."""
    f = _as_fiscal(fye)
    e = _last_day(report_fy, month_of(report_fy, f))
    out = []
    for _ in (1, 2):
        e = _prev_annual_end(e, f)
        label = e.year
        if f.since and label == f.since and e.month == f.month:
            out.append((label, False, "transition"))
        elif _last_day(label, month_of(label, f)) != e:
            out.append((label, False, "collision"))
        else:
            out.append((label, True, ""))
    return out


#: 서버용 — 결산월을 바꾼 회사 목록(종목, 결산월, 옛 결산월, 새 결산월 첫 사업연도). `to_jsonb` 로 읽어 **칸이 아직 없는 표(`scripts/fundamentals_ddl.py` 적용 전)에서도 질의가
#: 깨지지 않는다**(없으면 NULL → 빈 목록 = 종전 읽기). 서버 배포가 DDL 보다 먼저여도 되게 하려는 것이다. 바꾼 회사는 수십 곳이라 한 번에 읽어 둔다.
CHANGES_SQL = ("SELECT ticker, acc_mt, (to_jsonb(d)->>'acc_mt_prev')::int, (to_jsonb(d)->>'acc_mt_since')::int "
               "FROM dart_fundamentals d WHERE to_jsonb(d)->>'acc_mt_prev' IS NOT NULL")


def changes_from_rows(rows) -> dict[str, tuple[int, int, int]]:
    """`CHANGES_SQL` 의 행 → {종목: (결산월, 옛 결산월, 새 결산월 첫 사업연도)} — 올바른 변경만(`_norm_change`)."""
    out = {}
    for r in rows or []:
        try:
            t, m, p, s = r[0], int(r[1]) if r[1] is not None else 12, r[2], r[3]
        except (TypeError, ValueError, IndexError):
            continue
        p2, s2 = _norm_change(fye_month(m), p, s)
        if s2:
            out[str(t)] = (fye_month(m), p2, s2)
    return out


def load_fye_map(con) -> dict[str, "int | Fiscal"]:
    """배치용: 종목 → 결산월(`dart_fundamentals.acc_mt` 가운데 12월이 아닌 것만, int). `con` = psycopg 접속.

    261021: 결산월을 바꾼 회사(`acc_mt_prev`·`acc_mt_since` 가 있는 회사)는 12월이어도 `Fiscal`(옛 결산월·첫 사업연도 포함)로 준다 — `fiscal()`·`cell_end` 등 이 모듈의
    모든 함수가 int 와 같이 받는다. 칸이 아직 없으면(`scripts/fundamentals_ddl.py` 적용 전) 종전처럼 12월이 아닌 회사만 int 로 — 바뀐 읽기는 없다.
    `acc_mt` 칸이 없으면 빈 dict — 모두 12월 결산으로 읽는다(종전과 같다)."""
    has = con.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                      "AND table_name='dart_fundamentals' AND column_name='acc_mt')").fetchone()[0]
    if not has:
        return {}
    out: dict = {t: int(m) for t, m in con.execute(
        "SELECT ticker, acc_mt FROM dart_fundamentals WHERE acc_mt IS NOT NULL AND acc_mt <> 12")}
    try:
        # 결산월을 바꾼 회사 — `to_jsonb` 로 읽어 변경 칸이 없는 표에서도 깨지지 않는다(서버와 같은 질의: `CHANGES_SQL`). 못 읽으면 종전 읽기(fail-open).
        changes = changes_from_rows(list(con.execute(CHANGES_SQL)))
    except Exception:  # noqa: BLE001
        changes = {}
    for t, (m, p, s) in changes.items():
        out[t] = fiscal(m, None, p, s)
    return out
