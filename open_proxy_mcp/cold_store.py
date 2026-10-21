# -*- coding: utf-8 -*-
"""콜드 서빙 — DB 밖 보관본(서빙본 sqlite)을 받아 두고 읽는다. DB 공간 0 으로 옛 구간을 DB 행과 이어 붙이기 위한 것.

261019 신설. 첫 데이터셋은 `krx_weekly_hist` — 주간 시세(`krx_weekly`)의 보존 하한(최신 주의 달력 3년 전) 이전 전 행이다.
DB 는 하한 이전을 종목별 연말 행만 남기므로(`krx_retention`), 그 앞의 주간 곡선은 이 서빙본에서 읽는다.
261021: 집계 두 벌을 더했다 — `krx_cap_agg_hist`(시장·업종 시총 집계)·`opm_val_market_hist`(시장·업종 배수). 열쇠는 scheme 하나라
(as_of 단면을 한 번에 읽는다) 시장·버킷·섹터는 호출부(`price_multiple_data._read_agg`)가 거른다. 읽기 API 는 그대로다.

■ 무엇을 믿나 — 서빙 목록(manifest) 하나
  `OPM_COLD_MANIFEST_URL`(비밀값 — 서명 URL, 로그에는 쿼리를 뗀 경로만) 또는 `file://` 경로(로컬·시험).
  목록 모양(schema 1, kind `opm-cold-serve`):
    datasets.<이름> = {engine: "sqlite3", view, columns, key, date_col,
                      files: [{file, object, encoding("gzip"|없음), sha256(풀어낸 sqlite), bytes, object_sha256, object_bytes,
                               rows, coverage: {min_dd, max_dd, upto}, url(서명 URL), url_expires_at, file_schema}],
                      coverage: {min_dd, max_dd}}
  `coverage.max_dd` = 이 날까지(포함) 원천의 모든 행이 서빙본에 있다는 약속. 파일 이름에 sha 가 들어 있어(내용 주소) 덮어쓰기가 없다.
  만들고 서명하고 목록을 쓰는 쪽은 서버 밖(운영 발행기)이다. 서버는 읽기만 한다.

■ 받기 — `/data` 볼륨에 캐시(`OPM_COLD_DIR`, 기본 /data/cold)
  · 파일(sha)마다 한 번만 받는다(프로세스 안 단일 비행 + 디렉터리 `flock`). 같은 판이 디스크에 있으면 sha 만 다시 보고 재사용한다.
  · 스트림으로 받아 gzip 을 풀며 객체 sha256·풀어낸 sha256 을 함께 센다 → 둘 다 목록과 같고, sqlite 머리(application_id·user_version)가
    맞을 때만 임시 파일을 `os.replace` 로 바꿔 끼운다. 메모리는 조각(1 MiB) 단위만 쓴다.
  · 목록에 없는 옛 판 파일은 새 판을 받은 뒤 지운다. 디스크 상한(`OPM_COLD_MAX_MB`, 기본 128)·여유 공간을 넘으면 받지 않는다.
  · 요청 중 받기가 필요하면 `OPM_COLD_WAIT_SEC`(기본 4초)까지만 기다린다. 넘으면 그 요청만 강등하고 받기는 배경에서 계속한다.
  · 목록은 TTL 6시간(`OPM_COLD_MANIFEST_TTL_SEC`)마다 배경에서 새로 받는다. 새로 받는 데 실패하면 마지막 성공본을 쓰되,
    `OPM_COLD_MANIFEST_MAX_AGE_SEC`(기본 72시간)보다 오래 묵으면 쓰지 않는다 — 발행기가 coverage 를 넓힌 뒤 DB 쪽 행을 지우면
    묵은 목록의 이음매가 틀어지기 때문이다(그래서 DB 롤링은 새 coverage 발행 뒤 이 시간보다 오래 기다린다).

■ 읽기 — 표준 라이브러리 sqlite3(의존성 0)
  `file:…?mode=ro&immutable=1`, 연결마다 page cache 2 MiB·mmap 끔, 호출마다 열고 닫는다(열기 0.3 ms). 동시 읽기 4개(세마포어).
  프로세스 RSS 가 `OPM_COLD_RSS_GUARD_MB`(기본 1,700)를 넘으면 읽지 않고 강등한다. 결과 캐시는 두지 않는다(읽기가 ms 이하).

■ 강등 — 전부 아니면 없음
  꺼짐(`OPM_COLD` ≠ "1", 기본 꺼짐)·비밀값 없음·목록을 못 받음(403·404·만료·시간 초과)·sha 불일치·디스크·RSS 가드 → `None`.
  호출부는 지금과 같은 응답(DB 하한 이후 곡선 + 하한 안내)을 준다. 빈 해가 섞인 부분 곡선은 내지 않는다.

■ 관측 — `stats()` 가 `/health` 의 `cold` 칸. URL·키는 싣지 않는다.

이음매 규칙(어디서 자르나)은 이 모듈이 아니라 `krx_retention.serving_seam` 과 호출부(`price_multiple_data._read_weekly`·`_read_agg`)에 있다.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import datetime as _dt
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import zlib
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote, unquote, urlsplit

logger = logging.getLogger(__name__)

MANIFEST_SCHEMA = 1
MANIFEST_KIND = "opm-cold-serve"
APP_ID = 0x4F504D43            # 'OPMC' — 서빙본 sqlite 머리. 엉뚱한 파일을 열지 않는다.
FILE_SCHEMA = 1
_CHUNK = 1 << 20

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FILE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.sqlite")
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_DD_RE = re.compile(r"[0-9]{8}")


# ── 설정 ──────────────────────────────────────────────────────────────────────
def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, "") or default)
        return v if v > 0 else default
    except ValueError:                 # 오타 하나로 서버가 못 뜨면 안 된다
        return default


def enabled() -> bool:
    """콜드 서빙을 쓰나 — `OPM_COLD=1` 일 때만. 기본 꺼짐(배포만으로 동작이 바뀌지 않는다)."""
    return os.environ.get("OPM_COLD", "0").strip() == "1"


def _manifest_url() -> str:
    return (os.environ.get("OPM_COLD_MANIFEST_URL") or "").strip()


def _cold_dir() -> Path:
    return Path(os.environ.get("OPM_COLD_DIR") or "/data/cold")


def _wait_sec() -> float:
    return _env_float("OPM_COLD_WAIT_SEC", 4.0)


def _max_bytes() -> int:
    return int(_env_float("OPM_COLD_MAX_MB", 128.0) * 1048576)


def _rss_guard_mb() -> float:
    return _env_float("OPM_COLD_RSS_GUARD_MB", 1700.0)


def _ttl_sec() -> float:
    return _env_float("OPM_COLD_MANIFEST_TTL_SEC", 6 * 3600.0)


def _max_age_sec() -> float:
    return _env_float("OPM_COLD_MANIFEST_MAX_AGE_SEC", 72 * 3600.0)


#: 시험이 HTTP 를 바꿔 끼우는 자리(httpx 전송 계층). 운영은 None(기본 전송).
_TRANSPORT = None


def _scrub(text: str, *urls: str) -> str:
    """오류 문장에서 URL 을 통째로 지운다 — `/health` 는 인증 없이 열려 있고, 저장소 주소(인프라 좌표)·서명 토큰은 비공개다."""
    s = str(text)
    for url in urls:
        if not url:
            continue
        s = s.replace(url, "<url>")
        try:
            u = urlsplit(url)
        except ValueError:
            continue
        for part in (u.query, u.netloc, u.path):
            if part and len(part) > 3:
                s = s.replace(part, "<url>")
    return re.sub(r"https?://\S+", "<url>", s)[:200]


# ── 상태 ──────────────────────────────────────────────────────────────────────
class Ready(NamedTuple):
    """읽을 준비가 된 데이터셋 하나 — 검증된 로컬 파일 + 목록이 약속한 coverage."""
    dataset: str
    path: str
    view: str
    columns: tuple[str, ...]
    key: str
    date_col: str
    min_dd: str
    max_dd: str
    sha256: str


class ColdError(RuntimeError):
    """강등 까닭 — `reason` 은 짧은 영문 코드(관측·시험용), 문장은 로그용."""

    def __init__(self, reason: str, msg: str = ""):
        super().__init__(msg or reason)
        self.reason = reason


_lock = threading.Lock()                 # 아래 상태 dict 들을 스레드 사이에서 지킨다
_state: dict[str, Any] = {
    "manifest": None,          # 검증된 목록(dict)
    "manifest_sha": None,
    "manifest_fetched": 0.0,   # 성공한 받기의 시계(time.time)
    "manifest_error": None,
    "manifest_tries": 0,
}
_verified: dict[str, dict] = {}          # sha256 → {"path", "received_at", "bytes", "how"}
_bad: dict[str, tuple[str, float]] = {}  # sha256 → (검증 실패 까닭, 시각) — `_BAD_RETRY_SEC` 동안 그 판을 다시 받지 않는다
_BAD_RETRY_SEC = 3600.0
#: 받기 실패(403·400·네트워크·시간 한도) 뒤 같은 일을 다시 하기까지 — 요청마다 저장소를 두드리지 않게. 키 = "manifest" | 파일 sha.
_backoff: dict[str, tuple[str, float]] = {}
_BACKOFF_SEC = 300.0
_counters: dict[str, int] = {"reads": 0, "rows": 0, "downloads": 0, "download_bytes": 0, "verify_reuse": 0}
_degraded: dict[str, int] = {}
_read_ms: list[float] = []
_last_error: dict[str, Any] = {"reason": None, "at": None, "detail": None}

_EXEC: concurrent.futures.ThreadPoolExecutor | None = None
_loop_prims: dict[int, tuple[Any, asyncio.Semaphore, dict]] = {}   # id(loop) → (loop, 읽기 세마포어, 단일 비행 태스크)


def _executor() -> concurrent.futures.ThreadPoolExecutor:
    """목록 받기·파일 받기·sha 재확인 전용 스레드 1개 — DB 읽기 스레드 풀(`asyncio.to_thread`)을 오래 붙잡지 않는다."""
    global _EXEC
    with _lock:
        if _EXEC is None:
            _EXEC = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="cold_store")
        return _EXEC


def _prims() -> tuple[asyncio.Semaphore, dict]:
    """이벤트 루프마다 세마포어·단일 비행 표 — asyncio 객체는 만든 루프에 묶인다(시험은 루프를 여러 번 만든다)."""
    loop = asyncio.get_running_loop()
    ent = _loop_prims.get(id(loop))
    if ent is None or ent[0] is not loop:
        for k in [k for k, v in _loop_prims.items() if v[0].is_closed()]:
            _loop_prims.pop(k, None)
        ent = (loop, asyncio.Semaphore(4), {})
        _loop_prims[id(loop)] = ent
    return ent[1], ent[2]


def _note_degraded(reason: str, detail: str = "") -> None:
    with _lock:
        _degraded[reason] = _degraded.get(reason, 0) + 1
        _last_error.update(reason=reason, at=time.time(), detail=detail[:200] if detail else None)


def reset_for_tests() -> None:
    """시험 전용 — 프로세스 상태를 처음으로(디스크는 건드리지 않는다)."""
    global _EXEC
    with _lock:
        _state.update(manifest=None, manifest_sha=None, manifest_fetched=0.0, manifest_error=None, manifest_tries=0)
        _verified.clear()
        _bad.clear()
        _backoff.clear()
        for k in _counters:
            _counters[k] = 0
        _degraded.clear()
        _read_ms.clear()
        _last_error.update(reason=None, at=None, detail=None)
        _loop_prims.clear()
        ex, _EXEC = _EXEC, None
    if ex is not None:
        ex.shutdown(wait=True)


# ── 목록 ──────────────────────────────────────────────────────────────────────
def _validate_manifest(m: Any) -> dict:
    if not isinstance(m, dict) or m.get("schema") != MANIFEST_SCHEMA or m.get("kind") != MANIFEST_KIND:
        raise ColdError("manifest_schema", "서빙 목록의 schema·kind 가 이 서버가 아는 판이 아니다")
    ds = m.get("datasets")
    if not isinstance(ds, dict):
        raise ColdError("manifest_schema", "서빙 목록에 datasets 가 없다")
    return m


def _dataset_entry(m: dict, dataset: str) -> tuple[dict, dict]:
    """(데이터셋 머리, 파일 항목) — 모양이 어긋나면 ColdError."""
    d = (m.get("datasets") or {}).get(dataset)
    if not isinstance(d, dict):
        raise ColdError("no_dataset", f"서빙 목록에 {dataset} 가 없다")
    if d.get("engine") != "sqlite3":
        raise ColdError("unsupported", f"{dataset} 엔진 {d.get('engine')!r} — sqlite3 만 읽는다")
    files = d.get("files") or []
    if len(files) != 1:
        raise ColdError("unsupported", f"{dataset} 파일 {len(files)}개 — 지금은 데이터셋마다 한 파일만 읽는다")
    f = files[0]
    ok = (isinstance(f, dict) and isinstance(f.get("file"), str) and _FILE_RE.fullmatch(f["file"])
          and isinstance(f.get("sha256"), str) and _SHA_RE.fullmatch(f["sha256"])
          and isinstance(f.get("bytes"), int) and f["bytes"] > 0
          and f.get("encoding") in (None, "gzip")
          and (f.get("encoding") != "gzip" or (isinstance(f.get("object_sha256"), str) and _SHA_RE.fullmatch(f["object_sha256"])))
          and isinstance(f.get("coverage"), dict)
          and all(isinstance(f["coverage"].get(k), str) and _DD_RE.fullmatch(f["coverage"][k]) for k in ("min_dd", "max_dd")))
    cols = d.get("columns")
    ok = ok and isinstance(cols, list) and cols and all(isinstance(c, str) and _IDENT_RE.fullmatch(c) for c in cols)
    ok = ok and all(isinstance(d.get(k), str) and _IDENT_RE.fullmatch(d[k]) for k in ("view", "date_col"))
    key = d.get("key")
    ok = ok and isinstance(key, list) and len(key) == 2 and all(isinstance(k, str) and k in cols for k in key)
    ok = ok and d.get("date_col") in key and key[0] != key[1]
    if not ok:
        raise ColdError("manifest_schema", f"{dataset} 항목 모양이 어긋났다")
    try:                               # 달력에 있는 날짜·min ≤ max — 이음매 계산(`krx_retention.serving_seam`)이 요청 중에 터지지 않게
        lo, hi = (_dt.datetime.strptime(f["coverage"][k], "%Y%m%d") for k in ("min_dd", "max_dd"))
    except ValueError:
        raise ColdError("manifest_schema", f"{dataset} coverage 날짜가 달력에 없다") from None
    if lo > hi:
        raise ColdError("manifest_schema", f"{dataset} coverage min_dd 가 max_dd 보다 늦다")
    if f.get("file_schema", FILE_SCHEMA) != FILE_SCHEMA:
        raise ColdError("unsupported", f"{dataset} 서빙본 file_schema {f.get('file_schema')} — 이 서버는 {FILE_SCHEMA}")
    return d, f


def _fetch_bytes_sync(url: str, *, max_bytes: int, timeout: float) -> bytes:
    """작은 문서(목록) 받기 — https 또는 file://. 크기 상한."""
    u = urlsplit(url)
    if u.scheme == "file":
        p = Path(unquote(u.path))
        if p.stat().st_size > max_bytes:
            raise ColdError("manifest_too_big", "서빙 목록이 너무 크다")
        return p.read_bytes()
    if u.scheme != "https":
        raise ColdError("bad_url", f"https 또는 file 만 받는다({u.scheme})")
    import httpx
    with httpx.Client(timeout=timeout, transport=_TRANSPORT, follow_redirects=False) as c:
        with c.stream("GET", url) as r:
            if r.status_code != 200:
                raise ColdError(f"http_{r.status_code}", f"서빙 목록 받기 {r.status_code}")
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > max_bytes:
                    raise ColdError("manifest_too_big", "서빙 목록이 너무 크다")
            return bytes(buf)


def _manifest_cache_path() -> Path:
    return _cold_dir() / "serve-manifest.json"


def _load_manifest_from_disk() -> None:
    """재시작 직후 — 마지막으로 성공한 목록을 디스크에서(받은 시각 그대로). 묵은 정도는 호출부가 본다."""
    p = _manifest_cache_path()
    try:
        side = json.loads(p.with_suffix(".meta.json").read_text())
        raw = p.read_bytes()
        if hashlib.sha256(raw).hexdigest() != side.get("sha256"):
            return
        m = _validate_manifest(json.loads(raw))
    except (OSError, ValueError, ColdError):
        return
    with _lock:
        if _state["manifest"] is None:
            _state.update(manifest=m, manifest_sha=side["sha256"], manifest_fetched=float(side.get("fetched_at") or 0.0))


def _refresh_manifest_sync() -> dict:
    """목록을 새로 받아 검증하고 디스크에도 남긴다. 실패면 ColdError(마지막 성공본은 그대로 둔다)."""
    url = _manifest_url()
    if not url:
        raise ColdError("no_manifest_url", "OPM_COLD_MANIFEST_URL 이 없다")
    with _lock:
        _state["manifest_tries"] += 1
    try:
        raw = _fetch_bytes_sync(url, max_bytes=1 << 20, timeout=10.0)
        m = _validate_manifest(json.loads(raw))
    except ColdError as e:
        with _lock:
            _state["manifest_error"] = {"reason": e.reason, "at": time.time()}
            _backoff["manifest"] = (e.reason, time.time() + _BACKOFF_SEC)
        raise
    except Exception as e:  # noqa: BLE001 — 네트워크·JSON 오류는 강등 사유로 바꾼다
        with _lock:
            _state["manifest_error"] = {"reason": "manifest_fetch", "at": time.time(),
                                        "detail": _scrub(f"{type(e).__name__}: {e}", url)}
            _backoff["manifest"] = ("manifest_fetch", time.time() + _BACKOFF_SEC)
        raise ColdError("manifest_fetch", _scrub(f"{type(e).__name__}: {e}", url)) from None
    sha = hashlib.sha256(raw).hexdigest()
    now = time.time()
    with _lock:
        _state.update(manifest=m, manifest_sha=sha, manifest_fetched=now, manifest_error=None)
        _backoff.pop("manifest", None)
    try:                                          # 디스크 사본(재시작 직후 네트워크 없이도 이미 받은 파일을 쓰게) — 실패해도 서빙은 계속
        d = _cold_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = _manifest_cache_path()
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(raw)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
        mtmp = p.with_suffix(".meta.json.tmp")
        mtmp.write_text(json.dumps({"sha256": sha, "fetched_at": now}))
        os.replace(mtmp, p.with_suffix(".meta.json"))
    except OSError as e:
        logger.warning("콜드 서빙: 목록 디스크 사본을 쓰지 못했다 — %s", type(e).__name__)
    return m


def _start(key: str, fn, *args) -> asyncio.Future:
    """같은 key 의 일은 한 번만 — 이미 도는 일이 있으면 그 Future 를 돌려준다(받기 스레드 1개에서 돈다)."""
    _, tasks = _prims()
    t = tasks.get(key)
    if t is None or t.done():
        t = asyncio.wrap_future(_executor().submit(fn, *args))
        t.add_done_callback(lambda f: f.cancelled() or f.exception())   # 결과를 아무도 안 기다려도 경고가 남지 않게
        tasks[key] = t
    return t


async def _single_flight(key: str, fn, *args):
    """`_start` 의 결과를 기다린다 — 기다리는 쪽이 시간 초과로 물러나도 일 자체는 끝까지 돈다(shield)."""
    return await asyncio.shield(_start(key, fn, *args))


async def _manifest(wait: float) -> dict:
    """쓸 수 있는 목록 — 없으면 받아 오고(최대 wait 초), TTL 이 지났으면 배경에서 새로 받으며 지금 것을 쓴다."""
    with _lock:
        m, fetched = _state["manifest"], _state["manifest_fetched"]
    if m is None:                               # 작은 파일 하나 — 받기 스레드(긴 받기가 돌고 있을 수 있다) 뒤에 줄 세우지 않는다
        await asyncio.to_thread(_load_manifest_from_disk)
        with _lock:
            m, fetched = _state["manifest"], _state["manifest_fetched"]
    age = time.time() - fetched if m is not None else None
    if m is None or age > _max_age_sec():
        with _lock:
            bo = _backoff.get("manifest")
        if bo and time.time() < bo[1]:                # 방금 실패했다 — 저장소를 두드리지 않고 같은 까닭으로 강등
            raise ColdError("manifest_stale" if m is not None else bo[0], "서빙 목록 받기를 잠시 쉬는 중(직전 실패)")
        try:
            return await asyncio.wait_for(_single_flight("manifest", _refresh_manifest_sync), timeout=wait)
        except asyncio.TimeoutError:
            raise ColdError("timeout", "서빙 목록 받기가 기다림 한도를 넘었다") from None
        except ColdError:
            if m is not None:                     # 묵은 성공본은 최대 나이를 넘었으니 쓰지 않는다
                raise ColdError("manifest_stale", "서빙 목록을 새로 받지 못했고 마지막 성공본이 너무 묵었다") from None
            raise
    if age > _ttl_sec():
        with _lock:
            bo = _backoff.get("manifest")
        if not (bo and time.time() < bo[1]):
            _start("manifest", _refresh_manifest_sync)   # 배경에서 새로 받는다 — 실패는 상태(manifest_error)에 남고 지금 것을 쓴다
    return m


# ── 파일 ──────────────────────────────────────────────────────────────────────
def _object_url(manifest_url: str, entry: dict) -> str:
    """파일 항목의 받을 주소 — 서명 URL(`url`). 목록이 file:// 이면 목록 폴더 기준 상대 경로도 받는다(로컬·시험)."""
    u = urlsplit(manifest_url)
    if entry.get("url"):
        url = str(entry["url"])
        if u.scheme == "https" and urlsplit(url).scheme != "https":   # https 목록이 로컬 파일을 가리키게 두지 않는다
            raise ColdError("bad_url", "https 목록의 파일 주소는 https 여야 한다")
        return url
    if u.scheme == "file" and isinstance(entry.get("object"), str):
        rel = entry["object"].removeprefix("serve/")
        if ".." in Path(rel).parts:
            raise ColdError("bad_url", "객체 경로에 .. 이 있다")
        return "file://" + quote(str(Path(unquote(u.path)).parent / rel))
    raise ColdError("no_url", "서빙 목록에 이 파일의 서명 URL 이 없다(재서명 전)")


def _url_expired(entry: dict) -> bool:
    exp = entry.get("url_expires_at")
    if not exp or not entry.get("url"):
        return False
    try:
        t = _dt.datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    return t <= _dt.datetime.now(_dt.timezone.utc)


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(_CHUNK), b""):
            h.update(blk)
    return h.hexdigest()


def _check_sqlite(path: Path, entry: dict, head: dict) -> None:
    con = sqlite3.connect(f"file:{quote(str(path))}?mode=ro&immutable=1", uri=True)
    try:
        app = con.execute("PRAGMA application_id").fetchone()[0]
        uv = con.execute("PRAGMA user_version").fetchone()[0]
        if app != APP_ID or uv != entry.get("file_schema", FILE_SCHEMA):
            raise ColdError("bad_file", f"서빙본 머리 application_id {app:#x} · user_version {uv}")
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({head['view']})")]
        if cols != list(head["columns"]):
            raise ColdError("bad_file", "서빙본 뷰 칸이 목록과 다르다")
    except sqlite3.DatabaseError as e:
        raise ColdError("bad_file", f"서빙본을 열지 못했다 {type(e).__name__}") from None
    finally:
        con.close()


@contextlib.contextmanager
def _dir_lock(d: Path):
    """같은 머신의 다른 프로세스와 받기를 겹치지 않게(fcntl 없는 곳에선 프로세스 안 단일 비행만)."""
    d.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:                  # pragma: no cover — 윈도우
        yield
        return
    with open(d / ".lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _rss_mb() -> float | None:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        return None
    return None


def _mark_verified(sha: str, info: dict, d: Path, counter: str) -> None:
    """검증된 판을 적는다 — 같은 데이터셋 폴더의 옛 판 표시는 지운다(그 파일은 방금 `_prune_dir` 가 지웠다).
    남겨 두면 `/health` 의 files_ok 가 디스크보다 많게 센다(판이 바뀔 때마다 하나씩 는다)."""
    with _lock:
        for k in [k for k, v in _verified.items() if k != sha and Path(v["path"]).parent == d]:
            del _verified[k]
        _verified[sha] = info
        _counters[counter] += 1


def _ensure_file_sync(dataset: str, head: dict, entry: dict, manifest_url: str) -> dict:
    """그 판의 파일을 디스크에 검증된 상태로 — 이미 있으면 sha 만 다시 보고, 없으면 받아서 검증 뒤 바꿔 끼운다."""
    sha = entry["sha256"]
    with _lock:
        hit = _verified.get(sha)
    if hit and Path(hit["path"]).exists():
        return hit
    d = _cold_dir() / dataset
    final = d / entry["file"]
    with _dir_lock(_cold_dir()):
        if final.exists() and final.stat().st_size == entry["bytes"] and _sha_file(final) == sha:
            _check_sqlite(final, entry, head)
            _prune_dir(d, keep={entry["file"]})
            info = {"path": str(final), "received_at": final.stat().st_mtime, "bytes": entry["bytes"], "how": "reused"}
            _mark_verified(sha, info, d, "verify_reuse")
            return info
        if entry["bytes"] > _max_bytes():
            raise ColdError("disk_cap", f"서빙본 {entry['bytes']} B 가 상한 {_max_bytes()} B 를 넘는다")
        d.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(d).free
        if free < entry["bytes"] + 64 * 1048576:
            raise ColdError("disk_full", f"여유 공간 {free} B 부족")
        if _url_expired(entry):
            raise ColdError("url_expired", "서명 URL 이 만료됐다(재서명 필요)")
        url = _object_url(manifest_url, entry)
        tmp = d / f".{entry['file']}.{os.getpid()}.part"
        try:
            got = _download_sync(url, tmp, entry)
            if got["sha256"] != sha:
                raise ColdError("sha_mismatch", "받은 서빙본 sha256 이 목록과 다르다")
            if entry.get("encoding") == "gzip" and got["object_sha256"] != entry["object_sha256"]:
                raise ColdError("sha_mismatch", "받은 객체 sha256 이 목록과 다르다")
            if got["bytes"] != entry["bytes"]:
                raise ColdError("sha_mismatch", "받은 서빙본 크기가 목록과 다르다")
            _check_sqlite(tmp, entry, head)
            os.replace(tmp, final)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()
        _prune_dir(d, keep={entry["file"]})
        info = {"path": str(final), "received_at": time.time(), "bytes": entry["bytes"], "how": "downloaded"}
        _mark_verified(sha, info, d, "downloads")
        with _lock:
            _counters["download_bytes"] += got["object_bytes"]
        logger.info("콜드 서빙: %s 받음 · %s · %.1f MiB", dataset, entry["file"], entry["bytes"] / 1048576)
        return info


def _download_sync(url: str, tmp: Path, entry: dict) -> dict:
    """스트림으로 받아 (gzip 이면 풀며) 임시 파일에 쓴다. 객체·풀어낸 sha256 과 크기를 함께 센다. fsync 까지."""
    gz = entry.get("encoding") == "gzip"
    limit_obj = int(entry.get("object_bytes") or entry["bytes"]) + 1
    oh, fh_ = hashlib.sha256(), hashlib.sha256()
    n_obj = n_out = 0
    dec = zlib.decompressobj(16 + zlib.MAX_WBITS) if gz else None

    def feed(chunk: bytes, out) -> None:
        nonlocal n_obj, n_out
        n_obj += len(chunk)
        if n_obj > limit_obj:
            raise ColdError("sha_mismatch", "받은 객체가 목록 크기보다 크다")
        oh.update(chunk)
        data = dec.decompress(chunk, _CHUNK) if dec is not None else chunk
        while True:
            n_out += len(data)
            if n_out > entry["bytes"]:
                raise ColdError("sha_mismatch", "풀어낸 서빙본이 목록 크기보다 크다")
            fh_.update(data)
            out.write(data)
            if dec is None or not dec.unconsumed_tail:
                break
            data = dec.decompress(dec.unconsumed_tail, _CHUNK)

    u = urlsplit(url)
    deadline = time.monotonic() + _env_float("OPM_COLD_DOWNLOAD_MAX_SEC", 300.0)   # 아주 느린 응답이 받기 스레드를 붙잡지 않게
    with open(tmp, "wb") as out:
        if u.scheme == "file":
            with open(unquote(u.path), "rb") as src:
                for chunk in iter(lambda: src.read(_CHUNK), b""):
                    feed(chunk, out)
        elif u.scheme == "https":
            import httpx
            try:
                with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), transport=_TRANSPORT,
                                  follow_redirects=False) as c:
                    with c.stream("GET", url) as r:
                        if r.status_code != 200:
                            raise ColdError(f"http_{r.status_code}", f"서빙본 받기 {r.status_code}")
                        for chunk in r.iter_raw(_CHUNK):
                            feed(chunk, out)
                            if time.monotonic() > deadline:
                                raise ColdError("download", "서빙본 받기가 전체 시간 한도를 넘었다")
            except ColdError:
                raise
            except Exception as e:  # noqa: BLE001
                raise ColdError("download", _scrub(f"{type(e).__name__}: {e}", url)) from None
        else:
            raise ColdError("bad_url", f"https 또는 file 만 받는다({u.scheme})")
        if dec is not None:
            tail = dec.flush()
            if tail:
                n_out += len(tail)
                if n_out > entry["bytes"]:
                    raise ColdError("sha_mismatch", "풀어낸 서빙본이 목록 크기보다 크다")
                fh_.update(tail)
                out.write(tail)
            if not dec.eof:
                raise ColdError("download", "gzip 스트림이 끝나지 않았다(잘린 객체 — 다시 받는다)")
        out.flush()
        os.fsync(out.fileno())
    return {"sha256": fh_.hexdigest(), "object_sha256": oh.hexdigest(), "bytes": n_out, "object_bytes": n_obj}


def _prune_dir(d: Path, keep: set[str]) -> None:
    """목록에 없는 옛 판·남은 임시 파일을 지운다(읽는 중인 연결은 POSIX 에서 그대로 끝까지 읽는다)."""
    try:
        for f in d.iterdir():
            if f.name in keep:
                continue
            if f.suffix in (".sqlite", ".part") or f.name.endswith(".part"):
                with contextlib.suppress(OSError):
                    f.unlink()
    except OSError:
        pass


# ── 공개 API ──────────────────────────────────────────────────────────────────
async def ready(dataset: str, *, wait: float | None = None) -> Ready | ColdError:
    """데이터셋을 읽을 준비 — 목록 확인 → 파일 검증(필요하면 받기, 최대 wait 초). 실패면 ColdError(강등 까닭)를 **돌려준다**."""
    if not enabled():
        return ColdError("off")
    wait = _wait_sec() if wait is None else wait
    t0 = time.monotonic()
    try:
        m = await _manifest(wait)
        head, entry = _dataset_entry(m, dataset)
        sha = entry["sha256"]
        with _lock:
            bad, bo, hit = _bad.get(sha), _backoff.get(sha), _verified.get(sha)
        if hit and os.path.exists(hit["path"]):          # 늘 지나는 길 — 이미 검증한 판이면 받기 스레드를 거치지 않는다
            info = hit
        else:
            if bad and time.time() - bad[1] < _BAD_RETRY_SEC:
                raise ColdError(bad[0], "이 판은 앞서 검증에 실패했다(한 시간 뒤 다시 받는다)")
            if bo and time.time() < bo[1]:
                raise ColdError(bo[0], "서빙본 받기를 잠시 쉬는 중(직전 실패)")
            left = max(0.05, wait - (time.monotonic() - t0))
            try:
                info = await asyncio.wait_for(
                    _single_flight("file:" + sha, _ensure_file_sync, dataset, head, entry, _manifest_url()), timeout=left)
            except asyncio.TimeoutError:
                raise ColdError("timeout", "서빙본 받기가 기다림 한도를 넘었다(배경에서 계속)") from None
            except ColdError as e:
                with _lock:
                    if e.reason in ("sha_mismatch", "bad_file"):
                        _bad[sha] = (e.reason, time.time())
                    elif e.reason not in ("disk_cap", "disk_full", "no_url", "bad_url"):
                        _backoff[sha] = (e.reason, time.time() + _BACKOFF_SEC)
                raise
    except ColdError as e:
        _note_degraded(e.reason, str(e))
        return e
    except Exception as e:  # noqa: BLE001 — 무엇이든 강등으로
        err = ColdError("error", _scrub(f"{type(e).__name__}: {e}", _manifest_url()))
        _note_degraded(err.reason, str(err))
        return err
    cov = entry["coverage"]
    key = next(k for k in head["key"] if k != head["date_col"])
    return Ready(dataset, info["path"], head["view"], tuple(head["columns"]), key, head["date_col"],
                 cov["min_dd"], cov["max_dd"], entry["sha256"])


def _read_sync(r: Ready, cols: tuple[str, ...], key_value: str, lo: str, hi: str) -> list[tuple]:
    sel = ", ".join(cols)
    con = sqlite3.connect(f"file:{quote(r.path)}?mode=ro&immutable=1", uri=True, check_same_thread=False)
    try:
        con.execute("PRAGMA cache_size = -2048")
        con.execute("PRAGMA mmap_size = 0")
        return con.execute(f"SELECT {sel} FROM {r.view} WHERE {r.key} = ? AND {r.date_col} >= ? AND {r.date_col} < ? "
                           f"ORDER BY {r.date_col}", (key_value, lo, hi)).fetchall()
    finally:
        con.close()


async def read(r: Ready, cols: tuple[str, ...] | list[str], key_value: str, lo: str, hi: str) -> list[tuple] | None:
    """[lo, hi) 의 행을 날짜 오름차순으로 — 칸은 목록의 칸 안에서만. 실패(파일 오류·RSS 가드)면 None(강등)."""
    cols = tuple(cols)
    if not cols or any(c not in r.columns for c in cols):
        _note_degraded("bad_columns", f"{cols}")
        return None
    if not lo < hi:
        return []
    rss = _rss_mb()
    if rss is not None and rss > _rss_guard_mb():
        _note_degraded("rss_guard", f"RSS {rss:.0f} MB")
        return None
    sem, _ = _prims()
    t0 = time.perf_counter()
    try:
        async with sem:
            rows = await asyncio.to_thread(_read_sync, r, cols, key_value, lo, hi)
    except (sqlite3.Error, OSError) as e:
        with _lock:
            _verified.pop(r.sha256, None)        # 다음 요청이 파일을 다시 검증하게
        _note_degraded("read_error", type(e).__name__)
        return None
    ms = (time.perf_counter() - t0) * 1000
    with _lock:
        _counters["reads"] += 1
        _counters["rows"] += len(rows)
        _read_ms.append(ms)
        if len(_read_ms) > 512:
            del _read_ms[:256]
    return rows


def note_degraded(reason: str, detail: str = "") -> None:
    """호출부가 고른 강등(이음매 틈 등)도 같은 칸에 센다."""
    _note_degraded(reason, detail)


def stats() -> dict:
    """`/health` 의 `cold` — 켜짐·판·받은 시각·검증된 파일 수·읽기 수·강등 까닭. URL·키는 싣지 않는다."""
    with _lock:
        m = _state["manifest"]
        out: dict[str, Any] = {
            "enabled": enabled(),
            "engine": "sqlite3",
            "manifest_url_set": bool(_manifest_url()),
            "manifest_sha": (_state["manifest_sha"] or "")[:16] or None,
            "manifest_age_s": round(time.time() - _state["manifest_fetched"]) if m is not None else None,
            "manifest_error": _state["manifest_error"],
            "files_ok": len(_verified),
            "files_bad": len(_bad),
            "counters": dict(_counters),
            "degraded": dict(_degraded),
            "last_error": dict(_last_error) if _last_error["reason"] else None,
        }
        if _read_ms:
            s = sorted(_read_ms)
            out["read_p95_ms"] = round(s[min(len(s) - 1, int(len(s) * 0.95))], 3)
        datasets = {}
        for name, d in ((m or {}).get("datasets") or {}).items():
            f = (d.get("files") or [{}])[0] if isinstance(d, dict) else {}
            v = _verified.get(f.get("sha256") or "")
            exp_days = None
            if f.get("url_expires_at"):
                with contextlib.suppress(ValueError):
                    t = _dt.datetime.fromisoformat(str(f["url_expires_at"]).replace("Z", "+00:00"))
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=_dt.timezone.utc)
                    exp_days = round((t - _dt.datetime.now(_dt.timezone.utc)).total_seconds() / 86400, 1)
            datasets[name] = {
                "version": (f.get("sha256") or "")[:16] or None,
                "coverage": d.get("coverage") if isinstance(d, dict) else None,
                "file_ok": bool(v),
                "received_at": (_dt.datetime.fromtimestamp(v["received_at"], _dt.timezone.utc).isoformat(timespec="seconds")
                                if v else None),
                "how": v.get("how") if v else None,
                "bytes": f.get("bytes"),
                "url_set": bool(f.get("url")),
                "url_exp_days": exp_days,
            }
        out["datasets"] = datasets
    return out


# ── 배경: 예열·목록 갱신 ─────────────────────────────────────────────────────
async def _background(datasets: tuple[str, ...]) -> None:
    await asyncio.sleep(_env_float("OPM_COLD_PREWARM_SEC", 30.0))
    while True:
        if enabled() and _manifest_url():
            try:
                await asyncio.wrap_future(_executor().submit(_refresh_manifest_sync))
            except ColdError as e:
                logger.warning("콜드 서빙: 목록 갱신 실패 — %s", e.reason)
            except Exception as e:  # noqa: BLE001
                logger.warning("콜드 서빙: 목록 갱신 실패 — %s", type(e).__name__)
            for ds in datasets:
                r = await ready(ds, wait=600.0)
                if isinstance(r, ColdError) and r.reason != "off":
                    logger.warning("콜드 서빙: %s 예열 실패 — %s", ds, r.reason)
        await asyncio.sleep(_ttl_sec())


#: 서버가 예열·목록 갱신하는 데이터셋 — 주간 시세(261019)와 시장·업종 집계 두 벌(261021, 열쇠 scheme·날짜).
DEFAULT_DATASETS = ("krx_weekly_hist", "krx_cap_agg_hist", "opm_val_market_hist")


def attach_to_app(app, datasets: tuple[str, ...] = DEFAULT_DATASETS) -> None:
    """Starlette 앱의 lifespan 을 감싸 서버가 뜬 뒤 예열·목록 갱신을 배경으로 돌린다(꺼져 있으면 아무것도 받지 않는다).
    배경 작업이 실패해도 앱은 뜬다 — 요청 경로가 그때그때 받거나 강등한다."""
    inner = getattr(getattr(app, "router", None), "lifespan_context", None)
    if inner is None:
        logger.warning("콜드 서빙: 앱에 lifespan 이 없어 예열을 붙이지 못했다")
        return

    @contextlib.asynccontextmanager
    async def _lifespan(a):
        async with inner(a) as state:
            task = None
            if enabled():
                try:
                    task = asyncio.get_running_loop().create_task(_background(datasets))
                except Exception:  # noqa: BLE001
                    logger.exception("콜드 서빙 예열을 띄우지 못했다 — 서빙은 계속")
            try:
                yield state
            finally:
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

    app.router.lifespan_context = _lifespan
