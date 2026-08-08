---
type: reference
title: tool별 DART API 콜 budget (기업당 최대)
updated: 2026-07-15
method: 코드 실측 (services/*.py 의 DART client 호출 지점)
---

> 재무 SSOT 갱신 배치(내부 인프라)는 private wiki 참조.

## 260808 변경분

`proxy_advise_before_meeting` 이 per-firm 기준 **최대 2콜 늘었다**.

- `list.json` 1콜 — 주총일 시점에 이미 제출된 사업보고서를 찾는다(`latest_annual_report_before`).
  회의일을 모르면(사용자가 `year` 를 직접 지정) 부르지 않는다.
- `financial_metrics(scope="summary")` 1건 — 정기주총에서 승인 대상 연도의 확정치가 있을 때만.
  없으면 그 upstream 자체를 건너뛴다(없는 해를 물어 빈 응답을 받지 않는다).

market-scan 모드에는 영향이 없다 — 둘 다 회사별 경로다.
