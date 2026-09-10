"""Adapter-neutral source reading and discovery contracts."""
import asyncio
from copy import deepcopy
import pytest
from open_proxy_mcp.services.guideline_research import ResearchQuery
from open_proxy_mcp.services.guideline_evidence import build_source_packet, source_read_window_key
from open_proxy_mcp.harness.runner import SourceRead, _source_id

REQUEST = {'type': 'court_precedent', 'board': 'precedent_bulletin', 'seqnum': '11003'}


def test_court_read_action_uses_fixed_identity_and_distinct_windows():
    read = SourceRead.model_validate(REQUEST).request()
    assert _source_id(read) == 'court:precedent_bulletin:11003'
    assert source_read_window_key(read) != source_read_window_key({**read, 'seqnum': '11004'})


@pytest.mark.parametrize('extra', [{'url': 'https://evil.example'}, {'rcept_no': '20260301000001'},
                                  {'dcm_no': '123'}, {'published': '20250101'}])
def test_court_read_cannot_supply_url_date_or_filing_identity(extra):
    with pytest.raises(ValueError):
        SourceRead.model_validate({**REQUEST, **extra}).request()


def test_court_summary_stays_summary_in_citation_packet_and_next_read():
    item = {**REQUEST, 'source_id': 'court:precedent_bulletin:11003',
            'source_url': 'https://sc.scourt.go.kr/portal/news/NewsViewAction.work?gubun=4&seqnum=11003',
            'status': 'read', 'published': '20260409', 'text': '공식 판례속보 요약을 읽었습니다.',
            'source_kind': 'court_official_summary', 'attachments': [{'status': 'unread'}],
            'publication_basis': 'official_posted_date'}
    packet = build_source_packet(item)
    assert packet['publisher_type'] == 'court'
    assert packet['source_kind'] == 'court_official_summary'
    assert packet['attachments'][0]['status'] == 'unread'
    assert packet['read_next']['source_request']['seqnum'] == '11003'
    assert packet['read_next']['source_request']['type'] == 'court_precedent'


def test_legal_query_requires_bounded_search_text_and_cannot_change_filing_filters():
    assert ResearchQuery.model_validate({'kind':'legal_precedents','search_text':'정관 임기'}).kind=='legal_precedents'
    for value in [{'kind':'legal_precedents'}, {'kind':'charter_history','search_text':'정관'},
                  {'kind':'legal_precedents','search_text':' '},
                  {'kind':'legal_precedents','search_text':'정관','start_date':'20250101'}]:
        with pytest.raises(ValueError):ResearchQuery.model_validate(value)


def test_court_publication_metadata_change_invalidates_evidence_identity():
    item={**REQUEST,'source_id':'court:precedent_bulletin:11003','source_url':'https://sc.scourt.go.kr/',
          'status':'read','text':'공식 요약','published':'20260409','source_kind':'court_official_summary'}
    first=build_source_packet(item)
    assert first['document_sha256'] != build_source_packet({**item,'published':'20260410'})['document_sha256']


def test_official_read_crosses_evidence_boundary_and_never_admits_future_body():
    from test_precedent_documents import Client
    from open_proxy_mcp.services.guideline_evidence import collect_supplemental_sources
    async def exercise():
        client=Client()
        first=await collect_supplemental_sources(client,[REQUEST],'20260408')
        assert first[0]['status']=='after_as_of' and build_source_packet(first[0]) is None
        second=await collect_supplemental_sources(client,[REQUEST],'20260410')
        assert second[0]['status']=='read'
        packet=build_source_packet(second[0])
        assert packet['source_kind']=='court_official_summary'
        assert packet['read_next']['source_request']['board']=='precedent_bulletin'
    asyncio.run(exercise())
