"""가격 생략 사유의 빠른 검색도 기존 첫 매치의 문구·좌표·그룹을 보존한다."""
import random
import re

import pytest

from open_proxy_mcp.services import biz_fields as bf


LEGACY = re.compile(
    r"(?:가격\s*(?:변동\s*)?(?:추이|현황))?[\s\S]{0,160}?"
    r"(?:기재(?:는|를)?\s*생략|기재하지\s*않|산출.{0,25}?(?:어렵|곤란|부적합))"
)


def details(match):
    return None if match is None else (match.span(), match.group(0), match.groups(), match.groupdict())


@pytest.mark.parametrize('text', [
    '', '가격 변동 추이만 공시', '가격\n현황 없음',
    '기재 생략', '기재는 생략합니다', '기재를\n생략', '기재하지\t않음',
    '산출이 어렵습니다', '산출 곤란', '산출 부적합',
    '산출' + '가' * 25 + '어렵', '산출' + '가' * 26 + '어렵',
    '산출\n어렵', '산출\r어렵',
    '앞' * 500 + '기재 생략',
    '가격' + ' ' * 500 + '변동' + '\n' * 300 + '추이' + '뒤' * 160 + '기재 생략',
    '가격' + ' ' * 500 + '현황' + '뒤' * 161 + '기재 생략',
    '가격현황' + '뒤' * 161 + '기재 생략' + '기재하지 않음',
    '가격' * 200 + '기재 생략',
    '가격' + ' ' * 300 + '현황' + '산출어렵' + '뒤' * 500 + '기재 생략',
    '가' * 500 + '가격' + ' ' * 200 + '현황기재 생략',
])
def test_exact_first_match_including_unbounded_prefix_spaces(text):
    assert bf._PRODUCT_PRICING_NA.pattern == LEGACY.pattern
    assert bf._PRODUCT_PRICING_NA.flags == LEGACY.flags
    assert details(bf._pricing_na_match(bf._PRODUCT_PRICING_NA, text)) == details(LEGACY.search(text))


@pytest.mark.parametrize('gap', [0, 1, 159, 160, 161, 162])
def test_prefix_at_or_across_the_bounded_start_is_not_lost(gap):
    text = '앞' * 300 + '가격' + '\n' * 500 + '추이' + '뒤' * gap + '기재 생략'
    assert details(bf._pricing_na_match(bf._PRODUCT_PRICING_NA,text)) == details(LEGACY.search(text))


def test_generated_competing_prefixes_and_reasons_match_legacy():
    rng = random.Random(261109)
    parts = ['가격', '현황', '추이', '변동', '기재', '는', '를', '생략', '하지', '않',
             '산출', '어렵', '곤란', '부적합', 'x', '\n', '\r', '\t', ' ' * 180,
             '가격' + ' ' * 180 + '변동\n추이', '기재를 생략', '산출이 어렵']
    for _ in range(3000):
        text = ''.join(rng.choices(parts,k=rng.randrange(1,30)))
        assert details(bf._pricing_na_match(bf._PRODUCT_PRICING_NA,text)) == details(LEGACY.search(text))


def test_absent_reason_skips_the_broad_search(monkeypatch):
    class NoBroadSearch:
        def search(self,*a,**k):
            raise AssertionError('사유가 없는 본문은 넓은 검색이 필요 없다')
    pattern = NoBroadSearch()
    monkeypatch.setattr(bf,'_PRODUCT_PRICING_NA',pattern)
    assert bf._pricing_na_match(pattern,'가격 추이\n실제 판매 가격 원문' * 1000) is None


def test_other_absence_patterns_keep_their_original_behavior():
    text = '원재료는 해당 사항이 없습니다'
    assert details(bf._pricing_na_match(bf._RAW_MATERIALS_NA,text)) == details(bf._RAW_MATERIALS_NA.search(text))
    custom = re.compile('산출.*어렵',re.S)
    assert details(bf._pricing_na_match(custom,'산출\n어렵')) == details(custom.search('산출\n어렵'))
