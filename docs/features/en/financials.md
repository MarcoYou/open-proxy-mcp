# Financial Metrics

Pulls the company's financial statements and **automatically computes dozens of core metrics such as ROE (return on equity)**.
It does not just show DART's raw numbers — it processes them into ratios that capture profitability, stability, and cash flow at a glance.

## Six scopes

| scope | What it shows |
|---|---|
| **summary** | Core metrics summary (current + prior) |
| **yearly** | Yearly metrics (multi-year) |
| **quarterly** | Quarterly metrics (4Q × multi-year, standalone 3-month figures with QoQ/YoY attached, margins in %p; Q4 = annual minus 9-month cumulative) |
| **yoy** | Year-over-Year |
| **qoq** | Quarter-over-Quarter (both sides standalone) |
| **audit_opinion** | Auditor and audit-opinion trend |

## Interim basis — current quarter / cumulative / TTM

DART reports carry different period semantics per item: the **income statement** is the current 3 months (cumulative is separate), **cash flow** is cumulative, and the **balance sheet** is a point-in-time balance. Mixing these silently distorts ratios (e.g. operating cash flow ÷ operating profit = 6 months ÷ 3 months), so for quarterly/half-year reports the tool handles this automatically:

- **Two P&L bases**: cumulative (year-to-date) by default, plus the **current quarter (standalone, 3 months)** for half-year/Q3 reports, derived by differencing the prior report.
- **Turnover days (DSO/DIO/CCC) on a TTM (trailing-12-month) basis**: annualizing a single quarter distorts the figure in boom/swing periods (receivables can rise while days fall), so trailing-4-quarter revenue is used as the denominator for direct comparison with annual figures.
- **ROE/ROA are not annualized** — kept as the period value, labeled as such.
- The basis in use (`current quarter / cumulative / TTM`) is **always stated** in the output.

When no year is given, quarterly views target the latest already-disclosed quarter, while annual-style views (summary/yearly) use the most recent fiscal year.

## What it reads and what it computes

From the business report's financial statements (balance sheet, income statement, cash-flow statement), it computes the following.

| Item referenced | Computed metric |
|---|---|
| Net income (controlling) ÷ equity | **ROE** (return on equity) |
| Net income ÷ total assets | **ROA** (return on assets) |
| Operating income ÷ revenue | **Operating margin** |
| Liabilities ÷ equity | **Debt ratio** |
| Equity vs paid-in capital | **Capital impairment** (partial / full) |
| Audit opinion in the audit report | **Clean / qualified trend** |

**Advanced metrics**: ROE DuPont 3-way breakdown (margin / asset turnover / leverage), free cash flow (FCF), cash conversion cycle (CCC), book-income vs cash-flow gap.

> Korean basis — net income is controlling-interest, statements are consolidated-first.

## How to use

> "How did POSCO Holdings' ROE and debt ratio change over the last 3 years?"

Profitability, stability, and cash-flow metrics come back organized by period.

## See also

- [AGM Proxy Voting](proxy-voting.md) — these financials (impairment, audit opinion, tenure performance) feed the voting judgment
- [Shareholder Return](shareholder-return.md) — the financial backdrop for dividends and treasury shares
