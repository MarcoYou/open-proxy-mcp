"""forward_estimates_data(universe=…) — 유니버스 리비전 스크린 (DB·DART 0콜).

지키는 것: ① 유니버스 전 종목의 이력을 **한 질의**로 읽는다 ② 종목마다 가장 가까운 연간 추정
기간 한 행, 영업이익 window 변화율 내림차순, 비교 불가는 맨 뒤 ③ 추정 없는 종목은 표에서
빠지고 개수로 남는다 ④ 이력 짧음·기준일 부재를 행과 경고에 밝힌다 ⑤ DB 미설정/장애/해석
실패를 status 로 가른다 ⑥ window·period_type 검증.
"""
import asyncio
import datetime as dt

from open_proxy_mcp.services import forward_estimates as fe
from open_proxy_mcp.services import universe as uni
from open_proxy_mcp.tools import forward_estimates_data as tool

_UNI = [
    {"rank": 1, "ticker": "005930", "name": "삼성전자", "market": "KOSPI", "mktcap_krw": 5 * 10**14, "close_krw": 1, "list_shrs": 1},
    {"rank": 2, "ticker": "000660", "name": "SK하이닉스", "market": "KOSPI", "mktcap_krw": 3 * 10**14, "close_krw": 1, "list_shrs": 1},
    {"rank": 3, "ticker": "005380", "name": "현대자동차", "market": "KOSPI", "mktcap_krw": 6 * 10**13, "close_krw": 1, "list_shrs": 1},
    {"rank": 4, "ticker": "999999", "name": "미커버", "market": "KOSPI", "mktcap_krw": 10**12, "close_krw": 1, "list_shrs": 1},
]


_real_list_universe = uni.list_universe


def _weeks(n, start=dt.date(2026, 6, 6)):
    return [start + dt.timedelta(weeks=i) for i in range(n)]


# 금액은 **원(KRW)** 이다. 260921 분모 가드(기준 영업이익 100억원 미만은 순위 밖)가 생기면서
# 「영업이익 100원」짜리 옛 표본은 전부 순위 밖으로 떨어졌다 — 표본을 실제 자릿수로 올린다.
_E = 10**9          # 곱하면 10억원 단위 (op 100 → 1,000억원)


def _hist_rows():
    """(stock_code, as_of, period, period_type, rev, op, ni, eps, dps) — fwd_hist 질의 결과 모양."""
    rows = []
    for i, d in enumerate(_weeks(14)):                       # 삼성전자: 영업이익 꾸준히 상향
        rows.append(("005930", d, "2026.12E", "FY", 1000 * _E, (100 + 2 * i) * _E, 80 * _E, 10, 1))
        rows.append(("005930", d, "2027.12E", "FY", 1100 * _E, 200 * _E, 90 * _E, 12, 1))
    for i, d in enumerate(_weeks(14)):                       # SK하이닉스: 하향
        rows.append(("000660", d, "2026.12E", "FY", 900 * _E, (300 - 5 * i) * _E, 70 * _E, 9, None))
    for d in _weeks(2, start=dt.date(2026, 9, 5)):           # 현대차: 이력 2주 → 4w 는 「이력 짧음」
        rows.append(("005380", d, "2026.12E", "FY", 500 * _E, 50 * _E, 40 * _E, 5, 1))
    return sorted(rows, key=lambda r: (r[0], r[1]))


def _stub(monkeypatch, uni_rows=_UNI, hist=None, resolved=True, db_ok=True):
    async def lu(raw):
        return uni.UniverseList(spec="kospi:4", label="KOSPI 시총상위 4", resolved=resolved,
                                notice="" if resolved else "krx_weekly 조회 실패 → 전체시장으로 대체.",
                                as_of="20260911", rows=list(uni_rows), db_ok=db_ok)
    monkeypatch.setattr(uni, "list_universe", lu)
    monkeypatch.setattr(fe, "_hist_available", lambda: True)
    calls = []

    def pg(sql, params=()):
        calls.append((sql, params))
        return _hist_rows() if hist is None else hist
    monkeypatch.setattr(fe, "pg_rows", pg)
    return calls


def test_one_query_ranked_by_op_revision(monkeypatch):
    calls = _stub(monkeypatch)
    p = asyncio.run(fe.build_revision_screen_payload("코스피 시총 상위 4"))
    assert p["status"] == "ok"
    assert len(calls) == 1 and "= ANY" in calls[0][0] and "period_type=%s" in calls[0][0]
    assert calls[0][1] == ("FY", ["005930", "000660", "005380", "999999"])
    d = p["data"]
    assert d["window"] == "4w" and d["window_days"] == 28
    assert [r["ticker"] for r in d["rows"]] == ["005930", "005380", "000660"]   # 상향 → 유지(짧음) → 하향
    top = d["rows"][0]
    assert top["rank"] == 1 and top["name"] == "삼성전자" and top["period"] == "2026.12E"
    assert top["op_krw_4w_pct"] > 0 and top["history_short"] is False
    assert top["baseline_as_of"] is not None and top["rank_mktcap"] == 1
    assert d["rows"][1]["history_short"] is True                 # 현대차 — 가장 오래된 스냅샷과 비교
    assert d["rows"][2]["op_krw_4w_pct"] < 0
    assert d["coverage"] == {"universe": 4, "with_estimates": 3, "no_estimates": 1,
                             "comparable": 3, "ranked": 3, "rank_guarded": 0, "history_short": 1}
    assert d["direction"]["up"] == 1 and d["direction"]["down"] == 1
    assert any("1종목은 컨센서스 추정이 없어" in w for w in p["warnings"])
    assert any("이력 짧음" in w for w in p["warnings"])


def test_1w_window(monkeypatch):
    _stub(monkeypatch)
    p = asyncio.run(fe.build_revision_screen_payload("코스피 시총 상위 4", window="1w"))
    assert p["status"] == "ok" and p["data"]["window"] == "1w" and p["data"]["window_days"] == 7
    top = p["data"]["rows"][0]
    assert top["ticker"] == "005930" and top["baseline_days"] == 7 and top["history_short"] is False
    assert top["op_krw_1w_pct"] > 0


def test_12w_window_and_period_type_all(monkeypatch):
    calls = _stub(monkeypatch)
    p = asyncio.run(fe.build_revision_screen_payload("코스피 시총 상위 4", window="12w", period_type="all"))
    assert p["status"] == "ok" and p["data"]["window"] == "12w"
    assert "period_type=%s" not in calls[0][0]
    assert "op_krw_12w_pct" in p["data"]["rows"][0]


def test_no_baseline_goes_last_and_is_counted(monkeypatch):
    hist = [("005930", d, "2026.12E", "FY", _E, (100 + i) * _E, _E, 1, 1) for i, d in enumerate(_weeks(14))]
    hist += [("000660", dt.date(2026, 9, 12), "2026.12E", "FY", _E, 50 * _E, _E, 1, 1)]   # 스냅샷 하나뿐
    _stub(monkeypatch, hist=hist)
    p = asyncio.run(fe.build_revision_screen_payload("코스피 시총 상위 4"))
    rows = p["data"]["rows"]
    assert rows[-1]["ticker"] == "000660" and rows[-1]["baseline_as_of"] is None
    assert p["data"]["direction"]["not_comparable"] == 1
    assert p["data"]["coverage"]["comparable"] == 1                     # direction 과 같은 정의
    assert any("비교 불가" in w for w in p["warnings"])


def test_invalid_window_and_period(monkeypatch):
    _stub(monkeypatch)
    monkeypatch.setattr(uni, "list_universe", _real_list_universe)
    p = asyncio.run(fe.build_revision_screen_payload("코스닥 100"))
    assert p["status"] == "invalid" and "추측하지 않았습니다" in p["warnings"][0]
    _stub(monkeypatch)
    assert asyncio.run(fe.build_revision_screen_payload("코스피 상위 4", window="1y"))["status"] == "invalid"
    assert asyncio.run(fe.build_revision_screen_payload("코스피 상위 4", period_type="H"))["status"] == "invalid"


def test_db_and_resolution_failures_are_distinct(monkeypatch):
    _stub(monkeypatch, db_ok=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert asyncio.run(fe.build_revision_screen_payload("코스피 상위 4"))["status"] == "db_unconfigured"
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    assert asyncio.run(fe.build_revision_screen_payload("코스피 상위 4"))["status"] == "db_error"
    _stub(monkeypatch, resolved=False)
    p = asyncio.run(fe.build_revision_screen_payload("코스피 상위 4"))
    assert p["status"] == "no_data" and "전체시장" in p["warnings"][0]
    _stub(monkeypatch)
    monkeypatch.setattr(fe, "pg_rows", lambda *a, **k: None)
    assert asyncio.run(fe.build_revision_screen_payload("코스피 상위 4"))["status"] == "db_error"


def test_universe_without_estimates_is_no_estimates(monkeypatch):
    _stub(monkeypatch, hist=[])
    p = asyncio.run(fe.build_revision_screen_payload("코스피 상위 4"))
    assert p["status"] == "no_estimates" and p["data"]["rows"] == []


def test_md_render(monkeypatch):
    _stub(monkeypatch)
    p = asyncio.run(fe.build_revision_screen_payload("코스피 시총 상위 4"))
    md = tool._render_revision_screen(p)
    assert md.startswith("## KOSPI 시총상위 4 — 컨센서스 리비전 — 영업이익 추정 4w 변화")
    assert "| 1 | 삼성전자 | `005930` | KOSPI | 2026.12E |" in md
    assert "(이력 짧음)" in md and "상향 1 / 하향 1" in md
    assert "영업이익 4w | 이동 | 매출 4w | 지배순이익 4w | EPS 4w" in md


def test_universe_phrase_in_company_slot_routes_to_screen(monkeypatch):
    """옛 도구 정의를 가진 호출자가 문장을 company 에 넣어도 스크린으로 답한다 (260916 실측)."""
    _stub(monkeypatch)
    p = asyncio.run(fe.build_forward_estimates_payload(
        company="코스피 시총 상위 100개 종목의 영업이익 컨센서스가 1주 전 대비 얼마나 바뀌었는지 전체를 표로 달라",
        bundle="revision"))
    assert p["status"] == "ok" and p["data"]["scope"] == "revision_screen" and p["data"]["window"] == "1w"
    assert "유니버스 「코스피 시총 상위 100」" in p["warnings"][0] and "universe 인자" in p["warnings"][0]


def test_plain_company_name_is_not_a_phrase():
    from open_proxy_mcp.services.universe import universe_phrase
    assert universe_phrase("삼성전자") is None and universe_phrase("005930") is None
    assert universe_phrase("코스닥 상위 100") == ("코스닥 시총 상위 100", None)
    assert universe_phrase("시총 상위 50 종목 4주 전 대비") == ("시총 상위 50", "4w")
    assert universe_phrase("한 달 전 대비 코스닥 상위 20") == ("코스닥 시총 상위 20", "4w")


def test_tool_renders_screen_payload_from_company_phrase(monkeypatch):
    """도구 렌더러까지 통과해야 한다 — 서비스만 찍은 테스트가 live 의 `ruler` KeyError 를 놓쳤다."""
    _stub(monkeypatch)
    p = asyncio.run(fe.build_forward_estimates_payload(company="코스피 시총 상위 4 종목 1주 전 대비", bundle="revision"))
    md = tool.render_payload(p, "md")
    assert md.startswith("## KOSPI 시총상위 4 — 컨센서스 리비전")
    assert "| 1 | 삼성전자 |" in md
    assert tool.render_payload(p, "json").startswith("{")


# ── 260921 회귀 ─────────────────────────────────────────────────────────────
# 주간 리비전 자동화가 「하향 Top 5」를 하나도 못 뽑았고(내림차순 표를 앞에서 300행으로 잘라
# 하향 51종목이 전부 컷 뒤에 있었다), 분모를 |기준값| 로 나누는 바람에 순위 꼬리가 적자·소액
# 종목으로 덮였다. 둘 다 여기서 막는다.

def _big_universe(n):
    return [{"rank": i, "ticker": f"{i:06d}", "name": f"종목{i}", "market": "KOSPI",
             "mktcap_krw": (n - i) * 10**11, "close_krw": 1, "list_shrs": 1} for i in range(1, n + 1)]


def _monotone_hist(n):
    """종목1 이 가장 크게 상향, 종목n 이 가장 크게 하향 — 내림차순 표의 양 끝이 된다.
    기준 영업이익은 전부 분모 가드를 넉넉히 넘긴다(접기만 시험하려는 표본이다)."""
    rows = []
    for i in range(1, n + 1):
        step = (n // 2) - i                       # i 가 클수록 음수
        for k, d in enumerate(_weeks(14)):
            rows.append((f"{i:06d}", d, "2026.12E", "FY", 1000 * _E, (10000 + step * k) * _E,
                         80 * _E, 10, 1))
    return sorted(rows, key=lambda r: (r[0], r[1]))


def test_md_keeps_both_ends_when_over_cap(monkeypatch):
    """해법① — 내림차순 표를 앞에서만 자르면 하향이 통째로 사라진다. 양 끝을 남겨야 한다."""
    n = 400
    _stub(monkeypatch, uni_rows=_big_universe(n), hist=_monotone_hist(n))
    p = asyncio.run(fe.build_revision_screen_payload("코스피 시총 상위 400"))
    rows = p["data"]["rows"]
    assert len(rows) == n > tool._SCREEN_MD_MAX
    assert p["data"]["coverage"]["ranked"] == n                 # 표본은 전부 순위 안
    worst = rows[-1]
    assert worst["op_krw_4w_pct"] < 0                           # 꼬리는 진짜 하향이다
    md = tool._render_revision_screen(p)
    assert f"| 1 | {rows[0]['name']} |" in md                   # 머리
    assert f"| {worst['rank']} | {worst['name']} |" in md       # 꼬리 — 옛 코드는 여기서 죽었다
    assert "종목 접음" in md and "위 150 · 아래 150" in md
    assert md.count("\n| ") >= tool._SCREEN_MD_MAX


def test_md_keeps_every_row_under_cap(monkeypatch):
    """상한 아래면 접지 않는다 — 접기 표시도, json 안내도 나오지 않아야 한다."""
    _stub(monkeypatch)
    md = tool._render_revision_screen(asyncio.run(fe.build_revision_screen_payload("코스피 시총 상위 4")))
    assert "접음" not in md and "접었다" not in md
    assert "| 1 | 삼성전자 |" in md and "| 3 | SK하이닉스 |" in md


# ── 분모 가드 ───────────────────────────────────────────────────────────────

_GUARD_UNI = [
    {"rank": 1, "ticker": "000880", "name": "대형주", "market": "KOSPI", "mktcap_krw": 10**14, "close_krw": 1, "list_shrs": 1},
    {"rank": 2, "ticker": "126340", "name": "소액흑자", "market": "KOSDAQ", "mktcap_krw": 10**12, "close_krw": 1, "list_shrs": 1},
    {"rank": 3, "ticker": "282880", "name": "적자심화", "market": "KOSDAQ", "mktcap_krw": 10**11, "close_krw": 1, "list_shrs": 1},
]


def _guard_hist():
    """260921 실측 모양. 이동 절대액은 대형주가 소액흑자의 880배인데 %는 소액흑자가 더 크다.

    대형주   7,422억 → 6,101억  (−17.8%,  −1,321억)
    소액흑자     62억 →    47억  (−24.2%,     −15억)   ← 기준 100억 미만
    적자심화     −5억 →   −58억  (−1,050%,   −53억)   ← 기준이 적자
    """
    rows = []
    for d in _weeks(14):
        rows.append(("000880", d, "2026.12E", "FY", 30000 * _E, 742_2 * 10**8, 500 * _E, 10, 1))
        rows.append(("126340", d, "2026.12E", "FY", 300 * _E, 62 * 10**8, 5 * _E, 3, 1))
        rows.append(("282880", d, "2026.12E", "FY", 100 * _E, -5 * 10**8, -5 * _E, -1, None))
    last = _weeks(14)[-1]
    rows = [r for r in rows if r[1] != last]
    rows += [("000880", last, "2026.12E", "FY", 30000 * _E, 610_1 * 10**8, 500 * _E, 10, 1),
             ("126340", last, "2026.12E", "FY", 300 * _E, 47 * 10**8, 5 * _E, 3, 1),
             ("282880", last, "2026.12E", "FY", 100 * _E, -58 * 10**8, -5 * _E, -1, None)]
    return sorted(rows, key=lambda r: (r[0], r[1]))


def test_rank_guard_keeps_small_and_loss_out_of_the_ranking(monkeypatch):
    """해법② — 적자·소액 기준은 등수를 못 받는다. 순위 꼬리는 **진짜 큰 하향**이어야 한다."""
    _stub(monkeypatch, uni_rows=_GUARD_UNI, hist=_guard_hist())
    p = asyncio.run(fe.build_revision_screen_payload("코스피 시총 상위 3", window="1w"))
    by = {r["ticker"]: r for r in p["data"]["rows"]}
    assert by["000880"]["rank_basis"] == "ranked" and by["000880"]["rank"] == 1
    assert by["126340"]["rank_basis"] == "base_small" and by["126340"]["rank"] is None
    assert by["282880"]["rank_basis"] == "base_loss" and by["282880"]["rank"] is None
    # 가장 크게 하향한 순위 종목 = 대형주. %만 보면 3등이지만 순위에 남는 건 이것뿐이다.
    ranked = [r for r in p["data"]["rows"] if r["rank_basis"] == "ranked"]
    assert [r["ticker"] for r in ranked] == ["000880"]
    assert p["data"]["coverage"]["ranked"] == 1 and p["data"]["coverage"]["rank_guarded"] == 2
    assert p["data"]["rank_guarded"] == ["126340", "282880"]
    # 순위 밖이어도 **지우지 않는다** — 행도 %도 그대로 있다.
    assert by["282880"]["op_krw_1w_pct"] < -1000
    assert by["126340"] in p["data"]["rows"]
    # 방향 집계는 가드와 무관하게 비교 가능한 행 전부를 센다 (기존 뜻 유지)
    assert p["data"]["direction"]["down"] == 3


def test_guard_rows_render_with_reason_not_a_rank(monkeypatch):
    _stub(monkeypatch, uni_rows=_GUARD_UNI, hist=_guard_hist())
    md = tool._render_revision_screen(asyncio.run(
        fe.build_revision_screen_payload("코스피 시총 상위 3", window="1w")))
    assert "순위 밖 — 분모 가드 2종목" in md
    # 표본 3종목 모두 「내내 보합 뒤 한 번」 모양이라 † 도 같이 붙는다 — 두 표시는 서로 독립이다.
    assert "| 적자기준 | 적자심화" in md and "| 소액기준 | 소액흑자" in md
    assert "| 1 | 대형주" in md
    assert "임의 기준" in md                                        # 임의 임계값임을 숨기지 않는다
    assert "지운 게 아니라" in md


def test_absolute_move_rides_along_so_size_is_not_buried(monkeypatch):
    """변화율만 실으면 1,321억원 이동이 15억원 이동 아래로 간다 — 이동 절대액을 같이 싣는다."""
    _stub(monkeypatch, uni_rows=_GUARD_UNI, hist=_guard_hist())
    p = asyncio.run(fe.build_revision_screen_payload("코스피 시총 상위 3", window="1w"))
    by = {r["ticker"]: r for r in p["data"]["rows"]}
    assert by["000880"]["op_krw_base"] == 742_2 * 10**8
    assert by["000880"]["op_krw_1w_delta"] == -1321 * 10**8
    assert by["126340"]["op_krw_1w_delta"] == -15 * 10**8
    assert abs(by["000880"]["op_krw_1w_delta"]) > abs(by["126340"]["op_krw_1w_delta"]) * 80
    assert "이동" in tool._render_revision_screen(p)


# ── 단발 갱신 표시 ──────────────────────────────────────────────────────────

def _sole_shape(days, flat=7422 * 10**8, moved=6101 * 10**8, code="005930"):
    """내내 같은 값이다가 마지막 스냅샷에서 한 번 크게 움직이는 모양."""
    return [(code, d, "2026.12E", "FY", 30000 * _E, flat if i < len(days) - 1 else moved,
             500 * _E, 10, 1) for i, d in enumerate(days)]


def test_sole_update_is_flagged_and_claims_no_cause(monkeypatch):
    """값이 몇 주 내내 같다가 한 번만 움직인 행을 표시한다 — **원인은 말하지 않는다.**
    창(1w·4w·12w) 변화율로는 못 본다: 스냅샷이 적으면 세 창이 같은 기준일로 접힌다."""
    days = _weeks(14)
    hist = _sole_shape(days)
    hist += [("000660", d, "2026.12E", "FY", 900 * _E, (300 - 5 * i) * _E, 70 * _E, 9, 1)
             for i, d in enumerate(days)]                      # 매주 조금씩 — 평범한 점진 하향
    _stub(monkeypatch, hist=sorted(hist, key=lambda r: (r[0], r[1])))
    p = asyncio.run(fe.build_revision_screen_payload("코스피 시총 상위 4", window="1w"))
    by = {r["ticker"]: r for r in p["data"]["rows"]}
    assert by["005930"]["sole_update"]["as_of"] == days[-1].isoformat()
    assert by["005930"]["sole_update"]["op_krw_pct"] < -17
    assert by["000660"]["sole_update"] is None                  # 매주 움직인 종목은 아니다
    assert p["data"]["sole_update"] == ["005930"]
    assert by["005930"] in p["data"]["rows"]                    # 지우지 않는다 — 표시만
    w = " ".join(p["warnings"])
    assert "단발 갱신" in w and "005930" in w
    for banned in ("분할", "연결범위", "리베이싱", "의심"):       # 원인을 단정하지 않는다
        assert banned not in w
    md = tool._render_revision_screen(p)
    assert "삼성전자†" in md and "† = 관측 구간 내내" in md


def test_sole_update_is_sign_neutral(monkeypatch):
    """상향도 걸린다 — 260921 실측 51종목 중 21종목이 상향이었다. 부호로 거르지 않는다."""
    _stub(monkeypatch, hist=_sole_shape(_weeks(14), flat=6101 * 10**8, moved=7422 * 10**8))
    p = asyncio.run(fe.build_revision_screen_payload("코스피 시총 상위 4", window="1w"))
    assert p["data"]["rows"][0]["sole_update"]["op_krw_pct"] > 17


def test_sole_update_survives_few_snapshots(monkeypatch):
    """실데이터 회귀: 스냅샷이 4개뿐이면 4w·12w 가 같은 기준일(partial)로 접힌다.
    그래도 잡혀야 한다 — 창 비교에 기대면 여기서 전부 놓친다."""
    _stub(monkeypatch, hist=_sole_shape(_weeks(4, start=dt.date(2026, 8, 29))))
    p = asyncio.run(fe.build_revision_screen_payload("코스피 시총 상위 4"))
    r = p["data"]["rows"][0]
    assert r["ticker"] == "005930" and r["history_short"] is True    # 4w 는 partial 이다
    assert r["sole_update"] is not None


def test_update_before_the_window_is_not_flagged(monkeypatch):
    """갱신이 기준일보다 앞이면 지금 보이는 %의 원인이 아니다 — 표시하지 않는다."""
    days = _weeks(14)
    hist = [("005930", d, "2026.12E", "FY", 30000 * _E,
             7422 * 10**8 if i < 3 else 6101 * 10**8, 500 * _E, 10, 1)
            for i, d in enumerate(days)]                        # 갱신은 12주 전, 1w 창 밖
    _stub(monkeypatch, hist=sorted(hist, key=lambda r: (r[0], r[1])))
    p = asyncio.run(fe.build_revision_screen_payload("코스피 시총 상위 4", window="1w"))
    assert p["data"]["sole_update"] == []


def test_flat_and_short_history_are_not_sole_updates(monkeypatch):
    """내내 보합인 종목과 스냅샷 2개짜리는 표시 대상이 아니다."""
    hist = [("005930", d, "2026.12E", "FY", 30000 * _E, 7422 * 10**8, 500 * _E, 10, 1)
            for d in _weeks(14)]                                             # 내내 보합
    hist += [("005380", d, "2026.12E", "FY", 500 * _E, (50 if i == 0 else 20) * _E, 40 * _E, 5, 1)
             for i, d in enumerate(_weeks(2, start=dt.date(2026, 9, 5)))]    # 이력 2주
    _stub(monkeypatch, hist=sorted(hist, key=lambda r: (r[0], r[1])))
    p = asyncio.run(fe.build_revision_screen_payload("코스피 시총 상위 4"))
    assert p["data"]["sole_update"] == []
