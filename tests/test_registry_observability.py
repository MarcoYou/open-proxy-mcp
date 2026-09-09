"""원장 관측이 **관측 자체로 비싸지 않은지** — 네트워크 0.

260909: `_corp_code_cache`(118,942사 · 실측 RSS 약 68MB)는 메모리 층에 만료가 없고
`/health` 에도 안 보였다. fly 가 suspend/재개라 프로세스가 오래 살면 원장이 그만큼 묵고,
신규 상장사를 「그런 회사 없다」로 답하게 된다 — 조용한 실패다.

그래서 먼저 **보이게** 했는데, 관측이 그 자체로 비용이면 안 된다. 이 테스트가 그 계약이다.
"""
import datetime as dt

from open_proxy_mcp.dart import client as C


def test_sampling_does_not_copy_the_whole_registry():
    """`list(items)[:500]` 로 자르면 전체 사본이 먼저 생긴다 — 명부는 frozenset 118k 다.

    이터레이터만 소비하는 가짜 컨테이너를 넣어, 전수 순회가 **표본 단계에서** 일어나지
    않는지 본다(기준일 계산은 전수를 보지만 그건 문자열 비교라 별개다).
    """
    consumed = []

    class _Counted:
        def __init__(self, n): self._n = n
        def __len__(self): return self._n
        def __iter__(self):
            for i in range(self._n):
                consumed.append(i)
                yield {"corp_code": f"{i:08d}", "modify_date": "20260101"}

    C._registry_meta.pop("probe", None)
    C._note_registry("probe", _Counted(50_000), source="test")
    m = C._registry_meta["probe"]
    assert m["entries"] == 50_000
    assert m["bytes_est"] > 0, "표본으로 추정치가 나와야 한다"
    # 표본 500 + 기준일 전수 1회 = 50,500. 전체 사본을 떴다면 그보다 훨씬 커진다.
    assert len(consumed) <= 50_000 + 500 + 10, f"전수를 여러 번 돌았다({len(consumed)})"


def test_health_view_is_free():
    """`/health` 는 자주 불린다 — 볼 때마다 재면 관측이 부하가 된다."""
    C._registry_meta["probe2"] = {
        "entries": 118_942, "bytes_est": 81_000_000, "as_of": "20260907",
        "loaded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "sqlite",
    }
    out = C.registry_stats()["probe2"]
    assert out["entries"] == 118_942 and out["as_of"] == "20260907"
    assert out["age_hours"] is not None, "나이는 볼 때 계산한다(저장하면 그 값이 낡는다)"


def test_observation_never_breaks_serving():
    """관측이 실패해도 서빙은 계속된다 — 계기가 장애 원인이 되면 안 된다."""
    C._registry_meta.pop("bad", None)

    class _Boom:
        def __len__(self): raise RuntimeError("terrible")

    C._note_registry("bad", _Boom(), source="test")   # 예외가 새어 나오면 안 된다
    assert "bad" not in C._registry_meta
