import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services import value_up_v2 as vu
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus


class FakeClient:
    def __init__(self, docs=None):
        self.calls = 0
        self.docs = docs or {}

    def api_call_snapshot(self):
        return self.calls

    async def get_document_cached(self, rcept_no):
        self.calls += 1
        return {"text": self.docs.get(rcept_no, "")}


def _resolution():
    return CompanyResolution(
        status=AnalysisStatus.EXACT,
        query="삼성전자",
        selected={
            "corp_name": "삼성전자",
            "stock_code": "005930",
            "corp_code": "00126380",
        },
        candidates=[],
    )


async def _fake_resolve(_query):
    return _resolution()


async def _fake_search_value_up_items(*_args, **_kwargs):
    return [], [], None


async def _fake_search_kind_value_up_items(*_args, **_kwargs):
    return [], None


def test_value_up_exposes_search_stage_timings(monkeypatch):
    monkeypatch.setattr(vu, "get_dart_client", lambda: FakeClient())
    monkeypatch.setattr(vu, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(vu, "_search_value_up_items", _fake_search_value_up_items)
    monkeypatch.setattr(vu, "_search_kind_value_up_items", _fake_search_kind_value_up_items)

    payload = asyncio.run(vu.build_value_up_payload("삼성전자", scope="summary", year=2025))

    timings = payload["data"]["timings_ms"]
    assert "dart_search.requested_window" in timings
    assert "kind_search.requested_window" in timings
    assert "diagnostic_search.dart" in timings
    assert "diagnostic_search.kind" in timings
    assert "diagnostic_search" in timings


def test_value_up_payload_exposes_plan_status_and_nullable_result(monkeypatch):
    docs = {
        "202511270001": """
        1. 계획서 명칭 2025년 테스트 기업가치 제고 계획 이행현황
        2. 주요 내용 1) 2025년 이행현황 - ROE 8.5%로 개선
        3. 결정일자 2025-11-27
        """,
        "202411270001": """
        1. 계획서 명칭 2024년 테스트 기업가치 제고 계획
        2. 주요 내용 1) 중장기 목표 - ROE 10% 이상 목표
        3. 결정일자 2024-11-27
        """,
    }
    items = [
        {
            "rcept_no": "202511270001",
            "rcept_dt": "20251127",
            "report_nm": "기업가치제고계획(자율공시)",
            "flr_nm": "테스트",
        },
        {
            "rcept_no": "202411270001",
            "rcept_dt": "20241127",
            "report_nm": "기업가치제고계획(자율공시)",
            "flr_nm": "테스트",
        },
    ]

    async def fake_search(*_args, **_kwargs):
        return items, [], None

    monkeypatch.setattr(vu, "get_dart_client", lambda: FakeClient(docs))
    monkeypatch.setattr(vu, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(vu, "_search_value_up_items", fake_search)
    monkeypatch.setattr(vu, "_search_kind_value_up_items", _fake_search_kind_value_up_items)

    payload = asyncio.run(vu.build_value_up_payload("삼성전자", scope="summary", year=2025))
    data = payload["data"]

    assert data["latest"]["category"] == "progress"
    assert data["latest_plan"]["rcept_no"] == "202411270001"
    assert data["latest_status"]["rcept_no"] == "202511270001"
    assert data["latest_status"]["implementation_sections"][0]["tag"] == "implementation_status"
    assert data["latest_result"] is None


def test_value_up_payload_promotes_explicit_result_when_found(monkeypatch):
    docs = {
        "202603270001": """
        1. 계획서 명칭 2026년 테스트 기업가치 제고 계획 이행현황
        2. 주요 내용 ※(참고) 주주환원 이행결과 - [주당배당금] 6,000원 - 총주주환원율 108.9%
        3. 결정일자 2026-03-27
        """,
        "202411270001": """
        1. 계획서 명칭 2024년 테스트 기업가치 제고 계획
        2. 주요 내용 1) 중장기 목표 - ROE 10% 이상 목표
        3. 결정일자 2024-11-27
        """,
    }
    items = [
        {
            "rcept_no": "202603270001",
            "rcept_dt": "20260327",
            "report_nm": "기업가치제고계획(자율공시)              (고배당기업 표시를 위한 재공시)",
            "flr_nm": "테스트",
        },
        {
            "rcept_no": "202411270001",
            "rcept_dt": "20241127",
            "report_nm": "기업가치제고계획(자율공시)",
            "flr_nm": "테스트",
        },
    ]

    async def fake_search(*_args, **_kwargs):
        return items, [], None

    monkeypatch.setattr(vu, "get_dart_client", lambda: FakeClient(docs))
    monkeypatch.setattr(vu, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(vu, "_search_value_up_items", fake_search)
    monkeypatch.setattr(vu, "_search_kind_value_up_items", _fake_search_kind_value_up_items)

    payload = asyncio.run(vu.build_value_up_payload("삼성전자", scope="summary", year=2026))
    data = payload["data"]

    assert data["meta_amendment"]["rcept_no"] == "202603270001"
    assert data["latest_plan"]["rcept_no"] == "202411270001"
    assert data["latest_status"] is None
    assert data["latest_result"]["rcept_no"] == "202603270001"
    assert data["latest_result"]["implementation_sections"][0]["tag"] == "implementation_result"


def test_value_up_backfills_plan_and_status_when_requested_window_only_has_meta(monkeypatch):
    docs = {
        "202603270001": """
        1. 계획서 명칭 2026년 테스트 기업가치 제고 계획(고배당기업 여부)
        2. 주요 내용 - 조세특례제한법 제104조의27에 따른 고배당기업 요건 충족 공시
        3. 조세특례제한법 제104조의27에 따른 고배당기업 여부 해당
        """,
        "202504250001": """
        1. 계획서 명칭 2025년 테스트 기업가치 제고 계획 이행현황
        2. 주요 내용 1) 2025년 이행현황 - 주주환원율 40%
        3. 결정일자 2025-04-25
        """,
        "202410240001": """
        1. 계획서 명칭 2024년 테스트 기업가치 제고 계획
        2. 주요 내용 1) 중장기 목표 - 주주환원율 50%
        3. 결정일자 2024-10-24
        """,
    }
    meta = {
        "rcept_no": "202603270001",
        "rcept_dt": "20260327",
        "report_nm": "기업가치제고계획(자율공시)              (고배당기업 표시를 위한 재공시)",
        "flr_nm": "테스트",
    }
    backfill_items = [
        meta,
        {
            "rcept_no": "202504250001",
            "rcept_dt": "20250425",
            "report_nm": "기업가치제고계획(자율공시)              (이행현황)",
            "flr_nm": "테스트",
        },
        {
            "rcept_no": "202410240001",
            "rcept_dt": "20241024",
            "report_nm": "기업가치제고계획(자율공시)",
            "flr_nm": "테스트",
        },
    ]

    async def fake_search(*_args, **kwargs):
        if kwargs["bgn_de"].startswith("2026"):
            return [meta], [], None
        return backfill_items, [], None

    monkeypatch.setattr(vu, "get_dart_client", lambda: FakeClient(docs))
    monkeypatch.setattr(vu, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(vu, "_search_value_up_items", fake_search)
    monkeypatch.setattr(vu, "_search_kind_value_up_items", _fake_search_kind_value_up_items)

    payload = asyncio.run(vu.build_value_up_payload("삼성전자", scope="summary", year=2026))
    data = payload["data"]

    assert data["meta_amendment"]["rcept_no"] == "202603270001"
    assert data["latest_status"]["rcept_no"] == "202504250001"
    assert data["latest_plan"]["rcept_no"] == "202410240001"
    assert "role_backfill_search.dart" in data["timings_ms"]


def test_value_up_uses_plan_title_to_classify_progress_update():
    text = """
    기업가치 제고 계획(자율공시)
    1. 계획서 명칭 2025년 KT G 기업가치 제고계획 이행현황
    2. 주요 내용 [2025년 KT G 기업가치 제고계획 이행현황]
    1. 수익성 턴어라운드
    3. 결정일자 2025-09-23
    """

    assert vu._extract_plan_title(text) == "2025년 KT G 기업가치 제고계획 이행현황"
    assert vu._classify_value_up_item("기업가치제고계획(자율공시)", plan_title=vu._extract_plan_title(text)) == "progress"


def test_value_up_classifies_pre_announcement_separately():
    assert vu._classify_value_up_item("기업가치제고계획예고(안내공시)") == "pre_announcement"
    assert vu._classify_value_up_item("기업가치제고계획예고") == "pre_announcement"


def test_value_up_tags_implementation_status_outlook_and_future_plan():
    text = """
    2. 주요 내용 [2025년 KT G 기업가치 제고계획 이행현황]
    1) '24년 주주환원 이행현황 - [주당배당금] 5,400원 - [자사주매입] 5,467억원
    2) '25년 주주환원 이행전망 - [주당배당금] 최소 6,000원 - TSR 100%+ 예상
    3) 주주환원 배분원칙 Upgrade ('24년-'27년) - 현금환원 3.7조원+a 및 TSR 100%+
    3. 결정일자 2025-09-23
    """

    sections = vu._extract_implementation_sections(text)

    assert [section["tag"] for section in sections] == [
        "implementation_status",
        "implementation_outlook",
        "future_plan",
    ]
    assert "5,467억원" in sections[0]["text"]
    assert "TSR 100%+ 예상" in sections[1]["text"]


def test_value_up_tags_high_dividend_republication_embedded_results():
    text = """
    1. 계획서 명칭 2025년 KT G 기업가치 제고계획 이행현황
    2. 주요 내용 - 조세특례제한법 제104조의27에 따른 고배당기업 요건 충족 공시
    - 기존 기업가치제고계획 및 이행현황은 2025.09.23 공시한 '기업가치 제고 계획(자율공시)' 참조
    ※(참고)'25년 주주환원 이행결과 - [주당배당금] 6,000원 - 총주주환원율 108.9%
    3. 조세특례제한법 제104조의27에 따른 고배당기업 여부 해당
    """

    sections = vu._extract_implementation_sections(text)

    assert vu._classify_value_up_item(
        "기업가치제고계획(자율공시)(고배당기업 표시를 위한 재공시)",
        plan_title=vu._extract_plan_title(text),
    ) == "meta_amendment"
    assert [section["tag"] for section in sections] == [
        "meta_reference",
        "meta_reference",
        "implementation_result",
    ]
    assert "108.9%" in sections[2]["text"]


def test_value_up_tags_only_observed_status_wording_variants():
    assert vu._tag_implementation_unit("1. 2025년 이행현황 - ROE 개선") == "implementation_status"
    assert vu._tag_implementation_unit("1. 2025년 이행 현황 - ROE 개선") == "implementation_status"
    assert vu._tag_implementation_unit("※ 상세 이행내역 첨부파일 참조") == "meta_reference"
    assert vu._tag_implementation_unit("[Value-up Program 이행 내역] 주주 친화 정책 실행") == "implementation_status"
    assert vu._tag_implementation_unit("2. 진행 현황 ① 재무 현황 ② 주주환원 현황") == "implementation_status"


def test_value_up_does_not_tag_unobserved_progress_wording_variants():
    assert vu._tag_implementation_unit("※ 주주환원 이행 결과 - 배당 지급") is None
    assert vu._tag_implementation_unit("2. 진행현황 ① 재무 현황") is None
    assert vu._classify_value_up_item(
        "기업가치제고계획(자율공시)",
        plan_title="2025년 회사 기업가치 제고 계획 진행현황",
    ) == "plan"
