"""공시군별 제목 타깃 검색 helper."""

from __future__ import annotations

import asyncio
import math
from typing import Any, Iterable

from open_proxy_mcp.dart.client import DartClientError, get_dart_client


def report_name_matches(
    item: dict[str, Any],
    keywords: Iterable[str],
    *,
    strip_spaces: bool = False,
) -> bool:
    report_name = item.get("report_nm") or ""
    haystack = report_name.replace(" ", "") if strip_spaces else report_name
    return any(keyword in haystack for keyword in keywords)


async def search_filings_by_report_name(
    *,
    corp_code: str,
    bgn_de: str,
    end_de: str,
    pblntf_tys: str | Iterable[str],
    keywords: Iterable[str],
    strip_spaces: bool = False,
    max_pages: int = 10,
    page_count: int = 100,
    last_reprt_at: str = "",
    pblntf_detail_ty: str | Iterable[str] = "",
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    """기간 내 공시를 제목 기준으로 타깃 검색.

    DART list.json은 제목 직접 검색이 약하므로, 기간/공시유형으로 조회한 뒤
    제목(report_nm) 필터를 적용한다. 페이지는 무한정 넘기지 않고 max_pages까지만 본다.

    pblntf_detail_ty를 지정하면 서버단에서 상세유형으로 좁혀 page/호출 수를 줄인다
    (예: 배당 공시는 I001=주요경영사항 하위 → "I" 전체 대신 "I001"만 받으면 됨).

    last_reprt_at='Y'를 넘기면 정정공시 자동 정리 (최종본만). caller가 원본+정정 모두
    필요하면 ""로 둔다 (default).
    """

    items, notices, error = await fetch_filings_for_title_scan(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys=pblntf_tys,
        keyword_label=", ".join(keywords),
        max_pages=max_pages,
        page_count=page_count,
        last_reprt_at=last_reprt_at,
        pblntf_detail_ty=pblntf_detail_ty,
    )
    if error:
        return [], notices, error

    matched = [
        item for item in items
        if report_name_matches(item, keywords, strip_spaces=strip_spaces)
    ]

    matched.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)
    return matched, notices, None


async def fetch_filings_for_title_scan(
    *,
    corp_code: str,
    bgn_de: str,
    end_de: str,
    pblntf_tys: str | Iterable[str],
    keyword_label: str,
    max_pages: int = 10,
    page_count: int = 100,
    last_reprt_at: str = "",
    pblntf_detail_ty: str | Iterable[str] = "",
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    """기간/공시유형 기준 list.json fetch를 한 번 수행해 caller-side 제목 필터에 재사용."""

    client = get_dart_client()
    notices: list[str] = []
    items: list[dict[str, Any]] = []
    ptypes = [pblntf_tys] if isinstance(pblntf_tys, str) else list(pblntf_tys)
    detail_codes = [pblntf_detail_ty] if isinstance(pblntf_detail_ty, str) else list(pblntf_detail_ty)
    detail_codes = [d for d in detail_codes if d]
    # detail 코드가 있으면 **코드별** 스캔(pblntf_ty=""), 없으면 공시유형별 스캔.
    # detail로 좁히면 유형 전체 페이지(max_pages 컷)를 받지 않아 정확·고속.
    targets = [("", d) for d in detail_codes] if detail_codes else [(t, "") for t in ptypes]

    for pblntf_ty, detail_ty in targets:
        try:
            first = await client.search_filings(
                corp_code=corp_code,
                bgn_de=bgn_de,
                end_de=end_de,
                pblntf_ty=pblntf_ty,
                pblntf_detail_ty=detail_ty,
                page_no=1,
                page_count=page_count,
                last_reprt_at=last_reprt_at,
            )
        except DartClientError as exc:
            # 013 = 이 (유형/코드)엔 해당 공시 없음 → 다음 target으로 (멀티코드 스캔이
            # 한 코드의 no-data로 통째 중단되지 않게). 그 외 에러만 전파.
            if exc.status == "013":
                continue
            return items, notices, exc.status

        page_items = list(first.get("list", []))
        total_count = int(first.get("total_count", len(page_items)) or 0)
        total_pages = max(1, math.ceil(total_count / page_count)) if total_count else 1
        fetched_pages = min(total_pages, max_pages)

        async def fetch_page(page_no: int) -> tuple[list[dict[str, Any]], str | None]:
            try:
                page = await client.search_filings(
                    corp_code=corp_code,
                    bgn_de=bgn_de,
                    end_de=end_de,
                    pblntf_ty=pblntf_ty,
                    pblntf_detail_ty=detail_ty,
                    page_no=page_no,
                    page_count=page_count,
                    last_reprt_at=last_reprt_at,
                )
            except DartClientError as exc:
                return [], exc.status
            return list(page.get("list", [])), None

        page_results = await asyncio.gather(*[
            fetch_page(page_no) for page_no in range(2, fetched_pages + 1)
        ]) if fetched_pages >= 2 else []
        for page_rows, error in page_results:
            if error:
                return items, notices, error
            page_items.extend(page_rows)

        if total_pages > max_pages:
            notices.append(
                f"{bgn_de}~{end_de} 기간의 {pblntf_ty or detail_ty} 공시 중 '{keyword_label}' 제목군은 "
                f"{total_pages}페이지가 있었지만 {max_pages}페이지까지만 확인했다."
            )

        items.extend(page_items)

    items.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)
    return items, notices, None


async def find_withdrawal_notices(
    corp_code: str, bgn_de: str, end_de: str, topic_words: Iterable[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """회사·구간의 거래소 공시(I001) 중 제목에 「철회」와 주제 낱말이 든 것 — `(공시 목록, 경고)`. list.json 1콜.

    결정의 철회는 주요사항보고서가 아니라 「기타주요경영사항(… 철회)」·「[기재정정]…(철회)」로 온다. 결정이 철회되면 DART 구조화
    자료(DS005)에서 그 결정이 보이지 않아, 결정 도구는 「공시 없음 (정상)」이라고만 답했다(빅텐츠 유상증자·전환사채 2026-11-10 철회,
    대진첨단소재 합병 2026-10-01 철회). 제목·날짜·접수번호만 싣는다 — **어느 결정의 철회인지는 잇지 않는다**(원문을 읽어 확인).
    """
    try:
        filings, notices, error = await search_filings_by_report_name(
            corp_code=corp_code, bgn_de=bgn_de, end_de=end_de, pblntf_tys="", pblntf_detail_ty="I001",
            keywords=("철회",), strip_spaces=True, last_reprt_at="N")
    except DartClientError as exc:                      # 이 조회가 실패해도 결정 도구 본문은 그대로 나가야 한다
        filings, notices, error = [], [], exc.status
    if error and error != "013":
        return [], [*notices, f"철회 공시 조회 실패: {error} — 철회 여부를 확인하지 못했다"]
    words = tuple(topic_words)
    out = []
    for f in filings or []:
        title = " ".join((f.get("report_nm") or "").split())
        if any(w in title.replace(" ", "") for w in words):
            out.append({"rcept_no": f.get("rcept_no", ""), "rcept_dt": f.get("rcept_dt", ""), "report_nm": title,
                        "viewer_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={f.get('rcept_no', '')}"})
    return out, list(notices)
