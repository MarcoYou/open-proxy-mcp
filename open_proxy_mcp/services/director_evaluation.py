"""director_evaluation — 이사/감사/감사위원 후보 평가 모듈.

3축: 독립성 / 충실성 / 결격사유.
**✅ 가능 항목만 메모에 표시**. 자동 검증 안 된 항목 (hard-fail)은 침묵.

매핑 분류 (모든 항목 주석):
- success: 정형 필드 직접 매핑
- soft-fail: raw text를 LLM에게 노출 (정규식/매칭 실패 시)
- hard-fail: 데이터 자체 미존재 — 메모/코드 모두 침묵 (코붕이 명시 지시)

Phase 1: 독립성 + 결격사유 (기본 매핑) + 후보 추출.
Phase 2 (다음 iteration): 충실성 — Marco 시나리오 (과거 회사 × 재직 기간 × 회계 risk).
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    build_filing_meta,
    build_usage,
)
from open_proxy_mcp.tools.parser import parse_personnel_xml


# ── 후보 데이터 fetch (success/soft-fail 분류) ──

async def fetch_appointments(
    corp_code: str,
    year: int,
    meeting_type: str = "annual",
) -> tuple[list[dict[str, Any]], str | None, list[dict[str, Any]]]:
    """주총소집공고 검색 + 후보 추출.

    meeting_type:
    - "annual": 정기주총만 (본문 detect == "annual" 인 첫 공고)
    - "extraordinary": 임시주총만 (본문 detect == "extraordinary" 인 첫 공고)
    - "auto": 가장 최신 주총소집공고 (분류 무관)

    매핑:
    - rcept_no, rcept_dt, report_nm → success (정형)
    - 본문 personnel section → parse_personnel_xml로 success / soft-fail
    - meeting_type 매칭 실패 시 다음 공고 fallback (사용자 logic)

    return: (appointments, rcept_no, filings_meta)
    """
    from open_proxy_mcp.tools.parser import detect_meeting_type

    client = get_dart_client()
    # 검색 범위: auto 또는 extraordinary는 연중, annual은 1-5월
    # (본문 detect가 최종 판단이므로 search 범위는 넉넉히)
    if meeting_type == "annual":
        bgn_de = f"{year}0101"
        end_de = f"{year}0501"
    else:
        bgn_de = f"{year}0101"
        end_de = f"{year}1231"

    # report_nm 필터 — "주주총회소집공고" 포함 (정기/임시 구분은 본문 detect로)
    def _filter(items: list) -> list:
        return [i for i in items if "주주총회소집공고" in (i.get("report_nm") or "")]

    # auto/extraordinary는 연중 검색이라 KOSPI 대형주 (공시 400+건) page 5+ 까지 확장.
    # annual은 1-5월 좁아 page 1-2 충분.
    max_pages = 2 if meeting_type == "annual" else 6
    notices: list = []
    accumulated: list = []  # 모든 페이지 누적 (가장 최신부터)
    for pg in range(1, max_pages + 1):
        try:
            data = await client.search_filings(
                corp_code=corp_code, bgn_de=bgn_de, end_de=end_de,
                pblntf_ty=None, page_no=pg, page_count=100,
            )
        except DartClientError as exc:
            if pg == 1:
                return [], None, [{"error": f"search_filings 실패: {exc.status} {exc}"}]
            break
        items = data.get("list", []) or []
        page_notices = _filter(items)
        accumulated.extend(page_notices)
        # total_count 적으면 더 이상 페이지 없음
        if pg * 100 >= int(data.get("total_count") or 0):
            break

    notices = accumulated
    if not notices:
        return [], None, [{"info": f"{year} {meeting_type} 주총소집공고 미발견 (page 1-{max_pages} 모두 시도)"}]

    # 본문 detect 기반 meeting_type 매칭 + 정정공고 처리.
    # 1) 시간 desc 순서대로 본문 fetch + detect_meeting_type
    # 2) meeting_type=="auto" → 첫 번째 채택
    #    meeting_type 명시 → detect 결과 매칭 시 채택, 아니면 다음 공고 fallback
    # 3) 매칭 후 parse 결과 빈 경우 (정정 본문 등) 다음 공고 fallback
    # 4) 최대 5개 notice 시도
    notice = notices[0]
    rcept_no = notice.get("rcept_no")
    last_text = ""
    last_meta: dict[str, Any] = {}
    appointments: list[dict[str, Any]] = []
    agenda_titles: list[str] = []
    detected_type = None
    matched = False
    skipped_for_type: list[str] = []

    for idx, candidate_notice in enumerate(notices[:5]):
        candidate_rcept = candidate_notice.get("rcept_no")
        try:
            doc = await client.get_document_cached(candidate_rcept)
        except Exception as exc:
            if idx == 0:
                return [], candidate_rcept, [{"error": f"get_document 실패: {exc}"}]
            continue

        text = doc.get("html") or doc.get("text") or ""
        if not text:
            continue

        # detect meeting_type from body
        candidate_detected = detect_meeting_type(text)

        # meeting_type 매칭 — auto면 모두 통과, 명시면 일치 필요
        type_ok = (meeting_type == "auto") or (candidate_detected == meeting_type)
        if not type_ok:
            skipped_for_type.append(f"{candidate_rcept}({candidate_detected})")
            continue

        parsed = parse_personnel_xml(text)
        candidate_appointments = parsed.get("appointments", []) or []
        try:
            from open_proxy_mcp.tools.parser import parse_agenda_xml
            agenda_items = parse_agenda_xml(text, html=text)
            candidate_agenda_titles = [a.get("title") for a in (agenda_items or []) if a.get("title")]
        except Exception:
            candidate_agenda_titles = []

        is_correction = candidate_notice.get("report_nm", "").startswith("[기재정정]")

        # 매칭된 첫 공고이거나, 결과 있으면 채택
        if not matched or candidate_appointments or candidate_agenda_titles:
            notice = candidate_notice
            rcept_no = candidate_rcept
            appointments = candidate_appointments
            agenda_titles = candidate_agenda_titles
            last_text = text
            last_meta = {"is_correction": is_correction}
            detected_type = candidate_detected
            matched = True
            if candidate_appointments or candidate_agenda_titles:
                break

    if not last_text:
        # meeting_type 매칭 실패 시 명시
        base_info = {
            "requested_meeting_type": meeting_type,
            "detected_meeting_type": None,
            "skipped_for_type_count": len(skipped_for_type),
            "skipped_for_type_sample": skipped_for_type[:3],
        }
        if skipped_for_type and meeting_type != "auto":
            return [], rcept_no, [{"info": f"{year} {meeting_type} 매칭 공고 없음 (본문 detect 기준)", **base_info}]
        return [], rcept_no, [{"error": "본문 비어 있음", **base_info}]

    return appointments, rcept_no, [{
        "rcept_no": rcept_no,
        "report_nm": notice.get("report_nm"),
        "agenda_titles": agenda_titles,
        "is_correction": last_meta.get("is_correction", False),
        "detected_meeting_type": detected_type,
        "requested_meeting_type": meeting_type,
        "fallback_attempts": min(len(notices), 5),
        "skipped_for_type_count": len(skipped_for_type),
    }]


# ── 독립성 평가 (모두 success — DART 정형 필드) ──

# 5년 룰: 같은 회사 사외이사 누적 5년+ → 독립성 의심
_FIVE_YEAR_KEYWORDS = ("재선임", "재임", "연임", "중임")

# "최근 2년 회사 직원" 매칭 키워드 (careerDetails content에서)
_RECENT_EMPLOYEE_KEYWORDS = ("재직", "근무", "임직원")


def _is_recent_employee(career_details: list[dict[str, Any]] | None, current_year: int) -> tuple[bool, str | None]:
    """careerDetails에서 "최근 2년 내 회사 직원" 여부 추정.

    매핑: success (정형 list) / soft-fail (period 형식 다양 — 정규식 실패 시 raw 노출)
    return: (matched, evidence_text or None)
    """
    if not career_details:
        return False, None
    for cd in career_details:
        period = (cd.get("period") or "").strip()
        content = (cd.get("content") or "").strip()
        if not any(kw in content for kw in _RECENT_EMPLOYEE_KEYWORDS):
            continue
        # period 정규식: "2024 ~ 2026", "2023.01 ~ 현재", "2022 ~"
        m = re.search(r"(\d{4})", period)
        if not m:
            continue
        start_year = int(m.group(1))
        end_year = current_year
        if "현재" in period or "재직" in content:
            end_year = current_year
        else:
            m2 = re.search(r"~\s*(\d{4})", period)
            if m2:
                end_year = int(m2.group(1))
        if end_year >= current_year - 2:
            return True, f"{period}: {content[:60]}"
    return False, None


def evaluate_independence(candidate: dict[str, Any], current_year: int) -> dict[str, Any]:
    """독립성 4 sub-factor 평가 (모두 success).

    return: {sub_factors: {key: {result, evidence}}, summary: str}
    """
    out: dict[str, Any] = {"sub_factors": {}}

    # ralph iter8 fix: 부정 표현 다양화 — "관계없음" / "해당없음" / "없습니다" 등
    # 이전엔 ("없음", "-", "")만 negation 인식 → "관계없음" → "related" 잘못 분류 → 모든 후보 indep concerns
    def _is_negation(s: str | None) -> bool:
        if s is None:
            return True
        s = s.strip()
        if s in ("", "-"):
            return True
        # "없" 포함 (관계없음 / 해당없음 / 거래 없음 / 없습니다 등) — soft pattern
        if "없" in s and len(s) <= 12:  # 짧은 부정구만 (긴 본문은 raw 노출)
            return True
        return False

    # 1. 최대주주/특수관계인 여부 → success (DART 정형 필드)
    msr = (candidate.get("majorShareholderRelation") or "").strip()
    is_independent_from_major = _is_negation(msr)
    out["sub_factors"]["major_shareholder_relation"] = {
        "result": "independent" if is_independent_from_major else "related",
        "raw": msr,
        "mapping": "success",
    }

    # 2. 회사와 거래 관계 (recent3yTransactions) → success
    rt = candidate.get("recent3yTransactions")
    has_transactions = not _is_negation(rt)
    out["sub_factors"]["recent_3y_transactions"] = {
        "result": "no_transactions" if not has_transactions else "transactions_exist",
        "raw": rt if rt else None,
        "mapping": "success",
    }

    # 3. 최근 2년 회사 직원 이력 → success/soft-fail
    employee_match, employee_ev = _is_recent_employee(
        candidate.get("careerDetails"), current_year
    )
    out["sub_factors"]["recent_2y_employee"] = {
        "result": "former_employee" if employee_match else "outsider",
        "evidence": employee_ev,
        "mapping": "success" if employee_ev or not candidate.get("careerDetails") else "soft-fail",
    }

    # 4. 5년 룰 (같은 회사 사외이사 5년+) — careerDetails에 회사 자체가 있으면 누적 체크
    # title의 action ("재선임"/"중임"/"연임") + 임기 정보로 보완. 여기는 단순 신호만.
    five_year_signal = any(
        kw in (cd.get("content", "") or "")
        for kw in _FIVE_YEAR_KEYWORDS
        for cd in (candidate.get("careerDetails") or [])
    )
    out["sub_factors"]["five_year_rule"] = {
        "result": "potential_long_tenure" if five_year_signal else "first_term_or_short",
        "mapping": "success",
    }

    # iter23: 5년 룰 위반 — 장기연임 (9-13년 audit case) → strong signal.
    # mainstream "장기연임 → 독립성 훼손 → AGAINST" (서진/심텍/고영/펩트론 등 6 case 일치)
    # five_year_signal은 careerDetails에 "재선임/재임/연임/중임" 키워드 발견 시 True.

    # ralph iter18 + iter27: indep summary 약화 — major_shareholder_relation 단독은 약한 신호.
    # iter27 추가: 한자/한글 친족 키워드 — 최대주주의 자녀/배우자/형제 등은 strong relation
    strong_flags = has_transactions or employee_match
    msr_strong_keywords = ("현직", "재직중", "현재")
    # 친족 표기 (한자/한글): 子(자) / 女(녀) / 父(부) / 母(모) / 兄(형) / 弟(제) / 姉/姊 / 妹 / 妻 / 夫
    # 한글: 자녀/배우자/형제/자매/처/남편/딸/아들/모친/부친
    msr_kinship_keywords = ("子", "女", "父", "母", "兄", "弟", "姉", "姊", "妹", "妻", "夫",
                            "자녀", "배우자", "형제", "자매", "처(妻)", "남편", "딸", "아들", "모친", "부친", "친족")
    msr_now = (msr or "") and (
        any(k in msr for k in msr_strong_keywords)
        or any(k in msr for k in msr_kinship_keywords)
    )
    if five_year_signal:
        # 장기연임은 audit/사외이사 모두 strong concerns
        out["summary"] = "long_tenure_concerns"
    elif strong_flags or (not is_independent_from_major and msr_now):
        out["summary"] = "concerns"
    elif not is_independent_from_major:
        out["summary"] = "weak_concerns"
    else:
        out["summary"] = "independent"
    return out


# ── 결격사유 평가 (✅ 가능 항목만: 나이 + eligibility 필드) ──

def evaluate_disqualification(candidate: dict[str, Any], current_year: int) -> dict[str, Any]:
    """결격사유 — ✅ 가능 항목만.

    return: {sub_factors: {...}, summary: str}
    """
    out: dict[str, Any] = {"sub_factors": {}}

    # 1. 미성년 체크 → success (birthDate 정형)
    # iter22 fix: birth_date format 다양 (1970-05-04 / 70.05.04 / 5378-05-04 잘못된 데이터 등).
    # 1900-현재 범위만 허용 — 음수 나이 방지 (현대오토에버 -5378세 같은 case red_flag 잘못 분류).
    bd = (candidate.get("birthDate") or "").strip()
    age = None
    if bd:
        m = re.search(r"(\d{4})", bd)
        if m:
            year = int(m.group(1))
            if 1900 <= year <= current_year:
                age = current_year - year
    is_minor = age is not None and 0 < age < 19
    out["sub_factors"]["age"] = {
        "result": "minor" if is_minor else "adult",
        "age": age,
        "mapping": "success",
    }

    # 2. eligibility 필드 (taxDelinquency / insolventMgmt / legalDisqualification) → success
    # ralph iter14: 한국 회계공시 negation 키워드 다양 — "부"(단독) / "미해당" / "해당없음" 추가.
    # 이전엔 "없음" / "충족" / "해당사항없음"만 → "부" / "미해당" 표기 회사 모두 잘못 red_flag 분류.
    elig = candidate.get("eligibility") or {}
    elig_flags: dict[str, str | None] = {}
    has_red = False
    NEGATION_TOKENS = ("없음", "없다", "없습니다", "충족", "해당사항없음", "해당없음", "미해당", "비해당", "해당안", "N", "n")
    for k in ("taxDelinquency", "insolventMgmt", "legalDisqualification"):
        v = elig.get(k)
        if not v or v in ("-", None):
            elig_flags[k] = None
            continue
        v_norm = str(v).replace(" ", "").strip()
        # 단독 "부" / "무" / "X" 단답형
        if v_norm in ("부", "무", "X", "x", "아니오", "아니요"):
            elig_flags[k] = None
            continue
        # 부정 키워드 substring
        if any(kw in v_norm for kw in NEGATION_TOKENS):
            elig_flags[k] = None
        else:
            has_red = True
            elig_flags[k] = v
    out["sub_factors"]["eligibility"] = {
        "result": "red_flag" if has_red else "clean",
        "raw_flags": {k: v for k, v in elig_flags.items() if v},
        "mapping": "success",
    }

    # ⚠️ hard-fail (메모에 안 적음): 형사 처벌 / 파산 / 임원 자격 박탈 / 사적 관계
    # → 코드/메모에서 침묵 (코붕이 지시)

    out["summary"] = "red_flag" if (is_minor or has_red) else "clean"
    return out


# ── 충실성 — Marco 시나리오 (과거 회사 × 재직 기간 × 회계 risk overlap) ──

# 한국 회사명 정규식 패턴 — careerCompanyGroups company 필드에서 추출.
# 예: "삼성전자 사외이사", "KB금융 ESG위원장", "POSCO홀딩스 부사장"
# 한국 회사 + 직책이 한 줄로 붙어 있는 케이스 대응.
_KOREAN_CORP_SUFFIX_RE = re.compile(
    r"([가-힣A-Za-z0-9&\(\)]+(?:홀딩스|금융지주|증권|건설|중공업|화학|전자|반도체|"
    r"바이오|제약|텔레콤|에너지|화공|상사|글로벌|디스플레이|자동차|생명과학)?[가-힣A-Za-z0-9]*)"
)


def _extract_korean_corp_names(career_company_groups: list[dict[str, Any]] | None) -> list[str]:
    """careerCompanyGroups → 한국 회사명 candidates list.

    매핑: success (회사명 추출 성공) / soft-fail (정규식 실패 시 raw 그대로 노출)
    """
    if not career_company_groups:
        return []
    names: list[str] = []
    for grp in career_company_groups:
        company = (grp.get("company") or "").strip()
        if not company:
            continue
        # 첫 segment 추출 (콤마/공백 분리)
        first = re.split(r"[,，\(]", company, maxsplit=1)[0].strip()
        if first and len(first) >= 2:
            names.append(first)
    return names


def _parse_career_period(period: str) -> tuple[int | None, int | None]:
    """careerDetails.period → (start_year, end_year). "현재" → None (current).

    매핑: success (정규식 매칭) / soft-fail (포맷 다른 케이스)
    """
    if not period:
        return None, None
    period = period.strip()
    # "2013 ~ 현재" / "2013.01 ~ 2024.03" / "2013-2024"
    m = re.match(r"(\d{4})", period)
    if not m:
        return None, None
    start = int(m.group(1))
    if "현재" in period:
        return start, None
    m2 = re.search(r"~\s*(\d{4})", period)
    if m2:
        return start, int(m2.group(1))
    m3 = re.search(r"-\s*(\d{4})", period)
    if m3:
        return start, int(m3.group(1))
    return start, None


# Marco 회사명 alias — 약칭 → DART 정식명. 매핑 실패 시 fallback 시도.
_MARCO_ALIASES = {
    "KT": "케이티",
    "kt": "케이티",
    "POSCO": "포스코홀딩스",
    "POSCO홀딩스": "포스코홀딩스",
    "포스코": "포스코홀딩스",
    "SK": "에스케이",
    "LG": "엘지",
    "GS": "지에스",
    "CJ": "씨제이",
    "KT&G": "케이티앤지",
    "BNK": "BNK금융지주",
    "DGB": "iM금융지주",
    "JB": "JB금융지주",
    "KB": "KB금융",
    "NH": "농협금융지주",
    "BKK": "케이비국민카드",  # 가능
    "삼전": "삼성전자",
    "현차": "현대자동차",
    "현대차": "현대자동차",
    "셀트리온헬스케어": "셀트리온",
    "카뱅": "카카오뱅크",
}


def _candidate_corp_names(corp_name: str) -> list[str]:
    """원본 + alias + 변형 candidates 생성 (lookup 시도용)."""
    out: list[str] = [corp_name]
    if corp_name in _MARCO_ALIASES:
        out.append(_MARCO_ALIASES[corp_name])
    # 영문 대문자 → 한글 변환 시도
    for k, v in _MARCO_ALIASES.items():
        if k in corp_name and v not in out:
            # 회사명에 alias key가 substring 포함 (예: "KT 사외이사")
            out.append(corp_name.replace(k, v))
    # 공백/특수문자 제거 변형
    cleaned = re.sub(r"[\s\(\)·,]+", "", corp_name)
    if cleaned and cleaned not in out:
        out.append(cleaned)
    return out


async def _resolve_marco_corp(corp_name: str) -> dict | None:
    """corp_name → DART corp_code 매칭 (multi-alias fallback)."""
    client = get_dart_client()
    for cand in _candidate_corp_names(corp_name):
        if not cand or len(cand) < 2:
            continue
        try:
            match = await client.lookup_corp_code(cand)
            if match and match.get("stock_code"):
                return match
        except Exception:
            continue
    return None


def _periods_overlap(p_start: int, p_end: int | None, risk_year: int) -> bool:
    """재직 기간 (p_start ~ p_end) 와 risk 발생 연도 partial overlap 체크.

    p_end=None → 현재까지 재직.
    """
    actual_end = p_end if p_end is not None else 2026
    return p_start <= risk_year <= actual_end


async def _check_marco_overlap(
    corp_name: str,
    period_start: int | None,
    period_end: int | None,
) -> dict[str, Any] | None:
    """과거 회사 cross-check (4 risk type, 재직 기간 partial overlap, 병렬).

    Risk 유형 (코붕이 지시):
    1. audit_opinion 적정 외 (한정/부적정/거절)
    2. capital_impairment_full (완전 자본잠식)
    3. 적자전환 후 적자지속/악화 (loss_conversion → continued_loss + 악화)
    4. 레버리지 가중 (debt 30%+ 증가) → 후 실적 악화

    return: red_flag dict / None.
    매핑: success (corp_code lookup OK) / soft-fail (alias 매칭 실패 시 None)
    """
    if not corp_name or not period_start:
        return None

    match = await _resolve_marco_corp(corp_name)
    if not match:
        return None  # soft-fail (코드 침묵, 메모에서 별도 raw 노출 — 호출자가 처리)
    past_corp_code = match["corp_code"]
    actual_corp_name = match.get("corp_name", corp_name)
    end_year = period_end if period_end is not None else 2026

    # 재직 기간 ∩ [2020, 2025] (DART 데이터 가용 윈도우)
    scan_start = max(period_start, 2020)
    scan_end = min(end_year, 2025)
    if scan_end < scan_start:
        return None

    # 4 risk 병렬 호출 — yoy scope (loss/debt) + audit_opinion + capital(summary)
    from open_proxy_mcp.services.financial_metrics import _safe_fetch_audit, _fetch_year_metrics

    async def fetch_year(y):
        # audit_opinion + summary metrics 동시 호출
        try:
            audit_rows, _ = await _safe_fetch_audit(past_corp_code, y)
        except Exception:
            audit_rows = []
        try:
            metrics, _ws, _ev = await _fetch_year_metrics(past_corp_code, y, "CFS", include_prev=True)
        except Exception:
            metrics = {}
        return y, audit_rows, metrics

    years = list(range(scan_start, scan_end + 1))
    results = await asyncio.gather(*[fetch_year(y) for y in years], return_exceptions=False)

    red_flags: list[dict[str, Any]] = []
    metrics_by_year: dict[int, dict[str, Any]] = {}
    for y, audit_rows, metrics in results:
        metrics_by_year[y] = metrics
        # 1. audit_opinion
        if audit_rows:
            r = audit_rows[0]
            op = (r.get("adt_opinion") or "").strip()
            if op and "적정" not in op:
                red_flags.append({
                    "type": "non_clean_audit_opinion",
                    "year": y, "opinion": op,
                    "company": actual_corp_name, "rcept_no": r.get("rcept_no"),
                })
        # 2. capital_impairment_full
        cap_status = (metrics or {}).get("capital_impairment_status")
        if cap_status == "full":
            red_flags.append({
                "type": "capital_impairment_full",
                "year": y, "ratio_pct": metrics.get("capital_impairment_ratio_pct"),
                "company": actual_corp_name,
            })

    # 3. 적자전환 후 적자지속/악화 — 재직 기간 안 연속 적자 + net_income 악화 체크
    sorted_years = sorted(metrics_by_year.keys())
    for i, y in enumerate(sorted_years[:-1]):
        curr = metrics_by_year.get(y) or {}
        nxt = metrics_by_year.get(sorted_years[i + 1]) or {}
        ni_curr = curr.get("net_income_krw")
        ni_nxt = nxt.get("net_income_krw")
        if ni_curr is None or ni_nxt is None:
            continue
        # 적자전환 후 (curr 적자) + (nxt 적자 또는 ni 악화)
        if ni_curr < 0 and ni_nxt < 0 and ni_nxt < ni_curr:
            # 이미 같은 type 한 번이면 skip (회사당 1건)
            if any(rf["type"] == "loss_continued_worsening" for rf in red_flags):
                continue
            red_flags.append({
                "type": "loss_continued_worsening",
                "year_from": y, "year_to": sorted_years[i + 1],
                "ni_from": ni_curr, "ni_to": ni_nxt,
                "company": actual_corp_name,
            })

    # 4. 레버리지 가중 (debt 30%+) 후 실적 악화 — 재직 기간 내 30%+ debt 증가 + 다음 연도 영업이익 악화
    for i, y in enumerate(sorted_years[:-1]):
        curr = metrics_by_year.get(y) or {}
        nxt = metrics_by_year.get(sorted_years[i + 1]) or {}
        debt_curr = curr.get("total_liabilities_krw")
        debt_nxt = nxt.get("total_liabilities_krw")
        op_curr = curr.get("operating_profit_krw")
        op_nxt = nxt.get("operating_profit_krw")
        if not all(v is not None for v in (debt_curr, debt_nxt, op_curr, op_nxt)):
            continue
        if debt_curr <= 0:
            continue
        debt_growth = (debt_nxt - debt_curr) / debt_curr
        if debt_growth >= 0.30 and op_nxt < op_curr:
            if any(rf["type"] == "leverage_surge_op_worsening" for rf in red_flags):
                continue
            red_flags.append({
                "type": "leverage_surge_op_worsening",
                "year_from": y, "year_to": sorted_years[i + 1],
                "debt_growth_pct": round(debt_growth * 100, 1),
                "op_from": op_curr, "op_to": op_nxt,
                "company": actual_corp_name,
            })

    # partial overlap 필터 — risk year가 재직 기간과 겹치는지
    overlapped = [
        rf for rf in red_flags
        if _periods_overlap(period_start, period_end, rf.get("year") or rf.get("year_to") or 0)
    ]

    if overlapped:
        return {
            "company": actual_corp_name,
            "alias_input": corp_name,
            "corp_code": past_corp_code,
            "tenure_start_year": period_start,
            "tenure_end_year": period_end,
            "red_flags": overlapped,
        }
    return None


async def evaluate_faithfulness(
    candidate: dict[str, Any],
    *,
    enable_marco: bool = False,
) -> dict[str, Any]:
    """충실성 평가.

    Phase 1 기본:
    - dutyPlan / recommendationReason → soft-fail (raw 노출, LLM 자연어 판단)
    - mainJob / recommender / careerCompanyGroups → success (구조화)

    enable_marco=True: 과거 회사 × 재직 기간 × 회계 risk overlap 자동 체크.
    Marco 시나리오는 추가 DART 호출 발생 (cost) — 옵션.
    """
    out: dict[str, Any] = {
        "duty_plan_raw": candidate.get("dutyPlan") or None,
        "recommendation_reason_raw": candidate.get("recommendationReason") or None,
        "main_job": candidate.get("mainJob"),
        "recommender": candidate.get("recommender"),
        "career_company_groups": candidate.get("careerCompanyGroups") or [],
    }

    # Marco 시나리오 — 과거 회사 × 재직 기간 cross-check
    marco_red_flags: list[dict[str, Any]] = []
    marco_status = "disabled"
    if enable_marco:
        marco_status = "checked"
        career_groups = candidate.get("careerCompanyGroups") or []

        # (corp_name, start, end) 튜플 list — 회사 + 기간 조합 모두 만들고 병렬 호출.
        tasks_meta: list[tuple[str, int, int | None]] = []
        for grp in career_groups:
            company_raw = (grp.get("company") or "").strip()
            if not company_raw:
                continue
            first_segment = re.split(r"[,，\(]", company_raw, maxsplit=1)[0].strip()
            corp_name_candidate = first_segment.split()[0] if first_segment else ""
            if not corp_name_candidate or len(corp_name_candidate) < 2:
                continue
            for period in (grp.get("items") or []):
                start, end = _parse_career_period(period)
                if start is None:
                    continue
                tasks_meta.append((corp_name_candidate, start, end))

        # asyncio.gather로 N 회사 × 기간 동시 — 속도 핵심 (코붕이 5번 지시).
        if tasks_meta:
            overlaps = await asyncio.gather(*[
                _check_marco_overlap(n, s, e) for n, s, e in tasks_meta
            ], return_exceptions=False)
            marco_red_flags = [o for o in overlaps if o]

    out["marco_scenario"] = {
        "status": marco_status,
        "red_flags": marco_red_flags,
        "summary": "red_flag" if marco_red_flags else ("clean" if marco_status == "checked" else "not_checked"),
    }

    # 통합 summary
    if marco_red_flags:
        out["summary"] = "concerns"
    else:
        out["summary"] = "raw_disclosed" if marco_status != "checked" else "clean"
    return out


# 후방 호환 alias (Phase 1 코드 사용 중)
def evaluate_faithfulness_basic(candidate: dict[str, Any]) -> dict[str, Any]:
    """동기 alias — Marco 비활성. enable_marco 옵션 없는 호출처용."""
    return {
        "duty_plan_raw": candidate.get("dutyPlan") or None,
        "recommendation_reason_raw": candidate.get("recommendationReason") or None,
        "main_job": candidate.get("mainJob"),
        "recommender": candidate.get("recommender"),
        "career_company_groups": candidate.get("careerCompanyGroups") or [],
        "marco_scenario": {"status": "disabled", "red_flags": [], "summary": "not_checked"},
        "summary": "raw_disclosed",
    }


# ── 후보 평가 통합 ──

def evaluate_candidate(candidate: dict[str, Any], current_year: int) -> dict[str, Any]:
    """단일 후보 → 3축 평가 dict (Marco 비활성, sync)."""
    return {
        "name": candidate.get("name"),  # success
        "birth_date": candidate.get("birthDate"),  # success
        "role_type": candidate.get("roleType"),  # success
        "separate_election": candidate.get("separateElection"),  # success (감사위원 분리선임)
        "independence": evaluate_independence(candidate, current_year),
        "faithfulness": evaluate_faithfulness_basic(candidate),
        "disqualification": evaluate_disqualification(candidate, current_year),
    }


async def evaluate_candidate_async(
    candidate: dict[str, Any],
    current_year: int,
    *,
    enable_marco: bool = False,
) -> dict[str, Any]:
    """단일 후보 평가 (async, Marco 옵션). Marco 활성 시 과거 회사 cross-check."""
    return {
        "name": candidate.get("name"),
        "birth_date": candidate.get("birthDate"),
        "role_type": candidate.get("roleType"),
        "separate_election": candidate.get("separateElection"),
        "independence": evaluate_independence(candidate, current_year),
        "faithfulness": await evaluate_faithfulness(candidate, enable_marco=enable_marco),
        "disqualification": evaluate_disqualification(candidate, current_year),
    }


# ── Public payload builder ──

async def build_director_evaluation_payload(
    company_query: str,
    *,
    year: int | None = None,
    meeting_type: str = "annual",
    enable_marco: bool = False,
) -> dict[str, Any]:
    from open_proxy_mcp.services.company import _company_id, resolve_company_query

    client = get_dart_client()
    calls_start = client.api_call_snapshot()

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="director_evaluation",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={"query": company_query, "usage": build_usage(client.api_call_snapshot() - calls_start)},
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="director_evaluation",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 후보 평가 자동 선택하지 않았다."],
            data={
                "query": company_query,
                "candidates": [{"corp_name": c.get("corp_name"), "corp_code": c.get("corp_code")} for c in resolution.candidates[:10]],
                "usage": build_usage(client.api_call_snapshot() - calls_start),
            },
        ).to_dict()

    selected = resolution.selected
    target_year = year or (date.today().year if date.today().month <= 5 else date.today().year)

    appointments, rcept_no, meta = await fetch_appointments(
        selected["corp_code"], target_year, meeting_type
    )

    # 후보별 평가
    evaluations: list[dict[str, Any]] = []
    candidate_count = 0
    for ap in appointments:
        cands = ap.get("candidates") or []
        for c in cands:
            ev = await evaluate_candidate_async(c, target_year, enable_marco=enable_marco)
            ev["agenda_title"] = ap.get("title")
            ev["agenda_action"] = ap.get("action")
            ev["agenda_category"] = ap.get("category")
            evaluations.append(ev)
            candidate_count += 1

    filing_meta = build_filing_meta(
        filing_count=len(appointments),
        parsing_failures=0,
    )
    if filing_meta["no_filing"]:
        status = AnalysisStatus.NO_FILING
    else:
        status = AnalysisStatus.EXACT

    evidence = []
    if rcept_no:
        evidence.append(EvidenceRef(
            evidence_id=f"ev_director_eval_{selected['corp_code']}_{target_year}",
            source_type=SourceType.DART_XML,
            rcept_no=rcept_no,
            section="주주총회소집공고 — 임원 선임",
            note=f"{candidate_count}명 후보 추출 / {len(appointments)} 안건",
        ))

    return ToolEnvelope(
        tool="director_evaluation",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=[],
        data={
            "query": company_query,
            "company_id": _company_id(selected),
            "canonical_name": selected.get("corp_name"),
            "year": target_year,
            "meeting_type": meeting_type,
            "appointments_count": len(appointments),
            "candidates_count": candidate_count,
            "evaluations": evaluations,
            "rcept_no": rcept_no,
            "agenda_titles_fallback": (meta[0].get("agenda_titles") if meta and meta[0].get("agenda_titles") else []),
            **filing_meta,
            "usage": build_usage(client.api_call_snapshot() - calls_start),
        },
        evidence_refs=evidence,
    ).to_dict()
