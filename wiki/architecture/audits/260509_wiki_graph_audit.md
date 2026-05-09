---
type: audit
title: Wiki 그래프 audit — orphan / 약한 연결 / 명명 패턴 표준화
date: 2026-05-09
status: draft
related:
  - wiki/index.md
  - wiki/WIKI_SCHEMA.md
  - wiki/log.md
---

# Wiki 그래프 audit — 연결 취약점 + 명명 패턴 분석

## 배경

Karpathy 아키텍처: wiki는 LLM 도메인 지식 layer. 그래프 연결이 빈약하면 LLM이 관련 컨텍스트 못 찾음. 252 페이지 (raw 제외) 그래프 분석 + 취약점 식별 + 표준화 권장.

## 통계 (2026-05-09 기준)

| 카테고리 | 페이지 | Orphan (incoming=0) | Weak (incoming=1) | Leaf (outgoing=0) |
|---|---|---|---|---|
| tools | 17 | 0 | 0 | 0 |
| rules (concepts/disclosures/laws) | 73 | 0 | 0 | 2 |
| architecture | 40 | 11 | 3 | 21 |
| decisions | 23 | 1 (README) | 3 | 4 |
| lessons | 16 | 1 (README) | 3 | 1 |
| ralph | 19 | 6 | 5 | 14 |
| archive | 61 | 7 | 21 | 16 |
| **합계** | **252** | **26** | **35** | **58** |

총 1261 outgoing edges. 가장 많이 link되는 hub: `archive/entities/DART-OpenAPI` (46) / `rules/concepts/자사주` (27) / `rules/concepts/집중투표` (21) / `architecture/3-tier-fallback` (20).

## 발견 1 — Orphans (incoming=0, 26건)

### 카테고리별 분포

#### architecture/audits/ (11) — 옛 audit 결과 + iter 데이터
- `260503_0130_audit_advise-200-virtual` 등 옛 audit 4개 — Phase 3 시점 audit, 후속 lesson에서 link 안 됨
- `architecture/audits/data/260504_*/iter*` 7개 — Ralph 27 iter 진행 데이터, 종료 후 link 끊김

→ **조치**: index 또는 lesson에서 link 추가 (옛 작업도 컨텍스트로 가치). 아니면 archive 이동.

#### archive/ (7)
- `archive/analysis/dilutive_issuance-design`, `screen_events-design`, `related_party_transaction-design` 등 옛 설계 분석
- `archive/tools_advise_vote_before_meeting`, `archive/tools_recap_vote_after_meeting` — rename 전 tool 명세

→ **조치**: archive는 본질상 "역사 보존" — orphan 허용 가능. 단 rename 명세는 현재 tools/에서 link 필수.

#### ralph/ (6) — 옛 ralph plan
- `260501_1547_ralph_financial-metrics-phase1`
- `260502_0930_ralph_advise-recap-vote`
- `260503_0030_ralph_advise-200기업-가상실험`
- `260503_0230_ralph_advise-phase3-99pct`
- `260504_2118_ralph_proxy-advise-framework-enrichment`
- `260505_0051_ralph_treasury-execution-results`

→ **조치**: 후속 lesson이 ralph plan을 link하는 패턴 누락. lesson related에 ralph 명시 필수 (이미 일부 됨).

#### decisions/README + lessons/README
README 페이지는 카테고리 진입점이지만 외부에서 link 안 됨. → **조치**: index에서 link 추가.

## 발견 2 — Weak (incoming=1, 35건)

신규 페이지가 한 곳에서만 link됨. 그래프 fragility 높음.

#### 신규 (2026-05-08) — link 보강 필요
- `decisions/260507_2330_decision_httpx-connection-pool` — incoming 1
- `decisions/260508_0030_decision_classify-agenda-parent-shortcircuit` — incoming 1
- `lessons/classify-high-impact-260508` — incoming 1
- `lessons/parser-precision-260508` — incoming 1 (Ralph 5)
- `ralph/260508_0030_ralph_classify-high-impact` — incoming 1
- `ralph/260508_0207_ralph_parser-precision` — incoming 1

→ **조치**: index.md "최근 audit/fix" 섹션에 모두 link (일부 이미 추가됨).

#### archive/analysis tool-검증-예시 (7개)
- company / dividend / evidence / ownership_structure / proxy_contest / shareholder_meeting / value_up
- archive 카테고리는 weak 의도적이지만 **검증 예시는 tools/에서 link하면 사용성 ↑**

→ **조치**: tools/{name}.md 페이지에서 archive/analysis/{name}-tool-검증-예시 link 추가.

## 발견 3 — Leaves (outgoing=0, 58건)

페이지에서 외부로 link 없음 = "고립된 종착점". 컨텍스트 부족.

#### ralph/ 14건 — ralph plan 본질상 self-contained
ralph는 plan + iteration log이라 외부 link 적음. 단 다음 패턴 권장:
- ralph plan frontmatter `ref:` 필드에 (1) 선행 lesson, (2) 관련 decision, (3) 영향 코드 명시
- 일부 ralph plan은 ref 충실 (260508_0500_ralph_law-layer-precision) / 일부 빈약

#### architecture/audits/ 21건 — audit 결과 본문
audit 결과는 보통 lesson에 흡수되지만 audit 자체에서 lesson/decision/ralph link하면 그래프 강건.

→ **조치**: audit frontmatter `related:` 필드에 trigger/follow-up 명시.

## 발견 4 — Unresolved links (57건, 29 페이지)

[[wikilink]] 또는 frontmatter related에 명시했지만 실제 페이지 없음:

- `index.md`: `rules/concepts/`, `rules/disclosures/` (디렉토리 link)
- `tools/financial_metrics`: `[[ROE]]`, `[[ROA]]`, `[[ROIC]]`, `[[FCF]]` — concept 페이지 미존재
- `tools/proxy_result_after_meeting`: `[[가결]]`, `[[부결]]`, `[[위임장]]`, `[[찬반율]]` — concept 미존재
- `decisions/cross-domain-체이닝`: `agm-tool-rule\`, `own-tool-rule\` (escape 오류)
- 다수: `--` (구분선이 link로 잘못 파싱)

→ **조치**: 
1. 누락 concept 페이지 생성 (ROE/ROA/ROIC/가결/부결 등) 또는 link 제거
2. index에서 디렉토리 link → README link 변경
3. escape 오류 정정

## 발견 5 — 명명 패턴 분석

| 카테고리 | yymmdd_hhmm 비율 | identity 비율 | 정합도 |
|---|---|---|---|
| ralph | 95% | 5% | ✓ 정합 (시점 작업) |
| architecture | 60% | 38% | △ 혼재 (audit/fix는 시점, 설계 페이지는 identity) |
| **decisions** | **48%** | **52%** | ⚠ **혼재 — 정책 필요** |
| lessons | 0% | 100% | ✓ identity로 통일 (시점 본문에) |
| archive | 0% | 100% | ✓ 역사 보존 |
| tools / rules | 0% | 100% | ✓ identity (tool/concept는 영구) |

### decisions 혼재 분석

**identity decisions** (영구 정책):
- `open-proxy-guideline` (OPM 5 기준)
- `cross-domain-체이닝`
- `tool-changelog`
- `DART-KIND-매핑-화이트리스트-2026-04`

**yymmdd_hhmm decisions** (시점 결정):
- `260508_0700_decision_law-layer-precision`
- `260508_0200_decision_law-layer`
- `260507_2330_decision_httpx-connection-pool`
- `260506_2330_decision_v1-dead-parsers-archive`

→ **WIKI_SCHEMA에 명확화 필요**: "영구 정책 = identity / 단발 결정 = yymmdd_hhmm"

## 권장 조치

### 1. Bidirectional link 강제 (즉시 적용 가능)

A → B link 시 B frontmatter `related:`에 A 추가 필수. 한쪽 link만으로 incoming=0/1 발생.

**자동 검증 hook** (별도 ralph 후보):
- `/ship` 시 `related:` 필드 양방향 검증 + warning

### 2. 신규 페이지 link 보강 — Ralph 4-5 entries

이미 index.md에 추가됨 (commit `806fa1b`). 다음 보강 후보:
- [ ] `lessons/parser-precision-260508` ← `architecture/audits/260508_parser_audit` (이미 있음 ✓)
- [ ] `architecture/audits/260508_parser_audit` ← `lessons/parser-precision-260508` related 양방향
- [ ] `ralph/260508_0207_ralph_parser-precision` related에 lesson 추가

### 3. ralph → lesson 패턴 표준화

ralph plan **iter 종료 시** lesson 작성 + ralph plan ref 양방향 link 필수.

### 4. tools/ ↔ archive/analysis 검증예시 link

7개 archive/analysis/{name}-tool-검증-예시 → tools/{name}.md "관련 페이지" 섹션에 link 추가.

### 5. concept 페이지 신규 생성 vs link 제거

`tools/financial_metrics`의 `[[ROE]]`/`[[ROA]]`/`[[FCF]]` 등 — 둘 중 선택:
- (a) `wiki/rules/concepts/ROE.md` 등 신규 생성 (concept hub 강화)
- (b) `[[ROE]]` link 제거 (단순 텍스트로)

→ 추천: (a) — financial_metrics는 핵심 tool이고 concept hub로 가치 있음.

### 6. WIKI_SCHEMA 업데이트 — decisions 명명 정책

```
decisions:
- 영구 정책 (OPM 5 기준 / 가이드라인 / changelog) → {name}.md (identity)
- 단발 결정 (특정 시점 채택) → yymmdd_hhmm_decision_{title}.md
```

### 7. Orphan 옛 audit/data → archive 이동

`architecture/audits/data/260504_*/iter*` 7개 — Ralph 27 iter 진행 데이터 (작업 종료됨). archive로 이동 또는 README index에 명시.

## 구조화 패턴 — 시점 페이지 표준 link 4축

시점 작업 (audit/ralph/decision/lesson) 페이지는 frontmatter `related:`에 다음 4축 link 필수:

```yaml
related:
  # 1. trigger (이 작업이 시작된 곳)
  - wiki/ralph/yymmdd_xxx.md          # 이 작업의 ralph plan
  
  # 2. precursor (선행 결정/lesson)
  - wiki/decisions/identity-or-yymmdd.md
  
  # 3. impacted (변경 대상 코드/문서)
  - open_proxy_mcp/services/xxx.py
  - wiki/tools/xxx.md
  
  # 4. follow-up (이 작업의 결과)
  - wiki/lessons/xxx.md  (lesson)
  - wiki/decisions/yymmdd_xxx.md  (decision)
```

이 4축이 다 채워지면 incoming/outgoing ≥4. orphan/leaf 자연 해결.

## 향후 ralph 후보 (우선순위)

| 우선순위 | 작업 | 영향 |
|---|---|---|
| 🟡 1 | Ralph 4-5 신규 페이지 양방향 link 보강 | weak edges 6개 → 2개 이하 |
| 🟡 2 | concept 페이지 7개 신규 (ROE/ROA/ROIC/FCF/가결/부결/위임장) | unresolved links 50% 감소 + financial_metrics hub 강화 |
| 🟢 3 | tools/ ↔ archive/analysis 검증예시 link 추가 | archive 활용성 ↑ |
| 🟢 4 | WIKI_SCHEMA decisions 명명 정책 추가 | 일관성 ↑ |
| 🟢 5 | Orphan 옛 audit/data 정리 (archive 이동 또는 index 명시) | architecture orphan 11 → 4 이하 |
| 🟢 6 | bidirectional link 자동 검증 hook (`/ship` 통합) | 회귀 방지 |

## archive

- 분석 스크립트: `/tmp/wiki_graph.py`, `/tmp/wiki_analyze.py`
- 그래프 raw: `/tmp/wiki_graph.json` (252 페이지 × 1261 edges)

## 핵심 통찰

1. **rules/concepts hub 강건** — 자사주 / 집중투표 / 배당성향 등 27 incoming. 도메인 지식 핵심 layer.
2. **시점 페이지 (ralph/audit) 외부 link 빈약** — frontmatter 4축 표준화로 자동 강건성 확보 가능.
3. **archive는 의도적 orphan 허용** — 역사 보존 영역. 단 rename 명세는 예외 (현재 tools에서 link 필수).
4. **decisions 명명 혼재** — WIKI_SCHEMA 명확화 필요.
5. **Wiki 전체 그래프 강건도 80% 정도** (252 중 26 orphan = 10.3% / 35 weak = 13.9%) — 신규 페이지 추가 시 4축 link 강제하면 90%+ 가능.
