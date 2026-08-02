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
    assert tup_n == pg_c == sq_c, f"큐 튜플 {tup_n} ≠ 컬럼 {pg_c}"


def test_usage_never_records_which_company_was_queried():
    """텔레메트리에 회사·인자를 남기지 않는다 — 「사용자 조회 결과 저장 안 함」(CLAUDE.md).

    캐시 적중률·응답 크기를 재려고 컬럼을 더할 때 조회 대상이 함께 새면 원칙이 깨진다.
    """
    import re

    src = open("open_proxy_mcp/usage.py", encoding="utf-8").read()
    m = re.search(r"INSERT INTO tool_call_events\((.*?)\)", src, re.S)
    cols = {c.strip() for c in re.sub(r'["\n]', " ", m.group(1)).split(",") if c.strip()}
    for banned in ("corp", "company", "corp_code", "stock_code", "args", "rcept_no", "query"):
        assert not any(banned in c for c in cols), f"조회 대상이 새는 컬럼: {banned}"
