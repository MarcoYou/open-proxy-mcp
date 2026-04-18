---
type: analysis
title: shareholder_meeting tool 검증 예시
tags: [release-v2, tool, validation, shareholder-meeting]
date: 2026-04-18
related: [tool-추가-검증-템플릿, tool-추가-검증-정책, DART-KIND-매핑-화이트리스트-2026-04, 주주총회소집공고, 주주총회결과]
---

# shareholder_meeting tool 검증 예시

## 목적

`신규 tool 제안 및 검증 템플릿`을 실제로 어떻게 채우는지 보여주는 예시다.  
이번 예시는 `shareholder_meeting`을 `data tool`로 가정한다.

주의:

- 아래 예시는 `정기주주총회(annual)` 기준으로 채웠다
- `임시주주총회(extraordinary)`는 별도 샘플 검증이 더 필요하다

## A. Tool Proposal

### 1. 기본 정보

- tool name: `shareholder_meeting`
- tool type: `data`
- target user: 스튜어드십 담당자, 액티비스트, 기업지배구조 애널리스트
- analyst use case:
  - 올해 주총 안건이 무엇인지 빠르게 본다
  - 사내/사외이사 후보, 보수한도, 정관변경 안건을 점검한다
  - 주총 결과와 찬성률까지 확인한다

### 2. 질문 정의

- 이 tool이 답하려는 핵심 질문:
  - 이번 주총에서 무엇이 올라왔고 결과가 어땠는가
- 사용자가 기대하는 최종 결과물:
  - 안건, 후보자, 보수한도, 정관변경, 결과, 정정공시, 근거
- 기존 tool과의 차이:
  - `agm_search`, `agm_result`, `agm_agenda_xml` 같은 하위 기능을 하나의 analyst-facing data tool로 묶는다

### 3. 입력

- user-facing company input: 회사명 한글/영문/약칭/티커
- optional filters:
  - `meeting_type=annual|extraordinary`
  - `scope=summary|agenda|board|compensation|aoi_change|results|corrections|evidence`
  - `year`
- expected internal identifiers:
  - `ticker`
  - `corp_code`
  - `rcept_no`
  - 결과 조회 시 `KIND acptno` 변환 가능

### 4. 공시 범위

- disclosure universe:
  - `주주총회소집공고`
  - `[기재정정]주주총회소집공고`
  - `정기주주총회결과`
- target report names:
  - notice 계열: `주주총회소집공고`
  - result 계열: `주주총회결과`
- pblntf_ty:
  - notice: `E`
  - result: `I`
- time window rule:
  - 기본은 선택 연도 내 최신 notice/result
- correction handling rule:
  - 정정 notice가 있으면 최종본 우선

### 5. 소스 정책

- primary source:
  - notice / agenda / board / compensation / aoi_change / corrections:
    - `DART list.json + document.xml`
  - results:
    - `DART list.json`으로 `rcept_no` 확정 후 `KIND HTML`
- secondary source:
  - notice 계열은 `DART-only`
  - results는 whitelist 기반 `KIND`
- uses KIND?: `yes, results only`
- uses Naver?: `no`
- requires whitelist check?: `yes`

### 6. 출력

- key output fields:
  - `meeting_info`
  - `agendas`
  - `board_candidates`
  - `compensation_items`
  - `aoi_changes`
  - `vote_results`
  - `corrections`
- evidence fields:
  - `rcept_no`
  - `source_type`
  - `section`
  - `snippet`
- status fields:
  - `exact / partial / conflict / requires_review`
- requires_review triggers:
  - XML 주요 섹션 누락
  - 회사 식별 ambiguous
  - 정정공시 연결 실패
  - 결과 공시 KIND 매핑 검증 실패

## B. Data Tool Validation

### 1. 범위 확정

- 질문 범위가 명확한가:
  - `주총에서 무엇이 올라왔고 결과가 무엇이었는가`
- 읽는 공시 유형이 명확한가:
  - `주주총회소집공고`, `주주총회결과`
- 같은 회사에 동일 유형 공시가 여러 건일 때 선택 규칙이 있는가:
  - 정정공시는 최종본 우선
  - 결과 공시는 해당 연도 최신 건 우선

### 2. OpenDART 조사

- 공식 API 존재 여부:
  - `list.json` 사용
- list.json 사용 여부:
  - `yes`
- document.xml 사용 여부:
  - `yes`
- 구조화 API가 XML보다 우선하는 필드:
  - 주총 안건/후보자/보수한도는 구조화 API보다 `document.xml`이 중심
- 공식 문서 링크:
  - OpenDART 공시검색
  - OpenDART 공시서류원본파일

### 3. 공시 매핑표

| field | disclosure type | pblntf_ty | primary source | secondary source | external mapping | note |
|---|---|---|---|---|---|---|
| meeting summary | 주주총회소집공고 | E | DART list + XML | 없음 | 없음 | notice 기준 |
| agenda | 주주총회소집공고 | E | DART XML | 없음 | 없음 | agenda parsing |
| board candidates | 주주총회소집공고 | E | DART XML | 없음 | 없음 | personnel section |
| compensation | 주주총회소집공고 | E | DART XML | 없음 | 없음 | 보수한도 |
| aoi change | 주주총회소집공고 | E | DART XML | 없음 | 없음 | 정관변경 |
| corrections | 기재정정 주주총회소집공고 | E | DART list + XML | 없음 | 없음 | 최종본 우선 |
| vote result | 주주총회결과 | I | KIND HTML | DART list | `80 -> 00` | whitelist only |

### 4. 회사 식별

- 한글명 매칭: `삼성전자`, `케이티앤지`, `고려아연` 확인
- 영문명 매칭: 별도 추가 검증 필요
- 약칭 매칭: `KT&G` 계열 추가 확인 필요
- ambiguous 처리 규칙:
  - 유사명 충돌 시 `ambiguous`
- 내부 식별자 체인:
  - `company name -> ticker -> corp_code -> rcept_no -> KIND acptno(결과 공시만)`

### 5. 샘플 검증

| company | report_name | date | rcept_no | primary result | secondary result | final status | note |
|---|---|---|---|---|---|---|---|
| 삼성전자 | [기재정정]주주총회소집공고 | 2026-03-12 | `20260312000987` | DART XML 확보 | 없음 | exact | notice는 DART-only |
| 삼성전자 | 정기주주총회결과 | 2026-03-18 | `20260318801211` | DART list 확보 | KIND `20260318001211` 성공 | exact | results whitelist |
| 케이티앤지 | 주주총회소집공고 | 2026-02-25 | `20260225005779` | DART XML 확보 | 없음 | exact | notice는 DART-only |
| 케이티앤지 | 정기주주총회결과 | 2026-03-26 | `20260326802654` | DART list 확보 | KIND `20260326002654` 성공 | exact | results whitelist |
| 고려아연 | [기재정정]주주총회소집공고 | 2026-03-05 | `20260305001616` | DART XML 확보 | 없음 | exact | notice raw KIND는 false match 위험 |
| 고려아연 | 정기주주총회결과 | 2026-03-25 | `20260325800010` | DART list 확보 | KIND `20260325000010` 성공 | exact | results whitelist |

### 6. evidence 설계

- field-level evidence 지원 여부:
  - `yes`
- evidence 예시:
  - 안건명 -> `rcept_no`, `회의목적사항`, snippet
  - 후보자 경력 -> `rcept_no`, `후보자에 관한 사항`, snippet
  - 주총 결과 -> KIND title/table row, `acptno`
- source_type enum:
  - `dart_list`
  - `dart_xml`
  - `kind_html`
- confidence rule:
  - notice 본문: `dart_xml` 우선
  - vote result: `kind_html` whitelist 검증 통과 시 사용

### 7. requires_review 조건

- source missing:
  - notice XML 없음
- source conflict:
  - notice와 result의 meeting date/agenda count가 비정상적으로 충돌
- ambiguous company:
  - 유사명/동명 충돌
- malformed xml/html:
  - 주요 section parsing 실패
- other:
  - extraordinary meeting인데 정기주총 규칙만으로 찾았을 가능성

## 화이트리스트 체크

`shareholder_meeting` 자체는 부분적으로 화이트리스트를 사용한다.

- notice 계열:
  - `비화이트리스트`
  - 이유: raw KIND false match 발생
- result 계열:
  - `화이트리스트`
  - 규칙: `rcept_no 80 -> acptno 00`
  - 검증: 회사명/제목/날짜 확인

즉 이 tool은 한 도구 안에서도 `DART-only 구간`과 `KIND 허용 구간`이 나뉜다.

## release_v2 판정

### annual

- `go`
- 이유:
  - notice는 DART XML로 안정적
  - result는 KIND 화이트리스트 규칙이 이미 검증됨

### extraordinary

- `conditional`
- 이유:
  - tool 구조는 재사용 가능
  - 하지만 임시주총은 별도 샘플 검증이 더 필요

## 실무 해석

이 예시가 보여주는 핵심은 이거다.

- `shareholder_meeting`은 하나의 data tool이지만
- 내부적으로는 `공시별 소스 정책`이 다르다
- 안건/후보/보수한도는 `DART XML`
- 결과는 `KIND whitelist`

즉 새 tool을 만들 때는 “이 tool은 DART냐 KIND냐”로 묻는 게 아니라,  
`이 tool 안의 어떤 데이터 필드가 어떤 공시와 어떤 소스를 타는가`까지 적어야 한다.
