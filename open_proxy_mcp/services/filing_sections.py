"""공시 원문을 **절 단위**로 읽는다 — `opm://filing/{rcept_no}/toc` · `opm://filing/{rcept_no}/section/{no}{?start}`.

왜 (260906 실측, wiki/handoff/260906_filing-section-resources.md):
  `opm://filing/{rcept_no}` 는 document.xml 전문을 12만 자에서 자른다. 사업보고서는 82만~165만 자라
  III 장(재무)에서 잘리고, 파서 없는 질문 대부분(직원 현황·계열회사·우발부채·제재)은 그 뒤에 있다.
  DART 뷰어 목차는 장 › 절 › 항 3계층(문서당 95~172 노드)이고 항 하나는 중앙 1~3천 자라 절 단위면 닿는다.

실측이 정한 설계 (11개 사업보고서, leaf 1,358절):
  · **leaf 기본.** viewer.do 는 부모 노드를 부르면 하위를 통째로 준다(부모 글자 수 = 자식 합). 부모 번호가
    오면 본문 대신 하위 목록을 준다 — 부모를 그대로 받으면 금융사 III 장 하나가 HTML 7.8MB · RSS +83MB 다.
  · **캐시는 정제 텍스트만.** html 은 text 의 5~7배(KB금융 leaf 26MB vs 3.3MB). 문서당 text 는 0.2~3.3MB.
  · **절당 상한 4만 자 + `?start=` 이어읽기.** leaf 의 2.4%(32/1,358)가 4만 자를 넘고 최대 23만 자
    (「증권의 발행을 통한 자금조달」). 나머지는 한 번에 온다.
  · **전송 오류 재시도 1회.** 1,358절 중 2절이 30초 ReadTimeout 이었고 재시도에서 받혔다. HTTP 상태 오류는 재시도 안 함.
  · **doc_gate 를 지난다.** leaf 요청 하나의 RSS 피크는 ≤ 9MB 지만 문서 수신·파싱 동시 상한은 프로세스 규칙이다.
  · 웹 간격은 `_throttle_web`(공유 시계) 그대로 — 요청 하나에 절 하나.

본문을 document.xml 에서 자르지 않는 이유 (260906): `<TITLE>` 순서로 자르면 결과는 같고(표본 18절 숫자 100% 공통) 절당
  0초지만, XML 이 23~27M 자라 한 문서 로드에 RSS 가 금융사 +200MB 다. 1GB VM 에서 둘 겹치면 260901 OOM 모양. 뷰어는 절
  하나 ≤ 9MB. VM 을 키우거나 XML 을 스트리밍으로 자르게 되면 다시 본다.

`from=` 이 아니라 `start=` 인 이유: MCP SDK 는 URI 템플릿 변수 이름을 함수 인자 이름과 맞추는데 `from` 은 파이썬 예약어다.
"""
from __future__ import annotations

import re

import httpx

from open_proxy_mcp.dart.client import DartClientError, _note_doc, html_to_text
from open_proxy_mcp.services.business_details import _extract_node_tree, _node_fetchable
from open_proxy_mcp.services.shareholder_meeting_parser import _table_to_markdown

#: 절 하나의 응답 상한(글자). 넘으면 `?start=` 로 이어 읽는다.
SECTION_MAX_CHARS = 40_000
_TBL = "@@OPMTBL{}@@"


def toc_uri(rcept_no: str) -> str:
    return f"opm://filing/{rcept_no}/toc"


def section_uri(rcept_no: str, no: str, start: int | None = None) -> str:
    base = f"opm://filing/{rcept_no}/section/{no}"
    return f"{base}?start={start}" if start else base


def origin_hint(rcept_no: str, title: str | None = None, no: str | None = None) -> str:
    """도구 결과가 **원문 절 주소를 글자로** 적는 한 줄. 파싱이 약하거나 「찾지 못함」일 때 붙인다.

    260906 실측: Claude.ai 커넥터는 resource 목록은 못 보지만 **URI 를 알면 읽는다.** 그래서 도구가
    주소를 적어 줘야 AI 가 목차를 거치지 않고 절로 간다. 절 번호(`no`)를 알면 절 주소를, 모르면
    목차 주소 + 찾을 제목을 준다. 주소를 만들려고 뷰어를 새로 부르지는 않는다(웹 0회).
    """
    if not rcept_no:
        return ""
    if no:
        return f"원문 절: {section_uri(rcept_no, no)}" + (f" 「{title}」" if title else "")
    where = f" — 목차에서 「{title}」 절을 골라" if title else " — 목차에서 절을 골라"
    return f"원문 절 단위: {toc_uri(rcept_no)}{where} {section_uri(rcept_no, '{no}')} 로 읽는다"


def _toc_key(rcept_no: str) -> str:
    return f"toc:{rcept_no}"


def _section_key(rcept_no: str, no: str) -> str:
    return f"section:{rcept_no}:{no}"


# ── 목차 ──

def mark_tree(main_html: str) -> list[dict]:
    """main.do treeData 전 계층 → 절 목록. 각 절에 `leaf`(하위 없음) · `path`(상위 제목들) · `node`(뷰어 좌표).

    `no` 는 뷰어 `tocNo`(목차 순번). 없으면 순서 번호로 대신한다.
    """
    raw = _extract_node_tree(main_html)
    out: list[dict] = []
    stack: dict[int, str] = {}
    for i, n in enumerate(raw):
        lvl = int(n.get("lvl") or 1)
        nxt = raw[i + 1] if i + 1 < len(raw) else None
        leaf = nxt is None or int(nxt.get("lvl") or 1) <= lvl
        for d in [d for d in stack if d >= lvl]:
            del stack[d]
        path = [stack[d] for d in sorted(stack)]
        stack[lvl] = n.get("text", "")
        try:
            length = int(n.get("length") or 0)
        except ValueError:
            length = 0
        out.append({
            "no": n.get("tocNo") or str(i + 1),
            "level": lvl,
            "text": n.get("text", ""),
            "leaf": leaf,
            "length": length,
            "path": path,
            "fetchable": _node_fetchable(n),
            "node": {k: n[k] for k in ("rcpNo", "dcmNo", "eleId", "offset", "length", "dtd") if k in n},
        })
    return out


def children_of(toc: list[dict], idx: int) -> list[dict]:
    """직계 하위 절만 (같은 부모 아래 level+1)."""
    lvl = toc[idx]["level"]
    kids = []
    for e in toc[idx + 1:]:
        if e["level"] <= lvl:
            break
        if e["level"] == lvl + 1:
            kids.append(e)
    return kids


async def _retry_transport(fetch):
    """전송 오류(타임아웃·연결 끊김)만 한 번 더. HTTP 상태 오류는 그대로 올린다."""
    try:
        return await fetch()
    except httpx.TransportError:
        _note_doc("viewer_retries")
        return await fetch()


async def get_toc(client, rcept_no: str) -> list[dict]:
    """뷰어 목차(전 계층). 캐시 `toc:{rcept_no}` — 문서당 수십 KB."""
    key = _toc_key(rcept_no)
    cached = client._doc_cache.get(key)
    if cached is not None:
        _note_doc("toc_hits")
        return cached
    _note_doc("toc_misses")
    main_html = await _retry_transport(lambda: client._fetch_viewer_main_html(rcept_no))
    toc = mark_tree(main_html)
    if not toc:
        raise DartClientError("NO_VIEWER_NODES", f"DART viewer 목차를 찾지 못했습니다. (rcept_no={rcept_no})")
    client._doc_cache.put(key, toc)
    return toc


def _kb(n: int) -> str:
    return f"{n / 1024:,.0f}KB" if n >= 1024 else f"{n}B"


def render_toc(rcept_no: str, toc: list[dict]) -> str:
    n_leaf = sum(1 for e in toc if e["leaf"])
    example = section_uri(rcept_no, "{no}")
    L = [
        f"# 공시 {rcept_no} 목차 — {len(toc)}항목 (본문 절 {n_leaf})",
        "",
        f"- 절 하나 읽기: `{example}` — 아래 번호를 넣는다. 응답은 정제 텍스트 + 마크다운 표.",
        f"- 절당 상한 {SECTION_MAX_CHARS:,}자. 넘치면 응답 끝의 `?start=` 주소로 이어 읽는다.",
        "- ▸ 표시는 하위 절을 묶는 항목 — 본문 대신 하위 목록을 준다. 본문은 하위 절에 있다.",
        "- 크기는 뷰어 원문(HTML) 기준이라 텍스트는 그보다 작다(대략 1/5).",
        "",
    ]
    for i, e in enumerate(toc):
        indent = "  " * (e["level"] - 1)
        mark = "" if e["leaf"] else "▸ "
        tail = f" · 하위 {len(children_of(toc, i))}" if not e["leaf"] else ""
        L.append(f"{indent}- {e['no']} {mark}{e['text']} ({_kb(e['length'])}{tail}) → {section_uri(rcept_no, e['no'])}")
    return "\n".join(L)


# ── 절 ──

def html_to_markdown(html: str) -> str:
    """절 HTML → 정제 텍스트 + 마크다운 표. 표 앞뒤의 단위·기준일·각주 문장은 그대로 남는다."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    blocks: list[str] = []
    # 🔴 DART 원문은 절 전체를 **셀 하나짜리 바깥 표**로 감싸고 그 안에 진짜 표를 둔다(document.xml 실측,
    #    삼성전자 주석 「1. 일반적 사항」: 바깥 표 1행에 안쪽 셀 1,486개 → md 226만 자). 표를 품은 표는
    #    껍데기다 — 벗겨서 안쪽 표만 표로 만든다. 뷰어 HTML 은 이미 벗겨져 오므로 그대로 통과한다.
    for t in soup.find_all("table"):
        if t.find("table") is not None:
            t.unwrap()
    for t in soup.find_all("table"):
        blocks.append(_table_to_markdown(t))
        t.replace_with(soup.new_string(f"\n\n{_TBL.format(len(blocks) - 1)}\n\n"))
    text = html_to_text(str(soup))
    text = re.sub(r"@@OPMTBL(\d+)@@", lambda m: blocks[int(m.group(1))], text)
    return text.strip()


def _neighbors(toc: list[dict], idx: int) -> tuple[dict | None, dict | None]:
    """앞뒤 **본문 절**(leaf). 부모 항목은 건너뛴다."""
    prev = next((e for e in reversed(toc[:idx]) if e["leaf"]), None)
    nxt = next((e for e in toc[idx + 1:] if e["leaf"]), None)
    return prev, nxt


async def get_section(client, rcept_no: str, no: str) -> dict:
    """절 하나. 반환 `status`: ok · parent(하위 목록) · unknown_no · not_fetchable."""
    toc = await get_toc(client, rcept_no)
    idx = next((i for i, e in enumerate(toc) if e["no"] == no), None)
    if idx is None:
        return {"status": "unknown_no", "toc": toc}
    e = toc[idx]
    prev, nxt = _neighbors(toc, idx)
    base = {"entry": e, "prev": prev, "next": nxt}
    if not e["leaf"]:
        return {"status": "parent", "children": children_of(toc, idx), **base}
    if not e["fetchable"]:
        return {"status": "not_fetchable", **base}
    key = _section_key(rcept_no, no)
    text = client._doc_cache.get(key)
    if text is not None:
        _note_doc("section_hits")
        return {"status": "ok", "text": text, **base}
    _note_doc("section_misses")
    async with client.doc_gate_slot():
        html = await _retry_transport(lambda: client._fetch_viewer_section_html(e["node"]))
        text = html_to_markdown(html)
        del html                                    # 텍스트만 남긴다 — html 은 5~7배
    client._doc_cache.put(key, text)
    return {"status": "ok", "text": text, **base}


def _link(rcept_no: str, e: dict | None, label: str) -> str | None:
    if e is None:
        return None
    return f"{label}: {section_uri(rcept_no, e['no'])} 「{e['text']}」"


def _cut_at_line(text: str, start: int, limit: int) -> int:
    """`start` 부터 `limit` 자 안에서 **줄 끝**으로 자른다 — 표 행이 반 토막 나지 않게.
    마지막 10% 안에 줄바꿈이 없으면(한 줄이 4천 자를 넘는 표) 글자 수로 자른다."""
    total = len(text)
    end = min(total, start + limit)
    if end == total:
        return end
    nl = text.rfind("\n", start + int(limit * 0.9), end)
    return nl + 1 if nl != -1 else end


def render_section(rcept_no: str, sec: dict, start: int = 0) -> str:
    st = sec["status"]
    if st == "unknown_no":
        nos = ", ".join(e["no"] for e in sec["toc"][:12])
        return (f"[{rcept_no}] 그런 절 번호가 없습니다. 목차 {toc_uri(rcept_no)} 에서 번호를 고르세요. "
                f"(예: {nos}, …)")
    e = sec["entry"]
    title = " › ".join([*e["path"], e["text"]])
    head = [f"# {title}", "", f"- 접수번호 {rcept_no} · 절 {e['no']} · 목차 {toc_uri(rcept_no)}"]
    nav = [x for x in (_link(rcept_no, sec.get("prev"), "이전 절"), _link(rcept_no, sec.get("next"), "다음 절")) if x]
    if nav:
        head.append("- " + " · ".join(nav))
    if st == "parent":
        head += ["", "이 항목은 하위 절을 묶는 상위 항목이라 본문이 따로 없습니다. 아래 하위 절을 읽으세요.", ""]
        for c in sec["children"]:
            mark = "" if c["leaf"] else "▸ "
            head.append(f"- {c['no']} {mark}{c['text']} ({_kb(c['length'])}) → {section_uri(rcept_no, c['no'])}")
        return "\n".join(head)
    if st == "not_fetchable":
        head += ["", "뷰어가 이 절의 본문 좌표를 주지 않아 받을 수 없습니다(첨부·외부 문서일 수 있음). "
                     f"전문은 opm://filing/{rcept_no} 에서 찾아보세요."]
        return "\n".join(head)
    text = sec["text"]
    total = len(text)
    start = max(0, min(start, total))
    end = _cut_at_line(text, start, SECTION_MAX_CHARS)
    rng = f"{start + 1:,}–{end:,}" if total else "0"
    head.append(f"- 글자 수 {total:,} · 이 응답 {rng}자")
    body = text[start:end].rstrip("\n")
    tail = []
    if end < total:
        tail = ["", f"…(이후 {total - end:,}자) 이어 읽기: {section_uri(rcept_no, e['no'], end)}"]
    return "\n".join(head + ["", body] + tail)
