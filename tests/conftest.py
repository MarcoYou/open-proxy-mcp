"""테스트 공통 환경.

1) 네트워크·운영 DB 를 만지는 백그라운드 작업은 항상 끈다.
2) `pg17` — 로컬 Docker 의 Postgres 17 (뷰·권한·트랜잭션처럼 SQLite 로는 검증할 수 없는 SQL 용). Docker 가 없으면 그 테스트만 건너뛴다.
   🔴 외부 DB 주소를 받는 옵션은 일부러 없다 — 테스트가 운영 DB 에 표를 만드는 길을 열어 두지 않는다.
"""
import os
import shutil
import socket
import subprocess
import time
import uuid

import pytest

# 서버 앱을 띄우는 테스트(`build_app()` + TestClient)가 KRX 시세 백그라운드 갱신기를 띄우지 않게 한다.
# 🔴 setdefault 가 아니라 **강제**다 — 개발자 셸에 `OPM_KRX_REFRESHER=1` 과 운영 `DATABASE_URL` 이 있어도 테스트가 운영 DB 를 쓰는 갱신기를
# 띄우지 못하게 한다. 갱신기 자체의 테스트는 `monkeypatch` 로 값을 바꾸거나 함수를 직접 부른다.
os.environ["OPM_KRX_REFRESHER"] = "0"
# 갱신기의 지수 종가 쓰기(`krx_idx`)도 기본으로 끈다 — 갱신기 시험이 `run_cycle` 을 직접 부를 때 개발자 셸의 `DATABASE_URL` 로 지수 쪽이 DB 를
# 읽거나 쓰지 않게. 지수 시험(`test_krx_idx_refresher.py`)은 `monkeypatch.setenv` 로 켠다.
os.environ["OPM_KRX_IDX_WRITE"] = "0"
# 회사 명부(DART corpCode) 백그라운드 갱신기도 같은 이유로 끈다 — 테스트가 DART 에서 명부를 받거나 개발자의 configs/master.db 를 덮지 않게.
os.environ["OPM_CORP_REFRESHER"] = "0"
# 콜드 서빙(DB 밖 서빙본을 받아 읽는 것)도 끈다 — 개발자 셸의 `OPM_COLD=1`·서명 URL 로 시험이 네트워크·/data 를 만지지 않게.
# 콜드 서빙 시험은 `monkeypatch.setenv` 와 아래 `cold_serving` 고정물(로컬 file:// 서빙본)로 켠다.
os.environ["OPM_COLD"] = "0"
os.environ.pop("OPM_COLD_MANIFEST_URL", None)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _reset_weekly_start_cache():
    """배수 도구의 주간 구간 시작 캐시(`_WEEKLY_START_CACHE`, 1시간)가 시험 사이로 새지 않게 비운다 — 모듈을 이미 읽었을 때만(읽기 비용 0)."""
    import sys

    def _clear():
        mod = sys.modules.get("open_proxy_mcp.services.price_multiple_data")
        for name in ("_WEEKLY_START_CACHE", "_AGG_FIRST_CACHE"):     # 261021: 집계 표의 DB 가장 이른 날짜 캐시도
            cache = getattr(mod, name, None)
            if cache is not None:
                cache.clear()
    _clear()
    yield
    _clear()


@pytest.fixture(scope="session")
def _pg17_server():
    """세션당 컨테이너 하나. 반환: (관리용 접속 URL, URL 만들기 함수)."""
    if not shutil.which("docker"):
        pytest.skip("docker 없음 — Postgres 17 검증은 건너뜀(SQLite 로 되는 부분만 검사)")
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=20, check=True)
    except Exception:
        pytest.skip("docker 데몬이 응답하지 않음 — Postgres 17 검증은 건너뜀")
    port, name = _free_port(), f"opm-pg17-test-{uuid.uuid4().hex[:8]}"
    try:
        subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=test",
                        "-p", f"127.0.0.1:{port}:5432", "postgres:17"],
                       capture_output=True, timeout=180, check=True)
    except Exception as exc:
        pytest.skip(f"postgres:17 컨테이너를 띄우지 못함({type(exc).__name__}) — 건너뜀")
    import psycopg
    admin = f"postgresql://postgres:test@127.0.0.1:{port}/postgres"
    deadline = time.time() + 90
    while True:
        try:
            psycopg.connect(admin, connect_timeout=2).close()
            break
        except Exception:
            if time.time() > deadline:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True)
                pytest.skip("postgres:17 이 90초 안에 준비되지 않음 — 건너뜀")
            time.sleep(0.5)
    try:
        yield admin, (lambda db: f"postgresql://postgres:test@127.0.0.1:{port}/{db}")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture
def pg17(_pg17_server):
    """테스트마다 새 데이터베이스 하나(+ Supabase 와 같은 역할·기본 권한). 접속 URL 을 돌려준다."""
    import psycopg
    admin, url_for = _pg17_server
    db = "t_" + uuid.uuid4().hex[:10]
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{db}"')
        # 클러스터 전역 역할은 한 번만 만든다.
        for role, attrs in (("anon", "NOLOGIN"), ("authenticated", "NOLOGIN"),
                            ("service_role", "NOLOGIN BYPASSRLS"), ("opm_ro", "NOLOGIN")):
            if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                c.execute(f"CREATE ROLE {role} {attrs}")
    url = url_for(db)
    with psycopg.connect(url, autocommit=True) as c:
        # 운영과 같은 기본 권한: postgres 가 public 에 새로 만드는 표·뷰에 anon/authenticated 가 SELECT 를 자동으로 받는다.
        # 🔴 opm_ro 는 일부러 뺀다 — 운영 기본 권한에 의존하지 않고 DDL 이 직접 줘야 읽히는지(권한 테스트가 헛돌지 않는지) 보려는 것이다.
        c.execute("ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public "
                  "GRANT SELECT ON TABLES TO anon, authenticated")
        c.execute("ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public "
                  "GRANT ALL ON TABLES TO service_role")
    yield url
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')


@pytest.fixture(autouse=True)
def _reset_dart_request_memo_and_company_info_cache():
    """DART 회사 개황 프로세스 캐시(`_COMPANY_INFO_CACHE`, 1시간)와 요청 단위 메모(`dart_memo`)가 시험 사이로 새지 않게 비운다.

    시험이 요청 장부를 동기 문맥에서 만들면 그 장부가 다음 시험으로 흘러가고(문맥 변수), 클라이언트 싱글턴이 같으면 메모가
    다른 시험의 가짜 응답에 적중할 수 있다 — 장부 자체는 두고 메모만 지운다. 모듈을 이미 읽었을 때만(읽기 비용 0)."""
    import sys

    def _clear():
        mod = sys.modules.get("open_proxy_mcp.dart.client")
        if mod is None:
            return
        cache = getattr(mod, "_COMPANY_INFO_CACHE", None)
        if cache is not None:
            cache.clear()
        ledger_var = getattr(mod, "_ctx_ledger", None)
        ledger = ledger_var.get() if ledger_var is not None else None
        if isinstance(ledger, dict):
            ledger.pop("dart_memo", None)

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _reset_cold_store():
    """콜드 서빙 모듈의 프로세스 상태(받은 목록·검증된 파일·카운터)가 시험 사이로 새지 않게 — 모듈을 이미 읽었을 때만."""
    import sys
    yield
    mod = sys.modules.get("open_proxy_mcp.cold_store")
    if mod is not None:
        mod.reset_for_tests()


def build_cold_serving(root, rows, *, upto, dataset="krx_weekly_hist", min_dd=None, max_dd=None, url=None,
                       url_expires_at=None, tamper_sha=False, tamper_object_sha=False):
    """발행기와 같은 모양의 서빙본 sqlite·gz·`serve/manifest.json` 을 root 아래에 만든다(시험 전용).

    rows = [(ticker, price_dd, market, close, list_shrs)]. 반환 dict(manifest_path, manifest_url, entry, sqlite_path, object_path)."""
    import gzip
    import hashlib
    import json
    import sqlite3
    from pathlib import Path

    root = Path(root)
    build = root / "build"
    build.mkdir(parents=True, exist_ok=True)
    tmp = build / f"{dataset}.building.sqlite"
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp, isolation_level=None)
    con.execute("CREATE TABLE krx_wk_hist (ticker TEXT NOT NULL, price_dd TEXT NOT NULL, market TEXT, close INTEGER NOT NULL, "
                "list_shrs INTEGER NOT NULL, PRIMARY KEY (ticker, price_dd)) WITHOUT ROWID")
    con.execute("CREATE VIEW krx_weekly AS SELECT price_dd, ticker, market, close, close * list_shrs AS mktcap, list_shrs "
                "FROM krx_wk_hist")
    con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID")
    con.executemany("INSERT INTO krx_wk_hist VALUES (?, ?, ?, ?, ?)", sorted(rows))
    con.execute("INSERT INTO meta VALUES ('dataset', ?)", (dataset,))
    con.execute("PRAGMA application_id = 1330662723")    # 0x4F504D43
    con.execute("PRAGMA user_version = 1")
    con.execute("VACUUM")
    con.close()
    data = tmp.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    fname = f"{dataset}.{sha[:16]}.sqlite"
    sqlite_path = build / fname
    tmp.replace(sqlite_path)
    obj_dir = root / "serve" / dataset
    obj_dir.mkdir(parents=True, exist_ok=True)
    object_path = obj_dir / (fname + ".gz")
    with open(object_path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as g:
        g.write(data)
    obj = object_path.read_bytes()
    dates = sorted(r[1] for r in rows)
    entry = {
        "file": fname, "object": f"serve/{dataset}/{fname}.gz", "encoding": "gzip",
        "sha256": ("0" * 64) if tamper_sha else sha, "bytes": len(data),
        "object_sha256": ("0" * 64) if tamper_object_sha else hashlib.sha256(obj).hexdigest(), "object_bytes": len(obj),
        "rows": len(rows),
        "coverage": {"min_dd": min_dd or (dates[0] if dates else "20151230"), "max_dd": max_dd or upto, "upto": upto,
                     "last_row_dd": dates[-1] if dates else None},
        "file_schema": 1,
    }
    if url is not None:
        entry["url"] = url
    if url_expires_at is not None:
        entry["url_expires_at"] = url_expires_at
    manifest = {"schema": 1, "kind": "opm-cold-serve", "datasets": {dataset: {
        "engine": "sqlite3", "view": "krx_weekly",
        "columns": ["price_dd", "ticker", "market", "close", "mktcap", "list_shrs"],
        "key": ["ticker", "price_dd"], "date_col": "price_dd",
        "files": [entry], "coverage": {"min_dd": entry["coverage"]["min_dd"], "max_dd": entry["coverage"]["max_dd"]}}}}
    mpath = root / "serve" / "manifest.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False))
    return {"manifest_path": mpath, "manifest_url": "file://" + str(mpath), "entry": entry, "manifest": manifest,
            "sqlite_path": sqlite_path, "object_path": object_path, "object_bytes": obj, "sqlite_bytes": data}


@pytest.fixture
def cold_serving(tmp_path, monkeypatch):
    """콜드 서빙을 켠 상태 — `make(rows, upto=…)` 로 서빙본·목록을 만들고 환경을 그쪽으로 돌린다. 캐시는 tmp_path/cache."""
    def make(rows, *, upto, **kw):
        info = build_cold_serving(tmp_path / "src", rows, upto=upto, **kw)
        monkeypatch.setenv("OPM_COLD", "1")
        monkeypatch.setenv("OPM_COLD_MANIFEST_URL", info["manifest_url"])
        monkeypatch.setenv("OPM_COLD_DIR", str(tmp_path / "cache"))
        return info
    return make


#: 261021: 집계 서빙본 두 벌 — 발행기 Spec(`krx_cap_agg_hist`·`opm_val_market_hist`)과 같은 표 = 뷰 이름·칸·기본키·서버 열쇠.
COLD_AGG_SPECS = {
    "krx_cap_agg_hist": {
        "view": "krx_cap_agg",
        "cols": [("price_dd", "TEXT NOT NULL"), ("market", "TEXT NOT NULL"), ("scheme", "TEXT NOT NULL"), ("bucket", "TEXT NOT NULL"),
                 ("label", "TEXT"), ("n", "INTEGER NOT NULL"), ("cap", "INTEGER NOT NULL"), ("sector_asof", "TEXT")],
        "pk": ("scheme", "price_dd", "market", "bucket"), "key": ["scheme", "price_dd"]},
    "opm_val_market_hist": {
        "view": "opm_val_market",
        "cols": [("snap_dd", "TEXT NOT NULL"), ("market", "TEXT NOT NULL"), ("per_fy0", "REAL"), ("per_ttm", "REAL"), ("pbr_fy0", "REAL"),
                 ("pbr_mrq", "REAL"), ("cap", "REAL"), ("ni_ttm", "REAL"), ("eq", "REAL"), ("cap_pref", "REAL"),
                 ("sector", "TEXT NOT NULL"), ("label", "TEXT"), ("n", "INTEGER"), ("scheme", "TEXT NOT NULL"), ("ni_fy0", "REAL")],
        "pk": ("scheme", "snap_dd", "market", "sector"), "key": ["scheme", "snap_dd"]},
}


def build_cold_hist(root, datasets: dict, *, upto: str):
    """집계 서빙본(들)과 한 목록(`serve/manifest.json`)을 만든다 — 발행기와 같은 모양(시험 전용).

    datasets = {이름: rows(dict 목록 — 칸 이름 → 값, 없는 칸은 NULL)} 또는 {이름: (rows, {"min_dd":…, "max_dd":…})}.
    coverage 기본 = 행의 가장 이른 날짜 ~ upto 전날. 반환 manifest_url."""
    import datetime as _dt
    import gzip
    import hashlib
    import json
    import sqlite3
    from pathlib import Path

    root = Path(root)
    (root / "build").mkdir(parents=True, exist_ok=True)
    entries = {}
    for name, val in datasets.items():
        rows, cov = (val if isinstance(val, tuple) else (val, {}))
        spec = COLD_AGG_SPECS[name]
        names = [c for c, _ in spec["cols"]]
        tmp = root / "build" / f"{name}.building.sqlite"
        if tmp.exists():
            tmp.unlink()
        con = sqlite3.connect(tmp, isolation_level=None)
        con.execute(f"CREATE TABLE {spec['view']} ({', '.join(f'{c} {t}' for c, t in spec['cols'])}, "
                    f"PRIMARY KEY ({', '.join(spec['pk'])})) WITHOUT ROWID")
        con.executemany(f"INSERT INTO {spec['view']} VALUES ({', '.join('?' for _ in names)})",
                        [tuple(r.get(c) for c in names) for r in rows])
        con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID")
        con.execute("INSERT INTO meta VALUES ('dataset', ?)", (name,))
        con.execute("PRAGMA application_id = 1330662723")
        con.execute("PRAGMA user_version = 1")
        con.execute("VACUUM")
        con.close()
        data = tmp.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        fname = f"{name}.{sha[:16]}.sqlite"
        tmp.replace(root / "build" / fname)
        obj_dir = root / "serve" / name
        obj_dir.mkdir(parents=True, exist_ok=True)
        obj_path = obj_dir / (fname + ".gz")
        with open(obj_path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as g:
            g.write(data)
        obj = obj_path.read_bytes()
        date_col = spec["key"][1]
        dates = sorted(r[date_col] for r in rows)
        max_dd = (_dt.datetime.strptime(upto, "%Y%m%d") - _dt.timedelta(days=1)).strftime("%Y%m%d")
        coverage = {"min_dd": cov.get("min_dd") or (dates[0] if dates else "20151230"), "max_dd": cov.get("max_dd") or max_dd}
        entry = {"file": fname, "object": f"serve/{name}/{fname}.gz", "encoding": "gzip", "sha256": sha, "bytes": len(data),
                 "object_sha256": hashlib.sha256(obj).hexdigest(), "object_bytes": len(obj), "rows": len(rows),
                 "coverage": {**coverage, "upto": upto}, "file_schema": 1}
        entries[name] = {"engine": "sqlite3", "view": spec["view"], "columns": names, "key": spec["key"], "date_col": date_col,
                         "files": [entry], "coverage": coverage}
    mpath = root / "serve" / "manifest.json"
    mpath.write_text(json.dumps({"schema": 1, "kind": "opm-cold-serve", "datasets": entries}, ensure_ascii=False))
    return "file://" + str(mpath)


@pytest.fixture
def cold_hist(tmp_path, monkeypatch):
    """집계 서빙본을 켠 상태 — `make({이름: rows}, upto=…)`."""
    def make(datasets, *, upto):
        url = build_cold_hist(tmp_path / "src", datasets, upto=upto)
        monkeypatch.setenv("OPM_COLD", "1")
        monkeypatch.setenv("OPM_COLD_MANIFEST_URL", url)
        monkeypatch.setenv("OPM_COLD_DIR", str(tmp_path / "cache"))
        return url
    return make
