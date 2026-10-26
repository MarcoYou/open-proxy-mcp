
# AGM Analysis and Proxy Voting Advice

**Name a shareholder meeting and get a FOR, AGAINST or REVIEW draft with reasons for every agenda item.** Four advice tools on the hosted public server read the meeting notice and related filings, judge each item by the voting guideline, and have the server check every recommendation. Drafts are not reviewed by a person, and no vote, proxy or electronic ballot is ever sent.

## Four steps

| Tool | What it does |
|---|---|
| `proxy_advice_start` | Opens one meeting. Turns the agenda into votable items (including either-or and conditional items) and gathers candidate facts, dividends and cash, pay, the audit opinion and court developments |
| `proxy_advice_read` | Reads details the server does not fully structure (candidate careers, articles, pay tables and so on) from the exact section of a filing |
| `proxy_advice_rules` | Returns only the guideline rules that apply to each item |
| `proxy_advice_finish` | Checks each recommendation against those rules and your style, sends back anything to fix, then writes the report |

Your AI assistant reads this order from the server instructions and calls the tools itself. A meeting usually takes 3–10 minutes. These four tools are available only on the hosted public server; the guideline and the checks run on a private server.

## How to read the verdicts

| Verdict | Meaning | Check next |
|---|---|---|
| FOR | A guideline rule allows FOR and no fact blocks it | Read the one-line reason and the cited rules. It is not a guarantee about the company or candidate. |
| AGAINST | A rule-based reason to oppose is confirmed, such as disqualification, a breach of mandatory law or a pay spike not tied to performance | Check the cited rule and the filing behind it |
| REVIEW | Something is left for a person to judge | See "what would change it" in the report and decide. It is not an instruction to abstain. |

The end of the report lists the points to check yourself, such as whether an item is actually tabled after a court ruling.

## Cutoff and sources

- **Cutoff time:** only filings published before it are used. If you do not give one, it is 17:00 KST on the day before the meeting.
- **Sources:** the meeting notice and the board's decision to call it, the latest annual report with its articles, the audit opinion, proxy solicitations and opinion letters, court and injunction filings.
- **Guideline:** rules for 10 item types (financial statements, dividends, articles, directors, auditors, pay, treasury shares, mergers and splits, capital, shareholder proposals). Every recommendation must rest on an allowed rule to pass the server check.
- **Style:** choose a shareholder-engagement stance (conservative, neutral, active) and a decision firmness (decisive, balanced, cautious).

## Ask like this

> "Draft vote recommendations for each item at Gabia's 2026 annual meeting. Conservative, decisive."
>
> "For the items marked REVIEW, what would have to be confirmed to turn them into FOR or AGAINST?"
>
> "Redo this meeting's advice using only filings public before 2026-03-25 17:00."

## Related features

- [AGM agenda and results](meeting-agenda.md) — notices, candidates and actual voting results
- [Financial metrics](financials.md) — financial background for the advice
- [Shareholder returns](shareholder-return.md) — background for dividend and buyback items
