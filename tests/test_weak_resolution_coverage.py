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


def _valuation_payload(*, envelope_warnings: list[str], data_warnings: list[str]) -> dict:
    """`_render_md` 가 요구하는 최소 payload. 값은 렌더가 죽지 않을 정도만."""
    return {
        "subject": "현대자동차",
        "warnings": envelope_warnings,          # ← declare_weak_resolution 이 여기 붙인다
        "data": {
            "fiscal_year": 2025, "price_krw": 395500, "sector_class": "general",
            "warnings": data_warnings,
            "multiples": {"per_fy0": 5.1, "per_ttm": 4.8, "pbr_mrq": 0.6,
                          "pbr_basis": "MRQ", "dividend_yield_pct": 3.0},
            "inputs": {"eps_fy0_krw": 77000, "eps_ttm_krw": 82000, "bps_krw": 650000,
                       "roe_pct": 11.0, "dps_krw": 11500, "net_income_fy0_krw": 1,
                       "net_income_ttm_krw": 1, "controlling_equity_krw": 1,
                       "shares_common": 1, "shares_total": 1, "common_market_cap_krw": 1},
        },
    }


def test_weak_resolution_reaches_the_rendered_markdown() -> None:
    """payload 에 실렸는지가 아니라 **사용자가 읽는 글에 나오는지**를 잰다.

    위 두 테스트는 payload 를 재고 통과했는데, `valuation` 의 md 렌더러는 `data.warnings`
    만 읽고 봉투(`payload.warnings`)를 버렸다. 그래서 「현대」를 물으면 28곳 중 하나를
    고른 사실이 **아무 경고 없이** 완결된 밸류에이션으로 나왔다 — 틀린 회사를 조용히 분석한다.
    계약(payload)이 아니라 동작(출력)으로 잰다.
    """
    from open_proxy_mcp.services.price_multiple_data import _render_md
    weak = "「현대」를 **현대자동차**(으)로 추정했습니다 — 이름이 정확히 일치하지 않습니다 (다른 후보 28곳)."

    out = _render_md(_valuation_payload(envelope_warnings=[weak], data_warnings=[]))
    assert weak in out, "봉투 경고만 있을 때 렌더에 안 나온다"

    # 데이터 경고가 있어도 봉투 경고가 가려지면 안 된다 — `or` 단락으로 통째로 사라졌던 자리.
    out2 = _render_md(_valuation_payload(envelope_warnings=[weak], data_warnings=["스케일가드 주의"]))
    assert weak in out2, "데이터 경고가 봉투 경고를 가린다"
    assert "스케일가드 주의" in out2, "데이터 경고가 사라졌다"

    # 반대 어형 — 경고가 없으면 빈 「주의」 절을 만들지 않는다.
    out3 = _render_md(_valuation_payload(envelope_warnings=[], data_warnings=[]))
    assert "## 주의" not in out3


def test_weak_resolution_reaches_the_explain_scope_too() -> None:
    """scope='explain' 렌더러는 조건은 둘 다 보면서 출력은 `or` 로 하나만 봤다.

    데이터 경고가 하나라도 있으면 봉투 경고가 통째로 사라진다 — 같은 결함의 다른 자리.
    """
    from open_proxy_mcp.tools.price_multiple_data import _render_explain_firm
    weak = "「현대」를 **현대자동차**(으)로 추정했습니다 — 이름이 정확히 일치하지 않습니다 (다른 후보 28곳)."
    p = _valuation_payload(envelope_warnings=[weak], data_warnings=["스케일가드 주의"])
    p["data"]["data_quality"] = {"scale_tier": "ok"}
    out = _render_explain_firm(p)
    assert weak in out, "데이터 경고가 봉투 경고를 가린다(explain)"
    assert "스케일가드 주의" in out


def test_the_hand_built_services_wrap_their_entry_point() -> None:
    """dict 를 직접 만드는 서비스는 return 이 여러 곳이라 진입점 래핑만이 안전하다."""
    for name, entry in (
        ("price_multiple_data.py", "build_valuation_payload"),
        ("asset_holdings.py", "build_asset_holdings_payload"),
        ("screener.py", "build_screener_payload"),
    ):
        source = (_SERVICES / name).read_text()
        assert f"async def _{entry}_impl(" in source, name
        # 대입이든 즉시 return 이든 상관없다 — **impl 전체가 감싸였는지**가 계약이다.
        # (valuation 은 감싼 뒤 md 를 다시 찍어야 해서 대입 형태다 — 아래 렌더 테스트가 그걸 잰다.)
        assert f"declare_weak_resolution(await _{entry}_impl(" in source, name
