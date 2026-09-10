const fs=require('fs'),assert=require('assert'),crypto=require('crypto');
const {compile,evaluate}=require('./engine.js');const read=n=>JSON.parse(fs.readFileSync(__dirname+'/'+n));const write=(n,x)=>fs.writeFileSync(__dirname+'/'+n,JSON.stringify(x,null,2)+'\n');const clone=x=>JSON.parse(JSON.stringify(x));
const source=read('policy-source.json'),overlays=read('overlay-examples.json'),compiled=overlays.map(x=>compile(source,x));
const sha=x=>crypto.createHash('sha256').update(JSON.stringify(x)).digest('hex');
write('resolved-policies.json',compiled.map(x=>({...x,artifact_sha256:sha(x)})));
function input(){return {entity_id:'SYNTHETIC-CANDIDATE-001',as_of:'2026-09-08',facts:Object.fromEntries(Object.entries({attendance_pct:78,is_reelection:true,attendance_exception_accepted:false,independence_concern_accepted:false,coverage_complete:true}).map(([k,v])=>[k,{status:'known',value:v,accepted:true,evidence_ref:'synthetic-fixture:'+k,entity_id:'SYNTHETIC-CANDIDATE-001',period:source.metrics[k].period,available_at:'2026-09-01'}]))}}
const checks=[],examples=[];
function run(name,fn){fn();checks.push({name,passed:true})}
function scenario(name,mutate,vote,profile=0){run(name,()=>{const i=input();mutate(i);const out=evaluate(compiled[profile],i);assert.equal(out.assessment.vote,vote);examples.push({name,input:i,profile_id:compiled[profile].profile_id,output:out});});}
scenario('78% / OPM demo 75%: support with complete synthetic coverage',()=>{},'FOR');
scenario('78% / client demo 80%: opposition',()=>{},'AGAINST',1);
scenario('75% equality does not trigger less-than rule',i=>i.facts.attendance_pct.value=75,'FOR');
scenario('74.99% triggers opposition',i=>i.facts.attendance_pct.value=74.99,'AGAINST');
scenario('unknown attendance cannot become support',i=>i.facts.attendance_pct.status='unknown','NO_RECOMMENDATION');
scenario('unknown exception blocks this opposing rule',i=>{i.facts.attendance_pct.value=60;i.facts.attendance_exception_accepted.status='unknown'},'NO_RECOMMENDATION');
scenario('accepted exception clears this opposing rule',i=>{i.facts.attendance_pct.value=60;i.facts.attendance_exception_accepted.value=true},'FOR');
scenario('separate confirmed opposition survives unknown attendance',i=>{i.facts.attendance_pct.status='unknown';i.facts.independence_concern_accepted.value=true},'AGAINST');
scenario('coverage false cannot create support',i=>i.facts.coverage_complete.value=false,'NO_RECOMMENDATION');
scenario('new appointment makes prior-term attendance inapplicable',i=>{i.facts.is_reelection.value=false;delete i.facts.attendance_pct},'FOR');
for(const [name,mutate] of Object.entries({future:r=>r.available_at='2026-09-09',entity:r=>r.entity_id='OTHER',period:r=>r.period='prior_fiscal_year',type:r=>r.value='78',acceptance:r=>r.accepted=false,citation:r=>delete r.evidence_ref}))scenario('reject incompatible fact: '+name,i=>mutate(i.facts.attendance_pct),'NO_RECOMMENDATION');
run('autonomy cannot change assessment for every fixture',()=>{for(const x of examples){const p=compiled.find(p=>p.profile_id===x.profile_id);const results=['manual','assisted','auto'].map(m=>evaluate(p,x.input,m).assessment);assert.deepStrictEqual(results[0],results[1]);assert.deepStrictEqual(results[1],results[2]);}});
for(const [name,changes] of Object.entries({out_of_bounds:[{parameter:'attendance_min_pct',value:101,reason:'test'}],string_value:[{parameter:'attendance_min_pct',value:'80',reason:'test'}],unknown_parameter:[{parameter:'legal_min',value:80,reason:'test'}],no_reason:[{parameter:'attendance_min_pct',value:80,reason:''}],conflict:[{parameter:'attendance_min_pct',value:80,reason:'a'},{parameter:'attendance_min_pct',value:85,reason:'b'}],hidden_authority:[{parameter:'attendance_min_pct',value:80,reason:'test',authority:'law'}]}))run('reject overlay '+name,()=>assert.throws(()=>compile(source,{id:'bad',changes})));
run('reject unknown operator',()=>{const p=clone(source);p.rules[0].test.op='evaluate_natural_language';assert.throws(()=>compile(p,overlays[0]));});
run('reject unknown metric',()=>{const p=clone(source);p.rules[0].test.left.metric='absent';assert.throws(()=>compile(p,overlays[0]));});
run('reject duplicate rule',()=>{const p=clone(source);p.rules.push(p.rules[0]);assert.throws(()=>compile(p,overlays[0]));});
run('reject locked parameter override',()=>{const p=clone(source);p.parameters.attendance_min_pct.editable=false;assert.throws(()=>compile(p,overlays[1]));});
run('reject mixed types',()=>{const p=clone(source);p.rules[0].test.left.metric='is_reelection';assert.throws(()=>compile(p,overlays[0]));});
write('decision-examples.json',{status:'synthetic_contract_examples_not_quality_benchmark',examples});
write('validation.json',{status:'passed',scope:'limited demonstration semantic contract',checks,check_count:checks.length,scenario_count:examples.length,policy_source_sha256:crypto.createHash('sha256').update(fs.readFileSync(__dirname+'/policy-source.json')).digest('hex'),limitations:['Same author supplied expected results; this establishes contract conformance only.','No real MCP calls, independent truth labels, legal evaluation, source retrieval or LLM performance measurement.']});
console.log(JSON.stringify({passed:checks.length,scenarios:examples.length}));
