"""forward_estimates — 컨센서스 포워드 추정치·실적(Supabase `fwd`) 조회 서비스.

무엇: 종목 하나의 연도별 **추정(E)·실적(A)** 을 한 판에 담아 낸다. 저장층은 `fwd` 뷰 하나뿐이고
      **가르는 것은 응답에서만** 한다 — `reported`(벤더가 말한 것) / `derived`(우리가 계산한 것).

왜 실적/추정으로 안 가르나: 성장률이 그 경계를 넘나든다. 추정 행의 전기(前期)가 실적 행인 경우가
      2,180행이라, 실적/추정으로 뷰를 가르면 `eps_growth_pct` 가 뷰 경계를 넘어 LLM 이 두 번
      호출해 조인해야 한다. 반면 원천/파생은 **신뢰 등급·갱신 주기·틀렸을 때의 책임**이 다르다.
      (판정 260830 `verdict.md` 1장)

★ 자(尺)를 두 겹으로 붙인다 — 봉투(`ruler`)에 한 번, 줄(`row.basis`)마다 또.
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

_BUNDLES = ("core", "growth", "quality", "keys")

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
            # 지배순이익이 없어 하우스 정의를 못 쓴다 → 벤더식을 쓰되 **자를 바꿔 적는다.**
            r["per"] = round(vendor_per, 2)
            r["per_basis"] = (_PER_DEF_FALLBACK + at
                              + " (지배순이익 결측 폴백 — 위 행들과 자가 다르다)")
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
    return want, bad


async def build_forward_estimates_payload(
    company: str = "", bundle: str = "core", period_type: str = "FY",
    actual_years: int = 2, format: str = "md",
) -> dict[str, Any]:
    """`fwd` 스냅샷 한 종목. status: ok / no_estimates / not_found / unlisted /
    ambiguous / db_error / invalid — **「없음」을 뭉뚱그리지 않는다.**"""
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

    corp, early = await _resolve_listed(query)   # 공용 리졸버 — company 툴과 동일 진입
    if early:
        early["tool"] = TOOL
        return early
    if not corp or not corp.get("stock_code"):
        return {"tool": TOOL, "status": "not_found" if not corp else "unlisted",
                "subject": query,
                "warnings": [f"'{query}' 상장 종목을 찾지 못함 — 회사명 오탈자이거나 비상장. "
                             "우선주는 보통주 코드로 조회하세요. 회사 식별은 `company` 도구."]}
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
    if "growth" not in bundles or "quality" not in bundles or "keys" not in bundles:
        data["more"] = ("더 필요하면 bundle 을 넓히세요 — "
                        "growth(성장률·전기값·PEG) · quality(수익성·재무비율) · "
                        "keys(내부키·회계연도 칸) · all(전부). "
                        "기본 core 는 크기를 줄이려고 자른 것이지 그것이 정답이라서가 아니다.")
    return {"tool": TOOL, "status": "ok" if est_rows else "no_estimates",
            "subject": subject, "data": data, "warnings": warnings}
