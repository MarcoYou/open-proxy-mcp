---
type: tool
title: risk_events
domain: data
scope: [category 인자 — serious_accident/embezzlement/derivative_loss/rehabilitation/production_halt/dissolution]
data_source: [DART OpenAPI list.json (pblntf_detail_ty=I001+B001) + 키워드 + document (include_details=True 시 본문 파싱)]
related_disclosures: [공시유형코드체계]
related_concepts: []
related_decisions: [pblntf-ty-필터링]
related_audits: []
created: 2026-06-11
---

# risk_events

> 구 `serious_accident`(2026-06-11 당일 신설)를 흡수 확장. 중대재해 단독 tool로 출시 직후, 같은
> 프로파일(희소·정형·I001 키워드 타겟)의 리스크 공시 5종을 시장 90일 sweep으로 실측 발굴해
> 6카테고리 통합 tool로 재구성. 의존 사용자 없는 출시 직후가 통합 적기였음.

## 한 줄 요약
기업 리스크 이벤트 공시 통합. **활성 3종 — 중대재해 / 횡령·배임 / 생산중단·영업정지** (본사·종속/자회사 변형 포함). 파생손실·회생부도·해산 3종은 **mute** — 파서·검증 보존, 기본 조회 제외, 명시 category 요청 시에만 동작. **company 미지정 시 시장 전체 최근 30일(최대 90일) 스캔**. `include_details=True`면 카테고리별 원문 파싱 + 중대재해 사상자 집계.

## 사용법
```
risk_events(company="한화오션", include_details=True)        # 회사 24개월 전 카테고리
risk_events(company="태광산업", category="embezzlement")     # 카테고리 필터
risk_events()                                                # 시장 전체 최근 30일 스캔
```

자연어 예시:
- "한화오션 중대재해 공시 알려줘" / "최근 중대재해 발생한 기업들" (시장 스캔)
- "태광산업 횡령 배임 공시 있어?" → 혐의발생→진행→사실확인 단계 추적
- "최근 한 달 사고·사건 터진 회사 어디야?" → 시장 스캔, 6카테고리 분류

## 입력 인자
| 인자 | 타입 | 필수 | 설명 | 기본값 |
|---|---|---|---|---|
| company | str | no | 회사명 / ticker / corp_code. **공백이면 시장 전체 스캔** | "" |
| category | str | no | `serious_accident` / `embezzlement` / `derivative_loss` / `rehabilitation` / `production_halt` / `dissolution` | "" (전체) |
| start_date / end_date | str | no | YYYYMMDD | "" (company 지정 24개월 / 미지정 30일·최대 90일) |
| include_details | bool | no | True면 원문 파싱 + 사상자 집계 (DART 호출 N회 추가) | False |
| details_limit | int | no | 원문 파싱 대상 건수 (1-10) | 5 |
| format | str | no | "md" / "json" | "md" |

## 카테고리 × 채널 매핑 (시장 90일 sweep 실측, 2026-06-11)

| category | 상태 | 키워드 | 채널 | 90일 건수 | 단계(stage) |
|---|---|---|---|---|---|
| serious_accident | **활성** | 중대재해 | I001 | 14 | 발생 / 처벌확인 |
| embezzlement | **활성** | 횡령 | I001 | 22 | 혐의발생 / 진행사항 / 사실확인 |
| production_halt | **활성** | 생산중단·영업정지 | **I001+B001** | 16+5 | 발생 / 기타 |
| derivative_loss | mute | 파생상품거래손실 | I001 | 25 | 발생 |
| rehabilitation | mute | 회생절차·부도·은행거래정지 | **I001+B001** | 34+21 | 개시신청(B) / 개시결정(I) / 폐지 / 종결 / 부도 |
| dissolution | mute | 해산사유 | **B001** 위주 | 7+13 | 발생 |

> **mute (2026-06-11 결정)**: 기본 조회(category 미지정)는 활성 3종만 검색·반환. mute 3종은
> 파서·검증(359건) 완료 상태로 코드 보존 — 명시 `category=` 요청 시 warning과 함께 동작.
> tool desc에도 비노출(라우팅 차단). 활성화는 `_ACTIVE_CATEGORIES` 한 줄 수정.

- **회생절차개시'신청'(회사 제출)=B001, 개시'결정'(법원발 거래소공시)=I001** — 양 채널 필수.
- 중대재해·횡령·파생·생산중단은 B001 90일 0건 (I001 전용) 실측.
- dissolution은 SPAC·유동화전문회사 해산이 다수 포함 — 사실 그대로 노출 (판단은 LLM).

## 출력 schema (data dict)
```json
{
  "mode": "company | market_scan",
  "category": "all | <category>",
  "event_count": {"total": N, "serious_accident": N, "embezzlement": N,
                  "derivative_loss": N, "rehabilitation": N,
                  "production_halt": N, "dissolution": N,
                  "subsidiary_reports": N, "corrections": N},
  "casualties": {"deaths": N, "injuries": N, "parsed_rows": N},
  "by_company": {"회사명": N},
  "events": [{"category": "...", "stage": "...", "rcept_dt": "...",
              "report_nm": "...", "subsidiary_report": true,
              "is_correction": false, "corp_name": "(market scan만)",
              "details": {"...카테고리별 필드..."}}],
  "usage": {"dart_api_calls": N, "mcp_tool_calls": 1}
}
```

details 카테고리별 필드:
- serious_accident: location / description / **deaths / injuries** / accident_date / labor_ministry_report_date / response_plan (+처벌확인: confirmed_date·발췌)
- embezzlement: suspect / **amount_won / equity_ratio_pct** / 발췌
- derivative_loss: **loss_amount_won / equity_ratio_pct** / 발췌
- production_halt: halted_business / **revenue_ratio_pct** / reason / 발췌
- rehabilitation: court / event_date / 발췌

## Data sources
- **DART API**: `list.json` (pblntf_detail_ty=`I001`,`B001`) + 키워드 / `document` (include_details 시 N건)
- 외부 호출: company 지정 2회 / 시장 스캔 30일 실측 **44콜** (I001 36p + B001 7p + 첫페이지), 90일 ~185콜 (page cap 200). include_details +N.

## 파싱 전략
- **검증 이력 (serious_accident에서 승계)**:
  ① 고위험 49사 × 3.5년 — I 전체 vs I001 차집합 0 + 보유 21사 전체유형 풀스캔(삼성전자 35p) 누수 0.
  ② KOSPI 100 + KOSDAQ 100 + 건설 20 (220사) — 차집합 0 · truncation 0, 총 45건.
  ③ 중소형 건설·전문건설·설계/CM 36사 — 35사 0건 (유일 1건 한전KPS). **누적 305사 / 79건 / 차집합 0**.
  ④ risk_events 확장 시 시장 90일 B001/I001/I003 전수 sweep으로 5개 카테고리 채널·건수 실측.
- 제목 변형: (종속회사의주요경영사항) / (자회사의 주요경영사항) / [기재정정] / 주요사항보고서(...) 패턴 공통 처리. 중대재해 처벌확인 실물 2건 확인(화일약품 `20260529902110`, THE CUBE& `20260513900553`).
- **사상자 supersede 집계**: 같은 사건(발생일자+장소 정규화)의 원본·정정·**지주사/사업회사 이중 공시**(OCI홀딩스+OCI, DL+DL이앤씨, 한화+한화에어로 실측)는 최신 공시로 대체. 장소는 `㈜`/`(주)`·공백 정규화 — 한화 vs 한화에어로 표기 차이로 사망 5명 이중 집계되던 실측 버그 교정.
- 파서 검증: 중대재해(한화오션 사망3·POSCO홀딩스 사망3부상5), 횡령배임(태광산업 혐의금액 3,831,124,942원·자기자본 대비 0.09% 정확 추출).
- **90일 186건 전수 본문 audit (2026-06-11)** — 본문 확보 186/186 (거래소 9-채널 61건 포함 100%). 필드 추출률:
  | category | 핵심 필드 | 추출률 |
  |---|---|---|
  | serious_accident | deaths/location/accident_date | 100/100/100% |
  | embezzlement | amount_won/equity_ratio_pct | 100/100% |
  | rehabilitation | court/event_date | 100/91% |
  | derivative_loss | loss_amount_won/equity_ratio_pct | **92**/100% (라벨 `손실누계잔액(원)(기신고분 제외)` 교정 후 4%→92%) |
  | production_halt | halted_business/revenue_ratio_pct/reason | 100/100/100% (영업정지 양식 `영업정지 분야` 라벨 추가 후 59%→100%) |
  | dissolution | summary_excerpt | 100% |
  특이 제목: generic `투자판단관련주요경영사항 (1차 부도 발생)` 1건 — 괄호 사유가 report_nm에 포함돼 키워드 매칭으로 포착됨. [기재정정]은 본문 앞에 정정 전/후 표가 prepend되나 필드 추출엔 영향 없음(실측).
- **Out-of-sample 검증 — 직전 90일(20251213~20260312) 173건 전수 (2026-06-11)**:
  중대재해 100/100/100, 횡령배임 100/100, 생산중단 100/100/100, 해산 발췌 100% — 라벨 안정성 확인.
  추가 교정 2건: ① 파생손실 `손실발생금액(원)` 변형(SK하이닉스 8.37조 교환사채형) 라벨 추가 → 두 윈도우 모두 **100%**.
  ② 부도발생 양식은 법원 필드가 없어 느슨한 "법원" 라벨이 본문 문장을 오추출 → **법원명 형태 가드**(법원 포함·40자 미만)
  + `부도금액(원)` 필드 신설. court 87%·event_date 89%의 미스는 해당 필드가 실제 부재한 부도·출자법인 공시(정직한 결측).
  신규 제목 변형: `출자법인부도ㆍ해산사유등발생`, generic `기타주요경영사항 ('회생절차…' 재항고 취하)` — 모두 키워드로 포착됨.
  원문 조회 실패 1건([첨부정정] 014 오류)은 warning으로 정상 처리.
- 알려진 한계:
  - **제도 시점**: 거래소 중대재해 수시공시는 2025-10월부터 관측 (실측 최초 2025-10-29). 이전 구간 무공시 ≠ 무사고 — warning 자동 부착.
  - **공시 주체 편중**: 대형 원청·지주사 집중 (KOSDAQ 상위 100 보유 0, 중소형 건설 35사 0건). 중소형사·하청 무공시는 무사고 단정 불가 — 산재 통계 정본은 고용노동부.
  - 처벌확인·해산은 본문이 비정형이라 보수 파싱(발췌 위주). 표본 쌓이면 보강.
  - 비상장 자회사(포스코이앤씨 등) 질의는 resolve 실패 — 상장 모회사 조회 안내 자동 부착.

## 관련 공시 (rules/disclosures/)
- [[공시유형코드체계]] — I001 주요경영사항 / B001 주요사항보고서

## 관련 결정 (decisions/)
- [[pblntf-ty-필터링]] — detail-code 좁히기 + 차집합 0 검증 방법론

## 알려진 issue + TODO
- 처벌확인·해산 공시 정형 파싱 보강 (표본 누적 시).
- 횡령배임 혐의자 필드 추출률 점검 (대상자 표기 변형).
- 생산중단·영업정지 류는 제목에 단계 표지가 없어 stage가 "기타"로 표기 (정보 손실 없음, cosmetic).
- 풍문·조회공시/불성실공시법인(I003)은 성격이 "시장 레이더"라 본 tool 범위 밖 — 수요 확인 시 별도 tool 검토.

## 변경 이력
- 2026-06-11: serious_accident tool 신설 (중대재해 단독). 305사 × 3.5년 검증(차집합 0·truncation 0·누수 0), 시장 스캔 모드, 사상자 supersede 집계.
- 2026-06-11: **risk_events로 흡수 확장** — 시장 90일 sweep으로 같은 프로파일 5종(횡령배임 22·파생손실 25·회생 34·생산중단 16·해산 20건/90일) 실측 발굴 → 6카테고리 통합. B001 채널 추가(회생신청·부도·영업정지·해산), category 인자, 카테고리별 파서. 시장 스캔 30일 실측 54건/6카테고리/44콜.
- 2026-06-11: 90일 186건 **전수 본문 audit** — 파생손실 라벨 교정(`손실누계잔액`, 4%→92%) + 생산중단에 영업정지 양식 라벨 추가(59%→100%). 빈 본문(<30자) parsing failure 가드 추가.
- 2026-06-11: **직전 90일 173건 out-of-sample audit** — 파생손실 `손실발생금액` 변형 추가(양 윈도우 100%), 부도 양식 court 오추출 가드 + 부도금액 필드. 누적 본문 검증 359건.
- 2026-06-11: **스콥 결정 — 활성 3종(중대재해/횡령배임/생산중단영업정지), 파생손실·회생부도·해산 mute.** 기본 조회·desc에서 제외, 명시 category 요청 시 warning과 함께 동작. 파서·검증은 보존.
- 2026-06-11: **실사용 질의 4종 자체 검증** — ① 시장 스캔 26건/24사(mute 누수 0) ② 한화에어로스페이스: 본사 대전 폭발(사망5) + 종속 한화오션에코텍 3건까지 **모자 체인 통합 사망 8 집계** + 동일 사고의 생산중단 공시(매출 4.94%)가 한 화면에 — 카테고리 통합 가치 입증 ③ 한국타이어 횡령배임 3건 = 같은 사건의 **1심(징역3년)→2심(징역2년)→3심 판결 시계열** 추적 ④ 농심 no_filing + 이중 warning 정상.
