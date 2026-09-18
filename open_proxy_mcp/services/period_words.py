"""기간 말 → 기간 코드·날짜 — screener 카드 보기·흐름 보기가 같이 쓴다 (260918 전면 점검).

왜 한 곳인가: 같은 말이 보기마다 다르게 읽히면 안 된다. 260918 점검 때 「지난달」이 카드 보기에서는
최근 30일, 흐름 보기에서는 알아듣지 못함이었고, 「최근 7일」은 카드 보기 8일 · 흐름 보기 7일이었다.
말 → 코드(또는 날짜)는 여기서만 정하고, 창의 제약(카드 보기 3개월 한도, 흐름 보기 원장 범위)은 각 보기가 붙인다.

돌려주는 것은 `(code, cs, ce)` — 종전 `screener._nl_period` 계약 그대로.
- 굴러가는 창: `today` · `yesterday` · `since_yesterday` · `last_7d` · `last_30d` · `custom:N`(오늘 포함 N일)
- 달력 창(오늘 기준): `this_week`·`prev_week`·`this_month`·`prev_month`·`this_quarter`·`prev_quarter`·`ytd`·`prev_year`
  이번 주 = 월요일~오늘, 지난주 = 지난주 월~일, 이번 달 = 1일~오늘, 지난달 = 지난달 1일~말일, 분기·연도도 같은 식.
- 절대 날짜: `custom` + 시작·끝 YYYYMMDD — 날짜·월·분기·반기·연도, 「A부터 B까지」, 「A부터」(= A~오늘).
- 못 알아들으면 원문(소문자)을 그대로 돌려준다. 삼키지 않는다 — 각 보기가 「알아듣지 못했다」고 밝힌다.

실제 호출·요청에서 나온 말(260918 점검): 「오늘」「어제 이후」「오늘 자정부터」, 모델이 날짜로 바꿔 보낸 범위,
「작년」「전년」「3월」「4월부터 8월 10일 사이」「상반기」「2주 전」「지난달」「이번 주」. 부르는 모델은 설명에 있는
코드 이름을 흉내 내 `last_week`·`this_month` 같은 표기도 보낸다.

연도 없는 날짜·월·분기·반기는 **오늘 이전의 가장 최근 것**이다(9월에 「12월」 = 작년 12월, 「3분기」 = 올해 3분기).
범위의 앞쪽이 뒤쪽보다 늦으면 앞쪽을 한 해 당긴다(「11월부터 2월까지」 = 작년 11월 ~ 올해 2월).
"""
from __future__ import annotations

import re
from datetime import date, timedelta

ROLLING_CODES = frozenset({"today", "yesterday", "since_yesterday", "last_7d", "last_30d", "30d"})
THIS_CODES = ("this_week", "this_month", "this_quarter", "ytd")
PREV_CODES = ("prev_week", "prev_month", "prev_quarter", "prev_year")
CALENDAR_CODES = frozenset(THIS_CODES + PREV_CODES)
KNOWN_CODES = ROLLING_CODES | CALENDAR_CODES | {"custom"}

#: 사람에게 보일 이름(흐름 보기 안내문 등).
LABEL = {"this_week": "이번 주", "this_month": "이번 달", "this_quarter": "이번 분기", "ytd": "올해",
         "prev_week": "지난주", "prev_month": "지난달", "prev_quarter": "지난 분기", "prev_year": "작년"}

#: 코드처럼 들어오는 표기(영어·스네이크). 공백·하이픈은 밑줄로 바꾼 뒤 찾는다.
_CODE_ALIASES = {
    "this_week": "this_week", "wtd": "this_week", "week_to_date": "this_week",
    "last_week": "prev_week", "previous_week": "prev_week", "prev_week": "prev_week",
    "this_month": "this_month", "mtd": "this_month", "month_to_date": "this_month",
    "last_month": "prev_month", "previous_month": "prev_month", "prev_month": "prev_month",
    "this_quarter": "this_quarter", "qtd": "this_quarter", "quarter_to_date": "this_quarter",
    "last_quarter": "prev_quarter", "previous_quarter": "prev_quarter", "prev_quarter": "prev_quarter",
    "ytd": "ytd", "this_year": "ytd", "year_to_date": "ytd",
    "last_year": "prev_year", "previous_year": "prev_year", "prev_year": "prev_year",
    "today": "today", "yesterday": "yesterday", "since_yesterday": "since_yesterday",
    "last_7d": "last_7d", "last_7_days": "last_7d", "past_7_days": "last_7d", "past_week": "last_7d",
    "last_30d": "last_30d", "30d": "last_30d", "last_30_days": "last_30d", "past_30_days": "last_30d",
    "past_month": "last_30d",
    "last_90d": "custom:90", "last_3_months": "custom:90", "past_3_months": "custom:90",
    "custom": "custom",
}

#: 말 → 코드. 키는 **공백을 뺀** 소문자. 통째로 같을 때만 쓴다(부분 일치는 아래 _FALLBACK).
_WORDS: dict[str, str] = {}
for _code, _ws in {
    "today": ("오늘", "금일", "당일", "오늘하루", "오늘자정부터", "오늘0시부터", "오늘아침부터", "지금", "현재", "now"),
    "yesterday": ("어제", "전일", "어제하루"),
    "since_yesterday": ("어제부터", "어제이후", "어제이후로", "전일부터", "어제오늘", "sinceyesterday"),
    "this_week": ("이번주", "금주", "이번한주", "주초부터", "이번주초부터"),
    "prev_week": ("지난주", "저번주", "직전주", "지난주간", "지난주일주일"),
    "this_month": ("이번달", "금월", "당월", "월초부터", "이번달초부터", "이달", "이번한달"),
    "prev_month": ("지난달", "저번달", "전월", "직전달", "지난달한달"),
    "this_quarter": ("이번분기", "당분기", "금분기", "분기초부터"),
    "prev_quarter": ("지난분기", "전분기", "직전분기", "저번분기"),
    "ytd": ("올해", "금년", "연초부터", "올해초부터", "이번해", "올해들어", "연초이후", "올한해", "금년도", "올해전체"),
    "prev_year": ("작년", "지난해", "전년", "전년도", "작년한해", "지난한해"),
    "last_7d": ("일주일", "1주일", "지난일주일", "최근일주일", "한주", "지난한주", "최근한주", "한주간"),
    "last_30d": ("한달", "지난한달", "최근한달", "한달간", "최근한달간", "지난30일"),
    "custom:14": ("보름", "지난보름", "최근보름", "두주", "지난두주", "최근두주"),
    "custom:60": ("두달", "지난두달", "최근두달"),
    "custom:90": ("분기", "최근분기", "석달", "세달", "지난석달", "최근석달", "지난세달", "최근세달"),
    "custom:180": ("반년", "지난반년", "최근반년"),
    "custom:365": ("한해", "최근한해", "일년", "최근일년", "지난일년"),
}.items():
    for _w in _ws:
        _WORDS[_w] = _code

#: 부분 일치 — 말이 섞여 들어온 경우(「지난주 전체 수주」). 긴 것부터 본다(「지난 한 달」이 「한 달」보다 먼저).
_FALLBACK = sorted(((w, c) for w, c in _WORDS.items()
                    if not w.endswith("부터") and w not in ("분기", "당일")), key=lambda x: -len(x[0]))

#: 한 지점 뒤에 붙어도 뜻이 안 바뀌는 말.
_TAIL = re.compile(r"(?:한달|한주|동안|내내|전체|중|간|치|의|에|분|자정|0시|아침|오전|하루)+$")

_NUM_WORD = {"한": 1, "두": 2, "세": 3, "석": 3, "네": 4, "넉": 4, "다섯": 5, "여섯": 6}
_UNIT_DAYS = {"일": 1, "day": 1, "days": 1, "주": 7, "주일": 7, "week": 7, "weeks": 7,
              "개월": 30, "달": 30, "month": 30, "months": 30, "년": 365, "year": 365, "years": 365}
_ROLLING = re.compile(
    r"^(?:최근|지난|과거|last|past)?(\d{1,3}|한|두|세|석|네|넉|다섯|여섯)"
    r"(일|주일|주|개월|달|년|days?|weeks?|months?|years?)(간|동안|치|이내|전부터|전이후|전)?$")


# ── 달력 ────────────────────────────────────────────────────────────────

def _month_end(y: int, m: int) -> date:
    return (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))


def _quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def this_start(code: str, day: date) -> date:
    """「이번 ~」 코드에서 `day` 가 속한 칸의 첫날(주 = 월요일)."""
    if code == "this_week":
        return day - timedelta(days=day.isoweekday() - 1)
    if code == "this_month":
        return day.replace(day=1)
    if code == "this_quarter":
        return _quarter_start(day)
    if code == "ytd":
        return date(day.year, 1, 1)
    raise ValueError(code)


def calendar_window(code: str, today: date) -> tuple[date, date] | None:
    """달력 코드 → (시작, 끝), 오늘 기준. 「이번 ~」은 오늘까지. 달력 코드가 아니면 None."""
    if code in THIS_CODES:
        return this_start(code, today), today
    if code == "prev_week":
        monday = this_start("this_week", today)
        return monday - timedelta(days=7), monday - timedelta(days=1)
    if code == "prev_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if code == "prev_quarter":
        end = _quarter_start(today) - timedelta(days=1)
        return _quarter_start(end), end
    if code == "prev_year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    return None


def rolling_window(code: str, today: date) -> tuple[date, date] | None:
    """굴러가는 코드 → (시작, 끝). 「최근 N일」은 **오늘 포함 N일**이다(종전 카드 보기는 N+1일). 아니면 None."""
    if code == "today":
        return today, today
    if code == "yesterday":
        y = today - timedelta(days=1)
        return y, y
    if code == "since_yesterday":
        return today - timedelta(days=1), today
    if code == "last_7d":
        return today - timedelta(days=6), today
    if code in ("last_30d", "30d"):
        return today - timedelta(days=29), today
    if code.startswith("custom:") and code[7:].isdigit() and int(code[7:]) > 0:
        return today - timedelta(days=int(code[7:]) - 1), today
    return None


# ── 한 지점 ──────────────────────────────────────────────────────────────

def _year2(yy: str) -> int:
    return 2000 + int(yy)


def _latest(make, today: date, year: int | None):
    """연도 없으면 올해 → 시작이 오늘 뒤면 작년. make(year) → (시작, 끝) 또는 None(없는 날짜)."""
    if year is not None:
        return make(year)
    for y in (today.year, today.year - 1):
        span = make(y)
        if span and span[0] <= today:
            return span
    return None


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _day_span(y: int, m: int, d: int):
    dt = _safe(y, m, d)
    return (dt, dt) if dt else None


def _month_span(y: int, m: int):
    return (date(y, m, 1), _month_end(y, m)) if 1 <= m <= 12 else None


def _quarter_span(y: int, q: int):
    return (date(y, 3 * q - 2, 1), _month_end(y, 3 * q)) if 1 <= q <= 4 else None


def _half_span(y: int, h: int):
    return (date(y, 1, 1), date(y, 6, 30)) if h == 1 else (date(y, 7, 1), date(y, 12, 31))


def _year_prefix(t: str, today: date) -> tuple[int | None, str]:
    """「올해 3월」「작년 3분기」의 앞말 → (연도, 나머지)."""
    for words, dy in ((("올해", "금년", "이번해", "올"), 0), (("작년", "지난해", "전년"), -1), (("재작년",), -2)):
        for w in sorted(words, key=len, reverse=True):
            if t.startswith(w) and len(t) > len(w):
                return today.year + dy, t[len(w):]
    return None, t


def point(text: str, today: date) -> tuple[date, date] | None:
    """한 지점(날짜·월·분기·반기·연도·상대 말) → 그 칸의 (시작, 끝). 모르면 None."""
    t = re.sub(r"\s+", "", (text or "").lower())
    if not t:
        return None
    for _ in range(3):  # 뒤에 붙은 군말(「8월 한 달」「9월 1일 자정」)
        s = _TAIL.sub("", t)
        if not s or s == t:
            break
        t = s
    code = _WORDS.get(t)
    if code in CALENDAR_CODES:
        return calendar_window(code, today)
    if code in ROLLING_CODES:
        return rolling_window(code, today)
    if t in ("그저께", "그제", "엊그제", "그끄저께"):
        d = today - timedelta(days=3 if t == "그끄저께" else 2)
        return d, d
    if t == "재작년":
        return date(today.year - 2, 1, 1), date(today.year - 2, 12, 31)
    if t in ("월초",):
        return today.replace(day=1), today.replace(day=1)
    if t in ("연초", "올해초"):
        return date(today.year, 1, 1), date(today.year, 1, 1)
    if t in ("주초",):
        m = this_start("this_week", today)
        return m, m
    year, rest = _year_prefix(t, today)
    t2 = rest
    # 날짜 — 20260901 · 2026-09-01 · 2026.9.1 · 2026/9/1 · 2026년9월1일 · 26.9.1 · 26년9월1일
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", t2)
    if m:
        return _day_span(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{4}|\d{2})(?:[.\-/]|년)(\d{1,2})(?:[.\-/]|월)(\d{1,2})일?", t2)
    if m:
        y = int(m[1]) if len(m[1]) == 4 else _year2(m[1])
        return _day_span(y, int(m[2]), int(m[3]))
    # 월·일 — 9월1일 · 9/1 · 9.1
    m = re.fullmatch(r"(\d{1,2})(?:월|/|\.)(\d{1,2})일?", t2)
    if m:
        return _latest(lambda y: _day_span(y, int(m[1]), int(m[2])), today, year)
    # 연·월 — 2026년8월 · 2026-08 · 2026.08 · 26년8월 · 202608
    m = re.fullmatch(r"(\d{4}|\d{2})(?:년|[.\-/])(\d{1,2})월?", t2) or re.fullmatch(r"(20\d{2})(0[1-9]|1[0-2])", t2)
    if m:
        y = int(m[1]) if len(m[1]) == 4 else _year2(m[1])
        return _month_span(y, int(m[2]))
    # 월 — 8월
    m = re.fullmatch(r"(\d{1,2})월", t2)
    if m:
        return _latest(lambda y: _month_span(y, int(m[1])), today, year)
    # 분기 — 3분기 · 2026년3분기 · 26년3분기 · 3q · q3 · 3q26 · 2026q3
    m = (re.fullmatch(r"(?:(\d{4}|\d{2})년?)?([1-4])분기", t2) or re.fullmatch(r"(\d{4})?q([1-4])", t2)
         or re.fullmatch(r"q([1-4])(\d{4}|\d{2})", t2))
    if m and m.re.pattern.startswith("q("):
        y = int(m[2]) if len(m[2]) == 4 else _year2(m[2])
        return _quarter_span(y, int(m[1]))
    if m:
        y = year if m[1] is None else (int(m[1]) if len(m[1]) == 4 else _year2(m[1]))
        return _latest(lambda yy: _quarter_span(yy, int(m[2])), today, y)
    m = re.fullmatch(r"([1-4])q(\d{4}|\d{2})?", t2)
    if m:
        y = year if m[2] is None else (int(m[2]) if len(m[2]) == 4 else _year2(m[2]))
        return _latest(lambda yy: _quarter_span(yy, int(m[1])), today, y)
    # 반기 — 상반기 · 하반기 · 2026년상반기 · h1 · 2026h2
    m = re.fullmatch(r"(?:(\d{4}|\d{2})년?)?(상|하)반기", t2) or re.fullmatch(r"(?:(\d{4}))?h([12])", t2)
    if m:
        y = year if m[1] is None else (int(m[1]) if len(m[1]) == 4 else _year2(m[1]))
        h = 1 if m[2] in ("상", "1") else 2
        return _latest(lambda yy: _half_span(yy, h), today, y)
    # 연도 — 2025년 · 2025 · 25년(최근 연도로 읽히는 두 자리만)
    m = re.fullmatch(r"(\d{4})년?(?:도|연간|한해)?", t2)
    if m and 2000 <= int(m[1]) <= today.year:
        y = int(m[1])
        return date(y, 1, 1), date(y, 12, 31)
    m = re.fullmatch(r"(\d{2})년(?:도|연간|한해)?", t2)
    if m and 15 <= int(m[1]) <= today.year % 100:
        y = _year2(m[1])
        return date(y, 1, 1), date(y, 12, 31)
    if year is not None and t2 == "":
        return date(year, 1, 1), date(year, 12, 31)
    return None


# ── 전체 ────────────────────────────────────────────────────────────────

_RANGE = re.compile(r"\s*(?:~|∼|〜|–|—|\s-\s|->|부터|에서|\bto\b)\s*")
_SINCE_TAIL = re.compile(r"\s*(?:부터|이후로?|이래|\s*이후)\s*$")
_UNTIL_TAIL = re.compile(r"\s*(?:까지|사이에?|동안|이내)\s*$")


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _rolling_code(t: str) -> str | None:
    """「최근 N일/주/개월/년」「N주 전」 → custom:N일. 두 자리 「NN년」은 연도일 수 있어 여기서 안 받는다."""
    m = _ROLLING.fullmatch(t)
    if not m:
        return None
    n = _NUM_WORD.get(m[1]) or int(m[1])
    unit = m[2]
    if unit == "년" and m[1].isdigit() and len(m[1]) >= 2 and not t.startswith(("최근", "지난", "과거")):
        return None           # 「25년」은 연도다
    days = n * _UNIT_DAYS[unit]
    # 「3일 전(부터)」은 3일 전 그날부터 오늘까지다 — 「최근 3일」보다 하루 길다
    return f"custom:{days + 1 if (m[3] or '').startswith('전') else days}"


def parse(raw: str, today: date) -> tuple[str, str, str]:
    """기간 말 → (code, cs, ce). 모듈 설명 참고."""
    s = re.sub(r"\s+", " ", (raw or "").strip().lower())
    if not s:
        return "since_yesterday", "", ""
    since_prefix = bool(re.match(r"since\s+", s))
    s = re.sub(r"^(?:from|since)\s+", "", s)
    t = s.replace(" ", "")
    alias = _CODE_ALIASES.get(re.sub(r"[\s\-]+", "_", s))
    if alias:
        return alias, "", ""
    if re.fullmatch(r"custom:\d{1,4}", t):
        return t, "", ""
    # 날짜 두 개가 붙은 범위(20260801-20260820 · 2026-08-01~2026-08-20)
    m = re.fullmatch(r"(\d{4}-?\d{2}-?\d{2})\s*[~\-–]\s*(\d{4}-?\d{2}-?\d{2})", s)
    if m:
        a, b = point(m[1].replace("-", ""), today), point(m[2].replace("-", ""), today)
        if a and b:
            return "custom", _ymd(a[0]), _ymd(b[1])
    # 통째로 아는 말
    code = _WORDS.get(t)
    if code:
        return code, "", ""
    # 범위 — 「A ~ B」「A부터 B까지」「A에서 B 사이」
    parts = _RANGE.split(s, maxsplit=1)
    if len(parts) == 2:
        left, right = parts[0].strip(), _UNTIL_TAIL.sub("", parts[1]).strip()
        if left and right:
            got = _range(left, right, today)
            if got:
                return "custom", _ymd(got[0]), _ymd(got[1])
        elif left and not right:
            got = _since(left, today)
            if got:
                return got
    # 「A 이후」「A 이래」「since A」
    if since_prefix:
        got = _since(s, today)
        if got:
            return got
    if _SINCE_TAIL.search(s):
        got = _since(_SINCE_TAIL.sub("", s), today)
        if got:
            return got
    # 굴러가는 창
    rc = _rolling_code(t)
    if rc:
        return rc, "", ""
    # 한 지점(절대·상대)
    span = point(s, today)
    if span:
        code = _WORDS.get(_TAIL.sub("", t) or t)
        if code in CALENDAR_CODES or code in ROLLING_CODES:
            return code, "", ""
        return "custom", _ymd(span[0]), _ymd(span[1])
    # 섞여 들어온 말 — 아는 말이 들어 있으면 그것으로(긴 것 먼저)
    for w, c in _FALLBACK:
        if w in t:
            return c, "", ""
    return s, "", ""


def _since(left: str, today: date):
    """「A부터」 → A 칸의 시작 ~ 오늘. 「어제부터」는 종전 코드(since_yesterday)를 지킨다."""
    lt = left.replace(" ", "")
    if lt in ("어제", "전일"):
        return "since_yesterday", "", ""
    if lt in ("오늘", "금일", "오늘자정", "오늘0시", "오늘아침"):
        return "today", "", ""
    code = _WORDS.get(lt)
    if code in THIS_CODES:
        return code, "", ""
    rc = _rolling_code(lt) or _rolling_code(lt + "전")
    if rc:
        return rc, "", ""
    span = point(left, today)
    if span and span[0] <= today:
        return "custom", _ymd(span[0]), _ymd(today)
    return None


def _range(left: str, right: str, today: date) -> tuple[date, date] | None:
    a = point(left, today)
    b = None
    # 뒤쪽이 「10일」처럼 일만 있으면 앞쪽의 연·월을 물려받는다(「9월 1일부터 10일까지」)
    md = re.fullmatch(r"(\d{1,2})일", right.replace(" ", ""))
    if a and md:
        b0 = _safe(a[0].year, a[0].month, int(md[1]))
        b = (b0, b0) if b0 else None
    if b is None:
        b = point(right, today)
    if not (a and b):
        return None
    start, end = a[0], b[1]
    if start > end and not re.search(r"\d{4}", left):
        # 연도 없는 앞쪽이 뒤쪽보다 늦다 — 앞쪽을 한 해 당긴다(「11월부터 2월까지」)
        start = _safe(start.year - 1, start.month, start.day) or start
    return (start, end) if start <= end else None


def explain_unknown(raw: str) -> str:
    """못 알아들은 기간 말에 붙일 안내 — 왜 못 읽었는지 + 받는 꼴."""
    t = re.sub(r"\s+", "", (raw or "").lower())
    why = ""
    if t.endswith("까지") and not re.search(r"부터|에서|~|∼|〜|–|—", t):
        why = " 시작이 없다 — 「8월 1일부터 8월 10일까지」처럼 시작을 같이 주세요."
    elif re.fullmatch(r"\d{4}[.\-/]?\d{1,2}[.\-/]?\d{1,2}", t):
        why = " 달력에 없는 날짜다."
    return (f"기간 「{raw}」을 읽지 못했다.{why} 받는 꼴: 오늘·어제·어제부터·이번 주·지난주·이번 달·지난달·"
            "이번 분기·지난 분기·올해·작년 · 최근 7일·최근 2주·최근 3개월 · 8월·3분기·상반기·2025년 · "
            "2026-09-01·9월 1일 · 9월 1일부터·8월 1일부터 8월 20일까지.")
