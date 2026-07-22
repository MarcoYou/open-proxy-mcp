"""Ownership disclosure parsing helpers shared by ownership services."""

import re


def parse_holding_purpose(report_type: str, report_reason: str) -> str:
    """Infer the holding purpose from a major-shareholding API response."""
    if report_type == "일반":
        return "경영참여"

    reason = report_reason or ""
    if "단순투자" in reason:
        return "단순투자"
    if "일반투자" in reason:
        return "일반투자"
    if report_type == "약식":
        return "단순투자/일반투자"
    return "불명"


def parse_holding_purpose_from_document(html: str) -> str:
    """Parse the holding purpose from a DART document.xml body."""
    match = re.search(r'AUNIT="PUR_OWN"[^>]*>([^<]+)<', html)
    if match:
        return _normalize_purpose(match.group(1).strip())

    match = re.search(
        r'보유목적\s*</T[DH]>\s*<T[UDH][^>]*>\s*(.+?)\s*</T[UDH]>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        purpose = re.sub(r'<[^>]+>', '', match.group(1)).strip()
        if purpose:
            return _normalize_purpose(purpose)

    match = re.search(
        r'보유목적\s*</[hH]\d>\s*.*?<TD[^>]*>\s*(.+?)\s*</TD>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        purpose = re.sub(r'<[^>]+>', '', match.group(1)).strip()
        if purpose:
            return _normalize_purpose(purpose)

    return "불명"


def _normalize_purpose(raw: str) -> str:
    if "경영" in raw and "참여" in raw:
        return "경영참여"
    if "단순" in raw and "투자" in raw:
        return "단순투자"
    if "일반" in raw and "투자" in raw:
        return "일반투자"
    return raw
