"""위임장 권유 + 경영권 분쟁 관련 MCP tools (proxy_*)

위임장권유참고서류, 소송(경영권분쟁) 공시 조회 및 파싱.
"""

import re
import json
from datetime import datetime

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.tools.formatters import resolve_ticker, resolve_to_ticker, strip_css
from open_proxy_mcp.tools.errors import tool_error, tool_not_found, tool_empty


async def _resolve_proxy_rcept_no(ticker: str = "", rcept_no: str = "") -> str:
    """ticker 또는 rcept_no 중 하나로 위임장 공시 rcept_no를 확정.

    - ticker만 주어지면: proxy_search 로직으로 최신 위임장 rcept_no 자동 획득.
    - rcept_no만 주어지면: 그대로 사용.
    - 둘 다 주어지면: rcept_no 우선.
    - 둘 다 없으면: ValueError.
    """
    if rcept_no:
        return rcept_no
    if not ticker:
        raise ValueError("ticker 또는 rcept_no 중 하나는 필수입니다.")

    ticker = await resolve_to_ticker(ticker)
    client = get_dart_client()
    corp = await client.lookup_corp_code(ticker)
    if not corp:
        raise ValueError(f"'{ticker}'에 해당하는 기업을 찾을 수 없습니다.")

    corp_code = corp["corp_code"]
    year = str(datetime.now().year)
    bgn_de = f"{year}0101"
    end_de = datetime.now().strftime("%Y%m%d")

    try:
        result = await client.search_filings(
            bgn_de=bgn_de, end_de=end_de, corp_code=corp_code, pblntf_ty="D",
        )
    except DartClientError:
        raise ValueError(f"'{ticker}'의 위임장 공시를 검색할 수 없습니다.")

    filings = [
        item for item in result.get("list", [])
        if any(kw in item.get("report_nm", "") for kw in _PROXY_KEYWORDS)
    ]
    if not filings:
        raise ValueError(f"'{ticker}'의 위임장 공시를 찾을 수 없습니다. ({bgn_de}~{end_de})")

    filings.sort(key=lambda x: x.get("rcept_dt", ""))
    return filings[-1]["rcept_no"]


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
    async def proxy_search(
        ticker: str,
        year: str = "",
        format: str = "md",
    ) -> str:
        """desc: 경영권/위임장/proxy/M&A 관련 공시 검색 — 회사측/주주측 구분 + rcept_no 목록.
        when: [tier-3 Search] 경영권, 위임장, proxy, dispute, M&A, 표대결, 프록시파이트 관련 공시를 찾을 때. proxy_detail/proxy_direction에 필요한 rcept_no 획득.
        rule: DART list.json에서 corp_code + 날짜 범위 검색 후 report_nm으로 필터. flr_nm으로 회사측/주주측 구분.
        ref: corp_identifier, proxy_detail, proxy_direction, proxy_fight
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
    async def proxy_detail(
        ticker: str = "",
        rcept_no: str = "",
        format: str = "md",
    ) -> str:
        """desc: 위임장/proxy 상세 — 권유자 보유주식, 권유기간, 대리인, 전자위임장 방법.
        when: [tier-5 Detail] 위임장 권유자 정보가 필요할 때. ticker만 넣으면 최신 위임장 공시를 자동 탐색.
        rule: get_document()로 원문 파싱. Section I(권유자) + Section II-2(위임 방법) 추출. 기업 식별이 불확실하면 corp_identifier 먼저 호출.
        ref: proxy_search, proxy_direction, corp_identifier

        Args:
            ticker: 종목코드 또는 회사명 (예: "고려아연", "010130"). rcept_no 미입력 시 위임장 공시 자동 탐색.
            rcept_no: 접수번호 직접 지정. 입력 시 ticker보다 우선.
            format: "md" (기본) 또는 "json"
        """
        rcept_no = await _resolve_proxy_rcept_no(ticker, rcept_no)
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
    async def proxy_direction(
        ticker: str = "",
        rcept_no: str = "",
        format: str = "md",
    ) -> str:
        """desc: 안건별 의결권 행사방향 — 찬성/반대/기권 파싱.
        when: [tier-5 Detail] 위임장 권유자의 안건별 입장이 필요할 때. ticker만 넣으면 최신 위임장 공시를 자동 탐색.
        rule: get_document()로 원문 Section II-1 파싱. 자유서술이므로 정규식으로 추출 — 불명확한 경우 "불명" 반환. 기업 식별이 불확실하면 corp_identifier 먼저 호출.
        ref: proxy_search, proxy_detail, proxy_fight, corp_identifier

        Args:
            ticker: 종목코드 또는 회사명 (예: "고려아연", "010130"). rcept_no 미입력 시 위임장 공시 자동 탐색.
            rcept_no: 접수번호 직접 지정. 입력 시 ticker보다 우선.
            format: "md" (기본) 또는 "json"
        """
        rcept_no = await _resolve_proxy_rcept_no(ticker, rcept_no)
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
            "> 자유서술 정규식 파싱. 불명확한 경우 proxy_detail로 원문 확인.",
        ]
        return "\n".join(lines)

    @mcp.tool()
    async def proxy_fight(
        ticker: str,
        year: str = "",
        format: str = "md",
    ) -> str:
        """desc: 경영권/proxy 표대결 감지 + 회사측 vs 주주측 행사방향 비교.
        when: [tier-4 Orchestrate] 경영권, 위임장, proxy, dispute, M&A, 표대결, 프록시파이트, 경영권 분쟁이 있었는지 확인하고 양측 입장을 비교할 때.
        rule: proxy_search → 회사측/주주측 분류 → proxy_direction × N → 안건별 대립 표시. 경영권 분쟁 중 지분율 변화가 궁금하면 ownership_block을 함께 호출할 것.
        ref: corp_identifier, proxy_search, proxy_direction, ownership_block
        """
        ticker = await resolve_ticker(ticker)
        # tier-3: 위임장 공시 목록 + 회사측/주주측 구분
        search_out = await proxy_search(ticker=ticker, year=year, format="json")
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

        # tier-5: 각 위임장 행사방향 파싱 (proxy_direction 재사용 — 로직 일원화)
        async def get_dir(rcept_no: str) -> dict[str, str]:
            try:
                out = await proxy_direction(rcept_no=rcept_no, format="json")
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

    # ── 소송 / 기업가치제고 ──

    _LITIGATION_KEYWORDS = (
        "소송등의제기",
        "소송등의신청",
        "소송등의판결",
        "소송등의결정",
        "경영권분쟁소송",
    )

    @mcp.tool()
    async def proxy_litigation(
        ticker: str,
        year: str = "",
        format: str = "md",
    ) -> str:
        """desc: 경영권 분쟁 소송 공시 검색 -- 소송등의제기/신청, 판결/결정 타임라인.
        when: [tier-4 Analysis] 경영권 분쟁, 소송, 가처분, 판결, 법적 분쟁 현황을 파악할 때. proxy_fight와 함께 호출하면 분쟁의 전체 그림을 볼 수 있음.
        rule: DART pblntf_ty=I(거래소공시) + B(주요사항)에서 소송 키워드 필터. 원문 최신 3건 파싱.
        ref: corp_identifier, proxy_fight, proxy_search, ownership_block
        """
        ticker = await resolve_ticker(ticker)
        client = get_dart_client()
        corp = await client.lookup_corp_code(ticker)
        if not corp:
            return tool_not_found("기업", ticker)

        corp_code = corp["corp_code"]
        corp_name = corp["corp_name"]
        now = datetime.now()
        bsns_year = year or str(now.year)
        bgn_de = f"{int(bsns_year) - 1}0101"
        end_de = f"{bsns_year}1231"

        all_items = []
        for pty in ("I", "B"):
            try:
                result = await client.search_filings(
                    bgn_de=bgn_de, end_de=end_de,
                    corp_code=corp_code, pblntf_ty=pty, page_count=100,
                )
                all_items.extend(result.get("list", []))
            except DartClientError:
                pass  # 해당 카테고리에 데이터 없음 (013)
        litigation_items = [
            item for item in all_items
            if any(kw in (item.get("report_nm") or "").replace(" ", "").replace("\u3165", "")
                   for kw in _LITIGATION_KEYWORDS)
        ]

        if not litigation_items:
            return tool_empty("소송 공시", f"{ticker} {bsns_year}년")

        litigation_items.sort(key=lambda x: x.get("rcept_dt", ""), reverse=True)

        # 최신 3건 원문 파싱 (정정 제외)
        detail_items = [i for i in litigation_items if "[기재정정]" not in (i.get("report_nm") or "")][:3]
        details = {}
        for item in detail_items:
            try:
                doc = await client.get_document(item["rcept_no"])
                text = strip_css(doc.get("text", "") or "")
                details[item["rcept_no"]] = text[:3000]
            except Exception:
                pass

        if format == "json":
            return json.dumps({
                "corp_name": corp_name,
                "period": f"{bgn_de[:4]}-{end_de[:4]}",
                "count": len(litigation_items),
                "items": [{
                    "rcept_no": i.get("rcept_no", ""),
                    "rcept_dt": i.get("rcept_dt", ""),
                    "report_nm": (i.get("report_nm") or "").strip(),
                    "flr_nm": i.get("flr_nm", ""),
                    "detail": details.get(i.get("rcept_no"), ""),
                } for i in litigation_items],
            }, ensure_ascii=False, indent=2)

        lines = [
            f"# {corp_name} 소송/분쟁 공시",
            f"기간: {bgn_de[:4]}-{end_de[:4]} | 총 {len(litigation_items)}건",
            "",
            "| 날짜 | 공시명 | 제출인 | rcept_no |",
            "|------|--------|--------|----------|",
        ]
        for item in litigation_items:
            dt = item.get("rcept_dt", "")
            dt_fmt = f"{dt[:4]}.{dt[4:6]}.{dt[6:8]}" if len(dt) == 8 else dt
            rn = (item.get("report_nm") or "").strip()
            lines.append(
                f"| {dt_fmt} | {rn} | {item.get('flr_nm', '')} | `{item.get('rcept_no', '')}` |"
            )

        if details:
            lines.append("")
            lines.append("## 최신 공시 원문 (요약)")
            for rcept_no, text in details.items():
                lines.append(f"\n### `{rcept_no}`")
                lines.append("```")
                lines.append(text[:2000])
                lines.append("```")

        return "\n".join(lines)

