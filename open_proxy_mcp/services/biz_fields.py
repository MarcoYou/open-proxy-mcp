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
    # 매칭 헤딩 위치 수집(번호/한글자 접두 + 명부 배제)
    hits = []
    for kw in kw_patterns:
        for m in re.finditer(kw, html):
            pre = _strip_tags(html[max(0, m.start() - 26):m.start()])
            if not _SUBSEC_PREFIX.search(pre):
                continue
            if _is_roster(html[m.start():m.start() + 6000]):
                continue
            hits.append(m.start())
    if not hits:
        return None
    # 겹치는 헤딩(한 구간 내 여러 매치) 병합 → 비겹침 구간. 요약(II) + 상세(XII.상세표) 등 최대 2구간.
    parts, last_end = [], -1
    for s in sorted(set(hits)):
        if s < last_end:
            continue
        md = _render_html_region_md(html, max(0, s - 40), s + max_chars)
        if md and _md_has_data_rows(md, need_rows):
            parts.append(md)
            last_end = s + max_chars
        if len(parts) >= 2 or sum(len(p) for p in parts) > max_chars:
            break
    return "\n\n———\n\n".join(parts) if parts else None


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


def extract_utilization(biz_text: str, html: str) -> dict:
    """가동률 markdown-primary + %힌트(안전할 때만, 비교금지). 정의는 _field 아래에서 재사용."""
    return _util_impl(biz_text, html)


# ═══════════ markdown-primary 공통 필드 추출 (사업장·rnd·backlog·customers) ═══════════
# 설계: 파서가 '진짜 X표인가' 판정하지 않는다. 소절을 통째 마크다운으로 렌더→호출측 AI가 읽음.
# 실패 모드는 '섹션 있는데 md=0'(앵커 미스)뿐이라 헤딩 패턴을 census 앵커로 넓게 잡는다.

def _field(biz_text: str, html: str, head_patterns: list[str], na_re, max_chars: int = 20000) -> dict:
    """markdown-primary: 소절 마크다운 있으면 MARKDOWN, 없고 NA어휘면 NOT_APPLICABLE, 아니면 미검출."""
    md = render_biz_subsection_markdown(html, head_patterns, max_chars=max_chars)
    if md:
        return {"status": "MARKDOWN", "markdown": md}
    na = na_re.search(biz_text) if (na_re and biz_text) else None
    return {"status": "NOT_APPLICABLE",
            "na_reason": (_strip_tags(na.group(0))[:60] if na else "해당 소절 미검출")}


# ── 사업장 (business sites) — 위치판정은 호출측 AI (유형자산 함정은 AI가 원문 읽어 구분) ──
_SITE_HEAD = [
    r"생산설비\s*및\s*투자\s*현황(?:\s*등)?",              # 삼성전자·케이티앤지·대한전선
    r"생산\s*설비의?\s*현황(?:\s*등)?",
    r"(?:주요\s*)?(?:국내|해외)?\s*사업장의?\s*현황",
    r"생산설비에?\s*관한\s*사항",
    r"생산\s*및\s*설비(?:에?\s*관한\s*사항|의?\s*현황(?:\s*등)?)?",
    r"생산과\s*영업에\s*중요한\s*(?:시설|물적)",
    r"영업용\s*설비\s*현황",                               # 유통(이마트·롯데쇼핑)
    r"물적\s*재산의?\s*(?:내용|현황)",                     # IT(NAVER 등)
]
_SITE_NA = re.compile(r"기재하지\s*않았|해당\s*사항\s*(?:이)?\s*없|외주\s*(?:가공|생산)|위탁\s*생산|"
                      r"\bOEM\b|인적자원을?\s*활용|별도의?\s*생산\s*(?:시설|설비)")


# ── rnd 연구개발 (hint=매출액대비 % + 계금액; 회계처리/보조금 분해는 마크다운이 담당) ──
_RND_HEAD = [
    r"연구개발\s*실적",
    r"연구개발\s*비용",
    r"연구개발\s*활동(?:의?\s*개요)?",
    r"주요계약\s*및\s*연구개발활동",
    r"연구개발\s*담당\s*조직",
]
_RND_RATIO = re.compile(r"연구개발비\s*/?\s*(?:매출액|영업수익)\s*비율[^\d\n]{0,30}?([\d]{1,3}(?:\.\d+)?)\s*%")
_RND_NA = re.compile(r"연구개발\s*활동[^\n]{0,30}?(해당\s*사항\s*(?:이)?\s*없|없습니다)")


# ── backlog 수주 (value hint 없음 — flow표 오귀속 방지, QA BLOCKER. 마크다운만) ──
_BL_HEAD = [
    r"진행률\s*적용?\s*수주계약\s*현황",
    r"수주\s*계약\s*현황",
    r"수주\s*(?:상황|현황)",
    r"매출\s*및\s*수주\s*상황",
]
_BL_NA = re.compile(r"수주[^\n]{0,20}?(해당\s*사항\s*(?:이)?\s*없|없습니다)")


# ── customers 주요고객/매출처 (hint=집중률 % 안전할 때만; 이름은 마크다운) ──
_CUST_HEAD = [
    r"주요\s*매출처(?:\s*(?:및\s*매출\s*비중|현황))?",
    r"주요\s*고객에\s*대한\s*(?:정보|공시)",
    r"주요\s*(?:거래처|수요처)",
    r"판매\s*경로(?:\s*및\s*판매\s*방법)?",
]
_CUST_NA = re.compile(r"(?:주요\s*(?:매출처|고객)|판매\s*경로)[^\n]{0,30}?(해당\s*사항\s*(?:이)?\s*없|없습니다)")


def _util_impl(biz_text, html):
    r = _field(biz_text, html, _UTIL_HEAD, _UTIL_NA, max_chars=18000)
    hm = None
    for kw in _UTIL_HEAD:
        hm = re.search(kw, biz_text or "")
        if hm:
            break
    region = biz_text[hm.start():hm.start() + 5000] if hm else (biz_text or "")
    pv = [mm.group(1) for mm in _UTIL_PCT.finditer(region)]
    if pv:
        r["pct_hint"] = pv[:6]
        r["comparable"] = False
    return r


def extract_sites(biz_text, html):
    return _field(biz_text, html, _SITE_HEAD, _SITE_NA)

def extract_rnd(biz_text, html):
    r = _field(biz_text, html, _RND_HEAD, _RND_NA, max_chars=24000)
    m = _RND_RATIO.search(biz_text or "")
    if m:
        r["ratio_to_sales_pct_hint"] = m.group(1)
    return r

def extract_backlog(biz_text, html):
    return _field(biz_text, html, _BL_HEAD, _BL_NA)

def extract_customers(biz_text, html):
    return _field(biz_text, html, _CUST_HEAD, _CUST_NA)
