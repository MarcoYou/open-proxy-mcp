# 루틴 레시피 (docs/routines)

스케줄 클라우드 루틴(`/schedule`)에 **그대로 붙여넣는 프롬프트 모음**입니다. 매일/주기적으로
자동 실행돼 폰으로 훑는 디제스트·알람 유즈를 담습니다. tool의 한 줄 역할·상세 스키마는
[wiki/tools 카탈로그](../../wiki/tools/README.md)가 정본(SSOT)이고, 여기는 **운영 playbook 뷰**만
담습니다(예시 질문은 [docs/examples](../examples/README.md)).

## 레시피

| 레시피 | 무엇 | 쓰는 tool |
|---|---|---|
| [screener-morning-digest](screener-morning-digest.md) | 매일 아침 시총 상위 200 수주·임시주총 공시 디제스트 (신규/정정 구분 + ⭐ 임팩트) | screener |

## 루틴 프롬프트 공통 원칙

이 폴더의 프롬프트는 아래를 공통으로 지킵니다. 새 레시피를 추가할 때도 따르세요.

1. **출력 형식을 매일 똑같이 못박는다.** "table or bullets"처럼 `or`를 두지 말 것 — 제목·섹션 순서·표
   칼럼을 고정해야 날마다 같은 모양으로 스캔된다.
2. **결과만 출력.** 에이전트 진행상황·영어 상태로그("Screener succeeded…", "Sending…")·"확인해
   드릴 수 있습니다" 같은 대화체는 금지 — 답장 없는 푸시다.
3. **정직한 degrade.** 값이 없으면 지어내지 말고 "미상"으로 두고 원문(DART) 링크를 남긴다. 빈 결과는
   "없음"으로 명시하고, 조회 실패는 실패라고 솔직히 — "빈 배열 = 성공" 금지.
4. **콜 예산을 의식한다.** `universe`·`period`·`details`가 DART 콜 수를 정한다. market-scan vs
   per-firm(details) 구분은 [tool_call_budget](../../wiki/tools/tool_call_budget.md) 참조.
5. **신규 vs 정정을 가른다.** 예정치(결정·소집결의)와 정정을 섞지 않는다. 정정의 뉴스는 "바뀐 값"이므로
   가능하면 정정전→정정후 diff를 보여준다.
