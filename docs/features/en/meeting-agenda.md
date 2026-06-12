# AGM Agenda

Structures the AGM notice text by agenda item, and links the outcome via the AGM results filing.

## What it reads and what it computes

| Filing item referenced | What is structured / judged |
|---|---|
| The **agenda (item) list** in the notice | Classify item type (director/auditor election / articles amendment / compensation limit / financial-statement approval, etc.) |
| Director/auditor **nominee profile** items | Per-nominee career and recommender (input to voting judgment) |
| **Director/auditor compensation limit** items | Limit amount and year-over-year change |
| Before/after clauses of an **articles-amendment** item | Clause comparison (identify defensive clauses, cumulative-voting removal, etc.) |
| **Financial-statement summary** up for approval | Linked to audit opinion and capital status |
| **AGM results** filing | Per-item pass/reject + for/against ratio |

## Scopes — before the AGM (notice)

| scope | What it shows |
|---|---|
| **summary** | Agenda overview |
| **agenda** | Agenda list (director/auditor election, articles amendment, compensation limit, etc.) |
| **board** | Director/auditor nominees — career and recommender |
| **compensation** | Compensation limit — director/auditor limit approval items |
| **aoi_change** | Articles amendment — before/after clause comparison |
| **prov_financials** | Financial statements — summary up for approval |

## Scopes — after the AGM (results)

| scope | What it shows |
|---|---|
| **results** | Resolution outcome (each item pass/reject) + for/against ratio |

The notice (before) and results (after) come in different filing formats, so they are split into separate tools — `shareholder_meeting_notice` (before) / `shareholder_meeting_results` (after).

## How to use

> "Show me LG Chem's 2026 AGM agenda" (notice)
> "Hyundai Motor's last AGM results and approval rates" (results)

## See also

- [AGM Proxy Voting](proxy-voting.md) — recommendations on **how to vote** (this agenda data is the input)

## Technical reference (for developers)

> The pages below are tool I/O specs. General users do not need them.

- [shareholder_meeting_notice](../../../wiki/tools/shareholder_meeting_notice.md) — notice structuring (before)
- [shareholder_meeting_results](../../../wiki/tools/shareholder_meeting_results.md) — resolution outcome / approval rates (after)
- [shareholder_meeting_results](../../../wiki/tools/shareholder_meeting_results.md) — post-AGM per-agenda outcomes and vote rates
