"""Key data tools parsing success-rate audit runner.

회사 표본 기준으로 여러 data tool의 payload builder를 직접 호출해
status / latency / API call / filing meta를 수집한다.

주의:
- shareholder_meeting_notice는 별도 공시 표본 감사가 더 적합하므로 이 runner 기본 대상에서 제외한다.
- tool 하나씩 전체 universe를 도는 방식으로 rate limit을 관리한다.

예시:
    python3 scripts/parsing_success_rate_audit.py \
      --universe combined450 \
      --output wiki/architecture/audits/data/260517_parsing_success_rate_audit/baseline_company_sample_450.json

    python3 scripts/parsing_success_rate_audit.py \
      --universe combined450 --tools company,financial_metrics --start 0 --count 5 \
      --output /tmp/parsing_smoke.json
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Awaitable, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services.company import build_company_payload  # noqa: E402
from open_proxy_mcp.services.corp_gov_report import build_corp_gov_report_payload  # noqa: E402
from open_proxy_mcp.services.corporate_restructuring import build_corporate_restructuring_payload  # noqa: E402
from open_proxy_mcp.services.dilutive_issuance import build_dilutive_issuance_payload  # noqa: E402
from open_proxy_mcp.services.dividend_v2 import build_dividend_payload  # noqa: E402
from open_proxy_mcp.services.financial_metrics import build_financial_metrics_payload  # noqa: E402
from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload  # noqa: E402
from open_proxy_mcp.services.proxy_contest import build_proxy_contest_payload  # noqa: E402
from open_proxy_mcp.services.related_party_transaction import build_related_party_transaction_payload  # noqa: E402
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload  # noqa: E402
from open_proxy_mcp.services.treasury_share import build_treasury_share_payload  # noqa: E402
from open_proxy_mcp.services.value_up_v2 import build_value_up_payload  # noqa: E402


UniverseRow = dict[str, str]
Builder = Callable[[str], Awaitable[dict[str, Any]]]

DATA_DIR = ROOT / "wiki/architecture/audits/data/260517_parsing_success_rate_audit"
UNIVERSE_PATHS = {
    "kospi300": DATA_DIR / "universe_kospi300.csv",
    "kosdaq150": DATA_DIR / "universe_kosdaq150.csv",
    "recheck100_kospi50": DATA_DIR / "universe_kospi50_additional_nonoverlap.csv",
    "recheck100_kosdaq50": DATA_DIR / "universe_kosdaq50_additional_nonoverlap.csv",
}

LEGIT_NO_FILING_TOOLS = {
    "shareholder_meeting_results",
    "treasury_share",
    "value_up",
    "corp_gov_report",
    "dividend",
    "corporate_restructuring",
    "dilutive_issuance",
    "proxy_contest",
    "related_party_transaction",
}
SOFT_STATUSES = {"ambiguous", "partial", "requires_review", "conflict"}
HARD_STATUSES = {"error", "search_error"}


class ToolSpec(dict):
    name: str
    bucket: str
    builder: Builder


async def _build_company(query: str) -> dict[str, Any]:
    return await build_company_payload(query, max_recent_filings=10)


async def _build_shareholder_meeting_results(query: str) -> dict[str, Any]:
    return await build_shareholder_meeting_payload(
        query,
        year=2026,
        meeting_type="annual",
        scope="results",
    )


async def _build_ownership(query: str) -> dict[str, Any]:
    return await build_ownership_structure_payload(query, scope="summary")


async def _build_financial_metrics(query: str) -> dict[str, Any]:
    return await build_financial_metrics_payload(query, scope="summary", year=2025, years=3, consolidated=True)


async def _build_corp_gov_report(query: str) -> dict[str, Any]:
    return await build_corp_gov_report_payload(query, scope="summary", year=2025)


async def _build_dividend(query: str) -> dict[str, Any]:
    return await build_dividend_payload(query, scope="summary", year=2025, years=3)


async def _build_treasury_share(query: str) -> dict[str, Any]:
    return await build_treasury_share_payload(query, scope="summary", lookback_months=24)


async def _build_value_up(query: str) -> dict[str, Any]:
    return await build_value_up_payload(query, scope="summary", year=2026)


async def _build_restructuring(query: str) -> dict[str, Any]:
    return await build_corporate_restructuring_payload(query, scope="summary")


async def _build_dilutive(query: str) -> dict[str, Any]:
    return await build_dilutive_issuance_payload(query, scope="summary")


async def _build_proxy_contest(query: str) -> dict[str, Any]:
    return await build_proxy_contest_payload(query, scope="summary", lookback_months=12)


async def _build_rpt(query: str) -> dict[str, Any]:
    return await build_related_party_transaction_payload(query, scope="summary", include_details=False)


TOOL_SPECS: dict[str, ToolSpec] = {
    "company": {"name": "company", "bucket": "low", "builder": _build_company},
    "shareholder_meeting_results": {"name": "shareholder_meeting_results", "bucket": "heavy", "builder": _build_shareholder_meeting_results},
    "ownership_structure": {"name": "ownership_structure", "bucket": "heavy", "builder": _build_ownership},
    "financial_metrics": {"name": "financial_metrics", "bucket": "medium", "builder": _build_financial_metrics},
    "corp_gov_report": {"name": "corp_gov_report", "bucket": "medium", "builder": _build_corp_gov_report},
    "dividend": {"name": "dividend", "bucket": "low", "builder": _build_dividend},
    "treasury_share": {"name": "treasury_share", "bucket": "heavy", "builder": _build_treasury_share},
    "value_up": {"name": "value_up", "bucket": "heavy", "builder": _build_value_up},
    "corporate_restructuring": {"name": "corporate_restructuring", "bucket": "medium", "builder": _build_restructuring},
    "dilutive_issuance": {"name": "dilutive_issuance", "bucket": "medium", "builder": _build_dilutive},
    "proxy_contest": {"name": "proxy_contest", "bucket": "heavy", "builder": _build_proxy_contest},
    "related_party_transaction": {"name": "related_party_transaction", "bucket": "medium", "builder": _build_rpt},
}

DEFAULT_TOOLS = list(TOOL_SPECS.keys())
BUCKET_POLICY = {
    "low": {"chunk_size": 100, "chunk_sleep": 1.0, "concurrency": 8},
    "medium": {"chunk_size": 40, "chunk_sleep": 2.0, "concurrency": 4},
    "heavy": {"chunk_size": 15, "chunk_sleep": 3.0, "concurrency": 3},
}


def _load_csv(path: Path, market: str) -> list[UniverseRow]:
    rows: list[UniverseRow] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "ticker": (row.get("ticker") or "").strip(),
                    "company": (row.get("company") or "").strip(),
                    "sector": (row.get("sector") or "").strip(),
                    "industry": (row.get("industry") or "").strip(),
                    "market": market,
                }
            )
    return rows


def _load_universe(name: str) -> list[UniverseRow]:
    if name == "combined450":
        return _load_csv(UNIVERSE_PATHS["kospi300"], "KOSPI") + _load_csv(UNIVERSE_PATHS["kosdaq150"], "KOSDAQ")
    if name == "recheck100":
        return _load_csv(UNIVERSE_PATHS["recheck100_kospi50"], "KOSPI") + _load_csv(UNIVERSE_PATHS["recheck100_kosdaq50"], "KOSDAQ")
    if name == "kospi300":
        return _load_csv(UNIVERSE_PATHS["kospi300"], "KOSPI")
    if name == "kosdaq150":
        return _load_csv(UNIVERSE_PATHS["kosdaq150"], "KOSDAQ")
    raise ValueError(f"unknown universe: {name}")


def _parse_tools(raw: str) -> list[str]:
    if not raw.strip():
        return DEFAULT_TOOLS
    names = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [name for name in names if name not in TOOL_SPECS]
    if unknown:
        raise ValueError(f"unknown tools: {', '.join(unknown)}")
    return names


def _extract_usage_api_calls(payload: dict[str, Any]) -> int | None:
    data = payload.get("data") or {}
    usage = data.get("usage") or payload.get("usage") or {}
    value = usage.get("api_calls")
    return value if isinstance(value, int) else None


def _classify(tool: str, status: str) -> str:
    status = (status or "").lower()
    if status == "exact":
        return "success"
    if status == "no_filing":
        return "success" if tool in LEGIT_NO_FILING_TOOLS else "soft_fail"
    if status in SOFT_STATUSES:
        return "soft_fail"
    if status in HARD_STATUSES:
        return "hard_fail"
    return "hard_fail"


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[94]


def _iter_chunks(rows: list[UniverseRow], size: int):
    for i in range(0, len(rows), size):
        yield i // size, rows[i:i + size]


async def _run_tool_on_company(tool: str, spec: ToolSpec, row: UniverseRow) -> dict[str, Any]:
    t0 = time.perf_counter()
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            payload = await spec["builder"](row["company"])
            duration_ms = round((time.perf_counter() - t0) * 1000, 1)
            data = payload.get("data") or {}
            status = str(payload.get("status") or "").lower()
            audit_class = _classify(tool, status)
            warnings = payload.get("warnings") or []
            record = {
                "tool": tool,
                "ticker": row["ticker"],
                "company": row["company"],
                "market": row["market"],
                "sector": row["sector"],
                "industry": row["industry"],
                "status": status,
                "audit_class": audit_class,
                "duration_ms": duration_ms,
                "warnings_count": len(warnings),
                "no_filing": bool(data.get("no_filing", status == "no_filing")),
                "filing_count": data.get("filing_count"),
                "parsing_failures": data.get("parsing_failures"),
                "api_calls": _extract_usage_api_calls(payload),
            }
            if audit_class != "success" and warnings:
                record["warning_sample"] = warnings[:2]
            return record
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < 2:
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue

    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    return {
        "tool": tool,
        "ticker": row["ticker"],
        "company": row["company"],
        "market": row["market"],
        "sector": row["sector"],
        "industry": row["industry"],
        "status": "exception",
        "audit_class": "hard_fail",
        "duration_ms": duration_ms,
        "warnings_count": 0,
        "no_filing": False,
        "filing_count": None,
        "parsing_failures": None,
        "api_calls": None,
        "exception_type": type(last_exc).__name__ if last_exc else "Exception",
        "exception_message": str(last_exc)[:500] if last_exc else "",
    }


async def _run_chunk(tool: str, spec: ToolSpec, chunk: list[UniverseRow], concurrency: int) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(concurrency)

    async def _one(row: UniverseRow) -> dict[str, Any]:
        async with sem:
            return await _run_tool_on_company(tool, spec, row)

    return await asyncio.gather(*[_one(row) for row in chunk])


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"tools": {}, "markets": {}}
    by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_market_tool: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_tool[record["tool"]].append(record)
        by_market_tool[(record["market"], record["tool"])].append(record)

    def _one(group: list[dict[str, Any]]) -> dict[str, Any]:
        status_counts = Counter(r["status"] for r in group)
        audit_counts = Counter(r["audit_class"] for r in group)
        durations = [float(r["duration_ms"]) for r in group]
        api_calls = [r["api_calls"] for r in group if isinstance(r["api_calls"], int)]
        return {
            "total": len(group),
            "status_counts": dict(status_counts),
            "audit_counts": dict(audit_counts),
            "strict_success_rate": round(audit_counts.get("success", 0) * 100.0 / len(group), 1) if group else 0.0,
            "usable_rate": round((audit_counts.get("success", 0) + audit_counts.get("soft_fail", 0)) * 100.0 / len(group), 1) if group else 0.0,
            "hard_fail_rate": round(audit_counts.get("hard_fail", 0) * 100.0 / len(group), 1) if group else 0.0,
            "median_ms": round(statistics.median(durations), 1) if durations else 0.0,
            "p95_ms": round(_p95(durations), 1) if durations else 0.0,
            "avg_api_calls": round(sum(api_calls) / len(api_calls), 2) if api_calls else None,
        }

    for tool, group in sorted(by_tool.items()):
        summary["tools"][tool] = _one(group)
    for (market, tool), group in sorted(by_market_tool.items()):
        summary["markets"].setdefault(market, {})
        summary["markets"][market][tool] = _one(group)
    return summary


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", choices=["combined450", "recheck100", "kospi300", "kosdaq150"], default="combined450")
    parser.add_argument("--tools", default=",".join(DEFAULT_TOOLS))
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    rows = _load_universe(args.universe)
    if args.count > 0:
        rows = rows[args.start:args.start + args.count]
    elif args.start:
        rows = rows[args.start:]

    tools = _parse_tools(args.tools)
    all_records: list[dict[str, Any]] = []
    run_started = time.time()
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _checkpoint(final: bool = False) -> None:
        result = {
            "meta": {
                "script": "scripts/parsing_success_rate_audit.py",
                "universe": args.universe,
                "tools": tools,
                "n_companies": len(rows),
                "started_at_epoch": run_started,
                "finished_at_epoch": time.time() if final else None,
                "company_sample_note": "shareholder_meeting_notice는 별도 공시 표본 감사로 이 runner 기본 대상에서 제외함",
                "complete": final,
            },
            "records": all_records,
            "summary": _summarize(all_records),
        }
        target = out_path if final else out_path.with_suffix(out_path.suffix + ".partial")
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    for tool in tools:
        spec = TOOL_SPECS[tool]
        policy = BUCKET_POLICY[spec["bucket"]]
        tool_started = time.time()
        print(f"[audit] tool={tool} bucket={spec['bucket']} companies={len(rows)}", flush=True)
        for chunk_idx, chunk in _iter_chunks(rows, policy["chunk_size"]):
            print(f"[audit] tool={tool} chunk={chunk_idx + 1} size={len(chunk)}", flush=True)
            chunk_records = await _run_chunk(tool, spec, chunk, concurrency=policy["concurrency"])
            all_records.extend(chunk_records)
            _checkpoint(final=False)
            if (chunk_idx + 1) * policy["chunk_size"] < len(rows):
                await asyncio.sleep(policy["chunk_sleep"])
        print(f"[audit] tool={tool} done in {round(time.time() - tool_started, 1)}s", flush=True)

    _checkpoint(final=True)
    result = json.loads(out_path.read_text(encoding="utf-8"))
    print(f"[audit] wrote {out_path}", flush=True)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
