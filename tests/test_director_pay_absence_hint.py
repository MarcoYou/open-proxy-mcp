"""보수 지급액이 비었을 때 「없다」로 끝내지 않고 의심할 자리를 주는지 — 네트워크 0.

배경. 이 tool 이 부르는 보수 API 는 V1 서식이다. DART 가 Ver2.0 으로 넘기면 V1 응답은
**에러가 아니라 빈 채로** 온다 — 그러면 보수 축이 조용히 사라지고, 읽는 쪽은 「이 회사는
보수를 안 준다」로 오해한다. 이 레포가 가장 싫어하는 실패 모양(「에러가 아니라 대체」).

가르는 신호는 **한도만 읽혔는가**다. 한도도 지급액도 없으면 그냥 미제출·비대상일 뿐이지만,
한도는 읽혔는데 지급액만 비었다면 같은 보고서를 반쪽만 읽은 것이라 서식을 의심할 자리가 있다.

입력은 DART 응답 경계다 — 승인한도 `drctrAdtAllMendngSttusGmtsckConfmAmount.json` 와
유형별 지급 `drctrAdtAllMendngSttusMendngPymntamtTyCl.json` 의 `list` 행(CLAUDE.md #15).
"""
import asyncio

import pytest

from open_proxy_mcp import extensions
from open_proxy_mcp.services.director_board import _compensation_scope

_HINT = "서식이 바뀌었을 수 있으니"


@pytest.fixture(autouse=True)
def isolated_hint_providers(monkeypatch):
    """설치된 로컬 확장이나 앞선 테스트의 provider 캐시에 의존하지 않는다."""
    monkeypatch.setattr(extensions, "_hint_providers", [])


class _FakeClient:
    """연도별 (한도행, 지급행) 을 그대로 돌려주는 대역. DART 콜 0."""

    def __init__(self, limit_rows, actual_rows):
        self._limit, self._actual = limit_rows, actual_rows

    async def get_director_pay_limit(self, corp_code, year, reprt):
        return {"status": "000", "list": self._limit}

    async def get_director_pay_actual(self, corp_code, year, reprt):
        return {"status": "000", "list": self._actual}


def _run(limit_rows, actual_rows) -> list[str]:
    warnings: list[str] = []
    asyncio.run(_compensation_scope(_FakeClient(limit_rows, actual_rows), "00126380", 2025,
                                    lookback_years=3, warnings=warnings))
    return warnings


_LIMIT = [{"se": "이사", "gmtsck_confm_amount": "40,000,000,000", "nmpr": "9",
           "rcept_no": "20260311000123"}]
_ACTUAL = [{"se": "등기이사", "nmpr": "5", "pymnt_totamt": "10,000,000,000",
            "psn1_avrg_pymntamt": "2,000,000,000"}]


def test_a_paid_company_says_nothing():
    assert _run(_LIMIT, _ACTUAL) == []


def test_limit_without_pay_points_at_the_source_text():
    """반쪽만 읽힌 경우 — 서식 의심 + 어디를 볼지를 준다."""
    ws = _run(_LIMIT, [])
    assert len(ws) == 1 and _HINT in ws[0]
    assert "이사·감사의 보수" in ws[0]


def test_the_hint_names_no_tool_that_the_public_repo_lacks():
    """확장 훅이 없으면 절 이름만 남아야 한다 — 없는 tool 을 부르라고 하면 죽은 경로다.

    `extensions.py` 계약: 「확장이 있으면 줄이 하나 더 붙고, 없으면 그 줄이 없다」.
    """
    ws = _run(_LIMIT, [])
    assert "filing_section" not in ws[0]
    assert "사업보고서 「이사·감사의 보수」 절" in ws[0]


def test_an_installed_hint_provider_supplies_the_source_location(monkeypatch):
    calls = []
    location = "확장 제공 원문 위치: 이사·감사의 보수"

    def provider(rcept_no, title, no):
        calls.append((rcept_no, title, no))
        return location

    monkeypatch.setattr(extensions, "_hint_providers", [provider])
    ws = _run(_LIMIT, [])
    assert calls == [("20260311000123", "이사·감사의 보수", None)]
    assert len(ws) == 1 and _HINT in ws[0] and location in ws[0]
    assert "사업보고서 「이사·감사의 보수」 절" not in ws[0]


def test_neither_read_stays_plain():
    """둘 다 없으면 그냥 미제출·비대상이다 — 서식 의심을 덧붙이면 늑대소년이 된다."""
    ws = _run([], [])
    assert len(ws) == 1 and _HINT not in ws[0]
    assert "미제출" in ws[0]
