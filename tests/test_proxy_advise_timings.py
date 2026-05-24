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
    "발행주식 액면분할 및 액면분할을 위한 정관 변경의 건",
    "신주발행 시 이사의 충실의무 도입을 위한 정관 변경의 건",
    "집행임원제도 도입을 위한 정관 변경의 건",
    "주주총회 의장 변경을 위한 정관 변경의 건",
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

    assert decision == "REVIEW"
    assert "퇴직금" not in reason


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
