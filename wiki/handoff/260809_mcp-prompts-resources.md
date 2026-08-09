# 260809 — MCP 세 축 중 남은 둘(Prompts·Resources) 실측

**상태**: Prompts 배포·확인 완료 → **결론은 부정**. Resources 미착수(근거 부족으로 보류).

## 무엇을 왜 봤나

OPM 은 MCP 세 축 중 **Tools 만** 쓰고 있었다(25개). Prompts·Resources 가 「진입점을 낮추는」
수단이 될 수 있는지 보려고 양식 하나(`company_snapshot`)를 만들어 배포하고 실제 화면을 봤다.

## 실측 — 공식 문서와 다르다

| | 문서 | 실제 |
|---|---|---|
| 명령 형태 | `/mcp__<서버>__<양식>` | **`/claude.ai <커넥터명>:<양식명>`** |
| 데스크톱 앱 | 언급 없음 | **양식을 아예 안 보여준다** |
| 터미널(CLI) | — | 보여준다 |
| 화면에 뜨는 것 | — | `name` + `description`. **`title` 은 안 뜬다** |
| 목록 갱신 | listChanged 로 자동 | 서버가 `listChanged: false` 라 **재연결해야 반영** |
| `/claude.ai` 접두어 | — | 커넥터 출처 표시. **서버가 못 없앤다** |

**커넥터 표시 이름은 서버가 정한다** — `FastMCP("...")` 의 값이 `serverInfo.name` 으로 나가고
커넥터 목록에 그대로 뜬다(`open-proxy-mcp` → `openproxy` 로 변경, 260809 배포).
fly 앱 이름(=URL `open-proxy-mcp.fly.dev`)·레포명과는 별개다.

## 결정적 차이 — AI 가 볼 수 있는가

세션에서 직접 확인했다(`ListMcpResourcesTool` 호출, 프롬프트용 수단은 존재하지 않음).

```
Tools       AI 가 부른다                      ✅
Resources   AI 가 목록을 보고 읽는다            ✅  ListMcpResourcesTool / ReadMcpResourceTool
Prompts     AI 에게 접근 수단이 없다            ❌  사용자가 골라야만 대화에 들어간다
```

**그래서 Prompts 는 사용자가 안 고르면 완전히 불활성이다** — AI 한테 힌트조차 되지 않는다.
데스크톱에서 안 보이는 것과 겹치면, **실사용자 287명에게는 없는 기능**이다.

## 결론

- **Prompts 는 더 만들지 않는다.** `company_snapshot` 하나는 배포된 채로 둔다 — 터미널에서는
  실제로 작동하고, 다음에 「데스크톱이 지원하기 시작했나」를 확인할 시험대가 된다.
  단 그 파일에는 도구 이름이 하드코딩돼 있어 **이름이 바뀌면 조용히 죽고 아무 테스트도 안 잡는다.**
- **Resources 가 원래 목적에 더 맞다** — AI 가 사용자 개입 없이 읽는다. 다만 지금 근거가
  하나뿐이라 착수하지 않았다: **의결권 가이드라인**(판정이 어느 기준을 따랐는지 AI 가 스스로
  검산할 근거. 260809 태광산업 — 자사주 소각에 배당성향을 인용한 문장을 잡을 방법이 없었다).
  나머지 후보(법령·공시코드·안건분류)는 **이미 도구가 인자를 받아 답한다** — 리소스로 중복시킬 이유가 없다.
- 판단이 이 세션에서 **두 번 뒤집혔다**(Prompts 우선 → Resources 우선). 근거 없이 다시
  뒤집지 않도록 위 표를 근거로 남긴다.

## 재현

```bash
# 서버가 무엇을 내주는지 (키는 .env 에서 읽고 출력하지 않는다)
curl -s -X POST "$URL/mcp?opendart=$KEY" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-06-18' \
  -d '{"jsonrpc":"2.0","id":1,"method":"prompts/list","params":{}}'
```

화면 확인은 **커넥터 재연결 후** 터미널에서 `/` → `claude.ai` 로 검색.
