"""screener public tool — 범용 공시 스크리너 / 아침 공시 디제스트.

무인자 호출 = 오늘 아침 디제스트(전체시장 · 직전영업일 이후 · 핵심 프리셋 · scan only).
"""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.disclosure_flow import build_flow_payload
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


def _coverage_block(p: dict[str, Any]) -> list[str]:
    """「이 응답이 못 본 것」 — 불완전한 스캔 코드를 사람이 읽는 문장으로.

    한 곳에서만 만든다(service 와 renderer 가 각자 조립하면 한 화면에 두 번 찍힌다).
    그리고 **매칭 0건일 때도** 나와야 한다 — 스캔이 잘려서 0건인데 「새 공시 없음」만 보이면
    그게 가장 나쁜 침묵이다.
    """
    cut = [c for c in (p.get("coverage") or []) if not c.get("complete")]
    if not cut:
        return []
    out = ["", "### 이 응답이 못 본 것"]
    for c in cut:
        why = (f"DART 오류 {c['error']}" if c.get("error")
               else f"페이지 상한 {c['fetched_pages']}/{c['total_pages']}")
        saw = (f"본 접수일 {c['seen_from']}~{c['seen_to']}"
               if c.get("seen_from") else "받은 행 없음")
        hole = f" · 빠진 페이지 {c['missing_pages']}" if c.get("missing_pages") else ""
        out.append(f"- `{c['code']}` — {why} · {saw}{hole}")
    out.append("- 기간을 나눠 두 번 부르면 그만큼 더 본다. "
               "위 접수일 범위는 **실제로 받은 행**의 범위이지 창 전체가 아니다.")
    return out


def _render_digest(payload: dict[str, Any]) -> str:
    p = payload
    if p.get("status") == "needs_input":
        # 260918: 유니버스를 못 읽으면 조회하지 않고 되묻는다 — 시장 전체 결과를 대신 내지 않는다.
        L = ["# 📬 공시 디제스트 — 조회하지 않음", "", f"> ❓ {p.get('question', '')}", ""]
        return "\n".join(L + [f"- {w}" for w in p.get("warnings", [])])
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
        _cut = [c for c in (p.get("coverage") or []) if not c.get("complete")]
        lines.append("> ✨ **새 공시 없음** — 지정 기간·유형·유니버스에서 신규 공시가 없다."
                     + (" (조회는 정상)" if not _cut else " **다만 스캔이 온전하지 않았다 — 아래 참조.**"))
        lines += _coverage_block(p)
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
            link = f"    - [DART]({dart})" if dart else ""
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
        # 「상한 도달」 여섯 글자로는 **무엇이 빠졌는지**를 알 수 없다 — 어느 코드가 몇 페이지 중
        # 몇을 봤고 어느 날짜부터 안 보이는지까지 적는다(json 에만 있으면 사람은 못 본다).
        foot.append("스캔 페이지 상한 도달")
    if counts.get("deduped_away"):
        foot.append(f"정정본이 원본을 대체한 건 {counts['deduped_away']}건")
    if foot:
        lines.append("> " + " · ".join(foot))
    lines += _coverage_block(p)
    if p.get("warnings"):
        lines.append("")
        lines.append("### 유의")
        lines += [f"- {w}" for w in p["warnings"]]
    lines.append("")
    lines.append(f"_다음 실행 커서: `{p.get('next_cursor','')}` · DART {p.get('usage',{}).get('dart_api_calls','?')}콜_")
    return "\n".join(lines)


# ── 흐름 보기 (공시 원장 합산, 260918) ─────────────────────────────────

_FLOW_WORDS = {"flow", "흐름", "흐름보기", "업종별", "업종흐름", "평소대비", "추이"}
_FLOW_ROWS_INDUSTRY = 25
_FLOW_ROWS_COVERAGE = 20


def _is_flow(view: str) -> bool:
    return (view or "").strip().lower().replace(" ", "") in _FLOW_WORDS


def _times(v) -> str:
    return f"{v:.1f}배" if v is not None else "–"


def _cnt(v) -> str:
    return f"{v:.1f}건" if v is not None else "–"


def _flow_table(lv: dict[str, Any], kind: str, max_rows: int | None) -> list[str]:
    is_order = kind == "order"
    if is_order:
        L = ["| 업종 | 새 계약 | 평소(같은 일수) | 평소 대비 | 금액 합 (확인 건수) | 평균 매출 대비 | 정정 | 해지 |",
             "|---|---|---|---|---|---|---|---|"]
    else:
        L = ["| 업종 | 새 공시 | 평소(같은 일수) | 평소 대비 | 금액 합 (확인 건수) | 세부 | 정정 |",
             "|---|---|---|---|---|---|---|"]
    # 이번에 아무것도 없고 평소도 1건 미만인 줄은 md 에서 뺀다(json 에는 남는다) — 0배 줄이 표를 덮는다.
    shown_all = [r for r in lv["rows"] if r["new"] or r["corrections"] or r.get("cancels") or not r["thin_base"]]
    rows = shown_all if max_rows is None else shown_all[:max_rows]
    for r in rows + [lv["total"]]:
        name = f"**{r['bucket']}**" if r is lv["total"] else r["bucket"]
        vs = _times(r["vs_base"]) + (" · 평소 적음" if r["thin_base"] and r is not lv["total"] else "")
        amt = f"{_won(r['amount_krw'])} ({r['amount_n']})" if r["amount_n"] else "–"
        if is_order:
            L.append(f"| {name} | {r['new']} | {_cnt(r['expected'])} | {vs} | {amt} | {_pct(r['avg_ratio_pct'])} "
                     f"| {r['corrections']} | {r['cancels'] or 0} |")
        else:
            sub = " · ".join(f"{k} {v}" for k, v in list(r["subtypes"].items())[:2]) or "–"
            L.append(f"| {name} | {r['new']} | {_cnt(r['expected'])} | {vs} | {amt} | {sub} | {r['corrections']} |")
    hidden = len(lv["rows"]) - len(rows)
    notes = []
    if hidden:
        notes.append(f"{lv['level_label']} {len(lv['rows'])}개 중 {hidden}개(이번에 없고 평소도 드문 곳 포함)는 줄였다. 전체는 format=\"json\"")
    dups = lv["total"].get("subsidiary_duplicates") or 0
    if dups:
        notes.append(f"자회사가 직접 낸 공시와 겹치는 모회사 공시 {dups}건은 중복이라 뺐다")
    if notes:
        L += ["", "> " + ". ".join(notes) + "."]
    return L


def _render_flow(p: dict[str, Any]) -> str:
    d = p.get("data") or {}
    if p.get("status") not in ("ok", "no_data") or not d.get("flows"):
        tail = "조회하지 않음, 되물음" if p.get("status") == "needs_input" else "볼 수 없음"
        L = [f"# {p.get('subject') or '공시 흐름'} — {tail}", ""]
        L += [f"- {w}" for w in p.get("warnings", [])]
        return "\n".join(L)
    per, base, led, uni = d["period"], d["baseline"], d["ledger"], d["universe"]
    L = [f"# {p['subject']}", "",
         f"_{uni['label']} · {per['days']}일 · 비교 기준 직전 {base['weeks']}주 ({base['start']} ~ {base['end']}) "
         f"· 원장 수록 {led['first_day']} ~ {led['last_day']}_"]
    for f in d["flows"]:
        for lv_code in d["levels"]:
            lv = f["levels"][lv_code]
            L += ["", f"## {f['label']} — {lv['level_label']}", ""]
            L += _flow_table(lv, f["kind"], _FLOW_ROWS_INDUSTRY if lv_code == "industry" else None)
    lo = d.get("large_orders")
    if lo is not None:
        L += ["", f"## 큰 수주 — 매출 대비 {lo['min_ratio_pct']:g}% 이상 새 계약 ({lo['matched']}건)", ""]
        if lo["rows"]:
            L += ["| 일자 | 회사 | 중분류 | 금액 | 매출 대비 | 상대방 | 원문 |", "|---|---|---|---|---|---|---|"]
            for r in lo["rows"]:
                cp = (r.get("counterparty") or "비공개")[:30]
                name = r["corp_name"] + (" (자회사 계약)" if r.get("via_subsidiary") else "")
                L.append(f"| {r['rcept_dt'][5:]} | {name} | {r.get('industry') or '–'} | "
                         f"{_won(r['amount_krw'])} | {_pct(r['revenue_ratio_pct'])} | {cp} | [DART]({r['dart_url']}) |")
        else:
            L.append("해당 없음.")
        extra = []
        if lo["ratio_unread"]:
            extra.append(f"매출 대비 비율을 아직 못 읽은 새 계약 {lo['ratio_unread']}건은 목록에 없다")
        if lo["corrections_over_min"]:
            extra.append(f"기준 이상인 정정 공시 {lo['corrections_over_min']}건은 새 계약이 아니라 뺐다")
        if extra:
            L += ["", "> " + ". ".join(extra) + "."]
    cov = d.get("order_coverage")
    if cov is not None:
        L += ["", f"## 올해 누적 수주 — 매출 대비 합 상위 {_FLOW_ROWS_COVERAGE} ({cov['start']} ~ {cov['end']})", ""]
        if cov["rows"]:
            L += ["| 회사 | 중분류 | 시총 | 새 계약 | 매출 대비 합 | 금액 합 | 비율 확인 | 해지 |",
                  "|---|---|---|---|---|---|---|---|"]
            for r in cov["rows"][:_FLOW_ROWS_COVERAGE]:
                L.append(f"| {r['corp_name']} | {r.get('industry') or '–'} | {_cap(r.get('mktcap_krw'))} | {r['new']} | "
                         f"{_pct(r['ratio_sum_pct'])} | {_won(r['amount_krw'])} | {r['ratio_n']}/{r['new']} | {r['cancels']} |")
            L += ["", f"> {cov['companies']}개사 · 새 계약 {cov['new_contracts']}건 중 매출 대비 비율을 읽은 것 {cov['ratio_read']}건. "
                  "매출이 작은 회사는 합이 수백~수천 %까지 커진다 — 시총을 같이 볼 것. 자회사 계약을 다시 낸 공시는 "
                  "자회사 매출 기준이라 이 합에서 뺐다."]
        else:
            L.append("해당 없음.")
    L += ["", f"> {d['method']}"]
    L += [f"> {w}" for w in p.get("warnings", [])]
    return "\n".join(L)


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
        view: str = "",
        level: str = "both",
        baseline_weeks: int = 13,
        large_min_pct: float = 10.0,
    ) -> str:
        """desc: **전체시장 공시 스크리너 / 아침 공시 디제스트.** 직전 실행 이후~오늘 전종목에 뜬 주요 공시를 카드형으로 요약(기업명+시총+유형+단계+정정+DART 링크). 무인자 호출=오늘 아침 디제스트. scan(무엇이 떴나, 싸게)=디폴트, details=true면 필요 건만 문서 열어 유형별 핵심숫자(금액·분모%·DPS·안건·지분%).
        when: "오늘/어제 무슨 공시 떴어", "최근 며칠/일주일 잠정실적 발표", 매일 아침 공시 브리핑, 전체시장 **영업(잠정)실적**·수주·자사주·배당·증자·주총·5%보유 훑기, 특정 유형만 필터, 시총상위/지정종목만. 특정 회사 1곳 심층은 개별 tool(provisional_earnings·order_contracts·dividend_disclosure 등).
        types: `core`(**영업잠정실적**·수주·자사주·배당·증자CB·주총소집·5%보유) / `governance`(공개매수·위임장권유·최대주주변경·소송·자사주·5%보유·재편·주식양수도) / `all` / **사람 말 쉼표구분** — "자사주, 배당", "공개매수", "위임장", "거버넌스", "수주", "실적", "주총", "지분", "합병", "소송", "증자" 등. 코드도 그대로: earnings(잠정실적: 회계연도·기간·매출·영업익),order,treasury,dividend,dilutive,agm_notice,ownership5,agm_result,restructuring,stake_deal,control_change,litigation,insider10,tender_offer,proxy_solicitation.
        governance: 제목에서 발견한 공시는 조사 대상이며 부정 신호 판정이 아니다. hits의 corp_code/stock_code를 회사별 중복 제거해 최대 30개씩 governance_screen(companies=[...])으로 전달하고 원문을 읽어 평가한다. 공시 수와 회사 수를 구별하고 paging.has_more면 다음 페이지도 확인. 공개매수·위임장권유는 scan-only이며 서로 다른 제출자를 합치지 않는다. 이 호출은 예약 작업을 만들지 않는다.
        period: **사람 말로 받는다**(카드 보기·흐름 보기 같은 뜻, 오늘 기준) — 날: "오늘"/"어제"/"어제부터"/"그저께" · 달력: "이번 주"(월~오늘)/"지난주"(지난주 월~일)/"이번 달"/"지난달"(1일~말일)/"이번 분기"/"지난 분기"/"올해"/"작년" · 굴러가는 창: "최근 7일"(오늘 포함 7일)/"최근 2주"/"지난 한 달"/"최근 3개월"/"3일 전부터" · 절대: "8월"/"2026년 8월"/"3분기"/"2026년 2분기"/"상반기"/"2025년"/"2026-09-01"/"9월 1일"/"9/1" · 범위: "8월 1일부터 8월 20일까지"/"20260801~20260820"/"4월부터 8월 10일 사이" · 시작만: "9월 1일부터"(~오늘). 연도 없는 월·분기는 오늘 이전의 가장 최근 것. 시작 없는 "~까지"는 추측하지 않고 되묻는 안내를 단다. 또는 `start_date`/`end_date`(레포 공통 인자, YYYYMMDD — 2026.09.01 꼴도 받음). 옛 코드(today/yesterday/since_yesterday/last_7d/last_30d/custom+custom_start·custom_end)와 this_week·last_week·this_month·last_month·ytd 같은 코드도 받는다. 디폴트 since_yesterday. 카드 보기는 시장스캔 3개월 하드캡(넘으면 잘라서 밝히고 흐름 보기를 안내).
        universe: **사람 말로 받는다** — "전체"(디폴트) / "코스피"·"코스닥"(시장 전체) / "코스피200" / "코스피 시총 상위 30"·"코스닥 상위 50"·"시총 상위 100"·"코스피 시가총액 상위 200개 기업"(숫자 뒤 개·종목·기업·회사·개사·곳 무관) / "삼성전자, SK하이닉스"(이름 나열, 자동 코드화). 나열에서 회사를 하나도 못 찾거나 「코스피 120」처럼 수인지 이름인지 모를 때는 **조회하지 않고 되묻는다**(status=needs_input — 시장 전체로 바꿔 보이지 않는다). 옛 문법(all·kospi200·kospi:N·kosdaq:N·top_mktcap:N·market:kospi|kosdaq·custom:…)도 그대로 동작. 각 카드에 시총 병기.
        details: false(디폴트, scan만) / true(문서 열어 숫자 — **이번 페이지 건만** 연다. 기간>30일이면 자동 off, 기간>7일이면 preview. 유니버스 크기로는 더 이상 막지 않는다).
        offset: 이어받기 위치(디폴트 0). 응답의 `paging.next_offset` 을 그대로 넣으면 다음 묶음이 온다. **매칭 수(`paging.matched`)와 이번에 실은 수(`paging.returned`)는 다른 값이다** — 표시된 건수를 전체로 읽지 말 것.
        rule: DART list.json 전체시장 필러(corp_code 無)를 유형별 detail코드로 스캔 → report_nm 키워드 분류 → 시총(krx_weekly) 부착 → dedup(정정=최신본만). 정정=`[기재정정]` 프리픽스, 단계태깅(결정≠결과≠소각). details는 유형별 파서(order_contracts 등) 디스패치. 빈 결과는 no_new(신규없음)/status=error(조회실패)로 구분.
        view: 비우면 카드 보기(위 설명 전부). **"흐름"이면 흐름 보기** — 매일 밤 쌓이는 공시 원장만 읽어(DART 0콜) ① 업종 대분류·중분류별 새 공시 건수·금액을 직전 N주 같은 일수 환산 평소와 비교(「평소 대비 몇 배」) ② 매출 대비 비율이 기준 이상인 큰 수주 목록 ③ 올해 회사별 누적 수주(공시에 적힌 매출 대비 %의 합)를 준다. "지난주 업종별 수주 평소보다 많이 떴나"·"올해 수주가 매출 대비 가장 많이 쌓인 회사"·"업종별 자사주·증자 흐름"은 이것. 흐름 보기의 types 기본은 수주, period 기본은 원장 최신일까지 최근 7일(나머지 말은 카드 보기와 같은 뜻, 3개월 한도 없음 — 「이번 주·달·분기·올해」는 원장 최신일까지, 원장이 아직 그 칸에 없으면 가장 최근 칸을 보이고 밝힌다), universe 는 카드 보기와 같은 문법. 정정·해지는 새 공시로 세지 않고 따로 센다. 원장은 밤 배치라 오늘 뜬 공시는 카드 보기로.
        level: 흐름 보기의 업종 분류 단계 — "둘 다"(기본) / "대분류"(10) / "중분류"(28).
        baseline_weeks: 흐름 보기의 비교 기준 주 수(기본 13, 1~52).
        large_min_pct: 흐름 보기 큰 수주의 매출 대비 기준 %(기본 10).
        ref: order_contracts·treasury_share·dividend_disclosure·dilutive_issuance·shareholder_meeting_notice·ownership_structure (유형별 심층), governance_screen (최대 30개사 원문 기반 거버넌스 검토)
        """
        if _is_flow(view):
            payload = await build_flow_payload(
                types=types, period=period, universe=universe, start_date=start_date or custom_start,
                end_date=end_date or custom_end, level=level, baseline_weeks=baseline_weeks,
                large_min_pct=large_min_pct)
            if format == "json":
                return as_pretty_json(payload)
            return _render_flow(payload)
        payload = await build_screener_payload(
            types=types, period=period, universe=universe, details=details,
            start_date=start_date, end_date=end_date,
            max_hits=max(1, min(max_hits, 500)), offset=max(0, offset), cursor=cursor,
            custom_start=custom_start, custom_end=custom_end,
        )
        if format == "json":
            return as_pretty_json(payload)
        return _render_digest(payload)
