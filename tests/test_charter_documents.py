"""Synthetic responses at the DART HTTP/binary boundary; no user-result fixtures."""
import asyncio
import hashlib
import io
import zipfile

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
