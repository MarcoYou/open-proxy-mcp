# -*- coding: utf-8 -*-
"""연간 재무 수집(`scripts/market_val_series.py`) — 2018 사업보고서의 옛 계정 분류체계(`ifrs_` 접두어)도 읽는다(261019).

종전엔 새 분류체계(`ifrs-full_`) 계정만 정확일치로 찾아, 옛 분류체계로 제출된 2018 사업보고서가 거의 전부 nodata 로 굳었다
(운영 10-19: FY2018 2,599종목 중 2,559 nodata — 005930 DART 응답에 `ifrs_ProfitLossAttributableToOwnersOfParent` 가 있는데도).
그래서 주간 곡선(firm_history)의 FY0 배수가 2020-04(FY2019 공시)부터만 났다. network 0 · DB 0."""
from __future__ import annotations

import importlib.util
import pathlib
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("market_val_series", ROOT / "scripts" / "market_val_series.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: 005930 2018 사업보고서(연결) 응답의 해당 줄 모양 — 계정 코드·재무제표 구분·금액 문자열(실제 응답에서 옮김).
ROWS_2018 = [
    {"sj_div": "BS", "account_id": "ifrs_EquityAttributableToOwnersOfParent", "thstrm_amount": "240068993000000",
     "frmtrm_amount": "207213416000000"},
    {"sj_div": "BS", "account_id": "ifrs_NoncontrollingInterests", "thstrm_amount": "7684184000000"},
    {"sj_div": "IS", "account_id": "ifrs_ProfitLossAttributableToOwnersOfParent", "thstrm_amount": "43890877000000",
     "frmtrm_amount": "41344569000000"},
    {"sj_div": "IS", "account_id": "ifrs_ProfitLossAttributableToNoncontrollingInterests", "thstrm_amount": "453980000000"},
    {"sj_div": "CF", "account_id": "-표준계정코드 미사용-", "thstrm_amount": "8071000000"},
]


def test_old_taxonomy_accounts_are_read_when_the_new_ones_are_absent():
    V = _load()
    attr, eqa = "ifrs-full_ProfitLossAttributableToOwnersOfParent", "ifrs-full_EquityAttributableToOwnersOfParent"
    assert V.gid(ROWS_2018, attr, ("CIS", "IS")) == 43890877000000.0
    assert V.gid(ROWS_2018, attr, ("CIS", "IS"), "frmtrm_amount") == 41344569000000.0
    assert V.gid(ROWS_2018, eqa, ("BS",)) == 240068993000000.0
    # 재무제표 구분은 그대로 지킨다(손익 계정을 재무상태표에서 찾지 않는다)
    assert V.gid(ROWS_2018, attr, ("BS",)) is None


def test_new_taxonomy_wins_and_matching_stays_exact():
    V = _load()
    rows = [{"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent", "thstrm_amount": "1"},
            {"sj_div": "IS", "account_id": "ifrs_ProfitLossAttributableToOwnersOfParent", "thstrm_amount": "2"},
            {"sj_div": "BS", "account_id": "ifrs_EquityAttributableToOwnersOfParentXX", "thstrm_amount": "3"}]
    assert V.gid(rows, "ifrs-full_ProfitLossAttributableToOwnersOfParent", ("IS",)) == 1.0
    assert V.gid(rows, "ifrs-full_EquityAttributableToOwnersOfParent", ("BS",)) is None      # 접두어·꼬리 섞인 이름은 아니다
    assert V.gid(rows, "dart_OperatingIncomeLoss", ("IS",)) is None                          # 옛 이름으로 바꾸는 것은 ifrs-full_ 만


def test_refetch_nodata_years_ignore_the_retry_window():
    V = _load()
    today = date(2026, 10, 19)
    firms = [("D", "c1"), ("E", "c2")]
    have = {(t, y): "ok" for t, _ in firms for y in range(2018, 2026)}
    have[("D", 2018)] = "nodata"              # 공시 시점(2019-04-01) 뒤 7년 — 창(400일) 밖
    have[("E", 2019)] = "nodata"
    assert V.fetch_plan(firms, have, {}, today=today) == []                       # 종전과 같다(창 밖은 그대로)
    assert V.fetch_plan(firms, have, {}, today=today, refetch_nodata_fy={2018}) == [("D", "c1", 2018)]
    assert V.fetch_plan(firms, have, {}, today=today, refetch_nodata_fy=[2018, 2019]) == [("D", "c1", 2018), ("E", "c2", 2019)]
