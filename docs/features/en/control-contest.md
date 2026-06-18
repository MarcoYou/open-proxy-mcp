# Control-Contest Signals

Gathers signals of a control contest or activism (a shareholder actively intervening in management) from filings.
OPM does not conclude "contest or not" — it lists the signals in a structured way, and the analyst makes the final call.

## What it reads and what it computes

| Filing item referenced | What is computed / judged |
|---|---|
| The "case name" field of a **litigation filing** | After excluding amendments, classify as control / commercial / unspecified + distinguish filed vs ruling stage |
| Shares held and ownership % in the **5% bulk-holding report** | Track ownership changes over time; flag an abrupt move of **±5%p or more** vs the prior report (accumulation / exit) |
| The **holding purpose** field of the bulk-holding report | Judge a shift from passive investment → management participation |
| Presence of a **proxy solicitation document / tender offer filing** | Whether a proxy fight or tender offer is underway |
| Filer name vs the **business report's largest-shareholder registry** | Classify the accumulating party as the incumbent owner vs an external force |
| **AGM results** (approval rates, rejected items) | Compute a post-AGM contest-intensity grade (stable / watch / contestable) |

## Six scopes

| scope | What it shows |
|---|---|
| **summary** | Contest signals overview |
| **fight** | Direct-contest filings — proxy solicitation, tender offer |
| **litigation** | Litigation — classification, dedup, case-name parsing |
| **signals** | 5% bulk-holding dynamics + purpose shift |
| **timeline** | Contest-related filings in chronological order |
| **vote_math** | Post-AGM vote math + contest-intensity grade |

## Litigation — four-stage classification

Litigation filings are not just listed; after removing duplicates (including amendments), they are classified by nature and stage.

- **Nature**: management / commercial / unspecified
  - Unspecified is refined in order: filing title → company-level inference → **parsing the "case name" in the body** → LLM delegation
- **Stage**: filed / ruling / other

## 5% bulk-holding dynamics

From the 5% bulk-holding report time series, it watches how a force moves.

- **Purpose shift** — when the stated holding purpose changes from passive to management participation
- **Sustained accumulation** — reporting frequency and direction (up / down)
- **Abrupt-move flag** — a change of **±5%p or more** vs the prior report (both accumulation and exit)
- **Force split** — active 5% filings are split into incumbent owner vs external force
  - The split is decided by whether the filer's name appears in the business report's largest-shareholder registry

## Contest-intensity grade (vote_math)

After the AGM, contest intensity is shown in three levels based on vote math.

| Grade | Condition |
|---|---|
| **contestable** | A rejected item occurred / or a shareholder-side force + (external 5%+ or a high-opposition item) |
| **watch** | Litigation present / external 5%+ / incumbent-side 5%+ / a high-opposition item |
| **stable** | None of the above |

> This grade is **post-AGM (vote_math) only** — it requires actual vote-contest results.

## How to use

> "Summarize the control-contest signals for Korea Zinc"

Litigation, 5% dynamics, proxy solicitation, and tender offers come back bundled in chronological order.

## See also

- [Ownership Map](ownership.md) — who holds what (the ownership structure itself)
- [AGM Proxy Voting](proxy-voting.md) — voting judgment on contested agenda items
