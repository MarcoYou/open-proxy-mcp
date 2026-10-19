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

한계: 결산월을 바꾼 회사는 바꾸기 전 칸과 뒤 칸이 다른 결산월을 따른다. 이 모듈은 회사마다 결산월 하나(`dart_fundamentals.acc_mt` — 가장 최근
사업보고서의 기간 말 달, 바꾼 회사·반기 결산 리츠는 12 = 종전 읽기)를 쓴다.
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
    `pit_annual`(FY0 의 사업연도)은 달력 규칙 그대로다(firm 의 FY0 와 같은 정의)."""
    month: int = 12
    foreign: bool = False
    cells: tuple = ()


def fiscal(acc_mt, ticker: str | None = None) -> Fiscal:
    """`dart_fundamentals.acc_mt` + 종목코드 → 공시 달력. 외국 상장사 기한은 12월 결산이 아닐 때만(12월 결산은 종전 그대로)."""
    m = fye_month(acc_mt)
    return Fiscal(m, bool(ticker) and str(ticker).startswith("9") and m != 12)


def _as_fiscal(v) -> Fiscal:
    if isinstance(v, Fiscal):
        return v if v.month != 12 else Fiscal(12, False, v.cells)
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
    """칸 `(fy, quarter)` 의 기간 말(그 달 말일). 해는 DART 표기 그대로 `fy`."""
    return _last_day(fy, cell_month(quarter, fye_month(fye)))


@lru_cache(maxsize=4096)
def cell_available(fy: int, quarter: int, fye=12) -> date:
    """칸 `(fy, quarter)` 의 보고서를 쓸 수 있게 되는 날(이 날 포함). `fye` = 결산월 또는 `Fiscal`(외국 상장사 기한)."""
    f = _as_fiscal(fye)
    if quarter == 4:
        y, m = _add_months(fy, f.month, FOREIGN_ANNUAL_LAG_MONTHS if f.foreign else ANNUAL_LAG_MONTHS)
        return date(y, m, 1)
    return cell_end(fy, quarter, f.month) + timedelta(days=FOREIGN_QUARTERLY_LAG_DAYS if f.foreign else QUARTERLY_LAG_DAYS)


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
                e = cell_end(fy, q, fye.month)
                if best_end is None or e > best_end:
                    best, best_end = (fy, q), e
        if best is not None:
            return best
    if fye.month == 12:
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
    if fye.month == 12:
        return t.year - 1 if t.month >= 4 else t.year - 2
    return _pit_annual_any(t, fye)


def prior_annual(fy: int, quarter: int, fye=12) -> int:
    """칸 `(fy, quarter)` 직전 회계연도(그 칸 기간 말보다 앞선 가장 최근 결산)의 사업보고서 표기.

    TTM = 직전 회계연도 연간 + 이번 누적 − 1년 전 같은 칸 누적. 12월 결산이면 언제나 `fy - 1`(종전 식).
    3월 결산의 2026 1분기(2026.06)면 직전 결산 2026.03 → 2026. 6월 결산의 2026 3분기(2026.03)면 직전 결산 2025.06 → 2025."""
    if quarter == 4:
        return fy - 1
    fye = fye_month(fye)
    return fy if fye < cell_month(quarter, fye) else fy - 1


def latest_annual(today: date | None = None, fye=12) -> int:
    """오늘 공시돼 있어야 할 최신 사업보고서 표기(수집 대상 상한) — 12월 결산이면 종전 `_latest_annual_fy`(4월 이후 전년, 아니면 전전년)."""
    if today is None:
        from open_proxy_mcp.clock import today_kst
        today = today_kst()
    return pit_annual(today, fye)


def quarter_labels_after(fy_annual: int, fye=12) -> dict[int, int]:
    """사업보고서 `fy_annual` 다음 회계연도의 분기 칸 표기 `{quarter: fy}` — 12월 결산이면 모두 `fy_annual + 1`.
    3월 결산이면 모두 같은 해(6·9·12월), 6월 결산이면 1분기·반기는 같은 해(9·12월)·3분기는 다음 해(3월)."""
    fye = fye_month(fye)
    return {q: fy_annual + (1 if fye + _STEP[q] > 12 else 0) for q in (1, 2, 3)}


def load_fye_map(con) -> dict[str, int]:
    """배치용: 종목 → 결산월(`dart_fundamentals.acc_mt` 가운데 12월이 아닌 것만). `con` = psycopg 접속.

    칸이 아직 없으면(`scripts/fundamentals_ddl.py` 적용 전) 빈 dict — 모두 12월 결산으로 읽는다(종전과 같다)."""
    has = con.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                      "AND table_name='dart_fundamentals' AND column_name='acc_mt')").fetchone()[0]
    if not has:
        return {}
    return {t: int(m) for t, m in con.execute(
        "SELECT ticker, acc_mt FROM dart_fundamentals WHERE acc_mt IS NOT NULL AND acc_mt <> 12")}
