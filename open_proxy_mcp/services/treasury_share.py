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
from open_proxy_mcp.services.filing_search import (
    fetch_filings_for_title_scan,
    report_name_matches,
    search_filings_by_report_name,
)


_SUPPORTED_SCOPES = {"summary", "annual"}
# 폐기 scope: events / acquisition / disposal / cancelation — summary가 모든 type breakdown 포함
_CANCELATION_KEYWORDS = ("자기주식소각결정", "자사주소각결정", "자기주식소각", "주식소각결정")

# 결과 보고서 4종 keyword (별도 구조화 API 없음 — list.json + 본문 파싱)
_ACQUISITION_RESULT_KEYWORDS = ("자기주식취득결과보고서", "자기주식취득결과")
_DISPOSAL_RESULT_KEYWORDS = ("자기주식처분결과보고서", "자기주식처분결과")
_TRUST_ACQ_STATUS_KEYWORDS = ("신탁계약에의한취득상황보고서", "신탁계약에 의한 취득상황보고서", "신탁취득상황보고서")
_TRUST_TERM_RESULT_KEYWORDS = ("신탁계약해지결과보고서", "신탁계약 해지 결과보고서", "신탁해지결과보고서")
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
    """자기주식 취득결정 (tsstkAqDecsn) — DART 구조화 API 필드 풍부 추출.

    추출:
    - 보통주/우선주 별도 수량·금액 + 합계
    - 취득기간 / 보유예상기간 / 이사회결의일
    - 취득방법 / 취득목적 / 위탁투자중개업자
    - 사외이사 참석 (거버넌스 신호)
    - for_cancelation flag — 취득목적에 "소각" 명시 시 (별도 소각결정 공시 없이 소각 의도)
    """

    shares_common = _to_int(item.get("aqpln_stk_ostk"))
    shares_pref = _to_int(item.get("aqpln_stk_estk"))
    amount_common = _to_int(item.get("aqpln_prc_ostk"))
    amount_pref = _to_int(item.get("aqpln_prc_estk"))
    purpose = (item.get("aq_pp") or "").strip()
    for_cancelation = "소각" in purpose
    return {
        "event": "acquisition_decision",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": _rcept_dt_from_no(item.get("rcept_no", "")),
        "corp_name": item.get("corp_name", ""),
        "report_nm": "자기주식 취득 결정",
        # 수량 (보통/우선/합계)
        "shares_common": shares_common,
        "shares_preferred": shares_pref,
        "shares": shares_common + shares_pref,
        # 금액 (보통/우선/합계)
        "amount_common_krw": amount_common,
        "amount_preferred_krw": amount_pref,
        "amount_krw": amount_common + amount_pref,
        # 결정 본질
        "purpose": purpose,
        "method": (item.get("aq_mth") or "").strip(),
        "start_date": (item.get("aqexpd_bgd") or "").strip(),
        "end_date": (item.get("aqexpd_edd") or "").strip(),
        "holding_start_date": (item.get("hdexpd_bgd") or "").strip(),
        "holding_end_date": (item.get("hdexpd_edd") or "").strip(),
        "board_date": (item.get("aq_dd") or "").strip(),
        # 위탁기관 (실제 DART field name)
        "broker_name": (item.get("cs_iv_bk") or "").strip(),
        # 취득 전 자기주식 보유현황 (배당가능 + 기타)
        "before_div_shares_common": _to_int(item.get("aq_wtn_div_ostk")),
        "before_div_pct_common": item.get("aq_wtn_div_ostk_rt"),
        "before_div_shares_preferred": _to_int(item.get("aq_wtn_div_estk")),
        "before_other_shares_common": _to_int(item.get("eaq_ostk")),
        "before_other_pct_common": item.get("eaq_ostk_rt"),
        # 거버넌스 신호
        "outside_director_attended": _to_int(item.get("od_a_at_t")),
        "outside_director_absent": _to_int(item.get("od_a_at_b")),
        "auditor_attended": (item.get("adt_a_atn") or "").strip(),
        # 소각 의도
        "for_cancelation": for_cancelation,
    }


def _normalize_disposal(item: dict[str, Any]) -> dict[str, Any]:
    """자기주식 처분결정 (tsstkDpDecsn) — DART 구조화 API 풍부 추출.

    추출:
    - 보통주/우선주 별도 수량·금액 + 합계
    - 처분 대상 주식가격 (시가, 처분결정 핵심)
    - 처분기간 / 이사회결의일 / 처분방법
    - 처분상대방 (직원 N명, 회사명, 3자배정 등)
    - 위탁기관 / 사외이사 참석
    """

    shares_common = _to_int(item.get("dppln_stk_ostk"))
    shares_pref = _to_int(item.get("dppln_stk_estk"))
    amount_common = _to_int(item.get("dppln_prc_ostk"))
    amount_pref = _to_int(item.get("dppln_prc_estk"))
    # 처분방법 4 field 중 양수만 (시장/장외/시간외/기타)
    method_parts = []
    for label, key in [("시장매도", "dp_m_mkt"), ("시간외대량매매", "dp_m_ovtm"),
                       ("장외처분", "dp_m_otc"), ("기타", "dp_m_etc")]:
        n = _to_int(item.get(key))
        if n:
            method_parts.append(f"{label}({n:,}주)")
    method_str = " + ".join(method_parts) if method_parts else ""
    return {
        "event": "disposal_decision",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": _rcept_dt_from_no(item.get("rcept_no", "")),
        "corp_name": item.get("corp_name", ""),
        "report_nm": "자기주식 처분 결정",
        # 수량
        "shares_common": shares_common,
        "shares_preferred": shares_pref,
        "shares": shares_common + shares_pref,
        # 금액
        "amount_common_krw": amount_common,
        "amount_preferred_krw": amount_pref,
        "amount_krw": amount_common + amount_pref,
        # 단가 (처분 대상 주식가격 — 시가 기준; 실제 DART field name)
        "price_common_krw": _to_int(item.get("dpstk_prc_ostk")),
        "price_preferred_krw": _to_int(item.get("dpstk_prc_estk")),
        # 결정 본질
        "purpose": (item.get("dp_pp") or "").strip(),
        "method": method_str,
        "start_date": (item.get("dpprpd_bgd") or "").strip(),
        "end_date": (item.get("dpprpd_edd") or "").strip(),
        "board_date": (item.get("dp_dd") or "").strip(),
        # 처분 전 자기주식 보유현황 (배당가능 + 기타)
        "before_div_shares_common": _to_int(item.get("aq_wtn_div_ostk")),
        "before_div_pct_common": item.get("aq_wtn_div_ostk_rt"),
        "before_div_shares_preferred": _to_int(item.get("aq_wtn_div_estk")),
        "before_other_shares_common": _to_int(item.get("eaq_ostk")),
        # 위탁/거버넌스 (실제 DART field name)
        "broker_name": (item.get("cs_iv_bk") or "").strip(),
        "outside_director_attended": _to_int(item.get("od_a_at_t")),
        "outside_director_absent": _to_int(item.get("od_a_at_b")),
        # 처분상대방 — DART API에 별도 field 없음. 본문 파싱 필요 (purpose에 자유 서술)
        # 예: "직원 대상 자기주식 지급" / "RSU 지급" — purpose field 활용 권장
    }


def _normalize_trust(item: dict[str, Any], event: str, label: str) -> dict[str, Any]:
    """자기주식 신탁체결/해지 (tsstkAqTrctrCnsDecsn / tsstkAqTrctrCcDecsn).

    추출:
    - 계약금액 (ctr_prc / ctr_prc_am / ctr_pr — 다양한 필드명 fallback)
    - 계약기간 (시작·종료) / 계약체결기관 (수탁기관)
    - 계약목적 / 위탁투자중개업자 (broker)
    - 보유예상기간 / 사외이사 참석
    - 해지 시: 해지일 / 해지사유
    """

    amount = 0
    for key in ("ctr_prc", "ctr_prc_am", "ctr_pr", "ctr_pric"):
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
        # 신탁기관 (수탁기관 — 보통 증권사) / 위탁사
        "trustee_name": (item.get("ctr_cns_inst") or item.get("ctr_inst") or "").strip(),
        "broker_name": (item.get("iv_jdgh_idr") or "").strip(),
        # 거버넌스
        "outside_director_attended": _to_int(item.get("od_a_at_t")),
        "outside_director_absent": _to_int(item.get("od_a_at_b")),
        # 해지 시 추가
        "termination_reason": (item.get("ctr_cc_rs") or "").strip() if event == "trust_termination" else "",
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


def _extract_acode(html: str, code: str) -> str | None:
    """DART 표준 서식 ACODE semantic marker 추출 — 99% 안정 anchor.

    ACODE는 자본시장법 시행령 별지 표준 서식의 system field id로 모든 회사 동일.
    예: <TE ACODE="ACQ_AMT" ...>7,174,299,854,900</TE>
    """
    if not html or not code:
        return None
    m = re.search(rf'<T[EDH]\s+[^>]*ACODE="{re.escape(code)}"[^>]*>([\s\S]*?)</T[EDH]>', html)
    if not m:
        return None
    val = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return val or None


def _acode_int(html: str, code: str) -> int | None:
    val = _extract_acode(html, code)
    if not val or val in ("-", "—"):
        return None
    return _to_int(val)


def _parse_main_report_date(text: str) -> str | None:
    """결과 보고서 본문에서 '주요사항보고서 제출일' (또는 '최초제출일') 추출.

    결정-결과 사이클 매칭 키. 라벨 변형 다수:
    - "주요사항보고서 제출일: 2026년 3월 18일"
    - "주요사항보고서 제출일 : 최초제출일: 2026년 3월 4일 정정신고일: ..." (한화오션 등)
    - "최초제출일: ..." (정정공시)
    """
    if not text:
        return None
    clean = re.sub(r"\s+", " ", text)
    # 패턴 1: 주요사항보고서 제출일 — "최초제출일:" 같은 noise 30자 cover
    m = re.search(r"주요사항보고서\s*제출일[\s:.]{0,40}?(\d{4})\s*[년\-./]\s*(\d{1,2})\s*[월\-./]\s*(\d{1,2})", clean)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # 패턴 2: 최초제출일 (단독)
    m = re.search(r"최초제출일[\s:.]{0,10}?(\d{4})\s*[년\-./]\s*(\d{1,2})\s*[월\-./]\s*(\d{1,2})", clean)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def _parse_acquisition_result_body(text: str, html: str = "") -> dict[str, Any]:
    """자기주식 취득결과보고서 본문 파싱 — DART ACODE 기반 안정 추출.

    핵심 ACODE:
      ACQ_AMT     — 취득가액 총액 (실제)
      SCH_SLT_MN  — 취득예정 금액 (계획)
      SEL_SLT_MN  — 취득가액 총액 (실제 — ACQ_AMT와 동일 cell 다른 위치)
      SUM_ACT_CNT — 누적 취득수량
      AGR_MN_YSN  — 일치여부 (일치/여 등)
      DIF_MN_CAS  — 미달 사유
      CNS_NM      — 위탁투자중개업자명
      HLD_CNT3 / HLD_AMT3 — 보유 자기주식 합계
    """
    if not text:
        return {}
    result: dict[str, Any] = {}

    main_date = _parse_main_report_date(text)
    if main_date:
        result["main_report_date"] = main_date

    if html:
        result["actual_amount_krw"] = _acode_int(html, "ACQ_AMT")
        result["planned_amount_krw"] = _acode_int(html, "SCH_SLT_MN")
        result["cumulative_shares"] = _acode_int(html, "SUM_ACT_CNT")
        result["holding_shares_total"] = _acode_int(html, "HLD_CNT3")
        result["holding_amount_total_krw"] = _acode_int(html, "HLD_AMT3")
        result["agreement_status"] = _extract_acode(html, "AGR_MN_YSN")
        result["shortfall_reason"] = _extract_acode(html, "DIF_MN_CAS")
        result["broker_name"] = _extract_acode(html, "CNS_NM")
        # 미달 = AGR_MN_YSN이 "일치"가 아니거나 actual < planned
        if result.get("planned_amount_krw") and result.get("actual_amount_krw"):
            result["shortfall"] = result["actual_amount_krw"] < result["planned_amount_krw"]

    # 취득기간 (text fallback — XML에 직접 ACODE 없을 수 있음)
    clean = re.sub(r"\s+", " ", text)
    m = re.search(r"취득기간[\s\S]{0,80}?(\d{4})[년\-./\s]+(\d{1,2})[월\-./\s]+(\d{1,2})[일\s]*부터[\s\S]{0,30}?(\d{4})[년\-./\s]+(\d{1,2})[월\-./\s]+(\d{1,2})", text)
    if m:
        result["period_start"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        result["period_end"] = f"{m.group(4)}-{int(m.group(5)):02d}-{int(m.group(6)):02d}"

    return {k: v for k, v in result.items() if v is not None}


def _parse_disposal_result_body(text: str, html: str = "") -> dict[str, Any]:
    """자기주식 처분결과보고서 — DART ACODE 기반.

    핵심 ACODE:
      DSP_AMT  — 처분가액 총액
      SCH_SLT  — 처분예정 주식수
      SEL_SLT  — 처분 주식수 (실제)
      OBJ_OTH  — 처분상대방 (직원/회사명 등)
      AGR_YSN  — 일치여부
      DIF_CAS  — 미달 사유
      HLD_CNT3 / HLD_AMT3 — 처분 후 보유 자기주식
    """
    if not text:
        return {}
    result: dict[str, Any] = {}

    main_date = _parse_main_report_date(text)
    if main_date:
        result["main_report_date"] = main_date

    if html:
        result["actual_amount_krw"] = _acode_int(html, "DSP_AMT")
        result["planned_shares"] = _acode_int(html, "SCH_SLT")
        result["actual_shares"] = _acode_int(html, "SEL_SLT")
        result["counterparty"] = _extract_acode(html, "OBJ_OTH")
        result["agreement_status"] = _extract_acode(html, "AGR_YSN")
        result["shortfall_reason"] = _extract_acode(html, "DIF_CAS")
        result["broker_name"] = _extract_acode(html, "CNS_NM")
        result["holding_shares_total"] = _acode_int(html, "HLD_CNT3")
        result["holding_amount_total_krw"] = _acode_int(html, "HLD_AMT3")

    m = re.search(r"처분기간[\s\S]{0,80}?(\d{4})[년\-./\s]+(\d{1,2})[월\-./\s]+(\d{1,2})[일\s]*부터[\s\S]{0,30}?(\d{4})[년\-./\s]+(\d{1,2})[월\-./\s]+(\d{1,2})", text)
    if m:
        result["period_start"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        result["period_end"] = f"{m.group(4)}-{int(m.group(5)):02d}-{int(m.group(6)):02d}"

    return {k: v for k, v in result.items() if v is not None}


def _parse_trust_acquisition_status_body(text: str, html: str = "") -> dict[str, Any]:
    """신탁계약에 의한 취득상황보고서 — DART ACODE 기반.

    핵심 ACODE:
      STK_VAL_TOT — 취득금액 (분기 누적)
      STK_VAL     — 1주당 평균단가
      ACQ_CNT     — 취득수량 (월간/누적)
      DSP_CNT     — 처분수량
      HLD_AMT2    — 신탁계약금액 (계)
      HLD_CNT2    — 신탁 보유 주식수
      HLD_RATE2   — 신탁 보유 비율
      CNS_CRP     — 신탁사 corp_code
    """
    if not text:
        return {}
    result: dict[str, Any] = {}

    if html:
        result["acquired_amount_krw"] = _acode_int(html, "STK_VAL_TOT")
        result["avg_price_krw"] = _acode_int(html, "STK_VAL")
        result["acquired_shares"] = _acode_int(html, "ACQ_CNT")
        result["disposed_shares"] = _acode_int(html, "DSP_CNT")
        result["trust_contract_amount_krw"] = _acode_int(html, "HLD_AMT2")
        result["trust_holding_shares"] = _acode_int(html, "HLD_CNT2")
        result["trust_holding_pct"] = _extract_acode(html, "HLD_RATE2")
        result["trustee_corp_code"] = _extract_acode(html, "CNS_CRP")

    # 신탁계약 체결일 — text fallback (라벨 변형 다수)
    clean = re.sub(r"\s+", " ", text)
    for label in (r"신탁계약\s*체결일", r"계약체결일자", r"신탁계약체결일", r"체결일자"):
        m = re.search(label + r"[\s\S]{0,200}?(\d{4})[년\.\-/\s]+(\d{1,2})[월\.\-/\s]+(\d{1,2})", clean)
        if m:
            result["trust_contract_date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            break

    return {k: v for k, v in result.items() if v is not None}


def _parse_trust_termination_result_body(text: str, html: str = "") -> dict[str, Any]:
    """신탁계약 해지결과보고서 — DART ACODE 기반.

    핵심 ACODE:
      ACQ_AMT     — 취득가액 총액 (사이클 합계)
      ACQ_CNT     — 취득 수량 (합계)
      ACQ_RT      — 취득률(%)
      CTR_CNC_AMT — 신탁계약금액 (계약상)
      MONTH_AMT   — 월별 합계
      SCH_SLT_MN  — 취득예정금액 (계획)
      SEL_SLT_MN  — 취득가액 총액 (실제)
      AGR_MN_YSN  — 일치여부
      DIF_MN_CAS  — 미달 사유 (예: "주가단차에 따른 발생")
      HLD_CNT3 / HLD_AMT3 — 해지 후 보유 합계
      CNCL_CRP    — 신탁사 corp_code
    """
    if not text:
        return {}
    result: dict[str, Any] = {}

    if html:
        result["actual_amount_krw"] = _acode_int(html, "ACQ_AMT")
        result["actual_shares"] = _acode_int(html, "ACQ_CNT")
        result["acquisition_rate_pct"] = _extract_acode(html, "ACQ_RT")
        result["contract_amount_krw"] = _acode_int(html, "CTR_CNC_AMT")
        result["planned_amount_krw"] = _acode_int(html, "SCH_SLT_MN")
        result["agreement_status"] = _extract_acode(html, "AGR_MN_YSN")
        result["shortfall_reason"] = _extract_acode(html, "DIF_MN_CAS")
        result["post_termination_shares"] = _acode_int(html, "HLD_CNT3")
        result["post_termination_amount_krw"] = _acode_int(html, "HLD_AMT3")
        result["trustee_corp_code"] = _extract_acode(html, "CNCL_CRP")

    clean = re.sub(r"\s+", " ", text)
    for label in (r"신탁계약\s*체결일", r"계약체결일자", r"신탁계약체결일", r"체결일자"):
        m = re.search(label + r"[\s\S]{0,200}?(\d{4})[년\.\-/\s]+(\d{1,2})[월\.\-/\s]+(\d{1,2})", clean)
        if m:
            result["trust_contract_date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            break
    m = re.search(r"해지일[:\s]*(\d{4})[년\-./\s]+(\d{1,2})[월\-./\s]+(\d{1,2})", clean)
    if m:
        result["termination_date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    return {k: v for k, v in result.items() if v is not None}


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


def _normalize_result_report(item: dict[str, Any], event_type: str) -> dict[str, Any]:
    """결과보고서 list.json item → 표준 row dict (body parse는 별도)."""
    return {
        "event": event_type,
        "phase": "execution",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": item.get("rcept_dt", ""),
        "report_name": item.get("report_nm", ""),
        "corp_code": item.get("corp_code", ""),
    }


_RESULT_PARSER_MAP = {
    "acquisition_result": _parse_acquisition_result_body,
    "disposal_result": _parse_disposal_result_body,
    "trust_acquisition_status": _parse_trust_acquisition_status_body,
    "trust_termination_result": _parse_trust_termination_result_body,
}


async def _enrich_result_reports_with_body(*row_lists: list[dict[str, Any]]) -> int:
    """결과보고서 4 type 본문 fetch + ACODE 기반 body parse + row enrich.

    Returns: 본문 파싱 실패 건수.
    """
    all_rows: list[dict[str, Any]] = []
    for lst in row_lists:
        all_rows.extend(lst)
    if not all_rows:
        return 0
    client = get_dart_client()

    async def fetch(rcept_no: str):
        if not rcept_no:
            return None
        try:
            return await client.get_document_cached(rcept_no)
        except Exception:
            return None

    docs = await asyncio.gather(*[fetch(r.get("rcept_no", "")) for r in all_rows])
    failures = 0
    for row, doc in zip(all_rows, docs):
        if not doc:
            failures += 1
            continue
        parser = _RESULT_PARSER_MAP.get(row.get("event"))
        if parser is None:
            failures += 1
            continue
        parsed = parser(doc.get("text", "") or "", html=doc.get("html", "") or "")
        if not parsed:
            failures += 1
            continue
        for k, v in parsed.items():
            if v not in (None, "", 0):
                row[k] = v
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
        items, _notices, error = await fetch_filings_for_title_scan(
            corp_code=corp_code,
            bgn_de=bgn_de,
            end_de=end_de,
            pblntf_tys=("B", "I"),
            keyword_label=", ".join(_CANCELATION_KEYWORDS),
        )
        if error:
            return [], f"자사주 소각결정 조회 실패: {error}"
        filtered = [
            item for item in items
            if report_name_matches(item, _CANCELATION_KEYWORDS, strip_spaces=True)
        ]
        return filtered, None

    async def execution_report_search():
        # 결과보고서는 list.json에서 pblntf_ty="" (빈 문자열, 분류 없음). 한 번 fetch 후 keyword별 재필터링한다.
        items, _notices, error = await fetch_filings_for_title_scan(
            corp_code=corp_code,
            bgn_de=bgn_de,
            end_de=end_de,
            pblntf_tys="",
            keyword_label="treasury execution reports",
        )
        if error:
            return None, error
        filtered = {
            "acquisition_result": [
                item for item in items
                if report_name_matches(item, _ACQUISITION_RESULT_KEYWORDS, strip_spaces=True)
            ],
            "disposal_result": [
                item for item in items
                if report_name_matches(item, _DISPOSAL_RESULT_KEYWORDS, strip_spaces=True)
            ],
            "trust_acquisition_status": [
                item for item in items
                if report_name_matches(item, _TRUST_ACQ_STATUS_KEYWORDS, strip_spaces=True)
            ],
            "trust_termination_result": [
                item for item in items
                if report_name_matches(item, _TRUST_TERM_RESULT_KEYWORDS, strip_spaces=True)
            ],
        }
        return filtered, None

    (acq, w1), (dsp, w2), (trc, w3), (trt, w4), (ret, w5), exec_reports = await asyncio.gather(
        acq_task, dsp_task, trc_task, trt_task, cancelation_search(), execution_report_search(),
    )
    exec_report_sets, exec_report_error = exec_reports
    warnings = [w for w in (w1, w2, w3, w4, w5) if w]
    if exec_report_sets is None:
        warnings.extend(
            [
                f"자기주식취득결과보고서 조회 실패: {exec_report_error}",
                f"자기주식처분결과보고서 조회 실패: {exec_report_error}",
                f"신탁취득상황보고서 조회 실패: {exec_report_error}",
                f"신탁해지결과보고서 조회 실패: {exec_report_error}",
            ]
        )
        exec_report_sets = {
            "acquisition_result": [],
            "disposal_result": [],
            "trust_acquisition_status": [],
            "trust_termination_result": [],
        }
    elif exec_report_error:
        warnings.append(exec_report_error)

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

    # 결과보고서 4종 — list.json 메타 → 본문 파싱 enrich
    acq_res_rows = [_normalize_result_report(i, "acquisition_result") for i in exec_report_sets["acquisition_result"]]
    dsp_res_rows = [_normalize_result_report(i, "disposal_result") for i in exec_report_sets["disposal_result"]]
    trust_acq_status_rows = [_normalize_result_report(i, "trust_acquisition_status") for i in exec_report_sets["trust_acquisition_status"]]
    trust_term_res_rows = [_normalize_result_report(i, "trust_termination_result") for i in exec_report_sets["trust_termination_result"]]

    fail_count = await _enrich_result_reports_with_body(
        acq_res_rows, dsp_res_rows, trust_acq_status_rows, trust_term_res_rows
    )
    if fail_count:
        warnings.append(f"결과보고서 본문 파싱 실패 {fail_count}건 — 합계가 0으로 보일 수 있다.")

    return {
        "acquisition": [_normalize_acquisition(i) for i in acq],
        "disposal": [_normalize_disposal(i) for i in dsp],
        "trust_contract": [_normalize_trust(i, "trust_contract", "자기주식 취득 신탁계약 체결 결정") for i in trc],
        "trust_termination": [_normalize_trust(i, "trust_termination", "자기주식 취득 신탁계약 해지 결정") for i in trt],
        "cancelation": cancelation_rows,
        "acquisition_result": acq_res_rows,
        "disposal_result": dsp_res_rows,
        "trust_acquisition_status": trust_acq_status_rows,
        "trust_termination_result": trust_term_res_rows,
    }, warnings


_DECISION_KEYS = ("acquisition", "disposal", "trust_contract", "trust_termination", "cancelation")
_EXECUTION_KEYS = ("acquisition_result", "disposal_result", "trust_acquisition_status", "trust_termination_result")

# 결정 ↔ 결과 사이클 매칭 — execution event type → 매칭 대상 decision type
_CYCLE_MAP: dict[str, str] = {
    "acquisition_result": "acquisition",
    "disposal_result": "disposal",
    "trust_acquisition_status": "trust_contract",
    "trust_termination_result": "trust_termination",
}


def _norm_date(s: str) -> str:
    """YYYYMMDD or YYYY-MM-DD → YYYY-MM-DD."""
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return s


def _date_within(a: str, b: str, days: int) -> bool:
    """YYYY-MM-DD 두 날짜 차이 ±days 이내인지."""
    if not a or not b:
        return False
    from datetime import date as _d
    try:
        ya, ma, da = a.split("-")
        yb, mb, db = b.split("-")
        d1 = _d(int(ya), int(ma), int(da))
        d2 = _d(int(yb), int(mb), int(db))
        return abs((d1 - d2).days) <= days
    except (ValueError, AttributeError):
        return False


def _link_cycles(bundles: dict[str, list[dict]]) -> int:
    """결과보고서 본문의 main_report_date / trust_contract_date를 결정 rcept_dt와 매칭.

    실행 row에 linked_decision_rcept_no, 결정 row에 linked_execution_rcept_no 양방향 set.
    매칭 fail 시 main_report_date는 hint로 노출 (key_date_hint).

    매칭 우선순위:
    1. 일자 정확 매칭
    2. ±3일 허용 (이사회 → 다음 영업일 공시 패턴)
    3. trust 사이클: 가장 최근 trust_contract fallback
    4. lookback 밖 → key_date_hint만 노출 (linked X)

    Returns: 매칭 성공 execution 건수 (G2 metric).
    """
    matched_count = 0
    for exec_key, dec_key in _CYCLE_MAP.items():
        exec_rows = bundles.get(exec_key, []) or []
        dec_rows = bundles.get(dec_key, []) or []

        # 결정 rcept_dt → row list
        dec_by_date: dict[str, list[dict]] = {}
        for dr in dec_rows:
            d = _norm_date(dr.get("rcept_dt", ""))
            if d:
                dec_by_date.setdefault(d, []).append(dr)
        all_decision_dates = sorted(dec_by_date.keys())

        for er in exec_rows:
            # 매칭 키 (acq/dsp는 main_report_date, trust는 trust_contract_date)
            key_date = ""
            if exec_key in ("acquisition_result", "disposal_result"):
                key_date = _norm_date(er.get("main_report_date", "") or "")
            else:
                key_date = _norm_date(er.get("trust_contract_date", "") or "")

            # key_date hint로 노출 (매칭 fail이라도)
            if key_date:
                er["key_date_hint"] = key_date
                er["matched_decision_type"] = dec_key

            matched_dec = None

            # 1. 정확 매칭
            if key_date and dec_by_date.get(key_date):
                matched_dec = dec_by_date[key_date][0]

            # 2. ±7일 허용 (이사회→공시 지연 + 영업일 buffer + 정정공시 보정)
            if matched_dec is None and key_date and dec_rows:
                # 가장 가까운 결정 우선 (sort by abs distance)
                from datetime import date as _d
                try:
                    ky, km, kd = key_date.split("-")
                    key_dt_obj = _d(int(ky), int(km), int(kd))
                except (ValueError, AttributeError):
                    key_dt_obj = None
                if key_dt_obj is not None:
                    candidates = []
                    for d in all_decision_dates:
                        if _date_within(key_date, d, 7):
                            try:
                                dy, dm, dd = d.split("-")
                                diff = abs((_d(int(dy), int(dm), int(dd)) - key_dt_obj).days)
                                candidates.append((diff, d))
                            except ValueError:
                                continue
                    if candidates:
                        candidates.sort()
                        chosen_d = candidates[0][1]
                        matched_dec = dec_by_date[chosen_d][0]
                        er["match_proximity_days"] = candidates[0][0]

            # 3a. acquisition/disposal result fallback — main_report_date 추출 fail 시
            # er_dt 이전 가장 최근 동일 type decision과 매칭 (단일 사이클 가정).
            if matched_dec is None and exec_key in ("acquisition_result", "disposal_result") and dec_rows:
                er_dt = er.get("rcept_dt", "")
                prior_decs = sorted(
                    [d for d in dec_rows if d.get("rcept_dt", "") <= er_dt],
                    key=lambda x: x.get("rcept_dt", ""),
                    reverse=True,
                )
                if prior_decs:
                    matched_dec = prior_decs[0]
                    er["match_via_acq_dsp_fallback"] = True

            # 3b. trust fallback — date 매칭 fail 시 같은 사이클 trust 결정과 매칭.
            # trust_termination_result는 trust_termination 우선, 없으면 trust_contract (사이클 시작) fallback.
            # trust_acquisition_status는 trust_contract 우선.
            if matched_dec is None and exec_key in ("trust_acquisition_status", "trust_termination_result"):
                er_dt = er.get("rcept_dt", "")
                # primary: 매칭 type 동일 (trust_contract 또는 trust_termination)
                primary_priors = sorted(
                    [d for d in dec_rows if d.get("rcept_dt", "") <= er_dt],
                    key=lambda x: x.get("rcept_dt", ""),
                    reverse=True,
                )
                if primary_priors:
                    matched_dec = primary_priors[0]
                    er["match_via_trust_fallback"] = True
                else:
                    # trust_termination_result — primary (trust_termination) 없으면 trust_contract fallback
                    if exec_key == "trust_termination_result":
                        contract_decs = bundles.get("trust_contract", []) or []
                        contract_priors = sorted(
                            [d for d in contract_decs if d.get("rcept_dt", "") <= er_dt],
                            key=lambda x: x.get("rcept_dt", ""),
                            reverse=True,
                        )
                        if contract_priors:
                            matched_dec = contract_priors[0]
                            er["match_via_trust_contract_fallback"] = True
                    # trust_acquisition_status — primary (trust_contract) 없으면 fail (사이클 자체 없음)

            if matched_dec is None:
                # trust 케이스: er_dt가 가장 오래된 trust_contract decision보다 이전이면 out_of_lookback
                # (이전 사이클 결정 record가 lookback 24개월 밖)
                if exec_key in ("trust_acquisition_status", "trust_termination_result"):
                    contract_decs = bundles.get("trust_contract", []) or []
                    if contract_decs:
                        earliest = min((d.get("rcept_dt", "") for d in contract_decs if d.get("rcept_dt")), default="")
                        er_dt = er.get("rcept_dt", "")
                        if earliest and er_dt and er_dt < earliest:
                            er["match_status"] = "out_of_lookback"
                            continue
                # 매칭 실패 사유 분류 — lookback 밖 (key_date 기준)
                if key_date and (not all_decision_dates or key_date < all_decision_dates[0]):
                    er["match_status"] = "out_of_lookback"
                else:
                    er["match_status"] = "no_match"
                continue

            er["linked_decision_rcept_no"] = matched_dec.get("rcept_no", "")
            er["match_status"] = "matched"
            matched_dec.setdefault("linked_execution_rcept_nos", []).append(er.get("rcept_no", ""))
            matched_count += 1

    return matched_count


def _combined_events(bundles: dict[str, list[dict]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in _DECISION_KEYS:
        for r in bundles.get(key, []):
            r.setdefault("phase", "decision")
            rows.append(r)
    for key in _EXECUTION_KEYS:
        # _normalize_result_report에서 phase=execution 이미 set
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


async def fetch_treasury_signal_summary(
    corp_code: str,
    *,
    bgn_de: str,
    end_de: str,
) -> tuple[dict[str, Any], list[str]]:
    """Lightweight 24m treasury signal summary for cross-tool references.

    Keeps the same summary semantics used by `value_up` without building
    execution-phase rows, cycle matching, or the full treasury payload.
    """

    client = get_dart_client()

    async def safe(coro, label: str) -> tuple[list[dict[str, Any]], str | None]:
        try:
            res = await coro
            return res.get("list", []) or [], None
        except DartClientError as exc:
            return [], f"{label} 조회 실패: {exc.status}"

    acq_task = safe(client.get_treasury_acquisition(corp_code, bgn_de, end_de), "취득결정")
    trc_task = safe(client.get_treasury_trust_contract(corp_code, bgn_de, end_de), "신탁계약 체결결정")

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

    (acq, w1), (trc, w2), (ret, w3) = await asyncio.gather(
        acq_task,
        trc_task,
        cancelation_search(),
    )
    warnings = [w for w in (w1, w2, w3) if w]

    cancelation_rows = [_normalize_cancelation_row(item) for item in ret]
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

    bundles = {
        "acquisition": [_normalize_acquisition(item) for item in acq],
        "disposal": [],
        "trust_contract": [_normalize_trust(item, "trust_contract", "자기주식 취득 신탁계약 체결 결정") for item in trc],
        "trust_termination": [],
        "cancelation": cancelation_rows,
    }
    return _summary_counts(bundles), warnings


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
    # 결정 ↔ 결과 사이클 매칭 — main_report_date / trust_contract_date 키
    cycle_matched = _link_cycles(bundles)
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
        "cycle_matched_count": cycle_matched,
        **filing_meta,
        "available_scopes": sorted(_SUPPORTED_SCOPES),
    }

    # 단일 통합 — events 전체 (decisions + executions, phase flag 포함) + type별 breakdown
    if scope == "summary":
        data["events"] = events  # 전체 timeline (phase=decision/execution 모두)
        data["type_breakdown"] = {
            "acquisition": bundles.get("acquisition", []),
            "disposal": bundles.get("disposal", []),
            "trust_contract": bundles.get("trust_contract", []),
            "trust_termination": bundles.get("trust_termination", []),
            "cancelation": bundles.get("cancelation", []),
            "acquisition_result": bundles.get("acquisition_result", []),
            "disposal_result": bundles.get("disposal_result", []),
            "trust_acquisition_status": bundles.get("trust_acquisition_status", []),
            "trust_termination_result": bundles.get("trust_termination_result", []),
        }
    if scope == "annual":
        # 연간 누적은 ownership_structure(scope="summary")에서 가져온다 (summary에 treasury snapshot 포함).
        # 이전 ownership scope="treasury" 폐지로 summary로 전환.
        from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload
        own_payload = await build_ownership_structure_payload(company_query, scope="summary", year=year)
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
