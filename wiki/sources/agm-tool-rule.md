---
type: source
title: AGM_TOOL_RULE.md 요약
source_path: raw/rules/AGM_TOOL_RULE.md
ingested: 2026-04-05
tags: [agm, tools, fallback, parser, mcp]
related: [3-tier-fallback, 집중투표, KIND-주총결과, agm-case-rule]
---

# AGM_TOOL_RULE.md 요약

## 핵심 내용

AGM(주주총회) 분석을 위한 40개 MCP tool의 구조, fallback 흐름, 파싱 한계를 정의한 규칙 문서.

## Tool 구조

- **오케스트레이터**: `agm(ticker)` - 종합 분석, `own(ticker)` - 지분 종합
- **Search & Meta**: agm_search, agm_document, agm_info, agm_items, agm_extract, agm_corrections, agm_manual (7개)
- **8 Parsers x 3 Tiers** = 24개 tool: agenda, financials, personnel, aoi_change, compensation, treasury_share, capital_reserve, retirement_pay
- **결과**: agm_result (KIND 크롤링)
- **Ownership**: own_major, own_total, own_treasury, own_treasury_tx, own_block, own_latest (6개)

## [[3-tier-fallback]] 흐름

1. `agm_*_xml` 호출 (빠름)
2. AI가 결과를 CASE_RULE 기준으로 검증
3. SUCCESS -> 답변 / SOFT_FAIL -> AI 자체 보정 시도 / 보정 불가 -> PDF fallback 제안
4. 유저 동의 -> `agm_*_pdf` (4s+)
5. 여전히 부족 -> `agm_*_ocr` (UPSTAGE_API_KEY 필요)

## 파싱 한계

- **자기주식**: 소집공고에 명시적 안건 없는 기업 많음
- **보수한도**: 이사/감사 별도 안건 가능, 금액 단위 다양
- **퇴직금**: 재무제표 주석의 "퇴직급여" 테이블과 혼동 위험
- **재무제표**: 보고사항일 수 있음 (투표 없음)
- **정관변경**: 하위 안건 분할 빈번
- **이사 선임**: 경력 병합 시 PDF fallback 필요, [[감사위원-의결권-제한]]

## [[집중투표]] 관련

- 일반 투표: 안건당 1주 1표, 찬성/반대/기권(%)
- 집중투표: N명 선출 시 1주 N표, 득표율+순위, DART 찬성률 "-"

## [[KIND-주총결과]]

- KRX KIND 크롤링, DART rcept_no -> KIND acptno 변환 ("80" -> "00")
- 참석률 역산: 발행기준 찬성률 / 행사기준 찬성률
- 감사위원 선임은 3% 의결권 제한으로 분모 다름

## 시간순서 규칙

소집결의(이사회) -> 소집공고 -> 주총 당일 -> 주총결과. 결과 -> 공고 참조 금지 (시간 역전).
