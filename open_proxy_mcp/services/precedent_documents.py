"""Bounded official court bulletin discovery and dated summary reading.

This is a source adapter, not a precedent interpretation or legality engine.
Court bulletin dates establish the displayed summary's publication boundary;
judgment dates never substitute for publication. Attachments stay unread and
their historical versions are unproven. No API credential, LLM provider, OCR,
arbitrary URL fetch, or persistent user-result storage is introduced here.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from open_proxy_mcp.clock import KST
from open_proxy_mcp.dart.as_of import get_as_of, note_strict_exclusion, publication_day


BASE = 'https://sc.scourt.go.kr'
LIST_PATH = '/portal/news/NewsListAction.work'
READ_PATH = '/portal/news/NewsViewAction.work'
BOARD = 'precedent_bulletin'
MAX_DOCUMENT_BYTES = 3 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_ROWS = 100
MAX_ATTACHMENTS = 20
LIMITATIONS = [
    '법원 판례속보 한 페이지의 검색 결과이며 전체 판례를 포괄하지 않는다.',
    '게시일과 판결 선고일은 다르다. 공개시점 미확인 자료는 판단 근거에서 제외한다.',
    '현재 표시된 게시일을 확인하며 표시되지 않은 사후 변경이나 당시 첨부 버전까지 증명하지 않는다.',
    '법인 유형·당시 법령·사실관계·심급·확정 여부와 해당 안건의 관련성은 별도로 평가해야 한다.',
]


def validate_request(request: dict) -> dict:
    """Accept only a concrete official bulletin identity, never client dates/URLs."""
    if (not isinstance(request, dict) or set(request) != {'type', 'board', 'seqnum'}
            or request.get('type') != 'court_precedent' or request.get('board') != BOARD
            or not isinstance(request.get('seqnum'), str)
            or not re.fullmatch(r'[1-9][0-9]{0,11}', request['seqnum'])):
        raise ValueError('court precedent requires type, precedent_bulletin board, and numeric seqnum')
    return dict(request)


def _cutoff(as_of: str) -> str:
    if (not isinstance(as_of, str) or not re.fullmatch(r'[0-9]{8}', as_of)
            or not publication_day(as_of)
            or as_of > datetime.now(KST).strftime('%Y%m%d')):
        raise ValueError('court precedent requires a nonfuture YYYYMMDD as_of')
    return min(as_of, get_as_of()) if get_as_of() else as_of


def _identity(seqnum: str) -> dict:
    return {'source_id': f'court:{BOARD}:{seqnum}',
            'source_url': BASE + READ_PATH + '?' + urlencode({'gubun': '4', 'seqnum': seqnum})}


def _request(seqnum: str) -> dict:
    return {'type': 'court_precedent', 'board': BOARD, 'seqnum': seqnum}


async def _fetch(client, path: str, params: dict) -> bytes:
    # Paths and parameter names come only from this module. Korean input must
    # use the court portal's EUC-KR query encoding, not httpx's UTF-8 params.
    url = BASE + path + '?' + urlencode(params, encoding='euc-kr', errors='strict')
    await client._throttle_web()
    async with client._http.stream('GET', url, timeout=20, follow_redirects=False) as response:
        if response.status_code != 200:
            raise ValueError('court response unavailable')
        length = response.headers.get('content-length', '')
        if length.isdigit() and int(length) > MAX_DOCUMENT_BYTES:
            raise ValueError('court response size limit')
        chunks, total = [], 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_DOCUMENT_BYTES:
                raise ValueError('court response size limit')
            chunks.append(chunk)
    return b''.join(chunks)


def _day(value: str) -> str | None:
    value = re.sub(r'\s+', '', value)
    if re.fullmatch(r'[0-9]{4}\.[0-9]{2}\.[0-9]{2}', value):
        value = value.replace('.', '-')
    return publication_day(value)


def _status(published: str | None, cutoff: str) -> str:
    return 'publication_unknown' if not published else 'after_as_of' if published > cutoff else 'unread'


def _exclude(status: str) -> None:
    note_strict_exclusion('court_precedent', reason='after_cutoff' if status == 'after_as_of' else status)


def _link_identity(href: str) -> str | None:
    if not isinstance(href, str) or len(href) > 2000:
        return None
    parsed = urlsplit(urljoin(BASE, href))
    if (parsed.scheme != 'https' or parsed.netloc != 'sc.scourt.go.kr'
            or parsed.path != READ_PATH or parsed.fragment):
        return None
    params = parse_qs(parsed.query, keep_blank_values=True)
    if (params.get('gubun') != ['4'] or len(params.get('seqnum', [])) != 1
            or not set(params) <= {'gubun', 'seqnum', 'pageIndex', 'searchWord', 'searchOption', 'type'}):
        return None
    try:
        return validate_request(_request(params['seqnum'][0]))['seqnum']
    except ValueError:
        return None


async def discover_precedents(client, query: str, as_of: str, page: int = 1) -> dict:
    """Read one bounded results page; candidate handles are not read evidence."""
    cutoff = _cutoff(as_of)
    if (not isinstance(query, str) or not query.strip() or len(query) > 40
            or any(ord(c) < 32 for c in query)
            or not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 1000):
        raise ValueError('court search requires a short query and page between 1 and 1000')
    try:
        if len(query.encode('euc-kr')) > 40:
            raise ValueError
    except (UnicodeEncodeError, ValueError):
        raise ValueError('court query must fit the portal EUC-KR 40-byte search limit') from None
    result = {'status': 'fetch_failed', 'query': query, 'page': page, 'as_of': cutoff,
              'candidates': [], 'excluded': [], 'complete': False,
              'limitations': list(LIMITATIONS), 'truncated': False}
    try:
        raw = await asyncio.wait_for(_fetch(client, LIST_PATH, {
            'gubun': '4', 'type': '5', 'searchWord': query, 'pageIndex': page}), 25)
        soup = BeautifulSoup(raw, 'html.parser')
        table = soup.select_one('table.tableHor')
        if table is None:
            raise ValueError('court listing not recognized')
        headers = [th.get_text(' ', strip=True) for th in table.select('thead th')]
        if '작성일' not in headers or '제목' not in headers:
            raise ValueError('court listing header not recognized')
        date_index = headers.index('작성일')
        rows = table.select('tbody > tr')
        result['truncated'] = len(rows) > MAX_ROWS
        candidates, excluded, seen = [], [], set()
        for row in rows[:MAX_ROWS]:
            cells = row.find_all('td', recursive=False)
            links = row.select('td.tit a[href]')
            if len(links) != 1 or len(cells) <= date_index:
                continue
            seqnum = _link_identity(links[0].get('href'))
            if not seqnum or seqnum in seen:
                continue
            seen.add(seqnum)
            published = _day(cells[date_index].get_text(' ', strip=True))
            status = _status(published, cutoff)
            identity = {**_identity(seqnum), 'published': published, 'status': status}
            if status != 'unread':
                # Neither future titles nor read suggestions leave the boundary.
                excluded.append(identity)
                _exclude(status)
                continue
            candidates.append({**identity, 'title': links[0].get_text(' ', strip=True)[:1000],
                               'read_source': _request(seqnum), 'date_basis': 'court_list_posted_date'})
        return {**result, 'status': 'read', 'candidates': candidates, 'excluded': excluded,
                'list_sha256': hashlib.sha256(raw).hexdigest(),
                'next_page': page + 1 if len(rows) >= 10 and page < 1000 else None}
    except Exception:
        return result


def _field_values(table, labels: set[str]) -> list[str]:
    values = []
    for th in table.select('th'):
        if th.get_text('', strip=True) in labels:
            td = th.find_next_sibling('td')
            values.append(td.get_text(' ', strip=True) if td is not None else '')
    return values


def _modification_values(soup, table) -> list[str]:
    values = _field_values(table, {'수정일', '최종수정일', '수정일자', '최종수정일자', '변경일'})
    for meta in soup.select('meta[property="article:modified_time"], meta[name="last-modified"]'):
        value = meta.get('content', '')
        try:
            if 'T' in value:
                date = datetime.fromisoformat(value.replace('Z', '+00:00'))
                value = date.astimezone(KST).strftime('%Y%m%d') if date.tzinfo else date.strftime('%Y%m%d')
        except (ValueError, OverflowError):
            value = ''
        values.append(value)
    content = table.select_one('td.contArea')
    if content:
        for line in content.get_text('\n', strip=True).splitlines():
            match = re.fullmatch(r'\s*(?:최종\s*)?(?:수정|변경|업데이트)\s*일(?:자)?\s*[:：]\s*(.*?)\s*', line)
            if match:
                values.append(match[1])
    return values


def _version_status(soup, table, title: str, cutoff: str, published: str) -> str | None:
    values = _modification_values(soup, table)
    if values:
        days = [_day(value) for value in values]
        if any(day is None or day < published for day in days):
            return 'version_unproven'
        if any(day > cutoff for day in days):
            return 'updated_after_as_of'
    # A visible correction with no dated revision cannot be backdated to the
    # original posting. Ordinary references to amended laws are not this marker.
    elif re.search(r'^\s*(?:수정|정정|보완|업데이트)|[\[<(（【]\s*(?:수정|정정|보완|업데이트)\s*[\])>）】]', title):
        return 'version_unproven'
    return None


def _attachments(table) -> tuple[list[dict], int, bool]:
    anchors = []
    for th in table.select('th'):
        if th.get_text('', strip=True) == '첨부파일':
            cell = th.find_next_sibling('td')
            if cell:
                anchors.extend(cell.select('a[href]'))
    results, blocked = [], 0
    for anchor in anchors[:MAX_ATTACHMENTS]:
        raw = anchor.get('href', '')
        parsed = urlsplit(urljoin(BASE, raw))
        if (len(raw) > 1000 or parsed.scheme != 'https'
                or parsed.netloc not in {'www.scourt.go.kr', 'sc.scourt.go.kr'}
                or parsed.query or parsed.fragment
                or not re.fullmatch(r'/sjudge/[A-Za-z0-9_-]{1,160}\.(?:pdf|hwp|hwpx)', parsed.path, re.I)):
            blocked += 1
            continue
        results.append({'source_url': parsed.geturl(), 'title': anchor.get_text(' ', strip=True)[:300],
                        'status': 'unread', 'published': None,
                        'version_status': 'publication_unproven',
                        'hint': '첨부 본문 미독. 게시물 날짜만으로 현재 첨부 버전의 과거 공개를 보증하지 않는다.'})
    return results, blocked, len(anchors) > MAX_ATTACHMENTS


async def read_precedent_document(client, request: dict, as_of: str) -> dict:
    """Read only the identified bulletin summary after its publication gate."""
    request = validate_request(request)
    cutoff = _cutoff(as_of)
    result = {**_identity(request['seqnum']), 'as_of': cutoff, 'published': None,
              'status': 'fetch_failed', 'text': '', 'document_sha256': None,
              'source_kind': 'court_official_summary', 'attachments': [],
              'finality_status': 'not_assessed', 'limitations': list(LIMITATIONS)}
    try:
        raw = await asyncio.wait_for(_fetch(client, READ_PATH, {
            'gubun': '4', 'seqnum': request['seqnum']}), 25)
        soup = BeautifulSoup(raw, 'html.parser')
        for node in soup.select('input[name="seqnum"], input[name="gubun"]'):
            expected = request['seqnum'] if node.get('name') == 'seqnum' else '4'
            if node.get('value') != expected:
                return {**result, 'status': 'identity_mismatch'}
        table = soup.select_one('table.tableVer')
        if table is None or table.select_one('td.contArea') is None:
            raise ValueError('court bulletin not recognized')
        values = _field_values(table, {'작성일'})
        published = _day(values[0]) if len(values) == 1 else None
        result['published'] = published
        status = _status(published, cutoff)
        if status != 'unread':
            _exclude(status)
            return {**result, 'status': status}
        titles = _field_values(table, {'제목'})
        if len(titles) != 1:
            raise ValueError('court title not recognized')
        title = titles[0]
        version = _version_status(soup, table, title, cutoff, published)
        if version:
            _exclude(version)
            return {**result, 'status': version}
        content = table.select_one('td.contArea')
        for node in content.select('script, style, iframe, nav'):
            node.decompose()
        body = content.get_text('\n', strip=True)
        if not body or len(body) + len(title) > MAX_TEXT_CHARS:
            raise ValueError('court text unavailable or too large')
        attachments, blocked, truncated = _attachments(table)
        material = {'source_id': result['source_id'], 'published': published,
                    'source_kind': result['source_kind'], 'title': title, 'body': body,
                    'modifications': _modification_values(soup, table),
                    'attachments': attachments, 'blocked_attachment_count': blocked,
                    'attachments_truncated': truncated}
        # Session IDs, view counters and navigation are not the legal source.
        # Bind the exact admitted summary and relevant metadata for rereading.
        digest = hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()
        return {**result, 'status': 'read', 'title': title, 'text': title + '\n\n' + body,
                'document_sha256': digest, 'raw_document_sha256': hashlib.sha256(raw).hexdigest(),
                'hash_basis': 'court_summary_and_publication_metadata_v1',
                'date_basis': 'court_bulletin_posted_date',
                'publication_evidence_url': result['source_url'],
                'attachments': attachments, 'blocked_attachment_count': blocked,
                'attachments_truncated': truncated, 'human_review_status': 'not_reviewed'}
    except Exception:
        return result
