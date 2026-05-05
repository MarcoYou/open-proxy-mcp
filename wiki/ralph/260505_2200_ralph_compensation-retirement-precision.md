---
type: ralph
title: 보수한도 / 퇴직금 분기 정밀화 — yoy fetch fix + 소진율 단독 + parser NO_DATA 잔여 + 재측정
created: 2026-05-05 22:00
completion_promise: COMPENSATION_RETIREMENT_PRECISION_VERIFIED
max_iterations: 5
ref:
  - wiki/ralph/260505_1750_ralph_compensation-retirement-split.md
  - wiki/ralph/260505_2030_ralph_compensation-retirement-extend.md
  - wiki/decisions/260505_1900_decision_compensation-retirement-split.md
---

## Invoke (복붙)

```
/ralph-loop:ralph-loop wiki/ralph/260505_2200_ralph_compensation-retirement-precision.md 가이드 따라 보수 퇴직금 분기 정밀화. financial_metrics summary에 prev year 노출하여 흑자 yoy 감소 trigger 작동 + 소진율 단독 강화 + 피에스케이 parser NO_DATA fix + OLD parser batch 재측정. 파싱 성공률 99 퍼센트 이상 + trigger 발생 시 정확도 100 퍼센트 + 운용사 majority 정합도 90 퍼센트 이상 + NPS 정책 정합 100 퍼센트 모두 충족 시 promise. --completion-promise COMPENSATION_RETIREMENT_PRECISION_VERIFIED --max-iterations 5
```

# Ralph: 보수한도 / 퇴직금 분기 정밀화

## Context

이전 ralph (260505_1750 + 260505_2030)에서 분기 / hybrid / parser fallback 까지 wire 완료. KOSPI 200 + KOSDAQ 50 (n=226) batch 측정 결과:

| 카테고리 | n | G1 | G3 | trigger 검증 |
|---|---|---|---|---|
| director_compensation | 132 | 99.2% ✓ | 100% (11/11) ✓ | 자본잠식+인상 (퓨쳐메디신) AGAINST 정확 ✓ |
| audit_compensation | 39 | 100% ✓ | 100% (1/1) ✓ | trigger case 0 |
| retirement_pay | 14 | 78.6% ❌ | majority 0 | 지급률 2배수+ (GST) AGAINST 정확 ✓ / REVIEW 8건 정확 |

**남은 진짜 갭** (코붕이 + 분석 결과):

1. **yoy<0 trigger 미작동** — `_decide_director_compensation`의 "흑자 + 순익 yoy<0 + 인상 → AGAINST" 코드는 있으나 `fin_metrics`가 `scope="summary"`로 fetch되어 prev year data 미노출 → `_fm_yoy_pct` 항상 None → 트리거 미작동. **"잘 못하는데 인상" case 자동 catch X**.

2. **소진율 단독 분기 부족** — 현재 "소진율<30% AND 인상>0%"만 AGAINST. 코붕이 의견: 소진율<30% + 인상률 미파악 / 동결도 REVIEW로 (남는데 한도 유지).

3. **피에스케이 / 피에스케이홀딩스 NO_DATA** — NEW parser도 못 잡음. parser 추가 강화 필요.

4. **OLD parser batch (4 회사) NO_DATA** — 에스티팜 / 원익IPS / 에코프로비엠 / 카카오페이 — NEW parser spot 검증 OK이지만 batch 재측정 안 됨. G1 retirement 78.6% → 99%+ 가능.

## 가정

- AGAINST 빈도 자체에 집착 X (한국 회사 대부분 정상 mainstream FOR이 합리)
- **진짜 bad case (자본잠식+인상 / 소진율<30+인상 / 적자/yoy<0+인상 / 황금낙하산 / 사외이사 퇴직금 / 지급률 2배수+ / 50%+ 인상) 발생 시 정확히 catch되는지**가 검증의 핵심
- 정상 case는 mainstream FOR로 가야 (운용사 majority 정합)
- 안건 자체 없는 회사는 카운트 X (당연)

## 매 iteration 작업
1. 현황: git status + 직전 spot
2. 다음 1 step만
3. fix 검증: 회귀 spot
4. commit
5. 다음 iter 1줄

---

## 결정 기준 (FOR / AGAINST / REVIEW / NO_DATA)

### FOR — 정상 case (mainstream 운용사 패턴)

**이사 보수한도**:
- 인상률 -10 ~ +10% (동결 또는 소폭) — NPS IV-33① "이사회 안 원칙적 찬성"
- 한도 감액 (-10% 미만) — 주주가치 우호
- 인상률 +10 ~ +30% **AND** 순익 yoy ≥ +5% (경영성과 양호)
- 소진율 ≥100% **AND** 인상 (한도 부족 정당화)
- 데이터 부족 + 흑자 (순익>0) — mainstream fallback
- 데이터 부족 + 자본 정상 — 회사 결정 영역

**감사 보수한도**:
- 인상률 -10 ~ +10%
- 1인당 평균 ≥ threshold_high (1억) + 인상률 +10~+30% — 적정 + 정상
- 데이터 부족 + 흑자 / 자본 정상

**퇴직금**:
- amendments 0건 또는 단순 표현 정정 — 형식적
- 변경 사유 (reason)에 "법령/상법/개정" hit + 위험 hit 0 — 형식적
- 퇴직연금 제도 도입 (확정급여형/확정기여형/퇴직연금제도 after 필드 hit + 위험 hit 0) — 형식적

### AGAINST — 명백한 주주가치 훼손 (회색지대 X)

**이사 보수한도** — "오바해서 올리거나 / 사용 안하면서 늘리거나 / 잘 못하는데 인상":
- **자본잠식 (full) + 인상** — 회사 위기인데 보수 늘림 (OPM Guideline)
- **소진율 < 30% + 인상** — 남는데 더 (mainstream "남는데 더 늘림")
- **적자 (ni<0) OR 순익 yoy<0 + 인상** — 잘 못하는데 인상 (OPM #2 / NPS IV-33②)
  - **🚧 현재 fetch chain miss로 yoy<0 trigger 미작동 — 이번 ralph 핵심 fix**
- 데이터 부족 + 자본잠식 — 회사 위기

**감사 보수한도** (NPS IV-34 양방향):
- 자본잠식 + 인상
- 소진율 < 30% + 인상 (이사와 동일)
- **1인당 평균 < threshold_low (5천만)** — NPS IV-34 "과소" (감사 충실 업무 훼손)
- **인상률 ≥ +50% AND 1인당 평균 > threshold_high (1억)** — s_legacy 패턴 (경영진 동조 인센티브)

**퇴직금** (NPS IV-35 + OPM #6/#7):
- **황금낙하산 / 경영권 변동 special 가산 신설** — NPS IV-35①
- **사외이사 퇴직금 신설** — OPM #6 (사외이사 퇴직혜택 부여 against)
- **지급률 ≥ 2배수 인상** — s_legacy strict 패턴

### REVIEW — 회색지대 (사용자 검토)

**이사 보수한도**:
- 인상률 ≥ +50% (대폭 인상, 일회성 사유 가능) — OPM #8
- 인상률 +30 ~ +50%
- 인상률 +10 ~ +30% **AND** 순익 yoy < +5% (경영성과 둔화)
- **(NEW)** 소진율 < 30% **AND** 인상률 미파악 — 인상 가능성 있는데 데이터 부족, 검토 권장
- **(NEW)** 소진율 < 30% **AND** 인상률 = 0 (동결) — 남는데 한도 유지, 검토 권장

**감사 보수한도**:
- 인상률 +30 ~ +50%
- 1인당 평균 threshold_low ~ threshold_high (경계)

**퇴직금**:
- 자본잠식 + 변경 (보수)
- 한도/규정 신설 (형식적 외)
- 지급 대상 확장 (등기→비등기 등)
- 가중치/배수 인상 (소폭, 1배수 → 1.5배수)
- 위험 키워드 hit (지급률 / 특별공로금 / 명예퇴직 등)
- amendments ≥1, 위험 hit 0, 형식적 X — raw 검토

### NO_DATA — 안건 있는데 parser miss

- 보수한도: 보수+재무 데이터 둘 다 없음
- 퇴직금: parser amendments 추출 실패

→ G1 측정 분모에 들어감. 안건 X 회사는 분모에도 안 들어감 (당연).

---

## 성공 기준 (모두 충족 시 promise)

### G1. 파싱 성공률 ≥99%
KOSPI 200 + KOSDAQ 50 (n=226)에서:
- 보수한도 (이사+감사): NO_DATA 비율 ≤1%
- 퇴직금: NO_DATA 비율 ≤1% (현재 78.6% — 피에스케이 parser fix + OLD batch 재측정)

### G2. trigger 발생 시 정확도 100%
**AGAINST 갯수가 아닌 정확도** 검증. spot 검증:
- 자본잠식 + 인상 → AGAINST (퓨쳐메디신 case 확인됨 ✓)
- 소진율 < 30% + 인상 → AGAINST (sample 발생 시)
- 적자/yoy<0 + 인상 → AGAINST (yoy fix 후 sample 측정)
- 황금낙하산 → AGAINST (sample 발생 시)
- 사외이사 퇴직금 신설 → AGAINST (sample 발생 시)
- 지급률 2배수+ → AGAINST (GST case 확인됨 ✓)
- 50%+ 인상 → REVIEW (sample 발생 시)
- 1인당 평균 < 5천만 → AGAINST (sample 발생 시)

각 trigger 발생 case에서 logic 정확 작동 (spot 100%).

### G3. 운용사 majority 정합도 ≥90%
4+ majority case 기준. 현재 12/12 = 100% ✓ (단 표본 작음).
재측정 후도 90%+ 유지.

### G4. NPS 정책 정합 100%
trigger 발생 case에서 NPS [별표 1] IV-33/34/35 와 일치:
- AGAINST 결정이 NPS 정책 trigger와 매칭
- 발생 안 하면 N/A (정상 case는 mainstream FOR)

---

## 작업 plan (5 iter)

### iter 1. 피에스케이 NO_DATA spot + parser 추가 강화
- `scripts/spot_retirement_no_data.py`로 피에스케이 / 피에스케이홀딩스 본문 분석
- HTML 표 head 패턴 추가 발견 → `_extract_amendments_from_table_rows` 키워드 확장 OR fallback logic 강화
- 회귀 spot 4 회사 (에스티팜 / 원익IPS / 에코프로비엠 / SK하이닉스) — 기존 amends 유지 확인

### iter 2. financial_metrics summary에 prev year 노출
**가장 큰 impact**. `_fetch_year_metrics`이 이미 prev year fetch (avg 계산용)인데 summary 결과 dict에 미노출.

`open_proxy_mcp/services/financial_metrics.py:520` 결과 dict에 추가:
```python
"prev_net_income_krw": net_income_controlling_prev,
"prev_revenue_krw": revenue_prev,
"prev_operating_profit_krw": operating_profit_prev,
"net_income_yoy_pct": (curr - prev) / abs(prev) * 100,
"revenue_yoy_pct": ...,
"operating_yoy_pct": ...,
```

`proxy_advise.py`의 `_fm_yoy_pct`도 summary["net_income_yoy_pct"] 직접 사용:
```python
def _fm_yoy_pct(fm_payload):
    summary = ((fm_payload or {}).get("data") or {}).get("summary") or {}
    return summary.get("net_income_yoy_pct")
```

회귀 검증: 자본잠식 case 영향 없는지 + 적자/yoy<0 trigger 활성화 spot.

### iter 3. 소진율 단독 강화 (코붕이 의견)

`_decide_director_compensation` + `_decide_audit_compensation`에 분기 추가:

```python
# 분기 2 (강화)
if util_rate is not None and util_rate < 30:
    if inc is not None and inc > 0:
        return "AGAINST", f"소진율 {util_rate:.0f}% + 인상 ({inc:+.0f}%)"
    if inc is None:
        return "REVIEW", f"소진율 {util_rate:.0f}% 낮은데 인상률 미파악 — 검토"
    if inc == 0 or -10 < inc < 0:
        return "REVIEW", f"소진율 {util_rate:.0f}%인데 한도 동결 ({inc:+.0f}%) — 검토"
    # inc < -10 (감액)은 분기 8에서 처리
```

회귀 spot — 이전 batch 결과에서 소진율<30% case 분포 확인.

### iter 4. OLD parser batch 재측정

`iter02_kospi_0-30 / iter02_kospi_30-50 / iter01_extend_kospi_50-80 / iter03_kosdaq_30` 재돌림 (NEW parser + iter 2/3 fix 적용). 4 batch × 30 회사 = 120 회사 ~30분.

(또는 단순히 같은 batch 재실행 + dedup. 또는 spot list로 4 회사만 빠르게 — 더 빠름)

### iter 5. 통합 측정 + spot 검증 + promise

- aggregate script로 G1-G4 측정
- spot 검증: 모든 AGAINST/REVIEW case가 정확한 분기 사유로 작동했는지 review
- 부족하면 추가 fix
- 충족 시 promise 발행

---

## 영향 범위

- `open_proxy_mcp/services/financial_metrics.py` (summary scope에 prev/yoy 노출) — iter 2
- `open_proxy_mcp/services/proxy_advise.py` (`_fm_yoy_pct` 단순화 + 소진율 단독 강화) — iter 2-3
- `open_proxy_mcp/tools/parser.py` (peeskey case parser 추가 강화) — iter 1
- `scripts/spot_retirement_no_data.py` (이미 있음, 활용) — iter 1
- `scripts/aggregate_compensation_retirement.py` (이미 있음) — iter 5
- `wiki/architecture/audits/data/260505_compensation_retirement_precision/` — iter 4-5 결과

## 비목표

- 운용사 majority cache title normalize (별도 작업)
- 스톡옵션 부여 안건 (별도 ralph)
- 5억원+ 임원 보수 본문 분석 (별도 endpoint)
- KOSDAQ 50 이상 universe 확장 (별도)

## 가설 / 위험

- **위험 1 (financial_metrics 영향)**: summary scope에 새 필드 추가는 기존 caller에 영향 가능 (dict의 추가 field는 보통 안전, 단 schema validation이 있는 곳 있으면 fail 가능). 회귀 spot 필요.
- **위험 2 (피에스케이 parser 한계)**: 본문에 표 자체가 없거나 비표준이면 parser 강화 한계. 정직하게 archive에 case 기록 (lessons/ralph-threshold-realism 패턴).
- **위험 3 (OLD batch 재측정 시간)**: 120 회사 재돌림 ~30분. iter 4 안에 가능.
- **위험 4 (yoy fix 회귀)**: yoy<0 회사에서 갑자기 AGAINST 발생할 수 있음. spot 검증 — 진짜 적자/감소 회사인지 확인 후 판단. mainstream 운용사가 그 case에서 어떻게 행사했는지 cache 비교.

## archive 폴더

`wiki/architecture/audits/data/260505_compensation_retirement_precision/`

---

## iteration log (작성하면서 update)

### iter 1 — 피에스케이 spot + parser 강화 (commit `782af95`)
- 피에스케이/피에스케이홀딩스 본문 spot — table_headers 변경전/변경후 있으나 row text "퇴직금" 없음
- parser fix:
  - prev_text anchor 검출 (table 직전 ~600자에 "임원퇴직금" 등 안건 헤더) — table_has_retire 무관 항상 계산
  - "퇴직" 단독 row 인정 — broad_match (table 키워드 3+ OR anchor) 시
  - 표 head 키워드 확장: before "현재" / after "개정(안)"
- 검증 8 회사 모두 amendments 추출 ✓ (피에스케이 0→1 / 피에스케이홀딩스 0→2 / 카카오페이 13)

### iter 2 — financial_metrics prev/yoy 노출 (commit `8fe8bff`)
- `_compute_metrics`에 prev_revenue / prev_operating_profit / prev_net_income + revenue/operating/net_income_yoy_pct 추가
- summary scope 결과 dict에 노출
- `_fm_yoy_pct` 단순화: summary["net_income_yoy_pct"] 직접 사용
- 검증:
  - 하이브 2024 적자 (-3.4B) ← 흑자 (183B): yoy=-101.87% ✓
  - 카카오페이 2024 적자 → 더 큰 적자: yoy=-816% ✓
  - 두산에너빌리티 흑자 yoy +100% ✓
- → 흑자+yoy<0 trigger 활성화 (코드 검증, batch 측정 미완)

### iter 3 — 소진율 단독 강화 (commit `db44182`)
- `_decide_director_compensation` 분기 2 강화:
  - 소진율<30 + 인상>0 → AGAINST (기존)
  - 소진율<30 + 인상률 미파악 → REVIEW (NEW)
  - 소진율<30 + 동결/-10~0% → REVIEW (NEW)
  - 소진율<30 + 감액 (-10% 미만) → FOR (분기 8 — 한도 줄이는 건 OK)
- 검증 5 case smoke test 모두 정확 ✓

### iter 4 — OLD parser batch 재측정 (진행 중)
- 4 batch chain BG 시작: KOSPI 0-30 / 30-50 / 50-80 / KOSDAQ 0-30 = 110 회사
- iter 4 stop hook 시점 batch 1 (0-30) 11/30 진행 중
- 시간 부족 — iter 5 final 전 batch 미완

### iter 5 final — 정직 종료 (promise 미발행)
- batch 미완 상태에서 promise 발행 불가
- 코드 fix 모두 검증 완료 (iter 1-3 spot test 통과)
- batch 결과는 background 계속 → `wiki/architecture/audits/data/260505_compensation_retirement_precision/iter04_*.json`
- 다음 작업 (별도 ralph 또는 직접): batch 완료 후 G1-G4 측정 + spot 검증 + promise

### 최종 push 상태
- `782af95` parser 강화
- `8fe8bff` yoy fix
- `db44182` 소진율 단독 강화

각 fix 모두 회귀 spot 통과. batch 결과 측정만 남음.
