"""forward_estimates — 컨센서스 포워드 추정치·실적(Supabase `fwd`) 조회 서비스.

무엇: 종목 하나의 연도별 **추정(E)·실적(A)** 을 한 판에 담아 낸다. 저장층은 `fwd` 뷰 하나뿐이고
      **가르는 것은 응답에서만** 한다 — `reported`(벤더가 말한 것) / `derived`(우리가 계산한 것).

왜 실적/추정으로 안 가르나: 성장률이 그 경계를 넘나든다. 추정 행의 전기(前期)가 실적 행인 경우가
      2,180행이라, 실적/추정으로 뷰를 가르면 `eps_growth_pct` 가 뷰 경계를 넘어 LLM 이 두 번
      호출해 조인해야 한다. 반면 원천/파생은 **신뢰 등급·갱신 주기·틀렸을 때의 책임**이 다르다.
      (판정 260830 `verdict.md` 1장)

★ 숫자의 기준을 두 겹으로 붙인다 — 봉투(`ruler`)에 한 번, 줄(`row.basis`)마다 또.
  `as_of` 는 2026-08-30 인데 주가는 **8/28 종가**다(주말). `price_dd` 를 안 실으면 읽는 AI 가
  「8월 30일 기준 PER」이라고 말한다. 그래서 `price_dd` 는 어떤 bundle 에서도 빠지지 않는다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from open_proxy_mcp.db import pg_rows

logger = logging.getLogger(__name__)

TOOL = "forward_estimates_data"

# ─────────────────────────────────────────────────────────────────────────────
# 🔴 칸 이름 매핑 — **여기 한 곳만 고친다**
#
# 1단계(DB 층 개명)가 이 파일과 **병렬로** 돌고 있다. 그래서 칸 이름을 코드 여기저기에 흩지
# 않고 이 표 하나에 모았다. 개명 전/후 이름을 **둘 다** 후보로 적어 두고, 런타임에
# `information_schema` 로 실제 있는 칸을 골라 쓴다 — **개명 전후 양쪽에서 다 돈다.**
#
#   cands = (새이름, 옛이름)  ← 앞에 있는 것부터 찾아서 처음 있는 것을 쓴다
#   kind  = money : 물리 칸이 `_eok`(억원)면 ×1e8 해서 **원(KRW)** 으로 통일해 내보낸다
#                   (마스터 결정 2026-08-30 22:55 — 금액은 전부 원. `_eok` 는 밖으로 안 나간다)
#           pct / num / text / bool : 그대로
#   block = reported(벤더 원천) / derived(우리 계산) / meta(행 식별) / envelope(봉투로 올림)
#   bundle= core(기본) / growth / quality / keys
# ─────────────────────────────────────────────────────────────────────────────
_EOK_TO_KRW = 100_000_000  # 1억원

#: (출력칸, 물리칸 후보들, kind, block, bundle)
_FIELDS: tuple[tuple[str, tuple[str, ...], str, str, str], ...] = (
    # ── 행 식별 (bundle 무관, 항상 나간다) ──────────────────────────────────
    ("period",        ("period",),        "text", "meta", "always"),
    ("period_type",   ("period_type",),   "text", "meta", "always"),
    ("is_estimate",   ("is_estimate",),   "bool", "meta", "always"),
    ("basis",         ("basis",),         "text", "meta", "always"),
    ("basis_conflict", ("basis_conflict",), "bool", "meta", "always"),
    # ── 봉투로 올리는 칸 (행마다 같은 값 — 16행에 16번 싣지 않는다) ─────────
    ("as_of",         ("as_of",),         "text", "envelope", "always"),
    ("price_krw",     ("price_krw",),     "num",  "envelope", "always"),
    ("price_dd",      ("price_dd",),      "text", "envelope", "always"),
    ("mktcap_krw",    ("mktcap_krw",),    "num",  "envelope", "always"),
    ("market",        ("market",),        "text", "envelope", "always"),
    ("name",          ("name",),          "text", "envelope", "always"),
    ("sector",        ("sector",),        "text", "envelope", "always"),
    ("industry",      ("industry",),      "text", "envelope", "always"),
    ("share_type",    ("share_type",),    "text", "envelope", "always"),
    ("listing_status", ("listing_status",), "text", "envelope", "always"),
    # ── reported — 벤더가 말한 것 ────────────────────────────────────────────
    ("rev_krw",       ("rev_krw", "rev_eok"),             "money", "reported", "core"),
    ("op_krw",        ("op_krw", "op_eok"),               "money", "reported", "core"),
    ("ni_ctrl_krw",   ("ni_ctrl_krw", "ni_ctrl_eok"),     "money", "reported", "core"),
    ("eps_krw",       ("eps_krw",),                       "num",   "reported", "core"),
    ("bps_krw",       ("bps_krw",),                       "num",   "reported", "core"),
    ("dps_krw",       ("dps_krw",),                       "num",   "reported", "core"),
    ("div_yield_at_period_end_pct",
     ("div_yield_at_period_end_pct", "div_yield_pct"),    "pct",   "reported", "core"),
    ("ebitda_krw",    ("ebitda_krw", "ebitda_eok"),       "money", "reported", "quality"),
    ("roe_pct",       ("roe_pct",),                       "pct",   "reported", "quality"),
    ("roa_pct",       ("roa_pct",),                       "pct",   "reported", "quality"),
    ("op_margin_pct", ("op_margin_pct",),                 "pct",   "reported", "quality"),
    ("net_margin_pct", ("net_margin_pct",),               "pct",   "reported", "quality"),
    ("payout_pct",    ("payout_pct",),                    "pct",   "reported", "quality"),
    ("debt_ratio_pct", ("debt_ratio_pct",),               "pct",   "reported", "quality"),
    ("reserve_ratio_pct", ("reserve_ratio_pct",),         "pct",   "reported", "quality"),
    ("quick_ratio_pct", ("quick_ratio_pct",),             "pct",   "reported", "quality"),
    ("capex_krw",     ("capex_krw", "capex_eok"),         "money", "reported", "quality"),
    ("fcf_krw",       ("fcf_krw", "fcf_eok"),             "money", "reported", "quality"),
    ("debt_interest_krw",
     ("debt_interest_krw", "debt_interest_eok"),          "money", "reported", "quality"),
    ("shares_common", ("shares_common",),                 "num",   "reported", "quality"),
    ("rev_yoy_vendor_pct",
     ("rev_yoy_vendor_pct", "rev_yoy_pct"),               "pct",   "reported", "growth"),
    # ── derived — 우리가 계산한 것 ──────────────────────────────────────────
    ("per",           ("per", "fwd_per"),                 "num",  "derived", "core"),
    ("pbr",           ("pbr", "fwd_pbr"),                 "num",  "derived", "core"),
    ("psr",           ("psr", "fwd_psr"),                 "num",  "derived", "core"),
    ("per_basis",     ("per_basis",),                     "text", "derived", "core"),
    ("pbr_basis",     ("pbr_basis",),                     "text", "derived", "core"),
    ("psr_basis",     ("psr_basis",),                     "text", "derived", "core"),
    ("per_why",       ("per_why",),                       "text", "derived", "core"),
    ("pbr_why",       ("pbr_why",),                       "text", "derived", "core"),
    ("psr_why",       ("psr_why",),                       "text", "derived", "core"),
    ("div_yield_at_price_pct",
     ("div_yield_at_price_pct", "div_yield_own_pct"),     "pct",  "derived", "core"),
    ("div_yield_why", ("div_yield_why",),                 "text", "derived", "core"),
    ("peg",           ("peg",),                           "num",  "derived", "growth"),
    ("peg_why",       ("peg_why",),                       "text", "derived", "growth"),
    ("prev_period",   ("prev_period",),                   "text", "derived", "growth"),
    ("growth_why",    ("growth_why",),                    "text", "derived", "growth"),
    ("rev_growth_pct", ("rev_growth_pct",),               "pct",  "derived", "growth"),
    ("op_growth_pct", ("op_growth_pct",),                 "pct",  "derived", "growth"),
    ("ni_ctrl_growth_pct", ("ni_ctrl_growth_pct",),       "pct",  "derived", "growth"),
    ("eps_growth_pct", ("eps_growth_pct",),               "pct",  "derived", "growth"),
    ("dps_growth_pct", ("dps_growth_pct",),               "pct",  "derived", "growth"),
    ("fcf_growth_pct", ("fcf_growth_pct",),               "pct",  "derived", "growth"),
    ("rev_growth_disp", ("rev_growth_disp",),             "text", "derived", "growth"),
    ("op_growth_disp", ("op_growth_disp",),               "text", "derived", "growth"),
    ("ni_ctrl_growth_disp", ("ni_ctrl_growth_disp",),     "text", "derived", "growth"),
    ("eps_growth_disp", ("eps_growth_disp",),             "text", "derived", "growth"),
    ("dps_growth_disp", ("dps_growth_disp",),             "text", "derived", "growth"),
    ("fcf_growth_disp", ("fcf_growth_disp",),             "text", "derived", "growth"),
    ("rev_growth_state", ("rev_growth_state",),           "text", "derived", "growth"),
    ("op_growth_state", ("op_growth_state",),             "text", "derived", "growth"),
    ("ni_ctrl_growth_state", ("ni_ctrl_growth_state",),   "text", "derived", "growth"),
    ("eps_growth_state", ("eps_growth_state",),           "text", "derived", "growth"),
    ("dps_growth_state", ("dps_growth_state",),           "text", "derived", "growth"),
    ("fcf_growth_state", ("fcf_growth_state",),           "text", "derived", "growth"),
    ("prev_rev_krw",  ("prev_rev_krw", "prev_rev_eok"),   "money", "derived", "growth"),
    ("prev_op_krw",   ("prev_op_krw", "prev_op_eok"),     "money", "derived", "growth"),
    ("prev_ni_ctrl_krw",
     ("prev_ni_ctrl_krw", "prev_ni_ctrl_eok"),            "money", "derived", "growth"),
    ("prev_eps_krw",  ("prev_eps_krw",),                  "num",  "derived", "growth"),
    ("prev_dps_krw",  ("prev_dps_krw",),                  "num",  "derived", "growth"),
    ("prev_fcf_krw",  ("prev_fcf_krw", "prev_fcf_eok"),   "money", "derived", "growth"),
    # ── keys — 정본이 셋인 연도 칸·내부 키. 기본에서 숨긴다 ──────────────────
    #    `fiscal_year`·`fy_end`·`fy_major` 는 30,609행 중 각각 191/218/307행이 서로 다르다.
    #    이름 셋 다 그럴듯해서 그냥 내보내면 LLM 이 아무거나 고른다(판정 5장 ⑤).
    #    1단계가 `fy_canonical`(정본) + `fy_canonical_src`(어느 칸에서 왔나)를 세웠다.
    ("fy_canonical",  ("fy_canonical",),                  "num",  "keys", "keys"),
    ("fy_canonical_src", ("fy_canonical_src",),           "text", "keys", "keys"),
    ("fy_series_irregular", ("fy_series_irregular",),     "bool", "keys", "keys"),
    ("fiscal_year",   ("fiscal_year",),                   "num",  "keys", "keys"),
    ("fy_end",        ("fy_end",),                        "num",  "keys", "keys"),
    ("fy_major",      ("fy_major",),                      "num",  "keys", "keys"),
    ("period_end",    ("period_end",),                    "text", "keys", "keys"),
    ("period_months", ("period_months",),                 "num",  "keys", "keys"),
    ("fyr",           ("fyr",),                           "num",  "keys", "keys"),
    ("basis_from",    ("basis_from",),                    "text", "keys", "keys"),
    ("sec_id",        ("sec_id",),                        "num",  "keys", "keys"),
    ("co_id",         ("co_id",),                         "num",  "keys", "keys"),
)

#: 추정 행에서 **채움률 0.0%** 인 칸들(판정 5장 ⑧, 실측 2026-08-30).
#: 그대로 내보내면 읽는 AI 가 「이 회사는 자료가 없구나」로 읽는다 — **회사 특성이 아니라
#: 데이터 종류의 특성**이다. 그래서 값이 아니라 **봉투에 이유를 적어** 내보낸다.
_ABSENT_ON_ESTIMATE = {
    "debt_interest_krw": "벤더가 이자비용을 추정치로 제공하지 않는다 (추정 행 채움률 0.0%)",
    "reserve_ratio_pct": "벤더가 유보율을 추정치로 제공하지 않는다 (추정 행 채움률 0.0%)",
    "quick_ratio_pct":   "벤더가 당좌비율을 추정치로 제공하지 않는다 (추정 행 채움률 0.0%)",
    "shares_common":     "벤더 추정에 주식수가 없다 (추정 행 채움률 0.0%) — "
                         "주당값 검산은 봉투의 shares_common_latest(최근 실적 행 주식수)로 한다",
}

_BUNDLES = ("core", "growth", "quality", "keys", "revision")

# ─────────────────────────────────────────────────────────────────────────────
# revision — 컨센서스가 **어디서 왔나** (260904 신설)
#
# `fwd` 는 「지금 얼마」만 답한다. 추정치의 방향 전환은 주가보다 먼저 움직이는 신호라
# 「1주·4주·12주 전 대비 얼마나 올랐/내렸나」를 `fwd_hist`(슬림 18칸 · 추정 보유 종목 · 주 1회 토 ·
# 13주 롤링, 260904 신설)에서 읽는다. 기준일은 **목표일 이전의 가장 가까운 스냅샷**이다 —
# 주 1회라 정확히 28일 전은 없다. 이력이 목표일까지 안 닿으면 가장 오래된 스냅샷을 쓰고
# `partial=true` 로 밝힌다(「없음」과 「짧음」을 구별한다). 분모는 |기준값| — 적자에서 흑자로
# 돌아선 것도 방향은 「상향」이다.
# ─────────────────────────────────────────────────────────────────────────────
_REV_WINDOWS: tuple[tuple[str, int], ...] = (("1w", 7), ("4w", 28), ("12w", 91))   # 1w 는 260916 추가 — 주간 루틴이 직전 주 대비를 원했다
_REV_METRICS: tuple[str, ...] = ("rev_krw", "op_krw", "ni_ctrl_krw", "eps_krw", "dps_krw")
_REV_MIN_GAP_DAYS = 6  # 이보다 가까운 스냅샷은 「전」이 아니다

# ─── 분모 가드 (260921) ───────────────────────────────────────────────────────
# `_pct` 의 분모는 |기준값| 이라 **기준 영업이익이 0 에 가까우면 %가 폭주한다.** 적자가 깊어진
# 것도, 적자가 얕아진 것도 백·천 %가 되어 순위 양 끝을 덮는다. 260921 실측(전체 유니버스 ·
# 1w · 비교 가능 666행):
#
#   코윈테크  기준 −5억 → −58억   −1,050%   (이동 −52억)
#   아이씨티케이 기준 −20억 → −73억   −265%   (이동 −53억)
#   한화       기준 7.42조 → 6.10조   −17.8%  (이동 −1.32조)
#
# 이동 절대액이 **250배** 차이인데 순위는 거꾸로다. 「가장 크게 하향된 종목」을 물은 사람이
# 받는 답이 이것이면 도구가 거짓말을 한 것이다.
#
# 그래서 **순위만** 가른다 — 행을 지우지 않는다(원문을 지우지 않는다).
#   · 기준 영업이익 ≤ 0  → `base_loss`.  적자를 분모로 한 %는 「몇 % 좋아졌다」는 뜻이 아니다.
#                          이건 임의값이 아니라 **정의** 문제다.
#   · 0 < 기준 < 하한     → `base_small`. 이쪽은 **임의 임계값이다.** 자연스러운 경계가 없다.
#
# 하한을 100억으로 둔 근거(임의라는 점은 그대로다):
#   · 666행 중 538행(80.8%)이 통과 — 순위의 5분의 4가 남는다. 300억이면 430행(64.6%)까지 줄고
#     LG디스플레이(−7.6%, −708억) 같은 진짜 대형 하향이 Top5 에 들어오지만, 그만큼 중소형의
#     진성 하향도 함께 잘린다. 50억은 아모센스(기준 50억, 이동 −20억)를 못 걸러 효과가 없었다.
#   · |기준| 분위수는 p10 65억 · p25 201억 — 100억은 대략 하위 15% 언저리를 자른다.
#   · 하한을 바꿔도 코드 한 줄이다. 유니버스별 분위수(예: 하위 10%)로 두는 안은 버렸다 —
#     같은 종목이 「코스피200 에선 순위 안, 전체에선 순위 밖」이 되어 두 호출을 못 겹쳐 읽는다.
_REV_RANK_MIN_OP = 100 * 10**8   # 100억원. **임의값** — 위 주석의 실측이 근거지 이론값이 아니다.
_REV_TIER = {"ranked": 0, "base_loss": 1, "base_small": 1, "not_comparable": 2}

# ─── 단발 갱신 표시 (260921) ──────────────────────────────────────────────────
# 값이 몇 주 내내 같다가 한 스냅샷에서만 움직인 종목. 260921 실측으로 전체 685종목 중
# 51종목이 이 모양이고(상향 21 · 하향 30), 그중 이번 창 안에서 움직인 건 18종목이다
# (같은 창에서 움직인 종목은 93개이므로 아무 행에나 붙는 꼬리표는 아니다).
#
# 🔴 **이것은 분할·연결범위 변경 탐지기가 아니다.** 매출·지배순이익 동반 이동과 「연간은
# 움직였는데 분기는 그대로」까지 얹어 51 → 7종목으로 좁힌 뒤 `corporate_restructuring` 으로
# 교차검증했더니, 24개월 내 재편 공시가 있는 건 한화 한 곳뿐이었다(쎄트렉아이·LX홀딩스·
# 코윈테크·트리니티항공·넥스트바이오메디컬·사피엔반도체 모두 0건). 정밀도 1/7 이라 원인을
# 이름 붙일 수 없어 **좁히는 조건을 전부 버리고** 관측된 사실만 남겼다.
#
# 남긴 사실: 「이 %는 누적된 추세가 아니라 벤더의 단발 갱신이다.」 그 이상은 말하지 않는다.
# 원인(재편·커버리지 축소·담당 애널리스트 교체…)은 읽는 쪽이 공시로 확인할 몫이다.
_SOLE_UPDATE_MIN_PCT = 5.0    # 이보다 작은 움직임은 보합과 구별할 실익이 없다
_SOLE_UPDATE_FLAT_PCT = 0.5   # 하우스 보합 밴드 — direction 의 ±0.5% 와 같은 값
_SOLE_UPDATE_MIN_SNAPS = 3    # 「내내 같았다」고 말하려면 최소 이만큼은 봐야 한다


def _pct(now: Any, base: Any) -> float | None:
    if now is None or base is None:
        return None
    try:
        b = abs(float(base))
        if b == 0:
            return None
        return round((float(now) - float(base)) / b * 100, 2)
    except (TypeError, ValueError):
        return None


def compute_revision(hist: list[dict[str, Any]], windows=_REV_WINDOWS) -> dict[str, Any]:
    """`fwd_hist` 추정 행들(dict: as_of·period·period_type·<metrics>) → 리비전 표.

    반환:
      as_of_latest, snapshots(이력 날짜 수), baselines{win: {as_of, days, partial}},
      rows[{period, period_type, now{...}, vs{win: {as_of, <metric>_pct...}}}],
      summary{win: {up, down, flat, n, basis:"op_krw"}}
    hist 가 비면 rows=[] 로 돌려준다 — 호출부가 「자료 없음」으로 말한다.
    """
    if not hist:
        return {"as_of_latest": None, "snapshots": 0, "baselines": {}, "rows": [], "summary": {}}
    import datetime as _dt

    def _d(v: Any) -> _dt.date:
        return v if isinstance(v, _dt.date) else _dt.date.fromisoformat(str(v)[:10])

    by_day: dict[_dt.date, dict[tuple[str, str], dict[str, Any]]] = {}
    for r in hist:
        by_day.setdefault(_d(r["as_of"]), {})[(r["period"], r["period_type"])] = r
    days = sorted(by_day)
    latest = days[-1]
    baselines: dict[str, dict[str, Any]] = {}
    for name, span in windows:
        target = latest - _dt.timedelta(days=span)
        cands = [d for d in days if d <= target]
        if cands:
            b, partial = cands[-1], False
        else:
            older = [d for d in days if (latest - d).days >= _REV_MIN_GAP_DAYS]
            if not older:
                continue
            b, partial = older[0], True
        baselines[name] = {"as_of": b.isoformat(), "days": (latest - b).days, "partial": partial}

    rows: list[dict[str, Any]] = []
    summary: dict[str, dict[str, Any]] = {n: {"up": 0, "down": 0, "flat": 0, "n": 0, "basis": "op_krw"}
                                          for n in baselines}
    for key, cur in sorted(by_day[latest].items(), key=lambda kv: (kv[0][1], kv[0][0])):
        period, ptype = key
        now = {m: cur.get(m) for m in _REV_METRICS if cur.get(m) is not None}
        vs: dict[str, Any] = {}
        for name, b in baselines.items():
            base = by_day[_d(b["as_of"])].get(key)
            if base is None:
                vs[name] = {"as_of": b["as_of"], "absent": True}   # 그때는 이 기간 추정이 없었다
                continue
            cell: dict[str, Any] = {"as_of": b["as_of"]}
            for m in _REV_METRICS:
                p = _pct(cur.get(m), base.get(m))
                if p is not None:
                    cell[f"{m}_pct"] = p
            vs[name] = cell
            op = cell.get("op_krw_pct")
            if op is not None and ptype == "FY":
                s = summary[name]
                s["n"] += 1
                s["up" if op > 0.5 else "down" if op < -0.5 else "flat"] += 1
        rows.append({"period": period, "period_type": ptype, "now": now, "vs": vs})
    return {"as_of_latest": latest.isoformat(), "snapshots": len(days),
            "baselines": baselines, "rows": rows, "summary": summary}


_hist_cache: dict[str, Any] = {"at": 0.0, "ok": None}


def _hist_available() -> bool | None:
    """`fwd_hist` 표가 있나 — 10분 캐시. None = DB 장애.
    없는 표를 바로 질의하면 db.pg_rows 가 풀을 60초 내린다(질의 오류를 장애로 본다) —
    그래서 `to_regclass` 로 먼저 묻는다. 이건 표가 없어도 오류가 아니다."""
    now = time.monotonic()
    if _hist_cache["ok"] is not None and now - _hist_cache["at"] < 600:
        return _hist_cache["ok"]
    r = pg_rows("SELECT to_regclass('public.fwd_hist') IS NOT NULL")
    if r is None:
        return None
    _hist_cache.update(at=now, ok=bool(r and r[0][0]))
    return _hist_cache["ok"]


def _fetch_hist(stock_code: str) -> list[dict[str, Any]] | None:
    """`fwd_hist` 추정 행 전부(13주 롤링이라 많아야 ~150행). None = DB 장애. [] = 표 없음/이력 없음."""
    avail = _hist_available()
    if avail is None:
        return None
    if not avail:
        return []
    cols = ("as_of", "period", "period_type") + _REV_METRICS
    rows = pg_rows(f"SELECT {', '.join(cols)} FROM fwd_hist "
                   "WHERE stock_code=%s AND is_estimate ORDER BY as_of", (stock_code,))
    if rows is None:
        return None
    return [dict(zip(cols, r)) for r in rows]

#: 배수 분모 자 — PER 정의를 `price_multiple_data` 와 **맞춘다**(보통주 시총 ÷ 지배순이익).
#: 왜: `fwd` 원본은 주가÷EPS 인데 그 식은 260823 에 하우스에서 **의도적으로 버린 것**이다
#:     (액면분할·병합 때 옛 주식수 기준 EPS 와 새 주가가 섞여 틀렸다). 같은 `per` 라는 이름으로
#:     두 도구가 다른 값을 내면(삼성 FY2025 33.95 vs 39.15, 15.3% 차) 한 답변에 나란히 놓였을 때
#:     읽는 AI 가 하나를 고르고 근거를 지어낸다. 이름을 가르는 대신 **정의를 맞췄다.**
_PER_DEF = "보통주 시총 ÷ 지배주주순이익"
_PER_DEF_FALLBACK = "주가 ÷ EPS"


# ─────────────────────────────────────────────────────────────────────────────
# 물리 칸 해석 — 1단계 개명 전/후 어느 쪽이든 돈다
# ─────────────────────────────────────────────────────────────────────────────
_cols_cache: tuple[float, frozenset[str] | None] = (0.0, None)
_COLS_TTL = 300.0  # 초. 1단계가 도는 중이라 짧게 둔다(개명이 반영되기까지의 지연)


def _live_columns(force: bool = False) -> frozenset[str] | None:
    """운영 `fwd` 표에 **실제로 있는** 칸 이름. None = DB 미설정/장애.

    칸 이름을 상상하지 않는다. 1단계 개명이 병렬로 진행 중이라 코드가 아는 이름과 DB 의 이름이
    어긋날 수 있는데, 어긋난 순간 조용히 `column does not exist` 로 죽는 대신 **있는 쪽을 고른다.**
    """
    global _cols_cache
    ts, cached = _cols_cache
    if not force and cached is not None and (time.monotonic() - ts) < _COLS_TTL:
        return cached
    rows = pg_rows("SELECT column_name FROM information_schema.columns "
                   "WHERE table_schema='public' AND table_name='fwd'")
    if rows is None:
        return None
    cols = frozenset(r[0] for r in rows)
    _cols_cache = (time.monotonic(), cols)
    return cols


def resolve_columns(live: frozenset[str]) -> dict[str, str]:
    """출력칸 → 물리칸. 후보 중 **DB 에 실제로 있는 첫 번째**를 고른다. 없으면 뺀다."""
    out: dict[str, str] = {}
    for name, cands, _kind, _block, _bundle in _FIELDS:
        for c in cands:
            if c in live:
                out[name] = c
                break
    return out


def _spec(name: str) -> tuple[str, str, str]:
    """(kind, block, bundle)."""
    for n, _c, kind, block, bundle in _FIELDS:
        if n == name:
            return kind, block, bundle
    return "text", "reported", "keys"


def _to_krw(v: Any, physical: str) -> int | None:
    """금액 칸을 **원(KRW)** 으로 통일. 물리 칸이 `_eok` 면 ×1e8.

    마스터 결정(2026-08-30 22:55): 금액은 전부 원. 억원은 응답 밖으로 나가지 않는다.
    한 답변에 `rev_eok=7,384,675.3`(억원)과 `net_income_ttm_krw=44,260,960,000,000`(원)이
    같이 놓이면 읽는 AI 가 **1억 배 틀린다.**
    """
    if v is None:
        return None
    x = float(v) * (_EOK_TO_KRW if physical.endswith("_eok") else 1)
    return int(round(x))


# ─────────────────────────────────────────────────────────────────────────────
# 조회
# ─────────────────────────────────────────────────────────────────────────────
def _fetch(stock_code: str, colmap: dict[str, str]) -> list[dict[str, Any]] | None:
    """최신 as_of 한 벌. None = DB 장애."""
    names = list(colmap.keys())
    phys = [colmap[n] for n in names]
    sql = (f"SELECT {', '.join(phys)} FROM fwd "
           "WHERE stock_code=%s AND as_of=(SELECT MAX(as_of) FROM fwd WHERE stock_code=%s) "
           "ORDER BY period, period_type")
    rows = pg_rows(sql, (stock_code, stock_code))
    if rows is None:
        return None
    out: list[dict[str, Any]] = []
    for r in rows:
        rec: dict[str, Any] = {}
        for i, n in enumerate(names):
            v = r[i]
            kind = _spec(n)[0]
            if kind == "money":
                v = _to_krw(v, colmap[n])
            elif kind == "text" and v is not None:
                v = str(v)
            rec[n] = v
        out.append(rec)
    return out


def _period_sort_key(p: str) -> tuple:
    """'2026.12E' → (2026, 12). 라벨 접미(A/E)는 떼고 본다."""
    body = (p or "").rstrip("AE")
    try:
        y, m = body.split(".")
        return (int(y), int(m))
    except Exception:                      # noqa: BLE001
        return (0, 0)


# ─────────────────────────────────────────────────────────────────────────────
# 배수 재계산 — 정의 정렬 + 게이팅
# ─────────────────────────────────────────────────────────────────────────────
def _apply_multiples(rows: list[dict[str, Any]], mktcap: float | None,
                     price: float | None, price_dd: str | None) -> list[str]:
    """`per` 를 하우스 정의(보통주 시총÷지배순이익)로 다시 만들고, **뜻 없는 배수를 지운다.**

    지우는 이유(판정 5장 ①): `fwd_per` 이 실적 행에 16,778개 채워져 있는데 그 80.5% 가
      「**오늘 주가 ÷ 몇 년 전 EPS**」다. 삼성전자 2023.12A 는 120.6 이 붙어 있고
      `per_why` 는 `'ok'` 라고 말한다. 이름 없는 숫자에 PER 이라는 이름이 붙어 있는 것이다.
      → **최신 확정 FY 와 추정 FY 에만** 배수를 남긴다.

    반환: 벤더식(주가÷EPS)과 우리 식이 10% 이상 갈린 기간 라벨들 (봉투 경고용).
    """
    fy_actual = [r for r in rows if r.get("period_type") == "FY" and not r.get("is_estimate")]
    latest_actual = max((r["period"] for r in fy_actual), key=_period_sort_key, default=None)
    gaps: list[str] = []
    for r in rows:
        is_fy = r.get("period_type") == "FY"
        is_est = bool(r.get("is_estimate"))
        keep = is_fy and (is_est or r.get("period") == latest_actual)
        vendor_per = r.get("per")
        if not keep:
            # 분기 행 · 과거 실적 FY 행 — 배수를 만들지 않는다. 왜 없는지는 남긴다.
            for k in ("per", "pbr", "psr"):
                r[k] = None
            r["per_basis"] = r["pbr_basis"] = r["psr_basis"] = None
            why = ("분기 행 — 분기 EPS·BPS 를 주가로 나눈 값은 배수가 아니다(연환산 필요)"
                   if not is_fy else
                   "최신 확정 FY 가 아니다 — 오늘 주가 ÷ 과거 연도 실적은 뜻이 없는 숫자다")
            r["per_why"] = r["pbr_why"] = r["psr_why"] = why
            continue
        at = f" @{price_dd} 종가" if price_dd else ""
        ni = r.get("ni_ctrl_krw")
        if mktcap and ni and ni > 0:
            ours = round(mktcap / ni, 2)
            if vendor_per and vendor_per > 0:
                gap = abs(ours - vendor_per) / vendor_per * 100
                if gap >= 10:
                    gaps.append(f"{r['period']}(우리 {ours} vs 벤더식 {round(vendor_per, 2)}, "
                                f"{gap:.1f}% 차)")
            r["per"] = ours
            r["per_basis"] = _PER_DEF + at
            r["per_why"] = None
        elif vendor_per:
            # 지배순이익이 없어 하우스 정의를 못 쓴다 → 벤더식을 쓰되 **기준이 바뀐 것을 적는다.**
            r["per"] = round(vendor_per, 2)
            r["per_basis"] = (_PER_DEF_FALLBACK + at
                              + " (지배순이익 결측 폴백 — 위 행들과 기준이 다르다)")
            r["per_why"] = None
        else:
            r["per"] = None
            r["per_basis"] = None
        # PBR·PSR 은 정의 충돌이 없다 — 시총 = 주가 × 보통주식수 라서
        #   주가÷BPS ≡ 시총÷(BPS×주식수), 주가÷SPS ≡ 시총÷매출. 값은 그대로 두고 자만 적는다.
        bps = r.get("bps_krw")
        if r.get("pbr") is None and price and bps and bps > 0:
            # 최신 확정 FY 실적 행에 PBR 이 비어 있는 경우를 메운다 — 그 행에 PER·PSR 은 있는데
            #   PBR 만 없으면 읽는 쪽은 「자본 자료가 없나」로 읽는다. 자료는 있다(BPS 가 있다).
            r["pbr"] = round(price / bps, 2)
        if r.get("pbr") is not None:
            r["pbr"] = round(r["pbr"], 2)
            r["pbr_basis"] = "보통주 시총 ÷ 자기자본(BPS×보통주식수)" + at
            r["pbr_why"] = None
        if r.get("psr") is not None:
            r["psr"] = round(r["psr"], 2)
            r["psr_basis"] = "보통주 시총 ÷ 매출" + at
            r["psr_why"] = None
    return gaps


# ─────────────────────────────────────────────────────────────────────────────
# 응답 조립
# ─────────────────────────────────────────────────────────────────────────────
def _shape_row(rec: dict[str, Any], bundles: set[str]) -> dict[str, Any]:
    """한 행을 `reported` / `derived` 두 블록으로 담는다.

    실적/추정이 아니라 **원천/파생**으로 가른다 — 갈리는 것은 「누가 책임지나」이지
    「추정이냐」가 아니다. 추정이냐는 `row_kind` 한 글자로 이미 행이 지고 있다.
    """
    out: dict[str, Any] = {
        "period": rec.get("period"),
        "period_type": rec.get("period_type"),
        "row_kind": "estimate" if rec.get("is_estimate") else "actual",
        "basis": rec.get("basis"),
    }
    if rec.get("basis_conflict"):
        out["basis_conflict"] = True
    rep: dict[str, Any] = {}
    der: dict[str, Any] = {}
    keys: dict[str, Any] = {}
    for name, value in rec.items():
        kind, block, bundle = _spec(name)
        if block in ("meta", "envelope"):
            continue
        if bundle not in bundles:
            continue
        if value is None:
            continue           # 빈칸을 0 으로도, "미상" 으로도 채우지 않는다 (봉투가 정책을 밝힌다)
        if isinstance(value, float):
            value = round(value, 4) if kind == "pct" else round(value, 4)
        if block == "reported":
            rep[name] = value
        elif block == "derived":
            der[name] = value
        else:
            keys[name] = value
    if rep:
        out["reported"] = rep
    if der:
        out["derived"] = der
    if keys:
        out["keys"] = keys
    return out


def parse_bundles(bundle: str) -> tuple[set[str], list[str]]:
    """'core' / 'core,growth' / 'all' → 묶음 집합. 모르는 이름은 경고로 돌려준다."""
    raw = (bundle or "core").strip().lower()
    if raw in ("all", "*", "full"):
        return set(_BUNDLES), []
    want, bad = set(), []
    for tok in raw.replace("+", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok in _BUNDLES:
            want.add(tok)
        else:
            bad.append(tok)
    if not want:
        want = {"core"}
    if "revision" in want:
        # 리비전만 부르면 「추정·실적」 표가 빈칸으로 나간다(reported 칸이 core 묶음) —
        # 「지금 값」 없이 「얼마나 바뀌었나」만 보는 것은 반쪽이라 core 를 같이 싣는다(260904).
        want.add("core")
    return want, bad


async def build_forward_estimates_payload(
    company: str = "", bundle: str = "core", period_type: str = "FY",
    actual_years: int = 2, format: str = "md",
) -> dict[str, Any]:
    """`fwd` 스냅샷 한 종목. status: ok / no_estimates / not_found / unlisted /
    ambiguous / db_error / invalid — **「없음」을 뭉뚱그리지 않는다.**"""
    from open_proxy_mcp.services.company import (COMPANY_LOOKUP_NEXT_ACTION,
                                             company_not_found_warning)
    from open_proxy_mcp.services.price_multiple_data import _resolve_listed

    query = (company or "").strip()
    if not query:
        return {"tool": TOOL, "status": "invalid", "subject": company,
                "warnings": ["회사명 또는 종목코드(6자리)를 입력하세요."]}

    bundles, bad_bundles = parse_bundles(bundle)
    pt = (period_type or "FY").strip().upper()
    if pt not in ("FY", "Q", "ALL"):
        return {"tool": TOOL, "status": "invalid", "subject": query,
                "warnings": [f"period_type '{period_type}' 없음 — FY / Q / all 중 선택."]}

    # 회사명 자리에 「코스피 시총 상위 100개 … 1주 전 대비」 같은 문장이 오면 종목이 아니라 유니버스다.
    # 옛 도구 정의를 가진 호출자는 universe 인자를 모른다 — 문장을 읽어 스크린으로 보낸다(260916).
    from open_proxy_mcp.services.universe import universe_phrase
    phrase = universe_phrase(query)
    if phrase:
        uni, win = phrase
        payload = await build_revision_screen_payload(universe=uni, window=win or "4w",
                                                      period_type=pt, format=format)
        payload.setdefault("warnings", []).insert(
            0, f"회사명 자리의 문장을 유니버스 「{uni}」" + (f"·비교 창 {win}" if win else "")
               + " 으로 읽어 유니버스 리비전 스크린으로 답했다. 다음부터는 universe 인자로 주면 된다.")
        return payload
    corp, early = await _resolve_listed(query)   # 공용 리졸버 — company 툴과 동일 진입
    if early:
        early["tool"] = TOOL
        return early
    # 회사 자체가 없는 것과, 회사는 있는데 비상장인 것은 다른 사건이다.
    if not corp:
        return {"tool": TOOL, "status": "not_found", "subject": query,
                "next_actions": [COMPANY_LOOKUP_NEXT_ACTION],
                "warnings": [company_not_found_warning(query, listed_only=True),
                             "우선주는 보통주 종목코드로 조회한다."]}
    if not corp.get("stock_code"):
        return {"tool": TOOL, "status": "unlisted", "subject": query,
                "warnings": [f"'{query}' 는 찾았으나 상장 종목이 아니다 — 컨센서스는 상장사만 있다."]}
    isu = corp["stock_code"]
    subject = corp.get("corp_name", query)

    live = await asyncio.to_thread(_live_columns)
    if live is None:
        return {"tool": TOOL, "status": "db_error", "subject": subject,
                "warnings": ["추정치 DB(Supabase `fwd`) 접속 실패 — **자료 없음이 아니라 장애**다. "
                             "잠시 후 재시도. 계속되면 운영에 알릴 것."]}
    colmap = resolve_columns(live)
    if "period" not in colmap or "is_estimate" not in colmap:
        return {"tool": TOOL, "status": "db_error", "subject": subject,
                "warnings": ["`fwd` 표 구조를 알아보지 못했다(필수 칸 없음) — 스키마 개명 진행 중일 수 있다."]}

    recs = await asyncio.to_thread(_fetch, isu, colmap)
    if recs is None:
        return {"tool": TOOL, "status": "db_error", "subject": subject,
                "warnings": ["추정치 DB 조회 실패 — **자료 없음이 아니라 장애**다."]}

    if not recs:
        return {"tool": TOOL, "status": "no_estimates", "subject": subject,
                "data": {"ticker": isu, "coverage": {"in_snapshot": False, "estimate_rows": 0}},
                "warnings": [f"'{subject}'({isu}) 는 컨센서스 추정치 스냅샷 `fwd` 에 **아예 없다** — "
                             "커버리지 밖 종목(전체 2,764종목 중 추정 보유 713종목, 25.8%). "
                             "DB 장애가 아니고 종목 오인도 아니다. "
                             "확정 실적·배수는 `price_multiple_data`·`financial_metrics` 로."]}

    env = {n: recs[0].get(n) for n, _c, _k, block, _b in _FIELDS if block == "envelope"}
    mktcap = env.get("mktcap_krw")
    gaps = _apply_multiples(recs, mktcap, env.get("price_krw"), env.get("price_dd"))

    est_rows = [r for r in recs if r.get("is_estimate")]
    shares_latest = next((r.get("shares_common") for r in sorted(
        (x for x in recs if not x.get("is_estimate") and x.get("shares_common")),
        key=lambda x: _period_sort_key(x["period"]), reverse=True)), None)

    # 행 고르기 — 추정은 전부, 실적은 최근 N 개년(대조용). 기본을 좁게 두되 손잡이를 준다.
    picked = [r for r in recs if pt == "ALL" or r.get("period_type") == pt]
    act = sorted((r for r in picked if not r.get("is_estimate")),
                 key=lambda x: _period_sort_key(x["period"]), reverse=True)
    keep_actual = {id(r) for r in act[:max(0, actual_years)]}
    picked = [r for r in picked if r.get("is_estimate") or id(r) in keep_actual]
    picked.sort(key=lambda x: (_period_sort_key(x["period"]), x.get("period_type") or ""))

    warnings: list[str] = []
    if bad_bundles:
        warnings.append(f"모르는 bundle 무시: {', '.join(bad_bundles)} — "
                        f"core / growth / quality / keys / all 중에서 고르세요.")
    if not est_rows:
        warnings.append(f"'{subject}'({isu}) 는 **컨센서스 추정치가 없다** — 실적 행만 있다. "
                        "커버리지 밖 종목이다(전체 2,764종목 중 추정 보유 713종목, 25.8%). "
                        "애널리스트 미커버 소형주에서 정상이다. DB 장애가 아니다.")
    if gaps:
        warnings.append("⚠ 벤더 원본(주가÷EPS)과 우리 PER(시총÷지배순이익)이 10% 이상 갈리는 기간: "
                        + " · ".join(gaps)
                        + ". 우선주가 있어 EPS 의 가중평균 주식수와 보통주식수가 벌어지는 종목이다. "
                          "위 `per` 은 `price_multiple_data` 와 같은 정의로 맞춘 값이다.")
    if env.get("as_of") and env.get("price_dd"):
        dd = str(env["price_dd"])
        as_of = str(env["as_of"])
        if dd.replace("-", "") != as_of.replace("-", ""):
            warnings.append(f"⚠ 스냅샷 날짜({as_of})와 **주가 날짜({dd})가 다르다** — 배수는 "
                            f"{dd} 종가로 계산됐다. 「{as_of} 기준 PER」이라고 쓰지 말 것.")

    absent = {k: v for k, v in _ABSENT_ON_ESTIMATE.items()
              if _spec(k)[2] in bundles} if est_rows else {}

    # ── revision: 1주·4주·12주 전 대비 (fwd_hist) ──────────────────────────────────
    revision: dict[str, Any] | None = None
    if "revision" in bundles and est_rows:
        hist = await asyncio.to_thread(_fetch_hist, isu)
        if hist is None:
            warnings.append("⚠ 리비전 이력(`fwd_hist`) 조회 실패 — **장애**다, 자료 없음이 아니다. "
                            "현재 추정치는 정상이다. 재시도할 것.")
        else:
            revision = compute_revision(hist)
            if not revision["rows"]:
                warnings.append("리비전 이력이 없다 — `fwd_hist` 에 이 종목 추정 행이 없다"
                                "(260904 신설 표, 주 1회 토요일 적재). 현재 추정치는 정상이다.")
            elif not revision["baselines"]:
                warnings.append(f"리비전 비교 불가 — 이력이 {revision['snapshots']}개 스냅샷뿐이라 "
                                f"{_REV_MIN_GAP_DAYS}일 이상 떨어진 기준일이 없다. 다음 주부터 4w 가 잡힌다.")
            else:
                for name, b in revision["baselines"].items():
                    if b["partial"]:
                        warnings.append(f"리비전 {name}: 이력이 {b['days']}일치뿐이라 가장 오래된 "
                                        f"스냅샷({b['as_of']})과 비교했다 — **{name} 가 아니다**, 이력이 짧아 부분 비교다.")

    ruler = {
        "as_of": str(env.get("as_of")) if env.get("as_of") else None,
        "price_dd": env.get("price_dd"),
        "price_krw": env.get("price_krw") and int(round(env["price_krw"])),
        "mktcap_krw": mktcap and int(round(mktcap)),
        "shares_common_latest": shares_latest and int(shares_latest),
        "unit": "금액=원(KRW) 정수 · 비율=% · 배수=배. 억원(_eok) 은 쓰지 않는다",
        "per_def": _PER_DEF + " — `price_multiple_data` 와 같은 정의로 맞췄다",
        "pbr_def": "보통주 시총 ÷ 자기자본(BPS×보통주식수)",
        "psr_def": "보통주 시총 ÷ 매출",
        "multiple_scope": "배수는 **추정 FY 행과 최신 확정 FY 행에만** 있다 — "
                          "오늘 주가를 과거 연도 실적으로 나눈 숫자는 배수가 아니라서 뺐다",
        "row_split": "reported=벤더 컨센서스 원천값(틀리면 벤더 책임) · "
                     "derived=우리가 계산한 값(주가 스냅샷·전기 매칭이 섞인다, 검산 대상)",
        "growth_caveat": "성장률의 전기(prev_period)가 추정(E)일 수 있다 — 추정 위에 쌓은 추정이다. "
                         "prev_period 접미 A/E 를 반드시 확인할 것 (bundle=growth)",
        "null_policy": "값이 없는 칸은 응답에서 뺐다. 뺀 것은 「0」이 아니라 「자료 없음」이다",
        "source": "컨센서스 추정치 스냅샷 `fwd` (Supabase) — DART 공시가 아니다",
    }
    data: dict[str, Any] = {
        "ticker": isu, "name": env.get("name"), "market": env.get("market"),
        "sector": env.get("sector"), "industry": env.get("industry"),
        "share_type": env.get("share_type"),
        "bundle": sorted(bundles), "period_type": pt,
        "coverage": {"in_snapshot": True, "estimate_rows": len(est_rows),
                     "total_rows": len(recs)},
        "ruler": ruler,
        "rows": [_shape_row(r, bundles) for r in picked],
    }
    if absent:
        data["fields_absent_by_design"] = absent
        data["fields_absent_note"] = ("아래 칸은 **이 회사에 자료가 없어서가 아니라** 벤더가 "
                                      "추정 행에 아예 채우지 않는 종류라서 비어 있다. "
                                      "회사 특성으로 읽지 말 것.")
    if revision is not None:
        revision["note"] = ("기준일은 목표일(1w=7일·4w=28일·12w=91일) **이전의 가장 가까운 주간 스냅샷**. "
                            "%는 (지금−기준)/|기준|. 방향 집계는 FY 추정 행의 영업이익 기준 상향/하향 개수"
                            "(±0.5% 안은 flat). 출처 `fwd_hist`(주 1회 토, 13주 롤링) — 그 너머는 없다.")
        data["revision"] = revision
    if len(bundles) < len(_BUNDLES):
        data["more"] = ("더 필요하면 bundle 을 넓히세요 — "
                        "growth(성장률·전기값·PEG) · quality(수익성·재무비율) · "
                        "keys(내부키·회계연도 칸) · revision(1주·4주·12주 전 대비 추정 변화) · all(전부). "
                        "기본 core 는 크기를 줄이려고 자른 것이지 그것이 정답이라서가 아니다.")
    return {"tool": TOOL, "status": "ok" if est_rows else "no_estimates",
            "subject": subject, "data": data, "warnings": warnings}


# ─────────────────────────────────────────────────────────────────────────────
# revision screen — 유니버스 전체의 리비전을 **한 질의**로 (260916)
#
# 계기: 주간 루틴이 200종목 리비전을 종목마다 `bundle=revision` 으로 200번 불렀다(서브에이전트
# 10여 개, 수 분). `fwd_hist` 는 전체 9만 행에 (stock_code, period, period_type, as_of) 색인이
# 있어 코드 200개를 `= ANY` 로 한 번에 읽고 `compute_revision` 을 메모리에서 돌리면 끝난다.
# 종목당 1콜은 설계가 아니라 미구현이었다.
# ─────────────────────────────────────────────────────────────────────────────
_REV_SCREEN_COLS: tuple[str, ...] = ("stock_code", "as_of", "period", "period_type") + _REV_METRICS
_REV_SCREEN_MD_MAX = 300


def _fetch_hist_many(codes: list[str] | None, period_type: str) -> list[tuple] | None:
    """유니버스의 `fwd_hist` 추정 행을 한 질의로. codes=None 이면 커버리지 전 종목.
    None = DB 장애 · [] = 표 없음/이력 없음."""
    avail = _hist_available()
    if avail is None:
        return None
    if not avail:
        return []
    sql = f"SELECT {', '.join(_REV_SCREEN_COLS)} FROM fwd_hist WHERE is_estimate"
    params: list[Any] = []
    if period_type in ("FY", "Q"):
        sql += " AND period_type=%s"
        params.append(period_type)
    if codes is not None:
        sql += " AND stock_code = ANY(%s)"
        params.append(codes)
    sql += " ORDER BY stock_code, as_of"
    return pg_rows(sql, tuple(params))


def _as_num(v: Any) -> float | None:
    """DB 가 돌려준 Decimal·str 을 셈할 수 있는 수로. 못 고치면 None — 0 으로 메우지 않는다."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _series(hist: list[dict[str, Any]], period: str, period_type: str,
            metric: str) -> list[tuple[str, Any]]:
    """한 기간·한 지표의 (스냅샷일, 값) 시계열. 같은 날 중복은 뒤엣것이 이긴다."""
    d = {str(r["as_of"])[:10]: r.get(metric) for r in hist
         if r.get("period") == period and r.get("period_type") == period_type
         and r.get(metric) is not None}
    return sorted(d.items())


def _sole_update(hist: list[dict[str, Any]], period: str, period_type: str,
                 metric: str = "op_krw") -> dict[str, Any] | None:
    """관측 구간 내내 보합이다가 **한 스냅샷에서만** 움직였으면 그 갱신을, 아니면 None.

    돌려주는 건 관측된 사실뿐이다 — 「이 창의 %는 누적된 추세가 아니라 단발 갱신이다」.
    **원인은 말하지 않는다.** 재편·연결범위 변경으로 좁히려던 시도와 그 실패는 파일 윗부분
    `_SOLE_UPDATE_*` 주석에 남겼다(정밀도 1/7).

    창(1w·4w·12w) 변화율로는 이걸 못 본다 — 스냅샷이 적으면 4w·12w 가 같은 기준일로 접혀
    세 창이 같은 값이 되기 때문에, 원계열을 직접 훑는다."""
    ser = _series(hist, period, period_type, metric)
    if len(ser) < _SOLE_UPDATE_MIN_SNAPS:
        return None
    steps = [(ser[i][0], _pct(ser[i][1], ser[i - 1][1])) for i in range(1, len(ser))]
    if any(p is None for _, p in steps):
        return None
    big = [(d, p) for d, p in steps if abs(p) >= _SOLE_UPDATE_MIN_PCT]
    if len(big) != 1:
        return None
    if any(abs(p) > _SOLE_UPDATE_FLAT_PCT for d, p in steps if (d, p) != big[0]):
        return None
    return {"as_of": big[0][0], f"{metric}_pct": big[0][1], "snapshots": len(ser)}


def _rank_basis(pct: float | None, base_op: float | None) -> str:
    """이 행을 순위에 올려도 되나 — 올릴 수 없으면 **왜 못 올리는지**를 돌려준다."""
    if pct is None:
        return "not_comparable"
    if base_op is None or base_op <= 0:
        return "base_loss"          # 적자를 분모로 한 %는 「몇 % 좋아졌다」가 아니다 (정의 문제)
    if base_op < _REV_RANK_MIN_OP:
        return "base_small"         # 분모가 작아 %가 폭주한다 (임의 임계값)
    return "ranked"


def _screen_row(code: str, hist: list[dict[str, Any]], win: str,
                meta: dict[str, Any]) -> dict[str, Any] | None:
    """종목 하나의 이력 → 스크린 한 행. 초점은 **가장 가까운 연간 추정 기간**(FY 가 없으면 첫 행)."""
    rev = compute_revision(hist)
    if not rev["rows"]:
        return None
    fy = [x for x in rev["rows"] if x["period_type"] == "FY"] or rev["rows"]
    focus = fy[0]
    base = rev["baselines"].get(win)
    cell = (focus["vs"].get(win) or {}) if base else {}
    row: dict[str, Any] = {
        "ticker": code, "name": meta.get("name") or "-", "market": meta.get("market"),
        "mktcap_krw": meta.get("mktcap_krw"), "rank_mktcap": meta.get("rank"),
        "period": focus["period"], "period_type": focus["period_type"],
        "as_of_latest": rev["as_of_latest"], "snapshots": rev["snapshots"],
        "op_krw": (focus.get("now") or {}).get("op_krw"),
        "baseline_as_of": base["as_of"] if base else None,
        "baseline_days": base["days"] if base else None,
        "history_short": bool(base and base.get("partial")),
        "absent_at_baseline": bool(cell.get("absent")),
    }
    for m in _REV_METRICS:
        row[f"{m}_{win}_pct"] = cell.get(f"{m}_pct")

    # 분모와 **이동 절대액**을 같이 싣는다 — %만 있으면 한화의 1.32조원 이동이 비나텍의
    # 15억원 이동 아래로 간다. 기준값은 원계열에서 다시 집는다(`compute_revision` 은 %만 준다).
    op_now = _as_num(row["op_krw"])
    base_op = None
    if base:
        for d, v in _series(hist, focus["period"], focus["period_type"], "op_krw"):
            if d == base["as_of"]:
                base_op = _as_num(v)
                break
    row["op_krw_base"] = base_op
    row[f"op_krw_{win}_delta"] = (op_now - base_op) if (op_now is not None and base_op is not None) else None
    row["rank_basis"] = _rank_basis(row.get(f"op_krw_{win}_pct"), base_op)

    # 갱신이 창 **안**에서 일어났을 때만 싣는다 — 기준일보다 앞선 갱신은 지금 보이는 %를
    # 만든 것이 아니라서, 그걸로 표시를 달면 엉뚱한 행을 의심하게 된다.
    upd = _sole_update(hist, focus["period"], focus["period_type"])
    row["sole_update"] = upd if (upd and base and upd["as_of"] > base["as_of"]) else None
    return row


async def build_revision_screen_payload(universe: str, window: str = "4w",
                                        period_type: str = "FY", format: str = "md") -> dict[str, Any]:
    """유니버스(「코스피 시총 상위 100」·이름 나열·「전체」) 전 종목의 컨센서스 리비전 표.
    DB 2콜(유니버스 1 + 이력 1) · DART 0콜."""
    import os
    from open_proxy_mcp.services.universe import list_universe

    raw = (universe or "").strip()
    win = (window or "4w").strip().lower()
    windows = dict(_REV_WINDOWS)
    if win not in windows:
        return {"tool": TOOL, "status": "invalid", "subject": raw,
                "warnings": [f"window '{window}' 없음 — {' / '.join(windows)} 중 선택."]}
    pt = (period_type or "FY").strip().upper()
    if pt not in ("FY", "Q", "ALL"):
        return {"tool": TOOL, "status": "invalid", "subject": raw,
                "warnings": [f"period_type '{period_type}' 없음 — FY / Q / all 중 선택."]}

    ul = await list_universe(raw)
    subject = f"{ul.label or raw} — 컨센서스 리비전"
    if ul.question:
        return {"tool": TOOL, "status": "invalid", "subject": subject, "warnings": [ul.question]}
    if not ul.db_ok:
        st = "db_error" if os.getenv("DATABASE_URL") else "db_unconfigured"
        return {"tool": TOOL, "status": st, "subject": subject,
                "warnings": ["유니버스를 만들 주간 시세 저장분을 읽지 못했다 — "
                             + ("일시 장애일 수 있다, 재시도할 것." if st == "db_error"
                                else "이 서버에는 저장분 DB 가 연결돼 있지 않다. 재시도해도 되지 않는다.")]}
    if not ul.resolved:
        return {"tool": TOOL, "status": "no_data", "subject": subject,
                "warnings": [ul.notice or "유니버스를 해석하지 못했다 — 표현을 바꿔 다시 부를 것."]}
    codes = [r["ticker"] for r in ul.rows]
    rows = await asyncio.to_thread(_fetch_hist_many, codes if codes else None, pt)
    if rows is None:
        return {"tool": TOOL, "status": "db_error", "subject": subject,
                "warnings": ["리비전 이력(`fwd_hist`) 조회 실패 — **장애**다, 자료 없음이 아니다. 재시도할 것."]}

    by_code: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_code.setdefault(r[0], []).append(dict(zip(_REV_SCREEN_COLS[1:], r[1:])))
    meta = {r["ticker"]: r for r in ul.rows}
    out: list[dict[str, Any]] = []
    for code, hist in by_code.items():
        row = _screen_row(code, hist, win, meta.get(code, {}))
        if row is not None:
            out.append(row)
    key = f"op_krw_{win}_pct"
    # 순위 → 분모 가드에 걸린 행 → 비교 불가, 각 묶음 안에서는 영업이익 변화율 내림차순.
    # `rank` 는 **순위 묶음에만** 매긴다 — 못 믿을 %에 등수를 달면 가드를 둔 뜻이 없다.
    out.sort(key=lambda r: (_REV_TIER[r["rank_basis"]], -(r[key] or 0.0), r.get("rank_mktcap") or 10**9))
    n_ranked = 0
    for r in out:
        if r["rank_basis"] == "ranked":
            n_ranked += 1
            r["rank"] = n_ranked
        else:
            r["rank"] = None

    guarded = [r for r in out if r["rank_basis"] in ("base_loss", "base_small")]
    sole = [r for r in out if r["sole_update"]]
    n_short = sum(1 for r in out if r["history_short"])
    n_nobase = sum(1 for r in out if r["baseline_as_of"] is None)
    vals = [r[key] for r in out if r[key] is not None]
    direction = {"up": sum(1 for v in vals if v > 0.5), "down": sum(1 for v in vals if v < -0.5),
                 "flat": sum(1 for v in vals if -0.5 <= v <= 0.5), "not_comparable": len(out) - len(vals)}
    latest_dates = sorted({r["as_of_latest"] for r in out})
    # 「비교 가능」= 영업이익 변화율이 실제로 나온 행. 기준일은 있어도 그때 그 기간 추정이 없었으면
    # (absent) 비교가 아니다 — direction.not_comparable 과 같은 정의여야 두 숫자가 어긋나지 않는다.
    coverage = {"universe": len(ul.rows), "with_estimates": len(out),
                "no_estimates": len(ul.rows) - len(out),
                "comparable": len(vals), "ranked": n_ranked,
                "rank_guarded": len(guarded), "history_short": n_short}

    warnings: list[str] = []
    if ul.notice:
        warnings.append(ul.notice)
    if ul.excluded_pref:
        warnings.append(f"우선주 {ul.excluded_pref}종목은 유니버스에서 뺐다 — 같은 회사의 보통주가 순위에 있다.")
    if len(ul.rows) - len(out):
        warnings.append(f"유니버스 {len(ul.rows)}종목 중 {len(ul.rows) - len(out)}종목은 컨센서스 추정이 없어 "
                        "표에서 뺐다(애널리스트 미커버 — 자료 없음이지 장애가 아니다).")
    if guarded:
        n_loss = sum(1 for r in guarded if r["rank_basis"] == "base_loss")
        n_small = len(guarded) - n_loss
        bits = []
        if n_loss:
            bits.append(f"기준 영업이익이 적자·0 인 {n_loss}종목")
        if n_small:
            bits.append(f"기준 영업이익이 {_REV_RANK_MIN_OP // 10**8}억원 미만인 {n_small}종목")
        warnings.append(
            " / ".join(bits) + "은 **순위에서 뺐다**(등수를 비우고 표 뒤에 따로 묶었다 — "
            "행마다 `rank_basis` 에 뺀 이유). "
            "%의 분모가 |기준값| 이라 기준이 0 에 가까우면 몇 억원 움직임도 수백 %가 되어 "
            "순위 양 끝을 덮는다. 지운 게 아니라 등수만 안 매긴 것이고, **적자 축소가 「상향」으로 "
            "나오는 것도 그래서다.** 크기는 변화율 말고 "
            f"이동 절대액(`op_krw_{win}_delta`)으로 볼 것. "
            f"{_REV_RANK_MIN_OP // 10**8}억원은 **임의 기준**이다 — 자연스러운 경계가 아니라 "
            "「이 아래에서 %가 폭주하더라」는 관측으로 고른 값이다.")
    if sole:
        warnings.append(
            f"{len(sole)}종목은 관측 구간 내내 값이 같다가 **이번 한 번만** 움직였다 — 이 창의 %는 "
            "주마다 쌓인 추세가 아니라 **벤더의 단발 갱신**이다. 상향·하향 양쪽에 다 있다. "
            "왜 한 번에 움직였는지는 이 도구가 알지 못한다(재편·커버리지 변화·단순 갱신 주기 — "
            "확인하려면 공시를 볼 것): "
            + " · ".join(f"{r['name']}({r['ticker']}) {r['sole_update']['as_of']} "
                         f"{r['sole_update']['op_krw_pct']:+.1f}%" for r in sole[:10])
            + (f" 외 {len(sole) - 10}종목" if len(sole) > 10 else ""))
    # 비교 불가는 이유가 둘이다 — 「기준일이 없다」와 「기준일엔 있었는데 그 기간 추정이 없었다」.
    # 뭉뚱그리면 합이 direction 의 비교 불가와 어긋나 보인다(260921: 19 = 17 + 2).
    n_nocomp = sum(1 for r in out if r["rank_basis"] == "not_comparable")
    if n_nocomp:
        why = []
        if n_nobase:
            why.append(f"{n_nobase}종목은 이력에 {_REV_MIN_GAP_DAYS}일 이상 떨어진 기준일이 없고")
        if n_nocomp - n_nobase:
            why.append(f"{n_nocomp - n_nobase}종목은 기준일에 그 기간 추정이 아직 없었다")
        warnings.append(f"비교 불가 {n_nocomp}종목 — " + " / ".join(why)
                        + ". 순위 밖이라 md 표에서 빼고 개수로만 남겼다(전체는 json `data.rows`).")
    if n_short:
        warnings.append(f"{n_short}종목은 이력이 {win} 에 못 미쳐 가장 오래된 스냅샷과 비교했다 — "
                        "「이력 짧음」 표시. 그 값은 정확한 " + win + " 변화가 아니다.")
    if len(latest_dates) > 1:
        warnings.append("종목별 최신 스냅샷 날짜가 다르다: " + " · ".join(latest_dates)
                        + " — 같은 날 기준이 아니니 순위를 정밀 비교로 읽지 말 것.")
    if ul.as_of:
        warnings.append(f"유니버스(시총 순위)는 주간 시세 저장분 {ul.as_of} 기준.")

    data: dict[str, Any] = {
        "scope": "revision_screen", "universe": raw, "label": ul.label,
        "window": win, "window_days": windows[win], "period_type": pt,
        "as_of_latest": latest_dates[-1] if latest_dates else None, "universe_as_of": ul.as_of,
        "focus": "종목마다 가장 가까운 연간 추정 기간(예: 2026.12E) 한 행. 기간별 전체는 종목 단위 "
                 "`forward_estimates_data(company=…, bundle=\"revision\")`.",
        "coverage": coverage, "direction": direction, "rows": out,
        "rank_guarded": [r["ticker"] for r in guarded],
        "sole_update": [r["ticker"] for r in sole],
        "note": ("%는 (지금−기준)/|기준|. 기준일은 목표일 이전 가장 가까운 주간 스냅샷. ±0.5% 안은 유지. "
                 "정렬은 영업이익 " + win + " 변화율 내림차순. "
                 "🔴 **등수(`rank`)는 `rank_basis` 가 \"ranked\" 인 행에만 매긴다** — 기준 영업이익이 적자·0 이거나 "
                 f"{_REV_RANK_MIN_OP // 10**8}억원 미만이면(`base_loss` · `base_small`) 분모가 작아 %가 폭주하므로 "
                 "순위에서 빼고 뒤에 따로 묶는다(지우지는 않는다). 크기는 `op_krw_" + win + "_delta`(이동 "
                 "절대액, 원)로 볼 것. `op_krw_base` 가 그 분모다. "
                 "`sole_update` 는 관측 구간 내내 같다가 이번 한 번만 움직인 행 — 누적 추세가 아니라 단발 "
                 "갱신이라는 **사실만** 말한다. 원인(재편·커버리지·갱신 주기)은 판정하지 않는다. "
                 "비교 불가(변화율 없음)는 순위 밖. "
                 "출처 `fwd_hist`(주 1회 토, 13주 롤링) — 그 너머는 없다. 컨센서스 스냅샷이지 DART 공시가 아니다."),
    }
    return {"tool": TOOL, "status": "ok" if out else "no_estimates", "subject": subject,
            "data": data, "warnings": warnings}
