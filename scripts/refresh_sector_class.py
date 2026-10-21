#!/usr/bin/env python3
"""업종분류 수집 — 정적 맵(JSON) + Postgres 스냅샷 적재.

한국 종목의 업종분류(대분류 10 / 하위업종 28)를 분류 공급처의 공개 엔드포인트에서 수집한다.
분류만 담당하며 가격·시가총액은 수집하지 않는다(원시세 재배포 방지).

대시보드 프로젝트(Mirae_Asset_Securities)의 업종분류 수집 코드를
포팅. 완전성 검사·중복분류 거부·asOf 산출 규칙을 원본과 동일하게 유지한다.

엔드포인트 (분류 공급처 공개 API — 주소는 BASE_URL):
  - 분류 트리      GET /API/Tree/Get?id=4
  - 업종 구성종목  GET /Index/GetIndexComponets?ceil_yn=0&dt=YYYYMMDD&sec_cd=G4530
  `Componets` 철자는 사이트 원본 그대로다(오타 아님).

기준일: 자동 실행은 서울 기준 **직전 금요일 이하**에서 최대 14일 전까지 찾는다.
주말 또는 첫 하위업종의 명시적인 무자료 응답만 건너뛴다. --date/WICS_DATE는 기본적으로
그 날짜만 조회한다(--lookback-days로 명시적으로 소급 허용). requestedDate는 원래 요청일,
asOf는 모든 하위업종이 같은 날짜로 확인된 실제 기준일이다. 무자료 이외 오류는 즉시 중단한다.

무결성 규칙 (하나라도 걸리면 아무것도 쓰지 않고 중단):
  - 대분류 10 미만 / 하위업종 20 미만 / 종목 1,000 미만 → 불완전 응답으로 판단
  - 동일 종목이 둘 이상의 하위업종에 나타나면 실패 (임의로 최근 응답을 고르지 않는다)
  - 날짜 누락·오염·혼합·조회일 불일치 또는 일부 업종만 빈 응답이면 실패
  - 무자료 = 빈 list + 정확한 .NET 최소 날짜 sentinel. 음수 날짜를 유효일로 허용하지 않는다

DB: WICS_DATABASE_URL > DATABASE_URL 순으로 DSN을 찾는다.
    OPM의 DATABASE_URL은 Supabase를 가리키므로, 대시보드가 쓰는 Neon에 넣으려면
    WICS_DATABASE_URL을 따로 설정할 것. 둘 다 없으면 DB 단계를 건너뛴다(파일만 생성).

★ 구성종목 ≠ 분류 대상 (261018). 공급처 응답은 업종 **지수의 구성종목**이다. 공급처는 상장 종목이라도
  지수 편입 요건에서 빠지면(사유는 응답에 없다) 구성종목에서 뺀다 — 그 회사의 업종분류는 그대로 둔 채로.
  실측: 20260828 에 있던 상장 종목 32개가 20261016 응답에서 빠졌다(유니온머티리얼·SG글로벌은 10-08~10-16 사이,
  KR모터스는 09-04~09-10 사이 — 날짜가 제각각). 공급처 기업 정보 화면은 그 종목들의 분류를 그대로 보여 준다
  (표본 10개 모두 직전 관측과 같은 하위업종). 우선주는 처음부터 구성종목이 아니지만 기업 정보 화면에서 보통주와
  같은 분류다(삼성전자우·현대차2우B·LG화학우·CJ4우(전환)·LG우 실측). 그래서 DB 의 `wise_sector` 는
  관측 행에 **파생 행 두 가지**를 더한다(같은 스냅샷 안, 출처를 `src`·`src_ref` 칸에 남긴다):
    · `wise`  — 이 날 공급처 구성종목 응답에 있던 관측 행(`src_ref` 없음).
    · `carry` — 직전 스냅샷에 분류가 있었고 이 날도 상장(이 날 이하 가장 늦은 시세일의 시세 저장분에 있음)인데
                구성종목에서만 빠진 종목. 직전 분류를 잇는다. `src_ref` = 그 분류를 **실제로 관측한** 스냅샷 날짜.
    · `pref`  — 이 날 상장된 우선주(코드 끝자리가 0 이 아님)인데 같은 회사 보통주(앞 5자리 + 0)가 이 스냅샷에
                분류돼 있는 것. 보통주 분류를 잇는다. `src_ref` = 보통주 코드.
  공급처가 한 번도 분류를 준 적 없는 보통주(외국 기업·장기 거래정지·신규상장 등)는 지어내지 않는다 — 분류가 없는 채로
  둔다(집계는 `_UNCLASSIFIED`). 정적 맵(JSON)과 `kr_wics_snapshots` 는 **관측만** 담는다(공급처 응답 그대로).
  옛 스냅샷을 다시 수집하면 그 날짜와 그 뒤 스냅샷의 파생 행을 차례로 다시 만든다(이어 쓰기가 앞 스냅샷에 기대므로).
  상장 여부를 모르는 날짜(주간 시세가 촘촘하지 않은 보존 하한 이전)는 있던 파생 행을 지우지 않고 그대로 둔다.
  옛 스냅샷의 파생 행이 바뀌면 섹터 시총 집계(`scripts/krx_cap_agg.py`)는 스스로 따라오지 않는다 — `--since` 로 다시 집계한다.

★ 저장 모양 두 가지 (261021, 재설계 E1 업종 구간형 — `scripts/wise_sector_iv_ddl.py`). `wise_sector` 가 **표**면 종전대로 스냅샷마다 행을 쓴다.
  **뷰**(교체 뒤 — 밑 표 = 구간 표 `wise_sector_iv` + 스냅샷 날짜 목록 `wise_sector_snap`)면 쓰는 트랜잭션 안에서 뷰의 지금 행을 임시 작업 표로
  펴서(`sector_store`) 아래 규칙을 **글자 그대로** 그 작업 표에 적용한 뒤, 바뀐 구간만 지우고 넣는다(구간 차분). 넣은 뒤 「뷰 = 작업 표」를
  전 행·전 칸 확인하고 어긋나면 전체를 되돌린다. 모드는 매번 DB 에서 읽는다 — 이 코드가 교체 전에 배포돼도 표 모드로 지금과 같이 돈다.

★ 두 번째 관측 원천 — 공급처 기업 정보 화면 (261018). 구성종목 응답에 없는 상장 보통주(위 이어 쓰기 대상 + 한 번도 분류를 받지
  못한 종목 — 20261016 기준 37 + 155)는 공급처의 기업 정보 화면(`COMPANY_URL`, 종목마다 1콜)이 분류를 직접 보여 준다.
  화면은 같은 분류 체계에서 저장 단계(대분류 → 하위업종)의 한 단계 아래인 「산업」 이름을 준다(예: 생물공학, 자동차부품).
  산업은 정확히 한 하위업종에 속하므로 하위업종으로 올리는 것은 결정적 대응이지 추정이 아니다 — 20261016 구성종목 관측 2,456종목
  **전수**의 화면 값 ↔ 저장 하위업종 쌍으로 확인했다(`open_proxy_mcp/data/sector_class/company_industry_map.json` 의 meta).
    · `wise_company` — 기업 정보 화면 관측 행. `src_ref` = 「조회일:화면의 산업 이름」. 관측으로 취급한다(다시 만들기가 지우지 않고,
      우선주가 잇고, 다음 스냅샷이 화면 조회에 실패하면 이어 쓴다).
  대응표에 없는 산업 이름(전수 측정에서 두 하위업종에 걸치거나 한 번도 보지 못한 이름)은 지어내지 않는다 — 그 종목은 이어 쓰기나 미분류로 남는다.
  쓰기 전에 그 달 쓰는 대응마다 대응표의 본보기 종목(그 스냅샷의 구성종목 관측 행)을 화면으로 다시 확인한다 — 본보기의 화면 값이 같은데
  저장 하위업종이 다르면(분류 체계 개편) 그 대응은 그 달 쓰지 않는다. 호출 상한(`WICS_COMPANY_MAX_CALLS`)·공통 웹 속도제한을 따르고,
  차단·오류 응답이 잇달아 3번이면 화면 조회만 멈춘다(구성종목 관측·파생 행 적재는 그대로 한다). 「올바른 종목이 아닙니다」
  (그 코드의 기업 화면이 없다 — 상장폐지 등)는 차단이 아니라 답이므로 연속 실패로 세지 않고 그 종목만 분류 없음으로 둔다.

실행:
  python scripts/refresh_sector_class.py                      # 직전 금요일, 파일 + DB
  python scripts/refresh_sector_class.py --date 20260814      # 정확한 날짜 지정
  python scripts/refresh_sector_class.py --date 20260925 --lookback-days 14  # 이하 유효일
  python scripts/refresh_sector_class.py --no-db              # 파일만
  python scripts/refresh_sector_class.py --no-file            # DB만
  python scripts/refresh_sector_class.py --dry-run            # 수집·검증만, 쓰기 없음
  python scripts/refresh_sector_class.py --rederive           # 수집 없이 모든 스냅샷의 파생 행만 다시 만든다
  python scripts/refresh_sector_class.py --rederive --dry-run # 다시 만들 행 수만 보고 되돌린다
  python scripts/refresh_sector_class.py --no-company         # 기업 정보 화면 조회 없이(구성종목 관측 + 파생만)
  python scripts/refresh_sector_class.py --company-only       # 수집 없이 최신 스냅샷에 기업 정보 화면 관측만 더한다
  python scripts/refresh_sector_class.py --company-only --snap all --dry-run  # 모든 스냅샷 대상, 쓸 행 수만 본다
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # Windows cp949 콘솔에서도 한글 출력 안전
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import httpx
from dotenv import load_dotenv

from open_proxy_mcp.dart.client import throttle_web_request
from scripts.wise_sector_iv_ddl import (FLUSH_APPLY, FLUSH_CHECK, FLUSH_CHECK_READ, FLUSH_OPEN, WORK_TABLE,
                                        relkind as _relkind)

load_dotenv(ROOT / ".env")

BASE_URL = "https://www.wiseindex.com"
OUT_PATH = ROOT / "open_proxy_mcp" / "data" / "sector_class" / "class_map.json"
KST = timezone(timedelta(hours=9))

#: 불완전 응답 판정 임계값 — 포팅 원본과 동일.
MIN_SECTORS, MIN_INDUSTRIES, MIN_TICKERS = 10, 20, 1_000

TICKER_RE = re.compile(r"^[0-9A-Z]{6}$")
DOTNET_DATE_RE = re.compile(r"^/Date\(([0-9]+)(?P<offset>[+-][0-9]{4})?\)/$")
ISO_DATE_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(?:[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?"
    r"(?P<offset>Z|[+-][0-9]{2}:?[0-9]{2})?)?"
)
NO_DATA_TRADE_DATE = "/Date(-62135596800000)/"
MAX_LOOKBACK_DAYS = 14

DDL_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS kr_wics_snapshots (
    as_of          text PRIMARY KEY,
    requested_date text NOT NULL,
    data           jsonb NOT NULL,
    sector_count   integer NOT NULL,
    industry_count integer NOT NULL,
    ticker_count   integer NOT NULL,
    refreshed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kr_wics_snapshots_refreshed
    ON kr_wics_snapshots (refreshed_at DESC);
"""

#: 옛 모양(스냅샷마다 행) 표 — `wise_sector` 가 아직 없는 DB(새 DB·대시보드 쪽)에서만 만든다. 뷰(E1 교체 뒤)면 만들지 않는다 —
#: 옛 표는 이름이 바뀌어 색인 이름 `idx_wise_sector_ticker` 가 비므로, 그대로 돌리면 뷰에 색인을 만들려다 실패한다.
DDL_TABLE = """
-- 260823(OPM): 위 blob 은 대시보드 모양이다. OPM 은 krx_weekly 와 **조인**해 섹터 시총을
--   집계하므로 종목당 한 행이 필요하다. jsonb 를 매 질의마다 펴는 건 비싸다.
--   표 이름은 OPM 규약의 출처 접두사를 따른다(DB 이름 변경은 보류).
CREATE TABLE IF NOT EXISTS wise_sector (
    snap_dd       text NOT NULL,      -- YYYYMMDD (asOf 를 _dd 규약으로)
    ticker        text NOT NULL,
    sector_code   text NOT NULL,      -- G25 등 대분류 10
    sector        text NOT NULL,
    industry_code text NOT NULL,      -- G2510 등 하위업종 28
    industry      text NOT NULL,
    PRIMARY KEY (snap_dd, ticker)
);
CREATE INDEX IF NOT EXISTS idx_wise_sector_ticker ON wise_sector (ticker, snap_dd);
"""
DDL = DDL_SNAPSHOTS + DDL_TABLE

#: 쓰는 표 이름의 기본값 — 표 모드. 뷰 모드면 `sector_store` 가 작업 표 이름(`_ws_work`)을 준다.
TABLE = "wise_sector"

#: 행의 출처(261018) — 모듈 머리말 「구성종목 ≠ 분류 대상」·「두 번째 관측 원천」.
SRC_OBSERVED, SRC_CARRY, SRC_PREF, SRC_COMPANY = "wise", "carry", "pref", "wise_company"
#: 월간 수집 시작 전 날짜를 공급처 과거 구성종목으로 뒤늦게 채운 관측(구간 표 출처 'h'). 이 수집기는 쓰지 않는다 — 다만 읽을 때 관측으로 본다.
SRC_HISTORY = "wise_hist"
#: 관측 출처 — 다시 만들기(`derive_snapshot`)가 지우지 않고, 이어 쓰기·우선주의 바탕이 되는 행.
OBSERVED_SRCS = (SRC_OBSERVED, SRC_COMPANY, SRC_HISTORY)
#: 파생 출처 — 다시 만들기가 지우고 새로 만드는 행.
DERIVED_SRCS = (SRC_CARRY, SRC_PREF)

SRC_COMMENTS = (
    "COMMENT ON COLUMN wise_sector.src IS "
    "'행 출처: wise=그 날 공급처 구성종목 응답(관측) · wise_company=구성종목에 없는 상장 보통주, 공급처 기업 정보 화면의 "
    "산업을 하위업종으로 올림(관측) · carry=관측에서 빠진 상장 종목, 직전 분류를 이음 · "
    "pref=우선주, 같은 회사 보통주 분류를 이음 (scripts/refresh_sector_class.py)'",
    "COMMENT ON COLUMN wise_sector.src_ref IS "
    "'carry=그 분류를 실제로 관측한 스냅샷 날짜(YYYYMMDD) · pref=보통주 코드 · "
    "wise_company=조회일(YYYYMMDD):기업 정보 화면의 산업 이름 · wise=NULL'",
)
#: 출처 칸 — 없을 때만 더한다. 상수 기본값 칸 추가는 표를 다시 쓰지 않지만 표 잠금은 잡으므로,
#: 읽는 질의 뒤에서 오래 기다리지 않게 잠금 대기를 5초로 끊는다(실패하면 다음 실행이 다시 한다).
SRC_COLUMNS_DDL = (
    "SET LOCAL lock_timeout = '5s'",
    f"ALTER TABLE wise_sector ADD COLUMN IF NOT EXISTS src text NOT NULL DEFAULT '{SRC_OBSERVED}'",
    "ALTER TABLE wise_sector ADD COLUMN IF NOT EXISTS src_ref text",
    *SRC_COMMENTS,
)

#: 기업 정보 화면 — 종목마다 1콜. 공급처 자신의 화면이 아니라 같은 내용을 싣는 포털용 주소다(직접 주소는 자동 요청을
#: 오류 화면으로 돌려보낸다, 261018 실측). 리다이렉트는 따라가지 않는다 — 오류 화면으로 가는 302 가 곧 실패 신호다.
COMPANY_URL = "https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx"
COMPANY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}
COMPANY_MAP_PATH = ROOT / "open_proxy_mcp" / "data" / "sector_class" / "company_industry_map.json"
#: 한 실행의 화면 호출 상한(후보 + 대응 확인). 20261016 기준 후보 192 + 확인 수십 콜.
COMPANY_MAX_CALLS = int(os.getenv("WICS_COMPANY_MAX_CALLS", "600"))
#: 대응 확인에 남겨 두는 호출 수 — 후보 조회가 상한을 다 써서 확인을 못 하면 화면 관측을 하나도 못 쓴다.
COMPANY_VERIFY_RESERVE = 120
#: 차단·오류 응답이 잇달아 이만큼이면 화면 조회를 멈춘다.
COMPANY_STOP_AFTER_BAD = 3
#: 「올바른 종목이 아닙니다」 응답이 잇달아 이만큼이면 멈춘다 — 상장 종목만 묻는데 이렇게 잇따르면 응답이 이상한 것이다.
COMPANY_STOP_AFTER_NO_PAGE = 20
#: 대응 하나를 확인할 때 시도하는 본보기 종목 수(본보기 회사가 다른 산업으로 옮겼으면 다음 본보기).
COMPANY_EXEMPLAR_TRIES = 3
WICS_LINE_RE = re.compile(r'<dt class="line-left">WICS\s*:\s*([^<]*?)\s*</dt>')
SHOWN_TICKER_RE = re.compile(r'<b class="num">([0-9A-Z]{6})</b>')
_BLOCK_MARKERS = ("접근이 제한", "비정상적인 접근", "Access Denied", "captcha")
#: 그 코드의 기업 화면이 없다는 답(상장폐지 등) — 200 에 짧은 경고 스크립트만 온다(261018 실측: 상장폐지 3종목 모두 이 응답).
#: 차단이 아니라 답이므로 연속 실패로 세지 않는다 — 세면 옛 스냅샷의 상장폐지 종목 셋이 잇달아 나올 때 조회 전체가 멈춘다.
_NO_COMPANY_MARKER = "올바른 종목이 아닙니다"

#: 스냅샷 날짜 이하 가장 늦은 시세일 — 닫힌 주(krx_weekly)와 일별 저장분(최근 약 1년 — 날짜는 달력 krx_day)을 함께 본다.
#: 월간 워크플로의 「시세 조인」 확인과 같은 규칙이다.
LISTED_DAY_SQL = """SELECT max(d) FROM (SELECT max(price_dd) d FROM krx_weekly WHERE price_dd <= %s
                   UNION ALL SELECT max(dd) FROM krx_day WHERE n > 0 AND dd <= %s) x"""
LISTED_SQL = """SELECT ticker FROM krx_daily WHERE trd_dd = %s
                UNION SELECT ticker FROM krx_weekly WHERE price_dd = %s"""


def is_common(ticker: str) -> bool:
    """보통주인가 — 단축코드 끝자리 0. 우선주는 5·7·9·K·L… (앞 5자리가 같은 회사)."""
    return ticker.endswith("0")


def common_of(ticker: str) -> str:
    """우선주 → 같은 회사 보통주 코드(앞 5자리 + 0)."""
    return ticker[:5] + "0"


def derive_rows(observed: dict[str, tuple], prev: dict[str, tuple], prev_dd: str | None,
                listed: set[str]) -> list[tuple]:
    """한 스냅샷의 파생 행 — (ticker, sector_code, sector, industry_code, industry, src, src_ref) 목록.

    observed: 이 스냅샷의 관측 행(구성종목 `wise` + 기업 정보 화면 `wise_company`) {ticker: (sector_code, sector, industry_code, industry)}.
    prev:     직전 스냅샷의 행 {ticker: ((sector_code, sector, industry_code, industry), src, src_ref)}.
    listed:   이 스냅샷 날짜 이하 가장 늦은 시세일에 상장된 종목 코드.
    지어내지 않는다 — 이 스냅샷이나 직전 스냅샷에 이미 있는 분류만 잇는다."""
    out: dict[str, tuple] = {}
    for t, (cls, src, ref) in prev.items():
        # 우선주 파생 행은 잇지 않는다 — 아래에서 이 스냅샷의 보통주로 다시 정한다.
        if t in observed or t not in listed or src == SRC_PREF:
            continue
        # 출처 날짜는 실제로 관측한 스냅샷 — 관측 행(구성종목·기업 화면)이면 직전 스냅샷, 이어 쓴 행이면 그 행의 출처 날짜.
        out[t] = (cls, SRC_CARRY, prev_dd if src in OBSERVED_SRCS else ref)
    have = dict(observed)
    have.update({t: v[0] for t, v in out.items()})
    for t in sorted(listed):
        if t in have or is_common(t) or not TICKER_RE.fullmatch(t):
            continue
        base = common_of(t)
        if base in have:
            out[t] = (have[base], SRC_PREF, base)
    return [(t, *cls, src, ref) for t, (cls, src, ref) in sorted(out.items())]


def ensure_src_columns(con) -> bool:
    """출처 칸이 없으면 더한다. 이미 있으면 표 잠금 없이 끝난다. 더했으면 True.

    칸 추가는 **자기 트랜잭션에서 곧바로 커밋**한다 — 뒤따르는 파생 행 쓰기까지 표 전체 잠금을 쥐고 있으면
    그동안 서버의 업종 조회가 모두 기다린다. 뷰(E1 교체 뒤)면 할 일이 없다 — 출처 칸은 뷰 정의에 있고 칸 설명은 교체 DDL 이 단다."""
    if _relkind(con) == "v":
        con.commit()
        return False
    have = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'wise_sector'")}
    con.commit()
    if {"src", "src_ref"} <= have:
        # 칸 설명이 옛 출처 목록(기업 화면 관측 이전)이면 고쳐 쓴다 — 설명만 바꾸므로 표를 다시 쓰지 않는다.
        note = con.execute("SELECT col_description('wise_sector'::regclass, attnum) FROM pg_attribute "
                           "WHERE attrelid = 'wise_sector'::regclass AND attname = 'src'").fetchone()
        con.commit()
        if not note or SRC_COMPANY not in (note[0] or ""):
            with con.transaction():
                con.execute("SET LOCAL lock_timeout = '5s'")
                for stmt in SRC_COMMENTS:
                    con.execute(stmt)
        return False
    with con.transaction():
        for stmt in SRC_COLUMNS_DDL:
            con.execute(stmt)
    return True


def derive_snapshot(con, snap_dd: str, table: str = TABLE) -> dict:
    """그 스냅샷의 파생 행(`carry`·`pref`)을 지우고 다시 만든다. 관측 행(`wise`·`wise_company`)은 건드리지 않는다.

    상장 여부를 판단할 시세가 없거나(그 날짜 이하 시세 없음) 너무 적으면(보존 하한 이전의 부분 행) 파생 행을 새로 만들지 않고
    **이미 있는 파생 행은 그대로 둔다** — 상장 종목을 모르는 채로 잇는 것은 상장폐지 종목을 되살리는 일이고, 있던 행을 지우는 것은
    그 행을 만들 때 알던 상장 여부를 버리고 뒤 스냅샷의 이어 쓰기 사슬까지 끊는 일이다(최신 스냅샷에서도 종목이 미분류로 떨어진다).
    주간 시세는 최근 3년만 촘촘하므로 오래된 스냅샷을 다시 만들 때(`--rederive`·옛 날짜 재수집) 이 경우가 생긴다."""
    price_dd, listed = listed_tickers(con, snap_dd)
    if len(listed) < MIN_TICKERS:
        by = dict(con.execute(f"SELECT src, count(*) FROM {table} WHERE snap_dd = %s GROUP BY 1",
                              (snap_dd,)).fetchall())
        info = {"snap_dd": snap_dd, "prev_dd": None, "price_dd": price_dd, "listed": len(listed),
                "observed": by.get(SRC_OBSERVED, 0), "company": by.get(SRC_COMPANY, 0),
                "carry": by.get(SRC_CARRY, 0), "pref": by.get(SRC_PREF, 0), "kept": True}
        print(f"DB: {snap_dd} 파생 행 그대로 둠(이어 쓰기 {info['carry']:,} · 우선주 {info['pref']:,}) — 그 날짜 이하 시세일 "
              f"{price_dd} 의 상장 종목 {len(listed):,}개(< {MIN_TICKERS:,}, 상장 여부를 모른다)", flush=True)
        return info
    con.execute(f"DELETE FROM {table} WHERE snap_dd = %s AND src = ANY(%s)", (snap_dd, list(DERIVED_SRCS)))
    cur_rows = con.execute(
        f"SELECT ticker, sector_code, sector, industry_code, industry, src FROM {table} WHERE snap_dd = %s",
        (snap_dd,)).fetchall()
    observed = {r[0]: tuple(r[1:5]) for r in cur_rows}
    n_company = sum(1 for r in cur_rows if r[5] == SRC_COMPANY)
    prev_dd = con.execute(f"SELECT max(snap_dd) FROM {table} WHERE snap_dd < %s", (snap_dd,)).fetchone()[0]
    prev = {r[0]: (tuple(r[1:5]), r[5], r[6]) for r in con.execute(
        f"SELECT ticker, sector_code, sector, industry_code, industry, src, src_ref FROM {table} "
        "WHERE snap_dd = %s", (prev_dd,))} if prev_dd else {}
    info = {"snap_dd": snap_dd, "prev_dd": prev_dd, "price_dd": price_dd, "listed": len(listed),
            "observed": len(observed) - n_company, "company": n_company, "carry": 0, "pref": 0}
    rows = derive_rows(observed, prev, prev_dd, listed)
    with con.cursor() as cur:
        cur.executemany(
            f"INSERT INTO {table} (snap_dd, ticker, sector_code, sector, industry_code, industry, src, src_ref) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", [(snap_dd, *r) for r in rows])
    info["carry"] = sum(1 for r in rows if r[5] == SRC_CARRY)
    info["pref"] = sum(1 for r in rows if r[5] == SRC_PREF)
    print(f"DB: {snap_dd} 관측 {info['observed']:,} + 기업 화면 {n_company:,} + 이어 쓰기 {info['carry']:,}(직전 {prev_dd}) "
          f"+ 우선주 {info['pref']:,} · 상장 기준 시세일 {price_dd}({len(listed):,}종목)", flush=True)
    return info


def listed_tickers(con, snap_dd: str) -> tuple[str | None, set[str]]:
    """그 스냅샷 날짜 이하 가장 늦은 시세일과 그 날 상장 종목(`derive_snapshot` 과 같은 규칙)."""
    price_dd = con.execute(LISTED_DAY_SQL, (snap_dd, snap_dd)).fetchone()[0]
    return price_dd, ({r[0] for r in con.execute(LISTED_SQL, (price_dd, price_dd))} if price_dd else set())


def company_candidates(listed: set[str], constituents: set[str]) -> list[str]:
    """기업 정보 화면으로 볼 종목 — 그 날 상장된 보통주 중 구성종목 응답에 없는 것.

    이어 쓰기 대상(직전 분류가 있는 종목)도 포함한다: 화면이 지금 분류를 직접 보여 주므로 직전 분류를 가정하는 것보다 낫고,
    화면 조회에 실패한 종목만 이어 쓰기로 남는다. 우선주는 보지 않는다(같은 회사 보통주 분류를 잇는다)."""
    return sorted(t for t in listed if t not in constituents and is_common(t) and TICKER_RE.fullmatch(t))


# ── 기업 정보 화면 (두 번째 관측 원천) ───────────────────────────────────────────────────────────

class CompanyPageError(Exception):
    """화면 응답이 그 종목의 기업 정보 화면이 아니다(차단·오류 화면·다른 종목)."""


def parse_company_page(html: str, ticker: str) -> str | None:
    """화면의 WICS 표기(산업 이름). 줄이 없거나 비었으면 None — 공급처가 그 종목에 분류를 주지 않은 것.
    다른 종목의 화면이면(코드 불일치) `CompanyPageError` — 엉뚱한 회사의 분류를 쓰지 않는다."""
    shown = SHOWN_TICKER_RE.search(html)
    if not shown:
        raise CompanyPageError("종목 코드 표기 없음")
    if shown.group(1) != ticker:
        raise CompanyPageError("다른 종목의 화면")
    m = WICS_LINE_RE.search(html)
    return (m.group(1).strip() or None) if m else None


def load_company_map(path: Path = COMPANY_MAP_PATH) -> dict[str, dict]:
    """대응표 {화면의 산업 이름: {"industryCode": "G3520", "exemplars": [...]}}. 두 하위업종에 걸친 이름은 표에 없다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    cmap = data["map"]
    for name, entry in cmap.items():
        if not re.fullmatch(r"G[0-9]{4}", entry.get("industryCode", "")) or not entry.get("exemplars"):
            raise ValueError(f"대응표 항목 형식 오류: {name}")
    return cmap


class CompanyLookup:
    """기업 정보 화면 조회기 — 호출 상한·공통 웹 속도제한·연속 실패 시 멈춤·같은 실행 안의 캐시."""

    def __init__(self, client: httpx.AsyncClient, max_calls: int = COMPANY_MAX_CALLS, today: str | None = None):
        self.client, self.max_calls = client, max_calls
        self.today = today or datetime.now(KST).strftime("%Y%m%d")
        self.calls, self.bad_streak, self.no_page_streak = 0, 0, 0
        self.stopped: str | None = None
        self.cache: dict[str, str | None] = {}
        self.errors: dict[str, str] = {}
        #: 기업 화면이 없다고 답한 종목(상장폐지 등) — 캐시에는 None(분류 없음)으로 남는다.
        self.no_page: set[str] = set()

    def can_call(self, reserve: int = 0) -> bool:
        return self.stopped is None and self.calls < self.max_calls - reserve

    async def screen(self, ticker: str, reserve: int = 0) -> str | None:
        """그 종목 화면의 산업 이름. 못 봤으면(상한·멈춤·오류) None 이고 `errors` 에 이유가 남는다."""
        if ticker in self.cache:
            return self.cache[ticker]
        if not self.can_call(reserve):
            self.errors.setdefault(ticker, "상한" if self.stopped is None else "멈춤")
            return None
        await throttle_web_request("fetch_sector_company")
        self.calls += 1
        try:
            resp = await self.client.get(COMPANY_URL, params={"cmp_cd": ticker})
            text = resp.text
            if resp.status_code != 200 or (len(text) < 4000 and any(m in text for m in _BLOCK_MARKERS)):
                raise CompanyPageError(f"HTTP {resp.status_code}")
            if len(text) < 4000 and _NO_COMPANY_MARKER in text:
                self.bad_streak = 0
                self.no_page_streak += 1
                self.no_page.add(ticker)
                self.cache[ticker] = None
                self.errors[ticker] = "기업 화면 없음(올바른 종목이 아니라는 답)"
                if self.no_page_streak >= COMPANY_STOP_AFTER_NO_PAGE:
                    self.stopped = f"「기업 화면 없음」 응답 {self.no_page_streak}번 연속"
                return None
            name = parse_company_page(text, ticker)
        except (httpx.HTTPError, CompanyPageError) as exc:
            # 상류 원문은 옮기지 않는다(HTML·접속 정보가 섞일 수 있다).
            self.errors[ticker] = str(exc) if isinstance(exc, CompanyPageError) else type(exc).__name__
            self.bad_streak += 1
            if self.bad_streak >= COMPANY_STOP_AFTER_BAD:
                self.stopped = f"차단·오류 응답 {self.bad_streak}번 연속(마지막: {self.errors[ticker]})"
            return None
        self.bad_streak = self.no_page_streak = 0
        self.cache[ticker] = name
        return name


async def company_rows(lookup: CompanyLookup, candidates: list[str], constituents: dict[str, tuple],
                       cmap: dict[str, dict]) -> tuple[list[tuple], dict]:
    """한 스냅샷의 기업 화면 관측 행 — [(ticker, sector_code, sector, industry_code, industry, src, src_ref)], 통계.

    constituents: 그 스냅샷의 구성종목 관측 {ticker: (sector_code, sector, industry_code, industry)} — 하위업종 이름과
                  대응 확인(본보기)의 기준이다. 쓰는 대응마다 본보기 종목의 화면을 다시 보고, 본보기의 화면 값이 그 이름인데 저장
                  하위업종이 대응표와 다르면 그 대응을 쓰지 않는다(분류 체계가 바뀐 것)."""
    reserve = min(COMPANY_VERIFY_RESERVE, max(0, lookup.max_calls // 3))
    seen: dict[str, str] = {}
    for t in candidates:
        name = await lookup.screen(t, reserve=reserve)
        if name is not None:
            seen[t] = name
    classes = {cls[2]: cls for cls in constituents.values()}
    verified, refused = set(), {}
    for name in sorted({n for n in seen.values() if n in cmap}):
        entry = cmap[name]
        tried = 0
        for ex in entry["exemplars"]:
            if ex not in constituents:
                continue
            if tried >= COMPANY_EXEMPLAR_TRIES:
                break
            tried += 1
            ex_name = await lookup.screen(ex)
            if ex_name is None:
                continue
            if ex_name != name:
                continue  # 본보기 회사가 다른 산업으로 옮겼다 — 다음 본보기
            if constituents[ex][2] == entry["industryCode"]:
                verified.add(name)
            else:
                refused[name] = f"본보기 {ex} 의 저장 하위업종 {constituents[ex][2]} ≠ 대응표 {entry['industryCode']}"
            break
        if name not in verified and name not in refused:
            refused[name] = "본보기로 확인하지 못함"
    rows, unmapped = [], {}
    for t, name in sorted(seen.items()):
        if name not in cmap:
            unmapped[t] = name
            continue
        if name not in verified:
            continue
        cls = classes.get(cmap[name]["industryCode"])
        if cls is None:
            refused.setdefault(name, f"하위업종 {cmap[name]['industryCode']} 가 이 스냅샷 구성종목에 없음")
            continue
        rows.append((t, *cls, SRC_COMPANY, f"{lookup.today}:{name}"))
    no_page = [t for t in candidates if t in lookup.no_page]  # 기업 화면이 없다는 답(상장폐지 등)
    # 화면에 분류 줄이 없거나 빈 종목
    no_class = [t for t in candidates if t not in seen and t in lookup.cache and t not in lookup.no_page]
    failed = [t for t in candidates if t not in lookup.cache]
    stats = {"candidates": len(candidates), "rows": len(rows), "unmapped": unmapped, "refused": refused,
             "no_class": no_class, "no_page": no_page, "failed": failed, "calls": lookup.calls,
             "stopped": lookup.stopped}
    return rows, stats


def _company_report(snap_dd: str, stats: dict) -> None:
    print(f"기업 화면: {snap_dd} 후보 {stats['candidates']:,} → 관측 행 {stats['rows']:,} · 대응표에 없는 이름 "
          f"{len(stats['unmapped']):,} · 화면에 분류 없음 {len(stats['no_class']):,} · 기업 화면 없음(상장폐지 등) "
          f"{len(stats.get('no_page', [])):,} · 조회 실패 {len(stats['failed']):,} "
          f"(누적 호출 {stats['calls']:,})", flush=True)
    if stats["unmapped"]:
        print(f"  대응표에 없는 이름(지어내지 않음): {sorted(set(stats['unmapped'].values()))}", flush=True)
    for name, why in sorted(stats["refused"].items()):
        print(f"::warning::기업 화면 대응 「{name}」 이번에 쓰지 않음 — {why}", flush=True)
    if stats["stopped"]:
        print(f"::warning::기업 화면 조회 멈춤 — {stats['stopped']}. 못 본 종목은 이어 쓰기나 미분류로 남는다", flush=True)
    elif stats["failed"]:
        print(f"::warning::기업 화면 조회 실패 {len(stats['failed'])}종목(상한 {COMPANY_MAX_CALLS} 등) — 이어 쓰기나 미분류로 남는다",
              flush=True)


def _company_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30, headers=COMPANY_HEADERS, follow_redirects=False)


async def lookup_company_for_snapshot(snapshot: dict) -> list[tuple] | None:
    """월간 수집: 새 스냅샷의 기업 화면 관측 행. 실패해도 예외를 올리지 않는다(구성종목 관측 적재를 막지 않는다)."""
    dsn = _dsn()
    if not dsn:
        return None
    try:
        import psycopg

        snap_dd = snapshot["asOf"].replace("-", "")
        with psycopg.connect(dsn, connect_timeout=20) as con:
            price_dd, listed = listed_tickers(con, snap_dd)
        if len(listed) < MIN_TICKERS:
            print(f"기업 화면: {snap_dd} 이하 시세일 {price_dd} 의 상장 종목 {len(listed):,}개 — 상장 여부를 몰라 조회하지 않는다",
                  flush=True)
            return None
        constituents = {t: (v["sectorCode"], v["sector"], v["industryCode"], v["industry"])
                        for t, v in snapshot["data"].items()}
        cands = company_candidates(listed, set(constituents))
        async with _company_client() as client:
            lookup = CompanyLookup(client, max_calls=COMPANY_MAX_CALLS)
            rows, stats = await company_rows(lookup, cands, constituents, load_company_map())
        _company_report(snap_dd, stats)
        return rows
    except Exception as exc:  # 두 번째 원천의 실패가 첫 번째 원천 적재를 막으면 안 된다
        print(f"::warning::기업 화면 조회 단계 실패({type(exc).__name__}) — 구성종목 관측·파생만 적재한다", flush=True)
        return None


def write_company_rows(con, snap_dd: str, rows: list[tuple], table: str = TABLE) -> None:
    """기업 화면 관측 행 업서트 — 구성종목 관측 행(`wise`)은 절대 덮지 않는다(그 날 구성종목 응답이 이긴다)."""
    if not rows:
        return
    with con.cursor() as cur:
        cur.executemany(
            f"INSERT INTO {table} (snap_dd, ticker, sector_code, sector, industry_code, industry, src, src_ref) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (snap_dd, ticker) DO UPDATE SET "
            "sector_code=EXCLUDED.sector_code, sector=EXCLUDED.sector, industry_code=EXCLUDED.industry_code, "
            "industry=EXCLUDED.industry, src=EXCLUDED.src, src_ref=EXCLUDED.src_ref "
            f"WHERE {table}.src <> '{SRC_OBSERVED}'",
            [(snap_dd, *r) for r in rows])


def rederive_from(con, snap_dd: str | None = None, table: str = TABLE) -> list[dict]:
    """snap_dd(없으면 가장 이른 스냅샷)부터 마지막 스냅샷까지 차례로 파생 행을 다시 만든다."""
    snaps = [r[0] for r in con.execute(
        f"SELECT DISTINCT snap_dd FROM {table} WHERE snap_dd >= %s ORDER BY 1", (snap_dd or "",))]
    return [derive_snapshot(con, s, table) for s in snaps]


@contextmanager
def sector_store(con):
    """쓰는 트랜잭션에서 쓸 표 이름을 준다(`with sector_store(con) as T:`) — 모듈 머리말 「저장 모양 두 가지」.

    표 모드(또는 아직 없음): `wise_sector` 그대로 — 종전과 같다.
    뷰 모드(E1 교체 뒤): 구간 두 표를 SHARE ROW EXCLUSIVE 로 잡고(읽기는 그대로, 다른 쓰기만 기다림) 뷰의 지금 행을 임시 작업 표로 편다.
      블록이 예외 없이 끝나면 바뀐 구간만 고치고(`FLUSH_APPLY`) 「뷰 = 작업 표」·구간 무결성을 확인한다(`FLUSH_CHECK`) — 어긋나면 예외.
      커밋·되돌리기는 호출자 몫이다(dry-run 은 고친 뒤 되돌린다 — 같은 확인을 거친다). 작업 표는 트랜잭션이 끝나면 사라진다."""
    if _relkind(con) != "v":
        yield TABLE
        return
    con.execute(FLUSH_OPEN)
    yield WORK_TABLE
    con.execute(FLUSH_APPLY)
    con.execute(FLUSH_CHECK)
    bad = [(k, v) for k, v in con.execute(FLUSH_CHECK_READ).fetchall() if v]
    if bad:
        raise RuntimeError("업종 구간 쓰기 확인 실패 — 되돌린다: " + ", ".join(f"{k}={v}" for k, v in bad))
    iv, snaps = con.execute("SELECT (SELECT count(*) FROM wise_sector_iv), (SELECT count(*) FROM wise_sector_snap)").fetchone()
    print(f"DB: 업종 구간 표 wise_sector_iv {iv:,}행 · 스냅샷 {snaps}개(뷰 = 작업 표 확인)", flush=True)


def ensure_tables(con) -> str | None:
    """원문 스냅샷 표 + (표 모드·새 DB 면) 옛 모양 표를 만든다. 곧바로 커밋. 돌려주는 값 = `wise_sector` 의 종류('r'·'v')."""
    con.execute(DDL_SNAPSHOTS)
    if _relkind(con) != "v":
        con.execute(DDL_TABLE)
    kind = _relkind(con)
    con.commit()
    return kind


UPSERT = """
INSERT INTO kr_wics_snapshots
    (as_of, requested_date, data, sector_count, industry_count, ticker_count, refreshed_at)
VALUES (%s, %s, %s, %s, %s, %s, now())
ON CONFLICT (as_of) DO UPDATE SET
    requested_date = EXCLUDED.requested_date,
    data           = EXCLUDED.data,
    sector_count   = EXCLUDED.sector_count,
    industry_count = EXCLUDED.industry_count,
    ticker_count   = EXCLUDED.ticker_count,
    refreshed_at   = now()
"""


def previous_friday_kst(now: datetime | None = None) -> str:
    """서울 기준 직전 금요일 YYYYMMDD. 금요일에 돌리면 *지난주* 금요일을 준다."""
    today = (now or datetime.now(KST)).astimezone(KST).date()
    js_day = (today.weekday() + 1) % 7  # 파이썬 월=0 → JS 일=0 체계로 변환
    delta = (js_day + 2) % 7 or 7
    return (today - timedelta(days=delta)).strftime("%Y%m%d")


def parse_requested_date(value: str) -> date:
    """조회일은 실제 달력의 YYYYMMDD여야 한다(형식만 맞는 2월 30일도 거부)."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}", value):
        raise ValueError("--date 는 실제 날짜 YYYYMMDD여야 한다")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError("--date 는 실제 날짜 YYYYMMDD여야 한다") from exc


def parse_trade_date(value: str) -> str:
    """공급처 TRD_DT → 서울 기준 YYYY-MM-DD. 최소 날짜 sentinel은 유효일이 아니다."""
    if not isinstance(value, str) or value == NO_DATA_TRADE_DATE:
        raise ValueError("유효하지 않은 TRD_DT")
    try:
        dotnet = DOTNET_DATE_RE.fullmatch(value)
        if dotnet:
            _validate_utc_offset(dotnet.group("offset"))
            parsed = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
                milliseconds=int(dotnet.group(1))
            )
        else:
            iso = ISO_DATE_RE.fullmatch(value)
            if not iso:
                raise ValueError("날짜 형식 오류")
            _validate_utc_offset(iso.group("offset"))
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST).date().isoformat()
    except (ValueError, OverflowError) as exc:
        # 상류 원문을 예외에 복사하지 않는다(HTML/접속 정보가 섞일 수 있다).
        raise ValueError("유효하지 않은 TRD_DT") from exc


def _validate_utc_offset(offset: str | None) -> None:
    if not offset or offset == "Z":
        return
    digits = offset[1:].replace(":", "")
    if int(digits[:2]) >= 24 or int(digits[2:]) >= 60:
        raise ValueError("날짜 UTC 오프셋 범위 오류")


def _component_rows(payload: object, candidate: date, industry_code: str) -> list | None:
    """None은 검증된 무자료만 뜻한다. 다른 비정상 응답은 소급하지 않고 중단한다."""
    if not isinstance(payload, dict) or not isinstance(payload.get("info"), dict):
        raise RuntimeError(f"{industry_code}: 구성종목 응답 구조 오류(info)")
    rows = payload.get("list")
    if not isinstance(rows, list):
        raise RuntimeError(f"{industry_code}: 구성종목 응답 구조 오류(list)")
    trd_dt = payload["info"].get("TRD_DT")
    if trd_dt == NO_DATA_TRADE_DATE and not rows:
        return None
    actual_date = parse_trade_date(trd_dt)
    if actual_date != candidate.isoformat():
        raise RuntimeError(
            f"{industry_code}: 응답 기준일 {actual_date} != 조회일 {candidate.isoformat()}"
        )
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise RuntimeError(f"{industry_code}: 구성종목이 비었거나 행 구조가 잘못됐다")
    return rows


def find_node(nodes: list | None, title: str) -> dict | None:
    """트리에서 title이 일치하는 첫 노드를 깊이우선으로 찾는다."""
    if nodes is not None and not isinstance(nodes, list):
        raise RuntimeError("분류 트리 응답 구조 오류(children)")
    for node in nodes or []:
        if not isinstance(node, dict):
            raise RuntimeError("분류 트리 응답 구조 오류(node)")
        if node.get("title") == title:
            return node
        nested = find_node(node.get("children"), title)
        if nested:
            return nested
    return None


def _industry_groups(sectors: list) -> list[dict]:
    """중복·누락된 트리가 완전성 검사 개수를 부풀리지 않게 검증한다."""
    if not isinstance(sectors, list):
        raise RuntimeError("분류 트리 응답 구조 오류(sectors)")
    groups = []
    sector_codes, industry_codes = set(), set()
    for sector in sectors:
        _validate_node(sector, sector_codes)
        children = sector.get("children")
        if children is not None and not isinstance(children, list):
            raise RuntimeError("분류 트리 응답 구조 오류(children)")
        for industry in children or [sector]:
            _validate_node(industry, industry_codes)
            groups.append({
                "sectorCode": sector["key"], "sector": sector["title"],
                "industryCode": industry["key"], "industry": industry["title"],
            })
    return groups


def _validate_node(node: object, seen: set) -> None:
    if not isinstance(node, dict) or any(
        not isinstance(node.get(field), str) or not node[field].strip()
        for field in ("key", "title")
    ):
        raise RuntimeError("분류 트리 응답 구조 오류(key/title)")
    if not re.fullmatch(r"G[0-9]+", node["key"]):
        raise RuntimeError("분류 트리 응답 구조 오류(key 형식)")
    if node["key"] in seen:
        raise RuntimeError("분류 트리의 중복 코드")
    seen.add(node["key"])


async def _get_json(client: httpx.AsyncClient, path: str):
    await throttle_web_request("fetch_sector_class")
    try:
        response = await client.get(f"{BASE_URL}{path}")
    except httpx.HTTPError as exc:
        raise RuntimeError(f"상류 전송 오류({type(exc).__name__}) — 날짜 소급 없이 중단") from exc
    if response.status_code != 200:
        raise RuntimeError(f"상류 HTTP {response.status_code} — 날짜 소급 없이 중단")
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("상류 JSON 응답 오류 — 날짜 소급 없이 중단") from exc


async def fetch_snapshot(requested_date: str, *, lookback_days: int = 0) -> dict:
    """요청일 이하의 완전한 한 날짜만 선택. 정확일 기본값, 명시적 무자료만 제한 소급."""
    anchor = parse_requested_date(requested_date)
    if type(lookback_days) is not int or not 0 <= lookback_days <= MAX_LOOKBACK_DAYS:
        raise ValueError(f"--lookback-days 는 0~{MAX_LOOKBACK_DAYS} 정수여야 한다")
    # 날짜 하한도 HTTP 전에 검증한다.
    oldest = anchor - timedelta(days=lookback_days)
    headers = {
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/DataCenter/Index/G10",
        "User-Agent": "Mirae-Research-Terminal/1.0",
    }
    async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=False) as client:
        tree = await _get_json(client, "/API/Tree/Get?id=4")
        if not isinstance(tree, list):
            raise RuntimeError("분류 트리 응답 구조 오류")
        root = find_node(tree, "WICS")
        if not root or not root.get("children"):
            raise RuntimeError("분류 트리 최상위 노드를 찾지 못했다 — 사이트 응답 구조 변경 가능성")
        sectors = root["children"]
        industries = _industry_groups(sectors)
        if len(sectors) < MIN_SECTORS or len(industries) < MIN_INDUSTRIES:
            raise RuntimeError("완전성 검사 실패 — 분류 트리의 대분류/하위업종 부족")

        for offset in range(lookback_days + 1):
            candidate = anchor - timedelta(days=offset)
            if candidate.weekday() >= 5:
                print(f"기준일 제외: {candidate.isoformat()} — 주말", flush=True)
                continue
            candidate_dd = candidate.strftime("%Y%m%d")
            entries: dict[str, dict] = {}
            for group_index, group in enumerate(industries):
                params = f"ceil_yn=0&dt={candidate_dd}&sec_cd={group['industryCode']}"
                payload = await _get_json(client, f"/Index/GetIndexComponets?{params}")
                rows = _component_rows(payload, candidate, group["industryCode"])
                if rows is None:
                    if group_index:
                        raise RuntimeError(
                            f"{group['industryCode']}: 일부 하위업종만 무자료 — 불완전 응답으로 중단"
                        )
                    # 첫 업종을 가용일 탐침으로 재사용한다. 실패한 날짜에서 전 업종을 재수집하지 않는다.
                    print(
                        f"기준일 제외: {candidate.isoformat()} — 첫 하위업종의 명시적 무자료 "
                        "(빈 list + 최소 날짜)", flush=True,
                    )
                    break
                valid_rows = 0
                for row in rows:
                    code = str(row.get("CMP_CD") or "").strip()
                    if not TICKER_RE.fullmatch(code):
                        raise RuntimeError(f"{group['industryCode']}: 유효하지 않은 종목코드")
                    valid_rows += 1
                    existing = entries.get(code)
                    if existing and existing["industryCode"] != group["industryCode"]:
                        raise RuntimeError(
                            f"{code} 가 {existing['industryCode']} 와 {group['industryCode']} 양쪽에 있다 "
                            "— 분류 충돌이므로 중단한다"
                        )
                    entries[code] = dict(group)
                if not valid_rows:
                    raise RuntimeError(f"{group['industryCode']}: 유효한 종목이 없는 불완전 응답")
            else:
                if len(entries) < MIN_TICKERS:
                    raise RuntimeError(
                        f"완전성 검사 실패 — 종목 {len(entries)}/{MIN_TICKERS}"
                    )
                reason = "요청일 일치" if offset == 0 else "주말/명시적 무자료만 건너뛴 최근 유효일"
                print(
                    f"기준일 선택: 요청 {requested_date} → 실제 {candidate.isoformat()} "
                    f"/ 소급 {offset}일 / 사유: {reason}", flush=True,
                )
                return {
                    "requestedDate": requested_date,
                    "asOf": candidate.isoformat(),
                    "sectorCount": len(sectors),
                    "industryCount": len(industries),
                    "tickerCount": len(entries),
                    "data": dict(sorted(entries.items())),
                }

    raise RuntimeError(
        f"유효 거래일 없음 — 요청 {requested_date}, 조회 범위 {oldest.isoformat()}~{anchor.isoformat()}, "
        f"최대 소급 {lookback_days}일. 파일·DB를 쓰지 않는다"
    )


def write_file(snapshot: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "requestedDate": snapshot["requestedDate"],
            "asOf": snapshot["asOf"],
            "sectorCount": snapshot["sectorCount"],
            "industryCount": snapshot["industryCount"],
            "tickerCount": snapshot["tickerCount"],
            "refreshedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "external",
        },
        "data": snapshot["data"],
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(f"파일: {out_path.relative_to(ROOT)} ({snapshot['tickerCount']:,}종목)", flush=True)


def _dsn() -> str | None:
    return os.getenv("WICS_DATABASE_URL") or os.getenv("DATABASE_URL")


def persist(snapshot: dict, company: list[tuple] | None = None) -> bool:
    """kr_wics_snapshots 업서트 + wise_sector 관측 행(구성종목·기업 화면) + 파생 행. DSN이 없으면 False를 돌려주고 조용히 건너뛴다.

    관측·파생을 한 트랜잭션으로 쓴다 — 관측만 들어가고 파생이 빠진 스냅샷이 잠깐이라도 보이면, 그 사이에 도는 집계가
    구성종목에서만 빠진 종목을 미분류로 센다(261018 의 32종목이 그 모양이었다). `company` 는 `lookup_company_for_snapshot` 이
    트랜잭션 **밖에서** 미리 조회한 기업 화면 관측 행이다(네트워크를 기다리는 동안 트랜잭션을 열어 두지 않는다)."""
    dsn = _dsn()
    if not dsn:
        print("DB: WICS_DATABASE_URL/DATABASE_URL 없음 — 적재 건너뜀", flush=True)
        return False

    import psycopg
    from psycopg.types.json import Jsonb

    with psycopg.connect(dsn, connect_timeout=20) as con:
        ensure_tables(con)
        if ensure_src_columns(con):
            print("DB: wise_sector 출처 칸(src·src_ref) 추가", flush=True)
        con.execute(
            UPSERT,
            (
                snapshot["asOf"],
                snapshot["requestedDate"],
                Jsonb(snapshot["data"]),
                snapshot["sectorCount"],
                snapshot["industryCount"],
                snapshot["tickerCount"],
            ),
        )
        # 행 단위 전개 — OPM 이 실제로 쓰는 모양(krx_weekly 조인용)
        snap_dd = snapshot["asOf"].replace("-", "")
        # 같은 날짜를 다시 수집하면 이번 응답에 다시 나온 종목은 업서트가 관측으로 되돌린다(src·src_ref 를 덮는다).
        # 나머지 파생 행은 아래 다시 만들기가 지우고 새로 만든다 — 여기서 먼저 지우지 않는다: 그 날의 상장 여부를 모르면
        # (보존 하한 이전 날짜) 다시 만들지 못하므로 있던 파생 행을 그대로 두어야 한다(`derive_snapshot`).
        with sector_store(con) as T:
            with con.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO {T} "
                    "(snap_dd, ticker, sector_code, sector, industry_code, industry, src, src_ref) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,NULL) "
                    "ON CONFLICT (snap_dd, ticker) DO UPDATE SET "
                    "sector_code=EXCLUDED.sector_code, sector=EXCLUDED.sector, "
                    "industry_code=EXCLUDED.industry_code, industry=EXCLUDED.industry, "
                    "src=EXCLUDED.src, src_ref=NULL",
                    [(snap_dd, t, v["sectorCode"], v["sector"], v["industryCode"], v["industry"], SRC_OBSERVED)
                     for t, v in snapshot["data"].items()])
            # 기업 화면 관측 — 파생 행보다 먼저 넣어야 다시 만들기가 그 종목을 이어 쓰지 않고 우선주가 그 분류를 잇는다.
            write_company_rows(con, snap_dd, company or [], T)
            # 이 날짜와 그 뒤 스냅샷의 파생 행 — 이어 쓰기가 바로 앞 스냅샷에 기대므로 차례로.
            infos = rederive_from(con, snap_dd, T)
        con.commit()
        if len(infos) > 1:  # 최신이 아닌 날짜를 다시 수집했다 — 섹터 시총 집계는 새 관측만 따라온다
            print(_agg_reminder(snap_dd), flush=True)
        total = con.execute("SELECT count(*) FROM kr_wics_snapshots").fetchone()[0]
        rows, snaps = con.execute(
            "SELECT count(*), count(DISTINCT snap_dd) FROM wise_sector").fetchone()
    print(f"DB: kr_wics_snapshots 업서트 (as_of={snapshot['asOf']}, 누적 {total}개 스냅샷)", flush=True)
    print(f"DB: wise_sector {rows:,}행 / {snaps}개 시점", flush=True)
    return True


def rederive_db(dry_run: bool = False) -> int:
    """수집 없이 모든 스냅샷의 파생 행을 다시 만든다(--rederive). dry_run 이면 계산만 하고 되돌린다."""
    dsn = _dsn()
    if not dsn:
        print("DB: WICS_DATABASE_URL/DATABASE_URL 없음 — --rederive 는 DB 가 필요하다", file=sys.stderr, flush=True)
        return 1
    import psycopg

    with psycopg.connect(dsn, connect_timeout=20) as con:
        if not dry_run and ensure_src_columns(con):
            print("DB: wise_sector 출처 칸(src·src_ref) 추가", flush=True)
        if dry_run and not {"src", "src_ref"} <= {r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'wise_sector'")}:
            print("dry-run: 출처 칸이 아직 없다 — 칸을 더하는 실제 실행이 먼저다(이 실행은 아무것도 쓰지 않았다)", flush=True)
            return 0
        with sector_store(con) as T:
            infos = rederive_from(con, table=T)
        if dry_run:
            con.rollback()
            print("dry-run — 되돌렸다(쓰기 없음)", flush=True)
        else:
            con.commit()
    kept = sum(1 for i in infos if i.get("kept"))
    print(f"파생 행 {'계산(되돌림)' if dry_run else '다시 만듦'}: 스냅샷 {len(infos)}개"
          f"{f'(그중 {kept}개는 상장 여부를 몰라 그대로 둠)' if kept else ''} · "
          f"이어 쓰기 {sum(i['carry'] for i in infos):,} · 우선주 {sum(i['pref'] for i in infos):,}", flush=True)
    if infos and not dry_run:
        print(_agg_reminder(infos[0]["snap_dd"]), flush=True)
    return 0


async def company_only_db(snap: str | None = None, dry_run: bool = False) -> int:
    """수집 없이 저장된 스냅샷에 기업 화면 관측 행만 더한다(--company-only). snap: YYYYMMDD · 'all' · None(최신).

    스냅샷마다 그 날 상장 보통주 중 구성종목 관측에 없는 종목이 후보다. 한 종목은 한 실행에서 한 번만 조회한다(오늘 화면을 여러
    스냅샷에 쓴다 — 가장 이른 스냅샷이 과거 구간에 소급되는 것과 같은 소급이며, `src_ref` 에 조회일이 남는다).
    조회는 트랜잭션 밖에서 끝내고, 쓰기·파생 행 다시 만들기는 한 트랜잭션이다."""
    dsn = _dsn()
    if not dsn:
        print("DB: WICS_DATABASE_URL/DATABASE_URL 없음 — --company-only 는 DB 가 필요하다", file=sys.stderr, flush=True)
        return 1
    import psycopg

    cmap = load_company_map()
    with psycopg.connect(dsn, connect_timeout=20) as con:
        all_snaps = [r[0] for r in con.execute("SELECT DISTINCT snap_dd FROM wise_sector ORDER BY 1")]
        if not all_snaps:
            print("DB: wise_sector 가 비었다 — 먼저 수집", file=sys.stderr, flush=True)
            return 1
        targets = all_snaps if snap == "all" else [snap or all_snaps[-1]]
        if any(t not in all_snaps for t in targets):
            print(f"DB: 스냅샷 {snap} 없음 (있는 것: {all_snaps})", file=sys.stderr, flush=True)
            return 1
        plan = []
        for s in targets:
            price_dd, listed = listed_tickers(con, s)
            if len(listed) < MIN_TICKERS:
                print(f"기업 화면: {s} 이하 시세일 {price_dd} 의 상장 종목 {len(listed):,}개 — 상장 여부를 몰라 건너뜀", flush=True)
                continue
            cons = {r[0]: tuple(r[1:]) for r in con.execute(
                "SELECT ticker, sector_code, sector, industry_code, industry FROM wise_sector WHERE snap_dd = %s AND src = %s",
                (s, SRC_OBSERVED))}
            plan.append((s, cons, company_candidates(listed, set(cons))))
        con.commit()
    results = []
    async with _company_client() as client:
        lookup = CompanyLookup(client, max_calls=COMPANY_MAX_CALLS)
        for s, cons, cands in plan:
            rows, stats = await company_rows(lookup, cands, cons, cmap)
            _company_report(s, stats)
            results.append((s, rows))
    if not results:
        return 0
    with psycopg.connect(dsn, connect_timeout=20) as con:
        if not dry_run and ensure_src_columns(con):
            print("DB: wise_sector 출처 칸(src·src_ref) 추가", flush=True)
        if dry_run and not {"src", "src_ref"} <= {r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'wise_sector'")}:
            print("dry-run: 출처 칸이 아직 없다 — 칸을 더하는 실제 실행이 먼저다(이 실행은 아무것도 쓰지 않았다)", flush=True)
            return 0
        with sector_store(con) as T:
            for s, rows in results:
                write_company_rows(con, s, rows, T)
            infos = rederive_from(con, results[0][0], T)
        if dry_run:
            con.rollback()
            print("dry-run — 되돌렸다(쓰기 없음)", flush=True)
        else:
            con.commit()
    print(f"기업 화면 관측 {'계산(되돌림)' if dry_run else '적재'}: 스냅샷 {len(results)}개 · 행 "
          f"{sum(len(r) for _, r in results):,} · 이어 쓰기 {sum(i['carry'] for i in infos):,} · "
          f"우선주 {sum(i['pref'] for i in infos):,}", flush=True)
    if not dry_run and any(rows for _, rows in results):
        # 최신 스냅샷만 바꿔도 따라오지 않는다 — 집계는 평소 최신 주만, 새 관측은 「새 스냅샷 날짜」로만 알아챈다.
        print(_agg_reminder(results[0][0]), flush=True)
    return 0


def _agg_reminder(first_snap: str) -> str:
    """옛 스냅샷의 파생 행이 바뀐 뒤의 안내 — 섹터 시총 집계는 스스로 따라오지 않는다(`scripts/krx_cap_agg.py` 머리말)."""
    return (f"주의: {first_snap} 이후 스냅샷의 파생 행이 바뀌었다. 섹터 시총 집계(krx_cap_agg)는 평소 최신 주만, 새 관측은 그 관측일 "
            f"이후만 다시 집계하므로 이 변경을 따라오지 않는다 — scripts/krx_cap_agg.py --since <{first_snap}, 가장 이른 스냅샷이면 "
            "보존 하한> 으로 다시 집계할 것(가장 이른 스냅샷은 그 이전 구간에 소급 적용된다). 보존 하한 이전 주간 점은 원천이 없어 "
            "다시 집계되지 않는다.")


async def main(args: argparse.Namespace) -> int:
    if getattr(args, "rederive", False):
        return rederive_db(dry_run=args.dry_run)
    if getattr(args, "company_only", False):
        return await company_only_db(getattr(args, "snap", None), dry_run=args.dry_run)
    explicit_date = args.date or os.getenv("WICS_DATE")
    requested_date = explicit_date or previous_friday_kst()
    lookback_days = args.lookback_days
    if lookback_days is None:
        lookback_days = 0 if explicit_date else MAX_LOOKBACK_DAYS
    print(f"수집 요청일: {requested_date} / 최대 소급 {lookback_days}일", flush=True)

    try:
        snapshot = await fetch_snapshot(requested_date, lookback_days=lookback_days)
    except Exception as exc:
        print(f"실패: {exc}", file=sys.stderr, flush=True)
        return 1

    print(
        f"수집 완료: 대분류 {snapshot['sectorCount']} / 하위업종 {snapshot['industryCount']} / "
        f"종목 {snapshot['tickerCount']:,} / 실제 기준일 {snapshot['asOf']}",
        flush=True,
    )

    if args.dry_run:
        print("dry-run — 파일·DB 모두 쓰지 않았다", flush=True)
        return 0
    if not args.no_file:
        write_file(snapshot, Path(args.out) if args.out else OUT_PATH)
    if not args.no_db:
        company = None if getattr(args, "no_company", False) else await lookup_company_for_snapshot(snapshot)
        persist(snapshot, company)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="업종분류 수집")
    ap.add_argument("--date", help="정확한 기준일 YYYYMMDD (미지정: 직전 금요일 이하 유효일)")
    ap.add_argument("--lookback-days", type=int,
                    help="허용 소급 일수 0~14 (기본: 자동 날짜 14, --date/WICS_DATE 지정 시 0)")
    ap.add_argument("--out", help=f"정적 맵 출력 경로 (기본: {OUT_PATH.relative_to(ROOT)})")
    ap.add_argument("--no-file", action="store_true", help="정적 맵 파일을 쓰지 않는다")
    ap.add_argument("--no-db", action="store_true", help="Postgres 적재를 건너뛴다")
    ap.add_argument("--dry-run", action="store_true", help="수집·검증만 하고 아무것도 쓰지 않는다")
    ap.add_argument("--rederive", action="store_true",
                    help="수집 없이 모든 스냅샷의 파생 행(구성종목에서만 빠진 상장 종목·우선주)을 다시 만든다")
    ap.add_argument("--no-company", action="store_true",
                    help="기업 정보 화면 조회를 하지 않는다(구성종목 관측 + 파생 행만)")
    ap.add_argument("--company-only", action="store_true",
                    help="수집 없이 저장된 스냅샷에 기업 정보 화면 관측 행만 더한다(구성종목에 없는 상장 보통주)")
    ap.add_argument("--snap", help="--company-only 대상 스냅샷 YYYYMMDD 또는 all (기본: 최신)")
    sys.exit(asyncio.run(main(ap.parse_args())))
