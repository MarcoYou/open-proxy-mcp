"""Opt-in historical evidence boundaries at the client/cache edge; network zero."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest

from open_proxy_mcp.dart import as_of
from open_proxy_mcp.dart.client import (
    DartClient, DartClientError, _as_of_filter_rows, _doc_key, _viewer_key,
)

CUTOFF = "2025-03-26T09:00:00+09:00"
BEFORE = "20250325000001"
SAME_DAY = "20250326000001"
AFTER = "20250327000001"


@contextmanager
def strict(cutoff=CUTOFF):
    tokens = as_of.set_strict_as_of(cutoff)
    try:
        yield
    finally:
        as_of.reset_strict_as_of(tokens)


@pytest.fixture(autouse=True)
def clear_legacy_gate():
    tokens = as_of.set_as_of("")
    yield
    as_of.reset_as_of(tokens)


class MemoryCache:
    def __init__(self, values=None):
        self.values = values or {}
        self.reads = []
        self.writes = []

    def get(self, key):
        self.reads.append(key)
        return self.values.get(key)

    def put(self, key, value):
        self.writes.append((key, value))
        self.values[key] = value


def bare_client():
    client = DartClient.__new__(DartClient)
    client._search_cache = {}
    client._MAX_SEARCH_CACHE = 10
    client._doc_cache = MemoryCache()
    client._dividend_cache = MemoryCache()
    client._doc_inflight = {}
    return client


def test_strict_gate_is_opt_in_and_legacy_unknown_rows_remain_unchanged():
    payload = {"status": "000", "list": [{"bsns_year": "2024", "value": 10}]}
    assert as_of.get_strict_as_of() is None
    assert as_of.strict_cache_key() == ""
    assert as_of.strict_receipt_allowed("not-a-receipt")
    assert _as_of_filter_rows("fnlttSinglAcnt.json", payload) is payload
    tokens = as_of.set_as_of("20250325")
    try:
        assert _as_of_filter_rows("fnlttSinglAcnt.json", payload)["list"] == payload["list"]
    finally:
        as_of.reset_as_of(tokens)


@pytest.mark.parametrize("cutoff", [
    "2025-03-26", "2025-03-26T09:00:00", "2025-02-30T09:00:00+09:00",
    "2025-03-26T25:00:00+09:00", "2025-03-26T09:00:00+09:99",
    "2099-01-01T00:00:00+09:00", None,
])
def test_cutoff_requires_valid_aware_nonfuture_datetime(cutoff):
    with pytest.raises(ValueError, match="timezone-aware"):
        as_of.set_strict_as_of(cutoff)
    assert as_of.get_strict_as_of() is None


def test_kst_calendar_day_and_nested_legacy_gate_cannot_relax_strict():
    with strict("2025-03-25T20:00:00Z"):
        contract = as_of.get_strict_as_of()
        assert contract["cutoff_at"] == "2025-03-26T05:00:00+09:00"
        assert contract["effective_as_of"] == "20250325"
        assert as_of.get_as_of() == "20250325"
        assert as_of.clamp_end_de("") == "20250325"
        assert as_of.clamp_end_de("20251231") == "20250325"
        original_key = as_of.strict_cache_key()
        for legacy, expected in [("", "20250325"), ("20251231", "20250325"), ("20250301", "20250301")]:
            tokens = as_of.set_as_of(legacy)
            try:
                assert as_of.get_as_of() == expected
                if expected == "20250301":
                    assert as_of.strict_cache_key() != original_key
            finally:
                as_of.reset_as_of(tokens)
        assert as_of.strict_cache_key() == original_key
    assert as_of.get_as_of() == ""


def test_nested_strict_context_restores_cutoff_and_audit_and_child_reports_are_visible():
    async def worker():
        assert as_of.get_as_of() == "20250325"
        assert not as_of.strict_receipt_allowed(AFTER)

    with strict():
        asyncio.run(worker())
        outer = as_of.strict_exclusions()
        assert outer == [{"endpoint": "document", "receipt": AFTER, "reason": "after_cutoff", "count": 1}]
        with strict("2025-03-01T00:00:00+09:00"):
            assert as_of.strict_exclusions() == []
            assert as_of.get_as_of() == "20250228"
        assert as_of.strict_exclusions() == outer
        outer[0]["count"] = 900
        assert as_of.strict_exclusions()[0]["count"] == 1
    assert as_of.strict_exclusions() == []


def test_concurrent_requests_do_not_share_cutoffs_or_exclusion_ledgers():
    async def request(cutoff, expected, receipt):
        with strict(cutoff):
            await asyncio.sleep(0)
            assert as_of.get_as_of() == expected
            assert not as_of.strict_receipt_allowed(receipt)
            await asyncio.sleep(0)
            return as_of.strict_exclusions()

    async def run():
        return await asyncio.gather(
            request(CUTOFF, "20250325", SAME_DAY),
            request("2025-02-01T09:00:00+09:00", "20250131", "20250201000001"),
        )

    results = asyncio.run(run())
    assert [row[0]["receipt"] for row in results] == [SAME_DAY, "20250201000001"]
    assert all(len(row) == 1 for row in results)
    assert as_of.get_strict_as_of() is None


def test_json_rows_require_calendar_publication_and_reject_conflicting_dates():
    payload = {"status": "000", "total_page": 3, "current_ceo": "current-only", "list": [
        {"tag": "date", "rcept_dt": "2025-03-25"},
        {"tag": "receipt", "rcept_no": BEFORE},
        {"tag": "both", "rcept_dt": "20250325", "rcept_no": BEFORE},
        {"tag": "same", "rcept_dt": "20250326"},
        {"tag": "future", "rcept_no": AFTER},
        {"tag": "invalid", "rcept_dt": "20250230", "rcept_no": BEFORE},
        {"tag": "conflict", "rcept_dt": "20250324", "rcept_no": BEFORE},
        {"tag": "missing", "bsns_year": "2024"},
    ]}
    with strict():
        result = _as_of_filter_rows("fnlttSinglAcnt.json", payload)
        assert [row["tag"] for row in result["list"]] == ["date", "receipt", "both"]
        assert result["total_page"] == 3
        assert "current_ceo" not in result
        assert {row["reason"] for row in as_of.strict_exclusions()} == {
            "after_cutoff", "publication_invalid", "publication_date_conflict", "publication_unknown",
        }
    assert len(payload["list"]) == 8
    assert "current_ceo" in payload


def test_scalar_api_facts_are_not_historical_identity_metadata():
    company = {"status": "000", "corp_name": "Example", "corp_code": "00100000", "stock_code": "100000",
               "ceo_nm": "current CEO", "acc_mt": "12", "induty_code": "123"}
    with strict():
        assert _as_of_filter_rows("company.json", company) == {
            "status": "000", "corp_name": "Example", "corp_code": "00100000", "stock_code": "100000",
        }
        assert _as_of_filter_rows("unknown.json", {"status": "000", "current_value": 900}) == {
            "status": "000", "list": [],
        }


def test_request_filters_json_and_overrides_latest_only_before_http():
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "000", "list": [{"rcept_no": BEFORE}, {"rcept_no": AFTER}, {"value": 7}]}

    client = bare_client()
    client._request_counter = 0
    client.api_key = "unit-test-placeholder"
    client._throttle_api = AsyncMock()
    client._http = type("Http", (), {"get": AsyncMock(return_value=Response())})()
    params = {"bgn_de": "20250101", "end_de": "20251231", "last_reprt_at": "Y"}
    with strict():
        result = asyncio.run(client._request("list.json", params))
    assert result["list"] == [{"rcept_no": BEFORE}]
    sent = client._http.get.call_args.kwargs["params"]
    assert sent["end_de"] == "20250325"
    assert sent["last_reprt_at"] == "N"
    assert "crtfc_key" not in params  # strict request does not mutate caller inputs


def test_search_rechecks_legacy_cache_and_does_not_cache_filtered_partial_result():
    client = bare_client()
    cached = {"status": "000", "list": [{"rcept_no": BEFORE}, {"rcept_no": AFTER}, {"bsns_year": "2024"}]}
    key = "00100000|20250101|20250325|||N"
    client._search_cache[key] = (cached, None)
    client._request = AsyncMock(return_value=cached)
    with strict():
        result = asyncio.run(client.search_filings("20250101", "20251231", corp_code="00100000", last_reprt_at="Y"))
        assert result["list"] == [{"rcept_no": BEFORE}]
        client._request.assert_not_called()
        client._search_cache.clear()
        result = asyncio.run(client.search_filings("20250101", "20251231", corp_code="00100000", last_reprt_at="Y"))
        assert result["list"] == [{"rcept_no": BEFORE}]
        assert client._request.call_args.args[1]["last_reprt_at"] == "N"
        assert client._search_cache == {}
    assert len(cached["list"]) == 3


def test_dividend_global_cache_is_rechecked_without_poisoning_legacy_cache():
    client = bare_client()
    cached = {"status": "000", "list": [{"rcept_no": BEFORE, "value": 1}, {"rcept_no": AFTER, "value": 2}]}
    client._dividend_cache = MemoryCache({"00100000|2000|11011": cached})
    client._request = AsyncMock(return_value=cached)
    with strict():
        result = asyncio.run(client.get_dividend_info("00100000", "2000"))
        assert result["list"] == [{"rcept_no": BEFORE, "value": 1}]
        client._request.assert_not_called()
        client._dividend_cache.values.clear()
        result = asyncio.run(client.get_dividend_info("00100000", "2000"))
        assert result["list"] == [{"rcept_no": BEFORE, "value": 1}]
        assert client._dividend_cache.writes == []
    assert len(cached["list"]) == 2


@pytest.mark.parametrize("receipt", [SAME_DAY, AFTER, "20250230000001", "", "invalid"])
def test_document_cache_checks_before_memory_disk_or_inflight(receipt):
    client = bare_client()
    client._doc_cache = MemoryCache({_doc_key(receipt): {"text": "not available at cutoff"}})
    disk_reads = []
    client._load_from_disk = lambda rc: disk_reads.append(rc)
    with strict(), pytest.raises(DartClientError, match="판단 기준시점"):
        asyncio.run(client.get_document_cached(receipt))
    assert client._doc_cache.reads == []
    assert disk_reads == []


def test_pre_cutoff_xml_cache_can_be_reused():
    client = bare_client()
    doc = {"text": "fixed public filing"}
    client._doc_cache = MemoryCache({_doc_key(BEFORE): doc})
    with strict():
        assert asyncio.run(client.get_document_cached(BEFORE)) is doc


def test_direct_xml_and_binary_entrypoints_cannot_bypass_cutoff():
    client = bare_client()
    client._request_binary = AsyncMock()
    with strict(), pytest.raises(DartClientError):
        asyncio.run(client.get_document(AFTER))
    client._request_binary.assert_not_called()
    # Call the class entrypoint to exercise the real binary guard as well.
    with strict(), pytest.raises(DartClientError):
        asyncio.run(DartClient._request_binary(client, "document.xml", {"rcept_no": AFTER}))


def test_identity_binary_is_allowed_but_other_undated_binary_is_blocked():
    class Response:
        content = b"PK-public-identity-fixture"

        def raise_for_status(self):
            pass

    client = bare_client()
    client.api_key = "unit-test-placeholder"
    client._throttle_api = AsyncMock()
    client._http = type("Http", (), {"get": AsyncMock(return_value=Response())})()
    with strict():
        assert asyncio.run(client._request_binary("corpCode.xml", {})).startswith(b"PK")
        with pytest.raises(DartClientError):
            asyncio.run(client._request_binary("unknown.xml", {}))
    assert client._http.get.call_count == 1


def test_unversioned_viewer_caches_and_private_entrypoints_are_excluded():
    client = bare_client()
    client._doc_cache = MemoryCache({_viewer_key(BEFORE, ()): {"text": "unproven viewer aggregate"}})
    with strict():
        for method, args in [
            (client.get_viewer_document, (BEFORE,)),
            (client._fetch_viewer_main_html, (BEFORE,)),
            (client._fetch_viewer_section_html, ({"rcpNo": BEFORE},)),
        ]:
            with pytest.raises(DartClientError):
                asyncio.run(method(*args))
        assert any(row["reason"] == "unversioned_source" for row in as_of.strict_exclusions())
    assert client._doc_cache.reads == []


def test_unversioned_naver_and_dynamic_kind_do_not_fetch():
    client = bare_client()  # no HTTP transport exists; a fetch would fail the test
    with strict():
        assert asyncio.run(client.get_naver_corp_profile("100000")) == {}
        assert asyncio.run(client.naver_news_search("Example")) == []
        assert asyncio.run(client._naver_stock_price("100000", "20250325")) is None
        assert asyncio.run(client.get_stock_price("100000", "20250326")) is None
        assert asyncio.run(client.get_stock_price("100000", "2025-03-26")) is None
        with pytest.raises(DartClientError):
            asyncio.run(client.kind_fetch_document(BEFORE))


def test_krx_returned_quote_must_have_the_requested_pre_cutoff_day(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {"OutBlock_1": [
                {"ISU_CD": "100000", "TDD_CLSPRC": "900", "BAS_DD": "20250326"},
                {"ISU_CD": "100000", "TDD_CLSPRC": "800"},
                {"ISU_CD": "100000", "TDD_CLSPRC": "700", "BAS_DD": "20250325"},
            ]}

    from open_proxy_mcp.dart import krx_meter
    monkeypatch.setattr(krx_meter, "bump", lambda: None)
    monkeypatch.setenv("KRX_API_KEY", "unit-test-placeholder")
    client = bare_client()
    client._throttle_api = AsyncMock()
    client._http = type("Http", (), {"get": AsyncMock(return_value=Response())})()
    with strict():
        assert asyncio.run(client._krx_stock_price("100000", "20250325")) == {
            "closing_price": 700, "base_date": "20250325", "source": "krx",
        }
        assert {item["reason"] for item in as_of.strict_exclusions()} == {
            "publication_date_conflict", "publication_unknown",
        }


def test_kind_search_excludes_unverified_dates_and_clamps_request():
    class Response:
        text = "mocked public listing"
        status_code = 200

        def raise_for_status(self):
            pass

    client = bare_client()
    client._throttle_kind = AsyncMock()
    client._http = type("Http", (), {"post": AsyncMock(return_value=Response())})()
    client._parse_kind_disclosure_rows = lambda _: [
        {"acptno": BEFORE, "disclosure_date": "20250325"},
        {"acptno": SAME_DAY, "disclosure_date": "20250326"},
        {"acptno": "opaque", "disclosure_date": ""},
    ]
    with strict():
        result = asyncio.run(client.kind_search_disclosures(stock_code="100000", corp_name="Example",
            from_date="2025-01-01", to_date="2025-12-31", disclosure_type_code="0184"))
    assert result == [{"acptno": BEFORE, "disclosure_date": "20250325"}]
    assert client._http.post.call_args.kwargs["data"]["toDate"] == "2025-03-25"


def test_exclusion_log_contains_only_safe_metadata_and_no_submitted_text():
    with strict():
        assert not as_of.strict_receipt_allowed("arbitrary submitted content")
        as_of.note_strict_exclusion("https://example.test?token=sensitive", reason="unsafe submitted reason")
        assert all(set(item) == {"endpoint", "receipt", "reason", "count"} for item in as_of.strict_exclusions())
        assert "sensitive" not in str(as_of.strict_exclusions())
        assert "submitted" not in str(as_of.strict_exclusions())
