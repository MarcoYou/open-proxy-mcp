"""law_lookup — 정관↔법령 양방향 조회 (company-agnostic, DART 0콜)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.law_lookup import build_law_lookup_payload

_DIR_LABEL ={"clause_to_law": "정관/키워드 → 법령", "law_to_clause": "법령 → 정관·안건"}


def _decisions_lines(items: list[dict[str, Any]]) -> list[str]:
    lines = []
    for d in items:
        kws = ", ".join(d.get("keywords", [])[:6])
        lines.append(f"  - `[{d.get('rule_id')}]` {d.get('decision')} — {d.get('reason', '')[:90]}"
                     + (f" · 키워드: {kws}" if kws else ""))
    return lines


def _render(payload: dict[str, Any]) -> str:
    d = payload["data"]
    status = payload.get("status")
    lines = [
        f"# law_lookup — `{d.get('query')}`",
        f"방향: **{_DIR_LABEL.get(d.get('direction'), d.get('direction'))}** · status=`{status}` · "
        f"기준일 {d.get('as_of')}" + (f" · 필터 {d.get('law_filter')}" if d.get("law_filter") else ""),
        "",
    ]
    for w in payload.get("warnings", []):
        lines.append(f"> ⚠️ {w}")
    if payload.get("warnings"):
        lines.append("")

    toks = d.get("query_tokens") or []
    if toks:
        lines.append(f"인식 키워드: {', '.join(toks)}")
    if d.get("article_refs"):
        refs = ", ".join(r.get("article_no", "") for r in d["article_refs"])
        lines.append(f"인식 조문번호: {refs}")
    lines.append("")

    results = d.get("results") or []
    if not results:
        lines.append("_매칭된 조문이 없습니다._")
        for a in payload.get("next_actions", []):
            lines.append(f"- {a}")
        return "\n".join(lines)

    # 랭킹 표
    lines.append(f"## 후보 {d.get('total_candidates')}건 (표시 {len(results)})")
    lines.append("")
    lines.append("| # | 법령 | 조문 | 제목 | 시행 | score | 근거 |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, r in enumerate(results, 1):
        _if = r.get("in_force")
        force = "삭제" if r.get("deleted") else ("현행" if _if is True else ("확인필요" if _if is None else "미시행"))
        sig = "·".join(r.get("signals", []))
        lines.append(
            f"| {i} | {r.get('law')} | `{r.get('article_no')}` | {r.get('article_title') or '-'} | "
            f"{force} | {r.get('score')} | {sig} |")
    lines.append("")

    # 조문별 상세
    for i, r in enumerate(results, 1):
        lines.append(f"### {i}. [{r.get('law')}] {r.get('article_no')} ({r.get('article_title') or ''})")
        if r.get("path"):
            lines.append(f"- 위치: {' > '.join(r['path'])}")
        meta = [f"시행 {r.get('enforcement') or '-'}"]
        if r.get("amended_dates"):
            meta.append(f"개정 {', '.join(r['amended_dates'][-3:])}")
        lines.append(f"- {' · '.join(meta)}")
        for f in r.get("flags", []):
            lines.append(f"- ⚠️ {f}")
        # 항/호
        for h in r.get("hang", [])[:12]:
            mark = " (삭제)" if h.get("deleted") else ""
            lines.append(f"  - **{h.get('no')}항**{mark} {h.get('text', '')[:160]}")
            for ho in (r.get("ho") or {}).get(str(h.get("no")), [])[:12]:
                lines.append(f"    - {ho.get('no')}. {ho.get('text', '')[:120]}")
        if r.get("full_text"):
            lines.append("")
            lines.append("<details><요약>원문 전문</요약>")
            lines.append("")
            lines.append("```")
            lines.append(r["full_text"][:4000])
            lines.append("```")
            lines.append("</details>")
        # 방향 B: 관련 룰
        rel = r.get("related")
        if rel:
            for key, label in (("정관_변경유형", "관련 정관 변경유형"),
                               ("우회_시나리오", "우회 시나리오"),
                               ("주총_안건신호", "주총 안건신호")):
                items = rel.get(key) or []
                if items:
                    lines.append(f"- **{label}**:")
                    lines.extend(_decisions_lines(items))
        lines.append("")

    for a in payload.get("next_actions", []):
        lines.append(f"> ▷ {a}")
    if d.get("corpus_asof"):
        age = d.get("corpus_age_days")
        age_txt = f" ({age}일 전)" if isinstance(age, int) else ""
        lines.append("")
        lines.append(f"_법령 자료 기준: {d['corpus_asof']}{age_txt} · 원문 legalize-kr_")
    return "\n".join(lines)


def _render_status(payload: dict[str, Any]) -> str:
    d = payload.get("data", {})
    lines = [f"# law_lookup — `{d.get('query', payload.get('subject', ''))}` · status=`{payload.get('status')}`", ""]
    for w in payload.get("warnings", []):
        lines.append(f"> ⚠️ {w}")
    for a in payload.get("next_actions", []):
        lines.append(f"- {a}")
    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def law_lookup(
        query: str,
        direction: str = "auto",
        law: str = "",
        as_of: str = "",
        include_full_text: bool = True,
        top_k: int = 10,
        format: str = "md",
    ) -> str:
        """desc: **정관↔법령 양방향 조회기**. 정관 조항·키워드·두루뭉술한 표현 → 관련 법령 조문(전문 포함),
        또는 법령 조문번호·키워드 → 조문 전문 + 관련 정관 변경유형·우회 시나리오·주총 안건신호. 상법·상법
        시행령·자본시장법·공정거래법·외부감사법(각 법률+시행령) 원문(legalize-kr)을 인덱싱. **회사·DART
        무관, API 호출 0** — 순수 법령 지식 조회기. 카디널리티 1:1·N:1·1:N·N:N 전부(랭킹된 전체 후보, first-match 아님).
        when: "이 정관 조항이 무슨 법이랑 엮여?"(clause_to_law), "자본시장법 §147 뭐야·전문 보여줘"(law_to_clause),
        "집중투표 배제 조항 삭제하면 무슨 법 위반?", "감사위원 분리선출 근거 조문", "상호출자 금지 조문",
        "전자주주총회 도입 관련 법". proxy_advise(특정 회사 주총 안건 판단)와 달리 회사 맥락 없이 법령 자체를 묻는 질의.
        rule: 3신호 융합 — E(정확 조문 튜플 매칭, substring 절대 금지 → 제12조≠제12조의2, 상법§147≠자본시장법§147)
        + B(40룰 bridge 재사용, `_agenda_pattern_match` — 정관패턴↔조문, free-text law_reference도 파싱)
        + C(corpus 키워드, idf·anchor 게이트 — 두루뭉술 질의는 requires_review). 보수적: 폐쇄 큐레이션 어휘
        (`law_lookup_synonyms.json`)만, false-friend guard(이사↮사외이사 등), difflib 없음. 삭제 조문 보존+경고.
        미시행: 전문 시행예정본은 조문별 현행여부 '확인필요'로 유보(단정 X), 진짜 조문별 미래시행만 SSOT
        effective_date로 flag. 조문번호 법령 미지정+중복 → ambiguous. 강매치 아니면 폴백 유형별 안내(fallback).
        **범위 밖은 조문을 붙이지 않는다** — 거래소 상장규정·공시규정·업무규정(관리종목·상장폐지·실질심사·
        불성실공시·정리매매 등)은 이 4법 원문에 없으므로 어휘가 겹쳐도 조문을 반환하지 않고 범위 안내로 끝낸다
        (fallback.type=out_of_corpus_topic · data.results_suppressed). 용어 자체를 못 알아본 질의(too_vague·
        too_generic)는 전문 없이 후보 표만, 약한 매칭(weak_match)은 전문을 상위 3건만 붙인다
        (data.full_text_suppressed · full_text_limited_to). 전문이 더 필요하면 조문번호로 다시 묻는다.
        query: 정관 조항 텍스트 · 키워드 · 조문번호(예: 제542조의8) · 자유 질의
        direction: auto(기본) | clause_to_law(정관/키워드→법) | law_to_clause(법조문/키워드→정관·안건)
        law: 법령 필터 "" (전체) | 상법 | 자본시장법 | 공정거래법 | 외부감사법 (시행령 포함)
        as_of: YYYY-MM-DD 기준일(기본 오늘) — 시행/미시행·명칭변경(사외이사↔독립이사) 게이팅
        include_full_text: 조문 원문 전문 포함(기본 True). False면 메타·항/호 구조만
        top_k: 표시 후보 수(기본 10, 초과분은 총계만 경고)
        ref: proxy_advise_before_meeting, shareholder_meeting_notice, evidence
        """
        payload = build_law_lookup_payload(
            query, direction=direction, law=law, as_of=as_of,
            include_full_text=include_full_text, top_k=top_k, format=format)
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") in ("error", "requires_review") and not (payload.get("data") or {}).get("results"):
            return _render_status(payload)
        return _render(payload)
