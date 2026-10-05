"""Regression tests for observed operations defects; network-free, temporary cache files."""
import asyncio
import gc
import time
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient
from open_proxy_mcp.dart import client as C
from open_proxy_mcp import server as S
from open_proxy_mcp.maintenance import maintenance, AdmissionMiddleware


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    saved=list(C._CACHE_REGISTRY)
    monkeypatch.setattr(C,'_DISK_CACHE_DIR',str(tmp_path))
    maintenance.set_drain(0);maintenance.active_posts=0
    yield
    C._CACHE_REGISTRY[:]=saved
    maintenance.set_drain(0);maintenance.active_posts=0


def test_disk_stats_count_both_formats_exclude_partial_files(tmp_path):
    (tmp_path/'old.json').write_bytes(b'a'*10)
    (tmp_path/'new.json.gz').write_bytes(b'b'*100)
    (tmp_path/'new.json.gz.1.tmp').write_bytes(b'c'*1000)
    st=C._disk_cache_stats()
    assert (st['entries'],st['bytes'])==(2,110)
    assert st['volume_total_bytes']>st['volume_free_bytes']>0


def test_pressure_reclaim_expires_unread_keys_before_hot_payloads(monkeypatch):
    c=C.LruByteCache(100_000,300,'expire_regression')
    monkeypatch.setattr(C,'_CACHE_REGISTRY',[c])
    clock=[1000.0];monkeypatch.setattr(C.time,'time',lambda:clock[0])
    c.put('expired',{'v':'e'*1000},ttl_sec=1);c.put('hot',{'v':'h'*1000})
    clock[0]+=2
    result=C.cache_reclaim(100)
    assert c.get('hot') is not None and c.get('expired') is None
    assert result['expired_bytes']>=1000
    assert result['removed_bytes']==result['expired_bytes']


def test_new_writes_prune_expired_unread_keys(monkeypatch):
    c=C.LruByteCache(100_000,5,'write_expiry')
    clock=[1000.0];monkeypatch.setattr(C.time,'time',lambda:clock[0])
    c.put('old','old');clock[0]+=61;c.put('new','new')
    assert c.stats()['entries']==1 and c.stats()['expirations']==1


def test_selective_reclaim_preserves_recent_entries_and_disk(monkeypatch,tmp_path):
    c=C.LruByteCache(100_000,300,'selective');monkeypatch.setattr(C,'_CACHE_REGISTRY',[c])
    c.put('old','x'*2000);c.put('new','y'*2000)
    (tmp_path/'keep.json.gz').write_bytes(b'keep')
    r=C.cache_reclaim(1000)
    assert c.get('old') is None and c.get('new') is not None
    assert (tmp_path/'keep.json.gz').read_bytes()==b'keep'
    assert 1000<=r['removed_bytes']<4000


def test_financial_cache_is_bounded_registered_and_clearable(monkeypatch):
    from open_proxy_mcp.services import financial_metrics as fm
    c=C.LruByteCache(5000,300,'fm_test');monkeypatch.setattr(fm,'_FM_CACHE',c)
    for i in range(20):fm._fm_cache_set((str(i),),{'v':'x'*1500})
    assert c.stats()['bytes']<=5000 and c.evictions>0
    assert fm._fm_cache_get(('19',))=={'v':'x'*1500}
    C.cache_clear();assert fm._fm_cache_get(('19',)) is None


def test_cache_removal_does_not_run_gc(monkeypatch):
    collect=Mock();monkeypatch.setattr(gc,'collect',collect)
    C.cache_clear();C.cache_reclaim(0)
    collect.assert_not_called()


def test_full_client_registry_does_not_claim_active_clients(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(C,'_instances',{str(i):SimpleNamespace(_doc_inflight={}) for i in range(48)})
    monkeypatch.setattr(C,'_instance_seen',{str(i):0 for i in range(48)})
    monkeypatch.setattr(C,'_active_client_keys',{})
    st=C.client_registry_stats()
    assert st['entries']==48 and st['recent']==0 and st['evictable']==48 and st['over_limit']==0


def test_active_non_document_request_is_not_evicted(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(C,'_instances',{'busy':SimpleNamespace(_doc_inflight={}), 'idle':SimpleNamespace(_doc_inflight={})})
    monkeypatch.setattr(C,'_instance_seen',{'busy':0,'idle':0})
    monkeypatch.setattr(C,'_active_client_keys',{'busy':1});monkeypatch.setattr(C,'_INSTANCE_MAX',1)
    monkeypatch.setattr(C,'_close_client',Mock())
    C._evict_idle_clients()
    assert set(C._instances)=={'busy'}


def test_health_data_failure_is_not_ok(monkeypatch):
    from open_proxy_mcp.services import proxy_advise
    monkeypatch.setattr(proxy_advise,'_load_law_layer_rules',Mock(side_effect=RuntimeError('fixture')))
    r=TestClient(S.build_app()).get('/health')
    assert r.status_code==503 and r.json()['status']=='degraded'


def test_memtop_default_is_passive(monkeypatch):
    monkeypatch.setenv('OPM_ADMIN_KEY','fixture')
    collect=Mock();objects=Mock(side_effect=AssertionError('expensive enumeration'))
    monkeypatch.setattr(gc,'collect',collect);monkeypatch.setattr(gc,'get_objects',objects)
    r=TestClient(S.build_app()).get('/admin/memtop',headers={'x-admin-key':'fixture'})
    assert r.status_code==200 and r.json()['gc']['collection_performed'] is False
    collect.assert_not_called();objects.assert_not_called()


def test_admin_gc_runs_once_and_separate_effects(monkeypatch):
    monkeypatch.setenv('OPM_ADMIN_KEY','fixture')
    collect=Mock();monkeypatch.setattr(gc,'collect',collect)
    vals=iter([100,90,80,80]);monkeypatch.setattr(S,'_mem_stats',lambda:{'rss_mb':next(vals)})
    r=TestClient(S.build_app()).post('/admin/cache?trim=0',headers={'x-admin-key':'fixture'})
    assert r.status_code==200
    assert r.json()['freed_mb']=={'cache':10,'gc':10,'trim':0,'total':20}
    assert collect.call_count==1


def test_drain_requires_auth_rejects_new_requests_and_expires(monkeypatch):
    monkeypatch.setenv('OPM_ADMIN_KEY','fixture')
    clock=[100.0];monkeypatch.setattr('open_proxy_mcp.maintenance.time.monotonic',lambda:clock[0])
    c=TestClient(S.build_app());headers={'x-admin-key':'fixture'}
    assert c.post('/admin/drain?seconds=60').status_code==404
    assert c.post('/admin/drain?seconds=121',headers=headers).status_code==400
    assert c.post('/admin/drain?seconds=60',headers=headers).json()['draining']
    assert c.post('/mcp?opendart=fixture',json={}).status_code==503
    assert c.get('/health').status_code==503
    clock[0]+=61
    assert c.get('/health').status_code==200
    assert c.post('/mcp',json={}).status_code==401


def test_admission_waits_for_existing_request_and_tracks_cancellation():
    async def scenario():
        entered,finish=asyncio.Event(),asyncio.Event()
        async def app(*a):entered.set();await finish.wait()
        async def io(*a):return {}
        gate=AdmissionMiddleware(app)
        task=asyncio.create_task(gate({'type':'http','path':'/mcp','method':'POST'},io,io))
        await entered.wait();maintenance.set_drain(30)
        assert maintenance.active_posts==1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert maintenance.active_posts==0
    asyncio.run(scenario())
