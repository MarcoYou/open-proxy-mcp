import logging

import open_proxy_mcp.dart.client  # noqa: F401


def test_httpx_request_info_logging_is_suppressed_for_query_key_safety() -> None:
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_usage_insert_columns_match_placeholders_and_tuple():
    """컬럼 수 · 플레이스홀더 · 큐 튜플이 어긋나면 **조용히 다른 컬럼에 값이 들어간다**.

    260704 mkt_fund_hist 사고가 그것이었다(DDL 선언 순서와 실제 컬럼 순서가 어긋나
    문자열이 double precision 컬럼에 들어감).

    260824: 네 곳에 손으로 나열하던 목록을 `_EVENT_COLUMNS` 한 곳에서 만들도록 바꿔
    **어긋날 자리를 없앴다**. 그래도 검사는 남긴다 — 「구조가 지킨다」는 주장 자체가
    회귀 대상이고, 누군가 다시 손으로 나열하면 여기서 걸려야 한다.
    """
    import re

    from open_proxy_mcp.usage import _EVENT_COLUMNS, _insert_sql

    n = len(_EVENT_COLUMNS)
    for table, ph in (("ops_tool_calls", "%s"), ("events", "?")):
        sql = _insert_sql(table, ph)
        cols = [c for c in sql[sql.index("(") + 1:sql.index(")")].split(",") if c.strip()]
        vals = sql[sql.index("VALUES"):]
        assert len(cols) == n, f"{table} 컬럼 {len(cols)} ≠ SSOT {n}"
        assert vals.count(ph) == n, f"{table} 플레이스홀더 {vals.count(ph)} ≠ SSOT {n}"

    # 큐 튜플도 같은 목록에서 조립돼야 한다 — 손으로 나열하면 여기서 걸린다.
    src = open("open_proxy_mcp/usage.py", encoding="utf-8").read()
    assert re.search(r"_q\.put_nowait\(tuple\(vals\[c\] for c in _EVENT_COLUMNS\)\)", src), \
        "큐 튜플이 _EVENT_COLUMNS 순서로 조립되지 않는다"


def test_usage_records_only_normalized_corp_codes_never_raw_arguments():
    """텔레메트리에 남길 수 있는 조회 대상은 **정규화된 corp_code 하나뿐**이다.

    260802 에는 회사를 아예 안 남겼다. 260804 에 「어떤 기업이 많이 쓰이나」를 보려고
    `corp_codes` 를 열었고, 260810 에 「이벤트 행에 두면 조사 이력이 된다」는 이유로 뺐다가,
    260817 에 세션·재방문 분석을 위해 되돌렸다. 되돌린 쪽이 이 테스트의 관심사는 아니다 —
    **무엇이 여전히 금지인가**가 관심사다.

    원문·인자·문서번호는 정책이 세 번 바뀌는 동안 한 번도 안 열렸고, 지금도 막는다:
      · 자유 텍스트는 정규화가 안 돼 집계가 무의미하고, 무엇이 딸려 들어올지 모른다.
      · rcept_no 는 「어느 문서를 열었나」라 조회 **결과**에 가깝다.
    이 목록이 늘어난다면 그건 결정이어야지 부주의여선 안 된다.
    """
    import re

    from open_proxy_mcp.usage import _EVENT_COLUMNS

    src = open("open_proxy_mcp/usage.py", encoding="utf-8").read()
    cols = set(_EVENT_COLUMNS)

    assert "ops_corp_daily" in src, "집계 경로가 사라졌다 — 드레인 뒤 기업 신호가 통째로 없어진다"
    for banned in ("company", "stock_code", "args", "arguments", "rcept_no",
                   "query", "corp_name", "raw"):
        assert not any(banned in c for c in cols), f"조회 원문이 새는 컬럼: {banned}"
    # corp 계열은 **정규화된 코드 목록 하나만** 허용한다. 이름·원문이 붙은 변형은 막는다.
    assert {c for c in cols if "corp" in c} == {"corp_codes"}, \
        f"허용되지 않은 corp 계열 컬럼: {sorted(c for c in cols if 'corp' in c)}"

    # 집계 쪽도 코드만 받는다 — 이름·해시가 붙으면 뗀 의미가 없다.
    agg = re.search(r"INSERT INTO ops_corp_daily\((.*?)\)", src, re.S)
    assert agg, "ops_corp_daily INSERT 를 못 찾았다"
    agg_cols = {c.strip() for c in agg.group(1).split(",") if c.strip()}
    assert agg_cols == {"log_dd", "corp_code", "requests"}, agg_cols
