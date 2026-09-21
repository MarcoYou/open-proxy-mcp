"""공시 흐름 보기 — 공시 이벤트 원장(`dart_events`)을 업종·회사 단위로 합산한다 (screener 흐름 보기, 260918).

카드 보기(`screener` 기본)는 DART 를 그때그때 스캔해 「무엇이 떴나」를 한 줄씩 준다. 저장이 없어
「평소보다 많이 떴나」는 답할 수 없었다. 흐름 보기는 매일 밤 쌓이는 원장만 읽어(DART 0콜) 세 가지를 준다.

1. **업종별 흐름** — 업종 대분류·중분류별 새 공시 건수·금액을 직전 N주(기본 13주)의 같은 일수 환산값과 비교.
2. **큰 수주** — 매출 대비 비율이 기준(기본 10%) 이상인 새 계약 목록.
3. **올해 누적 수주** — 회사별로 올해 새 계약의 「매출 대비 %」를 더한 값. 공시에 회사가 직접 적은 값이라
   재무표와 조인하지 않는다.

지키는 것
- **정정은 새 공시로 세지 않는다.** 원장은 정정본을 원본에 접지 않고 둘 다 남긴다 — 여기서 따로 센다.
  수주 「해지」도 새 계약이 아니다.
- **모수를 정직하게.** 비교는 `dart_events_scan` 에서 실제로 본 날만 센다(원장에 없는 날 ≠ 0건).
  이번 기간과 비교 기준의 「본 날 수」가 다르면 그 비율로 환산한다. 그날 21시 이전에 본 날은 하루가 안 끝났으니
  본 날로 치지 않는다.
- **평소 건수가 적은 업종은 배수가 튄다.** 같은 일수 환산 평소 건수가 1건 미만이면 순위 뒤로 보내고 표시한다.
- **금액·비율은 상세를 읽은 공시만.** 확인 건수를 나란히 싣는다. 못 읽은 건을 0 으로 채우지 않는다.
- **자회사 계약을 모회사가 다시 공시한 건**(「…(자회사의 주요경영사항)」)은 같은 날 같은 금액으로 자회사가 직접 낸
  공시가 있으면 중복으로 빼고, 없으면(비상장 자회사) 유일한 기록이라 건수에는 넣는다. 그 공시의 「매출 대비 %」는
  **자회사 매출 기준**이라 모회사의 평균 비율·누적 합에는 넣지 않는다(260918 실측: 비츠로테크가 비츠로넥스텍 계약을
  같은 금액·같은 89.8% 로 다시 공시).
"""
from __future__ import annotations

import asyncio
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from open_proxy_mcp.db import pg_rows

TOOL = "screener"
LEVEL_LABEL = {"sector": "업종 대분류", "industry": "업종 중분류"}
NO_BUCKET = "(업종 미상)"
DEFAULT_BASELINE_WEEKS = 13
DEFAULT_LARGE_MIN_PCT = 10.0
MAX_KINDS = 6
LARGE_MAX_ROWS = 50
COVERAGE_MAX_ROWS = 50   # json 크기 — 200 이면 109KB 였다(260918 실측)

# 기간 말은 카드 보기와 같은 해석기(`services/period_words.py`)가 읽는다 — 260918 전면 점검.
#   「올해」「이번 주」「이번 달」「이번 분기」는 오늘 기준 칸의 첫날부터 원장 최신일까지이고, 원장이 아직 그 칸에
#   못 들어왔으면(월초·주초 밤 배치 전) 가장 최근 칸을 보이고 밝힌다.


# ── 인자 해석 ──────────────────────────────────────────────────────────

def resolve_levels(level: str) -> tuple[list[str], list[str]]:
    """「대분류」·「중분류」·「둘 다」 → ['sector'] / ['industry'] / 둘. 모르는 말은 둘 다로."""
    raw = (level or "").strip().lower().replace(" ", "")
    if raw in ("", "both", "all", "둘다", "모두", "전체", "대분류,중분류", "대분류중분류", "대·중분류", "대중분류"):
        return ["sector", "industry"], []
    if raw in ("sector", "대분류", "섹터", "대"):
        return ["sector"], []
    if raw in ("industry", "중분류", "업종", "산업", "중"):
        return ["industry"], []
    return ["sector", "industry"], [f"분류 단계 「{level}」를 알아듣지 못해 대분류·중분류를 모두 보였다."]


def resolve_kinds(types: str) -> tuple[list[str], list[str]]:
    """유형 말 → 원장 유형 코드. 흐름 보기의 기본은 수주다(카드 보기 기본 프리셋과 다르다)."""
    from open_proxy_mcp.services.screener import _BY_CODE, _nl_types, _resolve_types

    raw = (types or "").strip()
    if raw.lower() in ("", "core"):
        return ["order"], []
    codes, notices = _resolve_types(_nl_types(raw))
    kept = [c for c in codes if not _BY_CODE[c].get("opt_in")]
    dropped = [c for c in codes if _BY_CODE[c].get("opt_in")]
    if dropped:
        notices.append("원장에 쌓지 않는 유형은 뺐다: " + ", ".join(_BY_CODE[c]["label"] for c in dropped)
                       + " — 이 유형은 카드 보기로 본다.")
    if len(kept) > MAX_KINDS:
        notices.append(f"유형이 많아 앞의 {MAX_KINDS}개만 보였다: "
                       + ", ".join(_BY_CODE[c]["label"] for c in kept[MAX_KINDS:]) + " 는 따로 부를 것.")
        kept = kept[:MAX_KINDS]
    return kept, notices


def _dd(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def resolve_flow_period(period: str, start_date: str, end_date: str, today: date,
                        first_day: date | None, last_day: date | None) -> tuple[date, date, list[str]]:
    """기간 말 → (시작, 끝, 안내). 카드 보기와 달리 3개월 한도가 없다(원장에 있는 만큼).

    기본(말이 없거나 카드 보기 기본값 「어제부터」)은 **원장 최신일까지 최근 7일**이다 — 오늘치는 밤 배치가
    돌아야 들어오므로 오늘을 끝으로 잡으면 마지막 날이 늘 비어 있다.
    말은 카드 보기와 같은 해석기가 읽는다(`period_words`, 260918). 「이번 ~」은 오늘이 속한 칸의 첫날부터
    원장 최신일까지, 원장이 아직 그 칸에 없으면 가장 최근 칸을 보이고 밝힌다. 나머지는 해석한 창을 원장 범위로
    자른다 — 자르면 그 사실을 적는다.
    """
    from open_proxy_mcp.services.period_words import (LABEL, THIS_CODES, calendar_window, explain_unknown,
                                                      rolling_window, this_start)
    from open_proxy_mcp.dart.client import note_degradation
    from open_proxy_mcp.services.screener import _nl_period

    notices: list[str] = []
    anchor = min(today, last_day) if last_day else today
    raw = (period or "").strip()
    has_dates = bool((start_date or end_date or "").strip())
    if not has_dates and (not raw or raw.lower() == "since_yesterday"):
        start, end = anchor - timedelta(days=6), anchor
    else:
        code, cs, ce = _nl_period(raw, start_date, end_date, "", "", today=today)
        if code == "custom":
            try:
                start, end = _dd(cs), _dd(ce or cs)
            except (TypeError, ValueError):
                note_degradation("period_fallback")
                notices.append(f"날짜를 읽지 못해({cs!r}~{ce!r}) 원장 최신일까지 최근 7일로 보였다.")
                start, end = anchor - timedelta(days=6), anchor
        elif code in THIS_CODES:
            start = this_start(code, today)
            if anchor >= start:
                end = anchor
            else:
                start, end = this_start(code, anchor), anchor
                unit = {"this_week": "주", "this_month": "달", "this_quarter": "분기", "ytd": "해"}[code]
                which = {"this_week": f"{this_start(code, today).isoformat()} 시작",
                         "this_month": f"{today.month}월", "this_quarter": f"{(today.month - 1) // 3 + 1}분기",
                         "ytd": f"{today.year}년"}[code]
                notices.append(f"원장이 아직 {LABEL[code]}({which})에 들어오지 않아 가장 최근 {unit} "
                               f"{start.isoformat()} ~ {end.isoformat()} 을 보였다 — {LABEL[code]} 공시는 밤 배치 뒤에 "
                               "들어온다.")
        elif calendar_window(code, today) or rolling_window(code, today):
            start, end = calendar_window(code, today) or rolling_window(code, today)
        else:
            # 카드 보기와 같은 표지 — 못 읽은 말이 얼마나 오는지 사용 기록으로 센다(말 자체는 남기지 않는다)
            note_degradation("period_fallback")
            notices.append(explain_unknown(raw) + " 원장 최신일까지 최근 7일로 보였다.")
            start, end = anchor - timedelta(days=6), anchor
    req = (start, end)
    if last_day and end > last_day:
        notices.append(f"원장은 {last_day.isoformat()} 까지 쌓여 있어 끝날짜를 그날로 당겼다 "
                       "(오늘치는 밤 배치 뒤에 들어온다 — 오늘 뜬 공시는 카드 보기로 본다).")
        end = last_day
    if first_day and start < first_day:
        notices.append(f"원장은 {first_day.isoformat()} 부터라 시작날짜를 그날로 당겼다.")
        start = first_day
    if start > end:
        # 요청한 창 전체가 원장 밖이다 — 가장 가까운 하루를 보이고 그렇게 적는다(조용히 하루로 줄이지 않는다)
        near = end if req[0] > end else start
        notices.append(f"요청한 기간 {req[0].isoformat()} ~ {req[1].isoformat()} 이 원장 범위 밖이라 "
                       f"가장 가까운 {near.isoformat()} 하루를 보였다.")
        start = end = near
    return start, end, notices


# ── DB (동기, to_thread 로 부른다) ─────────────────────────────────────

def _ledger_ready() -> bool | None:
    """원장 표가 있나. None = DB 미설정/장애. 없는 표를 바로 물으면 풀이 60초 내려가므로 먼저 묻는다."""
    r = pg_rows("SELECT to_regclass('public.dart_events') IS NOT NULL AND "
                "to_regclass('public.dart_events_scan') IS NOT NULL")
    if r is None:
        return None
    return bool(r and r[0][0])


#: 그날 21시(KST) 이전에 본 날은 「본 날」로 치지 않는다. 밤 배치가 늦게(자정 넘어) 돌면 막 시작된 오늘을 보고
#: 완료로 적는데, 그 날은 거의 0건이라 평소 대비가 낮게 나온다(260918 실측: 02:15 실행이 9/18 을 완료로 적음).
#: 다음 밤 배치가 최근 3일을 다시 보면서 채운다.
_DAY_DONE = "complete AND scanned_at >= (scan_dd + time '21:00') AT TIME ZONE 'Asia/Seoul'"


def _ledger_bounds() -> tuple[date | None, date | None] | None:
    r = pg_rows(f"SELECT min(scan_dd), max(scan_dd) FROM dart_events_scan WHERE {_DAY_DONE}")
    if r is None:
        return None
    return (r[0][0], r[0][1]) if r else (None, None)


def _fetch_scan(since: date, until: date) -> dict[tuple[date, str], bool] | None:
    r = pg_rows(f"SELECT scan_dd, code, ({_DAY_DONE}) FROM dart_events_scan WHERE scan_dd BETWEEN %s AND %s",
                (since, until))
    if r is None:
        return None
    return {(dd, code): bool(ok) for dd, code, ok in r}


_EVENT_COLS = ("rcept_no", "rcept_dt", "corp_code", "corp_name", "stock_code", "corp_cls", "kind", "subtype",
               "is_correction", "report_nm", "sector", "industry", "mktcap_won", "amount", "ratio", "counterparty",
               "is_external")


def _fetch_events(kinds: list[str], since: date, until: date,
                  tickers: list[str] | None, corp_cls: str | None) -> list[dict[str, Any]] | None:
    sql = ("SELECT rcept_no, rcept_dt, corp_code, corp_name, stock_code, corp_cls, kind, subtype, is_correction, "
           "report_nm, sector, industry, mktcap_won, detail->>'amount_won', detail->>'revenue_ratio_pct', "
           "detail->>'counterparty', detail->>'is_external' "
           "FROM dart_events WHERE rcept_dt BETWEEN %s AND %s AND kind = ANY(%s)")
    params: list[Any] = [since, until, kinds]
    if tickers is not None:
        sql += " AND stock_code = ANY(%s)"
        params.append(tickers)
    elif corp_cls:
        sql += " AND corp_cls = %s"
        params.append(corp_cls)
    r = pg_rows(sql, tuple(params))
    if r is None:
        return None
    out = []
    for row in r:
        d = dict(zip(_EVENT_COLS, row))
        d["amount"] = _num(d["amount"])
        d["ratio"] = _num(d["ratio"])
        d["is_external"] = {"true": True, "false": False}.get(str(d["is_external"]).lower())
        out.append(d)
    mark_subsidiary_filings(out)
    return out


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── 순수 계산 (테스트 대상) ────────────────────────────────────────────

_SUB_MARK = "자회사의주요경영사항"


def mark_subsidiary_filings(rows: list[dict]) -> None:
    """모회사가 자회사 계약을 다시 낸 공시에 `via_sub`, 그중 자회사 자신의 공시와 겹치는 것에 `sub_dup` 를 단다.

    겹침 = 같은 접수일·같은 금액의 새 공시를 **다른 회사**가 냈다. 금액을 못 읽었으면 겹침을 판정할 수 없어 남긴다
    (모르면 지우지 않는다)."""
    own: dict[tuple, set[str]] = {}
    for r in rows:
        r["via_sub"] = _SUB_MARK in re.sub(r"\s+", "", r.get("report_nm") or "")
        r["sub_dup"] = False
        if not r["via_sub"] and not r.get("is_correction") and r.get("amount") is not None:
            own.setdefault((r["kind"], r["rcept_dt"], r["amount"]), set()).add(r.get("corp_code") or r["corp_name"])
    for r in rows:
        if r["via_sub"] and r.get("amount") is not None:
            others = own.get((r["kind"], r["rcept_dt"], r["amount"]), set()) - {r.get("corp_code") or r["corp_name"]}
            r["sub_dup"] = bool(others)


def is_new(row: dict) -> bool:
    """새 공시 = 정정 아님, 그리고 수주면 해지 아님."""
    if row.get("is_correction") or row.get("sub_dup"):
        return False
    return not (row.get("kind") == "order" and row.get("subtype") == "해지")


def covered_days(scan: dict[tuple[date, str], bool], code: str, since: date, until: date) -> int:
    n, d = 0, since
    while d <= until:
        if scan.get((d, code)):
            n += 1
        d += timedelta(days=1)
    return n


def aggregate_flow(rows: list[dict], kind: str, level: str, cur: tuple[date, date], base: tuple[date, date],
                   cur_cov: int, base_cov: int) -> dict[str, Any]:
    """한 유형·한 분류 단계의 업종별 흐름. 이번 기간 새 공시 vs 비교 기준 같은 일수 환산."""
    buckets: dict[str, dict[str, Any]] = {}

    def b(name: str | None) -> dict[str, Any]:
        key = name or NO_BUCKET
        if key not in buckets:
            buckets[key] = {"bucket": key, "new": 0, "corrections": 0, "cancels": 0, "amount_krw": 0.0,
                            "amount_n": 0, "ratio_sum": 0.0, "ratio_n": 0, "base_new": 0, "sub_dups": 0,
                            "subtypes": Counter()}
        return buckets[key]

    for r in rows:
        if r["kind"] != kind:
            continue
        dt = r["rcept_dt"]
        x = b(r.get(level))
        if cur[0] <= dt <= cur[1]:
            if r.get("sub_dup"):
                x["sub_dups"] += 1
            elif r.get("is_correction"):
                x["corrections"] += 1
            elif kind == "order" and r.get("subtype") == "해지":
                x["cancels"] += 1
            else:
                x["new"] += 1
                if r.get("subtype"):
                    x["subtypes"][r["subtype"]] += 1
                if r.get("amount") is not None:
                    x["amount_krw"] += r["amount"]
                    x["amount_n"] += 1
                if r.get("ratio") is not None and not r.get("via_sub"):
                    x["ratio_sum"] += r["ratio"]
                    x["ratio_n"] += 1
        elif base[0] <= dt <= base[1] and is_new(r):
            x["base_new"] += 1

    def finish(x: dict[str, Any]) -> dict[str, Any]:
        expected = (x["base_new"] * cur_cov / base_cov) if base_cov and cur_cov else None
        out = {
            "bucket": x["bucket"], "new": x["new"], "corrections": x["corrections"],
            "cancels": x["cancels"] if kind == "order" else None,
            "amount_krw": int(round(x["amount_krw"])) if x["amount_n"] else None, "amount_n": x["amount_n"],
            "avg_ratio_pct": round(x["ratio_sum"] / x["ratio_n"], 2) if x["ratio_n"] else None,
            "base_new": x["base_new"], "subsidiary_duplicates": x["sub_dups"],
            "expected": round(expected, 2) if expected is not None else None,
            "vs_base": round(x["new"] / expected, 2) if expected else None,
            "base_weekly_avg": round(x["base_new"] / base_cov * 7, 2) if base_cov else None,
            "thin_base": expected is None or expected < 1.0,
            "subtypes": dict(x["subtypes"].most_common()),
        }
        return out

    rows_out = [finish(x) for x in buckets.values() if x["new"] or x["base_new"] or x["corrections"] or x["cancels"]]
    rows_out.sort(key=lambda r: (r["bucket"] == NO_BUCKET, r["thin_base"], -(r["vs_base"] or 0.0), -r["new"], r["bucket"]))
    tot = {"bucket": "합계", "new": 0, "corrections": 0, "cancels": 0, "amount_krw": 0.0, "amount_n": 0,
           "ratio_sum": 0.0, "ratio_n": 0, "base_new": 0, "sub_dups": 0, "subtypes": Counter()}
    for x in buckets.values():
        for k in ("new", "corrections", "cancels", "amount_krw", "amount_n", "ratio_sum", "ratio_n", "base_new",
                  "sub_dups"):
            tot[k] += x[k]
        tot["subtypes"].update(x["subtypes"])
    return {"level": level, "level_label": LEVEL_LABEL[level], "rows": rows_out, "total": finish(tot)}


def large_orders(rows: list[dict], cur: tuple[date, date], min_pct: float) -> dict[str, Any]:
    """이번 기간 새 계약 중 매출 대비 비율이 기준 이상인 것. 비율을 못 읽은 건은 세기만 한다."""
    new = [r for r in rows if r["kind"] == "order" and cur[0] <= r["rcept_dt"] <= cur[1] and is_new(r)]
    hits = [r for r in new if r.get("ratio") is not None and r["ratio"] >= min_pct]
    hits.sort(key=lambda r: (-r["ratio"], -(r.get("amount") or 0)))
    corr = [r for r in rows if r["kind"] == "order" and cur[0] <= r["rcept_dt"] <= cur[1]
            and r.get("is_correction") and r.get("ratio") is not None and r["ratio"] >= min_pct]
    return {
        "min_ratio_pct": min_pct,
        "rows": [{"rcept_dt": r["rcept_dt"].isoformat(), "corp_name": r["corp_name"], "stock_code": r["stock_code"],
                  "sector": r.get("sector"), "industry": r.get("industry"),
                  "amount_krw": int(r["amount"]) if r.get("amount") is not None else None,
                  "revenue_ratio_pct": r["ratio"], "counterparty": r.get("counterparty"),
                  "is_external": r.get("is_external"), "mktcap_krw": r.get("mktcap_won"),
                  "via_subsidiary": bool(r.get("via_sub")), "rcept_no": r["rcept_no"],
                  "dart_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r['rcept_no']}"}
                 for r in hits[:LARGE_MAX_ROWS]],
        "matched": len(hits),
        "new_contracts": len(new),
        "ratio_unread": sum(1 for r in new if r.get("ratio") is None),
        "corrections_over_min": len(corr),
    }


def order_coverage(rows: list[dict], window: tuple[date, date]) -> dict[str, Any]:
    """회사별 올해 누적 새 계약 — 공시에 적힌 「매출 대비 %」의 합과 금액 합. 확인 건수를 함께."""
    by: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["kind"] != "order" or not (window[0] <= r["rcept_dt"] <= window[1]) or r.get("is_correction"):
            continue
        key = r.get("stock_code") or r.get("corp_code") or r["corp_name"]
        x = by.setdefault(key, {"corp_name": r["corp_name"], "stock_code": r.get("stock_code"),
                                "sector": r.get("sector"), "industry": r.get("industry"),
                                "new": 0, "ratio_sum": 0.0, "ratio_n": 0, "amount_krw": 0.0, "amount_n": 0,
                                "cancels": 0, "via_sub": 0, "mktcap_krw": None, "last_dt": None})
        if r.get("subtype") == "해지":
            x["cancels"] += 1
            continue
        if r.get("via_sub"):
            # 자회사 계약·자회사 매출 기준 비율 — 이 회사 자신의 수주 커버리지에 넣지 않는다.
            x["via_sub"] += 1
            continue
        x["new"] += 1
        if r.get("ratio") is not None:
            x["ratio_sum"] += r["ratio"]
            x["ratio_n"] += 1
        if r.get("amount") is not None:
            x["amount_krw"] += r["amount"]
            x["amount_n"] += 1
        if r.get("mktcap_won") is not None:
            x["mktcap_krw"] = r["mktcap_won"]
        x["last_dt"] = max(x["last_dt"] or r["rcept_dt"], r["rcept_dt"])
        x["sector"] = x["sector"] or r.get("sector")
        x["industry"] = x["industry"] or r.get("industry")
    out = []
    for x in by.values():
        if not x["new"]:
            continue
        out.append({**{k: x[k] for k in ("corp_name", "stock_code", "sector", "industry", "new", "ratio_n",
                                          "amount_n", "cancels", "mktcap_krw")},
                    "subsidiary_filings": x["via_sub"],
                    "ratio_sum_pct": round(x["ratio_sum"], 2) if x["ratio_n"] else None,
                    "amount_krw": int(round(x["amount_krw"])) if x["amount_n"] else None,
                    "last_dt": x["last_dt"].isoformat() if x["last_dt"] else None})
    out.sort(key=lambda r: (-(r["ratio_sum_pct"] or -1), -r["new"], r["corp_name"]))
    total_new = sum(r["new"] for r in out)
    total_ratio_n = sum(r["ratio_n"] for r in out)
    return {"start": window[0].isoformat(), "end": window[1].isoformat(), "companies": len(out),
            "new_contracts": total_new, "ratio_read": total_ratio_n,
            "ratio_read_share": round(total_ratio_n / total_new, 3) if total_new else None,
            "rows": out[:COVERAGE_MAX_ROWS]}


# ── 조립 ───────────────────────────────────────────────────────────────

def _err(status: str, subject: str, *warns: str) -> dict[str, Any]:
    return {"tool": TOOL, "status": status, "subject": subject, "data": {"view": "flow"}, "warnings": list(warns)}


async def build_flow_payload(types: str = "", period: str = "", universe: str = "all", start_date: str = "",
                             end_date: str = "", level: str = "both",
                             baseline_weeks: int = DEFAULT_BASELINE_WEEKS,
                             large_min_pct: float = DEFAULT_LARGE_MIN_PCT) -> dict[str, Any]:
    from open_proxy_mcp.services.screener import _BY_CODE, _KST, _nl_universe, resolve_universe

    subject = "공시 흐름"
    warnings: list[str] = []
    kinds, knotes = resolve_kinds(types)
    warnings += knotes
    if not kinds:
        return _err("invalid", subject, *warnings, "볼 유형이 없다 — 「수주」·「자사주」·「증자」처럼 준다.")
    levels, lnotes = resolve_levels(level)
    warnings += lnotes
    weeks = int(baseline_weeks or DEFAULT_BASELINE_WEEKS)
    if not 1 <= weeks <= 52:
        warnings.append(f"비교 기준 {weeks}주는 범위 밖이라 {min(max(weeks, 1), 52)}주로 맞췄다(1~52주).")
        weeks = min(max(weeks, 1), 52)
    try:
        min_pct = float(large_min_pct)
    except (TypeError, ValueError):
        min_pct = DEFAULT_LARGE_MIN_PCT

    ready = await asyncio.to_thread(_ledger_ready)
    if ready is None:
        st = "db_error" if os.getenv("DATABASE_URL") else "db_unconfigured"
        return _err(st, subject, "공시 원장 DB 를 읽지 못했다 — "
                    + ("일시 장애일 수 있다, 다시 부를 것." if st == "db_error"
                       else "이 서버에는 원장 DB 가 연결돼 있지 않다. 카드 보기(기본)는 쓸 수 있다."))
    if not ready:
        return _err("no_data", subject, "이 서버에는 공시 원장이 아직 없다 — 원장 배치가 한 번 이상 돌아야 한다. "
                    "카드 보기(기본)는 쓸 수 있다.")
    bounds = await asyncio.to_thread(_ledger_bounds)
    if bounds is None:
        return _err("db_error", subject, "공시 원장의 수록 기간을 읽지 못했다 — 다시 부를 것.")
    first_day, last_day = bounds
    if not last_day:
        return _err("no_data", subject, "공시 원장에 완료된 날이 아직 없다.")

    today = datetime.now(_KST).date()
    start, end, pnotes = resolve_flow_period(period, start_date, end_date, today, first_day, last_day)
    warnings += pnotes
    base = (start - timedelta(days=weeks * 7), start - timedelta(days=1))
    ytd = (date(end.year, 1, 1), end)

    uf = await resolve_universe(_nl_universe(universe))
    if uf.question:
        # 260918: 회사 목록을 하나도 못 찾으면 시장 전체로 바꾸지 않고 되묻는다(카드 보기와 같다).
        return _err("needs_input", subject, uf.question, *warnings)
    if uf.notice:
        warnings.append(uf.notice)
    tickers = sorted(uf.allowed) if uf.allowed is not None else None

    fetch_from = min(base[0], ytd[0]) if "order" in kinds else base[0]
    rows, scan = await asyncio.gather(
        asyncio.to_thread(_fetch_events, kinds, fetch_from, end, tickers, uf.corp_cls),
        asyncio.to_thread(_fetch_scan, min(base[0], start), end))
    if rows is None or scan is None:
        return _err("db_error", subject, "공시 원장을 읽는 중 DB 오류 — 다시 부를 것.")

    flows = []
    for k in kinds:
        code = _BY_CODE[k]["scan_code"]
        cur_cov = covered_days(scan, code, start, end)
        base_cov = covered_days(scan, code, base[0], base[1])
        base_days = (base[1] - base[0]).days + 1
        cur_days = (end - start).days + 1
        if cur_cov < cur_days:
            warnings.append(f"{_BY_CODE[k]['label']}: 이번 기간 {cur_days}일 중 원장이 본 날은 {cur_cov}일 — "
                            "본 날만 세고, 평소도 같은 날 수로 환산했다.")
        if base_cov < base_days:
            warnings.append(f"{_BY_CODE[k]['label']}: 비교 기준 {base_days}일 중 원장이 본 날은 {base_cov}일 — "
                            "본 날 기준 평균으로 환산했다.")
        flows.append({"kind": k, "label": _BY_CODE[k]["label"], "covered_days": cur_cov,
                      "base_covered_days": base_cov,
                      "levels": {lv: aggregate_flow(rows, k, lv, (start, end), base, cur_cov, base_cov)
                                 for lv in levels}})

    data: dict[str, Any] = {
        "view": "flow",
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days + 1},
        "baseline": {"weeks": weeks, "start": base[0].isoformat(), "end": base[1].isoformat()},
        "ledger": {"first_day": first_day.isoformat() if first_day else None, "last_day": last_day.isoformat()},
        "universe": {"label": uf.label, "resolved": uf.resolved},
        "levels": levels, "flows": flows,
        "method": ("새 공시 = 정정 아님(수주는 해지도 아님). 평소 = 직전 비교 기준 기간의 새 공시를 원장이 본 날 수로 나눠 "
                   "이번 기간의 본 날 수만큼 환산. 평소 대비 = 새 공시 ÷ 평소. 평소가 1건 미만이면 배수가 튀어 순위 뒤로. "
                   "금액·매출 대비 비율은 상세를 읽은 공시만 더했다(확인 건수를 함께 보인다). 업종은 공시일 이전 최신 "
                   "업종분류 스냅샷. 원장은 밤 배치로 쌓이므로 오늘 뜬 공시는 카드 보기로 본다."),
    }
    if "order" in kinds:
        data["large_orders"] = large_orders(rows, (start, end), min_pct)
        cov = order_coverage(rows, ytd)
        data["order_coverage"] = cov
        share = cov.get("ratio_read_share")
        if share is not None and share < 0.8:
            warnings.append(f"올해 새 계약 {cov['new_contracts']}건 중 매출 대비 비율을 읽은 것은 {cov['ratio_read']}건 — "
                            "누적 합은 읽은 것만 더한 값이라 실제보다 작다. 과거 공시의 상세를 채우면 늘어난다.")
        lo = data["large_orders"]
        if lo["ratio_unread"]:
            warnings.append(f"이번 기간 새 계약 중 매출 대비 비율을 아직 못 읽은 {lo['ratio_unread']}건은 큰 수주 목록에 없다.")
    labels = "·".join(_BY_CODE[k]["label"].split("(")[0] for k in kinds)
    subject = f"공시 흐름 — {labels} ({start.isoformat()} ~ {end.isoformat()})"
    total_new = sum(f["levels"][levels[0]]["total"]["new"] for f in flows)
    return {"tool": TOOL, "status": "ok" if total_new or any(f["levels"][levels[0]]["rows"] for f in flows) else "no_data",
            "subject": subject, "data": data, "warnings": warnings}
