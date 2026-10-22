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


def _denominator_lines(sb: dict[str, Any], listed_col: bool | None = None) -> list[str]:
    """100% 분해 표 아래 — 분모가 어느 정기보고서·어느 결산기준일 값인지, 최신 상장주식수와 다르면 두 값과 차이.

    listed_col: 「지분율(최신 상장주식수 기준)」 칸을 붙였는가. True 붙임 · False 붙일 수 없었음(명부+자사주가 상장주식수 초과) ·
    None 표 자체가 없어 칸 이야기를 하지 않음."""
    if not sb:
        return []
    src = [f"{sb['bsns_year']} {sb.get('report') or '정기보고서'}" if sb.get("bsns_year") else (sb.get("report") or "정기보고서")]
    if sb.get("rcept_no"):
        src.append(f"접수번호 `{sb['rcept_no']}`")
    if sb.get("stlm_dt"):
        src.append(f"결산기준일 {sb['stlm_dt']}")
    klass = {"보통주": "보통주", "합계-우선주": "합계 행에서 우선주를 뺀 보통주"}.get(sb.get("stock_class") or "", "")
    out = ["", f"> 분모: {klass + ' ' if klass else ''}발행주식총수 {sb.get('issued_shares', 0):,}주 — " + " · ".join(src) + " (DART stockTotqySttus)."]
    cmp_ = sb.get("comparison")
    listed = sb.get("listed") or {}
    if cmp_ in ("differs", "unit_changed"):
        dd = listed.get("price_dd") or ""
        dd_txt = f"{dd[:4]}-{dd[4:6]}-{dd[6:]}" if len(dd) == 8 else dd
        gap = (
            f"> 최신 상장주식수(KRX 보통주 코드, {dd_txt} 거래일) {listed.get('shares', 0):,}주와 다르다 — "
            f"보고서 {sb.get('issued_shares', 0):,}주 대 {listed.get('shares', 0):,}주, 차이 {sb.get('diff_shares', 0):+,}주"
            f"(상장주식수 대비 {sb.get('diff_pct', 0):+.2f}%). "
        )
        if cmp_ == "unit_changed":
            out.append(
                gap + "분할·병합 등으로 주식 수 단위가 달라졌을 수 있어 상장주식수 기준 지분율은 계산하지 않았다 "
                "— 보유주식수·자사주는 보고서 결산기준일 단위 그대로다."
            )
        elif listed_col is True:
            out.append(
                gap + "보고서 결산기준일 뒤 소각·증자 등으로 갈렸을 수 있다 "
                "— 원인은 이 표로 알 수 없다. 위 오른쪽 칸은 보유주식수·자사주를 보고서 수치 그대로 최신 상장주식수로 나눈 값이다."
            )
        elif listed_col is False:
            out.append(
                gap + "보유주식수와 자사주의 합이 최신 상장주식수를 넘어 상장주식수 기준 지분율은 계산하지 않았다 "
                "— 소각·감자 등으로 주식 수가 달라졌을 수 있고 원인은 이 표로 알 수 없다."
            )
        else:
            out.append(gap + "보고서 결산기준일 뒤 소각·증자 등으로 갈렸을 수 있다 — 원인은 이 표로 알 수 없다.")
    elif cmp_ == "past_year":
        out.append(
            f"> 과거 사업연도({sb.get('bsns_year') or '-'}) 조회라 오늘의 최신 상장주식수와 비교하지 않았다 "
            "— 그 뒤 분할·병합·증자·소각으로 주식 수가 달라졌을 수 있다."
        )
    elif cmp_ == "mixed_class":
        out.append("> 분모에 보통주 외 주식이 섞였을 수 있어 보통주 상장주식수와 섞어 비교하지 않았다.")
    elif cmp_ == "listed_unavailable":
        out.append("> 최신 상장주식수는 읽지 못했다(시세 자료 없음) — 상장주식수와의 비교는 생략했다.")
    return out


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
        sb = data.get("share_basis") or {}
        # 차이가 소각·증자 정도(comparison=differs)일 때만 후보다. 분할·병합 등으로 크게 갈린 경우(unit_changed)는 칸을 붙이지 않는다.
        _listed = (sb.get("listed") or {}) if sb.get("comparison") == "differs" else {}
        _ls = _listed.get("shares") or 0
        # 명부+자사주가 상장주식수를 넘으면(「기타」 음수) 단위가 어긋난 것이니 이 경우에도 붙이지 않는다.
        _listed_col = bool(_ls) and (_ls - major_sh - tr_sh) >= 0
        if issued and other >= 0:
            if _listed_col:
                # 보고서 분모와 최신 상장주식수가 조금 다르면 상장주식수 기준 지분율 칸을 덧붙인다.
                # 보유주식수·자사주는 보고서 결산기준일 수치 그대로(그 뒤 소각·취득은 반영되지 않는다).
                other_l = _ls - major_sh - tr_sh
                lines.extend([
                    "", "| 구분 | 보유주식수 | 지분율 | 지분율(최신 상장주식수 기준) |", "|------|-----------|--------|------|",
                    f"| 최대주주+특수관계인 | {major_sh:,} | {major_sh / issued * 100:.2f}% | {major_sh / _ls * 100:.2f}% |",
                    f"| 자사주 | {tr_sh:,} | {tr_sh / issued * 100:.2f}% | {tr_sh / _ls * 100:.2f}% |",
                    f"| 기타(소액주주·기관 등) | {other:,} | {other / issued * 100:.2f}% | {other_l / _ls * 100:.2f}% |",
                    f"| **합계(발행주식총수)** | **{issued:,}** | **100.00%** | **100.00%** (분모 {_ls:,}) |",
                ])
            else:
                lines.extend([
                    "", "| 구분 | 보유주식수 | 지분율 |", "|------|-----------|--------|",
                    f"| 최대주주+특수관계인 | {major_sh:,} | {major_sh / issued * 100:.2f}% |",
                    f"| 자사주 | {tr_sh:,} | {tr_sh / issued * 100:.2f}% |",
                    f"| 기타(소액주주·기관 등) | {other:,} | {other / issued * 100:.2f}% |",
                    f"| **합계(발행주식총수)** | **{issued:,}** | **100.00%** |",
                ])
            lines.extend(_denominator_lines(sb, _listed_col if _ls else None))
        elif issued and other < 0:
            lines.append("- (100% 분해 생략: 명부+자사주가 보통주 발행총수를 초과 — 우선주 등 분모 불일치)")
            lines.extend(_denominator_lines(sb, None))
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
            "> 보고자끼리 같은 특별관계자를 품고 있거나 보고 시점마다 주식총수가 달라, 이 표의 지분율은 더하면 안 된다.",
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
        # 겹치는 보고자가 없으면 진영 표는 단순 합을 되풀이할 뿐이라 내지 않는다 — 그때 100%를 넘는
        # 까닭은 보고마다 다른 기준 주식총수(아래 표)이고, 문장은 유의사항(`over_100_note`)에 있다.
        camps_data = data.get("block_camps") or {}
        camps = camps_data.get("camps") or []
        _merged = any((c.get("block_count") or 1) > 1 for c in camps)
        if camps and _merged:
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
        _bases = camps_data.get("report_bases") or []
        if camps_data.get("exceeds_100") and len(_bases) >= 2:
            lines.append("")
            lines.append("## 보고마다 다른 기준 주식총수 (5% 보고 합이 100%를 넘는 까닭)")
            lines.append(
                "> 지분율의 분모는 그 보고 시점의 주식등의 총수다. 기준이 다른 지분율끼리는 더할 수 없고, "
                "옛 기준 보고의 지분율은 지금 지분이 아닐 수 있다."
            )
            lines.append("")
            lines.append("| 기준 주식총수 (보유주식수 ÷ 지분율) | 보고자 | 접수일 |")
            lines.append("|------|--------|--------|")
            for base in _bases:
                first, last = base.get("report_dates") or ["", ""]
                lines.append(
                    f"| {base.get('implied_total_shares', 0):,}주 | {', '.join(base.get('reporters', []))} | "
                    f"{first if first == last else f'{first} ~ {last}'} |"
                )

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
        control_events = data.get("control_events", []) or []
        lines.extend(["", "## 최대주주 변경 · 변경 수반 계약 (거래소 공시)"])
        if not control_events:
            lines.append("- 조사 구간 내 최대주주변경·양수도·담보 계약 공시 없음")
        for ev in control_events:
            lines.extend(_control_event_lines(ev))
        if data.get("control_events_listed_only"):
            lines.append(f"- 그 밖에 {data['control_events_listed_only']}건은 원문을 열지 않았다 — 구간을 좁혀 다시 부를 것")
        change_filings = data.get("change_filings", [])
        lines.extend(["", "## 최대주주등 소유주식 변동신고서"])
        if not change_filings:
            lines.append("- 조사 구간 내 변동신고서 없음")
        for filing in change_filings:
            lines.extend(_change_filing_lines(filing))

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


def _v(x: Any, unit: str = "") -> str:
    return f"{x}{unit}" if x not in (None, "") else "-"


def _control_event_lines(ev: dict[str, Any]) -> list[str]:
    """최대주주변경 계열 사건 하나 — 원문 칸 글 그대로(숫자는 회사가 쓴 꼴)."""
    f = ev.get("fields") or {}
    kind = ev.get("kind")
    out = [f"\n### {ev.get('rcept_dt', '')} {ev.get('kind_label', '')} (`{ev.get('rcept_no', '')}`)"]
    if ev.get("status") == "error":
        out.append(f"- 원문을 못 읽었다 ({ev.get('error', '')}) — 원문 링크로 확인")
        return out
    def party(p):
        return f"{p['name']}({p['relation']})" if p and p.get("relation") else (p or {}).get("name", "-")
    if kind == "change":
        out.append(f"- **{_v(f.get('before_holder'))} {_v(f.get('before_pct'), '%')}** → "
                   f"**{_v(f.get('after_holder'))} {_v(f.get('after_pct'), '%')}** (변경일 {_v(f.get('changed_on'))})")
        for key, label in (("reason", "사유"), ("purpose", "지분인수 목적"), ("funding", "인수자금"), ("board_plan", "임원 선·해임 계획")):
            if f.get(key):
                out.append(f"- {label}: {' / '.join(f[key].split(chr(10)))}")
    elif kind in ("transfer_contract", "transfer_release"):
        if kind == "transfer_release":
            out.append(f"- **{_v(f.get('release_kind'))}** — {_v(f.get('reason'))} (해소일 {_v(f.get('released_on'))})")
        out.append(f"- 양도 {party(f.get('transferor'))} → 양수 {party(f.get('transferee'))}")
        out.append(f"- {_v(f.get('shares'), '주')} × {_v(f.get('price_per_share'), '원')} = **{_v(f.get('amount'), '원')}**"
                   + (f" (계약일 {f['contract_on']})" if f.get("contract_on") else ""))
        if kind == "transfer_contract" and f.get("expected_holder"):
            out.append(f"- 변경예정 최대주주: **{f['expected_holder']} {_v(f.get('expected_pct'), '%')}** "
                       f"({_v(f.get('expected_on'))} 예정)")
    elif kind == "pledge_contract":
        out.append(f"- 담보제공자 **{_v(f.get('pledgor'))}** {_v(f.get('pledgor_pct_now'), '%')} → 담보권 전부 실행시 "
                   f"**{_v(f.get('pct_if_executed'), '%')}**")
        out.append(f"- 차입 {_v(f.get('debt_total'), '원')} · 담보설정 {_v(f.get('pledge_total'), '원')} · 누적 담보 "
                   f"{_v(f.get('cumulative_pledged_shares'), '주')} (계약일 {_v(f.get('contract_on'))})")
    elif kind == "pledge_release":
        out.append(f"- 해소 {_v(f.get('released_shares'), '주')} · {_v(f.get('released_amount'), '원')} — {_v(f.get('reason'))} "
                   f"(해소일 {_v(f.get('released_on'))})")
        for c in ev.get("comparison") or []:
            out.append(f"  - {c['item']}: {c['before']} → {c['after']}")
    if not f:
        out.append("- 본표 칸을 못 찾았다 — 원문 링크로 확인")
    a = ev.get("amendment") or {}
    if a:
        out.append(f"- 정정: {_v(a.get('reason'))}" + (f" (원공시 {a['original_filed_date']})" if a.get("original_filed_date") else ""))
        for r in (a.get("rows") or [])[:3]:
            # 항목 이름도 원문 줄바꿈째 온다(`2. 계약 내역\n-양수도 대금…`) — md 한 줄 안에서는 ` / ` 로
            item = " / ".join(r["item"].split(chr(10)))
            out.append(f"  - {item}: {' / '.join(r['before'].split(chr(10)))[:60]} → {' / '.join(r['after'].split(chr(10)))[:60]}")
    if ev.get("notes"):
        note = " / ".join(ev["notes"][0].split("\n"))
        out.append(f"- 기타: {note[:200]}{'…' if len(note) > 200 else ''}")
    if ev.get("profile_omitted"):
        out.append("- _인적사항 표(주소·생년월일)는 싣지 않는다 — 원문 링크_")
    return out


def _sh(x: Any, sign: bool = False) -> str:
    """주식수 — 못 읽은 칸(None)은 `-`. 0 으로 메우지 않는다."""
    return "-" if x is None else (f"{x:+,}" if sign and x else f"{x:,}")


def _pc(x: Any, sign: bool = False) -> str:
    return "-" if x is None else (f"{x:+.2f}" if sign and x else f"{x:.2f}")


def _change_filing_lines(filing: dict[str, Any]) -> list[str]:
    """최대주주등소유주식변동신고서 한 건 — 보고 묶음마다 개요 · 개인별 변동 · 총괄현황."""
    rcept_no = filing.get("rcept_no", "")
    title = " ".join((filing.get("report_nm") or "").split())
    out = [f"\n### {filing.get('rcept_dt', '')} ({rcept_no})" + (f" {title}" if "변경시" in title else "")]
    if filing.get("status") == "unrecognized":
        out.append(f"- **못 읽음** — {' / '.join(filing.get('notes') or [])} (원문 `{rcept_no}`)")
        return out
    for rep in filing.get("reports") or []:
        if rep.get("group"):
            out.append(f"\n#### [{rep['group']}]")
        ov = rep.get("overview") or {}
        b, a, d = ov.get("before") or {}, ov.get("after") or {}, ov.get("delta") or {}
        before = (f"{_sh(b.get('shares'))}주 ({_pc(b.get('pct'))}%, {b.get('filed_on') or '-'} 보고)"
                  if b.get("shares") is not None else "직전 보고 없음")
        out.append(f"- 직전: {before} → 이번: {_sh(a.get('shares'))}주 ({_pc(a.get('pct'))}%, {a.get('filed_on') or '-'} 보고)"
                   f" / 증감 {_sh(d.get('shares'), sign=True)}주 ({_pc(d.get('pct'), sign=True)}%p)"
                   + (f" — {a.get('basis')} 기준" if a.get("basis") and a.get("basis") != "합계" else ""))
        for person in rep.get("individual_changes") or []:
            changes = person.get("changes") or []
            if not changes:
                continue
            who = " · ".join(x for x in (person.get("relation"), person.get("holder_kind")) if x)
            out.append(f"\n**{person.get('name') or '(성명 칸 못 읽음)'}**" + (f" ({who})" if who else "") + " 변동")
            out.extend(["| 변경일 | 변경원인 | 주식종류 | 변경전 | 증감 | 변경후 |",
                        "|--------|----------|----------|--------|------|--------|"])
            for row in changes:
                out.append(f"| {row['date']} | {row['reason']} | {row['stock_type']} | {_sh(row['before'])} | "
                           f"{_sh(row['delta'], sign=True)} | {_sh(row['after'])} |")
        holders = rep.get("total_holders") or []
        if holders:
            out.extend(["\n**총괄현황** (이번 보고 기준)", "| 성명 | 관계 | 구분 | 보통주 | 비율 | 합계 | 비율 |",
                        "|------|------|------|-------|------|------|------|"])
            for h in holders:
                out.append(f"| {h['name']} | {h.get('relation') or '-'} | {h.get('holder_kind') or '-'} | "
                           f"{_sh(h.get('common_shares'))} | {_pc(h.get('common_pct'))}% | "
                           f"{_sh(h.get('total_shares'))} | {_pc(h.get('total_pct'))}% |")
    for u in filing.get("unread") or []:
        out.append(f"- **못 읽음**: 원문 `{rcept_no}` 표 {u['table']}"
                   + (f" (「{u['section']}」)" if u.get("section") else "") + " — 칸 이름을 못 알아봤다, 원문 확인")
    for note in filing.get("notes") or []:
        out.append(f"- 확인 필요: {note}")
    if filing.get("profile_omitted"):
        out.append("- _인적사항(생년월일·사업자등록번호·성별·국적)은 싣지 않는다 — 원문 링크_")
    return out


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
        rule: 사업보고서 DART 공식 API 우선. **summary 의 100% 분해 표는 분모(보통주 발행주식총수)가 어느 정기보고서·어느 결산기준일 값인지 표 아래에 적고(`share_basis`: `rcept_no`·`stlm_dt`·`bsns_year`·`stock_class`), 최신 상장주식수(KRX `price_latest` 보통주 코드)와 다르면 두 값과 차이(`diff_shares`·`diff_pct`)를 함께 적고 「지분율(최신 상장주식수 기준)」 칸을 덧붙인다** — 보유주식수·자사주는 보고서 기준일 수치 그대로라 그 뒤 소각·증자는 반영되지 않는다. 차이가 20%를 넘으면(`comparison=unit_changed`: 액면분할·병합 등으로 주식 수 단위가 달라졌을 수 있음) 상장주식수 기준 지분율은 계산하지 않고 두 값과 차이만 적으며, 과거 사업연도 조회(`comparison=past_year`)는 오늘의 상장주식수와 비교하지 않는다. 분모가 보통주만이라고 장담할 수 없으면(`comparison=mixed_class`) 상장주식수와 섞어 비교하지 않는다. 5% 대량보유 목적은 최신 원문 보강. 변동신고서는 DART API 우선, KIND fallback. 5% 보고 헤드라인 지분율(ownership_pct)은 **보고자 본인 + 특별관계자 합산**임 — 본인만 보려면 `reporter_self_pct`, 공동보유자 내역은 `co_holders`[{name, ownership_pct, is_registry_holder}] 사용. `co_holders_verified=False`면 합계 미검증이라 원문 대조 필요(확정 인용 금지). 합계표 없는 약식보고(기관 단순투자 등)는 co_holders=None. **5% 보고 지분율끼리 더하지 말 것** — 보고자들이 같은 특별관계자를 공유해 같은 주식이 중복 신고되거나(고려아연 단순합 111.84%), 보고마다 지분율의 기준 주식총수가 다르다(출자전환·감자·신주 사이에 낸 보고 — STX). 중복을 걷어낸 진영별 순 지분은 `block_camps.camps[].net_pct`, 겹침 내역은 `block_camps.shared_holders_between_reporters`, 총합은 `block_camps.net_total_pct`(null이면 계산 불가 — 지어내지 말 것; `overlap_found=false`면 걷어낸 몫이 없어 단순 합과 같다). 보고별 기준 주식총수(보유주식수÷지분율) 묶음은 `block_camps.report_bases`, 100% 초과 까닭 문장은 `block_camps.over_100_note`. `reporter_self_pct=0`이면 `reporter_self_note`에 뜻이 담긴다(보고자 본인 직접 보유 없음; 사유는 본 보고서로 확정 불가). DART 013/014/404는 실패가 아니라 「해당 자료 없음」으로 표기된다.
        scope: `summary` 최대주주+5%블록(+공동보유자 분해)+자사주 snapshot / `major_holders` 특수관계인 detail / `blocks` 5% 대량보유 최신+이력+공동보유자 분해 / `control_map` 3대 카테고리(명부 등재/외부 능동/수동)+공동보유자 / `changes` 최대주주변동신고서(I004) + 5% 대량보유 변동(D001) + **최대주주변경·변경 수반 주식양수도·담보 계약 체결·해제(거래소 I001)** 통합
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
