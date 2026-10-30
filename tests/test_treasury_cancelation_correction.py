"""한섬 실제 원문과 합성 정정 관계 반례. 네트워크 없이 원문 경계를 재생한다."""
import hashlib
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from open_proxy_mcp.services import treasury_share as ts

RAW = Path(__file__).parent / 'treasury_inputs/handsome_20261023800327.txt'
CORP = '00188089'
CORRECTED = '20261023800327'


def original(receipt='20260918800133', **changes):
    return dict(dict(rcept_no=receipt, rcept_dt='20260918', corp_code=CORP,
                     corp_name='한섬', report_nm='주식소각결정', board_date='2026-09-18',
                     shares=629327, amount_krw=10000006030, body_parsed=True), **changes)


def corrected(**changes):
    return dict(original(CORRECTED, rcept_dt='20261023', report_nm='[기재정정]주식소각결정',
                         shares=487988, amount_krw=10002991080,
                         correction_final_body=True, correction_original_date='2026-09-18'), **changes)


def test_handsome_final_body_matches_independent_raw_reading():
    assert hashlib.sha256(RAW.read_bytes()).hexdigest() == 'cb5f4fa171f862b0877cb21bc0ababc880d3daf501ed9d02876565b36633dc06'
    parsed = ts._parse_cancelation_body(RAW.read_text())
    assert parsed['shares'] == parsed['shares_common'] == 487988
    assert parsed['amount_krw'] == 10002991080
    assert parsed['scheduled_date'] == '2026-11-06'
    assert parsed['board_date'] == parsed['correction_original_date'] == '2026-09-18'


@pytest.mark.parametrize('text', [
    '정정사항 정정전 정정후 1. 소각할 주식의 종류와 수 629,327 487,988 4. 소각예정금액(원) 10,000,006,030 10,002,991,080',
    '정정사항 주식 소각 결정 1. 소각할 주식 수 (주) 1 주식 소각 결정 1. 소각할 주식 수 (주) 2',
])
def test_correction_without_unique_final_body_is_not_a_before_value(text):
    assert ts._parse_cancelation_body(text) == {}


def test_final_body_wins_over_past_values_quoted_in_other_matters():
    parsed = ts._parse_cancelation_body(RAW.read_text() + ' 소각예정금액(원) 99,999,999,999')
    assert parsed['amount_krw'] == 10002991080


def test_changed_values_replace_only_explicit_unique_original_and_keep_receipt():
    before, after = original(), corrected()
    out = ts._dedupe_cancelation_rows([before, after])
    assert len(out) == 1
    assert out[0]['rcept_no'] == CORRECTED
    assert out[0]['superseded_rcept_nos'] == [before['rcept_no']]
    assert 'superseded_rcept_nos' not in after  # 입력 행은 바꾸지 않는다
    totals = ts._summary_counts({'cancelation': out})
    assert totals['cancelation_shares_total'] == 487988
    assert totals['cancelation_amount_total_krw'] == 10002991080


def test_equal_values_on_same_day_are_not_an_event_identity():
    rows = [original(), original('20260918000200')]
    assert len(ts._dedupe_cancelation_rows(rows)) == 2


@pytest.mark.parametrize('change', [
    {'corp_code': ''}, {'corp_code': '99999999'}, {'board_date': '2026-09-17'},
    {'correction_original_date': '2026-02-30'}, {'correction_original_date': '2026-10-24'},
    {'correction_original_date': ''}, {'body_parsed': False},
    {'report_nm': '[첨부정정]주식소각결정'}, {'report_nm': '[기재정정]주식소각결정(철회)'},
])
def test_missing_or_conflicting_parent_evidence_preserves_rows_and_blocks_total(change):
    out = ts._dedupe_cancelation_rows([original(), corrected(**change)])
    assert len(out) == 2
    assert out[0]['correction_relation_status'] == 'unresolved'
    assert ts._summary_counts({'cancelation': out})['cancelation_amount_total_krw'] is None
    assert ts._cancelation_relation_warnings(out)


def test_multiple_originals_and_parallel_corrections_are_not_guessed():
    assert len(ts._dedupe_cancelation_rows([original(), original('20260918000200'), corrected()])) == 3
    rows = [original(), corrected(), corrected(rcept_no='20261024800327', rcept_dt='20261024')]
    assert len(ts._dedupe_cancelation_rows(rows)) == 3


def test_explicit_chain_keeps_all_superseded_receipts_and_missing_link_stays_unresolved():
    last = corrected(rcept_no='20261024800327', rcept_dt='20261024', correction_original_date='2026-10-23')
    out = ts._dedupe_cancelation_rows([original(), corrected(), last])
    assert len(out) == 1
    assert out[0]['superseded_rcept_nos'] == [CORRECTED, original()['rcept_no']]
    assert len(ts._dedupe_cancelation_rows([original(), last])) == 2


def test_noncorrection_body_works_and_does_not_gain_a_correction_link():
    parsed = ts._parse_cancelation_body('주식 소각 결정 1. 소각할 주식 수 (주) 100 2. 소각예정금액(원) 1,000 6. 이사회결의일 2026-09-18')
    assert parsed['shares'] == 100
    assert parsed['amount_krw'] == 1000
    assert 'correction_original_date' not in parsed


class RawClient:
    """고정 목록·명부 + 실제 원공시와 정정 본문. 다른 공시는 이 시험 범위 밖이다."""
    def api_call_snapshot(self):
        return 0

    async def lookup_corp_code_all(self, query):
        return [{'corp_code': CORP, 'corp_name': '한섬', 'stock_code': '020000'}]

    async def search_filings(self, **kwargs):
        rows = []
        if kwargs['pblntf_detail_ty'] == 'I001':
            rows = [original(), corrected()]
            rows = [{k: r[k] for k in ('rcept_no', 'rcept_dt', 'corp_code', 'corp_name', 'report_nm')} for r in rows]
        return {'status': '000', 'list': rows, 'total_count': len(rows)}

    async def get_document_cached(self, receipt):
        if receipt == CORRECTED:
            return {'text': RAW.read_text()}
        assert receipt == original()['rcept_no']
        return {'text': RAW.with_name('handsome_20260918800133.txt').read_text()}

    async def get_treasury_acquisition(self, *args):
        return {'list': []}

    get_treasury_disposal = get_treasury_acquisition
    get_treasury_trust_contract = get_treasury_acquisition
    get_treasury_trust_termination = get_treasury_acquisition


@pytest.mark.parametrize('ambiguous', [False, True])
def test_real_app_mcp_json_and_markdown_use_corrected_body(monkeypatch, ambiguous):
    from open_proxy_mcp.services import company, filing_search
    from open_proxy_mcp.server import build_app
    raw = RawClient()
    if ambiguous:
        async def ambiguous_document(receipt):
            return {'text': '정정사항 정정전 정정후 소각예정금액(원) 100 200'}
        raw.get_document_cached = ambiguous_document
    # 원문/명부 경계 외 네트워크 접근은 시험 실패로 잡는다.
    import httpx
    async def reject_network(*args, **kwargs):
        raise AssertionError('외부 네트워크 호출 금지')
    monkeypatch.setattr(httpx.AsyncClient, 'send', reject_network)
    for module in (ts, company, filing_search):
        monkeypatch.setattr(module, 'get_dart_client', lambda: raw)
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'Host': 'open-proxy-mcp.fly.dev'}
    with TestClient(build_app()) as client:
        for format in ('json', 'md'):
            response = client.post('/mcp?opendart=fixture-key', headers=headers, json={
                'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                'params': {'name': 'treasury_share', 'arguments': {
                    'company': '020000', 'scope': 'summary', 'start_date': '20260901',
                    'end_date': '20261029', 'format': format}}})
            assert response.status_code == 200
            result = response.json()['result']
            assert not result.get('isError'), result
            text = '\n'.join(c['text'] for c in result['content'] if c['type'] == 'text')
            if format == 'json':
                payload = json.loads(text)
                if ambiguous:
                    assert payload['status'] == 'partial', payload
                    assert payload['data']['summary']['cancelation_amount_total_krw'] is None
                    assert any('미확정' in w for w in payload['warnings'])
                    continue
                assert payload['status'] == 'exact', payload
                summary = payload['data']['summary']
                assert summary['cancelation_count'] == 1
                assert summary['cancelation_shares_total'] == 487988
                assert summary['cancelation_amount_total_krw'] == 10002991080
                row = payload['data']['events'][0]
                assert row['phase'] == 'decision'
                assert row['scheduled_date'] == '2026-11-06'
                assert row['superseded_rcept_nos'] == [original()['rcept_no']]
            else:
                if ambiguous:
                    assert '미확정' in text and '487,988' not in text
                    continue
                assert '487,988' in text and '629,327' not in text
                assert '2026-11-06' in text and '실행 완료를 뜻하지 않음' in text
                assert '20260918800133' in text
                assert '소각결정' in text and '[D]' in text


@pytest.mark.parametrize('case', json.loads((RAW.parent / 'correction_expected.json').read_text()), ids=lambda r: r['company'])
def test_five_other_companies_match_independent_correction_reading(case):
    path = RAW.parent / ('correction_' + case['receipt'] + '.txt')
    assert hashlib.sha256(path.read_bytes()).hexdigest() == case['raw_sha256']
    parsed = ts._parse_cancelation_body(path.read_text())
    assert parsed['shares'] == case['shares_total']
    assert parsed['amount_krw'] == case['amount_krw']
    assert parsed.get('scheduled_date') == case['scheduled_date']
    assert parsed['board_date'] == case['board_date']
    assert parsed['correction_original_date'] == case['original_filing_date']
    if case['company'] == '메디톡스':
        assert parsed['share_type'] == '종류주'
        assert parsed['shares_preferred'] == 440635
