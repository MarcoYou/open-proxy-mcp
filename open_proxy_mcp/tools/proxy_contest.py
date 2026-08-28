"""proxy_contest public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.tools._shared import company_id_line
from open_proxy_mcp.services.proxy_contest import (
    LIT_FIELD_LABELS_KO as _LIT_FIELD_LABELS_KO,
    build_proxy_contest_payload,
)


# 260813: 아래 세 값이 **영문 그대로 사용자 화면에 찍혔다**(실측 고려아연 답변에
#   `| signal_level | contestable |` 이 표로 나감). 화면에 나가는 모든 값은 한글을 갖는다.
#   producer 를 읽고 만든 사전이다 — services/proxy_contest.py:690 `_signal_level`,
#   services/shareholder_meeting.py `_meeting_phase` 가 내는 값 전부.
_SIGNAL_LEVEL_KO = {
    "contestable": "표 대결 성립 가능",
    "watch": "지켜볼 단계",
    "stable": "안정",
}
_RESULT_STATUS_KO = {
    "available": "결과 공시 확보",
    "requires_review": "결과 공시 있으나 원문 확인 필요",
    "not_due_yet": "주총 전 (결과 없음)",
    "pending_or_missing": "주총 후 · 결과 공시 미확인",
    "unknown": "주총일 미확정",
}
_MEETING_TYPE_KO = {"annual": "정기주총", "extraordinary": "임시주총", "auto": "정기/임시"}



# 표 셀에 들어가는 분류값은 엔진 내부 enum 이다 — 사람이 읽는 표에 영문 코드를 두지 않는다.
# 사전은 **producer 를 읽고** 만든다(260728 디버깅 에이전트 지적: 관찰된 값만 보고 손으로 쓰면
# 절반이 새고 없는 키가 들어간다). 출처:
#   services/proxy_contest.py `_fight_actor_group()` — registry_overlap · coheld_with_registry
#                                                     · external_active_block · external_or_passive
#   services/proxy_contest.py `_signal_actor_side()` — 위 3종 + company · retail_activism · shareholder
# 두 producer 가 어휘를 공유하므로 사전도 하나로 둔다(둘로 나누면 컬럼이 뒤바뀐다 — 실측 결함).
_GROUP_KO = {
    "registry_overlap": "주주명부상 최대주주",
    "coheld_with_registry": "최대주주와 공동보유",
    "external_active_block": "외부 경영참여 블록",
    "external_or_passive": "외부·단순투자",
    "company": "회사측",
    "shareholder": "주주측",
    "retail_activism": "소액주주 행동",
    "litigation": "소송",
    "unknown": "미상", "": "-", "-": "-",
}
_CATEGORY_KO = {"fight": "위임장 대결", "litigation": "소송", "signal": "5% 보고"}


def _ko(m: dict, v) -> str:
    return m.get(str(v or ""), str(v or ""))


_INSIDER_TOP_N = {"summary": 8, "signals": 12, "insiders": 40}


def _n(value, suffix: str = "") -> str:
    """숫자 셀. **None 은 0이 아니라 「미기재」** — 회사가 공시에 안 적은 칸이다."""
    if value is None:
        return "미기재"
    if isinstance(value, float):
        return f"{value:,.2f}{suffix}"
    return f"{value:+,}{suffix}" if suffix == "주" else f"{value:,}{suffix}"


def _render_insiders(data: dict, scope: str) -> list[str]:
    insiders = data.get("insider_holdings")
    if not insiders:
        return []
    lines = [
        "",
        "## 임원·주요주주 소유상황 (5% 문턱 아래)",
        "5% 대량보유(D001)는 5% 이상만 잡는다. **그 아래에서 지배주주·특수관계인·임원이 조금씩 "
        "사 모으는 움직임은 이 보고(D002)에만 남는다** — 표 대결에서는 이 쪽이 먼저 움직인다. "
        "다만 임원 보고에는 스톡옵션 행사·상속·단순 처분도 섞여 있다. **자동 판정은 하지 않는다**; "
        "누가·언제·얼마나 움직였는지만 싣고 해석은 읽는 쪽에 맡긴다.",
    ]

    reason = insiders.get("status_reason")
    if reason == "no_data":
        lines.append("")
        lines.append("- **[데이터 없음]** 이 회사엔 임원·주요주주 소유상황보고가 없다 — "
                     "조회는 정상이었다(보고 자체가 없는 것이며 도구 문제가 아니다).")
        return lines
    if reason == "fetch_failed":
        lines.append("")
        lines.append("- **[호출 실패]** 임원·주요주주 소유상황보고(elestock)를 읽지 못했다. "
                     "**「임원 매집이 없다」는 뜻이 아니다** — 이 구간은 확인하지 못한 것이다. "
                     "유의사항에 실패 코드가 있다.")
        return lines

    cov = insiders.get("coverage") or {}
    rows = insiders.get("reporters") or []
    lines.append("")
    lines.append(
        f"- 조사구간 보고 {cov.get('rows_in_window', 0):,}건 / 보고자 {insiders.get('reporter_count', 0):,}명"
        + (f" (전체 이력 {cov.get('rows_all_history', 0):,}건)" if cov.get("rows_all_history") else "")
    )
    if cov.get("truncated"):
        lines.append(
            f"- ⚠️ **상한 {cov.get('rows_limit', 0):,}건에 걸렸다** — 최근 {cov.get('rows_analyzed', 0):,}건"
            f"({cov.get('analyzed_from_date', '')} 이후)만 집계했고 {cov.get('rows_dropped', 0):,}건은 "
            "아래 순증감에 **빠져 있다**. `insider_rows_limit`을 올리거나 `start_date`/`end_date`로 "
            "구간을 좁혀 다시 불러라."
        )
    if not rows:
        lines.append("- 조사구간 내 보고 없음 (구간 밖 이력은 있을 수 있다 — `start_date`로 넓혀 보라).")
        return lines

    top_n = _INSIDER_TOP_N.get(scope, 12)
    shown = rows[:top_n]
    lines.extend([
        "",
        "순증감은 **보유 수량의 차이**(구간 첫 보고 → 최근 보고)다. 공시의 「증감」칸을 더하지 않는다 — "
        "거기엔 보유 전량을 그대로 적는 **신규·재보고** 행이 섞여 있어 더하면 부풀려진다"
        "(실측: 국민연금 +13,710,029주로 나오지만 실제 보유는 2,752,107주). "
        "보고가 1건뿐인 보고자는 **변화를 말할 수 없어** 「보고 1회」로 두고, 그 1건이 신규보고면 그렇게 적는다.",
        "",
        "| 보고자 | 직위 | 주요주주 | 보고 | 기간 | 보유(주) 처음→최근 | 순증감(주) | 지분율 | 최근 90일 | 명부/5% |",
        "|--------|------|----------|------|------|-------------------|-----------|--------|-----------|---------|",
    ])
    for r in shown:
        pos = r.get("position") or "-"
        reg = r.get("registered_executive")
        if reg:
            pos = f"{pos} ({reg})" if r.get("position") else reg
        major = r.get("major_shareholder_type") or "-"

        basis = r.get("net_change_basis")
        if basis == "levels":
            held = f"{_n(r.get('shares_first'))} → {_n(r.get('shares_last'))}"
            net = _n(r.get("net_change_shares"), "주")
        elif basis == "reported":
            held = _n(r.get("shares_last"))
            net = f"{_n(r.get('net_change_shares'), '주')} (공시 증감칸)"
        else:
            held = _n(r.get("shares_last"))
            net = "신규보고 (변화 산출 불가)" if r.get("initial_report_in_window") else "보고 1회 (변화 산출 불가)"

        rw = r.get("recent_window") or {}
        recent = "-"
        recent_value = rw.get("net_change_shares")
        if recent_value is None:
            recent_value = rw.get("reported_change_sum")
        if recent_value is not None:
            mark = "🔺 매집 " if rw.get("accumulating") else ""
            recent = f"{mark}{recent_value:+,}주 ({rw.get('report_count', 0)}회)"
        elif rw.get("report_count"):
            recent = f"{rw['report_count']}회 (증감 산출 불가)"
        cross = "·".join(filter(None, [
            "명부" if r.get("in_registry") else "",
            "5%블록" if r.get("in_5pct_block") else "",
        ])) or "-"
        pct = r.get("ownership_pct_last")
        lines.append(
            f"| {r['reporter']} | {pos} | {major} | {r['report_count']}회 | "
            f"{r.get('first_date','')}~{r.get('last_date','')} | {held} | {net} | "
            f"{(f'{pct:.2f}%' if pct is not None else '미기재')} | {recent} | {cross} |"
        )

    # 두 기준이 갈리는 보고자는 그 사실 자체가 정보다 — 숨기지 않는다.
    split = [
        r for r in shown
        if r.get("net_change_shares") is not None
        and r.get("reported_change_sum") is not None
        and r["net_change_shares"] != r["reported_change_sum"]
    ]
    if split:
        lines.append("")
        lines.append("> 보유 차이와 공시 「증감」칸 합계가 갈리는 보고자 — "
                     "신규·재보고 행에 담긴 변동이 증감칸에 안 잡힌 것이다: "
                     + " · ".join(
                         f"{r['reporter']} 보유차 {r['net_change_shares']:+,}주 / 증감합 "
                         f"{r['reported_change_sum']:+,}주"
                         + (f" (신규보고 {r['initial_report_count']}건)" if r.get("initial_report_count") else "")
                         for r in split[:6]
                     ))
    if len(rows) > len(shown):
        lines.append("")
        wider = "`format=json`으로 부르면 전원이 나온다" if scope == "insiders" else \
            "`scope=insiders`로 부르면 40명까지, `format=json`이면 전원이 나온다"
        lines.append(f"> 보고자 {len(rows):,}명 중 {len(shown)}명만 표시했다 "
                     f"(주요주주 → 최근 매집 → 순증감 크기 순). {wider} — **조용히 자른 것이 아니다.**")

    if any(r.get("unparsed_change_count") for r in shown):
        lines.append("")
        lines.append("> 「미기재」는 회사가 공시에 값을 안 적은 칸이다 — **0이 아니다.** "
                     "그 보고는 순증감 합계에서 빠졌다.")

    if scope == "insiders":
        lines.extend(["", "### 보고자별 최근 보고 (원문 접근 경로)",
                      "아래 접수번호를 `evidence` 도구에 넣으면 공시 원문 뷰어 주소가 나온다. "
                      "사유(스톡옵션 행사·장내매수·상속 등)는 **공시 원문에만** 있다 — 여기 숫자만 보고 단정하지 마라."])
        for r in shown[:15]:
            refs = " · ".join(
                f"{f['date']} {_n(f.get('change_shares'), '주')}"
                + ("(신규보고)" if f.get("initial_report") else "")
                + f" `{f['rcept_no']}`"
                for f in (r.get("recent_filings") or [])
            )
            if refs:
                lines.append(f"- **{r['reporter']}**: {refs}")

    lines.append("")
    lines.append("> 여기 없으면 볼 곳: `ownership_structure`(명부·5% 블록 전체) · "
                 "`shareholder_meeting_notice`(주총 안건) · 원문은 접수번호를 `evidence` 도구에 넣어 확인한다. "
                 "출처: DART elestock (임원ㆍ주요주주 특정증권등 소유상황보고서, 공시유형 D002).")
    return lines


def _render_error(payload: dict[str, Any], scope: str = "summary") -> str:
    message = "분쟁 관련 공시를 확정하지 못했다."
    if scope == "vote_math":
        message = "vote_math를 확정하지 못했다."
    lines = [f"# proxy_contest: {payload.get('subject', '')}", "", message]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [f"# proxy_contest: {data.get('query', payload.get('subject', ''))}", "", "회사 식별이 애매해 분쟁 공시를 자동 선택하지 않았다.", "", "| 회사명 | ticker | corp_code | company_id |", "|------|--------|-----------|------------|"]
    for item in data.get("candidates", []):
        lines.append(f"| {item['corp_name']} | `{item['ticker']}` | `{item['corp_code']}` | `{item['company_id']}` |")
    return "\n".join(lines)


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    summary = data.get("summary", {})
    players = data.get("players", {})
    control_context = data.get("control_context", {})
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} proxy contest", ""]
    _cid = company_id_line(data)
    if _cid:
        lines.append(_cid)
    window = data.get("window", {})
    if window:
        lines.append(f"- 최근 12개월 조사구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`")
    lines.append("")
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    if scope == "summary":
        lines.append("## 요약")
        lines.append(f"- 위임장/공개매수 관련 공시: {summary.get('proxy_filing_count', 0)}건")
        lines.append(f"- 주주측 문서: {summary.get('shareholder_side_count', 0)}건")
        lit_dedup = summary.get("litigation_dedup") or {}
        if lit_dedup:
            lines.append(
                f"- 소송/분쟁 공시: {lit_dedup.get('primary_count', 0)}건 원본 "
                f"(정정 {lit_dedup.get('correction_excluded', 0)} 제외 / 원문 {lit_dedup.get('raw_count', 0)})"
            )
            inf_m = lit_dedup.get("unspecified_inferred_mgmt", 0)
            inf_c = lit_dedup.get("unspecified_inferred_commercial", 0)
            inf_note = ""
            if inf_m or inf_c:
                inf_note = f" [미상 중 경영권 추정 {inf_m} / 상거래 추정 {inf_c} — 판결 공시는 성격 미표기, 회사단위 추정]"
            lines.append(
                f"  - 성격: 경영권분쟁 {lit_dedup.get('management_count', 0)} (분쟁 신호) / "
                f"상거래 {lit_dedup.get('commercial_count', 0)} (분쟁 아님) / "
                f"미상 {lit_dedup.get('unspecified_count', 0)}{inf_note}"
            )
            lines.append(
                f"  - 단계: 제기 {lit_dedup.get('filed_count', 0)} / 판결 {lit_dedup.get('ruling_count', 0)} / 기타 {lit_dedup.get('other_count', 0)}"
            )
            # LLM 판단 재료 — 공시명 빈도 (정규화)
            freq = lit_dedup.get("report_name_freq") or []
            if freq:
                freq_str = " / ".join(f"{f['name']}×{f['count']}" for f in freq[:8])
                lines.append(f"  - 공시명 빈도: {freq_str}")
        else:
            lines.append(f"- 소송/분쟁 공시: {summary.get('litigation_count', 0)}건")
        ext_n = summary.get("active_external_block_count", 0)
        ovl_n = summary.get("active_overlap_block_count", 0)
        lines.append(
            f"- 능동적 5% 경영참여 신고: 외부세력 {ext_n}건 (분쟁 신호) / "
            f"대주주 본인 {ovl_n}건 (지배 신고, 분쟁 아님)"
        )
        top_holder = summary.get("top_holder", {})
        if top_holder:
            lines.append(f"- 명부상 최대주주: {top_holder.get('name', '')} {top_holder.get('ownership_pct', 0):.2f}%")
        lines.append(f"- 명부상 특수관계인 합계: {summary.get('related_total_pct', 0):.2f}%")
        lines.append(f"- 자사주: {summary.get('treasury_pct', 0):.2f}%")
        lines.extend(["", "## 판 구조", f"- 회사측 제출인: {', '.join(players.get('company_side_filers', [])) or '없음'}"])
        lines.append(f"- 주주측 제출인: {', '.join(players.get('shareholder_side_filers', [])) or '없음'}")
        lines.append(f"- 명부와 안 겹치는 능동 5% 블록: {', '.join(players.get('active_external_blocks', [])) or '없음'}")
        lines.append(f"- 명부와 겹치는 능동 5% 블록: {', '.join(players.get('active_overlap_blocks', [])) or '없음'}")
        if control_context.get("observations"):
            lines.extend(["", "## 관찰 포인트"])
            for item in control_context.get("observations", []):
                lines.append(f"- {item}")

    if scope in {"summary", "fight"}:
        lines.extend(["", "## fight", "| 날짜 | 구분 | 플레이어 분류 | 제출인 | 5%경영참여 | 소송연관 | 공시명 | 공시번호 |", "|------|------|---------------|--------|-----------|----------|--------|----------|"])
        for row in data.get("fight", [])[:20]:
            has_5pct = "✓" if row.get("filer_has_5pct_active_block") else "-"
            in_lit = "✓" if row.get("filer_in_litigation") else "-"
            lines.append(f"| {row['disclosure_date']} | {_ko(_GROUP_KO, row['side'])} | {_ko(_GROUP_KO, row.get('actor_group'))} | {row['filer_name']} | {has_5pct} | {in_lit} | {row['report_name']} | `{row['rcept_no']}` |")

    if scope in {"summary", "litigation"}:
        lit_dedup = summary.get("litigation_dedup") or {}
        doc_resolved = lit_dedup.get("document_resolved")
        title = "## litigation (정정 제외 원본)"
        if doc_resolved:
            title += f" — 본문 사건명으로 성격 미상 {doc_resolved}건 재분류"
        lines.extend(["", title,
                      "| 날짜 | 성격 | 단계 | 사건명 | 사건번호 | 원고ㆍ신청인 | 공시번호 |",
                      "|------|------|------|--------|----------|--------------|----------|"])
        _lit_type_ko = {"filed": "제기", "ruling": "판결", "other": "기타"}
        _kind_ko = {"management": "경영권", "commercial": "상거래", "unspecified": "미상"}
        # 본문을 열어 사건명을 못 얻은 이유. **「미조회」는 여기 없다** — 그때는 공시명을
        # 그대로 보여주고, 본문을 여는 방법을 표 아래에 한 줄로 안내한다(260828).
        _doc_ko = {
            "case_name_absent": "공시 본문에 사건명 미기재 (회사가 「추후 정정기재」로 비움)",
            "form_unrecognized": "본문이 아는 서식이 아님 — 원문 직접 확인",
            "fetch_failed": "본문 조회 실패",
        }
        detail_rows: list[dict] = []
        for row in data.get("litigation", [])[:20]:
            lit_type = _lit_type_ko.get(row.get("litigation_type", ""), "-")
            kind = _kind_ko.get(row.get("dispute_kind", ""), "-")
            doc_status = row.get("document_status", "not_looked_up")
            # 본문 사건명이 있으면 그것을 쓴다. 없으면 **왜 없는지**를 쓴다 —
            # 「미기재」(회사가 안 적음)와 「미조회/실패」(우리가 못 읽음)는 뜻이 다르다(260828).
            reason = _doc_ko.get(doc_status)
            case_name = row.get("case_name") or (f"_{reason}_" if reason else row["report_name"])
            marker = " 📄" if row.get("dispute_kind_source") == "document" else ""
            parties = row.get("parties") or ("-" if doc_status == "parsed" else "")
            lines.append(
                f"| {row['disclosure_date']} | {kind}{marker} | {lit_type} | {case_name} | "
                f"{row.get('case_number', '')} | {parties[:60]} | `{row['rcept_no']}` |")
            if row.get("case_fields") or row.get("case_excerpt"):
                detail_rows.append(row)

        if scope != "litigation":
            lines.append("")
            lines.append("> 사건명·원고·청구내용은 공시 **본문**에 있다. 여기서는 본문을 열지 않았다 — "
                         "소송 상세 조회(litigation)로 부르면 사건명·사건번호·원고ㆍ신청인과 "
                         "청구내용 원문을 함께 싣는다.")

        counts = []
        if lit_dedup.get("case_name_from_document") is not None:
            counts.append(f"사건명 확보 {lit_dedup['case_name_from_document']}건")
        for key, label in (("case_name_absent_in_document", "공시 본문에 사건명 미기재"),
                           ("document_fetch_failed", "본문 조회 실패"),
                           ("document_not_looked_up", "본문 미조회(조회 상한 초과)")):
            if lit_dedup.get(key):
                counts.append(f"{label} {lit_dedup[key]}건")
        if counts:
            lines.append("")
            lines.append(f"> 본문 파싱: {' · '.join(counts)}. "
                         "사건명 칸이 비어 있으면 회사가 「추후 정정기재」로 남긴 것이며 파싱 실패가 아니다.")

        # 원문 발췌 — 값을 요약하지 않고 공시 본문 그대로 싣는다. 인용해도 되는 문자열이다.
        if scope == "litigation" and detail_rows:
            lines.extend([
                "",
                "## 사건별 공시 원문 (발췌)",
                "아래 값은 **DART 공시 본문에서 그대로 잘라낸 원문**이다 — 요약·재작성하지 않았다. "
                "누가 누구를 상대로 무엇을 청구했는지는 `청구내용`/`판결ㆍ결정내용`을 읽어 판단한다. "
                "전문은 `evidence` 로 뷰어 URL을 얻어 확인한다.",
            ])
            for row in detail_rows:
                fields = row.get("case_fields") or {}
                head = fields.get("case_name") or row["report_name"]
                num = f" ({fields['case_number']})" if fields.get("case_number") else ""
                lines.append("")
                lines.append(f"### {row['disclosure_date']} {head}{num} · `{row['rcept_no']}`")
                for key in ("parties", "court", "filed_date", "decided_date",
                            "claim", "ruling", "ruling_reason", "other_material",
                            "related_filings"):
                    value = fields.get(key)
                    if not value:
                        continue
                    label = _LIT_FIELD_LABELS_KO.get(key, key)
                    lines.append(f"- **{label}**: {value[:900]}")
                absent = row.get("absent_fields") or []
                if absent:
                    lines.append(f"- _공시 본문에 미기재(회사가 「-」로 비움): {', '.join(absent)}_")

    if scope in {"summary", "signals"}:
        lines.extend(["", "## 5% signals", "| 날짜 | 보고자 | 분류 | 지분율 | 목적 | 공시번호 |", "|------|--------|------|--------|------|----------|"])
        for row in data.get("signals", [])[:20]:
            lines.append(f"| {row['report_date']} | {row['reporter']} | {_ko(_GROUP_KO, row.get('actor_side'))} | {row['ownership_pct']:.2f}% | {row['purpose']} | `{row['rcept_no']}` |")

        dynamics = data.get("block_holder_dynamics", [])
        if dynamics:
            lines.extend([
                "",
                "## 5% 보유 동학 (시계열)",
                "보고자별 목적 전환 / 지속 추가매입 / 보고 빈도. 분쟁 강도 자동 판정은 하지 않는다 — 변화만 노출.",
                "",
                "| 보고자 | 보고 | 기간 | 현재 목적 | 목적 전환 | 지분 추세 |",
                "|--------|------|------|-----------|-----------|-----------|",
            ])
            for d in dynamics[:15]:
                acc = d.get("accumulation", {})
                ps = d.get("purpose_shift")
                shift = f"{ps['from']}→{ps['to']} ({ps['date']})" if ps else "-"
                # 급변(±5%p) 강조 — 매집(↑) / exit·매각(↓)
                abrupt = "⚡" if acc.get("abrupt_change") else ""
                arrow = {"increasing": "↑", "decreasing": "↓", "flat": ""}.get(acc.get("direction", ""), "")
                trend = (
                    f"{abrupt}{acc.get('first_pct', 0):.2f}% → {acc.get('last_pct', 0):.2f}% "
                    f"({acc.get('change_pp', 0):+.2f}%p {arrow})"
                )
                lines.append(
                    f"| {d['reporter']} | {d['report_count']}회 | {d.get('first_date','')}~{d.get('last_date','')} "
                    f"| {d.get('current_purpose','')} | {shift} | {trend} |"
                )

    if scope in {"summary", "signals", "insiders"}:
        lines.extend(_render_insiders(data, scope))

    if scope == "timeline":
        lines.extend(["", "## timeline", "| 날짜 | 카테고리 | 주체 | 분류 | 이벤트 | 공시번호 |", "|------|----------|------|------|--------|----------|"])
        for row in data.get("timeline", [])[:30]:
            lines.append(f"| {row['date']} | {_ko(_CATEGORY_KO, row['category'])} | {row.get('actor', '')} | {_ko(_GROUP_KO, row.get('side'))} | {row['title']} | `{row['rcept_no']}` |")

    if scope == "vote_math":
        vote_math = data.get("vote_math", {})
        attendance = vote_math.get("attendance_estimate", {})
        capital = vote_math.get("capital_structure", {})
        pressure = vote_math.get("pressure_signals", {})
        interpretation = vote_math.get("interpretation", {})
        meeting_ref = vote_math.get("meeting_reference", {})

        lines.extend(["", "## 표 계산 기준 회차"])
        lines.append(f"- 회차 구분: {_MEETING_TYPE_KO.get(meeting_ref.get('meeting_type'), '-')}")
        lines.append(f"- 주총일: {meeting_ref.get('meeting_date') or '-'}")
        lines.append(f"- 결과 공시 접수번호: {meeting_ref.get('result_rcept_no') or '-'}")
        lines.append(f"- 결과 확보 상태: {_RESULT_STATUS_KO.get(meeting_ref.get('result_status'), '-')}")

        lines.extend(["", "## 대표 추정참석률"])
        lines.append(f"- 대표 추정참석률: {attendance.get('representative_pct') if attendance.get('representative_pct') is not None else '-'}%")
        lines.append(f"- 비교 가능한 보통결의 안건 수: {attendance.get('comparable_item_count', 0)}건")
        lines.append(f"- 제외 안건 수: {attendance.get('excluded_item_count', 0)}건")
        if attendance.get("min_pct") is not None and attendance.get("max_pct") is not None:
            lines.append(f"- 안건별 추정참석률 범위: {attendance.get('min_pct')}% ~ {attendance.get('max_pct')}%")
        lines.append(f"- 방법론: {attendance.get('methodology', '-')}")

        lines.extend(["", "## 표 구조"])
        lines.append(f"- 특수관계인 합계: {capital.get('related_total_pct', 0)}%")
        lines.append(f"- 자사주: {capital.get('treasury_pct', 0)}%")
        lines.append(f"- 의결권 기준 모수(자사주 차감 후): {capital.get('voting_share_base_pct', 0)}%")
        lines.append(f"- 특수관계인 제외 추정 참석분: {capital.get('contestable_turnout_pct') if capital.get('contestable_turnout_pct') is not None else '-'}%")
        lines.append(f"- 특수관계인 제외 추정 참석률: {capital.get('ex_related_turnout_pct') if capital.get('ex_related_turnout_pct') is not None else '-'}%")
        lines.append(f"- 명부와 안 겹치는 능동 블록 합계: {capital.get('active_external_block_total_pct', 0)}%")
        lines.append(f"- 명부와 겹치는 능동 블록 합계: {capital.get('active_overlap_block_total_pct', 0)}%")

        lines.extend(["", "## 압박 신호"])
        lines.append(f"- 주주측 제출인: {', '.join(pressure.get('shareholder_side_filers', [])) or '없음'}")
        lines.append(f"- 소송/분쟁 공시 수: {pressure.get('litigation_count', 0)}건")
        lines.append(f"- 고반대율 안건 수(10%+): {len(pressure.get('high_opposition_items', []))}건")
        lines.append(f"- 부결 안건 수: {len(pressure.get('failed_items', []))}건")
        lines.append(f"- 표 대결 신호: {_SIGNAL_LEVEL_KO.get(interpretation.get('signal_level'), '-')}")

        if pressure.get("high_opposition_items"):
            lines.extend(["", "## 고반대율 안건"])
            for item in pressure.get("high_opposition_items", [])[:10]:
                lines.append(f"- {item.get('number', '')} {item.get('agenda', '')} / 반대율 {item.get('opposition_rate', 0)}%")

        if attendance.get("items"):
            lines.extend(["", "## 비교에 사용한 안건"])
            for item in attendance.get("items", [])[:10]:
                lines.append(
                    f"- {item.get('number', '')} {item.get('agenda', '')} / 결의 {item.get('resolution_type', '-')}"
                    f" / 추정참석률 {item.get('estimated_attendance', '-') }%"
                )

        if interpretation.get("notes"):
            lines.extend(["", "## 해석 메모"])
            for note in interpretation.get("notes", []):
                lines.append(f"- {note}")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def proxy_contest(
        company: str,
        scope: str = "summary",
        year: int = 0,
        start_date: str = "",
        end_date: str = "",
        lookback_months: int = 12,
        insider_rows_limit: int = 500,
        format: str = "md",
    ) -> str:
        """desc: 위임장·소송·5% 경영참여 시그널 통합. **자동 분류 X 힌트 제공** (filer_has_5pct_active_block 등). 애널리스트 종합 판단.
        when: 경영권 분쟁, 주주 캠페인, 소송, 능동적 5% 보유, 표 대결 신호.
        rule: DART D/B/I만 (KIND false match 위험). 위임장 filer 3-way: company/shareholder/retail_activism(컨두잇·헤이홀더 등). has_contest_signal은 shareholder OR litigation OR external_active_block만. vote_math는 보수적, 승패 예측 X.
        scope: `summary` / `fight` 위임장+힌트 / `litigation` 소송 **+ 공시 본문 원문 발췌** / `signals` 5% 대량보유 / `insiders` 임원·주요주주 소유상황(D002) 상세 / `timeline` 전 이벤트 / `vote_math` 표 구조
        insiders: `summary`·`signals`·`insiders` 는 **임원·주요주주 특정증권등 소유상황보고(D002)** 를 함께 싣는다
          (`data.insider_holdings`). 5% 대량보유는 5% 이상만 잡으므로, **문턱 아래에서 지배주주·특수관계인이
          조금씩 사 모으는 움직임은 여기에만 남는다.** 보고자 단위로 접어 「누가 · 기간 · 순증감 · 최근 90일
          매집 여부 · 명부/5%블록 대조」를 준다. `in_registry`/`in_5pct_block` 은 사실 플래그일 뿐 분쟁 판정이
          아니다 — 임원 보고에는 스톡옵션 행사·상속도 섞이므로 사유는 `evidence(rcept_no=...)` 로 원문을 확인하라.
          **`insider_status` 를 읽어라**: `ok` / `no_data`(보고 자체가 없다 — 조회는 성공) /
          `fetch_failed`(못 읽었다 — 「없다」가 아니다). 보고가 폭주하는 회사(삼성전자 12개월 2,700건+)는
          `insider_rows_limit`(기본 500, 최대 5000) 상한에 걸리며 **걸리면 경고로 알린다** — 조용히 자르지 않는다.
          값이 「미기재」면 회사가 공시에 안 적은 것이지 0이 아니다.
        litigation: **공시명만으로 판단하지 마라.** `scope=litigation` 은 소송 공시 본문을 전건 열어
          `case_name`(사건의 명칭) · `case_number` · `parties`(원고ㆍ신청인) · `case_fields.claim`(청구내용) ·
          `case_fields.ruling`(판결ㆍ결정내용) · `court` 를 **원문 그대로** 싣는다. 이 값들은 요약이 아니라
          공시 본문에서 잘라낸 문자열이므로 **그대로 인용해도 된다.** 「누가 누구를 상대로 무엇을
          청구했나」는 `claim`/`ruling` 원문에 있다 — 표의 사건명만 읽고 결론내지 마라.
          `document_status` 를 반드시 읽어라: `parsed`(읽음) / `case_name_absent`(**회사가 본문에
          안 적었다** — 우리 파싱 실패가 아니다. 회사가 「추후 정정기재」로 남긴 칸이다) /
          `fetch_failed`(본문 조회 실패) / `not_looked_up`(다른 scope 라 본문을 안 열었다).
          `absent_fields` 는 회사가 「-」로 비워 둔 항목이다. **없는 값을 추정으로 채우지 마라.**
          `dispute_kind` 가 `unspecified` 인 것은 「분쟁이 아니다」가 아니라 「우리가 분류를 보류했다」이다 —
          원문을 읽고 네가 판단하라. 전문이 필요하면 `evidence(rcept_no=...)` 로 뷰어 URL을 얻어라.
        ref: shareholder_meeting_notice, ownership_structure, company, evidence
        """
        payload = await build_proxy_contest_payload(
            company,
            scope=scope,
            year=year or None,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
            insider_rows_limit=insider_rows_limit,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") in {"error", "requires_review"} and scope == "vote_math":
            return _render_error(payload, scope=scope)
        if payload.get("status") == "error":
            return _render_error(payload, scope=scope)
        return _render(payload, scope)
