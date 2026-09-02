---
type: tool
title: director_board
domain: data
scope: [단일 조회]
data_source: [exctvSttus(임원현황), drctrAdtAllMendngSttusGmtsckConfmAmount(주총승인 보수한도), drctrAdtAllMendngSttusMendngPymntamtTyCl(유형별 실지급·1인평균), hmvAuditIndvdlBySttus(개인별 5억+), unrstExctvMendngSttus(미등기임원), empSttus(직원현황), outcmpnyDrctrNdChangeSttus(사외이사 변동현황), 사업보고서 원문(출석률 — attendance)]
related_disclosures: [사업보고서, 기업지배구조보고서, 임원보수-API스펙]
related_concepts: [이사보수, 보수한도, 소진율, 이사회, 사외이사]
created: 2026-07-08
updated: 2026-09-02
---

# director_board — 이사회/개별 이사 프로필

## 한 줄 요약
**개별 이사 단위** 정보 — 이사 인당 보수·보수한도 소진율·임원 재직/사퇴 변동·이사회 출석률(사업보고서 원문·부분적).
`corp_gov_report`가 "회사 15지표 준수 여부"라면, 이 tool은 "누가 얼마 받고, 한도를 얼마나 썼고,
인원이 어떻게 바뀌었나".

## 답하는 질문
1. 이사 **인당 보수가 적절한가** → 인당 보수 수치 (DART가 직접 제공)
2. **보수한도 소진율**은 얼마인가 → 이사류 실지급 ÷ 주총 승인한도
3. **사퇴/인원변동으로 인당보수·소진율이 바뀌었나** → 연도간 임원 diff × 보수 변동 교차

가치판단(과다/적절)은 하지 않는다 — 동종·규모 대비 판단이 필요해, 수치·전년비 변동·flag만 제공.

## 사용법
`director_board(company, scope="summary", year=0, lookback_years=3, format="md")`
- scope: `compensation` · `roster` · `individual` · `unregistered` · `pay_gap` · `pay_agenda` · `attendance`(출석률·on-demand) · `pay_criteria`(보수 산정기준·on-demand) · `summary`(기본)
- year: 기준 사업연도(0=최근 확정 전년). lookback_years: 조회 기간(년)

## scope별 추천 질문 (자연어 예시)

| scope | 이럴 때 물어보세요 |
|---|---|
| `compensation` | "등기이사 인당 보수 얼마야?" · "현대차 이사 보수한도 소진율 얼마야?" · "삼성전자 최근 3년 한도 다 썼어?" · "HD현대중공업 한도초과 이유가 뭐야?"(→ rm 비고로 퇴직금 등 사유 확인) |
| `roster` | "기아 이사회 구성원 누구누구야?" · "현대차 작년에 이사 누가 오고 누가 나갔어?" · "이 회사 사외이사 중에 최대주주랑 관계있는 사람 있어?" |
| `individual` | "삼성전자 대표이사들 각각 얼마 받아?" · "이 회사 등기이사 중에 스톡옵션·RSA 받은 사람 있어?" · "정의선 작년 대비 보수 얼마나 늘었어?" |
| `unregistered` | "현대차 미등기임원 평균 보수 얼마야?" · "등기이사랑 미등기임원 보수 차이 얼마나 나?" |
| `pay_gap` | "이 회사 임원-직원 보수 격차 몇 배야?" · "직원 평균 연봉이랑 이사 보수 비교해줘" |
| `pay_agenda` | "이번 주총에 보수한도 얼마나 올려달래?" · "작년에 한도 다 쓰고 또 올려달라는 거야?"(인상 근거 판단) |
| `attendance` | "이사들 이사회 출석 잘 해?" · "출석률 저조한 이사 있어?"(사업보고서 원문, summary 제외 on-demand) |
| `pay_criteria` | "이 회사 상여는 무슨 기준으로 줘?" · "POSCO 성과급 KPI 가중치 뭐야?" · "대표이사 급여/상여 어떻게 산정했어?"(사업보고서 VIII-2 원문, summary 제외 on-demand) |
| `summary` (기본) | "이 회사 이사회 전반적으로 봐줘" · "이 회사 이사 보수 적절한지 살펴봐줘"(위 전부 종합) |

한 회사에 특정 관점만 궁금하면 scope 하나만 콜(DART 콜 절약), 종합 그림이 필요하면 `summary`.

## scope별 내용

| scope | 내용 | 소스 |
|---|---|---|
| `compensation` | 등기이사 인당보수·보수한도·**소진율** (연도별) | 정형 API |
| `roster` | 임원현황 + **재직/사퇴 감지**(연도 diff) | exctvSttus |
| `individual` | 개인별 **5억+ 실명** 보수 (누가 얼마) | hmvAuditIndvdlBySttus |
| `unregistered` | **미등기 집행임원** 인당보수 (등기 밖 경영진) | unrstExctvMendngSttus |
| `pay_gap` | 경영진 vs **직원 평균** 보수 배수 | empSttus 조합 |
| `pay_agenda` | 주총 보수한도 안건 **올해 제안 vs 작년 실적**(인상률·작년소진율) | shareholder_meeting notice 재사용 |
| `attendance` | 개별 이사 **이사회 출석률**(일부만 요약 시 partial flag, on-demand) | 사업보고서 원문 파서 |
| `pay_criteria` | **보수 산정기준**(버킷별 정책 배수 + 개인별 급여/상여/KPI 분해) + **정형 API 하이브리드 교차검증**(on-demand) | 사업보고서 VIII-2 원문 파서 + hmvAuditIndvdlBySttus |
| `summary` | 위 전부 종합 + 신호 | 전부 |

## 필드 커버리지 (전면 YoY)

이미 API 콜은 하면서 안 쓰던 필드를 전부 캡처·노출한다.

- **roster**: `main_career`(주요경력)·`mxmm_shrholdr_relate`(최대주주관계)·`sexdstn`(성별)·
  `birth_ym`·`tenure_end` 포함.
- **`outcmpnyDrctrNdChangeSttus`**(사외이사 변동현황) — 이사총수·사외이사수·선임/해임/중도퇴임
  **DART 공식 집계**(개별 성명 없음)를 roster diff의 교차검증(sanity check)으로 연동, YoY.
  비교 대상은 `director_type=='사외이사'` **신규선임**만이다 — 전체 임원 변동(미등기 포함)을
  공식 사외이사 집계와 견주면 규모 자체가 안 맞는다. 남는 격차는 재선임 미검출로 설명되므로
  cross_check는 **직접 비교가 아니라 참고**다.
- **compensation**: 승인한도(limit) 쪽 `rm`도 캡처(실지급 쪽뿐 아니라).
- **individual·unregistered·pay_gap**: 전부 `lookback_years` 지원(YoY) — `per_year: [...]` 구조.
- **pay_gap**: `employee_breakdown`(부문·성별 원본 — 정규직/계약직/합계/평균근속연수/1인평균급여)
  노출. **`is_total` 플래그가 필수**다 — 부문상세(DX/DS, 급여 공백)와 '성별합계' 총계행이 한 응답에
  같이 오는 회사가 있어, 구분 없이 전부 합산하면 실제 인원의 2배가 된다. `is_total=true` 행만 합산
  대상으로 표시한다.
- 개인별(5억+) `breakdown_note`(RSA·스톡옵션 등 미확정 주식보상) 렌더. **그룹 집계
  `stk_bsd_pd_mendng_totamt`는 전 표본 공백이지만 개인별 텍스트에는 16.3%가 실제 내용을 담는다** —
  그룹 집계 하나만 보고 "스톡옵션 없음"이라 단정할 수 없다.

## 신규 계산 로직

### 소진율
```
소진율(%) = (감사 단독 제외한 이사류 실지급 합) ÷ 이사 주총 승인한도 × 100
```
- 실지급은 유형별 여러 행(등기이사(사외·감사위 제외)/사외이사/감사위원 등). **감사위원회 위원은
  이사 한도 안**(등기이사이므로) — IR 확증: 현대차 한도 12명 = 실지급 버킷 5+2+5 헤드카운트와 정확히
  일치. 순수 '감사'(비위원회)만 별도 한도.
- **한도 공백** — 새 주총 결의 없는 해엔 `gmtsck_confm_amount="-"` → 최근 유효연도 한도로 lookback,
  `limit_source`에 명시.
- **한도 행은 합산한다.** 이사 한도를 상임/비상임 또는 등기/사외로 **분리 공시**하는 회사가 있어
  (한국전력·기업은행·강원랜드 등), 마지막 행으로 덮으면 한도가 5~8배 축소돼 소진율이 허위로 폭증한다.
  단 '계'/'합계' 총계행이 있으면 중복방지로 그 행만 채택한다.
- **한도/실지급의 감사 판정 기준은 서로 다르다.** 한도표에서 감사 전용 행은 "이사" 문자열 완전 부재로
  판정한다("감사위원회 위원 또는 감사" 같은 결합 표기가 "위원" 때문에 이사 한도로 오분류되던 자리).
  반면 **실지급 표의 "감사위원회 위원"(순수 표기, 결합 아님)은 이사 한도 몫**이다 — 기준을 통일하면
  위 헤드카운트 정합이 깨진다.
- **한도가 1억 미만이면 파싱 실패로 보고 한도 미상 처리**한다(lookback이 유효연도로 채움). 승인한도
  원문이 각주 마커 `(*1)`인 회사에서 spurious 소액이 분모가 되면 소진율이 수만 %로 튄다.
- **소진율 >100%는 버그가 아니라 실제 신호다.** 원인은 하나로 뭉뚱그릴 수 없고 네 갈래가 섞여 있다 —
  ① 명시적 1회성(rm에 퇴임이사 퇴직금·중도사임 명기) ② 회사가 한도를 스스로 낮추며 초과
  ③ 만성적 초과(전년도부터 이미 100% 근접·초과) ④ 순수 성과급 급등. 스톡옵션 행사이익 혼입은
  `stk_opt`/`stk_bsd` 필드 공백으로 기각된 가설이다. `utilization_flag="exceeded_limit"`로 표시하고
  (90%+ "high"와 구분) 해석은 사용 맥락(스튜어드십 engagement)에 맡긴다.

### 인당 보수
`psn1_avrg_pymntamt`(1인평균)이 API에서 이미 계산되어 옴 — 검증 결과 실지급÷인원과 정합.

### 공시 비고(rm) 원문 노출
`drctrAdtAllMendngSttusMendngPymntamtTyCl`의 `rm`(비고) 필드를 **raw로 그대로** 노출한다
(`compensation` scope `by_type[].note`). 전수 스캔 기준 17.3%에 내용이 있고 평균 24.9자·최대 187자로
짧다 — 회사마다 날짜 표기가 "선임('25.03.20)"/"2025년 03월 25일"/"2025.3.26 부"로 제각각이라
**정규식으로 구조화(이사명·날짜 추출)하지 않고 원문 그대로** 보여주는 것이 맞다(구조화 파서는
깨지기 쉽다). 내용 예: 퇴직금·중도사임 등 1회성 사유(HMM "퇴임한 사내이사의 퇴직소득(7억원) 포함"
→ 한도초과 127.6%의 원인을 tool이 자동으로 보여줌), 이사 성명+정확한 선임/사임 날짜까지 담긴 경우도
있음. 주식기준보상 필드(`stk_bsd_pd_mendng_totamt`)는 전 표본 공백이라 별도 스콥 가치 없음.

### 재직/사퇴 감지
`exctvSttus` 연도간 diff. 동일인 판정은 **2-pass 매칭**이다 — Pass1 이름 정확 일치로 잔류 확정 →
Pass2 나머지만 `birth_ym` 매칭(그 값이 남은 후보군에서 유일할 때만 인정).

- 단순 OR 매칭이면 이탈자와 **이름이 전혀 다른 잔류자**의 `birth_ym`이 우연히 같을 때(둘 다
  "1959년 06월") 이탈이 통째로 누락된다. `birth_ym`은 연·월만 있어 정밀도가 낮다.
- 복합키(이름 AND 생년월)면 원문 `birth_ym` 오타(`1972.12` vs `1972.02`)가 이탈+신규 이중 오탐이 된다.
- 로마자 표기 변동(이름 다름·생년월 같음)은 Pass2가 잡는다(José Muñoz "Jose Munoz"↔"호세무뇨스").
- 변동은 `director_type`으로 **등기 이사회 ↔ 미등기 집행임원을 분리**한다. exctvSttus에 이사회만 적는
  회사와 전 집행임원을 적는 회사가 섞여 있어, 섞으면 대형사 '이탈 16명'이 대부분 미등기 상무가 된다.
  종합신호의 이탈은 이사회 기준이다.
- 스냅샷이라 이탈 "사유"(사퇴·임기만료·해임)는 미확정 — 별도 수시공시로 확인 필요.
- `birth_ym` 결측 시 이름 단독 매칭으로 fallback → 로마자 오탐 재발 가능(관측 표본 결측 0%였으나
  보장 안 됨).

### 경영진-직원 보수 격차 (pay_gap)
```
격차 배수 = 등기이사(사외·감사위 제외) 인당보수 ÷ 직원 전체 가중평균 급여
직원 가중평균 = Σ(부문·성별 행 연급여총액) ÷ Σ(정규+계약 인원)   (합계행 중복 제외)
```
현대차 실측(2024): 등기이사 32.0억 ÷ 직원 1.24억 ≈ **25.8배**. 배수 자체가 과다/적정 판단은 아님
(업종·직군 구성 차이) — 비교 신호로만.

**인원수는 `sm`(공시 합계 필드)을 1순위로 신뢰한다.** `empSttus`의 정규·계약 인원에 오타가 있는 회사가
있고(`rgllbr_co=981`인데 `sm=98`), 정규+계약을 그대로 합산하면 총원이 10배 부풀려져 배수가 왜곡된다.

`empSttus` '성별합계'만 총액을 담는 서식은 상세행 공백 시 합계행으로 폴백한다 — `"합계" in se`
부분매칭은 '성별합계'까지 버리므로 쓰지 않는다.

### 보수한도 주총안건 비교 (pay_agenda)
`shareholder_meeting` 소집공고 보수한도 안건은 **하나의 공고에 current(올해 제안)·prior(작년 한도+
실지급)**를 모두 담는다. 이를 재사용해:
```
인상률 = (올해 제안 한도 − 작년 한도) ÷ 작년 한도
작년 소진율 = 작년 실지급 ÷ 작년 한도
```
현대차 실측: 제안 284억 / 작년 237억(인상 +19.8%) / 작년 실지급 236.9억(**작년 소진율 100%**) →
"한도 거의 소진 + 인상 요구 = 인상 근거 있음" 신호. **주의**: 이 '작년 소진율'은 주총공고 prior 컬럼
기준이라, `compensation` scope의 사업보고서 기반 소진율과 연도 기준·집계 정의가 다를 수 있음(둘 다
사실, 출처 다름). 찬반 판단 자체는 하지 않음(→ [[proxy_advise_before_meeting]]).

**주의**: pay_agenda는 `year` 파라미터를 무시하고 항상 **최근 주총 소집공고**를 본다(주총은 연 1회라
'올해 안건' 자체가 최신). 소집공고 안건 파싱에 실패하면 **compensation 표 승인한도 YoY로 폴백**하고
출처를 밝힌다.

**`no_agenda`는 대개 정상 폴백이다** — 보수한도 안건 자체가 없거나(그 해 이사선임만), "규정 신설"류
정관변경이라 금액이 없거나, 승인안건이 아닌 후보자 참고표인 경우다. `client.py`의 IMAGE_NOTICE
감지는 파일명에 "소집"·"통지"가 있으면 **본문 텍스트 유무와 무관하게** 경고를 찍는 느슨한 휴리스틱이라
(본문이 38,764자여도 뜬다) **파싱 실패의 근거가 아니다**. 진짜 데이터 손실은 **정정신고의 before/after
표 구조**를 안건 파서가 표준 소집공고 형식으로 인식하지 못하는 경우다(정정신고 파싱 TODO).

## 알려진 issue + TODO
- **attendance 부분성**: 회사가 개별 출석률을 '(출석률:%)' 인라인으로 요약하는 범위가 제각각 —
  **일부(주로 사외이사)만** 그 형식으로 쓰고 나머지는 회차별 출석표에만 있는 회사가 많다(기아 4/9명).
  parsed 인원<이사회 인원이면 `attendance_partial` flag. 전체 이사회 출석을 다 잡으려면 회차별
  매트릭스 파싱이 필요(TODO). 표 자체가 없는 회사(셀트리온·알테오젠 등)는 `not_found`.
  겸직(표5-2-1)·선임변동은 미구현.
- **birth_ym 결측 시** 보조키로 `hffc_pd`(재직시작일) 검토 — TODO.
- **개인별 보수 5억 미만 비공개**: `hmvAuditIndvdlBySttus`는 상위 일부만(범주 평균은 전원).
- **지연 제출 rcept**: 통상 3월 기한보다 늦은 rcept_no(예 미래에셋 2024=8월)는 정정공시 가능성 —
  버킷 내부 정합 확인 권장.
- **미해결 각주 마커**: 승인한도 rm·개인별 breakdown에 원문 각주 마커(`(*1)`·`주1)`)가 그대로
  남는 회사 있음(원문 각주 본문과 분리 제공) — 마커만 노출돼 의미 없는 경우 있음. 원문 각주 연결 TODO.
- **지주사 pay_gap 비교**: 금융지주·순수지주 격차배수는 지주사 본사 인력(신한 195명·KB 144명) 기준이라
  사업회사(삼성전자 54배)와 직접 비교 부적절 — 값은 정확하나 회사간 비교 시 주의(캡션 보강 TODO).
- **비고 조정치 병기 미구현**: 비고에 "행사이익 제외 시 X억"이 있으면 헤드라인 옆에 병기하는 것이
  이상적이나 자유텍스트 파싱이 fragile해 보류(원문 비고로 이미 답은 노출됨). 소진율/격차배수
  peer 밴드도 미구현.

## 렌더·정합 규칙

- **개행이 표를 깬다** — 원문 개행이 남는 필드는 전부 `_clean_text_or_none`으로 정규화한다
  (개행→`" / "`, `"-"`→None). 대상: `main_career`·`largest_shareholder_relation`·`duty`(담당업무)·
  `tenure`(재직기간)·`position`(직위, roster+individual 양쪽)·`breakdown_note`·
  `avrg_cnwk_sdytrn`(평균근속). `.strip()`은 양끝만 지우므로 셀 내부 개행에는 무력하다.
- **DART 013(조회된 데이터 없음)은 오류가 아니다.** `client._request`는 status≠000이면 재시도 후
  항상 예외를 던지므로, 응답 dict의 `status`를 보는 방어는 정상 응답에서만 도달하는 죽은 코드다.
  013은 "이 회사는 해당 항목이 없음"(신규상장사에 미등기임원·5억+개인·사외이사 변동이 없을 때)이라는
  흔한 정상 케이스라 `_fetch_rows`가 `DartClientError`를 잡아 **013만 빈 리스트로 흡수**한다
  (그 외 예외는 그대로 전파 — 진짜 오류를 숨기지 않는다).
- **5억+ 대상자 0명인 해에 DART는 `nm='-'` placeholder 행을 반환한다** — 실명·금액이 둘 다 없으면
  사람으로 세지 않고 '(0명)'으로 정직 표기한다.
- **렌더에서 `None`은 `'-'`로**(dict 키가 항상 존재하고 값만 None이라 `.get(k,'-')` 기본값이 발동하지
  않는다). `0`은 유효값이라 살린다.

## 병렬 구조

단일 회사 조회는 3단계로 병렬화한다. 배치 census 스크립트는 하드룰상 동시성 1~2 + sleep이 필요하지만,
**프로덕션 tool의 단일 회사 조회는 앱 레벨 rate limiter(900/min)가 보호**하므로 순차로 갈 이유가 없다.

1. **scope 간**: compensation·roster·individual·unregistered·pay_gap·pay_agenda 6개는 서로 데이터를
   안 쓴다 — `asyncio.gather`로 동시 실행(pay_gap의 comp_data 재사용은 병렬화를 위해 포기, 최신연도
   1콜 절약 최적화였을 뿐).
2. **scope 내부**: 같은 연도의 서로 다른 API(한도+실지급, 직원현황+실지급)도 병렬.
3. **연도 루프**: `lookback_years`만큼의 연도별 조회가 전부 독립 — 한 번에 병렬 fetch. 단
   compensation의 "직전 유효 한도 캐리포워드"처럼 순서에 의존하는 순수 계산 로직은 fetch 완료 후
   최신→과거 순으로 정렬해 순차 처리(정확성 유지).

실측: 현대차 `lookback_years=3` 기준 11.9초 → 3.0초.

## 파싱품질 플래그 + raw_text 폴백 설계

**성능 타이머(코드 상시)**: `build_director_board_payload` 가 scope별·전체 소요를 `data["timing"]`
(`per_scope_ms`·`total_wall_ms`·`scope_sum_ms`)에 기록한다(`time.perf_counter`, 단조 카운터).
성능 회귀는 이 필드로 바로 잰다. `pay_criteria` 는 병목이 원문 fetch(I/O)인지 parse(CPU)인지
가리기 위해 `data.pay_criteria.timing_detail`(`status_probe`/`fetch_gather`/`parse`/`reconcile` ms)
을 따로 낸다 — 지배하는 쪽은 8~14MB 원문 fetch 이고, 캐시 히트 시에는 parse 다.

**의심 신호는 전역 단일 플래그가 아니라 scope별 `data_quality_flags` 배열이다** — 신호가 이질적이라
하나로 뭉치면 실제값을 오탐한다:

| kind | severity | 의미 | raw_text |
|---|---|---|---|
| `limit_unreliable` | warn | 승인한도가 각주 마커라 금액 미파싱 → 소진율 산출 제외(lookback) | — |
| `footnote_marker_unresolved` | info | 비고·breakdown 이 각주 마커뿐 | 마커 자체 |
| `utilization_exceeds_limit` | info | 소진율>100% — **파싱오류 아님**(퇴직금·성과급·스톡옵션) | — |
| `parse_failed` (pay_agenda) | info/warn | 주총안건 미파싱 | (원문 스콥) |
| `crosscheck_mismatch` (roster) | warn | 이름 diff 와 공식 집계의 격차가 커 diff 신뢰도 낮음 | — |

핵심: **소진율이 1000%를 넘어도 실제값일 수 있다**(회장 보수·스톡옵션 행사이익) — 파싱 플래그로
잡으면 오탐이라 `info` 로 「파싱오류 아님」만 명시한다. warn(신뢰도 낮춤)과 info(참고)를 갈라야
소비자가 선택 대응할 수 있다.

**`raw_text` 폴백은 원문 파싱 스콥에서만 유효하다.**
- **정형 API 스콥**(compensation·individual·unregistered 등): 필드 자체가 이미 raw 이고 각주의
  **본문은 API 응답에 아예 없다**(사업보고서 원문에만 있다) → `raw_text` 가 `"(주1)"` 이라 복구
  불가. 여기선 `footnote_marker_unresolved` 플래그(마커를 참고로 첨부) + 렌더에서 무의미 마커 라인
  억제가 정답이다.
- **원문 파싱 스콥**(pay_agenda notice·attendance): 구조화 파싱이 실패하면 원문 블록을 그대로 실어
  LLM 이 직접 읽게 하는 것이 맞다. `data_quality_flags` 항목에 `raw_text` 필드를 둔 이유다.

**렌더 반영**: 각주 마커뿐인 비고 라인은 억제하고 `## 데이터 품질 참고` 절에 warn↑/info↓로 표시한다.
machine-readable `data_quality_flags` 는 payload 에 항상 포함된다(에이전트 프로그램 소비).

### 각주 원문 해소 (document.xml 폴백)

정형 API가 각주 **본문**을 안 주는 문제(위 `footnote_marker_unresolved`)를, **그 공시 원문에서 복구**한다.
DART 공시뷰어 URL의 `rcpNo`(=접수번호)로 `document.xml`(공시서류원본)을 받아 각주를 읽어오는 방식.

- **트리거**: 마커 플래그가 있을 때만 발동(`resolve_footnotes=True` 기본). 마커 뜬 공시(rcept_no)만
  `get_document_cached`로 1회씩 fetch·캐시 — 평소엔 빠른 API, 구멍 난 데만 원문. 앱레벨 limiter 보호.
- **section-local 필수**: 같은 `주1)`이 임원보수·재무각주·종속기업에 **따로 존재·의미 다름**(크래프톤 실측)
  → 전체 원문이 아니라 **섹션 앵커**(`주주총회 승인금액`·`개인별 보수지급 금액`·`미등기 임원`) 뒤 window
  안에서만 그 표의 각주를 찾는다. 전역 마커번호 매칭은 틀림.
- **문장 종결 필수(정밀도 우선)**: 진짜 각주 정의는 문장(~습니다/~함)으로 끝난다. 마커가 표 컬럼이라
  옆 행을 긁는 오탐은 종결어미 없음으로 걸러 **raw 발췌 폴백**으로 보낸다 — 틀린 각주를 지어내느니
  원문 발췌를 그대로 노출한다.
- **`get_document`의 본문 선택**(client.py): 사업보고서 원문 ZIP은 본문(`{rcpNo}.xml`, 8MB)+첨부
  (`{rcpNo}_NNNNN.xml`) 구조라, `xml_files[0]`을 집으면 첨부(575KB)를 읽어 임원보수·각주를 통째
  놓친다 → 본문 우선 선택(proxy·dividend 등 원문 파서 공통).
- **정밀도 게이트**: 추출한 문구가 정말 그 표의 각주인지 확인하는 게이트를 통과하지 못하면 원문
  발췌로 강등한다 — ① 슬롯 유형 적합성(승인한도 자리에 소송충당부채·스톡옵션 각주가 오면 거부)
  ② 인물 지목(개인별은 본문에 그 사람이 없으면 강등) ③ 문장 완결성 ④ 표 조각 배제 ⑤ 중복 제거.
  **틀린 각주 하나가 원문 발췌 열보다 해롭다** — 게이트 없이 "뭔가 추출"을 "복구 성공"으로 세면
  엉뚱한 각주를 자신 있게 노출하고 정밀도는 과대평가된다.

## attendance (이사회 출석률) 원문 파서 + 품질 로그

출석률은 **지배구조보고서/PDF가 아니라 사업보고서 원문**의 '이사회 활동내역'에
`한애라 (출석률 :100%)`·`박성하(출석률:50%)` 형식으로 있다(OCR 불필요 — 텍스트 파싱으로 된다).
exctvSttus의 rcept_no로 사업보고서 원문(각주 해소와 캐시 공유)을 받아 파싱한다.

- **section-local**: 출석률 표가 여러 개(이사회·감사위·보상위)라 같은 이름이 body마다 값이 다르다
  (SK하이닉스 안현 이사회 91% vs 위원회 100%). **첫 클러스터(이사회 본 표)만** 잡는다.
- **부분성 정직 처리(핵심)**: parsed<이사회 인원이면 `attendance_partial` flag(같은 exctvSttus 행에서
  이사회 인원 직접 카운트 → roster 의존 없음). 표 없으면 `not_found`.
- **summary 제외·on-demand**: 원문 fetch(8MB, 금융지주 최대 10초)라 흔한 summary를 느리게 한다 →
  `scope="attendance"`로만 조회.
- 출석률 <75%는 `low_attendance` warn flag.
- **소수점 출석률을 읽는다** — `(\d+(?:\.\d+)?)` + float 파싱. `(\d+)`만 잡으면
  `서창석 (출석률 : 87.5%)`에서 매치가 실패해 **해당 이사가 통째로 누락**된다(정수는 int 유지).

**구조화 품질 로그**: 매 호출 끝에 `logger.info("[DB_QUALITY] {회사} scope={s} wall={ms} calls={n}
flags={kind별개수} fn={복구}/{전체} warns={n}")` 1줄. 실전 트래픽에서 `parse_failed`·각주 복구율 하락·
`attendance_partial/not_found` 비율·특정 scope 지연을 로그로 관측 → 사용자 불평 전에 엣지케이스 발견.
**완벽 파싱 대신 "정직한 self-flag + 로그 관측"** 전략의 계측 지점.

## pay_criteria — 보수 산정기준 원문 파서 + 정형 API 하이브리드 교차검증

정형 API(compensation/individual)는 보수 **금액·인원**만 준다 — "얼마 받았나"는 알아도 "무슨 기준으로
산정했나"(성과급 배수·KPI 가중치)는 못 준다. `pay_criteria`는 사업보고서 **VIII. 임원 및 직원 등에
관한 사항 › 2. 임원의 보수 등** 원문(document.xml)에서 이 서술을 구조화한다. 파서 코어는
`services/executive_pay.py`.

**무엇을 뽑나**
- **보수지급기준(정책)**: 등기이사/사외이사/감사위원 버킷별 급여·상여·단기/장기성과급 배수(KT&G 단기
  0~280%(사장)/0~165%(사내), 장기 0~600%, RSU 3년 이연 등).
- **개인별 산정기준**: 실명 임원의 급여/상여/주식보상 분해 + 계량·비계량 KPI(POSCO 영업이익 15%·매출
  15%·ROA 10%·주가 15%·ESG 10% 등 가중치 실명 공개). 배수/비율 토큰은 `ranges`에 원문 그대로 보존.

**구조-우선 파싱(제목·순서 매칭 금지, 4축)**: ① `<...>` 꺾쇠 그룹제목 → 대블록 경계 ② 표 헤더 컬럼
시그니처(정규화 부분매칭) → 표 종류 ③ rowspan/colspan 그리드 정규화 → 좌표 기반 셀 접근 ④ 표별
`(단위:백만원)` 선언 → 스케일. 시그니처 불일치 표는 버린다(억지 매칭 금지).

### 하이브리드 검증 — 파서 Σ컴포넌트 vs 정형 API 공식 총액 (핵심 설계)
개인별 산정기준 표의 Σ(급여+상여+…)를 **두 축**으로 교차검증한다:
1. **자기일치(파서-vs-파서)**: 같은 원문 안의 「개인별 보수지급금액」 표 공식총액과 대조(DART 0콜).
2. **하이브리드(파서-vs-API)**: 정형 API `hmvAuditIndvdlBySttus`(5억+ 개인별 보수총액, DART가 별도
   구조화한 **독립 소스**)와 대조. `reconcile_with_api()`가 individuals에 `api_consistent`/`api_diff_krw`
   부여 + `api_unmatched`(API엔 5억+로 있는데 파서 개인에 대응 없음 = 이름 병합/누락 신호) 반환.

**왜 하이브리드가 필수인가**: 자기일치는 파서가 같은 원문 두 표를 **같은 방식으로 오독하면 통과**한다
(파서-vs-파서라 문서 오독을 못 잡음). API 는 파서 그리드를 안 거친 독립 축이라 이 silent 오독을
적발한다. 이 축이 아니면 못 잡을 결함이 두 갈래 있다 — ① 산정기준 표가 이름 셀에 직위를 병합
(`대표이사한종희`)해 공식표와 키가 어긋나는 것(자기일치가 `0/0` 으로 **조용히 빈다**) ② `(단위:)`
선언 없이 「6억7백만원」처럼 한글 수사로 자기서술한 금액(10배 축소). 잔여 불일치는
`total_consistent=False` 로 투명하게 플래그한다(은폐 안 함).

### async — 하이브리드 검증의 wall-clock 비용 0
`get_individual_pay`(API)는 rcept_no가 필요 없어 8MB 원문 `get_document_cached`와 **병렬**로 돌린다
(`asyncio.gather`). 같은 회사 cold 대조(캐시 무효화 apples-to-apples): 순차(document→API) 대비 병렬이
회사당 ~130ms 절감 = API 지연 전액이 원문 다운로드 그늘에 흡수. 검증 축을 붙여도 속도 비용 0.

### 미포함(보수적)
- **정책/개인별 커버리지 부분성**: 정책표 없이 서술형만 있는 회사는 `policy_narrative` 폴백, 그마저
  없으면 개인별만. 금융지주 지배구조 연차보고서 등 별도양식은 `status=not_found`.
- **상위5명(미등기·직원) 블록**은 이사회 명단과 다름(합산 금지). 이들은 5억+ 개인공개(등기·감사)
  대상 밖이라 API에 없을 수 있어 `parser_unmatched_ge5`(정상 가능)로 별도 표식.
- **이중공시 scope 처리**: 같은 사람이 「이사·감사 개인별」과 「상위 5명 개인별」 **두 법정표에 다른
  금액**으로 동시 공시될 수 있다(제일기획 김태해: 이사·감사 956M=등기 자격분 vs 상위5명 1,286M=연간
  총액분, **둘 다 정확**). API(hmvAuditIndvdlBySttus)는 이사·감사 scope라, 상위5명 블록 금액을 API와
  대조하면 scope 불일치 false positive가 난다 → `reconcile_with_api`는 상위5명 블록 개인을 API
  대조에서 제외(`_is_top5_group`).
- 남은 자기일치 잔여는 전부 `total_consistent=False`로 투명 플래그(은폐 안 함).

## roster — 현재 명단은 가장 최신 정기보고서로

`_roster_scope`가 사업보고서(11011)만 보면 주총 성수기인 2~3월에 FY(N-2) = **15개월 묵은 명단**을
「현재 이사회」로 내놓고, 해당 사업연도 보고서가 아예 없으면 **명단이 빈 채로** 나간다.

최신 연도만 **신선한 것부터 사다리**로 채운다 — 사업보고서 → 3분기 → 반기 → 1분기.
분기·반기보고서에도 임원현황이 실린다(실측 10사×4종 **100%**, 등기구분·재직기간도 100%).
다만 분·반기는 **기재 생략이 허용되는 항목**이라 소형사에서 빌 수 있어 단일 소스가 아니라 사다리다.
과거 연도는 diff 기준선이라 사업보고서로 고정한다 — 스냅샷 기준일을 섞으면 YoY 비교가 어긋난다.

`roster.roster_as_of` 로 **어느 보고서 기준인지** 산출물에 밝히고, 사업보고서가 아니면 경고를 붙인다.
실측: LG화학 `year=2026` → 「2026년 1분기보고서」 기준 121명.

### 직전 사업보고서 이후 변동 (`changes_since_last_annual`)

사업보고서끼리만 비교하면 **기중 변동이 다음 사업보고서까지 안 보인다** — 6월에 사임한 이사는
1년 뒤에야 드러난다. 분기·반기 명단을 직전 사업보고서와 대조해 그 사이 들고 난 사람을 따로 낸다.
실측(삼성전자 2025 사업보고서 → 2026 1분기): 김용관 신규 사내이사 · 송재혁·유명희 이탈 —
**2026년 3월 주총 변동**이 잡힌다. 연간 diff 로는 2027년 3월까지 안 보였을 것이다.

2-pass 동일인 판정(이름 → 남은 후보군 안에서 유일한 생년월)은 `_diff_roster_rows` 로 추출해
연간 diff 와 **같은 로직을 공유**한다. 그리고 연간 diff 와 같은 기준으로 **이사회(등기)만** 싣는다 —
집행임원을 섞으면 상무·담당·명예회장이 잔뜩 나온다. 집행임원은
`executive_changes_since_last_annual_count` 로 건수만 요약한다.

## Flow

```mermaid
sequenceDiagram
    participant U as User
    participant T as director_board
    participant R as resolve_company
    participant E as DART exctvSttus (임원현황)
    participant A as DART 보수 3종 (한도·유형별·개인별)
    participant EM as DART empSttus (직원)
    participant X as DART 사업보고서 document.xml
    U->>T: company, scope
    T->>R: 회사 식별 → corp_code
    par 병렬
        T->>E: 임원현황 (이사/감사 재직·경력·임기)
        T->>A: 보수한도/유형별/개인별 5억+
        T->>EM: 직원 부문·급여 (pay_gap 분모)
    end
    opt 각주 미해소 / attendance 필요
        T->>X: 사업보고서 원문 (rm 각주·이사회 출석률 파싱)
    end
    T->>T: 전면 YoY + 소진율·인당보수·pay_gap·pay_agenda 계산
    Note over T: 파싱품질 플래그 · 실패 시 raw_text fallback
    T-->>U: ToolEnvelope (이사회/개별이사 프로필)
```

## Data sources (회사당 ~5 DART 콜, per-firm)
| 소스 | 무엇 |
|---|---|
| `exctvSttus` | 성명·직위·이사구분·상근·담당업무·재직기간·임기만료 |
| `drctrAdtAllMendngSttusGmtsckConfmAmount` | 주총 승인 보수한도 + 인원 |
| `drctrAdtAllMendngSttusMendngPymntamtTyCl` | 유형별 실지급 총액·인원·1인평균 |
| `hmvAuditIndvdlBySttus` | 개인별 보수(5억+) |
| `unrstExctvMendngSttus` | 미등기 집행임원 인원·연급여총액·1인평균 |
| `empSttus` | 직원 부문·성별 인원·평균급여 (pay_gap 분모) |
| shareholder_meeting notice (재사용) | 보수한도 주총안건 current/prior (pay_agenda) |
| 사업보고서 원문 (document.xml) | 각주 본문 해소 + 이사회 출석률(attendance) + 보수 산정기준 VIII-2(pay_criteria) |
| `hmvAuditIndvdlBySttus` (재사용) | pay_criteria 하이브리드 교차검증(파서 Σ vs API 공식총액) |

## 변경 이력
- 2026-08-06: 검증 census·발견 경위 서술을 private storage 로 이관(경계 규칙 [[wiki_schema]] 0.0).
- 2026-07-30: roster 현재 명단을 최신 정기보고서 사다리로 + `changes_since_last_annual` 신설.
- 2026-07-13: `pay_criteria` scope 신설(원문 VIII-2 파서 + API 하이브리드 교차검증) ·
  소수점 출석률 파싱 교정.
- 2026-07-09: 전면 YoY + 미사용 필드 전량 노출 · `outcmpnyDrctrNdChangeSttus` 교차검증 연동 ·
  `attendance` scope 신설 · 013 흡수 · 3단계 병렬화 · 렌더 정합 6건 교정.
- 2026-07-08: tool 신설(compensation·roster·individual·unregistered·pay_gap·pay_agenda·summary) ·
  한도 행 합산 · rm 비고 원문 노출.

## 관련
- [[corp_gov_report]] — 회사 지배구조 15지표 준수(정성). 이 tool은 개별 이사 정량.
- [[director_evaluation]] — 이사 후보 독립성·결격(주총 안건). 이 tool은 재직 중 보수·재직변동.
- [[shareholder_meeting_notice]] — 보수한도 '안건'. 이 tool은 실제 지급·소진율.
- `pay_criteria` 파서 설계·하이브리드 검증 회고: private storage `wiki-private/lessons/`.
