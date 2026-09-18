"""screener 흐름 보기 — 공시 원장 합산 (DB·네트워크 0콜).

지키는 것: ① 흐름 보기 기본 유형은 수주, 분류 단계는 대분류·중분류 둘 다 ② 기간 기본은 원장 최신일까지 7일,
원장 범위 밖은 당긴다 ③ 정정·해지는 새 공시로 세지 않는다 ④ 자회사 계약 재공시는 자회사 자신의 공시와 겹치면
중복으로 빼고, 비율은 모회사 평균·누적에 넣지 않는다 ⑤ 평소는 원장이 본 날 수로 환산하고 1건 미만이면 뒤로
⑥ 큰 수주·올해 누적은 새 계약만 ⑦ DB 미설정·장애·원장 없음을 가른다 ⑧ md 에 두 분류 단계·세 절이 나온다.
"""
import asyncio
import datetime as dt

from open_proxy_mcp.services import disclosure_flow as df
from open_proxy_mcp.services import screener
from open_proxy_mcp.tools import screener as tool

D = dt.date


def _r(no, day, corp, *, kind="order", sub="체결", corr=False, report=None, sector="산업재", industry="자본재",
       amount=None, ratio=None, cp=None, code=None, cap=None):
    return {"rcept_no": no, "rcept_dt": day, "corp_code": corp, "corp_name": corp, "stock_code": code or corp,
            "corp_cls": "Y", "kind": kind, "subtype": sub, "is_correction": corr,
            "report_nm": report or ("[기재정정]" if corr else "") + "단일판매ㆍ공급계약체결",
            "sector": sector, "industry": industry, "mktcap_won": cap, "amount": amount, "ratio": ratio,
            "counterparty": cp, "is_external": True}


def test_levels_and_kinds_defaults():
    assert df.resolve_levels("")[0] == ["sector", "industry"]
    assert df.resolve_levels("대분류")[0] == ["sector"] and df.resolve_levels("중분류")[0] == ["industry"]
    lv, notes = df.resolve_levels("소분류")
    assert lv == ["sector", "industry"] and "알아듣지 못해" in notes[0]
    assert df.resolve_kinds("")[0] == ["order"] and df.resolve_kinds("core")[0] == ["order"]
    assert df.resolve_kinds("자사주, 증자")[0] == ["treasury", "dilutive"]
    kinds, notes = df.resolve_kinds("수주, 공개매수")
    assert kinds == ["order"] and any("원장에 쌓지 않는" in n for n in notes)


def test_period_default_ytd_week_dates_and_clipping():
    today, first, last = D(2026, 9, 18), D(2025, 9, 16), D(2026, 9, 17)
    assert df.resolve_flow_period("", "", "", today, first, last)[:2] == (D(2026, 9, 11), D(2026, 9, 17))
    assert df.resolve_flow_period("since_yesterday", "", "", today, first, last)[:2] == (D(2026, 9, 11), D(2026, 9, 17))
    assert df.resolve_flow_period("올해", "", "", today, first, last)[:2] == (D(2026, 1, 1), D(2026, 9, 17))
    assert df.resolve_flow_period("이번주", "", "", today, first, last)[:2] == (D(2026, 9, 14), D(2026, 9, 17))
    assert df.resolve_flow_period("20260907~20260913", "", "", today, first, last)[:2] == (D(2026, 9, 7), D(2026, 9, 13))
    assert df.resolve_flow_period("", "20260901", "20260905", today, first, last)[:2] == (D(2026, 9, 1), D(2026, 9, 5))
    s, e, notes = df.resolve_flow_period("최근 7일", "", "", today, first, last)
    assert (s, e) == (D(2026, 9, 12), D(2026, 9, 17)) and any("끝날짜를 그날로 당겼다" in n for n in notes)
    s, e, notes = df.resolve_flow_period("20250101~20250201", "", "", today, first, last)
    assert s == first or s == e                                     # 원장 이전은 시작을 당긴다
    assert any("시작날짜를 그날로 당겼다" in n for n in notes)


def test_subsidiary_refiling_duplicate_and_unique():
    rows = [_r("1", D(2026, 1, 16), "넥스텍", amount=100.0, ratio=89.8),
            _r("2", D(2026, 1, 16), "테크", amount=100.0, ratio=89.8,
               report="단일판매ㆍ공급계약체결(자회사의 주요경영사항)"),           # 자회사 공시와 겹침 → 중복
            _r("3", D(2026, 1, 16), "모회사", amount=50.0, ratio=30.0,
               report="단일판매ㆍ공급계약체결(자회사의주요경영사항)"),           # 비상장 자회사 → 유일한 기록
            _r("4", D(2026, 1, 16), "모회사2", amount=None, ratio=None,
               report="단일판매ㆍ공급계약체결(자회사의 주요경영사항)")]          # 금액 모름 → 판정 불가, 남김
    df.mark_subsidiary_filings(rows)
    assert [r["via_sub"] for r in rows] == [False, True, True, True]
    assert [r["sub_dup"] for r in rows] == [False, True, False, False]
    assert [df.is_new(r) for r in rows] == [True, False, True, True]


def test_covered_days_counts_only_complete_scans():
    scan = {(D(2026, 9, 7), "I001"): True, (D(2026, 9, 8), "I001"): False, (D(2026, 9, 9), "B001"): True}
    assert df.covered_days(scan, "I001", D(2026, 9, 7), D(2026, 9, 9)) == 1


def _flow_rows():
    cur = D(2026, 9, 8)
    rows = [
        _r("c1", cur, "A", amount=200.0, ratio=20.0),                       # 자본재 새 계약
        _r("c2", cur, "B", amount=100.0, ratio=10.0),                       # 자본재 새 계약
        _r("c3", cur, "C", corr=True, amount=999.0, ratio=99.0),            # 정정 — 새 공시 아님
        _r("c4", cur, "D", sub="해지", report="단일판매ㆍ공급계약해지"),       # 해지 — 새 공시 아님
        _r("c5", cur, "E", sector="IT", industry="반도체와반도체장비", amount=50.0, ratio=None),
        _r("c6", cur, "F", amount=30.0, ratio=40.0, report="단일판매ㆍ공급계약체결(자회사의 주요경영사항)"),
        _r("c7", cur, "G", sector=None, industry=None, amount=10.0, ratio=5.0),
    ]
    base_days = [D(2026, 6, 8) + dt.timedelta(days=i) for i in range(0, 91, 7)]    # 13주, 주 1건 자본재
    rows += [_r(f"b{i}", d, f"P{i}") for i, d in enumerate(base_days)]
    rows += [_r("bx", D(2026, 7, 1), "Q", corr=True)]                   # 기준의 정정은 평소에 안 넣는다
    df.mark_subsidiary_filings(rows)
    return rows


def test_aggregate_flow_counts_scaling_and_order():
    rows = _flow_rows()
    cur, base = (D(2026, 9, 7), D(2026, 9, 13)), (D(2026, 6, 8), D(2026, 9, 6))
    out = df.aggregate_flow(rows, "order", "industry", cur, base, cur_cov=7, base_cov=91)
    cap = next(r for r in out["rows"] if r["bucket"] == "자본재")
    assert cap["new"] == 3 and cap["corrections"] == 1 and cap["cancels"] == 1      # A·B·F(유일한 자회사 기록)
    assert cap["amount_krw"] == 330 and cap["amount_n"] == 3
    assert cap["avg_ratio_pct"] == 15.0                     # F 의 40%(자회사 매출 기준)는 평균에서 뺀다
    assert cap["base_new"] == 13 and cap["expected"] == 1.0 and cap["vs_base"] == 3.0
    assert cap["base_weekly_avg"] == 1.0 and cap["thin_base"] is False
    semi = next(r for r in out["rows"] if r["bucket"] == "반도체와반도체장비")
    assert semi["new"] == 1 and semi["thin_base"] is True and semi["avg_ratio_pct"] is None
    assert out["rows"][-1]["bucket"] == df.NO_BUCKET                  # 업종 미상은 맨 뒤
    assert out["total"]["new"] == 5 and out["total"]["corrections"] == 1
    half = df.aggregate_flow(rows, "order", "industry", cur, base, cur_cov=7, base_cov=45)
    assert next(r for r in half["rows"] if r["bucket"] == "자본재")["expected"] == round(13 * 7 / 45, 2)


def test_large_orders_and_coverage():
    rows = _flow_rows()
    lo = df.large_orders(rows, (D(2026, 9, 7), D(2026, 9, 13)), 15.0)
    assert [r["corp_name"] for r in lo["rows"]] == ["F", "A"]          # 40% · 20%, 정정 C(99%)는 빠진다
    assert lo["rows"][0]["via_subsidiary"] is True and lo["corrections_over_min"] == 1
    assert lo["ratio_unread"] == 1 and lo["new_contracts"] == 5
    cov = df.order_coverage(rows, (D(2026, 1, 1), D(2026, 9, 13)))
    a = next(r for r in cov["rows"] if r["corp_name"] == "A")
    assert a["new"] == 1 and a["ratio_sum_pct"] == 20.0 and a["amount_krw"] == 200
    assert not any(r["corp_name"] == "F" for r in cov["rows"])       # 자회사 계약만 낸 회사는 누적에 없다
    assert not any(r["corp_name"] == "C" for r in cov["rows"])       # 정정만 있는 회사도 없다
    assert cov["rows"][0]["corp_name"] == "A"


def _stub(monkeypatch, *, ready=True, bounds=(D(2025, 9, 16), D(2026, 9, 13)), rows=None, scan_ok=True):
    monkeypatch.setattr(df, "_ledger_ready", lambda: ready)
    monkeypatch.setattr(df, "_ledger_bounds", lambda: bounds)
    monkeypatch.setattr(screener, "_krx_latest_dd", lambda: "20260911")

    def ev(kinds, since, until, tickers, corp_cls):
        return _flow_rows() if rows is None else rows
    monkeypatch.setattr(df, "_fetch_events", ev)

    def sc(since, until):
        out, d = {}, since
        while d <= until:
            for code in ("I001", "B001", "D001", "I002"):
                out[(d, code)] = scan_ok
            d += dt.timedelta(days=1)
        return out
    monkeypatch.setattr(df, "_fetch_scan", sc)


def test_build_payload_ok_and_render(monkeypatch):
    _stub(monkeypatch)
    p = asyncio.run(df.build_flow_payload(period="20260907~20260913"))
    assert p["status"] == "ok" and p["data"]["view"] == "flow"
    assert p["data"]["levels"] == ["sector", "industry"]
    assert p["data"]["period"] == {"start": "2026-09-07", "end": "2026-09-13", "days": 7}
    assert p["data"]["baseline"]["weeks"] == 13 and "large_orders" in p["data"] and "order_coverage" in p["data"]
    md = tool._render_flow(p)
    assert md.startswith("# 공시 흐름 — 수주 (2026-09-07 ~ 2026-09-13)")
    assert "— WICS 대분류" in md and "— WICS 중분류" in md
    assert "## 큰 수주 — 매출 대비 10% 이상" in md and "(자회사 계약)" in md
    assert "## 올해 누적 수주" in md and "| **합계** |" in md


def test_build_payload_only_one_level_and_other_kind(monkeypatch):
    rows = [_r("t1", D(2026, 9, 8), "A", kind="treasury", sub="취득", report="자기주식취득결정", amount=5.0)]
    _stub(monkeypatch, rows=rows)
    p = asyncio.run(df.build_flow_payload(types="자사주", period="20260907~20260913", level="대분류"))
    assert p["data"]["levels"] == ["sector"] and "large_orders" not in p["data"]
    md = tool._render_flow(p)
    assert "WICS 중분류" not in md and "| 세부 |" in md and "취득 1" in md


def test_status_split_and_coverage_warning(monkeypatch):
    monkeypatch.setattr(df, "_ledger_ready", lambda: None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert asyncio.run(df.build_flow_payload())["status"] == "db_unconfigured"
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    assert asyncio.run(df.build_flow_payload())["status"] == "db_error"
    monkeypatch.setattr(df, "_ledger_ready", lambda: False)
    assert asyncio.run(df.build_flow_payload())["status"] == "no_data"
    _stub(monkeypatch, scan_ok=False)
    p = asyncio.run(df.build_flow_payload(period="20260907~20260913"))
    assert any("원장이 본 날은 0일" in w for w in p["warnings"])


def test_view_words():
    for w in ("흐름", "flow", "업종별", "평소 대비"):
        assert tool._is_flow(w)
    assert not tool._is_flow("") and not tool._is_flow("카드")
