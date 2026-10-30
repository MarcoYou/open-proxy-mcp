"""저장된 DART 입력 경계: 식별자·무결성·부분 목록·보완 호출 검사."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import pytest
from open_proxy_mcp.services import treasury_store as store, treasury_share as ts, screener

NOW = datetime.now(timezone.utc)
CORP = '00188089'
R1, R2 = '20260918000068', '20261023000068'


def raw(receipt=R1):
    return dict(corp_code=CORP, corp_name='한섬', rcept_no=receipt, aqpln_stk_ostk='629327',
                aqpln_stk_estk='0', aqpln_prc_ostk='10000006030', aqpln_prc_estk='0')


def item(receipt=R1):
    return dict(corp_code=CORP, rcept_no=receipt, rcept_dt=receipt[:8], report_nm='주요사항보고서(자기주식취득결정)')


def stored(receipt=R1):
    return dict(item(receipt), detail_status='parsed', detail={'source_api': store.make_source('acquisition', raw(receipt))})


@pytest.mark.parametrize('change', ['company', 'receipt', 'date', 'title', 'partial', 'legacy', 'sha', 'version', 'future', 'stale', 'endpoint', 'missing_field', 'duplicate'])
def test_invalid_or_old_details_are_not_reused(change):
    row = stored()
    src = row['detail']['source_api']
    if change == 'company': row['corp_code'] = '99999999'
    if change == 'receipt': src['raw']['rcept_no'] = R2
    if change == 'date': row['rcept_dt'] = '20260101'
    if change == 'title': row['report_nm'] = '다른 공시'
    if change == 'partial': row['detail_status'] = 'partial'
    if change == 'legacy': row['detail'] = {'shares': 629327, 'amount_won': 10000006030}
    if change == 'sha': src['raw_sha256'] = '0' * 64
    if change == 'version': src['version'] = 0
    if change == 'future': src['captured_at'] = (NOW + timedelta(days=1)).isoformat()
    if change == 'stale': src['captured_at'] = (NOW - timedelta(days=8)).isoformat()
    if change == 'endpoint': src['endpoint'] = 'tsstkDpDecsn'
    if change == 'missing_field':
        del src['raw']['aqpln_stk_ostk']; src['raw_sha256'] = store.digest(src['raw'])
    rows = [row, copy.deepcopy(row)] if change == 'duplicate' else [row]
    accepted, rejected = store.reusable(rows, CORP, [item()])
    assert not accepted and rejected


def test_source_input_only_public_fields_no_key_or_zero_loss():
    before = raw(); before['api_key'] = 'synthetic-secret'; before['aqpln_stk_estk'] = None
    src = store.make_source('acquisition', before)
    assert 'api_key' not in src['raw']
    assert src['raw']['aqpln_stk_estk'] is None
    assert src['raw']['aqpln_prc_estk'] == '0'
    assert before['api_key'] == 'synthetic-secret'


class Client:
    def __init__(self): self.calls = []
    async def get_treasury_acquisition(self, corp, start, end):
        self.calls.append(('acquisition', start, end))
        return {'list': [raw(r) for r in (R1, R2) if start <= r[:8] <= end]}
    async def empty(self, *args): return {'list': []}
    get_treasury_disposal = empty
    get_treasury_trust_contract = empty
    get_treasury_trust_termination = empty


@pytest.mark.parametrize('mode', ['reused', 'missing', 'legacy', 'truncated', 'db_failure'])
def test_only_missing_api_range_is_fetched(monkeypatch, mode):
    client = Client(); rows = [stored()]
    items = [item()] if mode == 'reused' else [item(), item(R2)]
    if mode == 'legacy': rows[0]['detail'] = {'shares': 629327}
    async def titles(**kwargs): return items, ['partial list'] if mode == 'truncated' else [], None
    def read(*args):
        if mode == 'db_failure': raise RuntimeError('do not print database credentials')
        return rows, 'available'
    monkeypatch.setattr(ts, 'get_dart_client', lambda: client)
    monkeypatch.setattr(ts, 'fetch_filings_for_title_scan', titles)
    monkeypatch.setattr(store, 'read_rows', read)
    stats = {}
    bundles, warnings, failed, _ = asyncio.run(ts._fetch_decisions(CORP, '20260101', '20261030', source_reuse=stats))
    assert len(bundles['acquisition']) == len(items)
    if mode == 'reused':
        assert client.calls == [] and stats['reused_receipts'] == [R1]
    elif mode == 'missing':
        assert client.calls == [('acquisition', R2[:8], R2[:8])]
        assert stats['reused_receipts'] == [R1]
    elif mode == 'truncated':
        assert client.calls == [('acquisition', '20260101', '20261030')]
        assert not stats['reused_receipts'] and failed and warnings
    else:
        assert client.calls == [('acquisition', R1[:8], R2[:8])]
        assert not stats['reused_receipts']


def test_detail_never_substitutes_first_event_and_preserves_source():
    event = ts._normalize_acquisition(raw())
    event['source_api'] = store.make_source('acquisition', raw())
    event['phase'] = 'decision'
    payload = {'status': 'exact', 'data': {'events': [event]}}
    assert screener._extract_treasury(payload, R2) == {}
    out = screener._extract_treasury(payload, R1)
    assert out['matched_rcept_no'] == R1 and out['source_api']['raw'] == raw()
    payload['data']['events'] = [dict(event, event='cancelation_decision', amount_krw=42)]
    assert screener._extract_treasury(payload, R1)['amount_won'] == 42
    assert screener._stage_tag('treasury', '주식 소각 결정', '소각') == '결정(예정)'
    assert screener._stage_tag('treasury', '자기주식소각', '소각') == '소각(단계 미확인)'


def test_batch_upgrade_serialization_and_next_mcp_read(monkeypatch):
    import json
    import httpx
    from starlette.testclient import TestClient
    from test_disclosure_ledger import led
    from test_treasury_cancelation_correction import RawClient
    from open_proxy_mcp.services import company, filing_search
    from open_proxy_mcp.server import build_app

    db_rows = [dict(item(), kind='treasury', detail_status='parsed', detail={'shares': 629327})]
    class Connection:
        def execute(self, sql, args):
            assert args == ([R1],)
            return self
        def fetchall(self): return [(x,) for x in db_rows]
    con = Connection()
    assert led.already_detailed(con, [R1]) == set()

    class Api(RawClient):
        acquisitions = 0
        async def search_filings(self, **kwargs):
            rows = [item()] if kwargs['pblntf_detail_ty'] == 'B001' else []
            return {'status': '000', 'list': rows, 'total_count': len(rows)}
        async def get_treasury_acquisition(self, *args):
            self.acquisitions += 1
            return {'list': [raw()]}
    api = Api()
    async def reject_network(*args, **kwargs): raise AssertionError('외부 호출 금지')
    monkeypatch.setattr(httpx.AsyncClient, 'send', reject_network)
    for mod in (ts, company, filing_search, screener):
        monkeypatch.setattr(mod, 'get_dart_client', lambda: api)
    monkeypatch.setattr(store, 'read_rows', lambda *args: (db_rows, 'available'))
    hit = dict(item(), _detail_kind='treasury', stock_code='020000', filed_at='2026-09-18')
    detail = asyncio.run(screener._fetch_detail(hit, {'calls': 0}))
    assert detail['detail_status'] == 'parsed', detail
    assert api.acquisitions == 1
    serialized = led.db_row(dict(db_rows[0], detail=detail['fields']))
    db_rows[0]['detail'] = json.loads(serialized['detail'])
    assert led.already_detailed(con, [R1]) == {R1}

    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'Host': 'open-proxy-mcp.fly.dev'}
    with TestClient(build_app()) as client:
        for fmt in ('json', 'md'):
            response = client.post('/mcp?opendart=fixture-key', headers=headers, json={
                'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                    'name': 'treasury_share', 'arguments': {'company': '020000', 'scope': 'summary',
                    'start_date': '20260901', 'end_date': '20261030', 'format': fmt}}})
            result = response.json()['result']
            assert not result.get('isError'), result
            text = '\n'.join(c['text'] for c in result['content'] if c['type'] == 'text')
            if fmt == 'json':
                payload = json.loads(text)
                assert payload['data']['source_reuse']['reused_receipts'] == [R1]
                assert payload['data']['events'][0]['shares'] == 629327
            else:
                assert '629,327' in text
    assert api.acquisitions == 1  # 다음 두 MCP 조회는 취득 API를 다시 부르지 않는다.
    db_rows[0]['detail']['source_api']['captured_at'] = (NOW - timedelta(days=8)).isoformat()
    assert led.already_detailed(con, [R1]) == set()
    db_rows[0]['kind'] = 'other'
    assert led.already_detailed(con, [R1]) == {R1}


def test_execution_detail_keeps_actual_zero_separate_from_planned():
    out = screener._extract_treasury({'data': {'events': [dict(rcept_no=R1, phase='execution',
        actual_shares=0, actual_amount_krw=0, shares=100, amount_krw=1000)]}}, R1)
    assert out['shares'] == out['amount_won'] == 0


def test_overlapping_fresh_row_never_keeps_an_old_source_packet(monkeypatch):
    client = Client()
    async def acquisition(*args):
        invalid = raw(); del invalid['aqpln_stk_estk']
        return {'list': [invalid, raw(R2)]}
    async def titles(**kwargs): return [item(), item(R2)], [], None
    client.get_treasury_acquisition = acquisition
    monkeypatch.setattr(ts, 'get_dart_client', lambda: client)
    monkeypatch.setattr(ts, 'fetch_filings_for_title_scan', titles)
    monkeypatch.setattr(store, 'read_rows', lambda *args: ([stored()], 'available'))
    stats = {}
    bundles, *_ = asyncio.run(ts._fetch_decisions(CORP, '20260101', '20261030', source_reuse=stats))
    first = next(r for r in bundles['acquisition'] if r['rcept_no'] == R1)
    assert not first.get('source_api')
    assert not stats['reused_receipts']
