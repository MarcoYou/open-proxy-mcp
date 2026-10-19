"""반기·분기 결산 회사(리츠·일부 인프라 펀드)의 결산기 → 연간·분기 재무 표 칸 — 12개월 결산 회사와 같은 꼴(261020 p1e).

왜: 반기(6개월)·분기(3개월) 결산 회사는 결산기마다 사업보고서를 따로 낸다. DART 재무제표 API 는 칸을 (사업연도 표기, 보고서 코드)로
열쇠 삼고 같은 열쇠에 보고서가 여럿이면 **가장 늦은 것 하나만** 준다(10-19 대조). 그래서 연간 재무 표(`dart_finstat_y`)에는 해마다
마지막 결산기 하나(3·6개월 순이익)가, 분기 재무 표(`dart_finstat_q`)의 반기 칸에는 그해 마지막 「3개월 시점 보고서」가 들어 있었다
(10-20 운영 롯데리츠 FY2025 = 2025.07~12 196.3억 · 2025 반기 칸 = 2025.07~09 — 11/13 접수인데 곡선은 8/14 부터 썼다).
실시간 firm 은 #149 부터 12개월 결산기 합(`price_multiple_data.trailing_period_ni`)인데 주간 곡선·과거 시점·업종 비교·시장/업종 집계는
이 표를 읽어 한 결산기 기준이었다(같은 날 firm PER 32.59 / 곡선 끝점 60 안팎).

어떻게(설계 (다) — 결산기 사실값을 12월 결산 회사의 「사업연도 누적」 꼴로 기존 칸에 싣는다):
  · 사업연도 y = 달력 y 해에 끝나는 마지막 결산기(E_y)에서 거꾸로 12개월. 연간 칸 = 그 12개월 지배순이익 합 · E_y 말 지배자본.
    firm 의 FY0(사업보고서 (y, 11011) 의 당기 결산기에서 거꾸로 12개월)과 같은 정의다.
  · 분기 칸 (y, q)(q=1~3, 달 3q 말) = E_{y-1} 다음부터 그 달 말까지 끝난 결산기 순이익 합(누적) · 그 달 말 이하 가장 늦은 재무상태표의 자본
    (결산기 말 또는 3개월 시점 보고서). 그러면 모든 읽는 쪽의 같은 식 TTM = 연간(y-1) + 누적(y,q) − 누적(y-1,q) 이 **그 칸 시점까지
    끝난 마지막 결산기에서 거꾸로 12개월**이 된다 — 곡선·업종 비교·집계 배치·재무 요약 파생(`derive`)이 따로 갈래를 두지 않는다.
  · 값은 정정 칸(`ni_cum_restated`·`eq_restated`, 연간은 `ni_restated`·`eq_restated`)에, 근거 칸(`restate_why`)에는 머리 `MARK` + 「공시 YYYYMMDD」
    (그 칸에 쓴 보고서 가운데 가장 늦은 **최초 접수일**) + 결산기 목록. 원본 칸은 그대로 둔다(DART 가 준 값).
  · 읽는 쪽(이 판)은 표시된 칸을 그 공시일부터 쓴다(`report_calendar.Fiscal.cells` — 미래 정보 금지). 표시 없는 1~3분기 칸은 그 종목에서 쓰지 않는다.
    배포된 옛 읽기(공시 기한 45일 규칙)도 깨지지 않는다 — 값이 12개월 기준이 되고 미래 정보가 줄 뿐이다.

저장보다 읽을 때 계산을 먼저 보았다: 결산기 사실값은 DART 가 다시 주지 않으므로(가장 늦은 보고서 하나) 어딘가에 남겨야 하고, 그 사실값을
「누적」 꼴로 두면 읽는 쪽 N 곳이 한 식을 그대로 쓴다. 칸은 이미 있는 행·칸이라 저장 공간은 늘지 않는다. 누적의 차가 곧 결산기 값이다(복원 가능).

이 모듈은 순수 함수만 둔다(시험 대상). DART 호출·DB 쓰기는 `scripts/market_fund_quarterly.py --period-cells`.
"""
from __future__ import annotations

import calendar
import re
from collections import Counter
from typing import Any

from open_proxy_mcp.services.scale_guard import gid_exact

#: 근거 칸 머리 — 이 칸은 결산기 합으로 쓴 칸이다(읽는 쪽은 이 머리로 알아본다).
MARK = "결산기 12개월"
#: 근거 칸의 공시일 — 그 칸에 쓴 보고서 가운데 가장 늦은 최초 접수일.
_AVAIL_RE = re.compile(r"공시 (\d{8})")
#: SQL 에서 표시 칸을 알아보는 조건(`restate_why LIKE …`).
MARK_LIKE = MARK + "%"
#: SQL 에서 공시일을 뽑는 식(Postgres `substring(… from 정규식)`).
AVAIL_SQL = "substring(restate_why from '공시 ([0-9]{8})')"

_TERM_RE = re.compile(r"제\s*(\d+)\s*기")
_REPORT_RE = re.compile(r"(사업보고서|반기보고서|분기보고서)\s*\((\d{4})\.(\d{2})\)")


def why_text(avail: str, detail: str) -> str:
    return f"{MARK}(261020) 공시 {avail} — {detail}"


def avail_of(why: str | None) -> str | None:
    """표시 칸이면 공시일(YYYYMMDD), 아니면 None."""
    if not why or not str(why).startswith(MARK):
        return None
    m = _AVAIL_RE.search(why)
    return m.group(1) if m else None


def month_end(y: int, m: int) -> str:
    return f"{y}{m:02d}{calendar.monthrange(y, m)[1]:02d}"


def shift(yyyymmdd: str, months: int) -> str:
    """월말 날짜를 `months` 달 옮긴 월말."""
    t = int(yyyymmdd[:4]) * 12 + int(yyyymmdd[4:6]) - 1 + months
    y, m = divmod(t, 12)
    return month_end(y, m + 1)


def _mdiff(a: str, b: str) -> int:
    """b − a (달 수)."""
    return (int(b[:4]) * 12 + int(b[4:6])) - (int(a[:4]) * 12 + int(a[4:6]))


# ── 원문 행 → 값 ───────────────────────────────────────────────────────────────

def _g(rows, account_id, sj, field):
    """정확 일치 — 새 분류체계(`ifrs-full_…`)가 없으면 옛 분류체계의 같은 이름(`ifrs_…`, 2018 사업보고서 무렵 — 연간 수집 `market_val_series.gid` 와 같은 규칙)."""
    v = gid_exact(rows, f"ifrs-full_{account_id}", sj, field)
    if v is None:
        v = gid_exact(rows, f"ifrs_{account_id}", sj, field)
    return float(v) if v is not None else None


def ctrl_ni(rows: list, field: str = "thstrm_amount") -> tuple[float | None, str]:
    """지배순이익과 고른 길. firm(`price_multiple_data._ctrl_ni`)과 같은 순서에 한 길을 더한다:
    ① 지배 귀속 순이익 ② 비지배 순이익 줄이 있으면 총순이익 − 비지배 ③ (더한 길) 지배·비지배 순이익 줄이 없는데 **지배 귀속 총포괄**이 있고
    기타포괄이 0(총포괄 = 총순이익)이면 그 값 — 10-20 원문 신한알파리츠 2023~2025 9월 사업보고서는 순이익을 지배·비지배로 나누지 않고
    총포괄만 나눴다(2025.09 총순이익 113.1억 · 지배 총포괄 67.5억 = 다음 보고서 전기 칸의 지배 순이익). firm 의 길(②의 비지배 0)이면 비지배 몫이
    섞인다. ④ (더한 길) 표준 코드 없는 이름 줄 — 값이 하나로 모일 때만(아래) ⑤ 총순이익(비지배 없는 회사)."""
    sj = ("CIS", "IS")
    v = _g(rows, "ProfitLossAttributableToOwnersOfParent", sj, field)
    if v is not None:
        return v, "ID"
    pl = _g(rows, "ProfitLoss", sj, field)
    nci = _g(rows, "ProfitLossAttributableToNoncontrollingInterests", sj, field)
    if pl is not None and nci is not None:
        return pl - nci, "TOTAL_MINUS_NCI"
    ci_o = _g(rows, "ComprehensiveIncomeAttributableToOwnersOfParent", sj, field)
    ci = _g(rows, "ComprehensiveIncome", sj, field)
    if pl is not None and ci_o is not None and ci is not None and abs(ci - pl) < 1:
        return ci_o, "CI_OWNERS_OCI0"
    # ④ 표준 계정코드 없이 이름으로만 지배·비지배를 나눈 보고서(10-20 원문 디앤디플랫폼리츠 2021·2022, ESR켄달스퀘어리츠 2021~2024 사업보고서 —
    #   「지배기업 소유주지분」·「지배기업주주지분」). 순이익 귀속과 총포괄 귀속 두 곳에 같은 이름 줄이 있어 **값이 하나로 모일 때만** 쓴다(기타포괄이
    #   있으면 두 값이 달라 쓰지 않는다 — 분기 수집 파서가 총포괄 지배 몫을 순이익으로 고르던 결함을 들이지 않는다).
    own = set(_nonstd(rows, field, nci=False))
    if len(own) == 1:
        return own.pop(), "NM_OWNERS"
    nci_nm = set(_nonstd(rows, field, nci=True))
    if pl is not None and len(nci_nm) == 1 and not own:
        return pl - nci_nm.pop(), "TOTAL_MINUS_NM_NCI"
    if pl is not None:
        return pl, "TOTAL"
    return None, "NONE"


def _nonstd(rows: list, field: str, nci: bool) -> list[float]:
    """표준 계정코드가 없는 손익 줄 가운데 이름이 지배(nci=False) 또는 비지배(nci=True) 귀속인 줄의 값."""
    out = []
    for r in rows or []:
        if r.get("sj_div") not in ("CIS", "IS") or not str(r.get("account_id") or "").startswith("-표준"):
            continue
        nm = str(r.get("account_nm") or "").replace(" ", "")
        if "지배" not in nm or ("비지배" in nm) != nci or "주당" in nm:
            continue
        v = r.get(field)
        if v in (None, "", "-"):
            continue
        try:
            out.append(float(str(v).replace(",", "")))
        except (TypeError, ValueError):
            continue
    return out


def ctrl_eq(rows: list, field: str = "thstrm_amount") -> float | None:
    """지배자본 — firm(`price_multiple_data._ctrl_equity`)과 같은 규칙(지배 귀속 → 총자본 − 비지배)."""
    v = _g(rows, "EquityAttributableToOwnersOfParent", ("BS",), field)
    if v is not None:
        return v
    tot = _g(rows, "Equity", ("BS",), field)
    if tot is None:
        return None
    return tot - (_g(rows, "NoncontrollingInterests", ("BS",), field) or 0)


def term_no(rows: list, nm_field: str) -> int | None:
    for sj in ("CIS", "IS", "BS"):
        for r in rows or []:
            if r.get("sj_div") == sj:
                m = _TERM_RE.search(str(r.get(nm_field) or ""))
                if m:
                    return int(m.group(1))
    return None


def rows_rcept(rows: list) -> str | None:
    seen: Counter = Counter(str(r.get("rcept_no") or "") for r in rows or [] if r.get("rcept_no"))
    return seen.most_common(1)[0][0] if seen else None


# ── 공시 목록 → 결산 달력 ────────────────────────────────────────────────────────

def parse_listing(items: list[dict]) -> list[tuple[str, str, str, str]]:
    """DART 공시 목록(list.json 의 list) → [(종류 'A'(사업보고서)·'I'(반기·분기보고서), 기준일 YYYYMMDD(월말), 접수번호, 접수일)]."""
    out = []
    for it in items or []:
        m = _REPORT_RE.search(str(it.get("report_nm") or ""))
        if not m:
            continue
        kind = "A" if m.group(1) == "사업보고서" else "I"
        out.append((kind, month_end(int(m.group(2)), int(m.group(3))), str(it.get("rcept_no") or ""), str(it.get("rcept_dt") or "")))
    return sorted(out, key=lambda r: (r[1], r[3], r[2]))


def calendar_of(listing: list[tuple[str, str, str, str]]) -> dict[str, Any] | None:
    """사업보고서 기준일이 3·6개월 간격으로 이어지는 회사의 결산 달력 — 아니면 None(12개월 결산 · 결산월 변경 회사).

    {"step": 3|6, "ends": [결산기준일 오름차순], "filed": {기준일: 최초 접수일}, "interims": {3개월 시점: 최초 접수일}, "by_rcept": {접수번호: (종류, 기준일)}}.
    간격은 사업보고서 기준일 차이의 최빈값이다(첫 결산기는 짧거나 길 수 있다). 최빈값이 3·6 이 아니거나 사업보고서가 셋 미만이면 None."""
    ends = sorted({r[1] for r in listing if r[0] == "A"})
    if len(ends) < 3:
        return None
    diffs = Counter(_mdiff(a, b) for a, b in zip(ends, ends[1:]))
    step, n = diffs.most_common(1)[0]
    if step not in (3, 6) or n < len(ends) - 2:     # 첫 간격 하나만 다를 수 있다
        return None
    filed: dict[str, str] = {}
    interims: dict[str, str] = {}
    for kind, end, _rc, dt in listing:
        tgt = filed if kind == "A" else interims
        if dt and (end not in tgt or dt < tgt[end]):
            tgt[end] = dt
    return {"step": step, "ends": ends, "filed": filed, "interims": interims,
            "by_rcept": {rc: (kind, end) for kind, end, rc, _ in listing if rc}}


# ── 사실값 모으기 ──────────────────────────────────────────────────────────────

def collect_facts(cal: dict[str, Any], annual_rows: list[list], interim_rows: list[list],
                  extra: dict[str, dict] | None = None) -> dict[str, Any]:
    """사업보고서 원문(연도마다 가장 늦은 것)·3개월 시점 보고서 원문 → 결산기·시점 사실값.

    결산기: {기준일: {"ni", "eq", "term", "how", "filed", "regular"}} — 사업보고서 하나의 당기·전기·전전기 칸을 기수로 묶는다
    (기수가 하나씩 줄어야 하고, 기준일 = 당기 기준일 − 칸 차 × 간격. 목록에 있는 기준일과 어긋나면 그 칸부터 쓰지 않는다). 같은 결산기가
    여러 보고서에 있으면 **새 보고서 값**(정정·지배 구분이 반영된 비교 칸 — firm `trailing_period_ni` 와 같은 규칙). 「filed」는 그 결산기가
    처음 공시된 날(목록의 그 기준일 사업보고서 최초 접수일, 상장 전 결산기면 그 값이 처음 실린 보고서 접수일).
    「regular」= 길이가 간격과 같다고 볼 수 있는 결산기(제1기 아님 · 앞 결산기 기준일과 간격만큼 떨어짐).
    시점: {3개월 시점 기준일: {"eq", "filed"}}. `extra` = 그 밖의 경로(XBRL 원본 등)로 얻은 결산기 값 {기준일: {"ni", "eq"}} — 비어 있는 칸만 채운다."""
    step, ends, by_rc = cal["step"], cal["ends"], cal["by_rcept"]
    reps = []
    for rows in annual_rows:
        rc = rows_rcept(rows)
        if not rc or by_rc.get(rc, ("", ""))[0] != "A":
            continue
        reps.append((by_rc[rc][1], rc, rows))
    reps.sort(key=lambda x: x[0], reverse=True)          # 새 보고서부터
    periods: dict[str, dict] = {}
    first_seen: dict[str, str] = {}
    for end0, rc, rows in reps:
        k0 = term_no(rows, "thstrm_nm")
        if k0 is None:
            continue
        for j, (amt, nm) in enumerate((("thstrm_amount", "thstrm_nm"), ("frmtrm_amount", "frmtrm_nm"),
                                       ("bfefrmtrm_amount", "bfefrmtrm_nm"))):
            if term_no(rows, nm) != k0 - j:
                break
            e = shift(end0, -j * step)
            if e >= ends[0] and e not in cal["filed"]:
                break                                       # 목록 범위 안인데 그 기준일 사업보고서가 없다 — 간격이 어긋난 칸
            ni, how = ctrl_ni(rows, amt)
            eq = ctrl_eq(rows, amt)
            dt = rc[:8]
            if e not in first_seen or dt < first_seen[e]:
                first_seen[e] = dt
            if e in periods:
                continue
            periods[e] = {"ni": ni, "eq": eq, "term": k0 - j, "how": how}
    for e, v in (extra or {}).items():
        p = periods.setdefault(e, {"ni": None, "eq": None, "term": None, "how": "EXTRA"})
        if p.get("ni") is None and v.get("ni") is not None:
            p["ni"], p["how"] = v["ni"], v.get("how") or "EXTRA"
        if p.get("eq") is None and v.get("eq") is not None:
            p["eq"] = v["eq"]
        if v.get("filed") and (e not in first_seen or v["filed"] < first_seen[e]):
            first_seen.setdefault(e, v["filed"])
    known = sorted(set(periods) | set(ends))
    for e, p in periods.items():
        p["filed"] = cal["filed"].get(e) or first_seen.get(e)
        prev = [x for x in known if x < e]
        if p.get("term") == 1:
            p["regular"] = False                            # 첫 결산기 — 시작일을 모른다
        elif prev:
            p["regular"] = _mdiff(prev[-1], e) == step
        else:
            p["regular"] = p.get("term") is not None        # 상장 전 결산기(목록 밖) — 기수가 이어지면 간격대로 본다
    points: dict[str, dict] = {}
    for rows in interim_rows:
        rc = rows_rcept(rows)
        if not rc or by_rc.get(rc, ("", ""))[0] != "I":
            continue
        pt = by_rc[rc][1]
        eq = ctrl_eq(rows, "thstrm_amount")
        if eq is not None and pt not in points:
            points[pt] = {"eq": eq, "filed": cal["interims"].get(pt) or rc[:8]}
    return {"step": step, "periods": periods, "points": points}


# ── 칸 만들기 ─────────────────────────────────────────────────────────────────

def _year_end(cal_ends: list[str], y: int) -> str | None:
    """달력 y 해에 끝나는 마지막 결산기 기준일(E_y)."""
    es = [e for e in cal_ends if e[:4] == str(y)]
    return max(es) if es else None


def build_cells(cal: dict[str, Any], facts: dict[str, Any], today: str) -> dict[str, Any]:
    """결산 달력·사실값 → {"annual": {y: 칸}, "quarter": {(y, q): 칸}}. 칸 = {"ni", "eq", "avail", "why"}.

    · 연간 y: E_y 에서 거꾸로 12 ÷ 간격 개 결산기가 모두 「regular」이고 순이익이 있어야 합(ni). 자본 = E_y 말. 공시일 = E_y 사업보고서 최초 접수일.
      합을 못 내면 ni=None(값을 비운다는 뜻이 아니라 쓰지 않는다 — 원본 칸 그대로).
    · 분기 (y, q): 그 달 말(C)이 오늘보다 앞이고, E_{y-1} 이 있고, (E_{y-1}, C] 에 끝나야 할 결산기·3개월 시점 보고서가 **모두 접수됐을 때만** 쓴다
      (아직 안 나온 보고서가 있으면 칸이 확정되지 않았다 — 다음 수집이 쓴다). 누적 = 그 결산기 순이익 합(하나라도 비면 None),
      자본 = C 이하 가장 늦은 재무상태표(결산기 말·3개월 시점 — 원문을 못 받은 시점은 건너뛰고 그 앞), 공시일 = 쓴 보고서들의 최초 접수일 가운데 가장 늦은 날.
      E_{y-1} 다음 결산기가 첫 결산기 등 길이를 모르는 결산기면 누적 None."""
    step = facts["step"]
    periods, points = facts["periods"], facts["points"]
    ends = sorted(set(cal["ends"]) | {e for e in periods})
    n_need = 12 // step
    out_a: dict[int, dict] = {}
    out_q: dict[tuple[int, int], dict] = {}
    years = sorted({int(e[:4]) for e in ends})
    last_known = ends[-1]
    # 달력 y 해의 마지막 결산기(E_y)는 **일정**으로 정한다 — 아직 공시 전인 결산기도 셈에 넣어야 올해(진행 중인 해)를 「6월이 마지막」으로 잘못 보지 않는다.
    sched = sorted(set(ends) | {shift(last_known, step * k) for k in range(1, 12 // step + 2)})
    for y in years:
        ey = _year_end(sched, y)
        if not ey:
            continue
        p_ey = periods.get(ey)
        if not p_ey or not p_ey.get("filed") or p_ey["filed"] > today:
            continue
        win = [shift(ey, -i * step) for i in range(n_need)][::-1]
        ok = all(w in periods and periods[w].get("regular") and periods[w].get("ni") is not None for w in win)
        ni = sum(periods[w]["ni"] for w in win) if ok else None
        out_a[y] = {"ni": round(ni) if ni is not None else None, "eq": p_ey.get("eq"), "avail": p_ey["filed"], "end": ey,
                    "own_ni": p_ey.get("ni"),
                    "why": why_text(p_ey["filed"], f"사업연도 {y} = {win[0][:6]}~{ey[:6]} 결산기 {len(win)}개"
                                    + (" 합" if ok else " 중 빈 결산기 — 순이익 합 없음") + f" · 간격 {step}개월")}
    t = str(today)
    nxt = shift(last_known, step)                     # 다음에 끝날 결산기(아직 공시 전)
    for y in years:
        prev_end = _year_end(sched, y - 1)
        if not prev_end or prev_end not in periods or not periods[prev_end].get("filed"):
            continue
        for q in (1, 2, 3):
            c = month_end(y, 3 * q)
            if c >= t:
                continue
            # 칸이 확정됐나 — 그 달 말까지 끝났어야 할 결산기(다음 결산기 `nxt`)·3개월 시점(반기 결산: 결산기 말 − 3개월)이 아직 공시 전이면 다음 수집이 쓴다.
            if nxt <= c or (step == 6 and shift(nxt, -3) <= c and shift(nxt, -3) not in cal["interims"]):
                continue
            exp_p = [e for e in ends if prev_end < e <= c]
            if any(p not in periods or not periods[p].get("filed") or periods[p]["filed"] > t for p in exp_p):
                continue                                     # 받지 못한 결산기 — 칸을 쓰지 않는다
            ok = all(periods[p].get("regular") and periods[p].get("ni") is not None for p in exp_p)
            ni = round(sum(periods[p]["ni"] for p in exp_p)) if ok else None
            # 자본 — C 이하 가장 늦은 재무상태표(값이 있는 것)
            bs = [(p, periods[p]["eq"], periods[p]["filed"]) for p in periods
                  if p <= c and periods[p].get("eq") is not None and periods[p].get("filed")]
            bs += [(p, v["eq"], v["filed"]) for p, v in points.items() if p <= c and v.get("eq") is not None]
            if not bs and ni is None:
                continue
            src = max(bs, key=lambda x: (x[0], x[2])) if bs else None
            dates = [periods[p]["filed"] for p in exp_p] + ([src[2]] if src else []) + [cal["filed"].get(prev_end) or periods.get(prev_end, {}).get("filed") or ""]
            avail = max(d for d in dates if d)
            lab = "+".join(p[:6] for p in exp_p) if exp_p else "없음"
            out_q[(y, q)] = {"ni": float(ni) if ni is not None else (0.0 if ok else None), "eq": src[1] if src else None, "avail": avail,
                             "why": why_text(avail, f"{prev_end[:6]} 다음 결산기 누적({lab})"
                                             + ("" if ok else " — 길이를 모르는 결산기·빈 값이 있어 순이익 없음")
                                             + (f" · 자본 {src[0][:6]}" if src else "") + f" · 간격 {step}개월")}
    return {"annual": out_a, "quarter": out_q}


# ── 배치(집계·파생)가 읽는 쪽 ─────────────────────────────────────────────────────

#: 표시 칸 전부(모든 분기 — 사업보고서 칸 포함) — 자리표시 하나 = `MARK_LIKE`.
MARKED_SQL = (f"SELECT ticker, fy, quarter, ni_cum_restated, eq_restated, {AVAIL_SQL} FROM dart_finstat_q "
              "WHERE restate_why LIKE %s")


def load_marked(con) -> list[tuple]:
    """배치용: 표시 칸 행 [(ticker, fy, quarter, 순이익 누적, 자본, 공시일)]. `con` = psycopg 접속."""
    return list(con.execute(MARKED_SQL, (MARK_LIKE,)))


def apply_marked(fin_all: dict | None, finq_all: dict | None, rows, keep_q4: bool = False) -> dict[str, dict]:
    """배치(시장·업종 집계, 재무 요약 파생)용 — 종목별 재무 dict 를 결산기 칸으로 고친다. 반환 {ticker: {(fy, quarter): 공시일}}.

    표시 칸이 있는 종목은 서버(`price_multiple_data._firm_fin_by_q`·`_firm_fin_by_fy`)와 같은 규칙이다 — 1~3분기 칸은 표시 칸만(값은 정정 칸만,
    표시 없는 칸은 DART 가 준 그해 가장 늦은 3개월 시점 보고서라 버린다), 연간은 표시된 사업보고서 칸 값(순이익 합을 못 낸 해는 None).
    `keep_q4` = 분기 dict 에 사업보고서 칸도 둔다(재무 요약 파생은 분기 표의 사업보고서 칸을 연간으로 읽는다)."""
    by: dict[str, dict] = {}
    for t, fy, q, ni, eq, av in rows:
        by.setdefault(t, {})[(int(fy), int(q))] = (float(ni) if ni is not None else None,
                                                    float(eq) if eq is not None else None, av)
    out: dict[str, dict] = {}
    for t, cs in by.items():
        if finq_all is not None:
            finq_all[t] = {k: (v[0], v[1]) for k, v in cs.items() if keep_q4 or k[1] != 4}
        if fin_all is not None:
            fa = fin_all.setdefault(t, {})
            for (fy, q), v in cs.items():
                if q == 4:
                    fa[fy] = (v[0], v[1] if v[1] is not None else (fa.get(fy) or (None, None))[1])
        out[t] = {k: v[2] for k, v in cs.items() if v[2]}
    return out
