"""director_board — 이사회/개별 이사 프로필 (보수·소진율·재직/사퇴·출석률) Action Tool.

`corp_gov_report`가 "회사 지배구조 15지표 준수 여부"라면, 이 tool은 "개별 이사 단위" 정보 —
누가 얼마 받고(보수), 승인한도를 얼마나 썼고(소진율), 인원이 바뀌었고(재직/사퇴), 이사회에 얼마나
나왔는지(출석률) — 를 잡는다. 스튜어드십 engagement·보수 안건 판단·거버넌스 스크리닝에 쓴다.

목표 질문 3개 (사용자 260708):
  ① 이사 인당 보수가 적절한가        → psn1_avrg_pymntamt (DART API 직접 제공)
  ② 보수한도 소진율은 얼마인가        → Σ실지급 ÷ 주총승인 한도
  ③ 사퇴/인원변동으로 인당보수·소진율이 바뀌었나 → 연도간 임원 diff × 보수 변동 교차

데이터 소스 (전부 신규 스콥 — corp_gov_report·director_performance·shareholder_meeting과 무중복):
  - 정형 API(DS002 정기보고서): exctvSttus(임원현황) · drctrAdtAllMendngSttus*(보수한도·실지급) ·
    hmvAuditIndvdlBySttus(개인별 5억+).
  - 원문 파서(지배구조보고서, scope=attendance): 개별 이사 출석률 매트릭스 · 표4-2-1 선임/변동 ·
    표5-2-1 겸직. (v1은 정형+출석; 금융지주 PDF 별도양식은 v2 — 경고 반환)

scope:
  - compensation : 인당보수·보수한도·소진율 (정형 API)          [🟢 바로]
  - roster       : 임원현황 + 재직/사퇴 감지 (연도 diff)         [🟢🟡]
  - attendance   : 개별 이사 출석률·선임변동·겸직 (원문 파서)     [🟡]  (v1 stub→차기)
  - summary      : 위 종합 + 판단(인당보수·소진율·사퇴영향)

주의 (data QA 260708 검증):
  1. 버킷 불일치 — 한도(gmtsckConfmAmount)는 "등기이사" 단일버킷(사외 포함) vs 실지급
     (MendngPymntamtTyCl)은 등기(사외·감사위 제외)/사외/감사위원 세분. 소진율 계산 시 실지급 버킷
     합을 한도와 대응(단순 join 금지).
  2. 한도 공백 — 새 주총 결의 없는 해엔 gmtsck_confm_amount="-" → 최근 유효연도 lookback.
  3. 감사 보수한도 분리 — 감사위원은 이사 한도 안, 별도 감사(비위원회)는 별도 한도.
  4. 5억 미만 비공개 — 개인별은 상위 일부만(범주 평균은 전원).
"""

from __future__ import annotations

import asyncio
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import resolve_company_query
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    ToolEnvelope,
    build_usage,
)

_SUPPORTED_SCOPES = {"compensation", "roster", "individual", "unregistered", "pay_gap",
                     "pay_agenda", "attendance", "summary"}

# 소진율 임계 (참고용 flag — 판단이 아니라 신호)
_UTILIZATION_HIGH = 90.0   # 한도의 90%+ 소진 = 여유 적음
_ATTENDANCE_LOW = 75.0     # 출석률 75% 미만 = 저조 flag

_REPRT_ANNUAL = "11011"    # 사업보고서


def _to_int(value: Any) -> int | None:
    """DART 정형 금액/인원 문자열('21,800,000,000'·'-'·'')을 int로. 공백/미기재는 None."""
    if value is None:
        return None
    s = str(value).replace(",", "").strip()
    if not s or s in {"-", "해당없음", "해당사항없음"}:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


async def _fetch_rows(coro) -> list[dict[str, Any]]:
    """DART 정형 API 응답에서 list만 안전 추출. status!=000이면 빈 리스트(no_data와 오류 구분은 상위)."""
    resp = await coro
    if (resp or {}).get("status") != "000":
        return []
    return resp.get("list") or []


# ── compensation: 인당보수·한도·소진율 ──────────────────────────────────────

def _compensation_from_rows(
    limit_rows: list[dict[str, Any]], actual_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """주총 승인한도(limit) vs 유형별 실지급(actual)으로 소진율·인당보수 계산.

    버킷 불일치(주의 1) 대응: 실지급은 유형별 여러 행이라 '이사 한도' 대상 유형만 합산.
    실지급 유형 se 예: '등기이사(사외이사, 감사위원회 위원 제외)'·'사외이사'·'감사위원회 위원'·'감사'.
    이사 보수한도 소진율 = (감사 제외 이사류 실지급 합) ÷ 이사 승인한도.
    """
    # 유형별 실지급 정리
    pay_by_type: list[dict[str, Any]] = []
    for r in actual_rows:
        se = (r.get("se") or "").strip()
        pay_by_type.append({
            "type": se,
            "headcount": _to_int(r.get("nmpr")),
            "total_paid_krw": _to_int(r.get("pymnt_totamt")),
            "per_capita_krw": _to_int(r.get("psn1_avrg_pymntamt")),
        })

    # 이사 승인한도 (감사 별도 한도 제외 — se에 '감사'만 단독인 행은 감사 한도)
    director_limit_krw = None
    director_limit_headcount = None
    audit_limit_krw = None
    for r in limit_rows:
        se = (r.get("se") or "").strip()
        amt = _to_int(r.get("gmtsck_confm_amount"))
        n = _to_int(r.get("nmpr"))
        if "감사" in se and "위원" not in se and "이사" not in se:
            audit_limit_krw = amt if amt is not None else audit_limit_krw
        else:
            # '이사'·'등기이사'·'이사·감사' 통합 등 → 이사 한도로 취급
            if amt is not None:
                director_limit_krw = amt
                director_limit_headcount = n

    # 이사류 실지급 합 (감사 단독 제외; 감사위원은 이사 한도 안이므로 포함)
    director_paid_total = 0
    has_director_paid = False
    for p in pay_by_type:
        t = p["type"]
        if "감사" in t and "위원" not in t and "이사" not in t:
            continue  # 순수 감사 → 별도 한도
        if p["total_paid_krw"] is not None:
            director_paid_total += p["total_paid_krw"]
            has_director_paid = True

    utilization_pct = None
    if director_limit_krw and has_director_paid:
        utilization_pct = round(director_paid_total / director_limit_krw * 100, 1)

    return {
        "director_pay_limit_krw": director_limit_krw,
        "director_pay_limit_headcount": director_limit_headcount,
        "audit_pay_limit_krw": audit_limit_krw,
        "director_paid_total_krw": director_paid_total if has_director_paid else None,
        "utilization_pct": utilization_pct,
        "by_type": pay_by_type,
    }


async def _compensation_scope(
    client, corp_code: str, year: int, *, lookback_years: int, warnings: list[str]
) -> dict[str, Any]:
    """연도별 인당보수·한도·소진율. 한도 공백 해는 최근 유효값 lookback(주의 2)."""
    years = [year - i for i in range(lookback_years)]
    per_year: list[dict[str, Any]] = []
    last_valid_limit: dict[str, Any] | None = None

    for y in years:
        limit_rows = await _fetch_rows(client.get_director_pay_limit(corp_code, str(y), _REPRT_ANNUAL))
        await asyncio.sleep(0.4)
        actual_rows = await _fetch_rows(client.get_director_pay_actual(corp_code, str(y), _REPRT_ANNUAL))
        await asyncio.sleep(0.4)

        comp = _compensation_from_rows(limit_rows, actual_rows)
        # 한도 공백 → 직전 유효 한도로 소진율 재계산(주의 2)
        if comp["director_pay_limit_krw"] is None and last_valid_limit:
            comp["director_pay_limit_krw"] = last_valid_limit["director_pay_limit_krw"]
            comp["director_pay_limit_headcount"] = last_valid_limit["director_pay_limit_headcount"]
            comp["limit_source"] = f"{last_valid_limit['year']}년 승인분 유지(당해 미갱신)"
            if comp["director_paid_total_krw"] and comp["director_pay_limit_krw"]:
                comp["utilization_pct"] = round(
                    comp["director_paid_total_krw"] / comp["director_pay_limit_krw"] * 100, 1
                )
        elif comp["director_pay_limit_krw"] is not None:
            last_valid_limit = {
                "year": y,
                "director_pay_limit_krw": comp["director_pay_limit_krw"],
                "director_pay_limit_headcount": comp["director_pay_limit_headcount"],
            }

        comp["year"] = y
        if comp.get("utilization_pct") is not None and comp["utilization_pct"] >= _UTILIZATION_HIGH:
            comp["utilization_flag"] = "high"
        per_year.append(comp)

    if not any(y.get("director_paid_total_krw") for y in per_year):
        warnings.append("이사 보수 정형 데이터가 조회 구간에 없음(사업보고서 미제출/비대상 가능).")

    return {"per_year": per_year}


# ── roster: 임원현황 + 재직/사퇴 감지 ───────────────────────────────────────

def _clean_name(value: Any) -> str:
    """DART 이름 필드의 개행·중복공백 정규화(원문이 '호세\\n무뇨스'처럼 개행 포함)."""
    return " ".join(str(value or "").split())


def _roster_key(row: dict[str, Any]) -> str:
    """동명이인 최소화를 위해 이름+생년월 조합을 식별키로."""
    return f"{_clean_name(row.get('nm'))}|{(row.get('birth_ym') or '').strip()}"


# 등기 이사회 멤버 구분값(rgist_exctv_at은 '등기여부'가 아니라 이사 '구분'을 담음 — QA 260708)
_BOARD_TYPES = {"사내이사", "사외이사", "기타비상무이사", "감사"}


async def _roster_scope(
    client, corp_code: str, year: int, *, lookback_years: int, warnings: list[str]
) -> dict[str, Any]:
    """임원현황 스냅샷 + 연도간 diff로 신규선임/이탈 감지(사유는 스냅샷이라 미확정 — 주의)."""
    years = [year - i for i in range(lookback_years)]
    snapshots: dict[int, dict[str, dict[str, Any]]] = {}

    for y in years:
        rows = await _fetch_rows(client.get_executive_status(corp_code, str(y), _REPRT_ANNUAL))
        await asyncio.sleep(0.4)
        snapshots[y] = {_roster_key(r): r for r in rows}

    latest = year
    current = snapshots.get(latest, {})
    roster = [{
        "name": _clean_name(r.get("nm")),
        "position": (r.get("ofcps") or "").strip(),
        "director_type": (r.get("rgist_exctv_at") or "").strip(),  # 사내이사/사외이사/기타비상무/감사
        "full_time": (r.get("fte_at") or "").strip(),              # 상근/비상근
        "duty": (r.get("chrg_job") or "").strip(),
        "tenure": (r.get("hffc_pd") or "").strip(),
        "tenure_end": (r.get("tenure_end_on") or "").strip(),
    } for r in current.values()]

    # 연도간 diff (최신 vs 직전연도). 동일인 판정은 **OR 매칭**(이름 일치 OR 생년월 일치):
    #   - 로마자 표기 변동(이름 다름·생년월 같음) → 생년월으로 매칭  (José: Jose Munoz↔호세무뇨스)
    #   - 원문 birth_ym 오타(이름 같음·생년월 다름) → 이름으로 매칭  (기아 신재용 1972.12↔1972.02, QA 발견)
    # 복합키(둘 다 일치)로는 한쪽만 어긋나도 별인으로 오탐 → OR로 억제.
    changes: list[dict[str, Any]] = []
    prev_year = latest - 1
    if prev_year in snapshots and snapshots[prev_year]:
        prev_rows = list(snapshots[prev_year].values())
        curr_rows = list(current.values())
        prev_names = {_clean_name(r.get("nm")) for r in prev_rows}
        prev_births = {(r.get("birth_ym") or "").strip() for r in prev_rows if (r.get("birth_ym") or "").strip()}
        curr_names = {_clean_name(r.get("nm")) for r in curr_rows}
        curr_births = {(r.get("birth_ym") or "").strip() for r in curr_rows if (r.get("birth_ym") or "").strip()}

        def _present(row, names, births) -> bool:
            b = (row.get("birth_ym") or "").strip()
            return _clean_name(row.get("nm")) in names or (bool(b) and b in births)

        for r in curr_rows:
            if not _present(r, prev_names, prev_births):
                changes.append({"name": _clean_name(r.get("nm")), "change": "신규선임/등재",
                                "position": (r.get("ofcps") or "").strip(), "since_year": latest})
        for r in prev_rows:
            if not _present(r, curr_names, curr_births):
                changes.append({"name": _clean_name(r.get("nm")),
                                "change": "이탈(사퇴·임기만료·해임 중 하나)",
                                "position": (r.get("ofcps") or "").strip(), "until_year": prev_year})
    else:
        warnings.append(f"{prev_year}년 임원현황이 없어 재직/사퇴 diff 미산출.")

    board_count = sum(1 for r in roster if r["director_type"] in _BOARD_TYPES)
    return {
        "roster": roster,
        "headcount_total": len(roster),
        "headcount_board": board_count,     # 등기 이사회 구성원(사내+사외+기타비상무+감사)
        "changes_vs_prev_year": changes,
    }


# ── individual: 개인별 5억+ 실명 보수 ───────────────────────────────────────

async def _individual_scope(
    client, corp_code: str, year: int, *, warnings: list[str]
) -> dict[str, Any]:
    """개인별 5억 이상 실명 보수(법정공개). 5억 미만은 비공개라 상위 일부만 — 정직하게 명시."""
    rows = await _fetch_rows(client.get_individual_pay(corp_code, str(year), _REPRT_ANNUAL))
    people = [{
        "name": _clean_name(r.get("nm")),
        "position": (r.get("ofcps") or "").strip(),
        "total_pay_krw": _to_int(r.get("mendng_totamt")),
        "breakdown_note": (r.get("mendng_totamt_ct_incls_mendng") or "").strip(),
    } for r in rows]
    people.sort(key=lambda p: p["total_pay_krw"] or 0, reverse=True)
    if not people:
        warnings.append("개인별 5억+ 보수 공개 대상이 없음(전원 5억 미만이거나 미공시).")
    return {
        "year": year,
        "disclosed_count": len(people),
        "note": "5억원 이상만 법정 개별공개 — 그 미만은 비공개(범주 평균은 compensation scope).",
        "people": people,
    }


# ── unregistered: 미등기 집행임원 보수 ──────────────────────────────────────

async def _unregistered_scope(
    client, corp_code: str, year: int, *, warnings: list[str]
) -> dict[str, Any]:
    """미등기 집행임원 인당보수. 주총 승인한도 밖(등기 안 됨)이라 등기이사와 별개 지표."""
    rows = await _fetch_rows(client.get_unregistered_pay(corp_code, str(year), _REPRT_ANNUAL))
    buckets = [{
        "type": (r.get("se") or "").strip(),
        "headcount": _to_int(r.get("nmpr")),
        "annual_total_krw": _to_int(r.get("fyer_salary_totamt")),
        "per_capita_krw": _to_int(r.get("jan_salary_am")),
    } for r in rows]
    if not buckets:
        warnings.append("미등기임원 보수 데이터 없음(미등기임원 미보유 또는 미공시).")
    return {"year": year, "buckets": buckets}


# ── pay_gap: 경영진 vs 직원 보수 배수 ───────────────────────────────────────

# 합계/집계 행 표시어(부분매칭). 부문별 상세가 공백이고 이 행에만 총액이 오는 양식(삼성 등) 대응.
_EMP_TOTAL_MARKERS = ("합계", "소계", "총계", "전체")


def _emp_row_usable(r: dict[str, Any]) -> tuple[int, int] | None:
    """(연급여총액, 인원) — 둘 다 유효할 때만. 인원=정규+계약, 없으면 sm(합계)."""
    pay = _to_int(r.get("fyer_salary_totamt"))
    head = (_to_int(r.get("rgllbr_co")) or 0) + (_to_int(r.get("cnttk_co")) or 0)
    if not head:
        head = _to_int(r.get("sm")) or 0
    return (pay, head) if (pay and head) else None


def _employee_avg_krw(emp_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """직원 전체 가중평균 급여 = Σ연급여총액 ÷ Σ인원.

    양식이 두 갈래(QA 260708 발견): ① 대부분은 부문·성별 상세행에 급여가 있음(현대차 남/여) →
    상세행만 합산(합계행 있으면 중복이므로 제외). ② 삼성류는 부문 상세행 급여가 공백("-")이고
    '성별합계' 행에만 실제 총액이 옴 → 이땐 상세행이 못 쓰이므로 합계행으로 폴백.
    ⇒ 상세행에 유효 데이터가 있으면 상세만, 없으면 합계행. **둘을 섞지 않음**(중복/누락 방지).
    """
    def _is_total(r: dict[str, Any]) -> bool:
        label = f"{r.get('se') or ''}{r.get('fo_bbm') or ''}"
        return any(m in label for m in _EMP_TOTAL_MARKERS)

    detail = [r for r in emp_rows if not _is_total(r)]
    totals = [r for r in emp_rows if _is_total(r)]

    use = [uv for r in detail if (uv := _emp_row_usable(r))]
    if not use:  # 상세행이 전부 공백 → 합계행 폴백(삼성류)
        use = [uv for r in totals if (uv := _emp_row_usable(r))]

    total_pay = sum(p for p, _ in use)
    total_head = sum(h for _, h in use)
    avg = round(total_pay / total_head) if total_head else None
    return {"employee_avg_pay_krw": avg, "employee_headcount": total_head or None}


async def _pay_gap_scope(
    client, corp_code: str, year: int, *, comp_data: dict[str, Any] | None, warnings: list[str]
) -> dict[str, Any]:
    """등기이사 인당보수 ÷ 직원 평균급여 배수. comp_data(이미 조회했으면 재사용)로 이사 인당 확보."""
    emp_rows = await _fetch_rows(client.get_employee_status(corp_code, str(year), _REPRT_ANNUAL))
    emp = _employee_avg_krw(emp_rows)

    # 등기이사(사외·감사위 제외) 인당보수 — comp_data 재사용 or 즉석 조회
    director_pc = None
    if comp_data:
        for y in comp_data.get("per_year", []):
            if y.get("year") == year:
                for b in y.get("by_type", []):
                    if "등기이사" in (b.get("type") or "") and "제외" in (b.get("type") or ""):
                        director_pc = b.get("per_capita_krw")
    if director_pc is None:
        actual_rows = await _fetch_rows(client.get_director_pay_actual(corp_code, str(year), _REPRT_ANNUAL))
        for r in actual_rows:
            if "등기이사" in (r.get("se") or "") and "제외" in (r.get("se") or ""):
                director_pc = _to_int(r.get("psn1_avrg_pymntamt"))

    avg = emp.get("employee_avg_pay_krw")
    gap = round(director_pc / avg, 1) if director_pc and avg else None
    if gap is None:
        warnings.append("보수 격차 배수 산출 불가(이사 인당보수 또는 직원 평균급여 결측).")
    return {
        "year": year,
        "director_per_capita_krw": director_pc,
        "employee_avg_pay_krw": avg,
        "employee_headcount": emp.get("employee_headcount"),
        "gap_multiple": gap,
        "note": "등기이사(사외·감사위 제외) 인당보수 ÷ 직원 전체 가중평균 급여. 배수 자체가 "
                "과다/적정 판단은 아님(업종·직군 구성 차이 있음) — 비교 신호로만.",
    }


# ── pay_agenda: 주총 보수한도 안건(올해 제안) vs 작년 실적 ────────────────────

async def _pay_agenda_scope(company_query: str, *, warnings: list[str]) -> dict[str, Any]:
    """주총 소집공고의 '이사 보수한도 승인' 안건을 재사용해 올해 제안 한도 vs 작년 한도·실지급 비교.

    소집공고 하나에 current(올해 제안)·prior(작년 한도+실지급)가 모두 들어있어(shareholder_meeting
    notice가 이미 파싱), 별도 계산 없이 '작년 소진율'과 '올해 인상률'을 뽑아 보수한도 안건 의결권
    판단 신호를 만든다. 가치판단(찬반)은 하지 않고 인상률·작년소진율 사실만.
    """
    from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload

    payload = await build_shareholder_meeting_payload(company_query, scope="compensation")
    for w in (payload.get("warnings") or []):
        warnings.append(f"[notice] {w}")
    items = ((payload.get("data") or {}).get("compensation") or {}).get("items") or []
    director_item = next(
        (it for it in items if "이사" in (it.get("target") or "") and "감사" not in (it.get("target") or "")),
        items[0] if items else None,
    )
    if not director_item:
        return {"status": "no_agenda",
                "note": "최근 주총 소집공고에 이사 보수한도 안건이 없거나 파싱 실패."}

    cur = director_item.get("current") or {}
    pri = director_item.get("prior") or {}
    proposed = cur.get("limitAmount")
    prior_limit = pri.get("limitAmount")
    prior_actual = pri.get("actualPaidAmount")

    limit_change_pct = (round((proposed - prior_limit) / prior_limit * 100, 1)
                        if proposed and prior_limit else None)
    prior_util = (round(prior_actual / prior_limit * 100, 1)
                  if prior_actual and prior_limit else None)

    signal = None
    if prior_util is not None and limit_change_pct is not None:
        if prior_util >= 90 and limit_change_pct > 5:
            signal = f"작년 소진율 {prior_util}%로 한도 거의 소진 + 올해 {limit_change_pct:+.1f}% 인상 요구 — 인상 근거 있음."
        elif prior_util < 60 and limit_change_pct > 5:
            signal = f"작년 소진율 {prior_util}%로 여유 있는데 올해 {limit_change_pct:+.1f}% 인상 요구 — 인상 근거 약함(검토)."
        elif limit_change_pct <= 0:
            signal = f"한도 동결/인하({limit_change_pct:+.1f}%)."

    return {
        "agenda_no": director_item.get("number"),
        "agenda_title": director_item.get("title"),
        "proposed_limit_krw": proposed,
        "prior_limit_krw": prior_limit,
        "prior_actual_krw": prior_actual,
        "limit_change_pct": limit_change_pct,
        "prior_utilization_pct": prior_util,
        "signal": signal,
        "note": "주총 소집공고 보수한도 안건의 current(올해 제안)/prior(작년 한도·실지급) 컬럼 재사용. "
                "인상률·작년소진율은 사실 — 찬반 판단은 proxy_advise_before_meeting 참조.",
    }


# ── 진입점 ─────────────────────────────────────────────────────────────────

async def build_director_board_payload(
    company_query: str, *, scope: str = "summary", year: int = 0, lookback_years: int = 3,
    format: str = "md",
) -> dict[str, Any]:
    client = get_dart_client()
    calls_start = client.api_call_snapshot()

    if scope not in _SUPPORTED_SCOPES:
        return ToolEnvelope(
            tool="director_board", status=AnalysisStatus.ERROR, subject=company_query,
            warnings=[f"지원하지 않는 scope='{scope}'. 사용 가능: {sorted(_SUPPORTED_SCOPES)}"],
            data={"query": company_query, "scope": scope},
        ).to_dict()

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="director_board", status=resolution.status, subject=company_query,
            warnings=[f"'{company_query}' 상장사를 찾지 못함"],
            data={"query": company_query, "candidates": resolution.candidates},
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="director_board", status=AnalysisStatus.AMBIGUOUS, subject=company_query,
            data={"query": company_query, "candidates": resolution.candidates},
        ).to_dict()

    selected = resolution.selected
    corp_code = selected["corp_code"]
    canonical_name = selected.get("corp_name", company_query)
    if not year:
        # 최근 확정 사업연도(사업보고서는 익년 3월 제출 → 보수적으로 전년)
        from datetime import date
        year = date.today().year - 1

    warnings: list[str] = []
    data: dict[str, Any] = {
        "query": company_query, "canonical_name": canonical_name,
        "corp_code": corp_code, "scope": scope, "year": year,
        "lookback_years": lookback_years,
    }

    try:
        if scope in {"compensation", "summary"}:
            data["compensation"] = await _compensation_scope(
                client, corp_code, year, lookback_years=lookback_years, warnings=warnings)
        if scope in {"roster", "summary"}:
            data["roster"] = await _roster_scope(
                client, corp_code, year, lookback_years=lookback_years, warnings=warnings)
        if scope in {"individual", "summary"}:
            data["individual"] = await _individual_scope(client, corp_code, year, warnings=warnings)
        if scope in {"unregistered", "summary"}:
            data["unregistered"] = await _unregistered_scope(client, corp_code, year, warnings=warnings)
        if scope in {"pay_gap", "summary"}:
            data["pay_gap"] = await _pay_gap_scope(
                client, corp_code, year, comp_data=data.get("compensation"), warnings=warnings)
        if scope in {"pay_agenda", "summary"}:
            data["pay_agenda"] = await _pay_agenda_scope(company_query, warnings=warnings)
        if scope in {"attendance", "summary"}:
            # v1: 원문 파서 미구현 — 정직하게 stub. (금융지주는 PDF 별도양식)
            data["attendance"] = {"status": "not_implemented",
                                  "note": "개별 이사 출석률·선임변동·겸직은 지배구조보고서 원문 파서 필요 "
                                          "(v2 예정). 금융지주는 PDF 별도양식이라 OCR tier 필요."}
            if scope == "attendance":
                warnings.append("attendance scope는 v2에서 원문 파서와 함께 제공 예정(현재 stub).")
    except DartClientError as e:
        return ToolEnvelope(
            tool="director_board", status=AnalysisStatus.ERROR, subject=canonical_name,
            warnings=[f"DART 조회 실패: {e}"], data=data,
        ).to_dict()

    if scope == "summary":
        data["assessment"] = _summary_assessment(data)

    data["usage"] = build_usage(client.api_call_snapshot() - calls_start)
    return ToolEnvelope(
        tool="director_board", status=AnalysisStatus.EXACT, subject=canonical_name,
        warnings=warnings, data=data,
    ).to_dict()


def _summary_assessment(data: dict[str, Any]) -> dict[str, Any]:
    """목표 질문 3개에 대한 신호(판단 아님 — 기계적 사실 + flag)."""
    comp_years = (data.get("compensation") or {}).get("per_year") or []
    latest = comp_years[0] if comp_years else {}
    prev = comp_years[1] if len(comp_years) > 1 else {}

    # 인당보수 변동 (등기이사(사외·감사위 제외) 유형 기준)
    def _reg_director_per_capita(comp: dict[str, Any]) -> int | None:
        for b in comp.get("by_type") or []:
            t = b.get("type") or ""
            if "등기이사" in t and "제외" in t:
                return b.get("per_capita_krw")
        return None

    pc_now = _reg_director_per_capita(latest)
    pc_prev = _reg_director_per_capita(prev)
    per_capita_change = None
    if pc_now and pc_prev:
        per_capita_change = {
            "prev_krw": pc_prev, "now_krw": pc_now,
            "delta_pct": round((pc_now - pc_prev) / pc_prev * 100, 1),
        }

    roster = data.get("roster") or {}
    changes = roster.get("changes_vs_prev_year") or []
    departures = [c for c in changes if "이탈" in (c.get("change") or "")]

    return {
        "latest_utilization_pct": latest.get("utilization_pct"),
        "latest_per_capita_krw": pc_now,
        "per_capita_change_yoy": per_capita_change,
        "departures_detected": departures,
        "note": "소진율·인당보수는 기계적 사실. '적절성'은 동종업계·규모 대비 판단이 필요해 "
                "이 tool은 수치와 변동·flag만 제공(가치판단 안 함).",
    }
