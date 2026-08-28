"""ownership_structure public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.tools._shared import company_id_line
from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# ownership_structure: {payload.get('subject', '')}", "", "지분 구조를 확정하지 못했다."]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# ownership_structure: {data.get('query', payload.get('subject', ''))}",
        "",
        "회사 식별이 애매해 지분 구조를 자동 선택하지 않았다.",
        "",
        "| 회사명 | ticker | corp_code | company_id |",
        "|------|--------|-----------|------------|",
    ]
    for item in data.get("candidates", []):
        lines.append(
            f"| {item.get('corp_name', '')} | `{item.get('ticker', '')}` | `{item.get('corp_code', '')}` | `{item.get('company_id', '')}` |"
        )
    return "\n".join(lines)


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    summary = data.get("summary", {})
    window = data.get("window", {})
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 지분 구조", ""]
    _cid = company_id_line(data)
    if _cid:
        lines.append(_cid)
    if data.get("as_of_date"):
        lines.append(f"- as_of_date: `{data.get('as_of_date', '')}`")
    if window:
        lines.append(f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`")
    lines.append("")
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    if scope == "summary":
        top = summary.get("top_holder") or {}
        blocks = data.get("blocks", []) or []
        top_block = blocks[0] if blocks else {}
        # 요약 + 100% 구성을 한 블록으로. 고유 정보(단독 최대주주·5% 실세)는 캡션 2줄로,
        # 중복되는 특관 합계·자사주 수치는 아래 100% 표가 흡수한다.
        lines.append("## 지분 구성")
        lines.append(f"- 명부상 최대주주(본인 단독): {top.get('name', '-') or '-'} {top.get('ownership_pct', 0):.2f}%")
        if top_block:
            _tb_self = top_block.get("reporter_self_pct")
            _tb_self_txt = (
                f", 그중 보고자 본인 {_tb_self:.2f}%" if _tb_self is not None else ""
            )
            lines.append(
                f"- 5% 대량보유 실세: {top_block.get('reporter', '-')} "
                f"{top_block.get('ownership_pct', 0):.2f}% ({top_block.get('purpose', '')}) "
                f"— 본인 + 특별관계자 합산{_tb_self_txt}"
            )
        _camps = (data.get("block_camps") or {}).get("camps") or []
        if any((c.get("block_count") or 1) > 1 for c in _camps):
            _top_camp = _camps[0]
            if _top_camp.get("net_pct") is not None:
                lines.append(
                    f"- 겹치는 몫을 걷어낸 최대 진영: {_top_camp.get('label','')} "
                    f"{_top_camp['net_pct']:.2f}% (아래 진영별 표)"
                )
        # 100% 정합 분해 — 명부(본인+특관)/자사주/기타로 발행총수를 중복 없이 나눈다.
        # 5% 대량보유는 보고자 공동보유·중복이라 합산 100%가 안 되므로 여기엔 쓰지 않는다.
        _tr = data.get("treasury", {})
        issued = _tr.get("issued_shares", 0)
        major_sh = sum(r.get("shares", 0) for r in data.get("major_holders", []))
        tr_sh = summary.get("treasury_shares", 0)
        other = issued - major_sh - tr_sh
        if issued and other >= 0:
            lines.extend([
                "", "| 구분 | 보유주식수 | 지분율 |", "|------|-----------|--------|",
                f"| 최대주주+특수관계인 | {major_sh:,} | {major_sh / issued * 100:.2f}% |",
                f"| 자사주 | {tr_sh:,} | {tr_sh / issued * 100:.2f}% |",
                f"| 기타(소액주주·기관 등) | {other:,} | {other / issued * 100:.2f}% |",
                f"| **합계(발행주식총수)** | **{issued:,}** | **100.00%** |",
            ])
        elif issued and other < 0:
            lines.append("- (100% 분해 생략: 명부+자사주가 보통주 발행총수를 초과 — 우선주 등 분모 불일치)")
        else:  # issued == 0
            lines.append("- (100% 분해 생략: 발행주식총수 미확보 — 집합투자기구(인프라펀드 등)이거나 주식총수 미공시)")

    if scope in {"summary", "major_holders", "control_map"}:
        rows = data.get("major_holders", [])
        # summary는 노이즈 컷: 0.1% 미만 군소 특관은 '외 N인'으로 접는다. major_holders scope는 전체 노출.
        if scope == "summary":
            shown = [r for r in rows if r.get("ownership_pct", 0) >= 0.1][:20]
            hidden = [r for r in rows if r.get("ownership_pct", 0) < 0.1]
        else:
            shown, hidden = rows[:20], []
        lines.extend(["", "## 최대주주/특수관계인", "| 이름 | 관계 | 지분율 | 보유주식수 |", "|------|------|--------|-----------|"])
        for row in shown:
            lines.append(f"| {row['name']} | {row['relation'] or '-'} | {row['ownership_pct']:.2f}% | {row['shares']:,} |")
        if hidden:
            hidden_pct = sum(r.get("ownership_pct", 0) for r in hidden)
            hidden_shares = sum(r.get("shares", 0) for r in hidden)
            lines.append(f"| 외 {len(hidden)}인 (0.1% 미만) | 특수관계인 | {hidden_pct:.2f}% | {hidden_shares:,} |")

    if scope in {"summary", "blocks", "control_map"}:
        blocks = data.get("blocks", []) or []
        lines.extend([
            "", "## 5% 대량보유 최신",
            "> 지분율은 **보고자 본인 + 특별관계자 합산**이다. 「본인」은 보고자가 직접 가진 몫.",
            "> 보고자끼리 같은 특별관계자를 품고 있을 수 있어 이 표의 지분율은 더하면 안 된다.",
            "",
            "| 보고자 | 지분율(본인+특관) | 본인 | 보유목적 | 날짜 | 공시번호 |",
            "|--------|------------------|------|----------|------|----------|",
        ])
        for row in blocks[:15]:
            _self = row.get("reporter_self_pct")
            self_cell = "미확인" if _self is None else f"{_self:.2f}%"
            if _self is not None and _self == 0:
                self_cell = "0.00% ⚠"
            lines.append(
                f"| {row['reporter']} | {row['ownership_pct']:.2f}% | {self_cell} | "
                f"{row['purpose']} | {row['report_date']} | `{row['rcept_no']}` |"
            )
        # 본인 지분이 0 인 보고자 — 「41.13%를 보고한 주체가 한 주도 없다」가 무슨 뜻인지 적는다.
        # 왜 0 인지는 대량보유보고서만으로 알 수 없어 단정하지 않는다.
        for row in blocks[:15]:
            if row.get("reporter_self_note"):
                lines.append("")
                lines.append(f"- ⚠ **{row['reporter']} 본인 0.00%** — {row['reporter_self_note']} "
                             f"(원문 공시번호 `{row.get('rcept_no','')}`)")
        # 공동보유자 분해 — 헤드라인 지분율은 보고자 본인+특별관계자 합산이라, 누가 얼마씩인지 표기.
        co_blocks = [r for r in blocks[:15] if r.get("co_holders")]
        if co_blocks:
            lines.append("")
            lines.append("## 공동보유자 분해 (보고자 본인 vs 특별관계자)")
            lines.append("> 5% 보고 지분율 = 보고자 본인 + 특별관계자 **합산**. 아래는 그 내역이다.")
            lines.append("> 0.00% 보유자는 접었다 — `format=\"json\"` 으로 부르면 전원 다 나온다.")
            for r in co_blocks:
                verified = r.get("co_holders_verified")
                vtag = "" if verified else "  ⚠합계가 보고 지분율과 안 맞는다(원문 대조 필요)"
                lines.append("")
                lines.append(f"### {r['reporter']} {r['ownership_pct']:.2f}% "
                             f"(본인 {r.get('reporter_self_pct')}% + 특관, 합산 {r.get('co_holders_total_pct')}%){vtag}")
                lines.append("| 공동보유자 | 지분율 | 명부 최대주주 |")
                lines.append("|------------|--------|----------------|")
                lines.append(f"| {r['reporter']} (보고자 본인) | {r.get('reporter_self_pct')}% | - |")
                _co = sorted(r["co_holders"], key=lambda x: -(x.get("ownership_pct") or 0))
                _zero = [c for c in _co if round(c.get("ownership_pct") or 0, 2) == 0]
                for ch in _co:
                    if round(ch.get("ownership_pct") or 0, 2) == 0:
                        continue
                    reg = "✓" if ch.get("is_registry_holder") else ""
                    lines.append(f"| {ch.get('name','')} | {ch.get('ownership_pct')}% | {reg} |")
                if _zero:
                    _zero_reg = sum(1 for c in _zero if c.get("is_registry_holder"))
                    _reg_cell = f"{_zero_reg}명 ✓" if _zero_reg else ""
                    lines.append(f"| +{len(_zero)}명 (각 0.0%) | 0.0% | {_reg_cell} |")

        # 진영별 순 지분 — 5% 보고를 그냥 더하면 100%를 넘는 문제(같은 주식의 이중 신고)를 푼다.
        camps_data = data.get("block_camps") or {}
        camps = camps_data.get("camps") or []
        _merged = any((c.get("block_count") or 1) > 1 for c in camps)
        if camps and (_merged or camps_data.get("exceeds_100")):
            lines.append("")
            lines.append("## 진영별 순 지분 (겹치는 몫을 한 번만 계산)")
            lines.append(
                f"> 위 5% 보고를 그냥 더하면 {camps_data.get('headline_total_pct', 0):.2f}%다 — "
                "보고자들이 같은 특별관계자를 서로 품고 있어 같은 주식이 여러 번 신고된 결과다."
            )
            lines.append("> 아래는 겹치는 보고자를 한 편으로 묶고, 한 편 안에서 같은 이름을 한 번만 센 값이다.")
            lines.append("")
            lines.append("| 진영 | 보고 합산 | 순 지분 | 어떻게 구했나 |")
            lines.append("|------|-----------|---------|----------------|")
            for camp in camps:
                net = camp.get("net_pct")
                net_cell = "계산 불가" if net is None else f"**{net:.2f}%**"
                lines.append(
                    f"| {camp.get('label','')} | {camp.get('headline_sum_pct', 0):.2f}% | "
                    f"{net_cell} | {camp.get('net_basis','')} |"
                )
            _net_total = camps_data.get("net_total_pct")
            if _net_total is not None:
                lines.append(
                    f"| **합계** | {camps_data.get('headline_total_pct', 0):.2f}% | "
                    f"**{_net_total:.2f}%** | 5% 보고가 잡아낸 몫만. 나머지는 5% 미만 주주 |"
                )
            pairs = camps_data.get("shared_holders_between_reporters") or []
            if pairs:
                lines.append("")
                lines.append("**어느 보고자끼리 누구를 함께 안고 있나**")
                for pair in pairs:
                    who = " ↔ ".join(pair.get("reporters", []))
                    held = pair.get("shared_holders", [])
                    shown = ", ".join(
                        f"{h.get('name','')} {h.get('ownership_pct', 0):.2f}%" for h in held[:4]
                    )
                    more = f" 외 {len(held) - 4}명" if len(held) > 4 else ""
                    lines.append(f"- {who}: {shown}{more}")

    # 자사주는 요약줄 + 100% 지분 구성표에 이미 노출되므로 별도 섹션은 두지 않는다(중복 제거).
    # 자사주 상세(취득/처분 이력 등)는 treasury_share tool.

    if scope == "blocks":
        # blocks scope에 timeline 통합 노출
        timeline = data.get("timeline", []) or []
        if timeline:
            lines.extend(["", "## 5% 대량보유 이력 (timeline)", "| 날짜 | 보고자 | 지분율 | 목적 | 공시번호 |", "|------|--------|--------|------|----------|"])
            for row in timeline[:30]:
                lines.append(f"| {row['report_date']} | {row['reporter']} | {row['ownership_pct']:.2f}% | {row['purpose']} | `{row['rcept_no']}` |")

    if scope == "changes":
        change_filings = data.get("change_filings", [])
        lines.extend(["", "## 최대주주등 소유주식 변동신고서"])
        if not change_filings:
            lines.append("- 조사 구간 내 변동신고서 없음")
        for filing in change_filings:
            rcept_dt = filing.get("rcept_dt", "")
            rcept_no = filing.get("rcept_no", "")
            ov = filing.get("overview", {})
            lines.append(f"\n### {rcept_dt} ({rcept_no})")
            if filing.get("parse_error"):
                lines.append(f"- 파싱 오류: {filing['parse_error']}")
                continue
            if ov:
                before_pct = ov.get("before_pct", 0)
                after_pct = ov.get("after_pct", 0)
                before_shares = ov.get("before_shares", 0)
                after_shares = ov.get("after_shares", 0)
                delta_pct = round(after_pct - before_pct, 2)
                lines.append(f"- 직전: {before_shares:,}주 ({before_pct:.2f}%) → 금번: {after_shares:,}주 ({after_pct:.2f}%) / 순변동: {delta_pct:+.2f}%p")
            for holder in filing.get("individual_changes", []):
                name = holder.get("holder_name", "")
                changes = holder.get("changes", [])
                if not changes:
                    continue
                lines.append(f"\n**{name}** 개인별 변동")
                lines.append("| 변경일 | 변경원인 | 주식종류 | 변경전 | 증감 | 변경후 |")
                lines.append("|--------|----------|----------|--------|------|--------|")
                for row in changes:
                    lines.append(f"| {row['date']} | {row['reason']} | {row['stock_type']} | {row['before']:,} | {row['delta']:+,} | {row['after']:,} |")
            total_holders = filing.get("total_holders", [])
            if total_holders:
                lines.extend(["\n**총괄현황** (금번 기준)", "| 성명 | 관계 | 보통주수 | 비율 |", "|------|------|---------|------|"])
                for th in total_holders:
                    lines.append(f"| {th['name']} | {th['relation'] or '-'} | {th['shares']:,} | {th['pct']:.2f}% |")

        # 5% 대량보유 변동 — 분쟁사(고려아연 등)는 최대주주변동신고서 대신 이쪽으로 지분이 움직인다.
        block_changes = data.get("block_changes", []) or []
        lines.extend(["", "## 5% 대량보유 변동 (주식등의대량보유상황보고서)"])
        if not block_changes:
            lines.append("- 조사 구간 내 5% 대량보유 변동 없음")
        else:
            lines.extend(["| 날짜 | 보고자 | 지분율 | 목적 | 공시번호 |", "|------|--------|--------|------|----------|"])
            for row in block_changes[:30]:
                lines.append(f"| {row['report_date']} | {row['reporter']} | {row['ownership_pct']:.2f}% | {row['purpose']} | `{row['rcept_no']}` |")

    if scope == "control_map":
        control_map = data.get("control_map", {})
        core = control_map.get("core_holder_block", {})
        top = core.get("top_holder") or {}
        treasury = control_map.get("treasury_block", {})
        flags = control_map.get("flags", {})

        lines.extend([
            "",
            "## control_map 요약",
            f"- 명부상 최대주주: {top.get('name', '-') or '-'} {top.get('ownership_pct', 0):.2f}%",
            f"- 명부상 특수관계인 합계: {core.get('related_total_pct', 0):.2f}%",
            f"- 자사주: {treasury.get('shares', 0):,}주 ({treasury.get('pct', 0):.2f}%)",
            f"- 비중 플래그: 50% 이상={flags.get('registry_majority', False)}, 30% 이상={flags.get('registry_over_30pct', False)}, 자사주 5% 이상={flags.get('treasury_over_5pct', False)}",
        ])

        observations = control_map.get("observations", [])
        if observations:
            lines.extend(["", "## 관찰 포인트"])
            for item in observations:
                lines.append(f"- {item}")

        lines.extend(["", "## 명부와 겹치지 않는 능동적 5% 블록", "| 보고자 | 지분율 | 목적 | 날짜 |", "|--------|--------|------|------|"])
        active_non_overlap_blocks = control_map.get("active_non_overlap_blocks", [])
        if active_non_overlap_blocks:
            for row in active_non_overlap_blocks[:10]:
                lines.append(f"| {row['reporter']} | {row['ownership_pct']:.2f}% | {row['purpose']} | {row['report_date']} |")
        else:
            lines.append("| - | - | - | - |")

        lines.extend(["", "## 명부와 이름이 겹치는 5% 블록", "| 보고자 | 지분율 | 목적 | 명부상 이름 | 날짜 |", "|--------|--------|------|-------------|------|"])
        overlap_blocks = control_map.get("overlap_blocks", [])
        if overlap_blocks:
            for row in overlap_blocks[:10]:
                lines.append(
                    f"| {row['reporter']} | {row['ownership_pct']:.2f}% | {row['purpose']} | {row.get('matched_major_holder') or '-'} | {row['report_date']} |"
                )
        else:
            lines.append("| - | - | - | - | - |")

        notes = control_map.get("notes", [])
        if notes:
            lines.extend(["", "## 해석 유의사항"])
            for note in notes:
                lines.append(f"- {note}")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def ownership_structure(
        company: str,
        scope: str = "summary",
        year: int = 0,
        as_of_date: str = "",
        start_date: str = "",
        end_date: str = "",
        format: str = "md",
    ) -> str:
        """desc: 최대주주·특수관계인·5% 대량보유 지분 구조 + **공동보유자 분해**. 자사주 detail은 `treasury_share` 별도.
        when: 지배력 구조, 최대주주 비중, 특수관계인 지분 합, 5% 활성 시그널, **"OO의 N% 지분이 누구누구 공동보유냐 / 보고자 본인 지분은 얼마냐"** 질의.
        rule: 사업보고서 DART 공식 API 우선. 5% 대량보유 목적은 최신 원문 보강. 변동신고서는 DART API 우선, KIND fallback. 5% 보고 헤드라인 지분율(ownership_pct)은 **보고자 본인 + 특별관계자 합산**임 — 본인만 보려면 `reporter_self_pct`, 공동보유자 내역은 `co_holders`[{name, ownership_pct, is_registry_holder}] 사용. `co_holders_verified=False`면 합계 미검증이라 원문 대조 필요(확정 인용 금지). 합계표 없는 약식보고(기관 단순투자 등)는 co_holders=None. **5% 보고 지분율끼리 더하지 말 것** — 보고자들이 같은 특별관계자를 공유해 같은 주식이 중복 신고된다(고려아연 단순합 111.84%). 중복을 걷어낸 진영별 순 지분은 `block_camps.camps[].net_pct`, 겹침 내역은 `block_camps.shared_holders_between_reporters`, 총합은 `block_camps.net_total_pct`(null이면 계산 불가 — 지어내지 말 것). `reporter_self_pct=0`이면 `reporter_self_note`에 뜻이 담긴다(보고자 본인 직접 보유 없음; 사유는 본 보고서로 확정 불가). DART 013/014/404는 실패가 아니라 「해당 자료 없음」으로 표기된다.
        scope: `summary` 최대주주+5%블록(+공동보유자 분해)+자사주 snapshot / `major_holders` 특수관계인 detail / `blocks` 5% 대량보유 최신+이력+공동보유자 분해 / `control_map` 3대 카테고리(명부 등재/외부 능동/수동)+공동보유자 / `changes` 최대주주변동신고서(I004) + 5% 대량보유 변동(D001) 통합
        ref: treasury_share, proxy_contest, evidence
        """
        payload = await build_ownership_structure_payload(
            company,
            scope=scope,
            year=year or None,
            as_of_date=as_of_date,
            start_date=start_date,
            end_date=end_date,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload, scope)
