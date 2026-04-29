"""국민연금기금운용본부 (NPS) 의결권 행사내역 크롤링 클라이언트.

엔드포인트
- list  : POST https://fund.nps.or.kr/impa/edwmpblnt/empty/getOHEF0007M0.do (HTML body 반환)
- detail: POST https://fund.nps.or.kr/impa/edwmpblnt/getOHEF0010M0.do      (HTML body 반환)

쿠키
- 첫 GET https://fund.nps.or.kr/impa/edwmpblnt/getOHEF0007M0.do 호출로 WMONID + EOH_JSESSIONID 확보
- httpx.AsyncClient 세션이 자동으로 보존

핵심 매핑
- NPS 종목코드 5자리 + "0" = KRX 6자리 티커 (검증 100%, 두산 00015 → 000150)

부하 가이드
- 페이지 사이 1초, detail 사이 2초 sleep (예의)
- 사이트 변경 대비 BeautifulSoup 파싱 try/except 방어
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


NPS_BASE = "https://fund.nps.or.kr"
NPS_INIT_URL = f"{NPS_BASE}/impa/edwmpblnt/getOHEF0007M0.do"
NPS_LIST_URL = f"{NPS_BASE}/impa/edwmpblnt/empty/getOHEF0007M0.do"
NPS_DETAIL_URL = f"{NPS_BASE}/impa/edwmpblnt/getOHEF0010M0.do"

_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 페이지 간 / 상세 간 sleep (사이트 부하 고려)
_PAGE_SLEEP_SEC = 1.0
_DETAIL_SLEEP_SEC = 2.0

# fnc_goDetail('','0095000','00015','20260331','1') 인자 추출
_GODETAIL_RE = re.compile(
    r"fnc_goDetail\(\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*\)"
)


def nps_code_to_ticker(nps_code: str) -> str:
    """NPS 5자리 코드 → KRX 6자리 티커 (단순 + '0')."""
    if not nps_code:
        return ""
    code = str(nps_code).strip().zfill(5)
    return code + "0"


def normalize_decision(text: str) -> str:
    """'찬성' → for, '반대' → against, '중립/기권' → abstain, '불행사' → not_voted."""
    if not text:
        return ""
    s = text.strip().replace(" ", "")
    if "찬성" in s:
        return "for"
    if "반대" in s:
        return "against"
    if "중립" in s or "기권" in s:
        return "abstain"
    if "불행사" in s or "미행사" in s:
        return "not_voted"
    return s


def normalize_kind(code: str) -> str:
    """'1' → annual, '2' → extra. 텍스트도 허용."""
    s = str(code or "").strip()
    if s in {"1", "정기주총", "정기"}:
        return "annual"
    if s in {"2", "임시주총", "임시"}:
        return "extra"
    return s


@dataclass
class NPSListRow:
    nps_code: str
    company_name: str
    gmos_date: str        # YYYY-MM-DD (테이블 표기)
    gmos_ymd: str         # YYYYMMDD (detail 호출용)
    gmos_kind_label: str  # "정기주총" / "임시주총"
    gmos_kind_cd: str     # "1" / "2"
    edwm_vtrt_use_sn: str
    data_pvsn_inst_cd_vl: str
    ticker: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "nps_code": self.nps_code,
            "company_name": self.company_name,
            "gmos_date": self.gmos_date,
            "gmos_ymd": self.gmos_ymd,
            "gmos_kind_label": self.gmos_kind_label,
            "gmos_kind_cd": self.gmos_kind_cd,
            "gmos_kind": normalize_kind(self.gmos_kind_cd),
            "edwm_vtrt_use_sn": self.edwm_vtrt_use_sn,
            "data_pvsn_inst_cd_vl": self.data_pvsn_inst_cd_vl,
            "ticker": self.ticker,
        }


@dataclass
class NPSDetailItem:
    agenda_no: str
    agenda_title: str
    decision_label: str
    decision: str         # for / against / abstain / not_voted
    against_reason: str
    rule_clause: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "agenda_no": self.agenda_no,
            "agenda_title": self.agenda_title,
            "decision_label": self.decision_label,
            "decision": self.decision,
            "against_reason": self.against_reason,
            "rule_clause": self.rule_clause,
        }


class NPSClientError(Exception):
    """NPS 크롤링 에러."""


class NPSClient:
    """국민연금 의결권 행사내역 크롤러 (httpx async)."""

    def __init__(
        self,
        timeout: float = 30.0,
        user_agent: str = _DEFAULT_UA,
        page_sleep_sec: float = _PAGE_SLEEP_SEC,
        detail_sleep_sec: float = _DETAIL_SLEEP_SEC,
    ):
        self._timeout = timeout
        self._user_agent = user_agent
        self._page_sleep = page_sleep_sec
        self._detail_sleep = detail_sleep_sec
        self._client: httpx.AsyncClient | None = None
        self._init_lock = asyncio.Lock()

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()

    async def aclose(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _ensure_session(self):
        async with self._init_lock:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    timeout=self._timeout,
                    follow_redirects=True,
                    headers={"User-Agent": self._user_agent},
                )
                resp = await self._client.get(NPS_INIT_URL)
                if resp.status_code != 200:
                    raise NPSClientError(
                        f"NPS init GET 실패: status={resp.status_code}"
                    )

    # ── List endpoint ──

    async def search_voting(
        self,
        start_date: str,
        end_date: str,
        company_name: str = "",
        max_pages: int = 200,
    ) -> list[dict[str, Any]]:
        """국내 의결권 행사내역 list (전체 페이지 순회).

        Args:
            start_date: "YYYY-MM-DD"
            end_date:   "YYYY-MM-DD"
            company_name: 회사명 부분일치 (빈값=전체)
            max_pages: 안전 가드 (기본 200)

        Returns:
            list[dict] — to_dict() 결과
        """
        await self._ensure_session()
        out: list[NPSListRow] = []
        seen_keys: set[tuple] = set()
        page = 1
        empty_pages = 0

        while page <= max_pages:
            payload = {
                "pageIndex": page,
                "issueInsNm": company_name or "",
                "gmosStartDt": start_date,
                "gmosEndDt": end_date,
            }
            try:
                resp = await self._client.post(  # type: ignore[union-attr]
                    NPS_LIST_URL,
                    json=payload,
                    headers={
                        "Content-Type": "application/json;",
                        "Accept": "application/json, text/html, */*",
                        "X-Requested-With": "XMLHttpRequest",
                        "Origin": NPS_BASE,
                        "Referer": NPS_INIT_URL,
                    },
                )
            except httpx.HTTPError as e:
                logger.warning("NPS list page=%s 요청 실패: %s", page, e)
                break

            if resp.status_code != 200:
                logger.warning(
                    "NPS list page=%s status=%s — 종료", page, resp.status_code
                )
                break

            rows = self._parse_list_html(resp.text)
            if not rows:
                empty_pages += 1
                # 빈 페이지 2회 연속이면 종료
                if empty_pages >= 2:
                    break
                page += 1
                await asyncio.sleep(self._page_sleep)
                continue
            empty_pages = 0

            new_rows = 0
            for row in rows:
                key = (row.nps_code, row.gmos_ymd, row.gmos_kind_cd)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                out.append(row)
                new_rows += 1

            # 새 row 0건 → 마지막 페이지 (NPS는 마지막 페이지를 넘어가도 같은 데이터를 반환할 수 있음)
            if new_rows == 0:
                break

            # 한 페이지 < 10건이면 보통 마지막 페이지
            if len(rows) < 10:
                break

            page += 1
            await asyncio.sleep(self._page_sleep)

        return [r.to_dict() for r in out]

    def _parse_list_html(self, html: str) -> list[NPSListRow]:
        """list 응답 HTML → NPSListRow list."""
        soup = BeautifulSoup(html, "lxml")
        rows: list[NPSListRow] = []
        for tr in soup.select("tbody tr"):
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue
            try:
                a = tds[1].find("a")
                if not a:
                    continue
                onclick = a.get("href", "") or a.get("onclick", "") or ""
                m = _GODETAIL_RE.search(onclick)
                if not m:
                    continue
                edwm_sn, data_pvsn, nps_code, gmos_ymd, kind_cd = m.groups()
                # 회사명, 표기 날짜, kind label은 td 내용에서 보강
                company = a.get_text(strip=True)
                table_code = tds[2].get_text(strip=True)
                table_date = tds[3].get_text(strip=True).replace("/", "-")
                table_kind = tds[4].get_text(strip=True)
                rows.append(
                    NPSListRow(
                        nps_code=nps_code or table_code,
                        company_name=company,
                        gmos_date=table_date,
                        gmos_ymd=gmos_ymd,
                        gmos_kind_label=table_kind,
                        gmos_kind_cd=kind_cd,
                        edwm_vtrt_use_sn=edwm_sn,
                        data_pvsn_inst_cd_vl=data_pvsn or "0095000",
                        ticker=nps_code_to_ticker(nps_code or table_code),
                    )
                )
            except Exception as e:  # 한 줄 실패는 무시
                logger.debug("NPS list row 파싱 실패: %s", e)
                continue
        return rows

    # ── Detail endpoint ──

    async def get_voting_detail(
        self,
        nps_code: str,
        gmos_ymd: str,
        gmos_kind_cd: str = "1",
        edwm_vtrt_use_sn: str = "",
        data_pvsn_inst_cd_vl: str = "0095000",
    ) -> dict[str, Any]:
        """특정 회사·주총 안건별 의결권 행사 상세."""
        await self._ensure_session()
        nps_code = str(nps_code).strip().zfill(5)
        gmos_ymd = str(gmos_ymd).strip().replace("-", "").replace("/", "")
        kind_cd = str(gmos_kind_cd).strip() or "1"

        payload = {
            "edwmVtrtUseSn": edwm_vtrt_use_sn or "",
            "dataPvsnInstCdVl": data_pvsn_inst_cd_vl or "0095000",
            "pblcnInstCdVl": nps_code,
            "gmosYmd": gmos_ymd,
            "gmosKindCd": kind_cd,
            "issueInsNm": "",
            "gmosStartDt": "",
            "gmosEndDt": "",
        }
        try:
            resp = await self._client.post(  # type: ignore[union-attr]
                NPS_DETAIL_URL,
                data=payload,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Origin": NPS_BASE,
                    "Referer": NPS_INIT_URL,
                },
            )
        except httpx.HTTPError as e:
            raise NPSClientError(f"NPS detail 요청 실패: {e}") from e

        if resp.status_code != 200:
            raise NPSClientError(
                f"NPS detail status={resp.status_code} (nps_code={nps_code}, gmos={gmos_ymd})"
            )

        parsed = self._parse_detail_html(resp.text)
        ticker = nps_code_to_ticker(nps_code)
        return {
            "nps_code": nps_code,
            "ticker": ticker,
            "gmos_ymd": gmos_ymd,
            "gmos_date": (
                f"{gmos_ymd[:4]}-{gmos_ymd[4:6]}-{gmos_ymd[6:8]}" if len(gmos_ymd) == 8 else gmos_ymd
            ),
            "gmos_kind_cd": kind_cd,
            "gmos_kind": normalize_kind(kind_cd),
            "company_name": parsed.get("company_name", ""),
            "items": [it.to_dict() for it in parsed.get("items", [])],
            "summary": self._summarize(parsed.get("items", [])),
        }

    def _parse_detail_html(self, html: str) -> dict[str, Any]:
        """detail HTML → {company_name, items: [...]}.

        - id="forRowspan" 테이블에서 회사명/코드/일자/구분 추출
        - 다음 일반 table에서 [의안번호 / 의안내용 / 행사내용 / 반대시 사유 / 근거조항] 추출
        - data-list 영역에 같은 표가 두 번 등장(헤더+본문) — 첫 번째 본문만 사용
        """
        soup = BeautifulSoup(html, "lxml")

        company_name = ""
        try:
            header = soup.find("table", id="forRowspan")
            if header:
                th = header.find("th", attrs={"scope": "row"})
                if th:
                    company_name = th.get_text(strip=True)
        except Exception:
            pass

        items: list[NPSDetailItem] = []
        seen_keys: set[tuple] = set()
        for table in soup.find_all("table"):
            if table.get("id") == "forRowspan":
                continue
            thead = table.find("thead")
            if not thead:
                continue
            head_text = thead.get_text(" ", strip=True)
            if "의안번호" not in head_text or "행사내용" not in head_text:
                continue
            for tr in table.select("tbody tr"):
                tds = tr.find_all("td")
                if len(tds) < 5:
                    continue
                try:
                    agenda_no = tds[0].get_text(strip=True)
                    agenda_title = tds[1].get_text(strip=True)
                    decision_label = tds[2].get_text(strip=True)
                    against_reason = tds[3].get_text(strip=True)
                    rule_clause = tds[4].get_text(strip=True)
                    key = (agenda_no, agenda_title, decision_label)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    items.append(
                        NPSDetailItem(
                            agenda_no=agenda_no,
                            agenda_title=agenda_title,
                            decision_label=decision_label,
                            decision=normalize_decision(decision_label),
                            against_reason=against_reason,
                            rule_clause=rule_clause,
                        )
                    )
                except Exception as e:
                    logger.debug("NPS detail row 파싱 실패: %s", e)
                    continue
            # 동일 표 두 번 패턴이므로 첫 번째 의안 테이블만 사용
            if items:
                break

        return {"company_name": company_name, "items": items}

    @staticmethod
    def _summarize(items: list[NPSDetailItem]) -> dict[str, int]:
        from collections import Counter

        counter = Counter(it.decision for it in items)
        return {
            "total": len(items),
            "for": counter.get("for", 0),
            "against": counter.get("against", 0),
            "abstain": counter.get("abstain", 0),
            "not_voted": counter.get("not_voted", 0),
        }

    # ── 편의 메서드 ──

    async def fetch_with_detail(
        self,
        start_date: str,
        end_date: str,
        company_name: str = "",
        detail_sleep_override: float | None = None,
    ) -> list[dict[str, Any]]:
        """list 전체 + detail까지 모두 수집 (sync 스크립트용)."""
        rows = await self.search_voting(start_date, end_date, company_name)
        sleep_sec = (
            detail_sleep_override if detail_sleep_override is not None else self._detail_sleep
        )
        out = []
        for r in rows:
            try:
                detail = await self.get_voting_detail(
                    nps_code=r["nps_code"],
                    gmos_ymd=r["gmos_ymd"],
                    gmos_kind_cd=r["gmos_kind_cd"],
                    edwm_vtrt_use_sn=r.get("edwm_vtrt_use_sn", ""),
                    data_pvsn_inst_cd_vl=r.get("data_pvsn_inst_cd_vl", "0095000"),
                )
            except NPSClientError as e:
                logger.warning("NPS detail 실패: %s — %s", r.get("company_name"), e)
                detail = {"items": [], "error": str(e)}
            out.append({**r, **detail})
            await asyncio.sleep(sleep_sec)
        return out
