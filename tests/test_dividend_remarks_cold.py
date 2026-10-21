# -*- coding: utf-8 -*-
"""결정공시 비고(`div_payment.remarks`) 를 콜드 보관본으로 옮긴 뒤에도 응답이 글자까지 같다 (261021).

`remarks` 는 행당 평균 약 620 B 의 자유서술이라 표 4.1 MiB 중 약 2.7 MiB 를 차지한다. 비고만 콜드 서빙본(`div_remarks`, 열쇠 corp_code ·
record_date, 칸 rcept_no · remarks)으로 옮기고, 옮긴 행은 DB 의 remarks 가 NULL 이다. 읽는 쪽 규칙(`dividend_data.fill_remarks_from_cold`):
  ① DB 가 비고를 갖고 있으면 DB — 콜드를 보지 않는다(종전과 같은 응답·같은 지연)
  ② NULL 이면 그 회사의 서빙본 행을 한 번 읽어 접수번호(rcept_no)로 맞춘다 — 채운 응답은 DB 가 갖고 있을 때와 글자까지 같다
  ③ 서빙본에 행이 있고 비고가 NULL 이면 진짜 빈 칸 — 안내하지 않는다. 행이 없거나 콜드를 못 읽으면 비워 둔 채 안내 한 줄
network 0 · DB 0 — `dd._rows` 대역과 로컬 file:// 서빙본.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from open_proxy_mcp import cold_store as C
from open_proxy_mcp.services import dividend_data as dd
from open_proxy_mcp.services import price_multiple_data as pmd
from open_proxy_mcp.tools import dividend_data as dd_tool

CORP = "00126380"
OTHER = "00164779"
R1, R2, R3 = "20250130800001", "20240725800002", "20240130800003"
TEXT1 = ("- 상기 배당금총액은 자기주식을 제외한 배당 대상 주식수 기준으로 산정한 금액입니다. "
         "- 상기 내용은 외부감사인의 감사결과 및 주주총회 승인과정에서 변경될 수 있습니다.")
TEXT2 = "- 금번 결산배당은 특별배당금 성격의 1,578원을 더하여 결정되었습니다.\n- 줄바꿈과 'quote' \"큰따옴표\" 그리고 ㈜ 같은 문자도 그대로."
TEXT3 = None            # 진짜 빈 비고


# ─────────────────────────────────────────────────────────────── 서빙본 고정물 ──
def build_remarks_serving(root: Path, rows: list[tuple], *, dataset: str = "div_remarks") -> dict:
    """발행기(`cold_serve_publish.DIV_REMARKS`)와 같은 모양 — 표 div_remarks(corp_code, record_date, rcept_no, remarks) WITHOUT ROWID.
    rows = [(corp_code, record_date(YYYYMMDD), rcept_no, remarks|None)]."""
    root = Path(root)
    build = root / "build"
    build.mkdir(parents=True, exist_ok=True)
    tmp = build / f"{dataset}.building.sqlite"
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp, isolation_level=None)
    con.execute("CREATE TABLE div_remarks (corp_code TEXT NOT NULL, record_date TEXT NOT NULL, rcept_no TEXT NOT NULL, remarks TEXT, "
                "PRIMARY KEY (corp_code, record_date, rcept_no)) WITHOUT ROWID")
    con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID")
    con.executemany("INSERT INTO div_remarks VALUES (?, ?, ?, ?)", sorted(rows))
    con.execute("INSERT INTO meta VALUES ('dataset', ?)", (dataset,))
    con.execute("PRAGMA application_id = 1330662723")
    con.execute("PRAGMA user_version = 1")
    con.execute("VACUUM")
    con.close()
    data = tmp.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    fname = f"{dataset}.{sha[:16]}.sqlite"
    tmp.replace(build / fname)
    obj_dir = root / "serve" / dataset
    obj_dir.mkdir(parents=True, exist_ok=True)
    object_path = obj_dir / (fname + ".gz")
    with open(object_path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as g:
        g.write(data)
    obj = object_path.read_bytes()
    dates = sorted(r[1] for r in rows)
    entry = {"file": fname, "object": f"serve/{dataset}/{fname}.gz", "encoding": "gzip", "sha256": sha, "bytes": len(data),
             "object_sha256": hashlib.sha256(obj).hexdigest(), "object_bytes": len(obj), "rows": len(rows),
             "coverage": {"min_dd": dates[0], "max_dd": dates[-1], "upto": dates[-1], "last_row_dd": dates[-1]}, "file_schema": 1}
    manifest = {"schema": 1, "kind": "opm-cold-serve", "datasets": {dataset: {
        "engine": "sqlite3", "view": "div_remarks", "columns": ["corp_code", "record_date", "rcept_no", "remarks"],
        "key": ["corp_code", "record_date"], "date_col": "record_date", "files": [entry],
        "coverage": {"min_dd": dates[0], "max_dd": dates[-1]}}}}
    mpath = root / "serve" / "manifest.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False))
    return {"manifest_url": "file://" + str(mpath), "entry": entry}


SERVED = [
    (CORP, "20241231", R1, TEXT1),
    (CORP, "20240630", R2, TEXT2),
    (CORP, "20231231", R3, TEXT3),
    (OTHER, "20241231", "20250130800099", "다른 회사의 비고 — 새어 나오면 안 된다"),
]


@pytest.fixture
def served(tmp_path, monkeypatch):
    def make(rows=SERVED):
        info = build_remarks_serving(tmp_path / "src", rows)
        monkeypatch.setenv("OPM_COLD", "1")
        monkeypatch.setenv("OPM_COLD_MANIFEST_URL", info["manifest_url"])
        monkeypatch.setenv("OPM_COLD_DIR", str(tmp_path / "cache"))
        return info
    return make


# ─────────────────────────────────────────────────────────────── DB 행 대역 ──
def _row(fy, board, rcept, dps, remarks, *, record="{fy}-12-31", special=False):
    #  fiscal_year, board_date, record_date, dividend_type_filed, dividend_type, dps_common, total_amount, rcept_no, amended, anomaly, has_special, remarks
    return (fy, board, record.format(fy=fy), "결산배당", "결산배당", dps, dps * 1000, rcept, False, None, special, remarks)


def _db_rows(remarks_by_rcept: dict):
    return [_row(2024, "2025-01-30", R1, 400, remarks_by_rcept[R1]),
            _row(2024, "2024-07-25", R2, 300, remarks_by_rcept[R2], record="2024-06-30", special=True),
            _row(2023, "2024-01-30", R3, 350, remarks_by_rcept[R3])]


FULL = {R1: TEXT1, R2: TEXT2, R3: TEXT3}         # 옮기기 전 — DB 가 전부 갖는다
MOVED = {R1: None, R2: None, R3: None}           # 옮긴 뒤 — DB 는 NULL


def _pay(monkeypatch, remarks_by_rcept):
    rows = _db_rows(remarks_by_rcept)
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): rows)
    return dd.payment_counts(CORP, 2020, 2025)


def _run(coro):
    return asyncio.run(coro)


def _decisions(pay):
    return [(d["rcept_no"], d["remarks"]) for d in pay["decisions"]]


# ═════════════════════════════════════════════════════════════════════════════════════════
# ① DB 가 비고를 갖고 있으면 콜드를 보지 않는다
# ═════════════════════════════════════════════════════════════════════════════════════════
def test_db_remarks_present_never_touches_cold(monkeypatch):
    monkeypatch.setenv("OPM_COLD", "1")
    boom = []

    async def ready(*a, **k):
        boom.append(1)
        raise AssertionError("DB 가 비고를 다 갖는데 콜드를 불렀다")
    monkeypatch.setattr(C, "ready", ready)
    pay = _pay(monkeypatch, {R1: TEXT1, R2: TEXT2, R3: "- 셋째 비고"})
    before = json.dumps(pay, ensure_ascii=False, sort_keys=True, default=str)
    out = _run(dd.fill_remarks_from_cold(pay, CORP))
    assert not boom and json.dumps(out, ensure_ascii=False, sort_keys=True, default=str) == before
    assert "remarks_cold_note" not in out


def test_cold_off_and_db_complete_is_the_old_response(monkeypatch):
    monkeypatch.delenv("OPM_COLD", raising=False)
    pay = _pay(monkeypatch, FULL | {R3: "- 셋째 비고"})
    out = _run(dd.fill_remarks_from_cold(pay, CORP))
    assert out is pay and "remarks_cold_note" not in out


# ═════════════════════════════════════════════════════════════════════════════════════════
# ② NULL 이면 서빙본으로 — 응답은 글자까지 같다
# ═════════════════════════════════════════════════════════════════════════════════════════
def test_null_remarks_are_filled_from_cold_and_match_the_db_answer_exactly(monkeypatch, served):
    full = _pay(monkeypatch, FULL)
    served()
    moved = _run(dd.fill_remarks_from_cold(_pay(monkeypatch, MOVED), CORP))
    assert _decisions(moved) == _decisions(full)
    assert "remarks_cold_note" not in moved
    assert json.dumps(moved, ensure_ascii=False, sort_keys=True, default=str) == json.dumps(full, ensure_ascii=False, sort_keys=True, default=str)


def test_tool_output_md_and_json_are_identical_before_and_after_the_move(monkeypatch, served):
    corp = {"corp_name": "삼성전자", "stock_code": "005930", "corp_code": CORP}

    async def fake_resolve(q):
        return corp, None
    monkeypatch.setattr(pmd, "_resolve_listed", fake_resolve)
    monkeypatch.setattr(dd, "year_range", lambda: (2020, 2025))
    monkeypatch.setattr(dd, "firm_history", lambda *a, **k: {"status": "ok", "annual": [], "quarterly": [], "annual_failed": False,
                                                              "quarterly_failed": False, "empty_kind_rows_folded": 0})
    reg = type("R", (), {"tools": {}, "tool": lambda self, *a, **k: (lambda fn: self.tools.setdefault(fn.__name__, fn) or fn)})()
    dd_tool.register_tools(reg)
    tool = reg.tools["dividend_data"]

    monkeypatch.delenv("OPM_COLD", raising=False)
    rows = _db_rows(FULL | {R3: "- 셋째 비고"})            # 옮기기 전: DB 가 셋 다 갖는다(진짜 빈 칸 케이스는 따로 시험)
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): rows)
    before_md = _run(tool(scope="firm", company="삼성전자", format="md"))
    before_js = _run(tool(scope="firm", company="삼성전자", format="json"))

    served([(CORP, "20241231", R1, TEXT1), (CORP, "20240630", R2, TEXT2), (CORP, "20231231", R3, "- 셋째 비고")])
    rows = _db_rows(MOVED)
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): rows)
    after_md = _run(tool(scope="firm", company="삼성전자", format="md"))
    after_js = _run(tool(scope="firm", company="삼성전자", format="json"))

    assert after_md == before_md, "비고를 콜드로 옮긴 뒤 마크다운 응답이 달라졌다"
    assert after_js == before_js, "비고를 콜드로 옮긴 뒤 JSON 응답이 달라졌다"
    assert TEXT1 in after_md and "그대로." in after_md and "비어 있다" not in after_md


def test_one_cold_read_per_call_and_only_that_company_is_read(monkeypatch, served):
    served()
    pay = _pay(monkeypatch, MOVED)
    _run(dd.fill_remarks_from_cold(pay, CORP))
    st = C.stats()
    assert st["counters"]["reads"] == 1 and st["counters"]["rows"] == 3, "회사 하나 = 읽기 한 번(그 회사 행 3개)"
    assert all("다른 회사" not in (d["remarks"] or "") for d in pay["decisions"])


def test_mixed_db_and_cold_only_null_rows_are_filled(monkeypatch, served):
    """최근 행은 DB 가 비고를 갖고(아직 안 옮김), 옛 행은 NULL — 섞여도 DB 값은 그대로, NULL 만 채운다."""
    served()
    pay = _pay(monkeypatch, {R1: TEXT1, R2: None, R3: None})
    out = _run(dd.fill_remarks_from_cold(pay, CORP))
    assert _decisions(out) == [(R1, TEXT1), (R2, TEXT2), (R3, TEXT3)]
    assert "remarks_cold_note" not in out


def test_match_is_by_rcept_no_not_by_record_date(monkeypatch, served):
    """같은 회사에 기준일이 같은 결의가 둘이어도(정정·분기) 접수번호로 맞춘다. DB 는 YYYY-MM-DD, 서빙본은 YYYYMMDD 라도 상관없다."""
    served([(CORP, "20241231", R1, "첫째"), (CORP, "20241231", R2, "둘째"), (CORP, "20241231", R3, "셋째")])
    rows = [_row(2024, "2025-01-30", R2, 1, None, record="2024-12-31"), _row(2024, "2025-01-30", R1, 1, None, record="2024-12-31"),
            _row(2024, "2025-01-30", R3, 1, None, record="")]          # 기준일 칸이 비어도 맞춘다
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): rows)
    out = _run(dd.fill_remarks_from_cold(dd.payment_counts(CORP, 2020, 2025), CORP))
    assert dict(_decisions(out)) == {R1: "첫째", R2: "둘째", R3: "셋째"}


# ═════════════════════════════════════════════════════════════════════════════════════════
# ③ 진짜 빈 칸 vs 못 읽음
# ═════════════════════════════════════════════════════════════════════════════════════════
def test_a_row_the_archive_holds_as_empty_is_genuinely_empty_and_not_announced(monkeypatch, served):
    served()
    out = _run(dd.fill_remarks_from_cold(_pay(monkeypatch, MOVED), CORP))
    assert dict(_decisions(out))[R3] is None and "remarks_cold_note" not in out


def test_cold_off_after_the_move_keeps_the_db_answer_and_says_so(monkeypatch):
    monkeypatch.delenv("OPM_COLD", raising=False)
    out = _run(dd.fill_remarks_from_cold(_pay(monkeypatch, MOVED), CORP))
    assert [r for _, r in _decisions(out)] == [None, None, None]
    assert out["remarks_cold_note"] == dd.cold_remarks_note(3)
    assert "특이사항 없음" in out["remarks_cold_note"]


def test_rcept_missing_from_the_archive_is_reported_but_others_are_filled(monkeypatch, served):
    served([r for r in SERVED if r[2] != R2])          # 보관본이 아직 R2 를 모른다(발행 전 새 행인데 DB 가 먼저 비워졌다 — 일어나면 안 되지만 알린다)
    out = _run(dd.fill_remarks_from_cold(_pay(monkeypatch, MOVED), CORP))
    assert dict(_decisions(out)) == {R1: TEXT1, R2: None, R3: None}
    assert out["remarks_cold_note"] == dd.cold_remarks_note(1)


def test_dataset_not_in_manifest_degrades_with_the_note(monkeypatch, tmp_path):
    info = build_remarks_serving(tmp_path / "src", SERVED, dataset="krx_weekly_hist_other")      # 다른 이름만 있는 목록
    monkeypatch.setenv("OPM_COLD", "1")
    monkeypatch.setenv("OPM_COLD_MANIFEST_URL", info["manifest_url"])
    monkeypatch.setenv("OPM_COLD_DIR", str(tmp_path / "cache"))
    out = _run(dd.fill_remarks_from_cold(_pay(monkeypatch, MOVED), CORP))
    assert out["remarks_cold_note"] == dd.cold_remarks_note(3)
    assert C.stats()["degraded"].get("no_dataset") == 1


def test_manifest_unreachable_degrades_with_the_note(monkeypatch, tmp_path):
    monkeypatch.setenv("OPM_COLD", "1")
    monkeypatch.setenv("OPM_COLD_MANIFEST_URL", "file://" + str(tmp_path / "없는목록.json"))
    monkeypatch.setenv("OPM_COLD_DIR", str(tmp_path / "cache"))
    out = _run(dd.fill_remarks_from_cold(_pay(monkeypatch, MOVED), CORP))
    assert out["remarks_cold_note"] == dd.cold_remarks_note(3) and [r for _, r in _decisions(out)] == [None] * 3


def test_read_failure_and_unexpected_exception_never_break_the_db_answer(monkeypatch, served):
    served()

    async def read_none(*a, **k):
        return None
    monkeypatch.setattr(C, "read", read_none)
    out = _run(dd.fill_remarks_from_cold(_pay(monkeypatch, MOVED), CORP))
    assert out["remarks_cold_note"] == dd.cold_remarks_note(3)

    async def read_boom(*a, **k):
        raise RuntimeError("예상 밖")
    monkeypatch.setattr(C, "read", read_boom)
    out2 = _run(dd.fill_remarks_from_cold(_pay(monkeypatch, MOVED), CORP))
    assert out2["status"] == "ok" and out2["remarks_cold_note"] == dd.cold_remarks_note(3)
    assert C.stats()["degraded"].get("error") == 1


def test_degraded_note_is_rendered_in_md_and_kept_in_json(monkeypatch):
    monkeypatch.delenv("OPM_COLD", raising=False)
    pay = _run(dd.fill_remarks_from_cold(_pay(monkeypatch, MOVED), CORP))
    md = dd_tool._render_firm("테스트회사", "005930", {"status": "ok"}, pay)
    assert f"> {dd.cold_remarks_note(3)}" in md
    assert "비고 원문이 없는 결의 3건" in md                  # 종전 「비었다」 표시는 그대로 — 안내가 더해질 뿐


def test_non_ok_payment_result_is_left_alone(monkeypatch):
    assert _run(dd.fill_remarks_from_cold({"status": "db_error"}, CORP)) == {"status": "db_error"}
    assert _run(dd.fill_remarks_from_cold({"status": "ok", "decisions": []}, CORP)) == {"status": "ok", "decisions": []}


# ═════════════════════════════════════════════════════════════════════════════════════════
# 예열 목록 · 목록 모양
# ═════════════════════════════════════════════════════════════════════════════════════════
def test_server_prewarms_div_remarks():
    assert "div_remarks" in C.DEFAULT_DATASETS and "krx_weekly_hist" in C.DEFAULT_DATASETS


def test_serving_file_shape_is_what_the_server_validates(tmp_path):
    """발행기 Spec 의 열쇠 (corp_code, record_date) · 날짜 칸 record_date 가 서버 목록 검증(`_dataset_entry`)을 통과한다."""
    info = build_remarks_serving(tmp_path, SERVED)
    m = json.loads(Path(info["manifest_url"][len("file://"):]).read_text())
    head, entry = C._dataset_entry(m, "div_remarks")
    assert head["key"] == ["corp_code", "record_date"] and head["date_col"] == "record_date" and entry["rows"] == len(SERVED)
