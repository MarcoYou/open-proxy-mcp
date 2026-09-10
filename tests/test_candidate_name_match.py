"""Candidate identity regression from public DART document responses; network 0.

An agenda must not inherit another person's policy assessment because a spaced
Korean name was reduced to one surname character. Expected people and birthdays
below come from the original notice, not stored intermediate parser results.
"""
from __future__ import annotations

from copy import deepcopy
import gzip
from pathlib import Path

import pytest

from open_proxy_mcp.services.proxy_advise import _match_candidate_for_agenda as _select
from open_proxy_mcp.services.shareholder_meeting_parser import parse_agenda_xml, parse_personnel_xml


@pytest.mark.parametrize("name,title", [
    ("김도형", "사외이사 김 도 형 선임의 건"),
    ("김호", "사외이사 김 호 선임의 건"),
    ("이 영 렬", "이사 선임의 건 (사외이사 이영렬)"),
    ("도진명 (Jim Myong Doh)", "사외이사 도진명 선임의 건"),
    ("홍 길 동 James Hong", "홍길동 사외이사 선임의 건"),
    ("Benjamin Tan", "사외이사 Benjamin Tan 선임의 건"),
    ("이한수", "감사 (이한수) 재선임의 건"),
    ("이훈복", "(사외)이사 후보자 이훈복 선임의 건"),
    ("김경수", "사외이사 1인 선임의 건 (김경수)"),
    ("강일규", "강일규 (사외이사)"),
])
def test_complete_names_preserve_spacing_and_english_aliases(name, title):
    candidate = {"name": name}
    assert _select(title, [candidate]) is candidate


@pytest.mark.parametrize("name,title", [
    ("김도형", "사외이사 박도형 선임의 건"),
    ("이 영 렬", "감사위원회 위원이 되는 사외이사 선임의 건 (왕상한)"),
    ("이", "사외이사 왕상한 선임의 건"),
    ("이 (Lee)", "사외이사 왕상한 선임의 건"),
    ("이 영 렬", "이사 선임의 건"),
    ("왕 상 한", "감사위원회 위원이 되는 사외이사 선임의 건"),
])
def test_surname_or_generic_agenda_never_identifies_another_person(name, title):
    assert _select(title, [{"name": name}]) is None


@pytest.mark.parametrize("reverse", [False, True])
def test_full_name_match_precedes_other_candidates_alias(reverse):
    # The abbreviated Korean alias alone cannot outrank the complete named person.
    alias = {"name": "김민수 (Min Su Kim)", "birth_date": "1960-01-01"}
    complete = {"name": "김민수", "birth_date": "1970-01-01"}
    candidates = [alias, complete]
    if reverse:
        candidates.reverse()
    assert _select("사외이사 김민수 선임의 건", candidates) is complete


@pytest.mark.parametrize("candidates,title", [
    ([{"name": "이 영 렬"}, {"name": "왕 상 한"}], "사외이사 이영렬 및 왕상한 선임의 건"),
    ([{"name": "김 도 형", "birth_date": "1960-01-01"},
      {"name": "김도형", "birth_date": "1970-01-01"}], "사외이사 김도형 선임의 건"),
])
def test_multiple_matching_people_are_not_resolved_by_input_order(candidates, title):
    assert _select(title, candidates) is None
    assert _select(title, list(reversed(candidates))) is None


@pytest.fixture
def youngone_notice_document():
    """Read the actual get_document_cached response without modifying raw data."""
    from open_proxy_mcp.dart.client import _DISK_CACHE_DIR
    import json

    root = Path(_DISK_CACHE_DIR)
    for suffix in (".json.gz", ".json"):
        path = root / ("20260312001098" + suffix)
        if path.is_file():
            with (gzip.open(path, "rt", encoding="utf-8") if suffix.endswith(".gz") else path.open()) as stream:
                return json.load(stream)
    pytest.skip("Public DART document-response cache 20260312001098 is unavailable; no network fallback")


@pytest.mark.parametrize("reverse", [False, True])
def test_youngone_agendas_bind_the_actual_person_and_birthdate_from_raw_notice(youngone_notice_document, reverse):
    document = youngone_notice_document
    appointments = parse_personnel_xml(document["html"])["appointments"]
    candidates = {}
    for appointment in appointments:
        for raw in appointment.get("candidates", []):
            key = (raw.get("name"), raw.get("birthDate"))
            candidates.setdefault(key, {
                "name": raw.get("name"), "birth_date": raw.get("birthDate"),
                "role_type": raw.get("roleType"), "agenda_title": appointment.get("title"),
            })
    values = list(candidates.values())
    before = deepcopy(values)
    if reverse:
        values.reverse()
    agendas = parse_agenda_xml(document["text"], html=document["html"])
    expectations = {"이영렬": ("이 영 렬", "1958.03.22"), "왕상한": ("왕 상 한", "1963.07.25")}
    checked = set()
    for agenda in agendas:
        title = agenda.get("title", "")
        for person, identity in expectations.items():
            if person in title and "선임" in title:
                matched = _select(title, values)
                assert matched is not None, title
                assert (matched["name"], matched["birth_date"]) == identity, title
                assert matched["role_type"] == "사외이사"
                checked.add(person)
    assert checked == set(expectations)
    # Selection must not rewrite public names or borrow a different DOB/role.
    assert list(candidates.values()) == before
