---
type: tool
title: asset_holdings
domain: data
scope: [summary, detail]
data_source: [DART fnlttSinglAcntAll(계정 API), DART otrCprInvstmntSttus(타법인출자현황), DART get_document(III.재무 주석), get_stock_price/get_stock_total(시가마크·시총)]
related_disclosures: [사업보고서]
related_concepts: [NAV, 청산가치, 지주사할인, FVOCI, FVPL, 지분법, 공시지가]
related_decisions: [markdown-primary-anchor-260719]
created: 2026-07-20
---

# asset_holdings

## 한 줄 요약
회사가 **보유한 자산**(현금성·투자부동산·지분증권·관계기업 지분)을 감사 연결재무제표 계정에서 뽑고,
상장 보유지분은 **시가로 마킹**해 시총 대비 청산가치(NAV) 커버리지를 계산한다. "시총보다 보유 자산이
값진가"에 답하는 자산주·지주사 할인 스크리닝 도구.

## 배경
`business_details`(II.사업의 내용) 개발 중 자산가치(토지·투자부동산·지분증권 원가vs공정가치) opt-in
필드로 시작했으나, ① 소스가 III.재무 주석(II와 다른 대분류) ② 유즈케이스가 시총 비교·시가마크까지
확장 ③ 계정 API·타법인출자 API를 새로 결합해야 해서 260720 별도 tool로 분리했다.

## 사용법
- `asset_holdings(company, scope="summary", format="md")`
- **`summary`(기본)** — 자산 티어 표 + **상장 보유지분 시가마크** + 담보·우발 haircut 플래그 + 시총
  대비 배수(잉여자산/지분NAV). 한 번 호출로 스크리닝에 필요한 전부.
- **`detail`** — III.재무 주석 원문 markdown(토지 원가vs공정가치·지분증권 명세·담보제공·우발부채).
  summary에서 haircut 플래그가 뜨거나 숫자 원문을 직접 확인하고 싶을 때.
- 예: `asset_holdings("영풍")` → 지주사 숨은 지분가치(코리아써키트 장부 820억→시가 7,736억,
  미실현 +6,916억) · `asset_holdings("천일고속", scope="detail")` → 소규모기업(OFS) 원문 명세.

## 출력 (ToolEnvelope.data)
- `assets`: BS 계정을 목적·환금성 티어로 묶은 표(현금성·환금투자FVPL·장기투자증권·FVOCI·지분법·투자부동산
  ·유형자산·매각예정·기타비유동금융·**결합출자(종속+관계/공동, 미분리)**). `subs`(순수 종속기업, 별도FS
  전용)는 제외 — 본업 지배지분이라 NAV 대상 아님.
- `nav`: `surplus_krw`/`surplus_cov`(잉여자산 = 현금+환금+장투증권+매각예정[+투자부동산, 비금융·비REIT])
  · `equity_nav_krw`/`equity_nav_cov`(지분 NAV = 관계기업[시가마크 반영]+FVOCI) ·
  **`mixed_combined_krw`/`mixed_combined_cov`**(결합계정 별도 참고라인 — 지배지분 섞여있어 위
  equity_nav엔 미포함) · `haircut_flags`(담보·우발 존재 시).
- `listed_stakes`: 타법인출자 상장 건 top-12(장부가) 시가마크. `marked[].{book_krw,mkt_krw,gap_krw}`.
- `is_financial`(KSIC 64/65/66 게이트) · `is_reit`(사명 "리츠"/"REIT" 휴리스틱) — 둘 다 투자부동산이
  본업이라 잉여자산에서 제외하고 라벨링.
- `market_cap_krw`: 유통주식수 × 최근 거래일 종가.

## Data sources
- **계정**: `fnlttSinglAcntAll`(CFS 우선 → OFS fallback, 소규모기업 013/정정 014 tolerant).
- **타법인출자**: `otrCprInvstmntSttus` — per-holding 취득원가·장부가·지분율. **상장여부는 API에
  없음** → 원문 doc에서 종목명 인접 "(비)상장" 텍스트로 도출(권위값, name-join 동명이인 오판 방지).
- **III.재무 주석**: `asset_valuation.py`의 markdown-primary 추출(토지·투자부동산·지분증권·담보·우발).
- **시세**: `get_stock_price`/`get_stock_total` — top-12(장부가) 상장 보유만 콜(상한으로 비용 제한).

## 파싱 전략
`asset_valuation.py`의 content-signature(순수 lookahead, `_find_regions`)가 앵커±윈도를 markdown으로
반환 — 표 구조 판정 없이 원문을 caller AI가 읽어 판단(`markdown-primary-anchor-260719` 원칙 계승).

**260720 전수조사(KOSPI+KOSDAQ+EDGE 2,608사 캐시 재사용, DART 0콜) + 5인 패널(재무·부동산·공시전문가·
가치투자자·Data QA) 토론 결과, 다음을 확정·수정**:

1. **[BLOCKER 수정] 결합계정 NAV 소실** — 별도재무제표(OFS) 지주사가 흔히 쓰는 "종속기업, 공동기업과
   관계기업에 대한 투자자산" 같은 결합계정이 `_tier()`에서 통째로 `subs`(NAV 제외)로 분류되던 버그.
   시총 대비 최대 4.57배(휴맥스·한국토지신탁 등 130사 실측)가 조용히 사라졌었음. 신규 `mixed` 티어로
   분리 — 자산표엔 노출하되 지배지분이 섞여있어(지분법원가≠공정가치) equity_nav엔 자동 합산하지 않고
   **별도 참고라인**(`mixed_combined_krw`)으로 표기(재무전문가 "블렌딩 근거없음" vs 나머지 패널 "숨기면
   안됨" 절충안). 캐시 전수 재검증: 순수 종속기업 단독계정은 그대로 subs 유지(과대교정 0).
2. **REIT 활성오탐 수정** — KSIC 68(부동산업)은 기존 금융업 게이트(64/65/66) 밖이라 REIT의 본업자산
   (투자부동산)이 그대로 "잉여자산"에 가산되던 문제(부동산전문가: "숨은 저평가가 아니라 명백한 오신호").
   사명 "리츠"/"REIT" 휴리스틱으로 게이트 추가(KSIC 세부코드보다 안전 — 개발업 vs REIT를 KSIC만으로
   못 가름). 롯데리츠 실측으로 surplus_cov 정상 억제 확인.
3. **시그니처 동의어 확장** — `_SIG_INV_PROP`(투자부동산 원가vs공정가치)이 "원가 또는 간주원가"(IFRS1
   최초채택 문구)·"취득금액"(취득원가의 실무 동의어) 변형을 놓치던 것 보강. `_SIG_TANGIBLE_LAND`(유형자산
   토지)가 롤포워드 없는 단순 당기말/전기말 스냅샷 표(중소형사 흔함)를 놓치던 것 보강. `_SIG_EQUITY`
   (지분증권)의 "취득원가" 리터럴 고정이 실명 종목(LG유플러스·KT스카이라이프 등) 있는 FVOCI 명세를
   과잉 배제하던 것 완화.
4. **완화의 부작용을 재검증에서 발견·수정** — 3번의 "총장부금액" 동의어 추가가 K-IFRS9 매출채권
   손상충당금 표까지 오탐시킴(바이오노트 실측, Data QA 패널이 요구한 "완화 후 오탐 회귀검증"에서 적발).
   "손상차손누계액"(매출채권 표 고유 용어, 지분증권 표는 "차손익적립금"/"평가손익" 사용) 공존 시 배제
   추가로 해결 — 재검증 결과 원래 목표(YTN 등 실명 상장주식 명세 복구)는 유지, 신규 오탐만 제거.

## 알려진 issue·TODO
- 089010류 "비상장 주식"(띄어쓰기 변형) 앵커 리터럴 불일치로 일부 잔여 미검출.
- 053160류 "원가 컬럼 자체가 없는 장부금액 표"는 시그니처로 계산 불가(데이터 자체 부재) — 패널
  합의로 markdown 통짜 덤프+저신뢰 라벨(D안) 후보로 남김, 이번 라운드 미구현(QA 프로토콜 선행 필요).
- holdco/fin_sub_suspect(비금융지주 산하 금융자회사 섞임) 라벨은 스코프 크리프 판단, later.
- 지분 NAV·잉여자산 모두 **원가/장부가 기준**(투자부동산은 공정가치 gap이 있어도 자동 반영 안 함) —
  gap은 `scope="detail"`에서 caller가 직접 읽어 반영.

## 관련
- [[business_details]] (II.사업의내용 — 원래 opt-in 필드였다가 분리된 원본)
- `asset_valuation.py`(markdown-primary content-signature 엔진, [[markdown-primary-anchor-260719]] 계승)
- [[valuation]] (전사 밸류에이션 PER/PBR — 시총 시계열은 이쪽, 자산 point-in-time은 asset_holdings)
- [[financial_metrics]] (재무비율 — 부채 미차감 gross NAV 배수는 PBR과 병용 권장)

## 변경 이력
- 2026-07-20: 신규 tool. summary+detail 2스콥(초기 3스콥 coverage/summary/detail → coverage를
  summary로 완전 흡수). 5인 패널 검증 후 결합계정 NAV 버그·REIT 오탐·시그니처 동의어 3종 수정,
  완화 부작용(매출채권 오탐) 재검증에서 발견해 즉시 수정.
