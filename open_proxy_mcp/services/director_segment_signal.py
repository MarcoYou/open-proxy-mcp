"""사내이사 후보 커리어 → 담당 사업부문 매핑 + 부문 성과 참고 fact (260723 Phase 1).

배경: director_performance 매트릭스는 전사 지표(ROE·부채비율·CSR)만 본다. 부문장 출신
사내이사(예: 260723 LG화학 김동춘 — 첨단소재 라인 재직 중 전사 ROE로 '부진' 평가)는
본인이 책임지지 않은 부문 실적으로 감점될 수 있다. Phase 1은 담당 부문의 매출·영업이익
추이를 **참고(점수 미반영)** fact로만 첨부한다 — order_signal·영업이익률과 동일 패턴,
decision 로직에는 개입하지 않는다.

보수적 매핑 원칙 (오매핑 = 엉뚱한 부문 실적 노출이므로 miss보다 나쁨):
- business_details segments가 정형 고신뢰(status=OK, deterministic)일 때만 사용.
  NEEDS_REVIEW 마크다운 폴백은 쓰지 않는다 (조용한 오답 방지).
- 후보의 "이 회사" 경력 텍스트에 부문명이 **정확히 1개 부문**으로 매칭될 때만 부착.
  0개(전사 경영·매핑 불가) 또는 2개+(복수 부문 이력·모호)는 조용히 skip + status만 기록.
- 부문명 정규화는 business_details._norm_seg_name 재사용 (단일 소스 원칙).

주의(K-IFRS 1108): 영업부문은 회사가 재편·개명할 수 있어 연도 간 불연속 가능. 매출
지표 정의(외부매출 vs 총매출)도 회사별 상이 — revenue_metric/unit을 그대로 병기한다.
"""

from __future__ import annotations

import re
from typing import Any

from open_proxy_mcp.services.business_details import _norm_seg_name

# 커리어 텍스트 정규화 (회사명·부문명 substring 대조용) — 공백/괄호/법인 접두 제거
_CAREER_NORM_RE = re.compile(r"[\s㈜㈱()（）주식회사]")

# 부문장/사업부 이력 신호 — 이 키워드가 전무하면 segments fetch 자체를 skip (콜 절약 게이트)
_DIVISION_CAREER_RE = re.compile(r"(사업본부|사업부문|사업부|본부장|부문장|사업담당|사업총괄|BU장?|디비전)")

# 매핑 제외 부문명 (정규화 후) — 너무 일반적이라 오매칭 위험
_SEGMENT_STOPWORDS = {
    "기타", "공통", "본사", "연결", "국내", "해외", "합계",
    # 260723 리뷰 P1-4: 지역·기능 축 pseudo-부문 (커리어 텍스트에 흔히 등장해 오매칭 유발)
    "수출", "내수", "지주", "금융", "물류", "유통", "제조", "서비스", "영업", "관리",
}

# latin 부문명은 짧을수록 오매칭 위험이 크다("IT"→'it'가 "Digital"·"Security"에 substring 매치).
# 한글은 2자만 돼도 변별력이 있으나(전지·소재), latin은 3자 이상만 허용 (260723 리뷰 P1-4).
_MIN_LEN_HANGUL = 2
_MIN_LEN_LATIN = 3


def _min_norm_len(norm: str) -> int:
    return _MIN_LEN_HANGUL if re.search(r"[가-힣]", norm) else _MIN_LEN_LATIN


def _norm_text(text: str) -> str:
    return _CAREER_NORM_RE.sub("", text or "").lower()


def candidate_career_texts(candidate: dict[str, Any], company_name: str) -> list[str]:
    """후보의 '이 회사' 재직 경력 텍스트 풀.

    1순위: careerDetails.content / career_company_groups 중 회사명이 포함된 항목.
    회사명 표기 변형(LG화학 vs 엘지화학)으로 매칭 0건이면 main_job fallback —
    사내이사의 main_job(현직)은 역할 정의상 이 회사 직책이다.
    """
    own_norm = _norm_text(company_name)
    pool: list[str] = []

    faith = candidate.get("faithfulness") or {}
    main_job = (faith.get("main_job") or "").strip()

    # eval dict는 raw careerDetails를 안 갖는다 — faithfulness.career_company_groups가 정본.
    # (careerDetails가 있는 다른 호출 경로도 방어적으로 지원)
    for cd in candidate.get("careerDetails") or []:
        content = (cd.get("content") or "").strip()
        if content and own_norm and own_norm in _norm_text(content):
            pool.append(content)

    for grp in faith.get("career_company_groups") or []:
        co = (grp.get("company") or "").strip()
        items = [i for i in (grp.get("items") or []) if i]
        if own_norm and own_norm in _norm_text(co):
            # company 문자열 자체에 부문명이 붙는 형태 다수 ("(주)LG화학 첨단소재사업") — co 포함
            pool.append(co)
            pool.extend(items)

    if main_job and own_norm and own_norm in _norm_text(main_job):
        pool.append(main_job)

    # 회사명 매칭 0건 (LG화학 vs 엘지화학 표기 변형 가능) → main_job fallback.
    # 타사 경력 오매핑 방지 위해 전체 경력이 아니라 현직 직책 하나만 쓴다.
    if not pool and main_job:
        pool = [main_job]
    return pool


def map_candidate_to_segment(
    candidate: dict[str, Any],
    segment_names: list[str],
    company_name: str,
) -> dict[str, Any]:
    """커리어 → 부문 보수적 매핑.

    Returns {status, segment?, matched_from?}:
    - mapped: 정확히 1개 부문 매칭 → segment(원문 부문명) + matched_from(근거 경력 텍스트)
    - no_division_career: 부문장류 이력 키워드 없음 (segments fetch 전 게이트에도 사용)
    - no_match / ambiguous: 매칭 0개 / 2개+ — 부착하지 않음
    """
    texts = candidate_career_texts(candidate, company_name)
    joined_raw = " / ".join(texts)
    if not texts or not _DIVISION_CAREER_RE.search(joined_raw):
        return {"status": "no_division_career"}

    joined = _norm_text(joined_raw)
    matched: dict[str, str] = {}  # 원문 부문명 → 근거 텍스트
    for name in segment_names:
        norm = _norm_seg_name(name or "")
        if len(norm) < _min_norm_len(norm) or norm in _SEGMENT_STOPWORDS:
            continue
        if norm in joined:
            evidence = next((t for t in texts if norm in _norm_text(t)), joined_raw[:80])
            matched[name] = evidence
    if not matched:
        return {"status": "no_match"}
    if len(matched) > 1:
        return {"status": "ambiguous", "candidates": sorted(matched)}
    seg, evidence = next(iter(matched.items()))
    return {"status": "mapped", "segment": seg, "matched_from": evidence[:120]}


def has_division_career(candidates: list[dict[str, Any]]) -> bool:
    """사내이사 후보군에 부문장류 이력이 하나라도 있는지 — segments fetch 사전 게이트."""
    for ev in candidates:
        faith = ev.get("faithfulness") or {}
        blob = " ".join(
            [faith.get("main_job") or ""]
            + [cd.get("content") or "" for cd in ev.get("careerDetails") or []]
            + [g.get("company") or "" for g in faith.get("career_company_groups") or []]
            + [i for g in faith.get("career_company_groups") or [] for i in (g.get("items") or [])]
        )
        if _DIVISION_CAREER_RE.search(blob):
            return True
    return False


def extract_segment_items(bd_payload: dict[str, Any]) -> dict[str, Any] | None:
    """business_details payload에서 정형 고신뢰 segments만 추출. 아니면 None."""
    seg = ((bd_payload or {}).get("data") or {}).get("segments") or {}
    if seg.get("status") == "OK":  # business_details.OK — 정형 고신뢰(deterministic)만
        items = seg.get("items") or []
        if items:
            return {
                "items": items,
                "unit": seg.get("unit") or "",
                "revenue_metric": seg.get("revenue_metric") or "",
                "profit_metric": seg.get("profit_metric") or "",
            }
    return None


def build_segment_series(
    yearly_payloads: dict[int, dict[str, Any] | None],
    segment_name: str,
) -> list[dict[str, Any]]:
    """연도별 payload에서 매핑된 부문의 {fy, revenue, profit, unit} 시계열 (오름차순).

    부문명 매칭은 정규화 동치 — 연도 간 표기 변형(공백·'부문' 접미) 흡수, 재편으로
    사라진 연도는 그 연도만 빠진다 (불연속 명시는 render note가 담당).
    """
    target_norm = _norm_seg_name(segment_name)
    series: list[dict[str, Any]] = []
    for fy in sorted(yearly_payloads):
        extracted = extract_segment_items(yearly_payloads[fy] or {})
        if not extracted:
            continue
        row = next(
            (s for s in extracted["items"] if _norm_seg_name(s.get("name", "")) == target_norm),
            None,
        )
        if row is None:
            continue
        series.append({
            "fy": fy,
            "revenue": row.get("revenue"),
            "profit": row.get("profit"),
            "unit": extracted["unit"],
        })
    return series
