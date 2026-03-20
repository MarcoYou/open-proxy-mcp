"""OpenDART API 클라이언트 — API 호출을 한 곳에서 관리"""

import os
import io
import zipfile
import xml.etree.ElementTree as ET
import httpx
from dotenv import load_dotenv

load_dotenv()

OPENDART_BASE_URL = "https://opendart.fss.or.kr/api"


class DartClientError(Exception):
    """OpenDART API 에러"""
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(f"DART API 에러 [{status}]: {message}")


# 기업 코드 매핑 캐시 (모듈 레벨 — 한번 로드하면 프로세스 동안 유지)
_corp_code_cache: list[dict] | None = None


class DartClient:
    """OpenDART API 호출 래퍼

    하는 일:
    - API 키를 .env에서 읽어서 자동으로 붙임
    - 요청 보내고 JSON 파싱
    - 에러 상태(status != "000")면 예외 발생
    - 종목코드/회사명 → corp_code 변환 (corpCode.xml 캐싱)
    """

    def __init__(self):
        self.api_key = os.getenv("OPENDART_API_KEY")
        if not self.api_key:
            raise ValueError("OPENDART_API_KEY가 .env에 설정되어 있지 않습니다.")

    async def _request(self, endpoint: str, params: dict) -> dict:
        """공통 API 호출 메서드 (JSON 응답용)

        Args:
            endpoint: API 엔드포인트 (예: "list.json")
            params: 쿼리 파라미터 (api_key는 자동 추가)

        Returns:
            API 응답 JSON (dict)
        """
        params["crtfc_key"] = self.api_key
        url = f"{OPENDART_BASE_URL}/{endpoint}"

        async with httpx.AsyncClient() as http:
            response = await http.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

        # DART API는 status "000"이 정상
        status = data.get("status", "")
        if status != "000":
            message = data.get("message", "알 수 없는 에러")
            raise DartClientError(status, message)

        return data

    async def _request_binary(self, endpoint: str, params: dict) -> bytes:
        """공통 API 호출 메서드 (바이너리 응답용 — ZIP 등)"""
        params["crtfc_key"] = self.api_key
        url = f"{OPENDART_BASE_URL}/{endpoint}"

        async with httpx.AsyncClient() as http:
            response = await http.get(url, params=params, timeout=60)
            response.raise_for_status()

        return response.content

    # ── 기업 코드 매핑 ──

    async def _load_corp_codes(self) -> list[dict]:
        """corpCode.xml에서 전체 기업 코드 목록 로드 (캐싱)

        Returns:
            [{"corp_code": "00126380", "corp_name": "삼성전자",
              "stock_code": "005930", "modify_date": "20240101"}, ...]
        """
        global _corp_code_cache
        if _corp_code_cache is not None:
            return _corp_code_cache

        # ZIP 다운로드
        data = await self._request_binary("corpCode.xml", {})
        z = zipfile.ZipFile(io.BytesIO(data))
        xml_file = z.namelist()[0]
        xml_content = z.read(xml_file)

        # XML 파싱
        root = ET.fromstring(xml_content)
        corps = []
        for item in root.findall("list"):
            corps.append({
                "corp_code": item.findtext("corp_code", ""),
                "corp_name": item.findtext("corp_name", ""),
                "stock_code": item.findtext("stock_code", "").strip(),
                "modify_date": item.findtext("modify_date", ""),
            })

        _corp_code_cache = corps
        return corps

    async def lookup_corp_code(self, query: str) -> dict | None:
        """종목코드(ticker) 또는 회사명으로 corp_code 조회

        Args:
            query: 종목코드 (예: "033780") 또는 회사명 (예: "KT&G", "삼성전자")

        Returns:
            {"corp_code": "...", "corp_name": "...", "stock_code": "..."} 또는 None
        """
        corps = await self._load_corp_codes()

        # 1) 종목코드로 정확히 매치
        for corp in corps:
            if corp["stock_code"] == query:
                return corp

        # 2) 회사명 정확히 매치
        for corp in corps:
            if corp["corp_name"] == query:
                return corp

        # 3) 회사명 부분 매치 (상장 기업 우선)
        partial = []
        for corp in corps:
            if query in corp["corp_name"]:
                partial.append(corp)

        if partial:
            # 상장 기업(stock_code 있는 것) 우선
            listed = [c for c in partial if c["stock_code"]]
            return listed[0] if listed else partial[0]

        return None

    # ── 공시 검색 ──

    async def search_filings(
        self,
        bgn_de: str,
        end_de: str,
        pblntf_ty: str = "",
        corp_code: str = "",
        corp_name: str = "",
        page_no: int = 1,
        page_count: int = 100,
    ) -> dict:
        """공시 검색 (list.json)

        Args:
            bgn_de: 검색 시작일 (YYYYMMDD)
            end_de: 검색 종료일 (YYYYMMDD)
            pblntf_ty: 공시유형 (A=정기, B=주요사항, E=기타공시 등)
            corp_code: DART 기업코드 (8자리)
            corp_name: 회사명 (부분 매치)
            page_no: 페이지 번호
            page_count: 페이지당 건수 (최대 100)

        Returns:
            {"list": [...], "total_count": ..., ...}
        """
        params = {
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_no": str(page_no),
            "page_count": str(page_count),
        }
        if pblntf_ty:
            params["pblntf_ty"] = pblntf_ty
        if corp_code:
            params["corp_code"] = corp_code
        if corp_name:
            params["corp_name"] = corp_name

        return await self._request("list.json", params)

    async def search_filings_by_ticker(
        self,
        ticker: str,
        bgn_de: str,
        end_de: str,
        pblntf_ty: str = "",
        page_no: int = 1,
        page_count: int = 100,
    ) -> dict:
        """종목코드 또는 회사명으로 공시 검색 (편의 메서드)

        Args:
            ticker: 종목코드 (예: "033780") 또는 회사명 (예: "KT&G")
            bgn_de: 검색 시작일 (YYYYMMDD)
            end_de: 검색 종료일 (YYYYMMDD)
            pblntf_ty: 공시유형

        Returns:
            {"list": [...], "total_count": ..., "corp_info": {...}, ...}
        """
        corp = await self.lookup_corp_code(ticker)
        if not corp:
            raise DartClientError("404", f"'{ticker}'에 해당하는 기업을 찾을 수 없습니다.")

        result = await self.search_filings(
            bgn_de=bgn_de,
            end_de=end_de,
            pblntf_ty=pblntf_ty,
            corp_code=corp["corp_code"],
            page_no=page_no,
            page_count=page_count,
        )
        result["corp_info"] = corp
        return result

    # ── 공시 본문 ──

    async def get_document(self, rcept_no: str) -> dict:
        """공시 본문 텍스트 가져오기

        document.xml API로 ZIP 다운로드 → XML 추출 → 텍스트 변환
        이미지 파일명은 본문에서 제거하고 별도 목록으로 반환.

        Args:
            rcept_no: 접수번호

        Returns:
            {"text": 본문 텍스트, "images": [이미지 파일명 목록]}
        """
        import re

        data = await self._request_binary("document.xml", {"rcept_no": rcept_no})
        z = zipfile.ZipFile(io.BytesIO(data))

        # XML 파일 찾기
        xml_files = [f for f in z.namelist() if f.endswith(".xml")]
        if not xml_files:
            raise DartClientError("NO_DOC", "ZIP에 XML 문서가 없습니다.")

        xml_content = z.read(xml_files[0])

        # 인코딩 처리
        for encoding in ["utf-8", "euc-kr", "cp949"]:
            try:
                text_html = xml_content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text_html = xml_content.decode("utf-8", errors="replace")

        # 이미지 파일명 추출 (src 속성에서)
        images = re.findall(r'[\w\-./]+\.(?:jpg|jpeg|png|gif|bmp)', text_html, re.IGNORECASE)
        images = list(dict.fromkeys(images))  # 중복 제거, 순서 유지

        # HTML/XML 태그 제거 → 텍스트 (블록 태그는 줄바꿈으로 보존)
        text = re.sub(r'<(?:br|BR)\s*/?>', '\n', text_html)
        text = re.sub(r'</(?:p|P|div|DIV|tr|TR|li|LI|h\d|H\d|table|TABLE)>', '\n', text)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'&[a-zA-Z]+;', ' ', text)
        # 이미지 파일명 제거
        for img in images:
            text = text.replace(img, '')
        text = re.sub(r'[^\S\n]+', ' ', text)    # 수평 공백만 정규화
        text = re.sub(r'\n\s*\n+', '\n\n', text)  # 빈 줄 정리
        text = text.strip()

        return {"text": text, "images": images}
