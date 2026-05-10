"""26 진정 카카오게임즈 패턴 회사 sub-agenda + amendment 매핑 가능성 정량화 (Ralph 8 iter 1).

각 회사:
- summary scope → 정관변경 top + sub-agenda list
- aoi_change scope → amendments (label/clause/before/after/reason)
- sub title 키워드 (조항 번호 / 한국어 명사) 추출
- amendment label 키워드 (제N조 / 제N조의M) 추출
- 매칭 score 측정 + 분류 (명확 / 부분 / 불가)
"""
from __future__ import annotations
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services.proxy_advise import _is_charter_top  # noqa: E402
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload  # noqa: E402

TARGETS = [
    "유한양행", "강원랜드", "씨에스윈드", "한미사이언스", "솔브레인",
    "동진쎄미켐", "원익홀딩스", "하나마이크론", "차바이오텍", "인텔리안테크",
    "솔브레인홀딩스", "쏠리드", "엔켐", "나노신소재", "파이버프로",
    "넥슨게임즈", "아난티", "에코프로에이치엔", "시노펙스", "한라캐스트",
    "파인엠텍", "인바디", "이수페타시스", "대덕전자", "ISC", "성호전자",
    "카카오게임즈",  # baseline 비교용
]


def _extract_clause_numbers(text: str) -> set[str]:
    """텍스트에서 정관 조항 번호 추출 (제N조 / 제N조의M)."""
    nums = set()
    for m in re.finditer(r'제\s*(\d+)\s*조(?:\s*의\s*(\d+))?', text):
        n1 = m.group(1)
        n2 = m.group(2)
        if n2:
            nums.add(f"제{n1}조의{n2}")
        else:
            nums.add(f"제{n1}조")
    return nums


def _extract_keywords(text: str) -> set[str]:
    """텍스트에서 한국어 명사 키워드 추출 (정관변경 도메인 키워드)."""
    domain_keywords = [
        "기준일", "소집지", "의결권", "이사", "감사", "보수", "퇴직금", "임기",
        "사업목적", "주식", "전자", "주주명부", "기타비상무이사", "이사회",
        "위원회", "수권주식", "전환사채", "신주인수권", "배당", "사명",
        "본점", "정원", "원수", "표결", "의안", "공고", "통지", "명의개서",
        "전자증권", "신설", "삭제", "축소", "확대", "증액", "한도", "정비",
        "개정", "반영", "조문",
    ]
    found = set()
    text_clean = text.replace(" ", "")
    for kw in domain_keywords:
        if kw in text_clean:
            found.add(kw)
    return found


def _walk(items, parent="", out=None):
    if out is None:
        out = []
    for it in items or []:
        t = (it.get("title") or "").strip()
        if t:
            out.append({"title": t, "parent": parent, "n_children": len(it.get("children") or [])})
        _walk(it.get("children", []), parent=t, out=out)
    return out


async def _audit_one(name: str) -> dict:
    out = {"name": name}
    try:
        sm_sum = await asyncio.wait_for(
            build_shareholder_meeting_payload(name, year=2026, scope="summary", meeting_type="annual"),
            timeout=60.0,
        )
        agendas = (sm_sum.get("data") or {}).get("agendas") or []
        flat = _walk(agendas)

        sm_aoi = await asyncio.wait_for(
            build_shareholder_meeting_payload(name, year=2026, scope="aoi_change", meeting_type="annual"),
            timeout=60.0,
        )
        amendments = (((sm_aoi.get("data") or {}).get("aoi_change") or {})).get("amendments") or []
    except Exception as exc:
        out["error"] = str(exc)[:100]
        return out

    # 정관변경 top + children > 0 안건 (1번째만 — 보통 1개)
    charter_top = None
    for it in flat:
        if it["parent"] == "" and _is_charter_top(it["title"]) and it["n_children"] > 0:
            charter_top = it
            break
    if not charter_top:
        out["status"] = "no_charter_top_with_children"
        return out

    subs = [c for c in flat if c["parent"] == charter_top["title"]]
    out["charter_top"] = charter_top["title"][:60]
    out["n_subs"] = len(subs)
    out["n_amendments"] = len(amendments)

    # sub별 매핑 score
    sub_results = []
    for sub in subs:
        sub_title = sub["title"]
        sub_clauses = _extract_clause_numbers(sub_title)
        sub_keywords = _extract_keywords(sub_title)

        am_scores = []
        for am in amendments:
            am_label = am.get("label") or ""
            am_reason = am.get("reason") or ""
            am_text = f"{am_label} {am_reason}"
            am_clauses = _extract_clause_numbers(am_text)
            am_keywords = _extract_keywords(am_text)

            clause_overlap = len(sub_clauses & am_clauses)
            keyword_overlap = len(sub_keywords & am_keywords)
            score = clause_overlap * 10 + keyword_overlap  # clause 매칭 가중치 ↑

            am_scores.append({
                "am_label": am_label,
                "score": score,
                "clause_overlap": list(sub_clauses & am_clauses),
                "keyword_overlap": list(sub_keywords & am_keywords),
            })
        am_scores.sort(key=lambda x: -x["score"])
        best = am_scores[0] if am_scores else None
        sub_results.append({
            "sub_title": sub_title[:60],
            "sub_clauses": list(sub_clauses),
            "sub_keywords": list(sub_keywords),
            "best_am_score": best["score"] if best else 0,
            "best_am_label": best["am_label"] if best else "",
            "best_am_clause_overlap": best["clause_overlap"] if best else [],
            "best_am_keyword_overlap": best["keyword_overlap"] if best else [],
        })

    out["subs"] = sub_results
    # 매핑 분류
    n_clear = sum(1 for s in sub_results if s["best_am_score"] >= 10)  # clause 매칭
    n_partial = sum(1 for s in sub_results if 1 <= s["best_am_score"] < 10)
    n_none = sum(1 for s in sub_results if s["best_am_score"] == 0)
    out["n_clear"] = n_clear  # clause 매칭
    out["n_partial"] = n_partial  # keyword 매칭
    out["n_none"] = n_none  # 매칭 X
    return out


async def _run():
    archive = ROOT / "wiki/architecture/audits/data/260510_subagenda_mapping"
    archive.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(3)

    async def _wrapped(name):
        async with sem:
            print(f"  → {name} ...", flush=True)
            return await _audit_one(name)

    results = await asyncio.gather(*[_wrapped(n) for n in TARGETS])

    # 요약
    print("\n=== 26 회사 sub→amendment 매핑 가능성 ===")
    print(f"{'회사':<14} {'subs':>5} {'ams':>4} {'clear':>6} {'partial':>8} {'none':>5}")
    total_subs = 0
    total_clear = 0
    total_partial = 0
    total_none = 0
    for r in results:
        if "error" in r or r.get("status") == "no_charter_top_with_children":
            print(f"{r['name']:<14} ERROR/SKIP: {r.get('error') or r.get('status')}")
            continue
        print(f"{r['name']:<14} {r['n_subs']:>5} {r['n_amendments']:>4} {r['n_clear']:>6} {r['n_partial']:>8} {r['n_none']:>5}")
        total_subs += r['n_subs']
        total_clear += r['n_clear']
        total_partial += r['n_partial']
        total_none += r['n_none']
    print(f"{'─' * 50}")
    print(f"{'TOTAL':<14} {total_subs:>5} {'':>4} {total_clear:>6} {total_partial:>8} {total_none:>5}")
    print(f"\nclear (clause 매칭): {total_clear}/{total_subs} ({100*total_clear/total_subs if total_subs else 0:.1f}%)")
    print(f"partial (keyword 매칭): {total_partial}/{total_subs} ({100*total_partial/total_subs if total_subs else 0:.1f}%)")
    print(f"none (매칭 X — generic): {total_none}/{total_subs} ({100*total_none/total_subs if total_subs else 0:.1f}%)")

    out = archive / "iter1_26_companies.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    asyncio.run(_run())
