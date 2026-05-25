import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services import proxy_advise as pa
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus


class FakeClient:
    def __init__(self):
        self.calls = 0

    def api_call_snapshot(self):
        return self.calls

    async def _load_corp_codes(self):
        return None

    async def get_document_cached(self, _rcept_no):
        return {"text": "", "html": ""}


def _resolution():
    return CompanyResolution(
        status=AnalysisStatus.EXACT,
        query="LG화학",
        selected={
            "corp_name": "LG화학",
            "stock_code": "051910",
            "corp_code": "00356361",
        },
        candidates=[],
    )


async def _fake_resolve(_query):
    return _resolution()


async def _fake_shareholder_meeting(_company, *, scope, **_kwargs):
    data = {
        "agenda_summary": {"titles": []},
        "notice": {"rcept_no": "20260224004273"},
        "summary": {},
    }
    if scope == "aoi_change":
        data["aoi_change"] = {"amendments": []}
    return {"status": "exact", "data": data, "evidence_refs": []}


async def _fake_payload(*_args, **_kwargs):
    return {"status": "exact", "data": {"summary": {}}, "evidence_refs": []}


async def _fake_financial(*_args, **_kwargs):
    return {
        "status": "exact",
        "data": {"summary": {"total_assets_krw": 1_000_000}},
        "evidence_refs": [],
    }


async def _fake_director_eval(*_args, **_kwargs):
    return {
        "status": "exact",
        "data": {"evaluations": [], "agenda_titles_fallback": []},
        "evidence_refs": [],
    }


def test_proxy_advise_exposes_upstream_stage_timings(monkeypatch):
    pa.clear_proxy_advise_cache()
    monkeypatch.setattr(pa, "get_dart_client", lambda: FakeClient())
    monkeypatch.setattr(pa, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(pa, "_load_vote_style_policy", lambda _style: None)
    monkeypatch.setattr(pa, "build_shareholder_meeting_payload", _fake_shareholder_meeting)
    monkeypatch.setattr(pa, "build_ownership_structure_payload", _fake_payload)
    monkeypatch.setattr(pa, "build_corp_gov_report_payload", _fake_payload)
    monkeypatch.setattr(pa, "build_financial_metrics_payload", _fake_financial)
    monkeypatch.setattr(pa, "build_director_evaluation_payload", _fake_director_eval)

    payload = asyncio.run(
        pa.build_proxy_advise_payload(
            "LG화학",
            year=2026,
            meeting_type="annual",
        )
    )

    timings = payload["data"]["timings_ms"]
    assert timings["total"] >= 0
    assert "resolve_company" in timings
    assert "prewarm_corp_codes" in timings
    assert "upstreams_total" in timings
    assert "upstream.shareholder_meeting.summary" in timings
    assert "upstream.financial_metrics.summary" in timings
    assert "decision_engine" in timings


async def _fake_shareholder_meeting_with_full_agenda(_company, *, scope, **_kwargs):
    titles = [f"{idx}호 일반 안건" for idx in range(1, 11)]
    full_agendas = [{"title": title, "children": []} for title in titles]
    full_agendas.append({
        "title": "2호 정관 일부 변경의 건",
        "children": [
            {"title": "2-8 분리선출 감사위원 확대", "children": []},
        ],
    })
    data = {
        "agenda_summary": {"titles": titles},
        "agendas": full_agendas,
        "notice": {"rcept_no": "20260305001616"},
        "summary": {},
    }
    if scope == "aoi_change":
        data["aoi_change"] = {"amendments": []}
    return {"status": "exact", "data": data, "evidence_refs": []}


async def _fake_large_company_financial(*_args, **_kwargs):
    return {
        "status": "exact",
        "data": {"summary": {"total_assets_krw": 3_000_000_000_000}},
        "evidence_refs": [],
    }


def test_proxy_advise_uses_full_agenda_tree_for_decisions(monkeypatch):
    pa.clear_proxy_advise_cache()
    monkeypatch.setattr(pa, "get_dart_client", lambda: FakeClient())
    monkeypatch.setattr(pa, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(pa, "_load_vote_style_policy", lambda _style: None)
    monkeypatch.setattr(pa, "build_shareholder_meeting_payload", _fake_shareholder_meeting_with_full_agenda)
    monkeypatch.setattr(pa, "build_ownership_structure_payload", _fake_payload)
    monkeypatch.setattr(pa, "build_corp_gov_report_payload", _fake_payload)
    monkeypatch.setattr(pa, "build_financial_metrics_payload", _fake_large_company_financial)
    monkeypatch.setattr(pa, "build_director_evaluation_payload", _fake_director_eval)

    payload = asyncio.run(
        pa.build_proxy_advise_payload(
            "LG화학",
            year=2026,
            meeting_type="annual",
        )
    )

    decisions = payload["data"]["agenda_decisions"]
    audit_split = [
        item for item in decisions
        if item["agenda_title"] == "2-8 분리선출 감사위원 확대"
    ]
    assert audit_split
    assert audit_split[0]["decision"] == "FOR"
    assert "[법령 A1-3]" in audit_split[0]["reason"]


@pytest.mark.parametrize("title", [
    "소수주주에 대한 보호 관련 정관 명문화의 건",
    "오기 정정의 건",
    "이사의 충실의무 도입을 위한 정관 변경의 건",
    "분기배당 도입을 위한 정관 변경의 건",
    "발행주식 액면분할 및 액면분할을 위한 정관 변경의 건",
    "신주발행 시 이사의 충실의무 도입을 위한 정관 변경의 건",
    "집행임원제도 도입을 위한 정관 변경의 건",
    "주주총회 의장 변경을 위한 정관 변경의 건",
    "이사회 소집 절차 변경의 건",
])
def test_specific_articles_subagenda_does_not_inherit_unrelated_retirement_reason(title):
    retirement_payload = {
        "data": {
            "amendments": [
                {
                    "before": "회장, 명예회장",
                    "after": "회장",
                    "reason": "임원퇴직금 지급 규정 개정",
                }
            ]
        }
    }

    decision, reason = pa._decide_articles_amendment(
        title,
        retirement_payload=retirement_payload,
        fin_metrics_payload=None,
    )

    assert decision in {"FOR", "REVIEW"}
    assert "퇴직금" not in reason


def test_law_layer_director_fiduciary_duty_is_for():
    hit = pa._law_layer(
        "이사의 충실의무 도입을 위한 정관 변경의 건",
        parent_title="정관 일부 변경의 건",
        corp_total_asset_won=3_000_000_000_000,
        today_iso="2026-05-25",
    )

    assert hit is not None
    decision, reason, rule_id, law_reference = hit
    assert decision == "FOR"
    assert rule_id == "A1-9"
    assert "충실의무" in reason
    assert "382" in law_reference


def test_law_layer_does_not_auto_for_share_issuance_fiduciary_duty():
    hit = pa._law_layer(
        "신주발행 시 이사의 충실의무 도입을 위한 정관 변경의 건",
        parent_title="정관 일부 변경의 건",
        corp_total_asset_won=3_000_000_000_000,
        today_iso="2026-05-25",
    )

    assert hit is None


def test_law_layer_independent_director_ratio_applies_to_two_trillion_plus():
    hit = pa._law_layer(
        "사외이사 선임비율 3분의 1 이상으로 변경의 건",
        parent_title="정관 일부 변경의 건",
        corp_total_asset_won=3_000_000_000_000,
        today_iso="2026-05-25",
    )

    assert hit is not None
    decision, reason, rule_id, _law_reference = hit
    assert decision == "FOR"
    assert rule_id == "A1-6"
    assert "자산 2조+" in reason
    assert "선제" not in reason


def test_law_layer_independent_director_ratio_skips_below_two_trillion():
    hit = pa._law_layer(
        "사외이사 선임비율 3분의 1 이상으로 변경의 건",
        parent_title="정관 일부 변경의 건",
        corp_total_asset_won=1_999_999_999_999,
        today_iso="2026-05-25",
    )

    assert hit is None


def test_law_layer_cumulative_voting_group_separation_deletion_is_for():
    amendment = {
        "label": "집중투표 규정 정비",
        "before": "집중투표의 방법에 의해 이사를 선임하는 경우 대표이사 사장과 그 외의 이사를 별개의 조로 구분한다.",
        "after": "삭제",
        "reason": "법무부 유권해석 반영",
    }

    hit = pa._law_layer_subagenda_mapped(
        "집중투표 규정 정비",
        amendment,
        parent_title="정관 일부 변경의 건",
        corp_total_asset_won=3_000_000_000_000,
        today_iso="2026-05-25",
    )

    assert hit is not None
    decision, reason, rule_id, _law_reference = hit
    assert decision == "FOR"
    assert rule_id == "A1-10"
    assert "조 분리 삭제" in reason


def test_law_layer_cumulative_voting_group_separation_creation_remains_review():
    amendment = {
        "label": "집중투표 규정 신설",
        "before": "신설",
        "after": "집중투표의 방법에 의해 이사를 선임하는 경우 대표이사 사장과 그 외의 이사를 별개의 조로 구분한다.",
    }

    hit = pa._law_layer_subagenda_mapped(
        "집중투표 규정 신설",
        amendment,
        parent_title="정관 일부 변경의 건",
        corp_total_asset_won=3_000_000_000_000,
        today_iso="2026-05-25",
    )

    assert hit is not None
    decision, _reason, rule_id, _law_reference = hit
    assert decision == "REVIEW"
    assert rule_id == "B1-8"


def test_director_compensation_uses_camelcase_parsed_limits():
    comp_payload = {
        "data": {
            "compensation": {
                "items": [
                    {
                        "target": "이사",
                        "current": {"limitAmount": 7_000_000_000, "totalDirectors": 7},
                        "prior": {"limitAmount": 7_000_000_000, "actualPaidAmount": 3_210_000_000},
                    }
                ],
                "summary": {
                    "currentTotalLimit": 7_000_000_000,
                    "priorTotalLimit": 7_000_000_000,
                    "priorTotalPaid": 3_210_000_000,
                    "priorUtilization": 45.9,
                }
            }
        }
    }
    fin_payload = {
        "data": {
            "summary": {
                "net_income_krw": 515_011_000_000,
                "capital_impairment_status": "normal",
            }
        }
    }

    decision, reason = pa._decide_director_compensation(comp_payload, fin_payload)

    assert decision == "FOR"
    assert "소폭 변경" in reason
    assert "흑자" not in reason


def test_director_compensation_prefers_director_item_over_aggregate_summary():
    comp_payload = {
        "data": {
            "compensation": {
                "items": [
                    {
                        "target": "이사",
                        "current": {"limitAmount": 7_000_000_000, "totalDirectors": 7},
                        "prior": {"limitAmount": 7_000_000_000, "actualPaidAmount": 1_769_000_000},
                    },
                    {
                        "target": "감사",
                        "current": {"limitAmount": 300_000_000, "totalDirectors": 1},
                        "prior": {"limitAmount": 300_000_000, "actualPaidAmount": 60_000_000},
                    },
                ],
                "summary": {
                    "currentTotalLimit": 7_300_000_000,
                    "priorTotalLimit": 7_300_000_000,
                    "priorTotalPaid": 1_829_000_000,
                    "priorUtilization": 25.1,
                },
            }
        }
    }
    fin_payload = {"data": {"summary": {"net_income_krw": 50_283_331_527}}}

    decision, reason = pa._decide_director_compensation(comp_payload, fin_payload)
    facts = pa._extract_facts("director_compensation", "", None, fin_payload, comp_payload)

    assert decision == "REVIEW"
    assert "소진율 25%" in reason
    assert facts["limit_krw"] == 7_000_000_000
    assert facts["prior_limit_krw"] == 7_000_000_000
    assert facts["prior_paid_krw"] == 1_769_000_000


def test_audit_compensation_uses_audit_item_limit_amount():
    comp_payload = {
        "data": {
            "compensation": {
                "items": [
                    {
                        "target": "감사",
                        "current": {"limitAmount": 300_000_000, "totalDirectors": 1},
                        "prior": {"limitAmount": 300_000_000, "actualPaidAmount": 60_000_000},
                    }
                ]
            }
        }
    }
    fin_payload = {"data": {"summary": {"net_income_krw": 50_283_331_527}}}

    decision, reason = pa._decide_audit_compensation(comp_payload, fin_payload)
    facts = pa._extract_facts("audit_compensation", "", None, fin_payload, comp_payload)

    assert decision == "FOR"
    assert "소폭 변경" in reason
    assert facts["audit_total_limit_krw"] == 300_000_000
    assert facts["audit_increase_rate_pct"] == 0.0
    assert facts["audit_prior_paid_krw"] == 60_000_000


def test_director_compensation_unknown_increase_is_review_not_profit_fallback():
    comp_payload = {"data": {"summary": {}}}
    fin_payload = {
        "data": {
            "summary": {
                "net_income_krw": 515_011_000_000,
                "capital_impairment_status": "normal",
            }
        }
    }

    decision, reason = pa._decide_director_compensation(comp_payload, fin_payload)

    assert decision == "REVIEW"
    assert "인상률 미파악" in reason


async def _fake_shareholder_meeting_with_relation_agenda(_company, *, scope, **_kwargs):
    data = {
        "agenda_summary": {"titles": [
            "집중투표에 의한 이사 선임의 건",
            "집중투표에 의하여 선임할 이사의 수 결정의 건",
            "집중투표에 의한 이사 5인 선임의 건",
        ]},
        "agendas": [
            {
                "agenda_id": "3",
                "number": "제3호",
                "title": "집중투표에 의한 이사 선임의 건",
                "agenda_relation_type": "procedural",
                "agenda_relation_reasons": ["procedural_title", "cumulative_voting_title"],
                "proposer_type": "company",
                "children": [
                    {
                        "agenda_id": "3-1",
                        "number": "제3-1호",
                        "title": "집중투표에 의하여 선임할 이사의 수 결정의 건",
                        "agenda_relation_type": "procedural",
                        "agenda_relation_reasons": ["procedural_title", "cumulative_voting_title"],
                        "proposer_type": "company",
                        "children": [],
                    },
                    {
                        "agenda_id": "3-2",
                        "number": "제3-2호",
                        "title": "집중투표에 의한 이사 5인 선임의 건",
                        "agenda_relation_type": "alternative",
                        "agenda_relation_reasons": ["alternative_title", "cumulative_voting_title"],
                        "proposer_type": "company",
                        "children": [],
                    },
                ],
            }
        ],
        "notice": {"rcept_no": "20260305001616"},
        "summary": {},
    }
    if scope == "aoi_change":
        data["aoi_change"] = {"amendments": []}
    return {"status": "exact", "data": data, "evidence_refs": []}


async def _fake_director_eval_with_candidates(*_args, **_kwargs):
    return {
        "status": "exact",
        "data": {
            "evaluations": [
                {
                    "name": "홍길동",
                    "role_type": "사외이사",
                    "independence": {"summary": "independent"},
                    "disqualification": {"summary": "clean"},
                    "faithfulness": {"audit_history_check": {"summary": "clean"}},
                }
            ],
            "agenda_titles_fallback": [],
        },
        "evidence_refs": [],
    }


def test_proxy_advise_relation_metadata_prevents_auto_for_on_procedural_and_alternative(monkeypatch):
    pa.clear_proxy_advise_cache()
    monkeypatch.setattr(pa, "get_dart_client", lambda: FakeClient())
    monkeypatch.setattr(pa, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(pa, "_load_vote_style_policy", lambda _style: None)
    monkeypatch.setattr(pa, "build_shareholder_meeting_payload", _fake_shareholder_meeting_with_relation_agenda)
    monkeypatch.setattr(pa, "build_ownership_structure_payload", _fake_payload)
    monkeypatch.setattr(pa, "build_corp_gov_report_payload", _fake_payload)
    monkeypatch.setattr(pa, "build_financial_metrics_payload", _fake_large_company_financial)
    monkeypatch.setattr(pa, "build_director_evaluation_payload", _fake_director_eval_with_candidates)

    payload = asyncio.run(
        pa.build_proxy_advise_payload(
            "LG화학",
            year=2026,
            meeting_type="annual",
        )
    )

    by_title = {
        item["agenda_title"]: item
        for item in payload["data"]["agenda_decisions"]
    }
    count_decision = by_title["집중투표에 의하여 선임할 이사의 수 결정의 건"]
    slate_decision = by_title["집중투표에 의한 이사 5인 선임의 건"]

    assert count_decision["decision"] == "REVIEW"
    assert count_decision["agenda_relation_type"] == "procedural"
    assert "절차" in count_decision["reason"]

    assert slate_decision["decision"] == "REVIEW"
    assert slate_decision["agenda_relation_type"] == "alternative"
    assert "대안" in slate_decision["reason"]
    assert "16.67%" in slate_decision["reason"]
    assert slate_decision["facts"]["cumulative_voting_threshold"] == {
        "seats_to_elect": 5,
        "guaranteed_election_threshold_pct_of_votes_cast": 16.67,
        "full_attendance_shareholding_threshold_pct": 16.67,
        "actual_shareholding_threshold_formula": "attendance_rate_pct / (seats_to_elect + 1)",
        "basis": "단순 근사: 1/(선임 이사 수+1), 행사 의결권 기준. 전원 출석·전원 행사 시 발행주식 대비 동일.",
    }
