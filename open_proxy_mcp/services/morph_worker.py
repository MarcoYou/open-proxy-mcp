"""법령 BM25 질의 토큰화(kiwipiepy)를 **별도 프로세스**에서 — 쉬면 프로세스째 내려 메모리를 OS 에 돌려준다 (261019).

왜 — `law_lookup` 첫 호출에 서버 RSS 가 400~500 MiB 뛰고 돌아오지 않았다(운영 10-19 새벽: 461→948 · 643→1031 MiB, 법령 원문 캐시가
처음 생긴 같은 분). 의심은 원문 캐시(`_FULLTEXT_CACHE`)였는데, 로컬 리눅스 컨테이너(python:3.12-slim · uv.lock 그대로)에서 단계별로 재 보니
원문은 20개 파일 전부를 올려도 4 MiB 안이었다. 뛴 몫은 거의 전부 **형태소 분석기 Kiwi 모델**이다.

    첫 law_lookup 한 번              +511 MiB
      ├ Kiwi() 생성                  +260
      ├ 첫 tokenize (지연 적재)       +213
      ├ law_index.json 파싱           +26   (/health 가 부팅 때 이미 올린다)
      ├ law_bm25.json 파싱            +10
      └ 원문(20개 파일 전부)            ≤4

같은 프로세스 안에서 Kiwi 를 놓으면(참조 제거 · gc · malloc_trim) 233 MiB 만 돌아오고 나머지는 힙에 남는다(3회 반복 같음).
자식 프로세스에 두면 부모 RSS 는 그대로이고(15 MiB), 자식이 끝나면 **전부** 돌아온다. 첫 호출 지연은 같다(1.4초 vs 1.5초),
그 뒤 한 번에 0.16ms(파이프 왕복).

토큰은 **같은 Kiwi(기본 설정)가 만든다** — 응답이 바뀌지 않는다. 가벼운 설정(다어절 사전 끄기, −175 MiB)은 코퍼스 3,950 조문 중
4개(「한국회계기준원」)의 토큰이 달라져 쓰지 않았다.

구조
  · 부모 `MorphWorker.tokenize(text)` → 자식 표준입력에 JSON 한 줄, 표준출력에서 JSON 한 줄(`[[형태, 품사], ...]`).
  · 자식은 `OPM_MORPH_IDLE_SEC`(기본 600초) 동안 요청이 없으면 스스로 끝난다. 부모가 죽으면 표준입력이 닫혀 함께 끝난다.
  · 자식을 못 띄우거나 답이 틀어지면 **같은 프로세스의 Kiwi** 로 그 요청을 처리한다(종전 동작) — 실패가 응답을 바꾸지 않는다.
    연달아 3번 실패하면 그 프로세스 동안은 자식을 쓰지 않는다(매번 기다리지 않게).
  · `OPM_MORPH_WORKER=0` 이면 처음부터 같은 프로세스(종전 동작). 윈도는 파이프에 select 를 못 써서 같은 프로세스.

이 파일은 자식에서 **경로로** 실행된다(`python <이 파일>`) — 패키지·서버를 import 하지 않게 맨 위 import 는 표준 라이브러리만 둔다.
"""
from __future__ import annotations

import json
import os
import select
import sys
import threading
import time

_ENV_ON = "OPM_MORPH_WORKER"
_ENV_IDLE = "OPM_MORPH_IDLE_SEC"
_DEFAULT_IDLE_SEC = 600
# 도구가 동기라 기다리는 동안 이벤트 루프가 선다 — 넉넉하되 길지 않게(넘으면 같은 프로세스로 처리).
_FIRST_REPLY_SEC = 20.0     # 자식 기동 + 모델 적재(실측 1.4~2.1초)
_REPLY_SEC = 10.0           # 그 뒤 한 요청(실측 0.16ms + 토큰화 — 코퍼스 가장 긴 조문도 1초 안)
_MAX_FAILURES = 3


def _idle_sec() -> int:
    try:
        return max(5, int(os.environ.get(_ENV_IDLE, _DEFAULT_IDLE_SEC)))
    except ValueError:
        return _DEFAULT_IDLE_SEC


# ── 자식 ──────────────────────────────────────────────────────────────────
def _serve(idle_sec: int) -> int:
    """표준입력 한 줄(JSON 문자열) → 표준출력 한 줄. `idle_sec` 동안 입력이 없거나 입력이 닫히면 끝난다."""
    try:
        from kiwipiepy import Kiwi
        kiwi = Kiwi()
    except Exception as exc:  # noqa: BLE001 — 미설치·모델 없음: 부모가 같은 프로세스 경로(종전 degrade)로 간다
        _reply({"err": f"unavailable:{type(exc).__name__}"})
        return 3
    stdin = sys.stdin.buffer
    while True:
        ready, _, _ = select.select([stdin], [], [], idle_sec)
        if not ready:
            return 0                       # 쉼 — 모델째 내려 메모리를 돌려준다
        line = stdin.readline()
        if not line:
            return 0                       # 부모가 닫았다(종료·재시작)
        try:
            text = json.loads(line)
            out = {"ok": [[m.form, m.tag] for m in kiwi.tokenize(text or "")]}
        except Exception as exc:  # noqa: BLE001
            out = {"err": type(exc).__name__}
        _reply(out)


def _reply(obj: dict) -> None:
    # 바이트로 쓴다 — 자식은 -I·최소 환경이라 로캘에 기대지 않는다.
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


# ── 부모 ──────────────────────────────────────────────────────────────────
class MorphWorkerError(RuntimeError):
    """자식이 답을 못 줬다 — 호출부는 같은 프로세스 Kiwi 로 처리한다."""


class MorphWorker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc = None
        self._last_used = 0.0
        self._started_at = 0.0
        self._served = 0            # 지금 자식이 답한 수(0 이면 다음 답이 첫 답 — 모델 적재 시간을 기다린다)
        self.spawns = 0
        self.requests = 0
        self.failures = 0           # 연속 실패
        self.fallbacks = 0          # 같은 프로세스로 넘긴 요청 수(누계)
        self.exits = 0              # 스스로 끝난 자식(대개 쉼 — 부모가 다음 요청·헬스체크 때 거둔 수)
        self.last_error = ""
        self.disabled_reason = ""

    # 켜짐 판단은 부를 때마다 — 시험·운영이 환경변수로 끌 수 있게
    def enabled(self) -> bool:
        if os.environ.get(_ENV_ON, "1").strip() in ("0", "false", "off", "no"):
            return False
        return os.name == "posix" and not self.disabled_reason

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _spawn(self) -> None:
        import subprocess
        # -I: 스크립트 폴더(services/)를 sys.path 에 넣지 않고 PYTHON* 환경도 안 본다 — 이 폴더의 모듈 이름이
        #   표준 라이브러리를 가리는 일이 없다. 환경은 최소만 — 자식은 키·DB 주소가 필요 없다.
        env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR") if k in os.environ}
        self._proc = subprocess.Popen(
            [sys.executable, "-I", os.path.abspath(__file__), "--serve", str(_idle_sec())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            close_fds=True, env=env,
        )
        self.spawns += 1
        self._started_at = time.time()
        self._served = 0

    def _stop_locked(self) -> None:
        p, self._proc = self._proc, None
        if p is None:
            return
        try:
            if p.stdin:
                p.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            p.wait(timeout=2)
        except Exception:  # noqa: BLE001
            p.kill()
            try:
                p.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass
        for f in (p.stdout,):
            try:
                if f:
                    f.close()
            except Exception:  # noqa: BLE001
                pass

    def _roundtrip(self, text: str) -> list[tuple[str, str]]:
        p = self._proc
        p.stdin.write((json.dumps(text, ensure_ascii=False) + "\n").encode("utf-8"))
        p.stdin.flush()
        wait = _FIRST_REPLY_SEC if self._served == 0 else _REPLY_SEC
        ready, _, _ = select.select([p.stdout], [], [], wait)
        if not ready:
            raise MorphWorkerError("timeout")
        line = p.stdout.readline()
        if not line:
            raise MorphWorkerError("eof")
        msg = json.loads(line)
        if "err" in msg:
            raise MorphWorkerError(f"child:{msg['err']}")
        self._served += 1
        return [(f, t) for f, t in msg["ok"]]

    def tokenize(self, text: str) -> list[tuple[str, str]]:
        """(형태, 품사) 목록. 실패면 `MorphWorkerError` — 호출부가 같은 프로세스로 처리한다."""
        with self._lock:
            self.requests += 1
            now = time.time()
            # 자식이 막 쉬러 들어가는 순간에 쓰면 답 없이 닫힌다 — 쉴 시각에 가까우면 미리 새로 띄운다(그래도 닫히면 한 번 더).
            if self._alive() and now - self._last_used > _idle_sec() * 0.9:
                self._stop_locked()
            if self._proc is not None and not self._alive():
                self.exits += 1
                self._stop_locked()
            for attempt in (1, 2):
                try:
                    if self._proc is None:
                        self._spawn()
                    out = self._roundtrip(text)
                    self._last_used = time.time()
                    self.failures = 0
                    return out
                except Exception as exc:  # noqa: BLE001 — 어떤 실패든 이 자식은 버린다
                    self.last_error = (str(exc) if isinstance(exc, MorphWorkerError) else type(exc).__name__)[:60]
                    self._stop_locked()
                    child_said_no = isinstance(exc, MorphWorkerError) and str(exc).startswith("child:unavailable")
                    if attempt == 2 or child_said_no or isinstance(exc, OSError) and not isinstance(exc, BrokenPipeError):
                        break
            self.failures += 1
            if self.failures >= _MAX_FAILURES or self.last_error.startswith("child:unavailable"):
                self.disabled_reason = self.last_error or "failures"
            raise MorphWorkerError(self.last_error)

    def stop(self) -> bool:
        """자식을 내린다(다음 요청이 다시 띄운다). 요청 처리 중이면 기다리지 않고 False."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            was = self._alive()
            self._stop_locked()
            return was
        finally:
            self._lock.release()

    def stats(self) -> dict:
        """`/health` 용 — 켜짐·살아 있나·자식 RSS(리눅스)·횟수. 자식 PID·명령줄은 싣지 않는다."""
        alive = self._proc is not None     # 요청 처리 중(잠금을 못 얻음)이면 이 값 그대로
        busy = True
        rss = None
        if self._lock.acquire(blocking=False):
            busy = False
            try:
                if self._proc is not None and not self._alive():
                    self.exits += 1
                    self._stop_locked()         # 끝난 자식을 거둔다(좀비로 두지 않는다)
                alive = self._alive()
                if alive:
                    try:
                        with open(f"/proc/{self._proc.pid}/status") as f:
                            for line in f:
                                if line.startswith("VmRSS:"):
                                    rss = round(int(line.split()[1]) / 1024, 1)
                                    break
                    except Exception:  # noqa: BLE001 — 리눅스 밖
                        pass
            finally:
                self._lock.release()
        return {
            "mode": "subprocess" if self.enabled() else "inprocess",
            "alive": alive, "busy": busy, "rss_mb": rss, "idle_exit_sec": _idle_sec(),
            "idle_sec": round(time.time() - self._last_used, 1) if alive and self._last_used else None,
            "spawns": self.spawns, "requests": self.requests, "fallbacks": self.fallbacks,
            "exits": self.exits, "last_error": self.last_error or None,
            "disabled_reason": self.disabled_reason or None,
        }


_WORKER: MorphWorker | None = None
_WORKER_INIT = threading.Lock()


def worker() -> MorphWorker:
    global _WORKER
    if _WORKER is None:
        with _WORKER_INIT:
            if _WORKER is None:
                import atexit
                _WORKER = MorphWorker()
                atexit.register(_WORKER.stop)
    return _WORKER


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--serve":
        raise SystemExit(_serve(int(sys.argv[2]) if len(sys.argv) > 2 else _DEFAULT_IDLE_SEC))
