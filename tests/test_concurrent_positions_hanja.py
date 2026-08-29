"""겸직 수는 원문이 한자를 써도 세야 한다 — 「2022 ~ 現」.

현직 표시를 「현재/현직/재직」으로만 보다가, 공시에서 가장 흔한 한자 「現」을 놓쳤다.
2026-08-28 실측 한국앤컴퍼니 이행희 — 원문에 사외이사 두 곳(포스코인터내셔널·무신사)이
나란히 적혀 있는데 **겸직 수 1**로 나갔다(본 회사 자동 +1 이 전부). 사람 눈에는 보이는
것이 파서에만 안 보였다.

기간 칸이 「2010~20141988~20242022 ~ 現」처럼 **합쳐져** 들어오는 표도 있다.
어느 줄이 현직인지 우리는 모른다 — 세되 **모른다는 사실을 같이 넘긴다.**
"""

from __future__ import annotations

from open_proxy_mcp.services.director_evaluation import count_outside_director_positions

_OWN = "한국앤컴퍼니"
_REAL = {  # 실측 이행희 행 (기간·경력이 한 칸에 합쳐져 들어온다)
    "period": "2010~20141988~20242022 ~ 現",
    "content": ("- (주)포스코인터내셔널 사외이사 (ESG위원장)"
                "- (주)무신사 사외이사 (보상위원장)"
                "- 숙명여자대학교 재단 이사- KB금융공익재단 이사"),
}


def test_hanja_present_marker_is_counted() -> None:
    out = count_outside_director_positions({"careerDetails": [_REAL]}, _OWN)
    assert out["in_career_count"] == 2          # 포스코인터내셔널 · 무신사
    assert out["total"] == 3                    # + 본 회사
    assert out["signals"]


def test_merged_period_is_flagged_not_hidden() -> None:
    out = count_outside_director_positions({"careerDetails": [_REAL]}, _OWN)
    assert out["period_merged"] is True
    assert out["confidence"] == "low"


def test_clean_row_is_normal_confidence() -> None:
    out = count_outside_director_positions(
        {"careerDetails": [{"period": "2022 ~ 現", "content": "(주)무신사 사외이사"}]}, _OWN)
    assert out["in_career_count"] == 1
    assert out["period_merged"] is False
    assert out["confidence"] == "normal"


def test_past_only_row_is_not_counted() -> None:
    """현직 표시가 없으면 세지 않는다 — 지난 겸직을 현직으로 부풀리지 않는다."""
    out = count_outside_director_positions(
        {"careerDetails": [{"period": "2010~2014", "content": "(주)포스코 사외이사"}]}, _OWN)
    assert out["in_career_count"] == 0
    assert out["total"] == 1                    # 본 회사만


def test_no_career_data_is_unknown_not_one() -> None:
    """읽을 경력이 없으면 「1곳」이 아니라 **못 셌다**고 말한다.

    본 회사 자동 +1 만으로 total=1 을 내보내면 「겸직이 한 곳뿐」이라는 확정 진술로 읽힌다.
    실측 한국앤컴퍼니 이행희 — 추천사유 원문에 포스코인터내셔널·무신사 사외이사가
    적혀 있는데 careerDetails 가 비어 「겸직 1곳」이 나갔다.
    """
    out = count_outside_director_positions({"careerDetails": []}, _OWN)
    assert out["total"] is None
    assert out["countable"] is False


def test_present_career_stays_countable() -> None:
    out = count_outside_director_positions(
        {"careerDetails": [{"period": "2010~2014", "content": "(주)포스코 사외이사"}]}, _OWN)
    assert out["countable"] is True
    assert out["total"] == 1


def test_career_says_zero_but_text_says_outside_director() -> None:
    """경력 표에 사외이사 줄이 없는데 추천사유는 타사 사외이사를 말한다 — 「1곳」이 아니다.

    실측 한국앤컴퍼니 이행희: careerDetails 에는 한국코닝 대표이사·사업부장만 있고
    포스코인터내셔널·무신사 사외이사는 **추천사유 본문에만** 있다. 경력만 세면 0 →
    본 회사 +1 = 「겸직 1곳」이라는 확정 오답이 나간다.
    """
    out = count_outside_director_positions({
        "careerDetails": [
            {"period": "1988~2024", "content": "한국코닝(주) 대표이사 (사장)"},
            {"period": "2022 ~ 현재", "content": "한국코닝(주) 자동차환경 사업부장"},
        ],
        "recommendationReason": "포스코인터내셔널과 무신사 등 다양한 산업군의 사외이사로서 …",
    }, _OWN)
    assert out["total"] is None
    assert out["countable"] is False
    assert out["text_mentions_outside"] is True


def test_no_conflict_when_text_is_silent() -> None:
    out = count_outside_director_positions({
        "careerDetails": [{"period": "2022 ~ 현재", "content": "한국코닝(주) 사업부장"}],
        "recommendationReason": "제조 현장 경영 경험이 풍부합니다.",
    }, _OWN)
    assert out["total"] == 1
    assert out["countable"] is True


def test_merged_period_is_split_into_spans() -> None:
    """합쳐진 기간 칸을 구간으로 끊는다 — 「2010~20141988~20242022 ~ 現」.

    마스터 지시(2026-08-29): 뒷부분 기간을 2010~2014 / 1988~2024 / 2022~null 로 갈라 달라.
    끝이 「現」이면 진행 중이라 end 를 None 으로 둔다.
    """
    from open_proxy_mcp.services.director_evaluation import split_merged_periods
    out = split_merged_periods("2010~20141988~20242022 ~ 現")
    assert [(s["start"], s["end"]) for s in out["spans"]] == [
        ("2010", "2014"), ("1988", "2024"), ("2022", None)]
    assert out["open_ended"] is True
    assert out["residue"] is None      # 남은 글자가 없다 = 다 읽었다


def test_month_precision_and_residue_are_kept() -> None:
    from open_proxy_mcp.services.director_evaluation import split_merged_periods
    out = split_merged_periods("2015.03~2020.02 2021~현재")
    assert out["spans"][0]["start"] == "2015.03"
    assert out["spans"][-1]["end"] is None

    # 못 읽은 글자는 삼키지 않는다
    bad = split_merged_periods("취임 이후")
    assert bad["spans"] == []
    assert bad["residue"] == "취임 이후"


def test_merged_periods_reach_the_caller() -> None:
    out = count_outside_director_positions({"careerDetails": [_REAL]}, _OWN)
    assert out["merged_periods"]
    assert out["merged_periods"][0]["open_ended"] is True
