---
type: decision
title: OPM guideline v2 최종 개편안 및 파일럿 검증 기록
date: 2026-09-08
status: pilot shadow
version: v0.1.0-pilot
related: [open-proxy-guideline, proxy_advise_before_meeting]
---

# OPM guideline v2 최종 개편안 및 파일럿 검증 기록

## 결정 범위

Astra 설계의 근거·판정 계약과 Fable 리뷰의 운영 범위를 합친 설계안을 최종 후보로 기록한다.
전체 비교·HTML·JSON·플로우차트는 `output/guideline-synthesis-20260908/`에 보관한다.
이 문서는 그 설계를 OPM 런타임에 연결한 최소 파일럿과 실제 호출 결과를 함께 기록한다.

v2는 아직 공식 의결권 정책이 아니다. `vote_style="opm_guideline_v2"`를 선택했을 때만
shadow trace를 만들며, 실제 `decision`은 기존 OPM 엔진이 계속 결정한다.

## 최종 설계

### 다섯 축을 분리한다

한 규칙에 출처, 효과, 근거 상태, 위임 수준, 운영 상태를 섞지 않는다.

| 축 | 기록할 값 | 적용 원칙 |
|---|---|---|
| origin | 법령, 기관 정책, OPM 정책, 고객 정책 | 출처를 보존하고 기관 정책을 OPM 정책으로 이름 바꾸지 않음 |
| effect | `oppose`, `support_basis`, `require_review`, `inform` | 신호와 확정 권고를 구분 |
| evidence | `fact`, `derived_fact`, `assessment` + known/unknown/conflicting | 결측을 `false`·0으로 치환하지 않음 |
| delegation | 사람 또는 승인된 평가자 | 모델 confidence를 승인 근거로 사용하지 않음 |
| operations | 검토 상태, 의결권 자격, 제출 권한, 실행 이력 | 분석 결과와 실제 투표 실행을 분리 |

### 정책은 작성본에서 실행본으로 컴파일한다

PolicySource에 규칙·예외·지표·단위·분모·기간·적용일·권한·오버레이를 기록하고,
컴파일 단계에서 타입·참조·상속 순환·충돌을 검사한다. ResolvedPolicy에는 상속이 풀린 값,
변경 이유, 원문과 정책 hash, 컴파일러·스키마 버전을 고정한다. HTML은 JSON에서 생성한다.

현재 런타임에 연결한 파일은
[`opm-guideline-v2.json`](../../open_proxy_mcp/data/guideline/opm-guideline-v2.json)이며,
출석률·재선임·예외 수용·독립성 평가 수용·긍정 커버리지 게이트를 최소 계약으로 둔다.
출석률 기본 문턱은 75%이고 50~100% 범위에서 pilot overlay로 조정할 수 있다.
이 값은 기관 공식 기준이 아니라 합성 검증용 설정이다.

### LLM의 역할과 출력

LLM은 원문에서 안건·후보·관계·조문·소명·반증의 후보를 만들고, 인용 위치·주체·기간·단위
검증을 통과한 것만 사실 또는 평가로 수용한다. 정성 평가는 초기에는 사람 검토가 수용한다.
정책 엔진은 작은 결정적 표현식만 평가하고 다음 상태를 보존한다.

- 정책 전체를 평가해 `not_applicable`, `not_triggered`, `fired`, `excepted`, `unresolved`를 기록한다.
- 확정된 반대 근거와 검토 필요 상태를 동시에 보존한다.
- 필수 근거가 비어 있으면 긍정 게이트를 통과시키지 않는다.
- 새 정책 trace는 기존 `decision`을 덮어쓰지 않는다.

응답에는 전체 `guideline_application`과 안건별 `guideline_trace`를 넣어 어떤 규칙이 평가됐고
무엇이 부족한지 재현할 수 있게 한다. 정책 갱신은 변경 감지와 영향 범위 계산을 자동화하되,
의미가 바뀌는 버전의 활성화는 정책·법률 책임자 승인을 거친다.

## 파일럿 결과

### 계약 검증

- `engine.js`로 합성 경계·예외·unknown·80% overlay·신임 후보 사례를 포함한 16개 사례를 실행해 **16/16 통과**했다.
- 전체 Python 테스트는 **1,685 passed, 1 warning**이었다. warning은 기존 재무제표 parser의 BeautifulSoup XML 파싱 경고다.
- `python3 scripts/wiki_lint.py --strict`는 **160개 페이지 전체 통과**했다.
- 라이브 기준 커밋 `40a6a6ce8c208348fdaeac725e8519b7edd41f29`와 `origin/main`이 일치하는지 확인한 뒤 같은 입력 조건으로 비교했다.

### 실제 MCP 호출

`proxy_advise_before_meeting`에 `vote_style="open_proxy"`와
`vote_style="opm_guideline_v2"`를 각각 호출하고, 회차·안건·기존 판정·v2 trace를 대응시켰다.

| 회사·회차 | 안건 | 기존 판정 | v2 판정 | diff | trace |
|---|---:|---|---|---:|---|
| 고려아연 2026 임시주총 | 9 | FOR 5 / AGAINST 0 / REVIEW 4 | 동일 | 0 | 평가 6건, 미해결 규칙 7건, 발화 효과 0 |
| KT&G 2026 정기주총 | 15 | FOR 10 / AGAINST 0 / REVIEW 5 | 동일 | 0 | 평가 3건, 미해결 규칙 3건, 발화 효과 0 |

두 회사 모두 응답 상태는 `exact`였다. 현재 도구 출력에는 출석률과 평가 승인자가 확정된
입력으로 들어오지 않으므로, 미해결 trace가 생기는 것은 결함을 숨기지 않은 정상 결과다.
Markdown 렌더러에서도 기계 가이드라인 요약, 안건별 v2 trace, 기존 판정 유지 문구를 확인했다.

## 해석과 승격 조건

이번 파일럿이 입증한 것은 계약 정합성, unknown 보존, 기존 판정과의 비파괴적 연결,
실제 두 회사 응답의 paired diff가 0이라는 사실이다. 실제 공시 추출 정확도나 전문가의
의결권 판단과의 일치율을 입증한 것은 아니다. 합성 16개 사례의 기대값도 독립된 정답 집합이
아니므로 모델 품질 점수로 사용하지 않는다.

production effect를 켜기 전 다음 조건을 충족한다.

1. 출석률·독립성·정관 조문에 대해 기준일과 인용 위치가 있는 독립 gold set을 만든다.
2. 확인·미확인·충돌·예외·경계값을 분리한 회사/연도 holdout으로 규칙 적용 정확도와 결측률을 측정한다.
3. 두 명 이상의 독립 검토자와 불일치 심의를 거쳐 정성 평가의 수용 rubric과 위임 범위를 승인한다.
4. 최소 3회 shadow 주기에서 기존 엔진 회귀와 정책 trace 누락을 확인한 뒤 별도 버전으로 effect를 활성화한다.

## 변경 파일

- `open_proxy_mcp/data/guideline/opm-guideline-v2.json`: 기계 정책 source
- `open_proxy_mcp/services/guideline_policy.py`: unknown 보존 결정적 evaluator
- `open_proxy_mcp/services/proxy_advise.py`: v2 shadow 연결과 trace 출력
- `open_proxy_mcp/tools/proxy_advise_before_meeting.py`: JSON/Markdown 공개 계약
- `tests/test_guideline_policy.py`: 정책·경계·예외 계약 테스트
- `wiki/tools/proxy_advise_before_meeting.md`: 호출·출력 문서
- `output/guideline-synthesis-20260908/`: Astra·Fable 비교, 최종 설계, HTML·JSON·플로우차트
