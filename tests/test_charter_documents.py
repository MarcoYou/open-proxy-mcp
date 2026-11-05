"""Responses at the DART HTTP/binary boundary: synthetic, or saved public screens.

tests/dart_responses/charter/ holds unmodified public DART pages; no user results.
"""
import asyncio
import hashlib
import io
import zipfile
from pathlib import Path

import httpx
import pytest

from open_proxy_mcp.dart.client import DartClient
from open_proxy_mcp.dart.as_of import set_strict_as_of, reset_strict_as_of

RC = '20260318000001'
DCM = '12345678'


def listing(day='2026.03.18', extra=''):
    return f'<select id="att"><option value="null">첨부선택</option><option value="rcpNo={RC}&amp;dcmNo={DCM}">{day} 정관</option>{extra}</select>'


def selected(dcm=DCM):
    return listing() + f'''<script>var node1 = {{}};
node1['text'] = "정관"; node1['rcpNo'] = "{RC}";
node1['dcmNo'] = "{dcm}"; node1['eleId'] = "1";
node1['offset'] = "0"; node1['length'] = "100";
node1['dtd'] = "dart4.xsd"; treeData.push(node1);</script>'''


def archive(entries=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        for name, data in (entries or {f'{RC}.xml': '<DOCUMENT>사업보고서 본문</DOCUMENT>'}).items():
            z.writestr(name, data)
    return output.getvalue()


class RawClient(DartClient):
    def __init__(self, *, main=None, body='<p>제1조 정관 원문</p>', entries=None, selected_html=None, image=b'\xff\xd8\xffimage'):
        super().__init__(api_keys=['unit-test-only'])
        self.calls = []
        self.throttles = 0
        self.raw_zip = archive(entries)
        self._http = httpx.AsyncClient(transport=httpx.MockTransport(self.respond))
        self.main = listing() if main is None else main
        self.body = body
        self.selected_html = selected() if selected_html is None else selected_html
        self.image = image

    def respond(self, request):
        self.calls.append(str(request.url))
        if request.url.path == '/dsaf001/main.do':
            return httpx.Response(200, text=self.selected_html if 'dcmNo' in request.url.params else self.main)
        if request.url.path == '/report/viewer.do':
            return httpx.Response(200, content=self.body.encode() if isinstance(self.body, str) else self.body)
        if request.url.path == '/report/download.do':
            return httpx.Response(200, content=self.image, headers={'content-type':'image/jpeg'})
        raise AssertionError('unapproved endpoint')

    async def _throttle_web(self):
        self.throttles += 1

    async def _request_binary(self, endpoint, params):
        assert endpoint == 'document.xml' and params == {'rcept_no': RC}
        self.calls.append('document.xml')
        return self.raw_zip


def run(coro):
    return asyncio.run(coro)


def api():
    from open_proxy_mcp.services import charter_documents
    return charter_documents


def request(**kwargs):
    return {'type':'dart_attachment','rcept_no':RC,'dcm_no':DCM,**kwargs}


def test_discovers_only_dated_member_charters():
    extra = f'<option value="rcpNo={RC}&amp;dcmNo=55555555">2026.04.01 정관</option><option value="rcpNo=20200101000001&amp;dcmNo=99999999">2026.03.18 정관</option>'
    client = RawClient(main=listing(extra=extra))
    result = run(api().discover_charter_attachments(client, RC, '20260320'))
    assert result['status'] == 'read'
    assert [(a['dcm_no'],a['status']) for a in result['attachments']] == [(DCM,'available'),('55555555','after_as_of')]
    assert result['attachments'][0]['published'] == '20260318'
    assert client.throttles == 1


@pytest.mark.parametrize('day,status', [('','publication_unknown'),('2026.02.30','publication_unknown'),('2026.04.01','after_as_of')])
def test_unproven_or_late_attachment_never_fetches_content(day,status):
    client = RawClient(main=listing(day))
    result = run(api().read_charter_document(client, request(), '20260320'))
    assert result['status'] == status
    assert len(client.calls) == 1


def test_strict_effective_date_cannot_be_relaxed_by_argument():
    tokens=set_strict_as_of('2026-03-18T23:59:00+09:00')
    try:
        client=RawClient()
        result=run(api().read_charter_document(client, request(), '20260901'))
        assert result['status']=='after_as_of'
        assert client.calls==[]
    finally:
        reset_strict_as_of(tokens)


def test_xml_missing_attachment_falls_back_to_its_own_viewer():
    client=RawClient()
    result=run(api().read_charter_document(client, request(), '20260320'))
    assert result['status']=='read'
    assert result['text']=='제1조 정관 원문'
    assert result['source_id']==f'filing:{RC}:attachment:{DCM}'
    assert result['document_sha256']==hashlib.sha256(client.body.encode()).hexdigest()
    assert result['acquisition']=='dart_attachment_viewer_html'
    assert client.calls[1]=='document.xml'
    assert client.throttles==3


def test_exact_xml_attachment_identity_precedes_viewer():
    xml='<DOCUMENT><P>제1조 정관 XML 원문</P></DOCUMENT>'
    client=RawClient(entries={f'{RC}.xml':'<DOCUMENT>본문</DOCUMENT>',f'{DCM}.xml':xml})
    result=run(api().read_charter_document(client, request(), '20260320'))
    assert result['status']=='read'
    assert result['text']=='제1조 정관 XML 원문'
    assert result['acquisition']=='dart_attachment_xml'
    assert result['document_sha256']==hashlib.sha256(xml.encode()).hexdigest()
    assert len(client.calls)==2


def test_selected_viewer_must_match_attachment_membership():
    client=RawClient(selected_html=selected('99999999'))
    result=run(api().read_charter_document(client, request(), '20260320'))
    assert result['status']=='attachment_identity_unverified'
    assert not any('/report/viewer.do' in call for call in client.calls)


IMAGE_BODY=f'<h1>정관</h1><img src="/report/download.do?dcmNo={DCM}&amp;flNm=page01.jpg">'


def test_images_are_unread_original_references_without_provider():
    client=RawClient(body=IMAGE_BODY)
    result=run(api().read_charter_document(client, request(), '20260320'))
    assert result['status']=='needs_visual_reading'
    assert result['needs_visual_reading'] is True
    assert result['visual_reading']['pages'][0]['source_url'].endswith('flNm=page01.jpg')
    assert result['visual_reading']['pages'][0]['status']=='not_fetched'
    assert not any('/report/download.do' in call for call in client.calls)


def test_optional_visual_reader_receives_original_and_keeps_hashes_separate():
    received=[]
    async def reader(payload):
        received.append(payload)
        return {'reader_id':'test-vision','text':'제1조 원문 이미지 내용','pages':[{'page':1,'text':'제1조 원문 이미지 내용','uncertainties':['작은 글씨']}], 'document_sha256':'forged'}
    client=RawClient(body=IMAGE_BODY)
    result=run(api().read_charter_document(client,request(),'20260320',visual_reader=reader))
    assert received[0]['pages'][0]['content']==client.image
    assert result['status']=='read'
    assert result['document_sha256']==hashlib.sha256(IMAGE_BODY.encode()).hexdigest()
    visual=result['visual_reading']
    assert visual['status']=='model_read_unreviewed'
    assert visual['human_reviewed'] is False
    assert visual['reader_id']=='test-vision'
    assert visual['pages'][0]['source_sha256']==hashlib.sha256(client.image).hexdigest()
    assert visual['text_sha256']==hashlib.sha256('제1조 원문 이미지 내용'.encode()).hexdigest()


@pytest.mark.parametrize('source', ['http://127.0.0.1/private.jpg','https://evil.example/a.jpg',f'/report/download.do?dcmNo=99999999&flNm=page.jpg', f'/report/download.do?dcmNo={DCM}&flNm=../secrets.jpg'])
def test_untrusted_image_references_never_fetched(source):
    async def reader(payload):
        raise AssertionError('reader must not run without originals')
    client=RawClient(body=f'<img src="{source}">')
    result=run(api().read_charter_document(client,request(),'20260320',visual_reader=reader))
    assert result['status']=='needs_visual_reading'
    assert result['visual_reading']['blocked_references']==1
    assert not any('/report/download.do' in call for call in client.calls)


def test_provider_failure_is_unread_not_not_disclosed():
    async def reader(payload):
        raise RuntimeError('private failure detail')
    result=run(api().read_charter_document(RawClient(body=IMAGE_BODY),request(),'20260320',visual_reader=reader))
    assert result['status']=='needs_visual_reading'
    assert result['visual_reading']['status']=='reader_failed'
    assert 'private failure detail' not in repr(result)


def test_binary_pdf_is_deferred_without_unconditional_pdf_download():
    client=RawClient(body=b'%PDF-1.7\nsynthetic')
    result=run(api().read_charter_document(client,request(),'20260320'))
    assert result['status']=='needs_visual_reading'
    assert result['visual_reading']['format']=='pdf'
    assert result['document_sha256']==hashlib.sha256(client.body).hexdigest()


def test_arbitrary_source_url_rejected_before_network():
    client=RawClient()
    with pytest.raises(ValueError):
        run(api().read_charter_document(client,request(url='http://localhost'),'20260320'))
    assert client.calls==[]


def test_selected_page_rechecks_its_own_attachment_date():
    client=RawClient(selected_html=selected().replace('2026.03.18','2026.04.01'))
    result=run(api().read_charter_document(client,request(),'20260320'))
    assert result['status']=='after_as_of'
    assert not any('/report/viewer.do' in call for call in client.calls)


def test_page_budget_returns_original_references_without_calling_reader(monkeypatch):
    monkeypatch.setattr(api(),'MAX_VISUAL_PAGES',1)
    async def reader(payload):
        pytest.fail('page budget must stop the provider')
    body=IMAGE_BODY+IMAGE_BODY.replace('page01','page02')
    client=RawClient(body=body)
    result=run(api().read_charter_document(client,request(),'20260320',visual_reader=reader))
    assert result['status']=='needs_visual_reading'
    assert result['visual_reading']['truncated'] is True
    assert len(result['visual_reading']['pages'])==1
    assert not any('/report/download.do' in call for call in client.calls)


def test_unproven_reader_text_does_not_become_evidence():
    async def reader(payload):
        return {'reader_id':'test-reader','text':'not in page','pages':[{'page':1,'text':'page text','uncertainties':[]}]}
    result=run(api().read_charter_document(RawClient(body=IMAGE_BODY),request(),'20260320',visual_reader=reader))
    assert result['status']=='needs_visual_reading'
    assert 'not in page' not in result['text']
    assert result['visual_reading']['status']=='reader_failed'


def test_registered_reader_can_be_removed():
    calls=[]
    async def reader(payload):
        calls.append(payload)
        return {'text':'원문','pages':[{'page':1,'text':'원문','uncertainties':[]}]}
    api().register_visual_reader(reader,reader_id='deployment-reader')
    try:
        result=run(api().read_charter_document(RawClient(body=IMAGE_BODY),request(),'20260320'))
        assert result['visual_reading']['reader_id']=='deployment-reader'
        assert len(calls)==1
    finally:
        api().register_visual_reader(None)
    result=run(api().read_charter_document(RawClient(body=IMAGE_BODY),request(),'20260320'))
    assert result['status']=='needs_visual_reading'
    assert len(calls)==1


def test_oversized_http_body_stops_safely(monkeypatch):
    monkeypatch.setattr(api(),'MAX_DOCUMENT_BYTES',100)
    # The aggregate bound also applies even though the function default is frozen.
    result=run(api().read_charter_document(RawClient(body='a'*101),request(),'20260320'))
    assert result['status']=='size_limit'
    assert result['text']==''


def test_redirect_cannot_escape_dart_origin():
    client=RawClient()
    calls=[]
    def redirect(req):
        calls.append(str(req.url))
        return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
    client._http=httpx.AsyncClient(transport=httpx.MockTransport(redirect),follow_redirects=True)
    result=run(api().discover_charter_attachments(client,RC,'20260320'))
    assert result['status']=='fetch_failed'
    assert len(calls)==1


def test_image_fetch_budget_timeout_keeps_original_reading_handle(monkeypatch):
    monkeypatch.setattr(api(),'VISUAL_TIMEOUT',0.01,raising=False)
    async def reader(payload):
        await asyncio.sleep(1)
    result=run(api().read_charter_document(RawClient(body=IMAGE_BODY),request(),'20260320',visual_reader=reader))
    assert result['status']=='needs_visual_reading'
    assert result['visual_reading']['status']=='reader_timeout'
    assert result['visual_reading']['pages'][0]['source_url']


def test_http_200_block_message_cannot_be_read_as_source_text(monkeypatch):
    from open_proxy_mcp.dart import client as client_module
    from open_proxy_mcp.dart.client import _BLOCK_PAGE_MARKERS
    monkeypatch.setattr(client_module, '_web_block',
                        {'count': 0, 'last_status': None, 'last_at': None, 'last_where': None})
    client=RawClient(body='<p>'+_BLOCK_PAGE_MARKERS[0]+'</p>')
    result=run(api().read_charter_document(client,request(),'20260320'))
    assert result['status']=='fetch_failed'
    assert not result['text']


def test_invalid_as_of_format_rejected_without_fetch():
    client=RawClient()
    with pytest.raises(ValueError):
        run(api().read_charter_document(client,request(),'2026-03-20'))
    assert client.calls==[]


SCREENS = Path(__file__).parent / 'dart_responses' / 'charter'


class ScreenClient(RawClient):
    """Serves saved public DART pages; a missing page fails the test."""

    def __init__(self, name):
        super().__init__(image=b'%PDF-1.7\nsynthetic')
        self.dir = SCREENS / name

    def respond(self, request):
        self.calls.append(str(request.url))
        path, params = request.url.path, request.url.params
        if path == '/dsaf001/main.do':
            name = (f"att_{params['rcpNo']}_{params['dcmNo']}.html" if 'dcmNo' in params
                    else f"main_{params['rcpNo']}.html")
        elif path == '/report/viewer.do':
            name = f"view_{params['dcmNo']}_0.html"
        elif path == '/report/download.do':
            return httpx.Response(200, content=self.image)
        else:
            raise AssertionError('unapproved endpoint')
        return httpx.Response(200, content=(self.dir / name).read_bytes())

    async def _request_binary(self, endpoint, params):
        self.calls.append('document.xml')
        return self.raw_zip


AMENDED, ORIGINAL, ORIGINAL_DCM = '20260331004244', '20260311004614', '11111704'


def test_amended_screen_keeps_charter_of_original_receipt():
    result = run(api().discover_charter_attachments(ScreenClient('amended_text_samsunglife'), AMENDED, '20261031'))
    assert result['status'] == 'read'
    assert result['family_receipts'] == [ORIGINAL, AMENDED]
    entry = result['attachments'][0]
    assert (entry['rcept_no'], entry['dcm_no'], entry['status']) == (ORIGINAL, ORIGINAL_DCM, 'available')
    assert entry['published'] == '20260311'
    assert entry['source_url'].endswith(f'rcpNo={ORIGINAL}&dcmNo={ORIGINAL_DCM}')


def test_amended_receipt_reads_attachment_under_its_own_receipt():
    client = ScreenClient('amended_text_samsunglife')
    request_ = {'type': 'dart_attachment', 'rcept_no': AMENDED, 'dcm_no': ORIGINAL_DCM}
    result = run(api().read_charter_document(client, request_, '20261031'))
    assert result['status'] == 'read'
    assert result['rcept_no'] == ORIGINAL
    assert result['requested_rcept_no'] == AMENDED
    assert result['source_id'] == f'filing:{ORIGINAL}:attachment:{ORIGINAL_DCM}'
    assert len(result['text']) > 10000
    assert any(f'rcpNo={ORIGINAL}' in call and '/report/viewer.do' in call for call in client.calls)


def family_page(members, owner):
    options = ''.join(f'<option value="rcpNo={m}">2026.03.18 사업보고서</option>' for m in members)
    return (f'<select id="family"><option value="null">본문선택</option>{options}</select>'
            f'<select id="att"><option value="rcpNo={owner}&amp;dcmNo={DCM}">2026.03.11 정관</option></select>')


def test_foreign_receipt_is_refused_when_family_does_not_list_it():
    main = family_page([RC, '20260311000001'], '20260311000002')
    result = run(api().discover_charter_attachments(RawClient(main=main), RC, '20260320'))
    assert result['attachments'] == []


def test_attachment_only_correction_is_absent_from_family_but_still_resolves():
    main = family_page(['20260311000001'], '20260311000001')
    result = run(api().discover_charter_attachments(RawClient(main=main), RC, '20260320'))
    assert [a['rcept_no'] for a in result['attachments']] == ['20260311000001']


def test_family_member_keeps_its_own_publication_rules():
    main = family_page([RC, '20260312000001'], '20260312000001')
    result = run(api().discover_charter_attachments(RawClient(main=main), RC, '20260320'))
    assert result['attachments'][0]['status'] == 'publication_date_conflict'


def test_pdf_link_is_an_original_not_read_text():
    client = ScreenClient('pdf_link_huons')
    request_ = {'type': 'dart_attachment', 'rcept_no': '20260324001000', 'dcm_no': '11169512'}
    result = run(api().read_charter_document(client, request_, '20261031'))
    assert result['status'] == 'needs_visual_reading'
    assert result['needs_visual_reading'] is True
    assert result['visual_reading']['format'] == 'pdf'
    assert result['visual_reading']['pages'][0]['source_url'].endswith('.pdf')
    assert '.pdf' not in result['text']


def test_pdf_link_reaches_reader_as_one_document():
    received = []
    async def reader(payload):
        received.append(payload)
        return {'reader_id': 'test-reader', 'text': '제1조', 'page_count': 1,
                'pages': [{'page': 1, 'text': '제1조', 'uncertainties': []}]}
    body = f'<p>정관</p><a href="/report/download.do?dcmNo={DCM}&amp;flNm=charter.pdf">charter.pdf</a>'
    client = RawClient(body=body, image=b'%PDF-1.7\nsynthetic')
    result = run(api().read_charter_document(client, request(), '20260320', visual_reader=reader))
    assert received[0]['format'] == 'pdf'
    assert result['status'] == 'read'


def test_ordinary_link_stays_text_and_unsupported_download_stays_unread():
    plain = run(api().read_charter_document(RawClient(body='<p>제1조 <a href="https://example.com">공고</a></p>'), request(), '20260320'))
    assert plain['status'] == 'read'
    assert '공고' in plain['text']
    hwp = run(api().read_charter_document(RawClient(body=f'<a href="/report/download.do?dcmNo={DCM}&amp;flNm=a.hwp">a.hwp</a>'), request(), '20260320'))
    assert hwp['status'] == 'needs_visual_reading'
    assert hwp['visual_reading']['blocked_references'] == 1
    assert 'a.hwp' not in hwp['text']


KOREAN_AIR = {'type': 'dart_attachment', 'rcept_no': '20260318001125', 'dcm_no': '11140186'}


def test_observed_26_page_charter_fits_the_page_budget():
    result = run(api().read_charter_document(ScreenClient('images_26_koreanair'), KOREAN_AIR, '20261031'))
    visual = result['visual_reading']
    assert (len(visual['pages']), visual['total_pages'], visual['unread_pages'], visual['truncated']) == (26, 26, 0, False)


def test_page_budget_states_how_much_was_left_unread(monkeypatch):
    monkeypatch.setattr(api(), 'MAX_VISUAL_PAGES', 24)
    result = run(api().read_charter_document(ScreenClient('images_26_koreanair'), KOREAN_AIR, '20261031'))
    visual = result['visual_reading']
    assert (len(visual['pages']), visual['total_pages'], visual['unread_pages'], visual['truncated']) == (24, 26, 2, True)
    assert '26쪽' in visual['hint']


def test_oversized_pdf_is_reported_as_page_limit(monkeypatch):
    monkeypatch.setattr(api(), 'MAX_VISUAL_PAGES', 2)
    async def reader(payload):
        return {'reader_id': 'test-reader', 'text': 'x', 'page_count': 5, 'pages': []}
    client = RawClient(body=b'%PDF-1.7\nsynthetic', image=b'%PDF-1.7\nsynthetic')
    result = run(api().read_charter_document(client, request(), '20260320', visual_reader=reader))
    visual = result['visual_reading']
    assert result['status'] == 'needs_visual_reading'
    assert (visual['status'], visual['total_pages'], visual['unread_pages'], visual['truncated']) == ('page_limit', 5, 3, True)


def reviewed_reader(**extra):
    async def reader(payload):
        return {'reader_id': 'test-reader', 'text': '제1조', 'pages': [{'page': 1, 'text': '제1조', 'uncertainties': []}], **extra}
    return reader


def test_reader_default_review_state_stays_unreviewed():
    visual = run(api().read_charter_document(RawClient(body=IMAGE_BODY), request(), '20260320', visual_reader=reviewed_reader()))['visual_reading']
    assert (visual['status'], visual['review_status'], visual['human_reviewed']) == ('model_read_unreviewed', 'unreviewed', False)


def test_reader_can_carry_review_state_and_reference():
    reader = reviewed_reader(review_status='dual_read_unresolved', archive_ref='doc/1:v2')
    visual = run(api().read_charter_document(RawClient(body=IMAGE_BODY), request(), '20260320', visual_reader=reader))['visual_reading']
    assert (visual['status'], visual['review_status'], visual['archive_ref']) == ('model_read_unreviewed', 'dual_read_unresolved', 'doc/1:v2')
    assert visual['human_reviewed'] is False


def test_human_review_state_is_the_only_one_that_sets_human_reviewed():
    visual = run(api().read_charter_document(RawClient(body=IMAGE_BODY), request(), '20260320', visual_reader=reviewed_reader(review_status='human_reviewed')))['visual_reading']
    assert (visual['status'], visual['human_reviewed']) == ('human_reviewed', True)


@pytest.mark.parametrize('extra', [{'review_status': 'verified'}, {'archive_ref': 'bad ref'}])
def test_invalid_review_fields_are_refused(extra):
    result = run(api().read_charter_document(RawClient(body=IMAGE_BODY), request(), '20260320', visual_reader=reviewed_reader(**extra)))
    assert result['status'] == 'needs_visual_reading'
    assert result['visual_reading']['status'] == 'reader_failed'


def test_rate_limit_wait_does_not_count_against_transfer_timeout(monkeypatch):
    monkeypatch.setattr(api(), 'FETCH_TIMEOUT', 0.05, raising=False)
    client = RawClient()
    async def slow_throttle():
        await asyncio.sleep(0.2)
    client._throttle_web = slow_throttle
    assert run(api().discover_charter_attachments(client, RC, '20260320'))['status'] == 'read'
