"""director_news public tool — 이사·감사 후보 부정 뉴스 검색.

**우리 키로 부른다.** 사용자는 키를 넣지 않고, 키는 서버 환경변수에만 있다
(`NAVER_SEARCH_API_CLIENT_ID`/`_SECRET`, NAVER API HUB 발급). 응답에 키를 싣지 않는다.
그래서 한도(일 25,000 · 월 775,000)는 전 사용자가 나눠 쓴다 — 호출당 1회로 묶고
`limit` 상한을 둔 이유다.

네이버 검색 API 자체에는 **언론사 선택 옵션이 없다.** `originallink` 도메인으로
우리가 갈라낸다(`press` 인자).
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services import director_news_keywords as KW

#: 언론사 묶음. 도메인 조각 → 표시 이름. 검색 API 에는 언론사 옵션이 없어
#: `originallink` 도메인으로 우리가 갈라낸다.
MAJOR_PRESS = {
    "chosun.com": "조선일보", "donga.com": "동아일보", "joongang.co.kr": "중앙일보",
    "hani.co.kr": "한겨레", "khan.co.kr": "경향신문", "seoul.co.kr": "서울신문",
    "kmib.co.kr": "국민일보", "segye.com": "세계일보", "munhwa.com": "문화일보",
    "hankookilbo.com": "한국일보", "yna.co.kr": "연합뉴스",
}
ECON_PRESS = {
    "mk.co.kr": "매일경제", "hankyung.com": "한국경제", "sedaily.com": "서울경제",
    "edaily.co.kr": "이데일리", "fnnews.com": "파이낸셜뉴스", "mt.co.kr": "머니투데이",
    "asiae.co.kr": "아시아경제", "heraldcorp.com": "헤럴드경제", "etoday.co.kr": "이투데이",
    "newsis.com": "뉴시스", "thebell.co.kr": "더벨", "infostockdaily.co.kr": "인포스탁데일리",
}
ALL_PRESS = {**MAJOR_PRESS, **ECON_PRESS}


def _press_name(link: str) -> str:
    """원문 링크 도메인으로 언론사를 판별한다. 모르면 도메인을 그대로 준다."""
    for frag, name in ALL_PRESS.items():
        if frag in link:
            return name
    m = re.search(r"https?://(?:www\.)?([^/]+)", link or "")
    return m.group(1) if m else "-"


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def _parse_date(raw: str):
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


async def build_payload(name: str, company: str = "", press: str = "all",
                        days: int = 365, limit: int = 10,
                        categories: str = "", min_severity: str = "low",
                        extra_keywords: str = "", exclude_keywords: str = "") -> dict[str, Any]:
    from open_proxy_mcp.dart.client import DartClient

    name = (name or "").strip()
    if not name:
        return {"status": "error", "subject": "director_news",
                "warnings": ["후보자 이름(name)이 비어 있다."], "data": {}}

    query = f'"{name}"' + (f' "{company.strip()}"' if company.strip() else "")
    client = DartClient()
    items = await client.naver_news_search(query, display=100, sort="date")

    cats = tuple(c.strip() for c in categories.split(",") if c.strip())
    extra = tuple(w.strip() for w in extra_keywords.split(",") if w.strip())
    exclude = tuple(w.strip() for w in exclude_keywords.split(",") if w.strip())
    allow = {"major": MAJOR_PRESS, "econ": ECON_PRESS}.get(press, ALL_PRESS)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))

    hits, skipped_old, skipped_press = [], 0, 0
    for it in items:
        link = it.get("originallink") or it.get("link") or ""
        dt = _parse_date(it.get("pubDate", ""))
        if dt and dt < cutoff:
            skipped_old += 1
            continue
        if press != "all" and not any(f in link for f in allow):
            skipped_press += 1
            continue
        title, desc = _clean(it.get("title", "")), _clean(it.get("description", ""))
        found = KW.match(f"{title} {desc}", cats=cats, min_severity=min_severity,
                         extra=extra, exclude=exclude)
        if not found:
            continue
        hits.append({
            "title": title, "press": _press_name(link), "link": link,
            "date": dt.astimezone().strftime("%Y-%m-%d") if dt else "",
            "keywords": [h["w"] for h in found[:5]],
            "labels": sorted({h["label"] for h in found}),
            "severity": KW.worst(found),
            "description": desc[:160],
        })

    # 고른 언론사 묶음을 위로, 그 안에서는 최신순
    hits.sort(key=lambda h: (0 if h["press"] in allow.values() else 1, h["date"]),
              reverse=False)
    hits.sort(key=lambda h: h["date"], reverse=True)
    hits.sort(key=lambda h: 0 if h["press"] in allow.values() else 1)
    hits.sort(key=lambda h: -KW.SEVERITY_ORDER.get(h["severity"], 0))

    warnings = []
    if not items:
        warnings.append("네이버 검색 API가 결과를 주지 않았다 — 키 미설정 또는 호출 실패.")
    return {
        "status": "ok", "subject": f"director_news: {name}",
        "warnings": warnings,
        "data": {
            "query": query, "name": name, "company": company,
            "press_filter": press, "days": days,
            "categories": list(cats) or list(KW.categories()),
            "min_severity": min_severity,
            "extra_keywords": list(extra), "exclude_keywords": list(exclude),
            "received": len(items), "matched": len(hits),
            "skipped_old": skipped_old, "skipped_press": skipped_press,
            "items": hits[:max(1, min(limit, 30))],
        },
    }


def _render(payload: dict[str, Any]) -> str:
    d = payload.get("data", {})
    lines = [f"# 후보 뉴스 점검 — {d.get('name','')}"
             + (f" ({d.get('company')})" if d.get("company") else ""), ""]
    for w in payload.get("warnings", []):
        lines.append(f"⚠️ {w}")
    lines += [
        f"- 검색어 `{d.get('query','')}` · 최근 {d.get('days')}일 · 언론사 `{d.get('press_filter')}`",
        f"- 분류 `{','.join(d.get('categories', []))}` · 최소 심각도 `{d.get('min_severity')}`"
        + (f" · 추가어 {d.get('extra_keywords')}" if d.get('extra_keywords') else "")
        + (f" · 제외어 {d.get('exclude_keywords')}" if d.get('exclude_keywords') else ""),
        f"- 받은 기사 {d.get('received',0)}건 → **부정 키워드 걸린 것 {d.get('matched',0)}건** "
        f"(기간 밖 {d.get('skipped_old',0)} · 언론사 제외 {d.get('skipped_press',0)})",
        "",
    ]
    items = d.get("items", [])
    if not items:
        lines.append("걸린 기사 없음. **없다는 뜻이 아니라 이 검색어·기간·언론사에서 안 나왔다는 뜻이다.**")
        return "\n".join(lines)
    sev = {"high": "🔴", "mid": "🟠", "low": "·"}
    lines += ["| 심각 | 날짜 | 언론사 | 분류 | 걸린 말 | 제목 |",
              "|---|---|---|---|---|---|"]
    for h in items:
        lines.append(f"| {sev.get(h.get('severity'), '')} | {h['date']} | {h['press']} | "
                     f"{'·'.join(h.get('labels', []))} | {'·'.join(h['keywords'])} | "
                     f"[{h['title'][:60]}]({h['link']}) |")
    lines += ["", "※ 키워드가 걸렸다고 사실이 확인된 것은 아니다. 원문을 열어 확인할 것."]
    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def director_news(
        name: str,
        company: str = "",
        press: str = "all",
        days: int = 365,
        limit: int = 10,
        categories: str = "",
        min_severity: str = "low",
        extra_keywords: str = "",
        exclude_keywords: str = "",
        format: str = "md",
    ) -> str:
        """desc: 이사·감사·감사위원 **후보자 부정 뉴스 점검**. 후보 이름(+회사명)으로 네이버 뉴스를 훑어 횡령·배임·수사·제재·해임 등 48개 부정 키워드가 걸린 기사만 남긴다. 언론사 묶음을 골라 거를 수 있다.
        when: 주총 안건 검토, 이사·감사위원 선임 찬반 판단, 후보자 적격성·평판 확인, 주주제안 후보 검증.
        rule: NAVER API HUB 뉴스 검색 1회 호출(최대 100건) 후 로컬 필터. 검색 API에는 언론사 옵션이 없어 원문 링크 도메인으로 판별한다. **키워드가 걸렸다 ≠ 사실 확인** — 원문을 열어 확인해야 한다. 결과 0건은 「무혐의」가 아니라 「이 조건에서 안 걸림」이다.
        press: `all` 전체 / `major` 주요 일간지 11곳 / `econ` 경제·증권지 12곳.
        categories: 볼 분류를 쉼표로 — `criminal` 형사·수사 / `economic` 경제범죄 / `accounting` 회계·공시 / `regulatory` 규제·제재 / `governance` 지배구조·직무 / `labor_safety` 노동·안전 / `ethics` 윤리·평판. 비우면 전체.
        min_severity: `low`(기본) / `mid` / `high` — 낮은 심각도를 잘라낸다.
        extra_keywords: 이번 검색에만 더할 말(쉼표). exclude_keywords: 뺄 말(쉼표).
        days: 최근 며칠까지 볼 것인가 (기본 365).
        limit: 표에 실을 최대 건수 (기본 10, 최대 30).
        ref: director_board (이사회 구성), proxy_advise_before_meeting (안건 판단), risk_events (회사 리스크 공시)
        """
        payload = await build_payload(name, company=company, press=press,
                                      days=days, limit=limit,
                                      categories=categories, min_severity=min_severity,
                                      extra_keywords=extra_keywords,
                                      exclude_keywords=exclude_keywords)
        if format == "json":
            return as_pretty_json(payload)
        return _render(payload)
