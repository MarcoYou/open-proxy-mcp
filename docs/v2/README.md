# OpenProxy MCP v2 문서

현재 `release_v2.0.0` 기준 설계 문서입니다.  
즉, 아직 운영 기준 문서라기보다 `다음 공개 표면`, `검증 정책`, `소스 정책`, `tool 구조 개편안`을 보는 문서군입니다.

## 핵심 방향

- `company`를 공통 입구로 둠
- 공개 표면을 `data tool` 중심으로 재구성
- `proxy_contest`를 분쟁 탭으로 분리
- `evidence`를 근거 확인 탭으로 분리
- `action tool`은 phase-2로 분리
- 기본 소스 정책은 `DART API / DART XML / whitelist 기반 KIND / Naver 보조`
- `PDF 다운로드`는 기본 경로에서 제외

## 바로 가기

- [release_v2 tool 아키텍처](../../wiki/analysis/release_v2-tool-아키텍처.md)
- [release_v2 public tool 검증 매트릭스](../../wiki/analysis/release_v2-public-tool-검증-매트릭스.md)
- [신규 tool 추가 검증 정책](../../wiki/decisions/tool-추가-검증-정책.md)
- [신규 tool 검증 템플릿](../../wiki/templates/tool-추가-검증-템플릿.md)
- [DART-KIND 매핑 화이트리스트](../../wiki/decisions/DART-KIND-매핑-화이트리스트-2026-04.md)

## 주요 data tool

```text
company
shareholder_meeting
ownership_structure
dividend
proxy_contest
value_up
evidence
```

## 주요 action tool

```text
prepare_vote_brief
prepare_engagement_case
build_campaign_brief
```

## v2를 이렇게 보면 된다

```text
v2 = 다음 릴리즈 설계 문서
   = data-first / evidence-aware / source-policy 중심
```

## 참고

현재 운영 문서는 [v1 문서](../v1/README.md)에서 따로 봐야 합니다.
