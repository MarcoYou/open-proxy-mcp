# Shareholder Return

Bundles dividends, treasury shares, and value-up plans to see **whether shareholder return was actually delivered**.
The core is comparing the "promise (policy)" against "actual execution (fact)".

## 1. Dividends — actually-paid facts

From the **cash/in-kind dividend decision** filing and the business report's **dividend section**, it references and computes the following (confirmed, actually-paid facts — not future promises).

- **Dividend per share (DPS) / total dividend** — referenced directly from filing items
- **Payout ratio** — computed as total dividend ÷ net income
- **Dividend yield** — computed as DPS ÷ record-date price
- **Dividend type** — payment cycle identified from the year-end / interim / quarterly field
- **Year-over-year trend** — judged from the business report's dividend section (multi-year columns) as the authority, not decision-filing summation
- **Interim progress** — if the latest year's year-end dividend is unconfirmed, the cumulative figure from quarterly/half reports shows "confirmed through Qn"

Scopes: **summary** · **detail** (dividend-decision detail — record/payment date) · **history** (year-over-year trend).

## 2. Treasury shares — acquire → dispose → cancel cycle

It aggregates treasury **acquisition / disposal / cancellation / trust** decision/result filings and **matches decisions to results as a cycle**.

- Whether acquired treasury shares were actually **cancelled** determines the quality of the return (acquire-but-not-cancel weakens the effect)
- `for_cancelation` flag — when the acquisition purpose explicitly states "cancellation" (captures intent even without a separate cancellation decision)

| scope | What it shows |
|---|---|
| **summary** | Treasury-event overview |
| **annual** | Yearly acquisition / disposal / cancellation totals |

## 3. Value-up — policy / future promise

Reads the value-up (corporate-value-enhancement) filing and its key commitment sentences.

- Each filing is classified as **future_plan / implementation_status / implementation_result / meta_reference**
- **Cross-referenced with treasury-share cancellation** — linking the promise (value-up) to actual execution (cancellation)

| scope | What it shows |
|---|---|
| **summary** | Value-up filing overview |
| **plan** | Plan body + plan title |
| **commitments** | Key commitment sentences |
| **timeline** | Filings in chronological order |

## How to use

> "Show me KT&G's dividends, treasury cancellation history, and whether its value-up plan was executed"

You trace policy (value-up) → promise → actual execution (dividend, treasury cancellation) in one flow.
A dedicated tool, `shareholder_commitment` (added 2026-07-07), combines all three in a single call — for
each buyback-cancellation cycle it also computes the **book-value (BPS) gain or loss** by comparing the
weighted-average purchase price against BPS at the time of purchase (dividends are shown separately as an
overall shareholder-return figure, since their book-value effect runs the opposite direction).

## See also

- [Ownership Map](ownership.md) — what treasury shares mean in the ownership structure
- [AGM Proxy Voting](proxy-voting.md) — voting judgment on dividend / treasury agenda items
