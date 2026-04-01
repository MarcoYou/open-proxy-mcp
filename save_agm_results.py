"""KOSPI 200 주총결과 KIND 크롤링 → JSON 저장"""

import asyncio
import json
import re
import time
import os
from open_proxy_mcp.dart.client import DartClient, DartClientError
from bs4 import BeautifulSoup
from collections import Counter


OUTPUT_DIR = "OpenProxy/frontend/src/data/pipeline_result"


def parse_agm_result_full(html: str) -> list[dict]:
    """주총결과 KIND HTML → 섹션별 테이블 구조화"""
    soup = BeautifulSoup(html, 'lxml')
    elements = []
    for elem in soup.find_all(['span', 'table']):
        if elem.name == 'span':
            text = elem.get_text(strip=True)
            if not text:
                continue
            m = re.search(r'[【\[](.+?)[】\]]', text)
            if m:
                elements.append(('section', m.group(1), elem))
            elif '주주총회' in text and '결과' in text and len(text) < 30:
                elements.append(('section', text, elem))
        elif elem.name == 'table':
            if elem.find_all('tr'):
                elements.append(('table', None, elem))

    sections = []
    current = {'title': '정기주주총회 결과', 'tables': []}

    for etype, title, elem in elements:
        if etype == 'section':
            if current['tables']:
                sections.append(current)
            current = {'title': title, 'tables': []}
        elif etype == 'table':
            rows_data = []
            for row in elem.find_all('tr'):
                cells = []
                for cell in row.find_all(['td', 'th']):
                    c = {'text': cell.get_text(strip=True)}
                    cs = cell.get('colspan')
                    rs = cell.get('rowspan')
                    if cs and cs != '1':
                        c['colspan'] = int(cs)
                    if rs and rs != '1':
                        c['rowspan'] = int(rs)
                    if len(c) == 1:
                        cells.append(c['text'])
                    else:
                        cells.append(c)
                if cells:
                    rows_data.append(cells)
            if rows_data:
                current['tables'].append({
                    'row_count': len(rows_data),
                    'rows': rows_data,
                })

    if current['tables']:
        sections.append(current)
    return sections


def extract_vote_results(sections: list[dict]) -> list[dict]:
    """안건 세부내역 테이블에서 투표 결과 추출"""
    for sec in sections:
        if '안건' in sec['title'] and '세부' in sec['title']:
            for table in sec['tables']:
                rows = table['rows']
                if len(rows) < 3:
                    continue
                # 헤더 확인
                first_text = ' '.join(
                    c if isinstance(c, str) else c.get('text', '')
                    for c in rows[0]
                )
                if '번호' not in first_text or '가결' not in first_text:
                    continue

                data_start = 1
                second_text = ' '.join(
                    c if isinstance(c, str) else c.get('text', '')
                    for c in rows[1]
                ) if len(rows) > 1 else ''
                if '찬성률' in second_text:
                    data_start = 2

                items = []
                for row in rows[data_start:]:
                    texts = [c if isinstance(c, str) else c.get('text', '') for c in row]
                    if len(texts) < 5 or not texts[0] or texts[0] == '-':
                        continue

                    try:
                        iss = float(texts[4]) if texts[4] else None
                        vot = float(texts[5]) if len(texts) > 5 and texts[5] else None
                        attend = round(iss / vot * 100, 1) if iss and vot and vot > 0 else None
                    except (ValueError, ZeroDivisionError):
                        iss = vot = attend = None

                    items.append({
                        'number': texts[0],
                        'resolution_type': texts[1] if len(texts) > 1 else '',
                        'agenda': texts[2] if len(texts) > 2 else '',
                        'passed': texts[3] if len(texts) > 3 else '',
                        'approval_rate_issued': texts[4] if len(texts) > 4 else '',
                        'approval_rate_voted': texts[5] if len(texts) > 5 else '',
                        'opposition_rate': texts[6] if len(texts) > 6 else '',
                        'remark': texts[7] if len(texts) > 7 else '',
                        'estimated_attendance': attend,
                    })
                return items
    return []


def calc_attendance(vote_items: list[dict]) -> dict | None:
    """보통결의 최빈값으로 추정 참석률 계산"""
    ordinary = [
        item['estimated_attendance']
        for item in vote_items
        if '보통' in item.get('resolution_type', '') and item.get('estimated_attendance')
    ]
    if not ordinary:
        return None
    most_common = Counter(ordinary).most_common(1)[0]
    return {
        'estimated_attendance': most_common[0],
        'basis_count': most_common[1],
        'basis_type': '보통결의',
    }


async def main():
    client = DartClient()

    with open('filing_tracker.json') as f:
        tracker = json.load(f)

    companies = []
    for code, info in tracker.items():
        stock_code = info.get('stockCode', code)
        name = info.get('name', '')
        # 파일명용 이름 정리 (특수문자 제거)
        safe_name = re.sub(r'[^\w가-힣]', '', name)
        companies.append({
            'stock_code': stock_code,
            'name': name,
            'safe_name': safe_name,
        })

    print(f'Total companies: {len(companies)}')
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    ok = 0
    no_result = 0
    kind_fail = 0
    saved = 0
    start = time.time()

    summary = []

    for i, comp in enumerate(companies):
        try:
            corp = await client.lookup_corp_code(comp['stock_code'])
            if not corp:
                continue

            # 주총결과 찾기
            filings = await client.search_filings(
                bgn_de='20260101', end_de='20260401',
                corp_code=corp['corp_code'], pblntf_ty='I',
            )

            rf = None
            for item in filings.get('list', []):
                if '주주총회결과' in item.get('report_nm', ''):
                    rf = item
                    break

            if not rf:
                no_result += 1
                summary.append({'name': comp['name'], 'status': 'NO_RESULT'})
                continue

            rcept_no = rf['rcept_no']
            acptno = rcept_no[:8] + rcept_no[8:].replace('80', '00', 1)

            # KIND 크롤링
            try:
                html = await client.kind_fetch_document(acptno)
            except Exception:
                try:
                    html = await client.kind_fetch_document(rcept_no)
                except Exception:
                    kind_fail += 1
                    summary.append({'name': comp['name'], 'status': 'KIND_FAIL', 'rcept_no': rcept_no})
                    continue

            # 파싱
            sections = parse_agm_result_full(html)
            vote_items = extract_vote_results(sections)
            attendance = calc_attendance(vote_items)

            output = {
                'corp_name': comp['name'],
                'stock_code': comp['stock_code'],
                'rcept_no': rcept_no,
                'rcept_dt': rf.get('rcept_dt', ''),
                'attendance': attendance,
                'vote_results': vote_items,
                'sections': sections,
            }

            # 저장
            filename = f"A{comp['stock_code']}_result_{comp['safe_name']}.json"
            filepath = os.path.join(OUTPUT_DIR, filename)
            with open(filepath, 'w') as f:
                json.dump(output, f, ensure_ascii=False, indent=2)

            ok += 1
            saved += 1

            sec_titles = [s['title'] for s in sections]
            n_items = len(vote_items)
            att = attendance['estimated_attendance'] if attendance else None
            summary.append({
                'name': comp['name'],
                'status': 'OK',
                'sections': sec_titles,
                'vote_items': n_items,
                'attendance': att,
            })

        except Exception as e:
            summary.append({'name': comp['name'], 'status': 'ERROR', 'error': str(e)[:80]})

        if (i + 1) % 10 == 0 or i == len(companies) - 1:
            elapsed = time.time() - start
            print(f'[{i+1}/{len(companies)}] OK={ok} NO_RESULT={no_result} KIND_FAIL={kind_fail} ({elapsed:.0f}s)')

    elapsed = time.time() - start
    print(f'\n=== 완료 ({elapsed:.0f}s) ===')
    print(f'OK: {ok} | NO_RESULT: {no_result} | KIND_FAIL: {kind_fail} | Saved: {saved}')

    # 참석률 분포
    att_values = [s['attendance'] for s in summary if s.get('attendance')]
    if att_values:
        print(f'\n=== 추정 참석률 분포 ===')
        print(f'  평균: {sum(att_values)/len(att_values):.1f}%')
        print(f'  최소: {min(att_values):.1f}%')
        print(f'  최대: {max(att_values):.1f}%')
        print(f'  중위: {sorted(att_values)[len(att_values)//2]:.1f}%')

    # 섹션 패턴
    all_titles = Counter()
    for s in summary:
        for t in s.get('sections', []):
            all_titles[t] += 1
    print(f'\n=== 섹션 패턴 ===')
    for title, count in all_titles.most_common(15):
        print(f'  {title}: {count}건')

    # 요약 저장
    with open('/tmp/agm_result_survey_full.json', 'w') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    asyncio.run(main())
