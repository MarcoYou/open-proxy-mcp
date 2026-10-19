"""가짜 DB 의 연간·분기 재무 행을 읽기 SQL 모양에 맞춘다(261020 p1e).

연간·분기 재무 읽기(`price_multiple_data._FIN_FY_SQL`·`_FIN_Q_SQL`)가 결산기 칸(반기·분기 결산 회사 — `period_cells`)의 정정 칸·공시일을 함께
고른다. 종전 모양으로 적힌 가짜 행(연간 5칸·분기 4칸)에 「결산기 칸 아님」 빈 칸을 붙인다. 종전 SQL(origin/main 대조용 옛 모듈)이면 그대로다."""


def pad_fin_rows(sql: str, rows):
    s = " ".join(str(sql).split())
    n = 8 if ("FROM dart_finstat_y y" in s and "LEFT JOIN dart_finstat_q" in s) else (
        7 if ("FROM dart_finstat_q" in s and "CASE WHEN restate_why LIKE" in s) else 0)
    if not n or not rows:
        return rows
    return [tuple(r) + (None,) * (n - len(r)) if len(r) < n else tuple(r) for r in rows]
