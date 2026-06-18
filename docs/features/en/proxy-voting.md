# AGM Proxy Voting

Helps you decide how to vote on each AGM agenda item **before the meeting**.
It structures the items in the AGM notice and returns a per-item **FOR / AGAINST / REVIEW** recommendation with rationale.
The basis is OPM's own **Open Proxy Guideline** (minority protection, governance transparency, long-term value, traceability).

## First — three design principles

1. **Judges only on information inside DART filings.** It does not read external news, reputation, or market rumor — that is for the user to verify.
2. **AGAINST only on a clear hard trigger.** Ambiguous concerns are not auto-rejected; they return **REVIEW** for the analyst.
3. **No auto-FOR when data is missing.** If no supporting filing is found, it returns `NO_DATA` to prompt a manual read (preventing a wrong auto-FOR).

## When AGAINST appears — these four are all of them

| Agenda | AGAINST condition |
|---|---|
| Financial statements | **Full capital impairment** (delisting cause) |
| Financial statements | **Qualified / adverse audit opinion** (not clean) |
| Director election | **Disqualification** (legal ineligibility / minor) |
| Audit-committee member | **Long tenure** (5-year-rule breach — independence) |

→ Every other agenda returns only **FOR or REVIEW**. Even a steep compensation hike or removing cumulative voting is **REVIEW**, not AGAINST (OPM does not conclude; the user decides).

## Per-agenda decision matrix

| Agenda type | FOR | REVIEW | AGAINST |
|---|---|---|---|
| **Financial statements** | Clean opinion + no impairment | Data unconfirmed (NO_DATA) | Full impairment / qualified-adverse |
| **Outside director election** | Independence + no disqualification | Independence concern (largest-shareholder tie / dealing with company / former employee) / long tenure / prior accounting-risk history | Disqualification / (audit committee) long tenure |
| **Inside director re-election** | No disqualification + tenure performance good/moderate | Tenure performance weak or bad (not a legal disqualification → review) | Disqualification |
| **Inside director (new)** | No disqualification (no tenure → performance N/A) | — | Disqualification |
| **Director/auditor compensation limit** | Utilization reasonable / cut / minor change | Low utilization yet increase / 50%+ large increase / earnings slowdown + increase / impairment | (none) |
| **Retirement pay** | Formal change (pension adoption etc.) | Golden-parachute keywords / new clause / 2x+ increase | (none) |
| **Articles amendment** | Formal / minority-protection wording / fiduciary duty | Removes cumulative voting / supermajority / board-size cut / authorized-shares increase / new-share clause | (none) |
| **Treasury shares** | Cancellation (shareholder return) | Disposal (possible friendly stake) | (none) |
| **Dividend** | Profit + sound capital / REIT | Loss / impairment / payout ratio > 200% | (none) |

## Inside-director re-election — tenure performance 2×3 matrix

Treating a company-nominated inside director as "FOR unless disqualified" creates a **status-quo bias**.
OPM scores each inside director's **operating performance during tenure** across 6 cells and feeds it into the recommendation.

| Metric | avg | trend |
|---|---|---|
| **ROE** | average score | trend score |
| **Debt ratio** | average score | cumulative-change score over tenure |
| **CSR** (dividend + cancellation / net income) | average score | trend score |

Each cell: **good +2 / moderate +1 / weak 0 / bad -1** → total maps to a grade:

| Total | Grade | Recommendation |
|---|---|---|
| +7 or above | good | FOR |
| +3 to +6 | moderate | FOR |
| 0 to +2 | weak | REVIEW |
| below 0 | bad | REVIEW |

> A bad grade is **REVIEW, not an automatic AGAINST** — it is not a legal disqualification, so the final call is the user's.

**Special rules**: full capital impairment → ROE/leverage avg auto-bad / loss + return activity → CSR weak / loss + no return → CSR moderate (conservatism).

> Validated on KOSPI 100 + KOSDAQ 50 (n=128) — classification coverage 100%, distribution good 29.7% / moderate 45.3% / weak 18.0% / bad 7.0%.

## How to use

> "Show me per-item voting opinions for Samsung Electronics' 2026 AGM"

A single question returns the agenda list + per-item recommendation (FOR/REVIEW/AGAINST) + rationale.

## See also

- [AGM Agenda](meeting-agenda.md) — actual post-AGM results and approval rates
- [Financial Metrics](financials.md) — the financial figures behind tenure-performance scoring
- [Shareholder Return](shareholder-return.md) — background for dividend / treasury agenda items
