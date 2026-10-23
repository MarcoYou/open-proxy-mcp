"""ownership_structure — 과거 시점(as_of)이 그 날의 정보만 쓰고(DATA-PIT9·PIT8), 최대주주는 공시가 「본인」으로 적은 행이다.

세 결함(공개 TODO 의 P0 줄):
  ① `_latest_block_rows` 가 전체에서 보고자별 최신 5% 보고를 먼저 고르고 기준일 밖이면 나중에 버려, 기준일 뒤에 다시 보고한 보고자가
     과거 시점 보유자 목록에서 통째로 사라졌고 보유 목적을 기준일 뒤 원문에서 읽을 수 있었다.
  ② `as_of.year - 1` 로 사업연도를 못박아 비12월 결산·사업보고서 제출 전 시점이 어긋났고, 과거 시점에도 오늘의 상장주식수와 맞댔다.
  ③ `_top_holder_summary` 가 지분율 내림차순 첫 행을 최대주주로 내, 본인 아닌 임원·친족의 지분이 더 크면 그 사람이 최대주주로 나갔다.

고정본(실제 DART 응답을 필요한 칸·행만 남김, network 0 · DB 0):
  · majorstock_korea_zinc_00102858_20261023.json — 고려아연 5% 대량보유 목록 중 2025-09 이후 접수분.
  · hyslrsttus_hanmi_science_00161426_2025.json — 한미사이언스 2025 사업보고서 최대주주 명부(지분 1% 넘는 행, 보유 주식 수 칸은 뺌).
비12월 결산(3월)·결산월 조회 실패는 합성 클라이언트다 — 실제 회사 응답이 아니라 달력 규칙(`report_calendar.pit_annual`)을 시험한다.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from open_proxy_mcp.services import ownership_structure as O
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus

_DIR = Path(__file__).parent / "dart_responses" / "dart"
_KZ = json.loads((_DIR / "majorstock_korea_zinc_00102858_20261023.json").read_text(encoding="utf-8"))
_HANMI = json.loads((_DIR / "hyslrsttus_hanmi_science_00161426_2025.json").read_text(encoding="utf-8"))

KOREA_ZINC = {"corp_code": "00102858", "corp_name": "고려아연", "stock_code": "010130"}
HANMI = {"corp_code": "00161426", "corp_name": "한미사이언스", "stock_code": "008930"}


class _Client:
    """합성 DART 클라이언트 — 부른 인자를 적어 둔다."""

    def __init__(self, *, major=None, blocks=None, acc_mt="12", company_info_fails=False) -> None:
        self._major = major if major is not None else {"list": []}
        self._blocks = blocks if blocks is not None else {"list": []}
        self._acc_mt = acc_mt
        self._info_fails = company_info_fails
        self.major_years: list[str] = []
        self.documents: list[str] = []
        self.info_calls = 0

    def api_call_snapshot(self) -> int:
        return 0

    async def get_company_info(self, corp_code):
        self.info_calls += 1
        if self._info_fails:
            raise RuntimeError("company.json 실패")
        return {"status": "000", "acc_mt": self._acc_mt}

    async def get_major_shareholders(self, corp_code, bsns_year, reprt_code="11011"):
        self.major_years.append(str(bsns_year))
        return self._major

    async def get_stock_total(self, corp_code, bsns_year, reprt_code="11011"):
        return {"list": []}

    async def get_treasury_stock(self, corp_code, bsns_year, reprt_code="11011"):
        return {"list": []}

    async def get_block_holders(self, corp_code):
        return self._blocks

    async def get_document_cached(self, rcept_no):
        self.documents.append(rcept_no)
        return {"html": ""}


def _build(monkeypatch, client, selected, **kwargs) -> dict:
    async def resolve(query, *a, **k):
        return CompanyResolution(status=AnalysisStatus.EXACT, query=query, selected=dict(selected), candidates=[])

    listed_calls: list[str] = []

    async def fake_listed(stock_code):
        listed_calls.append(stock_code)
        return {"shares": 1_000, "price_dd": "20261021"}

    monkeypatch.setattr(O, "get_dart_client", lambda: client)
    monkeypatch.setattr(O, "resolve_company_query", resolve)
    monkeypatch.setattr(O, "_latest_listed_shares", fake_listed)
    monkeypatch.setattr(O, "today_kst", lambda: date(2026, 10, 23))
    payload = asyncio.run(O.build_ownership_structure_payload(selected["corp_name"], **kwargs))
    payload["_listed_calls"] = listed_calls
    return payload


# ── ① 보고자별 최신 5% 보고는 기준일까지 접수된 것만 ─────────────────────────────

def test_as_of_blocks_keep_holders_whose_latest_report_came_after_the_date(monkeypatch) -> None:
    """2026-01-31 시점: 그 뒤(2026-03~09)에 다시 보고한 보유자도 그 날까지 낸 마지막 보고로 남는다.

    종전: 보고자별 「전체 최신」(2026-05~09 접수)이 기준일 밖이라 버려져 영풍·한국기업투자홀딩스·최윤범·국민연금공단이 모두 사라지고
    그 날 이전 보고뿐이던 크루시블제이브이 한 곳만 남았다.
    """
    client = _Client(blocks=_KZ)
    p = _build(monkeypatch, client, KOREA_ZINC, scope="blocks", as_of_date="20260131")
    blocks = {b["reporter"]: b for b in p["data"]["blocks"]}
    assert set(blocks) == {"영풍", "한국기업투자홀딩스", "최윤범", "국민연금공단", "크루시블제이브이"}
    assert blocks["영풍"]["rcept_no"] == "20251224000546" and blocks["영풍"]["ownership_pct"] == 46.0
    assert blocks["한국기업투자홀딩스"]["rcept_no"] == "20251224000566"
    assert blocks["최윤범"]["rcept_no"] == "20251112000401" and blocks["최윤범"]["ownership_pct"] == 19.11
    assert blocks["국민연금공단"]["rcept_no"] == "20251001000229" and blocks["국민연금공단"]["ownership_pct"] == 5.0
    assert all(b["report_date"].replace("-", "") <= "20260131" for b in blocks.values())


def test_as_of_blocks_never_read_a_filing_received_after_the_date(monkeypatch) -> None:
    """보유 목적을 읽으려고 원문을 부를 때도 기준일 뒤 접수번호는 부르지 않는다(미래 원문에서 목적을 읽지 않는다)."""
    client = _Client(blocks=_KZ)
    _build(monkeypatch, client, KOREA_ZINC, scope="blocks", as_of_date="20260131")
    assert client.documents, "시험이 원문 조회 경로를 지나가야 의미가 있다"
    assert all(no[:8] <= "20260131" for no in client.documents)


def test_blocks_without_as_of_still_use_the_latest_reports(monkeypatch) -> None:
    """as_of 없이 부르면 종전과 같다 — 보고자별 가장 늦은 보고."""
    client = _Client(blocks=_KZ)
    p = _build(monkeypatch, client, KOREA_ZINC, scope="blocks")
    blocks = {b["reporter"]: b for b in p["data"]["blocks"]}
    assert blocks["영풍"]["rcept_no"] == "20260520000264" and blocks["영풍"]["ownership_pct"] == 41.13
    assert blocks["국민연금공단"]["rcept_no"] == "20260731000377" and blocks["국민연금공단"]["ownership_pct"] == 5.48
    assert blocks["최윤범"]["rcept_no"] == "20260917000266"


def test_latest_block_rows_timeline_is_not_cut_by_as_of(monkeypatch) -> None:
    """이력(timeline)은 그대로 전부 — 호출자가 기간창으로 거른다."""
    monkeypatch.setattr(O, "get_dart_client", lambda: _Client(blocks=_KZ))
    rows, timeline, _note = asyncio.run(O._latest_block_rows("00102858", "20260131"))
    assert len(timeline) == len(_KZ["list"])
    assert {r["reporter"] for r in rows} == {"영풍", "한국기업투자홀딩스", "최윤범", "국민연금공단", "크루시블제이브이"}


# ── ② as_of 사업연도는 결산월을 아는 공시 시점 ─────────────────────────────────

@pytest.mark.parametrize(
    "acc_mt, as_of, expected",
    [
        # 3월 결산: 사업보고서(기간 말 3월)는 4개월 뒤 7/1 부터. 종전 `as_of.year - 1` 은 2026-08 에 2025 를 가리켰다(2026.03 보고서가 나온 뒤인데).
        ("3", "20260810", "2026"),
        ("3", "20260510", "2025"),
        # 12월 결산: 1~3월은 아직 안 낸 전년 사업보고서 대신 전전년(종전은 전년으로 못박았다). 4월 이후는 종전과 같다.
        ("12", "20260210", "2024"),
        ("12", "20260630", "2025"),
        # 개황을 못 읽으면 12월 결산으로 본다.
        ("", "20260630", "2025"),
    ],
)
def test_as_of_year_follows_the_fiscal_calendar(monkeypatch, acc_mt, as_of, expected) -> None:
    client = _Client(major=_HANMI, acc_mt=acc_mt)
    p = _build(monkeypatch, client, HANMI, scope="major_holders", as_of_date=as_of)
    assert client.major_years[0] == expected
    assert p["data"]["year"] == expected


def test_as_of_year_falls_back_to_december_when_company_info_fails(monkeypatch) -> None:
    client = _Client(major=_HANMI, company_info_fails=True)
    p = _build(monkeypatch, client, HANMI, scope="major_holders", as_of_date="20260630")
    assert p["data"]["year"] == "2025"


def test_explicit_year_wins_and_skips_the_company_info_call(monkeypatch) -> None:
    client = _Client(major=_HANMI, acc_mt="3")
    p = _build(monkeypatch, client, HANMI, scope="major_holders", year=2023, as_of_date="20260810")
    assert p["data"]["year"] == "2023" and client.info_calls == 0


def test_without_as_of_the_year_is_unchanged_and_no_extra_call(monkeypatch) -> None:
    client = _Client(major=_HANMI, acc_mt="3")
    p = _build(monkeypatch, client, HANMI, scope="major_holders")
    assert p["data"]["year"] == "2025" and client.info_calls == 0   # 오늘(2026-10-23) 기준 전년


def test_as_of_does_not_compare_with_todays_listed_shares(monkeypatch) -> None:
    """과거 시점의 분모를 오늘의 상장주식수와 맞대지 않는다 — 종전엔 사업연도가 우연히 작년과 같으면 맞댔다."""
    stock_total = {"list": [{"rcept_no": "20260310002820", "se": "보통주", "istc_totqy": "1,000", "tesstk_co": "0",
                             "distb_stock_co": "1,000", "stlm_dt": "2025-12-31"}]}
    client = _Client(major=_HANMI)

    async def total(corp_code, bsns_year, reprt_code="11011"):
        return stock_total

    client.get_stock_total = total
    p = _build(monkeypatch, client, HANMI, scope="summary", as_of_date="20260630")
    assert p["_listed_calls"] == []
    assert p["data"]["share_basis"]["comparison"] == "past_year"
    # as_of 없이는 종전 그대로 맞댄다
    p2 = _build(monkeypatch, client, HANMI, scope="summary")
    assert p2["_listed_calls"] == ["008930"]


# ── ③ 최대주주는 「본인」 행 ───────────────────────────────────────────────────

def test_top_holder_is_the_principal_row_not_the_largest_row(monkeypatch) -> None:
    """한미사이언스 2025 사업보고서: 본인 송영숙 3.38% 인데 발행회사 임원 신동국 16.43% 가 지분율 첫 행이었다."""
    rows = O._major_holders_rows(_HANMI)
    assert rows[0]["name"] == "신동국" and rows[0]["ownership_pct"] == 16.43      # 정렬 자체는 그대로(명부 표시 순서)
    top = O._top_holder_summary(rows)
    assert (top["name"], top["relation"], top["ownership_pct"]) == ("송영숙", "본인", 3.38)
    p = _build(monkeypatch, _Client(major=_HANMI), HANMI, scope="major_holders")
    assert p["data"]["summary"]["top_holder"]["name"] == "송영숙"
    assert p["data"]["major_holders"][0]["name"] == "신동국"


@pytest.mark.parametrize("label", ["본인", "최대주주 본인", "최대주주본인", "최대주주", " 본인 "])
def test_principal_labels_seen_in_real_filings(label) -> None:
    """공시 표기는 회사마다 다르다 — 「본인」(현대차·고려아연) · 「최대주주 본인」(삼성전자·LG전자) · 「최대주주」(SK하이닉스·롯데지주)."""
    rows = [
        {"name": "임원", "relation": "계열회사 임원", "shares": 9, "ownership_pct": 9.0},
        {"name": "최대", "relation": label, "shares": 1, "ownership_pct": 1.0},
    ]
    assert O._top_holder_summary(rows)["name"] == "최대"


@pytest.mark.parametrize("label", ["최대주주의 특수관계인", "친족", "계열회사", "발행회사 임원", "기타", ""])
def test_other_relations_are_not_the_principal(label) -> None:
    rows = [{"name": "가", "relation": label, "shares": 9, "ownership_pct": 9.0},
            {"name": "나", "relation": "친족", "shares": 1, "ownership_pct": 1.0}]
    assert O._top_holder_summary(rows)["name"] == "가"        # 본인 표기가 없으면 종전처럼 지분 최대 행


def test_principal_with_several_rows_takes_the_largest_of_them() -> None:
    """같은 최대주주의 행이 여럿(특별계정 등)이면 그중 지분 최대 행."""
    rows = [
        {"name": "큰회사", "relation": "최대주주 본인", "shares": 85, "ownership_pct": 8.51},
        {"name": "큰회사 (특별계정)", "relation": "최대주주 본인", "shares": 1, "ownership_pct": 0.07},
        {"name": "계열", "relation": "계열회사", "shares": 50, "ownership_pct": 5.05},
    ]
    assert O._top_holder_summary(rows)["name"] == "큰회사"


def test_top_holder_unchanged_when_principal_is_already_the_largest() -> None:
    rows = [{"name": "현대모비스", "relation": "본인", "shares": 1, "ownership_pct": 22.36},
            {"name": "정몽구", "relation": "기타", "shares": 1, "ownership_pct": 5.57}]
    assert O._top_holder_summary(rows)["name"] == "현대모비스"


def test_top_holder_empty_and_block_estimate_rows() -> None:
    assert O._top_holder_summary([]) == {}
    est = [{"name": "국민연금공단", "relation": "5% 보유자(추정)", "shares": 1, "ownership_pct": 6.1},
           {"name": "다른 보유자", "relation": "5% 보유자(추정)", "shares": 1, "ownership_pct": 5.2}]
    assert O._top_holder_summary(est)["name"] == "국민연금공단"
