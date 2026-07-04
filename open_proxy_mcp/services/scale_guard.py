"""실시간 스케일 오류 가드 — DART acntAll 단위 오류(소프트센 032680 FY2022, 100만배) 탐지.

스펙: wiki/decisions/valuation-methodology.md §9 (3인 전문가 검토 + 20개사 스모크 테스트 완료).
과거 연도는 다음해 보고서 재작성치로 사후정정 가능(market_val_series.backfill_restated)하나,
가장 최신 연도는 그 방법이 안 통함 — 여기 4개 체크는 "같은 API 응답 안에서" 지연 없이 판정한다.

account_id 매칭은 반드시 정확일치(==) — substring(in) 금지. 260704 스모크 테스트에서
"ifrs-full_Liabilities" in "ifrs-full_LiabilitiesIncludedInDisposalGroupsClassifiedAsHeldForSale"
가 True가 되는 접두어 충돌로 정상 종목을 오탐시킨 실측 사고 재현 → exact match로 교정 후 20/20 정상.
"""
from __future__ import annotations

import math

_DIGIT_CAP = 16  # KRW 절대값 16자리(1000조) 초과는 물리적으로 불가능(삼성전자 전체자산=15자리, 국내 최대)
_POWER_TOLERANCE = 0.20  # 배수점프가 10^n에 ±20% 이내로 근접하면 단위오류로 판정
_MKTCAP_RATIO_CAP = 50.0  # |값| / 시가총액 > 50배 → 의심(중신뢰, 20개사 실측 최대 1.56배 확인)


def gid_exact(rows: list, account_id: str, sj: tuple[str, ...], field: str = "thstrm_amount"):
    """account_id 정확일치 매칭. rows: DART fnlttSinglAcntAll의 list. sj: sj_div 허용집합."""
    for r in rows:
        if r.get("sj_div") in sj and r.get("account_id") == account_id and str(r.get(field) or "") != "":
            try:
                return float(str(r.get(field)).replace(",", ""))
            except (TypeError, ValueError):
                return None
    return None


def _near_power_of_ten(ratio: float, tol: float = _POWER_TOLERANCE) -> int | None:
    if ratio <= 0:
        return None
    n = round(math.log10(ratio))
    if n == 0:
        return None
    if abs(ratio / (10 ** n) - 1) <= tol:
        return n
    return None


def check_magnitude_jump(thstrm: float | None, frmtrm: float | None) -> dict:
    """① 당기/전기 배수점프 — 같은 API 응답의 frmtrm과 대조, 신규 호출 불필요."""
    if not thstrm or not frmtrm:
        return {"triggered": False}
    ratio = abs(thstrm) / abs(frmtrm)
    n = _near_power_of_ten(ratio)
    if n is not None and abs(n) >= 2:  # 100배 미만은 정상 사업 변동 범위로 간주
        return {"triggered": True, "power": n, "ratio": ratio}
    return {"triggered": False, "ratio": ratio}


def check_balance_identity(assets, liabilities, equity, tol_pct: float = 0.01) -> dict:
    """② 자산총계 = 부채총계 + 자본총계 (260704 실측 20/20 오차 0.0000%, 허용오차 사실상 불필요)."""
    if assets is None or liabilities is None or equity is None or not assets:
        return {"triggered": False}
    diff_pct = abs(assets - (liabilities + equity)) / abs(assets) * 100
    return {"triggered": diff_pct > tol_pct, "diff_pct": diff_pct}


def check_digit_cap(value, cap_digits: int = _DIGIT_CAP) -> dict:
    """③ 자릿수 상한 — 회사규모 무관 즉시 판정."""
    if value is None:
        return {"triggered": False}
    digits = len(str(abs(int(value))))
    return {"triggered": digits > cap_digits, "digits": digits}


def check_mktcap_ratio(value, mktcap, cap_ratio: float = _MKTCAP_RATIO_CAP) -> dict:
    """④ 시총 대비 비율(보조) — 상장전/거래정지 등 시총 부재 시 무력(soft 신호로만 사용)."""
    if value is None or not mktcap:
        return {"triggered": False}
    ratio = abs(value) / mktcap
    return {"triggered": ratio > cap_ratio, "ratio": ratio}


def assess(*, thstrm=None, frmtrm=None, assets=None, liabilities=None, equity=None,
           mktcap=None) -> dict:
    """4개 체크 종합. tier: 'hard'(①②③ 중 하나라도 → N/M 무효화) / 'soft'(④만 → 경고만) / 'clean'."""
    hard = {
        "magnitude_jump": check_magnitude_jump(thstrm, frmtrm),
        "balance_identity": check_balance_identity(assets, liabilities, equity),
        "digit_cap": check_digit_cap(thstrm),
    }
    soft = {
        "mktcap_ratio": check_mktcap_ratio(thstrm, mktcap),
    }
    hard_hit = [k for k, v in hard.items() if v.get("triggered")]
    soft_hit = [k for k, v in soft.items() if v.get("triggered")]
    tier = "hard" if hard_hit else ("soft" if soft_hit else "clean")
    return {"tier": tier, "hard_hit": hard_hit, "soft_hit": soft_hit,
            "diagnostics": {**hard, **soft}}
