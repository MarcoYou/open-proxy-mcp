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

## 문서 운영 규칙 (구 `docs/DOCUMENTATION_GOVERNANCE.md`, 260904 흡수)

**정본과 역할** — 루트 `README.md`/`README_ENG.md` = 사용자 진입점 + 현재 runtime tool 목록(분류는 `tools/README` 표를 따른다) ·
`docs/` = 기능 설명·사용 예·릴리즈 노트·운영 절차 · `wiki/tools/` = tool 별 입력·출력·데이터 출처의 기술 정본 ·
`wiki/rules/` = 사실 · `wiki/decisions/` = 왜. 같은 주제를 두 곳에 복사하지 않고 한 곳을 정본으로 정한 뒤 나머지는 링크와 짧은 요약만.

**문서 수** — 새 문서는 기존 문서에 흡수할 수 없는 독립된 질문·tool·결정·절차가 있을 때만. 대체된 문서는 보관하지 않고 삭제해
git 이력으로 남기며, 살아 있는 규칙만 정본으로 옮기고 대체 관계는 정본의 변경 이력 한 줄로. 한/영 기능 문서는 `docs/features/`와
`docs/features/en/`에서 1:1. 회고·과정 서사는 storage(위 「wiki vs storage」).

**한 번에 하나만 믿는다** — 검사가 안 보는 사본은 반드시 뒤처진다(260817 카탈로그 합 21 vs 런타임 26). 그래서 아래 표의
「돌릴 검사」가 그 사본을 실제로 읽는지가 규칙의 핵심이다.

### 무엇을 바꾸면 어디를 고치나

| 바꾼 것 | 고칠 파일 | 돌릴 검사 |
|---|---|---|
| **새 tool 추가 / 이름 변경 / 제거** | `wiki/tools/<tool>.md`(frontmatter `domain`·`updated`) · `wiki/tools/README.md` 표 + 「카테고리별 통계」 · 루트 `README.md`·`README_ENG.md` 표 + tool 수 문구 · 개명·제거면 `scripts/usage_tracker.py` `TOOL_ALIASES` + 옛 이름 `grep -rl` 치환 · 그 tool 페이지 「외부 호출」 절(DART 콜 수, per-firm vs market-scan) · `docs/RELEASE_NOTES.md`(+`_ENG`) | `uv run python scripts/check_tool_catalog.py` · `python3 scripts/wiki_lint.py --strict` · `uv run pytest -q tests/ -k "catalog or vocabulary or mcp_protocol"` |
| **파서·필드(출력) 수정** | `wiki/tools/<tool>.md` 출력 schema + 변경 이력 + `updated` · 단위 접미어는 [[단위-표기-규약]] · `business_details` 필드면 `docs/features/business-details.md`(+`en/`)의 documentation-contract 주석 | `wiki_lint.py --strict` · `scripts/check_documentation_contract.py` · `scripts/output_vocab_lint.py` · `uv run pytest -q` · 커밋 훅 `scripts/wiki_drift_warn.sh`(코드만 바뀌고 wiki 가 안 바뀌면 경고) |
| **파라미터 변경** | `wiki/tools/<tool>.md` 「입력 인자」 표 + 사용법 예 · `docs/features/*.md` 의 예시가 그 파라미터를 쓰면 함께 · DART 콜 수가 바뀌면 그 tool 페이지 「외부 호출」 절(per-firm vs market-scan 구분) | `wiki_lint.py --strict` · `check_tool_catalog.py`(설명 `ref:`·`when:` 토큰) · `uv run pytest -q` |
| **개념·법령 사실 변경** | `wiki/rules/concepts/<개념>.md` 또는 `rules/laws/` · 시행일은 `open_proxy_mcp/data/laws/law_provisions.json`(SSOT) 만 고치고 `scripts/gen_law_timeline.py` 로 md 표 재생성 · 같은 사실을 옮겨 적은 페이지(`grep`) | `wiki_lint.py --strict`([7] 시행일 3자 정합) · `scripts/verify_law_against_corpus.py` · `uv run pytest -q tests/ -k law` |
| **페이지 이동·삭제·병합** | 폴더 `README.md` 인덱스 · 옛 이름의 위키링크·frontmatter `related_*` 전부 치환(`grep -rl`) · `wiki_index.md` 의 나열(Concepts 등은 손으로) | `python3 scripts/gen_index.py`(카운트) → `gen_index.py --check` · `wiki_lint.py --strict`([3] README 인덱스 · [9] 없는 문서 참조) |
| **wiki 구조·규칙 변경** | 이 파일(`wiki_schema.md`) 만 — `wiki_index.md` 에 규칙을 다시 적지 않는다([8]) · `CLAUDE.md` 「wiki 참조」 표의 행 | `wiki_lint.py --strict` |

CI(`.github/workflows/wiki-lint.yml`)는 `gen_index.py --check` → `wiki_lint.py --strict` → `check_documentation_contract.py` 순으로 막는다.
`check_tool_catalog.py` 는 런타임 앱을 import 하므로 의존성이 있는 환경(`uv run`)에서 돈다.
