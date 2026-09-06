# -*- coding: utf-8 -*-
"""공시 절 단위 리소스 `opm://filing/{rcept_no}/toc` · `/section/{no}{?start}` — network 0.

뷰어 main.do(treeData)·viewer.do(절 HTML)를 합성 fixture 로 대고, **MCP 경로**(`mcp.read_resource`)로
읽는다 — 리소스 등록·템플릿 매칭·렌더러·기본값을 다 지나야 사용자가 보는 것과 같다(CLAUDE.md 워크플로).

260906 실측(11개 사업보고서, wiki/handoff/260906_filing-section-resources.md)이 정한 것을 고정한다:
  · 부모 노드는 하위를 통째로 주므로 **부모 번호 → 하위 목록**, 본문은 leaf 만
  · 캐시는 텍스트만 · 절당 4만 자 + `?start=` 이어읽기 · 전송 오류 재시도 1회 · doc_gate 통과
"""
from __future__ import annotations

import asyncio
import re

import httpx
import pytest

from open_proxy_mcp.dart import client as dart_client
from open_proxy_mcp.server import mcp
from open_proxy_mcp.services import filing_sections as fs

RNO = "20260310002820"


def _node(var: str, toc_no: str, text: str, ele: str, length: int) -> str:
    return (f"var {var} = {{}}; {var}['text'] = \"{text}\"; {var}['id'] = \"{toc_no}\"; "
            f"{var}['rcpNo'] = \"{RNO}\"; {var}['dcmNo'] = \"11104488\"; {var}['eleId'] = \"{ele}\"; "
            f"{var}['offset'] = \"100\"; {var}['length'] = \"{length}\"; {var}['dtd'] = \"dart4.xsd\"; "
            f"{var}['tocNo'] = \"{toc_no}\"; ")


MAIN_HTML = "<html><script>var treeData = [];" + "".join([
    _node("node1", "1", "사 업 보 고 서", "1", 4524), "treeData.push(node1);",
    _node("node1", "3", "I. 회사의 개요", "3", 197163),
    _node("node2", "4", "1. 회사의 개요", "4", 45026), "node1['children'].push(node2);",
    _node("node2", "5", "2. 회사의 연혁", "5", 25144), "node1['children'].push(node2);",
    "treeData.push(node1);",
    _node("node1", "17", "III. 재무에 관한 사항", "17", 4659691),
    _node("node2", "25", "3. 연결재무제표 주석", "25", 2100360),
    _node("node3", "26", "1. 일반적 사항 (연결)", "26", 141429), "node2['children'].push(node3);",
    _node("node3", "27", "2. 중요한 회계처리방침 (연결)", "27", 11593), "node2['children'].push(node3);",
    "node1['children'].push(node2);",
    "treeData.push(node1);",
]) + "</script></html>"

SECTION_HTML = {
    "4": ("<HTML><BODY><P>1. 회사의 개요</P><P>가. 연결대상 종속회사 개황</P>"
          "<P>(기준일 : 2025년 12월 31일) (단위 : 사)</P>"
          "<TABLE><TR><TD>구분</TD><TD>회사수</TD></TR><TR><TD>상장</TD><TD>1&nbsp;</TD></TR>"
          "<TR><TD>비상장</TD><TD>2</TD></TR></TABLE><P>주1) 각주 문장</P></BODY></HTML>"),
    "5": "<HTML><BODY><P>2. 회사의 연혁</P>" + "<P>가나다라마바사 </P>" * 12_000 + "</BODY></HTML>",
    "26": "<HTML><BODY><P>1. 일반적 사항</P><P>삼성전자주식회사는 1969년 설립.</P></BODY></HTML>",
    "27": "<HTML><BODY><P>2. 회계처리방침</P></BODY></HTML>",
}


class FakeClient:
    """뷰어 두 함수만 흉내 낸다. 캐시·문은 진짜(`_DOC_CACHE`·`doc_gate_slot`)를 쓴다."""

    def __init__(self):
        self.main_calls = 0
        self.section_calls: list[str] = []
        self.fail_first = False
        self._doc_cache = dart_client.LruByteCache(8 * 1024 * 1024, 60, "test-doc")

    async def _fetch_viewer_main_html(self, rcept_no):
        self.main_calls += 1
        return MAIN_HTML

    async def _fetch_viewer_section_html(self, node):
        self.section_calls.append(node["eleId"])
        if self.fail_first:
            self.fail_first = False
            raise httpx.ReadTimeout("")
        return SECTION_HTML[node["eleId"]]

    doc_gate_slot = dart_client.DartClient.doc_gate_slot
    _own_gate = dart_client.DartClient._own_gate


@pytest.fixture
def fake(monkeypatch):
    fc = FakeClient()
    monkeypatch.setattr(dart_client, "get_dart_client", lambda: fc)
    return fc


def _read(uri: str) -> str:
    async def go():
        out = await mcp.read_resource(uri)
        return "".join(c.content for c in out)
    return asyncio.run(go())


# ── 목차 ──

def test_mark_tree_marks_leaf_and_parent_path():
    toc = fs.mark_tree(MAIN_HTML)
    by = {e["no"]: e for e in toc}
    assert len(toc) == 8
    assert by["1"]["leaf"] and by["4"]["leaf"] and by["26"]["leaf"]
    assert not by["3"]["leaf"] and not by["17"]["leaf"] and not by["25"]["leaf"]
    assert by["26"]["path"] == ["III. 재무에 관한 사항", "3. 연결재무제표 주석"]
    assert by["26"]["node"]["eleId"] == "26" and by["26"]["fetchable"]


def test_toc_resource_lists_every_node_with_section_uri(fake):
    body = _read(f"opm://filing/{RNO}/toc")
    assert "8항목" in body and "본문 절 5" in body
    assert f"opm://filing/{RNO}/section/26" in body
    assert "▸ III. 재무에 관한 사항" in body and "하위 1" in body
    # 두 번째 읽기는 캐시 — main.do 재호출 없음
    _read(f"opm://filing/{RNO}/toc")
    assert fake.main_calls == 1


def test_toc_rejects_bad_rcept_no(fake):
    assert "14자리" in _read("opm://filing/123/toc")
    assert fake.main_calls == 0


# ── 절 ──

def test_leaf_section_renders_text_and_markdown_table(fake):
    body = _read(f"opm://filing/{RNO}/section/4")
    assert body.startswith("# I. 회사의 개요 › 1. 회사의 개요")
    assert "(기준일 : 2025년 12월 31일) (단위 : 사)" in body          # 표 앞 단위·기준일 보존
    assert "| 구분 | 회사수 |" in body and "| 비상장 | 2 |" in body      # 마크다운 표
    assert "주1) 각주 문장" in body                                   # 표 뒤 각주 보존
    assert "<TABLE" not in body and "<P>" not in body
    assert f"다음 절: opm://filing/{RNO}/section/5" in body
    assert "이전 절" in body and f"opm://filing/{RNO}/section/1" in body


def test_parent_number_returns_children_not_body(fake):
    body = _read(f"opm://filing/{RNO}/section/17")
    assert "하위 절을 묶는 상위 항목" in body
    assert f"25 ▸ 3. 연결재무제표 주석" in body and f"opm://filing/{RNO}/section/25" in body
    assert fake.section_calls == []                       # 부모는 뷰어를 부르지 않는다 (7.8MB 를 안 받는다)


def test_long_section_is_capped_and_continues_with_start(fake):
    first = _read(f"opm://filing/{RNO}/section/5")
    m = re.search(rf"이어 읽기: opm://filing/{RNO}/section/5\?start=(\d+)", first)
    assert m, first[-300:]
    cut = int(m.group(1))
    # 줄 끝에서 자른다 — 상한의 90~100% 사이, 표 행이 반 토막 나지 않게
    assert fs.SECTION_MAX_CHARS * 0.9 <= cut <= fs.SECTION_MAX_CHARS
    assert f"이 응답 1–{cut:,}자" in first
    assert first.rstrip().splitlines()[-3].strip().endswith("가나다라마바사")   # 본문 마지막 줄이 온전한 한 줄
    second = _read(f"opm://filing/{RNO}/section/5?start={cut}")
    assert f"이 응답 {cut + 1:,}–" in second
    assert fake.section_calls == ["5"]                    # 이어읽기는 캐시에서 — 뷰어 재호출 없음
    beyond = _read(f"opm://filing/{RNO}/section/5?start=99999999")
    assert "이어 읽기" not in beyond


def test_section_cache_holds_text_only(fake):
    _read(f"opm://filing/{RNO}/section/4")
    cached = fake._doc_cache.get(f"section:{RNO}:4")
    assert isinstance(cached, str) and "<" not in cached


def test_unknown_number_points_to_toc(fake):
    body = _read(f"opm://filing/{RNO}/section/999")
    assert "그런 절 번호가 없습니다" in body and f"opm://filing/{RNO}/toc" in body


def test_transport_error_is_retried_once(fake):
    fake.fail_first = True
    body = _read(f"opm://filing/{RNO}/section/26")
    assert "1969년 설립" in body
    assert fake.section_calls == ["26", "26"]


def test_section_fetch_passes_the_doc_gate(fake, monkeypatch):
    """전체 문이 꽉 차 있으면 절 읽기도 「busy」로 물러난다 — document.xml 경로와 같은 문."""
    monkeypatch.setattr(dart_client, "_DOC_GATE_WAIT_SEC", 0.2)
    monkeypatch.setattr(dart_client, "_doc_gate_sem", None)

    async def go():
        sem = dart_client._doc_gate()
        for _ in range(dart_client._DOC_GATE):
            await sem.acquire()
        try:
            out = await mcp.read_resource(f"opm://filing/{RNO}/section/27")
            return "".join(c.content for c in out)
        finally:
            for _ in range(dart_client._DOC_GATE):
                sem.release()
    body = asyncio.run(go())
    assert "busy" in body and fake.section_calls == []
    monkeypatch.setattr(dart_client, "_doc_gate_sem", None)


def test_full_text_resource_points_to_toc(fake, monkeypatch):
    async def fake_doc(rcept_no):
        return {"text": "본문 " * 10}
    fake.get_document_cached = fake_doc
    body = _read(f"opm://filing/{RNO}")
    assert body.startswith(f"[목차] opm://filing/{RNO}/toc")
