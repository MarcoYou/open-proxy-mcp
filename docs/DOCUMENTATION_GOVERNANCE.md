# 문서 운영 규칙

이 문서는 `README.md`·`docs/`·`wiki/`가 서로 다른 정본으로 갈라지는 것을 막기 위한 운영 기준입니다.

## 정본과 역할

- 루트 `README.md` / `README_ENG.md`: 사용자용 진입점과 현재 runtime tool 목록
- `docs/`: 기능 설명, 사용 예시, 릴리즈 노트, 운영 절차
- `wiki/tools/`: tool별 입력·출력·데이터 출처의 기술 정본
- 과거 문서는 보관하지 않는다. 대체된 문서는 삭제하고 git 이력으로 남긴다. 회고·과정 서사는
  private 저장소(open-proxy-storage)로 보낸다 (`wiki/wiki_schema.md` 의 wiki vs storage 기준)

새 문서는 기존 문서와 역할이 겹치는지 먼저 확인합니다. 같은 주제의 설명을 두 곳에 복사하지 말고,
한 곳을 정본으로 정한 뒤 다른 곳에서는 링크와 짧은 요약만 둡니다.

## 문서 수 관리

- 새 문서는 기존 문서에 흡수할 수 없는 독립된 사용자 질문·tool·결정·절차가 있을 때만 만듭니다.
- 같은 기능의 한/영 문서는 `docs/features/`와 `docs/features/en/`에서 1:1로 유지합니다.
- 릴리즈가 끝나거나 결정이 대체되면 중복 문서를 삭제합니다. 살아 있는 규칙만 정본 페이지로 옮기고,
  대체 관계는 정본 페이지의 변경 이력 한 줄로 남깁니다.
- 분기별로 문서 인벤토리를 검토해 고아·중복·오래된 문서를 병합 후보로 GitHub Issue에 등록합니다.
- 병합은 원문 보존이 필요 없는 문서만 수행하며, 링크를 갱신한 뒤 전체 lint를 통과시킵니다.

## 변경 시 검증

- tool 이름·출력이 바뀌면 `wiki/tools/<tool>.md`와 양쪽 README를 함께 갱신합니다.
- 모든 tool 문서는 frontmatter의 `updated: YYYY-MM-DD`를 갱신합니다.
- `python scripts/wiki_lint.py --strict`는 wiki 정책과 저장소 전체 상대 링크를 검사합니다.
- `python scripts/check_tool_catalog.py`는 runtime tool 수, 루트 README, 한/영 대칭, 구 tool 링크를 검사합니다.
- CI 실패·문서 병합 후보·대규모 문서 정리는 GitHub Issue로 남기고, 커밋에서 해결된 issue를 닫습니다.

로컬 hook은 빠른 경고를 제공하고, CI는 전체 tracked Markdown을 검사합니다. 현재 규모에서는 전체 링크 검사가
파일 수에 선형으로 비례하지만 외부 네트워크 호출이 없어 CI에서 무시할 수준의 비용입니다.
