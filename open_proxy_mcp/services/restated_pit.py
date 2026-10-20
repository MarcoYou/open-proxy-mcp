"""정정 칸의 시점 규칙 — 정정 칸 값은 **그 출처 보고서가 공시된 날부터** 쓴다(261020 p1g pit-restated, 미래 정보 금지).

왜: 정정 칸(연간 `dart_finstat_y.ni_restated`·`eq_restated`, 분기 `dart_finstat_q.ni_cum_restated`·`eq_restated`)은 더 늦은 보고서의
전기·전전기 칸이다. 과거 시점을 읽는 쪽(서버 곡선·과거 시점·업종 비교 기업 행·연말 밴드, 시장·업종 과거 백필)은 그 칸을 출처 보고서의
접수일과 상관없이 그 사업연도 칸으로 썼다 — 재작성 보고서가 나오기 전 시점에도 재작성 값이 들어갔다. #157(정정 칸 = 가장 늦게 접수된
보고서의 값)이 그 폭을 넓혔다: 10-20 배포 main 의 000680 곡선 2024-06-28 점 FY0 PBR 0.372 는 2026-03 사업보고서가 재작성한 FY2023 자본
9,277억으로 센 값이다(그 시점에 공시돼 있던 5,640억이면 0.611 — 같은 점 MRQ PBR 0.673 과 맞는다).

규칙(칸마다, 순이익·자본 따로): 기준일 d 에 정정 값은 **출처 보고서 접수일 ≤ d** 일 때만 쓰고, 아니면 원본 칸(그 시점에 공시돼 있던 값)을 쓴다.

출처 접수일
  · 연간: 근거 칸 `restated_rcept_no` 앞 8자리(DART 접수번호 = 접수일 YYYYMMDD + 일련번호 6자리).
  · 분기: 근거 칸 `restate_why` 의 「접수 YYYYMMDD…」(다음 해 같은 분기 보고서 접수번호 — 전기 칸 보충·지배 순이익 검토 칸이 남긴다).
  · 결산기 칸(반기·분기 결산 리츠 — `period_cells.MARK`): 근거 칸의 「공시 YYYYMMDD」(#153 이 칸 고르기에 쓰는 날짜와 같다). 그 전엔 값이 없다
    — 원본 칸은 그 해 마지막 한 결산기 값이라 12개월 값 대신 쓰지 않는다(서버 종전 규칙과 같다).
  · 출처를 모르는 옛 정정 칸 — 보수적 추정 공시일. #157 전에는 손으로 돌리는 정정치 백필(`market_val_series.py --backfill-restated`,
    기본 2024 사업보고서)만 정정 칸을 썼고 출처를 남기지 않았다. 그 백필은 **한 보고서에서 전기(그 해 + 1 보고서 몫)·전전기(그 해 + 2 보고서 몫)
    칸을 함께** 썼다(운영 10-20: 출처 없는 정정 칸이 FY2022·FY2023 에 각 약 2,450행 — 2024 사업보고서 한 통). 그래서 연간은
    fy+2 사업보고서 공시 시점(`report_calendar.cell_available(fy + 2, 4)` — 12월 결산이면 fy+3 년 4월 1일)이 오늘까지 지났으면 그날, 아니면
    fy+1 사업보고서 공시 시점(fy+2 년 4월 1일)으로 본다(`annual_estimate`). 분기는 다음 해 같은 분기 보고서 공시 시점(`quarter_estimate`).
    추정일이 오늘 뒤면 그 값의 출처는 다음 해 보고서일 수 없다(지금 이미 있는 값이다 — 단위 고침 등) — 시점 규칙을 걸지 않는다. 그래서 최신 응답
    (지금 시점)은 정정 칸 그대로다.

처음부터 정정 값(그 칸 자체의 공시 시점부터 — 종전 그대로)인 경우
  ① 원본 칸이 빈 칸(보충값): DART 가 그 보고서를 주지 않았거나(013) 못 받은 칸을 다음 해 보고서 전기 칸으로 채운 값이다. 그 보고서는 그 시점에
     공시돼 있었고 전기 칸은 재작성이 없으면 같은 값이다(운영 10-20 출처 없는 연간 정정 칸 5,016 가운데 3,619 가 원본과 같다). 비우면 데이터 누락이다.
  ② 원본과 10ⁿ 배(100배 이상, ±20%) 어긋나는 칸(단위 고침 — 회사가 단위를 잘못 적은 보고서 · `scale_guard.check_prior_period_mismatch`).
     원본은 그 시점의 정보가 아니라 오류다(0091W0 FY2025 1,000배 · 032680 FY2022 100만 배 등).
  ③ 원본과 같은 값.

이 모듈은 순수 함수와 배치용 읽기(`load_annual`·`load_quarter`)만 둔다. 서버(`price_multiple_data._firm_fin_by_fy`·`_firm_fin_by_q`)와 두 과거 백필
(`scripts/market_val_history_backfill.py`·`scripts/sector_val_backfill.py`)이 같은 함수를 쓴다. 읽는 쪽은 `at(재무, 기준일)` 로 그 날 값을 얻는다.
재무 요약 파생(`market_fund_quarterly.derive_fundamentals`)·Q4 seed(분기 표 사업보고서 칸 = 연간 값 사본)는 **지금 시점**만 읽는다 — 지금은 모든 문이
열려 있으므로(위 추정 규칙) 정정 값 그대로이고 바꾸지 않았다. 과거 시점 읽기는 분기 표 사업보고서 칸이 아니라 연간 표(정정 칸 + 출처)를 읽는다.
"""
from __future__ import annotations

import re
from datetime import date

from open_proxy_mcp.services import report_calendar as _rc
from open_proxy_mcp.services.scale_guard import check_prior_period_mismatch

#: 분기 근거 칸의 출처 접수번호(접수일 8자리 + 일련번호 6자리) — 「… 접수 20200814001642 …」.
_RCEPT_IN_WHY = re.compile(r"접수 (\d{8})\d{6}")
#: SQL 에서 분기 근거 칸의 출처 접수일만 뽑는 식(Postgres `substring(… from 정규식)`) — 칸 이름 앞에 표 별칭을 붙여 쓴다.
WHY_RCEPT_SQL = "substring({col} from '접수 ([0-9]{{8}})[0-9]{{6}}')"

Gate = tuple   # (공시일 YYYYMMDD, 그 전 값 (순이익, 자본))


class PitFin(dict):
    """`{열쇠: 지금 값 (순이익, 자본)}` + `gates` `{열쇠: (공시일 YYYYMMDD, 그 전 값)}`.

    dict 로서의 값은 **지금 시점** 값이다(최신 응답·재무 요약 파생과 같은 값 — 종전 읽기 그대로). 과거 시점은 `at(기준일)` 이 공시일이 기준일보다
    늦은 칸만 그 전 값으로 바꾼 사본을 준다(닫힌 칸 조합마다 한 번만 만든다). 값과 문을 다 채운 뒤에 `at` 을 부른다."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.gates: dict = {}
        self._views: dict = {}

    def gate(self, key, g: Gate | None) -> None:
        """칸 `key` 의 문을 단다(None 이면 뗀다)."""
        if g is None:
            self.gates.pop(key, None)
        else:
            self.gates[key] = g
        self._views = {}

    def at(self, d) -> "PitFin":
        if not self.gates:
            return self
        d = _ymd(d)
        closed = tuple(sorted(k for k, g in self.gates.items() if g[0] > d))
        if not closed:
            return self
        v = self._views.get(closed)
        if v is None:
            v = self.__class__(self)
            for name, val in self.__dict__.items():          # 붙은 속성(서버 분기 재무의 결산기 칸 공시일 `cells` 등)은 그대로
                if name not in ("gates", "_views"):
                    setattr(v, name, val)
            for k in closed:
                v[k] = self.gates[k][1]
            self._views[closed] = v
        return v


def at(fin, d):
    """기준일 `d`(YYYYMMDD)의 재무 — 문이 달린 재무(`PitFin`)면 그 날 값, 아니면 그대로(이미 그 날 값이거나 문 없는 dict)."""
    return fin.at(d) if isinstance(fin, PitFin) else fin


def _ymd(d) -> str:
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    return str(d).replace("-", "")[:8]


def _today(today) -> str:
    if today is None:
        from open_proxy_mcp.clock import today_kst
        today = today_kst()
    return _ymd(today)


def rcept_day(rcept_no) -> str | None:
    """접수번호 → 접수일(YYYYMMDD). 꼴이 아니면 None."""
    s = str(rcept_no or "").strip()
    return s[:8] if len(s) >= 8 and s[:8].isdigit() else None


def why_rcept_day(why) -> str | None:
    """분기 근거 칸(`restate_why`)의 출처 접수일 — 첫 「접수 YYYYMMDD……」. 없으면 None."""
    m = _RCEPT_IN_WHY.search(str(why or ""))
    return m.group(1) if m else None


def annual_estimate(fy: int, fye=12, today=None) -> str | None:
    """출처를 모르는 연간 정정 칸의 보수적 추정 공시일 — fy+2 사업보고서 공시 시점이 오늘까지 지났으면 그날, 아니면 fy+1 사업보고서 공시 시점,
    둘 다 오늘 뒤면 None(시점 규칙을 걸지 않는다 — 모듈 설명)."""
    t = _today(today)
    for k in (2, 1):
        av = _rc.cell_available(int(fy) + k, 4, fye).strftime("%Y%m%d")
        if av <= t:
            return av
    return None


def quarter_estimate(fy: int, quarter: int, fye=12, today=None) -> str | None:
    """출처를 모르는 분기 정정 칸의 추정 공시일 — 다음 해 같은 분기 보고서 공시 시점(오늘 뒤면 None)."""
    av = _rc.cell_available(int(fy) + 1, int(quarter), fye).strftime("%Y%m%d")
    return av if av <= _today(today) else None


def _field(raw, restated) -> tuple:
    """(지금 값, 출처 공시 전 값). 정정 값이 없거나 · 원본이 빈 칸(보충값) · 같은 값 · 10ⁿ 배 단위 고침이면 둘 다 정정 값(처음부터)."""
    if restated is None:
        return raw, raw
    if raw is None or raw == restated:
        return restated, restated
    if check_prior_period_mismatch(raw, restated).get("triggered"):
        return restated, restated
    return restated, raw


def cell(raw: tuple, restated: tuple, avail) -> tuple[tuple, Gate | None]:
    """한 칸 → (지금 값, 문). `avail` = 출처 공시일(YYYYMMDD) 또는 그것을 내는 인자 없는 함수(문이 필요할 때만 부른다).
    문이 필요한데 공시일을 모르면(None) 문을 달지 않는다 — 지금 값을 쓴다."""
    (ni, eq), (ni_r, eq_r) = raw, restated
    n_now, n_pre = _field(ni, ni_r)
    e_now, e_pre = _field(eq, eq_r)
    now = (n_now, e_now)
    if (n_pre, e_pre) == now:
        return now, None
    av = avail() if callable(avail) else avail
    if not av:
        return now, None
    return now, (str(av), (n_pre, e_pre))


def annual_cell(fy: int, ni, eq, ni_r, eq_r, rcept_no=None, fye=12, today=None) -> tuple[tuple, Gate | None]:
    """연간 표 한 행 → (지금 값, 문). 출처는 `restated_rcept_no`, 없으면 보수적 추정(`annual_estimate`)."""
    return cell((ni, eq), (ni_r, eq_r), lambda: rcept_day(rcept_no) or annual_estimate(fy, fye, today))


def quarter_cell(fy: int, quarter: int, ni, eq, ni_r, eq_r, rcept=None, fye=12, today=None) -> tuple[tuple, Gate | None]:
    """분기 표 한 칸(1~3분기, 결산기 칸 밖) → (지금 값, 문). `rcept` = 근거 칸의 출처 접수일(또는 접수번호), 없으면 추정(`quarter_estimate`)."""
    return cell((ni, eq), (ni_r, eq_r), lambda: rcept_day(rcept) or quarter_estimate(fy, quarter, fye, today))


def marked_gate(avail) -> Gate | None:
    """결산기 칸(`period_cells.MARK`) — 공시일 전엔 값 없음."""
    return (str(avail), (None, None)) if avail else None


# ── 배치(시장·업종 과거 백필)용 읽기 ──────────────────────────────────────────────────

def _cols(con, table: str) -> set[str]:
    return {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
                                      "AND table_name=%s", (table,))}


def load_annual(con, fye_for=None, today=None) -> dict[str, PitFin]:
    """연간 표 전부 → `{종목: PitFin{fy: (지배순이익, 지배자본)}}`(값 = 지금, 문 = 정정 칸 출처 공시일). `fye_for(종목)` = 공시 달력(결산월·외국 상장사 기한),
    없으면 12월 결산. 출처 칸(`restated_rcept_no`)이 없는 표(그 칸을 만드는 이행 전 — 시험용 최소 표)면 모두 추정 공시일로 본다."""
    rc = "restated_rcept_no" if "restated_rcept_no" in _cols(con, "dart_finstat_y") else "NULL"
    t_ = _today(today)
    out: dict[str, PitFin] = {}
    for t, fy, ni, eq, ni_r, eq_r, r in con.execute(
            f"SELECT ticker, fy, ni, eq, ni_restated, eq_restated, {rc} FROM dart_finstat_y"):
        fy = int(fy)
        now, g = annual_cell(fy, ni, eq, ni_r, eq_r, r, fye_for(t) if fye_for else 12, t_)
        f = out.setdefault(t, PitFin())
        f[fy] = now
        if g is not None:
            f.gate(fy, g)
    return out


def load_quarter(con, fye_for=None, today=None) -> dict[str, PitFin]:
    """분기 표 1~3분기 칸 전부 → `{종목: PitFin{(fy, quarter): (지배순이익 누적, 지배자본)}}`. 사업보고서 칸(Q4 seed 사본)은 읽지 않는다(과거 시점은 연간 표).
    결산기 칸 종목은 뒤이어 `period_cells.apply_marked` 가 그 칸으로 바꾼다(문 = 그 칸 공시일)."""
    why = WHY_RCEPT_SQL.format(col="restate_why") if "restate_why" in _cols(con, "dart_finstat_q") else "NULL"
    t_ = _today(today)
    out: dict[str, PitFin] = {}
    for t, fy, q, ni, eq, ni_r, eq_r, r in con.execute(
            f"SELECT ticker, fy, quarter, ni_cum, eq, ni_cum_restated, eq_restated, {why} FROM dart_finstat_q WHERE quarter != 4"):
        k = (int(fy), int(q))
        now, g = quarter_cell(k[0], k[1], ni, eq, ni_r, eq_r, r, fye_for(t) if fye_for else 12, t_)
        f = out.setdefault(t, PitFin())
        f[k] = now
        if g is not None:
            f.gate(k, g)
    return out
