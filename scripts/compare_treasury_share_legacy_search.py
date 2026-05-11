from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import open_proxy_mcp.services.treasury_share as treasury_mod  # noqa: E402
from open_proxy_mcp.dart.client import DartClientError, get_dart_client  # noqa: E402
from open_proxy_mcp.services.filing_search import search_filings_by_report_name  # noqa: E402
from open_proxy_mcp.services.treasury_share import build_treasury_share_payload  # noqa: E402


UNIVERSE_FILES = (
    ROOT / "wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/universe_kospi50.csv",
    ROOT / "wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/universe_kosdaq10.csv",
)


def _load_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in UNIVERSE_FILES:
        with path.open(encoding="utf-8") as f:
            rows.extend(csv.DictReader(f))
    return rows


def _strip_usage(payload: dict[str, Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(payload, ensure_ascii=False))
    clone.pop("generated_at", None)
    data = clone.get("data") or {}
    data.pop("usage", None)
    return clone


async def _legacy_fetch_decisions(corp_code: str, bgn_de: str, end_de: str) -> tuple[dict[str, list[dict]], list[str]]:
    client = get_dart_client()

    async def safe(coro, label: str) -> tuple[list[dict[str, Any]], str | None]:
        try:
            res = await coro
            return res.get("list", []) or [], None
        except DartClientError as exc:
            return [], f"{label} 조회 실패: {exc.status}"

    acq_task = safe(client.get_treasury_acquisition(corp_code, bgn_de, end_de), "취득결정")
    dsp_task = safe(client.get_treasury_disposal(corp_code, bgn_de, end_de), "처분결정")
    trc_task = safe(client.get_treasury_trust_contract(corp_code, bgn_de, end_de), "신탁계약 체결결정")
    trt_task = safe(client.get_treasury_trust_termination(corp_code, bgn_de, end_de), "신탁계약 해지결정")

    async def cancelation_search():
        items, _notices, error = await search_filings_by_report_name(
            corp_code=corp_code,
            bgn_de=bgn_de,
            end_de=end_de,
            pblntf_tys=("B", "I"),
            keywords=treasury_mod._CANCELATION_KEYWORDS,
            strip_spaces=True,
        )
        if error:
            return [], f"자사주 소각결정 조회 실패: {error}"
        return items, None

    async def keyword_search(keywords, label):
        items, _notices, error = await search_filings_by_report_name(
            corp_code=corp_code,
            bgn_de=bgn_de,
            end_de=end_de,
            pblntf_tys="",
            keywords=keywords,
            strip_spaces=True,
        )
        if error:
            return [], f"{label} 조회 실패: {error}"
        return items, None

    (acq, w1), (dsp, w2), (trc, w3), (trt, w4), (ret, w5), (acq_res, w6), (dsp_res, w7), (trust_acq_status, w8), (trust_term_res, w9) = await asyncio.gather(
        acq_task,
        dsp_task,
        trc_task,
        trt_task,
        cancelation_search(),
        keyword_search(treasury_mod._ACQUISITION_RESULT_KEYWORDS, "자기주식취득결과보고서"),
        keyword_search(treasury_mod._DISPOSAL_RESULT_KEYWORDS, "자기주식처분결과보고서"),
        keyword_search(treasury_mod._TRUST_ACQ_STATUS_KEYWORDS, "신탁취득상황보고서"),
        keyword_search(treasury_mod._TRUST_TERM_RESULT_KEYWORDS, "신탁해지결과보고서"),
    )
    warnings = [w for w in (w1, w2, w3, w4, w5, w6, w7, w8, w9) if w]

    cancelation_rows = [treasury_mod._normalize_cancelation_row(item) for item in ret]
    cancelation_failures = await treasury_mod._enrich_cancelation_with_body(cancelation_rows)
    if cancelation_failures:
        warnings.append(f"자사주 소각결정 본문 파싱 실패 {cancelation_failures}건 — 소각 금액이 0으로 보일 수 있다.")
    raw_cnt = len(cancelation_rows)
    cancelation_rows = treasury_mod._dedupe_cancelation_rows(cancelation_rows)
    if len(cancelation_rows) < raw_cnt:
        warnings.append(f"[기재정정] 중복 {raw_cnt - len(cancelation_rows)}건을 제거해 소각 합산했다.")

    acq_res_rows = [treasury_mod._normalize_result_report(item, "acquisition_result") for item in acq_res]
    dsp_res_rows = [treasury_mod._normalize_result_report(item, "disposal_result") for item in dsp_res]
    trust_acq_status_rows = [treasury_mod._normalize_result_report(item, "trust_acquisition_status") for item in trust_acq_status]
    trust_term_res_rows = [treasury_mod._normalize_result_report(item, "trust_termination_result") for item in trust_term_res]

    fail_count = await treasury_mod._enrich_result_reports_with_body(
        acq_res_rows, dsp_res_rows, trust_acq_status_rows, trust_term_res_rows
    )
    if fail_count:
        warnings.append(f"결과보고서 본문 파싱 실패 {fail_count}건 — 합계가 0으로 보일 수 있다.")

    return {
        "acquisition": [treasury_mod._normalize_acquisition(item) for item in acq],
        "disposal": [treasury_mod._normalize_disposal(item) for item in dsp],
        "trust_contract": [treasury_mod._normalize_trust(item, "trust_contract", "자기주식 취득 신탁계약 체결 결정") for item in trc],
        "trust_termination": [treasury_mod._normalize_trust(item, "trust_termination", "자기주식 취득 신탁계약 해지 결정") for item in trt],
        "cancelation": cancelation_rows,
        "acquisition_result": acq_res_rows,
        "disposal_result": dsp_res_rows,
        "trust_acquisition_status": trust_acq_status_rows,
        "trust_termination_result": trust_term_res_rows,
    }, warnings


@contextmanager
def _legacy_fetch_patch() -> Any:
    original = treasury_mod._fetch_decisions
    treasury_mod._fetch_decisions = _legacy_fetch_decisions
    try:
        yield
    finally:
        treasury_mod._fetch_decisions = original


async def _run_one(company: str, timeout_sec: float) -> dict[str, Any]:
    current_started = time.perf_counter()
    current_payload = await asyncio.wait_for(
        build_treasury_share_payload(company, scope="summary", lookback_months=24),
        timeout=timeout_sec,
    )
    current_elapsed = time.perf_counter() - current_started

    with _legacy_fetch_patch():
        legacy_started = time.perf_counter()
        legacy_payload = await asyncio.wait_for(
            build_treasury_share_payload(company, scope="summary", lookback_months=24),
            timeout=timeout_sec,
        )
        legacy_elapsed = time.perf_counter() - legacy_started

    current_normalized = _strip_usage(current_payload)
    legacy_normalized = _strip_usage(legacy_payload)
    equal = current_normalized == legacy_normalized

    return {
        "company": company,
        "status_current": current_payload.get("status"),
        "status_legacy": legacy_payload.get("status"),
        "elapsed_current_sec": current_elapsed,
        "elapsed_legacy_sec": legacy_elapsed,
        "speedup_pct": ((legacy_elapsed - current_elapsed) / legacy_elapsed * 100.0) if legacy_elapsed else None,
        "equal_without_usage": equal,
    }


async def main(args: argparse.Namespace) -> None:
    rows = _load_rows()
    records: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, 1):
        record = await _run_one(row["company"], args.timeout_sec)
        records.append({"ticker": row["ticker"], **record})
        print(
            f"[compare {idx}/{len(rows)}] {row['ticker']} {row['company']} "
            f"equal={record['equal_without_usage']} speedup={record['speedup_pct']:.1f}%",
            flush=True,
        )

    equal_count = sum(1 for record in records if record["equal_without_usage"])
    payload = {
        "meta": {
            "sample": "KOSPI 50 + KOSDAQ 10",
            "row_count": len(rows),
        },
        "summary": {
            "equal_count": equal_count,
            "row_count": len(rows),
            "median_speedup_pct": sorted(record["speedup_pct"] for record in records if record["speedup_pct"] is not None)[len(records) // 2],
            "min_speedup_pct": min(record["speedup_pct"] for record in records if record["speedup_pct"] is not None),
            "max_speedup_pct": max(record["speedup_pct"] for record in records if record["speedup_pct"] is not None),
        },
        "records": records,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "wiki/architecture/audits/data/260511_perf_treasury_share_audit/legacy_search_compare_kospi50_kosdaq10.json",
    )
    parser.add_argument("--timeout-sec", type=float, default=180.0)
    asyncio.run(main(parser.parse_args()))
