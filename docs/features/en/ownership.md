# Ownership Map

Builds the company's ownership structure (who holds how much and who is tied to whom) from filing items.

## What it reads and what it computes

| Filing item referenced | What is computed / judged |
|---|---|
| Shares and ownership % in the business report's **largest shareholder and related parties** | Largest shareholder's own stake + combined related-party stake (effective control) |
| Holder and ownership % in the **5% bulk-holding report** | Identify external 5%+ holders (institutions, foreigners, activists) |
| **Officer / major-shareholder ownership report** | Holdings of officers and 10% shareholders |
| Treasury share holdings | Non-voting treasury share ratio (affects vote contests) |
| Filer name vs largest-shareholder registry (registry_overlap) | Mark each party as incumbent-owner camp vs external |

The point is the **real size of control** — whether the largest shareholder controls comfortably, or whether the vote is tight against an external force.

## Five scopes

| scope | What it shows |
|---|---|
| **summary** | Ownership overview |
| **major_holders** | Largest shareholder + related parties (self / family / affiliates) |
| **blocks** | 5% bulk holdings — latest + history |
| **control_map** | Control-structure relationship map |
| **changes** | Ownership-change records |

> Treasury shares are separated into [treasury_share](../../../wiki/tools/treasury_share.md) (deprecated in this tool).

## control_map — registry_overlap

In the control-structure map, each party is marked by whether it is **in the largest-shareholder registry (registry_overlap)**.

- `registry_overlap = true` — the filer's name exists in the business report's largest-shareholder registry (presumed incumbent-owner camp)
- `registry_overlap = false` — not in the registry (possible external force)

> registry_overlap means **whether the same name is in the registry**; it does not mean interests are fully aligned (homonyms / shifting interests are possible).

## How to use

> "Show me SK Hynix's ownership structure and largest shareholder"

Returns an ownership table organized by largest shareholder, related parties, and 5% blocks.

## See also

- [Control-Contest Signals](control-contest.md) — whether ownership **changes** are contest signals
- [Shareholder Return](shareholder-return.md) — treasury shares (non-voting stake) in detail

## Technical reference (for developers)

> The pages below are tool I/O specs. General users do not need them.

- [ownership_structure](../../../wiki/tools/ownership_structure.md) — ownership structure
- [treasury_share](../../../wiki/tools/treasury_share.md) — treasury shares
