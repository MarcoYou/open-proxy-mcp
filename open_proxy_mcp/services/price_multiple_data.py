"""밸류에이션 — DART(공시)+KRX(공식시세) 가격 배수.

설계 스펙·검증 근거: private wiki.
지표: PER(FY0+TTM) · PBR(MRQ, 미공시시 FY0) · 배당수익률(alotMatter 보통주 DPS).
가드: N/M(분모≤0) · 자본잠식→N/M+상폐/관리종목 경고.
시계열 기준: FY0=최근 사업연도, TTM=FY+1Q차분(flow), MRQ=최근 분기말 잔액(stock).
측정: 가격·시총=KRX(공식) / 순이익·EPS·자본=지배귀속 account_id / BPS=지배자본÷유통주식수.
산출 범위는 이 셋뿐이다 — 260823 에 RIM·EV/EBITDA·PSR·FCF·5년밴드·PIT 시계열의
「v1.1 예정」 표기를 걷어냈다. 한 번도 만들지 않은 로드맵이 코드·설명·경고에 남아
**없는 지표를 있는 것처럼 안내**하고 있었다(금융사 경고가 대표적).
260918: 시장·산업 표에 **선행(애널리스트 추정) PER·PBR** 을 얹는다(PSR 은 JSON 만). 우리가 재무로
계산하는 지표가 아니라 추정치 집계라 위 「셋」과 별개다 — 기업 단위 선행은 `forward_estimates_data`.
"""
from __future__ import annotations
from open_proxy_mcp.clock import today_kst

import asyncio
import contextvars
import logging
import os
import re
import time
from typing import Any, NamedTuple

import httpx

import calendar

from open_proxy_mcp.dart.client import get_dart_client, DartClientError, LruByteCache, _env_mb
from open_proxy_mcp.dart.fx import fiscal_year_end_date, fx_to_krw, statement_currency
from open_proxy_mcp.services.sector_class import MAJOR, MID, canon as _class_canon, db_scheme as _db_scheme
from open_proxy_mcp.services.ksic_sector import ksic_bucket
from open_proxy_mcp.services import report_calendar as _rc
from open_proxy_mcp.services.company import (COMPANY_LOOKUP_NEXT_ACTION,
                                             company_ambiguous_warning,
                                             company_not_found_warning,
                                             _company_id, resolve_company_query)
from open_proxy_mcp.services.contracts import AnalysisStatus
from open_proxy_mcp.services.contracts import declare_weak_resolution
from open_proxy_mcp.services.financial_metrics import build_financial_metrics_payload
from open_proxy_mcp.services.dividend import _annual_summary, period_dps, trailing_period_dps
from open_proxy_mcp.services.scale_guard import gid_exact, assess as scale_assess, MARKET_MAX_NI_ANCHOR
from open_proxy_mcp.market_codes import KS as MKT_KS, KQ as MKT_KQ, to_db as mkt_to_db, to_label as mkt_label
from open_proxy_mcp.krx_retention import krx_weekly_cutoff, week_unit, weekly_window_note
from open_proxy_mcp import krx_daily as _krx_daily

logger = logging.getLogger(__name__)

_KRX_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
_KSQ_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"


def _num(v):
    try:
        return int(str(v).replace(",", "")) if v not in (None, "", "-") else None
    except Exception:
        return None


def _div(a, b):
    return (a / b) if (a not in (None,) and b not in (None, 0)) else None


def _price_dates(today=None) -> list[str]:
    """최근 12일 중 주말 제외 후보일(최신 먼저) — KRX가 빈값 줄 토·일 콜 낭비 제거(API QA).
    `today` 를 주면 그 날짜 기준(백그라운드 갱신기가 시각을 직접 넘긴다). 이 서버의 유일한 거래일 달력이다 —
    공휴일은 모르고, KRX 빈 응답으로 자연히 걸러진다."""
    from datetime import date, timedelta
    today = today or today_kst()
    out = []
    for i in range(12):
        d = today - timedelta(days=i)
        if d.isoweekday() <= 5:  # 월~금 (공휴일은 KRX 빈 응답으로 자연 skip)
            out.append(d.strftime("%Y%m%d"))
    return out


# KRX 시세의 **지난 값**(주간 곡선·연말 밴드·주식수 배율의 첫 주) = 기존 검증 자산 **krx_weekly**(주별 최종거래일 전종목,
# 2015-12~, 원장 대조 불일치 0, 수정주가 파이프라인과 공유) — 주간 사본 테이블 신설 금지(260705 krx_weekly_px 중복 생성 → 폐기).
# **최신값**(종가·시총·상장주식수)은 261018 부터 일별 시세 저장분에서 읽는다(아래 「재설계 5-1」 항목).
# 라이브 KRX는 개인키 1개·일 10,000콜 한도(배치와 공유)라 유저마다 못 씀. FX 캐시와 동형:
#  · (~261018) 매일 최신 거래일 스냅샷을 '그 ISO주' 슬롯에 수렴(같은 주 옛 price_dd 삭제 후 insert) — 하루 약 2.2천 죽은 행.
#  · 261018(재설계 5-1 쓰는 쪽): **기록의 주(主)는 `krx_daily`**(261019~ 최근 약 1년, 시·고·저·종·거래량·거래대금·주식수)다.
#    krx_weekly 는 **닫힌 주만** 한 번 옮겨 적는다(`krx_daily.sync_weekly` — 금요일 저장 때, 또는 다음 주 첫 기록 때).
#    한 주 = ISO 주를 달력 연도 안으로 자른 것(`krx_retention.week_unit`) — 연말 거래일이 다음 해 첫 주에 지워지지 않게.
#  · 261018: **갱신은 요청 경로가 아니라 서버 백그라운드 작업이 한다**(`services/krx_refresher.py`, 월~토 07:30~20:00 10분 간격,
#    `pg_try_advisory_lock` 으로 두 머신 중 하나만). 요청 경로(`_market_for`)는 DB 의 최신 거래일만 읽는다 —
#    하루 첫 질문이 KRX 갱신을 떠안아 2~12초 기다리던 것과, 게시 전에 하루 1회 래치가 굳어 옛 날짜를 종일 쓰던 것을 없앤다.
#    `_ensure_krx_fresh`(갱신 능력 있음)는 **배치 스크립트 전용**으로 남는다 — 서버가 못 했을 때의 예비.
#  · 261018(재설계 5-1, 읽는 쪽): **최신값은 일별 시세 저장분에서 읽는다.** 서빙 기준일 = `krx_daily` 의 max(trd_dd)
#    (`_krx_daily_latest_dd`), 종목 한 줄 = `price_latest`(종목마다 가장 늦은 거래일 한 줄, 기준일 이후 행만 — `_krx_db_get`),
#    최신일 여러 종목 = `krx_daily_v WHERE trd_dd=기준일`.
#    기준일과 행은 늘 같은 원천에서 읽고, 일별 저장분을 못 읽으면 krx_weekly 로 돌아가지 않고 기존 강등 경로(KRX 라이브)를 탄다.
#    krx_weekly 는 주간 곡선·연말 밴드·주식수 배율의 첫 주 같은 **지난 값**을 읽을 때만 쓴다(`_weekly_bounds` 의 하한도 그대로 krx_weekly 기준).
#    쓰는 쪽이 닫힌 주만 쓰게 된 뒤(위)에는 krx_weekly 에 이번 주 행이 없다 — 이번 주 최신값은 일별 저장분에만 있다.
# price_date로 기준일 투명 노출(며칠 전 종가여도 날짜 명시 → 사용자 판단).
# 260821 OOM 수습: 무상한 dict → 바이트 상한 LRU. 날짜별 전종목(~2,766)+:split 중복 저장이
# 무제한 누적하던 것을 32MB(≈수일치)로 캡. 초과 시 오래된 날짜부터 evict.
_KRX_CACHE = LruByteCache(_env_mb("OPM_KRX_CACHE_MB", 32), 48 * 60 * 60, "krx")
_KRX_STATE: dict[str, str] = {}              # {"day": 오늘, "latest_dd": 서빙할 최신 price_dd}


def _iso_wk_range(price_dd: str) -> tuple[str, str]:
    """YYYYMMDD 가 속한 「한 주」의 (첫날, 끝날) YYYYMMDD — 같은 주 옛 스냅샷 수렴 삭제·일별 끝점 잇기용.
    261018: ISO 주(월~일)를 **그 달력 연도 안으로 자른다**(`krx_retention.week_unit`) — 주간 닫기와 같은 정의.
    종전엔 연도를 넘는 ISO 주에서 다음 해 첫 거래일을 쓰는 순간 그해 마지막 거래일이 지워졌다(2019·2024·2025 연말 행)."""
    return week_unit(price_dd)


def _krx_db_latest_dd() -> str | None:
    """krx_weekly 의 최신 주(max(price_dd)) — 261018(재설계 5-1 쓰는 쪽)부터 **마지막으로 닫힌 주**. 주간 곡선 하한(`_weekly_bounds`) 전용 —
    표를 자르는 쪽과 같은 정의여야 해서 krx_weekly 를 본다. **최신값 서빙 기준일·배치 갱신 판정은 `_krx_daily_latest_dd`** 다."""
    rows = _pg_rows("SELECT MAX(price_dd) FROM krx_weekly")
    return rows[0][0] if rows and rows[0][0] else None


def _krx_daily_latest_dd() -> str | None:
    """서빙 기준일 — 일별 시세 저장분(`krx_daily`)의 가장 늦은 거래일(261018, 재설계 5-1).

    주간 표의 이번 주 행이 없어져도(다음 단계) 최신값이 낡지 않도록 일별 저장분을 본다. 기본키 (trd_dd, ticker) 역방향 스캔 한 번이다.
    비었거나 못 읽으면 None — krx_weekly 로 대신하지 않는다(기준일과 행은 같은 원천에서 읽는다). 호출자는 기존 강등 경로(KRX 라이브)를 탄다.
    정의는 `krx_daily.latest_day` 한 곳이다 — 유니버스·시총 단면의 기준일(`screener._krx_latest_dd`)과 같은 것. 읽기는 이 모듈의
    `_pg_rows` 로 한다(시험이 이 모듈의 DB 읽기를 바꿔 끼운다)."""
    return _krx_daily.latest_day(_pg_rows)


#: 주간 구간 판정 — 이웃한 주간 날짜 사이가 이 날 수 이하면 같은 구간이다(설·추석으로 한 주가 통째로 비어도 14일 · 연말 행끼리는 약 1년).
_WEEKLY_GAP_DAYS = 21
#: `_krx_weekly_start` 결과 캐시 {보존 하한: (계산한 시각, 구간 시작)}. 표를 줄이는 롤링 정리는 주 1회 정비 잡이라 1시간이면 따라간다.
_WEEKLY_START_CACHE: dict[str, tuple[float, str]] = {}
_WEEKLY_START_TTL = 3600.0
#: 하한 아래 날짜별 행 수 · 하한 이후 첫 날짜의 행 수 · 최신 주의 행 수(촘촘한 주의 기준). 하한 아래는 연말 행과 아직 안 지운 주뿐이라 작다
#: (10-18 운영 1.9만 행 — 롤링을 켜지 않으면 주마다 약 2.7천 행씩 는다). 세 갈래 모두 기본키 (price_dd, ticker) 색인으로 읽는다.
_WEEKLY_START_SQL = (
    "SELECT 'below', price_dd, count(*) FROM krx_weekly WHERE price_dd < %s GROUP BY price_dd "
    "UNION ALL SELECT 'first', price_dd, count(*) FROM krx_weekly "
    "WHERE price_dd = (SELECT MIN(price_dd) FROM krx_weekly WHERE price_dd >= %s) GROUP BY price_dd "
    "UNION ALL SELECT 'ref', price_dd, count(*) FROM krx_weekly "
    "WHERE price_dd = (SELECT MAX(price_dd) FROM krx_weekly) GROUP BY price_dd")


def _weekly_run_start(rows: list[tuple], cut: str) -> str:
    """`_WEEKLY_START_SQL` 결과 → 주간 곡선 하한. 하한(`cut`) 아래에 **촘촘한 주**(최신 주 행 수의 절반 이상)가 하한 이후 첫 날짜에서 거꾸로
    `_WEEKLY_GAP_DAYS` 일 안팎으로 이어져 있으면 그 구간의 첫 주 첫날(주 = `week_unit`)까지 내린다. 없으면 `cut` 그대로.

    하한 아래의 연말 행(종목별 해마다 마지막 행)은 같은 해 12월 말 한 날짜에 몰려 행 수는 크지만 이웃 촘촘한 주와 약 1년 떨어져 있어 이어지지 않는다.
    상장폐지 종목의 마지막 행(연말 행)은 날짜마다 몇 개뿐이라 촘촘한 주로 치지 않는다. 하한이 1월 초면 직전 해 연말 주(전 종목의 그해 마지막 행)가
    촘촘한 주로 이어지는데, 그 주는 실제로 모든 종목의 그 주 마지막 거래일이라 곡선의 한 점으로 맞다."""
    first = [(d, n) for k, d, n in rows if k == "first"]
    ref = max([n for k, _, n in rows if k == "ref"] or [0])
    if not first or not ref:
        return cut
    from datetime import date as _date

    def _d(s: str) -> _date:
        return _date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    anchor, start = first[0][0], None
    for d in sorted((d for k, d, n in rows if k == "below" and d < cut and 2 * n >= ref), reverse=True):
        if (_d(anchor) - _d(d)).days > _WEEKLY_GAP_DAYS:
            break
        anchor = start = d
    return week_unit(start)[0] if start else cut


def _krx_weekly_start(cut: str) -> str | None:
    """krx_weekly 에 **실제로 남은 주간 구간의 시작**(곡선 하한). None = 장애 — 호출자가 `cut` 으로 대신한다."""
    rows = _pg_rows(_WEEKLY_START_SQL, (cut, cut))
    return None if rows is None else _weekly_run_start(rows, cut)


async def _weekly_bounds() -> tuple[str, str]:
    """(저장된 최신 주, 주간 곡선 하한). 오늘 날짜가 아니라 **저장된 최신 주**를 쓴다 — 표를 자르는 쪽이 그걸 쓴다.
    표가 비었거나 못 읽으면(None) 오늘로 대신한다 — 그 뒤 질의가 어차피 no_data/db_error 로 갈린다.

    261019(재설계 6단계 읽는 쪽 복원, 감사 2): 하한은 **DB 에 실제로 남은 주간 구간의 시작**이다(`_krx_weekly_start`).
    종전엔 최신 주의 달력 3년 전(`krx_weekly_cutoff` — 자르는 쪽의 정의)이었다. 표를 한 번 자른 뒤(10-18, 하한 20231016) 롤링 정리는 아직 켜지 않아
    DB 에는 그 주간 행이 그대로 있는데, 최신 주가 한 주 닫힐 때마다 하한이 한 주씩 올라가 그 행을 응답에서 숨겼다(10-24 부터 20231020 주 —
    약 2.7천 행, 1년이면 약 14만 행). 그래서 하한 아래에도 촘촘한 주가 이어져 있으면 거기까지 내린다. 하한 아래가 연말 행뿐이면(지금) 종전과 같다.
    연말 행을 곡선에 섞지 않는 원칙은 그대로다(`_weekly_run_start`). 같은 하한을 시세 도구의 종목 시계열(`trading_data firm`)도 쓴다."""
    latest = await _db_call(_krx_db_latest_dd) or today_kst().strftime("%Y%m%d")
    cut = krx_weekly_cutoff(latest)
    hit = _WEEKLY_START_CACHE.get(cut)
    if hit and time.monotonic() - hit[0] < _WEEKLY_START_TTL:
        return latest, hit[1]
    start = await _db_call(_krx_weekly_start, cut)
    if start is None:                    # 장애 — 자르는 쪽의 하한으로(종전과 같다). 캐시하지 않는다
        return latest, cut
    _WEEKLY_START_CACHE[cut] = (time.monotonic(), start)
    return latest, start


def _krx_db_get(price_dd: str, ticker: str) -> dict:
    """그 종목의 최신 종가·시총·상장주식수 — `price_latest`(종목마다 가장 늦은 거래일 한 줄)에서, 서빙 기준일(`price_dd`) 이후 행만.

    261018: krx_weekly 의 이번 주 행 → 일별 저장분. ticker 조건은 뷰 안의 (ticker, trd_dd) 인덱스로 내려가 한 종목만 읽는다.
    기준일 조건(`price_dd >= 기준일`)이 가르는 두 경우:
      · 그 종목의 최신 행이 기준일보다 **이르면**(보관 창 안에서 상장폐지·거래정지) 옛 날짜를 최신처럼 내지 않고 미스로 돌린다
        → 호출자가 지금처럼 라이브로 확인한다(종전 `WHERE price_dd=기준일` 과 같은 뜻).
      · 그 종목의 최신 행이 기준일보다 **늦으면**(다른 머신의 갱신기가 새 거래일을 쓴 직후, 이 머신의 60초 기준일 캐시가 아직
        하루 전일 때) 그 행을 그대로 낸다 — 같은 날짜만 찾으면 모든 종목이 미스가 나 요청마다 KRX 라이브를 부르고 몇 초씩 기다린다.
        이때 `date` 는 기준일이 아니라 **행 자신의 거래일**이다(값과 날짜가 늘 같은 행에서 나온다).
    캐시가 맞을 때는 행의 거래일 = 기준일이라 종전과 값·날짜가 같다.
    시총은 뷰가 종가 × 상장주식수로 계산한 값이다(실측 20261016 krx_weekly 저장값과 2,762종목 차이 0)."""
    rows = _pg_rows("SELECT close, mktcap, list_shrs, price_dd FROM price_latest "
                    "WHERE ticker=%s AND price_dd>=%s", (ticker, price_dd))
    if rows and rows[0][0]:
        r = rows[0]
        return {"price": r[0], "date": r[3], "common_mktcap": r[1], "list_shrs": r[2]}
    return {}


class KrxWrite(NamedTuple):
    """`_krx_db_upsert` 결과. **성공 판정은 `daily_ok` 하나다**(261018 재설계 5-1 — 일별 저장분이 기록의 주)."""
    daily_ok: bool                   # 그 거래일 전종목이 krx_daily 에 커밋됐나(서빙 기준일이 그 날로 나아갔나)
    weekly_ok: bool = False          # 주간 닫기가 오류 없이 끝났나(닫을 주가 없어도 참). daily_ok 가 거짓이면 늘 거짓
    closed: tuple[str, ...] = ()     # 이번에 krx_weekly 에 닫아 넣은 거래일들


def _krx_db_upsert(price_dd: str, kospi: list, kosdaq: list) -> KrxWrite:
    """한 거래일 전종목을 기록한다 — `krx_daily`(주) + 닫힌 주만 `krx_weekly`(부), 한 트랜잭션.

    261018(재설계 5-1 쓰는 쪽): 종전에는 매 거래일 krx_weekly 의 이번 주 행을 지우고 다시 넣었다(같은 ISO 주 수렴 — 하루 약 2.2천
    죽은 행). 이제 krx_weekly 는 **닫힌 주만 한 번** 옮겨 적는다(`krx_daily.sync_weekly`: 그 주 마지막 평일이 저장됐거나 더 늦은 주가
    저장됐을 때, 그 주의 가장 늦은 거래일로. 같은 주는 더 늦은 날로만 바꾼다). 주간 닫기는 SAVEPOINT 안이라 실패해도 일별 기록은
    커밋되고, 그 주가 일별 보관 창에 남아 있는 동안 다음 기록이 다시 닫는다.
    일별 기록이 실패하면 아무것도 커밋하지 않는다 — 서빙 기준일(`krx_daily` 최신일)과 주간 표가 어긋나지 않게.
    market 은 endpoint 기준 태깅(DB 는 KS/KQ — `krx_daily.daily_records`)."""
    url = os.getenv("DATABASE_URL")
    if not url or not (kospi or kosdaq) or not _krx_daily.daily_records(price_dd, kospi, kosdaq):
        return KrxWrite(False)
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=20) as c:
            # 바깥 트랜잭션 하나에 일별 기록·주간 닫기를 담는다 — 안쪽은 각자 SAVEPOINT. 바깥 블록 없이 부르면 psycopg 는 첫 블록을
            # 그 자체로 커밋하는 트랜잭션으로 다뤄 두 쓰기가 따로 커밋된다.
            with c.transaction():
                ok, n, pruned = _krx_daily.write_daily(c, price_dd, kospi, kosdaq)
                wk = _krx_daily.sync_weekly(c) if ok else None
        if not ok or wk is None:
            return KrxWrite(False)
        logger.info("KRX 스냅샷 기록 %s: krx_daily %d행(오래된 %d행 삭제) · krx_weekly 닫음 %s%s", price_dd, n, pruned,
                    ",".join(wk.closed) or "-", "" if wk.ok else " (주간 닫기 실패 — 다음 기록 때 다시)")
        return KrxWrite(True, wk.ok, wk.closed)
    except Exception as exc:  # noqa: BLE001 — 호출 경로를 깨지 않는다. 다만 조용히 삼키지는 않는다
        logger.warning("KRX 스냅샷 기록 실패 %s: %s: %s", price_dd, type(exc).__name__, exc)
        return KrxWrite(False)


def _krx_weekly_sync() -> bool:
    """주간 닫기만 따로 한 번 — 배치(`_ensure_krx_fresh`)가 일별 저장분이 이미 최신일 때 부른다(서버 갱신기가 닫기를 놓친 경우의 예비).
    멱등이라 닫을 주가 없으면 아무것도 안 쓴다. DB 미설정·실패면 False(로그만)."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return False
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=20) as c:
            wk = _krx_daily.sync_weekly(c)
            c.commit()
        return wk.ok
    except Exception as exc:  # noqa: BLE001
        logger.warning("krx_weekly 주간 닫기(예비) 실패: %s: %s", type(exc).__name__, exc)
        return False


async def _krx_market_live(basDd: str) -> dict[str, dict]:
    """KRX 전종목(코스피+코스닥) 라이브 fetch → {단축코드: row}. 2시장 병렬 + basDd 인메모리 캐시.
    ⚠ 개인키 한도 때문에 서빙은 DB(krx_weekly·krx_daily) 우선 — 이 경로는 DB미스 fallback·배치용이다."""
    _cached = _KRX_CACHE.get(basDd)
    if _cached is not None:
        return _cached
    key = os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY")
    if not key:
        return {}
    from open_proxy_mcp.dart.krx_meter import bump

    async def _one(h, url):
        try:
            bump()  # KRX 일별 사용량 장부
            r = await h.get(url, headers={"AUTH_KEY": key}, params={"basDd": basDd})
            return next((v for v in r.json().values() if isinstance(v, list)), [])
        except Exception:
            return []

    async with httpx.AsyncClient(timeout=30) as h:  # 코스피·코스닥 독립 → 병렬
        kospi, kosdaq = await asyncio.gather(_one(h, _KRX_URL), _one(h, _KSQ_URL))
    out: dict[str, dict] = {}
    for rows in (kospi, kosdaq):
        for row in rows:
            out[row.get("ISU_CD")] = row  # bydd_trd ISU_CD = 단축코드
    if out:
        _KRX_CACHE.put(basDd, out)
        _KRX_CACHE.put(basDd + ":split", {"KOSPI": kospi, "KOSDAQ": kosdaq})  # 시장별(krx_weekly 태깅용)
    return out


async def _fetch_live_snapshot() -> tuple[str | None, dict[str, dict]]:
    """최근 거래일 전종목 스냅샷(첫 데이터 있는 날). 주말·휴장이면 직전 거래일."""
    for d in _price_dates():
        snap = await _krx_market_live(d)
        if any(_num(r.get("TDD_CLSPRC")) for r in snap.values()):
            return d, snap
    return None, {}


#: 요청 경로의 기준일(`krx_daily` 의 max(trd_dd)) 짧은 캐시 — 질문마다 DB 를 한 번 더 왕복하지 않으려는 것이다.
#: 60초면 백그라운드 갱신기가 쓴 새 거래일을 다른 머신도 1분 안에 본다. 빈 값은 담지 않는다.
#: 그 1분 동안 캐시가 하루 전이어도 종목 한 줄(`_krx_db_get`)은 기준일 이후 행을 받으므로 새 거래일 행을 그대로 낸다(KRX 라이브로 새지 않는다).
_SERVING_DD_TTL_SEC = 60.0
_SERVING_DD: dict[str, Any] = {"dd": None, "at": 0.0}


async def _serving_price_dd() -> str | None:
    """**요청 경로**가 서빙할 최신 거래일 — DB 상태(`krx_daily` 의 max(trd_dd), `_krx_daily_latest_dd`)만 읽는다.
    KRX 를 부르지도, 갱신을 떠안지도 않는다.

    갱신은 서버 백그라운드 작업(`services/krx_refresher.py`)이 한다. 종전 `_ensure_krx_fresh` 는 하루 첫 질문이 KRX 를
    스캔·기록하며 2~12초를 기다렸고, KRX 게시 전에 하루 1회 래치가 굳으면 그날 종일 옛 날짜를 썼다 — 둘 다 여기엔 없다.
    261018: krx_weekly 의 max(price_dd) → 일별 저장분. 행을 읽는 쪽(`_krx_db_get` 의 `price_latest`, 후보 시총의 `krx_daily_v`)과 같은 원천이다.
    DB 를 못 읽거나 표가 비면 None(→ 호출자의 라이브 폴백). krx_weekly 로 대신하지 않는다."""
    now = time.monotonic()
    dd = _SERVING_DD.get("dd")
    if dd and now - _SERVING_DD.get("at", 0.0) < _SERVING_DD_TTL_SEC:
        return dd
    dd = await asyncio.to_thread(_krx_daily_latest_dd)
    if dd:
        _SERVING_DD.update(dd=dd, at=now)
    return dd or None


def _serving_dd_invalidate() -> None:
    """갱신기가 새 거래일을 쓴 직후 이 머신의 캐시를 비운다(다른 머신은 TTL 로 따라온다)."""
    _SERVING_DD.update(dd=None, at=0.0)


async def _ensure_krx_fresh() -> str | None:
    """**배치 스크립트 전용**(`scripts/market_val_weekly.py`·`market_fund_quarterly.py`) — 서버 요청 경로는 부르지 않는다.
    하루 1회(프로세스): 일별 저장분(`krx_daily`)이 직전 영업일까지 있나 보고, 없으면 KRX 최신 거래일을 받아 기록한다
    (`_krx_db_upsert` — krx_daily + 닫힌 주만 krx_weekly). 반환 = 일별 저장분의 최신 거래일(배치가 시총을 읽을 날짜).
    서버 백그라운드 갱신기가 이미 썼으면 DB 가 최신이라 KRX 를 부르지 않는다(예비 역할) — 그때는 주간 닫기만 한 번 더 돈다(멱등).

    261018(재설계 5-1 쓰는 쪽): 최신 판정을 krx_weekly max → **krx_daily max** 로. krx_weekly 는 닫힌 주만 담게 되어 주중에는
    늘 지난 금요일이라, 종전 판정이면 배치가 매일 KRX 를 다시 훑고 이미 저장된 날을 다시 썼다. 반환값도 일별 최신일이다 —
    호출자는 그 날짜의 시총을 `krx_daily_v` 에서 읽는다(krx_weekly 에는 이번 주 행이 없다)."""
    today = today_kst().strftime("%Y%m%d")
    if _KRX_STATE.get("day") == today and _KRX_STATE.get("latest_dd"):
        return _KRX_STATE["latest_dd"] or None
    db_latest = await asyncio.to_thread(_krx_daily_latest_dd)
    # DB가 이미 '직전 완료 영업일'(오늘 이전 최근 평일)까지 있으면 라이브 스캔 생략 — 콜드 프로세스
    # 마다 무조건 스캔하던 낭비 제거(API QA: 일요일 8콜 실측). KRX는 T+1 게시라 오늘 데이터는 없다.
    prev_bd = next((d for d in _price_dates() if d < today), None)
    if db_latest and prev_bd and db_latest >= prev_bd:
        await asyncio.to_thread(_krx_weekly_sync)   # 서버 갱신기가 주간 닫기를 놓쳤으면 여기서 메운다(평소 0행)
        _KRX_STATE.update(day=today, latest_dd=db_latest)
        return db_latest
    if db_latest is None or db_latest < today:   # 직전 영업일 미확보 → 라이브 확인·갱신
        dd, snap = await _fetch_live_snapshot()
        if snap and dd:
            # 전진(dd > db_latest)일 때만 기록 — KRX API가 이미 저장된 거래일 데이터를 일시 소실하면
            # (260703 실측: 금요일 데이터가 이틀째 0행) 스캔이 전일을 잡는데, != 조건이면 저장된
            # 금요일을 지우고 목요일로 롤백해버림(QA WARN-1). 과거로는 절대 되돌리지 않는다.
            if db_latest is None or dd > db_latest:
                split = _KRX_CACHE.get(dd + ":split") or {}
                kospi, kosdaq = split.get("KOSPI") or [], split.get("KOSDAQ") or []
                if kospi and kosdaq:             # 두 시장 모두 있을 때만 — 반쪽 스냅샷으로 덮기 금지(QA WARN-2)
                    res = await asyncio.to_thread(_krx_db_upsert, dd, kospi, kosdaq)
                    if res.daily_ok:
                        db_latest = dd
                    else:                        # 일별 기록 실패 — 저장되지 않은 날을 최신으로 돌려주지 않는다
                        logger.warning("KRX 스냅샷 %s 기록 실패 — 저장된 최신일 %s 를 그대로 쓴다", dd, db_latest)
                else:
                    logger.warning(
                        "KRX 스냅샷 반쪽(KOSPI %d/KOSDAQ %d) — 기록 스킵", len(kospi), len(kosdaq))
            else:
                db_latest = max(db_latest, dd)   # dd ≤ db_latest: 저장분이 이미 최신 — 그대로 서빙
    _KRX_STATE.update(day=today, latest_dd=db_latest or "")
    return db_latest


#: `_market_for` 반환의 출처 표시(`source`) — 값을 어디서 받았나. `trading_data(scope="quote")` 의 `data.source` 와 같은 값을 쓴다.
PRICE_SOURCE_DB = "db"              # 일별 시세 저장분(서버가 매 거래일 보관한 KRX 응답)
PRICE_SOURCE_LIVE = "krx_live"      # 저장분에서 받지 못함(그 종목 행 없음·저장분 읽기 실패·DB 미연결 서버) → 요청 경로에서 KRX 를 직접 불러 받은 값


async def _market_for(stock_code: str) -> dict:
    """보통주 종가·시총·상장주식수 — 일별 시세 저장분(DB) 우선, 라이브 KRX fallback.
    price_date로 기준일 노출. 배수 분자는 보통주 시총(우선주 시총은 cap_pref 로 별도).
    기준일은 DB 상태만 읽는다(`_serving_price_dd`) — 갱신은 백그라운드 갱신기 몫이다.
    261018: krx_weekly 의 이번 주 행 → `krx_daily` 기준일 + `price_latest` 한 줄. 반환 키(price·date·common_mktcap·list_shrs)와 값은 그대로다.
    261018: 반환에 **`source`**(`PRICE_SOURCE_DB` / `PRICE_SOURCE_LIVE`)를 더했다 — `scope="explain"` 의 주가 인풋 줄이 라이브 폴백으로
    받은 값에도 「일별 시세 저장분 서빙」이라고 적던 것을 실제 출처대로 적게 하려는 것이다. 못 받으면 종전처럼 {}."""
    latest_dd = await _serving_price_dd()
    if latest_dd:
        row = await asyncio.to_thread(_krx_db_get, latest_dd, stock_code)
        if row.get("price"):
            return {**row, "source": PRICE_SOURCE_DB}
    # 저장분에서 못 받음 — 최신 스냅샷에 아직 없는 신규상장·정지 해제, 저장분 읽기 실패(기준일 None), DB 를 붙이지 않은 서버 → 라이브 단발 fallback
    for d in _price_dates():
        snap = await _krx_market_live(d)
        base = snap.get(stock_code)
        if base and _num(base.get("TDD_CLSPRC")):
            return {"price": _num(base.get("TDD_CLSPRC")), "date": d,
                    "common_mktcap": _num(base.get("MKTCAP")), "list_shrs": _num(base.get("LIST_SHRS")),
                    "source": PRICE_SOURCE_LIVE}
    return {}


async def _resolve_listed(query: str) -> tuple[dict | None, dict | None]:
    """공용 리졸버(resolve_company_query) 채택 — company 툴과 동일 진입 방식(260705).
    상장사 우선 + 동명 다수 시 ambiguous 후보표(silent 첫 후보 pick 제거).
    반환 (corp, early_payload): corp=식별 결과 / early=즉시 반환할 payload(ambiguous).
    ERROR(비상장만·무매칭)는 (None, None) — 호출부의 기존 세분화(unlisted 시총순 후보·
    우선주 힌트)가 더 구체적이라 그 경로로 폴백한다."""
    res = await resolve_company_query(query)
    if res.status == AnalysisStatus.AMBIGUOUS:
        return None, {
            "tool": "price_multiple_data", "status": "ambiguous", "subject": query,
            "data": {"query": query, "candidates": [
                {"corp_name": c.get("corp_name"), "stock_code": c.get("stock_code"),
                 "corp_code": c.get("corp_code")} for c in res.candidates[:10]]},
            "warnings": [f"'{query}' 동명 후보 여러 건 — 아래에서 골라 종목코드로 재시도."]}
    if res.status == AnalysisStatus.EXACT and res.selected:
        return res.selected, None
    return None, None


# ── 시장·산업·종목 히스토리 스코프 — 주간 스냅샷 테이블(DB-first, market_val_weekly.py가 갱신) ──
# opm_val_market(시장·업종 집계). 종목별 값(업종 비교의 기업 배수·firm_history)은 261018(재설계 6단계)부터 저장분 없이
# 주간 시세 × 시점별 재무로 읽을 때 계산한다(`_pit_raw`·`_weekly_series`). PER/PBR·시총 시계열은
# 시총 기반이라 **수정주가 조정에 불변**(시총=주가×주식수, 분할·무상증자에 양쪽이 상쇄) — 조정 불필요.
# 주당 가격·EPS 시계열을 노출하게 되면 그때 krx_adj_events(기준가 리셋 실측) 적용 필수(wiki 수정주가).


def _pg_rows(sql: str, params: tuple = ()) -> list[tuple] | None:
    """None = DB 미설정/장애(no_data와 구분 — 오진 방지, QA), [] = 정상 조회·데이터 없음.

    260824: 질의마다 새로 접속하던 것을 **커넥션 풀**로 옮겼다(`open_proxy_mcp.db`).
      실측 핸드셰이크 124ms vs 질의 63ms — 연결이 질의보다 2배 비쌌고, `firm_history` 는
      이 함수를 8번 지나 1.68초 중 1초가 접속이었다. 호출부 20곳은 그대로 두고 **여기 한
      곳만** 갈아끼운다 — 시그니처·반환 계약(None vs [])이 같아야 호출부가 안 흔들린다.
      풀이 없거나 실패하면 예전처럼 직접 접속한다(fail-open).
    """
    from open_proxy_mcp.db import pg_rows
    return pg_rows(sql, params)


# ── 한 호출 안의 DB 읽기를 겹쳐 돌린다 (261018, Supabase 재설계 4단계 T5) ───────────────────────────────────────────
# `scope=firm_history` 는 서로 입력이 필요 없는 읽기 9번을, `scope=firm` 은 5번을 **차례로** 기다렸다. 읽기 하나가 Fly 도쿄 ↔ Supabase 서울
# 왕복(≈30ms 이상)이라 대기의 대부분이 왕복이다. 입력이 겹치지 않는 것끼리 묶어 한꺼번에 보낸다(firm_history 9→3 왕복, firm 5→2 왕복).
#
# 🔴 **한 호출이 풀을 다 쓰지 못하게 호출 단위로 상한을 둔다.** 풀은 머신당 5커넥션(`db.py`), `asyncio.to_thread` 기본 스레드 풀도 1 CPU 에서 5칸이다.
#    상한이 없으면 호출 하나가 둘을 다 차지해 같은 머신의 다른 도구가 줄을 서고, 풀이 2초 넘게 막히면 `pg_rows` 가 풀을 60초 내린다
#    (모든 질의가 핸드셰이크 124ms 짜리 직접 접속으로 떨어진다). 그래서 기본 3 — 나머지 2칸은 다른 호출 몫이다.
#    `OPM_PG_FANOUT=1` 이면 종전처럼 읽기를 하나씩 보낸다(롤백 손잡이 — 배포 없이 환경변수만 바꾼다).
def _env_fanout() -> int:
    try:
        return max(1, int(os.environ.get("OPM_PG_FANOUT", "") or 3))
    except ValueError:                 # 오타 하나로 서버가 못 뜨면 안 된다 — 기본값으로
        return 3


_PG_FANOUT = _env_fanout()
#: 지금 도는 `_gather_db` 의 호출 단위 상한. 컨텍스트 변수라 안쪽 `asyncio.gather`(→ 새 태스크)도 같은 상한을 이어받는다.
_PG_SLOTS: contextvars.ContextVar[asyncio.Semaphore | None] = contextvars.ContextVar("opm_pg_slots", default=None)


async def _db_call(fn, *args):
    """`asyncio.to_thread(fn, *args)` — `_gather_db` 안이면 호출 단위 상한의 칸을 얻은 뒤에만 스레드로 보낸다. 밖이면 종전 `to_thread` 와 같다."""
    sem = _PG_SLOTS.get()
    if sem is None:
        return await asyncio.to_thread(fn, *args)
    async with sem:
        return await asyncio.to_thread(fn, *args)


async def _db_rows(sql: str, params: tuple = ()) -> list[tuple] | None:
    """`_pg_rows` 한 번을 이벤트 루프 밖 스레드에서. 반환 계약도 `_pg_rows` 그대로다(None = 장애, [] = 데이터 없음)."""
    return await _db_call(_pg_rows, sql, params)


async def _gather_db(*aws) -> list:
    """서로 입력이 필요 없는 DB 읽기(코루틴)들을 동시에 기다리고 결과를 **넣은 순서대로** 돌려준다.

    · 동시에 도는 읽기는 호출당 `_PG_FANOUT` 개까지다. 안쪽에서 `_gather_db`/`asyncio.gather` 를 또 써도 같은 상한을 쓴다(이미 상한 안이면 새로 만들지 않는다).
    · 하나가 예외를 내도 나머지를 끝까지 기다린 뒤 **넣은 순서상 첫** 예외를 올린다. 종전 차례 실행에서 처음 터진 예외가 올라가던 것과 같은 모양이고,
      끝나지 않은 읽기가 이 호출 밖에 남지 않는다.
    · 장애(None)는 예외가 아니다 — 각 읽기가 `_pg_rows` 계약대로 None 을 돌려주고, 어떻게 다룰지는 호출부가 종전과 똑같이 정한다."""
    token = None
    if _PG_SLOTS.get() is None:
        token = _PG_SLOTS.set(asyncio.Semaphore(_PG_FANOUT))
    try:
        res = await asyncio.gather(*aws, return_exceptions=True)     # 태스크는 여기서 만들어져 위 상한을 컨텍스트로 물려받는다
    finally:
        if token is not None:
            _PG_SLOTS.reset(token)
    for r in res:
        if isinstance(r, BaseException):
            raise r
    return list(res)


_DB_ERROR_PAYLOAD_WARN = "스냅샷 DB 연결 실패 — 일시 장애 가능, 잠시 후 재시도. (배치 미실행과 다름)"
#: 시장·산업 주간 밸류 스냅샷이 비었을 때(배치 미실행). 261018: 사용자 문장에 표·배치 이름(종전 「opm_val_market 비어있음 —
#: market_val_weekly 배치 미실행」)을 쓰지 않는다 — 260907 저장소 이름 정리의 남은 자리.
_VAL_SNAPSHOT_EMPTY_WARN = "주간 밸류 스냅샷이 비어 있음 — 집계 배치 미실행."


# ══════════════════════════════════════════════════════════════════════════
# 배당수익률 — 두 벌(확정 · 선행). 260831 신설.
#
# 🔴 **PER·PBR 과 출처 표가 다르다.** PER·PBR 은 `opm_val_market`(주간 스냅샷),
#    배당수익률은 `div_yield_hist`(사업연도 확정, 연 1회 갱신)와 `opm_val_fwd`(추정 스냅샷, 260918~)다.
#    그래서 기준일이 셋 다 다르다 — 한 표에 놓을 때 각각의 자를 같이 실어야 한다.
# 🔴 **분모를 한 벌만 내면 코스닥이 왜곡된다.** `all`(무배당 포함)만 내면 코스닥은
#    절반으로 눌린다 — 눌림의 정체는 배당력이 아니라 「배당하는 회사가 적다」는 구성 차이다
#    (실측: KQ all→payers 최대 −59.7%, KS 는 −15.5~−20.8%). 두 벌을 같이 낸다.
# 🔴 **PER 과 게이팅이 다르다.** PER 은 적자면 안 나오지만 배당수익률은 적자여도
#    배당이 있으면 값이 난다. 왜 PER 은 비는데 이건 있는지 묻게 되는 자리라 각주로 밝힌다.
# ══════════════════════════════════════════════════════════════════════════
_DIV_METHOD = (
    "배당수익률(%) = Σ(주당배당금 × 주식수) ÷ Σ보통주 시총 × 100 — 시총가중. **배(倍)가 아니라 %다.** "
    "확정=`div_yield_hist`(사업연도 12월결산 확정 DPS, 시총은 그 해 12월 마지막 주. 연 1회 갱신) · "
    "선행=애널리스트 추정 DPS(선행 PER·PBR 과 같은 표 `opm_val_fwd`, 분모는 추정이 있는 종목의 시총 `covered`). "
    "**둘은 기준일도 모집단도 다르다 — 나란히 놓되 차이를 배당의 증감으로 읽지 말 것.** "
    "분모 두 벌: `all`=무배당·DPS미확정까지 다 센 시장 관행값(본값) / `payers`=배당하는 회사만. "
    "코스닥은 둘을 반드시 같이 본다 — `all` 만 보면 배당력이 아니라 배당하는 회사 수가 적은 것을 본다. "
    "PER·PBR 과 달리 **적자여도 배당이 있으면 값이 난다**(같은 게이팅을 쓰지 않는다)."
)


# ══════════════════════════════════════════════════════════════════════════
# 선행 배수 — 애널리스트 추정 기반 PER·PBR·PSR·배당수익률. 260918 신설.
#
# 표는 `opm_val_fwd`(scripts/fwd_val_weekly.py, market-val-weekly 가 매일 새 날짜만 계산).
# 🔴 **트레일링과 같은 방식으로 낸다.** 트레일링 PER 은 적자 회사까지 더한 합이다. 흑자 추정만 더한
#    벤더식(`fwd_per_pos`)을 옆에 놓으면 방식 차이가 기대이익 차이로 읽힌다(260913 실측:
#    코스피 전자와 전기제품 선행 PER 88.9 적자 포함 vs 24.9 흑자만). 벤더식은 JSON 에만 싣는다.
# 🔴 **모집단이 다르다.** 트레일링은 상장 보통주 전부, 선행은 추정이 있는 종목만(시장 약 650사).
#    그래서 행마다 추정 종목 수를 같이 싣는다.
# 🔴 기준일이 다르다 — 추정 스냅샷(주 1회 토요일)과 주간 시세 스냅샷을 각각 적는다.
# ══════════════════════════════════════════════════════════════════════════
_FWD_SCHEMES = ("market", _db_scheme(MAJOR), _db_scheme(MID))
_FWD_COLS = ("as_of", "market", "bucket", "label", "n_total", "cap_krw", "n_per", "ni_krw", "fwd_per",
             "n_per_pos", "fwd_per_pos", "n_pbr", "eq_krw", "fwd_pbr", "n_psr", "fwd_psr",
             "n_dps", "fwd_div_yield_pct", "fy_main", "fy_min", "fy_max", "class_dd")


def _fwd_method(fy: int | None) -> str:
    yr = f"대부분 {fy}년" if fy else "가장 가까운 추정 연도"
    return ("선행 = 애널리스트 추정(종목마다 가장 가까운 추정 사업연도 — " + yr + "). "
            "PER=Σ시총÷Σ추정 지배순이익 · PBR=Σ시총÷Σ추정 자기자본(시총×BPS÷주가) — "
            "**트레일링과 같은 방식(적자 추정도 더한다)**. 합이 0 이하면 「적자」로 적는다. "
            "**추정이 있는 보통주만 더한다** — 트레일링(상장 보통주 전부)과 모집단이 달라 "
            "추정 종목 수를 같이 적는다. 흑자 추정만 더한 벤더식은 `fwd_per_pos`, 선행 PSR 은 `fwd_psr`(JSON). "
            "업종은 추정 날짜 이하 가장 최근 업종분류(`class_dd`).")


def _as_date(dd: str | None) -> str | None:
    """YYYYMMDD → YYYY-MM-DD. 달력에 없는 날짜면 ValueError — DB 에 넘기기 전에 여기서 막는다
    (DB 가 날짜 변환에 실패하면 커넥션 풀이 장애로 보고 60초간 꺼진다, 260918 QA)."""
    if not dd:
        return None
    import datetime as _dt
    return _dt.datetime.strptime(dd, "%Y%m%d").date().isoformat()


async def _fwd_val_map(scheme: str, as_of: str | None = None,
                       history: bool = False) -> tuple[dict, dict, list[dict]]:
    """(선행 {(market, bucket): 행}, 자, 이력). as_of(YYYYMMDD) 가 있으면 그 이하 가장 최근 스냅샷.

    fail-open — 조회가 실패해도 트레일링 표는 그대로 낸다. 자에 이유를 남긴다.
    history=True 면 같은 분류의 전 스냅샷을 최신순으로 함께 준다(시장 표의 추이용).
    """
    ruler: dict[str, Any] = {}
    if scheme not in _FWD_SCHEMES:
        return {}, ruler, []
    try:
        day = _as_date(as_of)
    except ValueError:
        ruler["error"] = f"기준일 {as_of} 은 달력에 없는 날짜라 선행 조회를 하지 않았다."
        return {}, ruler, []
    cond = " AND as_of <= %s" if day else ""
    cols = ", ".join(_FWD_COLS)
    if history:
        sql = f"SELECT {cols} FROM opm_val_fwd WHERE scheme=%s{cond} ORDER BY as_of DESC, market"
        params: tuple = (scheme, day) if day else (scheme,)
    else:
        sql = (f"SELECT {cols} FROM opm_val_fwd WHERE scheme=%s "
               f"AND as_of=(SELECT MAX(as_of) FROM opm_val_fwd WHERE scheme=%s{cond})")
        params = (scheme, scheme, day) if day else (scheme, scheme)
    rows = await asyncio.to_thread(_pg_rows, sql, params)
    if rows is None:
        ruler["error"] = "선행 배수 조회 실패 — 일시 장애 가능, 트레일링 값은 그대로다."
        return {}, ruler, []
    recs = [dict(zip(_FWD_COLS, r)) for r in rows]
    if not recs:
        if day:
            first = await asyncio.to_thread(_pg_rows, "SELECT MIN(as_of) FROM opm_val_fwd WHERE scheme=%s",
                                            (scheme,))
            since = first[0][0] if first and first[0][0] else None
            ruler["note_empty"] = (f"기준일 {as_of} 이하 선행 집계 없음" +
                                   (f" — 선행 집계는 {since} 부터 쌓였다." if since else "."))
        return {}, ruler, []
    latest = max(r["as_of"] for r in recs)
    cur = {(r["market"], r["bucket"]): r for r in recs if r["as_of"] == latest}
    fys = [r["fy_main"] for r in cur.values() if r.get("fy_main")]
    ruler.update(as_of=str(latest), class_dd=next(iter(cur.values()))["class_dd"],
                 fy_main=max(set(fys), key=fys.count) if fys else None)
    hist = [{"as_of": str(r["as_of"]), "market": r["market"], "fwd_per": _r2(r["fwd_per"]),
             "fwd_pbr": _r2(r["fwd_pbr"]), "fwd_psr": _r2(r["fwd_psr"]), "fwd_per_pos": _r2(r["fwd_per_pos"]),
             "n_total": r["n_total"], "ni_krw": r["ni_krw"]} for r in recs] if history else []
    return cur, ruler, hist


def _round_mult(v: float, nd: int) -> float:
    """배수 반올림 — 0 이 아닌 값이 반올림으로 0 이 되면 유효숫자 두 자리로 둔다(261019).
    10-19 검증: 정리매매가 11원인 한창 PER(FY0) 0.0038 이 「0.0」 으로 나가 「PER 0」(이익이 무한대)처럼 읽혔다.
    그 밖의 값은 종전 반올림 그대로다."""
    r = round(v, nd)
    if r == 0 and v != 0:
        return float(f"{v:.2g}")
    return r


def _r2(v):
    return None if v is None else _round_mult(v, 2)


def _attach_fwd(row: dict, f: dict | None) -> None:
    """행 하나에 선행 칸을 붙인다. 없으면 붙이지 않는다(빈 칸을 0 으로 메우지 않는다)."""
    if not f:
        return
    row.update(fwd_per=_r2(f["fwd_per"]), fwd_pbr=_r2(f["fwd_pbr"]), fwd_psr=_r2(f["fwd_psr"]),
               fwd_per_pos=_r2(f["fwd_per_pos"]),
               fwd_n_total=f["n_total"], fwd_n_per=f["n_per"], fwd_n_per_pos=f["n_per_pos"],
               fwd_ni_krw=f["ni_krw"], fwd_eq_krw=f["eq_krw"], fwd_cap_krw=f["cap_krw"],
               fwd_fy=f["fy_main"])
    if f.get("fwd_div_yield_pct") is not None:
        row["fwd_div_yield_pct"] = round(f["fwd_div_yield_pct"], 4)
        row["fwd_div_n_dps"] = f["n_dps"]


def _merge_fwd_ruler(div_ruler: dict, fwd_ruler: dict) -> dict:
    """배당 자에 선행 기준일을 얹는다 — 선행 배당수익률이 선행 배수와 같은 표에서 온다.

    조회 실패는 여기 옮기지 않는다 — 선행 각주(`fwd_ruler.error`)가 이미 한 번 적는다.
    """
    out = dict(div_ruler)
    if fwd_ruler.get("as_of"):
        out["forward_as_of"] = fwd_ruler["as_of"]
    return out


async def _div_yield_map(scheme: str) -> tuple[dict, dict]:
    """(확정, 자) — 키는 (market, bucket). 실패해도 None 이 아니라 빈 dict 를 낸다.

    🔴 배당수익률이 없다고 PER·PBR 표까지 죽이지 않는다(fail-open). 표가 통째로 사라지는
       것보다 한 칸이 비는 편이 낫다 — 대신 기준 줄에 「배당수익률 조회 실패」를 남긴다.
    260918: 선행 배당수익률은 선행 배수와 같은 표(`opm_val_fwd`)로 옮겼다 — `_fwd_val_map`.
    """
    ruler: dict[str, Any] = {}
    act: dict[tuple, dict] = {}

    rows = await asyncio.to_thread(_pg_rows,
        "SELECT market, bucket, denom_basis, div_yield_pct, fiscal_year, price_dd, "
        "       n_total, n_div, cov_cap_pct "
        "FROM div_yield_hist WHERE scheme=%s "
        "AND fiscal_year=(SELECT MAX(fiscal_year) FROM div_yield_hist WHERE scheme=%s) "
        "AND denom_basis IN ('all','payers')", (scheme, scheme))
    if rows:
        for mkt, bucket, basis, pct, fy, price_dd, n_total, n_div, cov in rows:
            d = act.setdefault((mkt, bucket), {})
            d[basis] = pct and round(pct, 4)
            d.update(fiscal_year=fy, price_dd=price_dd, n_total=n_total, n_div=n_div,
                     cov_cap_pct=cov and round(cov, 2))
            ruler.setdefault("actual_fiscal_year", fy)
            ruler.setdefault("actual_price_dd", price_dd)
    elif rows is None:
        ruler["actual_error"] = "확정 배당수익률 조회 실패 — 일시 장애 가능, PER·PBR 값은 그대로다."   # 261018: 표 이름 대신 선행 쪽과 같은 안내
    return act, ruler


def _attach_div(row: dict, key: tuple, act: dict) -> None:
    """행 하나에 확정 배당수익률 칸을 붙인다. 없으면 붙이지 않는다(빈 칸을 0 으로 메우지 않는다)."""
    a = act.get(key)
    if a:
        row["div_yield_pct_all"] = a.get("all")
        row["div_yield_pct_payers"] = a.get("payers")
        row["div_fiscal_year"] = a.get("fiscal_year")
        row["div_price_dd"] = a.get("price_dd")
        row["div_n_payers"] = a.get("n_div")
        row["div_cov_cap_pct"] = a.get("cov_cap_pct")


def _norm_as_of(as_of) -> str | None:
    """YYYYMMDD 또는 YYYY-MM-DD → YYYYMMDD. 빈 값은 None. 형식이 틀리면 ValueError."""
    v = (as_of or "").strip().replace("-", "")
    if not v:
        return None
    if not (len(v) == 8 and v.isdigit()):
        raise ValueError(f"as_of 는 YYYYMMDD 또는 YYYY-MM-DD 여야 합니다 (받은 값: {as_of})")
    # 260918: 자릿수만 보면 20260231 이 통과한다 — 트레일링은 문자열 비교라 조용히 넘어가지만
    #   선행 조회는 DB 날짜 변환에서 죽는다. 달력에 있는 날짜인지 여기서 본다.
    import datetime as _dt
    try:
        _dt.datetime.strptime(v, "%Y%m%d")
    except ValueError:
        raise ValueError(f"as_of {as_of} 은 달력에 없는 날짜입니다 — YYYYMMDD 로 실제 날짜를 주세요.") from None
    return v


async def build_market_val_payload(format: str = "md", as_of: str | None = None) -> dict[str, Any]:
    """시장 전체(KOSPI/KOSDAQ) 시총가중 밸류에이션 — 최신(또는 as_of 이하 최근 스냅샷) + 주간 히스토리(opm_val_market)."""
    rows = await asyncio.to_thread(_pg_rows,
        "SELECT snap_dd, market, per_fy0, per_ttm, pbr_fy0, pbr_mrq, cap, ni_ttm, eq, cap_pref, ni_fy0 "
        "FROM opm_val_market WHERE sector='_ALL' AND scheme='market' ORDER BY snap_dd DESC, market")
    if rows is None:
        return {"tool": "price_multiple_data", "status": "db_error", "subject": "시장 밸류에이션",
                "warnings": [_DB_ERROR_PAYLOAD_WARN]}
    if not rows:
        return {"tool": "price_multiple_data", "status": "no_data", "subject": "시장 밸류에이션",
                "warnings": [_VAL_SNAPSHOT_EMPTY_WARN]}
    hist = [{"snap_dd": r[0], "market": r[1],
             "per_fy0": r[2] and round(r[2], 2), "per_ttm": r[3] and round(r[3], 2),
             "pbr_fy0": r[4] and round(r[4], 2), "pbr_mrq": r[5] and round(r[5], 2),
             "cap_krw": r[6], "ni_ttm_krw": r[7], "eq_krw": r[8],
             "cap_pref_krw": r[9] if len(r) > 9 else None,
             "ni_fy0_krw": r[10] if len(r) > 10 else None} for r in rows]
    if as_of:
        # 260907: 과거 시점 — as_of 이하 가장 최근 주간 스냅샷. 히스토리는 그대로 다 준다.
        past = [h for h in hist if h["snap_dd"] <= as_of]
        if not past:
            return {"tool": "price_multiple_data", "status": "no_data", "subject": "시장 밸류에이션",
                    "warnings": [f"기준일 {as_of} 이하 스냅샷 없음 — 가장 이른 스냅샷은 {hist[-1]['snap_dd']}."]}
        latest_dd = past[0]["snap_dd"]
    else:
        latest_dd = hist[0]["snap_dd"]
    latest = [h for h in hist if h["snap_dd"] == latest_dd]
    # 260831: 배당수익률 두 벌을 같은 표에 얹는다. 키는 (market, 'ALL').
    # 260918: 선행 PER·PBR 도 얹는다. 선행 키는 (market, '_ALL') — 트레일링 표와 같은 센티넬.
    (div_act, div_ruler), (fwd, fwd_ruler, fwd_hist) = await asyncio.gather(
        _div_yield_map("market"), _fwd_val_map("market", as_of, history=True))
    for h in latest:
        _attach_div(h, (mkt_to_db(h["market"]), "ALL"), div_act)
        _attach_fwd(h, fwd.get((mkt_to_db(h["market"]), "_ALL")))
    has_fwd_div = any(h.get("fwd_div_yield_pct") is not None for h in latest)
    return {"tool": "price_multiple_data", "status": "ok", "subject": "시장 밸류에이션(KOSPI·KOSDAQ)",
            "data": {"scope": "market", "as_of": latest_dd, "as_of_requested": as_of,
                     "latest": latest,
                     "history": hist,
                     "fwd_ruler": fwd_ruler or None,
                     "fwd_method": _fwd_method(fwd_ruler.get("fy_main")) if fwd else None,
                     "fwd_history": fwd_hist,
                     "div_yield_ruler": _merge_fwd_ruler(div_ruler, fwd_ruler) or None,
                     "div_yield_method": _DIV_METHOD if (div_act or has_fwd_div) else None,
                     "method": "**보통주 기준**(260705 확정): PER=Σ보통주 시총÷Σ지배순이익 · PBR=Σ보통주 "
                               "시총÷Σ지배자본(MRQ) — KRX 지수 PER 관행. 우선주 시총은 배수 제외, cap_pref_krw로 "
                               "별도 노출(분모 이익·자본엔 우선주 몫 포함 → 소폭 하향 편향, 클래스 분리는 공시 부재로 불가) · "
                               "trailing(과거 실적) 기준 — 컨센서스 선행 PER와 다름. "
                               "Σ지배순이익에 적자기업 포함(흑자기업만 쓰는 일부 벤더와 상이 — 적자 우세 "
                               "시장·섹터의 PER이 크게 높아짐, KOSDAQ 고PER의 주원인. PBR 병행 해석 권장). "
                               "※ 표기 PER 분모별 Σ시총은 해당 지표 보유 종목만 — cap_krw(전체 시총)"
                               "÷ni_ttm_krw 재계산과 다를 수 있음"},
            "warnings": [f"주간 스냅샷 기준(최신 {latest_dd}) — 매일 집계 배치가 갱신."]}


# 260823: 분류 축이 둘이 됐다. `sector != '_ALL'` 만으로 거르면 KSIC 와 업종분류가 **섞여**
#   같은 종목이 두 버킷에 잡힌다(섹터 표가 중복된다). 반드시 scheme 을 함께 건다.
_SECTOR_SCHEMES = {
    "ksic": "KSIC 하이브리드(자체 매핑) — 62버킷",
    MAJOR: "업종 대분류 10",
    MID: "업종 중분류 28 — KSIC 세분과 비교 가능한 층",
}


async def build_sector_val_payload(company: str = "", format: str = "md",
                                   scheme: str = MID, as_of: str | None = None) -> dict[str, Any]:
    """산업별 시총가중 밸류에이션 — 최신 스냅샷 + 섹터 히스토리(opm_val_market).
    company 지정 시 그 기업의 섹터를 함께 표시. scheme 으로 분류 축 선택."""
    scheme = _class_canon(scheme, MID)
    if scheme not in _SECTOR_SCHEMES:
        return {"tool": "price_multiple_data", "status": "invalid", "subject": "산업별 밸류에이션",
                "warnings": [f"scheme '{scheme}' 없음 — {' / '.join(_SECTOR_SCHEMES)} 중 선택."]}
    as_of_req = as_of  # 아래에서 as_of 가 실제 스냅샷 날짜로 바뀐다 — 선행 조회는 요청값 기준
    # 260907: as_of 가 있으면 그 이하 가장 최근 스냅샷 (과거 시점 비교)
    sub = "SELECT MAX(snap_dd) FROM opm_val_market WHERE sector != '_ALL' AND scheme=%s" + (" AND snap_dd <= %s" if as_of else "")
    dbs = _db_scheme(scheme)
    # 261019: 시장 전체 스냅샷의 최신일(as_of 가 있으면 그 이하)을 먼저 읽는다 — 이 축의 최신 스냅샷이 그보다 이르면 「낡았다」고 밝힌다(아래 `stale`).
    #   KSIC 축은 2026-08-23 결정으로 집계를 멈춰(20260821) 8주 전 값을 기준일만 적어 내고 있었다(10-19 검증). 읽기 실패는 판정 없음(None)으로 둔다.
    mk_rows = await asyncio.to_thread(_pg_rows,
        "SELECT MAX(snap_dd) FROM opm_val_market WHERE scheme=%s AND sector=%s" + (" AND snap_dd <= %s" if as_of else ""),
        ("market", "_ALL", as_of) if as_of else ("market", "_ALL"))
    params: tuple = (dbs, dbs, as_of) if as_of else (dbs, dbs)
    rows = await asyncio.to_thread(_pg_rows,
        "SELECT snap_dd, market, sector, label, n, cap, per_ttm, pbr_mrq, per_fy0, pbr_fy0, "
        "ni_fy0, ni_ttm FROM opm_val_market "
        "WHERE sector != '_ALL' AND scheme=%s "
        f"AND snap_dd=({sub}) "
        "ORDER BY market, cap DESC", params)
    if rows is None:
        return {"tool": "price_multiple_data", "status": "db_error", "subject": "산업별 밸류에이션",
                "warnings": [_DB_ERROR_PAYLOAD_WARN]}
    if not rows:
        return {"tool": "price_multiple_data", "status": "no_data", "subject": "산업별 밸류에이션",
                "warnings": [f"기준일 {as_of} 이하 산업별 스냅샷 없음." if as_of else _VAL_SNAPSHOT_EMPTY_WARN]}
    as_of = rows[0][0]
    sectors = [{"market": r[1], "sector": r[2], "label": r[3], "n": r[4], "cap_krw": r[5],
                "per_ttm": r[6] and round(r[6], 2), "pbr_mrq": r[7] and round(r[7], 2),
                "per_fy0": r[8] and round(r[8], 2), "pbr_fy0": r[9] and round(r[9], 2),
                # 260829: 배수가 비었을 때 「적자」와 「자료없음」을 가르는 분모. None = 더한 회사 없음.
                "ni_fy0_krw": r[10], "ni_ttm_krw": r[11]}
               for r in rows]
    # 260831: 확정 배당수익률은 업종 대분류로만 집계돼 있다(`div_yield_hist`). 다른 축에서는 안 붙인다 —
    #   KSIC·업종 중분류에 억지로 맞추면 버킷이 어긋난 값이 붙는다.
    #   키를 코드가 아니라 **label** 로 맞춘다(그 표의 bucket 이 섹터 이름이다).
    # 260918: 선행(PER·PBR·PSR·배당)은 업종분류 두 층 다 있다(`opm_val_fwd`). 키는 업종 **코드** —
    #   이 표의 `sector` 칸과 같은 값이다. KSIC 선행 집계는 없다(안 붙인다).
    div_act, div_ruler = ({}, {})
    fwd, fwd_ruler = ({}, {})
    if scheme == MAJOR:
        (div_act, div_ruler), (fwd, fwd_ruler, _) = await asyncio.gather(
            _div_yield_map(_db_scheme(MAJOR)), _fwd_val_map(_db_scheme(MAJOR), as_of_req))
    elif scheme == MID:
        fwd, fwd_ruler, _ = await _fwd_val_map(_db_scheme(MID), as_of_req)
    for srow in sectors:
        _attach_div(srow, (mkt_to_db(srow["market"]), srow["label"]), div_act)
        _attach_fwd(srow, fwd.get((mkt_to_db(srow["market"]), srow["sector"])))
    has_fwd_div = any(s.get("fwd_div_yield_pct") is not None for s in sectors)
    company_ctx = None
    # 260823: scheme 을 열었는데 각주가 「KSIC 하이브리드」로 굳어 있었다 — 업종분류로 조회해도
    #   KSIC 라고 말한다. 사용자가 다른 축을 봤다고 믿게 되는 자리다.
    _SRC = {"ksic": "KSIC 하이브리드(opm_sector_map)",
            MAJOR: "업종 대분류",
            MID: "업종 중분류"}
    mk_latest = mk_rows[0][0] if mk_rows and mk_rows[0] and isinstance(mk_rows[0][0], str) else None
    stale = None
    # 과거 기준일(as_of)은 KSIC 축만 본다 — 업종분류 축은 2026-07 주간 집계 전까지 월말 한 점씩이라(시장 전체는 그 사이 주간도 있다)
    #   과거 기준일에서 그 축이 시장보다 이른 것은 설계대로다.
    if mk_latest and mk_latest > as_of and (not as_of_req or scheme == "ksic"):
        stale = {"axis_as_of": as_of, "market_as_of": mk_latest,
                 "reason": "ksic_aggregation_frozen" if scheme == "ksic" else "axis_not_updated"}
    warnings = [f"주간 스냅샷 기준({'이 축의 마지막 집계 ' if stale else '최신 '}{as_of}) · 분류={_SRC[scheme]}."]
    if stale and scheme == "ksic":
        warnings.append(f"KSIC 축 집계는 {as_of} 에서 멈춰 있다 — 2026-08-23 결정으로 업종 배수·시총 집계의 기준 축을 업종분류로 옮기고 "
                        "KSIC 는 「기업이 어느 업종인가」를 알려주는 용도로만 남겼다. 이 표·소속 섹터 히스토리·기업 비교는 "
                        f"{as_of} 값이다(시장 전체 최신 스냅샷은 {mk_latest}). 최신 업종 배수는 scheme='중분류'(기본)·'대분류'.")
    elif stale:
        warnings.append(f"이 축({_SRC[scheme]})의 최신 스냅샷 {as_of} 이 시장 전체 스냅샷({mk_latest})보다 이르다 — "
                        "그 사이 이 축 집계가 갱신되지 않았다(배치 확인 필요).")
    if scheme != "ksic":
        warnings.append(
            "업종분류는 2026-08 부터 관측을 쌓는다 — 그 이전 시점의 분류는 **현재 분류를 소급 적용**한 "
            "것이라 당시 실제 소속과 다를 수 있다(관측이 쌓이면 소급 구간이 뒤로 밀린다).")
    if company.strip():
        corp, early = await _resolve_listed(company.strip())  # 공용 리졸버 — ambiguous 후보표
        if early:
            return early
        if not corp:
            corp = await get_dart_client().lookup_corp_code(company.strip())
        isu = (corp or {}).get("stock_code")
        if not isu:  # 회사 미해결/비상장 — 전체 표 덤프 대신 짧은 에러(실사용 QA P1: 1,600토큰 낭비 방지)
            return {"tool": "price_multiple_data", "status": "not_found", "subject": company,
                    "next_actions": [COMPANY_LOOKUP_NEXT_ACTION],
                    "warnings": [company_not_found_warning(company, listed_only=True),
                                 "전체 섹터 표는 company 없이 scope='sector'."]}
        else:
            # 260823: 기업의 섹터 코드가 scheme 마다 다른 원천에서 온다 —
            #   ksic 은 KSIC 하이브리드 버킷(업종코드), 업종분류는 분류 스냅샷 표(가장 가까운 스냅샷).
            # 261018(재설계 6단계): 그 날 기업 PER·PBR·시장·KSIC 섹터를 종목별 밸류에이션 스냅샷 표에서 읽던 것을 읽을 때 계산으로 바꿨다
            #   (`_firm_val_at` — 주간 곡선과 같은 정의). KSIC 섹터의 이름·배수는 이미 읽은 이 scheme·이 날짜의 섹터 표(`sectors`)에서
            #   찾는다 — 종전 조인(같은 날·같은 시장·같은 섹터·scheme='ksic')과 같은 행이다. 소수 둘째 자리 반올림도 같은 값이다.
            # 261018(같은 단계, 감사 4): 그 시점 칸이 비면 앞선 칸으로 센 값과 「기준 분기 늦음」 표시(`fnote["basis"]`) — 그런 종목에서만
            #   칸·경고가 붙는다(그 밖의 응답은 종전 그대로). 완전자본잠식(`fnote["impaired"]`)은 종전처럼 기업 행을 내지 않되 까닭을 바로 말한다.
            # 261019(재설계 6단계 읽는 쪽 복원): 기업 행이 없을 때 까닭을 가른다(`why`) — 종전엔 모두 「종목 스냅샷 없음(비상장·미수집)」이라
            #   상장·수집된 회사를 두고 틀린 말을 했다(스냅샷 표는 없앴다). 재무 요약 미수집·그 주 시세 없음·업종분류에 없음·DB 읽기 실패(`_firm_row_missing`).
            fv = await _firm_val_at(isu, as_of)
            fr = []
            fnote: dict = {}
            why = "db" if fv is None else None
            if fv and (fv[4].get("impaired") or fv[4].get("missing")):
                fnote, fv = fv[4], None
            if fv:
                sec, market, pt, pb, fnote = fv
                sr = [x for x in sectors if x["market"] == market and x["sector"] == sec] if scheme == "ksic" else []
                fr = [(sec, market, pt, pb, sr[0]["label"] if sr else None,
                       sr[0]["per_ttm"] if sr else None, sr[0]["pbr_mrq"] if sr else None)]
            if fr and scheme != "ksic":
                # 업종분류는 종목의 섹터 코드가 분류 스냅샷 표에 있다. 스냅샷은 as_of 이하의
                # 가장 최근 것, 없으면 가장 이른 것(=소급) — 폴백 규칙은 집계 백필과 동일.
                _, market, pt, pb, *_ = fr[0]
                col = "sector_code" if scheme == MAJOR else "industry_code"
                nmc = "sector" if scheme == MAJOR else "industry"
                wr = await asyncio.to_thread(_pg_rows,
                    f"SELECT {col}, {nmc}, snap_dd FROM wise_sector WHERE ticker=%s "
                    "ORDER BY (snap_dd <= %s) DESC, ABS(snap_dd::bigint - %s::bigint) LIMIT 1",
                    (isu, as_of, as_of))
                if wr:
                    sec, lbl, sector_asof = wr[0][0], wr[0][1], wr[0][2]
                    sr = [x for x in sectors if x["market"] == market and x["sector"] == sec]
                    spt = sr[0]["per_ttm"] if sr else None
                    spb = sr[0]["pbr_mrq"] if sr else None
                    fr = [(sec, market, pt, pb, lbl, spt, spb)]
                    _class_asof = sector_asof
                else:
                    why = "db" if wr is None else "unclassified"
                    fr = []
            if fr:
                sec, market, pt, pb, lbl, spt, spb = fr[0]
                if lbl is None:  # 소규모 섹터 → mkt_val_history엔 '_fold'로 접혀 raw 코드 JOIN 미스
                    fold = [s for s in sectors if s["market"] == market and s["sector"] == "_fold"]
                    if fold:  # 폴드 버킷과 비교(정직하게 표기) — literal None 렌더 방지(QA 109종목 실측)
                        lbl = f"{fold[0]['label']} (소규모 섹터 {sec} 포함)"
                        spt, spb = fold[0]["per_ttm"], fold[0]["pbr_mrq"]
                    else:
                        lbl = f"KSIC {sec} (섹터 집계 없음)"
                company_ctx = {"name": corp.get("corp_name"), "ticker": isu, "market": market,
                               "sector": sec, "sector_label": lbl,
                               "sector_asof": locals().get("_class_asof"),  # 업종분류 소급 여부 표시
                               "firm_per_ttm": pt and round(pt, 2), "firm_pbr_mrq": pb and round(pb, 2),
                               "sector_per_ttm": spt and round(spt, 2), "sector_pbr_mrq": spb and round(spb, 2)}
                if fnote.get("basis"):
                    b = fnote["basis"]
                    company_ctx["firm_basis"] = b
                    used = " · ".join(f"{nm} {b[k]}" + ("(사업보고서)" if b[k].endswith("Q4") else "")
                                      for k, nm in (("per_ttm", "PER(TTM)"), ("pbr_mrq", "PBR(MRQ)")) if k in b)
                    warnings.append(f"기준 분기 늦음 — {corp.get('corp_name')} 의 그 시점 칸({b['pit_q']})이 비어 {used} 칸으로 셌다"
                                    "(그 시점까지 공시 마감이 지난 칸 가운데 값이 있는 가장 늦은 칸, 1년 안 — 반기 결산 리츠·보고서 미제출·결산월 변경 등).")
                # 소속 섹터의 과거 시계열(2020-01~, market_val_history_backfill.py가 채움) — 0콜, 이미 DB에 있음.
                # 소규모(_fold) 섹터면 fold 버킷 자체의 히스토리로 폴백(개별 sec 코드는 mkt_val_history에 없음).
                hist_sector = "_fold" if lbl and "(소규모 섹터" in lbl else sec
                # 261019: scheme 은 공개 이름(「대분류」·「중분류」)이고 표의 scheme 칸은 옛 값(wics_sector·wics_industry)이다(`_db_scheme`).
                #   260921 이름 전환 때 이 질의만 변환이 빠져, 업종분류 축의 sector_history 가 0행이었다(운영 005930 중분류 — 표에는 88행, 10-18 실측).
                hrows = await asyncio.to_thread(_pg_rows,
                    "SELECT snap_dd, per_fy0, per_ttm, pbr_fy0, pbr_mrq, cap, ni_fy0, ni_ttm "
                    "FROM opm_val_market "
                    "WHERE market=%s AND sector=%s AND scheme=%s ORDER BY snap_dd",
                    (market, hist_sector, _db_scheme(scheme))) or []
                if hrows:
                    company_ctx["sector_history"] = [
                        {"snap_dd": h[0], "per_fy0": h[1] and round(h[1], 2), "per_ttm": h[2] and round(h[2], 2),
                         "pbr_fy0": h[3] and round(h[3], 2), "pbr_mrq": h[4] and round(h[4], 2),
                         "cap_krw": h[5], "ni_fy0_krw": h[6], "ni_ttm_krw": h[7]}
                        for h in hrows]
            elif fnote.get("impaired"):
                warnings.append(f"'{company}' 는 그 시점 완전자본잠식(지배주주 자본 ≤ 0) — 기업 배수·비교를 내지 않는다.")
            else:
                warnings.append(_firm_row_missing(company, why or fnote.get("missing"), as_of))
    return {"tool": "price_multiple_data", "status": "ok", "subject": "산업별 밸류에이션",
            "data": {"scope": "sector", "as_of": as_of, "as_of_requested": as_of_req, "scheme": scheme,
                     # 261019: 이 축의 최신 스냅샷이 시장 전체보다 이르면 {axis_as_of, market_as_of, reason} — 그럴 때만 칸이 생긴다(종전 응답 그대로)
                     **({"stale": stale} if stale else {}),
                     "scheme_desc": _SECTOR_SCHEMES[scheme], "sectors": sectors,
                     "fwd_ruler": fwd_ruler or None,
                     "fwd_method": _fwd_method(fwd_ruler.get("fy_main")) if fwd else None,
                     "div_yield_ruler": _merge_fwd_ruler(div_ruler, fwd_ruler) or None,
                     "div_yield_method": _DIV_METHOD if (div_act or has_fwd_div) else None,
                     "company": company_ctx},
            "warnings": warnings}


#: not_found 안내의 종류주 한 줄(261019) — 우선주·종류주 코드는 해석기가 발행 회사로 받는다(#114). 배수는 정의대로 보통주 시총 기준이고, 그 종류주 자체의 시세는 시세 도구.
PREF_CODE_HINT = ("우선주·종류주는 그 종목코드(예: 005935)로 조회해도 된다 — 발행 회사의 배수(보통주 시총 기준)를 주고, "
                  "그 종류주 자체의 주가·시총은 trading_data 가 준다.")


#: 업종 비교에서 기업 행이 없는 까닭(`_firm_val_at` 의 `missing` · 업종분류 조회 결과) → 사용자 문장. 재무 요약 수집 상태는 `dart_fundamentals.fetched` 값이다.
_FIRM_ROW_MISSING = {
    "nofin": "재무 요약이 아직 없다(신규 상장 등 — 수집 전)",
    "nocorp": "재무 요약을 DART 회사와 잇지 못했다(회사 고유번호 매칭 없음)",
    "outlier_scale": "재무 요약이 규모 이상치로 걸러졌다",
    "price": "그 주에 시세가 없다(상장 전·거래정지·상장폐지)",
}


def _firm_row_missing(company: str, why: str | None, as_of: str) -> str:
    """업종 비교(company 지정)에서 기업 행이 없을 때의 경고 — 실제 까닭으로(261019). 종전 문장 「종목 스냅샷 없음(비상장·미수집)」은 없앤 표를 가리켰고,
    비상장은 앞에서 not_found 로 끝나므로 여기 오지 않는다."""
    if why == "db":
        return f"'{company}' 기업 행을 읽지 못했다(DB 읽기 실패) — 잠시 뒤 다시 조회한다."
    if why == "unclassified":
        return (f"'{company}' 기업 행 없음 — 업종분류 스냅샷 어디에도 없는 종목이다(분류 공급처 구성종목 밖). "
                "KSIC 축(scheme='ksic')으로는 비교할 수 있다.")
    reason = _FIRM_ROW_MISSING.get(why or "") or f"재무 요약 수집 상태가 정상이 아니다({why})"
    when = f" — 기준일 {as_of}" if why == "price" else ""
    return f"'{company}' 기업 행 없음 — {reason}{when}. 기업 배수·비교를 내지 않는다."


def _latest_annual_fy() -> int:
    """지금 시점 확정된 최신 사업연도 — 사업보고서 3월 공시(4월 이후 전년, 아니면 전전년).
    market_fund_quarterly._latest_annual_fy / market_val_series._latest_annual_fy와 동일 규칙.
    ⚠ dart_fundamentals.ni_fy/eq_fy는 derive_fundamentals가 이 FY로 덮어쓰는 **가변열**이라,
    그 값을 담을 fin[] 키는 반드시 이 헬퍼로 파생해야 함(하드코딩 금지 — FY 넘어가면 조용히 오라벨)."""
    from datetime import date
    t = today_kst()
    return t.year - 1 if t.month >= 4 else t.year - 2


def _pit_fy(price_dd: str, fye: int = 12) -> int:
    """PIT 근사: 그 시점 최신 확정 FY(사업보고서의 DART 사업연도 표기). 12월 결산이면 사업보고서 3월 공시 → 4월 이후면 전년 FY,
    아니면 전전년(look-ahead 방지 — market_val_series.py series()와 동일 규칙).

    261018(재설계 6단계): 결산월 `fye` 를 받는다(`report_calendar.pit_annual` — 결산월 다음 넷째 달 1일부터). 3월 결산이면 7/1 부터 그해 3월 말
    사업보고서(표기 = 그해). 12월 결산(기본값)은 종전과 같다."""
    return _rc.pit_annual(price_dd, fye)


def _needs_fx(currency: str | None) -> bool:
    """재무를 KRW 로 환산해야 하는 통화인가. 환산은 환율 조회(외부 호출·직접 접속)를 연도·분기 순서대로 이어 가므로, 이 경우엔 두 재무 로더를 겹치지 않는다."""
    return (currency or "KRW").upper() not in ("KRW", "NODATA", "?")


async def _firm_fin_by_fy(ticker: str, currency: str = "KRW", fye: int = 12) -> dict[int, tuple]:
    """종목 FY별 (지배순이익, 지배자본) — KRW 환산 완료. dart_finstat_y(FY2018~과거, 스케일오류
    정정치 ni_restated/eq_restated 우선) ∪ dart_fundamentals(최신 확정 FY=_latest_annual_fy()).
    비KRW는 FY 기말환율 환산.

    261018: 연간 재무와 최신 확정 FY 요약은 입력이 겹치지 않아 두 읽기를 함께 보낸다(환산은 읽은 뒤 종전 순서 그대로).

    261018(재설계 6단계, 결산월): 재무 요약의 연간 값(`ni_fy`·`eq_fy`)을 얹는 자리 `_latest_annual_fy()` 는 **12월 결산의 달력 규칙**이다.
    12월 결산이 아니면(`fye`) 얹지 않는다 — 그 값은 분기 표 사업보고서 칸 가운데 가장 늦은 칸에서 파생되는데, 그 칸의 사업연도 표기가 달력 규칙과
    다를 수 있어(3월 결산은 7월부터 그해 3월 말 사업보고서가 최신) 엉뚱한 해에 얹히면 연간 값을 덮어쓴다. 같은 값은 연간 재무 표에 이미 있다
    (파생의 원천이 연간 표다). 환산 기준일은 결산월 말일(`fiscal_year_end_date` — 12월 결산이면 종전과 같은 12/31)."""
    rows, cur = await _gather_db(
        _db_rows("SELECT fy, ni, eq, ni_restated, eq_restated FROM dart_finstat_y WHERE ticker=%s "
                 "AND (fetched='ok' OR ni_restated IS NOT NULL OR eq_restated IS NOT NULL)", (ticker,)),
        _db_rows("SELECT ni_fy, eq_fy FROM dart_fundamentals WHERE ticker=%s", (ticker,)),
    )
    rows = rows or []
    fin: dict[int, tuple] = {}
    for fy, ni, eq, ni_r, eq_r in rows:
        ni_f = ni_r if ni_r is not None else ni
        eq_f = eq_r if eq_r is not None else eq
        fin[int(fy)] = (float(ni_f) if ni_f is not None else None,
                        float(eq_f) if eq_f is not None else None)
    if cur and cur[0][0] is not None and _rc.fye_month(fye) == 12:
        ov = (float(cur[0][0]), float(cur[0][1]) if cur[0][1] is not None else None)
        latest = _latest_annual_fy()
        # 261018: 재무 요약의 연간 값은 「분기 표에서 값이 있는 가장 늦은 사업보고서 칸」이다. 최신 사업보고서를 안 낸 회사(10-18 운영 2종목 —
        #   082660·241820)는 그 값이 2023·2024년 것인데 2025 자리에 얹혀 FY0 배수·TTM 의 직전 연간이 한두 해 낡은 값으로 나갔다.
        #   같은 값(순이익·자본 둘 다)이 이미 앞선 해에 있으면 그 해의 값이므로 얹지 않는다(최신 해는 값 없음 = N/M).
        if not any(y < latest and v == ov for y, v in fin.items()):
            fin[latest] = ov
    ccy = (currency or "KRW").upper()
    if _needs_fx(currency):
        for fy in list(fin):
            fx = await fx_to_krw(ccy, fiscal_year_end_date(fy, _rc.fye_month(fye)))
            ni_f, eq_f = fin[fy]
            fin[fy] = ((ni_f * fx if ni_f is not None else None,
                        eq_f * fx if eq_f is not None else None) if fx else (None, None))
    return fin


def _pit_quarter(price_dd: str, fye: int = 12) -> tuple[int, int]:
    """그 시점까지 공시된 최신 분기 → (fy, quarter) — 분기 재무 표의 칸(DART 사업연도 표기·보고서 순번). 공시지연(look-ahead 방지):
    12월 결산이면 사업보고서(Q4) 익년 3월 · 1Q 5/15 · 반기 8/14 · 3Q 11/14. Q4 경계는 _pit_fy(4월)와 일치.

    261018(재설계 6단계): 결산월 `fye` 를 받는다(`report_calendar.pit_cell`). 칸의 기간 말을 결산월로 계산하고(3월 결산의 「반기」 칸 = 9월 말)
    분기·반기는 기간 말 + 45일, 사업보고서는 결산월 다음 넷째 달 1일부터 쓴다. 종전엔 모든 회사의 칸을 달력 분기로 읽어 12월 결산이 아닌
    회사는 아직 안 나온 보고서를 앞당겨 썼다(3월 결산 「2026 반기」 = 2026.09 말 기준 · 11/14 마감인데 8/14 부터). 12월 결산(기본값)은 종전과 같다."""
    return _rc.pit_cell(price_dd, fye)


async def _firm_fin_by_q(ticker: str, currency: str = "KRW", fye: int = 12) -> dict[tuple, tuple]:
    """종목 (fy,quarter)별 (지배순이익 누적, 지배자본 잔액) — KRW 환산. dart_finstat_q.
    비KRW는 분기말 환율(Q1 0331·Q2 0630·Q3 0930·Q4 1231)로 환산 — 누적순익은 분기말 근사.
    261018(결산월): 환율 기준일은 그 칸의 실제 기간 말(`report_calendar.cell_end` — 12월 결산이면 종전 네 날짜 그대로)."""
    rows = await _db_rows(
        "SELECT fy, quarter, COALESCE(ni_cum_restated, ni_cum), COALESCE(eq_restated, eq) FROM dart_finstat_q WHERE ticker=%s", (ticker,)) or []
    finq: dict[tuple, tuple] = {}
    for fy, q, ni, eq in rows:
        finq[(int(fy), int(q))] = (float(ni) if ni is not None else None,
                                   float(eq) if eq is not None else None)
    ccy = (currency or "KRW").upper()
    if _needs_fx(currency):
        fxcache: dict[str, float | None] = {}
        for (fy, q) in list(finq):
            key = _rc.cell_end(fy, q, _rc.fye_month(fye)).strftime("%Y%m%d")
            if key not in fxcache:
                fxcache[key] = await fx_to_krw(ccy, key)
            fx = fxcache[key]
            ni_f, eq_f = finq[(fy, q)]
            finq[(fy, q)] = ((ni_f * fx if ni_f is not None else None,
                              eq_f * fx if eq_f is not None else None) if fx else (None, None))
    return finq


def _ttm_ni(fin: dict[int, tuple], finq: dict[tuple, tuple], fy: int, q: int, fye: int = 12) -> float | None:
    """TTM 지배순이익 = FY(fy-1) 연간 + 누적(fy,q) − 누적(fy-1,q). q==4면 사업보고서=연간(fy).

    261018(결산월): 「직전 회계연도 연간」의 표기는 `report_calendar.prior_annual` — 12월 결산이면 종전 그대로 fy-1, 3월 결산의 1~3분기 칸
    (6·9·12월 말)이면 같은 해 fy(그해 3월 말 사업보고서). 1년 전 같은 칸은 결산월과 무관하게 (fy-1, q)."""
    if q == 4:
        return fin.get(fy, (None, None))[0]
    cum_now = finq.get((fy, q), (None, None))[0]
    cum_prev = finq.get((fy - 1, q), (None, None))[0]
    fy_prev = fin.get(_rc.prior_annual(fy, q, fye), (None, None))[0]
    if cum_now is None or cum_prev is None or fy_prev is None:
        return None
    return fy_prev + cum_now - cum_prev


def _mrq_eq(fin: dict[int, tuple], finq: dict[tuple, tuple], fy: int, q: int) -> float | None:
    """최근분기(MRQ) 지배자본 잔액. q==4면 FY말 자본(연간)."""
    if q == 4:
        return fin.get(fy, (None, None))[1]
    return finq.get((fy, q), (None, None))[1]


async def _annual_pit_band(ticker: str, currency: str = "KRW",
                           fin: dict[int, tuple] | None = None, fye: int = 12) -> list[dict]:
    """연말 PIT PER/PBR 밴드 — **이미 있는 데이터로 질의 시 계산**(백필 저장 X, compute-on-query).
    연말 보통주 시총(krx_weekly, ticker=보통주 코드) ÷ 그 시점 최신 확정 FY 재무(dart_finstat_y).
    시총 기반이라 수정주가(분할·무상증자) 조정 불변. 비KRW는 그 FY 기말환율로 KRW 환산.
    261018(결산월): 「그 시점 최신 확정 FY」를 결산월로 고른다(`_pit_fy(…, fye)` — 3월 결산의 12월 말이면 그해 3월 말 사업보고서)."""
    caps = await _db_rows(
        "SELECT DISTINCT ON (substring(price_dd,1,4)) substring(price_dd,1,4), price_dd, mktcap "
        "FROM krx_weekly WHERE ticker=%s AND substring(price_dd,5,2)='12' "
        "ORDER BY substring(price_dd,1,4), price_dd DESC", (ticker,)) or []
    if fin is None:
        fin = await _firm_fin_by_fy(ticker, currency, fye)
    band = []
    for yr_s, price_dd, cap in caps:
        pit_fy = _pit_fy(price_dd, fye)
        if pit_fy not in fin or not cap:
            continue
        ni_k, eq_k = fin[pit_fy]
        cap = float(cap)
        # 260829: 완전자본잠식 시점은 배수를 내지 않는다(마스터 지시) — 집계와 같은 규칙.
        _impaired = eq_k is not None and eq_k <= 0
        band.append({
            "period": yr_s, "asof": price_dd, "pit_fy": pit_fy, "cap_krw": round(cap),
            "per_fy0": round(cap / ni_k, 2) if (ni_k and ni_k > 0 and not _impaired) else None,
            "pbr_fy0": round(cap / eq_k, 2) if eq_k and eq_k > 0 else None,
            "source": f"연말(PIT FY{pit_fy})",
        })
    return band


#: 주간 곡선 읽기 — 주간 시세 행(하한 이후, 'w') + 끝점 후보(일별 저장분에서 그 종목의 가장 늦은 한 줄 `price_latest`, 'd')를 **한 왕복**으로.
#: 시총은 둘 다 종가 × 상장주식수(같은 정의). 끝점을 따로 읽으면 firm_history 셋째 단계의 동시 읽기가 상한(`_PG_FANOUT`=3)을 넘어 왕복이 하나 는다.
_SERIES_SQL = ("SELECT price_dd, mktcap, 'w' FROM krx_weekly WHERE ticker=%s AND price_dd>=%s "
               "UNION ALL SELECT price_dd, mktcap, 'd' FROM price_latest WHERE ticker=%s")


def _join_daily_tail(px: list, tail: list | None, lo: str) -> list:
    """주간 시세 행 (날짜, 시총) 오름차순 끝에 일별 최신 한 줄을 잇는다 — 시세 도구 `trading._with_daily_tail` 과 같은 규칙(261019, 감사 3).
      · 꼬리가 마지막 주간 점보다 **늦을 때만** 쓴다(같은 날이면 그대로 — 금요일이 닫힌 뒤 주간 표에도 그날 행이 있다).
      · 마지막 주간 점과 **같은 주**(`_iso_wk_range` — ISO 주를 달력 연도로 자른 것, 주를 닫는 쪽과 같은 정의)면 갈아 끼우고, 더 늦은 주면 뒤에 붙인다.
      · 주간 행이 없는데 꼬리가 있으면(이번 주 신규 상장) 그 한 점이다. 하한(`lo`)보다 이른 꼬리(오래 거래정지)는 버린다.
    꼬리 읽기가 실패(None)·빈 결과면 주간 행 그대로다."""
    if not tail or not tail[0] or not tail[0][0]:
        return px
    dd, cap = tail[0][0], tail[0][1]
    if dd < lo or (px and dd <= px[-1][0]):
        return px
    if px and _iso_wk_range(dd)[0] == _iso_wk_range(px[-1][0])[0]:
        return [*px[:-1], (dd, cap)]
    return [*px, (dd, cap)]


async def _weekly_series(ticker: str, currency: str = "KRW",
                         fin: dict[int, tuple] | None = None,
                         finq: dict[tuple, tuple] | None = None,
                         since_dd: str | None = None, fye: int = 12,
                         raw_out: list | None = None) -> list[dict]:
    """차트용 주간 dense 시계열 — 주간 보통주 시총(krx_weekly) × 재무.
    FY0(연 계단, dart_finstat_y) + TTM/MRQ(분기 계단, dart_finstat_q). 분모는 공시시점 계단,
    주가는 주간이라 곡선은 촘촘. 시총 기반이라 수정주가 조정 불변. 반환은 asof 오름차순.
    TTM = FY(y-1) + 누적(y,q) − 누적(y-1,q) — 전년 동분기 필요해 사실상 2020~부터 산출.

    261017: **`since_dd`(보존 하한) 이상만 읽는다.** krx_weekly 는 최근 3년 + 종목별로 해마다 마지막 행(연말 행)만
    남기도록 줄이는데, 하한 이전은 연말 한 점씩이라 그대로 읽으면 촘촘한 곡선 앞에 1년 간격 점이 조용히 섞인다.
    비우면 저장된 최신 주에서 스스로 구한다(`_weekly_bounds`).

    261019(재설계 6단계 읽는 쪽 복원, 감사 3): **끝점은 일별 저장분의 그 종목 최신 한 줄**(`price_latest`)이다 — `_join_daily_tail`.
    krx_weekly 는 닫힌 주만 담게 되어(5-1 쓰는 쪽) 화~금에는 곡선 끝이 지난 금요일이었다. 같은 도구의 scope=firm 은 어제 값을 내는데 firm_history 의
    「최신 X 기준」·series 마지막 점·월말 요약, 이번 주 날짜를 준 과거 시점(firm_at)은 지난 주 값이었다. 종전(주간 표의 이번 주 행을 매일 갈아 끼우던 때)엔
    최신 거래일이 곡선 끝이었다 — 그 모양으로 되돌린다. 시세 도구 종목 시계열(`trading._with_daily_tail`)과 같은 규칙이다."""
    if since_dd is None:
        _, since_dd = await _weekly_bounds()
    rows = await _db_rows(_SERIES_SQL, (ticker, since_dd, ticker)) or []
    px = sorted((r[0], r[1]) for r in rows if r[2] == "w")          # 날짜 오름차순(종목 안에서 날짜는 하나씩)
    px = _join_daily_tail(px, [(r[0], r[1]) for r in rows if r[2] == "d"], since_dd)
    if fin is None:
        fin = await _firm_fin_by_fy(ticker, currency, fye)
    if finq is None:
        finq = await _firm_fin_by_q(ticker, currency, fye)
    series = []
    for price_dd, cap in px:
        pt = _pit_point(price_dd, cap, fin, finq, fye)
        if pt is not None:
            series.append(pt)
        if raw_out is not None:                     # 261018(감사 5): firm_history 의 「주간」 항목용 — 시세 행마다 (날짜, 시총), 추가 읽기 0
            raw_out.append((price_dd, cap))
    return series


def _gap_point(price_dd: str, cap, fin: dict[int, tuple], finq: dict[tuple, tuple], fye: int = 12) -> dict | None:
    """곡선이 비는 주(그 시점 칸이 다 비어 `_pit_raw` 가 점을 만들지 않은 주)의 대체 값 — 업종 비교 기업 행·「주간」 항목과 **같은 규칙**(`_row_val` —
    앞선 칸 1년 안, `basis` 표시)으로 센 한 점. 셀 것이 없거나(앞선 칸도 없음) 완전자본잠식이면 None. (261019)

    곡선 자체는 바꾸지 않는다(그 시점 칸만 쓰는 규칙 — `_pit_fallback` 참조). 10-19 검증: 2025 사업보고서·2026 분기보고서를 내지 않은
    피씨엘(241820)은 곡선이 20260327 에서 끝나, 20260814 를 물은 과거 시점(firm + as_of)이 20주 전 점으로 까닭 없이 답했다 — 같은 응답의
    「주간」 항목과 같은 날 업종 비교는 앞선 칸으로 값(PBR(MRQ) 6.19)을 냈다. firm_history 는 이 점들을 곡선 밖(`series_gap_fill`)에 싣고,
    과거 시점은 그 주 곡선 점이 없을 때 이 점으로 답하며 「기준 분기 늦음」을 밝힌다."""
    if not cap:
        return None
    rv = _row_val(price_dd, cap, fin, finq, fye)
    if rv is None or not rv.get("basis") or all(rv.get(k) is None for k in ("per_fy0", "pbr", "per_ttm", "pbr_mrq")):
        return None
    aq_fy, aq_q = _pit_quarter(price_dd, fye)
    return {"asof": price_dd, "pit_fy": _pit_fy(price_dd, fye), "pit_q": f"{aq_fy}Q{aq_q}", "cap_krw": round(float(cap)),
            "per_fy0": _r3(rv["per_fy0"]), "pbr": _r3(rv["pbr"]), "per_ttm": _r3(rv["per_ttm"]), "pbr_mrq": _r3(rv["pbr_mrq"]),
            "basis": rv["basis"]}


def _pit_impaired(price_dd: str, fin: dict[int, tuple], finq: dict[tuple, tuple], fye: int = 12) -> bool:
    """그 시점 완전자본잠식(지배자본 ≤ 0)인가 — 최근분기(MRQ) 자본, 없으면 그 시점 확정 FY 말 자본. 260829 마스터 지시: 잠식 시점은 배수를 내지 않는다."""
    aq_fy, aq_q = _pit_quarter(price_dd, fye)
    mrq = _mrq_eq(fin, finq, aq_fy, aq_q)
    eq_now = mrq if mrq is not None else fin.get(_pit_fy(price_dd, fye), (None, None))[1]
    return eq_now is not None and eq_now <= 0


def _pit_raw(price_dd: str, cap, fin: dict[int, tuple], finq: dict[tuple, tuple], fye: int = 12) -> dict | None:
    """주간 곡선의 한 점(반올림 전) — 그 날 보통주 시총 ÷ 그 시점까지 공시된 재무(PIT). 시총이 없으면 None.
    그 시점 확정 FY 의 연간 재무가 없으면(신규 상장 등) FY0 배수는 비우고 분기 배수만 낸다 — 그것도 없으면 None(점을 만들지 않는다).

    261018(재설계 6단계): `_weekly_series` 의 반복 본문을 떼어 냈다. 업종 비교(`build_sector_val_payload` 의 company)가
    종목별 밸류에이션 스냅샷 표 대신 **같은 함수로** 그 날 한 점을 계산한다 — 곡선과 업종 비교가 한 정의를 쓴다.
    반올림 전 값을 따로 두는 까닭: 업종 비교는 종전처럼 원값을 소수 둘째 자리로 한 번만 반올림한다(셋째 자리 반올림을 거치면 드물게 끝자리가 달라진다).

    261018(같은 단계, 신규 상장): 종전엔 그 시점 확정 FY 의 연간 행이 없으면 분기 자본이 있어도 None 이었다. 상장 첫해 종목(직전 사업보고서가
    없다 — 예: 2026년 상장, 분기 표에는 2026년 1·2분기 자본이 있다)은 곡선이 통째로 비고(firm_history 「시계열 없음」), 업종 비교의 PBR(MRQ)도
    N/M 이 됐다 — 스냅샷 표는 재무 요약의 최근 분기 자본으로 값을 냈다(10-18 운영 최신 주 4종목, 같은 값). 이제 분기 자본으로 PBR(MRQ)를 낸다.
    PER(TTM)은 직전 사업연도 연간 순이익이 있어야 하므로(`_ttm_ni`) 이 경우 언제나 비고, 스냅샷 표도 비어 있었다. `pit_fy` 는 그 시점 규칙상
    확정 FY 그대로다(값이 없음은 FY0 배수 null 로 드러난다 — 연간 행은 있는데 값이 빈 종목과 같은 모양).

    261018(같은 단계, 결산월): `fye`(결산월)로 그 시점 칸을 고른다 — `pit_fy`·`pit_q` 는 DART 표기(사업연도 = 보고서 기준일의 해) 그대로라
    3월 결산의 `2026Q1` 은 2026.06 말 1분기보고서다. 12월 결산(기본값)은 종전과 같다."""
    if not cap:
        return None
    fy = _pit_fy(price_dd, fye)
    ni_k, eq_k = fin.get(fy, (None, None))
    cap = float(cap)
    aq_fy, aq_q = _pit_quarter(price_dd, fye)
    ttm = _ttm_ni(fin, finq, aq_fy, aq_q, fye)
    mrq = _mrq_eq(fin, finq, aq_fy, aq_q)
    # 260829: 완전자본잠식 시점은 배수를 내지 않는다(마스터 지시).
    _impaired = _pit_impaired(price_dd, fin, finq, fye)
    p = {
        "asof": price_dd, "pit_fy": fy, "pit_q": f"{aq_fy}Q{aq_q}", "cap": cap,
        "per_fy0": cap / ni_k if (ni_k and ni_k > 0 and not _impaired) else None,
        "pbr": cap / eq_k if eq_k and eq_k > 0 else None,
        "per_ttm": cap / ttm if (ttm and ttm > 0 and not _impaired) else None,
        "pbr_mrq": cap / mrq if mrq and mrq > 0 else None,
    }
    if fy not in fin and p["per_ttm"] is None and p["pbr_mrq"] is None:
        return None   # 연간 행도 분기 배수도 없다 — 종전처럼 점을 만들지 않는다(시총만 있는 점으로 곡선을 채우지 않는다)
    return p


#: 업종 비교 기업 행의 직전 칸 대체가 거슬러 올라가는 한도(그 시점 칸의 기간 말에서 몇 해 전 같은 달 말까지) — 1년(직전 네 칸).
_FALLBACK_YEARS = 1


def _cell_label(cell: tuple[int, int]) -> str:
    return f"{cell[0]}Q{cell[1]}"


def _pit_fallback(price_dd: str, cap, fin: dict[int, tuple], finq: dict[tuple, tuple], fye: int = 12) -> dict:
    """그 시점 칸(PIT)이 비어 PER(TTM)·PBR(MRQ)를 셀 수 없을 때, **그보다 앞선 칸 가운데 값이 있는 가장 늦은 칸**으로 센다(업종 비교 기업 행·firm_history 「주간」 항목 — `_row_val`).

    261018(재설계 6단계, 감사 4 — 설계 v03 §6-0 P-1): 종전 스냅샷 표는 그날 수집돼 있던 가장 늦은 분기(재무 요약 파생값)로 값을 냈다.
    읽을 때 계산으로 바꾸며 분모를 그 시점 칸(PIT)으로 맞추자, 그 칸이 비는 종목 — 리츠(결산마다 사업보고서만 — 1·3분기 칸이 없다)·
    반기보고서 미제출·결산월을 바꾼 회사(종전 읽기 12월의 칸이 빈다)·늦은 수집 — 의 업종 비교 값이 N/M 이 됐다(10-18 실측 최신 주 7·과거 주 최대 24종목).
    그래서 그 칸이 **비었을 때만**(값이 있는데 적자·잠식인 경우는 그대로 N/M) 앞선 칸으로 채우고 어느 칸을 썼는지 돌려준다 — 응답은 「기준 분기 늦음」으로
    표시한다. 앞선 칸만 보므로 아직 안 나온 보고서를 앞당겨 쓰지 않는다. 거슬러 올라가는 한도는 기간 말 기준 1년(`_FALLBACK_YEARS`)이다.
    곡선(`_pit_raw`)은 바꾸지 않는다 — 곡선은 종전부터 그 칸이 비면 N/M 이었다.

    반환: {"per_ttm": (값 또는 None(적자), 칸), "pbr_mrq": (값 또는 None, 칸), "impaired": 대체 칸 자본 ≤ 0} — 대체가 없으면 빈 dict."""
    if not cap:
        return {}
    cap = float(cap)
    aq = _pit_quarter(price_dd, fye)
    month = _rc.fye_month(fye)
    end0 = _rc.cell_end(aq[0], aq[1], month)
    floor = _rc._last_day(end0.year - _FALLBACK_YEARS, end0.month)
    cells = sorted((c for c in set(finq) | {(fy, 4) for fy in fin}
                    if floor <= _rc.cell_end(c[0], c[1], month) < end0),
                   key=lambda c: _rc.cell_end(c[0], c[1], month), reverse=True)
    out: dict = {}
    if _ttm_ni(fin, finq, aq[0], aq[1], fye) is None:
        for c in cells:
            ttm = _ttm_ni(fin, finq, c[0], c[1], fye)
            if ttm is not None:
                out["per_ttm"] = (cap / ttm if ttm > 0 else None, c)
                break
    if _mrq_eq(fin, finq, aq[0], aq[1]) is None:
        for c in cells:
            eq = _mrq_eq(fin, finq, c[0], c[1])
            if eq is not None:
                out["pbr_mrq"] = (cap / eq if eq > 0 else None, c)
                if eq <= 0:
                    out["impaired"] = True
                break
    return out


def _r3(v):
    return _round_mult(v, 3) if v is not None else None


def _pit_point(price_dd: str, cap, fin: dict[int, tuple], finq: dict[tuple, tuple], fye: int = 12) -> dict | None:
    """`_pit_raw` 를 곡선 응답 모양으로(시총은 원 단위 정수, 배수는 소수 셋째 자리)."""
    p = _pit_raw(price_dd, cap, fin, finq, fye)
    if p is None:
        return None
    return {"asof": p["asof"], "pit_fy": p["pit_fy"], "pit_q": p["pit_q"], "cap_krw": round(p["cap"]),
            "per_fy0": _r3(p["per_fy0"]), "pbr": _r3(p["pbr"]), "per_ttm": _r3(p["per_ttm"]), "pbr_mrq": _r3(p["pbr_mrq"])}


#: `_firm_cap_at` 의 질의 — 그 날 행(주간 → 일별), 없으면 **같은 주 안 그 날 이하 가장 늦은 행**(주간 → 일별),
#: 그것도 없으면 **같은 주 안 그 날 뒤 가장 이른 행**(주간 → 일별). 앞 칸이 이긴다(`src`).
#: 「같은 주」 = `_iso_wk_range`(ISO 주를 달력 연도 안으로 자른 것 — krx_weekly 의 주 닫기와 같은 정의). 다른 주의 행은 쓰지 않는다.
_FIRM_CAP_SQL = (
    "SELECT mktcap, market, src FROM ("
    " SELECT mktcap, market, 1 AS src FROM krx_weekly WHERE ticker=%s AND price_dd=%s"
    " UNION ALL SELECT mktcap, market, 2 AS src FROM krx_daily_v WHERE ticker=%s AND trd_dd=%s"
    " UNION ALL (SELECT mktcap, market, 3 AS src FROM krx_weekly WHERE ticker=%s AND price_dd >= %s AND price_dd < %s"
    " ORDER BY price_dd DESC LIMIT 1)"
    " UNION ALL (SELECT mktcap, market, 4 AS src FROM krx_daily_v WHERE ticker=%s AND trd_dd >= %s AND trd_dd < %s"
    " ORDER BY trd_dd DESC LIMIT 1)"
    " UNION ALL (SELECT mktcap, market, 5 AS src FROM krx_weekly WHERE ticker=%s AND price_dd > %s AND price_dd <= %s"
    " ORDER BY price_dd LIMIT 1)"
    " UNION ALL (SELECT mktcap, market, 6 AS src FROM krx_daily_v WHERE ticker=%s AND trd_dd > %s AND trd_dd <= %s"
    " ORDER BY trd_dd LIMIT 1)"
    ") x ORDER BY src LIMIT 1")


async def _firm_cap_at(ticker: str, dd: str) -> tuple[float | None, str | None] | None:
    """그 날 종목 보통주 시총과 시장. None = DB 장애, (None, None) = 그 주에 쓸 행이 없음.

    고르는 순서(`_FIRM_CAP_SQL`): ① 주간 시세 저장분(`krx_weekly`, 주 마지막 거래일·연말 행)의 그 날 행 ② 일별 시세 저장분(`krx_daily_v`,
    261019~ 최근 약 1년 — 그 전 7거래일)의 그 날 행 ③ 그 날 행이 정확히 없으면 **같은 주 안** krx_weekly 의 그 날 이하 가장 늦은 행 ④ 그것도 없으면 일별 저장분의 같은 주·그 날 이하 가장 늦은 행
    ⑤ 그것도 없으면 **같은 주 안** krx_weekly 의 그 날 뒤 가장 이른 행 ⑥ 그것도 없으면 일별 저장분의 같은 주·그 날 뒤 가장 이른 행.

    261018(재설계 6단계): 업종 비교의 기준일은 시장·업종 스냅샷(`opm_val_market`)의 날짜다. krx_weekly 는 닫힌 주만 담으므로(5-1 쓰는 쪽)
    이번 주 스냅샷 날짜는 일별 저장분에만 있다(②). ③~⑥ 은 스냅샷 날짜와 주간 행 날짜가 어긋난 주의 대비다 — 일별 보관 창이 지나면
    ①② 가 모두 비어 업종 비교에서 기업 행이 조용히 빠진다(「종목 스냅샷 없음」).
      · 주간 행이 스냅샷보다 이른 거래일로 닫힌 주 → 같은 주의 더 이른 날 값(③④).
      · 주간 행이 스냅샷보다 늦은 주(예: 그 주 마지막 배치가 실패해 스냅샷은 목요일, 주간 행은 금요일) → 같은 주의 더 늦은 날 값(⑤⑥).
        종전 스냅샷 표는 이 경우 스냅샷 날짜의 시총으로 행이 있었다 — 행을 잃는 것보다 같은 주 하루 이틀 차이의 시총이 종전 응답에 가깝다.
        그 날 이하 행이 있으면 언제나 그쪽이 먼저다. 다른 주로는 넘어가지 않는다(연도 경계 주도 둘로 나뉜 그대로).
    두 표 모두 시총 = 종가 × 상장주식수라 같은 날이면 같은 값이다."""
    wk0, wk1 = _iso_wk_range(dd)
    rows = await _db_rows(_FIRM_CAP_SQL, (ticker, dd, ticker, dd, ticker, wk0, dd, ticker, wk0, dd,
                                          ticker, dd, wk1, ticker, dd, wk1))
    if rows is None:
        return None
    if not rows or not rows[0][0]:
        return (None, rows[0][1] if rows else None)
    return (float(rows[0][0]), rows[0][1])


async def _firm_val_at(ticker: str, dd: str) -> tuple[str | None, str | None, float | None, float | None, dict] | None:
    """종목의 그 날 (KSIC 섹터 코드, 시장, PER(TTM), PBR(MRQ), 표시) — 배수는 반올림 전 원값. None = DB 장애.
    표시(dict)는 평소 빈 dict 다. 「기준 분기 늦음」이면 `basis`(그 시점 칸·실제로 쓴 칸), 완전자본잠식이면 `impaired`,
    그 날 행을 낼 수 없으면 `missing`(261019 — 재무 요약 없음 `nofin` · 수집 상태 값 그대로(`nocorp` 등) · 그 주 시세 없음 `price`).

    261018(재설계 6단계): 종전엔 종목별 밸류에이션 스냅샷 표(주간 배치가 시총 × 재무 요약으로 저장)에서 읽었다. 그 표를 없애고 읽을 때 계산한다.
      · 배수 = `_pit_raw`(주간 곡선과 같은 정의 — 그 날 시총 ÷ 그 시점까지 공시된 재무).
      · 시장 = 재무 요약(`dart_fundamentals.market`) — 스냅샷 표도 같은 값을 담았다(16주 41,311행 전부 일치, 같은 날 KRX 시장과도 일치 · 10-18 실측).
      · KSIC 섹터 = `ksic_bucket(업종코드)` — 배치와 같은 규칙(41,311행 전부 일치).
    행을 내지 않는 경우는 스냅샷 표에 행이 없던 경우와 같게 맞췄다: 재무 요약이 없거나 수집 정상이 아님 · 그 날 시세 행이 없음(261019 부터 `missing` 으로
    까닭을 돌려준다 — 업종 비교 경고가 그 까닭을 말한다). DB 장애는 None(종전 `or []` 처럼 행 없음 — 경고만 「읽지 못했다」로 가른다). 그 시점 완전자본잠식(260829 마스터 지시 — 「모든 연산·출력에서 뺀다」, 배치도 08-29 부터 그 종목을 빼고 저장했다)이면
    배수 없이 `impaired` 표시만 돌려주고, 업종 비교는 기업 행을 내지 않는다 — 종전엔 「종목 스냅샷 없음(비상장·미수집)」이라고 잘못 말했다.
    분모는 그 시점까지 공시 마감이 지난 분기(PIT)다 — firm_history 곡선과 같다. 스냅샷 표는 그날 수집돼 있던 가장 늦은 분기(재무 요약의 파생값)를
    썼다. **그 칸이 비면** 앞선 칸 가운데 값이 있는 가장 늦은 칸(1년 안)으로 센다(`_pit_fallback` — 감사 4, `basis` 표시). 빈 칸의 원인은 세 갈래다.
      · **분기 재무 수집 결함** — 늦게 낸 보고서를 다시 받지 않던 것(마감 뒤 400일 재확인으로 고침)과, 응답은 받았는데 지배 순이익·자본을
        못 찾던 파서 공백(표준 계정코드 없는 순이익 총계 행·빈 지배자본 행·재무상태표가 빠진 응답 — 이름으로 찾기·XBRL 원본 대체로 고침).
        둘 다 수집 쪽을 고쳐 다시 받았다.
      · 그 칸이 원래 없음 — 반기보고서 미제출·리츠(결산마다 사업보고서만): 앞선 칸으로 채운다(기준 분기 늦음). 그 칸으로 센 TTM 이 적자면 N/M 그대로다.
      · 수집 공백이 아닌 읽는 쪽 한계 — 12월 결산이 아닌 회사를 달력 분기로 읽던 것(이제 결산월 `dart_fundamentals.acc_mt` 로 칸을 고른다, 아래)과
        상장 첫해 종목(분기 자본으로 PBR(MRQ), `_pit_raw`). 결산월을 바꾼 회사는 바꾼 해의 DART 칸이 두 결산월에서 겹쳐 종전 읽기(12월)로 두므로,
        그 규칙의 최신 칸이 비면 앞선 칸으로 채운다(기준 분기 늦음).
    재무 요약에 연간 값이 빈 종목(외국 상장사 등)은 연간 재무 표로 채워 오히려 값이 생긴다. 상장 첫해 종목(그 시점 확정 FY 의 연간 행이 없다)은
    분기 자본으로 PBR(MRQ)를 낸다 — 스냅샷 표와 같은 값(`_pit_raw`).

    261018(같은 단계, 결산월): 재무 요약의 결산월(`acc_mt`)을 함께 읽어 칸 고르기·TTM·환율 기준일에 쓴다. 비었으면 12월(종전과 같다)."""
    cur_row, cap_mkt = await _gather_db(
        _db_rows("SELECT market, induty, currency, fetched, acc_mt FROM dart_fundamentals WHERE ticker=%s", (ticker,)),
        _firm_cap_at(ticker, dd),
    )
    if cur_row is None or cap_mkt is None:              # DB 장애
        return None
    if not cur_row or cur_row[0][3] != "ok":            # 261019: 까닭을 돌려준다(업종 비교 경고 — `_firm_row_missing`)
        return (None, None, None, None, {"missing": (cur_row[0][3] if cur_row else None) or "nofin"})
    if cap_mkt[0] is None:
        return (None, cap_mkt[1], None, None, {"missing": "price"})
    market, induty, currency, _, acc_mt = cur_row[0]
    currency = currency or "KRW"
    fye = _rc.fiscal(acc_mt, ticker)               # 결산월 + 외국 상장사 기한(12월 결산이 아닐 때만)
    if _needs_fx(currency):                       # 환산은 환율 조회를 연도 → 분기 순서로 이어 간다(firm_history 와 같은 순서)
        fin = await _firm_fin_by_fy(ticker, currency, fye)
        finq = await _firm_fin_by_q(ticker, currency, fye)
    else:
        fin, finq = await _gather_db(_firm_fin_by_fy(ticker, currency, fye), _firm_fin_by_q(ticker, currency, fye))
    sec = ksic_bucket(induty, ticker)
    rv = _row_val(dd, cap_mkt[0], fin, finq, fye)
    if rv is None:
        return (sec, market, None, None, {"impaired": True})
    return (sec, market, rv["per_ttm"], rv["pbr_mrq"], {"basis": rv["basis"]} if rv["basis"] else {})


def _row_val(dd: str, cap, fin: dict[int, tuple], finq: dict[tuple, tuple], fye: int = 12) -> dict | None:
    """종전 종목별 스냅샷 표 한 행의 값(반올림 전) — 업종 비교 기업 행(`_firm_val_at`)과 firm_history 「주간」 항목(`_weekly_hist_items`)이 쓴다.

    곡선 점(`_pit_raw`)과 같고, 그 시점 칸이 비어 PER(TTM)·PBR(MRQ)를 셀 수 없으면 앞선 칸으로 센다(`_pit_fallback` — `basis` 에 그 시점 칸·쓴 칸).
    None = 그 시점 완전자본잠식(그 시점 칸, 대체했으면 대체 칸의 자본 ≤ 0) — 260829 마스터 지시로 행을 내지 않는다."""
    if _pit_impaired(dd, fin, finq, fye):
        return None
    p = _pit_raw(dd, cap, fin, finq, fye) or {}
    out = {k: p.get(k) for k in ("per_fy0", "pbr", "per_ttm", "pbr_mrq")}
    out["basis"] = None
    fb = _pit_fallback(dd, cap, fin, finq, fye)
    if fb.get("impaired"):                        # 대체 칸의 자본이 0 이하 — 잠식으로 본다
        return None
    if fb:
        basis = {"pit_q": _cell_label(_pit_quarter(dd, fye))}
        for k in ("per_ttm", "pbr_mrq"):
            if k in fb:
                out[k], basis[k] = fb[k][0], _cell_label(fb[k][1])
        out["basis"] = basis
    return out


def _month_end_summary(series: list[dict], months: int = 12) -> list[dict]:
    """주간 series → 월말(각 월 마지막 거래주) 다운샘플, 최근 `months`개월. series의 부분집합이라
    차트와 100% 일치. pit_q 변경 달에 분기공시 마커(▲) — 배수 변화가 가격/실적 어느 쪽인지 표시."""
    if not series:
        return []
    by_month: dict[str, dict] = {}
    for pt in series:              # asc 정렬 → 각 월 마지막 관측이 최종(=월말)
        by_month[pt["asof"][:6]] = pt
    out: list[dict] = []
    prev_q = None
    for ym in sorted(by_month)[-months:]:
        pt = dict(by_month[ym])
        pt["marker"] = f"▲{pt.get('pit_q')}" if prev_q is not None and pt.get("pit_q") != prev_q else ""
        prev_q = pt.get("pit_q")
        out.append(pt)
    return out


async def build_firm_at_payload(company: str, as_of: str) -> dict[str, Any]:
    """종목의 **과거 시점** 배수 — firm_history 의 주간 곡선(krx_weekly 최근 3년 × DART 재무 PIT)에서 as_of 이하
    가장 최근 점 하나(260907: 종목별 주간 스냅샷 표는 최근 몇 주만 있어 과거로 못 갔다 — 261018 그 표는 없앴다).
    scope=firm 의 실시간 계산(DART×krx)은 과거로 못 가므로 곡선으로 답한다.

    261017: 주간 곡선은 보존 하한(최신 주 3년 전) 이후만이다. 그 이전 기준일은 연말 한 점씩만 남아 있어
    「가장 가까운 옛 점」으로 답하면 몇 달~1년 낡은 값이 기준일 값처럼 나간다 — 그렇게 답하지 않고 안내한다."""
    hist = await build_firm_history_payload(company, format="json")
    if hist.get("status") != "ok":
        return hist
    d = hist["data"]
    series = d.get("series") or []
    floor = d.get("weekly_from") or (krx_weekly_cutoff(series[-1]["asof"]) if series else None)
    # 261019: 곡선이 비는 주는 앞선 칸으로 센 대체 점(`series_gap_fill` — 업종 비교·「주간」 항목과 같은 규칙)으로 답한다.
    #   종전엔 그 주 곡선 점이 없으면 그 이하 가장 늦은 곡선 점(피씨엘 20260814 → 20260327, 20주 전)으로 까닭 없이 답했다.
    gaps = d.get("series_gap_fill") or []
    pts = sorted([p for p in series if str(p.get("asof", "")) <= as_of]
                 + [g for g in gaps if str(g.get("asof", "")) <= as_of], key=lambda x: str(x.get("asof", "")))
    if not pts:
        warns: list[str] = []
        if floor and as_of < floor:
            warns.append(f"기준일 {as_of} 은 제공 범위 밖입니다. {weekly_window_note(floor)} "
                         "연말 값은 `scope=firm_history` 의 `history`(연말 PIT 밴드)에서 전체 기간으로 봅니다.")
        first = min([x.get("asof") for x in (series[:1] + gaps[:1]) if x.get("asof")], default=None)
        if first:
            warns.append(f"기준일 {as_of} 이하 주간 점 없음 — 곡선의 첫 점은 {first}. 기준일을 비우면 현재 값.")
        elif not warns:
            warns.append(f"기준일 {as_of} 이하 주간 점 없음 — 최근 3년 안에 시세가 없어 주간 곡선이 비어 있습니다"
                         "(거래정지·상장폐지 가능). 연말 값은 `scope=firm_history` 의 `history` 에서 봅니다.")
        return {"tool": "price_multiple_data", "status": "no_data", "subject": hist.get("subject", company),
                "warnings": warns}
    p = pts[-1]
    # 261018(결산월): 12월 결산이 아니면 곡선의 「결산월 …」 안내(칸 표기 읽는 법)를 그대로 옮긴다 — 12월 결산은 종전과 같다(안내 없음).
    fye_notes = [w for w in (hist.get("warnings") or []) if str(w).startswith(_FYE_NOTE_HEAD)]
    # 261019(감사 3): 이번 주 날짜면 곡선 끝점(일별 최신 한 줄)으로 답한다 — 그 점은 주 마지막 거래일이 아니라 그 주 최신 거래일이다.
    when = (f"일별 최신 거래일 {p['asof']}(이번 주 — 주간 곡선의 끝점)" if p["asof"] == d.get("series_daily_end")
            else f"주간 스냅샷(주 마지막 거래일 {p['asof']})")
    data = {"scope": "firm_at", "ticker": d.get("ticker"), "as_of_requested": as_of, "as_of": p["asof"],
            "market": d.get("market"), "sector": d.get("sector") or d.get("induty"),
            "pit_fy": p.get("pit_fy"), "pit_q": p.get("pit_q"), "cap_krw": p.get("cap_krw"),
            "per_fy0": p.get("per_fy0"), "per_ttm": p.get("per_ttm"), "pbr_fy0": p.get("pbr"), "pbr_mrq": p.get("pbr_mrq")}
    warns = [f"{when} · 재무는 그 시점까지 공시된 값(FY0={p.get('pit_fy')}, 분기={p.get('pit_q')}) · 배당수익률은 과거 시점 미제공."]
    b = p.get("basis")
    if b:   # 261019: 곡선이 비는 주 — 앞선 칸으로 센 대체 점
        data["basis"] = b
        used = " · ".join(f"{nm} {b[k]}" + ("(사업보고서)" if b[k].endswith("Q4") else "")
                          for k, nm in (("per_ttm", "PER(TTM)"), ("pbr_mrq", "PBR(MRQ)")) if k in b)
        warns.append(f"기준 분기 늦음 — 그 시점 칸({b.get('pit_q')})이 비어 주간 곡선에는 이 주 점이 없다. {used} 칸으로 셌다"
                     "(그 시점까지 공시 마감이 지난 칸 가운데 값이 있는 가장 늦은 칸, 1년 안 — 업종 비교·firm_history 「주간」 항목과 같은 규칙)."
                     + (f" FY0 배수는 그 시점 사업연도({p.get('pit_fy')}) 연간 재무가 없어 비운다."
                        if p.get("per_fy0") is None and p.get("pbr") is None else ""))
    # 261019: 답한 점이 요청 기준일(그 종목 마지막 시세 날 — 없으면 곡선 끝 — 을 넘으면 그 날)보다 2주 넘게 이르면 그 사이에 점이 없다는 것을 밝힌다.
    from datetime import date as _date
    _data_end = str(d.get("price_rows_end") or d.get("series_daily_end") or (series[-1].get("asof") if series else "") or as_of)
    _end = min(as_of, _data_end)
    try:
        _lag = (_date(int(_end[:4]), int(_end[4:6]), int(_end[6:8])) - _date(int(p["asof"][:4]), int(p["asof"][4:6]), int(p["asof"][6:8]))).days
    except (ValueError, TypeError):
        _lag = 0
    if _lag > 14:
        warns.append(f"요청 기준일 {as_of} 의 주에는 쓸 점이 없어 {_lag}일 이른 {p['asof']} 점으로 답했다 — 그 사이 곡선 점이 없다"
                     "(배수를 셀 재무가 없거나 — 완전자본잠식·1년 넘게 빈 공시 칸 — 그 주 시세가 없다).")
    return {"tool": "price_multiple_data", "status": "ok", "subject": hist.get("subject", company),
            "data": data, "warnings": warns + fye_notes}


#: 12월 결산이 아닌 종목의 곡선·과거 시점 응답에 붙는 안내의 머리(이 머리로 firm_at 이 옮겨 붙인다).
_FYE_NOTE_HEAD = "결산월 "

#: 정기보고서 이름의 기준 연월 — 「사업보고서 (2026.06)」·「[기재정정]사업보고서 (2025.06)」 → 6.
_REPORT_YM = re.compile(r"\((\d{4})\.(\d{2})\)")


def _source_report_month(fm: dict | None) -> int | None:
    """재무 요약(`build_financial_metrics_payload` summary)이 붙여 둔 출처 정기보고서 이름의 기준월. 없거나 못 읽으면 None.

    261019: 실시간 firm 이 결산월 칸(`dart_fundamentals.acc_mt`)을 읽을지 가르는 데 쓴다 — 이미 받아 둔 값이라 DART·DB 읽기가 늘지 않는다.
    재무 요약 표의 결산월도 같은 것(사업보고서 이름의 기준월 — `market_fund_quarterly.py --acc-mt`)에서 채운다."""
    nm = ((((fm or {}).get("data") or {}).get("source_report")) or {}).get("report_nm") or ""
    m = _REPORT_YM.search(str(nm))
    if not m:
        return None
    month = int(m.group(2))
    return month if 1 <= month <= 12 else None


def _fye_note(fye) -> str | None:
    """12월 결산이 아니면 칸 표기(`pit_fy`·`pit_q`) 읽는 법 한 줄 — 12월 결산은 None(종전 응답 그대로)."""
    month = _rc.fye_month(fye)
    if month == 12:
        return None
    m = {q: _rc.cell_month(q, month) for q in (1, 2, 3, 4)}
    lag = ("분기·반기는 기간 말 + 60일, 사업보고서는 결산월 다음 다섯째 달 1일(외국 상장사 제출 기한)"
           if getattr(fye, "foreign", False) else "분기·반기는 기간 말 + 45일, 사업보고서는 결산월 다음 넷째 달 1일")
    return (f"{_FYE_NOTE_HEAD}{month}월 — 분기·연간 칸은 DART 표기(사업연도 = 보고서 기준일의 해)다: "
            f"1분기 = 그해 {m[1]}월 말 · 반기 = {m[2]}월 말 · 3분기 = {m[3]}월 말 · 사업보고서 = 그해 {m[4]}월 말. "
            f"그 시점 칸은 {lag}부터 쓴다(공시 기한 — 아직 안 나온 보고서를 앞당겨 쓰지 않는다).")


#: firm_history `history` 의 「주간」 항목 수 — 종전 주간 스냅샷 표가 담던 최근 16주(설계 v03 감사 5: 22항목 = 연말 6 + 주간 16 유지).
_HIST_WEEKS = 16


def _weekly_hist_items(px: list, fin: dict[int, tuple], finq: dict[tuple, tuple], fye: int = 12,
                       latest_dd: str | None = None) -> list[dict]:
    """주간 시세 행(`_weekly_series(raw_out=…)`)의 최근 `_HIST_WEEKS` 주 → `history` 의 「주간」 항목(종전 주간 스냅샷 표 모양).
    「최근 16주」는 시장의 최근 16주다(`latest_dd` = 저장된 최신 주 — 곡선 끝점을 일별 최신 한 줄로 이었으면 그 날(261019), 그 16주 전 날 뒤만) —
    종전 표가 주마다 전 종목을 담았으므로, 상장폐지·거래정지로 시세가 끊긴 종목은 그 창 안에 남은 주만 나온다(종전과 같다).
    종전 표는 이번 주 행을 그 주 최신 거래일로 매일 갈아 끼웠으므로 화~금에는 이번 주 + 닫힌 15주였다 — 끝점을 이은 `px` 가 같은 모양이다.

    값은 그 주 업종 비교 기업 행과 같다(`_row_val` — 곡선 점, 그 시점 칸이 비면 앞선 칸으로 세고 그 항목에 `basis` 를 붙인다)를 소수 둘째 자리로
    한 번 반올림한다. `pbr` 은 PBR(MRQ), 없으면 PBR(FY0) — 종전 규칙. 재무가 없어 셀 것이 없는 주(인프라 펀드 등 — 종전 스냅샷 표는 시총만 담았다)는
    배수 없이 시총만 낸다. 그 시점 완전자본잠식인 주는 뺀다(260829 마스터 지시 — 스냅샷 배치도 08-29 부터 그 종목을 빼고 저장했다)."""
    out = []
    since = None
    if latest_dd:
        from datetime import date, timedelta
        since = (date(int(latest_dd[:4]), int(latest_dd[4:6]), int(latest_dd[6:8])) - timedelta(weeks=_HIST_WEEKS)).strftime("%Y%m%d")
    for price_dd, cap in px[-_HIST_WEEKS:]:
        if not cap or (since and price_dd <= since):
            continue
        rv = _row_val(price_dd, cap, fin, finq, fye)
        if rv is None:
            continue
        pbr = rv["pbr_mrq"] if rv["pbr_mrq"] is not None else rv["pbr"]
        item = {"period": f"{price_dd[:4]}-{price_dd[4:6]}-{price_dd[6:]}", "asof": price_dd, "cap_krw": float(cap),
                "per_fy0": _r2(rv["per_fy0"]), "per_ttm": _r2(rv["per_ttm"]), "pbr": _r2(pbr), "source": "주간"}
        if rv["basis"]:
            item["basis"] = rv["basis"]
        out.append(item)
    return out


async def build_firm_history_payload(company: str, format: str = "md") -> dict[str, Any]:
    """종목별 밸류에이션 히스토리 — 연말 PIT 밴드(전체 기간) + 주간 곡선(최근 3년). PER/PBR/시총 시계열(수정주가 조정 불변).
    전부 주간 시세 저장분 × DART 재무(연간·분기)로 읽을 때 계산한다(261018: 종목별 주간 스냅샷 표 읽기를 뺐다 — 재설계 6단계)."""
    query = (company or "").strip()
    if not query:
        return {"tool": "price_multiple_data", "status": "invalid", "subject": company,
                "warnings": ["회사명 또는 종목코드(6자리)를 입력하세요."]}
    corp, early = await _resolve_listed(query)   # 공용 리졸버 — firm과 동일 진입
    if early:
        return early
    if not corp:
        corp = await get_dart_client().lookup_corp_code(query)
    if not corp or not corp.get("stock_code"):
        # 회사 자체가 없는 것과, 회사는 있는데 비상장인 것은 다른 사건이다 —
        # 후자에 사명 변경 안내를 주면 사용자를 엉뚱한 쪽으로 끌고 간다.
        if not corp:
            return {"tool": "price_multiple_data", "status": "not_found", "subject": query,
                    "next_actions": [COMPANY_LOOKUP_NEXT_ACTION],
                    "warnings": [company_not_found_warning(company, listed_only=True)]}
        return {"tool": "price_multiple_data", "status": "unlisted", "subject": query,
                "warnings": [f"'{company}' 는 찾았으나 상장 종목이 아니다 — 배수는 주가가 있어야 낸다."]}
    isu = corp["stock_code"]
    # 261018(재설계 4단계 T5): 이 아래 DB 읽기는 종전에 **차례로** 기다렸다(왕복 ≈30ms+ × 9). 서로 입력이 필요 없는 것끼리 3단계로 묶는다 —
    #   ⓐ 통화·기준 주   ⓑ 연간·분기 재무(통화가 정해져야 환산한다)   ⓒ 연말 밴드·주간 곡선(ⓑ의 재무와 ⓐ의 기준 주가 필요)·수정주가 flag.
    #   동시에 도는 읽기는 호출당 `_PG_FANOUT`(기본 3)개까지라 단계마다 한 왕복이다(→ 3 왕복). 결과·오류 처리는 종전과 같다.
    # 261018(재설계 6단계): ⓐ 에 있던 종목별 밸류에이션 스냅샷 표 읽기를 뺐다(9 → 8 읽기). 그 표는 같은 주간 시총 × 재무 요약을
    #   주간 배치가 따로 저장해 둔 것이고, 여기서는 `history` 에 「주간」 항목(최근 16주)을 덧붙이는 데만 썼다 — 같은 주는 곡선(`series`)에 이미 있다.
    #   DB 장애 판정(db_error)은 그 읽기 대신 ⓐ 의 재무 요약 읽기로 한다 — 통화를 모른 채 KRW 로 계산하지 않는다.
    # 261018(결산월): 재무 요약에서 결산월(`acc_mt`)도 함께 읽는다 — 칸 고르기·TTM·환율 기준일에 쓴다(비었으면 12월 = 종전과 같다).
    cur_row, (latest_wk, weekly_from) = await _gather_db(
        _db_rows("SELECT market, induty, currency, acc_mt, fetched FROM dart_fundamentals WHERE ticker=%s", (isu,)),
        _weekly_bounds(),                                            # 주간 곡선은 보존 하한 이후만
    )
    if cur_row is None:   # DB 장애 — 재무·곡선을 읽지 않고 곧바로 낸다
        return {"tool": "price_multiple_data", "status": "db_error", "subject": corp.get("corp_name", query),
                "warnings": [_DB_ERROR_PAYLOAD_WARN]}
    currency = (cur_row[0][2] if cur_row and cur_row[0][2] else "KRW")
    fye = _rc.fiscal(cur_row[0][3] if cur_row else None, isu)   # 결산월 + 외국 상장사 기한(12월 결산이 아닐 때만)
    # ① 연말 PIT 밴드 + ③ 차트용 주간 dense series + ④ 최근 12개월 월말 요약 — 이미 있는 데이터
    #    (krx_weekly × mkt_finstat_y연간 × mkt_finstat_q분기)로 질의 시 계산(저장 X). 재무 로더는
    #    한 번만 만들어 공유. series는 FY0(연)+TTM/MRQ(분기) 곡선, summary는 그 월말 다운샘플.
    if _needs_fx(currency):
        # 비KRW(두산밥캣=USD 등): 환산이 환율 조회를 연도 → 분기 순서로 이어 간다. 겹치면 연말(1231)을 연간·분기 양쪽이 동시에 묻는다 — 종전 순서 그대로 둔다.
        fin = await _firm_fin_by_fy(isu, currency, fye)
        finq = await _firm_fin_by_q(isu, currency, fye)
    else:
        fin, finq = await _gather_db(_firm_fin_by_fy(isu, currency, fye), _firm_fin_by_q(isu, currency, fye))
    px: list = []
    band, series, flags = await _gather_db(
        _annual_pit_band(isu, currency, fin=fin, fye=fye),           # 종목별 연말 행 — 전체 기간(보존 대상)
        _weekly_series(isu, currency, fin=fin, finq=finq, since_dd=weekly_from, fye=fye, raw_out=px),
        _db_rows("SELECT flag, detail FROM krx_stock_flags WHERE ticker=%s", (isu,)),
    )
    summary = _month_end_summary(series, 12)   # 텍스트 표: 최근 12개월 월말 + 분기공시 마커
    # 261019: 곡선에 점이 없는 시세 주 — 앞선 칸으로 센 대체 점(`_gap_point`, 곡선 밖). 추가 읽기 0(이미 읽은 시세 행·재무).
    _in_series = {pt["asof"] for pt in series}
    gap = [g for g in (_gap_point(dd, cap, fin, finq, fye) for dd, cap in px if dd not in _in_series) if g is not None]
    # 261019(감사 3): 곡선 끝점을 일별 최신 한 줄로 이었으면(`_join_daily_tail`) 그 날 — 닫힌 최신 주보다 늦은 날이다. 「주간」 항목의 「시장의 최근 16주」도
    #   그 날 기준이다(종전 스냅샷 표도 이번 주 행을 그 주 최신 거래일로 매일 갈아 끼웠다 — 최근 16주 = 이번 주 + 닫힌 15주).
    daily_end = px[-1][0] if px and px[-1][0] > latest_wk else None
    weekly_hist = (_weekly_hist_items(px, fin, finq, fye, daily_end or latest_wk)
                   if cur_row and cur_row[0][4] == "ok" else [])
    if not band and not series and not weekly_hist:
        return {"tool": "price_multiple_data", "status": "no_data", "subject": corp.get("corp_name", query),
                # 261018: 사용자 문장에 저장소 표 이름을 쓰지 않는다(종전 「과거 시세(krx_weekly)·재무(dart_finstat_y)」 — 260907 정리의 남은 자리).
                "warnings": ["시계열 없음 — 과거 시세(주간 시세 저장분)·재무(연간·분기) 미수집."]}
    # `history` = 연말 PIT 밴드(전체 기간) + 최근 16주 「주간」 항목. 261018(재설계 6단계): 종전 「주간」 항목은 주간 스냅샷 표(최근 16주)에서 읽었다.
    #   그 표를 없애며 한때 뺐다가(22 → 6항목, 감사 5) 같은 주의 곡선 점(`series` 와 같은 `_pit_raw`)으로 다시 붙인다 — 칸 모양·소수 둘째 자리·
    #   시총(원 단위 실수)·「pbr = PBR(MRQ), 없으면 PBR(FY0)」 규칙은 종전 그대로다(`_weekly_hist_items`). 마크다운은 연말 항목만 그리므로 화면은 그대로다.
    hist = [{"period": b["period"], "asof": b["asof"], "cap_krw": b["cap_krw"],
             "per_fy0": b["per_fy0"], "per_ttm": None, "pbr": b["pbr_fy0"],
             "source": b["source"]} for b in band]
    hist += weekly_hist
    hist.sort(key=lambda h: h["asof"])
    latest_dd = series[-1]["asof"] if series else (weekly_hist[-1]["asof"] if weekly_hist else "?")   # 곡선이 없으면 「주간」 항목의 마지막 주(종전과 같다)
    warnings = [f"최신 {latest_dd} 기준. 정밀 배수(보통주 주가·배당·경고 포함)는 scope='firm' 사용.",
                weekly_window_note(weekly_from) + " 연말 값은 `history` 의 「연말(PIT FY…)」 항목."]
    if gap:   # 261019: 곡선이 비는 주 — 곡선은 그대로 두고 앞선 칸으로 센 값을 곡선 밖에 싣는다(`_gap_point`)
        warnings.append(f"주간 곡선에 점이 없는 주 {len(gap)}개({gap[0]['asof']}~{gap[-1]['asof']}) — 그 시점 재무 칸(사업보고서·분기보고서)이 "
                        "비어(미제출·미수집) 곡선에는 넣지 않았다(곡선은 그 시점까지 공시된 칸만 쓴다). 앞선 칸(1년 안)으로 센 값은 "
                        "`data.series_gap_fill`(점마다 `basis` — 기준 분기 늦음)이고 업종 비교·「주간」 항목과 같은 규칙이다.")
    if _fye_note(fye):                         # 12월 결산이 아니면 칸 표기 읽는 법(12월 결산은 종전 응답 그대로)
        warnings.append(_fye_note(fye))
    # 수정주가 파이프라인 flag 대조 — 분할(스핀오프)·미해결 조정 종목은 시계열 해석 주의(QA 권고). (읽기는 위 ⓒ 단계에서 함께 했다)
    for fl, detail in flags or []:
        if fl == "spinoff_break":
            warnings.append("⚠ 인적분할 이력(spinoff_break) — 분할 시점 시총 점프 + 직후 배수는 "
                            "재무 반영 지연으로 왜곡 가능. 시계열 비교 주의.")
        elif fl == "unresolved_adjustment":
            warnings.append("⚠ 미해결 주가조정 이력(unresolved_adjustment) — 시계열 해석 주의.")
    # 시장·섹터 — 종전엔 스냅샷 표 마지막 주의 값(재무 요약의 시장 · 업종코드의 KSIC 하이브리드 버킷), 없으면 재무 요약 그대로였다.
    #   261018: 같은 원천에서 바로 만든다(16주 41,311행에서 시장·버킷 모두 일치 실측). 업종코드가 비었으면 종전 폴백처럼 원래 값을 둔다.
    market = cur_row[0][0] if cur_row else None
    induty = cur_row[0][1] if cur_row else None
    sector = ksic_bucket(induty, isu) or induty
    # 261018(재설계 6단계): 종전 조건은 「곡선도 연말 밴드도 없음」이었다. 곡선이 없으면 연말 밴드가 있어도 최근 점이 하나도 없으니(「주간」 항목은
    #   곡선과 같은 점이거나 시총만 있는 항목이다) 이 안내를 낸다. 곡선·밴드·「주간」 항목이 모두 없으면 위에서 no_data 로 끝난다.
    if not summary:
        warnings.append("주간 곡선 미산출 — 주간 시세 저장분 또는 재무(연간·분기) 미수집.")
    data = {"scope": "firm_history", "ticker": isu, "market": market,
            "sector": sector, "history": hist, "series": series, "summary": summary,
            "weekly_from": weekly_from,
            "method": "보통주 시총 ÷ 재무. FY0=직전 확정 FY(연간 재무·PIT) · "
                      "TTM=최근4분기 지배순이익 FY(y-1)+누적(y,q)−누적(y-1,q)(분기 재무) · "
                      "MRQ PBR=최근분기 지배자본. 차트=최근 3년 주간 곡선(series, weekly_from 이후 — 그 이전 "
                      "연말 값은 history 의 연말 항목), 요약=최근 12개월 "
                      "월말(summary). 시총 기반이라 분할·무상증자 등 조정성 이벤트에 불변(유증·자사주 "
                      "소각·인적분할의 실제 시총 점프는 그대로 반영)."}
    if gap:         # 261019: 곡선이 비는 주의 대체 값(앞선 칸) — 그런 종목에서만 칸이 생긴다
        data["series_gap_fill"] = gap
    if px and series and px[-1][0] > series[-1]["asof"]:
        # 261019: 곡선 끝 뒤에도 그 종목 시세 행이 있으면(셀 재무가 없어 점을 못 만든 주) 그 마지막 시세 날 — 과거 시점이 「몇 주 이른 점으로 답했다」를
        #   가르는 데 쓴다. 곡선이 마지막 시세 행까지 닿은 종목은 칸이 없다(종전 응답 그대로).
        data["price_rows_end"] = px[-1][0]
    if daily_end:   # 261019(감사 3): 곡선 끝점을 일별 최신 한 줄로 이었을 때만 — 그 밖의 응답은 칸·문구가 종전 그대로다
        data["series_daily_end"] = daily_end
        data["method"] += f" 곡선 마지막 점({daily_end})은 이번 주 — 주간 저장분은 닫힌 주만 담아, 일별 시세 저장분의 그 종목 최신 거래일로 잇는다."
    return {"tool": "price_multiple_data", "status": "ok", "subject": corp.get("corp_name", query),
            "data": data, "warnings": warnings}


async def _acntall(client, cc: str, year: int, rc: str, fs: str | None = None) -> tuple[list, str | None]:
    """fs 지정 시 그 기준만, 미지정 시 CFS(연결)→OFS(별도) 폴백 후 성공한 기준을 함께 반환.
    260704 실측: 카카오뱅크·코스모신소재는 FY2025 연결이 없고 별도만 있어(연결대상 없음) CFS만
    시도하면 전 지표가 N/M. TTM은 연간·분기를 같은 기준으로 맞춰야 정합(연결/별도 혼용 방지)."""
    for cand in ((fs,) if fs else ("CFS", "OFS")):
        try:
            d = await client.get_fnltt_singl_acnt_all(cc, str(year), rc, cand)
            rows = (d.get("list", d) if isinstance(d, dict) else d) or []
            if rows:
                return rows, cand
        except DartClientError:
            continue
    return [], None


def _gid(rows, account_id, sj, field="thstrm_amount"):
    """account_id 정확일치(exact match) — substring 금지(260704 실측 사고: 접두어 충돌로
    'ifrs-full_Liabilities'가 'ifrs-full_LiabilitiesIncludedIn...'에 오매칭될 수 있음)."""
    v = gid_exact(rows, f"ifrs-full_{account_id}", sj, field)
    return int(v) if v is not None else None


def _ctrl_equity(rows, field="thstrm_amount"):
    """지배자본 = EquityAttributableToOwnersOfParent. 없으면(비지배지분 없는 회사는 이 계정을
    아예 안 적음) 총자본 − 비지배지분으로 폴백 — 260704 실측: 카카오뱅크·케이씨텍·코스모신소재·
    JW중외제약이 지배자본 계정 부재로 PBR이 N/M이던 것을 이 폴백이 해소(NCI 없으면 총자본=지배자본)."""
    eq = _gid(rows, "EquityAttributableToOwnersOfParent", ("BS",), field)
    if eq is not None:
        return eq
    total = _gid(rows, "Equity", ("BS",), field)
    if total is None:
        return None
    nci = _gid(rows, "NoncontrollingInterests", ("BS",), field) or 0
    return total - nci


def _ctrl_ni(rows, field="thstrm_amount"):
    """지배순이익 = ProfitLossAttributableToOwnersOfParent. 없으면(비지배지분 없는 회사) 총순이익
    − 비지배귀속 순이익으로 폴백(대칭 로직) — 260704 실측: 카카오뱅크·케이씨텍·코스모신소재가
    지배순이익 계정 부재로 PER이 N/M이던 것을 해소."""
    ni = _gid(rows, "ProfitLossAttributableToOwnersOfParent", ("CIS", "IS"), field)
    if ni is not None:
        return ni
    total = _gid(rows, "ProfitLoss", ("CIS", "IS"), field)
    if total is None:
        return None
    nci = _gid(rows, "ProfitLossAttributableToNoncontrollingInterests", ("CIS", "IS"), field) or 0
    return total - nci


def _ctrl_ni_cum(rows):
    """분기보고서의 **당기 누적** 지배순이익.

    🔴 DART 분기 원장은 `thstrm_amount`(당기 3개월)와 `thstrm_add_amount`(당기 누적)를
    따로 준다. **1분기는 둘이 같아서** 이 차이가 여태 드러나지 않았다 — TTM 이 1분기에
    못 박혀 있었기 때문이다(2026-08-30). 반기·3분기로 옮기는 순간 `thstrm_amount` 는
    그 분기 3개월치라 TTM 이 통째로 틀린다. 누적을 먼저 보고, 없을 때만 3개월로 내려간다.
    """
    ni = _ctrl_ni(rows, "thstrm_add_amount")
    return ni if ni is not None else _ctrl_ni(rows, "thstrm_amount")


def _eps_disclosed(rows: list, *fields: str) -> float | None:
    """공시 기본주당이익 — fields 우선순위로 첫 유효값. 3단 매칭(100사 스윕 실측으로 확장):
    ① 표준 `ifrs-full_BasicEarningsLossPerShare`
    ② 계속영업+중단영업 분리 공시(삼바형) — 분모(가중평균) 동일하므로 합산 = 총 기본 EPS
    ③ 비표준 코드('-표준계정코드 미사용-', LG형) — nm 기반(보통주·기본·비중단·비우선주)
    분기 응답은 thstrm_add_amount(누적 EPS)가 실존([[per-pbr-data-points]]) → TTM 조립 재료.
    두산밥캣류 USD EPS는 소수점 문자열("2.95") — int 파싱 금지, float."""
    def _val(r):
        for f in fields:
            v = r.get(f)
            if v not in (None, "", "-"):
                try:
                    return float(str(v).replace(",", ""))
                except (TypeError, ValueError):
                    pass
        return None

    for sj in ("IS", "CIS"):  # 통상 IS 하단, 일부 회사 CIS
        rs = [r for r in rows if r.get("sj_div") == sj]
        for r in rs:  # ① 표준 총 기본 EPS
            if r.get("account_id") == "ifrs-full_BasicEarningsLossPerShare":
                v = _val(r)
                if v is not None:
                    return v
        cont = disc = None  # ② 계속+중단 분리(삼바형)
        for r in rs:
            if r.get("account_id") == "ifrs-full_BasicEarningsLossPerShareFromContinuingOperations":
                cont = _val(r) if cont is None else cont
            elif r.get("account_id") == "ifrs-full_BasicEarningsLossPerShareFromDiscontinuedOperations":
                disc = _val(r) if disc is None else disc
        if cont is not None:
            return cont + (disc or 0)
        for r in rs:  # ③ 비표준 코드(LG형) — nm 기반, 보수적 필터
            if not (r.get("account_id") or "").startswith("-표준"):
                continue
            nm = (r.get("account_nm") or "").replace(" ", "")
            if "주당" in nm and "기본" in nm and "우선주" not in nm and "중단" not in nm:
                v = _val(r)
                if v is not None:
                    return v
    return None


#: 수정계수를 곱할 **조정성 이벤트**의 조건 — 액면분할·병합·무상증자·주식배당, 계수가 있고 라벨이 확정된 것만.
#: 주가와 주식수가 서로 상쇄하는 이벤트다(유상증자·감자·미라벨은 뺀다). 계수를 읽는 곳은 이 조건 하나를 쓴다 —
#: `_eps_adj_factor`(공시 EPS 정렬)와 `asset_holdings`(기말 보유수량을 오늘 주식수 기준으로, 261018).
ADJ_EVENT_SQL = ("event_type IN ('split','merge','bonus','stock_div') AND adj_factor IS NOT NULL "
                 "AND label_confidence='confirmed'")


async def _eps_adj_factor(ticker: str, after_dd: str) -> float:
    """(after_dd, 오늘] 조정성 이벤트(액면분할·병합·무상증자·주식배당)의 누적 수정계수 —
    수정주가 파이프라인 krx_adj_events(기준가 리셋 실측 + 라벨) 재사용.
    260823: krx_base_resets(측정) + krx_adj_factor_v3(라벨) 두 표를 통합했다 — 갱신이 2단계라
    뒤쪽이 깨져도 앞쪽만 돌면 멀쩡해 보였고, 실제로 260705 부터 라벨 쪽이 실행 불가였다.
    EPS는 가격과 같은 방향으로 조정(주식수 n배 → EPS 1/n = ×factor). 유상증자·감자·미라벨(None)은
    보수적으로 제외 — 잔여는 sanity 경고가 방어."""
    rows = await _db_rows(
        "SELECT adj_factor FROM krx_adj_events WHERE ticker=%s AND event_dd>%s AND " + ADJ_EVENT_SQL,
        (ticker, after_dd))
    f = 1.0
    for (x,) in rows or []:
        if x and x > 0:
            f *= x
    return f


async def _shares_ratio(ticker: str, after_dd: str) -> float | None:
    """(after_dd, 오늘] 상장주식수 변동배율 — KRX 상장주식수 실측(krx_weekly · 일별 저장분). 계수 누락 탐지용.

    260823: 수정계수 파이프라인이 **cron 없이 수동**이라(private wiki 「이벤트/수동」) 7주 밀려
    있었고, 그 사이 액면병합·분할 62종목의 계수가 통째로 비었다. 계수가 없으면
    `_eps_adj_factor` 가 조용히 1.0 을 돌려주고 공시 EPS 조각들이 **옛 분모와 새 분모로 섞인 채**
    조립된다(메이슨캐피탈 실측: TTM 지배순이익 -70억인데 EPS(TTM) +39원, PER 32.31 이 나갔다).

    261018(재설계 5-1): 분자(가장 최근 상장주식수)는 일별 저장분 `price_latest` 에서 읽는다 — krx_weekly 에 이번 주 행이
    없어져도 최신 주식수를 본다. 그 종목 행이 없거나(보관 창 내내 행이 없음·주식수 0) 못 읽으면(표 장애) **종전 krx_weekly 질의로 대신한다**
    — 배율이 비면 계수 누락을 못 잡아 섞인 EPS 가 그대로 나간다. 분모(기준일 뒤 첫 주)는 지난 값이라 krx_weekly 그대로다.
    분모가 없으면 배율을 낼 수 없으니 대신 읽기도 하지 않는다(장애 중에 읽기를 늘리지 않는다).
    """
    first, last = await _gather_db(      # 261018: 기준일 뒤 첫 주와 가장 최근 주식수는 서로 입력이 필요 없다 — 함께 보낸다
        _db_rows("SELECT list_shrs FROM krx_weekly WHERE ticker=%s AND price_dd>%s AND list_shrs>0 "
                 "ORDER BY price_dd ASC LIMIT 1", (ticker, after_dd)),
        _db_rows("SELECT list_shrs FROM price_latest WHERE ticker=%s AND list_shrs>0", (ticker,)),
    )
    if first and first[0][0] and not (last and last[0][0]):
        last = await _db_rows("SELECT list_shrs FROM krx_weekly WHERE ticker=%s AND list_shrs>0 "
                              "ORDER BY price_dd DESC LIMIT 1", (ticker,))
    if not first or not last or not first[0][0] or not last[0][0]:
        return None
    return last[0][0] / first[0][0]


# 계수 f 와 주식수 배율 r 의 불변식: 조정성 이벤트는 주가·EPS 와 주식수가 상쇄하므로 f × r ≈ 1.
# 벗어나면 「주식수는 변했는데 계수가 없다」 = 조립 EPS 가 섞였다는 뜻.
# 밴드를 ±50% 로 넉넉히 잡는 이유 — 유상증자·감자는 계수 대상이 아니라 정상적으로 r 만 움직인다.
# 30% 유상증자(r=1.3)는 통과시키고, 10:1 병합(r=0.1)만 잡는다.
_ADJ_INVARIANT_LO, _ADJ_INVARIANT_HI = 0.67, 1.5


async def _shares_outstanding(client, cc: str, year: int) -> dict:
    """유통주식수: total(보통+우선 합계, BPS용) · common(보통주, EPS용). 자기주식 제외(distb)."""
    out = {"total": None, "common": None}
    try:
        st = await client.get_stock_total(cc, str(year), "11011")
    except DartClientError:
        return out
    for r in (st.get("list", st) if isinstance(st, dict) else st) or []:
        se = (r.get("se") or "").strip()
        if se == "합계":
            out["total"] = _num(r.get("distb_stock_co"))
        elif se == "보통주":
            out["common"] = _num(r.get("distb_stock_co"))
    return out


def _dps_fetch_failed(div_err: Any) -> bool:
    """배당 사항 조회가 **실패**했나 — `_annual_summary` 의 두 번째 값(실패 시 「alotMatter 조회 실패: {DART 상태}」, 아니면 None).
    상태 013(조회된 데이터 없음)은 실패가 아니라 그 사업보고서에 배당 항목이 없다는 뜻이라(`guideline_research` 등도 013 을 no_rows 로 친다)
    무배당·미공시 쪽에 둔다. 그 밖의 상태(요청 한도·전송 오류 등)는 실패 — DPS 를 모른다(261018)."""
    if not div_err:
        return False
    return str(div_err).rsplit(":", 1)[-1].strip() != "013"


#: 회사 개황에서 업종코드를 못 받았을 때의 대체 — 저장된 업종코드(재무 요약)와 업종분류 최신 스냅샷의 대분류를 한 왕복으로.
_FIN_CLASS_SQL = ("SELECT (SELECT induty FROM dart_fundamentals WHERE ticker=%s), "
                  "(SELECT sector FROM wise_sector WHERE ticker=%s ORDER BY snap_dd DESC LIMIT 1)")


async def _financial_by_class(ticker: str | None) -> bool:
    """회사 개황(DART)의 업종코드가 비었을 때만 쓰는 금융·지주 판정(261019) — ① 재무 요약에 저장된 DART 업종코드(KSIC 64·65·66)
    ② 없으면 업종분류 최신 스냅샷의 대분류가 「금융」인가. 둘 다 모르면 False — 매출이 비었다는 것만으로 금융이라 하지 않는다
    (종전 폴백이 재무 없는 신규상장사를 「금융·지주」로 표시했다). 읽기 실패도 False(경고 한 줄의 문제라 응답을 막지 않는다)."""
    if not ticker:
        return False
    rows = await _db_rows(_FIN_CLASS_SQL, (ticker, ticker))
    if not rows:
        return False
    ind, cls = rows[0]
    ind = str(ind or "").strip()
    if ind and ind not in ("none", "err"):
        return ind[:2] in ("64", "65", "66")
    return cls == "금융"


async def _build_valuation_payload_impl(company: str, format: str = "md") -> dict[str, Any]:
    client = get_dart_client()
    query = (company or "").strip()
    if not query:
        return {"tool": "price_multiple_data", "status": "invalid", "subject": company,
                "warnings": ["회사명 또는 종목코드(6자리)를 입력하세요."]}
    corp, early = await _resolve_listed(query)   # 공용 리졸버(company 툴 방식) — ambiguous 후보표
    if early:
        return early
    if not corp:  # ERROR(비상장만·무매칭) → 기존 세분화 경로(unlisted/not_found + 커스텀 안내)
        corp = await client.lookup_corp_code(query)
    if not corp:
        return {"tool": "price_multiple_data", "status": "not_found", "subject": company,
                "next_actions": [COMPANY_LOOKUP_NEXT_ACTION],
                # 261019: 종전 「우선주는 보통주 종목코드로 조회한다」 — #114 부터 종류주 코드를 발행 회사로 받으므로 지금 동작대로 말한다.
                "warnings": [company_not_found_warning(company, listed_only=True), PREF_CODE_HINT]}
    cc, stock_code = corp["corp_code"], corp.get("stock_code")
    name = corp.get("corp_name", company)
    # 비상장 = 주가 없음 → 시장배수(PER·PBR) 정의 불가. DART 마스터엔 비상장 법인(삼성·쿠팡 등)도
    # 있어 resolve되므로 여기서 조기 차단(전부 None 산출·크래시 방지). 상장 동명 후보는 안내.
    if not stock_code:
        alts = [c for c in await client.lookup_corp_code_all(query) if c.get("stock_code")][:5]
        if not alts:  # exact-match 단락으로 빈 경우('삼성'→비상장 법인만) → 부분매치 상장 후보 별도 조회
            corps = await client._load_corp_codes()  # 실사용 QA P2: "삼성전자를 찾으셨나요?" 오도 방지
            cand = [c for c in corps if c.get("stock_code") and query in c["corp_name"]]
            if cand:  # 시총순 정렬 — 사용자가 의도했을 가능성이 큰 대형사(삼성전자)부터
                # 261018: 서빙 기준일(`krx_daily` 최신일) + 그날의 일별 저장분 — 기준일과 행을 같은 원천에서 읽는다. 시총만 고른다(등락 같은 창 칸은 안 고른다).
                latest = await _serving_price_dd()
                caps = {r[0]: r[1] for r in (await asyncio.to_thread(
                    _pg_rows, "SELECT ticker, mktcap FROM krx_daily_v WHERE trd_dd=%s "
                    "AND ticker = ANY(%s)", (latest, [c["stock_code"] for c in cand])) or [])} if latest else {}
                alts = sorted(cand, key=lambda c: -(caps.get(c["stock_code"]) or 0))[:5]
        alt_txt = ("  혹시 이 상장사를 찾으셨나요? " +
                   ", ".join(f"{c['corp_name']}({c['stock_code']})" for c in alts)) if alts else ""
        return {"tool": "price_multiple_data", "status": "unlisted", "subject": name,
                "warnings": [f"'{name}'은(는) 비상장 — 주가가 없어 시장배수(PER·PBR·배당수익률) 산출 불가. "
                             f"재무 펀더멘탈은 financial_metrics 사용.{alt_txt}"]}

    # ── 데이터 fetch 병렬화: 의존성 3단계 (P1 fy 무관 → P2 fy 의존 → P3 fs_used 의존) ──
    # P1: 재무요약(대형 ~7콜)·업종정보·시세 — 모두 cc/stock_code만 의존(fy 불필요) → 병렬.
    #     (이전엔 순차라 info·market이 fm 뒤에서 대기했음). stock_code는 위 unlisted 가드로 보장.
    fm, info, mk = await asyncio.gather(
        build_financial_metrics_payload(stock_code, scope="summary", year=0, consolidated=True),
        client.get_company_info(cc),
        _market_for(stock_code),
    )
    # 261018(재설계 6단계, 결산월): 회사 개황의 결산월이 12월이 아니면 재무 요약 표의 결산월(`acc_mt`)을 읽어 그것으로 최신 사업보고서와 그 뒤
    #   분기 칸을 고른다 — 곡선·업종 비교와 같은 값이다(결산월을 바꾼 회사·반기 결산 리츠는 그 칸이 12 = 종전 읽기. 개황은 「지금」 결산월이라
    #   저장된 칸과 어긋날 수 있다). 12월 결산은 읽기 하나 늘지 않고 종전 그대로다.
    # 261019: 개황이 12월이어도 재무 요약의 출처 사업보고서 이름이 12월 기준이 아니면(「사업보고서 (2025.06)」) 같은 칸을 읽는다 — 결산월을 12월로
    #   바꿨지만 새 결산월의 사업보고서가 아직 없는 회사(저장된 칸은 옛 결산월 — 10-19 운영 001080 6월·021880 3월)를 종전엔 12월로 읽어
    #   실시간 firm 만 FY0 가 한 해 낡았다(001080: firm FY2025 PER N/M·TTM 45.82, 같은 날 firm + as_of·업종 비교는 FY2026 116.51).
    #   보고서 이름은 재무 요약이 이미 받아 둔 값이라 12월 결산(이름이 12월 기준)은 여전히 읽기가 늘지 않는다.
    fye = _rc.Fiscal()
    _rep_m = _source_report_month(fm)
    if _rc.fye_month(info.get("acc_mt")) != 12 or (_rep_m is not None and _rep_m != 12):
        fye_row = await _db_rows("SELECT acc_mt FROM dart_fundamentals WHERE ticker=%s", (stock_code,))
        fye = _rc.fiscal(fye_row[0][0] if fye_row else None, stock_code)
    nondec = fye.month != 12
    fy = fm.get("data", {}).get("year")
    if nondec and fy is not None:
        # 12월 결산이 아니면 재무 요약의 기본 연도(달력 규칙: 4월 이후 전년)가 최신 사업보고서가 아닐 수 있다 — 3월 결산은 7월부터 그해 3월 말
        #   사업보고서가 나와 있다(종전엔 한 해 앞 사업보고서를 FY0 로, 그 뒤 분기를 다음 해 표기로 찾아 TTM 이 한 해 어긋났다).
        #   최신 사업보고서가 실제로 있을 때만 그 해로 바꾼다(늦게 낸 회사는 종전 연도 그대로).
        fy_a = _rc.latest_annual(today_kst(), fye)
        if fy_a != fy:
            fm_a = await build_financial_metrics_payload(stock_code, scope="summary", year=fy_a, consolidated=True)
            if (fm_a.get("data", {}).get("year") == fy_a
                    and (fm_a.get("data", {}).get("summary") or {}).get("reprt_code") == "11011"):
                fm, fy = fm_a, fy_a
    s = fm.get("data", {}).get("summary") or {}
    if fy is None:  # 상장사여도 재무 미확정(신규상장·SPAC 등) → fy+1 크래시 방지, 명확한 상태 반환
        return {"tool": "price_multiple_data", "status": "no_financials", "subject": name,
                "warnings": [f"'{name}'({stock_code}) 재무 데이터를 확정하지 못함 — 밸류에이션 산출 불가."]}
    eps_fy = s.get("eps_krw"); revenue_fy = s.get("revenue_krw"); roe = s.get("roe_pct")
    cap_status = s.get("capital_impairment_status")
    # 금융사 판별: KSIC 업종코드(induty) 대분류 K = 64(은행·금융지주)·65(보험)·66(증권).
    # 260704 실측: 매출=None 휴리스틱은 인터넷은행(카카오뱅크 영업수익 3조 신고)을 놓쳐 오분류 →
    # induty를 1차 신호로, 매출=None을 2차 폴백으로. 결과는 sector_class 와 해석 경고에 쓴다.
    # 261019: 「매출=None → 금융」 폴백을 뺐다. 재무가 아직 없는 회사(신규상장 — 10-19 운영 멜콘 업종코드 292 특수목적용 기계·
    #   글로벌테크놀로지)는 매출이 비어 있을 뿐인데 「금융·지주 업종」 경고가 붙었다. 업종코드가 있으면 그것만 본다. 회사 개황을 못 받아
    #   업종코드가 비었을 때만 저장된 업종코드(재무 요약) → 업종분류(대분류 「금융」) 순으로 본다(`_financial_by_class`).
    induty = str(info.get("induty_code") or "").strip()
    if induty:
        is_financial = induty[:2] in ("64", "65", "66")
    else:
        is_financial = await _financial_by_class(stock_code)

    # TTM(flow) = FY + 1Q(당해) − 1Q(전년); MRQ(stock) = 최근 분기 잔액
    q_cur = fy + 1  # 예: fy=2025 → 1Q2026 (전년 같은 칸 = q_cur − 1 → 1Q2025)
    # 261018(결산월): 사업보고서 fy 다음 회계연도의 분기 칸 표기 — 12월 결산이면 모두 fy+1(종전 그대로). 3월 결산이면 모두 fy(6·9·12월 말),
    #   6월 결산이면 1분기·반기는 fy(9·12월 말)·3분기는 fy+1(3월 말). 전년 같은 칸은 언제나 그 표기 − 1.
    q_lab = _rc.quarter_labels_after(fy, fye)
    # P2: 연간 재무원장·유통주식수·배당 — fy만 의존 → 병렬.
    (fy_rows, fs_used), sh, (div_sum, div_err) = await asyncio.gather(
        _acntall(client, cc, fy, "11011"),
        _shares_outstanding(client, cc, fy),
        _annual_summary(cc, fy),
    )
    # P3: 분기 재무원장 — 연간에서 확정한 fs(연결/별도) 강제(TTM 혼용 방지).
    #
    # 🔴 **여기가 「11013」(1분기)으로 못 박혀 있었다** (2026-08-30 U 6차 실측).
    #    그래서 반기보고서가 나온 8월 이후에도 TTM 이 **1분기 누적에 머물렀다**. 같은 날
    #    태광산업 TTM PER 이 firm 29.96 / 스냅샷 8.61 로 3.5배 갈린 이유가 이것이다 —
    #    스냅샷 배치는 최신 분기를 쓰고 이 경로만 한 분기 뒤처졌다. 검산:
    #      FY2025 808억 + 2026반기 868억 − 2025반기 489억 = **1,187억**(스냅샷과 일치)
    #      FY2025 808억 + 2026 1Q 283억 − 2025 1Q 750억 =   341억(이 경로가 내던 값)
    #    한 종목이 아니라 **전 종목**에 걸린 문제였다.
    #
    #    이제 **최신 분기부터 내려오며** 찾는다(3Q → 반기 → 1Q). 🔴 **당해와 전년은 반드시
    #    같은 분기여야 한다** — 다른 분기끼리 빼면 없는 실적을 만들어낸다. 그래서 당해에서
    #    고른 코드를 전년에 그대로 쓰고, 전년이 비면 그 코드를 버리고 다음 후보로 내려간다.
    _Q_CODES = ("11014", "11012", "11013")  # 3분기누적 · 반기누적 · 1분기누적
    qc_rows, qp_rows, q_code = [], [], "11013"
    _Q_OF = {"11013": 1, "11012": 2, "11014": 3}
    any_q_after = False
    for _rcode in _Q_CODES:
        _cur = q_lab[_Q_OF[_rcode]] if nondec else q_cur
        _qc, _ = await _acntall(client, cc, _cur, _rcode, fs_used)
        if not _qc:
            continue
        any_q_after = True
        _qp, _ = await _acntall(client, cc, _cur - 1, _rcode, fs_used)
        if not _qp:
            continue          # 짝이 없으면 뺄 수 없다 — 다음(더 이른) 분기로 내려간다
        qc_rows, qp_rows, q_code = _qc, _qp, _rcode
        if nondec:
            q_cur = _cur
        break
    # 261018(결산월): 12월 결산이 아니고 사업보고서 fy 뒤 분기 칸이 아직 하나도 없으면(최신 공시가 사업보고서) TTM = 그 사업보고서 연간 값 —
    #   곡선(`_ttm_ni` 의 사업보고서 칸)과 같은 정의. 6월 결산은 10/1~11/13 이 그렇다. 12월 결산은 종전 그대로(4~5월 TTM 비움).
    ttm_is_annual = nondec and not any_q_after
    if ttm_is_annual:
        q_code, q_cur = "11011", fy
    elif nondec and not qc_rows:
        q_cur = q_lab[1]      # 짝을 못 찾음 — 표기만 결산월 칸으로(종전 12월 결산의 fy+1 1분기와 같은 자리)

    # 고른 분기 칸의 기간 말(당해·전년 동기) — 수정계수 보정과 환율 환산이 함께 쓴다.
    # 🔴 결산기준일도 **고른 분기**를 따라가야 한다 — 분기를 최신으로 옮겨 놓고 날짜만
    #    0331 로 두면 그 사이의 액면분할·무상증자를 한 번 더 세거나 빠뜨린다.
    _QEND = {"11013": "0331", "11012": "0630", "11014": "0930"}
    _qe = _QEND.get(q_code, "0331")
    _d_cur, _d_prev = f"{fy + 1}{_qe}", f"{fy}{_qe}"
    if nondec:   # 261018(결산월): 고른 칸의 실제 기간 말(사업보고서가 최신이면 그 결산일)
        if ttm_is_annual:
            _lab, _q = fy, 4
        else:
            _q = _Q_OF.get(q_code, 1)
            _lab = q_cur if qc_rows else q_lab[_q]
        _d_cur = _rc.cell_end(_lab, _q, fye).strftime("%Y%m%d")
        _d_prev = _rc.cell_end(_lab - 1, _q, fye).strftime("%Y%m%d")

    # 통화 환산: 기능통화≠KRW(두산밥캣=USD 등)면 KRW 환산 — KRW 주가/시총과
    # 통화 일치시켜야 배수가 유효(미환산 시 환율배수만큼 왜곡: 두산밥캣 PBR 1,238 오탐). wiki §9.
    # 261019: **기간 말 환율을 기간마다** 쓴다 — 연간(FY0)은 그 사업연도 말, 분기 누적·자본(MRQ)은 그 분기 말, 전년 동기 누적은 그 분기 말.
    #   종전엔 모두 사업연도 말 환율 하나로 환산해 반기 자본까지 6개월 전 환율로 바꿨다(10-19 운영 두산밥캣 PBR 0.67 — 분기 말 환율이면 0.62,
    #   딥커머스 0.18 vs 0.16). 곡선·업종 비교(`_firm_fin_by_fy`·`_firm_fin_by_q` — 칸마다 그 기간 말 환율)와 같은 규칙이다.
    #   순이익의 기간 평균 환율 규칙은 저장소에 없다(`dart/fx.py` — v1.1) — 기간 말 근사이고 경고에 적는다.
    stmt_cur = statement_currency(fy_rows)
    ecos_fx_rate = 1.0            # 사업연도 말 환율(종전 이름 — 1.0 이면 환산 없음)
    fx_qc = fx_qp = 1.0           # 분기 칸(당해·전년 동기) 기간 말 환율
    fx_q_fallback = False         # 분기 말 환율을 못 받아 사업연도 말 환율로 대신했나
    fy_end_dd = None
    if stmt_cur != "KRW":
        # 261019: 칸을 결산월(`fye`)로 골랐으면 환산 기준일(그 사업연도 말)도 그 달 — 개황 결산월은 결산월을 바꾼 회사에서 칸과 어긋난다.
        acc_mt = f"{fye.month:02d}" if nondec else str(info.get("acc_mt") or "12").zfill(2)
        last_day = calendar.monthrange(fy, int(acc_mt))[1]
        fy_end_dd = f"{fy}{acc_mt}{last_day:02d}"
        if qc_rows and not ttm_is_annual:
            # 세 날짜가 서로 다르다(분기 칸은 1~3분기라 사업연도 말이 아니다) — 같은 날을 동시에 묻지 않으므로 함께 보낸다.
            r_fy, r_qc, r_qp = await asyncio.gather(fx_to_krw(stmt_cur, fy_end_dd), fx_to_krw(stmt_cur, _d_cur),
                                                    fx_to_krw(stmt_cur, _d_prev))
        else:
            r_fy, r_qc, r_qp = await fx_to_krw(stmt_cur, fy_end_dd), None, None
        ecos_fx_rate = r_fy or 1.0
        if qc_rows and not ttm_is_annual:
            fx_q_fallback = not (r_qc and r_qp)
            fx_qc, fx_qp = (r_qc or ecos_fx_rate), (r_qp or ecos_fx_rate)
        else:
            fx_qc = fx_qp = ecos_fx_rate

    def _fxr(x, rate):  # None 보존, 나머지는 KRW 환산(1.0이면 무변화)
        return round(x * rate) if x is not None else None

    def _fx(x):  # 사업연도(FY0) 값 — 사업연도 말 환율
        return _fxr(x, ecos_fx_rate)

    if ecos_fx_rate != 1.0:
        # revenue_fy 는 financial_metrics 요약에서 온다 — 그쪽이 이미 KRW 로 환산해 내보내므로
        # 여기서 다시 곱하면 환율이 두 번 곱해진다(두산밥캣 실측 8.87조 → 12,728조, ≈1,435²).
        # 아래 _fx()·_fxr() 는 이 함수가 **원행에서 직접 뽑은** 값(ni_*·eq_*·assets_fy…)에만 쓴다.
        eps_fy = None  # fm의 eps_krw 도 KRW 환산본 → 폐기, 아래서 공시 EPS×환율로 통일 조립

    # ── 공시 EPS 조립 (260705, [[per-pbr-data-points]] 전수조사 귀결) ──
    # 가중평균주식수는 어느 endpoint에도 없음 → 주식수를 직접 만들지 않고 공시 EPS끼리 조립:
    #   TTM EPS = FY0 EPS + 당해 분기누적 EPS(thstrm_add_amount) − 전년동기누적 EPS
    # → FY0·TTM 모두 공시 가중평균·우선주 배분 기준 = 분모 비대칭(현대차 29% 괴리·방향 왜곡) 근본 해소.
    eps_fy_disc = _eps_disclosed(fy_rows, "thstrm_amount")
    eps_qc_disc = _eps_disclosed(qc_rows, "thstrm_add_amount", "thstrm_amount")
    eps_qp_disc = _eps_disclosed(qp_rows, "thstrm_add_amount", "thstrm_amount")
    # 수정계수 보정(krx_adj_events, 코스닥 스윕 리노공업 실증): 기중 액면분할·무상증자·주식배당
    # 이후의 보고서는 EPS를 새 분모로 내지만 **그 이전에 제출된 분기보고서 EPS는 옛 분모 그대로**
    # (실무상 소급 재발행 없음 — 리노 1:5 분할 실측: 전년1Q 1,933은 분할 전, 연간 2,002는 분할 후).
    # → 각 조각을 '그 보고서 결산기준일 이후 발생한 조정성 이벤트' 누적 계수로 현재 기준에 정렬.
    #   리노 검산: 2,002 + 532 − 1,933×0.2 = 2,147 (균일분모 2,148과 정합).
    eps_adj = None  # 보정 발동 시 {"current": f, "prior_q": f} — 근거 투명(explain에 표시)
    shares_unadjusted = None  # 계수 누락 탐지 시 {"shares_ratio": r, "factor": f} — PER 무효화
    flag_rows: list | None = []   # 수정주가 flag — 아래 경고에서 쓴다 (읽기는 EPS 보정 읽기와 한 묶음)
    if stock_code and any(x is not None for x in (eps_fy_disc, eps_qc_disc, eps_qp_disc)):
        # 결산기준일(`_d_cur`·`_d_prev`)은 위에서 고른 분기 칸의 기간 말이다.
        # 261018(재설계 4단계 T5): 수정계수 둘 · 주식수 배율(읽기 2) · 수정주가 flag — 읽기 5번이 서로 입력이 필요 없는데 종전엔 차례로 기다렸다.
        #   한 묶음으로 보낸다(동시에 도는 읽기는 호출당 `_PG_FANOUT` 개까지 → 5번 차례 대기 = 2 왕복). 아래 보정 계산은 종전 순서 그대로다.
        f_cur, f_qp, r_cur, flag_rows = await _gather_db(
            _eps_adj_factor(stock_code, _d_cur),   # 연간·당해분기 결산기준일 이후
            _eps_adj_factor(stock_code, _d_prev),  # 전년동기 결산기준일 이후
            _shares_ratio(stock_code, _d_cur),
            _db_rows("SELECT flag, detail FROM krx_stock_flags WHERE ticker=%s", (stock_code,)),
        )
        if f_cur != 1.0 or f_qp != 1.0:
            eps_adj = {"current": round(f_cur, 6), "prior_q": round(f_qp, 6)}
        if eps_fy_disc is not None:
            eps_fy_disc *= f_cur
        if eps_qc_disc is not None:
            eps_qc_disc *= f_cur
        if eps_qp_disc is not None:
            eps_qp_disc *= f_qp
        # 계수 누락 탐지 — 조용히 틀린 값을 내느니 N/M 을 낸다
        if r_cur and not (_ADJ_INVARIANT_LO <= f_cur * r_cur <= _ADJ_INVARIANT_HI):
            shares_unadjusted = {"shares_ratio": round(r_cur, 4), "factor": round(f_cur, 4)}
    elif stock_code:
        flag_rows = await _db_rows("SELECT flag, detail FROM krx_stock_flags WHERE ticker=%s", (stock_code,))
    eps_ttm_disc = (eps_fy_disc + eps_qc_disc - eps_qp_disc) \
        if None not in (eps_fy_disc, eps_qc_disc, eps_qp_disc) else None
    if ttm_is_annual:                  # 261018(결산월): 최신 공시가 사업보고서 — TTM = 그 연간 값(12월 결산은 해당 없음)
        eps_ttm_disc = eps_fy_disc

    ni_fy = _fx(_ctrl_ni(fy_rows))
    ni_qc = _fxr(_ctrl_ni_cum(qc_rows), fx_qc)     # 261019: 분기 칸은 그 분기 말 환율
    ni_qp = _fxr(_ctrl_ni_cum(qp_rows), fx_qp)
    ni_ttm = (ni_fy + ni_qc - ni_qp) if None not in (ni_fy, ni_qc, ni_qp) else None
    if ttm_is_annual:
        ni_ttm = ni_fy
    eq_mrq = _fxr(_ctrl_equity(qc_rows), fx_qc)
    eq_fy = _fx(_ctrl_equity(fy_rows))
    ctrl_equity = eq_mrq if eq_mrq is not None else eq_fy  # MRQ 우선, 미공시시 FY0
    equity_basis = "MRQ" if eq_mrq is not None else "FY0"

    shares_total = sh.get("total")        # 합계(보통+우선) — BPS 분모 (sh = P2 병렬 fetch)
    shares_common = sh.get("common") or shares_total  # 보통주 — EPS 분모(스펙 P1)
    price = mk.get("price")               # mk = P1 병렬 fetch

    # ── 실시간 스케일 오류 가드 (소프트센 032680 사례, wiki §9) ──
    # hard 등급 = ②(항등식)·③(시장최댓값 배수). soft = ①(배수점프, 실측 오탐 97.5%)·④(시총비율).
    # ★개별 종목 조회에서는 값을 무효화(N/M)하지 않고 그대로 노출 + 강한 경고만 부착 — 이 tool의
    #  철학("배수·인풋·가정 모두 노출, 판단은 사용자")과 자본잠식 처리(값 유지+경고)에 일관.
    #  (기계가 합산하는 시장 aggregate = market_val_series·market_fund_quarterly 수집에서는 반대로 무효화 — 경고문이
    #   합산 연산에 무력하므로. 소비 맥락이 다르면 처리도 다르다.)
    # 스케일가드용 값도 KRW 환산(_fx) — market_max 앵커가 KRW(44조)이므로 통화 일치 필수.
    ni_fy_frmtrm = _fx(_ctrl_ni(fy_rows, "frmtrm_amount"))  # 지배순이익 부재사도 폴백 일관 적용
    assets_fy = _fx(_gid(fy_rows, "Assets", ("BS",)))
    liab_fy = _fx(_gid(fy_rows, "Liabilities", ("BS",)))
    # 항등식(자산=부채+자본)은 반드시 총자본(지배+비지배지분) 기준 — 지배자본(eq_fy)만 쓰면
    # 비지배지분만큼 항상 어긋남(실측 발견: 삼성전자 비지배지분 12조 → 2.12% 오탐).
    eq_total_fy = _fx(_gid(fy_rows, "Equity", ("BS",)))
    scale_verdict = scale_assess(
        thstrm=ni_fy, frmtrm=ni_fy_frmtrm, assets=assets_fy, liabilities=liab_fy,
        equity=eq_total_fy, mktcap=mk.get("common_mktcap"), market_max=MARKET_MAX_NI_ANCHOR,
    )

    # 주식수 sanity: DART 유통 > KRX 상장×3 = 파싱오류(LS에코 ×1e6) → 무효화 (우선주 감안 여유 ×3)
    list_shrs = mk.get("list_shrs")
    shares_bad = bool(list_shrs and shares_total and shares_total > list_shrs * 3)
    if shares_bad:
        shares_total = shares_common = None

    # DPS = alotMatter 보통주 결의 현금배당금 (이미 주당값 — 주식수 불필요). div_sum = P2 병렬 fetch.
    dps = (div_sum or {}).get("cash_dps") or None
    # 261018: 조회 **실패**도 dps=None 이라 무배당과 구분이 안 됐다(실패 사유를 버렸다) → explain 이 DART 장애 때도 「없음(무배당 또는 미공시)」.
    dps_fetch_failed = _dps_fetch_failed(div_err)
    # 261019: 반기·분기 결산(리츠 등)은 사업연도 하나에 결산기가 여럿이다 — 종전 `cash_dps` 는 그 가운데 **마지막 한 결산기**라
    #   배당수익률이 연 배당 기준의 절반(반기)·4분의 1(분기)로 나갔다(10-19 운영: 롯데리츠 3.01%·SK리츠 1.24%·신한알파리츠 3.25%).
    #   결산기가 둘 이상 보이면 다음 사업연도 사업보고서(이미 나온 더 늦은 결산기)도 읽어 **최근 12개월 결산기 DPS 합**을 쓴다(`trailing_period_dps`).
    #   창 첫 결산기의 앞 결산기가 없어 주기를 확인하지 못하면(다음 해 결산기가 아직 없을 때) 앞 사업연도도 읽는다 — 결산기 둘만으로는
    #   6개월짜리인지(반기 결산) 12개월짜리인지(결산월 변경 이행기) 모른다. 12월 결산(결산기 하나)은 읽기가 늘지 않고 종전 그대로다.
    #   주기가 고르지 않으면(결산월 변경 이행기) 종전 값.
    dps_periods = None
    dps_next_failed = False
    _per = period_dps((div_sum or {}).get("items"))
    if len(_per) > 1:
        if fy + 1 <= today_kst().year:
            nxt, nxt_err = await _annual_summary(cc, fy + 1)
            dps_next_failed = _dps_fetch_failed(nxt_err)
            _per.update(period_dps((nxt or {}).get("items")))
        dps_periods = trailing_period_dps(_per)
        if dps_periods is None:
            prv, _ = await _annual_summary(cc, fy - 1)
            _prev = period_dps((prv or {}).get("items"))
            if _prev:
                dps_periods = trailing_period_dps({**_prev, **_per})
        if dps_periods:
            dps = dps_periods["dps"] or None

    bps = round(_div(ctrl_equity, shares_total)) if (ctrl_equity and shares_total) else None
    # EPS(FY0): 공시값 우선 — fy_rows 직접(비KRW는 ×환율) → fm(eps_krw) → 지배순이익÷보통주 폴백.
    if eps_fy_disc is not None:
        eps_fy = _fx(eps_fy_disc) if ecos_fx_rate != 1.0 else round(eps_fy_disc)
    if eps_fy is None and ni_fy is not None and shares_common:
        eps_fy = round(_div(ni_fy, shares_common))
    # EPS(TTM): 공시 EPS 조립(FY0과 같은 기준) 우선 → 조각 결측 시 지배순이익÷보통주 폴백(비대칭).
    if eps_ttm_disc is not None:
        if ecos_fx_rate != 1.0 and not ttm_is_annual:
            # 261019: 조각마다 그 기간 말 환율(지배순이익 TTM 과 같은 규칙) — 종전엔 USD 로 조립한 뒤 사업연도 말 환율 하나를 곱했다.
            eps_ttm = round(eps_fy_disc * ecos_fx_rate + eps_qc_disc * fx_qc - eps_qp_disc * fx_qp)
        else:
            eps_ttm = _fx(eps_ttm_disc) if ecos_fx_rate != 1.0 else round(eps_ttm_disc)
        eps_ttm_basis = "disclosed_assembled"
    elif ni_ttm and shares_common:
        eps_ttm = round(_div(ni_ttm, shares_common))
        eps_ttm_basis = "ni_div_shares_fallback"
    else:
        eps_ttm, eps_ttm_basis = None, None

    # ── 가드: 자본잠식·적자·섹터 ──
    impaired_full = cap_status == "full"
    def nm(x, denom_ok):  # 분모≤0 or 완전자본잠식 → N/M
        return _round_mult(x, 2) if (x is not None and denom_ok and not impaired_full) else None
    # ── 배수는 **시총 기반**이다 (260823 전환) ─────────────────────────────────
    # 종전: PER = 주가 ÷ EPS · PBR = 주가 ÷ BPS — 둘 다 분모에 주식수가 들어간다.
    #   그래서 액면분할·병합이 나면 옛 주식수 기준 EPS 와 새 주가가 섞여 배수가 통째로 틀렸고
    #   (메이슨캐피탈 실측: 적자인데 PER 32.31), 이를 막으려면 수정계수 파이프라인이
    #   상시 최신이어야 했다. 실측 4.1%(116/2,797)가 계수 부재로 N/M 이 되고 있었다.
    #
    # 이제: PER = 보통주 시총 ÷ 지배순이익 · PBR = 보통주 시총 ÷ 지배자본.
    #   분자·분모에서 주식수가 상쇄되므로 **조정성 이벤트에 불변**이다 — 계수가 필요 없다.
    #   덤으로 scope=market/sector/firm_history 스냅샷과 **방법론이 같아진다**(종전에는
    #   같은 `per_ttm` 이름으로 서로 다른 정의가 나갔다).
    #
    # 대가 두 가지 — 출력에 명시한다:
    #   ① 가중평균이 아니다. 공시 EPS 는 기중 주식수 변동을 가중평균으로 반영한 회사 공식값인데,
    #      시총은 오늘 주식수만 본다. 연중 유상증자한 회사는 두 값이 벌어진다.
    #   ② 우선주 편향. 분자는 보통주 시총인데 분모(지배순이익·지배자본)에는 우선주 몫이 포함돼
    #      배수가 소폭 낮게 나온다. 클래스별 이익·자본 분리는 공시 부재로 불가.
    # EPS·BPS 는 인풋으로 계속 노출한다 — 회사 공식값이라 대조에 쓴다.
    cap = mk.get("common_mktcap")
    per_fy = nm(_div(cap, ni_fy), cap and ni_fy is not None and ni_fy > 0)
    per_ttm = nm(_div(cap, ni_ttm), cap and ni_ttm is not None and ni_ttm > 0)
    pbr = nm(_div(cap, ctrl_equity), cap and ctrl_equity is not None and ctrl_equity > 0)
    div_yield = round(_div(dps, price) * 100, 2) if (dps and price and not impaired_full) else None

    warnings = []
    if impaired_full:
        warnings.append("⚠️ 완전자본잠식(자본≤0) — 상장폐지 위험. PER·PBR N/M. risk_events 확인 요망.")
    elif cap_status == "partial_50plus":
        warnings.append("⚠️ 자본잠식 50%↑ — 관리종목 위험.")
    elif cap_status == "partial":
        warnings.append("자본잠식 진행 중.")
    if eps_fy is not None and eps_fy <= 0:   # None(파싱실패)을 '적자'로 오표기 금지 (패널 P1)
        warnings.append("적자(FY0 EPS≤0) — PER N/M.")
    if shares_unadjusted:
        r = shares_unadjusted["shares_ratio"]
        # 260823: 배수가 시총 기반으로 바뀌어 **PER·PBR 은 이 영향을 받지 않는다.**
        #   다만 아래 EPS 는 공시 조각 조립이라 여전히 섞여 있으므로 인풋으로만 읽어야 한다.
        warnings.append(
            f"상장주식수가 결산기준일 이후 {r}배로 바뀌었습니다(액면분할·병합 추정) — "
            "PER·PBR 은 시총 기반이라 영향 없지만, 아래 **EPS는 옛 분모와 새 분모가 섞여 있어** "
            "주당 비교에는 쓰지 마세요.")
    elif shares_bad:
        # 260823: 종전에는 이 문구가 계수 누락 케이스까지 덮어 **원인을 오진**했다
        # (액면병합인데 「DART 파싱오류」라고 안내). 위 분기가 그 경우를 먼저 가져간다.
        warnings.append("⚠️ 유통주식수 이상(상장주식수 초과) — DART 파싱오류 의심, PBR/EPS 무효화. 확인 요망.")
    # 극단 배수 plausibility (두산밥캣류 단위 오독 방어 — 값은 내되 경고)
    if (pbr and pbr > 100) or (per_fy and per_fy > 500) or (per_ttm and per_ttm > 500):
        warnings.append("⚠️ 배수 비정상 고값 — 재무 단위/스케일 오류 가능(예: 지배자본 과소). 원문 확인 요망.")
    if is_financial:
        # 260823: 종전 문구가 EV/EBITDA·PSR·FCF 를 「산출 제외」라 안내했는데 **애초에 만들지
        #   않는 지표**였다. 없는 것을 뺐다고 말하면 있는 줄 안다. 해석 주의만 남긴다.
        warnings.append("금융·지주 업종 — 자산·부채 구조가 달라 PER 보다 PBR 이 비교에 낫습니다. 레버리지가 사업모델이라 부채비율도 일반 업종과 같은 잣대로 보면 안 됩니다.")
    if ecos_fx_rate != 1.0:
        # 261019: 기간마다 그 기간 말 환율 — 어느 값에 어느 환율을 썼는지 적는다(분기 칸이 없으면 사업연도 말 하나).
        _dd = lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}"
        if qc_rows and not ttm_is_annual:
            _rates = (f"연간(FY0)은 {fy} 사업연도 말({_dd(fy_end_dd)}) {ecos_fx_rate:,.1f}원 · 최근 분기 누적·자본(MRQ)은 {_dd(_d_cur)} "
                      f"{fx_qc:,.1f}원 · 전년 동기 누적은 {_dd(_d_prev)} {fx_qp:,.1f}원")
        else:
            _rates = f"{fy} 사업연도 말({_dd(fy_end_dd)}) {ecos_fx_rate:,.1f}원"
        warnings.append(f"기능통화 {stmt_cur} — 재무를 기간 말 환율(원/{stmt_cur})로 KRW 환산: {_rates}. 순이익은 원칙상 기간 평균 환율이라 "
                        "기간 말 근사는 수% 오차가 있다. 곡선·업종 비교와 같은 규칙이고, KRW 시총과 통화가 맞는다.")
        if fx_q_fallback:
            warnings.append(f"⚠️ 분기 말 환율 조회 실패 — 분기 값도 {fy} 사업연도 말 환율로 환산했다(분기 사이 환율 변동만큼 TTM·MRQ 오차).")
    elif stmt_cur != "KRW":
        warnings.append(f"⚠️ 기능통화 {stmt_cur}인데 환율 조회 실패 — 배수 통화 불일치 가능, 원문 확인 요망.")
    if scale_verdict and scale_verdict["tier"] == "hard":
        warnings.append(f"🚨 DART 재무 단위(스케일) 오류 강하게 의심({scale_verdict['hard_hit']}) — 아래 순이익·자본·배수는 **원문 그대로**이며 신뢰 불가. 반드시 원문 확인 후 사용. (예: 소프트센 032680 100만배 오류)")
    elif scale_verdict and scale_verdict["tier"] == "soft":
        warnings.append(f"재무 비율 이상치({scale_verdict['soft_hit']}) — 값은 정상일 수 있음(원샷 이익·자산매각·적자흑자 전환 등). 참고용 플래그.")
    # 조립 EPS sanity(재무 QA 260705, 이오플로우 실증): 기중 주식수 급변(대규모 유증·감자) 시
    # 서로 다른 가중평균 분모의 EPS를 가감하는 구조적 한계. 단 우선주 배분·가중평균의 '구조적' 괴리
    # (현대차 22% — 정상)는 FY0·TTM 양쪽에 동일하게 나타나므로, **괴리 비율의 변화**(FY0 대비 TTM)와
    # 부호 불일치만 경고 — 상시 발동 노이즈 방지.
    if eps_ttm_basis == "disclosed_assembled" and eps_ttm is not None and ni_ttm and shares_common:
        uni_ttm = _div(ni_ttm, shares_common)
        uni_fy = _div(ni_fy, shares_common) if ni_fy is not None else None
        sign_flip = eps_ttm * ni_ttm < 0
        shift = None
        if uni_ttm and uni_fy and eps_fy:
            r_ttm, r_fy = eps_ttm / uni_ttm, eps_fy / uni_fy
            if r_fy:
                shift = abs(r_ttm / r_fy - 1)
        if sign_flip or (shift is not None and shift > 0.15):
            warnings.append(
                "⚠️ TTM EPS 조립값의 정합 이상 — 기중 주식수 급변(대규모 유증·감자·전환) 시 서로 다른 "
                f"가중평균 분모의 공시 EPS를 가감하는 알려진 한계"
                f"({'순이익과 부호 불일치' if sign_flip else f'FY0 대비 괴리 변화 {shift*100:.0f}%'}). "
                "TTM 배수 해석 주의.")
    # EPS 비대칭 경고 — TTM이 폴백(지배NI÷보통주)일 때만: FY0(공시 가중평균)과 기준이 달라
    # 괴리 >10%면 방향 왜곡 가능(현대차 29% 실증). 공시 조립(disclosed_assembled)이면 대칭 — 경고 불필요.
    if eps_ttm_basis == "ni_div_shares_fallback" and eps_fy and ni_fy is not None and shares_common:
        eps_calc = _div(ni_fy, shares_common)
        if eps_calc and abs(eps_calc - eps_fy) / abs(eps_fy) > 0.10:
            warnings.append(
                f"⚠️ EPS(TTM)이 폴백 계산(지배순이익÷보통주) — 공시 EPS(FY0 {eps_fy:,}원)와 기준 괴리 "
                f"{abs(eps_calc-eps_fy)/abs(eps_fy)*100:.0f}%(가중평균·우선주 배분 차이). "
                "FY0·TTM PER의 증감 방향 비교 주의.")
    # 수정주가 파이프라인 flag — 분할·미해결 조정 종목은 배수 해석 주의(재무 QA: 삼바 분할 실증).
    if stock_code:
        for fl, _detail in flag_rows or []:
            if fl == "spinoff_break":
                warnings.append("⚠️ 인적분할 이력 — 분할 전후 재무·주식수 불연속으로 FY0 배수(특히 공시 "
                                "EPS의 가중평균 주식수)가 왜곡될 수 있음. TTM·MRQ 중심 해석 권장.")
            elif fl == "unresolved_adjustment":
                warnings.append("⚠️ 미해결 주가조정 이력 — 과거 가격 비교 시 주의.")
    if dps_fetch_failed:
        warnings.append("DART 배당 사항 조회 실패 — DPS·배당수익률 미확인(무배당이라는 뜻이 아니다, 일시 장애 가능). PER·PBR 값은 그대로다.")
    if dps_periods:
        _pp = " + ".join(f"{d[:4]}-{d[4:6]} {v:,}원" for d, v in dps_periods["periods"])
        warnings.append(f"{'반기' if dps_periods['months'] == 6 else '분기'} 결산 회사 — DPS 는 최근 12개월 결산기 {len(dps_periods['periods'])}개의 "
                        f"보통주 주당 현금배당 합({_pp} = {dps_periods['dps']:,}원)이다. 결산기마다 사업보고서가 따로 나와 한 결산기만 쓰면 "
                        "연 배당의 일부만 잡힌다.")
        if dps_next_failed:
            warnings.append("다음 사업연도 배당 사항 조회 실패 — 가장 늦은 결산기가 빠졌을 수 있다(DPS 는 읽은 결산기까지의 최근 12개월 합).")
    if mk.get("date"):
        warnings.append(f"주가 기준일 {mk['date']} 종가 {price:,}원 (KRX).")
    if _fye_note(fye):                 # 261018(결산월): 12월 결산이 아니면 칸 표기 읽는 법(12월 결산은 종전 응답 그대로)
        warnings.append(_fye_note(fye))

    payload = {
        "tool": "price_multiple_data", "status": "ok", "subject": name,
        "data": {
            "company_id": _company_id(corp),
            "identifiers": {"ticker": stock_code, "corp_code": cc},
            "sector_class": "financial" if is_financial else "general",
            "fiscal_year": fy, "price_krw": price, "price_date": mk.get("date"),
            # 261018: 주가를 어디서 받았나 — "db"(일별 시세 저장분) / "krx_live"(저장분에서 못 받음 → KRX 직접) / None(못 받음).
            #   `scope="explain"` 의 주가 인풋 줄이 이것으로 출처를 고른다(종전엔 라이브 폴백이어도 「저장분 서빙」이라 적었다).
            "price_source": mk.get("source"),
            "multiples": {
                "per_fy0": per_fy, "per_ttm": per_ttm,
                "pbr_mrq": pbr, "pbr_basis": equity_basis,
                # 260823: 주가÷EPS 에서 전환. 스냅샷(market/sector/firm_history)과 같은 정의.
                "multiples_basis": "common_mktcap_over_controlling_income",
                "dividend_yield_pct": div_yield,
            },
            "inputs": {
                "eps_fy0_krw": eps_fy, "eps_ttm_krw": eps_ttm, "eps_ttm_basis": eps_ttm_basis,
                "eps_adj_factors": eps_adj,  # 분할·무상증자 수정계수 보정(v3) — 미발동 시 None
                "shares_unadjusted": shares_unadjusted,  # 계수 누락 탐지 — 있으면 PER 무효(N/M)
                "bps_krw": bps, "roe_pct": roe,
                "net_income_fy0_krw": ni_fy, "net_income_ttm_krw": ni_ttm,
                # TTM 이 **어느 분기까지** 반영한 것인지. 이 값이 없어서 한 분기 뒤처진
                # 것을 아무도 못 봤다(2026-08-30). 기준은 값에 붙어 다녀야 한다.
                "ttm_quarter": {"11013": "1분기 누적", "11012": "반기 누적", "11011": "사업보고서(연간)",
                                "11014": "3분기 누적"}.get(q_code, q_code),
                "ttm_quarter_year": q_cur,
                "controlling_equity_krw": ctrl_equity,
                "shares_common": shares_common, "shares_total": shares_total,
                # 261018: dps_krw=None 의 뜻을 가른다 — True = DART 배당 사항 조회 실패(값 미확인) / False = 조회는 됐고 보통주 DPS 가 없음(무배당 또는 미공시).
                "dps_krw": dps, "dps_fetch_failed": dps_fetch_failed, "revenue_fy0_krw": revenue_fy,
                "common_market_cap_krw": mk.get("common_mktcap"),
                "capital_impairment_status": cap_status,
                "functional_currency": stmt_cur,
                "fx_rate_to_krw": ecos_fx_rate if ecos_fx_rate != 1.0 else None,   # 사업연도(FY0) 말 환율 — 종전 칸 그대로
            },
            "warnings": warnings,
            "data_quality": {
                "scale_tier": scale_verdict["tier"],          # hard=강한 오류의심 / soft=참고 / clean
                "scale_flags": scale_verdict["hard_hit"] + scale_verdict["soft_hit"],
                "values_masked": False,  # 개별조회는 값 무효화 안 함(집계 tool과 반대) — 판단은 사용자
            },
            "note": "EPS(FY0·TTM 모두)=DART 공시 기본주당이익 기준(TTM=공시 EPS 조립: FY0+분기누적−전년동기누적 "
                    "— 가중평균 주식수·우선주 배분 반영, 두 PER 직접비교 가능. 클래스별 EPS 미공시사(삼성전자 등)는 "
                    "보·우 합산 가중평균 = 국내 포털·데이터 공급처 관행과 동일). 공시 EPS 결측 시에만 "
                    "지배순이익÷보통주 폴백(경고 부착). "
                    "PER·PBR=보통주 시총(KRX 상장주식수×종가)÷지배순이익·지배자본(MRQ 우선) — 주식수 상쇄로 분할·병합에 불변, 분모에 우선주 몫이 포함돼 소폭 낮을 수 있음. BPS 분모=합계 유통주식수(보통+우선, 자기주식 제외). "
                    "배수는 trailing(과거 실적) 기준 — 컨센서스 선행(fwd) PER와 상이.",
        },
    }
    inputs = payload["data"]["inputs"]
    if dps_periods:
        # 261019: 반기·분기 결산이면 DPS 가 어느 결산기들의 합인지 — 그런 회사에서만 칸이 생긴다(그 밖엔 사업보고서 한 결산기 그대로).
        inputs["dps_basis"] = {"rule": "trailing_12m_periods", "period_months": dps_periods["months"],
                               "periods": [{"stlm_dt": f"{d[:4]}-{d[4:6]}-{d[6:]}", "dps_krw": v} for d, v in dps_periods["periods"]]}
    if ecos_fx_rate != 1.0:
        # 261019: 값마다 쓴 기간 말 환율(분기 칸이 없으면 FY0 하나) — 외화 보고사에서만 칸이 생긴다.
        inputs["fx_rates_to_krw"] = {"fy0": {"date": fy_end_dd, "rate": ecos_fx_rate},
                                     **({"quarter": {"date": _d_cur, "rate": fx_qc},
                                         "prior_quarter": {"date": _d_prev, "rate": fx_qp}}
                                        if qc_rows and not ttm_is_annual else {})}
    if format == "md":
        payload["markdown"] = _render_md(payload)
    return payload


def _render_md(p: dict[str, Any]) -> str:
    d = p["data"]; m = d["multiples"]; i = d["inputs"]
    def g(x, suf="", fmt="{}"):
        return (fmt.format(x) + suf) if x is not None else "N/M"
    lines = [
        f"# {p['subject']} 밸류에이션 ({d['fiscal_year']} 재무 · 주가 {g(d['price_krw'],'원','{:,}')})",
        "",
        "## 배수",
        f"- PER {g(m['per_fy0'])}(FY0) / {g(m['per_ttm'])}(TTM) · PBR {g(m['pbr_mrq'])}({m['pbr_basis']}) · 배당수익률 {g(m['dividend_yield_pct'],'%')}",
        "",
        "## 인풋 (근거 투명)",
        f"- EPS {g(i['eps_fy0_krw'],'','{:,}')}(FY0)/{g(i['eps_ttm_krw'],'','{:,}')}(TTM) · BPS {g(i['bps_krw'],'','{:,}')} · ROE {g(i['roe_pct'],'%')} · DPS {g(i['dps_krw'],'','{:,}')}",
        f"- TTM 기준: {i.get('ttm_quarter_year')}년 {i.get('ttm_quarter','-')} "
        f"(= {d['fiscal_year']} 연간 + 당해 누적 − 전년 동기 누적)",
        f"- 지배순이익 {g(i['net_income_fy0_krw'],'','{:,}')}(FY0)/{g(i['net_income_ttm_krw'],'','{:,}')}(TTM) · 지배자본 {g(i['controlling_equity_krw'],'','{:,}')} · 유통주식 보통 {g(i['shares_common'],'','{:,}')}/합계 {g(i['shares_total'],'','{:,}')}",
        f"- 보통주 시총 {g(i['common_market_cap_krw'],'','{:,}')} (업종구분: {'금융·지주' if d['sector_class']=='financial' else '일반(비금융)'})",
    ]
    # 🔴 **두 주식수를 한 화면에 나란히 두고 무엇이 다른지 안 밝히면 틀린 것처럼 보인다**
    #    (2026-08-30 U 6차 실측 태광산업 — 「시총 1조 221억 ÷ 주가 918,000원 = 111만 주인데
    #    표기 유통주식은 841,631주, 27만 주 어긋난다」). 계산은 맞다. 시총은 **상장주식수**
    #    (자기주식 포함, KRX 관행)이고 표기 유통주식은 **자기주식 제외**다. 자기주식 비중이
    #    큰 회사(태광산업 24.4%)일수록 벌어진다. 값을 고치지 않고 **차이를 드러낸다.**
    _cap, _px, _sc = i.get("common_market_cap_krw"), d.get("price_krw"), i.get("shares_common")
    if _cap and _px and _sc:
        _listed = round(_cap / _px)
        if abs(_listed - _sc) > max(1, _sc * 0.005):
            lines.append(
                f"  - ⓘ 시총 ÷ 주가 = {_listed:,}주로 위 유통주식({_sc:,}주)과 {_listed - _sc:,}주 "
                f"다릅니다 — **시총은 상장주식수(자기주식 포함, KRX 관행), 유통주식은 자기주식 "
                f"제외**라서입니다. 둘 다 맞는 값이며 배수 분자는 시총 쪽입니다. "
                f"자기주식 규모는 `treasury_share` 로 확인하십시오")
    # 봉투(payload["warnings"])와 데이터(data["warnings"])를 **둘 다** 싣는다.
    # 「이 회사가 맞나」를 묻는 추정 경고는 declare_weak_resolution 이 봉투에 다는데,
    # 종전에는 데이터 쪽만 읽어 통째로 버렸다 — 「현대」를 물으면 28곳 중 하나를 고른 사실이
    # 아무 표시 없이 완결된 밸류에이션으로 나갔다. 봉투를 앞에 둔다(분석 전체의 전제라서).
    _seen: set[str] = set()
    _warns = [w for w in list(p.get("warnings") or []) + list(d.get("warnings") or [])
              if not (w in _seen or _seen.add(w))]
    if _warns:
        lines += ["", "## 주의"] + [f"- {w}" for w in _warns]
    # note(방법론 고지)를 md에도 렌더 — json에만 있으면 기본(md) 사용자가 핵심 고지를 못 봄(재무 QA HIGH)
    if d.get("note"):
        lines += ["", f"> {d['note']}"]
    lines += ["> 시장·섹터 대비 비교는 scope='market'/'sector' — 스냅샷 배수(총시총 기준)는 본 값과 다를 수 있음. "
              "수치 근거·계산 과정은 scope='explain'."]
    return "\n".join(lines)


async def build_valuation_payload(*args, **kwargs):
    """이름이 정확히 맞지 않아 추정으로 고른 기업을 응답에 밝힌다.

    이 서비스는 `ToolEnvelope` 를 쓰지 않고 dict 를 직접 만들어 return 이 여러 곳에
    흩어져 있다 — 진입점 하나만 감싸 두면 새 return 이 늘어도 전파가 끊기지 않는다.
    """
    payload = declare_weak_resolution(await _build_valuation_payload_impl(*args, **kwargs))
    # **순서 결함 방어.** impl 은 md 를 자기 안에서 먼저 찍고(`payload["markdown"] = _render_md(...)`),
    # 추정 고지는 그 바깥에서 붙는다. 그래서 md 사용자만 「이 회사가 맞나」를 못 봤다 —
    # json 사용자는 봉투에서 봤다. 경고를 붙인 뒤 firm md 만 다시 찍는다.
    if isinstance(payload, dict) and payload.get("markdown") and "multiples" in (payload.get("data") or {}):
        payload["markdown"] = _render_md(payload)
    return payload
