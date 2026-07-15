# Corporate Risk Events

**Tracks incident filings like serious accidents, embezzlement, and production stoppages.** View one company's risk history, or — with no company specified — **scan the whole market for recent incidents** ("what blew up where lately"). It organizes only the facts read from filings (casualties, amount, cause, stage) and **does not score risk.**

## What it answers

- Three categories currently served reliably — **serious accidents** (workplace death/injury), **embezzlement / breach of trust** (amount, % of equity; suspicion → in-progress → confirmed stages), and **production stoppage / business suspension** (size vs revenue).
- **With a company specified**, it covers that company's (and subsidiaries') last 24 months; **without one**, it scans the whole market's last 30 days (up to 90) by category.
- When the same accident is filed by both a holding company and its operating unit (e.g. Hanwha + Hanwha Aerospace), it merges to the latest so **casualties aren't double-counted.**
- Source: DART major-matter / ad-hoc filings + source parsing. Detail → [risk_events](../../../wiki/tools/risk_events.md).

## Ask it like this

> "Which companies had accidents or incidents in the last month?"
>
> "Any serious-accident filing for Hanwha Ocean?"

## Good to know

- Exchange serious-accident ad-hoc disclosures are **observable only from October 2025** — an absence of filings before that does not mean no accidents.
- Small/mid-caps and subcontractors are often not the filing entity (the authoritative accident record is Korea's Ministry of Employment and Labor).

## See also

- [Financial Metrics](financials.md) — how losses/embezzlement amounts affect financials
- [Evidence](../../../wiki/tools/evidence.md) — verify the incident's original filing
