"""신규 상장 보통주 등록(`market_fund_quarterly --include-new`)이 신형 영문 코드(0126Z0 …)와 숫자 코드를 같은 규칙으로 잇는다 (261020).

10-20 운영 실측(최신 거래일 20261019 상장 2,761종목): 재무 요약 표(`dart_fundamentals`)에 회사 고유번호(corp_code) 없이 있거나 행이 없는
상장 보통주 76종목(영문 코드 62 · 숫자 코드 14, 시총 약 14.3조 + α). 연간·분기 수집과 업종 비교·집계가 이 표의 ok 행만 대상으로 해서 모두 빠졌다.
  ① 해석기가 숫자 6자리만 코드로 읽던 때(10-18 고침) 적힌 영문 코드 52종목이 nocorp 로 굳었다 — 등록은 「행이 있으면 건너뜀」이라 다시 보지 않았다.
  ② 등록 단계를 도는 일정이 없어(수동 전용) 8~9월 상장사는 행 자체가 없었다.
  ③ 등록 행은 업종코드(induty)가 비어 KSIC 업종 버킷이 없었다(기존 ok 행은 전부 있다 — 옛 수집기가 채웠다). 결산월은 종전대로 결산월 단계가 정한다.
우선주·종류주(끝자리가 0 이 아닌 코드, KRX 종목유형 우선주·종류주권)는 관례대로 행을 두지 않는다(회사 단위 값은 보통주 행 하나).

순수 함수 시험은 network 0 · DB 0. 등록 시험은 로컬 Docker `postgres:17`(`tests/conftest.py` 의 `pg17`)이 필요하다 — 없으면 건너뛴다.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from test_krx_daily_pg import KRX_WEEKLY_DDL, _ddl, _row  # noqa: E402 — 일별 시세 스키마·KRX 행 모양 재사용

from open_proxy_mcp.company_resolver import CompanyResolver, MarketContext  # noqa: E402
from open_proxy_mcp.dart.client import DartClientError  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as pmd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: 회사 명부(DART corpCode) — 신형 영문 코드 둘(보통주)과 숫자 코드 둘. 우선주·종류주 코드는 명부에 없다(DART 는 보통주 코드만 싣는다).
CORPS = [
    {"corp_code": "01965324", "corp_name": "삼성에피스홀딩스", "corp_eng_name": "SAMSUNG EPIS HOLDINGS", "stock_code": "0126Z0",
     "modify_date": "20260108"},
    {"corp_code": "01885222", "corp_name": "대신밸류리츠", "corp_eng_name": "DAISHIN VALUE REIT", "stock_code": "0030R0",
     "modify_date": "20250711"},
    {"corp_code": "02031369", "corp_name": "인제니아테라퓨틱스", "corp_eng_name": "INGENIA", "stock_code": "950260",
     "modify_date": "20260821"},
    {"corp_code": "00164742", "corp_name": "미래에셋증권", "corp_eng_name": "Mirae Asset Securities", "stock_code": "006800",
     "modify_date": "20250101"},
]
#: 그 날 상장 — 코스피 0126Z0·0030R0·006800·00680K(미래에셋증권2우B)·K00000 · 코스닥 950260·Q00000
KS = ("0126Z0", "0030R0", "006800", "00680K", "K00000")
KQ = ("950260", "Q00000")
KINDS = {"0126Z0": "보통주", "0030R0": "보통주", "006800": "보통주", "00680K": "신형우선주", "K00000": "보통주",
         "950260": "보통주", "Q00000": "보통주"}
INFO = {"01965324": {"induty_code": "64992", "acc_mt": "12"}, "01885222": {"induty_code": "68112", "acc_mt": "08"},
        "02031369": {"induty_code": "", "acc_mt": "12"}}


def _resolver() -> CompanyResolver:
    market = MarketContext(market_caps={t: 1 for t in KS + KQ}, active_tickers=None, as_of_date="20261019",
                           source="krx_daily", share_class_tickers=frozenset({"00680K"}))
    return CompanyResolver(CORPS, {}, market)


class _Client:
    """`include_new_tickers` 가 부르는 것만 — 해석은 진짜 해석기(`CompanyResolver`, 서버 `company` 와 같은 규칙)."""

    def __init__(self, halt_info: bool = False, corps=CORPS):
        self.r = _resolver()
        self.corps = corps
        self.info_calls: list[str] = []
        self.lookups: list[str] = []
        self.halt_info = halt_info

    async def _load_corp_codes(self):
        return self.corps

    async def lookup_corp_code(self, q):
        self.lookups.append(q)
        res = self.r.search(q)
        if not res:
            return None
        meta = res[0].get("_resolution") or {}
        if meta.get("inferred") and not meta.get("auto_selected"):
            return None
        return res[0]

    async def get_company_info(self, cc):
        self.info_calls.append(cc)
        if self.halt_info:
            raise DartClientError("020", "사용한도를 초과하였습니다.")
        return INFO[cc]


@pytest.fixture
def db(pg17, monkeypatch):
    from scripts import market_fund_quarterly as Q
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
    _ddl().apply(pg17)
    monkeypatch.setenv("DATABASE_URL", pg17)
    import open_proxy_mcp.db as dbm
    monkeypatch.setattr(dbm, "_pool", None)
    monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))
    res = pmd._krx_db_upsert("20261019", [_row(t, 1000 + i) for i, t in enumerate(KS)], [_row(t, 500 + i) for i, t in enumerate(KQ)])
    assert res.daily_ok
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(Q.DDL_FUNDAMENTALS)
        c.execute("ALTER TABLE dart_fundamentals ADD COLUMN induty text")      # 운영 표에는 있다(옛 수집기 칸)
        # 기존 행: 숫자 보통주 ok 둘 · 신형 영문 코드 nocorp(해석기가 숫자 6자리만 코드로 읽던 때 적힌 것) · 리츠 0030R0 은 행 없음 · 950260 행 없음
        #   · D00000 = 그 날 상장 목록에 없는(상장폐지) ok 행, 업종코드 빈 칸 — 업종코드 메우기는 상장 보통주만이라 개황을 부르지 않는다(검토 변이)
        c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, market, fetched, induty, acc_mt) VALUES "
                  "('006800', '00164742', 'KS', 'ok', '66121', 12), ('K00000', 'cK00000', 'KS', 'ok', '264', 12), "
                  "('Q00000', 'cQ00000', 'KQ', 'ok', '582', 12), ('0126Z0', NULL, 'KS', 'nocorp', NULL, NULL), "
                  "('D00000', 'cD00000', 'KS', 'ok', NULL, 12)")
    monkeypatch.setitem(sys.modules, "market_val_weekly", types.SimpleNamespace(_krx_kinds=_kinds))
    monkeypatch.setattr(Q, "INCLUDE_NEW_MIN_LISTED", 3)
    monkeypatch.setattr(Q, "SLEEP", 0)
    return pg17


async def _kinds(dd):
    assert dd == "20261019"
    return dict(KINDS)


def _rows(url):
    with psycopg.connect(url) as c:
        return {r[0]: r[1:] for r in c.execute(
            "SELECT ticker, corp_code, market, fetched, induty, acc_mt FROM dart_fundamentals ORDER BY ticker").fetchall()}


def _run(monkeypatch, client):
    from scripts import market_fund_quarterly as Q
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: client)
    return asyncio.run(Q.include_new_tickers())


def test_alnum_nocorp_rows_are_relinked_and_new_common_listings_registered_with_industry(db, monkeypatch, capsys):
    """기존 ok 행(006800 은 결산월 12)은 그대로, 이은·새 행은 업종코드까지."""
    client = _Client()
    assert _run(monkeypatch, client) == 3                      # 다시 이음 0126Z0 + 새로 0030R0·950260
    rows = _rows(db)
    # 신형 영문 코드 nocorp → ok(회사 고유번호·업종코드). 결산월은 여기서 정하지 않는다 — 결산월 단계(`--acc-mt`, 사업보고서 이름이 정본) 한 곳
    assert rows["0126Z0"] == ("01965324", "KS", "ok", "64992", None)
    # 행이 없던 영문·숫자 코드 상장 보통주 — 같은 규칙으로 등록. 개황이 빈 업종코드를 주면 'none'(옛 수집기와 같은 값 — 다시 부르지 않는다)
    assert rows["0030R0"] == ("01885222", "KS", "ok", "68112", None)
    assert rows["950260"] == ("02031369", "KQ", "ok", "none", None)
    # 우선주(00680K)는 관례대로 행을 두지 않는다 · 기존 ok 행은 그대로
    assert "00680K" not in rows
    assert rows["006800"] == ("00164742", "KS", "ok", "66121", 12)
    assert rows["D00000"] == ("cD00000", "KS", "ok", None, 12)  # 상장 목록에 없는 ok 행은 업종코드가 비어도 그대로(개황을 부르지 않는다)
    assert sorted(client.info_calls) == ["01885222", "01965324", "02031369"]   # 이은 종목만 개황 1콜씩
    assert client.lookups == []                                 # 모두 명부의 종목코드 표로 이었다 — 해석기(정기공시 제출사 명부 만들기)를 거치지 않는다
    out = capsys.readouterr().out
    assert "다시 이음 1" in out and "(nocorp 0)" in out
    # 두 번째 실행 — 할 일이 없다(개황도 다시 부르지 않는다)
    client2 = _Client()
    assert _run(monkeypatch, client2) == 0
    assert client2.info_calls == [] and _rows(db) == rows


def test_a_daily_limit_on_company_info_keeps_the_link_and_the_next_run_fills_the_industry(db, monkeypatch, capsys):
    halted = _Client(halt_info=True)
    assert _run(monkeypatch, halted) == 3
    rows = _rows(db)
    assert rows["0126Z0"][:3] == ("01965324", "KS", "ok") and rows["0126Z0"][3] is None   # 잇기는 남고 업종코드는 빈 채
    out = capsys.readouterr().out
    assert "020" in out and "중단" in out
    assert len(halted.info_calls) == 1                          # 키 한도(020)면 그 자리에서 멈춘다 — 남은 종목에 같은 답을 받으러 더 부르지 않는다
    client = _Client()
    assert _run(monkeypatch, client) == 0                       # 새로 이을 것은 없고 — 업종코드가 빈 상장 보통주 ok 행만 개황을 부른다
    assert sorted(client.info_calls) == ["01885222", "01965324", "02031369"]
    assert _rows(db)["0126Z0"] == ("01965324", "KS", "ok", "64992", None)


def test_a_code_the_registry_does_not_have_stays_nocorp_and_is_tried_again(db, monkeypatch):
    class Stale(_Client):                                       # 명부가 아직 그 코드를 모를 때(DART 가 늦게 싣는다)
        async def lookup_corp_code(self, q):
            self.lookups.append(q)
            return None if q == "0126Z0" else await super().lookup_corp_code(q)
    stale = Stale(corps=[c for c in CORPS if c["stock_code"] != "0126Z0"])
    _run(monkeypatch, stale)
    assert _rows(db)["0126Z0"][:3] == (None, "KS", "nocorp")
    assert stale.lookups == ["0126Z0"]                          # 명부 표에 없는 코드만 해석기로 다시 본다(명부가 낡았으면 해석기가 새로 받는다)
    _run(monkeypatch, _Client())                                # 다음 실행 — 명부가 따라오면 잇는다
    assert _rows(db)["0126Z0"][:3] == ("01965324", "KS", "ok")


# ── 순수 함수 ─────────────────────────────────────────────────────────────────

def test_link_only_to_the_company_of_that_very_code():
    from scripts.market_fund_quarterly import exact_corp_code
    r = _resolver()
    assert exact_corp_code(r.search("0126Z0")[0], "0126Z0") == "01965324"
    assert exact_corp_code(r.search("950260")[0], "950260") == "02031369"
    # 종류주 코드는 발행 회사로 이어지지만(서버 해석) — 재무 요약 행은 그 종목코드 자체의 회사일 때만
    share = r.search("00680K")
    assert share and share[0]["corp_code"] == "00164742"
    assert exact_corp_code(share[0], "00680K") is None
    assert exact_corp_code({"corp_code": "00999999"}, "N00000") is None     # 종목코드가 없는 결과(이름 추정 등)는 받지 않는다
    assert exact_corp_code(None, "0126Z0") is None


def test_registry_code_map_is_upper_trimmed_and_prefers_the_latest_record():
    from scripts.market_fund_quarterly import corp_by_ticker
    m = corp_by_ticker(CORPS + [{"corp_code": "X", "stock_code": "", "modify_date": "1"},
                                {"corp_code": "OLD", "stock_code": " 0126z0 ", "modify_date": "20200101"}])
    assert m["0126Z0"] == "01965324" and m["950260"] == "02031369" and "" not in m
    # 소문자·공백 섞인 명부 기록도 KRX 의 대문자 코드로 찾힌다(위 OLD 는 날짜가 늦은 기록에 져서 대문자화를 따로 확인 못 한다 — 검토 변이)
    assert corp_by_ticker([{"corp_code": "L", "stock_code": " 0002b0 ", "modify_date": "20260101"}]) == {"0002B0": "L"}
    assert corp_by_ticker([{"corp_code": "A", "stock_code": "0001A0", "modify_date": "20250101"},
                           {"corp_code": "B", "stock_code": "0001A0", "modify_date": "20260101"}]) == {"0001A0": "B"}


def test_targets_are_listed_common_shares_without_a_row_or_with_a_nocorp_row():
    from scripts.market_fund_quarterly import include_targets
    listed = [(t, "KS") for t in KS] + [(t, "KQ") for t in KQ]
    rows = {"006800": ("00164742", "ok"), "K00000": ("c", "ok"), "Q00000": ("c", "ok"), "0126Z0": (None, "nocorp")}
    new, again = include_targets(listed, rows, KINDS)
    assert new == [("0030R0", "KS"), ("950260", "KQ")]
    assert again == [("0126Z0", "KS")]
    # 종목유형을 모르는 코드(목록에 없음)는 넣지 않는다 — 우선주를 보통주로 넣지 않기 위해
    assert include_targets([("0220W0", "KS")], {}, {}) == ([], [])


def test_the_weekly_schedule_registers_new_listings_before_annual_and_quarterly_fetch():
    wf = (ROOT / ".github/workflows/finstat-quarterly.yml").read_text(encoding="utf-8")
    steps = [ln.strip() for ln in wf.splitlines() if ln.strip().startswith("run: uv run python scripts/")]
    order = [s.split("scripts/", 1)[1] for s in steps]
    assert order.index("market_fund_quarterly.py --include-new") < order.index("market_fund_quarterly.py --acc-mt") \
        < order.index("market_val_series.py --fetch")
    block = wf.split("run: uv run python scripts/market_fund_quarterly.py --include-new", 1)[0].rsplit("- name:", 1)[1]
    assert "KRX_OPEN_API_KEY: ${{ secrets.KRX_OPEN_API_KEY }}" in block and "OPENDART_API_KEY" in block
