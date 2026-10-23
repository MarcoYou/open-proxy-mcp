"""감사의견 판정은 부분 문자열(`"적정" in op`)이 아니라 정규화한 의견의 정확 일치(op == "적정")다. network 0 · DB 0.

옛 판정은 `"적정" in op` / `"적정" not in op` 라 「부적정」이 「적정」을 포함해 깨끗한 의견으로 셈해졌다:
- `_detect_yoy_signals`: 부적정이면 non_clean_audit_opinion·audit_opinion_change 가 꺼졌다.
- `_build_audit_opinion_data`: 부적정 행만 있어도 summary.all_clean=True.
- `_next_actions`: 부적정 행이면 non-clean 안내가 빠졌다.
또 「해당사항없음」(같은 결산일에 의견 행과 함께 오는 자리 채움 행 — 셀리버리 2022-12-31)이 비적정으로 셈해져
all_clean 이 거짓 False 가 되고 non_clean_audit_opinion 이 거짓으로 켜졌다.
실제 회사의 부적정 원문은 찾지 못해(부적정 의견은 극히 드물다) 합성 고정본이다 — 행 모양은 accnutAdtorNmNdAdtOpinion 응답 그대로.
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import financial_metrics as FM


def _row(stlm_dt: str, opinion: str, tag: str = "(당기)") -> dict:
    return {"bsns_year": f"제1기\n{tag}", "stlm_dt": stlm_dt, "adtor": "시험회계법인", "adt_opinion": opinion,
            "adt_reprt_spcmnt_matter": "-", "emphs_matter": "-", "core_adt_matter": "-", "rcept_no": "20260101000001"}


def _audit_data(monkeypatch: pytest.MonkeyPatch, rows: list[dict]) -> dict:
    async def fake(corp_code: str, year: int):
        return rows, None
    monkeypatch.setattr(FM, "_safe_fetch_audit", fake)
    data, _w, _ev = asyncio.run(FM._build_audit_opinion_data("00000001", 2025))
    return data


def _alerts(cur: str, prev: str | None = None) -> list[str]:
    ac = {"adt_opinion": cur}
    ap = {"adt_opinion": prev} if prev is not None else None
    return [a for a in FM._detect_yoy_signals({}, {}, ac, ap) if "audit" in a]


# ── _detect_yoy_signals ──

def test_signal_adverse_opinion_turns_on_non_clean():
    assert "non_clean_audit_opinion" in _alerts("부적정")


def test_signal_adverse_after_clean_turns_on_change():
    a = _alerts("부적정", "적정")
    assert "non_clean_audit_opinion" in a and "audit_opinion_change" in a


def test_signal_clean_stays_quiet():
    assert _alerts("적정", "적정") == []
    assert _alerts("적정") == []


def test_signal_clean_with_suffix_or_space_is_still_clean():
    assert _alerts("적정의견", "적 정") == []


def test_signal_qualified_and_disclaimer_still_non_clean():
    assert "non_clean_audit_opinion" in _alerts("한정")
    assert "non_clean_audit_opinion" in _alerts("의견거절", "적정")


def test_signal_not_applicable_row_is_not_judged():
    assert _alerts("해당사항없음", "적정") == []
    assert _alerts("적정", "해당사항없음") == []


def test_signal_prior_adverse_to_clean_is_not_a_change():
    # 전기 부적정 → 당기 적정: 의견이 나아진 것이라 non_clean 도 change(악화) 도 아니다
    assert _alerts("적정", "부적정") == []


# ── _build_audit_opinion_data ──

def test_all_clean_false_when_adverse_row(monkeypatch):
    d = _audit_data(monkeypatch, [_row("2025-12-31", "부적정"), _row("2024-12-31", "적정", "(전기)")])
    assert d["summary"]["all_clean"] is False


def test_all_clean_true_for_clean_rows(monkeypatch):
    d = _audit_data(monkeypatch, [_row("2025-12-31", "적정"), _row("2024-12-31", "적정", "(전기)")])
    assert d["summary"]["all_clean"] is True


def test_all_clean_ignores_not_applicable_rows(monkeypatch):
    d = _audit_data(monkeypatch, [_row("2025-12-31", "적정"), _row("2025-12-31", "해당사항없음"),
                                  _row("2024-12-31", "적정", "(전기)")])
    assert d["summary"]["all_clean"] is True
    # 표시용 행은 그대로 싣는다(원문 보존) — 판정에서만 뺀다
    assert any(o["adt_opinion"] == "해당사항없음" for o in d["opinions"])


def test_all_clean_none_when_nothing_to_judge(monkeypatch):
    d = _audit_data(monkeypatch, [_row("2025-12-31", "해당사항없음")])
    assert d["summary"]["all_clean"] is None


# ── _next_actions ──

def test_next_actions_flags_adverse_only():
    def acts(*ops: str) -> list[str]:
        return FM._next_actions("audit_opinion", {"audit_opinion": {"opinions": [{"adt_opinion": o} for o in ops]}})
    assert acts("부적정", "적정")
    assert not acts("적정", "적정")
    assert not acts("적정", "해당사항없음")


# ── yoy 응답 전체(호출부) ──
from open_proxy_mcp.services.company import CompanyResolution  # noqa: E402
from open_proxy_mcp.services.contracts import AnalysisStatus  # noqa: E402
from tests.test_fm_yoy_same_report import CO, _NO, _table  # noqa: E402


def _yoy(monkeypatch: pytest.MonkeyPatch, opinions: list[dict]) -> dict:
    table = _table(with_prior_q3=False, annual_2026=True)
    monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")

    async def resolve(query, *a, **k):
        return CompanyResolution(status=AnalysisStatus.EXACT, query=query, selected=dict(CO), candidates=[])

    async def info(cc):
        return {"acc_mt": "12", "induty_code": "471"}, None

    async def acnt(cc, y, rc, fs):
        return (table[(y, rc)], None) if (y, rc) in table else _NO

    async def acnt_all(cc, y, rc, fs):
        return _NO

    async def audit(*a, **k):
        return {"opinions": opinions, "summary": {}}, [], []

    async def none(*a, **k):
        return None

    monkeypatch.setattr(FM, "resolve_company_query", resolve)
    monkeypatch.setattr(FM, "_safe_company_info", info)
    monkeypatch.setattr(FM, "_safe_fetch_acnt", acnt)
    monkeypatch.setattr(FM, "_safe_fetch_acnt_all", acnt_all)
    monkeypatch.setattr(FM, "_build_audit_opinion_data", audit)
    monkeypatch.setattr(FM, "_periodic_filing_ref", none)
    monkeypatch.setattr(FM, "_accrual_payout_pct", none)
    monkeypatch.setattr(FM, "_FM_CACHE", type(FM._FM_CACHE)(16 * 1024 * 1024, 300.0, "test-fm-audit-exact"))
    return asyncio.run(FM.build_financial_metrics_payload("시험회사", scope="yoy", year=2026))["data"]["yoy"]


def test_yoy_response_adverse_opinion_raises_alerts(monkeypatch):
    y = _yoy(monkeypatch, [{"adt_opinion": "부적정", "stlm_dt": "2026-12-31"}, {"adt_opinion": "적정", "stlm_dt": "2025-12-31"}])
    assert "non_clean_audit_opinion" in y["alerts"] and "audit_opinion_change" in y["alerts"]


def test_yoy_response_not_applicable_row_does_not_hide_the_real_opinion(monkeypatch):
    # 같은 결산일에 「해당사항없음」이 먼저 와도 의견 행(부적정)이 당기로 판정된다. 응답의 audit_opinion.current 는 원문 첫 행 그대로.
    ops = [{"adt_opinion": "해당사항없음", "stlm_dt": "2026-12-31"}, {"adt_opinion": "부적정", "stlm_dt": "2026-12-31"},
           {"adt_opinion": "적정", "stlm_dt": "2025-12-31"}]
    y = _yoy(monkeypatch, ops)
    assert "non_clean_audit_opinion" in y["alerts"]
    assert y["audit_opinion"]["current"]["adt_opinion"] == "해당사항없음"


def test_yoy_response_clean_opinion_has_no_audit_alert(monkeypatch):
    y = _yoy(monkeypatch, [{"adt_opinion": "적정", "stlm_dt": "2026-12-31"}, {"adt_opinion": "적정", "stlm_dt": "2025-12-31"}])
    assert not [a for a in y["alerts"] if "audit" in a]
