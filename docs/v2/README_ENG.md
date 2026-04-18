# OpenProxy MCP v2 Docs

These are the design docs for `release_v2.0.0`.  
They describe the next public surface, validation policy, source policy, and tool architecture refactor.

## Core Direction

- `company` as the shared entry point
- public surface reorganized around `data tools`
- `proxy_contest` as the contest/dispute tab
- `evidence` as the source verification tab
- `action tools` deferred to phase 2
- default source policy: `DART API / DART XML / whitelist-based KIND / Naver reference`
- no default PDF download path

## Quick Links

- [release_v2 Tool Architecture](../../wiki/analysis/release_v2-tool-아키텍처.md)
- [release_v2 Public Tool Validation Matrix](../../wiki/analysis/release_v2-public-tool-검증-매트릭스.md)
- [New Tool Validation Policy](../../wiki/decisions/tool-추가-검증-정책.md)
- [New Tool Validation Template](../../wiki/templates/tool-추가-검증-템플릿.md)
- [DART-KIND Mapping Whitelist](../../wiki/decisions/DART-KIND-매핑-화이트리스트-2026-04.md)

## Summary

```text
v2 = next-release design docs
   = data-first / evidence-aware / source-policy driven
```

For current stable docs, see [v1 docs](../v1/README_ENG.md).
