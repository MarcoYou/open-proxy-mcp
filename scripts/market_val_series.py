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
정정되면 정정 칸을 비워 그 해 칸이 쓰인다. 종전엔 `--backfill-restated` 만 정정 칸을 썼고 출처를 남기지 않았다. 후보 보고서의 재무제표 기준
(연결/별도)이 그 해 행과 다르면 쓰지 않는다(`restated_basis_ok` — 그 해 행에 값이 하나도 없을 때만 채운다).
261021(DATA-PIT10): 더 늦은 보고서가 **지금 정정 칸과 같은 값**을 다시 적은 것(예: 2025 사업보고서 전전기 칸이 2024 사업보고서 전기 칸과 같다)은 출처를 늦은 보고서로 옮기지 않는다 —
출처는 그 값을 처음 낸 보고서다(`restated_same`·`restated_source`). 정정 칸은 출처가 공시된 날부터 쓰므로(`restated_pit`), 출처가 한 해 늦으면 그 한 해 동안 원본(틀린) 값을 썼다
(065150 FY2023: 2025-03-21 접수 2024 사업보고서가 낸 값을 2026-03-23 접수 2025 사업보고서를 출처로 잡아 1년 동안 원본 값). 더 이른 보고서를 나중에 읽어도 같은 값이면 출처가 그 보고서로 내려온다 — 그 보고서가 지금 정정 칸의 값 있는 칸(순이익·자본)을 모두 적었을 때만(`restated_covers`).

261021(DATA-SC4): 사업보고서를 읽는 길(정규 `--fetch`·정정 다시 받기 `--amended`·`--refetch`)에 **보고서 사이 단위 가드**를 넣었다. 회사가 단위를 잘못 적어
사업보고서가 통째로 10ⁿ 배면 한 보고서 안의 검사(항등식·자릿수)는 다 맞아 원본 칸에 그대로 들어가고(065150 FY2023 ×1,000 · 032680 FY2022 ×10⁶ · 311390 FY2022
×1,000 · 0091W0 FY2025 ×1,000), 정정 보고서가 와도 같은 일이 되풀이된다. 새 보고서의 당기·전기·전전기 자본을 **저장돼 있는 이웃 해 값**(원본 칸 · 정정 칸 — 접수번호로
보고서를 가른다)과 서버 실시간 firm·`financial_metrics` 와 같은 함수(`scale_guard.reconcile_report_scales`)로 대, 10ⁿ 배(|n|≥2) 어긋난 쪽을 다수결과 시총(PBR)
두 신호로 가린다. 새 보고서가 틀렸으면 금액 칸을 나눠 쓰고(`rescale_rows` — 주당 금액은 그대로), 못 가리면 쓰지 않는다(정규 수집은 「자료 없음」으로 두어 다음 보고서가 나오면
다시 보고, 다시 받기는 있던 ok 행을 그대로 둔다). 저장 칸이 틀린 쪽이면 새 보고서는 그대로 쓰고 경고만 한다(저장 칸 고치기는 데이터 갈래의 몫). DART 콜은 늘지 않는다.

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
from open_proxy_mcp.services.scale_guard import gid_exact, assess as scale_assess, reconcile_report_scales, rescale_rows
from open_proxy_mcp.dart.fx import SRC_YAHOO, ecos_key_missing_note, fiscal_year_end_date, fx_source, statement_currency, fx_to_krw
from open_proxy_mcp.clock import today_kst
from open_proxy_mcp.services import report_calendar as rcal
from open_proxy_mcp.services import period_cells as pcells
from open_proxy_mcp.services import restated_pit as rpit
from open_proxy_mcp.services.controlling_ni import controlling_ni as _cni
from open_proxy_mcp.services import controlling_eq as _ceq


def _latest_annual_fy(today: date | None = None) -> int:
    """그 시점 확정된 최신 사업연도 — 사업보고서 3월 공시(4월 이후 전년, 아니면 전전년). 261021(DATA-FY1): `report_calendar.latest_annual` 한 곳의 얇은 껍데기
    (세 파일에 따로 있던 규칙을 모았다). 날짜는 한국 달력(`today_kst`)."""
    return rcal.latest_annual(today or today_kst(), 12)


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
    t = today or today_kst()
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


_WHY_EQ = {"CAPLINE": "납입자본 줄", "COMPLINE": "자본 구성요소 줄", "OFFTOTAL": "자본총계 − 비지배와 크게 어긋난 줄"}


def ctrl_eq(rows, field="thstrm_amount"):
    """지배자본 → (값, 종전 규칙과 달라진 까닭 또는 None). 지배 귀속 계정 줄(새·옛 접두어, 정확일치)을 쓰고, 그 줄이 없거나 믿지 못하면
    분기 수집·서버 실시간 firm 과 **같은 규칙**(`services.controlling_eq.controlling_eq` — 이름 줄 → 자본총계 − 비지배지분 → 자본총계)으로 고른다.

    261019: 지배 귀속 계정이 붙은 줄이 **납입자본(자본금) 줄**이면(회사 XBRL 태깅 오류) 그 줄을 버렸다 — 10-19 운영 056360 FY2018~21 · 065680
    FY2018~24 · 069960 FY2019~21 의 자본이 자본금(49억·50억·1,170억)이라 PBR 이 25~40배 부풀었다.
    261020(p1f eq-suspects): ① 거르는 줄을 공용 판정(`ctrl_line_rejected`)으로 — 납입자본 줄에 더해 자본 구성요소 줄과 자본총계 − 비지배와 부호가
    다르거나 1.6배 넘게 다른 줄(060230 FY2018 「지배기업의소유주에게귀속되는자본」 −66.02억 = 자본금 + 결손금만 더한 소계, 자본총계 324.48억 · 비지배 0).
    ② 지배 귀속 계정 줄이 **없을 때** 종전엔 자본총계 계정(`ifrs-full_Equity` — 비지배 포함)을 그대로 썼다. 분기 수집은 같은 보고서에서 이름 줄
    (「지배기업소유주지분」 — 표준 코드 없음)이나 자본총계 − 비지배를 쓰므로, 비지배가 있는 회사는 사업보고서 칸(연간 표 → 분기 표 4분기 칸)만
    비지배를 품어 1~3분기 칸과 기준이 갈렸다(원문 캐시 사업보고서 1,609건 가운데 71건 — 000100 FY2025 2조 3,622.8억 vs 지배 2조 3,068.4억,
    112040 FY2023 4,007.8억 vs 2,487.8억, 419050 FY2024 1,042.4억 vs 738.4억). 이제 분기와 같은 값이다. 비지배가 없는 회사는 종전과 같다(자본총계).
    두 번째 값은 종전 규칙과 값이 달라졌을 때만 까닭을 적는다(수집 로그용)."""
    val = lambda r: num(r.get(field))  # noqa: E731
    row = next((r for aid in (_EQA, "ifrs_" + _EQA[len("ifrs-full_"):]) for r in rows
                if r.get("sj_div") == "BS" and r.get("account_id") == aid and str(r.get(field) or "") != ""), None)
    if row is not None and val(row) is None:
        # 261020(검토): 값이 「-」 같은 빈 표기면 계정 줄이 없는 것으로 본다 — 분기 수집(값이 있는 줄만 지배자본 줄)과 같다. 그러지 않으면 (None, None)
        #   으로 끝나 그 해가 빈다(main 은 자본총계로 넘어갔다).
        row = None
    why = _ceq.ctrl_line_rejected(row, rows, val) if row is not None else None
    if row is not None and not why:
        return val(row), None
    if not any(r.get("sj_div") == "BS" for r in rows):
        return None, None
    v, case = _ceq.controlling_eq(rows, field)
    if why:
        x = val(row)
        return v, f"지배자본 계정이 {_WHY_EQ[why]}({row.get('account_nm')} {'빈 값' if x is None else f'{x:.0f}원'})에 붙어 거름 → {case}"
    old = gid(rows, "ifrs-full_Equity", ("BS",), field)
    if old is not None and v is not None and abs(old - v) <= 1:
        return v, None
    # 261019: 두 계정이 다 없으면(표준 계정코드 없이 「지배기업지분」·「자본총계」로 낸 회사) 종전에도 이 규칙이었다 — 10-19 운영 037560 FY2020·21 ·
    #   089860 FY2021(2026-07 이전 수집기가 「자본과부채총계」를 자본으로 읽어 두었던 행).
    return v, (f"지배자본 계정 줄이 없어 {case}" + ("" if old is None else f"(종전 자본총계 {old:.0f}원 — 비지배 포함)"))


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

def prior_candidates(rows, report_fy: int, fye=12, own_terms: dict | None = None) -> list[tuple[int, float | None, float | None]]:
    """사업보고서(표기 `report_fy`) 응답의 전기 칸·전전기 칸 지배 순이익·지배자본 → `[(그 칸의 표기, 순이익, 자본)]` — 그 해 칸과 **같은 규칙**
    (`ctrl_ni`·`ctrl_eq`). 둘 다 비면 그 해는 뺀다. 통화 환산은 호출부(그 해 결산월 말 환율).

    261021(DATA-FY2 F10): 종전엔 전기 = `report_fy − 1`·전전기 = `report_fy − 2` 로 가정했다. 결산월을 바꾼 회사는 맞지 않아 정정 칸이 한 결산기씩 밀렸다
    (154030 FY2024 정정 칸 19.05억 = 실은 2025.09 결산기 · FY2023 −6.34억 = 2024.09 결산기, 222160 FY2022 1.22억 = 2023.03 결산기). 이제 전기·전전기 칸이 실제로
    어느 표기 칸의 자리인지 결산월로 가르고(`report_calendar.prior_columns` — 그 결산기가 그 표기 칸을 차지한 결산기일 때만, 전환기는 연간 값이 아니라 뺀다)
    보고서의 기수로 확인한다(`period_cells.term_no` 방식): 세 칸의 기수가 하나씩 줄어야 하고(`제 23 기`·`제 22 기`·`제 21 기` — 어긋나면 그 칸부터 뒤는 쓰지 않는다), 그 칸의 표기를 이 실행에서 읽은
    그 해 보고서(`own_terms` = {표기: 그 보고서 당기 기수})가 있으면 그 기수가 이 칸의 기수와 같아야 같은 결산기다. 어긋나면 그 칸은 쓰지 않는다.
    안 바꾼 회사는 종전 식(`report_fy − 1`·`− 2`)과 같다."""
    cols = rcal.prior_columns(report_fy, fye)
    terms = [pcells.term_no(rows, f) for f in ("thstrm_nm", "frmtrm_nm", "bfefrmtrm_nm")]
    out = []
    for k, ((label, ok, _why), field) in enumerate(zip(cols, ("frmtrm_amount", "bfefrmtrm_amount")), start=1):
        if terms[k] is not None and terms[k - 1] is not None and terms[k - 1] - terms[k] != 1:
            break                                      # 기수가 잇따르지 않는다 — 이 칸은 앞 칸의 바로 앞 결산기가 아니다(그 뒤 칸도 한 결산기 이상 밀린다)
        if not ok:
            continue
        own = (own_terms or {}).get(label)
        if own is not None and terms[0] is not None and own != terms[0] - k:
            continue                                   # 그 해 보고서의 당기와 기수가 다르다 — 다른 결산기(밀림)
        ni = ctrl_ni(rows, field)[0]
        eq = ctrl_eq(rows, field)[0]
        if ni is not None or eq is not None:
            out.append((label, ni, eq))
    return out


def restated_wins(cand_rcept: str | None, *, own_rcept: str | None = None, restated_rcept: str | None = None, same_value: bool = False,
                  covers: bool = False) -> bool:
    """정정 칸 후보가 칸을 차지하나(순수 함수 — 아래 SQL `_restated_update_sql`·`_restated_upsert_sql` 의 접수번호 조건과 같다).

    후보 = 그 사업연도를 전기·전전기 칸에 적은 **더 늦은 사업보고서**(정정 포함)의 값, `cand_rcept` = 그 보고서 접수번호.
    · 그 해 보고서(정정 포함, `own_rcept`)가 후보보다 늦게 접수됐으면 그 해 보고서가 이긴다 — 후보는 쓰지 않는다.
    · 지금 정정 칸의 출처(`restated_rcept`)보다 이르면 쓰지 않는다 — **같은 값이면(`same_value`) 예외**: 그 값을 더 일찍 낸 보고서가 출처다(261021 DATA-PIT10, `restated_source`).
      단 이른 후보가 지금 정정 칸의 **값 있는 칸을 모두** 적었을 때만(`covers` — `restated_covers`): 출처는 두 칸 모두의 공시일이라, 한 칸만 적은 이른 보고서로 당기면
      그 보고서가 적지 않은 칸의 정정 값(늦은 보고서가 낸 값)을 그 이른 날부터 쓰게 된다(미래 정보).
      같은 보고서를 다시 읽은 것은 쓴다(규칙을 고친 뒤 같은 원문을 다시 읽을 때).
    · 출처를 모르는 정정 칸(이 규칙 전 백필 — 출처 칸이 빔)은 가장 이른 것으로 본다: 그 값은 이 규칙이 들어오기 전에 접수된 보고서에서 왔고,
      이 뒤로 수집이 읽는 보고서(새 사업보고서·최근 정정)는 그보다 늦다. `--backfill-restated --report-year` 로 더 이른 해 보고서를 손으로 다시 돌릴
      때만 예외가 생길 수 있다(그 보고서가 출처 모르는 칸의 실제 출처보다 이르면 덮는다 — 종전 백필과 같다).
    접수번호는 「접수일 YYYYMMDD + 일련번호」 14자리라 글자 순서가 곧 접수 순서다."""
    if not cand_rcept:
        return False
    if own_rcept and cand_rcept <= own_rcept:
        return False
    if restated_rcept and cand_rcept < restated_rcept:
        return bool(same_value and covers)
    return True


def restated_same(cand_ni, cand_eq, cur_ni, cur_eq) -> bool:
    """후보가 지금 정정 칸과 **같은 값**인가(순수 함수 — 아래 SQL 의 `_same_sql` 과 같다). 후보의 값이 있는 칸마다 지금 정정 칸에 값이 있고 `RESTATED_SAME_REL`(상대 1e-6, 1원 미만) 안이어야 한다 —
    후보가 비워 둔 칸은 보지 않는다(쓰면 지금 값이 그대로 남는 칸). 후보에 값이 하나도 없으면 같다고 하지 않는다."""
    if cand_ni is None and cand_eq is None:
        return False
    return all(c is None or (cur is not None and not _differs(c, cur)) for c, cur in ((cand_ni, cur_ni), (cand_eq, cur_eq)))


def restated_covers(cand_ni, cand_eq, cur_ni, cur_eq) -> bool:
    """후보가 지금 정정 칸의 값 있는 칸을 **모두** 적었나(순수 함수 — SQL `_covers_sql` 과 같다). 이른 후보가 출처를 당기려면 필요하다(`restated_wins`) —
    출처 접수번호는 순이익·자본 두 칸 모두의 공시일이다."""
    return (cur_ni is None or cand_ni is not None) and (cur_eq is None or cand_eq is not None)


def restated_source(cand_rcept: str, restated_rcept: str | None, same_value: bool) -> str:
    """후보를 쓴 뒤 정정 칸 출처로 남길 접수번호(순수 함수 — SQL 의 접수번호 식과 같다). 같은 값을 다시 적은 보고서는 출처를 옮기지 않고 **둘 중 이른 쪽**을 남긴다
    (그 값을 처음 낸 보고서 — `restated_pit` 이 그 접수일부터 정정 값을 쓴다). 값이 다르면(재작성) 후보, 출처를 모르던 칸도 후보."""
    return min(restated_rcept, cand_rcept) if (same_value and restated_rcept) else cand_rcept


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


def _same_sql(ni: str, eq: str, cur_ni: str, cur_eq: str) -> str:
    """`restated_same` 의 SQL 식 — 후보 값(`ni`·`eq` 식)이 지금 정정 칸(`cur_ni`·`cur_eq`)과 같은가. 후보가 비운 칸은 보지 않고, 후보에 값이 하나도 없으면 거짓."""
    def one(c: str, cur: str) -> str:
        return f"({c} IS NULL OR ({cur} IS NOT NULL AND abs({c} - {cur}) <= GREATEST(1.0, abs({cur}) * {RESTATED_SAME_REL!r})))"
    return f"(({ni} IS NOT NULL OR {eq} IS NOT NULL) AND {one(ni, cur_ni)} AND {one(eq, cur_eq)})"


def _covers_sql(ni: str, eq: str, cur_ni: str, cur_eq: str) -> str:
    """`restated_covers` 의 SQL 식 — 후보가 지금 정정 칸의 값 있는 칸을 모두 적었나."""
    return f"(({cur_ni} IS NULL OR {ni} IS NOT NULL) AND ({cur_eq} IS NULL OR {eq} IS NOT NULL))"


def _restated_update_sql() -> str:
    """수집 경로: 있는 행만 바꾼다(없는 해에 `restate_only` 행을 만들면 `fetch_plan` 이 그 해 보고서를 받지 않는다). 접수번호 조건은 `restated_wins` 와 같다.
    261021(DATA-PIT10): 후보가 지금 정정 칸과 같은 값이면(`_same_sql`) 값은 그대로 두고 출처는 둘 중 이른 접수번호로 — 더 늦은 보고서가 같은 값을 다시 적어도 출처가 늦어지지 않는다.
    더 이른 보고서가 출처를 당기는 것은 지금 정정 칸의 값 있는 칸을 모두 적었을 때만이다(`_covers_sql` — 한 칸만 적은 보고서로 당기면 다른 칸이 미래 정보가 된다)."""
    own = " AND %(rc)s > COALESCE(rcept_no, '')" if _RCEPT_COL else ""
    same = _same_sql("%(ni)s::float8", "%(eq)s::float8", "ni_restated", "eq_restated")
    covers = _covers_sql("%(ni)s::float8", "%(eq)s::float8", "ni_restated", "eq_restated")    # 이른 후보는 두 칸을 다 적어야 출처를 당긴다
    return ("UPDATE dart_finstat_y SET "
            f"ni_restated=CASE WHEN {same} THEN ni_restated ELSE COALESCE(%(ni)s::float8, ni_restated) END, "
            f"eq_restated=CASE WHEN {same} THEN eq_restated ELSE COALESCE(%(eq)s::float8, eq_restated) END, "
            f"restated_rcept_no=CASE WHEN {same} THEN LEAST(restated_rcept_no, %(rc)s) ELSE %(rc)s END "     # LEAST 는 NULL(출처 모름)을 건너뛴다
            f"WHERE ticker=%(t)s AND fy=%(fy)s AND (%(rc)s >= COALESCE(restated_rcept_no, '') OR ({same} AND {covers}))" + own)


def _restated_upsert_sql() -> str:
    """정정치 백필: 행이 없으면 `restate_only` 로 넣는다(종전 그대로), 있으면 규칙대로(접수번호 조건은 `restated_wins` 와 같다). 같은 값이면 출처는 이른 쪽(`_restated_update_sql`)."""
    own = " AND EXCLUDED.restated_rcept_no > COALESCE(dart_finstat_y.rcept_no, '')" if _RCEPT_COL else ""
    same = _same_sql("EXCLUDED.ni_restated", "EXCLUDED.eq_restated", "dart_finstat_y.ni_restated", "dart_finstat_y.eq_restated")
    covers = _covers_sql("EXCLUDED.ni_restated", "EXCLUDED.eq_restated", "dart_finstat_y.ni_restated", "dart_finstat_y.eq_restated")
    return ("INSERT INTO dart_finstat_y (ticker,fy,fs,ni,eq,ni_restated,eq_restated,fetched,restated_rcept_no) "
            "VALUES (%(t)s,%(fy)s,%(fs)s,NULL,NULL,%(ni)s,%(eq)s,'restate_only',%(rc)s) ON CONFLICT (ticker,fy) DO UPDATE SET "
            f"ni_restated=CASE WHEN {same} THEN dart_finstat_y.ni_restated ELSE COALESCE(EXCLUDED.ni_restated, dart_finstat_y.ni_restated) END, "
            f"eq_restated=CASE WHEN {same} THEN dart_finstat_y.eq_restated ELSE COALESCE(EXCLUDED.eq_restated, dart_finstat_y.eq_restated) END, "
            f"restated_rcept_no=CASE WHEN {same} THEN LEAST(dart_finstat_y.restated_rcept_no, EXCLUDED.restated_rcept_no) "
            "ELSE EXCLUDED.restated_rcept_no END "
            f"WHERE (EXCLUDED.restated_rcept_no >= COALESCE(dart_finstat_y.restated_rcept_no, '') OR ({same} AND {covers}))" + own)


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


def restated_basis_ok(row_fs: str | None, src_fs: str | None, row=None) -> bool:
    """정정 칸 후보를 낸 보고서의 재무제표(연결 CFS/별도 OFS)가 그 해 행과 같은 기준인가(순수 함수).
    다른 기준이면 같은 사업연도의 **다른 재무제표**이지 재작성이 아니다 — 쓰지 않는다. 10-20 검토: 정정 칸 운영 쓰기 267행 가운데 48행이 연결 행에
    별도 보고서 값(종속회사를 처분해 별도만 내게 된 회사 — 154040 FY2021 지배 순이익 연결 58.47억 → 별도 1.6억)이나 별도 행에 연결 보고서 값
    (스팩 역합병 — 284620 FY2018 스팩 0.39억 → 합병 뒤 회사 −80.28억)이 들어갔다. 그 해 행에 값이 하나도 없으면(`row` 의 그 해 순이익·자본이 모두
    빔) 후보가 유일한 값이라 채운다. 기준을 모르면(칸이 빔) 종전처럼 쓴다."""
    if not row_fs or not src_fs or row_fs == src_fs:
        return True
    return row is not None and row[0] is None and row[1] is None


def unit_mismatch(a, b) -> bool:
    """같은 사업연도의 같은 숫자인데 10ⁿ 배(|n|≥2) 다른가 — 보고서 사이 단위 오류 꼴(`scale_guard.check_prior_period_mismatch`, 서버 실시간 firm 의
    보고서 사이 단위 가리기와 같은 판정). 이 꼴이면 어느 쪽이 맞는지 이 자리에선 모르므로 정정 칸을 바꾸지 않고 경고만 남긴다."""
    from open_proxy_mcp.services.scale_guard import check_prior_period_mismatch
    return bool(a) and bool(b) and (a > 0) == (b > 0) and check_prior_period_mismatch(a, b).get("triggered", False)


#: 보고서 사이 단위 대조에서 「지금 읽은 보고서」의 이름(저장된 이웃 값은 접수번호 등 다른 이름).
_XR_NEW = "NEW"


#: 보고서의 당기 자본이 바로 앞 해 저장 값과 이 배수 안쪽이면 「이어진다」 — 통째 10ⁿ 배(100배 이상) 보고서는 당기 값도 앞 해와 이어지지 않는다.
_CONTINUITY_FACTOR = 20.0


def cross_report_plan(new_eq: dict, stored: list, *, mktcap_fn=None, current_fy: int | None = None) -> dict:
    """261021(DATA-SC4) 지금 읽은 사업보고서가 저장된 이웃 해 값과 **다른 단위**인가 — 순수 함수(시험 대상).

    `new_eq` = {사업연도 표기: 새 보고서의 그 날짜 자본(원)} — 당기·전기·전전기 칸. `stored` = [(보고서 이름, 사업연도 표기, 자본(원))] — 저장된 이웃 해의 원본 칸(그 해 보고서의
    접수번호)·정정 칸(출처 접수번호)이 같은 날짜에 대해 낸 값. 같은 날짜의 같은 숫자를 다른 보고서와 대는 `scale_guard.reconcile_report_scales` 로 가린다
    (서버 실시간 firm·`financial_metrics` 와 같은 함수 — 10ⁿ 배(|n|≥2, ±20%)이면 단위 차, 다수결과 시총(PBR) 두 신호로 어느 쪽이 틀렸는지).
    **새 보고서와 짝 변으로 이어진 보고서들만** 본다 — 새 보고서와 상관없는 옛 해의 저장 칸끼리 어긋나 있어도 이번 판정을 막지 않는다.
    `mktcap_fn`(없어도 됨) = 어긋남이 있을 때만 부르는 시총 조회(둘째 신호 — 이웃 보고서가 하나뿐이면 다수결이 안 선다, 0091W0 꼴).

    반환 {"status", "divisor", "method", "off", "pairs"}:
      · clean — 새 보고서에 어긋난 짝이 없다(대 볼 저장 값이 없어도 clean).
      · fixed — 새 보고서가 틀렸다. `divisor` 로 금액 칸을 나눠 쓴다(`rescale_rows`).
      · stored_off — 새 보고서가 맞고 저장 칸 일부(`off` — 보고서 이름)가 틀렸다. 새 보고서는 그대로.
      · prior_only — 전기·전전기 칸만 저장 값과 어긋나고 당기 값(`current_fy`)은 바로 앞 해 저장 값과 이어진다(20배 안쪽). 「보고서가 통째로 10ⁿ 배」라는 가정이 서지 않아
        당기 값을 나누지도 막지도 않고 그대로 쓴다(전기·전전기 칸은 정정 칸 쓰기의 단위 꼴 검사가 따로 막는다).
      · undecided / conflict — 새 보고서와 저장 칸이 어긋나는데 어느 쪽이 맞는지 못 가린다(conflict = 서로 앞뒤가 안 맞는 고리). 새 보고서는 쓰지 않는다."""
    base = {"status": "clean", "divisor": 1, "method": None, "off": [], "pairs": []}
    obs = [(_XR_NEW, ("FYE", int(fy), "KRW"), float(v), int(fy)) for fy, v in new_eq.items() if v]
    if not obs:
        return base
    obs += [(lab, ("FYE", int(fy), "KRW"), float(v), int(fy)) for lab, fy, v in stored if v and lab != _XR_NEW]
    first = reconcile_report_scales(obs)
    if not any(p["power"] for p in first["pairs"] if _XR_NEW in (p["a"], p["b"])):
        return base                            # 새 보고서는 만난 모든 저장 값과 같은 단위(2배 안쪽)이거나 만난 값이 없다
    if current_fy is not None and new_eq.get(current_fy) and not any(
            p["power"] and p["key"][1] == current_fy for p in first["pairs"] if _XR_NEW in (p["a"], p["b"])):
        # 어긋남이 전기·전전기 칸에만 있다 — 당기 값이 바로 앞 해 저장 값과 이어지면(단위 오류 보고서는 당기도 앞 해와 이어지지 않는다) 통째 오류가 아니다
        earlier = [fy for _lab, fy, v in stored if v and current_fy - 2 <= fy < current_fy]
        if earlier:
            cur = float(new_eq[current_fy])
            near = [float(v) for _lab, fy, v in stored if v and fy == max(earlier)]
            if any((v > 0) == (cur > 0) and 1 / _CONTINUITY_FACTOR <= abs(cur / v) <= _CONTINUITY_FACTOR for v in near):
                return {**base, "status": "prior_only", "pairs": [p for p in first["pairs"] if p["power"]]}
    comp = {_XR_NEW}
    grew = True
    while grew:
        grew = False
        for p in first["pairs"]:
            if (p["a"] in comp) != (p["b"] in comp):
                comp |= {p["a"], p["b"]}
                grew = True
    mk = None
    if mktcap_fn is not None:
        try:
            mk = mktcap_fn()
        except Exception:  # noqa: BLE001 — 시총을 못 읽으면 다수결만으로(없는 신호는 쓰지 않는다)
            mk = None
    res = reconcile_report_scales([o for o in obs if o[0] in comp], mktcap=mk)
    out = {**base, "method": res.get("method"), "pairs": [p for p in res.get("pairs") or [] if p["power"]]}
    if res["status"] == "fixed":
        d = (res.get("divisors") or {}).get(_XR_NEW)
        if d:
            return {**out, "status": "fixed", "divisor": d}
        return {**out, "status": "stored_off", "off": sorted(k for k in (res.get("divisors") or {}) if k != _XR_NEW)}
    if res["status"] == "clean":              # 이론상 닿지 않는다(어긋난 짝이 있으면 덩어리가 clean 일 수 없다) — 막는 쪽으로
        return {**out, "status": "undecided"}
    return {**out, "status": res["status"]}


def _mktcap_latest(ticker: str):
    """그 종목의 가장 새 시가총액(원, `krx_weekly` 뷰의 최신 행) — 단위 가리기의 둘째 신호(PBR 가 10^±1 안쪽인 단위). 읽을 수 없으면(권한·표 없음·행 없음) None —
    그러면 다수결만으로 가린다. 어긋남이 있을 때만 부른다(드문 일 — 종목당 1행 읽기)."""
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as c:
            r = c.execute("SELECT mktcap FROM krx_weekly WHERE ticker=%s AND mktcap > 0 ORDER BY price_dd DESC LIMIT 1", (ticker,)).fetchone()
        return float(r[0]) if r and r[0] else None
    except Exception:  # noqa: BLE001
        return None


async def _annual_cross_guard(isu: str, yr: int, rows: list, fye, stmt_cur: str, terms, eqsnap: dict, own_rc: str | None) -> tuple[list, dict | None, list]:
    """261021(DATA-SC4) 지금 읽은 사업보고서(표기 `yr`)의 자본을 저장된 이웃 해 값과 대 → (보고서 rows — 틀렸으면 금액 칸을 나눈 사본, 판정 `cross_report_plan` 결과, 비교한 저장 칸).

    새 보고서의 값: 당기 말(`ctrl_eq` — 그 해 칸과 같은 지배자본 규칙) + 전기·전전기 말(`prior_candidates` — 결산월·기수 확인을 거친 칸). 외화 보고사는 저장과 같이 그 해
    결산월 말 환율로 원화로 바꿔 댄다(안 바꾸면 비율이 1/환율 ≈ 10⁻³ 이라 단위 오류로 오판 — 분기 수집의 글로벌에스엠 꼴). 환율을 못 구한 해는 대지 않는다.
    저장 값(`eqsnap[(종목, 해)]` = (원본 자본, 원본 접수번호, 정정 자본, 정정 출처 접수번호)): 이웃 해의 원본 칸 · 정정 칸. 보고서 이름은 접수번호(몰라도 「칸 종류:해」 —
    따로 센다). **지금 읽는 그 해 원본 칸은 빼고**(곧 바꿀 칸 — 정정 보고서라면 옛 판이라 대 볼 이유가 없다), 지금 보고서·그 행의 옛 판 접수번호에서 온 정정 칸도 뺀다."""
    new_eq: dict = {}
    v0, _ = ctrl_eq(rows)
    if v0 is not None:
        new_eq[yr] = v0
    for fy_off, _cni, ceq in prior_candidates(rows, yr, fye, terms):
        if ceq is not None:
            new_eq[fy_off] = ceq
    if stmt_cur != "KRW":
        conv = {}
        for fy, v in new_eq.items():
            rate = await fx_to_krw(stmt_cur, fiscal_year_end_date(fy, rcal.month_of(fy, fye)))
            if rate is not None:
                conv[fy] = v * rate
        new_eq = conv
    old = eqsnap.get((isu, yr))
    drop = {x for x in (own_rc, old[1] if old else None) if x}
    stored = []
    for fy in new_eq:
        e = eqsnap.get((isu, fy))
        if e is None:
            continue
        eq_o, rc_o, eq_r, rc_r = e
        if fy != yr and eq_o:
            stored.append((rc_o or f"원본:{fy}", fy, eq_o))
        if eq_r:
            stored.append((rc_r or f"정정:{fy}", fy, eq_r))
    stored = [x for x in stored if x[0] not in drop]
    plan = cross_report_plan(new_eq, stored, mktcap_fn=lambda: _mktcap_latest(isu), current_fy=yr)
    if plan["status"] == "fixed":
        rows = rescale_rows(rows, plan["divisor"])
    return rows, plan, stored


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
    반기·분기 결산 회사(결산기 칸 — 정정 칸이 결산기 12개월 합)는 둘 다 하지 않는다. 같은 숫자가 10ⁿ 배 다르면(보고서 사이 단위 오류 꼴) 바꾸지 않고 경고.
    그 보고서의 재무제표 기준(연결/별도)이 앞 해 행과 다르면 그 행의 정정 칸에 쓰지 않는다(`restated_basis_ok` — 다른 재무제표지 재작성이 아니다)."""
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
    _yrows=con.execute("SELECT ticker, fy, ni, eq, ni_restated, eq_restated, fs FROM dart_finstat_y").fetchall() if _RESTATED_COL else []
    ysnap={(r[0],int(r[1])): (r[2],r[3],r[4],r[5]) for r in _yrows}
    yfs={(r[0],int(r[1])): r[6] for r in _yrows}       # 261020 검토: 그 해 행의 재무제표 기준(연결/별도) — 다른 기준 보고서 값은 정정 칸에 넣지 않는다
    ptk={r[0] for r in con.execute("SELECT DISTINCT ticker FROM dart_finstat_q WHERE restate_why LIKE %s",(pcells.MARK_LIKE,))} \
        if _RESTATED_COL and (con.execute("SELECT to_regclass('public.dart_finstat_q')").fetchone() or [None])[0] else set()
    rops=[]; rstat={"cand":0,"skip_unit":0,"clear":0,"skip_fs":0}
    terms_of: dict = {}     # 261021(F10): 이 실행에서 읽은 사업보고서의 당기 기수 {종목: {표기: 기수}} — 전기 칸의 기수 대조
    # 시장 최대 순이익 앵커(scale_guard.MARKET_MAX_NI_ANCHOR)는 이 수집에서 쓰지 않는다 — 아래 `scale_assess` 는 market_max 없이 자릿수 상한(16자리)만 본다.
    # (종전 `market_max_ni` 변수는 값을 넣기만 하고 호출에 넘기지 않았다. 고정 앵커는 새 최대 실적에서 먼저 울린다 — DATA-SC5. dart_finstat_y 최댓값으로 동적 확장하는 것도
    #  이 표에 남은 소프트센 오염값(1.07×10^16)이 앵커가 되어 가드가 무력화되므로 하지 않는다.)
    # 261021(DATA-SC4): 보고서 사이 단위 대조용 — 저장된 자본 {(종목, 해): (원본 자본, 원본 접수번호, 정정 자본, 정정 출처 접수번호)}. 칸이 없으면(이행 전) 접수번호는 모른다(None).
    eqsnap={(r[0],int(r[1])): (r[2],r[4],r[3],r[5]) for r in con.execute(
        "SELECT ticker, fy, eq, eq_restated, " + ("rcept_no" if _RCEPT_COL else "NULL") + ", "
        + ("restated_rcept_no" if _RESTATED_COL else "NULL") + " FROM dart_finstat_y")}
    xr_stat={"fixed":0,"blocked":0,"stored_off":0,"prior_only":0}
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
            xr_block=False
            if rows:
                # 261021(DATA-SC4): 이 보고서의 자본(당기·전기·전전기 말)을 저장된 이웃 해 값과 대 단위를 가린다 — 보고서가 통째로 10ⁿ 배면 아래 보고서 안 검사는 다 맞는다.
                rows,xp,xr_stored=await _annual_cross_guard(isu,yr,rows,fye_of.get(isu,12),statement_currency(rows),terms_of.get(isu),eqsnap,pcells.rows_rcept(rows))
                if xp["status"]=="fixed":
                    xr_stat["fixed"]+=1
                    _d=xp["divisor"]; _how=f"÷{_d:,.0f}" if _d>=1 else f"×{round(1/_d):,}"
                    print(f"::warning::[단위] {isu} FY{yr} 사업보고서(접수 {pcells.rows_rcept(rows) or '?'})의 자본이 저장된 앞뒤 해 칸과 10의 거듭제곱 배로 어긋난다"
                          f"({len(xp['pairs'])}곳) — 이 보고서의 금액 칸을 {_how} 해 쓴다(주당 금액은 그대로, 가린 근거: {xp['method']})",flush=True)
                elif xp["status"]=="prior_only":
                    xr_stat["prior_only"]+=1
                    print(f"::warning::[단위] {isu} FY{yr} 사업보고서의 전기·전전기 칸만 저장된 앞 해 칸과 10의 거듭제곱 배로 어긋나고 당기 자본은 저장된 앞 해와 이어진다 — "
                          f"보고서가 통째로 틀린 꼴이 아니라 당기 값은 그대로 쓴다(전기·전전기 칸은 정정 칸 후보에서 막는다)",flush=True)
                elif xp["status"]=="stored_off":
                    xr_stat["stored_off"]+=1
                    _bad=sorted({f"FY{fy}" for lab,fy,_v in xr_stored if lab in xp["off"]})
                    print(f"::warning::[단위] {isu} FY{yr} 사업보고서는 저장된 다른 해 칸들과 단위가 맞는데 저장된 {'·'.join(_bad)} 칸이 10의 거듭제곱 배로 어긋난다 — "
                          f"이 보고서는 그대로 쓴다(저장 칸 점검 필요 — 이 수집은 저장 칸을 고치지 않는다)",flush=True)
                elif xp["status"] in ("undecided","conflict"):
                    xr_stat["blocked"]+=1
                    xr_block=True
                    print(f"::warning::[단위] {isu} FY{yr} 사업보고서(접수 {pcells.rows_rcept(rows) or '?'})의 자본이 저장된 앞뒤 해 칸과 10의 거듭제곱 배로 어긋나는데 "
                          f"어느 쪽이 맞는지 못 가려({xp['status']}) 이 보고서 값은 쓰지 않는다 — 있던 ok 행은 그대로, 새 행은 자료 없음(다음 보고서가 나오면 다시 본다)",flush=True)
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
                _fx_dd = fiscal_year_end_date(yr, rcal.month_of(yr, fye_of.get(isu, 12)))
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
            if xr_block:
                verdict={"tier":"hard","hard_hit":["cross_report_scale"]}   # 261021(DATA-SC4): 보고서 사이 대조에서 못 가린 어긋남 — 보고서 안 hard 와 같이 비운다
            if verdict["tier"]=="hard":
                print(f"[가드] {isu} FY{yr} 스케일오류 감지({verdict['hard_hit']}) — ni/eq 무효화",flush=True)
                ni=eq=None
            if ni is not None and rcal.is_transition(yr, fye_of.get(isu, 12)):
                # 261021(DATA-FY2): 결산월을 바꾼 첫 해의 사업보고서는 전환기(12개월이 아님) — 순이익은 연간 칸에 넣지 않는다(자본은 그 날 잔액이라 둔다)
                print(f"[전환기] {isu} FY{yr} 결산월을 바꾼 첫 사업보고서 — 순이익 {ni:.0f}원은 12개월이 아니라 연간 칸에 넣지 않는다(자본만)",flush=True)
                ni=None
            st="ok" if (ni is not None or eq is not None) else "nodata"
            rc_own=pcells.rows_rcept(rows) if rows else None
            if rows:
                _t0=pcells.term_no(rows,"thstrm_nm")
                if _t0 is not None: terms_of.setdefault(isu,{})[yr]=_t0
            buf.append((isu,yr,fs,ni,eq,None,None,st,rc_own))   # 261020: 접수번호(정정 판정 기준)
            if st=="ok":      # 261021: 같은 실행의 다음 해 보고서가 이 값을 이웃 해로 대 보게(정정 칸·그 해 정정 칸 출처는 그대로)
                _o=eqsnap.get((isu,yr)); eqsnap[(isu,yr)]=(eq,rc_own,_o[2] if _o else None,_o[3] if _o else None)
            if _RESTATED_COL and st=="ok":
                ysnap.setdefault((isu,yr),(ni,eq,None,None))   # 같은 실행에서 새로 넣은 행도 다음 해 보고서의 정정 칸 후보를 받게
                yfs.setdefault((isu,yr),fs)
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
                for fy_off,cni,ceq in prior_candidates(rows,yr,fye_of.get(isu,12),terms_of.get(isu)):
                    if stmt_cur!="KRW":
                        _r=await fx_to_krw(stmt_cur, fiscal_year_end_date(fy_off, rcal.month_of(fy_off, fye_of.get(isu, 12))))
                        if _r is None:
                            continue
                        cni,ceq=(v*_r if v is not None else None for v in (cni,ceq))
                    row=ysnap.get((isu,fy_off))
                    if row is None or not restated_worth(cni,ceq,row):
                        continue                       # 행이 없거나(그 해는 정규 수집 몫) 재작성 없음
                    if not restated_basis_ok(yfs.get((isu,fy_off)), fs, row):
                        rstat["skip_fs"]+=1
                        print(f"[정정 칸] {isu} FY{fy_off} {yr} 사업보고서({fs})가 그 해 행({yfs.get((isu,fy_off))})과 재무제표 기준이 달라 쓰지 않는다",flush=True)
                        continue
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
    if any(xr_stat.values()):
        print(f"보고서 사이 단위 대조 — 금액 칸을 나눠 씀 {xr_stat['fixed']} · 못 가려 쓰지 않음 {xr_stat['blocked']} · 저장 칸이 어긋남(알림만) {xr_stat['stored_off']} · 전기 칸만 어긋남(알림만) {xr_stat['prior_only']}",flush=True)
    print("fetch 종료" + (f" — 정정 칸 후보 {rstat['cand']}(규칙에 맞을 때만 씀) · 정정 보고서로 비움 {rstat['clear']} · 단위 꼴로 건너뜀 {rstat['skip_unit']} · 연결/별도 기준 달라 건너뜀 {rstat['skip_fs']}"
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
    t = today or today_kst()
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
    # 261020(p1f): 출처 칸이 있으면 「그 보고서(표기 report_year — 그 다음 해에 접수) 이상에서 온 정정 칸이 이미 있는 종목」만 처리됐다고 본다.
    #   종전 판정(두 해 중 하나라도 정정 칸)은 2024 보고서로 채운 2023 정정 칸 때문에 2025 보고서 백필(--report-year 2025)이 거의 모든 종목을 건너뛰게 했다.
    #   재작성이 없는 종목은 정정 칸을 쓰지 않으므로 다시 돌리면 다시 받는다(끊긴 실행 이어 돌리기는 --tickers 로 좁힌다).
    if tickers:
        done=set()
    elif _RESTATED_COL:
        done={r[0] for r in con.execute(
            "SELECT ticker FROM dart_finstat_y WHERE fy=%s AND restated_rcept_no >= %s", (report_year-1, str(report_year+1)))}
    else:
        done={r[0] for r in con.execute(
            "SELECT ticker FROM dart_finstat_y WHERE fy IN (%s,%s) "
            "AND (ni_restated IS NOT NULL OR eq_restated IS NOT NULL)", (report_year-2, report_year-1))}
    fye_of=rcal.load_fye_map(con)
    # 261020(p1e): 반기·분기 결산 회사(결산기 칸 종목)는 건너뛴다 — 그 회사의 전기·전전기 칸은 앞 **결산기**(6·3개월 전)라 앞 사업연도 행에 넣으면
    #   틀린다(10-20 운영 롯데리츠 FY2022·2023 정정 칸이 그렇게 들어가 있었다). 연간 값은 --period-cells 가 결산기 12개월 합으로 쓴다.
    _ptk={r[0] for r in con.execute("SELECT DISTINCT ticker FROM dart_finstat_q WHERE restate_why LIKE %s",(pcells.MARK_LIKE,))}
    # 261020(p1f): 출처 칸이 있으면 수집과 같은 규칙(가장 늦게 접수된 보고서 우선 · 출처 접수번호 · 단위 꼴이면 건너뜀)으로 쓴다
    _yrows=con.execute("SELECT ticker, fy, ni, eq, ni_restated, eq_restated, fs FROM dart_finstat_y").fetchall() if _RESTATED_COL else []
    ysnap={(r[0],int(r[1])): (r[2],r[3],r[4],r[5]) for r in _yrows}
    yfs={(r[0],int(r[1])): r[6] for r in _yrows}       # 261020 검토: 수집과 같은 연결/별도 기준 검사
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
            # 261021(DATA-FY2 F10): 전기·전전기 칸의 표기는 결산월로 가른다(결산월을 바꾼 회사는 report_year−1·−2 가 아니다) — 기수로도 확인(`prior_candidates`)
            _ok_off={fy_off for fy_off,_n,_e in prior_candidates(rows,report_year,fye_of.get(isu,12))}
            _cols=rcal.prior_columns(report_year,fye_of.get(isu,12))
            for (fy_off,_ok,_why),field in zip(_cols,("frmtrm_amount","bfefrmtrm_amount")):
                if not _ok or fy_off not in _ok_off:
                    if _why: print(f"[정정 칸] {isu} {report_year} 사업보고서의 {'전기' if field.startswith('frmtrm') else '전전기'} 칸은 FY{fy_off} 칸의 자리가 아니다({'전환기' if _why=='transition' else '그 표기 칸은 전환기 보고서가 차지'}) — 쓰지 않는다",flush=True)
                    continue
                ni_r,ni_note=ctrl_ni(rows,field)     # 261020: 정정치도 같은 규칙
                if ni_note:
                    print(f"[순이익] {isu} FY{fy_off} 정정 {ni_note}",flush=True)
                eq_r,eq_note=ctrl_eq(rows,field)       # 261019: 납입자본 줄 태깅 오류는 정정치에도 있었다(065680 FY2022·23)
                if eq_note:
                    print(f"[자본] {isu} FY{fy_off} 정정 {eq_note}",flush=True)
                if stmt_cur!="KRW" and (ni_r is not None or eq_r is not None):
                    # 261019: 저장은 항상 원화(fetch 와 같은 규칙 — 그 해 결산월 말일 환율). 환율을 못 구하면 그 해는 쓰지 않는다.
                    _fx_dd=fiscal_year_end_date(fy_off, rcal.month_of(fy_off, fye_of.get(isu, 12)))
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
                    if not restated_basis_ok(yfs.get((isu,fy_off)), fs, row):
                        print(f"[정정 칸] {isu} FY{fy_off} {report_year} 사업보고서({fs})가 그 해 행({yfs.get((isu,fy_off))})과 재무제표 기준이 달라 쓰지 않는다",flush=True)
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
    # 261020(p1g pit-restated): 단, 분기말 d 에는 그 출처 보고서가 공시된 정정 칸만(`restated_pit` — 서버 곡선·두 과거 백필과 같은 규칙). 원본이 빈
    #   보충값·10ⁿ 배 단위 고침(위 032680)은 처음부터. 이 진단은 12월 결산 달력(아래 pit_fy)이라 문도 12월 결산으로 본다.
    fin={}
    gates={}
    restated_used=[]
    _rc_col = "restated_rcept_no" if con.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='dart_finstat_y' "
        "AND column_name='restated_rcept_no')").fetchone()[0] else "NULL"
    for isu,fy,ni,eq,ni_r,eq_r,r_rc in con.execute(
            f"SELECT ticker,fy,ni,eq,ni_restated,eq_restated,{_rc_col} FROM dart_finstat_y WHERE fetched='ok' OR ni_restated IS NOT NULL OR eq_restated IS NOT NULL"):
        (final_ni, final_eq), g = rpit.annual_cell(int(fy), ni, eq, ni_r, eq_r, r_rc)
        if ni_r is not None and ni is not None and abs(ni_r - ni) > abs(ni) * 0.01:
            restated_used.append((isu, fy, ni, ni_r))
        fin[(isu,fy)]=(final_ni, final_eq)
        if g is not None:
            gates[(isu,fy)]=g
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
            g=gates.get((isu,pit_fy))
            if g and g[0]>d: f=g[1]          # 261020(p1g): 출처 공시 전 — 그때 공시돼 있던 원본 칸
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
