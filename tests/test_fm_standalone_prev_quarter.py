"""당기(standalone, 3개월) 지표의 전년 값은 전년 같은 분기 3개월이어야 한다 — DATA-Q1 (기간 맞춤).

반기(11012)·3분기(11014) 보고서의 당기 3개월 값을 전년 **누적**(상반기 6개월·3분기 9개월) 전기 값과 견주면
전년비가 기간이 다른 두 값 사이의 숫자가 된다. 079160(CJ CGV) 2021 반기
지배 순이익: 당기 3개월 −845.09억, 전년 같은 분기 3개월 −1,431.69억 → +40.97%.
옛 코드는 전년 누적 −2,386.87억과 견줘 +64.59% 로 적었다.
DART 콜은 늘지 않는다(같은 행을 3개월 열로 다시 읽을 뿐).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import open_proxy_mcp.services.financial_metrics as fm  # noqa: E402

_NO = ([], "no_filing")


def _acnt(year: int, rev: str, op: str, ni: str) -> list[dict]:
    base = {"fs_div": "CFS", "thstrm_dt": f"{year}.06.30", "ord": "1"}
    return [
        {**base, "sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "1000000000000"},
        {**base, "sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "500000000000"},
        {**base, "sj_div": "IS", "account_nm": "매출액", "thstrm_amount": rev},
        {**base, "sj_div": "IS", "account_nm": "영업이익(손실)", "thstrm_amount": op},
        {**base, "sj_div": "IS", "account_nm": "당기순이익(손실)", "thstrm_amount": ni},
    ]


def _acnt_all(year: int, rev: tuple[str, str], tot: tuple[str, str], own: tuple[str, str],
              nci: tuple[str, str]) -> list[dict]:
    """전체 재무제표 손익 — (3개월, 누적) 쌍."""
    base = {"fs_div": "CFS", "sj_div": "CIS", "thstrm_dt": f"{year}.06.30"}

    def r(i, aid, nm, pair):
        return {**base, "ord": str(i), "account_id": aid, "account_nm": nm,
                "thstrm_amount": pair[0], "thstrm_add_amount": pair[1]}

    return [
        r(1, "ifrs-full_Revenue", "영업수익(매출액)", rev),
        r(2, "ifrs-full_ProfitLoss", "당기순이익(손실)", tot),
        r(3, "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 당기순이익(손실)", own),
        r(4, "ifrs-full_ProfitLossAttributableToNoncontrollingInterests", "비지배지분에 귀속되는 당기순이익(손실)", nci),
    ]


def _run(monkeypatch, rc: str = "11012"):
    calls: list[tuple] = []

    async def fake_acnt(corp_code, year, reprt_code, fs_div):
        calls.append(("acnt", year, reprt_code))
        if reprt_code != rc:
            return _NO
        if year == 2021:
            return _acnt(2021, "161679293473", "-57251577151", "-103227452618"), None
        if year == 2020:
            return _acnt(2020, "41593592855", "-130534892626", "-174894883839"), None
        return _NO

    async def fake_acnt_all(corp_code, year, reprt_code, fs_div):
        calls.append(("all", year, reprt_code))
        # 반기 보고서의 손익은 (3개월, 누적) — 전년도 같은 모양이다.
        if (year, reprt_code) == (2021, rc):
            return _acnt_all(2021, ("161679293473", "334161363820"), ("-103227452618", "-210022285835"),
                             ("-84509010233", "-176339238316"), ("-18718442385", "-33683047519")), None
        if (year, reprt_code) == (2020, rc):
            return _acnt_all(2020, ("41593592855", "284902076845"), ("-174894883839", "-293446134476"),
                             ("-143168951343", "-238687087714"), ("-31725932496", "-54759046762")), None
        if (year, reprt_code) == (2021, "11013"):  # 직전 보고서(1분기) — CF 차분용
            return _acnt_all(2021, ("172481", "172481"), ("-1", "-1"), ("-1", "-1"), ("0", "0")), None
        return _NO

    async def fake_ref(*a, **k):
        return None

    monkeypatch.setattr(fm, "_safe_fetch_acnt", fake_acnt)
    monkeypatch.setattr(fm, "_safe_fetch_acnt_all", fake_acnt_all)
    monkeypatch.setattr(fm, "_periodic_filing_ref", fake_ref)
    monkeypatch.setattr(fm, "_accrual_payout_pct", fake_ref)
    metrics, warnings, _ = asyncio.run(fm._fetch_year_metrics("00000000", 2021, "CFS", induty_code="52999"))
    return metrics, warnings, calls


def test_standalone_ni_yoy_uses_same_quarter_3m_prev(monkeypatch):
    metrics, _w, _calls = _run(monkeypatch)
    std = metrics["standalone"]
    assert std is not None
    # 같은 3개월끼리: (−845.09 − (−1431.69)) / 1431.69 = +40.97% (옛 값 +64.59% = 전년 상반기 누적 −2386.87 과 비교)
    assert std["net_income_yoy_pct"] == 40.97
    # 누적(primary) 쪽은 누적끼리 — (−1763.39 − (−2386.87)) / 2386.87 = +26.12%
    assert metrics["net_income_yoy_pct"] == 26.12


def test_standalone_prev_fix_adds_no_dart_calls(monkeypatch):
    _m, _w, calls = _run(monkeypatch)
    # 같은 행을 3개월 열로 다시 읽을 뿐 — 호출 수는 고치기 전과 같다(중복 없음).
    assert len(calls) == len(set(calls)), calls
