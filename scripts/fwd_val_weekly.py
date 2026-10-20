"""선행(애널리스트 추정) 배수 — 시장·업종 대분류·중분류 집계 `opm_val_fwd` (260918 신설).

왜: 선행 PER·PBR 은 수집 머신의 토요일 체인이 만들던 `fwd_agg` 에 시장·업종 대분류까지만 있었고,
도구는 그중 선행 배당수익률만 서빙했다. 하위업종(28)은 집계 자체가 없었다. 수집 머신의 로컬 원본을
건드리지 않고 **운영 DB 에 이미 있는 것만으로** 만든다 —
추정치 이력 `fwd_hist`(주 1회. DB 보관은 창 52주 — `open_proxy_mcp/fwd_window.py`)
+ 업종분류 스냅샷(월 1회) + 시장 구분 `krx_weekly`.
`fwd_agg` 는 재설계 5단계(2610)에서 은퇴했다 — 시장·업종 선행 배수를 저장하는 곳은 이 표 하나다.

- `fwd_hist` 에는 주식 종류 칸이 없다. 영문이 섞인 새 보통주 코드도 포함하되,
  종류가 미확인인 0161M0는 확인될 때까지 집계에서 제외한다.
- 트레일링 PER 은 **적자 회사까지 더한다**(`opm_val_market`). 옛 `fwd_agg`(벤더 관행)는 흑자 추정만 더했다.
  나란히 놓으면 방식 차이가 기대이익 차이로 읽힌다. 그래서 두 벌을 둔다(아래).

한 종목 = 한 행: **가장 가까운 추정 사업연도**의 연간(FY) 추정. 연결/별도 중복은 값 있는 쪽 → 연결 → 기간표기 순
(`build_fwd_agg.py` 와 같은 순서). 자기자본 = 시총 × BPS ÷ 주가(= 주식수 × BPS) — 이력 표에 PBR 칸이 없다.

배수 두 벌:
- `fwd_per`·`fwd_pbr` — **트레일링과 같은 방식**. 추정이 있는 종목 전부(적자·자본잠식 추정 포함)를 더한다.
  합이 0 이하면 비운다(`ni_krw`·`eq_krw` 에 합을 남겨 「적자」와 「자료없음」을 가른다).
- `fwd_per_pos`·`fwd_pbr_pos` — 분모가 0 초과인 종목만(벤더 관행, 옛 `fwd_agg` 와 같은 방식).
- `fwd_psr` 는 매출 > 0 종목만(한 벌). 선행 배당수익률 분모 = 추정 DPS 가 있는 종목 시총(옛 `fwd_agg` 와 같다).

모집단: 시장 행 = 추정이 있는 보통주 전부. 대분류·하위업종 행 = 그중 업종분류가 있는 종목.
분류 없는 종목(신규 상장·분류 누락 — 261017 에 4종목)은 대분류의 **「미분류」 버킷**(`_UNCLASSIFIED`, 시장별 + ALL 로
주 많아야 3행)에 든다 — 그래서 대분류 합이 시장 행과 같다. 은퇴한 `fwd_agg` 에 있던 버킷을 되살린 것이다(261018,
시총 집계 `krx_cap_agg` 와 같은 버킷 이름). 하위업종에는 두지 않는다(대분류의 미분류 한 줄과 같은 종목이다).
이미 쓴 날짜에 이 버킷이 없으면 정상 실행이 그 행만 채운다(그 날짜의 저장 분류·시세 날짜로 — 다른 행은 건드리지 않는다).

시점 규칙(판단 시점 이후 정보를 쓰지 않는다):
- 업종 = 추정 날짜 **이하 가장 최근 업종분류 스냅샷**(`class_dd`). 그보다 이른 스냅샷이 없으면 가장 이른 것(소급 —
  업종분류 관측 시작 전 날짜만. 260918 결정 「과거 집계가 없으면 지금 분류로 백필」의 적용 범위). 260918 백필 9개 날짜는
  전부 0828 스냅샷 — 모든 추정 날짜와 같거나 앞서 소급이 없다.
  (처음엔 「계산 시점의 최신 스냅샷」이었다. 토요일 송출이 늦어 월초 업종분류 갱신 뒤에 계산되면 추정 날짜보다 뒤의
  분류가 붙는다 — 독립 QA 가 짚었다, 260918.)
- 시장 구분 = 추정 날짜 이하 가장 최근 주간 시세(`mk_dd`)의 KS/KQ. 거기 없는 종목(그 주 뒤 상장)만 **그 뒤 첫 시세**
  — 가장 최근 시세가 아니라 가장 가까운 관측을 쓴다(이전상장으로 시장이 바뀐 뒤 값을 끌어오지 않게).
- 같은 날짜는 다시 계산해도 같은 값이 나온다. 그래서 이미 쓴 과거 날짜는 건너뛰고, **가장 최근 날짜**만 매번 다시
  계산한다(체인이 같은 날짜를 다시 올릴 수 있다). 전부 다시 쓰려면 `--recompute`.

보관 — **전체**(2610~). 원본 `fwd_hist` 는 DB 창(13주 → 구간형 저장 뒤 52주)만 남으므로 이 표가 선행 배수의
유일한 DB 이력이다.
- 원본 날짜 목록: 게시 달력 `fwd_asof` 의 창 안 날짜
  (창보다 이른 날짜는 판 일부가 보관본으로 옮겨졌을 수 있어 집계하지 않는다 — `fwd_window` 「창」).
- 지우는 것은 **원본 창 안에서 원본에 없는 날짜**뿐이다 — 창 = 원본의 가장 이른 날짜가 속한 ISO 주의 월요일부터.
  같은 ISO 주 재송출로 대체된 날짜(예: 월요일 보강분을 그 주 토요일분이 덮음)가 여기에 걸린다.
- 창보다 이른 날짜는 원본 창 밖으로 나갔을 뿐이라 남긴다. (260918~2610 에는 원본에 없는 날짜를 전부 지워
  이 표도 사실상 13주 롤링이었다.) 원본이 일부 주만 보여도 그보다 이른 집계는 지워지지 않는다.

실행 (DART·KRX 0콜, 수 초):
  python3 scripts/fwd_val_weekly.py                  # 새 날짜 + 가장 최근 날짜 (cron — market-val-weekly)
  python3 scripts/fwd_val_weekly.py --recompute      # fwd_hist 의 모든 날짜를 다시(날짜마다 그 시점 분류)
  python3 scripts/fwd_val_weekly.py --dry            # 계산만, 쓰지 않음 (모든 날짜)
  python3 scripts/fwd_val_weekly.py --dry --check    # 모든 날짜를 다시 내서 저장분과 대조(쓰지 않음) — 원본 구조를 바꾼 뒤 확인
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from open_proxy_mcp import fwd_window  # noqa: E402
from open_proxy_mcp.krx_daily import weekly_with_latest  # noqa: E402

#: 시장 구분 원천 — 닫힌 주 + 일별 최신 거래일(261018). 칸은 세 개만 읽는다.
WEEKLY_MARKET = weekly_with_latest("price_dd, ticker, market")

TABLE = "opm_val_fwd"
#: 분류 없는 종목의 대분류 버킷 — 시총 집계(`krx_cap_agg`)와 같은 이름·이름표.
UNCLASSIFIED = "_UNCLASSIFIED"
UNCLASSIFIED_LABEL = "미분류"

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  as_of             date NOT NULL,
  market            text NOT NULL,
  scheme            text NOT NULL,
  bucket            text NOT NULL,
  label             text,
  parent            text,
  n_total           integer,
  cap_krw           double precision,
  n_per             integer,
  ni_krw            double precision,
  fwd_per           double precision,
  n_per_pos         integer,
  ni_pos_krw        double precision,
  fwd_per_pos       double precision,
  n_pbr             integer,
  eq_krw            double precision,
  fwd_pbr           double precision,
  n_pbr_pos         integer,
  fwd_pbr_pos       double precision,
  n_psr             integer,
  rev_krw           double precision,
  fwd_psr           double precision,
  n_dps             integer,
  dps_total_krw     double precision,
  dps_cap_krw       double precision,
  fwd_div_yield_pct double precision,
  div_denom_basis   text,
  fy_main           integer,
  fy_min            integer,
  fy_max            integer,
  class_dd          text NOT NULL,
  mk_dd             text,
  computed_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (as_of, market, scheme, bucket)
);
COMMENT ON TABLE {TABLE} IS
  '선행(애널리스트 추정) PER·PBR·PSR·배당수익률 — scheme market(bucket _ALL)·wics_sector·wics_industry(bucket=업종 코드, '
  '대분류의 분류 없는 종목은 _UNCLASSIFIED). '
  'scripts/fwd_val_weekly.py 가 fwd_hist + wise_sector(class_dd) + 주간 시세(krx_weekly + 일별 최신일, mk_dd)로 만든다. '
  'fwd_per·fwd_pbr=적자 포함 합(트레일링과 같은 방식), *_pos=분모>0 종목만(벤더 관행). '
  '보관 전체 — fwd_hist 창 안에서 원본에 없는 날짜(같은 ISO 주 재송출)만 지운다.';
"""

#: 집계와 `--check` 재대조가 같은 SQL 을 쓴다 — 보통주·종류 미확인 제외 조건이 이 한 곳에 있다.
AGG_SQL = """
WITH cls AS (
  SELECT ticker, sector_code, sector, industry_code, industry
  FROM wise_sector WHERE snap_dd = %(class_dd)s
), mk AS (
  -- 추정 날짜 이하 가장 최근 주간 시세(mk_dd)의 시장 구분. 거기 없는 종목(그 주 뒤 상장)만 그 뒤 첫 시세.
  -- 261018: 원천 = 닫힌 주 + 일별 최신 거래일(krx_daily.weekly_with_latest) — 이번 주 신규 상장이 주가 닫힐 때까지 빠지지 않게.
  SELECT DISTINCT ON (ticker) ticker, market FROM """ + WEEKLY_MARKET + """ w
  WHERE price_dd >= %(mk_dd)s
  ORDER BY ticker, price_dd
), e AS (
  SELECT h.stock_code, h.fiscal_year, h.mktcap_krw, h.ni_ctrl_krw, h.rev_krw, h.bps_krw, h.price_krw, h.dps_krw,
         row_number() OVER (PARTITION BY h.stock_code
                            ORDER BY h.fiscal_year, (h.ni_ctrl_krw IS NULL), (h.bps_krw IS NULL),
                                     (h.basis <> 'IFRS연결'), h.period) AS rn
  FROM fwd_hist h
  WHERE h.as_of = %(as_of)s AND h.is_estimate AND h.period_type = 'FY' AND h.mktcap_krw IS NOT NULL
    AND right(h.stock_code, 1) = '0'
    AND h.stock_code <> '0161M0' -- 주식 종류 미확인: 끝자리만으로 보통주를 확정하지 않는다.
), p AS (
  SELECT mk.market, cls.sector_code, cls.sector, cls.industry_code, cls.industry,
         e.fiscal_year AS fy, e.mktcap_krw::float8 AS cap, e.ni_ctrl_krw::float8 AS ni, e.rev_krw::float8 AS rev,
         e.mktcap_krw::float8 * e.bps_krw / NULLIF(e.price_krw, 0) AS eq,
         e.dps_krw::float8 AS dps, e.price_krw::float8 AS price
  FROM e JOIN mk ON mk.ticker = e.stock_code LEFT JOIN cls ON cls.ticker = e.stock_code
  WHERE e.rn = 1
), x AS (
  SELECT v.scheme, m.market, v.bucket, v.label, v.parent, p.fy, p.cap, p.ni, p.rev, p.eq, p.dps, p.price
  FROM p
  -- 분류 없는 종목은 대분류의 「미분류」 버킷으로(대분류 합 = 시장 행). 하위업종에는 두지 않는다.
  CROSS JOIN LATERAL (VALUES ('market', '_ALL', NULL, NULL),
                             ('wics_sector', COALESCE(p.sector_code, '_UNCLASSIFIED'),
                                             CASE WHEN p.sector_code IS NULL THEN '미분류' ELSE p.sector END, NULL),
                             ('wics_industry', p.industry_code, p.industry, p.sector)
                     ) AS v(scheme, bucket, label, parent)
  CROSS JOIN LATERAL (VALUES (p.market), ('ALL')) AS m(market)
  WHERE v.bucket IS NOT NULL
)
SELECT scheme, market, bucket, max(label) AS label, max(parent) AS parent,
  count(*)                                              AS n_total,
  sum(cap)                                              AS cap_krw,
  count(*) FILTER (WHERE ni IS NOT NULL)                AS n_per,
  sum(ni)  FILTER (WHERE ni IS NOT NULL)                AS ni_krw,
  CASE WHEN sum(ni) FILTER (WHERE ni IS NOT NULL) > 0
       THEN sum(cap) FILTER (WHERE ni IS NOT NULL) / sum(ni) FILTER (WHERE ni IS NOT NULL) END AS fwd_per,
  count(*) FILTER (WHERE ni > 0)                        AS n_per_pos,
  sum(ni)  FILTER (WHERE ni > 0)                        AS ni_pos_krw,
  sum(cap) FILTER (WHERE ni > 0) / NULLIF(sum(ni) FILTER (WHERE ni > 0), 0)   AS fwd_per_pos,
  count(*) FILTER (WHERE eq IS NOT NULL)                AS n_pbr,
  sum(eq)  FILTER (WHERE eq IS NOT NULL)                AS eq_krw,
  CASE WHEN sum(eq) FILTER (WHERE eq IS NOT NULL) > 0
       THEN sum(cap) FILTER (WHERE eq IS NOT NULL) / sum(eq) FILTER (WHERE eq IS NOT NULL) END AS fwd_pbr,
  count(*) FILTER (WHERE eq > 0)                        AS n_pbr_pos,
  sum(cap) FILTER (WHERE eq > 0) / NULLIF(sum(eq) FILTER (WHERE eq > 0), 0)   AS fwd_pbr_pos,
  count(*) FILTER (WHERE rev > 0)                       AS n_psr,
  sum(rev) FILTER (WHERE rev > 0)                       AS rev_krw,
  sum(cap) FILTER (WHERE rev > 0) / NULLIF(sum(rev) FILTER (WHERE rev > 0), 0) AS fwd_psr,
  count(*) FILTER (WHERE dps IS NOT NULL)               AS n_dps,
  sum(dps * cap / NULLIF(price, 0)) FILTER (WHERE dps IS NOT NULL) AS dps_total_krw,
  sum(cap) FILTER (WHERE dps IS NOT NULL)               AS dps_cap_krw,
  100.0 * sum(dps * cap / NULLIF(price, 0)) FILTER (WHERE dps IS NOT NULL)
        / NULLIF(sum(cap) FILTER (WHERE dps IS NOT NULL), 0) AS fwd_div_yield_pct,
  mode() WITHIN GROUP (ORDER BY fy)                     AS fy_main,
  min(fy)                                               AS fy_min,
  max(fy)                                               AS fy_max
FROM x GROUP BY scheme, market, bucket ORDER BY scheme, market, bucket
"""

#: 표에 쓰는 칸 — 이름으로 넣는다(규칙 5).
COLS = ("as_of", "market", "scheme", "bucket", "label", "parent", "n_total", "cap_krw",
        "n_per", "ni_krw", "fwd_per", "n_per_pos", "ni_pos_krw", "fwd_per_pos",
        "n_pbr", "eq_krw", "fwd_pbr", "n_pbr_pos", "fwd_pbr_pos", "n_psr", "rev_krw", "fwd_psr",
        "n_dps", "dps_total_krw", "dps_cap_krw", "fwd_div_yield_pct", "div_denom_basis",
        "fy_main", "fy_min", "fy_max", "class_dd", "mk_dd")
_KEY = ("as_of", "market", "scheme", "bucket")

UPSERT = (f"INSERT INTO {TABLE} ({', '.join(COLS)}, computed_at) "
          f"VALUES ({', '.join(f'%({c})s' for c in COLS)}, now()) "
          f"ON CONFLICT ({', '.join(_KEY)}) DO UPDATE SET "
          + ", ".join(f"{c} = EXCLUDED.{c}" for c in COLS if c not in _KEY)
          + ", computed_at = now()")


# ── 순수 함수 (테스트 대상) ─────────────────────────────────────────────

def pick_days(hist_days: list, done_days: set, recompute: bool) -> list:
    """계산할 스냅샷 날짜 — 아직 안 쓴 날짜 + 가장 최근 날짜(체인이 같은 날짜를 다시 올릴 수 있다).

    이미 쓴 과거 날짜는 건드리지 않는다 — 분류가 바뀌어도 과거 행을 새 분류로 흔들지 않는다.
    """
    days = sorted(set(hist_days))
    if recompute or not days:
        return days
    return [d for d in days if d not in done_days or d == days[-1]]


def snapshot_at_or_before(as_of_dd: str, dds: list[str]) -> str | None:
    """추정 날짜 이하 가장 최근 스냅샷(YYYYMMDD). 그보다 이른 것이 없으면 가장 이른 것(소급).

    업종분류 스냅샷(`snap_dd`)과 주간 시세(`krx_weekly.price_dd`) 둘 다 이 규칙으로 고른다 —
    도구의 산업 표가 기업의 업종 소속을 고를 때와 같은 폴백이다.
    """
    if not dds:
        return None
    ordered = sorted(set(dds))
    before = [d for d in ordered if d <= as_of_dd]
    return before[-1] if before else ordered[0]


def finish_rows(as_of, class_dd: str, mk_dd: str, rows: list[dict]) -> list[dict]:
    """집계 결과에 날짜·분류 스냅샷·시세 날짜·배당 분모 표기를 붙인다(옛 `fwd_agg` 와 같은 표기)."""
    out = []
    for r in rows:
        d = dict(r)
        d.update(as_of=as_of, class_dd=class_dd, mk_dd=mk_dd,
                 div_denom_basis="covered" if (d.get("n_dps") or 0) > 0 else None)
        out.append(d)
    return out


def hist_window_start(hist_days: list):
    """원본 창의 시작 — 원본 `fwd_hist` 의 가장 이른 날짜가 속한 ISO 주의 월요일. 원본이 비면 None.

    주 단위로 여는 까닭: 가장 이른 원본 주에 재송출로 대체된 날짜(예: 금요일분을 토요일분이 덮음)가 남아 있으면
    그것도 창 안에서 지워야 한다."""
    if not hist_days:
        return None
    first = min(hist_days)
    return first - timedelta(days=first.weekday())


def prune_stale_days(con, hist_days: list) -> int:
    """원본 창 안에서 원본 `fwd_hist` 에 없는 날짜의 파생 행만 지운다 — 같은 ISO 주 재송출로 대체된 날짜 등.

    창보다 이른 날짜는 원본이 13주 롤링으로 내보냈을 뿐이라 **남긴다**(이 표가 선행 배수의 유일한 이력이다).
    원본이 비었으면 아무것도 지우지 않는다(호출하는 쪽도 원본이 비면 멈춘다)."""
    start = hist_window_start(hist_days)
    if start is None:
        return 0
    cur = con.execute(
        f"DELETE FROM {TABLE} WHERE as_of >= %s AND NOT (as_of = ANY(%s))",
        (start, hist_days),
    )
    return cur.rowcount


#: `--check` 가 맞춰 보는 칸. 이름·회사 수·연도는 정확히, 합·배수는 상대 1e-9 안(합산 순서에 따른 부동소수 차이만).
_CHECK_EXACT = ("label", "parent", "n_total", "n_per", "n_per_pos", "n_pbr", "n_pbr_pos", "n_psr", "n_dps",
                "fy_main", "fy_min", "fy_max")
_CHECK_FLOAT = ("cap_krw", "ni_krw", "fwd_per", "ni_pos_krw", "fwd_per_pos", "eq_krw", "fwd_pbr", "fwd_pbr_pos",
                "rev_krw", "fwd_psr", "dps_total_krw", "dps_cap_krw", "fwd_div_yield_pct")
_CHECK_REL_TOL = 1e-9


def _same_float(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= _CHECK_REL_TOL * max(1.0, abs(a), abs(b))


def diff_rows(stored: list[dict], fresh: list[dict], *, report_missing: bool = True) -> list[str]:
    """저장분과 다시 낸 집계를 (market, scheme, bucket) 으로 맞춰 어긋난 행을 한 줄씩 적는다.

    report_missing=False 면 「다시 내면 있는데 저장분에 없는 행」은 적지 않는다(옛 분류로 남은 일부 행만 볼 때)."""
    def key(r):
        return (r["market"], r["scheme"], r["bucket"])

    mine = {key(r): r for r in fresh}
    out = []
    for r in sorted(stored, key=key):
        m = mine.get(key(r))
        if m is None:
            out.append(f"{key(r)}: 저장돼 있으나 다시 내면 없다")
            continue
        diffs = [f"{c} {r.get(c)} → {m.get(c)}" for c in _CHECK_EXACT if r.get(c) != m.get(c)]
        diffs += [f"{c} {r.get(c)} → {m.get(c)}" for c in _CHECK_FLOAT if not _same_float(r.get(c), m.get(c))]
        if diffs:
            out.append(f"{key(r)}: " + " · ".join(diffs))
    if report_missing:
        have = {key(r) for r in stored}
        out += [f"{k}: 다시 내면 있으나 저장분에 없다" for k in sorted(mine) if k not in have]
    return out


# ── 실행 ───────────────────────────────────────────────────────────────

def compute(con, as_of, class_dd: str, mk_dd: str) -> list[dict]:
    from psycopg.rows import dict_row

    with con.cursor(row_factory=dict_row) as cur:
        cur.execute(AGG_SQL, {"as_of": as_of, "class_dd": class_dd, "mk_dd": mk_dd})
        return cur.fetchall()


STORED_SQL = (f"SELECT market, scheme, bucket, class_dd, mk_dd, {', '.join(_CHECK_EXACT + _CHECK_FLOAT)} "
              f"FROM {TABLE} WHERE as_of = %s")


def check_against_stored(con, as_of, class_dd: str, mk_dd: str, fresh: list[dict]) -> int:
    """저장된 `opm_val_fwd` 를 지금 원본(`fwd_hist`)으로 다시 낸 집계와 대조한다. 어긋난 행 수를 돌려준다.

    저장할 때 쓴 분류·시세 날짜(`class_dd`·`mk_dd`)로 다시 내므로 비교 대상은 원본 하나다 — 원본의 물리 구조를
    바꾼 뒤(2610 뷰 전환) 같은 집계가 나오는지, 원본이 조용히 바뀌지 않았는지를 본다.
    fresh = 이번 실행이 고른 분류·시세 날짜로 이미 낸 결과(저장분과 날짜 쌍이 같으면 다시 계산하지 않는다).
    (260918~2610 에는 수집 머신 집계 `fwd_agg` 와 대조했다 — 그 표는 은퇴했다.)"""
    from psycopg.rows import dict_row

    with con.cursor(row_factory=dict_row) as cur:
        cur.execute(STORED_SQL, (as_of,))
        stored = cur.fetchall()
    if not stored:
        print(f"    대조 {as_of}: 저장분 없음 — 건너뜀")
        return 0
    pairs = Counter((r["class_dd"], r["mk_dd"]) for r in stored)
    main_pair = pairs.most_common(1)[0][0]
    if main_pair != (class_dd, mk_dd):
        print(f"    대조 {as_of}: 저장분은 분류 {main_pair[0]}·시세 {main_pair[1]}, 지금 고르면 {class_dd}·{mk_dd}"
              " — 저장할 때의 날짜로 다시 내서 대조")
    lines: list[str] = []
    # 「미분류」 버킷(261018)이 생기기 전에 쓴 날짜는 그 버킷이 없다 — 원본이 바뀐 것이 아니라 버킷이 새로 생긴 것이라
    # 어긋남으로 세지 않는다(정상 실행이 그 행만 채운다 — `fill_unclassified`).
    unclassified_stored = any(r["bucket"] == UNCLASSIFIED for r in stored)
    for pair in sorted(pairs, key=lambda p: (p[0] or "", p[1] or "")):
        mine = fresh if pair == (class_dd, mk_dd) else compute(con, as_of, *pair)
        if not unclassified_stored:
            mine = [r for r in mine if r["bucket"] != UNCLASSIFIED]
        group = [r for r in stored if (r["class_dd"], r["mk_dd"]) == pair]
        lines += diff_rows(group, mine, report_missing=(pair == main_pair))
    for line in lines:
        print(f"    {line}")
    print(f"    대조 {as_of}: 저장 {len(stored)}행 중 어긋남 {len(lines)}건"
          + ("" if unclassified_stored else " · 미분류 버킷은 아직 저장 전(대조에서 뺌)"))
    return len(lines)


def load_hist_days(con) -> list:
    """원본 날짜 목록 — 게시 달력(`fwd_asof`)의 창 안 날짜. `open_proxy_mcp/fwd_window.py` 의 정의를 그대로 쓴다.

    창보다 이른 날짜는 판 일부가 보관본으로 옮겨졌을 수 있어 집계하면 틀린다. 뷰의 DISTINCT 는 이력 52주면 느리다."""
    rows = con.execute(fwd_window.DAYS_SQL_CALENDAR, (fwd_window.window_days(),)).fetchall()
    return [r[0] for r in rows]


def fill_unclassified(con, days: list, skip: set) -> int:
    """이미 쓴 날짜 중 「미분류」 버킷이 없는 날짜에 그 행만 넣는다. 넣은 행 수를 돌려준다.

    그 날짜를 저장할 때 쓴 분류·시세 날짜(가장 많은 쌍)로 다시 낸다 — 이미 쓴 다른 행은 건드리지 않는다
    (「이미 쓴 과거 날짜는 새 분류로 흔들지 않는다」와 같은 원칙). 분류 없는 종목이 없던 날은 넣을 것이 없다.
    days = 원본 창 안 날짜(창 밖은 원본이 없어 다시 낼 수 없다), skip = 이번 실행이 통째로 계산하는 날짜."""
    have = {r[0] for r in con.execute(
        f"SELECT DISTINCT as_of FROM {TABLE} WHERE scheme = 'wics_sector' AND bucket = %s", (UNCLASSIFIED,)
    ).fetchall()}
    added = 0
    for as_of in days:
        if as_of in skip or as_of in have:
            continue
        pair = con.execute(
            f"SELECT class_dd, mk_dd, count(*) FROM {TABLE} WHERE as_of = %s "
            "GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2 LIMIT 1", (as_of,)).fetchone()
        if not pair:
            continue
        class_dd, mk_dd = pair[0], pair[1]
        rows = [r for r in compute(con, as_of, class_dd, mk_dd) if r["bucket"] == UNCLASSIFIED]
        if not rows:
            continue
        with con.cursor() as cur:
            cur.executemany(UPSERT, finish_rows(as_of, class_dd, mk_dd, rows))
        added += len(rows)
    return added


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--recompute", action="store_true", help="fwd_hist 의 모든 날짜를 다시 계산(날짜마다 그 시점 분류)")
    ap.add_argument("--dry", action="store_true", help="계산만 하고 쓰지 않는다 (표도 안 만든다)")
    ap.add_argument("--check", action="store_true",
                    help="저장된 집계를 지금 원본으로 다시 낸 값과 대조(저장할 때의 분류·시세 날짜로, 쓰기 전에)")
    a = ap.parse_args(argv)
    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL 이 없다.", file=sys.stderr)
        return 1
    import psycopg

    con = psycopg.connect(url, connect_timeout=20)
    con.autocommit = True
    if not a.dry:
        con.execute(DDL)
        con.execute(f"GRANT SELECT ON {TABLE} TO opm_ro")
    class_dds = [r[0] for r in con.execute("SELECT DISTINCT snap_dd FROM wise_sector").fetchall()]
    if not class_dds:
        print("분류 스냅샷 표가 비었다 — 업종분류 배치(sector-class-monthly)가 먼저 돌아야 한다.", file=sys.stderr)
        return 1
    hist_days = load_hist_days(con)
    if not hist_days:
        print("fwd_hist 가 비었다 — 토요일 체인의 이력 송출이 먼저 돌아야 한다.", file=sys.stderr)
        return 1
    price_dds = [r[0] for r in con.execute(f"SELECT DISTINCT price_dd FROM {WEEKLY_MARKET} w").fetchall()]
    if not price_dds:
        print("krx_weekly 가 비었다 — 시장 구분을 붙일 수 없다.", file=sys.stderr)
        return 1
    if not a.dry:
        pruned = prune_stale_days(con, hist_days)
        if pruned:
            print(f"원본 창({hist_window_start(hist_days)}~) 안에서 원본에 없는 날짜의 파생 행 {pruned}개 삭제")
    done = set() if a.dry else {r[0] for r in con.execute(f"SELECT DISTINCT as_of FROM {TABLE}").fetchall()}
    days = pick_days(hist_days, done, a.recompute or a.dry)
    print(f"선행 배수 집계 · 업종분류 스냅샷 {len(class_dds)}개(최신 {max(class_dds)}) · 추정치 이력 {len(hist_days)}일"
          f" ({hist_days[0]} ~ {hist_days[-1]}"
          + f", 게시 달력 창 {fwd_window.hot_weeks()}주) · 이번에 {len(days)}일"
          + (" · 쓰지 않음(--dry)" if a.dry else ""))
    total, bad = 0, 0
    for as_of in days:
        dd = as_of.strftime("%Y%m%d")
        class_dd = snapshot_at_or_before(dd, class_dds)
        mk_dd = snapshot_at_or_before(dd, price_dds)
        raw = compute(con, as_of, class_dd, mk_dd)
        if a.check:
            bad += check_against_stored(con, as_of, class_dd, mk_dd, raw)
        rows = finish_rows(as_of, class_dd, mk_dd, raw)
        if not a.dry:
            with con.cursor() as cur:
                cur.executemany(UPSERT, rows)
        by = {s: sum(1 for r in rows if r["scheme"] == s and r["market"] != "ALL")
              for s in ("wics_sector", "wics_industry")}
        allm = {r["market"]: r for r in rows if r["scheme"] == "market"}
        ks, kq = allm.get("KS") or {}, allm.get("KQ") or {}
        print(f"  {as_of}: {len(rows)}행 · 대분류 {by['wics_sector']}칸 · 하위업종 {by['wics_industry']}칸"
              f" · 종목 코스피 {ks.get('n_total')} / 코스닥 {kq.get('n_total')}"
              f" · 선행 PER 코스피 {ks.get('fwd_per') or 0:.2f} / 코스닥 {kq.get('fwd_per') or 0:.2f}"
              f" · 분류 {class_dd}{'(소급)' if class_dd > dd else ''} · 시장 구분 {mk_dd}")
        total += len(rows)
    if not a.dry:
        filled = fill_unclassified(con, hist_days, set(days))
        if filled:
            print(f"「미분류」 버킷이 없던 날짜에 그 행만 {filled}개 채움(저장 때의 분류·시세 날짜로)")
            total += filled
        n, d0, d1 = con.execute(f"SELECT count(*), min(as_of), max(as_of) FROM {TABLE}").fetchone()
        print(f"합계 {total}행 반영 · {TABLE} 현황 {n}행 · {d0} ~ {d1}")
    con.close()
    return 1 if (a.check and bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
