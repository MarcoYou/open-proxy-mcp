"""분기 재무 시계열 저장소(dart_finstat_q) — firm/market/sector 밸류 밴드의 분기 granularity 원천.

설계(260705, 사용자 확정): "주간 가격 × **분기 재무** × 분기말 환율". 분기 재무가 있어야 과거
TTM PER(최근 4분기 지배순이익 합)·MRQ PBR(최근분기 지배자본)을 시계열로 산출 — 연간(dart_finstat_y)만
있어서 밴드 TTM이 N/A였던 한계를 해소.

키: (ticker, fy, quarter). quarter 1/2/3/4(=사업보고서). 저장:
  ni_cum = 지배순이익 **누적(YTD)** — TTM = FY(y-1) + ni_cum(y,q) − ni_cum(y-1,q).
  eq     = 지배자본 **기말 잔액**(BS, 기간무관).

수집 절약: **Q4(사업보고서)는 이미 있는 연간 데이터에서 seed(DART 0콜)** —
  · dart_finstat_y(ni=연간누적=Q4누적, eq=FY말자본, restated 우선) — 연간 표가 정본(261018: 재무 요약에서 넣던 bootstrap 은 뺐다)
  나머지 Q1(11013)·반기(11012)·3Q(11014)만 DART 수집.

추출: financial_metrics._extract_cumulative_is 규칙 재사용 — 분기/반기 손익은 thstrm_add_amount(누적),
1Q·사업보고서는 thstrm(=누적). BS는 thstrm(잔액). 지배주주 귀속 account_id. 스케일가드(소프트센).

PIT는 **읽기 시점**에 표준 공시지연으로 매핑(저장 X): 1Q→5/15·반기→8/14·3Q→11/14·사업보고서→익년 4/1(12월 결산).
261018: 칸의 실제 기간 말·공시 시점은 회사 결산월(`dart_fundamentals.acc_mt`)로 센다 — `open_proxy_mcp/services/report_calendar.py`.
  수집 마감(`_disclosed`)·nodata 재확인 기간·외화 환율 기준일·전기 칸 대조 연도·파생(--derive)의 「최신 분기」와 TTM 직전 연간이 모두 그 매핑을 쓴다.
  12월 결산(결산월 칸이 비었을 때 포함)은 종전과 같다.

Rate limit(하드룰 준수): 동시성 1 · 0.45s sleep(≈133콜/분, 910 안전) · ReadError 즉시 중단(resume) ·
25건마다 flush · 최종 flush는 try로 감싸 blip에도 버퍼 손실 최소화.

재개(resume) 규칙(261018): ok 는 완료, err 는 다음 실행에 다시 받는다. **nodata 는 그 분기의 공시 마감 뒤
`NODATA_RETRY_DAYS`(400)일 동안 매 실행 다시 받는다** — 종전엔 nodata 도 완료로 봐서, 마감 직후 첫 실행 때
아직 안 낸 회사가 그 뒤에 내도 영영 비어 있었다(늦게 내는 외국 상장사·제출 지연, 결산월이 12월이 아니라 그
(사업연도, 보고서) 칸의 보고서가 실행 시점엔 아직 없던 회사). 400일 = 1년 뒤 같은 분기를 받을 때도 전년 동분기
(TTM 에 필요)를 한 번 더 본다.

칸의 뜻: (fy, quarter) = DART 의 (bsns_year, reprt_code) 그대로다. DART 는 사업연도를 **그 보고서 기준일의 달력
연도**로 붙인다 — 12월 결산이 아니면 같은 fy 안의 네 칸이 서로 다른 회계연도·달력 분기를 가리킨다(3월 결산:
quarter 4 = 그해 3월말 사업보고서, 1 = 6월말, 2 = 9월말, 3 = 12월말). 이 표는 받은 그대로 저장한다.

실행:
  python3 scripts/market_fund_quarterly.py --seed              # Q4 seed(0콜)만
  python3 scripts/market_fund_quarterly.py --pilot 005930,000660,051910  # 소표본 전분기(검증용)
  python3 scripts/market_fund_quarterly.py --fetch             # 전 종목 Q1~Q3 백필(재개 가능, ~1.5h)
  python3 scripts/market_fund_quarterly.py --fetch --years 2020,2021,2022,2023,2024,2025
  python3 scripts/market_fund_quarterly.py --acc-mt [--acc-mt-since 20180101]
      # 결산월(dart_fundamentals.acc_mt)을 사업보고서 이름의 기준월로 채운다·고친다(기본 최근 100일 목록, 처음엔 2018년부터)
  python3 scripts/market_fund_quarterly.py --prior-from-next 105560:2023:1,105560:2023:2 [--dry]
      # 261019: API 에 보고서가 없는 nodata 칸(금융업 2023 반기까지 등)을 다음 해 같은 분기 보고서의 전년 동기 값으로 정정 칸에(아래 「전기 칸 보충」)
  python3 scripts/market_fund_quarterly.py --include-new [--fetch]
      # 신규 상장 보통주를 dart_fundamentals 에 등록(KRX 2~4콜 + DART 0콜). 옛 시장 aggregate 수집기(--fetch)의
      # 유일한 잔존 기능(260902 흡수·원본은 open-proxy-storage archive/opm-scripts). 재무 4열은 넣지 않는다 —
      # 이후 --fetch(분기)·market_val_series --fetch
      # (연간)·--derive 가 채운다. --fetch 와 같이 주면 등록 직후 그 종목의 분기 수집까지 이어진다.
"""
import argparse, asyncio, os, re, sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
import psycopg
from open_proxy_mcp.services.scale_guard import gid_exact, assess as scale_assess
from open_proxy_mcp.services.financial_metrics import normalize_amount
from open_proxy_mcp.dart.fx import SRC_YAHOO, fx_source, statement_currency, fx_to_krw
from open_proxy_mcp.services import report_calendar as rcal

# 분기 수집연도: 2019~현재. 2018 분기는 2019 TTM에만 필요(→ 2020~ 추이엔 불필요)해 제외 —
# 2018은 seed_q4가 mkt_finstat_y에서 Q4(연간)만 seed. TTM은 2019 동분기가 있어 2020~부터 산출.
YEARS_DEFAULT = [2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
# (quarter, reprt_code) — Q1/반기/3Q만 DART 수집(Q4는 seed)
QFETCH = [(1, "11013"), (2, "11012"), (3, "11014")]
# 분기 공시 마감(그 이후 available): 1Q 5/15 · 반기 8/14 · 3Q 11/14. 아직 공시 전 분기는 수집 제외
# (미래 분기 [013] nodata 낭비 방지 + 현재연도 자동 대응). 실행 시점(date.today) 기준.
_Q_DEADLINE = {1: (5, 15), 2: (8, 14), 3: (11, 14)}


def _disclosed(fy: int, q: int, today: date | None = None, fye: int = 12) -> bool:
    """그 칸의 공시 마감이 지났는가. 261018(결산월): 마감은 칸의 실제 기간 말 + 45일(`report_calendar.cell_available`) —
    12월 결산이면 종전 5/15·8/14·11/14 그대로. 6월 결산의 「3분기」 칸(3월 말)은 5/15 부터 받는다(종전엔 달력 3분기라 11/14 까지 미뤘다)."""
    if fye == 12:
        mo, dy = _Q_DEADLINE[q]
        return (today or date.today()) >= date(fy, mo, dy)
    return (today or date.today()) >= rcal.cell_available(fy, q, fye)


#: nodata 분기를 다시 받는 기간 — 그 분기 공시 마감일부터 이 날수까지(261018, 위 「재개 규칙」).
#: 10-18 실측: 2026 반기 칸 nodata 63종목 중 24종목은 마감 뒤(8/24~10/07) 반기보고서를 냈는데도 비어 있었다.
NODATA_RETRY_DAYS = int(os.getenv("FUND_Q_NODATA_RETRY_DAYS", "400"))


def _retry_nodata(fy: int, q: int, today: date | None = None, days: int | None = None, fye: int = 12) -> bool:
    """이 nodata 분기를 이번 실행에 다시 받는가 — 공시 마감이 지났고, 지난 지 `days`일 이하. 마감은 결산월로(`_disclosed` 와 같다)."""
    t = today or date.today()
    if fye == 12:
        mo, dy = _Q_DEADLINE[q]
        since = (t - date(fy, mo, dy)).days
    else:
        since = (t - rcal.cell_available(fy, q, fye)).days
    return 0 <= since <= (NODATA_RETRY_DAYS if days is None else days)


def _latest_annual_fy(today: date | None = None) -> int:
    """그 시점 확정된 최신 사업연도(사업보고서 3월 공시 → 4월 이후 전년, 아니면 전전년). _pit_fy와 동일."""
    t = today or date.today()
    return t.year - 1 if t.month >= 4 else t.year - 2
Q_TO_RC = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
# 콜 간 sleep(초) — env로 조정. DART client _throttle_api가 910/분을 별도 강제하므로 이건 예의용.
SLEEP = float(os.getenv("FUND_Q_SLEEP", "0.45"))

# 지배주주 귀속 추출(260705 QA 후 정정): account_id **substring**으로 net income 귀속만 —
# "ProfitLossAttributableToOwnersOfParent"는 총포괄 "ComprehensiveIncomeAttributable…"와 문자열이
# 구분되므로 substring이 안전(dart_* 접두 변형도 포착). account_id가 아예 없는(-표준계정코드 미사용-)
# 종목(LG그룹 등 다수)은 account_nm 폴백 — 단 **IS/BS만**(CIS 총포괄 제외, 값이 달라 오염). 총계
# (ifrs-full_ProfitLoss·Equity) 폴백은 **금지**(비지배 포함 총계를 지배로 오저장 — QA 실측 LG화학).
_NI_CTRL_ID = "ProfitLossAttributableToOwnersOfParent"
_EQ_CTRL_ID = "EquityAttributableToOwnersOfParent"


def _cum_is(r):
    """IS 누적: thstrm_add(당기누적) 우선, 없으면 thstrm(1Q·사업보고서는 이게 누적)."""
    v = normalize_amount(r.get("thstrm_add_amount"))
    return float(v) if v is not None else (
        float(normalize_amount(r.get("thstrm_amount"))) if normalize_amount(r.get("thstrm_amount")) is not None else None)


def _bal(r):
    v = normalize_amount(r.get("thstrm_amount"))
    return float(v) if v is not None else None


def _ordv(r):
    try:
        return int(r.get("ord") or 0)
    except (TypeError, ValueError):
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# 경우의수 로직 트리 — 각 firm-quarter 응답이 어느 케이스로 지배 순이익·자본을 얻는지 명시 분류.
# 각 추출 함수는 (값, 케이스코드)를 반환. 케이스코드로 전 종목 분포를 집계해 exhaustive 검증.
#
# 재무제표 조합: IS(손익)·CIS(포괄손익) 중 하나 또는 둘 · CFS(연결)/OFS(별도) — fetch가 CFS→OFS.
# 지배/비지배 분해: 표준 account_id / 비표준(-표준계정코드 미사용- nm) / 분해 없음(무소수지분·별도).
# ─────────────────────────────────────────────────────────────────────────────
def _nm(r):
    return (r.get("account_nm") or "").replace(" ", "")


def _ctrl_in_sj(rows, sj, val):
    """한 재무제표(sj) 안 지배 귀속 행: ① id substring → ② nm '지배'(비지배·총포괄 제외) ord 최선두.
    반환 (값, 서브케이스 'ID'|'NM'|None). IS/CIS는 ord 시퀀스 별개 → sj 단위 비교."""
    hit = [r for r in rows if r.get("sj_div") == sj and _NI_CTRL_ID in (r.get("account_id") or "")]
    if hit:
        return val(min(hit, key=_ordv)), "ID"
    cands = [r for r in rows if r.get("sj_div") == sj and "지배" in _nm(r) and "비지배" not in _nm(r)
             and "ComprehensiveIncome" not in (r.get("account_id") or "")]
    return (val(min(cands, key=_ordv)), "NM") if cands else (None, None)


def _first(rows, pred, val):
    """조건 pred 만족 행 중 ord 최선두 → val(r), 없으면 None. (id/taxonomy 무관 매칭용)"""
    cands = [r for r in rows if pred(r)]
    return val(min(cands, key=_ordv)) if cands else None


def _total_ni(rows):
    """당기순이익 총계(지배+비지배). 구·신 taxonomy(ifrs_ProfitLoss / ifrs-full_ProfitLoss) 모두."""
    for sj in ("IS", "CIS"):
        v = _first(rows, lambda r: r.get("sj_div") == sj and (r.get("account_id") or "").endswith("_ProfitLoss"), _cum_is)
        if v is not None:
            return v
    return None


def _minority_ni(rows):
    """비지배 당기순이익(총포괄 비지배는 ComprehensiveIncome id로 배제)."""
    for sj in ("IS", "CIS"):
        v = _first(rows, lambda r: r.get("sj_div") == sj and "ProfitLossAttributableToNoncontrolling" in (r.get("account_id") or ""), _cum_is)
        if v is not None:
            return v
    for sj in ("IS", "CIS"):
        v = _first(rows, lambda r: r.get("sj_div") == sj and "비지배" in _nm(r) and "ComprehensiveIncome" not in (r.get("account_id") or ""), _cum_is)
        if v is not None:
            return v
    return None


# 261018(재설계 6단계 — 수집 파서 공백): 당기순이익 총계 행을 **이름으로** 찾는 마지막 단계. 표준 계정코드를 안 쓴
# 회사(`-표준계정코드 미사용-`)는 총계 행 이름이 「Ⅸ.당기순손실」·「Ⅸ.반기순손실」·「VII. 당기순이익(손실)」·「분기순이익(손실)」 꼴이라
# 코드로는 못 찾았다(NO_INCOME_ROW — 10-18 운영 036480 2026 1·2분기 등 40행). 이름 전체가 이 꼴일 때만 — 「법인세비용차감전순이익」·
# 「계속영업이익」·「총포괄손익」·「지배기업 소유주지분 순이익」 같은 다른 행은 걸리지 않는다. 같은 보고서의 XBRL 원본 ProfitLoss 가
# 있는 행은 값이 전부 같았다(10-18 실측 26행).
_ENUM_PREFIX = re.compile(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+|[IVX]+|\d+|\(\d+\))[.)]?")
_NI_TOTAL_NM = re.compile(r"^(?:당기|당반기|반기|당분기|분기)?순(?:이익|손실|손익)(?:\((?:이익|손실)\))?$")


def _ni_total_name(nm) -> bool:
    s = (nm or "").replace(" ", "")
    return bool(_NI_TOTAL_NM.match(_ENUM_PREFIX.sub("", s, count=1)))


def _total_ni_nm(rows):
    """당기순이익 총계 — 이름으로(`_ni_total_name`), IS 다음 CIS, 값이 있는 행 가운데 ord 최선두. 코드로 못 찾았을 때만 쓴다."""
    for sj in ("IS", "CIS"):
        v = _first(rows, lambda r: r.get("sj_div") == sj and _ni_total_name(r.get("account_nm")) and _cum_is(r) is not None,
                   _cum_is)
        if v is not None:
            return v
    return None


def extract_controlling_ni_cum(rows):
    """지배 당기순이익 누적 → (값, 케이스). 트리:
      NODATA · IS_ID/IS_NM · CIS_ID/CIS_NM — 명시 지배행 [LG화학·삼성·기아]
      TOTAL_MINUS_MINORITY — 명시 지배행 없으나 당기순이익 총계 − 비지배순이익 [유한양행형: 서브토탈 부재]
      TOTAL                — 지배·비지배 귀속 둘 다 부재 → 당기순이익 총계=지배 [001340·NAVER 무소수지분]
      TOTAL_NM[_MINUS_MINORITY] — 위와 같은데 총계 행을 코드가 아니라 이름으로 찾음(261018, 표준 계정코드 미사용 회사)
      MINORITY_NO_CTRL / NO_INCOME_ROW — 유도 불가(플래그) → None. 수집(`fetch`)은 이때 같은 보고서의 XBRL 원본을 본다."""
    if not any(r.get("sj_div") in ("IS", "CIS") for r in rows):
        return None, "NODATA"
    for sj in ("IS", "CIS"):
        v, sub = _ctrl_in_sj(rows, sj, _cum_is)
        if v is not None:
            return v, f"{sj}_{sub}"
    tot, mino = _total_ni(rows), _minority_ni(rows)
    nm = ""
    if tot is None:                       # 261018: 코드로 못 찾은 총계만 이름으로 — 종전에 값이 나던 행은 그대로다
        tot = _total_ni_nm(rows)
        nm = "_NM" if tot is not None else ""
    if tot is not None:
        return (tot - mino, f"TOTAL{nm}_MINUS_MINORITY") if mino is not None else (tot, f"TOTAL{nm}")
    return (None, "MINORITY_NO_CTRL") if mino is not None else (None, "NO_INCOME_ROW")


def _total_eq(rows):
    v = _first(rows, lambda r: r.get("sj_div") == "BS" and (r.get("account_id") or "").endswith("_Equity"), _bal)
    if v is not None:
        return v
    return _first(rows, lambda r: r.get("sj_div") == "BS" and _nm(r) == "자본총계", _bal)


def _minority_eq(rows):
    v = _first(rows, lambda r: r.get("sj_div") == "BS" and "NoncontrollingInterests" in (r.get("account_id") or ""), _bal)
    if v is not None:
        return v
    return _first(rows, lambda r: r.get("sj_div") == "BS" and "비지배" in _nm(r), _bal)


def extract_controlling_eq(rows):
    """지배자본 기말잔액 → (값, 케이스). 트리:
      NODATA · BS_ID/BS_NM — 명시 지배자본행
      TOTAL_MINUS_MINORITY — 명시 지배행 없으나 자본총계 − 비지배지분 [유한양행형: 서브토탈 부재]
      TOTAL                — 지배·비지배 자본 둘 다 부재 → 자본총계=지배 [카카오뱅크 별도·001340]
      MINORITY_NO_CTRL / NO_EQUITY_ROW — 유도 불가(플래그) → None. 수집(`fetch`)은 이때 같은 보고서의 XBRL 원본을 본다.

    261018(재설계 6단계 — 수집 파서 공백): 지배자본 행(코드·이름)이 **있는데 칸이 빈** 보고서는 종전엔 그 자리에서 None 으로 끝났다
    (10-18 운영 26행 — 예: 011300 2026 1분기 「지배기업의 소유주에게 귀속되는 자본」 빈칸, 자본총계 306.1억). 이제 값이 있는 행만 지배자본
    행으로 보고, 없으면 아래 총계 규칙으로 간다. 그 26행은 XBRL 원본에도 자본총계(Equity)만 있었고 값이 같았다(지배·비지배 구분을
    태깅하지 않은 보고서 — 「둘 다 부재 → 자본총계=지배」와 같은 경우). 지배자본 행에 값이 있는 보고서는 종전 그대로다."""
    if not any(r.get("sj_div") == "BS" for r in rows):
        return None, "NODATA"
    hit = [r for r in rows if r.get("sj_div") == "BS" and _EQ_CTRL_ID in (r.get("account_id") or "") and _bal(r) is not None]
    if hit:
        return _bal(min(hit, key=_ordv)), "BS_ID"
    cands = [r for r in rows if r.get("sj_div") == "BS" and "지배" in _nm(r) and "비지배" not in _nm(r) and _bal(r) is not None]
    if cands:
        return _bal(min(cands, key=_ordv)), "BS_NM"
    tot, mino = _total_eq(rows), _minority_eq(rows)
    if tot is not None:
        return (tot - mino, "TOTAL_MINUS_MINORITY") if mino is not None else (tot, "TOTAL")
    return (None, "MINORITY_NO_CTRL") if mino is not None else (None, "NO_EQUITY_ROW")


# ─────────────────────────────────────────────────────────────────────────────
# XBRL 원본 대체(261018, 재설계 6단계 — 수집 파서 공백). 전체 재무제표 API(fnlttSinglAcntAll) 응답이 있는데 지배 순이익·자본이
# 비는 보고서는 같은 접수번호의 **재무제표 원본 XBRL**(fnlttXbrl, 보고서당 1콜)에서 그 값을 찾는다. API 응답에서 재무상태표가
# 통째로 빠지거나(10-18 운영 365330·489460 2026 반기 — 손익·현금흐름·자본변동만) 한 줄만 남은 보고서(000540 흥국화재 2026 반기),
# 손익에 당기순이익 행이 없는 보고서(084180 2023 반기 등)가 있었다 — 원본 XBRL 에는 그 값이 있다(365330: 지배자본 904.68억 =
# 자본총계 908.32억 − 비지배 3.64억, 같은 보고서 주요계정 API 의 자본총계와도 같다).
# ─────────────────────────────────────────────────────────────────────────────
_XBRLI = "{http://www.xbrl.org/2003/instance}"
_XBRLDI = "{http://xbrl.org/2006/xbrldi}"
_FS_AXIS = "ConsolidatedAndSeparateFinancialStatementsAxis"
_FS_MEMBER = {"CFS": "ConsolidatedMember", "OFS": "SeparateMember"}
_XBRL_EQ = ("EquityAttributableToOwnersOfParent", "Equity", "NoncontrollingInterests")
_XBRL_NI = ("ProfitLossAttributableToOwnersOfParent", "ProfitLoss", "ProfitLossAttributableToNoncontrollingInterests")


def _xbrl_facts(data: bytes) -> list[tuple]:
    """ZIP 안 XBRL 인스턴스 하나 → [(요소 이름, 차원 {축: 멤버}, 기간, 통화, 값)]. 접두어(ifrs-full: · ifrs:)는 뗀다.
    기간 = ('I', 날) 또는 ('D', 시작, 끝). 숫자가 아니거나 형식이 맞지 않는 사실값은 뺀다. 인스턴스가 없거나 둘 이상이면 []."""
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".xbrl")]
        if len(names) != 1:
            return []
        root = ET.fromstring(z.read(names[0]))

    def _d(s):
        return date.fromisoformat((s or "").strip()[:10])

    ctx = {}
    for c in root.iter(_XBRLI + "context"):
        if any(True for _ in c.iter(_XBRLDI + "typedMember")):
            continue
        dims = {(m.get("dimension") or "").split(":")[-1]: (m.text or "").strip().split(":")[-1]
                for m in c.iter(_XBRLDI + "explicitMember")}
        p = c.find(_XBRLI + "period")
        try:
            inst, st, en = (p.find(_XBRLI + k) if p is not None else None for k in ("instant", "startDate", "endDate"))
            per = ("I", _d(inst.text)) if inst is not None else (("D", _d(st.text), _d(en.text)) if st is not None and en is not None else None)
        except ValueError:
            per = None
        if per:
            ctx[c.get("id")] = (dims, per)
    units = {}
    for u in root.iter(_XBRLI + "unit"):
        ms = [(m.text or "").strip().split(":")[-1].upper() for m in u.iter(_XBRLI + "measure")]
        units[u.get("id")] = ms[0] if len(ms) == 1 else None
    out = []
    for el in root:
        cref = el.get("contextRef")
        if cref not in ctx or el.get("unitRef") not in units:
            continue
        try:
            v = float((el.text or "").strip())
        except ValueError:
            continue
        dims, per = ctx[cref]
        out.append((el.tag.rsplit("}", 1)[-1], dims, per, units[el.get("unitRef")], v))
    return out


def xbrl_controlling(data: bytes, fs: str, period_end: date, fy_start: date) -> dict:
    """같은 보고서의 XBRL 원본 → {"eq": (지배자본 기간 말 잔액, 케이스, 통화), "ni": (지배 순이익 누적, 케이스, 통화)} — 찾은 것만.

    맥락: 연결(CFS)이면 연결 멤버, 별도(OFS)면 별도 멤버 **하나만** 붙은 값(자본 구성요소 같은 다른 차원이 붙은 값은 쓰지 않는다).
    별도만 내는 회사가 차원 없이 태깅했으면(인스턴스에 연결 멤버가 아예 없을 때) 차원 없는 값도 별도로 본다.
    기간: 자본은 `period_end`(칸의 실제 기간 말) 시점 값만, 순이익은 `fy_start`(그 회계연도 첫날)~`period_end` 누적만. 그 분기 말 값이 없고
    전기 말 값만 있으면 쓰지 않는다(10-18 실측 488060 2025 3분기 — 자본총계는 전기 말 것뿐). 같은 요소에 다른 값이 둘 이상이면 그 요소는 버린다.
    고르는 순서(파서와 같은 규칙): 지배 귀속(XBRL_ID) → 총계 − 비지배(XBRL_TOTAL_MINUS_MINORITY) → 총계(XBRL_TOTAL — 지배·비지배를
    나눠 태깅하지 않은 보고서. 파서의 「둘 다 부재 → 총계=지배」와 같다). 세 요소의 통화가 다르면 쓰지 않는다."""
    member = _FS_MEMBER.get(fs)
    facts = _xbrl_facts(data) if member else []
    has_cons = any(d.get(_FS_AXIS) == "ConsolidatedMember" for _, d, _, _, _ in facts)

    def _ctx_ok(dims):
        return dims == {_FS_AXIS: member} or (not dims and fs == "OFS" and not has_cons)

    def _chain(names, per_ok):
        seen: dict[str, set] = {}
        for n, dims, per, cur, v in facts:
            if n in names and _ctx_ok(dims) and per_ok(per):
                seen.setdefault(n, set()).add((v, cur))
        uniq = {n: next(iter(s)) for n, s in seen.items() if len(s) == 1}
        ctrl, total, nci = names
        if ctrl in uniq:
            return (uniq[ctrl][0], "XBRL_ID", uniq[ctrl][1])
        if ctrl in seen or total not in uniq or (nci in seen and nci not in uniq):
            return None                                       # 값이 갈리는 요소가 끼면 버린다
        if nci in uniq:
            if uniq[nci][1] != uniq[total][1]:
                return None
            return (uniq[total][0] - uniq[nci][0], "XBRL_TOTAL_MINUS_MINORITY", uniq[total][1])
        return (uniq[total][0], "XBRL_TOTAL", uniq[total][1])

    out = {}
    eq = _chain(_XBRL_EQ, lambda per: per == ("I", period_end))
    if eq is not None:
        out["eq"] = eq
    ni = _chain(_XBRL_NI, lambda per: per == ("D", fy_start, period_end))
    if ni is not None:
        out["ni"] = ni
    return out


def fiscal_year_start(fy: int, quarter: int, fye: int = 12) -> date:
    """칸 `(fy, quarter)` 이 속한 회계연도의 첫날 = 직전 결산일 다음 날(12월 결산이면 그해 1/1, 3월 결산 2024 1분기면 2024-04-01)."""
    from datetime import timedelta
    return rcal.cell_end(rcal.prior_annual(fy, quarter, fye), 4, fye) + timedelta(days=1)


DDL = """CREATE TABLE IF NOT EXISTS dart_finstat_q(
  ticker text, fy int, quarter int, reprt_code text, fs text,
  ni_cum double precision, eq double precision, fetched text,
  ni_case text, eq_case text,
  PRIMARY KEY(ticker, fy, quarter))"""
DDL_MIGRATE = (
    "ALTER TABLE dart_finstat_q ADD COLUMN IF NOT EXISTS ni_case text",
    "ALTER TABLE dart_finstat_q ADD COLUMN IF NOT EXISTS eq_case text",
)


def _pg():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15)


def _ensure_ddl(con) -> None:
    """분기 재무 표와 칸(`ni_case`·`eq_case`)이 **없을 때만** DDL 을 보낸다(261019, 재설계 6단계).

    `ALTER TABLE … ADD COLUMN IF NOT EXISTS` 는 칸이 이미 있어도 표 전체 잠금(ACCESS EXCLUSIVE)을 먼저 잡는다 — 10-19 Postgres 17 실측: 열린 읽기
    트랜잭션 뒤에서 기다리고, 그동안 서버의 분기 재무 읽기(firm_history·업종 비교)가 그 뒤에 줄 선다. 종전엔 `--fetch` 만 이 DDL 을 보냈는데
    `--seed` 가 주간 일정 두 곳(finstat-quarterly·market-val-weekly)에 들어가면서 매번 보내지 않게 한다. `market_val_series._pg` 와 같은 규칙."""
    have = con.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' "
                       "AND table_name='dart_finstat_q' AND column_name IN ('ni_case','eq_case')").fetchone()[0]
    if have < 2:
        con.execute(DDL)
        for _m in DDL_MIGRATE:
            con.execute(_m)


def derive_fundamentals(tickers: list[str] | None = None) -> None:
    """dart_fundamentals 재무 4열(ni_fy·ni_ttm·eq_fy·eq_mrq)을 dart_finstat_q(분기+Q4연간)에서 파생 —
    **DART 0콜**. SSOT = dart_finstat_q. 원통화 raw 유지(daily cron이 fx_rate로 KRW 환산). 최신 공시분기
    기준 TTM/MRQ라 분기 공시마다 자동 최신화(옛 시장 aggregate 수집기는 1Q 고정·done셋이라 갱신 불가였음 — 260902 삭제).
      ni_fy/eq_fy = 최신 완결 FY(Q4) · eq_mrq = 최신 분기 자본 · ni_ttm = FY(y-1)+누적(y,q)−누적(y-1,q).

    261018: `tickers` 를 주면 그 종목만(재수집한 종목만 좁혀 돌릴 때). 값이 그대로인 행은 고쳐 쓰지 않는다 —
    종전엔 매 실행 전 종목을 같은 값으로 UPDATE 했다(주 1회 재확인 실행이 늘어도 죽은 행이 늘지 않게).

    261018(재설계 6단계, 결산월): 「최신 분기」와 TTM 의 직전 연간 값을 칸 표기 `(fy, quarter)` 의 크기 순서가 아니라 **실제 기간 말**로
    고른다(`report_calendar` — 결산월은 이 표의 `acc_mt`, 비었으면 12월). 12월 결산이면 표기 순서 = 기간 말 순서라 결과가 종전과 같다.
    12월 결산이 아니면 종전엔 같은 해 표기의 사업보고서 칸(3월 결산 = 그해 3월 말)을 그해 마지막 칸으로 보고, TTM 의 직전 연간 값을
    한 해 앞(fy-1)에서 가져왔다 — 3월 결산의 2026 1분기(6월 말) TTM 이 2025.03 사업보고서 + (2026.06 − 2025.06 누적)이 됐다(맞는 값은 2026.03)."""
    from collections import defaultdict
    con = _pg(); con.autocommit = True
    q: dict[str, dict] = defaultdict(dict)
    sql = "SELECT ticker, fy, quarter, COALESCE(ni_cum_restated, ni_cum), COALESCE(eq_restated, eq) FROM dart_finstat_q"
    rows = con.execute(sql + " WHERE ticker = ANY(%s)", (list(tickers),)) if tickers else con.execute(sql)
    for isu, fy, quarter, ni, eq in rows:
        q[isu][(int(fy), int(quarter))] = (ni, eq)
    fye_of = _fye_map(con)
    seen = updated = 0
    for isu, m in q.items():
        fye = fye_of.get(isu, 12)
        latest = lambda keys: max(keys, key=lambda k: rcal.cell_end(k[0], k[1], fye))  # noqa: E731 — 실제 기간 말이 가장 늦은 칸
        # ni·eq 앵커를 **독립**으로(Data-QA #2): 최신분기가 eq만 있고 ni=None이어도 ni_ttm이 불필요하게
        # None 되지 않게 — 각 지표의 최신 유효분기 기준. 값 있는 분기가 곧 available(저장=공시분).
        ni_present = [(fy, qq) for (fy, qq), (ni, eq) in m.items() if ni is not None]
        eq_present = [(fy, qq) for (fy, qq), (ni, eq) in m.items() if eq is not None]
        if not ni_present and not eq_present:
            continue
        if not any(qq != 4 for (fy, qq) in ni_present + eq_present):
            continue   # 분기(Q1-3) 미수집 = Q4 seed만 → 기존 dart_fundamentals 유지(백필 중 안전)
        fys4 = [fy for (fy, qq), (ni, eq) in m.items() if qq == 4 and (ni is not None or eq is not None)]
        fy_full = max(fys4) if fys4 else None                    # 최신 완결 사업연도
        ni_fy, eq_fy = m.get((fy_full, 4), (None, None)) if fy_full else (None, None)
        eq_mrq = m[latest(eq_present)][1] if eq_present else None    # eq 있는 최신분기(기간 말 기준)
        ni_ttm = None
        if ni_present:
            lfy, lq = latest(ni_present)                          # ni 있는 최신분기 기준 TTM
            if lq == 4:
                ni_ttm = m.get((lfy, 4), (None, None))[0]
            else:
                cum_now = m.get((lfy, lq), (None, None))[0]
                cum_prev = m.get((lfy - 1, lq), (None, None))[0]
                fy_prev = m.get((rcal.prior_annual(lfy, lq, fye), 4), (None, None))[0]   # 12월 결산이면 lfy-1(종전과 같다)
                ni_ttm = (fy_prev + cum_now - cum_prev) if None not in (cum_now, cum_prev, fy_prev) else None
        cur = con.execute(
            "UPDATE dart_fundamentals SET ni_fy=%s, ni_ttm=%s, eq_fy=%s, eq_mrq=%s WHERE ticker=%s "
            "AND (ni_fy, ni_ttm, eq_fy, eq_mrq) IS DISTINCT FROM (%s::float8, %s::float8, %s::float8, %s::float8)",
            (ni_fy, ni_ttm, eq_fy, eq_mrq, isu, ni_fy, ni_ttm, eq_fy, eq_mrq))
        seen += 1
        updated += cur.rowcount
    con.close()
    print(f"derive: dart_fundamentals {seen}사 재무 4열 파생 · 값이 바뀐 {updated}사 갱신(DART 0콜)", flush=True)


DDL_FUNDAMENTALS = """CREATE TABLE IF NOT EXISTS dart_fundamentals(
  ticker text PRIMARY KEY, corp_code text, market text, fs text,
  ni_fy double precision, ni_ttm double precision,
  eq_fy double precision, eq_mrq double precision, fetched text,
  acc_mt smallint CHECK (acc_mt BETWEEN 1 AND 12))"""


def _fye_map(con) -> dict[str, int]:
    """종목 → 결산월(`dart_fundamentals.acc_mt`, 12월이 아닌 것만). 칸이 아직 없거나(DDL 전) 비었으면 12월로 본다 — 종전과 같은 읽기.

    261018 신설(재설계 6단계). 채우기는 `--acc-mt`(가장 최근 사업보고서의 기준월), 칸 만들기는 `scripts/fundamentals_ddl.py`."""
    m = rcal.load_fye_map(con)
    if not m and not con.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                                 "AND table_name='dart_fundamentals' AND column_name='acc_mt')").fetchone()[0]:
        print("결산월 칸(dart_fundamentals.acc_mt)이 없다 — 모든 종목을 12월 결산으로 읽는다(scripts/fundamentals_ddl.py --apply)", flush=True)
    return m


#: `--acc-mt` 가 보는 사업보고서 접수 기간(오늘부터 거꾸로 일수). 주 1회 실행이면 넉넉하다 — 결산월이 바뀐 회사는 새 결산월의 첫 사업보고서가
#: 이 창에 들어오는 주에 잡힌다. 처음 채울 때는 `--acc-mt-since 20180101`(연간 표가 2018 사업연도부터다 — 그 사이 결산월을 바꾼 회사를 가려낸다).
ACC_MT_SCAN_DAYS = int(os.getenv("FUND_ACC_MT_SCAN_DAYS", "100"))
_ANNUAL_NM = re.compile(r"사업보고서\s*\((\d{4})\.(\d{2})\)")


def decide_acc_mt(stored: int | None, months: set[int], company_acc_mt=None) -> int | None:
    """결산월 하나를 정한다(순수 함수 — 시험 대상).

    · 창 안 사업보고서의 기준월이 하나 = 그 달 — 저장값이 비었거나 같을 때.
    · 기준월이 둘 이상이거나 저장값과 다르면 = 12(종전 읽기 그대로). 결산월을 바꾼 회사(바꾼 해의 DART 칸이 두 결산월에서 겹친다 — 같은 해
      표기의 1분기보고서가 둘)와 결산마다 사업보고서를 내는 리츠·인프라 펀드(반기·분기 결산)는 결산월 하나로 칸을 못 읽는다.
      한 번 12 가 되면 그대로다(다음 실행에 새 달이 보여도 저장값 12 와 달라 12).
    · 창 안 사업보고서가 없으면 = 저장값, 그것도 없으면 회사 개황(company.json)의 결산월(상장 첫해 — 아직 사업보고서가 없다)."""
    ms = set(months)
    if stored is not None:
        ms.add(int(stored))
    if len(ms) > 1:
        return 12
    if len(ms) == 1:
        return ms.pop()
    if company_acc_mt in (None, ""):
        return None
    return rcal.fye_month(company_acc_mt)


async def refresh_acc_mt(since: date | None = None, today: date | None = None) -> dict[str, int]:
    """`dart_fundamentals.acc_mt`(결산월)을 채운다·고친다 — DART 공시 목록의 사업보고서 이름(「사업보고서 (2026.03)」)의 기준월로.

    261018 신설(재설계 6단계). 회사 개황(company.json)의 결산월은 **지금** 결산월이라, 결산월을 바꾸는 중인 회사(예: 6월 → 12월로 바꾸는
    회사는 개황이 이미 12월인데 저장된 칸은 6월 기준)를 틀리게 읽는다 — 저장된 칸은 보고서를 따르므로 보고서 이름이 정본이다.
    콜: 정기공시 목록(코스피·코스닥, 분기·반기보고서도 섞여 온다 — DART 가 상세 유형 거르기를 받지 않는다) 3개월 창 × 쪽수
    (100일 창 ≈ 90콜, 2018년부터 ≈ 900콜) + 사업보고서가 창 안에 없고 저장값도 빈 종목만 회사 개황 1콜씩."""
    from collections import defaultdict
    from datetime import timedelta
    from open_proxy_mcp.dart.client import get_dart_client, DartClientError, market_window_end, dart_halts_batch, dart_batch_halt_line
    t = today or date.today()
    start = since or (t - timedelta(days=ACC_MT_SCAN_DAYS))
    con = _pg(); con.autocommit = True
    firms = {cc: (tk, m) for tk, cc, m in con.execute(
        "SELECT ticker, corp_code, acc_mt FROM dart_fundamentals WHERE fetched='ok' AND corp_code IS NOT NULL")}
    con.close()
    c = get_dart_client()
    months: dict[str, set[int]] = defaultdict(set)
    calls = 0
    for cls in ("Y", "K"):
        cur = start
        while cur <= t:
            end = min(market_window_end(cur), t)
            page = 1
            while True:
                try:
                    res = await c.search_filings(bgn_de=cur.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                                                 pblntf_ty="A", corp_cls=cls, page_no=page, page_count=100)
                except DartClientError as e:
                    if "013" in str(e):
                        break
                    raise
                calls += 1
                await asyncio.sleep(SLEEP)             # 다른 수집기와 같은 예의(0.45초) — 분당 910 한도를 넉넉히 밑돈다
                for it in res.get("list") or []:
                    hit = _ANNUAL_NM.search(it.get("report_nm") or "")
                    if hit and it.get("corp_code") in firms:
                        months[it["corp_code"]].add(int(hit.group(2)))
                if page >= int(res.get("total_page") or 1):
                    break
                page += 1
            cur = end + timedelta(days=1)
    out: dict[str, int] = {}
    changed: list[tuple] = []
    halted = False
    for cc, (tk, stored) in firms.items():
        comp = None
        if not months.get(cc) and stored is None and not halted:
            try:
                comp = (await c.get_company_info(cc)).get("acc_mt")
                calls += 1
            except DartClientError as e:
                comp = None
                if dart_halts_batch(e):
                    # 261019: 키 한도·키 문제·점검 — 남은 종목도 같은 답이다. 더 부르지 않는다(그 종목은 결산월을 정하지 않고 둔다 —
                    #   저장값이 빈 채라 다음 실행이 다시 본다). 종전엔 종목마다 쉬지 않고 불렀다.
                    halted = True
                    print(dart_batch_halt_line(e, "회사 개황"), flush=True)
        new = decide_acc_mt(stored, months.get(cc, set()), comp)
        if new is not None and new != stored:
            out[tk] = new
            changed.append((tk, stored, new, sorted(months.get(cc, set())), comp))
    if out:
        con = _pg(); con.autocommit = True
        with con.cursor() as cur:
            cur.executemany("UPDATE dart_fundamentals SET acc_mt=%s WHERE ticker=%s AND acc_mt IS DISTINCT FROM %s",
                            [(m, tk, m) for tk, m in out.items()])
        con.close()
    for tk, old, new, ms, comp in sorted(changed):
        if old is not None:   # 이미 있던 결산월이 바뀜 — 사람이 볼 일(결산월 변경·리츠)
            print(f"[결산월] {tk} {old} → {new} (창 안 사업보고서 기준월 {ms})", flush=True)
    n_set = sum(1 for _, old, *_ in changed if old is None)
    print(f"acc-mt: 사업보고서 목록 {start}~{t} · 대상 {len(firms)}사 · 새로 채움 {n_set} · 바뀜 {len(changed) - n_set} · DART {calls}콜", flush=True)
    return out


#: 신규 편입 대상을 고를 상장 종목 수 하한 — 정상은 약 2,760종목. 이보다 적으면 일별 시세 저장분이 비었거나 반쪽이다(261018).
INCLUDE_NEW_MIN_LISTED = 1_500


async def include_new_tickers() -> int:
    """KRX 최신 거래일 상장 **보통주** 가운데 dart_fundamentals 에 없는 종목을 등록한다 — 옛 시장 aggregate
    수집기(--fetch)가 하던 유일한 잔존 역할(260902 흡수). 이 표의 `fetched='ok'` 행이 분기(fetch)·연간
    (market_val_series)·주간 스냅샷(market_val_weekly) 세 수집기의 대상 집합이라, 여기 없는 신규 상장사는
    어디서도 안 잡힌다(구 스크립트는 done 셋 고정이라 갱신 불가였다).

    재무 4열은 쓰지 않는다(NULL) — SSOT 는 dart_finstat_q 이고 파생(--derive)이 채운다. 옛 방식(1Q 고정
    직접 수집)을 되살리지 않는다. corp_code 미해결이면 fetched='nocorp' 로 남겨 재시도 대상에서 뺀다.
    콜: KRX ≤4(시세 2 는 _ensure_krx_fresh 가 이미 DB 에 있으면 0, 종목유형 2) · DART 0(corpCode 캐시).
    """
    from open_proxy_mcp.services.price_multiple_data import _ensure_krx_fresh
    from market_val_weekly import _krx_kinds          # 같은 폴더 — 우선주/보통주 판별(KRX isu_base_info 2콜)
    from open_proxy_mcp.dart.client import get_dart_client
    snap_dd = await _ensure_krx_fresh()
    if not snap_dd:
        print("include-new: KRX 최신 거래일 확보 실패 — 건너뜀", flush=True)
        return 0
    kinds = await _krx_kinds(snap_dd)
    if not kinds:
        print("include-new: KRX 종목유형(kinds) 확보 실패 — 건너뜀(우선주를 보통주로 넣지 않기 위해)", flush=True)
        return 0
    con = _pg(); con.autocommit = True
    con.execute(DDL_FUNDAMENTALS)
    known = {r[0] for r in con.execute("SELECT ticker FROM dart_fundamentals")}
    # 261018(재설계 5-1 쓰는 쪽): krx_weekly → 일별 시세 저장분. `_ensure_krx_fresh` 가 돌려주는 날짜는 krx_daily 최신일이고
    #   krx_weekly 는 닫힌 주만 담아 주중 그 날짜 행이 없다 — 종전 질의는 조용히 0종목(「신규 0」)이 됐다.
    listed = con.execute("SELECT ticker, market FROM krx_daily WHERE trd_dd=%s", (snap_dd,)).fetchall()
    if len(listed) < INCLUDE_NEW_MIN_LISTED:   # 반쪽·빈 저장분을 「신규 없음」으로 읽지 않는다
        print(f"::error::include-new: {snap_dd} 일별 시세가 {len(listed)}종목뿐 — 저장분이 비었거나 반쪽이다. 건너뜀", flush=True)
        con.close()
        return 0
    new = [(t, m) for t, m in listed if t not in known and kinds.get(t) == "보통주"]
    print(f"include-new: {snap_dd} 상장 {len(listed)} · 기등록 {len(known)} · 신규 보통주 {len(new)}", flush=True)
    c = get_dart_client()
    n_ok = n_nocorp = 0
    for ticker, market in new:
        corp = await c.lookup_corp_code(ticker)
        if corp and corp.get("corp_code"):
            con.execute("INSERT INTO dart_fundamentals(ticker, corp_code, market, fetched) "
                        "VALUES(%s, %s, %s, 'ok') ON CONFLICT (ticker) DO NOTHING",
                        (ticker, corp["corp_code"], market))
            n_ok += 1
        else:
            con.execute("INSERT INTO dart_fundamentals(ticker, market, fetched) "
                        "VALUES(%s, %s, 'nocorp') ON CONFLICT (ticker) DO NOTHING", (ticker, market))
            n_nocorp += 1
    # 양쪽으로 센다(CLAUDE.md 13): 새 행 N건이 실제로 들어갔는지.
    after = con.execute("SELECT count(*) FROM dart_fundamentals").fetchone()[0]
    con.close()
    print(f"include-new: 등록 {n_ok}(ok) + {n_nocorp}(nocorp) → dart_fundamentals {len(known)} → {after}행", flush=True)
    return n_ok


def seed_q4() -> None:
    """Q4(사업보고서) 행을 이미 있는 연간 데이터에서 seed — DART 0콜.

    261018(재설계 6단계): ① 값이 같은 행은 다시 쓰지 않는다(종전엔 매번 전 행 ~1.6만 행을 같은 값으로 UPDATE — 주 1회·매일 일정에 넣으면서 죽은 행이
    쌓이지 않게). ② 연간 표의 빈 칸(NULL)이 Q4 칸의 값을 지우지 않는다 — 10-18 운영: 연간 표에 순이익·자본 칸만 빈 행이 있는 12월 결산 3종목
    (466100·471820·082270)은 Q4 칸에 값이 있는데, 종전 식으로 돌리면 NULL 로 덮여 재무 요약 연간 값이 사라졌다. 둘 다 값이면 연간 표(정본)를 따른다.
    ③ 「최신 FY 가 연간 표에 아직 없으면 재무 요약에서 bootstrap」 하던 경로를 뺐다 — 재무 요약의 연간 값(`ni_fy`·`eq_fy`)은 260902 부터 **이 표의
    Q4 칸에서 파생**되므로, 연간 표가 비었을 때 그 값을 새 FY 칸에 넣으면 지난해 값이 올해 표기로 들어간다(4월 첫 실행에서 연간 수집이 한 행도 못 쓰고
    멈춘 경우). 연간 값은 연간 표(`market_val_series --fetch`)로만 들어온다.
    261019: 표·칸이 이미 있으면 DDL 을 보내지 않는다(`_ensure_ddl` — 칸이 있어도 표 전체 잠금을 기다리던 것)."""
    con = _pg(); con.autocommit = True
    _ensure_ddl(con)
    n1 = con.execute("""
        INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched)
        SELECT ticker, fy, 4, '11011', fs,
               COALESCE(ni_restated, ni), COALESCE(eq_restated, eq),
               CASE WHEN COALESCE(ni_restated,ni) IS NOT NULL OR COALESCE(eq_restated,eq) IS NOT NULL
                    THEN 'ok' ELSE 'nodata' END
        FROM dart_finstat_y
        WHERE fetched='ok' OR ni_restated IS NOT NULL OR eq_restated IS NOT NULL
        ON CONFLICT (ticker, fy, quarter) DO UPDATE SET
          ni_cum=COALESCE(EXCLUDED.ni_cum, dart_finstat_q.ni_cum), eq=COALESCE(EXCLUDED.eq, dart_finstat_q.eq),
          fetched=CASE WHEN COALESCE(EXCLUDED.ni_cum, dart_finstat_q.ni_cum) IS NOT NULL
                         OR COALESCE(EXCLUDED.eq, dart_finstat_q.eq) IS NOT NULL THEN 'ok' ELSE 'nodata' END
        WHERE (dart_finstat_q.ni_cum, dart_finstat_q.eq)
              IS DISTINCT FROM (COALESCE(EXCLUDED.ni_cum, dart_finstat_q.ni_cum), COALESCE(EXCLUDED.eq, dart_finstat_q.eq))
           OR dart_finstat_q.fetched IS DISTINCT FROM
              (CASE WHEN COALESCE(EXCLUDED.ni_cum, dart_finstat_q.ni_cum) IS NOT NULL
                      OR COALESCE(EXCLUDED.eq, dart_finstat_q.eq) IS NOT NULL THEN 'ok' ELSE 'nodata' END)
    """).rowcount
    print(f"Q4 seed: dart_finstat_y → 분기 표 사업보고서 칸 {n1}행(새로 넣거나 값이 바뀐 행만)")
    con.close()


def _flush(buf) -> None:
    if not buf:
        return
    with _pg() as c:
        with c.cursor() as cur:
            cur.executemany("""INSERT INTO dart_finstat_q
                (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched, ni_case, eq_case)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker, fy, quarter) DO UPDATE SET
                  reprt_code=EXCLUDED.reprt_code, fs=EXCLUDED.fs, ni_cum=EXCLUDED.ni_cum,
                  eq=EXCLUDED.eq, fetched=EXCLUDED.fetched, ni_case=EXCLUDED.ni_case,
                  eq_case=EXCLUDED.eq_case""", buf)
        c.commit()
    buf.clear()


_NET = ("ReadError", "ConnectError", "ConnectTimeout", "ReadTimeout", "Timeout", "Operational", "gaierror")


def _is_net(e) -> bool:
    return any(t in type(e).__name__ for t in _NET) or any(t in str(e) for t in _NET)


def _self_ref_map(con) -> dict:
    """종목 → 자기 규모의 자(尺). 연간 자본(정정치 우선)의 중앙값과 최근값 중 큰 쪽.

    260829 신설. 종전 가드는 시장 최댓값(삼성전자)을 앵커로 써서 「삼성보다 큰가」를 물었다 —
    소형주의 1,000배 오류는 통과시키고 대형주의 정상 실적은 오탐했다. 회사마다 자를 따로 든다.
    """
    import collections
    import statistics as _st
    ann = collections.defaultdict(list)
    for t, fy, eq, eqr in con.execute(
            "SELECT ticker, fy, eq, eq_restated FROM dart_finstat_y ORDER BY fy"):
        v = eqr if eqr is not None else eq
        if v:
            ann[t].append(abs(v))
    return {t: max(_st.median(vs), vs[-1]) for t, vs in ann.items() if vs}


def _resume_sets(rows, nodata_days: int | None = None, today: date | None = None,
                 fye_of: dict | None = None) -> tuple[set, set]:
    """(이미 끝난 분기, 다시 받을 nodata 분기). rows = (ticker, fy, quarter, fetched) — Q1~Q3 행.

    ok·그 밖의 값 = 완료 · err:… = 완료 아님(종전과 같다) · nodata = 마감 뒤 `nodata_days` 일 안이면 다시 받는다(261018).
    `fye_of` = 종목 → 결산월(없으면 12월) — 마감을 칸의 실제 기간 말로 센다."""
    done, retry = set(), set()
    fye_of = fye_of or {}
    for t, fy, qq, f in rows:
        key = (t, int(fy), int(qq))
        if f is not None and f.startswith("err:"):
            continue
        if f == "nodata" and _retry_nodata(key[1], key[2], today=today, days=nodata_days, fye=fye_of.get(t, 12)):
            retry.add(key)
            continue
        done.add(key)
    return done, retry


async def fetch(years, pilot=None, conc=2, nodata_days: int | None = None,
                only: set[tuple[str, int, int]] | None = None) -> None:
    """동시성 conc(기본 2, CLAUDE.md 허용) 워커풀. DART client _throttle_api가 910/분 강제하므로
    안전. 큐에서 분기 작업을 꺼내 CFS→OFS + backoff 재시도. 지속 outage면 stop 세팅 → 워커 종료 →
    resume 가능. 버퍼는 lock 보호, flush는 to_thread로 이벤트루프 비차단.
    `nodata_days`: nodata 를 다시 받는 기간(일) — 비우면 `NODATA_RETRY_DAYS`.
    `only`: {(종목, fy, quarter)} — 이 칸만 다시 받는다(이미 ok 여도). 파서를 고친 뒤 그 파서로 비던 칸만 다시 채울 때(261018 `--refetch`).
      공시 마감 전 칸·4분기(사업보고서 — seed 몫)는 빼고, `years` 와 무관하게 그 칸의 연도를 쓴다."""
    from open_proxy_mcp.dart.client import get_dart_client, DartClientError, dart_halts_batch, dart_batch_halt_line
    con = _pg(); con.autocommit = True
    _ensure_ddl(con)                     # 261019: 표·칸이 있으면 DDL 을 보내지 않는다
    if only:
        pilot = sorted({k[0] for k in only})
    if pilot:
        firms = [r for r in con.execute(
            "SELECT ticker, corp_code FROM dart_fundamentals WHERE ticker = ANY(%s) AND fetched='ok'", (pilot,))]
    else:
        firms = [r for r in con.execute(
            "SELECT ticker, corp_code FROM dart_fundamentals WHERE fetched='ok' ORDER BY ticker")]
    # 분기 단위 resume — ok 는 완료. err(기타 실패)는 done에서 제외 →
    # 재실행 시 자동 재수집(DART 일일한도 20k/키 리셋 후 남은분 채움). DO UPDATE로 err행 덮어씀.
    # 261019: 키 한도[020]·키 문제·점검은 err 로 적지 않고 그 자리에서 멈춘다(`dart_halts_batch`) — 못 받은 칸은 행이 그대로라 다음 실행이 받는다.
    # 261018: nodata 도 마감 뒤 NODATA_RETRY_DAYS 일 동안은 다시 받는다(`_resume_sets`) — 종전엔 완료로 봐서
    #   마감 직후 비어 있던 회사가 나중에 내도 다시 받지 않았다.
    # 261018(결산월): 마감·환율 기준일·전기 칸 대조 연도를 종목 결산월로 센다(12월 결산은 종전과 같다).
    fye_of = _fye_map(con)
    done, retry = _resume_sets(con.execute(
        "SELECT ticker, fy, quarter, fetched FROM dart_finstat_q WHERE quarter != 4").fetchall(), nodata_days,
        fye_of=fye_of)
    # 260829: 가드에 넘길 「그 회사 자신의 자」 — 연간 자본의 중앙값과 가장 최근 연간 자본 중 큰 쪽.
    #   중앙값만 쓰면 실제로 커진 회사(증자)를 오탐한다. 실측 74,918행에서 오탐 0·목표 8행 전부 적중.
    self_ref = _self_ref_map(con)
    # 260829(마스터 착안): 그 보고서의 「전기」 칸과 대조할 **우리가 아는 그 기간의 값**.
    #   분기보고서의 전기 = 직전 사업연도말이므로 연간표에서 (종목, fy-1) 로 찾는다.
    prev_eq = {(r[0], int(r[1])): (r[3] if r[3] is not None else r[2])
               for r in con.execute("SELECT ticker, fy, eq, eq_restated FROM dart_finstat_y")
               if (r[3] if r[3] is not None else r[2])}
    con.close()
    todo = [(i, c, y, q, code) for i, c in firms for y in years
            for q, code in QFETCH if (i, y, q) not in done and _disclosed(y, q, fye=fye_of.get(i, 12))]
    if only:
        todo = [(i, c, y, q, code) for i, c in firms for y in sorted({k[1] for k in only})
                for q, code in QFETCH if (i, y, q) in only and _disclosed(y, q, fye=fye_of.get(i, 12))]
    yspan = sorted({y for _, _, y, _, _ in todo})
    n_retry = sum(1 for i, _, y, q, _ in todo if (i, y, q) in retry)
    print(f"대상 {len(firms)}사 · 공시완료 분기만 · 남은 {len(todo)}건(연도 {yspan}, 그중 nodata 재확인 {n_retry}) · "
          f"동시성 {conc}", flush=True)
    c = get_dart_client()
    buf: list = []; lock = asyncio.Lock(); stop = asyncio.Event()
    prog = {"n": 0}; total = len(todo)

    def _halt(e) -> None:
        """261019: 키 한도·키 문제·점검(`dart_halts_batch` — 남은 키까지 다 본 뒤) — 남은 칸도 같은 답이다. 그 칸은 적지 않고(행이 그대로라
        다음 실행의 재개 규칙이 받는다) 모든 worker 를 멈춘다. 종전엔 칸마다 err 로 적고 쉬지 않고 다음 칸을 불렀다."""
        if not stop.is_set():
            print(dart_batch_halt_line(e, f"{prog['n']}/{total}칸 처리"), flush=True)
        stop.set()
    # 결과를 상태별로 센다 — 「종료」만으로는 몇 건이 실제로 채워졌는지 모른다(CLAUDE.md 12: 양쪽 세기).
    tally = {"ok": 0, "nodata": 0, "err": 0, "refilled": 0, "xbrl": 0, "xbrl_filled": 0}
    queue: asyncio.Queue = asyncio.Queue()
    for item in todo:
        queue.put_nowait(item)

    async def acnt(cc, yr, rc, fs):
        try:
            d = await c.get_fnltt_singl_acnt_all(cc, str(yr), rc, fs)
            return (d.get("list") or []) if isinstance(d, dict) else []
        except DartClientError as e:
            if "[013]" in str(e):
                return []
            raise

    async def fetch_rows(cc, yr, rc):
        for attempt in range(9):  # ~5분 outage까지 견딤
            try:
                fs = "CFS"; rows = await acnt(cc, yr, rc, fs); await asyncio.sleep(SLEEP)
                if not rows:
                    fs = "OFS"; rows = await acnt(cc, yr, rc, fs); await asyncio.sleep(SLEEP)
                return fs, rows
            except Exception as ne:
                if _is_net(ne) and attempt < 8:
                    await asyncio.sleep(min(60, 5 * 2 ** attempt)); continue
                raise

    async def xbrl_lookup(rows, rc, fs, yr, q, fye) -> dict:
        """같은 접수번호의 XBRL 원본 → `xbrl_controlling`. 받지 못하면(문서 없음·형식 오류) {} — 그 칸은 종전처럼 빈 채로 둔다.
        키 한도·키 문제·점검(`dart_halts_batch`)은 올린다(261019 — 종전엔 이것도 {} 로 삼켜 그 칸을 빈 채로 적었다)."""
        rcept = next((r.get("rcept_no") for r in rows if r.get("rcept_no")), None)
        getter = getattr(c, "get_fnltt_xbrl", None)
        if not rcept or getter is None:
            return {}
        async with lock:
            tally["xbrl"] += 1
        try:
            data = await getter(rcept, rc)
            await asyncio.sleep(SLEEP)
            return xbrl_controlling(data, fs, rcal.cell_end(yr, q, fye), fiscal_year_start(yr, q, fye))
        except Exception as e:  # noqa: BLE001 — 대체 경로라 실패해도 종전 결과(빈 칸)를 지킨다
            if dart_halts_batch(e):
                # 261019: 키 한도로 못 받은 원본은 「원본 없음」이 아니다 — 그 칸을 빈 채로 적지 않고 배치를 멈춘다(worker 가 받는다).
                raise
            print(f"[XBRL] {rcept} 못 읽음({type(e).__name__}) — 그 칸은 빈 채로", flush=True)
            return {}

    async def maybe_flush(force=False):
        async with lock:
            if buf and (force or len(buf) >= 25):
                snap = buf[:]; buf.clear()
                await asyncio.to_thread(_flush, snap)

    async def worker():
        while not stop.is_set():
            try:
                isu, cc, yr, q, rc = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                fs, rows = await fetch_rows(cc, yr, rc)
            except Exception as e:
                if _is_net(e):
                    stop.set(); print(f"네트워크({type(e).__name__}) — 중단(재개 가능)", flush=True); return
                if dart_halts_batch(e):
                    _halt(e); return
                async with lock:
                    buf.append((isu, yr, q, rc, None, None, None, f"err:{str(e)[:30]}", "ERR", "ERR"))
                    tally["err"] += 1
                await asyncio.sleep(SLEEP)   # 261019: 오류 뒤에도 같은 간격
            else:
                ni, ni_case = extract_controlling_ni_cum(rows)
                eq, eq_case = extract_controlling_eq(rows)
                assets = gid_exact(rows, "ifrs-full_Assets", ("BS",))
                liab = gid_exact(rows, "ifrs-full_Liabilities", ("BS",))
                eq_total = gid_exact(rows, "ifrs-full_Equity", ("BS",))
                # 전기 칸(직전 사업연도말) — 같은 기간의 같은 숫자라 우리 보유값과 일치해야 한다.
                eq_frm = gid_exact(rows, "ifrs-full_Equity", ("BS",), field="frmtrm_amount")
                # 통화 근본해법(260706): 그 분기 응답 자체에서 통화 감지 후 KRW 환산(저장은 항상 KRW) —
                # market_val_series.py 연간 fetch와 동일 패턴(statement_currency+fx_to_krw) 재사용.
                stmt_cur = statement_currency(rows)
                fx_err = None
                fye = fye_of.get(isu, 12)
                # 261018: 응답은 있는데 지배 순이익·자본이 비면 같은 보고서의 XBRL 원본에서 그 칸만 찾는다(그 보고서 1콜).
                #   이미 찾은 값은 바꾸지 않는다. 원본의 통화가 응답과 다르면 쓰지 않는다(아래 환산을 같은 규칙으로 탄다).
                if (ni is None or eq is None) and rows:
                    try:
                        x = await xbrl_lookup(rows, rc, fs, yr, q, fye)
                    except Exception as e:   # xbrl_lookup 은 멈춰야 하는 오류만 올린다 — 이 칸은 적지 않는다
                        _halt(e); return
                    for key in ("ni", "eq"):
                        got = x.get(key)
                        if got is None or got[2] != stmt_cur:
                            continue
                        if key == "ni" and ni is None:
                            ni, ni_case = got[0], got[1]
                        elif key == "eq" and eq is None:
                            eq, eq_case = got[0], got[1]
                        else:
                            continue
                        async with lock:
                            tally["xbrl_filled"] += 1
                if stmt_cur != "KRW":
                    # 261018(결산월): 환율 기준일 = 칸의 실제 기간 말(12월 결산이면 종전 0331·0630·0930 그대로)
                    _fx_dd = rcal.cell_end(yr, q, fye).strftime("%Y%m%d")
                    ecos_fx_rate = await fx_to_krw(stmt_cur, _fx_dd)
                    if ecos_fx_rate is None:
                        fx_err = f"err:fx_{stmt_cur}"
                    else:
                        if fx_source(stmt_cur, _fx_dd) == SRC_YAHOO:
                            # 261019: ECOS 를 못 받아 야후 대체값으로 환산했다 — 값은 담되(누락보다 낫다) 로그에 남긴다.
                            #   대체값은 이제 환율 표에 굳지 않지만 이 행의 원화 값에는 들어간다. 배치 환경에 ECOS_API_KEY 를 둘 것.
                            #   ★ 경고만 하고 환산은 아래에서 그대로 한다(elif 로 붙이면 환산을 건너뛰어 원통화 값이 「ok」로 들어간다).
                            print(f"::warning::[환율] {isu} {yr}Q{q} {stmt_cur} {_fx_dd} — ECOS 대신 야후 대체값 {ecos_fx_rate}", flush=True)
                        def _fx(x): return x * ecos_fx_rate if x is not None else None
                        # 261018: 전기 칸도 원으로 — 대조 상대(연간 표의 직전 연도 자본)는 원 단위다. 종전엔 원통화 그대로
                        #   대어 USD 보고사의 비율이 1/환율(≈10⁻³)로 떨어져 「단위 오류 ÷0.001」로 오판, 멀쩡한 값을 1,000배로
                        #   「복구」했다(10-18 실측 글로벌에스엠 2026 반기 자본 130.9조). 같은 분기말 환율이라 기말 환율과의 차이는
                        #   10의 거듭제곱 판정(±20%)에 걸리지 않는다.
                        ni, eq, assets, liab, eq_total, eq_frm = (_fx(v) for v in (ni, eq, assets, liab, eq_total, eq_frm))
                if fx_err:
                    async with lock:
                        buf.append((isu, yr, q, rc, None, None, None, fx_err, "ERR", "ERR"))
                        tally["err"] += 1
                else:
                    # 전기 칸 = 직전 결산 자본 — 그 사업보고서 표기는 결산월로(12월 결산이면 yr-1, 3월 결산의 1~3분기면 같은 해)
                    v = scale_assess(thstrm=ni, assets=assets, liabilities=liab, equity=eq_total,
                                     self_ref=self_ref.get(isu),
                                     frmtrm_equity=eq_frm, known_prev_equity=prev_eq.get((isu, rcal.prior_annual(yr, q, fye))))
                    if v["tier"] == "hard":
                        pm = v["diagnostics"].get("prior_period_mismatch") or {}
                        if pm.get("triggered"):
                            # 배수를 알면 무효화 대신 **고쳐서** 담는다 — 보고서가 통째로 틀리므로
                            # 전기 칸에서 나온 배수가 당기 값에도 그대로 적용된다(260829 실측 7/7).
                            d = pm["fix_divisor"]
                            print(f"[가드] {isu} {yr}Q{q} 단위오류 ÷{d:,} — 전기칸 대조로 복구", flush=True)
                            ni = ni / d if ni is not None else None
                            eq = eq / d if eq is not None else None
                            ni_case = eq_case = f"SCALE_FIXED_DIV{d}"
                        else:
                            print(f"[가드] {isu} {yr}Q{q} 스케일오류({v['hard_hit']}) — 무효화", flush=True)
                            ni = eq = None; ni_case = eq_case = "SCALE_GUARD"
                    st = "ok" if (ni is not None or eq is not None) else "nodata"
                    async with lock:
                        buf.append((isu, yr, q, rc, fs, ni, eq, st, ni_case, eq_case))
                        tally[st] += 1
                        if st == "ok" and (isu, yr, q) in retry:
                            tally["refilled"] += 1
            prog["n"] += 1
            if prog["n"] % 300 == 0:
                print(f"{prog['n']}/{total}", flush=True)
            await maybe_flush()

    await asyncio.gather(*[asyncio.create_task(worker()) for _ in range(conc)])
    try:
        await maybe_flush(force=True)
    except Exception as e:
        print(f"최종 flush 실패({type(e).__name__}) — 재개 시 복구", flush=True)
    print(f"fetch 종료 — ok {tally['ok']}(그중 nodata → ok {tally['refilled']}) · nodata {tally['nodata']} · "
          f"err {tally['err']} · XBRL 원본 {tally['xbrl']}건 → 채운 칸 {tally['xbrl_filled']}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# 전기 칸 보충(261019, 재설계 P1 — 원천 결측). DART 재무제표 API(전체 재무제표·주요계정 둘 다)가 그 보고서를 **아예 주지 않는**
# 칸이 있다(`fetched='nodata'`). 10-19 실측: 금융업(은행·보험·증권 지주)은 2023 반기까지의 분기 보고서가 API 에 없다 — KB금융
# 2023 1분기·반기, 삼성생명 2023 1분기·반기 모두 [013]. 원 보고서는 공시돼 있지만 구조화 재무가 없다(XBRL 이 없던 시기).
# 같은 값은 **다음 해 같은 분기 보고서의 전년 동기 칸**에 있다 — 손익은 전기 누적(frmtrm_add_amount), 자본은 자본변동표의 전기 말
# 잔액. 그 값을 원본 칸(ni_cum·eq)이 아니라 정정 칸(ni_cum_restated·eq_restated)에 근거(restate_why)와 함께 둔다 — 읽는 쪽은 이미
# `COALESCE(정정, 원본)` 이다(연간 표의 재작성치·단위 오류 정정과 같은 자리). 원본 칸·수집 상태(fetched)는 건드리지 않으므로 정기
# 수집이 나중에 원 보고서를 받으면 그 값이 원본 칸에 들어가고, 정정 칸이 계속 우선한다(다음 해 보고서가 재작성한 값 = 회사가 고친 값).
# ─────────────────────────────────────────────────────────────────────────────
#: 전년 동기 **누적** 손익 칸 — 1분기는 3개월 = 누적이라 3개월 칸도 쓴다. 반기·3분기의 frmtrm_q_amount 는 3개월이라 쓰지 않는다.
_PRIOR_NI_FIELDS = {1: ("frmtrm_add_amount", "frmtrm_q_amount", "frmtrm_amount"), 2: ("frmtrm_add_amount",), 3: ("frmtrm_add_amount",)}


def _as_prior_rows(rows, quarter: int) -> list[dict]:
    """다음 해 같은 분기 보고서 rows → 손익(IS·CIS) 행의 당기 칸을 「전년 동기 누적」으로 바꾼 rows. 추출 트리
    (`extract_controlling_ni_cum` — 지배 귀속 코드·이름·총계−비지배)를 그대로 쓰려고. 전기 칸이 빈 행은 빈 값이 된다."""
    fields = _PRIOR_NI_FIELDS[quarter]
    out = []
    for r in rows:
        if r.get("sj_div") in ("IS", "CIS"):
            v = next((r.get(f) for f in fields if normalize_amount(r.get(f)) is not None), "")
            out.append({**r, "thstrm_add_amount": v, "thstrm_amount": v})
        else:
            out.append(r)
    return out


def prior_equity_from_sce(rows) -> tuple[float | None, str]:
    """다음 해 같은 분기 보고서 → (전년 같은 분기 말 지배자본, 케이스). 못 정하면 (None, 사유).

    재무상태표의 전기 칸은 직전 **사업연도 말**이라 분기 말 잔액이 아니다. 자본변동표의 기말 행(`ifrs-full_Equity`, 구성요소마다 한 행)은
    당기 칸 = 이번 분기 말, 전기 칸(frmtrm_q_amount) = 전년 같은 분기 말이다. 구성요소 가운데 **이번 분기 말 값이 같은 보고서 재무상태표의
    지배자본과 같은 행**만 믿는다(그 행이 지배 몫 합계라는 확인 — 이익잉여금 같은 다른 구성요소·비지배·총계 행은 걸리지 않는다).
    그런 행들의 전기 값이 서로 다르면 쓰지 않는다."""
    cur_eq, _ = extract_controlling_eq(rows)
    if cur_eq is None:
        return None, "NO_CURRENT_EQ"
    prev = set()
    for r in rows:
        if r.get("sj_div") != "SCE" or r.get("account_id") != "ifrs-full_Equity":
            continue
        cur, pv = normalize_amount(r.get("thstrm_amount")), normalize_amount(r.get("frmtrm_q_amount"))
        if cur is None or pv is None or abs(float(cur) - cur_eq) > 0.5:
            continue
        prev.add(float(pv))
    if not prev:
        return None, "NO_SCE_ROW"
    if len(prev) > 1:
        return None, "SCE_AMBIGUOUS"
    return prev.pop(), "SCE_PRIOR"


async def fill_prior_from_next(keys: set[tuple[str, int, int]], dry: bool = False) -> dict:
    """`keys`(종목, 사업연도, 분기 1~3) 가운데 **수집 상태가 nodata 이고 값·정정 값이 모두 빈** 칸만, 다음 해 같은 분기 보고서의 전년 동기
    값으로 정정 칸을 채운다(위 머리 주석). 보고서 하나에 1~2콜(연결 → 별도). `dry` 면 쓰지 않고 무엇을 쓸지 출력만 한다.

    지키는 것: 원본 칸·fetched·이미 있는 정정 값은 그대로 · 다음 해 보고서가 아직 공시 마감 전이면 건너뜀 · 외화 보고사는 그 칸의 기간 말
    환율로 원화 환산(수집과 같은 규칙) · 회사 자기 규모의 20배를 넘는 값(단위 오류 꼴)은 쓰지 않는다. 결과 = 칸별 처리 사유."""
    from open_proxy_mcp.dart.client import get_dart_client, DartClientError, dart_halts_batch, dart_batch_halt_line
    from open_proxy_mcp.services.scale_guard import check_self_relative
    keys = {(t, int(fy), int(q)) for t, fy, q in keys if int(q) in (1, 2, 3)}
    con = _pg(); con.autocommit = True
    tickers = sorted({k[0] for k in keys})
    corp = {t: cc for t, cc in con.execute(
        "SELECT ticker, corp_code FROM dart_fundamentals WHERE ticker = ANY(%s)", (tickers,))}
    cells = {(t, int(fy), int(q)): rest for t, fy, q, *rest in con.execute(
        "SELECT ticker, fy, quarter, fetched, ni_cum, eq, ni_cum_restated, eq_restated FROM dart_finstat_q "
        "WHERE ticker = ANY(%s)", (tickers,))}
    fye_of = _fye_map(con)
    self_ref = _self_ref_map(con)
    con.close()
    c = get_dart_client()

    async def acnt(cc, yr, rc, fs):
        try:
            d = await c.get_fnltt_singl_acnt_all(cc, str(yr), rc, fs)
            return (d.get("list") or []) if isinstance(d, dict) else []
        except DartClientError as e:
            if "[013]" in str(e):
                return []
            raise

    result: dict[tuple, str] = {}
    writes = []
    halted = None   # 261019: 키 한도·키 문제·점검·네트워크면 남은 칸은 부르지 않고, 그때까지 받은 칸은 쓴다(종전엔 예외로 죽어 0칸)
    for i, (t, fy, q) in enumerate(sorted(keys)):
        if halted is not None:
            result[(t, fy, q)] = f"중단 — 앞 칸에서 {halted} (다음 실행이 받는다)"
            continue
        cell, cc, fye = cells.get((t, fy, q)), corp.get(t), fye_of.get(t, 12)
        if cell is None or cc is None:
            result[(t, fy, q)] = "행 없음"
            continue
        fetched, ni, eq, ni_r, eq_r = cell
        if fetched != "nodata" or ni is not None or eq is not None or ni_r is not None or eq_r is not None:
            result[(t, fy, q)] = f"건너뜀(상태 {fetched} · 값 또는 정정 값 있음)"
            continue
        if not _disclosed(fy + 1, q, fye=fye):
            result[(t, fy, q)] = "건너뜀(다음 해 같은 분기 보고서가 아직 공시 마감 전)"
            continue
        rc = Q_TO_RC[q]
        try:
            fs = "CFS"; rows = await acnt(cc, fy + 1, rc, fs); await asyncio.sleep(SLEEP)
            if not rows:
                fs = "OFS"; rows = await acnt(cc, fy + 1, rc, fs); await asyncio.sleep(SLEEP)
        except Exception as e:
            if dart_halts_batch(e) or _is_net(e):
                halted = str(e)
                print(dart_batch_halt_line(e, f"{i}/{len(keys)}칸 처리"), flush=True)
                result[(t, fy, q)] = f"중단 — {e} (다음 실행이 받는다)"
                continue
            if isinstance(e, DartClientError):   # 그 칸만의 오류 — 그 칸만 건너뛴다(종전엔 예외로 죽어 받은 칸까지 버렸다)
                result[(t, fy, q)] = f"오류 — {e}"
                await asyncio.sleep(SLEEP)
                continue
            raise
        if not rows:
            result[(t, fy, q)] = "다음 해 같은 분기 보고서도 API 에 없음"
            continue
        p_ni, ni_case = extract_controlling_ni_cum(_as_prior_rows(rows, q))
        p_eq, eq_case = prior_equity_from_sce(rows)
        stmt_cur = statement_currency(rows)
        if stmt_cur != "KRW":
            _fx_dd = rcal.cell_end(fy, q, fye).strftime("%Y%m%d")
            rate = await fx_to_krw(stmt_cur, _fx_dd)
            if rate is None:
                result[(t, fy, q)] = f"건너뜀(환율 {stmt_cur} 조회 실패)"
                continue
            if fx_source(stmt_cur, _fx_dd) == SRC_YAHOO:   # 261019: 수집(--fetch)과 같은 경고
                print(f"::warning::[환율] {t} {fy}Q{q} 정정 {stmt_cur} {_fx_dd} — ECOS 대신 야후 대체값 {rate}", flush=True)
            p_ni, p_eq = (v * rate if v is not None else None for v in (p_ni, p_eq))
        ref = self_ref.get(t)
        if p_ni is not None and check_self_relative(p_ni, ref)["triggered"]:
            p_ni, ni_case = None, f"{ni_case}→규모 이상(버림)"
        if p_eq is not None and check_self_relative(p_eq, ref)["triggered"]:
            p_eq, eq_case = None, f"{eq_case}→규모 이상(버림)"
        if p_ni is None and p_eq is None:
            result[(t, fy, q)] = f"전년 동기 값 없음(순이익 {ni_case} · 자본 {eq_case})"
            continue
        rcept = next((r.get("rcept_no") for r in rows if r.get("rcept_no")), "?")
        why = (f"전기 칸 보충(261019) — DART 재무제표 API 에 이 칸의 보고서가 없어(013) 다음 해 같은 분기 보고서({fy + 1} {rc} {fs}, "
               f"접수 {rcept})의 전년 동기 값: 순이익 {ni_case}(전기 누적) · 자본 {eq_case}(자본변동표 전기 말)"
               + (f" · {stmt_cur}→KRW {rcal.cell_end(fy, q, fye):%Y%m%d} 환율" if stmt_cur != "KRW" else ""))
        writes.append((p_ni, p_eq, why, t, fy, q))
        result[(t, fy, q)] = f"채움 순이익 {p_ni} ({ni_case}) · 자본 {p_eq} ({eq_case}) · {fs} 접수 {rcept}"
    for k, v in sorted(result.items()):
        print(f"[전기 칸] {k[0]} {k[1]}Q{k[2]} — {v}", flush=True)
    if writes and not dry:
        with _pg() as con2:
            con2.execute("SET LOCAL lock_timeout = '3s'")
            with con2.cursor() as cur:
                # 원본 칸·상태는 건드리지 않는다. 정정 칸이 그 사이 채워졌으면(다른 실행) 그대로 둔다.
                cur.executemany(
                    "UPDATE dart_finstat_q SET ni_cum_restated=%s, eq_restated=%s, restate_why=%s "
                    "WHERE ticker=%s AND fy=%s AND quarter=%s AND fetched='nodata' AND ni_cum IS NULL AND eq IS NULL "
                    "AND ni_cum_restated IS NULL AND eq_restated IS NULL", writes)
            con2.commit()
    print(f"전기 칸 보충 — 대상 {len(keys)}칸 · {'쓸 칸' if dry else '쓴 칸'} {len(writes)}"
          + (" (--dry: 쓰지 않음)" if dry else ""), flush=True)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true", help="Q4 seed(0콜)")
    ap.add_argument("--pilot", type=str, help="소표본 ticker 콤마구분(전분기 수집·검증)")
    ap.add_argument("--fetch", action="store_true", help="Q1~Q3 백필")
    ap.add_argument("--derive", action="store_true", help="dart_fundamentals 재무 4열 파생(0콜)")
    ap.add_argument("--include-new", action="store_true",
                    help="KRX 신규 상장 보통주를 dart_fundamentals 에 등록(KRX ≤4콜·DART 0콜) — --fetch 앞에 수행")
    ap.add_argument("--years", type=str, help="콤마구분 연도(기본 2019~2026)")
    ap.add_argument("--conc", type=int, default=int(os.getenv("FUND_Q_CONC", "2")),
                    help="동시성(기본 2, CLAUDE.md 허용 1~2)")
    ap.add_argument("--nodata-retry-days", type=int, default=None,
                    help=f"nodata 분기를 다시 받는 기간(공시 마감 뒤 일수, 기본 {NODATA_RETRY_DAYS})")
    ap.add_argument("--refetch", type=str,
                    help="이 칸만 다시 받는다(이미 ok 여도) — 종목:사업연도:분기 콤마구분(예 000540:2026:2). 파서를 고친 뒤 비던 칸만 채울 때")
    ap.add_argument("--prior-from-next", type=str,
                    help="API 에 보고서가 없는 nodata 칸을 다음 해 같은 분기 보고서의 전년 동기 값으로 정정 칸에 채운다 — "
                         "종목:사업연도:분기 콤마구분(예 105560:2023:1). 칸당 1~2콜")
    ap.add_argument("--dry", action="store_true", help="--prior-from-next 를 쓰지 않고 출력만")
    ap.add_argument("--tickers", type=str,
                    help="--derive 를 이 종목만(콤마구분) — 재수집한 종목만 좁혀 파생할 때")
    ap.add_argument("--acc-mt", action="store_true",
                    help="결산월(dart_fundamentals.acc_mt)을 사업보고서 이름의 기준월로 채운다·고친다(DART 목록 ~90콜)")
    ap.add_argument("--acc-mt-since", type=str,
                    help=f"--acc-mt 가 볼 사업보고서 접수 시작일 YYYYMMDD(기본 최근 {ACC_MT_SCAN_DAYS}일 — 처음 채울 땐 20180101)")
    a = ap.parse_args()
    yrs = [int(y) for y in a.years.split(",")] if a.years else YEARS_DEFAULT
    conc = max(1, min(2, a.conc))  # 하드 상한 2(910 한도·CLAUDE.md 준수)
    if a.include_new:
        asyncio.run(include_new_tickers())   # fetch 가 firms 를 DB 에서 다시 읽으므로 같은 실행에서 이어진다
    if a.acc_mt:                             # 결산월 — 신규 등록 뒤·수집 앞(수집 마감을 결산월로 센다)
        _since = a.acc_mt_since
        asyncio.run(refresh_acc_mt(since=date(int(_since[:4]), int(_since[4:6]), int(_since[6:8])) if _since else None))
    if a.seed:
        seed_q4()
    if a.derive:
        derive_fundamentals(a.tickers.split(",") if a.tickers else None)
    if a.prior_from_next:
        asyncio.run(fill_prior_from_next({(t, int(fy), int(qq)) for t, fy, qq in
                                          (x.split(":") for x in a.prior_from_next.split(",") if x)}, dry=a.dry))
    if a.refetch:
        _only = {(t, int(fy), int(qq)) for t, fy, qq in (x.split(":") for x in a.refetch.split(",") if x)}
        asyncio.run(fetch(yrs, conc=conc, only=_only))
    elif a.pilot:
        seed_q4()
        asyncio.run(fetch(yrs, pilot=a.pilot.split(","), conc=conc, nodata_days=a.nodata_retry_days))
    elif a.fetch:
        asyncio.run(fetch(yrs, conc=conc, nodata_days=a.nodata_retry_days))
