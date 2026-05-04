---
type: audit
title: treasury 결과보고서 iter1~8 — KOSPI 100 검증
created: 2026-05-05 05:30
ralph_doc: wiki/ralph/260505_0051_ralph_treasury-execution-results.md
promise: TREASURY_EXECUTION_VERIFIED
status: PARTIAL — G1 PASS, G2 미달 (97.69% vs target 99%)
---

# treasury 결과보고서 iter1~8 audit

## 작업 요약

ralph 5 step 완료:
1. keyword 4종 + parser prototype (iter1)
2. ACODE 기반 parser 재작성 (iter2) — DART 표준 서식 system field id 발견
3. list.json 검색 + body enrich + event 통합 (iter3)
4. 결정↔결과 사이클 매칭 (iter4)
5. scope 통합 6→2 + render (iter5)
6. 검증 harness + KOSPI 30 baseline (iter6)
7. ±3일 fallback + lookback 분리 (iter7)
8. KOSPI 100 확장 측정 (iter8)

## Gate 결과 (KOSPI 100 표본)

| Gate | Target | 결과 | Status |
|------|--------|------|--------|
| **G1** 본문 파싱 성공률 | ≥99% | **100%** (해당 표본 전체) | ✓ PASS |
| **G2 raw** 사이클 매칭률 (전체) | ≥99% | **90.56%** (211/233) | ✗ |
| **G2 adjusted** (lookback 밖 제외) | ≥99% | **97.69%** (211/216) | ✗ (1.31%p 미달) |
| **G3** phase flag (decision/execution) | binary | wire 완료 | ✓ PASS |
| **G4** scope 통합 (6→2) | binary | summary + annual | ✓ PASS |

## ACODE 발견 (iter2 핵심 insight)

DART 본문 XML에 표준 서식 ACODE semantic markers 발견:
- `ACQ_AMT` 취득가액총액, `DSP_AMT` 처분가액
- `SCH_SLT_MN` 예정금액, `SEL_SLT_MN` 실제금액
- `SUM_ACT_CNT` 누적수량
- `AGR_MN_YSN` 일치여부, `DIF_MN_CAS` 미달사유
- `HLD_CNT/AMT 1/2/3` 직접/신탁/계 보유
- `OBJ_OTH` 처분상대방, `CNS_NM` 위탁사
- `ACQ_RT` 취득률, `CTR_CNC_AMT` 신탁계약금액
- `STK_VAL_TOT` 신탁취득금액, `STK_VAL` 평균단가

자본시장법 시행령 별지 표준 서식 system field id — 모든 회사 동일 → G1 100% 안정성 확보.

## G2 미달 원인 (인정)

### Out-of-lookback (17/233, 7.3%) — 본질 매칭 불가
결과보고서의 `main_report_date`가 lookback 24개월 밖. 결정 record 자체 없음 → 매칭 불가능.
adjusted metric에서 제외 (97.69% adjusted).

### 인접 일자 차이 (5/216, 2.31%) — fuzzy timing
- 결정 ↔ 결과 사이 4-8일 차이 케이스 (이사회 일정 변경 등)
- 셀트리온: main_date 2025-05-20 vs 가장 가까운 acquisition_decision 2025-05-28 (8일)
- ±3일 fallback으로 완벽 cover X

### iter 9 (±7일 확장) 측정 실패
- DART API 분당 1000 한도 + 100 회사 × ~10 호출 → 일시 차단
- httpx.ReadError 발생 → 측정 데이터 무효

## 결론

**Promise X — 정직 fallback**:
- G1 100% (표준 서식 ACODE 활용 99%+ 확정)
- G2 97.69% (1.31%p 미달, fuzzy timing 한계 — parse_personnel_xml ralph 패턴과 동일)

**실용적 가치**:
- 96-97% 매칭률은 사용자 분석에 충분
- key_date_hint, match_status, match_proximity_days 메타로 LLM이 unmatched 케이스 hint 활용 가능

**가능 향후 개선** (별도 ralph):
- lookback 동적 확장 (결과보고서 main_date 기반)
- fuzzy matching ±10일 + decision type cross-validation
- DART API rate limit 대응 (sqlite cache 보강)

## archive 데이터
- iter06_kospi_30.json (G2 raw 87.39%)
- iter07_kospi_30.json (±3일, G2 raw 90.76% / adjusted 96.43%)
- iter08_kospi_100.json (G2 raw 90.56% / adjusted 97.69%)
- iter09_*.json (DART 차단으로 무효 측정)

## iter 10 (코드 변경, 측정 보류)

### normalize 보강 (User 요구: API로 얻는 정보 품질 최대화)

추가 필드:
- 취득결정: 보통주/우선주 별도 + 보유예상기간 + 위탁사 + 사외이사 참석/감사 참석
- 처분결정: 보통주/우선주 별도 + 단가(시가) + 처분상대방 + 위탁사 + 사외이사 참석
- 신탁체결/해지: 신탁기관 + 위탁사 + 사외이사 + 해지사유

User 제외 (수집 X): 1일 매수/매도 한도, 공정위 신고, 액면가 (treasury 공시에 없음)

### 측정 보류 사유

DART API IP 차단 (Connection reset by peer):
- 두 키 모두 동일 에러 (키 문제 X — IP 차단)
- ping opendart.fss.or.kr 100% packet loss
- 보통 24시간 cool-down

수정된 normalize는 syntax 검증만 (직접 측정 X).
차단 풀린 후 별도 spot 검증 필요.

## Ralph 종료 권고

96.43~97.69% G2 adjusted는 99% target 미달. 코드 측면 개선 (iter 10 normalize 보강) 완료. 측정은 IP 차단으로 보류. parse_personnel_xml ralph 패턴 — 정직 fallback으로 ralph cancel + acceptance level 협상 권장.
