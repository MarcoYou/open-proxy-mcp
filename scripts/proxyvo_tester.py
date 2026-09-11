#!/usr/bin/env python3
"""Reproducible, human-unreviewed proxy voting experiments over pilot HTTP MCP.

Usage: uv run python scripts/proxyvo_tester.py --help
No command runs a model except preflight, run, and score. No command transmits votes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
import httpx

from open_proxy_mcp.harness.experiment_config import ExperimentError, Ledger, read_plan, digest
from open_proxy_mcp.harness.experiment import prepare, run_jobs, schedule, verify_manifest
from open_proxy_mcp.harness.experiment_model import ResponsesModel
from open_proxy_mcp.harness.experiment_report import report, score
from open_proxy_mcp.harness.checkpoint import CheckpointError


async def execute(args):
    if args.env_file:
        load_dotenv(args.env_file, override=False)
    plan = read_plan(args.plan)
    if args.command == 'validate':
        return {'valid': True, 'plan_sha256': digest(plan.model_dump()), 'scheduled': len(schedule(plan)),
                'model_api_calls': 0, 'mcp_calls': 0, 'supported_scope': 'election_structure'}
    if not args.out:
        raise ExperimentError('output_directory_required')
    ledger = Ledger(args.out)
    if args.command == 'prepare':
        manifest = await prepare(plan, ledger)
        return {'prepared': True, 'scheduled': len(manifest['schedule']), 'model_api_calls': 0}
    if args.command == 'preflight':
        verify_manifest(plan, ledger)
        run_id = uuid.uuid4().hex
        states = []
        async with httpx.AsyncClient(follow_redirects=False) as client:
            for spec in plan.models:
                prefix = f'preflight/{run_id}/{spec.id}'
                model = ResponsesModel(spec, plan.budget, ledger, prefix, client)
                try:
                    response = await model.complete({'question': 'Return exactly {"ok":true} as JSON.',
                        'output_schema': {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
                            'required': ['ok'], 'additionalProperties': False}}, stage='probe')
                    status = 'ready' if response == {'ok': True} else 'unexpected_probe_output'
                except Exception as error:
                    status = error.code if hasattr(error, 'code') else 'probe_failed'
                states.append({'model': spec.id, 'effort': spec.effort,
                               'output_mode': spec.output_mode, 'status': status})
        ledger.write(f'preflight/{run_id}/result.json', states)
        return {'models': states, 'quality_evaluation': False}
    if args.command == 'run':
        summary = await run_jobs(plan, ledger, limit=args.limit, retry_failed=args.retry_failed, resume=args.resume)
        return {**summary, **report(ledger)}
    if args.command == 'score':
        summary = await score(plan, ledger)
        return {**summary, **report(ledger)}
    return report(ledger)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate', 'prepare', 'preflight', 'run', 'score', 'report'])
    parser.add_argument('--plan', type=Path, required=True, help='JSON experiment specification')
    parser.add_argument('--out', type=Path, help='Private run directory outside this public repository')
    parser.add_argument('--env-file', type=Path, help='Load credential/endpoint environment, never model context')
    parser.add_argument('--limit', type=int, help='Maximum additional jobs this invocation; pending jobs remain visible')
    recovery = parser.add_mutually_exclusive_group()
    recovery.add_argument('--retry-failed', action='store_true', help='New independent attempt; preserve all old attempts')
    recovery.add_argument('--resume', action='store_true', help='Resume interrupted attempts from completed-call receipts')
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    # HTTP SDK 로그에 인증 URL이나 응답 오류 원문이 노출되지 않게 한다.
    logging.disable(logging.CRITICAL)
    try:
        result = asyncio.run(execute(args))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        print(json.dumps({'status': 'interrupted', 'resume': 'use --resume; uncertain calls require a new --retry-failed attempt'}))
        return 130
    except (ExperimentError, CheckpointError) as error:
        print(json.dumps({'status': 'error', 'code': str(error)}))
        return 2
    except Exception:
        print(json.dumps({'status': 'error', 'code': 'experiment_failed'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
