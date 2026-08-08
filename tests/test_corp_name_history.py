"""사명이 바뀐 회사를 옛 이름으로도 찾는다.

DART 는 옛 사명을 **어디에도** 주지 않는다. corpCode.xml 에는 현재 사명만 있고, 공시 목록의
`corp_name`·`flr_nm` 조차 과거 공시에까지 현재 사명을 소급해 채운다 — 실측: 036560 의 2024년
공시가 「KZ정밀」로 나온다(당시 사명은 영풍정밀). 문서 본문의 「(구 영풍정밀)」 표기를 긁어보면
1,129건에서 524건이 걸리지만 「회생채권」·「808」 같은 쓰레기가 태반이라 쓸 수 없다.

그래서 우리가 7일마다 받는 corpCode.xml 을 스냅샷으로 남기는 것이 유일한 구조적 방법이다.
한계는 분명하다 — **스냅샷을 남기기 시작한 뒤의 변경만** 잡는다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from open_proxy_mcp.dart import client as dart_client


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "master.db"
    monkeypatch.setattr(dart_client, "_MASTER_DB_PATH", path)
    return path


def _corp(code: str, name: str, stock: str = "") -> dict:
    return {"corp_code": code, "corp_name": name, "corp_eng_name": "",
            "stock_code": stock, "modify_date": "20260101"}


def test_a_rename_survives_the_full_reload(db) -> None:
    """저장은 매번 DELETE 후 전체 재적재다 — 지우기 전에 이력을 남기지 않으면 옛 이름이 증발한다."""
    dart_client.DartClient._master_db_save([_corp("001", "옛이름", "036560")])
    dart_client.DartClient._master_db_save([_corp("001", "새이름", "036560")])

    found = dart_client.DartClient.lookup_former_name("옛이름")
    assert found is not None
    assert found["current_name"] == "새이름"
    assert found["stock_code"] == "036560"


def test_the_current_name_is_not_reported_as_a_rename(db) -> None:
    """현재 이름으로 물은 것을 「바뀌었다」고 하면 안 된다 — 이력에는 현재 이름도 들어 있다."""
    dart_client.DartClient._master_db_save([_corp("001", "그대로", "000660")])
    assert dart_client.DartClient.lookup_former_name("그대로") is None


def test_an_unknown_name_returns_nothing(db) -> None:
    dart_client.DartClient._master_db_save([_corp("001", "어떤회사")])
    assert dart_client.DartClient.lookup_former_name("없는회사") is None
    assert dart_client.DartClient.lookup_former_name("") is None


def test_repeated_saves_do_not_pile_up_rows(db) -> None:
    """갱신마다 12만 행을 넣는다 — 이름이 안 바뀌면 행이 늘지 않아야 한다."""
    for _ in range(3):
        dart_client.DartClient._master_db_save([_corp("001", "그대로"), _corp("002", "저기")])
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM corp_name_history").fetchone()[0]
    conn.close()
    assert n == 2


def test_the_message_says_where_the_company_went(db) -> None:
    """「찾지 못했다」로 끝내지 않고 현재 사명을 알려준다."""
    from open_proxy_mcp.services.company import company_not_found_warning

    dart_client.DartClient._master_db_save([_corp("001", "옛이름", "036560")])
    dart_client.DartClient._master_db_save([_corp("001", "새이름", "036560")])

    msg = company_not_found_warning("옛이름")
    assert "새이름" in msg and "036560" in msg
    assert "찾지 못했다" not in msg

    # 이력에 없으면 종전 안내(종목코드로 재조회)를 그대로 쓴다
    assert "종목코드" in company_not_found_warning("전혀모르는회사")


def test_the_latest_rename_wins_when_a_name_was_used_twice(db) -> None:
    """같은 이름을 쓴 법인이 둘이면 가장 최근 것을 준다 — 오래된 쪽을 집어 엉뚱한 회사로 보내지 않는다."""
    dart_client.DartClient._master_db_save([_corp("001", "공용이름"), _corp("002", "다른회사")])
    dart_client.DartClient._master_db_save([_corp("001", "A로변경"), _corp("002", "공용이름")])
    dart_client.DartClient._master_db_save([_corp("001", "A로변경"), _corp("002", "B로변경")])

    conn = sqlite3.connect(db)
    conn.execute("UPDATE corp_name_history SET last_seen = ? WHERE corp_code = '002'",
                 (datetime(2030, 1, 1).isoformat(),))
    conn.commit()
    conn.close()

    assert dart_client.DartClient.lookup_former_name("공용이름")["current_name"] == "B로변경"
