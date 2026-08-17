# -*- coding: utf-8 -*-
"""자료 기준일은 **공포일**이어야 한다 — 커밋 날짜가 아니라. network 0콜.

260817: legalize-kr 은 히스토리를 다시 쓰는 방식이라 커밋 날짜가 내용을 대변하지
못한다. 8-04 공포분을 담은 스냅샷의 커밋일이 2026-02-10, 5-12 에서 멎은 포크가
7-02 — **낡은 쪽이 최신으로 보인다.** 그 값을 사용자에게 「N일 전 자료」로 보여주고
있었다. 두 지표는 평소엔 며칠 차이라 눈으로는 구분되지 않는다.
"""
from __future__ import annotations

from open_proxy_mcp.services.law_lookup import _promulgation_asof, corpus_freshness


def test_asof_comes_from_promulgation_not_commit():
    """커밋일이 공포일보다 **최신**이어도 공포일을 쓴다 — 함정이 그 방향으로 났다."""
    m = {
        "source_committed_date": "2026-12-31T00:00:00+09:00",
        "files": [
            {"frontmatter": {"공포일자": "2026-03-06"}},
            {"frontmatter": {"공포일자": "2026-06-30"}},
            {"frontmatter": {"공포일자": "2025-04-01"}},
        ],
    }
    assert _promulgation_asof(m) == "2026-06-30"


def test_top_level_field_wins_over_rescan():
    """sync 가 계산한 값을 재사용한다(독자 재계산 금지)."""
    m = {
        "source_promulgated_date": "2026-08-04",
        "files": [{"frontmatter": {"공포일자": "2026-06-30"}}],
    }
    assert _promulgation_asof(m) == "2026-08-04"


def test_falls_back_to_commit_date_on_old_manifest():
    """옛 manifest 에는 공포일이 없다 — 없는 것보다는 커밋일이 낫다."""
    from unittest.mock import patch
    with patch("open_proxy_mcp.services.law_lookup.load_manifest",
               return_value={"source_committed_date": "2026-07-02T12:00:00+09:00"}):
        assert corpus_freshness()["asof"] == "2026-07-02"


def test_live_corpus_reports_promulgation():
    """커밋된 corpus 로도 실제로 공포일이 나오나."""
    f = corpus_freshness()
    m_dates = ["2026-03-06", "2026-06-23", "2026-05-12", "2026-06-30",
               "2026-05-12", "2026-03-24", "2025-04-01", "2026-05-26"]
    assert f["asof"] == max(m_dates)
