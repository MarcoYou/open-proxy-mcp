---
type: architecture
title: 확장 훅 — 설치된 확장 패키지가 있으면 부르고, 없으면 조용히 건너뛴다
updated: 2026-09-06
---

# 확장 훅 (260906)

공개 서버는 `open_proxy_mcp/extensions.py` 로 두 entry point 그룹을 찾는다.

| 그룹 | 계약 | 부르는 곳 |
|---|---|---|
| `open_proxy_mcp.extensions` | `register(mcp)` | `server.py` 가 도구·프롬프트·리소스를 다 건 뒤 한 번 |
| `open_proxy_mcp.hints` | `hint(rcept_no, title=None, no=None) -> str` | 도구가 파싱이 약하거나 값을 못 찾은 자리에 「원문 위치」 한 줄을 붙일 때 (`extensions.origin_hint`) |

## 왜
공개 레포에 넣지 않기로 한 기능이 있어도, **배포하는 서버**에는 있어야 하고 코드는 한 곳에만 있어야 한다.
그래서 공개 레포에는 훅만 두고, 확장 패키지는 배포 이미지에서 설치한다. 레포를 클론만 한 사람은
훅이 비어 있는 서버를 얻고, 동작 계약은 같다 — 확장이 있으면 줄이 하나 더 붙고 없으면 그 줄이 없다.

## 규칙
- 확장 하나가 실패해도 서버·나머지 확장은 산다(`load_extensions` 가 예외를 삼키고 warning 만).
- 훅이 빈 문자열을 주면 도구는 줄을 붙이지 않는다. 확장이 없을 때 도구 본문은 종전과 같다.
- 훅을 부르는 도구: `business_details`(응답 머리·저신뢰 문맥) · `financial_notes`(읽은 절·못 찾은 필드) ·
  `asset_holdings`(`extraction_failed`·`cross_reference`·발췌 있는 부재) · `shareholder_meeting_notice`(약한 파싱 경고).
- 테스트 `tests/test_extension_hooks.py` — 확장 없음·가짜 확장·깨진 확장 세 갈래.

## 배포
Dockerfile 의 빌드 시크릿 `opm_ext_spec` 에 설치 스펙(pip 가 받는 문자열)이 있으면 `/app/.venv` 에 설치하고,
없으면 건너뛴다. 무엇을 설치하는지는 시크릿에만 있다 — 공개 레포에는 「슬롯이 있다」는 사실만.
```bash
SPEC="$(cat ~/.opm/ext_spec)"
fly deploy --build-secret opm_ext_spec="$SPEC" --build-arg OPM_EXT_REV="$(printf %s "$SPEC" | shasum -a 256 | cut -c1-16)"
```
`OPM_EXT_REV` 는 스펙의 해시다(비밀 아님). BuildKit 은 시크릿 내용을 캐시 키에 안 넣어서, 이게 없으면 시크릿 없이
빌드한 층이 재사용돼 확장이 조용히 빠진다(260906 원격 빌드 실측). 설치 층 끝에서 entry point 가 실제로 있는지 assert 한다.
slim 이미지엔 git 이 없어 git+https 스펙이면 같은 층에서 git 을 깔고 지운다. 런타임에 토큰은 남지 않는다.
클론한 사람은 시크릿 없이 빌드해 확장 없는 서버를 얻는다 — 빌드는 그대로 된다.

## 관련
[[mcp-endpoints]] · [[financial_notes]] · [[asset_holdings]] · [[business_details]] · [[shareholder_meeting_notice]]
