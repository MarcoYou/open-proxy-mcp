---
name: opm-tool-validation
description: OPM 검증의 목적과 범위를 정하고 수집 안전성·파싱·MCP 응답·출력·측정기 검사를 연결한다. 복합 검증, 개선 후 재검증, 기존 tool validation 요청에 사용한다.
metadata:
  short-description: 목적별 검증 선택과 근거 기반 통과 판정
---

# OPM 검증 총괄

이 스킬은 검사 종류를 선택하고 결과를 합친다. 모든 검사를 무조건 실행하는 스킬도, 수집·수정·배포 권한을 주는 스킬도 아니다.

## 시작

- 대상 저장소의 `CLAUDE.md`를 읽고 작업 종류(설명/진단/개선)를 확인한다. 다른 제품이면 그 저장소의 지침도 확인한다.
- 현재 체크아웃, 수정 파일, 검증 대상 코드, 사용할 실행 환경을 식별한다. 스킬이 설치된 저장소와 평가할 저장소를 혼동하지 않는다.
- [워크플로우](references/workflow.md)를 읽고 검증 프로필, 주장할 범위, 필요한 단계, 반복 예산을 먼저 기록한다.
- 처음 실행하거나 작업 폴더가 바뀌었으면 [실행 경로](references/commands.md)를 읽는다. 파일이 없는 main에 실험 브랜치의 기능이 있다고 가정하지 않는다.

## 목적별 선택

| 목적 | 읽을 스킬 | 끝나도 주장할 수 없는 것 |
|---|---|---|
| 원문 수집·중단·캐시 안전성 | [opm-collection-validation](../opm-collection-validation/SKILL.md) | 파싱 정확도 |
| 이름·역할·기간·수치의 정확성 | [opm-parser-validation](../opm-parser-validation/SKILL.md) | 사용자에게 같은 값이 전달됨 |
| 실제 MCP 요청·응답 계약 | [opm-mcp-validation](../opm-mcp-validation/SKILL.md) | 원문 판독 정답 또는 운영 배포 성공 |
| 설명·표·경고·문서·화면의 의미 | [opm-output-validation](../opm-output-validation/SKILL.md) | 계산·판독 정확도 |
| 정답·평가기·스킬이 틀릴 가능성 | [opm-validation-audit](../opm-validation-audit/SKILL.md) | 모든 다른 단계의 통과 |

선택한 스킬만 본문 전체를 읽고 적용한다. 단일 목적 요청이면 해당 스킬로 바로 진행해도 된다.
새 평가기·정답집·검증 스킬을 만들거나 과도하게 좋은 결과가 나오면 측정기 검사를 먼저 한다.

## 공통 계약과 판정

[근거 계약](references/evidence.md)에 따라 범위와 근거를 남긴다. 빈 표본·누락 단계·근거 없는 PASS는 통과가 아니다.
PASS는 측정한 범위만 뜻한다. 합성 검사, 실제 고정 표본, 앱 내부 MCP, pilot, live 결과를 합쳐 부풀리지 않는다.

워크플로우 보고서를 합칠 때는 `scripts/check_report.py`로 필수 단계와 근거 파일의 무결성을 검사한다.
이 도구는 원문을 판독하거나 테스트를 실행하지 않는다. 자세한 입력과 한계는 근거 계약에 있다.

기존의 병렬 수집 예시, 숫자 집합만 비교하는 합격 기준, 내부 함수 호출을 최종 검증으로 보는 절차는 폐기했다.
현행화 기준과 재검토 계기는 [워크플로우](references/workflow.md)의 유지관리 절을 따른다.
