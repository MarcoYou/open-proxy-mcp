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
from open_proxy_mcp.services.guideline_workflow import WorkflowSettings
from open_proxy_mcp.services.governance_screen import GovernanceAssessment

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'wiki/decisions/260908_1200_decision_guideline-v2-final-redesign-pilot.md'
OUT = ROOT / 'output/guideline-specification-20260909'
POLICY = ROOT / 'open_proxy_mcp/data/guideline/opm-guideline-v2-pilot.json'
OUT.mkdir(parents=True, exist_ok=True)
h = html.escape


def diagram(kind):
    height = 610 if kind in {'runtime', 'governance'} else 410
    title = {'runtime': '현재 MCP의 원문·평가·판정 흐름',
             'governance': '공시 발견 → LLM 거버넌스 검토 → 호출자 증분 조회',
             'updates': '자료와 정책의 갱신 경로 — 목표 설계'}[kind]
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
        boxes=[(35,65,'01 · REQUEST','회차·기준일 고정',['회사 · 회의 · 후보 · 보팅 설정','성향과 자동/수동 처리를 분리']),
               (405,65,'02 · EVIDENCE','공개 원문 수집',['공고 · 연차 · 임원 변동 공시','회사 맥락 4건 · 추가 5건']),
               (775,65,'03 · TASK','평가 과업 반환',['원문 발췌 · 정책 · 출력 스키마','대상과 내용에 연결된 task_id']),
               (775,240,'04 · CALLER LLM','원문 읽기·평가 제출',['선임구분 · 독립성 · 반증','필요한 문맥만 이어 읽기']),
               (405,240,'05 · VALIDATE','과업·인용 연결 검사',['타입 · 현재 과업 · 원문 인용','의미 정확성 인증과는 별개']),
               (35,240,'06 · METRICS','수용 평가를 지표로',['원문 판독 · 기간·출석 계산','누락 기준 제외 · 나머지 진행']),
               (35,415,'07 · RULES','규칙별 상태와 권고',['반대 신호 보존 · 긍정 게이트','재직·정지 구간과 회의 수 계산']),
               (405,415,'08 · CONSTRAINTS','교정과 보호 범위',['추출 오류 REVIEW만 근거별 교정','법령 · 표결 관계 · 다른 우려 보존']),
               (775,415,'09 · RESULT','권고와 처리 경로',['자동 준비 / 일부·전체 수동','사람 미검토 · 실제 투표 전송 없음'])]
        for args in boxes:box(*args)
        for d in ['M325 124H405','M695 124H775','M920 183V240','M775 299H695','M405 299H325','M180 358V415','M325 474H405','M695 474H775']:line(d)
        parts.append('<text x="35" y="579" class="s">연결 검사 실패 → 평가 거절·미사용. 추가 자료나 정책이 바뀌면 새 과업으로 재평가.</text>')
    elif kind == 'governance':
        boxes=[(35,65,'01 · DISCOVER','읽을 회사 발견',['screener · governance 유형','회사 중복 제거 · 최대 30개']),
               (405,65,'02 · SOURCE','회사별 원문 과업',['공개매수 · 지분 · 소송 · 재편','날짜 · 해시 · 문맥 · 실패 보존']),
               (775,65,'03 · CALLER LLM','판독 · 연결 · 반증',['행위 · 당사자 · 조건 · 후속 사건','필요한 원문만 다시 펼쳐 읽기']),
               (775,240,'04 · ASSESS','구체적 영향 평가',['우려 · 반대 근거 · 건너뛴 항목','사건 존재만으로 악재 판정 금지']),
               (405,240,'05 · VALIDATE','과업 · 인용 · 계약 검사',['현재 자료와 평가를 연결','잘못된 제출은 해당 회사만 제외']),
               (35,240,'06 · TRIAGE','검토 우선순위',['사용자 중요도에 따라 분기','읽은 범위 · 사람 미검토 표시']),
               (35,415,'07 · CHECKPOINT','본 접수번호 반환',['호출자가 보존해 다음에 전달','서버에 평가 결과 저장 없음']),
               (405,415,'08 · NEXT CALL','원할 때 새 공시 조회',['since · known_receipts 적용','예약 실행을 만들지 않음']),
               (775,415,'09 · REASSESS','변경 원문을 다시 판단',['정정 · 결과 · 철회 내용 대조','새 원문 · 설정이면 새 과업'])]
        for args in boxes:box(*args)
        for d in ['M325 124H405','M695 124H775','M920 183V240','M775 299H695','M405 299H325','M180 358V415','M325 474H405','M695 474H775']:line(d)
        parts.append('<text x="35" y="579" class="s">기업 거버넌스 검토와 안건별 의결권 자문은 별도 과업. 실제 투표 전송·영구 사건 그래프는 범위 밖.</text>')
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


def render_roadmap(soup):
    """Turn the canonical status table into a chart and done/remaining panels."""
    heading = soup.find('h2', string='배포 로드맵')
    section = soup.new_tag('section', attrs={'class': 'roadmap-overview', 'aria-label': '배포 로드맵'})
    heading.insert_before(section)
    node = heading
    while node is not None:
        following = node.next_sibling
        if node is not heading and node.name == 'h2':
            break
        section.append(node.extract())
        node = following

    table = section.find('table')
    stages = []
    for row in table.select('tbody tr'):
        cells = row.find_all('td')
        number, status, title, done, remaining = [cell.get_text(' ', strip=True) for cell in cells]
        tone = {'완료': 'done', '현재 도달': 'current', '다음 착수': 'next', '남음': 'pending'}[status]
        stages.append(dict(number=number, status=status, title=title, done=done, remaining=remaining, tone=tone))

    chart = [f'<ol class="roadmap-chart" aria-label="설계부터 운영 배포까지 {len(stages)}단계">']
    for stage in stages:
        current = ' aria-current="step"' if stage['tone'] == 'current' else ''
        chart.append(f'<li class="phase-{stage["tone"]}"><a href="#phase-{stage["number"]}"{current}>'
                     f'<span class="phase-number">{stage["number"]}</span>'
                     f'<span class="phase-status">{h(stage["status"])}</span>'
                     f'<strong>{h(stage["title"])}</strong></a></li>')
    chart.append('</ol><p class="chart-caption">각 단계를 누르면 완료 내용과 남은 일을 볼 수 있습니다. '
                 '<a href="roadmap.svg">로드맵 그림 열기</a></p>')
    panels = ['<div class="roadmap-panels">']
    for completed, title in [(True, '완료·현재 구현'), (False, '배포까지 남음')]:
        panels.append(f'<section class="roadmap-panel {"completed" if completed else "remaining"}">'
                      f'<h3>{title}</h3><ol>')
        for stage in stages:
            if (stage['tone'] in {'done', 'current'}) != completed:
                continue
            primary, secondary = ('done', 'remaining') if completed else ('remaining', 'done')
            panels.append(f'<li id="phase-{stage["number"]}"><h4><span>{stage["number"]}</span> '
                          f'{h(stage["title"])}</h4><p>{h(stage[primary])}</p>')
            if completed:
                panels.append(f'<p class="phase-limit">{"남은 확인: " if stage["tone"] == "current" else "범위: "}{h(stage[secondary])}</p>')
            else:
                panels.append(f'<details><summary>이미 확보한 기반</summary><p>{h(stage[secondary])}</p></details>')
            panels.append('</li>')
        panels.append('</ol></section>')
    panels.append('</div>')
    table.replace_with(BeautifulSoup(''.join(chart + panels), 'html.parser'))

    # Put the immediate next action between the chart and the details.
    next_heading = section.find('h3', string='바로 다음 작업')
    action = soup.new_tag('section', attrs={'class': 'next-action', 'aria-label': '바로 다음 작업'})
    next_paragraph = next_heading.find_next_sibling('p')
    action.append(next_heading.extract())
    action.append(next_paragraph.extract())
    section.select_one('.roadmap-panels').insert_before(action)
    section.find('blockquote')['class'] = 'current-position'
    for paragraph in section.find_all('p'):
        if paragraph.get_text().startswith('저장·배포 상태'):
            paragraph['class'] = 'release-status'

    # Export the same stages, without a second hand-maintained roadmap.
    current = next(stage for stage in stages if stage['tone'] == 'current')
    next_stage = next(stage for stage in stages if stage['tone'] == 'next')
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 280" role="img" aria-labelledby="roadmap-title">',
           f'<title id="roadmap-title">배포 로드맵 · 현재 {current["number"]} {h(current["title"])}</title>',
           '<rect width="1200" height="280" rx="18" fill="#faf9f4"/>',
           '<style>text{font-family:Arial,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;fill:#1c302b}.title{font-size:25px;font-weight:700}.label{font-size:17px;font-weight:700}.status{font-size:14px}.number{font-size:26px;font-weight:700}.caption{font-size:14px;fill:#597168}</style>',
           '<text x="28" y="40" class="title">OPM guideline v2 · 배포 로드맵</text>']
    for index, stage in enumerate(stages):
        x = 28 + index * 194
        fill, stroke = {'done': ('#edf5ed', '#aec9b8'), 'current': ('#e9f1fd', '#3b69a6'),
                        'next': ('#fff4df', '#c89643'), 'pending': ('#fff', '#cad3cc')}[stage['tone']]
        dash = ' stroke-dasharray="5 4"' if stage['tone'] == 'pending' else ''
        svg.append(f'<rect x="{x}" y="68" width="174" height="154" rx="10" fill="{fill}" stroke="{stroke}"{dash}/>'
                   f'<text x="{x+15}" y="102" class="number">{stage["number"]}</text>'
                   f'<text x="{x+15}" y="134" class="status">{h(stage["status"])}</text>'
                   f'<text x="{x+15}" y="184" class="label">{h(stage["title"])}</text>')
        if index < len(stages) - 1:
            svg.append(f'<path d="M{x+179} 144h10m-4-4 4 4-4 4" stroke="#597168" fill="none"/>')
    svg.append(f'<text x="28" y="255" class="caption">현재: {h(current["title"])} · 다음: {h(next_stage["title"])} · 상세 상태와 적용 범위는 정본 문서 참조</text></svg>')
    (OUT/'roadmap.svg').write_text(''.join(svg))


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
render_roadmap(soup)
nav=[]
for i,heading in enumerate(soup.find_all(['h2','h3']),1):
    heading['id']='roadmap' if heading.get_text()=='배포 로드맵' else f'section-{i}'
    if heading.name=='h2':nav.append(f'<a href="#{heading["id"]}">{h(heading.get_text())}</a>')
    if heading.get_text() in ['판정 로직','확장 아키텍처와 운영 계약','기업 발견·분쟁 추적·대량 검토 (0.7.0)']:
        kind={'판정 로직':'runtime','확장 아키텍처와 운영 계약':'updates','기업 발견·분쟁 추적·대량 검토 (0.7.0)':'governance'}[heading.get_text()]
        svg=diagram(kind);(OUT/f'{kind}-flow.svg').write_text(svg)
        figure=BeautifulSoup(f'<figure class="diagram">{svg}<figcaption>{"미구현 목표 설계" if kind=="updates" else "현재 구현 흐름"} · <a href="{kind}-flow.svg">도식 파일 열기</a></figcaption></figure>','html.parser')
        heading.insert_after(figure)
for table in soup.find_all('table'):
    wrapper=soup.new_tag('div',attrs={'class':'table-scroll'});table.wrap(wrapper)
policy=json.loads(POLICY.read_text())
(OUT/'policy.json').write_text(json.dumps(policy,ensure_ascii=False,indent=2)+'\n')
(OUT/'assessment.schema.json').write_text(json.dumps(GuidelineAssessment.model_json_schema(),ensure_ascii=False,indent=2)+'\n')
(OUT/'workflow.schema.json').write_text(json.dumps(WorkflowSettings.model_json_schema(),ensure_ascii=False,indent=2)+'\n')
(OUT/'governance.schema.json').write_text(json.dumps(GovernanceAssessment.model_json_schema(),ensure_ascii=False,indent=2)+'\n')
manifest={'generated_from':str(SOURCE.relative_to(ROOT)),'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),
          'policy_version':policy['version'],'policy_file_sha256':hashlib.sha256(POLICY.read_bytes()).hexdigest(),
          'assessment_contract':'opm-llm-assessment/5','governance_contract':'1','document_date':'2026-09-09','status':'local_document_export',
          'verification_note':'Version-separated live MCP pilot evidence is maintained in the canonical verification section. Source-bound acceptance and routing are not an independent accuracy benchmark. All assessments remain human unreviewed; no ballots or scheduled runs.'}
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
css='''
:root{--ink:#1c302b;--muted:#597168;--paper:#faf9f4;--line:#dce4dc;--green:#14684e}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:30px}body{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;line-height:1.85;font-size:16px}.layout{display:grid;grid-template-columns:250px minmax(0,1fr);max-width:1500px;margin:auto}aside{height:100vh;position:sticky;top:0;padding:35px 24px;overflow:auto;border-right:1px solid var(--line)}.brand{font-weight:800;font-size:24px;letter-spacing:-1px}.edition{color:var(--muted);font-size:12px;margin:5px 0 25px}nav a{display:block;color:var(--muted);font-size:13px;text-decoration:none;padding:7px 0;line-height:1.5}nav a:hover{color:var(--green)}main{padding:65px 65px 100px;min-width:0}header{margin-bottom:55px;border-bottom:2px solid var(--ink);padding-bottom:35px}.eyebrow{font-size:12px;letter-spacing:2px;color:var(--green);font-weight:750}h1{font-size:45px;line-height:1.22;letter-spacing:-2px;margin:22px 0}header p{max-width:720px;color:var(--muted)}.tags{display:flex;gap:10px;flex-wrap:wrap;margin:24px 0}.tags span{border:1px solid #bcd3c5;border-radius:4px;padding:4px 10px;font-size:12px}.actions{display:flex;flex-wrap:wrap;gap:10px}.actions a,.actions button{border:1px solid var(--line);background:white;color:var(--green);padding:7px 13px;border-radius:5px;text-decoration:none;font:inherit;font-size:13px;cursor:pointer}h2{font-size:29px;letter-spacing:-1px;margin:64px 0 20px;padding-top:20px;border-top:1px solid var(--line);line-height:1.4}h3{font-size:20px;margin:35px 0 15px}p{margin:15px 0}a{color:var(--green);text-underline-offset:3px}strong{font-weight:750}ul,ol{padding-left:24px}li{margin:8px 0}code{font-family:"SFMono-Regular",Consolas,monospace;font-size:.85em;background:#edf1eb;border-radius:3px;padding:2px 5px;overflow-wrap:anywhere}pre{background:#eaf0e9;border:1px solid var(--line);padding:22px;border-radius:10px;overflow:auto;line-height:1.7}pre code{background:none;padding:0;font-size:13px}.table-scroll{overflow:auto;border:1px solid var(--line);border-radius:9px;margin:24px 0;background:#fff}table{width:100%;border-collapse:collapse;font-size:14px;line-height:1.7}th{text-align:left;background:#eaf0e9;font-weight:750;white-space:normal}th,td{padding:14px 16px;border-bottom:1px solid var(--line);vertical-align:top;min-width:140px}tr:last-child td{border:0}td:first-child{font-weight:600;min-width:165px}figure{margin:25px 0}.diagram{overflow:auto}.diagram svg{width:100%;min-width:680px;display:block}.diagram figcaption{font-size:12px;color:var(--muted);margin-top:8px}blockquote{border-left:3px solid var(--green);padding-left:20px;margin-left:0;color:var(--muted)}footer{font-size:12px;color:var(--muted);margin-top:65px;border-top:1px solid var(--line);padding-top:20px;overflow-wrap:anywhere}.skip{position:absolute;left:-9999px}.skip:focus{left:15px;top:15px;background:white;padding:10px;z-index:9}@media(max-width:1100px){main{padding:45px 30px}.layout{grid-template-columns:210px minmax(0,1fr)}aside{padding:25px 18px}}@media(max-width:760px){.layout{display:block}aside{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line);padding:18px 24px}nav{display:flex;gap:12px;overflow:auto;white-space:nowrap}nav a{flex:none}.edition{margin:0 0 8px}main{padding:32px 22px}h1{font-size:34px}h2{font-size:25px}table{font-size:13px}.brand{font-size:19px}}@media print{body{background:white;font-size:10pt}.layout{display:block}aside,.actions,.skip{display:none}main{padding:0}header{margin-bottom:20px;padding-bottom:15px}h1{font-size:30pt}h2{font-size:19pt;margin-top:28px;break-after:avoid}h3{break-after:avoid}pre{white-space:pre-wrap}table{font-size:8pt}.table-scroll{overflow:visible}th,td{min-width:0!important;padding:7px;overflow-wrap:anywhere}tr{break-inside:avoid}.diagram svg{min-width:0}figure{break-inside:avoid}a{color:inherit}@page{size:A4;margin:16mm}}
'''
css+='''
main{padding:36px 48px 100px}header{margin-bottom:26px;padding-bottom:24px}h1{font-size:35px;margin:12px 0;letter-spacing:-1.3px}header p{margin:10px 0 18px}.eyebrow{letter-spacing:1px}nav a:first-child{font-weight:800;color:var(--green)}
.roadmap-overview>h2{border:0;padding:0;margin:0 0 16px;font-size:28px}.roadmap-overview>p{font-size:14px;color:var(--muted)}
.current-position{border:1px solid #b8cce9;border-left:4px solid #3b69a6;background:#eef4fd;border-radius:8px;padding:15px 18px;margin:0 0 16px;color:#244a7d;font-size:14px;line-height:1.7}.current-position p{margin:0}.current-position strong:first-child{font-size:16px}
.roadmap-chart{list-style:none;padding:0;margin:24px 0 8px;display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:14px}.roadmap-chart li{position:relative;margin:0;min-width:0}.roadmap-chart li:not(:last-child)::after{content:'→';position:absolute;right:-13px;top:62px;color:var(--muted);font-size:16px;z-index:1}
.roadmap-chart a{height:100%;min-height:162px;display:flex;flex-direction:column;align-items:flex-start;padding:13px 12px 17px;border:1px solid #cad3cc;border-radius:9px;background:#fff;text-decoration:none;color:var(--ink);line-height:1.55}.roadmap-chart a:hover,.roadmap-chart a:focus-visible{outline:2px solid var(--green);outline-offset:3px}.roadmap-chart .phase-done a{background:#edf5ed;border-color:#aec9b8}.roadmap-chart .phase-current a{background:#e9f1fd;border:2px solid #3b69a6;padding:12px 11px 16px;color:#244a7d}.roadmap-chart .phase-next a{background:#fff4df;border-color:#c89643}.roadmap-chart .phase-pending a{border-style:dashed}.phase-number{font-size:26px;font-weight:800;line-height:1.2}.phase-status{font-size:11px;font-weight:700;margin:9px 0 13px;white-space:nowrap}.roadmap-chart strong{font-size:15px;word-break:keep-all;overflow-wrap:anywhere}.chart-caption{font-size:12px!important;margin:10px 0 20px!important}
.next-action{padding:18px 22px;border-radius:9px;background:#fff4df;border:1px solid #e4c997;margin:24px 0}.next-action h3{font-size:17px;margin:0 0 6px;color:#825b1f}.next-action p{font-size:14px;line-height:1.75;margin:0}
.roadmap-panels{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:24px 0 30px}.roadmap-panel{border:1px solid var(--line);border-radius:10px;background:white;padding:22px}.roadmap-panel>h3{font-size:21px;margin:0 0 16px}.completed>h3{color:var(--green)}.remaining>h3{color:#825b1f}.roadmap-panel ol{list-style:none;padding:0;margin:0}.roadmap-panel li{margin:0;padding:18px 0;border-top:1px solid var(--line);scroll-margin-top:24px}.roadmap-panel li:first-child{border-top:0;padding-top:0}.roadmap-panel li:last-child{padding-bottom:0}.roadmap-panel h4{font-size:16px;margin:0 0 7px;line-height:1.5}.roadmap-panel h4 span{font-variant-numeric:tabular-nums;color:var(--muted);font-size:13px;margin-right:5px}.roadmap-panel p{font-size:14px;line-height:1.75;margin:6px 0}.roadmap-panel .phase-limit{font-size:12px;color:var(--muted)}.roadmap-panel details{font-size:12px;color:var(--muted);margin-top:8px}.roadmap-panel summary{cursor:pointer}.roadmap-panel details p{font-size:12px}.release-status{padding:16px 18px;background:#edf1eb;border:1px solid var(--line);border-radius:8px;font-size:13px!important}.roadmap-overview>ul{font-size:14px}
@media(max-width:1100px){main{padding:30px}.roadmap-chart{gap:10px}.roadmap-chart li:not(:last-child)::after{right:-10px;font-size:13px}.roadmap-chart a{padding:12px 8px}.roadmap-chart .phase-current a{padding:11px 7px}.roadmap-chart strong{font-size:14px}.roadmap-panel{padding:18px}}
@media(max-width:950px) and (min-width:761px){.layout{grid-template-columns:185px minmax(0,1fr)}}
@media(max-width:950px){main{padding:26px 20px}header{margin-bottom:22px}h1{font-size:29px}.eyebrow{font-size:10px}.roadmap-chart{grid-template-columns:1fr;gap:12px;margin-top:18px}.roadmap-chart a,.roadmap-chart .phase-current a{min-height:70px;padding:13px 16px;display:grid;grid-template-columns:36px 78px minmax(0,1fr);align-items:center;gap:12px}.roadmap-chart .phase-current a{padding:12px 15px}.phase-number{font-size:23px}.phase-status{margin:0;font-size:11px}.roadmap-chart strong{font-size:15px}.roadmap-chart li:not(:last-child)::after{content:'↓';top:auto;bottom:-13px;left:28px;right:auto;line-height:13px}.roadmap-panels{grid-template-columns:1fr;gap:16px}.next-action{padding:16px}.current-position{padding:13px 14px}.roadmap-overview>h2{font-size:25px}.actions{gap:7px}.actions a,.actions button{font-size:12px;padding:6px 9px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
@media print{main{padding:0}h1{font-size:25pt}.roadmap-chart{grid-template-columns:repeat(6,minmax(0,1fr));gap:7px;break-inside:avoid}.roadmap-chart a,.roadmap-chart .phase-current a{display:flex;min-height:110px;padding:10px 7px}.roadmap-chart strong{font-size:9pt}.phase-number{font-size:17pt}.phase-status{font-size:8pt;margin:5px 0}.roadmap-chart li:not(:last-child)::after{content:'→';top:45px;bottom:auto;left:auto;right:-8px;font-size:10px}.roadmap-panels{grid-template-columns:1fr 1fr;gap:12px}.roadmap-panel{padding:12px}.roadmap-panel li,.next-action,.current-position{break-inside:avoid}.roadmap-panel p,.next-action p{font-size:9pt}.roadmap-panel h4{font-size:10pt}.chart-caption{display:none}}
'''
page=f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OPM guideline v2 · 로드맵, 명세, 설계</title><style>{css}</style></head><body><a class="skip" href="#document">본문 바로가기</a><div class="layout"><aside><div class="brand">OPM / Guidelines</div><div class="edition">명세·설계 문서 · 2026.09.09</div><nav aria-label="문서 목차">{''.join(nav)}</nav></aside><main id="document"><header><div class="eyebrow">OPEN PROXY MCP · {h(policy['version'])} · ASTRA</div><h1>Guideline v2 · 진행 현황과 설계</h1><p>완료한 일, 현재 위치, 배포까지 남은 일부터 봅니다.<br>아래에 근거·정책·판정 로직과 실제 파일럿 결과가 이어집니다.</p><div class="actions"><button onclick="window.print()">인쇄 / PDF</button><a href="policy.json">현재 정책 JSON</a><a href="assessment.schema.json">평가 입력</a><a href="workflow.schema.json">보팅 설정</a><a href="governance.schema.json">거버넌스 평가</a><a href="../../wiki/decisions/{SOURCE.name}">문서 원본</a></div></header><article>{soup}</article><footer>이 HTML은 정본 Markdown과 현재 정책·평가 스키마에서 생성한 읽기용 문서입니다. 운영 배포나 후보 재평가를 뜻하지 않습니다.<br>정본 SHA-256: {manifest['source_sha256']}<br>재생성: scripts/render_guideline_spec.py · <a href="manifest.json">생성 명세</a></footer></main></div></body></html>'''
(OUT/'index.html').write_text(page)
print(json.dumps({'output':str(OUT/'index.html'),'sections':len(nav),'policy':policy['version']},ensure_ascii=False))
