"""screener public tool — 범용 공시 스크리너 / 아침 공시 디제스트.

무인자 호출 = 오늘 아침 디제스트(전체시장 · 직전영업일 이후 · 핵심 프리셋 · scan only).
"""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.screener import build_screener_payload

# ── 렌더 헬퍼 ──────────────────────────────────────────────────────────

def _cap(n: int | None) -> str:
    """시총(원) → 사람이 읽는 한글 단위."""
    if not n:
        return "시총 –"
    if n >= 1_0000_0000_0000:  # 1조
        return f"{n/1_0000_0000_0000:.1f}조"
    if n >= 1_0000_0000:  # 1억
        return f"{n/1_0000_0000:,.0f}억"
    return f"{n:,}원"


def _won(n) -> str:
    if n in (None, "", 0):
        return "–"
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if abs(n) >= 1_0000_0000_0000:
        return f"{n/1_0000_0000_0000:.2f}조원"
    if abs(n) >= 1_0000_0000:
        return f"{n/1_0000_0000:,.0f}억원"
    return f"{n:,}원"


def _pct(v) -> str:
    if v in (None, ""):
        return "–"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return f"{v}"
    # <1% 값(자사주 시총대비 등)은 1자리면 0.0%로 뭉개져 분모가 안 읽힘 → 2자리 적응형.
    return f"{f:.2f}%" if 0 < abs(f) < 1 else f"{f:.1f}%"


# 단계/유형 이모지(폰 훑기 가독성)
_TYPE_ICON = {
    "order": "📦", "treasury": "🏦", "dividend": "💰", "dilutive": "🧬",
    "agm_notice": "🗳️", "ownership5": "📊", "insider10": "👤",
    "earnings": "📈", "agm_result": "✅", "restructuring": "🔀",
    "stake_deal": "🤝", "control_change": "👑", "litigation": "⚖️",
}

_DETAIL_BADGE = {
    "parsed": "", "partial": " ⚠부분", "unparsed_image": " 🖼️이미지(원문확인)",
    "no_data": " (무자료)", "error": " (조회실패)", "skipped": " (캡초과)",
    "scan_only": "",
}


def _detail_line(card: dict) -> str | None:
    """유형별 핵심필드 한 줄(details=true일 때)."""
    f = card.get("detail_fields") or {}
    tc = card["type"]["code"]
    if not f:
        return None
    if tc == "order":
        parts = [f"금액 {_won(f.get('amount_won'))}"]
        if f.get("revenue_ratio_pct") is not None:
            parts.append(f"매출대비 {_pct(f['revenue_ratio_pct'])}")
        if f.get("counterparty"):
            parts.append(f"상대 {f['counterparty']}")
        return " · ".join(parts)
    if tc == "treasury":
        parts = [f"금액 {_won(f.get('amount_won'))}"]
        # 분모 항상: 시총대비% — 추출기가 안 주면 amount ÷ 시총(krx_weekly, 이미 카드에 부착)으로 파생.
        ratio = f.get("mktcap_ratio_pct")
        if ratio is None and f.get("amount_won") and card.get("mktcap_won"):
            ratio = round(f["amount_won"] / card["mktcap_won"] * 100, 2)
        if ratio is not None:
            parts.append(f"시총대비 {_pct(ratio)}")
        if f.get("is_cancellation"):
            parts.append("소각")
        return " · ".join(parts)
    if tc == "dividend":
        parts = []
        if f.get("dps_won") is not None:
            parts.append(f"DPS {_won(f['dps_won'])}")
        if f.get("payout_ratio_pct") is not None:
            parts.append(f"성향 {_pct(f['payout_ratio_pct'])}")
        if f.get("record_date"):
            parts.append(f"기준일 {f['record_date']}")
        return " · ".join(parts) or None
    if tc == "dilutive":
        parts = [f"규모 {_won(f.get('amount_won'))}"]
        if f.get("allocation"):
            parts.append(f"배정 {f['allocation']}")
        if f.get("dilution_pct") is not None:
            parts.append(f"희석 {_pct(f['dilution_pct'])}")
        return " · ".join(parts)
    if tc == "agm_notice":
        titles = f.get("agenda_titles") or []
        if titles:
            head = "; ".join(titles[:3])
            more = f" 외 {len(titles)-3}건" if len(titles) > 3 else ""
            return f"안건 {head}{more}"
        return f"안건 {f.get('agenda_count','?')}건" if f.get("agenda_count") else None
    if tc == "ownership5":
        parts = []
        if f.get("holder"):
            parts.append(f"보유자 {f['holder']}")
        if f.get("stake_pct") is not None:
            parts.append(f"지분 {_pct(f['stake_pct'])}")
        if f.get("purpose"):
            parts.append(f"목적 {f['purpose']}")
        return " · ".join(parts) or None
    if tc == "earnings":
        parts = []
        if f.get("fiscal_year"):
            if f.get("period_kind") == "annual":
                parts.append(f"{f['fiscal_year']} 사업연도 결산 잠정치")
            elif f.get("fiscal_quarter"):
                parts.append(f"{f['fiscal_year']} 사업연도 {f['fiscal_quarter']}분기")
        if f.get("period"):
            per = f["period"]
            if per.get("start") and per.get("end"):
                parts.append(f"기간 {per['start']}~{per['end']}")
        if f.get("revenue_krw") is not None:
            parts.append(f"매출 {_won(f['revenue_krw'])}")
        if f.get("operating_profit_krw") is not None:
            parts.append(f"영업이익 {_won(f['operating_profit_krw'])}")
        if f.get("comparison_basis"):
            parts.append(f["comparison_basis"])
        return " · ".join(parts) or None
    return None


def _render_digest(payload: dict[str, Any]) -> str:
    p = payload
    period = p.get("period", {})
    uni = p.get("universe", {})
    counts = p.get("counts", {})
    as_of = (p.get("as_of") or "")[:16].replace("T", " ")

    # 헤더
    lines = [f"# 📬 공시 디제스트 · {as_of} KST", ""]
    span = f"{period.get('bgn_de','')}~{period.get('end_de','')}"
    pg = p.get("paging", {})
    _matched = pg.get("matched", counts.get("hits", 0))
    _ret = pg.get("returned", counts.get("returned", 0))
    _off = pg.get("offset", 0)
    head = f"**{uni.get('label','전체시장')}** · 기간 `{span}` · 스캔 {counts.get('scanned',0):,}건 → **{_matched}건 포착**"
    if _ret != _matched:
        head += f" · 이 중 {_off+1}~{_off+_ret}번째를 아래 싣는다"
    lines.append(head)
    lines.append("")

    # 조회실패 vs 신규없음 구분
    if p.get("status") == "error":
        lines.append("> ⚠️ **조회 실패** — DART 응답 오류로 스캔이 완료되지 않았다. 아래 경고 참조.")
        for w in p.get("warnings", []):
            lines.append(f"> - {w}")
        return "\n".join(lines)

    if p.get("no_new"):
        lines.append("> ✨ **새 공시 없음** — 지정 기간·유형·유니버스에서 신규 공시가 없다. (조회는 정상)")
        if p.get("warnings"):
            lines.append("")
            lines += [f"- {w}" for w in p["warnings"]]
        lines.append("")
        lines.append(f"_다음 실행 커서: `{p.get('next_cursor','')}`_")
        return "\n".join(lines)

    if p.get("status") == "partial":
        lines.append("> ⚠️ 부분 결과 — 일부 코드 스캔이 중단됐다(아래 경고). 포착분만 표시.")
        lines.append("")

    # 유형별 그룹 카드
    hits = p.get("hits", [])
    by_type: dict[str, list[dict]] = {}
    for h in hits:
        by_type.setdefault(h["type"]["code"], []).append(h)

    # 유형 표시 순서 = 첫 등장 순(이미 시총순 정렬됨)
    seen: list[str] = []
    for h in hits:
        if h["type"]["code"] not in seen:
            seen.append(h["type"]["code"])

    for tc in seen:
        rows = by_type[tc]
        icon = _TYPE_ICON.get(tc, "•")
        label = rows[0]["type"]["label"]
        lines.append(f"## {icon} {label} ({len(rows)})")
        for h in rows:
            corr = "🔁[정정] " if h.get("is_correction") else ""
            stage = h.get("stage", "")
            cap = _cap(h.get("mktcap_won"))
            code = h.get("stock_code") or "–"
            name = h.get("corp_name", "")
            title = h.get("title", "")
            # 카드 헤드라인
            head = f"- {corr}**{name}** `{code}` · {cap} · _{stage}_"
            lines.append(head)
            # 상세 한 줄(details)
            dl = _detail_line(h)
            badge = _DETAIL_BADGE.get(h.get("detail_status", ""), "")
            if dl:
                lines.append(f"    - {dl}{badge}")
            elif badge and h.get("detail_status") not in ("scan_only", "parsed"):
                lines.append(f"    - _{title}_{badge}")
            # 링크
            dart = h.get("dart_url", "")
            naver = h.get("naver_url", "")
            link = f"    - [DART]({dart})" if dart else ""
            if naver:
                link += f" · [naver]({naver})"
            if h.get("suggested_tool"):
                link += f" · `{h['suggested_tool']}`"
            if link.strip():
                lines.append(link)
        lines.append("")

    # 푸터
    foot = []
    if p.get("paging", {}).get("has_more"):
        foot.append(f"전체 {p['paging']['matched']}건 중 {p['paging']['returned']}건만 실었다 — "
                    f"이어받기 `offset={p['paging']['next_offset']}`")
    if counts.get("truncated_details"):
        foot.append("details 캡 초과분 존재")
    if counts.get("truncated_scan"):
        foot.append("스캔 페이지 상한 도달")
    if foot:
        lines.append("> " + " · ".join(foot))
    if p.get("warnings"):
        lines.append("")
        lines.append("### 유의")
        lines += [f"- {w}" for w in p["warnings"]]
    lines.append("")
    lines.append(f"_다음 실행 커서: `{p.get('next_cursor','')}` · DART {p.get('usage',{}).get('dart_api_calls','?')}콜_")
    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def screener(
        types: str = "core",
        period: str = "since_yesterday",
        universe: str = "all",
        details: bool = False,
        max_hits: int = 200,
        offset: int = 0,
        cursor: str = "",
        custom_start: str = "",
        custom_end: str = "",
        start_date: str = "",
        end_date: str = "",
        format: str = "md",
    ) -> str:
        """desc: **전체시장 공시 스크리너 / 아침 공시 디제스트.** 직전 실행 이후~오늘 전종목에 뜬 주요 공시를 카드형으로 요약(기업명+시총+유형+단계+정정+DART/naver 링크). 무인자 호출=오늘 아침 디제스트. scan(무엇이 떴나, 싸게)=디폴트, details=true면 필요 건만 문서 열어 유형별 핵심숫자(금액·분모%·DPS·안건·지분%).
        when: "오늘/어제 무슨 공시 떴어", "최근 며칠/일주일 잠정실적 발표", 매일 아침 공시 브리핑, 전체시장 **영업(잠정)실적**·수주·자사주·배당·증자·주총·5%보유 훑기, 특정 유형만 필터, 시총상위/지정종목만. 특정 회사 1곳 심층은 개별 tool(provisional_earnings·order_contracts·dividend 등).
        types: `core`(**영업잠정실적**·수주·자사주·배당·증자CB·주총소집·5%보유) / `all` / **사람 말 쉼표구분** — "자사주, 배당", "수주", "실적", "주총", "지분", "합병", "소송", "증자" 등. 코드도 그대로: earnings(잠정실적: 회계연도·기간·매출·영업익),order,treasury,dividend,dilutive,agm_notice,ownership5,agm_result,restructuring,stake_deal,control_change,litigation,insider10.
        period: **사람 말로 받는다** — "오늘"/"어제"/"어제부터"/"지난주"/"최근 7일"/"지난 한 달"/"최근 3개월"/"최근 45일", 날짜범위 "20260801~20260820"·"2026-08-01~2026-08-20", 단일일 "20260820". 또는 `start_date`/`end_date`(레포 공통 인자, YYYYMMDD). 옛 코드(today/yesterday/since_yesterday/last_7d/last_30d/custom+custom_start·custom_end)도 그대로 동작. 디폴트 since_yesterday. 시장스캔 3개월 하드캡.
        universe: **사람 말로 받는다** — "전체"(디폴트) / "코스피"·"코스닥"(시장 전체) / "코스피200" / "코스피 시총 상위 30"·"코스닥 상위 50"·"시총 상위 100" / "삼성전자, SK하이닉스"(이름 나열, 자동 코드화). 옛 문법(all·kospi200·kospi:N·kosdaq:N·top_mktcap:N·market:kospi|kosdaq·custom:…)도 그대로 동작. 각 카드에 시총 병기.
        details: false(디폴트, scan만) / true(문서 열어 숫자 — **이번 페이지 건만** 연다. 기간>30일이면 자동 off, 기간>7일이면 preview. 유니버스 크기로는 더 이상 막지 않는다).
        offset: 이어받기 위치(디폴트 0). 응답의 `paging.next_offset` 을 그대로 넣으면 다음 묶음이 온다. **매칭 수(`paging.matched`)와 이번에 실은 수(`paging.returned`)는 다른 값이다** — 표시된 건수를 전체로 읽지 말 것.
        rule: DART list.json 전체시장 필러(corp_code 無)를 유형별 detail코드로 스캔 → report_nm 키워드 분류 → 시총(krx_weekly) 부착 → dedup(정정=최신본만). 정정=`[기재정정]` 프리픽스, 단계태깅(결정≠결과≠소각). details는 유형별 파서(order_contracts 등) 디스패치. 빈 결과는 no_new(신규없음)/status=error(조회실패)로 구분.
        ref: order_contracts·treasury_share·dividend·dilutive_issuance·shareholder_meeting_notice·ownership_structure (유형별 심층)
        """
        payload = await build_screener_payload(
            types=types, period=period, universe=universe, details=details,
            start_date=start_date, end_date=end_date,
            max_hits=max(1, min(max_hits, 500)), offset=max(0, offset), cursor=cursor,
            custom_start=custom_start, custom_end=custom_end,
        )
        if format == "json":
            return as_pretty_json(payload)
        return _render_digest(payload)
