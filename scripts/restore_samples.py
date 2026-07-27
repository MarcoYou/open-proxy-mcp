#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""검증 표본을 DART 에서 재수집해 문서 캐시에 적재한다.

원문은 저장소에 두지 않는다(용량). 검증이 필요할 때 이 스크립트로 다시 받는다.
시스템 임시 디렉터리는 OS 가 정리하므로 "캐시에 있으니 재현된다"는 가정을 두면 안 된다.

DART 하드룰 (위반 시 24시간 IP 차단) — 옵션으로 바꿀 수 없게 고정한다:
  · 동시성 1  · 호출 사이 sleep 1.0s
  · status 020/011/012 또는 전송오류 감지 시 즉시 전체 중단

사용:
    python3 scripts/restore_samples.py
    python3 scripts/restore_samples.py --kind 소집공고 --limit 50
    python3 scripts/restore_samples.py --verify        # 이미 있는 것의 sha1 만 대조
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 표본 목록은 private 저장소에 둔다 — 어떤 회사를 언제 조사했는지가 드러나므로 public 금지.
# 경로는 환경변수로 덮어쓸 수 있다: OPM_SAMPLE_REGISTRY
REGISTRY = Path(os.environ.get("OPM_SAMPLE_REGISTRY")
                or ROOT.parent / "open-proxy-storage" / "samples" / "registry.json")
SLEEP = 1.0          # 하드룰 — 낮추지 말 것
CONCURRENCY = 1      # 하드룰 — 올리지 말 것


def cache_dir() -> Path:
    return Path(os.environ.get("OPM_CACHE_DIR") or (Path(tempfile.gettempdir()) / "opm_cache"))


def sha16(html: str) -> str:
    return hashlib.sha1(html.encode()).hexdigest()[:16]


def load_registry(kind: str | None, limit: int | None) -> list[dict]:
    if not REGISTRY.exists():
        sys.exit(f"레지스트리 없음: {REGISTRY}")
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    rows = reg.get("samples") or []
    if kind:
        rows = [r for r in rows if kind in (r.get("kind") or "")]
    return rows[:limit] if limit else rows


def verify_only(rows: list[dict]) -> None:
    cd = cache_dir()
    ok = miss = bad = 0
    for r in rows:
        p = cd / f"{r['rcept_no']}.json"
        if not p.exists():
            miss += 1
            continue
        try:
            html = json.loads(p.read_text(encoding="utf-8")).get("html", "")
        except Exception:
            bad += 1
            continue
        if r.get("sha1_16") and sha16(html) != r["sha1_16"]:
            bad += 1
            print(f"  ⚠ 본문 불일치 {r['rcept_no']} {r.get('company') or ''}")
        else:
            ok += 1
    print(f"대조 — 일치 {ok} · 없음 {miss} · 불일치·손상 {bad} (총 {len(rows)})")


async def restore(rows: list[dict]) -> None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import httpx
    from open_proxy_mcp.dart.client import DartClientError, get_dart_client

    client = get_dart_client()
    cd = cache_dir()
    abort = asyncio.Event()
    todo = [r for r in rows if not (cd / f"{r['rcept_no']}.json").exists()]
    print(f"레지스트리 {len(rows)}건 · 이미 있음 {len(rows)-len(todo)} · 받을 것 {len(todo)}건 "
          f"(예상 {len(todo)*SLEEP/60:.1f}분)")
    ok = mismatch = 0
    t0 = time.time()
    for i, r in enumerate(todo, 1):
        if abort.is_set():
            break
        try:
            doc = await client.get_document_cached(r["rcept_no"])
        except DartClientError as exc:
            if getattr(exc, "status", None) in ("020", "011", "012"):
                print(f"  ⛔ 중단 — DART status {exc.status} (과호출·차단)")
                abort.set()
            continue
        except (httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            print(f"  ⛔ 중단 — 전송오류 {type(exc).__name__}")
            abort.set()
            continue
        except Exception as exc:
            print(f"  · 건너뜀 {r['rcept_no']} ({type(exc).__name__})")
            continue
        finally:
            await asyncio.sleep(SLEEP)
        html = (doc or {}).get("html") or ""
        if not html:
            continue
        ok += 1
        if r.get("sha1_16") and sha16(html) != r["sha1_16"]:
            mismatch += 1
            print(f"  ⚠ 본문이 레지스트리와 다름 {r['rcept_no']} {r.get('company') or ''}")
        if i % 25 == 0:
            print(f"  {i}/{len(todo)} · 성공 {ok} · {time.time()-t0:.0f}s", flush=True)
    print(f"완료 — 적재 {ok}/{len(todo)} · 본문 불일치 {mismatch} · {time.time()-t0:.0f}s · "
          f"중단={abort.is_set()}")
    print(f"캐시 위치: {cd}")


def main() -> None:
    ap = argparse.ArgumentParser(description="검증 표본 재수집 (DART 하드룰 고정)")
    ap.add_argument("--kind", help="공시종류 필터 (예: 소집공고 · 사업보고서)")
    ap.add_argument("--limit", type=int, help="앞 N건만")
    ap.add_argument("--verify", action="store_true", help="재수집 없이 sha1 대조만")
    a = ap.parse_args()
    rows = load_registry(a.kind, a.limit)
    if not rows:
        sys.exit("대상 없음 — --kind 값을 확인하세요")
    if a.verify:
        verify_only(rows)
        return
    asyncio.run(restore(rows))


if __name__ == "__main__":
    main()
