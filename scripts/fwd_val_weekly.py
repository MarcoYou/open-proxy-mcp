"""선행(애널리스트 추정) 배수 — 시장·WICS 대분류·WICS 하위업종(중분류) 집계 `opm_val_fwd` (260918 신설).

왜: 선행 PER·PBR 은 수집 머신의 토요일 체인이 만드는 `fwd_agg` 에 시장·WICS 대분류까지만 있었고,
도구는 그중 선행 배당수익률만 서빙했다. 하위업종(28)은 집계 자체가 없었다. 수집 머신의 로컬 원본을
건드리지 않고 **운영 DB 에 이미 있는 것만으로** 만든다 —
추정치 이력 `fwd_hist`(주 1회, 13주 롤링) + WICS 분류 `wise_sector`(월 1회) + 시장 구분 `krx_weekly`.

왜 `fwd_agg` 에 넣지 않나:
- 토요일 체인의 `push_fwd_agg.py` 는 올릴 때 **그 날짜의 `fwd_agg` 행을 분류 구분 없이 전부 지우고** 다시 넣는다.
  하위업종을 같은 표에 넣으면 매주 지워진다.
- `fwd_agg` 는 보통주를 「숫자 6자리 + 끝자리 0」으로 가른다. 2024년부터 나오는 **영문 섞인 새 종목코드**
  (0126Z0 삼성에피스홀딩스 등)가 전부 「우선주·기타」로 빠진다(260913 실측 9종목).
  여기서는 끝자리만 본다 — 새 코드도 끝자리 0 이 보통주다.
- 트레일링 PER 은 **적자 회사까지 더한다**(`opm_val_market`). `fwd_agg` 는 흑자 추정만 더한다.
  나란히 놓으면 방식 차이가 기대이익 차이로 읽힌다. 그래서 두 벌을 둔다(아래).

한 종목 = 한 행: **가장 가까운 추정 사업연도**의 연간(FY) 추정. 연결/별도 중복은 값 있는 쪽 → 연결 → 기간표기 순
(`build_fwd_agg.py` 와 같은 순서). 자기자본 = 시총 × BPS ÷ 주가(= 주식수 × BPS) — 이력 표에 PBR 칸이 없다.

배수 두 벌:
- `fwd_per`·`fwd_pbr` — **트레일링과 같은 방식**. 추정이 있는 종목 전부(적자·자본잠식 추정 포함)를 더한다.
  합이 0 이하면 비운다(`ni_krw`·`eq_krw` 에 합을 남겨 「적자」와 「자료없음」을 가른다).
- `fwd_per_pos`·`fwd_pbr_pos` — 분모가 0 초과인 종목만(`fwd_agg` 방식, 벤더 관행). `--check` 가 이것으로 대조한다.
- `fwd_psr` 는 매출 > 0 종목만(한 벌). 선행 배당수익률은 `fwd_agg` 와 같다(분모 = 추정 DPS 가 있는 종목 시총).

모집단: 시장 행 = 추정이 있는 보통주 전부. 대분류·하위업종 행 = 그중 WICS 분류가 있는 종목
(분류 없는 종목은 시장 행에만 든다 — 260913 에 1종목).

시점 규칙(판단 시점 이후 정보를 쓰지 않는다):
- 업종 = 추정 날짜 **이하 가장 최근 WICS 스냅샷**(`class_dd`). 그보다 이른 스냅샷이 없으면 가장 이른 것(소급 —
  WICS 관측 시작 전 날짜만. 260918 결정 「과거 집계가 없으면 지금 분류로 백필」의 적용 범위). 260918 백필 9개 날짜는
  전부 0828 스냅샷 — 모든 추정 날짜와 같거나 앞서 소급이 없다.
  (처음엔 「계산 시점의 최신 스냅샷」이었다. 토요일 송출이 늦어 월초 WICS 갱신 뒤에 계산되면 추정 날짜보다 뒤의
  분류가 붙는다 — 독립 QA 가 짚었다, 260918.)
- 시장 구분 = 추정 날짜 이하 가장 최근 주간 시세(`mk_dd`)의 KS/KQ. 거기 없는 종목(그 주 뒤 상장)만 **그 뒤 첫 시세**
  — 가장 최근 시세가 아니라 가장 가까운 관측을 쓴다(이전상장으로 시장이 바뀐 뒤 값을 끌어오지 않게).
- 같은 날짜는 다시 계산해도 같은 값이 나온다. 그래서 이미 쓴 과거 날짜는 건너뛰고, **가장 최근 날짜**만 매번 다시
  계산한다(체인이 같은 날짜를 다시 올릴 수 있다). 전부 다시 쓰려면 `--recompute`.

실행 (DART·KRX 0콜, 수 초):
  python3 scripts/fwd_val_weekly.py                  # 새 날짜 + 가장 최근 날짜 (cron — market-val-weekly)
  python3 scripts/fwd_val_weekly.py --recompute      # fwd_hist 의 모든 날짜를 다시(날짜마다 그 시점 분류)
  python3 scripts/fwd_val_weekly.py --dry            # 계산만, 쓰지 않음 (모든 날짜)
  python3 scripts/fwd_val_weekly.py --dry --check    # 수집 머신 방식으로 다시 내서 fwd_agg 와 대조(검증)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

TABLE = "opm_val_fwd"

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
  '선행(애널리스트 추정) PER·PBR·PSR·배당수익률 — scheme market(bucket _ALL)·wics_sector·wics_industry(bucket=WICS 코드). '
  'scripts/fwd_val_weekly.py 가 fwd_hist + wise_sector(class_dd) + krx_weekly(mk_dd)로 만든다. '
  'fwd_per·fwd_pbr=적자 포함 합(트레일링과 같은 방식), *_pos=분모>0 종목만(fwd_agg 방식).';
"""

#: 집계 SQL. {extra} = 검증용 모집단 조건(수집 머신 흉내 — 숫자 코드만). 평소엔 빈 문자열.
AGG_SQL = """
WITH cls AS (
  SELECT ticker, sector_code, sector, industry_code, industry
  FROM wise_sector WHERE snap_dd = %(class_dd)s
), mk AS (
  -- 추정 날짜 이하 가장 최근 주간 시세(mk_dd)의 시장 구분. 거기 없는 종목(그 주 뒤 상장)만 그 뒤 첫 시세.
  SELECT DISTINCT ON (ticker) ticker, market FROM krx_weekly
  WHERE price_dd >= %(mk_dd)s
  ORDER BY ticker, price_dd
), e AS (
  SELECT h.stock_code, h.fiscal_year, h.mktcap_krw, h.ni_ctrl_krw, h.rev_krw, h.bps_krw, h.price_krw, h.dps_krw,
         row_number() OVER (PARTITION BY h.stock_code
                            ORDER BY h.fiscal_year, (h.ni_ctrl_krw IS NULL), (h.bps_krw IS NULL),
                                     (h.basis <> 'IFRS연결'), h.period) AS rn
  FROM fwd_hist h
  WHERE h.as_of = %(as_of)s AND h.is_estimate AND h.period_type = 'FY' AND h.mktcap_krw IS NOT NULL
    AND right(h.stock_code, 1) = '0' {extra}
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
  CROSS JOIN LATERAL (VALUES ('market', '_ALL', NULL, NULL),
                             ('wics_sector', p.sector_code, p.sector, NULL),
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

    WICS 분류(`wise_sector.snap_dd`)와 주간 시세(`krx_weekly.price_dd`) 둘 다 이 규칙으로 고른다 —
    도구의 산업 표가 기업의 WICS 소속을 고를 때와 같은 폴백이다.
    """
    if not dds:
        return None
    ordered = sorted(set(dds))
    before = [d for d in ordered if d <= as_of_dd]
    return before[-1] if before else ordered[0]


def finish_rows(as_of, class_dd: str, mk_dd: str, rows: list[dict]) -> list[dict]:
    """집계 결과에 날짜·분류 스냅샷·시세 날짜·배당 분모 표기를 붙인다(`fwd_agg` 와 같은 표기)."""
    out = []
    for r in rows:
        d = dict(r)
        d.update(as_of=as_of, class_dd=class_dd, mk_dd=mk_dd,
                 div_denom_basis="covered" if (d.get("n_dps") or 0) > 0 else None)
        out.append(d)
    return out


# ── 실행 ───────────────────────────────────────────────────────────────

def compute(con, as_of, class_dd: str, mk_dd: str, collector_like: bool = False) -> list[dict]:
    from psycopg.rows import dict_row

    extra = "AND h.stock_code ~ '^[0-9]{6}$'" if collector_like else ""
    with con.cursor(row_factory=dict_row) as cur:
        cur.execute(AGG_SQL.format(extra=extra), {"as_of": as_of, "class_dd": class_dd, "mk_dd": mk_dd})
        return cur.fetchall()


#: 대조 허용오차. PBR 만 느슨하다 — 수집 머신은 반올림된 종목 PBR 로 자본을 되돌리고(시총÷PBR),
#: 여기는 BPS 로 되돌린다(시총×BPS÷주가). 실측 차이 0.1% 안팎.
_TOL = {"fwd_per": 1e-6, "fwd_pbr": 5e-3, "fwd_psr": 1e-6, "fwd_div_yield_pct": 1e-6}


def check_against_collector(con, as_of, class_dd: str, mk_dd: str) -> int:
    """수집 머신 방식(숫자 코드만·분모>0만)으로 다시 내서 `fwd_agg` 와 대조한다. 어긋난 칸 수를 돌려준다.

    WICS 분류가 없는 종목은 `fwd_agg` 에서 「미분류」 칸으로, 여기서는 어느 칸에도 안 들어간다 — 대조에서 뺀다.
    """
    from psycopg.rows import dict_row

    mine = {}
    for r in compute(con, as_of, class_dd, mk_dd, collector_like=True):
        if r["scheme"] == "wics_industry":
            continue
        key = (r["market"], r["scheme"], "ALL" if r["scheme"] == "market" else r["label"])
        mine[key] = {"n_total": r["n_total"], "n_per": r["n_per_pos"], "fwd_per": r["fwd_per_pos"],
                     "fwd_pbr": r["fwd_pbr_pos"], "fwd_psr": r["fwd_psr"],
                     "fwd_div_yield_pct": r["fwd_div_yield_pct"]}
    with con.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT market, scheme, bucket, n_total, n_per, fwd_per, fwd_pbr, fwd_psr, fwd_div_yield_pct "
                    "FROM fwd_agg WHERE as_of = %s AND bucket NOT IN ('미분류', '(미분류)')", (as_of,))
        theirs = {(r["market"], r["scheme"], r["bucket"]): r for r in cur.fetchall()}
    if not any(k[1] == "wics_sector" for k in theirs):
        print(f"    대조 {as_of}: fwd_agg 에 대분류가 없는 날짜(수집 당시 분류 미부착) — 시장 행만 대조")
    bad, n = 0, 0
    for k in sorted(theirs):
        t, m = theirs[k], mine.get(k)
        n += 1
        if not m:
            print(f"    {k}: 이 방식에 없음")
            bad += 1
            continue
        diffs = [f"{c} {m[c]} vs {t[c]}" for c, tol in _TOL.items()
                 if (m[c] is None) != (t[c] is None)
                 or (m[c] is not None and abs(m[c] - t[c]) > tol * max(1.0, abs(t[c])))]
        if m["n_total"] != t["n_total"] or m["n_per"] != t["n_per"]:
            diffs.append(f"회사 수 {m['n_total']}/{m['n_per']} vs {t['n_total']}/{t['n_per']}")
        if diffs:
            bad += 1
            print(f"    {k}: " + " · ".join(diffs))
    print(f"    대조 {as_of}: 저장 {n}칸 중 어긋남 {bad}칸")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--recompute", action="store_true", help="fwd_hist 의 모든 날짜를 다시 계산(날짜마다 그 시점 분류)")
    ap.add_argument("--dry", action="store_true", help="계산만 하고 쓰지 않는다 (표도 안 만든다)")
    ap.add_argument("--check", action="store_true", help="수집 머신 방식으로 다시 내서 fwd_agg 와 대조")
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
        print("wise_sector 가 비었다 — WICS 분류 배치(wics-monthly)가 먼저 돌아야 한다.", file=sys.stderr)
        return 1
    hist_days = [r[0] for r in con.execute("SELECT DISTINCT as_of FROM fwd_hist ORDER BY 1").fetchall()]
    if not hist_days:
        print("fwd_hist 가 비었다 — 토요일 체인의 이력 송출이 먼저 돌아야 한다.", file=sys.stderr)
        return 1
    price_dds = [r[0] for r in con.execute("SELECT DISTINCT price_dd FROM krx_weekly").fetchall()]
    if not price_dds:
        print("krx_weekly 가 비었다 — 시장 구분을 붙일 수 없다.", file=sys.stderr)
        return 1
    done = set() if a.dry else {r[0] for r in con.execute(f"SELECT DISTINCT as_of FROM {TABLE}").fetchall()}
    days = pick_days(hist_days, done, a.recompute or a.dry)
    print(f"선행 배수 집계 · WICS 스냅샷 {len(class_dds)}개(최신 {max(class_dds)}) · 추정치 이력 {len(hist_days)}일"
          f" ({hist_days[0]} ~ {hist_days[-1]}) · 이번에 {len(days)}일" + (" · 쓰지 않음(--dry)" if a.dry else ""))
    total, bad = 0, 0
    for as_of in days:
        dd = as_of.strftime("%Y%m%d")
        class_dd = snapshot_at_or_before(dd, class_dds)
        mk_dd = snapshot_at_or_before(dd, price_dds)
        rows = finish_rows(as_of, class_dd, mk_dd, compute(con, as_of, class_dd, mk_dd))
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
        if a.check:
            bad += check_against_collector(con, as_of, class_dd, mk_dd)
    if not a.dry:
        n, d0, d1 = con.execute(f"SELECT count(*), min(as_of), max(as_of) FROM {TABLE}").fetchone()
        print(f"합계 {total}행 반영 · {TABLE} 현황 {n}행 · {d0} ~ {d1}")
    con.close()
    return 1 if (a.check and bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
