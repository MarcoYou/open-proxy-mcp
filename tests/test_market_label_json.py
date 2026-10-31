"""json 응답의 시장 표기 통일 (261031) — DB 약어 KS/KQ 를 직렬화 경계(`as_pretty_json`)에서 KOSPI/KOSDAQ 로.

실사용 대화: 같은 `trading_data` 안에서 시세 단면·순위표는 「KOSPI」, 주가 곡선·시장 시총은 「KS」를 내보내
읽는 AI 가 두 시장을 가려 읽지 못했다. 서비스 안의 비교(렌더러의 `== "KS"` 등)는 약어 그대로 두고 밖으로 나가는 json 만 바꾼다.
network 0 · DB 0.
"""
from __future__ import annotations

import json

from open_proxy_mcp.services.contracts import _label_markets, as_pretty_json


def test_market_values_are_labelled_at_any_depth():
    p = {"data": {"market": "KS", "latest": [{"market": "KQ", "cap_krw": 1}, {"market": "KS", "cap_krw": 2}],
                  "buckets": [{"market": "kq", "label": "x"}], "markets": ["KS"], "nested": ({"market": "KQ"},)}}
    j = json.loads(as_pretty_json(p))["data"]
    assert j["market"] == "KOSPI"
    assert [x["market"] for x in j["latest"]] == ["KOSDAQ", "KOSPI"]
    assert j["buckets"][0]["market"] == "KOSDAQ"
    assert j["markets"] == ["KS"]                       # `market` 키만 — 다른 키의 값은 건드리지 않는다
    assert j["nested"][0]["market"] == "KOSDAQ"


def test_other_values_and_the_original_payload_are_untouched():
    p = {"data": {"market": "KOSPI", "scope": "market", "ticker": "KS", "x": {"market": None},
                  "m2": {"market": "시장 전체"}, "lst": {"market": ["KS", "KQ"]}}}
    j = json.loads(as_pretty_json(p))["data"]
    assert j["market"] == "KOSPI" and j["scope"] == "market" and j["ticker"] == "KS"
    assert j["x"]["market"] is None and j["m2"]["market"] == "시장 전체"
    assert j["lst"]["market"] == ["KOSPI", "KOSDAQ"]
    assert p["data"]["lst"]["market"] == ["KS", "KQ"]   # 원본(렌더러가 쓰는 payload)은 그대로
    assert _label_markets("KS") == "KS"                 # 키 없이 떠도는 값은 바꾸지 않는다
