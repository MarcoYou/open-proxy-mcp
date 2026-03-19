"""주주총회 소집공고 관련 MCP tools"""

from datetime import datetime
from open_proxy_mcp.dart.client import DartClient


def register_tools(mcp):
    """FastMCP 서버에 주주총회 관련 tool 등록"""

    @mcp.tool()
    async def search_shareholder_meetings(
        ticker: str,
        bgn_de: str = "",
        end_de: str = "",
    ) -> str:
        """주주총회 소집공고를 검색합니다.

        종목코드(예: 033780) 또는 회사명(예: 케이티앤지)으로
        해당 기업의 주주총회 소집공고 목록을 반환합니다.

        Args:
            ticker: 종목코드 또는 회사명
            bgn_de: 검색 시작일 (YYYYMMDD). 미입력 시 올해 1월 1일
            end_de: 검색 종료일 (YYYYMMDD). 미입력 시 오늘
        """
        if not bgn_de:
            bgn_de = f"{datetime.now().year}0101"
        if not end_de:
            end_de = datetime.now().strftime("%Y%m%d")

        client = DartClient()
        result = await client.search_filings_by_ticker(
            ticker=ticker,
            bgn_de=bgn_de,
            end_de=end_de,
            pblntf_ty="E",
        )

        corp_info = result.get("corp_info", {})
        filings = [
            item for item in result.get("list", [])
            if "소집" in item.get("report_nm", "")
        ]

        if not filings:
            return f"{corp_info.get('corp_name', ticker)}의 주주총회 소집공고가 없습니다. (검색기간: {bgn_de}~{end_de})"

        lines = [
            f"## {corp_info.get('corp_name', '')} ({corp_info.get('stock_code', '')}) 주주총회 소집공고",
            f"검색기간: {bgn_de} ~ {end_de}",
            f"총 {len(filings)}건",
            "",
        ]
        for item in filings:
            lines.append(f"- **{item['report_nm']}** | 접수일: {item['rcept_dt']} | 접수번호: {item['rcept_no']}")

        return "\n".join(lines)

    @mcp.tool()
    async def get_shareholder_meeting_document(
        rcept_no: str,
        max_length: int = 10000,
    ) -> str:
        """주주총회 소집공고 본문을 가져옵니다.

        search_shareholder_meetings에서 얻은 접수번호(rcept_no)로
        공시 본문 텍스트를 반환합니다.

        Args:
            rcept_no: 접수번호 (예: 20260225000123)
            max_length: 반환할 최대 글자 수 (기본 10000)
        """
        client = DartClient()
        doc = await client.get_document(rcept_no)
        text = doc["text"]
        images = doc["images"]

        result_lines = []

        if images:
            result_lines.append(f"[첨부 이미지 {len(images)}개: {', '.join(images)}]")
            result_lines.append("")

        if len(text) > max_length:
            result_lines.append(text[:max_length])
            result_lines.append(f"\n... (전체 {len(text):,}자 중 {max_length:,}자 표시)")
        else:
            result_lines.append(text)

        return "\n".join(result_lines)
