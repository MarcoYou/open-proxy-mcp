"""5% 대량보유 「합이 100%를 넘는다」 유의사항이 실제로 찾은 까닭만 말하는가(2026-10-18, STX).

운영 출력: 「그냥 더하면 199.86%로 100%를 넘는다 — 보고자들이 같은 특별관계자를 서로 품고 있어 …
겹치는 몫을 한 번만 세면 199.86%다.」 — 단순 합과 「한 번만 센」 합이 같은 수였다. 원인 둘:
  - 같은 날 두 번 낸 보고 중 목록에 먼저 나온 **옛 보고**가 「보고자별 최신 1건」이 됐다
    (신한은행 2026-10-14: 출자전환 5.81% → 같은 날 감자·신주 뒤 0.34%).
  - 겹치는 보고자는 없었다. 넘는 까닭은 보고마다 다른 **기준 주식총수**(출자전환 보고들 약 1억 7천만 주,
    감자·제3자배정 뒤 범한산업 약 2,866만 주)인데, 문구는 늘 겹침이라고 했다.

자료: 실제 DART majorstock 응답(`tests/dart_responses/dart/majorstock_stx_00138297_20261018.json`). 네트워크 0.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from open_proxy_mcp.services import ownership_structure as O
from open_proxy_mcp.tools.ownership_structure import _render

_FIX = Path(__file__).parent / "dart_responses" / "dart" / "majorstock_stx_00138297_20261018.json"


class _FakeClient:
    def __init__(self, data: dict) -> None:
        self._data = data

    async def get_block_holders(self, corp_code: str) -> dict:
        return self._data

    async def get_document_cached(self, rcept_no: str) -> dict:
        return {"html": ""}


def _stx(reverse: bool = False) -> dict:
    data = json.loads(_FIX.read_text(encoding="utf-8"))
    if reverse:
        data["list"] = list(reversed(data["list"]))
    return data


def _latest(monkeypatch, data: dict) -> list[dict]:
    monkeypatch.setattr(O, "get_dart_client", lambda: _FakeClient(data))
    rows, _timeline, note = asyncio.run(O._latest_block_rows("00138297"))
    assert note is None
    return rows


def _stx_blocks(monkeypatch) -> list[dict]:
    """서비스와 같은 순서 — 보고자별 최신 1건 → 5% 이상만 → 공동보유자 분해."""
    rows = [r for r in _latest(monkeypatch, _stx()) if r["ownership_pct"] >= 5.0]
    O._enrich_co_holders(rows, [])
    return rows


def test_same_day_reports_keep_the_later_receipt(monkeypatch) -> None:
    """같은 접수일이면 접수번호가 늦은 보고가 최신이다 — 목록 순서와 무관하게."""
    for reverse in (False, True):
        by = {r["reporter"]: r for r in _latest(monkeypatch, _stx(reverse))}
        assert by["신한은행"]["rcept_no"] == "20261014000418"
        assert by["신한은행"]["ownership_pct"] == 0.34
        assert by["서울보증보험"]["rcept_no"] == "20261015000368"
        assert by["서울보증보험"]["ownership_pct"] == 0.94
        assert by["범한산업"]["shares"] == 27_200_000


def test_largest_shareholder_fallback_uses_the_later_receipt_too() -> None:
    for reverse in (False, True):
        rows, _w = asyncio.run(O._fetch_largest_shareholder_from_blocks(_FakeClient(_stx(reverse)), "00138297"))
        by = {r["name"]: r for r in rows}
        assert by["신한은행"]["ownership_pct"] == 0.34
        assert by["서울보증보험"]["ownership_pct"] == 0.94


def test_stx_note_names_report_bases_not_overlap(monkeypatch) -> None:
    blocks = _stx_blocks(monkeypatch)
    # 신한은행 옛 5.81% 가 빠진다(이전 199.86%).
    assert sorted(b["reporter"] for b in blocks) == [
        "농협은행", "범한산업", "에스티엑스썬에이스해운", "에이피씨머큐리", "우리은행", "한국산업은행",
    ]
    camps = O._build_block_camps(blocks)
    assert camps["headline_total_pct"] == 194.05
    assert camps["overlap_found"] is False
    assert camps["net_total_pct"] == camps["headline_total_pct"]

    bases = camps["report_bases"]
    assert [b["reporters"] for b in bases] == [
        ["범한산업"],
        ["한국산업은행", "우리은행", "농협은행"],
        ["에이피씨머큐리"],
        ["에스티엑스썬에이스해운"],
    ]
    assert bases[1]["report_dates"] == ["2026-09-29", "2026-10-16"]

    note = camps["over_100_note"]
    assert note.startswith("5% 대량보유 보고 지분율을 그냥 더하면 194.05%로 100%를 넘는다")
    # 겹침을 찾지 못했으면 겹침이라고 하지 않고, 단순 합을 「한 번만 센 값」으로 되풀이하지 않는다.
    assert "서로 품고" not in note
    assert "한 번만 세" not in note
    assert "겹치는 특별관계자는 찾지 못했다" in note
    assert "약 2,866만 주(범한산업 2026-10-16)" in note
    assert "약 1억 7,052만 주(한국산업은행 외 2건 2026-09-29~2026-10-16)" in note
    assert "밝히지 못했다" not in note


def _koreazinc_like(shares: bool) -> list[dict]:
    def block(reporter, pct, self_pct, co, n):
        return {
            "reporter": reporter, "ownership_pct": pct, "reporter_self_pct": self_pct,
            "purpose": "경영참여", "report_date": "2026-05-20", "rcept_no": f"2026052000026{n}",
            "shares": int(20_700_000 * pct / 100) if shares else 0,
            "co_holders": None if co is None else [
                {"name": nm, "ownership_pct": p, "is_registry_holder": False} for nm, p in co
            ],
            "co_holders_verified": True,
        }
    return [
        block("영풍", 41.13, 0.0, [("(유)와이피씨", 25.21), ("한국기업투자홀딩스", 8.25), ("장형진", 7.67)], 1),
        block("한국기업투자홀딩스", 36.92, 8.25, [("(유)와이피씨", 25.21), ("장형진", 3.46)], 2),
        block("최윤범", 17.72, None, None, 3),
        block("크루시블제이브이", 10.59, None, None, 4),
        block("국민연금공단", 5.48, None, None, 5),
    ]


def test_overlap_note_is_unchanged_when_net_is_under_100() -> None:
    """겹침을 찾고 걷어낸 합이 100% 밑이면 예전 문구 그대로(고려아연)."""
    camps = O._build_block_camps(_koreazinc_like(shares=True))
    assert camps["overlap_found"] is True
    assert len(camps["report_bases"]) == 1          # 같은 기준 주식총수
    note = camps["over_100_note"]
    assert "서로 품고 있어" in note
    assert f"겹치는 몫을 한 번만 세면 {camps['net_total_pct']:.2f}%다." in note
    assert "기준 주식총수" not in note


def test_overlap_still_over_100_says_so() -> None:
    blocks = _koreazinc_like(shares=False)
    blocks.append({"reporter": "갑", "ownership_pct": 60.0, "purpose": "단순투자",
                   "report_date": "2026-06-01", "rcept_no": "20260601000001",
                   "co_holders": None, "co_holders_verified": None})
    camps = O._build_block_camps(blocks)
    note = camps["over_100_note"]
    assert f"한 번만 세도 {camps['net_total_pct']:.2f}%로 여전히 100%를 넘는다" in note
    # 보유주식수가 없으면 기준 주식총수로 설명할 수 없다 — 지어내지 않고 원문을 가리킨다.
    assert camps["report_bases"] == []
    assert "넘는 까닭을 이 자료로는 밝히지 못했다" in note


def test_under_100_has_no_note() -> None:
    blocks = _koreazinc_like(shares=True)[:3]
    camps = O._build_block_camps(blocks)
    assert camps["exceeds_100"] is False
    assert camps["over_100_note"] == ""


def test_render_blocks_shows_bases_instead_of_a_camp_table(monkeypatch) -> None:
    """겹침이 없으면 진영 표(단순 합의 되풀이)는 내지 않고, 기준 주식총수 표를 낸다."""
    blocks = _stx_blocks(monkeypatch)
    camps = O._build_block_camps(blocks)
    payload = {
        "status": "exact",
        "subject": "STX",
        "warnings": [camps["over_100_note"]],
        "data": {"canonical_name": "STX", "summary": {}, "window": {}, "blocks": blocks, "block_camps": camps},
    }
    out = _render(payload, "blocks")
    assert "## 진영별 순 지분" not in out
    assert "## 보고마다 다른 기준 주식총수" in out
    assert "| 170,515,091주 | 한국산업은행, 우리은행, 농협은행 | 2026-09-29 ~ 2026-10-16 |" in out
    assert "| 28,664,770주 | 범한산업 | 2026-10-16 |" in out
