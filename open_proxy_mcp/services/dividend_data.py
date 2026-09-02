"""dividend_data — 확정 배당 원장(`div_declared`·`div_quarterly`) 조회 서비스.

무엇 — DART 정기보고서 `alotMatter` 를 코스피 828사 × 사업연도 2020~2025 × 보고서 4종으로
       전수 수집해 만든 표를 읽는다. **여기서 DART 를 호출하지 않는다.**

왜 `dividend` 와 따로 두나 — `dividend` 는 회사 하나를 깊게 보는 도구다(실시간 DART 호출,
       결정공시 fallback, 정책 신호). 이쪽은 **여럿을 가로로** 본다 — 시계열과 전수 스크리닝.
       같은 표를 쓰지 않으므로 둘의 값이 어긋날 수 있고, 어긋나면 그것이 검산 재료다.
       🔴 **두 소스를 합치지 않는다**(마스터 확정 2026-09-02) — 합치면 검산 수단이 사라진다.

🔴 자(尺) — 이 표의 모든 숫자에 붙는 기준
  - 단위: 금액은 **원(KRW)**, DPS 는 주당 원, 배당성향은 %.
  - 기간: **사업연도**(`bsns_year`). 12월 결산이 아니면 `stlm_dt` 가 실제 결산일이다.
  - 출처: 정기보고서 `alotMatter` — **확정치**다. 추정도, 결정공시 예고도 아니다.
  - 빈칸: `확정`/`무배당`/`항목없음`/`보고서없음` 넷을 가른다. **0 으로 메우지 않는다.**
  - 분기: 누적 차분(3분기 누계 − 반기 누계). 앞 원장이 없으면 `미산출`.
    🔴 `anomaly='음수차분'` 은 버리지 않고 남긴다 — 분기 발표 뒤 결산에서 배당이 하향된
    사례가 실재한다(계룡건설 2023: 주당 500→400원). 그 행은 「누적」 전제가 깨진 것이다.

🔴 못 하는 것 — **보통/우선 총액 배분은 내지 않는다.** `alotMatter` 에 종류별 발행주식수가
   없어 `DPS × 주식수` 로 만든 배분값은 신고총액과 57.2% 만 5% 이내로 맞았다(2026-09-02 검산).
   종류별 DPS 는 원문 그대로 내고, **총액은 신고총액 하나만** 낸다.
"""
from __future__ import annotations

import logging
from typing import Any

from open_proxy_mcp.db import pg_rows

logger = logging.getLogger(__name__)

# 보고서 코드 → 사람이 읽는 이름. 표에 `reprt_label` 이 있으나 정렬은 코드로 한다.
_REPRT_ORDER = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
_ANNUAL = "11011"          # 사업보고서 — 연간 확정치는 여기만 본다
# 🔴 `stock_kind` 실제 값은 `보통`·`우선`·`미구분`·`해당없음` 넷이다(`보통주` 가 아니다).
#    「미구분」은 종류주식이 없는 회사가 종류 칸을 `-` 로 낸 것이라 **보통주로 취급한다** —
#    빼면 3,209행이 통째로 사라진다(첫 구현이 그래서 모집단 0사를 냈다).
_COMMON = ("보통", "미구분")


def _rows(sql: str, params: tuple = ()) -> list[tuple] | None:
    try:
        return pg_rows(sql, params)
    except Exception as exc:                      # pragma: no cover - DB 장애 경로
        logger.warning("dividend_data query failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────── 회사 시계열 ──
def firm_history(corp_code: str, year_from: int, year_to: int) -> dict[str, Any]:
    """회사 하나의 사업연도별 확정 배당 — 연간(사업보고서) + 분기(누적차분).

    연간 행은 주식 종류별로 여러 줄이 나온다(보통주·우선주·종류 미구분). 합치지 않고
    **종류를 붙여 그대로** 낸다 — `stock_knd` 표기가 50종 넘게 갈라져 있어(제1우선주·
    상환전환우선주·앞뒤 공백…) 우리가 묶으면 원문이 사라진다.
    """
    ann = _rows(
        """
        SELECT bsns_year, stock_kind, stock_knd_raw, dps_krw, div_total_krw,
               payout_pct, status, rcept_no, stlm_dt
          FROM div_declared
         WHERE corp_code = %s AND reprt_code = %s
           AND bsns_year BETWEEN %s AND %s
         ORDER BY bsns_year DESC, stock_kind, stock_knd_raw
        """,
        (corp_code, _ANNUAL, year_from, year_to),
    )
    qtr = _rows(
        """
        SELECT bsns_year, reprt_code, q_label, cum_div_total_krw, quarterly_div_krw,
               anomaly, status, rcept_no
          FROM div_quarterly
         WHERE corp_code = %s AND bsns_year BETWEEN %s AND %s
         ORDER BY bsns_year DESC,
                  CASE reprt_code WHEN '11013' THEN 1 WHEN '11012' THEN 2
                                  WHEN '11014' THEN 3 WHEN '11011' THEN 4 ELSE 9 END
        """,
        (corp_code, year_from, year_to),
    )
    if ann is None and qtr is None:
        return {"status": "db_error"}

    # 🔴 **빈 종류 행을 「무배당」으로 보이게 두지 않는다.** DART 서식은 종류주식 칸을
    # 회사가 안 써도 줄 수를 맞춰 내보낸다 — 셀트리온 FY2025 는 「주당 현금배당금」이
    # 두 줄이고 둘째가 `-` 다(우선주 자리인데 그 회사엔 우선주가 없다). 그 줄을 그대로
    # 렌더하면 「같은 해에 배당했는데 무배당」으로 읽힌다. 같은 연도에 확정 행이 있고
    # 종류 표기가 같은데 값이 빈 줄은 **서식이 남긴 빈칸**이므로 접고, 몇 줄을 접었는지
    # 남긴다 — 숨기는 것이 아니라 설명한다.
    confirmed_years = {r[0] for r in (ann or []) if r[6] == "확정"}
    annual_rows, folded = [], 0
    for r in (ann or []):
        if r[6] != "확정" and r[3] is None and r[0] in confirmed_years:
            folded += 1
            continue
        annual_rows.append(r)
    ann = annual_rows

    return {
        "status": "ok",
        "empty_kind_rows_folded": folded,
        "annual": [
            {"bsns_year": r[0], "stock_kind": r[1], "stock_knd_raw": r[2],
             "dps_krw": r[3], "div_total_krw": r[4], "payout_pct": r[5],
             "row_status": r[6], "rcept_no": r[7], "stlm_dt": r[8]}
            for r in (ann or [])
        ],
        "quarterly": [
            {"bsns_year": r[0], "reprt_code": r[1], "q_label": r[2],
             "cum_div_total_krw": r[3], "quarterly_div_krw": r[4],
             "anomaly": r[5], "row_status": r[6], "rcept_no": r[7]}
            for r in (qtr or [])
        ],
    }


# ───────────────────────────────────────────────────────── 시장·섹터 시계열 ──
def aggregate_history(scope: str, key: str, year_from: int, year_to: int) -> dict[str, Any]:
    """시장(KOSPI) 또는 WICS 섹터의 사업연도별 배당 집계.

    🔴 **분모를 두 벌 낸다** — `n_universe`(그 해 표에 있는 회사 전부)와 `n_payers`(실제 배당).
    한 벌만 내면 「배당이 줄었다」와 「배당하는 회사가 줄었다」가 구별되지 않는다.
    🔴 빈 버킷을 0 으로 메우지 않는다 — 「배당 0」과 「잴 회사가 없다」는 다르다.
    """
    # 섹터는 `wise_sector` 최신 스냅샷으로 붙인다. 종목 하나가 여러 티커(우선주)를 갖는
    # 경우가 있어 `div_declared.tickers` 로 조인하되 corp_code 로 중복을 없앤다.
    if scope == "sector":
        sql = """
        WITH snap AS (SELECT MAX(snap_dd) AS d FROM wise_sector),
        base AS (
          SELECT DISTINCT ON (d.corp_code, d.bsns_year)
                 d.corp_code, d.bsns_year, d.div_total_krw, d.payout_pct, d.status
            FROM div_declared d
            JOIN wise_sector w ON w.ticker = d.tickers AND w.snap_dd = (SELECT d FROM snap)
           WHERE d.reprt_code = %s AND d.bsns_year BETWEEN %s AND %s
             AND d.stock_kind IN ('보통','미구분') AND w.sector = %s
           ORDER BY d.corp_code, d.bsns_year,
                    (d.status = '확정') DESC, d.div_total_krw DESC NULLS LAST
        )
        SELECT bsns_year, COUNT(*) AS n_universe,
               COUNT(*) FILTER (WHERE status = '확정' AND div_total_krw > 0) AS n_payers,
               SUM(div_total_krw) FILTER (WHERE status = '확정') AS div_total_krw,
               AVG(payout_pct) FILTER (WHERE status = '확정' AND payout_pct IS NOT NULL) AS payout_avg
          FROM base GROUP BY bsns_year ORDER BY bsns_year DESC
        """
        params: tuple = (_ANNUAL, year_from, year_to, key)
    else:
        sql = """
        WITH base AS (
          SELECT DISTINCT ON (corp_code, bsns_year)
                 corp_code, bsns_year, div_total_krw, payout_pct, status
            FROM div_declared
           WHERE reprt_code = %s AND bsns_year BETWEEN %s AND %s AND stock_kind IN ('보통','미구분')
           ORDER BY corp_code, bsns_year,
                    (status = '확정') DESC, div_total_krw DESC NULLS LAST
        )
        SELECT bsns_year, COUNT(*) AS n_universe,
               COUNT(*) FILTER (WHERE status = '확정' AND div_total_krw > 0) AS n_payers,
               SUM(div_total_krw) FILTER (WHERE status = '확정') AS div_total_krw,
               AVG(payout_pct) FILTER (WHERE status = '확정' AND payout_pct IS NOT NULL) AS payout_avg
          FROM base GROUP BY bsns_year ORDER BY bsns_year DESC
        """
        params = (_ANNUAL, year_from, year_to)
    rows = _rows(sql, params)
    if rows is None:
        return {"status": "db_error"}
    return {
        "status": "ok",
        "rows": [
            {"bsns_year": r[0], "n_universe": r[1], "n_payers": r[2],
             "div_total_krw": r[3], "payout_avg": r[4]}
            for r in rows
        ],
    }


def sector_list() -> list[str]:
    rows = _rows("SELECT DISTINCT sector FROM wise_sector WHERE sector IS NOT NULL ORDER BY 1")
    return [r[0] for r in (rows or [])]


# ─────────────────────────────────────────────────────────────────── 스크리너 ──
def screen(
    bsns_year: int,
    min_payout: float | None = None,
    max_payout: float | None = None,
    min_dps: float | None = None,
    quarterly_only: bool = False,
    sector: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """한 사업연도에서 조건으로 회사를 거른다 — 보통주 기준.

    `quarterly_only` — **그 해에 실제로 두 번 이상 배당한 회사**. 판정은 분기 원장에서
    `quarterly_div_krw > 0` 인 분기가 2개 이상인지로 한다.

    🔴 260902 에 갈아엎었다. 종전 판정은 「4칸이 모두 확정인가」였는데 그건 **데이터가
    채워졌나**를 보는 조건이지 **분기배당을 하나**를 보는 조건이 아니다. 두 방향으로 틀렸다:
      · 계룡건설(013580) — 연 1회 배당인데 1분기 보고서에 전기 배당액이 실려 4칸이 차서 들어왔다
      · KB금융(105560) FY2025 — 실제 분기배당인데 H1·Q3 원장이 비어 빠졌다

    두 번째 것은 「분기배당이 아니다」가 아니라 **모른다**이다. 그래서 결과에서 빼되
    `n_unknown` 으로 몇 곳이 판단불가인지 함께 낸다 — 없다고 말하지 않는다.
    """
    # `보통`·`미구분`만. 260902 4분류 뒤로 `종류`(상환·전환·무의결권·트래킹스톡)는 여기서
    #   자동으로 빠진다 — 종전엔 그것들이 `우선` 통에 섞여 있었다.
    # `dps_krw > 0` — 표 머리에 「무배당 제외」라고 써 놓고 DPS 0원 회사를 넣고 있었다
    #   (씨티알모빌리티·DB, U7 실측). 원문에 0 이 적혀 있어도 그건 배당한 것이 아니다.
    where = ["d.reprt_code = %s", "d.bsns_year = %s", "d.stock_kind IN ('보통','미구분')",
             "d.status = '확정'", "d.dps_krw > 0"]
    params: list[Any] = [_ANNUAL, bsns_year]
    if min_payout is not None:
        where.append("d.payout_pct >= %s"); params.append(min_payout)
    if max_payout is not None:
        where.append("d.payout_pct <= %s"); params.append(max_payout)
    if min_dps is not None:
        where.append("d.dps_krw >= %s"); params.append(min_dps)

    join = ""
    if sector:
        join = ("JOIN wise_sector w ON w.ticker = d.tickers "
                "AND w.snap_dd = (SELECT MAX(snap_dd) FROM wise_sector)")
        where.append("w.sector = %s"); params.append(sector)
    _PAID_TWICE = (
        "(SELECT COUNT(*) FROM div_quarterly q "
        " WHERE q.corp_code = d.corp_code AND q.bsns_year = d.bsns_year "
        "   AND q.status = '확정' AND q.quarterly_div_krw > 0) >= 2")
    _LEDGER_FULL = (
        "(SELECT COUNT(*) FROM div_quarterly q "
        " WHERE q.corp_code = d.corp_code AND q.bsns_year = d.bsns_year "
        "   AND q.status = '확정') = 4")
    if quarterly_only:
        where.append(_PAID_TWICE)

    sql = f"""
        SELECT DISTINCT ON (d.corp_code)
               d.corp_code, d.name, d.tickers, d.dps_krw, d.div_total_krw,
               d.payout_pct, d.rcept_no
          FROM div_declared d {join}
         WHERE {' AND '.join(where)}
         ORDER BY d.corp_code, d.div_total_krw DESC NULLS LAST
    """
    rows = _rows(f"SELECT * FROM ({sql}) t ORDER BY t.div_total_krw DESC NULLS LAST LIMIT %s",
                 tuple(params) + (limit,))
    if rows is None:
        return {"status": "db_error"}
    # 조건에 걸린 전체 수. `limit` 은 **표시 한도**일 뿐인데 종전엔 이 값이 없어서
    #   돌려준 행 수를 매칭 수로 읽었다(U7: 「100사」가 실은 121사였다).
    m = _rows(f"SELECT COUNT(*) FROM ({sql}) t", tuple(params))
    matched = (m or [(None,)])[0][0]
    # 분모 — 조건을 걸기 전 그 해 모집단. 「몇 중 몇」이 없으면 결과를 못 읽는다.
    tot = _rows(
        "SELECT COUNT(DISTINCT corp_code) FROM div_declared "
        "WHERE reprt_code = %s AND bsns_year = %s AND stock_kind IN ('보통','미구분')",
        (_ANNUAL, bsns_year))
    # 판단불가 — 분기 원장이 4칸을 못 채운 배당사. 「분기배당이 아니다」가 아니라 「모른다」다.
    n_unknown = None
    if quarterly_only:
        u_where = [w for w in where if w != _PAID_TWICE] + [f"NOT {_LEDGER_FULL}"]
        u = _rows(
            f"SELECT COUNT(DISTINCT d.corp_code) FROM div_declared d {join} "
            f"WHERE {' AND '.join(u_where)}", tuple(params))
        n_unknown = (u or [(None,)])[0][0]
    return {
        "status": "ok",
        "n_universe": (tot or [(None,)])[0][0],
        "matched": matched,
        "returned": len(rows),
        "limit": limit,
        "n_unknown": n_unknown,
        "rows": [
            {"corp_code": r[0], "name": r[1], "ticker": r[2], "dps_krw": r[3],
             "div_total_krw": r[4], "payout_pct": r[5], "rcept_no": r[6]}
            for r in rows
        ],
    }


def year_range() -> tuple[int, int] | None:
    rows = _rows("SELECT MIN(bsns_year), MAX(bsns_year) FROM div_declared")
    if not rows or rows[0][0] is None:
        return None
    return rows[0][0], rows[0][1]
