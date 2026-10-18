"""`scripts/mcp_probe.py` 의 `k=v` 값 해석 — 종목코드(6자리 숫자)는 문자열로 둔다(261018).

`950260` 같은 0 으로 시작하지 않는 종목코드가 int 로 넘어가 `company` 인자 검증에서 죽었다(로컬 파일럿 실측:
「Input should be a valid string [type=string_type, input_value=950260, input_type=int]」). network 0."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("mcp_probe", Path(__file__).resolve().parent.parent / "scripts" / "mcp_probe.py")
probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(probe)


def test_stock_codes_and_long_digit_ids_stay_strings():
    for code in ("950260", "352820", "005930", "20261016", "20261016000123"):
        assert probe._coerce(code) == code
    assert probe._coerce("10") == 10 and probe._coerce("true") is True and probe._coerce("firm") == "firm"


def test_numeric_parameters_still_accept_a_six_digit_string():
    """6자리 수를 문자열로 넘겨도 수를 받는 인자는 받는다 — 검증기가 숫자 문자열을 수로 바꾼다."""
    from open_proxy_mcp.server import TimedMCPServer

    mcp = TimedMCPServer("t")

    @mcp.tool()
    def echo(company: str, section_chars: int = 0) -> str:
        return f"{company}|{section_chars + 1}"

    async def go():
        r = await mcp.call_tool("echo", {"company": probe._coerce("950260"), "section_chars": probe._coerce("120000")})
        return "".join(getattr(c, "text", "") for c in (r if isinstance(r, list) else r.content))
    assert asyncio.run(go()) == "950260|120001"
