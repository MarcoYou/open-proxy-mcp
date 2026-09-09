"""Build the offline reading edition from the canonical guideline decision page.

Run: uv run --with markdown-it-py python scripts/render_guideline_spec.py
Only documentation exports are written; no user queries or assessments are saved.
"""
from pathlib import Path
import hashlib
import html
import json
import re
from urllib.parse import unquote, urlsplit
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from open_proxy_mcp.services.guideline_assessment import GuidelineAssessment

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'wiki/decisions/260908_1200_decision_guideline-v2-final-redesign-pilot.md'
OUT = ROOT / 'output/guideline-specification-20260909'
POLICY = ROOT / 'open_proxy_mcp/data/guideline/opm-guideline-v2-pilot.json'
OUT.mkdir(parents=True, exist_ok=True)
h = html.escape


def diagram(kind):
    height = 610 if kind == 'runtime' else 410
    title = '현재 MCP의 원문·평가·판정 흐름' if kind == 'runtime' else '자료와 정책의 갱신 경로 — 목표 설계'
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1100 {height}" role="img" aria-labelledby="{kind}-title"><title id="{kind}-title">{title}</title>',
             '<defs><marker id="arr-'+kind+'" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto"><path d="M0 1L8 5L0 9" fill="none" stroke="#53756c" stroke-width="1.5"/></marker></defs>',
             '<rect width="1100" height="'+str(height)+'" rx="20" fill="#f0f4ef"/>',
             '<style>text{font-family:Arial,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;fill:#17372e}.t{font-size:21px;font-weight:700}.s{font-size:16px}.n{font-size:13px;fill:#557b6e}</style>']
    def box(x,y,num,title,sub,proposed=False):
        parts.append(f'<rect x="{x}" y="{y}" width="290" height="118" rx="12" fill="white" stroke="#acc5b9"'+(' stroke-dasharray="6 4"' if proposed else '')+'/>')
        for yy,txt,cl in [(y+25,num,'n'),(y+55,title,'t'),(y+84,sub[0],'s'),(y+106,sub[1],'s')]:
            parts.append(f'<text x="{x+18}" y="{yy}" class="{cl}">{h(txt)}</text>')
    def line(d):parts.append(f'<path d="{d}" fill="none" stroke="#53756c" stroke-width="2" marker-end="url(#arr-{kind})"/>')
    parts.append(f'<text x="35" y="40" class="t">{title}</text>')
    if kind=='runtime':
        boxes=[(35,65,'01 · REQUEST','회차·기준일 고정',['회사 · 회의 · 후보 범위','사후 자료와 대상 회차 결과 제외']),
               (405,65,'02 · EVIDENCE','공개 원문 수집',['공고 · 사업보고서 이사회 절','지정 5건 + 임원 변동 탐색 5건']),
               (775,65,'03 · TASK','평가 과업 반환',['원문 발췌 · 정책 · 출력 스키마','대상과 내용에 연결된 task_id']),
               (775,240,'04 · CALLER LLM','원문 읽기·평가 제출',['선임구분 · 독립성 · 반증','기사 논조 제외 / 사람 미검토']),
               (405,240,'05 · VALIDATE','과업·인용 연결 검사',['타입 · 현재 과업 · 원문 인용','의미 정확성 인증과는 별개']),
               (35,240,'06 · METRICS','수용 평가를 지표로',['미수용은 unknown','미공개 관계 세부는 후속 확인']),
               (35,415,'07 · RULES','규칙별 상태와 권고',['반대 신호 보존 · 긍정 게이트','재직·정지 구간과 회의 수 계산']),
               (405,415,'08 · CONSTRAINTS','기존 제약 적용',['법령 · 기존 판정 · 표결 관계','부모/자식 안건 · 좌석 제한']),
               (775,415,'09 · RESULT','최종 권고와 근거',['FOR / AGAINST / REVIEW 등','별도 후보 권고·미확인·검토 표시'])]
        for args in boxes:box(*args)
        for d in ['M325 124H405','M695 124H775','M920 183V240','M775 299H695','M405 299H325','M180 358V415','M325 474H405','M695 474H775']:line(d)
        parts.append('<text x="35" y="579" class="s">연결 검사 실패 → 평가 거절·미사용. 추가 자료나 정책이 바뀌면 새 과업으로 재평가.</text>')
    else:
        box(35,75,'DATA CHANGE','공시·원문 변경',['신규 · 정정 · 철회','시점과 원문 계보 확인'],True)
        box(405,75,'IMPACT','영향받는 과업 선택',['원천 → 사실 → 규칙 연결','같은 정책으로 필요한 부분 재평가'],True)
        box(775,75,'NEW RUN','새 분석 결과',['근거·정책 버전 명시','과거 기준일에 미래 자료 혼입 금지'],True)
        box(35,240,'POLICY CHANGE','정책 의미 변경',['기관 정책 · 법령 · 고객 설정','변경 후보 작성'],True)
        box(405,240,'REVIEW','컴파일·영향 평가·승인',['타입 · 단위 · 권한 · 충돌','승인된 범위만 활성화'],True)
        box(775,240,'ACTIVATE / RESTORE','새 버전 활성화·복구',['불변 버전과 활성 버전 분리','오류 시 검증된 이전 버전 사용'],True)
        for y in [134,299]:
            line(f'M325 {y}H405');line(f'M695 {y}H775')
        parts.append('<text x="35" y="390" class="s">점선은 미구현 목표 설계. 현재는 재호출 시 과업의 변경 여부를 대조한다.</text>')
    parts.append('</svg>')
    return ''.join(parts)

raw = SOURCE.read_text()
body = re.sub(r'^---\n.*?\n---\n', '', raw, count=1, flags=re.S)
body = re.sub(r'^\s*# .+\n', '', body, count=1)
# The authored Mermaid is represented by the offline runtime SVG, no CDN needed.
body = re.sub(r'```mermaid\n.*?```', '흐름은 앞의 「판정 로직」 도식과 함께 읽는다.', body, flags=re.S)
# Resolve wiki references against actual repository pages.
by_stem = {p.stem:p for p in (ROOT/'wiki').rglob('*.md') if p.is_file()}
def wikilink(m):
    key=m[1];target=by_stem.get(key.split('/')[-1]);label=key.split('/')[-1]
    return f'[{label}](../../{target.relative_to(ROOT).as_posix()})' if target else label
body=re.sub(r'\[\[([^\]]+)\]\]',wikilink,body)
soup=BeautifulSoup(MarkdownIt('commonmark', {'html':True}).enable('table').render(body),'html.parser')
# Relative markdown links are based on the canonical page, except resolved wiki links.
for a in soup.find_all('a',href=True):
    href=a['href']
    if href.startswith('../../wiki/'):
        continue
    if not urlsplit(href).scheme and not href.startswith('#'):
        resolved=(SOURCE.parent/unquote(href.split('#')[0])).resolve()
        if resolved.is_relative_to(ROOT):a['href']='../../'+resolved.relative_to(ROOT).as_posix()
nav=[]
for i,heading in enumerate(soup.find_all(['h2','h3']),1):
    heading['id']=f'section-{i}'
    if heading.name=='h2':nav.append(f'<a href="#section-{i}">{h(heading.get_text())}</a>')
    if heading.get_text() in ['판정 로직','확장 아키텍처와 운영 계약']:
        kind='runtime' if heading.get_text()=='판정 로직' else 'updates'
        svg=diagram(kind);(OUT/f'{kind}-flow.svg').write_text(svg)
        figure=BeautifulSoup(f'<figure class="diagram">{svg}<figcaption>{"현재 구현 흐름" if kind=="runtime" else "미구현 목표 설계"} · <a href="{kind}-flow.svg">도식 파일 열기</a></figcaption></figure>','html.parser')
        heading.insert_after(figure)
for table in soup.find_all('table'):
    wrapper=soup.new_tag('div',attrs={'class':'table-scroll'});table.wrap(wrapper)
policy=json.loads(POLICY.read_text())
(OUT/'policy.json').write_text(json.dumps(policy,ensure_ascii=False,indent=2)+'\n')
(OUT/'assessment.schema.json').write_text(json.dumps(GuidelineAssessment.model_json_schema(),ensure_ascii=False,indent=2)+'\n')
manifest={'generated_from':str(SOURCE.relative_to(ROOT)),'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),
          'policy_version':policy['version'],'policy_file_sha256':hashlib.sha256(POLICY.read_bytes()).hexdigest(),
          'assessment_contract':'opm-llm-assessment/3','document_date':'2026-09-09','status':'local_document_export',
          'verification_note':'0.5.0: Hanwha attendance 12/12 accepted unreviewed via MCP; final REVIEW for unresolved independence; duplicate rejected. Historical results labeled separately.'}
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
css='''
:root{--ink:#1c302b;--muted:#597168;--paper:#faf9f4;--line:#dce4dc;--green:#14684e}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:30px}body{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;line-height:1.85;font-size:16px}.layout{display:grid;grid-template-columns:250px minmax(0,1fr);max-width:1500px;margin:auto}aside{height:100vh;position:sticky;top:0;padding:35px 24px;overflow:auto;border-right:1px solid var(--line)}.brand{font-weight:800;font-size:24px;letter-spacing:-1px}.edition{color:var(--muted);font-size:12px;margin:5px 0 25px}nav a{display:block;color:var(--muted);font-size:13px;text-decoration:none;padding:7px 0;line-height:1.5}nav a:hover{color:var(--green)}main{padding:65px 65px 100px;min-width:0}header{margin-bottom:55px;border-bottom:2px solid var(--ink);padding-bottom:35px}.eyebrow{font-size:12px;letter-spacing:2px;color:var(--green);font-weight:750}h1{font-size:45px;line-height:1.22;letter-spacing:-2px;margin:22px 0}header p{max-width:720px;color:var(--muted)}.tags{display:flex;gap:10px;flex-wrap:wrap;margin:24px 0}.tags span{border:1px solid #bcd3c5;border-radius:4px;padding:4px 10px;font-size:12px}.actions{display:flex;flex-wrap:wrap;gap:10px}.actions a,.actions button{border:1px solid var(--line);background:white;color:var(--green);padding:7px 13px;border-radius:5px;text-decoration:none;font:inherit;font-size:13px;cursor:pointer}h2{font-size:29px;letter-spacing:-1px;margin:64px 0 20px;padding-top:20px;border-top:1px solid var(--line);line-height:1.4}h3{font-size:20px;margin:35px 0 15px}p{margin:15px 0}a{color:var(--green);text-underline-offset:3px}strong{font-weight:750}ul,ol{padding-left:24px}li{margin:8px 0}code{font-family:"SFMono-Regular",Consolas,monospace;font-size:.85em;background:#edf1eb;border-radius:3px;padding:2px 5px;overflow-wrap:anywhere}pre{background:#eaf0e9;border:1px solid var(--line);padding:22px;border-radius:10px;overflow:auto;line-height:1.7}pre code{background:none;padding:0;font-size:13px}.table-scroll{overflow:auto;border:1px solid var(--line);border-radius:9px;margin:24px 0;background:#fff}table{width:100%;border-collapse:collapse;font-size:14px;line-height:1.7}th{text-align:left;background:#eaf0e9;font-weight:750;white-space:normal}th,td{padding:14px 16px;border-bottom:1px solid var(--line);vertical-align:top;min-width:140px}tr:last-child td{border:0}td:first-child{font-weight:600;min-width:165px}figure{margin:25px 0}.diagram{overflow:auto}.diagram svg{width:100%;min-width:680px;display:block}.diagram figcaption{font-size:12px;color:var(--muted);margin-top:8px}blockquote{border-left:3px solid var(--green);padding-left:20px;margin-left:0;color:var(--muted)}footer{font-size:12px;color:var(--muted);margin-top:65px;border-top:1px solid var(--line);padding-top:20px;overflow-wrap:anywhere}.skip{position:absolute;left:-9999px}.skip:focus{left:15px;top:15px;background:white;padding:10px;z-index:9}@media(max-width:1100px){main{padding:45px 30px}.layout{grid-template-columns:210px minmax(0,1fr)}aside{padding:25px 18px}}@media(max-width:760px){.layout{display:block}aside{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line);padding:18px 24px}nav{display:flex;gap:12px;overflow:auto;white-space:nowrap}nav a{flex:none}.edition{margin:0 0 8px}main{padding:32px 22px}h1{font-size:34px}h2{font-size:25px}table{font-size:13px}.brand{font-size:19px}}@media print{body{background:white;font-size:10pt}.layout{display:block}aside,.actions,.skip{display:none}main{padding:0}header{margin-bottom:20px;padding-bottom:15px}h1{font-size:30pt}h2{font-size:19pt;margin-top:28px;break-after:avoid}h3{break-after:avoid}pre{white-space:pre-wrap}table{font-size:8pt}.table-scroll{overflow:visible}th,td{min-width:0!important;padding:7px;overflow-wrap:anywhere}tr{break-inside:avoid}.diagram svg{min-width:0}figure{break-inside:avoid}a{color:inherit}@page{size:A4;margin:16mm}}
'''
page=f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OPM guideline v2 · 명세, 설계, 판정 로직</title><style>{css}</style></head><body><a class="skip" href="#document">본문 바로가기</a><div class="layout"><aside><div class="brand">OPM / Guidelines</div><div class="edition">명세·설계 문서 · 2026.09.09</div><nav aria-label="문서 목차">{''.join(nav)}</nav></aside><main id="document"><header><div class="eyebrow">OPEN PROXY MCP · DESIGN SPECIFICATION</div><h1>근거에서 판단까지.<br>Guideline v2 명세와 설계</h1><p>무엇을 읽고, 어떤 평가를 수용하며, 어떻게 권고를 만드는가.<br>기사 사용 원칙과 공시·기관 원천, 실제 판정 로직과 확장 설계를 한 문서로 정리했습니다.</p><div class="tags"><span>정책 {h(policy['version'])}</span><span>현재 구현 / 목표 설계 구분</span><span>LLM 평가 · 사람 미검토</span></div><div class="actions"><button onclick="window.print()">인쇄 / PDF로 저장</button><a href="policy.json">현재 정책 JSON</a><a href="assessment.schema.json">평가 입력 스키마</a><a href="../../wiki/decisions/{SOURCE.name}">문서 원본</a></div></header><article>{soup}</article><footer>이 HTML은 정본 Markdown과 현재 정책·평가 스키마에서 생성한 읽기용 문서입니다. 운영 배포나 후보 재평가를 뜻하지 않습니다.<br>정본 SHA-256: {manifest['source_sha256']}<br>재생성: scripts/render_guideline_spec.py · <a href="manifest.json">생성 명세</a></footer></main></div></body></html>'''
(OUT/'index.html').write_text(page)
print(json.dumps({'output':str(OUT/'index.html'),'sections':len(nav),'policy':policy['version']},ensure_ascii=False))
