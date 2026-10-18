# -*- coding: utf-8 -*-
"""주간 밸류에이션 배치의 KRX 종목유형 조회(`_krx_kinds`) — 실패 이유가 남는지 고정한다(261018).

배경: 10-18 02:42 KST 수동 실행에서 「종목유형 확보 실패 — 스냅샷 중단」이 났는데, 예외를 그냥 삼켜서
원인(KRX 일시 장애 / 키·권한 / 시간 초과)을 가를 근거가 로그에 없었다. 반환 계약(받은 행만 · 둘 다
실패면 빈 dict → 호출부가 건너뜀)은 그대로 두고, 시장별 실패 이유를 `::warning::` 한 줄로 남긴다.
network 0 · DB 0 — httpx.MockTransport 로 KRX 응답을 흉내 낸다.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from open_proxy_mcp.dart import krx_meter
from scripts import market_val_weekly as W

_KEY = "TEST-KRX-KEY-NOT-REAL"


def _rows(*pairs):
    return {"OutBlock_1": [{"ISU_SRT_CD": c, "KIND_STKCERT_TP_NM": k} for c, k in pairs]}


@pytest.fixture
def krx(monkeypatch):
    """엔드포인트 이름 → 응답(dict=200 JSON · httpx.Response · 예외)을 정해 두면 그대로 돌려준다."""
    original = httpx.AsyncClient
    calls = []
    monkeypatch.setenv("KRX_OPEN_API_KEY", _KEY)
    monkeypatch.delenv("KRX_API_KEY", raising=False)
    monkeypatch.setattr(krx_meter, "bump", lambda *a, **k: None)   # 콜 장부(운영 DB)는 건드리지 않는다

    def install(by_ep: dict):
        def handler(request):
            ep = request.url.path.rsplit("/", 1)[-1]
            calls.append((ep, request.url.params.get("basDd"), request.headers.get("AUTH_KEY")))
            res = by_ep[ep]
            if isinstance(res, Exception):
                raise res
            if isinstance(res, httpx.Response):
                return res
            return httpx.Response(200, json=res)
        monkeypatch.setattr(W.httpx, "AsyncClient", lambda **kw: original(
            **kw, transport=httpx.MockTransport(handler)))
        return calls

    return install


def _run(dd="20261016"):
    return asyncio.run(W._krx_kinds(dd))


def test_both_markets_ok_merges_and_stays_quiet(krx, capsys):
    calls = krx({"stk_isu_base_info": _rows(("005930", "보통주"), ("005935", "구형우선주")),
                 "ksq_isu_base_info": _rows(("035720", "보통주"))})
    kinds = _run()
    assert kinds == {"005930": "보통주", "005935": "구형우선주", "035720": "보통주"}
    assert [c[:2] for c in calls] == [("stk_isu_base_info", "20261016"), ("ksq_isu_base_info", "20261016")]
    assert all(c[2] == _KEY for c in calls)          # 키는 헤더로만 간다
    assert "::warning::" not in capsys.readouterr().out


def test_one_market_empty_keeps_the_other_and_says_which(krx, capsys):
    krx({"stk_isu_base_info": _rows(("005930", "보통주")),
         "ksq_isu_base_info": {"OutBlock_1": []}})
    kinds = _run()
    assert kinds == {"005930": "보통주"}
    out = capsys.readouterr().out
    assert out.count("::warning::") == 1
    assert "ksq_isu_base_info(basDd=20261016) 실패 — HTTP 200 · 0행" in out
    assert "stk_isu_base_info" not in out


def test_both_fail_returns_empty_with_a_reason_per_market(krx, capsys):
    """둘 다 실패 → 빈 dict(호출부의 「스냅샷 중단 · 기존 데이터 보존」 경로). 이유는 시장마다 다르게 남는다."""
    krx({"stk_isu_base_info": httpx.ReadTimeout("느림"),
         "ksq_isu_base_info": httpx.Response(503, text="<html>점검 중</html>")})
    assert _run() == {}
    out = capsys.readouterr().out
    assert "stk_isu_base_info(basDd=20261016) 실패 — ReadTimeout" in out
    assert "ksq_isu_base_info(basDd=20261016) 실패 — HTTP 503 · JSONDecodeError" in out
    assert "점검 중" not in out                       # 응답 본문은 옮기지 않는다
    assert _KEY not in out                            # 키는 어디에도 찍지 않는다


def test_error_json_without_a_list_is_named(krx, capsys):
    """KRX 가 목록 없이 오류 JSON 만 줄 때(인증·권한 문제 꼴) — 종전엔 StopIteration 을 삼켜 아무 흔적이 없었다."""
    krx({"stk_isu_base_info": httpx.Response(401, json={"respCode": "401", "respMsg": "Unauthorized"}),
         "ksq_isu_base_info": httpx.Response(401, json={"respCode": "401", "respMsg": "Unauthorized"})})
    assert _run() == {}
    out = capsys.readouterr().out
    assert out.count("HTTP 401 · 응답에 목록 없음") == 2


def test_broken_row_keeps_rows_already_read(krx, capsys):
    """행 중간에 단축코드가 빠져 있으면 그 앞까지 받은 행은 남긴다(종전 동작) — 몇 행 뒤에 깨졌는지 적는다."""
    krx({"stk_isu_base_info": {"OutBlock_1": [{"ISU_SRT_CD": "005930", "KIND_STKCERT_TP_NM": "보통주"},
                                              {"KIND_STKCERT_TP_NM": "보통주"}]},
         "ksq_isu_base_info": _rows(("035720", "보통주"))})
    assert _run() == {"005930": "보통주", "035720": "보통주"}
    assert "stk_isu_base_info(basDd=20261016) 실패 — HTTP 200 · KeyError(1행 받은 뒤)" in capsys.readouterr().out
