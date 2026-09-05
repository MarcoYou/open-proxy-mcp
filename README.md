# OpenProxy MCP

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-lightgrey.svg)](https://polyformproject.org/licenses/noncommercial/1.0.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-31-orange.svg)](#tool-구조-31개)
[![Release](https://img.shields.io/badge/release-v2.5-blue.svg)](docs/RELEASE_NOTES.md)

[English README](README_ENG.md)

## Why OpenProxy?

**주총 안건에 제대로 투표하려면, 그 회사의 모든 것을 알아야 합니다.**

OpenProxy는 주주총회 의결권 분석을 위해 태어났습니다. 그런데 안건 하나를 판단하려면 재무제표, 지분 구조, 배당 이력, 이사회, 법령까지 전부 필요했습니다. 그걸 다 만들다 보니 — **DART 공시분석 범용 엔진**이 됐습니다. 재무 분석부터 의결권 판단까지, AI에게 물어보면 공시 근거와 함께 답합니다.

![재무·현금흐름 분석 예시](screenshot/opx-cashflow.png)
*사업보고서·감사보고서 등 공시를 근거로 재무를 분석합니다 — OpenProxy를 연결한 AI 대화 예시*

<!-- 보류 (파일은 screenshot/opx-agm.png 에 그대로 둔다 — 되살릴 땐 이 주석만 벗기면 된다)
![주총 안건 분석 예시](screenshot/opx-agm.png)
*그리고 이것이 원점 — 소집공고·법령·지배구조보고서를 묶어 안건별 의견과 근거를 제시합니다*
-->

## 주요 기능

각 기능을 클릭하면 상세 설명 페이지로 이동합니다.

- **[주총 분석과 의결권 행사 권고](docs/features/proxy-voting.md)**: 정기·임시주총 안건별 근거·정책 인용과 FOR/AGAINST/REVIEW 권고를 제시하고, 표결 없음(NO_VOTE)과 자료 부족(NO_DATA)을 구분합니다.
- **[재무지표](docs/features/financials.md)**: 수익성·안정성·현금흐름 + 듀퐁 분해·감사의견 추이. 분기는 누적(YTD)·당기(3개월) 두 기준으로 QoQ·YoY 제공.
- **[밸류에이션](docs/features/price_multiple_data.md)**: PER·PBR·배당수익률(기업 심층) + 시장·산업·종목 히스토리. 시장·산업 표에는 **시총가중 배당수익률**이 확정·선행 두 벌로 실리고, 분모를 `all`(무배당 포함)·`payers`(배당주만) 두 벌로 함께 냅니다 — 코스닥은 두 값이 두 배 차이라 하나만 보면 오독합니다. `scope="explain"`으로 계산 과정·출처까지 답합니다. (runtime: `price_multiple_data`)
- **[컨센서스 포워드 추정치](wiki/tools/forward_estimates_data.md)**: 내년·내후년 예상 매출·영업이익·EPS와 **포워드 PER·PBR·PSR** + 대조용 최근 실적 2개년. 애널리스트 추정 스냅샷(`fwd`) 기반으로 DART 공시가 아니며, 커버리지는 713/2,764종목입니다. 배수는 **추정 FY·최신 확정 FY 행에만** 두고 나머지는 비웁니다 — 오늘 주가를 과거 실적으로 나눈 숫자는 배수가 아니기 때문입니다. (runtime: `forward_estimates_data`)
- **[자산주 스크리닝](docs/features/asset-holdings.md)**: 보유 자산(현금성·투자부동산·지분증권)을 티어로 나누고 상장 보유지분은 시가로 마킹 — 시총 대비 잉여자산·지분NAV 배수로 "숨은 자산"을 찾습니다.
- **[사업의 내용](docs/features/business-details.md)**: 사업부문별 매출·이익, 생산설비·가동률, 연구개발, 수주잔고, 주요 고객, **원재료·투입원가와 제품·서비스 가격 추이** — "II. 사업의 내용"을 통째로 읽어줍니다.
- **[잠정실적 속보](docs/features/provisional-earnings.md)**: 분기 영업(잠정)실적 공시를 표·증감률로 정리합니다.
- **[주주환원](docs/features/shareholder-return.md)**: 배당·자사주 소각 사이클·밸류업 계획 — 약속과 실제 집행을 비교합니다.
- **[지분·지배구조 맵](docs/features/ownership.md)**: 최대주주·특수관계인·5% 대량보유·자사주로 소유 구조를 그립니다.
- **[주총 안건 구조화](docs/features/meeting-agenda.md)**: 소집공고 안건·후보·보수한도·정관변경과 주총 후 의결 결과·찬반율.
- **[경영권 분쟁 시그널](docs/features/control-contest.md)**: 위임장·공개매수·소송·5% 경영참여 신호를 모아 정황을 나열합니다 (자동 판정 X).
- **[기업 리스크 이벤트](docs/features/risk-events.md)**: 중대재해·횡령배임·생산중단 추적. 회사 미지정 시 시장 전체 스캔.
- **[금융사 유동성·자산건전성](wiki/tools/financial_notes.md)**: 은행·증권·보험의 재무제표 주석에서 사용제한 예치금·담보제공자산(→unencumbered cash)과 투자자산 유형별 구성(→헤어컷)을 원형 그대로 추출 — 연결/별도·시점·단위·뺄 계정을 판정해 함께 냅니다.
- **[전체시장 공시 디제스트](wiki/tools/screener.md)**: 수주·자사주·배당·증자·주총·5%지분·잠정실적 공시를 한 번에 훑어 카드형으로 요약 — 매일 아침 공시 알람 루틴 ([레시피](docs/routines/screener-morning-digest.md)).

그 외 출처 추적, 기업지배구조보고서, 희석 이벤트(증자/CB), 구조개편(합병/분할), 지분 인수·매각, 거래·규모 시계열, 정관↔법령 양방향 조회, 의결권 정책 원문 조회 등 **총 31개 tool**을 제공합니다.

---

## 빠른 시작

OpenProxy MCP는 Claude, ChatGPT, Perplexity 같은 AI 서비스에 **연결해서 쓰는 도구**입니다. 설치는 필요 없습니다.

### 1단계: DART API 키 발급 (필수·무료)

OPM은 DART·거래소 공시 원문과 OpenDART API, 3개 소스를 함께 씁니다. 이 중 OpenDART API 호출에 본인의 키가 필요합니다.
[DART OpenAPI](https://opendart.fss.or.kr/) 접속 → 회원가입 → 인증키 신청 (바로 발급됩니다).

### 2단계: AI 서비스에 연결

사용하는 AI 서비스의 커넥터(앱) 추가 화면에 아래 주소를 등록합니다.

```
https://open-proxy-mcp.fly.dev/mcp?opendart=발급받은_OpenDART_API_키
```

> **API 키 주의**: 위 주소에는 본인의 API 키가 들어갑니다. 일반 채팅창에 붙여넣지 말고, 커넥터 설정의 서버 주소 입력칸에만 넣으세요.

공통 절차는 같습니다 — **커넥터 추가 메뉴에서 이름 `open-proxy-mcp` + 위 URL 입력 → 새 채팅에서 `+` 버튼으로 커넥터 선택 확인**:

| 서비스 | 메뉴 경로 | 비고 |
|---|---|---|
| **Claude** | 설정 → 커넥터 → 맞춤 설정 → 커넥터 추가 | 유료 플랜 필요. 추가 후 `도구 권한`을 **항상 허용**으로 |
| **ChatGPT** | 설정 → 앱 → 고급설정에서 `개발자 모드` ON → 앱 만들기 | 새 채팅 `+` → 더보기에서 선택 |
| **Perplexity** | 설정 → 커넥터 → 사용자 지정 커넥터 추가 | — |

> **참고**: 요금제·계정 설정에 따라 커넥터 메뉴가 없을 수 있습니다. 처음엔 서버 기동에 시간이 걸려 타임아웃이 날 수 있으니 잠시 후 재시도하세요. 새 기능이 안 보이면 커넥터를 삭제 후 재연결하면 빨리 반영됩니다.

### 사용 예시

연결이 끝났다면 자연어로 이어서 질문하면 됩니다. tool 이름을 알 필요는 없습니다.

**주총 안건 검토**
1. `LG화학 2026년 정기 주주총회 안건 알려줘`
2. `각 안건별로 찬성/반대/검토 필요 의견을 조언해줘`
3. `검토 필요 사유와 표결 없음(NO_VOTE)·자료 부족(NO_DATA)을 구분해 설명해줘`

임시주총은 `다가오는 임시주총의 경합 후보와 집중투표 제약까지 검토해줘`처럼 요청할 수 있습니다.

**주주환원 점검**
1. `KT&G 기업가치제고계획 알려줘`
2. `지난 3년 배당·자사주 취득 이력도 같이 보여줘`
3. `계획과 실제 주주환원이 일관적인지 정리해줘`

**리스크 모니터링**
1. `최근 한 달 사이에 중대재해나 횡령 공시 낸 상장사 알려줘`
2. `한화에어로스페이스 중대재해 이력을 사상자까지 자세히 보여줘`

더 많은 예시(이사 보수·경영권 분쟁·재무·밸류 등 도구별 질문) → **[wiki/tools 카탈로그](wiki/tools/README.md)** 의 각 도구 페이지 「사용법」 절

---

## Tool 구조 (31개)

분류는 [wiki/tools 카탈로그](wiki/tools/README.md)의 「무엇을 알고 싶을 때 무엇을 쓰나」 표와 같다 — 그 표가 정본이다.

| 분류 | Tools | 역할 |
|---|---|---|
| 🏢 기본 — 회사 찾기 | [`company`](wiki/tools/company.md) | 회사 식별 + 최근 공시 목록 — 모든 분석의 출발점 |
| 🔔 전체시장 스캔·디제스트 | [`screener`](wiki/tools/screener.md) | 전체시장 공시 스크리너 / 아침 공시 디제스트 |
| 🗳️ 주주총회·의결권 | [`shareholder_meeting_notice`](wiki/tools/shareholder_meeting_notice.md), [`shareholder_meeting_results`](wiki/tools/shareholder_meeting_results.md), [`proxy_advise_before_meeting`](wiki/tools/proxy_advise_before_meeting.md), [`proxy_guideline`](wiki/tools/proxy_guideline.md) | 소집공고(전)·결과(후) · 안건별 찬성/반대/검토 보조 · 판단 기준 문서 원문 |
| 💰 지분·재무·지배구조 | [`ownership_structure`](wiki/tools/ownership_structure.md), [`financial_metrics`](wiki/tools/financial_metrics.md), [`provisional_earnings`](wiki/tools/provisional_earnings.md), [`business_details`](wiki/tools/business_details.md), [`asset_holdings`](wiki/tools/asset_holdings.md), [`price_multiple_data`](wiki/tools/price_multiple_data.md), [`forward_estimates_data`](wiki/tools/forward_estimates_data.md), [`trading_data`](wiki/tools/trading_data.md), [`corp_gov_report`](wiki/tools/corp_gov_report.md), [`director_board`](wiki/tools/director_board.md) | 지분 구조 · 확정/잠정 실적 · 사업의 내용 · 자산주 · PER/PBR · 컨센서스 · 시세·시총 · 지배구조보고서 · 이사회 |
| 🎁 주주환원·자본 | [`dividend_disclosure`](wiki/tools/dividend_disclosure.md), [`dividend_data`](wiki/tools/dividend_data.md), [`treasury_share`](wiki/tools/treasury_share.md), [`value_up`](wiki/tools/value_up.md), [`shareholder_commitment`](wiki/tools/shareholder_commitment.md), [`corporate_restructuring`](wiki/tools/corporate_restructuring.md), [`dilutive_issuance`](wiki/tools/dilutive_issuance.md) | 배당 공시·시계열 · 자기주식 · 밸류업 · 약속 vs 이행 · 합병/분할 · 증자/CB/BW/감자 |
| ⚔️ 분쟁·거래·리스크 | [`proxy_contest`](wiki/tools/proxy_contest.md), [`corporate_deals`](wiki/tools/corporate_deals.md), [`order_contracts`](wiki/tools/order_contracts.md), [`risk_events`](wiki/tools/risk_events.md), [`financial_notes`](wiki/tools/financial_notes.md), [`director_news`](wiki/tools/director_news.md) | 경영권 분쟁 신호 · 지분 인수/매각 · 수주·공급계약 · 리스크 사건 · 금융사 주석 · 이사 후보 뉴스 |
| 🔗 근거·참조 | [`evidence`](wiki/tools/evidence.md), [`law_lookup`](wiki/tools/law_lookup.md) | 접수번호 → 원문 열람 URL · 정관↔법령 양방향 조회 (API 0콜) |

> 도구별 예시 질문·상세 스키마·데이터 출처 → [wiki/tools 카탈로그](wiki/tools/README.md) (각 도구 페이지의 「사용법」 절에 자연어 예시)

### 의결권 정책

**정책의 반대 기준이 곧 엔진의 자동 반대 조건은 아닙니다.** 추가 판단이 필요한 우려는 REVIEW로 두며, 출석률은 현재 판정 트리거에 미반영입니다. `proxy_guideline`으로 인용된 절을, `0-A`로 정책과 엔진의 대응표를 확인하세요. [판정·회차·정보 기준일 읽는 법](docs/features/proxy-voting.md).

`proxy_advise_before_meeting`은 OPM 자체 **Open Proxy Guideline**을 기본 정책으로 사용합니다. 판단 기준은 소수주주 보호, 거버넌스 투명성, 장기 가치, 추적 가능성입니다. 익명화된 기관 정책 corpus는 내부 cross-reference로만 쓰며 기관 실명을 노출하지 않습니다. 모든 응답에는 `data.usage`(DART·tool 호출 수)가 포함됩니다 (DART 분당 1,000 한도 — cap 910 hard guard).

**재무 기준 확인** — 승인 대상 연도의 확정치와 소집공고 잠정치, 직전 확정치를 구분해 읽습니다. 잠정치가 모든 지표를 대체하는 것은 아니므로 응답의 연도·출처·잠정 여부를 확인하세요. 잠정치에 따른 자본잠식 평가는 감사 후 재무제표를 요구하는 규정 판정을 대신하지 않습니다. 정보 기준일과 사후 자료 포함 여부는 [기능 안내](docs/features/proxy-voting.md)를 따릅니다.

---

## 데이터 소스

| 소스 | 용도 | 비고 |
|------|------|------|
| [DART OpenAPI](https://opendart.fss.or.kr/) | 정기·주요 공시 메타 + 재무 endpoint + 배당/자사주/지분 등 정형 데이터 | **필수** — 무료 API 키. 분당 1,000회 hard rule (cap 910) |
| DART 웹 (`dart.fss.or.kr`) | 공시 본문 파싱 (소집공고·주요사항보고서 등) | rate-limited (요청 간 1–2초 랜덤) |
| [KRX KIND](https://kind.krx.co.kr/) | 거래소 공시 보조 확인 | 보조 소스 |
| 익명화 기관 정책 corpus | 의결권 판단 cross-reference | 내부 정적 데이터, 실명 비노출 |

---

## 릴리즈 노트

버전별 변경 이력 → **[docs/RELEASE_NOTES.md](docs/RELEASE_NOTES.md)**

---

## Disclaimer

OpenProxy는 DART 공시 데이터를 구조화하여 AI에게 제공하는 도구입니다. AI는 할루시네이션을 일으킬 수 있고 부정확한 분석을 제공할 수 있습니다. AI가 제시하는 의견은 개발자 또는 소속 단체의 의견이 아닙니다. 분석 결과는 참고 목적으로만 사용하고, 투자 결정이나 의결권 행사의 최종 판단은 반드시 원문 공시와 전문가 검토를 거쳐야 합니다.

---

## 라이선스

[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/) — 비상업적 사용만 허용 (전문: 루트 [`LICENSE`](LICENSE))

- **비상업적 사용**(개인 연구·학습·비영리·공공기관)은 자유롭게 허용됩니다.
- **상업적 사용**은 별도 라이선스 계약이 필요합니다 (OpenProxy AI).
- **재배포 시 출처 표기**: `Copyright (c) 2026 OpenProxy AI (https://github.com/MarcoYou/open-proxy-mcp)` 유지 (PolyForm 'Notices' 조항).

> 상업 라이선스·기타 문의: gunhoqw20@gmail.com
