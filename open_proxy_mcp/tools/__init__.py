"""MCP tool 패키지 — auto-discovery"""

import importlib
import pkgutil


def register_all_tools(mcp):
    """tools/ 하위 모듈에서 register_tools(mcp) 함수를 자동 탐색하여 실행"""
    import open_proxy_mcp.tools as tools_pkg
    for _importer, modname, _ispkg in pkgutil.iter_modules(tools_pkg.__path__):
        if modname.startswith("_") or modname in ("formatters", "errors", "parser", "pdf_parser"):
            continue
        module = importlib.import_module(f"open_proxy_mcp.tools.{modname}")
        if hasattr(module, "register_tools"):
            module.register_tools(mcp)
