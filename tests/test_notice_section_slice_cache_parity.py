"""절 조각 파싱 == 전체 파싱 — 로컬 문서 캐시(DART 응답 경계, 규칙 15)의 소집공고 전수로 대조한다.

캐시가 없는 환경(CI)에선 건너뛴다. 로컬에선 143건(260907) — 정정공고 20건 포함 전 필드 동일해야 한다.
새 서식이 캐시에 들어와 갈리면 여기서 먼저 걸린다.
"""
import gzip
import json
from pathlib import Path

import pytest

from open_proxy_mcp.dart.client import _DISK_CACHE_DIR
from open_proxy_mcp.services.shareholder_meeting import _notice_section_slice
from open_proxy_mcp.services.shareholder_meeting_parser import parse_meeting_info_xml


def _cached_notices(limit: int = 400):
    d = Path(_DISK_CACHE_DIR)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json.gz")):
        try:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception:  # noqa: BLE001 — 손상 캐시는 클라이언트가 알아서 지운다
            continue
        text = doc.get("text") or ""
        if "주주총회소집공고" in text[:400].replace(" ", ""):
            out.append((f.name.split(".")[0], text, doc.get("html") or ""))
            if len(out) >= limit:
                break
    return out


def test_slice_parse_matches_full_parse_on_every_cached_notice():
    notices = _cached_notices()
    if len(notices) < 20:
        pytest.skip(f"로컬 소집공고 캐시 부족({len(notices)}건) — 대조는 캐시가 있는 환경에서")
    mismatch = []
    for rcept_no, text, html in notices:
        full = parse_meeting_info_xml(text, html=html)
        sliced = _notice_section_slice(html)
        light = parse_meeting_info_xml(text, html=sliced or html)
        bad = [k for k in full if full[k] != light.get(k)]
        if bad:
            mismatch.append((rcept_no, bad))
    assert not mismatch, f"{len(mismatch)}/{len(notices)}건 갈림: {mismatch[:5]}"
