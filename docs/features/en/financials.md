# Financial Metrics

Pulls the company's financial statements and **automatically computes dozens of core metrics such as ROE (return on equity)**.
It does not just show DART's raw numbers — it processes them into ratios that capture profitability, stability, and cash flow at a glance.

## Six scopes

| scope | What it shows |
|---|---|
| **summary** | Core metrics summary (current + prior) |
| **yearly** | Yearly metrics (multi-year) |
| **quarterly** | Quarterly metrics (4Q × multi-year) |
| **yoy** | Year-over-Year |
| **qoq** | Quarter-over-Quarter |
| **audit_opinion** | Auditor and audit-opinion trend |

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

## Technical reference (for developers)

> The pages below are tool I/O specs. General users do not need them.

- [financial_metrics](../../../wiki/tools/financial_metrics.md) — financial metrics
- [corp_gov_report](../../../wiki/tools/corp_gov_report.md) — corporate governance report (15 indicators)
