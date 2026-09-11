<div align="center">

# OpenProxy MCP

[![Stars](https://img.shields.io/github/stars/MarcoYou/open-proxy-mcp?label=stars&color=f5c518&logo=github&logoColor=white)](https://github.com/MarcoYou/open-proxy-mcp)
[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-lightgrey.svg)](https://polyformproject.org/licenses/noncommercial/1.0.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/tools-32-orange.svg)](#도구-구조-32개)
[![Release](https://img.shields.io/badge/release-v2.6.0-blue.svg)](docs/RELEASE_NOTES.md)
[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-ea4aaa?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/MarcoYou)

[English](README_ENG.md) · [简体中文](README_ZH.md)

[빠른 시작](#빠른-시작) · [이렇게 물어보세요](#이렇게-물어보세요) · [주요 기능](#주요-기능) · [도구 구조](#도구-구조-32개) · [읽을 때 주의](#읽을-때-주의) · [데이터 출처](#데이터-소스)

</div>

## Why OpenProxy?

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="screenshot/opm-readme-particle-flow-dark-ko-20260905.png">
  <source media="(prefers-color-scheme: light)" srcset="screenshot/opm-readme-particle-flow-light-ko-20260905.png">
  <img alt="공시 데이터가 AI 재무분석과 의결권 판단으로 구조화되는 과정" src="screenshot/opm-readme-particle-flow-light-ko-20260905.png">
</picture>

**안건은 한 줄이지만, 판단에는 회사 전체가 필요합니다.**

OpenProxy는 주주총회 의결권 분석에서 시작했습니다. 재무제표, 지분 구조, 배당 이력, 이사회와 관련 법령을 함께 읽기 위해 만든 기능은 DART 공시 전반을 분석하는 범용 엔진으로 확장됐습니다. 재무 분석부터 의결권 권고까지, AI가 판단과 원문 근거를 함께 제시합니다.

## 빠른 시작

**설치 없이 DART API 키 하나로 연결합니다.**

### 1. 무료 API 키 받기

DART는 한국 기업의 전자공시 시스템입니다. [DART OpenAPI](https://opendart.fss.or.kr/)에서 회원가입 후 무료 인증키를 신청합니다.

### 2. AI 서비스에 연결하기

커넥터 또는 앱 추가 화면의 서버 주소에 아래 URL을 입력합니다.

```
https://open-proxy-mcp.fly.dev/mcp?opendart=발급받은_OpenDART_API_키
```

> 서버 주소는 커넥터 설정에만 입력하세요. OpenProxy는 키 원문을 저장하지 않으며 로그에서도 가립니다.

| 서비스 | 연결 경로 | 이용 범위 |
|---|---|---|
| [**Claude**](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) | `Customize → Connectors → + → Add custom connector` | Free(1개)·Pro·Max. Team/Enterprise는 관리자 추가 |
| [**ChatGPT**](https://developers.openai.com/api/docs/guides/developer-mode) | `Settings → Security and login → Developer mode`, 이후 `Plugins → +` | 웹 Plus·Pro·Business·Enterprise·Education |
| [**Perplexity**](https://www.perplexity.ai/help-center/en/articles/13915507-adding-custom-remote-connectors.html) | `Account settings → Connectors → + Custom connector → Remote` | Pro·Max·Enterprise |

이름은 `open-proxy-mcp`로 지정하고 새 채팅에서 선택합니다. 메뉴 제공 범위는 계정 설정에 따라 달라질 수 있습니다.

#### Claude에서 프롬프트와 리소스 불러오기

채팅 입력창의 `+`를 누르고 `커넥터 → open-proxy-mcp에서 추가`를 선택합니다.

[Claude 한국어 화면에서 메뉴 위치 보기](screenshot/claude-resource-picker-ko-20260907.png)

- `Company Snapshot` — 회사명이나 종목코드로 분석 흐름을 시작하는 MCP 프롬프트
- `OpenProxy Feature Guide` — 현재 등록된 도구와 기능을 보여주는 `tools_guide` 리소스
- `Open Proxy Guideline` — 의결권 판단 정책 원문 리소스

### 3. 첫 질문 보내기

먼저 `삼성전자 회사 정보와 최근 공시 3건을 보여줘`라고 물어보세요. 회사와 공시 목록이 나오면 연결된 것입니다. 도구 이름을 알 필요 없이 이어서 질문할 수 있습니다.

주제별 질문 예시는 아래 [이렇게 물어보세요](#이렇게-물어보세요)에 모아 두었습니다. 도구별 상세 스키마는 [도구 카탈로그](wiki/tools/README.md)에 있습니다.

`OpenProxy Feature Guide`는 서버에 등록된 도구 목록에서 자동으로 구성됩니다. `Company Snapshot`은 사업 구조·3년 확정 실적·제공되는 연간 예상치 최대 2년·가격·지분·배당·최근 공시를 연결하고, 더 확인할 질문까지 정리하도록 안내합니다. 표에서 확정(A)과 예상(E)을 구분하며, 시각화가 가능한 클라이언트에는 매출 막대·영업이익 선 차트를 요청합니다.

---

## 이렇게 물어보세요

도구 이름을 외울 필요가 없습니다. 하고 싶은 말을 그냥 하면 AI 가 알아서 고릅니다.
아래는 실제로 되는 질문들입니다.

<details open>
<summary><b>🗳️ 주주총회·의결권이 궁금할 때</b></summary>

> - LG화학 다음 정기 주주총회 안건과 안건별 의결권 의견을 근거와 함께 알려줘
> - 카카오 이번 주총 이사 선임 안건, 후보별로 짚어야 할 점이 뭐야?
> - 삼성전자 작년 주총 안건이 실제로 어떻게 표결됐어?
> - 이 안건을 판단할 근거가 되는 정관 조항과 상법 조문 찾아줘

안건마다 **찬성·반대·검토 필요** 의견과 공시·정책·법령 근거가 나옵니다. 표결 대상이 아닌 안건과
자료가 부족한 안건은 따로 구분합니다. 반대가 한 건도 없는 회사가 정상입니다 — [왜 그런지](#읽을-때-주의)

</details>

<details open>
<summary><b>📊 실적이 궁금할 때</b></summary>

> - 삼성전자 최근 3년 실적과 향후 2개년 컨센서스를 비교해줘
> - SK하이닉스 분기 실적 추세를 영업현금흐름까지 같이 보여줘
> - 현대차 잠정실적 나온 거 있어? 확정치랑 무엇이 달라?
> - LG화학 수익성을 듀퐁 분해로 뜯어줘

확정 실적은 DART 재무 API, 잠정치는 잠정실적 공시, 예상치는 애널리스트 컨센서스에서 각각 옵니다.
**셋은 기준이 다르므로 한 줄에 놓지 않습니다** — 표에서 확정(A)과 예상(E)을 나눠 표시합니다.

</details>

<details>
<summary><b>💹 지금 가격이 궁금할 때</b></summary>

> - 네이버 PER·PBR 이 과거 대비 어느 수준이야?
> - 포스코홀딩스 내년·내후년 추정치로 포워드 PER 을 계산해줘
> - 삼성전자 2024년 말 시점의 밸류에이션은 어땠어?

시총·PER·PBR·PSR·배당수익률을 과거 구간과 함께 봅니다. **실적 기준일과 주가 기준일은 따로
표시됩니다** — 같은 날이 아닙니다.

</details>

<details>
<summary><b>🏭 무엇으로 버는지 궁금할 때</b></summary>

> - 한화솔루션이 무엇으로 돈을 벌어? 사업부문별로 나눠줘
> - 고려아연 가동률과 원재료 가격 추이 알려줘
> - 태광산업이 들고 있는 계열사 지분과 잉여자산 가치를 계산해줘
> - 이 회사 수주잔고가 얼마나 남았어?

부문별·제품별·지역별 매출은 **같은 매출을 나눈 서로 다른 축이라 합산하지 않습니다.**
보유지분은 상장분 시가와 비상장분 장부가를 나눠 셉니다.

</details>

<details>
<summary><b>🧭 지분과 주주환원이 궁금할 때</b></summary>

> - 삼성물산 최대주주와 특수관계인 지분 알려줘
> - KB금융 최근 3년 배당 추이와 배당성향을 보여줘
> - 자사주를 매입한다고 한 회사 중에 실제로 소각까지 한 곳이 있어?
> - 밸류업 공시에 적은 내용과 실제 집행이 얼마나 맞아?

배당은 **주당값과 총액의 기준이 다릅니다.** 주당배당금·시가배당률은 종류주식마다 한 주 기준이고,
배당총액·배당성향은 회사 전체(전 종류 합산·연결)입니다 — [자세히](#읽을-때-주의)

</details>

<details>
<summary><b>⚔️ 무엇이 달라졌는지 궁금할 때</b></summary>

> - 오늘 아침 공시 중에 중요한 것만 정리해줘
> - 최근에 경영권 분쟁 신호가 잡힌 회사가 있어?
> - 두산로보틱스 유상증자·전환사채 발행 이력을 보여줘
> - 이 회사에 소송이나 제재 이력이 있어?

전체시장 스캔은 **최근 3개월이 한도**입니다. 그 밖의 기간까지 「사건이 없었다」고 말하지 않습니다.

</details>

<details>
<summary><b>🔗 근거를 확인하고 싶을 때</b></summary>

> - 방금 그 수치가 어느 공시 몇 번 항목에서 나온 거야?
> - 이 접수번호 원문은 어디서 볼 수 있어?
> - 정관의 이 조항이 상법 몇 조에 걸려? 강행규정이야?

모든 수치에 접수번호가 붙고, 접수번호는 DART 원문 열람 주소로 바뀝니다. 정관과 법령은 양방향으로
조회하며 이 조회에는 DART API 를 쓰지 않습니다.

</details>

### 알아두면 편한 것

- **회사는 한 번만 확정하면 됩니다** — 처음 조회에서 잡힌 이름·종목코드·고유번호를 이어지는 질문이 그대로 씁니다.
- **후보가 여럿이면 되묻습니다** — 「한국철강」처럼 겹치는 이름은 고르기 전까지 다음 조회로 넘어가지 않습니다.
- **응답의 `status` 와 `warnings` 를 먼저 읽습니다** — 무엇을 못 찾았고 어떤 기준으로 대체했는지가 거기 적힙니다.
- **못 찾은 값은 「읽은 공시에서 찾지 못했다」입니다** — 0 도 아니고 「없다」도 아닙니다.

---

## 주요 기능

**공시를 읽고, 숫자를 연결하고, 판단 근거까지 남깁니다.**

| 분석 영역 | 핵심 질문 | OpenProxy가 제공하는 답 |
|---|---|---|
| 🗳️ [주총·의결권](docs/features/proxy-voting.md) | 이 안건에 어떻게 투표할까? | **찬성·반대·검토 필요** 의견과 공시·정책·법령 근거. 표결 대상이 아닌 안건과 자료가 부족한 안건도 구분 |
| 📊 [재무·실적](docs/features/financials.md) | 실적은 어떻게 변했나? | 확정·[잠정](docs/features/provisional-earnings.md)·컨센서스 비교, 수익성·현금흐름·듀퐁 분석 |
| 💹 [가치평가·추정치](docs/features/price_multiple_data.md) | 현재 가격에 무엇이 반영됐나? | 과거·선행 PER/PBR/PSR, 배당수익률, [내년·내후년 추정치](wiki/tools/forward_estimates_data.md) |
| 🏭 [사업·보유자산](docs/features/business-details.md) | 무엇으로 벌고 무엇을 보유하나? | 사업부문·가동률·원가·수주잔고와 [잉여자산·보유지분 NAV](docs/features/asset-holdings.md) |
| 🧭 [지분·주주환원](docs/features/ownership.md) | 누가 지배하고 자본은 어디로 가나? | 소유구조, 배당·자사주 소각, [밸류업 약속과 실제 집행](docs/features/shareholder-return.md) |
| 🔔 [시장·리스크](wiki/tools/screener.md) | 오늘 무엇이 달라졌나? | 시장 공시 디제스트, [경영권 분쟁](docs/features/control-contest.md)·거래·희석·[리스크 이벤트](docs/features/risk-events.md) 추적 |

이 여섯 가지 분석 흐름을 출처 추적과 정관↔법령 조회까지 **총 32개 도구**가 뒷받침합니다. 전체 목록은 [도구 구조](#도구-구조-32개)에서 확인할 수 있습니다.

**거버넌스 검토 파일럿** — `governance_screen`은 지정한 최대 30개사의 공시 원문을 모아 연결된 AI가 소수주주 대우·이해상충·이사회 책임 등을 판독하고, 근거와 중요도에 따라 검토 순서를 정리합니다. 공개매수·행동주의·소송이 있다는 사실만으로 부정 평가하지 않습니다. 결과는 **부분 근거에 대한 LLM 평가 · 사람 미검토**로 표시하며, 누락은 해당 항목만 알리고 다른 검토를 계속합니다. `since`·`known_receipts`로 새 공시 목록을 좁혀 다시 호출할 수 있습니다. 예약 실행이나 실제 투표는 만들지 않습니다. 이 브랜치의 파일럿 기능이며 운영 배포 여부는 별도로 확인해야 합니다.

---

## 도구 구조 (32개)

분류는 [wiki/tools 카탈로그](wiki/tools/README.md)의 「무엇을 알고 싶을 때 무엇을 쓰나」 표와 같다 — 그 표가 정본이다.

| 분류 | Tools | 역할 |
|---|---|---|
| 🏢 기본 — 회사 찾기 | [`company`](wiki/tools/company.md) | 회사 식별 + 최근 공시 목록 — 모든 분석의 출발점 |
| 🔔 공시 스캔·검토 | [`screener`](wiki/tools/screener.md), [`governance_screen`](wiki/tools/governance_screen.md) | 전체시장 공시 디제스트 · 지정 기업 공시 원문과 LLM 거버넌스 검토 순서(파일럿) |
| 🗳️ 주주총회·의결권 | [`shareholder_meeting_notice`](wiki/tools/shareholder_meeting_notice.md), [`shareholder_meeting_results`](wiki/tools/shareholder_meeting_results.md), [`proxy_advise_before_meeting`](wiki/tools/proxy_advise_before_meeting.md), [`proxy_guideline`](wiki/tools/proxy_guideline.md) | 소집공고(전)·결과(후) · 안건별 찬성/반대/검토 보조 · 판단 기준 문서 원문 |
| 💰 지분·재무·지배구조 | [`ownership_structure`](wiki/tools/ownership_structure.md), [`financial_metrics`](wiki/tools/financial_metrics.md), [`provisional_earnings`](wiki/tools/provisional_earnings.md), [`business_details`](wiki/tools/business_details.md), [`asset_holdings`](wiki/tools/asset_holdings.md), [`price_multiple_data`](wiki/tools/price_multiple_data.md), [`forward_estimates_data`](wiki/tools/forward_estimates_data.md), [`trading_data`](wiki/tools/trading_data.md), [`corp_gov_report`](wiki/tools/corp_gov_report.md), [`director_board`](wiki/tools/director_board.md) | 지분 구조 · 확정/잠정 실적 · 사업의 내용 · 자산주 · PER/PBR · 컨센서스 · 시세·시총 · 지배구조보고서 · 이사회 |
| 🎁 주주환원·자본 | [`dividend_disclosure`](wiki/tools/dividend_disclosure.md), [`dividend_data`](wiki/tools/dividend_data.md), [`treasury_share`](wiki/tools/treasury_share.md), [`value_up`](wiki/tools/value_up.md), [`shareholder_commitment`](wiki/tools/shareholder_commitment.md), [`corporate_restructuring`](wiki/tools/corporate_restructuring.md), [`dilutive_issuance`](wiki/tools/dilutive_issuance.md) | 배당 공시·시계열 · 자기주식 · 밸류업 · 약속 vs 이행 · 합병/분할 · 증자/CB/BW/감자 |
| ⚔️ 분쟁·거래·리스크 | [`proxy_contest`](wiki/tools/proxy_contest.md), [`corporate_deals`](wiki/tools/corporate_deals.md), [`order_contracts`](wiki/tools/order_contracts.md), [`risk_events`](wiki/tools/risk_events.md), [`financial_notes`](wiki/tools/financial_notes.md), [`director_news`](wiki/tools/director_news.md) | 경영권 분쟁 신호 · 지분 인수/매각 · 수주·공급계약 · 리스크 사건 · 금융사 주석 · 이사 후보 뉴스 |
| 🔗 근거·참조 | [`evidence`](wiki/tools/evidence.md), [`law_lookup`](wiki/tools/law_lookup.md) | 접수번호 → 원문 열람 URL · 정관↔법령 양방향 조회 (API 0콜) |

> 도구별 예시 질문·상세 스키마·데이터 출처 → [wiki/tools 카탈로그](wiki/tools/README.md) (각 도구 페이지의 「사용법」 절에 자연어 예시)

### 의결권 정책

**정책의 반대 기준이 곧 엔진의 자동 반대 조건은 아닙니다.** 기본 엔진은 추가 판단이 필요한 우려를 `검토 필요(REVIEW)`로 두며 출석률을 판정 조건에 반영하지 않습니다. 별도로 선택하는 v2 파일럿은 원문에 연결된 LLM 평가로 직전 완료 사업연도 출석·독립성을 후보 권고에 적용합니다. [파일럿 입력·적용 범위](wiki/tools/proxy_advise_before_meeting.md)를 확인하세요. `proxy_guideline`에서 인용된 절을, `0-A`에서 기본 정책과 엔진의 대응표를 확인할 수 있습니다. [판정·회차·정보 기준일 읽는 법](docs/features/proxy-voting.md).

`proxy_advise_before_meeting`은 OPM 자체 **Open Proxy Guideline**을 기본 정책으로 사용합니다. 판단 기준은 소수주주 보호, 거버넌스 투명성, 장기 가치, 추적 가능성입니다. 주요 자산운용사의 거래소 공시 의결권 행사 내역과 국민연금의 공개 행사 내역을 교차 검토에 활용합니다. 모든 응답에는 DART와 도구 호출 수를 담은 `data.usage`가 포함됩니다(DART 분당 1,000회 한도, 서버 안전 제한 910회).

**재무 기준 확인** — 승인 대상 연도의 확정치와 소집공고 잠정치, 직전 확정치를 구분해 읽습니다. 잠정치가 모든 지표를 대체하는 것은 아니므로 응답의 연도·출처·잠정 여부를 확인하세요. 잠정치에 따른 자본잠식 평가는 감사 후 재무제표를 요구하는 규정 판정을 대신하지 않습니다. 정보 기준일과 사후 자료 포함 여부는 [기능 안내](docs/features/proxy-voting.md)를 따릅니다.

---

## 읽을 때 주의

공시는 항목마다 기준이 다릅니다. 아래 열한 가지를 모르면 맞는 값을 틀리게 읽습니다.

| 무엇 | 왜 |
|---|---|
| **「자료 없음」은 「없다」가 아닙니다** | 읽은 공시에서 못 찾았다는 뜻입니다. 응답의 `status`·`warnings` 에 무엇을 못 찾았고 무엇으로 대체했는지 적힙니다 |
| **반대 0건이 정상입니다** | 정책의 반대 기준이 곧 엔진의 자동 반대 조건은 아닙니다. 추가 판단이 필요한 우려는 **검토 필요(REVIEW)** 로 둡니다 |
| **배당은 주당값과 총액의 기준이 다릅니다** | 주당배당금·시가배당률은 **종류주식마다 한 주** 기준이고, 배당총액·배당성향은 **회사 전체**(전 종류 합산·연결)입니다. 나란히 놓고 나누면 안 됩니다 |
| **확정·잠정·추정을 한 줄에 놓지 마세요** | 출처가 각각 DART 재무 API·잠정실적 공시·애널리스트 컨센서스입니다. 표에서는 확정 A, 예상 E 로 나눕니다. 잠정치가 모든 지표를 대체하지도 않습니다 |
| **연결과 별도, 귀속을 확인하세요** | 순이익의 귀속(지배주주 대 전체)이나 연결 여부가 다르면 성장률을 계산하지 않고 차이만 표시합니다 |
| **사업연도는 결산월을 따릅니다** | 12월 결산이 아닌 회사는 달력 연도와 어긋납니다. 신영증권(001720)의 `2025-06-30` 은 FY2026-Q1 입니다 |
| **금융사에는 없는 지표가 있습니다** | 매출이나 일반기업용 비율은 **미제공**이지 0 이 아닙니다. 영업이익·순이익과 그 업종의 건전성 자료로 읽습니다 |
| **실적 기준일과 주가 기준일은 다릅니다** | 포워드 배수는 추정치 기준일과 주가 기준일을 따로 답니다. 과거 수치를 오늘의 값으로 말하지 않습니다 |
| **접수번호 앞 두 자리가 출처를 가릅니다** | `00` 으로 시작하면 DART 정기공시(소집공고), `80` 이면 거래소 수시공시(주총결과)입니다 |
| **전체시장 스캔은 3개월이 한도입니다** | 그 밖의 기간까지 「사건이 없었다」고 주장하지 않습니다. 회사를 특정하면 더 넓게 봅니다 |
| **거버넌스 검토는 사람이 보지 않은 결과입니다** | `governance_screen` 은 **부분 근거에 대한 LLM 평가 · 사람 미검토**로 표시합니다. 공개매수·행동주의·소송이 있다는 사실만으로 부정 평가하지 않습니다 |

<details>
<summary><b>자세히 — 숫자와 근거</b></summary>

<br>

- **종류주식이 보통주 배당을 덮던 문제**(2026-09-06 수정) — 「우선주」 글자가 없는 종류주식 표기
  (「종류주식」·「1종 종류주식」·「전환주」 등, 코스피 원장 235행)를 옛 규칙이 보통주로 읽었습니다.
  한국금융지주 FY2024 는 보통주 3,980원이 「1종 종류주식」 4,042원으로, 두산은 2,000원이 2,050원으로
  나갔고 현재가 기준 수익률과 배당수익률까지 함께 틀렸습니다. 지금은 분류기 하나를 기말 요약과
  다년 추이가 같이 씁니다 → [`dividend_disclosure`](wiki/tools/dividend_disclosure.md)
- **DART 는 API 키 하나당 분당 1,000회**가 한도이고, 초과하면 그 키가 두세 시간 막힙니다.
  서버는 910회에서 스스로 멈춥니다. 응답의 `data.usage` 에 그 요청이 쓴 DART 호출 수가 들어 있습니다.
- **웹 원문 파싱은 프로세스 하나의 시계**로 돌립니다 — 요청 간 0.4~1초 무작위, 분당 40건.
  차단 신호가 잡히면 1~2초로 넓힙니다. 차단은 IP 기준이라 그 머신 전체가 막힙니다.
- **분류 체계가 둘입니다** — 산업분류(KSIC)와 DART 공시유형(`pblntf_ty`). 공시 검색은 유형으로 먼저
  거르며, 회사를 특정하지 않은 시장 검색은 3개월까지만 봅니다.
- **정관과 법령 조회에는 DART API 를 쓰지 않습니다.** 법령 원문은 국가법령정보센터 기반 자료를
  주간으로 동기화해 둡니다 → [`law_lookup`](wiki/tools/law_lookup.md)
- **사용자의 조회 결과는 저장하지 않습니다.** 캐시와 시장 스냅샷, 호출량 집계만 남습니다.

</details>

---

## 데이터 소스

| 소스 | 용도 | 비고 |
|------|------|------|
| [DART OpenAPI](https://opendart.fss.or.kr/) | 정기·주요 공시 메타 + 재무 API + 배당·자사주·지분 등 정형 데이터 | **필수** — 무료 API 키. 분당 최대 1,000회, 서버 안전 제한 910회 |
| DART 웹 (`dart.fss.or.kr`) | 공시 본문 파싱 (소집공고·주요사항보고서 등) | 요청 간 0.4–1초 무작위 대기, 분당 40건. 차단 신호 뒤에는 1–2초 |
| [KRX KIND](https://kind.krx.co.kr/) | 거래소 공시 보조 확인 | 보조 소스 |
| [국가법령정보센터](https://www.law.go.kr/) 기반 법령 원문 | 정관 변경·의결권 판단의 법령 근거 조회 | [legalize-kr](https://github.com/legalize-kr/legalize-kr)에서 주간 동기화 |
| 주요 자산운용사의 거래소 공시 의결권 행사 내역과 국민연금 공개 행사 내역 | 의결권 판단 교차 검토 | 사전 수집·구조화한 공개 자료 |

---

## 릴리즈 노트

버전별 변경 이력 → **[docs/RELEASE_NOTES.md](docs/RELEASE_NOTES.md)**

---

## 보안

취약점은 **공개 이슈 대신** [SECURITY.md](SECURITY.md) 의 절차로 알려 주세요 — 특히 API 키가 새는 경로.

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
