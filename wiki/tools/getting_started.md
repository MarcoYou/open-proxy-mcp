---
type: tool
title: getting_started
domain: discovery
scope: []
data_source: []
related_decisions: [260721_1600_decision_getting-started-tool-vs-resource]
created: 2026-07-21
---

# getting_started

## 한 줄 요약
"OPM으로 뭐 할 수 있어?" 같은 포괄적 capability 질문에 답하는 tool. 등록된 25개 tool의 `desc:`
필드를 **매 호출 시 런타임에 그대로 추출**해 카테고리별로 정리해 반환 — 하드코딩된 markdown이
아니라 실제 tool 목록에서 자동 생성되므로 tool이 추가/제거돼도 항상 최신이다.

## 사용법
- `getting_started()` — 인자 없음. DART 호출 0.
- 사용자가 "OPM으로 뭐 할 수 있어?", "무슨 기능 있어?", "what can this do?" 처럼 특정 회사·데이터가
  아닌 서버 전체를 묻는 질문을 할 때 캐릭터(AI)가 이 tool을 호출하도록 `when:` 필드에 실제 질문
  문구를 나열해뒀다.

## 배경 — 왜 만들었나
4인 전문가 패널 토론(MCP 프로토콜·LLM tool-use·멀티클라이언트·DX 엔지니어) 결과([[260721_1600_decision_getting-started-tool-vs-resource]]) 결정. 검토 중 **과거 v1 toolset의 `tool_guide`**
(`open_proxy_mcp/tools/guide.py`)가 v2 재설계 후 완전히 단절된 채 방치돼, 현재 등록된 tool 이름과
하나도 안 겹치는 죽은 코드가 된 사실을 발견 — 하드코딩 markdown 가이드는 반드시 썩는다는 반면교사가
됐다. 그래서 이번엔 **콘텐츠를 손으로 안 쓰고 런타임에 조립**하도록 설계했다: 카테고리 그루핑만
최소 `name→category` 매핑으로 유지하고, 매핑에 없는 새 tool은 "기타"로 떨어질 뿐 절대 누락되지
않는다(무매핑 시 침묵 삭제 방지 — 옛 tool_guide는 정확히 이 실패를 겪었다).

## 출력
등록된 tool을 7개 카테고리(기본/전체시장 스캔/주주총회·의결권/지분·재무·밸류에이션/주주환원·자본/
분쟁·거래·리스크/근거·참조)로 묶은 markdown. 각 항목은 `- **\`tool_name\`** — {desc: 필드 원문}`
형태. 마지막 줄에 실제 등록 tool 개수를 명시(자기 자신은 목록에서 제외).

## 파싱 전략
`mcp.list_tools()`로 런타임에 등록된 `Tool` 객체 전체를 가져와, 각 `description`(=Python docstring
원문)에서 정규식 `desc:\s*(.+?)(?:\n\s*when:|\Z)`로 desc 구간만 추출·개행정리. `tools_v2/__init__.py`의
`pkgutil.iter_modules` 자동 등록 덕에 이 파일 자체도 별도 등록 없이 자동 인식된다.

## 알려진 issue·TODO
- 카테고리 매핑(`_CATEGORY` dict)은 여전히 손으로 유지 — 새 tool 추가 시 매핑을 안 넣으면 "기타"로
  가지만(누락은 안 됨) 분류는 부정확해짐. tool 추가 시 습관적으로 `getting_started.py`의
  `_CATEGORY`도 함께 갱신할 것.
- LLM tool-use 패널 의견: capability형(자기참조) 질문은 모델이 "이미 아는 얘기"로 판단해 tool
  호출을 생략하는 경향(과소호출)이 있을 수 있음 — `instructions` 필드에 중복 힌트를 넣어뒀지만,
  실제 3개 클라이언트(Claude/ChatGPT/Perplexity)에서 호출률 실측 검증은 아직 안 함.

## 관련
- [[260721_1600_decision_getting-started-tool-vs-resource]] (설계 결정 — tool vs resource vs 무대응)
- `open_proxy_mcp/tools/guide.py` (v1 `tool_guide`, 현재 프로덕션 미등록 — 반면교사 사례)
