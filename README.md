# OpenProxy MCP (OPM)

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-33-orange.svg)](#tool-아키텍처-33개)

[English README](README_ENG.md)

## Why OpenProxy?

패시브 투자의 확대로 주식 오너십의 의미가 희미해지고 있는 지금, 오히려 더 적극적인 주주권 행사와 더 깊이 있는 경영 분석이 필요한 시대입니다. 하지만 주주총회 안건 설명은 불친절하고, 공시 내용은 방대하며, 분석에 필요한 전문 지식의 벽은 높습니다.

**OpenProxy는 AI를 활용해 이 장벽을 허뭅니다.** 100페이지가 넘는 DART 공시 문서를 구조화된 데이터로 변환하고, 누구나 몇 초 만에 기관투자자 수준의 의결권 분석에 접근할 수 있도록 만들었습니다.

![OpenProxy MCP 비교](screenshot/open-proxy-mcp%20output%20kor.png)

---

## 빠른 시작

### 1단계: DART API 키 발급 (필수)

OpenProxy의 모든 데이터는 DART OpenAPI에서 가져옵니다. **본인의 API 키가 있어야 사용할 수 있습니다.**

1. [DART OpenAPI](https://opendart.fss.or.kr/) 접속 -> 회원가입
2. 인증키 신청 -> 발급 (무료, 즉시 발급)

### 2단계: 연결

API 키를 발급받았으면, 아래 두 가지 방법 중 선택하세요.

#### 방법 A: 원격 서버 (설치 불필요, 30초)

URL 끝에 발급받은 DART API 키를 붙여서 연결합니다. 키는 서버에서만 사용되며, AI에게 노출되지 않습니다.

**claude.ai 웹:**

1. [claude.ai](https://claude.ai) 접속 -> 설정 -> 커넥터
2. "커스텀 커넥터 추가" 선택
3. 이름: `open-proxy-mcp`, URL 입력:
```
https://open-proxy-mcp.fly.dev/mcp?opendart=발급받은_키
```
4. "추가" 클릭 -> 33개 tool 자동 인식
5. 추가된 커넥터의 구성 -> 권한에서 **"항상 허용"** 선택 (매번 승인 없이 tool 자동 실행)

#### 방법 B: 로컬 설치

로컬에서 실행하면 DART 외에 추가 API 키도 설정할 수 있습니다 (후보자 뉴스 검색, OCR fallback 등).

[로컬 설치 가이드](docs/connect.md) 참조

### 사용 예시

연결 후 자연어로 질문하면 됩니다:

```
"삼성전자 주주총회 안건 분석해줘"
"KB금융 사외이사 후보 독립성 검토해줘"
"현대차 보수한도 적정성 판단해줘"
"삼성전자 지분 구조 보여줘"
"SK하이닉스 배당 추이 알려줘"
"고려아연 프록시파이트 분석해줘"
```

---

## Tool 아키텍처 (33개)

33개 tool은 5단계 Tier로 구성됩니다. AI는 Tier 1부터 순서대로 호출하며, 필요에 따라 하위 Detail tool로 내려갑니다.

```
                            ┌──────────────────────┐
                            │    corp_identifier    │  Tier 1 Entity
                            │  기업명/ticker 식별    │  "삼성전자" -> 005930
                            └──────────┬───────────┘
                                       │
                            ┌──────────▼───────────┐
                            │      tool_guide      │  Tier 2 Context
                            │   사용법 + 판단 기준   │
                            └──────────┬───────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
    ┌─────────▼─────────┐  ┌──────────▼──────────┐  ┌──────────▼──────────┐
    │    agm_search      │  │     div_search      │  │    proxy_search     │  Tier 3
    │   소집공고 검색     │  │    배당공시 검색     │  │   위임장공시 검색    │  Search
    └─────────┬─────────┘  └──────────┬──────────┘  └──────────┬──────────┘
              │                       │                        │
    ┌─────────▼──────────────┐ ┌──────▼───────────┐ ┌──────────▼──────────┐
    │ agm_pre_analysis       │ │div_full_analysis │ │    proxy_fight      │  Tier 4
    │ agm_post_analysis      │ │  배당 종합 분석   │ │  프록시파이트 감지   │  Orchestrate
    │ ownership_full_analysis│ └──────┬───────────┘ └──────────┬──────────┘
    │ governance_report      │        │                        │
    └─────────┬──────────────┘        │                        │
              │                       │                        │
    ┌─────────▼───────────────────────▼────────────────────────▼──────────┐
    │                           Tier 5 Detail                            │
    │                                                                    │
    │  AGM (12)              OWN (5)               DIV (2)   PRX (2)    │
    │  ├ agenda_xml          ├ ownership_major     ├ detail  ├ detail   │
    │  ├ financials_xml      ├ ownership_total     └ history └ direction│
    │  ├ personnel_xml       ├ ownership_treasury                       │
    │  ├ aoi_change_xml      ├ ownership_block     NEWS (1)             │
    │  ├ compensation_xml    └ ownership_treasury_tx└ news_check        │
    │  ├ treasury_share_xml                                             │
    │  ├ capital_reserve_xml                                            │
    │  ├ retirement_pay_xml                                             │
    │  ├ info / corrections / result / items                            │
    │  └ 각 파서 _xml / _pdf / _ocr fallback                            │
    └───────────────────────────────────────────────────────────────────┘
```

### 도메인별 요약

| 도메인 | 설명 | tool 수 |
|--------|------|---------|
| **AGM** | 주총 소집공고 파싱 -- 안건, 재무제표, 이사선임, 정관변경, 보수한도, 자기주식 등 | 14 |
| **OWN** | 지분 구조 -- 최대주주, 주식총수, 자사주, 5% 대량보유자 | 6 |
| **DIV** | 배당 분석 -- 배당 상세, 3개년 추이, 배당성향/수익률 | 4 |
| **PRX** | 프록시파이트 -- 위임장 권유 검색, 양측 비교 | 4 |
| **NEWS** | 후보자 부정 뉴스 검색 | 1 |
| **CORP** | 기업 식별 (ticker/corp_code 변환) | 1 |
| **GUIDE** | 전체 tool 사용 가이드 | 1 |
| **GOV** | 거버넌스 종합 리포트 (AGM+OWN+DIV 통합) | 1 |
| | **합계** | **33** |

---

## Fallback 파싱

원격 서버는 XML 파싱만 지원합니다. 로컬 설치 시 PDF/OCR fallback을 사용할 수 있습니다. 상세는 [로컬 설치 가이드](docs/connect.md#fallback-파싱)를 참조하세요.

---

## 의결권 행사 판단

파싱된 데이터를 기반으로 안건별 의결권 행사 방향을 판정합니다.

| 안건 유형 | FOR | AGAINST | REVIEW |
|-----------|-----|---------|--------|
| 재무제표 | 감사의견 적정 | 한정/부적정 | 배당성향 극단적 |
| 이사 선임 | 사외이사 독립성 충족 | 독립성 미달 | 겸직 3개+, 부정 뉴스 |
| 보수한도 | 소진율 적정 | 소진율 < 30%인데 인상 | 50%+ 대폭 인상 |
| 정관변경 | 법령 반영 (형식적) | 집중투표 배제 | 이사 정원 축소 |
| 자기주식 | 소각 목적 | 경영권 방어 목적 | 재단 출연 |
| 배당 | 업종 평균 이상 | 이익 증가인데 DPS 감소 | 감액배당 |

---

## 데이터 소스

| 소스 | 용도 | 비고 |
|------|------|------|
| [DART OpenAPI](https://opendart.fss.or.kr/) | 소집공고, 사업보고서, 대량보유 공시 | 필수 (무료 API 키) |
| [KRX KIND](https://kind.krx.co.kr/) | 주총 의결권 행사 결과 | 웹 크롤링 |
| [네이버 뉴스 API](https://developers.naver.com/) | 후보자 부정 뉴스 검색 | 선택 (무료 API 키) |
| [네이버 금융](https://finance.naver.com/) | 주가, 업종명, 배당 시세 | 웹 크롤링 |

---

## 프로젝트 구조

```
open-proxy-mcp/
  open_proxy_mcp/
    server.py              # FastMCP 서버 (stdio + HTTP)
    tools/                 # 33개 tool (AGM/OWN/DIV/PRX/NEWS/CORP/GUIDE/GOV)
    dart/client.py         # DART API + KIND 크롤링 + 네이버 + rate limiter
  Dockerfile               # Fly.io 배포용 컨테이너
  fly.toml                 # Fly.io 설정 (nrt 리전, auto-suspend)
  wiki/                    # 도메인 지식 위키 (68페이지)
```

---

## 라이선스

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) -- 비상업적 사용만 허용

본 프로젝트의 코드와 데이터를 사용할 때는 출처를 표시해야 하며, 상업적 목적으로 사용할 수 없습니다.
