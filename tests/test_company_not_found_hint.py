"""회사를 못 찾았을 때 「없다」로 끝내지 않는다.

DART 회사 목록에는 **현재 사명만** 있다. 사명을 바꾼 회사를 옛 이름으로 조회하면 한 건도 안
나오는데, 그때 「찾지 못했다」만 돌려주면 *회사가 없는 것*인지 *이름이 바뀐 것*인지 구분할 수
없다. 하필 사명 변경은 지배구조 분쟁 직후에 잦아서, 의결권 분석이 가장 필요한 국면과 겹친다.

실측: 「영풍정밀」 조회 실패 → 종목코드 036560 은 그대로이고 사명이 「케이젯정밀(주)」로 바뀐
것이었다. 고려아연 분쟁의 당사자라 조용히 넘어갈 회사가 아니었다.
"""

from __future__ import annotations

import ast
from pathlib import Path

from open_proxy_mcp.services.company import company_not_found_warning

SERVICES = Path(__file__).resolve().parents[1] / "open_proxy_mcp"


def test_it_points_at_the_way_out() -> None:
    """종목코드는 사명이 바뀌어도 유지된다 — 그게 탈출구라는 걸 문구가 말해야 한다."""
    msg = company_not_found_warning("영풍정밀")
    assert "영풍정밀" in msg
    assert "종목코드" in msg
    assert "사명" in msg


def test_the_listed_only_wording_stays_available() -> None:
    """treasury_share 처럼 「상장사」로 좁혀 말하던 곳의 어감을 잃지 않는다."""
    assert "상장사를 찾지 못했다" in company_not_found_warning("없는회사", listed_only=True)
    assert "회사를 찾지 못했다" in company_not_found_warning("없는회사")


def test_nobody_hardcodes_the_message_anymore() -> None:
    """같은 문구가 14곳에 흩어져 있었다 — 다음에 또 14곳을 고치는 일이 없도록 고정한다."""
    offenders = []
    for path in SERVICES.rglob("*.py"):
        if path.name == "company.py":       # helper 정의 + 영문 갈래가 사는 곳
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "해당하는 회사를 찾지 못했다" in node.value or "해당하는 상장사를 찾지 못했다" in node.value:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"company_not_found_warning() 을 쓸 것: {offenders}"
