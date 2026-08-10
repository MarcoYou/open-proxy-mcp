import logging

import open_proxy_mcp.dart.client  # noqa: F401


def test_httpx_request_info_logging_is_suppressed_for_query_key_safety() -> None:
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_usage_insert_columns_match_placeholders_and_tuple():
    """컬럼 수 · 플레이스홀더 · 큐 튜플이 어긋나면 **조용히 다른 컬럼에 값이 들어간다**.

    260704 mkt_fund_hist 사고가 그것이었다(DDL 선언 순서와 실제 컬럼 순서가 어긋나
    문자열이 double precision 컬럼에 들어감). INSERT 는 컬럼명을 명시하고 있으니
    남은 위험은 **개수 불일치**뿐이라 그것만 계약으로 고정한다.
    """
    import re

    src = open("open_proxy_mcp/usage.py", encoding="utf-8").read()

    def _count(pattern):
        m = re.search(pattern, src, re.S)
        cols = [c for c in re.sub(r'["\n]', " ", m.group(1)).split(",") if c.strip()]
        ph = [x for x in re.sub(r'["\n\s]', "", m.group(2)).split(",") if x]
        return len(cols), len(ph)

    pg_c, pg_p = _count(r"INSERT INTO tool_call_events\((.*?)\).*?VALUES\((.*?)\)")
    sq_c, sq_p = _count(r"INSERT OR IGNORE INTO events\((.*?)\).*?VALUES\((.*?)\)")
    tup = re.search(r"_q\.put_nowait\(\((.*?)\)\)", src, re.S).group(1)
    tup_n = len([x for x in re.sub(r"\s", "", tup).split(",") if x])

    assert pg_c == pg_p, f"Postgres 컬럼 {pg_c} ≠ 플레이스홀더 {pg_p}"
    assert sq_c == sq_p, f"SQLite 컬럼 {sq_c} ≠ 플레이스홀더 {sq_p}"
    # 큐 튜플은 이벤트 컬럼보다 **정확히 하나 많다** — `corp_codes` 는 워커까지 실려 가지만
    # 이벤트 행에는 안 들어가고 `corp_daily` 집계로만 올라간다(260810, 사용자-기업 연결 제거).
    # 하나가 아니라 둘 이상 많아지면 어딘가에서 값이 조용히 버려지고 있다는 뜻이다.
    assert pg_c == sq_c, f"Postgres 컬럼 {pg_c} ≠ SQLite 컬럼 {sq_c}"
    assert tup_n == pg_c + 1, f"큐 튜플 {tup_n} ≠ 컬럼 {pg_c} + corp_codes 1"


def test_usage_records_only_normalized_corp_codes_never_raw_arguments():
    """텔레메트리에 남길 수 있는 조회 대상은 **정규화된 corp_code 하나뿐**이고,
    그것도 **사용자와 같은 행에는 못 둔다**(260810).

    260802 에는 회사를 아예 안 남겼다. 260804 에 「어떤 기업이 많이 쓰이나」를 보려고
    `corp_codes` 를 열었는데, 이벤트 행에 `key_hash`·`ts_ns` 와 나란히 앉아 셋이 붙으면
    **「이 사용자가 언제 어느 기업을 조사했는지」**가 됐다 — 조사 이력이다. 회사 이름이
    공개 정보라는 것과 무관한 문제다(무엇을 언제 봤는가 자체가 정보다).
    그래서 260810 에 사용자를 떼고 `corp_daily(day, corp_code, requests)` 로만 올린다.

    원문·인자·문서번호는 **여전히 막는다**:
      · 자유 텍스트는 정규화가 안 돼 집계가 무의미하고, 무엇이 딸려 들어올지 모른다.
      · rcept_no 는 「어느 문서를 열었나」라 조회 **결과**에 가깝다.
    이 목록이 늘어난다면 그건 결정이어야지 부주의여선 안 된다.
    """
    import re

    src = open("open_proxy_mcp/usage.py", encoding="utf-8").read()
    m = re.search(r"INSERT INTO tool_call_events\((.*?)\)", src, re.S)
    cols = {c.strip() for c in re.sub(r'["\n]', " ", m.group(1)).split(",") if c.strip()}

    assert "corp_codes" not in cols, (
        "기업이 사용자와 같은 행에 다시 들어갔다 — 조사 이력이 쌓인다")
    assert "corp_daily" in src, "집계 경로가 사라졌다 — 기업 신호를 통째로 잃는다"
    for banned in ("company", "stock_code", "args", "arguments", "rcept_no",
                   "query", "corp_name", "raw"):
        assert not any(banned in c for c in cols), f"조회 원문이 새는 컬럼: {banned}"
    assert not [c for c in cols if "corp" in c], f"corp 계열 컬럼이 남아 있다: {cols}"

    # 집계 쪽도 코드만 받는다 — 이름·해시가 붙으면 뗀 의미가 없다.
    agg = re.search(r"INSERT INTO corp_daily\((.*?)\)", src, re.S)
    assert agg, "corp_daily INSERT 를 못 찾았다"
    agg_cols = {c.strip() for c in agg.group(1).split(",") if c.strip()}
    assert agg_cols == {"day", "corp_code", "requests"}, agg_cols
