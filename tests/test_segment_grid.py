# -*- coding: utf-8 -*-
"""부문표 격자 재판독(segment_grid) 회귀 테스트 — synthetic 표. network 0콜.

핵심 계약: ① 외부매출 행 우선(연도 간 개념 일관) ② dash 열=0.0 자리표시자(부문 소실 금지)
③ 조정·합계 열은 excess로 분리(기존 검산 게이트 호환) ④ 이름 불일치·비주석 소스는 None.
"""
from __future__ import annotations

from open_proxy_mcp.services.business_details import OK, SegmentProfit, _segment_confident
from open_proxy_mcp.services.segment_grid import _parse_table, grid_refine


def _tbl(rows: list[list[str]]) -> str:
    trs = "".join(
        "<TR>" + "".join(f"<TD>{c}</TD>" for c in r) + "</TR>" for r in rows)
    # 스페이서 필터(<1500자 표 skip)를 넘도록 표 내부에 무해한 패딩 셀 포함
    pad = "<TR><TD>" + " " * 1500 + "</TD></TR>"
    return f'<TABLE ACLASS="NORMAL"><TBODY>{trs}{pad}</TBODY></TABLE>'


_ROWS = [
    ["(단위 : 백만원)", "", "", "", ""],
    ["구분", "화학", "전지", "조선", "합계"],
    ["총부문수익", "1,200", "2,300", "-", "3,500"],
    ["부문간수익", "200", "300", "-", "500"],
    ["매출액", "1,000", "2,000", "-", "3,000"],
    ["영업이익(손실)", "100", "(50)", "-", "50"],
]


def test_parse_prefers_external_row_and_keeps_dash_column() -> None:
    p = _parse_table(_tbl(_ROWS))
    assert p is not None
    assert p["revenue_metric"] == "매출액"            # 총부문수익(위) 아닌 외부 계열
    names = [s["name"] for s in p["segments"]]
    assert names == ["화학", "전지", "조선"]           # dash 열(조선) 유지
    assert p["segments"][2]["revenue"] == 0.0
    assert p["segments"][1]["profit"] == -50.0        # 괄호 음수
    assert p["excess"] == [3000.0]                    # 합계 열 → 검산 재료
    assert p["unit"] == "백만원"


def test_parse_adjustment_column_ordered_before_total() -> None:
    rows = [r[:] for r in _ROWS]
    rows[1] = ["구분", "화학", "전지", "연결조정", "합계"]
    rows[4] = ["매출액", "1,000", "2,000", "(500)", "2,500"]
    p = _parse_table(_tbl(rows))
    assert [s["name"] for s in p["segments"]] == ["화학", "전지"]
    assert p["excess"] == [-500.0, 2500.0]            # 조정 → 합계 순(게이트 ⓑ 호환)


def test_gate_passes_grid_output() -> None:
    p = _parse_table(_tbl(_ROWS))
    sp = SegmentProfit(status=OK, source="note_grid", segments=p["segments"])
    sp.adjustments = [{"revenue_excess": p["excess"]}]
    assert _segment_confident(sp)                     # 부문합 3,000 == 총계 3,000


def test_refine_skips_non_note_source_and_name_mismatch() -> None:
    html = "<DOCUMENT>3. 부문정보" + _tbl(_ROWS) + "</DOCUMENT>"
    body_sp = SegmentProfit(status=OK, source="body", anchor="부문정보")
    assert grid_refine(html, body_sp) is None         # 본문표 소스는 v1 미대상
    note_sp = SegmentProfit(status=OK, source="note", anchor="부문정보",
                            segments=[{"name": "완전히다른부문", "revenue": 1.0}])
    assert grid_refine(html, note_sp) is None         # 이름 겹침 0 = 다른 표 의심 → 텍스트 유지


def test_refine_adopts_on_note_anchor() -> None:
    html = "<DOCUMENT>3. 부문정보" + _tbl(_ROWS) + "</DOCUMENT>"
    sp = SegmentProfit(status=OK, source="note", anchor="부문정보",
                       segments=[{"name": "화학", "revenue": 1200.0}])
    g = grid_refine(html, sp)
    assert g is not None and g.source == "note_grid"
    assert g.revenue_metric == "매출액"


def test_geo_share_is_the_unit_proof_metric():
    """해외비중은 단위가 약분되므로 단위 미상일 때도 맞는 유일한 지표.

    각주가 붙은 지역명(카카오 「국내(주1)」)을 국내로 못 읽으면 해외비중이 100%로 나온다 —
    실측에서 실제로 그랬다(정정 후 20.6%).
    """
    from open_proxy_mcp.services.segment_grid import _foreign_share

    got = _foreign_share([{"name": "국내(주1)", "revenue": 800.0},
                          {"name": "아시아", "revenue": 100.0},
                          {"name": "북미", "revenue": 100.0}])
    assert got["foreign_share_pct"] == 20.0
    assert "share_caveat" not in got

    # 표에 국내 구분이 아예 없으면 100%로 계산되지만 그 사실을 밝힌다(대한해운)
    none_dom = _foreign_share([{"name": "아시아", "revenue": 60.0},
                               {"name": "유럽", "revenue": 40.0}])
    assert none_dom["foreign_share_pct"] == 100.0
    assert "국내 구분 항목이 없어" in none_dom["share_caveat"]

    # 「본사 소재지 국가」도 국내다
    hq = _foreign_share([{"name": "본사 소재지 국가", "revenue": 932160.0},
                         {"name": "외국", "revenue": 3147338.0}])
    assert hq["foreign_share_pct"] == 77.2


def test_entity_wide_geo_tables_are_small_and_must_not_be_length_filtered():
    """entity-wide 지역표는 데이터가 한 행뿐이라 작다 — 길이 하한에 걸려선 안 된다.

    실측: HD현대일렉트릭 493자 · 현대차 1,235자로 둘 다 1500자 하한에 걸려 아예 읽히지
    않았다(파싱 자체는 정상이었다). 지역 머리를 가진 표만 하한을 낮춘다.
    캐시 65사 회귀: 검출 8 → 17사, 상실 0 · 기존 값 변경 0.
    """
    from open_proxy_mcp.services.segment_grid import _EW_GEO_MIN_CHARS, _GEO_HEAD_RE

    assert _EW_GEO_MIN_CHARS < 493, "실측 최소 지역표(493자)보다 낮아야 한다"
    assert _GEO_HEAD_RE.search("<TH>본사 소재지 국가</TH>")
    assert _GEO_HEAD_RE.search("<TH>외 국</TH>")
    assert not _GEO_HEAD_RE.search("<TH>차량</TH><TH>금융</TH>")


def test_geo_anchor_falls_back_when_segment_note_anchor_is_missing():
    """부문정보 앵커를 못 찾아도 지역 공시는 따로 있을 수 있다.

    HD현대일렉트릭은 앵커가 안 잡혀(`''`) 스캔 자체를 건너뛰었는데 지역표는 실재했다.
    """
    from open_proxy_mcp.services.segment_grid import _find_geo_anchor_pos

    html = "x" * 5000 + "<TH>본사 소재지 국가</TH>"
    pos = _find_geo_anchor_pos(html)
    assert pos >= 0
    assert pos <= 5000, "표를 포함하도록 조금 앞에서 시작해야 한다"
    assert _find_geo_anchor_pos("<TH>차량</TH><TH>금융</TH>") == -1


def test_note_sections_are_the_anchor_and_narrow_the_search():
    """주석 절 목록(`<A name='tocN'>`)이 탐색 창을 정한다 — 좁게 시작해 못 찾으면 넓힌다.

    주석 본문엔 AASSOCNOTE·ACODE 가 **없다**(캐시 89건 실측 0%). 구조 코드는 챕터
    경계까지고 주석 안에선 사라진다. 대신 toc 앵커가 89/89(100%) 있어 절 경계를 준다.
    절로 좁히면 볼 표가 중앙 248개 → 9개(28배). 못 찾으면 문서 전체로 넓히는데
    전수 파싱이 중앙 80ms·최대 615ms 라(DART 콜 하나가 1~3초) 넓히지 못할 이유가 없다.
    """
    from open_proxy_mcp.services.segment_grid import _note_sections, _scan_windows

    html = ("<A name='toc1'>1. 회사의 개요 (연결)</A>" + "a" * 500
            + "<A name='toc2'>2. 영업부문 (연결)</A>" + "b" * 500
            + "<A name='toc3'>3. 리스 (연결)</A>" + "c" * 500)
    secs = _note_sections(html)
    assert [t for _, t, _, _ in secs] == ["1. 회사의 개요 (연결)", "2. 영업부문 (연결)", "3. 리스 (연결)"]
    assert secs[0][3] == secs[1][2], "절 끝은 다음 절 시작이어야 한다"

    wins = _scan_windows(html, "2. 영업부문 (연결)")
    assert wins[0][2] == "2. 영업부문 (연결)", "호출측 앵커 절이 1순위"
    assert wins[-1][1] == len(html), "마지막 창은 문서 전체 폴백"
    # 리스 절은 사전에 없으므로 후보에서 빠진다
    assert "3. 리스 (연결)" not in [w[2] for w in wins]


def test_geo_section_dictionary_covers_sections_other_than_부문():
    """지역표는 「부문」 절에만 있는 게 아니다 — 사전을 넓혀야 한다.

    캐시 32건 실측: 부문 절만 보면 25/32(78%), 수익·고객과의 계약·보험위험까지 넓히면
    31/32(97%). LG전자는 「28. 매출액」, HD현대일렉트릭은 「27. 수익」 절에 있었다.
    """
    from open_proxy_mcp.services.segment_grid import _GEO_SECTION_RE

    for t in ("37. 부문정보 (연결)", "4. 영업부문 (연결)", "27. 수익 (연결)",
              "28. 매출액 (연결)", "23. 영업수익 (연결)",
              "5. 영업부문 및 고객과의 계약에서 생기는 수익 (연결)", "보험위험 (연결)"):
        assert _GEO_SECTION_RE.search(t), t
    for t in ("3. 리스 (연결)", "9. 유형자산 (연결)", "12. 퇴직급여 (연결)"):
        assert not _GEO_SECTION_RE.search(t), t


def test_xbrl_taxonomy_code_is_the_anchor_on_the_main_path():
    """주 경로(document.xml)의 주석 절 앵커는 제목이 아니라 XBRL 택소노미 코드다.

    회사마다 절 번호(4·5·6·22·33·35·39)와 제목이 다른데 코드는 하나로 모인다:
      NT_C_D871100 (28회) ← 「4. 영업부문 (연결)」「33. 부문별 정보 (연결)」
                              「35. 영업부문 정보 (연결)」「주석 - 5. 영업부문정보 - 연결 (연결)」
    제목 사전은 새 표현이 나오면 뚫리지만 코드는 안 뚫린다. 실측 34건 중 97%가 보유.
    D871=K-IFRS 1108 영업부문 · D831=K-IFRS 1115 수익 · 804 계열=부문 공시 변형.
    """
    from open_proxy_mcp.services.segment_grid import _GEO_XBRL_RE, _note_sections, _scan_windows

    for code in ("NT_C_D871100", "NT_S_D871105", "NT_C_D831150", "NT_S_D831155",
                 "NT_C_DS804000", "NT_C_DI804000", "NT_C_DX804000"):
        assert _GEO_XBRL_RE.search(code), code
    for code in ("BS_C", "IS_C", "NT_C_D610000"):
        assert not _GEO_XBRL_RE.search(code), code

    html = ('<TABLE-GROUP ACLASS="{XBRL}NT_C_D610000"><TITLE>9. 유형자산 (연결)</TITLE>' + "a" * 400
            + '<TABLE-GROUP ACLASS="{XBRL}NT_C_D871100"><TITLE>33. 부문별 정보 (연결)</TITLE>' + "b" * 400)
    secs = _note_sections(html)
    assert [k for k, _t, _s, _e in secs] == ["NT_C_D610000", "NT_C_D871100"]
    wins = _scan_windows(html, "")
    assert "NT_C_D871100" in wins[0][2], "XBRL 코드가 1순위 창이어야 한다"


def test_row_oriented_geo_tables_are_read():
    """지역이 **행**에 오는 서식도 읽는다 — 부문표 파서는 열 지향만 안다.

    LG화학은 「지역 | 한국 | 총부문수익 | 비유동자산」 구조라 항목이 0개로 나와
    표를 통째로 버렸다(앵커는 맞았는데 표에서 걸림). 셀이 `<TD>` 가 아니라
    DART XML 의 `<TE>` 라 데이터 행이 통째로 비어 보인 것도 함께 잡았다.
    수정 후 LG화학 해외비중 78.4%(한국 10.55조 · 아메리카 12.5조 · 유럽 8.9조).
    """
    from open_proxy_mcp.services.segment_grid import _read_row_oriented_geo

    chunk = """<TABLE><THEAD><TR><TH></TH><TH></TH><TH>총부문수익</TH><TH>비유동자산</TH></TR></THEAD>
    <TBODY>
    <TR><TE>지역</TE><TE>한국</TE><TE>10,553,720</TE><TE>20,165,226</TE></TR>
    <TR><TE></TE><TE>중국</TE><TE>11,108,354</TE><TE>4,989,625</TE></TR>
    <TR><TE></TE><TE>아메리카</TE><TE>12,513,258</TE><TE>26,294,590</TE></TR>
    <TR><TE>지역 합계</TE><TE></TE><TE>34,175,332</TE><TE>51,449,441</TE></TR>
    </TBODY></TABLE>"""
    got = _read_row_oriented_geo(chunk)
    assert got is not None
    assert [s["name"] for s in got["segments"]] == ["한국", "중국", "아메리카"]
    assert got["segments"][0]["revenue"] == 10553720.0
    assert got["excess"] == [34175332]
    # 비유동자산 열이 있으면 생산지 판별용으로 함께 돌려준다
    assert (got.get("_assets_by_region") or {}).get("한국") == 20165226.0


def test_absence_signal_separates_not_disclosed_from_extraction_failure():
    """`NOT_COLLECTED` 만 내면 「회사가 안 냈다」와 「우리가 못 읽었다」를 구분할 수 없다.

    실측 75건: 부문/수익 XBRL 블록 자체가 없음 45.3%(확정적 부재) · 블록에 지역표가
    있는데 미검출 5.3%(추출 실패) · 문서 어디에도 지역 표지 없음 4.0%.
    """
    from open_proxy_mcp.services.segment_grid import absence_signal

    # 부문 주석 자체가 없다 — 단일 부문이라 생략한 것(확정적 부재)
    assert absence_signal('<TABLE-GROUP ACLASS="{XBRL}NT_C_D610000">'
                          "<TITLE>9. 유형자산 (연결)</TITLE>")["absence_kind"] == "no_segment_note"
    # 부문 주석은 있으나 지역 정보를 안 실었다(확정적 부재)
    assert absence_signal('<TABLE-GROUP ACLASS="{XBRL}NT_C_D871100">'
                          "<TITLE>4. 영업부문 (연결)</TITLE>부문별 매출"
                          )["absence_kind"] == "not_disclosed"
    # 지역 표지가 블록 안에 있는데 못 읽었다 — 고칠 대상
    got = absence_signal('<TABLE-GROUP ACLASS="{XBRL}NT_C_D871100">'
                         "<TITLE>4. 영업부문 (연결)</TITLE>본사 소재지 국가")
    assert got["absence_kind"] == "extraction_failed"
    assert "NT_C_D871100" in got["absence_sections"][0]


def test_single_region_table_means_all_domestic_not_missing_data():
    """지역이 하나뿐인 표는 「정보 없음」이 아니라 「전량 국내」라는 확정 정보다.

    동우팜투테이블 「본사 소재지 국가 | 325,458」은 항목 2개 이상 게이트에 걸려
    통째로 버려졌다 — 해외비중 0%를 낼 수 있는 케이스였다.
    """
    from open_proxy_mcp.services.segment_grid import _read_column_oriented_geo

    chunk = ("<TABLE><TR><TH></TH><TH>본사 소재지 국가</TH></TR>"
             "<TR><TE>수익(매출액)</TE><TE>325,458</TE></TR></TABLE>")
    got = _read_column_oriented_geo(chunk)
    assert got is not None
    assert [s["name"] for s in got["segments"]] == ["본사 소재지 국가"]
    assert got["segments"][0]["revenue"] == 325458.0


def test_ii_export_is_carried_in_parallel_never_merged_with_iii():
    """II 수출/내수는 III 지역별과 **다른 지표**다 — 병렬로 싣되 합치지 않는다.

    실측 75건: III 부문 주석이 없는 34건 중 **31건이 II 에 수출 표기**를 갖는다(상호보완).
    그런데 별도 기준 수출 vs 연결 기준 외국 수익이라 현대차 1.4x·대한제분 0.5x 로
    방향이 양쪽으로 갈린다(현지생산 vs 내부거래 제거). 현대차는 수출 행이 품목마다
    반복되므로 전부 합산해야 한다.
    """
    from open_proxy_mcp.services.business_details import _export_from_biz_table

    # 실제 현대차 매출실적표 구조 — 머리에 「매출유형」이 있고 수출/내수가 품목마다 반복된다
    html = ("<div>(단위 : 백만원)</div>"
            "<TABLE><TR><TH>매출유형</TH><TH>품 목</TH><TH>2025년 (제58기)</TH></TR>"
            "<TR><TE>제품</TE><TE>승용</TE><TE>내 수</TE><TE>9,747,622</TE></TR>"
            "<TR><TE></TE><TE></TE><TE>수 출</TE><TE>10,799,207</TE></TR>"
            "<TR><TE></TE><TE>RV</TE><TE>내 수</TE><TE>13,556,256</TE></TR>"
            "<TR><TE></TE><TE></TE><TE>수 출</TE><TE>26,917,762</TE></TR></TABLE>")
    got = _export_from_biz_table(html)
    assert got is not None
    assert got["rows_summed"] == 2, "품목마다 반복되는 수출 행을 전부 합산해야 한다"
    assert got["export_krw"] == (10_799_207 + 26_917_762) * 1_000_000
    assert "다른 지표" in got["caveat"]


def test_window_boundary_replaces_the_legacy_150k_cap():
    """창 경계가 있으면 그것이 범위다 — 150,000자 제한은 앵커 하나로 훑던 시절의 잔재.

    현대차 「37. 부문정보 (연결)」 절은 **435,866자**라 150KB 에서 끊겨 지역표에
    닿지 못했다(검산·분류는 다 통과할 표였다). 창 경계로 바꿔 해외 70.3% 확정.
    창이 없을 때(문서 전체 폴백)만 옛 제한을 유지한다.
    """
    import inspect

    from open_proxy_mcp.services import segment_grid as SG

    src = inspect.getsource(SG._scan_window)
    assert "limit = win_end if win_title else min(win_end, pos + 150000)" in src
    assert SG._EW_MAX_ATTEMPTS >= 60, "435KB 절에는 후보 표가 14개를 훌쩍 넘는다"


def test_degraded_fallback_is_not_erased_by_a_later_empty_window():
    """뒤 창이 폴백을 못 찾아도 앞 창에서 잡은 원문 폴백을 지우면 안 된다.

    창 단위 탐색으로 바꾸면서 `geo_md_fallback = got` 로 덮어써, 단위 미상으로
    강등된 원문 표가 NOT_COLLECTED 로 사라졌다.
    """
    import inspect

    from open_proxy_mcp.services import segment_grid as SG

    assert "geo_md_fallback = got or geo_md_fallback" in inspect.getsource(SG.scan_entity_wide)
