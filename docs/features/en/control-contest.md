# Control-Contest Signals

**Gathers signals of control contests and shareholder activism from filings, in chronological order.** OPM **does not declare "contest or not" — it only lays out the signals; the final call is the analyst's.**

## What it answers

- Bundles **lawsuits, 5% bulk-holding dynamics, proxy solicitations, and tender offers** in time order.
- Flags **when a 5% holder's stated purpose flips from passive investment to management participation**, and stretches where the stake moved sharply (accumulation / exit).
- Distinguishes whether the mover is the **incumbent-owner camp or an external force** — judged by whether the filer's name appears in the business report's largest-shareholder registry; a matching name does not mean aligned interests (homonyms possible).
- Lawsuits are de-duplicated (including corrections) and sorted by nature — **management / commercial / unspecified**.
- If meeting results exist, it shows a **contest-intensity grade (stable / watch / contestable)** — computed **only after the meeting**, when an actual vote outcome exists (never beforehand).
- Source: DART lawsuit, bulk-holding, proxy-solicitation, and tender-offer filings. Detail → [proxy_contest](../../../wiki/tools/proxy_contest.md).

## Ask it like this

> "Summarize control-contest signals for Korea Zinc"
> "Any external party that bought over 5% of this company?"

## See also

- [Ownership Map](ownership.md) — who holds what (the ownership structure itself)
- [Proxy Voting Support](proxy-voting.md) — voting judgment on contested items
