"""확장 훅 — 설치된 확장 패키지가 있으면 부르고, 없으면 조용히 건너뛴다.

두 entry point 그룹:
  · `open_proxy_mcp.extensions` — `register(mcp)`. 서버가 도구·프롬프트·리소스를 다 건 뒤 한 번 부른다.
  · `open_proxy_mcp.hints`      — `hint(rcept_no, title=None, no=None) -> str`. 도구가 파싱이 약하거나
                                   값을 못 찾은 자리에 적을 「원문 위치」 한 줄. 없으면 빈 문자열.

공개 레포에는 훅만 있고 확장의 내용은 없다. 확장이 무엇을 하든 공개 서버의 동작 계약은 같다 —
확장이 있으면 줄이 하나 더 붙고, 없으면 그 줄이 없다.
"""
from __future__ import annotations

import logging
from importlib.metadata import entry_points

logger = logging.getLogger(__name__)

_GROUP_REGISTER = "open_proxy_mcp.extensions"
_GROUP_HINT = "open_proxy_mcp.hints"
_hint_providers: list | None = None


def load_extensions(mcp) -> list[str]:
    """설치된 확장의 `register(mcp)` 를 전부 부른다. 하나가 죽어도 나머지·서버는 산다."""
    loaded: list[str] = []
    for ep in entry_points(group=_GROUP_REGISTER):
        try:
            ep.load()(mcp)
            loaded.append(ep.name)
        except Exception as exc:  # noqa: BLE001 — 확장 하나가 서버를 못 죽인다
            logger.warning(f"[extensions] {ep.name} 등록 실패: {exc}")
    if loaded:
        logger.info(f"[extensions] 등록: {', '.join(loaded)}")
    return loaded


def _providers() -> list:
    global _hint_providers
    if _hint_providers is None:
        found = []
        for ep in entry_points(group=_GROUP_HINT):
            try:
                found.append(ep.load())
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[extensions] hint {ep.name} 로드 실패: {exc}")
        _hint_providers = found
    return _hint_providers


def origin_hint(rcept_no: str, title: str | None = None, no: str | None = None) -> str:
    """원문 위치 한 줄. 확장이 없으면 "" — 호출자는 빈 문자열이면 줄을 안 붙인다."""
    if not rcept_no:
        return ""
    for fn in _providers():
        try:
            out = fn(rcept_no, title, no)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[extensions] hint 실패: {exc}")
            continue
        if out:
            return out
    return ""
