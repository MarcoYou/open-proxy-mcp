# -*- coding: utf-8 -*-
"""리포트가 **결말을 갈라서 보여주는가**. network/DB 0콜.

260810: 데이터는 세 번 고쳤는데(3상태·폴백 경로·degrade) **읽는 쪽은 한 번도 안 고쳤다.**
그래서 세 가지가 화면에 안 나왔다 —
  ① 핸드셰이크가 latency 평균에 섞여 「평균 응답 1,522ms」가 나왔다(near-0 이 눌렀다)
  ② `WHERE is_error=true` 만 봐서 상류실패(degrade)·자료없음·판정불가가 전부 안 보였다
  ③ 「판정불가」와 「컬럼 생기기 전」을 한 칸에 세면 없는 경보가 울린다(실측 3,528 vs 0)
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "scripts" / "usage_tracker.py"


def _ut():
    spec = importlib.util.spec_from_file_location("usage_tracker", _SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_protocol_methods_are_identified():
    ut = _ut()
    for t in ("initialize", "ping", "tools/list", "notifications/initialized", None, ""):
        assert ut.is_protocol(t), f"{t!r} 를 도구 호출로 셌다"
    for t in ("company", "dividend", "law_lookup", "valuation"):
        assert not ut.is_protocol(t), f"{t!r} 를 핸드셰이크로 뺐다"


def test_latency_excludes_handshakes():
    """**이게 「평균 1,522ms」의 원인이었다.** 핸드셰이크는 near-0 이라 평균을 끌어내린다."""
    ut = _ut()
    rows = [("initialize", "h1", 1, None)] * 100 + [("dividend", "h1", 900, False)] * 2
    ranked, avg, p50, p95 = ut.tool_stats(rows)
    assert avg == 900, f"핸드셰이크가 평균에 섞였다: {avg}"
    assert p50 == 900 and p95 == 900
    assert [t for t, *_ in ranked] == ["dividend"], "핸드셰이크가 기능 표에 들어갔다"


def test_latency_reports_distribution_not_just_mean():
    """문서 파싱은 꼬리가 길어(사업보고서 수십MB) 평균이 중앙값을 한참 웃돈다 —
    실측 평균 6,849ms / p50 862ms. 평균만 내면 「보통 얼마나 걸리나」를 못 말한다."""
    ut = _ut()
    rows = [("dividend", "h1", 100, False)] * 99 + [("dividend", "h1", 100_000, False)]
    _, avg, p50, p95 = ut.tool_stats(rows)
    assert p50 == 100 and avg > 1000, f"avg={avg} p50={p50}"


def test_report_helpers_exist():
    """결말 분해가 리포트 경로에 실제로 붙어 있어야 한다 — 함수만 있고 안 부르면 소용없다."""
    ut = _ut()
    assert hasattr(ut, "outcome_breakdown") and hasattr(ut, "print_outcomes")
    src = _SRC.read_text(encoding="utf-8")
    assert src.count("print_outcomes()") >= 2, "report·stats 양쪽에서 안 부른다"
    # 옛 `WHERE is_error=true` 집계는 **지웠다.** 남겨두면 언젠가 다시 배선돼
    # 상류실패·자료없음·판정불가를 또 못 보게 된다(죽은 코드가 되살아나는 흔한 경로).
    # 단언은 **실행되는 SQL** 만 겨냥한다 — 산문에서 「종전엔 WHERE is_error=true 만 봤다」고
    # 설명하는 주석까지 잡으면, 왜 바꿨는지 적은 기록을 지워야 테스트가 통과하게 된다.
    assert "FROM tool_call_events WHERE is_error=true" not in src, (
        "옛 is_error=true 전용 질의가 되살아났다")
    assert "fetch_error_kinds" not in src, "대체된 함수가 남아 있다"


# ── 결말 분류 (DB 0콜) ─────────────────────────────────────────────────────
def test_unclassifiable_is_not_mixed_with_pre_schema_rows():
    """**하나는 경보, 하나는 역사다.** 처음 짤 때 둘 다 「판정불가」로 세었더니 3,528건이
    잡혀 경보처럼 보였는데, 실제로는 전부 구스키마였고 진짜 판정불가는 0건이었다."""
    ut = _ut()
    assert ut.classify_outcome(None, "unclassifiable")[0] == "판정불가"
    assert ut.classify_outcome(None, None)[0] == "미기록(구스키마)"


def test_upstream_failure_is_separated_from_our_crash():
    """대응이 다르다 — 상류는 사용자 안내, 우리 크래시는 우리가 고칠 것."""
    ut = _ut()
    assert ut.classify_outcome(True, "dart_rate_limited") == ("상류(DART)", "dart_rate_limited")
    assert ut.classify_outcome(True, "crash") == ("우리오류", "crash")
    assert ut.classify_outcome(True, None) == ("우리오류", "untagged")


def test_no_data_is_an_answer_not_a_failure():
    """013·404 를 실패로 세면 오류율이 부풀고 진짜 고장이 그 안에 묻힌다."""
    ut = _ut()
    for k in ("no_data", "not_found"):
        assert ut.classify_outcome(False, k) == ("자료없음", k)
    assert ut.classify_outcome(False, None) == ("정상", None)
