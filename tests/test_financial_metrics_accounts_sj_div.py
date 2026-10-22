"""financial_metrics scope=accounts — sj_div 캐시 키 · 단일 포괄손익계산서(CIS) 공시 회사의 IS 필터 · 필터 뒤 0행 고지. network 0 · DB 0.

SK하이닉스(00164779) 2025 사업보고서 연결(CFS) fnlttSinglAcntAll 는 손익계산서(IS) 없이 포괄손익계산서(CIS) 하나로 공시한다
(230행 = BS 54 · CIS 27 · CF 37 · SCE 112). 옛 코드는 sj_div=["IS"] 를 원행 sj_div 와 글자 그대로 맞춰 0행을 내고 status 를 exact 로 두었으며,
그 0행이 캐시 키(sj_div 없음)에 굳어 sj_div 없이 부른 호출도 0행을 받았다. 삼성전자(00126380)는 IS 를 따로 낸다(229행 = BS 52 · IS 17 · CIS 13 · CF 42 · SCE 105).
픽스처 두 개는 DART 실응답(배치 키, 2026-10-23)이다. 40자 이상인 IFRS 요소명 `account_id` 는 저장소 비밀값 검사(긴 토큰 규칙)에 걸려
앞 39자로 줄였다 — 다른 칸은 그대로이고 이 시험은 `account_id` 를 읽지 않는다.

고침 전에 실패하는 시험: 캐시 키·CIS 대체·필터 뒤 0행 고지·필터 전 0행 미캐시. 삼성전자 IS 필터 결과가 같다는 시험은 고치기 전·후 모두 통과한다.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import financial_metrics as FM
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus

_FIX = Path(__file__).parent / "dart_responses" / "dart"
_SKH = json.loads((_FIX / "skhynix_000660_00164779_2025_11011_acnt_all_cfs_full.json").read_text(encoding="utf-8"))["list"]
_SEC = json.loads((_FIX / "samsung_005930_00126380_2025_11011_acnt_all_cfs_full.json").read_text(encoding="utf-8"))["list"]

_CO = {"corp_code": "00164779", "corp_name": "SK하이닉스", "stock_code": "000660"}


def _run(monkeypatch, rows: list[dict] | None, calls: list | None = None, **kw):
    """accounts 응답을 만든다 — DART 호출은 픽스처 행으로 대신한다."""
    calls = calls if calls is not None else []
    monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")

    async def resolve(query, *a, **k):
        return CompanyResolution(status=AnalysisStatus.EXACT, query=query, selected=dict(_CO), candidates=[])

    async def info(cc):
        return {"acc_mt": "12", "induty_code": "26110"}, None

    async def acnt_all(cc, y, rc, fs):
        calls.append((y, rc, fs))
        return (list(rows), None) if rows else ([], "no_filing")

    async def none(*a, **k):
        return None

    monkeypatch.setattr(FM, "resolve_company_query", resolve)
    monkeypatch.setattr(FM, "_safe_company_info", info)
    monkeypatch.setattr(FM, "_safe_fetch_acnt_all", acnt_all)
    monkeypatch.setattr(FM, "_periodic_filing_ref", none)
    return calls


def _fresh_cache(monkeypatch, name: str = "test-fm-accounts-sj"):
    monkeypatch.setattr(FM, "_FM_CACHE", type(FM._FM_CACHE)(16 * 1024 * 1024, 300.0, name))


def _accounts(**kw) -> dict:
    p = asyncio.run(FM.build_financial_metrics_payload("000660", scope="accounts", year=2025, **kw))
    return p


def test_same_company_year_with_different_sj_div_gets_different_responses(monkeypatch):
    """① 캐시 키에 sj_div 가 있다 — BS 만 부른 응답이 이어진 호출(전체·IS)에 굳어 나가지 않는다."""
    calls = _run(monkeypatch, _SEC)
    _fresh_cache(monkeypatch)
    bs = _accounts(sj_div=["BS"])["data"]["accounts"]
    full = _accounts()["data"]["accounts"]
    is_only = _accounts(sj_div=["IS"])["data"]["accounts"]
    assert bs["row_count"] == 52 and {r["sj_div"] for r in bs["rows"]} == {"BS"}
    assert full["row_count"] == 229
    assert is_only["row_count"] == 17 and {r["sj_div"] for r in is_only["rows"]} == {"IS"}
    assert len(calls) == 3  # 서로 다른 요청이라 세 번 다 DART 를 부른다


def test_sj_div_is_normalised_in_the_cache_key(monkeypatch):
    """대소문자·순서·중복이 달라도 같은 요청은 같은 항목, None 과 빈 목록은 둘 다 「필터 없음」이다."""
    calls = _run(monkeypatch, _SEC)
    _fresh_cache(monkeypatch)
    a = _accounts(sj_div=["is", "BS"])
    b = _accounts(sj_div=["BS", "IS", "bs"])
    assert a["data"]["accounts"]["row_count"] == b["data"]["accounts"]["row_count"] == 52 + 17
    assert len(calls) == 1
    _accounts()
    _accounts(sj_div=[])
    assert len(calls) == 2


def test_sj_div_does_not_split_the_cache_key_of_other_scopes(monkeypatch):
    """sj_div 는 accounts 전용이다 — 다른 scope 의 캐시 키는 sj_div 와 무관하고, accounts 만 정규화한 sj_div 를 키에 담는다."""
    keys: list = []

    def spy(key):
        keys.append(key)
        return {"tool": "financial_metrics", "status": "exact", "warnings": [], "data": {}}  # 캐시 적중으로 바로 돌려준다

    monkeypatch.setattr(FM, "_fm_cache_get", spy)
    for scope in ("summary", "yearly", "yoy"):
        asyncio.run(FM.build_financial_metrics_payload("000660", scope=scope, year=2025, sj_div=["IS"]))
        asyncio.run(FM.build_financial_metrics_payload("000660", scope=scope, year=2025, sj_div=["BS"]))
    assert keys[0] == keys[1] and keys[2] == keys[3] and keys[4] == keys[5]
    keys.clear()
    asyncio.run(FM.build_financial_metrics_payload("000660", scope="accounts", year=2025, sj_div=["IS"]))
    asyncio.run(FM.build_financial_metrics_payload("000660", scope="accounts", year=2025, sj_div=["BS"]))
    assert keys[0] != keys[1]


def test_single_statement_filer_is_filter_returns_cis_rows_with_mapping_and_warning(monkeypatch):
    """② SK하이닉스형(IS 없이 CIS 하나) — sj_div=["IS"] 는 CIS 27행 + sj_div_mapped + 경고."""
    _run(monkeypatch, _SKH)
    _fresh_cache(monkeypatch)
    p = _accounts(sj_div=["IS"])
    acc = p["data"]["accounts"]
    assert acc["row_count"] == 27
    assert {r["sj_div"] for r in acc["rows"]} == {"CIS"}  # 행의 sj_div 는 원문 그대로
    assert acc["sj_div_mapped"] == {"IS": "CIS"}
    assert any("손익계산서(IS)" in w and "포괄손익계산서(CIS)" in w for w in p["warnings"])


def test_single_statement_filer_without_filter_returns_all_rows_not_the_cached_empty_one(monkeypatch):
    """결정 파일의 재현 순서 — IS 필터 다음 필터 없음: 230행(앞 호출의 캐시가 아님)."""
    calls = _run(monkeypatch, _SKH)
    _fresh_cache(monkeypatch)
    _accounts(sj_div=["IS"])
    full = _accounts()["data"]["accounts"]
    assert full["row_count"] == 230
    assert "sj_div_mapped" not in full
    assert len(calls) == 2


def test_is_and_cis_both_requested_does_not_duplicate_rows(monkeypatch):
    """sj_div=["IS","CIS"] 는 CIS 를 이미 요청한 것 — 행을 겹쳐 내지 않고 대체 표시도 달지 않는다."""
    _run(monkeypatch, _SKH)
    _fresh_cache(monkeypatch)
    acc = _accounts(sj_div=["IS", "CIS"])["data"]["accounts"]
    assert acc["row_count"] == 27
    assert len({(r["ord"], r["account_nm"]) for r in acc["rows"]}) == 27
    assert "sj_div_mapped" not in acc


def test_cis_request_on_is_only_report_is_not_substituted(monkeypatch):
    """반대 방향(CIS 요청인데 IS 만 있음)은 대체하지 않는다 — 포괄손익 항목이 없을 수 있다."""
    rows = [{"sj_div": "BS", "account_nm": "자산총계", "ord": "1", "thstrm_amount": "10"},
            {"sj_div": "IS", "account_nm": "매출액", "ord": "2", "thstrm_amount": "5"}]
    _run(monkeypatch, rows)
    _fresh_cache(monkeypatch)
    p = _accounts(sj_div=["CIS"])
    acc = p["data"]["accounts"]
    assert acc["row_count"] == 0 and acc["rows"] == []
    assert "sj_div_mapped" not in acc


def test_filter_to_zero_rows_reports_the_pre_filter_composition(monkeypatch):
    """③ 필터 뒤 0행·필터 전 행 있음 — 경고와 sj_div_present 에 필터 전 구성이 나와 「자료 없음」으로 읽히지 않는다."""
    rows = [{"sj_div": "BS", "account_nm": "자산총계", "ord": "1", "thstrm_amount": "10"},
            {"sj_div": "IS", "account_nm": "매출액", "ord": "2", "thstrm_amount": "5"},
            {"sj_div": "CF", "account_nm": "영업활동", "ord": "3", "thstrm_amount": "1"}]
    _run(monkeypatch, rows)
    _fresh_cache(monkeypatch)
    p = _accounts(sj_div=["CIS"])
    acc = p["data"]["accounts"]
    assert acc["row_count"] == 0
    assert acc["sj_div_present"] == ["BS", "IS", "CF"]
    joined = " ".join(p["warnings"])
    assert "BS·IS·CF" in joined and "CIS" in joined and "자료가 없는 것이 아니라" in joined


def test_sj_div_present_is_the_pre_filter_composition_but_rows_are_unchanged(monkeypatch):
    """삼성전자형 IS 필터 — rows·row_count 는 그대로(IS 17행, 대체 없음), sj_div_present 는 필터 전 구성(의도된 변경)."""
    _run(monkeypatch, _SEC)
    _fresh_cache(monkeypatch)
    p = _accounts(sj_div=["IS"])
    acc = p["data"]["accounts"]
    assert acc["row_count"] == 17 and {r["sj_div"] for r in acc["rows"]} == {"IS"}
    assert "sj_div_mapped" not in acc
    assert acc["sj_div_present"] == ["BS", "IS", "CIS", "CF", "SCE"]
    assert not any("포괄손익계산서(CIS)" in w for w in p["warnings"])


def test_samsung_is_filter_rows_are_the_original_is_rows(monkeypatch):
    """고치기 전·후 모두 통과해야 하는 시험 — IS 가 있는 회사의 IS 필터 행은 원문 IS 행과 정확히 같다."""
    _run(monkeypatch, _SEC)
    _fresh_cache(monkeypatch)
    acc = _accounts(sj_div=["IS"])["data"]["accounts"]
    want = [r for r in _SEC if r["sj_div"] == "IS"]
    assert [r["account_nm"] for r in acc["rows"]] == [r["account_nm"] for r in sorted(want, key=lambda r: int(r["ord"]))]
    assert acc["row_count"] == 17


def test_renderer_draws_only_groups_with_rows_in_pre_filter_order(monkeypatch):
    """md 렌더러는 sj_div_present 순서로 그리고 행 없는 묶음은 건너뛴다 — 필터 전 구성을 담아도 표는 그대로다."""
    from open_proxy_mcp.tools.financial_metrics import _render

    _run(monkeypatch, _SKH)
    _fresh_cache(monkeypatch)
    md = _render(_accounts(sj_div=["IS"]))
    assert "### 포괄손익계산서 (CIS) — 27행" in md
    assert "재무상태표 (BS)" not in md and "현금흐름표 (CF)" not in md
    assert "포괄손익계산서(CIS) 하나로 공시" in md  # 대체 사실이 경고로 md 에 실린다


def test_renderer_tells_filtered_out_apart_from_not_received(monkeypatch):
    """필터가 다 걸러낸 0행은 「받지 못했다」가 아니라 필터 전 구성을 말한다."""
    from open_proxy_mcp.tools.financial_metrics import _render

    rows = [{"sj_div": "BS", "account_nm": "자산총계", "ord": "1", "thstrm_amount": "10"}]
    _run(monkeypatch, rows)
    _fresh_cache(monkeypatch)
    md = _render(_accounts(sj_div=["CF"]))
    assert "받지 못했습니다" not in md
    assert "해당하는 계정 행이 없습니다" in md and "이 보고서의 구성: BS" in md


def test_pre_filter_empty_accounts_response_is_not_cached(monkeypatch):
    """④ 필터 전 행이 0 인 응답(모든 폴백에서 행을 못 받음)은 캐시에 넣지 않는다 — 일시 오류·한도 초과가 5분 굳지 않는다."""
    calls = _run(monkeypatch, None)
    _fresh_cache(monkeypatch)
    _accounts()
    n = len(calls)
    assert n >= 1
    _accounts()
    assert len(calls) == 2 * n  # 두 번째도 DART 를 다시 부른다(캐시 적중이 아님)


def _fake_client(monkeypatch, handler):
    class _C:
        def api_call_snapshot(self):
            return 0

        async def get_fnltt_singl_acnt_all(self, corp_code, year, reprt_code, fs_div):
            return handler(reprt_code, fs_div)

    monkeypatch.setattr(FM, "get_dart_client", lambda: _C())


def test_status_000_with_empty_list_is_not_cached(monkeypatch):
    """응답이 000 인데 list 가 비어 있는 꼴도 캐시에 들어가지 않는다."""
    monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")
    _run(monkeypatch, None)  # 회사 식별·보고서 참조만 대신하고
    seen: list = []

    def handler(rc, fs):
        seen.append((rc, fs))
        return {"status": "000", "message": "정상", "list": []}

    _fake_client(monkeypatch, handler)
    # _safe_fetch_acnt_all 는 _run 이 가짜로 바꿨으니 진짜 구현으로 되돌려 클라이언트 경로를 탄다.
    monkeypatch.setattr(FM, "_safe_fetch_acnt_all", _REAL_SAFE_FETCH_ALL)
    _fresh_cache(monkeypatch)
    _accounts()
    n = len(seen)
    assert n >= 1
    _accounts()
    assert len(seen) == 2 * n


def test_dart_failure_is_status_error_and_not_cached(monkeypatch):
    """DART 조회 실패(013 이 아닌 오류)는 status=error 로 나가고 캐시에 넣지 않는다(#200 현행 동작을 고정)."""
    _run(monkeypatch, None)
    seen: list = []

    def handler(rc, fs):
        seen.append((rc, fs))
        raise DartClientError("800", "시스템 점검")

    _fake_client(monkeypatch, handler)
    monkeypatch.setattr(FM, "_safe_fetch_acnt_all", _REAL_SAFE_FETCH_ALL)
    _fresh_cache(monkeypatch)
    p = _accounts()
    assert p["status"] in ("error", AnalysisStatus.ERROR)
    n = len(seen)
    _accounts()
    assert len(seen) == 2 * n


_REAL_SAFE_FETCH_ALL = FM._safe_fetch_acnt_all
