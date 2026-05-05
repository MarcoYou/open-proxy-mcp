"""파서 omnibus 통합 audit script (260505 ralph parser-omnibus-perf).

회사당 1 doc fetch + 9 parser in-memory 호출 — DART 호출 수 폭증 X.

Per-company DART calls (rate-safe):
  1. corp_code lookup (local cache, DART 호출 0)
  2. search_filings (E type, 2026 AGM 소집공고) — 1 call
  3. get_document_cached (latest 소집공고 rcept_no) — 1 call
  → ~2 DART calls / 회사

Concurrency 2, 회사간 sleep 1s, batch 사이 sleep 30s.
20 회사 batch ≈ 40 DART calls / 분산 ~2-3분 → cap 900/min 안전.

사용법:
    uv run python scripts/spot_parser_omnibus.py \\
        --universe kospi200 --start 0 --count 10 \\
        --output wiki/architecture/audits/data/260505_parser_omnibus/iter01_kospi_0-10.json

Tier A 파서 9종:
    parse_agenda_xml / parse_agenda_details_xml / parse_meeting_info_xml /
    parse_personnel_xml / parse_aoi_xml / parse_compensation_xml /
    parse_corrections_xml / parse_retirement_pay_xml /
    parse_provisional_financial_statement (services 내 독립 모듈)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
import traceback
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import DartClientError, get_dart_client  # noqa: E402
from open_proxy_mcp.services.filing_search import search_filings_by_report_name  # noqa: E402
from open_proxy_mcp.services.provisional_financial_statement import (  # noqa: E402
    extract_metrics as _pfs_extract_metrics,
    parse_provisional_financial_statement,
)
from open_proxy_mcp.tools.parser import (  # noqa: E402
    parse_agenda_details_xml,
    parse_agenda_xml,
    parse_aoi_xml,
    parse_compensation_xml,
    parse_corrections_xml,
    parse_meeting_info_xml,
    parse_personnel_xml,
    parse_retirement_pay_xml,
)


UNIVERSE_PATHS = {
    "kospi200": ROOT / "wiki/architecture/audits/data/260506_universe_kospi_200.csv",
    "kosdaq100": ROOT / "wiki/architecture/audits/data/260506_universe_kosdaq_100.csv",
}


def _load_universe(name: str, start: int, count: int) -> list[tuple[str, str]]:
    path = UNIVERSE_PATHS.get(name)
    if not path:
        raise ValueError(f"unknown universe '{name}' (choose from {list(UNIVERSE_PATHS)})")
    rows: list[tuple[str, str]] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((row["ticker"], row["company"]))
    return rows[start:start + count]


# ── parser dispatch ──

def _agenda_titles(items: list[dict[str, Any]]) -> list[str]:
    titles: list[str] = []
    for item in items or []:
        t = (item.get("title") or "").strip()
        if t:
            titles.append(t)
        titles.extend(_agenda_titles(item.get("children", [])))
    return titles


def _has_topic(titles: list[str], keywords: tuple[str, ...]) -> bool:
    for title in titles:
        if any(kw in title for kw in keywords):
            return True
    return False


def _safe(call_label: str, fn, *args, **kwargs) -> dict[str, Any]:
    """Call parser, capture timing + exception."""
    t0 = time.time()
    try:
        result = fn(*args, **kwargs)
        return {
            "label": call_label,
            "status": "ok",
            "duration_ms": int((time.time() - t0) * 1000),
            "result": result,
        }
    except Exception as exc:
        return {
            "label": call_label,
            "status": "error",
            "duration_ms": int((time.time() - t0) * 1000),
            "error_type": type(exc).__name__,
            "error_msg": str(exc)[:300],
            "traceback": traceback.format_exc(limit=3)[:600],
        }


def _summarize_personnel(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") != "ok":
        return {"call_status": result.get("status"), "error": result.get("error_type")}
    parsed = result.get("result") or {}
    appts = parsed.get("appointments") or []
    return {
        "call_status": "ok",
        "appointments_count": len(appts),
        "with_career": sum(1 for a in appts if (a.get("career") or "").strip()),
        "with_birth": sum(1 for a in appts if a.get("birth_year")),
        "stats": parsed.get("statistics") or {},
    }


def _summarize_compensation(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") != "ok":
        return {"call_status": result.get("status"), "error": result.get("error_type")}
    parsed = result.get("result") or {}
    items = parsed.get("items") or []
    return {
        "call_status": "ok",
        "items_count": len(items),
        "categories": sorted({(it.get("category") or "") for it in items if it.get("category")}),
    }


def _summarize_aoi(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") != "ok":
        return {"call_status": result.get("status"), "error": result.get("error_type")}
    parsed = result.get("result") or {}
    amends = parsed.get("amendments") or []
    return {
        "call_status": "ok",
        "amendments_count": len(amends),
        "with_before": sum(1 for a in amends if (a.get("before") or "").strip()),
        "with_after": sum(1 for a in amends if (a.get("after") or "").strip()),
    }


def _summarize_retirement(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") != "ok":
        return {"call_status": result.get("status"), "error": result.get("error_type")}
    parsed = result.get("result") or {}
    amends = parsed.get("amendments") or []
    return {
        "call_status": "ok",
        "amendments_count": len(amends),
        "kinds": sorted({(a.get("kind") or "") for a in amends if a.get("kind")}),
    }


def _summarize_meeting_info(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") != "ok":
        return {"call_status": result.get("status"), "error": result.get("error_type")}
    parsed = result.get("result") or {}
    return {
        "call_status": "ok",
        "meeting_type": parsed.get("meeting_type"),
        "meeting_term": parsed.get("meeting_term"),
        "datetime_present": bool(parsed.get("datetime")),
        "location_present": bool(parsed.get("location")),
        "is_correction": parsed.get("is_correction", False),
    }


def _summarize_agenda(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") != "ok":
        return {"call_status": result.get("status"), "error": result.get("error_type")}
    parsed = result.get("result") or []
    titles = _agenda_titles(parsed)
    return {
        "call_status": "ok",
        "root_count": len(parsed),
        "total_titles": len(titles),
        "topics": {
            "has_director": _has_topic(titles, ("이사 선임", "이사선임", "사외이사 선임", "이사 후보")),
            "has_audit": _has_topic(titles, ("감사 선임", "감사위원", "감사위원회위원")),
            "has_compensation": _has_topic(titles, ("보수한도", "보수 한도", "이사 보수", "감사 보수")),
            "has_aoi": _has_topic(titles, ("정관 일부 변경", "정관변경", "정관 변경")),
            "has_retirement": _has_topic(titles, ("퇴직금", "임원 퇴직", "퇴직급여")),
            "has_financials": _has_topic(titles, ("재무제표 승인", "재무제표승인", "재무제표 보고")),
        },
        "titles_sample": titles[:6],
    }


def _summarize_agenda_details(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") != "ok":
        return {"call_status": result.get("status"), "error": result.get("error_type")}
    parsed = result.get("result") or []
    return {
        "call_status": "ok",
        "items_count": len(parsed),
        "with_body": sum(1 for it in parsed if (it.get("body") or "").strip()),
    }


def _summarize_corrections(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") != "ok":
        return {"call_status": result.get("status"), "error": result.get("error_type")}
    parsed = result.get("result")
    if parsed is None:
        return {"call_status": "ok", "is_correction": False}
    return {
        "call_status": "ok",
        "is_correction": parsed.get("is_correction", False),
        "items_count": len(parsed.get("items") or []),
        "has_reason": bool((parsed.get("reason") or "").strip()),
    }


def _summarize_provisional(result: dict[str, Any], metrics: dict[str, Any] | None) -> dict[str, Any]:
    if result.get("status") != "ok":
        return {"call_status": result.get("status"), "error": result.get("error_type")}
    parsed = result.get("result") or {}

    def _rows(parsed_dict: dict, scope_key: str, stmt_key: str) -> int:
        scope = parsed_dict.get(scope_key) or {}
        stmt = scope.get(stmt_key) or {}
        rows = stmt.get("rows") or []
        return len(rows)

    return {
        "call_status": "ok",
        "consolidated_bs_rows": _rows(parsed, "consolidated", "balance_sheet"),
        "consolidated_is_rows": _rows(parsed, "consolidated", "income_statement"),
        "separate_bs_rows": _rows(parsed, "separate", "balance_sheet"),
        "separate_is_rows": _rows(parsed, "separate", "income_statement"),
        "extraction_status": (metrics or {}).get("extraction_status") if metrics else None,
        "scope_used": (metrics or {}).get("scope_used") if metrics else None,
        "metric_keys_filled": sum(
            1 for k, v in (metrics or {}).items()
            if k.startswith("fy_") and v not in (None, "")
        ),
    }


# ── core: per-company audit ──

async def _audit_one(
    ticker: str,
    name: str,
    sem: asyncio.Semaphore,
    *,
    year: int,
    inter_call_sleep: float,
) -> dict[str, Any]:
    async with sem:
        t0 = time.time()
        try:
            client = get_dart_client()
            corp = await client.lookup_corp_code(ticker)
            if not corp:
                return {
                    "ticker": ticker, "name": name, "status": "no_corp",
                    "duration_s": round(time.time() - t0, 2),
                }
            corp_code = corp["corp_code"]

            # 검색: 2026 AGM 소집공고 (E type)
            await asyncio.sleep(inter_call_sleep)
            filings, _notices, error = await search_filings_by_report_name(
                corp_code=corp_code,
                bgn_de=f"{year}0101",
                end_de=f"{year}1231",
                pblntf_tys="E",
                keywords=("주주총회소집공고", "소집공고"),
            )
            if error:
                return {
                    "ticker": ticker, "name": name, "status": "search_error",
                    "error": str(error)[:200],
                    "duration_s": round(time.time() - t0, 2),
                }
            agm_filings = [
                f for f in filings
                if "주주총회소집공고" in (f.get("report_nm") or "")
            ]
            if not agm_filings:
                return {
                    "ticker": ticker, "name": name, "status": "no_agm_notice",
                    "duration_s": round(time.time() - t0, 2),
                }
            # latest: (rcept_dt, rcept_no) ascending → last
            agm_filings.sort(key=lambda r: (r.get("rcept_dt", ""), r.get("rcept_no", "")))
            latest = agm_filings[-1]
            rcept_no = latest["rcept_no"]

            await asyncio.sleep(inter_call_sleep)
            doc = await client.get_document_cached(rcept_no)
            text = doc.get("text", "") or ""
            html = doc.get("html", "") or ""

            # ── 9 parsers (in-memory) ──
            r_meeting = _safe("parse_meeting_info_xml", parse_meeting_info_xml, text, html=html)
            r_agenda = _safe("parse_agenda_xml", parse_agenda_xml, text, html=html)
            r_agenda_d = _safe("parse_agenda_details_xml", parse_agenda_details_xml, html)
            r_personnel = _safe("parse_personnel_xml", parse_personnel_xml, html)
            r_aoi = _safe("parse_aoi_xml", parse_aoi_xml, html)
            r_comp = _safe("parse_compensation_xml", parse_compensation_xml, html)
            r_corr = _safe("parse_corrections_xml", parse_corrections_xml, html)
            r_retire = _safe("parse_retirement_pay_xml", parse_retirement_pay_xml, html)
            r_pfs = _safe("parse_provisional_financial_statement",
                          parse_provisional_financial_statement, html)

            metrics = None
            if r_pfs.get("status") == "ok":
                try:
                    metrics = _pfs_extract_metrics(r_pfs["result"])
                except Exception as exc:
                    metrics = {"extraction_status": "error", "error": str(exc)[:200]}

            return {
                "ticker": ticker,
                "name": name,
                "corp_code": corp_code,
                "rcept_no": rcept_no,
                "rcept_dt": latest.get("rcept_dt"),
                "report_nm": latest.get("report_nm"),
                "status": "ok",
                "duration_s": round(time.time() - t0, 2),
                "doc_html_len": len(html),
                "doc_text_len": len(text),
                "agm_filings_count": len(agm_filings),
                "parsers": {
                    "meeting_info": _summarize_meeting_info(r_meeting),
                    "agenda": _summarize_agenda(r_agenda),
                    "agenda_details": _summarize_agenda_details(r_agenda_d),
                    "personnel": _summarize_personnel(r_personnel),
                    "aoi": _summarize_aoi(r_aoi),
                    "compensation": _summarize_compensation(r_comp),
                    "corrections": _summarize_corrections(r_corr),
                    "retirement_pay": _summarize_retirement(r_retire),
                    "provisional_fs": _summarize_provisional(r_pfs, metrics),
                },
            }
        except DartClientError as dexc:
            return {
                "ticker": ticker, "name": name, "status": "dart_error",
                "code": getattr(dexc, "code", ""), "error": str(dexc)[:200],
                "duration_s": round(time.time() - t0, 2),
            }
        except Exception as exc:
            return {
                "ticker": ticker, "name": name, "status": "exception",
                "error_type": type(exc).__name__, "error": str(exc)[:200],
                "traceback": traceback.format_exc(limit=3)[:600],
                "duration_s": round(time.time() - t0, 2),
            }


# ── G1 metrics aggregation ──

def _aggregate_g1(records: list[dict[str, Any]]) -> dict[str, Any]:
    """G1 = success rate per parser when topic is detected.

    For parsers with topic gating (personnel, aoi, compensation, retirement, provisional_fs):
        denominator = OK records where agenda topic flag is True
        numerator = denominator with parser status == 'ok' AND has_data
    For unconditional parsers (meeting_info, agenda, agenda_details, corrections):
        denominator = OK records
        numerator = denominator with parser status == 'ok' AND meaningful output
    """
    ok = [r for r in records if r.get("status") == "ok"]
    n_ok = len(ok)
    if not ok:
        return {"n_ok": 0, "note": "no successful records"}

    def _pct(num: int, den: int) -> float:
        return round(num * 100.0 / den, 1) if den else 0.0

    # Unconditional
    meeting_ok = sum(1 for r in ok if r["parsers"]["meeting_info"].get("call_status") == "ok"
                     and r["parsers"]["meeting_info"].get("meeting_type"))
    agenda_ok = sum(1 for r in ok if r["parsers"]["agenda"].get("call_status") == "ok"
                    and r["parsers"]["agenda"].get("total_titles", 0) >= 1)
    agenda_d_ok = sum(1 for r in ok if r["parsers"]["agenda_details"].get("call_status") == "ok"
                      and r["parsers"]["agenda_details"].get("items_count", 0) >= 1)
    corrections_ok = sum(1 for r in ok if r["parsers"]["corrections"].get("call_status") == "ok")

    # Topic-gated
    def _topic(r, key):
        return bool(r["parsers"]["agenda"].get("topics", {}).get(key))

    director_or_audit = [r for r in ok if _topic(r, "has_director") or _topic(r, "has_audit")]
    personnel_filled = sum(1 for r in director_or_audit
                           if r["parsers"]["personnel"].get("call_status") == "ok"
                           and r["parsers"]["personnel"].get("appointments_count", 0) >= 1)

    has_aoi = [r for r in ok if _topic(r, "has_aoi")]
    aoi_filled = sum(1 for r in has_aoi
                     if r["parsers"]["aoi"].get("call_status") == "ok"
                     and r["parsers"]["aoi"].get("amendments_count", 0) >= 1)

    has_comp = [r for r in ok if _topic(r, "has_compensation")]
    comp_filled = sum(1 for r in has_comp
                      if r["parsers"]["compensation"].get("call_status") == "ok"
                      and r["parsers"]["compensation"].get("items_count", 0) >= 1)

    has_retire = [r for r in ok if _topic(r, "has_retirement") or _topic(r, "has_aoi")]
    retire_filled = sum(1 for r in has_retire
                        if r["parsers"]["retirement_pay"].get("call_status") == "ok")

    has_fin = [r for r in ok if _topic(r, "has_financials")]
    pfs_filled = sum(1 for r in has_fin
                     if r["parsers"]["provisional_fs"].get("call_status") == "ok"
                     and r["parsers"]["provisional_fs"].get("metric_keys_filled", 0) >= 6)

    return {
        "n_ok": n_ok,
        "unconditional": {
            "meeting_info": {"ok": meeting_ok, "n": n_ok, "pct": _pct(meeting_ok, n_ok)},
            "agenda": {"ok": agenda_ok, "n": n_ok, "pct": _pct(agenda_ok, n_ok)},
            "agenda_details": {"ok": agenda_d_ok, "n": n_ok, "pct": _pct(agenda_d_ok, n_ok)},
            "corrections": {"ok": corrections_ok, "n": n_ok, "pct": _pct(corrections_ok, n_ok)},
        },
        "topic_gated": {
            "personnel_when_director_or_audit": {
                "ok": personnel_filled, "n": len(director_or_audit),
                "pct": _pct(personnel_filled, len(director_or_audit)),
            },
            "aoi_when_aoi": {"ok": aoi_filled, "n": len(has_aoi), "pct": _pct(aoi_filled, len(has_aoi))},
            "compensation_when_comp": {
                "ok": comp_filled, "n": len(has_comp), "pct": _pct(comp_filled, len(has_comp)),
            },
            "retirement_call_when_retire_or_aoi": {
                "ok": retire_filled, "n": len(has_retire), "pct": _pct(retire_filled, len(has_retire)),
            },
            "provisional_fs_when_financials": {
                "ok": pfs_filled, "n": len(has_fin), "pct": _pct(pfs_filled, len(has_fin)),
            },
        },
    }


# ── runner ──

async def _run(args: argparse.Namespace) -> int:
    universe = _load_universe(args.universe, args.start, args.count)
    print(f"[omnibus] universe={args.universe} range={args.start}:{args.start + args.count} "
          f"resolved={len(universe)} concurrency={args.concurrency} sleep={args.sleep}s",
          flush=True)

    sem = asyncio.Semaphore(args.concurrency)

    tasks = [
        _audit_one(ticker, name, sem, year=args.year, inter_call_sleep=args.sleep)
        for ticker, name in universe
    ]

    results: list[dict[str, Any]] = []
    t0 = time.time()
    for fut in asyncio.as_completed(tasks):
        r = await fut
        results.append(r)
        status = r.get("status")
        marker = "✓" if status == "ok" else f"✗{status}"
        elapsed = round(time.time() - t0, 1)
        print(f"  [{len(results)}/{len(universe)}] {r.get('ticker')} {r.get('name')} {marker} "
              f"({r.get('duration_s', 0)}s, total={elapsed}s)", flush=True)

    # G1 aggregation
    g1 = _aggregate_g1(results)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "universe": args.universe,
            "start": args.start,
            "count": args.count,
            "year": args.year,
            "concurrency": args.concurrency,
            "inter_call_sleep_s": args.sleep,
            "duration_s": round(time.time() - t0, 1),
            "generated_at": date.today().isoformat(),
        },
        "g1": g1,
        "records": results,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[omnibus] wrote {out_path}", flush=True)
    print(f"[omnibus] G1 summary: {json.dumps(g1, ensure_ascii=False)}", flush=True)
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--universe", required=True, choices=list(UNIVERSE_PATHS))
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--sleep", type=float, default=1.0,
                   help="회사 내 DART call 사이 sleep (sec)")
    p.add_argument("--output", required=True)
    return p.parse_args()


def main() -> int:
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    sys.exit(main())
