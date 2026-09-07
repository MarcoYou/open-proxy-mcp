"""260907 live hang 뒤 넣은 진단(호출시간 로그)과 light smoke 에서 잡힌 렌더 누출 회귀."""
import asyncio
import logging

import pytest


def test_every_tool_call_leaves_a_timing_log(caplog):
    from open_proxy_mcp.server import TimedMCPServer

    mcp = TimedMCPServer("t")

    @mcp.tool()
    def echo(company: str) -> str:
        return company

    with caplog.at_level(logging.INFO, logger="opm.tool"):
        asyncio.run(mcp.call_tool("echo", {"company": "삼성전자"}))
    recs = [r for r in caplog.records if r.name == "opm.tool"]
    assert len(recs) == 1 and "tool=echo" in recs[0].getMessage() and "wall=" in recs[0].getMessage()
    assert "삼성전자" not in recs[0].getMessage() and "args=[company]" in recs[0].getMessage()   # 값은 안 남긴다(규칙 10)


def test_slow_tool_call_is_a_warning(caplog, monkeypatch):
    import open_proxy_mcp.server as srv

    monkeypatch.setattr(srv, "_SLOW_TOOL_SEC", 0.0)
    mcp = srv.TimedMCPServer("t")

    @mcp.tool()
    def echo(company: str) -> str:
        return company

    with caplog.at_level(logging.INFO, logger="opm.tool"):
        asyncio.run(mcp.call_tool("echo", {"company": "x"}))
    assert any(r.levelno == logging.WARNING and "slow tool=echo" in r.getMessage() for r in caplog.records)


def test_eps_missing_says_undisclosed_not_dash_won():
    from open_proxy_mcp.tools.financial_metrics import _eps
    assert _eps(None) == "미공시" and _eps(3319) == "3,319원"


def test_company_filing_type_is_korean_not_code():
    from open_proxy_mcp.tools.company import _filing_type_label
    assert _filing_type_label("ownership_block", False) == "대량보유 보고"
    assert _filing_type_label("ownership_block", True) == "ownership_block"
    assert _filing_type_label("something_new", False) == "something new"


def test_forward_estimates_period_label_is_korean():
    from open_proxy_mcp.tools.forward_estimates_data import _PERIOD_KO
    assert _PERIOD_KO["FY"] == "연간"


def test_user_facing_strings_do_not_name_db_tables_or_columns():
    """krx_weekly·close_krw·mktcap_krw 는 저장소 이름이다 — 사용자 문장엔 나가지 않는다(rule/desc 는 AI 용이라 제외)."""
    import re
    from pathlib import Path
    for f in ("open_proxy_mcp/services/trading.py", "open_proxy_mcp/tools/shareholder_commitment.py",
              "open_proxy_mcp/tools/price_multiple_data.py", "open_proxy_mcp/tools/forward_estimates_data.py"):
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            if re.search(r"^\s*(rule|desc|when|window):", line) or line.lstrip().startswith("#") or '"' not in line or line.lstrip().startswith('"""'):
                continue   # 주석·docstring 본문(따옴표 없는 줄)은 사용자에게 안 나간다
            if re.search(r"[가-힣].*(krx_weekly|`close_krw`|`mktcap_krw`|period_type=|bundle=)|(krx_weekly|period_type=|bundle=).*[가-힣]", line):
                # SQL·키 접근·docstring 은 제외 — 한글 문장과 같은 줄에 있는 것만 본다
                if "SELECT" in line or "sql" in line.lower() or "source=" in line:
                    continue
                raise AssertionError(f"{f}: {line.strip()[:120]}")
