# -*- coding: utf-8 -*-
"""운영자 공지 — 모든 도구 응답에 끼우는 짧은 안내(전용 도구 없음).

261023 신설. 점검·변경 예고처럼 운영자가 사용자에게 알릴 일을 **도구 응답 안**에 싣는다. md 응답은 `# 제목` 바로 아래
`> 📢 공지 (OPM <버전> · <MM-DD>) <title> — <body> <link>` 블록, JSON 응답은 최상위 dict 의 `notices` 칸(`warnings` 와 따로)이다.
공지 문구는 서버 지시문(`instructions`)에 넣지 않는다 — 지시문에는 「공지를 사용자에게 한 번 전하라」는 영어 한 문장만 있다.

■ 어디서 오나 — 매니페스트(JSON) 하나
  환경변수 `OPM_NOTICE_URL`(https 또는 file:// — 로컬·시험). 없으면 이 모듈은 아무것도 하지 않고 도구 응답은 바이트 그대로다.
  {"notices": [{id, level("info"|"normal"|"important"), title(≤60자), body(≤400자), link(https 1개, 선택),
                starts_at, expires_at(ISO 8601 — 시간대가 없으면 KST),
                show("once_per_key"|"weekly"|"daily"|"every_call"|"company_only")}]}
  최상위가 공지 목록(list) 그대로여도 받는다 — 표 하나를 그대로 내보내는 REST 응답(`[{…}, …]`)을 주소로 쓸 수 있게(261025).
  `OPM_NOTICE_APIKEY` 가 있으면 받기 요청에 `apikey` 헤더로 싣는다(읽기 전용 공개 키를 요구하는 주소용). 값은 어디에도 남기지 않는다.
  형식에 없는 칸은 무시한다. 한 공지가 한도·형식을 어기면(길이 초과·`[xxx=…]` 꼴 대괄호 태그·제어문자·https 아닌 링크…) **그 공지만** 버린다.
  대괄호 태그를 막는 이유: 서버 미들웨어가 응답 본문에서 `[ekind=…]`·`[degraded=…]`·`[nodata=…]` 표지를 뽑아 통계에 적는다.
  show 를 비우면 중요도로 정한다: info → once_per_key(드물게), normal → daily(하루 한 번), important → every_call(자주).
  important 는 show 를 적어도 every_call 로 본다(company_only 로 명시한 공지만 그대로 company 응답에만).

■ 받기 — `cold_store.py` 의 원격 JSON 재수신과 같은 꼴
  · 도구 응답 경로는 받기를 기다리지 않는다. 호출이 들어올 때 마지막 성공(또는 실패 쉼)에서 지난 시간을 보고, 받을 때가 됐으면
    받기 스레드 1개에 일을 맡기고 곧바로 돌아온다. 아직 받은 것이 없으면 이번 응답에는 공지가 없다.
  · 성공 뒤 `_TTL_SEC`(10분)마다 새로 받는다. 받기 실패(네트워크·HTTP 오류·시간 초과)나 매니페스트 전체 형식 오류(JSON 아님·notices 목록 없음)는
    일시 실패다 — 마지막 성공본을 계속 쓰고, `_BACKOFF_SEC`(5분 = `cold_store._BACKOFF_SEC`)는 다시 받지 않는다.
  · 마지막 성공본은 성공 시각부터 `_MAX_AGE_SEC`(72시간 = `cold_store` 의 `OPM_COLD_MANIFEST_MAX_AGE_SEC` 기본값)까지만 쓴다.
    URL 이 없거나 한 번도 못 받았거나 72시간이 넘으면 빈 목록. 성공적으로 받은 **빈 목록은 즉시 반영**된다(공지를 바로 내리는 길).
  · 디스크 사본은 두지 않는다(메모리만). 재시작 직후 첫 받기 전 응답은 공지가 없다.

■ 누구에게 몇 번 — `show`
  · once_per_key: 요청 키(`dart.client._ctx_opendart_key`)를 `usage.record` 와 같은 `sha256(key.lower())` 으로 해시해 (공지 id, 키 해시)를
    머신 메모리 LRU(`_SEEN_MAX` = 1만)에 기억하고 같은 키의 두 번째 호출부터 붙이지 않는다. 키가 없는 요청은 머신 단위로 첫 응답에만.
    원래 키 값은 메모리·로그·응답 어디에도 남기지 않는다. 붙일 자리가 없어 못 붙인 응답은 본 것으로 치지 않는다.
  · daily·weekly: 같은 기억에 마지막으로 보인 시각을 두고, 같은 키에 24시간·7일이 지나야 다시 붙인다(261025).
  · every_call·important: 만료까지 매 호출. company_only: `company` 도구 응답에만(매번).

공지를 끼우다 어떤 오류가 나도 도구 응답은 그대로 나간다(`attach` 는 예외를 밖으로 내지 않는다).
"""
from __future__ import annotations

import concurrent.futures
import datetime as _dt
import hashlib
import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)

_ENV_URL = "OPM_NOTICE_URL"
_ENV_APIKEY = "OPM_NOTICE_APIKEY"           # 선택 — 받기 요청의 apikey 헤더(값은 남기지 않는다)
_TTL_SEC = 600.0                       # 성공 뒤 다시 받기까지 — 10분
_BACKOFF_SEC = 300.0                   # 실패 뒤 다시 받기까지 — 5분(cold_store._BACKOFF_SEC 와 같다)
_MAX_AGE_SEC = 72 * 3600.0             # 마지막 성공본을 쓰는 한도 — 72시간
_FETCH_TIMEOUT_SEC = 10.0
_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_NOTICES = 20                      # 매니페스트에서 쓰는 공지 수 상한(앞에서부터)
_SEEN_MAX = 10_000                     # once_per_key 기억 상한(LRU)
_TITLE_MAX, _BODY_MAX, _LINK_MAX, _ID_MAX = 60, 400, 500, 64
_COMPANY_TOOL = "company"
_KST = _dt.timezone(_dt.timedelta(hours=9))

_LEVELS = ("info", "normal", "important")
_SHOWS = ("once_per_key", "weekly", "daily", "every_call", "company_only")
#: 중요도별 기본 노출 — show 를 비운 공지(드물게 · 하루 한 번 · 매번).
_DEFAULT_SHOW = {"info": "once_per_key", "normal": "daily", "important": "every_call"}
#: 다시 보이기까지의 간격(초). once_per_key 는 간격 없이 한 번(기억이 밀려나기 전까지).
_REPEAT_SEC = {"once_per_key": None, "weekly": 7 * 86400.0, "daily": 86400.0}
_ID_RX = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")
#: `[ekind=…]` 꼴의 여는 부분. 닫는 대괄호가 없어도 막는다(미들웨어는 본문 바이트에서 표지를 찾는다).
_TAG_RX = re.compile(r"\[\s*[A-Za-z_][A-Za-z0-9_ .-]*=")
#: 제어문자(Cc)와 줄·문단 구분(Zl·Zp), 글자 방향을 뒤집는 문자(bidi) — 한 줄 인용 블록을 깨거나 글을 속이는 데 쓰인다.
_BIDI = frozenset("‪‫‬‭‮⁦⁧⁨⁩‎‏؜")


#: 시험이 HTTP 를 바꿔 끼우는 자리(httpx 전송 계층). 운영은 None(기본 전송).
_TRANSPORT = None


def _now() -> float:
    """시계(time.time). 시험이 바꿔 끼운다."""
    return time.time()


@dataclass(frozen=True, slots=True)
class Notice:
    id: str
    level: str
    title: str
    body: str
    link: str | None
    starts_at: str
    expires_at: str
    show: str                          # 실제로 쓰는 값(important → every_call 반영)
    starts_ts: float
    expires_ts: float
    date_label: str                    # starts_at 의 KST 날짜 MM-DD

    def public(self) -> dict[str, Any]:
        """도구 응답의 `notices` 칸 한 항목."""
        out: dict[str, Any] = {"id": self.id, "level": self.level, "title": self.title, "body": self.body}
        if self.link:
            out["link"] = self.link
        return out

    def listing(self) -> dict[str, Any]:
        """리소스 `opm://notices` 의 한 항목 — 기간·노출 방식까지."""
        out = self.public()
        out.update(starts_at=self.starts_at, expires_at=self.expires_at, show=self.show)
        return out


# ── 상태 ──────────────────────────────────────────────────────────────────────
_lock = threading.Lock()
_state: dict[str, Any] = {}
_seen: "OrderedDict[tuple[str, str], float]" = OrderedDict()  # (공지 id, 키 해시) → 마지막으로 보인 시각, 키 해시는 sha256 hex
_EXEC: concurrent.futures.ThreadPoolExecutor | None = None


def _blank_state() -> dict[str, Any]:
    return {
        "notices": [],        # 검증을 통과한 마지막 성공본(list[Notice])
        "have": False,        # 한 번이라도 받기에 성공했나
        "fetched": 0.0,       # 마지막 성공 시각
        "error": None,        # 마지막 실패의 예외 클래스 이름과 시각(URL·본문 없음)
        "next_try": 0.0,      # 실패 쉼이 끝나는 시각
        "inflight": False,
        "tries": 0,
        "ok": 0,
    }


_state.update(_blank_state())


def _reset_for_tests() -> None:
    global _EXEC
    with _lock:
        ex, _EXEC = _EXEC, None
        _state.clear()
        _state.update(_blank_state())
        _seen.clear()
    if ex is not None:
        ex.shutdown(wait=True)


def _executor() -> concurrent.futures.ThreadPoolExecutor:
    """받기 전용 스레드 1개 — 도구 호출 스레드를 붙잡지 않는다."""
    global _EXEC
    with _lock:
        if _EXEC is None:
            _EXEC = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="notices")
        return _EXEC


def _url() -> str:
    return (os.environ.get(_ENV_URL) or "").strip()


def enabled() -> bool:
    return bool(_url())


# ── 받기 ──────────────────────────────────────────────────────────────────────
class _FetchError(Exception):
    pass


def _fetch_bytes_sync(url: str) -> bytes:
    """매니페스트 받기 — https 또는 file://(로컬·시험). 크기 상한, 리다이렉트 따라가지 않음."""
    u = urlsplit(url)
    if u.scheme == "file":
        from pathlib import Path

        p = Path(unquote(u.path))
        if p.stat().st_size > _MAX_MANIFEST_BYTES:
            raise _FetchError("too_big")
        return p.read_bytes()
    if u.scheme != "https":
        raise _FetchError("bad_scheme")
    import httpx

    apikey = (os.environ.get(_ENV_APIKEY) or "").strip()
    headers = {"apikey": apikey} if apikey else {}
    with httpx.Client(timeout=_FETCH_TIMEOUT_SEC, transport=_TRANSPORT, follow_redirects=False, headers=headers) as c:
        with c.stream("GET", url) as r:
            if r.status_code != 200:
                raise _FetchError(f"http_{r.status_code}")
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > _MAX_MANIFEST_BYTES:
                    raise _FetchError("too_big")
            return bytes(buf)


def _refresh_sync() -> bool:
    """받기 스레드에서 한 번 받아 검증하고 상태에 반영한다. 실패하면 마지막 성공본은 그대로 둔다."""
    url = _url()
    try:
        if not url:
            raise _FetchError("no_url")
        notices = _parse_manifest(_fetch_bytes_sync(url))
    except Exception as exc:  # noqa: BLE001 — 네트워크·형식 오류 모두 일시 실패. 예외 클래스 이름만 남긴다(URL·본문은 비공개).
        now = _now()
        with _lock:
            _state.update(error={"kind": type(exc).__name__, "at": now}, next_try=now + _BACKOFF_SEC, inflight=False)
        logger.info("공지 매니페스트 받기 실패(%s) — 마지막 성공본을 계속 쓴다", type(exc).__name__)
        return False
    now = _now()
    with _lock:
        _state.update(notices=notices, have=True, fetched=now, error=None, next_try=0.0, inflight=False)
        _state["ok"] += 1
    return True


def _run_refresh() -> None:
    try:
        _refresh_sync()
    finally:
        with _lock:
            _state["inflight"] = False


def _maybe_refresh(now: float) -> None:
    """받을 때가 됐으면 배경에 맡기고 곧바로 돌아온다(기다리지 않는다)."""
    if not _url():
        return
    with _lock:
        if _state["inflight"] or now < _state["next_try"]:
            return
        if _state["have"] and now - _state["fetched"] < _TTL_SEC:
            return
        _state["inflight"] = True
        _state["tries"] += 1
    try:
        _executor().submit(_run_refresh)
    except RuntimeError:                       # 인터프리터 종료 중
        with _lock:
            _state["inflight"] = False


# ── 검증 ──────────────────────────────────────────────────────────────────────
def _clean_text(value: Any, limit: int, *, required: bool) -> str | None:
    """길이·제어문자·대괄호 태그를 본다. 통과하면 앞뒤 공백을 뗀 문자열, 아니면 None."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if required and not s:
        return None
    if len(s) > limit:
        return None
    for ch in s:
        if unicodedata.category(ch) in ("Cc", "Zl", "Zp") or ch in _BIDI:
            return None
    if _TAG_RX.search(s):
        return None
    return s


def _parse_time(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        t = _dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=_KST)
    return t.timestamp()


def _parse_link(value: Any) -> str | None | bool:
    """None = 링크 없음, str = 통과, False = 어김(그 공지를 버린다)."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s or len(s) > _LINK_MAX or any(c.isspace() for c in s):
        return False
    if _clean_text(s, _LINK_MAX, required=True) is None:
        return False
    try:
        u = urlsplit(s)
    except ValueError:
        return False
    if u.scheme != "https" or not u.netloc or u.username is not None or u.password is not None:
        return False
    return s


def _parse_notice(item: Any) -> Notice | None:
    if not isinstance(item, dict):
        return None
    nid = item.get("id")
    if not isinstance(nid, str) or len(nid) > _ID_MAX or not _ID_RX.fullmatch(nid):
        return None
    level = item.get("level")
    if level not in _LEVELS:
        return None
    title = _clean_text(item.get("title"), _TITLE_MAX, required=True)
    body = _clean_text(item.get("body") or "", _BODY_MAX, required=False)   # 표에서 온 null 은 빈 본문
    if title is None or body is None:
        return None
    link = _parse_link(item.get("link"))
    if link is False:
        return None
    show = item.get("show") or _DEFAULT_SHOW[level]
    if show not in _SHOWS:
        return None
    starts, expires = _parse_time(item.get("starts_at")), _parse_time(item.get("expires_at"))
    if starts is None or expires is None or expires <= starts:
        return None
    if level == "important" and show != "company_only":
        show = "every_call"
    label = _dt.datetime.fromtimestamp(starts, _KST).strftime("%m-%d")
    return Notice(nid, level, title, body, link or None, str(item["starts_at"]).strip(), str(item["expires_at"]).strip(),
                  show, starts, expires, label)


def _parse_manifest(raw: bytes) -> list[Notice]:
    """매니페스트 전체 검사 — JSON 이 아니거나 notices 목록이 없으면 ValueError(일시 실패로 다룬다). 공지 하나의 어김은 그 공지만 버린다."""
    obj = json.loads(raw)
    if isinstance(obj, list):                                      # 표 하나를 그대로 내보내는 REST 응답
        obj = {"notices": obj}
    if not isinstance(obj, dict) or not isinstance(obj.get("notices"), list):
        raise ValueError("notices 목록이 없다")
    out: list[Notice] = []
    ids: set[str] = set()
    for item in obj["notices"]:
        n = _parse_notice(item)
        if n is None or n.id in ids:
            continue
        ids.add(n.id)
        out.append(n)
        if len(out) >= _MAX_NOTICES:
            break
    return out


# ── 지금 유효한 공지 ──────────────────────────────────────────────────────────
def active() -> list[dict[str, Any]]:
    """지금 유효한 공지 전부(노출 방식과 무관) — 리소스용. 받기는 배경에 맡기고 기다리지 않는다."""
    return [n.listing() for n in _valid_now()]


def _valid_now() -> list[Notice]:
    now = _now()
    _maybe_refresh(now)
    with _lock:
        if not _state["have"] or now - _state["fetched"] > _MAX_AGE_SEC:
            return []
        items = list(_state["notices"])
    return [n for n in items if n.starts_ts <= now < n.expires_ts]


def resource_json() -> str:
    """리소스 `opm://notices` 의 본문."""
    return json.dumps({"notices": active()}, ensure_ascii=False, indent=2)


def stats() -> dict[str, Any]:
    """관측용 요약(URL·본문 없음)."""
    with _lock:
        return {"enabled": enabled(), "have": _state["have"], "notices": len(_state["notices"]), "fetched": _state["fetched"],
                "tries": _state["tries"], "ok": _state["ok"], "error": _state["error"], "seen": len(_seen)}


# ── 한 번만 ───────────────────────────────────────────────────────────────────
def _key_hash() -> str:
    """요청 키의 해시(`usage.record` 와 같은 식). 키가 없으면 빈 문자열 = 머신 단위."""
    from open_proxy_mcp.dart.client import _ctx_opendart_key

    key = _ctx_opendart_key.get()
    return hashlib.sha256(key.lower().encode()).hexdigest() if key else ""


def _was_seen(nid: str, khash: str, within: float | None = None) -> bool:
    """이 키에 이미 보였나. within(초)을 주면 그 안에 보였을 때만 참(daily·weekly), 없으면 한 번이라도 보였으면 참."""
    with _lock:
        shown = _seen.get((nid, khash))
        if shown is None:
            return False
        _seen.move_to_end((nid, khash))
    return within is None or _now() - shown < within


def _mark_seen(nid: str, khash: str) -> None:
    with _lock:
        _seen[(nid, khash)] = _now()
        _seen.move_to_end((nid, khash))
        while len(_seen) > _SEEN_MAX:
            _seen.popitem(last=False)


def _candidates(tool_name: str) -> tuple[list[Notice], str]:
    khash = _key_hash()
    out: list[Notice] = []
    for n in _valid_now():
        if n.show == "company_only" and tool_name != _COMPANY_TOOL:
            continue
        if n.show in _REPEAT_SEC and _was_seen(n.id, khash, _REPEAT_SEC[n.show]):
            continue
        out.append(n)
    return out, khash


# ── 끼우기 ────────────────────────────────────────────────────────────────────
def _version() -> str:
    try:
        from open_proxy_mcp.server import _opm_version

        return _opm_version()
    except Exception:  # noqa: BLE001 — 버전을 못 읽어도 공지는 나간다
        return ""


def _md_line(n: Notice, version: str) -> str:
    line = f"> 📢 공지 (OPM {version} · {n.date_label}) {n.title}"
    if n.body:
        line += f" — {n.body}"
    if n.link:
        line += f" {n.link}"
    return line


def _render_md(result: str, items: list[Notice]) -> tuple[str, list[Notice]]:
    version = _version()
    fresh = [(n, _md_line(n, version)) for n in items]
    fresh = [(n, ln) for n, ln in fresh if ln not in result]       # 이미 실려 있으면 다시 싣지 않는다
    if not fresh:
        return result, []
    block = "\n".join(ln for _n, ln in fresh)
    head, _sep, rest = result.partition("\n")
    if head.startswith("#"):
        rest = rest.lstrip("\n")
        out = f"{head}\n\n{block}\n\n{rest}" if rest else f"{head}\n\n{block}"
    else:
        out = f"{block}\n\n{result}"
    return out, [n for n, _ln in fresh]


def _render_json(result: str, items: list[Notice]) -> tuple[str, list[Notice]] | None:
    """JSON 응답에 `notices` 칸을 싣는다. JSON 이 아니면 None(md 경로로), 붙일 자리가 없으면 (result, [])."""
    try:
        obj = json.loads(result)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return result, []                                          # 붙일 자리가 없는 JSON — 깨뜨리지 않는다
    have = obj.get("notices")
    if have is not None and not isinstance(have, list):
        return result, []
    have = list(have or [])
    present = {x.get("id") for x in have if isinstance(x, dict)}
    fresh = [n for n in items if n.id not in present]
    if not fresh:
        return result, []
    merged = have + [n.public() for n in fresh]
    if "notices" in obj:
        obj["notices"] = merged
        out = obj
    else:
        out = {}
        for k, v in obj.items():
            out[k] = v
            if k == "warnings":
                out["notices"] = merged
        out.setdefault("notices", merged)
    return json.dumps(out, ensure_ascii=False, indent=2), fresh


def attach(tool_name: str, result: Any) -> Any:
    """도구 응답에 유효한 공지를 끼운다. 공지가 없거나 붙일 자리가 없거나 오류가 나면 응답을 **그대로** 돌려준다."""
    if not isinstance(result, str) or not _url():
        return result
    try:
        items, khash = _candidates(tool_name)
        if not items:
            return result
        head = result.lstrip()[:1]
        if head in ('"', "["):
            try:
                json.loads(result)
                return result                                      # 배열·문자열 JSON — 붙일 자리가 없다(md 블록을 앞에 붙이면 JSON 이 깨진다)
            except ValueError:
                pass                                               # 대괄호로 시작하는 md
        if head == "{":
            done = _render_json(result, items)
            out, shown = done if done is not None else _render_md(result, items)
        else:
            out, shown = _render_md(result, items)
        for n in shown:
            if n.show in _REPEAT_SEC:
                _mark_seen(n.id, khash)
        return out if shown else result
    except Exception:  # noqa: BLE001 — 공지 때문에 도구 응답이 깨지면 안 된다
        logger.warning("공지 끼우기 실패 — 응답은 그대로 나간다", exc_info=False)
        return result
