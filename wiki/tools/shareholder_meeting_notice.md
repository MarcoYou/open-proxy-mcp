---
type: tool
title: shareholder_meeting_notice
description: 주주총회 소집공고 (사전) — DART API/XML 기반
related: [shareholder_meeting_results, proxy_advise_before_meeting, ownership_structure, proxy_contest, evidence]
---

# shareholder_meeting_notice

주총 **소집공고** 공시 데이터 (사전 — DART API/XML). 빠르고 안정 (0.5-1.5s). 2026-05-24부터 summary 기본 응답은 경량화하고, stage별 `timings_ms`를 노출한다. 2026-05-25 KOSPI300 재검증에서 정기 소집공고가 현재 DART에 없는 2건을 제외하고 `requires_review` 0을 확인했다.

## 분리 배경 (2026-05-04)

기존 `shareholder_meeting` tool은 DART API + KIND scraping 두 source를 한 데에 묶었음:
- **notice scopes** (DART, 0.5-1.5s, 안정적): summary/agenda/board/compensation/aoi_change
- **results scope** (KIND, 4.9s, fragile): 결과 의결 결과

→ proxy_advise_before / proxy_result_after 분리 패턴과 consistency. KIND fragile 부분 격리. Claude.ai 동적 tool loading 부담 감소.

## scope (5, 260506 정리)

| scope | 데이터 | 시간 |
|---|---|---|
| `summary` (default) | 메타 + 정정공시 cover + **안건 hierarchy (number+title+children)** + **1호 안건 메타 (회기/사업연도/배당 예정액)**. 긴 전자투표/온라인중계 안내문은 기본 제외. | 0.5s |
| `board` | 이사·감사 후보 + 경력 (raw) | 0.5s |
| `compensation` | 보수한도 안건 + 소진율 | 0.5s |
| `aoi_change` | 정관변경 (변경 전/후/사유) **+ 퇴직금 변경 raw** (260505 통합) | 0.5s |
| `prov_financials` (NEW 260506) | 잠정 재무제표 4 quadrant raw — consolidated/separate × balance_sheet/income_statement + flat metrics | 0.5s |

### 폐지된 scope (260506)

- `agenda` — summary에 hierarchy 통합 (silent fallback to summary)
- `full` — 병렬 wrapper, 거의 사용 X. 종합 분석은 `proxy_advise_before_meeting` 호출 (silent fallback to summary)

### 제거된 필드 (시점 분리)

- `result_status` / `result_reference` — 사후 정보, `shareholder_meeting_results` tool 참조

## source

- DART OpenAPI `list.json` 검색 + 상세 (`fnlttSinglAcnt` 등 X — XML 직접 파싱)
- DART XML 본문 (rcept_no → viewer_url)
- 정정공시 자동 선택 (rcept_no rank — 최신 정정 우선)

## 성능/디버깅 옵션 (2026-05-24)

| 옵션/필드 | 의미 |
|---|---|
| `include_coverage=false` (default) | 명시적 `annual`/`extraordinary` 조회에서 최근 12개월 정기/임시 coverage 재검색을 생략. 정기/임시 판별은 선택된 소집공고 본문으로 계속 수행. |
| `include_coverage=true` | `meeting_coverage_12m`를 추가 계산. 최근 정기/임시 주총 존재 여부가 필요한 경우에만 사용. |
| `rcept_no` | 이미 소집공고 접수번호를 알면 회사 식별/후보 검색을 건너뛰고 해당 원문을 직접 파싱. 리포트 재현과 timeout fallback에 유용. |
| `fiscal_month` | `annual` + `year` 조회에서 OpenDART `company.json.acc_mt` 결산월을 읽어 정기주총 후보 window를 먼저 좁힘. fiscal window에서는 최신 후보 1건만 먼저 열고, 정기 매칭 실패 시 나머지 후보와 full-year 검색으로 fallback. |
| `data.timings_ms` | `resolve_company`, `fiscal_month_lookup`, `select_notice_candidate`, `select_notice_candidate.search_filings`, `select_notice_candidate.fetch_top_documents`, `select_notice_candidate.parse_top_documents`, `select_notice_candidate.filter_meeting_window`, `select_notice_candidate.build_candidate`, `select_notice_candidate.full_year_fallback`, `coverage_search`, `load_notice_bundle`, `total` 등 stage별 소요 시간(ms). 병목 원인 확인용. |

## 파싱 정확도 / relation metadata (2026-05-25)

- agenda node는 `proposer_type`, `agenda_relation_type`, `agenda_relation_reasons`를 포함한다.
- `agenda_relation_type`: `normal`, `procedural`, `conditional`, `alternative`, `cumulative_related`.
- 정기/임시 판별(`detect_meeting_type`, 2026-06-20 개선)은 **head 길이 제한 없이** text 전체에서 `주주총회 소집공고` 매칭을 순회하며, 그 직후 40자 윈도우의 괄호 종류표기 — `(제N기 정기|임시)` · `(YYYY년 정기|임시)` · `(정기|임시주주총회)` — 가 가까이 오는 **첫 매칭**을 앵커로 채택한다. 본문 후방 문장(`주주총회 소집공고 등)에 의거…`)을 앵커로 잡던 오선택(임시→정기 fallback), head 내 참고사항의 `임시` 단어 선점, `(YYYY년 임시)` 변형 누락을 함께 해소한다. 윈도우에서 못 찾으면 text 전체의 첫 `(정기|임시)주주총회` 키워드로 fallback. `parse_meeting_info_xml`의 1순위 heading 패턴도 `(YYYY년 …)` 변형까지 확장했다. 전수 검증(2026 3/15~5/15, 891건, text 픽스처 순수함수 재호출 DART 0콜): 구 detect 880/891 → 신 detect 888/891. 잔여 차이는 회사가 **제목과 본문에 정기/임시를 다르게 적은 모순 공시**(891건 중 11건, 1.2% — 예: 파멥신 제목 `(정기)`/본문 `임시주주총회를 …개최`, 프리티 제목 `(제56기 임시)`/본문 `정기주주총회를 …개최`)다. 이런 공시는 `detect_meeting_type_conflict(text)`가 감지해(제목 종류표기 ≠ 본문 `(정기|임시)주주총회를 다음/아래 …` 소집문구) `parse_meeting_info_xml`의 `meeting_type_conflict` 플래그로 노출한다. detect는 제목을 우선해 한 값을 주되, 플래그가 뜨면 안건(재무제표 승인 여부 등)으로 **수동 확인**이 필요하다. 회귀 앵커(와이즈넛 20260512000585=임시, JTC 20260513000621=정기) 유지.
- 마침표형 안건 marker(`제N호 의안.`), 후보자 표 boundary, `4. 목적사항` 정정공고형 목록, `※` 주석 뒤 안건 경계를 지원한다.
- 안건 marker/zone 변형 보강 (2026-06-20, 891건 html 경로 실측 버그 교정 — 순수함수, DART 0콜):
  - **닫는괄호형 marker** `제N호 의안) …`(토비스 20260508000227) — `AGENDA_RE` 분리자를 `[:：.]` → `[:：.)）]`로 확장. 부수로 `제N-M호)` 하위안건(솔루엠·SBS·LG헬로비전)도 회수.
  - **각주마커형 marker** `제N호 의안* :`(`*†‡`, 미래에셋생명 20260323001086 제3호) — marker에 `[*†‡]?` 허용. 번호 점프(진짜 누락)를 교정.
  - **zone start 변형** — `_extract_agenda_zone`의 start_patterns에 `회의 및 목적사항`(`회의\s*(?:및\s*)?…`), `가. 목적사항`(`[가-힣]\.`), `목적사항 :`(콜론형), `부 의 안 건`(자간변형), `의안사항`을 추가. zone 추출 실패로 안건 0개이던 5건(이엠앤아이 20260504000182·퓨쳐메디신 20260427000479·프로이천 20260320000191·아스플로 20260318000030/20260316000111) 교정.
  - **제목 200자 초과 안전망** — `_AGENDA_BOUNDARY`에 `변경 전 내용`/`변경 전 변경 후`(자간 허용)·표 셀 join 번호목록(`\s{3,}\d+\.\s+`) 경계 추가, `_clean_title`에 200자 초과 시 첫 `의 건` 직후 절단(없으면 하드 200) 안전망. 표/다단 본문이 제목에 딸려와 validate 실패하던 17건(헝셩그룹 20260422000581·에스아이리소스 20260402003524·진원생명과학 20260415000404 등) 교정.
  - **제목 bleeding 경계** (2026-06-21, 전수 안건 감사) — 마지막 안건 제목에 딸려오던 다음 섹션(`4. 배당내역`·`기준일`·`Ⅳ. 경영참고사항`·`＊의안 세부내용`·`4. 주주총회 소집통지`)을 `_AGENDA_BOUNDARY`에 경계 추가. validate(200자)를 통과하던 '조용한 오류'로, 891 전수에서 제목 27건 정리(이지스밸류·아모텍·대창단조·도이치 등), 안건 개수 불변(6363), regression 0.
  - 891건 html 경로 before/after 전수 diff: zero_agenda 17→11, invalid 34→11, title_over200 17→0, regression 0(기존 정상분 안건 개수·계층 유지). 잔여 zero 11건은 baseline에서도 0개인 **비표준 양식**(①②·가나다 등 — 미커버) 또는 보고전용 공시. 번호 점프 중 평화홀딩스 20260316000840 제5호 누락은 **소스 자체 번호 공백**으로 확인되어 정상 분류(미수정).
- 인라인 하위안건 분리 + 표/변형번호 흡수 차단 + 군더더기 부호 정리 (2026-06-21, 891건 html 경로 실측 잔여 손상 교정 — 순수함수, DART 0콜):
  - **인라인 하위안건 분리** — 신규 `_split_inline_subagendas(flat)`를 `_build_tree` 직전에 호출. 부모 제목에 통째로 뭉친 `N-M …의 건`(`호` 없는 인라인 마커)을 별도 하위노드로 분리하고 부모 제목은 첫 `N-M` 앞까지 절단. 가드 3중: (a) children이 빈 l1 부모만 대상, (b) M이 1부터 순차일 때만, (c) 마커에 `(?!호)` negative lookahead — 이미 별도 파싱되던 정상 `제N-M호` 다단 계층은 **불간섭**. `제N-M 의안:`·`의안 N-M`·`- 의안 N-M`·bare `N-M` 접두를 모두 처리하고 `N-M 의안 :` 꼬리도 소비. 교정 8건(제닉 제3호·HC홈센타 제5호·국영지앤엠 제2호·제이에스링크·유에스티·크리스에프앤씨 + 보너스 스타플렉스·티웨이홀딩스) — 하위 N-M이 트리에 별도 안건 노드로 잡히며 안건 개수가 늘고 부모 제목이 깨끗해짐. 부모가 제목 없이 `N-M`으로만 구성된 경우(국영지앤엠 제2호)는 하위 공통 안건유형(`이사/감사`+`선임/중임의 건`)으로 부모 제목을 추론(빈 제목 방지).
  - **표 흡수 차단** — `_AGENDA_BOUNDARY`에 표헤더 경계 `구 분 성 명`·`전 기 당 기`(자간 허용) 추가. 이사보수 한도/임원 표가 제목에 딸려오던 엔케이 제4호·인지디스플레 제5호·현대에이치티 제3호 교정.
  - **변형 안건번호 경계** — `_AGENDA_BOUNDARY`에 `제N[-_]M(조|호) 의안` 변형(`제N-M조의안`·`제N_M호 의안`) 추가. 다음 안건번호가 변형 표기라 경계 인식이 안 되던 베셀 제2-10호·에이스테크 제4호 흡수 정리.
  - **군더더기 부호 정리** — `_clean_title` 강화: 선행 대시(`- 이사 선임의 건`), `◈◆◇` 마커(`…(상근이사 1명)◈`), 후행 `+`(하림지주), 후행 `]`는 닫는>여는 **짝 불일치일 때만** 제거(`…선임의 건]`/TS트릴리온·`이사 선임의 건 -`/비비안)하고 짝 맞는 `[현금배당 200원]`형 괄호는 **보존**. 부호 정리를 안정될 때까지 반복. 교정 9건(TS트릴리온·하림지주·평화홀딩스·비비안·아모텍 등).
  - 891건 html 경로 before/after 전수 diff: total nodes 6363→6384(+21 = 8건 하위안건 분리), valid 880 유지, empty 11 유지, numbers_lost 0(기존 정상 `제N-M호` 다단 계층 불간섭), changed 17건(8 split + 9 clean, 전부 개선).
  - 미수정 3건(의도적 deferral — 회귀 위험/범위 밖): [4] 에이엘티 제2-2호 `제31조 의결권의 행`(잘림 — 가나다 strip 완화가 버킷스튜디오 등 8건 회귀 유발해 revert)·수산세보틱스 `사내이사 문상보 - 선임`(소스 어순 문제로 clean 범위 밖), [5] 하이로닉 제2호 `2025년 정기주주총회`(비안건 헤더 오인 — 재의결 제목 내부 `제2호 의안` 참조가 콜론 없는 경계를 발동, 경계 정규식이 '제목 내부 안건참조 vs 실제 다음안건'을 구분하도록 재설계 필요한 고위험으로 보류). → 별도 후속 개선 항목.
  - **proposer(제안주체) 복원** (2026-06-21, 통합 3,016건 전수 html 경로 실측 — 순수함수, DART 0콜 · **채택 후보, main 미머지**): 주주제안 안건이 `source='주주제안'`으로 잡히도록 두 유형 보강 + false positive 0 확인. agenda node의 `source` 필드(= proposer)는 의결권 분석에 직결.
    - **[유형1 제목형]** `제N호 의안(주주제안) :` 처럼 marker 직후 괄호에 제안주체가 붙는 형태(패션플랫폼 20260318000766 제7호). `AGENDA_RE`의 marker prefix `(?:\([^)]*\))?`가 이 괄호 소스를 title 캡처 전에 소비해 `_detect_source(title)`(line 790)가 못 봄. → 신규 `_detect_source_in_marker(m.group(0))`를 폴백으로 추가: title에서 source를 못 잡으면 전체 매치에서 `[(\[]주주제안[)\]]`(및 `이사회안`) 태그를 재검출.
    - **[유형2 그룹헤더형]** `제N-M호 [주주제안]` zone 그룹헤더 / `소액주주권에 따른 주주제안` 본문 문구의 제안주체가 하위안건에 전파 안 됨(솔루엠 20260319001012 제3-2-1호, 다원시스 20260317000013 제3·4호). → `parse_agenda_xml`의 dedupe 직후 신규 `_propagate_proposer(flat, zone, text)` 호출: (a) zone 그룹헤더 `[주주제안]`/`(주주제안)`을 감지해 해당 `(l1,l2)` prefix 하위안건에 전파, (b) 본문 `제N호 의안(…) … 주주제안 후보` 직결문구를 제N호 안건에 전파. **source가 빈 안건에만** 적용(기존 잡힌 source 불간섭).
    - **[false positive 배제 — 핵심]** `주주제안권`(권리설명, 미래에셋생명 20260323001086)·`주주제안 인입보고`(보고사항, KG이니시스 20260319001390)·`주주제안에 따른 이사회 결의`(해임 사유문구, 광명전기 20260515002923)는 괄호/대괄호 태그도 `주주제안 후보` 직결문구도 아니므로 어떤 패턴에도 미매치 → `source='주주제안'` 0건 유지(절대 안건 제안주체로 오분류 금지).
    - 통합 3,016건 전수 before/after: `source='주주제안'` 68공시/282안건 → 75공시/290안건. baseline 68공시 전부 보존(손실 0), 신규 플래그 7공시(솔루엠·패션플랫폼·다원시스·솔루엠 중복 2건·휴럼 20260309001141 제1-2-2호 inline)는 전부 source-flip only(다른 필드 무변경), false positive 증가 0. 4축 통과(속도 순수함수, API 0콜, 정확성 진짜손실 교정+false 배제, regression 0). 후속 백로그 (1) proposer 손실 항목을 해소.
  - **후속 개선 백로그** (2026-06-21, 통합 3,016건 전수 — 2026 3/1~5/15 코스피·코스닥, 정기 2,849): ~~(1) proposer 손실~~ — **2026-06-21 proposer 복원으로 해소(위 항목 참조)**. (2) **빈/잘린 제목** — 다음 `제N-M호` marker가 부모 제목 영역 침범(`'제'`만 남음; 파인디앤씨·스튜디오드래곤·이오플로우). (3) **bleed 신규 표현** — `순번 안건 생년월일 약력`·`[#붙임 약력]`·`의안순번`. (4) **검증 프로토콜** — html 경로 픽스처로 0콜 전수 diff + 직접 표본 병행(스크립트 단독 신뢰 금지 — 측정 과대/과소 다수 경험).
- `annual` 조회에서 정기 소집공고가 아직 없으면 결산월과 예상 정기주총 window를 warning에 표시한다.
- KOSPI300 재실행: `exact` 298, `no_filing` 2, `requires_review` 0. 상세: [[260525_0200_audit_agenda-relation-kospi300]].
- KOSPI500 + KOSDAQ150 marketwide audit: XML 확보 641건, `no_filing` 9건, 로컬 재파싱 3회 hash diff 0. 상세: [[260525_1620_audit_agenda-parser-marketwide]].

## 사용 예

```
"삼성전자 다음 주총 안건 알려줘"
"LG화학 사외이사 후보 명단"
"카카오 보수한도 인상률 정보"
"현대차 정관변경 변경 전/후 비교"
"LG화학 주총소집공고 rcept_no=20260224004273으로 다시 파싱해줘"
```

## ref

- 주총 결과 의결: [[shareholder_meeting_results]]
- 종합 분석 (안건별 FOR/AGAINST): [[proxy_advise_before_meeting]]
- 후보 평가 (사용자 노출 X — proxy_advise chain): director_evaluation (services internal)
- 지분 구조: [[ownership_structure]]
- 분쟁 맥락: [[proxy_contest]]
- relation/parser audit: [[260525_0200_audit_agenda-relation-kospi300]]
- marketwide parser audit: [[260525_1620_audit_agenda-parser-marketwide]]
