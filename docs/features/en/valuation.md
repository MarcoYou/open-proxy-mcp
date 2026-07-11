# Valuation

Combines DART filings with official KRX prices to compute **relative-value multiples such as PER, PBR,
and dividend yield**. It doesn't use DART's raw financials as-is — it aligns them to a controlling-interest
basis and auto-converts non-KRW functional currencies via the Bank of Korea (ECOS) FX rate. When a
multiple would be meaningless (losses, capital impairment) it does not force a number, marking it **N/M
(not meaningful)** instead.

## Five scopes

| scope | What it shows |
|---|---|
| **firm** (default) | Deep dive on one company — PER (FY0/TTM) · PBR (MRQ) · dividend yield, live prices |
| **market** | Whole-market (KOSPI/KOSDAQ) cap-weighted PER/PBR + weekly trend |
| **sector** | Per-industry (KSIC) multiples + firm vs. its sector + sector time series |
| **firm_history** | One stock's PER/PBR time series — FY0/TTM/MRQ bases (weekly curve + month-end summary) |
| **explain** | "How was this PER derived?" — the calculation, basis, and source spelled out with actual values |

## Three bases — FY0 / TTM / MRQ

The same PER/PBR differs depending on which period's numbers you use. Mixing them silently distorts the
ratio, so the basis is always stated.

- **FY0**: PER on the most recent confirmed fiscal-year (annual) net income.
- **TTM**: PER on trailing-twelve-month controlling-interest net income — reflects intra-year changes.
- **MRQ**: PBR on most-recent-quarter controlling-interest equity.

Because it is computed off market capitalization, the series stays consistent regardless of adjusted-price
events (stock splits, rights issues).

## What it reads vs. what it computes

| Source item | Computed metric |
|---|---|
| Market cap ÷ controlling net income (FY0/TTM) | **PER** |
| Market cap ÷ controlling equity (MRQ) | **PBR** |
| Dividend per share ÷ price | **Dividend yield** |
| Non-KRW functional-currency financials × ECOS rate | **KRW-converted multiples** |
| Loss / impairment / abnormal-scale detection | **N/M gating + scale guard** |

> Korean convention — net income and equity on a controlling-interest basis, consolidated statements
> first. Prices and market cap from official KRX data.

## How to use it

> "What's Doosan Bobcat's PBR?"

It auto-converts USD functional-currency financials at the Bank of Korea rate, computes PBR, and shows
which basis (FY0/TTM/MRQ) and the conversion detail. Ask with `scope="explain"` to see how the number
was derived.

## See also

- [Financial metrics](financials.md) — the profitability and equity figures behind the multiples' denominators
- [Shareholder return](shareholder-return.md) — how dividend yield and treasury shares tie into payout policy
