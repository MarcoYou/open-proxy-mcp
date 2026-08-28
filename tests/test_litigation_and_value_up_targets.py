"""소송 본문 파싱 · 밸류업 수치 목표 대조 (260828 U 지적 12·13번 회귀).

두 지적 모두 「도구가 못 준 것을 못 줬다고 말하지 않는다」의 사례였다.
- 소송: 본문에서 사건명을 읽고도 경영권/상거래로 분류될 때만 남겨 「미상」으로 나갔다.
- 밸류업: 수치 목표를 실적과 대조하지 않아 사용자가 직접 나눠 계산했다.
"""

from open_proxy_mcp.services.proxy_contest import parse_litigation_form
from open_proxy_mcp.services.value_up import (
    _judge,
    extract_numeric_targets,
)


_RULING_DOC = """
<html><body>고려아연/소송등의판결ㆍ결정/(2026.07.13)
1. 사건의 명칭 손해배상 청구 사건번호 2025가합9268
2. 원고ㆍ신청인 1. 주식회사 영풍 2. 주식회사 한국기업투자홀딩스
3. 판결ㆍ결정내용 [주문] 1. 피고는 원고 주식회사 영풍에게 100,000,000원을 지급하라.
4. 판결ㆍ결정사유 피고의 위법한 의결권 제한행위로 원고 영풍은 의결권을 침해당하였다.
5. 관할법원 서울중앙지방법원
6. 판결ㆍ결정일자 2026-07-10
7. 확인일자 2026-07-13
8. 기타 투자판단과 관련한 중요사항 1. 상기 '7. 확인일자'는 당사가 결정문을 전달받은 일자입니다.
※ 관련공시 2025-03-26 소송 등의 제기ㆍ신청(경영권 분쟁 소송)
</body></html>
"""

# 회사가 「추후 정정기재」로 비워 둔 서식. 「파싱 실패」와 뜻이 다르다.
_BLANK_FIELDS_DOC = """
<html><body>1. 사건의 명칭 검사인 선임 사건번호 2026비합30369
2. 원고(신청인) -
3. 청구내용 -
4. 관할법원 서울중앙지방법원
6. 제기ㆍ신청일자 2026-08-18
7. 확인일자 -
8. 기타 투자판단과 관련한 중요사항 1. 상기 '1. 원고(신청인)', '3. 청구내용'은 신청인의 문서를
당사가 송달받는대로 정정기재할 예정입니다.
</body></html>
"""


def test_ruling_document_carries_case_name_and_verbatim_text():
    """분류 불가 사건명(「손해배상」)도 원문·당사자와 함께 남는다 — 이것이 12번 지적의 핵심."""
    parsed = parse_litigation_form(_RULING_DOC)
    assert parsed["status"] == "parsed"
    fields = parsed["fields"]
    assert fields["case_name"] == "손해배상 청구"
    assert fields["case_number"] == "2025가합9268"
    assert "영풍" in fields["parties"] and "한국기업투자홀딩스" in fields["parties"]
    # 청구/주문은 요약이 아니라 원문 그대로여야 인용할 수 있다.
    assert "100,000,000원을 지급하라" in fields["ruling"]
    assert fields["court"] == "서울중앙지방법원"
    assert parsed["excerpt"].startswith("1. 사건의 명칭")


def test_blank_field_is_reported_as_absent_not_as_parse_failure():
    parsed = parse_litigation_form(_BLANK_FIELDS_DOC)
    assert parsed["status"] == "parsed"          # 사건명은 읽혔다
    assert parsed["fields"]["parties"] == ""     # 회사가 안 적었다
    assert "parties" in parsed["absent_fields"]
    assert "claim" in parsed["absent_fields"]
    # 회사가 「왜 비었는지」를 적어 둔 칸을 버리지 않는다.
    assert "정정기재할 예정" in parsed["fields"]["other_material"]


def test_case_number_is_not_taken_from_the_footnote():
    """서식의 사건번호 칸이 없으면 「기타」 안내문 문장이 값으로 새어 들어왔다."""
    doc = ("<html>1. 사건의 명칭 신주발행무효의 소 2. 원고(신청인) 주식회사 엠제이파트너스 "
           "4. 관할법원 서울중앙지방법원 "
           "8. 기타 투자판단과 관련한 중요사항 (1) 상기 '1. 사건의 명칭'의 사건번호는 "
           "서울중앙지방법원 2026가합3015 입니다.</html>")
    fields = parse_litigation_form(doc)["fields"]
    assert fields["case_name"] == "신주발행무효의 소"
    assert "case_number" not in fields or not fields["case_number"]


def test_unrecognized_form_says_so_and_still_returns_text():
    parsed = parse_litigation_form("<html><body>알 수 없는 서식입니다.</body></html>")
    assert parsed["status"] == "form_unrecognized"
    assert "알 수 없는 서식" in parsed["excerpt"]


_PLAN_TEXT = (
    "3. 목표설정 a. 중장기 목표 부채비율 10% 이하 수준 유지 영업이익률 15% 이상 확대 "
    "ROE 10% 이상 개선 PBR 1배 이상 달성 추진 b. 주주환원 안정적인 현금배당 지속 "
    "중장기 배당성향 30~40% 수준 확대 유지 FY2025는 40% 배당 계획"
)


def test_numeric_targets_extracted_with_direction_and_range():
    targets, unparsed = extract_numeric_targets(_PLAN_TEXT)
    by_key = {t["metric_key"]: t for t in targets}
    assert set(by_key) == {"debt_ratio", "operating_margin", "roe", "pbr", "payout_ratio"}
    assert (by_key["debt_ratio"]["target_low"], by_key["debt_ratio"]["comparator"]) == (10.0, "이하")
    assert (by_key["roe"]["target_low"], by_key["roe"]["comparator"]) == (10.0, "이상")
    assert (by_key["pbr"]["target_low"], by_key["pbr"]["unit"]) == (1.0, "배")
    payout = by_key["payout_ratio"]
    assert (payout["target_low"], payout["target_high"], payout["comparator"]) == (30.0, 40.0, "범위")
    assert not unparsed


def test_target_text_is_verbatim_from_the_filing():
    targets, _ = extract_numeric_targets(_PLAN_TEXT)
    for t in targets:
        assert t["target_text"] in _PLAN_TEXT
        assert t["source_text"] in _PLAN_TEXT


def test_metric_mentioned_without_a_number_is_reported_not_dropped():
    targets, unparsed = extract_numeric_targets("ROE 개선을 지속 추진하겠습니다.")
    assert not targets
    assert [u["metric_label"] for u in unparsed] == ["ROE"]
    assert "ROE 개선을 지속" in unparsed[0]["source_text"]


def test_judgement_covers_achieved_missed_and_undecidable():
    ge = {"target_low": 10.0, "target_high": None, "comparator": "이상", "target_text": "ROE 10% 이상"}
    assert _judge(12.0, ge)[0] == "달성"
    assert _judge(6.09, ge)[0] == "미달"
    le = {"target_low": 10.0, "target_high": None, "comparator": "이하", "target_text": "부채비율 10% 이하"}
    assert _judge(7.36, le)[0] == "달성"
    rng = {"target_low": 30.0, "target_high": 40.0, "comparator": "범위", "target_text": "배당성향 30~40%"}
    assert _judge(35.0, rng)[0] == "달성"
    assert _judge(28.96, rng)[0] == "미달"
    # 실적을 못 받았으면 「달성」도 「미달」도 아니다.
    assert _judge(None, ge)[0] == "대조 못 함"
    # 방향이 없는 목표를 임의로 정하지 않는다.
    vague = {"target_low": 10.0, "target_high": None, "comparator": "", "target_text": "ROE 10% 수준"}
    verdict, note = _judge(6.0, vague)
    assert verdict == "판정 보류" and "방향" in note
