"""KOSPI 200 + KOSDAQ 50 보수/퇴직 audit 통합 + G1-G4 측정.

iter01_*.json (extend) + iter02/iter03 (이전 ralph) 합산.
"""

from __future__ import annotations
import argparse
import glob
import json
from collections import Counter
from pathlib import Path

ROOT = Path("/Users/marcoyou/Projects/open-proxy-mcp")


def load_summaries(patterns: list[str]) -> tuple[list, dict]:
    """Returns (relevant_decisions_flat, n_co/n_ok totals)."""
    all_records = []
    n_co = 0
    n_ok = 0
    seen_companies = set()
    for pat in patterns:
        for fp in sorted(glob.glob(str(ROOT / pat))):
            d = json.load(open(fp))
            for r in d['results']:
                co_key = r['name']
                if co_key in seen_companies:
                    continue  # de-dupe overlapping batches
                seen_companies.add(co_key)
                n_co += 1
                if r.get('status') != 'exception':
                    n_ok += 1
                for dec in r.get('relevant_decisions', []):
                    all_records.append({**dec, '_company': r['name'], '_ticker': r['ticker'], '_source': Path(fp).name})
    return all_records, {'n_co': n_co, 'n_ok': n_ok}


def measure(records: list, totals: dict) -> dict:
    cats = ('director_compensation', 'audit_compensation', 'retirement_pay')
    out = {'totals': totals, 'categories': {}}
    for cat in cats:
        items = [r for r in records if r['category'] == cat]
        dist = Counter(r['decision'] for r in items)
        n_no_data = dist.get('NO_DATA', 0)
        n = len(items)
        # G3 majority match
        with_majority = [r for r in items if r.get('majority')]
        match_n = sum(1 for r in with_majority if r['decision'] == r['majority']['majority'])
        out['categories'][cat] = {
            'n_decisions': n,
            'distribution': dict(dist),
            'g1_parse_success_pct': round((n - n_no_data) / max(n, 1) * 100, 1),
            'g3_majority_n': len(with_majority),
            'g3_match_n': match_n,
            'g3_match_pct': round(match_n / max(len(with_majority), 1) * 100, 1) if with_majority else None,
        }
    # G2 trigger sample
    trigger_dec = Counter()
    for r in records:
        if r['decision'] in ('AGAINST', 'REVIEW'):
            trigger_dec[(r['category'], r['decision'])] += 1
    out['g2_trigger_sample'] = {f"{c[0]}.{c[1]}": v for c, v in sorted(trigger_dec.items())}
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--include-prev', action='store_true', help='이전 ralph iter02/iter03 결과 포함')
    parser.add_argument('--out', default=None)
    args = parser.parse_args()

    patterns = ["wiki/architecture/audits/data/260505_compensation_retirement_extend/iter01_kospi_*.json",
                "wiki/architecture/audits/data/260505_compensation_retirement_extend/iter01_kosdaq_*.json"]
    if args.include_prev:
        patterns.extend([
            "wiki/architecture/audits/data/260505_compensation_retirement/iter03_*.json",
        ])

    records, totals = load_summaries(patterns)
    summary = measure(records, totals)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
