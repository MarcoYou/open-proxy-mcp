"""OpenDART API 클라이언트 — API 호출을 한 곳에서 관리

⚠️ DART 접근 시 주의사항 (API + 웹 공통):
  - OpenDART API: 분당 1,000회 초과 시 24시간 IP 차단. 일일 20,000회 한도.
  - DART 웹 스크래핑: 공식 API가 아니므로 더 보수적으로 접근.
    과도한 요청은 DDoS로 오해받을 수 있음.
  - 모든 요청에 최소 간격(API: 0.1초, 웹: 2초) 강제 적용.
  - 배치 작업 시 CLAUDE.md의 "DART API 호출 규칙" 반드시 참조.
"""

import os
import io
import re
import json
import time
import asyncio
import logging
import zipfile
import xml.etree.ElementTree as ET
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

OPENDART_BASE_URL = "https://opendart.fss.or.kr/api"
DART_WEB_BASE_URL = "https://dart.fss.or.kr"

# ── Rate Limiting ──
# API와 웹 스크래핑에 각각 다른 최소 간격 적용
_MIN_INTERVAL_API = 0.1     # API: 최소 0.1초 간격 (분당 600회 이하)
_MIN_INTERVAL_WEB = 2.0     # 웹: 최소 2초 간격 (DDoS 오해 방지)


class DartClientError(Exception):
    """OpenDART API 에러"""
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(f"DART API 에러 [{status}]: {message}")


# 기업 코드 매핑 캐시 (모듈 레벨 — 한번 로드하면 프로세스 동안 유지)
_corp_code_cache: list[dict] | None = None

# 법인격 suffix 제거 패턴
_CORP_SUFFIX_RE = re.compile(
    r'\s*[\(（]?주[\)）]?\s*$'     # (주), ㈜, 주)
    r'|\s*㈜\s*$'
    r'|\s*주식회사\s*$'
    r'|\s*co\.,?\s*ltd\.?\s*$'
    r'|\s*inc\.?\s*$'
    r'|\s*corp\.?\s*$',
    re.IGNORECASE
)

# 알려진 약칭/영문명 → DART 정식 한글명 매핑 (lowercase key)
_CORP_ALIASES: dict[str, str] = {
    "ls electric": "엘에스일렉트릭",
    "ls일렉트릭": "엘에스일렉트릭",
    "sk바이오팜": "에스케이바이오팜",
    "kt&g": "케이티앤지",
    "ktng": "케이티앤지",
    "tkg휴켐스": "티케이지휴켐스",
    "휴켐스": "티케이지휴켐스",
}

def _normalize_corp_name(name: str) -> str:
    """법인격 suffix 제거 후 소문자 변환 (매칭용)"""
    name = _CORP_SUFFIX_RE.sub("", name.strip())
    return name.strip().lower()

def _sort_corp_results(corps: list[dict]) -> list[dict]:
    """상장(stock_code 있음) 우선, 동점 시 modify_date 최신 순"""
    return sorted(
        corps,
        key=lambda c: (0 if c.get("stock_code") else 1, -(int(c.get("modify_date") or "0"))),
    )


class DartClient:
    """OpenDART API 호출 래퍼

    하는 일:
    - API 키를 .env에서 읽어서 자동으로 붙임 (속도 제한 시 보조 키로 자동 전환)
    - 요청 보내고 JSON 파싱
    - 에러 상태(status != "000")면 예외 발생
    - 종목코드/회사명 → corp_code 변환 (corpCode.xml 캐싱)
    """

    def __init__(self):
        self._api_keys = []
        primary = os.getenv("OPENDART_API_KEY")
        if primary:
            self._api_keys.append(primary)
        secondary = os.getenv("OPENDART_API_KEY_2")
        if secondary:
            self._api_keys.append(secondary)
        if not self._api_keys:
            raise ValueError("OPENDART_API_KEY가 .env에 설정되어 있지 않습니다.")
        self._key_index = 0
        self.api_key = self._api_keys[0]
        # Rate limiting — 마지막 요청 시각 추적
        self._last_api_request = 0.0
        self._last_web_request = 0.0
        # Document caching (메모리 + 디스크)
        self._doc_cache: dict[str, dict] = {}
        self._MAX_CACHE = 30
        self._disk_cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "cache")

    def _rotate_key(self) -> bool:
        """다음 API 키로 전환. 전환 가능하면 True, 더 없으면 False."""
        if len(self._api_keys) <= 1:
            return False
        self._key_index = (self._key_index + 1) % len(self._api_keys)
        self.api_key = self._api_keys[self._key_index]
        return True

    async def _request(self, endpoint: str, params: dict) -> dict:
        """공통 API 호출 메서드 (JSON 응답용)

        Args:
            endpoint: API 엔드포인트 (예: "list.json")
            params: 쿼리 파라미터 (api_key는 자동 추가)

        Returns:
            API 응답 JSON (dict)
        """
        await self._throttle_api()
        params["crtfc_key"] = self.api_key
        url = f"{OPENDART_BASE_URL}/{endpoint}"

        async with httpx.AsyncClient() as http:
            response = await http.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

        # DART API는 status "000"이 정상
        status = data.get("status", "")
        if status != "000":
            # 속도 제한("020") 등 일시적 에러 시 보조 키로 재시도
            if self._rotate_key():
                params["crtfc_key"] = self.api_key
                async with httpx.AsyncClient() as http:
                    response = await http.get(url, params=params, timeout=30)
                    response.raise_for_status()
                    data = response.json()
                status = data.get("status", "")
                if status == "000":
                    return data
            message = data.get("message", "알 수 없는 에러")
            raise DartClientError(status, message)

        return data

    async def _request_binary(self, endpoint: str, params: dict) -> bytes:
        """공통 API 호출 메서드 (바이너리 응답용 — ZIP 등)

        비정상 응답(XML 에러) 수신 시:
        1. XML 에러면 DartClientError 발생 (접수번호 오류 등)
        2. ZIP도 XML도 아니면 보조 키로 전환 후 재시도
        """
        await self._throttle_api()
        params["crtfc_key"] = self.api_key
        url = f"{OPENDART_BASE_URL}/{endpoint}"

        async with httpx.AsyncClient() as http:
            response = await http.get(url, params=params, timeout=60)
            response.raise_for_status()

        content = response.content

        # ZIP 파일은 PK 시그니처(50 4B)로 시작
        if content[:2] == b'PK':
            return content

        # XML 에러 응답 체크 (접수번호 오류, 한도 초과 등)
        if content[:5] == b'<?xml':
            import re
            status_m = re.search(r'<status>(\d+)</status>', content.decode('utf-8', errors='replace'))
            msg_m = re.search(r'<message>(.+?)</message>', content.decode('utf-8', errors='replace'))
            if status_m:
                raise DartClientError(status_m.group(1), msg_m.group(1) if msg_m else "알 수 없는 에러")

        # ZIP도 XML도 아닌 비정상 응답 → 보조 키로 재시도
        if self._rotate_key():
            params["crtfc_key"] = self.api_key
            async with httpx.AsyncClient() as http:
                response = await http.get(url, params=params, timeout=60)
                response.raise_for_status()
            content = response.content
            if content[:5] == b'<?xml':
                import re
                status_m = re.search(r'<status>(\d+)</status>', content.decode('utf-8', errors='replace'))
                msg_m = re.search(r'<message>(.+?)</message>', content.decode('utf-8', errors='replace'))
                if status_m:
                    raise DartClientError(status_m.group(1), msg_m.group(1) if msg_m else "알 수 없는 에러")

        return content

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
        """종목코드/회사명/약칭/영문명으로 corp_code 조회 (단일 결과)

        동명 기업이 있을 경우 modify_date 최신 + 상장 기업 우선.
        여러 후보가 필요하면 lookup_corp_code_all() 사용.

        Args:
            query: 종목코드(6자리), corp_code(8자리), 회사명, 약칭, 영문명

        Returns:
            {"corp_code": ..., "corp_name": ..., "stock_code": ..., "modify_date": ...} 또는 None
        """
        results = await self.lookup_corp_code_all(query)
        if not results:
            return None
        return results[0]

    async def lookup_corp_code_all(self, query: str) -> list[dict]:
        """query에 매칭되는 기업 전체 목록 반환 (우선순위 정렬)

        우선순위: 1) 정확매치 > 2) 정규화 매치 > 3) 부분 매치
                  각 단계 내에서: 상장(stock_code 있음) 우선 → modify_date 최신 순
        """
        corps = await self._load_corp_codes()
        query = query.strip()

        # 1) 종목코드 6자리 정확 매치
        if re.match(r'^\d{6}$', query):
            exact = [c for c in corps if c["stock_code"] == query]
            if exact:
                return exact

        # 2) corp_code 8자리 정확 매치
        if re.match(r'^\d{8}$', query):
            exact = [c for c in corps if c["corp_code"] == query]
            if exact:
                return exact

        # 3) 알려진 alias → DART 정식명으로 변환
        q_lower = query.lower()
        if q_lower in _CORP_ALIASES:
            query = _CORP_ALIASES[q_lower]

        # 4) 회사명 정확 매치
        exact = [c for c in corps if c["corp_name"] == query]
        if exact:
            return _sort_corp_results(exact)

        # 5) 정규화 후 정확 매치 (법인격 제거)
        q_norm = _normalize_corp_name(query)
        norm_exact = [c for c in corps if _normalize_corp_name(c["corp_name"]) == q_norm]
        if norm_exact:
            return _sort_corp_results(norm_exact)

        # 6) 부분 매치 (원본 query)
        partial = [c for c in corps if query in c["corp_name"]]
        if partial:
            return _sort_corp_results(partial)

        # 7) 정규화 부분 매치
        norm_partial = [c for c in corps if q_norm in _normalize_corp_name(c["corp_name"])]
        if norm_partial:
            return _sort_corp_results(norm_partial)

        return []

    async def get_naver_corp_profile(self, stock_code: str) -> dict:
        """NAVER 금융에서 업종명 조회 (웹 스크래핑)

        Returns:
            {"sector_name": "반도체와반도체장비", "sector_code": "278"} 또는 {}
        """
        try:
            async with httpx.AsyncClient() as http:
                await asyncio.sleep(2.0)  # 웹 스크래핑 최소 간격
                r = await http.get(
                    f"https://finance.naver.com/item/coinfo.naver?code={stock_code}",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=10,
                )
                # 업종 링크에서 no 추출
                m = re.search(r'sise_group_detail\.naver\?type=upjong&no=(\d+)', r.text)
                if not m:
                    return {}
                sector_code = m.group(1)

                await asyncio.sleep(2.0)
                r2 = await http.get(
                    f"https://finance.naver.com/sise/sise_group_detail.naver?type=upjong&no={sector_code}",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=10,
                )
                # title: "반도체와반도체장비 : Npay 증권"
                m2 = re.search(r'<title>([^:]+)\s*:', r2.text)
                sector_name = m2.group(1).strip() if m2 else ""
                return {"sector_name": sector_name, "sector_code": sector_code}
        except Exception:
            return {}

    # ── 기업 기본정보 ──

    async def get_company_info(self, corp_code: str) -> dict:
        """기업 기본정보 (company.json) — 대표이사, 결산월 등

        Returns:
            {"corp_name": ..., "ceo_nm": ..., "fiscal_month": ..., ...}
        """
        return await self._request("company.json", {"corp_code": corp_code})

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

        return {"text": text, "html": text_html, "images": images}

    # ── Rate Limiting ──

    async def _throttle_api(self):
        """API 요청 간격 강제 (최소 _MIN_INTERVAL_API 초)"""
        elapsed = time.monotonic() - self._last_api_request
        if elapsed < _MIN_INTERVAL_API:
            await asyncio.sleep(_MIN_INTERVAL_API - elapsed)
        self._last_api_request = time.monotonic()

    async def _throttle_web(self):
        """웹 스크래핑 요청 간격 강제 (최소 _MIN_INTERVAL_WEB 초)

        ⚠️ DART 웹사이트는 공식 API가 아닙니다.
        과도한 요청은 IP 차단 또는 법적 문제를 야기할 수 있으므로
        반드시 보수적인 간격을 유지합니다.
        """
        elapsed = time.monotonic() - self._last_web_request
        if elapsed < _MIN_INTERVAL_WEB:
            wait = _MIN_INTERVAL_WEB - elapsed
            logger.debug(f"[DART 웹] {wait:.1f}초 대기 (rate limit)")
            await asyncio.sleep(wait)
        self._last_web_request = time.monotonic()

    # ── DART 웹 스크래핑 (PDF 다운로드용) ──

    async def _fetch_dcm_no(self, rcept_no: str) -> str:
        """DART 웹에서 dcm_no(문서번호)를 추출

        PDF 다운로드에 필요한 dcm_no를 공시 뷰어 페이지의
        JavaScript(makeToc)에서 regex로 추출합니다.

        ⚠️ 웹 스크래핑이므로 rate limit 엄격 적용.
        """
        await self._throttle_web()
        url = f"{DART_WEB_BASE_URL}/dsaf001/main.do?rcpNo={rcept_no}"

        async with httpx.AsyncClient() as http:
            response = await http.get(url, timeout=30, headers={
                "User-Agent": "OpenProxyMCP/1.0 (research; +https://github.com/MarcoYou/open-proxy-mcp)",
            })
            response.raise_for_status()

        html = response.text
        # makeToc() 안의 node1['dcmNo'] = "XXXXXXXX"; 패턴
        m = re.search(r"\['dcmNo'\]\s*=\s*\"(\d+)\"", html)
        if not m:
            raise DartClientError("NO_DCM", f"dcm_no를 찾을 수 없습니다. (rcept_no={rcept_no})")

        dcm_no = m.group(1)
        logger.info(f"[DART 웹] dcm_no={dcm_no} 추출 완료 (rcept_no={rcept_no})")
        return dcm_no

    async def get_document_pdf(self, rcept_no: str) -> bytes:
        """공시 본문 PDF 다운로드

        ⚠️ DART 웹 스크래핑 기반 — 공식 API가 아닙니다.
        - 요청 간격: 최소 2초 (dcm_no 조회 + PDF 다운로드 각각)
        - 배치 사용 금지: 한 번에 1건씩만, 필요할 때만 호출
        - User-Agent에 프로젝트 정보 명시

        Args:
            rcept_no: 접수번호

        Returns:
            PDF 바이너리 (bytes)
        """
        dcm_no = await self._fetch_dcm_no(rcept_no)

        await self._throttle_web()
        url = f"{DART_WEB_BASE_URL}/pdf/download/pdf.do"

        async with httpx.AsyncClient() as http:
            response = await http.get(
                url,
                params={"rcp_no": rcept_no, "dcm_no": dcm_no},
                timeout=60,
                headers={
                    "User-Agent": "OpenProxyMCP/1.0 (research; +https://github.com/MarcoYou/open-proxy-mcp)",
                },
            )
            response.raise_for_status()

        content = response.content

        # PDF 매직 넘버 확인 (%PDF)
        if not content[:4] == b'%PDF':
            raise DartClientError("NO_PDF", f"PDF가 아닌 응답 수신 (rcept_no={rcept_no}, size={len(content)})")

        logger.info(f"[DART 웹] PDF 다운로드 완료: {len(content):,} bytes (rcept_no={rcept_no})")
        return content

    # ── Ownership API (DS002 정기보고서) ──

    async def get_major_shareholders(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """최대주주 현황 (hyslrSttus) — 최대주주+특수관계인 지분

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("hyslrSttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    async def get_major_shareholder_changes(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """최대주주 변동현황 (hyslrChgSttus)

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("hyslrChgSttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    async def get_minority_shareholders(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """소액주주 현황 (mrhlSttus)

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("mrhlSttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    async def get_stock_total(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """주식의 총수 현황 (stockTotqySttus) — 발행총수, 자기주식수, 유통주식수

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("stockTotqySttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    async def get_treasury_stock(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """자기주식 취득 및 처분 현황 (tesstkAcqsDspsSttus) — 기초/취득/처분/소각/기말

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("tesstkAcqsDspsSttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    # ── Ownership API (DS004 수시보고) ──

    async def get_block_holders(self, corp_code: str) -> dict:
        """5% 대량보유 상황보고 (majorstock) — 전체 이력 반환, 날짜 필터 없음

        Args:
            corp_code: DART 기업코드 (8자리)
        """
        return await self._request("majorstock.json", {
            "corp_code": corp_code,
        })

    async def get_executive_holdings(self, corp_code: str) -> dict:
        """임원/주요주주 소유보고 (elestock) — 전체 이력 반환, 대량 데이터 주의

        Args:
            corp_code: DART 기업코드 (8자리)
        """
        return await self._request("elestock.json", {
            "corp_code": corp_code,
        })

    # ── Ownership API (DS005 주요사항보고서) ──

    async def get_treasury_acquisition(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """자기주식 취득 결정 (tsstkAqDecsn)

        Args:
            corp_code: DART 기업코드 (8자리)
            bgn_de: 검색 시작일 (YYYYMMDD)
            end_de: 검색 종료일 (YYYYMMDD)
        """
        return await self._request("tsstkAqDecsn.json", {
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
        })

    async def get_treasury_disposal(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """자기주식 처분 결정 (tsstkDpDecsn)

        Args:
            corp_code: DART 기업코드 (8자리)
            bgn_de: 검색 시작일 (YYYYMMDD)
            end_de: 검색 종료일 (YYYYMMDD)
        """
        return await self._request("tsstkDpDecsn.json", {
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
        })

    async def get_treasury_trust_contract(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """자기주식취득 신탁계약 체결 결정 (tsstkAqTrctrCnsDecsn)

        Args:
            corp_code: DART 기업코드 (8자리)
            bgn_de: 검색 시작일 (YYYYMMDD)
            end_de: 검색 종료일 (YYYYMMDD)
        """
        return await self._request("tsstkAqTrctrCnsDecsn.json", {
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
        })

    async def get_treasury_trust_termination(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """자기주식취득 신탁계약 해지 결정 (tsstkAqTrctrCcDecsn)

        Args:
            corp_code: DART 기업코드 (8자리)
            bgn_de: 검색 시작일 (YYYYMMDD)
            end_de: 검색 종료일 (YYYYMMDD)
        """
        return await self._request("tsstkAqTrctrCcDecsn.json", {
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
        })

    # ── Dividend API (DS002 정기보고서) ──

    async def get_dividend_info(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """배당에 관한 사항 (alotMatter) — 배당금, 배당률, 기준일

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("alotMatter.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    # ── 주가 시세 조회 (네이버 금융 → KRX fallback) ──

    async def get_stock_price(self, stock_code: str, base_date: str) -> dict | None:
        """특정 종목의 일별 시세 (종가). 네이버 금융 우선, KRX Open API fallback.

        Args:
            stock_code: 종목코드 6자리 (예: "005930")
            base_date: 기준일 YYYYMMDD (예: "20260404")

        Returns:
            {"closing_price": int, "base_date": str, "source": str}
            또는 None (데이터 없음)
        """
        # 1차: KRX Open API (공식)
        result = await self._krx_stock_price(stock_code, base_date)
        if result:
            return result

        # 2차: 네이버 금융 (fallback)
        result = await self._naver_stock_price(stock_code, base_date)
        if result:
            return result

        return None

    async def _naver_stock_price(self, stock_code: str, base_date: str) -> dict | None:
        """네이버 금융 시세 API — 일별 종가"""
        try:
            await self._throttle_api()
            url = "https://api.finance.naver.com/siseJson.naver"
            params = {
                "symbol": stock_code,
                "requestType": "1",
                "startTime": base_date,
                "endTime": base_date,
                "timeframe": "day",
            }
            async with httpx.AsyncClient() as http:
                resp = await http.get(url, params=params, timeout=15,
                                       headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    return None

                # 응답 파싱: [["날짜","시가","고가","저가","종가","거래량","외국인소진율"],\n["20251230",119100,121200,118700,119900,...]]
                import re as _re
                rows = _re.findall(r'\["(\d{8})",\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)', resp.text)
                if rows:
                    date_str, open_p, high, low, close = rows[0]
                    return {
                        "closing_price": int(close),
                        "base_date": date_str,
                        "source": "naver",
                    }

                # 해당 날짜 데이터 없으면 (비거래일) — 범위 넓혀서 직전 거래일
                start = str(int(base_date) - 7)  # 7일 전부터
                params["startTime"] = start
                resp2 = await http.get(url, params=params, timeout=15,
                                        headers={"User-Agent": "Mozilla/5.0"})
                rows2 = _re.findall(r'\["(\d{8})",\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)', resp2.text)
                if rows2:
                    # 마지막 행이 가장 최근
                    date_str, open_p, high, low, close = rows2[-1]
                    return {
                        "closing_price": int(close),
                        "base_date": date_str,
                        "source": "naver",
                    }
                return None
        except Exception as e:
            logger.warning(f"[네이버] 시세 조회 실패: {e}")
            return None

    async def _krx_stock_price(self, stock_code: str, base_date: str) -> dict | None:
        """KRX Open API — 일별 시세 (서비스 승인 필요)"""
        import os
        api_key = os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY")
        if not api_key:
            return None

        try:
            await self._throttle_api()
            url = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
            params = {"AUTH_KEY": api_key, "basDd": base_date}
            async with httpx.AsyncClient() as http:
                resp = await http.get(url, params=params, timeout=30)
                if resp.status_code != 200:
                    return None
                data = resp.json()
                for item in data.get("OutBlock_1", []):
                    isu_cd = item.get("ISU_CD", "")
                    if isu_cd == stock_code or stock_code in isu_cd:
                        return {
                            "closing_price": int(str(item.get("TDD_CLSPRC", "0")).replace(",", "") or "0"),
                            "base_date": item.get("BAS_DD", base_date),
                            "source": "krx",
                        }
                return None
        except Exception as e:
            logger.warning(f"[KRX] 시세 조회 실패: {e}")
            return None

    # ── 네이버 뉴스 검색 API ──

    async def naver_news_search(self, query: str, display: int = 100, sort: str = "date") -> list[dict]:
        """네이버 뉴스 검색 API

        Args:
            query: 검색어 (예: '"김용관" "삼성전자"')
            display: 결과 수 (최대 100)
            sort: "date" (최신순) 또는 "sim" (정확도순)

        Returns:
            [{"title", "link", "originallink", "description", "pubDate"}, ...]
        """
        import os
        client_id = os.getenv("NAVER_SEARCH_API_CLIENT_ID")
        client_secret = os.getenv("NAVER_SEARCH_API_CLIENT_SECRET")
        if not client_id or not client_secret:
            logger.warning("[네이버] 검색 API 키가 설정되지 않았습니다")
            return []

        await self._throttle_api()
        url = "https://openapi.naver.com/v1/search/news.json"
        params = {"query": query, "display": display, "sort": sort}
        headers = {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        }

        try:
            async with httpx.AsyncClient() as http:
                resp = await http.get(url, params=params, headers=headers, timeout=15)
                if resp.status_code != 200:
                    logger.warning(f"[네이버] HTTP {resp.status_code}: {resp.text[:200]}")
                    return []
                data = resp.json()
                return data.get("items", [])
        except Exception as e:
            logger.warning(f"[네이버] 뉴스 검색 실패: {e}")
            return []

    # ── KRX KIND 크롤링 ──

    async def _throttle_kind(self):
        """KIND 웹 요청 간격 강제 (1-3초 랜덤)"""
        import random
        elapsed = time.monotonic() - self._last_web_request
        wait = random.uniform(1.0, 3.0)
        if elapsed < wait:
            await asyncio.sleep(wait - elapsed)
        self._last_web_request = time.monotonic()

    async def kind_fetch_document(self, acptno: str) -> str:
        """KIND에서 공시 본문 HTML 가져오기 (3단계 iframe 크롤링)

        1. 메인 페이지에서 docNo 추출
        2. searchContents에서 본문 URL 추출
        3. 본문 HTML 다운로드

        Args:
            acptno: 접수번호 (예: "20260130000495")

        Returns:
            본문 HTML 텍스트
        """
        kind_base = "https://kind.krx.co.kr"
        headers = {
            "User-Agent": "OpenProxyMCP/1.0 (research; +https://github.com/MarcoYou/open-proxy-mcp)",
        }

        # Step 1: 메인 페이지 → docNo 추출
        await self._throttle_kind()
        url1 = f"{kind_base}/common/disclsviewer.do"
        async with httpx.AsyncClient() as http:
            resp1 = await http.get(url1, params={
                "method": "search", "acptno": acptno,
            }, timeout=30, headers=headers)
            resp1.raise_for_status()

        # <select id="mainDoc"> 안의 <option value="docNo|Y">
        m = re.search(r"<option[^>]+value=['\"](\d+)\|?[^'\"]*['\"]", resp1.text)
        if not m:
            raise DartClientError("KIND_NO_DOC", f"KIND에서 docNo를 찾을 수 없습니다 (acptno={acptno})")
        doc_no = m.group(1)

        # Step 2: searchContents → 본문 URL 추출
        await self._throttle_kind()
        async with httpx.AsyncClient() as http:
            resp2 = await http.get(url1, params={
                "method": "searchContents", "docNo": doc_no,
            }, timeout=30, headers=headers)
            resp2.raise_for_status()

        # setPath('목차URL', '본문URL') — 두 번째 인자가 본문 (목차가 빈 문자열일 수 있음)
        m2 = re.search(r"setPath\s*\(\s*'([^']*)'\s*,\s*'([^']+)'", resp2.text)
        if not m2:
            raise DartClientError("KIND_NO_PATH", f"KIND에서 본문 URL을 찾을 수 없습니다 (docNo={doc_no})")
        body_path = m2.group(2)

        # Step 3: 본문 HTML 다운로드
        await self._throttle_kind()
        body_url = f"{kind_base}{body_path}" if body_path.startswith("/") else body_path
        async with httpx.AsyncClient() as http:
            resp3 = await http.get(body_url, timeout=30, headers=headers)
            resp3.raise_for_status()

        logger.info(f"[KIND] 본문 다운로드 완료: {len(resp3.text):,} chars (acptno={acptno})")
        return resp3.text

    # ── Document Caching ──

    def _disk_cache_path(self, rcept_no: str) -> str:
        return os.path.join(self._disk_cache_dir, f"{rcept_no}.json")

    def _load_from_disk(self, rcept_no: str) -> dict | None:
        path = self._disk_cache_path(rcept_no)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def _save_to_disk(self, rcept_no: str, doc: dict):
        os.makedirs(self._disk_cache_dir, exist_ok=True)
        path = self._disk_cache_path(rcept_no)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)

    async def get_document_cached(self, rcept_no: str) -> dict:
        """get_document 결과를 캐싱 (메모리 + 디스크). 중복 API 호출 방지."""
        if rcept_no in self._doc_cache:
            return self._doc_cache[rcept_no]
        disk_doc = self._load_from_disk(rcept_no)
        if disk_doc:
            if len(self._doc_cache) >= self._MAX_CACHE:
                self._doc_cache.pop(next(iter(self._doc_cache)))
            self._doc_cache[rcept_no] = disk_doc
            return disk_doc
        doc = await self.get_document(rcept_no)
        if len(self._doc_cache) >= self._MAX_CACHE:
            self._doc_cache.pop(next(iter(self._doc_cache)))
        self._doc_cache[rcept_no] = doc
        self._save_to_disk(rcept_no, doc)
        # 이미지 기반 공고 감지
        images = doc.get("images", [])
        notice_images = [img for img in images if any(
            kw in img for kw in ["소집", "통지", "주총", "공고"]
        )]
        if notice_images:
            logger.warning(
                f"[IMAGE_NOTICE] 소집공고 본문이 이미지에 포함된 것으로 추정: "
                f"{rcept_no} | images={notice_images}"
            )
        return doc


# ── Singleton ──

_instance: "DartClient | None" = None

def get_dart_client() -> DartClient:
    """DartClient 싱글턴 — 전 tool에서 throttle 공유"""
    global _instance
    if _instance is None:
        _instance = DartClient()
    return _instance
