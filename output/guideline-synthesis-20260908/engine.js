/* Limited demonstration contract. No production data, legal evaluation or ballot submission. */
(function(root){
'use strict';
const copy=x=>JSON.parse(JSON.stringify(x));
const fail=m=>{throw new Error(m)};
function keys(o,allowed,required=allowed){if(!o||typeof o!=='object'||Array.isArray(o))fail('object required');if(Object.keys(o).some(k=>!allowed.includes(k))||required.some(k=>!(k in o)))fail('unknown or missing field');}
function typeOperand(o,p){
 keys(o,['metric','parameter','literal'],[]);if(Object.keys(o).length!==1)fail('one operand required');
 if('metric' in o){if(!p.metrics[o.metric])fail('unknown metric');return p.metrics[o.metric]}
 if('parameter' in o){if(!p.parameters[o.parameter])fail('unknown parameter');return p.parameters[o.parameter]}
 if(!['number','boolean'].includes(typeof o.literal)||typeof o.literal==='number'&&!Number.isFinite(o.literal))fail('invalid literal');
 return {type:typeof o.literal,unit:typeof o.literal==='boolean'?'boolean':null};
}
function checkExpr(e,p){
 if('const' in e){keys(e,['const']);if(typeof e.const!=='boolean')fail('boolean constant required');return}
 keys(e,['op','left','right']);if(!['eq','lt'].includes(e.op))fail('unsupported operator');
 const l=typeOperand(e.left,p),r=typeOperand(e.right,p);
 if(l.type!==r.type||l.unit&&r.unit&&l.unit!==r.unit)fail('type or unit mismatch');
 if(e.op==='lt'&&l.type!=='number')fail('numeric comparison required');
}
function compile(source,overlay){
 const p=copy(source);keys(p,['schema_version','id','version','status','authority','parameters','metrics','rules','positive_gate','aggregation','limitations']);
 if(p.schema_version!=='opm-demo-policy/1'||p.status!=='synthetic_demonstrator'||p.authority!=='opm_proposal'||p.aggregation!=='preserve_confirmed_opposition_then_require_complete_support')fail('unsupported policy contract');
 const values={};for(const [name,x] of Object.entries(p.parameters)){
  keys(x,['type','unit','default','minimum','maximum','owner','editable','note']);
  if(x.type!=='number'||x.unit!=='percent'||x.owner!=='opm'||typeof x.editable!=='boolean'||![x.default,x.minimum,x.maximum].every(Number.isFinite)||x.minimum>x.maximum||x.default<x.minimum||x.default>x.maximum)fail('invalid parameter definition');values[name]=x.default;
 }
 for(const x of Object.values(p.metrics)){keys(x,['type','unit','min','max','period','kind'],['type','unit','period','kind']);if(!['number','boolean'].includes(x.type)||!['prior_term','meeting'].includes(x.period)||!['fact','derived_fact','assessment'].includes(x.kind)||x.unit!==(x.type==='number'?'percent':'boolean'))fail('invalid metric definition');}
 const ids=new Set();for(const r of p.rules){keys(r,['id','applies_when','test','exception','effect']);if(ids.has(r.id)||r.effect!=='oppose')fail('duplicate rule or unsupported effect');ids.add(r.id);for(const e of [r.applies_when,r.test,r.exception])checkExpr(e,p)}checkExpr(p.positive_gate,p);
 keys(overlay,['id','changes']);if(!Array.isArray(overlay.changes))fail('changes array required');const touched=new Set(),diff=[];
 for(const c of overlay.changes){keys(c,['parameter','value','reason']);const x=p.parameters[c.parameter];if(!x||!x.editable||touched.has(c.parameter))fail('unknown, locked or conflicting override');if(typeof c.value!=='number'||!Number.isFinite(c.value)||c.value<x.minimum||c.value>x.maximum)fail('override outside typed bounds');if(typeof c.reason!=='string'||!c.reason.trim())fail('change reason required');touched.add(c.parameter);values[c.parameter]=c.value;diff.push({parameter:c.parameter,from:x.default,to:c.value,reason:c.reason});}
 return {schema_version:'opm-demo-resolved/1',source_id:p.id,source_version:p.version,profile_id:overlay.id,authority:p.authority,parameters:values,metrics:p.metrics,rules:p.rules,positive_gate:p.positive_gate,aggregation:p.aggregation,diff};
}
function evaluate(p,input,autonomy='assisted'){
 if(!['manual','assisted','auto'].includes(autonomy))fail('invalid autonomy');
 const rejected=[];
 function metric(name){
  const spec=p.metrics[name],r=input.facts[name];
  if(!r||r.status!=='known')return null;
  if(r.accepted!==true||!r.evidence_ref||r.entity_id!==input.entity_id||r.period!==spec.period||!/^\d{4}-\d{2}-\d{2}$/.test(r.available_at||'')||r.available_at>input.as_of||typeof r.value!==spec.type||(spec.type==='number'&&(!Number.isFinite(r.value)||r.value<spec.min||r.value>spec.max))){rejected.push(name);return null}
  return r.value;
 }
 function val(o){if('metric'in o)return metric(o.metric);if('parameter'in o)return p.parameters[o.parameter];return o.literal}
 function expr(e){if('const'in e)return e.const;const l=val(e.left),r=val(e.right);if(l===null||r===null)return null;return e.op==='eq'?l===r:l<r}
 const results=[];
 for(const r of p.rules){const applies=expr(r.applies_when);let status;
  if(applies===false)status='not_applicable';else if(applies===null)status='unresolved';else{const hit=expr(r.test);if(hit===false)status='clear';else if(hit===null)status='unresolved';else{const ex=expr(r.exception);status=ex===true?'exception_accepted':ex===null?'unresolved':'oppose'}}
  results.push({rule_id:r.id,status});
 }
 const basis=results.filter(r=>r.status==='oppose').map(r=>r.rule_id),unresolved=results.filter(r=>r.status==='unresolved').map(r=>r.rule_id);
 const positive=expr(p.positive_gate);if(positive!==true)unresolved.push('positive_coverage_gate');
 const vote=basis.length?'AGAINST':unresolved.length?'NO_RECOMMENDATION':'FOR';
 return {assessment:{vote,basis,scope:'synthetic demonstration only'},unresolved,rule_results:results,rejected_facts:[...new Set(rejected)],workflow:{status:unresolved.length?'NEEDS_REVIEW':autonomy==='auto'?'READY_FOR_DELEGATED_REVIEW':'READY_FOR_REVIEW',autonomy},eligibility:'NOT_ASSESSED',ballot:{kind:'SIMPLE_VOTE_DEMO'},action_plan:{submission:'NOT_IMPLEMENTED'},profile_id:p.profile_id};
}
root.OPMDemo={compile,evaluate};if(typeof module!=='undefined')module.exports=root.OPMDemo;
})(typeof globalThis==='undefined'?this:globalThis);
