"""본문 세부 준수 플래그 — 서식 표와 거르는 규칙이 반대라 한 글자 틀리면 0개가 나온다."""

from __future__ import annotations

from open_proxy_mcp.services.corp_gov_form import (
    COMPLIANCE_FLAGS,
    FORM_TABLES,
    SECTION_CODES,
    parse_compliance_flags,
)

_SHARED = "TimePeriodWhenFinancialStatementsWereProvidedToShareholdersAbstract"
_TWO_IN_ONE = "선임 사외이사, 집행임원제도 도입 여부 및 그 제도 도입 배경 이유, 관련 근거, 현황 등"


def _flag(aclass: str, label: str, value: str) -> str:
    return (
        f'<p>{label}</p>'
        f'<table-group aclass="{aclass}"><table><tbody><tr>'
        f'<td valuetxt="{value}">{value}</td></tr></tbody></table></table-group>'
    )


def test_registry_covers_every_flag_the_form_asks() -> None:
    assert len(COMPLIANCE_FLAGS) == 78
    keys = {(f["concept"], f["nth"]) for f in COMPLIANCE_FLAGS}
    assert len(keys) == 78
    for flag in COMPLIANCE_FLAGS:
        assert flag["label"], flag["concept"]
        assert flag["question"], flag["concept"]
        assert flag["section"] in SECTION_CODES, flag["concept"]
        assert not flag["concept"].startswith("krx-cg_"), flag["concept"]


def test_every_flag_lands_under_a_principle_or_at_least_a_chapter() -> None:
    """「5. 기타사항」은 핵심원칙이 없는 장이다 — 장까지 안 내려가면 빈 값이 나간다."""
    doc = "<body>" + _flag(
        "HasTheCompanyDisclosedItsCorporateValueupPlanAbstract", "자율공시 여부", "Y(O)"
    ) + "</body>"
    assert parse_compliance_flags(doc)[0]["principle"] == "5"


def test_the_answer_label_is_kept_because_one_question_can_ask_two_things() -> None:
    """「선임 사외이사, 집행임원제도 도입 여부」처럼 한 질문에 답이 둘 달린 것이 10종 있다."""
    shared = [f for f in COMPLIANCE_FLAGS if f["question"] == _TWO_IN_ONE]
    assert [f["label"] for f in shared] == [
        "선임사외이사 제도 시행 여부",
        "집행임원 제도 시행 여부",
    ]


def test_six_flags_are_the_same_fact_as_a_key_indicator() -> None:
    same = [f for f in COMPLIANCE_FLAGS if f.get("same_as_metric")]
    assert len(same) == 6
    assert "사외이사가 이사회 의장인지 여부" in {f["same_as_metric"] for f in same}


def test_form_tables_are_not_picked_up_as_flags() -> None:
    """서식 표도 `table-group` 이다 — `krx-cg_` 접두로 갈라야 섞이지 않는다."""
    doc = (
        f'<body><table-group aclass="{FORM_TABLES["7-2-1"]["aclass"]}">'
        '<table><tbody><tr><td valuetxt="Y(O)">Y(O)</td></tr></tbody></table></table-group>'
        + _flag("ShareholderProposalsAndImplementationStatusAbstract", "주주제안 여부", "N(X)")
        + "</body>"
    )
    found = parse_compliance_flags(doc)
    assert [f["label"] for f in found] == ["주주제안 여부"]


def test_one_concept_shared_by_two_groups_stays_two_flags() -> None:
    """같은 개념 코드를 두 그룹이 나눠 쓴다 — 개념만으로 키를 잡으면 하나가 사라진다."""
    doc = (
        "<body>"
        + _flag(_SHARED, "재무제표 정기주총 6주전 제공 여부", "Y(O)")
        + _flag(_SHARED, "연결재무제표 정기주총 4주전 제공 여부", "N(X)")
        + "</body>"
    )
    found = parse_compliance_flags(doc)
    assert [(f["label"], f["complied"]) for f in found] == [
        ("재무제표 정기주총 6주전 제공 여부", True),
        ("연결재무제표 정기주총 4주전 제공 여부", False),
    ]


def test_a_non_boolean_answer_is_not_folded_into_no() -> None:
    """배당을 안 한 회사는 「배당 미실시」라고 적는다 — 미준수와 같은 값이 아니다."""
    doc = "<body>" + _flag(
        "PredictabilityOfCashDividendAbstract", "예측가능성 제공 여부", "배당 미실시(No Dividend)"
    ) + "</body>"
    found = parse_compliance_flags(doc)
    assert found[0]["complied"] is None
    assert found[0]["value"] == "배당 미실시(No Dividend)"


def test_a_group_with_no_answer_is_not_a_flag() -> None:
    doc = (
        '<body><p>설명문</p><table-group aclass="SomethingAbstract">'
        "<table><tbody><tr><td>없음</td></tr></tbody></table></table-group></body>"
    )
    assert parse_compliance_flags(doc) == []


def test_unregistered_flag_falls_back_to_the_label_in_the_document() -> None:
    """서식이 개정되면 모르는 플래그가 온다 — 버리지 말고 원문 라벨로 실어 보낸다."""
    doc = "<body>" + _flag("BrandNewQuestionAbstract", "새 항목 여부", "Y(O)") + "</body>"
    found = parse_compliance_flags(doc)
    assert found[0]["label"] == "새 항목 여부"
    assert found[0]["same_as_metric"] == ""


def test_empty_document_yields_no_flags() -> None:
    assert parse_compliance_flags("") == []


def test_a_yes_is_not_always_good_so_no_rate_is_computed() -> None:
    """「불성실공시법인 지정」은 Y 가 제재를 받았다는 뜻이다 — Y 를 준수로 세면 방향이 뒤집힌다."""
    doc = "<body>" + _flag(
        "SanctionsRelatedToDisclosureAbstract", "불성실공시법인 지정여부", "Y(O)"
    ) + "</body>"
    found = parse_compliance_flags(doc)
    assert found[0]["complied"] is True  # 답이 Y 라는 사실만 싣는다
    assert "rate" not in found[0] and "score" not in found[0]
