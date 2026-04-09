"""지분 구조 관련 MCP tools (own_*)"""

import json
import os
import re
import logging
from datetime import datetime

from open_proxy_mcp.dart.client import DartClient, DartClientError, get_dart_client
from open_proxy_mcp.tools.formatters import (
    _parse_holding_purpose, _parse_holding_purpose_from_document,
    _normalize_purpose, _pct, _format_number,
    _format_major_shareholders, _format_stock_total, _format_treasury_stock,
    _format_treasury_tx, _format_block_holders, _format_latest_snapshot,
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
        when: 최대주주가 누구인지, 특관인 포함 합산 지분율을 볼 때.
        rule: 사업보고서 신고 기준. 실질 최다보유자와 다를 수 있음 (대량보유는 own_block 참조). 우선주 별도 표시.
        ref: own_block, own_latest, own_manual

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
        when: 발행주식수/자사주/유통비율을 볼 때.
        rule: 사업보고서 기준. 보통주/우선주 각각 표시.
        ref: own_treasury, own_manual

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
        when: 자사주 보유 현황 상세를 볼 때. 직접취득/신탁계약 구분.
        rule: 사업보고서 기준 연간 baseline. 최신 이벤트는 own_treasury_tx 참조.
        ref: own_treasury_tx, own_total, own_manual

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
        when: 자사주 취득/처분/신탁 의사결정을 볼 때. 수시 공시 기반.
        rule: 4개 DART API 호출 (취득+처분+신탁체결+해지). 경영권 방어/주주환원 시그널.
        ref: own_treasury, own_manual, div_detail

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
        when: 5% 이상 대량보유자와 보유목적을 볼 때. 프록시 파이트 감지.
        rule: 수시 공시 기반. 보유목적은 원문 파싱 (report_resn + document.xml). 보고자+특별관계자 합산.
        ref: own_major, own_latest, own_manual, agm_result

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
    async def own_latest(
        ticker: str,
        year: str = "",
        format: str = "md",
    ) -> str:
        """desc: 전 주주 최신 스냅샷 - 사업보고서(최대주주) + 수시(5% 대량보유 + 임원소유) 통합.
        when: 특정 기업의 현재 주주 구성을 종합적으로 볼 때.
        rule: 3개 API 호출. 사업보고서 기준일과 수시 공시일이 다를 수 있음 - 기준일 확인 필요.
        ref: own_major, own_block, own_manual

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

        empty = {"list": []}

        try:
            major = await client.get_major_shareholders(corp_code, bsns_year)
        except DartClientError:
            major = empty

        try:
            block = await client.get_block_holders(corp_code)
        except DartClientError:
            block = empty

        try:
            exec_data = await client.get_executive_holdings(corp_code)
        except DartClientError:
            exec_data = empty

        if format == "json":
            result = {"major": major, "block": block, "executive": exec_data}
            return json.dumps(result, ensure_ascii=False, indent=2)

        return _format_latest_snapshot(major, block, exec_data)

    @mcp.tool()
    async def own(
        ticker: str,
        format: str = "md",
    ) -> str:
        """desc: 지분 구조 종합 오케스트레이터. 최대주주, 주식총수, 자사주, 소액주주, 5% 대량보유 한 번에.
        when: 특정 기업 지분 구조를 빠르게 파악할 때. 상세 분석은 개별 own_* tool.
        rule: 5+ API 호출 + 보고자 수만큼 원문 다운로드. 보유목적까지 파싱.
        ref: own_major, own_total, own_block, own_manual

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

        # 사업보고서 기반 (5 API calls)
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

        # 수시 (1 API call)
        try:
            block = await client.get_block_holders(corp_code)
        except DartClientError:
            block = empty

        # 5% 대량보유 보유목적 — 보고자별 최신 1건 원문 파싱
        block_items = block.get("list", [])
        latest_by_reporter: dict[str, dict] = {}
        for item in block_items:
            name = item.get("repror", "")
            dt = item.get("rcept_dt", "")
            if name and (name not in latest_by_reporter or dt > latest_by_reporter[name].get("rcept_dt", "")):
                latest_by_reporter[name] = item

        purposes: dict[str, str] = {}
        for name, item in latest_by_reporter.items():
            rcept_no = item.get("rcept_no", "")
            purpose = _parse_holding_purpose(item.get("report_tp", ""), item.get("report_resn", ""))
            if purpose not in ("불명", "단순투자/일반투자"):
                purposes[rcept_no] = purpose
            else:
                try:
                    doc = await client.get_document(rcept_no)
                    html = doc.get("html", "") or doc.get("full_text", "")
                    parsed = _parse_holding_purpose_from_document(html)
                    purposes[rcept_no] = parsed if parsed != "불명" else purpose
                except Exception:
                    purposes[rcept_no] = purpose

        api_calls = 5 + len([1 for n, i in latest_by_reporter.items()
                             if purposes.get(i.get("rcept_no", ""), "") in ("불명", "단순투자/일반투자")
                             or i.get("rcept_no", "") not in purposes])
        # 실제로는 report_resn에서 잡히면 원문 안 받으므로 정확한 수는 달라질 수 있음
        api_calls = 5 + len(latest_by_reporter)  # 최대치 기준

        if format == "json":
            result = {
                "corp_name": corp_name,
                "bsns_year": bsns_year,
                "major_shareholders": major,
                "stock_total": stock_total,
                "minority_shareholders": minority,
                "block_holders": block,
                "purposes": purposes,
            }
            return json.dumps(result, ensure_ascii=False, indent=2)

        # ── 마크다운 종합 ──
        sections = [f"# {corp_name} 지분 구조\n"]

        # 최대주주 + 특관인 합계 (사업보고서 기준)
        major_items = major.get("list", [])
        if major_items:
            stlm_dt = major_items[0].get("stlm_dt", "")

            # 보통주만 집계
            top_name = ""
            top_rt = ""
            total_rt = 0.0
            related_count = 0
            shareholder_details: list[tuple[str, str, float]] = []  # (name, relate, pct)

            for item in major_items:
                if "보통" not in item.get("stock_knd", "보통"):
                    continue
                name = item.get("nm", "").strip()
                relate = item.get("relate", "").strip()
                try:
                    rt = float(item.get("trmend_posesn_stock_qota_rt", "0") or "0")
                except ValueError:
                    rt = 0.0

                # "계" 행 스킵
                if name == "계":
                    continue

                total_rt += rt
                if "본인" in relate or "최대주주" in relate:
                    if not top_name:
                        top_name = name
                        top_rt = item.get("trmend_posesn_stock_qota_rt", "")
                else:
                    related_count += 1

                if rt >= 1.0:
                    shareholder_details.append((name, relate, rt))

            sections.append(f"**최대주주 (사업보고서 신고 기준)**: {top_name} {_pct(top_rt)}")
            if related_count > 0:
                sections.append(f"**특수관계인 포함 합계**: {total_rt:.2f}% ({related_count}명)")

            # 1% 이상 특관인 상세
            others = [(n, r, p) for n, r, p in shareholder_details if n != top_name]
            if others:
                sections.append("**주요 특수관계인 (보통주 1%+)**:")
                for name, relate, pct in sorted(others, key=lambda x: x[2], reverse=True):
                    sections.append(f"  - {name} ({relate}): {pct:.2f}%")

            sections.append(f"*기준: {bsns_year} 사업보고서 ({stlm_dt})*\n")

        # 주식총수 + 자사주 비율
        stock_items = stock_total.get("list", [])
        issued = 0
        treasury_cnt = 0
        floating = 0
        for item in stock_items:
            if "보통" in item.get("se", ""):
                issued = int(re.sub(r'[^\d]', '', item.get("istc_totqy", "0")) or "0")
                treasury_cnt = int(re.sub(r'[^\d]', '', item.get("tesstk_co", "0")) or "0")
                floating = int(re.sub(r'[^\d]', '', item.get("distb_stock_co", "0")) or "0")
                break

        if issued > 0:
            treasury_pct = (treasury_cnt / issued * 100) if issued else 0
            sections.append(f"**발행주식**: {issued:,}주 (보통주)")
            sections.append(f"**자사주**: {treasury_cnt:,}주 ({treasury_pct:.2f}%, 의결권 없음)")
            sections.append(f"**유통주식**: {floating:,}주\n")

        # 소액주주
        minority_items = minority.get("list", [])
        if minority_items:
            m = minority_items[0]
            sections.append(
                f"**소액주주**: {_format_number(m.get('shrholdr_co', ''))}명, "
                f"보유 {m.get('hold_stock_rate', '')}\n"
            )

        # 5% 대량보유 (보유목적 포함)
        if latest_by_reporter:
            sections.append("**5% 대량보유 (보고자+특별관계자 합산 기준)**:")
            for name, item in sorted(
                latest_by_reporter.items(),
                key=lambda x: float(x[1].get("stkrt", 0) or 0),
                reverse=True,
            ):
                rcept_no = item.get("rcept_no", "")
                purpose = purposes.get(rcept_no, "불명")
                sections.append(
                    f"- {name}: {_pct(item.get('stkrt', ''))} "
                    f"({purpose}, {item.get('rcept_dt', '')})"
                )
            sections.append("")

        sections.append("*상세: own_major, own_total, own_treasury, own_block, own_treasury_tx, own_latest*")
        sections.append(f"*API 호출: {api_calls}회*")

        return "\n".join(sections)

    @mcp.tool()
    async def own_full_analysis(
        ticker: str,
        format: str = "md",
    ) -> str:
        """desc: 지분 구조 종합 분석 — 사업보고서 vs 최신 공시 지분율 비교 테이블.
        when: 특정 기업 주주 구성을 사업보고서 기준과 최신 공시 기준으로 비교할 때.
        rule: own_major(사업보고서) + own_block(수시 공시) 데이터를 통합. 변동 감지 + 보유목적 표시.
        ref: own_major, own_block, own_total, own_manual

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
                    # ctr_stkrt = 보고자 본인 지분 (합산 아님)
                    latest_pct = float(b_item.get("ctr_stkrt", 0) or 0)
                    total_pct = float(b_item.get("stkrt", 0) or 0)
                except ValueError:
                    latest_pct = None
                    total_pct = None
                latest_date = b_item.get("rcept_dt", "")
                purpose = purposes.get(name, "")
                if purpose:
                    note = purpose
                # 본인+특관 합산도 표시
                if total_pct and latest_pct and total_pct > latest_pct + 0.01:
                    note += f" (특관 포함 {total_pct:.2f}%)"

            rows.append({
                "name": name,
                "category": category,
                "ar_pct": info["pct"],
                "latest_pct": latest_pct,
                "latest_date": latest_date,
                "note": note,
            })

        # 사업보고서에 없지만 5% 대량보유에만 있는 주주
        for name, item in latest_by_reporter.items():
            if name not in ar_shareholders:
                try:
                    ctr_pct = float(item.get("ctr_stkrt", 0) or 0)
                    total_pct = float(item.get("stkrt", 0) or 0)
                except ValueError:
                    ctr_pct = 0.0
                    total_pct = 0.0
                pct = ctr_pct if ctr_pct > 0 else total_pct
                if pct > 0:
                    purpose = purposes.get(name, "")
                    note = purpose
                    if total_pct > ctr_pct + 0.01:
                        note += f" (특관 포함 {total_pct:.2f}%)"
                    rows.append({
                        "name": name,
                        "category": "5% 대량보유",
                        "ar_pct": None,
                        "latest_pct": pct,
                        "latest_date": item.get("rcept_dt", ""),
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

        # Markdown
        lines = [
            f"# {corp_name} 지분 구조 종합 분석\n",
            f"**사업보고서 기준**: {bsns_year} ({stlm_dt})",
            f"**발행주식(보통주)**: {issued:,}주",
            f"**자사주**: {treasury_cnt:,}주 ({treasury_pct:.2f}%)",
        ]
        if minority_info:
            lines.append(f"**소액주주**: {minority_info}")
        lines.append("")

        # 주주 비교 테이블
        lines.append("| 주주 | 구분 | 지분율(사업보고서) | 지분율(최신 공시) | 비고 |")
        lines.append("|------|------|-----------------|---------------|------|")

        for r in rows:
            ar = f"{r['ar_pct']:.2f}%" if r.get("ar_pct") else "-"
            if r.get("latest_pct") is not None:
                lt = f"{r['latest_pct']:.2f}%"
                dt = r.get("latest_date", "")
                if dt:
                    lt_display = f"{lt} ({dt[:4]}.{dt[4:6]}.{dt[6:8]})"
                else:
                    lt_display = lt
            else:
                lt_display = "-"
            note = r.get("note", "")
            if not note and r.get("latest_pct") is None:
                note = "최신 공시 없음"
            lines.append(f"| {r['name']} | {r['category']} | {ar} | {lt_display} | {note} |")

        # 합계
        lines.append(f"| **합계** | | **{ar_total:.2f}%** | | |")

        lines.append("")
        lines.append(f"*사업보고서: {bsns_year} ({stlm_dt}) / 최신 공시: 5% 대량보유 수시 공시 기준*")
        lines.append("*상세: own_major, own_total, own_block, own_treasury_tx*")

        return "\n".join(lines)

    @mcp.tool()
    async def own_manual() -> str:
        """desc: ownership tool 구조, 출력 형태 가이드, 컬럼별 소스 매핑, 판정 기준.
        when: 지분 구조 분석 시 또는 출력 형태 판단이 필요할 때.
        rule: 없음.
        ref: OWN_TOOL_RULE.md, OWN_CASE_RULE.md"""
        pkg_dir = os.path.dirname(os.path.dirname(__file__))
        parts = []
        for fname in ("OWN_TOOL_RULE.md", "OWN_CASE_RULE.md"):
            fpath = os.path.join(pkg_dir, fname)
            try:
                with open(fpath, "r") as f:
                    parts.append(f.read())
            except FileNotFoundError:
                parts.append(f"\n({fname}를 찾을 수 없습니다)")
        return "\n\n---\n\n".join(parts)
