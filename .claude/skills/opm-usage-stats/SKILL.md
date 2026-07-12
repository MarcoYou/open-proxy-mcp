---
name: opm-usage-stats
description: OPM 외부 사용자 통계(6/29 최초 수집 시점 ~ 현재)를 조회해 터미널에 간결하게 요약해 보여준다. "사용 통계 보여줘", "usage stats" 요청 시 사용.
metadata:
  short-description: OPM 사용자 통계 터미널 요약
---

# opm-usage-stats

OPM(open-proxy-mcp)의 외부 사용자 통계를 조회해 채팅에 간결히 요약한다. HTML/Artifact를 만들지
않는다 — 터미널 결과를 그대로 사람이 읽기 좋은 텍스트로 정리해 보여주는 것으로 충분하다.

## 1. 데이터 조회

```bash
cd /Users/marcoyou/Projects/open-proxy-mcp && python3 scripts/usage_tracker.py --stats
```

(스킬 base directory가 프로젝트 루트가 아니라 스킬 폴더 자체이므로 `cd`를 반드시 같은 커맨드에
붙여서 실행한다 — 별도 명령으로 나누면 다음 호출에서 cwd가 초기화될 수 있다.)

Postgres(Supabase) `tool_call_events`에서 매번 새로 조회한다(이전 대화의 캐시된 숫자를 재사용하지
않는다). 세션 분할(30분 갭), KST 일/주 버킷, 운영자 본인 키 제외는 스크립트가 이미 처리함.

## 2. 요약 구성

원본 출력을 그대로 붙여넣지 말고, 아래 항목을 **이 순서 그대로** 짧은 텍스트/불릿으로 재구성해 보여준다
(일별 추이를 맨 먼저 — "매일 얼마나 있었는지"가 이 스킬의 핵심 질문):

- **일별 추이** (가장 먼저): 날짜별 단일사용자·요청수를 **표로 그대로 전부 보여준다**(요약 문장으로
  뭉개지 말 것). 단, 세로로 길게 나열하지 말고 **날짜를 열(column)로, 지표를 행(row)으로
  전치(transpose)**해 가로로 채운 2행짜리 표로 만든다(날짜별 1행씩 쌓지 말 것 — 스크롤이 길어짐).
  표 아래에 특이 급증/급감일이 있으면 한 줄로 짚어준다(원인 추정 대신 사실만)
- **기간·모집단**: 조회 기간, 외부 사용자 수, 총 요청 수
- **재방문 비율**: 재방문(2일+) 인원 / 전체 사용자, %로 계산해 함께 표기
- **인당 지표**: 평균 요청/인, 평균 체류(분) — 필요시 "N시간 M분"으로 변환
- **집중도**: `--stats` 출력의 `[집중도]` 줄을 그대로 사용 — "상위 N명(X%)이 전체 요청의 90%를 차지"
- **Top 사용자**: 상위 3~5명만 요청수·활성일·체류시간 요약 (해시는 앞 10자만, 전체 해시 노출 금지)
- **Tool별 사용**: `initialize`/`notifications/*`/`tools/list`/`resources/list`/`prompts/list` 같은
  MCP 프로토콜 핸드셰이크 호출과 **`company`**(모든 tool 호출 전 항상 선행되는 필수 조회라 사용 빈도
  지표로서 의미 없음 — 매번 제외)를 제외하고, 나머지 도메인 tool(`financial_metrics`,
  `ownership_structure` 등) 기준 상위 5개를 **일별 표와 같은 방식으로 날짜 대신 tool을 열(column)로
  전치한 가로 표**(요청수/사용자수/오류율 3행)로 정리한다
- **성능**: 평균 응답시간(ms)

## 3. 출력 형식

숫자 위주 표 하나 + 짧은 해설 몇 줄로 끝낸다. 마크다운 표를 써도 되지만 과하게 길게 만들지 말 것
(터미널/채팅에서 바로 읽히는 것이 목적 — Artifact나 별도 파일 생성 불필요).

## 4. 특정 유저 상세가 필요할 때

`--stats`는 필터 옵션이 없다. 특정 `key_hash`의 최근 호출 내역이 필요하면:

```python
import os; from dotenv import load_dotenv; load_dotenv('.env'); import psycopg
con = psycopg.connect(os.environ['DATABASE_URL']); con.autocommit = True
rows = con.execute(
    "SELECT tool, status, is_error, latency_ms FROM tool_call_events "
    "WHERE key_hash=%s ORDER BY ts_ns DESC LIMIT 30",
    ('<key_hash>',)
).fetchall()
```

`key_hash`는 `--stats`의 "사용자 Top 15" 표에 나오는 값을 그대로 사용한다. 쿼리 파라미터(회사명 등)는
저장되지 않으므로(개인정보 정책) tool/status/latency만 확인 가능하다.

과거(드레인된) 로그는 이 레포 범위 밖 — `~/Projects/open-proxy-storage/view_logs.py`(별도 private
레포, `--file`/`--daily` 옵션)에서 조회한다.
