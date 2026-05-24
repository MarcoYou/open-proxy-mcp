---
type: audit-data
title: Agenda relation local corpus
date: 2026-05-24
---

# Agenda Relation Local Corpus

Purpose: local parser/audit/regression corpus for agenda hierarchy, conditional
director slates, sub-agenda to amendment mapping, and normalized raw text review.
Production MCP tools still fetch DART live and do not persist user-request data.

## Contents

- `manifest.json`: 50-sample index and parser coverage summary.
- `documents/{rcept_no}.json`: DART `document.xml` decoded body plus parsed outputs.
- `relation_audit/relation_scan.json|md`: local corpus relation scan.
- `relation_audit/kospi_001_300_relation_live_rerun_after_parser_fix.json`: KOSPI300 live rerun after parser fixes.
- `relation_audit/kosdaq_top50_relation_live.json`: KOSDAQ top 50 spot check.

Each document stores:

- `document_xml`: decoded XML/HTML string from DART `document.xml`.
- `text`: normalized parser text from the same document.
- `parsed.agendas_raw`: raw parser agenda tree.
- `parsed.agendas`: public-style normalized agenda tree.
- `parsed.aoi_change`: articles-of-incorporation amendments.
- `parsed.retirement_pay`: retirement-pay amendments.
- `parsed.board`: personnel appointment parsing.
- `parsed.compensation`: compensation-limit parsing.

## Sample Mix

| bucket | count | purpose |
|---|---:|---|
| controversial | 20 | dispute/proxy-fight/shareholder-proposal prone companies |
| large_cap | 15 | 2T+ or law-layer-rich companies |
| small_mid_cap | 15 | non-mega-cap parser variety |

## Initial Coverage

| metric | value |
|---|---:|
| documents | 50 |
| ok | 50 |
| total agenda tree nodes | 631 |
| docs with AOI amendments | 44 |
| docs with retirement amendments | 4 |
| docs with board appointments | 44 |
| agenda parser zero-node fixture | 1 (`호텔신라`) |

## Final KOSPI300 Rerun (2026-05-25)

After parser fixes for meeting type, period-form agenda markers, candidate table
boundaries, and `4. 목적사항` fallback:

| status | count |
|---|---:|
| exact | 298 |
| no_filing | 2 |
| requires_review | 0 |
| timeout/exception | 0 |

Remaining `no_filing` companies are fiscal-year timing cases:

- `신영증권`: March fiscal year-end, no annual notice currently available in DART.
- `프레스티지바이오파마`: June fiscal year-end, only extraordinary meeting notice currently available in DART.

## Next Use

1. Use this corpus for parser regression before changing agenda boundary logic.
2. Add future 비표준 정정공고/image-heavy cases as small targeted fixtures instead
   of blindly expanding the whole corpus.
3. Keep production MCP behavior live-fetch only; this corpus is test/audit data.
