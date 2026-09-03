"""직위 어휘 한 벌 — 「독립이사」= 「사외이사」(상법 §542의8, 2026-07-23 시행 명칭 변경).

배경(실측 고려아연 2026-09-09 임시주총 소집공고 20260811000705):
  · `shareholder_meeting_notice(view="board")` 요약이 「사외이사 후보: 0명」 — 후보 6명 전원이
    「독립이사」인데 안건 **카테고리**(「집중투표에 의한 이사 4인 선임의 건」→ 이사)로 세고 있었다.
  · `proxy_advise_before_meeting` 은 같은 공고를 「사외/독립 4」로 읽었다 — 두 도구의 어휘가 달랐다.
  · 「직위 표기 불일치: 후보자 표「독립이사」 vs 안건 제목「사외이사」」— 실제 제목은 「…독립이사
    백인규 선임의 건」. 제목의 「독립이사」를 「사외이사」로 바꿔 적은 뒤 표의 원문과 문자열
    비교해 생긴 오탐이다.
원칙: 산출물엔 **원문 직위명**을 남기고, 같은 직위인지는 `role_class()` 로 묻는다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services.shareholder_meeting_parser import (  # noqa: E402
    ROLE_AUDIT_COMMITTEE,
    ROLE_AUDITOR,
    ROLE_DIRECTOR,
    ROLE_INSIDE,
    ROLE_OTHER_NON_EXECUTIVE,
    ROLE_OUTSIDE,
    _build_personnel_summary,
    declared_role_for_candidate,
    is_outside_role,
    parse_personnel_xml,
    role_class,
    same_role_class,
)


# ── 어휘 ────────────────────────────────────────────────────────────────────

def test_role_class_maps_both_labels_to_the_same_bucket():
    assert role_class("독립이사") == role_class("사외이사") == ROLE_OUTSIDE
    assert role_class("독립이사 후보자(재선임)") == ROLE_OUTSIDE
    assert role_class("사내이사") == role_class("대표이사") == ROLE_INSIDE
    assert role_class("기타비상무이사") == ROLE_OTHER_NON_EXECUTIVE
    assert role_class("감사위원회 위원이 되는 독립이사") == ROLE_AUDIT_COMMITTEE
    assert role_class("사외이사인 감사위원") == ROLE_AUDIT_COMMITTEE
    assert role_class("상근감사") == ROLE_AUDITOR
    assert role_class("이사") == ROLE_DIRECTOR
    assert role_class("") == role_class(None) == ""


def test_is_outside_role_survives_audit_committee_wording():
    """감사위원 표기가 붙어도 사외 자격 문턱(독립성 검증)은 그대로다."""
    for r in ("사외이사", "독립이사", "감사위원회 위원이 되는 독립이사", "사외이사인 감사위원"):
        assert is_outside_role(r), r
    for r in ("사내이사", "기타비상무이사", "감사위원", "상근감사", "이사", "", None):
        assert not is_outside_role(r), r


def test_same_role_class_treats_rename_as_same_seat():
    assert same_role_class("독립이사", "사외이사")
    assert not same_role_class("사내이사", "독립이사")
    assert not same_role_class("이사", "사내이사"), "세분 미상 vs 사내는 여전히 다르다"


# ── 제목이 밝힌 직위 — 원문 표기 유지 ───────────────────────────────────────

def test_declared_role_keeps_the_notice_wording():
    f = declared_role_for_candidate
    assert f("감사위원회 위원이 되는 독립이사 백인규 선임의 건", "백인규", ("백인규",)) == ("독립이사", "named")
    assert f("감사위원회 위원이 되는 독립이사 선임의 건", "박유경", ("백인규", "박유경")) == ("독립이사", "sole")
    assert f("사외이사 김대근 선임의 건", "김대근") == ("사외이사", "named")
    # 두 표기가 한 제목에 있어도 같은 범주면 직위는 하나다(종전엔 「직위 둘」로 보고 판단 포기)
    r, basis = f("사외이사(독립이사) 홍길동 선임의 건", "홍길동")
    assert basis == "named" and r in ("사외이사", "독립이사") and role_class(r) == ROLE_OUTSIDE
    # 범주가 갈리는데 지목이 없으면 여전히 판단하지 않는다
    assert f("이사선임의건(사내이사2명,독립이사3명)", "아무개") == (None, "")


# ── 인사 요약 — 후보의 직위로 센다 ──────────────────────────────────────────

def _cand(name, role=None):
    return {"name": name, "roleType": role}


def test_summary_counts_candidates_by_their_own_role_not_agenda_category():
    """고려아연 2026-09 모양: 「이사 4인 선임」(카테고리 이사) 아래 독립이사 4명 + 감사위원 2명."""
    appts = [
        {"number": "제2호", "title": "집중투표에 의한 이사 4인 선임의 건", "action": "선임",
         "category": "이사",
         "candidates": [_cand(n, "독립이사") for n in ("이형규", "서은숙", "이준봉", "심혜섭")]},
        {"number": "제3호", "title": "감사위원회 위원이 되는 독립이사 선임의 건", "action": "선임",
         "category": "감사위원회",
         "candidates": [_cand(n, "독립이사") for n in ("백인규", "박유경")]},
    ]
    s = _build_personnel_summary(appts)
    assert s["outside_directors"] == 4, "종전엔 0 — 안건 카테고리로 셌다"
    assert s["audit_committee"] == 2, "감사위원 선거는 카테고리가 우선(종전과 같다)"
    assert s["directors"] == 0
    assert s["total_candidates"] == 6 and s["unique_candidates"] == 6


def test_summary_falls_back_to_category_only_when_role_is_missing_or_generic():
    appts = [
        {"category": "사외이사", "action": "선임",
         "candidates": [_cand("가", None), _cand("나", "이사"), _cand("다", "사내이사")]},
    ]
    s = _build_personnel_summary(appts)
    assert s["outside_directors"] == 2, "직위 없음·「이사」는 카테고리(사외)로"
    assert s["directors"] == 1, "표가 사내이사라고 하면 표가 이긴다"


def test_summary_counts_people_not_appearances():
    """같은 사람이 묶음 안건과 개별 안건에 겹쳐 나와도 직위별 인원은 사람 수다."""
    appts = [
        {"number": "제3호", "category": "이사", "action": "선임",
         "candidates": [_cand("A", "사외이사"), _cand("B", "사내이사")]},
        {"number": "제3-1호", "category": "사외이사", "action": "선임", "candidates": [_cand("A", "사외이사")]},
        {"number": "제3-2호", "category": "사내이사", "action": "선임", "candidates": [_cand("B", "사내이사")]},
    ]
    s = _build_personnel_summary(appts)
    assert s["total_candidates"] == 4 and s["unique_candidates"] == 2
    assert s["outside_directors"] == 1 and s["directors"] == 1


def test_summary_dismissal_and_auditor_buckets_unchanged():
    appts = [
        {"category": "사외이사", "action": "해임", "candidates": [_cand("A", "사외이사")]},
        {"category": "감사", "action": "선임", "candidates": [_cand("B", "상근감사")]},
        {"category": "이사", "action": "선임", "candidates_raw_fallback": "표 없음", "candidates": []},
    ]
    s = _build_personnel_summary(appts)
    assert s["dismissals"] == 1 and s["auditors"] == 1
    assert s["directors"] == 0, "후보표 없는 raw fallback 안건은 인원을 부풀리지 않는다"
    assert s["total_appointments"] == 3


# ── end-to-end: 고려아연 모양 XML ─────────────────────────────────────────────

_KZ_HTML = """
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 이사의 선임</TITLE>
<P><SPAN>제2호 의안: 집중투표에 의한 이사 4인 선임의 건</SPAN></P>
<P><SPAN>- 제2-1호 의안: 독립이사 이형규 선임의 건</SPAN></P>
<P><SPAN>- 제2-2호 의안: 독립이사 서은숙 선임의 건</SPAN></P>
<P>가. 후보자의 성명ㆍ생년월일ㆍ추천인ㆍ최대주주와의 관계ㆍ독립이사후보자 등 여부</P>
<TABLE>
<TR><TH>후보자성명</TH><TH>생년월일</TH><TH>독립이사후보자여부</TH><TH>감사위원회 위원인이사 분리선출 여부</TH><TH>최대주주와의 관계</TH><TH>추천인</TH></TR>
<TR><TD>이형규</TD><TD>1955.10.29</TD><TD>독립이사</TD><TD>해당사항 없음</TD><TD>-</TD><TD>독립이사후보추천위원회</TD></TR>
<TR><TD>서은숙</TD><TD>1968.04.27</TD><TD>독립이사</TD><TD>해당사항 없음</TD><TD>-</TD><TD>독립이사후보추천위원회</TD></TR>
</TABLE>
<P>나. 후보자의 주된직업ㆍ세부경력ㆍ해당법인과의 최근3년간 거래내역</P>
<TABLE>
<TR><TH ROWSPAN="2">후보자성명</TH><TH ROWSPAN="2">주된직업</TH><TH COLSPAN="2">세부경력</TH><TH ROWSPAN="2">해당법인과의최근3년간 거래내역</TH></TR>
<TR><TH>기간</TH><TH>내용</TH></TR>
<TR><TD>이형규</TD><TD>한양대 명예교수</TD><TD>2022~현재</TD><TD>인천도시가스㈜ 독립이사</TD><TD>해당사항 없음</TD></TR>
<TR><TD>서은숙</TD><TD>상명대 교수</TD><TD>2025~현재</TD><TD>NH투자증권㈜ 독립이사</TD><TD>해당사항 없음</TD></TR>
</TABLE>
</SECTION-3>
</LIBRARY>
<LIBRARY>
<SECTION-3>
<TITLE>□ 감사위원회 위원의 선임</TITLE>
<P><SPAN>제3호 의안: 감사위원회 위원이 되는 독립이사 선임의 건</SPAN></P>
<P><SPAN>- 제3-1호 의안: 감사위원회 위원이 되는 독립이사 백인규 선임의 건</SPAN></P>
<P><SPAN>- 제3-2호 의안: 감사위원회 위원이 되는 독립이사 박유경 선임의 건</SPAN></P>
<P>가. 후보자의 성명ㆍ생년월일ㆍ추천인ㆍ최대주주와의 관계ㆍ독립이사후보자 등 여부</P>
<TABLE>
<TR><TH>후보자성명</TH><TH>생년월일</TH><TH>독립이사후보자여부</TH><TH>감사위원회 위원인이사 분리선출 여부</TH><TH>최대주주와의 관계</TH><TH>추천인</TH></TR>
<TR><TD>백인규</TD><TD>1968.02.13</TD><TD>독립이사</TD><TD>분리선출</TD><TD>-</TD><TD>독립이사후보추천위원회</TD></TR>
<TR><TD>박유경</TD><TD>1969.03.10</TD><TD>독립이사</TD><TD>분리선출</TD><TD>-</TD><TD>주주제안</TD></TR>
</TABLE>
<P>나. 후보자의 주된직업ㆍ세부경력ㆍ해당법인과의 최근3년간 거래내역</P>
<TABLE>
<TR><TH ROWSPAN="2">후보자성명</TH><TH ROWSPAN="2">주된직업</TH><TH COLSPAN="2">세부경력</TH><TH ROWSPAN="2">해당법인과의최근3년간 거래내역</TH></TR>
<TR><TH>기간</TH><TH>내용</TH></TR>
<TR><TD>백인규</TD><TD>단국대 산학협력교수</TD><TD>2026~현재</TD><TD>세진회계법인 자문</TD><TD>해당사항 없음</TD></TR>
<TR><TD>박유경</TD><TD>Tara Climate Foundation 사외이사</TD><TD>2022~현재</TD><TD>Tara Climate Foundation 사외이사</TD><TD>해당사항 없음</TD></TR>
</TABLE>
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""


def test_koreazinc_shape_no_false_conflict_and_outside_count():
    personnel = parse_personnel_xml(_KZ_HTML)
    cands = [c for a in personnel["appointments"] for c in a["candidates"]]
    assert {c["name"] for c in cands} >= {"이형규", "서은숙", "백인규", "박유경"}
    for c in cands:
        assert c.get("roleType") == "독립이사", (c["name"], c.get("roleType"))   # 원문 표기 보존
        assert "roleTypeConflict" not in c, (c["name"], c.get("roleTypeConflict"))
    audit = [c for a in personnel["appointments"] for c in a["candidates"]
             if a["number"].startswith("제3") and c.get("declaredRole")]
    assert audit and all(c["declaredRole"] == "독립이사" for c in audit), \
        "제목이 밝힌 직위도 원문 그대로 — 「사외이사」로 바꿔 적지 않는다"
    s = personnel["summary"]
    assert s["outside_directors"] == 2 and s["audit_committee"] == 2 and s["directors"] == 0


def test_genuine_conflict_still_reported():
    """표가 사내이사라고 하는데 제목이 독립이사라고 하면 그건 진짜 불일치 — 계속 알린다."""
    html = _KZ_HTML.replace("<TD>이형규</TD><TD>1955.10.29</TD><TD>독립이사</TD>",
                            "<TD>이형규</TD><TD>1955.10.29</TD><TD>사내이사</TD>")
    html = html.replace("제2호 의안: 집중투표에 의한 이사 4인 선임의 건",
                        "제2호 의안: 독립이사 선임의 건")
    personnel = parse_personnel_xml(html)
    lee = next(c for a in personnel["appointments"] for c in a["candidates"] if c["name"] == "이형규")
    assert lee["roleType"] == "사내이사"
    assert lee.get("roleTypeConflict", {}).get("declared_role") == "독립이사"


# ── 소비처 — 같은 어휘를 부른다 ─────────────────────────────────────────────

def test_consumers_share_the_vocabulary():
    from open_proxy_mcp.services.director_evaluation import _is_outside_director_role
    from open_proxy_mcp.services.proxy_advise import _classify_agenda
    from open_proxy_mcp.services.shareholder_meeting import _role_scope
    assert _is_outside_director_role("독립이사") and _is_outside_director_role("사외이사")
    assert _classify_agenda("독립이사 홍길동 해임의 건") == "director_election"
    assert _role_scope("독립이사 3인 선임의 건") == _role_scope("사외이사 3인 선임의 건") == "사외이사"


def test_audit_committee_seat_is_not_a_conflict_with_outside_title():
    """「감사위원회 위원이 되는 사외이사 선임」 + 표/구간이 「감사위원회」 → 모순 아님(둘 다다).
    제목이 감사위원을 말하지 않으면 여전히 충돌."""
    from open_proxy_mcp.services.shareholder_meeting_parser import _audit_seat_compatible
    assert _audit_seat_compatible("감사위원회 위원이 되는 사외이사 선임의 건", "감사위원회", "사외이사")
    assert _audit_seat_compatible("감사위원회 위원이 되는 독립이사 홍길동 선임의 건", "감사위원", "독립이사")
    assert not _audit_seat_compatible("사외이사 홍길동 선임의 건", "감사위원회", "사외이사")
    assert not _audit_seat_compatible("감사위원회 위원이 되는 사외이사 선임의 건", "사내이사", "사외이사")
