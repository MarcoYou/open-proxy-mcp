# -*- coding: utf-8 -*-
"""부문 부재 사유 판정 + 구간 확정 시 반환 보장 단위 테스트.

문구는 전부 캐시 실측에서 가져왔다 — 가공 예시로 테스트하면 실제 표현 변형을 놓친다.
매핑 파일은 비공개이므로 합성 fixture 로 기제(mechanism)만 검증한다.
"""
import json

import pytest

from open_proxy_mcp.services import business_details as bd
from open_proxy_mcp.services import coordinate_map


# ── 단일부문 선언: 실측 문구 (기존 정규식이 전부 놓쳤던 것들) ──────────────
@pytest.mark.parametrize("phrase", [
    "4. 부문정보 연결실체는 반도체 제조 및 판매를 주요사업으로 하는 단일의 보고부문으로 구성되어 있으며",
    "연결기업은 기업전체가 하나의 영업부문으로 분류되기 때문에 영업부문의 선택이나",
    "연결실체의 영업부문은 화장품 판매 단일의 보고부문으로 구성됩니다",
    "연결실체는 단일 부문으로 사업을 수행하고 있으며, 부문별로 수익, 비용을 관리하고 있지 아니합니다",
    "당사는 단일의 영업부문을 보유하고 있으며 당사 매출 지역에 대한 정보는 다음과 같습니다",
    "연결회사 전체를 단일 보고부문으로 결정하였습니다",
    "연결회사는 단일의 영업부문으로 운영되고 있습니다",
])
def test_single_segment_declarations_detected(phrase):
    got = bd.classify_absence(phrase)
    assert got is not None, "실측 단일부문 문구를 놓쳤다"
    assert got[0] == bd.NOT_APPLICABLE
    assert "단일부문 선언" in got[1] and "「" in got[1], "근거 인용이 없으면 사용자가 검증할 수 없다"


# ── 부정문은 단일부문으로 오판하지 않는다 ────────────────────────────────
@pytest.mark.parametrize("phrase", [
    "연결실체는 단일 부문이 아니며 3개 보고부문으로 구성되어 있습니다. " + "x" * 300,
    "단일부문이 아닌 복수의 보고부문을 운영하고 있습니다. " + "y" * 300,
])
def test_negated_single_segment_not_detected(phrase):
    got = bd.classify_absence(phrase)
    assert got is None or got[0] != bd.NOT_APPLICABLE, f"부정문을 단일부문으로 오판: {got}"


# ── 주석 미기재는 파싱 실패가 아니라 미수집이다 ──────────────────────────
@pytest.mark.parametrize("note", [
    "3. 연결재무제표 주석 \n\n 해당사항이 없습니다.",
    "3. 연결재무제표 주석 \n\n - 해당사항없음",
    "3. 연결재무제표 주석 \n 보고서 작성 기준일 현재 해당사항 없습니다.",
    "3. 연결재무제표 주석",
])
def test_note_omitted_is_not_collected(note):
    got = bd.classify_absence(note)
    assert got is not None and got[0] == bd.NOT_COLLECTED, f"미기재를 못 가렸다: {got}"


def test_unknown_returns_none_not_a_guess():
    """모르는 것을 '단일부문 추정'으로 단정하면 오진이 조용히 나간다 → None 이어야 한다."""
    note = "3. 연결재무제표 주석\n" + "실제 주석 본문이 길게 있으나 부문 언급은 없다. " * 30
    assert bd.classify_absence(note) is None


# ── 구간 확정 = 반환 보장 (게이트가 내용을 폐기하지 않는다) ───────────────
_LOW_ROW_HTML = (
    '<TABLE-GROUP ACLASS="{XBRL}TESTSEG">'
    '<TITLE ATOC="Y">3. 영업부문 (연결)</TITLE>'
    '<P>연결회사는 단일 보고부문으로 결정하였습니다.</P>'
    '<TABLE><TR><TH>구분</TH><TH>당기</TH></TR>'
    '<TR><TD>영업수익</TD><TD>1,234,567</TD></TR></TABLE>'
    '</TABLE-GROUP>'
)


@pytest.fixture
def synthetic_map(tmp_path, monkeypatch):
    p = tmp_path / "coordinate_map.json"
    p.write_text(json.dumps({
        "version": "test",
        "concepts": {"부문별_보고": {"consolidated": "{XBRL}TESTSEG",
                                    "title_must_contain": ["부문"]}},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("OPM_COORDINATE_MAP_PATH", str(p))
    coordinate_map._CACHE.update({"path": None, "mtime": None, "data": None, "error": None})
    return p


def test_confirmed_region_returned_even_with_few_data_rows(synthetic_map):
    md, reason = bd.render_segment_note_md_by_code(_LOW_ROW_HTML)
    assert md, "구간을 확정했는데 내용을 버렸다 — 원칙 위반"
    assert reason.startswith("code:consolidated")
    assert "low_rows" in reason, "행이 적다는 사실은 라벨로 표시되어야 한다"


def test_title_mismatch_is_rejected_with_reason(synthetic_map):
    html = _LOW_ROW_HTML.replace("3. 영업부문 (연결)", "20-2. 주요 고객")
    md, reason = bd.render_segment_note_md_by_code(html)
    assert md is None, "부문 표가 아닌 블록을 채택하면 엉뚱한 값을 찾게 된다"
    assert reason.startswith("title_mismatch"), reason


def test_map_absent_degrades_loudly(monkeypatch):
    monkeypatch.setenv("OPM_COORDINATE_MAP_PATH", "/nonexistent/coordinate_map.json")
    coordinate_map._CACHE.update({"path": None, "mtime": None, "data": None, "error": None})
    md, reason = bd.render_segment_note_md_by_code(_LOW_ROW_HTML)
    assert md is None and reason == "map_not_loaded"
    st = coordinate_map.status()
    assert st["loaded"] is False and "error" in st, "미탑재 사실이 응답에 표면화되어야 한다"
