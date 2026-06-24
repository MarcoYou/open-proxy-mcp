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

발단: "태광산업 최근 주식 희석 리스크" 질의에 `dilutive_issuance`가 **0건(no_filing)** 반환. 그러나 태광산업은 2025년 **자기주식 전량(24.41%) 기초 교환사채(EB) 3,185.8억원**을 발행(후 철회)한 상태였다.

근본 원인: 툴이 DART DS005 **4 API**(`piicDecsn`/`cvbdIsDecsn`/`bdwtIsDecsn`/`crDecsn`)만 호출 → 교환사채 `exbdIsDecsn`이 스코프에 없음.

추가로 EB는 DART 구조화 응답이 다음 3가지로 불완전하다는 것을 라이브로 확인:

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

구조화 EB가 **blank이거나 0건이면** `list.json`(B001, 키워드 "교환사채권발행결정")로 EB 공시 존재를 확인하고 원본 문서를 파싱:

- **(A)** blank stub 행에 원문 조건 **병합** (`recovered_from_document=true`).
- **(B)** 구조화 0건이지만 list.json에 EB 있으면 원문 파싱해 **새 행 생성**.
- **(C)** 문서마저 014면 조건은 못 뽑아도 **탐지 전용 행**(`detection_only=true`) 생성 → "EB 공시 발견, 원문 확인 필요" surface. **no_filing 오인 방지**.

원문 파서 `_parse_eb_document()` — 라벨줄→값줄 구조(probe로 검증): `권면(전자등록)총액 (원)`·`교환가액 (원/주)`·`교환비율 (%)`·`교환대상 종류/주식수`·`사채만기일`·`사채발행방법`·`이사회결의일(결정일)`·`회차`·`종류`(단독줄). 인수자는 `○○증권 주식회사` best-effort.

원본(가장 오래된) 공시부터 최대 4건 시도 — 후속 정정본은 재검토 중 필드가 다시 `-`로 비기 때문.

**비용**: 구조화가 EB를 완전 제공하면 list.json 생략(추가 호출 0). blank/0건일 때만 list.json 1 + 문서 최대 4. EB는 시장 전체의 소수라 대부분의 호출은 list.json 1회만 추가.

### 3. 원문 파서 보강 (전수 검증에서 발견)

187사 전수 검증 중 원문복원 경로에서 2개 추가 버그 발견·수정:

- **교환대상 산식 오염**: 일부 레이아웃(광동제약·동성제약)은 구조화 `교환대상` 셀이 비어 교환가액 *조정 산식*의 변수줄(`A: 기발행주식수`)을 교환대상으로 오인. → `_looks_like_eb_target()`로 산식 변수줄(`^[A-Z]:`, "기발행주식수" 등) 배제 + `교환대상` 라벨 앵커 + narrative(`교환대상 주식: …보통주식`, 푸드나무) 폴백 + 자기주식 마커 최소 신호. 결과: 광동제약 "광동제약 보통주", 푸드나무 "에프엔프레시 보통주" 정확 복원.
- **중복 행**: 구조화 complete 행 + 같은 EB의 정정 stub 복원이 2행 생성(동성제약). → `_dedup_eb_rows()`로 (회차,총액) 그룹 dedup, 우선순위 구조화>복원>탐지.

## 검증 (라이브 전수 187사)

시장 전체 `list.json` 스캔(2024-01~2026-06)으로 EB 발행사 **186곳 전수 발굴** + 태광(복원) = **187사 실측**(넓은 윈도우 2024-01~2026-06).

| 구분 | 결과 |
|---|---|
| **PASS (조건 완비)** | **219 이벤트** (교환가·교환대상·주식수·% 전부) |
| **DETECT (탐지만)** | 2 (한라IMS·녹원씨엔아이 — 첨부정정 014, 누락→surface 전환) |
| **WARN (빈값)** | 0 |
| **FAIL (누락/에러)** | 0 |

> 기본 24개월 윈도우에선 2024 상반기 EB 19곳이 윈도우 밖이라 0건(정상). 넓은 윈도우로 재확인 시 전부 PASS/DETECT — 누락 0 확인.

커버 다양성:
- **자기주식 교환**(의결권 희석): 태광·남성·위닉스·알서포트·크레버스·한국전자홀딩스·제노레이·티에프이·범한퓨얼셀·한국토지신탁·오킨스전자·클리오
- **타사주식 교환**: 경농·금강공업·디지캡·엠에스오토텍·PS일렉트로닉스·이엠텍·심텍홀딩스·HD한국조선해양·만호제강·코오롱·코오롱인더·링크드·아이티아이즈·HLB생명과학
- **다건**: PS일렉트로닉스 7 / 링크드 4 / 심텍·HD조선·만호·크레버스·HLB 2
- **초대형**: HD한국조선해양 2.37조 + 6천억
- **복원경로(A)**: 태광(3,185.8억, 교환가 1,172,251, 자기주식 271,769주)·위닉스·광동제약·푸드나무(에프엔프레시)
- **탐지경로(C)**: 한라IMS·녹원씨엔아이 (첨부정정 document.xml 014)
- **dedup**: 동성제약(구조화+복원 중복 → 1행)
- **회귀**: 삼성전자 no_filing 정상 (항상 켜진 list.json 체크가 no-EB를 깨지 않음)

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
