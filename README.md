# OpenProxy MCP

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-16-orange.svg)](#tool-구조-16개)

[English README](README_ENG.md)

## Why OpenProxy?

코리아 디스카운트의 핵심에는 거버넌스 리스크가 있어요. 패시브 투자가 늘면서 주식 오너십의 의미가 희미해지는 지금, 이 리스크는 오히려 더 선명해지고 있죠. 거버넌스 정보에 쉽게 접근하고, 빠르게 분석할 수 있어야 하지만 -- 수백 페이지의 공시 원문을 직접 읽고 판단하기엔 시간도 전문성도 부족해요.

**OpenProxy는 AI로 이 장벽을 허물어요.** DART 공시를 구조화된 데이터로 바꿔서, 지분 구조부터 배당 이력, 주총 안건, 경영권 분쟁까지 -- 거버넌스 분석 전반을 누구나 몇 초 만에 할 수 있게 만들었어요.

![OpenProxy MCP 비교](screenshot/open-proxy-mcp%20output%20kor.png)

---

## 빠른 시작

### 0단계: Claude 구독 확인 (필수)

MCP 커넥터는 **Claude Pro, Max, Teams** 구독자만 사용할 수 있어요. [claude.ai](https://claude.ai)에서 구독 상태를 확인해주세요.

### 1단계: DART API 키 발급 (필수)

OpenProxy의 모든 데이터는 DART OpenAPI에서 가져와요. **본인의 API 키가 있어야 사용할 수 있어요.**

1. [DART OpenAPI](https://opendart.fss.or.kr/) 접속 -> 회원가입
2. 인증키 신청 -> 발급 (무료, 바로 발급돼요)

### 2단계: 연결

API 키를 발급받았다면, 아래 두 가지 방법 중 하나를 선택하세요.

#### 방법 A: 원격 서버 (설치 없이 30초면 돼요)

URL 끝에 발급받은 DART API 키를 붙여서 연결해요. 키는 서버에서만 사용되고, AI에게는 노출되지 않아요.

**claude.ai 웹:**

1. [claude.ai](https://claude.ai) 접속 -> 설정 -> 커넥터
2. "커스텀 커넥터 추가" 선택
3. 이름: `open-proxy-mcp`, URL 입력:
```
https://open-proxy-mcp.fly.dev/mcp?opendart=발급받은_키
```
4. "추가" 클릭 -> 16개 tool이 자동으로 인식돼요
5. 추가된 커넥터의 구성 -> 권한에서 **"항상 허용"** 선택 (매번 승인 없이 tool이 자동 실행돼요)

> **참고**: tool이 추가되거나 변경된 경우 커넥터 MCP 서버 업데이트에 시간이 걸릴 수 있어요. 커넥터를 삭제한 뒤 다시 연결하면 바로 최신 tool이 반영돼요. 재연결한 후 새 채팅을 열어서 다시 시도해주세요.

### 사용 예시

연결이 끝났다면, 자연어로 질문하면 돼요:

```
"삼성전자 주주총회 안건 분석해줘"
"KB금융 사외이사 후보 독립성 검토해줘"
"현대차 보수한도 적정성 판단해줘"
"삼성전자 지분 구조 보여줘"
"SK하이닉스 배당 추이 알려줘"
"고려아연 경영권 분쟁 분석해줘"
"최근 30일 자사주 소각 결정한 KOSPI 기업 찾아줘"
"최근 60일 임시주총 소집한 기업 리스트업해줘"
"이마트 최근 주식교환·이전 결정 분석해줘"
"감성코퍼레이션 분할결정 상대방·일정 보여줘"
"하이퍼코퍼레이션 전환사채 잠재 희석률 계산해줘"
"EDGC 최근 2년 유상증자·감자 이력 보여줘"
"POSCO홀딩스 자회사 타법인주식 거래 내역"
"현대건설 최근 단일공급계약 체결 패턴"
"KT&G 기업지배구조보고서 준수율 보여줘"
"현대자동차 15개 지배구조 핵심지표 상세"
"삼성전자 지배구조 연도별 준수율 추이 (timeline)"
"KT&G 의결권 메모 만들어줘 (행동주의 운용사 스타일로)"
"8개 자산운용사 이사 보수한도 의결권 정책 비교해줘"
"S레거시(대형 운용사) 의결권 행사 정책 vs 실제 갭 분석해줘"
"M레거시(대형 운용사) 2025년 삼성전자 의결권 행사 내역 보여줘"
"Open Proxy Guideline 12 카테고리 정책 보여줘"
"롯데케미칼 2024 연결 재무 yoy + 회계 risk alert 보여줘"
"SK하이닉스 2024 turnaround 검증 (적자→흑자, 듀퐁 분해)"
"오스템임플란트 5년 감사의견 추이"
"삼성전자 듀퐁 3단 분해 — ROE 13.07%의 구성 요인"
```

\* 재무지표 분석은 `financial_metrics` tool에서 가능해요 (Phase 1: DART 4 endpoint 통합 — 51 핵심 지표 + 22 alert + 감사의견 추이)

---

## Tool 구조 (16개)

16개 tool은 **회사 → 시점별 주총 → 데이터 탭 → 종합 분석** 으로 흐름.

```
company                            # 기업 식별 + 최근 공시 인덱스
│
├─ Meeting Tools (2)
│  ├─ shareholder_meeting_notice   # 주총 소집공고 (사전, DART)
│  └─ shareholder_meeting_results  # 주총 의결 결과 (사후, KIND)
│
├─ Data Tools (10)
│  ├─ ownership_structure          # 지분 구조 (최대주주/5%/control_map)
│  ├─ dividend                     # 배당 사실 + 분기별 breakdown
│  ├─ financial_metrics            # DART 재무 4 endpoint — 51 지표 + 듀퐁
│  ├─ treasury_share               # 자사주 결정 5종 + 결과 4종 + 사이클 매칭
│  ├─ proxy_contest                # 경영권 분쟁 (위임장/소송/5%)
│  ├─ value_up                     # 밸류업 계획 (약속/이행)
│  ├─ corporate_restructuring      # 합병/분할/주식교환 통합
│  ├─ dilutive_issuance            # 유상증자/CB/BW/감자 통합
│  ├─ related_party_transaction    # 내부거래 (타법인주식 + 공급계약)
│  ├─ corp_gov_report              # 기업지배구조보고서 15지표
│  └─ evidence                     # 공시 원문 링크 (rcept_no → URL)
│
└─ Action Tools (2)
   ├─ proxy_advise_before_meeting  # 주총 사전 안건별 FOR/AGAINST/REVIEW/NO_DATA
   └─ proxy_result_after_meeting   # 주총 사후 결과 보고
```

> 각 tool의 scope·옵션·data source·검증 결과는 **[wiki/tools/README.md](wiki/tools/README.md)** 카탈로그 또는 개별 tool 페이지 (`wiki/tools/{name}.md`) 참조.

### 주요 변화 (2026-05-04~05)
- 17 → 16 tool: `screen_events` drop, `proxy_guideline` archive (internal로 만든 후 호출 X 확인), `shareholder_meeting` → notice + results 분리
- proxy_advise scope **10 → 1** (`decisions`만, raw는 각 data tool 직접 호출)
- treasury_share scope **6 → 2**, 결과 보고서 4종 추가, 결정↔결과 사이클 매칭
- DART 분당 1000회 hard rule (rolling window rate limiter cap 900)
- **사내이사 재직 중 성과 매트릭스 (2x3)** 도입 — 회사 추천 사내이사 자동 FOR 방어 (status quo bias mitigation). ROE/부채비율/CSR × avg/trend 6 cell 채점, bad → AGAINST · weak → REVIEW. KOSPI 100 + KOSDAQ 50 (n=128) 검증 G1 100% / dist target band 충족.
- proxy_advise 응답 한국어 자연화 (`weak_concerns` → "약한 우려" 등)

### 의결권 정책 (vote_style)

`proxy_advise_before_meeting`의 `vote_style` 옵션으로 8 운용사 + NPS 정책에 맞춘 안건별 권고 가능:

| vote_style | 설명 |
|---|---|
| `open_proxy` (default) | OPM 자체 Open Proxy Guideline (12 카테고리, 4 기준: 소수주주 보호 / 거버넌스 투명성 / 장기 가치 / 추적 가능성) |
| `mirae_asset` / `samsung` / `samsung_active` / `kim` | 대형 레거시 4 |
| `truston` / `align_partners` / `cha_partners` | 행동주의 3 |
| `baring` | 외국계 (ISS Korea 참조 사례) |
| `nps` | 국민연금 |

**모든 응답에 `data.usage` 블록**: DART API 호출 수 + MCP tool 호출 수 노출 (분당 1000 한도 — `dart/client.py` rolling window cap 900으로 hard guard).

```
사용 패턴:  company로 시작 → 데이터 탭으로 사실 확인 → action tool로 종합 분석
```

### 도메인별 요약

| 도메인 | 설명 | tool 수 |
|--------|------|---------|
| **회사** | 기업 식별 + 최근 공시 인덱스 | 1 |
| **주총 (사전)** | shareholder_meeting_notice — 안건·이사후보·보수한도·정관변경 (DART) | 1 |
| **주총 (사후)** | shareholder_meeting_results — KIND 의결 결과 | 1 |
| **지분** | 최대주주, 대량보유, control map, 변동신고서 | 1 |
| **배당** | 실지급 배당 사실 + 분기별 breakdown | 1 |
| **자사주** | 결정 5종 (사전) + 결과 4종 (실집행) + 사이클 매칭 (★ 결정-실집행 검증) | 1 |
| **분쟁** | 위임장 경쟁, 소송, 5% 시그널 | 1 |
| **밸류업** | 기업가치 제고 계획, 이행현황 | 1 |
| **재편** | 합병·분할·분할합병·주식교환·이전 결정 | 1 |
| **희석** | 유상증자·CB·BW·감자 발행 결정 | 1 |
| **내부거래** | 타법인주식 거래 + 단일공급계약 | 1 |
| **거버넌스** | 기업지배구조보고서 (15 핵심지표, 2026년부터 KOSPI 전체 의무) | 1 |
| **재무** | DART 재무 4 endpoint 통합 — 51 지표 + 듀퐁 + FCF + NWC + 회계 risk + 감사의견 3년 추이 | 1 |
| **근거** | 공시 원문 링크 제공 | 1 |
| **액션** | proxy_advise_before_meeting (사전 안건별 결정 + facts/risk/citation/근거공고/후보 raw, ralph G2 99.36%) + proxy_result_after_meeting (사후 결과) | 2 |
| | **합계** | **16** |

---

## 의결권 행사 판단

주주총회 안건에 대한 의결권 행사 판단을 요청하면, 아래의 기준에 따라 찬성/반대/검토 의견을 제시해요.

| 안건 유형 | FOR | AGAINST | REVIEW |
|-----------|-----|---------|--------|
| 재무제표 | 감사의견 적정 | 한정/부적정 | 배당성향 극단적 |
| **사외이사 선임** | 독립성 충족 + 결격 없음 | 독립성 미달 / 결격사유 | 겸직 3개+, 부정 뉴스 |
| **사내이사 선임 (연임)** | 결격 없음 + 재직 성과 good/moderate | 재직 성과 **bad** (자본잠식/적자 + 누적 악화) | 재직 성과 **weak** (사용자 검토) |
| 사내이사 선임 (신임) | 결격 없음 (재직 X → 성과 미평가) | 결격사유 발견 | — |
| 보수한도 | 소진율 적정 | 소진율 < 30%인데 인상 | 50%+ 대폭 인상 |
| 정관변경 | 법령 반영 (형식적) | 집중투표 배제 | 이사 정원 축소 |
| 자기주식 | 소각 목적 | 경영권 방어 목적 | 재단 출연 |
| 배당 | 업종 평균 이상 | 이익 증가인데 DPS 감소 | 감액배당 |

### 사내이사 재직 중 성과 매트릭스 (2x3)

회사 추천 사내이사를 결격사유만 보고 자동 FOR 처리하면 status quo 편향이 생겨요. 그래서 **재직 기간 동안의 회사 운영 성과** 를 6 cell로 채점해서 의결권 권고에 반영합니다.

| Metric | avg | trend |
|---|---|---|
| **ROE** (자기자본이익률) | 평균 점수 | 추세 점수 |
| **부채비율** | 평균 점수 | 재직 누적변화 점수 |
| **CSR** (배당+소각/순이익) | 평균 점수 | 추세 점수 |

각 cell: good +2 / moderate +1 / weak 0 / bad -1. 총점 ≥+7 = good / +3~+6 = moderate / 0~+2 = weak / <0 = bad.

**Special rules**: 자본잠식 (full) 시 ROE/leverage avg 자동 bad / 적자 + 환원 활동 시 CSR weak (자본잠식 가속) / 적자 + 환원 자제 시 CSR moderate (보수성).

KOSPI 100 + KOSDAQ 50 (n=128) 검증: G1 classification 노출률 100%, distribution good 29.7% / mod 45.3% / weak 18.0% / bad 7.0% (모든 target band 충족).

---

## 데이터 소스

| 소스 | 용도 | 비고 |
|------|------|------|
| [DART OpenAPI](https://opendart.fss.or.kr/) | 소집공고, 사업보고서, 대량보유 공시 | 필수 (무료 API 키) |
| [KRX KIND](https://kind.krx.co.kr/) | 주총 의결권 행사 결과 | 웹 크롤링 |
| [네이버 뉴스 API](https://developers.naver.com/) | 후보자 부정 뉴스 검색 | 선택 (무료 API 키) |
| [네이버 금융](https://finance.naver.com/) | 주가, 업종명, 배당 시세 | 웹 크롤링 |
| 자산운용사 의결권 행사 공시 | 8 운용사 정책 + 행사내역 (총 17,900+ votes, 익명화) | parsed JSON 정적 보존 (proxy_guideline tool) |

---

## 프로젝트 구조

```
wiki/
  open_proxy_mcp/
    server.py              # FastMCP 서버 (stdio + HTTP)
    tools_v2/              # 16개 tool
    services/              # 도메인별 분석 로직 (tool과 분리)
    dart/client.py         # DART API + KIND 크롤링 + 네이버 + rate limiter
    data/asset_managers/   # 8 운용사 정책 (익명화) + 행사내역 + Open Proxy Guideline + 12 매트릭스
  Dockerfile               # Fly.io 배포용 컨테이너
  fly.toml                 # Fly.io 설정 (nrt 리전, auto-suspend)
  wiki/          # 도메인 지식 위키 (구 wiki/)
```

---

## Disclaimer

OpenProxy는 DART 공시 데이터를 구조화하여 AI에게 제공하는 도구예요. AI는 할루시네이션(hallucination)을 일으킬 수 있고, 부정확한 분석을 제공할 수도 있어요. AI가 제시하는 의견은 개발자 또는 개발자의 소속 단체의 의견이 아니에요. 분석 결과는 참고 목적으로만 사용하시고, 투자 결정이나 의결권 행사의 최종 판단은 반드시 원문 공시와 전문가 검토를 거쳐주세요.

---

## 라이선스

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) -- 비상업적 사용만 허용

이 프로젝트의 코드와 데이터를 사용할 때는 출처를 밝혀주세요. 상업적 목적으로는 사용할 수 없어요.
