"""각주 해소 정밀도 골든 회귀 테스트 (260709 footnote_qa 300사 검수 산물).

이번 세션이 잡은 5개 오답 유형(승인한도 셀 마커에 무관한 소송·스톡옵션·표조각·타인 각주를
"원문 각주"로 자신있게 노출)이 게이트 키워드·정규식을 나중에 건드릴 때 조용히 되살아나는 걸
막는다. director_board.py의 5중 게이트(_fn_topic_ok·인물 disambiguation·문장완결성·표조각
필터·dedup) 회귀 감시.

판정: 각 회사 build_director_board_payload → data_quality_flags 중 resolved_text(원문 각주로
확정 노출)만 검사. raw_text_excerpt(원문 발췌 폴백)는 "자동추출 실패, 직접확인" 라벨이라
독자를 오도하지 않으므로 검사 대상 아님(오답 0이 목표지 raw 최소화가 아님).

실행: python3 scripts/spot_footnote_golden.py   (DART 6사 라이브 — 순차, rate-limit 안전)
종료코드 0=전부 PASS, 1=하나라도 FAIL.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services.director_board import build_director_board_payload  # noqa: E402


def _resolved(flags: list[dict]) -> list[dict]:
    """resolved_text가 실제로 붙은(= '원문 각주'로 확정 노출된) 플래그만."""
    return [f for f in flags if f.get("resolved_text")]


async def _flags(company: str, scope: str = "summary") -> list[dict]:
    p = await build_director_board_payload(company, scope=scope, lookback_years=3)
    return (p.get("data") or {}).get("data_quality_flags") or []


async def run() -> int:
    results: list[tuple[bool, str]] = []

    def check(passed: bool, label: str) -> None:
        results.append((passed, label))

    # ── NEGATIVE: 아래 텍스트가 resolved로 올라오면 오답 = FAIL ──────────────
    # 1) 한국가스공사 — 소송충당부채/특수관계자거래 각주가 감사위원 보수로 둔갑하면 안 됨.
    gas = _resolved(await _flags("한국가스공사"))
    bad_gas = [f for f in gas if re.search(r"소송|충당부채|특수관계", f["resolved_text"])]
    check(not bad_gas, f"한국가스공사: 소송/특수관계 각주 resolved 없음 (발견 {len(bad_gas)})")

    # 2) 보로노이 — 승인한도 셀에 스톡옵션/액면분할 각주가 붙으면 안 됨.
    voro = _resolved(await _flags("보로노이"))
    bad_voro = [f for f in voro if f.get("scope") == "compensation"
                and re.search(r"스톡옵션|주식매수선택권|액면분할|무상증자", f["resolved_text"])]
    check(not bad_voro, f"보로노이: 승인한도에 스톡옵션/액면분할 각주 없음 (발견 {len(bad_voro)})")

    # 3) BGF리테일 — 'N N N'(표 셀 숫자 3연속) 표조각이 각주로 올라오면 안 됨.
    bgf = _resolved(await _flags("BGF리테일"))
    bad_bgf = [f for f in bgf if re.search(r"\d+\s+\d+\s+\d+", f["resolved_text"])]
    check(not bad_bgf, f"BGF리테일: 표조각(N N N) 각주 resolved 없음 (발견 {len(bad_bgf)})")

    # 4) SK — 이성형 개인 각주에 조대식/장동현(타인) 각주가 귀속되면 안 됨.
    sk = _resolved(await _flags("SK"))
    bad_sk = [f for f in sk if f.get("scope") == "individual" and f.get("subject") == "이성형"
              and re.search(r"조대식|장동현", f["resolved_text"])]
    check(not bad_sk, f"SK 이성형: 조대식/장동현 각주 귀속 없음 (발견 {len(bad_sk)})")

    # ── POSITIVE: 정확한 모범 각주는 resolved로 유지돼야 PASS ────────────────
    # 5) NAVER — 최수연 RSU(제한조건부주식) 각주 유지.
    nav = _resolved(await _flags("NAVER"))
    ok_nav = any(re.search(r"RSU|제한조건부주식", f["resolved_text"]) for f in nav)
    check(ok_nav, "NAVER: RSU(제한조건부주식) 각주 resolved 유지")

    # 6) SK바이오팜 — 이동훈 22,435주 PSU vs 정지영 8,763주 LTI 연도별 구분 유지.
    skb = _resolved(await _flags("SK바이오팜"))
    ok_lee = any("22,435" in f["resolved_text"] for f in skb)
    ok_jung = any("8,763" in f["resolved_text"] for f in skb)
    check(ok_lee and ok_jung,
          f"SK바이오팜: 이동훈 22,435 / 정지영 8,763 유지 (이동훈={ok_lee} 정지영={ok_jung})")

    # 7) SK하이닉스 — 150/200/200억 승인한도 각주 유지.
    hy = _resolved(await _flags("SK하이닉스"))
    ok_150 = any("150억" in f["resolved_text"] for f in hy)
    ok_200 = any("200억" in f["resolved_text"] for f in hy)
    check(ok_150 and ok_200,
          f"SK하이닉스: 150억/200억 승인한도 각주 유지 (150={ok_150} 200={ok_200})")

    # ── 결과 ────────────────────────────────────────────────────────────
    print("\n골든 각주 회귀 테스트")
    print("=" * 60)
    fails = 0
    for passed, label in results:
        print(f"  {'✅ PASS' if passed else '❌ FAIL'}  {label}")
        if not passed:
            fails += 1
    print("=" * 60)
    print(f"  {len(results) - fails}/{len(results)} PASS" + (f" · {fails} FAIL" if fails else " · 전부 통과"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
