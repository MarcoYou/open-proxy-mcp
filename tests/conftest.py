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
# 갱신기 자체의 테스트는 이 값을 직접 바꾸거나 함수를 직접 부른다.
os.environ.setdefault("OPM_KRX_REFRESHER", "0")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
        # 운영과 같은 기본 권한: postgres 가 public 에 새로 만드는 표·뷰에 anon/authenticated/opm_ro 가 SELECT 를 자동으로 받는다.
        c.execute("ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public "
                  "GRANT SELECT ON TABLES TO anon, authenticated, opm_ro")
        c.execute("ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public "
                  "GRANT ALL ON TABLES TO service_role")
    yield url
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
