---
type: analysis
title: Proxy Voting Decision Tree (통합 의결권 행사 판단 프레임워크)
tags: [proxy-voting, governance, decision-tree, framework]
related: [jpm-voting-process, 주총방어전략-2026, 주총체크리스트-2026, 주총방어-시나리오-4가지, 상법개정-타임라인-2026, 의결권, cross-domain-체이닝]
---

# Proxy Voting Decision Tree

3개 원본 소스를 통합한 의결권 행사 판단 프레임워크.

- [[jpm-voting-process]] - 글로벌 기관투자자 투표 프로세스 (JPMAM)
- [[주총방어전략-2026]] - 방어 시나리오 4가지 (미래에셋증권)
- [[주총체크리스트-2026]] - 주총 체크리스트 9개 항목 (미래에셋증권)

---

## 1. 안건 유형별 판단 기준

### 재무제표 승인

| 조건 | 판단 | 확인 tool |
|------|------|----------|
| 감사의견 적정 + 이익 추세 양호 | 찬성 | agm_financials_xml |
| 감사의견 한정/부적정 | 반대 | agm_financials_xml |
| 보고사항 (투표 없음) | 해당 없음 | agm_agenda (안건 유형 확인) |

### 이사 선임

| 조건 | 판단 | 확인 tool |
|------|------|----------|
| 독립 사외이사, 경력 적합 | 찬성 | agm_personnel_xml |
| 경영진 측 내부이사, 겸직 과다 | 기권/반대 | agm_personnel_xml |
| [[집중투표]] 안건 | 후보별 개별 판단 | agm_result (득표율) |
| 정관변경(이사 정수 축소)이 선행 | 방어 전술 의심 | agm_agenda (안건 순서) |

### 정관변경

| 조건 | 판단 | 확인 tool |
|------|------|----------|
| 상법 개정 반영 (형식적) | 찬성 | agm_aoi_change_xml |
| 집중투표 배제 조항 삽입 | 반대 | agm_aoi_change_xml |
| 이사 정수 축소 (이사선임 전) | 방어 전술 의심 -> 반대 | agm_aoi_change_xml, agm_agenda |

### 보수한도

| 조건 | 판단 | 확인 tool |
|------|------|----------|
| 전기 대비 소폭 증액, [[소진율]] 70%+ | 찬성 | agm_compensation_xml |
| 대폭 증액, 소진율 30% 미만 | 반대 | agm_compensation_xml |
| 이사/감사 별도 안건 | 각각 판단 | agm_compensation_xml |

### 자사주

| 조건 | 판단 | 확인 tool |
|------|------|----------|
| 소각 목적 취득 | 찬성 (주주환원) | own_treasury_tx, agm_agenda |
| 경영권 방어 목적 보유 | 반대/기권 | own_treasury (보유 비율) |
| 재단 출연 계획 | 방어 전술 의심 | own_treasury_tx |

### 배당

| 조건 | 판단 | 확인 tool |
|------|------|----------|
| [[배당성향]] 적정 (업종 평균 이상) | 찬성 | div_detail, div_ratio |
| DPS 감소 + 이익 증가 | 반대/주주제안 | div_history, agm_financials_xml |
| [[감액배당]] | 사유 확인 필요 | agm_agenda |

---

## 2. 찬성/반대/기권 조건 종합

| 판단 | 조건 |
|------|------|
| **찬성** | 가이드라인 부합 + 방어 전술 미감지 + 주주가치 제고 |
| **반대** | 주주가치 훼손, 방어 전술 감지, 독립성 부족 |
| **기권** | 정보 불충분, 찬반 논거 균형, 이해충돌 (JPMAM 프로세스 참조) |
| **투표 불가** | 대차 미회수, share blocking, 규제 제한, proxy 자료 부족 ([[jpm-voting-process]] 참조) |

---

## 3. 체크포인트 (미래에셋 체크리스트 9개)

[[주총체크리스트-2026]] 기반. 각 항목별 OPM tool 매핑:

| # | 체크포인트 | 핵심 확인 사항 | OPM tool |
|---|-----------|---------------|----------|
| 1 | 재무제표 승인 | 당기순이익, 감사의견 | `agm_financials_xml` |
| 2 | 이사 선임 | 후보자 경력, 독립성, 겸직 | `agm_personnel_xml` |
| 3 | 감사/감사위원 선임 | [[감사위원-의결권-제한]] 3% 룰 | `agm_result`, `own_major` |
| 4 | [[정관변경]] | 변경 사유, 방어 목적 여부 | `agm_aoi_change_xml` |
| 5 | [[보수한도]] | 전기 소진율, 증액 사유 | `agm_compensation_xml` |
| 6 | [[자사주]] | 취득/처분/소각 계획 | `own_treasury`, `own_treasury_tx` |
| 7 | 배당 | DPS, [[배당성향]], 연속성 | `div_detail`, `div_history` |
| 8 | [[주주제안]] | 안건 내용, 이사회 의견 | `agm_agenda` |
| 9 | 기타 (합병/분할) | 합병비율, 소수주주 보호 | `agm_agenda`, `agm_extract` |

---

## 4. 방어 전술 감지 ([[주총방어-시나리오-4가지]])

| 시나리오 | 감지 신호 | OPM tool |
|----------|----------|----------|
| 1. 집중투표/분리선출 무력화 | 정관변경(이사 정수 축소)이 이사선임 앞에 배치 | `agm_agenda`, `agm_aoi_change_xml` |
| 2. 보수/임기 우회 | 보수한도 대폭 증액 + 소진율 낮음 | `agm_compensation_xml` |
| 3. 합산 3% 룰 회피 | 주총 전 최대주주 지분 감소, 보유목적 변경 | `own_major`, `own_block`, `own_latest` |
| 4. 자사주 소각 의무화 회피 | 자사주 비율 높음 + 소각 안건 없음/재단 출연 | `own_treasury`, `own_treasury_tx`, `agm_agenda` |

---

## 5. OPM Tool 연결 (데이터 확인 가이드)

### 안건 탐색 단계

```
agm_search(ticker) -> 소집공고 목록
agm_document(rcept_no) -> 문서 전문
agm_agenda(rcept_no) -> 안건 목록 + 순서
```

### 안건별 상세 분석

```
agm_financials_xml(rcept_no) -> 재무제표 (감사의견, 당기순이익)
agm_personnel_xml(rcept_no) -> 이사 후보자 (경력, 독립성)
agm_compensation_xml(rcept_no) -> 보수한도 (소진율)
agm_aoi_change_xml(rcept_no) -> 정관변경 (변경 내용)
agm_treasury_share_xml(rcept_no) -> 자사주 안건
```

### 지분 구조 확인

```
own_major(ticker) -> 최대주주+특관인 합산
own_block(ticker) -> 5% 대량보유자 (보유목적)
own_treasury(ticker) -> 자사주 현황
own_latest(ticker) -> 내부자 거래
```

### 배당 정책 확인

```
div_detail(ticker) -> 배당 상세 (DPS, 배당성향)
div_history(ticker) -> 배당 추이
div_ratio(ticker) -> 배당수익률
```

### 투표 결과 확인

```
agm_result(ticker) -> 안건별 찬반율, 참석률
```

### 통합 분석 흐름 (JPMAM 프로세스 적용)

```
1. agm_agenda -> 안건 유형 분류 (Prescribed vs Case-by-case)
2. 유형별 tool 호출 -> 데이터 수집
3. 체크포인트 9개 대조 -> 찬성/반대/기권 판단
4. 방어 전술 감지 -> 4가지 시나리오 체크
5. 최종 의결권 행사 권고
```
