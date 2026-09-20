"""수집기는 누락분만 읽고 손상·차단이면 멈춘다. 라이브 호출 없음."""
import asyncio
import gzip
import json

import pytest

from scripts.collect_parser_documents import collect

RECEIPTS = ['20260901000001', '20260901000002']
DOC = {'html': '<p>원문</p>', 'text': '원문', 'images': []}


def run(tmp_path, *, fetch=None, receipts=RECEIPTS):
    calls = []
    async def sleep(seconds):
        calls.append(seconds)
    result = asyncio.run(collect(receipts, tmp_path/'out', tmp_path/'cache', fetch=fetch, sleep=sleep))
    return result, calls


def test_missing_only_and_immutable_reuse(tmp_path):
    cache = tmp_path/'cache'
    cache.mkdir()
    raw = json.dumps(DOC).encode()
    source = cache/(RECEIPTS[0]+'.json.gz')
    source.write_bytes(gzip.compress(raw))
    before = source.stat().st_mtime_ns
    calls = []
    async def fetch(r):
        calls.append(r)
        return DOC
    result, waits = run(tmp_path, fetch=fetch)
    assert not result['aborted']
    assert calls == [RECEIPTS[1]] and waits == [1.1]
    assert source.stat().st_mtime_ns == before
    assert (tmp_path/'out'/(RECEIPTS[0]+'.json')).read_bytes() == raw
    again, waits = run(tmp_path, fetch=fetch)
    assert again['fetch_attempts'] == 0 and waits == []
    assert calls == [RECEIPTS[1]]


@pytest.mark.parametrize('mode', ['duplicate', 'corrupt', 'invalid', 'broken_link'])
def test_bad_source_is_failure_not_network_retry(tmp_path, mode):
    cache = tmp_path/'cache'; cache.mkdir()
    p = cache/(RECEIPTS[0]+'.json')
    p.write_text(json.dumps(DOC) if mode == 'duplicate' else '{' if mode == 'corrupt' else '{}')
    if mode == 'duplicate':
        (cache/(RECEIPTS[0]+'.json.gz')).write_bytes(gzip.compress(p.read_bytes()))
    if mode == 'broken_link':
        p.unlink()
        p.symlink_to(cache/'absent-source')
    async def fetch(r):
        pytest.fail('bad cached input must not trigger network')
    result, waits = run(tmp_path, fetch=fetch)
    assert result['aborted'] and result['fetch_attempts'] == 0 and not waits


@pytest.mark.parametrize('status', ['010', '011', '012', '020', '021'])
def test_abort_does_not_rotate_or_continue_or_leak_error(tmp_path, status):
    from open_proxy_mcp.dart.client import DartClientError
    calls = []
    async def fetch(r):
        calls.append(r)
        raise DartClientError(status, 'SENSITIVE_URL_AND_KEY_MUST_NOT_APPEAR')
    result, waits = run(tmp_path, fetch=fetch)
    assert result['aborted'] and result['error']['status'] == status
    assert calls == [RECEIPTS[0]] and waits == [1.1]
    assert 'SENSITIVE' not in json.dumps(result)


@pytest.mark.parametrize('receipts', [[], ['../unsafe'], ['２０２６０９０１０００００１'], [RECEIPTS[0]]*2,
                                     [f'20260901{i:06d}' for i in range(31)]])
def test_limit_and_receipt_validation_before_side_effects(tmp_path, receipts):
    with pytest.raises(ValueError):
        run(tmp_path, receipts=receipts)
    assert not (tmp_path/'out').exists()


def test_repository_is_not_a_raw_corpus_destination(tmp_path):
    from pathlib import Path
    from scripts import collect_parser_documents as collector
    root = Path(collector.__file__).resolve().parents[1]
    with pytest.raises(ValueError, match='private_destination_required'):
        asyncio.run(collect(RECEIPTS, root, tmp_path))
