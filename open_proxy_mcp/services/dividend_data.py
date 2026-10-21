"""dividend_data — 확정 배당 원장(`div_declared`·`div_quarterly`) + 결정공시 집계(`div_payment`) 조회 서비스.

무엇 — 두 갈래를 각자 그대로 읽는다. **여기서 DART 를 호출하지 않는다.**
  · 원장 — DART 정기보고서 `alotMatter` 를 코스피 약 830사 × 사업연도 2020~ × 보고서 4종으로
    전수 수집(매주 갱신 — 법정 마감이 지난 보고서만). **얼마·배당성향**(금액)의 유일한 출처.
    🔴 올해처럼 사업보고서(`11011`)가 아직 없는 해는 1분기·반기 등 분기 보고서 행만 있다 — 연간 확정치를
    쓰는 화면(screen 기본 연도)은 `latest_annual_year()` 를 본다.
  · 결정공시 — 「현금ㆍ현물배당결정」 원문을 배당 한 번 = 한 행으로 접은 것(`div_payment`,
    온전한 해는 `div_payment_scope.is_complete` — 수집 기준일이 그 해 결산 결의 마감을 넘으면 켜진다). **몇 번 배당했나**(횟수)의 유일한 출처. 실측(260903)으로 확인됨 —
    원장 기반 분기배당 판정(구 `quarterly_only`)은 FY2024 「2회 이상」을 20사로 냈는데
    실제는 84사다(누락 64사, 오탐 0). 원장의 분기 칸은 회사 절반이 비워 두므로 「안 했다」와
    「모른다」를 못 가른다 — 결정공시는 실제로 접수된 건이므로 그 구멍이 없다.

왜 `dividend_disclosure` 와 따로 두나 — 그쪽은 회사 하나를 깊게 보는 도구다(실시간 DART 호출,
       미확정 최신분, 정책 신호). 이쪽은 **여럿을 가로로** 본다 — 시계열과 전수 스크리닝.
       같은 표를 쓰지 않으므로 둘의 값이 어긋날 수 있고, 어긋나면 그것이 검산 재료다.
       🔴 **두 소스를 합치지 않는다**(마스터 확정 2026-09-02) — 합치면 검산 수단이 사라진다.
       같은 원칙으로 원장(금액)과 결정공시(횟수)도 한 표에 합치지 않는다 — 나란히만 낸다.

🔴 이 표의 숫자를 읽는 기준
  - 단위: 금액은 **원(KRW)**, DPS 는 주당 원, 배당성향은 %.
  - 기간: **사업연도**(`bsns_year`/`fiscal_year`). 12월 결산이 아니면 `stlm_dt`/`acc_mt` 가
    실제 결산월이다(코스피 배당사 632/639 가 12월 — 3·6·9·11월 결산 7사는 예외).
  - 🔴 **반기·분기 결산 회사(리츠 등)는 한 사업연도에 결산기 행이 여럿이다**(`stlm_dt` 가 갈린다 — 롯데리츠 FY2025 = 2025-06-30 117원 · 2025-12-31 123원).
    회사·사업연도의 DPS 는 **그 해 결산기 DPS 의 합**(240원)이다 — 종전 집계는 `DISTINCT ON (회사, 사업연도)` 로 임의의 한 결산기만 읽거나(`screen` 은 총액이 같은
    행 가운데 임의 DPS) 결산기 행을 그대로 늘어놓았다. 12월 결산 회사처럼 결산기가 하나면 값은 그대로다. (261021)
  - 🔴 **총액 칸은 결산기 행마다 그 결산기의 총액이다**(롯데리츠 2025-06-30 행 338.09억 · 2025-12-31 행 355.43억). 261021 전에는 수집기가 보고서마다 가장 늦은 결산기의 총액을
    모든 결산기 행에 복사해 뒀다(롯데리츠 2025-06-30 행 355.43억) — 그 시절 값은 운영에서 다시 썼고, 수집기도 고쳤다. 합치기 전에 한 번 더 확인한다:
    한 사업연도 안 결산기 행의 총액이 모두 같은데 **DPS 가 서로 다르면** 복사 꼴로 보고(`total_basis="ledger_copy"`) 더하지 않고 그 값 하나를 낸다(실제 연 총액은 이보다 크다 — 경고로 알린다).
    총액이 서로 다르거나, 총액도 DPS 도 같으면(주식 수가 그대로인 리츠가 두 결산기에 같은 DPS 를 준 해 — 제이알글로벌리츠 FY2021·FY2024·FY2025) 더한다(`total_basis="sum"`).
  - 출처: 정기보고서 `alotMatter`(원장) — **확정치**. 결정공시(`div_payment`)는 이사회
    결의 시점 원문 — **결의된 그대로**이지 사업보고서로 재확인된 것은 아니다.
  - 빈칸: `확정`/`무배당`/`항목없음`/`보고서없음` 넷을 가른다. **0 으로 메우지 않는다.**
  - 자유서술 칸: **통째로 낸다.** 결정공시 비고(11번 「기타 투자판단과 관련한 중요사항」)는
    자리가 정해진 칸이 아니라 회사가 무엇이든 적는 칸이다 — 정규식으로 한 가지를 뽑으면
    나머지가 사라진다. 읽는 쪽이 LLM 이므로 원문을 넘기고 판단은 읽는 쪽에서 한다.
    파생 플래그(`has_special` 등)는 **힌트로만** 내고 원문 옆에 붙인다.
  - 분기: 누적 차분(3분기 누계 − 반기 누계). 앞 원장이 없으면 `미산출`.
    🔴 `anomaly='음수차분'` 은 버리지 않고 남긴다 — 분기 발표 뒤 결산에서 배당이 하향된
    사례가 실재한다(계룡건설 2023: 주당 500→400원). 그 행은 「누적」 전제가 깨진 것이다.
  - **0회와 모름의 구분** — `krx_listing`(주간 시세 관측 파생)으로 가른다. 그 사업연도
    말일 이전에 상장이 확인되면 결의가 없는 해는 `0`, 아직 상장 전이거나 티커가 그 표에
    없어 상장 여부를 모르면 `null`이다. 질의 실패는 `null` 로 메우지 않고 따로 낸다.
    🔴 `krx_listing.first_seen_dd` 는 **관측 시작일이지 상장일이 아니다** — 관측창
    (2015-12-30~)전부터 있던 종목은 `before_window=True`로 표시되고 우리 배당 창
    (2020~)을 통째로 덮으므로 판단엔 지장이 없다.

🔴 못 하는 것
  - **보통/우선 총액 배분은 내지 않는다.** `alotMatter` 에 종류별 발행주식수가 없어
    `DPS × 주식수` 로 만든 배분값은 신고총액과 57.2% 만 5% 이내로 맞았다(2026-09-02 검산).
    종류별 DPS 는 원문 그대로 내고, **총액은 신고총액 하나만** 낸다.
  - **결정공시 횟수 집계는 온전한 해만** 신뢰한다(`payment_scope_years()`). FY2018·2019 는 수집창
    이전 결의가 빠졌고, 최근 해는 결산 결의가 이듬해(비12월 결산은 그 뒤)에 접수돼 아직 다 안 걷혔다 —
    `div_payment_scope` 가 그 사실을 표로 갖고 있다(`reason` 에 마감 추정일).
"""
from __future__ import annotations

import calendar
import logging
from typing import Any

from open_proxy_mcp.db import pg_rows

logger = logging.getLogger(__name__)

_ANNUAL = "11011"          # 사업보고서 — 연간 확정치는 여기만 본다
# 🔴 `stock_kind` 실제 값은 `보통`·`우선`·`미구분`·`해당없음` 넷이다(`보통주` 가 아니다).
#    「미구분」은 종류주식이 없는 회사가 종류 칸을 `-` 로 낸 것이라 **보통주로 취급한다** —
#    빼면 3,209행이 통째로 사라진다(첫 구현이 그래서 모집단 0사를 냈다).
#    실제 필터는 아래 SQL 리터럴 `stock_kind IN ('보통','미구분')` 4곳이다 — 바꿀 때 함께 바꾼다.


def _rows(sql: str, params: tuple = ()) -> list[tuple] | None:
    try:
        return pg_rows(sql, params)
    except Exception as exc:                      # pragma: no cover - DB 장애 경로
        logger.warning("dividend_data query failed: %s", exc)
        return None


_COMMON_KINDS = ("보통", "미구분")      # 아래 SQL 리터럴 `stock_kind IN ('보통','미구분')` 과 같다 — 바꿀 때 함께 바꾼다.

#: 결산기 행 → 회사·사업연도 한 줄. 결산기(`stlm_dt`)마다 한 행만 고르고(확정 우선 → 총액 큰 순 → 원문 줄 순서 — 같은 결산기에 보통주·우선주(종류 칸이 `-` 인 미구분)
#: 줄이 여럿이어도 결과가 정해진다. 종전 `DISTINCT ON` 이 총액 동률에서 고르던 줄 = 원문 첫 줄(보통주)과 같아, 12월 결산 회사의 값은 그대로다) 그 위에서 사업연도 합을 낸다. `{join}`·`{where}` 는 호출부 조건(섹터 조인·사업연도 범위 등).
#: 총액: 결산기 둘 이상인데 총액이 모두 같고 DPS 는 서로 다르면(`ledger_copy` — 원장이 가장 늦은 결산기 총액을 복사해 둔 꼴) 더하지 않고 그 하나를 낸다. 총액도 DPS 도 같으면 실제로 같은 배당이라 더한다.
_YEAR_SQL = """
  per AS (
    SELECT DISTINCT ON (d.corp_code, d.bsns_year, d.stlm_dt)
           d.corp_code, d.name, d.tickers, d.bsns_year, d.stlm_dt, d.dps_krw, d.div_total_krw, d.payout_pct, d.status, d.rcept_no
      FROM div_declared d {join}
     WHERE {where}
     ORDER BY d.corp_code, d.bsns_year, d.stlm_dt, (d.status = '확정') DESC, d.div_total_krw DESC NULLS LAST, d.line_no
  ),
  yr AS (
    SELECT corp_code, bsns_year,
           (array_agg(name ORDER BY stlm_dt DESC))[1] AS name,
           (array_agg(tickers ORDER BY stlm_dt DESC))[1] AS tickers,
           count(*) AS n_periods,
           bool_or(status = '확정') AS confirmed,
           sum(dps_krw) FILTER (WHERE status = '확정') AS dps_krw,
           CASE WHEN count(div_total_krw) > 1 AND count(DISTINCT div_total_krw) = 1
                     AND count(DISTINCT dps_krw) FILTER (WHERE status = '확정' AND div_total_krw IS NOT NULL) > 1 THEN max(div_total_krw)
                ELSE sum(div_total_krw) END AS div_total_krw,
           (count(div_total_krw) > 1 AND count(DISTINCT div_total_krw) = 1
            AND count(DISTINCT dps_krw) FILTER (WHERE status = '확정' AND div_total_krw IS NOT NULL) > 1) AS copy_guard,
           (array_agg(payout_pct ORDER BY stlm_dt DESC))[1] AS payout_pct,
           (array_agg(rcept_no ORDER BY stlm_dt DESC))[1] AS rcept_no
      FROM per GROUP BY corp_code, bsns_year
  )
"""


def year_totals(annual: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`firm_history` 의 원장 행(종류별·결산기별 줄) → **사업연도별 보통주 합**. 결산기가 둘 이상인 해는 DPS 가 그 해 결산기 DPS 의 합이다. (261021)

    SQL 의 `_YEAR_SQL` 과 같은 규칙을 파이썬으로 — 회사 하나의 원장 줄은 이미 읽어 와 있어 질의를 더하지 않는다.
    같은 결산기에 보통주 줄이 여럿(종류 칸이 비어 우선주가 미구분으로 섞인 경우 등)이면 확정 → 총액 큰 순 → 원문 순서로 한 줄. 반환 줄: `bsns_year`·`n_periods`·`dps_krw`(확정 결산기 합, 없으면 None)·
    `div_total_krw`·`total_basis`(`single`/`sum`/`ledger_copy`)·`periods`(결산기별 `stlm_dt`·`dps_krw`·`div_total_krw`·`row_status`·`rcept_no`)."""
    best: dict[tuple, dict[str, Any]] = {}
    for r in annual:
        if r.get("stock_kind") not in _COMMON_KINDS:
            continue
        key = (r["bsns_year"], r.get("stlm_dt") or "")
        rank = (r.get("row_status") == "확정", r.get("div_total_krw") if r.get("div_total_krw") is not None else float("-inf"))
        if key not in best or rank > best[key][0]:       # 동률이면 먼저 온 줄(원문 순서)
            best[key] = (rank, r)
    by_year: dict[int, list[dict[str, Any]]] = {}
    for (fy, _stlm), (_rank, r) in sorted(best.items(), key=lambda kv: kv[0]):
        by_year.setdefault(fy, []).append(r)
    out = []
    for fy in sorted(by_year, reverse=True):
        rows = by_year[fy]
        confirmed = [r for r in rows if r.get("row_status") == "확정"]
        dps = [r["dps_krw"] for r in confirmed if r.get("dps_krw") is not None]
        totals = [r["div_total_krw"] for r in rows if r.get("div_total_krw") is not None]
        dps_with_total = {r["dps_krw"] for r in confirmed if r.get("dps_krw") is not None and r.get("div_total_krw") is not None}
        if len(rows) > 1 and len(totals) > 1 and len(set(totals)) == 1 and len(dps_with_total) > 1:      # SQL `copy_guard` 와 같은 조건
            basis, total = "ledger_copy", totals[0]
        elif len(rows) > 1:
            basis, total = "sum", (sum(totals) if totals else None)
        else:
            basis, total = "single", (totals[0] if totals else None)
        out.append({
            "bsns_year": fy, "n_periods": len(rows),
            "dps_krw": sum(dps) if dps else None, "div_total_krw": total, "total_basis": basis,
            "periods": [{"stlm_dt": r.get("stlm_dt"), "dps_krw": r.get("dps_krw"), "div_total_krw": r.get("div_total_krw"),
                         "row_status": r.get("row_status"), "rcept_no": r.get("rcept_no")} for r in rows],
        })
    return out


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
         ORDER BY bsns_year DESC, stlm_dt, stock_kind, stock_knd_raw, line_no
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
    ann_failed, qtr_failed = ann is None, qtr is None
    if ann_failed and qtr_failed:
        return {"status": "db_error"}
    # 🔴 한쪽만 실패해도 그 쪽은 「없다」가 아니라 「못 읽었다」다 — 플래그로 나른다.
    #   (260903 점검: 연간 질의만 실패하면 「원장이 이 구간에 없다」로 렌더되고 있었다.)

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
    annual_rows_out = [
        {"bsns_year": r[0], "stock_kind": r[1], "stock_knd_raw": r[2],
         "dps_krw": r[3], "div_total_krw": r[4], "payout_pct": r[5],
         "row_status": r[6], "rcept_no": r[7], "stlm_dt": r[8]}
        for r in (ann or [])
    ]

    return {
        "status": "ok",
        "annual_failed": ann_failed,
        "quarterly_failed": qtr_failed,
        "empty_kind_rows_folded": folded,
        # 261021: 반기·분기 결산 회사는 한 사업연도에 결산기 줄이 여럿이다 — 줄은 그대로(`annual`) 두고 사업연도별 보통주 합을 따로 낸다(새 칸).
        "year_totals": year_totals(annual_rows_out),
        "annual": annual_rows_out,
        "quarterly": [
            {"bsns_year": r[0], "reprt_code": r[1], "q_label": r[2],
             "cum_div_total_krw": r[3], "quarterly_div_krw": r[4],
             "anomaly": r[5], "row_status": r[6], "rcept_no": r[7]}
            for r in (qtr or [])
        ],
    }


# ───────────────────────────────────────────────────────────── 결정공시 횟수 ──
def payment_scope_years(market: str = "KOSPI") -> list[int]:
    """온전하다고 표시된 사업연도만. 이 목록 밖에서 횟수를 세면 「모른다」를 「0」으로 잘못 읽는다."""
    rows = _rows(
        "SELECT fiscal_year FROM div_payment_scope WHERE market = %s AND is_complete "
        "ORDER BY fiscal_year", (market,))
    return [r[0] for r in (rows or [])]


def payment_counts(corp_code: str, year_from: int, year_to: int) -> dict[str, Any]:
    """회사 하나 — 결의 한 건 = 한 행(`decisions`)과 그것을 사업연도로 접은 것(`rows`).

    🔴 `dividend_type_filed` 를 그대로 낸다(판정값 아님) — 원문 표기가 어떻든 판정은
    `dividend_type` 이 이미 했고, 다르면 `anomaly` 에 이유가 있다. 원문을 덮어쓰지 않는다.

    🔴 `remarks` — 비고(11번 「기타 투자판단과 관련한 중요사항」) **칸 전문을 무조건**
    싣는다. 자르지 않고, 플래그로 거르지도 않는다.
    260903 이전엔 `array_agg(special_note) FILTER (WHERE has_special)` 였다 — 정규식이
    「특별배당」을 본 결의에서만, 그것도 200자까지만 원문을 줬다. 그러면 감액배당 재원,
    자기주식 제외 산정, 주총 갈음, 차등배당, 「변동될 수 있음」 단서처럼 **서식에 칸이
    없어 비고에만 적히는 사실이 전부 사라진다.** 읽는 쪽이 LLM 이므로 원문을 넘기고
    판단은 읽는 쪽에서 한다. 실측 3,831건: 중앙값 245자 · 최대 1,512자 · 빈 칸 0건.

    `has_special` — 같은 비고에서 파서가 「특별배당」·「기념배당」을 본 결의인가. **힌트일
    뿐 정본이 아니다** — 정본은 위 `remarks` 다. 낮게 나오는 게 정상이다(코스피
    FY2020~2024 전수에서 2/3,831). 느슨한 `추가.*배당` 휴리스틱은 쓰지 않는다(같은 전수
    22건 중 20건이 「우선주 가산배당」 같은 무관 문구 오탐이었다).
    **정기·특별분이 한 결의에 섞여도 금액은 못 가른다** — 서식에 분리 칸이 없다.

    연도 집계는 SQL 이 아니라 여기서 접는다 — 같은 행을 두 번 읽지 않으려는 것이다
    (왕복 1회로 결의 원문과 연도 합계를 동시에 낸다).
    """
    rows = _rows(
        """
        SELECT fiscal_year, board_date, record_date, dividend_type_filed, dividend_type,
               dps_common, total_amount, rcept_no, amended, anomaly, has_special, remarks
          FROM div_payment
         WHERE corp_code = %s AND fiscal_year BETWEEN %s AND %s
         ORDER BY fiscal_year DESC, board_date DESC NULLS LAST, rcept_no DESC
        """,
        (corp_code, year_from, year_to),
    )
    if rows is None:
        return {"status": "db_error"}

    decisions = [
        {"fiscal_year": r[0], "board_date": r[1], "record_date": r[2],
         "dividend_type_filed": r[3], "dividend_type": r[4],
         "dps_common": r[5], "total_amount": r[6], "rcept_no": r[7],
         "amended": r[8], "anomaly": r[9], "has_special": r[10], "remarks": r[11]}
        for r in rows
    ]

    agg: dict[int, dict[str, Any]] = {}
    for d in decisions:
        a = agg.setdefault(d["fiscal_year"], {
            "fiscal_year": d["fiscal_year"], "n_payments": 0,
            "dps_sum": None, "total_sum": None, "kinds_filed": [],
            "amended": False, "anomalies": [], "has_special": False})
        a["n_payments"] += 1
        # 🔴 None 을 0 으로 바꾸지 않는다 — 전부 빈 해는 합계도 빈 칸이어야 한다.
        for src, dst in (("dps_common", "dps_sum"), ("total_amount", "total_sum")):
            if d[src] is not None:
                a[dst] = (a[dst] or 0) + d[src]
        if d["dividend_type_filed"] and d["dividend_type_filed"] not in a["kinds_filed"]:
            a["kinds_filed"].append(d["dividend_type_filed"])
        if d["anomaly"] and d["anomaly"] not in a["anomalies"]:
            a["anomalies"].append(d["anomaly"])
        a["amended"] = a["amended"] or bool(d["amended"])
        a["has_special"] = a["has_special"] or bool(d["has_special"])
    for a in agg.values():
        a["kinds_filed"].sort()
        a["anomalies"].sort()

    return {
        "status": "ok",
        "complete_years": payment_scope_years(),
        "rows": [agg[fy] for fy in sorted(agg, reverse=True)],
        "decisions": decisions,
    }


def payment_history(pairs: list[tuple[str, str | None]], year_from: int, year_to: int
                     ) -> dict[str, list[int | None]] | None:
    """(corp_code, stock_code) 목록 → corp_code 별 `[year_from..year_to]` 결정공시 횟수 배열.

    한 칸에 세 뜻을 가른다 — **n**(그 해 실제 결의 횟수) · **0**(상장 중인데 결의 없음) ·
    **null**(상장 여부를 모른다: 그 사업연도 말일 시점에 아직 상장 전이거나, 티커가 없거나
    `krx_listing` 에 없다). 상장 여부는 `krx_listing`(모듈 docstring의 「0회와 모름의 구분」
    참조). 모르는 회사는 전 구간이 `null` 이다 — 0 으로 메우지 않는다.

    🔴 **질의 자체가 실패하면 `None`** 을 돌려준다 — 「전 회사가 상장 전」처럼 보이는 null
    배열로 메우지 않는다(260903 점검). 호출부가 `None` 을 「조회 실패」로 따로 렌더한다.

    실측(260903): 3,257종목 전체를 매 호출 스캔하면 279ms — 그래서 `krx_listing` 을
    먼저 구워 두고 여기서는 그 표만 좁혀 읽는다(대상 corp 수만큼, 통상 10ms 대).
    """
    codes = [c for c, _ in pairs]
    tickers = [t for _, t in pairs]
    if not codes:
        return {}
    rows = _rows(
        """
        WITH years AS (SELECT generate_series(%s::int, %s::int) AS fy),
        input AS (SELECT * FROM unnest(%s::text[], %s::text[]) AS t(corp_code, ticker)),
        acc AS (SELECT corp_code, max(acc_mt) AS acc_mt FROM div_payment
                 WHERE corp_code = ANY(%s) GROUP BY 1),
        cnt AS (SELECT corp_code, fiscal_year, count(*) AS n FROM div_payment
                 WHERE corp_code = ANY(%s) GROUP BY 1, 2)
        SELECT i.corp_code, y.fy, cnt.n, COALESCE(a.acc_mt, 12), l.first_seen_dd, l.before_window
          FROM input i CROSS JOIN years y
          LEFT JOIN cnt ON cnt.corp_code = i.corp_code AND cnt.fiscal_year = y.fy
          LEFT JOIN acc a ON a.corp_code = i.corp_code
          LEFT JOIN krx_listing l ON l.ticker = i.ticker
         ORDER BY i.corp_code, y.fy
        """,
        (year_from, year_to, codes, tickers, codes, codes),
    )
    if rows is None:
        return None

    out: dict[str, list[int | None]] = {}
    for corp_code, fy, n, acc_mt, first_seen_dd, before_window in rows:
        last_day = calendar.monthrange(fy, acc_mt)[1]
        fy_end = f"{fy:04d}{acc_mt:02d}{last_day:02d}"
        if first_seen_dd is None:
            val = None  # 티커를 모르거나 krx_listing 에 없다 — 상장 여부를 모른다
        elif before_window or first_seen_dd <= fy_end:
            val = n or 0
        else:
            val = None  # 그 사업연도 말일 시점에 아직 상장 전
        out.setdefault(corp_code, []).append(val)
    return out


# ───────────────────────────────────────────────────────── 시장·섹터 시계열 ──
def aggregate_history(scope: str, key: str, year_from: int, year_to: int) -> dict[str, Any]:
    """시장(KOSPI) 또는 업종 대분류의 사업연도별 배당 집계.

    🔴 **분모를 두 벌 낸다** — `n_universe`(그 해 표에 있는 회사 전부)와 `n_payers`(실제 배당).
    한 벌만 내면 「배당이 줄었다」와 「배당하는 회사가 줄었다」가 구별되지 않는다.
    🔴 빈 버킷을 0 으로 메우지 않는다 — 「배당 0」과 「잴 회사가 없다」는 다르다.
    🔴 회사·사업연도 한 줄은 **그 해 결산기 행의 합**이다(`_YEAR_SQL`, 261021) — 반기·분기 결산 회사(리츠)는 결산기 행이 여럿이고, 종전 `DISTINCT ON
    (회사, 사업연도)` 는 그 가운데 총액이 큰 한 결산기만 셌다. 총액이 결산기 행마다 같은데 DPS 가 다른 복사 꼴이면 더하지 않고(`n_copy_guard` 가 그 회사 수) 그 하나를 쓴다.
    """
    # 섹터는 업종분류 최신 스냅샷으로 붙인다. 종목 하나가 여러 티커(우선주)를 갖는
    # 경우가 있어 `div_declared.tickers` 로 조인하되 (회사, 사업연도, 결산기) 로 중복을 없앤다.
    where = ["d.reprt_code = %s", "d.bsns_year BETWEEN %s AND %s", "d.stock_kind IN ('보통','미구분')"]
    params: list[Any] = [_ANNUAL, year_from, year_to]
    join = ""
    ctes = ""
    if scope == "sector":
        ctes = "snap AS (SELECT MAX(snap_dd) AS d FROM wise_sector),\n"
        join = "JOIN wise_sector w ON w.ticker = d.tickers AND w.snap_dd = (SELECT d FROM snap)"
        where.append("w.sector = %s")
        params.append(key)
    sql = f"""
        WITH {ctes}{_YEAR_SQL.format(join=join, where=' AND '.join(where))}
        SELECT bsns_year, COUNT(*) AS n_universe,
               COUNT(*) FILTER (WHERE confirmed AND div_total_krw > 0) AS n_payers,
               SUM(div_total_krw) FILTER (WHERE confirmed) AS div_total_krw,
               AVG(payout_pct) FILTER (WHERE confirmed AND payout_pct IS NOT NULL) AS payout_avg,
               COUNT(*) FILTER (WHERE copy_guard) AS n_copy_guard
          FROM yr GROUP BY bsns_year ORDER BY bsns_year DESC
    """
    rows = _rows(sql, tuple(params))
    if rows is None:
        return {"status": "db_error"}
    return {
        "status": "ok",
        "rows": [
            {"bsns_year": r[0], "n_universe": r[1], "n_payers": r[2],
             "div_total_krw": r[3], "payout_avg": r[4], "n_copy_guard": r[5] if len(r) > 5 else 0}
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
    min_payments: int | None = None,
    sector: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """한 사업연도에서 조건으로 회사를 거른다 — 보통주 기준. 금액(배당성향·DPS)은 원장
    `div_declared`, **횟수**(`min_payments`)는 결정공시 `div_payment` — 자를 재료에서
    만들지 않는다(원장 스스로 배당구분을 4칸으로 억지로 나눈 값이 아니라, 실제 접수건 수).

    `min_payments` — **그 해에 실제로 결의된 배당 횟수**가 이 값 이상인 회사. 온전한 해만
    신뢰한다(`payment_scope_years()`) — 그 밖의 해에 걸면 `scope_incomplete` 를 낸다.

    🔴 260903 갈아엎음 — 종전(`quarterly_only`)은 분기 **원장**(`div_quarterly`)의 확정
    칸이 2개 이상인지를 봤는데, 원장은 대다수 회사가 분기 배당칸을 비워 둔다. 실측
    FY2024 「2회 이상」: 원장 기준 20사 vs 결정공시 기준 84사(누락 64, 오탐 0). 결정공시는
    실제 접수된 결의 건수라 이 구멍이 없다 — `n_unknown`(판단불가) 개념 자체가 사라진다.
    """
    if min_payments is not None and min_payments > 0:
        complete = payment_scope_years()
        if bsns_year not in complete:
            return {"status": "scope_incomplete", "complete_years": complete}

    # `보통`·`미구분`만. 260902 4분류 뒤로 `종류`(상환·전환·무의결권·트래킹스톡)는 여기서
    #   자동으로 빠진다 — 종전엔 그것들이 `우선` 통에 섞여 있었다.
    # 회사·사업연도 한 줄 = 그 해 결산기 행의 합(`_YEAR_SQL`, 261021). 종전엔 행 단위로 조건을 걸고 총액이 같은 행 가운데 **임의의 한 줄**의 DPS 를 냈다 —
    #   반기·분기 결산 리츠는 결산기 행의 총액이 복사되어 모두 같아 어느 결산기 DPS 가 나올지 정해져 있지 않았다. 이제 조건(`min_dps`·성향)은 사업연도 합에 건다.
    # `dps_krw > 0` — 표 머리에 「무배당 제외」라고 써 놓고 DPS 0원 회사를 넣고 있었다
    #   (씨티알모빌리티·DB, U7 실측). 원문에 0 이 적혀 있어도 그건 배당한 것이 아니다.
    per_where = ["d.reprt_code = %s", "d.bsns_year = %s", "d.stock_kind IN ('보통','미구분')"]
    params: list[Any] = [_ANNUAL, bsns_year]
    join = ""
    if sector:
        join = ("JOIN wise_sector w ON w.ticker = d.tickers "
                "AND w.snap_dd = (SELECT MAX(snap_dd) FROM wise_sector)")
        per_where.append("w.sector = %s"); params.append(sector)
    where = ["confirmed", "dps_krw > 0"]
    if min_payout is not None:
        where.append("payout_pct >= %s"); params.append(min_payout)
    if max_payout is not None:
        where.append("payout_pct <= %s"); params.append(max_payout)
    if min_dps is not None:
        where.append("dps_krw >= %s"); params.append(min_dps)
    if min_payments is not None and min_payments > 0:
        where.append(
            "(SELECT COUNT(*) FROM div_payment p "
            " WHERE p.corp_code = yr.corp_code AND p.fiscal_year = yr.bsns_year) >= %s")
        params.append(min_payments)

    # 질의 하나로 — 행 · 조건에 걸린 전체 수(`COUNT(*) OVER ()` — LIMIT 앞에서 센다) · 모집단(조건 전 그 해 회사 수, 첫 CTE). 종전엔 이 셋이 질의 셋이었다(같은 CTE 를
    #   두 번 돌렸다). 행이 하나도 없을 때만 모집단을 따로 한 번 더 읽는다(매칭 수는 0).
    sql = f"""
        WITH uni AS (SELECT COUNT(DISTINCT corp_code) AS n FROM div_declared
                      WHERE reprt_code = %s AND bsns_year = %s AND stock_kind IN ('보통','미구분')),
        {_YEAR_SQL.format(join=join, where=' AND '.join(per_where))}
        SELECT t.corp_code, t.name, t.tickers, t.dps_krw, t.div_total_krw, t.payout_pct, t.rcept_no, t.n_periods, t.copy_guard,
               COUNT(*) OVER () AS matched, (SELECT n FROM uni) AS n_universe
          FROM (SELECT * FROM yr WHERE {' AND '.join(where)}) t
         ORDER BY t.div_total_krw DESC NULLS LAST, t.corp_code LIMIT %s
    """
    rows = _rows(sql, (_ANNUAL, bsns_year, *params, limit))
    if rows is None:
        return {"status": "db_error"}
    if rows:
        matched, n_universe = rows[0][9], rows[0][10]
    else:
        # 조건에 맞는 회사가 없다(matched = 0). 모집단은 「몇 중 몇」을 읽으려고 따로 낸다 — 이 질의만 실패하면 모집단을 못 센 채 낸다(렌더가 말한다).
        matched = 0
        tot = _rows(
            "SELECT COUNT(DISTINCT corp_code) FROM div_declared "
            "WHERE reprt_code = %s AND bsns_year = %s AND stock_kind IN ('보통','미구분')",
            (_ANNUAL, bsns_year))
        n_universe = (tot or [(None,)])[0][0]
    return {
        "status": "ok",
        "n_universe": n_universe,
        "matched": matched,
        "returned": len(rows),
        "limit": limit,
        "rows": [
            {"corp_code": r[0], "name": r[1], "ticker": r[2], "dps_krw": r[3],
             "div_total_krw": r[4], "payout_pct": r[5], "rcept_no": r[6],
             "n_periods": r[7], "total_basis": "ledger_copy" if r[8] else ("sum" if r[7] > 1 else "single")}
            for r in rows
        ],
    }


def latest_annual_year() -> int | None:
    """사업보고서(`11011`) 행이 있는 **마지막** 사업연도. None = 질의 실패 또는 사업보고서 행이 없음.

    261019: 원장이 매주 갱신되면서 올해(사업보고서는 이듬해 3월)의 1분기·반기 행이 먼저 들어온다. 그러면
    `year_range()` 의 끝(모든 보고서 기준)이 올해가 되는데, 연간 확정치만 쓰는 screen 이 그 해를 기본으로 잡으면
    **연도 없이 부른 스크리닝이 빈 결과**를 낸다. 연간 확정치의 기본 연도는 이것으로 고른다.
    운영 계획: `div_declared_yr`(bsns_year) 인덱스를 뒤에서부터 훑다 첫 `11011` 행에서 멈춘다 — 올해 분기 행 수천 개만 지나간다."""
    rows = _rows("SELECT MAX(bsns_year) FROM div_declared WHERE reprt_code = %s", (_ANNUAL,))
    if not rows or rows[0][0] is None:
        return None
    return rows[0][0]


def year_range() -> tuple[int, int] | None:
    """원장의 `(처음, 마지막)` 사업연도 — **호출마다 DB 에 묻는다**. None = 질의 실패 또는 빈 표 — 호출부가 `db_error` 로 낸다.

    261018: 이 질의가 인덱스 없이 표 전체(38,183행)를 읽던 때(평균 475ms) 6시간 메모리 캐시를 붙였는데(#88), 그 대가로 새 사업연도를
    적재한 뒤 최대 6시간(머신마다) 기본 연도·범위가 지난해로 남았다. 같은 PR 의 인덱스 `div_declared_yr`(`scripts/div_indexes_ddl.py`)가
    운영에 있어 이제 MIN·MAX 는 인덱스 양 끝 한 칸씩만 읽는다(운영 EXPLAIN — Index Only Scan, 행 1개씩). 그래서 캐시를 없앴다 —
    적재 직후 바로 보인다. 동기 함수다 — async 호출부는 `asyncio.to_thread` 로 부른다(`tools/dividend_data.py`)."""
    rows = _rows("SELECT MIN(bsns_year), MAX(bsns_year) FROM div_declared")
    if not rows or rows[0][0] is None:
        return None
    return (rows[0][0], rows[0][1])
