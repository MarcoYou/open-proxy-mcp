"""Phase 1 batch JSON 통합 + G1 / 실패 케이스 / fix 후보 분석.

DART 호출 X — 기존 audit JSON만 읽음.

사용법:
    uv run python scripts/agg_parser_omnibus.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
AUDIT_DIR = ROOT / "wiki/architecture/audits/data/260505_parser_omnibus"


def _load_all() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in sorted(AUDIT_DIR.glob("iter0*_*.json")):
        if "smoke" in p.name:
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        for r in data["records"]:
            r["_source_file"] = p.name
            r["_universe"] = "kospi" if "kospi" in p.name else "kosdaq"
            rows.append(r)
    return rows


def _topic(r: dict, key: str) -> bool:
    if r.get("status") != "ok":
        return False
    return bool((((r.get("parsers") or {}).get("agenda") or {}).get("topics") or {}).get(key))


def _pf(records: list[dict], topic: str | None, parser: str, predicate) -> dict[str, Any]:
    """Compute G1 for parser given topic gate + predicate(parser_dict) → bool."""
    if topic:
        denom = [r for r in records if r.get("status") == "ok" and _topic(r, topic)]
    else:
        denom = [r for r in records if r.get("status") == "ok"]
    n = len(denom)
    fails: list[dict] = []
    ok = 0
    for r in denom:
        p = (r["parsers"] or {}).get(parser, {})
        if predicate(p):
            ok += 1
        else:
            fails.append({
                "ticker": r["ticker"], "name": r["name"], "rcept_no": r.get("rcept_no"),
                "_universe": r["_universe"],
                "parser_summary": p,
            })
    pct = round(ok * 100.0 / n, 1) if n else 0.0
    return {"ok": ok, "n": n, "pct": pct, "fails": fails}


def _agg(records: list[dict]) -> dict[str, Any]:
    ok = [r for r in records if r.get("status") == "ok"]

    # Status counts
    status_counter = Counter(r.get("status") for r in records)

    # Per-parser G1
    g1 = {
        "meeting_info": _pf(records, None, "meeting_info",
                            lambda p: p.get("call_status") == "ok" and bool(p.get("meeting_type"))),
        "agenda": _pf(records, None, "agenda",
                      lambda p: p.get("call_status") == "ok" and p.get("total_titles", 0) >= 1),
        "agenda_details": _pf(records, None, "agenda_details",
                              lambda p: p.get("call_status") == "ok" and p.get("items_count", 0) >= 1),
        "corrections": _pf(records, None, "corrections",
                           lambda p: p.get("call_status") == "ok"),
        "personnel": _pf(records, "has_director", "personnel",
                         lambda p: p.get("call_status") == "ok" and p.get("appointments_count", 0) >= 1),
        "personnel_audit_topic": _pf(records, "has_audit", "personnel",
                                     lambda p: p.get("call_status") == "ok" and p.get("appointments_count", 0) >= 1),
        "aoi": _pf(records, "has_aoi", "aoi",
                   lambda p: p.get("call_status") == "ok" and p.get("amendments_count", 0) >= 1),
        "compensation": _pf(records, "has_compensation", "compensation",
                            lambda p: p.get("call_status") == "ok" and p.get("items_count", 0) >= 1),
        "retirement_pay_call": _pf(records, "has_retirement", "retirement_pay",
                                   lambda p: p.get("call_status") == "ok"),
        "provisional_fs_call": _pf(records, "has_financials", "provisional_fs",
                                   lambda p: p.get("call_status") == "ok"),
        "provisional_fs_metrics_6plus": _pf(records, "has_financials", "provisional_fs",
                                            lambda p: p.get("call_status") == "ok"
                                                       and p.get("metric_keys_filled", 0) >= 6),
        "provisional_fs_metrics_4plus": _pf(records, "has_financials", "provisional_fs",
                                            lambda p: p.get("call_status") == "ok"
                                                       and p.get("metric_keys_filled", 0) >= 4),
    }

    # KOSPI vs KOSDAQ split
    by_market = {
        "kospi": _market_g1([r for r in records if r["_universe"] == "kospi"]),
        "kosdaq": _market_g1([r for r in records if r["_universe"] == "kosdaq"]),
    }

    # Topic frequency (in OK records)
    topics_freq = {}
    if ok:
        for key in ["has_director", "has_audit", "has_compensation", "has_aoi", "has_retirement", "has_financials"]:
            n = sum(1 for r in ok if _topic(r, key))
            topics_freq[key] = {"n": n, "pct": round(n * 100.0 / len(ok), 1)}

    return {
        "totals": {
            "n_records": len(records),
            "n_ok": len(ok),
            "n_failed": len(records) - len(ok),
            "ok_pct": round(len(ok) * 100.0 / len(records), 1),
        },
        "status_counts": dict(status_counter),
        "topic_frequency": topics_freq,
        "g1_overall": {k: {"ok": v["ok"], "n": v["n"], "pct": v["pct"]} for k, v in g1.items()},
        "g1_per_market": by_market,
        "fail_samples": {k: v["fails"][:8] for k, v in g1.items() if v["fails"]},
        "non_ok_records": [
            {"ticker": r["ticker"], "name": r["name"], "status": r.get("status"),
             "error": (r.get("error") or "")[:120], "_source": r["_source_file"]}
            for r in records if r.get("status") != "ok"
        ],
    }


def _market_g1(records: list[dict]) -> dict[str, Any]:
    if not records:
        return {}
    return {
        "n_records": len(records),
        "n_ok": sum(1 for r in records if r.get("status") == "ok"),
        "agenda_ok_pct": _pf(records, None, "agenda",
                             lambda p: p.get("call_status") == "ok" and p.get("total_titles", 0) >= 1)["pct"],
        "personnel_pct": _pf(records, "has_director", "personnel",
                             lambda p: p.get("call_status") == "ok" and p.get("appointments_count", 0) >= 1)["pct"],
        "aoi_pct": _pf(records, "has_aoi", "aoi",
                       lambda p: p.get("call_status") == "ok" and p.get("amendments_count", 0) >= 1)["pct"],
        "compensation_pct": _pf(records, "has_compensation", "compensation",
                                lambda p: p.get("call_status") == "ok" and p.get("items_count", 0) >= 1)["pct"],
        "pfs_metrics_6plus_pct": _pf(records, "has_financials", "provisional_fs",
                                     lambda p: p.get("call_status") == "ok"
                                                and p.get("metric_keys_filled", 0) >= 6)["pct"],
    }


def main():
    records = _load_all()
    print(f"[agg] loaded {len(records)} records from {len(list(AUDIT_DIR.glob('iter0*_*.json')))} batch files")
    result = _agg(records)
    out_path = AUDIT_DIR / "phase1_aggregate.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[agg] wrote {out_path}")
    print()
    print(f"--- Totals ---")
    print(json.dumps(result["totals"], ensure_ascii=False, indent=2))
    print(f"--- Topic frequency ---")
    print(json.dumps(result["topic_frequency"], ensure_ascii=False, indent=2))
    print(f"--- G1 Overall ---")
    print(json.dumps(result["g1_overall"], ensure_ascii=False, indent=2))
    print(f"--- G1 per market ---")
    print(json.dumps(result["g1_per_market"], ensure_ascii=False, indent=2))
    print(f"--- Status counts ---")
    print(json.dumps(result["status_counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
