---
type: schema
title: OPM Wiki Schema
updated: 2026-05-01
---

# OPM Wiki Schema

OpenProxy MCP(OPM) 도메인 지식 위키. Karpathy LLM-wiki 아키텍처 기반.
LLM이 작성/유지하고, 사용자는 소싱과 질문에 집중.
OPM repo 안에 `open-proxy-mcp/` 디렉토리로 존재 (구 `wiki/`).

처음 방문하면 [[index]] -> [[tools/README]] 순서로 본다.

## 1. 카테고리 정의 (5+1)

```
open-proxy-mcp/
  raw/            # 외부 source (PDF/xlsx/md). 절대 수정 금지
  tools/          # 17 tool 진입점 (사용자 입장)
  architecture/   # OPM 시스템 설계 + audits/ + fixes/
  decisions/      # OPM 정책 + 판단 + debate
  rules/          # 한국 자본시장 사실 (concepts/ + disclosures/ + laws/)
  archive/        # 흡수된 페이지 (역사 보존)
  index.md        # 전체 인덱스 (시작점)
  WIKI_SCHEMA.md  # 이 문서
  log.md          # 작업 로그
```

| 카테고리 | 무엇 | 수정 정책 |
|---|---|---|
| `raw/` | 외부 원본 (운용사 정책 PDF, 행사내역 xlsx, 외부 reference markdown) | NO 수정 금지 (read-only) |
| `tools/` | 17 tool 카탈로그 + 통일 schema | tool 코드 변경 시 함께 update |
| `architecture/` | 시스템 설계, 데이터 수집, 3-tier fallback, matrix system | 시스템 변경 시 |
| `decisions/` | OPM 정책 (open-proxy-guideline), debate transcript, 파서 채택 | 새 결정 시 추가 |
| `rules/concepts/` | 한국 자본시장 도메인 개념 (배당성향, 최대주주 등) | 사실 update 시 |
| `rules/disclosures/` | DART/KIND 공시 유형 (현금배당결정, 유상증자결정 등) | 신규 공시 유형 발견 시 |
| `rules/laws/` | 상법 / 자본시장법 등 법령 변화 | 법령 개정 시 |
| `archive/` | 흡수된 페이지, 구 RULE 요약, 외부 entity 페이지 | 단순 보존, 신규 X |

## 2. 명명 규칙 (2026-05-01~)

```
시점 있는 문서:  yymmdd_hhmm_{type}_{title}.md
정체성 문서:     {name}.md
```

### Prefix 사용 (시점 있음)

| Type | 의미 | 예시 |
|---|---|---|
| `audit` | 데이터/시스템 진단 | `260429_2030_audit_parsing-200기업.md` |
| `fix` | 버그 fix + regression 검증 | `260427_1145_fix_ownership-stockknd.md` |
| `decision` | 정책 결정 transcript | `260429_0059_decision_voting-policy-consensus-matrix.md` |
| `debate` | 다인 토론 / 페르소나 토론 | `260429_0059_debate_opm-guideline-7전문가.md` |
| `improvement` | 시스템 개선 (audit + fix 결합) | `260429_0216_improvement_turnkey-11agent.md` |
| `changelog` | 버전 변경 이력 (특정 시점 release) | `tool-changelog.md` (정체성으로 보존) |
| `release` | 릴리스 이벤트 | (예정) |
| `log` | 일반 작업 log | `log.md` (단일 파일로 보존) |

### Prefix 없음 (정체성 = 이름)

| Type | 위치 | 예시 |
|---|---|---|
| `tool` | `tools/{name}.md` | `tools/shareholder_meeting.md` |
| `concept` | `rules/concepts/{name}.md` | `rules/concepts/배당성향.md` |
| `disclosure` | `rules/disclosures/{name}.md` | `rules/disclosures/현금배당결정.md` |
| `law` | `rules/laws/{name}.md` | `rules/laws/상법개정-타임라인-2026.md` |

이유: tool 이름, 공시명, 개념명, 법령명은 정체성 자체가 이름. 시점 prefix 붙이면 검색·link·MCP 호출 시 마찰 발생.

### 한국어 OK
파일명에 한국어 사용 OK (예: `최대주주.md`, `현금배당결정.md`). hyphen으로 단어 구분.

## 3. Frontmatter Schema (페이지 type별)

### tool
```yaml
---
type: tool
title: shareholder_meeting
domain: data        # discovery | data | policy_matrix | action
scope: [agendas, candidates, compensation, articles, results, ...]
data_source: [DART API, KIND]
related_disclosures: [주주총회소집공고, 주주총회결과]
related_concepts: [의결권, 집중투표, 보수한도]
related_decisions: [pblntf-ty-필터링]
related_audits: [260429_0912_audit_parsing-200기업-v2-no_filing]
created: 2026-05-01
---
```

### concept
```yaml
---
type: concept
title: 배당성향
tags: [dividend, financial-metric]
related: [배당수익률, 당기순이익, 주주환원]
---
```

### disclosure
```yaml
---
type: disclosure
title: 현금배당결정
source: [DART(I), KRX]
mandatory: true
related_tool: dividend
related_concepts: [배당성향, 시가배당률]
---
```

### law
```yaml
---
type: law
title: 상법개정-타임라인-2026
effective: 2026-03-06
related_disclosures: [자기주식의무소각-2026신법]
---
```

### architecture / audit / fix
```yaml
---
type: audit          # 또는 fix, architecture, improvement
title: parsing-200기업-v2-no_filing
date: 2026-04-29
scope: 196 기업 × 11 tool
result: exact 66.9%, partial 1.5% (4-class)
related_tools: [shareholder_meeting, ownership_structure, ...]
---
```

### decision / debate
```yaml
---
type: decision       # 또는 debate
title: voting-policy-consensus-matrix
date: 2026-04-29
participants: [7 전문가 페르소나]
outcome: v1.0 -> v1.1 -> v1.2
---
```

### index / readme / schema / log
정형 frontmatter:
```yaml
---
type: index | readme | schema | log
title: ...
updated: 2026-05-01
---
```

## 4. 신규 페이지 추가 워크플로우

### Step 1: 어떤 카테고리?

| 추가하려는 것 | 카테고리 | 예시 |
|---|---|---|
| 새 tool | `tools/{name}.md` | tools/proxy_guideline.md |
| 새 한국 자본시장 개념 | `rules/concepts/{name}.md` | rules/concepts/사외이사.md |
| 새 공시 유형 발견 | `rules/disclosures/{name}.md` | rules/disclosures/임시주총소집공고.md |
| 법령 개정 | `rules/laws/{name}.md` | rules/laws/공정거래법-개정-2027.md |
| 시스템 설계 | `architecture/{name}.md` | architecture/cache-strategy.md |
| 데이터/시스템 진단 | `architecture/audits/yymmdd_hhmm_audit_{title}.md` | 260501_1530_audit_corp_gov.md |
| 버그 fix | `architecture/fixes/yymmdd_hhmm_fix_{title}.md` | 260501_1530_fix_dividend-rate.md |
| OPM 정책 결정 | `decisions/yymmdd_hhmm_decision_{title}.md` | 260501_1530_decision_naver-fallback.md |
| 다인 토론 | `decisions/yymmdd_hhmm_debate_{title}.md` | 260501_1530_debate_action-tool.md |
| 외부 source 추가 | `raw/{policies|records|references}/원본.{pdf|xlsx|md}` | raw/policies/2026.04 X운용사.pdf |

### Step 2: 명명

- 시점 있음: `yymmdd_hhmm` (KST 기준)
- 정체성 문서: 이름 그대로
- hyphen으로 단어 구분, 한국어 OK

### Step 3: frontmatter + 본문

위 schema 따라 frontmatter 작성. type별 본문 구조:
- tool: 12 섹션 통일 (tools/README.md 참조)
- audit/fix: scope / 결과 / regression / 다음 액션
- decision/debate: 배경 / 옵션 / 토론 / 결정 / 영향
- concept/disclosure/law: 정의 / 핵심 필드 / OPM tool 매핑 / 관련 문서

### Step 4: link 작성

- 같은 vault 안: Obsidian wikilink `[[페이지명]]`
- 폴더 구조 명시 필요할 때: `[[architecture/audits/...]]`
- 외부 link: 정상 markdown `[text](https://...)`
- 같은 폴더 안 ref (markdown 호환): `[text](상대경로.md)` 도 사용 가능

### Step 5: index.md 추가

신규 페이지 1줄 요약과 함께 index.md 해당 섹션에 추가.

### Step 6: log.md entry

```markdown
## [YYYY-MM-DD] {feat|fix|docs|audit} | 한 줄 요약
- 핵심 변경 1
- 핵심 변경 2
```

## 5. Link 패턴

### Obsidian wikilink (1차)
```
[[page-name]]            # 같은 vault, 페이지명만 적기
[[page-name|보이는 텍스트]] # alias
```

Obsidian이 자동 resolve. 폴더 깊이 무관.

### Markdown link (호환성)
```
[보이는 텍스트](상대경로/page.md)
[보이는 텍스트](architecture/audits/260429_0912_audit_parsing-200기업-v2-no_filing.md)
```

Obsidian + 일반 markdown viewer 둘 다 호환.

### 명시적 폴더 path (충돌 회피)
같은 이름 페이지가 여러 폴더에 있을 때:
```
[[architecture/data-collection]]    # OPM 시스템
[[archive/sources/dart-kind-...]]   # 흡수된 구 페이지
```

## 6. raw/ 수정 금지 강조

**중요**: `raw/` 안 파일은 LLM도 사람도 절대 수정하지 않는다.

이유:
- 외부 source의 원본 무결성 보존
- 분석 + 요약은 별도 페이지(`architecture/`, `decisions/`, `rules/`)에 작성
- 새 외부 source 추가는 OK, 단 기존 파일 수정 X

신규 source 추가 워크플로우:
1. `raw/{policies|records|references}/`에 파일 그대로 배치 (rename 가능)
2. ingest 작업으로 요약/분석 페이지 생성 (raw 외부에 작성)
3. index.md + log.md update

## 7. archive/ 정책

archive는 **흡수된 페이지의 역사 보존**.

원칙:
- 페이지가 다른 페이지로 흡수되면 archive로 이동 (삭제 X)
- archive 페이지는 단순 보존, 신규 추가 X
- 구 entity 페이지(DART-OpenAPI 등)도 archive 보존 (CLAUDE.md path 호환)

archive 안 추가가 필요한 경우:
- tool 통합 (예: matrix-auto-scoring + decision-matrix-design -> matrix-system)
- 구조 재편으로 다른 페이지에 흡수됨

## 8. 자기 학습 + lint

### 자동 학습 (/ship 연동)
- 코드 변경 시 `/ship`이 관련 wiki 페이지 자동 update
- 새 tool -> tools/{name}.md + index.md update
- 파서 개선 -> architecture/audits/ 신규 entry
- 새 공시 연동 -> rules/disclosures/{name}.md 신규
- 변경 없으면 wiki 안 건드림

### 토큰 절약
- CLAUDE.md는 최소한 (~70줄)
- "상세는 wiki 참조"로 위임
- AI는 `index.md` 먼저 읽고, 필요한 페이지만 선택적 로드
- 전체 wiki 한 번에 로드 X

### lint (주기적 점검)
- 모순 / 고아 페이지 / 누락 개념 / 교차 참조 누락
- 새 세션에서 답변 후 wiki에 인사이트 반영

## 9. 보안 + 민감 정보

- `.env`, API 키 등 민감 정보 절대 wiki에 넣지 X
- 운용사 실명 -> 익명화 (M레거시 / S레거시 / T행동주의 등). 실명 매핑은 `manager_aliases.json` (gitignore)
- 개인정보 (이름, 주민번호 등) 마스킹

## 10. Quick Reference

| 하고 싶은 것 | 가야 할 곳 |
|---|---|
| OPM 처음 사용 | [[index]] -> [[tools/README]] |
| tool 17개 보기 | `tools/` |
| OPM 정책 알기 | [[open-proxy-guideline]] |
| 한국 공시 용어 | `rules/concepts/`, `rules/disclosures/` |
| 시스템 설계 | `architecture/` |
| 외부 원본 | `raw/` (read-only) |
| 작업 history | `log.md` |
| 흡수된 페이지 | `archive/` |
