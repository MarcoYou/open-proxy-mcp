"""기간 말 해석 — screener 카드 보기·흐름 보기 공용 (260918 전면 점검). network·DB 0.

지키는 것
① 같은 말은 두 보기에서 같은 뜻 — 말 목록·창 계산이 한 곳(`period_words`)이다.
② 실제 호출·요청에서 나온 말을 다 읽는다: 루틴의 「오늘」「어제 이후」「오늘 자정부터」, 사용자의 「작년」「전년」「3월」
   「4월부터 8월 10일 사이」「상반기」「2주 전」「지난달」「이번 주」, 부르는 모델의 `last_week`·`this_month`·날짜 범위.
③ 달력 말은 오늘 기준 — 이번 주 = 월~오늘, 지난주 = 지난주 월~일, 이번 달 = 1일~오늘, 지난달 = 1일~말일,
   분기·연도도 같다. 굴러가는 창(최근 N일)은 **오늘 포함 N일**.
④ 연도 없는 날짜·월·분기·반기는 오늘 이전의 가장 최근 것, 범위 앞쪽이 늦으면 앞쪽을 한 해 당긴다.
⑤ 못 알아들으면 삼키지 않고 왜 못 읽었는지와 받는 꼴을 밝힌다(추측하지 않는다 — 「8월 10일까지」는 시작이 없다).
⑥ 카드 보기: 3개월 한도로 자르면 흐름 보기를 안내, 오늘 뒤는 오늘까지. 흐름 보기: 원장 범위로 자르고 밝힌다,
   「이번 ~」은 원장이 그 칸에 못 들어왔으면 가장 최근 칸.
"""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from open_proxy_mcp.services import period_words as pw
from open_proxy_mcp.services import disclosure_flow as df
from open_proxy_mcp.services import screener as S

D = dt.date
T = D(2026, 9, 18)          # 금요일
C = "custom"


# ── ② 말 → 코드·날짜 ─────────────────────────────────────────────────
@pytest.mark.parametrize("raw,want", [
    # 루틴·되묻기에서 실제로 온 말
    ("오늘", ("today", "", "")), ("오늘 자정부터", ("today", "", "")), ("어제 이후", ("since_yesterday", "", "")),
    ("2026-08-31~2026-09-01", (C, "20260831", "20260901")), ("20260831~20260901", (C, "20260831", "20260901")),
    # 사용자가 실제로 쓴 꼴
    ("작년", ("prev_year", "", "")), ("전년", ("prev_year", "", "")), ("3월", (C, "20260301", "20260331")),
    ("4월부터 8월 10일 사이", (C, "20260401", "20260810")), ("상반기", (C, "20260101", "20260630")),
    ("2주 전", ("custom:15", "", "")), ("지난달", ("prev_month", "", "")), ("이번 주", ("this_week", "", "")),
    # 부르는 모델이 흉내 내는 코드
    ("last_week", ("prev_week", "", "")), ("this_month", ("this_month", "", "")), ("last month", ("prev_month", "", "")),
    ("this_year", ("ytd", "", "")), ("since_yesterday", ("since_yesterday", "", "")), ("last_7d", ("last_7d", "", "")),
    ("30d", ("last_30d", "", "")), ("custom:45", ("custom:45", "", "")), ("past week", ("last_7d", "", "")),
    # 달력 말
    ("이번주", ("this_week", "", "")), ("금주", ("this_week", "", "")), ("지난 주", ("prev_week", "", "")),
    ("저번 주", ("prev_week", "", "")), ("이번 달", ("this_month", "", "")), ("월초부터", ("this_month", "", "")),
    ("전월", ("prev_month", "", "")), ("이번 분기", ("this_quarter", "", "")), ("지난 분기", ("prev_quarter", "", "")),
    ("전분기", ("prev_quarter", "", "")), ("올해", ("ytd", "", "")), ("연초부터", ("ytd", "", "")),
    ("지난해", ("prev_year", "", "")), ("재작년", (C, "20240101", "20241231")),
    # 굴러가는 창
    ("최근 7일", ("custom:7", "", "")), ("최근 2주", ("custom:14", "", "")), ("최근 4주", ("custom:28", "", "")),
    ("최근 3개월", ("custom:90", "", "")), ("최근 6개월", ("custom:180", "", "")), ("최근 1년", ("custom:365", "", "")),
    ("3일 전부터", ("custom:4", "", "")), ("두 달", ("custom:60", "", "")), ("지난 한 달", ("last_30d", "", "")),
    ("일주일", ("last_7d", "", "")), ("보름", ("custom:14", "", "")), ("분기", ("custom:90", "", "")),
    # 절대 — 월·분기·반기·연도
    ("8월", (C, "20260801", "20260831")), ("12월", (C, "20251201", "20251231")), ("8월 한 달", (C, "20260801", "20260831")),
    ("2026년 8월", (C, "20260801", "20260831")), ("2026-08", (C, "20260801", "20260831")), ("26년 8월", (C, "20260801", "20260831")),
    ("3분기", (C, "20260701", "20260930")), ("4분기", (C, "20251001", "20251231")), ("2026년 2분기", (C, "20260401", "20260630")),
    ("2q26", (C, "20260401", "20260630")), ("q2 2026", (C, "20260401", "20260630")), ("작년 하반기", (C, "20250701", "20251231")),
    ("2025년", (C, "20250101", "20251231")), ("25년", (C, "20250101", "20251231")), ("작년 8월", (C, "20250801", "20250831")),
    # 절대 — 날짜 꼴
    ("2026.09.01", (C, "20260901", "20260901")), ("2026/9/1", (C, "20260901", "20260901")),
    ("2026년 9월 1일", (C, "20260901", "20260901")), ("9월 1일", (C, "20260901", "20260901")), ("9/1", (C, "20260901", "20260901")),
    # 범위·부터
    ("8/1~8/20", (C, "20260801", "20260820")), ("8월 1일부터 20일까지", (C, "20260801", "20260820")),
    ("2026.08.01~2026.08.20", (C, "20260801", "20260820")), ("11월부터 2월까지", (C, "20251101", "20260228")),
    ("1분기부터 2분기까지", (C, "20260101", "20260630")), ("지난주부터 이번 주까지", (C, "20260907", "20260918")),
    ("어제부터 지금까지", (C, "20260917", "20260918")), ("9월 1일부터", (C, "20260901", "20260918")),
    ("9/1 이후", (C, "20260901", "20260918")), ("지난주부터", (C, "20260907", "20260918")),
    ("since 2026-09-01", (C, "20260901", "20260918")), ("from 2026-09-01 to 2026-09-10", (C, "20260901", "20260910")),
    ("이번 달부터", ("this_month", "", "")), ("그저께", (C, "20260916", "20260916")),
    # 섞여 들어온 말
    ("지난주 전체", ("prev_week", "", "")), ("지난달 한 달", ("prev_month", "", "")), ("이번 주 동안", ("this_week", "", "")),
    # 못 알아들음 — 원문 그대로
    ("헛소리", ("헛소리", "", "")), ("8월 10일까지", ("8월 10일까지", "", "")), ("2026-02-31", ("2026-02-31", "", "")),
])
def test_words_to_codes_and_dates(raw, want):
    assert pw.parse(raw, T) == want


def test_empty_is_the_card_view_default():
    assert pw.parse("", T) == ("since_yesterday", "", "") and pw.parse("   ", T) == ("since_yesterday", "", "")


# ── ③ 달력 창 — 요일·월초·해 넘김·윤년·분기 경계 ───────────────────────
@pytest.mark.parametrize("code,today,start,end", [
    ("this_week", D(2026, 9, 14), D(2026, 9, 14), D(2026, 9, 14)),          # 월요일 = 오늘 하루
    ("this_week", D(2026, 9, 20), D(2026, 9, 14), D(2026, 9, 20)),          # 일요일
    ("prev_week", D(2026, 9, 20), D(2026, 9, 7), D(2026, 9, 13)),
    ("prev_week", D(2026, 1, 2), D(2025, 12, 22), D(2025, 12, 28)),
    ("this_month", D(2026, 9, 1), D(2026, 9, 1), D(2026, 9, 1)),
    ("prev_month", D(2026, 1, 15), D(2025, 12, 1), D(2025, 12, 31)),
    ("prev_month", D(2028, 3, 1), D(2028, 2, 1), D(2028, 2, 29)),             # 윤년
    ("this_quarter", D(2026, 4, 1), D(2026, 4, 1), D(2026, 4, 1)),
    ("prev_quarter", D(2026, 9, 18), D(2026, 4, 1), D(2026, 6, 30)),
    ("prev_quarter", D(2026, 1, 15), D(2025, 10, 1), D(2025, 12, 31)),
    ("ytd", D(2027, 1, 1), D(2027, 1, 1), D(2027, 1, 1)),
    ("prev_year", D(2026, 9, 18), D(2025, 1, 1), D(2025, 12, 31)),
])
def test_calendar_windows(code, today, start, end):
    assert pw.calendar_window(code, today) == (start, end)


@pytest.mark.parametrize("code,start", [
    ("today", D(2026, 9, 18)), ("yesterday", D(2026, 9, 17)), ("since_yesterday", D(2026, 9, 17)),
    ("last_7d", D(2026, 9, 12)), ("last_30d", D(2026, 8, 20)), ("custom:14", D(2026, 9, 5)), ("custom:1", D(2026, 9, 18)),
])
def test_rolling_is_n_days_including_today(code, start):
    s, e = pw.rolling_window(code, T)
    assert s == start and e == (D(2026, 9, 17) if code == "yesterday" else T)


# ── ④ 연도 추정 ──────────────────────────────────────────────────────
def test_year_inference_most_recent_past():
    assert pw.parse("12월", D(2026, 12, 15))[1:] == ("20261201", "20261231")      # 이번 달이면 올해
    assert pw.parse("1월", D(2026, 1, 5))[1:] == ("20260101", "20260131")
    assert pw.parse("4분기", D(2026, 10, 2))[1:] == ("20261001", "20261231")      # 이번 분기면 올해
    assert pw.parse("하반기", D(2026, 5, 10))[1:] == ("20250701", "20251231")     # 아직 안 왔으면 작년
    assert pw.parse("10월부터 12월까지", T)[1:] == ("20251001", "20251231")


# ── ⑤ 못 알아들음 안내 ────────────────────────────────────────────────
def test_unknown_explains_why_and_what_is_accepted():
    msg = pw.explain_unknown("8월 10일까지")
    assert "시작이 없다" in msg and "8월 1일부터 8월 10일까지" in msg and "받는 꼴" in msg
    assert "달력에 없는 날짜" in pw.explain_unknown("2026-02-31")
    assert "받는 꼴" in pw.explain_unknown("요즘")


# ── ⑥ 카드 보기 — 창 계산·3개월 한도·오늘 뒤 ──────────────────────────
def test_card_view_windows(monkeypatch):
    monkeypatch.setattr(S, "_today_kst", lambda: T)
    assert S.resolve_period("this_week")[:2] == ("20260914", "20260918")
    assert S.resolve_period("this_month")[:2] == ("20260901", "20260918")
    assert S.resolve_period("this_quarter")[:2] == ("20260701", "20260918")
    assert S.resolve_period("prev_quarter")[:2] == ("20260401", "20260630")
    assert S.resolve_period("last_7d")[:2] == ("20260912", "20260918")         # 종전 8일(9/11~)
    b, e, notes = S.resolve_period("ytd")
    assert e == "20260918" and b > "20260101" and "3개월까지만" in notes[0] and "흐름 보기" in notes[0]
    code, cs, ce = S._nl_period("9월", "", "", "", "")
    assert S.resolve_period(code, custom_start=cs, custom_end=ce)[:2] == ("20260901", "20260918")   # 오늘 뒤는 오늘까지
    b, e, notes = S.resolve_period(S._nl_period("요즘", "", "", "", "")[0])
    assert (b, e) == ("20260917", "20260918") and "받는 꼴" in notes[0]


def test_card_view_start_date_forms(monkeypatch):
    monkeypatch.setattr(S, "_today_kst", lambda: T)
    assert S._nl_period("", "2026.09.01", "2026/9/10", "", "") == ("custom", "20260901", "20260910")
    assert S._nl_period("지난주", "20260801", "", "", "") == ("custom", "20260801", "20260801")   # 날짜가 이긴다


def test_card_view_through_the_tool(monkeypatch):
    """도구 호출 경로로 — 「이번 주」가 스캔 창·입력 해석까지 간다(DART 는 대역)."""
    from open_proxy_mcp.server import mcp

    seen: list[tuple] = []

    class _Client:
        def api_call_snapshot(self):
            return 0

    async def _scan(client, code, bgn, end, pages):
        seen.append((bgn, end))
        return S._scan_result([], 0, 1, 1)

    monkeypatch.setattr(S, "_today_kst", lambda: T)
    monkeypatch.setattr(S, "_scan_code", _scan)
    monkeypatch.setattr(S, "get_dart_client", lambda: _Client())
    monkeypatch.setattr(S, "_krx_mktcap_map", lambda codes, dd: {})

    async def go(args):
        r = await mcp.call_tool("screener", args)
        return "".join(getattr(c, "text", "") for c in (r if isinstance(r, list) else r.content))

    out = asyncio.run(go({"types": "자사주", "period": "이번 주"}))
    assert seen and set(seen) == {("20260914", "20260918")}
    assert "`20260914~20260918`" in out and "이번 주→`this_week`" in out
    seen.clear()
    out = asyncio.run(go({"types": "자사주", "period": "지난달"}))
    assert set(seen) == {("20260801", "20260831")} and "지난달→`prev_month`" in out


# ── ⑥ 흐름 보기 — 원장 범위·「이번 ~」 ─────────────────────────────────
FIRST, LAST = D(2025, 9, 16), D(2026, 9, 17)


def _fp(raw, today=T, last=LAST):
    return df.resolve_flow_period(raw, "", "", today, FIRST, last)


def test_flow_explicit_since_yesterday_and_today():
    s, e, notes = _fp("어제부터")                     # 종전엔 「알아듣지 못해」 7일로 빠졌다
    assert (s, e) == (D(2026, 9, 17), D(2026, 9, 17)) and not any("읽지 못했다" in n for n in notes)
    s, e, notes = _fp("오늘")
    assert (s, e) == (LAST, LAST) and any("원장 범위 밖" in n for n in notes)


def test_flow_this_period_falls_back_when_ledger_is_behind():
    s, e, notes = _fp("이번 주", today=D(2026, 9, 21), last=D(2026, 9, 19))    # 월요일 아침 밤 배치 전
    assert (s, e) == (D(2026, 9, 14), D(2026, 9, 19)) and "이번 주(2026-09-21 시작)" in notes[0]
    s, e, notes = _fp("이번 분기", today=D(2026, 10, 1), last=D(2026, 9, 30))
    assert (s, e) == (D(2026, 7, 1), D(2026, 9, 30)) and "이번 분기(4분기)" in notes[0]
    s, e, notes = _fp("올해", today=D(2027, 1, 1), last=D(2026, 12, 31))
    assert (s, e) == (D(2026, 1, 1), D(2026, 12, 31)) and "올해(2027년)" in notes[0]


def test_flow_absolute_words_ranges_and_unknown():
    assert _fp("8월")[:2] == (D(2026, 8, 1), D(2026, 8, 31))
    assert _fp("4월부터 8월 10일 사이")[:2] == (D(2026, 4, 1), D(2026, 8, 10))
    s, e, notes = _fp("작년")
    assert (s, e) == (FIRST, D(2025, 12, 31)) and any("시작날짜를 그날로 당겼다" in n for n in notes)
    s, e, notes = _fp("8월 10일까지")
    assert (s, e) == (D(2026, 9, 11), LAST) and "시작이 없다" in notes[0]
    assert _fp("최근 7일")[:2] == (D(2026, 9, 12), LAST)            # 두 보기 모두 오늘 포함 7일
