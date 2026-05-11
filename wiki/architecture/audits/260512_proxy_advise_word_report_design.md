---
type: audit
title: proxy_advise Word 보고서 양식 설계
domain: action
created: 2026-05-12
updated: 2026-05-12
related: [proxy_advise_before_meeting, 260510_proxy_advise_audit_통합정리, proxy_advise_word_report_spec]
---

# proxy_advise Word 보고서 양식 설계

## 최종 권고

기본 채택안은 **“1페이지 요약 + 안건별 본문 + 후보/근거 부록” 3층 구조**다.

이 형식이 가장 적합한 이유는 다음과 같다.

- `samples/`의 실무 문서들이 공통적으로 가진 강점
  - 회사/주총 메타를 첫 페이지에서 빠르게 파악
  - 안건별 권고표를 초반에 제시
  - 필요한 경우 안건별 근거를 뒤에서 상세 전개
- 현재 OPM `proxy_advise`가 실제로 보유한 데이터 구조
  - `agenda_decisions[]`가 이미 “권고 + 사유 + facts + risk + policy + 근거 공고” 단위로 정리되어 있음
  - `candidates_evaluations[]`가 후보 부록을 만들기에 충분한 밀도를 가짐
  - `financial_summary`, `ownership_summary`, `governance_summary`, `evidence_refs`가 별도 부록/참고 섹션으로 자연스럽게 매핑됨
- Word 문서 사용 방식
  - 실제 실무에서는 첫 페이지 요약만 보는 사용자와, 안건별 근거까지 읽는 사용자, 후보 raw까지 확인하는 사용자가 나뉜다
  - 따라서 “한 장 요약”과 “추적 가능한 상세 본문”을 같이 가져가는 것이 유리하다

한 줄 결론:

- **v1 기본안**: `요약 중심 표준형 (Standard)`
- **v2 확장안**: `상세 부록 강화형 (Full)`
- `compact`는 후속 옵션으로 만들 수 있지만 기본 채택안으로는 권장하지 않는다.

## 입력 근거

### 외부 샘플

검토한 샘플:

- `samples/KCGS_2025_KB금융.pdf`
- `samples/[서스틴베스트] 의안분석 보고서_Sample SK.pdf`
- `samples/영원무역홀딩스 - 대신경제연구소.pdf`
- `samples/하이브(352820)_20250331_보고서 (1).pdf`

### 내부 기준

검토한 OPM 기준:

- [proxy_advise_word_report_spec.md](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/proxy_advise_word_report_spec.md:1)
- [proxy_advise_before_meeting.md](/Users/marcoyou/Projects/open-proxy-mcp/wiki/tools/proxy_advise_before_meeting.md:1)
- [proxy_advise.py](/Users/marcoyou/Projects/open-proxy-mcp/open_proxy_mcp/services/proxy_advise.py:1367)
- [proxy_advise_before_meeting.py](/Users/marcoyou/Projects/open-proxy-mcp/open_proxy_mcp/tools_v2/proxy_advise_before_meeting.py:1)

## 샘플 형식 비교

### 공통 패턴

샘플 4종에서 공통으로 반복되는 구조는 아래와 같다.

1. 첫 페이지 상단에 회사명, 종목코드, 주총 일시/장소, 시장/산업, 결산일 같은 메타데이터를 둔다.
2. 초반 1~2페이지 안에 **안건별 권고표**를 둔다.
3. 권고표의 핵심 컬럼은 대체로 `번호 / 의안명 / 의견 / 사유`다.
4. 뒤쪽에는 회사 profile, 주주구성, 재무 요약, 지배구조 또는 기업집단 정보가 붙는다.
5. 반대 또는 주의 안건은 표 안에 짧게 사유를 넣거나, 뒤쪽 상세 section에서 풀어 쓴다.

### 샘플별 특징

#### KCGS형

특징:

- 가장 공공기관형/표준 템플릿 느낌이 강하다.
- 첫 페이지 메타와 `의안 및 권고안` 표가 매우 명확하다.
- 이후 기업집단 정보, 주주구성, 재무 하이라이트가 붙는다.
- 본문은 비교적 간결하고 “권고표 중심”이다.

장점:

- 빠르게 읽힌다.
- Word export로 옮기기 쉽다.

약점:

- 반대 사유와 개별 안건의 논리 전개가 상대적으로 얇다.

#### 서스틴베스트형

특징:

- `Proxy Advisory Report`라는 영문형 헤더를 쓰되, 내용은 국내 실무형이다.
- 주총 개요 + 안건 및 의견 표가 앞에 오고, 이후 기업집단 profile과 주주 구성을 자세히 붙인다.
- 반대 안건이 있을 때 개별 상정 주석과 장문 사유를 적는다.

장점:

- 요약표와 상세 사유의 연결이 좋다.
- 안건별 반대 논리를 따로 적기 쉽다.

약점:

- 회사 profile 비중이 커서 문서가 길어지기 쉽다.

#### 한국ESG연구소형

특징:

- `의안유형`, `내용`, `권고의견`, `반대사유`로 표를 구조화한다.
- 각 안건별로 `권고의견 / 근거규정 / 권고사유`를 상세하게 전개한다.
- 재무제표 요약 같은 supporting table이 안건 바로 뒤에 붙는다.

장점:

- “표준형 안건별 상세 본문” 구조를 만들기 가장 좋다.
- OPM의 `policy_citation`, `reason`, `facts`와 잘 맞는다.

약점:

- 모든 안건에 같은 밀도의 본문을 붙이면 문서가 과하게 길어진다.

#### 하이브형

특징:

- 첫 페이지 최상단에 `전자투표`, `집중투표`, `주총 집중일`, `배당기준일` 같은 **기타 주요사항**을 둔다.
- 이후 주총 개요, 안건 및 의견, 주주 구성으로 이어진다.
- “주총 실무자에게 바로 필요한 운영 정보”가 빠르게 보인다.

장점:

- 운영 메타를 별도 박스로 빼는 아이디어가 좋다.

약점:

- OPM이 현재 항상 자동 생성할 수 있는 운영 메타가 제한적이라, v1에 그대로 쓰기는 어렵다.

## OPM 데이터 적합성 평가

### 현재 바로 매핑되는 것

`proxy_advise`는 이미 Word 보고서의 핵심 뼈대를 만들 만큼 구조화돼 있다.

직접 매핑 가능한 필드:

- 회사/회차 메타
  - `canonical_name`
  - `company_id`
  - `year`
  - `meeting_type`
- 전체 현황
  - `agenda_count`
  - `candidates_count`
  - `vote_style`
  - `audit_history_enabled`
- 안건별 권고 본문
  - `agenda_decisions[].agenda_title`
  - `agenda_decisions[].agenda_category`
  - `agenda_decisions[].decision`
  - `agenda_decisions[].reason`
  - `agenda_decisions[].facts`
  - `agenda_decisions[].risk_factors`
  - `agenda_decisions[].policy_citation`
  - `agenda_decisions[].policy_basis`
  - `agenda_decisions[].evidence_rcept_no`
- 후보 부록
  - `candidates_evaluations[]`
  - independence / disqualification / audit history / performance
- 참고 부록
  - `financial_summary`
  - `ownership_summary`
  - `governance_summary`
  - `evidence_refs`

### 현재 없는 것 또는 파생이 필요한 것

현재 없는 필드 또는 바로 쓰기 애매한 항목:

- 문서 수준 `executive summary` 한 줄
  - 현재는 안건별 `decision`은 있으나, 문서 전체 총평은 별도 생성 로직이 필요
- 회사명/주총일/장소의 완전한 문서형 메타
  - tool spec상 정보는 upstream에 있지만 `proxy_advise` output에 명시적으로 평탄화돼 있지 않다
- 안건 번호
  - `agenda_title`은 있으나 Word 보고서 표에서 기대하는 `제1호/제2호` 번호 필드가 항상 flat하게 노출되는지 점검 필요
- 안건 유형 한글 라벨
  - `agenda_category`는 machine-friendly라서 문서용 라벨 테이블이 필요
- “찬성/반대/검토”의 한국어 스타일 통일
  - 현재 decision은 `FOR/AGAINST/REVIEW/NO_DATA`
- 근거 공고의 보고서형 인용 문자열
  - 현재는 `evidence_rcept_no`나 `evidence_refs`는 있으나 문서용 citation 템플릿은 별도 필요
- 주주총회 운영 메타
  - 전자투표, 집중투표 배제, 배당기준일 같은 항목은 별도 조합 로직 필요

## 대안 비교

### 안 A. 권고표 중심 압축형

구성:

- 1페이지 메타
- 1~2페이지 안건별 권고표
- 끝

장점:

- 구현이 매우 쉽다.
- 빠르게 읽힌다.

단점:

- OPM의 장점인 `facts/risk/policy/evidence`를 거의 살리지 못한다.
- 내부 검토/보관 문서로는 약하다.

판단:

- 기본안으로는 부적합

### 안 B. 요약 + 안건별 본문 + 부록형

구성:

- 표지/헤더
- 1페이지 요약
- 안건별 본문
- 후보/재무/근거 부록

장점:

- 샘플들의 공통 구조를 가장 잘 흡수한다.
- OPM 데이터 구조와 가장 잘 맞는다.
- v1 즉시 구현과 v2 확장이 모두 쉽다.

단점:

- 문서 길이가 다소 길어질 수 있다.

판단:

- **기본 채택안**

### 안 C. 안건별 full memo형

구성:

- 모든 안건을 동일 밀도로 장문 memo화

장점:

- 법무/심의용으로는 강하다.

단점:

- 길고 무겁다.
- `FOR`인 단순 안건에도 과한 서술이 붙는다.

판단:

- v2 확장용으로만 적합

## 추천 Word 양식

### 기본안 이름

`OPM Proxy Advice Report - Standard`

### 문서 구조

1. 표지/문서 헤더
2. 문서 요약
3. 주총 메타 정보
4. 안건별 권고 요약표
5. 안건별 상세 판단
6. 후보 평가 부록
7. 회사 참고 정보 부록
8. 근거 공시 / 출처 부록
9. caveat / 생성 범위 메모

## 섹션별 템플릿 스펙

### 1. 표지/문서 헤더

목적:

- 어떤 회사의 어떤 회차 주총에 대한 사전 의결권 자문 문서인지 즉시 식별

필수 필드:

- 회사명
- 종목코드 또는 `company_id`
- 문서명: `의결권 행사 검토 메모` 또는 `Proxy Advice Report`
- 기준 회차 (`2026년 정기주주총회` 등)
- 작성일

선택 필드:

- vote style (`Open Proxy guideline` / `Internal policy variant`)
- 문서 버전

현재 자동 생성 가능:

- 부분 가능
- 회사명, 연도, meeting_type, vote_style은 가능
- 작성일은 generator가 넣으면 됨

권장 형식:

- 상단 큰 제목 1줄
- 우측 상단 메타 박스

### 2. 문서 요약

목적:

- 문서를 30초 안에 파악하게 함

필수 필드:

- 총 안건 수
- 반대 안건 수
- 검토 안건 수
- 후보 평가 대상 수
- 핵심 주의사항 2~4개

선택 필드:

- `NO_DATA` 안건 수

현재 자동 생성 가능:

- 가능
- `agenda_decisions[]` 집계로 계산 가능

권장 형식:

- 좌측 summary box
- 우측 “주요 검토 포인트” bullet

### 3. 주총 메타 정보

목적:

- 회의 운영 정보와 회사 기본 맥락 제공

필수 필드:

- 회사명
- 연도
- meeting_type

선택 필드:

- 회의 일시
- 회의 장소
- 전자투표 여부
- 집중투표 관련 메타
- 배당 기준일

현재 자동 생성 가능:

- 일부만 가능
- 일시/장소/운영 메타는 upstream 추가 평탄화가 필요

권장 형식:

- 2열 메타 테이블

### 4. 안건별 권고 요약표

목적:

- 샘플 문서들의 핵심 장점을 그대로 가져오는 section

필수 필드:

- 번호
- 안건명
- 안건 유형
- 권고
- 핵심 사유 요약

선택 필드:

- 법령 hit tag
- 근거 공고 번호

현재 자동 생성 가능:

- 대부분 가능
- 번호는 별도 추출 또는 순번 fallback 필요

권장 형식:

| 번호 | 안건 | 유형 | 권고 | 핵심 사유 |

표현 규칙:

- `FOR` → `찬성`
- `AGAINST` → `반대`
- `REVIEW` → `검토`
- `NO_DATA` → `추가 확인 필요`

### 5. 안건별 상세 판단

목적:

- 표 뒤에 붙는 핵심 본문
- OPM의 실제 강점을 드러내는 section

각 안건 subsection에 포함할 것:

- 안건명
- 최종 권고
- 한 줄 판단 요약
- 사실관계 (`facts`)
- 위험 신호 (`risk_factors`)
- 정책/법령 근거 (`policy_citation`, `policy_basis`)
- 근거 공고

현재 자동 생성 가능:

- 가능

권장 형식:

```text
제N호 의안. [안건명]
권고: 찬성 / 반대 / 검토

판단 요지
- ...

주요 사실
- ...

위험 신호
- ...

근거 규정 / 정책
- ...

근거 공시
- ...
```

정책:

- `FOR`인 단순 안건은 4~6줄
- `AGAINST`/`REVIEW` 안건은 더 길게

### 6. 후보 평가 부록

목적:

- 이사/감사 후보 안건에서 상세 raw를 분리 보관

필수 필드:

- 후보명
- role_type
- 선임유형
- 독립성
- 결격사유
- audit history

선택 필드:

- main_job
- recommendation reason raw
- career groups
- performance

현재 자동 생성 가능:

- 가능

권장 형식:

- 요약표 1개
- 필요 시 후보별 상세 카드

### 7. 회사 참고 정보 부록

목적:

- 샘플 문서의 company profile 기능 흡수

구성:

- `financial_summary`
- `ownership_summary`
- `governance_summary`

현재 자동 생성 가능:

- 부분 가능
- summary 수준은 가능
- 샘플 문서 수준의 상세 회사 profile은 별도 확장 필요

### 8. 근거 공시 / 출처 부록

목적:

- 문서의 추적 가능성 보강

필수 필드:

- section
- rcept_no
- note

선택 필드:

- viewer URL

현재 자동 생성 가능:

- 가능

권장 형식:

- appendix table

### 9. caveat / 생성 범위 메모

목적:

- 자동 생성 문서의 책임 범위 명시

필수 내용:

- 본 문서는 OPM 자동 분석 결과를 기반으로 생성
- 일부 항목은 raw 기반 사용자 검토 필요
- 사람 검토가 필요한 안건 (`REVIEW`, `NO_DATA`)은 최종 행사 전 재확인 권장

현재 자동 생성 가능:

- 가능

## OPM field mapping

| Word 섹션 | OPM 필드 | 비고 |
|---|---|---|
| 표지 | `canonical_name`, `year`, `meeting_type`, `vote_style` | 작성일은 generator 추가 |
| 요약 | `agenda_decisions[]`, `candidates_count` | 집계 로직 필요 |
| 주총 메타 | `year`, `meeting_type` + upstream 평탄화 필요 필드 | 일시/장소는 추가 평탄화 필요 |
| 안건 요약표 | `agenda_decisions[].agenda_title`, `agenda_category`, `decision`, `reason` | 번호 필드 추가 필요 |
| 안건 상세 | `agenda_decisions[].facts`, `risk_factors`, `policy_citation`, `policy_basis`, `evidence_rcept_no` | v1 핵심 |
| 후보 부록 | `candidates_evaluations[]` | role_type / independence / performance 등 |
| 재무 부록 | `financial_summary` | 표준 요약형 |
| 지분 부록 | `ownership_summary` | summary 수준 |
| 거버넌스 부록 | `governance_summary` | summary 수준 |
| 출처 부록 | `evidence_refs[]` | appendix table |

## 부족한 데이터 / 추가 파생 로직

### v1 전에 있으면 좋은 것

- flat `meeting_datetime`
- flat `meeting_location`
- flat `agenda_number`
- flat `agenda_label_ko`
- 문서 수준 `executive_summary`
- 안건별 `short_reason`

### v2에서 고려할 것

- `meeting mechanics` 묶음
  - 전자투표
  - 집중투표 배제/허용
  - 배당 기준일
- 표지 하단 `핵심 governance signal`
- 후보별 long-form profile appendix
- `REVIEW` 안건 전용 상세 memo appendix

## 구현 메모

### v1 즉시 구현 범위

바로 구현 권장:

- 표지
- 문서 요약
- 안건별 권고 요약표
- 안건별 상세 판단
- 후보 부록
- 근거 공시 부록

이 범위만으로도 Word 보고서 품질은 충분히 실무형이 된다.

### v2 확장 범위

- 회사 profile 부록 강화
- 운영 메타 박스
- `compact / standard / full` 3 variant
- 반대/검토 안건 자동 강조 스타일

### Word export 구현 시 주의점

- 표는 첫 페이지 1개, 상세 본문은 표 아래로 자연스럽게 이어지게 해야 한다.
- `AGAINST`/`REVIEW` 안건은 색 또는 아이콘으로 시각 강조할 수 있다.
- raw 텍스트(`recommendation_reason_raw`, 일부 후보 경력)는 본문보다 부록에 두는 것이 낫다.
- `facts` dict는 그대로 dump하지 말고 라벨 맵을 둬서 한국어 문장/표로 변환해야 한다.
- `evidence_rcept_no`는 본문 footnote 스타일이나 appendix reference number 스타일 중 하나로 통일해야 한다.

## 기본 출력 예시

권장 문서 흐름:

1. 제목: `KT&G 2026년 정기주주총회 의결권 행사 검토 메모`
2. 요약 박스:
   - 총 안건 8
   - 반대 1
   - 검토 2
   - 후보 5
3. 안건별 권고표
4. 제1호 의안 상세
5. 제2호 의안 상세
6. ...
7. 후보 평가 부록
8. 참고 재무/지분/거버넌스
9. 근거 공시 appendix

## 구현 우선순위

1. `proxy_advise` payload에 Word-friendly flat field 추가
2. `decision`/`category`/`facts`의 한국어 label map 확정
3. `Standard` Word template 구현
4. 반대/검토 안건 강조 규칙 추가
5. `Full` variant 확장

## 비채택안 / 기각 사유

- **권고표-only 압축형**
  - OPM 강점인 근거/위험/정책/evidence 구조를 너무 버린다.
- **모든 안건 장문 memo형**
  - 문서가 과도하게 길어지고, 단순 `FOR` 안건까지 과잉 설명이 붙는다.
- **특정 자문사 포맷 복제형**
  - OPM 데이터 구조와 안 맞는 부분이 많고, 제품 정체성도 약해진다.

## 최종 판단

OPM의 `proxy_advise` Word 문서는 외부 자문사 샘플의 “권고표 중심 구조”를 가져오되, OPM이 실제로 잘하는 `안건별 facts/risk/policy/evidence`를 본문으로 승격시키는 방향이 맞다.

따라서 기본 양식은:

- **앞부분은 샘플들처럼 빠르게 읽히는 권고표**
- **중간은 OPM답게 추적 가능한 안건별 판단**
- **뒤쪽은 후보/재무/출처 부록**

이 3층 구조로 고정하는 것을 권고한다.
