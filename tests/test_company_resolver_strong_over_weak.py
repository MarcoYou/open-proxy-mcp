"""약한 매칭이 강한 매칭을 가로채면 안 된다.

「지에스」는 부분일치로 「지에스이」에 먼저 걸려 역음차 경로를 못 타고 「GS」를 영영
못 찾고 있었다. 상장사 3,967개 질의 A/B 에서 이 순서 교정으로 바뀐 것은 「지에스」·
「에스케이」 둘뿐이고 나머지는 그대로였다.
"""

from __future__ import annotations

import pytest

from open_proxy_mcp.company_resolver import CompanyResolver


def _corps() -> list[dict]:
    return [
        {"corp_code": "00000001", "corp_name": "GS", "stock_code": "078930", "corp_eng_name": "GS"},
        {"corp_code": "00000002", "corp_name": "지에스이", "stock_code": "053050", "corp_eng_name": "GSE"},
        {"corp_code": "00000003", "corp_name": "JYP Ent.", "stock_code": "035900", "corp_eng_name": "JYP"},
    ]


@pytest.fixture
def resolver() -> CompanyResolver:
    return CompanyResolver(_corps(), {})


def _picked(resolver: CompanyResolver, query: str) -> tuple[str, str] | None:
    found = resolver.search(query)
    if not found:
        return None
    return found[0]["corp_name"], (found[0].get("_resolution") or {}).get("match_kind")


def test_transliteration_beats_a_partial_name_collision(resolver: CompanyResolver) -> None:
    """「지에스」는 「지에스이」의 앞부분이기도 하지만 「GS」의 음차다 — 음차가 이긴다."""
    assert _picked(resolver, "지에스") == ("GS", "official")


def test_the_exact_longer_name_still_wins_for_itself(resolver: CompanyResolver) -> None:
    assert _picked(resolver, "지에스이") == ("지에스이", "official")


def test_a_weak_transliteration_hit_is_kept_when_nothing_stronger_exists(resolver: CompanyResolver) -> None:
    """「제이와이피」는 원문으로 아무것도 안 걸리고 'jyp' 토큰으로만 닿는다 — 버리면 안 된다."""
    assert _picked(resolver, "제이와이피") is not None
    assert _picked(resolver, "제이와이피")[0] == "JYP Ent."


def test_a_weak_original_hit_survives_when_no_transliteration_is_stronger(resolver: CompanyResolver) -> None:
    """강한 대안이 없으면 종전대로 약한 결과를 낸다 — 다만 응답이 추정임을 밝힌다."""
    found = resolver.search("지에스이주식회사")
    assert found and found[0]["corp_name"] == "지에스이"
