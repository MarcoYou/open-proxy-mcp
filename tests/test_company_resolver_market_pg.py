"""이름 해석의 시장 맥락(`company_resolver._database_market_context`)을 진짜 Postgres 17 에서 돌린다.

261018: 최신일 전 종목 단면을 `krx_weekly` 이번 주 행에서 일별 시세 저장분(`krx_daily` 최신일 + `krx_daily_v`)으로 옮겼다.
- 다음 단계 세계 — `krx_weekly` 에 닫힌 지난주 행만 있어도 최신 거래일 단면·기준일·출처 라벨을 낸다.
- 지금 세계 — `krx_weekly` 에도 이번 주 행이 있으면 옛 질의와 값이 같다.
- 창 안에서 폐지된 종목(`price_latest` 에는 옛 날짜 행이 남는다)이 명부에 섞이지 않는다.

로컬 Docker 의 `postgres:17` 이 필요하다(`tests/conftest.py` 의 `pg17`). Docker 가 없으면 건너뛴다. 네트워크 0.
"""
from __future__ import annotations

from datetime import date

import pytest

psycopg = pytest.importorskip("psycopg")

from test_krx_daily_pg import KRX_WEEKLY_DDL, _apply, _row, put_daily  # noqa: E402 — 스키마·KRX 행 모양은 그 시험과 같은 것을 쓴다

from open_proxy_mcp import company_resolver as CR  # noqa: E402
from open_proxy_mcp.company_resolver import CompanyResolver  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as pmd  # noqa: E402

LAST_WEEK = ("20261005", "20261006", "20261007", "20261008", "20261009")
THIS_WEEK = ("20261012", "20261013", "20261014", "20261015", "20261016")
SHRS = {"005930": 5_919_637_922, "000660": 728_002_365, "247540": 97_000_000, "900100": 10_000_000, "900200": 20_000_000}
DELISTED = "900100"           # KQ — 20261013 까지만 거래(보관 창 안에서 상장폐지)
NEW_LISTING = "900200"        # KS — 20261015 신규상장(krx_weekly 에는 닫힌 주 행이 없다)


def _close(ticker: str, i: int) -> int:
    return {"005930": 70_000, "000660": 180_000, "247540": 250_000, "900100": 3_000, "900200": 15_000}[ticker] + i * 100


def _day_rows(i: int, dd: str) -> tuple[list, list]:
    kospi = ["005930", "000660"] + ([NEW_LISTING] if dd >= "20261015" else [])
    kosdaq = ["247540"] + ([DELISTED] if dd <= "20261013" else [])
    return ([_row(t, _close(t, i), shrs=SHRS[t]) for t in kospi], [_row(t, _close(t, i), shrs=SHRS[t]) for t in kosdaq])


def _expected_caps(dd: str) -> dict[str, int]:
    i = (LAST_WEEK + THIS_WEEK).index(dd)
    kospi, kosdaq = _day_rows(i, dd)
    return {r["ISU_CD"]: _close(r["ISU_CD"], i) * SHRS[r["ISU_CD"]] for r in kospi + kosdaq}


@pytest.fixture
def two_weeks(pg17, monkeypatch):
    """서버의 실제 쓰기(`_krx_db_upsert`)로 두 주(10거래일)를 쓴다 → krx_weekly = 닫힌 두 주의 금요일(이번 주도 금요일까지 써서 닫혔다),
    일별 저장분 = 열흘 전부(보관 창은 약 1년)."""
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
    _apply(pg17)
    monkeypatch.setenv("DATABASE_URL", pg17)
    for i, dd in enumerate(LAST_WEEK + THIS_WEEK):
        assert pmd._krx_db_upsert(dd, *_day_rows(i, dd)).daily_ok
    with psycopg.connect(pg17) as c:
        assert [r[0] for r in c.execute("SELECT DISTINCT price_dd FROM krx_weekly ORDER BY 1").fetchall()] == ["20261009", "20261016"]
        assert c.execute("SELECT min(trd_dd), max(trd_dd), count(DISTINCT trd_dd) FROM krx_daily").fetchone() == ("20261005", "20261016", 10)
    return pg17


def _old_weekly_read(url: str) -> tuple[str, list]:
    """옮기기 전 질의 그대로 — 지금 세계와 값이 같은지 대조하는 기준."""
    with psycopg.connect(url) as c:
        as_of = c.execute("SELECT MAX(price_dd) FROM krx_weekly").fetchone()[0]
        rows = c.execute("SELECT ticker, mktcap, market FROM krx_weekly WHERE price_dd=%s", (as_of,)).fetchall()
    return as_of, rows


def test_reads_the_latest_daily_cross_section_when_krx_weekly_holds_only_closed_weeks(two_weeks):
    """다음 단계 세계: krx_weekly 에서 이번 주 행이 사라져도 이름 해석의 시총·기준일은 최신 거래일이다."""
    with psycopg.connect(two_weeks, autocommit=True) as c:
        c.execute("DELETE FROM krx_weekly WHERE price_dd >= '20261012'")             # 쓰는 쪽이 닫힌 주만 남긴다고 가정
        assert c.execute("SELECT max(price_dd) FROM krx_weekly").fetchone()[0] == "20261009"   # 옛 질의라면 지난주 금요일로 낡는다
        # 함정이 실제로 있다 — price_latest 에는 창 안에서 폐지된 종목의 옛 날짜 행이 남는다
        assert c.execute("SELECT trd_dd FROM price_latest WHERE ticker=%s", (DELISTED,)).fetchone()[0] == "20261013"

    ctx = CR._database_market_context()
    assert ctx is not None
    assert ctx.as_of_date == "20261016" and ctx.source == "krx_daily"
    assert ctx.market_caps == _expected_caps("20261016")                          # 최신일 종가 × 상장주식수
    assert DELISTED not in ctx.market_caps                                        # 폐지 종목이 명부에 섞이지 않는다
    assert NEW_LISTING in ctx.market_caps                                         # 이번 주 신규상장은 들어온다

    # 사용자에게 나가는 근거(출처 라벨·기준일·시총)도 같은 값이다
    corps = [{"corp_code": "00126380", "corp_name": "삼성전자", "corp_eng_name": "SAMSUNG ELECTRONICS CO,.LTD",
              "stock_code": "005930", "modify_date": "20250101"}]
    meta = CompanyResolver(corps, {}, ctx).search("삼성전자")[0]["_resolution"]
    assert (meta["market_data_source"], meta["market_data_as_of"], meta["market_cap_won"]) == \
        ("krx_daily", "20261016", _close("005930", 9) * SHRS["005930"])


def test_matches_the_old_krx_weekly_read_while_this_weeks_row_still_exists(two_weeks):
    """지금 세계: krx_weekly 에 이번 주 행이 있으면 옛 질의와 기준일·시총·시장 구성이 같다(라벨만 krx_daily)."""
    old_as_of, old_rows = _old_weekly_read(two_weeks)
    ctx = CR._database_market_context()
    assert ctx is not None and old_as_of == ctx.as_of_date == "20261016"
    assert ctx.market_caps == {str(t): int(cap or 0) for t, cap, _m in old_rows}
    with psycopg.connect(two_weeks) as c:
        new_markets = sorted(c.execute("SELECT ticker, market FROM krx_daily_v WHERE trd_dd=%s", (ctx.as_of_date,)).fetchall())
    assert new_markets == sorted((t, m) for t, _cap, m in old_rows)              # 완전성 판정에 쓰는 시장 칸도 같다
    assert ctx.source == "krx_daily"


def test_empty_or_missing_daily_store_returns_none_without_falling_back_to_krx_weekly(pg17, monkeypatch):
    """기준일과 행은 같은 원천에서 — krx_daily 가 비거나 없으면 krx_weekly 로 돌지 않고 None(호출자가 이전 맥락 유지·로컬 폴백)."""
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
        c.execute("INSERT INTO krx_weekly (price_dd, ticker, market, close, mktcap, list_shrs) "
                  "VALUES ('20261016', '005930', 'KS', 70000, 70000, 1)")
    monkeypatch.setenv("DATABASE_URL", pg17)
    assert CR._database_market_context() is None                                  # 마이그레이션 전(표·뷰 없음)
    _apply(pg17)
    assert CR._database_market_context() is None                                  # 표는 있는데 비었다
    monkeypatch.delenv("DATABASE_URL")
    assert CR._database_market_context() is None


def test_full_ks_kq_cross_section_turns_the_active_registry_on(pg17, monkeypatch):
    """DB 시장 칸 그대로(KS/KQ)의 전 종목 단면이면 `active_tickers` 가 켜진다 — 종전엔 'KOSPI'/'KOSDAQ' 을 세어 늘 None 이었다.
    (261019: 사실값 표의 시장은 1바이트 S/Q 라 뷰는 KS/KQ 만 낸다 — 옛 표기 행은 더 생길 수 없다.)"""
    _apply(pg17)
    with psycopg.connect(pg17, autocommit=True) as c:
        put_daily(c, [("20261016", f"{t:06d}", "KS" if t <= 800 else "KQ", 1000, 1000) for t in range(1, 2101)])
    monkeypatch.setenv("DATABASE_URL", pg17)
    monkeypatch.setattr(CR, "today_kst", lambda: date(2026, 10, 18))                # 신선도 조건은 채운다(2일 전)
    everyone = frozenset(f"{t:06d}" for t in range(1, 2101))
    ctx = CR._database_market_context()
    assert ctx is not None and len(ctx.market_caps) == 2100 and ctx.active_tickers == everyone
