---
type: decision
title: 보수한도 / 퇴직금 안건 분리 — 이사·감사 분기 + 정관 hybrid 통합
date: 2026-05-05 19:00
status: adopted
related:
  - wiki/decisions/open-proxy-guideline.md
---

# 보수한도 / 퇴직금 안건 분리

## 배경

보수한도와 퇴직금을 한 함수(`_decide_compensation`)로 처리하면 두 가지 갭이 생긴다.

1. **퇴직금 자동 FOR**: 퇴직금에는 인상률 데이터가 없어 NO_DATA / fm_fallback FOR로 떨어진다 —
   회사 추천 퇴직금 변경이 사실상 자동 FOR가 된다.
2. **이사 vs 감사 미분리**: parser는 target("이사"/"감사")을 나누지만 결정은 합산 처리한다.
   감사는 독립성이 본질이라, 회사가 보수를 늘려주면 회사 편을 들 인센티브가 생긴다.

### 자동 FOR 이 왜 위험한가 — 운용사 실제 행사 분포

`data/asset_managers/` 행사내역 22파일(2024-2026) 기준. 절대 다수가 FOR 이지만 **다섯 번에 한 번꼴로
AGAINST 가 나온다** — 자동 FOR 로 처리하면 그 한 번을 통째로 놓친다.

| 안건 | 전체 | FOR | AGAINST | 편차 |
|---|--:|--:|--:|---|
| 퇴직금 | 373 | 298 (79.9%) | 75 (20.1%) | 한 운용사 31.4% AGAINST(최고) · 한 외국계 100% FOR |
| 이사 보수한도 | 3,139 | 2,591 (82.5%) | 479 (15.3%) | 평균적 |
| 감사 보수한도 | 927 | 772 (83.3%) | 150 (16.2%) | **한 운용사 43% AGAINST**(187 FOR / 141 AGAINST) |

감사 보수한도에서 한 운용사만 43% 반대라는 편차가 ②(이사 vs 감사 미분리)의 근거다. 시장 평균에
묻어가는 자동 FOR 이 아니라 **변경 raw 노출 + REVIEW 기본**으로 두는 이유가 여기 있다.

## 결정

### 1. 카테고리 분리 (3개)
- `director_compensation` — 이사 보수한도 (기존 강화)
- `audit_compensation` — 감사 보수한도 (NEW)
- `retirement_pay` — 단독 퇴직금 안건 (NEW, fallback)

### 2. 정관 안에 묶인 case는 articles_amendment에서 hybrid 통합
한국 회사 관행상 퇴직금/보수 변경은 대부분 "정관 일부 변경" 형식. 정관이 본질, 퇴직금/보수는 사유.

→ `_decide_articles_amendment` 안에서 키워드 hit 시 helper 재사용:
```python
if "퇴직금" in t or "퇴임위로금" in t:
    return _decide_retirement_pay(retirement_payload, fin_metrics_payload)
if "보수한도" in t and "감사" in t:
    return _decide_audit_compensation(comp_payload, fin_metrics_payload)
if "보수한도" in t:
    return _decide_director_compensation(comp_payload, fin_metrics_payload)
```

같은 helper 재사용 → 결정 logic 중복 X.

### 3. 결정 분기 표 (지표 → 정책 → 결정)

#### 이사 보수한도 (13 분기)
1. 자본잠식 + 인상 → AGAINST (OPM)
2. 소진율 30% 미만 + 인상 → AGAINST (OPM mainstream)
3. 적자/순익 yoy < 0 + 인상 → AGAINST (OPM #2)
4. +10~+30% + 순익 yoy < 5% → REVIEW (N연기금 IV-33②)
5. ≥+50% → REVIEW (OPM #8)
6. +30~+50% → REVIEW
7. 소진율 ≥100% + 인상 → FOR (한도 부족 정당화)
8. 한도 감액 (-10% 미만) → FOR
9. -10~+10% (동결) → FOR (N연기금 IV-33①)
10. +10~+30% + 순익 양호 → FOR (N연기금 IV-33①)
11-13. 데이터 부족 fallback (자본 양호 → FOR / 잠식 → AGAINST / 둘 다 X → NO_DATA)

#### 감사 보수한도 (11 분기) — N연기금 IV-34 양방향
1. 자본잠식 + 인상 → AGAINST
2. 소진율 30% 미만 + 인상 → AGAINST
3. 1인당 평균 < 5천만원 → AGAINST (N연기금 IV-34 과소)
4. ≥+50% + 1인당 평균 > 1억 → AGAINST (s_legacy 패턴, 경영진 동조 인센티브)
5. +30~+50% → REVIEW
6. 1인당 평균 5천~1억 → REVIEW (경계)
7. -10~+10% → FOR
8. 1인당 평균 ≥1억 + +10~+30% → FOR
9-11. 데이터 부족 fallback

#### 퇴직금 (12 분기) — N연기금 IV-35 + OPM #6/#7
1. 황금낙하산 / 경영권 변동 special 가산 → AGAINST (N연기금 IV-35①)
2. 사외이사 퇴직금 신설 → AGAINST (OPM #6)
3. 지급률 ≥2배수 인상 → AGAINST (s_legacy strict)
4. 자본잠식 + 변경 → REVIEW
5. 한도/규정 신설 → REVIEW (단, 신설이 모두 형식적 키워드만 포함이면 분기 9a로)
6. 지급 대상 확장 → REVIEW
7. 가중치/배수 인상 (소폭) → REVIEW
8. 위험 키워드 hit → REVIEW
9a. 퇴직연금 제도 도입 (after에 확정급여형/확정기여형/퇴직연금제도 hit, 위험 hit 0) → FOR (형식적)
9b. 변경 사유 (reason)에 법령/상법/개정 hit → FOR (형식적)
10. amendments ≥1, 위험 hit 0 → REVIEW (raw 노출)
11. amendments 0 → FOR
12. parser fail → NO_DATA

## 정책 카탈로그 (2 layer 원칙)

```
Layer 1: 정책 (open_proxy_v1.json + 운용사 7 + nps) — 트리거 카탈로그
Layer 2: 결정 코드 (_decide_*) — 자동 trigger wire + 정성은 facts raw 노출
```

자동 trigger:
- OPM #2 (적자 + 한도 증액): 결정 트리에 wire
- OPM #7 (황금낙하산): 키워드 매칭 wire
- OPM #8 (50%+ 인상): 결정 트리에 wire
- N연기금 IV-33②, IV-34, IV-35: 결정 트리에 wire

정성 trigger (facts raw 노출 → LLM 판단):
- OPM #1 (성과 미연계 보상)
- OPM review #1 (5억원+ 임원 + 동종업계 P75)

## 영향 범위

- `services/proxy_advise.py`: `_classify_agenda` 카테고리 분리, `_decide_director_compensation` 강화 (13 분기), `_decide_audit_compensation` 신규 (11 분기), `_decide_retirement_pay` 신규 (12 분기), `_decide_articles_amendment` hybrid 통합
- chain: `parse_retirement_pay_xml` 같은 doc에서 통합 fetch (extra DART 호출 0)
- audit harness `scripts/ralph_compensation_retirement_audit.py`: articles_amendment hybrid reason-based detection
- 운용사 majority cache `_majority_cache_compensation_retirement.json` (G3 검증용 일회성 산출물 — 코드가 읽지 않아 260902 에 `build_manager_majority_cache.py` 와 함께 open-proxy-storage `wiki-private/archive/opm-scripts/` 로 이관)

## 비목표

- 스톡옵션 부여 안건 (OPM #3-5, #10) — 별도 카테고리 + 별도 ralph
- 임원별 개별 보수 5억원+ 본문 분석 — 별도 endpoint
- 동종업계 P75 비교 — 별도 chain

---

## 검증 (KOSPI 200 + KOSDAQ 50, n=226)

| 카테고리 | n | 분포 | G1 | G3 |
|---|---|---|---|---|
| director_compensation | 132 | 130 FOR / 1 AGAINST / 1 NO_DATA | **99.2%** ✓ | **100% (11/11)** ✓ |
| audit_compensation | 39 | 39 FOR | **100%** ✓ | **100% (1/1)** ✓ |
| retirement_pay | 14 | FOR 4 / REVIEW 6 / **AGAINST 4** | **100%** ✓ | majority 0 |

**AGAINST 5건 모두 정확 분기**:
- 피에스케이 / 피에스케이홀딩스 / GST 퇴직금 → 지급률 2배수+ (s_legacy strict 패턴)
- 카카오페이 퇴직금 → 사외이사 퇴직금 신설 (OPM #6)
- 퓨쳐메디신 보수한도 → 자본잠식 + 인상 (OPM Guideline)

**REVIEW 6건** — SK바이오팜/LIG넥스원/에코프로비엠 등 raw 검토가 필요한 case. "퇴직연금 제도 도입"처럼
형식적 변경은 분기 9a로 FOR.

**정밀화 (현행 동작)**:
1. `parse_retirement_pay_xml`: anchor 검출 + 표 head 키워드(현재/개정(안)/개정전후) + 표 본문 "퇴직" broad-match
2. `financial_metrics` summary scope가 prev_net_income/yoy_pct를 노출 → 흑자+yoy<0 trigger 활성
3. 소진율 단독 분기: 소진율<30% + 인상률 미파악/동결 → REVIEW

**G4 N연기금 정합 100%**: 모든 AGAINST가 N연기금 [별표 1] IV-33/34/35 + OPM Open Proxy v1.3 #6/#7/#8 trigger와 일치.
