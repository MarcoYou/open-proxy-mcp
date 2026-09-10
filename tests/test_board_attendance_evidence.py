import asyncio

from open_proxy_mcp.services.board_attendance import (
    parse_board_attendance_observations,
    summarize_attendance_observations,
)
from open_proxy_mcp.services.guideline_evidence import collect_guideline_evidence, candidate_guideline_inputs, collect_supplemental_filings
from open_proxy_mcp.services.guideline_policy import evaluate_guideline_policy, load_guideline_policy


def test_supplemental_filings_preserve_future_failure_and_read_distinctions():
    class Client:
        async def get_document_cached(self, rc):
            assert rc[:8] <= "20260325"
            if rc == "20260301000001":
                return {"text": "후보 원문"}
            raise RuntimeError("must not expose exceptions")
    result = asyncio.run(collect_supplemental_filings(Client(), [
        "20260301000001", "20260301000002", "20260326000001"], "20260325"))
    assert [r["status"] for r in result] == ["read", "fetch_failed", "after_as_of"]


def document(body):
    return '<DOCUMENT><SECTION-2><TITLE>1. 이사회에 관한 사항</TITLE>' + body + '</SECTION-2></DOCUMENT>'


def test_period_headers_are_preserved_and_committee_rates_excluded():
    xml = document('''<P>나. 주요 의결사항</P>
      <P>기간 : 2025.1.1 ~ 2025.3.26</P>
      <TABLE><TR><TH>이사의 성명(출석률)</TH></TR><TR><TH>홍길동(100%)</TH></TR></TABLE>
      <P>기간 : 2025.3.27~2025.12.31</P>
      <TABLE><TR><TH>이사의 성명(출석률)</TH></TR><TR><TH>홍길동(50%)</TH></TR></TABLE>
      <P>다. 이사회 내 위원회</P><P>홍길동(출석률:33%)</P>''')
    parsed = parse_board_attendance_observations(xml)
    rows = parsed['observations']
    assert [r['attendance_pct'] for r in rows] == [100, 50]
    assert rows[1]['period_end'] == '2025.12.31'
    for row in rows:
        span = row['span']
        assert parsed['section_text'][span['start']:span['end']] == row['quote']
    grouped = summarize_attendance_observations(rows)
    assert grouped[0]['attendance_pct'] is None  # Not the unweighted mean 75%.
    assert grouped[0]['aggregation'] == 'requires_denominators'


def test_committee_only_summary_never_becomes_board_attendance():
    parsed = parse_board_attendance_observations(document('''
      <P>나. 주요 의결사항</P><TABLE><TR><TH>회차</TH><TH>성명</TH></TR>
      <TR><TD>1</TD><TD>홍길동</TD><TD>찬성</TD></TR></TABLE>
      <P>다. 이사회 내 위원회</P><P>(2) 위원회 활동내용- 내부거래위원회</P>
      <P>홍길동(출석률:33%)</P>'''))
    assert parsed['status'] == 'format_unsupported'
    assert not parsed['observations']
    assert '찬성' in parsed['section_text']  # Raw remains available for the reader.


def test_no_board_section_is_not_treated_as_no_disclosure():
    parsed = parse_board_attendance_observations('<DOCUMENT><P>홍길동(출석률:20%)</P></DOCUMENT>')
    assert parsed['status'] == 'section_not_located'
    assert not parsed['observations']


def test_asof_gate_rejects_future_correction_before_fetch():
    class Client:
        async def get_document_cached(self, rc):
            raise AssertionError('Future source must not be fetched')
    result = asyncio.run(collect_guideline_evidence(Client(), {
        'rcept_no': '20260813001726', 'rcept_dt': '20260813'}, '20260325'))
    assert result['status'] == 'after_as_of'
    assert not result['document_read']


def test_real_source_collection_preserves_supported_and_unsupported_formats():
    class Client:
        async def get_document_cached(self, rc):
            return {'html': document('<P>홍길동(출석률:87.5%)</P>')}
    result = asyncio.run(collect_guideline_evidence(Client(), {
        'rcept_no': '20260318001422', 'rcept_dt': '20260318'}, '20260325'))
    assert result['document_read']
    assert result['observations'][0]['attendance_pct'] == 87.5
    inputs = candidate_guideline_inputs({'name': '홍길동'}, result, '20260225005779', '20260325')
    assert inputs['metrics']['is_reelection'] is None
    assert inputs['metrics']['attendance_pct'] is None  # Period and identity not accepted.
    assert inputs['evidence_status']['attendance_pct']['status'] == 'period_not_resolved'
    other = candidate_guideline_inputs({'name': '김철수'}, result, '20260225005779', '20260325')
    assert other['evidence_status']['attendance_pct']['status'] == 'candidate_not_linked'


def test_false_trigger_does_not_require_an_exception_assessment():
    trace = evaluate_guideline_policy(load_guideline_policy(), {
        'is_reelection': True, 'attendance_pct': 100,
        'attendance_exception_accepted': None,
        'independence_concern_accepted': False, 'coverage_complete': True})
    assert trace['unresolved'] == []
    assert 'missing' not in trace['rule_results'][0]


def test_kind_supplement_uses_publication_path_shared_throttle_and_encoding():
    import httpx
    from open_proxy_mcp.services.guideline_evidence import collect_supplemental_sources
    url = 'https://kind.krx.co.kr/external/2026/07/21/000927/20260706001079/91471.htm'
    class Client:
        def __init__(self):
            self._http = self
            self.throttled = 0
        async def _throttle_kind(self):
            self.throttled += 1
        async def get(self, requested, **kwargs):
            assert requested == url and kwargs['follow_redirects'] is False
            return httpx.Response(200, request=httpx.Request('GET', url), content=(
                '<meta charset="euc-kr"><p>심혜섭 신규선임</p><script>지시문</script>').encode('euc-kr'))
    client = Client()
    async def run():
        future = await collect_supplemental_sources(client, [{'type': 'kind', 'url': url}], '20260720')
        assert future[0]['status'] == 'after_as_of' and client.throttled == 0
        rows = await collect_supplemental_sources(client, [{'type': 'kind', 'url': url}], '20260908')
        assert rows[0]['text'] == '심혜섭 신규선임'
        assert rows[0]['source_id'] == 'kind:20260706001079:91471'
        assert client.throttled == 1
    asyncio.run(run())


def test_supplemental_sources_reject_arbitrary_hosts_redirects_and_bad_schemas():
    import httpx
    import pytest
    from open_proxy_mcp.services.guideline_evidence import collect_supplemental_sources
    url = 'https://kind.krx.co.kr/external/2026/07/21/000927/20260706001079/91471.htm'
    for source in [None, {'type': 'kind', 'url': url + '?redirect=x'},
                   {'type': 'kind', 'url': url.replace('kind.krx.co.kr', 'example.com')},
                   {'type': 'kind', 'url': url.replace('/07/21/', '/02/30/')},
                   {'type': 'dart', 'rcept_no': 'invalid'},
                   {'type': 'dart', 'rcept_no': '20260101000001', 'text': 'untrusted'}]:
        with pytest.raises(ValueError):
            asyncio.run(collect_supplemental_sources(None, [source], '20260908'))
    class Client:
        async def _throttle_kind(self):
            pass
        async def get(self, *args, **kwargs):
            return httpx.Response(302, headers={'location': 'https://example.com'},
                                  request=httpx.Request('GET', url))
    client = Client()
    client._http = client
    rows = asyncio.run(collect_supplemental_sources(client, [{'type': 'kind', 'url': url}], '20260908'))
    assert rows[0]['status'] == 'fetch_failed'
