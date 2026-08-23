#!/usr/bin/env python3
"""정기보고서 제출 법인 명부 수집 — 패키지 동봉본(JSON) 생성. DART ~183콜.

260823 사고의 재발 방지. 명부는 런타임에 처음 만들 때 **183 API콜(약 3분)** 이 든다.
그걸 요청 경로에서 돌다 프록시 타임아웃에 걸려 502 가 났고, 끊기니 저장을 못 해
다음 요청도 같은 3분을 반복하는 **영영 안 낫는 고리**가 됐다(fix: 2c228105).

백그라운드로 옮겨 502 는 막았지만, **배포 직후 몇 분간 명부가 비는 창**은 남는다.
그동안 비상장 금융사 55곳이 안 열리고 동명 법인이 AMBIGUOUS 로 남는다.
동봉본이 있으면 그 창이 아예 없다 — 부팅 즉시 명부를 갖는다.

명부는 **분기 공시 시즌에만 늘어나는 느린 데이터**라 월 1회 갱신이면 충분하다.
법령 corpus·WICS 와 같은 패턴이다.

★ 명부를 금융사로 좁히면 안 된다. 두 가지 일을 하는데:
    ① 비상장 개방 판정 — 금융업만 쓴다(마스터 지시, d15c1de1)
    ② **동명 법인 가르기** — 국민은행 원장 3건 중 살아 있는 하나를 고른다. 상장사에도 걸린다.
  ②가 전 종목을 필요로 하므로 전량 모은다.

실행:
  python3 scripts/refresh_periodic_filers.py            # 수집 + JSON 쓰기
  python3 scripts/refresh_periodic_filers.py --dry      # 수집만, 쓰기 없음
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

OUT = ROOT / "open_proxy_mcp" / "data" / "dart" / "periodic_filers.json"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="쓰기 없이 수집·검증만")
    a = ap.parse_args()

    from open_proxy_mcp.dart.client import _FILERS_MIN_EXPECTED, get_dart_client

    client = get_dart_client()
    print("DART 정기보고서 명부 수집 — 400일치, 85일 창 (약 183콜)", flush=True)
    filers = await client._fetch_periodic_filers()

    # 무결성: 덜 모인 것을 쓰면 **명부에 없는 회사가 조용히 닫힌다.**
    #   전부-아니면-전무가 맞다(런타임 규칙 _FILERS_MIN_EXPECTED 와 같은 문턱).
    if len(filers) < _FILERS_MIN_EXPECTED:
        print(f"FAIL: {len(filers):,}건뿐 — {_FILERS_MIN_EXPECTED:,} 미만이면 덜 모인 것으로 본다")
        return 1

    payload = {
        "meta": {
            "count": len(filers),
            "refreshed_at": date.today().strftime("%Y-%m-%d"),
            "source": "DART list.json pblntf_ty=A (정기공시), 최근 400일",
            "note": "corp_code → 최근 정기보고서 접수일(YYYYMMDD). 런타임은 키만 쓴다.",
            "names": "담지 않는다 — 이름은 corp_codes 원장(7일 갱신)에 있고 corp_code 로 잇는다. "
                     "여기 또 담으면 월 1회인 이쪽이 더 낡아 두 곳이 어긋난다.",
        },
        "filers": dict(sorted(filers.items())),
    }
    print(f"수집 완료: {len(filers):,}사 · 최근 접수일 {max(filers.values())}")

    if a.dry:
        print("--dry — 쓰기 생략")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"저장: {OUT.relative_to(ROOT)} ({OUT.stat().st_size/1024:.0f}KB)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
