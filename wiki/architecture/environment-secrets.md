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
| `OPENDART_API_KEY_2` | DART 2번째 키 (분당한도 fallback·키 회전) | 권장 | opendart.fss.or.kr |
| `KRX_OPEN_API_KEY` | KRX 시세·시총·상장주식수 (밸류에이션·수정주가) | 밸류에이션 필수 | data.krx.co.kr |
| `ECOS_API_KEY` | 한국은행 환율(매매기준율) — 기능통화 USD사(두산밥캣) KRW 환산 | 밸류에이션 필수 | ecos.bok.or.kr → 오픈API 인증키 |
| `ANTHROPIC_API_KEY` | LLM fallback (파싱 실패 시) | 선택 | console.anthropic.com |
| `OPENAI_API_KEY` | LLM fallback (대체) | 선택 | platform.openai.com |
| `UPSTAGE_API_KEY` | OCR (이미지 공고) — **OPM에선 미사용**, open-proxy-ai(PDF/OCR 폴백)로 이관 260712 | OPM 불필요 | upstage.ai |
| `OPEN_LAW_API_KEY` | 국가법령정보 API (법령 layer) | 선택 | open.law.go.kr |
| `NAVER_SEARCH_API_CLIENT_ID` / `..._SECRET` | 네이버 검색(뉴스 체크) | 선택 | developers.naver.com |
| `DATABASE_URL` | Supabase Postgres — 사용통계·KRX데이터·FX캐시·밸류에이션 배치 | 배치·통계 필수 | Supabase 콘솔 |

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
