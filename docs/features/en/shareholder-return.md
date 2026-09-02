# Shareholder Return

**Places the "promise (policy)" next to the "actual execution (fact)" to see whether shareholder return was delivered.** Bundles dividends, treasury shares, and value-up (corporate-value-improvement) plans.

## What it answers

- **Dividends**: DPS and total payout, payout ratio (dividend ÷ net income), dividend yield on record-date price, and the multi-year trend — confirmed, actually-paid facts, not future promises.
- **Treasury shares**: matches the buy → dispose → cancel cycle. The key is that **buying without cancellation is weak return** — it checks whether cancellation was the stated purpose and whether it actually happened.
- **Value-up**: cross-references the plan's promises against actual treasury-share cancellation to separate **"lip-service value-up" from real return.**
- The dedicated `shareholder_commitment` feature tracks promise-vs-delivery in one shot, and even computes the **book-value gain/loss of cancellations against their purchase price, in KRW** (dividends shown separately, since their book-value effect runs the opposite direction).
- Source: DART dividend-decision, business-report dividend section, treasury-share, and value-up filings. Detail → [dividend](../../../wiki/tools/dividend_disclosure.md) · [treasury_share](../../../wiki/tools/treasury_share.md) · [value_up](../../../wiki/tools/value_up.md).

## Ask it like this

> "Show KT&G's dividend and treasury-cancellation history"
>
> "Did this company actually keep its value-up plan?"

## See also

- [Ownership Map](ownership.md) — what treasury shares mean in the ownership structure
- [Proxy Voting Support](proxy-voting.md) — voting judgment on dividend/treasury items
