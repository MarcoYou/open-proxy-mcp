"""위임장 권유 관련 MCP tools (prx_*)

위임장권유참고서류(의결권대리행사권유참고서류) 공시 조회 및 파싱.
"""

import re
import json
from datetime import datetime

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.tools.formatters import resolve_ticker
from open_proxy_mcp.tools.errors import tool_error, tool_not_found, tool_empty


# ── 내부 파서 ──

_DIRECTION_PATTERNS = [
    # "제3-1호 ... 에 찬성" (줄바꿈 포함)
    re.compile(r'제\s*(\d+(?:-\d+)?)\s*호[\s\S]{0,200}?(찬성|반대|기권)', re.DOTALL),
    # "찬성 ... 제3-1호" (줄바꿈 포함)
    re.compile(r'(찬성|반대|기권)[\s\S]{0,200}?제\s*(\d+(?:-\d+)?)\s*호', re.DOTALL),
    # "3-3\n호 ... 찬성" — "제" 없는 경우 (일부 주주측 서식)
    re.compile(r'(\d+(?:-\d+)?)\s*\n?\s*호[\s\S]{0,200}?(찬성|반대|기권)', re.DOTALL),
    # "찬성 ... 3-3호" — "제" 없는 경우
    re.compile(r'(찬성|반대|기권)[\s\S]{0,200}?(\d+(?:-\d+)?)\s*\n?\s*호', re.DOTALL),
]

def _parse_directions(text: str) -> dict[str, str]:
    """Section II-1 자유서술에서 안건별 행사방향 추출"""
    result: dict[str, str] = {}

    # 패턴 1, 3: 안건번호 먼저 ("제N호", "N호" 모두)
    for pat in [_DIRECTION_PATTERNS[0], _DIRECTION_PATTERNS[2]]:
        for m in pat.finditer(text):
            agno, direction = m.group(1), m.group(2)
            if agno not in result:
                result[agno] = direction

    # 패턴 2, 4: 행사방향 먼저 (패턴 1,3으로 못 잡은 것만)
    for pat in [_DIRECTION_PATTERNS[1], _DIRECTION_PATTERNS[3]]:
        for m in pat.finditer(text):
            direction, agno = m.group(1), m.group(2)
            if agno not in result:
                result[agno] = direction

    return result


def _extract_section(text: str, marker: str, end_markers: list[str]) -> str:
    """텍스트에서 특정 섹션 추출"""
    start = text.find(marker)
    if start == -1:
        return ""
    end = len(text)
    for em in end_markers:
        pos = text.find(em, start + len(marker))
        if pos != -1 and pos < end:
            end = pos
    return text[start:end]


def _is_company_side(flr_nm: str, corp_name: str) -> bool:
    """flr_nm == corp_name 이면 회사측"""
    if not flr_nm or not corp_name:
        return False
    # 법인격 제거 후 비교
    def strip(s):
        return re.sub(r'[\(（]?주[\)）]?$|㈜$|주식회사\s*$', '', s.strip()).strip()
    return strip(flr_nm) == strip(corp_name) or strip(corp_name) in strip(flr_nm)


_PROXY_KEYWORDS = (
    # 위임장 관련
    "의결권대리행사권유",      # 의결권대리행사권유참고서류 / 의견표명서
    "위임장권유참고서류",
    "의결권대리행사참고서류",
    # 공개매수 관련 (프록시파이트 동반 발생)
    "공개매수신고서",
    "공개매수설명서",
    "공개매수결과보고서",
    "공개매수에관한의견표명서",
)


def register_tools(mcp):

    @mcp.tool()
    async def prx_search(
        ticker: str,
        year: str = "",
        format: str = "md",
    ) -> str:
        """desc: 위임장 권유 참고서류 검색 — 회사측/주주측 구분 + rcept_no 목록.
        when: [tier-3 Search] 특정 기업의 위임장 공시를 찾을 때. prx_detail/prx_direction에 필요한 rcept_no 획득.
        rule: DART list.json에서 corp_code + 날짜 범위 검색 후 report_nm으로 필터. flr_nm으로 회사측/주주측 구분.
        ref: corp_identifier, prx_detail, prx_direction, prx_fight
        """
        ticker = await resolve_ticker(ticker)
        client = get_dart_client()
        corp = await client.lookup_corp_code(ticker)
        if not corp:
            return tool_not_found("기업", ticker)

        corp_code = corp["corp_code"]
        corp_name = corp["corp_name"]
        bsns_year = year or str(datetime.now().year - 1)
        bgn_de = f"{bsns_year}0101"
        end_de = f"{bsns_year}1231"

        try:
            # pblntf_ty="D" (지분공시) — 위임장권유참고서류가 여기 속함
            # 전체 공시를 순회하지 않고 지분공시 카테고리만 검색
            result = await client.search_filings(
                bgn_de=bgn_de,
                end_de=end_de,
                corp_code=corp_code,
                pblntf_ty="D",
                page_count=100,
            )
        except DartClientError as e:
            return tool_error("위임장 공시 검색", e, ticker=ticker)

        items = result.get("list", [])
        # 위임장 관련 공시만 필터
        proxy_items = [
            item for item in items
            if any(kw in (item.get("report_nm") or "") for kw in _PROXY_KEYWORDS)
        ]

        if not proxy_items:
            return tool_empty("위임장 공시", f"{ticker} {bsns_year}년")

        rows = []
        for item in proxy_items:
            flr_nm = item.get("flr_nm", "")
            side = "회사측" if _is_company_side(flr_nm, corp_name) else "주주측"
            rows.append({
                "rcept_no": item.get("rcept_no", ""),
                "rcept_dt": item.get("rcept_dt", ""),
                "flr_nm": flr_nm,
                "side": side,
                "report_nm": item.get("report_nm", ""),
            })

        # 동일 주총 복수 제출 감지
        has_proxy_fight = any(r["side"] == "주주측" for r in rows)

        if format == "json":
            return json.dumps({
                "corp_name": corp_name,
                "year": bsns_year,
                "count": len(rows),
                "has_proxy_fight": has_proxy_fight,
                "items": rows,
            }, ensure_ascii=False, indent=2)

        lines = [
            f"# {corp_name} 위임장 공시 ({bsns_year})",
            f"총 {len(rows)}건" + (" | ⚠️ **프록시 파이트 감지**" if has_proxy_fight else ""),
            "",
            "| rcept_no | 제출일 | 제출인 | 구분 | 공시명 |",
            "|----------|--------|--------|------|--------|",
        ]
        for r in rows:
            dt = r["rcept_dt"]
            dt_fmt = f"{dt[:4]}.{dt[4:6]}.{dt[6:8]}" if len(dt) == 8 else dt
            lines.append(
                f"| `{r['rcept_no']}` | {dt_fmt} | {r['flr_nm']} | **{r['side']}** | {r['report_nm']} |"
            )

        return "\n".join(lines)

    @mcp.tool()
    async def prx_detail(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """desc: 위임장 권유 상세 — 권유자 보유주식, 권유기간, 대리인, 전자위임장 방법. (비용은 별도 공시인 의결권대리행사권유신고서에 있음)
        when: [tier-5 Detail] 특정 위임장 공시(rcept_no)의 권유자 정보와 방법을 볼 때.
        rule: get_document()로 원문 파싱. Section I(권유자) + Section II-2(위임 방법) 추출.
        ref: prx_search, prx_direction
        """
        client = get_dart_client()
        try:
            doc = await client.get_document(rcept_no)
        except DartClientError as e:
            return tool_error("위임장 원문 조회", e, rcept_no=rcept_no)

        text = doc.get("text", "") or ""
        if not text:
            return tool_empty("위임장 원문", rcept_no)

        # 권유자 정보 섹션
        section_i = _extract_section(text, "I. 의결권 대리행사 권유에 관한 사항",
                                      ["II. 의결권 대리행사 권유의 취지", "III."])
        # 위임 방법 섹션
        section_ii2 = _extract_section(text, "2. 의결권의 위임에 관한 사항",
                                        ["3. 주주총회에서", "III."])

        # 권유기간 추출
        period_m = re.search(r'권유기간\s*[:\s]*([\d.~\s년월일]+)', text)
        period = period_m.group(1).strip() if period_m else "-"

        # 전자위임장 여부
        e_proxy = "가능" if "전자위임장" in section_ii2 and "불가" not in section_ii2 else "확인 필요"

        # 전자투표 기간
        e_vote_m = re.search(r'전자투표\s*기간[^\n]*\n([^\n]+)', section_ii2)
        e_vote = e_vote_m.group(1).strip() if e_vote_m else "-"

        if format == "json":
            return json.dumps({
                "rcept_no": rcept_no,
                "period": period,
                "e_proxy": e_proxy,
                "e_vote_period": e_vote,
                "section_i_raw": section_i[:2000],
                "section_ii2_raw": section_ii2[:1000],
            }, ensure_ascii=False, indent=2)

        lines = [
            f"# 위임장 상세 | {rcept_no}",
            "",
            f"**권유기간**: {period}",
            f"**전자위임장**: {e_proxy}",
            f"**전자투표 기간**: {e_vote}",
            "",
            "## 권유자 정보 (Section I)",
            "```",
            section_i[:1500].strip() if section_i else "(파싱 실패)",
            "```",
            "",
            "## 위임 방법 (Section II-2)",
            "```",
            section_ii2[:800].strip() if section_ii2 else "(파싱 실패)",
            "```",
        ]
        return "\n".join(lines)

    @mcp.tool()
    async def prx_direction(
        rcept_no: str,
        format: str = "md",
    ) -> str:
        """desc: 안건별 의결권 행사방향 — 찬성/반대/기권 파싱.
        when: [tier-5 Detail] 특정 위임장(rcept_no)에서 각 안건에 대한 권유자 입장을 볼 때.
        rule: get_document()로 원문 Section II-1 파싱. 자유서술이므로 정규식으로 추출 — 불명확한 경우 "불명" 반환.
        ref: prx_search, prx_detail, prx_fight
        """
        client = get_dart_client()
        try:
            doc = await client.get_document(rcept_no)
        except DartClientError as e:
            return tool_error("위임장 원문 조회", e, rcept_no=rcept_no)

        text = doc.get("text", "") or ""
        if not text:
            return tool_empty("위임장 원문", rcept_no)

        # Section II-1 추출
        section = _extract_section(
            text,
            "1. 의결권 대리행사의 권유를 하는 취지",
            ["2. 의결권의 위임", "나. 전자위임장", "II-2", "2."],
        )
        if not section:
            # fallback: II. 전체에서 찬성/반대 포함 부분
            section = _extract_section(text, "II. 의결권 대리행사 권유의 취지",
                                        ["III."])

        directions = _parse_directions(section or text[:5000])

        if format == "json":
            return json.dumps({
                "rcept_no": rcept_no,
                "directions": directions,
                "parsed_from": "Section II-1",
                "note": "자유서술 정규식 파싱 — 불명확한 경우 AI가 원문 확인 필요",
            }, ensure_ascii=False, indent=2)

        if not directions:
            return (
                f"# 행사방향 | {rcept_no}\n\n"
                "정규식으로 추출하지 못했습니다. 아래 원문에서 직접 확인하세요.\n\n"
                f"```\n{section[:2000] if section else text[:2000]}\n```"
            )

        icon = {"찬성": "✅", "반대": "❌", "기권": "⬜"}
        lines = [
            f"# 행사방향 | {rcept_no}",
            "",
            "| 안건번호 | 행사방향 |",
            "|----------|----------|",
        ]
        for agno, d in sorted(directions.items(), key=lambda x: x[0]):
            lines.append(f"| 제{agno}호 | {icon.get(d, '')} {d} |")

        lines += [
            "",
            "> 자유서술 정규식 파싱. 불명확한 경우 prx_detail로 원문 확인.",
        ]
        return "\n".join(lines)

    @mcp.tool()
    async def prx_fight(
        ticker: str,
        year: str = "",
        format: str = "md",
    ) -> str:
        """desc: 프록시 파이트 감지 + 양측 행사방향 비교.
        when: [tier-4 Orchestrate] 특정 기업/연도에 프록시 파이트가 있었는지 확인하고 회사측 vs 주주측 입장을 비교할 때.
        rule: prx_search → 회사측/주주측 분류 → prx_direction × N → 안건별 대립 표시.
        ref: corp_identifier, prx_search, prx_direction
        """
        ticker = await resolve_ticker(ticker)
        # tier-3: 위임장 공시 목록 + 회사측/주주측 구분
        search_out = await prx_search(ticker=ticker, year=year, format="json")
        try:
            search_data = json.loads(search_out)
        except json.JSONDecodeError:
            return search_out  # tool_empty 또는 tool_error 문자열 그대로 반환

        corp_name = search_data.get("corp_name", ticker)
        bsns_year = search_data.get("year", year or str(datetime.now().year - 1))
        items = search_data.get("items", [])

        if not items:
            return tool_empty("위임장 공시", f"{ticker} {bsns_year}년")

        company_items = [i for i in items if i.get("side") == "회사측"]
        other_items = [i for i in items if i.get("side") == "주주측"]

        if not other_items:
            return (
                f"# {corp_name} ({bsns_year}) — 프록시 파이트 없음\n\n"
                f"회사측 위임장 {len(company_items)}건만 제출됨."
            )

        # tier-5: 각 위임장 행사방향 파싱 (prx_direction 재사용 — 로직 일원화)
        async def get_dir(rcept_no: str) -> dict[str, str]:
            try:
                out = await prx_direction(rcept_no=rcept_no, format="json")
                return json.loads(out).get("directions", {})
            except Exception:
                return {}

        company_rcept = company_items[0]["rcept_no"] if company_items else None
        company_dir = await get_dir(company_rcept) if company_rcept else {}

        other_sides = []
        for item in other_items:
            d = await get_dir(item["rcept_no"])
            other_sides.append({
                "flr_nm": item.get("flr_nm", ""),
                "rcept_no": item["rcept_no"],
                "rcept_dt": item.get("rcept_dt", ""),
                "directions": d,
            })

        # 전체 안건번호 목록
        all_agnos = set(company_dir.keys())
        for os in other_sides:
            all_agnos.update(os["directions"].keys())

        if format == "json":
            return json.dumps({
                "corp_name": corp_name,
                "year": bsns_year,
                "company_rcept_no": company_rcept,
                "company_directions": company_dir,
                "other_sides": other_sides,
            }, ensure_ascii=False, indent=2)

        icon = {"찬성": "✅", "반대": "❌", "기권": "⬜", None: "-"}

        lines = [
            f"# {corp_name} 프록시 파이트 ({bsns_year})",
            f"회사측 {len(company_items)}건 | 주주측 {len(other_items)}건",
            "",
        ]

        # 헤더: 회사측 + 주주측들
        other_names = [o["flr_nm"] for o in other_sides]
        header = "| 안건번호 | 회사측 | " + " | ".join(other_names) + " |"
        sep = "|----------|--------|" + "--------|" * len(other_sides)
        lines += [header, sep]

        for agno in sorted(all_agnos):
            c_d = company_dir.get(agno)
            c_icon = f"{icon.get(c_d, '-')} {c_d or '-'}"
            others_str = " | ".join(
                f"{icon.get(o['directions'].get(agno), '-')} {o['directions'].get(agno, '-')}"
                for o in other_sides
            )
            lines.append(f"| 제{agno}호 | {c_icon} | {others_str} |")

        lines += [
            "",
            f"회사측 rcept_no: `{company_rcept}`",
        ]
        for o in other_sides:
            dt = o["rcept_dt"]
            dt_fmt = f"{dt[:4]}.{dt[4:6]}.{dt[6:8]}" if len(dt) == 8 else dt
            lines.append(f"주주측 ({o['flr_nm']}): `{o['rcept_no']}` ({dt_fmt})")

        return "\n".join(lines)
