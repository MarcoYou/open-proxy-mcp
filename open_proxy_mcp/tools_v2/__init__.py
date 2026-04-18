"""v2 public facade tools."""

import importlib
import pkgutil


def register_all_tools_v2(mcp):
    """tools_v2/ 하위 public tool 등록."""

    import open_proxy_mcp.tools_v2 as tools_pkg

    for _importer, modname, _ispkg in pkgutil.iter_modules(tools_pkg.__path__):
        if modname.startswith("_"):
            continue
        module = importlib.import_module(f"open_proxy_mcp.tools_v2.{modname}")
        if hasattr(module, "register_tools"):
            module.register_tools(mcp)

