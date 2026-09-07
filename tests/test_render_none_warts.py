"""260907 live smoke — director_board 가 비고 없는 자리에 「> None」·「[roster] None」을, business_details 가
내부 진단값 「주석fetch=None」을 사용자 출력에 내던 것. None 은 사용자 문서에 글자로 나갈 수 없다."""
from open_proxy_mcp.tools.director_board import _render as render_board
from open_proxy_mcp.tools import business_details as bd


def _board_payload():
    return {
        "status": "exact", "subject": "테스트", "tool": "director_board",
        "data": {
            "canonical_name": "테스트", "scope": "summary", "year": 2025,
            "roster": {"roster": [], "headcount_total": 3, "headcount_board": 2, "official_outside_director_changes": [{"year": 2025}],
                       "diff_cross_check": {"note": None}},
            "individual": {"rows": [], "note": None},
            "pay_gap": {"note": None},
            "pay_agenda": {"note": None},
            "attendance": {"status": "parsed", "directors": [], "note": None},
            "pay_criteria": {"rows": [], "note": None, "unit_note": None},
            "assessment": {"note": None},
            "data_quality_flags": [{"scope": "roster", "kind": "crosscheck_mismatch", "severity": "warn", "detail": None}],
        },
        "warnings": [],
    }


def test_director_board_never_prints_the_word_none():
    text = render_board(_board_payload())
    assert "None" not in text
    assert "> \n" not in text


def test_roster_flag_uses_the_detail_key():
    """services 쪽 플래그 dict 가 「상세」 키로 적혀 있어 렌더러의 detail 이 늘 None 이었다."""
    import inspect
    from open_proxy_mcp.services import director_board as svc
    src = inspect.getsource(svc)
    assert '"상세":' not in src


def test_business_details_footer_hides_internal_diagnostics():
    src = inspect_src = open(bd.__file__, encoding="utf-8").read()
    assert "주석fetch" not in inspect_src
