---
type: log
title: Operation Log
---

# Operation Log

## [2026-04-07] init | Wiki 초기화
- 디렉토리 구조 생성 (raw/ + wiki/)
- CLAUDE.md(schema) 작성
- raw/ 시딩: rules 6개 + devlog 1개 + benchmarks 1개 + READMEs 2개

## [2026-04-05] ingest | 첫 전체 ingest (10 raw sources)
- raw/rules/ 6개: AGM_TOOL_RULE, AGM_CASE_RULE, DIV_TOOL_RULE, DIV_CASE_RULE, OWN_TOOL_RULE, OWN_CASE_RULE
- raw/rules/ 2개: OPM_README, OPA_README
- raw/devlog/DEVLOG.md
- raw/benchmarks/benchmark_personnel_results.json
- 생성: sources 10개, concepts 24개, entities 9개, analysis 8개 (총 51 페이지)
- index.md 전체 업데이트
