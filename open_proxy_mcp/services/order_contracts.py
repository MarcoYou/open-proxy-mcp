"""수주(단일판매·공급계약) 추적 서비스.

적자 디폴트인 코스닥 바이오/기술주에서 수주 = 미래 매출 가시성. 단일판매·공급계약(체결/해지)을
전담하는 단일 소스 — 외부 수주 규모·매출액 대비·최근 모멘텀, 그리고 **기재정정 dedup + diff**.
외부 수주와 계열 일감을 공시 '회사와의 관계' 필드로 구분(카운트). corporate_deals는 타법인주식
(지분 인수/매각) 전담.

설계 메모(2026-06-14): 체결↔해지 매핑·순수주·계열일감 규모 같은 추론은 넓은 샘플 측정 결과
부정확(매핑 26%, 관계 미기재 83%)해 제외했다. 공시에서 읽은 사실(체결·해지 파싱, 정정 dedup,
외부/계열 카운트)만 제공한다.

수주는 정정(변경계약)이 흔하다. 정정본 본문에 '정정전/정정후'가 같이 있어 증액/감액을
직접 추출한다. dedup은 multi-signal: (계약명+상대방) 그룹 + 정정본의 정정전 금액으로
원본↔정정 매칭 (같은 키라도 금액 체인이 안 맞으면 별개 계약 — 조선업 익명 상대방 오묶음 방지).

단위: 본문 라벨((원)/(천원)/(백만원))에서 단위를 읽어 원으로 환산 (가정 금지).
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import resolve_company_query
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    build_usage,
)
from open_proxy_mcp.services.corporate_deals import _extract_text
from open_proxy_mcp.services.filing_search import search_filings_by_report_name
from open_proxy_mcp.services.date_utils import format_yyyymmdd, resolve_date_window

_SUPPLY_KEYWORDS = (
    "단일판매ㆍ공급계약체결", "단일판매ㆍ공급계약해지",
    "단일판매·공급계약체결", "단일판매·공급계약해지",
)

# 계열/특수관계 상대방 — 외부 수주에서 제외 (일감몰아주기 vs 진짜 수주 구분)
# 계열/특수관계 — DART '회사와의 관계' 표준값. 최대주주·모회사(현대오토에버↔현대차 같은
# 명백한 일감몰아주기)가 빠져 외부로 오판하던 것 보강 (2026-06-14).
_INTERNAL_RELATION = ("자회사", "종속회사", "손자회사", "계열회사", "계열사", "관계회사",
                      "특수관계", "최대주주", "모회사", "지배회사", "대주주", "출자회사")

_UNIT_MULT = {"원": 1, "천원": 1_000, "백만원": 1_000_000, "천": 1_000, "백만": 1_000_000}


def _to_won(num_str: str, unit: str) -> int | None:
    if not num_str:
        return None
    try:
        n = int(num_str.replace(",", ""))
    except ValueError:
        return None
    return n * _UNIT_MULT.get(unit, 1)


def _amount_with_unit(flat: str, *label_patterns: str) -> tuple[int | None, str]:
    """라벨 뒤 금액 + 단위((원)/(천원)/(백만원)) 추출 → (원 환산값, 원본표기)."""
    for lab in label_patterns:
        # 라벨과 숫자 사이의 따옴표·하이픈 노이즈 허용 (정정 서술형 "'계약금액(원)' - 4,082…" LG엔솔)
        m = re.search(lab + r"\s*\((원|천원|백만원|천|백만)\)['’\"\s\-]*([\d,]{4,})", flat)
        if m:
            return _to_won(m.group(2), m.group(1)), f"{m.group(2)}({m.group(1)})"
        m2 = re.search(lab + r"['’\"\s\-]*([\d,]{4,})", flat)
        if m2:
            return _to_won(m2.group(1), "원"), m2.group(1)
    return None, ""


def _pct(flat: str, *label_patterns: str) -> float | None:
    # 천단위 콤마 허용 — 적자기업은 매출대비%가 천%를 넘어 '3,140.24'처럼 콤마가 들어간다
    # (콤마 미허용 시 '3'만 읽어 3.0%로 오파싱 — 프레스티지바이오로직스 등 적자 CDMO에서 발생).
    for lab in label_patterns:
        m = re.search(lab + r"\s*\(?%?\)?\s*([\d,]+(?:\.\d+)?)", flat)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


def _contract_name(flat: str) -> str:
    # '체결계약명'(일반) / '세부내용'(자율공시, HD현대 'VLGC 2척') / '판매ㆍ공급계약 내용'(바이오)
    # 길이 200자 — 영문 장문 계약명(대한전선 'TERM CONTRACT FOR THE SUPPLY, DELIVERY AND
    # INSTALLATION OF 400KV…' 140자)이 종료조건 전에 길이 제한에 걸려 누락되던 것 대응.
    # 해지 본문은 '판매ㆍ공급계약 해지내용 [계약명]' 라벨 — 체결('내용')과 달리 '해지' infix가 낀다.
    # '세부물건'은 건설 자율공시 계약명 라벨(대우건설 '대장~홍대 광역철도…').
    for lab in ("체결계약명", "해지계약명", "세부내용", "세부물건",
                "판매[ㆍ·]공급계약\\s*(?:해지\\s*)?내용", "공급계약\\s*(?:해지\\s*)?내용", "계약명"):
        m = re.search(lab + r"\s*[:\s]*([^\n]{3,200}?)(?:\s*\d\.\s|\s*조건부|\s*계약내역|\s*해지내역|\s*판매[ㆍ·]공급|\s*대규모|$)", flat)
        if m and m.group(1).strip() not in ("", "-"):
            return re.sub(r"\s+", " ", m.group(1).strip())
    return ""


def _counterparty(flat: str) -> str:
    # 상대방 항목 양식: '3. 계약상대 [회사명] - 회사와의 관계'. 해지 본문은 해지사유 문장에
    # '계약상대의 요청…'이 먼저 나오므로 (1) 항목번호 '3. 계약상대'를 우선 (2) 조사로 시작하는
    # 값은 건너뛴다. 영문사(Guangdong Landu Pharmaceutical Co., LTD. / EMS S/A / GreenPine
    # Pharma Group Co., Ltd. (구 …)) 위해 쉼표·마침표·&·/ 허용 + 길이 80(장문 영문사·구사명 병기).
    val_chars = r"[가-힣A-Za-z()㈜（）·,./&\s0-9]"
    for pat in (
        r"3\s*\.\s*계약상대방?\s*[:\s]*(" + val_chars + r"{2,80}?)(?:\s*-\s*회사와|\s*-\s*최근|\s*\d\s*\.)",
        r"계약상대방?\s*[:\s]*(" + val_chars + r"{2,80}?)(?:\s*-\s*회사와|\s*-\s*최근|\s*주요|\s*\d\s*\.)",
    ):
        for m in re.finditer(pat, flat):
            val = re.sub(r"\s+", " ", m.group(1).strip()).strip(" ,")
            if val and val[0] not in "의을를이가에와과으로은는도" and len(val) >= 2:
                return val
    return ""


def _relationship(flat: str) -> str:
    # 값은 다음 항목번호(예 '4. 판매ㆍ공급지역') 전까지 — flat은 개행이 없어 종료조건 없으면
    # 다음 필드까지 끌고 온다(현대오토에버 '최대주주 4. 판매…' 오염).
    m = re.search(r"(?:회사와의?\s*관계|최대주주[ㆍ·]?\s*임원과\s*상대방과의\s*관계)\s*[:\s]*([^\n]{1,40}?)(?:\s*\d\s*\.\s|$)", flat)
    return m.group(1).strip() if m else ""


def _is_external(relationship: str, counterparty: str) -> bool:
    blob = (relationship or "") + (counterparty or "")
    return not any(kw in blob for kw in _INTERNAL_RELATION)


def _is_self_filing(flr_nm: str, corp_name: str) -> bool:
    """공시 제출인이 회사 본인인지 (자회사 주요경영사항이면 제출인=모회사 등으로 다름).
    corporate_deals 공급계약 일원화로 흡수 — 일감몰아주기 관점 메타."""
    a, b = (flr_nm or "").strip(), (corp_name or "").strip()
    return bool(a and b and (a == b or b in a or a in b))


def _correction_diff(flat: str) -> dict[str, Any]:
    """정정본의 정정전/정정후 블록에서 금액·매출대비 변경 추출.

    표 양식이 두 가지 — 라벨이 값 사이('계약금액 [전] [후]', 레인보우)거나 값 앞에 몰림
    ('계약금액(원) 매출대비(%) [전금액][전매출][후금액][후매출]', 한화). 라벨 위치에 의존하지
    않고 블록 내 **큰 금액(천만원↑) 2개 = 전/후 계약금액, 소수 2개 = 전/후 매출대비**로 추출.
    """
    m = re.search(r"정정\s*전\s*정정\s*후(.{0,400})", flat)
    if not m:
        return {}
    blk = m.group(1).split("계약기간")[0].split("기타")[0]  # 날짜·기타 숫자 혼입 차단
    # '계약금액 X원에서 총 이행금액은 Y원' = 변경계약(정정)이 아니라 이행현황 안내 →
    # X(계약금액)와 Y(이행금액)를 정정전/후로 오인하지 않게 금액 diff 제외 (공시유보 해제 케이스)
    if re.search(r"이행금액|원에서\s*총", blk):
        return {}  # 정정(변경계약) 아님 — diff 없음
    out: dict[str, Any] = {}
    before = after = None
    # A) '계약금액(원) [전] [후]' 라벨 직접 (포스코 등) — 최근매출 혼입 없음
    am = re.search(r"계약금액\s*\(원\)\s*([\d,]{6,})\s+([\d,]{6,})", blk)
    if am:
        before, after = _to_won(am.group(1), "원"), _to_won(am.group(2), "원")
    else:
        # B) 라벨이 값 앞에 몰린 양식 (한화) — 계약금액 라벨 이후, 최근매출 라벨 이전 구간의 큰금액 2개
        seg = re.split(r"최근\s*매출", blk)[0]
        big = [a for a in re.findall(r"\d{1,3}(?:,\d{3}){2,}", seg) if len(a.replace(",", "")) >= 8]
        if len(big) >= 2:
            before, after = _to_won(big[0], "원"), _to_won(big[1], "원")
    rm = re.search(r"매출액\s*대비\s*\(%\)\s*([\d.]+)\s+([\d.]+)", blk)
    if rm:
        out["revenue_ratio_before_pct"] = float(rm.group(1))
        out["revenue_ratio_after_pct"] = float(rm.group(2))
    if before and after:
        out["amount_before_won"] = before
        out["amount_after_won"] = after
        out["amount_change_won"] = after - before
        out["amount_change_pct"] = round((after - before) / before * 100, 1)
    return out


def _parse_order(html: str) -> dict[str, Any]:
    flat = re.sub(r"\s+", " ", _extract_text(html))
    correction = _correction_diff(flat)  # diff는 정정전/후 테이블 전체에서
    # 정정본은 [정정전/후 테이블] + [정정후 반영 재공시 본문] 구조다. 현재 유효값(금액·매출대비)은
    # 테이블의 정정전 값을 먼저 잡으면 안 되고, 재공시 본문(정정후)에서 파싱한다.
    body = flat
    split = re.search(r"정정\s*전\s*정정\s*후.*?(단일판매[ㆍ·]공급계약\s*체결\s*1\.|판매[ㆍ·]공급계약\s*구분)", flat)
    if split:
        body = flat[split.start(1):]
    amount_won, amount_raw = _amount_with_unit(body, r"계약금액\s*총액", r"확정\s*계약금액", r"계약금액")
    revenue_won, _ = _amount_with_unit(body, r"최근\s*매출액")
    revenue_ratio = _pct(body, r"매출액\s*대비")
    name = _contract_name(body)
    cp = _counterparty(body)
    rel = _relationship(body)
    # 재공시 본문에서 금액 못 잡으면(드문 양식) 정정후 테이블값 fallback
    if not amount_won and correction.get("amount_after_won"):
        amount_won = correction["amount_after_won"]
    if revenue_ratio is None and correction.get("revenue_ratio_after_pct") is not None:
        revenue_ratio = correction["revenue_ratio_after_pct"]
    # 단위 정합 보정 — 계약금액·최근매출은 안정적이나 공시 매출대비%가 본문의 엉뚱한 숫자를
    # 잡는 엣지(스피어 1.0/앱클론 396 등, 450사 audit 0.5%)가 있다. 계산값(금액÷매출×100)과
    # 15%+ 괴리면 계산값을 채택하되 공시값을 병기 + warning (사용자가 원천 판단).
    revenue_ratio_disclosed = revenue_ratio
    ratio_warning = None
    if amount_won and revenue_won and revenue_won > 0:
        computed = round(amount_won / revenue_won * 100, 2)
        if revenue_ratio is None:
            revenue_ratio = computed
        elif abs(computed - revenue_ratio) > max(revenue_ratio * 0.15, 1.0):
            ratio_warning = f"공시 매출대비 {revenue_ratio}% ≠ 계산값 {computed}% (금액÷최근매출) — 계산값 채택"
            revenue_ratio = computed
    period_start = (re.search(r"계약\s*시작일\s*[:\s]*([\d.\-]{8,12})", flat) or [None, None])[1] if re.search(r"계약\s*시작일", flat) else None
    period_end_m = re.search(r"계약\s*종료일\s*[:\s]*([\d.\-]{8,12})", flat)
    return {
        "contract_name": name,
        "counterparty": cp,
        "relationship": rel,
        "is_external": _is_external(rel, cp),
        "contract_amount_won": amount_won,
        "revenue_ratio_disclosed_pct": revenue_ratio_disclosed,
        "ratio_warning": ratio_warning,
        "amount_raw": amount_raw,
        "recent_revenue_won": revenue_won,
        "revenue_ratio_pct": revenue_ratio,
        "period_end": period_end_m.group(1) if period_end_m else None,
        "correction_diff": correction or None,
    }


def _parse_termination(html: str) -> dict[str, Any]:
    """단일판매·공급계약 '해지' 본문 파싱 — 해지금액·매출대비%·상대방·사유.

    체결과 같은 구조에 라벨만 다르다(해지계약명/해지금액(원)/해지일자). 매출 대비 큰 해지는
    미래매출 가시성 급락 시그널 (삼성제약 매출 42.91% 수주 해지 등). 헬퍼는 체결과 공용 —
    단위 환산·콤마·노이즈 처리 그대로 적용.
    """
    flat = re.sub(r"\s+", " ", _extract_text(html))
    amount_won, amount_raw = _amount_with_unit(flat, r"해지금액", r"해지\s*계약금액")
    revenue_won, _ = _amount_with_unit(flat, r"최근\s*매출액")
    revenue_ratio = _pct(flat, r"매출액\s*대비")
    if amount_won and revenue_won and revenue_won > 0 and revenue_ratio is None:
        revenue_ratio = round(amount_won / revenue_won * 100, 2)
    reason_m = re.search(r"해지\s*사유\s*[:\s]*([^\n]{2,60}?)(?:\s*\d\.|$)", flat)
    date_m = re.search(r"해지\s*일자\s*[:\s]*([\d.\-]{8,12})", flat)
    cp = _counterparty(flat)
    rel = _relationship(flat)
    return {
        "contract_name": _contract_name(flat),
        "counterparty": cp,
        "relationship": rel,
        "is_external": _is_external(rel, cp),
        "terminated_amount_won": amount_won,
        "recent_revenue_won": revenue_won,
        "revenue_ratio_pct": revenue_ratio,
        "termination_reason": reason_m.group(1).strip() if reason_m else None,
        "termination_date": date_m.group(1) if date_m else None,
    }


def _norm(s: str) -> str:
    return re.sub(r"[\s()㈜（）·ㆍ,]+", "", s or "").lower()


def _dedup(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """multi-signal 정정 체인 dedup.

    (계약명+상대방) 그룹 안에서 정정본의 정정전 금액이 다른 건의 금액과 맞으면 같은 계약
    체인으로 병합(최신본 유효). 금액 체인이 안 맞으면 별개 계약 (조선업 익명 상대방 오묶음 방지).
    """
    groups: dict[tuple, list[dict]] = {}
    for ev in events:
        key = (_norm(ev["contract_name"])[:24], _norm(ev["counterparty"])[:16])
        groups.setdefault(key, []).append(ev)

    resolved: list[dict[str, Any]] = []
    for key, items in groups.items():
        items.sort(key=lambda e: (e["rcept_dt"], e["rcept_no"]))
        # 정정 체인: 정정본의 정정전금액으로 이전 건과 연결. 연결 안 되는(금액 다른) 원본은 별개.
        chains: list[list[dict]] = []
        for ev in items:
            cd = ev.get("correction_diff") or {}
            before = cd.get("amount_before_won")
            placed = False
            if ev["is_correction"] and before:
                for ch in chains:
                    last_amt = ch[-1].get("contract_amount_won")
                    if last_amt and abs(last_amt - before) <= max(1, last_amt * 0.001):
                        ch.append(ev)
                        placed = True
                        break
            if not placed:
                # 키 단서가 약하면(계약명 빈/익명) 같은 키여도 금액으로 합치지 않는다
                if ev["is_correction"] and chains and not before:
                    # 정정전금액 미파싱 — 가장 가까운 체인에 보수적으로 붙임(같은 키이므로)
                    chains[-1].append(ev)
                else:
                    chains.append([ev])
        for ch in chains:
            latest = dict(ch[-1])  # 최신본 = 유효값
            # 금액 미변경 정정(매출대비·유보사유만 정정해 정정본 계약금액이 '-')은 정정본 자체에
            # 금액이 없다 → 체인 이전본에서 누락 필드 상속 (LG엔솔 등).
            for fld in ("contract_amount_won", "recent_revenue_won", "revenue_ratio_pct", "contract_name", "counterparty"):
                if not latest.get(fld):
                    for e in reversed(ch[:-1]):
                        if e.get(fld):
                            latest[fld] = e[fld]
                            break
            corr_history = [
                {"rcept_dt": e["rcept_dt"], "rcept_no": e["rcept_no"], **(e.get("correction_diff") or {})}
                for e in ch if e["is_correction"]
            ]
            resolved.append({
                **latest,
                "first_rcept_dt": ch[0]["rcept_dt"],
                "filing_count": len(ch),
                "correction_count": sum(1 for e in ch if e["is_correction"]),
                "correction_history": corr_history or None,
            })
    resolved.sort(key=lambda e: e["rcept_dt"], reverse=True)
    return resolved


def _signal_summary(orders: list[dict[str, Any]], terminations: list[dict[str, Any]], window_label: str) -> dict[str, Any]:
    concluded = [o for o in orders if o.get("contract_amount_won")]
    external = [o for o in concluded if o.get("is_external")]
    internal = [o for o in concluded if not o.get("is_external")]  # 외부/계열 카운트 (공시 관계필드)
    ext_amt = sum(o["contract_amount_won"] for o in external)
    ratios = [o["revenue_ratio_pct"] for o in external if o.get("revenue_ratio_pct")]
    term_amts = [t["terminated_amount_won"] for t in terminations if t.get("terminated_amount_won")]
    term_total = sum(term_amts)
    term_ratios = [t["revenue_ratio_pct"] for t in terminations if t.get("revenue_ratio_pct")]
    # 파싱 사실만 집계 — 매핑·순수주·계열일감 규모 같은 추론은 제외(가능성 측정 결과, 2026-06-14).
    return {
        "order_count": len(concluded),
        "external_count": len(external),
        "internal_count": len(internal),
        "external_total_amount_won": ext_amt,
        "max_revenue_ratio_pct": max(ratios) if ratios else None,
        "sum_revenue_ratio_pct": round(sum(ratios), 1) if ratios else None,
        "correction_count": sum(o.get("correction_count", 0) for o in orders),
        # 해지(부정 시그널) — 매출 대비 큰 수주 해지는 미래매출 가시성 급락
        "terminated_count": len(terminations),
        "terminated_total_amount_won": term_total,
        "max_terminated_revenue_ratio_pct": max(term_ratios) if term_ratios else None,
        "window": window_label,
    }


async def build_order_contracts_payload(
    company_query: str,
    *,
    start_date: str = "",
    end_date: str = "",
    max_documents: int = 30,
) -> dict[str, Any]:
    client = get_dart_client()
    calls_start = client.api_call_snapshot()
    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="order_contracts", status=resolution.status,
            subject=company_query, warnings=["회사를 특정하지 못했다."],
            data={"query": company_query, "candidates": [_c for _c in (resolution.candidates or [])][:10]},
        ).to_dict()

    selected = resolution.selected
    begin, finish, win_warn = resolve_date_window(start_date=start_date, end_date=end_date, lookback_months=24)
    bgn, end = format_yyyymmdd(begin), format_yyyymmdd(finish)

    items, notices, error = await search_filings_by_report_name(
        corp_code=selected["corp_code"], bgn_de=bgn, end_de=end,
        pblntf_tys="", pblntf_detail_ty="I001",  # 일반+자율공시 모두 I001 (시장 실측)
        keywords=_SUPPLY_KEYWORDS,
    )
    warnings = list(win_warn)
    if error and error != "013":
        warnings.append(f"수주 공시 검색 실패: {error}")

    # 문서 본문 병렬 fetch — 각 공시는 독립적이라 gather (형제 tool corporate_deals·treasury_share
    # 동일 패턴). DART client가 롤링윈도우 rate limiter로 동시성 자체 스로틀. 파싱은 순서 유지 순차.
    async def _fetch(it: dict) -> tuple[dict, dict | None]:
        try:
            return it, await client.get_document_cached(it.get("rcept_no", ""))
        except DartClientError:
            return it, None

    fetched = await asyncio.gather(*[_fetch(it) for it in items[:max_documents]])

    events: list[dict[str, Any]] = []
    doc_calls = 0
    for it, doc in fetched:
        if doc is None:
            continue
        rcept_no = it.get("rcept_no", "")
        doc_calls += 1
        report_nm = (it.get("report_nm") or "").strip()
        parsed = _parse_termination(doc.get("html") or "") if "해지" in report_nm else _parse_order(doc.get("html") or "")
        compact_nm = report_nm.replace(" ", "")
        parsed.update({
            "rcept_no": rcept_no,
            "rcept_dt": it.get("rcept_dt", ""),
            "report_nm": report_nm,
            "filer_name": it.get("flr_nm", ""),
            "is_correction": "정정" in report_nm,
            "is_termination": "해지" in report_nm,
            "autonomous_disclosure": "자율공시" in compact_nm,
            # 일감몰아주기 관점 메타 (corporate_deals 공급계약 일원화로 흡수)
            "subsidiary_report": "자회사의주요경영사항" in compact_nm,
            "self_filing": _is_self_filing(it.get("flr_nm", ""), selected.get("corp_name", "")),
        })
        events.append(parsed)

    orders = _dedup([e for e in events if not e["is_termination"]])
    terminations = [e for e in events if e["is_termination"]]
    summary = _signal_summary(orders, terminations, f"{bgn}~{end}")
    # 매출대비% 보정 발생 건 — 회사 단위 warning으로 surface
    ratio_warned = [o for o in orders if o.get("ratio_warning")]
    if ratio_warned:
        warnings.append(
            f"매출대비% {len(ratio_warned)}건 보정 — 공시값이 계약금액÷최근매출 계산과 달라 계산값 채택"
            f" (예: {ratio_warned[0].get('contract_name', '')[:14]} {ratio_warned[0]['ratio_warning']})"
        )

    evidence_refs = [
        EvidenceRef(
            evidence_id=f"ev_order_{o['rcept_no']}", source_type=SourceType.DART_XML,
            rcept_no=o["rcept_no"], rcept_dt=o.get("rcept_dt", ""),
            report_nm=o.get("report_nm", ""), section="단일판매·공급계약",
            note=f"{o.get('contract_name','')} / {o.get('contract_amount_won')}원",
        )
        for o in orders[:10] if o.get("rcept_no")
    ]
    if items and not orders and not terminations:
        warnings.append("수주 공시는 있으나 본문 파싱에서 유효 계약을 만들지 못했다.")
    if terminations and not orders:
        warnings.append(f"체결 공시 없이 계약 해지 {len(terminations)}건만 있다 (과거 수주의 해지).")

    return ToolEnvelope(
        tool="order_contracts",
        # 해지(termination)만 있어도 '수주했다 해지'라는 정보다 — no_filing(수주 자체 없음)과 구분
        status=AnalysisStatus.EXACT if (orders or terminations) else AnalysisStatus.NO_FILING,
        subject=selected.get("corp_name", company_query),
        warnings=warnings + notices,
        data={
            "query": company_query,
            "company_id": f"cmp_{selected.get('stock_code') or selected['corp_code']}",
            "canonical_name": selected.get("corp_name"),
            "window": {"start_date": bgn, "end_date": end},
            "signal_summary": summary,
            "orders": orders,
            "terminations": terminations,
            "usage": build_usage(client.api_call_snapshot() - calls_start),
        },
        evidence_refs=evidence_refs,
    ).to_dict()
