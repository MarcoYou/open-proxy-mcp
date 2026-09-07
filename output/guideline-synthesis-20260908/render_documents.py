from pathlib import Path
import json,html
D=Path(__file__).resolve().parent
r=lambda n:json.loads((D/n).read_text())
p,c,a=r('proposal.json'),r('comparison.json'),r('source-audit.json')
h=html.escape
# Accessible SVG with two distinct lifecycles.
svg=['<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="1060" viewBox="0 0 1440 1060" role="img" aria-labelledby="title desc"><title id="title">OPM 정책 컴파일과 근거 적용·갱신 흐름</title><desc id="desc">정책을 검증해 고정한 뒤 원문에서 수용된 근거로 모든 규칙을 평가한다. 자료 갱신과 정책 갱신은 각각 영향 분석과 승인 경로를 따른다.</desc><defs><marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="none" stroke="#677786" stroke-width="1.5"/></marker></defs><style>text{font-family:Arial,\'Apple SD Gothic Neo\',sans-serif}.title{font-size:27px;font-weight:700;fill:#153546}.label{font-size:21px;font-weight:700;fill:#153546}.sub{font-size:17px;fill:#485e6b}.tag{font-size:15px;fill:#266d60}.line{fill:none;stroke:#677786;stroke-width:2;marker-end:url(#arrow)}</style><rect width="1440" height="1060" fill="#f8f8f3"/>']
def text(x,y,s,cl='sub'):svg.append(f'<text x="{x}" y="{y}" class="{cl}">{h(s)}</text>')
def box(x,y,w,title,lines,fill='#ffffff',height=110):
 svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{height}" rx="14" fill="{fill}" stroke="#cbd7d8"/>');text(x+20,y+34,title,'label')
 for i,line in enumerate(lines):text(x+20,y+64+i*23,line)
def arrow(x1,y1,x2,y2):svg.append(f'<path d="M{x1},{y1} L{x2},{y2}" class="line"/>')
text(40,46,'01  정책을 먼저 고정한다','title')
box(40,74,390,'작성 정책 + 선택한 변경',['OPM 기준 · 법률/기관 팩의 출처','범위가 정해진 고객 파라미터'])
box(500,74,370,'컴파일 / 검토',['타입·단위·참조·기간·예외·충돌','해석 미확정 / 불가 변경은 거절'])
box(940,74,460,'ResolvedPolicy',['상속을 푼 규칙 · 실제 적용 값 · 변경 이유','정책/스키마/rubric 버전 + 내용 hash'],'#e2eee8')
arrow(430,130,500,130);arrow(870,130,940,130)
text(40,255,'02  필요한 근거를 확보하고 판단한다','title')
box(40,290,250,'요청 범위 / 계획',['회의 · 후보 · as_of','필요 지표부터 역추적'])
box(322,290,250,'원문 + 조회 힌트',['OPM 공개 자료 / 파생값','값이 없으면 다음 경로'])
box(604,290,250,'LLM 과업',['추출 · 연결 · 평가','인용 / 반증 / 미해결'])
box(886,290,250,'근거 수용 검사',['주체 · 기간 · 단위 · 인용','사실과 승인 평가 분리'])
box(1168,290,232,'모든 규칙 평가',['3값 + 예외 + 적용성','결정적 결합'],'#e2eee8')
for x in [290,572,854,1136]:arrow(x,345,x+32,345)
svg.append('<path d="M1170,184 L1170,232 L1285,232 L1285,290" class="line"/>')
box(886,455,514,'분리된 결과 / 다음 행동',['권고 · 미해결 · 법률 상태 · 의결권 자격','검토 / 추가 조회 / 투표 계획 — 제출은 별도'],'#e2eee8',height=115)
arrow(1285,400,1285,455)
svg.append('<path d="M1010,400 L1010,424 L447,424 L447,400" class="line"/>');text(455,450,'수용 실패 → 부족한 근거만 다시 조회','tag')
box(40,482,785,'공개와 개인 정보의 경계',['서버: 공개 원문·검증 사실 캐시 / 사용자 조회 결과는 기본 저장하지 않음','클라이언트: 보유·위임·private overlay·참여 이력·개별 실행 manifest'],'#eef0f5',height=115)
text(40,670,'03  변경 종류에 맞춰 갱신한다','title')
box(40,706,390,'공시 / 근거 변경',['정정 · 신규 자료 · 시점 확인','source → fact → rule 영향 그래프'])
box(500,706,370,'영향받는 분석만 재실행',['현재 as_of에 사용할 수 있는 자료만','변경 전후 근거·판단 차분'])
box(940,706,460,'자료 갱신 결과',['같은 정책 버전으로 새 실행 생성','과거 실행은 유지 · 알림은 고객 설정'])
arrow(430,760,500,760);arrow(870,760,940,760)
box(40,884,390,'법령 / 정책 원문 변경',['표현·위치 변화 vs 의미 변화','조항별 후보 변경안'])
box(500,884,370,'컴파일 · 영향 평가 · 승인',['의미 변경은 기본 검토 대상','독립 평가 / 사전 위임 범위 검사'])
box(940,884,460,'새 정책 활성화 / 복구',['불변 버전 + active 포인터','실패 시 이전 검증 버전으로 복구'],'#e2eee8')
arrow(430,938,500,938);arrow(870,938,940,938)
text(40,1038,'설계 제안 · 실제 모니터/정책 자동 승격/의결권 제출은 아직 구현하지 않음','tag')
svg.append('</svg>');(D/'flowchart.svg').write_text(''.join(svg))
(D/'flowchart.mmd').write_text('''flowchart TD
  PS[작성 정책 + 범위가 정해진 고객 변경] --> C[타입·참조·기간·예외·충돌 컴파일]
  C --> RP[불변 ResolvedPolicy]
  Q[회의·후보·as_of 고정] --> EP[필요 근거 계획]
  EP --> S[OPM 원문 + 조회 힌트]
  S --> L[LLM 추출·연결·rubric 평가]
  L --> V{인용·주체·기간·단위 수용}
  V -->|실패: 부족분만| S
  V -->|통과| E[모든 적용 규칙 3값 평가]
  RP --> E
  E --> O[권고·미해결·자격·검토·행동 계획 분리]
  DC[공시 정정·신규 자료] --> DI[as_of 확인 + 영향 그래프]
  DI --> EP
  PC[법령·정책 원문 변경] --> PD[의미 변경안 + 컴파일·평가·승인]
  PD --> NV[새 버전 활성화 / 이전 버전 복구]
  NV --> RP
''')
css='''*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:28px}body{margin:0;background:#f7f8f4;color:#193341;font:16px/1.8 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif}a{color:#126e61;text-underline-offset:4px}button,input,select{font:inherit}button,select{cursor:pointer}header{background:#153746;color:#f8faf5;padding:64px max(6vw,24px) 48px}header .kicker{color:#a7d8c4;font-size:13px;letter-spacing:2px}h1{font-size:clamp(32px,5vw,60px);line-height:1.2;letter-spacing:-2px;max-width:960px;margin:20px 0}header p{color:#d6e6e6;max-width:890px;font-size:19px}header a{color:#d5f0dd}.chips{display:flex;gap:10px;flex-wrap:wrap}.chip{border:1px solid #718d93;padding:4px 12px;border-radius:30px;font-size:13px}.layout{display:grid;grid-template-columns:218px minmax(0,1fr);max-width:1450px;margin:auto;gap:48px;padding:44px 36px}aside nav{position:sticky;top:24px;font-size:14px}nav a{display:block;text-decoration:none;padding:5px 0;color:#49606a}nav strong{display:block;margin:0 0 12px;color:#173c49}main{min-width:0}section{margin-bottom:64px}h2{font-size:29px;line-height:1.4;letter-spacing:-.8px;margin:0 0 20px}h3{font-size:19px;margin:12px 0}p{margin:12px 0 18px}ul{padding-left:23px}li{margin:10px 0}.lead{font-size:21px;color:#23534d}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.card{background:#fff;border:1px solid #dbe2dd;border-radius:14px;padding:23px}.card strong{display:block;font-size:21px}.small{font-size:13px;color:#637779}.note{border-left:4px solid #ba9251;background:#f2ecdf;padding:18px 22px;border-radius:0 8px 8px 0}.controls{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}.controls input{flex:1;min-width:140px}input,select,button{border:1px solid #b9cbc4;border-radius:7px;background:white;padding:8px 12px;color:#153746}details{background:#fff;border:1px solid #d4dfd8;border-radius:10px;margin:13px 0;overflow:hidden}summary{cursor:pointer;padding:16px 20px;font-weight:650}summary span{font-size:12px;margin-right:12px;color:#418276}.comparison-body{padding:0 22px 20px}.two{display:grid;grid-template-columns:1fr 1fr;gap:24px}.lens{padding:16px;background:#f4f6f5;border-radius:8px}.lens b{font-size:13px;color:#537174}.adopt{padding:14px 0 0;color:#176356}.refs{font-size:12px;overflow-wrap:anywhere;color:#637779}.hidden{display:none}.diagram{width:100%;border:1px solid #d5e0d9;border-radius:14px}.demo{background:#e4eee7;padding:26px;border-radius:15px}.demo-grid{display:grid;grid-template-columns:1fr 1fr;gap:26px}.field{margin-bottom:15px}.field label{display:block;font-size:14px;font-weight:600;margin-bottom:5px}.field select,.field input{width:100%}.result{background:#fff;padding:24px;border-radius:12px}#vote{font-size:28px;font-weight:750;line-height:1.3}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.6;background:#f1f4f1;padding:15px;border-radius:8px}.downloads{display:flex;gap:12px;flex-wrap:wrap}.downloads a{border:1px solid #b7ccc0;border-radius:7px;padding:6px 11px;text-decoration:none}footer{padding:28px 6vw;border-top:1px solid #d4dfd8;font-size:13px;color:#637779}@media(max-width:900px){.layout{grid-template-columns:1fr;padding:24px;gap:24px}aside nav{position:static;display:flex;gap:8px;flex-wrap:wrap}nav strong{display:none}nav a{padding:3px 10px;border:1px solid #d4dfd8;border-radius:20px}aside .sections-nav{display:none}.cards{grid-template-columns:1fr}header{padding-top:40px}.two,.demo-grid{grid-template-columns:1fr}.diagram{min-height:240px}section{margin-bottom:44px}}@media print{aside,.controls,.demo{display:none}.layout{display:block;padding:0}header{background:white;color:black;padding:0}header p{color:black}details{break-inside:avoid}section{margin:24px 0}.downloads{display:none}}'''
nav='<strong>읽는 순서</strong>'+''.join(f'<a href="#{id}">{label}</a>' for id,label in [('summary','핵심 제안'),('comparison','16가지 관점 차이'),('flow','설계 흐름도'),('demo','설정 변경 체험'),('documents','문서 · JSON')])+'<div class="sections-nav">'+''.join(f'<a href="#{s["id"]}">{s["title"].split(":")[0]}</a>' for s in p['sections'])+'</div>'
body='''<section id="summary"><h2>운영의 폭과 판단의 엄밀함을 함께</h2><p class="lead">Fable의 실무·프로필 설계를 가져오고, Astra의 근거·판정 계약으로 연결한다. OPM의 가치 기준은 별도로 정의한다.</p><div class="cards"><div class="card"><strong>판단 기준</strong><p>출처의 권위, 정책의 효과, 사용자 변경을 구분한다.</p></div><div class="card"><strong>근거의 상태</strong><p>사실·평가·미확인·예외를 보존하고 부족하면 다시 조회한다.</p></div><div class="card"><strong>운영의 위임</strong><p>자동화만 바꿔서는 같은 근거의 찬반이 바뀌지 않는다.</p></div></div><p class="note">두 모델의 일반 성능 비교가 아니라 제공된 설계 산출물의 비교다. Fable 브랜치는 엔진 한계 문서 14줄을 추가했고, 상세 설계는 별도 리뷰 팩에 있다. 이 통합안도 운영 엔진으로 적용된 상태가 아니다.</p></section>'''
body+='<section id="comparison"><h2>16가지 관점 차이와 채택 방향</h2><p>Astra: 34개 규칙·45개 지표. Fable: 71개 비-default 규칙과 12개 default, 총 83개 규칙·103개 지표. 개수는 범위의 차이이며 품질 점수가 아니다.</p><div class="controls"><input id="search" aria-label="관점 검색" placeholder="예: 자동화, 출석, 갱신"><select id="priority" aria-label="검토 유형"><option value="all">모든 항목</option><option value="blocking">실행 전 수정</option><option value="foundation">기본 구조</option><option value="design">운영 확장</option><option value="evidence">비교의 근거</option></select><button id="expand">모두 펼치기</button></div>'
for row in c['differences']:
 body+=f'<details class="comparison" data-priority="{row["priority"]}"'+(' open' if row['id'] in ['C01','C02'] else '')+f'><summary><span>{row["id"]}</span>{h(row["topic"])}</summary><div class="comparison-body"><div class="two"><div class="lens"><b>ASTRA 관점</b><p>{h(row["astra"])}</p></div><div class="lens"><b>FABLE 관점</b><p>{h(row["fable"])}</p></div></div><p class="adopt"><b>제안</b> · {h(row["proposal"])}</p><p>{h(row["why"])}</p><p class="refs">Fable 근거: {h(" · ".join(row["fable_refs"]))}</p></div></details>'
body+='</section><section id="flow"><h2>정책을 고정하는 흐름, 근거를 적용하는 흐름</h2><p>정책 변경과 공시 갱신을 다른 경로로 처리한다. 둘 다 이전 실행을 덮어쓰지 않고 영향받는 범위만 다시 계산한다.</p><a href="flowchart.svg">흐름도 크게 보기 ↗</a><img class="diagram" src="flowchart.svg" alt="정책 컴파일, 근거 적용, 자료와 정책의 분리 갱신 흐름도"></section>'
body+='''<section id="demo"><h2>설정 변경 체험: 기준은 바꾸되, 자동화로 찬반을 바꾸지 않는다</h2><p>가상의 재선임 후보와 수용된 근거를 사용한다. 78% 출석은 데모 기준 75%에서 통과하지만 80%에서는 반대 사유가 된다. 실제 회사·법령·기관 정책의 재현이나 운영 승인을 뜻하지 않는다.</p><div class="demo"><div class="demo-grid"><div><div class="field"><label for="threshold">사용자가 선택한 출석 기준 (%)</label><input id="threshold" type="number" min="50" max="100" step="1" value="75"></div><div class="field"><label for="attendance">가상 후보의 직전 임기 출석률 (%)</label><input id="attendance" type="number" min="0" max="100" step="0.1" value="78"></div><div class="field"><label for="known">출석 근거 상태</label><select id="known"><option value="known">확보 · 수용됨</option><option value="unknown">미확인</option></select></div><div class="field"><label for="exception">불참 예외 평가</label><select id="exception"><option value="false">예외 인정 없음</option><option value="true">예외 인정됨</option><option value="unknown">사유 평가 미확인</option></select></div><div class="field"><label for="independence">별도 독립성 반대 사유</label><select id="independence"><option value="false">검토 완료 · 없음</option><option value="true">확인 · 수용됨</option><option value="unknown">미확인</option></select></div><div class="field"><label for="autonomy">처리 위임 수준</label><select id="autonomy"><option value="assisted">검토 보조</option><option value="manual">수동 검토</option><option value="auto">위임된 검토로 전달</option></select></div></div><div class="result" aria-live="polite"><div class="small">합성 예제의 권고</div><p id="vote"></p><p id="explanation"></p><p id="routing" class="small"></p><pre id="trace"></pre><p class="small">이 데모는 출석·독립성 두 반대 규칙과 완전성 게이트만 구현한다. 다른 근거의 완전성은 합성 입력으로 가정한다. 인용 검증·법률 팩·LLM 호출·저장·제출은 구현하지 않는다.</p></div></div></div></section>'''
for s in p['sections']:body+=f'<section id="{s["id"]}"><h2>{h(s["title"])}</h2><p>{h(s["body"])}</p><ul>'+''.join(f'<li>{h(x)}</li>' for x in s['items'])+'</ul></section>'
links=[('report.md','전체 설명 Markdown'),('comparison.json','16개 비교 기록'),('proposal.json','통합 설계 JSON'),('policy-source.json','예시 정책 JSON'),('policy.schema.json','예시 정책 Schema'),('overlay-examples.json','사용자 설정 예시'),('resolved-policies.json','컴파일된 정책'),('decision-examples.json','16개 판단 예시'),('validation.json','28개 계약 검사'),('input-manifest.json','입력 판본 · hash'),('source-audit.json','원문 내부 검증'),('flowchart.svg','흐름도 SVG'),('flowchart.mmd','흐름도 Mermaid')]
body+='<section id="documents"><h2>문서와 기계 계약</h2><p>설계 설명과 제한된 실행 예제를 구분했다. 검증은 합성 계약 검사이며 실제 의결권 품질 평가가 아니다.</p><div class="downloads">'+''.join(f'<a href="{n}">{label}</a>' for n,label in links)+'</div><p class="small">출처: 제공된 리뷰 팩 5개 파일과 고정된 Astra/Fable 커밋. 원본 파일은 이 패키지에 재배포하지 않는다. <a href="https://fund.nps.or.kr/fileDown.do?atchFileId=FL26002595&amp;atchFileSn=1">국민연금 비교 원문</a>의 출석 기준은 직전 임기와 공개된 사유를 포함하므로 Fable의 직전 사업연도 지표와 구분했다. 기타 법령 조건은 적용 전 별도 검토 대상으로 남겼다.</p></section>'
script='''const data=DATA;const $=id=>document.getElementById(id);
function filter(){for(const el of document.querySelectorAll('.comparison'))el.classList.toggle('hidden',!el.textContent.includes($('search').value)||$('priority').value!=='all'&&el.dataset.priority!==$('priority').value)}$('search').addEventListener('input',filter);$('priority').addEventListener('change',filter);$('expand').addEventListener('click',()=>{const open=$('expand').textContent==='모두 펼치기';for(const el of document.querySelectorAll('.comparison'))el.open=open;$('expand').textContent=open?'모두 접기':'모두 펼치기'});
function render(){try{if($('threshold').value===''||$('attendance').value==='')throw Error('숫자를 입력해 주세요.');const threshold=Number($('threshold').value),attendance=Number($('attendance').value);if(!Number.isFinite(attendance)||attendance<0||attendance>100)throw Error('출석률은 0~100% 사이입니다.');const overlay={id:'interactive-opm-demo',changes:[{parameter:'attendance_min_pct',value:threshold,reason:'합성 체험에서 선택한 기준'}]},p=OPMDemo.compile(data.policy,overlay),i=JSON.parse(JSON.stringify(data.input));i.facts.attendance_pct.value=attendance;i.facts.attendance_pct.status=$('known').value;for(const [id,key] of [['exception','attendance_exception_accepted'],['independence','independence_concern_accepted']]){i.facts[key].status=$(id).value==='unknown'?'unknown':'known';i.facts[key].value=$(id).value==='true'}const o=OPMDemo.evaluate(p,i,$('autonomy').value);$('vote').textContent={FOR:'찬성 · 데모 범위',AGAINST:'반대 근거 있음',NO_RECOMMENDATION:'권고 보류'}[o.assessment.vote];$('explanation').textContent=o.assessment.basis.length?'확인된 반대 사유를 보존합니다. 다른 미해결 항목은 별도로 표시합니다.':o.unresolved.length?'필요한 근거 또는 예외 평가가 미확인입니다. 자동화만으로 해소하지 않습니다.':'합성 예제의 긍정 조건과 필수 근거가 충족됐습니다.';$('routing').textContent='처리 상태: '+o.workflow.status+' · 실제 제출 기능 없음';$('trace').textContent=JSON.stringify({policy_diff:p.diff,vote:o.assessment.vote,basis:o.assessment.basis,unresolved:o.unresolved,rules:o.rule_results},null,2)}catch(e){$('vote').textContent='설정 거절';$('explanation').textContent=e.message;$('routing').textContent='정책을 컴파일하지 않았습니다.';$('trace').textContent=''}}
for(const id of ['threshold','attendance','known','exception','independence','autonomy'])$(id).addEventListener('input',render);render();'''.replace('DATA',json.dumps({'policy':r('policy-source.json'),'input':r('decision-examples.json')['examples'][0]['input']},ensure_ascii=False).replace('</','<\\/'))
out='<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+h(p['title'])+'</title><style>'+css+'</style></head><body><header><div class="kicker">OPEN PROXY MCP · DESIGN SYNTHESIS · 2026.09.08</div><h1>OPM의 기준은 분명하게.<br>적용은 유연하게.</h1><p>Astra–Fable 관점 차이를 기록하고, 근거를 읽는 LLM과 조절 가능한 정책을 연결하는 통합 설계를 제안한다.</p><div class="chips">'+''.join('<span class="chip">'+x+'</span>' for x in ['LLM-friendly','Adjustable','Scalable','Auto-updatable','Customizable'])+'</div></header><div class="layout"><aside><nav>'+nav+'</nav></aside><main>'+body+'</main></div><footer>astra-fable-opm-guideline-v2 · 두 원안 보존 · 통합 설계 제안 · 운영 엔진 미적용</footer><script>'+ (D/'engine.js').read_text()+'</script><script>'+script+'</script></body></html>'
(D/'index.html').write_text(out)
md=['# '+p['title'],'','2026-09-08 · 설계 제안 · 운영 엔진 미적용','',p['thesis'],'','## 비교의 범위','', 'Astra의 커밋된 34개 규칙·45개 지표 패키지와 Fable 리뷰 팩의 71개 비-default+12개 default 규칙·103개 지표를 비교한다. Fable 브랜치 자체의 변경은 엔진 한계 문서 14줄이다. 리뷰 팩의 상세 설계가 브랜치에 구현된 것으로 해석하지 않는다. 모델의 일반 성능을 비교하는 실험이 아니다. 입력 판본은 [manifest](input-manifest.json)에 고정했다.','', '## 관점 차이','']
for row in c['differences']:
 md += ['### '+row['id']+' · '+row['topic'],'','- Astra: '+row['astra'],'- Fable: '+row['fable'],'- 채택 방향: '+row['proposal'],'',row['why'],'','근거: `02-opm-guideline-v2.json`의 '+', '.join('`'+x+'`' for x in row['fable_refs']), '']
md+=['## 통합 설계','','![정책 컴파일과 근거 적용·갱신 흐름](flowchart.svg)','']
for s in p['sections']:md+=['### '+s['title'],'',s['body'],'']+['- '+x for x in s['items']]+['']
md+=['## 증거와 한계','','국민연금 출석 기준의 기간·사유는 [비교 원문](https://fund.nps.or.kr/fileDown.do?atchFileId=FL26002595&atchFileSn=1)을 대조했다. 나머지 법률 조건은 이번 비교에서 포괄적으로 재검증하지 않았다. 자산/자본금, >/>= 등의 불일치는 원문 내부 검증이며 올바른 법률 해석을 확정했다는 뜻이 아니다. Fable의 실제 회사 재현 보고와 독립 리뷰 부록은 제공 자료로 읽었으며 이번에 재현하거나 작성자의 자격을 검증하지 않았다.','','실행 예제는 동일 작성자의 합성 기대값에 대한 28개 계약 검사다. 과거 회사 표결이나 독립 판정자 자료에 대한 정확도 결과가 아니다. 실제 기관 정책과 OPM 정책의 적합성은 별도 평가가 필요하다.','','## 산출물','','[HTML 읽기](index.html) · [기계 비교 기록](comparison.json) · [통합 설계 JSON](proposal.json) · [정책 예시](policy-source.json) · [Schema](policy.schema.json) · [변경 예시](overlay-examples.json) · [실행 정책](resolved-policies.json) · [합성 사례](decision-examples.json) · [검증](validation.json)']
(D/'report.md').write_text('\n'.join(md)+'\n')
(D/'README.md').write_text('''# OPM 가이드라인 통합 설계 · 2026-09-08

Fable의 실무 범위와 Astra의 근거·판정 계약을 연결하는 **설계 제안**이다. OPM의 목표는 장기 기업가치와 주주 간 공정한 대우를 위한 기준을 공개하고, 왜 그 기준을 적용했는지 설명하는 것이다. 이 가치 기준의 최종 승인도 아직 남아 있다.

[HTML 문서와 설정 체험](index.html) · [전체 설명](report.md) · [흐름도](flowchart.svg)

## 비교 대상

Astra 패키지: 34개 규칙·45개 지표. Fable 리뷰 팩: 71개 비-default와 12개 default, 총 83개 규칙·103개 지표. Fable 브랜치 변경은 엔진 한계를 기록한 문서 14줄이며, 상세 설계는 제공된 리뷰 팩에 있다. 두 원안과 입력 파일은 보존했다. [입력 manifest](input-manifest.json)에 판본과 hash를 기록했다.

16개 관점 차이는 [comparison.json](comparison.json)에 기록했다. 규칙 수는 품질 점수가 아니며 두 모델의 일반 성능 비교도 아니다. Fable의 회사 사례와 독립 리뷰는 제공 자료이지 이번에 독립 재현·검증한 결과가 아니다.

## 제안하는 계약

1. **정책·근거·위임 분리**: 규칙의 출처와 효과, 사실의 상태, 자동화 수준을 서로 다른 축으로 둔다. 같은 정책과 수용된 근거에서는 자동화 수준만 바꿔 찬반을 바꾸지 않는다.
2. **컴파일된 정책**: 타입·단위·기간·예외·참조·사용자 변경의 충돌을 검증하고 상속이 풀린 실행 정책을 버전과 hash로 고정한다. 비교용 기관 정책은 독립 실행하며, 고객이 바꾸면 기관 공식 기준으로 표시하지 않는다.
3. **근거 중심 적용**: LLM이 원문을 추출·연결·평가하고 근거 수용 검사를 거친다. 모든 적용 규칙을 평가해 확인된 반대와 미해결 사유를 함께 보존한다. 같은 반대 규칙의 예외가 미확인이면 그 규칙의 반대는 확정하지 않는다.
4. **분리된 출력**: 권고·법률 상태·의결권 자격·검토 상태·투표 방식·행동 계획을 따로 둔다. 자료 부족을 자동으로 기권/찬성/반대로 바꾸지 않는다.
5. **범위가 있는 맞춤 설정**: 이름·단위·허용 범위·권한·변경 이유가 있는 파라미터를 조정한다. 사실이나 법률 팩의 법정 요건을 사용자 선호로 덮지 않는다.
6. **확장과 갱신**: 공개 근거는 재사용하고 변경 영향이 있는 노드만 다시 실행한다. 자료 갱신과 정책 개정은 별도 파이프라인이다. 정책 의미 변경은 기본적으로 검토 후 새 버전으로 활성화한다. 과거 as_of 실행은 보존한다.

서버는 원문·힌트·공개 사실을 제공한다. 사용자 보유·위임·private overlay·실행 이력은 클라이언트 또는 명시적으로 승인된 별도 저장소에 둔다. 사용자 조회 결과를 기본 저장하지 않는 현재 OPM 원칙을 유지한다.

## 승인·재현 계약

OPM 정책 책임자는 가치·규칙 효과·편집 범위를, 법률 검토자는 적용 법률의 해석을, 고객 정책 책임자는 사용자 정책과 위임을 승인한다. 근거 검증기는 인용·주체·기간·단위를 검사하고, 평가 검토자 또는 제한적으로 위임된 자동 평가자가 rubric에 따른 판단을 수용한다. 담당자와 실제 승격 기준의 지정은 남아 있다.

확정 반대 A와 미확인 B가 함께 있으면 권고는 AGAINST, 처리 상태는 NEEDS_REVIEW다. A 자체의 예외가 미확인이면 A의 반대는 확정하지 않는다. 찬성 게이트는 적용 규칙·필수 근거 목록의 평가 완료와 정책의 긍정 조건을 요구한다. 자동화로 새 근거가 수용돼 결과가 달라지면 근거 차분을 보여 준다.

정책뿐 아니라 원문 판본·내용 hash·인용 위치·수용된 사실/평가·과업/검증기 버전도 고정한다. 공개 원문/사실 캐시와 클라이언트가 보관하는 개인 실행 manifest를 분리한다. 원문 재확보 실패는 재현 불가로 표시한다.

## 구현된 것과 제안인 것

HTML/Markdown, 16개 비교 기록, 통합 설계 JSON, 흐름도, **한정된 합성 실행 예제**를 만들었다. 예제는 출석·독립성 두 반대 규칙과 긍정 커버리지 게이트, 사용자 출석 기준 75→80 변경을 지원한다. 50~100의 편집 범위는 데모용이며 운영 승인 범위나 기관 공식 기준이 아니다. 합성 78% 출석은 75% 기준을 통과하고 80% 기준에서 반대 사유가 된다.

[policy-source.json](policy-source.json) → [overlay-examples.json](overlay-examples.json) → [resolved-policies.json](resolved-policies.json). [policy.schema.json](policy.schema.json)은 이 최소 예제의 스키마다. 실제 운영용 전체 DSL이 아니다. [decision-examples.json](decision-examples.json)에 16개 합성 사례와 분리 출력을 담았다.

예제의 근거 수용·나머지 범위 커버리지는 합성 입력으로 가정한다. 실제 제품에서 사용자나 LLM이 임의로 수용 완료를 선언하도록 허용한다는 뜻이 아니다. 인용 대조, 법률 적용, LLM 호출, 기관 팩, 전 범위 컴파일, 모니터, 자동 갱신, 결과 저장, 의결권 제출은 구현하지 않았다.

[validation.json](validation.json)의 28개 검사는 계약 준수만 확인한다. 같은 작성자가 만든 기대값이므로 실제 의결권 판단 품질이나 전문가 정확도를 입증하지 않는다. 운영 승격 전 독립 자료와 별도 판정자 평가가 필요하다.

## 관리와 다음 단계

설명 정본은 proposal.json과 comparison.json이다. HTML/Markdown은 이를 읽기 좋게 생성한 문서다. 기존 두 정책의 정본을 교체하지 않는다. 기계 예제의 정본은 policy-source.json이며 engine.js는 제한된 계약 검증용이다.

다음 구현은 출석·독립성·정관 조문 세 흐름의 실제 MCP 출력부터 검증한다. 근거의 주체/기간 오류를 차단하고, 결측 시 원문과 다음 조회 경로가 유효한지 측정한다. 그 이후 사용자 정책과 대량 처리, 정책 갱신·실무 모듈을 확장한다. 이 패키지는 astra-fable-opm-guideline-v2 브랜치에서 관리한다. OPM 운영 코드와 배포는 변경하지 않았다. 문서 사이트 게시는 별도다.
''')
print('rendered HTML, report, README and SVG')
