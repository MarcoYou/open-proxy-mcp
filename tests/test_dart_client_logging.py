import logging

import open_proxy_mcp.dart.client  # noqa: F401


def test_httpx_request_info_logging_is_suppressed_for_query_key_safety() -> None:
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
