# -*- coding: utf-8 -*-
"""표준 서식 구간 매핑 로더 — 개념명 → 원문 구간 식별자.

정기보고서 원문은 제출 서식이 남긴 식별자를 갖고 있어, 특정 주석·구간을 제목 텍스트가 아니라
식별자로 짚을 수 있다. 그 대응 표는 코드에 박지 않고 **외부 데이터 파일**로 둔다 —
개념이 늘어날 때 코드 변경 없이 데이터만 갱신하기 위함이다.

파일이 없으면 조용히 넘어가지 않고 `loaded=False`를 남긴다. 호출측은 기존 텍스트 경로로
폴백하되 진단 필드로 그 사실을 표면화해야 한다(무표시 열화 금지).

경로: 환경변수 `OPM_COORDINATE_MAP_PATH` (기본 `/data/coordinate_map.json`).
mtime이 바뀌면 다시 읽는다 — 프로세스가 suspend 로 장시간 살아 있어도 갱신이 반영되게.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

DEFAULT_PATH = "/data/coordinate_map.json"
_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"path": None, "mtime": None, "data": None, "error": None}


def _path() -> str:
    return os.environ.get("OPM_COORDINATE_MAP_PATH") or DEFAULT_PATH


def load() -> dict[str, Any]:
    """{loaded, version, concepts, error} — 실패해도 예외를 던지지 않는다."""
    p = _path()
    try:
        st = os.stat(p)
    except OSError as exc:
        return {"loaded": False, "version": None, "concepts": {},
                "error": f"{type(exc).__name__}: {p}"}
    with _LOCK:
        if _CACHE["path"] == p and _CACHE["mtime"] == st.st_mtime and _CACHE["data"] is not None:
            return _CACHE["data"]
        try:
            with open(p, encoding="utf-8") as fh:
                raw = json.load(fh)
            data = {"loaded": True, "version": raw.get("version"),
                    "concepts": raw.get("concepts") or {}, "error": None}
        except Exception as exc:                      # 깨진 파일도 서비스를 죽이지 않는다
            data = {"loaded": False, "version": None, "concepts": {},
                    "error": f"{type(exc).__name__}: {exc}"}
        _CACHE.update({"path": p, "mtime": st.st_mtime, "data": data})
        return data


def concept(name: str) -> dict[str, Any] | None:
    """개념 하나의 정의. 없으면 None."""
    c = load()["concepts"].get(name)
    return c if isinstance(c, dict) else None


def status() -> dict[str, Any]:
    """응답 진단용 — 매핑 탑재 여부를 호출측이 그대로 실어 보낼 수 있게."""
    m = load()
    return {"loaded": m["loaded"], "version": m["version"],
            "concepts": len(m["concepts"]),
            **({"error": m["error"]} if m["error"] else {})}
