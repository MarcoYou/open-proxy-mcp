#!/usr/bin/env python3
"""law_lookup 회귀·통계검증 (spot_* 계열, DART 0콜).

검증 축:
  1) 원문 정합    — 인덱스 조문의 full_text 슬라이스·시행일·조문번호 round-trip
  2) recall(bridge) — 40룰 provision → 조문 역방향(방향B) + 앵커 키워드 → 조문(방향A)
  3) collision    — '제147조'(법령 미지정) → ambiguous + 복수 법령
  4) precision    — 적대적 두루뭉술 질의 → requires_review / 후보 bound
  5) guard        — false-friend(이사↮사외이사) 오탐 차단
  6) shared-asset — proxy_advise 40룰·_agenda_pattern_match 무결(공유자산 회귀)
슬라이스(법·질의유형) 리포트. 하드실패(원문/collision/vague/guard/shared)만 exit 1.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from open_proxy_mcp.services.law_lookup import (  # noqa: E402
    build_law_lookup_payload as B,
    extract_tokens,
    get_full_text,
    load_index,
    _rule_articles,
)
from open_proxy_mcp.services.proxy_advise import (  # noqa: E402
    _agenda_pattern_match,
    _load_law_layer_rules,
)

hard_fail = 0


def section(t):
    print("\n" + "=" * 68 + f"\n{t}\n" + "=" * 68)


# ── 1) 원문 정합 ─────────────────────────────────────────────────────────
def test_corpus_integrity():
    global hard_fail
    section("1) 원문 정합 (full_text·시행일·round-trip)")
    idx = load_index()
    arts = idx["articles"]
    laws = {L["law_key"]: L for L in idx["meta"]["laws"]}
    empty_text = 0
    bad_head = 0
    bad_enf = 0
    for r in arts:
        ft = get_full_text(r)
        if not ft:
            empty_text += 1
            continue
        # 슬라이스 첫 줄이 이 조문 heading이어야
        if not ft.lstrip("#").strip().startswith(r["article_no"]):
            bad_head += 1
        if r["enforcement"] != laws[r["law_key"]]["enforcement"]:
            bad_enf += 1
    print(f"  조문 {len(arts)} · full_text 빈값 {empty_text} · heading 불일치 {bad_head} · 시행일 불일치 {bad_enf}")
    if empty_text or bad_head or bad_enf:
        hard_fail += 1
        print("  ✗ FAIL 원문 정합")
    else:
        print("  ✓ PASS")


# ── 2) recall (bridge) ───────────────────────────────────────────────────
def test_recall_bridge():
    section("2) recall — bridge 40룰 (방향B 역방향 + 방향A 앵커)")
    rules = _load_law_layer_rules()
    b_hit = b_tot = 0
    a_hit = a_tot = 0
    miss_b, miss_a = [], []
    for rule in rules:
        arts = _rule_articles(rule)
        if not arts:
            continue
        law, art = arts[0]
        # 방향B: 조문 → 이 룰이 related에 나오나
        b_tot += 1
        p = B(art, law=law, direction="law_to_clause", include_full_text=False, top_k=20)
        found = False
        for res in p["data"]["results"]:
            rel = res.get("related") or {}
            allrules = [d["rule_id"] for grp in rel.values() for d in grp]
            if rule.get("id") in allrules:
                found = True
                break
        if found:
            b_hit += 1
        else:
            miss_b.append(rule.get("id"))
        # 방향A: 룰 앵커 키워드 → 조문 top_k
        pat = rule.get("agenda_pattern") or {}
        kws = (pat.get("all_of") or []) + (pat.get("any_of") or [])[:1] + (pat.get("secondary") or [])[:1]
        if not kws:
            continue
        a_tot += 1
        pa = B(" ".join(kws), direction="clause_to_law", include_full_text=False, top_k=20)
        hitset = {(r["law"], r["article_no"]) for r in pa["data"]["results"]}
        if any((law, art) in hitset for law, art in arts):
            a_hit += 1
        else:
            miss_a.append((rule.get("id"), kws, arts[:2]))
    print(f"  방향B recall(조문→룰): {b_hit}/{b_tot} = {b_hit/max(b_tot,1):.1%}")
    if miss_b:
        print(f"    miss: {miss_b}")
    print(f"  방향A recall(키워드→조문): {a_hit}/{a_tot} = {a_hit/max(a_tot,1):.1%}")
    for mid, kws, arts in miss_a[:8]:
        print(f"    miss {mid}: {kws} → expected {arts}")


# ── 3) collision ─────────────────────────────────────────────────────────
def test_collision():
    global hard_fail
    section("3) collision — '제147조' 법령 미지정")
    p = B("제147조", include_full_text=False, top_k=10)
    laws = {r["law"] for r in p["data"]["results"]}
    ok = p["status"] == "ambiguous" and len(laws) > 1
    print(f"  status={p['status']} laws={sorted(laws)} → {'✓ PASS' if ok else '✗ FAIL'}")
    if not ok:
        hard_fail += 1


# ── 4) precision (두루뭉술) ──────────────────────────────────────────────
def test_vague():
    global hard_fail
    section("4) precision — 적대적 두루뭉술 질의")
    fails = 0
    for q in ["변경", "이사", "정관", "주식", "회사", "선임", "결정"]:
        p = B(q, include_full_text=False)
        st = p["status"]
        tot = p["data"]["total_candidates"]
        ok = st == "requires_review"
        print(f"  {q!r:8s} status={st:16s} total={tot} {'✓' if ok else '✗'}")
        if not ok:
            fails += 1
    if fails:
        hard_fail += 1
        print(f"  ✗ FAIL {fails}건 두루뭉술 미차단")
    else:
        print("  ✓ PASS 전부 requires_review")


# ── 5) guard (false-friend) ──────────────────────────────────────────────
def test_guard():
    global hard_fail
    section("5) guard — false-friend 오탐 차단")
    cases = [
        ("대표이사 선임", "이사", False),    # 대표이사는 이사(단독) 토큰 아님
        ("사외이사 후보", "이사", False),
        ("감사위원회 구성", "감사", False),   # 감사위원회는 감사(단독) 아님
        ("자기주식 취득", "주식", False),     # 자기주식은 주식(단독) 아님
        ("감사보고서 제출", "감사", False),   # 감사보고서는 감사(단독) 아님(복합어 누출 차단)
        ("이사 참석 회의", "이사", True),     # 순수 '이사'는 이사 토큰
        ("이사의 보수", "이사보수", True),    # 조사 분리 복합어 → 이사보수 (RC1)
    ]
    fails = 0
    for text, tok, expect in cases:
        got = tok in extract_tokens(text)
        ok = got == expect
        print(f"  {text!r:16s} '{tok}' in tokens? {got} (기대 {expect}) {'✓' if ok else '✗'}")
        if not ok:
            fails += 1
    if fails:
        hard_fail += 1
        print(f"  ✗ FAIL {fails}건")
    else:
        print("  ✓ PASS")


# ── 5b) 삭제 조문 탐지 (260713 멀티에이전트 적발 회귀) ────────────────────
def test_deleted():
    global hard_fail
    section("5b) 삭제 조문 탐지")
    idx = load_index()
    dele = [a for a in idx["articles"] if a.get("deleted")]
    print(f"  인덱스 삭제 조문: {len(dele)}건 (0이면 파서 회귀)")
    ok = len(dele) > 50  # 상법+자본시장법 합 100+ 기대
    # 실호출: 삭제 조문 query → deleted flag + 경고
    p = B("제35조", law="상법", include_full_text=False, top_k=8)
    hit = [r for r in p["data"]["results"] if r.get("deleted")]
    flag_ok = bool(hit) and any("삭제" in f for f in (hit[0].get("flags") or []))
    print(f"  제35조(상법) 삭제 flag: {flag_ok} ({[r['article_no'] for r in hit]})")
    if not (ok and flag_ok):
        hard_fail += 1
        print("  ✗ FAIL 삭제 탐지")
    else:
        print("  ✓ PASS")


# ── 5c) 법률 > 시행령 tier ────────────────────────────────────────────────
def test_tier():
    section("5c) 법률 > 시행령 tier (동점 시 governing 법률 우선)")
    # 자본시장법 단기매매차익: §172(법률)가 시행령 조문들보다 위여야
    p = B("단기매매차익 반환", include_full_text=False, top_k=6)
    tiers = [(r["article_no"], "시행령" if "시행령" in (r.get("law_name") or "") else "법률")
             for r in p["data"]["results"]]
    top_law = next((a for a, t in tiers if t == "법률"), None)
    print(f"  단기매매차익 top: {tiers[:3]} · 첫 법률 조문={top_law}")
    print("  ✓ 리포트(랭킹 확인용)")


# ── 5d) 미시행 유보 (260713 자본시장법 599/599 오탐 회귀) ─────────────────
def test_enforcement():
    global hard_fail
    section("5d) 미시행 유보 — 전문 시행일 브로드캐스트 금지")
    # 상법(전문 이미 시행) 조문 → 현행(True), 거짓 미시행 없어야
    ps = B("제388조", law="상법", include_full_text=False, top_k=2)
    sang_ok = all(r["in_force"] is True for r in ps["data"]["results"])
    print(f"  상법 제388조 in_force 전부 True: {sang_ok} ({[r['in_force'] for r in ps['data']['results']]})")
    # 자본시장법 법률(전문 2026-11-13 시행예정) 조문 → 미시행 단정 금지 → None(확인필요)
    pc = B("제147조", law="자본시장법", include_full_text=False, top_k=5)
    # in_force는 True/None만 (False 단정 없어야). '미시행(시행 ' flag는 SSOT effective_date에서만.
    no_false = all(r["in_force"] in (True, None) for r in pc["data"]["results"])
    has_unknown = any(r["in_force"] is None for r in pc["data"]["results"])
    ver_warn = any("시행 예정본" in w for w in pc["warnings"])
    print(f"  자본시장법 §147 in_force∈(True,None): {no_false} · 확인필요 존재: {has_unknown} · 버전경고: {ver_warn}")
    if not (sang_ok and no_false and has_unknown and ver_warn):
        hard_fail += 1
        print("  ✗ FAIL 미시행 유보")
    else:
        print("  ✓ PASS")


# ── 5e) 폴백 유형 분류 + 유형별 안내 ─────────────────────────────────────
def test_fallback():
    global hard_fail
    section("5e) 폴백 유형 — 이유별 분류 + 안내 문구")
    cases = [
        ("이사", "", "too_generic"),
        ("안녕하세요 오늘 날씨가 좋네요", "", "too_vague"),
        ("제147조", "", "law_collision"),
        ("제9999조", "상법", "article_not_found"),
        ("차등의결권 도입", "", "out_of_corpus_topic"),
        ("집중투표 배제 조항 삭제", "", None),  # clean → 폴백 없음
    ]
    fails = 0
    for q, lw, expect in cases:
        p = B(q, law=lw, include_full_text=False, top_k=3)
        fb = p["data"].get("fallback")
        got = fb["type"] if fb else None
        ok = got == expect
        # 폴백이면 문구·행동이 비어있지 않아야
        msg_ok = True if expect is None else bool(fb and fb.get("message") and fb.get("actions"))
        print(f"  {q!r:22s} law={lw!r:6s} → {str(got):20s} (기대 {expect}) {'✓' if ok and msg_ok else '✗'}")
        if not (ok and msg_ok):
            fails += 1
    if fails:
        hard_fail += 1
        print(f"  ✗ FAIL {fails}건 폴백 오분류")
    else:
        print("  ✓ PASS 전 유형 정확 + 문구·행동 존재")


# ── 6) shared-asset 회귀 ─────────────────────────────────────────────────
def test_shared_asset():
    global hard_fail
    section("6) shared-asset — proxy_advise 40룰·_agenda_pattern_match 무결")
    rules = _load_law_layer_rules()
    n = len(rules)
    m = _agenda_pattern_match("집중투표 배제 조항 삭제", "", {"all_of": ["집중투표"], "secondary": ["배제"]})
    ok = n == 40 and m is True
    print(f"  rules={n} (기대 40) · pattern_match sanity={m} → {'✓ PASS' if ok else '✗ FAIL'}")
    if not ok:
        hard_fail += 1


if __name__ == "__main__":
    test_corpus_integrity()
    test_recall_bridge()
    test_collision()
    test_vague()
    test_guard()
    test_deleted()
    test_tier()
    test_enforcement()
    test_fallback()
    test_shared_asset()
    section("결과")
    if hard_fail:
        print(f"✗ HARD FAIL {hard_fail} 축")
        sys.exit(1)
    print("✓ 하드 검증 통과 (recall은 리포트 — 튜닝 대상)")
    sys.exit(0)
