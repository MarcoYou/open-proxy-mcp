"""단순 HTML 조각의 직접 판독. 복구 차이가 날 수 있는 입력은 호출측 기존 경로에 맡긴다."""
import re

from lxml import etree


_TEXT = etree.XPath(
    ".//text()[not(ancestor::script or ancestor::style or ancestor::template or ancestor::rt or ancestor::rp)]",
    smart_strings=False,
)
_LEGACY_MARKUP = re.compile(r"<!DOCTYPE|<\?|\bxmlns(?::|\s*=)|</?(?:html|head|body)\b", re.I)


def node_text(node) -> str:
    """get_text(' ', strip=True)의 문자열 종류와 경계로 결합한다."""
    return " ".join(text for value in _TEXT(node) if (text := value.strip()))


def parse_fragment(html: str):
    """직접 판독 가능한 HTML 트리 또는 기존 파싱이 필요한 경우 None."""
    if not isinstance(html, str) or _LEGACY_MARKUP.search(html):
        return None
    try:
        encoded = html.encode("utf-8")
        if len(encoded) >= 8 * 1024 * 1024:
            return None
        parser = etree.HTMLParser(encoding="utf-8")
        root = etree.HTML(encoded, parser)
    except (ValueError, UnicodeError, etree.LxmlError):
        return None
    if root is None:
        return None
    # DART 절 경계의 불일치 닫는 태그는 허용한다. 그 외 진단은 복구 차이를 피한다.
    if any(error.type_name != "ERR_TAG_NAME_MISMATCH" for error in parser.error_log):
        return None
    # 진단 장부가 가득 차 깊이 초과 기록이 빠져도 후속 내용 누락을 감지한다.
    depth = 0
    for event, _ in etree.iterwalk(root, events=("start", "end")):
        depth += 1 if event == "start" else -1
        if depth >= 250:
            return None
    return root
