---
type: failure_archive
iteration: 2
gate: G2
fail_type: accuracy_vs_majority (vote_style 변경 효과 없음)
sample_size: 10 회사 × m_legacy
date: 2026-05-04
---

# Iter 2 — 옵션 B 시도 (vote_style 변경) — 효과 없음, 32.4% 동일

## 가설
vote_style="open_proxy"가 conservative라 mainstream majority와 차이. mainstream vote_style (m_legacy = [운용사A])로 호출 시 majority와 align 가능.

## 결과
- 동일 10 회사, vote_style="m_legacy"
- **G2 정확도: 12/37 = 32.4%** (open_proxy와 완전 동일)
- alignment dist: {follows_consensus: 12, unique: 24, aligns_with_outlier: 1, no_data: 2}
- our_decision dist: {FOR: 14, REVIEW: 25}

## 가설 기각 — 이유

`services/proxy_advise.py:_apply_policy_default`:
```python
def _apply_policy_default(default_str, fallback_decision, fallback_reason):
    if not default_str or default_str == "case_by_case":
        return fallback_decision, fallback_reason  # OPM logic 그대로
```

운용사 정책 JSON (m_legacy/s_legacy 등)이 대부분 카테고리에서 `default="case_by_case"` 설정 → OPM REVIEW 그대로 fallback.

vote_style param은 정책 wire 메타데이터일 뿐, 실제 결정은 OPM logic이 100%.

## Root cause 재확인

진짜 문제 위치:
- `_decide_director_election`: matched_eval None → REVIEW
- `_decide_compensation`: increase_rate None → REVIEW
- `_decide_articles_amendment`: 특정 패턴 외 → REVIEW
- 카테고리 미분류 → REVIEW

**OPM logic 자체가 데이터 부족 시 REVIEW 보수**. 운용사 mainstream은 default FOR.

## 남은 옵션

| 옵션 | 내용 | 99% 가능성 | 위험 |
|---|---|---|---|
| ~~B~~ | ~~vote_style 변경~~ | ❌ 효과 없음 (확정) | - |
| **A** | OPM logic 손보기 (default FOR) | 높음 | OPM 정체성 변경 |
| **C** | 데이터 누락 fix (parser 보강) | 중간 (시간 多) | 안전 |
| **D** | G2 target 재정의 | 100% | ralph md 수정 (목표 회피) |

## 사용자 결정 필요

ralph rule (막힘 → 사용자 요청). promise 출력 X.

다음 step (선택지):
- A: `_decide_*` 함수에 "데이터 부족 + 결격사유 미발견 → FOR" 분기 추가 (OPM 정책 변경)
- C: director_evaluation parser 강화 + agenda parser 강화 → 데이터 수집률 ↑
- D: G2 target → "vote_style 별 정책 wire 일관성" 으로 재정의 (open_proxy vs majority 자연스러움 인정)

## 시도한 fallback
- B (vote_style m_legacy) — 32.4% 동일 (확정 효과 없음)
