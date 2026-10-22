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
# DATA-SC5: 시장 상대 상한(위)은 **혼자서는 hard 가 아니다.** 고정 앵커는 새 최대 실적 보고서(FY2026E 삼성전자 303.64조 = 앵커의 6.9배)에서 먼저 울리므로
# 시총 대비 이 배수를 넘거나(20개사 실측 최대 1.56배의 3배 남짓 — 순이익이 시총의 5배면 PER 0.2 라 정상 값이 아니다) 전기 대비 10ⁿ 점프가 함께 있어야 hard 다.
_MKTCAP_CORROBORATE_RATIO = 5.0

# 시장 내 실측 순이익 최댓값(삼성전자 FY2025 확인치, 260704) — market_relative_cap의 검증된 앵커.
# DB의 살아있는 MAX() 값으로 동적 확장 금지: 이미 소프트센류 오염값이 섞여있으면 그 오염값 자체가
# 앵커가 되어 가드가 통째로 무력화되는 자기오염 위험 실측 확인(mkt_finstat_y에서 재현, 260706 rename). 이 상수는
# 다음 회계연도에 더 큰 회사가 나오면 수동 갱신(검증 후) — 호출부(services/price_multiple_data.py·
# scripts/market_val_series.py)가 모두 여기서 import해 단일 지점 갱신.
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


#: 항등식이 깨진 폭이 이 배수 이상이면 단위(스케일) 오류로 본다 — 자산과 (부채+자본) 중 큰 쪽 ÷ 작은 쪽.
_BALANCE_SCALE_FACTOR = 10.0


def check_balance_identity_scale(assets, liabilities, equity, factor: float = _BALANCE_SCALE_FACTOR,
                                 tol_pct: float = 0.01) -> dict:
    """② 항등식 위반이 **단위 오류 꼴**인가 — 자산총계와 (부채총계 + 자본총계)가 `factor`(10)배 이상 벌어졌을 때만.

    261019(재설계 P1 — 분기 재무 원천 결측): 종전엔 항등식이 0.01% 만 어긋나도 hard(값 무효화)였다. 운영 분기 표에서 이 검사
    **하나만** 켜져 무효화된 행 22개를 DART 원문으로 다시 받아 보니 어긋남이 0.01%~32%(한 행은 자산 칸만 틀려 5.1배 — 모두 10배 미만)였고, 지배 순이익·자본은
    그 회사 제 규모·전기 칸과 맞았다 — 원문의 자산총계·부채총계 칸이 틀렸을 뿐이다(예: 삼성생명 2025 반기·3분기 응답의 자산총계·
    부채총계가 1분기 값 그대로 — 같은 응답의 「자본과부채총계」와도 다르다). 반대로 같은 표의 진짜 단위 오류 8행(÷10³·÷10⁶)은 전부
    항등식이 0.0000% 로 맞았고(보고서가 통째로 부풀려진다) 다른 검사(회사 자기 규모·전기 칸 대조·자릿수)가 잡았다.
    단위 오류는 원·천원·백만원 경계라 1,000배 단위다 — 한 칸만 틀려도 그 칸이 자산의 1% 이상이면 10배 넘게 벌어진다.
    그래서 10배 미만 어긋남은 「원문 칸 불일치」(soft — 경고만)로, 10배 이상이거나 (부채+자본)이 0 이하인데 자산이 양수면 hard 로 둔다."""
    base = check_balance_identity(assets, liabilities, equity, tol_pct)
    if not base.get("triggered"):
        return {**base, "triggered": False, "mismatch": False}
    rhs = liabilities + equity
    if rhs <= 0 or assets <= 0:
        ratio = None
        scale_like = (assets > 0) != (rhs > 0)
    else:
        ratio = max(assets, rhs) / min(assets, rhs)
        scale_like = ratio >= factor
    return {"triggered": scale_like, "mismatch": True, "diff_pct": base["diff_pct"], "ratio": ratio}


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


_SELF_FACTOR = 20.0  # 그 회사 제 규모의 20배 — 자본이 한 분기에 20배 뛰는 회사는 없다


def check_self_relative(value, self_ref, factor: float = _SELF_FACTOR) -> dict:
    """③(260829 신설) **그 회사 자신**의 규모 대비 배수.

    기존 ③은 시장 최댓값(삼성전자)을 앵커로 썼다. 그래서 「삼성보다 큰가」를 묻는 셈이라
    **대형주는 오탐하고 소형주는 놓친다** — 실측(260829): 코스닥 소형주가 1,000배 틀려도
    절대값이 삼성 밑이라 통과했고(JW생명과학 자본 125조·CMG제약 192조), 반대로
    SK하이닉스의 정상 실적은 걸렸다.

    self_ref = 그 회사 과거 자본(자기자본)의 중앙값. 회사마다 자를 따로 들게 하면
    규모와 무관하게 「제 몸의 몇 배인가」로 판정된다.
    """
    if value is None or not self_ref:
        return {"triggered": False}
    ratio = abs(value) / self_ref
    return {"triggered": ratio > factor, "ratio": ratio}


def trailing_zeros(value) -> int:
    """원문 숫자 끝에 0이 몇 개인가. 단위 배수가 덧곱해지면 그만큼 0이 붙는다(260829 실측 8/8)."""
    if value is None:
        return 0
    s = str(abs(int(value)))
    return len(s) - len(s.rstrip("0"))


def propose_scale_fix(value, ref, max_pow: int = 9, band: tuple = (0.2, 5.0)) -> dict:
    """단위 배수가 덧곱해진 값의 **나눌 배수**를 특정한다.

    두 신호가 **독립적으로** 같은 10ⁿ 을 가리킬 때만 제안한다.
      ① 원문 끝의 0 개수가 n 이상 — 배수가 곱해졌으면 그만큼 0이 붙는다
      ② value/10ⁿ 이 그 회사 평소 규모(ref)의 band 안에 든다
    둘 다 맞을 확률은 우연으로 나오지 않는다(260829 실측: 8건 전부 두 신호 일치).

    ⚠️ 이 값은 **공시된 값이 아니다.** 원본 칸에 쓰지 말고 restated 칸에 근거와 함께 남긴다.
    """
    if not value or not ref:
        return {"ok": False}
    tz = trailing_zeros(value)
    lo, hi = band
    for n in range(1, min(max_pow, tz) + 1):
        cand = abs(value) / 10 ** n
        if lo < cand / ref < hi:
            return {"ok": True, "power": n, "fixed": value / 10 ** n,
                    "trailing_zeros": tz, "ratio_to_ref": cand / ref}
    return {"ok": False, "trailing_zeros": tz}


def check_prior_period_mismatch(frmtrm, known_prev, tol: float = _POWER_TOLERANCE) -> dict:
    """①-c(260829 신설, 마스터 착안) **같은 기간의 같은 숫자**를 두 보고서에서 대조한다.

    2026.03 보고서의 「전기」 칸과 2025.12 보고서의 「당기」 칸은 **같은 날짜의 같은
    재무상태**다 — 원래 일치해야 한다. 다르면 변명의 여지가 없다.

    이것이 다른 검사보다 강한 이유: 당기끼리 비교하면 실제 실적 변동과 섞이는데
    (회사가 정말 10배 벌었을 수도 있다), 전기 칸은 **이미 확정된 과거**라 변할 이유가 없다.
    실측(260829, 오염 8건 중 대조 가능한 7건): 전부 적중했고 비율이 1,000.0 · 1,000,000
    처럼 깨끗하게 떨어졌다.

    그리고 보고서는 **통째로** 틀리므로(같은 날 실측: 당기·전기가 같이 부풀려짐,
    항등식 오차 0.0000%) 여기서 나온 배수가 **당기 값의 복구 배수**이기도 하다.
    한 번의 비교로 탐지와 복구가 같이 나온다.

    재작성·연결범위 변경은 비율이 1.07·0.93 처럼 어중간하다 — 10의 거듭제곱에
    딱 붙을 때만 단위 오류로 본다.
    """
    if not frmtrm or not known_prev:
        return {"triggered": False}
    ratio = abs(frmtrm) / abs(known_prev)
    n = _near_power_of_ten(ratio, tol)
    if n is not None and abs(n) >= 2:
        return {"triggered": True, "power": n, "ratio": ratio,
                "fix_divisor": 10 ** n}   # 당기도 같은 배수로 나누면 된다
    return {"triggered": False, "ratio": ratio}


def check_cross_report_jump(value, prev_value, tol: float = _POWER_TOLERANCE) -> dict:
    """①-b(260829 신설) **보고서 사이** 배수점프.

    기존 ①은 같은 응답 안의 당기 vs 전기를 봤다. 그런데 실측(260829) 결과 단위 오류는
    **그 보고서가 통째로** 틀린다 — 당기·전기가 같이 부풀려져 비율이 0.87~1.22 로
    지극히 정상이었다. 그래서 보고서 안에서는 절대 안 잡힌다.
    **직전 분기(다른 보고서)의 저장값**과 대야 10ⁿ 점프가 드러난다.
    """
    if not value or not prev_value:
        return {"triggered": False}
    ratio = abs(value) / abs(prev_value)
    n = _near_power_of_ten(ratio, tol)
    if n is not None and abs(n) >= 2:
        return {"triggered": True, "power": n, "ratio": ratio}
    return {"triggered": False, "ratio": ratio}


# ── 보고서 사이 단위 가리기(261020 p1f unit-restated) ────────────────────────────────────────────────────────────
#: 같은 날짜의 같은 숫자(자본총계)가 두 보고서에서 이 배수 안쪽이면 **같은 단위**로 본다 — 재작성·연결 범위 변경은 수십 % 안쪽이다.
#: 2배 ~ 10ⁿ(±20%) 사이의 어중간한 비율은 단위 판정에 쓰지 않는다(어느 쪽으로도 묶지 않음).
_SAME_SCALE_FACTOR = 2.0
#: 시총 ÷ 자본(PBR)으로 단위를 가를 때 「그럴듯한」 폭 — 10^±1(PBR 0.1~10배). 단위 오류는 100배 이상이라 맞는 단위와 틀린 단위의 PBR 이
#: 두 자릿수 넘게 벌어진다. 맞는 단위가 이 폭 밖이면(PBR 10배 넘는 회사 등) 시총으로는 가르지 않는다.
_PBR_PLAUSIBLE_DECADES = 1.0
#: 금액 칸(원) — 단위를 바로잡을 때 나누는 칸. 주당 금액 줄(EPS 등, 원/주)은 단위 선언과 따로 적혀 나누지 않는다(0091W0 2025 사업보고서:
#: 금액은 1,000배인데 기본주당이익 −11원은 그대로).
_AMOUNT_FIELDS = ("thstrm_amount", "thstrm_add_amount", "frmtrm_amount", "frmtrm_add_amount", "frmtrm_q_amount",
                  "bfefrmtrm_amount", "bfefrmtrm_add_amount")


def reconcile_report_scales(obs, *, mktcap=None) -> dict:
    """여러 보고서가 **같은 날짜의 같은 숫자**(재무상태표 자본총계)를 다른 단위로 적었는지 — 어느 보고서가 10ⁿ 배 틀렸는지 가린다(261020 p1f).

    `check_prior_period_mismatch`(①-c, 분기 수집)는 「그 보고서의 전기 칸 vs 우리가 아는 값」 둘만 대서 **우리가 아는 값을 정답으로** 믿었다.
    그래서 아는 값(저장된 연간)이 틀리면 맞는 보고서를 1,000배로 「복구」했다(10-20 운영 0091W0 신영스팩11호: 2025 사업보고서 금액이 통째로 1,000배 →
    맞는 2026 1분기·반기 칸을 ×1,000). 실시간 firm 은 같은 보고서 안의 검사만 해 이런 보고서를 그대로 읽었다(PBR 0.001 · 지배자본 11조 9,884억).
    단위 오류는 **보고서가 통째로** 틀린다(당기·전기 함께, 항등식 0.0000%) — 보고서 안에서는 안 보이고 **다른 보고서**와 대야 드러난다.

    obs = [(보고서 이름, 날짜 열쇠, 자본총계, 순서)] — 한 보고서가 여러 날짜(당기 말·전기 말)를 낸다. 열쇠가 같으면 같은 날짜·같은 통화의 같은 숫자다
    (통화를 열쇠에 넣는다 — 원화 보고서와 달러 보고서의 비율 1/1,100 이 10⁻³ 에 붙어 단위 오류로 보이지 않게). 순서는 시총 대조에 쓸 「가장 새 값」 고르기용.

    1. 같은 열쇠를 낸 두 보고서마다 비율 → 10ⁿ(|n|≥2, ±20% — `check_prior_period_mismatch` 와 같은 판정)이면 단위 차 n, 2배 안쪽이면 같은 단위(0),
       그 사이는 쓰지 않는다. 부호가 다르면 같은 숫자가 아니라 대지 않는다.
    2. 단위 차를 이어 보고서마다 상대 단위를 매긴다. 고리가 서로 어긋나면 `conflict`(고치지 않는다).
    3. 어느 단위가 맞나 — 두 신호:
       · 다수결: 같은 단위인 보고서가 둘 이상이고 다른 어느 단위보다 많다.
       · 시총(`mktcap`): 가장 새 자본을 각 단위로 바꾼 PBR 가운데 10^±1 안쪽인 것이 하나뿐(틀린 단위는 10ⁿ 배 벌어진다).
       둘 다 있으면 **같아야** 고친다(엇갈리면 `undecided` — 같은 해 보고서들이 함께 틀린 경우를 다수결이 잘못 고르지 않게). 하나만 있으면 그 신호로.
    반환: {"status": "clean"|"fixed"|"undecided"|"conflict", "divisors": {보고서: 나눌 배수}, "offsets": {보고서: 상대 단위 n},
           "pairs": [대조 근거], "method": "majority+mktcap"|"majority"|"mktcap"|None, "pbr": {단위: PBR}}
    `divisors` 는 status 가 fixed 일 때만 차 있다 — 그 보고서의 금액 칸을 그 값으로 나누면(`rescale_rows`) 맞는 단위가 된다.
    """
    obs = [o for o in obs if o is not None]
    reports: list[str] = []
    by_key: dict = {}
    for rep, key, v, order in obs:
        if rep not in reports:
            reports.append(rep)
        if v:
            by_key.setdefault(key, []).append((rep, float(v), order))
    pairs, adj = [], {}
    for key, items in by_key.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (a, va, _), (b, vb, _) = items[i], items[j]
                if a == b or (va > 0) != (vb > 0):
                    continue
                ratio = abs(va) / abs(vb)
                pm = check_prior_period_mismatch(va, vb)
                if pm.get("triggered"):
                    n = pm["power"]
                elif 1 / _SAME_SCALE_FACTOR <= ratio <= _SAME_SCALE_FACTOR:
                    n = 0
                else:
                    continue
                pairs.append({"key": key, "a": a, "b": b, "a_value": va, "b_value": vb, "ratio": ratio, "power": n})
                adj.setdefault(a, []).append((b, -n))      # 단위(a) − 단위(b) = n
                adj.setdefault(b, []).append((a, n))
    out = {"status": "clean", "divisors": {}, "offsets": {}, "pairs": pairs, "method": None, "pbr": {}}
    off: dict[str, int] = {}
    comps = []
    for r in reports:
        if r in off or r not in adj:
            continue
        off[r] = 0
        comp, stack = [r], [r]
        while stack:
            x = stack.pop()
            for y, d in adj[x]:
                want = off[x] + d
                if y not in off:
                    off[y] = want
                    comp.append(y)
                    stack.append(y)
                elif off[y] != want:
                    out.update(status="conflict", offsets=dict(off))
                    return out
        comps.append(comp)
    out["offsets"] = dict(off)
    bad = [c for c in comps if len({off[r] for r in c}) > 1]
    if not bad:
        return out
    divisors, methods = {}, []
    for comp in bad:
        cnt: dict[int, int] = {}
        for r in comp:
            cnt[off[r]] = cnt.get(off[r], 0) + 1
        ranked = sorted(cnt.items(), key=lambda kv: -kv[1])
        maj = ranked[0][0] if (ranked[0][1] >= 2 and ranked[0][1] > ranked[1][1]) else None
        by_cap = None
        if mktcap and mktcap > 0:
            newest = max(((rep, v, order) for rep, _k, v, order in obs if rep in comp and v and v > 0), key=lambda t: t[2], default=None)
            if newest is not None:
                rep, v, _ = newest
                dist = {}
                for o in cnt:
                    pbr = mktcap / (v / 10 ** (off[rep] - o))
                    out["pbr"][o] = pbr
                    dist[o] = abs(math.log10(pbr))
                fit = [o for o, d in dist.items() if d <= _PBR_PLAUSIBLE_DECADES]
                by_cap = fit[0] if len(fit) == 1 else None
        if maj is not None and by_cap is not None:
            if maj != by_cap:
                out.update(status="undecided", method="majority≠mktcap")
                return out
            true, how = maj, "majority+mktcap"
        elif maj is not None:
            true, how = maj, "majority"
        elif by_cap is not None:
            true, how = by_cap, "mktcap"
        else:
            out.update(status="undecided")
            return out
        methods.append(how)
        for r in comp:
            if off[r] != true:
                divisors[r] = 10 ** (off[r] - true)
    out.update(status="fixed", divisors=divisors, method="/".join(sorted(set(methods))))
    return out


def _amount(raw):
    """DART 금액 문자열 → Decimal(쉼표·괄호 음수). 비었거나 숫자가 아니면 None."""
    from decimal import Decimal, InvalidOperation
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if s in ("", "-"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    try:
        d = Decimal(s.strip("()"))
    except InvalidOperation:
        return None
    return -d if neg else d


def rescale_rows(rows: list, divisor) -> list:
    """보고서 rows 의 **금액 칸**을 `divisor` 로 나눈 사본(`reconcile_report_scales` 의 divisors). 주당 금액 줄(EPS — 계정 id 에 PerShare·
    이름에 「주당」)은 그대로 둔다. 나눈 값은 쉼표 없는 수 문자열(정수로 떨어지면 정수). divisor 가 1 이면 rows 그대로."""
    from decimal import Decimal
    if not rows or not divisor or divisor == 1:
        return rows
    dv = Decimal(str(divisor))
    out = []
    for r in rows:
        if "PerShare" in str(r.get("account_id") or "") or "주당" in str(r.get("account_nm") or ""):
            out.append(r)
            continue
        nr = dict(r)
        for f in _AMOUNT_FIELDS:
            v = _amount(r.get(f))
            if v is None:
                continue
            q = v / dv
            nr[f] = str(int(q)) if q == q.to_integral_value() else str(float(q))
        out.append(nr)
    return out


def assess(*, thstrm=None, frmtrm=None, assets=None, liabilities=None, equity=None,
           mktcap=None, market_max=None, self_ref=None, prev_equity=None,
           frmtrm_equity=None, known_prev_equity=None, cross_report=None) -> dict:
    """4개 체크 종합. tier: 'hard'(②③ 중 하나라도 → N/M 무효화) / 'soft'(①④만 → 경고만) / 'clean'.
    market_max 제공 시 ③은 시장최댓값 대비 배수(원칙적, 회사규모 무관 작동) — 없으면 자릿수 백스톱.

    ②(항등식)는 261019 부터 **단위 오류 꼴(10배 이상)일 때만** hard(`balance_identity`)다. 그보다 작은 어긋남은 soft
    `balance_identity_mismatch`(원문 칸 불일치 — 경고만) — 근거는 `check_balance_identity_scale`.

    ①(배수점프)은 260704 전수 재검증(dart_finstat_y 12,995행)에서 오탐률 39/40(97.5%)로 확인돼
    hard에서 제외 — 중소형사의 정상적 실적 급변동(적자↔흑자 전환 등)이 "10^n 근접"이라는 통계적
    기준에 흔하게 걸림(대형 우량주 위주 표본검증에선 안 드러난 사각지대). 정보성 soft 신호로만 유지.
    """
    hard_scale_check = (check_market_relative_cap(thstrm, market_max) if market_max
                         else check_digit_cap(thstrm))
    bal = check_balance_identity_scale(assets, liabilities, equity)
    hard = {
        "balance_identity": bal,
        "market_relative_cap" if market_max else "digit_cap": hard_scale_check,
    }
    # DATA-SC5: 시장 상대 상한이 걸렸어도 시총을 알고, 시총 대비가 정상 범위이고, 전기 대비 10ⁿ 점프도 없으면 새 최대 실적이지 단위 오류가 아니다 → soft.
    #   시총을 모르면 단서가 없어 종전대로 hard(소프트센 032680 FY2022 1.07×10^16 은 시총 대비 수만 배라 그대로 hard).
    capped_soft = None
    if market_max and hard_scale_check.get("triggered") and mktcap:
        corroborated = (check_mktcap_ratio(thstrm, mktcap, _MKTCAP_CORROBORATE_RATIO).get("triggered")
                        or check_magnitude_jump(thstrm, frmtrm).get("triggered"))
        if not corroborated:
            capped_soft = {"triggered": True, "ratio": hard_scale_check.get("ratio"),
                           "mktcap_ratio": abs(thstrm) / mktcap}
            hard["market_relative_cap"] = {"triggered": False, "uncorroborated": True, "ratio": hard_scale_check.get("ratio")}
    # 260829: 자릿수 상한이 **순이익에만** 걸려 있었다. 자본은 아무도 안 봤다 —
    #   소노스퀘어 2024Q3 자본 112,400조(18자리)가 그대로 통과했다. 자본도 본다.
    if equity is not None:
        hard["digit_cap_equity"] = check_digit_cap(equity)
    # 260829: 회사 자기 규모 대비 — 위 두 절대 기준이 못 잡는 중간 배율(×1,000)을 잡는다.
    if self_ref:
        hard["self_relative_ni"] = check_self_relative(thstrm, self_ref)
        if equity is not None:
            hard["self_relative_eq"] = check_self_relative(equity, self_ref)
    # 260829: 직전 분기(다른 보고서) 대비 10ⁿ 점프. 보고서 안에서는 안 보이는 신호다.
    if prev_equity:
        hard["cross_report_jump_eq"] = check_cross_report_jump(equity, prev_equity)
    # 260829: **가장 강한 신호.** 그 보고서의 전기 칸 vs 우리가 아는 그 기간의 값.
    #   같아야 하는 숫자라 어긋나면 곧 오류이고, 어긋난 배수가 복구 배수다.
    if frmtrm_equity and known_prev_equity:
        hard["prior_period_mismatch"] = check_prior_period_mismatch(frmtrm_equity, known_prev_equity)
    # 261020(p1f): 보고서 사이 단위 가리기(`reconcile_report_scales`) — 단위가 어긋난 보고서가 있는데 어느 쪽이 맞는지 못 가렸으면 hard,
    #   가려서 바로잡았으면 soft(값은 바로잡은 값 — 근거는 호출부 경고). 맞으면(clean) 칸을 만들지 않는다(종전 응답 그대로).
    xr_status = (cross_report or {}).get("status")
    if xr_status in ("undecided", "conflict"):
        hard["cross_report_scale"] = {"triggered": True, "status": xr_status, "method": cross_report.get("method")}
    soft = {
        "magnitude_jump": check_magnitude_jump(thstrm, frmtrm),
        "mktcap_ratio": check_mktcap_ratio(thstrm, mktcap),
        # 261019: 항등식이 어긋났지만 단위 오류 꼴은 아닌 경우 — 원문의 자산·부채 총계 칸이 다른 칸과 안 맞는다는 신호(값은 쓴다)
        "balance_identity_mismatch": {"triggered": bool(bal.get("mismatch") and not bal.get("triggered")),
                                      "diff_pct": bal.get("diff_pct"), "ratio": bal.get("ratio")},
    }
    if capped_soft:
        soft["market_relative_cap_uncorroborated"] = capped_soft
    if xr_status == "fixed":
        soft["cross_report_scale_fixed"] = {"triggered": True, "divisors": dict(cross_report.get("divisors") or {}),
                                            "method": cross_report.get("method")}
    hard_hit = [k for k, v in hard.items() if v.get("triggered")]
    soft_hit = [k for k, v in soft.items() if v.get("triggered")]
    tier = "hard" if hard_hit else ("soft" if soft_hit else "clean")
    return {"tier": tier, "hard_hit": hard_hit, "soft_hit": soft_hit,
            "diagnostics": {**hard, **soft}}
