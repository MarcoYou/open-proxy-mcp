---
type: analysis
title: screen_events discovery tool 설계 + 전수조사
tags: [discovery, screener, screen_events, dart, market-wide]
related: [OpenProxy-MCP, pblntf-ty-필터링, dart-kind-disclosure-taxonomy, MCP-개발-lessons-learned]
date: 2026-04-19
---

# screen_events 설계

v2 최초 **discovery tool**. 기존 data tool은 모두 `company`를 입력으로 받는 company-centric인데, `screen_events`는 **filing-centric** — 이벤트(공시 유형) → N개 기업 목록을 역조회한다.

## 동기

Claude 웹 커넥터는 대화당 tool 호출이 ~25-45회로 제한된다. "최근 임시주총 소집한 KOSPI 기업 찾아줘" 같은 요청을 기존 tool로 처리하려면 200종목을 순회해야 하는데, 웹에서는 불가능. `screen_events`는 DART list.json을 1회 호출로 N개 기업을 추려서 이 제약을 회피한다.

상태 기반 스크리닝(자사주 10% 이상 기업 등)은 Claude Code CLI에서 기존 tool을 루프로 돌려서 처리하는 것으로 역할 분담.

## 설계 원칙

- **단일 tool, event_type enum**: 도메인별 5-6개 tool로 쪼개면 AI가 "무엇을 써야 할지" 혼란 (MCP lesson #1). 하나의 tool에 enum으로 통합.
- **얇은 결과**: rcept_no, 기업명, ticker, 제목, 날짜, market만. 원문 파싱은 기존 data tool로 drill-down.
- **market 필터는 DART 서버단**: list.json의 `corp_cls` 파라미터로 Y/K/N/E 매핑 → 응답 크기 자체를 줄여 rate/context 절약.
- **키워드 가정 금지**: 실제 DART report_nm을 관찰한 뒤에 키워드 확정 (MCP lesson #4).

## event_type 카탈로그 (14종)

| event_type | pblntf_tys | 대표 키워드 | 실제 report_nm 예시 |
|-----------|-----------|------------|---------------------|
| `shareholder_meeting_notice` | E | 주주총회소집공고 | `주주총회소집공고`, `[기재정정]주주총회소집공고` |
| `major_shareholder_change` | I, B | 최대주주변경 | `최대주주변경을수반하는주식양수도계약체결` |
| `ownership_change_filing` | I | 최대주주등소유주식변동신고서 | `최대주주등소유주식변동신고서` |
| `block_holding_5pct` | D | 주식등의대량보유상황보고서 | `주식등의대량보유상황보고서(일반)` |
| `executive_ownership` | D | 임원ㆍ주요주주특정증권등소유상황보고서 | 동일 |
| `treasury_acquire` | B, I | 자기주식취득결정 | `주요사항보고서(자기주식취득결정)` |
| `treasury_dispose` | B, I | 자기주식처분결정 | `주요사항보고서(자기주식처분결정)` |
| `treasury_retire` | I, B | 주식소각결정 | `주식소각결정` (자기주식소각 접두어 없음) |
| `proxy_solicit` | D | 의결권대리행사권유 | `의결권대리행사권유참고서류` |
| `litigation` | I, B | 소송등의제기 | `소송등의제기ㆍ신청` |
| `management_dispute` | I, B | 경영권분쟁소송 | `소송등의제기ㆍ신청(경영권분쟁소송)` |
| `value_up_plan` | I | 기업가치제고계획 | `기업가치제고계획(자율공시)` |
| `cash_dividend` | I | 현금ㆍ현물배당결정 | `[기재정정]현금ㆍ현물배당결정` |
| `stock_dividend` | I | 주식배당결정 | `[기재정정]주식배당결정` |

## 전수조사 결과 (2026-04-19, 최근 30일, market=all, max_results=10)

| event_type | 결과 | 비고 |
|-----------|------|------|
| shareholder_meeting_notice | 10건 | 정상 |
| major_shareholder_change | 10건 | 정상 |
| ownership_change_filing | 10건 | 신한지주, 한국콜마 등 |
| block_holding_5pct | 10건 | 정상 |
| executive_ownership | 10건 | 정상 |
| treasury_acquire | 10건 | 두산에너빌리티 등 |
| treasury_dispose | 10건 | 정상 |
| treasury_retire | 10건 | PS일렉트로닉스, 대림바스 |
| proxy_solicit | 10건 | 롯데케미칼 등 |
| litigation | 10건 | 정상 |
| management_dispute | 10건 | 정상 |
| value_up_plan | 10건 | 메디톡스 등 |
| cash_dividend | 10건 | 정상 |
| stock_dividend | 1건 | 드문 이벤트, 정상 |

**14/14 통과.**

## 초기 설계에서 수정된 사항

### annual_meeting / extraordinary_meeting 통합 제거
- 원안: 정기/임시 2개 event_type 분리
- 문제: DART report_nm은 `주주총회소집공고` 단일 포맷이며, 제목에 "임시/정기" 키워드가 전혀 포함되지 않음
- 본문 파싱은 screener 컨셉(가벼운 역조회)에 부합하지 않음
- 결정: `shareholder_meeting_notice` 하나로 통합, 정기/임시 구분은 `shareholder_meeting` data tool로 drill-down

### treasury_retire 키워드·pblntf_ty 수정
- 원안: `("자기주식소각결정", "자사주소각결정", "자기주식소각")` + B 우선
- 실제: DART report_nm은 `주식소각결정` (접두어 없음), pblntf_ty = I
- 수정 후 키워드: `("주식소각결정", "자기주식소각결정", "자사주소각결정", "자기주식소각")` + I 우선

## 동작 원리

```
screen_events(event_type="ownership_change_filing",
              start_date="2026-03-20", end_date="2026-04-19",
              market="kospi", max_results=50)
  ↓
1. event_type → (pblntf_tys, keywords) 매핑
2. market → corp_cls 매핑 (kospi=Y)
3. 각 pblntf_ty별로 DART list.json 순회 (page_count=100, max 20페이지/ty)
4. report_nm 키워드 매칭된 건만 수집 (strip_spaces=True면 공백 제거 후 매칭)
5. rcept_dt 내림차순 정렬, max_results까지 반환
```

## 제한 사항

- **상태 기반 필터는 없음**: "자사주 5% 이상 + 최대주주 40% 미만" 같은 복합 상태 조건은 screener가 아니라 Claude Code에서 기존 tool 루프로 처리.
- **정기/임시 주총 구분 불가**: report_nm만으로는 불가능. drill-down 필요.
- **페이지 상한**: 각 pblntf_ty당 20페이지까지만 확인 (= 최대 2000건 검토). 초과 시 warning.
- **검색 키워드 정확성**: DART report_nm 패턴이 바뀌면 매칭률이 떨어질 수 있음. 주기적 재검증 필요.

## next action

- 실사용 피드백으로 자주 쓰이는 조합(예: `market=kospi` + `value_up_plan`) alias 화 검토
- 키워드 매칭 실패율 모니터링
- KOSPI 200 / KOSDAQ 150 지수 필터는 corp_cls로는 불가 — 필요 시 별도 유니버스 데이터 소스 고려
