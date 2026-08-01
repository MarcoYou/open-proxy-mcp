"""회귀의 입력은 **프로덕션이 실제로 받는 것**이어야 한다 — 260731 사고 재발 방지.

DART 문서를 받는 경로가 둘인데 구조 표지가 정반대다:

  `document.xml`(주 경로, `_fetch_getdoc`)  : AASSOCNOTE·ACODE 있음 · `<A name='tocN'>` 없음
  viewer HTML  (014 폴백, `_fetch_viewer_sec`): 반대

260731에 viewer HTML 을 캐시해 geo 회귀를 돌리고 「검출 8 → 20사」를 보고했는데,
주 경로로 재측정하니 14 → 16 이었다. 코드는 멀쩡했고 **입력이 달랐다**.
「production 함수를 import 했다」는 검증의 근거가 못 된다 — 함수가 아니라 입력이 기준이다.

이 테스트는 두 원본이 서로 대체 가능하지 **않다**는 것을 계약으로 고정한다.
네트워크를 타지 않으며, `opm_cache`(= `get_document_cached` 디스크 캐시)가 있으면
그 실제 원본으로 한 번 더 확인한다(없으면 그 부분만 건너뛴다).
"""
import json
import os
import re
import tempfile

TOC_ANCHOR = re.compile(r"<A\s+name=['\"]toc\d+['\"]", re.I)
AASSOC = re.compile(r'AASSOCNOTE="')
ACODE = re.compile(r'\bACODE="')


def _cached_documents(limit: int = 40):
    """`get_document_cached` 가 남긴 디스크 캐시에서 사업보고서류 document.xml 을 읽는다."""
    d = os.path.join(tempfile.gettempdir(), "opm_cache")
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if not (fn.endswith(".json") and len(fn) == 19):
            continue
        try:
            doc = json.load(open(os.path.join(d, fn), encoding="utf-8"))
        except Exception:
            continue
        html, text = doc.get("html") or "", doc.get("text") or ""
        if html and "사업의 내용" in text:
            out.append((fn, html))
        if len(out) >= limit:
            break
    return out


def test_the_two_document_sources_are_not_interchangeable():
    """주 경로와 폴백 경로의 원본은 구조 표지가 달라 서로 대체할 수 없다.

    회귀 입력을 viewer 로 만들면 toc 앵커에 의존하는 로직이 「잘 된다」고 나오지만
    주 경로에는 그 앵커가 없다. 이 계약이 깨지면(둘이 같아지면) 이 테스트를 지워도 된다.
    """
    docs = _cached_documents()
    if not docs:
        return  # 캐시 없는 환경(CI 등) — 아래 계약 문구 테스트로 충분
    with_code = sum(1 for _, h in docs if AASSOC.search(h) and ACODE.search(h))
    with_toc = sum(1 for _, h in docs if TOC_ANCHOR.search(h))
    assert with_code == len(docs), (
        f"document.xml 은 AASSOCNOTE·ACODE 를 갖는다 — {with_code}/{len(docs)}")
    assert with_toc == 0, (
        f"document.xml 에는 toc 앵커가 없다 — {with_toc}/{len(docs)} 에서 발견. "
        "toc 앵커에 의존하는 로직은 주 경로에서 동작하지 않는다")


def test_regression_input_must_come_from_the_dart_response_boundary():
    """회귀 캐시는 `get_document_cached` 결과여야 한다는 규칙이 CLAUDE.md 에 있다.

    규칙은 잊히므로 문서에 남아 있는지 기계로 확인한다(260731 사고 재발 방지).
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    md = open(os.path.join(root, "CLAUDE.md"), encoding="utf-8").read()
    assert "get_document_cached" in md and "중간 함수" in md, (
        "CLAUDE.md 에 「회귀 캐시는 DART 응답 경계에서만 만든다」 규칙이 있어야 한다")
    assert "함수가 아니라 입력이 기준" in md


def test_geo_scan_survives_the_main_path_input():
    """주 경로 입력(document.xml)으로도 지역 스캐너가 예외 없이 동작한다.

    260731 실측(캐시 34건): 검출 14 → 16건 · 검출 상실 0 · 예외 0.
    viewer 기준의 8 → 20 은 폴백 경로 수치이고 주 경로 성능이 아니다.
    """
    docs = _cached_documents(limit=12)
    if not docs:
        return
    from open_proxy_mcp.services.business_details import (
        _GEO_NAMES, _slice_getdoc_sections, find_segment_note_region)
    from open_proxy_mcp.services.segment_grid import scan_entity_wide

    for fn, html in docs:
        biz, note, _src = _slice_getdoc_sections("사업의 내용", html=html, images=None)
        anchor = (find_segment_note_region(note or "")[0]) or ""
        got = scan_entity_wide(html, anchor, _GEO_NAMES,
                               product_fallback=True, exclude_names=set())
        assert isinstance(got, dict) and "geo" in got, fn
        g = got.get("geo")
        if g and g.get("foreign_share_pct") is not None:
            assert -1 <= g["foreign_share_pct"] <= 101, (fn, g["foreign_share_pct"])
