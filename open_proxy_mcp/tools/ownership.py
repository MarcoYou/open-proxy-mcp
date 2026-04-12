"""지분 구조 관련 MCP tools (own_*)"""

import json
import re
import logging
from datetime import datetime

from open_proxy_mcp.dart.client import DartClient, DartClientError, get_dart_client
from open_proxy_mcp.tools.formatters import (
    _parse_holding_purpose, _parse_holding_purpose_from_document,
    _normalize_purpose, _pct, _format_number,
    _format_major_shareholders, _format_stock_total, _format_treasury_stock,
    _format_treasury_tx, _format_block_holders,
)
from open_proxy_mcp.tools.errors import tool_error, tool_not_found, tool_empty

logger = logging.getLogger(__name__)



# ── Tool 등록 ──

def register_tools(mcp):
    """ownership MCP tools 등록"""

    @mcp.tool()
    async def own_major(
        ticker: str,
        year: str = "",
        format: str = "md",
    ) -> str:
        """desc: 최대주주 + 특수관계인 지분 현황 (사업보고서 기준). 보통주 기준 지분율 + 변동이력.
        when: [tier-5 Detail] own_full_analysis 실행 후 사용자가 최대주주/특관인 상세를 요청했을 때만 사용.
        rule: 사업보고서 신고 기준. 실질 최다보유자와 다를 수 있음 (대량보유는 own_block 참조). 우선주 별도 표시.
        ref: corp_identifier, own_block, own_full_analysis

        Args:
            ticker: 종목코드 또는 회사명
            year: 사업연도 (미입력 시 전년도)
            format: "md" (기본) 또는 "json"
        """
        client = get_dart_client()
        corp = await client.lookup_corp_code(ticker)
        if not corp:
            return tool_not_found("기업", ticker)

        corp_code = corp["corp_code"]
        bsns_year = year or str(datetime.now().year - 1)

        try:
            major = await client.get_major_shareholders(corp_code, bsns_year)
        except DartClientError as e:
            return tool_error("최대주주 현황 조회", e, ticker=ticker)

        try:
            changes = await client.get_major_shareholder_changes(corp_code, bsns_year)
        except DartClientError:
            changes = None

        if format == "json":
            result = {"major": major, "changes": changes}
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_major_shareholders(major, changes)

    @mcp.tool()
    async def own_total(
        ticker: str,
        year: str = "",
        format: str = "md",
    ) -> str:
        """desc: 발행주식 총수, 자기주식, 유통주식, 소액주주 현황.
        when: [tier-5 Detail] own_full_analysis 실행 후 사용자가 발행주식/자사주/소액주주 상세를 요청했을 때만 사용.
        rule: 사업보고서 기준. 보통주/우선주 각각 표시.
        ref: own_treasury, own_full_analysis

        Args:
            ticker: 종목코드 또는 회사명
            year: 사업연도 (미입력 시 전년도)
            format: "md" (기본) 또는 "json"
        """
        client = get_dart_client()
        corp = await client.lookup_corp_code(ticker)
        if not corp:
            return tool_not_found("기업", ticker)

        corp_code = corp["corp_code"]
        bsns_year = year or str(datetime.now().year - 1)

        try:
            stock = await client.get_stock_total(corp_code, bsns_year)
        except DartClientError as e:
            return tool_error("주식 총수 조회", e, ticker=ticker)

        try:
            minority = await client.get_minority_shareholders(corp_code, bsns_year)
        except DartClientError:
            minority = None

        if format == "json":
            result = {"stock_total": stock, "minority": minority}
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_stock_total(stock, minority)

    @mcp.tool()
    async def own_treasury(
        ticker: str,
        year: str = "",
        format: str = "md",
    ) -> str:
        """desc: 자기주식 취득방법별 기초-취득-처분-소각-기말 잔액.
        when: [tier-5 Detail] own_full_analysis 실행 후 사용자가 자사주 취득방법별 상세를 요청했을 때만 사용.
        rule: 사업보고서 기준 연간 baseline. 최신 이벤트는 own_treasury_tx 참조.
        ref: own_treasury_tx, own_total, own_full_analysis

        Args:
            ticker: 종목코드 또는 회사명
            year: 사업연도 (미입력 시 전년도)
            format: "md" (기본) 또는 "json"
        """
        client = get_dart_client()
        corp = await client.lookup_corp_code(ticker)
        if not corp:
            return tool_not_found("기업", ticker)

        corp_code = corp["corp_code"]
        bsns_year = year or str(datetime.now().year - 1)

        try:
            data = await client.get_treasury_stock(corp_code, bsns_year)
        except DartClientError as e:
            return tool_error("자기주식 현황 조회", e, ticker=ticker)

        if format == "json":
            return json.dumps(data, ensure_ascii=False, indent=2)

        return _format_treasury_stock(data)

    @mcp.tool()
    async def own_treasury_tx(
        ticker: str,
        bgn_de: str = "",
        end_de: str = "",
        format: str = "md",
    ) -> str:
        """desc: 자사주 이벤트 - 취득결정, 처분결정, 신탁계약 체결, 신탁계약 해지.
        when: [tier-5 Detail] own_full_analysis 실행 후 사용자가 자사주 이벤트 이력을 요청했을 때만 사용.
        rule: 4개 DART API 호출 (취득+처분+신탁체결+해지). 경영권 방어/주주환원 시그널.
        ref: own_treasury, own_full_analysis, div_detail

        Args:
            ticker: 종목코드 또는 회사명
            bgn_de: 검색 시작일 YYYYMMDD (미입력 시 2년 전 1월 1일)
            end_de: 검색 종료일 YYYYMMDD (미입력 시 오늘)
            format: "md" (기본) 또는 "json"
        """
        client = get_dart_client()
        corp = await client.lookup_corp_code(ticker)
        if not corp:
            return tool_not_found("기업", ticker)

        corp_code = corp["corp_code"]
        if not bgn_de:
            bgn_de = f"{datetime.now().year - 2}0101"
        if not end_de:
            end_de = datetime.now().strftime("%Y%m%d")

        empty = {"list": []}

        try:
            acq = await client.get_treasury_acquisition(corp_code, bgn_de, end_de)
        except DartClientError:
            acq = empty

        try:
            disp = await client.get_treasury_disposal(corp_code, bgn_de, end_de)
        except DartClientError:
            disp = empty

        try:
            trust_in = await client.get_treasury_trust_contract(corp_code, bgn_de, end_de)
        except DartClientError:
            trust_in = empty

        try:
            trust_out = await client.get_treasury_trust_termination(corp_code, bgn_de, end_de)
        except DartClientError:
            trust_out = empty

        if format == "json":
            result = {
                "acquisition": acq, "disposal": disp,
                "trust_contract": trust_in, "trust_termination": trust_out,
            }
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_treasury_tx(acq, disp, trust_in, trust_out)

    @mcp.tool()
    async def own_block(
        ticker: str,
        format: str = "md",
    ) -> str:
        """desc: 5% 대량보유 상황보고. 보유목적(단순투자/일반투자/경영참여) + 목적변경 감지.
        when: [tier-5 Detail] own_full_analysis 실행 후 사용자가 5% 대량보유 상세를 요청했을 때만 사용. 프록시 파이트 감지 목적이면 prx_fight 사용.
        rule: 수시 공시 기반. 보유목적은 원문 파싱 (report_resn + document.xml). 보고자+특별관계자 합산.
        ref: corp_identifier, own_major, own_full_analysis, agm_result

        Args:
            ticker: 종목코드 또는 회사명
            format: "md" (기본) 또는 "json"
        """
        client = get_dart_client()
        corp = await client.lookup_corp_code(ticker)
        if not corp:
            return tool_not_found("기업", ticker)

        corp_code = corp["corp_code"]

        try:
            data = await client.get_block_holders(corp_code)
        except DartClientError as e:
            return tool_error("대량보유 조회", e, ticker=ticker)

        items = data.get("list", [])

        # 보고자별 최신 rcept_no 수집 → 원문에서 보유목적 파싱
        purposes: dict[str, str] = {}
        latest_by_reporter: dict[str, dict] = {}
        for item in items:
            name = item.get("repror", "")
            dt = item.get("rcept_dt", "")
            if name and (name not in latest_by_reporter or dt > latest_by_reporter[name].get("rcept_dt", "")):
                latest_by_reporter[name] = item

        # 원문 파싱 (보고자별 최신 1건씩)
        for name, item in latest_by_reporter.items():
            rcept_no = item.get("rcept_no", "")
            # 먼저 report_resn에서 시도
            purpose = _parse_holding_purpose(
                item.get("report_tp", ""), item.get("report_resn", "")
            )
            if purpose not in ("불명", "단순투자/일반투자"):
                purposes[rcept_no] = purpose
            else:
                # DART 원문에서 보유목적 파싱
                try:
                    doc = await client.get_document(rcept_no)
                    html = doc.get("html", "") or doc.get("full_text", "")
                    parsed = _parse_holding_purpose_from_document(html)
                    if parsed != "불명":
                        purposes[rcept_no] = parsed
                    else:
                        purposes[rcept_no] = purpose
                except Exception:
                    purposes[rcept_no] = purpose

        api_calls = 1 + len(latest_by_reporter)

        if format == "json":
            result = {"data": data, "purposes": purposes, "api_calls": api_calls}
            return json.dumps(result, ensure_ascii=False, indent=2)

        result_md = _format_block_holders(data, purposes)
        result_md += f"\n\n*API 호출: {api_calls}회 (majorstock 1 + 원문 {len(latest_by_reporter)})*"
        return result_md

    @mcp.tool()
    async def own_full_analysis(
        ticker: str,
        format: str = "md",
    ) -> str:
        """desc: 지분 구조 종합 분석 — 사업보고서 vs 최신 공시 지분율 비교 테이블.
        when: [tier-4 Orchestrate] 특정 기업 주주 구성을 사업보고서 기준과 최신 공시 기준으로 비교할 때.
        rule: own_major(사업보고서) + own_block(수시 공시) 데이터를 통합. 결과를 반드시 | 주주 | 구분 | 지분율 | 비고 | 형식의 4컬럼 markdown 테이블로 출력. 차트/시각화 사용 금지. 이 tool 하나로 지분 분석이 완성됨. own_major/own_block 개별 tool은 사용자의 명시적 요청 없는 한 추가 호출 금지.
        ref: corp_identifier, own_major, own_block, own_total

        Args:
            ticker: 종목코드 또는 회사명
            format: "md" (기본) 또는 "json"
        """
        client = get_dart_client()
        corp = await client.lookup_corp_code(ticker)
        if not corp:
            return tool_not_found("기업", ticker)

        corp_code = corp["corp_code"]
        corp_name = corp.get("corp_name", ticker)
        bsns_year = str(datetime.now().year - 1)

        empty = {"list": []}

        # 1. 사업보고서 기준 (최대주주 + 주식총수)
        try:
            major = await client.get_major_shareholders(corp_code, bsns_year)
        except DartClientError:
            major = empty
        try:
            stock_total = await client.get_stock_total(corp_code, bsns_year)
        except DartClientError:
            stock_total = empty
        try:
            minority = await client.get_minority_shareholders(corp_code, bsns_year)
        except DartClientError:
            minority = empty

        # 2. 최신 공시 (5% 대량보유)
        try:
            block = await client.get_block_holders(corp_code)
        except DartClientError:
            block = empty

        # 3. 사업보고서 기준 주주 테이블 구성
        major_items = major.get("list", [])
        stlm_dt = major_items[0].get("stlm_dt", "") if major_items else ""

        # 사업보고서 주주 (보통주만)
        ar_shareholders = {}  # name → {relate, pct}
        for item in major_items:
            if "보통" not in item.get("stock_knd", "보통"):
                continue
            name = item.get("nm", "").strip()
            if name == "계":
                continue
            relate = item.get("relate", "").strip()
            try:
                pct = float(item.get("trmend_posesn_stock_qota_rt", "0") or "0")
            except ValueError:
                pct = 0.0
            if pct > 0:
                ar_shareholders[name] = {"relate": relate, "pct": pct}

        # 최신 공시 주주 (5% 대량보유)
        block_items = block.get("list", [])
        latest_by_reporter = {}
        for item in block_items:
            name = item.get("repror", "")
            dt = item.get("rcept_dt", "")
            if name and (name not in latest_by_reporter or dt > latest_by_reporter[name].get("rcept_dt", "")):
                latest_by_reporter[name] = item

        # 보유목적 파싱
        purposes = {}
        for name, item in latest_by_reporter.items():
            rcept_no = item.get("rcept_no", "")
            purpose = _parse_holding_purpose(item.get("report_tp", ""), item.get("report_resn", ""))
            if purpose in ("불명", "단순투자/일반투자"):
                try:
                    doc = await client.get_document(rcept_no)
                    html = doc.get("html", "") or doc.get("full_text", "")
                    parsed = _parse_holding_purpose_from_document(html)
                    purposes[name] = parsed if parsed != "불명" else purpose
                except Exception:
                    purposes[name] = purpose
            else:
                purposes[name] = purpose

        # 4. 통합 테이블 구성
        rows = []  # [{name, category, ar_pct, latest_pct, latest_date, note}]

        # 사업보고서 주주 추가
        for name, info in ar_shareholders.items():
            relate = info["relate"]
            if "본인" in relate or "최대주주" in relate:
                category = "최대주주"
            elif "계열" in relate:
                category = "계열사"
            elif "특수" in relate or "관계" in relate:
                category = "특수관계인"
            elif "재단" in relate or "출연" in relate:
                category = "재단"
            else:
                category = relate or "특수관계인"

            # 최신 공시에도 있는지 확인
            latest_pct = None
            latest_date = None
            note = ""
            if name in latest_by_reporter:
                b_item = latest_by_reporter[name]
                try:
                    # stkrt = 보고자+특별관계자 합산 지분율 (보고서 표지 기준)
                    # ctr_stkrt = 주요계약체결 주식 비율 (담보/신탁) — 지분율 아님, 사용 안 함
                    total_pct = float(b_item.get("stkrt", 0) or 0)
                except ValueError:
                    total_pct = None
                latest_date = b_item.get("rcept_dt", "")
                # 날짜 포맷
                dt_fmt = f"{latest_date[:4]}.{latest_date[4:6]}.{latest_date[6:8]}" if latest_date and len(latest_date) >= 8 else ""
                # 비고: 대량보유 합산(보고자+특관) + 목적 + 날짜
                parts = []
                if total_pct and total_pct > 0.01:
                    parts.append(f"대량보유 {total_pct:.2f}% (보고자+특관)")
                purpose = purposes.get(name, "")
                if purpose:
                    parts.append(purpose)
                if dt_fmt:
                    parts.append(dt_fmt)
                note = ", ".join(parts)

            rows.append({
                "name": name,
                "category": category,
                "ar_pct": info["pct"],
                "latest_pct": latest_pct,
                "latest_date": latest_date,
                "note": note,
            })

        # 사업보고서에 없지만 5% 대량보유에만 있는 주주 (국민연금, 외국계 기관 등)
        for name, item in latest_by_reporter.items():
            if name not in ar_shareholders:
                try:
                    # stkrt = 보고자+특별관계자 합산. 개별 지분은 API 없음 (원문 파싱 필요)
                    total_pct = float(item.get("stkrt", 0) or 0)
                except ValueError:
                    total_pct = 0.0
                if total_pct > 0:
                    dt = item.get("rcept_dt", "")
                    dt_fmt = f"{dt[:4]}.{dt[4:6]}.{dt[6:8]}" if dt and len(dt) >= 8 else ""
                    parts = []
                    parts.append(f"대량보유 {total_pct:.2f}% (보고자+특관)")
                    purpose = purposes.get(name, "")
                    if purpose:
                        parts.append(purpose)
                    if dt_fmt:
                        parts.append(dt_fmt)
                    note = ", ".join(parts)
                    rows.append({
                        "name": name,
                        "category": "5% 대량보유",
                        "ar_pct": None,
                        "latest_pct": total_pct,
                        "latest_date": dt,
                        "note": note,
                    })

        # 지분율 내림차순 정렬
        rows.sort(key=lambda r: max(r.get("ar_pct") or 0, r.get("latest_pct") or 0), reverse=True)

        # 합계 계산
        ar_total = sum(r["ar_pct"] for r in rows if r.get("ar_pct"))

        # 발행주식 / 자사주 / 소액주주
        issued = 0
        treasury_cnt = 0
        for item in stock_total.get("list", []):
            if "보통" in item.get("se", ""):
                issued = int(re.sub(r'[^\d]', '', item.get("istc_totqy", "0")) or "0")
                treasury_cnt = int(re.sub(r'[^\d]', '', item.get("tesstk_co", "0")) or "0")
                break
        treasury_pct = (treasury_cnt / issued * 100) if issued else 0

        minority_items = minority.get("list", [])
        minority_info = ""
        if minority_items:
            m = minority_items[0]
            minority_info = f"{_format_number(m.get('shrholdr_co', ''))}명, {m.get('hold_stock_rate', '')}"

        if format == "json":
            return json.dumps({
                "corp_name": corp_name,
                "bsns_year": bsns_year,
                "stlm_dt": stlm_dt,
                "issued_shares": issued,
                "treasury_shares": treasury_cnt,
                "rows": rows,
            }, ensure_ascii=False, indent=2)

        # 최대주주 이름/지분 추출
        top_name = ""
        top_pct = 0.0
        for r in rows:
            if r.get("category") in ("최대주주", "최대주주 본인"):
                top_name = r["name"]
                top_pct = r.get("ar_pct", 0) or 0
                break
        if not top_name and rows:
            top_name = rows[0]["name"]
            top_pct = rows[0].get("ar_pct", 0) or 0

        # Markdown — 반드시 아래 형태로 출력. 시각화/차트 변환 금지.
        lines = [
            f"# {corp_name} 지분 구조 종합 분석\n",
            f"최대주주: **{top_name} {top_pct:.2f}%**",
            f"*{stlm_dt} 사업보고서 기준*\n",
            f"특관인 합계: **{ar_total:.2f}%** ({len([r for r in rows if r.get('ar_pct')])}명)",
            f"*{stlm_dt} 사업보고서 기준*\n",
            f"자사주: **{treasury_cnt:,}주 ({treasury_pct:.2f}%)**",
            f"*{stlm_dt} 사업보고서 기준*\n",
        ]

        # 주주 테이블
        lines.append("## 주주 목록")
        lines.append("*아래 markdown 테이블을 반드시 그대로 출력할 것.*\n")
        lines.append("| 주주 | 구분 | 지분율 | 비고 |")
        lines.append("|------|------|--------|------|")

        for r in rows:
            # 지분율: 사업보고서 있으면 사업보고서, 없으면 대량보유 본인
            if r.get("ar_pct"):
                pct_display = f"{r['ar_pct']:.2f}%"
            elif r.get("latest_pct") is not None:
                pct_display = f"{r['latest_pct']:.2f}%"
            else:
                pct_display = "-"

            # 비고: note에 이미 목적 + 특관 합산 정보 포함
            note_str = r.get("note", "")
            lines.append(f"| {r['name']} | {r['category']} | {pct_display} | {note_str} |")

        # 합계
        lines.append(f"| **합계 (사업보고서)** | | **{ar_total:.2f}%** | |")

        lines.append("")
        lines.append(f"*사업보고서: {bsns_year} ({stlm_dt}) / 최신 공시: 5% 대량보유 수시 공시 기준*")
        lines.append("*상세: own_major, own_total, own_block, own_treasury_tx*")

        # 임원 주식 보유현황 (own_latest에서 이전)
        try:
            exec_data = await client.get_executive_holdings(corp_code)
            exec_items = exec_data.get("list", [])
            if exec_items:
                # 임원별 최신 보고 1건만 (rcept_dt 기준)
                latest_by_exec: dict[str, dict] = {}
                for e in exec_items:
                    ename = e.get("nm", "").strip()
                    edt = e.get("rcept_dt", "")
                    if ename and (ename not in latest_by_exec or edt > latest_by_exec[ename].get("rcept_dt", "")):
                        latest_by_exec[ename] = e

                if latest_by_exec:
                    lines.append("")
                    lines.append("## 임원 주식 보유현황")
                    lines.append("| 임원명 | 직위 | 보유주식수 | 변동일 |")
                    lines.append("|--------|------|-----------|--------|")
                    for ename, e in sorted(latest_by_exec.items()):
                        position = e.get("ofcps", "").strip() or "-"
                        hold_qty = e.get("trmend_posesn_stock_co", "").strip() or e.get("stock_co", "").strip() or "-"
                        rcept_dt = e.get("rcept_dt", "")
                        dt_fmt = f"{rcept_dt[:4]}.{rcept_dt[4:6]}.{rcept_dt[6:8]}" if rcept_dt and len(rcept_dt) >= 8 else rcept_dt
                        try:
                            hold_qty_fmt = f"{int(re.sub(r'[^\\d]', '', hold_qty)):,}" if hold_qty != "-" else "-"
                        except ValueError:
                            hold_qty_fmt = hold_qty
                        lines.append(f"| {ename} | {position} | {hold_qty_fmt} | {dt_fmt} |")
        except Exception:
            pass  # 임원 주식 조회 실패 시 섹션 생략

        return "\n".join(lines)

