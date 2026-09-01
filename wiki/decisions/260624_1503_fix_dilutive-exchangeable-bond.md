---
type: analysis
title: dilutive_issuance 교환사채(EB) 추가 + 정정/철회/누락 복원 fix 2026-06-24
tags: [fix, parsing, dilutive_issuance, exchangeable-bond, EB, document-recovery, regression-test]
related: [dilutive_issuance, 교환사채권발행결정]
date: 2026-06-24
related_tools: [dilutive_issuance]
---

# dilutive_issuance 교환사채(EB) 추가 + 정정/철회/누락 복원 fix 2026-06-24

`dilutive_issuance`가 희석성 증권 4종(유증/CB/BW/감자)만 보고 **교환사채(EB)를 통째로 누락**하던 문제를 5종으로 확장하고, DART 구조화 API가 정정·철회 EB를 불완전하게 주는 3가지 패턴을 원문 복원으로 보강했다.

## 문제 요약

DART DS005 의 희석성 증권 4 API(`piicDecsn`/`cvbdIsDecsn`/`bdwtIsDecsn`/`crDecsn`)에는 교환사채
(`exbdIsDecsn`)가 없다. 그 4개만 부르면 자기주식 기초 EB 를 대규모로 발행한 회사도 `no_filing` 으로
보인다(예: 태광산업 2025년 자기주식 전량 24.41% 기초 EB 3,185.8억원 — 이후 철회).

또 EB 는 DART 구조화 응답이 다음 3가지로 불완전하다:

| 패턴 | 증상 | 대표 |
|---|---|---|
| **A. blank stub** | 정정/철회 후 최신본만 + 교환조건 전부 `-` | 태광산업 (구조화 1건 공란, list.json 체인 9건) |
| **B. 0건 누락** | 첨부정정만 있는 체인은 구조화 013(0건) | 한라IMS (구조화 0건, list.json `[첨부정정]` 1건) |
| **C. 문서 미제공** | 그 공시의 `document.xml`마저 014(파일 없음) | 한라IMS (첨부정정 014) |

## EB의 성격 (왜 dilutive에 넣는가)

- CB/BW = 신주 발행 → **지분 희석**
- EB(자기주식 기초) = 신주 발행 없음. 그러나 **교환권 행사 시 의결권 없던 자기주식이 제3자(인수자)로 이전되며 의결권 부활** → **의결권 희석**. 사실상 제3자배정 유상증자와 같은 지배구조 효과. 경영권 분쟁·행동주의 대응에서 우호지분 형성 수단.

## Fix

### 1. 구조화 5번째 타입 (`exbdIsDecsn`)

- `client.get_exchangeable_bond_decision()` 추가 (DS005).
- `_normalize_exchangeable_bond()` — 검증한 실제 필드: `ex_rt`(교환비율)·`ex_prc`(교환가)·`ex_prc_dmth`·`extg`(교환대상)·`extg_stkcnt`·`extg_tisstk_vs`(발행총수 대비 %)·`exrqpd_bgd/edd`. 공통 필드는 CB와 동일.
- `_SUPPORTED_SCOPES`에 `exchangeable_bond`, `_fetch_scope`에 병렬 태스크, `event_count`/`data["exchangeable_bond_events"]`/`_summary_headline` 분기.
- 정상 EB(미철회)는 이 경로로 전체 필드 확보.

### 2. EB 보정 (`_ensure_eb_coverage`) — 패턴 A·B·C 대응

구조화 EB 응답이 **blank 이거나 0건이면** `list.json`으로 EB 공시 존재부터 확인한 뒤 원문을 파싱한다.
blank stub 에는 원문 조건을 병합(`recovered_from_document`), 구조화 0건이면 새 행을 만들고, 원문마저
014(파일 없음)면 조건 없이 **탐지 전용 행**(`detection_only`)을 낸다 — **EB 가 있는데 `no_filing`으로
보이는 것을 막는 게 이 경로의 존재 이유**다. 구조화가 EB 를 완전히 주면 이 경로는 아예 돌지 않아
추가 DART 콜이 0이다.

원문 복원 경로에는 두 가지 함정이 있어 코드가 그만큼 복잡하다. ① 일부 레이아웃은 `교환대상` 셀이 비어
**교환가액 조정 산식의 변수줄**(`A: 기발행주식수`)을 교환대상으로 오인시킨다 → 산식 변수줄 배제 +
라벨 앵커 + 서술형 폴백. ② 구조화 완전행과 같은 EB 의 정정 stub 복원이 **2행**이 되므로 (회차,총액)
기준 dedup(우선순위 구조화 > 복원 > 탐지).

> 파서 재현 상세(라벨줄 목록·헬퍼 시그니처)와 라이브 전수 187사 검증 census 는 storage
> (`wiki-private/architecture/이관_260806_arch-decisions.md`).

## 코드 변경 파일

- `open_proxy_mcp/dart/client.py` — `get_exchangeable_bond_decision`
- `open_proxy_mcp/services/dilutive_issuance.py` — normalizer + scope + `_ensure_eb_coverage`/`_find_eb_terms_from_filings`/`_parse_eb_document`/`_merge_eb_doc_into_row`/`_looks_like_eb_target`/`_dedup_eb_rows`
- `open_proxy_mcp/tools_v2/dilutive_issuance.py` — EB 카드 + count(5종) + desc
- `wiki/rules/disclosures/교환사채권발행결정.md` (신규) + `wiki/rules/disclosures/README.md`
- `wiki/tools/dilutive_issuance.md` (4종→5종)

## 남은 작업 (후속)

- **인수자·발행총수대비%**: 원본 시점 미기재(정정에서 추가)면 `-`. "원본+정정 병합"으로 보강 가능.
- **패턴 C 문서 복원**: 첨부정정 014는 DART 뷰어 HTML 스크래핑(웹) fallback 검토 가능. 현재는 탐지 전용으로 surface.

## 관련

[[dilutive_issuance]] [[교환사채권발행결정]]
