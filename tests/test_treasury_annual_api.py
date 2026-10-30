"""저장한 DART 원응답과 사업보고서 독립 판독값의 일치 검사."""
import copy
import json
from pathlib import Path

import pytest

from open_proxy_mcp.services import ownership_structure as own

RAW = json.loads((Path(__file__).parent / 'treasury_inputs/celltrion_2025_treasury_api.json').read_text())


def snapshot(raw=None):
    raw = RAW if raw is None else raw
    return own._treasury_snapshot({'list': raw['stockTotqySttus']},
                                 {'list': raw['tesstkAcqsDspsSttus']}, bsns_year='2025')


def test_celltrion_matches_business_report_and_keeps_row_levels():
    before = copy.deepcopy(RAW)
    out = snapshot()
    total = next(r for r in out['rows'] if r['row_kind'] == 'total' and r['stock_class'] == '보통주')
    assert [total[k] for k in ('begin_shares', 'acquired_shares', 'disposed_shares', 'retired_shares', 'end_shares')] == [12047681, 5284826, 13712, 4970950, 12347845]
    assert 12047681 + 5284826 - 13712 - 4970950 == total['end_shares']
    assert total['rcept_no'] == '20260316001415'
    assert total['stlm_dt'] == '2025-12-31'
    assert len(out['rows']) == len(RAW['tesstkAcqsDspsSttus'])
    assert {r['row_kind'] for r in out['rows']} == {'detail', 'subtotal', 'total'}
    assert out['issued_shares'] == 230960969
    assert out['treasury_shares'] == 12347845
    assert out['tradable_shares'] == 218613124
    assert out['treasury_pct'] == 5.35
    # 자사주 표의 보통주 표기만으로 발행주식 전체의 종류를 확정하지 않는다.
    assert out['basis']['stock_class'] == '합계'
    assert out['basis']['common_only'] is False
    assert RAW == before


@pytest.mark.parametrize('value', [None, '', '-', 'unknown', '1.5', '1,23'])
def test_missing_or_invalid_movement_is_not_zero(value):
    raw = copy.deepcopy(RAW)
    raw['tesstkAcqsDspsSttus'][0]['change_qy_acqs'] = value
    raw['tesstkAcqsDspsSttus'][0]['acqs_qy'] = '999'
    assert snapshot(raw)['rows'][0]['acquired_shares'] is None


def test_official_field_wins_and_explicit_zero_survives():
    raw = copy.deepcopy(RAW)
    raw['tesstkAcqsDspsSttus'][0].update(change_qy_acqs='0', acqs_qy='999')
    assert snapshot(raw)['rows'][0]['acquired_shares'] == 0


def test_real_mcp_annual_json_and_markdown(monkeypatch):
    import httpx
    from starlette.testclient import TestClient
    from open_proxy_mcp.server import build_app
    from open_proxy_mcp.services import company, filing_search, treasury_share
    from test_treasury_cancelation_correction import RawClient

    class AnnualClient(RawClient):
        async def lookup_corp_code_all(self, query):
            return [{'corp_code': '00413046', 'corp_name': '셀트리온', 'stock_code': '068270'}]

        async def search_filings(self, **kwargs):
            return {'list': [], 'status': '013', 'total_count': 0}

        async def get_stock_total(self, *args):
            return {'list': copy.deepcopy(RAW['stockTotqySttus'])}

        async def get_treasury_stock(self, *args):
            return {'list': copy.deepcopy(RAW['tesstkAcqsDspsSttus'])}

        async def get_major_shareholders(self, *args):
            return {'list': [{'nm': '시험 주주', 'relate': '본인', 'stock_knd': '보통주', 'trmend_posesn_stock_co': '1', 'trmend_posesn_stock_qota_rt': '0'}]}

        async def get_block_holders(self, *args):
            return {'list': []}

    raw = AnnualClient()
    async def reject_network(*args, **kwargs):
        raise AssertionError('시험 중 외부 호출 금지')
    monkeypatch.setattr(httpx.AsyncClient, 'send', reject_network)
    # 최신 시세는 이 연간 원문 대조의 입력이 아니다. DB 원응답도 빈 값으로 고정한다.
    from open_proxy_mcp import db
    monkeypatch.setattr(db, 'pg_rows', lambda *args, **kwargs: [])
    for module in (own, company, filing_search, treasury_share):
        monkeypatch.setattr(module, 'get_dart_client', lambda: raw)
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'Host': 'open-proxy-mcp.fly.dev'}
    with TestClient(build_app()) as client:
        for fmt in ('json', 'md'):
            response = client.post('/mcp?opendart=fixture-key', headers=headers, json={
                'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                    'name': 'treasury_share', 'arguments': {'company': '068270', 'scope': 'annual', 'year': 2025,
                    'start_date': '20250101', 'end_date': '20251231', 'format': fmt}}})
            result = response.json()['result']
            assert not result.get('isError'), result
            text = '\n'.join(c['text'] for c in result['content'] if c['type'] == 'text')
            if fmt == 'json':
                annual = json.loads(text)['data']['annual']
                assert annual == snapshot()
            else:
                assert '5,284,826' in text and '4,970,950' in text and '13,712' in text
                assert '보통주' in text and '미기재' in text and '중복 합산하지 않는다' in text
