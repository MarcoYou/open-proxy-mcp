"""실시간 스케일 오류 가드 — DART acntAll 단위 오류(소프트센 032680 FY2022, 100만배) 탐지.

설계 스펙·검증 근거: private wiki.
과거 연도는 다음해 보고서 재작성치로 사후정정 가능(market_val_series.backfill_restated)하나,
가장 최신 연도는 그 방법이 안 통함 — 여기 4개 체크는 "같은 API 응답 안에서" 지연 없이 판정한다.

account_id 매칭은 반드시 정확일치(==) — substring(in) 금지. 260704 스모크 테스트에서
"ifrs-full_Liabilities" in "ifrs-full_LiabilitiesIncludedInDisposalGroupsClassifiedAsHeldForSale"
가 True가 되는 접두어 충돌로 정상 종목을 오탐시킨 실측 사고 재현 → exact match로 교정 후 20/20 정상.
"""
from __future__ import annotations

import math

_DIGIT_CAP = 16  # KRW 절대값 16자리(1000조) 초과는 물리적으로 불가능 — 최후 백스톱(아래 한계 참고)
_POWER_TOLERANCE = 0.20  # 배수점프가 10^n에 ±20% 이내로 근접하면 단위오류로 판정
_MKTCAP_RATIO_CAP = 50.0  # |값| / 시가총액 > 50배 → 의심(중신뢰, 20개사 실측 최대 1.56배 확인)
_MARKET_MAX_FACTOR = 3.0  # |값| / 시장내 실측 최댓값 > 3배 → 물리적으로 불가능(260704 시나리오 검증)

# 시장 내 실측 순이익 최댓값(삼성전자 FY2025 확인치, 260704) — market_relative_cap의 검증된 앵커.
# DB의 살아있는 MAX() 값으로 동적 확장 금지: 이미 소프트센류 오염값이 섞여있으면 그 오염값 자체가
# 앵커가 되어 가드가 통째로 무력화되는 자기오염 위험 실측 확인(mkt_finstat_y에서 재현, 260706 rename). 이 상수는
# 다음 회계연도에 더 큰 회사가 나오면 수동 갱신(검증 후) — 세 호출부(valuation.py·market_val_agg.py·
# market_val_series.py)가 모두 여기서 import해 단일 지점 갱신.
MARKET_MAX_NI_ANCHOR = 44_260_956_000_000

# ③ 자릿수 상한의 한계(260704 시나리오 분석): 고정 절대값이라 회사 규모에 안 맞음 —
# 소형주는 100~1만배 오류를 다 놓치고(값 자체가 작아 16자리 밑), 대형주는 100배 오류를
# 놓친다(비율체크도 분모가 커서 둔감). 시장 내 실측 최댓값(현재는 삼성전자) 대비 배수로
# 판정하면 소형·대형 안 가리고 10배부터 잡힘 — check_market_relative_cap이 우선 체크,
# digit_cap은 market_max 미제공 시(예: 배치 초기·단독 스크립트) 최후 백스톱으로 유지.


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
    """③(백스톱) 자릿수 상한 — market_max 없을 때만 쓰는 최후 방어선(회사규모 무관 즉시 판정
    가능하나, 소형주 중간배율 오류·대형주 100배 오류를 놓치는 한계 있음 — check_market_relative_cap 우선)."""
    if value is None:
        return {"triggered": False}
    digits = len(str(abs(int(value))))
    return {"triggered": digits > cap_digits, "digits": digits}


def check_market_relative_cap(value, market_max, factor: float = _MARKET_MAX_FACTOR) -> dict:
    """③(개정) 시장 내 실측 최댓값 대비 배수 — 고정 자릿수보다 원칙적, 회사규모 안 가리고 작동.
    market_max: 같은 시장·같은 지표(순이익 등)의 현재 알려진 최댓값(예: 삼성전자). 없으면 무력."""
    if value is None or not market_max:
        return {"triggered": False}
    ratio = abs(value) / market_max
    return {"triggered": ratio > factor, "ratio": ratio}


def check_mktcap_ratio(value, mktcap, cap_ratio: float = _MKTCAP_RATIO_CAP) -> dict:
    """④ 시총 대비 비율(보조) — 상장전/거래정지 등 시총 부재 시 무력(soft 신호로만 사용)."""
    if value is None or not mktcap:
        return {"triggered": False}
    ratio = abs(value) / mktcap
    return {"triggered": ratio > cap_ratio, "ratio": ratio}


def assess(*, thstrm=None, frmtrm=None, assets=None, liabilities=None, equity=None,
           mktcap=None, market_max=None) -> dict:
    """4개 체크 종합. tier: 'hard'(②③ 중 하나라도 → N/M 무효화) / 'soft'(①④만 → 경고만) / 'clean'.
    market_max 제공 시 ③은 시장최댓값 대비 배수(원칙적, 회사규모 무관 작동) — 없으면 자릿수 백스톱.

    ①(배수점프)은 260704 전수 재검증(dart_finstat_y 12,995행)에서 오탐률 39/40(97.5%)로 확인돼
    hard에서 제외 — 중소형사의 정상적 실적 급변동(적자↔흑자 전환 등)이 "10^n 근접"이라는 통계적
    기준에 흔하게 걸림(대형 우량주 위주 표본검증에선 안 드러난 사각지대). 정보성 soft 신호로만 유지.
    """
    hard_scale_check = (check_market_relative_cap(thstrm, market_max) if market_max
                         else check_digit_cap(thstrm))
    hard = {
        "balance_identity": check_balance_identity(assets, liabilities, equity),
        "market_relative_cap" if market_max else "digit_cap": hard_scale_check,
    }
    soft = {
        "magnitude_jump": check_magnitude_jump(thstrm, frmtrm),
        "mktcap_ratio": check_mktcap_ratio(thstrm, mktcap),
    }
    hard_hit = [k for k, v in hard.items() if v.get("triggered")]
    soft_hit = [k for k, v in soft.items() if v.get("triggered")]
    tier = "hard" if hard_hit else ("soft" if soft_hit else "clean")
    return {"tier": tier, "hard_hit": hard_hit, "soft_hit": soft_hit,
            "diagnostics": {**hard, **soft}}
