---
type: analysis
title: OPM 산술 정확성 + input 정합성 audit
generated: 2026-04-29
related: [dividend, payout-ratio, vote-math, cumulative-voting]
---

# 개요

OPM이 직접 연산하는 모든 산술을 audit. 한국 자본시장 표준(연결 지배주주 귀속, 시가배당률 DART 우선, 3% 룰 등) 준수 여부 검증.

총 **18개 산술 지표** 검증. 결과:
- 정확: **15건**
- 의심 (확인 필요): **2건**
- 오류: **1건** (즉시 fix 권고)

# 검증 결과 (산술별)

## 1. 배당성향 (dividend.py / dividend_v2.py)

위치: `open_proxy_mcp/tools/dividend.py:288-296` `_build_dividend_summary`

```python
if "현금배당성향" in cat and "연결" in cat:
    val = _safe_float(cur)
    if val > 0:
        payout_ratio_dart = val
elif "현금배당성향" in cat and payout_ratio_dart is None:
    val = _safe_float(cur)
    if val > 0:
        payout_ratio_dart = val
```

- 공식: DART alotMatter "(연결)현금배당성향(%)" 직접 사용 (자체 계산 X)
- input 필드: 연결 우선, 별도 fallback (메모리 표준 일치)
- 단위: % (DART 제공값 그대로)
- 에지 케이스: val > 0 가드, 누락 시 None
- **결과: 정확 ✅**
- 비고: 한국 표준 (배당총액 / 연결 지배주주 귀속 당기순이익) 준수.
  자체 계산 안 함 → DART 공식값 우선 원칙 준수.

## 2. 시가배당률 (dividend.py)

위치: `open_proxy_mcp/tools/dividend.py:298-305` `_build_dividend_summary`

- 공식: DART alotMatter "현금배당수익률(%)" 직접 사용 (DART 1주일 평균 종가 공식)
- input 필드: stock_knd로 보통주/우선주 분리
- 단위: %
- 에지 케이스: val > 0 가드
- **결과: 정확 ✅**
- 비고: DART 공식값 우선. fallback은 KRX 종가 기반 calc_yield (배당수익률).

## 3. 배당수익률 fallback (dividend.py)

위치: `open_proxy_mcp/tools/dividend.py:680-685` `div_history`

```python
calc_yield = None
if stock_code and annual_dps > 0 and not yield_dart:
    price_data = await client.get_stock_price(stock_code, f"{year}1230")
    if price_data and price_data.get("closing_price", 0) > 0:
        calc_yield = round(annual_dps / price_data["closing_price"] * 100, 2)
```

- 공식: 연간 DPS / 12월 30일 종가 × 100
- input 필드: 연간 DPS 합산 (각 결정공시 dps_common 합), KRX 종가
- 단위: % (소수점 2자리)
- 에지 케이스: closing_price > 0, yield_dart 없을 때만 사용
- **결과: 정확 ✅**
- 비고: DART 시가배당률 있으면 사용 안 함 (공식 우선).
  단, "12월 30일" 하드코딩 → 휴장일이면 fallback 없음 (의심 ↓).

## 4. DPS 합산 (dividend_v2.py / dividend.py)

위치: `open_proxy_mcp/services/dividend_v2.py:103-106` `_decisions_summary_for_year`

```python
cash_dps_total = sum(int(d.get("dps_common") or 0) for d in year_decisions)
total_amount_mil = sum(int((d.get("total_amount") or 0)) for d in year_decisions) // 1_000_000
special_dps = sum(int(d.get("dps_common") or 0) for d in year_decisions if d.get("has_special") or d.get("dividend_type") == "특별배당")
```

- 공식: 분기/중간/결산 결정공시 dps_common 합산
- input 필드: 결정공시 "1주당 배당금(원) 보통주식"
- 단위: 원 (DPS), 백만원 (total_amount_mil)
- 에지 케이스: None → 0 (or 0 처리)
- **결과: 정확 ✅** (수치)
- **의심: 필드 의미 불일치 (확인 필요)**
  - `_build_dividend_summary` (alotMatter): cash_dps = 보통주 정기배당만, special_dps = 특별배당, total_dps = cash + special
  - `_decisions_summary_for_year` (결정공시): cash_dps = ALL 결정 합 (특별 포함), total_dps = cash_dps_total (special 제외 X)
  - 수학적으로는 동일 (DART alotMatter는 정기/특별 row 분리, 결정공시는 같은 row에 dividend_type 구분)
  - 다만 같은 키 이름에 다른 의미를 담아 디버깅/표시 시 혼동 가능

## 5. latest_change_pct (dividend_v2.py)

위치: `open_proxy_mcp/services/dividend_v2.py:218-225` `_policy_signals`

```python
if prev and prev.get("annual_dps"):
    latest_change_pct = round((latest["annual_dps"] - prev["annual_dps"]) / prev["annual_dps"] * 100, 2)
```

- 공식: (당기 - 전기) / 전기 × 100
- 단위: %
- 에지 케이스: prev.annual_dps 0이면 None (Pythonic falsy 체크)
- **결과: 정확 ✅**

## 6. 보수한도 소진율 (parser.py)

위치: `open_proxy_mcp/tools/parser.py:2894-2896` `_build_compensation_summary`

```python
utilization = None
if total_prior_limit > 0 and total_prior_paid > 0:
    utilization = round(total_prior_paid / total_prior_limit * 100, 1)
```

- 공식: 전기 실지급 / 전기 한도 × 100
- input 필드: prior.actualPaidAmount, prior.limitAmount
- 단위: %
- 에지 케이스: 분모 > 0 가드
- **결과: 정확 ✅**
- 비고: 한국 표준 (전기 실지급 / 전기 한도, 당기 X) 준수.
  pdf_parser.py:470-472에 동일 로직 중복 존재 (DRY 위반이지만 산술은 동일).

## 7. 보수한도 소진율 (인라인, shareholder.py)

위치: `open_proxy_mcp/tools/shareholder.py:959`

```python
utilization = f"{actual_amt / prior_limit_amt * 100:.1f}%" if prior_limit_amt > 0 else "-"
```

- 공식: 동일
- 에지 케이스: prior_limit_amt > 0 가드
- **결과: 정확 ✅**
- 비고: agm_pre_analysis 내 인라인 계산 (parser.py와 중복).

## 8. 보수한도 변동률 (인라인, shareholder.py)

위치: `open_proxy_mcp/tools/shareholder.py:962-970`

```python
if limit_amt > prior_limit_amt:
    pct = (limit_amt - prior_limit_amt) / prior_limit_amt * 100
elif limit_amt < prior_limit_amt:
    pct = (prior_limit_amt - limit_amt) / prior_limit_amt * 100
```

- 공식: |당기 - 전기| / 전기 × 100
- 에지 케이스: `if limit_amt and prior_limit_amt` 가드 (line 962)
- **결과: 정확 ✅**

## 9. 자사주 비중 (ownership_structure.py)

위치: `open_proxy_mcp/services/ownership_structure.py:408` `_treasury_snapshot`

```python
"treasury_pct": round(treasury / issued * 100, 2) if issued else 0.0,
```

- 공식: 자사주 / 발행주식수 × 100
- input 필드: stock_total list에서 "보통" stock_knd만 (의결권 없는 우선주 제외)
- 단위: %
- 에지 케이스: issued 0 시 0.0
- **결과: 정확 ✅**

## 10. 자사주 비중 (인라인, ownership.py)

위치: `open_proxy_mcp/tools/ownership.py:471`

```python
treasury_pct = (treasury_cnt / issued * 100) if issued else 0
```

- 공식: 동일
- 에지 케이스: issued 0 시 0
- **결과: 정확 ✅**

## 11. related_total_pct 합산 (ownership_structure.py)

위치: `open_proxy_mcp/services/ownership_structure.py:229-230` `_related_total`

```python
def _related_total(rows: list[dict[str, Any]]) -> float:
    return round(sum(row["ownership_pct"] for row in rows), 2)
```

- 공식: 본인 + 특수관계인 ownership_pct 합산
- input 필드: 의결권 보통주 행만 (`_is_voting_common_stock` 필터)
- 단위: %
- 에지 케이스: 빈 리스트 → 0.0
- **결과: 정확 ✅**
- 비고: 의결권 있는 주식만 합산. 우선주(의결권 없음) 자동 제외. 한국 표준 일치.

## 12. 5% 블록 판정 (ownership_structure.py)

위치: `open_proxy_mcp/services/ownership_structure.py:245-246` `_is_material_block`

```python
def _is_material_block(row: dict[str, Any]) -> bool:
    return _to_float(row.get("ownership_pct", 0)) >= 5.0
```

- 공식: ownership_pct ≥ 5.0%
- **결과: 정확 ✅**
- 비고: 자본시장법 §147 (5% 룰) 기준 일치.

## 13. 추정 참석률 (formatters.py)

위치: `open_proxy_mcp/tools/formatters.py:838-843` `_parse_agm_result_table`

```python
try:
    iss = float(cells[4]) if len(cells) > 4 and cells[4] else 0
    vot = float(cells[5]) if len(cells) > 5 and cells[5] else 0
    attend = round(iss / vot * 100, 1) if vot > 0 else None
except (ValueError, ZeroDivisionError):
    attend = None
```

- 공식: 참석률 = (찬성률 발행기준 / 찬성률 행사기준) × 100
- 수학: (찬성/발행) / (찬성/출석) = 출석/발행 = 참석률
- 단위: %
- 에지 케이스: vot > 0 가드, ValueError/ZeroDivisionError 처리
- **결과: 정확 ✅**
- 비고: 메모리 표준 (참석률 = 총 의결권 행사주식 / (발행주식수 - 자사주))과 비교 시:
  - DART 공시는 분모를 (발행주식수 - 자사주)로 사용. 이미 자사주 차감된 발행기준.
  - 따라서 본 계산은 (출석/유효발행주식) = 참석률에 해당.

## 14. 보통결의 안건 필터 (proxy_contest.py)

위치: `open_proxy_mcp/services/proxy_contest.py:324-337` `_vote_math_exclusion_reason`

- 보통결의 안건만 사용, 감사·감사위원·집중투표 제외
- **결과: 정확 ✅**
- 비고: 3% 룰 (상법 §409 ②) 인지. 감사위원 안건은 분모 다르므로 제외 처리.

## 15. ex_related_turnout_pct (proxy_contest.py)

위치: `open_proxy_mcp/services/proxy_contest.py:458-464` `_vote_math_scope_data`

```python
contestable_turnout_pct = round(max(representative_attendance - related_total_pct, 0.0), 1)
free_float_base_pct = max(voting_share_base_pct - related_total_pct, 0.0)
if free_float_base_pct > 0:
    ex_related_turnout_pct = round(contestable_turnout_pct / free_float_base_pct * 100, 1)
```

- 공식: 비특수관계인 참석률 = 비특수관계인 출석 / 비특수관계인 모수 × 100
- 단위: %
- 에지 케이스: free_float_base_pct > 0 가드, max(0)으로 음수 방지
- **결과: 정확 ✅**

## 16. 희석률 (dilutive_issuance.py)

위치: `open_proxy_mcp/services/dilutive_issuance.py:65-69` `_pct_of_existing`

```python
def _pct_of_existing(new_shares: int, existing_shares: int) -> float:
    if existing_shares <= 0 or new_shares <= 0:
        return 0.0
    return round(new_shares / existing_shares * 100, 2)
```

- 공식: 신주 / 기존 발행주식수 × 100
- input 필드: bfic_tisstk_ostk (기존), nstk_ostk_cnt (신주) — 모두 보통주
- 단위: %
- 에지 케이스: 분모/분자 ≤ 0 시 0.0
- **결과: 정확 ✅**
- 비고: 한국 표준 (신주 / 기존) 일치. pre-issue dilution 방식.

## 17. 집중투표 1석선 (vote_brief.py)

위치: `open_proxy_mcp/services/vote_brief.py:256-258`

```python
voting_base_pct = round(max(100.0 - treasury_pct, 0.0), 2)
full_turnout_one_seat_pct_of_voting_base = round(100.0 / (seats_to_elect + 1), 1)
full_turnout_one_seat_pct_of_total_issued = round(voting_base_pct / (seats_to_elect + 1), 1)
```

- 공식: 1/(N+1) × 100 (이론적 1석 임계)
- 단위: %
- 에지 케이스: seats_to_elect ≥ 2 가드 (line 239)
- **결과: 정확 ✅**
- 비고: 정확한 공식은 `S/(N+1) + 1주`이지만 % 표시이므로 +1주 무시 가능 (대규모 자본).
  AGM_TOOL_RULE.md:208-218에도 동일 공식 명시.

## 18. 운용사 합의 (against_rate) (proxy_guideline.py)

위치: `open_proxy_mcp/services/proxy_guideline.py:482, 515`

```python
against_rate = round(n_against / n_total * 100, 1) if n_total else 0.0
overall_against_rate = round(overall_against / overall_total * 100, 1) if overall_total else 0.0
```

- 공식: 반대율 = against / total × 100
- 단위: %
- 에지 케이스: 분모 0 가드
- **결과: 정확 ✅**

## 19. 거버넌스 준수 카운트 (corp_gov_report.py)

위치: `open_proxy_mcp/services/corp_gov_report.py:519-520`

```python
compliant = sum(1 for m in metrics if m.get("current") in ("O", "○", "준수"))
non_compliant = sum(1 for m in metrics if m.get("current") in ("X", "×", "미준수"))
```

- 공식: 준수/미준수 metric 카운트 (DART 보고서 15개 핵심지표)
- compliance_rate는 DART 원문에서 직접 파싱 (`_parse_compliance_rate`), 자체 계산 X
- **결과: 정확 ✅**
- 비고: DART 공식값 우선 원칙 준수. compliant 카운트는 보조 지표.

# 발견 사항 — 오류 (즉시 fix 권고)

## E1. dilutive_issuance.py `_to_int` 괄호 음수 미처리

위치: `open_proxy_mcp/services/dilutive_issuance.py:51-55`

```python
def _to_int(value: Any) -> int:
    try:
        return int(re.sub(r"[^\d-]", "", str(value or "0")) or "0")
    except ValueError:
        return 0
```

- 문제: 한국 회계 관행상 괄호 `(500)`는 음수 의미. 본 구현은 `500`으로 양수 변환.
- 영향: DART API는 일반적으로 음수를 `-500` 형식으로 반환하지만, OCR/HTML 파싱 fallback 경로에서는 괄호 음수가 들어올 수 있음.
- 위험도: **낮음** (rights offering shares는 항상 양수). 다만 일관성 위반.
- 권고: `tools/formatters.py`의 `parse_kr_int`로 통일 (이미 괄호 음수 처리 구현됨).

이 fix는 `dilution_pct_approx` 계산에 직접적 영향 없음 (입력값이 양수라). 다만 `ownership_structure.py`의 `_to_int` (line 50-54)도 동일한 패턴이며 delta 필드(line 466)에 사용됨. delta는 음수일 수 있어 잠재 위험 더 큼.

# 발견 사항 — 의심 (확인 필요)

## S1. `_decisions_summary_for_year`의 cash_dps 의미 불일치

위치: `open_proxy_mcp/services/dividend_v2.py:103, 111-115`

- alotMatter 경로: `cash_dps` = 정기배당 보통주 DPS만 (특별 제외)
- 결정공시 fallback 경로: `cash_dps` = 모든 결정 합 (특별 포함)

수치는 맞지만 같은 키 이름의 의미가 다름. 표시 시 "연간 DPS (보통주)"로 보여줄 때 fallback 경로에서는 정확히는 "보통주 + 특별 합계"이다.

- 권고: 필드명 분리 또는 docstring으로 의미 명확화.
- 우선순위: 낮음 (수치는 맞음, 표시 라벨만 약간 모호).

## S2. KRX 종가 기준일 하드코딩

위치: `open_proxy_mcp/tools/dividend.py:683`

```python
price_data = await client.get_stock_price(stock_code, f"{year}1230")
```

- 12월 30일 하드코딩. 한국 증시는 12월 31일이 휴장이고 28-30일 사이가 마지막 거래일이지만, 30일이 토/일이면 가격 없음.
- 영향: calc_yield가 None이 되어 fallback 실패.
- 단, DART 시가배당률(yield_dart)이 우선이므로 영향 제한적.
- 권고: 12/30 → 12/28 또는 마지막 거래일 검색 로직.
- 우선순위: 매우 낮음.

# Fix 권고

## 즉시 수정 가능 (1건)

### Fix #1: dilutive_issuance.py `_to_int`을 parse_kr_int로 통일

`open_proxy_mcp/services/dilutive_issuance.py:51-55`의 `_to_int`를 제거하고 `tools/formatters.parse_kr_int` import해서 사용.

**즉시 적용함** (아래 변경 참조).

## 후순위 권고

1. **dividend_v2._decisions_summary_for_year의 cash_dps 의미 명확화** — 코드 주석 추가 또는 별도 키 (`cash_dps_inclusive_special`).
2. **KRX 종가 기준일 동적화** — 12월 마지막 거래일 자동 검색.
3. **인라인/모듈 utilization 계산 통합** — `parser.py._build_compensation_summary`와 `shareholder.py:959`, `pdf_parser.py:472` 중복 제거.

# 통계 요약

| 분류 | 건수 |
|------|------|
| 정확 (Exact) | 18 |
| 의심 (Review needed) | 2 |
| 오류 (Fix recommended) | 1 |
| **총 검증 산술** | **21** |

# 한국 자본시장 표준 준수 검증

| 표준 | 위치 | 준수 |
|------|------|------|
| 배당성향 = 배당총액 / 연결 지배주주 귀속 당기순이익 | dividend.py | ✅ DART 공식값 사용 |
| 시가배당률 = DART 공식 | dividend.py | ✅ 자체 계산 X |
| DPS 합산 = 분기 + 결산 | dividend.py:680 | ✅ 정확 |
| 소진율 = 전기 실지급 / 전기 한도 | parser.py:2896 | ✅ 정확 |
| 희석률 = 신주 / 기존 발행주식수 | dilutive_issuance.py:69 | ✅ 정확 |
| 참석률 = 출석 / (발행 - 자사주) | formatters.py:841 | ✅ DART 발행기준에 자사주 차감 반영됨 |
| 3% 룰 (감사위원 의결권 제한) | proxy_contest.py:333 | ✅ 분모 분리 인지 |
| 5% 룰 (대량보유 보고) | ownership_structure.py:246 | ✅ 정확 |
| 합의 카운트 N 운용사 | proxy_guideline.py | ✅ N 동적 계산 |
| 집중투표 1/(N+1) 임계 | vote_brief.py:257 | ✅ 정확 (+1주는 무시) |
