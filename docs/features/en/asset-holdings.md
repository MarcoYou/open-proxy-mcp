# Asset-Holdings Screen

**Answers "does this company hold more asset value than its market cap suggests?"** It tiers cash, investment property, equity holdings, and associate/joint-venture stakes from the audited consolidated balance sheet, then re-marks listed stakes at today's price and compares the total to market cap.

## What it answers

- **Asset composition** — cash & equivalents, tradable securities (FVPL), long-term securities, strategic FVOCI stakes, controlling/associate stakes (equity method), investment property, and tangible assets, split by purpose.
- **One-line character read** — automatically diagnoses which purpose dominates relative to market cap and labels the story: "trading-heavy" (short-term stocks/funds), "real-estate play" (investment property unrelated to the core business), "holdco discount" (subsidiary/associate stakes), or "friendly-stake" (cross-shareholding unrelated to control).
- **Mark-to-market on listed stakes** — re-prices listed holdings (from the other-corporate-investment filing) at today's closing price and shows the unrealized gap vs. book value — e.g. a holding company still carrying a subsidiary at cost years after it listed and re-rated.
- **Coverage ratios vs. market cap** — surplus assets (cash + liquid securities + investment property) and equity NAV (controlling/associate stakes) each compared to market cap, flagging when they exceed it.
- **Pledged-asset & contingent-liability warnings** — flags when assets are pledged as collateral or contingent liabilities/guarantees exist, since both should be subtracted from NAV (see `scope="detail"` for the original text).
- Correctly separates the common holding-company case where subsidiary and associate stakes are reported as one combined line item — instead of silently dropping it. REITs are auto-excluded from the "hidden asset" signal since investment property is their core business, not surplus.
- **States consolidated vs separate** (`scope="detail"`) — note tables now carry which financial statements they came from. Mistaking separate-entity assets for consolidated changes the whole NAV calculation. Warns when one region mixes both, when separate was read although consolidated notes exist, or when the section title and the declaration disagree. **If the filing declares nothing, no basis is invented.**
- **When a value is missing, it says why and quotes the original** — the old "collateral note not detected (unencumbered or undisclosed)" left readers unable to tell which. It now splits into not disclosed / not here / no table / not found, and quotes the surrounding wording so you can check for yourself.
- Core design: ratios are **gross, before debt** — use alongside PBR. Whether to net out pledged/contingent haircuts is left to the reading AI's judgment.

## Ask like this

> "Show me Yungpoong's asset holdings — does it hold more than its market cap?"
>
> "Re-mark this company's listed subsidiary stakes at today's price"
>
> "What's Cheonil Express's asset composition and surplus-asset ratio vs. market cap?"

## Related features

- [Business details](business-details.md) — sites/production facilities live there; footnote-level asset cost-vs-fair-value lives here
- [Valuation](price_multiple_data.md) — market-cap/PER/PBR time series there; point-in-time asset composition here
- [Financial metrics](financials.md) — pair with debt/profitability ratios (these ratios are pre-debt)
