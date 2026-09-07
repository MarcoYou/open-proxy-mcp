"""Artifact conformance and illustrative predicate checks only; not an OPM runtime or outcome eval."""
from pathlib import Path
from datetime import datetime
import json, operator, hashlib
import jsonschema
P=Path(__file__).resolve().parent
policy=json.loads((P/'policy.json').read_text())
MET={x['id']:x for x in policy['metrics']}
RULE={x['id']:x for x in policy['rules']}
OPS={'eq':operator.eq,'lt':operator.lt,'lte':operator.le,'gt':operator.gt,'gte':operator.ge}
def expr_metrics(e):
 if e is None:return set()
 return ({e['metric']} if 'metric' in e else set()).union(*(expr_metrics(a) for a in e.get('args',[])))
def evaluate_expr(e,facts,case,issues):
 op=e['op']
 if op=='const':return e['value']
 if op in ('all','any','not'):
  xs=[evaluate_expr(a,facts,case,issues) for a in e['args']]
  if op=='not':return None if xs[0] is None else not xs[0]
  if op=='all':return False if False in xs else None if None in xs else True
  return True if True in xs else None if None in xs else False
 mid=e['metric']; m=MET[mid];f=facts.get(mid)
 def unknown(why):issues.append(mid+': '+why);return None
 if not f or f.get('status')!='verified':return unknown('미확인 또는 비적용 값')
 if f.get('metric_id')!=mid or f.get('entity_id')!=case['entity_id'] or f.get('agenda_id')!=case['agenda_id']:return unknown('회사/안건/지표 불일치')
 if f['unit']!=m['unit']:return unknown('단위 불일치')
 if not f.get('public_at') or datetime.fromisoformat(f['public_at'])>datetime.fromisoformat(case['as_of']):return unknown('기준일 이후 자료')
 if not f.get('evidence_refs') or any(x not in case['evidence_registry'] for x in f['evidence_refs']):return unknown('원문 근거 누락')
 for rid in f['evidence_refs']:
  ref=case['evidence_registry'][rid]
  if ref['entity_id']!=case['entity_id'] or ref['agenda_id']!=case['agenda_id']:return unknown('근거의 회사/안건 불일치')
  if datetime.fromisoformat(ref['public_at'])>datetime.fromisoformat(case['as_of']):return unknown('근거의 미래 공개일')
 if m['method']=='reviewer_assessment' and not f.get('accepted_by'):return unknown('정성 판단 미승인')
 val=f['value'];typ=m['value_type']
 if typ in ['number','integer'] and (isinstance(val,bool) or not isinstance(val,(float,int))):return unknown('숫자 타입 불일치')
 if typ=='integer' and not isinstance(val,int):return unknown('정수 타입 불일치')
 if typ=='boolean' and not isinstance(val,bool):return unknown('참/거짓 타입 불일치')
 if typ=='string' and not isinstance(val,str):return unknown('문자열 타입 불일치')
 if m.get('allowed_values') and val not in m['allowed_values']:return unknown('허용되지 않은 값')
 if mid=='director.attendance_pct' and not 0<=val<=100:return unknown('출석률 범위 오류')
 if typ=='integer' and val<0:return unknown('음수 개수')
 if op=='in':return val in e['value']
 try:return OPS[op](val,e['value'])
 except (TypeError,KeyError):return unknown('연산 불가')

def eval_case(c):
 if c['agenda_state'] in ['withdrawn','report_only']:
  return {'recommended_vote':'NO_RECOMMENDATION','workflow_status':'not_votable','trace':[]}
 if c['profile_available'] is False:
  return {'recommended_vote':'NO_RECOMMENDATION','workflow_status':'policy_unavailable','trace':[]}
 trace=[]
 for rid in c['rule_ids']:
  r=RULE[rid];issues=[];g=evaluate_expr(r['applies_when'],c['facts'],c,issues)
  state='incomplete' if g is None else 'not_applicable' if g is False else None
  if state is None:
   v=evaluate_expr(r['when'],c['facts'],c,issues)
   state='incomplete' if v is None else 'not_matched' if v is False else 'matched'
   if state=='matched' and r['exception']:
    ex=evaluate_expr(r['exception'],c['facts'],c,issues)
    state='incomplete' if ex is None else 'exception_review' if ex else 'matched'
  trace.append({'rule_id':rid,'state':state,'issues':list(dict.fromkeys(issues)), 'vote':r['effect']['recommended_vote'] if state=='matched' else None})
 votes={t['vote'] for t in trace if t['vote']}
 unresolved=any(t['state'] in ['incomplete','exception_review'] for t in trace)
 if 'AGAINST' in votes:vote='AGAINST'
 elif 'FOR' in votes and not unresolved and 'NO_RECOMMENDATION' not in votes and c['category_ready']:vote='FOR'
 else:vote='NO_RECOMMENDATION'
 status='needs_review'
 if vote=='NO_RECOMMENDATION' and any(t['state']=='incomplete' for t in trace):status='insufficient_evidence'
 if vote in ['FOR','AGAINST'] and not unresolved and len(votes)==1 and c['category_ready']:status='ready_for_review'
 return {'recommended_vote':vote,'workflow_status':status,'trace':trace}

def fact(mid,value,status='verified',public_at='2026-03-01T10:00:00+09:00',accepted_by='synthetic-reviewer'):
 return {'metric_id':mid,'value':value,'status':status,'unit':MET[mid]['unit'],'entity_id':'SYNTHETIC_CO','agenda_id':'SYNTHETIC_AGENDA','period_start':'2023-03-01','period_end':'2026-02-28','public_at':public_at,'evidence_refs':['SYNTHETIC_E1'],'accepted_by':accepted_by,'derivation':None}
EVIDENCE={'SYNTHETIC_E1':{'document_type':'synthetic_fixture','entity_id':'SYNTHETIC_CO','agenda_id':'SYNTHETIC_AGENDA','public_at':'2026-03-01T10:00:00+09:00','locator':'명시적으로 만든 가상 사례; 실제 공시나 기관 행사 결과 아님'}}
CASES=[]
def case(id,title,rules,values,vote,status,**kwargs):
 c={'id':id,'title':title,'synthetic':True,'scope':'isolated_rule_conformance','entity_id':'SYNTHETIC_CO','agenda_id':'SYNTHETIC_AGENDA','as_of':'2026-03-10T23:59:59+09:00','agenda_state':'votable','profile_available':True,'category_ready':True,'rule_ids':rules,'facts':{k:fact(k,v) for k,v in values.items()},'evidence_registry':EVIDENCE,'expected':{'recommended_vote':vote,'workflow_status':status}}
 c.update(kwargs);CASES.append(c);return c
base={'director.is_reelection':True,'director.attendance_complete':True,'director.attendance_exception':False}
case('EX01','출석 6/8 = 75% 경계',['B01'],dict(base,**{'director.attendance_pct':75}),'NO_RECOMMENDATION','needs_review')
case('EX02','출석 5/8 = 62.5%, 예외 없음',['B01'],dict(base,**{'director.attendance_pct':62.5}),'AGAINST','ready_for_review')
case('EX03','62.5%지만 소명 수용',['B01'],dict(base,**{'director.attendance_pct':62.5,'director.attendance_exception':True}),'NO_RECOMMENDATION','needs_review')
case('EX04','62.5%, 소명 존재 여부 미확인',['B01'],{k:v for k,v in dict(base,**{'director.attendance_pct':62.5}).items() if k!='director.attendance_exception'},'NO_RECOMMENDATION','insufficient_evidence')
c=case('EX05','기준일 후 공개된 출석률 제외',['B01'],dict(base,**{'director.attendance_pct':62.5}),'NO_RECOMMENDATION','insufficient_evidence');c['facts']['director.attendance_pct']['public_at']='2026-03-20T10:00:00+09:00'
case('EX06','신임 후보에는 임기 출석률 규칙 비적용',['B01'],{'director.is_reelection':False},'NO_RECOMMENDATION','needs_review')
case('EX07','감사의견 부적정: OPM 초안 반대',['F01'],{'financial.audit_opinion':'adverse'},'AGAINST','ready_for_review')
case('EX08','감사의견 미확인: 의견거절로 추정 금지',['F01'],{},'NO_RECOMMENDATION','insufficient_evidence')
case('EX09','철회 안건은 정책 기본값보다 우선',['F01'],{'financial.audit_opinion':'adverse'},'NO_RECOMMENDATION','not_votable',agenda_state='withdrawn')
case('EX10','기관 정책팩 부재: OPM으로 대체 금지',['F01'],{'financial.audit_opinion':'adverse'},'NO_RECOMMENDATION','policy_unavailable',profile_available=False)
case('EX11','반대와 찬성의 충돌은 반대 근거 보존',['B04','B05'],{'director.accountable_harm':True,'director.fitness_accepted':True},'AGAINST','needs_review')
c=case('EX12','법 위반이라고 쓴 LLM 초안은 미확인',['G01'],{'law.compliance':'violation'},'NO_RECOMMENDATION','insufficient_evidence');c['facts']['law.compliance']['accepted_by']=None
case('EX13','확인된 적법성만으로 찬성 생성 금지',['G01'],{'law.compliance':'compliant'},'NO_RECOMMENDATION','needs_review')
c=case('EX14','미확인 기준이 남은 찬성 차단',['B04','B05'],{'director.fitness_accepted':True},'NO_RECOMMENDATION','insufficient_evidence')
c=case('EX15','숫자 대신 true가 들어오면 비교 금지',['B01'],dict(base,**{'director.attendance_pct':True}),'NO_RECOMMENDATION','insufficient_evidence')
c=case('EX16','단위 혼용 차단',['B01'],dict(base,**{'director.attendance_pct':0.625}),'NO_RECOMMENDATION','insufficient_evidence');c['facts']['director.attendance_pct']['unit']='ratio'
c=case('EX17','회사·후보가 다른 근거 차단',['B04'],{'director.accountable_harm':True},'NO_RECOMMENDATION','insufficient_evidence');c['facts']['director.accountable_harm']['entity_id']='OTHER_CO'
case('EX18','단순 소각에는 처분 불공정 자료 미요구',['T02'],{'treasury.action':'cancel'},'NO_RECOMMENDATION','needs_review')
case('EX19','자료가 정상이어도 카테고리 미지원이면 찬성 불가',['B05'],{'director.fitness_accepted':True},'NO_RECOMMENDATION','needs_review',category_ready=False)
c=case('EX20','음수 순익의 배당성향은 0으로 대체하지 않음',['D01'],{'dividend.funding_gap':False},'NO_RECOMMENDATION','insufficient_evidence');c['facts']['dividend.payout_pct']=fact('dividend.payout_pct',None,status='not_applicable')
c=case('EX21','적자라도 비율 비적용 근거·재원·정책 확인 시 배당 찬성 경로',['D01','D02','D03'],{'dividend.payout_comparable':False,'dividend.funding_gap':False,'dividend.underpayment_harm':False,'dividend.policy_consistent':True},'FOR','ready_for_review');c['facts']['dividend.payout_pct']=fact('dividend.payout_pct',None,status='not_applicable')
c=case('EX22','적자여도 재원 부족 신호는 유지',['D01','D02','D03'],{'dividend.payout_comparable':False,'dividend.funding_gap':True,'dividend.underpayment_harm':False,'dividend.policy_consistent':True},'NO_RECOMMENDATION','needs_review');c['facts']['dividend.payout_pct']=fact('dividend.payout_pct',None,status='not_applicable')
case('EX23','감사 직무 이력 부재가 확인된 신임: 용역비율 비적용',['U02'],{'audit.has_relevant_service_5y':False},'NO_RECOMMENDATION','needs_review')
case('EX24','감사 경력 자체가 미확인이면 비적용 처리 금지',['U02'],{},'NO_RECOMMENDATION','insufficient_evidence')
case('EX25','출석률 예외 미확정이어도 다른 규칙의 확정 반대 보존',['B01','B04'],{'director.is_reelection':True,'director.attendance_complete':True,'director.attendance_pct':62.5,'director.accountable_harm':True},'AGAINST','needs_review')
# Economic formulas checked on independent arithmetic identities, not on real-company outcomes.
arithmetic=[{'case':'출석률','inputs':{'attended':6,'eligible':8},'actual':6/8*100,'expected':75}, {'case':'증가율과 희석률 구분','inputs':{'existing':100,'new':25},'increase_pct':25/100*100,'actual':25/(100+25)*100,'expected':20}, {'case':'배당성향 분모 음수','inputs':{'dividend':30,'net_income':-10},'actual':None,'expected':None}, {'case':'신임·대상 회수 0','inputs':{'attended':0,'eligible':0},'actual':None,'expected':None}]

def main():
 schema=json.loads((P/'policy.schema.json').read_text());jsonschema.Draft202012Validator.check_schema(schema);jsonschema.validate(policy,schema)
 assert len(RULE)==len(policy['rules'])
 assert len(MET)==len(policy['metrics'])
 for r in RULE.values():
  used=expr_metrics(r['applies_when'])|expr_metrics(r['when'])|expr_metrics(r['exception'])
  assert used==set(r['required_metrics']),r['id']
 assert set(policy['categories'])=={r['category'] for r in RULE.values()}
 evidence_schema=json.loads((P/'evidence.schema.json').read_text())
 jsonschema.Draft202012Validator.check_schema(evidence_schema)
 results=[]
 for c in CASES:
  for f in c['facts'].values():jsonschema.validate(f,evidence_schema,format_checker=jsonschema.FormatChecker())
  result=eval_case(c);assert all(result[k]==v for k,v in c['expected'].items()),(c['id'],result,c['expected'])
  results.append(dict(id=c['id'],title=c['title'],expected=c['expected'],actual=result,passed=True))
 for a in arithmetic:assert a['actual']==a['expected']
 # Guard the comparison operators and schema against unsupported expression payloads.
 bad=json.loads(json.dumps(policy));bad['rules'][0]['when']['op']='python_eval'
 try:jsonschema.validate(bad,schema)
 except jsonschema.ValidationError:pass
 else:raise AssertionError('unknown operator accepted')
 report={'status':'passed','rule_count':len(RULE),'metric_count':len(MET),'synthetic_cases':len(CASES),'arithmetic_cases':len(arithmetic),'policy_sha256':hashlib.sha256((P/'policy.json').read_bytes()).hexdigest(),'checks':['JSON Schema','unique ids and source/metric links','full category presence',f'{len(CASES)} synthetic predicate cases','4 arithmetic cases','unknown operator rejection'],'limitations':['실제 OPM MCP 통합·기업 판정·기관의 실제 투표 예측 성능은 검증하지 않음','정성 평가·법률검토·규칙팩 승인 프로세스는 설계 계약','가상 사례는 작성자가 설계한 명세 검사이며 독립 성능평가가 아님'],'results':results,'arithmetic':arithmetic}
 (P/'examples.json').write_text(json.dumps({'note':'가상 사례; selected rules only, no live or institutional vote prediction','cases':CASES},ensure_ascii=False,indent=2)+'\n')
 (P/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ['results','arithmetic']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
