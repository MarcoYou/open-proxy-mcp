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
- **`scope="detail"` 주석 4필드**(`real_estate`·`equity_holdings`·`pledged_assets`·`contingent`) — 260803 계약 확장:
  - `basis`: 그 표가 **연결**인지 **별도**인지. DART `document.xml`이 주석 표의 셀마다 다는 XBRL 컨텍스트
    (`ACONTEXT`)의 선언을 읽는다. **선언이 없으면 내지 않는다** — 별도 자산을 연결로 믿고 NAV를 계산하면
    스크리닝 결과가 뒤집히므로, 추정으로 메우지 않는 쪽이 맞다.
  - `basis_conflict`: 경고. ① 한 구간에 연결·별도 표가 섞임 ② 별도를 읽었는데 같은 보고서에 연결 주석도 있음
    ③ 절 제목(「연결재무제표 주석」)과 셀 선언이 어긋남(공시 자체의 표기 불일치일 수 있어 판단하지 않고 드러낸다).
  - `source_excerpt` / `absence_excerpt`: 그 자리의 **원문 문구 인용**. 주석 번호를 되짚어 뽑는 방식은
    한 칸 앞 주석을 집는 일이 잦아 폐기했다 — 틀린 번호는 읽는 쪽을 엉뚱한 데로 보내니 없느니만 못하다.
    인용은 추론이 아니라 사실이라 틀릴 수 없다.
  - `absence_kind` / `absence_note`: `business_details`와 같은 어휘로 부재 사유를 가른다
    (`not_disclosed`·`cross_reference`·`narrative_only`·`extraction_failed`). 종전 `na_reason`은
    "무담보 or 미기재"처럼 읽는 쪽이 어느 쪽인지 알 수 없는 문장이었다. **미탐이라 부르려면 그 필드를
    정의하는 축**(지분증권=취득원가 / 토지·투자부동산=공정가치·공시지가 / 담보=담보권자·담보설정금액)이
    곁에 있어야 한다 — 어휘만 스친 다른 표(민감도·공정가치 서열·재무상태표 줄)는 미탐이 아니다.

## Data sources
- **계정**: `fnlttSinglAcntAll`(CFS 우선 → OFS fallback, 소규모기업 013/정정 014 tolerant).
- **타법인출자**: `otrCprInvstmntSttus` — per-holding 취득원가·장부가·지분율. **상장여부는 API에
  없음** → 원문 doc에서 종목명 인접 "(비)상장" 텍스트로 도출(권위값, name-join 동명이인 오판 방지).
- **III.재무 주석**: `asset_valuation.py`의 markdown-primary 추출(토지·투자부동산·지분증권·담보·우발).
- **시세**: `get_stock_price`/`get_stock_total` — top-12(장부가) 상장 보유만 콜(상한으로 비용 제한).

## 파싱 전략
`asset_valuation.py`의 content-signature(순수 lookahead, `_find_regions`)가 앵커±윈도를 markdown으로
반환 — 표 구조 판정 없이 원문을 caller AI가 읽어 판단(`markdown-primary-anchor-260719` 원칙 계승).

**설계 근거**:

1. **결합계정은 `mixed` 티어로 따로 둔다** — 별도재무제표(OFS) 지주사가 흔히 쓰는 「종속기업,
   공동기업과 관계기업에 대한 투자자산」 같은 결합계정을 통째로 `subs`(NAV 제외)로 보내면 큰
   자산이 조용히 사라진다. 자산표엔 노출하되 지배지분이 섞여 있어(지분법원가≠공정가치)
   `equity_nav` 에 자동 합산하지 않고 **별도 참고라인**(`mixed_combined_krw`)으로 표기한다 —
   블렌딩할 근거는 없지만 숨겨서도 안 된다. 순수 종속기업 단독계정은 그대로 `subs` 다.
2. **REIT 게이트** — KSIC 68(부동산업)은 금융업 게이트(64/65/66) 밖이라 REIT 의 본업자산
   (투자부동산)이 그대로 「잉여자산」에 가산될 수 있다(숨은 저평가가 아니라 명백한 오신호).
   사명 「리츠」/「REIT」 휴리스틱으로 게이트를 둔다 — KSIC 세부코드만으로는 부동산 개발업과
   REIT 를 못 가른다.
3. **시그니처 동의어·표 형태 확장** — 같은 정보를 다른 말과 다른 형태로 싣는 회사들이 있다
   (「원가 또는 간주원가」·「취득금액」, 롤포워드 없는 당기말/전기말 스냅샷 표, 실명 종목이 있는
   FVOCI 명세). 앵커 시그니처가 이 변형을 흡수한다.
4. **넓힌 시그니처에는 배제어를 짝지어 둔다.** 지분증권 앵커를 넓히면 K-IFRS 9 매출채권 손상충당금
   표까지 걸린다 — 그 표에서만 쓰이는 고유 용어로 배제를 건다.

## 알려진 issue·TODO
- 089010류 "비상장 주식"(띄어쓰기 변형) 앵커 리터럴 불일치로 일부 잔여 미검출.
- 053160류 "원가 컬럼 자체가 없는 장부금액 표"는 시그니처로 계산 불가(데이터 자체 부재) —
  markdown 통짜 덤프 + 저신뢰 라벨이 후보안, 미구현.
- holdco/fin_sub_suspect(비금융지주 산하 금융자회사 섞임) 라벨은 스코프 크리프 판단, later.
- 지분 NAV·잉여자산 모두 **원가/장부가 기준**(투자부동산은 공정가치 gap이 있어도 자동 반영 안 함) —
  gap은 `scope="detail"`에서 caller가 직접 읽어 반영.

## 관련
- [[business_details]] (II.사업의내용 — 원래 opt-in 필드였다가 분리된 원본)
- `asset_valuation.py`(markdown-primary content-signature 엔진, [[markdown-primary-anchor-260719]] 계승)
- [[price_multiple_data]] (전사 밸류에이션 PER/PBR — 시총 시계열은 이쪽, 자산 point-in-time은 asset_holdings)
- [[financial_metrics]] (재무비율 — 부채 미차감 gross NAV 배수는 PBR과 병용 권장)
- [[260721_1500_decision_asset-holdings-purpose-buckets]] (자산 목적버킷 6분류 — 회계사 검토·확정,
  자산 성격 서사(재테크형/부동산 자산주형/지주사 할인형/우호지분형) 근거)

## 변경 이력
- 2026-08-06: 파싱 기법 상세·census·검증 프로토콜을 private storage 로 이관(경계 규칙 [[wiki_schema]] 0.0).
- 2026-07-21: **시총은 [[price_multiple_data]]의 `_market_for`(KRX 캐시, 상장주식수 기준)를 재사용**한다 —
  자체계산(DART 유통주식수 × 종가)은 같은 회사에 다른 시총을 내고 DART 콜만 는다(계산 지표 단일 소스
  원칙). **FVPL 종목별 보유명세** 시그니처 추가(「상장주식의 내역」 롤포워드 표 — 원가 비교가 아니라
  기초~기말 시가평가 변동이라 기존 시그니처에 안 걸렸다). **목적버킷 6분류 신설**
  ([[260721_1500_decision_asset-holdings-purpose-buckets]]) — 현금성/환금성증권/우호제휴지분/
  지배관계사지분/투자용부동산/본업자산. 금융업은 surplus·지분NAV 배수를 내지 않는다(트레이딩·FVOCI가
  본업이라 「보유자산 대비 저평가」 서사가 성립하지 않는다). `_mark_listed_stakes` 정렬에 `key=` 명시
  (튜플 전체비교는 동률 시 dict 비교로 크래시).
- 2026-07-20: 신규 tool. summary + detail 2스콥(coverage 는 summary 가 흡수).
