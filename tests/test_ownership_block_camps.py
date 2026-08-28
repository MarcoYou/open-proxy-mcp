"""5% 대량보유 보고의 「합이 100%를 넘는다」·「본인 0.0%」·「013을 실패로 표시」 회귀 방지.

셋 다 실사용 리뷰에서 나온 막힘이다(2026-08-28, 고려아연·에스제이그룹).
  - 영풍 41.13% + 한국기업투자홀딩스 36.92% + … = 111.84%. 두 보고자가 (유)와이피씨 25.21%를
    서로 특별관계자로 안고 있어 같은 주식이 두 번 신고된 결과인데, 도구는 「합산 기준」이라고만 했다.
  - 영풍은 41.13%를 보고했는데 본인 지분이 0.0%다. 그 뜻이 산출물에 없었다.
  - 에스제이그룹 changes 가 「변동신고서 DART 검색 실패: 013」. 013은 실패가 아니라 「없음」이다.
"""

from __future__ import annotations

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services.ownership_structure import (
    _build_block_camps,
    _dart_read_note,
    _enrich_co_holders,
)
from open_proxy_mcp.tools.ownership_structure import _render


def _block(reporter: str, pct: float, self_pct: float | None, co: list[tuple[str, float]] | None,
           verified: bool | None = True) -> dict:
    return {
        "reporter": reporter,
        "ownership_pct": pct,
        "reporter_self_pct": self_pct,
        "purpose": "경영참여",
        "report_date": "2026-05-20",
        "rcept_no": "20260520000264",
        "co_holders": (
            None if co is None
            else [{"name": n, "ownership_pct": p, "is_registry_holder": False} for n, p in co]
        ),
        "co_holders_verified": verified,
    }


def test_shared_co_holder_merges_reporters_and_removes_double_count() -> None:
    """같은 특별관계자를 안고 있는 두 보고자는 한 편으로 묶이고, 겹친 몫은 한 번만 센다."""
    blocks = [
        _block("영풍", 41.13, 0.0, [("(유)와이피씨", 25.21), ("한국기업투자홀딩스", 8.25), ("장형진", 3.46),
                                     ("에이치씨(유)", 4.21)]),
        _block("한국기업투자홀딩스", 36.92, 8.25, [("(유)와이피씨", 25.21), ("장형진", 3.46), ("(주)영풍", 0.0)]),
        _block("최윤범", 17.72, 1.55, [("피23파트너스", 16.17)]),
    ]
    camps = _build_block_camps(blocks)

    assert camps["headline_total_pct"] == 95.77
    assert camps["exceeds_100"] is False
    labels = {c["label"]: c for c in camps["camps"]}
    assert "영풍 · 한국기업투자홀딩스" in labels
    # 겹친 (유)와이피씨 25.21 + 장형진 3.46 을 두 번 세지 않는다.
    assert labels["영풍 · 한국기업투자홀딩스"]["net_pct"] == 41.13
    assert labels["영풍 · 한국기업투자홀딩스"]["headline_sum_pct"] == 78.05
    # 겹치지 않는 보고자는 공시된 지분율을 그대로 쓴다(재계산으로 0.01%p 어긋난 값을 만들지 않는다).
    assert labels["최윤범"]["net_pct"] == 17.72
    assert camps["net_total_pct"] == 58.85


def test_over_100_percent_is_detected() -> None:
    blocks = [
        _block("영풍", 41.13, 0.0, [("(유)와이피씨", 25.21), ("한국기업투자홀딩스", 8.25), ("장형진", 7.67)]),
        _block("한국기업투자홀딩스", 36.92, 8.25, [("(유)와이피씨", 25.21), ("장형진", 3.46)]),
        _block("최윤범", 17.72, None, None),
        _block("크루시블제이브이", 10.59, None, None),
        _block("국민연금공단", 5.48, None, None),
    ]
    camps = _build_block_camps(blocks)
    assert camps["headline_total_pct"] == 111.84
    assert camps["exceeds_100"] is True
    assert camps["net_total_pct"] is not None and camps["net_total_pct"] < 100


def test_unrelated_reporters_are_not_merged() -> None:
    """겹치는 이름이 없으면 묶지 않는다 — 대립하는 두 진영을 한 편으로 만들면 안 된다."""
    blocks = [
        _block("조원태", 31.15, 5.9, [("한진칼우호", 25.25)]),
        _block("호반건설", 20.15, 20.15, [("호반산업", 0.0)]),
    ]
    camps = _build_block_camps(blocks)
    assert len(camps["camps"]) == 2
    assert camps["shared_holders_between_reporters"] == []


def test_camp_with_unparsed_member_reports_no_net_figure() -> None:
    """합계표를 못 읽은 보고자가 낀 편은 숫자를 지어내지 않는다 — 겹침 사실만 남긴다."""
    blocks = [
        _block("갑", 30.0, 5.0, [("공통", 25.0)]),
        _block("공통", 25.0, None, None),
    ]
    camps = _build_block_camps(blocks)
    assert len(camps["camps"]) == 1
    camp = camps["camps"][0]
    assert camp["net_pct"] is None
    assert camps["net_total_pct"] is None
    assert camps["shared_holders_between_reporters"][0]["shared_holders"][0]["name"] == "공통"


def test_unverified_holder_table_blocks_net_figure() -> None:
    """보고서 합계가 공시 지분율과 안 맞으면(미검증) 순 지분을 확정하지 않는다."""
    blocks = [
        _block("갑", 30.0, 5.0, [("공통", 25.0)], verified=False),
        _block("을", 26.0, 1.0, [("공통", 25.0)], verified=True),
    ]
    camps = _build_block_camps(blocks)
    assert camps["camps"][0]["net_pct"] is None


def test_reporter_self_zero_gets_explained() -> None:
    """본인 0.0% 는 뜻을 붙이되, 왜 0인지는 단정하지 않는다."""
    rows = [{
        "reporter": "영풍",
        "ownership_pct": 41.13,
        "holder_table": {
            "format": "일반",
            "self": {"name": "영풍", "pct": 0.0},
            "related": [{"name": "(유)와이피씨", "pct": 41.13}],
        },
    }]
    _enrich_co_holders(rows, [])
    note = rows[0]["reporter_self_note"]
    assert "직접 보유가 없" in note
    assert "특별관계자" in note
    assert "원문 확인" in note
    # 사유를 단정하는 표현이 없어야 한다.
    assert "때문" not in note

    rows2 = [{
        "reporter": "조종민",
        "ownership_pct": 51.85,
        "holder_table": {
            "format": "일반",
            "self": {"name": "조종민", "pct": 34.35},
            "related": [{"name": "가족", "pct": 17.5}],
        },
    }]
    _enrich_co_holders(rows2, [])
    assert rows2[0]["reporter_self_note"] == ""


def test_render_folds_zero_percent_holders_and_shows_self_note() -> None:
    rows = [{
        "reporter": "영풍",
        "ownership_pct": 41.13,
        "purpose": "경영참여",
        "report_date": "2026-05-20",
        "rcept_no": "20260520000264",
        "holder_table": {
            "format": "일반",
            "self": {"name": "영풍", "pct": 0.0},
            "related": [
                {"name": "(유)와이피씨", "pct": 41.13},
                {"name": "엠비케이1", "pct": 0.0},
                {"name": "엠비케이2", "pct": 0.0},
                {"name": "엠비케이3", "pct": 0.0},
            ],
        },
    }]
    _enrich_co_holders(rows, [])
    payload = {
        "status": "exact",
        "subject": "고려아연",
        "warnings": [],
        "data": {"canonical_name": "고려아연", "summary": {}, "window": {}, "blocks": rows},
    }
    out = _render(payload, "blocks")
    assert "+3명 (각 0.0%)" in out
    assert "엠비케이1" not in out          # 화면에서는 접힌다
    assert rows[0]["co_holders"][1]["name"] == "엠비케이1"   # 자료에는 남아 있다
    assert "본인 0.00%" in out
    assert "0.00% ⚠" in out


def test_dart_no_data_codes_are_not_failures() -> None:
    """013·014·404 는 「없음」이고, 키·점검 오류만 「실패」다."""
    for status in ("013", "014", "404"):
        exc = DartClientError(status, "x")
        failed, message = _dart_read_note(exc, empty_text="해당 기간 변동신고서 없음", fail_text="조회 실패")
        assert failed is False
        assert message == "해당 기간 변동신고서 없음"
        assert "실패" not in message

    for status in ("010", "011", "020", "800", "900"):
        exc = DartClientError(status, "x")
        failed, message = _dart_read_note(exc, empty_text="없음", fail_text="조회 실패")
        assert failed is True
        assert message.startswith("조회 실패 —")
