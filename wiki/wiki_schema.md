# Wiki Schema

OPM 도메인 지식 위키. LLM이 유지하고 사용자는 소싱·질문에 집중.
처음 방문: [[wiki_index]] → [[tools/README]].

## 폴더 (4개 + 보조 2개)

| 폴더 | 무엇 | 수정 |
|---|---|---|
| `raw/` | 외부 원본 (PDF·xlsx·md) | **절대 수정 금지** |
| `rules/` (concepts·disclosures·laws) | 한국 자본시장 사실 | 사실 변경 시 |
| `tools/` | MCP tool 카탈로그 | 코드 변경 시 함께 |
| `decisions/` | 설계·정책·판단 + 시점 작업 | 결정 시 추가 |
| `guide/` (보조) | 사람용 개요·발표 자료 — 지식의 정본이 아니라 안내 | 구조가 바뀔 때 |
| `handoff/` (보조) | 세션 간 미해결 항목. **다 풀리면 삭제** (규칙은 `handoff/README`) | 세션 종료 시 |

작업 로그·회고·과정 서사는 wiki 에 두지 않는다 (storage — 아래 "wiki vs storage").

## Link 방향

```
raw → rules → decisions/tools   단방향(위→아래만). rules가 tools를 알면 안 됨
decisions ↔ tools                자유 (서로 참조 가능, 강제 아님)
잎 ↔ 잎                         자유 (자사주 ↔ 의결권 등)
```

## 명명

- 정체성 문서: `{name}.md` (tool명·공시명·개념명)
- 시점 문서: `yymmdd_hhmm_{type}_{title}.md`
- 한국어 파일명 OK. hyphen 구분.

## wiki vs storage

wiki = 지금 무엇이 참인가(현재형). storage(private) = 어떻게 여기까지 왔나(과거형 — 회고·레슨·발견 과정).
판별: 그 문장을 지우면 오늘의 동작 이해에 지장이 있나? 있으면 wiki, 없으면 storage.
