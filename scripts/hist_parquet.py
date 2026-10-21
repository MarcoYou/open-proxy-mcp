"""3년 밖 집계 행을 DB 대신 Parquet 로 — 콜드 보관본(서빙본 발행기의 원천)에 올릴 파일을 만든다. DART·KRX 0콜, DB 쓰기 0.

261021 신설. 시장·업종 집계(`krx_cap_agg`)와 배수(`opm_val_market`)는 DB 에 3년 창(보존 하한 `krx_weekly_cutoff` 이후)만 둔다.
그 앞을 다시 계산하는 길(`krx_cap_agg.py --cold-sqlite … --out`, 두 배수 백필의 `--hist-out`)은 결과를 여기로 내보낸다 —
올리기·서빙본 발행(발행 전 검사: 섹터 합 = 시장 합 등)은 저장소 밖 운영 도구 몫이다.

칸 이름·순서·형은 호출부가 DB 표와 같게 준다. 쓰고 나서 되읽어 행 수와 칸을 확인하고, 파일 sha256 을 돌려준다.
의존: duckdb — dev 그룹(`uv sync`, 서버 런타임 의존성 아님). 지연 import.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

_IDENT = re.compile(r"[a-z_][a-z0-9_]*")
_TYPES = {"VARCHAR", "INTEGER", "BIGINT", "DOUBLE"}


def write(path: str | Path, cols: list[tuple[str, str]], rows: list[tuple]) -> dict:
    """`rows` 를 `cols`(이름, duckdb 형) 모양의 Parquet(zstd) 로 쓴다 — 임시 파일에 쓰고 되읽어 확인한 뒤 바꿔 끼운다.
    반환 {rows, sha256, bytes}. 칸 수가 어긋난 행·모르는 형·식별자가 아닌 이름이면 ValueError(아무것도 쓰지 않는다)."""
    import duckdb  # 지연 — 이 모듈을 import 만 하는 쪽은 duckdb 가 없어도 된다

    if not cols or any(not _IDENT.fullmatch(n) or t not in _TYPES for n, t in cols):
        raise ValueError(f"칸 정의가 이상하다: {cols}")
    if any(len(r) != len(cols) for r in rows):
        raise ValueError("칸 수가 다른 행이 있다")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.part")
    duck = duckdb.connect()
    try:
        duck.execute("CREATE TABLE t (" + ", ".join(f"{n} {t}" for n, t in cols) + ")")
        if rows:
            duck.executemany("INSERT INTO t VALUES (" + ", ".join("?" for _ in cols) + ")", rows)
        order = ", ".join(n for n, _ in cols)
        duck.execute(f"COPY (SELECT * FROM t ORDER BY {order}) TO '{tmp.as_posix()}' (FORMAT parquet, COMPRESSION zstd)")
        got = duck.execute(f"SELECT count(*) FROM read_parquet('{tmp.as_posix()}')").fetchone()[0]
        names = [r[0] for r in duck.execute(f"DESCRIBE SELECT * FROM read_parquet('{tmp.as_posix()}')").fetchall()]
        if got != len(rows) or names != [n for n, _ in cols]:
            raise RuntimeError(f"되읽기 불일치 — 행 {got}/{len(rows)}, 칸 {names}")
        os.replace(tmp, path)
    finally:
        duck.close()
        if tmp.exists():
            tmp.unlink()
    data = path.read_bytes()
    return {"rows": len(rows), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def read(path: str | Path, cols: list[str]) -> list[tuple]:
    """Parquet 를 (칸 순서대로) 읽는다 — 대조 기준(예: 지금 보관본의 시장 행)을 읽을 때."""
    import duckdb

    if not cols or any(not _IDENT.fullmatch(c) for c in cols):
        raise ValueError(f"칸 이름이 이상하다: {cols}")
    duck = duckdb.connect()
    try:
        return duck.execute(f"SELECT {', '.join(cols)} FROM read_parquet('{Path(path).as_posix()}')").fetchall()
    finally:
        duck.close()
