"""지수 구성종목 원장(코스피200·코스닥150) — 261018. network 0콜 · DB 0콜(가짜 읽기).

계기: 「코스피200」 유니버스가 원장이 없어 늘 KOSPI 시총상위 200 으로 대체됐다(처음부터). 실측(20261016) 공식 201종목 중
34종목이 대체 목록에 없었고, 대체 목록의 33종목은 지수 밖이었다 — `screener(universe="코스피200")` 가 그만큼의 공시를 놓치고
지수 밖 공시를 섞었다. 지키는 것:
  ① 원장이 있으면 그 구성 그대로(정원으로 자르지 않고 우선주 판정으로 빼지 않는다) · 대체 경고 없음 · 확인일을 싣는다
  ② 원장을 못 읽으면 종전처럼 시총 상위 정원으로 대체하되 **밝히고**(경고) **센다**(조용한 대체 장부)
  ③ 원장 확인이 오래되면 경고 ④ 「코스닥150」「KOSPI 200」 같은 지수 이름을 되묻지 않고 알아듣는다
  ⑤ 적재는 KRX 공식 지수 합계(시총·거래량·거래대금)와 셋 다 맞을 때만, 구성이 그대로면 확인일만 올린다
실제 Postgres 에서의 표·권한·쓰기는 `test_index_members_pg.py`.
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp import index_members as im
from open_proxy_mcp.dart.client import _ctx_ledger, degradations, new_request_ledger
from open_proxy_mcp.services import screener, universe as uni
from open_proxy_mcp.services.trading import build_universe_payload
from scripts import krx_index_members as S


# ── 읽기 모듈 ────────────────────────────────────────────────────────────────

def test_latest_reads_one_snapshot_and_treats_down_and_empty_alike():
    seen = []

    def read(sql, params):
        seen.append((sql, params))
        return [("20261016", "20261016", "005930"), ("20261016", "20261015", "000660")]
    m = im.latest("kospi200", read)
    assert m.tickers == {"005930", "000660"} and m.snap_dd == "20261016"
    assert m.checked_dd == "20261015"                    # 어긋나면 덜 믿는 쪽(이른 값)
    assert seen[0][1] == ("kospi200", "kospi200") and "max(snap_dd)" in seen[0][0]
    assert im.latest("kospi200", lambda *a: None) is None   # 장애
    assert im.latest("kospi200", lambda *a: []) is None     # 빈 표


def test_stale_note_only_beyond_threshold():
    spec = im.INDEXES["kospi200"]
    m = im.Members("kospi200", "20261001", "20261005", frozenset({"005930"}))
    assert im.stale_note(spec, m, "20261015") == ""                      # 10일 — 경계 안(긴 연휴 뒤 아침)
    note = im.stale_note(spec, m, "20261016")                            # 11일
    assert "KOSPI200" in note and "20261005" in note and "11일" in note
    assert im.stale_note(spec, m, None) == "" and im.days_behind("20261016", "20261010") == 0


# ── 말 해석 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("given,spec", [
    ("코스피200", "kospi200"), ("KOSPI 200", "kospi200"), ("kospi 200", "kospi200"),
    ("코스닥150", "kosdaq150"), ("코스닥 150", "kosdaq150"), ("KOSDAQ150", "kosdaq150"), ("kosdaq150", "kosdaq150"),
])
def test_index_names_are_understood_not_asked_back(given, spec):
    """종전: 「코스닥150」「KOSPI 200」은 「시총 상위 150 인지 이름인지」 되묻기로 갔다."""
    from open_proxy_mcp.services.universe import clarification
    assert screener._nl_universe(given) == spec
    assert clarification(given) is None


def test_plain_number_after_market_still_asks_back():
    """지수 이름이 아닌 「코스닥 120」은 여전히 되묻는다 — 별칭을 넓히다 이 규칙을 깨지 않는다."""
    from open_proxy_mcp.services.universe import clarification
    assert clarification("코스닥 120") is not None


# ── screener 유니버스 ───────────────────────────────────────────────────────

_MEMBERS = frozenset({"005930", "000660", "000080"})


@pytest.fixture
def ledger(monkeypatch):
    """원장 대역 — 지수마다 Members 또는 None(못 읽음)."""
    table: dict[str, im.Members | None] = {
        "kospi200": im.Members("kospi200", "20261016", "20261016", _MEMBERS),
        "kosdaq150": im.Members("kosdaq150", "20261016", "20261016", frozenset({"196170", "086520"})),
    }
    monkeypatch.setattr(screener, "_krx_latest_dd", lambda: "20261016")
    monkeypatch.setattr(screener, "_index_members_latest", lambda code: table.get(code))
    return table


@pytest.fixture
def _ledger_ctx():
    _ctx_ledger.set(None)
    new_request_ledger()
    yield
    _ctx_ledger.set(None)


def test_kospi200_comes_from_the_ledger(ledger, _ledger_ctx, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("원장이 있는데 시총 순위를 읽었다")
    monkeypatch.setattr(screener, "_krx_top_mktcap", boom)
    uf = asyncio.run(screener.resolve_universe("kospi200"))
    assert uf.resolved and uf.allowed == set(_MEMBERS)
    assert uf.label == "KOSPI200" and uf.notice == "" and uf.members_dd == "20261016"
    assert degradations() == []
    kq = asyncio.run(screener.resolve_universe(screener._nl_universe("코스닥150")))
    assert kq.allowed == {"196170", "086520"} and kq.label == "KOSDAQ150"


def test_ledger_unreadable_falls_back_to_rank_and_says_so(ledger, _ledger_ctx, monkeypatch):
    ledger["kospi200"] = None
    asked = []

    def top(n, dd, market=None):
        asked.append((n, market))
        return {"005930", "005935"}
    monkeypatch.setattr(screener, "_krx_top_mktcap", top)
    uf = asyncio.run(screener.resolve_universe("kospi200"))
    assert asked == [(200, "KS")]
    assert uf.resolved and uf.allowed == {"005930", "005935"} and uf.members_dd is None
    assert "대체" in uf.label and "원장을 읽지 못해" in uf.notice and "KOSPI 시총상위 200" in uf.notice
    assert degradations() == ["universe_approximated"]          # 조용히 넘어가지 않고 센다


def test_stale_ledger_is_warned(ledger, monkeypatch):
    ledger["kospi200"] = im.Members("kospi200", "20260901", "20260930", _MEMBERS)
    uf = asyncio.run(screener.resolve_universe("kospi200"))
    assert uf.allowed == set(_MEMBERS)
    assert "마지막으로 확인됐다" in uf.notice and "20260930" in uf.notice


# ── 순위표(trading_data scope=universe) ───────────────────────────────────────

_CAPS = [  # (ticker, market, mktcap, close, shrs)
    ("005930", "KS", 500_000_000_000_000, 80_000, 5_969_782_550),
    ("005935", "KS", 150_000_000_000_000, 70_000, 822_886_700),     # 우선주 — 지수 구성 아님
    ("000660", "KS", 300_000_000_000_000, 400_000, 728_002_365),
    ("005380", "KS", 60_000_000_000_000, 280_000, 209_416_191),     # 시총은 크지만 이 가짜 지수의 구성 아님
    ("000080", "KS", 2_000_000_000_000, 20_000, 70_000_000),
]
_NAMES = {"005930": "삼성전자", "000660": "SK하이닉스", "005380": "현대자동차", "000080": "하이트진로"}


def _stub_rows(monkeypatch, caps=_CAPS):
    def rows(price_dd, codes):
        return [c for c in sorted(caps, key=lambda c: -c[2]) if codes is None or c[0] in codes]
    monkeypatch.setattr(uni, "_krx_rows", rows)

    async def names():
        return dict(_NAMES)
    monkeypatch.setattr(uni, "_names_by_ticker", names)


def test_universe_table_is_the_ledger_as_is(ledger, monkeypatch):
    _stub_rows(monkeypatch)
    p = asyncio.run(build_universe_payload("코스피200"))
    assert p["status"] == "ok"
    d = p["data"]
    assert [r["ticker"] for r in d["rows"]] == ["005930", "000660", "000080"]   # 시총순, 지수 밖 현대차·우선주 없음
    assert d["label"] == "KOSPI200" and d["members_dd"] == "20261016" and d["n"] == 3
    assert not any("대체" in w for w in p["warnings"])
    assert not any("우선주" in w for w in p["warnings"])
    assert "구성종목 원장" in d["method"]


def test_universe_table_does_not_cut_at_the_nominal_size(ledger, monkeypatch):
    """분할 신설회사 편입으로 정원(200)을 넘는 날이 있다(20261016 실측 201) — 자르지 않는다."""
    caps = [(f"{i:06d}", "KS", 10**12 * (300 - i), 1000, 1) for i in range(1, 202)]
    ledger["kospi200"] = im.Members("kospi200", "20261016", "20261016", frozenset(c[0] for c in caps))
    _stub_rows(monkeypatch, caps)
    p = asyncio.run(build_universe_payload("코스피200"))
    assert p["data"]["n"] == 201


def test_member_without_a_price_row_is_named_not_dropped_silently(ledger, monkeypatch):
    ledger["kospi200"] = im.Members("kospi200", "20261016", "20261016", _MEMBERS | {"0220W0"})
    _stub_rows(monkeypatch)
    p = asyncio.run(build_universe_payload("코스피200"))
    assert p["data"]["n"] == 3
    assert any("0220W0" in w and "시세가 없어" in w for w in p["warnings"])


def test_universe_table_fallback_keeps_the_old_shape(ledger, monkeypatch):
    """원장을 못 읽으면 종전과 같다 — 시총 상위 200, 우선주 빼고 채운다, 대체를 밝힌다."""
    ledger["kospi200"] = None
    caps = [(f"{i:06d}", "KS", 10**12 * (400 - i), 1000, 1) for i in range(1, 251)] + \
           [("005935", "KS", 10**15, 70_000, 1)]
    names = {c[0]: f"회사{c[0]}" for c in caps if c[0] != "005935"} | {"005930": "삼성전자"}

    def top(n, dd, market=None):
        return {c[0] for c in sorted(caps, key=lambda c: -c[2])[:n]}
    monkeypatch.setattr(screener, "_krx_top_mktcap", top)
    _stub_rows(monkeypatch, caps)

    async def _names():
        return names
    monkeypatch.setattr(uni, "_names_by_ticker", _names)
    p = asyncio.run(build_universe_payload("코스피200"))
    d = p["data"]
    assert d["n"] == 200 and "005935" not in [r["ticker"] for r in d["rows"]]
    assert "대체" in d["label"] and "members_dd" not in d
    assert any("원장을 읽지 못해" in w for w in p["warnings"])


# ── 적재 스크립트(순수 함수) ─────────────────────────────────────────────────

def _items(codes):
    return [{"itemCode": c, "stockEndType": "stock"} for c in codes]


def test_parse_list_rejects_odd_shapes():
    assert S.parse_list_page(_items(["005930", "0220W0"])) == ["005930", "0220W0"]
    assert S.parse_list_page([]) == []
    for bad in ({"code": "x"}, [{"itemCode": "5930"}], [{"itemCode": "005930", "stockEndType": "etf"}]):
        with pytest.raises(S.SourceError):
            S.parse_list_page(bad)


def test_check_members_guards_count_and_duplicates():
    src = S.SOURCES[0]
    ok = [f"{i:06d}" for i in range(201)]
    assert len(S.check_members(ok, src)) == 201
    with pytest.raises(S.SourceError, match="두 번"):
        S.check_members(ok[:-1] + [ok[0]], src)
    with pytest.raises(S.SourceError, match="기대 범위"):
        S.check_members(ok[:120], src)                     # 목록이 잘렸다


def _stock(code, cap, vol, val):
    return {"ISU_CD": code, "MKTCAP": str(cap), "ACC_TRDVOL": str(vol), "ACC_TRDVAL": str(val)}


def test_verify_needs_all_three_sums_exact():
    rows = [_stock("A00001", 100, 10, 1000), _stock("A00002", 50, 7, 500), _stock("A00003", 49, 7, 499)]
    idx = {"IDX_NM": "코스피 200", "MKTCAP": "150", "ACC_TRDVOL": "17", "ACC_TRDVAL": "1500"}
    assert S.verify(frozenset({"A00001", "A00002"}), rows, idx).ok
    # 비슷한 종목으로 하나 바뀌면(시총 1 차이) — 셋 중 어느 것도 맞지 않는다
    c = S.verify(frozenset({"A00001", "A00003"}), rows, idx)
    assert not c.ok and "차" in c.describe()
    # KRX 그 시장 자료에 없는 종목
    c = S.verify(frozenset({"A00001", "B00009"}), rows, idx)
    assert not c.ok and c.missing == ["B00009"]
    # KRX 합계 칸이 비면 확인하지 않은 것이다
    assert not S.verify(frozenset({"A00001", "A00002"}), rows, {**idx, "MKTCAP": ""}).ok
    assert S.find_index_row([idx, {"IDX_NM": "코스피 200 TOP 10"}], "코스피 200") is idx


def test_plan_insert_touch_noop_refuse():
    m = frozenset({"A", "B"})
    assert S.plan(None, m, "20261016").action == "insert"
    prev = S.Prev("20261001", "20261015", m)
    assert S.plan(prev, m, "20261016").action == "touch"
    assert S.plan(prev, m, "20261015").action == "noop"
    p = S.plan(prev, frozenset({"A", "C"}), "20261016")
    assert p.action == "insert" and p.added == ("C",) and p.removed == ("B",)
    assert S.plan(prev, frozenset({"A", "C"}), "20261001").action == "refuse"   # 옛 날짜로 이력을 고쳐 쓰지 않는다


class _FakeConn:
    """settle 이 쓰는 만큼만 흉내 — 직전 묶음 읽기·자문 잠금·삽입·확인일 올리기."""

    def __init__(self, prev_row):
        self.prev_row, self.sql = prev_row, []

    def transaction(self):
        import contextlib
        return contextlib.nullcontext()

    def execute(self, sql, params=()):
        self.sql.append(sql)
        row = self.prev_row if sql == S.PREV_SQL else None
        return type("R", (), {"fetchone": lambda s: row, "rowcount": 2})()

    def cursor(self):
        conn = self

        class _C:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def executemany(self, sql, rows): conn.sql.append(sql)
        return _C()

    def close(self):
        pass


def _settle(monkeypatch, prev_row, outcome, d="20261016", dry=False):
    import psycopg
    conn = _FakeConn(prev_row)
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: conn)
    return S.settle([outcome], d, "postgresql://x/y", dry), conn


def test_settle_mismatch_on_a_transition_day_is_a_warning(monkeypatch, capsys):
    src = S.SOURCES[0]
    bad = S.Outcome(src, members=frozenset({"A"}), check=S.Check(ok=False, sums=(1, 1, 1), krx=(2, 2, 2)))
    rc, conn = _settle(monkeypatch, ("20261001", "20261015", ["A", "B"]), bad)
    assert rc == 0 and "::warning::" in capsys.readouterr().out
    assert S.INSERT_SQL not in conn.sql and S.TOUCH_SQL not in conn.sql      # 쓰지 않았다
    rc, _ = _settle(monkeypatch, ("20261001", "20261005", ["A", "B"]), bad)    # 11일째 어긋남
    assert rc == 1 and "::error::" in capsys.readouterr().out
    rc, _ = _settle(monkeypatch, None, bad)                                    # 원장이 비었는데 확인 실패
    assert rc == 1


def test_settle_source_error_fails_and_verified_writes(monkeypatch, capsys):
    src = S.SOURCES[0]
    rc, _ = _settle(monkeypatch, ("20261001", "20261015", ["A"]), S.Outcome(src, error="목록 1쪽 응답 403"))
    assert rc == 1
    good = S.Outcome(src, members=frozenset({"A", "B"}), check=S.Check(ok=True, sums=(1, 1, 1), krx=(1, 1, 1)))
    rc, conn = _settle(monkeypatch, ("20261001", "20261015", ["A", "B"]), good)
    assert rc == 0 and S.TOUCH_SQL in conn.sql and S.INSERT_SQL not in conn.sql
    rc, conn = _settle(monkeypatch, ("20261001", "20261015", ["A", "B"]), good, dry=True)
    assert rc == 0 and S.TOUCH_SQL not in conn.sql                              # 드라이런은 쓰지 않는다
    assert "계획 touch" in capsys.readouterr().out
