"""정기보고서 제출 법인 명부 — 「상장사냐」가 아니라 「공시 의무가 있느냐」로 가른다.

260823 실측 근거:
  · DART 는 상장폐지돼도 stock_code 를 안 지운다 — 신한은행 000010·우리은행 000030·
    KB손해보험 002550 전부 미상장인데 코드가 남아 있다.
  · 반대로 농협금융지주·농협생명보험은 **한 번도 상장된 적 없어 코드가 없는데**
    정기보고서를 낸다. stock_code 로 거르면 앞은 우연히 통과하고 뒤는 영영 막힌다.
  · 같은 명부가 동명 법인도 가른다 — 국민은행 원장 3건 중 정기보고서를 내는 것은
    00386937 하나뿐(나머지는 2017-06-30 에 멈춘 소멸 법인)이다.
"""
from __future__ import annotations

from open_proxy_mcp.company_resolver import CompanyResolver

_UNLISTED_FILER = {"corp_code": "00908021", "corp_name": "농협금융지주",
                   "corp_eng_name": "", "stock_code": "", "modify_date": "20260303"}
_UNLISTED_SPC = {"corp_code": "02043993", "corp_name": "카드오토할부제팔차",
                 "corp_eng_name": "", "stock_code": "", "modify_date": "20260820"}
_LISTED = {"corp_code": "00126380", "corp_name": "삼성전자",
           "corp_eng_name": "SAMSUNG ELECTRONICS", "stock_code": "005930",
           "modify_date": "20260101"}


def _resolver(filers: frozenset[str] | None) -> CompanyResolver:
    return CompanyResolver([_LISTED, _UNLISTED_FILER, _UNLISTED_SPC], {}, None, filers)


def test_unlisted_periodic_filer_is_searchable_by_name() -> None:
    """농협금융지주는 종목코드가 없지만 정기보고서를 내므로 이름으로 찾혀야 한다."""
    hits = _resolver(frozenset({"00908021"})).search("농협금융지주")

    assert [h["corp_code"] for h in hits] == ["00908021"]


def test_unlisted_non_filer_stays_out_of_the_name_index() -> None:
    """유동화 SPC 는 정기보고서를 안 내므로 이름 색인에 들어가면 안 된다.

    이름 규칙(「제○차」·「유동화전문」)을 손으로 만들지 않아도 명부가 걸러 준다.
    """
    assert _resolver(frozenset({"00908021"})).search("카드오토할부제팔차") == []


def test_without_the_registry_nothing_unlisted_is_searchable() -> None:
    """명부를 못 만들었으면(네트워크·쿼터) 예전대로 — 비상장은 이름으로 안 찾힌다."""
    assert _resolver(None).search("농협금융지주") == []
    assert [h["corp_code"] for h in _resolver(None).search("삼성전자")] == ["00126380"]


def test_registry_splits_same_named_corps_by_who_still_files() -> None:
    """동명 법인 — 살아 있는 쪽만 남는다. 국민은행 실제 배치를 그대로 옮겼다."""
    dead = {"corp_code": "00104467", "corp_name": "국민은행", "corp_eng_name": "",
            "stock_code": "031150", "modify_date": "20170630"}
    alive = {"corp_code": "00386937", "corp_name": "국민은행", "corp_eng_name": "",
             "stock_code": "060000", "modify_date": "20250102"}
    resolver = CompanyResolver([dead, alive], {}, None, frozenset({"00386937"}))

    hits = resolver.search("국민은행")

    assert [h["corp_code"] for h in hits][0] == "00386937"
