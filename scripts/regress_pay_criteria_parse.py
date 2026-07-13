"""pay_criteria 파서 회귀 — 이름복원/grid-1회/pre-pass 변경이 유니버스 자기일치를 회귀시키지
않는지 **네트워크 0콜**로 확인(260713). 이전 수집(results.jsonl)의 rcept_no로 디스크 캐시 원문만
로드해 재파싱하고, 변경 전(results.jsonl에 저장된 reconciliation) vs 변경 후를 전수 비교한다.

디스크 캐시(opm_cache/{rcept}.json)가 없는 회사는 skip(재fetch 금지 — 하드룰). 오늘 수집분이라 TTL 내.
"""
from __future__ import annotations
import os, sys, json, tempfile
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\Projects\open-proxy-mcp")
for line in open(ROOT / ".env.local", encoding="utf-8"):
    if line.startswith("DART_API_KEY="):
        os.environ["OPENDART_API_KEY"] = line.split("=", 1)[1].strip()
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.executive_pay import parse_executive_pay

OUT = Path(os.environ.get("OPM_VPAY_OUT") or (Path(tempfile.gettempdir()) / "opm_vpay_validation"))
JSONL = OUT / "results.jsonl"


def main():
    client = get_dart_client()
    recs = [json.loads(l) for l in open(JSONL, encoding="utf-8") if l.strip()]
    parsed = [r for r in recs if r.get("status") == "parsed" and r.get("rcept_no")]

    tot_check_before = tot_ok_before = 0
    tot_check_after = tot_ok_after = 0
    skipped = 0
    fixed_from_zero = []   # 자기일치 checkable가 0→>0 (병합버그로 대조불가였던 회사)
    regressed = []          # rate가 유의미하게 떨어진 회사
    n = 0
    for r in parsed:
        rc = r["rcept_no"]
        disk = client._load_from_disk(rc)      # 디스크 캐시만(네트워크 0)
        if not disk:
            skipped += 1
            continue
        n += 1
        html = disk.get("html") or ""
        text = disk.get("text") or ""
        out = parse_executive_pay(html, text)
        rec_before = r.get("reconciliation") or {}
        cb, ob = rec_before.get("checkable") or 0, rec_before.get("consistent") or 0
        rec_after = out.get("reconciliation") or {}
        ca, oa = rec_after.get("checkable") or 0, rec_after.get("consistent") or 0
        tot_check_before += cb; tot_ok_before += ob
        tot_check_after += ca; tot_ok_after += oa
        if cb == 0 and ca > 0:
            fixed_from_zero.append((r["company"], f"{oa}/{ca}"))
        # 회귀: 전에 대조되던 회사가 후에 rate 하락(checkable 유지되며 consistent 감소)
        rate_b = ob / cb if cb else None
        rate_a = oa / ca if ca else None
        if rate_b is not None and rate_a is not None and rate_a < rate_b - 0.01:
            regressed.append((r["company"], f"{ob}/{cb} → {oa}/{ca}"))

    print(f"재파싱(디스크캐시): {n}사  skip(캐시없음)={skipped}")
    print(f"자기일치 BEFORE: {tot_ok_before}/{tot_check_before} "
          f"({tot_ok_before/tot_check_before*100:.1f}%)" if tot_check_before else "n/a")
    print(f"자기일치 AFTER : {tot_ok_after}/{tot_check_after} "
          f"({tot_ok_after/tot_check_after*100:.1f}%)" if tot_check_after else "n/a")
    print(f"\n병합버그 복구(checkable 0→>0): {len(fixed_from_zero)}사")
    for c, s in fixed_from_zero[:40]:
        print(f"  + {c}: {s}")
    print(f"\n회귀(rate 하락): {len(regressed)}사")
    for c, s in regressed[:40]:
        print(f"  ! {c}: {s}")


if __name__ == "__main__":
    main()
