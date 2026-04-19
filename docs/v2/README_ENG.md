# OpenProxy MCP v2

MCP server for Korean listed-company governance and shareholder-return analysis. Analyst-facing data tool design.

Branch: `release_v2.0.0` — ready for deployment.

---

## Core Design Principles

| Principle | Implementation |
|---|---|
| **Facts vs Policy split** | `dividend` (actual payment) vs `value_up` (future commitment) as separate tools. `treasury_share` is also fact. |
| **Hints over auto-classification** | `proxy_contest` doesn't make binary "contest" judgment. Surfaces `has_contest_signal` + filer cross-reference flags (5%-purpose/litigation) for the LLM/analyst to synthesize. |
| **Retail activism platform split** | Conduit(ACT)/Heyholder/BsideKorea filers classified as `retail_activism` — avoids false positives vs proxy fights. |
| **Listed universe only** | `company` automatically excludes unlisted corps. |
| **Evidence-centric** | `EvidenceRef` schema: `rcept_no`, `rcept_dt`, `report_nm`, `viewer_url` — citation link on every output. |
| **3-stage fallback** | On structured-parse failure: DART viewer HTML crawl → raw text excerpt. No PDF dependency. |
| **Scope-based progressive load** | 6 data tools accept `scope` param to fetch only what's needed. |

---

## Tools (11 total)

### Data Tools (7)

| tool | one-liner | scopes |
|---|---|---|
| **`company`** | Entity resolution + recent filings index | — |
| **`shareholder_meeting`** | AGM/EGM agenda, candidates, comp, AOI changes, results | summary / agenda / board / compensation / aoi_change / results / full |
| **`ownership_structure`** | Major holders, 5% blocks, treasury balance | summary / major_holders / blocks / treasury / control_map / timeline |
| **`dividend`** | Actual-payment dividend facts | summary / detail / history / policy_signals |
| **`treasury_share`** | Treasury share events (acq/disp/cancel/trust) | summary / events / acquisition / disposal / cancelation / annual |
| **`proxy_contest`** | Proxy/litigation/5%-active signals + hints | summary / fight / litigation / signals / timeline / vote_math |
| **`value_up`** | Value-up plan + shareholder-return commitments | summary / plan / commitments / timeline |
| **`evidence`** | Citation info provider (zero API) | — |

### Action Tools (3)

| tool | output | upstream |
|---|---|---|
| **`prepare_vote_brief`** | Voting memo | shareholder_meeting + ownership_structure + evidence |
| **`prepare_engagement_case`** | Engagement memo | ownership_structure + proxy_contest + value_up + evidence |
| **`build_campaign_brief`** | Campaign fact brief | proxy_contest + ownership_structure + shareholder_meeting + evidence |

---

## Common Response Schema

All tools return a `ToolEnvelope` (md/json dual support):

```json
{
  "tool": "shareholder_meeting",
  "status": "exact|ambiguous|partial|conflict|requires_review|error",
  "subject": "Samsung Electronics",
  "warnings": [...],
  "data": { "...": "tool-specific" },
  "evidence_refs": [{
    "rcept_no": "20260312000987",
    "rcept_dt": "2026-03-12",
    "report_nm": "[Corrected] AGM Convocation Notice",
    "viewer_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260312000987",
    "source_type": "dart_xml",
    "section": "AGM Convocation",
    "note": "Meeting: Mar 18, 2026"
  }],
  "next_actions": [...]
}
```

---

## Source Policy

Priority:
1. DART OpenAPI (structured)
2. DART `document.xml` (filing body)
3. DART viewer HTML crawl (re-parse fallback)
4. KIND HTML (whitelisted types only)
5. Naver (sector enrichment only)
6. Raw text fallback

**Excluded**: PDF downloads. KIND filings outside whitelist.

---

## 3-stage Fallback (`shareholder_meeting`)

```
1. DART API/XML structured parsing
   → success: scope-specific data returned

2. DART viewer HTML crawl + re-parse
   → success: corrected structured data + warning

3. Raw text fallback (prefers richer between viewer/XML text)
   → status=requires_review + data.raw_text_excerpt (≤6,000 chars)
   → LLM/analyst interprets original text directly
```

Handles non-standard XML filings (Samchundang Pharm, Peptron, etc.).

---

## Notable Edge Case Handling

### Fiscal-year bucketing for year-end dividend
Korean year-end dividends accrue to fiscal year-end but are disclosed in next Feb-Mar. Bifurcated record-date regime (post-2024) can push record_date into Mar-Apr.
→ `dividend` applies: `dividend_type=year-end` ⇒ **fiscal_year = rcept_dt year - 1**.

### Retail activism platforms
Filers like Conduit(ACT)/Heyholder/BsideKorea classified as `side="retail_activism"`.
→ Excluded from `shareholder_side_count` and `has_contest_signal`.

### meta_amendment filings (value_up)
"High-dividend tax status re-filing" contains no substantive plan text.
→ Service surfaces actual plan filing as `latest_plan` separately.

### for_cancelation treasury acquisition
When `aq_pp` contains "소각" (cancelation), flag as `for_cancelation=True`.
→ Catches firms (e.g. Mirae Asset Securities) declaring cancelation intent at acquisition stage without separate cancelation decision filings.

---

## Quick Links

- [Architecture (KR)](../../wiki/analysis/release_v2-tool-아키텍처.md)
- [Validation matrix (KR)](../../wiki/analysis/release_v2-public-tool-검증-매트릭스.md)
- [New tool addition policy (KR)](../../wiki/decisions/tool-추가-검증-정책.md)
- [DART-KIND mapping whitelist (KR)](../../wiki/decisions/DART-KIND-매핑-화이트리스트-2026-04.md)

---

## Deployment

Toolset selection via environment variable:

```bash
# v2 only (recommended)
OPEN_PROXY_TOOLSET=v2

# v1 + v2 parallel
OPEN_PROXY_TOOLSET=hybrid

# v1 only (default, backward compat)
OPEN_PROXY_TOOLSET=v1
```

Local run + remote MCP exposure:

```bash
OPEN_PROXY_TOOLSET=v2 .venv/bin/python -m open_proxy_mcp.server \
    --transport streamable-http --toolset v2

# Additional host allow-list (e.g. ngrok):
FASTMCP_ALLOWED_HOSTS="example.ngrok-free.dev" \
    OPEN_PROXY_TOOLSET=v2 .venv/bin/python -m open_proxy_mcp.server ...
```

---

## v1 → v2 Migration

v1 (36 tools) → v2 (11 tools):

| v1 | v2 |
|---|---|
| `corp_identifier` | `company` |
| `agm_search` + `agm_items` + various `agm_*_xml` | `shareholder_meeting(scope=...)` |
| `ownership_major` + `ownership_block` + `ownership_full_analysis` | `ownership_structure(scope=...)` |
| `ownership_treasury` + `ownership_treasury_tx` | `treasury_share` + `ownership_structure(scope="treasury")` |
| `div_detail` + `div_full_analysis` | `dividend(scope=...)` |
| `proxy_fight` + `proxy_litigation` + `proxy_full_analysis` | `proxy_contest(scope=...)` |
| `value_up_plan` | `value_up(scope=...)` |
| `governance_report` | `prepare_vote_brief` / `prepare_engagement_case` / `build_campaign_brief` |

v1 docs: [v1 README](../v1/README_ENG.md)
