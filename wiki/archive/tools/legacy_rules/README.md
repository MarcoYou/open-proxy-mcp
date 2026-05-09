---
type: readme
title: Legacy Tool Rules (구 *_RULE.md)
updated: 2026-05-09
---

# Legacy Tool Rules

OPM v1 시대 tool 규칙 markdown. 2026-05-09 `open_proxy_mcp/`에서 archive로 이동 (코드와 분리).

## 잔존 7개

| 파일 | 도메인 | tool 매핑 (현 v2) |
|---|---|---|
| `AGM_CASE_RULE.md` | 주총 case 규칙 | shareholder_meeting_notice / shareholder_meeting_results |
| `AGM_TOOL_RULE.md` | 주총 tool 규칙 | shareholder_meeting_notice / proxy_advise_before_meeting |
| `DIV_CASE_RULE.md` | 배당 case 규칙 | dividend |
| `DIV_TOOL_RULE.md` | 배당 tool 규칙 | dividend |
| `OWN_CASE_RULE.md` | 지분 case 규칙 | ownership_structure |
| `OWN_TOOL_RULE.md` | 지분 tool 규칙 | ownership_structure |
| `PRX_TOOL_RULE.md` | 위임장 tool 규칙 | proxy_contest |

## 현 위치

archive `wiki/archive/tools/legacy_rules/`. 정책상 archive는 역사 보존 (수정 X, 신규 X).

## 흡수 진행 중

각 규칙은 점진적으로 다음 영역에 흡수됨:
- **도메인 지식** → `wiki/rules/concepts/` + `wiki/rules/disclosures/`
- **OPM 정책** → `wiki/decisions/open-proxy-guideline.md`
- **tool 명세** → `wiki/tools/{name}.md` + frontmatter
- **법령 layer** → `wiki/rules/laws/law_layer_rules.json` (Ralph 4)
- **분류 logic** → `open_proxy_mcp/services/proxy_advise.py` 등

흡수 완료 후 archive 보존 (참조용).

## 관련

- [[../README]] — archive/tools 인덱스
- [[../../../decisions/tool-changelog]] — tool rename/흡수 history
