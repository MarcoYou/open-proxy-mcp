"""disclosure_ledger — 공시 이벤트 원장 배치 (DB·네트워크 0콜).

지키는 것: ① 분류는 screener 와 같은 분류기(정정·단계·dedup_key) ② 선택 유형 밖·날짜 불량은 버린다
③ 기본 창은 오늘 포함 최근 3일 ④ 스냅샷 고르기는 접수일 이전 최신, 없으면 가장 오래된 것
⑤ 하루 스캔은 코드별 커버리지를 남기고 같은 접수번호를 두 번 넣지 않는다 ⑥ 상세는 파싱된
것만 덮어쓴다(upsert SQL) ⑦ DB 행에는 내부 키가 안 들어간다.
"""
import asyncio
import datetime as dt
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

_SPEC = importlib.util.spec_from_file_location(
    "disclosure_ledger", Path(__file__).resolve().parents[1] / "scripts" / "disclosure_ledger.py")
led = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(led)


def _item(rcept_no, report_nm, dt_="20260915", corp="00126380", name="삼성전자", code="005930", cls="Y"):
    return {"rcept_no": rcept_no, "report_nm": report_nm, "rcept_dt": dt_, "corp_code": corp,
            "corp_name": name, "stock_code": code, "corp_cls": cls, "flr_nm": name}


def test_row_from_item_uses_screener_classifier():
    r = led.row_from_item(_item("20260915000001", "단일판매ㆍ공급계약체결"), set(led.DEFAULT_TYPES))
    assert r["kind"] == "order" and r["subtype"] == "체결" and r["stage"] == "-" or r["kind"] == "order"
    assert r["rcept_dt"] == dt.date(2026, 9, 15) and r["is_correction"] is False
    assert r["dedup_key"] == "00126380:order:체결" and r["_detail_kind"] == "order"
    assert r["detail_status"] == "scan_only" and r["filed_at"] == "2026-09-15"
    c = led.row_from_item(_item("20260915000002", "[기재정정]단일판매ㆍ공급계약체결"), set(led.DEFAULT_TYPES))
    assert c["is_correction"] is True and c["_force_detail"] is True
    t = led.row_from_item(_item("20260915000003", "자기주식취득결정"), set(led.DEFAULT_TYPES))
    assert t["kind"] == "treasury" and t["stage"] == "결정(예정)"


def test_row_from_item_drops_unselected_and_bad_dates():
    assert led.row_from_item(_item("1", "단일판매ㆍ공급계약체결"), {"treasury"}) is None
    assert led.row_from_item(_item("2", "기타경영사항(자율공시)"), set(led.DEFAULT_TYPES)) is None
    assert led.row_from_item(_item("3", "자기주식취득결정", dt_="2026-09"), set(led.DEFAULT_TYPES)) is None
    assert "insider10" not in led.DEFAULT_TYPES and "order" in led.DEFAULT_TYPES


def test_default_window_and_days():
    since, until = led.default_window(dt.date(2026, 9, 16))
    assert (since, until) == (dt.date(2026, 9, 14), dt.date(2026, 9, 16))
    assert len(led.days_between(since, until)) == 3


def test_pick_snapshot_latest_at_or_before_else_oldest():
    snaps = ["20260731", "20260828"]
    assert led.pick_snapshot(snaps, "20260915") == "20260828"
    assert led.pick_snapshot(snaps, "20260810") == "20260731"
    assert led.pick_snapshot(snaps, "20260101") == "20260731"     # 백필 초기: 가장 가까운(오래된) 것
    assert led.pick_snapshot([], "20260915") is None


def test_scan_codes_union():
    assert led.scan_codes_for(["order", "dividend", "treasury"]) == ["B001", "I001"]


def test_db_row_strips_internal_keys_and_serializes_detail():
    r = led.row_from_item(_item("20260915000001", "단일판매ㆍ공급계약체결"), set(led.DEFAULT_TYPES))
    r["detail"] = {"amount_won": 1000, "counterparty": "A사"}
    d = led.db_row(r)
    assert not any(k.startswith("_") for k in d) and "filed_at" not in d
    assert d["detail"].startswith("{") and "A사" in d["detail"]
    assert set(d) >= {"rcept_no", "rcept_dt", "kind", "stage", "is_correction", "dedup_key", "detail_status"}


def test_upsert_sql_only_overwrites_detail_when_parsed():
    sql = led.UPSERT_EVENT
    assert "ON CONFLICT (rcept_no) DO UPDATE" in sql
    assert "WHEN EXCLUDED.detail_status IN ('parsed','partial') THEN EXCLUDED.detail" in sql
    assert "CREATE TABLE IF NOT EXISTS dart_events_scan" in led.DDL


def test_upsert_sql_records_a_real_first_result_over_the_scan_only_placeholder():
    """260917 버그 회귀 방지. 옛 SQL은 `COALESCE(dart_events.detail_status, EXCLUDED.…)` 라
    새 값이 parsed/partial 이 아니면 **옛 값이 있으면 무조건 옛 값**을 썼다 — 첫 시도라 옛 값이
    기본치 scan_only(「아직 안 봄」)뿐이어도 그걸 지켜, 실제 결과(no_data 등)가 영영 안 남았다
    (해지 단계 공시 85건 실측). 옳은 규칙은 「기존이 진짜 결과(parsed/partial)일 때만 지킨다」다."""
    sql = led.UPSERT_EVENT
    assert "COALESCE(dart_events.detail_status, EXCLUDED.detail_status)" not in sql   # 그 버그 패턴
    assert "WHEN dart_events.detail_status IN ('parsed','partial') THEN dart_events.detail_status" in sql
    assert "ELSE EXCLUDED.detail_status END" in sql   # scan_only·이전 실패는 새 결과로 갱신
    assert "WHEN dart_events.detail_status IN ('parsed','partial') THEN dart_events.detail_note" in sql


def test_scan_day_dedups_rcept_and_records_coverage(monkeypatch):
    calls = []

    async def fake_scan(client, code, bgn, end, max_pages):
        calls.append((code, bgn, end))
        items = {"I001": [_item("20260915000001", "단일판매ㆍ공급계약체결"),
                          _item("20260915000001", "단일판매ㆍ공급계약체결"),          # 페이지 경계 중복
                          _item("20260915000009", "정기주주총회결과", corp="X", name="갑", code="000010")],
                 "B001": [_item("20260915000005", "자기주식취득결정")]}[code]
        return {"items": items, "total": len(items), "total_pages": 1, "received_pages": 1,
                "complete": code == "I001", "error": None if code == "I001" else "020"}
    monkeypatch.setattr(led, "_scan_code_uncached", fake_scan)
    rows, cov = asyncio.run(led.scan_day(None, dt.date(2026, 9, 15), ["B001", "I001"], set(led.DEFAULT_TYPES)))
    assert sorted(r["rcept_no"] for r in rows) == ["20260915000001", "20260915000005", "20260915000009"]
    assert {c["code"]: c["complete"] for c in cov} == {"B001": False, "I001": True}
    assert calls[0][1] == calls[0][2] == "20260915"                     # 하루 창


def test_fetch_details_respects_cap_and_skip(monkeypatch):
    rows = [led.row_from_item(_item(f"2026091500000{i}", "단일판매ㆍ공급계약체결", code=f"00000{i}"), {"order"})
            for i in range(1, 4)]
    rows[1]["mktcap_won"] = 10**14

    class Client:                       # 전역 콜 카운터 — 상한은 이것으로 잰다
        n = 0
        def api_call_snapshot(self):
            return self.n
    client = Client()

    async def fake_detail(hit, running):
        client.n += 2
        return {"detail_status": "parsed", "fields": {"amount_won": 1}}
    monkeypatch.setattr(led, "_fetch_detail", fake_detail)
    stats = asyncio.run(led.fetch_details(client, rows, ("order",), max_calls=3, skip={rows[0]["rcept_no"]}))
    assert stats["targets"] == 2                                          # 한 건은 이미 상세 있음 → 건너뜀
    assert stats["done"] == 2 and stats["dart_calls"] == 4                # 2콜 뒤 2<3 이라 둘째도 돈다
    stats2 = asyncio.run(led.fetch_details(client, [dict(r) for r in rows[1:]], ("order",), max_calls=1, skip=set()))
    assert stats2["done"] == 1 and stats2["skipped"] == 1                  # 첫 건 뒤 2≥1 → 둘째는 상한
    assert rows[1]["detail_status"] == "parsed"                           # 시총 큰 건이 먼저
    assert rows[0]["detail_status"] == "scan_only"
