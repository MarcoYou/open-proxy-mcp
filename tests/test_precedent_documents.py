"""Court HTTP boundary regressions; no real requests or user result fixtures."""
import asyncio
import hashlib
import importlib

import httpx
import pytest

from open_proxy_mcp.dart.as_of import reset_strict_as_of, set_strict_as_of


REQUEST = {'type': 'court_precedent', 'board': 'precedent_bulletin', 'seqnum': '11003'}


def api():
    return importlib.import_module('open_proxy_mcp.services.precedent_documents')


def run(coro):
    return asyncio.run(coro)


def listing(rows=None):
    rows = rows if rows is not None else [('11003', '2026-04-09', '주주총회 정관 사건')]
    return '<meta charset="utf-8"><table class="tableHor"><thead><tr><th>번호</th><th>제목</th><th>작성자</th><th>작성일</th></tr></thead><tbody>' + ''.join(
        f'<tr><td>1</td><td class="tit"><a href="/portal/news/NewsViewAction.work?gubun=4&amp;seqnum={seq}">{title}</a></td><td>법원도서관</td><td>{day}</td></tr>'
        for seq, day, title in rows) + '</tbody></table>'


def document(date='2026-04-09', extra='', title='정관 사건 [2026. 4. 2. 결정]'):
    return f'''<meta charset="utf-8"><nav>관련 없는 최신 뉴스</nav><table class="tableVer">
    <tr><th>제목</th><td>{title}</td></tr><tr><th>작성자</th><td>법원도서관</td><th>작성일</th><td>{date}</td></tr>
    {extra}<tr><th>첨부파일</th><td><a href="https://www.scourt.go.kr/sjudge/123_456.pdf">판결문.pdf</a></td></tr>
    <tr><td class="contArea"><p>2025마6793 의결권행사허용가처분</p><p>재항고기각. 법원 공식 요약 원문.</p></td></tr></table>'''


class Client:
    def __init__(self, html=None, responder=None):
        self.html = document() if html is None else html
        self.requests = []
        self.throttles = 0
        self.responder = responder
        self._http = httpx.AsyncClient(transport=httpx.MockTransport(self.respond), follow_redirects=True)

    def respond(self, request):
        self.requests.append(request)
        return self.responder(request) if self.responder else httpx.Response(200, content=self.html.encode())

    async def _throttle_web(self):
        self.throttles += 1


def test_discovery_uses_euckr_bounded_list_and_only_unread_handles():
    client = Client(listing())
    result = run(api().discover_precedents(client, '상법', '20260410'))
    assert result['status'] == 'read' and result['complete'] is False
    candidate, = result['candidates']
    assert candidate['read_source'] == REQUEST and candidate['status'] == 'unread'
    assert candidate['published'] == '20260409'
    assert 'text' not in candidate
    assert '%BB%F3%B9%FD' in str(client.requests[0].url)
    assert client.requests[0].url.host == 'sc.scourt.go.kr'
    assert client.throttles == 1 and len(client.requests) == 1


def test_discovery_suppresses_future_or_undated_descriptive_information():
    result = run(api().discover_precedents(Client(listing([
        ('11003', '2026-04-09', '공개된 정관 사건'),
        ('11004', '2026-04-11', '미래에 소송 패소'),
        ('11005', '', '알 수 없는 미래 사건'),
    ])), '정관', '20260410'))
    assert len(result['candidates']) == 1
    assert len(result['excluded']) == 2
    assert '미래' not in str(result)
    assert all('read_source' not in row and 'title' not in row for row in result['excluded'])


def test_read_requires_official_posted_date_not_decision_date():
    client = Client()
    result = run(api().read_precedent_document(client, REQUEST, '20260408'))
    assert result['status'] == 'after_as_of' and result['published'] == '20260409'
    assert result['text'] == '' and result['document_sha256'] is None
    assert not result.get('title') and not result.get('attachments')
    assert '재항고기각' not in str(result)


def test_read_official_summary_preserves_body_hash_and_unread_attachment():
    client = Client()
    result = run(api().read_precedent_document(client, REQUEST, '20260410'))
    assert result['status'] == 'read' and result['published'] == '20260409'
    assert result['source_id'] == 'court:precedent_bulletin:11003'
    assert result['source_kind'] == 'court_official_summary'
    assert '법원 공식 요약 원문.' in result['text'] and '최신 뉴스' not in result['text']
    assert result['raw_document_sha256'] == hashlib.sha256(client.html.encode()).hexdigest()
    assert len(result['document_sha256']) == 64
    attachment, = result['attachments']
    assert attachment['status'] == 'unread'
    assert attachment['published'] is None
    assert attachment['version_status'] == 'publication_unproven'
    assert result['finality_status'] == 'not_assessed'
    assert len(client.requests) == 1


@pytest.mark.parametrize('day', ['', '2026-02-30', '2026-04-09 2026-04-11'])
def test_missing_invalid_ambiguous_publication_never_uses_verdict_date(day):
    result = run(api().read_precedent_document(Client(document(date=day)), REQUEST, '20260410'))
    assert result['status'] == 'publication_unknown' and result['text'] == ''


def test_strict_context_cannot_be_relaxed_by_argument():
    token = set_strict_as_of('2026-04-09T12:00:00+09:00')
    try:
        result = run(api().read_precedent_document(Client(), REQUEST, '20260901'))
        assert result['as_of'] == '20260408' and result['status'] == 'after_as_of'
    finally:
        reset_strict_as_of(token)


@pytest.mark.parametrize('extra', [
    '<tr><th>수정일</th><td>2026-04-11</td></tr>',
    '<meta property="article:modified_time" content="2026-04-11T09:00:00+09:00">',
])
def test_updated_after_cutoff_body_is_suppressed(extra):
    result = run(api().read_precedent_document(Client(document(extra=extra)), REQUEST, '20260410'))
    assert result['status'] == 'updated_after_as_of' and result['text'] == ''


def test_explicit_undated_correction_cannot_be_silently_backdated():
    result = run(api().read_precedent_document(Client(document(title='[수정] 정관 사건')), REQUEST, '20260410'))
    assert result['status'] == 'version_unproven' and result['text'] == ''


@pytest.mark.parametrize('change', [
    {'source_url': 'https://evil.example/'}, {'published': '20200101'},
    {'board': '44'}, {'type': 'dart'}, {'seqnum': '../etc'}, {'seqnum': 11003},
    {'seqnum': '0'}, {'seqnum': '0011003'}, {'seqnum': '9' * 30},
])
def test_identity_is_strict_and_no_network_for_bad_requests(change):
    client = Client()
    with pytest.raises(ValueError):
        run(api().read_precedent_document(client, {**REQUEST, **change}, '20260410'))
    assert not client.requests


def test_request_validator_returns_canonical_identity():
    assert api().validate_request(REQUEST) == REQUEST


@pytest.mark.parametrize('cutoff', ['20260230', '2026-04-10', '99991231', '', None])
def test_cutoff_requires_nonfuture_yyyymmdd(cutoff):
    client = Client()
    with pytest.raises(ValueError):
        run(api().read_precedent_document(client, REQUEST, cutoff))
    assert not client.requests


@pytest.mark.parametrize('query,page', [('', 1), ('상법', 0), ('상법', True), ('상법', 1001), ('x' * 41, 1), ('😀', 1)])
def test_invalid_search_does_not_fetch(query, page):
    client = Client()
    with pytest.raises(ValueError):
        run(api().discover_precedents(client, query, '20260410', page))
    assert not client.requests


def test_redirect_not_followed_even_if_client_defaults_to_following():
    client = Client(responder=lambda _: httpx.Response(302, headers={'location': 'https://evil.example/'}))
    result = run(api().read_precedent_document(client, REQUEST, '20260410'))
    assert result['status'] == 'fetch_failed' and len(client.requests) == 1
    assert 'evil' not in str(result)


def test_exception_message_never_enters_source_packet():
    def fail(_):
        raise RuntimeError('https://example.test/?secret=do-not-repeat')
    result = run(api().read_precedent_document(Client(responder=fail), REQUEST, '20260410'))
    assert result['status'] == 'fetch_failed' and 'do-not-repeat' not in str(result)


def test_oversized_response_rejected(monkeypatch):
    monkeypatch.setattr(api(), 'MAX_DOCUMENT_BYTES', 20)
    result = run(api().read_precedent_document(Client(), REQUEST, '20260410'))
    assert result['status'] == 'fetch_failed' and result['text'] == ''


def test_unexpected_html_is_not_evidence_or_empty_search_success():
    for action in ('read', 'discover'):
        client = Client('<html>일시적으로 이용이 제한됩니다</html>')
        result = run(api().read_precedent_document(client, REQUEST, '20260410') if action == 'read'
                     else api().discover_precedents(client, '상법', '20260410'))
        assert result['status'] == 'fetch_failed'


def test_untrusted_or_wrong_board_list_links_are_not_handles():
    html = listing().replace('href="/portal/news/', 'href="https://evil.example/portal/news/')
    assert run(api().discover_precedents(Client(html), '상법', '20260410'))['candidates'] == []
    html = listing().replace('gubun=4', 'gubun=3')
    assert run(api().discover_precedents(Client(html), '상법', '20260410'))['candidates'] == []


def test_untrusted_attachment_is_not_exposed_as_fetchable_handle():
    html = document().replace('https://www.scourt.go.kr/sjudge/', 'https://evil.example/sjudge/')
    result = run(api().read_precedent_document(Client(html), REQUEST, '20260410'))
    assert result['status'] == 'read' and result['attachments'] == []
    assert result['blocked_attachment_count'] == 1


def test_mismatched_echoed_identity_cannot_be_read():
    html = document(extra='<input name="seqnum" value="11004">')
    result = run(api().read_precedent_document(Client(html), REQUEST, '20260410'))
    assert result['status'] == 'identity_mismatch' and result['text'] == ''


def test_identity_hash_ignores_navigation_views_and_session_but_detects_body_changes():
    first = run(api().read_precedent_document(Client(), REQUEST, '20260410'))
    changed_shell = document().replace('관련 없는 최신 뉴스', '새로운 주변 뉴스').replace(
        '<tr><th>첨부파일', '<tr><th>조회수</th><td>99</td></tr><tr><th>첨부파일')
    second = run(api().read_precedent_document(Client(changed_shell), REQUEST, '20260410'))
    assert first['document_sha256'] == second['document_sha256']
    assert first['raw_document_sha256'] != second['raw_document_sha256']
    changed_body = document().replace('재항고기각', '파기환송')
    third = run(api().read_precedent_document(Client(changed_body), REQUEST, '20260410'))
    assert first['document_sha256'] != third['document_sha256']


def test_undated_correction_suffix_or_body_update_label_blocks_backdating():
    for html in [document(title='정관 사건 (수정)'),
                 document(extra='<tr><th>수정일</th><td></td></tr>'),
                 document().replace('법원 공식 요약 원문.', '법원 공식 요약 원문.</p><p>최종 수정일: 2026-04-11')]:
        result = run(api().read_precedent_document(Client(html), REQUEST, '20260410'))
        assert result['status'] in {'version_unproven', 'updated_after_as_of'}
        assert result['text'] == ''
