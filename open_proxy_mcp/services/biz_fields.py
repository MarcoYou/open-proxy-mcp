"""II.사업의 내용 추가 필드(사업장·가동률·rnd·backlog·customers) — markdown-primary 추출.

설계(260718 census 286사 + 재무·공시·산업 QA패널 결론): **markdown-primary**.
- 신뢰경로 = 해당 소절 원문을 통째 **마크다운**으로 반환 → 호출측 강력 AI가 읽어 추출([[feedback_leverage_caller_ai]]).
- 정형 hint = 애매하지 않을 때만 아주 작게(비-authoritative). "이게 진짜 X표인가" 판정 게이트는 두지 않음
  (파서가 판정하면 조용한 오답 — 사업장 유형자산 함정·가동률 단위카오스는 호출측 AI가 원문 읽어 구분).
마크다운은 get_document HTML에서, hint는 biz 텍스트에서.
"""
from __future__ import annotations

import re

from open_proxy_mcp.services.segment_candidates import (
    _render_html_region_md, _strip_tags, _md_has_data_rows, _is_roster,
)

# 소절 접두(번호/한글자/괄호): "2." "나." "(2)" "2)" 목차 제목 앞에 오는 마커
_SUBSEC_PREFIX = re.compile(r"(?:\d{1,2}|[가-하]|\(\s*\d{1,2}\s*\)|\d{1,2}\s*\))\s*[.)]?\s*$")


def render_biz_subsection_markdown(html: str, kw_patterns: list[str], max_chars: int = 22000,
                                   need_rows: int = 1) -> str | None:
    """II.사업의 내용의 특정 소절(kw_patterns 제목)을 통째 마크다운으로 렌더.

    소절 제목이 번호/한글자 접두를 가진 헤딩일 때만 앵커(프로즈 언급 오탐 방지). 명부(roster) 배제.
    kw_patterns는 '전체 헤딩'을 잡도록(접두 바로 뒤에 오게) 작성 — 가장 구체적 패턴부터.
    """
    if not html:
        return None
    for kw in kw_patterns:
        for m in re.finditer(kw, html):
            pre = _strip_tags(html[max(0, m.start() - 26):m.start()])
            if not _SUBSEC_PREFIX.search(pre):
                continue
            seg = html[m.start():m.start() + max_chars]
            if _is_roster(seg[:6000]):
                continue
            start = max(0, m.start() - 40)
            md = _render_html_region_md(html, start, start + max_chars)
            if md and _md_has_data_rows(md, need_rows):
                return md
    return None


# ─────────────────────────── 가동률 (utilization) ───────────────────────────
# 소절 제목 변형(자간 삽입·율/률·결합제목·슬래시). 앵커는 '전체 헤딩'을 잡아야 접두(나./3))가 붙음.
_UTIL_HEAD = [
    r"생산능력\s*[/·,]\s*(?:생산)?실적\s*[/·,]\s*가\s*동\s*[율률]",       # 3) 생산능력/실적/가동률 (한미반도체)
    r"생산능력[\s,]*(?:및\s*)?생산실적[\s,및]*가\s*동\s*[율률]",           # 나. 생산능력, 생산실적 및 가동률 (한화솔루션)
    r"생산실적\s*(?:및|,|/)?\s*가\s*동\s*[율률]",                          # 나. 생산실적 및 가동률/가동율 (오리온)
    r"당해\s*사업연도의?\s*가\s*동\s*[율률]",
    r"당기\s*가\s*동\s*[율률]",
    r"설비\s*가\s*동\s*[율률]",
    r"생산\s*및\s*설비에?\s*관한\s*사항",
]
# 값 hint는 %만 보수적으로(단위 없는 '1개'·'1?' 노이즈 배제). 시간/톤 등은 마크다운이 담당.
_UTIL_PCT = re.compile(r"(?:평균\s*|가중평균\s*|설비\s*)?가\s*동\s*[율률]"
                       r"[^\d\n]{0,12}?약?\s*([\d]{1,3}(?:\.\d+)?)\s*%")
_UTIL_NA = re.compile(r"가\s*동\s*[율률][^\n]{0,60}?"
                      r"(기재하지\s*않았|산정할?\s*수\s*없|일률적으로\s*산출.{0,10}곤란|"
                      r"해당\s*사항\s*(?:이)?\s*없|보안\s*관계상|정보\s*유출)")


def extract_utilization(biz_text: str) -> dict:
    """가동률 hint(비-authoritative, %만): 값·N/A사유. 마크다운이 신뢰경로."""
    if not biz_text:
        return {"status": "NOT_COLLECTED"}
    hm = None
    for kw in _UTIL_HEAD:
        hm = re.search(kw, biz_text)
        if hm:
            break
    region = biz_text[hm.start(): hm.start() + 5000] if hm else biz_text
    vals = [{"value": mm.group(1), "unit": "%", "raw": _strip_tags(mm.group(0))[:40].strip()}
            for mm in _UTIL_PCT.finditer(region)]
    if vals:
        return {"status": "HINT", "pct_values": vals[:8], "comparable": False,
                "note": "정형 %힌트일 뿐 — 단위·정의 firm간 상이(비교금지), 마크다운 원문 확인"}
    na = _UTIL_NA.search(region if hm else biz_text)
    return {"status": "NOT_APPLICABLE" if na else "NEEDS_MARKDOWN",
            "na_reason": (_strip_tags(na.group(0))[:60] if na else None)}
