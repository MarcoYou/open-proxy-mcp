"""공시군별 제목 타깃 검색 helper."""

from __future__ import annotations

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
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    """기간 내 공시를 제목 기준으로 타깃 검색.

    DART list.json은 제목 직접 검색이 약하므로, 기간/공시유형으로 조회한 뒤
    제목(report_nm) 필터를 적용한다. 페이지는 무한정 넘기지 않고 max_pages까지만 본다.
    """

    client = get_dart_client()
    notices: list[str] = []
    matched: list[dict[str, Any]] = []
    ptypes = [pblntf_tys] if isinstance(pblntf_tys, str) else list(pblntf_tys)
    keyword_label = ", ".join(keywords)

    for pblntf_ty in ptypes:
        try:
            first = await client.search_filings(
                corp_code=corp_code,
                bgn_de=bgn_de,
                end_de=end_de,
                pblntf_ty=pblntf_ty,
                page_no=1,
                page_count=page_count,
            )
        except DartClientError as exc:
            return matched, notices, exc.status

        items = list(first.get("list", []))
        total_count = int(first.get("total_count", len(items)) or 0)
        total_pages = max(1, math.ceil(total_count / page_count)) if total_count else 1
        fetched_pages = min(total_pages, max_pages)

        for page_no in range(2, fetched_pages + 1):
            try:
                page = await client.search_filings(
                    corp_code=corp_code,
                    bgn_de=bgn_de,
                    end_de=end_de,
                    pblntf_ty=pblntf_ty,
                    page_no=page_no,
                    page_count=page_count,
                )
            except DartClientError as exc:
                return matched, notices, exc.status
            items.extend(page.get("list", []))

        if total_pages > max_pages:
            notices.append(
                f"{bgn_de}~{end_de} 기간의 {pblntf_ty} 공시 중 '{keyword_label}' 제목군은 "
                f"{total_pages}페이지가 있었지만 {max_pages}페이지까지만 확인했다."
            )

        matched.extend(
            item for item in items
            if report_name_matches(item, keywords, strip_spaces=strip_spaces)
        )

    matched.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)
    return matched, notices, None
