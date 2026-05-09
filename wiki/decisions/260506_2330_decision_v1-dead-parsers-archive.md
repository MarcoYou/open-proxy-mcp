---
type: decision
title: v1 dead parsers archive 결정 (3개)
date: 2026-05-06 23:30
status: adopted
related:
  - wiki/ralph/260505_2330_ralph_parser-omnibus-perf.md
  - wiki/decisions/260506_0030_decision_notice-scope-cleanup-prov-financials.md
related_lessons: [parser-omnibus-260506]
---

# v1 dead parsers archive 결정

## 배경

ralph parser-omnibus iter 7 (2026-05-06): tools/parser.py 내 3개 파서가 v2 production에서 사용되지 않음.

production = `OPEN_PROXY_TOOLSET=v2` (fly.toml 명시). v2 는 `tools_v2/*` + `services/*` 만 사용. v1 (tools/shareholder.py) 미사용.

## 결정

3개 파서 모두 **logical archive**:
- v1 dead (tools/shareholder.py 만 import)
- v2 production path (tools_v2/* / services/*) 호출 X — grep evidence
- 코드 본체는 parser.py 에 보존 (v1 mode local dev 호환성 유지)

| Parser | 위치 | v1 caller | v2 caller | 결정 |
|---|---|---|---|---|
| `parse_treasury_share_xml` | tools/parser.py:3508 | tools/shareholder.py:686 | 없음 | archive (logical) |
| `parse_capital_reserve_xml` | tools/parser.py:3593 | tools/shareholder.py:718 | 없음 | archive (logical) |
| `parse_financials_xml` | tools/parser.py:2626 | tools/shareholder.py:463 | 없음 (services/provisional_financial_statement.py 가 본체 흡수 — 260506 0030 결정) | archive (logical) |

### 검증 (grep evidence, 2026-05-06)

```
parse_treasury_share_xml usages:
  open_proxy_mcp/tools/parser.py:3508 (def)
  open_proxy_mcp/tools/shareholder.py:31 (import)
  open_proxy_mcp/tools/shareholder.py:686 (call)
→ tools_v2/* / services/* 미사용 (v1 dead)

parse_capital_reserve_xml usages:
  open_proxy_mcp/tools/parser.py:3593 (def)
  open_proxy_mcp/tools/shareholder.py:32 (import)
  open_proxy_mcp/tools/shareholder.py:718 (call)
→ tools_v2/* / services/* 미사용 (v1 dead)

parse_financials_xml usages:
  open_proxy_mcp/tools/parser.py:2626 (def)
  open_proxy_mcp/tools/shareholder.py:26 (import)
  open_proxy_mcp/tools/shareholder.py:463 (call)
→ tools_v2/* / services/* 미사용 (v1 dead)
   services/provisional_financial_statement.py 본체 흡수 완료 (260506 0030 결정)
```

### 왜 physical archive 미실행?

- parser.py 3974 라인 — 다수 helper (`_extract_period_labels` / `_normalize_financial_rows` 등)가 active parser 와 dead parser 모두 사용
- physical archive (코드 삭제) 시 active parser regression 위험
- v1 mode local dev 호환성 보존 가치 (코붕이 결정)

→ 본체 보존 + decision 기록으로 충분. v1 mode 실제 retire 시점에 재검토.

## 영향 범위

- 본 결정 = 문서화 only. 코드 변경 X.
- 이후 v1 mode 완전 retire 시 (별도 결정) tools/parser.py + tools/shareholder.py 통째로 archive.

## 비목표

- v1 mode 즉시 retire (별도 결정 필요)
- helper 함수 재사용 분리 (active vs dead parser 의존 untangle — 가치 낮음)

## 영향받는 파일

없음 (decision-only).
