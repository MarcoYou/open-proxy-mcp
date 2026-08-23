# -*- coding: utf-8 -*-
"""정기보고서 명부 동봉본 — **배포 직후 명부가 비는 창을 없앤다**. network 0콜.

260823 사고: 명부를 요청 경로에서 만들다(183콜·약 3분) 프록시 타임아웃에 걸려 502.
끊기니 저장을 못 해 다음 요청도 같은 3분을 반복하는 영영 안 낫는 고리가 됐다.
백그라운드로 옮겨(2c228105) 502 는 막았지만 **배포 직후 명부가 비는 창**은 남았다 —
그동안 비상장 금융사가 안 열리고 동명 법인이 AMBIGUOUS 로 남는다.

동봉본이 그 창을 없앤다. 해결 순서: 메모리 → sqlite → **동봉본** → 백그라운드 수집.
"""
from __future__ import annotations

import inspect
import json

from importlib.resources import files

import open_proxy_mcp.dart.client as C


def _bundle() -> dict:
    return json.loads((files("open_proxy_mcp.data.dart") / "periodic_filers.json")
                      .read_text(encoding="utf-8"))


def test_runtime_loader_actually_reads_the_bundle():
    """**파일이 있는 것과 로더가 읽는 건 다르다** — 260814 교훈(경로 의존이 조용히 0을 낸다)."""
    got = C._filers_bundled_load()
    assert got is not None, "동봉본을 런타임이 못 읽는다 — 패키지 배선 확인"
    assert len(got) >= C._FILERS_MIN_EXPECTED


def test_bundle_is_read_as_package_data_not_a_path():
    src = inspect.getsource(C._filers_bundled_load)
    assert 'files("open_proxy_mcp.data.dart")' in src
    assert "Path(__file__)" not in src, "파일 경로 의존으로 되돌아갔다"


def test_resolution_order_puts_bundle_after_sqlite_before_fetch():
    """sqlite(운영 중 갱신본)가 동봉본보다 새것이므로 먼저다.
    동봉본을 쓸 때도 **뒤에서 최신본을 만든다** — 최대 한 달 낡을 수 있으므로."""
    src = inspect.getsource(C.DartClient.periodic_filers)
    i_sql = src.index("_filers_db_load()")
    i_bundle = src.index("_filers_bundled_load()")
    i_build = src.index("_start_filers_build()")
    assert i_sql < i_bundle < i_build, "해결 순서가 어긋났다"
    assert src[i_bundle:i_build].count("_start_filers_build") == 0
    assert "_start_filers_build()" in src[i_bundle:], "동봉본을 쓰고 갱신을 안 건다"


def test_bundle_carries_provenance():
    """언제 것인지 모르는 명부는 낡아도 알 수 없다."""
    meta = _bundle()["meta"]
    for k in ("count", "refreshed_at", "source"):
        assert meta.get(k), f"meta.{k} 없음"


def test_bundle_covers_unlisted_financials_the_list_exists_for():
    """명부의 목적 — 종목코드가 없는 금융사(농협금융지주 등)를 여는 것.
    corp_code 를 하드코딩하지 않고 **개수로** 본다(코드는 바뀔 수 있다)."""
    filers = _bundle()["filers"]
    assert len(filers) >= 3_000, f"{len(filers)}사 — 너무 적다"
    # 접수일이 최근인가(400일 창이 제대로 돌았나)
    assert max(filers.values()) >= "20260101"


def test_company_tool_path_uses_the_same_filers_rule_as_the_resolver():
    """🔴 **판정이 두 곳에 있으면 한쪽만 고쳐진다.**

    260823 에 `resolve_company_query` 를 명부 기반으로 고쳤는데, `company` tool 이 직접
    타는 경로는 여전히 `stock_code` 로만 걸렀다. 그래서 **리졸버는 찾는데 tool 이 버렸다** —
    농협금융지주·교보생명보험이 「비상장이어서 제외」로 막혔다(명부·금융명 판정은 둘 다 통과했는데도).
    """
    import inspect

    import open_proxy_mcp.services.company as CO

    src = inspect.getsource(CO)
    i_tool = src.index("unlisted_only = False")
    tool_path = src[i_tool:i_tool + 1400]
    assert "periodic_filers()" in tool_path, "tool 경로가 명부를 안 본다"
    assert "is_financial_name" in tool_path, "tool 경로가 금융업 판정을 안 한다"


def test_names_are_not_duplicated_into_the_bundle():
    """이름은 **corp_codes 원장에만** 둔다.

    원장은 118,744사를 7일마다 갱신하고 이 명부는 월 1회다 — 여기 이름을 또 담으면
    이쪽이 더 낡아, 사명이 바뀐 회사에 옛 이름을 보여주게 된다. corp_code 로 이으면
    되는 것을 복사해두면 두 곳이 어긋난다(260823~24 에 반복해서 본 형태:
    per_ttm 두 정의 · mkt/market 키 불일치 · 각주가 KSIC 로 굳음 · 상장사 필터 두 벌).
    """
    b = _bundle()
    assert "corp_name_at_collection" not in b, "이름을 명부에 복사했다"
    assert set(b) == {"meta", "filers"}, f"예상 밖 블록: {set(b) - {'meta', 'filers'}}"
    assert b["meta"].get("names"), "이름을 왜 안 담는지 적혀 있지 않다"

    import inspect
    src = inspect.getsource(C._fetch_periodic_filers) if hasattr(C, "_fetch_periodic_filers") \
        else inspect.getsource(C.DartClient._fetch_periodic_filers)
    assert "corp_name" not in src, "수집기가 이름을 담는다"
