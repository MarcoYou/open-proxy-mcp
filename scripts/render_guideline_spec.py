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
from pydantic import TypeAdapter
from open_proxy_mcp.services.guideline_assessment import GuidelineAssessment
from open_proxy_mcp.services.guideline_workflow import WorkflowSettings
from open_proxy_mcp.services.governance_screen import GovernanceAssessment
from open_proxy_mcp.services.guideline_harness import HarnessRequest, CONTRACT as HARNESS_CONTRACT
from open_proxy_mcp.services.guideline_research import ResearchQuery
from open_proxy_mcp.harness.runner import ModelAction
from open_proxy_mcp.services.election_structure import (
    StructureRequest, StructureAssessment, StructureFact, StructureGap,
    StructureFinding, StructureJudgment, DATA_TYPES, CONTRACT as STRUCTURE_CONTRACT)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'wiki/decisions/260908_1200_decision_guideline-v2-final-redesign-pilot.md'
OUT = ROOT / 'output/guideline-specification-20260909'
POLICY = ROOT / 'open_proxy_mcp/data/guideline/opm-guideline-v2-pilot.json'
OUT.mkdir(parents=True, exist_ok=True)
h = html.escape

# Keep the five body entry points stable; the appendix explains harness controls.
SECTIONS = {
    '현황·로드맵': ('roadmap', '완료한 일과 다음 단계'),
    '가이드라인·판단 원칙': ('principles', '시점·근거·사용자 설정'),
    '모델 교체·실행 흐름': ('workflow', '공통 과업과 모델의 역할'),
    '검증 계획·결과': ('validation', '10개사 회차별 품질과 후속 과제'),
    '배포·운영': ('release', '출시 조건과 남은 기능'),
    'Appendix': ('appendix', 'A 실행 통제 · B 오류 교정 · C 검증'),
}
FOLDED_HEADINGS = {
    '판단 기준과 적용 범위', '정책 설계 상세', '정보·기사 사용 기준',
    '출처와 시점 관리', '실행 명세', '판정 로직', '역할과 대조 순서',
    '파일럿 결과', '통합 준비 검증', '출시 조건과 미구현 범위',
    '확장 아키텍처와 운영 계약', '관련 파일', '비교 브랜치 정리 (2026-09-09)',
    '시점 고정 실행 하네스',
}
# These were exported as section-2 ... section-58 before the five-section layout.
# Retain them independently of the next generation's document order.
LEGACY_HEADINGS = '''바로 다음 작업
완료·현재 구현
배포까지 남음
배포 범위와 확장 작업
실행 프레임워크 및 검증 프로필
실행 기준과 기본 설정
역할과 대조 순서
종료 조건과 이번 준비의 범위
작업 기여
비교 브랜치 정리 (2026-09-09)
결정 범위
현재 구현 범위와 다음 결정
사용자 원칙과 구현 경계
출석기간 정책 (0.5.0부터 적용, 0.6.0 직접 판독 보강)
최종 설계
다섯 축을 분리한다
정책은 작성본에서 실행본으로 컴파일한다
LLM의 역할과 출력
실행 명세
공시 원문을 읽는 실행 경로 (0.7.0)
기업 발견·분쟁 추적·대량 검토 (0.7.0)
참고 서비스에서 채택한 점
요청과 응답
요청별 보팅 성향과 처리 설정
평가 데이터 계약
수집·평가·권고·처리 상태를 섞지 않는다
판정 로직
현재의 처리 순서
평가 → 지표 → 권고
누락·충돌·평가 미실행의 적용 계약 (0.6.0)
출석 입력과 원천 탐색의 실행 계약 (0.6.0)
후보 권고와 최종 권고의 차이
실무 관점을 적용한 설계 선택
경계 사례 (설명용, 실제 회사 재평가 결과 아님)
확장 아키텍처와 운영 계약
모듈 책임과 경계
확장할 공통 데이터 객체
임원 변동 공시를 재직 이력으로 연결하는 계약
설정·확장·갱신의 작동 방식
원천 연결 우선순위와 수용 기준
파일럿 결과
실제 MCP 호출: 0.7.0 대량 파일럿
실제 MCP 호출: 0.7.0 추출 교정
실제 MCP 호출: 0.7.0 분쟁 추적의 경계 검증
실제 MCP 호출: 0.6.0 워크플로 검증
실제 MCP 호출: 0.5.0 출석·재직 연결
실제 MCP 호출: 0.3.0 공개자료 한정 LLM 파일럿
공개되지 않은 관계 정보 처리: 0.3.0 도입과 현재 확장
기사 사용 원칙: 사실·절차와 평가 분리 (0.4.0)
공시 활용 공백 점검: 전체 부재와 v2 연결 부족 구별
시의성·정확성의 공통 기준
공개 원천과 추가 연결 계획
임원 변동 공시 표본에서 확인한 연결 조건
공정위 API를 호출할 때
계약·회귀 검증
해석과 승격 조건
관련 파일'''.splitlines()
LEGACY_IDS = {title: f'section-{i}' for i, title in enumerate(LEGACY_HEADINGS, 2)}
RENAMED_HEADINGS = {
    '실행 프레임워크 및 검증 프로필': '모델 교체·실행 흐름',
    '종료 조건과 이번 준비의 범위': '통합 준비 검증',
    '결정 범위': '판단 기준과 적용 범위',
    '현재 구현 범위와 다음 결정': '판단 기준과 적용 범위',
    '최종 설계': '정책 설계 상세',
    '해석과 승격 조건': '출시 조건과 미구현 범위',
}


def fold_document_details(soup):
    """Fold complete authored subsections without dropping text or lower headings."""
    for heading in list(soup.find_all('h3')):
        if heading.get_text(' ', strip=True) not in FOLDED_HEADINGS:
            continue
        detail = soup.new_tag('details', attrs={'class': 'document-detail'})
        summary = soup.new_tag('summary')
        content = soup.new_tag('div', attrs={'class': 'detail-body'})
        heading.insert_before(detail)
        node = heading.next_sibling
        summary.append(heading.extract())
        detail.append(summary)
        while node is not None:
            following = node.next_sibling
            if node.name in {'h1', 'h2', 'h3'}:
                break
            content.append(node.extract())
            node = following
        detail.append(content)


def preserve_legacy_fragments(soup, headings_by_title):
    """Old deep links resolve to the retained heading or its new owning section."""
    for title, legacy_id in LEGACY_IDS.items():
        if soup.find(id=legacy_id):
            continue
        target = headings_by_title.get(RENAMED_HEADINGS.get(title, title))
        if target is None:
            number = int(legacy_id.split('-')[1])
            fallback = ('roadmap' if number <= 5 or number in {10, 13} else
                        'workflow' if 6 <= number <= 9 or 20 <= number <= 41 else
                        'validation' if 42 <= number <= 48 or number == 56 else
                        'release' if number in {11, 57, 58} else 'principles')
            target = soup.find(id=fallback)
        if target is not None:
            alias = soup.new_tag('span', attrs={
                'id': legacy_id, 'class': 'fragment-alias', 'aria-hidden': 'true',
            })
            target.insert(0, alias)


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
        boxes=[(35,65,'01 · REQUEST','회사·정확한 공고 고정',['cutoff_at · 소집공고 접수번호','정기/임시 · 보팅 설정 확인']),
               (405,65,'02 · EVIDENCE','공개시점 검사·수집',['마감일 전일까지 · 최신 캐시 제외','누락·시점 불명은 알리고 진행']),
               (775,65,'03 · TASK','실행·정책·원문 연결',['엔진 · 정책 · 원문 해시','task_id · continuation 반환']),
               (775,240,'04 · CALLER LLM','교체 가능한 모델 실행',['목록 탐색 / 원문 읽기·교체','평가 제출 / 종료 · 후보별 예산']),
               (405,240,'05 · VALIDATE','동일 실행·인용 검사',['시각 · 정책 · 원문 · 과업 대조','바뀐 후보 과업만 다시 평가']),
               (35,240,'06 · METRICS','수용 평가를 지표로',['원문 판독 · 기간·출석 계산','누락 기준 제외 · 나머지 진행']),
               (35,415,'07 · RULES','규칙별 상태와 권고',['반대 신호 보존 · 긍정 게이트','재직·정지 구간과 회의 수 계산']),
               (405,415,'08 · CONSTRAINTS','교정과 보호 범위',['추출 오류 REVIEW만 근거별 교정','당시 공개 법령 · 표결 제약 적용']),
               (775,415,'09 · RESULT','권고와 처리 경로',['자동 준비 / 일부·전체 수동','사람 미검토 · 실제 투표 전송 없음'])]
        for args in boxes:box(*args)
        for d in ['M325 124H405','M695 124H775','M920 183V240','M775 299H695','M405 299H325','M180 358V415','M325 474H405','M695 474H775']:line(d)
        parts.append('<text x="35" y="579" class="s">시점 고정 하네스 선택 시의 흐름. 현재 정책의 과거 적용 실험이며 의미 정확성과 모델 기억 제거는 인증하지 않음.</text>')
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
    heading = soup.find('h2', string='현황·로드맵')
    if heading is None:
        raise ValueError('The canonical document needs a 현황·로드맵 section.')
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
        if paragraph.get_text().startswith(('저장·배포 상태', '동기화 기준')):
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
document_date = re.search(r'^updated: (\d{4}-\d{2}-\d{2})$', raw, re.M).group(1)
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
for quote in soup.find_all('blockquote'):
    label = quote.find('strong')
    if label and label.get_text(strip=True) in {'결론 요약', '설계 결론 요약'}:
        classes = quote.get('class', [])
        if isinstance(classes, str):
            classes = classes.split()
        quote['class'] = [*classes, 'conclusion-summary']
nav=[]
headings_by_title = {}
top_titles = [heading.get_text(' ', strip=True) for heading in soup.find_all('h2')]
if top_titles != list(SECTIONS):
    raise ValueError(f'The reading edition requires these sections in order: {list(SECTIONS)}')
for heading in soup.find_all(['h2', 'h3', 'h4', 'h5', 'h6']):
    title = heading.get_text(' ', strip=True)
    headings_by_title.setdefault(title, heading)
    if heading.name == 'h2':
        heading['id'], description = SECTIONS[title]
        nav.append(f'<a href="#{heading["id"]}"><span class="nav-number">{len(nav)+1:02}</span>'
                   f'<span><strong>{h(title)}</strong><small>{h(description)}</small></span></a>')
    else:
        heading['id'] = LEGACY_IDS.get(title, 'detail-' + hashlib.sha256(title.encode()).hexdigest()[:12])
    if title in ['판정 로직','확장 아키텍처와 운영 계약','기업 발견·분쟁 추적·대량 검토 (0.7.0)']:
        kind={'판정 로직':'runtime','확장 아키텍처와 운영 계약':'updates','기업 발견·분쟁 추적·대량 검토 (0.7.0)':'governance'}[title]
        svg=diagram(kind);(OUT/f'{kind}-flow.svg').write_text(svg)
        figure=BeautifulSoup(f'<figure class="diagram">{svg}<figcaption>{"미구현 목표 설계" if kind=="updates" else "현재 구현 흐름"} · <a href="{kind}-flow.svg">도식 파일 열기</a></figcaption></figure>','html.parser')
        heading.insert_after(figure)
preserve_legacy_fragments(soup, headings_by_title)
fold_document_details(soup)
for table in soup.find_all('table'):
    wrapper=soup.new_tag('div',attrs={'class':'table-scroll'});table.wrap(wrapper)
policy=json.loads(POLICY.read_text())
(OUT/'policy.json').write_text(json.dumps(policy,ensure_ascii=False,indent=2)+'\n')
(OUT/'assessment.schema.json').write_text(json.dumps(GuidelineAssessment.model_json_schema(),ensure_ascii=False,indent=2)+'\n')
(OUT/'workflow.schema.json').write_text(json.dumps(WorkflowSettings.model_json_schema(),ensure_ascii=False,indent=2)+'\n')
(OUT/'governance.schema.json').write_text(json.dumps(GovernanceAssessment.model_json_schema(),ensure_ascii=False,indent=2)+'\n')
(OUT/'harness.schema.json').write_text(json.dumps(HarnessRequest.model_json_schema(),ensure_ascii=False,indent=2)+'\n')
(OUT/'research.schema.json').write_text(json.dumps(ResearchQuery.model_json_schema(),ensure_ascii=False,indent=2)+'\n')
(OUT/'model-actions.schema.json').write_text(json.dumps(TypeAdapter(ModelAction).json_schema(),ensure_ascii=False,indent=2)+'\n')
(OUT/'structure-contract.json').write_text(json.dumps({
    'contract': STRUCTURE_CONTRACT, 'artifact_kind': 'runtime_schema_bundle',
    'request': StructureRequest.model_json_schema(), 'assessment': StructureAssessment.model_json_schema(),
    'item_schemas': {k: v.model_json_schema() for k, v in {
        'facts': StructureFact, 'gaps': StructureGap, 'findings': StructureFinding, 'judgments': StructureJudgment}.items()},
    'fact_data_schemas': {k: v.model_json_schema() for k, v in DATA_TYPES.items()},
    'human_reviewed': False}, ensure_ascii=False, indent=2)+'\n')
manifest={'generated_from':str(SOURCE.relative_to(ROOT)),'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),
          'policy_version':policy['version'],'policy_file_sha256':hashlib.sha256(POLICY.read_bytes()).hexdigest(),
          'assessment_contract':'opm-llm-assessment/6','harness_contract':HARNESS_CONTRACT,
          'research_schema':'research.schema.json','model_actions_schema':'model-actions.schema.json',
          'structure_runtime_contract':'structure-contract.json',
          'governance_contract':'1','document_date':document_date,'status':'local_document_export',
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
.conclusion-summary{border:1px solid #b8cbbf;border-left:4px solid var(--green);background:#edf5ed;border-radius:9px;padding:18px 22px;margin:18px 0 24px;color:var(--ink);font-size:14px;line-height:1.75}.conclusion-summary p{margin:10px 0}.conclusion-summary>p:first-child{margin-top:0;color:var(--green);font-size:17px}.conclusion-summary>p:last-child{margin-bottom:0}.conclusion-summary ul{margin:12px 0;padding-left:20px}.conclusion-summary li{margin:9px 0}
.roadmap-panels{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:24px 0 30px}.roadmap-panel{border:1px solid var(--line);border-radius:10px;background:white;padding:22px}.roadmap-panel>h3{font-size:21px;margin:0 0 16px}.completed>h3{color:var(--green)}.remaining>h3{color:#825b1f}.roadmap-panel ol{list-style:none;padding:0;margin:0}.roadmap-panel li{margin:0;padding:18px 0;border-top:1px solid var(--line);scroll-margin-top:24px}.roadmap-panel li:first-child{border-top:0;padding-top:0}.roadmap-panel li:last-child{padding-bottom:0}.roadmap-panel h4{font-size:16px;margin:0 0 7px;line-height:1.5}.roadmap-panel h4 span{font-variant-numeric:tabular-nums;color:var(--muted);font-size:13px;margin-right:5px}.roadmap-panel p{font-size:14px;line-height:1.75;margin:6px 0}.roadmap-panel .phase-limit{font-size:12px;color:var(--muted)}.roadmap-panel details{font-size:12px;color:var(--muted);margin-top:8px}.roadmap-panel summary{cursor:pointer}.roadmap-panel details p{font-size:12px}.release-status{padding:16px 18px;background:#edf1eb;border:1px solid var(--line);border-radius:8px;font-size:13px!important}.roadmap-overview>ul{font-size:14px}
@media(max-width:1100px){main{padding:30px}.roadmap-chart{gap:10px}.roadmap-chart li:not(:last-child)::after{right:-10px;font-size:13px}.roadmap-chart a{padding:12px 8px}.roadmap-chart .phase-current a{padding:11px 7px}.roadmap-chart strong{font-size:14px}.roadmap-panel{padding:18px}}
@media(max-width:950px) and (min-width:761px){.layout{grid-template-columns:185px minmax(0,1fr)}}
@media(max-width:950px){main{padding:26px 20px}header{margin-bottom:22px}h1{font-size:29px}.eyebrow{font-size:10px}.roadmap-chart{grid-template-columns:1fr;gap:12px;margin-top:18px}.roadmap-chart a,.roadmap-chart .phase-current a{min-height:70px;padding:13px 16px;display:grid;grid-template-columns:36px 78px minmax(0,1fr);align-items:center;gap:12px}.roadmap-chart .phase-current a{padding:12px 15px}.phase-number{font-size:23px}.phase-status{margin:0;font-size:11px}.roadmap-chart strong{font-size:15px}.roadmap-chart li:not(:last-child)::after{content:'↓';top:auto;bottom:-13px;left:28px;right:auto;line-height:13px}.roadmap-panels{grid-template-columns:1fr;gap:16px}.next-action{padding:16px}.current-position{padding:13px 14px}.roadmap-overview>h2{font-size:25px}.actions{gap:7px}.actions a,.actions button{font-size:12px;padding:6px 9px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
@media print{main{padding:0}h1{font-size:25pt}.roadmap-chart{grid-template-columns:repeat(6,minmax(0,1fr));gap:7px;break-inside:avoid}.roadmap-chart a,.roadmap-chart .phase-current a{display:flex;min-height:110px;padding:10px 7px}.roadmap-chart strong{font-size:9pt}.phase-number{font-size:17pt}.phase-status{font-size:8pt;margin:5px 0}.roadmap-chart li:not(:last-child)::after{content:'→';top:45px;bottom:auto;left:auto;right:-8px;font-size:10px}.roadmap-panels{grid-template-columns:1fr 1fr;gap:12px}.roadmap-panel{padding:12px}.roadmap-panel li,.next-action,.current-position{break-inside:avoid}.roadmap-panel p,.next-action p{font-size:9pt}.roadmap-panel h4{font-size:10pt}.chart-caption{display:none}}
'''
css += '''
[hidden]{display:none!important}article,aside,nav,.document-detail,.detail-body{min-width:0}article{overflow-wrap:anywhere}nav a{display:grid;grid-template-columns:24px minmax(0,1fr);gap:8px;padding:13px 0;border-bottom:1px solid var(--line);white-space:normal}nav a strong{display:block;font-size:13px;font-weight:700;color:var(--ink)}nav a small{display:block;font-size:11px;margin-top:5px;font-weight:400;color:var(--muted)}.nav-number{font-size:11px;font-weight:800;font-variant-numeric:tabular-nums;color:var(--green);padding-top:2px}a:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--green);outline-offset:4px}h4,h5,h6{line-height:1.55;margin:26px 0 12px}h4{font-size:17px}h5,h6{font-size:15px}.document-controls{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin:0 0 24px}.document-controls button{border:1px solid var(--line);border-radius:5px;background:#fff;color:var(--green);padding:7px 11px;font:inherit;font-size:12px;cursor:pointer}.controls-status{font-size:12px;color:var(--muted)}.document-detail{margin:22px 0;border:1px solid var(--line);border-radius:9px;background:#fff}.document-detail>summary{padding:17px 20px;cursor:pointer;line-height:1.55;color:var(--green);overflow-wrap:anywhere}.document-detail>summary h3{display:inline;margin:0;font-size:18px;letter-spacing:-.3px;color:var(--ink)}.document-detail>summary::marker{font-size:14px;color:var(--green)}.document-detail[open]>summary{border-bottom:1px solid var(--line)}.detail-body{padding:6px 22px 20px}.detail-body> :first-child{margin-top:15px}.detail-body> :last-child{margin-bottom:0}.fragment-alias{display:inline-block;width:0;height:0;overflow:hidden;vertical-align:top}.table-scroll,.diagram,pre{max-width:100%}.document-detail .diagram svg{min-width:680px}
@media(max-width:760px){nav{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 14px;overflow:visible;white-space:normal}nav a{padding:9px 0;grid-template-columns:20px minmax(0,1fr);gap:5px}nav a strong{font-size:12px}nav a small{font-size:10px;margin-top:3px}.document-detail>summary{padding:15px}.document-detail>summary h3{font-size:17px}.detail-body{padding:3px 15px 17px}.document-controls{gap:7px}.controls-status{flex-basis:100%}.detail-body th,.detail-body td{padding:12px}.actions a,.actions button{overflow-wrap:anywhere;max-width:100%}}
@media print{.document-controls{display:none}.document-detail{border:0;border-radius:0;margin:20px 0;background:transparent}.document-detail>summary,.document-detail[open]>summary{padding:0;border:0;list-style:none;break-after:avoid}.document-detail>summary::-webkit-details-marker{display:none}.document-detail>summary h3{font-size:13pt}.detail-body{padding:0}.document-detail .diagram svg{min-width:0}.document-detail>summary::marker{content:''}}
'''
reader_script = '''
(() => {
  const article = document.getElementById('document-body');
  const controls = document.querySelector('.document-controls');
  const allDetails = () => Array.from(article.querySelectorAll('details'));
  controls.hidden = false;
  for (const button of controls.querySelectorAll('button[data-expand]')) {
    button.addEventListener('click', () => {
      const expand = button.dataset.expand === 'true';
      const details = allDetails();
      details.forEach(detail => { detail.open = expand; });
      document.getElementById('detail-status').textContent =
        `상세 ${details.length}개를 모두 ${expand ? '펼쳤습니다' : '접었습니다'}.`;
    });
  }
  function revealFragment(fragment, scroll = true) {
    let id;
    try { id = decodeURIComponent(fragment.replace(/^#/, '')); }
    catch { return; }
    const target = id && document.getElementById(id);
    if (!target) return;
    for (let ancestor = target; ancestor; ancestor = ancestor.parentElement) {
      if (ancestor.tagName === 'DETAILS') ancestor.open = true;
    }
    if (scroll) requestAnimationFrame(() => {
      (target.closest('h2,h3,h4,h5,h6') || target).scrollIntoView({block: 'start'});
    });
  }
  window.addEventListener('hashchange', () => revealFragment(location.hash));
  document.addEventListener('click', event => {
    const anchor = event.target.closest('a[href^="#"]');
    if (anchor) revealFragment(anchor.getAttribute('href'));
  });
  revealFragment(location.hash);
  let printStates = null;
  window.addEventListener('beforeprint', () => {
    if (printStates) return;
    printStates = allDetails().map(detail => [detail, detail.open]);
    printStates.forEach(([detail]) => { detail.open = true; });
  });
  window.addEventListener('afterprint', () => {
    if (!printStates) return;
    printStates.forEach(([detail, wasOpen]) => { detail.open = wasOpen; });
    printStates = null;
  });
})();
'''
page=f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OPM guideline v2 · 로드맵, 명세, 설계</title><style>{css}</style></head><body><a class="skip" href="#document">본문 바로가기</a><div class="layout"><aside><div class="brand">OPM / Guidelines</div><div class="edition">명세·설계 문서 · {h(document_date.replace('-', '.'))}</div><nav aria-label="문서 목차">{''.join(nav)}</nav></aside><main id="document"><header><div class="eyebrow">OPEN PROXY MCP · {h(policy['version'])} · ASTRA</div><h1>Guideline v2 · 진행 현황과 설계</h1><p>판단 원칙부터 검증과 배포까지 다섯 본문으로 보고, Appendix에서 하네싱의 동작과 이유를 확인합니다.</p><div class="actions"><button onclick="window.print()">인쇄 / PDF</button><a href="policy.json">현재 정책 JSON</a><a href="assessment.schema.json">평가 입력</a><a href="workflow.schema.json">보팅 설정</a><a href="governance.schema.json">거버넌스 평가</a><a href="harness.schema.json">시점 고정 실행</a><a href="../../wiki/decisions/{SOURCE.name}">문서 원본</a></div></header><div class="document-controls" hidden><button type="button" data-expand="true" aria-controls="document-body">상세 모두 펼치기</button><button type="button" data-expand="false" aria-controls="document-body">상세 모두 접기</button><span id="detail-status" class="controls-status" aria-live="polite">세부 명세와 과거 기록은 필요한 항목만 펼쳐 볼 수 있습니다.</span></div><article id="document-body">{soup}</article><footer>이 HTML은 정본 Markdown과 현재 정책·평가 스키마에서 생성한 읽기용 문서입니다. 운영 배포나 후보 재평가를 뜻하지 않습니다.<br>정본 SHA-256: {manifest['source_sha256']}<br>재생성: scripts/render_guideline_spec.py · <a href="manifest.json">생성 명세</a></footer></main></div><script>{reader_script}</script></body></html>'''
(OUT/'index.html').write_text(page)
print(json.dumps({'output':str(OUT/'index.html'),'sections':len(nav),'policy':policy['version']},ensure_ascii=False))
