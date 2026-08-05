"""범용 공시 스크리너 — '무엇이 떴나'(scan, 싸게) + 필요 건만 '숫자'(details, 파서 디스패치).

대전제: 1순위 유즈 = **매일 아침 출근길 공시 알람 디제스트**. 전날(직전 실행 이후)~오늘
전체시장 주요 공시를, 폰에서 훑기 좋은 카드형 요약으로. 벤치마크 = 텔레그램 AWAKE.

아키텍처 핵심 사실(스카우트로 실측):
- `client.search_filings(bgn_de,end_de,pblntf_detail_ty,...)`는 **corp_code 없이 시장 전체**
  필러를 100/page로 반환(2026-07-14 I001 하루 102건=2페이지). universe는 메모리 사후필터.
- list.json item 필드 = corp_code / corp_name / stock_code / corp_cls / report_nm / rcept_no
  / flr_nm / rcept_dt / rm. **정정은 report_nm 프리픽스 `[기재정정]`/`[첨부정정]`**(rm은 시장마커 코/유).
- 시총은 `krx_weekly`(isu_cd=6자리 단축코드, mktcap=원, DART 0콜)에서 배치 파생.
- 유형 판별은 report_nm 키워드(B001은 `주요사항보고서(…)` 괄호 안 사유가 판별자).

게이트는 universe가 아니라 **details**. scan=발견/details=숫자.
"""

from __future__ import annotations

from open_proxy_mcp.services.contracts import declare_weak_resolution

import asyncio
import math
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import resolve_company_query

# ── KST(공시 기준 시간대) ──────────────────────────────────────────────
_KST = timezone(timedelta(hours=9))

# 시장스캔 하드캡: corp_code 없는 전체시장 검색은 3개월(92일)까지만.
_MARKET_SCAN_MAX_DAYS = 92

# scan 페이지 throttle / 상한
_SCAN_PAGE_SLEEP = 0.7      # 페이지 사이 sleep(레이트리밋 가드)
_SCAN_MAX_PAGES = 20        # 코드당 최대 페이지(전체시장 폭주 방지)
_PAGE_COUNT = 100

# details 러닝 가드
_DETAILS_TOTAL_CALL_CAP = 300   # run당 총 DART 콜 러닝카운터
_DETAILS_UNIVERSE_MAX = 300     # details 허용 유니버스 상한(초과=너무 넓음 → off)
_DETAILS_CONCURRENCY = 2
_DETAILS_SLEEP = 0.8

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
        "max_items": 20, "detail_kind": None, "force_keywords": ["정정"],
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
]

_BY_CODE = {t["code"]: t for t in TYPE_REGISTRY}

# 유형 → 상세페이지로 이어질 OPM tool 힌트(카드의 suggested_tool)
_SUGGESTED_TOOL = {
    "order": "order_contracts", "treasury": "treasury_share", "dividend": "dividend",
    "dilutive": "dilutive_issuance", "agm_notice": "shareholder_meeting_notice",
    "ownership5": "ownership_structure", "insider10": "ownership_structure",
    "earnings": "provisional_earnings", "agm_result": "shareholder_meeting_results",
    "restructuring": "corporate_restructuring", "stake_deal": "corporate_deals",
    "control_change": "ownership_structure", "litigation": "risk_events",
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


def resolve_period(period: str, *, cursor: str = "",
                   custom_start: str = "", custom_end: str = "") -> tuple[str, str, list[str]]:
    """period → (bgn_de, end_de, notices). 시장스캔 3개월 하드캡.

    반개구간[from,to) 커서: cursor(YYYYMMDD)가 오면 bgn을 그날로 오버라이드(직전 실행 이후).
    """
    notices: list[str] = []
    today = _today_kst()
    period = (period or "since_yesterday").strip().lower()

    if period == "today":
        bgn = end = today
    elif period == "yesterday":
        bgn = end = today - timedelta(days=1)
    elif period == "since_yesterday":
        bgn, end = today - timedelta(days=1), today
    elif period == "last_7d":
        bgn, end = today - timedelta(days=7), today
    elif period in ("last_30d", "30d"):
        bgn, end = today - timedelta(days=30), today
    elif period == "custom":
        try:
            bgn = datetime.strptime(custom_start, "%Y%m%d").date()
            end = datetime.strptime(custom_end or custom_start, "%Y%m%d").date()
        except (ValueError, TypeError):
            notices.append(f"custom 기간 파싱 실패(start={custom_start!r},end={custom_end!r}) → since_yesterday로 대체.")
            bgn, end = today - timedelta(days=1), today
    else:
        notices.append(f"알 수 없는 period={period!r} → since_yesterday로 대체.")
        bgn, end = today - timedelta(days=1), today

    # 커서(반개구간 시작) 오버라이드
    cur = (cursor or "").strip()
    if re.fullmatch(r"\d{8}", cur):
        cbgn = datetime.strptime(cur, "%Y%m%d").date()
        if cbgn <= end:
            bgn = cbgn

    # 3개월 하드캡
    if (end - bgn).days > _MARKET_SCAN_MAX_DAYS:
        bgn = end - timedelta(days=_MARKET_SCAN_MAX_DAYS)
        notices.append(f"시장스캔은 3개월(≤{_MARKET_SCAN_MAX_DAYS}일)까지만 — 시작일을 {_yyyymmdd(bgn)}로 절단했다.")
    if bgn > end:
        bgn = end
    return _yyyymmdd(bgn), _yyyymmdd(end), notices


# ══════════════════════════════════════════════════════════════════════
#  universe (krx_weekly 파생 — DART 0콜)
# ══════════════════════════════════════════════════════════════════════

def _krx_latest_dd() -> str | None:
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=8) as c:
            r = c.execute("SELECT MAX(bas_dd) FROM krx_weekly").fetchone()
            return r[0] if r and r[0] else None
    except Exception:
        return None


def _krx_mktcap_map(isu_cds: Iterable[str], bas_dd: str) -> dict[str, int]:
    """단축코드 집합 → {isu_cd: mktcap(원)}. 한 쿼리로 배치(이름기반 컬럼)."""
    codes = [c for c in {(c or "").strip() for c in isu_cds} if c]
    if not codes:
        return {}
    url = os.getenv("DATABASE_URL")
    if not url:
        return {}
    try:
        import psycopg
        out: dict[str, int] = {}
        with psycopg.connect(url, connect_timeout=10) as c:
            rows = c.execute(
                "SELECT isu_cd, mktcap FROM krx_weekly WHERE bas_dd=%s AND isu_cd = ANY(%s)",
                (bas_dd, codes),
            ).fetchall()
            for isu, mktcap in rows:
                if mktcap:
                    out[isu] = int(mktcap)
        return out
    except Exception:
        return {}


def _krx_top_mktcap(n: int, bas_dd: str, mkt: str | None = None) -> set[str]:
    """시총 상위 N 단축코드. mkt(KOSPI/KOSDAQ) 지정 시 그 시장만 — 시장 혼합 방지."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return set()
    try:
        import psycopg
        q = "SELECT isu_cd FROM krx_weekly WHERE bas_dd=%s AND mktcap IS NOT NULL"
        params: list = [bas_dd]
        if mkt:
            q += " AND mkt=%s"
            params.append(mkt)
        q += " ORDER BY mktcap DESC LIMIT %s"
        params.append(n)
        with psycopg.connect(url, connect_timeout=10) as c:
            return {r[0] for r in c.execute(q, tuple(params)).fetchall()}
    except Exception:
        return set()


def _krx_market_codes(mkt: str, bas_dd: str) -> set[str]:
    """한 시장(KOSPI/KOSDAQ) 전 종목 단축코드."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return set()
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=10) as c:
            rows = c.execute(
                "SELECT isu_cd FROM krx_weekly WHERE bas_dd=%s AND mkt=%s AND mktcap IS NOT NULL",
                (bas_dd, mkt),
            ).fetchall()
            return {r[0] for r in rows}
    except Exception:
        return set()


@dataclass(slots=True)
class UniverseFilter:
    label: str
    resolved: bool
    notice: str = ""
    allowed: set[str] | None = None   # None = 전체시장(필터 없음)
    bas_dd: str | None = None

    def contains(self, stock_code: str) -> bool:
        if self.allowed is None:
            return True
        return (stock_code or "").strip() in self.allowed


def _looks_like_code(tok: str) -> bool:
    """KRX 단축코드(6자 영숫자, 대부분 숫자)인가 — 회사명과 구분. 예: 005930, 900110, 0011A0."""
    return bool(re.fullmatch(r"[0-9A-Z]{6}", tok)) and sum(c.isdigit() for c in tok) >= 4


async def _resolve_custom_universe(raw: str, bas_dd: str | None) -> UniverseFilter:
    """custom:… 토큰을 코드/이름 혼용으로 해석. 코드는 그대로, 이름은 resolve_company_query로 코드화."""
    tokens = [t.strip() for t in re.split(r"[,，]+", raw) if t.strip()]
    if not tokens:
        return UniverseFilter(label="custom", resolved=False,
                              notice="custom:[…] 종목 파싱 실패 → 전체시장으로 대체.", allowed=None, bas_dd=bas_dd)
    codes: set[str] = set()
    notes: list[str] = []
    name_tokens: list[str] = []
    for tok in tokens:
        if _looks_like_code(tok.upper()):
            codes.add(tok.upper())
        else:
            name_tokens.append(tok)

    async def _resolve_one(tok: str) -> tuple[str, str | None]:
        # 이름/티커 → 회사 식별(기존 엔진 재사용, 이름당 DART 0콜=캐시)
        try:
            res = await resolve_company_query(tok)
        except Exception:
            return tok, None
        sel = getattr(res, "selected", None)
        return tok, ((sel or {}).get("stock_code") if sel else None)

    if name_tokens:
        for tok, sc in await asyncio.gather(*[_resolve_one(t) for t in name_tokens]):
            if sc:
                codes.add(sc.strip())
            else:
                notes.append(f"'{tok}' 미식별(모호/없음)")
    if not codes:
        notice = ("일부 종목 미해결: " + " · ".join(notes)) if notes else ""
        return UniverseFilter(label="지정종목", resolved=False,
                              notice=(notice + " → 전체시장으로 대체.").strip(), allowed=None, bas_dd=bas_dd)
    # 부분 해결 시: 해결분으로 진행함을 명시(전체 degrade로 오해 방지)
    notice = (f"해결된 {len(codes)}종목으로 진행 — 미해결: " + " · ".join(notes)) if notes else ""
    return UniverseFilter(label=f"지정 {len(codes)}종목", resolved=True,
                          notice=notice, allowed=codes, bas_dd=bas_dd)


async def resolve_universe(universe: str) -> UniverseFilter:
    """universe 스펙 → UniverseFilter. 디폴트 all=전체시장(필터 없음).

    지원 스펙:
      all · market:kospi|kosdaq · kospi200(=KOSPI 시총상위200) · kospi:N · kosdaq:N ·
      top_mktcap:N(전체시장 시총상위) · custom:코드|이름,… · sector:…(미구현 degrade)
    """
    spec = (universe or "all").strip()
    bas_dd = _krx_latest_dd()

    if spec in ("", "all"):
        return UniverseFilter(label="전체시장", resolved=True, allowed=None, bas_dd=bas_dd)

    low = spec.lower()

    def _rank(n: int, mkt: str | None, label: str) -> UniverseFilter:
        allowed = _krx_top_mktcap(n, bas_dd, mkt) if bas_dd else set()
        if not allowed:
            return UniverseFilter(label=label, resolved=False,
                                  notice="krx_weekly 조회 실패 → 전체시장으로 대체.", allowed=None, bas_dd=bas_dd)
        return UniverseFilter(label=label, resolved=True, allowed=allowed, bas_dd=bas_dd)

    # 시장 전체(랭킹 없음) — exact 매칭(kospi200/kospi:N 흡수 방지)
    if low in ("market:kospi", "kospi"):
        codes = _krx_market_codes("KOSPI", bas_dd) if bas_dd else set()
        return UniverseFilter(label="KOSPI 전체", resolved=bool(codes),
                              notice="" if codes else "krx_weekly 조회 실패 → 전체시장으로 대체.",
                              allowed=codes or None, bas_dd=bas_dd)
    if low in ("market:kosdaq", "kosdaq"):
        codes = _krx_market_codes("KOSDAQ", bas_dd) if bas_dd else set()
        return UniverseFilter(label="KOSDAQ 전체", resolved=bool(codes),
                              notice="" if codes else "krx_weekly 조회 실패 → 전체시장으로 대체.",
                              allowed=codes or None, bas_dd=bas_dd)

    # 시장별 시총 상위 N
    if low.startswith("kospi:") or low.startswith("kospi_top:"):
        try:
            n = int(spec.split(":", 1)[1])
        except ValueError:
            return UniverseFilter(label=spec, resolved=False, notice="kospi:N 파싱 실패 → 전체시장.", allowed=None, bas_dd=bas_dd)
        return _rank(n, "KOSPI", f"KOSPI 시총상위 {n}")
    if low.startswith("kosdaq:") or low.startswith("kosdaq_top:"):
        try:
            n = int(spec.split(":", 1)[1])
        except ValueError:
            return UniverseFilter(label=spec, resolved=False, notice="kosdaq:N 파싱 실패 → 전체시장.", allowed=None, bas_dd=bas_dd)
        return _rank(n, "KOSDAQ", f"KOSDAQ 시총상위 {n}")

    # KOSPI200 → 지수 원장 부재라 KOSPI 시총상위 200으로 대체(시장 분리됨, 안내)
    if low.startswith("kospi200"):
        uf = _rank(200, "KOSPI", "KOSPI200(→KOSPI 시총상위200 대체)")
        if uf.resolved:
            uf.notice = "KOSPI200 구성종목 원장이 없어 KOSPI 시총상위 200으로 대체했다(코스닥 미포함)."
        return uf

    # 전체시장 시총 상위 N (시장 혼합 — 라벨로 명시)
    if low.startswith("top_mktcap:"):
        try:
            n = int(spec.split(":", 1)[1])
        except ValueError:
            return UniverseFilter(label=spec, resolved=False,
                                  notice="top_mktcap:N 파싱 실패 → 전체시장으로 대체.", allowed=None, bas_dd=bas_dd)
        return _rank(n, None, f"전체시장 시총상위 {n}")

    if low.startswith("custom:"):
        return await _resolve_custom_universe(spec.split(":", 1)[1], bas_dd)

    if low.startswith("sector"):
        return UniverseFilter(
            label=spec, resolved=False,
            notice="섹터 필터는 미구현(KSIC 조인 TODO) — 전체시장으로 스캔했다.",
            allowed=None, bas_dd=bas_dd)

    return UniverseFilter(label=spec, resolved=False,
                          notice=f"알 수 없는 universe={spec!r} → 전체시장으로 대체.", allowed=None, bas_dd=bas_dd)


# ══════════════════════════════════════════════════════════════════════
#  scan (전체시장 페이지네이션 + throttle)
# ══════════════════════════════════════════════════════════════════════

async def _scan_code(client, detail_ty: str, bgn_de: str, end_de: str,
                     max_pages: int) -> tuple[list[dict], int, bool, str | None]:
    """한 detail 코드의 전체시장 필러를 페이지네이션(순차 + sleep). ReadError/차단코드 즉시 중단."""
    items: list[dict] = []
    try:
        first = await client.search_filings(
            bgn_de=bgn_de, end_de=end_de, pblntf_detail_ty=detail_ty,
            page_no=1, page_count=_PAGE_COUNT)
    except DartClientError as exc:
        if exc.status == "013":  # 해당 없음 = 정상 no-data
            return [], 0, False, None
        return [], 0, False, exc.status
    total = int(first.get("total_count", 0) or 0)
    items.extend(first.get("list", []))
    total_pages = max(1, math.ceil(total / _PAGE_COUNT)) if total else 1
    fetch_pages = min(total_pages, max_pages)
    truncated = total_pages > max_pages
    for p in range(2, fetch_pages + 1):
        await asyncio.sleep(_SCAN_PAGE_SLEEP)
        try:
            page = await client.search_filings(
                bgn_de=bgn_de, end_de=end_de, pblntf_detail_ty=detail_ty,
                page_no=p, page_count=_PAGE_COUNT)
        except DartClientError as exc:
            return items, total, truncated, exc.status  # 즉시 중단(레이트리밋 가드)
        items.extend(page.get("list", []))
    return items, total, truncated, None


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
        "revenue_yoy_pct": _y("revenue"),
        "op_yoy_pct": _y("operating_profit"),
        "consolidated": d.get("consolidated"),
        "provisional_kind": d.get("kind"),  # financial | non_financial(자동차 판매대수 등)
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
    if kind == "agm_notice":
        kwargs["rcept_no"] = hit.get("rcept_no", "")

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

    if status in ("error", "ambiguous"):
        detail_status = "no_data"
    elif not fields:
        # 사건은 있으나 정형필드 추출 실패 → XML 불완전/이미지 degrade 가능성
        detail_status = "unparsed_image" if status == "partial" else "no_data"
    elif status == "partial":
        detail_status = "partial"
    else:
        detail_status = "parsed"
    return {"detail_status": detail_status, "fields": fields}


# ══════════════════════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════════════════════

_DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r}"
_NAVER = "https://finance.naver.com/item/main.naver?code={c}"


def _resolve_types(types: str) -> tuple[list[str], list[str]]:
    """types 스펙 → (선택된 type_code 리스트, notices)."""
    notices: list[str] = []
    spec = (types or "core").strip()
    if spec in ("", "core"):
        return list(CORE_PRESET), notices
    if spec == "all":
        return [t["code"] for t in TYPE_REGISTRY], notices
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
    details: bool = False,
    max_hits: int = 200,
    cursor: str = "",
    custom_start: str = "",
    custom_end: str = "",
) -> dict[str, Any]:
    """범용 공시 스크리너 페이로드. scan(싸게) + (details=true면) 파서 디스패치.

    아침 디제스트 디폴트: types=core · period=since_yesterday · universe=all · details=false.
    """
    warnings: list[str] = []
    client = get_dart_client()
    calls_start = client.api_call_snapshot()

    sel_types, tnotes = _resolve_types(types)
    warnings += tnotes
    bgn_de, end_de, pnotes = resolve_period(period, cursor=cursor,
                                             custom_start=custom_start, custom_end=custom_end)
    warnings += pnotes
    uni = await resolve_universe(universe)
    if uni.notice:
        warnings.append(uni.notice)

    # ── 가드: all × details, 기간 폭 × details ─────────────────────────
    period_days = (datetime.strptime(end_de, "%Y%m%d") - datetime.strptime(bgn_de, "%Y%m%d")).days
    details_effective = details
    details_preview = False
    if details:
        # 게이트는 유니버스 "크기" — market:kospi(전종목)처럼 넓으면 좁힌 게 아니라 details off.
        if uni.allowed is None or len(uni.allowed) > _DETAILS_UNIVERSE_MAX:
            details_effective = False
            warnings.append(f"유니버스가 너무 넓어(전체시장 또는 {_DETAILS_UNIVERSE_MAX}종목 초과) details를 껐다 — 콜 폭주 방지. 좁은 유니버스(top_mktcap:N / kospi:N ≤{_DETAILS_UNIVERSE_MAX} / custom:종목)에서만 켜진다.")
        elif period_days > 30:
            details_effective = False
            warnings.append("기간>30일 × details=true는 콜 폭주 위험 → details를 껐다.")
        elif period_days > 7:
            details_preview = True
            warnings.append("기간>7일 → details는 preview(유형별 캡 1/2)로 제한했다.")

    # ── scan: 선택 유형이 쓰는 detail 코드 합집합만 스캔 ───────────────
    scan_codes = sorted({_BY_CODE[c]["scan_code"] for c in sel_types})
    # 스캔 폭 = 기간에 비례하되 코드당 상한
    max_pages = _SCAN_MAX_PAGES if period_days <= 30 else _SCAN_MAX_PAGES
    scan_status = "ok"
    scan_error: str | None = None
    raw_items: list[dict] = []
    scanned = 0
    truncated_scan = False
    for code in scan_codes:
        items, total, trunc, err = await _scan_code(client, code, bgn_de, end_de, max_pages)
        raw_items.extend(items)
        scanned += total
        truncated_scan = truncated_scan or trunc
        if err:
            scan_status = "partial"
            scan_error = err
            warnings.append(f"{code} 스캔 중단(DART {err}) — 부분 결과만 반영.")
            if err in ("020", "011", "012") or err.startswith("0"):
                break  # 레이트리밋/차단 계열은 즉시 전면 중단
        if code != scan_codes[-1]:
            await asyncio.sleep(_SCAN_PAGE_SLEEP)

    # ── 분류 + universe 필터 + dedup ───────────────────────────────────
    allowed_selset = set(sel_types)
    classified: list[dict] = []
    for it in raw_items:
        type_code, subtype, is_corr = classify(it.get("report_nm", ""))
        if type_code is None or type_code not in allowed_selset:
            continue
        stock_code = (it.get("stock_code") or "").strip()
        if not uni.contains(stock_code):
            continue
        tdef = _BY_CODE[type_code]
        rcept_dt = it.get("rcept_dt", "")
        filed_at = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}" if len(rcept_dt) == 8 else rcept_dt
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
            "dedup_key": f"{it.get('corp_code','')}:{type_code}:{subtype}",
            "_force_detail": _force_details(tdef, it.get("report_nm", ""), is_corr),
            "_detail_kind": tdef.get("detail_kind"),
        })

    # dedup: 같은 dedup_key는 최신 rcept_no만(원본↔정정 수렴). 정정본이 원본을 supersede.
    by_key: dict[str, dict] = {}
    for row in sorted(classified, key=lambda r: r["rcept_no"]):
        prev = by_key.get(row["dedup_key"])
        if prev is not None:
            row["supersedes_rcept_no"] = prev["rcept_no"]
        by_key[row["dedup_key"]] = row
    hits = list(by_key.values())
    hits.sort(key=lambda r: r["rcept_no"], reverse=True)  # 최신순

    # ── 시총 배치 부착(krx_weekly, DART 0콜) ───────────────────────────
    if uni.bas_dd:
        cap_map = _krx_mktcap_map((h["stock_code"] for h in hits), uni.bas_dd)
    else:
        cap_map = {}
    for h in hits:
        h["mktcap_won"] = cap_map.get(h["stock_code"])

    # 시총 큰 순 정렬(디제스트 상단에 대형사) — 시총 없으면 뒤로, 동률은 최신순
    hits.sort(key=lambda r: (r.get("mktcap_won") or -1, r["rcept_no"]), reverse=True)

    # ── details (선택 건만, per-type 캡 + 300콜 러닝가드) ───────────────
    truncated_details = False
    if details_effective:
        running = {"calls": 0}
        per_type_count: dict[str, int] = {}
        # 우선순위: force_detail 먼저, 그다음 시총순(이미 정렬됨)
        ordered = sorted(hits, key=lambda r: (not r["_force_detail"],))
        detail_targets = []
        for h in ordered:
            if h["_detail_kind"] is None:
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
                await asyncio.sleep(_DETAILS_SLEEP)
                res = await _fetch_detail(h, running)
                h["detail_status"] = res["detail_status"]
                h["detail_fields"] = res.get("fields", {})
                if res.get("note"):
                    h["detail_note"] = res["note"]

        await asyncio.gather(*[_run(h) for h in detail_targets])
        if running["calls"] >= _DETAILS_TOTAL_CALL_CAP:
            truncated_details = True
            warnings.append("details 300콜 러닝캡 도달 — 일부 건은 scan-only로 남았다.")

    # scan-only(details 미실행) 건은 detail_status 명시
    for h in hits:
        h.setdefault("detail_status", "scan_only" if not details_effective else
                     ("no_data" if h["_detail_kind"] else "scan_only"))

    # ── 최종 카드 정리 + paging ────────────────────────────────────────
    returned = hits[:max_hits]
    truncated_paging = len(hits) > max_hits
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
                     "notice": uni.notice},
        "types": {"selected": sel_types, "scan_codes": scan_codes,
                  "details": details_effective, "details_preview": details_preview},
        "counts": {"scanned": scanned, "classified": len(classified),
                   "hits": len(hits), "returned": len(returned),
                   "truncated_scan": truncated_scan,
                   "truncated_details": truncated_details,
                   "truncated_paging": truncated_paging},
        "warnings": warnings,
        "hits": cards,
        "next_cursor": end_de,   # 다음 실행은 이 값을 cursor로 → 반개구간[end_de, 다음)
        "usage": {"dart_api_calls": total_calls, "mcp_tool_calls": 1,
                  "dart_daily_limit_per_minute": 1000},
    }


def _finalize_card(h: dict) -> dict:
    """내부필드(_...) 제거 + URL 부착한 공개 카드."""
    card = {k: v for k, v in h.items() if not k.startswith("_")}
    card["dart_url"] = _DART_VIEWER.format(r=h.get("rcept_no", "")) if h.get("rcept_no") else ""
    sc = h.get("stock_code")
    card["naver_url"] = _NAVER.format(c=sc) if sc else ""
    card["suggested_tool"] = _SUGGESTED_TOOL.get(h["type"]["code"], "")
    return card


async def build_screener_payload(*args, **kwargs):
    """이름이 정확히 맞지 않아 추정으로 고른 기업을 응답에 밝힌다.

    이 서비스는 `ToolEnvelope` 를 쓰지 않고 dict 를 직접 만들어 return 이 여러 곳에
    흩어져 있다 — 진입점 하나만 감싸 두면 새 return 이 늘어도 전파가 끊기지 않는다.
    """
    return declare_weak_resolution(await _build_screener_payload_impl(*args, **kwargs))
