---
type: readme
title: wiki/decisions/ — OPM 정책 + 결정 + 토론
updated: 2026-05-08
---

# wiki/decisions/ — OPM 정책 + 결정 + 토론

> OPM 의사결정 추적. 정책 master + 시점별 결정 + 토론 transcript.

## 핵심 master 파일

| 파일 | 용도 |
|---|---|
| **`open-proxy-guideline.md`** | OPM 자체 의결권 정책 v1.2 (12 카테고리 + OPM 5 기준 + 8 운용사 + NPS 통합). **유일 master** |
| `260429_0059_decision_voting-policy-consensus-matrix.md` | 8 운용사 합의 매트릭스 (79 토픽). 매트릭스 형태 보존 (master 보조) |
| `260429_0059_debate_opm-guideline-7전문가.md` | open-proxy-guideline 작성 토론 transcript (역사적 발전) |

## 사용 흐름

### 분석가 / LLM (사람)
→ `open-proxy-guideline.md` 읽기 (12 카테고리별 룰 + OPM 5 기준)

### 코드 (proxy_advise)
→ `open_proxy_mcp/data/asset_managers/policies/open_proxy_v1.json` 로드 (open-proxy-guideline의 머신리더블 버전)

### 정책 변경 시
→ `open_proxy_v1.json` 수정 (코드)
→ `open-proxy-guideline.md` 동기화 (인간 가독)

## 시점별 결정 (yymmdd_hhmm_decision_)

작은 기술적 결정 — 보존 (각자 명확한 scope):

| 파일 | 내용 |
|---|---|
| 260429_0059_voting-policy-consensus-matrix | 8 운용사 합의 매트릭스 |
| 260429_0216_improvement_turnkey-11agent | 11 agent 통합 |
| 260505_1700_inside-director-performance-matrix | 사내이사 성과 매트릭스 2x3 |
| 260505_1900_compensation-retirement-split | 보수/퇴직금 분리 |
| 260506_0030_notice-scope-cleanup-prov-financials | shareholder_meeting_notice scope 정리 |
| 260506_2330_v1-dead-parsers-archive | v1 dead parser archive 결정 |
| 260507_2330_httpx-connection-pool | httpx connection pool |
| 260508_0030_classify-agenda-parent-shortcircuit | _classify_agenda parent 인지 |
| **260508_0200_law-layer** | **법령 layer 도입 (Ralph 3 결과)** |

## 정체성 문서 (시점 prefix 없음)

| 파일 | 용도 |
|---|---|
| `open-proxy-guideline.md` | OPM 자체 정책 master |
| `tool-changelog.md` | tool 변경 이력 |
| `XML-vs-PDF.md` / `BeautifulSoup-파서-선택.md` 등 | 파서/데이터 소스 결정 |

## 관련 페이지

- [[open-proxy-guideline]] (master)
- `open_proxy_mcp/data/asset_managers/policies/open_proxy_v1.json` (코드 master)
- [[260508_0200_decision_law-layer]] (법령 layer 도입)
- [[law-layer-260508]] (lesson)
- [[rules/laws/README]] (법령 자료 입구)

## 신규 결정 추가 시

1. **시점별 결정**: `yymmdd_hhmm_decision_{title}.md`
2. **정책 변경**: `open-proxy-guideline.md` + `open_proxy_v1.json` 동시 update
3. **토론 transcript**: `yymmdd_hhmm_debate_{title}.md`
