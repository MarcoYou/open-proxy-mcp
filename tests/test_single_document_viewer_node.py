"""목차 없는 공시(거래소 서식)도 원문 절 읽기가 열 수 있게 — 뷰어 첫 화면 호출 한 줄을 절 하나로 삼는다."""
from open_proxy_mcp.dart.client import html_to_text
from open_proxy_mcp.services.business_details import _extract_node_tree, _node_fetchable

# 2026-11-14 실측: 한섬 주식소각결정 20260918800133 의 main.do — treeData 가 없고 이 호출만 있다.
SINGLE = '''<html><head><title>한섬/주식소각결정/2026.09.18</title></head><body><script>
function load(){ viewDoc( original.rcpNo, original.dcmNo, original.eleId, original.offset, original.length, original.dtd, original.tocNo ); }
viewDoc("20260918800133", "11584406", "0", "0", "0", "HTML", "");
</script></body></html>'''

TREE = '''<script>var node1 = {};
node1['text'] = "Ⅰ. 공개매수의 결과"; node1['rcpNo'] = "20241017000004"; node1['dcmNo'] = "1"; node1['eleId'] = "2";
node1['offset'] = "10"; node1['length'] = "20"; node1['dtd'] = "dart4.xsd"; node1['tocNo'] = "2"; treeData.push(node1);
viewDoc("20241017000004", "1", "2", "10", "20", "dart4.xsd", "");</script>'''


def test_document_without_toc_becomes_one_fetchable_section_named_by_the_form():
    nodes = _extract_node_tree(SINGLE)
    assert len(nodes) == 1 and _node_fetchable(nodes[0])
    assert nodes[0]["text"] == "주식소각결정" and nodes[0]["lvl"] == 1
    assert (nodes[0]["rcpNo"], nodes[0]["dcmNo"], nodes[0]["dtd"]) == ("20260918800133", "11584406", "HTML")


def test_document_with_toc_is_unchanged_and_garbage_stays_empty():
    assert [n["text"] for n in _extract_node_tree(TREE)] == ["Ⅰ. 공개매수의 결과"]   # 목차가 있으면 그 목차만
    assert _extract_node_tree("<html><body>점검 중</body></html>") == []


def test_style_and_script_bodies_are_dropped_from_text():
    html = "<STYLE>.xforms * { font-family: 돋움체;}</STYLE><script>var a = '<p>x</p>';</script><P>1. 소각할 주식의 종류와 수</P>"
    assert html_to_text(html) == "1. 소각할 주식의 종류와 수"
    assert html_to_text("<style>.x{}<p>본문</p>") == ".x{} 본문"      # 닫는 태그가 없으면 건드리지 않는다
