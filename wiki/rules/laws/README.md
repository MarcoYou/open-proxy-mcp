---
type: readme
title: rules/laws/ — 한국 자본시장 법령 자료
updated: 2026-05-25
---

# wiki/rules/laws/ — 한국 자본시장 법령 자료

> 한국 상장사 거버넌스 관련 강행규정 + 우회 catalog. proxy_advise의 법령 layer 출처.

## 핵심 master 파일

| 파일 | 종류 | 용도 |
|---|---|---|
| **`상법-2025-2026-종합.md`** | 사람 가독 | 1·2·3차 상법 개정 + 정관 우회 시나리오 + catalog. **유일 master** |
| **`law_layer_rules.json`** | 머신리더블 | proxy_advise._law_layer 직접 로드. **40 룰** (A1=10 / A2=5 / B1=12 / B2=9 / C=4). A1-10 집중투표 조 분리 삭제 추가 |

## 사용 흐름

### 분석가 / LLM (사람)
→ `상법-2025-2026-종합.md` 읽기 (시행 일정 + 적용 대상 + catalog)

### 코드 (proxy_advise)
→ `law_layer_rules.json` 로드 + 룰 sequential evaluation

### 시행 일정/패턴 변경 시
→ `law_layer_rules.json` JSON 수정 (코드 변경 X)
→ 인간 가독 위해 `상법-2025-2026-종합.md`도 동기화

## 옛 분산 자료 (archive)

`wiki/archive/laws/`에 보존 (역사):
- `상법개정-2025-2026-통합본.md` — 종합본의 1차/2차/3차 부분 (260508 만든 후 통합)
- `상법개정-타임라인-2026.md` — 옛 타임라인 (2026 시점, M레거시 리서치 출처)
- `정관-우회-시나리오-2026.md` — 종합본의 우회 catalog 부분 (260508 통합)
- `주총방어-시나리오-4가지.md` — 출처 인용용 (M레거시 리서치)
- `주총체크리스트-2026.md` — 출처 인용용

## 관련 페이지

- [[상법-2025-2026-종합]] (master)
- `law_layer_rules.json` (master)
- law-layer-260508 (lesson — 도입 배경)
- law-layer-precision-260508 (lesson — Ralph 4 정밀화 280 회사 검증)
- 260508_0200_decision_law-layer (decision — 도입)
- 260508_0700_decision_law-layer-precision (decision — 정밀화)
- open-proxy-guideline (OPM 5 기준 + voting_rules 12 카테고리)

## 신규 자료 추가 시

1. **법령 자료**: `상법-2025-2026-종합.md` 본문에 추가 (또는 새 master 페이지)
2. **코드 룰**: `law_layer_rules.json`에 항목 추가
3. **출처 reference 자료**: archive에 추가 + 본 README link

→ 신규 분산 X. 항상 master 1개 + archive (보존).
