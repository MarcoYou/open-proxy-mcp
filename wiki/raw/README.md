---
type: readme
title: raw/ — 외부 원본 (불변)
---

# raw/ — 외부 원본

> 이 폴더의 파일은 **절대 수정 금지**. 외부 source. `git pull` 외엔 read only.

## 구조

| 폴더 | 내용 | 개수 |
|---|---|---|
| `references/` | 외부 markdown / JSON (JPM 프로세스 원문·요약, NPS 행사내역 요약, DART-KIND 매핑 화이트리스트) | 4 |

운용사 정책 PDF·행사내역 xlsx 원본은 레포에 두지 않는다 — 파싱 결과 JSON 만 `open_proxy_mcp/data/asset_managers/{policies,records}/` 에 있다.

## 원칙 (Karpathy LLM-wiki)

1. **원본 보존**: LLM은 raw/ 안 파일을 절대 수정하지 않음
2. **참조 전용**: 내용을 인용하거나 분석할 때만 사용
3. **추가 가능**: 새 외부 source 추가는 OK, 단 기존 파일은 그대로
4. **요약은 별도**: 요약/분석 페이지는 `decisions/`, `rules/`에 작성

## 활용 흐름

```
raw/policies/X.pdf  →  parsed JSON (open_proxy_mcp/data/asset_managers/policies/X.json)
                    →  요약 페이지 (decisions/ 또는 rules/)
```

## 신규 source 추가 시

1. raw/ 안 적절한 하위 폴더에 파일 그대로 배치 (rename 가능)
2. ingest 작업으로 요약/분석 페이지 생성 (raw 외부에 작성)
3. wiki_index.md에 라인 추가
