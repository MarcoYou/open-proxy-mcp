# OpenProxy MCP

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Branch](https://img.shields.io/badge/branch-release_v2.0.0-blue.svg)](https://github.com/MarcoYou/open-proxy-mcp/tree/release_v2.0.0)

[Korean README](README.md)

> This README is for the `release_v2.0.0` branch.  
> It describes the **next public surface (v2)**. For the current stable/production-facing docs, see [docs/v1/README_ENG.md](docs/v1/README_ENG.md).

## What v2 is trying to fix

OpenProxy previously exposed many low-level tools directly. Coverage was strong, but the user-facing surface was too close to the internal implementation:

- it was not always obvious where to start
- AGM, ownership, dividends, and contest signals were exposed as low-level tool units
- conclusions and evidence were not clearly separated
- source policy across DART, KIND, and Naver was not visible enough

v2 changes that into:

```text
company identification
-> data tabs
-> evidence review
-> action outputs
```

## Documentation Tracks

- `v1 (current stable / production-facing)`: [docs/v1/README_ENG.md](docs/v1/README_ENG.md)
- `v2 (release_v2.0.0 design / next public surface)`: [docs/v2/README_ENG.md](docs/v2/README_ENG.md)

## At a Glance

```text
OpenProxy MCP v2
├─ company
│  ├─ company identification
│  ├─ ticker / corp_code / ISIN
│  └─ recent filings index
│
├─ Data Tools
│  ├─ shareholder_meeting
│  ├─ ownership_structure
│  ├─ dividend
│  ├─ proxy_contest
│  ├─ value_up
│  └─ evidence
│
└─ Action Tools
   ├─ prepare_vote_brief
   ├─ prepare_engagement_case
   └─ build_campaign_brief
```

In one line:

```text
Start with the company name,
inspect the data tabs,
verify the evidence,
then generate action-ready outputs.
```

## Public Data Tools

### 1. `company`

The shared entry point.

- resolves company name / English name / alias / ticker
- normalizes `ticker / corp_code / ISIN`
- acts as a filing index hub for downstream tools

### 2. `shareholder_meeting`

The AGM/EGM tab.

- annual / extraordinary meetings
- agendas
- board candidates
- compensation limits
- articles amendments
- vote results
- corrected filings

Recommended scopes:

```text
summary
agenda
board
compensation
aoi_change
results
corrections
evidence
```

### 3. `ownership_structure`

The ownership tab.

- largest shareholders
- 5% block holders
- treasury shares
- control map
- timeline

Recommended scopes:

```text
summary
major_holders
blocks
treasury
control_map
timeline
```

### 4. `dividend`

The dividend tab.

- dividend decisions
- DPS
- payout ratio
- dividend yield
- dividend history
- special / quarterly dividend signals

Recommended scopes:

```text
summary
detail
history
policy_signals
evidence
```

### 5. `proxy_contest`

The contest/dispute tab.

- proxy solicitation
- litigation / rulings / filings
- 5% ownership-purpose changes
- contest signals
- timeline
- vote math

Recommended scopes:

```text
summary
fight
litigation
signals
timeline
vote_math
evidence
```

### 6. `value_up`

The value-up tab.

- corporate value-up plans
- re-filings
- implementation updates
- commitments
- timeline

Recommended scopes:

```text
summary
plan
commitments
timeline
evidence
```

### 7. `evidence`

The source verification tab.

- `rcept_no`
- `source_type`
- `section`
- `snippet`
- `confidence`

This is the answer to: “Where exactly did this statement come from?”

## Action Tools

Action tools are output-oriented tools.  
The design direction for v2 is to stabilize the data layer first, then layer these on top:

```text
prepare_vote_brief
prepare_engagement_case
build_campaign_brief
```

Summary:

- `data tools` = facts, filings, structured retrieval
- `action tools` = vote memos, engagement memos, campaign briefs

## Source Policy

The default v2 source policy is:

1. `DART API`
2. `DART document.xml`
3. `KIND HTML` (`whitelist only`)
4. `Naver` reference only
5. `requires_review`

Core principles:

- `DART` is the base
- `KIND` is not used for every disclosure type
- `Naver` never overrides official values
- `PDF download` is removed from the default path
- if the result is weak or conflicted, return `requires_review`

Related docs:

- [DART-KIND Mapping Whitelist](wiki/decisions/DART-KIND-매핑-화이트리스트-2026-04.md)
- [New Tool Validation Policy](wiki/decisions/tool-추가-검증-정책.md)

## How `shareholder_meeting` works

Example:

```text
shareholder_meeting(company="Samsung Electronics", meeting_type="annual", scope="summary")
```

Internally this is roughly:

```text
1. company resolution
2. annual notice search
   └─ pblntf_ty=E / AGM notice / corrected notices included
3. correction resolver
4. DART XML fetch
5. meeting_info parser
6. agenda parser (top-level only)
7. evidence refs
```

Then scopes open additional components only when needed:

- `board`
  - add the personnel parser
- `compensation`
  - add the compensation parser
- `aoi_change`
  - add the articles parser
- `results`
  - result filing search
  - whitelist check
  - KIND fetch
  - result parser

So `summary` is not meant to run every sub-parser by default.  
It should first show the structure of the meeting, then open the deeper tabs only when needed.

## Release Priority

```text
Phase 1
  company
  shareholder_meeting
  ownership_structure
  dividend
  value_up

Phase 1.5
  proxy_contest
  evidence

Phase 2
  prepare_vote_brief
  prepare_engagement_case
  build_campaign_brief
```

The reasoning is simple:

- first: fast, accurate data access
- second: evidence visibility
- third: action-ready output generation

## Implementation / Validation Docs

- [v2 Docs Index](docs/v2/README_ENG.md)
- [release_v2 Tool Architecture](wiki/analysis/release_v2-tool-아키텍처.md)
- [release_v2 Public Tool Validation Matrix](wiki/analysis/release_v2-public-tool-검증-매트릭스.md)
- [New Tool Validation Policy](wiki/decisions/tool-추가-검증-정책.md)
- [New Tool Validation Template](wiki/templates/tool-추가-검증-템플릿.md)

## If you need the current stable product

For the currently deployed/publicly stable structure, follow the v1 docs:

- [v1 docs](docs/v1/README_ENG.md)
- [Local installation guide](docs/connect_eng.md)
- [Current architecture (v1)](docs/ARCHITECTURE.md)

## Disclaimer

OpenProxy structures disclosure data for AI use. AI can hallucinate and may produce inaccurate analysis.  
This branch is specifically for `release_v2.0.0` design and transition work, so final investment or voting decisions should always be verified against the original filings and expert review.

## License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)  
Non-commercial use only.
