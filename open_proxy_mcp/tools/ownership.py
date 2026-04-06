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
        """최대주주+특수관계인 지분 현황 + 변동이력.
        사업보고서 기준 보통주 기말수량/지분율. 최대주주 변경 시 이전→현재 이력 포함.
        최대주주는 사업보고서 신고 기준이므로 실질 최다보유자와 다를 수 있음 (대량보유는 own_block 참조).
        데이터 없으면 해당 연도 사업보고서가 아직 공시되지 않은 것.

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
        """주식 총수 + 자기주식 + 유통주식 + 소액주주 현황.
        사업보고서 기준 발행주식 총수, 자기주식수, 유통주식수. 소액주주 수와 보유비율 포함.
        자기주식은 의결권 없음. 수시 자사주 거래는 own_treasury_tx 참조.

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
        """자기주식 기말 보유수량 (사업보고서 baseline).
        취득방법별(직접취득/신탁 등) 기초/취득/처분/소각/기말 수량.
        사업보고서 이후 자사주 거래 이벤트는 own_treasury_tx 참조.

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
        """자사주 취득결정/처분결정/신탁체결/해지 이벤트 (이사회 결정 공시 기반).
        4개 API를 한 번에 조회. API 4회 사용.
        기말 보유수량 기준은 own_treasury, 사업보고서 기반 현황은 own_total 참조.

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
        """5% 대량보유 상황보고 — 보유목적(단순투자/일반투자/경영참여) + 목적변경 감지.
        보유비율은 보고자+특별관계자 합산 기준 (사업보고서 개별 지분율과 다를 수 있음).
        보고자별 최신 보고서 원문에서 보유목적 파싱. API 1회 + 보고자 수만큼 원문 다운로드.
        여러 기업 연속 조회 시 rate limit 주의.

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
        """전 주주 최신 스냅샷 + 변동 집계.
        사업보고서 기준 최대주주 + 5% 대량보유(수시) + 임원소유(수시)를 합쳐서
        사업보고서 이후 어떻게 달라졌는지 주체별로 반환. API 3회 사용.
        임원소유 데이터는 대형주의 경우 수천 건일 수 있음 (최근 5건만 표시).

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
        """지분 구조 종합 오케스트레이터.
        사업보고서 기준 지분 구조(최대주주, 주식총수, 자사주, 소액주주)를 baseline으로,
        5% 대량보유(수시 공시)로 최신 변동을 반영하여 한 번에 반환.
        상세 분석은 개별 own_* tool 사용. API 6회 + 보고자 수만큼 원문 다운로드.

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
    async def own_manual() -> str:
        """desc: ownership tool 구조, 출력 형태 가이드, 컬럼별 소스 매핑, 판정 기준.
        when: 지분 구조 분석 시 또는 출력 형태 판단이 필요할 때.
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
