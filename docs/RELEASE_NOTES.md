# 릴리즈 노트

OpenProxy MCP의 버전별 변경 이력입니다. [English](RELEASE_NOTES_ENG.md)

## v2.1 이후 (미릴리즈, 2026-06-12 ~ )

- **financial_metrics 기간(period) 처리 정밀화** — DART는 항목별로 기간 의미가 다르다(손익 thstrm=당기 3개월·누적은 thstrm_add / 현금흐름=누적 / 재무상태=잔액). 분기보고서 조회 시 ① 손익을 **누적(YTD) + 당기 분기(standalone) 두 기준**으로 산출(반기/3분기는 직전 보고서 차분), ② 회전일수(DSO/DIO/CCC)를 **TTM(최근 4분기) 분모**로 계산해 단일분기 연환산 왜곡 제거(SK하이닉스 26Q1 DIO 511→133일, DSO 거짓 38.6→61.4), ③ ROE/ROA는 연환산 없이 분기값 유지, ④ 기준을 항상 `period_basis`/`turnover_basis`/`basis_note`로 명시. 부수로 evidence에 원문 보고서 rcept·뷰어URL 부착, 연결(CFS) 미작성 시 별도(OFS) 폴백 경고, 분기 인지형 디폴트(year 미지정 quarterly는 당해 연도), 영업이익률 QoQ/YoY %p 동봉.
- **ownership_structure / proxy_contest 공동보유 분류** — 5% 대량보유보고서 합계표를 파싱해 보고자 본인 vs 특별관계자 분리, 특관에 명부상 최대주주가 포함되면 `coheld_with_registry`로 재분류(외부 세력 오분류 방지).
- **proxy_result_after_meeting 제거 (17→16 tool)** — 핵심(안건별 가결/부결/찬반율)은 `shareholder_meeting_results`가 더 적은 호출로 동일 제공. 후속 공시·분쟁·거버넌스는 각 tool 직접 호출로 체이닝.
- **전 tool 전수조사 완료** — 파싱 성공률을 넘어 값 정확도·단위·이음새(seam)·렌더·production까지 검증. ownership 450사(DART 원본 단위 오염 자가 교정), 값 정확도 286사, corp_gov 30사 기준값 일치, 렌더 31케이스, production MCP smoke.
- **proxy_result 결과 0건 회귀 발견·교정** — upstream 키 개편 미반영으로 안건 결과가 항상 비던 문제 (제거 전 교정 검증 완료).
- **proxy_advise 견고성** — 보수 인원수 문자열 crash, 성과 매트릭스 None 포맷 crash 교정.

## v2.1

17개 tool 체계. 리스크 이벤트 추적과 자연어 라우팅 개선이 중심입니다.

- **risk_events 신설 (17번째 tool)** — 중대재해·횡령배임·생산중단 공시 통합. 회사 미지정 시 시장 전체 최근 30일 스캔. 검색 305사 × 3.5년 차집합 0, 본문 359건 전수 검증.
- **related_party_transaction → corporate_deals** — "인수/매각" 자연어 질의가 tool 라우팅에 실패하던 문제를 이름·설명 어휘 개선으로 해결.
- **ownership_structure 정밀화** — 발행주식총수 100% 정합 분해, 명부상 최대주주 vs 5% 보유 실세 구분, 분쟁사 5% 변동 통합.
- **dividend 정밀화** — 분기 배당을 정기보고서 누적 차분으로 산출, 51개사 정합성 100% 검증.
- **financial_metrics 정밀화** — Q4에 연간 누적치가 섞이던 문제를 누적 차분으로 해결, 전 분기 standalone 3개월 기준 + QoQ·YoY 기본 동봉. 이자보상배율 왜곡(금융비용 오염) 제거 및 커버리지 97%로 확대, 차입금·분기 현금흐름 복구. 금융사·기중 분할 재작성은 자동 안내. KOSPI 300·KOSDAQ 100 포함 412개사 × 2개년 전수 검증.

## v2.0

OpenProxy MCP의 첫 정식 릴리즈입니다. `tools_v2` toolset 기준 16개 public tool로 한국 상장사 거버넌스 분석 전반을 커버합니다.

- **16 public tool** — Company → Meeting/Data/Evidence → Action 흐름.
- **지분·경영권 분쟁 신호 정밀화** (`proxy_contest`) — 소송 4단계 분류·중복제거, 5% 보유 동학(목적 전환·지속 매집), 외부세력/대주주 본인 분리.
- **공시유형 코드체계 인덱스** — `pblntf_ty`/`pblntf_detail_ty` → 실제 공시 매핑([wiki](../wiki/rules/disclosures/공시유형코드체계.md)). 검색 시 상세코드로 범위를 먼저 좁힘 (배당=`I001` 등).
- **주주환원 추적** — 배당/자기주식/기업가치제고 통합 조회.
- **재무·지배구조 점검** — DART 재무 endpoint + 기업지배구조보고서.
- **안정성** — DART 분당 1,000 한도 rolling-window hard guard(cap 910), 3-tier fallback(XML→PDF→OCR), 전 응답 출처 추적(`data.usage` + 공시번호).

다음 작업(내부 관리): 재무제표 주석 파싱(특수관계자·우발부채·세그먼트), 공시 검색 detail-코드 확장 등.
