"""정렬 근거는 **실제로 쓴 것**만 말한다 — 네트워크 0.

260909: 시총 자료가 통째로 없어도(참조 파일 부재·DB 타임아웃) `ranking_signal` 이
`local_popularity_prior` 로 박혀, 같은 응답의 `market_data_source: "none"` 과 모순됐다.
그 경우 정렬은 사실상 원장 최신순(종목코드 보유 여부 + modify_date)이다.
"""
from open_proxy_mcp.company_resolver import MarketContext
from open_proxy_mcp.services.company import _resolution_reasons


def _signal(mc: MarketContext) -> str:
    """company_resolver 가 meta 에 넣는 것과 같은 판정(그 자리와 한 벌로 읽어야 한다)."""
    return ("market_cap" if mc.as_of_date
            else ("local_popularity_prior" if mc.market_caps else "registry_recency"))


def test_no_market_data_means_registry_recency():
    mc = MarketContext(market_caps={})
    assert mc.source == "none", "시총 자료가 없으면 source 는 none 이다"
    assert _signal(mc) == "registry_recency"


def test_local_caps_without_asof_still_say_prior():
    mc = MarketContext(market_caps={"005930": 1}, source="local_popularity_prior")
    assert _signal(mc) == "local_popularity_prior"


def test_reason_text_names_the_signal_it_actually_used():
    for sig, ko in (("market_cap", "시가총액"),
                    ("local_popularity_prior", "로컬 인기도 prior"),
                    ("registry_recency", "등록 최신순")):
        r = _resolution_reasons("token", active_registry_used=True, ranking_signal=sig)
        assert ko in r["ko"], f"{sig} 의 근거 문구가 그 신호를 말하지 않는다"

    # 알 수 없는 값이 와도 **없는 근거를 주장하지 않는다**(prior 라고 말하지 않는다).
    r = _resolution_reasons("token", active_registry_used=True, ranking_signal="")
    assert "인기도" not in r["ko"]
