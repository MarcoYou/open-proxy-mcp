import asyncio


def test_concurrent_document_misses_share_one_fetch(monkeypatch):
    from open_proxy_mcp.dart.client import DartClient

    client = DartClient(api_keys=["test-key"])
    rcept_no = "singleflight-test-20260825"
    calls = 0

    async def fake_get_document(_rcept_no):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"text": "ok", "html": "", "images": []}

    monkeypatch.setattr(client, "get_document", fake_get_document)
    monkeypatch.setattr(client, "_save_to_disk", lambda *_args: None)

    async def run():
        return await asyncio.gather(
            *(client.get_document_cached(rcept_no) for _ in range(8))
        )

    results = asyncio.run(run())

    assert calls == 1
    assert results == [{"text": "ok", "html": "", "images": []}] * 8
    client.invalidate_document(rcept_no)
