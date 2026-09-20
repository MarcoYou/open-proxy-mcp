# 기존 실행기와 연결하기

현재 위치에 필요한 파일이 있는지 먼저 확인한다. 이 목록은 설치나 기능 존재를 보증하지 않는다.
`CLAUDE.md`와 실제 CLI 도움말을 우선하고, 기존 가상환경을 사용한다. 검증을 핑계로 의존성을 자동 갱신하지 않는다.

## 위치 확인

```bash
git worktree list
git status --short
rg --files scripts tests docs | rg 'parser_feedback|collect_parser|personnel|mcp_protocol|PARSER_FEEDBACK'
```

파싱 실험 코드는 `codex/parser-feedback-loop` 작업 폴더에 있을 수 있다. main에 없으면 없는 것으로 보고한다.
스킬의 위치에서 제품 코드를 자동 선택하지 말고 평가할 저장소 경로를 명시한다.

## 목적별 기존 자산

| 자산 | 쓰는 목적 | 범위/조건 |
|---|---|---|
| `tests/test_collect_parser_documents.py` | 수집기 안전성 회귀 | 실험 코드가 있는 checkout |
| `tests/test_parser_feedback.py` | 후보 평가기의 거짓 통과 검사 | 같은 평가기 버전에서 실행 |
| `scripts/parser_feedback.py` + `docs/PARSER_FEEDBACK.md` | 고정 원문·독립 정답 평가 | 현행은 후보 이름·생년월일·역할·출현 횟수. 임기 아님 |
| `tests/test_personnel_term.py` | 기간 분리의 합성 회귀 | 실제 시장 임기 정확도 아님 |
| `tests/test_personnel_feedback_regressions.py` | 원문 경계부터 실제 앱 MCP 응답 | 앱 내부, pilot/live 아님 |
| `tests/test_mcp_protocol_contract.py` | 실제 앱의 프로토콜 계약 | 원천 값을 고정한 관련 도구 호출 검사도 필요 |
| `scripts/diff_tool_output.py` | 출력 소실 보조 경보 | 정수·단위·대상 결합·중복을 모두 검증하지 않음 |
| `scripts/scan_tool_output.py` | 내부 식별자 보조 경보 | 의미 검수를 대체하지 않음 |
| `scripts/wiki_lint.py --strict` | 위키 연결·계약 검사 | 설명 친절성의 증명 아님 |

제품 문서 `docs/PARSER_FEEDBACK.md`가 존재하면 실행 전 읽는다. 스키마는 그 문서와 실행기의 계약이 정본이다.
명령의 `/path/to/...`는 확인한 실제 경로로 바꾼다. 명령은 관련 checkout을 작업 폴더로 삼아 실행한다.

```bash
/path/to/venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_parser_feedback.py
/path/to/venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_collect_parser_documents.py
/path/to/venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_personnel_term.py tests/test_personnel_feedback_regressions.py tests/test_mcp_protocol_contract.py
```

## 검증 체계 자체의 회귀

새 보고서 검사기의 정상/실패 대조는 일반 테스트에도 연결되어 있다.

```bash
/path/to/venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_validation_workflow.py
```

pytest가 없는 환경에서는 스킬의 `scripts/test_check_report.py`를 표준 Python으로 직접 실행해도 된다.
이 테스트 통과는 보고서 취합기의 동작 근거이며 파서 정확도의 근거는 아니다.

## 고정 입력으로 이전/수정 코드 비교

같은 평가기 파일·원문·라벨을 쓰고, 코드는 서로 다른 프로세스로 평가한다.
산출물 경로는 새 이름을 사용한다. 입력·정답·기준선 파일을 덮어쓰지 않는다.

```bash
/path/to/venv/bin/python /path/to/lab/scripts/parser_feedback.py \
  --code-root /path/to/before \
  --manifest /private/corpus/gold.json --cache-dir /private/corpus/documents \
  --out /private/run/before.json

/path/to/venv/bin/python /path/to/lab/scripts/parser_feedback.py \
  --code-root /path/to/after \
  --manifest /private/corpus/gold.json --cache-dir /private/corpus/documents \
  --baseline /private/run/before.json --out /private/run/after.json
```

이 실행기는 후보가 없는 음성 정답이나 임기 필드를 모두 지원하는 일반 평가기가 아니다.
계약 밖 범위는 별도 테스트·평가기 확장과 독립 정답이 필요하다. 성공 출력의 범위를 확대해서 읽지 않는다.

## 실제 수집과 소비 측

`scripts/collect_parser_documents.py`는 실제 API를 부를 수 있다. 오프라인 검사 명령 묶음에 포함하지 않는다.
수집 스킬의 안전성 검증과 과업 권한 확인 후 대상·예산·저장 경로를 명시해 중앙 담당자만 실행한다.

연결 대시보드에서는 그 프로젝트의 지침과 `wiki/pipeline/parser-preview.md`가 있는지 확인한다.
`pipeline/preview_personnel.py` 및 `tests/test_preview_personnel.py`가 있어도 미리보기 전용일 수 있다.
운영 수집·저장·화면 연결을 실제로 검사한 범위와 분리해 보고한다.
