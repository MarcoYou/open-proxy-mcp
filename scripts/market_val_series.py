"""시장 밸류에이션 분기 시계열 — PIT(공시 접수 근사) 기준 시장 PER/PBR 추이.

시총: krx_weekly(주간, 2015-12~) 재활용 → KRX 콜 0.
재무: FY2018~2024 지배순이익·지배자본 배치(dart_finstat_y). 260705 FY2018-2019 백필 추가
      — firm_history 밴드 깊이를 2020년까지 확장(2020 PBR/PER PIT 커버). resume으로 누락분만 수집.
PIT 근사: 분기말 D 시점 최신 확정재무 = (D가 4월 이후면 전년 FY, 아니면 전전년 FY)
          — 사업보고서 3월 중순 공시 규칙의 연 단위 근사(look-ahead 방지).

자동 정정(260704, 소프트센 032680 FY2022 사례로 확정): 회사가 당해년도 XBRL에 단위 스케일을
잘못 넣는 경우(사람이 읽는 표는 "단위: 백만원"이 맞는데 XBRL 원시값엔 배율을 안 곱함 → 100만배
부풀림)가 실제로 있다. 원본 하나만 보고는 못 잡지만(두 DART 엔드포인트가 같은 버그를 공유),
**다음 해 보고서의 전기(frmtrm_amount) 비교치는 회사가 재작성하면서 정상화**돼 있다(실측 확인:
소프트센 FY2022 매출 73,373,050,121,000,000 → FY2023 보고서 전기란엔 73,373,050,121로 정정).
그래서 **다음 해 보고서의 전기·전전기 비교치**를 `ni_restated`/`eq_restated`에 저장하고, 읽는 쪽은 **restated가 있으면 그걸 우선**
사용한다. 원 보고서가 API 에 없는 해(금융업의 XBRL 이전 사업보고서 등)도 이 길로 채운다.
261020(p1f): 같은 사업연도를 적은 보고서가 여럿이면 **가장 늦게 접수된 보고서의 값**이 정정 칸을 차지하고 그 접수번호를 `restated_rcept_no`
(근거 칸)에 남긴다(`restated_wins`). 사업보고서를 읽는 모든 길 — 정규 `--fetch`·정정 사업보고서 다시 받기 `--amended`·`--refetch`·
`--backfill-restated` — 이 같은 응답의 전기·전전기 칸을 이 규칙으로 쓴다(DART 콜은 늘지 않는다). 그 해 보고서가 정정 칸 출처보다 늦게
정정되면 정정 칸을 비워 그 해 칸이 쓰인다. 종전엔 `--backfill-restated` 만 정정 칸을 썼고 출처를 남기지 않았다.

실행: python3 scripts/market_val_series.py --fetch   # 과거 FY 배치(재개 가능, ~100분)
      python3 scripts/market_val_series.py --series  # 분기 시계열 산출
      python3 scripts/market_val_series.py --backfill-restated [--tickers 001720 --report-year 2024]
          # 그 해 사업보고서의 전기·전전기 칸 → (해-1)·(해-2) 재작성치. --tickers 를 주면 「이미 처리됨」 판정 없이 그 종목만(261019)
      python3 scripts/market_val_series.py --refetch 065680:2018,065680:2019
          # 261019: 이 (종목, 사업연도) 행만 다시 받는다(이미 ok 여도) — 수집 규칙을 고친 뒤 그 규칙으로 틀렸던 행만 바로잡을 때.
          #   받은 값으로 순이익·자본 칸을 바꾼다(빈 값이면 빈 값으로). 받지 못하면(오류·자료 없음·단위 가드) 있던 ok 행은 그대로 둔다.
"""
import argparse, asyncio, os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from open_proxy_mcp.market_codes import KS as MKT_KS, KQ as MKT_KQ
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import psycopg
from datetime import date
from open_proxy_mcp.services.scale_guard import gid_exact, assess as scale_assess, MARKET_MAX_NI_ANCHOR
from open_proxy_mcp.dart.fx import SRC_YAHOO, ecos_key_missing_note, fiscal_year_end_date, fx_source, statement_currency, fx_to_krw
from open_proxy_mcp.services import report_calendar as rcal
from open_proxy_mcp.services import period_cells as pcells
from open_proxy_mcp.services.controlling_ni import controlling_ni as _cni


def _latest_annual_fy(today: date | None = None) -> int:
    """그 시점 확정된 최신 사업연도 — 사업보고서 3월 공시(4월 이후 전년, 아니면 전전년). _pit_fy와 동일."""
    t = today or date.today()
    return t.year - 1 if t.month >= 4 else t.year - 2


# 2018 ~ 최신 확정 FY(자동 확장) — 신규 사업연도 공시 후 재실행하면 resume(done)이 그 FY만 수집.
# 261018: 수집 대상 상한은 종목마다 결산월로 센다(`fetch` 의 `rcal.latest_annual`) — 이 목록은 12월 결산 기준(종전 그대로, 표시용).
YEARS = list(range(2018, _latest_annual_fy() + 1))
#: 사업보고서 nodata(그 표기의 보고서가 없음) 를 다시 받는 기간 — 공시 시점(결산월 다음 넷째 달 1일)부터 이 날수까지(261018).
#: 분기 수집(`market_fund_quarterly.NODATA_RETRY_DAYS`)과 같은 규칙: 첫 실행 때 아직 안 낸 회사가 나중에 내도 영영 비지 않게.
ANNUAL_NODATA_RETRY_DAYS = int(os.getenv("SERIES_NODATA_RETRY_DAYS", "400"))
#: 261019: 다시 받기 창(위)과 상관없이 nodata 를 다시 받을 사업연도(쉼표) — 수집기가 그 해 응답을 못 읽어 nodata 로 굳은 해를 한 번 다시 받을 때.
#: 예) `SERIES_REFETCH_NODATA_FY=2018` — 2018 사업보고서는 옛 계정 분류체계(`ifrs_` 접두어)라 종전 수집기가 읽지 못했다(`gid`).
REFETCH_NODATA_FY = frozenset(int(y) for y in os.getenv("SERIES_REFETCH_NODATA_FY", "").replace(" ", "").split(",") if y.isdigit())


def fetch_plan(firms, have: dict, fye_of: dict, today: date | None = None,
               nodata_days: int | None = None, refetch_nodata_fy=None) -> list[tuple]:
    """받을 (ticker, corp_code, fy) — 순수 함수(시험 대상).

    261018(재설계 6단계, 결산월): 종전엔 모든 회사의 상한이 12월 결산 규칙(4월 이후 전년)이라 3월 결산 회사의 2026.03 사업보고서
    (6월 제출)를 2027년 4월에야 받았다. 이제 종목마다 결산월로 「지금 나와 있어야 할 최신 사업보고서 표기」(`report_calendar.latest_annual`)까지 받는다.
    이미 있는 행: ok·restate_only = 완료(종전과 같다) · err = 다시 받는다 · nodata = 공시 시점 뒤 `ANNUAL_NODATA_RETRY_DAYS` 일 안이면 다시 받는다
    (종전엔 어떤 상태든 행이 있으면 완료라, 첫 실행 때 아직 안 낸 회사는 영영 비었다).
    `refetch_nodata_fy`(기본 `REFETCH_NODATA_FY`)의 해는 창과 상관없이 nodata 를 다시 받는다(261019 — 수집기 결함으로 굳은 nodata)."""
    t = today or date.today()
    days = ANNUAL_NODATA_RETRY_DAYS if nodata_days is None else nodata_days
    again = REFETCH_NODATA_FY if refetch_nodata_fy is None else frozenset(refetch_nodata_fy)
    out = []
    for isu, cc in firms:
        fye = fye_of.get(isu, 12)
        for y in range(2018, rcal.latest_annual(t, fye) + 1):
            f = have.get((isu, y))
            if f is None or f.startswith("err:"):
                out.append((isu, cc, y))
            elif f == "nodata" and (y in again or 0 <= (t - rcal.cell_available(y, 4, fye)).days <= days):
                out.append((isu, cc, y))
    return out
DDL = """CREATE TABLE IF NOT EXISTS dart_finstat_y(
  ticker text, fy int, fs text, ni double precision, eq double precision,
  ni_restated double precision, eq_restated double precision,
  fetched text, PRIMARY KEY(ticker, fy))"""
DDL_MIGRATE = (
    "ALTER TABLE dart_finstat_y ADD COLUMN IF NOT EXISTS ni_restated double precision",
    "ALTER TABLE dart_finstat_y ADD COLUMN IF NOT EXISTS eq_restated double precision",
)
#: 261020(p1e firm-ctrl-ni): 그 행 값이 나온 사업보고서의 접수번호 — 정정 사업보고서가 나오면 다시 받는다(`refetch_amended`). 칸이 없으면(이행 전·권한 없음)
#: 종전처럼 쓰고 정정 판정은 목록 창(`ANNUAL_AMEND_SCAN_DAYS`) 안의 [기재정정] 만 본다.
DDL_RCEPT = "ALTER TABLE dart_finstat_y ADD COLUMN IF NOT EXISTS rcept_no text"
_RCEPT_COL = False
#: 261020(p1f unit-restated): 정정 칸(`ni_restated`·`eq_restated`)이 **어느 보고서의 값인지** — 그 사업연도를 전기·전전기 칸에 적은 더 늦은 사업보고서
#: (정정 포함)의 접수번호(근거 칸). 같은 사업연도를 적은 보고서가 여럿이면 가장 늦게 접수된 보고서의 값이 칸을 차지한다(`restated_wins`).
#: 칸이 없으면(이행 전·권한 없음) 종전처럼 정정치 백필만 쓰고(덮어쓰기) 수집 경로는 정정 칸을 건드리지 않는다.
DDL_RESTATED_RCEPT = "ALTER TABLE dart_finstat_y ADD COLUMN IF NOT EXISTS restated_rcept_no text"
_RESTATED_COL = False

def num(v):
    try: return float(str(v).replace(",","")) if v not in (None,"","-") else None
    except: return None

def gid(rows, account_id, sj, field="thstrm_amount"):
    """정확일치(exact) — substring(in) 금지(260704 실측: 접두어 충돌로 오탐 확인, wiki §9).

    261019: 새 분류체계(`ifrs-full_…`)의 그 계정이 응답에 없으면 **옛 분류체계의 같은 이름**(`ifrs_…`)을 정확일치로 본다.
    2018 사업보고서(그 이전도)는 옛 분류체계로 제출돼 `ifrs_ProfitLossAttributableToOwnersOfParent`·`ifrs_EquityAttributableToOwnersOfParent`
    처럼 온다 — 종전엔 이를 못 찾아 FY2018 이 거의 전부 nodata 였다(운영 10-19: 2,599종목 중 2,559 · 005930 DART 직접 확인).
    분기 수집(`market_fund_quarterly`)은 이미 두 접두어를 다 본다. 같은 계정의 이름만 바뀐 것이라 뜻은 같다."""
    v = gid_exact(rows, account_id, sj, field)
    if v is None and account_id.startswith("ifrs-full_"):
        v = gid_exact(rows, "ifrs_" + account_id[len("ifrs-full_"):], sj, field)
    return v

_EQA = "ifrs-full_EquityAttributableToOwnersOfParent"
_Q = None


def _quarterly():
    """분기 수집기(`market_fund_quarterly.py`)의 순수 추출 함수 — 지배자본 트리를 두 벌로 두지 않으려고 같은 파일을 읽는다."""
    global _Q
    if _Q is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_mfq_for_series", Path(__file__).with_name("market_fund_quarterly.py"))
        _Q = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_Q)
    return _Q


def ctrl_eq(rows, field="thstrm_amount"):
    """지배자본 → (값, 거른 까닭 또는 None). 종전 규칙 그대로: 지배 귀속 계정(새·옛 접두어, 정확일치) → 없으면 자본총계.

    261019: 지배 귀속 계정이 붙은 줄이 **납입자본(자본금) 줄**이면(회사 XBRL 태깅 오류 — 이름이 자본금·납입자본이거나 값이 같은 재무상태표의
    자본금 계정과 같다) 그 줄은 버리고 분기 수집의 지배자본 규칙(`extract_controlling_eq` — 이름 → 자본총계 − 비지배지분 → 자본총계)으로 간다.
    10-19 운영: 056360 FY2018~21 · 065680 FY2018~24(FY2022·23 정정치 포함) · 069960 FY2019~21 의 자본이 자본금(49억·50억·1,170억)이라 PBR 이
    25~40배 부풀었다. 그 길로도 없으면 비운다(값 None — 까닭은 로그)."""
    Q = _quarterly()
    val = lambda r: num(r.get(field))  # noqa: E731
    row = next((r for aid in (_EQA, "ifrs_" + _EQA[len("ifrs-full_"):]) for r in rows
                if r.get("sj_div") == "BS" and r.get("account_id") == aid and str(r.get(field) or "") != ""), None)
    if row is not None and Q._capital_line(row, rows, val):
        bs = [{**r, "thstrm_amount": r.get(field)} for r in rows if r.get("sj_div") == "BS"]
        v, case = Q.extract_controlling_eq(bs)
        return v, f"지배자본 계정이 납입자본 줄({row.get('account_nm')} {val(row):.0f}원)이라 거름 → {case}"
    v = gid(rows, _EQA, ("BS",), field) or gid(rows, "ifrs-full_Equity", ("BS",), field)
    if v is None and any(r.get("sj_div") == "BS" for r in rows):
        # 261019: 두 계정이 다 없으면(표준 계정코드 없이 「지배기업지분」·「자본총계」로 낸 회사) 분기 수집과 같은 이름·총계 규칙으로.
        #   10-19 운영: 037560 FY2020·21 · 089860 FY2021 — 2026-07 이전 수집기(부분 일치)가 「자본과부채총계」(EquityAndLiabilities)를
        #   자본으로 읽어 두었고(PBR 이 수 분의 1), 정확 일치로 다시 받으면 빈 값이 됐다. 원문 「지배기업지분」 6,523.8억·1조 2,293억.
        bs = [{**r, "thstrm_amount": r.get(field)} for r in rows if r.get("sj_div") == "BS"]
        v2, case = Q.extract_controlling_eq(bs)
        if v2 is not None:
            return v2, f"지배자본·자본총계 계정이 없어 이름·총계 규칙 → {case}"
    return v, None


_NIA = "ifrs-full_ProfitLossAttributableToOwnersOfParent"


def ctrl_ni(rows, field="thstrm_amount"):
    """지배 순이익 → (값, 종전 규칙과 달라진 까닭 또는 None).

    261020(p1e firm-ctrl-ni): 분기 수집·서버 실시간 firm 과 **같은 규칙**(`open_proxy_mcp/services/controlling_ni.controlling_ni` — 값 구조 검사)으로 고른다.
    종전 규칙은 ① 지배 귀속 순이익 계정 줄(새·옛 접두어, 정확일치) → ② 없으면(또는 0) **당기순이익 총계(비지배 포함)** 였고, #152 가 ① 줄의 값이 총포괄
    지배 몫일 때만 분기 규칙으로 보냈다. 그래서 지배 계정 없이 이름으로 지배·비지배를 나눈 회사(표준 계정코드 미사용)는 연간 순이익에 비지배 몫이 섞였다
    (10-20 운영 089860 FY2021: 총계 1,135.8억 — 원문 지배 몫 1,187.5억, 비지배 −51.7억). 이제 비지배가 있으면 값 구조로 지배 몫을 고른다.
    비지배가 없는 회사(056360 FY2020·21 처럼 지배 줄에 총포괄 계정 id 를 붙였고 지배 몫 = 총계)는 종전과 같은 값이다.
    두 번째 값은 종전 규칙(① → ②)과 값이 달라졌을 때만 까닭(공용 규칙의 케이스 — `extract_controlling_ni_cum` 트리)을 적는다(수집 로그용)."""
    v, case = _cni(rows, field)
    old = gid(rows, _NIA, ("CIS", "IS"), field)
    if not old:
        old = gid(rows, "ifrs-full_ProfitLoss", ("CIS", "IS"), field)
    if v is None or (old is not None and abs(v - old) <= 1):
        if v is None and old is not None:
            return old, None             # 공용 규칙이 못 고르면(손익 줄이 없는 응답 꼴) 종전 값 — 값을 지우지 않는다
        return v, None
    return v, (f"지배 순이익 계정 줄 값 {old:.0f}원이 총포괄이익의 지배 몫이라 거름 → {case}" if "CIREJ" in case and old is not None
               else f"종전 규칙 값 {'없음' if old is None else f'{old:.0f}원'} → {case}")


def _pg():
    """261018: 표·칸이 이미 있으면 DDL 을 보내지 않는다 — 이 수집이 주 1회 일정(finstat-quarterly)에 들어가면서, 있어도 표 전체 잠금을
    잡는 `ALTER TABLE … ADD COLUMN IF NOT EXISTS` 를 매번 보내지 않게(서버의 연간 재무 읽기와 겹치지 않게)."""
    global _RCEPT_COL, _RESTATED_COL
    con=psycopg.connect(os.environ["DATABASE_URL"])
    cols={r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
                                    "AND table_name='dart_finstat_y'")}
    if not {"ni_restated","eq_restated"} <= cols:
        con.execute(DDL)
        for stmt in DDL_MIGRATE: con.execute(stmt)
    con.commit()
    if "rcept_no" not in cols:
        # 261020: 접수번호 칸(정정 사업보고서 다시 받기의 기준) — 못 만들면(권한 등) 칸 없이 종전처럼 간다(경고 한 줄)
        try:
            con.execute("SET lock_timeout = '3s'"); con.execute(DDL_RCEPT); con.commit(); cols.add("rcept_no")
        except psycopg.Error as e:
            con.rollback()
            print(f"::warning::[연간 재무] 접수번호 칸(dart_finstat_y.rcept_no)을 만들지 못했다({type(e).__name__}) — 정정 판정은 목록 창 안의 [기재정정] 만",flush=True)
    _RCEPT_COL = "rcept_no" in cols
    if "restated_rcept_no" not in cols:
        # 261020(p1f): 정정 칸의 출처 접수번호 칸 — 못 만들면 종전처럼(경고 한 줄)
        try:
            con.execute("SET lock_timeout = '3s'"); con.execute(DDL_RESTATED_RCEPT); con.commit(); cols.add("restated_rcept_no")
        except psycopg.Error as e:
            con.rollback()
            print(f"::warning::[연간 재무] 정정 칸 출처 칸(dart_finstat_y.restated_rcept_no)을 만들지 못했다({type(e).__name__}) — 정정 칸은 백필만 종전처럼",flush=True)
    _RESTATED_COL = "restated_rcept_no" in cols
    return con

_REPLACE_SQL = (
    "INSERT INTO dart_finstat_y (ticker,fy,fs,ni,eq,ni_restated,eq_restated,fetched) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON CONFLICT (ticker,fy) DO UPDATE SET fs=EXCLUDED.fs, ni=EXCLUDED.ni, eq=EXCLUDED.eq, fetched=EXCLUDED.fetched "
    "WHERE NOT (EXCLUDED.fetched <> 'ok' AND dart_finstat_y.fetched = 'ok')")


def _flush(buf, replace=False):
    """버퍼를 새 연결로 일괄 저장 — Supabase 유휴 끊김에 면역(flush마다 fresh conn).
    `replace`(261019 `--refetch`): 받은 순이익·자본으로 **바꾼다**(빈 값이면 빈 값으로 — 수집 규칙이 거른 칸을 비우려고). 받지 못한 결과(err·nodata —
    단위 가드 포함)는 있던 ok 행을 덮지 않는다. 정정치 칸은 손대지 않는다."""
    if not buf: return
    # 261020: 행 끝(9번째)의 접수번호 — 칸이 있으면 함께 쓰고(받지 못한 결과는 있던 값을 둔다), 없으면 떼고 종전 그대로 쓴다.
    rows9 = [tuple(r) + (None,) * (9 - len(r)) for r in buf]
    for attempt in (1,2):
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as c:
                with c.cursor() as cur:
                    if _RCEPT_COL:
                        cur.executemany(
                            "INSERT INTO dart_finstat_y (ticker,fy,fs,ni,eq,ni_restated,eq_restated,fetched,rcept_no) "
                            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (ticker,fy) DO UPDATE SET "
                            + ("fs=EXCLUDED.fs, ni=EXCLUDED.ni, eq=EXCLUDED.eq, fetched=EXCLUDED.fetched, "
                               "rcept_no=COALESCE(EXCLUDED.rcept_no, dart_finstat_y.rcept_no) "
                               "WHERE NOT (EXCLUDED.fetched <> 'ok' AND dart_finstat_y.fetched = 'ok')" if replace else
                               "fs=COALESCE(EXCLUDED.fs, dart_finstat_y.fs), ni=COALESCE(EXCLUDED.ni, dart_finstat_y.ni), "
                               "eq=COALESCE(EXCLUDED.eq, dart_finstat_y.eq), "
                               "ni_restated=COALESCE(EXCLUDED.ni_restated, dart_finstat_y.ni_restated), "
                               "eq_restated=COALESCE(EXCLUDED.eq_restated, dart_finstat_y.eq_restated), "
                               "fetched=CASE WHEN EXCLUDED.fetched <> 'restate_only' THEN EXCLUDED.fetched ELSE dart_finstat_y.fetched END, "
                               "rcept_no=COALESCE(EXCLUDED.rcept_no, dart_finstat_y.rcept_no)"),
                            rows9)
                        c.commit(); buf.clear(); return
                    buf8 = [r[:8] for r in rows9]
                    if replace:
                        cur.executemany(_REPLACE_SQL, buf8)
                        c.commit(); buf.clear(); return
                    # 순서 무관 병합: 실제조회(ni/eq)와 재작성(ni_restated/eq_restated)이 어느 순서로
                    # 와도 서로의 필드를 덮어쓰지 않음. fetched는 'restate_only'(선행 삽입용 자리표시)
                    # 보다 실제 상태('ok'/'nodata'/'err:*')가 항상 우선.
                    cur.executemany(
                        "INSERT INTO dart_finstat_y (ticker,fy,fs,ni,eq,ni_restated,eq_restated,fetched) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (ticker,fy) DO UPDATE SET "
                        "fs=COALESCE(EXCLUDED.fs, dart_finstat_y.fs), "
                        "ni=COALESCE(EXCLUDED.ni, dart_finstat_y.ni), "
                        "eq=COALESCE(EXCLUDED.eq, dart_finstat_y.eq), "
                        "ni_restated=COALESCE(EXCLUDED.ni_restated, dart_finstat_y.ni_restated), "
                        "eq_restated=COALESCE(EXCLUDED.eq_restated, dart_finstat_y.eq_restated), "
                        "fetched=CASE WHEN EXCLUDED.fetched <> 'restate_only' THEN EXCLUDED.fetched "
                        "             ELSE dart_finstat_y.fetched END",
                        buf8)
                c.commit()
            buf.clear(); return
        except psycopg.OperationalError:
            if attempt==2: raise

# ─────────────────────────────────────────────────────────────────────────────
# 정정 칸 우선 규칙(261020 p1f unit-restated) — 같은 사업연도를 적은 보고서가 여럿이면 **가장 늦게 접수된 보고서**의 값.
# 종전엔 정정 칸을 `--backfill-restated`(손으로 돌리는 백필, 기본 2024 사업보고서)만 썼고 출처를 남기지 않았다 — 그래서 #155 의 정정 사업보고서 다시
# 받기(`--amended`)가 그 해 칸만 바꾸고, 정정된 보고서의 전기·전전기 칸에서 오는 앞 두 해 정정 칸(2022·2023 행 약 2,400행의 출처)은 그대로였고,
# 백필을 다른 해 보고서로 다시 돌리면 늦은 보고서 값을 이른 보고서 값으로 덮을 수 있었다. 이제 사업보고서를 읽는 모든 길(정규 수집·정정 다시 받기·
# `--refetch`·백필)이 같은 응답의 전기·전전기 칸을 이 규칙으로 정정 칸에 쓰고 출처 접수번호(`restated_rcept_no`)를 남긴다 — DART 콜은 늘지 않는다.
# ─────────────────────────────────────────────────────────────────────────────

def prior_candidates(rows, report_fy: int) -> list[tuple[int, float | None, float | None]]:
    """사업보고서(표기 `report_fy`) 응답의 전기 칸(→ report_fy−1)·전전기 칸(→ report_fy−2) 지배 순이익·지배자본 — 그 해 칸과 **같은 규칙**
    (`ctrl_ni`·`ctrl_eq`). 둘 다 비면 그 해는 뺀다. 통화 환산은 호출부(그 해 결산월 말 환율)."""
    out = []
    for fy_off, field in ((report_fy - 1, "frmtrm_amount"), (report_fy - 2, "bfefrmtrm_amount")):
        ni = ctrl_ni(rows, field)[0]
        eq = ctrl_eq(rows, field)[0]
        if ni is not None or eq is not None:
            out.append((fy_off, ni, eq))
    return out


def restated_wins(cand_rcept: str | None, *, own_rcept: str | None = None, restated_rcept: str | None = None) -> bool:
    """정정 칸 후보가 칸을 차지하나(순수 함수 — 아래 SQL `_restated_update_sql`·`_restated_upsert_sql` 의 접수번호 조건과 같다).

    후보 = 그 사업연도를 전기·전전기 칸에 적은 **더 늦은 사업보고서**(정정 포함)의 값, `cand_rcept` = 그 보고서 접수번호.
    · 그 해 보고서(정정 포함, `own_rcept`)가 후보보다 늦게 접수됐으면 그 해 보고서가 이긴다 — 후보는 쓰지 않는다.
    · 지금 정정 칸의 출처(`restated_rcept`)보다 이르면 쓰지 않는다. 같은 보고서를 다시 읽은 것은 쓴다(규칙을 고친 뒤 같은 원문을 다시 읽을 때).
    · 출처를 모르는 정정 칸(이 규칙 전 백필 — 출처 칸이 빔)은 가장 이른 것으로 본다: 그 값은 이 규칙이 들어오기 전에 접수된 보고서에서 왔고,
      이 뒤로 수집이 읽는 보고서(새 사업보고서·최근 정정)는 그보다 늦다. `--backfill-restated --report-year` 로 더 이른 해 보고서를 손으로 다시 돌릴
      때만 예외가 생길 수 있다(그 보고서가 출처 모르는 칸의 실제 출처보다 이르면 덮는다 — 종전 백필과 같다).
    접수번호는 「접수일 YYYYMMDD + 일련번호」 14자리라 글자 순서가 곧 접수 순서다."""
    if not cand_rcept:
        return False
    if own_rcept and cand_rcept <= own_rcept:
        return False
    if restated_rcept and cand_rcept < restated_rcept:
        return False
    return True


#: 후보가 그 해 칸과 「같다」고 보는 폭(상대) — 다음 해 보고서가 백만 원 단위로 반올림해 적은 전기 칸(10-20 운영 005830 FY2023 자본 400만 원 차)을
#: 재작성으로 보지 않는다. 1원 미만 차도 같다.
RESTATED_SAME_REL = 1e-6


def _differs(a, b) -> bool:
    if a is None:
        return False
    if b is None:
        return True
    return abs(a - b) > max(1.0, abs(b) * RESTATED_SAME_REL)


def restated_worth(cand_ni, cand_eq, row) -> bool:
    """후보를 쓸 가치가 있나(순수 함수). row = (그 해 순이익, 자본, 정정 순이익, 정정 자본) 또는 None(행 없음).
    정정 칸이 이미 있으면 늘(값과 출처를 새 보고서로 — 같은 값이면 출처만 바뀐다), 없으면 후보가 그 해 칸과 다를 때만(재작성) —
    정정 칸이 없고 후보가 그 해 칸과 같으면(재작성 없음) 쓰지 않는다: 회사 2,600곳의 같은 값을 정정 칸에 복사하지 않는다."""
    if row is None:
        return cand_ni is not None or cand_eq is not None
    ni, eq, nir, eqr = row
    if nir is not None or eqr is not None:
        return True
    return _differs(cand_ni, ni) or _differs(cand_eq, eq)


def _restated_update_sql() -> str:
    """수집 경로: 있는 행만 바꾼다(없는 해에 `restate_only` 행을 만들면 `fetch_plan` 이 그 해 보고서를 받지 않는다). 접수번호 조건은 `restated_wins` 와 같다."""
    own = " AND %(rc)s > COALESCE(rcept_no, '')" if _RCEPT_COL else ""
    return ("UPDATE dart_finstat_y SET ni_restated=COALESCE(%(ni)s::float8, ni_restated), "
            "eq_restated=COALESCE(%(eq)s::float8, eq_restated), restated_rcept_no=%(rc)s "
            "WHERE ticker=%(t)s AND fy=%(fy)s AND %(rc)s >= COALESCE(restated_rcept_no, '')" + own)


def _restated_upsert_sql() -> str:
    """정정치 백필: 행이 없으면 `restate_only` 로 넣는다(종전 그대로), 있으면 규칙대로(접수번호 조건은 `restated_wins` 와 같다)."""
    own = " AND EXCLUDED.restated_rcept_no > COALESCE(dart_finstat_y.rcept_no, '')" if _RCEPT_COL else ""
    return ("INSERT INTO dart_finstat_y (ticker,fy,fs,ni,eq,ni_restated,eq_restated,fetched,restated_rcept_no) "
            "VALUES (%(t)s,%(fy)s,%(fs)s,NULL,NULL,%(ni)s,%(eq)s,'restate_only',%(rc)s) ON CONFLICT (ticker,fy) DO UPDATE SET "
            "ni_restated=COALESCE(EXCLUDED.ni_restated, dart_finstat_y.ni_restated), "
            "eq_restated=COALESCE(EXCLUDED.eq_restated, dart_finstat_y.eq_restated), "
            "restated_rcept_no=EXCLUDED.restated_rcept_no "
            "WHERE EXCLUDED.restated_rcept_no >= COALESCE(dart_finstat_y.restated_rcept_no, '')" + own)


#: 정정 사업보고서로 그 해 칸을 다시 받았을 때 정정 칸 비우기 — 그 정정 보고서가 정정 칸 출처보다 늦으면 그 해 보고서가 가장 늦은 보고서다.
_RESTATED_CLEAR_SQL = ("UPDATE dart_finstat_y SET ni_restated=NULL, eq_restated=NULL, restated_rcept_no=NULL "
                       "WHERE ticker=%(t)s AND fy=%(fy)s AND (ni_restated IS NOT NULL OR eq_restated IS NOT NULL) "
                       "AND COALESCE(restated_rcept_no, '') < %(rc)s")


def report_year_mismatch(rows, year: int) -> str | None:
    """응답의 사업연도(`bsns_year`)가 요청한 해와 다르면 그 해(문자열), 같거나 칸이 없으면 None.
    10-20 실측: 082660 은 2024 요청에 2025 사업보고서(제25기 — 당기 칸이 비고 전기·전전기 = 2024·2023)가 와서, 정정치 백필이 그 전기·전전기를
    2023·2022 정정 칸에 넣어 **한 해 밀렸다**(FY2022 정정 칸 = FY2023 값). 캐시 원문 2,210건 가운데 이 한 건뿐이다."""
    by = next((str(r.get("bsns_year")) for r in rows or [] if r.get("bsns_year")), None)
    return by if by and by != str(year) else None


def unit_mismatch(a, b) -> bool:
    """같은 사업연도의 같은 숫자인데 10ⁿ 배(|n|≥2) 다른가 — 보고서 사이 단위 오류 꼴(`scale_guard.check_prior_period_mismatch`, 서버 실시간 firm 의
    보고서 사이 단위 가리기와 같은 판정). 이 꼴이면 어느 쪽이 맞는지 이 자리에선 모르므로 정정 칸을 바꾸지 않고 경고만 남긴다."""
    from open_proxy_mcp.services.scale_guard import check_prior_period_mismatch
    return bool(a) and bool(b) and (a > 0) == (b > 0) and check_prior_period_mismatch(a, b).get("triggered", False)


def _flush_restated(ops: list[tuple[str, dict]]) -> None:
    """정정 칸 작업(`("update"|"upsert"|"clear", 값)`)을 새 연결 하나로 차례대로 — 그 해 칸 쓰기(`_flush`) 뒤에 부른다."""
    if not ops or not _RESTATED_COL:
        ops.clear(); return
    sqls = {"update": _restated_update_sql(), "upsert": _restated_upsert_sql(), "clear": _RESTATED_CLEAR_SQL}
    for attempt in (1, 2):
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as c:
                with c.cursor() as cur:
                    for kind, params in ops:
                        cur.execute(sqls[kind], params)
                c.commit()
            ops.clear(); return
        except psycopg.OperationalError:
            if attempt == 2: raise


async def fetch(tickers: list[str] | None = None, only: set[tuple[str, int]] | None = None, keep_fs: bool = False,
                amended: bool = False):
    """`tickers` 를 주면 그 종목만(261018 — 결산월이 12월이 아닌 회사의 새 사업보고서만 좁혀 받을 때).
    `only` = {(종목, 사업연도)} — 그 행만 다시 받는다(이미 ok 여도, 261019 `--refetch`). 결과는 `_flush(replace=True)` 로 쓴다.
    `keep_fs`(261020 검토 — 정정 사업보고서 다시 받기): 그 행이 받았던 재무제표(연결/별도)가 빈 응답이면 다른 쪽으로 물러나지 않고 행을 그대로 둔다.
    정정본에 연결 재무제표가 없다고 연결 행을 별도 값으로 바꿔 쓰면 그 해만 기준이 갈린다(10-20 [기재정정] 12행 — 연결 요청에 빈 응답, 별도만 옴).
    261020(p1f): 읽은 사업보고서의 전기·전전기 칸을 앞 두 해 정정 칸 후보로 쓴다(`prior_candidates` · `restated_wins` — 가장 늦게 접수된 보고서 우선,
    출처 접수번호를 남긴다). `amended`(정정 사업보고서 다시 받기): 그 정정 보고서가 그 해 정정 칸의 출처보다 늦으면 정정 칸을 비워 그 해 칸이 쓰이게 한다.
    반기·분기 결산 회사(결산기 칸 — 정정 칸이 결산기 12개월 합)는 둘 다 하지 않는다. 같은 숫자가 10ⁿ 배 다르면(보고서 사이 단위 오류 꼴) 바꾸지 않고 경고."""
    from open_proxy_mcp.dart.client import get_dart_client, DartClientError, dart_halts_batch, dart_batch_halt_line
    sleep_s = float(os.getenv("SERIES_SLEEP", "0.45"))  # 260706: 필요시 조정, 기본값은 검증된 0.45 유지
    con=_pg()
    buf=[]
    firms=[r for r in con.execute("SELECT ticker, corp_code FROM dart_fundamentals WHERE fetched='ok' ORDER BY ticker")]
    if tickers:
        firms=[r for r in firms if r[0] in set(tickers)]
    have={(r[0],int(r[1])): (r[2] or "") for r in con.execute("SELECT ticker, fy, fetched FROM dart_finstat_y")}
    # 261019 --refetch: 그 행이 받았던 재무제표(연결/별도)부터 — 별도만 내는 회사는 연결을 먼저 불러 013 을 받던 콜을 아낀다(빈 응답이면 다른 쪽).
    fs_had={(r[0],int(r[1])): r[2] for r in con.execute("SELECT ticker, fy, fs FROM dart_finstat_y WHERE fs IS NOT NULL")} if only else {}
    fye_of=rcal.load_fye_map(con)
    # 261020(p1f): 정정 칸 후보 대조용 — 지금 유효 값(정정 우선)·정정 칸 유무, 결산기 칸 종목(정정 칸 = 결산기 12개월 합이라 건드리지 않는다)
    ysnap={(r[0],int(r[1])): (r[2],r[3],r[4],r[5]) for r in con.execute(
        "SELECT ticker, fy, ni, eq, ni_restated, eq_restated FROM dart_finstat_y")} if _RESTATED_COL else {}
    ptk={r[0] for r in con.execute("SELECT DISTINCT ticker FROM dart_finstat_q WHERE restate_why LIKE %s",(pcells.MARK_LIKE,))} \
        if _RESTATED_COL and (con.execute("SELECT to_regclass('public.dart_finstat_q')").fetchone() or [None])[0] else set()
    rops=[]; rstat={"cand":0,"skip_unit":0,"clear":0}
    # 시장 내 실측 최댓값 앵커(scale_guard.MARKET_MAX_NI_ANCHOR) — dart_finstat_y DB값으로 동적
    # 확장 금지: 이 테이블엔 이미 소프트센 오염값(1.07×10^16)이 남아있어(구버전 fetch, 가드 신설
    # 전 수집) MAX()로 앵커를 잡으면 오염값이 그대로 앵커가 되어 가드가 무력화되는 자기오염 실측 확인.
    market_max_ni = MARKET_MAX_NI_ANCHOR
    con.close()  # 이후 쓰기는 _flush(fresh conn)만 사용
    todo=fetch_plan(firms, have, fye_of)
    if only:
        cc_of={i:cc for i,cc in firms}
        todo=[(i,cc_of[i],y) for i,y in sorted(only) if i in cc_of]
    n_re=sum(1 for i,_,y in todo if (i,y) in have)
    print(f"대상 {len(firms)}사(12월 결산이 아닌 {len(fye_of)}사는 결산월로 상한) · 남은 {len(todo)}건(그중 다시 받기 {n_re})",flush=True)
    c=get_dart_client(); k=0
    async def acnt(cc,yr,fs):
        try:
            d=await c.get_fnltt_singl_acnt_all(cc,str(yr),"11011",fs)
            return (d.get("list") or []) if isinstance(d,dict) else []
        except DartClientError as e:
            if "[013]" in str(e): return []
            raise
    for isu,cc,yr in todo:
        k+=1
        try:
            first,second=("OFS","CFS") if fs_had.get((isu,yr))=="OFS" else ("CFS","OFS")
            fs=first; rows=await acnt(cc,yr,fs); await asyncio.sleep(sleep_s)
            if not rows and keep_fs and fs_had.get((isu,yr)):
                print(f"[정정] {isu} FY{yr} 정정본에 {fs} 재무제표가 없다 — 있던 행 그대로(연결/별도를 바꿔 쓰지 않는다)",flush=True)
                continue
            if not rows:
                fs=second; rows=await acnt(cc,yr,fs); await asyncio.sleep(sleep_s)
            if report_year_mismatch(rows, yr):
                # 261020(p1f): 요청한 사업연도와 다른 해 보고서가 왔다 — 그 값을 이 해 칸·앞 두 해 정정 칸에 넣지 않는다(자료 없음으로)
                print(f"::warning::[연간 재무] {isu} FY{yr} 요청에 {report_year_mismatch(rows, yr)} 사업연도 보고서가 왔다 — 이 해는 자료 없음으로",flush=True)
                rows=[]
            ni,ni_note=ctrl_ni(rows)              # 261020: 지배 순이익 계정 줄의 총포괄 값은 거른다
            if ni_note:
                print(f"[순이익] {isu} FY{yr} {ni_note}",flush=True)
            ni_frmtrm=ctrl_ni(rows,"frmtrm_amount")[0]   # 261020: 스케일 가드의 전기 값도 같은 규칙(종전 지배 계정 줄)
            eq,eq_note=ctrl_eq(rows)
            if eq_note:
                print(f"[자본] {isu} FY{yr} {eq_note}",flush=True)
            assets=gid(rows,"ifrs-full_Assets",("BS",)); liab=gid(rows,"ifrs-full_Liabilities",("BS",))
            eq_total=gid(rows,"ifrs-full_Equity",("BS",))
            # 통화 근본해법(260706): 연도별로 그 응답 자체에서 통화 감지(연도별 통화 상이 가능 —
            # 두산밥캣 fy≤2022 원화/fy2023+ USD) → **저장 전에 KRW로 환산**. valuation.py의 실시간
            # firm 조회와 동일 패턴(statement_currency+fx_to_krw) 재사용 — 로직 이중구현 방지.
            # 저장은 항상 KRW 원칙: 하위 소비처(firm_history 등)의 read-time FX를 이걸로 대체 가능.
            stmt_cur = statement_currency(rows)
            if stmt_cur != "KRW":
                # 261018: 결산월 말일(12월 결산이면 종전과 같은 12/31 — 2월 결산 외화 보고사가 아직 오지 않은 12/31 환율을 쓰던 것을 고침)
                _fx_dd = fiscal_year_end_date(yr, fye_of.get(isu, 12))
                ecos_fx_rate = await fx_to_krw(stmt_cur, _fx_dd)
                if ecos_fx_rate is None:
                    buf.append((isu,yr,None,None,None,None,None,f"err:fx_{stmt_cur}"))
                    if len(buf)>=25: _flush(buf, replace=bool(only))
                    continue
                if fx_source(stmt_cur, _fx_dd) == SRC_YAHOO:
                    # 261019: ECOS 대신 야후 대체값 — 값은 담고(누락보다 낫다) 로그에 남긴다. 배치 환경에 ECOS_API_KEY 를 둘 것.
                    print(f"::warning::[환율] {isu} FY{yr} {stmt_cur} {_fx_dd} — ECOS 대신 야후 대체값 {ecos_fx_rate}",flush=True)
                def _fx(x): return x*ecos_fx_rate if x is not None else None
                ni,ni_frmtrm,eq,assets,liab,eq_total = (_fx(v) for v in (ni,ni_frmtrm,eq,assets,liab,eq_total))
            # 실시간 스케일 가드(소프트센 032680 사례, wiki §9) — mktcap은 이 배치엔 없어 ①②③만 적용.
            # 항등식은 총자본(지배+비지배) 기준 — eq(지배자본)와 별도로 조회 필요. (통화환산 후 KRW 기준으로 비교)
            verdict=scale_assess(thstrm=ni, frmtrm=ni_frmtrm, assets=assets, liabilities=liab, equity=eq_total)
            if verdict["tier"]=="hard":
                print(f"[가드] {isu} FY{yr} 스케일오류 감지({verdict['hard_hit']}) — ni/eq 무효화",flush=True)
                ni=eq=None
            st="ok" if (ni is not None or eq is not None) else "nodata"
            rc_own=pcells.rows_rcept(rows) if rows else None
            buf.append((isu,yr,fs,ni,eq,None,None,st,rc_own))   # 261020: 접수번호(정정 판정 기준)
            if _RESTATED_COL and st=="ok":
                ysnap.setdefault((isu,yr),(ni,eq,None,None))   # 같은 실행에서 새로 넣은 행도 다음 해 보고서의 정정 칸 후보를 받게
            if _RESTATED_COL and rows and rc_own and verdict["tier"]!="hard" and isu not in ptk:
                # 261020(p1f): 정정 사업보고서로 그 해 칸을 다시 받았으면 — 그 보고서가 정정 칸 출처보다 늦을 때 정정 칸을 비운다(SQL 이 출처를 대 본다)
                cur=ysnap.get((isu,yr))
                if amended and cur and (cur[2] is not None or cur[3] is not None):
                    if unit_mismatch(cur[3], eq) or unit_mismatch(cur[2], ni):
                        rstat["skip_unit"]+=1
                        print(f"::warning::[정정 칸] {isu} FY{yr} 정정 보고서 값이 정정 칸과 10의 거듭제곱 배 다르다 — 정정 칸 그대로(단위 정정 칸일 수 있다)",flush=True)
                    else:
                        rops.append(("clear",{"t":isu,"fy":yr,"rc":rc_own})); rstat["clear"]+=1
                # 같은 응답의 전기·전전기 칸 → 앞 두 해 정정 칸 후보(가장 늦게 접수된 보고서 우선 — SQL 이 출처·그 해 보고서 접수번호를 대 본다)
                for fy_off,cni,ceq in prior_candidates(rows,yr):
                    if stmt_cur!="KRW":
                        _r=await fx_to_krw(stmt_cur, fiscal_year_end_date(fy_off, fye_of.get(isu, 12)))
                        if _r is None:
                            continue
                        cni,ceq=(v*_r if v is not None else None for v in (cni,ceq))
                    row=ysnap.get((isu,fy_off))
                    if row is None or not restated_worth(cni,ceq,row):
                        continue                       # 행이 없거나(그 해는 정규 수집 몫) 재작성 없음
                    eff_ni=row[2] if row[2] is not None else row[0]; eff_eq=row[3] if row[3] is not None else row[1]
                    if unit_mismatch(ceq, eff_eq) or unit_mismatch(cni, eff_ni):
                        rstat["skip_unit"]+=1
                        print(f"::warning::[정정 칸] {isu} FY{fy_off} {yr} 사업보고서 전기 칸이 지금 값과 10의 거듭제곱 배 다르다 — 쓰지 않는다(보고서 사이 단위 오류 꼴)",flush=True)
                        continue
                    rops.append(("update",{"t":isu,"fy":fy_off,"ni":cni,"eq":ceq,"rc":rc_own})); rstat["cand"]+=1
            if len(buf)>=25:
                _flush(buf, replace=bool(only)); _flush_restated(rops)
        except Exception as e:
            en=type(e).__name__
            if "ReadError" in en or "Connect" in en or "Timeout" in en:
                _flush(buf, replace=bool(only)); _flush_restated(rops)
                print(f"네트워크({en}) — 중단(재개 가능)",flush=True); break
            if dart_halts_batch(e):
                # 261019: 키 한도·키 문제·점검(남은 키까지 다 본 뒤) — 남은 항목도 같은 답이다. 멈추고, 이 항목은 err 로 적지 않는다
                #   (행이 그대로라 다음 실행의 재개 규칙이 이 항목부터 받는다). 종전엔 항목마다 err 로 적고 쉬지 않고 다음을 불렀다.
                _flush(buf, replace=bool(only)); _flush_restated(rops)
                print(dart_batch_halt_line(e, f"{k-1}/{len(todo)}건 처리"),flush=True); break
            buf.append((isu,yr,None,None,None,None,None,f"err:{str(e)[:30]}"))
            if len(buf)>=25: _flush(buf, replace=bool(only))
            await asyncio.sleep(sleep_s)   # 261019: 오류 뒤에도 같은 간격 — 종전엔 오류가 이어지면 쉬지 않고 불렀다
        if k%200==0: print(f"{k}/{len(todo)}",flush=True)
    _flush(buf, replace=bool(only)); _flush_restated(rops)
    print("fetch 종료" + (f" — 정정 칸 후보 {rstat['cand']}(규칙에 맞을 때만 씀) · 정정 보고서로 비움 {rstat['clear']} · 단위 꼴로 건너뜀 {rstat['skip_unit']}"
                          if _RESTATED_COL else ""),flush=True)


#: 261020(p1e firm-ctrl-ni): 정정 사업보고서를 찾는 공시 목록 창(오늘부터 거꾸로 일수). 이 수집은 주 1회(분기 마감 뒤 한 달은 매일) 돌고, 행마다 그 값이
#: 나온 보고서의 접수번호를 남기므로(`rcept_no`) 창은 실행 간격 + 여유면 된다. 빠진 실행을 메울 때는 `--amended-since YYYYMMDD`.
ANNUAL_AMEND_SCAN_DAYS = int(os.getenv("SERIES_AMEND_SCAN_DAYS", "10"))
_ANNUAL_RPT = re.compile(r"사업보고서\s*\((\d{4})\.(\d{2})\)")


def amended_targets(items, rows: dict, cc_to_ticker: dict) -> set[tuple[str, int]]:
    """정정 사업보고서가 나온 연간 행 — 다시 받을 {(종목, 사업연도)}(순수 함수, 시험 대상).

    261020(p1e firm-ctrl-ni): 연간 수집은 받은(ok) 행을 다시 받지 않아, 사업보고서를 [기재정정]으로 고쳐 내도 값이 그대로였다(10-20 운영 246710 FY2025:
    DB −349.4억 · 8/25 기재정정 뒤 DART −319.5억). 분기 결산 리츠의 결산기 칸(#153)처럼 **새 보고서(정정 포함)가 있을 때만** 다시 받는다.
    `items` = DART 공시 목록 항목(report_nm·rcept_no·corp_code), `rows` = {(종목, 사업연도): (fetched, 저장된 접수번호 또는 None)}.
    · 사업보고서 이름의 연도(「사업보고서 (2025.12)」 → 2025, 「(2026.03)」 → 2026)가 연간 표의 사업연도다(DART 표기 — 연간 표와 같다).
    · [첨부정정]·[첨부추가]는 재무제표를 바꾸지 않아 보지 않는다.
    · 저장된 접수번호가 있으면 그보다 늦은 보고서(정정 포함)가 있을 때, 없으면(칸 이행 전에 받은 행) 목록에 [기재정정]이 있을 때 다시 받는다 —
      다시 받으면 접수번호가 남아 같은 정정으로 또 받지 않는다. ok 가 아닌 행은 정규 수집(`fetch_plan`)이 본다."""
    latest: dict[tuple[str, int], str] = {}
    amended: set[tuple[str, int]] = set()
    for it in items or []:
        nm = it.get("report_nm") or ""
        m = _ANNUAL_RPT.search(nm)
        t = cc_to_ticker.get(it.get("corp_code"))
        if not m or not t or "첨부" in nm.split("사업보고서")[0]:
            continue
        k = (t, int(m.group(1)))
        rc = str(it.get("rcept_no") or "")
        if rc > latest.get(k, ""):
            latest[k] = rc
        if "기재정정" in nm:
            amended.add(k)
    out = set()
    for k, rc in latest.items():
        st, stored = rows.get(k, (None, None))
        if st != "ok":
            continue
        if (stored and rc > stored) or (not stored and k in amended):
            out.add(k)
    return out


async def refetch_amended(days: int | None = None, since: str | None = None, today: date | None = None) -> set[tuple[str, int]]:
    """정정 사업보고서가 나온 연간 행만 다시 받는다(`amended_targets` → `fetch(only=…)`, 받은 값으로 바꾼다 — 261019 `--refetch` 와 같은 쓰기).
    콜: 정기공시 목록(코스피·코스닥 · 창 10일 ≈ 4~10콜 — DART 가 상세 유형 거르기를 받지 않아 분기·반기보고서도 섞여 온다) + 대상 행마다 1~2콜.
    Q4 seed(연간 → 분기 표 사업보고서 칸)와 파생은 이 뒤 단계가 그대로 옮긴다(값이 같은 행은 다시 쓰지 않는다)."""
    from datetime import timedelta
    from open_proxy_mcp.dart.client import get_dart_client, DartClientError, market_window_end, dart_halts_batch, dart_batch_halt_line
    t = today or date.today()
    start = date(int(since[:4]), int(since[4:6]), int(since[6:8])) if since else t - timedelta(days=ANNUAL_AMEND_SCAN_DAYS if days is None else days)
    con = _pg()
    cc_to_ticker = {cc: tk for tk, cc in con.execute("SELECT ticker, corp_code FROM dart_fundamentals WHERE fetched='ok' AND corp_code IS NOT NULL")}
    rows = {(r[0], int(r[1])): (r[2], r[3] if len(r) > 3 else None) for r in con.execute(
        "SELECT ticker, fy, fetched" + (", rcept_no" if _RCEPT_COL else "") + " FROM dart_finstat_y")}
    con.close()
    c = get_dart_client(); items = []; calls = 0
    try:
        for cls in ("Y", "K"):
            cur = start
            while cur <= t:
                end = min(market_window_end(cur), t); page = 1
                while True:
                    try:
                        res = await c.search_filings(bgn_de=cur.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"), pblntf_ty="A",
                                                     corp_cls=cls, page_no=page, page_count=100)
                    except DartClientError as e:
                        if "013" in str(e):
                            break
                        raise
                    calls += 1
                    items += [it for it in res.get("list") or [] if "사업보고서" in (it.get("report_nm") or "")]
                    if page >= int(res.get("total_page") or 1):
                        break
                    page += 1
                    await asyncio.sleep(0.45)
                cur = end + timedelta(days=1)
    except Exception as e:  # noqa: BLE001 — 목록을 다 못 읽으면 다시 받지 않는다(다음 실행이 같은 창을 다시 본다)
        if dart_halts_batch(e):
            print(dart_batch_halt_line(e, "정정 사업보고서 목록"), flush=True)
        else:
            print(f"::warning::[연간 재무] 정정 사업보고서 목록 실패({type(e).__name__}) — 이번엔 다시 받지 않는다", flush=True)
        return set()
    targets = amended_targets(items, rows, cc_to_ticker)
    print(f"정정 사업보고서: 목록 {start}~{t} 사업보고서 {len(items)}건(DART {calls}콜) · 다시 받을 연간 행 {len(targets)}"
          + ("" if _RCEPT_COL else " (접수번호 칸 없음 — 창 안 [기재정정] 기준)"), flush=True)
    for tk, fy in sorted(targets):
        print(f"  [정정] {tk} FY{fy}", flush=True)
    if targets:
        await fetch(only=targets, keep_fs=True, amended=True)
    return targets


async def fetch_then_amended(tickers: list[str] | None = None, do_fetch: bool = True, amended: bool = False,
                             days: int | None = None, since: str | None = None) -> None:
    """정규 수집(`fetch`) 다음 정정 사업보고서 다시 받기(`refetch_amended`) — **한 이벤트 루프에서**(261020 검토).
    DART 클라이언트(`get_dart_client`)는 프로세스에 하나이고 httpx 연결 풀을 쥔다. 두 단계를 `asyncio.run` 두 번으로 돌리면 앞 단계가 DART 를
    한 번이라도 부른 날(주간 일정은 거의 늘 — 자료 없음 행을 400일 동안 다시 본다, 10-18 실행 「남은 11건」) 둘째 루프가 닫힌 루프의 연결을 다시 쓰다
    `RuntimeError: Event loop is closed` 로 정정 목록을 못 읽고, 경고 한 줄만 남긴 채 정정 행을 하나도 다시 받지 않았다."""
    if do_fetch:
        await fetch(tickers)
    if amended:
        await refetch_amended(days, since)


async def backfill_restated(tickers: list[str] | None = None, report_year: int = 2024):
    """이미 수집된 firms에 대해 **fy=report_year(기본 2024) 딱 1콜만** 재조회 — fnlttSinglAcntAll이 thstrm(2024)·
    frmtrm(2023)·bfefrmtrm(2022)을 한 응답에 다 주므로, 5년 재조회 없이 종목당 1콜로 2022~2024
    3개년 재작성치를 확보(소프트센 032680 실측으로 bfefrmtrm 제공 확인, 260704).

    261019: `tickers` 를 주면 그 종목만, 「이미 처리됨」 판정 없이 받는다 — 종전 판정은 두 해 중 **하나라도** 재작성치가 있으면 처리됐다고
    봐서, 한 해만 채워진 종목(10-19 운영 신영증권 001720 — 2022 칸만 있고 2023 칸이 빔. 원 사업보고서(2023.03)는 API 에 없다)을 다시
    보지 않았다. `report_year` 로 다른 해 보고서의 비교치도 받는다(DART 표기 — 결산월과 무관하게 전기 = 표기 −1, 전전기 = −2)."""
    from open_proxy_mcp.dart.client import get_dart_client, DartClientError, dart_halts_batch, dart_batch_halt_line
    con=_pg()
    firms=[r for r in con.execute("SELECT ticker, corp_code FROM dart_fundamentals WHERE fetched='ok' ORDER BY ticker")]
    fs_map={r[0]:r[1] for r in con.execute("SELECT ticker, fs FROM dart_finstat_y WHERE fy=%s", (report_year,))}
    # 이 함수는 fy=report_year 보고서를 조회해 그 앞 두 해 행에 재작성치를 쓴다(report_year 자체엔 안 씀).
    # 따라서 완료 판정은 두 해 중 하나라도 재작성치가 있으면 그 종목은 처리됐다고 본다(tickers 를 주면 판정하지 않는다).
    done=set() if tickers else {r[0] for r in con.execute(
        "SELECT ticker FROM dart_finstat_y WHERE fy IN (%s,%s) "
        "AND (ni_restated IS NOT NULL OR eq_restated IS NOT NULL)", (report_year-2, report_year-1))}
    fye_of=rcal.load_fye_map(con)
    # 261020(p1e): 반기·분기 결산 회사(결산기 칸 종목)는 건너뛴다 — 그 회사의 전기·전전기 칸은 앞 **결산기**(6·3개월 전)라 앞 사업연도 행에 넣으면
    #   틀린다(10-20 운영 롯데리츠 FY2022·2023 정정 칸이 그렇게 들어가 있었다). 연간 값은 --period-cells 가 결산기 12개월 합으로 쓴다.
    _ptk={r[0] for r in con.execute("SELECT DISTINCT ticker FROM dart_finstat_q WHERE restate_why LIKE %s",(pcells.MARK_LIKE,))}
    # 261020(p1f): 출처 칸이 있으면 수집과 같은 규칙(가장 늦게 접수된 보고서 우선 · 출처 접수번호 · 단위 꼴이면 건너뜀)으로 쓴다
    ysnap={(r[0],int(r[1])): (r[2],r[3],r[4],r[5]) for r in con.execute(
        "SELECT ticker, fy, ni, eq, ni_restated, eq_restated FROM dart_finstat_y")} if _RESTATED_COL else {}
    con.close()
    if tickers:
        firms=[r for r in firms if r[0] in set(tickers)]
    firms=[r for r in firms if r[0] not in _ptk]
    todo=[(i,c) for i,c in firms if i not in done]
    print(f"대상 {len(firms)}사 · 남은 {len(todo)}건 (종목당 1콜, fy={report_year})",flush=True)
    c=get_dart_client(); k=0
    restate_buf=[]; rops=[]
    for isu,cc in todo:
        k+=1
        try:
            fs=fs_map.get(isu,"CFS")
            d=await c.get_fnltt_singl_acnt_all(cc,str(report_year),"11011",fs)
            rows=(d.get("list") or []) if isinstance(d,dict) else []
            await asyncio.sleep(0.45)
            if report_year_mismatch(rows, report_year):
                print(f"::warning::[정정 칸] {isu} {report_year} 요청에 {report_year_mismatch(rows, report_year)} 사업연도 보고서가 왔다 — 쓰지 않는다",flush=True)
                continue
            stmt_cur=statement_currency(rows) if rows else "KRW"
            rc=pcells.rows_rcept(rows) if rows else None
            for fy_off,field in ((report_year-1,"frmtrm_amount"),(report_year-2,"bfefrmtrm_amount")):
                ni_r,ni_note=ctrl_ni(rows,field)     # 261020: 정정치도 같은 규칙
                if ni_note:
                    print(f"[순이익] {isu} FY{fy_off} 정정 {ni_note}",flush=True)
                eq_r,eq_note=ctrl_eq(rows,field)       # 261019: 납입자본 줄 태깅 오류는 정정치에도 있었다(065680 FY2022·23)
                if eq_note:
                    print(f"[자본] {isu} FY{fy_off} 정정 {eq_note}",flush=True)
                if stmt_cur!="KRW" and (ni_r is not None or eq_r is not None):
                    # 261019: 저장은 항상 원화(fetch 와 같은 규칙 — 그 해 결산월 말일 환율). 환율을 못 구하면 그 해는 쓰지 않는다.
                    _fx_dd=fiscal_year_end_date(fy_off, fye_of.get(isu, 12))
                    rate=await fx_to_krw(stmt_cur, _fx_dd)
                    if rate is None:
                        continue
                    if fx_source(stmt_cur, _fx_dd) == SRC_YAHOO:   # 261019: fetch 와 같은 경고
                        print(f"::warning::[환율] {isu} FY{fy_off} 정정 {stmt_cur} {_fx_dd} — ECOS 대신 야후 대체값 {rate}",flush=True)
                    ni_r, eq_r = (v*rate if v is not None else None for v in (ni_r, eq_r))
                if ni_r is None and eq_r is None:
                    continue
                if _RESTATED_COL and rc:
                    # 261020(p1f): 가장 늦게 접수된 보고서 우선 + 출처 접수번호(SQL `_restated_upsert_sql`) · 같은 숫자가 10ⁿ 배 다르면 쓰지 않는다
                    row=ysnap.get((isu,fy_off))
                    if not restated_worth(ni_r,eq_r,row):
                        continue
                    if row and (unit_mismatch(eq_r, row[3] if row[3] is not None else row[1])
                                or unit_mismatch(ni_r, row[2] if row[2] is not None else row[0])):
                        print(f"::warning::[정정 칸] {isu} FY{fy_off} {report_year} 사업보고서 전기 칸이 지금 값과 10의 거듭제곱 배 다르다 — 쓰지 않는다",flush=True)
                        continue
                    rops.append(("upsert",{"t":isu,"fy":fy_off,"fs":fs,"ni":ni_r,"eq":eq_r,"rc":rc}))
                else:
                    restate_buf.append((isu,fy_off,fs,None,None,ni_r,eq_r,"restate_only"))
            if len(restate_buf)>=25: _flush(restate_buf)
            if len(rops)>=25: _flush_restated(rops)
        except Exception as e:
            en=type(e).__name__
            if "ReadError" in en or "Connect" in en or "Timeout" in en:
                _flush(restate_buf); _flush_restated(rops)
                print(f"네트워크({en}) — 중단(재개 가능)",flush=True); break
            if dart_halts_batch(e):   # 261019: fetch 와 같은 규칙
                _flush(restate_buf); _flush_restated(rops)
                print(dart_batch_halt_line(e, f"{k-1}/{len(todo)}건 처리"),flush=True); break
            await asyncio.sleep(0.45)
        if k%200==0: print(f"{k}/{len(todo)}",flush=True)
    _flush(restate_buf); _flush_restated(rops)
    print("백필 종료",flush=True)

def series():
    con=psycopg.connect(os.environ["DATABASE_URL"])
    # 분기말 후보: krx_weekly에서 각 분기 마지막 주간일
    qs=con.execute("""SELECT MAX(price_dd) FROM krx_weekly
      GROUP BY LEFT(price_dd,4), CASE WHEN SUBSTRING(price_dd,5,2)::int<=3 THEN 1
        WHEN SUBSTRING(price_dd,5,2)::int<=6 THEN 2 WHEN SUBSTRING(price_dd,5,2)::int<=9 THEN 3 ELSE 4 END
      ORDER BY 1""").fetchall()
    qdates=[r[0] for r in qs if r[0]>="20210601"]
    # 261019: krx_weekly 는 보존 하한 이전을 종목별 연말 행만 남긴다 — 하한 이전 1~3분기의 「마지막 주간일」은 그 분기에 상장폐지한
    #   종목 몇 개의 마지막 행이라 시장 합이 아니다. 월말 원천(`month_end_prices`)이 온전하다고 말하는 날짜(연말·촘촘한 구간)만 쓴다.
    from scripts.month_end_prices import MonthEndPrices
    _src = MonthEndPrices(con, "20210601")
    _ok = set(_src.plan.dates)
    _drop = [d for d in qdates if d not in _ok and d < _src.floor]
    qdates = [d for d in qdates if d in _ok or d >= _src.floor]
    if _drop:
        print(f"[하한] 보존 하한 이전 분기말 {len(_drop)}개 건너뜀(그 날짜 시세가 부분 행뿐): {_drop[0]}~{_drop[-1]}")
    # 재무: FY별 (dart_finstat_y ∪ 최신 확정 FY=_latest_annual_fy()는 dart_fundamentals)
    # restated(다음 해 보고서 전기 비교치)가 있으면 그걸 우선 — 회사가 스스로 재작성한 참값
    # (소프트센 032680 FY2022 사례: 당해 XBRL 100만배 오류를 다음해 보고서가 정상화).
    fin={}
    restated_used=[]
    for isu,fy,ni,eq,ni_r,eq_r in con.execute(
            "SELECT ticker,fy,ni,eq,ni_restated,eq_restated FROM dart_finstat_y WHERE fetched='ok' OR ni_restated IS NOT NULL OR eq_restated IS NOT NULL"):
        final_ni = ni_r if ni_r is not None else ni
        final_eq = eq_r if eq_r is not None else eq
        if ni_r is not None and ni is not None and abs(ni_r - ni) > abs(ni) * 0.01:
            restated_used.append((isu, fy, ni, ni_r))
        fin[(isu,fy)]=(final_ni, final_eq)
    _lfy = _latest_annual_fy()
    for isu,ni,eq in con.execute("SELECT ticker,ni_fy,eq_fy FROM dart_fundamentals WHERE fetched='ok'"):
        fin[(isu,_lfy)]=(ni,eq)
    if restated_used:
        print(f"[재작성 적용] {len(restated_used)}건 (당해 XBRL 대신 다음해 보고서 전기 비교치 사용)")
        for r in restated_used[:10]: print(f"    {r}")

    # 스케일 오류 가드(재작성으로도 못 잡은 잔여 케이스): KOSDAQ 종목의 ni/eq가 50조 초과면
    # 자릿수 오류로 간주해 무효화(KOSPI 대기업은 실제로 100조 넘으므로 KOSDAQ만 적용).
    kosdaq_isu = {r[0] for r in con.execute(
        "SELECT ticker FROM dart_fundamentals WHERE market='KQ'")}
    SCALE_CAP = 50e12
    dropped = []
    for k in list(fin):
        isu, fy = k
        ni, eq = fin[k]
        if isu in kosdaq_isu and ((ni is not None and abs(ni) > SCALE_CAP) or
                                    (eq is not None and abs(eq) > SCALE_CAP)):
            dropped.append(k); del fin[k]
    if dropped:
        print(f"[가드] KOSDAQ 스케일오류 제외(재작성 미확보분): {dropped}")
    print(f"{'분기말':>9} {'시장':>7} {'PIT_FY':>6} {'PER':>7} {'PBR':>6} {'커버시총%':>7}")
    for d in qdates:
        y,m=int(d[:4]),int(d[4:6])
        pit_fy = y-1 if m>=4 else y-2
        caps=con.execute("SELECT ticker, market, mktcap FROM krx_weekly WHERE price_dd=%s",(d,)).fetchall()
        from collections import defaultdict
        agg=defaultdict(lambda: dict(cap=0,ni=0,eq=0,capn=0,cape=0,tot=0))
        for isu,market,cap in caps:
            if not cap: continue
            a=agg[market]; a["tot"]+=cap
            f=fin.get((isu,pit_fy))
            if not f: continue
            ni,eq=f
            if ni is not None: a["ni"]+=ni; a["capn"]+=cap
            if eq and eq>0: a["eq"]+=eq; a["cape"]+=cap
        for market in (MKT_KS,MKT_KQ):
            a=agg.get(market)
            if not a or not a["tot"]: continue
            per=a["capn"]/a["ni"] if a["ni"] and a["ni"]>0 else None
            pbr=a["cape"]/a["eq"] if a["eq"] else None
            cov=a["capn"]/a["tot"]*100
            print(f"{d:>9} {market:>7} {pit_fy:>6} {(f'{per:.1f}' if per else 'N/M'):>7} {(f'{pbr:.2f}' if pbr else '-'):>6} {cov:>6.0f}%")
    con.close()

def main(argv: list[str] | None = None) -> None:
    """명령줄 진입점(261020 검토: 시험이 같은 갈래 고르기를 부를 수 있게 함수로 — 동작은 종전 그대로)."""
    ap=argparse.ArgumentParser()
    ap.add_argument("--fetch",action="store_true")
    ap.add_argument("--backfill-restated",action="store_true",
                    help="종목당 1콜(fy=--report-year, 기본 2024)로 그 앞 두 해 재작성치만 백필")
    ap.add_argument("--report-year",type=int,default=2024,help="--backfill-restated 가 읽을 사업보고서 표기 연도")
    ap.add_argument("--series",action="store_true")
    ap.add_argument("--tickers",type=str,help="--fetch·--backfill-restated 를 이 종목만(콤마구분)")
    ap.add_argument("--amended",action="store_true",
                    help="261020: 정정 사업보고서(기재정정)가 나온 연간 행만 다시 받는다(공시 목록 창 --amended-days, 기본 10일)")
    ap.add_argument("--amended-days",type=int,default=None)
    ap.add_argument("--amended-since",type=str,default=None,help="정정 판정 목록 시작일 YYYYMMDD(빠진 실행 메우기)")
    ap.add_argument("--refetch",type=str,
                    help="이 (종목:사업연도) 행만 다시 받는다(이미 ok 여도) — 콤마구분(예 065680:2018). 수집 규칙을 고친 뒤 틀렸던 행만")
    a=ap.parse_args(argv)
    if a.fetch or a.backfill_restated or a.refetch or a.amended:
        _note=ecos_key_missing_note("연간 재무 수집")   # 261019: 외화 보고사 환산 — 키가 빠진 실행을 처음에 한 줄로
        if _note: print(_note,flush=True)
    if a.refetch:
        asyncio.run(fetch(only={(t,int(y)) for t,y in (x.split(":") for x in a.refetch.split(",") if x)}))
    elif a.fetch or a.amended:
        asyncio.run(fetch_then_amended(a.tickers.split(",") if a.tickers else None, a.fetch, a.amended, a.amended_days, a.amended_since))
    elif a.backfill_restated: asyncio.run(backfill_restated(a.tickers.split(",") if a.tickers else None, a.report_year))
    else: series()


if __name__=="__main__":
    main()
