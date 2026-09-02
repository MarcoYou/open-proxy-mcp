---
type: architecture
title: 환경변수·시크릿 — 필요한 키 목록 + 설정 위치
updated: 2026-07-04
---

# 환경변수·시크릿

> **셋업 시 어떤 키가 필요한가 + 어디에 넣는가.** (`.env.example` 대체 — 260704 문서화로 일원화)
> 코드는 전부 `os.getenv("KEY")`로 읽으므로 **로컬 `.env`와 fly secrets에 같은 이름으로** 넣으면
> 로컬·배포 양쪽에서 동작한다. `.env`는 gitignore(커밋 금지) — 값은 절대 wiki·git에 올리지 않는다.

## 설정 위치 (두 곳)

| 환경 | 위치 | 방법 |
|---|---|---|
| **로컬 개발·스크립트** | 프로젝트 루트 `.env` (숨김파일, gitignore) | 파일에 `KEY=값` 추가 (`open -e .env`로 편집) |
| **배포 서버(fly.io)** | fly secrets (런타임 주입, fly.toml엔 비밀 아닌 설정만) | `fly secrets set KEY=값` — 자동 롤링 재배포 |

fly.toml `[env]`에는 경로 같은 **비밀 아닌 설정**만 둔다. API 키·DB URL은 전부 fly secrets.

## 필요한 키

| 키 | 용도 | 필수? | 발급처 |
|---|---|---|---|
| `OPENDART_API_KEY` | DART 공시 API (주 데이터 소스) | **필수** | opendart.fss.or.kr |
| `OPENDART_API_KEY_2` … `_N` | DART 보조 키 (배치·내부용 순번 회전). 분당 한도 910 은 키를 늘려도 그대로, 늘어나는 건 일일 쿼터뿐 | 권장 | opendart.fss.or.kr |
| `KRX_OPEN_API_KEY` | KRX 시세·시총·상장주식수 (밸류에이션·수정주가) | 밸류에이션 필수 | data.krx.co.kr |
| `ECOS_API_KEY` | 한국은행 환율(매매기준율) — 기능통화 USD사(두산밥캣) KRW 환산 | 밸류에이션 필수 | ecos.bok.or.kr → 오픈API 인증키 |
| `OPM_ADMIN_KEY` | `/admin/cache`·`/admin/memtop` 인증. 없으면 404 로 숨김 | 운영 | (자체 생성) |
| `OPM_MASTER_DB_PATH` / `OPM_DOC_CACHE_DIR` | corp_code sqlite · 문서 디스크 캐시 경로 (fly.toml `[env]`, 비밀 아님) | 운영 | — |
| `FASTMCP_HOST` / `FASTMCP_PORT` / `FASTMCP_ALLOWED_HOSTS` | 바인드 주소·포트·허용 호스트 (`run_beta.sh`) | 로컬 | — |
| `OPM_CAPTURE_DIR` | 요청·응답 전문 캡처(로컬 시험용, 배포 금지) | 로컬 | — |
| `OPM_DOC_CACHE_MB`·`OPM_DIVIDEND_CACHE_MB`·`OPM_DOC_DISK_CACHE_MB`·`OPM_DOC_DISK_SWEEP_MB`·`OPM_CACHE_HIGH_RATIO`·`OPM_CACHE_LOW_RATIO`·`OPM_DOC_CONCURRENCY`·`OPM_DOC_GATE_WAIT_SEC`·`OPM_DOC_CONCURRENCY_PER_KEY`·`OPM_CLIENT_MAX`·`OPM_CLIENT_IDLE_SEC`·`OPM_PG_POOL_MAX/MIN/TIMEOUT/RETRY_SEC`·`OPM_SCAN_CACHE_TTL_SEC`·`OPM_SCAN_CACHE_CLOSED_SEC` | 캐시·동시성·풀 튜닝 노브. 기본값은 코드(`dart/client.py`·`db.py`·`services/screener.py`) | 선택 | — |
| `NAVER_SEARCH_API_CLIENT_ID` / `..._SECRET` | 네이버 검색(뉴스 체크) | 선택 | developers.naver.com |
| `DATABASE_URL` | Supabase Postgres — 사용통계·KRX데이터·FX캐시·밸류에이션·컨센서스·스크리너 | 배치·통계·DB 기반 scope 필수 | Supabase 콘솔 |
| `KRX_API_KEY` | KRX_OPEN_API_KEY 와 같은 키의 별칭 (`services/price_multiple_data.py`·`trading.py` 가 둘 다 본다) | — | — |

- **필수** = 이게 없으면 핵심 tool이 동작 안 함. **선택** = 해당 기능만 비활성(앱은 기동).
- `FLY_io` = fly 배포 토큰(로컬 편의용, CI는 GitHub Secrets `FLY_API_TOKEN` 사용).

## 새 키 추가 시 (체크리스트)

1. 로컬 `.env`에 `KEY=값` 추가.
2. `fly secrets set KEY=값` (배포 반영).
3. **이 문서 표에 한 줄 추가** (셋업 문서 일원화 — `.env.example` 없이 여기가 단일 출처).
4. 코드는 `os.getenv("KEY")`로 읽기 (순서·위치 의존 X — CLAUDE.md 원칙).

## 관련

- 셋업 흐름: CLAUDE.md 「셋업·개발」 (이 문서 참조)
- FX(ECOS) 캐싱 설계: private wiki · `open_proxy_mcp/dart/fx.py`
- 배포: `.github/workflows/deploy.yml` (fly.io)
