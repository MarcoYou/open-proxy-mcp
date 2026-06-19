---
type: readme
title: 350 회사 법령 layer audit (KOSPI 200 + KOSDAQ 150)
date: 2026-05-10
---

# 350 회사 법령 layer audit

LG화학 misread 사례 fix 후 회귀 검증. A1-3 / B1-10 분리선출 패턴 정밀화 + production wiki/rules/laws/ 누락 fix 후.

## 통계

| 차원 | 값 |
|---|---|
| 회사 | 350 (KOSPI 200 + KOSDAQ 150) |
| exact | 331 (94.6%) |
| 자산 2조+ | 175 |
| 안건 | 3535 |
| hits | 265 (7.5% hit rate) |

## Rule hits 분포

| Rule | Hits | 비고 |
|---|---|---|
| A1-1 (집중투표 배제 삭제/변경) | 66 | "변경" 키워드 추가 후 보강 |
| A1-2 (집중투표 도입) | 7 | |
| **A1-3 (분리선출 의무 충족)** | **22** | ★ LG화학 fix 후 정확 catch |
| A1-4 (의결권 제한 강화) | 27 | |
| A1-5 (독립이사 명칭) | 77 | |
| A1-6 | 1 | |
| A1-7 (전자주주총회) | 58 | |
| B1-4b (후보 임기 1년) | 3 | |
| B1-7 (정원 축소) | 2 | |
| B2-8 (자발 강화) | 2 | |

## 충돌 / False positive

- 한 안건 다중 hit: **0건** ✓
- A1-3 / B1-10 분기: A1-3 22건 (의무 충족) / B1-10 0건 (350 sample 의무 초과 케이스 없음)

## 놓친 것 (false negative) 발견 + fix

### 1. 에코프로 "집중투표 배제 조항 **변경**의 건"
- A1-1 패턴 `secondary_then`이 ["삭제", "폐지", "제거"]만 → "변경" 누락
- **fix**: secondary_then에 ["변경", "개정"] 추가 (이번 commit)
- 회귀 검증: "집중투표 한도 변경" / "집중투표 도입" 미매치 유지 ✓

### 2. 본문 검사 필요 (별도 ralph)
다음 회사들은 sub-agenda 없이 top-level "정관 일부 변경의 건"만 표시 → spot script가 hierarchy 못 펼침. parse_aoi_xml body 매칭 필요:
- 에코프로비엠 / 카카오게임즈 / 에스엠 / 메리츠금융지주 등

## 미사용 룰 (28개)

| 그룹 | 룰 | 비고 |
|---|---|---|
| 시행 전 (5) | A2-1~A2-5 | 2026-07-23 / 09-10 시행 후 자연 catch |
| C signal (4) | C-1~C-4 | agenda 비대상 (ownership signal) |
| 자산 2조+ 한정 (5) | A1-8, B1-6, B1-8, B1-9 | 광범위 sample 부족 |
| 광범위 부족 (13) | B1-1~B1-3, B1-5, B2-1~B2-7, B2-9 | specific 패턴 |
| 의무 초과 (1) | B1-10 | 350 sample 0건 |

## artifacts

- `kospi_200.json` (200 회사 spot)
- `kosdaq_150.json` (150 회사 spot)
