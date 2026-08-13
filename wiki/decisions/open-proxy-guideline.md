---
type: decision
title: Open Proxy Guideline — OPM 자체 의결권 행사 정책 (실체 이전 안내)
generated: 2026-04-28
updated: 2026-08-14
version: v1.2
related: [voting-policy-consensus-matrix, decision-matrix-design, opm-guideline-debate-transcript, 2026 신법]
---

# Open Proxy Guideline — 실체는 패키지 안에 있습니다

**원문 위치**: `open_proxy_mcp/data/guideline/open-proxy-guideline.md`

## 왜 옮겼나 (260814)

이 문서는 **읽는 문서가 아니라 서버가 실행 중에 읽는 데이터**다.
`proxy_guideline` tool 과 `opm://guideline` resource 가 이 파일을 열어 사용자에게 돌려준다.

`wiki/` 에 두면 배포 이미지에 안 들어간다 — Dockerfile 이 코드(`open_proxy_mcp/`)와
법령 데이터(`wiki/rules/laws/`)만 복사하기 때문이다. 실제로 260813 배포에서
`proxy_guideline` 이 「문서를 찾지 못했습니다」를 돌려줬다.

패키지 안(`open_proxy_mcp/data/`)에 두면 **코드와 함께 자동으로 배포**된다.
운용사 정책(`data/asset_managers/`)·업종코드(`data/ksic/`)가 이미 그 방식이고,
`importlib.resources` 로 읽으므로 작업 디렉터리·실행 방식에 의존하지 않는다.

> 법령 40룰(`wiki/rules/laws/`)은 아직 경로 의존이라 같은 취약함이 남아 있다 —
> 별도 과제로 둔다.

## 어떻게 읽나

| 경로 | 방법 |
|---|---|
| 사람 | `open_proxy_mcp/data/guideline/open-proxy-guideline.md` 를 직접 연다 |
| AI·사용자 (MCP) | `proxy_guideline` tool. `section="재무제표"` 로 안건 유형별 절만 |
| MCP resource | `opm://guideline` — **Claude.ai 커넥터는 resource 를 모델에게 노출하지 않는다**(260813 실측). 다른 클라이언트용 |

## ⚠ 정책과 엔진은 의도적으로 다르다

문서 **§0-A 「정책 ↔ 엔진 정합표」** 가 그 간극의 공식 지도다. 정책이 `against` 를
선언해도 법령 강행규정·법정 결격 같은 hard trigger 가 아니면 엔진은 자동 반대 대신
**검토(REVIEW)** 로 두고 판단 재료를 애널리스트에게 넘긴다.
이 문서만 읽고 「시스템이 자동 반대한다」고 읽으면 안 된다.

## 관련
- [[../tools/proxy_guideline]] — 이 문서를 읽는 tool
- [[../tools/proxy_advise_before_meeting]] — 이 문서를 인용하는 쪽
- [[260429_0059_decision_voting-policy-consensus-matrix]] — 12 카테고리 합의 매트릭스
