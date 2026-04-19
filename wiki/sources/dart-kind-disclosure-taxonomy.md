---
type: source
title: DART-KIND 공시 분류 체계
source_path: open_proxy_mcp/dart-kind-disclosure-taxonomy.md
tags: [dart, kind, disclosure, taxonomy, source-policy]
related: [DART-OpenAPI, KRX-KIND, pblntf-ty-필터링, DART-KIND-매핑-화이트리스트-2026-04]
updated: 2026-04-19
---

# DART-KIND 공시 분류 체계

## 왜 중요한가
- `공시를 못 찾았다`와 `검색 범위를 잘못 잡았다`를 구분하려면, 먼저 `어느 공시군을 어디서 찾는지`가 명확해야 한다.
- 이 문서는 DART `pblntf_ty`와 KIND 세부 공시군을 연결해, `DART 우선 / KIND 병행 / KIND 우선` 정책의 기준점으로 쓴다.

## 핵심 구조
- `A~H`: 금융위 공시(FSS) 중심
- `I`: 거래소 공시(KRX/KIND)
- `J`: 공정위 공시(FTC)

실무적으로 OPM v2에서 특히 중요한 건 아래 4개다.

1. `E` 기타공시
- `주주총회소집공고`
- notice, agenda, board, compensation의 기본 소스

2. `D` 지분공시
- `주식등의대량보유상황보고서`
- `의결권대리행사권유`
- `공개매수`
- ownership / proxy_contest의 핵심 소스

3. `I` 거래소 공시
- `주주총회결과`
- `현금ㆍ현물배당결정`
- `기업가치 제고 계획`
- value_up, result, 일부 dividend의 핵심 소스

4. `B` 주요사항보고
- `소송`, `가처분`, `경영권분쟁소송`
- proxy_contest의 litigation 축에서 병행 확인 필요

## 현재 v2 적용 원칙
- `shareholder_meeting.notice`: `E` + 제목 `주주총회소집공고`
- `shareholder_meeting.results`: `I` + 제목 `주주총회결과`
- `dividend`: `I` + 제목 `현금ㆍ현물배당결정`
- `proxy_contest.fight`: `D` + 위임장/공개매수 제목군
- `proxy_contest.litigation`: `I/B` + 소송 제목군
- `value_up`: `I` + 제목 `기업가치제고 / 밸류업`

## 검색 정책에 주는 시사점
- `페이지 몇 개만 보고 없음`은 금지
- 대신 `회사 + 기간 + pblntf_ty + 공시명(report_nm) 타깃 검색`으로 간다
- 기간은 무한 확장하지 않고, 기본 lookback을 두고 필요 시 넓힌다
- KIND도 같은 원칙으로 `세부 공시군`을 정한 뒤 검색해야 한다

## 실무적 해석
- DART는 `공식 API + XML + 보고서 원문`에 강하다
- KIND는 `거래소 공시 세부 분류와 결과공시 본문`에 강하다
- 따라서 `다 KIND`, `다 DART`가 아니라 `공시군별 source policy`가 필요하다

## 관련 문서
- [[pblntf-ty-필터링]]
- [[DART-KIND-매핑-화이트리스트-2026-04]]
- [[release_v2-public-tool-검증-매트릭스]]
