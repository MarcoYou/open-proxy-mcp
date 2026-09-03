---
type: decision
title: Open Proxy Guideline — OPM 자체 의결권 행사 정책 (실체 이전 안내)
generated: 2026-04-28
updated: 2026-09-03
version: v1.2
related: [voting-policy-consensus-matrix, decision-matrix-design, opm-guideline-debate-transcript, 2026 신법]
---

# Open Proxy Guideline — 실체는 패키지 안에 있습니다

> **이 페이지는 안내문이다. 정책 본문이 아니다.** 정책의 절 번호(§2.4 등)·항목·정합표는 전부
> 아래 원문 파일에만 있고, 이 페이지에는 「어디 있고 어떻게 읽나」만 적는다.
> 인용 라벨이나 테스트가 가리키는 것은 언제나 원문 파일이다.

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
| AI·사용자 (MCP) | `proxy_guideline` tool. `section="2.4"` 처럼 **판정의 「정책 인용」이 가리키는 절 번호**로, 또는 `section="재무제표"` 로 안건 유형별 절만 |
| MCP resource | `opm://guideline` — **Claude.ai 커넥터는 resource 를 모델에게 노출하지 않는다**(260813 실측). 다른 클라이언트용 |

## 인용 라벨 ↔ 원문 ↔ 엔진 (260903)

`proxy_advise_before_meeting` 의 「정책 인용」은 원문의 **절 번호와 항목 번호**를 가리킨다 —
「OPM Guideline §2.4 이사 선임 — against ①「사외이사 5년 룰」… ▸ 엔진: …」. 종전의 「§재무제표」식
요약(문서에 없는 절)은 없어졌다. `tests/test_policy_citations_match_document.py` 가 라벨의 절·제목·항목을
원문과 자동 대조하므로, **원문의 항목을 고치거나 순서를 바꾸면 라벨을 같이 고쳐야 한다.**
읽는 법은 [[../tools/proxy_guideline]] 에.

## ⚠ 정책과 엔진은 의도적으로 다르다

문서 **§0-A 「정책 ↔ 엔진 정합표」** 가 그 간극의 공식 지도다. 정책이 `against` 를
선언해도 법령 강행규정·법정 결격 같은 hard trigger 가 아니면 엔진은 자동 반대 대신
**검토(REVIEW)** 로 두고 판단 재료를 애널리스트에게 넘긴다.
이 문서만 읽고 「시스템이 자동 반대한다」고 읽으면 안 된다.

260903 에 정합표에 **출석률·5년 룰·겸임** 3행을 추가했다. 출석률(§2.4 against ④)은
`director_board`(attendance)·`corp_gov_report`(표 7-2-1)에 데이터가 있는데 엔진이 안 쓰는 항목이다 —
10사 표본에서 시점(소집공고 시점엔 당기 사업보고서 부재)·재직 짧은 이사의 분모 왜곡·표 부재 30% 가
확인돼 **판정 트리거로는 넣지 않고** `facts` 노출 → 30사 표본 뒤 결정으로 둔다. 근거는 표 안에.

## 관련
- [[../tools/proxy_guideline]] — 이 문서를 읽는 tool
- [[../tools/proxy_advise_before_meeting]] — 이 문서를 인용하는 쪽
- [[260429_0059_decision_voting-policy-consensus-matrix]] — 12 카테고리 합의 매트릭스
