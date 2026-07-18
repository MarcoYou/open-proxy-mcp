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
                                   need_rows: int = 1, content_re=None) -> str | None:
    """II.사업의 내용의 특정 소절(kw_patterns 제목)을 통째 마크다운으로 렌더.

    소절 제목이 번호/한글자 접두를 가진 헤딩일 때만 앵커(프로즈 언급 오탐 방지). 명부(roster) 배제.
    content_re 주면 렌더된 구간이 그 필드 내용을 실제로 담을 때만 채택(부모/오섹션 오탐 차단) —
    이 content-gate 덕에 앵커를 넓게 잡아도 안전(넓은 앵커=놓침↓, gate=오탐↓).
    """
    if not html:
        return None
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
    # 비겹침 구간. 요약(II) + 상세(XII.상세표) 등 최대 2구간.
    parts, last_end = [], -1
    for s in sorted(set(hits)):
        if s < last_end:
            continue
        md = _render_html_region_md(html, max(0, s - 40), s + max_chars)
        if not (md and _md_has_data_rows(md, need_rows)):
            continue
        if content_re is not None and not content_re.search(md):
            continue                    # 렌더됐지만 그 필드 내용 아님(부모·오섹션) → 스킵
        parts.append(md)
        last_end = s + max_chars
        if len(parts) >= 2 or sum(len(p) for p in parts) > max_chars:
            break
    return "\n\n———\n\n".join(parts) if parts else None


# ─────────────────────────── 가동률 (utilization) ───────────────────────────
# 소절 제목 변형(자간 삽입·율/률·결합제목·슬래시). 앵커는 '전체 헤딩'을 잡아야 접두(나./3))가 붙음.
# content-gate(_C_UTIL)가 오섹션을 거르므로 앵커를 넓게 잡아 놓침을 줄인다(census 54 놓침 대응).
_UTIL_HEAD = [
    r"생산능력\s*[/·,]\s*(?:생산)?실적\s*[/·,]\s*가\s*동\s*[율률]",       # 3) 생산능력/실적/가동률 (한미반도체)
    r"생산능력[\s,]*(?:및\s*)?생산실적[\s,및]*가\s*동\s*[율률]",           # 나. 생산능력, 생산실적 및 가동률 (한화솔루션)
    r"생산능력\s*(?:및|,)\s*가\s*동\s*[율률]",                            # 나. 생산능력 및 가동률 (한국전력공사)
    r"생산실적\s*(?:및|,|/)?\s*가\s*동\s*[율률]",                          # 나. 생산실적 및 가동률/가동율 (오리온)
    r"당해\s*사업연도의?\s*가\s*동\s*[율률]",
    r"당기\s*가\s*동\s*[율률]",
    r"설비\s*가\s*동\s*[율률]",
    r"가\s*동\s*[율률]",                                                   # (2) 가동률 단독 (쎄트렉아이) — prefix+content-gate로 안전
    r"생산\s*및\s*설비(?:에?\s*관한\s*사항|\s*\(|의?\s*현황)?",            # 2. 생산 및 설비(1)생산능력 (HL만도)
    r"생산\s*능력\s*(?:및\s*생산능력의?\s*)?산출\s*근거",                  # 현대차
    r"생산\s*능력\s*(?:및|,)\s*(?:생산)?실적",                            # 3) 생산능력 및 실적 (한미약품 등 제약 — 가동시간表 동반, content-gate가 확인)
    r"평균\s*가\s*동\s*시간",                                             # (2) 평균 가동 시간 (한미약품 사업장별 가동일수·가동가능시간)
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

# content-gate 정규식: 렌더 구간이 실제 그 필드 내용을 담는지(부모/오섹션 오탐 차단)
_C_UTIL = re.compile(r"가\s*동\s*[율률]|가동\s*시간")
_C_SITE = re.compile(r"소재지|주소|사업장|사업소|공장|영업소|점포|㎡|[가-힣]{2}(?:시|도)\b|"
                     r"경기|서울|인천|부산|대구|대전|광주|울산|충청|전라|경상|강원|제주|베트남|중국|미국")
_C_RND = re.compile(r"연구개발")
_C_BL = re.compile(r"수주\s*(?:잔고|잔액|총액|상황|현황|계약)|기납품|계약잔액|납기|발주처")
_C_CUST = re.compile(r"고객|매출처|거래처|판매\s*경로|수요처")


def _field(biz_text: str, html: str, head_patterns: list[str], na_re, content_re=None,
           max_chars: int = 20000) -> dict:
    """markdown-primary: 소절 마크다운(content-gate 통과) 있으면 MARKDOWN, 없고 NA어휘면 N/A, 아니면 미검출."""
    md = render_biz_subsection_markdown(html, head_patterns, max_chars=max_chars, content_re=content_re)
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
    r"영업\s*설비(?:의?\s*현황)?",                          # 편의점(BGF리테일: '가. 영업설비' + 점포·사업장명)
    r"영업장\s*(?:의?\s*)?현황",                            # 호텔·면세(호텔신라: '나. 영업장 현황(요약)')
    r"주요\s*설비의?\s*현황",                               # 건설(대우건설: '나. 주요 설비의 현황')
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
    r = _field(biz_text, html, _UTIL_HEAD, _UTIL_NA, content_re=_C_UTIL, max_chars=18000)
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
    return _field(biz_text, html, _SITE_HEAD, _SITE_NA, content_re=_C_SITE)

def extract_rnd(biz_text, html):
    r = _field(biz_text, html, _RND_HEAD, _RND_NA, content_re=_C_RND, max_chars=24000)
    m = _RND_RATIO.search(biz_text or "")
    if m:
        r["ratio_to_sales_pct_hint"] = m.group(1)
    return r

def extract_backlog(biz_text, html):
    return _field(biz_text, html, _BL_HEAD, _BL_NA, content_re=_C_BL)

def extract_customers(biz_text, html):
    return _field(biz_text, html, _CUST_HEAD, _CUST_NA, content_re=_C_CUST)


# ═══════════ D-트랙: 금융·REIT 필드 (헤딩앵커 + 내용시그니처 폴백) ═══════════
# 사용자 지적(260718): 키워드 헤딩만이면 특이 헤딩에 무용지물 → 헤딩 미스 시 '데이터 시그니처'로
# 표를 찾아 렌더(헤딩라벨보다 안정적). segments의 table-scan 방식을 필드 일반화.

def _find_by_signature(html, signature_re, window=18000):
    """헤딩 못 찾을 때 폴백: signature 든 <table>(없으면 프로즈) 위치를 찾아 그 앞부터 렌더."""
    if not html:
        return None
    from open_proxy_mcp.services.segment_candidates import _TABLE_RE
    for m in _TABLE_RE.finditer(html):
        if signature_re.search(m.group(0)) and not _is_roster(m.group(0)):
            md = _render_html_region_md(html, max(0, m.start() - 1500), m.start() + window)
            if md and _md_has_data_rows(md, 2) and len(md) > 300:   # 폴백은 stricter(빈/tiny 렌더 배제)
                return md
    tm = signature_re.search(html)
    if tm:
        md = _render_html_region_md(html, max(0, tm.start() - 1200), tm.start() + window)
        if md and len(md) > 300:
            return md
    return None


def _field2(biz_text, html, head_patterns, content_re, signature_re, na_re, max_chars=18000):
    """헤딩앵커(content-gate) → 실패 시 내용시그니처 폴백 → N/A. source로 어느 경로인지 표기."""
    md = render_biz_subsection_markdown(html, head_patterns, max_chars=max_chars, content_re=content_re)
    if md:
        return {"status": "MARKDOWN", "source": "heading", "markdown": md}
    md = _find_by_signature(html, signature_re, max_chars)
    if md:
        return {"status": "MARKDOWN", "source": "signature", "markdown": md}
    na = na_re.search(biz_text) if (na_re and biz_text) else None
    return {"status": "NOT_APPLICABLE", "na_reason": (_strip_tags(na.group(0))[:60] if na else "해당 소절 미검출")}


# 금융 영업현황(영업부문별 재무정보=금융판 segments·영업개황·영업실적)
_FOPS_HEAD = [r"영업의?\s*현황", r"영업\s*개황", r"영업부문별\s*(?:재무정보|비중|현황)",
              r"영업의?\s*종류"]
# KSIC 게이트가 비금융을 이미 막으므로(오케스트레이터), content-gate는 서브타입 용어를 넓게 잡아
# '영업의 현황 섹션인지'만 확인(은행 순이자·증권 영업순수익·운용사 관리보수·보험 보험영업 다 커버).
_C_FOPS = re.compile(r"순이자|보험영업|영업순수익|운용보수|관리보수|성과보수|투자조합|운용조합|수탁고|"
                     r"수입보험료|영업부문별|예대율|지급여력|위탁매매|집합투자|영업수익|영업이익|약정")
_SIG_FOPS = re.compile(r"순이자손익|영업부문별\s*재무|은행\s*부문|보험영업손익|영업순수익|관리보수.{0,10}성과보수")
# 금융 재무건전성(지급여력·RBC·BIS·자본적정성)
_FSND_HEAD = [r"재무\s*건전성", r"지급\s*여력", r"자본\s*적정성"]
# KSIC 게이트가 비금융 배제하므로 '재무건전성 섹션인지'만 확인(서브타입 지표 넓게 + VC 자기자본류).
_C_FSND = re.compile(r"지급여력|K-ICS|BIS|RBC|순자본|영업용순자본|고정이하|연체|책임준비금|"
                     r"위험가중|건전성|자기자본|재무구조")
_SIG_FSND = re.compile(r"지급여력비율|K-ICS|RBC\s*비율|BIS\s*비율|고정이하여신|영업용순자본비율")
# REIT 투자부동산(투자부동산 내역·투자자산 개요)
# REIT마다 서식 상이(SK리츠=투자부동산 내역 / 롯데리츠=임대조건+프로즈). KSIC 68게이트라 넓게.
_IPROP_HEAD = [r"투자\s*부동산의?\s*(?:내역|현황)", r"투자\s*자산\s*개요", r"투자\s*대상\s*(?:자산|부동산)",
               r"부동산\s*(?:보유|투자)\s*현황", r"임대\s*조건", r"임대\s*현황", r"보유\s*부동산",
               r"주요\s*(?:자산|부동산)\s*현황"]
_C_IPROP = re.compile(r"임대율|공실|임대면적|임대\s*형태|투자부동산|임대료|임차인|책임임대차|연면적|임대\s*조건")
# 시그니처: REIT 특화(단순 '투자부동산' 계정언급 오발 방지 — 임대료·임차인·공실·책임임대차 동반)
_SIG_IPROP = re.compile(r"임대율|공실률|임대\s*형태|투자부동산의?\s*내역|책임임대차|"
                        r"임차인.{0,40}임대료|임대료.{0,20}배당")
_IPROP_NA = re.compile(r"투자부동산[^\n]{0,20}?(해당\s*사항\s*(?:이)?\s*없|없습니다)")
# 지주형 REIT(명목회사)가 표준(제조)폼에 부동산을 프로즈로 싣는 케이스(제이알글로벌·해외리츠 등):
# 부동산이 '2.주요 제품 및 서비스 → 영업개황'에 서술형(임대료·WALE·임차율)로 들어가 전용헤딩·표
# 시그니처가 다 놓친다. 전용경로 실패 시에만 표준폼 헤딩을 시도하되, content-gate를 강화
# (임대료/임대차/임차 + 부동산/투자대상/임대 동반)해 비REIT 오섹션(보험 영업개황 등)을 차단.
_IPROP_HEAD_STD = [r"주요\s*제품\s*및\s*서비스", r"영업\s*개황", r"회사의?\s*현황"]
_C_IPROP_PROSE = re.compile(r"(?:임대료|임대차|임차)[^\n]{0,300}"
                            r"(?:부동산|임차|임대|투자대상|잔여임대|WALE|공실|연면적|기초자산)")


def extract_financial_ops(biz_text, html):
    return _field2(biz_text, html, _FOPS_HEAD, _C_FOPS, _SIG_FOPS, None)

def extract_financial_soundness(biz_text, html):
    return _field2(biz_text, html, _FSND_HEAD, _C_FSND, _SIG_FSND, None)

def extract_investment_property(biz_text, html):
    r = _field2(biz_text, html, _IPROP_HEAD, _C_IPROP, _SIG_IPROP, _IPROP_NA)
    if r.get("status") == "MARKDOWN":
        return r
    # 지주형 REIT 표준폼 프로즈 폴백(전용헤딩·시그니처 실패 시에만 — 작동하는 REIT엔 영향 없음)
    md = render_biz_subsection_markdown(html, _IPROP_HEAD_STD, content_re=_C_IPROP_PROSE)
    if md:
        return {"status": "MARKDOWN", "source": "reit_prose", "markdown": md}
    na = _IPROP_NA.search(biz_text) if biz_text else None
    return {"status": "NOT_APPLICABLE",
            "na_reason": (_strip_tags(na.group(0))[:60] if na else "해당 소절 미검출")}
