"""공시의 정식 상호 → 짧은 DART 등록명. 법인격 표기 · 이름 가운데·끝 음차 · 리츠 법적 상호.

261018 실측(asset_holdings 가 타법인출자현황의 보유 법인명을 공용 해석기로 종목코드로 바꾸다 발견):
「삼성에스디아이㈜」·「삼성이앤에이㈜」는 0건, 「한화갤러리아㈜」는 추정으로만 잡혔다(추정 후보를 받지 않는 호출자에겐 미해결).
리츠는 등록명이 「롯데리츠」·「SK리츠」인데 공시는 「…위탁관리부동산투자회사」로 적는다. 네트워크 0.
"""
from __future__ import annotations

from open_proxy_mcp.company_resolver import CompanyResolver, MarketContext, spelling_variants, strip_legal_form

CORPS = [
    {"corp_code": "00126362", "corp_name": "삼성SDI", "corp_eng_name": "SAMSUNG SDI CO., LTD.", "stock_code": "006400"},
    {"corp_code": "00126439", "corp_name": "삼성E&A", "corp_eng_name": "Samsung E&A Co., Ltd.", "stock_code": "028050"},
    {"corp_code": "01727219", "corp_name": "한화갤러리아", "corp_eng_name": "Hanwha Galleria Co., Ltd.", "stock_code": "452260"},
    {"corp_code": "00124911", "corp_name": "한화갤러리아타임월드", "corp_eng_name": "Hanwha Galleria Timeworld", "stock_code": "027390"},
    {"corp_code": "00181712", "corp_name": "SK", "corp_eng_name": "SK Inc.", "stock_code": "034730"},
    {"corp_code": "01535150", "corp_name": "SK리츠", "corp_eng_name": "SK REIT Co., Ltd.", "stock_code": "395400"},
    {"corp_code": "01363818", "corp_name": "롯데리츠", "corp_eng_name": "LOTTE REIT Co., Ltd.", "stock_code": "330590"},
    {"corp_code": "00544452", "corp_name": "이리츠코크렙", "corp_eng_name": "E KOCREF CR-REIT", "stock_code": "088260"},
    {"corp_code": "00145880", "corp_name": "알파AI", "corp_eng_name": "Alpha AI", "stock_code": "043100"},
    {"corp_code": "00126380", "corp_name": "삼성전자", "corp_eng_name": "SAMSUNG ELECTRONICS CO,.LTD", "stock_code": "005930"},
]
MARKET = MarketContext(
    market_caps={c["stock_code"]: 1_000 - i for i, c in enumerate(CORPS)},
    active_tickers=frozenset(c["stock_code"] for c in CORPS),
    as_of_date="20261016",
)


def _resolver(corps=CORPS) -> CompanyResolver:
    return CompanyResolver(corps, {}, MARKET)


def _strong(query: str, corps=CORPS) -> list[str]:
    """강한 일치(추정 아님)로 잡힌 종목코드들 — 추정이면 빈 목록."""
    res = _resolver(corps).search(query)
    return [] if not res or res[0]["_resolution"]["inferred"] else [c["stock_code"] for c in res]


def test_legal_form_on_an_official_name_is_still_an_official_match():
    """「㈜」가 붙으면 정식명 일치를 못 하고 normalized 로 내려가, 같은 글자로 시작하는 「한화갤러리아타임월드」 때문에
    브랜드 검색(추정)으로 떨어졌다."""
    for q in ("한화갤러리아㈜", "(주)한화갤러리아", "주식회사 한화갤러리아", "한화갤러리아 주식회사"):
        res = _resolver().search(q)
        assert [c["stock_code"] for c in res] == ["452260"], q
        assert res[0]["_resolution"]["match_kind"] == "official" and not res[0]["_resolution"]["inferred"], q
    assert _strong("한화갤러리아") == ["452260"]                         # 법인격 없는 원래 질의는 그대로
    assert strip_legal_form("㈜한화갤러리아") == strip_legal_form("한화갤러리아(주)") == "한화갤러리아"


def test_letter_spelling_in_the_middle_or_end_of_a_name_resolves_to_the_registered_name():
    assert _strong("삼성에스디아이㈜") == _strong("삼성에스디아이") == ["006400"]
    assert _strong("삼성이앤에이㈜") == ["028050"]                       # 「앤」은 &(and) — 이·에이가 E·A
    assert _strong("알파에이아이") == ["043100"]                        # '알'(R)은 되돌리지 않은 조합이 맞는다


def test_reit_legal_names_resolve_to_the_short_registered_name():
    assert _strong("롯데위탁관리부동산투자회사") == ["330590"]
    assert _strong("에스케이위탁관리부동산투자회사㈜") == ["395400"]          # SK리츠 — 꼬리를 뗀 「에스케이」(=SK)보다 먼저
    assert _strong("이리츠코크렙기업구조조정부동산투자회사") == ["088260"]      # 「…리츠」가 없으면 꼬리를 뗀 이름


def test_variants_are_grouped_reit_suffix_replaced_before_stripped():
    groups = spelling_variants("에스케이위탁관리부동산투자회사")
    assert "sk위탁관리부동산투자회사" in groups[0]
    assert "sk리츠" in groups[1] and "sk" in groups[2]
    assert "삼성eanda" in spelling_variants("삼성이and에이")[0]
    assert "뉴엔ai" in spelling_variants("뉴엔에이아이")[0]               # 연쇄 뒷부분만 — '엔'은 이름 글자(뉴엔AI)
    assert "형지iandc" in spelling_variants("형지아이and씨")[0]           # 형지I&C
    assert spelling_variants("삼성전자") == [[]]                         # 음차표 글자가 없으면 변형도 없다


def test_a_spelling_variant_is_never_taken_from_a_weak_or_split_match():
    # 서로 다른 두 회사가 같은 묶음의 변형으로 정확히 맞으면 고르지 않는다
    split = CORPS + [{"corp_code": "09999999", "corp_name": "R파AI", "corp_eng_name": "", "stock_code": "999990"}]
    assert _strong("알파에이아이", corps=split) == []
    # 등록명과 정확히 같아지지 않는 변형(삼성sd)은 강한 결과를 만들지 않는다
    assert "006400" not in _strong("삼성에스디")
