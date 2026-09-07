from pathlib import Path
import json,html,hashlib,re
P=Path(__file__).resolve().parent
D=json.loads((P/'policy.json').read_text()); M={m['id']:m for m in D['metrics']}; R={r['id']:r for r in D['rules']}; S={s['id']:s for s in D['source_registry']}
H=html.escape
sha=hashlib.sha256((P/'policy.json').read_bytes()).hexdigest()

def refs(ids):return ' '.join(f'<a class="cite" href="#source-{H(i)}">{H(i)}</a>' for i in ids)
def table(head,rows):return '<div class="table-scroll"><table><thead><tr>'+''.join('<th>'+x+'</th>' for x in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+x+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'
def section(id,kicker,title,body):return f'<section id="{id}"><div class="eyebrow">{kicker}</div><h2>{title}</h2>{body}</section>'
def linkfile(path,label=None):return f'<a href="../../{H(path)}">{H(label or path)}</a>'
CSS='''
:root{--ink:#122b36;--muted:#536976;--paper:#f3f5f4;--line:#d5e0e2;--teal:#087e83;--blue:#285b88;--red:#9a3e31;--gold:#8e661e;--navy:#113443}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:30px}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.75 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;word-break:keep-all;overflow-wrap:anywhere}a{color:var(--teal);text-underline-offset:4px}h1,h2,h3,h4,p{margin-top:0}h1{font-size:clamp(36px,4.2vw,65px);line-height:1.22;letter-spacing:-2px;font-weight:750;max-width:960px;margin-bottom:26px}h2{font-size:30px;line-height:1.4;letter-spacing:-1px;margin-bottom:24px}h3{font-size:20px;line-height:1.5;margin-bottom:12px}p{margin-bottom:17px}.shell{display:grid;grid-template-columns:235px minmax(0,1fr);max-width:1800px;margin:auto}aside{background:var(--navy);color:#fff;min-height:100vh}.side-inner{position:sticky;top:0;padding:38px 25px;height:100vh;display:flex;flex-direction:column}.brand{letter-spacing:2px;font-size:14px;font-weight:800;margin-bottom:8px}.brand-sub{color:#c5dcdf;font-size:12px;line-height:1.7;margin-bottom:44px}.side-inner nav a{display:block;color:#e4eff0;text-decoration:none;font-size:14px;padding:11px 0;border-bottom:1px solid #ffffff1c}.side-inner nav a:hover{color:#8ee5d9}.side-footer{margin-top:auto;color:#b9d4d9;font-size:12px}.side-footer a{color:#c8eceb}main{padding:58px clamp(24px,4.2vw,76px) 90px;min-width:0}.eyebrow{color:var(--teal);font-size:12px;letter-spacing:1.5px;font-weight:750;text-transform:uppercase;margin-bottom:13px}.meta{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:32px}.tag,.cite{display:inline-block;padding:3px 9px;border-radius:5px;font-size:12px;line-height:1.65;background:#e6efee;color:#195b60}.tag.draft{background:#fff0d5;color:#715014}.hero-intro{font-size:21px;line-height:1.7;max-width:900px;color:#375766;margin-bottom:24px}.lead{background:#e3efee;border-left:4px solid var(--teal);padding:22px 25px;line-height:1.85;border-radius:0 8px 8px 0}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:30px 0 42px}.stat{padding:19px 21px;border:1px solid var(--line);background:white;border-radius:9px}.stat b{font-size:30px;display:block;line-height:1.3;color:var(--teal)}.stat span{font-size:13px;color:var(--muted)}section{padding:48px 0;border-top:1px solid var(--line)}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.card{background:#fff;border:1px solid var(--line);padding:23px;border-radius:10px}.card small{color:var(--muted)}.table-scroll{overflow:auto;border:1px solid var(--line);border-radius:8px;background:#fff;margin:20px 0}table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.75}th,td{padding:16px 17px;vertical-align:top;text-align:left;border-bottom:1px solid var(--line);min-width:120px}th{background:#e8eff0;font-size:13px;font-weight:750}tr:last-child td{border-bottom:0}td:first-child{font-weight:650;min-width:150px}.comparison{min-width:1040px}.cite{font-size:11px;margin:2px 2px 0 0;text-decoration:none;background:#e9f0f4;color:#31586d}.note{font-size:14px;color:var(--muted)}.warning{background:#fcf0e8;border:1px solid #ebcfbb;border-radius:8px;padding:19px 22px;color:#744a30}.priority{font-size:12px;font-weight:750;color:var(--red)}code{font-family:ui-monospace,SFMono-Regular,monospace;font-size:.88em;background:#eef2f4;border-radius:4px;padding:2px 5px}pre{overflow:auto;background:#12333f;color:#ddedef;padding:24px;border-radius:8px;line-height:1.65;font-size:12px;white-space:pre-wrap;word-break:break-word}pre code{background:none;color:inherit;padding:0}.filters{display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap}input,select,button{font:inherit;padding:10px 14px;border:1px solid #b3c9ce;border-radius:6px;background:#fff;color:var(--ink)}input[type=search]{flex:1;min-width:200px}input[type=range]{width:100%;padding:0}button{cursor:pointer;background:#087e83;color:white;border:0;font-weight:650}details{background:white;border:1px solid var(--line);border-radius:8px;margin:12px 0}summary{padding:17px 20px;cursor:pointer;display:flex;align-items:center;gap:12px;list-style:none}summary:before{content:'+';font-size:19px;color:var(--teal);font-weight:600}details[open] summary:before{content:'−'}summary .rule-title{flex:1}summary .id{color:var(--muted);font-size:12px;font-weight:700;min-width:30px}.detail-body{padding:0 24px 24px}.detail-body dl{display:grid;grid-template-columns:125px minmax(0,1fr);gap:10px;font-size:14px}.detail-body dt{font-weight:700}.detail-body dd{margin:0}.vote{font-size:12px;border-radius:5px;padding:4px 9px;white-space:nowrap}.FOR{background:#e1f3ec;color:#14604f}.AGAINST{background:#f8e7e1;color:#873c2d}.NO_RECOMMENDATION{background:#fff3d9;color:#76551b}.rule-category{font-size:12px;color:var(--muted)}.hidden{display:none!important}.demo{display:grid;grid-template-columns:1fr 1fr;gap:25px;background:#fff;border:1px solid var(--line);padding:26px;border-radius:9px}.demo label{display:block;margin:0 0 10px}.demo select{width:100%;margin-bottom:18px}.demo-output{padding:23px;border-radius:7px;background:#e9f2f1;display:flex;flex-direction:column;justify-content:center}.demo-output b{font-size:28px}.demo-output p{font-size:14px;margin-top:14px}.source{margin-bottom:20px;font-size:14px}.source h3{font-size:16px;margin-bottom:7px}.source p{margin:0 0 4px}.diagram{display:block;width:100%;height:auto;background:#fff;border:1px solid var(--line);border-radius:9px}.downloads{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0}.downloads a{background:white;border:1px solid #bed2d4;border-radius:6px;padding:9px 15px;text-decoration:none;font-size:14px}.steps{counter-reset:steps;list-style:none;padding:0}.steps li{position:relative;padding:0 0 23px 48px;border-left:2px solid #d4e7e7;margin-left:16px}.steps li:before{counter-increment:steps;content:counter(steps);position:absolute;left:-17px;top:0;background:#0b8085;color:white;width:32px;height:32px;line-height:32px;text-align:center;border-radius:50%;font-weight:700}.steps b{display:block;margin-bottom:5px}.steps li:last-child{border-left-color:transparent}.doc-footer{font-size:12px;color:var(--muted);padding-top:30px;border-top:1px solid var(--line)}@media(max-width:900px){.shell{display:block}aside{min-height:0}.side-inner{height:auto;position:relative;padding:22px 25px}.brand-sub,.side-footer{display:none}.side-inner nav{display:flex;overflow:auto;gap:20px}.side-inner nav a{white-space:nowrap;border:0;padding:8px 0}.brand{margin-bottom:13px}main{padding:32px 22px}.stats{grid-template-columns:repeat(2,1fr)}.grid,.demo{grid-template-columns:1fr}summary{flex-wrap:wrap}.rule-category{display:none}.detail-body dl{display:block}.detail-body dd{margin:6px 0 14px}h2{font-size:27px}}@media print{body{background:white;font-size:11px}aside,.filters,.demo,.downloads{display:none}.shell{display:block}main{padding:0}h1{font-size:34px}h2{font-size:22px}section{padding:20px 0}.table-scroll{overflow:visible}table{font-size:10px}th,td{min-width:0!important;padding:7px}.stats{margin:15px 0}.stat{padding:10px}.stat b{font-size:24px}.card,details,.source{break-inside:avoid}summary{padding:10px}details .detail-body{display:block!important}.comparison{min-width:0}.doc-footer{font-size:9px}a{color:inherit}.diagram{break-inside:avoid}}
'''
def page(title,nav,content,script=''):
 navhtml=''.join(f'<a href="#{id}">{label}</a>' for id,label in nav)
 return '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+'</title><meta name="description" content="OPM 의결권 가이드라인의 기관 비교, 정책 규칙, 지표와 LLM 적용 설계 검토안"><style>'+CSS+'</style></head><body><div class="shell"><aside><div class="side-inner"><div class="brand">OPEN PROXY</div><div class="brand-sub">GUIDELINE DESIGN NOTE<br>2026.09.07 · REVIEW DRAFT</div><nav>'+navhtml+'</nav><div class="side-footer">근거에서 규칙으로<br>규칙에서 설명 가능한 판단으로<br><br><a href="guideline.html">기준·비교 문서</a><br><a href="architecture.html">적용 설계 문서</a></div></div></aside><main>'+content+'<footer class="doc-footer">OPM Guideline v2 검토안 · 2026-09-07 · 운영 미적용<br>규칙·지표 정본: policy.json · SHA-256 '+sha+'<br>HTML의 규칙·지표 목록은 이 JSON에서 생성했습니다.</footer></main></div><script>'+script+'</script></body></html>'
VOTE={'FOR':'찬성 권고','AGAINST':'반대 권고','ABSTAIN':'기권','NO_RECOMMENDATION':'판단 보류'}
def pretty(e):
 if e is None:return '없음'
 if e['op']=='const':return '항상 적용' if e['value'] else '미적용'
 if e['op'] in ['all','any']:return (' AND ' if e['op']=='all' else ' OR ').join('('+pretty(x)+')' for x in e['args'])
 if e['op']=='not':return 'NOT '+pretty(e['args'][0])
 op={'eq':'=','lt':'<','lte':'≤','gt':'>','gte':'≥','in':'∈'}[e['op']]
 return M[e['metric']]['label']+' '+op+' '+json.dumps(e['value'],ensure_ascii=False)

COMPARISON=[
 ['판본·검증 범위','2026.07.02 원문·별표 확인. 위임받은 운용사의 지침 준수 조항도 확인.','파일명과 달리 본문은 2025.02.27판. 별첨 확인.','2026.01.30 개정 공지 확인. 상세 원문 재수집은 남은 과제.','현재 공개 페이지의 상세 DOC 확인. 파일명 20240425이며 본문 시행일은 미표시.','정책 시행일·수집일·조항 확인일을 각각 저장. 최신성 불명은 자동 활성화 금지.'],
 ['감사의견 비적정','적정 외 의견에 기권 또는 반대.','적정 외 의견에 반대.','저장소 기준은 반대.','적정 외 의견에 반대.','OPM 초안은 반대. 기관의 기권 선택권을 단일 AGAINST에 흡수하지 않음.'],
 ['출석률','직전 임기 이사회 75% 미만. 공시된 사유를 고려해 달리 판단 가능.','이사회·주요위원회 3/4 출석 기준.','저장소 기준: 직전 임기 이사회 75% 미만.','이사회·주요위원회 3/4 출석. 공시된 소명 예외를 포함.','대상 회의·기간·분자·분모·소명 수용 여부를 별도 필드화. 75%와 75% 미만 구별.'],
 ['독립성·겸임','최근 5년 고용관계 등과 과도 겸임 검토.','독립성·겸임·장기 재임을 역할별 판단.','개정 공지에 비사외 감사위원·감사의 장기연임 반대 근거 명시.','당사 포함 3개 이상 사외이사 겸임. 일반·금융회사 재직기간 경계가 다름.','고용 냉각기간 / 사외 재직 상한 / 동시 겸임을 분리. 모든 5년을 장기연임으로 바꾸지 않음.'],
 ['배당','재무·투자기회·자사주·기업대화 등을 종합.','과소·과다에 따른 훼손 및 정책·재무상황 검토.','저장소: 이익·재무상황·투자기회 고려.','적정배당 지지, 이사회 배당권은 조건부 검토.','일률적 50% 환원 목표 폐기. 업종과 가용자본으로 평가.'],
 ['보수','한도와 실제 지급, 경영성과를 구분. 추가 공개자료가 있으면 재량.','적자·순익감소에서 인당·총 보수한도 증액에 원칙적 반대.','저장소: 규모·성과·개별 보상자료 검토.','과도한 보수 여부와 성과 연계 검토.','성과 불일치는 정책상 반대 가능. 한도 인상률 자체는 검토 신호.'],
 ['정관 묶음·회사분할','반대 조문 포함 묶음은 전체 반대. 분할은 가치 훼손 판단.','반대 조문 포함 묶음은 전체 반대. 분할은 다면 검토.','저장소: 장기 주주가치 기준 종합 판단.','방식·공정성·이해상충·가치 검토. 묶음은 모든 개별 안건 동의 여부 확인.','실제 표결 단위로 합성. 물적분할/상장/심사 규칙을 분리.'],
 ['누가 최종 판단하는가','투자위원회·수탁자책임 전문위원회와 위임 절차.','담당 부서·위원회, 외부 자문을 받아도 회사 책임.','공식 지침·담당 조직의 의사결정 체계.','이해상충 점검·준법 협의·외부 자문 활용.','LLM은 근거 있는 검토안을 만들고 정책 승인·소명 수용·최종 결정의 책임자는 명시.'],
]
AUDIT=[
 ('P0','기관별 정책이 실제로는 충분히 실행되지 않음','기관 JSON에는 상세 조건이 있지만 선택 정책의 카테고리 default를 주로 읽고 OPM 판정을 보조한다. 같은 사실에 대한 기관별 충실한 모의판정으로 볼 수 없다.','각 기관의 조건·예외를 원자 규칙으로 만들고 단독 정책팩으로 선택. 원문이 없으면 policy_unavailable.', 'OPM-ENGINE'),
 ('P0','반대와 검토가 한 축에 섞임','_apply_policy_default는 against/review 기본값을 REVIEW로 바꾸며 기존 판정·근거를 대체할 수 있다. 이 함수의 테스트도 그 동작을 허용한다.','recommended_vote와 workflow_status를 분리. 법적 위반, 정책 반대, 자료 부족을 독립 사유로 유지.', 'OPM-ENGINE'),
 ('P0','원문 조건·예외가 추출에서 소실','국민연금 최신 출석률 조항은 공시된 소명에 따른 재량을 포함하지만 저장소의 해당 source_text는 그 예외를 담지 않는다. 미래에셋 최신 JSON의 completeness는 본문 유지라 적었으나 공식 공지는 실질 기준 개정을 알린다.','원문→정규화 조건→예외→규칙→화면의 연결을 검증. 자기보고 confidence=high를 승인 기준으로 쓰지 않음.', 'NPS26 MIR26 OPM-POLICIES'),
 ('P0','법·규정·로드맵의 혼동','현행 문서는 중복상장을 2026.07 상법상 절대금지처럼 다룬다. 금융위 최종 발표는 2026.08.03 거래소 규정·가이드라인과 예외 체계다. TCFD 2025 의무화 설명도 공식 ESG 공시 로드맵과 맞지 않는다.','해당 산문을 규범 엔진 입력에서 격리. 법률·거래소 규정·자율 정책을 분리하고 적용 시점·대상·예외를 연결.', 'OPM-DOC FSC-SPLIT FSC-ESG'),
 ('P1','정량값의 개념과 근거가 약함','5년 냉각기간·5년 장기연임, 당사 포함 3개·타사 3곳, 6년 초과·6년 이상, 신주 증가율·희석률이 혼용될 여지가 있다.','기간·분모·단위·비교 연산자를 규칙에 저장. 임계값의 기관 출처 또는 OPM 제안임을 표시.', 'OPM-DOC OPM-POLICIES'),
 ('P1','기관 다수결이 규칙의 정당성을 대신함','기존 합의 매트릭스는 조건부 문장을 for/against/silent로 압축하고, 서로 다른 조건을 같은 의견으로 묶는다. 반대율만으로 기관의 정책 품질을 단정하기도 어렵다.','같은 질문·조건·예외·기간에서만 비교. 운용사 합의는 참고 정보로 두고 OPM 규범의 이유를 별도로 설명.', 'OPM-DOC OPM-POLICIES'),
 ('P1','강한 목표가 과도한 단일 기준이 됨','50% 환원, 이사회 7명, 총수일가 후보, 물적분할, 자사주 처분에 대한 일괄 기준은 회사·업종·행위의 차이를 충분히 설명하지 못한다.','목표는 유지하되 인과관계와 반증을 요구. 실질 훼손과 보호장치로 판단하고 합리적 예외 경로를 마련.', 'OPM-DOC'),
 ('P1','검증 가능한 근거와 설계 의견이 혼재','전문가 토론 참조에 재현 불가능한 /tmp 경로가 있고, 문서 자체가 이를 토론 시뮬레이션으로 설명한다. 구현되지 않은 매트릭스와 현행 기능도 함께 남아 있다.','토론은 설계 가설로 표시. 외부 전문가의 실제 감수·독립 근거로 간주하지 않음. 미구현 항목은 capability 목록에서 분리.', 'OPM-DOC'),
]
hero='<div class="eyebrow">01 / POLICY & EVIDENCE</div><div class="meta"><span class="tag draft">검토안 · 운영 미적용</span><span class="tag">한국 상장사</span><span class="tag">2026.09.07 기준</span></div><h1>의결권 가이드라인을<br>근거가 남는 규칙으로</h1><p class="hero-intro">국민연금·주요 운용사와 비교하고, OPM 자체 기준을 다시 설계했습니다. LLM이 적용할 수 있도록 조건·정보·예외·판단 책임을 함께 정의합니다.</p><div class="lead"><strong>권고: OPM의 일반주주 보호 원칙은 유지하고, 정책·사실·권고·검토 상태를 분리합니다.</strong><br>정량 조건은 코드가 계산하고, LLM은 원문에서 사실 후보·쟁점·반증을 정리합니다. 정성 판단은 검토자의 수용을 거쳐 규칙에 연결합니다. “반대 권고”와 “사람의 검토 필요”를 동시에 표현할 수 있어야 합니다.</div>'
hero+='<div class="stats">'+''.join(f'<div class="stat"><b>{a}</b><span>{b}</span></div>' for a,b in [('34','개편 규칙'),('45','지표·정보 정의'),('4','주요 비교 기관'),('25','가상 명세 사례')])+'</div><div class="downloads"><a href="policy.json" download>기계용 JSON</a><a href="architecture.html">적용 설계·플로우차트 →</a><a href="policy.schema.json" download>JSON Schema</a></div>'
body=hero
body+=section('scope','READING GUIDE','먼저 비교의 범위를 구분합니다', '<p>핵심 비교는 국민연금, 삼성자산운용, 미래에셋자산운용, 한국투자신탁운용입니다. 규모 순위를 새로 산정한 표본은 아니며, 공개 기준의 상세도와 저장소의 비교 대상 연속성을 고려했습니다. 저장소의 삼성액티브·트러스톤·얼라인·차파트너스 등은 보조 대조에 활용했습니다.</p><div class="warning"><strong>확인 수준이 서로 다릅니다.</strong> 국민연금·삼성·한국투자신탁운용은 공개 상세 원문까지 확인했습니다. 미래에셋은 최신 개정 공지와 저장소 추출본을 대조했으며 첨부 본문 재수집이 남아 있습니다. 한국투자신탁운용의 공개 DOC 파일명은 20240425이며 본문 시행일은 표시되지 않았습니다. 또한 CB/BW 조항의 우선인수권 조건과 두 증가율 문턱이 원문 자체에서 중복되어, 해당 항목은 기관 해석 확인이 필요합니다. 따라서 아래 내용은 모든 기관의 최신 세부 조항을 완전 구현한 결과가 아닙니다.</div><p class="note" style="margin-top:15px">현행 OPM의 동작 평가는 저장소 파일과 테스트의 정적 검토입니다. 이번 문서 작업에서 운영 MCP의 기업별 결과를 새로 실측하지 않았습니다.</p>')
comp=table(['비교 항목','국민연금¹','삼성자산운용²','미래에셋자산운용³','한국투자신탁운용⁴','OPM 개편 방향'],[[H(x) for x in row] for row in COMPARISON]).replace('<table>','<table class="comparison">')
comp+='<p class="note">¹ '+refs(['NPS26'])+' 제5·10·12조 및 별표1 I-1·2, II-8, III-30·31, IV-33, VI-39. ² '+refs(['SAM25'])+' 제12조 및 별첨 I·II·III·IV·VI. ³ '+refs(['MIR26','OPM-POLICIES'])+' ⁴ '+refs(['KIM'])+'<br>“원칙적 반대”, “반대할 수 있음”, “사안별 검토”는 서로 다른 강도입니다. 이 표현과 예외를 유지해야 기관별 정책을 재현할 수 있습니다.</p>'
body+=section('compare','BENCHMARK','기관의 숫자보다 적용 조건이 중요합니다',comp)
auditrows=[[f'<span class="priority">{p}</span><br>{H(t)}',H(f),H(fix)+ '<br>'+refs(ref.split())] for p,t,f,fix,ref in AUDIT]
body+=section('audit','INDEPENDENT REVIEW','현행 OPM에서 먼저 고칠 문제', '<p>원문을 보존하는 방향, 분석 기준일 제한, 파생지표 재사용, 미구현 항목 공개는 좋은 기반입니다. 아래 문제는 그 기반 위에서 해결할 수 있습니다.</p>'+table(['우선순위·문제','확인한 내용','개편 방법'],auditrows)+'<p class="note">P0는 정책팩 활성화 전에 해결할 문제, P1은 규칙 이관과 함께 다룰 문제입니다. 모든 항목이 운영에서 발생했다고 단정한 것은 아닙니다.</p>')
body+=section('principles','POLICY CHOICES','가이드라인을 설정하는 여섯 가지 원칙','<div class="grid">'+''.join('<div class="card"><h3>'+H(t)+'</h3><p>'+H(v)+'</p></div>' for t,v in [
 ('01. 장기 가치와 공정대우','일반주주 보호를 기본 가치로 둡니다. 단기 주가 상승이나 반대율 증가를 정책의 성공으로 삼지 않습니다.'),('02. 세 종류의 규범','법률·거래소 규정, 개별 기관 정책, OPM 자체 제안을 구분합니다. 기관 비교자료가 OPM 기준에 법적 권위를 부여하지 않습니다.'),('03. 결론과 절차 상태','찬성·반대·기권은 권고의 방향입니다. 검토 필요·자료 부족·표결 없음은 처리 상태입니다. 두 축을 함께 보여줍니다.'),('04. 긍정 근거를 요구','반대 사유를 못 찾았다고 찬성하지 않습니다. 찬성 규칙·필수 증거·법규 적용·안건 관계가 검토되어야 합니다.'),('05. 같은 사실, 다른 정책','기관팩은 같은 사실 묶음에 독립 적용합니다. OPM 추가 기준을 적용할 때는 선택한 추가 기준과 우선순위를 표시합니다.'),('06. 반증을 갖는 정성 판단','“합리적”, “과도한”, “주주가치 훼손”은 추출값이 아닙니다. 구체적 질문·근거·반증·대안·검토자 확인이 필요한 판단 항목입니다.')])+'</div><p class="note" style="margin-top:18px">위 원칙 및 아래 34개 규칙은 OPM에 대한 제안입니다. 기관의 공식 입장이나 법률상 의무로 제시하지 않습니다.</p>')
rulehtml='<p>각 규칙을 열면 적용 범위·조건·예외·필요 정보가 보입니다. 목록과 JSON은 동일한 원본에서 생성되며, 출처 표시는 <strong>비교·설계 참고 근거</strong>입니다. 규칙 자체의 규범 출처는 모두 <strong>OPM 제안</strong>으로 표시했습니다.</p><div class="filters"><input type="search" id="ruleSearch" aria-label="규칙 검색" placeholder="규칙·지표·판단 내용 검색"><select id="category" aria-label="안건 유형 선택"><option value="">모든 안건 유형</option>'+''.join(f'<option value="{k}">{v}</option>' for k,v in D['categories'].items())+'</select><button id="expandRules">보이는 규칙 모두 열기</button></div><p id="ruleCount" class="note"></p>'
for r in D['rules']:
 metrics=', '.join(f'<a href="#metric-{H(x)}">{H(M[x]["label"])}</a>' for x in r['required_metrics'])
 rulehtml+=f'<details class="rule" id="rule-{r["id"]}" data-category="{r["category"]}"><summary><span class="id">{r["id"]}</span><span class="rule-title">{H(r["title"])}</span><span class="rule-category">{H(D["categories"][r["category"]])}</span><span class="vote {r["effect"]["recommended_vote"]}">{VOTE[r["effect"]["recommended_vote"]]}</span></summary><div class="detail-body"><p>{H(r["policy_intent"])}</p><dl><dt>적용 대상</dt><dd>{H(pretty(r["applies_when"]))}</dd><dt>판정 조건</dt><dd>{H(pretty(r["when"]))}</dd><dt>예외</dt><dd>{H(pretty(r["exception"]))}'+(' · 해당하면 검토로 돌리고 자동 찬성하지 않음' if r['exception'] else '')+f'</dd><dt>필요 정보</dt><dd>{metrics}</dd><dt>자료 부족 시</dt><dd>적용 대상·조건·예외의 미확정 여부를 남김. 최종 찬성을 차단하고 다음 확인 경로 반환.</dd><dt>적용 방식</dt><dd>{"정량·명시적 조건 비교" if r["evaluation_mode"]=="deterministic" else "사실 추출 + 검토자가 수용한 정성 판단 + 기계적 조건 평가"}</dd><dt>검토 질문</dt><dd>'+ '<br>'.join(H(x) for x in r['review_questions']) +'</dd><dt>비교 근거</dt><dd>'+refs(r['source_refs'])+'</dd></dl></div></details>'
support_rows=[]
for cat,support in D['category_support'].items():
 label='공통 게이트' if cat=='cross_cutting' else '찬성 경로 초안 있음' if support['positive_rule_ids'] else '검토 전용 · 자동 찬성 경로 미설계'
 support_rows.append([H(D['categories'][cat]),label,', '.join(support['positive_rule_ids']) or '없음'])
rulehtml+='<details><summary><span class="rule-title">안건 유형별 지원 범위와 찬성 경로</span></summary><div class="detail-body"><p>이 초안은 모든 유형의 완전한 판정기를 뜻하지 않습니다. 이사·감사 보수, 퇴직금, 옵션, CB/BW/EB는 반대 또는 검토 신호만 정의한 검토 전용 범위입니다. 반대가 없더라도 NO_RECOMMENDATION + needs_review로 남기며, 향후 긍정 규칙 승인 또는 근거를 남긴 사람의 별도 결정이 필요합니다. 감사위원·감사 선임은 B01~B05를 상속하므로 B05의 역할별 적합성 검토를 통해 찬성 경로가 있습니다.</p>'+table(['안건 유형','설계 범위','찬성 규칙'],support_rows)+'</div></details>'
body+=section('rules','RULE CATALOG','조건·예외·정보가 연결된 34개 규칙',rulehtml)
metrics='<p>계산값과 해석값을 분리합니다. 원문에 숫자가 있어도 그 회사·후보·기간·분모에 대응하는지 검증되지 않으면 미확정 상태입니다. 아래의 “기존 도구”는 관련 정보를 얻는 경로이며, 해당 필드의 완전한 자동 추출을 뜻하지 않습니다.</p><div class="filters"><input type="search" id="metricSearch" aria-label="지표 검색" placeholder="지표·정의·출처 검색"></div>'
for m in D['metrics']:
 metrics+=f'<details class="metric" id="metric-{m["id"]}"><summary><span class="rule-title">{H(m["label"])}</span><span class="tag">{H(m["unit"])}</span></summary><div class="detail-body"><p>{H(m["definition"])}</p><dl><dt>JSON 항목</dt><dd><code>{m["id"]}</code></dd><dt>관찰 기간</dt><dd>{H(m["period"])}</dd><dt>정보 경로</dt><dd>'+', '.join('<code>'+H(t)+'</code>' for t in m['source_tools'])+f'</dd><dt>구현 상태</dt><dd>{H(m["availability"])}</dd><dt>생성 방법</dt><dd>{H(m["method"])}</dd><dt>자료가 없으면</dt><dd>{H(m["missing_policy"])}</dd></dl></div></details>'
metrics+='<h3 style="margin-top:28px">정성 판단을 뒷받침하는 추가 계산</h3>'+table(['분석 항목','정의·주의할 분모','적용 규칙·정보 상태'],[[H(x['name']),H(x['definition']),', '.join(x['used_by'])+'<br>'+H(x['availability'])] for x in D['metric_supplements']])
metrics+='<div class="lead"><strong>출처 우선순위</strong><br>정정 관계를 반영한 해당 공시 원문 → 동일 기간의 감사된 자료·공식 지배구조보고서 → 회사의 확인 가능한 설명 → 보조 뉴스·외부 자료. 단순한 고정 순위로 충돌을 지우지 않고, 두 원문과 차이를 보존합니다. 뉴스만으로 후보 동일성이나 법 위반을 확정하지 않습니다.</div>'
body+=section('metrics','METRICS & EVIDENCE','어떤 정보를, 어떻게 사용할 것인가',metrics)
body+=section('example','INTERACTIVE EXAMPLE','출석률 한 줄에도 네 가지 상태가 있습니다','<p>가상의 재선임 후보에게 OPM 초안 B01만 적용하는 예입니다. 실제 기업 또는 국민연금의 투표를 예측한 결과가 아닙니다. 75% 이상은 이 규칙의 반대 사유에 해당하지 않을 뿐, 전체 찬성을 뜻하지 않습니다.</p><div class="demo"><div><label for="attendance">직전 임기 출석률 <strong id="attValue">62.5%</strong></label><input id="attendance" type="range" min="0" max="100" step="0.5" value="62.5"><label for="complete">임기·분모·근거 확인</label><select id="complete"><option value="yes">완료</option><option value="unknown">미확정 또는 기준일 후 공개</option></select><label for="exception">불참 사유 검토</label><select id="exception"><option value="no">예외 불수용 / 예외 없음 확인</option><option value="yes">예외 수용</option><option value="unknown">아직 확인하지 못함</option></select></div><div class="demo-output" aria-live="polite"><span class="eyebrow">B01 · 가상 사례</span><b id="demoVote"></b><p id="demoReason"></p></div></div><h3 style="margin-top:28px">한 건의 판단 기록 예시</h3><pre>'+H(json.dumps({'recommended_vote':'AGAINST','workflow_status':'ready_for_review','legal_status':'not_assessed_in_this_isolated_example','matched_rule_ids':['B01'],'facts':{'attendance':'5 / 8 = 62.5%','period':'직전 임기 전체','exception_accepted':False},'evidence_refs':['SYNTHETIC_E1'],'note':'법정 결격 판정이 아닌 OPM 정책상 반대 권고'},ensure_ascii=False,indent=2))+'</pre>')
body+=section('validation','QUALITY GATES','검증은 정답을 외부에서 가져와야 합니다','<p>이 묶음은 JSON 형식·참조 정합성과 가상 사례의 규칙 의미를 검증합니다. 작성자가 만든 가상 사례가 통과했다는 사실은 실제 공시 추출 정확도, 의결권 권고 품질, 기관 투표 예측력을 입증하지 않습니다.</p>'+table(['검증 층','대상','권고하는 통과 기준'],[
 ['이번 산출물','34개 규칙·45개 정보 항목, 25개 가상 사례, 4개 산술 사례','형식·연결·경계·누락·충돌·미래자료 차단 통과. '+ '<a href="validation.json">검증 결과</a>'],
 ['원문 추출 평가','기업·연도·문서서식별 분리 표본. 기준일 당시 원문만 사용','출처 연결·회사/후보/기간·단위 오류 별도 측정. 없는 근거로 찬성하거나 위반을 확정한 사례는 0건이어야 다음 단계.'],
 ['정책 적용 평가','승인된 기관 지침을 독립 검토자들이 적용한 판단과 비교','조항·예외 누락, 허용 재량, 규칙별 미지원율 측정. 기관 과거 투표는 행동 관측치이며 규범 정답과 분리.'],
 ['사람 판단의 일치','서로의 결론을 보지 않은 분석자 2명 + 불일치 심의','찬반과 판단 근거를 함께 대조. 전문가 토론 시뮬레이션이나 같은 LLM의 자기평가를 독립 감수라고 하지 않음.'],
 ['운영 승격 전','실제 MCP 응답으로 기존·새 판단을 shadow 비교','예: 30개사 이상, 핵심 3개 유형별 충분한 표본 확보 후 판단. 30개사는 최소 탐색 제안이지 통계적 보증이 아님. 회사/연도 holdout 유지.']
 ])+'<p class="note">평가 항목과 수치 기준은 도입 제안입니다. 포착된 위험에 따라 표본을 넓히되, 실패 사례를 데이터에서 지우고 통과율을 높이지 않습니다.</p>')
def source_html():
 out=''
 for s in D['source_registry']:
  link=f'<a href="{H(s["url"])}" target="_blank" rel="noreferrer">공식 자료 열기 ↗</a>' if s.get('url') else linkfile(s['path'],'저장소 자료 열기')
  out+=f'<div class="source" id="source-{s["id"]}"><h3>{s["id"]} · {H(s["publisher"])} · {H(s["title"])}</h3><p>{link} · 판본 {H(s.get("version") or "상세 판본 미확인")} · 확인일 {s["checked_at"]}</p><p>{H(s["verified"])} · {H(s["locator"])}</p><p class="note">{H(s.get("notes",""))}</p></div>'
 return out
body+=section('sources','SOURCE REGISTER','출처와 확인 수준',source_html())
JS='''
const rules=[...document.querySelectorAll('.rule')];
function filterRules(){const q=document.querySelector('#ruleSearch').value.toLowerCase(),cat=document.querySelector('#category').value;let n=0;rules.forEach(r=>{const visible=(!cat||r.dataset.category===cat)&&r.textContent.toLowerCase().includes(q);r.classList.toggle('hidden',!visible);if(visible)n++});document.querySelector('#ruleCount').textContent=n+' / 34개 규칙 표시'}
document.querySelector('#ruleSearch').addEventListener('input',filterRules);document.querySelector('#category').addEventListener('change',filterRules);document.querySelector('#expandRules').onclick=()=>{const visible=rules.filter(r=>!r.classList.contains('hidden'));const open=visible.some(r=>!r.open);visible.forEach(r=>r.open=open)};filterRules();
document.querySelector('#metricSearch').addEventListener('input',e=>document.querySelectorAll('.metric').forEach(m=>m.classList.toggle('hidden',!m.textContent.toLowerCase().includes(e.target.value.toLowerCase()))));
function revealHash(){const el=document.getElementById(decodeURIComponent(location.hash.slice(1)));if(el&&el.tagName==='DETAILS'){el.classList.remove('hidden');el.open=true;el.scrollIntoView()}}window.addEventListener('hashchange',revealHash);revealHash();
const B01=__B01__;
function demo(){let v=+document.querySelector('#attendance').value,c=document.querySelector('#complete').value,x=document.querySelector('#exception').value;document.querySelector('#attValue').textContent=v+'%';let vote,reason;if(c==='unknown'){vote='자료 부족';reason='출석률의 임기·분모·공개시점을 확인하기 전에는 규칙을 확정 적용하지 않습니다.'}else if(v>=B01.when.args[1].value){vote='이 규칙은 미해당';reason='75% 미만이라는 반대 조건이 충족되지 않습니다. 다른 규칙을 검토해야 하므로 전체 찬성은 아닙니다.'}else if(x==='unknown'){vote='소명 확인 필요';reason='낮은 출석률은 확인했지만 예외 판단이 미완료입니다. 필요한 공시와 소명을 요청합니다.'}else if(x==='yes'){vote='예외 검토';reason='소명을 수용해 이 규칙의 자동 반대 적용을 보류합니다. 다른 규칙까지 찬성으로 바뀌지는 않습니다.'}else{vote='반대 권고';reason='OPM 출석률 정책의 반대 조건이 충족됩니다. 법적 결격 판정과 구분해 검토자에게 근거를 전달합니다.'}document.querySelector('#demoVote').textContent=vote;document.querySelector('#demoReason').textContent=reason}
['attendance','complete','exception'].forEach(id=>document.getElementById(id).addEventListener('input',demo));demo();
'''.replace('__B01__',json.dumps(R['B01'],ensure_ascii=False))
(P/'guideline.html').write_text(page('OPM 의결권 가이드라인 개편안', [('scope','01 범위·읽는 법'),('compare','02 기관 비교'),('audit','03 현행 점검'),('principles','04 설정 원칙'),('rules','05 규칙 34개'),('metrics','06 지표·정보 45개'),('example','07 적용 예시'),('validation','08 검증 계획'),('sources','09 출처')],body,JS))

# Standalone vector diagram. Coordinates are fixed; no external font, CDN or renderer is needed.
svg=['<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1120" viewBox="0 0 1600 1120" role="img" aria-labelledby="t d"><title id="t">OPM 가이드라인의 정책 준비와 안건 적용 흐름</title><desc id="d">정책 원문을 검토하여 승인한 규칙팩을 준비한다. 실행 시 대상과 시점을 고정하고 OPM 원문 자료를 수집한다. LLM은 사실 후보와 반증을 추출한다. 검증 후 규칙을 평가하고 사람이 검토하여 권고와 근거를 반환한다. 누락 자료는 제한된 횟수로 보완한다.</desc><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 Z" fill="#47858a"/></marker><marker id="soft" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 Z" fill="#a97736"/></marker></defs><rect width="1600" height="1120" rx="18" fill="#f7f9f8"/><style>text{font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif}.title{font-weight:750;font-size:32px;fill:#143541}.sub{font-size:17px;fill:#546f77}.lane{font-size:15px;font-weight:700;letter-spacing:1px;fill:#31747b}.nt{font-size:20px;font-weight:700;fill:#153743}.nb{font-size:16px;fill:#496772}.tiny{font-size:13px;fill:#5a757b}.edge{fill:none;stroke:#47858a;stroke-width:2.6;marker-end:url(#arrow)}.retry{fill:none;stroke:#a97736;stroke-width:2.2;stroke-dasharray:7 5;marker-end:url(#soft)}</style><text class="title" x="60" y="65">정책은 승인하고, 사실은 검증하고, 판단 근거는 남깁니다</text><text class="sub" x="60" y="100">설계 제안 · 기존 OPM 원문/지표 활용 · LLM 추출 후보와 승인된 판단을 분리</text><rect x="35" y="135" width="1530" height="235" rx="15" fill="#eaf1f0"/><text class="lane" x="60" y="172">A. 정책 준비  /  원문이 바뀔 때 수행</text>']
def node(x,y,w,h,no,title,lines,fill='#fff'):
 svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="#b9d0d2"/><text class="tiny" x="{x+20}" y="{y+25}">{H(no)}</text><text class="nt" x="{x+20}" y="{y+55}">{H(title)}</text>')
 for i,line in enumerate(lines):svg.append(f'<text class="nb" x="{x+20}" y="{y+85+i*25}">{H(line)}</text>')
node(60,198,320,140,'SOURCE','공식 정책·법규 원문',['기관·판본·조항·시행일','경과조치·대상·원문 위치'])
node(435,198,320,140,'DRAFT','LLM이 규칙 초안 작성',['조건·예외·모호한 표현 분해','근거 없는 문턱은 새로 만들지 않음'])
node(810,198,320,140,'APPROVAL','정책 담당자·법률 검토',['원문 충실성·예외·적용대상 확인','JSON 검사 + 명세 사례 검증'])
node(1185,198,320,140,'VERSION','승인된 규칙팩 고정',['정책 버전·해시·승인 기록','HTML과 실행 규칙 동시 생성'],'#d9eeea')
for x in [380,755,1130]:svg.append(f'<path class="edge" d="M{x} 268 H{x+48}"/>')
svg.append('<text class="lane" x="60" y="426">B. 안건 적용  /  분석 요청마다 수행</text>')
node(60,455,320,160,'01 · REQUEST','대상·정책·시점 고정',['회사·주총·실제 표결 단위','기관팩 또는 OPM팩 선택','기준일·관찰기간·안건 관계'])
node(435,455,320,160,'02 · OPM','원문·기존 지표 수집',['DART 공고·보고서·정관','재무·후보·배당·보수·거래','값이 없으면 위치·다음 경로'])
node(810,455,320,160,'03 · LLM','사실 후보와 쟁점 추출',['원문 인용·회사/후보/기간','정성 판단의 근거·반증·소명','계산·예외 승인은 맡기지 않음'])
node(1185,455,320,160,'04 · VALIDATE','사실·판단의 검증',['단위·분모·시점·원문 일치','정성 판단은 검토자가 수용','미확인은 false로 변환 금지'])
for x in [380,755,1130]:svg.append(f'<path class="edge" d="M{x} 535 H{x+48}"/>')
svg.append('<path class="retry" d="M1345 615 V672 H595 V622"/><rect x="736" y="651" width="470" height="40" rx="6" fill="#fff2dd"/><text class="tiny" x="753" y="676">누락·충돌 → 필요한 원문만 최대 2회 보완 → 미해결은 검토</text>')
node(1185,752,320,175,'05 · RULE ENGINE','조건 평가·안건 합성',['참 / 거짓 / 미확정','법규 → 정책 → 예외 → 묶음','반대 근거와 미해결 항목 보존'],'#d9eeea')
node(810,752,320,175,'06 · HUMAN','검토와 최종 결정',['정책상 권고·소명·반증 확인','재량 선택·변경 이유 기록','책임 있는 승인 주체 명시'])
node(435,752,320,175,'07 · RESPONSE','권고 + 상태 + 근거',['찬성·반대·기권·판단 보류','검토 필요·자료 부족·표결 없음','사용자에게 원문까지 연결'])
node(60,752,320,175,'08 · AUDIT','재현·평가·정책 개선',['버전·사실·규칙·변경 이력','사용자 결과는 서버 기본 저장 X','승인된 외부 저장소/사용자 내보내기'])
svg.append('<path class="edge" d="M1505 535 H1537 V730 H1345 V746"/>')
for x in [1185,810,435]:svg.append(f'<path class="edge" d="M{x} 835 H{x-48}"/>')
svg.append('<path class="edge" d="M1505 268 H1554 V959 H1345 V934"/><text class="tiny" x="1108" y="981">규칙팩은 실행 중 임의 수정하지 않습니다</text>')
svg.append('<rect x="60" y="1015" width="1445" height="62" rx="10" fill="#123644"/><text x="85" y="1053" font-size="18" fill="#eff8f7">표결 없음은 즉시 종료 · 정책팩이 없으면 대체 적용 금지 · 검증 실패는 자료 부족으로 보존 · 실제 투표 전송은 별도 업무</text></svg>')
SVG=''.join(svg)
(P/'flowchart.svg').write_text(SVG)
MERMAID='''flowchart TB
 subgraph prepare["정책 준비: 원문 개정 시"]
  P1["공식 정책·법규 원문"] --> P2["LLM: 조건·예외 초안"]
  P2 --> P3["정책 담당자·법률 검토 + 명세 검사"]
  P3 --> P4["승인 규칙팩 / 버전·해시 / HTML 생성"]
 end
 subgraph runtime["안건 적용: 분석 요청마다"]
  A["회사·주총·안건·기관팩·기준일 고정"] --> G{"표결 대상인가?"}
  G -->|아니오| N["표결 없음 반환"]
  G -->|예| K{"해당 정책팩 사용 가능?"}
  K -->|아니오| U["정책 미확인 반환"]
  K -->|예| B["OPM: 원문·검증된 지표 수집"]
  B --> C["LLM: 사실 후보·쟁점·소명·반증"]
  C --> V["검증: 인물·기간·단위·원문 / 정성 판단 수용"]
  V -->|확인| E["3값 규칙 평가 + 법규·정책·예외 + 실제 표결 단위 합성"]
  V -->|누락·충돌: 최대 2회| B
  V -->|보완 한도 소진| Q["자료 부족·검토 필요 보존"]
  E --> H["사람: 권고·소명·반증 검토 / 최종 판단"]
  Q --> H
  H --> O["권고 + 처리상태 + 근거 + 부족 자료 + 변경 이력"]
  O --> T["사용자 내보내기 / 승인된 외부 저장소 / 재현 평가"]
 end
 P4 --> E
'''
(P/'flowchart.mmd').write_text(MERMAID)

arch='<div class="eyebrow">02 / APPLICATION ARCHITECTURE</div><div class="meta"><span class="tag draft">설계 제안 · 미구현</span><span class="tag">기존 OPM 위에 단계적 적용</span></div><h1>LLM이 읽고,<br>규칙이 일관되게 적용되게</h1><p class="hero-intro">새 파서를 계속 늘리기 전에 원문·지표·규칙·검토의 연결을 만듭니다. 데이터 제공 서버라는 OPM의 역할을 유지하면서 판단 과정을 재현 가능하게 설계합니다.</p><div class="lead"><strong>기본 구조는 증거 제공 서버 + 정책팩 + 호출하는 LLM·검토자입니다.</strong><br>OPM은 검증된 숫자와 원문 위치를 제공합니다. LLM 호출은 우선 클라이언트 측에 두고, 사실 후보의 수용과 정책 적용을 명시적 계약으로 연결합니다. 서버 내 LLM 도입은 운영비·접근권·저장 정책을 검토한 후 별도 선택할 수 있습니다.</div><div class="downloads"><a href="guideline.html">← 기준·비교 문서</a><a href="flowchart.svg" download>플로우차트 SVG</a><a href="flowchart.mmd" download>Mermaid 원본</a><a href="policy.json" download>정책 JSON</a><a href="examples.json" download>가상 사례 JSON</a></div>'
arch+=section('flow','OVERVIEW','정책 준비와 안건 적용을 분리합니다','<img class="diagram" src="flowchart.svg" alt="정책 준비와 안건 적용의 전체 플로우차트"><p class="note" style="margin-top:12px">그림의 규칙팩 승인·검증·사람 검토는 제안된 프로세스입니다. 현재 이 기능이 운영에 구현되었다는 뜻이 아닙니다. 도식은 벡터 파일로 확대하거나 재사용할 수 있습니다.</p>')
arch+=section('layers','RESPONSIBILITY','각 구성요소의 책임',table(['구성요소','담당하는 일','명확히 남길 출력'],[
 ['정책 원문 저장·등록','기관명·원문 링크·원문 해시·공개일·시행일·개정/폐지 관계·적용범위 관리','source_id, source_version, clause_locator, effective interval, verification status'],
 ['정책 정규화','문장 → 적용 대상 → 조건 → 예외 → 권고/허용재량 → 필요한 증거. LLM 초안 뒤 담당자 확인','승인된 policy pack. 미확인 항목은 disabled/unverified. 모든 문장을 자동 실행 가능하다고 간주하지 않음.'],
 ['OPM 데이터 계층','기존 도구로 원문과 검증된 파생지표 공급. 분모·시점·회계범위와 공고-안건-후보 연결','EvidenceBundle + 후보 값 + missing_information + next_retrieval'],
 ['클라이언트 LLM','쟁점 분해·사실 후보·정관 전후 비교·회사 소명·반대 근거·추가 조회 제안','구조화된 FactProposal/AssessmentProposal. 원문 위치가 없는 결론은 수용 대상에서 제외.'],
 ['검증·수용 계층','타입·단위·원문 인용 일치·회사/후보/기간·정정 관계 검사. 정성 판단은 사람 수용','verified / unknown / conflicting / stale / invalid / not_applicable. confidence는 보조 정보.'],
 ['결정 엔진','숫자 계산, 고정 규칙 평가, 예외·충돌·묶음·조건부 관계 합성','RuleTrace + recommended_vote + workflow_status + legal_status. 정책의 기본값으로 근거 덮어쓰기 금지.'],
 ['검토·설명 계층','사람이 재량과 최종 결론 결정. LLM은 검증된 추적만 설명으로 렌더링','근거·반증·부족 자료·사용 규칙·변경 사유. 실제 투표 명령과 분리.'],
 ]))
arch+=section('evidence','DATA CONTRACT','최소한의 증거 계약을 먼저 만듭니다','<p>숫자 하나에도 “무엇을 언제 어떻게 읽었나”가 붙습니다. 소집공고에 75%라고 적혀 있더라도 회사 평균인지, 후보 개인의 임기 전체인지 모르면 B01의 입력으로 사용할 수 없습니다.</p><pre>'+H(json.dumps({'metric_id':'director.attendance_pct','value':62.5,'status':'verified','unit':'percent','entity_id':'SYNTHETIC_CO / SYNTHETIC_CANDIDATE','agenda_id':'SYNTHETIC_AGENDA','period_start':'2023-03-01','period_end':'2026-02-28','public_at':'2026-03-01T10:00:00+09:00','evidence_refs':['E1'], 'accepted_by':'reviewer-id','derivation':{'attended_count':5,'eligible_count':8,'formula':'attended_count / eligible_count * 100','source_refs':['E1']}},ensure_ascii=False,indent=2))+'</pre><p>실제 EvidenceBundle의 E1에는 접수번호, 문서 URL, 조항/표/행 위치, 원문 인용, 인용 범위 해시, 정정 관계, 공시 공개시점이 들어갑니다. 위 예시의 회사·후보·문서 ID는 모두 가상입니다.</p><div class="grid"><div class="card"><h3>없음과 모름</h3><p>회사에 소명이 없다고 확인한 경우와 검색에서 못 찾은 경우를 구분합니다. 결격 “해당 없음”이라는 회사의 자체 기재도 법적 결격이 없다는 독립 검증과 구별합니다.</p></div><div class="card"><h3>동일성과 시간</h3><p>인물은 회사·경력·임기로 식별합니다. 과거 주총을 재현할 때 후속 사업보고서와 주총 결과를 입력에서 차단합니다. 정책 개정일과 적용 사건일도 별도로 고정합니다.</p></div></div><p class="note" style="margin-top:15px">동봉 evidence.schema.json은 값의 기본 형식 계약입니다. 후보 식별 그래프·정정 계보·접수번호 검증·필드별 회계범위 검증까지 구현한 것은 아닙니다.</p>')
arch+=section('acceptance','REVIEW CONTRACT','정성 판단을 누가 수용하는가','<p>아래 역할과 권한은 도입 제안입니다. 실제 기관의 권한 규정에 맞춰 담당자를 지정해야 합니다. 사실 수용, 정성 판단 수용, 최종 의결권 결정은 서로 다른 승인입니다.</p>'+table(['판단','담당 역할','수용 기준','불일치 처리'],[[H(x['decision']),H(x['owner']),H(x['acceptance']),H(x['escalation'])] for x in D['review_authority']])+'<details><summary><span class="rule-title">보호장치 충분성의 수용 기록 예시 · 가상 사례</span></summary><div class="detail-body"><p>일반주주 보호장치는 제도의 명칭보다 권한·독립성·정보 접근·실제 행사 가능성으로 검토합니다. 아래 기록은 결론의 근거와 반증 처리 방법을 보여주는 제안이며, 기업 분석 결과가 아닙니다.</p><pre>'+H(json.dumps(D['assessment_example'],ensure_ascii=False,indent=2))+'</pre></div></details>')
arch+=section('semantics','EVALUATION SEMANTICS','찬반을 덮어쓰지 않고 근거를 합성합니다','<ol class="steps"><li><b>표결 단위와 정책부터 고정</b>철회·보고사항은 표결 없음으로 종료합니다. 지정 기관팩이 없거나 미승인이면 정책 미확인 상태로 반환합니다. 자동으로 OPM 기준을 대입하지 않습니다.</li><li><b>규칙의 적용 대상부터 판별</b>새 이사에게 직전 임기 출석률을 요구하지 않습니다. 소각 안건에는 처분 상대방 자료를 요구하지 않습니다. 적용 대상 자체가 불명확하면 검토에 남깁니다.</li><li><b>조건은 참·거짓·미확정으로 계산</b>정보 누락·타입 오류·미래자료·출처 충돌은 미확정입니다. 논리합의 확정 true, 논리곱의 확정 false는 보존하되 카테고리의 필수 증거 완결성은 별도 검사합니다.</li><li><b>예외는 조건 뒤에 평가</b>반대 조건을 충족했어도 소명이 받아들여지면 예외 검토로 돌립니다. 해당 규칙의 소명이 불명확하면 그 규칙은 반대 표를 만들지 않고 자료 부족으로 남깁니다. 예외 수용을 자동 찬성으로 뒤집지 않습니다.</li><li><b>반대 근거와 미해결 질문을 함께 반환</b>조건과 해당 예외까지 확인한 반대 근거가 있으면 다른 규칙의 정보 누락으로 지우지 않습니다. 찬성은 모든 필수 규칙·법규·원문·안건 관계를 확인했을 때만 가능합니다. 법 준수는 다른 정책상 문제를 지우지 않습니다.</li><li><b>실제 표결 단위에서 마무리</b>분석상 조문/후보를 분리해도 실제로 한 표인 묶음은 하나의 결론으로 합성합니다. 경합안은 동시에 찬성시키지 않고, 조건부 안건은 선행 결과별 시나리오로 표현합니다. 집중투표는 의결권 배분이라는 별도 문제입니다.</li></ol>'+table(['권고 방향','처리 상태 예','의미'],[
 ['AGAINST','ready_for_review','반대 근거가 정리되어 검토자에게 전달 가능. 실제 투표 승인·전송을 뜻하지 않음.'],
 ['AGAINST','needs_review','예외까지 확인한 한 규칙의 반대를 보존하면서 다른 규칙의 누락·반증·충돌을 추가 검토.'],
 ['NO_RECOMMENDATION','insufficient_evidence','필요한 원문·임기·지표가 미확정. 기권과 다름.'],
 ['NO_RECOMMENDATION','not_votable','철회 또는 보고사항. 찬반 생성 금지.'],
 ['NO_RECOMMENDATION','policy_unavailable','선택한 기관의 올바른 정책팩이 없음. 다른 기관 정책으로 대신하지 않음.'],
 ['ABSTAIN','needs_review / ready_for_review','기관 지침이 허용하는 기권 선택. 중립투표·미행사·REVIEW와 구분.'],
 ]))
arch+=section('edgecases','EXPLICIT OUTCOMES','모호하기 쉬운 경우의 기대 출력',table(['입력·범위','규칙 처리','권고 + 상태'],[
 ['B01 단독: 출석률 62.5%, 불참 소명 미확인','본체 조건은 true, 예외는 unknown. B01은 incomplete이며 반대 표는 아직 없음.','NO_RECOMMENDATION + insufficient_evidence'],
 ['위 B01 + B04의 별도 확정 반대','B01의 미해결 사유는 유지하고 B04에서 나온 반대만 보존.','AGAINST + needs_review'],
 ['적자 배당: 비율 비적용 근거 확인, 재원 충분, 과소배당 훼손 없음, 정책 부합','payout_comparable=false가 D01의 비율 가지를 배제. 재원 판단은 계속하며 D02·D03도 평가. 나머지 필수 게이트 완료 전제.','D01~D03 범위: FOR + ready_for_review'],
 ['비율의 not_applicable만 있고 적용 여부 근거는 미확인','숫자 null을 0이나 false로 바꾸지 않음. D01 미확정이며 찬성 차단.','NO_RECOMMENDATION + insufficient_evidence'],
 ['감사 후보: 최근 5년 감사 직무 이력 부재가 확인됨','U02 not_applicable. 현재 회사 신임이어도 타사 감사 경력이 있으면 이 경로를 쓰지 않음.','U02 단독: NO_RECOMMENDATION + needs_review; 다른 선임 규칙 계속'],
 ])+'<p class="note">위 표의 찬성은 범위가 명시된 가상 규칙 결과입니다. 표결 가능 여부·기관팩·법규·안건 관계가 추가로 완료되어야 전체 권고로 쓸 수 있습니다. ready_for_review는 검토 자료 준비 상태이며 실제 투표 승인을 뜻하지 않습니다.</p>')
arch+=section('integration','OPM INTEGRATION','현행 구조에는 이렇게 연결합니다',table(['현행 자산','재사용 방법','추가할 계약'],[
 [linkfile('open_proxy_mcp/services/proxy_advise.py','proxy_advise 서비스'),'주총·기준일·안건 관계 조정과 기존 upstream 수집을 유지. _decide_*와 신규 규칙을 처음에는 나란히 실행.','신규 RuleTrace를 별도 응답 필드로 shadow 노출. 내부 정책 ID를 공개 응답에 그대로 노출하는 문제는 기존 공개 규약에 맞게 처리.'],
 [linkfile('open_proxy_mcp/data/guideline/guideline_thresholds.json','수치 임계값 대장'),'정책 숫자를 가져오되 의미·단위·연산자·예외를 함께 이관.','임계값만 바꾸고 함수 분기가 남는 상태를 끝냄. 모든 수치는 기관 근거 또는 OPM 제안으로 표기.'],
 [linkfile('open_proxy_mcp/data/laws/law_provisions.json','법령 개정 대장'),'effective_date, obligation_date, first_agm_trigger, scope를 그대로 참조.','법규 원문 감수와 시행일·의무 강제일·적용 사건일의 판정. 산문 문서의 날짜를 별도 정본으로 삼지 않음.'],
 [linkfile('open_proxy_mcp/services/director_board.py','이사회·개별 이사 자료'),'출석률·보수·재직 변동·겸직 원문 활용.','후보 식별, 직전 임기 합성, 실제 회수 분자·분모, 소명 연결. 소집공고 출석표 우선 경로 추가.'],
 [linkfile('wiki/tools/README.md','OPM 도구 카탈로그'),'financial_metrics, dividend_disclosure/data, corp_gov_report, treasury_share, corporate_restructuring, dilutive_issuance 등 재사용.','지표 재계산 중복 금지. 검증된 회사·기간·통화·회계범위에 대한 normalized facts adapter.'],
 [linkfile('open_proxy_mcp/tools/proxy_guideline.py','proxy_guideline 도구'),'승인된 정책팩으로 사람이 읽는 정책 문서를 생성해 제공.','문서 헤딩·라벨만 맞추는 테스트에서 규칙 ID·조건·예외·판정 추적의 정합 검사로 확대.'],
 ] )+'<div class="warning"><strong>이번 변경은 output 폴더의 검토 산출물에 한정합니다.</strong> 현행 운영 정책·함수·raw 원문은 바꾸지 않았습니다. 새 MCP 도구명과 저장 방식은 확정하지 않았으며, 구현할 때 공개 카탈로그·위키·회귀 검증 절차를 함께 적용해야 합니다.</div>')
arch+=section('migration','ROLLOUT','처음에는 세 가지 안건으로 작게 검증합니다',table(['단계','산출물','다음 단계로 넘어갈 조건'],[
 ['0 · 원문 복원','국민연금 2026.07.02 및 기관별 최신 판본 확정. 누락된 예외와 개정사항 대조. OPM의 미검증 법·통계 문장 격리.','모든 활성 규칙의 원문·판본·조항·예외·검토자 확인. 전체 자료가 없는 기관은 비교만 가능.'],
 ['1 · 첫 적용','재무제표 감사의견, 이사 출석률, 보수한도/성과 연계. 동일 사실에 기존·신규 출력을 함께 생성.','단위·시점·신임/재선임·소명 경계 검사와 실제 MCP 표본 검토. 확인되지 않은 법 위반/찬성 생성이 없어야 함.'],
 ['2 · 구조적 관계','정관 조문 단위, 묶음·조건부·경합, 법규 경과조치, 선택한 단일 기관 정책팩.','개별 분석 결과와 실제 표결 단위의 대응 확인. 기관정책의 기본값으로 다른 근거를 덮는 사례 제거.'],
 ['3 · 정성 안건','합병·분할·자기주식·CB/BW/EB. 독립성·가치평가·자금소요 판단 계약 확대.','LLM의 정성 제안과 사람의 수용 기록, 반증·미지원 항목 모두 표시.'],
 ['4 · 선택적 운영','승인된 카테고리만 활성화. 정책·모델·프롬프트·증거 버전 고정.','동일 입력 재현, 버전 변경 영향 보고, 이전 정책팩 복귀 가능. 성능이 나쁜 영역은 계속 검토 전용.'],
 ])+'<p><strong>가장 먼저 구현할 기능:</strong> <code>EvidenceBundle → RuleTrace</code> 연결을 감사의견·출석률·보수에서 검증하십시오. 34개 규칙을 한 번에 운영으로 옮기는 것보다, 잘못 연결된 원문·미래자료·소명 누락을 발견할 수 있는 이 경로가 먼저입니다.</p>')
arch+=section('evaluation','INDEPENDENT VALIDATION','규칙 검증과 판단 품질 평가는 다릅니다','<p>모델은 추출·해석을 제안하고, 검토자는 원문에서 독립적으로 판단합니다. 데이터 설계자와 채점자가 같은 가상 사례를 만들면 통과율은 명세의 일관성만 보여줍니다. 따라서 실제 성능평가에는 다른 회사·연도·서식의 원문 표본과 독립 검토 결과가 필요합니다.</p>'+table(['위험','독립성을 확보하는 방법'],[
 ['과거 투표의 사후 정보 누출','기업 공시의 공개시점을 기준일로 차단하고, 실제 기관 투표와 사후 주총 결과는 평가 라벨로만 격리.'],
 ['설계자가 만든 답을 스스로 채점','외부 검토자들이 출처를 보고 독립 판단. 정책 재량이 허용되면 단일 정답 대신 허용 가능한 결론 집합과 사유로 평가.'],
 ['기관 투표 따라하기를 규범 품질로 오인','기관 행동 예측 성능과 해당 지침에 대한 충실성을 별도 과제로 측정. 의사결정에 반영된 비공개 정보는 공개자료만으로 알 수 없다고 표시.'],
 ['오류를 REVIEW로 숨기기','판정 커버리지, 규칙 미지원율, 자료 부족률, 반대/찬성 정확성, 인용 정확성, 검토자의 수정 사유를 함께 보고.'],
 ['같은 모델의 자기검증','LLM 간 일치도는 보조 진단. 외부 원문·규칙 명세·독립 검토자 없이 사실의 정답으로 사용하지 않음.'],
 ])+'<p class="note">동봉 validate_package.py는 교육용 명세 검사기입니다. 정책팩 승인·법적 적용 판별·실제 인용 검증·안건 그래프·기관별 전체 평가기는 아직 구현하지 않았습니다. 25개 사례는 규칙 단위의 경계와 오류 처리를 보여줍니다.</p>')
arch+=section('artifacts','DELIVERABLES','검토 및 구현 인계 파일',table(['파일','내용'],[
 ['<a href="guideline.html">guideline.html</a>','기관 비교, 독립 점검, 규칙 34개, 지표 45개, 인터랙티브 출석률 예시'],
 ['<a href="policy.json">policy.json</a>','규칙·지표·출처·적용 계약·이관 순서. 모든 규칙은 초안이며 runtime_enabled=false'],
 ['<a href="policy.schema.json">policy.schema.json</a> / <a href="evidence.schema.json">evidence.schema.json</a>','규칙 표현식과 사실 값의 형식 계약. 의미 검증은 별도 필요'],
 ['<a href="flowchart.svg">flowchart.svg</a> / <a href="flowchart.mmd">flowchart.mmd</a>','확대 가능한 플로우차트와 편집 가능한 Mermaid 원본'],
 ['<a href="examples.json">examples.json</a> / <a href="validation.json">validation.json</a>','가상 사례 입력·기대 출력 및 실제 명세 검사 결과'],
 ['<a href="README.md">README.md</a>','검토 범위·생성 방법·남은 검증 과제'],
 ]))
arch+=section('sources','REFERENCES','설계의 근거와 제약',source_html())
(P/'architecture.html').write_text(page('OPM 가이드라인 적용 설계', [('flow','01 전체 흐름'),('layers','02 구성요소 책임'),('evidence','03 증거 계약'),('acceptance','04 판단 수용'),('semantics','05 판정 의미'),('edgecases','06 경계 사례'),('integration','07 현행 OPM 연결'),('migration','08 이관 순서'),('evaluation','09 독립 검증'),('artifacts','10 인계 파일'),('sources','11 근거')],arch))
print('Rendered guideline.html, architecture.html, flowchart.svg and flowchart.mmd')
