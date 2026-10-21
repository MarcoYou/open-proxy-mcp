"""업종 구성·비중 유니버스 — 「반도체 업종」「중분류 …」「대분류 …」을 구성 종목·시총·업종 안 비중으로 (261021).

계기: 「반도체 업종이 뭘로 이뤄졌고 비중이 얼마냐」「업종 수익률을 종목 기여로 쪼개 보자」에 답하는 길이 없었다.
`trading_data(scope="sector")` 는 업종 **합계**(시총 집계 `krx_cap_agg`)만 주고 **구성 종목**은 주지 않는다.
업종분류 저장분(`wise_sector`, 종목마다 한 줄)과 시세 단면(`krx_daily_v` 또는 주간 `krx_weekly`)을 그 날짜로 맞대 읽으면 구성·비중이 나온다 —
새로 쌓을 것은 없고(DB 0 변경) 읽을 때 계산한다. 이 목록은 앞으로 업종 기여 분해·블렌딩 도구가 부르는 바탕이 된다.

■ 문법(`parse` — 순수 함수): 업종 표지(「업종」「섹터」「대분류」「중분류」「하위업종」)가 있으면 업종 조회다.
    「업종 반도체」·「반도체 업종」·「중분류 반도체와반도체장비」·「대분류 IT」·「코스피 반도체 업종 상위 10」·「코스닥 IT 섹터」.
  업종 이름 자리만 읽는다 — 표지 바로 앞뒤 낱말 묶음(최대 5개)을 이름으로 대 본다. 문장 속 군더더기(「…종목의 영업이익이」)는 이름 후보가 못 된다.
■ 이름 맞추기(`resolve` — 순수 함수): ① 정확(공백·쉼표·대소문자 무시, 업종 코드 G4530 포함) ② 별칭(바이오·헬스케어) ③ 부분 일치(두 글자 이상).
  하나로 정해지면 쓰고, 여럿이면 **추측하지 않고 후보를 보여 되묻는다**. 대분류와 중분류에 이름이 같고 그 날 구성도 같으면(에너지·소재·유틸리티)
  중분류로 풀고 밝힌다 — 구성이 다르면 되묻는다. KSIC(한국표준산업분류)는 이 서버의 업종 구성 축이 아니다 — 그렇게 안내한다.
■ 날짜(`_pick_day`): 그 날 이하 가장 가까운 앞 거래일. 일별 보관 창(약 1년) 안이면 일별 시세(`krx_daily_v`), 더 오래되면 주간 시세(`krx_weekly`,
  주 마지막 거래일 한 점 — 주간 해상도), 주간 구간보다 이르면 제공하지 않는다(그 앞은 종목별 연말 행뿐이라 구성이 불완전하다).
■ 분류: 그 날 이하 가장 최근 분류 스냅샷, 없으면 가장 이른 스냅샷을 **소급**(`scripts/krx_cap_agg.py` 와 같은 규칙 — 업종 합이 같다). 소급 구간은 경고한다.
  스냅샷에 없는 종목(신규 상장·공급처가 분류를 안 준 종목)은 미분류로 밝히고 어느 업종에도 넣지 않는다. 우선주는 빼지 않는다 —
  시총 집계(`krx_cap_agg`)가 우선주 포함 합이고(`scope="sector"`), 분류 저장분이 우선주에 같은 회사 보통주의 업종을 이어 주므로(`src='pref'`)
  업종 합이 두 도구에서 같아야 한다. 우선주 행은 `preferred: true`·`issuer_ticker`(보통주 코드)를 단다.
■ 비중: 종목 시총 ÷ **선택한 범위의 시총 합**(업종 전체, 시장을 골랐으면 그 시장 안의 업종). 상위 N 으로 잘라도 분모는 그대로다(잘린 표의 합은 100% 미만) —
  기여 분해가 쓰는 가중치라 다시 100% 로 맞추지 않는다.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from open_proxy_mcp.db import pg_rows
from open_proxy_mcp.market_codes import KQ, KS, to_label
from open_proxy_mcp.services import wise_sector_dates as _WSD
from open_proxy_mcp.services.sector_class import MAJOR, MID

# ── 문법 ──────────────────────────────────────────────────────────────────────

#: 업종 표지. 「업종분류」「하위업종」을 「업종」보다 앞에 둬야 통째로 잡힌다.
_MARKER_WORDS = ("업종분류", "하위업종", "업종", "섹터", "sector", "대분류", "중분류")
_MARKER_RE = re.compile("(" + "|".join(_MARKER_WORDS) + ")", re.I)
_LEVEL_OF = {"대분류": MAJOR, "중분류": MID, "하위업종": MID}
#: KSIC(한국표준산업분류) — 업종 구성 축이 아니다. 걸리면 안내만 한다.
_KSIC = re.compile(r"ksic|표준\s*산업\s*분류|산업\s*분류\s*코드", re.I)
_MARKETS = ((KS, ("코스피", "kospi", "유가증권")), (KQ, ("코스닥", "kosdaq")))
_RANK_RE = re.compile(
    r"(?:시총|시가총액)?\s*(?:상위|top)\s*(\d{1,4})\s*(?:개)?\s*(?:종목|기업|회사)?|(\d{1,4})\s*개\s*(?:종목|기업|회사)?", re.I)
#: 업종 이름이 아닌 군더더기 낱말(낱말 전체가 같을 때만 버린다).
_NOISE = frozenset({
    "구성", "구성종목", "종목", "종목들", "종목의", "비중", "목록", "리스트", "전체", "시총", "시가총액", "순위", "순", "표",
    "알려줘", "보여줘", "뽑아줘", "줘", "좀", "의", "를", "을", "은", "는", "이", "가", "에서", "별", "기준", "조회", "상세", "내",
})
#: 회사 이름 자리에 문장이 들어왔을 때 그 문장을 업종 조회로 읽을지 가르는 말 — 업종 표지만으로는 「삼성전자 업종」 같은 회사 질문을 가로챌 수 있다.
_LIST_WORDS = ("상위", "top", "구성", "비중", "종목", "목록", "리스트", "시총", "시가총액")
#: 부르는 말 별칭 — 업종 이름에 글자가 없는 흔한 말만. (정규화한 말 → 업종 이름들)
_ALIASES = {"바이오": ("제약과생물공학",), "헬스케어": ("건강관리",)}
_DD = re.compile(r"^\d{8}$")


@dataclass(frozen=True)
class SectorQuery:
    raw: str
    windows: tuple[str, ...] = ()      # 업종 이름 후보 낱말 묶음(우선순위 순)
    level: str | None = None           # MAJOR / MID / None(이름으로 가림)
    market: str | None = None          # KS / KQ / None(양쪽)
    top: int | None = None             # 시총 상위 N
    ksic: bool = False


def is_sector_phrase(text: str) -> bool:
    """업종 조회 말인가 — 업종 표지나 KSIC 표현이 있으면 그렇다."""
    t = (text or "").strip()
    return bool(t) and bool(_MARKER_RE.search(t) or _KSIC.search(t))


def looks_like_sector_list_sentence(text: str) -> bool:
    """회사 이름 자리에 들어온 문장이 업종 구성 조회인가 — 업종 표지 + 목록을 바라는 말이 함께 있을 때만."""
    t = (text or "").strip().lower()
    return is_sector_phrase(t) and any(w in t for w in _LIST_WORDS)


def norm(s: str) -> str:
    """이름 비교용 — 공백·쉼표·가운뎃점·괄호·콜론을 없애고 소문자로."""
    return re.sub(r"[\s,，·ㆍ/\-_()（）\[\]:：=]+", "", s or "").lower()


def parse(raw: str) -> SectorQuery | None:
    """업종 조회 말을 (이름 후보, 단계, 시장, 상위 N) 으로. 업종 말이 아니면 None."""
    text = (raw or "").strip()
    if not is_sector_phrase(text):
        return None
    ksic = bool(_KSIC.search(text))
    level: str | None = None
    for m in _MARKER_RE.finditer(text):
        lv = _LEVEL_OF.get(m.group(1))
        if lv:
            level = lv
            break
    top = None
    rm = _RANK_RE.search(text)
    if rm:
        top = int(rm.group(1) or rm.group(2))
        text = text[:rm.start()] + " " + text[rm.end():]
    kinds = []
    for code, words in _MARKETS:
        for w in words:
            if re.search(re.escape(w), text, re.I):
                kinds.append(code)
                text = re.sub(re.escape(w), " ", text, flags=re.I)
    market = kinds[0] if len(set(kinds)) == 1 else None      # 둘 다 적었으면 양쪽
    # 표지를 낱말로 떼어 놓는다(「반도체업종」→「반도체 업종」)
    text = _MARKER_RE.sub(lambda m: f" {m.group(1)} ", text)
    tokens = [t for t in re.split(r"[\s:：=]+", text) if t and t not in _NOISE]
    marks = {i for i, t in enumerate(tokens) if _MARKER_RE.fullmatch(t)}
    windows: list[str] = []
    for i in sorted(marks):
        for k in range(5, 0, -1):               # 표지 뒤 → 앞, 긴 묶음부터
            seg = tokens[i + 1:i + 1 + k]
            if seg and not (marks & set(range(i + 1, i + 1 + len(seg)))):
                windows.append(" ".join(seg))
        for k in range(5, 0, -1):
            seg = tokens[max(0, i - k):i]
            if seg and not (marks & set(range(max(0, i - k), i))):
                windows.append(" ".join(seg))
    seen: set[str] = set()
    uniq = tuple(w for w in windows if not (w in seen or seen.add(w)))
    return SectorQuery(raw=(raw or "").strip(), windows=uniq, level=level, market=market, top=top, ksic=ksic)


# ── 이름 맞추기 ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Entry:
    level: str          # MAJOR / MID
    code: str           # G45 / G4530
    name: str
    major: str          # 소속 대분류 이름(대분류면 자기 이름)


def _label(e: Entry) -> str:
    return f"{e.level} {e.name}({e.code})"


def _ask_pick(raw: str, hits: list[Entry]) -> str:
    opts = " · ".join(_label(e) for e in hits)
    ex = hits[0]
    return (f"「{raw}」라는 말은 여러 업종에 걸려 하나로 정하지 않았습니다 — 골라 주세요: {opts}. "
            f"예: universe=\"{ex.level} {ex.name}\"")


def _all_names(catalog: list[Entry]) -> str:
    majors = [e.name for e in catalog if e.level == MAJOR]
    mids = [e.name for e in catalog if e.level == MID]
    return f"대분류({len(majors)}): {', '.join(majors)} / 중분류({len(mids)}): {', '.join(mids)}"


def resolve(q: SectorQuery, catalog: list[Entry], members_of: Callable[[Entry], frozenset[str]]
            ) -> tuple[Entry | None, str, str]:
    """업종 이름 → (고른 업종, 풀이 근거 한 줄, 되물을 문장). 고르면 되물음은 빈 문자열, 못 고르면 업종은 None."""
    if not catalog:
        return None, "", "업종분류 저장분이 비어 있어 업종 구성을 만들 수 없습니다."
    pool = [e for e in catalog if q.level in (None, e.level)]
    wins = [(w, norm(w)) for w in q.windows if norm(w)]
    label = q.windows[0] if q.windows else q.raw

    def pick(hits: list[Entry], how: str, w: str) -> tuple[Entry | None, str, str]:
        hits = list({(e.level, e.code): e for e in hits}.values())
        if len(hits) == 2 and {e.level for e in hits} == {MAJOR, MID} and hits[0].name == hits[1].name \
                and members_of(hits[0]) == members_of(hits[1]):
            mid = next(e for e in hits if e.level == MID)
            return mid, (f"「{w}」: 대분류와 중분류에 같은 이름이고 그 날 구성도 같아 중분류 {mid.name}({mid.code})로 풀었다."), ""
        if len(hits) == 1:
            e = hits[0]
            note = "" if how == "정확" else f"「{w}」: {_label(e)} 로 풀었다({how} 일치 — 아니면 이름을 정확히 주세요)."
            return e, note, ""
        return None, "", _ask_pick(w, hits)

    # ① 정확 ② 별칭 — 모든 낱말 묶음을 먼저 본다
    for w, nw in wins:
        hits = [e for e in pool if norm(e.name) == nw or e.code.lower() == nw]
        if hits:
            return pick(hits, "정확", w)
    for w, nw in wins:
        names = _ALIASES.get(nw)
        if names:
            hits = [e for e in pool if e.name in names]
            if hits:
                return pick(hits, "별칭", w)
    # ③ 부분 일치 — 짧은 말(한 글자)은 뜻이 너무 넓어 쓰지 않는다
    for w, nw in wins:
        if len(nw) < 2:
            continue
        hits = [e for e in pool if nw in norm(e.name) or (len(norm(e.name)) >= 3 and norm(e.name) in nw)]
        if hits:
            return pick(hits, "부분", w)
    # 단계를 정했는데 그 단계에 없고 다른 단계에 있으면 — 추측하지 않고 알려 준다
    if q.level:
        other = [e for e in catalog if e.level != q.level]
        for w, nw in wins:
            hits = [e for e in other if norm(e.name) == nw or (len(nw) >= 2 and nw in norm(e.name))]
            if hits:
                h = hits[0]
                return None, "", (f"「{w}」: {q.level}에는 없고 {h.level} 「{h.name}」({h.code})의 이름입니다. "
                                  f"그 업종이면 universe=\"{h.level} {h.name}\" 로 불러 주세요.")
    if not q.windows:
        return None, "", ("어느 업종인지 알 수 없습니다 — 업종 이름을 함께 주세요(예: universe=\"업종 반도체\"). 가능한 업종 — "
                          + _all_names(catalog))
    return None, "", (f"「{label}」: 맞는 업종을 찾지 못했습니다 — 가능한 업종: {_all_names(catalog)}. "
                      "예: universe=\"중분류 반도체와반도체장비\"")


KSIC_ANSWER = ("KSIC(한국표준산업분류) 기준 구성은 제공하지 않습니다 — 이 서버의 업종 구성은 업종분류(대분류 10·중분류 28) 기준입니다. "
               "KSIC 축은 업종 배수·시총 집계를 업종분류로 옮기면서 20260821 에 집계 저장을 멈춘(동결) 상태이고, 종목별 KSIC 업종코드는 "
               "DART 기업개황의 값이라 구성 종목 조회에 쓰지 않습니다. 업종분류 이름으로 다시 불러 주세요"
               "(예: universe=\"중분류 반도체와반도체장비\").")


# ── 날짜 ──────────────────────────────────────────────────────────────────────

def norm_date(s: str) -> str | None:
    """2026-10-15·2026.10.15·2026/10/15·20261015 → 20261015. 달력에 없는 날이면 None."""
    t = re.sub(r"[-./\s]", "", (s or "").strip())
    if not _DD.match(t):
        return None
    try:
        date(int(t[:4]), int(t[4:6]), int(t[6:]))
    except ValueError:
        return None
    return t


_DAYS_SQL = ("SELECT (SELECT max(dd) FROM krx_day WHERE n > 0 AND dd <= %s), "
             "(SELECT min(dd) FROM krx_day WHERE n > 0), (SELECT max(dd) FROM krx_day WHERE n > 0)")
#: 주간 시세의 날짜들 — 뷰 모드(`krx_weekly` = 압축 표 ∪ 일별 주 닫는 날)는 작은 달력 둘에서 읽는다(`krx_retention.WEEKLY_DAYS_SQL`).
#: 달력 표가 없는 DB(교체 전·표 모드)는 오류로 None 이 와서 `krx_weekly` 를 직접 읽는 질의로 대신한다.
_WEEK_PICK_SQL = ("SELECT max(dd) FROM (SELECT dd, n FROM krx_wk_day WHERE n > 0 "
                  "UNION ALL SELECT d.dd, d.n FROM krx_day d WHERE d.wk_close AND d.n > 0 "
                  "AND NOT EXISTS (SELECT 1 FROM krx_wk_day x WHERE x.wk_dd = d.trd_dd)) c WHERE dd <= %s AND dd >= %s")
_WEEK_PICK_TABLE_SQL = "SELECT max(price_dd) FROM krx_weekly WHERE price_dd <= %s AND price_dd >= %s"


@dataclass
class DayPick:
    dd: str | None = None
    source: str = "daily"                 # daily / weekly
    note: str = ""                        # 요청일을 앞 거래일로 푼 근거
    error: str = ""                       # 비어 있지 않으면 제공할 수 없는 날짜(사용자에게 그대로)
    db_ok: bool = True
    daily_lo: str | None = None


async def _pick_day(as_of: str) -> DayPick:
    """요청일 이하 가장 가까운 앞 거래일. 요청이 없으면 보관된 최신 거래일."""
    req = norm_date(as_of) if (as_of or "").strip() else ""
    if req is None:
        return DayPick(error=f"as_of 「{as_of}」은 날짜가 아닙니다 — YYYYMMDD 또는 YYYY-MM-DD 로 주세요.")
    rows = await asyncio.to_thread(pg_rows, _DAYS_SQL, (req or "99999999",))
    if rows is None or not rows or rows[0][2] is None:
        return DayPick(db_ok=False)
    pick, lo, hi = rows[0]
    if not req:
        return DayPick(dd=hi, daily_lo=lo)
    if req > hi:
        return DayPick(dd=hi, daily_lo=lo,
                       note=f"요청일 {req} 은 보관된 최신 거래일 {hi} 보다 뒤라 최신 거래일로 풀었다.")
    if req >= lo and pick:
        note = "" if pick == req else f"요청일 {req} 은 거래일이 아니어서(휴장·주말) 그 앞 거래일 {pick} 로 풀었다."
        return DayPick(dd=pick, daily_lo=lo, note=note)
    # 일별 보관 창(약 1년)보다 오래됨 → 주간 시세
    from open_proxy_mcp.services.price_multiple_data import _weekly_bounds
    _latest_wk, floor = await _weekly_bounds()
    if req < floor:
        return DayPick(daily_lo=lo, error=(
            f"{_ymd(req)} 은 주간 시세가 촘촘한 구간({_ymd(floor)}~) 보다 이릅니다 — 그 앞은 종목별로 해마다 마지막 한 점만 "
            "보관해 그 날의 업종 구성을 만들 수 없습니다. 업종 시총 합계의 긴 시계열은 `scope=\"sector\"` 에 있습니다(2015-12~)."))
    wk = await asyncio.to_thread(pg_rows, _WEEK_PICK_SQL, (req, floor))
    if wk is None:
        wk = await asyncio.to_thread(pg_rows, _WEEK_PICK_TABLE_SQL, (req, floor))
    if wk is None:
        return DayPick(db_ok=False)
    if not wk or not wk[0][0]:
        return DayPick(daily_lo=lo, error=f"{_ymd(req)} 이하의 주간 시세가 {_ymd(floor)} 이후에 없습니다.")
    dd = wk[0][0]
    return DayPick(dd=dd, source="weekly", daily_lo=lo, note=(
        f"요청일 {req} 은 일별 보관 창({lo}~) 밖이라 주간 시세(주 마지막 거래일 한 점, 주간 해상도)에서 읽는다 — 그 앞 주 마지막 거래일 {dd} 로 풀었다."
        if dd != req else f"요청일 {req} 은 일별 보관 창({lo}~) 밖이라 주간 시세(주 마지막 거래일 한 점, 주간 해상도)에서 읽는다."))


# ── 읽기 ──────────────────────────────────────────────────────────────────────

#: 한 날의 전 종목 + 그 날의 분류 스냅샷(그 날 이하 가장 최근, 없으면 가장 이른 것 — `krx_cap_agg` 와 같은 규칙).
#: 분류 칸이 NULL 이면 미분류(스냅샷에 없는 종목). 시총 > 0 — `krx_cap_agg` 의 조건과 같다.
_ROWS_SQL = """\
SELECT d.ticker, d.market, d.close, d.mktcap, d.list_shrs,
       w.sector_code, w.sector, w.industry_code, w.industry, w.src, w.src_ref,
       s.snap, s.snap_min
FROM {src} d
CROSS JOIN (SELECT COALESCE({snap_le},
                            {snap_min}) AS snap,
                   {snap_min} AS snap_min) s
LEFT JOIN wise_sector w ON w.snap_dd = s.snap AND w.ticker = d.ticker
WHERE d.{col} = %s AND d.mktcap > 0
ORDER BY d.mktcap DESC, d.ticker
"""
_SRC = {"daily": ("krx_daily_v", "trd_dd"), "weekly": ("krx_weekly", "price_dd")}
_ROW_KEYS = ("ticker", "market", "close", "mktcap", "shrs", "major_code", "major", "mid_code", "mid", "src", "src_ref",
             "snap", "snap_min")


def _read_rows(dd: str, source: str) -> list[dict[str, Any]] | None:
    table, col = _SRC[source]

    def sql(fast: bool) -> str:
        # 날짜 세 곳(이하 가장 최근 · 가장 이른 둘)은 뷰 모드면 작은 날짜 표에서 읽는다(뷰 위 max/min 은 요청마다 약 10만 행을 세 번 펼쳤다 — `wise_sector_dates`)
        return _ROWS_SQL.format(src=table, col=col, snap_le=_WSD.max_dd_le(fast), snap_min=_WSD.min_dd(fast))

    rows = _WSD.query(sql, (dd, dd), pg_rows)
    if rows is None:
        return None
    return [dict(zip(_ROW_KEYS, r)) for r in rows]


def _catalog(rows: list[dict[str, Any]]) -> list[Entry]:
    majors: dict[str, str] = {}
    mids: dict[str, tuple[str, str]] = {}
    for r in rows:
        if r["major_code"]:
            majors[r["major_code"]] = r["major"]
            mids[r["mid_code"]] = (r["mid"], r["major"])
    out = [Entry(MAJOR, c, n, n) for c, n in sorted(majors.items())]
    out += [Entry(MID, c, n, mj) for c, (n, mj) in sorted(mids.items())]
    return out


def _is_member(r: dict[str, Any], e: Entry) -> bool:
    return r["major_code"] == e.code if e.level == MAJOR else r["mid_code"] == e.code


def _pct(num: int, den: int) -> float:
    return round(num / den * 100, 4) if den else 0.0


def _ymd(dd: str) -> str:
    return f"{dd[:4]}-{dd[4:6]}-{dd[6:]}"


@dataclass
class SectorResult:
    """`compute` 의 결과 — 호출부(`universe.list_universe`)가 `UniverseList` 로 옮긴다."""
    rows: list[dict[str, Any]] = field(default_factory=list)
    sector: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    question: str = ""
    notice: str = ""                      # 못 만든 사유(no_data)
    label: str = ""
    spec: str = ""


def compute(q: SectorQuery, rows: list[dict[str, Any]], day: DayPick, names: dict[str, str]) -> SectorResult:
    """그 날 전 종목 행 + 분류 → 업종 구성·비중. 순수 함수(DB 없이 시험한다)."""
    from open_proxy_mcp.company_resolver import issuer_ticker
    catalog = _catalog(rows)
    cache: dict[tuple[str, str], frozenset[str]] = {}

    def members_of(e: Entry) -> frozenset[str]:
        k = (e.level, e.code)
        if k not in cache:
            cache[k] = frozenset(r["ticker"] for r in rows if _is_member(r, e))
        return cache[k]

    if q.top is not None and q.top < 1:
        return SectorResult(question="상위 N 은 1 이상이어야 합니다 — 예: universe=\"코스피 반도체 업종 상위 10\".")
    entry, how, ask = resolve(q, catalog, members_of)
    if entry is None:
        return SectorResult(question=ask)

    dd = day.dd or ""
    snap, snap_min = (rows[0]["snap"], rows[0]["snap_min"]) if rows else (None, None)
    in_mkt = [r for r in rows if q.market in (None, r["market"])]
    mkt_cap = sum(int(r["mktcap"]) for r in in_mkt)
    by_cap = lambda r: (-int(r["mktcap"]), r["ticker"])      # noqa: E731 — 시총 내림차순, 같으면 코드순(읽기 질의의 정렬과 같다)
    members = sorted((r for r in in_mkt if _is_member(r, entry)), key=by_cap)
    market_word = to_label(q.market) if q.market else None
    label = f"{entry.name}({entry.level})" + (f" · {market_word}" if market_word else "") \
        + (f" 시총상위 {q.top}" if q.top else "")
    spec = f"sector:{entry.level}:{entry.code}" + (f":{q.market}" if q.market else "") + (f":top{q.top}" if q.top else "")
    if not members:
        whole = sum(1 for r in rows if _is_member(r, entry))
        return SectorResult(label=label, spec=spec, notice=(
            f"{_ymd(dd)} 기준 {entry.name}({entry.level})에 {market_word or ''} 종목이 없습니다"
            + (f" — 이 업종 전체는 {whole}종목(다른 시장)입니다." if whole else ".")))

    total = sum(int(r["mktcap"]) for r in members)
    preferred_n = 0
    out: list[dict[str, Any]] = []
    cum = 0
    for r in members:
        t = r["ticker"]
        cap = int(r["mktcap"])
        cum += cap
        row: dict[str, Any] = {"rank": len(out) + 1, "ticker": t, "name": names.get(t) or "-", "market": to_label(r["market"]),
                               "mktcap_krw": cap, "close_krw": int(r["close"]) if r["close"] is not None else None,
                               "list_shrs": int(r["shrs"]) if r["shrs"] is not None else None,
                               "weight_pct": _pct(cap, total), "cum_weight_pct": _pct(cum, total)}
        if r["src"] == "pref":                            # 우선주 — 같은 회사 보통주 분류를 이은 행(src_ref = 보통주 코드)
            base = r["src_ref"] or issuer_ticker(t)
            row["preferred"] = True
            row["issuer_ticker"] = base
            if not row["name"] or row["name"] == "-":
                row["name"] = f"{names[base]} 우선주" if names.get(base) else "-"
            preferred_n += 1
        if entry.level == MAJOR:
            row["mid"] = r["mid"]
        out.append(row)
    shown = out[:q.top] if q.top else out
    shown_cap = sum(x["mktcap_krw"] for x in shown)

    unclassified = sorted((r for r in in_mkt if not r["major_code"]), key=by_cap)
    unc_cap = sum(int(r["mktcap"]) for r in unclassified)
    sector: dict[str, Any] = {
        "level": entry.level, "code": entry.code, "name": entry.name, "major": entry.major,
        "market": to_label(q.market) if q.market else None,
        "classification_asof": snap, "classification_retro": bool(snap_min and dd and dd < snap_min),
        "n": len(members), "n_shown": len(shown), "preferred_n": preferred_n,
        "cap_krw": total, "market_cap_krw": mkt_cap, "share_of_market_pct": _pct(total, mkt_cap),
        "shown_cap_krw": shown_cap, "shown_weight_pct": _pct(shown_cap, total),
        "unclassified": {"n": len(unclassified), "cap_krw": unc_cap, "share_of_market_pct": _pct(unc_cap, mkt_cap),
                         "top": [{"ticker": r["ticker"], "name": names.get(r["ticker"]) or "-", "market": to_label(r["market"]),
                                  "mktcap_krw": int(r["mktcap"])} for r in unclassified[:10]]},
    }
    if entry.level == MAJOR:                              # 대분류는 중분류별 구성도 함께 — 블렌딩이 한 단계 아래 가중을 쓴다
        by: dict[str, dict[str, Any]] = {}
        for r in members:
            c = by.setdefault(r["mid_code"], {"code": r["mid_code"], "name": r["mid"], "n": 0, "cap_krw": 0})
            c["n"] += 1
            c["cap_krw"] += int(r["mktcap"])
        sector["children"] = [{**c, "weight_pct": _pct(c["cap_krw"], total)}
                              for c in sorted(by.values(), key=lambda c: -c["cap_krw"])]

    warns: list[str] = []
    if day.note:
        warns.append(day.note)
    if snap_min and dd and dd < snap_min:
        warns.append(f"⚠ 업종분류는 {_ymd(snap_min)} 부터 관측입니다 — {_ymd(dd)} 구성은 그 첫 관측을 **소급** 적용한 것이라 그때 그 업종이 아니었던 "
                     "회사가 섞이고, 그 뒤 상장폐지된 종목은 분류가 없어 빠집니다(생존 편향). 업종 합이 실제보다 작을 수 있습니다.")
    if how:
        warns.append(how)
    if unclassified:
        why = ("분류 첫 관측에 없는 종목이다(그 뒤 상장폐지됐거나 공급처가 분류를 주지 않은 종목)" if sector["classification_retro"]
               else "분류 스냅샷에 없는 종목이다(스냅샷 뒤 신규 상장이거나 공급처가 분류를 주지 않은 종목)")
        warns.append(f"미분류 {len(unclassified)}종목(시총 {_pct(unc_cap, mkt_cap):.2f}%)은 어느 업종에도 넣지 않았다 — {why}. "
                     "`share_of_market_pct` 의 분모는 미분류를 포함한 시장 전체 시총이다.")
    if preferred_n:
        warns.append(f"우선주 {preferred_n}종목이 들어 있다(순위표와 달리 빼지 않는다) — 업종 시총은 우선주 포함 합(`scope=\"sector\"` 시총 집계와 같은 정의)이고, "
                     "우선주는 같은 회사 보통주의 업종에 속한다. 회사 단위로 보려면 `issuer_ticker` 로 보통주와 합친다.")
    if q.top and len(members) > q.top:
        warns.append(f"시총 상위 {q.top}종목만 실었다(업종 전체 {len(members)}종목의 {sector['shown_weight_pct']:.2f}%) — "
                     "비중은 업종 **전체** 시총 대비라 표 합이 100% 에 못 미친다.")
    return SectorResult(rows=shown, sector=sector, warnings=warns, label=label, spec=spec)


async def list_sector_universe(raw: str, as_of: str = ""):
    """업종 문구 → 구성 종목·시총·비중(`UniverseList`). 호출부: `services/universe.list_universe`."""
    from open_proxy_mcp.services import universe as _uni
    q = parse(raw)
    if q is None:
        return _uni.UniverseList(spec="", label=raw, resolved=False, question="업종 표현으로 읽지 못했습니다.")
    if q.ksic:
        return _uni.UniverseList(spec="", label=raw, resolved=False, question=KSIC_ANSWER)
    day = await _pick_day(as_of)
    if day.error:
        return _uni.UniverseList(spec="", label=raw, resolved=False, question=day.error)
    if not day.db_ok or not day.dd:
        return _uni.UniverseList(spec="", label=raw, resolved=False, db_ok=False)
    rows = await asyncio.to_thread(_read_rows, day.dd, day.source)
    if rows is None:
        return _uni.UniverseList(spec="", label=raw, resolved=True, as_of=day.dd, db_ok=False)
    if not rows:
        return _uni.UniverseList(spec="", label=raw, resolved=False, as_of=day.dd,
                                 notice=f"{_ymd(day.dd)} 기준 저장분에 종목이 없습니다.")
    names = await _uni._names_by_ticker()
    res = compute(q, rows, day, names)
    if res.question:
        return _uni.UniverseList(spec="", label=raw, resolved=False, as_of=day.dd, question=res.question)
    if not res.rows:
        return _uni.UniverseList(spec=res.spec, label=res.label, resolved=False, as_of=day.dd, notice=res.notice)
    ul = _uni.UniverseList(spec=res.spec, label=res.label, resolved=True, as_of=day.dd, rows=res.rows)
    ul.sector = res.sector
    ul.extra_warnings = res.warnings
    ul.granularity = day.source
    return ul
