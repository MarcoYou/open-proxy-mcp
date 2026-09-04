# -*- coding: utf-8 -*-
"""260904 라이브 관찰 결함 3건의 회귀. network 0콜.

  근거 위치 밀림 (솔루엠 20260319001012): 2번 자본준비금 안건 아래 제3호 정관 원문, 3번 정관 아래
    제4호 이사 선임 원문 … 한 칸씩 밀렸는데 「근거 위치: §…」로 확정 표기됐다. → 지우지 않고 불확실
    표시 + 「이 발췌가 이 안건 것이면 쓰라」.
  임시주총 없음 (가비아·솔루엠 extraordinary 호출): 「2025년 임시주총 · 안건 0건」 프레임에 2025 정기
    공고를 후보 출처로, 2023 재무를 기준으로 실었다. → 없다고 말하고 같은 창의 소집공고 목록만.
  오늘의 기준: 서버(UTC)의 date.today() 가 KST 자정~09시에 어제를 줘 오늘 접수분이 잘렸다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import open_proxy_mcp.services.proxy_advise as PA
from open_proxy_mcp.clock import KST, today_kst
from open_proxy_mcp.tools import proxy_advise_before_meeting as R


# ── 근거 위치 불확실 ──────────────────────────────────────────────────────────────

def _row(title, note=None, src=True, excerpt=None):
    return {"agenda_title": title, "decision": "FOR", "reason": "r", "facts": {}, "risk_factors": [],
            "policy_citation": "OPM Guideline §7 의사결정 7단계 ▸ 엔진: x", "policy_basis": "-",
            "classification_note": note,
            "source_section": {"section_title": "정관의 변경", "section_code": "L0-0-2-2-0"} if src else None,
            "source_excerpt": excerpt, "agenda_id": "2"}


def test_mismatch_root_and_its_children_are_marked_uncertain_but_promotion_is_not():
    rows = [
        _row("자본준비금 감액 승인의 건", note="표준 서식 신고 유형('정관 변경')과 제목 기반 분류('현금배당') 불일치 — 원문 발췌 확인 권고"),
        _row("회사 홈페이지 주소 변경"),                       # 자식 — 부모 절을 물려받는다
        _row("정관 일부 변경의 건", note="분류 근거: 표준 서식 신고 유형 '정관 변경' — 제목 기반 분류 미매칭을 보완"),
        _row("이사 보수한도 승인의 건"),
    ]
    roots = PA._mark_uncertain_evidence(rows, {"회사 홈페이지 주소 변경": "자본준비금 감액 승인의 건"})
    assert roots == {"자본준비금 감액 승인의 건"}
    assert rows[0].get("source_section_uncertain") is True
    assert rows[1].get("source_section_uncertain") is True      # 자식까지
    assert "source_section_uncertain" not in rows[2]             # 승격 메모는 확실한 쪽
    assert "source_section_uncertain" not in rows[3]


def test_renderer_asks_instead_of_asserting_when_evidence_is_uncertain():
    sure = _row("이사 보수한도 승인의 건", excerpt="제6호 의안: 이사 보수한도 …")
    unsure = dict(_row("자본준비금 감액 승인의 건", excerpt="제3호 의안: 정관 일부 변경 …"),
                  source_section_uncertain=True)
    text = R._render({"status": "ok", "subject": "테스트",
                      "data": {"year": 2026, "agenda_count": 2, "candidates_count": 0,
                               "agenda_decisions": [sure, unsure]}})
    assert "- 근거 위치: 소집공고 **§정관의 변경**" in text
    assert "- 근거 위치(불확실): 소집공고 **§정관의 변경**" in text
    assert "이 안건(2)의 내용이 맞으면 근거로 쓰고" in text
    assert "이 안건의 것인지 먼저 확인하십시오" in text
    assert text.count("제3호 의안: 정관 일부 변경") == 1     # 발췌는 지우지 않는다


# ── 임시주총 없음 ─────────────────────────────────────────────────────────────────

def test_absent_meeting_renders_without_a_round_frame():
    payload = {"status": "no_filing", "subject": "가비아", "data": {
        "canonical_name": "가비아", "selected_meeting_type": "extraordinary",
        "agenda_decisions": [], "agenda_count": 0, "candidates_count": 0,
        "meeting_absent": {
            "window_start": "2024-09-05", "window_end": "2026-12-03", "method": "m",
            "found_notices": [{"rcept_no": "20260311004404", "report_name": "주주총회소집공고", "rcept_dt": "20260311"}],
            "other_type_latest": {"meeting_type": "annual", "notice_date": "20260311",
                                  "meeting_date": "2026-03-26", "notice_rcept_no": "20260311004404"},
            "notes": [],
        }}}
    text = R._render(payload)
    assert "요청하신 **임시주총** 소집공고가 없습니다" in text
    assert "20260311 주주총회소집공고" in text
    assert 'meeting_type="annual"' in text
    assert "안건 0건" not in text and "2025년" not in text     # 없는 회차를 그리지 않는다
    assert "재무 분석 기준" not in text


def test_absent_report_lists_notices_without_reading_them(monkeypatch):
    import open_proxy_mcp.services.filing_search as FS
    import open_proxy_mcp.services.shareholder_meeting as SM
    calls = {"search": 0}

    async def fake_search(**kw):
        calls["search"] += 1
        assert kw["pblntf_detail_ty"] == "E006" and kw["keywords"] == ("소집",)
        return ([{"rcept_no": "20250311000336", "report_nm": "주주총회소집공고", "rcept_dt": "20250311"},
                 {"rcept_no": "20260311004404", "report_nm": "주주총회소집공고", "rcept_dt": "20260311"}], [], None)

    async def fake_resolve(corp_code, *, meeting_type="annual", **kw):
        assert meeting_type == "annual"
        return {"year": 2026, "meeting_type": "annual", "meeting_date": None,
                "notice_rcept_no": "20260311004404", "notice_date": "20260311", "meeting_phase": "x"}

    monkeypatch.setattr(FS, "search_filings_by_report_name", fake_search)
    monkeypatch.setattr(SM, "resolve_latest_meeting_year", fake_resolve)
    out = asyncio.run(PA._meeting_absent_report("00000000", "extraordinary"))
    assert calls["search"] == 1
    assert [n["rcept_dt"] for n in out["found_notices"]] == ["20260311", "20250311"]   # 최신 먼저
    assert out["other_type_latest"]["meeting_type"] == "annual"
    assert out["window_end"] > out["window_start"]


# ── 오늘 = KST ────────────────────────────────────────────────────────────────────

def test_today_is_korean_calendar():
    expect = (datetime.now(timezone.utc) + timedelta(hours=9)).date()
    assert today_kst() in (expect, expect + timedelta(days=1))   # 자정 경계 허용
    assert KST.utcoffset(None) == timedelta(hours=9)


def test_no_naive_today_left_in_package():
    import pathlib, re
    root = pathlib.Path(PA.__file__).resolve().parent.parent
    left = [str(p.relative_to(root)) for p in root.rglob("*.py")
            if p.name != "clock.py" and re.search(r"\btoday\(\)", p.read_text(encoding="utf-8"))]
    assert not left, f".today() 가 남아 있다(UTC 서버에서 KST 아침엔 어제): {left}"
