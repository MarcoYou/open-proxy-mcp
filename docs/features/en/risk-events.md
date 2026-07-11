# Corporate Risk Events

Tracks filings for events like **serious industrial accidents, embezzlement/breach of trust, and
production halts**. View one company's risk history, or — with no company specified — **scan the whole
market for recent events** to see "what happened where lately." It organizes only the facts read from the
filing (casualties, amounts, cause, stage); it does not pass judgment on the risk.

## Tracked categories

The default query returns only the **three active categories** whose parsing is stable on a sufficient
sample. The other three are parsed and verified but excluded from the default, running only on explicit
request.

| Category | Status | What |
|---|---|---|
| Serious accident | **active** | Workplace deaths/injuries (occurrence / penalty-confirmation stages) |
| Embezzlement / breach of trust | **active** | Insider embezzlement (allegation / progress / confirmed stages, amount vs. equity) |
| Production halt / suspension | **active** | Shutdown or business suspension (scale vs. revenue) |
| Derivative trading loss | mute | Derivative valuation/trading losses |
| Rehabilitation / default | mute | Court rehabilitation, default, bank-transaction suspension |
| Dissolution | mute | Grounds for dissolution |

## Company lookup vs. market scan

| Mode | Condition | Scope |
|---|---|---|
| **Per-firm** | company given | That company (+ subsidiaries) over the last 24 months |
| **Market scan** | company blank | Whole market, last 30 days (up to 90), classified by category |

Turning on `include_details` parses the **full text** of the top few filings to fill in fields like
casualty counts, loss amounts, and cause (at the cost of more filing-text calls).

## What it reads vs. what it computes

| Source item | What it organizes |
|---|---|
| Death/injury counts in a serious-accident filing | Casualty tally (dedup per incident, de-double-counted) |
| Amount and equity ratio in an embezzlement filing | Scale metric + allegation → progress → confirmed stage tracking |
| Revenue ratio in a production-halt filing | Business-impact scale |
| Parent filing + subsidiary filing | Same incident's duplicate filings merged to the latest (supersede) |

> When the same accident is filed by both a holding company and its operating company (e.g. Hanwha +
> Hanwha Aerospace), they are merged to the latest filing so casualties aren't double-counted.

## How to use it

> "Which companies had accidents or incidents in the past month?"

A market scan classifies recent events by category. Name a specific company (e.g. "Hanwha Ocean serious
accident filings") to track that company's history stage by stage.

> **Limits**: Exchange serious-accident disclosures have only been observed since October 2025. Absence of
> filings in earlier periods does not mean no accidents, and smaller firms/subcontractors are often absent
> as filers (the authoritative industrial-accident statistics are from the Ministry of Employment and Labor).

## See also

- [Financial metrics](financials.md) — how loss/embezzlement amounts affect the financials
- [Source tracing](../../../wiki/tools/evidence.md) — check the original event filing
