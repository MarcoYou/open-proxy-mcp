"""v2 treasury_share facade 서비스.

자기주식 이벤트(취득·처분·소각·신탁) 전용 data tool.
주주환원 관점에서 소각 중심 신호를 애널리스트에게 제공한다.

데이터 소스:
  1. tsstkAqDecsn        — 자기주식 취득결정
  2. tsstkDpDecsn        — 자기주식 처분결정
  3. tsstkAqTrctrCnsDecsn — 자기주식 취득 신탁계약 체결
  4. tsstkAqTrctrCcDecsn  — 자기주식 취득 신탁계약 해지
  5. list.json + keyword  — 자기주식 소각결정 (별도 API 없음)
  6. tesstkAcqsDspsSttus  — 연간 사업보고서 기반 누적 잔고·소각 (기존 재사용)

소각결정은 별도 구조화 API가 없으므로 list.json 메타 + 본문 파싱
(`_parse_cancelation_body`)으로 소각 주식 수·금액(KRW)을 추출한다.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    build_filing_meta,
    build_usage,
    status_from_filing_meta,
)
from open_proxy_mcp.services.date_utils import (
    format_iso_date,
    format_yyyymmdd,
    resolve_date_window,
)
from open_proxy_mcp.services.filing_search import search_filings_by_report_name


_SUPPORTED_SCOPES = {"summary", "events", "acquisition", "disposal", "cancelation", "annual"}
_CANCELATION_KEYWORDS = ("자기주식소각결정", "자사주소각결정", "자기주식소각", "주식소각결정")
_ACQUISITION_KEYWORDS = ("자기주식취득결정", "자사주취득결정", "자기주식 취득 결정", "주식취득결정")


def _to_int(value: Any) -> int:
    try:
        return int(str(value).replace(",", "").strip() or 0)
    except Exception:
        return 0


def _rcept_dt_from_no(rcept_no: str) -> str:
    if len(rcept_no) >= 8 and rcept_no[:8].isdigit():
        return rcept_no[:8]
    return ""


def _normalize_acquisition(item: dict[str, Any]) -> dict[str, Any]:
    """자기주식 취득결정 (tsstkAqDecsn) — 보통주+우선주 수량·금액 합산.

    `aq_pp`(취득목적)에 "소각" 포함 시 `for_cancelation=True` — 소각결정 별도 공시 없이
    취득 단계에서 소각 의도를 밝히는 케이스(예: 미래에셋증권)를 잡아낸다.
    """

    shares = _to_int(item.get("aqpln_stk_ostk")) + _to_int(item.get("aqpln_stk_estk"))
    amount = _to_int(item.get("aqpln_prc_ostk")) + _to_int(item.get("aqpln_prc_estk"))
    purpose = (item.get("aq_pp") or "").strip()
    for_cancelation = "소각" in purpose
    return {
        "event": "acquisition_decision",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": _rcept_dt_from_no(item.get("rcept_no", "")),
        "corp_name": item.get("corp_name", ""),
        "report_nm": "자기주식 취득 결정",
        "shares": shares,
        "amount_krw": amount,
        "purpose": purpose,
        "method": (item.get("aq_mth") or "").strip(),
        "start_date": (item.get("aqexpd_bgd") or "").strip(),
        "end_date": (item.get("aqexpd_edd") or "").strip(),
        "board_date": (item.get("aq_dd") or "").strip(),
        "for_cancelation": for_cancelation,
    }


def _normalize_disposal(item: dict[str, Any]) -> dict[str, Any]:
    """자기주식 처분결정 (tsstkDpDecsn)."""

    shares = _to_int(item.get("dppln_stk_ostk")) + _to_int(item.get("dppln_stk_estk"))
    amount = _to_int(item.get("dppln_prc_ostk")) + _to_int(item.get("dppln_prc_estk"))
    return {
        "event": "disposal_decision",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": _rcept_dt_from_no(item.get("rcept_no", "")),
        "corp_name": item.get("corp_name", ""),
        "report_nm": "자기주식 처분 결정",
        "shares": shares,
        "amount_krw": amount,
        "purpose": (item.get("dp_pp") or "").strip(),
        "start_date": (item.get("dpprpd_bgd") or "").strip(),
        "end_date": (item.get("dpprpd_edd") or "").strip(),
        "board_date": (item.get("dp_dd") or "").strip(),
    }


def _normalize_trust(item: dict[str, Any], event: str, label: str) -> dict[str, Any]:
    """자기주식 신탁체결/해지 — DART 필드명 추정. 필드가 없어도 rcept_no 기준으로 노출."""

    amount = 0
    for key in ("ctr_prc", "ctr_prc_am", "ctr_pr"):
        amount = _to_int(item.get(key, 0))
        if amount:
            break
    return {
        "event": event,
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": _rcept_dt_from_no(item.get("rcept_no", "")),
        "corp_name": item.get("corp_name", ""),
        "report_nm": label,
        "shares": 0,
        "amount_krw": amount,
        "purpose": (item.get("ctr_pp") or "").strip(),
        "start_date": (item.get("ctr_cns_prd_bgd") or item.get("ctr_prd_bgd") or "").strip(),
        "end_date": (item.get("ctr_cns_prd_edd") or item.get("ctr_prd_edd") or "").strip(),
        "board_date": (item.get("ctr_cns_dd") or item.get("ctr_cc_dd") or "").strip(),
    }


def _parse_cancelation_body(text: str) -> dict[str, Any]:
    """자기주식 소각결정 공시 본문 파싱 — 소각 주식수·금액(KRW)·소각방법 추출.

    DART 거래소공시 표준 서식(주식소각결정):
      1. 소각할 주식
         - 종류
         - 수량(주)
         - 발행주식총수 대비 비율(%)
      2. 소각예정 금액(원)
      3. 소각방법 (자본금감소 / 이익잉여금 소각 / 기타)
      4. 소각 사유
      5. 소각 예정일
      6. 이사회결의일

    KT&G 같은 1조원+ 대형 소각 케이스도 동일 서식이라 정규식 한 벌로 처리 가능.
    HTML/XML 노이즈는 _normalize_text로 단일 공백화한 뒤 매칭한다.
    """

    if not text:
        return {}

    # CSS 잔재 제거 + 공백 정규화 (dividend 본문 파서와 동일 전략)
    clean = re.sub(r"\.xforms[^}]+\}", "", text)
    clean = re.sub(r"\s+", " ", clean).strip()

    result: dict[str, Any] = {}

    # 1. 소각할 주식 — 종류 / 수량(주) / 발행주식총수 대비 비율
    # (a) 단일 종류만 표시: "소각할 주식의 종류 보통주식"
    m = re.search(r"소각할\s*주식의?\s*종류\s*(보통주식|보통주|종류주식|우선주)", clean)
    if m:
        result["share_type"] = m.group(1)

    # (b) 표 형식 1: "소각할 주식 수 (주) 1,000,000" — 통합 수량
    qty = 0
    for pat in (
        r"소각할\s*주식\s*수\s*\(?\s*주\s*\)?\s*([\d,]+)",
        r"소각할\s*주식\s*수량\s*\(?\s*주\s*\)?\s*([\d,]+)",
        r"소각\s*주식수\s*\(?\s*주\s*\)?\s*([\d,]+)",
        r"소각\s*예정\s*주식\s*수\s*([\d,]+)",
    ):
        mm = re.search(pat, clean)
        if mm:
            qty = _to_int(mm.group(1))
            if qty:
                break

    # (c) 표 형식 2 — 삼성전자 등 대형사 패턴:
    # "소각할 주식의 종류와 수 보통주식 (주) 50,144,628 종류주식 (주) 6,912,036"
    # 보통주 + 종류주식 합산.
    if not qty:
        block_match = re.search(
            r"소각할\s*주식의?\s*종류\s*와?\s*수\s*[^.]{0,400}",
            clean,
        )
        block = block_match.group(0) if block_match else ""
        common = 0
        kind = 0
        m_common = re.search(r"보통주식\s*\(?\s*주\s*\)?\s*([\d,]+)", block)
        if m_common:
            common = _to_int(m_common.group(1))
        m_kind = re.search(r"종류주식\s*\(?\s*주\s*\)?\s*([\d,]+)", block)
        if m_kind:
            kind = _to_int(m_kind.group(1))
        if common or kind:
            qty = common + kind
            # share_type을 합쳐 표기.
            parts = []
            if common:
                parts.append("보통주")
            if kind:
                parts.append("종류주")
            if parts and not result.get("share_type"):
                result["share_type"] = "+".join(parts)
            result["shares_common"] = common
            result["shares_preferred"] = kind
    result["shares"] = qty

    m = re.search(r"발행주식\s*총수\s*대비\s*비율\s*\(?\s*%\s*\)?\s*([\d.]+)", clean)
    if m:
        try:
            result["pct_of_issued"] = float(m.group(1))
        except ValueError:
            result["pct_of_issued"] = None

    # 2. 소각예정 금액(원) — 핵심 필드. 표기 변형: 소각예정금액 / 소각금액 / 소각 예정 금액
    amount = 0
    for pat in (
        r"소각\s*예정\s*금액\s*\(?\s*원\s*\)?\s*([\d,]+)",
        r"소각\s*예정금액\s*\(?\s*원\s*\)?\s*([\d,]+)",
        r"소각\s*금액\s*\(?\s*원\s*\)?\s*([\d,]+)",
        # 표 변형: "2. 소각예정 금액 (원)" 다음 줄이 떨어진 케이스
        r"소각\s*예정\s*금액[^\d]{0,30}([\d,]{6,})",
    ):
        mm = re.search(pat, clean)
        if mm:
            amount = _to_int(mm.group(1))
            if amount:
                break
    result["amount_krw"] = amount

    # 3. 소각방법
    method = ""
    if "이익잉여금" in clean and re.search(r"이익잉여금\s*[^.]*해당", clean):
        method = "이익잉여금 소각"
    elif "자본금" in clean and re.search(r"자본금\s*감소\s*해당", clean):
        method = "자본금 감소"
    else:
        # 자유서술형 폴백 — "소각방법" 라벨 뒤 30자 슬라이스에서 키워드 우선순위 매칭
        m = re.search(r"소각\s*방법[^\d가-힣]{0,5}([^.\n]{0,80})", clean)
        if m:
            seg = m.group(1)
            if "이익잉여금" in seg:
                method = "이익잉여금 소각"
            elif "자본금" in seg or "자본의 감소" in seg:
                method = "자본금 감소"
            else:
                method = seg.strip()[:40]
    result["method"] = method

    # 4. 소각 사유 (자유기재)
    m = re.search(r"소각\s*사유\s*([^0-9.\n]{2,80})", clean)
    if m:
        result["purpose"] = m.group(1).strip()[:80]

    # 5. 소각 예정일
    m = re.search(r"소각\s*예정일\s*(\d{4}-?\d{2}-?\d{2})", clean)
    if m:
        result["scheduled_date"] = m.group(1)

    # 6. 이사회결의일
    m = re.search(r"이사회\s*결의일(?:\s*\(?결정일\)?)?\s*(\d{4}-?\d{2}-?\d{2})", clean)
    if m:
        result["board_date"] = m.group(1)

    return result


def _parse_acquisition_body(text: str) -> dict[str, Any]:
    """자기주식 취득결정 공시 본문 파싱 — 취득 주식수·금액(KRW) 보강 추출.

    DART 거래소공시 표준 서식(자기주식취득결정):
      1. 취득예정 주식
         - 종류 (보통주식 / 종류주식)
         - 수량(주)
         - 발행주식총수 대비 비율(%)
      2. 취득예정 금액(원)
      3. 취득예상 기간 (시작 ~ 종료)
      4. 보유예상 기간
      5. 취득 목적 (자유기재 — 주주가치 제고, 소각, 임직원 보상 등)
      6. 취득 방법 (장내매수 / 장외매수 / 공개매수 / 신탁)
      7. 위탁 투자중개업자
      8. 취득 결정일 / 사외이사 참석여부 / 감사 참석여부

    구조화 API(tsstkAqDecsn)가 보통주+종류주 별도 필드를 제공하지만 [기재정정] 공시
    경우 본문 파싱이 더 안정적이므로 본문 정규식 폴백을 둔다 (CSR 산출 안정성).
    """

    if not text:
        return {}

    clean = re.sub(r"\.xforms[^}]+\}", "", text)
    clean = re.sub(r"\s+", " ", clean).strip()

    result: dict[str, Any] = {}

    # 1. 취득예정 주식 — 통합 수량 패턴
    qty = 0
    for pat in (
        r"취득\s*예정\s*주식\s*수\s*\(?\s*주\s*\)?\s*([\d,]+)",
        r"취득\s*예정\s*주식\s*수량\s*\(?\s*주\s*\)?\s*([\d,]+)",
        r"취득\s*주식수\s*\(?\s*주\s*\)?\s*([\d,]+)",
    ):
        mm = re.search(pat, clean)
        if mm:
            qty = _to_int(mm.group(1))
            if qty:
                break

    # 보통주 + 종류주식 합산 (대형사 패턴)
    if not qty:
        block_match = re.search(
            r"취득\s*예정\s*주식의?\s*종류\s*와?\s*수\s*[^.]{0,400}",
            clean,
        )
        block = block_match.group(0) if block_match else ""
        common = 0
        kind = 0
        m_common = re.search(r"보통주식\s*\(?\s*주\s*\)?\s*([\d,]+)", block)
        if m_common:
            common = _to_int(m_common.group(1))
        m_kind = re.search(r"종류주식\s*\(?\s*주\s*\)?\s*([\d,]+)", block)
        if m_kind:
            kind = _to_int(m_kind.group(1))
        if common or kind:
            qty = common + kind
            result["shares_common"] = common
            result["shares_preferred"] = kind
    result["shares"] = qty

    # 2. 취득예정 금액(원) — 핵심 필드. 표기 변형 다수
    amount = 0
    for pat in (
        r"취득\s*예정\s*금액\s*\(?\s*원\s*\)?\s*([\d,]+)",
        r"취득\s*예정금액\s*\(?\s*원\s*\)?\s*([\d,]+)",
        r"취득\s*금액\s*\(?\s*원\s*\)?\s*([\d,]+)",
        r"취득\s*예정\s*금액[^\d]{0,30}([\d,]{6,})",
    ):
        mm = re.search(pat, clean)
        if mm:
            amount = _to_int(mm.group(1))
            if amount:
                break
    result["amount_krw"] = amount

    # 3. 발행주식총수 대비 비율
    m = re.search(r"발행주식\s*총수\s*대비\s*비율\s*\(?\s*%\s*\)?\s*([\d.]+)", clean)
    if m:
        try:
            result["pct_of_issued"] = float(m.group(1))
        except ValueError:
            result["pct_of_issued"] = None

    # 4. 취득 방법
    method = ""
    if "장내매수" in clean:
        method = "장내매수"
    elif "장외매수" in clean:
        method = "장외매수"
    elif "공개매수" in clean:
        method = "공개매수"
    elif "신탁" in clean and re.search(r"취득\s*방법[^.]{0,30}신탁", clean):
        method = "신탁"
    result["method"] = method

    # 5. 취득 목적 — 소각 commitment 식별
    m = re.search(r"취득\s*목적\s*([^.\n]{2,120})", clean)
    if m:
        purpose = m.group(1).strip()[:120]
        result["purpose"] = purpose
        result["for_cancelation"] = "소각" in purpose

    # 6. 이사회결의일
    m = re.search(r"이사회\s*결의일(?:\s*\(?결정일\)?)?\s*(\d{4}-?\d{2}-?\d{2})", clean)
    if m:
        result["board_date"] = m.group(1)

    return result


def _normalize_cancelation_row(item: dict[str, Any]) -> dict[str, Any]:
    """자기주식 소각결정 list.json 메타 + 본문 파싱 결과 결합.

    본문 파싱은 `_enrich_cancelation_with_body`에서 비동기로 수행.
    여기서는 메타만 우선 채워두고 amount_krw/shares는 0으로 초기화한다.
    """

    return {
        "event": "cancelation_decision",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": item.get("rcept_dt", ""),
        "report_nm": item.get("report_nm", ""),
        "corp_name": item.get("corp_name", ""),
        "filer_name": item.get("flr_nm", ""),
        # 본문 파싱 후 채워짐
        "shares": 0,
        "amount_krw": 0,
        "method": "",
        "share_type": "",
        "pct_of_issued": None,
        "scheduled_date": "",
        "board_date": "",
        "body_parsed": False,
    }


async def _enrich_cancelation_with_body(rows: list[dict[str, Any]]) -> int:
    """소각결정 행 본문을 병렬로 받아와 금액·수량을 채운다.

    Returns: 본문 파싱이 실패한 건수 (parsing_failures 카운트용).
    """

    if not rows:
        return 0
    client = get_dart_client()

    async def fetch(rcept_no: str):
        if not rcept_no:
            return None
        try:
            return await client.get_document_cached(rcept_no)
        except Exception:
            return None

    docs = await asyncio.gather(*[fetch(r.get("rcept_no", "")) for r in rows])
    failures = 0
    for row, doc in zip(rows, docs):
        if not doc:
            failures += 1
            continue
        parsed = _parse_cancelation_body(doc.get("text", "") or "")
        if not parsed:
            failures += 1
            continue
        # 핵심 필드만 병합 (메타는 list.json 우선)
        for key in ("shares", "amount_krw", "method", "share_type",
                    "pct_of_issued", "scheduled_date", "board_date", "purpose"):
            if key in parsed and parsed[key] not in (None, "", 0):
                row[key] = parsed[key]
        # 본문이 와도 amount/shares가 0이면 사실상 미파싱과 동일.
        if not row.get("amount_krw") and not row.get("shares"):
            failures += 1
        else:
            row["body_parsed"] = True
    return failures


async def _fetch_decisions(corp_code: str, bgn_de: str, end_de: str) -> tuple[dict[str, list[dict]], list[str]]:
    """취득·처분·신탁체결·신탁해지 4개 API 병렬 호출 + 소각결정 list.json 검색."""

    client = get_dart_client()

    async def safe(coro, label: str) -> tuple[list[dict[str, Any]], str | None]:
        try:
            res = await coro
            return res.get("list", []) or [], None
        except DartClientError as exc:
            return [], f"{label} 조회 실패: {exc.status}"

    acq_task = safe(client.get_treasury_acquisition(corp_code, bgn_de, end_de), "취득결정")
    dsp_task = safe(client.get_treasury_disposal(corp_code, bgn_de, end_de), "처분결정")
    trc_task = safe(client.get_treasury_trust_contract(corp_code, bgn_de, end_de), "신탁계약 체결결정")
    trt_task = safe(client.get_treasury_trust_termination(corp_code, bgn_de, end_de), "신탁계약 해지결정")

    async def cancelation_search():
        items, _notices, error = await search_filings_by_report_name(
            corp_code=corp_code,
            bgn_de=bgn_de,
            end_de=end_de,
            pblntf_tys=("B", "I"),
            keywords=_CANCELATION_KEYWORDS,
            strip_spaces=True,
        )
        if error:
            return [], f"자사주 소각결정 조회 실패: {error}"
        return items, None

    (acq, w1), (dsp, w2), (trc, w3), (trt, w4), (ret, w5) = await asyncio.gather(
        acq_task, dsp_task, trc_task, trt_task, cancelation_search()
    )
    warnings = [w for w in (w1, w2, w3, w4, w5) if w]

    cancelation_rows = [_normalize_cancelation_row(i) for i in ret]
    # 본문 파싱으로 소각 주식수·금액(KRW) 추출 — 자사주 소각 분석용. CSR 분자에는 사용하지 않음 (acquire 사용).
    cancelation_failures = await _enrich_cancelation_with_body(cancelation_rows)
    if cancelation_failures:
        warnings.append(
            f"자사주 소각결정 본문 파싱 실패 {cancelation_failures}건 — 소각 금액이 0으로 보일 수 있다."
        )
    raw_cnt = len(cancelation_rows)
    cancelation_rows = _dedupe_cancelation_rows(cancelation_rows)
    if len(cancelation_rows) < raw_cnt:
        warnings.append(
            f"[기재정정] 중복 {raw_cnt - len(cancelation_rows)}건을 제거해 소각 합산했다."
        )

    return {
        "acquisition": [_normalize_acquisition(i) for i in acq],
        "disposal": [_normalize_disposal(i) for i in dsp],
        "trust_contract": [_normalize_trust(i, "trust_contract", "자기주식 취득 신탁계약 체결 결정") for i in trc],
        "trust_termination": [_normalize_trust(i, "trust_termination", "자기주식 취득 신탁계약 해지 결정") for i in trt],
        "cancelation": cancelation_rows,
    }, warnings


def _combined_events(bundles: dict[str, list[dict]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("acquisition", "disposal", "trust_contract", "trust_termination", "cancelation"):
        rows.extend(bundles.get(key, []))
    rows.sort(key=lambda r: (r.get("rcept_dt", ""), r.get("rcept_no", "")), reverse=True)
    return rows


def _summary_counts(bundles: dict[str, list[dict]]) -> dict[str, Any]:
    acq = bundles.get("acquisition", [])
    # 취득목적에 "소각" 명시된 건. 별도 소각결정 공시 없는 기업(예: 미래에셋증권)에서 주주환원 신호로 쓰임.
    acq_for_cancelation = [r for r in acq if r.get("for_cancelation")]
    cancelations = bundles.get("cancelation", [])
    return {
        "acquisition_count": len(acq),
        "acquisition_for_cancelation_count": len(acq_for_cancelation),
        "disposal_count": len(bundles.get("disposal", [])),
        "trust_contract_count": len(bundles.get("trust_contract", [])),
        "trust_termination_count": len(bundles.get("trust_termination", [])),
        "cancelation_count": len(cancelations),
        "total_event_count": sum(len(bundles.get(k, [])) for k in ("acquisition", "disposal", "trust_contract", "trust_termination", "cancelation")),
        "acquisition_shares_total": sum(r.get("shares", 0) for r in acq),
        "acquisition_amount_total_krw": sum(r.get("amount_krw", 0) for r in acq),
        "acquisition_for_cancelation_shares_total": sum(r.get("shares", 0) for r in acq_for_cancelation),
        "acquisition_for_cancelation_amount_total_krw": sum(r.get("amount_krw", 0) for r in acq_for_cancelation),
        "disposal_shares_total": sum(r.get("shares", 0) for r in bundles.get("disposal", [])),
        "trust_contract_amount_total_krw": sum(r.get("amount_krw", 0) for r in bundles.get("trust_contract", [])),
        # 소각 금액·수량 — 자사주 정책 분석용 (CSR 분자에는 사용하지 않음, retire가 아닌 acquire 사용).
        "cancelation_shares_total": sum(r.get("shares", 0) for r in cancelations),
        "cancelation_amount_total_krw": sum(r.get("amount_krw", 0) for r in cancelations),
        "cancelation_body_parsed_count": sum(1 for r in cancelations if r.get("body_parsed")),
    }


def _dedupe_cancelation_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[기재정정] 공시는 원공시를 대체하므로 (board_date, amount, shares) 기준 중복 제거.

    동일 결정에 대한 원본 + 정정본 모두 list.json에 별도 entry로 잡히면 합산 시
    이중 계산이 발생한다. 결의일·금액·수량이 모두 같으면 동일 사건으로 보고
    가장 최신(rcept_dt 큰) 1건만 남긴다.

    board_date가 비어 있으면 (rcept_dt, amount, shares)로 대체.
    """

    if not rows:
        return rows

    # 최신순 정렬 후 dedupe.
    rows_sorted = sorted(rows, key=lambda r: (r.get("rcept_dt") or "", r.get("rcept_no") or ""), reverse=True)
    seen: set[tuple] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows_sorted:
        key = (
            row.get("board_date") or row.get("rcept_dt", ""),
            int(row.get("amount_krw") or 0),
            int(row.get("shares") or 0),
        )
        # board_date/금액/수량 모두 비면 dedupe 키가 무의미 — rcept_no fallback으로 항상 keep.
        if all(not v for v in key):
            deduped.append(row)
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    # 원래 정렬(최신순) 유지.
    return deduped


async def fetch_cancelation_summary(
    corp_code: str,
    *,
    year: int,
) -> dict[str, Any]:
    """단일 사업연도의 자사주 소각 합계 (수량·금액).

    자사주 소각 정책 단독 분석용. CSR(`scope=cash_shareholder_return`) 분자에는
    매입(acquire) 금액을 사용하므로 본 함수 결과는 사용하지 않는다.
    회계기준은 `rcept_dt` (이사회 결의 연도) 기준.
    [기재정정] 공시 중복은 board_date+amount+shares로 dedupe.

    Returns:
      {
        "year": int,
        "cancelation_count": int,
        "cancelation_shares_total": int,
        "cancelation_amount_total_krw": int,
        "rows": [...],     # 메타 + 본문 파싱 결합 (dedupe 후)
        "rows_raw_count": int,  # dedupe 전 원본 건수
        "warnings": [...]  # 본문 파싱 실패 등
      }
    """

    bgn_de = f"{year}0101"
    end_de = f"{year}1231"
    items, _notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys=("B", "I"),
        keywords=_CANCELATION_KEYWORDS,
        strip_spaces=True,
    )
    warnings: list[str] = []
    if error:
        warnings.append(f"자사주 소각결정 조회 실패: {error}")
        items = []

    rows = [_normalize_cancelation_row(it) for it in items]
    failures = await _enrich_cancelation_with_body(rows)
    if failures:
        warnings.append(
            f"{year}년 자사주 소각결정 본문 파싱 실패 {failures}건 — 금액이 누락되었을 수 있다."
        )
    raw_count = len(rows)
    rows = _dedupe_cancelation_rows(rows)
    if len(rows) < raw_count:
        warnings.append(
            f"[기재정정] 중복 {raw_count - len(rows)}건을 제거해 {len(rows)}건으로 합산했다."
        )

    return {
        "year": year,
        "cancelation_count": len(rows),
        "rows_raw_count": raw_count,
        "cancelation_shares_total": sum(r.get("shares", 0) for r in rows),
        "cancelation_amount_total_krw": sum(r.get("amount_krw", 0) for r in rows),
        "rows": rows,
        "warnings": warnings,
    }


def _dedupe_acquisition_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[기재정정] 자기주식 취득결정 dedupe — (board_date, amount, shares) 기준.

    cancelation과 동일 정책. 정정공시는 원공시를 대체하므로 같은 결의일·금액·수량
    조합은 가장 최신 1건만 남긴다.
    """

    if not rows:
        return rows

    rows_sorted = sorted(rows, key=lambda r: (r.get("rcept_dt") or "", r.get("rcept_no") or ""), reverse=True)
    seen: set[tuple] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows_sorted:
        key = (
            row.get("board_date") or row.get("rcept_dt", ""),
            int(row.get("amount_krw") or 0),
            int(row.get("shares") or 0),
        )
        if all(not v for v in key):
            deduped.append(row)
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


async def fetch_acquisition_summary(
    corp_code: str,
    *,
    year: int,
) -> dict[str, Any]:
    """단일 사업연도의 자사주 **취득(매입)** 합계 (수량·금액).

    `dividend_v2.scope_cash_shareholder_return`(CSR) 분자 합산용. 한국 시장
    정의의 주주환원에서는 회사가 실제로 시장에서 현금을 지출해 자사주를 매입한
    금액을 환원으로 본다 (소각은 매입 후 회계 정리 단계).

    데이터 소스:
      1. 구조화 API tsstkAqDecsn (1차) — `aqpln_prc_ostk` + `aqpln_prc_estk`
      2. 본문 파싱 (2차 폴백) — `_parse_acquisition_body` ([기재정정] 안정성)

    회계기준은 이사회 결의 연도 (rcept_dt) 기준. [기재정정] 공시는
    (board_date, amount, shares) tuple로 dedupe.

    Returns:
      {
        "year": int,
        "acquisition_count": int,
        "acquisition_shares_total": int,
        "acquisition_amount_total_krw": int,
        "rows": [...],
        "rows_raw_count": int,
        "warnings": [...],
      }
    """

    client = get_dart_client()
    bgn_de = f"{year}0101"
    end_de = f"{year}1231"
    warnings: list[str] = []

    # 1차: 구조화 API
    try:
        api_data = await client.get_treasury_acquisition(corp_code, bgn_de, end_de)
        api_items = api_data.get("list", []) or []
    except DartClientError as exc:
        warnings.append(f"자사주 취득결정 API 조회 실패: {exc.status}")
        api_items = []

    rows: list[dict[str, Any]] = []
    for item in api_items:
        normalized = _normalize_acquisition(item)
        # 회계 연도 필터: rcept_dt 연도가 target year와 일치해야 함.
        rcept_dt = normalized.get("rcept_dt") or ""
        if len(rcept_dt) >= 4 and rcept_dt[:4].isdigit():
            if int(rcept_dt[:4]) != year:
                continue
        rows.append(normalized)

    # 2차: API에 amount/shares가 0인 행은 본문 파싱으로 폴백
    needs_body: list[dict[str, Any]] = [
        r for r in rows if not r.get("amount_krw") or not r.get("shares")
    ]

    async def fetch(rcept_no: str):
        if not rcept_no:
            return None
        try:
            return await client.get_document_cached(rcept_no)
        except Exception:
            return None

    if needs_body:
        docs = await asyncio.gather(*[fetch(r.get("rcept_no", "")) for r in needs_body])
        body_failures = 0
        for row, doc in zip(needs_body, docs):
            if not doc:
                body_failures += 1
                continue
            parsed = _parse_acquisition_body(doc.get("text", "") or "")
            if not parsed:
                body_failures += 1
                continue
            for key in ("shares", "amount_krw", "method", "purpose",
                        "pct_of_issued", "board_date", "for_cancelation"):
                if key in parsed and parsed[key] not in (None, "", 0, False):
                    row[key] = parsed[key]
            row["body_parsed"] = True
        if body_failures:
            warnings.append(
                f"{year}년 자사주 취득결정 본문 파싱 실패 {body_failures}건 — 금액이 누락되었을 수 있다."
            )

    raw_count = len(rows)
    rows = _dedupe_acquisition_rows(rows)
    if len(rows) < raw_count:
        warnings.append(
            f"[기재정정] 자사주 취득결정 중복 {raw_count - len(rows)}건을 제거해 {len(rows)}건으로 합산했다."
        )

    return {
        "year": year,
        "acquisition_count": len(rows),
        "rows_raw_count": raw_count,
        "acquisition_shares_total": sum(r.get("shares", 0) for r in rows),
        "acquisition_amount_total_krw": sum(r.get("amount_krw", 0) for r in rows),
        "rows": rows,
        "warnings": warnings,
    }


async def build_treasury_share_payload(
    company_query: str,
    *,
    scope: str = "summary",
    year: int | None = None,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 24,
) -> dict[str, Any]:
    if scope not in _SUPPORTED_SCOPES:
        return ToolEnvelope(
            tool="treasury_share",
            status=AnalysisStatus.REQUIRES_REVIEW,
            subject=company_query,
            warnings=[f"`{scope}` scope는 아직 지원하지 않는다."],
            data={"query": company_query, "scope": scope},
        ).to_dict()

    client = get_dart_client()
    _calls_start = client.api_call_snapshot()
    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="treasury_share",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 상장사를 찾지 못했다."],
            data={
                "query": company_query,
                "scope": scope,
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
            },
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="treasury_share",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 자사주 공시를 자동 선택하지 않았다."],
            data={
                "query": company_query,
                "scope": scope,
                "candidates": [
                    {
                        "company_id": _company_id(corp),
                        "corp_name": corp.get("corp_name", ""),
                        "ticker": corp.get("stock_code", ""),
                        "corp_code": corp.get("corp_code", ""),
                    }
                    for corp in resolution.candidates[:10]
                ],
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
            },
        ).to_dict()

    selected = resolution.selected
    default_end = date(year, 12, 31) if year else date.today()
    window_start, window_end, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=default_end,
        lookback_months=lookback_months,
    )
    bgn_de = format_yyyymmdd(window_start)
    end_de = format_yyyymmdd(window_end)
    warnings: list[str] = list(window_warnings)

    bundles, fetch_warnings = await _fetch_decisions(selected["corp_code"], bgn_de, end_de)
    warnings.extend(fetch_warnings)

    counts = _summary_counts(bundles)
    events = _combined_events(bundles)

    # 사건 발견 vs 진짜 partial 분리.
    # 4개 결정 API + cancellation list.json 모두 결과 0건은 사건 없음 = 정상.
    filing_meta = build_filing_meta(
        filing_count=len(events),
        parsing_failures=0,
    )

    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
        },
        "window": {
            "start_date": bgn_de,
            "end_date": end_de,
            "lookback_months": lookback_months,
        },
        "summary": counts,
        **filing_meta,
        "available_scopes": sorted(_SUPPORTED_SCOPES),
    }

    if scope == "events":
        data["events"] = events
    if scope == "acquisition":
        data["events"] = bundles.get("acquisition", []) + bundles.get("trust_contract", [])
    if scope == "disposal":
        data["events"] = bundles.get("disposal", []) + bundles.get("trust_termination", [])
    if scope == "cancelation":
        data["events"] = bundles.get("cancelation", [])
    if scope == "summary":
        data["latest_events"] = events[:5]
    if scope == "annual":
        # 연간 누적은 ownership_structure(scope="treasury")에서 가져온다.
        from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload
        own_payload = await build_ownership_structure_payload(company_query, scope="treasury", year=year)
        data["annual"] = own_payload.get("data", {}).get("treasury", {})

    # evidence_refs — 최신 5건 이벤트의 공시
    evidence_refs: list[EvidenceRef] = []
    for ev in events[:5]:
        if not ev.get("rcept_no"):
            continue
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_treasury_{ev['event']}_{ev['rcept_no']}",
                source_type=SourceType.DART_API if ev["event"] != "cancelation_decision" else SourceType.DART_XML,
                rcept_no=ev["rcept_no"],
                rcept_dt=format_iso_date(ev.get("rcept_dt", "")),
                report_nm=ev.get("report_nm", ""),
                section=ev["event"],
                note=f"{ev.get('shares', 0):,}주" if ev.get("shares") else "",
            )
        )

    status = status_from_filing_meta(filing_meta)
    if filing_meta["no_filing"]:
        warnings.append(f"조사 구간 ({bgn_de}~{end_de}) 내 자사주 이벤트 공시 없음 (정상). 연간 누적은 `scope='annual'`로 확인할 수 있다.")

    data["usage"] = build_usage(client.api_call_snapshot() - _calls_start)

    return ToolEnvelope(
        tool="treasury_share",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=[
            "scope=`cancelation`으로 소각결정만 확인" if scope == "summary" else "value_up 교차 참조로 주주환원 정책 신호 함께 해석",
            "scope=`annual`로 사업보고서 기준 연간 잔고·소각 누적 확인",
        ],
    ).to_dict()
