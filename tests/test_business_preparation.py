"""준비 작업 생략은 원문 선택과 금융·부동산 필드의 기존 게이트를 보존한다."""
import asyncio
from types import SimpleNamespace

import pytest

from open_proxy_mcp.dart import client as dart
from open_proxy_mcp.services import biz_fields as bf, business_details as bd, company
from open_proxy_mcp.services.contracts import AnalysisStatus


@pytest.fixture
def run_payload(monkeypatch):
    state = {'industry': '', 'route': 'get_document', 'has_biz': True, 'slice': '다시 찾은 구간'}
    calls = {'slice': [], 'export': [], 'index': [], 'fields': []}
    async def resolve(query):
        return SimpleNamespace(status=AnalysisStatus.EXACT,
            selected={'corp_code':'00000001','corp_name':'검증회사','stock_code':'999999'},candidates=[])
    async def info(code): return {'induty_code':state['industry']}
    async def reports(*args):
        result=[{'rcept_no':'20260302000002','report_nm':'사업보고서 (2025.12)'}]
        if state['route']=='correction':
            result.append({'rcept_no':'20260301000001','report_nm':'사업보고서 (2025.12)'})
        return result
    def section():
        sec={'biz_text':'사업 본문','note_text':'','note_source':'','note_html':'전체 또는 주석 HTML','toc':[]}
        if state['has_biz']: sec['biz_html']='이미 구한 사업 HTML'
        return sec
    async def getdoc(client,receipt):
        if state['route']=='viewer' or (state['route']=='correction' and receipt.endswith('000002')):
            raise dart.DartClientError('014','원문 없음')
        return section()
    async def viewer(*args): return section()
    def boundary(html):
        calls['slice'].append(html)
        return state['slice']
    def export(html):
        calls['export'].append(html)
        return None
    def index(html):
        node=object();calls['index'].append((html,node))
        return node
    def field(name):
        def extract(text,html,region_index,*args):
            calls['fields'].append((name,html,region_index))
            return {'status':'MARKDOWN','extraction_status':'SUCCESS','markdown':'원문 유지'}
        return extract
    monkeypatch.setattr(company,'resolve_company_query',resolve)
    monkeypatch.setattr(dart,'get_dart_client',lambda:SimpleNamespace(get_company_info=info))
    monkeypatch.setattr(bd,'_find_report_candidates',reports)
    monkeypatch.setattr(bd,'_fetch_getdoc',getdoc)
    monkeypatch.setattr(bd,'_fetch_viewer_sec',viewer)
    monkeypatch.setattr(bd,'_biz_html_region',boundary)
    monkeypatch.setattr(bd,'_export_from_biz_table',export)
    monkeypatch.setattr(bf,'build_region_index',index)
    for name in ('financial_ops','financial_soundness','investment_property','key_contracts'):
        monkeypatch.setattr(bf,'extract_'+name,field(name))
    def run(fields,**options):
        state.update(options)
        value=asyncio.run(bd.build_business_details_payload('검증회사',fields=fields))
        assert value['status']=='exact'
        return value,calls
    return run


@pytest.mark.parametrize('route',['get_document','correction'])
def test_get_document_export_reuses_its_own_prepared_region(run_payload,route):
    value,calls=run_payload(['export_domestic'],route=route)
    assert calls['export']==['이미 구한 사업 HTML']
    assert calls['slice']==[]
    if route=='correction': assert value['data']['report']['rcept_no']=='20260301000001'


@pytest.mark.parametrize('route',['get_document','viewer'])
@pytest.mark.parametrize('has_biz',[False,True])
@pytest.mark.parametrize('slice',['다시 찾은 구간',''])
def test_viewer_and_missing_prepared_region_keep_original_selection(run_payload,route,has_biz,slice):
    _,calls=run_payload(['export_domestic'],route=route,has_biz=has_biz,slice=slice)
    reuse=route=='get_document' and has_biz
    expected='이미 구한 사업 HTML' if reuse else slice or ('이미 구한 사업 HTML' if has_biz else '')
    assert calls['export']==[expected]
    assert calls['slice']==([] if reuse else ['전체 또는 주석 HTML'])


@pytest.mark.parametrize('industry,fields,needed',[
    ('',['financial_ops','financial_soundness','investment_property'],[]),
    ('10',['financial_ops','financial_soundness','investment_property'],[]),
    ('64',['financial_ops'],['financial_ops']),
    ('64992',['financial_ops'],['financial_ops']),
    ('65',['financial_ops','financial_soundness','investment_property'],
        ['financial_ops','financial_soundness','investment_property']),
    ('66',['financial_soundness'],['financial_soundness']),
    ('68',['investment_property'],['investment_property']),
    ('64',['investment_property'],[]),
    ('66',['investment_property'],[]),
    ('68',['financial_ops','financial_soundness'],[]),
    ('65',['key_contracts'],[]),
    ('64',['key_contracts','financial_ops'],['financial_ops']),
])
def test_index_is_built_for_actual_consumers_and_shared(run_payload,industry,fields,needed):
    value,calls=run_payload(fields,industry=industry)
    full_requested='key_contracts' in fields
    expected_html=(['전체 또는 주석 HTML'] if full_requested else [])
    if needed: expected_html.append('이미 구한 사업 HTML')
    assert [html for html,node in calls['index']]==expected_html
    actual=[name for name,html,node in calls['fields'] if name!='key_contracts']
    assert actual==needed
    for name,html,node in calls['fields']:
        expected_source='전체 또는 주석 HTML' if name=='key_contracts' else '이미 구한 사업 HTML'
        assert html==expected_source
        assert node is next(index for source,index in calls['index'] if source==expected_source)
        assert value['data'][name]['markdown']=='원문 유지'
