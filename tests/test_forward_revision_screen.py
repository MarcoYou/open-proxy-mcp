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


def _hist_rows():
    """(stock_code, as_of, period, period_type, rev, op, ni, eps, dps) — fwd_hist 질의 결과 모양."""
    rows = []
    for i, d in enumerate(_weeks(14)):                       # 삼성전자: 영업이익 꾸준히 상향
        rows.append(("005930", d, "2026.12E", "FY", 1000, 100 + 2 * i, 80, 10, 1))
        rows.append(("005930", d, "2027.12E", "FY", 1100, 200, 90, 12, 1))
    for i, d in enumerate(_weeks(14)):                       # SK하이닉스: 하향
        rows.append(("000660", d, "2026.12E", "FY", 900, 300 - 5 * i, 70, 9, None))
    for d in _weeks(2, start=dt.date(2026, 9, 5)):           # 현대차: 이력 2주 → 4w 는 「이력 짧음」
        rows.append(("005380", d, "2026.12E", "FY", 500, 50, 40, 5, 1))
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
                             "comparable": 3, "history_short": 1}
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
    hist = [("005930", d, "2026.12E", "FY", 1, 100 + i, 1, 1, 1) for i, d in enumerate(_weeks(14))]
    hist += [("000660", dt.date(2026, 9, 12), "2026.12E", "FY", 1, 50, 1, 1, 1)]   # 스냅샷 하나뿐
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
    assert "영업이익 4w | 매출 4w | 지배순이익 4w | EPS 4w" in md


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
