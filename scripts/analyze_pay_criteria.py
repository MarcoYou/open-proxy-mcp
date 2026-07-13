"""pay_criteria 검증 분석 — 수집 JSONL(DART-0콜)에서 기계적 통계 + 적대검증 샘플 선정(260713).

① 파싱률·not_found 사유·자기일치(reconciliation) 분포·정책커버리지·단위 sanity 통계.
② 멀티에이전트 적대검증 대상 층화표본 선정: 자기일치 실패 전건 + 엣지 전건 + parsed-but-0명 +
   단위이상 + 랜덤. → sample.json(원문 raw_section + 파서출력)로 저장(Workflow args용).
"""
from __future__ import annotations
import os, json, random, tempfile
from pathlib import Path
from collections import Counter, defaultdict

OUT = Path(os.environ.get("OPM_VPAY_OUT") or (Path(tempfile.gettempdir()) / "opm_vpay_validation"))
JSONL = OUT / "results.jsonl"

def load():
    recs = []
    for line in open(JSONL, encoding="utf-8"):
        line = line.strip()
        if line:
            recs.append(json.loads(line))
    return recs

def main():
    recs = load()
    n = len(recs)
    by_status = Counter(r.get("status") for r in recs)
    by_market_status = defaultdict(Counter)
    for r in recs:
        by_market_status[r.get("source")][r.get("status")] += 1

    parsed = [r for r in recs if r.get("status") == "parsed"]
    # 자기일치
    recon_checkable = recon_consistent = 0
    inconsistent = []
    for r in parsed:
        rc = r.get("reconciliation") or {}
        recon_checkable += rc.get("checkable") or 0
        recon_consistent += rc.get("consistent") or 0
        for p in (r.get("individuals") or []):
            if p.get("total_consistent") is False:
                inconsistent.append({"ticker": r["ticker"], "company": r["company"],
                                     "name": p.get("name"), "diff_krw": p.get("total_diff_krw")})
    # 정책 커버리지
    pol_table = sum(1 for r in parsed if (r.get("policy_rows") or 0) > 0)
    pol_narr = sum(1 for r in parsed if not (r.get("policy_rows") or 0) and r.get("policy_narrative"))
    pol_none = len(parsed) - pol_table - pol_narr
    # 개인 커버리지
    zero_indiv = [r["ticker"] for r in parsed if not (r.get("n_individuals") or 0)]
    # 단위 sanity: parsed인데 개인 총액이 비현실(>1조 or, 5억+ 공개대상인데 <3억 → 단위오류 의심)
    unit_flags = []
    for r in parsed:
        for p in (r.get("individuals") or []):
            t = p.get("total_krw")
            if t is None:
                continue
            if t > 1_000_000_000_000 or (0 < t < 300_000_000):
                unit_flags.append({"ticker": r["ticker"], "company": r["company"],
                                   "name": p.get("name"), "total_krw": t})
    # not_found 사유
    nf_reasons = Counter()
    for r in recs:
        if r.get("status") != "parsed":
            note = (r.get("note") or "") + " " + " ".join((r.get("warnings") or []) if isinstance(r.get("warnings"), list) else [])
            key = "금융지주/별도양식" if ("금융" in note or "별도" in note or "연차보고서" in note) else \
                  ("접수번호실패" if "접수번호" in note else (r.get("status") or "기타"))
            nf_reasons[key] += 1

    stats = {
        "n": n, "by_status": dict(by_status),
        "by_market_status": {k: dict(v) for k, v in by_market_status.items()},
        "reconciliation": {"checkable": recon_checkable, "consistent": recon_consistent,
                           "rate_pct": round(recon_consistent / recon_checkable * 100, 1) if recon_checkable else None,
                           "inconsistent_count": len(inconsistent)},
        "policy_coverage": {"table": pol_table, "narrative_only": pol_narr, "none": pol_none, "of_parsed": len(parsed)},
        "zero_individual_parsed": zero_indiv,
        "unit_sanity_flags": unit_flags,
        "not_found_reasons": dict(nf_reasons),
        "unknown_tables_median": sorted(r.get("unknown_tables") or 0 for r in parsed)[len(parsed)//2] if parsed else None,
    }
    (OUT / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    # 적대검증 층화표본: 자기일치실패 + 엣지 + 0명 + 단위이상 + 랜덤 parsed
    picked = {}
    def add(r, reason):
        picked.setdefault(r["ticker"], {"reasons": set()})["reasons"].add(reason)
        picked[r["ticker"]]["rec"] = r
    incon_tk = {i["ticker"] for i in inconsistent}
    np_added = 0
    for r in recs:
        if r["ticker"] in incon_tk: add(r, "reconciliation_fail")
        if r.get("source") == "edge": add(r, "edge")
        if r.get("status") == "parsed" and not (r.get("n_individuals") or 0): add(r, "zero_individual")
        if r.get("status") not in ("parsed",) and np_added < 6:  # not_found 확인은 소수만(대부분 금융지주 정상)
            add(r, "not_parsed"); np_added += 1
    for tk in {u["ticker"] for u in unit_flags}:
        r = next((x for x in recs if x["ticker"] == tk), None)
        if r: add(r, "unit_flag")
    # 랜덤 parsed 20 (재현성 위해 정렬 후 고정 시드)
    rng = random.Random(20260713)
    pool = sorted([r for r in parsed if r["ticker"] not in picked], key=lambda x: x["ticker"])
    for r in rng.sample(pool, min(15, len(pool))):
        add(r, "random_parsed")

    def slim_indiv(inds):
        out = []
        for p in inds or []:
            out.append({
                "group": (p.get("group") or "")[:14], "name": p.get("name"),
                "total_krw": p.get("total_krw"), "official_total_krw": p.get("official_total_krw"),
                "total_consistent": p.get("total_consistent"), "total_diff_krw": p.get("total_diff_krw"),
                "components": [{"pay_type": c.get("pay_type"), "amount_krw": c.get("amount_krw"),
                               "ranges": c.get("ranges")} for c in p.get("components", [])],
            })
        return out
    # 우선순위: 이슈성 사유 우선, 랜덤은 자리 남으면. args 크기 위해 40사 상한.
    PRIO = {"unit_flag": 0, "zero_individual": 1, "reconciliation_fail": 2, "edge": 3, "not_parsed": 4, "random_parsed": 5}
    ordered = sorted(picked.items(),
                     key=lambda kv: min(PRIO.get(x, 9) for x in kv[1]["reasons"]))[:40]
    sample = []
    for tk, v in ordered:
        r = v["rec"]
        sample.append({
            "ticker": tk, "company": r.get("company"), "source": r.get("source"),
            "reasons": sorted(v["reasons"]), "status": r.get("status"), "rcept_no": r.get("rcept_no"),
            "parser_output": {
                "pay_policy": [{"group": p.get("group"), "ranges": p.get("ranges")} for p in (r.get("pay_policy") or [])],
                "policy_narrative": r.get("policy_narrative"),
                "individuals": slim_indiv(r.get("individuals")),
                "individual_totals": r.get("individual_totals"),
                "reconciliation": r.get("reconciliation"), "unknown_tables": r.get("unknown_tables"),
            },
            "raw_section": (r.get("raw_section") or "")[:3000],
        })
    (OUT / "sample.json").write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

    # 멀티에이전트 적대검증용 shard (각 에이전트가 자기 shard만 Read — Workflow fs제약 우회)
    shard_dir = OUT / "shards"
    shard_dir.mkdir(exist_ok=True)
    for f in shard_dir.glob("*.json"):
        f.unlink()
    SHARD = 5
    for i in range(0, len(sample), SHARD):
        (shard_dir / f"shard_{i//SHARD:02d}.json").write_text(
            json.dumps(sample[i:i+SHARD], ensure_ascii=False, indent=1), encoding="utf-8")
    n_shards = (len(sample) + SHARD - 1) // SHARD

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"\nADVERSARIAL SAMPLE: {len(sample)} companies -> sample.json + {n_shards} shards")

if __name__ == "__main__":
    main()
