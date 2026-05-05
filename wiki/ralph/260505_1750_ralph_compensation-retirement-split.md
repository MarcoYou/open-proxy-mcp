---
type: ralph
title: 보수한도 / 퇴직금 안건 분리 — A 별도 함수 + B 이사·감사 분기 + 운용사·NPS 참조
created: 2026-05-05 17:50
completion_promise: COMPENSATION_RETIREMENT_SPLIT_VERIFIED
max_iterations: 8
---

## Invoke (복붙)

```
/ralph-loop:ralph-loop wiki/ralph/260505_1750_ralph_compensation-retirement-split.md 가이드 따라 A 퇴직금 별도 함수 신규 + B 보수한도 이사 감사 분기 추가 + 운용사 NPS reference. KOSPI 100 + KOSDAQ 50 표본 검증, 파싱 성공률 99 퍼센트 이상 + 이사 감사 분기 정확도 100 퍼센트 + 운용사 majority 정합도 90 퍼센트 이상 + NPS 정책 정합 100 퍼센트 모두 충족 시 promise. --completion-promise COMPENSATION_RETIREMENT_SPLIT_VERIFIED --max-iterations 8
```

# Ralph: 보수한도 / 퇴직금 안건 분리

## Context

코붕이 (2026-05-05): 이사·감사 보수한도 변경 + 퇴직금 안건이 현재 어떻게 처리되는지 확인 → 갭 발견:

1. **갭 1 (퇴직금)**: 카테고리 매칭 (proxy_advise.py:155) `"보수" or "보수한도" or "퇴직금" or "퇴임위로금"` → 모두 `director_compensation` → `_decide_compensation` 호출 → 인상률 데이터 없으니 NO_DATA / fm_fallback FOR 떨어짐. 즉 회사 추천 퇴직금 변경 = 사실상 **자동 FOR** (status quo bias의 한 형태). v1 parser `parse_retirement_pay_xml`은 변경전/변경후 raw 추출 가능하나 v2 chain에 wire 안 됨.

2. **갭 2 (이사 vs 감사)**: parser는 target ("이사" or "감사") 분리하나 `_decide_compensation`은 합산 처리. 감사는 독립성이 본질 — 회사가 보수 늘려주면 회사 편들 인센티브. mainstream과 NPS 모두 별도 룰 보유.

## 사전 검증 결과 (이번 ralph 출발점)

### 운용사 records (n=22 files, 2024-2026)

| 안건 | 전체 | FOR | AGAINST | 메모 |
|---|---|---|---|---|
| 퇴직금 | 373 | 298 (79.9%) | 75 (20.1%) | s_legacy 31.4% AGAINST (가장 strict), b_foreign 100% FOR |
| 이사 보수한도 | 3,139 | 2,591 (82.5%) | 479 (15.3%) | 평균적 패턴 |
| 감사 보수한도 | 927 | 772 (83.3%) | 150 (16.2%) | **s_legacy 43% AGAINST** (outlier, 187 FOR / 141 AGAINST) |

→ 절대 다수 FOR이지만 5번에 1번꼴로 AGAINST 발생. mainstream에 묻어가는 자동 FOR이 아니라 **변경 raw 노출 + REVIEW 기본**이 데이터 근거.

### 정책 카탈로그 (8 운용사 + NPS + OPM)

`data/asset_managers/policies/`에 모두 보유 (records만 있는 게 아님):
- 운용사 8: m_legacy / s_legacy / sa_active / k_legacy / t_activist / a_activist / c_activist / b_foreign
- 공기관: nps
- **OPM 자체**: `open_proxy_v1.json` (Open Proxy Guideline v1.3, 12 카테고리 116 룰 — 운용사 공통 모범 + 행동주의 + 신규 보강)

#### OPM `open_proxy_v1` director_compensation — 11 against trigger

```
1.  성과 미연계 경영진 보상체계 (정성 — LLM)
2.  적자/순익 감소 기업의 인당·총 보수한도 증액 (자동)
3.  스톡옵션 사후 행사가격 조정·재발행 — Repricing (키워드, 별도 안건)
4.  스톡옵션 권리행사 유보기간 단축 (2년 미만) (키워드, 별도 안건)
5.  스톡옵션 규모 발행주식수 2% 초과 / 누적 희석률 5%+ (자동, 별도 안건)
6.  사외이사 퇴직혜택·성과급·스톡옵션 부여 (키워드)
7.  황금낙하산 — 연봉 3배 초과 (키워드, 퇴직금 안건)
8.  보수한도 직전년 대비 50%+ 인상 — M&A·IPO 일회성 외 (자동)
9.  [행동주의] 황금낙하산 일반 반대
10. [정량] 스톡옵션 희석률 성숙 5% / 성장 10% 초과 (자동, 별도 안건)
11. [c_activist 신규 v1.3] 임직원 자사주 구입 대출 (키워드)
```

→ **이번 ralph 범위**: 자동 검증 가능한 보수한도 trigger 2개 (#2, #8) + 퇴직금 키워드 trigger (#7, #9) wire. 스톡옵션 관련 5개 (#3-5, #6, #10)는 별도 ralph (스톡옵션 부여 안건 분리).

#### NPS `nps_2025-03` 별표 1

**이사 보수한도** (IV-33):
- default 원칙 FOR
- AGAINST: 한도가 보수금액 대비 과다 / 보수금액이 경영성과 대비 과다 / 중점관리사안 거부

**감사 보수한도** (IV-34):
- default 원칙 FOR
- AGAINST: 한도가 **과소** (감사 충실 업무이행 유인 훼손) — *너무 적으면 반대*. 운용사 (s_legacy "과다" 기준)와 결 다름. → OPM은 양방향 cover.

**퇴직금** (IV-35):
- 황금낙하산(Golden Parachute) **원칙적 반대** (정당한 사유 없으면)
- 중점관리사안 거부 시 반대

### 정책-코드 wire 원칙 (2 layer)

```
Layer 1: 정책 (open_proxy_v1.json + 운용사 7 + nps) — 트리거 카탈로그
Layer 2: 결정 코드 (_decide_*) — 자동 trigger wire + 정성은 facts raw 노출
```

- 자동 trigger: 결정 코드에 직접 wire (호출마다 정책 다시 파싱 X)
- 정성 trigger: `facts` / `risk_factors`에 raw 노출 → LLM이 정책 카탈로그 보고 판단

## 가정
- No conversation context / no web search / MCP only / deterministic
- 분당 DART 1000회 hard rule (rolling cap 900)
- v2 production (fly.toml `OPEN_PROXY_TOOLSET=v2`) 기준

## 매 iteration 작업
1. 현황: git status + 직전 검증
2. 다음 1 step만 (작게 쪼갬)
3. fix 검증: KOSPI 표본 spot 측정
4. commit
5. 다음 iter 1줄

---

## 성공 기준 (모두 충족 시 promise)

### G1. 파싱 성공률 ≥99%
KOSPI 100 + KOSDAQ 50 표본의 보수한도/퇴직금 안건 본문 → parser 추출 성공률 ≥99%.
- 보수한도 (`parse_compensation_xml`): items + summary 채움 (한도/소진율/인원수 중 ≥1 필드)
- 퇴직금 (`parse_retirement_pay_xml`): amendments ≥1건

부족 시 ACODE 추가 / fallback / 본문 패턴 추가. (lessons/ralph-threshold-realism: 표준 서식 ≥99% target valid.)

### G2. 이사 vs 감사 분기 정확 100%
보수한도 안건의 target ("이사" or "감사")가 parser에서 정확 분리되고 (이미 됨), `_decide_compensation` dispatch가 target별로 다른 logic 적용.

### G3. 운용사 majority 정합도 ≥90%
KOSPI 100 + KOSDAQ 50 표본의 보수한도/퇴직금 안건에서 OPM 결정이 4+ 운용사 majority와 정합. 4+ majority case 기준 ≥90%.

### G4. NPS 정책 정합 100%
NPS [별표 1] IV-33 / IV-34 / IV-35 명시 trigger와 OPM 결정이 일치.

---

## 지표 / 정책 / 결정 분기 표 (핵심)

### 1. 이사 보수한도 (`director_compensation`)

분기 우선순위 (first-match): hard trigger → 자동 trigger → 데이터 부족 fallback.

| # | 지표 (parser/financial_metrics) | 정책 근거 | 결정 |
|---|---|---|---|
| 1 | 자본잠식 full **AND** 인상률 > 0% | OPM Guideline (자본잠식 시 보수 결정 부적절) | **AGAINST** |
| 2 | 소진율 < 30% **AND** 인상률 > 0% | OPM mainstream ("남는데 더 늘림") | **AGAINST** |
| 3 | (적자 OR 순익 yoy < 0%) **AND** 인상률 > 0% | OPM #2 (적자/순익 감소 기업 한도 증액) — strict | **AGAINST** |
| 4 | 순익 yoy < +5% **AND** 인상률 +10 ~ +30% | NPS IV-33② 보수적 (경영성과 둔화 + 인상) | **REVIEW** |
| 5 | 인상률 ≥ +50% (경영성과 무관) | OPM #8 (50%+ 인상, 일회성 사유 외) | **REVIEW** |
| 6 | 인상률 +30 ~ +50% (#3-4 미해당) | OPM Guideline (대폭 인상 우려) | **REVIEW** |
| 7 | 소진율 ≥ 100% **AND** 인상률 > 0% | OPM Guideline (한도 부족 → 인상 정당화) | **FOR** |
| 8 | 인상률 < -10% (한도 감액) | NPS IV-33① + 주주가치 관점 (보수적) | **FOR** |
| 9 | 인상률 -10 ~ +10% (동결 or 소폭) | NPS IV-33① (이사회 안 원칙적 찬성) | **FOR** |
| 10 | 인상률 +10 ~ +30% **AND** 순익 yoy ≥ +5% | NPS IV-33① + 경영성과 양호 | **FOR** |
| 11 | 인상률 데이터 부족 + 흑자 + 자본 정상 | NPS IV-33① + mainstream fallback | **FOR** |
| 12 | 인상률 데이터 부족 + 자본잠식 | OPM Guideline (자본잠식 시 보수 결정 부적절) | **AGAINST** |
| 13 | 모든 데이터 부족 (보수 + 재무 둘 다 X) | — | **NO_DATA** |

**필요 데이터 chain**:
- `shareholder_meeting(scope=compensation)` → `compensation.summary.utilization_rate_pct` / `increase_rate_pct` / `current.total_amount` / `prior.total_amount`
- `financial_metrics(scope=yearly)` → `net_income_yoy_pct` / `net_income_krw` (적자 detect) / `capital_impairment_status`

### 2. 감사 보수한도 (`audit_compensation` — NEW 카테고리)

NPS IV-34는 운용사 mainstream과 결이 다름 (운용사: "과다" 우려, NPS: "과소" 우려). OPM은 둘 다 cover.

분기 우선순위: hard trigger → 자동 trigger → 데이터 부족 fallback.

| # | 지표 | 정책 근거 | 결정 |
|---|---|---|---|
| 1 | 자본잠식 full **AND** 인상률 > 0% | OPM Guideline (자본잠식 시 보수 결정 부적절) | **AGAINST** |
| 2 | 소진율 < 30% **AND** 인상률 > 0% | OPM mainstream ("남는데 더 늘림") | **AGAINST** |
| 3 | 1인당 평균 < threshold_low (잠정 5천만원/인) | NPS IV-34 (과소 → 감사 충실 업무 훼손) | **AGAINST** |
| 4 | 인상률 ≥ +50% **AND** 1인당 평균 > threshold_high | s_legacy 패턴 (감사 보수 급증 = 경영진 동조 인센티브) | **AGAINST** |
| 5 | 인상률 +30 ~ +50% | s_legacy 패턴 보수적 적용 | **REVIEW** |
| 6 | 1인당 평균 threshold_low ~ threshold_high (경계) | mainstream FOR이지만 사용자 노출 | **REVIEW** |
| 7 | 인상률 -10 ~ +10% (동결 or 소폭) | NPS IV-34 + mainstream FOR | **FOR** |
| 8 | 1인당 평균 ≥ threshold_high **AND** 인상률 +10 ~ +30% | NPS IV-34 + mainstream | **FOR** |
| 9 | 데이터 부족 + 흑자 + 자본 정상 | mainstream fallback | **FOR** |
| 10 | 데이터 부족 + 자본잠식 | OPM Guideline | **AGAINST** |
| 11 | 모든 데이터 부족 | — | **NO_DATA** |

**threshold 정의 (Step 6 calibrate)**:
- `threshold_low`: KOSPI 200 표본 분포의 25 percentile 또는 5천만원/인 (잠정)
- `threshold_high`: 50 percentile 또는 1억원/인 (잠정)

**필요 데이터**:
- `compensation.items[].target == "감사"` (parser 이미 분리됨)
- `summary.auditor_count` + `summary.auditor_total_limit_krw` → 1인당 평균 계산 (parser 추가 필요 여부 Step 0에서 확인)
- `compensation.summary.utilization_rate_pct` (감사 분리 필요 — Step 0에서 확인)

### 3. 퇴직금 (`retirement_pay` — NEW 카테고리)

`parse_retirement_pay_xml(html)` → `amendments[]` (변경전/후 비교) 활용.

분기 우선순위: hard trigger → 키워드 → 일반 변경 → fallback.

| # | 지표 (Step 0 sample 결과로 최종) | 정책 근거 | 결정 |
|---|---|---|---|
| 1 | 황금낙하산 신설 (`황금낙하산` / `경영권 변동 시 특별 지급` 키워드 hit) | NPS IV-35① + OPM #7, #9 (원칙적 반대) | **AGAINST** |
| 2 | 사외이사 퇴직금 신설 (대상에 `사외이사` 포함된 신규 조항) | OPM #6 (사외이사 퇴직혜택·성과급·스톡옵션 부여 against) | **AGAINST** |
| 3 | 지급률 ≥ 2배수 인상 (예: 1배수 → 2배수, 100% 인상) | s_legacy strict 패턴 (퇴직금 31% AGAINST) | **AGAINST** |
| 4 | 자본잠식 full **AND** 변경 amendments ≥ 1 | OPM Guideline (자본잠식 시 결정 부적절) | **REVIEW** |
| 5 | 퇴직금 한도/규정 신설 (없던 것을 신설) | OPM Guideline (경영진 보호 신호) | **REVIEW** |
| 6 | 지급 대상 확장 (등기임원 → 비등기임원 포함 등) | OPM Guideline (남용 우려) | **REVIEW** |
| 7 | 가중치/배수 인상 (소폭, 1배수 → 1.5배수) | OPM Guideline (사용자 검토) | **REVIEW** |
| 8 | 위험 키워드 hit ≥ 1 | OPM Guideline | **REVIEW** |
| 9 | 변경 사유 (reason 필드)에 `법령` / `상법` / `개정` hit **AND** 위험 hit 0 | NPS IV-35 default (형식적) | **FOR** |
| 10 | 위험 키워드 hit 0건 + amendments ≥ 1건 + 위 #9 미해당 | OPM Guideline (raw 노출 + 사용자 검토) | **REVIEW** |
| 11 | amendments 0건 또는 단순 표현 정정 (조항번호/문구) | NPS IV-35 default + mainstream FOR | **FOR** |
| 12 | parser 추출 실패 | — | **NO_DATA** |

**위험 키워드 list (Step 0 sample 결과로 확정)**:
- 1차 후보: `황금낙하산`, `경영권`, `변경시`, `배수`, `지급률`, `퇴임위로금`, `특별공로금`, `명예퇴직`, `사외이사`
- 형식적 변경 키워드 (FOR 분류용): `법령`, `상법`, `개정`, `정비`, `용어`, `명칭`
- 2차: 변경전/후 숫자 패턴 (예: `1배수` → `2배수`, 적용 범위 확장)

**필요 데이터 chain**:
- (NEW) `services/retirement_pay.py` → `build_retirement_pay_payload(company, year)` 
  - 안에서 소집공고 본문 fetch + `parse_retirement_pay_xml(html)` 호출
  - 안건 title에 "퇴직금" 또는 "퇴임위로금" 포함된 안건만 amendments 추출
- proxy_advise chain에 1번 호출 (회사 단위, 퇴직금 안건 detect 시만)
- `financial_metrics(yearly).capital_impairment_status` (이미 chain)

### 4. 공통 — facts raw 노출 (정성 trigger LLM 판단용)

자동 검증 안 되는 정성 trigger는 결정에 반영하지 않고 `facts` / `risk_factors`에 raw 노출 → LLM이 정책 카탈로그 보고 판단:

| raw 항목 | 정책 trigger (정성) | 노출 source |
|---|---|---|
| 5억원+ 임원 보수 list (이름·직책·금액) | OPM review #1 (개인별 5억원 + 동종업계 P75 초과) | DART 사업보고서 임원 보수 endpoint (Step 7+ 별도 처리) |
| 회사 규모 reference (시총/매출/자산) | OPM #1 (성과 미연계) — 규모 대비 적정성 | financial_metrics summary |
| 동종업계 평균 보수 (가능 시) | OPM review #1 (P75 비교) | 별도 chain (이번 ralph X) |
| 퇴직금 amendments raw 텍스트 (변경전/후) | NPS IV-35 + OPM #7 정성 부분 | parse_retirement_pay_xml 결과 |

---

---

## 작업 plan

### Step 0. 실제 퇴직금 데이터 spot 측정 (A 핵심 사전조사)

**목표**: parse_retirement_pay_xml이 실제로 어떤 데이터를 추출하는지 확인 + 의사결정에 활용 가능한 필드 식별.

`scripts/spot_retirement_pay.py`:
- KOSPI 200 + KOSDAQ 50에서 퇴직금 안건 있는 회사 ~10개 추출 (filing_search 안건 title 포함)
- 각 회사 소집공고 본문 fetch → `parse_retirement_pay_xml(html)` 호출
- 추출된 `amendments[]` 출력 (clause, before, after, reason)
- 패턴 분석:
  - 지급률 인상 (1배수 → 2배수)
  - 대상 확장 (등기임원 → 비등기임원 포함)
  - 가중치 변경
  - 적용 시점 변경
  - 보수기준 변경 (기본급 → 기본급+상여)
- 위험 키워드 추출 → `_RETIREMENT_RISK_KEYWORDS` 정의

### Step 0.5. 운용사 records cache 구축 (G3 검증용)

`scripts/manager_majority_index.py`:
- 22 records 파일 합산 → (company, agenda_title) → {manager: decision} dict 구축
- 4+ majority case 추출 (mainstream pattern)
- pickle 또는 JSON 저장 → audit harness에서 로드

### Step 1. `_decide_retirement_pay` 신규 함수

`open_proxy_mcp/services/proxy_advise.py`:
```python
_RETIREMENT_RISK_KEYWORDS = [
    "지급률", "배수", "황금낙하산", "전직", "경영권 변동", ...  # Step 0에서 정의
]

def _decide_retirement_pay(retirement_payload: dict | None) -> tuple[str, str]:
    """퇴직금 규정 변경 안건 — 변경전/후 비교.

    NPS [별표 1] IV-35 + s_legacy/k_legacy strict 패턴 반영.

    AGAINST: 황금낙하산 신설 / 지급률 큰 폭 인상 / 경영권 변동 시 special 가산
    REVIEW: 변경 내역 raw 노출, 위험 키워드 hit 1개 이상
    FOR: 단순 표현 정정 / 법령 반영 (형식적)
    NO_DATA: parser 추출 실패
    """
    if not retirement_payload or not retirement_payload.get("amendments"):
        return "NO_DATA", "퇴직금 변경 raw 추출 실패"

    amendments = retirement_payload["amendments"]
    # 위험 키워드 hit 검출
    risk_hits = []
    for a in amendments:
        for kw in _RETIREMENT_RISK_KEYWORDS:
            if kw in (a.get("after") or ""):
                risk_hits.append({"clause": a["clause"], "kw": kw})

    if any("황금낙하산" in h["kw"] or "경영권" in h["kw"] for h in risk_hits):
        return "AGAINST", f"황금낙하산 또는 경영권 변동 special 가산 신설 — NPS IV-35 원칙적 반대"

    if risk_hits:
        return "REVIEW", f"퇴직금 규정 변경 {len(amendments)}건 (위험 키워드 hit {len(risk_hits)}건) — 사용자 검토"

    if amendments:
        return "REVIEW", f"퇴직금 규정 변경 {len(amendments)}건 — 변경 내역 검토 권장"

    return "FOR", "퇴직금 규정 단순 정정"
```

### Step 2. 카테고리 매칭 분리

```python
# proxy_advise.py:155 분기
def _categorize(t: str) -> str:
    if "퇴직금" in t or "퇴임위로금" in t:
        return "retirement_pay"  # NEW
    if "보수" in t or "보수한도" in t:
        return "director_compensation"
    ...

# proxy_advise.py:812 dispatch 분기
elif category == "retirement_pay":
    decision, reason = _decide_retirement_pay(retirement_payload)
elif category == "director_compensation":
    decision, reason = _decide_compensation(meeting_comp, fin_metrics, target_hint=...)
```

### Step 3. v2 chain wire (`parse_retirement_pay_xml`)

옵션 A) `shareholder_meeting_notice`에 새 scope `retirement` 추가
옵션 B) services에 신규 `retirement_pay.py` (전용 chain, proxy_advise만 활용)

→ B 권장 (사용자 직접 호출은 드물고, proxy_advise chain 안에서만 필요).

```python
# services/retirement_pay.py
from open_proxy_mcp.tools.parser import parse_retirement_pay_xml

async def build_retirement_pay_payload(company_query: str, year: int):
    # 소집공고 본문 fetch + parse_retirement_pay_xml(html)
    # 안건 title에 "퇴직금" 포함된 안건만 amendments 추출
    return {"data": {"amendments": [...]}, "status": "ok"}
```

proxy_advise에서 1번 호출 (회사당, 퇴직금 안건 detect 시):
```python
retirement_payload = None
if any("퇴직금" in (a.get("title") or "") for a in agenda_items):
    retirement_payload = await _safe_throttled(build_retirement_pay_payload, company_query, year=year)
```

### Step 4. `_decide_compensation` 이사·감사 분기 + 신규 카테고리 `audit_compensation`

```python
# proxy_advise.py:155 분기 강화
def _categorize(t: str) -> str:
    if "퇴직금" in t or "퇴임위로금" in t:
        return "retirement_pay"
    if ("감사" in t and "감사위원" not in t) and ("보수" in t or "보수한도" in t):
        return "audit_compensation"  # NEW (감사 보수한도 분리)
    if "보수" in t or "보수한도" in t:
        return "director_compensation"
    ...

# proxy_advise.py:812 dispatch
elif category == "retirement_pay":
    decision, reason = _decide_retirement_pay(retirement_payload)
elif category == "audit_compensation":
    decision, reason = _decide_audit_compensation(meeting_comp, fin_metrics)
elif category == "director_compensation":
    decision, reason = _decide_director_compensation(meeting_comp, fin_metrics)


def _decide_audit_compensation(comp_payload, fm_payload) -> tuple[str, str]:
    """감사 보수한도 — NPS [별표 1] IV-34 + s_legacy 패턴 (양방향).

    NPS는 "과소" 우려, s_legacy는 "과다" 우려. 둘 다 cover.

    threshold (Step 6 calibrate):
      threshold_low  = KOSPI 200 표본 25 percentile 또는 5천만원/인
      threshold_high = 50 percentile 또는 1억원/인
    """
    # parser items에서 target == "감사" 인 항목만 사용
    # 1인당 평균 = current.total_amount / current.count
    # 인상률 = (current.total_amount - prior.total_amount) / prior.total_amount
    ...
```

### Step 5. 검증 harness

`scripts/audit_compensation_retirement.py`:
- KOSPI 100 + KOSDAQ 50 회사 audit
- 보수한도 안건 (이사/감사) + 퇴직금 안건 추출
- OPM 결정 vs 운용사 majority (4+ 표) 비교 → G3 정합도 측정
- NPS 정책 정합 (G4) — Golden Parachute 키워드 hit / 인상률 과다 case spot check
- distribution 측정: FOR/AGAINST/REVIEW/NO_DATA

### Step 6. 임계값 조정 (필요 시)

distribution 결과로 fine-tune:
- 감사 1인당 평균 threshold (현재 5천만원 가정 — sample 기반 조정)
- 인상률 cutoff
- 위험 키워드 list

### Step 7. 문서화

- `wiki/decisions/260505_1750_decision_compensation-retirement-split.md` — 정책 결정
- `wiki/log.md` 신규 entry
- `wiki/tools/proxy_advise_before_meeting.md` — retirement_payload 필드 + 결정 logic 추가
- (선택) `wiki/lessons/` — 만약 새 인사이트 있으면

---

## 영향 범위

- `open_proxy_mcp/services/proxy_advise.py` (decision 분기 + 카테고리)
- `open_proxy_mcp/services/retirement_pay.py` (NEW — 별도 chain service)
- `open_proxy_mcp/tools/parser.py` (기존 `parse_retirement_pay_xml` 재사용, 수정 X)
- `wiki/decisions/260505_1750_decision_compensation-retirement-split.md` (NEW)
- `wiki/architecture/audits/data/260505_compensation_retirement/` (검증 csv)

## 비목표 (이번 ralph X)

- v1 tool (`agm_retirement_pay_xml`) 재활성화 (production은 v2)
- 임원별 개별 보수 본문 분석 (5억원+ 임원)
- 스톡옵션 부여 안건 (별도 카테고리, NPS V-36)

## 가설 / 위험

- **위험 1**: parse_retirement_pay_xml이 실제 회사 본문에서 amendments 추출 못 함 → Step 0 spot 결과로 검증.
- **위험 2**: 감사 1인당 평균 threshold 임의로 정하면 G3/G4 fail. → distribution 측정 후 calibrate (lessons/distribution-calibrated-thresholds 패턴).
- **위험 3**: 황금낙하산 keyword 단순 매칭이 false positive 생산 (예: "퇴직금 일반 규정"이 황금낙하산이 아닌 경우). → Step 0 sample 검증 + 위험 키워드 list 좁힘.

## archive 폴더

`wiki/architecture/audits/data/260505_compensation_retirement/`
