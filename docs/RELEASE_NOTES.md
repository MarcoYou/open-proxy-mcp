# 릴리즈 노트

OpenProxy MCP의 버전별 변경 이력입니다. [English](RELEASE_NOTES_ENG.md)

## v2.3 (2026-07-20)

26개 tool 체계. capability 질문에 답하는 신규 tool 1종과 business_details 시계열 조회 확장이 중심입니다.

- **`getting_started` 신규(26번째 tool, 신규 Discovery 카테고리)** — "OPM으로 뭐 할 수 있어?" 같은
  포괄적 capability 질문에 답하는 tool. 4인 전문가 패널(MCP 프로토콜·LLM tool-use·멀티클라이언트·
  DX 엔지니어) 토론 결과 tool 채택(resource·무대응 기각) — MCP 스펙상 "모델이 자율 판단해 반응"은
  model-controlled 영역이라 tool이 정공법이고, resource는 Claude/ChatGPT/Perplexity 3사 동시
  지원이 실무상 보장 안 됨. 검토 중 v1 `tool_guide`가 v2 재설계 후 완전히 방치돼 현재 tool 이름과
  하나도 안 겹치는 죽은 코드가 된 사실을 발견 — 반면교사로 콘텐츠를 하드코딩 대신 `mcp.list_tools()`
  런타임 introspection으로 조립해 드리프트를 구조적으로 차단. FastMCP `instructions` 필드(서버
  연결 시 1회 오리엔테이션)도 함께 신설.
- **`business_details` — bsns_year+reprt_code로 특정 과거 시점 조회 지원** — 기존 `period`
  (latest/annual/quarterly)는 항상 최신 제출분만 반환해 부문별 매출 추이 같은 시계열 질문에 답할
  방법이 없었음(실사용 세션에서 삼성전자 지난 1년 부문매출 추이 질문이 불가 판정된 사례로 발견).
  DART 표준 파라미터(기존 `get_major_shareholders` 등과 동일 컨벤션)로 과거 특정 분기/반기/연간
  1건을 지정 조회 가능. report_nm 기수라벨로 정밀 매칭해 결산월이 12월이 아닌 회사도 안전. 8개
  엣지케이스 실DART 대조 + 기존 경로 회귀 없음 확인. 한 번의 호출로 여러 기간을 반환하진 않음(추이는
  분기마다 반복 호출).

## v2.2

25개 tool 체계. 사업내용·잠정실적·자산주 스크리닝 신규 tool 3종과 이사·주주환원·자사주 정밀화가 중심입니다.

- **`asset_holdings` 신설 — 25번째 tool (2026-07-20)** — 감사 연결재무제표 계정을 목적버킷(현금성·
  환금성증권·우호제휴지분·지배관계사지분·투자용부동산·본업자산)으로 분류하고 상장 보유지분은 오늘
  시가로 재평가해 시총 대비 잉여자산/지분NAV 배수로 자산주 스크리닝. "재테크형·부동산 자산주형·
  지주사 할인형·우호지분형" 자산 성격 한 줄 진단 자동생성. 회계사·Data QA 등 전문가 에이전트 패널
  검토 + 기존 census 캐시 2,608사(KOSPI+KOSDAQ+EDGE, DART 0콜) 재활용 전수조사로 별도재무제표
  결합계정(종속+관계기업) NAV 소실 버그(130사·최대 4.57배)·REIT 활성오탐·시총 소스 불일치(19%
  괴리)를 발견·수정.
- **`provisional_earnings` 신설 — 24번째 tool (2026-07-19)** — 영업(잠정)실적(I002 공정공시) 분기
  잠정 매출·영업이익·순이익+YoY. 정기보고서 확정치보다 먼저 나오는 가장 빠른 실적 신호.
  table_markdown primary + headline best-effort, 자동차 판매대수·조선 수주 등 비재무형도 커버.
  멀티에이전트 24사 + KOSPI500 census 검증.
- **`business_details` 신설 — 23번째 tool (2026-07-18)** — "II.사업의 내용"에서 사업부문별 매출·
  영업이익(segments, 정형→저신뢰 시 원문 마크다운) + 사업장·가동률·연구개발·수주현황·주요 고객
  5필드(markdown-primary) + D-트랙(금융·REIT: 영업현황·재무건전성·투자부동산, KSIC 게이트).
  286사 census + 재무·공시·산업 3전문가 QA. `period=latest`(사업·반기·분기 중 최신) 기본.
- **`director_board` 신설 — 20번째 tool (2026-07-08), 각주정밀도·출석률·성능 검수 (2026-07-09)** —
  **개별 이사 단위** 정보 tool: 등기이사 인당보수·보수한도 소진율·재직/사퇴 변동·5억원 이상 개별
  보수·미등기 임원 보수·직원 대비 임원 보수 배수(pay_gap)·이사회 출석률. 정형 API가 각주 본문을
  안 줘 바 마커(`(주1)`)만 남던 것을 **사업보고서 원문(document.xml)에서 해소** — 단, 승인한도 셀에
  무관한 각주(소송충당부채·특수관계·스톡옵션·표조각·타인 각주)를 자신있게 노출하던 오답을 **5중
  게이트**(유형·인물 disambiguation·문장완결성·표조각 필터·dedup)로 차단하고 미달 시 원문 발췌로
  강등(300사 검수: resolved 오답 0). 이사회 출석률도 원문에서 파싱(section-local, 회사별 인라인
  요약이 사외이사만인 경우 attendance_partial 플래그). 성능: 각주 fetch 병렬화 + 소집공고 파싱
  타임아웃으로 max wall 21.6초→8.7초. 골든 회귀 테스트 `spot_footnote_golden.py`로 5개 오답유형 감시.
- **`shareholder_commitment` 신설 — 19번째 tool, 2번째 Action Tool (2026-07-07)** — 밸류업·배당·자사주
  소각의 약속 vs 실제 이행을 연중 추적(`proxy_advise_before_meeting`이 주총 1회성 판단이라면 이건
  스튜어드십 engagement 관점). 자사주 소각 사이클마다 매입시점 BPS 대비 실제 매입가를 비교해 장부가
  손익을 원화로 계산, 배당은 별도 "주주환원 종합"에 포함. 배당수익률은 DART 결의시점 시가배당률의
  옛 연도 결측을 krx_weekly 연말종가로 보완(`yield_pct_yearend`).
- **`treasury_share` 원문 단위(백만원 등) 미인식 버그 수정 (2026-07-07)** — 실행결과보고서 표가 단위를
  다르게 선언하면 금액이 최대 100만분의 1로 축소되던 버그. KOSPI200 전수 스캔으로 7개사 26건 확인 후
  수정, 0건으로 회귀검증.
- **financial_metrics 기간(period) 처리 정밀화** — DART는 항목별로 기간 의미가 다르다(손익 thstrm=당기 3개월·누적은 thstrm_add / 현금흐름=누적 / 재무상태=잔액). 분기보고서 조회 시 ① 손익을 **누적(YTD) + 당기 분기(standalone) 두 기준**으로 산출(반기/3분기는 직전 보고서 차분), ② 회전일수(DSO/DIO/CCC)를 **TTM(최근 4분기) 분모**로 계산해 단일분기 연환산 왜곡 제거(SK하이닉스 26Q1 DIO 511→133일, DSO 거짓 38.6→61.4), ③ ROE/ROA는 연환산 없이 분기값 유지, ④ 기준을 항상 `period_basis`/`turnover_basis`/`basis_note`로 명시. 부수로 evidence에 원문 보고서 rcept·뷰어URL 부착, 연결(CFS) 미작성 시 별도(OFS) 폴백 경고, 분기 인지형 디폴트(year 미지정 quarterly는 당해 연도), 영업이익률 QoQ/YoY %p 동봉.
- **ownership_structure 공동보유자 명세 제품화** — 5% 대량보유 헤드라인 지분율은 보고자 본인+특별관계자 합산. 이를 분해해 `reporter_self_pct`(본인) + `co_holders`[{name, ownership_pct, is_registry_holder}] + `co_holders_verified`(합≈헤드라인 불변식)로 노출, 렌더에 "공동보유자 분해" 표 추가("OO의 N%=누구 얼마씩"에 직접 답). 특관에 명부상 최대주주 포함 시 `coheld_with_registry` 재분류(proxy_contest 외부세력 오분류 방지). 파서 정제: self 이름 오염(주수비율)·㈜ 기호·펀드명 숫자(제N호)·긴 영문명·외국법인 ID(LEI·외국 등록번호) — 분쟁 엣지 포함 332사 전수 불변식 92.7→95.3%, 미검증은 verified=False로 정직 표기.
- **shareholder_meeting proposer_type 통일** — 주주제안 안건 proposer_type 값을 canonical `shareholder_proposal`로(과거 `shareholder`와 불일치해 소비자가 주주제안을 놓침). KOSDAQ 주주제안 전수(원문 교차검증)로 검출 정상화.
- **treasury_share 자사주 종류별(보통주/종류주식) + 복수종류 누락 교정** — 취득/처분 결과를 보통주 vs 종류주식(우선주·기타주식·RCPS 등 통합) 2분류로 노출. 결과보고서가 보통주/우선주 표를 따로 두고 ACODE가 보통주만 잡던 누락(미래에셋 600→1,000억=보통주600+종류주식400) 일별 합산으로 보정. KOSPI 200·KOSDAQ 200·우선주 활동 172사 전수 — 누락은 미래에셋 1건뿐(처분·소각은 정상), 단주 노이즈는 1억 하한으로 미발동.
- **보수한도 단일-library fallback** — 전 안건을 단일 `<library>`에 몰아넣는 양식(기업은행·한국금융지주)에서 보수 안건의 당기/전기 표가 안 붙어 `amount_unparsed`이던 것을, 구조 파싱 실패 시에만 원문 텍스트로 보정(정상 회사 미접촉=회귀 안전). 금융 35사 전수로 두 회사 한정 확인. 기업은행·한국금융지주 이사 보수한도 정상 산출(둘 다 상향).
- **proxy_result_after_meeting 제거** (order_contracts 신설로 상쇄 — 현행 17 tool) — 핵심(안건별 가결/부결/찬반율)은 `shareholder_meeting_results`가 더 적은 호출로 동일 제공. 후속 공시·분쟁·거버넌스는 각 tool 직접 호출로 체이닝.
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
- **공시유형 코드체계 인덱스** — `pblntf_ty`/`pblntf_detail_ty` → 실제 공시 매핑. 검색 시 상세코드로 범위를 먼저 좁힘 (배당=`I001` 등).
- **주주환원 추적** — 배당/자기주식/기업가치제고 통합 조회.
- **재무·지배구조 점검** — DART 재무 endpoint + 기업지배구조보고서.
- **안정성** — DART 분당 1,000 한도 rolling-window hard guard(cap 910), 3-tier fallback(XML→PDF→OCR), 전 응답 출처 추적(`data.usage` + 공시번호).

다음 작업(내부 관리): 재무제표 주석 파싱(특수관계자·우발부채·세그먼트), 공시 검색 detail-코드 확장 등.
