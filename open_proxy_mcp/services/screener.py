"""범용 공시 스크리너 — '무엇이 떴나'(scan, 싸게) + 필요 건만 '숫자'(details, 파서 디스패치).

대전제: 1순위 유즈 = **매일 아침 출근길 공시 알람 디제스트**. 전날(직전 실행 이후)~오늘
전체시장 주요 공시를, 폰에서 훑기 좋은 카드형 요약으로. 벤치마크 = 텔레그램 AWAKE.

아키텍처 핵심 사실(스카우트로 실측):
- `client.search_filings(bgn_de,end_de,pblntf_detail_ty,...)`는 **corp_code 없이 시장 전체**
  필러를 100/page로 반환(2026-07-14 I001 하루 102건=2페이지). universe는 메모리 사후필터.
- list.json item 필드 = corp_code / corp_name / stock_code / corp_cls / report_nm / rcept_no
  / flr_nm / rcept_dt / rm. **정정은 report_nm 프리픽스 `[기재정정]`/`[첨부정정]`**(rm은 시장마커 코/유).
- 시총은 일별 시세 저장분의 최신 거래일 단면(`krx_daily_v`, ticker=6자리 단축코드, mktcap=종가×상장주식수 원,
  DART 0콜)에서 배치 파생(261018 — 종전 `krx_weekly` 이번 주 행. 아래 universe 절 참조).
- 유형 판별은 report_nm 키워드(B001은 `주요사항보고서(…)` 괄호 안 사유가 판별자).

게이트는 universe가 아니라 **details**. scan=발견/details=숫자.
"""

from __future__ import annotations
from open_proxy_mcp.dart.client import (MARKET_WINDOW_MAX_MONTHS, LruByteCache, _env_mb,
                                        list_pages_per_code, market_window_start,
                                        note_degradation)
from open_proxy_mcp.db import pg_rows
from open_proxy_mcp import krx_daily as _krx_daily   # 서빙 기준일 정의(`latest_day`) — DB·순수 함수만 있는 모듈이라 순환 없음
from open_proxy_mcp import index_members as _index_members   # 지수 구성종목 원장(코스피200·코스닥150) — DB 읽기만
from open_proxy_mcp.market_codes import KS as MKT_KS, KQ as MKT_KQ, to_db, to_label

from open_proxy_mcp.services.contracts import build_usage, declare_weak_resolution, AnalysisStatus

import asyncio
import math
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.amendment_table import card_summary, parse_amendment_table
from open_proxy_mcp.services.company import resolve_company_query, company_ambiguous_warning, company_not_found_warning

# ── KST(공시 기준 시간대) ──────────────────────────────────────────────
_KST = timezone(timedelta(hours=9))

# 시장스캔 하드캡은 client 의 SSOT(`market_window_start`) 가 쥔다 — 일수가 아니라 3역월이다.

# scan 상한
# 코드당 최대 페이지. 값은 client 의 요청당 예산(`LIST_PAGE_BUDGET_PER_REQUEST`)을 스캔 코드
# 수로 나눈 몫이다 — 같은 list.json 을 쓰는 risk_events 와 예산이 10배 어긋나 있던 것(20 vs 200)을
# 한 곳에서 유도하게 바꿨다(260909). 코드가 늘어도 한 요청의 총 콜은 그대로다.
# 실제 절단 발생률은 `scan_page_truncated` 계기로 재고 있고, 예산 자체를 올릴지는 그 수치로 정한다.
_PAGE_COUNT = 100

# ── 260824: 호출측 sleep 을 걷어내고 **클라이언트 스로틀에 맡긴다** ──────────────
#
# 종전엔 페이지마다 0.7초, 상세마다 0.8초를 여기서 쉬었다. 실측하니 그 대기가 응답의
# **87%**(kospi200·details=ON 42.3초 중 36.7초)였고, 실사용 p95 116초·최대 306초의 몸통이었다.
# 5분이면 클라이언트가 먼저 끊으므로 그 응답은 아무에게도 닿지 않는다.
#
# 걷어내도 되는 근거 — 한도는 **지켜야 할 곳에서 이미 지키고 있다**:
#   · `_throttle_api`  롤링 60초 윈도우 910/분 + 최소간격, `_api_rate_lock` 으로 직렬화.
#     실측: 동시 100건 → 실효 903/분, 최소간격 위반 0건.
#   · `_throttle_scrape` 웹·KIND 1~2초 랜덤 공유 시계. **260824 에 락을 채웠다** —
#     그전엔 동시 4건에서 간격 0.299초·0.151초로 하한 1.0초를 뚫었다. 호출측 sleep 이
#     그 결함을 가리고 있었던 셈이라, 가린 것을 걷어내려면 결함부터 고쳐야 했다.
#
# 그래서 여기서는 **양을 제한**(캡)하고 **속도는 클라이언트가** 잡는다. 두 곳에서 같은 일을
# 하면 한쪽만 고쳐진다.
# ── 260902: 캡을 「유니버스」가 아니라 「이번 페이지」에 건다 ──────────────────
#
# 종전엔 유니버스가 300종목을 넘으면 details 를 통째로 껐다. 그 가드가 필요했던 이유는
# details 를 **자르기 전에** 돌렸기 때문이다 — 200건만 돌려줄 참이어도 문서는 전부 열었다.
# 260902 에 details 를 페이징 뒤로 옮겼으므로 비용은 유니버스가 아니라 **이번에 돌려주는
# 건수**에 비례한다. 그래서 캡도 그쪽으로 옮긴다.
#
# 한 응답이 쓰는 콜을 1,500(910/분 기준 약 100초어치)으로 묶고, 넘는 만큼은 자르지 않고
# `next_offset` 으로 넘긴다. **이 값을 더 올리면 안 된다** — 60초 창은 사용자별이 아니라
# 키 하나에 붙어 있어서, 한 요청이 오래 붙잡을수록 아침 디제스트와 공시 폴러가 같이 굶는다.
_DETAILS_TOTAL_CALL_CAP = 1500  # 페이지당 DART 콜 러닝카운터
#: 상세 동시성. 웹 폴백이 섞이면 `_throttle_scrape` 락에서 알아서 줄을 선다 —
#: 올려도 웹 예절이 깨지지 않고, API 만 쓰는 건은 그만큼 빨라진다.
_DETAILS_CONCURRENCY = 6
#: 스캔 코드 동시성. 코드 5개는 서로 독립이라 순차로 돌 이유가 없었다.
_SCAN_CONCURRENCY = 5
#: `details` 를 말하지 않은 호출에서 숫자(금액·시총 대비)를 기본으로 싣는 문턱 — 유니버스가 이 종목 수 이하이고
#: 기간이 이 일수 이하일 때만 켠다(작은 호출은 콜이 얼마 안 들고, 금액이 빠진 카드는 숫자를 보려고 도구를 한 번 더 부르게 한다).
#: 켜도 콜은 기존 `_DETAILS_TOTAL_CALL_CAP` 안에서만 쓴다. `details=false` 를 명시하면 켜지 않는다.
_AUTO_DETAILS_MAX_UNIVERSE = 200
_AUTO_DETAILS_MAX_DAYS = 7

# ── 스캔 결과 캐시 (260824) ──────────────────────────────────────────────────
#
# 스캔은 **공시유형 × 기간**만으로 정해진다 — 누가 물었는지와 무관하게 답이 같은 시장 데이터다
# (CLAUDE.md 의 「시장 snapshot」 인프라 예외). 그래서 키별이 아니라 **전역**으로 나눠 쓴다.
# 실측: 한 사용자가 08-23 에 58건, 08-18 에 35건을 몰아 썼다. 그 안에서 universe·types·details
# 만 바꾸는 탐색이 대부분이라 스캔은 같은 것을 반복해서 받고 있었다.
#
# ★ **페이지 단위로 캐시하면 안 된다.** 새 공시가 들어오면 페이지 경계가 밀리므로, 캐시된
#   1페이지와 새로 받은 2페이지를 섞으면 같은 건이 두 번 들어오거나 사이가 빈다.
#   그래서 `_scan_code` **한 코드의 결과를 통째로** 담는다 — 한 시점에서 온 것끼리만 합쳐진다.
#
# ★ 부분 실패(err)는 담지 않는다. 담으면 그 순간의 장애가 TTL 동안 굳는다.
#
# ★ 수명은 **창이 닫혔나**로 가른다 — 한 값으로 정할 문제가 아니었다.
#
#   · 끝날짜가 오늘  = 살아 있는 창. 지금도 공시가 들어온다 → 짧게.
#   · 끝날짜가 과거  = 닫힌 창. **그 구간의 답은 더 안 변한다** — 공시는 접수일로 색인되고
#     정정도 새 접수번호(오늘 날짜)를 받아 과거 창에 안 들어온다 → 길게.
#
# 살아 있는 창을 180초로 잡은 근거(260824 실측):
#   호출 간격이 양극단이다 — p50 18초 · p75 98초인데 p90 은 27분으로 뛴다. 그래서 이득의
#   대부분이 첫 2~3분에 나오고 그 뒤로는 신선도만 잃는다.
#     TTL  1분 71.6% 적중 / 2분 77.1% / 3분 80.7% / 5분 85.3% / 10분 88.1% / 30분 91.7%
#   유입은 영업일 평균 254건(0.38건/분)이고 균일하지 않다(점심 9분간 0건 실측, 장 마감 후 몰림).
#   3분이면 평균 0.6건·피크 1.2건을 놓친다. 5분은 적중 4.6%p 더 얻고 놓침이 두 배가 된다.
_SCAN_TTL_LIVE = float(os.environ.get("OPM_SCAN_CACHE_TTL_SEC", "180") or 180)
#: 닫힌 창. 「안 변한다」를 완전히 믿지는 않는다 — 뒤늦은 등록·재색인 여지를 두고 1시간.
_SCAN_TTL_CLOSED = float(os.environ.get("OPM_SCAN_CACHE_TTL_CLOSED_SEC", "3600") or 3600)
_SCAN_CACHE = LruByteCache(_env_mb("OPM_SCAN_CACHE_MB", 24), _SCAN_TTL_CLOSED, "screener_scan")


def _scan_ttl(end_de: str) -> float:
    """끝날짜가 오늘이면 짧게, 과거면 길게."""
    return _SCAN_TTL_LIVE if end_de >= _yyyymmdd(_today_kst()) else _SCAN_TTL_CLOSED

# ── 정정 프리픽스 감지 ──────────────────────────────────────────────
_CORRECTION_RE = re.compile(r"^\s*\[[^\]]*정정[^\]]*\]")


def _normalize_report_nm(report_nm: str) -> str:
    """정정 프리픽스·공백·괄호 부가설명을 벗겨 키워드 매칭용 core를 만든다."""
    nm = _CORRECTION_RE.sub("", report_nm or "").strip()
    # 트레일링 부가설명 괄호는 남겨두되 매칭 시 공백만 제거
    return nm.replace(" ", "").replace("ㆍ", "").replace("·", "")


# ══════════════════════════════════════════════════════════════════════
#  유형 레지스트리
#  각 유형: code/label/tier/scan_code(어느 pblntf_detail_ty에서 잡히나)
#           matchers=[(subtype_label, [normalized keyword,...]), ...] (우선순위 순)
#           max_items(details 캡), detail_kind(파서 디스패치 키; None=scan-only)
#           force_keywords(정정 외에 details 강제하는 단계 키워드)
#  ⚠ 순서 = 분류 우선순위(구체 → 일반). 첫 매치가 이긴다.
# ══════════════════════════════════════════════════════════════════════

CORE_PRESET = ["order", "treasury", "dividend", "dilutive", "agm_notice", "ownership5", "earnings"]
GOVERNANCE_PRESET = ["tender_offer", "proxy_solicitation", "control_change", "litigation",
                     "treasury", "ownership5", "restructuring", "stake_deal"]

# details 대상 Tier1 여섯 + scan-only Tier2/3. scan_codes 합집합이 실제 스캔 코드가 된다.
TYPE_REGISTRY: list[dict[str, Any]] = [
    # ── Tier1 (details 지원) ──────────────────────────────────────────
    {
        "code": "order", "label": "수주(단일판매·공급계약)", "tier": 1,
        "scan_code": "I001",
        "matchers": [("체결", ["단일판매공급계약체결"]), ("해지", ["단일판매공급계약해지"])],
        "max_items": 40, "detail_kind": "order",
        "force_keywords": ["해지", "정정"],
    },
    {
        "code": "treasury", "label": "자기주식", "tier": 1,
        "scan_code": "B001",
        "matchers": [
            ("소각", ["자기주식소각"]),
            ("취득신탁해지", ["자기주식취득신탁계약해지"]),
            ("취득신탁", ["자기주식취득신탁계약체결"]),
            ("처분", ["자기주식처분"]),
            ("취득", ["자기주식취득"]),
        ],
        "max_items": 30, "detail_kind": "treasury",
        "force_keywords": ["소각", "해지", "정정"],
    },
    {
        "code": "dividend", "label": "배당", "tier": 1,
        "scan_code": "I001",
        "matchers": [
            ("현금배당", ["현금·현물배당결정", "현금현물배당결정", "현금배당결정"]),
            ("현물배당", ["현물배당결정"]),
            ("기준일", ["주주명부폐쇄", "기준일"]),
        ],
        "max_items": 40, "detail_kind": "dividend",
        "force_keywords": ["정정"],
    },
    {
        "code": "dilutive", "label": "증자·CB·BW", "tier": 1,
        "scan_code": "B001",
        "matchers": [
            ("유상증자", ["유상증자결정"]),
            ("무상증자", ["무상증자결정"]),
            ("CB발행", ["전환사채권발행"]),
            ("BW발행", ["신주인수권부사채권발행"]),
            ("교환사채", ["교환사채권발행"]),
            ("감자", ["감자결정"]),
        ],
        "max_items": 25, "detail_kind": "dilutive",
        "force_keywords": ["정정", "철회"],
    },
    {
        "code": "agm_notice", "label": "주주총회소집", "tier": 1,
        "scan_code": "I001",
        "matchers": [("소집결의", ["주주총회소집결의"]), ("소집공고", ["주주총회소집공고"])],
        "max_items": 25, "detail_kind": "agm_notice",
        "force_keywords": ["정정"],
    },
    {
        "code": "ownership5", "label": "5%대량보유", "tier": 1,
        "scan_code": "D001",
        "matchers": [("상세", ["주식등의대량보유상황보고서(일반)"]),
                     ("약식", ["주식등의대량보유상황보고서(약식)"])],
        "max_items": 40, "detail_kind": "ownership",
        "force_keywords": ["정정"],
    },
    # ── Tier2/3 (scan-only; 같은 스캔 코드에 편승 → 추가 콜 0) ─────────────
    {
        "code": "earnings", "label": "잠정실적", "tier": 2,
        "scan_code": "I002",
        "matchers": [("잠정실적", ["영업(잠정)실적", "영업잠정실적", "잠정실적"])],
        "max_items": 40, "detail_kind": "earnings", "force_keywords": ["정정"],
    },
    {
        "code": "agm_result", "label": "주총결과", "tier": 2,
        "scan_code": "I001",
        "matchers": [("정기", ["정기주주총회결과"]), ("임시", ["임시주주총회결과"]),
                     ("결과", ["주주총회결과"])],
        "max_items": 30, "detail_kind": None, "force_keywords": ["정정"],
    },
    {
        "code": "restructuring", "label": "합병·분할·영업양수도", "tier": 2,
        "scan_code": "B001",
        "matchers": [("합병", ["회사합병결정", "분할합병결정"]),
                     ("분할", ["회사분할결정"]),
                     ("영업양수도", ["영업양수결정", "영업양도결정"]),
                     ("주식교환", ["주식의포괄적교환", "주식의포괄적이전"])],
        "max_items": 20, "detail_kind": None, "force_keywords": ["정정"],
    },
    {
        "code": "stake_deal", "label": "타법인주식 양수·양도", "tier": 3,
        "scan_code": "B001",
        "matchers": [("양수도", ["타법인주식및출자증권양수결정", "타법인주식및출자증권양도결정",
                                 "타법인주식및출자증권취득결정", "타법인주식및출자증권처분결정"])],
        "max_items": 20, "detail_kind": None, "force_keywords": ["정정"],
    },
    {
        "code": "control_change", "label": "최대주주변경", "tier": 3,
        "scan_code": "I001",
        "matchers": [("변경", ["최대주주변경"])],
        "max_items": 20, "detail_kind": "control", "force_keywords": ["정정", "해제", "취소"],
    },
    {
        "code": "litigation", "label": "소송·제재·위험", "tier": 3,
        "scan_code": "I001",
        "matchers": [("소송", ["소송등의제기", "소송등의판결"]),
                     ("제재", ["벌금등의부과", "과징금", "행정처분"]),
                     ("재해", ["중대재해발생"])],
        "max_items": 20, "detail_kind": None, "force_keywords": ["정정"],
    },
    # ── opt-in(디폴트 프리셋 밖) — 노이즈/전문성 큰 코드 ────────────────
    {
        "code": "insider10", "label": "임원·주요주주 소유상황", "tier": 3,
        "scan_code": "D002",
        "matchers": [("소유상황", ["임원·주요주주특정증권등소유상황보고서",
                                   "임원주요주주특정증권등소유상황보고서"])],
        "max_items": 40, "detail_kind": None, "force_keywords": ["정정"],
        "opt_in": True,  # 임원 소유상황은 매우 노이즈 → 디제스트 디폴트 제외
    },
    {
        "code": "tender_offer", "label": "공개매수", "tier": 3,
        "scan_code": "D004",
        "matchers": [("의견표명", ["공개매수에관한의견표명"]),
                     ("결과", ["공개매수결과"]), ("철회", ["공개매수철회"]),
                     ("설명서", ["공개매수설명서"]), ("신고서", ["공개매수신고서"]),
                     ("공개매수", ["공개매수"])],
        "max_items": 20, "detail_kind": None, "force_keywords": ["정정"],
        "opt_in": True, "interpretation": "discovery_only", "dedup_by_filer": True,
    },
    {
        "code": "proxy_solicitation", "label": "의결권대리행사 권유", "tier": 3,
        "scan_code": "D003",
        "matchers": [("의견표명", ["의결권대리행사권유에관한의견표명"]),
                     ("권유서류", ["의결권대리행사권유", "의결권대리행사참고서류", "위임장권유참고서류"])],
        "max_items": 20, "detail_kind": None, "force_keywords": ["정정"],
        "opt_in": True, "interpretation": "discovery_only", "dedup_by_filer": True,
    },
]

_BY_CODE = {t["code"]: t for t in TYPE_REGISTRY}
#: 원장 라벨도 그대로 받는다 — 표에 보이는 이름을 사용자가 되돌려 주는 일이 흔하다.
_LABEL_TO_CODE = {t["label"].strip().lower(): t["code"] for t in TYPE_REGISTRY if t.get("label")}

# 유형 → 상세페이지로 이어질 OPM tool 힌트(카드의 suggested_tool)
_SUGGESTED_TOOL = {
    "order": "order_contracts", "treasury": "treasury_share",
    "dividend": "dividend_disclosure",
    "dilutive": "dilutive_issuance", "agm_notice": "shareholder_meeting_notice",
    "ownership5": "ownership_structure",
    # insider10 = 임원·주요주주 소유상황(D002/elestock). ownership_structure 는 5% 대량보유
    # (majorstock)만 보므로 그리로 보내면 「임원 매매 없음」이라는 false negative 로 끝난다.
    "insider10": "proxy_contest",
    "earnings": "provisional_earnings", "agm_result": "shareholder_meeting_results",
    "restructuring": "corporate_restructuring", "stake_deal": "corporate_deals",
    "control_change": "ownership_structure", "litigation": "risk_events",
    "tender_offer": "governance_screen", "proxy_solicitation": "governance_screen",
}


# ── 분류기 ─────────────────────────────────────────────────────────────

def classify(report_nm: str) -> tuple[str | None, str, bool]:
    """report_nm → (type_code, subtype_label, is_correction). 매치 실패 시 (None, "", corr)."""
    is_corr = bool(_CORRECTION_RE.match(report_nm or ""))
    core = _normalize_report_nm(report_nm)
    for t in TYPE_REGISTRY:
        for subtype, keywords in t["matchers"]:
            for kw in keywords:
                if _normalize_report_nm(kw) in core:
                    return t["code"], subtype, is_corr
    return None, "", is_corr


#: 「…(자회사의 주요경영사항)」 — 상장 모회사가 비상장 자회사의 결정·실적을 대신 공시한 서식. 같은 날 모회사 자신의 공시와
#: 제목이 거의 같아(HD한국조선해양 잠정실적 20261019800441/800444) 카드 두 장이 같은 공시의 중복으로 읽힌다.
_SUBSIDIARY_MARK = "자회사의주요경영사항"

#: 자기주식 카드의 구분 — subtype(매처 이름) → 사람이 읽는 말. 신탁은 체결·해지를 가른다.
_TREASURY_ACTION = {"취득": "취득", "처분": "처분", "소각": "소각",
                    "취득신탁": "신탁 체결", "취득신탁해지": "신탁 해지"}

#: 문서를 열어도 **접수번호로 그 공시를 고르지 못하는** 파서(회사·날짜로만 찾는다) — 모회사 값이 자회사 카드에 붙는다.
_RCEPT_BLIND_KINDS = frozenset({"dividend", "earnings"})


_SUBSIDIARY_BLIND_NOTE = "자회사의 주요경영사항 공시 — 모회사 값과 섞이지 않게 숫자를 싣지 않았다(원문 링크로 확인)"


def _is_subsidiary_matter(report_nm: str) -> bool:
    return _SUBSIDIARY_MARK in _normalize_report_nm(report_nm)


def _stage_tag(type_code: str, report_nm: str, subtype: str) -> str:
    """단계 태깅 — 예정치≠실행치 구분. report_nm 키워드 기반."""
    core = _normalize_report_nm(report_nm)
    if "철회" in core or "취소" in core:
        return "철회/취소"
    if "해지" in core:
        return "해지"
    if "소각" in core:
        return "소각(실행)"
    if "결과" in core or "발행결과" in core:
        return "결과(확정)"
    if "공고" in core:
        return "공고"
    if "신고서" in core:
        return "신고서(접수)"
    if "결의" in core or "결정" in core:
        return "결정(예정)"
    return subtype or "-"


def _force_details(type_def: dict, report_nm: str, is_corr: bool) -> bool:
    """정정/해지/철회/소각/신규·변동 = details 강제(판단이 갈리는 단계)."""
    if is_corr:
        return True
    core = _normalize_report_nm(report_nm)
    return any(kw in core for kw in type_def.get("force_keywords", []) if kw != "정정")


# ══════════════════════════════════════════════════════════════════════
#  기간 해석
# ══════════════════════════════════════════════════════════════════════

def _today_kst() -> date:
    return datetime.now(_KST).date()


def _yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


# ══════════════════════════════════════════════════════════════════════
#  자연어 앞단 (260824) — 우리가 만든 코드 어휘를 사람 말로도 받는다
#
#  screener 만 `period="last_7d"` · `universe="kospi:30"` · `custom_start=` 같은
#  **우리끼리 정한 어휘**를 요구했다. 나머지 tool 은 전부 `company` 에 회사명을 그냥 받고
#  기간은 `start_date`/`end_date` 로 받는다. 부르는 쪽(LLM)이 그 어휘를 외워야 했고,
#  틀리면 조용히 기본값(`since_yesterday`·전체시장)으로 빠졌다.
#
#  ★ 여기서는 **정규화만** 한다 — 사람 말을 기존 코드로 바꿔서 원래 리졸버에 넘긴다.
#    리졸버를 다시 쓰면 지금 도는 것들이 함께 흔들린다. 옛 코드도 그대로 받는다(하위호환).
# ══════════════════════════════════════════════════════════════════════

#: 기간 — 260918 전면 점검으로 `services/period_words.py` 로 옮겼다. 카드 보기와 흐름 보기가 **같은 말 목록**을
#: 쓴다(종전엔 「지난달」이 보기마다 달랐다). 여기 남은 이름은 그 모듈을 가리킨다.
from open_proxy_mcp.services.period_words import (  # noqa: E402
    calendar_window, explain_unknown as _explain_period, parse as _parse_period, point as _period_point,
    rolling_window)

#: 유형 — 말 → 코드. TYPE_REGISTRY 의 label 도 자동으로 받는다(아래에서 합친다).
_NL_TYPES = {
    "거버넌스": "governance", "지배구조": "governance",
    "공개매수": "tender_offer", "공개매수신고서": "tender_offer", "tender": "tender_offer",
    "위임장": "proxy_solicitation", "위임장권유": "proxy_solicitation", "proxy": "proxy_solicitation",
    "의결권대리행사": "proxy_solicitation", "의결권권유": "proxy_solicitation",
    "의결권대리행사권유": "proxy_solicitation",
    "자사주": "treasury", "자기주식": "treasury", "자사주매입": "treasury", "소각": "treasury",
    "배당": "dividend", "현금배당": "dividend",
    "수주": "order", "공급계약": "order", "계약": "order", "단일판매": "order",
    "증자": "dilutive", "유상증자": "dilutive", "전환사채": "dilutive", "cb": "dilutive",
    "bw": "dilutive", "신주인수권": "dilutive",
    "주총": "agm_notice", "주주총회": "agm_notice", "소집": "agm_notice", "소집공고": "agm_notice",
    "주총결과": "agm_result", "의결": "agm_result",
    "지분": "ownership5", "대량보유": "ownership5", "5%": "ownership5", "지분공시": "ownership5",
    "실적": "earnings", "잠정실적": "earnings", "영업실적": "earnings", "어닝": "earnings",
    "합병": "restructuring", "분할": "restructuring", "영업양수도": "restructuring",
    "구조조정": "restructuring",
    "타법인": "stake_deal", "주식양수도": "stake_deal",
    "최대주주변경": "control_change", "경영권": "control_change",
    "소송": "litigation", "제재": "litigation", "위험": "litigation",
    "임원": "insider10", "내부자": "insider10",
}

_UNIVERSE_ALIASES = {
    "전체": "all", "전체시장": "all", "시장전체": "all", "다": "all", "everything": "all",
    "코스피": "market:kospi", "kospi": "market:kospi", "유가증권": "market:kospi",
    "코스닥": "market:kosdaq", "kosdaq": "market:kosdaq",
    "코스피200": "kospi200", "kospi200": "kospi200", "코스피 200": "kospi200", "kospi 200": "kospi200",
    # 261018: 지수 구성종목 원장에 코스닥150 도 담았다. 종전엔 「코스닥150」이 「시총 상위 150 인지 이름인지」 되묻기로 갔다.
    "코스닥150": "kosdaq150", "kosdaq150": "kosdaq150", "코스닥 150": "kosdaq150", "kosdaq 150": "kosdaq150",
}


def _nl_period(period: str, start_date: str, end_date: str,
               custom_start: str, custom_end: str, today: date | None = None) -> tuple[str, str, str]:
    """(period, custom_start, custom_end) 로 정규화. 말 해석은 `period_words.parse` 하나가 한다.

    `start_date`/`end_date` 는 **레포의 다른 tool 과 같은 이름**이다 — screener 만
    `custom_start`/`custom_end` 를 쓰고 있었다. 둘 다 받고 새 이름을 우선한다.
    날짜 인자는 YYYYMMDD 가 정본이지만 2026.09.01·2026/9/1 같은 꼴도 받는다(260918).
    `today` 는 연도 없는 말(「8월」「3분기」)의 연도를 정할 때 쓴다 — 흐름 보기가 자기 기준일을 넘긴다.
    """
    today = today or _today_kst()

    def _d(v: str) -> str:
        v = (v or "").strip()
        if re.fullmatch(r"\d{4}-?\d{2}-?\d{2}", v):
            return v.replace("-", "")
        span = _period_point(v, today) if v else None
        return span[0].strftime("%Y%m%d") if span and span[0] == span[1] else v.replace("-", "")

    cs = _d(start_date or custom_start or "")
    ce = _d(end_date or custom_end or "")

    # 날짜가 직접 왔으면 그게 이긴다 — 말보다 구체적이다
    if re.fullmatch(r"\d{8}", cs):
        return "custom", cs, (ce if re.fullmatch(r"\d{8}", ce) else cs)

    code, pcs, pce = _parse_period(period, today)
    if code == "custom" and pcs:
        return code, pcs, pce
    return code, cs, ce     # 못 알아들으면 원문 그대로 — 원래 리졸버가 notice 를 단다


def _nl_types(types: str) -> str:
    """말 → 코드 목록. 못 알아들은 조각은 그대로 넘겨 원래 검증이 걸러낸다."""
    raw = (types or "").strip()
    if not raw or raw.lower() in ("core", "all", "governance"):
        return raw or "core"
    out, unknown = [], []
    for tok in re.split(r"[,\s·/]+", raw):
        t = tok.strip().lower()
        if not t:
            continue
        if t in _BY_CODE:
            out.append(t)
        elif t in _NL_TYPES:
            out.append(_NL_TYPES[t])
        elif t in _LABEL_TO_CODE:
            out.append(_LABEL_TO_CODE[t])
        else:
            hit = next((c for k, c in _NL_TYPES.items() if k in t), None)
            (out if hit else unknown).append(hit or tok)
    seen: set[str] = set()
    ordered = [c for c in out if not (c in seen or seen.add(c))]
    return ",".join(ordered + unknown) if (ordered or unknown) else raw


def _nl_universe(universe: str) -> str:
    """말 → 기존 universe 문법. 「코스피 시총 상위 30」 같은 표현을 받는다."""
    raw = (universe or "").strip()
    if not raw:
        return "all"
    low = raw.lower()
    if low in _UNIVERSE_ALIASES:
        return _UNIVERSE_ALIASES[low]
    # 이미 기존 문법이면 그대로 (kospi:30 · top_mktcap:50 · custom:… · market:kospi)
    if re.match(r"^(all|kospi200|kosdaq150|kospi:|kosdaq:|kospi_top:|kosdaq_top:|top_mktcap:|market:|custom:)",
                low):
        return raw
    # 「KTOP30」(KRX 지수)은 아래 순위 말(「top 30」)로 읽지 않는다 — 나열 해석이 지원하지 않는 지수라고 안내한다(261019).
    if re.fullmatch(r"ktop\s*30", low):
        return f"custom:{raw}"
    # "코스피 시총 상위 30" · "코스닥 상위 50" · "시총 상위 100"
    # 260918: 숫자 뒤 「개 기업」「개 종목」「개 회사」「개사」「곳」도 받는다 — 종전엔 「200개 기업」이 회사 이름
    #   나열로 읽혀 해석에 실패했고, 카드 보기는 시장 전체로 바꿔 보였다(루틴 프롬프트가 이 꼴이다).
    m = re.search(r"(\d{1,4})\s*(?:개\s*)?(?:종목|기업|회사|사|곳|업체|상장사)?\s*(?:전체|들)?\s*$", low)
    if m and any(w in low for w in ("상위", "top", "시총", "시가총액")):
        n = m.group(1)
        if "코스피" in low or "kospi" in low:
            return f"kospi:{n}"
        if "코스닥" in low or "kosdaq" in low:
            return f"kosdaq:{n}"
        return f"top_mktcap:{n}"
    if low in ("코스피 전체", "코스피전체"):
        return "market:kospi"
    if low in ("코스닥 전체", "코스닥전체"):
        return "market:kosdaq"
    # 남은 것은 종목 이름·코드 나열로 본다 — `custom:` 이 이름도 코드화한다
    return f"custom:{raw}"


def resolve_period(period: str, *, cursor: str = "",
                   custom_start: str = "", custom_end: str = "") -> tuple[str, str, list[str]]:
    """period → (bgn_de, end_de, notices). 시장스캔 3개월 하드캡.

    반개구간[from,to) 커서: cursor(YYYYMMDD)가 오면 bgn을 그날로 오버라이드(직전 실행 이후).
    """
    notices: list[str] = []
    today = _today_kst()
    period = (period or "since_yesterday").strip().lower()

    # 260918: 창 계산은 `period_words` 하나가 한다(흐름 보기와 같은 뜻).
    #   「최근 N일」은 오늘 포함 N일 — 종전 카드 보기는 N+1일(최근 7일 = 8일)이었다.
    #   달력 코드: 이번 주·지난주·이번 달·지난달·이번 분기·지난 분기·올해·작년.
    win = rolling_window(period, today) or calendar_window(period, today)
    if win:
        bgn, end = win
    elif period == "custom":
        try:
            bgn = datetime.strptime(custom_start, "%Y%m%d").date()
            end = datetime.strptime(custom_end or custom_start, "%Y%m%d").date()
        except (ValueError, TypeError):
            note_degradation("period_fallback")
            notices.append(f"custom 기간 파싱 실패(start={custom_start!r},end={custom_end!r}) → since_yesterday로 대체.")
            bgn, end = today - timedelta(days=1), today
    else:
        note_degradation("period_fallback")
        notices.append(_explain_period(period) + " 어제부터로 보였다.")
        bgn, end = today - timedelta(days=1), today

    # 오늘 뒤는 아직 없다 — 「9월」「하반기」처럼 이번 칸 전체를 말해도 오늘까지만 본다
    if end > today:
        end = today

    # 커서(반개구간 시작) 오버라이드
    cur = (cursor or "").strip()
    if re.fullmatch(r"\d{8}", cur):
        cbgn = datetime.strptime(cur, "%Y%m%d").date()
        if cbgn <= end:
            bgn = cbgn

    # 3역월 하드캡
    _earliest = market_window_start(end)
    if bgn < _earliest:
        bgn = _earliest
        # 절단은 에러가 아니라 **대체**다 — 세지 않으면 얼마나 자주 발생하는지 영영 모른다.
        note_degradation("period_clamped")
        notices.append(f"시장스캔은 {MARKET_WINDOW_MAX_MONTHS}개월까지만 — 시작일을 {_yyyymmdd(bgn)}로 절단했다. "
                       "더 긴 기간의 업종별 건수·금액은 흐름 보기(`view=\"흐름\"`, 공시 원장 약 1년치)로 본다.")
    if bgn > end:
        bgn = end
    return _yyyymmdd(bgn), _yyyymmdd(end), notices


# ══════════════════════════════════════════════════════════════════════
#  universe (일별 시세 저장분의 최신 거래일 단면 — DART 0콜)
# ══════════════════════════════════════════════════════════════════════
#
# 261018: 기준일·순위·시장 전 종목·시총을 `krx_weekly` 이번 주 행(max(price_dd))에서 읽던 것을 일별 시세
#   저장분으로 옮겼다. 다음 단계에서 `krx_weekly` 는 닫힌 주의 마지막 거래일만 담게 되는데(이번 주 행을 매일
#   지우고 다시 넣는 일을 그만둔다), 그때 여기가 그대로면 유니버스·시총이 지난주 값으로 낡는다.
#   실측(20261016): 두 저장분의 같은 날 단면이 2,762종목 종가·시총·상장주식수·시장 모두 차이 0 — 응답은 그대로다.
# 🔴 단면은 **기준일을 먼저 읽고 그 날 행만** 본다(기준일은 달력 `krx_day` 의 가장 늦은 거래일 — `krx_daily.latest_day`,
#   그다음 `krx_daily_v WHERE trd_dd=%s`).
#   종목별 최신 행 뷰(`price_latest`)를 날짜 조건 없이 통째로 읽지 않는다 — 보관 창(약 1년) 안에서 상장폐지된
#   종목은 옛 날짜 행이 그대로 남아(실측 5종목) 오늘 순위에 섞인다. 종목 몇 개의 최신값이면 그 뷰가 맞다.
# 🔴 고르는 칸은 사실값 + mktcap 뿐이다 — 등락(chg·chg_pct·prev_*)은 종목별 창 계산이라 단면에서 고르면 느려진다
#   (실측 전 종목 단면: 사실값 칸만 6.9ms, 창 칸까지 40ms).
# 🔴 기준일과 행은 늘 같은 원천이다. 일별 저장분이 비거나 죽으면 `krx_weekly` 로 돌아가지 않고 기존 강등
#   (`universe_fallback` — 전체시장·DART 시장코드로 대체 + 안내)을 그대로 탄다. 섞어 읽으면 날짜와 행이 어긋난다.

def _krx_latest_dd() -> str | None:
    """서빙 단면의 기준일 — 일별 시세 저장분에 보관된 가장 늦은 거래일(YYYYMMDD). 비었거나 장애면 None.
    이름은 그대로 둔다(여러 시험이 이 이름을 바꿔 끼운다).
    261018: 정의는 `krx_daily.latest_day` 한 곳이다 — 종목 최신가의 기준일(`price_multiple_data._krx_daily_latest_dd`)과 같은 것.
    읽기는 이 모듈의 `pg_rows` 로 한다(시험이 이 모듈의 DB 읽기를 바꿔 끼운다).
    동기 함수다 — async 호출부는 `asyncio.to_thread` 로 부른다(이벤트 루프 밖)."""
    return _krx_daily.latest_day(pg_rows)


def _krx_mktcap_map(tickers: Iterable[str], price_dd: str) -> dict[str, int]:
    """단축코드 집합 → {ticker: mktcap(원)}. 한 쿼리로 배치(이름기반 컬럼)."""
    codes = [c for c in {(c or "").strip() for c in tickers} if c]
    if not codes:
        return {}
    url = os.getenv("DATABASE_URL")
    if not url:
        return {}
    rows = pg_rows("SELECT ticker, mktcap FROM krx_daily_v WHERE trd_dd=%s AND ticker = ANY(%s)",
                   (price_dd, codes)) or []
    return {isu: int(mktcap) for isu, mktcap in rows if mktcap}


def _krx_top_mktcap(n: int, price_dd: str, market: str | None = None) -> set[str]:
    """시총 상위 N 단축코드. market 지정 시 그 시장만 — 시장 혼합 방지.

    ★ market 은 `to_db` 로 **정규화해서** 묻는다. 시세 저장분은 260823 개명 뒤 KS/KQ 를
      담는데(당시 `krx_weekly`, 261018 부터 읽는 `krx_daily` 도 같다) 호출부가 "KOSPI" 를
      넘기면 질의가 죽지 않고 **0건**을 준다. 그러면 위쪽 `_rank` 가 그걸 「조회 실패」로
      읽어 조용히 전체시장으로 대체한다 — 사용자는 kospi200 을 물었는데 2,764종목을 받고,
      에러는 어디에도 안 뜬다(260824 실측).
      호출부를 상수로 바꾸는 것과 **둘 다** 한다. 경계에서 막아야 다음에 또 안 샌다.
    """
    q = "SELECT ticker FROM krx_daily_v WHERE trd_dd=%s AND mktcap IS NOT NULL"
    params: list = [price_dd]
    if market:
        q += " AND market=%s"
        params.append(to_db(market))
    q += " ORDER BY mktcap DESC LIMIT %s"
    params.append(n)
    return {r[0] for r in (pg_rows(q, tuple(params)) or [])}


def _krx_market_codes(market: str, price_dd: str) -> set[str]:
    """한 시장(KOSPI/KOSDAQ) 전 종목 단축코드."""
    return {r[0] for r in (pg_rows(
        "SELECT ticker FROM krx_daily_v WHERE trd_dd=%s AND market=%s AND mktcap IS NOT NULL",
        (price_dd, to_db(market)),              # 정규화 — `_krx_top_mktcap` 주석 참조
    ) or [])}


@dataclass(slots=True)
class UniverseFilter:
    label: str
    resolved: bool
    notice: str = ""
    allowed: set[str] | None = None   # None = 전체시장(필터 없음)
    price_dd: str | None = None
    corp_cls: str | None = None       # 시세 저장분 장애 시 DART 시장코드 fallback
    #: 비어 있지 않으면 **조회하지 않고 되묻는다**. 회사 목록에서 하나도 못 찾았을 때 시장 전체로 바꿔
    #: 보이지 않는다(260918 — 「코스피 시가총액 상위 200개 기업」이 시장 전체 75건으로 나갔다).
    question: str = ""
    #: 지수 구성종목 원장에서 집합을 만들었으면 그 구성이 마지막으로 확인된 거래일(YYYYMMDD). 아니면 None
    #: (원장을 못 읽어 시총 순위로 대체한 경우도 None — 그때는 notice 가 대체를 밝힌다).
    members_dd: str | None = None
    #: 이름 나열 안의 **시장 이름**(「코스피」·「코스닥」)을 풀어 넣은 종목 중 직접 고르지 않은 것(261019). 순위표
    #: (`services/universe.list_universe`)는 시장 전체(`market:…`)와 똑같이 이 중 우선주를 빼고 밝힌다 — 직접 적은 코드·이름과
    #: 지수 원장 종목은 고른 쪽이 정한 집합이라 그대로 둔다. 공시 필터(`contains`)에는 쓰지 않는다.
    pref_screen: frozenset[str] = frozenset()

    def contains(self, stock_code: str, corp_cls: str = "") -> bool:
        if self.allowed is None:
            return not self.corp_cls or (corp_cls or "").strip() == self.corp_cls
        return (stock_code or "").strip() in self.allowed


def _looks_like_code(tok: str) -> bool:
    """KRX 단축코드(6자 영숫자, 대부분 숫자)인가 — 회사명과 구분. 예: 005930, 900110, 0011A0."""
    return bool(re.fullmatch(r"[0-9A-Z]{6}", tok)) and sum(c.isdigit() for c in tok) >= 4


def _inactive_universe_note(tok: str, sel: dict) -> str:
    """유니버스 이름이 비활성 회사(상장폐지·합병·코넥스 등) 하나로 확정됐을 때의 안내 한 조각(261018)."""
    meta = sel.get("_resolution") or {}
    who = f"{sel.get('corp_name') or tok}({sel.get('stock_code') or ''})"
    day = f"최신 거래일({meta['market_data_as_of']})" if meta.get("market_data_as_of") else "최신 거래일"
    return (f"'{tok}'→{who}는 {day} 코스피·코스닥 시세에 없는 상장폐지·코넥스 등 비활성 회사 — "
            "시세·시총을 쓰는 표에는 행이 없다")


#: KRX 지수 이름 — 이 서버가 구성종목을 아는 지수(코스피200·코스닥150)는 `_UNIVERSE_ALIASES` 에 있고, 나머지는 여기서 알아본다.
#: 회사 이름 찾기로 보내지 않는다(261019 실측: 「KRX300」이 회사 못 찾음으로 떨어져 「사명이 바뀐 회사는 옛 이름으로 조회되지
#: 않는다」 안내까지 붙었다). 「코스피 100」·「코스피50」처럼 숫자만 붙은 꼴은 시총 순위일 수도 있어 `universe.clarification` 이 묻는다.
_W = r"[가-힣a-z\-]+"
_UNSUPPORTED_INDEX = re.compile(
    rf"^(?:krx\s*\d+(?:\s*{_W})*|krx\s+{_W}(?:\s*{_W})*|ktop\s*30"          # KRX300·KRX100·KRX 반도체·KTOP30
    rf"|(?:코스피|kospi)\s*200\s+[가-힣a-z]{{2,}}(?:\s*{_W})*"                 # 코스피200 정보기술(섹터 지수)
    rf"|(?:코스닥|kosdaq)\s*150\s+[가-힣a-z]{{2,}}(?:\s*{_W})*"
    r"|(?:코스닥|kosdaq)\s*(?:글로벌|global)|(?:코스피|코스닥|kospi|kosdaq)\s*(?:대형|중형|소형)주?"
    r"|.+지수)$", re.I)
#: 순위 말이 들어 있으면 지수 이름이 아니다(「KRX 시총 상위 100」 = 전체시장 순위).
_RANK_WORDS = ("상위", "시총", "시가총액")


def _set_spec(tok: str) -> str | None:
    """나열 안의 한 토큰이 종목 **집합** 이름(지수·시장)이면 그 스펙(kospi200·kosdaq150·market:kospi·market:kosdaq), 아니면 None.
    끝의 「지수」는 떼고 본다(「코스피200 지수」 = 코스피200)."""
    low = tok.strip().lower()
    base = re.sub(r"\s*지수$", "", low)
    for key in (low, re.sub(r"\s+", "", low), base, re.sub(r"\s+", "", base)):
        spec = _UNIVERSE_ALIASES.get(key)
        if spec in _index_members.INDEXES or spec in ("market:kospi", "market:kosdaq"):
            return spec
    return None


def unsupported_index_note(tok: str) -> str | None:
    """지원하지 않는 KRX 지수 이름이면 그렇게 말하는 문장, 아니면 None."""
    t = (tok or "").strip()
    if not t or _set_spec(t) or any(w in t for w in _RANK_WORDS) or not _UNSUPPORTED_INDEX.match(t):
        return None
    return (f"「{t}」은 지수 이름으로 보인다 — 이 서버의 지수 구성종목 원장은 코스피200·코스닥150 두 지수뿐이라 "
            f"「{t}」 구성종목은 지원하지 않는다(시총 순위로 바꿔 보이지 않는다)")


async def _resolve_custom_universe(raw: str, price_dd: str | None) -> UniverseFilter:
    """custom:… 토큰을 코드/이름 혼용으로 해석. 코드는 그대로, 이름은 resolve_company_query로 코드화.

    261019: 나열 안의 지수·시장 이름(「코스피200, 코스닥150」·「코스피, 코스닥」)은 그 집합들의 **합집합**으로 읽는다 —
    종전엔 회사 이름으로 찾다가 못 찾아 「사명 변경」 안내로 되물었다. 지원하지 않는 지수 이름(KRX300 등)은 지수라고 말한다."""
    tokens = [t.strip() for t in re.split(r"[,，]+", raw) if t.strip()]
    if not tokens:
        return UniverseFilter(label="지정종목", resolved=False, allowed=None, price_dd=price_dd,
                              question="종목 목록이 비어 있어 조회하지 않았다 — 종목 이름·코드를 쉼표로 나열하거나 "
                                       "「코스피 시총 상위 50」처럼 순위로 주세요.")
    codes: set[str] = set()
    notes: list[str] = []
    name_tokens: list[str] = []
    set_tokens: list[tuple[str, str]] = []
    index_notes: list[str] = []
    for tok in tokens:
        spec = _set_spec(tok)
        if spec:
            set_tokens.append((tok, spec))
        elif inote := unsupported_index_note(tok):
            index_notes.append(inote)
        elif _looks_like_code(tok.upper()):
            codes.add(tok.upper())
        else:
            name_tokens.append(tok)
    notes.extend(index_notes)
    market_codes: set[str] = set()                     # 시장 이름을 풀어 넣은 종목(우선주 판정 대상 — `pref_screen`)

    # 지수·시장 이름 → 그 집합(지수는 구성종목 원장, 원장을 못 읽으면 `resolve_universe` 가 시총 순위 대체를 밝힌다)
    set_labels: list[str] = []
    set_notices: list[str] = []
    members_dds: list[str | None] = []
    done_specs: set[str] = set()
    for tok, spec in set_tokens:
        if spec in done_specs:
            continue                                   # 같은 집합을 두 번 적었다
        done_specs.add(spec)
        sub = await resolve_universe(spec)
        if sub.resolved and sub.allowed:
            (market_codes if spec.startswith("market:") else codes).update(sub.allowed)
            set_labels.append(sub.label)
            members_dds.append(sub.members_dd)
            if sub.notice:
                set_notices.append(sub.notice)
        else:
            notes.append(f"'{tok}' 종목 집합을 만들지 못했다" + (f"({sub.notice})" if sub.notice else ""))

    async def _resolve_one(tok: str) -> tuple[str, str | None, str]:
        """(토큰, 종목코드, 안내) — 못 찾은 이유를 **토큰마다** 말한다.

        종전엔 「'X' 미식별(모호/없음)」 한 문장이라, 사명이 바뀐 회사인지 후보가 여럿인지
        구분할 수 없었다. 다른 tool 은 정본 안내를 주는데 여기만 안 줬다.
        """
        try:
            res = await resolve_company_query(tok)
        except Exception:
            return tok, None, f"'{tok}' 조회 중 오류 — 종목코드 6자리로 다시 준다."
        sel = getattr(res, "selected", None)
        if sel:
            # 261018: 활성 회사엔 맞는 이름이 없어 비활성 회사 하나로 확정했으면(`CompanyResolver._inactive_single`) 넣되 **밝힌다** —
            #   그 종목은 최신 거래일 시세가 없어 시세·시총 표(trading_data·forward_estimates_data 의 유니버스)에서 행 없이 사라진다.
            #   종전(못 찾음)엔 「미해결」로 밝혔는데, 확정만 하고 말하지 않으면 그 회사가 조용히 빠진다.
            meta = sel.get("_resolution") or {}
            return tok, sel.get("stock_code"), (_inactive_universe_note(tok, sel) if meta.get("inactive_listing") else "")
        if getattr(res, "status", None) == AnalysisStatus.AMBIGUOUS:
            return tok, None, company_ambiguous_warning(tok, getattr(res, "candidates", None))
        return tok, None, company_not_found_warning(tok)

    from open_proxy_mcp.services.universe import clarification
    inactive: list[str] = []
    # 「코스피100」처럼 수인지 지수·이름인지 모를 토큰은 회사로 찾지 않고 그 토큰에 대한 질문을 안내로 단다.
    ask_tokens = [(t, clarification(t)) for t in name_tokens]
    notes.extend(q for _t, q in ask_tokens if q)
    name_tokens = [t for t, q in ask_tokens if not q]
    if name_tokens:
        for tok, sc, hint in await asyncio.gather(*[_resolve_one(t) for t in name_tokens]):
            if sc:
                codes.add(sc.strip())
                if hint:
                    inactive.append(hint)
            else:
                notes.append(hint or f"'{tok}' 미식별(모호/없음)")
    pref_screen = frozenset(market_codes - codes)      # 직접 적었거나 지수 원장에 든 종목은 판정하지 않는다
    codes |= market_codes
    if not codes:
        # 260918: 시장 전체로 바꾸지 않는다 — 되묻는다. 「코스피 120」처럼 수인지 이름인지 모를 꼴은 전용 질문으로.
        if index_notes and len(index_notes) == len(tokens):
            # 지원하지 않는 지수 이름만 왔다 — 회사 못 찾음(사명 변경 안내)으로 말하지 않는다.
            ask = (" · ".join(index_notes) + ". 「코스피200」·「코스닥150」·「코스피200, 코스닥150」(합집합) 중에서 고르거나, "
                   "시총 순위라면 「시총 상위 300」(코스피·코스닥 혼합 — 지수 구성과 다르다)처럼, 종목이면 이름·코드를 쉼표로 나열해 주세요.")
        else:
            ask = clarification(raw) or (
                f"「{raw}」에서 회사를 하나도 찾지 못해 조회하지 않았다(시장 전체로 바꿔 보이지 않는다). "
                "종목 이름·코드를 쉼표로 나열하거나, 순위라면 「코스피 시총 상위 50」처럼 주세요."
                + ((" 찾지 못한 이유: " + " · ".join(notes)) if notes else ""))
        return UniverseFilter(label="지정종목", resolved=False, allowed=None, price_dd=price_dd, question=ask)
    # 부분 해결 시: 해결분으로 진행함을 명시(전체 degrade로 오해 방지)
    notice = (f"해결된 {len(codes)}종목으로 진행 — 미해결: " + " · ".join(notes)) if notes else ""
    if inactive:
        notice = " / ".join(x for x in (notice, "비활성 회사로 식별: " + " · ".join(inactive)) if x)
    if set_notices:
        notice = " / ".join(x for x in (notice, *set_notices) if x)
    only_sets = bool(set_labels) and len(set_tokens) == len(tokens)
    if only_sets:
        # 지수·시장 이름만 나열 — 그 집합들의 합집합. 모두 지수 원장에서 왔으면 원장 확인일(가장 이른 것)을 함께 싣는다.
        label = "+".join(set_labels)
        members_dd = (min(d for d in members_dds if d) if members_dds and all(members_dds) else None)
        return UniverseFilter(label=label, resolved=True, notice=notice, allowed=codes, price_dd=price_dd,
                              members_dd=members_dd, pref_screen=pref_screen)
    label = f"지정 {len(codes)}종목" + (f"({'+'.join(set_labels)} 포함)" if set_labels else "")
    return UniverseFilter(label=label, resolved=True,
                          notice=notice, allowed=codes, price_dd=price_dd, pref_screen=pref_screen)


def _index_members_latest(code: str) -> "_index_members.Members | None":
    """지수 구성종목 원장의 가장 늦은 묶음. 못 읽거나 비면 None. 읽기는 이 모듈의 `pg_rows`(시험이 바꿔 끼운다)."""
    return _index_members.latest(code, pg_rows)


async def _index_universe(idx: "_index_members.IndexSpec", price_dd: str | None,
                          rank: Callable[[int, str | None, str], Awaitable[UniverseFilter]]) -> UniverseFilter:
    """지수 → 구성종목 집합. 원장을 못 읽으면 종전처럼 그 시장의 시총 상위 정원으로 대체하고 **대체했다고 밝힌다**.

    구성종목 집합은 시세 저장분과 무관하게 원장에서 온다 — 기준일(`price_dd`)이 없어도 집합은 만든다
    (공시 필터에는 종목코드만 있으면 된다). 시세 단면이 필요한 호출자(순위표)는 그 날 행을 따로 읽는다.
    원장 읽기는 이벤트 루프 밖 스레드에서 돈다(`asyncio.to_thread`)."""
    members = await asyncio.to_thread(_index_members_latest, idx.code)
    if members is None:
        note_degradation("universe_approximated")
        uf = await rank(idx.size, idx.market, f"{idx.label}(→{to_label(idx.market)} 시총상위{idx.size} 대체)")
        if uf.resolved:
            uf.notice = (f"{idx.label} 구성종목 원장을 읽지 못해 {to_label(idx.market)} 시총상위 {idx.size}으로 대체했다"
                         f"(실제 구성과 다를 수 있다 · 다른 시장 미포함).")
        return uf
    return UniverseFilter(label=idx.label, resolved=True, allowed=set(members.tickers), price_dd=price_dd,
                          notice=_index_members.stale_note(idx, members, price_dd), members_dd=members.checked_dd)


async def resolve_universe(universe: str) -> UniverseFilter:
    """universe 스펙 → UniverseFilter. 디폴트 all=전체시장(필터 없음).

    지원 스펙:
      all · market:kospi|kosdaq · kospi200 · kosdaq150(지수 구성종목 원장) · kospi:N · kosdaq:N ·
      top_mktcap:N(전체시장 시총상위) · custom:코드|이름,… · sector:…(미구현 degrade)
    """
    spec = (universe or "all").strip()
    # 시세 DB 읽기는 모두 이벤트 루프 밖 스레드에서 돈다(`asyncio.to_thread` — 루프를 60~100ms 막지 않는다).
    price_dd = await asyncio.to_thread(_krx_latest_dd)

    if spec in ("", "all"):
        return UniverseFilter(label="전체시장", resolved=True, allowed=None, price_dd=price_dd)

    low = spec.lower()

    async def _rank(n: int, market: str | None, label: str) -> UniverseFilter:
        allowed = (await asyncio.to_thread(_krx_top_mktcap, n, price_dd, market)) if price_dd else set()
        if not allowed:
            # ★ 260824 계기 지점. 이 폴백이 **모든 kospi200 호출에서 100% 발화**하고 있었는데
            #   문장이 사용자 응답에만 실려 나가고 우리가 보는 곳엔 안 쌓였다.
            note_degradation("universe_fallback")
            return UniverseFilter(label=label, resolved=False,
                                  notice="시세 저장분 조회 실패 → 전체시장으로 대체.", allowed=None, price_dd=price_dd)
        return UniverseFilter(label=label, resolved=True, allowed=allowed, price_dd=price_dd)

    # 시장 전체(랭킹 없음) — exact 매칭(kospi200/kospi:N 흡수 방지)
    if low in ("market:kospi", "kospi"):
        codes = (await asyncio.to_thread(_krx_market_codes, MKT_KS, price_dd)) if price_dd else set()
        if not codes:
            note_degradation("universe_fallback")
        return UniverseFilter(label="KOSPI 전체", resolved=bool(codes),
                              notice="" if codes else "시세 저장분 조회 실패 → DART corp_cls=Y로 제한.",
                              allowed=codes or None, price_dd=price_dd,
                              corp_cls=None if codes else "Y")
    if low in ("market:kosdaq", "kosdaq"):
        codes = (await asyncio.to_thread(_krx_market_codes, MKT_KQ, price_dd)) if price_dd else set()
        if not codes:
            note_degradation("universe_fallback")
        return UniverseFilter(label="KOSDAQ 전체", resolved=bool(codes),
                              notice="" if codes else "시세 저장분 조회 실패 → DART corp_cls=K로 제한.",
                              allowed=codes or None, price_dd=price_dd,
                              corp_cls=None if codes else "K")

    # 시장별 시총 상위 N
    if low.startswith("kospi:") or low.startswith("kospi_top:"):
        try:
            n = int(spec.split(":", 1)[1])
        except ValueError:
            note_degradation("universe_fallback")
            return UniverseFilter(label=spec, resolved=False, notice="kospi:N 파싱 실패 → 전체시장.", allowed=None, price_dd=price_dd)
        return await _rank(n, MKT_KS, f"KOSPI 시총상위 {n}")
    if low.startswith("kosdaq:") or low.startswith("kosdaq_top:"):
        try:
            n = int(spec.split(":", 1)[1])
        except ValueError:
            note_degradation("universe_fallback")
            return UniverseFilter(label=spec, resolved=False, notice="kosdaq:N 파싱 실패 → 전체시장.", allowed=None, price_dd=price_dd)
        return await _rank(n, MKT_KQ, f"KOSDAQ 시총상위 {n}")

    # 지수(코스피200·코스닥150) → 구성종목 원장. 261018 전에는 원장이 없어 늘 시총 순위로 대체했다
    #   (실측 20261016: 공식 201종목 중 34종목이 대체 목록에 없었다 — `open_proxy_mcp/index_members.py`).
    idx = next((sp for code, sp in _index_members.INDEXES.items() if low.startswith(code)), None)
    if idx is not None:
        return await _index_universe(idx, price_dd, _rank)

    # 전체시장 시총 상위 N (시장 혼합 — 라벨로 명시)
    if low.startswith("top_mktcap:"):
        try:
            n = int(spec.split(":", 1)[1])
        except ValueError:
            return UniverseFilter(label=spec, resolved=False,
                                  notice="top_mktcap:N 파싱 실패 → 전체시장으로 대체.", allowed=None, price_dd=price_dd)
        return await _rank(n, None, f"전체시장 시총상위 {n}")

    if low.startswith("custom:"):
        return await _resolve_custom_universe(spec.split(":", 1)[1], price_dd)

    if low.startswith("sector"):
        return UniverseFilter(
            label=spec, resolved=False,
            notice="섹터 필터는 미구현(KSIC 조인 TODO) — 전체시장으로 스캔했다.",
            allowed=None, price_dd=price_dd)

    return UniverseFilter(label=spec, resolved=False,
                          notice=f"알 수 없는 universe={spec!r} → 전체시장으로 대체.", allowed=None, price_dd=price_dd)


# ══════════════════════════════════════════════════════════════════════
#  scan (전체시장 페이지네이션 + throttle)
# ══════════════════════════════════════════════════════════════════════

async def _scan_code(client, detail_ty: str, bgn_de: str, end_de: str,
                     max_pages: int) -> dict:
    """캐시 앞단 — 실제 수집은 `_scan_code_uncached`. 키는 **공시유형 × 기간 × 페이지상한**뿐이라
    누가 물었는지가 안 들어간다(시장 데이터)."""
    # `_SCAN_CACHE` 는 프로세스 메모리(LruByteCache)라 배포하면 함께 사라진다 — 포맷 세대
    # 접두는 막을 대상이 없어서 두지 않는다.
    key = f"{detail_ty}|{bgn_de}|{end_de}|{max_pages}"
    hit = _SCAN_CACHE.get(key)
    if hit is not None:
        # 리스트를 그대로 내주면 호출측이 고칠 때 캐시가 함께 바뀐다 — 얕은 복사로 끊는다.
        return {**hit, "items": list(hit["items"])}
    res = await _scan_code_uncached(client, detail_ty, bgn_de, end_de, max_pages)
    if res["error"] is None:
        # ★ **복사해서 담는다.** 같은 리스트를 담고 그대로 돌려주면, 호출측이 그 리스트를
        #   고치는 순간 캐시가 함께 바뀐다(미스 경로에서 실제로 그랬다). 적중 경로만
        #   복사하면 첫 호출자가 캐시를 오염시킨다 — 넣는 쪽에서 끊는 게 맞다.
        _SCAN_CACHE.put(key, {**res, "items": list(res["items"])}, ttl_sec=_scan_ttl(end_de))
    return res


def _scan_result(items: list[dict], total: int, total_pages: int, fetched_pages: int,
                 error: str | None = None) -> dict:
    """스캔 한 코드의 결과 + **어디까지 봤는지**. 에러도 이 dict 안에 넣는다.

    에러를 밖으로 따로 빼면(2튜플) 완전성 판정이 그것을 못 보고 「빈 결과 = 완전」이 된다 —
    실제로 그랬다: 전송오류로 통째로 죽은 코드가 `complete: true` 로 나갔다. 모수를 정직하게
    적으려고 만든 필드가 실패할 때만 거짓말을 하면 읽는 쪽은 경고를 무시하고 이 표를 믿는다.

    `fetched_pages` 는 **던진 수가 아니라 실제로 받은 수**다. 3페이지가 죽고 4페이지가 살아오면
    4/4 를 주장하면서 가운데 구멍을 덮게 된다.

    `seen_from`/`seen_to` 는 **실제로 받은 행의 접수일 범위**다 — DART 기본 정렬을 가정하지 않고
    본 것만 적는다(정렬은 문서화된 계약이 아니라, 이 레포의 다른 스캐너도 그것에 기대지 않는다).
    """
    dts = sorted(str(it["rcept_dt"]) for it in items if it.get("rcept_dt"))
    return {"items": items, "total": total, "total_pages": total_pages,
            "fetched_pages": fetched_pages,
            "truncated": total_pages > fetched_pages,
            "error": error,
            # 완전 = 상한에 안 걸렸고 **에러도 없었다**. 둘 중 하나라도 있으면 이 코드의 모수는 불완전하다.
            "complete": error is None and total_pages <= fetched_pages,
            "seen_from": dts[0] if dts else None,
            "seen_to": dts[-1] if dts else None,
            "missing_pages": [], "received_pages": fetched_pages}


async def _scan_code_uncached(client, detail_ty: str, bgn_de: str, end_de: str,
                              max_pages: int) -> dict:
    """한 detail 코드의 전체시장 필러를 페이지네이션. ReadError/차단코드 즉시 중단."""
    items: list[dict] = []
    try:
        first = await client.search_filings(
            bgn_de=bgn_de, end_de=end_de, pblntf_detail_ty=detail_ty,
            page_no=1, page_count=_PAGE_COUNT)
    except DartClientError as exc:
        if exc.status == "013":
            # 「해당 없음」은 진짜 무자료다 — 에러가 아니라 완전한 0건이다(complete: true 가 옳다).
            return _scan_result([], 0, 0, 0)
        return _scan_result([], 0, 0, 0, error=exc.status)
    except Exception as exc:      # noqa: BLE001
        # ★ 전송 오류는 `DartClientError` 로 안 온다 — `_request` 가 원래 예외(httpx)를
        #   그대로 올린다(260824 실측: DNS 실패 시 httpx.ConnectError). 종전 `except
        #   DartClientError` 만으로는 못 잡아 스캔 전체가 죽었다.
        return _scan_result([], 0, 0, 0, error=f"transport:{type(exc).__name__}")
    total = int(first.get("total_count", 0) or 0)
    items.extend(first.get("list", []))
    total_pages = max(1, math.ceil(total / _PAGE_COUNT)) if total else 1
    fetch_pages = min(total_pages, max_pages)
    if fetch_pages < 2:
        return _scan_result(items, total, total_pages, fetch_pages)

    # 2페이지부터는 서로 독립이다 — 1페이지가 총량을 알려주므로 나머지는 한꺼번에 던진다.
    #   속도는 `_throttle_api`(910/분 롤링윈도우 + 락)가 잡는다. 여기서 또 재우면 두 곳이
    #   같은 일을 하게 되고, 실측상 그 대기가 응답의 대부분이었다.
    async def _page(p: int):
        return p, await client.search_filings(
            bgn_de=bgn_de, end_de=end_de, pblntf_detail_ty=detail_ty,
            page_no=p, page_count=_PAGE_COUNT)

    results = await asyncio.gather(*[_page(p) for p in range(2, fetch_pages + 1)],
                                   return_exceptions=True)
    # ★ 순서를 복원한다. gather 는 입력 순서로 돌려주지만 예외가 섞이므로 페이지 번호를
    #   함께 들고 다니며 정렬한다 — 공시 목록의 순서가 뒤집히면 dedup(정정=최신본)이 흔들린다.
    ok_pages: dict[int, dict] = {}
    err = None
    for r in results:
        if isinstance(r, DartClientError):
            err = err or r.status          # 첫 오류를 보고 (부분 결과는 살린다)
            continue
        if isinstance(r, BaseException):
            err = err or f"transport:{type(r).__name__}"
            continue
        p_no, page = r
        ok_pages[p_no] = page
    # 받은 페이지는 **전부 싣는다**(원문을 버리지 않는다). 다만 완전성은 「연속으로 받은 데까지」로
    #   센다 — 3페이지가 죽고 4페이지가 살아왔다고 4/4 를 주장하면 가운데 구멍을 덮은 채
    #   「여기까지는 온전하다」고 말하게 된다. 데이터는 남기고 모수만 정직하게 적는 게 맞다.
    missing: list[int] = []
    contiguous = 1                          # 1페이지는 위에서 이미 받았다
    still_contiguous = True
    for p_no in range(2, fetch_pages + 1):
        page = ok_pages.get(p_no)
        if page is None:
            missing.append(p_no)
            still_contiguous = False
            continue
        items.extend(page.get("list", []))
        if still_contiguous:
            contiguous += 1
    res = _scan_result(items, total, total_pages, contiguous, error=err)
    res["missing_pages"] = missing          # 구멍을 숨기지 않는다
    res["received_pages"] = 1 + len(ok_pages)
    return res


# ══════════════════════════════════════════════════════════════════════
#  details 디스패치 (파서 재사용 — build_*_payload)
#  ⚠ 각 파서는 company_query + date window로 self-resolve/self-search 한다.
#    hit 1건당 좁은 창(start=end=filed_at)으로 호출 → 그 회사·그날 것만 파싱.
#    추출 실패는 조작값 대신 degrade(no_data). 원문 URL은 항상.
# ══════════════════════════════════════════════════════════════════════

def _first(*vals):
    for v in vals:
        if v not in (None, "", [], {}):
            return v
    return None


def _extract_order(payload: dict, rcept_no: str) -> dict:
    orders = (payload.get("data") or {}).get("orders") or []
    row = next((o for o in orders if o.get("rcept_no") == rcept_no), orders[0] if orders else None)
    if not row:
        return {}
    return {
        "amount_won": row.get("contract_amount_won"),
        "counterparty": row.get("counterparty"),
        "revenue_ratio_pct": row.get("revenue_ratio_pct"),
        "is_external": row.get("is_external"),
    }


def _extract_treasury(payload: dict, rcept_no: str) -> dict:
    # 실제 payload: data.events[] = {event, rcept_no, shares, amount_common_krw, amount_preferred_krw, ...}
    data = payload.get("data") or {}
    events = data.get("events") or []
    row = next((e for e in events if e.get("rcept_no") == rcept_no), events[0] if events else None) or {}
    amt_c = row.get("amount_common_krw") or 0
    amt_p = row.get("amount_preferred_krw") or 0
    amount = (amt_c + amt_p) or None
    return {
        "amount_won": amount,
        "shares": row.get("shares"),
        "is_cancellation": row.get("event") == "cancelation_decision",
    }


def _extract_dividend(payload: dict, rcept_no: str) -> dict:
    # 실제 payload: data.summary{cash_dps,total_dps,payout_ratio_dart,stlm_dt} + data.latest_decisions[]
    data = payload.get("data") or {}
    s = data.get("summary") or {}
    dec = (data.get("latest_decisions") or [{}])[0]
    return {
        "dps_won": _first(s.get("cash_dps"), s.get("total_dps"), dec.get("dps_common")),
        "payout_ratio_pct": _first(s.get("payout_ratio_dart"), s.get("payout_ratio")),
        "record_date": _first(s.get("stlm_dt"), dec.get("record_date")),
    }


def _extract_dilutive(payload: dict, rcept_no: str) -> dict:
    # 실제 payload: 유형별 리스트 분리(rights_offering_events/convertible_bond_events/...) — union.
    data = payload.get("data") or {}
    events = []
    for k in ("events_timeline", "rights_offering_events", "convertible_bond_events",
              "exchangeable_bond_events", "warrant_bond_events", "capital_reduction_events"):
        events += data.get(k) or []
    row = next((e for e in events if e.get("rcept_no") == rcept_no), events[0] if events else None) or {}
    return {
        "event_label": _first(row.get("event_label"), row.get("type")),
        "allocation": _first(row.get("issuance_method"), row.get("allocation_method")),
        "dilution_pct": _first(row.get("dilution_pct_approx"), row.get("dilution_pct")),
        "amount_won": _first(row.get("total_issue_amount_krw"), row.get("bond_amount_krw"),
                             row.get("amount_krw"), row.get("raise_amount_won")),
    }


def _extract_agm_notice(payload: dict, rcept_no: str) -> dict:
    # 실제 payload: data.agenda_summary{titles,total_count} + data.agendas[] + data.meeting_type
    data = payload.get("data") or {}
    if data.get("scope") == "resolution":
        # 소집결의 — data.meeting{meeting_date} + data.agenda{status, items[{title}]}
        agenda = data.get("agenda") or {}
        items = agenda.get("items") or []
        return {
            "meeting_type": data.get("meeting_type"),
            "meeting_date": (data.get("meeting") or {}).get("meeting_date"),
            # 안건 칸은 원문 줄바꿈째 온다(`사내이사 선임의 건\n후보자 : …`) — 카드는 한 줄이라 ` / ` 로 편다
            "agenda_titles": [" / ".join(i["title"].split("\n")) for i in items if i.get("title")][:12],
            "agenda_count": len(items) or None,
            "agenda_status": agenda.get("status"),
        }
    summ = data.get("agenda_summary") or {}
    titles = summ.get("titles") or []
    if not titles:
        agendas = data.get("agendas") or []
        titles = [a.get("title") for a in agendas if isinstance(a, dict) and a.get("title")][:12]
    return {
        "meeting_type": _first(data.get("meeting_type"), data.get("requested_meeting_type")),
        "meeting_date": _first(data.get("meeting_date"), data.get("meeting_datetime")),
        "agenda_titles": titles,
        "agenda_count": _first(summ.get("total_count"), len(titles) or None),
    }


def _extract_control(payload: dict, rcept_no: str) -> dict:
    # data.event{kind, kind_label, fields{...}} — 원문 글자 그대로(숫자도 회사가 쓴 꼴)
    ev = (payload.get("data") or {}).get("event") or {}
    f = ev.get("fields") or {}
    who = lambda p: (p or {}).get("name")  # noqa: E731
    return {
        "control_kind": ev.get("kind"),
        "holder_before": f.get("before_holder"),
        "holder_after": _first(f.get("after_holder"), f.get("expected_holder"), who(f.get("transferee"))),
        "stake_after_pct": _first(f.get("after_pct"), f.get("expected_pct")),
        "amount": f.get("amount"),
        "pledgor": f.get("pledgor"),
        "stake_if_executed_pct": f.get("pct_if_executed"),
        "reason": f.get("reason"),
        # 해제 서식의 날짜는 **해제일**이다 — 원계약 체결일도 같은 서식에 있어 먼저 집으면 몇 달 전 날짜가 찍힌다(아스타 20261016900765)
        "on": (_first(f.get("released_on"), f.get("contract_on"))
               if ev.get("kind") in ("pledge_release", "transfer_release")
               else _first(f.get("changed_on"), f.get("contract_on"))),
    }


def _extract_ownership(payload: dict, rcept_no: str) -> dict:
    # 5%보고서 본체 = data.blocks[]{reporter, ownership_pct, purpose, report_reason, report_type}.
    # major_holders/summary.top_holder는 정기보고서 최대주주(폴백).
    data = payload.get("data") or {}
    blocks = data.get("blocks") or []
    b = next((x for x in blocks if x.get("rcept_no") == rcept_no), blocks[0] if blocks else None)
    if b:
        return {
            "holder": b.get("reporter"),
            "stake_pct": b.get("ownership_pct"),
            "purpose": b.get("purpose"),
            "change_kind": _first(b.get("report_reason"), b.get("report_type")),
        }
    holders = data.get("major_holders") or []
    top = (data.get("summary") or {}).get("top_holder") or {}
    row = holders[0] if holders else top
    return {
        "holder": _first(row.get("name"), row.get("holder_name")),
        "stake_pct": _first(row.get("ownership_pct"), row.get("stake_pct")),
        "purpose": row.get("purpose"),
        "change_kind": row.get("relation"),
    }


def _card_gap(g: dict | None) -> dict | None:
    """`provisional_earnings` 의 `consensus_gap` 을 카드 크기로. 계산은 다시 하지 않는다(규칙 6 — 그대로 옮긴다).

    카드에는 맞댄 카드·맞댄 지표만 싣는다(이유 문장은 `provisional_earnings` 에 있다). 출처·계산식 문장
    (`source`·`note`)은 카드마다 같아 md 각주에 한 번만 적는다 — 실적 카드가 40장이면 40번 싣지 않는다.
    """
    if not g or g.get("status") != "ok":
        # 맞대지 못한 카드엔 싣지 않는다(261018 결정) — 실적 카드의 2/3 가 월간이라 「비교 안 함」이 다이제스트를
        # 덮는다. 이유가 궁금하면 `provisional_earnings` 가 status·why 를 준다.
        return None
    out = {k: g[k] for k in ("status", "as_of", "lag_days", "period", "basis", "caveats")
           if g.get(k) not in (None, "", [])}
    metrics = {k: {kk: c[kk] for kk in ("consensus_krw", "diff_krw", "gap_pct", "gap_caveat") if c.get(kk) is not None}
               for k, c in (g.get("metrics") or {}).items() if c.get("diff_krw") is not None}
    if metrics:
        out["metrics"] = metrics
    return out


def _extract_earnings(payload: dict, rcept_no: str) -> dict:
    # 실제 payload: data.headline{revenue/operating_profit/net_income:{value_krw,yoy_pct}} + kind/consolidated
    d = payload.get("data") or {}
    h = d.get("headline") or {}
    def _v(k):
        return (h.get(k) or {}).get("value_krw")
    def _y(k):
        return (h.get(k) or {}).get("yoy_pct")
    return {
        "revenue_krw": _v("revenue"),
        "operating_profit_krw": _v("operating_profit"),
        "net_income_krw": _v("net_income"),
        "pretax_profit_krw": _v("pretax_profit"),
        "capital_stock_krw": _v("capital_stock"),
        "revenue_yoy_pct": _y("revenue"),
        "op_yoy_pct": _y("operating_profit"),
        "consolidated": d.get("consolidated"),
        "provisional_kind": d.get("kind"),  # financial | non_financial(자동차 판매대수 등)
        "provisional_type": d.get("provisional_type"),
        "period": d.get("period"),
        "fiscal_year": d.get("fiscal_year"),
        "fiscal_quarter": d.get("fiscal_quarter"),
        "period_kind": d.get("period_kind"),
        "period_month": d.get("period_month"),   # 월간 공시의 달 — 카드 라벨용(261018)
        "fiscal_year_end_month": d.get("fiscal_year_end_month"),
        "comparison_basis": d.get("comparison_basis"),
        "correction": d.get("correction"),
        "consensus_gap": _card_gap(d.get("consensus_gap")),   # 공시 직전 분기 컨센서스 대비(261018)
    }


# detail_kind → (import path, build fn name, extractor, call kwargs builder)
def _build_kwargs_narrow(filed_dt: str) -> dict:
    return {"start_date": filed_dt, "end_date": filed_dt}


def _build_kwargs_window(back_days: int, fwd_days: int = 3):
    """일부 파서(증자·CB 등)는 정정 rcept_dt≠이사회 결정일이라 좁은 창을 놓친다 → 완충 창."""
    def _mk(filed_dt: str) -> dict:
        try:
            d = datetime.strptime(filed_dt, "%Y%m%d").date()
        except (ValueError, TypeError):
            return {"start_date": filed_dt, "end_date": filed_dt}
        return {"start_date": _yyyymmdd(d - timedelta(days=back_days)),
                "end_date": _yyyymmdd(d + timedelta(days=fwd_days))}
    return _mk


_DETAIL_DISPATCH: dict[str, dict[str, Any]] = {
    "order": {"module": "open_proxy_mcp.services.order_contracts",
              "fn": "build_order_contracts_payload", "extract": _extract_order,
              "kwargs": _build_kwargs_narrow},
    "treasury": {"module": "open_proxy_mcp.services.treasury_share",
                 "fn": "build_treasury_share_payload", "extract": _extract_treasury,
                 "kwargs": _build_kwargs_narrow},
    "dividend": {"module": "open_proxy_mcp.services.dividend",
                 "fn": "build_dividend_payload", "extract": _extract_dividend,
                 "kwargs": _build_kwargs_narrow},
    "dilutive": {"module": "open_proxy_mcp.services.dilutive_issuance",
                 "fn": "build_dilutive_issuance_payload", "extract": _extract_dilutive,
                 # 정정/철회는 원 결정일이 앞서 있어 완충 창(뒤 60일)으로 되짚는다.
                 "kwargs": _build_kwargs_window(60)},
    "agm_notice": {"module": "open_proxy_mcp.services.shareholder_meeting",
                   "fn": "build_shareholder_meeting_payload", "extract": _extract_agm_notice,
                   # 주총소집은 rcept_no 직접 디스패치 지원(가장 정확)
                   "kwargs": lambda filed_dt: {"scope": "agenda"}},
    "ownership": {"module": "open_proxy_mcp.services.ownership_structure",
                  "fn": "build_ownership_structure_payload", "extract": _extract_ownership,
                  "kwargs": _build_kwargs_narrow},
    "earnings": {"module": "open_proxy_mcp.services.provisional_earnings",
                 "fn": "build_provisional_earnings_payload", "extract": _extract_earnings,
                 "kwargs": _build_kwargs_narrow},
    # 최대주주변경 계열은 접수번호 한 건을 바로 읽는다(서식 다섯 — 변경 사실·양수도·담보 체결·해제).
    "control": {"module": "open_proxy_mcp.services.control_change",
                "fn": "build_control_event_payload", "extract": _extract_control,
                "kwargs": lambda filed_dt: {}, "by_rcept": True},
}


async def _fetch_detail(hit: dict, running: dict) -> dict:
    """단일 hit의 details를 파서 디스패치로 채운다. 러닝 콜 캡 초과 시 skip.

    반환: {detail_status, fields, dart_calls}
    detail_status ∈ {parsed, partial, unparsed_image, no_data, skipped, error}
    """
    kind = hit.get("_detail_kind")
    disp = _DETAIL_DISPATCH.get(kind)
    if not disp:
        return {"detail_status": "no_data", "fields": {}}
    if hit.get("subsidiary_matter") and kind in _RCEPT_BLIND_KINDS:
        # 이 파서는 회사·날짜로만 공시를 찾아 접수번호를 보지 않는다 — 자회사 공시 카드에 모회사 자신의 값(신한지주 DPS 1,480원·
        # 기준일 2026-12-31 이 신한은행 중간배당 카드에 붙었다)이 실려 나간다. 틀린 숫자보다 빈 칸이 낫다: 열지 않고 밝힌다.
        return {"detail_status": "no_data", "fields": {},
                "note": _SUBSIDIARY_BLIND_NOTE}
    if running["calls"] >= _DETAILS_TOTAL_CALL_CAP:
        return {"detail_status": "skipped", "fields": {}, "note": "300콜 러닝캡 도달"}

    client = get_dart_client()
    import importlib
    mod = importlib.import_module(disp["module"])
    build_fn = getattr(mod, disp["fn"])
    company = hit.get("stock_code") or hit.get("corp_name")
    filed_dt = hit.get("filed_at", "").replace("-", "")
    kwargs = disp["kwargs"](filed_dt)
    # 주총소집은 rcept_no 직접 지정 경로 사용(가장 정확)
    if disp.get("by_rcept"):
        kwargs["rcept_no"] = hit.get("rcept_no", "")
        kwargs["report_nm"] = hit.get("title", "")
    if kind == "agm_notice":
        kwargs["rcept_no"] = hit.get("rcept_no", "")
        if hit.get("subtype") == "소집결의":
            # 결의는 공고와 서식이 다르다 — 공고 파서로 읽으면 안건 0건·정기/임시 뒤바뀜(대구백화점 20260811801007).
            kwargs["scope"] = "resolution"

    before = client.api_call_snapshot()
    try:
        payload = await build_fn(company, **kwargs)
    except DartClientError as exc:
        running["calls"] += max(0, client.api_call_snapshot() - before)
        return {"detail_status": "error", "fields": {}, "note": f"DART {exc.status}"}
    except Exception as exc:  # noqa: BLE001 — 파서 내부버그는 degrade(조작값 금지)
        running["calls"] += max(0, client.api_call_snapshot() - before)
        return {"detail_status": "error", "fields": {}, "note": f"{type(exc).__name__}"}
    running["calls"] += max(0, client.api_call_snapshot() - before)

    status = payload.get("status")
    fields = {}
    try:
        fields = disp["extract"](payload, hit.get("rcept_no", ""))
    except Exception:  # noqa: BLE001
        fields = {}
    fields = {k: v for k, v in fields.items() if v not in (None, "", [], {})}

    if status == "error":
        # 빌더가 조회 실패(020·800 등)를 error 봉투로 돌려줬다 — 「자료 없음」(공시 0건·no_data)과 같은 배지로 내지 않는다. 기존 상태값 그대로.
        warn = (payload.get("warnings") or [""])[0]
        return {"detail_status": "error", "fields": {}, **({"note": warn[:120]} if warn else {})}
    if status == "ambiguous":
        detail_status = "no_data"
    elif not fields:
        # 사건은 있으나 정형필드 추출 실패 → XML 불완전/이미지 degrade 가능성
        detail_status = "unparsed_image" if status == "partial" else "no_data"
    elif status == "partial":
        detail_status = "partial"
    else:
        detail_status = "parsed"
    return {"detail_status": detail_status, "fields": fields}


async def _fetch_amendment(hit: dict, running: dict) -> dict:
    """정정 카드에 「무엇이 바뀌었나」 — 회사가 붙인 정정 비교표를 원문 그대로 싣는다.

    유형 파서는 최신 정정본의 **지금 값**만 준다. 무엇이 바뀌었는지는 그 공시 머리의 비교표에만 있다.
    문서 1건 — 유형 파서가 방금 같은 원문을 열었으면 캐시에서 나와 DART 0콜이다(정형 API 만 쓰는
    자기주식·증자는 1콜). 콜은 details 러닝캡에 함께 센다.
    """
    if running["calls"] >= _DETAILS_TOTAL_CALL_CAP:
        return {"status": "skipped", "note": f"{_DETAILS_TOTAL_CALL_CAP}콜 러닝캡 도달"}
    client = get_dart_client()
    before = client.api_call_snapshot()
    try:
        doc = await client.get_document_cached(hit.get("rcept_no", ""))
    except DartClientError as exc:
        return {"status": "error", "note": f"DART {exc.status}"}
    except Exception as exc:  # noqa: BLE001 — 표를 못 읽어도 카드는 나간다(조작값 금지)
        return {"status": "error", "note": type(exc).__name__}
    finally:
        running["calls"] += max(0, client.api_call_snapshot() - before)
    return card_summary(parse_amendment_table((doc or {}).get("html") or ""))


# ══════════════════════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════════════════════

_DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r}"


def _resolve_types(types: str) -> tuple[list[str], list[str]]:
    """types 스펙 → (선택된 type_code 리스트, notices)."""
    notices: list[str] = []
    spec = (types or "core").strip()
    if spec in ("", "core"):
        return list(CORE_PRESET), notices
    if spec == "all":
        return [t["code"] for t in TYPE_REGISTRY], notices
    if spec == "governance":
        return list(GOVERNANCE_PRESET), notices
    codes = [c.strip() for c in re.split(r"[,\s]+", spec) if c.strip()]
    valid = [c for c in codes if c in _BY_CODE]
    unknown = [c for c in codes if c not in _BY_CODE]
    if unknown:
        notices.append(f"알 수 없는 유형 무시: {', '.join(unknown)}. 사용가능: {', '.join(_BY_CODE)}.")
    if not valid:
        notices.append("유효 유형 0개 → core 프리셋으로 대체.")
        return list(CORE_PRESET), notices
    return valid, notices


async def _build_screener_payload_impl(
    *,
    types: str = "core",
    period: str = "since_yesterday",
    universe: str = "all",
    details: bool | None = None,
    max_hits: int = 200,
    offset: int = 0,
    cursor: str = "",
    custom_start: str = "",
    custom_end: str = "",
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """범용 공시 스크리너 페이로드. scan(싸게) + (details=true면) 파서 디스패치.

    아침 디제스트 디폴트: types=core · period=since_yesterday · universe=all · details 미지정(=전체시장이라 꺼짐).
    `details` 를 말하지 않으면(None) 유니버스가 200종목 이하이고 기간이 7일 이하일 때만 켠다 — 명시한 값(true·false)은 그대로.

    260824: 인자를 **사람 말로도** 받는다(`_nl_*`). 「지난주」·「코스피 시총 상위 30」·
      「자사주, 배당」 같은 표현을 기존 코드로 정규화한 뒤 원래 리졸버에 넘긴다.
      옛 코드도 그대로 받는다.
    """
    warnings: list[str] = []
    client = get_dart_client()
    # ── 자연어 앞단 — 정규화만 하고 아래 로직은 그대로 ──
    _in = (types, period, universe)
    period, custom_start, custom_end = _nl_period(
        period, start_date, end_date, custom_start, custom_end)
    types = _nl_types(types)
    universe = _nl_universe(universe)
    # 무엇을 어떻게 알아들었는지 밝힌다 — 조용히 다른 걸 조회하면 사용자가 모른다.
    #   ★ 날짜를 직접 준 경우의 period 변화는 싣지 않는다. 사용자는 "since_yesterday" 라고
    #     말한 적이 없는데 「since_yesterday→custom」이라고 나오면 무슨 말인지 모른다
    #     (그 값은 함수 기본값이다). 해석 결과는 아래 기간 표시가 이미 보여준다.
    _date_driven = bool((start_date or end_date).strip())
    _chg = [f"{a}→`{b}`" for i, (a, b) in
            enumerate(zip(_in, (types, period, universe)))
            if a != b and not (i == 1 and _date_driven)]
    if _chg:
        warnings.append("입력 해석: " + " · ".join(_chg))
    calls_start = client.api_call_snapshot()

    sel_types, tnotes = _resolve_types(types)
    warnings += tnotes
    bgn_de, end_de, pnotes = resolve_period(period, cursor=cursor,
                                             custom_start=custom_start, custom_end=custom_end)
    warnings += pnotes
    uni = await resolve_universe(universe)
    if uni.notice:
        warnings.append(uni.notice)
    if uni.question:
        # 260918: 되물어야 하면 DART 를 부르지 않는다 — 시장 전체 결과를 「지정종목」 이름으로 내보내지 않는다.
        return {"tool": "screener", "status": "needs_input", "question": uni.question,
                "as_of": datetime.now(_KST).isoformat(timespec="seconds"),
                "period": {"label": period, "bgn_de": bgn_de, "end_de": end_de},
                "universe": {"label": uni.label, "resolved": False},
                "counts": {"scanned": 0, "hits": 0, "returned": 0}, "hits": [], "warnings": warnings}

    # ── 가드: all × details, 기간 폭 × details ─────────────────────────
    period_days = (datetime.strptime(end_de, "%Y%m%d") - datetime.strptime(bgn_de, "%Y%m%d")).days
    details_auto = False
    if details is None:
        uni_size = len(uni.allowed) if uni.allowed is not None else None
        if (uni_size is not None and uni_size <= _AUTO_DETAILS_MAX_UNIVERSE
                and period_days <= _AUTO_DETAILS_MAX_DAYS):
            details, details_auto = True, True
            warnings.append(
                f"유니버스 {uni_size}종목(≤{_AUTO_DETAILS_MAX_UNIVERSE}) · 기간 {bgn_de}~{end_de}(≤{_AUTO_DETAILS_MAX_DAYS}일 폭)이라 "
                "details 를 기본으로 켰다(금액·시총 대비 등 숫자 포함). 끄려면 details 를 꺼짐(boolean 거짓)으로 명시한다.")
        else:
            details = False
    details_effective = details
    details_preview = False
    if details:
        # 260902: 유니버스 크기로 막지 않는다. details 는 페이징 뒤에서 **이번 페이지에만**
        #   돌고 콜은 `_DETAILS_TOTAL_CALL_CAP` 로 묶이므로, 전체시장이어도 비용이 유한하다.
        #   남은 건 자르지 않고 `next_offset` 으로 넘긴다.
        if period_days > 30:
            details_effective = False
            warnings.append("기간>30일 × details=true는 콜 폭주 위험 → details를 껐다.")
        elif period_days > 7:
            details_preview = True
            warnings.append("기간>7일 → details는 preview(유형별 캡 1/2)로 제한했다.")

    # ── scan: 선택 유형이 쓰는 detail 코드 합집합만 스캔 ───────────────
    scan_codes = sorted({_BY_CODE[c]["scan_code"] for c in sel_types})
    # 코드 하나가 쓸 페이지 = 요청당 예산 ÷ 코드 수. 코드가 늘어도 한 요청의 총 콜은 그대로다.
    max_pages = list_pages_per_code(len(scan_codes))
    scan_status = "ok"
    scan_error: str | None = None
    raw_items: list[dict] = []
    scanned = 0
    truncated_scan = False
    coverage: list[dict] = []   # 코드별 「어디까지 봤나」 — 절단은 값이 아니라 모수를 바꾼다
    # 코드 5개는 서로 독립이라 순차로 돌 이유가 없었다. 총 콜 수는 그대로고
    #   속도만 `_throttle_api` 가 잡는다(910/분). 결과는 코드 순서로 복원한다.
    _scan_sem = asyncio.Semaphore(_SCAN_CONCURRENCY)

    async def _one(code: str):
        async with _scan_sem:
            return code, await _scan_code(client, code, bgn_de, end_de, max_pages)

    # `return_exceptions=True` — 한 코드가 죽어도 나머지 결과를 받는다. 안 주면 첫 예외가
    #   즉시 올라오면서 나머지 태스크가 **완료되지 않은 채 남는다**(고아 태스크).
    #   종전 순차 루프는 첫 실패에서 break 라 뒤 코드를 아예 안 봤으니, 이쪽이 더 낫다.
    _scanned_raw = await asyncio.gather(*[_one(c) for c in scan_codes], return_exceptions=True)
    _ok: list[tuple] = []
    for code, r in zip(scan_codes, _scanned_raw):
        if isinstance(r, BaseException):
            _ok.append((code, _scan_result([], 0, 0, 0, error=f"transport:{type(r).__name__}")))
        else:
            _ok.append(r)
    for code, res in sorted(_ok, key=lambda t: t[0]):
        err = res["error"]
        raw_items.extend(res["items"])
        scanned += res["total"]
        truncated_scan = truncated_scan or res["truncated"]
        coverage.append({"code": code, "total": res["total"],
                         "total_pages": res["total_pages"], "fetched_pages": res["fetched_pages"],
                         "received_pages": res["received_pages"],
                         "missing_pages": res["missing_pages"],
                         "seen_from": res["seen_from"], "seen_to": res["seen_to"],
                         "complete": res["complete"], "error": err})
        if err:
            # 병렬이라 「즉시 중단」은 못 한다(이미 다 던졌다). 대신 **전부 보고**한다 —
            #   종전 `break` 는 뒤 코드를 아예 안 돌아 어디까지 봤는지 알 수 없었다.
            scan_status = "partial"
            scan_error = err
            warnings.append(f"{code} 스캔 일부 실패(DART {err}) — 그 코드의 부분 결과만 반영.")
    if truncated_scan:
        # 절단은 에러가 아니라 **대체**다 — 세지 않으면 발생률을 영영 모르고, 상한을 얼마로
        # 올릴지 정할 근거도 없다.
        note_degradation("scan_page_truncated")
    # 사람이 읽는 절단 문장은 렌더러 한 곳에서만 만든다(`coverage` 를 읽어서). 여기서도 조립하면
    # 같은 사실이 한 화면에 두 번 찍힌다.

    # ── 분류 + universe 필터 + dedup ───────────────────────────────────
    allowed_selset = set(sel_types)
    classified: list[dict] = []
    for it in raw_items:
        type_code, subtype, is_corr = classify(it.get("report_nm", ""))
        if type_code is None or type_code not in allowed_selset:
            continue
        stock_code = (it.get("stock_code") or "").strip()
        if not uni.contains(stock_code, it.get("corp_cls", "")):
            continue
        tdef = _BY_CODE[type_code]
        rcept_dt = it.get("rcept_dt", "")
        filed_at = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}" if len(rcept_dt) == 8 else rcept_dt
        sub = _is_subsidiary_matter(it.get("report_nm", ""))
        classified.append({
            "rcept_no": it.get("rcept_no", ""),
            "corp_code": it.get("corp_code", ""),
            "corp_name": it.get("corp_name", ""),
            "stock_code": stock_code,
            "corp_cls": it.get("corp_cls", ""),
            "type": {"code": type_code, "label": tdef["label"], "tier": tdef["tier"]},
            "subtype": subtype,
            "title": (it.get("report_nm") or "").strip(),
            "filed_at": filed_at,
            "flr_nm": it.get("flr_nm", ""),
            "is_correction": is_corr,
            "stage": _stage_tag(type_code, it.get("report_nm", ""), subtype),
            # 자회사 공시는 모회사 자신의 공시와 키를 가른다 — 같은 키면 자회사 공시의 정정이 모회사 공시를 대체하거나
            # (원본 후보가 둘이라) 정정 짝을 못 찾는다.
            "dedup_key": f"{it.get('corp_code','')}:{type_code}:{subtype}" + (":자회사" if sub else ""),
            "_force_detail": _force_details(tdef, it.get("report_nm", ""), is_corr),
            "_detail_kind": tdef.get("detail_kind"),
        })
        if sub:
            classified[-1]["subsidiary_matter"] = True
        if type_code == "treasury" and subtype in _TREASURY_ACTION:
            classified[-1]["treasury_action"] = _TREASURY_ACTION[subtype]
        if tdef.get("dedup_by_filer"):
            # Opposing offerors/solicitors must remain separate discovery paths.
            # An absent filer cannot establish that two reports supersede each other.
            filer = (it.get("flr_nm") or "").strip() or it.get("rcept_no", "")
            classified[-1]["dedup_key"] += f":{filer}"

    # dedup: **정정본만** 원본을 대체한다.
    #
    # 종전엔 같은 `corp_code:type_code:subtype` 이면 무조건 최신 1건으로 접었는데, subtype 이
    # 사건 식별자가 아니다 — 공급계약 matcher 는 「체결」·「해지」 둘뿐이라 한 회사가 한 달에 낸
    # 계약 3건이 전부 같은 키로 수렴해 카드 1장이 됐다. 값이 아니라 **건수**가 틀리는데
    # 원문 링크를 눌러도 그 1건은 맞으니 오류가 드러나지 않는다.
    #
    # 원본↔정정 수렴은 원래 의도이므로 유지하되, 대체하는 쪽이 정정본일 때만 접는다.
    # list.json 은 정정본이 **어느 접수번호를 고쳤는지** 알려주지 않는다(필드 자체가 없다).
    # 그래서 원본이 창 안에 하나뿐일 때만 접는다. 둘 이상이면 어느 것의 정정인지 모르므로
    # 접지 않고 후보를 실어 남긴다 — 「모르면 지우지 않는다」. 종전 판은 무조건 **가장 최근**
    # 원본을 덮어, 정정이 옛 건의 정정일 때 그 사이의 최신 계약이 통째로 사라졌다.
    slots: dict[str, list[int]] = {}   # dedup_key → kept 안의 후보 위치들
    kept: list[dict] = []
    seen_rcept: set[str] = set()
    superseded = 0
    for row in sorted(classified, key=lambda r: r["rcept_no"]):
        rn = row["rcept_no"]
        if rn in seen_rcept:
            # 같은 공시가 두 번 들어오는 경우(페이지 경계·코드 중복 스캔). 종전 dict dedup 이
            # 부수적으로 막아 주던 것이라, 키를 완화하면서 이 그물이 사라졌었다.
            continue
        seen_rcept.add(rn)
        k = row["dedup_key"]
        cand = slots.setdefault(k, [])
        if row["is_correction"] and len(cand) == 1:
            at = cand[0]
            row["supersedes_rcept_no"] = kept[at]["rcept_no"]
            kept[at] = row        # 자리 유지 — 정정은 새 사건이 아니라 같은 사건의 최신판
            superseded += 1
        else:
            if row["is_correction"] and len(cand) > 1:
                # 후보가 여럿 — 접지 않고 무엇과 짝일 수 있는지를 함께 준다.
                row["ambiguous_correction"] = True
                row["correction_candidates"] = [kept[i]["rcept_no"] for i in cand]
            cand.append(len(kept))
            kept.append(row)
    hits, companion_merged = _merge_dividend_companions(kept)
    deduped_away = superseded
    hits.sort(key=lambda r: r["rcept_no"], reverse=True)  # 최신순

    # ── 시총 배치 부착(일별 시세 저장분 최신 거래일, DART 0콜) ─────────
    # DB 한 왕복은 이벤트 루프 밖 스레드에서 돈다(`asyncio.to_thread`).
    if uni.price_dd:
        cap_map = await asyncio.to_thread(_krx_mktcap_map, [h["stock_code"] for h in hits], uni.price_dd)
    else:
        cap_map = {}
    for h in hits:
        h["mktcap_won"] = cap_map.get(h["stock_code"])

    # 시총 큰 순 정렬(디제스트 상단에 대형사) — 시총 없으면 뒤로, 동률은 최신순
    hits.sort(key=lambda r: (r.get("mktcap_won") or -1, r["rcept_no"]), reverse=True)

    # ── 페이징: details 보다 **먼저** 자른다 (260902) ──────────────────
    #
    # 순서가 뒤바뀌어 있었다. 종전엔 details 를 전체 hits 에 돌린 뒤 `[:max_hits]` 로 잘랐다
    # — 돌려주지도 않을 건의 문서를 열고 그 콜을 버린 셈이다. 유니버스 가드로 details 자체를
    # 꺼야 했던 이유가 여기 있었다. 자르고 나서 열면 비용이 페이지 크기에 묶인다.
    total_hits = len(hits)
    offset = max(0, offset)
    page = hits[offset:offset + max_hits]
    next_offset = offset + len(page)
    has_more = next_offset < total_hits
    truncated_paging = has_more

    # ── details (이번 페이지만, per-type 캡 + 콜 러닝가드) ──────────────
    truncated_details = False
    if details_effective:
        running = {"calls": 0}
        per_type_count: dict[str, int] = {}
        # 우선순위: force_detail 먼저, 그다음 시총순(이미 정렬됨)
        ordered = sorted(page, key=lambda r: (not r["_force_detail"],))
        detail_targets = []
        for h in ordered:
            if h["_detail_kind"] is None:
                continue
            if h.get("subsidiary_matter") and h["_detail_kind"] in _RCEPT_BLIND_KINDS:
                # 문서를 열지 않는 카드(_fetch_detail 의 가드와 같은 사유)는 유형별 상한 셈에 넣지 않는다 — 모회사 시총순으로
                # 앞에 와서 열 수 있는 카드의 몫을 밀어내지 않게. 정정본은 정정 내역 조회를 위해 대상에는 남긴다.
                h["detail_status"] = "no_data"
                h["detail_fields"] = {}
                h["detail_note"] = _SUBSIDIARY_BLIND_NOTE
                if h.get("is_correction"):
                    detail_targets.append(h)
                continue
            tc = h["type"]["code"]
            cap = _BY_CODE[tc]["max_items"]
            if details_preview:
                cap = max(1, cap // 2)
            if per_type_count.get(tc, 0) >= cap:
                truncated_details = True
                continue
            per_type_count[tc] = per_type_count.get(tc, 0) + 1
            detail_targets.append(h)

        sem = asyncio.Semaphore(_DETAILS_CONCURRENCY)

        async def _run(h):
            async with sem:
                # sleep 없음 — 속도는 클라이언트 스로틀이 잡는다(위 상수 주석 참조).
                #   웹 폴백이 섞이면 `_throttle_scrape` 락에서 줄을 선다.
                res = await _fetch_detail(h, running)
                h["detail_status"] = res["detail_status"]
                h["detail_fields"] = res.get("fields", {})
                if res.get("note"):
                    h["detail_note"] = res["note"]
                if h.get("is_correction"):
                    h["amendment"] = await _fetch_amendment(h, running)

        await asyncio.gather(*[_run(h) for h in detail_targets])
        if running["calls"] >= _DETAILS_TOTAL_CALL_CAP:
            truncated_details = True
            warnings.append(
                f"details {_DETAILS_TOTAL_CALL_CAP}콜 러닝캡 도달 — 이 페이지의 일부 건은 "
                "scan-only로 남았다. max_hits를 줄여 다시 부르면 전부 채워진다.")

    # scan-only(details 미실행) 건은 detail_status 명시
    for h in page:
        h.setdefault("detail_status", "scan_only" if not details_effective else
                     ("no_data" if h["_detail_kind"] else "scan_only"))

    # ── 최종 카드 정리 ────────────────────────────────────────────────
    returned = page
    cards = [_finalize_card(h) for h in returned]

    no_new = len(hits) == 0
    status = "ok"
    if scan_status == "partial":
        status = "partial" if hits else "error"
    if scan_error and not hits:
        status = "error"

    as_of = datetime.now(_KST).replace(microsecond=0).isoformat()
    total_calls = client.api_call_snapshot() - calls_start

    return {
        "tool": "screener",
        "status": status,
        "no_new": no_new,
        "as_of": as_of,
        "period": {"label": period, "bgn_de": bgn_de, "end_de": end_de, "days": period_days},
        "universe": {"label": uni.label, "resolved": uni.resolved,
                     "size": (len(uni.allowed) if uni.allowed is not None else None),
                     "notice": uni.notice,
                     # 지수 구성종목 원장에서 만든 집합이면 그 구성의 마지막 확인 거래일(261018)
                     **({"members_dd": uni.members_dd} if uni.members_dd else {})},
        "coverage": coverage,
        "types": {"selected": sel_types, "scan_codes": scan_codes,
                  "details": details_effective, "details_preview": details_preview,
                  **({"details_auto": True} if details_auto else {}),
                  **({"interpretation": "discovery_only", "suggested_tool": "governance_screen",
                      "next_step": "발견된 공시의 회사를 중복 제거해 최대 30개씩 governance_screen으로 원문을 읽고 평가한다. 제목은 부정 신호 판정이 아니다."}
                     if types == "governance" else {})},
        "counts": {"scanned": scanned, "classified": len(classified),
                   # `matched` = 조건에 걸린 전체. `returned` = 이번 응답에 실은 수.
                   #   둘을 한 칸에 담으면 표시 한도를 매칭 수로 읽는다(U7 실측).
                   "matched": total_hits, "hits": total_hits, "returned": len(returned),
                   "truncated_scan": truncated_scan,
                   "deduped_away": deduped_away,
                   "companion_merged": companion_merged,
                   "truncated_details": truncated_details,
                   "truncated_paging": truncated_paging},
        "paging": {"offset": offset, "page_size": max_hits,
                   "matched": total_hits, "returned": len(returned),
                   "has_more": has_more,
                   "next_offset": next_offset if has_more else None},
        "warnings": warnings,
        "hits": cards,
        "next_cursor": end_de,   # 다음 실행은 이 값을 cursor로 → 반개구간[end_de, 다음)
        "usage": build_usage(total_calls),
    }


_DIVIDEND_DECISION_SUBTYPES = ("현금배당", "현물배당")


def _is_dividend_record_title(title: str) -> bool:
    """배당 전용 기준일 서식(「…배당을위한주주명부폐쇄(기준일)결정」, 중간·분기배당 포함)인지.

    유형 레지스트리의 「기준일」 매처(주주명부폐쇄·기준일)는 일반 서식 「주주명부폐쇄기간또는기준일설정」(임시·정기주총 권리주주
    확정용)과 자율공시 기준일 공고도 잡는다 — 서식 이름에 「배당」이 없으면 어느 결의의 기준일인지 알 수 없으므로 묶지 않는다.
    """
    return "배당을위한주주명부폐쇄" in _normalize_report_nm(title)


def _merge_dividend_companions(rows: list[dict]) -> tuple[list[dict], int]:
    """같은 날 같은 결의에서 나온 배당 결정 공시와 기준일(주주명부폐쇄) 결정 공시를 카드 하나로 묶는다.

    회사는 한 이사회 결의로 「현금ㆍ현물배당결정」과 「현금ㆍ현물배당을위한주주명부폐쇄(기준일)결정」을 따로 낸다(둘 다 같은
    결의일·같은 기준일). 신한지주 20261021800514(배당 결정)·20261021800503(기준일 결정)은 원문을 열어 보니 정정 짝이 아니라
    **신한은행 중간배당 한 결의의 두 서식**이었고, 카드 두 장이 같은 배당의 중복으로 읽혔다.

    list.json 에는 두 공시가 같은 결의인지 알려 주는 칸이 없다 — 그래서 **모르면 묶지 않는다**:
      · 같은 회사 · 같은 접수일 · 같은 자회사 여부 안에서 배당 결정이 **정확히 1건**, 기준일 공시가 **정확히 1건**이고
      · 둘 다 정정본이 아니고 정정 짝 모호 표시도 없을 때만 묶는다(여럿이면 어느 것이 짝인지 모른다).
      · 기준일 공시는 배당 전용 서식(제목에 「배당을위한주주명부폐쇄」)일 때만 짝이다 — 주총용 기준일 설정 공시는 묶지 않는다.
    묶여도 지우지 않는다 — 결정 공시 카드가 주 카드가 되고 기준일 공시는 `companions` 에 접수번호·링크와 함께 남는다.
    """
    groups: dict[tuple, dict[str, list[int]]] = {}
    for i, r in enumerate(rows):
        if r["type"]["code"] != "dividend":
            continue
        role = ("decision" if r["subtype"] in _DIVIDEND_DECISION_SUBTYPES
                else "record" if (r["subtype"] == "기준일" and _is_dividend_record_title(r["title"])) else None)
        if role is None:
            continue
        key = (r["corp_code"], r["filed_at"], bool(r.get("subsidiary_matter")))
        groups.setdefault(key, {"decision": [], "record": []})[role].append(i)
    drop: set[int] = set()
    for g in groups.values():
        if len(g["decision"]) != 1 or len(g["record"]) != 1:
            continue
        d, c = rows[g["decision"][0]], rows[g["record"][0]]
        if any(x.get("is_correction") or x.get("ambiguous_correction") for x in (d, c)):
            continue
        d.setdefault("companions", []).append({
            "rcept_no": c["rcept_no"], "subtype": c["subtype"], "title": c["title"],
            "filed_at": c["filed_at"],
            "dart_url": _DART_VIEWER.format(r=c["rcept_no"]) if c.get("rcept_no") else ""})
        drop.add(g["record"][0])
    return [r for i, r in enumerate(rows) if i not in drop], len(drop)


def _finalize_card(h: dict) -> dict:
    """내부필드(_...) 제거 + URL 부착한 공개 카드."""
    card = {k: v for k, v in h.items() if not k.startswith("_")}
    card["dart_url"] = _DART_VIEWER.format(r=h.get("rcept_no", "")) if h.get("rcept_no") else ""
    sc = h.get("stock_code")
    card["suggested_tool"] = _SUGGESTED_TOOL.get(h["type"]["code"], "")
    if _BY_CODE[h["type"]["code"]].get("interpretation") == "discovery_only":
        card["interpretation"] = "discovery_only"
        card["signal_basis"] = "filing_title"
    return card


async def build_screener_payload(*args, **kwargs):
    """이름이 정확히 맞지 않아 추정으로 고른 기업을 응답에 밝힌다.

    이 서비스는 `ToolEnvelope` 를 쓰지 않고 dict 를 직접 만들어 return 이 여러 곳에
    흩어져 있다 — 진입점 하나만 감싸 두면 새 return 이 늘어도 전파가 끊기지 않는다.
    """
    return declare_weak_resolution(await _build_screener_payload_impl(*args, **kwargs))
