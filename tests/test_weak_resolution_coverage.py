"""회사를 해석하는 서비스는 추정 사실을 반드시 응답에 실어야 한다 — 드리프트 방지.

해석기의 `confidence` 를 23개 서비스가 전부 버리고 있던 것이 「지에스 → 지에스이」 조용한
오답의 원인이었다. 전파 경로는 둘뿐이다: `ToolEnvelope`(자동) 또는 `declare_weak_resolution`
(dict 를 직접 만드는 서비스가 진입점에서 호출). 새 서비스가 둘 다 안 쓰면 여기서 걸린다.
"""

from __future__ import annotations

from pathlib import Path

_SERVICES = Path(__file__).resolve().parents[1] / "open_proxy_mcp" / "services"


def _resolves_companies(source: str) -> bool:
    return "resolve_company_query" in source or "_resolve_match" in source


def test_every_company_resolving_service_declares_weak_matches() -> None:
    missing = []
    for path in sorted(_SERVICES.glob("*.py")):
        source = path.read_text()
        if not _resolves_companies(source):
            continue
        if "ToolEnvelope" in source or "declare_weak_resolution" in source:
            continue
        missing.append(path.name)
    assert not missing, (
        f"{missing} 가 회사를 해석하면서 추정 사실을 전파하지 않는다 — "
        "ToolEnvelope 를 쓰거나 진입점을 declare_weak_resolution 으로 감싸라."
    )


def test_the_hand_built_services_wrap_their_entry_point() -> None:
    """dict 를 직접 만드는 서비스는 return 이 여러 곳이라 진입점 래핑만이 안전하다."""
    for name, entry in (
        ("valuation.py", "build_valuation_payload"),
        ("asset_holdings.py", "build_asset_holdings_payload"),
        ("screener.py", "build_screener_payload"),
    ):
        source = (_SERVICES / name).read_text()
        assert f"async def _{entry}_impl(" in source, name
        assert f"return declare_weak_resolution(await _{entry}_impl(" in source, name
