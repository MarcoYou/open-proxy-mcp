---
type: tool
title: proxy_guideline
domain: reference
scope: [단일 조회]
data_source: [wiki/decisions/open-proxy-guideline.md (OPM 자체 의결권 행사 정책 v1.2)]
related_disclosures: [주주총회소집공고]
related_concepts: [정관변경, 집중투표, 감사위원-의결권-제한]
created: 2026-08-13
---

# proxy_guideline — 의결권 판단 기준 문서 조회

## 한 줄 요약
`proxy_advise_before_meeting` 의 판정 사유에 「OPM Guideline §재무제표 — 감사의견 적정 +
자본잠식 없음이면 찬성」처럼 붙는 **인용의 원문**을 읽는다. 회사·DART 무관, **API 호출 0**.

## 왜 이 tool 이 따로 있나
판정 사유에 실리는 인용은 `_POLICY_CITATIONS`(`services/proxy_advise.py`)에 손으로 적어둔
14줄짜리 **요약 한 줄**이다. 「왜 이게 찬성이냐」를 더 캐물으면 그 한 줄 뒤에 무엇이 있는지
확인할 방법이 없었다 — 정책 문서를 서버가 노출하지 않았기 때문이다.

같은 문서를 `opm://guideline` **resource** 로도 걸어 두었지만, 260813 실측에서
**Claude.ai 커넥터는 resource 를 모델에게 노출하지 않는다**는 것이 확인됐다(사용자
클라이언트가 "리소스·프롬프트가 하나도 안 보인다"고 응답). resource 만 두면 아무도
못 읽으므로 tool 로도 둔다.

## 입력
| 인자 | 뜻 |
|---|---|
| `section` | 비우면 전문. 값을 주면 **제목에 그 말이 들어간 절**만 (예: `재무제표`·`이사선임`·`정관`·`0-A`). 없는 절이면 사용 가능한 절 목록을 돌려준다 |

## 출력
정책 문서 마크다운 원문. 상한 120,000자(Claude.ai tool 결과 상한 약 150,000자 안).

## ⚠ 정책과 엔진은 의도적으로 다르다
문서 **§0-A 「정책 ↔ 엔진 정합표」** 가 그 간극의 공식 지도다. 정책이 `against` 를
선언해도 법령 강행규정·법정 결격 같은 hard trigger 가 아니면 엔진은 자동 반대 대신
**검토(REVIEW)** 로 두고 판단 재료를 애널리스트에게 넘긴다.
**이 문서만 읽고 「시스템이 자동 반대한다」고 읽으면 안 된다.**

## 알려진 한계 (260813)
- 문서·인용 라벨·판정 함수 셋이 **손으로 동기화**돼 있다. 이 tool 은 그중 문서를 읽게
  해줄 뿐, 셋의 정합을 보장하지 않는다.
- 배포 이미지에 문서가 없으면 그 사실을 그대로 말한다(무표시 열화 금지). 현재
  `Dockerfile` 은 `wiki/rules/laws/` 만 복사하므로, fly 배포본에서는 문서를 못 찾을 수 있다.

## 관련
- [[proxy_advise_before_meeting]] — 이 문서를 인용하는 쪽
- [[law_lookup]] — 법령 조문 자체를 묻는 조회기
- `decisions/open-proxy-guideline` — 원문 문서
