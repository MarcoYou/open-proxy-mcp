# wiki/handoff — 세션 핸드오프

세션 종료 시 "지금까지 뭐 했고 / 어디서 멈췄고 / 내일 뭐부터"를 남기는 **임시(transient)** 폴더. 다음
세션(사람 또는 LLM)이 이 문서만 읽고 이어갈 수 있게 작성한다. **모든 핸드오프는 여기 저장** (사용자 지시 260705).

명명: `yymmdd_{title}.md`. 최신이 위로.

## 생명주기 — 여기는 종착지가 아니다 (사용자 지시 260705)

핸드오프 문서는 **다 풀리고 나면 사라져야 하는 임시 메모**다. 항목이 해결되면:
1. **durable한 지식**(왜 이렇게 됐는지, 재발방지, 방법론)은 알맞은 wiki 카테고리로 옮겨 문서화한다
   — `lessons/`(작업 이유·회고) · `decisions/`(설계 결정) · `architecture/`(구조 변경) 등, 성격에 맞게.
2. 옮긴 뒤 **그 핸드오프 문서는 바로 삭제**한다(부분 해결이면 남은 항목만 남기고 해결분은 제거).
3. 삭제/이관 후 **바로 `python3 scripts/wiki_lint.py --strict` 실행** — README 인덱스 drift·양방향
   link 결손을 즉시 잡는다. 이 폴더의 README도 색인에서 제거 항목을 함께 갱신.

**핵심**: 핸드오프에 있다 = 아직 미해결/미이관. 다 풀린 핸드오프가 남아있으면 안 됨(stale 핸드오프 = 다음
세션이 뭐가 진짜 남았는지 헷갈리게 만드는 원인).

## 색인
- [[260807_proxy-advise-persona-validation]] — proxy_advise 3자 페르소나 검증. 결함 4가족 확인,
  **코드 미수정**. 수정 착수는 사용자 지시 대기.

_(과거: 260809_mcp-prompts-resources — Resources 는 `open_proxy_mcp/resources.py`(opm://filing·opm://guideline)로
260813~14 구현·배포, 실측 결론은 [[mcp-endpoints]]·`tools/proxy_guideline` 에 기록돼 260902 삭제 /
260705_valuation-timeseries 해결 완료, durable 지식은 data-storage-registry(private 이관)·
aggregate-denominator-pairing-260706·`project_fund_currency` 메모리로 이관 후 260706 삭제 /
260723 핸드오프 — 잔여 항목·회고 전부 private wiki로 이관 후 260724 삭제 /
260802_geo-revenue-anchoring — 좀비 stdio는 [[mcp-endpoints]]로, 4축 재편·지역별 결함 3건은
`tools/business_details` 실사용 검증 절로, 측정 함정 회고는 private lesson
`production-call-signature-260802` 로 이관 후 260802 삭제. 남은 개선거리(키움증권 절 밖 지역표 1건 ·
절 맵 적재)는 그 tool 문서에 기록됨 — 표본 1건·사용자 판단 대기라 핸드오프로 들 만한 미해결이 아님)_
