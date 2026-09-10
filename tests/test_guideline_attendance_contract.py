"""Arithmetic and source-boundary checks, not a benchmark of LLM judgment."""
from copy import deepcopy
import asyncio
import pytest
from open_proxy_mcp.services.guideline_assessment import GuidelineAssessment, accept_assessment, assessment_metrics, pilot_recommendation
from open_proxy_mcp.services.guideline_policy import load_pilot_guideline_policy, evaluate_guideline_policy
from open_proxy_mcp.services.guideline_evidence import fiscal_attendance_period, discover_officer_filings, officer_filing_kind, collect_supplemental_filings
from test_guideline_assessment import packet, submission


def attendance_case():
    text = '홍길동 재직 2025년 전체. 이사회 제1회 참석, 제2회 불참, 제3회 참석. 전체 세 회의.'
    task = packet(attendance={'filing': {'rcept_no': '20260301000002'}, 'raw_text': text,
                              'attendance_period': {'status':'resolved','start':'2025-01-01','end':'2025-12-31'}})
    data = submission(task)
    data['appointment']['value']='renewed'
    refs=[{'source_id':'annual:20260301000002','quote':text}]
    j={'rationale':'합성 원문으로 계약과 산술만 검사','evidence_refs':refs,'counterevidence':[],'unresolved':[]}
    data['attendance']={**j,'value':'known','period_start':'2025-01-01','period_end':'2025-12-31',
       'all_board_meetings_covered':True,
       'service_intervals':[{'start':'2025-01-01','end':'2025-12-31','rationale':'연간 재직','evidence_refs':refs}],
       'legal_suspension_intervals':[],
       'meetings':[{'meeting_id':str(i),'date':f'2025-0{i}-01','attendance':v,'evidence_refs':refs}
                    for i,v in enumerate(['present','absent','present'],1)],
       'exception':{**j,'value':'rejected'}}
    return task,data


def verdict(task,data):
    accepted=accept_assessment(task,GuidelineAssessment.model_validate(data))
    return accepted,pilot_recommendation(evaluate_guideline_policy(load_pilot_guideline_policy(),assessment_metrics(accepted)))


def test_counted_low_attendance_exception_and_unknown_are_distinct():
    t,d=attendance_case();a,v=verdict(t,d)
    assert v=='AGAINST' and a['attendance_calculation']['eligible_meetings']==3
    assert a['attendance_calculation']['attendance_pct']==pytest.approx(200/3)
    assert a['human_reviewed'] is False
    d['attendance']['exception']['value']='accepted'
    assert verdict(t,d)[1]=='FOR'
    d['attendance']['exception'].update(value='unknown',unresolved=['불참 사유 미확인'])
    assert verdict(t,d)[1]=='REVIEW'
    d['attendance']['meetings'][1]['attendance']='present'
    assert verdict(t,d)[1]=='FOR' # irrelevant exception cannot block a false low-attendance rule


@pytest.mark.parametrize('mutation',['duplicate','reverse','outside','wrong_year','invented_quote','invalid_day'])
def test_invalid_attendance_contract_rejected(mutation):
    t,d=attendance_case();a=d['attendance']
    if mutation=='duplicate':a['meetings'].append(deepcopy(a['meetings'][0]))
    elif mutation=='reverse':a['service_intervals'][0].update(start='2025-12-31',end='2025-01-01')
    elif mutation=='outside':a['meetings'][0]['date']='2024-01-01'
    elif mutation=='wrong_year':a['period_start']='2024-01-01'
    elif mutation=='invalid_day':a['meetings'][0]['date']='2025-02-30'
    else:a['meetings'][0]['evidence_refs']=[{'source_id':'annual:20260301000002','quote':'원문에 존재하지 않는 조작된 참석 문장입니다.'}]
    accepted,v=verdict(t,d)
    assert accepted['status']=='rejected' and v=='REVIEW'


def test_legal_suspension_and_incomplete_meeting_list():
    t,d=attendance_case();a=d['attendance']
    a['legal_suspension_intervals']=[{**a['service_intervals'][0],'start':'2025-02-01','end':'2025-02-28'}]
    accepted,v=verdict(t,d)
    assert v=='FOR' and accepted['attendance_calculation']['eligible_meetings']==2
    a['all_board_meetings_covered']=False
    accepted,v=verdict(t,d)
    assert v=='REVIEW' and accepted['attendance_calculation']['attendance_pct'] is None
    d['independence']['value']='concern'
    assert verdict(t,d)[1]=='AGAINST'


def test_fiscal_period_stale_noncalendar_and_transition():
    def cover(s,e):return f'사업연도 {s} 부터 {e} 까지'
    assert fiscal_attendance_period(cover('2025년 01월 01일','2025년 12월 31일'),'20260325')['status']=='resolved'
    assert fiscal_attendance_period(cover('2024년 01월 01일','2024년 12월 31일'),'20260325')['status']=='unresolved'
    p=fiscal_attendance_period(cover('2024년 04월 01일','2025년 03월 31일'),'20250601')
    assert p['start']=='2024-04-01'
    assert fiscal_attendance_period('사업연도 2025.01.01 부터 2025.12.31 까지','20260907')['status']=='resolved'
    assert fiscal_attendance_period(cover('2025년 01월 01일','2025년 03월 31일'),'20250601')['status']=='unresolved'


def test_officer_discovery_aliases_scope_limits_same_day_and_failure():
    assert officer_filing_kind('[정정] 사외이사의 선임ㆍ해임 또는 중도퇴임에 관한 신고')=='officer_change'
    assert officer_filing_kind('독립이사의선임ㆍ해임또는중도퇴임에관한신고')=='officer_change'
    assert officer_filing_kind('대표이사 변경')=='representative_change'
    assert officer_filing_kind('감사보고서') is None
    class Client:
        async def search_filings(self,**kw):
            assert kw['corp_code']=='00000001' and kw['pblntf_ty'] in ('E','I')
            assert kw['page_no']<=2 and kw['last_reprt_at']=='N'
            return {'list':[{'rcept_no':'20260908000407','rcept_dt':'20260908','report_nm':'독립이사 중도퇴임 신고'},
                            {'rcept_no':'20260907000001','rcept_dt':'20260907','report_nm':'대표이사변경'}],
                    'total_page':3,'total_count':201}
        async def get_document_cached(self,rc):
            assert rc=='20260907000001'
            return {'text':'홍길동 대표이사 변경 공시'}
    r=asyncio.run(discover_officer_filings(Client(),'00000001','20260908'))
    assert r['status']=='partial' and r['selected_count']==1
    assert r['complete_history'] is False
    assert any(x['same_day_unverified'] for x in r['matches'])
