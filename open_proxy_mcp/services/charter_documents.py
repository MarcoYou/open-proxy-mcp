"""Bounded, dated DART charter attachments and optional original-image reading.

The public attachment list and a receipt+dcm viewer are an explicit service
path. They do not change the generic viewer's strict historical guards. XML
is tried first; absence from its ZIP says nothing about DART attachment absence.
No PDF conversion, OCR dependency, provider SDK, or persistent result storage.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import re
import zipfile
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from open_proxy_mcp.dart.as_of import get_as_of, note_strict_exclusion, publication_day
from open_proxy_mcp.dart.client import _BLOCK_PAGE_MARKERS, _check_web_response, _require_strict_receipt

BASE = 'https://dart.fss.or.kr'
MAX_ATTACHMENTS = 50
MAX_SECTIONS = 12
MAX_VISUAL_PAGES = 24
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_VISUAL_BYTES = 24 * 1024 * 1024
MAX_TEXT_CHARS = 500_000
READ_TIMEOUT = 120
VISUAL_TIMEOUT = 65
VisualReader = Callable[[dict], Awaitable[dict]]
_visual_reader: VisualReader | None = None
_visual_reader_id = ''


def register_visual_reader(reader: VisualReader | None, *, reader_id: str = '') -> None:
    """Deployment hook; receives original bytes, never a provider credential.

    Callable input: {source_id, document_sha256, format, max_pages,
    pages:[{page, source_url, source_sha256, content:bytes, media_type}]}.
    A PDF is one document entry and the provider must enforce max_pages.
    Output: {reader_id, text, pages:[{page, text, uncertainties:[str]}]}.
    Optional page uncertain_spans:[{start,end,reason}] uses Python codepoint
    offsets in that page's text. Every uncertainty must be localized to avoid
    treating the whole page as uncertain. Reader confidence is not verification.
    PDF output additionally needs page_count. Text must equal page texts joined
    with two newlines. All such readings remain explicitly human-unreviewed.
    """
    global _visual_reader, _visual_reader_id
    if reader is not None and (not callable(reader) or not _reader_identity(reader_id)):
        raise ValueError('visual reader requires a callable and a reader_id')
    _visual_reader, _visual_reader_id = reader, reader_id if reader is not None else ''


def _reader_identity(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}', value))


def _cutoff(rcept_no: str, as_of: str) -> str:
    if (not isinstance(rcept_no, str) or not re.fullmatch(r'\d{14}', rcept_no)
            or not publication_day(rcept_no[:8]) or not isinstance(as_of, str)
            or not re.fullmatch(r'\d{8}', as_of) or not publication_day(as_of)):
        raise ValueError('charter attachment requires a valid receipt and YYYYMMDD as_of')
    return min(as_of, get_as_of()) if get_as_of() else as_of


def _identity(rc: str, dcm: str = '') -> dict:
    query = {'rcpNo': rc, **({'dcmNo': dcm} if dcm else {})}
    return {'type': 'dart_attachment', 'rcept_no': rc, 'dcm_no': dcm,
            'source_id': f'filing:{rc}:attachment:{dcm}' if dcm else f'filing:{rc}',
            'source_url': f'{BASE}/dsaf001/main.do?{urlencode(query)}'}


async def _fetch(client, path: str, params: dict, limit: int = MAX_DOCUMENT_BYTES) -> bytes:
    """Only internally constructed paths/parameters reach this streaming fetch."""
    await client._throttle_web()
    async with client._http.stream('GET', BASE + path, params=params, timeout=20,
                                   follow_redirects=False) as response:
        # Do not let the shared checker read a streaming body or expose URLs.
        if response.status_code != 200:
            if response.status_code in (403, 429, 503):
                from open_proxy_mcp.dart.client import _note_web_block
                _note_web_block(response.status_code, 'charter_attachment')
            raise ValueError('attachment response unavailable')
        length = response.headers.get('content-length', '')
        if length.isdigit() and int(length) > limit:
            raise ValueError('attachment size limit')
        chunks, total = [], 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > limit:
                raise ValueError('attachment size limit')
            chunks.append(chunk)
    body = b''.join(chunks)
    # Shared blocking detection also recognizes 200-status block pages.
    import httpx
    checked = httpx.Response(200, content=body)
    _check_web_response(checked, 'charter_attachment')
    if len(checked.text) < 4000 and any(marker in checked.text for marker in _BLOCK_PAGE_MARKERS):
        raise ValueError('attachment response blocked')
    return body


def _listing(raw: bytes, rc: str, cutoff: str) -> tuple[list[dict], bool]:
    soup = BeautifulSoup(raw, 'html.parser')
    options = soup.select('#att option')
    entries = []
    for option in options[:MAX_ATTACHMENTS + 1]:
        params = parse_qs(option.get('value', ''), strict_parsing=False)
        if set(params) != {'rcpNo', 'dcmNo'} or params['rcpNo'] != [rc]:
            continue
        dcm = params['dcmNo']
        if len(dcm) != 1 or not re.fullmatch(r'\d{1,20}', dcm[0]):
            continue
        title = option.get_text(' ', strip=True)
        if '정관' not in re.sub(r'\s+', '', title) and 'articles of' not in title.lower():
            continue
        match = re.match(r'\s*(\d{4})[.-](\d{2})[.-](\d{2})(?:\s|$)', title)
        published = publication_day(''.join(match.groups())) if match else None
        status = ('publication_unknown' if not published else
                  'publication_date_conflict' if published < rc[:8] else
                  'after_as_of' if published > cutoff else 'available')
        if status != 'available':
            note_strict_exclusion('charter_attachment', receipt=rc,
                                  reason='after_cutoff' if status == 'after_as_of' else status)
        entries.append({**_identity(rc, dcm[0]), 'title': title, 'published': published,
                        'status': status, 'date_basis': 'dated DART attachment option'})
    return entries, len(options) > MAX_ATTACHMENTS + 1


async def discover_charter_attachments(client, rcept_no: str, as_of: str) -> dict:
    """Inspect one receipt's public attachment list, including excluded candidates."""
    cutoff = _cutoff(rcept_no, as_of)
    result = {**_identity(rcept_no), 'attachments': [], 'complete': False,
              'acquisition': 'dart_attachment_list', 'as_of': cutoff}
    if rcept_no[:8] > cutoff:
        note_strict_exclusion('charter_attachment', receipt=rcept_no, reason='after_cutoff')
        return {**result, 'status': 'after_as_of'}
    _require_strict_receipt(rcept_no, 'charter_attachment')
    try:
        raw = await asyncio.wait_for(_fetch(client, '/dsaf001/main.do', {'rcpNo': rcept_no}), 25)
        entries, truncated = _listing(raw, rcept_no, cutoff)
        return {**result, 'status': 'read', 'attachments': entries,
                'truncated': truncated, 'complete': False,
                'list_sha256': hashlib.sha256(raw).hexdigest(),
                'hint': '현재 첨부 목록에서 확인한 후보. 빈 목록은 정관 미공시의 증거가 아니다.'}
    except Exception:
        return {**result, 'status': 'fetch_failed'}


async def _xml_attachment(client, rc: str, dcm: str) -> bytes | None:
    """An exact dcm filename is sufficient; title similarity is never identity."""
    try:
        data = await asyncio.wait_for(client._request_binary('document.xml', {'rcept_no': rc}), 25)
        if len(data) > MAX_DOCUMENT_BYTES:
            return None
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > 256 or sum(info.file_size for info in infos) > 40 * 1024 * 1024:
                return None
            matches = [info for info in infos if info.filename in (f'{dcm}.xml', f'{rc}_{dcm}.xml')]
            if len(matches) != 1 or matches[0].file_size > MAX_DOCUMENT_BYTES:
                return None
            return archive.read(matches[0])
    except Exception:
        return None


def _asset(src: str, dcm: str) -> tuple[str, dict] | None:
    """Allow only this attachment's DART download asset, without redirects."""
    if not isinstance(src, str) or len(src) > 2000:
        return None
    parsed = urlsplit(urljoin(BASE, src))
    if (parsed.scheme != 'https' or parsed.netloc != 'dart.fss.or.kr'
            or parsed.path != '/report/download.do' or parsed.fragment):
        return None
    params = parse_qs(parsed.query, keep_blank_values=True)
    if (set(params) != {'dcmNo', 'flNm'} or params['dcmNo'] != [dcm]
            or len(params['flNm']) != 1):
        return None
    filename = params['flNm'][0]
    if (not filename or len(filename) > 300 or any(char in filename for char in '/\\\x00\r\n')
            or '..' in filename or not re.search(r'\.(jpg|jpeg|png|gif|bmp|webp|pdf)$', filename, re.I)):
        return None
    safe = {'dcmNo': dcm, 'flNm': filename}
    return BASE + '/report/download.do?' + urlencode(safe), safe


def _format(raw: bytes) -> str:
    if raw.startswith(b'%PDF-'):
        return 'pdf'
    if raw.startswith((b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'GIF87a', b'GIF89a', b'BM')):
        return 'image'
    if raw.startswith(b'RIFF') and raw[8:12] == b'WEBP':
        return 'image'
    return 'html'


async def _read_visual(client, item: dict, originals: list[dict], reader: VisualReader | None) -> dict:
    visual = item['visual_reading']
    if reader is None or visual['blocked_references'] or visual['truncated']:
        return item
    try:
        payload_pages, total = [], 0
        for page in originals:
            content = page.get('content')
            if content is None:
                content = await _fetch(client, '/report/download.do', page['params'], MAX_PAGE_BYTES)
            total += len(content)
            if total > MAX_VISUAL_BYTES or _format(content) not in {'image', 'pdf'}:
                raise ValueError('visual source unreadable')
            page_record = {k: v for k, v in page.items() if k not in {'params', 'content'}}
            page_record.update(source_sha256=hashlib.sha256(content).hexdigest(), status='fetched',
                               media_type='application/pdf' if _format(content) == 'pdf' else 'image')
            payload_pages.append({**page_record, 'content': content})
        visual['pages'] = [{k: v for k, v in page.items() if k != 'content'} for page in payload_pages]
        result = await asyncio.wait_for(reader({'source_id': item['source_id'],
            'document_sha256': item['document_sha256'], 'format': visual['format'],
            'max_pages': MAX_VISUAL_PAGES, 'pages': payload_pages}), 45)
        reader_id = _visual_reader_id if reader is _visual_reader else result.get('reader_id')
        pages, text = result.get('pages'), result.get('text')
        if (not _reader_identity(reader_id) or not isinstance(text, str) or not text.strip()
                or len(text) > MAX_TEXT_CHARS or not isinstance(pages, list) or not pages
                or len(pages) > MAX_VISUAL_PAGES):
            raise ValueError('invalid visual reader response')
        page_count = result.get('page_count') if visual['format'] == 'pdf' else len(originals)
        if type(page_count) is not int or page_count != len(pages):
            raise ValueError('incomplete visual reading')
        for index, page in enumerate(pages, 1):
            if (not isinstance(page, dict) or type(page.get('page')) is not int or page['page'] != index
                    or not isinstance(page.get('text'), str) or not page['text'].strip()
                    or not isinstance(page.get('uncertainties'), list)
                    or len(page['uncertainties']) > 30
                    or any(not isinstance(u, str) or len(u) > 1000 for u in page['uncertainties'])):
                raise ValueError('invalid page provenance')
            spans = page.get('uncertain_spans', [])
            if not isinstance(spans, list) or len(spans) > 100:
                raise ValueError('invalid uncertainty spans')
            for span in spans:
                if (not isinstance(span, dict) or set(span) != {'start', 'end', 'reason'}
                    or type(span['start']) is not int or type(span['end']) is not int
                    or not 0 <= span['start'] < span['end'] <= len(page['text'])
                    or span['reason'] not in page['uncertainties']):
                    raise ValueError('invalid uncertainty span')
        if text != '\n\n'.join(page['text'] for page in pages):
            raise ValueError('page text mismatch')
        visual.update(status='model_read_unreviewed', reader_id=reader_id,
                      text_sha256=hashlib.sha256(text.encode()).hexdigest(), human_reviewed=False,
                      transcription_verified=False,
                      readings=[{'page': p['page'], 'text': p['text'], 'uncertainties': p['uncertainties'],
                                 'uncertain_spans': p.get('uncertain_spans', []),
                                 'source_sha256': payload_pages[0 if visual['format'] == 'pdf' else i]['source_sha256'],
                                 'source_url': payload_pages[0 if visual['format'] == 'pdf' else i]['source_url']}
                                for i, p in enumerate(pages)])
        return {**item, 'status': 'read', 'text': '\n\n'.join(filter(None, [item['text'], text])),
                'needs_visual_reading': False, 'visual_reading': visual}
    except Exception:
        visual['status'] = 'reader_failed'
        return {**item, 'visual_reading': visual}


async def _content_record(client, base: dict, raw: bytes, acquisition: str,
                          reader: VisualReader | None, *, source_url: str = '') -> dict:
    kind = _format(raw)
    originals, blocked, text = [], 0, ''
    if kind in {'pdf', 'image'}:
        originals = [{'page': 1, 'source_url': source_url or base['source_url'], 'content': raw,
                      'status': 'fetched', 'source_sha256': hashlib.sha256(raw).hexdigest()}]
    else:
        soup = BeautifulSoup(raw, 'html.parser')
        for node in soup(['script', 'style']):
            node.decompose()
        for node in soup.find_all(['img', 'object', 'embed', 'iframe']):
            src = node.get('src') or node.get('data') or ''
            asset = _asset(src, base['dcm_no'])
            if asset:
                url, params = asset
                if not any(page['source_url'] == url for page in originals):
                    originals.append({'page': len(originals) + 1, 'source_url': url,
                                      'params': params, 'status': 'not_fetched'})
            else:
                blocked += 1
            node.decompose()
        text = soup.get_text(' ', strip=True)
        if len(text) > MAX_TEXT_CHARS:
            return {**base, 'status': 'size_limit', 'text': '', 'acquisition': acquisition}
    item = {**base, 'status': 'read' if text else 'format_unsupported', 'text': text,
            'native_text': text,
            'document_sha256': hashlib.sha256(raw).hexdigest(),
            'document_hash_basis': 'original_source_bytes', 'acquisition': acquisition,
            'needs_visual_reading': bool(originals or blocked)}
    if not originals and not blocked:
        return item
    item.update(status='needs_visual_reading', visual_reading={
        'status': 'needs_visual_reading', 'format': kind if kind != 'html' else 'images',
        'human_reviewed': False, 'transcription_verified': False,
        'pages': [{k: v for k, v in page.items() if k not in {'content', 'params'}}
                  for page in originals[:MAX_VISUAL_PAGES]],
        'blocked_references': blocked, 'truncated': len(originals) > MAX_VISUAL_PAGES,
        'max_pages': MAX_VISUAL_PAGES,
        'hint': '원본 이미지/PDF 읽기 필요. 미독은 미공시가 아니며 모델 전사는 사람 검토를 거치지 않았다.'})
    try:
        return await asyncio.wait_for(
            _read_visual(client, item, originals[:MAX_VISUAL_PAGES], reader), VISUAL_TIMEOUT)
    except asyncio.TimeoutError:
        item['visual_reading']['status'] = 'reader_timeout'
        return item


async def _read(client, request: dict, as_of: str, reader: VisualReader | None) -> dict:
    rc, dcm = request['rcept_no'], request['dcm_no']
    cutoff = _cutoff(rc, as_of)
    base = {**_identity(rc, dcm), 'status': 'fetch_failed', 'text': '', 'published': None,
            'document_sha256': None, 'needs_visual_reading': False}
    discovery = await discover_charter_attachments(client, rc, cutoff)
    if discovery['status'] != 'read':
        return {**base, 'status': discovery['status']}
    matches = [a for a in discovery['attachments'] if a['dcm_no'] == dcm]
    if len(matches) != 1:
        return {**base, 'status': 'attachment_identity_unverified'}
    entry = matches[0]
    base.update(published=entry['published'], title=entry['title'],
                date_basis=entry['date_basis'], attachment_list_sha256=discovery['list_sha256'])
    if entry['status'] != 'available':
        return {**base, 'status': entry['status']}
    raw = await _xml_attachment(client, rc, dcm)
    if raw is not None:
        return await _content_record(client, base, raw, 'dart_attachment_xml', reader)
    main = await _fetch(client, '/dsaf001/main.do', {'rcpNo': rc, 'dcmNo': dcm})
    selected_entries, _ = _listing(main, rc, cutoff)
    selected_matches = [a for a in selected_entries if a['dcm_no'] == dcm]
    if len(selected_matches) != 1:
        return {**base, 'status': 'attachment_identity_unverified'}
    selected_entry = selected_matches[0]
    if selected_entry['status'] != 'available':
        return {**base, 'status': selected_entry['status']}
    if selected_entry['published'] != entry['published']:
        return {**base, 'status': 'publication_date_conflict'}
    # This page can silently select another document: reject its node identities.
    nodes = client._extract_viewer_nodes(main.decode('utf-8', errors='replace'))
    if not nodes or len(nodes) > MAX_SECTIONS or any(
        node.get('rcpNo') != rc or node.get('dcmNo') != dcm for node in nodes
    ):
        return {**base, 'status': 'attachment_identity_unverified'}
    parts, urls = [], []
    for node in nodes:
        if (any(not re.fullmatch(r'\d{1,12}', node.get(field, '')) for field in ('eleId', 'offset', 'length'))
                or not re.fullmatch(r'[A-Za-z0-9_.-]{1,60}', node.get('dtd', ''))):
            return {**base, 'status': 'attachment_identity_unverified'}
        params = {field: node[field] for field in ('rcpNo', 'dcmNo', 'eleId', 'offset', 'length', 'dtd')}
        parts.append(await _fetch(client, '/report/viewer.do', params))
        urls.append(BASE + '/report/viewer.do?' + urlencode(params))
        if sum(map(len, parts)) > MAX_DOCUMENT_BYTES:
            return {**base, 'status': 'size_limit'}
    return await _content_record(client, {**base, 'original_section_urls': urls,
        'xml_attachment_status': 'not_identified_in_xml_zip'}, b'\n'.join(parts),
        'dart_attachment_viewer_html', reader, source_url=urls[0] if len(urls) == 1 else '')


async def read_charter_document(client, request: dict, as_of: str, *,
                                visual_reader: VisualReader | None = None) -> dict:
    """Read a dated attachment, never an arbitrary submitted URL or selected main."""
    allowed = {'type', 'rcept_no', 'dcm_no', 'source_scope', 'focus_terms',
               'candidate_names', 'text_offset', 'text_chars', 'read_options'}
    if (not isinstance(request, dict) or set(request) - allowed
            or request.get('type') != 'dart_attachment'
            or not isinstance(request.get('dcm_no'), str)
            or not re.fullmatch(r'\d{1,20}', request['dcm_no'])):
        raise ValueError('invalid dart_attachment request')
    _cutoff(request.get('rcept_no'), as_of)
    try:
        return await asyncio.wait_for(_read(client, request, as_of, visual_reader or _visual_reader), READ_TIMEOUT)
    except Exception:
        return {**_identity(request['rcept_no'], request['dcm_no']), 'status': 'fetch_failed',
                'text': '', 'published': None, 'document_sha256': None, 'needs_visual_reading': False}
