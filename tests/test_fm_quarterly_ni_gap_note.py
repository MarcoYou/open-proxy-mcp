"""financial_metrics 분기 표 — 순이익이 영업이익의 1.3배를 넘는 분기에 영업외손익·법인세 설명 줄을 단다(DATA-NI4).

SK하이닉스(000660) 2026 반기보고서(연결, 3개월 칸) 원문 대조: 영업이익 60.54조 · 법인세차감전 순이익 122.71조 · 당기순이익(총계) 93.92조 ·
법인세비용 28.79조 → 순이익 > 영업이익은 오류가 아니라 영업외 이익이다(전체 재무제표: 금융수익 65.86조·금융비용 3.64조·지분법 −0.20조).
값은 그대로 두고 주요계정의 「법인세차감전 순이익」으로 영업외손익(= 세전 − 영업이익)·법인세(= 세전 − 순이익)를 구해 함께 싣는다. network 0 · DB 가짜.
"""
from __future__ import annotations

import asyncio

from open_proxy_mcp import db
from open_proxy_mcp.services import financial_metrics as FM
from open_proxy_mcp.tools.financial_metrics import _render_quarterly

MIL = 1_000_000


def _rows(end: str, op: tuple, pretax: tuple | None, ni: tuple, fs: str = "CFS") -> list[dict]:
    """(3개월, 누적) 원 단위 문자열 — DART 주요계정 모양."""
    base = {"fs_div": fs, "thstrm_dt": end, "ord": "1"}
    rows = [
        {**base, "sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "348862214000000"},
        {**base, "sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "262693228000000"},
        {**base, "sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "79318746000000", "thstrm_add_amount": "131895033000000"},
        {**base, "sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": str(op[0]), "thstrm_add_amount": str(op[1])},
    ]
    if pretax is not None:
        rows.append({**base, "sj_div": "IS", "account_nm": "법인세차감전 순이익",
                     "thstrm_amount": str(pretax[0]), "thstrm_add_amount": str(pretax[1])})
    rows.append({**base, "sj_div": "IS", "account_nm": "당기순이익(손실)", "thstrm_amount": str(ni[0]), "thstrm_add_amount": str(ni[1])})
    return rows


def _run(monkeypatch, reports: dict, end_year=2026):
    async def fake_acnt(corp_code, year, reprt_code, fs_div):
        r = reports.get((year, reprt_code))
        return (r, None) if r else ([], "no_filing")

    monkeypatch.setattr(FM, "_safe_fetch_acnt", fake_acnt)
    monkeypatch.setattr(db, "pg_rows", lambda sql, params=(): [])
    out, ws = asyncio.run(FM._build_quarterly("00164779", end_year, "CFS", fiscal_month=12, stock_code="000660"))
    return out, ws


# SK하이닉스 2026 (원문, 백만원 단위를 원으로): Q1 = 반기 누적 − Q2
Q2 = _rows("2026.06.30",
           op=(60_542_608 * MIL, 98_152_891 * MIL),
           pretax=(122_708_355 * MIL, 174_325_213 * MIL),
           ni=(93_922_593 * MIL, 134_268_502 * MIL))
Q1 = _rows("2026.03.31",
           op=(37_610_283 * MIL, 37_610_283 * MIL),
           pretax=(51_616_858 * MIL, 51_616_858 * MIL),
           ni=(40_345_909 * MIL, 40_345_909 * MIL))


def test_000660_2026_q2_net_income_above_operating_gets_non_operating_and_tax_columns(monkeypatch):
    out, _ws = _run(monkeypatch, {(2026, "11012"): Q2})
    q2 = next(r for r in out if r["fiscal_quarter"] == "Q2")
    # 값은 원문 그대로(총계) — 고치지 않는다.
    assert q2["operating_profit_krw"] == 60_542_608 * MIL and q2["net_income_krw"] == 93_922_593 * MIL
    assert q2["income_before_tax_krw"] == 122_708_355 * MIL
    assert q2["non_operating_net_krw"] == 62_165_747 * MIL        # 세전 − 영업이익
    assert q2["income_tax_krw"] == 28_785_762 * MIL               # 세전 − 순이익 (원문 법인세비용 28,785,762 와 같다)
    assert q2["net_to_operating_ratio"] == 1.55
    # 세 값이 닫힌다: 영업이익 + 영업외손익 − 법인세 = 순이익
    assert q2["operating_profit_krw"] + q2["non_operating_net_krw"] - q2["income_tax_krw"] == q2["net_income_krw"]


def test_render_adds_an_explanation_line_only_for_flagged_quarters(monkeypatch):
    out, _ws = _run(monkeypatch, {(2026, "11013"): Q1, (2026, "11012"): Q2})
    q1 = next(r for r in out if r["fiscal_quarter"] == "Q1")
    assert "non_operating_net_krw" not in q1 or q1["non_operating_net_krw"] is None       # 40.3조 / 37.6조 = 1.07배 — 기준 아래
    md = "\n".join(_render_quarterly({"quarterly": out}))
    assert "2026-Q2" in md and "영업외손익" in md and "법인세" in md
    line = next(l for l in md.splitlines() if "영업외손익" in l and "2026-Q2" in l)
    assert "62.2조원" in line and "28.8조원" in line and "1.55배" in line
    assert "2026-Q1" not in line
    assert sum(1 for l in md.splitlines() if "영업외손익" in l and "법인세" in l and "배" in l and l.startswith(">")) == 1


def test_not_flagged_quarter_has_no_explanation_line(monkeypatch):
    out, _ws = _run(monkeypatch, {(2026, "11013"): Q1})
    md = "\n".join(_render_quarterly({"quarterly": out}))
    assert "영업외손익" not in md


def test_no_pretax_row_in_the_report_means_no_explanation_and_no_crash(monkeypatch):
    rows = _rows("2026.06.30", op=(60_542_608 * MIL, 98_152_891 * MIL), pretax=None, ni=(93_922_593 * MIL, 134_268_502 * MIL))
    out, _ws = _run(monkeypatch, {(2026, "11012"): rows})
    q2 = out[0]
    assert q2["net_income_krw"] == 93_922_593 * MIL
    assert q2.get("income_before_tax_krw") is None and q2.get("non_operating_net_krw") is None
    assert "영업외손익" not in "\n".join(_render_quarterly({"quarterly": out}))


def test_operating_loss_quarter_is_never_flagged(monkeypatch):
    """영업이익이 0 이하면 순이익/영업이익 비가 뜻이 없다 — 설명 줄을 달지 않는다."""
    rows = _rows("2026.06.30", op=(-5 * MIL, -9 * MIL), pretax=(80 * MIL, 90 * MIL), ni=(60 * MIL, 70 * MIL))
    out, _ws = _run(monkeypatch, {(2026, "11012"): rows})
    assert out[0].get("non_operating_net_krw") is None
    assert "영업외손익" not in "\n".join(_render_quarterly({"quarterly": out}))


def test_q4_pretax_is_annual_minus_nine_month_cumulative(monkeypatch):
    q3 = _rows("2026.09.30", op=(10 * MIL, 100 * MIL), pretax=(30 * MIL, 150 * MIL), ni=(25 * MIL, 120 * MIL))
    annual = _rows("2026.12.31", op=(150 * MIL, 150 * MIL), pretax=(400 * MIL, 400 * MIL), ni=(330 * MIL, 330 * MIL))
    out, _ws = _run(monkeypatch, {(2026, "11014"): q3, (2026, "11011"): annual})
    q4 = next(r for r in out if r["fiscal_quarter"] == "Q4")
    assert q4["operating_profit_krw"] == 50 * MIL and q4["net_income_krw"] == 210 * MIL
    assert q4["income_before_tax_krw"] == 250 * MIL                       # 연간 400 − 9개월 누적 150
    assert q4["non_operating_net_krw"] == 200 * MIL and q4["income_tax_krw"] == 40 * MIL
    assert q4["net_to_operating_ratio"] == 4.2


def test_q4_without_nine_month_cumulative_never_shows_the_annual_pretax_as_a_quarter(monkeypatch):
    annual = _rows("2026.12.31", op=(150 * MIL, 150 * MIL), pretax=(400 * MIL, 400 * MIL), ni=(330 * MIL, 330 * MIL))
    out, _ws = _run(monkeypatch, {(2026, "11011"): annual})
    q4 = out[0]
    assert q4["basis"] == "annual_cumulative"
    assert q4.get("income_before_tax_krw") is None and q4.get("non_operating_net_krw") is None
    assert "영업외손익" not in "\n".join(_render_quarterly({"quarterly": out}))


# --- 중단영업 분기: 세전 − 순이익 이 법인세가 아니다 (고치기 모드 지적 fin-ni-note-disc-ops-as-tax) -------------------
# 합성 픽스처(086520 2021 반기 꼴): 영업이익 200억 · 법인세차감전 순이익 250억 · 당기순이익(총계) 5,786억(계속 200억 + 중단영업 5,586억).
# 세전 − 순이익 = −5,536억 은 음수라 법인세일 수 없다. 이름을 법인세로 붙이면 원문에 없는 거짓 설명이 된다.
DISC = _rows("2026.06.30",
             op=(20_000 * MIL, 40_000 * MIL),
             pretax=(25_000 * MIL, 50_000 * MIL),
             ni=(578_600 * MIL, 600_000 * MIL))


def test_discontinued_ops_quarter_does_not_call_pretax_minus_net_a_tax(monkeypatch):
    out, _ws = _run(monkeypatch, {(2026, "11012"): DISC})
    q2 = next(r for r in out if r["fiscal_quarter"] == "Q2")
    assert q2["net_income_krw"] == 578_600 * MIL and q2["income_before_tax_krw"] == 25_000 * MIL     # 값은 원문 그대로
    assert q2["non_operating_net_krw"] == 5_000 * MIL                                                  # 세전 − 영업이익은 사실
    assert q2.get("income_tax_krw") is None                                                            # 음수는 법인세가 아니다
    assert q2["pretax_minus_net_krw"] == -553_600 * MIL                                                # 기준을 밝힌 칸
    assert q2["net_to_operating_ratio"] == 28.93


def test_discontinued_ops_quarter_line_names_the_basis_and_drops_the_tax_sentence(monkeypatch):
    out, _ws = _run(monkeypatch, {(2026, "11012"): DISC})
    md = "\n".join(_render_quarterly({"quarterly": out}))
    line = next(l for l in md.splitlines() if "2026-Q2" in l and "28.93배" in l)
    assert "− 법인세" not in line and "차이는 영업외 손익과 세금이다" not in line      # 거짓 설명 문장 없음
    assert "세전 − 순이익" in line and "중단영업" in line                           # 기준을 밝히고 법인세 외 항목 가능성을 적는다
    assert "법인세 -5,536" not in line and "법인세 −5,536" not in line                    # 음수 금액에 법인세 이름을 붙이지 않는다
    assert "영업외손익" in line and "50억" in line


def test_net_income_just_above_pretax_is_not_a_tax_either(monkeypatch):
    rows = _rows("2026.06.30", op=(100 * MIL, 100 * MIL), pretax=(200 * MIL, 200 * MIL), ni=(201 * MIL, 201 * MIL))
    out, _ws = _run(monkeypatch, {(2026, "11012"): rows})
    assert out[0].get("income_tax_krw") is None and out[0]["pretax_minus_net_krw"] == -1 * MIL


def test_zero_tax_when_net_equals_pretax_keeps_the_tax_column(monkeypatch):
    rows = _rows("2026.06.30", op=(100 * MIL, 100 * MIL), pretax=(200 * MIL, 200 * MIL), ni=(200 * MIL, 200 * MIL))
    out, _ws = _run(monkeypatch, {(2026, "11012"): rows})
    assert out[0]["income_tax_krw"] == 0 and out[0].get("pretax_minus_net_krw") is None


# --- 법인세수익 분기: 음수 차이가 바로 법인세(수익)일 수 있다 (고치기 모드 지적 fin-ni-note-neg-gap-tax-benefit) ---------
# 합성 픽스처: 영업이익 100억 · 법인세차감전 순이익 100억 · 당기순이익 140억 → 세전 − 순이익 = −40억 은 전부 법인세수익(이연법인세자산 인식 등)이다.
# 주요계정에는 법인세비용 행이 없어 도구는 음수의 원인을 가를 수 없다 — 「법인세가 아니다」로 단정하지 않고 가능성(법인세수익·중단영업)만 늘어놓는다.
TAX_BENEFIT = _rows("2026.06.30",
                    op=(10_000 * MIL, 20_000 * MIL),
                    pretax=(10_000 * MIL, 20_000 * MIL),
                    ni=(14_000 * MIL, 24_000 * MIL))


def test_tax_benefit_quarter_line_never_claims_the_negative_gap_is_not_a_tax(monkeypatch):
    out, _ws = _run(monkeypatch, {(2026, "11012"): TAX_BENEFIT})
    q2 = next(r for r in out if r["fiscal_quarter"] == "Q2")
    assert q2.get("income_tax_krw") is None and q2["pretax_minus_net_krw"] == -4_000 * MIL           # 값은 그대로, 원인 단정 없음
    md = "\n".join(_render_quarterly({"quarterly": out}))
    line = next(l for l in md.splitlines() if "2026-Q2" in l and "1.4배" in l)
    assert "법인세가 아니다" not in line                                                                # 법인세수익 분기를 반대로 단정하지 않는다
    assert "법인세수익" in line and "중단영업" in line                                                  # 가능성을 둘 다 적는다
    assert "법인세비용" in line                                                                         # 원문 확인 대상을 가리킨다


def test_in_range_quarter_keeps_tax_label_with_basis_stated(monkeypatch):
    """0 ~ 세전 범위 안 값은 기준(세전 − 순이익)을 밝혀 법인세로 싣는다 — 이 한계(중단영업 손익이 섞이면 순수한 법인세비용이 아닐 수 있다)는 문서에 적는다."""
    rows = _rows("2026.06.30", op=(100 * MIL, 100 * MIL), pretax=(300 * MIL, 300 * MIL), ni=(150 * MIL, 150 * MIL))
    out, _ws = _run(monkeypatch, {(2026, "11012"): rows})
    md = "\n".join(_render_quarterly({"quarterly": out}))
    assert out[0]["income_tax_krw"] == 150 * MIL and "(세전 − 순이익)" in md
