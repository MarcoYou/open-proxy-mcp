# -*- coding: utf-8 -*-
"""KRX Open API 일별매매정보(`stk_bydd_trd`·`ksq_bydd_trd`) 호출 — **서버 안 KRX 시세 호출의 한 곳**.

종전에는 같은 호출이 네 곳에 따로 적혀 있었다 — 배수 도구의 라이브 보조(`price_multiple_data._krx_market_live`), 시세 확인
(`trading._krx_quote_row`), 백그라운드 갱신기(`krx_refresher._fetch_day`), DART 클라이언트의 종목 시세(`DartClient._krx_stock_price`).
키 읽기·주소·호출 장부(`krx_meter.bump`)·목록 꺼내기가 네 번 적혔고, 넷 다 **실패를 빈 목록으로 삼켰다** — 키 만료·한도·장애·전송 오류가
「그 날 시세가 없다」(휴장·게시 전)와 같은 모양으로 올라와, 읽는 쪽이 오류를 자료 없음으로 읽거나(DATA-PX8) 휴장으로 굳혔다.
이 모듈은 호출을 한 함수(`fetch_day`)로 모으고 결과에 **상태**를 붙인다:

  `ok`          한 시장이라도 행이 왔고 호출이 모두 성공했다
  `empty`       호출은 모두 성공했는데 행이 없다 — 게시 전이거나 휴장. **자료 없음의 답**이다
  `auth`        키가 없거나 인증·권한 거절(HTTP 401·403)
  `limit`       호출 한도 초과(HTTP 429)
  `http_error`  그 밖의 HTTP 오류 · 본문이 JSON 이 아님 · 오류 코드 본문
  `transport`   전송 오류(시간 초과·연결 실패)

`empty` 만 「자료 없음」이다. 나머지 넷은 **실패**라 호출자가 휴장·자료 없음으로 적으면 안 된다(캐시에 담지도 않는다).
호출 장부(`krx_meter.bump`)는 여기서 한 번씩만 센다 — 호출자가 따로 세지 않는다. 키·응답 본문은 로그에 남기지 않는다.

받은 행은 **줄인 행**으로 돌려준다(`ROW_KEYS` — 시세 확인·배수·일별 저장분 기록이 쓰는 칸만. 응답 한 행은 15칸이라 전종목 한 날이
수 MB 였다). 시장은 호출한 endpoint 로 태그한다(`MARKET_TAG`: `KS`/`KQ` — `krx_daily.daily_records` 와 같은 기준).

이 모듈은 네트워크만 다룬다 — DB 도, 캐시도 없다(캐시는 호출자마다 목적이 달라 호출자가 둔다). `market_codes` 만 읽는 잎 모듈이라
어느 모듈에서든 순환 없이 import 한다.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, NamedTuple

from open_proxy_mcp.market_codes import KQ as MKT_KQ
from open_proxy_mcp.market_codes import KS as MKT_KS

logger = logging.getLogger(__name__)

BASE_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/"
KOSPI_URL = BASE_URL + "stk_bydd_trd"
KOSDAQ_URL = BASE_URL + "ksq_bydd_trd"
TIMEOUT_SEC = 30

OK, EMPTY = "ok", "empty"
HTTP_ERROR, AUTH, LIMIT, TRANSPORT = "http_error", "auth", "limit", "transport"
#: 실패 상태 — 「그 날 시세가 없다」가 아니다.
ERROR_STATUSES = frozenset({HTTP_ERROR, AUTH, LIMIT, TRANSPORT})
#: 실패 상태를 사람이 읽는 말로 — 경고 문구가 같은 말을 쓰게 한다.
STATUS_LABEL = {
    AUTH: "KRX 인증 실패(키 없음·만료·권한)",
    LIMIT: "KRX 호출 한도 초과",
    HTTP_ERROR: "KRX 응답 오류",
    TRANSPORT: "KRX 전송 오류(시간 초과·연결 실패)",
}

#: 줄인 행에 남기는 칸 — 시세 확인(`trading._QUOTE_FIELDS`)·배수(종가·시총·상장주식수)·일별 기록(`krx_daily.daily_records`)·
#: DART 클라이언트의 as-of 검산(`BAS_DD`)이 읽는 칸의 합집합. 칸을 더 쓰려면 여기 한 곳만 고친다.
KEEP_KEYS = ("ISU_CD", "BAS_DD", "MKT_NM", "TDD_CLSPRC", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "CMPPREVDD_PRC",
             "ACC_TRDVOL", "ACC_TRDVAL", "MKTCAP", "LIST_SHRS", "FLUC_RT")
#: 호출한 endpoint 로 붙이는 시장 태그(`KS`/`KQ`) — KRX 의 `MKT_NM` 이 아니라 endpoint 기준이다.
MARKET_TAG = "_MKT"
ROW_KEYS = KEEP_KEYS + (MARKET_TAG,)


class DayFetch(NamedTuple):
    """`fetch_day` 결과 — (코스피 행, 코스닥 행, KRX 호출 수, 상태). 4개로 풀어 쓸 수 있다."""
    kospi: list
    kosdaq: list
    calls: int
    status: str


def api_key() -> str | None:
    return os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY") or None


_NO_KEY_WARNED = {"done": False}


def _warn_no_key(basDd: str) -> None:
    """키 없음은 설정 문제라 프로세스당 한 번만 경고로 남긴다(키 없는 로컬·파일럿 서버가 요청마다 줄을 쓰지 않게)."""
    if _NO_KEY_WARNED["done"]:
        logger.debug("KRX 키 없음 basDd=%s", basDd)
        return
    _NO_KEY_WARNED["done"] = True
    logger.warning("KRX 키가 없다(KRX_API_KEY·KRX_OPEN_API_KEY) basDd=%s — 호출하지 않았다(이 경고는 프로세스당 한 번)", basDd)


def to_int(v: Any) -> int | None:
    """KRX 숫자 문자열(쉼표 포함)을 정수로. 비었거나 '-' 면 None."""
    try:
        return int(str(v).replace(",", "")) if v not in (None, "", "-") else None
    except Exception:
        return None


def reduce_row(row: dict, market: str | None = None) -> dict:
    """응답 한 행 → 줄인 행(`ROW_KEYS`). 없는 칸은 None 으로 둔다(칸 집합이 늘 같다)."""
    out = {k: row.get(k) for k in KEEP_KEYS}
    out[MARKET_TAG] = market
    return out


def index_rows(*lists: list) -> dict[str, dict]:
    """{단축코드: 줄인 행} — 같은 종목이 두 번 오면 마지막 것이 이긴다(`krx_daily.write_daily` 와 같은 규칙)."""
    return {r["ISU_CD"]: r for rows in lists for r in rows if r.get("ISU_CD")}


def split_markets(day: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """{단축코드: 줄인 행} → (코스피 행들, 코스닥 행들) — 행의 시장 태그로 가른다. 태그 없는 행은 어느 쪽에도 넣지 않는다."""
    return ([r for r in day.values() if r.get(MARKET_TAG) == MKT_KS],
            [r for r in day.values() if r.get(MARKET_TAG) == MKT_KQ])


def _from_http(code: int) -> str:
    if code in (401, 403):
        return AUTH
    if code == 429:
        return LIMIT
    return HTTP_ERROR


def _read_body(data: Any) -> tuple[list | None, str]:
    """200 응답 본문 → (행 목록 또는 None, 상태). 목록 없이 오류 코드만 온 본문(`respCode`)은 실패로 가른다."""
    if not isinstance(data, dict):
        return None, HTTP_ERROR
    rows = next((v for v in data.values() if isinstance(v, list)), None)
    if rows is not None:
        return rows, (OK if rows else EMPTY)
    code = str(data.get("respCode") or "").strip()
    if code[:3] in ("401", "403"):
        return None, AUTH
    if code[:3] == "429":
        return None, LIMIT
    if code[:1] in ("4", "5"):
        return None, HTTP_ERROR
    return None, EMPTY                       # 목록도 오류 코드도 없다 — 종전처럼 빈 응답으로 본다


async def _one(h: Any, url: str, basDd: str, key: str, market: str, own_client: bool) -> tuple[list, str]:
    """한 endpoint 를 한 번 부른다 → (줄인 행들, 상태). 예외를 올리지 않는다."""
    from open_proxy_mcp.dart import krx_meter
    label = "KOSPI" if market == MKT_KS else "KOSDAQ"
    krx_meter.bump()                          # KRX 일별 사용량 장부 — 메모리 계수만(배경 스레드가 쓴다)
    try:
        kw = {} if own_client else {"timeout": TIMEOUT_SEC}
        r = await h.get(url, headers={"AUTH_KEY": key}, params={"basDd": basDd}, **kw)
    except Exception as exc:                  # noqa: BLE001 — 실패는 상태로 올리고 호출 경로는 깨지 않는다
        logger.warning("KRX %s 호출 실패(전송) basDd=%s: %s", label, basDd, type(exc).__name__)
        return [], TRANSPORT
    code = r.status_code
    if code != 200:
        status = _from_http(code)
        logger.warning("KRX %s 응답 %s basDd=%s — %s", label, code, basDd, STATUS_LABEL[status])
        return [], status
    try:
        data = r.json()
    except Exception as exc:                  # noqa: BLE001
        logger.warning("KRX %s 응답이 JSON 이 아니다 basDd=%s: %s", label, basDd, type(exc).__name__)
        return [], HTTP_ERROR
    rows, status = _read_body(data)
    if status in ERROR_STATUSES:
        logger.warning("KRX %s 오류 본문 basDd=%s — %s", label, basDd, STATUS_LABEL[status])
        return [], status
    return [reduce_row(x, market) for x in rows or ()], status


async def fetch_day(basDd: str, both: bool = False, *, ticker: str | None = None, http: Any = None) -> DayFetch:
    """한 거래일 전종목을 받는다 → `DayFetch(코스피 행, 코스닥 행, 호출 수, 상태)`.

    `both=False`(기본)면 코스피를 먼저 부르고, 코스피가 비었거나 실패했으면(게시 전·휴장·오류) 코스닥은 부르지 않는다 — 확인만 하는 호출은 1콜이다.
    `ticker` 를 주면(한 종목만 필요한 호출) 코스피에 그 종목이 있을 때도 코스닥을 부르지 않는다(그 경우 `kosdaq` 은 비어 있다).
    `both=True` 는 두 시장이 다 필요한 호출(전종목 스냅샷·휴장 판정처럼 두 시장이 다 비었는지 알아야 하는 호출)이다 — 코스피가 비어도 코스닥을 묻고,
    **두 시장을 동시에 부른다**(종전 배수 라이브·시세 확인이 병렬로 부르던 대기 시간 그대로. 인증·한도 오류면 코스닥 호출 하나가 헛되이 나간다).
    `http` 를 주면 그 클라이언트로 부른다(DART 클라이언트가 자기 연결 풀을 재사용) — 없으면 호출마다 새로 만든다.
    상태: 호출한 것 중 하나라도 실패면 그 실패(`auth` > `limit` > `transport` > `http_error`), 모두 성공이면 행이 있으면 `ok`, 없으면 `empty`.
    일부만 받았어도 받은 행은 돌려준다 — 쓸지는 호출자가 상태를 보고 정한다.
    """
    key = api_key()
    if not key:
        _warn_no_key(basDd)
        return DayFetch([], [], 0, AUTH)
    import httpx
    calls = 0
    results: list[tuple[list, str]] = []

    async def run(h: Any, own: bool) -> None:
        nonlocal calls
        if both:
            calls = 2
            results.extend(await asyncio.gather(_one(h, KOSPI_URL, basDd, key, MKT_KS, own),
                                                _one(h, KOSDAQ_URL, basDd, key, MKT_KQ, own)))
            return
        calls = 1
        kospi, st = await _one(h, KOSPI_URL, basDd, key, MKT_KS, own)
        results.append((kospi, st))
        if not kospi:                    # 게시 전·휴장·실패 — 코스닥은 묻지 않는다(실패했는데 같은 키로 한 번 더 부를 이유도 없다)
            return
        if ticker and any(r.get("ISU_CD") == ticker for r in kospi):
            return
        calls = 2
        results.append(await _one(h, KOSDAQ_URL, basDd, key, MKT_KQ, own))

    if http is not None:
        await run(http, False)
    else:
        async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as h:
            await run(h, True)
    kospi = results[0][0]
    kosdaq = results[1][0] if len(results) > 1 else []
    statuses = [s for _, s in results]
    for bad in (AUTH, LIMIT, TRANSPORT, HTTP_ERROR):
        if bad in statuses:
            return DayFetch(kospi, kosdaq, calls, bad)
    return DayFetch(kospi, kosdaq, calls, OK if (kospi or kosdaq) else EMPTY)
