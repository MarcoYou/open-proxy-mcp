"""공시 원장 재스캔이 이미 얻은 결과를 덮지 않는다 — Postgres 17 에서 증명한다 (261019).

10-19 운영 실측(재설계 6단계 확인 중 `disclosure-ledger-daily` 수동 실행):
  ① 10-16 하루 재스캔에서 DART 접속 시간 초과가 네 코드 모두에 났다 → `dart_events_scan` 의 「다 받음」(B001 34건 등)이
     「total 0 · 불완전 · transport:ConnectTimeout」으로 덮였다. 그 날은 다음 정기 실행의 3일 창 밖이라 다시 볼 일이 없고,
     서버 흐름 보기는 다 본 날만 세므로 10-16 이 비교에서 조용히 빠진다.
  ② 상세 끈 재스캔(`--no-details`)이 이미 본 결과 `no_data` 14행을 `scan_only`(아직 안 봄)로 되돌렸다.
고침: 커버리지는 새 시도가 다 받았거나 옛 행이 다 받지 못한 때만 갈아 쓰고, 상세는 새 값이 scan_only 면 옛 결과를 지킨다.
Docker 가 없으면 건너뛴다(`tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

from datetime import date

import pytest

psycopg = pytest.importorskip("psycopg")

from scripts import disclosure_ledger as DL  # noqa: E402

DAY = date(2026, 10, 16)


@pytest.fixture
def ldb(pg17):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(DL.DDL)
    return pg17


def _scan(url, **kw):
    row = {"scan_dd": DAY, "code": "B001", "total": None, "total_pages": None, "fetched_pages": 0,
           "complete": False, "error": None} | kw
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(DL.UPSERT_SCAN, row)
        return c.execute("SELECT total, total_pages, fetched_pages, complete, error FROM dart_events_scan "
                         "WHERE scan_dd=%s AND code='B001'", (DAY,)).fetchone()


def _event(url, status, detail=None, note=None):
    r = DL.row_from_item({"rcept_no": "20261016000001", "rcept_dt": "20261016", "corp_code": "00000001",
                          "corp_name": "가", "stock_code": "000001", "corp_cls": "Y",
                          "report_nm": "현금ㆍ현물배당결정", "flr_nm": "가"}, {"dividend"})
    assert r is not None
    r["detail_status"], r["detail"], r["detail_note"] = status, detail, note
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(DL.UPSERT_EVENT, DL.db_row(r))
        return c.execute("SELECT detail_status, detail, detail_note FROM dart_events WHERE rcept_no='20261016000001'").fetchone()


def test_a_failed_rescan_does_not_overwrite_complete_coverage(ldb):
    done = _scan(ldb, total=34, total_pages=1, fetched_pages=1, complete=True)
    assert done == (34, 1, 1, True, None)
    # 재스캔이 접속 시간 초과(scan_day 가 전송 예외를 이렇게 적는다) — 옛 「다 받음」을 지킨다
    assert _scan(ldb, error="transport:ConnectTimeout") == done
    # DART 가 일부 페이지만 준 재스캔도 덮지 않는다
    assert _scan(ldb, total=40, total_pages=2, fetched_pages=1, complete=False, error="page 2: 020") == done
    # 새로 다 받으면(늦은 접수로 건수가 늘었어도) 갈아 쓴다
    assert _scan(ldb, total=35, total_pages=1, fetched_pages=1, complete=True) == (35, 1, 1, True, None)


def test_incomplete_coverage_is_still_refreshed_until_complete(ldb):
    assert _scan(ldb, error="transport:ConnectTimeout")[3:] == (False, "transport:ConnectTimeout")
    assert _scan(ldb, total=40, total_pages=2, fetched_pages=1, complete=False, error="page 2: 020") == \
        (40, 2, 1, False, "page 2: 020")                                   # 불완전끼리는 최신 시도를 적는다
    assert _scan(ldb, total=40, total_pages=2, fetched_pages=2, complete=True) == (40, 2, 2, True, None)


def test_a_rescan_without_details_keeps_an_earlier_detail_result(ldb):
    assert _event(ldb, "no_data", note="표 없음")[0] == "no_data"
    # 상세 끈 재스캔·콜 상한에 걸린 행 — 이번엔 안 봤을 뿐이라 옛 결과를 지킨다
    assert _event(ldb, "scan_only", note="상세 콜 상한 도달 — 다음 실행에서 채운다") == ("no_data", None, "표 없음")
    # 다시 보면 새 결과로 바뀐다(parsed 가 되면 값까지)
    got = _event(ldb, "parsed", detail={"dps_krw": 100})
    assert got[0] == "parsed" and got[1] == {"dps_krw": 100}
    # parsed 는 그 뒤 scan_only·no_data 로 되돌지 않는다(260917 규칙 그대로)
    assert _event(ldb, "scan_only")[:2] == ("parsed", {"dps_krw": 100})
    assert _event(ldb, "no_data")[:2] == ("parsed", {"dps_krw": 100})


def test_scan_only_rows_still_take_the_first_real_result_and_the_latest_cap_note(ldb):
    assert _event(ldb, "scan_only") == ("scan_only", None, None)
    assert _event(ldb, "scan_only", note="상세 콜 상한 도달 — 다음 실행에서 채운다") == \
        ("scan_only", None, "상세 콜 상한 도달 — 다음 실행에서 채운다")    # 아직 안 본 행은 최신 안내를 적는다
    assert _event(ldb, "no_data", note="표 없음") == ("no_data", None, "표 없음")   # 260917: scan_only 는 지킬 값이 아니다
