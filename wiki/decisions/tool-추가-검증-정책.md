---
type: decision
title: release_v2 신규 tool 추가 검증 정책
tags: [decision, release-v2, tool, validation, source-policy]
date: 2026-04-18
related: [DART-KIND-매핑-화이트리스트-2026-04, pblntf-ty-필터링, XML-vs-PDF]
---

# release_v2 신규 tool 추가 검증 정책

## 결정

release_v2에서 새 tool은 먼저 `data tool`인지 `action tool`인지 분류한다.

- `data tool`
  - 애널리스트가 직접 보는 사실/원문/구조화 데이터 제공 도구
  - 예: `shareholder_meeting`, `ownership_structure`, `dividend`
- `action tool`
  - 여러 data tool을 묶어 바로 쓰는 결과물을 만드는 도구
  - 예: `prepare_vote_brief`, `build_campaign_brief`

새 tool은 아래 원칙을 반드시 따른다.

1. 모든 public tool은 `회사 이름 -> 내부 식별자(ticker / corp_code / ISIN / rcept_no)` 흐름을 명확히 가져야 한다.
2. `PDF 다운로드`는 기본 경로에서 제외한다.
3. `DART API + DART document.xml`이 기본 축이다.
4. `KIND HTML`은 화이트리스트 공시에만 허용한다.
5. `Naver`는 참고 보조만 허용하며 공식값을 덮어쓰지 않는다.
6. action tool은 새 사실을 만들면 안 되고, 검증된 data tool과 evidence만 재구성해야 한다.

## 왜 이 정책이 필요한가

애널리스트 입장에서는 `빨리 보는 결과물`도 중요하지만, 더 중요한 것은 `근거가 어디서 왔는지`와 `틀릴 수 있는 구간이 어디인지`다.

실제 점검 결과:

- 어떤 공시는 `DART -> KIND`가 안정적으로 붙지만
- 어떤 공시는 raw 번호만 넣어도 다른 회사 공시나 전혀 다른 제목이 열렸다
- 따라서 `새 tool 추가`는 곧 `새 소스 리스크 추가`일 수 있다

즉, 새 tool은 기능 추가가 아니라 `데이터 신뢰도 계약`을 추가하는 일로 본다.

## 공통 필수 항목

새 tool 제안서는 최소한 아래를 먼저 써야 한다.

| 항목 | 질문 |
|---|---|
| analyst use case | 이 tool이 어떤 의사결정을 돕는가 |
| tool type | `data` / `action` 중 무엇인가 |
| target company input | 사용자가 회사명을 어떻게 넣는가 |
| internal identifiers | ticker / corp_code / ISIN / rcept_no 중 무엇을 쓰는가 |
| disclosure universe | 어떤 공시를 읽는가 |
| pblntf_ty | DART 검색 코드는 무엇인가 |
| primary source | 1차 소스는 무엇인가 |
| secondary source | 2차 보강 소스는 무엇인가 |
| whitelist need | KIND 등 외부 HTML 화이트리스트 검토가 필요한가 |
| output fields | 사용자가 실제로 받는 핵심 데이터는 무엇인가 |
| evidence fields | 어떤 근거를 붙일 것인가 |
| requires_review triggers | 언제 검토 필요로 올릴 것인가 |

## 소스 정책

release_v2의 기본 소스 우선순위는 아래다.

1. `DART API`
2. `DART document.xml`
3. `KIND HTML` 또는 필요한 경우 `DART 웹 HTML`
4. `Naver` 보조
5. `requires_review`

주의:

- 위 순서는 `기본 우선순위`다
- 실제로는 데이터 항목별로 가장 적합한 공식 소스를 우선한다
- 예: 주총 결과는 `KIND`가 더 적합할 수 있지만, 이 경우에도 화이트리스트 검증이 선행돼야 한다

### 금지

- 화이트리스트 밖 KIND raw 조회
- 제목/회사명/날짜 검증 없는 외부 HTML 채택
- Naver 값으로 DART/KIND 공식값 덮어쓰기
- XML 품질이 낮다는 이유만으로 자동 PDF 다운로드 진입

## Data Tool 검증 매뉴얼

data tool은 `바로 보는 데이터 탭`이다.  
따라서 새 data tool은 아래 순서를 반드시 거친다.

### 1. 분석 범위 고정

먼저 아래를 고정한다.

- 어떤 질문에 답하는가
- 어떤 공시 유형을 읽는가
- 그 공시가 정기/수시/지분/거래소공시 중 어디에 속하는가
- 동일 회사에서 연도별로 여러 건이 있을 때 어떤 우선순위로 고를 것인가

예:

- `shareholder_meeting`
  - 정기/임시
  - 소집공고 / 결과 / 정정공시
- `ownership_structure`
  - 대량보유 / 임원주요주주 / 자사주

### 2. OpenDART 공식 문서 조사

새 data tool은 구현 전에 먼저 공식 문서에서 아래를 조사한다.

- 가져올 수 있는 공식 API가 있는가
- `list.json`에서 어떤 `pblntf_ty`를 써야 하는가
- `document.xml`로 본문이 안정적으로 확보되는가
- 구조화 API가 있다면 XML보다 무엇이 더 정확한가

즉, `API 가능 범위`를 먼저 확정하고, 그 다음에 HTML 보강 여부를 판단한다.

### 3. 공시 매핑표 작성

새 data tool마다 아래 표를 만든다.

| field | disclosure type | pblntf_ty | primary source | secondary source | external mapping | note |
|---|---|---|---|---|---|---|
| 예: vote result | 주주총회결과 | I | KIND HTML | DART list | `80 -> 00` | whitelist only |

이 표가 없는 tool은 merge하지 않는다.

### 4. 회사 식별 점검

모든 public tool은 내부적으로 `company` 단계를 탄다.

확인 항목:

- 회사명 한글/영문/약칭으로 진입 가능한가
- 동명/유사명 혼동 시 `ambiguous`로 멈추는가
- 내부적으로 ticker / corp_code / ISIN / market / sector가 정리되는가
- 이후 공시 선택 시 `rcept_no`가 일관되게 이어지는가

### 5. 화이트리스트 체크

KIND나 다른 외부 HTML을 쓰려면 먼저 기존 화이트리스트를 확인한다.

- 이미 화이트리스트에 있는 공시인가
- 기존 규칙을 그대로 재사용 가능한가
- 아니면 신규 화이트리스트 후보인가

기존 화이트리스트는 [[DART-KIND-매핑-화이트리스트-2026-04]]를 따른다.

### 6. 신규 화이트리스트 후보 검증

화이트리스트에 없는 공시를 KIND와 연결하려면 아래를 반드시 수행한다.

1. 해당 공시 유형으로 `회사 3개` 1차 샘플 선정
2. 각 샘플에서
   - DART 검색 성공 여부
   - `document.xml` 확보 여부
   - KIND 매핑 규칙 존재 여부
   - 제목/회사명/날짜 일치 여부
   - false match 여부
3. 1차 샘플이 안정적이면
4. `추가 10개 샘플` 재검증
5. 하나라도 false match가 나오면 화이트리스트 승인 보류

정리:

- `미매칭`은 `DART-only` fallback으로 처리 가능
- `false match`는 허용하지 않는다

### 7. 샘플 검증 기준

새 data tool은 최소한 아래 샘플 검증을 남긴다.

- 공시 유형별 회사 3개
- 가능하면 대형주/금융/이슈 기업 혼합
- 최근 케이스 위주
- 정정공시가 있으면 1개 이상 포함

검증 로그에는 최소한 아래를 남긴다.

- 회사명
- 공시명
- 공시일
- rcept_no
- primary source 결과
- secondary source 결과
- 판정: `exact / partial / conflict / requires_review`

### 8. evidence 설계

data tool은 핵심 필드마다 가능하면 아래 근거를 붙인다.

- `rcept_no`
- `source_type`
- `section`
- `snippet`
- `parser`
- `confidence`

애널리스트가 나중에 “이 숫자가 어디서 왔나”를 바로 확인할 수 있어야 한다.

### 9. 출시 게이트

아래를 다 채우지 못하면 data tool은 public으로 올리지 않는다.

- 공시 매핑표 작성 완료
- 회사 식별 흐름 검증 완료
- 소스 우선순위 정의 완료
- KIND 화이트리스트 여부 판정 완료
- 샘플 로그 저장 완료
- `requires_review` 조건 정의 완료
- evidence 예시 확인 완료

## Action Tool 검증 매뉴얼

action tool은 `바로 쓰는 결과물`이다.  
예: 투표 메모, engagement 메모, 캠페인 브리프

따라서 action tool은 `새 데이터를 발명하는지`가 아니라 `검증된 데이터를 제대로 재구성하는지`를 본다.

### 1. 선행 조건

action tool은 아래 없이는 추가하지 않는다.

- 참조하는 data tool이 이미 검증 완료 상태일 것
- 각 핵심 판단이 어느 data tool / evidence에서 왔는지 연결될 것
- upstream이 `partial`, `conflict`, `requires_review`일 때 행동 규칙이 있을 것

### 2. 설계 원칙

- action tool은 새 사실을 만들지 않는다
- action tool은 결론과 근거를 분리한다
- action tool은 확정적 문장을 남발하지 않는다
- upstream 데이터가 애매하면 결과도 `requires_review`로 올린다

예:

- 안건명이 비정상적으로 비어 있으면 `투표 의견`을 확정하지 않는다
- 지분구조와 분쟁 신호가 충돌하면 `캠페인 브리프`를 단정적으로 쓰지 않는다

### 3. 시나리오 검증

action tool은 최소 `실전 시나리오 5개`로 점검한다.

권장 시나리오:

1. 정기주총 의안 검토
2. 임시주총 경영권 분쟁
3. 배당정책 변화 점검
4. 5% 보유 목적 변화 반영
5. 소송/분쟁 이슈 반영

각 시나리오에서 아래를 본다.

- 핵심 결론이 evidence로 추적 가능한가
- upstream partial/conflict가 결과에 반영되는가
- 사람이 다시 원문을 열어야 하는 경우 `requires_review`로 표시되는가

### 4. 출력 검증

action tool 출력에는 최소한 아래가 있어야 한다.

- `status`
- `summary`
- `key_findings`
- `evidence_refs`
- `next_actions` 또는 `watch_items`

### 5. 출시 게이트

아래를 다 채우지 못하면 action tool은 public으로 올리지 않는다.

- 참조 data tool 목록 확정
- 각 판단의 evidence chain 확인
- 시나리오 5개 검증 완료
- partial/conflict/requires_review 처리 규칙 확인
- 결과 문구가 evidence 범위를 넘지 않는지 검토 완료

## 회사 식별 및 공시 매핑 체크리스트

모든 새 public tool은 아래 체크리스트를 통과해야 한다.

### 회사 식별

- 회사명으로 시작 가능한가
- 유사명 충돌 시 `ambiguous` 처리가 되는가
- ticker / corp_code / ISIN / market / sector가 정리되는가

### 공시 매핑

- 어떤 공시군을 읽는지 명확한가
- `pblntf_ty`가 지정되는가
- report name keyword가 명시적인가
- 공시일/정정공시 우선순위가 있는가
- 최종적으로 `rcept_no`가 일관되게 이어지는가

### 외부 HTML / KIND

- 화이트리스트 대상인가
- 번호 변환 규칙이 문서화됐는가
- 회사명/제목/날짜 검증이 있는가
- false match 방지 장치가 있는가

## 권장 판정 상태

모든 신규 public tool은 아래 상태 중 하나를 반환하도록 설계한다.

- `exact`
- `ambiguous`
- `partial`
- `conflict`
- `requires_review`
- `error`

## 필수 기록 양식

새 tool 추가 시 위키에는 최소 아래 3개가 남아야 한다.

1. `tool proposal`
   - tool type
   - analyst use case
   - disclosure universe
2. `source mapping`
   - 공시별 primary / secondary source
   - whitelist 여부
3. `validation log`
   - 샘플 회사
   - rcept_no
   - 판정
   - false match 여부

## 요약

release_v2에서 새 tool 추가는 `기능 추가`가 아니라 `근거 체계 추가`다.

따라서:

- data tool은 `공시/소스/매핑`을 먼저 검증하고
- action tool은 `근거를 넘지 않게` 재구성하는지 검증한다

이 정책을 통과하지 못한 tool은 `실험용`으로는 둘 수 있어도 `public tool`로는 올리지 않는다.
