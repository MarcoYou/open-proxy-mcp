"""Summarize captured HTTP outcomes; never executes a model or invents a grade."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from markdown_it import MarkdownIt


def build(directory: Path) -> dict:
    def load(name):
        return json.loads((directory / name).read_text())
    proof = load('stage-proof.json')
    initial = load('initial.json')
    binding = initial['data']['guideline_harness']
    modes = [('valid', '정상 제출'), ('stale', 'QA 후 판단 변경 주입'),
             ('challenge', '한 안건의 QA 문제 주입'), ('missing-review', 'QA 누락 주입')]
    checks, runs = [], []
    for mode, label in modes:
        response = load(mode + '-response.json')
        data = response['data']
        harness = data['guideline_harness']
        result = data['guideline_application']['structure_tasks'][0]['assessment']
        assert harness['run_id'] == binding['run_id'], 'mixed_run_identity'
        assert harness['source_manifest'] == binding['source_manifest'], 'mixed_source_identity'
        rows = [{k: row.get(k) for k in ('agenda_id','agenda_title','decision','reason','voting_workflow')}
                for row in data['agenda_decisions'] if row['agenda_id'] in proof['supported_agenda_ids']]
        accepted = {j['agenda_id']: j['recommendation'] for j in result['judgments']}
        for row in rows:
            row['accepted_llm_recommendation'] = accepted.get(row['agenda_id'])
        runs.append({'mode': mode, 'label': '정상 제출' if mode == 'valid' else label,
            'accepted_judgments': len(accepted), 'rejected': result['rejected_items'],
            'completion_status': result['completion_status'], 'rows': rows,
            'human_reviewed': False, 'ballots_submitted': harness['ballots_submitted']})
    valid, stale, challenge, missing = runs
    normal_votes = {r['agenda_id']:r['accepted_llm_recommendation'] for r in valid['rows']}
    def peers_unchanged(run):
        return all(r['accepted_llm_recommendation'] == normal_votes[r['agenda_id']]
                   for r in run['rows'] if r['accepted_llm_recommendation'] is not None)
    checks = [
        {'gate':'normal_submission_applied','passed':valid['accepted_judgments'] == len(proof['supported_agenda_ids'])},
        {'gate':'stale_review_rejected_only_affected_agenda','passed':stale['accepted_judgments'] == valid['accepted_judgments']-1
            and peers_unchanged(stale)
            and any(r['code']=='stale_review_basis' for r in stale['rejected'])},
        {'gate':'challenge_isolated','passed':challenge['accepted_judgments'] == valid['accepted_judgments']-1
            and peers_unchanged(challenge)
            and any(r['code']=='unresolved_semantic_challenge' for r in challenge['rejected'])},
        {'gate':'missing_qa_never_accepted','passed':missing['accepted_judgments']==0
            and len(missing['rejected']) == valid['accepted_judgments']},
        {'gate':'no_ballots_sent','passed':all(run['ballots_submitted']==0 for run in runs)},
    ]
    assert all(c['passed'] for c in checks), 'http_proof_gate_failed'
    manifest = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob('*.json')
                if p.name not in {'report.json','presentation-qa.json'}}
    report = {'scope':'two_structure_agendas_only', 'run_id':binding['run_id'],
        'engine_bundle_sha256':binding['engine_bundle_sha256'], 'policy_sha256':binding['policy_sha256'],
        'cutoff_at':binding['cutoff_at'], 'runs':runs, 'http_checks':checks, 'stage_proof':proof,
        'model_quality':{'luna_executed':False,'noninferiority_established':False,
                         'independent_reviewer':False,'human_reviewed':False},
        'auth_scope':'loopback_header_bridge_to_existing_middleware; production_auth_not_evaluated',
        'input_manifest':manifest}
    (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines = ['# 단계별 하네스 · 실제 MCP 확인', '',
        '**정관·선출 구조의 단계별 실행과 오류 격리를 확인했다. Luna의 성능 검증은 아직 하지 않았다.**', '',
        '영원무역 2026년 3월 27일 정기주총의 정관 안건 두 개를 부분 검증했다. 마감은 주총일 00:00 KST, 날짜만 있는 공시는 전일까지 사용했다. 나머지 안건을 검증 완료나 무관으로 처리하지 않았다.', '',
        '## 실제 적용 결과', '', '| 안건 | 수용된 LLM 권고 | 최종 MCP 권고 | 처리 |', '|---|---|---|---|']
    labels = {'FOR':'찬성','AGAINST':'반대','REVIEW':'검토','NO_VOTE':'표결 없음'}
    for row in valid['rows']:
        lines.append(f"| {row['agenda_title']} | {labels[row['accepted_llm_recommendation']]} | {labels[row['decision']]} | 사람 검토 |")
    lines += ['', '이사의 수는 상한 8명 자체를 위반으로 판단하지 않았다. 시행시점의 실제 구성·업무량 대조가 남아 검토로 두었다. 집중투표 배제 금지는 삭제 제안에 찬성하되, 장래 적용례를 보존했다. 이 행은 기존 법률 제약이 남아 자동 실행 준비로 바뀌지 않았다.', '',
        '원문은 [소집공고](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260312001098)다. 제29조의 별도 시행일과 집중투표의 장래 최초 소집 조건을 제안 이력으로 기록했다. 유효 정관 전문이나 법률 적합성 인증은 아니다.', '',
        '## 오류를 넣었을 때', '', '| 입력 | 수용한 판단 | 거절 이유 | 독립 안건 |', '|---|---:|---|---|']
    for run in runs:
        codes = ', '.join(sorted({r['code'] for r in run['rejected']})) or '없음'
        lines.append(f"| {run['label']} | {run['accepted_judgments']} | {codes} | {'찬성 유지' if run['mode'] in {'stale','challenge'} else '대상 범위대로 처리'} |")
    lines += ['', '오류 행은 추가 판단으로 수용되지 않는다. 해당 행에 남아 있는 기존 엔진 권고는 새 LLM 판단의 수용 결과가 아니며, 처리 상태와 함께 읽어야 한다. 문제 주입은 오류 탐지 실험으로 실제 공시에서 잘못된 사실을 발견한 횟수가 아니다.', '',
        '## 무엇이 구현됐고 무엇이 남았나', '',
        '| 항목 | 상태 |', '|---|---|',
        '| 사실 → 해석 → 권고 → QA → MCP 응답 | 부분 안건 실제 실행 확인 |',
        '| 설정과 분리된 사실·표결 범위, 재사용 지문 | 구현·회귀 확인 |',
        '| 온톨로지의 기준·사실 종류·원천·QA 연결 | 구현·스키마 연결 검사 |',
        '| 모델 교체 callback과 단계별 예산·오류 격리 | 구현·대역 callback 검사 |',
        '| Luna 실제 판단·반복·독립 실무 검토 | 미실행 |',
        '| 전 안건 공통 제출·경합 선택 해결·관계 회사 자동 탐색 | 남음 |', '',
        '이번 판독과 QA는 같은 데스크탑 Astra 문맥에서 작성했다. 독립 리뷰나 Luna 실행으로 포장하지 않는다. 직접 유료 모델 API 호출과 실제 투표 전송은 0건이다. 로컬 HTTP에서 실제 도구·수집·제출·렌더링을 사용했으며, 로컬 인증 브리지는 운영 인증 검증 범위에 포함하지 않는다.', '',
        '해석·권고 단계는 고정된 사실의 인용문을 받아 입력을 줄인다. 판독·QA는 확보한 원문 발췌를 다시 읽으므로 총 호출 비용이나 정확도가 좋아졌다는 결론은 아직 낼 수 없다.', '',
        '[로드맵·이행현황](../guideline-specification-20260909/roadmap.html) · [가이드라인 Appendix](../guideline-specification-20260909/index.html#appendix) · [구조화 보고서](report.json)', '']
    markdown = '\n'.join(lines)
    (directory/'report.md').write_text(markdown)
    body = MarkdownIt('commonmark').enable('table').render(markdown)
    (directory/'index.html').write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>단계별 하네스 · MCP 검증</title><style>body{max-width:1050px;margin:40px auto;padding:0 22px;color:#19382d;background:#faf9f5;font-family:system-ui;line-height:1.75}h1{font-size:32px}h2{margin-top:38px}table{border-collapse:collapse;width:100%;background:white;display:block;overflow:auto}td,th{text-align:left;border:1px solid #c8d4cb;padding:10px 14px}th{background:#e8f1e9}a{color:#24674f}p{max-width:930px}</style><main>'+body+'</main></html>')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory',type=Path)
    report = build(parser.parse_args().directory)
    print(json.dumps({'http_checks':report['http_checks'],'luna_executed':False},ensure_ascii=False))
