"""OpenDART API 클라이언트 — API 호출을 한 곳에서 관리

⚠️ DART 접근 시 주의사항 — **한도는 API 와 웹이 다르다**:
  - OpenDART API: 한도가 **키마다** 걸린다. 분당 1,000회를 넘기면 그 키가 막힌다
    (실측 2~3시간). 일일 한도는 키당 4만회이지만, 먼저 닿는 건 거의 언제나 분당 쪽이다.
    사용자 키는 요청마다 다르므로 한 사용자가 다른 사용자를 막지는 않는다 —
    위험한 건 **우리 자신의 키**(배치·스크립트)다.
  - DART 웹 스크래핑(원문·KIND): 위 한도와 **별개**이고 공표된 수치도 차단 기준도 없다.
    「한도가 없다」가 아니라 **「한도를 모른다」**이므로 수치가 아니라 예의로 다룬다.
  - 최소 간격 강제: API 0.066초(=분당 910), 웹 2초.
  - 배치 작업 시 CLAUDE.md의 "OpenDART API 한도" 절 반드시 참조.
"""

import os
import gzip
import io
import re
import sys
import json
import time
import asyncio
import collections
import logging
import sqlite3
import tempfile
import threading
import zipfile
import xml.etree.ElementTree as ET
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from html import unescape
from pathlib import Path
import httpx
from dotenv import load_dotenv

load_dotenv()

# ── 요청별 API 키 (URL 쿼리 파라미터 → contextvar) ──
_ctx_opendart_key: ContextVar[str | None] = ContextVar("opendart_key", default=None)

# ── 요청 장부 (per-request 계측) ──
# **ContextVar 는 아래로만 흐른다.** 자식 task 는 부모 문맥의 *사본*을 받으므로, 하류에서
# `.set()` 해도 부모(ASGI 미들웨어)는 영원히 못 본다. 260804 실측: 종전 `_ctx_doc_cache_hit`
# 이 이 방식이었고 266,615건 **전부 NULL** 이었다 — 같은 줄에서 기록되는 response_bytes 는
# 정상이었는데(그건 미들웨어가 제 손으로 센 값이다) 이것만 한 번도 안 들어왔다.
#
# 그래서 값을 올려보내지 않는다. **미들웨어가 빈 장부를 먼저 만들어 내려보내고**, 하류는
# 그 장부를 고치기만 한다. 사본이 전달돼도 가리키는 dict 는 하나라 변경이 위에서도 보인다.
#
# 겸사겸사 종전 boolean 의 두 결함도 사라진다 —
#   ① 문서를 10건 받아도 마지막 1건만 남던 것 → 건수로 센다.
#   ② 디스크 적중을 메모리 적중과 뭉뚱그리던 것 → 나눠 센다(예산은 **메모리** 것이므로
#      섞으면 캐시 크기 판단이 왜곡된다).
_ctx_ledger: ContextVar[dict | None] = ContextVar("request_ledger", default=None)


def new_request_ledger() -> dict:
    """요청 시작 시 **미들웨어가** 만든다. 하류는 만들지 않고 고치기만 한다."""
    ledger: dict = {
        "doc_mem_hits": 0, "doc_disk_hits": 0, "doc_misses": 0,
        # 폴백 경로를 따로 센다. 주 경로(document.xml API)는 doc_misses 가 이미 세므로
        # 여기 없는 것이 정상이다. web_wait_ms 는 웹 스로틀에서 **실제로 잠든** 시간(ms).
        "fetch_viewer": 0, "fetch_kind": 0, "web_wait_ms": 0,
        "corp_codes": [], "weak_resolutions": [],
    }
    _ctx_ledger.set(ledger)
    return ledger


def _note_doc(kind: str) -> None:
    """문서 1건의 출처를 장부에 적는다. 장부가 없으면(스크립트·테스트) 조용히 통과."""
    ledger = _ctx_ledger.get()
    if ledger is not None:
        ledger[kind] = ledger.get(kind, 0) + 1


def _note_web_wait(seconds: float) -> None:
    """웹 스로틀에서 **실제로 잠든** 시간을 누적한다(ms).

    「2초 간격이 비싼가」는 빈도만으론 답이 안 나온다 — 폴백이 드물면 2초는 아무 비용도
    아니고, 잦으면 그건 간격이 아니라 **주 경로가 자주 실패한다**는 뜻이다. 둘을 가르려면
    「몇 번 갔나」와 「그래서 얼마나 기다렸나」를 같이 봐야 한다."""
    ledger = _ctx_ledger.get()
    if ledger is not None:
        ledger["web_wait_ms"] = ledger.get("web_wait_ms", 0) + int(seconds * 1000)


def _note_corp(corp_code: str | None) -> None:
    """이 요청이 **해석해 낸** 기업을 적는다. 사용자가 친 원문이 아니라 8자리 코드다
    (`삼성전자`·`005930`·`삼전`이 한 값으로 모여야 집계가 뜻을 가진다).
    한 요청이 여러 기업을 건드리면(비교·스크리너) 순서대로 쌓되 상한을 둔다 —
    시장 전수 스캔이 장부를 수천 건으로 부풀리는 걸 막는다."""
    ledger = _ctx_ledger.get()
    if ledger is None or not corp_code:
        return
    codes = ledger["corp_codes"]
    if corp_code not in codes and len(codes) < _LEDGER_MAX_CORPS:
        codes.append(corp_code)


_LEDGER_MAX_CORPS = 20
_LEDGER_MAX_WEAK = 5


def note_weak_resolution(query: str, corp_name: str, kind: str, candidates: int) -> None:
    """이름이 정확히 맞지 않아 **추정으로 고른** 기업을 적는다.

    `_note_corp` 와 같은 관문에서 적는다 — 확정 지점이 하나이므로 여기서 놓치면 어디서도
    못 잡는다. 읽는 쪽은 `ToolEnvelope.to_dict()` 하나뿐이라, tool 이 늘어도 전파가 끊기지
    않는다(23개 서비스가 해석기의 `confidence` 를 전부 버리고 있던 게 이 결함의 원인이었다).
    """
    ledger = _ctx_ledger.get()
    if ledger is None or not query or not corp_name:
        return
    weak = ledger.setdefault("weak_resolutions", [])
    if len(weak) >= _LEDGER_MAX_WEAK or any(w["query"] == query for w in weak):
        return
    weak.append({"query": query, "corp_name": corp_name, "kind": kind, "candidates": candidates})


def weak_resolutions() -> list[dict]:
    ledger = _ctx_ledger.get()
    return list((ledger or {}).get("weak_resolutions") or [])


def set_request_api_key(opendart: str):
    """HTTP 요청의 쿼리 파라미터 ?opendart=키 값을 contextvar에 세팅"""
    _ctx_opendart_key.set(opendart)

logger = logging.getLogger(__name__)
# OpenDART 인증키는 query string으로 전달된다. httpx INFO request 로그는 전체 URL을
# 출력하므로 서버·stdio 모두에서 비활성화하고, warning 이상 전송 오류만 남긴다.
logging.getLogger("httpx").setLevel(logging.WARNING)

OPENDART_BASE_URL = "https://opendart.fss.or.kr/api"
DART_WEB_BASE_URL = "https://dart.fss.or.kr"

# ── Rate Limiting ──
# API와 웹 스크래핑에 각각 다른 최소 간격 적용
# API 최소 간격: 순간 burst를 시간축에 펴는 평활화용(분당 window cap과 별개).
# 0.066초 = 분당 상한 910 = _API_RATE_LIMIT_PER_MINUTE와 정합 → 단일 흐름이 window cap에
# 도달 가능하면서 초당 ~15로 burst 평활. (이전 0.1초는 분당 600 상한이라 window cap을
# 무력화 = 과보수. race는 _api_rate_lock이 직렬화로 보장하므로 간격과 무관.)
_MIN_INTERVAL_API = 0.066
#: 웹 스크래핑(DART 웹 원문 viewer · KIND) 요청 간격 — **한 규칙, 한 시계**(260810 통일).
#: 종전엔 DART 웹 2.0 고정 / KIND 1~3 랜덤으로 갈려 있었는데, 둘은 이미 `_last_web_request`
#: 라는 **같은 시계**를 공유하고 있었다. 즉 두 정책이 아니라 한 흐름의 간격만 호출 경로에
#: 따라 달랐던 것 — 근거 없는 불일치라 하나로 합쳤다.
#:
#: 랜덤인 이유: 고정 간격은 요청이 정확히 규칙적으로 나가 기계 티가 그대로 난다. 지터는
#: 예의 스크래핑의 표준 관행이다.
#: 하한 1.0초는 **새로 만든 값이 아니라** KIND 가 이미 쓰던 하한이다(사고 없이 운영 중).
#: 이 구간(0.5→0.67 req/s)은 차단 판정이 갈리는 자리가 아니다 — 차단은 지속 볼륨·병렬·
#: 정체불명 UA 같은 **패턴**이 좌우한다. 그래서 여기 붙은 규칙은 숫자가 아니라 이 셋이다:
#:   ① 하한 1.0초 아래로 내리지 않는다  ② 시계는 계속 공유한다(총 요청률을 묶는다)
#:   ③ 배치·병렬 금지
#: 공표된 한도가 없으므로 이 값들은 「측정된 안전선」이 아니라 「예의」다. 폴백 빈도가
#: 급증하면 다시 본다 — 그때 볼 계기가 `fetch_viewer`/`fetch_kind`/`web_wait_ms` 다.
_WEB_INTERVAL_RANGE = (1.0, 2.0)
# DART OpenAPI 분당 한도 1000회 — 초과 시 **그 키**가 막힌다(실측 2~3시간).
# 실제 cap을 910으로 둠 (9% buffer, batch 동시 호출 race도 cover).
_API_RATE_LIMIT_PER_MINUTE = 910

_KIND_VALUE_UP_DISCLOSURE_CODE = "0184"
_TRANSIENT_HTTP_ERRORS = (
    httpx.ReadError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


def html_to_text(html: str, images: list[str] | None = None) -> str:
    """HTML/XML을 파서 친화적인 평문으로 정규화.

    get_document의 text 필드가 이 함수로 생성된다. 문서 일부(html 슬라이스)에 적용해도
    전체 적용 결과의 해당 구간과 내용이 일치한다(치환이 전부 로컬) — 구간 슬라이스의
    text화에도 재사용(business_details._slice_getdoc_sections).
    """
    text = re.sub(r'<(?:br|BR)\s*/?>', '\n', html)
    text = re.sub(r'</(?:p|P|div|DIV|tr|TR|li|LI|h\d|H\d|table|TABLE|td|TD|th|TH)>', '\n', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    if images:
        for img in images:
            text = text.replace(img, '')
    text = re.sub(r'[^\S\n]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    return text.strip()


class DartClientError(Exception):
    """OpenDART API 에러"""
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(f"DART API 에러 [{status}]: {message}")


# 기업 코드 매핑 캐시 (모듈 레벨 — 한번 로드하면 프로세스 동안 유지)
_corp_code_cache: list[dict] | None = None
_corp_code_lock: asyncio.Lock | None = None  # lazy init (asyncio loop 필요)


# ── 메모리 캐시 (바이트 예산 LRU) ──
# 260804 OOM 사고: fly 머신이 exit_code=137(oom_killed)로 죽었다. 원인은 캐시 예산을
# **항목 수**로 잡아 둔 것이었다. 「200 entry × ~500KB = ~100MB」라는 주석이 있었지만
# 실측하면 항목 크기가 두 자릿수 배 어긋난다(아래 실측표). 그래서 이제 개수가 아니라
# **바이트**로 자른다 — 항목 크기 가정이 틀려도 예산은 안 틀린다.
#
# 실측 (2026-08-04). 문서 크기는 **디스크 캐시 전수 1,865건**(서버가 실제로 받아 온 것들)
# 이 표본이다. 처음엔 74건짜리 부분표본으로 쟀는데 소집공고에 치우쳐 tail 을 놓쳤다 —
# 표본을 키우자 상한이 29MB 에서 62MB 로 뛰었다. 작은 표본으로 예산을 정하면 안 된다.
#   문서 (n=1,865, mem/disk≈1.54)  p50 0.78MB · p90 10.5MB · p99 26.6MB · 최대 62.0MB
#                                  평균 3.78MB · 8MB 초과가 17.5%
#   list.json 검색결과 (n=10, page_count=100)  평균 9.8KB · 최대 16.2KB
#   alotMatter 배당    (n=10)                  평균 11.2KB
# 옛 가정(500KB)은 평균의 1/7, p99 의 1/53 이다. **개수 200 을 실제로 채워 보니 828MB**
# (아래 before/after 참조) — 그게 768MB VM 을 죽인 값이고, 같은 상한을 쓰는 캐시가
# doc·viewer 둘이라 실제 수용량은 그 두 배였다.
# 문서 항목의 90%는 원문 `html`(사업보고서 본문 XML 4~13M자)이고, 한글이라 파이썬 str 이
# 문자당 2바이트를 쓴다. 이 필드는 파서 20여 곳이 직접 읽으므로 뺄 수 없다.
#
# before/after (서로 다른 문서 200건을 production 경로로 통과시키며 실측):
#   옛 동작(개수 200)   캐시 보유 828.3MB — 계속 증가, evict 0회
#   이번 수정(96MB)     캐시 보유  92.8MB — 평평, evict 179회
#   MCP 부하(30사·동시 3, phys_footprint)  peak 557 → 428MB · 상시 549 → 262MB
#
# 예산 산정 (1GB VM): baseline 172MB(인터프리터 20 + import 87 + corpCode 118,583사 66)
#   + 문서 96 + 배당 16 + 검색 0.5 ≈ 285MB 상주. 나머지는 파싱 transient 몫으로 남긴다
#   (html_to_text 가 13M자 문자열에 re.sub 를 7번 돌려 매번 새 사본을 만든다 — 동시 3건에서
#    이 transient 만으로 footprint 가 230MB 넘게 뛴다. 캐시보다 이쪽이 이제 더 큰 항목이다).
def _cache_entry_bytes(obj, _seen: set[int] | None = None) -> int:
    """캐시 항목의 실제 메모리 바이트. 페이로드가 문자열 지배적이라 getsizeof 합이 정확하다."""
    if _seen is None:
        _seen = set()
    oid = id(obj)
    if oid in _seen:
        return 0
    _seen.add(oid)
    size = sys.getsizeof(obj)
    if isinstance(obj, dict):
        for k, v in obj.items():
            size += _cache_entry_bytes(k, _seen) + _cache_entry_bytes(v, _seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            size += _cache_entry_bytes(item, _seen)
    return size


class LruByteCache:
    """LRU + TTL 캐시 — 항목 수가 아니라 **총 바이트**로 evict 한다.

    항목 크기가 20KB(소집공고)에서 62MB(대형 사업보고서)까지 3자릿수 배 흩어져 있어서
    개수 상한은 메모리 상한이 되지 못한다. 예산보다 큰 단일 항목은 아예 담지 않는다
    (담으면 캐시 전체를 비우고도 못 들어가므로).
    """

    def __init__(self, max_bytes: int, ttl_sec: float, name: str):
        # key → (value, expires_at_unix, nbytes). dict 삽입순서 = LRU 순서(오래된 것이 앞).
        self._entries: dict[str, tuple[object, float, int]] = {}
        self._total_bytes = 0
        self._max_bytes = max_bytes
        self._ttl_sec = ttl_sec
        self._name = name
        self._lock = threading.Lock()
        self.evictions = 0
        self.rejections = 0   # 단일 항목이 예산보다 커서 안 담긴 횟수

    def get(self, key: str):
        """LRU + TTL get. 만료면 제거하고 None."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            value, expires_at, nbytes = entry
            if time.time() >= expires_at:
                del self._entries[key]
                self._total_bytes -= nbytes
                return None
            # LRU touch — 맨 뒤(최근)로 이동
            del self._entries[key]
            self._entries[key] = entry
            return value

    def put(self, key: str, value) -> None:
        nbytes = _cache_entry_bytes(value)
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._total_bytes -= old[2]
            if nbytes > self._max_bytes:
                # 예산보다 큰 단일 항목 — 담으면 캐시를 다 비우고도 못 들어간다.
                self.rejections += 1
                logger.warning(
                    f"[CACHE] {self._name}: 항목이 예산보다 큼 — 캐시 생략 "
                    f"({nbytes/1024/1024:.1f}MB > {self._max_bytes/1024/1024:.0f}MB) key={key}"
                )
                return
            while self._total_bytes + nbytes > self._max_bytes and self._entries:
                # dict 는 삽입순서를 지키므로 첫 키가 가장 오래 안 쓰인 항목이다.
                oldest = next(iter(self._entries))
                self._total_bytes -= self._entries.pop(oldest)[2]
                self.evictions += 1
            self._entries[key] = (value, time.time() + self._ttl_sec, nbytes)
            self._total_bytes += nbytes

    def pop(self, key: str, default=None):
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is None:
                return default
            self._total_bytes -= entry[2]
            return entry[0]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._total_bytes = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "name": self._name,
                "entries": len(self._entries),
                "bytes": self._total_bytes,
                "max_bytes": self._max_bytes,
                "fill_pct": round(100 * self._total_bytes / self._max_bytes, 1) if self._max_bytes else 0.0,
                "evictions": self.evictions,
                "rejections": self.rejections,
            }

    def __len__(self) -> int:
        return len(self._entries)


def _env_mb(name: str, default_mb: int) -> int:
    """운영 중 예산 조정용 — 잘못된 값이면 기본값으로 되돌린다(부팅 실패보다 낫다)."""
    try:
        value = int(os.environ.get(name, default_mb))
        return value * 1024 * 1024 if value > 0 else default_mb * 1024 * 1024
    except ValueError:
        return default_mb * 1024 * 1024


_DOC_CACHE_TTL_SEC = 24 * 60 * 60   # 24h — rcept_no 는 immutable 이지만 영구 점유는 막는다.

# 프로세스 전역 — DartClient 인스턴스마다 캐시를 들면 안 된다.
# `_instances` 는 **API 키마다** DartClient 를 하나씩 만든다. 캐시가 인스턴스 소유면
# 예산이 키 개수만큼 곱해져 「예산」이라는 말이 무의미해진다(사용자 20명 = 20배).
# 캐시 내용은 rcept_no·corp_code 로 찍히는 **공개 데이터라 키와 무관**하고, 디스크 캐시
# (opm_cache)는 이미 인스턴스 간 공유돼 왔다. 전역화는 정책 변경이 아니라 정합성 회복이다.
# 96MB: 실측 p90(10.5MB) 문서 9건 또는 p50(0.78MB) 문서 120여 건. 메모리에서 밀려나도
# 디스크 캐시가 받아 주므로 **evict 가 DART 콜을 늘리지 않는다** — 보수적으로 잡아도 되는 이유.
_DOC_CACHE = LruByteCache(_env_mb("OPM_DOC_CACHE_MB", 96), _DOC_CACHE_TTL_SEC, "document")
# 과거 연도 배당(alotMatter) — 확정된 과거 연도는 안 변해 원래 「영구 캐시」였다(260607).
# 영구 = 무제한이라 프로세스 수명 내내 자란다. 항목 11.2KB × (상장 2,700사 × 캐시 대상 연도)
# 면 상한이 150MB 대다. 16MB(≈1,400건) + 24h TTL 로 문서 캐시와 같은 규율을 준다.
_DIVIDEND_CACHE = LruByteCache(_env_mb("OPM_DIVIDEND_CACHE_MB", 16), _DOC_CACHE_TTL_SEC, "dividend")

#: 디스크 캐시 — 메모리에서 밀려난 것과 **프로세스가 다시 뜬 뒤**를 받는 자리.
#: 종전엔 `/tmp/opm_cache` 였고, 그건 컨테이너 이미지 안이라 **배포마다 통째로 사라졌다.**
#: 받침의 존재 이유는 본체가 죽었을 때 살아 있는 것인데 같이 죽으면 받침이 아니다
#: (260810 실측: 배포 직후 적중률 0%, 24h 평균 36%, 디스크 적중은 24h 13건뿐).
#: 그래서 운영은 볼륨을 가리킨다 — `OPM_DOC_CACHE_DIR=/data/opm_cache`(fly.toml).
#:
#: **경로 이동과 예산·청소는 한 몸이다.** `/tmp` 는 상한이 필요 없었다(배포가 비워줬다).
#: 볼륨엔 그 자동 청소가 없고, 같은 볼륨에 master.db(회사코드 원장 14MB)가 산다 —
#: 캐시가 볼륨을 채우면 **원장 쓰기가 실패한다.**
#:
#: 예산 640MB — 실측 볼륨 974MB, master.db 14MB, 문서 평균 0.58MB 라 약 1,100건이
#: 들어가고 320MB 가 남는다. **볼륨은 머신마다 따로다** — 2대라고 2GB 가 아니라
#: 「974MB 짜리 두 벌」이고, A 에 받아둔 문서는 B 에겐 없다. 그래서 예산은 대당으로 잡는다.
#: 디스크는 RAM 을 안 먹으므로 260804 OOM 과는 무관하다(메모리 예산 96MB 는 그대로).
#:
#: 청소 트리거는 **바이트**다. 종전 「32건마다」는 크기를 못 봤다 — 실측 문서가
#: 20KB~42MB 로 2,000배 흩어져 있어, 큰 것만 연달아 오면 청소 전에 32×42MB=1.3GB 가
#: 쌓여 예산이 무의미해진다(개수 예산으로 터졌던 260804 OOM 과 똑같은 실수다).
#: 32MB 마다 훑으면 초과분은 크기 분포와 무관하게 **32MB + 문서 한 건**으로 묶인다.
#:
#: 퇴출 순서는 **LRU**(가장 오래 안 쓴 것부터)다 — `_load_from_disk` 가 적중마다 mtime 을
#: 올려 「마지막 사용 시각」으로 쓴다. 빈도(LFU)로 안 가는 이유는 둘이다.
#:   ① 디스크는 **메모리 뒤에 있다.** 뜨거운 문서는 메모리에서 끝나 디스크까지 안 온다 —
#:      디스크가 보는 건 이미 걸러진 차가운 꼬리라 빈도 신호 자체가 약하다.
#:   ② LFU 는 횟수를 파일마다 들고 있어야 해서 사이드카 인덱스가 필요하고(정합성·손상
#:      위험), 한때 인기였던 항목이 영영 안 나가는 문제가 있다. LRU 는 그게 없다.
#: 볼륨 이전 전의 디스크 적중은 24h 13건뿐이라 분포를 논할 표본이 아니었다.
#: **먼저 LRU 로 두고 적중 분포를 재본 뒤** 필요하면 그때 빈도를 얹는다.
#:
#: **청소는 경로를 명시한 곳에서만 한다.** 예산의 목적은 볼륨을 지키는 것이고, 볼륨이
#: 아니면 지킬 것이 없다. 로컬 기본 경로(`/tmp/opm_cache`)는 그냥 캐시가 아니라
#: **회귀 재생의 유일한 소재**다(CLAUDE.md: 회귀 캐시는 DART 응답 경계에서만 만든다).
#: 거기에 예산을 집행하면 그 소재를 우리 손으로 지운다 — 260810 실측 로컬 1.35GB/2,350건.
_DISK_CACHE_DIR = (os.environ.get("OPM_DOC_CACHE_DIR")
                   or os.path.join(tempfile.gettempdir(), "opm_cache"))
_DISK_CACHE_MANAGED = bool(os.environ.get("OPM_DOC_CACHE_DIR"))
_DISK_CACHE_MAX_BYTES = _env_mb("OPM_DOC_DISK_CACHE_MB", 640)
_DISK_SWEEP_BYTES = _env_mb("OPM_DOC_DISK_SWEEP_MB", 32)   # 이만큼 쓰면 한 번 훑는다
_disk_bytes_since_sweep = _DISK_SWEEP_BYTES    # 첫 write 에서 한 번 — 부팅 시 초과분 정리
_disk_evictions = 0


def _sweep_disk_cache(written: int = 0, force: bool = False) -> int:
    """예산 초과분을 **오래된 것부터** 지운다. 반환 = 지운 바이트.

    실패는 전부 삼킨다 — 캐시 청소가 사용자 요청을 깨뜨리면 본말전도다.
    `force=False` 면 마지막 청소 이후 쓴 바이트가 `_DISK_SWEEP_BYTES` 를 넘을 때만
    실제로 훑는다 — **개수가 아니라 바이트**로 세는 이유는 위 주석 참조."""
    global _disk_bytes_since_sweep, _disk_evictions
    if not force:
        if not _DISK_CACHE_MANAGED:
            return 0            # 경로를 안 정해준 곳 = 지킬 볼륨이 없다 (위 주석)
        _disk_bytes_since_sweep += written
        if _disk_bytes_since_sweep < _DISK_SWEEP_BYTES:
            return 0
    _disk_bytes_since_sweep = 0
    entries = []
    try:
        with os.scandir(_DISK_CACHE_DIR) as it:
            for e in it:
                if not (e.name.endswith(".json") or e.name.endswith(".json.gz")):
                    continue    # 260823 압축 전환 — 두 형식이 한동안 섞여 산다
                try:
                    st = e.stat()
                except OSError:
                    continue
                entries.append((st.st_mtime, st.st_size, e.path))
    except OSError:
        return 0
    total = sum(size for _, size, _ in entries)
    if total <= _DISK_CACHE_MAX_BYTES:
        return 0
    # key= 를 명시한다. 튜플 전체비교면 mtime 동률일 때 경로까지 비교하게 된다.
    # mtime 은 `_load_from_disk` 가 적중마다 갱신하므로 **마지막 사용 시각**이다 —
    # 즉 이 정렬은 FIFO 가 아니라 LRU 다. 빈도(LFU)로 안 가는 이유는 위 주석 참조.
    entries.sort(key=lambda t: t[0])            # 가장 오래 안 쓴 것부터
    freed = 0
    for _, size, path in entries:
        if total - freed <= _DISK_CACHE_MAX_BYTES:
            break
        try:
            os.remove(path)
        except OSError:
            continue
        freed += size
        _disk_evictions += 1
    if freed:
        logger.info(f"disk cache swept: {freed / 1024 / 1024:.1f}MB freed "
                    f"({total / 1024 / 1024:.1f}MB → {(total - freed) / 1024 / 1024:.1f}MB)")
    return freed


def _disk_cache_stats() -> dict:
    """디스크는 **메모리 예산 밖**이라 따로 센다.

    `persistent` 는 「이 캐시가 배포를 견디는가」다 — 종전 사고가 정확히 그 지점이라
    숫자보다 먼저 보이게 둔다."""
    entries = 0
    nbytes = 0
    try:
        with os.scandir(_DISK_CACHE_DIR) as it:
            for e in it:
                if not e.name.endswith(".json"):
                    continue
                try:
                    nbytes += e.stat().st_size
                except OSError:
                    continue
                entries += 1
    except OSError:
        pass
    return {
        "name": "document_disk",
        "dir": _DISK_CACHE_DIR,
        "persistent": not _DISK_CACHE_DIR.startswith(tempfile.gettempdir()),
        "swept": _DISK_CACHE_MANAGED,     # 예산이 집행되는 곳인가 (로컬 회귀 소재는 안 건드림)
        "entries": entries,
        "bytes": nbytes,
        "max_bytes": _DISK_CACHE_MAX_BYTES,
        "fill_pct": round(100 * nbytes / _DISK_CACHE_MAX_BYTES, 1) if _DISK_CACHE_MAX_BYTES else 0.0,
        "evictions": _disk_evictions,
    }


# doc(document.xml)과 viewer(HTML 폴백)가 예산을 공유하므로 키 네임스페이스로 가른다.
def _doc_key(rcept_no: str) -> str:
    return f"doc:{rcept_no}"


def _viewer_key(rcept_no: str, keywords: tuple[str, ...]) -> str:
    return f"viewer:{rcept_no}|{'|'.join(keywords)}"


def cache_stats() -> dict:
    """캐시 점유 현황 — /health 가 노출한다.

    260804 OOM 은 「죽고 나서야」 보였다. 예산을 정해 놓고 채워지는 걸 못 보면 같은 일이
    반복되므로, 예산 대비 점유율·evict 횟수를 상시 관측 가능하게 둔다.
    (검색 캐시는 인스턴스 소유 + 실측 0.5MB 규모라 여기 넣지 않는다.)
    """
    return {"document": _DOC_CACHE.stats(), "dividend": _DIVIDEND_CACHE.stats(),
            "document_disk": _disk_cache_stats()}


# ── sqlite master cache (KIS 참고, iter27 ship) ──
# corpCode.xml 50MB 영구 cache → cold start 6-15s → ms.
# fly.io volume mount 시 machine restart에도 영구.
# TTL 24h — 자동 update.
_MASTER_DB_PATH = Path(os.environ.get("OPM_MASTER_DB_PATH", "configs/master.db"))
_MASTER_DB_TTL_HOURS = 168   # 7d (corpCode 변경 빈도 낮음, 24h이었지만 idle 후 첫 호출마다 50MB 재다운로드 발생)

# ── 정기보고서 제출 법인 명부 ──
# 🔴 **「상장사냐」가 아니라 「공시 의무가 있느냐」로 판별해야 한다.** 260823 실측 —
#    DART 는 상장폐지돼도 stock_code 를 지우지 않고(신한은행 000010·우리은행 000030·
#    KB손해보험 002550 전부 미상장인데 코드가 남아 있다), 반대로 농협금융지주·농협생명보험·
#    NH농협손해보험은 **한 번도 상장된 적 없어 코드가 없는데 정기보고서를 꼬박꼬박 낸다.**
#    stock_code 로 거르면 앞은 우연히 통과하고 뒤는 영영 막힌다.
#
#    같은 명부가 **동명 법인**도 가른다. 국민은행은 원장에 셋인데(00386937·00104467·00104476)
#    정기보고서를 내는 것은 00386937 하나뿐이다. 하나은행·미래에셋증권도 같다.
#    DART 가 소멸 법인을 2017-06-30 상태로 남겨두기 때문에 생기는 문제다.
#
#    실측(2026-08-23): 400일 · 3개월 창 5구간 · API 183회 · 162초 → **3,424개 법인.**
#    유동화 SPC·소형 자산운용사는 정기보고서를 안 내므로 자동으로 빠진다 —
#    이름 규칙(「제○차」·「유동화전문」)을 손으로 관리할 필요가 없다.
_FILERS_TTL_HOURS = 168      # 7d — 정기보고서는 분기마다 몰려 나오므로 주 1회면 충분
_FILERS_LOOKBACK_DAYS = 400  # 사업보고서 1주기(1년) + 여유
_FILERS_WINDOW_DAYS = 85     # corp_code 없는 조회는 **3개월까지만** 허용된다(DART status 100)
_FILERS_MIN_EXPECTED = 2_000  # 이보다 적으면 수집이 덜 된 것으로 보고 명부를 쓰지 않는다

_periodic_filers_cache: frozenset[str] | None = None
_periodic_filers_lock: "asyncio.Lock | None" = None
#: 백그라운드 수집 태스크 — 요청은 절대 이것을 기다리지 않는다
_filers_build_task: "asyncio.Task | None" = None

# 법인격 suffix 제거 패턴
_CORP_SUFFIX_RE = re.compile(
    r'\s*[\(（]?주[\)）]?\s*$'     # (주), ㈜, 주)
    r'|\s*㈜\s*$'
    r'|\s*주식회사\s*$'
    r'|\s*co\.,?\s*ltd\.?\s*$'
    r'|\s*inc\.?\s*$'
    r'|\s*corp\.?\s*$',
    re.IGNORECASE
)

# 법인격 prefix 제거 패턴 — DART 정식명은 「(주)광무」·「주식회사솔루엠」처럼 앞에 붙는다.
# suffix 만 떼면 우리 툴이 스스로 출력한 회사명으로 재조회했을 때 식별 실패한다
# (실측 100사 라이브 스윕에서 14곳). 「주성엔지니어링」처럼 '주'로 시작하는 정상 상호를
# 깎지 않도록 닫는 괄호나 '식회사'를 반드시 요구한다.
_CORP_PREFIX_RE = re.compile(
    r'^\s*[\(（]\s*[주유재사]\s*[\)）]\s*'      # (주) (유) (재) (사)
    r'|^\s*[㈜㈐]\s*'
    r'|^\s*(?:주식|유한|합자|합명)\s*회사\s*'
    r'|^\s*(?:재단|사단)\s*법인\s*',
)

# 알려진 약칭/영문명 → DART 정식 한글명 매핑 (lowercase key)
_CORP_ALIASES: dict[str, str] = {
    # ── 영문/약칭 → DART 정식명 ──
    "ls electric": "엘에스일렉트릭",
    "ls일렉트릭": "엘에스일렉트릭",
    "sk바이오팜": "에스케이바이오팜",
    "kt&g": "케이티앤지",
    "ktng": "케이티앤지",
    "tkg휴켐스": "티케이지휴켐스",
    "휴켐스": "티케이지휴켐스",
    # ── 슬랭/약칭 ──
    "삼전": "삼성전자",
    "삼성화재": "삼성화재해상보험",
    "엘지전자": "LG전자",
    "엘지화학": "LG화학",
    "엘지에너지솔루션": "LG에너지솔루션",
    "한국전력": "한국전력공사",
    "현차": "현대자동차",
    "현대차": "현대자동차",
    "기아차": "기아",
    # 옛 사명·한글 음차라 역음차로도 안 닿는다(「에쓰오일」→'s오일'은 'soil'과 다르고,
    # 「기아자동차」는 2021 사명변경 전 이름이다). 실측 라이브 스윕에서 둘 다 error 였다.
    "기아자동차": "기아",
    "에쓰오일": "S-Oil",
    "에스오일": "S-Oil",
    "에스-오일": "S-Oil",
    "셀트리온헬스케어": "셀트리온",
    "카뱅": "카카오뱅크",
    "카페": "카카오페이",
    "네이버": "NAVER",  # DART 정식명=영문 "NAVER"(2017 사명변경) — 한글 그대로면 자회사(네이버웹툰컴퍼니 등)로 오매칭
    "크래프톤": "크래프톤",
    # ── 사명 변경 ──
    "lig aerospace": "LIG넥스원",
    "lig aerospace & defense": "LIG넥스원",
    "lig에어로스페이스": "LIG넥스원",
    "하이브": "하이브",
    "sk이노베이션": "SK이노베이션",
    # ── K-POP ──
    "sm": "에스엠",
    "sm엔터테인먼트": "에스엠",
    "에스엠엔터테인먼트": "에스엠",
    "sm엔터": "에스엠",
    "jyp엔터테인먼트": "JYP Ent.",
    "jyp엔터": "JYP Ent.",
    "와이지엔터테인먼트": "와이지엔터테인먼트",
    "yg": "와이지엔터테인먼트",
    # ── 사명 변경 (리브랜딩) ──
    "dgb금융지주": "iM금융지주",
    "dgb": "iM금융지주",
    "아이엠금융지주": "iM금융지주",
    "대구은행": "아이엠뱅크",
    "im뱅크": "아이엠뱅크",
    # ── 금융지주 영문 약칭 ──
    "jb": "JB금융지주",
    "bnk": "BNK금융지주",
    "kb": "KB금융",
    "kb금융지주": "KB금융",  # KB금융지주는 KB금융으로 사명 변경
    "신한금융지주": "신한지주",
    "하나금융": "하나금융지주",
    # ── 코스닥 대형 (발음 한글화 변형) ──
    "cj이엔엠": "CJ ENM",
    "cj이엔앰": "CJ ENM",
    # ── 한국타이어 사명 변경 (한국타이어 → 한국타이어앤테크놀로지, 2019.05) ──
    "한국타이어": "한국타이어앤테크놀로지",
    "한국타이어월드와이드": "한국앤컴퍼니",  # 지주회사 분리
    # ── 영문 그룹사 → DART 정식 등록명 ──
    # 주의: DART corp_name이 영문 그대로인 경우와 한글인 경우 구분.
    # "엘지"로 변환하면 "엘지데이콤" (옛 통신사) 첫 매칭 fail. "LG" 그대로 사용해야 003550 정확.
    "kt": "케이티",
    "sk": "SK",            # corp_name="SK", stock=034730
    "lg": "LG",            # corp_name="LG", stock=003550
    "gs": "GS",            # corp_name="GS", stock=078930
    "cj": "CJ",            # corp_name="CJ", stock=001040
    # ── 사명 변경 (DART 등록명 정확 매핑) ──
    "엔씨소프트": "NC",         # 2022~ 사명 변경, corp_name="NC", stock=036570
    "ncsoft": "NC",
    "엔씨": "NC",
    "lig넥스원": "LIG디펜스앤에어로스페이스",  # 2024 사명 변경, stock=079550
    "lig넥스": "LIG디펜스앤에어로스페이스",
    "ligdefense": "LIG디펜스앤에어로스페이스",
    # ── 포스코 그룹 (2022 분할) ──
    "포스코": "POSCO홀딩스",
    "posco": "POSCO홀딩스",
    "포스코그룹": "POSCO홀딩스",
    # ── HD현대 그룹 사명 (2022 변경) ──
    "현대중공업": "HD현대중공업",
    "현대미포조선": "HD현대미포",
    "현대일렉트릭": "HD현대일렉트릭",
    "현대로보틱스": "HD현대로보틱스",
    # ── 셀트리온헬스케어 합병 (2024) ──
    # 주의: 셀트리온제약(068760)은 별도 상장 회사로 존속. alias 매핑하지 않음.
    # 옛 코멘트 "합병 후 셀트리온"은 잘못된 매핑으로 자회사 query → 모회사로 잘못 해석되어 제거 (260508).
    # ── 두산 그룹 사명 ──
    "두산인프라코어": "HD현대인프라코어",  # 2021 매각
    "두산밥캣": "두산밥캣",
    # ── 일반 약칭 ──
    "고려아연": "고려아연",
    "kcc": "케이씨씨",
    "포스코홀딩스": "POSCO홀딩스",
    "롯데칠성": "롯데칠성음료",
    "삼화콘덴서": "삼화콘덴서공업",
    "한국단자": "한국단자공업",
    "di동일": "디아이동일",
    "유진투자증권": "유진증권",
    "kcc글라스": "케이씨씨글라스",
    "f&f홀딩스": "F&F 홀딩스",
    "m레거시증권": "미래에셋증권",
    "m레거시생명": "미래에셋생명",
    "m레거시벤처투자": "미래에셋벤처투자",
    "lg에너지": "LG에너지솔루션",
    "이마트": "이마트",
    "이베이코리아": "이마트",  # 일부 alias
}

def _normalize_corp_name(name: str) -> str:
    """법인격 suffix 제거 후 소문자 변환 (매칭용)"""
    name = _CORP_PREFIX_RE.sub("", name.strip())
    name = _CORP_SUFFIX_RE.sub("", name.strip())
    return name.strip().lower()

def _sort_corp_results(corps: list[dict]) -> list[dict]:
    """상장(stock_code 있음) 우선, 동점 시 modify_date 최신 순"""
    return sorted(
        corps,
        key=lambda c: (0 if c.get("stock_code") else 1, -(int(c.get("modify_date") or "0"))),
    )


def _validate_corp_master(corps: list[dict]) -> None:
    listed = [corp for corp in corps if corp.get("stock_code")]
    english_coverage = (
        sum(bool(corp.get("corp_eng_name")) for corp in listed) / len(listed)
        if listed else 0.0
    )
    if len(corps) < 100_000 or len(listed) < 3_000 or english_coverage < 0.9:
        raise ValueError(
            f"corpCode master validation failed: total={len(corps)}, "
            f"listed={len(listed)}, english={english_coverage:.1%}"
        )


class DartClient:
    """OpenDART API 호출 래퍼

    하는 일:
    - API 키를 .env에서 읽어서 자동으로 붙임 (속도 제한 시 보조 키로 자동 전환)
    - 요청 보내고 JSON 파싱
    - 에러 상태(status != "000")면 예외 발생
    - 종목코드/회사명 → corp_code 변환 (corpCode.xml 캐싱)
    """

    def __init__(self, api_keys: list[str] | None = None):
        self._api_keys = []
        if api_keys:
            self._api_keys = list(api_keys)
        elif (ctx_key := _ctx_opendart_key.get()):
            # **사용자 요청은 그 사용자의 키 하나만 쓴다.** 예비키를 뒤에 붙이면 사용자 키가
            # 한도에 걸렸을 때 `_rotate_key()` 가 조용히 **우리 키**로 넘어간다. 그러면
            #   ① 한 사용자의 과다 호출이 우리 키를 태우고, 우리 키가 막히면 **다른 사용자와
            #      배치 작업이 전부 함께 멈춘다**(CLAUDE.md: 한도는 키마다다).
            #   ② 사용자는 자기 키가 한도에 걸린 걸 모른 채 계속 쓴다 — 알려야 할 것을 덮는다.
            # 풀이 1개면 `_rotate_key()` 가 False 를 내므로 회전 자체가 일어나지 않는다.
            self._api_keys.append(ctx_key)
        else:
            # 사용자 키가 없는 경로(로컬 스크립트·배치·부팅 시 corpCode 적재)만 env 키를 쓴다.
            # OPENDART_API_KEY_2, _3, _4, ... 순번 있는 만큼 전부 로드(260705, 키 2개 초과 지원).
            # 분당 스로틀(_api_call_timestamps)은 인스턴스 하나가 전 키 공유 — 키를 늘려도 IP레벨
            # 분당한도(910)는 그대로 지켜짐, 늘어나는 건 하루 총 쿼터(키당 4만)뿐.
            primary = os.getenv("OPENDART_API_KEY")
            if primary:
                self._api_keys.append(primary)
            i = 2
            while True:
                k = os.getenv(f"OPENDART_API_KEY_{i}")
                if not k:
                    break
                self._api_keys.append(k)
                i += 1
        if not self._api_keys:
            raise ValueError("OPENDART_API_KEY가 설정되어 있지 않습니다. 쿼리 파라미터(?opendart=키) 또는 .env에 설정하세요.")
        self._key_index = 0
        self.api_key = self._api_keys[0]
        # Rate limiting — 마지막 요청 시각 추적
        self._last_api_request = 0.0
        self._last_web_request = 0.0
        # Rolling window rate limiter (분당 한도 강제) — DART 1000/min 정책 hard guard
        self._api_call_timestamps: collections.deque[float] = collections.deque()
        self._api_rate_lock = asyncio.Lock()
        # Document caching (메모리 + 디스크)
        # 메모리 캐시는 **프로세스 전역**이다(모듈 상단 _DOC_CACHE 주석 참조 — 인스턴스 소유면
        # API 키 수만큼 예산이 곱해진다). 여기서는 그 전역 캐시를 가리키기만 한다.
        # doc 과 viewer 는 **하나의 바이트 예산을 공유**한다. 예전엔 200개씩 따로 잡혀
        # 문서화된 예산의 두 배(400개)를 담을 수 있었다. 키 앞에 네임스페이스를 붙여 구분한다.
        self._doc_cache = _DOC_CACHE
        self._disk_cache_dir = _DISK_CACHE_DIR      # 전역 (모듈 상단 주석 참조)
        # Search result caching (세션 기반, TTL 없음)
        # 실측 평균 9.8KB · 최대 16.2KB(page_count=100) → 50건이면 ~0.5MB. 문서 캐시의
        # 1/500 수준이라 개수 상한으로 충분하다(260804 OOM 조사에서 확인, 변경 불필요).
        self._search_cache: dict[str, dict] = {}
        self._MAX_SEARCH_CACHE = 50
        # 과거 연도 배당(alotMatter) — 전역 바이트 예산 LRU (모듈 상단 _DIVIDEND_CACHE 참조)
        self._dividend_cache = _DIVIDEND_CACHE
        # 사용량 추적 (각 service가 snapshot으로 차이 계산)
        self._request_counter = 0
        # Persistent HTTP client — connection pool 재사용으로 TLS handshake 중복 제거.
        # 매 요청마다 새 AsyncClient 생성 시 100-300ms TLS 비용 → 재사용 시 0ms.
        # fly machine restart 시 OS가 자동 정리, leak 위험 최소.
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=60.0,
            ),
        )

    def invalidate_document(self, rcept_no: str) -> None:
        """문서 메모리 캐시 무효화 — cold fetch 를 재현하는 성능 스크립트용.
        (디스크 캐시는 호출측이 따로 지운다.)"""
        self._doc_cache.pop(_doc_key(rcept_no))

    def api_call_snapshot(self) -> int:
        """현재까지 누적된 DART API 호출 수. service가 시작·종료 시점에 찍어 차이를 계산."""
        return self._request_counter

    def _rotate_key(self) -> bool:
        """다음 API 키로 전환. 전환 가능하면 True, 더 없으면 False."""
        if len(self._api_keys) <= 1:
            return False
        self._key_index = (self._key_index + 1) % len(self._api_keys)
        self.api_key = self._api_keys[self._key_index]
        return True

    async def _request(self, endpoint: str, params: dict) -> dict:
        """공통 API 호출 메서드 (JSON 응답용)

        Args:
            endpoint: API 엔드포인트 (예: "list.json")
            params: 쿼리 파라미터 (api_key는 자동 추가)

        Returns:
            API 응답 JSON (dict)
        """
        self._request_counter += 1
        await self._throttle_api()
        params["crtfc_key"] = self.api_key
        url = f"{OPENDART_BASE_URL}/{endpoint}"

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._http.get(url, params=params, timeout=30)
                break
            except _TRANSIENT_HTTP_ERRORS as exc:
                last_exc = exc
                if attempt < 2:
                    wait = 0.5 * (2 ** attempt)
                    logger.warning(f"{endpoint} attempt {attempt+1} failed ({type(exc).__name__}): retry in {wait}s")
                    await asyncio.sleep(wait)
        else:
            raise last_exc or DartClientError("TRANSPORT_ERROR", f"{endpoint} 요청 실패")
        response.raise_for_status()
        data = response.json()

        # DART API는 status "000"이 정상
        status = data.get("status", "")
        if status != "000":
            # 속도 제한("020") 등 일시적 에러 시 보조 키로 재시도
            if self._rotate_key():
                params["crtfc_key"] = self.api_key
                response = await self._http.get(url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                status = data.get("status", "")
                if status == "000":
                    return data
            message = data.get("message", "알 수 없는 에러")
            raise DartClientError(status, message)

        return data

    async def _request_binary(self, endpoint: str, params: dict) -> bytes:
        """공통 API 호출 메서드 (바이너리 응답용 — ZIP 등)

        비정상 응답(XML 에러) 수신 시:
        1. XML 에러면 DartClientError 발생 (접수번호 오류 등)
        2. ZIP도 XML도 아니면 보조 키로 전환 후 재시도
        """
        await self._throttle_api()
        params["crtfc_key"] = self.api_key
        url = f"{OPENDART_BASE_URL}/{endpoint}"

        # corpCode.xml은 50MB라 cold start 시 60s 부족 → 120s
        timeout = 120 if endpoint == "corpCode.xml" else 60
        response = await self._http.get(url, params=params, timeout=timeout)
        response.raise_for_status()

        content = response.content

        # ZIP 파일은 PK 시그니처(50 4B)로 시작
        if content[:2] == b'PK':
            return content

        # XML 에러 응답 체크 (접수번호 오류, 한도 초과 등)
        if content[:5] == b'<?xml':
            import re
            status_m = re.search(r'<status>(\d+)</status>', content.decode('utf-8', errors='replace'))
            msg_m = re.search(r'<message>(.+?)</message>', content.decode('utf-8', errors='replace'))
            if status_m:
                raise DartClientError(status_m.group(1), msg_m.group(1) if msg_m else "알 수 없는 에러")

        # ZIP도 XML도 아닌 비정상 응답 → 보조 키로 재시도
        if self._rotate_key():
            params["crtfc_key"] = self.api_key
            response = await self._http.get(url, params=params, timeout=60)
            response.raise_for_status()
            content = response.content
            if content[:5] == b'<?xml':
                import re
                status_m = re.search(r'<status>(\d+)</status>', content.decode('utf-8', errors='replace'))
                msg_m = re.search(r'<message>(.+?)</message>', content.decode('utf-8', errors='replace'))
                if status_m:
                    raise DartClientError(status_m.group(1), msg_m.group(1) if msg_m else "알 수 없는 에러")

        return content

    # ── 기업 코드 매핑 ──

    @staticmethod
    def _master_db_load(*, require_english: bool = True, allow_stale: bool = False) -> list[dict] | None:
        """sqlite master.db에서 corp_codes 로드 (TTL 7d 검증).

        Returns:
            list[dict] (cache fresh) 또는 None (없거나 stale)
        """
        if not _MASTER_DB_PATH.exists():
            return None
        try:
            conn = sqlite3.connect(_MASTER_DB_PATH)
            cur = conn.cursor()
            # _meta.last_updated 검증
            cur.execute("CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)")
            cur.execute("SELECT value FROM _meta WHERE key='last_updated'")
            row = cur.fetchone()
            if not row:
                conn.close()
                return None
            try:
                last = datetime.fromisoformat(row[0])
            except ValueError:
                conn.close()
                return None
            if not allow_stale and datetime.now() - last > timedelta(hours=_MASTER_DB_TTL_HOURS):
                conn.close()
                return None
            columns = {r[1] for r in cur.execute("PRAGMA table_info(corp_codes)").fetchall()}
            if "corp_eng_name" not in columns:
                if require_english:
                    cur.execute("ALTER TABLE corp_codes ADD COLUMN corp_eng_name TEXT NOT NULL DEFAULT ''")
                    conn.commit()
                    conn.close()
                    # Existing rows cannot be backfilled without corpCode.xml. Force one refresh.
                    return None
                english_column = "''"
            else:
                english_column = "corp_eng_name"
            cur.execute(f"SELECT corp_code, corp_name, {english_column}, stock_code, modify_date FROM corp_codes")
            corps = [
                {"corp_code": r[0], "corp_name": r[1], "corp_eng_name": r[2] or "",
                 "stock_code": r[3] or "", "modify_date": r[4] or ""}
                for r in cur.fetchall()
            ]
            conn.close()
            listed = [corp for corp in corps if corp["stock_code"]]
            if require_english and listed and sum(bool(corp["corp_eng_name"]) for corp in listed) / len(listed) < 0.9:
                return None
            return corps if corps else None
        except sqlite3.Error as exc:
            logger.warning(f"sqlite master load 실패 (download fallback): {exc}")
            return None

    @staticmethod
    def lookup_former_name(name: str) -> dict | None:
        """옛 사명으로 물었을 때 지금 회사를 찾아준다 — 없으면 None.

        corpCode.xml 스냅샷 이력(`corp_name_history`)에서 이 이름을 쓴 적이 있는 법인을 찾고,
        그 법인의 **현재** 사명이 다를 때만 「바뀐 것」으로 본다. 같으면 그냥 현재 이름이다.

        한계 — 이력은 우리가 스냅샷을 남기기 시작한 뒤의 변경만 담는다. 그 전에 이미 바뀐
        사명(예: 영풍정밀 → 케이젯정밀)은 DART 어디에도 남아 있지 않아 복구할 수 없다.
        """
        if not name or not _MASTER_DB_PATH.exists():
            return None
        try:
            conn = sqlite3.connect(_MASTER_DB_PATH)
            row = conn.execute(
                "SELECT h.corp_code, c.corp_name, c.stock_code, h.last_seen"
                "  FROM corp_name_history h JOIN corp_codes c ON c.corp_code = h.corp_code"
                " WHERE h.corp_name = ? AND c.corp_name <> h.corp_name"
                " ORDER BY h.last_seen DESC LIMIT 1",
                (name.strip(),),
            ).fetchone()
            conn.close()
        except sqlite3.Error:
            return None
        if not row:
            return None
        return {"corp_code": row[0], "current_name": row[1],
                "stock_code": row[2] or "", "last_seen_as": row[3]}

    @staticmethod
    def _master_db_save(corps: list[dict]) -> None:
        """sqlite master.db에 corp_codes 저장 + _meta.last_updated 갱신."""
        try:
            _MASTER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(_MASTER_DB_PATH)
            cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS corp_codes (corp_code TEXT PRIMARY KEY, corp_name TEXT NOT NULL, corp_eng_name TEXT NOT NULL DEFAULT '', stock_code TEXT, modify_date TEXT)")
            columns = {r[1] for r in cur.execute("PRAGMA table_info(corp_codes)").fetchall()}
            if "corp_eng_name" not in columns:
                cur.execute("ALTER TABLE corp_codes ADD COLUMN corp_eng_name TEXT NOT NULL DEFAULT ''")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_stock_code ON corp_codes(stock_code) WHERE stock_code != ''")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_corp_name ON corp_codes(corp_name)")
            cur.execute("CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)")

            # 사명 이력 — DART 는 옛 이름을 **어디에도** 주지 않는다. corpCode.xml 에는 현재
            # 사명만 있고, 공시 목록(list.json)의 corp_name·flr_nm 조차 과거 공시에까지 현재
            # 사명을 소급해 채워준다(실측: 2024년 공시가 「KZ정밀」로 나온다. 당시엔 영풍정밀).
            # 그래서 우리가 스냅샷을 남기지 않으면 「구 사명으로 조회」는 영원히 불가능하다.
            # 아래 DELETE 가 매 갱신마다 옛 이름을 지우므로, 지우기 전에 적재한다.
            cur.execute(
                "CREATE TABLE IF NOT EXISTS corp_name_history ("
                " corp_code TEXT NOT NULL, corp_name TEXT NOT NULL,"
                " first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,"
                " PRIMARY KEY (corp_code, corp_name))"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_hist_name ON corp_name_history(corp_name)")
            now = datetime.now().isoformat()
            # 이번에 본 (코드, 이름) 을 전부 적재한다. PRIMARY KEY 가 중복을 흡수하므로 실제로
            # 행이 느는 건 사명이 바뀐 회사뿐이다. 「과거 이름」은 나중에 현재 사명과 달라진
            # 행으로 정의된다 — 변경 시점을 따로 판정할 필요가 없다.
            cur.executemany(
                "INSERT INTO corp_name_history (corp_code, corp_name, first_seen, last_seen)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(corp_code, corp_name) DO UPDATE SET last_seen = excluded.last_seen",
                [(c["corp_code"], c["corp_name"], now, now) for c in corps if c.get("corp_name")],
            )

            cur.execute("DELETE FROM corp_codes")  # 전체 reload
            cur.executemany(
                "INSERT INTO corp_codes (corp_code, corp_name, corp_eng_name, stock_code, modify_date) VALUES (?, ?, ?, ?, ?)",
                [(c["corp_code"], c["corp_name"], c.get("corp_eng_name", ""), c["stock_code"], c["modify_date"]) for c in corps],
            )
            cur.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('last_updated', ?)", (datetime.now().isoformat(),))
            conn.commit()
            conn.close()
            logger.info(f"sqlite master saved: {len(corps)} corps → {_MASTER_DB_PATH}")
        except sqlite3.Error as exc:
            logger.warning(f"sqlite master save 실패 (memory cache만 사용): {exc}")

    async def _load_corp_codes(self) -> list[dict]:
        """corpCode.xml 로드 — 3-layer cache: memory → sqlite (TTL 7d) → DART download.

        F6/F7 (Phase 4): asyncio.Lock으로 동시 다운로드 race 제거 + httpx
        ReadError/ConnectError 시 3회 retry (1/2/4s backoff).
        iter27 (KIS 참고): sqlite master cache (7d TTL) — cold start 6-15s → ms.
        OPM_MASTER_DB_PATH env로 위치 변경 가능 (fly.io volume mount 등).

        Returns:
            [{"corp_code": "00126380", "corp_name": "삼성전자",
              "stock_code": "005930", "modify_date": "20240101"}, ...]
        """
        global _corp_code_cache, _corp_code_lock
        # Layer 1: memory cache
        if _corp_code_cache is not None:
            return _corp_code_cache

        if _corp_code_lock is None:
            _corp_code_lock = asyncio.Lock()

        async with _corp_code_lock:
            if _corp_code_cache is not None:
                return _corp_code_cache

            # Layer 2: sqlite cache (TTL 7d)
            corps = self._master_db_load()
            if corps:
                logger.info(f"corp_codes loaded from sqlite master ({len(corps)} corps, fresh ≤7d)")
                _corp_code_cache = corps
                return corps

            # Layer 3: DART download (3회 retry)
            last_exc: Exception | None = None
            for attempt in range(3):
                try:
                    data = await self._request_binary("corpCode.xml", {})
                    z = zipfile.ZipFile(io.BytesIO(data))
                    xml_file = z.namelist()[0]
                    xml_content = z.read(xml_file)
                    root = ET.fromstring(xml_content)
                    corps = []
                    for item in root.findall("list"):
                        corps.append({
                            "corp_code": item.findtext("corp_code", ""),
                            "corp_name": item.findtext("corp_name", ""),
                            "corp_eng_name": item.findtext("corp_eng_name", ""),
                            "stock_code": item.findtext("stock_code", "").strip(),
                            "modify_date": item.findtext("modify_date", ""),
                        })
                    _validate_corp_master(corps)
                    _corp_code_cache = corps
                    # sqlite save (실패해도 memory cache로 계속)
                    self._master_db_save(corps)
                    return corps
                except (httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                    last_exc = exc
                    if attempt < 2:
                        wait = 1.0 * (2 ** attempt)  # 1s / 2s / 4s
                        logger.warning(f"_load_corp_codes attempt {attempt+1} failed ({type(exc).__name__}): retry in {wait}s")
                        await asyncio.sleep(wait)
                except DartClientError as exc:
                    last_exc = exc
                    break
                except Exception as exc:
                    last_exc = exc
                    logger.warning(f"corpCode master parse/validation 실패: {type(exc).__name__}: {exc}")
                    break

            fallback_corps = self._master_db_load(require_english=False, allow_stale=True)
            if fallback_corps:
                logger.warning("corpCode 영문명 갱신 실패 — 기존 한글 master로 fail-open")
                _corp_code_cache = fallback_corps
                return fallback_corps
            raise DartClientError("CORPCODE_DOWNLOAD_FAILED", f"corpCode.xml 3회 retry 모두 실패: {type(last_exc).__name__}: {last_exc}")


    # ── 정기보고서 제출 법인 명부 ──

    @staticmethod
    def _filers_db_load() -> frozenset[str] | None:
        """sqlite 에서 명부 로드 (TTL 7d). 없거나 상하면 None."""
        if not _MASTER_DB_PATH.exists():
            return None
        try:
            conn = sqlite3.connect(_MASTER_DB_PATH)
            cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)")
            cur.execute("SELECT value FROM _meta WHERE key='filers_updated'")
            row = cur.fetchone()
            if not row:
                conn.close()
                return None
            try:
                last = datetime.fromisoformat(row[0])
            except ValueError:
                conn.close()
                return None
            if datetime.now() - last > timedelta(hours=_FILERS_TTL_HOURS):
                conn.close()
                return None
            cur.execute("CREATE TABLE IF NOT EXISTS periodic_filers ("
                        " corp_code TEXT PRIMARY KEY, last_rcept_dt TEXT)")
            codes = {r[0] for r in cur.execute("SELECT corp_code FROM periodic_filers")}
            conn.close()
            return frozenset(codes) if len(codes) >= _FILERS_MIN_EXPECTED else None
        except sqlite3.Error as exc:
            logger.warning(f"periodic_filers sqlite load 실패: {exc}")
            return None

    @staticmethod
    def _filers_db_save(filers: dict[str, str]) -> None:
        try:
            _MASTER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(_MASTER_DB_PATH)
            cur = conn.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS periodic_filers ("
                        " corp_code TEXT PRIMARY KEY, last_rcept_dt TEXT)")
            cur.execute("CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)")
            cur.execute("DELETE FROM periodic_filers")
            cur.executemany("INSERT INTO periodic_filers (corp_code, last_rcept_dt) VALUES (?, ?)",
                            list(filers.items()))
            cur.execute("INSERT INTO _meta (key, value) VALUES ('filers_updated', ?)"
                        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (datetime.now().isoformat(),))
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            logger.warning(f"periodic_filers sqlite save 실패: {exc}")

    async def periodic_filers(self) -> frozenset[str]:
        """최근 400일 안에 **정기보고서를 낸 법인**의 corp_code 집합.

        못 만들면 **빈 집합**을 돌려준다 — 호출부는 「명부가 없으면 예전대로」로 동작해야
        한다. 명부를 못 받았다는 이유로 조회를 막으면 안 된다(fail-open).
        """
        global _periodic_filers_cache, _periodic_filers_lock
        if _periodic_filers_cache is not None:
            return _periodic_filers_cache
        if _periodic_filers_lock is None:
            _periodic_filers_lock = asyncio.Lock()
        async with _periodic_filers_lock:
            if _periodic_filers_cache is not None:
                return _periodic_filers_cache
            cached = self._filers_db_load()
            if cached is not None:
                logger.info(f"periodic_filers loaded from sqlite ({len(cached)} corps)")
                _periodic_filers_cache = cached
                return cached
        # 🔴 **요청 경로에서 명부를 만들면 안 된다.** 260823 프로덕션 실측 — 첫 조회가
        #    183 API콜(약 3분)을 동기로 돌다 프록시 타임아웃에 걸려 502 가 났고, 저장을
        #    못 하니 **다음 요청도 같은 3분을 다시 돌아 영구히 낫지 않았다.**
        #    없으면 **빈 집합으로 즉시 돌려주고**(= 명부 없던 때의 동작) 뒤에서 만든다.
        self._start_filers_build()
        return frozenset()

    def _start_filers_build(self) -> None:
        """명부를 백그라운드에서 만든다. 이미 만드는 중이면 아무것도 하지 않는다."""
        global _filers_build_task
        if _filers_build_task is not None and not _filers_build_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        _filers_build_task = loop.create_task(self._build_filers())

    async def _build_filers(self) -> None:
        global _periodic_filers_cache
        try:
            filers = await self._fetch_periodic_filers()
        except Exception as exc:      # 네트워크·쿼터 — 다음 기회에 다시 만든다
            logger.warning(f"periodic_filers 수집 실패({type(exc).__name__}) — 명부 없이 진행")
            return
        if len(filers) < _FILERS_MIN_EXPECTED:
            logger.warning(f"periodic_filers {len(filers)}건뿐 — 덜 모인 것으로 보고 쓰지 않는다")
            return
        self._filers_db_save(filers)
        _periodic_filers_cache = frozenset(filers)
        logger.info(f"periodic_filers 백그라운드 수집 완료 ({len(filers)} corps)")

    async def _fetch_periodic_filers(self) -> dict[str, str]:
        """DART 에서 명부를 만든다. corp_code 없는 조회는 3개월 창이 상한이라 나눠 돈다."""
        today = date.today()
        filers: dict[str, str] = {}
        cur = today - timedelta(days=_FILERS_LOOKBACK_DAYS)
        while cur < today:
            end = min(cur + timedelta(days=_FILERS_WINDOW_DAYS), today)
            page = 1
            while True:
                res = await self.search_filings(
                    bgn_de=cur.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                    pblntf_ty="A", page_no=page, page_count=100,
                )
                for item in res.get("list") or []:
                    code = item.get("corp_code")
                    if not code:
                        continue
                    dt = item.get("rcept_dt", "")
                    if dt > filers.get(code, ""):
                        filers[code] = dt
                total_page = int(res.get("total_page") or 1)
                if page >= total_page:
                    break
                page += 1
            cur = end + timedelta(days=1)
        return filers

    async def lookup_corp_code(self, query: str) -> dict | None:
        """종목코드/회사명/약칭/영문명으로 corp_code 조회 (단일 결과)

        동명 기업이 있을 경우 modify_date 최신 + 상장 기업 우선.
        여러 후보가 필요하면 lookup_corp_code_all() 사용.

        Args:
            query: 종목코드(6자리), corp_code(8자리), 회사명, 약칭, 영문명

        Returns:
            {"corp_code": ..., "corp_name": ..., "stock_code": ..., "modify_date": ...} 또는 None
        """
        results = await self.lookup_corp_code_all(query)
        if not results:
            return None
        resolution = results[0].get("_resolution") or {}
        if resolution.get("inferred") and not resolution.get("auto_selected"):
            return None
        # **확정된** 것만 적는다 — 후보 목록(lookup_corp_code_all)은 적지 않는다.
        # 「무엇을 조사했나」는 서버가 그 기업이라고 결론 낸 것이지, 스쳐간 후보가 아니다.
        _note_corp(results[0].get("corp_code"))
        return results[0]

    async def lookup_corp_code_all(self, query: str) -> list[dict]:
        """query에 매칭되는 기업 전체 목록 반환 (우선순위 정렬)

        우선순위: 식별자 → curated alias → 공식명 → 정규화 → token/부분/fuzzy.
        같은 tier에서는 최신 KRX 활성 여부와 시총 prior를 사용한다.
        """
        from open_proxy_mcp.company_resolver import get_company_resolver

        corps = await self._load_corp_codes()
        resolver = await get_company_resolver(corps, _CORP_ALIASES,
                                              await self.periodic_filers())
        return resolver.search(query)

    async def suggest_corp_candidates(self, query: str, limit: int = 5) -> list[dict]:
        """조회 실패 시 보여줄 근접 후보. 자동 선택이 아니라 사람이 고르게 하는 용도."""
        from open_proxy_mcp.company_resolver import get_company_resolver

        corps = await self._load_corp_codes()
        resolver = await get_company_resolver(corps, _CORP_ALIASES,
                                              await self.periodic_filers())
        return resolver.suggest(query, limit)

    async def get_naver_corp_profile(self, stock_code: str) -> dict:
        """NAVER 금융에서 업종명 조회 (웹 스크래핑)

        Returns:
            {"sector_name": "반도체와반도체장비", "sector_code": "278"} 또는 {}
        """
        try:
            await asyncio.sleep(2.0)  # 웹 스크래핑 최소 간격
            r = await self._http.get(
                f"https://finance.naver.com/item/coinfo.naver?code={stock_code}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            # 업종 링크에서 no 추출
            m = re.search(r'sise_group_detail\.naver\?type=upjong&no=(\d+)', r.text)
            if not m:
                return {}
            sector_code = m.group(1)

            await asyncio.sleep(2.0)
            r2 = await self._http.get(
                f"https://finance.naver.com/sise/sise_group_detail.naver?type=upjong&no={sector_code}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            # title: "반도체와반도체장비 : Npay 증권"
            m2 = re.search(r'<title>([^:]+)\s*:', r2.text)
            sector_name = m2.group(1).strip() if m2 else ""
            return {"sector_name": sector_name, "sector_code": sector_code}
        except Exception:
            return {}

    # ── 기업 기본정보 ──

    async def get_company_info(self, corp_code: str) -> dict:
        """기업 기본정보 (company.json) — 대표이사, 결산월 등

        Returns:
            {"corp_name": ..., "ceo_nm": ..., "fiscal_month": ..., ...}
        """
        return await self._request("company.json", {"corp_code": corp_code})

    # ── 공시 검색 ──

    async def search_filings(
        self,
        bgn_de: str,
        end_de: str,
        pblntf_ty: str = "",
        pblntf_detail_ty: str = "",
        corp_code: str = "",
        corp_name: str = "",
        corp_cls: str = "",
        page_no: int = 1,
        page_count: int = 100,
        last_reprt_at: str = "",
    ) -> dict:
        """공시 검색 (list.json)

        Args:
            bgn_de: 검색 시작일 (YYYYMMDD)
            end_de: 검색 종료일 (YYYYMMDD)
            pblntf_ty: 공시유형 (A=정기, B=주요사항, E=기타공시 등)
            pblntf_detail_ty: 상세 공시유형 (I001=주요경영사항 등). 지정 시
                서버단에서 detail로 좁혀 page 수를 줄인다 (pblntf_ty보다 좁음).
            corp_code: DART 기업코드 (8자리)
            corp_name: 회사명 (부분 매치)
            corp_cls: 법인구분 (Y=유가, K=코스닥, N=코넥스, E=기타)
            page_no: 페이지 번호
            page_count: 페이지당 건수 (최대 100)
            last_reprt_at: "Y" 시 정정공시 자동 정리 (최종본만 반환).
                "" 또는 "N"이면 원본 + 정정 모두 반환 (default).

        Returns:
            {"list": [...], "total_count": ..., ...}
        """
        # 캐싱: corp_code 있고 page_no==1, page_count==100일 때만
        _cacheable = bool(corp_code) and not corp_name and not corp_cls and page_no == 1 and page_count == 100
        if _cacheable:
            _cache_key = f"{corp_code}|{bgn_de}|{end_de}|{pblntf_ty}|{pblntf_detail_ty}|{last_reprt_at}"
            if _cache_key in self._search_cache:
                return self._search_cache[_cache_key]

        params = {
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_no": str(page_no),
            "page_count": str(page_count),
        }
        if pblntf_ty:
            params["pblntf_ty"] = pblntf_ty
        if pblntf_detail_ty:
            params["pblntf_detail_ty"] = pblntf_detail_ty
        if corp_code:
            params["corp_code"] = corp_code
        if corp_name:
            params["corp_name"] = corp_name
        if corp_cls:
            params["corp_cls"] = corp_cls
        if last_reprt_at in ("Y", "N"):
            params["last_reprt_at"] = last_reprt_at

        result = await self._request("list.json", params)

        if _cacheable:
            if len(self._search_cache) >= self._MAX_SEARCH_CACHE:
                self._search_cache.pop(next(iter(self._search_cache)))
            self._search_cache[_cache_key] = result

        return result

    async def search_filings_by_ticker(
        self,
        ticker: str,
        bgn_de: str,
        end_de: str,
        pblntf_ty: str = "",
        page_no: int = 1,
        page_count: int = 100,
    ) -> dict:
        """종목코드 또는 회사명으로 공시 검색 (편의 메서드)

        Args:
            ticker: 종목코드 (예: "033780") 또는 회사명 (예: "KT&G")
            bgn_de: 검색 시작일 (YYYYMMDD)
            end_de: 검색 종료일 (YYYYMMDD)
            pblntf_ty: 공시유형

        Returns:
            {"list": [...], "total_count": ..., "corp_info": {...}, ...}
        """
        corp = await self.lookup_corp_code(ticker)
        if not corp:
            raise DartClientError("404", f"'{ticker}'에 해당하는 기업을 찾을 수 없습니다.")

        result = await self.search_filings(
            bgn_de=bgn_de,
            end_de=end_de,
            pblntf_ty=pblntf_ty,
            corp_code=corp["corp_code"],
            page_no=page_no,
            page_count=page_count,
        )
        result["corp_info"] = corp
        return result

    # ── 공시 본문 ──

    async def get_document(self, rcept_no: str) -> dict:
        """공시 본문 텍스트 가져오기

        document.xml API로 ZIP 다운로드 → XML 추출 → 텍스트 변환
        이미지 파일명은 본문에서 제거하고 별도 목록으로 반환.

        Args:
            rcept_no: 접수번호

        Returns:
            {"text": 본문 텍스트, "images": [이미지 파일명 목록]}
        """
        import re

        data = await self._request_binary("document.xml", {"rcept_no": rcept_no})
        z = zipfile.ZipFile(io.BytesIO(data))

        # XML 파일 찾기. 사업보고서 등 큰 공시는 ZIP에 본문(`{rcept_no}.xml`) + 첨부
        # (`{rcept_no}_NNNNN.xml`)가 함께 들어있는데, 정렬상 첨부가 앞서 [0]을 읽으면 본문(임원보수·
        # 각주 등)을 놓친다(260709 실측: SK하이닉스 사업보고서 ZIP=본문8MB+첨부2, [0]은 첨부575KB).
        # 본문은 언더스코어 접미사 없는 `{rcept_no}.xml` → 그걸 우선, 없으면 가장 큰 XML, 최후 [0].
        xml_files = [f for f in z.namelist() if f.endswith(".xml")]
        if not xml_files:
            raise DartClientError("NO_DOC", "ZIP에 XML 문서가 없습니다.")

        main_name = f"{rcept_no}.xml"
        if main_name in xml_files:
            chosen = main_name
        elif len(xml_files) > 1:
            chosen = max(xml_files, key=lambda f: z.getinfo(f).file_size)
        else:
            chosen = xml_files[0]
        xml_content = z.read(chosen)

        # 인코딩 처리
        for encoding in ["utf-8", "euc-kr", "cp949"]:
            try:
                text_html = xml_content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text_html = xml_content.decode("utf-8", errors="replace")

        # 이미지 파일명 추출 (src 속성에서)
        images = re.findall(r'[\w\-./]+\.(?:jpg|jpeg|png|gif|bmp)', text_html, re.IGNORECASE)
        images = list(dict.fromkeys(images))  # 중복 제거, 순서 유지

        text = self._html_to_text(text_html, images=images)

        return {"text": text, "html": text_html, "images": images}

    def _html_to_text(self, html: str, images: list[str] | None = None) -> str:
        """HTML/XML을 파서 친화적인 평문으로 정규화 (모듈 함수 html_to_text 위임)."""
        return html_to_text(html, images=images)

    # ── Rate Limiting ──

    async def _throttle_api(self):
        """API 요청 분당 한도 hard guard (rolling window 60s).

        DART OpenAPI 분당 1000회 초과 시 그 키가 막힌다(실측 2~3시간). 실제 cap을 _API_RATE_LIMIT_PER_MINUTE
        (default 900)로 두어 10% buffer + batch 동시 호출 race 모두 cover.

        구조:
        1. 60s 윈도우 안의 timestamps deque 유지
        2. 윈도우 가득 차면 oldest 만료까지 sleep (하나 expire 후 진행)
        3. 그 외엔 _MIN_INTERVAL_API (race 방지) 만 강제
        """
        async with self._api_rate_lock:
            now = time.monotonic()
            # purge timestamps older than 60s
            while self._api_call_timestamps and now - self._api_call_timestamps[0] > 60:
                self._api_call_timestamps.popleft()
            # rate window 가득 — oldest expire까지 wait
            if len(self._api_call_timestamps) >= _API_RATE_LIMIT_PER_MINUTE:
                wait = 60 - (now - self._api_call_timestamps[0]) + 0.05
                if wait > 0:
                    logger.warning(
                        f"[DART API] rate limit window full ({_API_RATE_LIMIT_PER_MINUTE}/min) — wait {wait:.1f}s"
                    )
                    await asyncio.sleep(wait)
                    now = time.monotonic()
                    while self._api_call_timestamps and now - self._api_call_timestamps[0] > 60:
                        self._api_call_timestamps.popleft()
            # 최소 간격 (race 방지)
            elapsed = now - self._last_api_request
            if elapsed < _MIN_INTERVAL_API:
                await asyncio.sleep(_MIN_INTERVAL_API - elapsed)
                now = time.monotonic()
            self._api_call_timestamps.append(now)
            self._last_api_request = now

    async def _throttle_scrape(self, counter: str):
        """웹 스크래핑 공통 간격 — DART 웹과 KIND 가 **한 규칙, 한 시계**를 쓴다.

        ⚠️ 둘 다 공식 API가 아니다. 과도한 요청은 IP 차단이나 법적 문제로 이어질 수 있다.

        **API 한도와 격리 수준이 다르다** — API 는 키마다라 한 사용자가 넘겨도 그 사람만
        막히지만, 웹 차단은 IP 기준이라 **우리 서버 하나가 막히면 전원이 막힌다.**
        그래서 수치가 아니라 예의로 다룬다(공표된 한도가 없다 = 한도를 모른다).
        간격의 근거와 지켜야 할 셋은 `_WEB_INTERVAL_RANGE` 주석 참조.

        계기는 여기 둔다 — 웹 요청은 **전부** 이 함수를 지나므로 호출측이 빠뜨릴 수 없다.
        """
        import random
        _note_doc(counter)
        wait = random.uniform(*_WEB_INTERVAL_RANGE)
        elapsed = time.monotonic() - self._last_web_request
        if elapsed < wait:
            sleep_for = wait - elapsed
            logger.debug(f"[웹 스크래핑] {sleep_for:.1f}초 대기 ({counter})")
            await asyncio.sleep(sleep_for)
            _note_web_wait(sleep_for)
        self._last_web_request = time.monotonic()

    async def _throttle_web(self):
        """DART 웹 원문 viewer 용 — 간격은 `_throttle_scrape` 가 하나로 관리한다."""
        await self._throttle_scrape("fetch_viewer")

    # ── DART 웹 스크래핑 (viewer HTML 폴백용) ──
    # NOTE: _fetch_dcm_no (PDF 다운로드용 dcm_no 추출)는 2026-07-12 폐기 —
    #       get_document_pdf와 함께 open-proxy-ai로 이관.

    async def _fetch_viewer_main_html(self, rcept_no: str) -> str:
        """DART 메인 viewer 페이지 HTML을 가져온다."""
        await self._throttle_web()
        url = f"{DART_WEB_BASE_URL}/dsaf001/main.do?rcpNo={rcept_no}"

        response = await self._http.get(
            url,
            timeout=30,
            headers={
                "User-Agent": "OpenProxyMCP/1.0 (research; +https://github.com/MarcoYou/open-proxy-mcp)",
            },
        )
        response.raise_for_status()
        return response.text

    def _extract_viewer_nodes(self, main_html: str) -> list[dict]:
        """main.do의 목차 treeData 정의에서 viewer section 메타를 추출한다."""
        blocks = re.findall(
            r"var\s+node1\s*=\s*\{\};(.*?)treeData\.push\(node1\);",
            main_html,
            re.S,
        )
        nodes: list[dict] = []
        for block in blocks:
            values = {}
            for field in ("text", "rcpNo", "dcmNo", "eleId", "offset", "length", "dtd", "tocNo"):
                match = re.search(rf"\['{field}'\]\s*=\s*\"([^\"]*)\"", block)
                if match:
                    values[field] = match.group(1)
            if {"text", "rcpNo", "dcmNo", "eleId", "offset", "length", "dtd"} <= values.keys():
                nodes.append(values)
        return nodes

    async def _fetch_viewer_section_html(self, node: dict[str, str]) -> str:
        """report/viewer.do로 개별 section HTML을 가져온다."""
        await self._throttle_web()
        params = {
            "rcpNo": node["rcpNo"],
            "dcmNo": node["dcmNo"],
            "eleId": node["eleId"],
            "offset": node["offset"],
            "length": node["length"],
            "dtd": node["dtd"],
        }
        response = await self._http.get(
            f"{DART_WEB_BASE_URL}/report/viewer.do",
            params=params,
            timeout=30,
            headers={
                "User-Agent": "OpenProxyMCP/1.0 (research; +https://github.com/MarcoYou/open-proxy-mcp)",
            },
        )
        response.raise_for_status()
        return response.text

    async def get_viewer_document(
        self,
        rcept_no: str,
        section_keywords: list[str] | None = None,
    ) -> dict:
        """DART viewer HTML 크롤링 기반 본문.

        API/XML 구조가 깨졌을 때만 2차 경로로 사용한다.
        """
        keywords = tuple(section_keywords or [])
        cache_key = _viewer_key(rcept_no, keywords)
        cached = self._doc_cache.get(cache_key)
        if cached is not None:
            return cached

        main_html = await self._fetch_viewer_main_html(rcept_no)
        nodes = self._extract_viewer_nodes(main_html)
        if not nodes:
            raise DartClientError("NO_VIEWER_NODES", f"DART viewer 목차를 찾지 못했습니다. (rcept_no={rcept_no})")

        selected_nodes = nodes
        if keywords:
            lowered = [keyword.lower() for keyword in keywords]
            selected_nodes = [
                node for node in nodes
                if any(keyword in node.get("text", "").lower() for keyword in lowered)
            ] or nodes

        parts = []
        for node in selected_nodes:
            try:
                parts.append(await self._fetch_viewer_section_html(node))
            except Exception as exc:
                logger.warning(
                    f"[DART 웹] viewer section 조회 실패: rcept_no={rcept_no} "
                    f"eleId={node.get('eleId')} text={node.get('text')} err={exc}"
                )
        if not parts:
            raise DartClientError("NO_VIEWER_BODY", f"DART viewer 본문을 가져오지 못했습니다. (rcept_no={rcept_no})")

        html = "\n".join(parts)
        payload = {
            "text": self._html_to_text(html),
            "html": html,
            "nodes": [
                {
                    "text": node.get("text", ""),
                    "eleId": node.get("eleId", ""),
                    "tocNo": node.get("tocNo", ""),
                }
                for node in selected_nodes
            ],
            "source": "viewer_html",
        }
        self._doc_cache.put(cache_key, payload)
        return payload

    # NOTE: get_document_pdf (공시 본문 PDF 다운로드)는 2026-07-12 폐기.
    # OPM은 XML 단독 경로(get_document/get_document_cached)만 유지한다.
    # PDF 다운로드는 고급 프로덕트 open-proxy-ai(pipeline/pdf_download.py)로 이관했다.

    # ── Ownership API (DS002 정기보고서) ──

    async def get_major_shareholders(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """최대주주 현황 (hyslrSttus) — 최대주주+특수관계인 지분

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("hyslrSttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    async def get_major_shareholder_changes(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """최대주주 변동현황 (hyslrChgSttus)

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("hyslrChgSttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    async def get_minority_shareholders(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """소액주주 현황 (mrhlSttus)

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("mrhlSttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    async def get_stock_total(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """주식의 총수 현황 (stockTotqySttus) — 발행총수, 자기주식수, 유통주식수

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("stockTotqySttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    async def get_treasury_stock(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """자기주식 취득 및 처분 현황 (tesstkAcqsDspsSttus) — 기초/취득/처분/소각/기말

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("tesstkAcqsDspsSttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    # ── Director/board API (DS002 정기보고서 임원·보수 정형) ──

    async def get_executive_status(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """임원 현황 (exctvSttus) — 성명·직위·등기/미등기·상근/비상근·담당업무·재직기간·임기만료일

        연도간 명단 diff로 신규선임/사퇴(중도이탈) 감지에 쓴다(스냅샷이라 사유는 별도 수시공시).

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("exctvSttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    async def get_director_pay_limit(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """이사·감사 전체의 보수현황 - 주주총회 승인금액 (drctrAdtAllMendngSttusGmtsckConfmAmount)

        인원수(nmpr) + 주총 승인 보수총액(gmtsck_confm_amount = 한도). 새 주총 결의 없는 해엔
        공백("-")일 수 있어 최근 유효값 lookback 필요.

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("drctrAdtAllMendngSttusGmtsckConfmAmount.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    async def get_director_pay_actual(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """이사·감사 전체의 보수현황 - 유형별 지급금액 (drctrAdtAllMendngSttusMendngPymntamtTyCl)

        유형별(등기이사(사외·감사위 제외)/사외이사/감사위원 등) 인원(nmpr)·지급총액(pymnt_totamt)·
        1인평균 보수액(psn1_avrg_pymntamt)이 이미 계산되어 제공됨. 소진율 분자.

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("drctrAdtAllMendngSttusMendngPymntamtTyCl.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    async def get_individual_pay(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """이사·감사 개인별 보수현황 (hmvAuditIndvdlBySttus) — 5억원 이상 법정공개 대상만

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("hmvAuditIndvdlBySttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    async def get_unregistered_pay(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """미등기임원 보수현황 (unrstExctvMendngSttus) — se·인원(nmpr)·연급여총액·1인평균(jan_salary_am)

        등기이사 보수(get_director_pay_actual)와 합치면 경영진 전체 보수 그림. 미등기 집행임원은
        주총 승인한도 밖(등기 안 됨)이라 별개 지표.

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("unrstExctvMendngSttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    async def get_outside_director_changes(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """사외이사 및 그 변동현황 (outcmpnyDrctrNdChangeSttus) — 이사총수·사외이사총수·
        선임/해임/중도퇴임 인원(집계, 개별 성명은 없음).

        director_board의 roster scope(exctvSttus 연도간 diff, 이름 기반 추론)와 독립적인
        DART 공식 집계값 — diff 결과의 교차검증(sanity check)에 쓴다.

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("outcmpnyDrctrNdChangeSttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    async def get_employee_status(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """직원 현황 (empSttus) — 사업부문·성별 행별 인원(정규/계약)·1인평균급여·연급여총액

        이사 인당보수 ÷ 직원 평균급여 = 경영진-직원 보수 격차 배수(스튜어드십 신호) 계산에 쓴다.

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        return await self._request("empSttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    # ── Ownership API (DS004 수시보고) ──

    async def get_block_holders(self, corp_code: str) -> dict:
        """5% 대량보유 상황보고 (majorstock) — 전체 이력 반환, 날짜 필터 없음

        Args:
            corp_code: DART 기업코드 (8자리)
        """
        return await self._request("majorstock.json", {
            "corp_code": corp_code,
        })

    async def get_executive_holdings(self, corp_code: str) -> dict:
        """임원/주요주주 소유보고 (elestock) — 전체 이력 반환, 대량 데이터 주의

        Args:
            corp_code: DART 기업코드 (8자리)
        """
        return await self._request("elestock.json", {
            "corp_code": corp_code,
        })

    # ── Ownership API (DS005 주요사항보고서) ──

    async def get_treasury_acquisition(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """자기주식 취득 결정 (tsstkAqDecsn)

        Args:
            corp_code: DART 기업코드 (8자리)
            bgn_de: 검색 시작일 (YYYYMMDD)
            end_de: 검색 종료일 (YYYYMMDD)
        """
        return await self._request("tsstkAqDecsn.json", {
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
        })

    async def get_treasury_disposal(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """자기주식 처분 결정 (tsstkDpDecsn)

        Args:
            corp_code: DART 기업코드 (8자리)
            bgn_de: 검색 시작일 (YYYYMMDD)
            end_de: 검색 종료일 (YYYYMMDD)
        """
        return await self._request("tsstkDpDecsn.json", {
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
        })

    async def get_treasury_trust_contract(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """자기주식취득 신탁계약 체결 결정 (tsstkAqTrctrCnsDecsn)

        Args:
            corp_code: DART 기업코드 (8자리)
            bgn_de: 검색 시작일 (YYYYMMDD)
            end_de: 검색 종료일 (YYYYMMDD)
        """
        return await self._request("tsstkAqTrctrCnsDecsn.json", {
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
        })

    async def get_treasury_trust_termination(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """자기주식취득 신탁계약 해지 결정 (tsstkAqTrctrCcDecsn)

        Args:
            corp_code: DART 기업코드 (8자리)
            bgn_de: 검색 시작일 (YYYYMMDD)
            end_de: 검색 종료일 (YYYYMMDD)
        """
        return await self._request("tsstkAqTrctrCcDecsn.json", {
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
        })

    # ── Corporate Restructuring API (DS005 주요사항보고서) ──

    async def get_merger_decision(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """회사합병결정 (cmpMgDecsn) — 합병비율, 상대방, 신주, 외부평가, 매수청구권."""
        return await self._request("cmpMgDecsn.json", {
            "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de,
        })

    async def get_division_decision(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """회사분할결정 (cmpDvDecsn) — 분할방법, 분할비율, 신설/존속회사, 재상장 여부."""
        return await self._request("cmpDvDecsn.json", {
            "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de,
        })

    async def get_division_merger_decision(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """회사분할합병결정 (cmpDvmgDecsn) — 분할 후 합병 동시 결정."""
        return await self._request("cmpDvmgDecsn.json", {
            "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de,
        })

    async def get_stock_exchange_decision(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """주식교환·이전 결정 (stkExtrDecsn) — 교환종류, 비율, 대상회사, 일정, 매수청구권."""
        return await self._request("stkExtrDecsn.json", {
            "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de,
        })

    # ── Dilutive Issuance API (DS005 주요사항보고서) ──

    async def get_rights_offering_decision(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """유상증자 결정 (piicDecsn) — 발행주식수, 배정방식, 자금조달 목적."""
        return await self._request("piicDecsn.json", {
            "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de,
        })

    async def get_convertible_bond_decision(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """전환사채 발행결정 (cvbdIsDecsn) — 전환가, 전환비율, 잠재희석, 만기, 풋옵션."""
        return await self._request("cvbdIsDecsn.json", {
            "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de,
        })

    async def get_warrant_bond_decision(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """신주인수권부사채 발행결정 (bdwtIsDecsn) — 행사가, 분리/비분리, 신주 발행 조건."""
        return await self._request("bdwtIsDecsn.json", {
            "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de,
        })

    async def get_capital_reduction_decision(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """감자결정 (crDecsn) — 감자비율, 자본금 전/후, 감자 방법·사유, 일정."""
        return await self._request("crDecsn.json", {
            "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de,
        })

    async def get_exchangeable_bond_decision(self, corp_code: str, bgn_de: str, end_de: str) -> dict:
        """교환사채권 발행결정 (exbdIsDecsn) — 교환가액, 교환대상(자기주식 등), 교환비율, 만기.

        ⚠️ 정정·철회된 EB는 DART 구조화 응답이 최신본(철회)만 반환하며 교환 조건이
        비어 있을 수 있다. 이 경우 service 레이어가 원본 문서를 파싱해 복원한다.
        """
        return await self._request("exbdIsDecsn.json", {
            "corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de,
        })

    # ── Dividend API (DS002 정기보고서) ──

    async def get_dividend_info(self, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
        """배당에 관한 사항 (alotMatter) — 배당금, 배당률, 기준일

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
        """
        # 과거 연도(2년 전 이전)는 사업보고서 확정 후 안 변하므로 캐시 대상.
        # 당해/전년은 정정 가능성 있어 캐시 X.
        # 값이 안 변한다고 **무제한**으로 들고 있을 이유는 없다 — 상장 2,700사 × 캐시 대상
        # 연도면 상한이 150MB 대다(260804 OOM 조사). 바이트 예산 LRU + TTL 로 문서 캐시와
        # 같은 규율을 적용한다. evict 돼도 다음 조회 때 alotMatter 1콜이면 복구된다.
        from datetime import date as _date
        cacheable = reprt_code == "11011" and bsns_year.isdigit() and int(bsns_year) <= _date.today().year - 2
        cache_key = f"{corp_code}|{bsns_year}|{reprt_code}"
        if cacheable:
            cached = self._dividend_cache.get(cache_key)
            if cached is not None:
                return cached
        result = await self._request("alotMatter.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })
        if cacheable:
            self._dividend_cache.put(cache_key, result)
        return result

    # ── 재무제표 / 주요지표 / 감사의견 (DS003) ──

    async def get_fnltt_singl_acnt(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str = "11011",
        fs_div: str = "CFS",
    ) -> dict:
        """단일회사 주요계정 (fnlttSinglAcnt) — 재무상태표 + 손익계산서 핵심.

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
            fs_div: CFS(연결, 한국 표준 default) / OFS(별도)
        """
        return await self._request("fnlttSinglAcnt.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        })

    async def get_fnltt_singl_indx(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str = "11011",
        idx_cl_code: str = "M210000",
    ) -> dict:
        """단일회사 주요 재무지표 (fnlttSinglIndx) — DART 산출 ROE/ROA/부채비율 등.

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
            idx_cl_code: 지표분류 — M210000(수익성), M220000(안정성), M230000(성장성), M240000(활동성)
        """
        return await self._request("fnlttSinglIndx.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
            "idx_cl_code": idx_cl_code,
        })

    async def get_fnltt_singl_acnt_all(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str = "11011",
        fs_div: str = "CFS",
    ) -> dict:
        """단일회사 전체 재무제표 (fnlttSinglAcntAll) — 현금흐름표 + 자본변동표 포함.

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업), 11012(반기), 11013(1분기), 11014(3분기)
            fs_div: CFS(연결, default) / OFS(별도)
        """
        return await self._request("fnlttSinglAcntAll.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        })

    async def get_other_corp_investment(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str = "11011",
    ) -> dict:
        """타법인 출자현황 (otrCprInvstmntSttus) — 사업보고서 XII.상세표. 표준화 구조화 표.

        지분증권 보유의 **표준 소스**(재무주석 명세보다 깨끗). 컬럼:
          inv_prm(법인명) · frst_acqs_amount(최초취득금액=원가) · trmend_blce_acntbk_amount(기말장부가액)
          · trmend_blce_qota_rt(기말지분율) · incrs_dcrs_evl_lstmn(평가손익)
          · recent_bsns_year_fnnr_sttus_tot_assets(피투자사 총자산). 상장=장부가액≈공정가치.
        자산저평가 지분증권 트랙의 원가 vs 시가 gap을 파싱 없이 확보. 필드 상세는 opendart 공식 가이드.
        """
        return await self._request("otrCprInvstmntSttus.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    async def get_audit_opinion(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str = "11011",
    ) -> dict:
        """회계감사인 + 감사의견 (accnutAdtorNmNdAdtOpinion) — 감사인/의견/강조사항/KAM.

        사업보고서 기준만 의미 있음 (반기/분기는 감사 없음).

        Args:
            corp_code: DART 기업코드 (8자리)
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 11011(사업)이 표준. 반기/분기는 감사의견 없음.
        """
        return await self._request("accnutAdtorNmNdAdtOpinion.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })

    # ── 주가 시세 조회 (네이버 금융 → KRX fallback) ──

    async def get_stock_price(self, stock_code: str, base_date: str) -> dict | None:
        """특정 종목의 일별 시세 (종가). 네이버 금융 우선, KRX Open API fallback.

        Args:
            stock_code: 종목코드 6자리 (예: "005930")
            base_date: 기준일 YYYYMMDD (예: "20260404")

        Returns:
            {"closing_price": int, "base_date": str, "source": str}
            또는 None (데이터 없음)
        """
        # 1차: KRX Open API (공식)
        result = await self._krx_stock_price(stock_code, base_date)
        if result:
            return result

        # 2차: 네이버 금융 (fallback)
        result = await self._naver_stock_price(stock_code, base_date)
        if result:
            return result

        return None

    async def _naver_stock_price(self, stock_code: str, base_date: str) -> dict | None:
        """네이버 금융 시세 API — 일별 종가"""
        try:
            await self._throttle_api()
            url = "https://api.finance.naver.com/siseJson.naver"
            params = {
                "symbol": stock_code,
                "requestType": "1",
                "startTime": base_date,
                "endTime": base_date,
                "timeframe": "day",
            }
            resp = await self._http.get(url, params=params, timeout=15,
                                   headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return None

            # 응답 파싱: [["날짜","시가","고가","저가","종가","거래량","외국인소진율"],\n["20251230",119100,121200,118700,119900,...]]
            import re as _re
            rows = _re.findall(r'\["(\d{8})",\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)', resp.text)
            if rows:
                date_str, open_p, high, low, close = rows[0]
                return {
                    "closing_price": int(close),
                    "base_date": date_str,
                    "source": "naver",
                }

            # 해당 날짜 데이터 없으면 (비거래일) — 범위 넓혀서 직전 거래일
            start = str(int(base_date) - 7)  # 7일 전부터
            params["startTime"] = start
            resp2 = await self._http.get(url, params=params, timeout=15,
                                    headers={"User-Agent": "Mozilla/5.0"})
            rows2 = _re.findall(r'\["(\d{8})",\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)', resp2.text)
            if rows2:
                # 마지막 행이 가장 최근
                date_str, open_p, high, low, close = rows2[-1]
                return {
                    "closing_price": int(close),
                    "base_date": date_str,
                    "source": "naver",
                }
            return None
        except Exception as e:
            logger.warning(f"[네이버] 시세 조회 실패: {e}")
            return None

    async def _krx_stock_price(self, stock_code: str, base_date: str) -> dict | None:
        """KRX Open API — 일별 시세 (서비스 승인 필요)"""
        import os
        api_key = os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY")
        if not api_key:
            return None

        try:
            await self._throttle_api()
            try:
                from open_proxy_mcp.dart.krx_meter import bump
                bump()  # KRX 일별 사용량 장부
            except Exception:
                pass
            url = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
            params = {"AUTH_KEY": api_key, "basDd": base_date}
            resp = await self._http.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                return None
            data = resp.json()
            for item in data.get("OutBlock_1", []):
                isu_cd = item.get("ISU_CD", "")
                if isu_cd == stock_code or stock_code in isu_cd:
                    return {
                        "closing_price": int(str(item.get("TDD_CLSPRC", "0")).replace(",", "") or "0"),
                        "base_date": item.get("BAS_DD", base_date),
                        "source": "krx",
                    }
            return None
        except Exception as e:
            logger.warning(f"[KRX] 시세 조회 실패: {e}")
            return None

    # ── 네이버 뉴스 검색 API ──

    async def naver_news_search(self, query: str, display: int = 100, sort: str = "date") -> list[dict]:
        """네이버 뉴스 검색 API

        Args:
            query: 검색어 (예: '"김용관" "삼성전자"')
            display: 결과 수 (최대 100)
            sort: "date" (최신순) 또는 "sim" (정확도순)

        Returns:
            [{"title", "link", "originallink", "description", "pubDate"}, ...]
        """
        client_id = os.getenv("NAVER_SEARCH_API_CLIENT_ID")
        client_secret = os.getenv("NAVER_SEARCH_API_CLIENT_SECRET")
        if not client_id or not client_secret:
            logger.warning("[네이버] 검색 API 키가 설정되지 않았습니다")
            return []

        await self._throttle_api()
        # 260820: 개발자센터 → NAVER API HUB 이관. 도메인·경로·헤더가 **셋 다** 바뀐다.
        #   openapi.naver.com/v1/search/news.json  →  naverapihub.apigw.ntruss.com/search/v1/news
        #   X-Naver-Client-Id / -Secret            →  X-NCP-APIGW-API-KEY-ID / -KEY
        # 구 방식은 2027-06-30 까지만 지원되고, HUB 키로는 구 방식이 아예 401 이다(실측).
        # 응답 items 필드(title·originallink·link·description·pubDate)는 그대로라 파서는 유지.
        url = "https://naverapihub.apigw.ntruss.com/search/v1/news"
        params = {"query": query, "display": display, "sort": sort}
        headers = {
            "X-NCP-APIGW-API-KEY-ID": client_id,
            "X-NCP-APIGW-API-KEY": client_secret,
        }

        try:
            resp = await self._http.get(url, params=params, headers=headers, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"[네이버] HTTP {resp.status_code}: {resp.text[:200]}")
                return []
            data = resp.json()
            return data.get("items", [])
        except Exception as e:
            logger.warning(f"[네이버] 뉴스 검색 실패: {e}")
            return []

    # ── KRX KIND 크롤링 ──

    async def _throttle_kind(self):
        """KIND 용 — DART 웹과 **같은 규칙·같은 시계**를 쓴다(260810 통일).
        종전엔 여기만 1~3초 랜덤이라 DART 웹의 고정 2초와 갈려 있었다."""
        await self._throttle_scrape("fetch_kind")

    async def kind_fetch_document(self, acptno: str) -> str:
        """KIND에서 공시 본문 HTML 가져오기 (3단계 iframe 크롤링)

        1. 메인 페이지에서 docNo 추출
        2. searchContents에서 본문 URL 추출
        3. 본문 HTML 다운로드

        Args:
            acptno: 접수번호 (예: "20260130000495")

        Returns:
            본문 HTML 텍스트
        """
        kind_base = "https://kind.krx.co.kr"
        headers = {
            "User-Agent": "OpenProxyMCP/1.0 (research; +https://github.com/MarcoYou/open-proxy-mcp)",
        }

        # Step 1: 메인 페이지 → docNo 추출
        await self._throttle_kind()
        url1 = f"{kind_base}/common/disclsviewer.do"
        resp1 = await self._http.get(url1, params={
            "method": "search", "acptno": acptno,
        }, timeout=30, headers=headers)
        resp1.raise_for_status()

        # <select id="mainDoc"> 안의 <option value="docNo|Y">
        m = re.search(r"<option[^>]+value=['\"](\d+)\|?[^'\"]*['\"]", resp1.text)
        if not m:
            raise DartClientError("KIND_NO_DOC", f"KIND에서 docNo를 찾을 수 없습니다 (acptno={acptno})")
        doc_no = m.group(1)

        # Step 2: searchContents → 본문 URL 추출
        await self._throttle_kind()
        resp2 = await self._http.get(url1, params={
            "method": "searchContents", "docNo": doc_no,
        }, timeout=30, headers=headers)
        resp2.raise_for_status()

        # setPath('목차URL', '본문URL') — 두 번째 인자가 본문 (목차가 빈 문자열일 수 있음)
        m2 = re.search(r"setPath\s*\(\s*'([^']*)'\s*,\s*'([^']+)'", resp2.text)
        if not m2:
            raise DartClientError("KIND_NO_PATH", f"KIND에서 본문 URL을 찾을 수 없습니다 (docNo={doc_no})")
        body_path = m2.group(2)

        # Step 3: 본문 HTML 다운로드
        await self._throttle_kind()
        body_url = f"{kind_base}{body_path}" if body_path.startswith("/") else body_path
        resp3 = await self._http.get(body_url, timeout=30, headers=headers)
        resp3.raise_for_status()

        logger.info(f"[KIND] 본문 다운로드 완료: {len(resp3.text):,} chars (acptno={acptno})")
        return resp3.text

    @staticmethod
    def _kind_strip_html(fragment: str) -> str:
        text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = unescape(text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _parse_kind_disclosure_rows(self, html: str) -> list[dict]:
        rows: list[dict] = []
        for match in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.IGNORECASE | re.DOTALL):
            row_html = match.group(1)
            acptno_match = re.search(
                r"openDisclsViewer\('(\d+)'\s*,\s*''\)",
                row_html,
                re.IGNORECASE,
            )
            if not acptno_match:
                continue
            acptno = acptno_match.group(1)
            td_matches = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.IGNORECASE | re.DOTALL)
            cells = [self._kind_strip_html(cell) for cell in td_matches]
            if len(cells) < 5:
                continue
            company_match = re.search(
                r"id=['\"]companysum['\"][^>]*title=['\"]([^'\"]+)['\"]",
                row_html,
                re.IGNORECASE,
            )
            report_match = re.search(
                r"openDisclsViewer\('\d+'\s*,\s*''\)[^>]*title=['\"]([^'\"]+)['\"]",
                row_html,
                re.IGNORECASE,
            )
            company_name = company_match.group(1).strip() if company_match else cells[2]
            report_name = report_match.group(1).strip() if report_match else cells[3]
            rows.append({
                "acptno": acptno,
                "disclosure_datetime": cells[1],
                "disclosure_date": cells[1][:10].replace("-", ""),
                "corp_name": company_name,
                "report_name": report_name,
                "filer_name": cells[4],
            })
        return rows

    async def kind_search_disclosures(
        self,
        *,
        stock_code: str,
        corp_name: str,
        from_date: str,
        to_date: str,
        disclosure_type_code: str,
    ) -> list[dict]:
        """KIND 상세검색에서 특정 공시분류를 검색.

        Args:
            stock_code: 종목코드 6자리
            corp_name: 회사명
            from_date: YYYY-MM-DD
            to_date: YYYY-MM-DD
            disclosure_type_code: KIND 공시세부코드 (예: 0184=기업가치 제고 계획)
        """
        kind_base = "https://kind.krx.co.kr"
        headers = {
            "User-Agent": "OpenProxyMCP/1.0 (research; +https://github.com/MarcoYou/open-proxy-mcp)",
        }
        payload = {
            "method": "searchDetailsSub",
            "forward": "details_sub",
            "searchCorpName": corp_name,
            "oldSearchCorpName": corp_name,
            "repIsuSrtCd": f"A{stock_code}" if stock_code else "",
            "allRepIsuSrtCd": f"A{stock_code}" if stock_code else "",
            "fromDate": from_date,
            "toDate": to_date,
            "currentPageSize": "100",
            "pageIndex": "1",
            "disclosureType01": disclosure_type_code,
            "pDisclosureType01": disclosure_type_code,
            "disclosureTypeArr01": disclosure_type_code,
        }

        await self._throttle_kind()
        response = await self._http.post(
            f"{kind_base}/disclosure/details.do",
            data=payload,
            timeout=30,
            headers=headers,
        )
        response.raise_for_status()

        return self._parse_kind_disclosure_rows(response.text)

    async def kind_search_value_up(
        self,
        *,
        stock_code: str,
        corp_name: str,
        from_date: str,
        to_date: str,
    ) -> list[dict]:
        """KIND에서 기업가치 제고 계획 공시 검색."""
        return await self.kind_search_disclosures(
            stock_code=stock_code,
            corp_name=corp_name,
            from_date=from_date,
            to_date=to_date,
            disclosure_type_code=_KIND_VALUE_UP_DISCLOSURE_CODE,
        )

    # ── Document Caching ──

    def _disk_cache_path(self, rcept_no: str) -> str:
        """새로 쓰는 자리 — **gzip**. 옛 평문(.json)은 `_disk_cache_paths` 가 같이 본다."""
        return os.path.join(self._disk_cache_dir, f"{rcept_no}.json.gz")

    def _disk_cache_paths(self, rcept_no: str) -> tuple[str, str]:
        """(gzip 경로, 옛 평문 경로). 260823 압축 전환 — 기존 캐시를 버리지 않는다."""
        base = os.path.join(self._disk_cache_dir, rcept_no)
        return f"{base}.json.gz", f"{base}.json"

    def _load_from_disk(self, rcept_no: str) -> dict | None:
        """적중하면 **mtime 을 지금으로 올린다** — 청소가 LRU 로 돌게 하는 장치다.

        안 올리면 mtime 은 「처음 쓴 때」로 굳어 청소가 FIFO 가 된다. 그러면 매일 읽히는
        문서도 오래됐다는 이유로 나가고, 나가자마자 DART 왕복으로 다시 받아 온다.
        메모리 캐시는 이미 LRU 인데(`get()` 이 맨 뒤로 옮긴다) 디스크만 FIFO 면
        두 층의 규칙이 어긋난다.

        깨진 파일은 **지우고 miss 로 취급**한다. `/tmp` 시절엔 부분 파일이 배포 때
        사라져 저절로 나았지만 볼륨에서는 안 낫는다 — 한 번 잘린 json 이 그 rcept_no 를
        **영구히** 못 읽게 만든다."""
        gz_path, legacy_path = self._disk_cache_paths(rcept_no)
        for path, opener in ((gz_path, lambda p: gzip.open(p, "rt", encoding="utf-8")),
                             (legacy_path, lambda p: open(p, "r", encoding="utf-8"))):
            try:
                with opener(path) as f:
                    doc = json.load(f)
                try:
                    os.utime(path, None)    # 적중 = 최근 사용. 실패해도 캐시는 유효하다
                except OSError:
                    pass
                return doc
            except FileNotFoundError:
                continue                    # gz 없으면 옛 평문을 본다(전환기)
            except (json.JSONDecodeError, OSError, UnicodeDecodeError, EOFError) as e:
                # gzip 은 잘리면 EOFError/BadGzipFile 로 온다 — 평문 때와 같이 지우고 miss 처리
                logger.warning(f"disk cache 손상 — 삭제하고 다시 받는다: {rcept_no} ({e})")
                try:
                    os.remove(path)
                except OSError:
                    pass
                return None
        return None

    def _save_to_disk(self, rcept_no: str, doc: dict):
        """임시 파일에 쓰고 rename 한다 — 쓰다 죽어도 **부분 파일이 캐시로 읽히지 않게**."""
        try:
            os.makedirs(self._disk_cache_dir, exist_ok=True)
            path = self._disk_cache_path(rcept_no)
            tmp = f"{path}.{os.getpid()}.tmp"
            # 260823 gzip 전환. 금융사 정기보고서가 20~42MB 라 평문으로 두면 볼륨이 금방 찬다
            # (실측 42.2MB → 2.0MB, 4%). 푸는 비용은 0.01초라 읽기 경로에 영향이 없다.
            # level 6(기본) — 9 로 올려도 공시 문서는 1%p 남짓 더 줄고 쓰기만 느려진다.
            with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
                json.dump(doc, f, ensure_ascii=False)
            os.replace(tmp, path)               # 같은 파일시스템 내 원자적 교체
            written = os.path.getsize(path)
            # 옛 평문 사본이 남아 있으면 지운다 — 같은 문서를 두 벌 들고 있을 이유가 없다
            legacy = self._disk_cache_paths(rcept_no)[1]
            if os.path.exists(legacy):
                try:
                    os.remove(legacy)
                except OSError:
                    pass
        except OSError as e:
            logger.warning(f"disk cache 쓰기 실패(무시): {rcept_no} ({e})")
            return
        _sweep_disk_cache(written)

    async def get_document_cached(self, rcept_no: str) -> dict:
        """get_document 결과를 캐싱 (메모리 바이트예산 LRU + TTL 24h, 디스크는 보조).
        중복 API 호출 방지. 메모리에서 evict 돼도 디스크가 받아주므로 **DART 왕복은 안 는다**
        — 예산을 보수적으로 잡아도 되는 이유다."""
        cache_key = _doc_key(rcept_no)
        cached = self._doc_cache.get(cache_key)
        if cached is not None:
            _note_doc("doc_mem_hits")
            return cached
        disk_doc = self._load_from_disk(rcept_no)
        if disk_doc:
            self._doc_cache.put(cache_key, disk_doc)
            _note_doc("doc_disk_hits")         # 메모리 예산과 무관 — 따로 센다
            return disk_doc
        _note_doc("doc_misses")                # DART 왕복 — fetch 가 병목인 구간
        doc = await self.get_document(rcept_no)
        self._doc_cache.put(cache_key, doc)
        self._save_to_disk(rcept_no, doc)
        # 이미지 기반 공고 감지
        images = doc.get("images", [])
        notice_images = [img for img in images if any(
            kw in img for kw in ["소집", "통지", "주총", "공고"]
        )]
        if notice_images:
            logger.warning(
                f"[IMAGE_NOTICE] 소집공고 본문이 이미지에 포함된 것으로 추정: "
                f"{rcept_no} | images={notice_images}"
            )
        return doc


# ── Client Factory ──

_instances: dict[str, "DartClient"] = {}

def get_dart_client() -> DartClient:
    """DartClient 팩토리 — API 키별 인스턴스 캐싱, 전 tool에서 throttle 공유"""
    ctx_key = _ctx_opendart_key.get()
    cache_key = ctx_key or os.getenv("OPENDART_API_KEY") or "__default__"
    if cache_key not in _instances:
        _instances[cache_key] = DartClient()
    return _instances[cache_key]
