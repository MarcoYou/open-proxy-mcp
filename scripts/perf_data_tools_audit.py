from __future__ import annotations

import argparse
import asyncio
import copy
import json
import statistics
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import open_proxy_mcp.services.provisional_financial_statement as pfs_mod  # noqa: E402
import open_proxy_mcp.tools.parser as parser_mod  # noqa: E402
import open_proxy_mcp.services.treasury_share as treasury_mod  # noqa: E402
from open_proxy_mcp.services.company import build_company_payload  # noqa: E402
from open_proxy_mcp.services.corp_gov_report import build_corp_gov_report_payload  # noqa: E402
from open_proxy_mcp.services.corporate_restructuring import build_corporate_restructuring_payload  # noqa: E402
from open_proxy_mcp.services.dilutive_issuance import build_dilutive_issuance_payload  # noqa: E402
from open_proxy_mcp.services.dividend_v2 import build_dividend_payload  # noqa: E402
from open_proxy_mcp.services.evidence import build_evidence_payload  # noqa: E402
from open_proxy_mcp.services.financial_metrics import build_financial_metrics_payload  # noqa: E402
from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload  # noqa: E402
from open_proxy_mcp.services.proxy_contest import build_proxy_contest_payload  # noqa: E402
from open_proxy_mcp.services.related_party_transaction import build_related_party_transaction_payload  # noqa: E402
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload  # noqa: E402
from open_proxy_mcp.services.treasury_share import build_treasury_share_payload  # noqa: E402
from open_proxy_mcp.services.value_up_v2 import build_value_up_payload  # noqa: E402


CaseFactory = Callable[[], Any]


def _strip_dynamic(payload: dict[str, Any]) -> dict[str, Any]:
    clone = copy.deepcopy(payload)
    data = clone.get("data")
    if isinstance(data, dict):
        data.pop("usage", None)
    return clone


def _summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") or {}
    summary: dict[str, Any] = {
        "status": payload.get("status"),
        "warning_count": len(payload.get("warnings") or []),
        "filing_count": data.get("filing_count"),
        "parsing_failures": data.get("parsing_failures"),
        "usage": data.get("usage"),
    }
    for key in (
        "summary",
        "event_count",
        "agenda_summary",
        "board_summary",
        "compensation_summary",
        "meeting_phase",
        "result_status",
        "availability_status",
    ):
        if key in data:
            summary[key] = data[key]
    return summary


async def _time_call(factory: CaseFactory) -> tuple[float, dict[str, Any]]:
    t0 = time.perf_counter()
    payload = await factory()
    return time.perf_counter() - t0, payload


async def _benchmark_case(factory: CaseFactory, warm_runs: int = 3) -> dict[str, Any]:
    cold_sec, cold_payload = await _time_call(factory)
    warm_times: list[float] = []
    warm_payload = cold_payload
    for _ in range(warm_runs):
        sec, warm_payload = await _time_call(factory)
        warm_times.append(sec)
    return {
        "cold_sec": cold_sec,
        "warm_runs": warm_runs,
        "warm_times_sec": warm_times,
        "warm_avg_sec": statistics.mean(warm_times) if warm_times else None,
        "payload_summary": _summarize_payload(warm_payload),
    }


class SoupCache:
    def __init__(self, original: Callable[..., Any]) -> None:
        self.original = original
        self.cache: dict[tuple[str, Any], Any] = {}

    def clear(self) -> None:
        self.cache.clear()

    def __call__(self, markup: Any = "", features: Any = None, *args: Any, **kwargs: Any) -> Any:
        key = (markup, features) if isinstance(markup, str) else None
        if key is not None and key in self.cache:
            return self.cache[key]
        soup = self.original(markup, features, *args, **kwargs)
        if key is not None:
            self.cache[key] = soup
        return soup


@contextmanager
def _cached_soup_patch() -> Any:
    orig_parser_bs = parser_mod.BeautifulSoup
    orig_pfs_bs = pfs_mod.BeautifulSoup
    parser_cache = SoupCache(orig_parser_bs)
    pfs_cache = SoupCache(orig_pfs_bs)
    parser_mod.BeautifulSoup = parser_cache
    pfs_mod.BeautifulSoup = pfs_cache
    try:
        yield parser_cache, pfs_cache
    finally:
        parser_mod.BeautifulSoup = orig_parser_bs
        pfs_mod.BeautifulSoup = orig_pfs_bs


@contextmanager
def _treasury_skip_body_patch() -> Any:
    orig_cancel = treasury_mod._enrich_cancelation_with_body
    orig_result = treasury_mod._enrich_result_reports_with_body

    async def _noop_cancel(*args: Any, **kwargs: Any) -> int:
        return 0

    async def _noop_result(*args: Any, **kwargs: Any) -> int:
        return 0

    treasury_mod._enrich_cancelation_with_body = _noop_cancel
    treasury_mod._enrich_result_reports_with_body = _noop_result
    try:
        yield
    finally:
        treasury_mod._enrich_cancelation_with_body = orig_cancel
        treasury_mod._enrich_result_reports_with_body = orig_result


async def _benchmark_experiment(
    label: str,
    factory: CaseFactory,
    patch_cm: Callable[[], Any],
    runs: int = 5,
) -> dict[str, Any]:
    await factory()
    baseline_times: list[float] = []
    baseline_payload = None
    for _ in range(runs):
        sec, baseline_payload = await _time_call(factory)
        baseline_times.append(sec)

    with patch_cm() as patch_state:
        experimental_times: list[float] = []
        experimental_payload = None
        for _ in range(runs):
            if isinstance(patch_state, tuple):
                for item in patch_state:
                    if hasattr(item, "clear"):
                        item.clear()
            sec, experimental_payload = await _time_call(factory)
            experimental_times.append(sec)

    baseline_avg = statistics.mean(baseline_times)
    experimental_avg = statistics.mean(experimental_times)
    speedup_pct = ((baseline_avg - experimental_avg) / baseline_avg * 100.0) if baseline_avg else 0.0
    return {
        "label": label,
        "baseline_times_sec": baseline_times,
        "experimental_times_sec": experimental_times,
        "baseline_avg_sec": baseline_avg,
        "experimental_avg_sec": experimental_avg,
        "speedup_pct": speedup_pct,
        "status_pair": [
            baseline_payload.get("status") if baseline_payload else None,
            experimental_payload.get("status") if experimental_payload else None,
        ],
        "payload_equal_without_usage": (
            _strip_dynamic(baseline_payload) == _strip_dynamic(experimental_payload)
            if baseline_payload and experimental_payload
            else False
        ),
        "baseline_summary": _summarize_payload(baseline_payload or {}),
        "experimental_summary": _summarize_payload(experimental_payload or {}),
    }


async def _build_evidence_case() -> dict[str, Any]:
    company = await build_company_payload("LG화학")
    recent = ((company.get("data") or {}).get("recent_filings") or [])
    if not recent:
        return {"status": "error", "warnings": ["recent_filings empty"], "data": {}}
    return await build_evidence_payload(rcept_no=recent[0]["rcept_no"])


BASELINE_CASES: list[tuple[str, CaseFactory]] = [
    ("company", lambda: build_company_payload("LG화학")),
    ("shareholder_meeting_notice_summary", lambda: build_shareholder_meeting_payload("LG화학", scope="summary", year=2026, meeting_type="annual")),
    ("shareholder_meeting_results", lambda: build_shareholder_meeting_payload("LG화학", scope="results", year=2026, meeting_type="annual")),
    ("ownership_structure_summary", lambda: build_ownership_structure_payload("LG화학", scope="summary")),
    ("financial_metrics_summary", lambda: build_financial_metrics_payload("LG화학", scope="summary", year=2025)),
    ("corp_gov_report_summary", lambda: build_corp_gov_report_payload("KT&G", scope="summary")),
    ("dividend_summary", lambda: build_dividend_payload("삼성전자", scope="summary", year=2025)),
    ("treasury_share_summary", lambda: build_treasury_share_payload("삼성전자", scope="summary", lookback_months=24)),
    ("value_up_summary", lambda: build_value_up_payload("KT&G", scope="summary")),
    ("corporate_restructuring_summary", lambda: build_corporate_restructuring_payload("LG화학", scope="summary", start_date="2024-01-01", end_date="2026-05-10")),
    ("dilutive_issuance_summary", lambda: build_dilutive_issuance_payload("LG화학", scope="summary", start_date="2024-01-01", end_date="2026-05-10")),
    ("proxy_contest_summary", lambda: build_proxy_contest_payload("고려아연", scope="summary")),
    ("related_party_transaction_summary", lambda: build_related_party_transaction_payload("삼성전자", scope="summary")),
    ("evidence", _build_evidence_case),
]


async def main(output_path: Path) -> None:
    baseline: dict[str, Any] = {}
    for label, factory in BASELINE_CASES:
        baseline[label] = await _benchmark_case(factory)

    experiments = {
        "shareholder_meeting_summary_soup_cache": await _benchmark_experiment(
            "shareholder_meeting_summary_soup_cache",
            lambda: build_shareholder_meeting_payload("LG화학", scope="summary", year=2026, meeting_type="annual"),
            _cached_soup_patch,
        ),
        "treasury_share_summary_skip_body_enrich": await _benchmark_experiment(
            "treasury_share_summary_skip_body_enrich",
            lambda: build_treasury_share_payload("삼성전자", scope="summary", lookback_months=24),
            _treasury_skip_body_patch,
        ),
    }

    payload = {
        "baseline_cases": baseline,
        "experiments": experiments,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(output_path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "wiki/architecture/audits/data/260510_perf_data_tools_audit/baseline_and_experiments.json",
    )
    args = parser.parse_args()
    asyncio.run(main(args.output))
