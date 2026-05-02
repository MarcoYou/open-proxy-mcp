---
type: audit
title: ownership_structure baseline 200×3 — 패턴 fix 불필요
date: 2026-05-03
related_tools: [ownership_structure]
related_audits: [260503_2330_audit_proxy_contest_baseline]
result: baseline 100% 일치 + max 1.8s — fix 불필요
---

# ownership_structure baseline audit

`ownership_structure`는 3 endpoint gather (`get_major_shareholders` + `get_stock_total` + `get_treasury_stock`). proxy_contest와 같은 DART API 직접 호출 구조. baseline 측정으로 fix 필요 여부 판단.

## 200×3 baseline batch (fix 없이)

- 597 호출 / **3.9분**
- complete 195/197 회사
- **일치율 195/195 = 100.0%** ✅
- Status: exact 591 / error 6 (alias 잔존)
- **timeout 0** ✅
- Elapsed: mean **1.17s** / p50 1.20s / p95 1.40s / max **1.80s**

## 결정 — 패턴 fix 불필요

근거: max 1.8s, p95 1.4s → race window 사실상 없음. 적용 판단 기준에서 "DART endpoint 직접 호출" 분류 → 불필요.

## 200×3 비교 (4 tool)

| Tool | upstream 종류 | Mean | p95 | Max | 일치율 | fix |
|---|---|---|---|---|---|---|
| advise_vote | 6 build_* 재귀 | 4.7s | 21.9s | 46.7s | 100% | ✅ 필요 |
| recap_vote | 8 build_* 재귀 | 5.6s | 24.5s | 49.8s | 100% | ✅ 필요 |
| proxy_contest | 8 endpoint 직접 | 1.09s | 1.90s | 7.70s | 100% | ⚪ 불필요 |
| **ownership_structure** | **3 endpoint 직접** | **1.17s** | **1.40s** | **1.80s** | **100%** | ⚪ 불필요 |

**판단 기준 재확인**: build_*_payload 재귀 호출 (다른 service 재귀 트리) vs DART endpoint 직접 호출 — 후자는 fix 효과 미미.

## 다음

- 정정공고 4건 fix (`items[0]` → 시간 desc fallback) — race와 별개 problem (parsing fail)
- `corp_gov_report` (2 + N doc gather) baseline 검증은 우선순위 낮음 (alias error 6건 외 일치율 정상 검증된 적 있음)
