"""뉴스 검색 MCP tools (news_*)

이사/감사 후보자의 부정 뉴스를 검색하여 의결권 행사 판단에 참고.
네이버 뉴스 검색 API 기반. 경력 기반 멀티 검색 + 부정 키워드 필터링.
"""

import json
import os
import re
import logging
from datetime import datetime, timedelta

from open_proxy_mcp.dart.client import get_dart_client, DartClientError
from open_proxy_mcp.tools.errors import tool_error, tool_not_found

logger = logging.getLogger(__name__)

# 부정 키워드
_NEGATIVE_KEYWORDS = [
    "횡령", "배임", "기소", "구속", "벌금", "과징금", "사기", "분식", "불법", "탈세",
    "성범죄", "폭행", "징계", "해임", "사퇴", "갑질", "부당", "비리", "수사", "검찰",
    "경찰", "고발", "소송", "제재", "과태료", "위반", "부정", "조작", "졸속", "전관",
    "결격", "훼손", "미흡",
]

# 주요 일간지 도메인
_MAJOR_PRESS = {
    "hankyung.com": "한국경제",
    "mk.co.kr": "매일경제",
    "chosun.com": "조선일보",
    "joongang.co.kr": "중앙일보",
    "donga.com": "동아일보",
    "sedaily.com": "서울경제",
    "hankookilbo.com": "한국일보",
    "fnnews.com": "파이낸셜뉴스",
    "edaily.co.kr": "이데일리",
    "mt.co.kr": "머니투데이",
    "yna.co.kr": "연합뉴스",
}


def _strip_html(text: str) -> str:
    """HTML 태그 제거"""
    return re.sub(r'<[^>]+>', '', text).strip()


def _parse_pub_date(date_str: str) -> datetime | None:
    """네이버 pubDate 파싱 (예: 'Mon, 07 Apr 2026 09:00:00 +0900')"""
    try:
        # Remove timezone offset for simpler parsing
        clean = re.sub(r'\s*\+\d{4}$', '', date_str.strip())
        for fmt in ["%a, %d %b %Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]:
            try:
                return datetime.strptime(clean, fmt)
            except ValueError:
                continue
        return None
    except Exception:
        return None


def _get_press_name(url: str) -> str:
    """URL에서 매체명 추출"""
    for domain, name in _MAJOR_PRESS.items():
        if domain in url:
            return name
    return "기타"


def _dedupe_articles(articles: list[dict]) -> list[dict]:
    """중복 기사 제거 (originallink 기준)"""
    seen = set()
    result = []
    for a in articles:
        key = a.get("originallink", a.get("link", ""))
        # URL에서 쿼리스트링 제거하여 비교
        key_clean = re.sub(r'\?.*$', '', key)
        if key_clean not in seen:
            seen.add(key_clean)
            result.append(a)
    return result


def _find_negative_keywords(text: str) -> list[str]:
    """텍스트에서 부정 키워드 찾기"""
    found = []
    for kw in _NEGATIVE_KEYWORDS:
        if kw in text:
            found.append(kw)
    return found


def register_tools(mcp):
    """news MCP tools 등록"""

    @mcp.tool()
    async def news_check(
        name: str,
        company: str = "",
        companies: str = "",
        years: int = 5,
        format: str = "md",
    ) -> str:
        """desc: 인물 부정 뉴스 검색. 이사/감사 후보자의 횡령, 배임, 기소 등 부정 뉴스 확인.
        when: [tier-5 Detail] agm_personnel_xml 실행 후 특정 후보자의 부정 뉴스 리스크를 확인할 때만 사용. 단독 호출하지 말 것.
        rule: 경력 기반 멀티 검색 (이름+현재 회사 + 이름+전직 회사). 최근 5년 기본, 더 넓은 기간 가능. 주요 11개 일간지 우선 표시. 부정 키워드 매칭 기사만 필터.
        ref: agm_personnel_xml, agm_manual

        Args:
            name: 인물 이름 (예: "김용관")
            company: 현재 소속 회사명 (예: "삼성전자")
            companies: 추가 검색할 회사명 (쉼표 구분, 경력에서 추출. 예: "LG전자,LX인터내셔널")
            years: 검색 기간 (기본 5년)
            format: "md" (기본) 또는 "json"
        """
        client = get_dart_client()

        # 검색할 회사 목록 구성 (중복 제거)
        search_companies = set()
        if company:
            search_companies.add(company)
        if companies:
            for c in companies.split(","):
                c = c.strip()
                if c:
                    search_companies.add(c)

        if not search_companies:
            search_companies.add("")  # 회사명 없이 이름만 검색

        # 기간 필터
        cutoff = datetime.now() - timedelta(days=years * 365)

        # 멀티 검색 수행
        all_articles = []
        queries_used = []

        for comp in search_companies:
            if comp:
                query = f'"{name}" "{comp}"'
            else:
                query = f'"{name}"'
            queries_used.append(query)

            results = await client.naver_news_search(query, display=100, sort="date")
            for item in results:
                item["_query_company"] = comp
            all_articles.extend(results)

        # 중복 제거
        all_articles = _dedupe_articles(all_articles)

        # 기간 필터링
        filtered = []
        for a in all_articles:
            pub_date = _parse_pub_date(a.get("pubDate", ""))
            if pub_date and pub_date < cutoff:
                continue
            a["_pub_date"] = pub_date
            a["_title_clean"] = _strip_html(a.get("title", ""))
            a["_desc_clean"] = _strip_html(a.get("description", ""))
            a["_press"] = _get_press_name(a.get("originallink", ""))
            filtered.append(a)

        # 부정 키워드 필터
        negative_articles = []
        for a in filtered:
            text = a["_title_clean"] + " " + a["_desc_clean"]
            keywords = _find_negative_keywords(text)
            if keywords:
                a["_negative_keywords"] = keywords
                negative_articles.append(a)

        # 주요 일간지 우선 정렬
        major = [a for a in negative_articles if a["_press"] != "기타"]
        others = [a for a in negative_articles if a["_press"] == "기타"]
        negative_articles = major + others

        if format == "json":
            return json.dumps({
                "name": name,
                "queries": queries_used,
                "period": f"최근 {years}년",
                "total_results": len(filtered),
                "negative_count": len(negative_articles),
                "articles": [{
                    "title": a["_title_clean"],
                    "press": a["_press"],
                    "date": a.get("pubDate", ""),
                    "keywords": a.get("_negative_keywords", []),
                    "link": a.get("originallink", a.get("link", "")),
                } for a in negative_articles[:20]],
            }, ensure_ascii=False, indent=2)

        # Markdown
        lines = [
            f"# {name} 부정 뉴스 검색 결과",
            f"*검색 기간: 최근 {years}년 | 검색 쿼리: {', '.join(queries_used)}*",
            f"*전체 뉴스: {len(filtered)}건 | 부정 키워드 매칭: {len(negative_articles)}건*\n",
        ]

        if not negative_articles:
            lines.append("**부정 뉴스 없음.** 검색 기간 내 주요 부정 키워드에 매칭되는 기사가 없습니다.")
        else:
            lines.append(f"## 부정 뉴스 ({len(negative_articles)}건)\n")
            lines.append("| 날짜 | 매체 | 제목 | 키워드 |")
            lines.append("|------|------|------|--------|")

            for a in negative_articles[:20]:
                date_str = ""
                if a.get("_pub_date"):
                    date_str = a["_pub_date"].strftime("%Y-%m-%d")
                keywords = ", ".join(a.get("_negative_keywords", []))
                title = a["_title_clean"][:60]
                if len(a["_title_clean"]) > 60:
                    title += "..."
                lines.append(f"| {date_str} | {a['_press']} | {title} | {keywords} |")

            if len(negative_articles) > 20:
                lines.append(f"\n*... 외 {len(negative_articles) - 20}건 추가*")

        lines.append(f"\n---")
        lines.append(f"*최근 {years}년만 검색했습니다. 더 넓은 기간을 검색하려면 years 파라미터를 조정하세요.*")

        return "\n".join(lines)
