"""asset_holdings — 회사 보유 자산(부동산·지분증권·현금성)을 뽑아 시총 대비 청산가치(NAV) 스크리닝.

설계: 자산저평가주 전수조사(private research 260719) + 공시전문가↔밸류투자자 토론(260720).
소스: 연결 BS 계정 API(fnlttSinglAcntAll, 감사·구조화) + 타법인출자현황(otrCprInvstmntSttus) +
      III.재무 주석(asset_valuation 엔진) + 시세(일별 시세 저장분 krx_daily — 시총과 같은 거래일, 못 읽으면 라이브).
      시총 대비는 point-in-time.

스콥(260720 2개로 정리 — 딥하게 다듬은 건 이 둘뿐):
  summary(기본) 계정 API 자산 티어 + 잉여자산 배수 + 지분 NAV(타법인출자현황 합산 — 상장 시가·비상장 장부가) 배수
                + 담보·우발 haircut 플래그
  detail        III.주석 원문 markdown(토지 원가vs공정가치·지분증권 명세·담보제공·우발부채)

원칙: 구조화 가능한 것(계정·타법인출자)은 숫자로, 주석(담보·우발·토지gap)은 markdown-primary(caller 판단).
      금융업(KSIC 64/65/66)은 포트폴리오가 본업이라 summary에서 '금융 리그' 라벨.
"""
from __future__ import annotations
from open_proxy_mcp.clock import today_kst
from open_proxy_mcp.dart.fx import (FALLBACK_WARNING, SRC_YAHOO, fiscal_year_end_date, fx_source, fx_to_krw,
                                  statement_currency)

from open_proxy_mcp.services.contracts import declare_weak_resolution, AnalysisStatus

import asyncio
import calendar
import re
from typing import Any

from open_proxy_mcp.company_resolver import is_inactive_fallback
from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import asset_valuation as _av
from open_proxy_mcp.services.ownership_structure import _dart_read_note

_NORM = re.compile(r"㈜|\(주\)|\(유\)|주식회사|\(재\)|\(사\)")
_WS = re.compile(r"\s+")
_THRESH = 10**8  # 1억 노이즈 컷


def _num(s) -> int:
    if not s or s == "-":
        return 0
    try:
        return int(str(s).replace(",", ""))
    except (ValueError, TypeError):
        return 0


def _norm(nm: str) -> str:
    return _WS.sub("", _NORM.sub("", nm or "")).strip()


def _won(v: int) -> str:
    """경고 문장용 원화 표기 — 1조 이상은 「x.xx조」, 그 밖은 「x억」(렌더러 `_f` 와 같은 꼴)."""
    e = (v or 0) / 1e8
    return f"{e / 1e4:,.2f}조" if abs(e) >= 1e4 else f"{e:,.0f}억"


# ── BS 계정 라인 → 목적·환금성 티어 (전수조사 검증 로직 이식) ──
def _is_current(nm: str) -> bool:
    return ("유동" in nm and "비유동" not in nm) or ("단기" in nm and "장기" not in nm)


#: 자본 쪽 계정을 가르는 낱말. 계정 API 의 재무상태표(sj_div BS)에는 자산·부채·**자본** 줄이 함께 온다.
#: 261018: 「기타포괄손익누계액」(자본)이 「기타포괄」 낱말로 FVOCI 지분(자산) 티어에 들어가 자산으로 더해졌다 —
#:   실측 LG 5.19조·현대차 5.24조가 「우호·제휴 지분」 버킷과 지분 NAV 에 섞였다. 자본 줄은 자산 티어가 아니다.
_EQUITY_WORDS = ("누계액", "잉여금", "적립금", "자본", "비지배", "평가손익", "평가이익", "평가손실")


def _tier(nm: str) -> str | None:
    """BS 계정명 → 티어. 함정: 부채·자본·비금융자산·매출채권 제외."""
    if "부채" in nm or "비금융자산" in nm or "매출채권" in nm:
        return None
    if any(w in nm for w in _EQUITY_WORDS):
        return None
    if "매각예정" in nm or "처분자산집단" in nm:
        return "held_for_sale"                        # IFRS5 near-cash
    if "종속기업" in nm and ("관계기업" in nm or "공동기업" in nm):
        return "mixed"           # 별도FS 결합계정(종속+관계/공동 합산) — 분리불가, NAV엔 별도 보조라인
    if "종속기업" in nm:
        return "subs"
    if "관계기업" in nm or "공동기업" in nm or "지분법" in nm:
        return "assoc"                                # 지배·전략지분 NAV
    if "기타포괄" in nm:
        return "oci"                                  # FVOCI 장기보유
    if "당기손익" in nm:
        return "trading" if _is_current(nm) else "ltinv"   # FVPL 유동/비유동
    if "투자부동산" in nm:
        return "inv_prop"
    if "유형자산" in nm:
        return "tangible"
    if "현금및현금성" in nm or "단기금융상품" in nm:
        return "cash"                                 # 진짜 현금성(예금)
    if "금융자산" in nm or "투자주식" in nm or "장기투자" in nm or "매도가능" in nm:
        if "매도가능" in nm or "투자주식" in nm:
            return "oci"
        if _is_current(nm):
            return "trading"                          # 기타유동금융 = 시장성 유가증권(ETF·주식)
        return "nc_other"
    return None


def _dedupe(lines: list[dict]) -> list[dict]:
    """부모/자식 라인 이중계상 제거(전수조사: 비유동FVPL=기타비유동금융자산 원단위 동일 중복)."""
    spec = {ln["amt"] for ln in lines if ln["amt"] >= _THRESH and any(
        k in ln["nm"] for k in ("당기손익", "기타포괄", "관계기업", "공동기업", "매도가능", "종속기업", "매각예정"))}
    out = []
    for ln in lines:
        nm = ln["nm"]
        generic = nm.startswith("기타") and "금융자산" in nm and not any(
            k in nm for k in ("당기손익", "기타포괄", "관계기업", "공동기업", "매도가능"))
        if generic and ln["amt"] in spec:
            continue
        out.append(ln)
    return out


_TIER_LABEL = {
    "cash": "현금성·단기금융", "trading": "환금투자(FVPL·단기)", "ltinv": "장기투자증권(비유동FVPL)",
    "oci": "전략보유지분(FVOCI)", "assoc": "지배·전략지분(지분법)", "inv_prop": "투자부동산",
    "tangible": "유형자산(토지 포함)", "held_for_sale": "매각예정자산(near-cash)",
    "nc_other": "기타비유동금융", "subs": "종속기업투자(별도FS)",
    "mixed": "결합출자(종속+관계/공동, 미분리)",
}

# ── 목적 버킷 (260721, 회계사 검토·확정 — wiki/decisions/260721_1500_decision_asset-holdings-purpose-buckets.md 참조) ──
# ltinv(FVPL 비유동)은 "장기"라는 이름과 달리 측정·처분목적이 trading과 동일(비유동은 표시분류일
# 뿐, K-IFRS1001§60) → trading과 같은 버킷. oci(FVOCI 지분상품)는 영향력 '없는' 지분(K-IFRS1109
# §5.7.5)이라 assoc/subs(지배력·유의적영향력 전제, K-IFRS1110/1028)와 목적이 반대 → 별도 버킷.
_BUCKET = {
    "cash": "cashlike", "held_for_sale": "cashlike",
    "trading": "trading_sec", "ltinv": "trading_sec",
    "oci": "friendly_stake",
    "assoc": "control_stake", "subs": "control_stake", "mixed": "control_stake",
    "inv_prop": "real_estate",
    "tangible": "core_biz", "nc_other": "unclassified",
}
_BUCKET_ORDER = ["cashlike", "trading_sec", "friendly_stake", "control_stake",
                "real_estate", "core_biz", "unclassified"]
_BUCKET_LABEL = {
    "cashlike": "현금성 자산", "trading_sec": "환금성 증권(재테크형)",
    "friendly_stake": "우호·제휴 지분", "control_stake": "지배·관계사 지분",
    "real_estate": "투자용 부동산", "core_biz": "본업 자산(참고)", "unclassified": "기타(미분류)",
}
_BUCKET_DESC = {
    "cashlike": "즉시 현금화 가능",
    "trading_sec": "언제든 팔 수 있는 주식·펀드 — 트레이딩 목적(만기 1년 초과분 포함)",
    "friendly_stake": "영향력 없는 지분관계 유지 목적(상호출자 등), 지배력과 무관",
    "control_stake": "경영권·유의적 영향력 보유 — 팔기 어려움(지주사 할인 요인)",
    "real_estate": "본업과 무관한 임대·시세차익 목적 부동산",
    "core_biz": "회사가 직접 쓰는 자산(자가사용) — 스크리닝 대상 아님",
    "unclassified": "목적 분류 불가(소액·특수계정)",
}
_NARRATIVE = {
    "trading_sec": "재테크형 — 트레이딩 가능한 주식·펀드 비중이 큽니다",
    "real_estate": "부동산 자산주형 — 본업과 무관한 투자부동산이 큽니다",
    "control_stake": "지주사 할인형 — 자회사·관계사 지분이 큽니다",
    "friendly_stake": "우호지분형 — 경영권과 무관한 상호출자성 지분이 큽니다",
}


def _asset_story(bucket_totals: dict[str, int], mcap: int | None) -> str | None:
    """가장 큰 버킷(본업·미분류 제외)을 시총 대비로 판단해 자산 성격 한 줄 서사 생성.
    시총 15% 미만이면 특별한 서사 없음(잡음 방지)."""
    candidates = [(k, v) for k, v in bucket_totals.items() if k in _NARRATIVE and v]
    if not candidates:
        return None
    top_k, top_v = max(candidates, key=lambda x: x[1])
    if mcap and top_v < mcap * 0.15:
        return None
    return _NARRATIVE[top_k]


def _bs_tiers(fin_list: list[dict]) -> tuple[dict[str, int], list[dict]]:
    lines = []
    for x in fin_list:
        nm = (x.get("account_nm") or "").strip()
        if x.get("sj_div") != "BS" or any(w in nm for w in _EQUITY_WORDS):
            continue                                      # 자본 줄은 중복 제거의 기준 금액에도 넣지 않는다
        lines.append({"nm": nm, "amt": _num(x.get("thstrm_amount"))})
    tiers: dict[str, int] = {}
    kept = []
    for ln in _dedupe(lines):
        t = _tier(ln["nm"])
        if t and ln["amt"]:
            tiers[t] = tiers.get(t, 0) + ln["amt"]
            kept.append({**ln, "tier": t})
    return tiers, kept


# ── 상장 투자사명 → 종목코드 후보 (공용 기업 마스터 해석기) ──
# 261018: 종전엔 `<레포>/configs/master.db` 를 **경로를 박아** 직접 열었다. 운영 이미지엔 그 파일이 없고
#   기업 마스터는 볼륨(`OPM_MASTER_DB_PATH=/data/master.db`)에 있어, 여는 데 실패 → 빈 사전 → **모든 상장지분이
#   조용히 미해결**이었다(운영 실측 삼성물산 0/12·SK 0/13·LG 0/7 평가, 경고 0). 로컬 체크아웃엔 그 파일이 있어 재현되지 않았다.
#   이제 서버가 이미 메모리에 올린 기업 마스터(`lookup_corp_code_all`)로 찾는다 — 경로 설정을 한 곳만 따른다.
#   덤으로 공식명과 다른 표기(「삼성생명보험」→삼성생명, 「엘지전자」→LG전자, 「KCC」→케이씨씨)도 같은 해석기 규칙으로 잡힌다.
#   `resolve_company_query` 는 쓰지 않는다 — 그 관문은 「이 요청이 조사한 기업」 장부와 추정 경고에 적어서, 보유지분 이름이
#   응답 맨 앞 「추정으로 골랐다」 문구나 조사 기업 집계에 섞인다. 후보 목록 함수는 적지 않는다.
_CODES_PER_NAME = 3      # 동명 상장 후보(상장폐지 뒤 코드가 남은 옛 법인 포함) 몇 개까지 시세로 가려 볼지


async def _stake_codes(client, name: str) -> tuple[list[str], str | None]:
    """보유 법인명 → 상장 종목코드 후보들, 못 찾았으면 ([], 사유).

    후보가 여럿일 수 있다 — DART 는 상장폐지 뒤에도 종목코드를 지우지 않아 「SK」「삼성물산」은 옛 법인 코드가 함께 나온다
    (기업 마스터 실측: 정규화 이름 충돌 30건). 여기서 고르지 않고 **그 거래일 시세가 있는 코드**로 가린다(`_mark_listed_stakes`).
    이름이 정확히 맞지 않아 추정으로만 잡힌 후보는 받지 않는다 — 다른 회사 시세로 평가하느니 장부가로 두는 편이 낫다."""
    try:
        matches = await client.lookup_corp_code_all(name)
    except Exception:                                   # noqa: BLE001 — 해석 실패가 응답 전체를 깨면 안 된다
        return [], "종목코드 조회 실패"
    listed = [m for m in (matches or []) if (m.get("stock_code") or "").strip()]
    # 261018: 활성 회사엔 맞는 이름이 없어 비활성 회사 하나로 고른 결과(`is_inactive_fallback`)는 **못 찾은 것과 같다** — 그 법인은
    #   최신 거래일 시세가 없어 평가할 수 없고, 「추정 후보」로 달면 해외 상장 법인(현대차 보유 「HMI」)에 이름 조각이 겹치는
    #   옛 법인(케이에이치미래물산)을 붙이는 오해만 남는다.
    if not listed or is_inactive_fallback(listed):
        return [], "국내 상장 종목코드를 찾지 못함(해외 상장·상장폐지이거나 공식 상호와 표기가 다름)"
    if (listed[0].get("_resolution") or {}).get("inferred"):
        return [], f"상호가 정확히 맞지 않음(추정 후보: {listed[0].get('corp_name')})"
    codes: list[str] = []
    for m in listed:
        if (m.get("_resolution") or {}).get("inferred"):
            continue
        code = m["stock_code"].strip()
        if code not in codes:
            codes.append(code)
    return codes[:_CODES_PER_NAME], None


#: 시세 출처 값(`listed_stakes.price_source`) — 시세 tool(`trading_data` quote 의 `data.source`)·가격 배수 서비스와 같은 어휘.
#: 261018: 종전엔 저장소 표 이름(`krx_daily`)·`krx` 를 값으로 냈다 — 같은 개념을 두 tool 이 다른 값으로 내지 않게 맞춘다.
_PX_DB = "db"                 # 일별 시세 저장분(서버가 매 거래일 보관한 KRX 응답)
_PX_LIVE = "krx_live"         # 저장분에서 받지 못해 요청 경로에서 KRX 를 직접 불러 받은 값
_PX_PORTAL = "portal"         # 포털 시세(KRX 키 없음·장애 때 종목별로만)
_PX_PER_STOCK = {"krx": _PX_LIVE, "portal": _PX_PORTAL}   # `client.get_stock_price` 의 source → 위 어휘


def _db_quotes(day: str, codes: list[str]) -> dict[str, tuple[int, int]] | None:
    """일별 시세 저장분(`krx_daily`, 코스피·코스닥 전 종목)에서 그 거래일의 (종가, 상장주식수) — 종목 여럿을 한 번 왕복으로.

    상장주식수는 같은 행에서 함께 읽는다 — 기말 뒤 주식수 변동(분할·병합)을 가릴 때 **종가와 같은 날** 의 주식수가 필요하다.
    None = DB 미설정·장애(호출자가 라이브로 간다). 빈 사전 = 그날 그 종목들이 저장분에 없다."""
    from open_proxy_mcp.db import pg_rows
    rows = pg_rows("SELECT ticker, close, list_shrs FROM krx_daily WHERE trd_dd = %s AND ticker = ANY(%s)",
                   (day, codes))
    if rows is None:
        return None
    return {r[0]: (_num(r[1]), _num(r[2])) for r in rows if _num(r[1])}


def _db_share_adjust(codes: list[str], qty_dd: str, upto_dd: str) -> tuple[dict[str, float], dict[str, int]] | None:
    """보유수량 기준일(`qty_dd`) 뒤 ~ 종가 거래일(`upto_dd`)의 수정계수와, 기준일 시점의 상장주식수 — 종목 여럿을 한 번에.

    · 수정계수: `krx_adj_events` 의 조정성 이벤트(가격 배수 서비스 `ADJ_EVENT_SQL` 과 같은 조건 — 분할·병합·무상증자·주식배당,
      확정 라벨). 종목별 누적곱이다(1:5 분할 = 0.2 — 가격 방향). 보유수량은 이것으로 **나눠** 오늘 주식수 기준으로 맞춘다.
    · 기준일 상장주식수: `krx_weekly` 에서 기준일 **이전** 마지막 주. 계수가 빠진 이벤트를 잡는 데 쓴다(오늘 주식수 ÷ 이것).
    None = DB 장애(확인 못 함)."""
    from open_proxy_mcp.db import pg_rows
    ev = pg_rows("SELECT ticker, adj_factor FROM krx_adj_events WHERE ticker = ANY(%s) AND event_dd > %s "
                 "AND event_dd <= %s AND " + _val.ADJ_EVENT_SQL, (codes, qty_dd, upto_dd))
    base = pg_rows("SELECT DISTINCT ON (ticker) ticker, list_shrs FROM krx_weekly WHERE ticker = ANY(%s) "
                   "AND price_dd <= %s AND list_shrs > 0 ORDER BY ticker, price_dd DESC", (codes, qty_dd))
    if ev is None or base is None:
        return None
    factors: dict[str, float] = {}
    for t, x in ev:
        if x and float(x) > 0:
            factors[t] = factors.get(t, 1.0) * float(x)
    return factors, {t: _num(s) for t, s in base if _num(s)}


def _prev_trading_day() -> str | None:
    """오늘 이전의 가장 가까운 평일 — KRX 일별 시세는 다음 날 게시라 오늘 값은 없다(공휴일은 빈 응답으로 걸러진다)."""
    today = today_kst().strftime("%Y%m%d")
    return next((d for d in _val._price_dates() if d < today), None)


def _doc_listed(doc_text: str, names: list[str]) -> dict[str, bool]:
    """원문 doc에서 투자사명 인접 상장/비상장(권위값 — name-join 동명이인 오판 방지)."""
    dt = _WS.sub("", _NORM.sub("", doc_text or ""))
    out = {}
    for nm in names:
        n = _norm(nm)
        m = re.search(re.escape(n) + r"(비?상장)", dt) if n else None
        out[n] = (m.group(1) == "상장") if m else False
    return out


def _rate(s) -> float:
    """지분율 문자열(「51.70」「100」「-」) → 숫자. 못 읽으면 0."""
    try:
        return float(str(s).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return 0.0


_MARK_CAP = 40          # 이름 해석·평가 대상 상한(장부가 큰 순) — 시세는 DB 한 번 왕복이라 콜이 아니라 해석 시간을 막는 상한
_LIVE_PX_CAP = 12       # 종목별 라이브 시세 콜 상한 — DB·KRX 전종목 스냅샷을 둘 다 못 얻었을 때만 쓴다(종전 「콜 상한 12」)
#: 타법인출자현황의 합계 줄 이름 — 개별 보유분이 아니다. 261018: 원문에 「합계 … 상장」 이 붙어 있으면 상장 후보로 들어가
#:   합계 장부가가 한 번 더 더해졌다(실측 한화: 합계 5.96조가 「보유수량 없음」 미해결로 상장지분 장부가·시가 양쪽에).
_TOTAL_ROWS = {"합계", "계", "소계", "총계"}


async def _mark_listed_stakes(client, holdings: list[dict], doc_text: str,
                              price_dd: str | None = None, qty_dd: str | None = None) -> dict[str, Any]:
    """타법인출자현황(별도 기준 보유지분)을 상장은 시가(보유수량×종가), 비상장은 장부가로 나눠 센다.
    sanity(장부>피투자사 총자산 제외)·상장 여부는 원문 doc 권위값. `qty_dd` = 보유수량 기준일(보고 기간 말).

    261018 재작성(운영에서 상장지분이 하나도 평가되지 않던 결함):
      · 이름 → 종목코드: 서버가 쓰는 기업 마스터 해석기(`_stake_codes`). 종전 경로 박은 sqlite 는 운영에 없었다.
      · 종가: **시총과 같은 원천·같은 거래일**(`price_dd` = 시총 기준일, 없으면 서빙 기준일)의 일별 시세 저장분을 한 번에 읽는다.
        종전엔 종목마다 KRX 를 **오늘 날짜**로 불렀는데 KRX 일별 시세는 다음 날 게시라 늘 빈 응답이었고(주말도 빈 응답),
        그 엔드포인트는 코스피만 담아 코스닥 보유지분은 날짜가 맞아도 못 찾았다. 결국 포털 시세로 떨어졌고, 포털 종가는
        거래소 공식 종가와 다를 수 있다(실측 20261016 삼성전자 포털 217,000 · KRX 215,500 — 같은 응답의 시총은 KRX 값).
      · DB 를 못 읽으면 KRX 전종목 스냅샷(가격 배수 서비스와 같은 캐시 — 시총을 라이브로 구했다면 같은 날짜라 추가 콜 0),
        그것도 없으면(키 없음·장애) 종목별 시세(`get_stock_price`, 상한 `_LIVE_PX_CAP`)로 간다.
      · 기말 뒤 액면분할·병합·무상증자: 보유수량은 보고 기간 말 기준인데 종가는 오늘 기준이다. 수정계수로 보유수량을 오늘 주식수
        기준으로 맞춘다(실측 LG 의 엘에스일렉트릭 690,167주 — 20260413 1/5 분할 뒤 종가로 곱해 시가 1,094억, 실제 약 5,473억).
        계수는 없는데 상장주식수가 바뀌었으면(계수 파이프라인 지연·감자) 평가하지 않고 사유와 함께 장부가로 둔다.
      · 못 평가한 건은 이름·장부가·사유를 `unresolved` 에 싣는다 — 종전엔 개수만 남아 「왜 0건인가」를 알 수 없었다.
      · `n_listed` 와 합계가 같은 집합을 센다 — 종전엔 상한(12) 밖 후보가 개수에만 들고 합계에서 빠졌다(SK 13 vs 12).
      · 비상장 보유분의 장부가 합(`unlisted_book_krw`)도 함께 낸다 — 지분 NAV(보유지분 합산)의 나머지 절반이다."""
    listed_map = _doc_listed(doc_text, [h.get("inv_prm") or "" for h in holdings])
    cand = []
    unlisted_book = unlisted_major = n_unlisted = 0
    for h in holdings:
        key = _norm(h.get("inv_prm") or "")
        if key in _TOTAL_ROWS:                           # 합계 줄 — 개별 보유분이 아니다
            continue
        book = _num(h.get("trmend_blce_acntbk_amount"))
        qty = _num(h.get("trmend_blce_qy"))
        ta = _num(h.get("recent_bsns_year_fnnr_sttus_tot_assets"))
        if ta > 0 and book > ta * 1.05:                # sanity: 필링 자릿수오류 제외
            continue
        if not book and not qty:                       # 기중 전량 처분(기말 수량·장부가 0) — 셀 보유분이 없다
            continue
        if listed_map.get(key):
            cand.append((book, h))
        else:
            unlisted_book += book
            n_unlisted += 1
            if _rate(h.get("trmend_blce_qota_rt")) > 50:
                unlisted_major += book
    cand.sort(key=lambda x: x[0], reverse=True)  # book 동률 시 dict 2차비교로 TypeError 방지(260721)

    # 1) 이름 → 종목코드 후보
    rows: list[dict[str, Any]] = []
    for i, (book, h) in enumerate(cand):
        nm = (h.get("inv_prm") or "").strip()            # 원자료에 꼬리 공백이 붙어 온다(「SK이노베이션㈜ 」)
        qty = _num(h.get("trmend_blce_qy"))
        codes, why = [], None
        if i >= _MARK_CAP:
            why = f"평가 상한 {_MARK_CAP}종 밖(장부가 작은 순)"
        elif not qty:
            why = "보유수량 없음"
        else:
            codes, why = await _stake_codes(client, nm)
        rows.append({"name": nm, "book": book, "qty": qty, "codes": codes, "why": why})

    # 2) 종가 — 일별 시세 저장분 → KRX 전종목 스냅샷 → 종목별 시세(콜 상한)
    want = sorted({c for r in rows for c in r["codes"]})
    px: dict[str, int] = {}
    px_dd: dict[str, str] = {}
    now_shrs: dict[str, int] = {}   # 종가와 같은 날의 상장주식수(저장분·전종목 스냅샷에서만 — 종목별 시세엔 없다)
    day, source = price_dd, None
    whole_day = False          # True = 그날 전 종목을 본 결과(DB·전종목 스냅샷) — 없으면 「그날 시세 없음」이 사실이다
    tried: set[str] = set()    # 종목별 라이브로 실제 물어본 코드
    if want:
        if not day:
            # 시총을 못 구했을 때 — 서빙 기준일은 가격 배수 서비스의 정의 한 곳을 따른다(이 파일에 두 번째 정의를 두지 않는다)
            day = await _val._serving_price_dd()
        db = await asyncio.to_thread(_db_quotes, day, want) if day else None
        if db:
            px = {c: v[0] for c, v in db.items()}
            now_shrs = {c: v[1] for c, v in db.items() if v[1]}
            source, whole_day = _PX_DB, True
        else:
            # DB 장애 또는 그날 저장분에 하나도 없음 → 라이브. 날짜는 시총 기준일, 없으면 직전 거래일.
            day = day or _prev_trading_day()
            snap = await _val._krx_market_live(day) if day else {}
            if snap:
                for c in want:
                    if c in snap and _num(snap[c].get("TDD_CLSPRC")):
                        px[c] = _num(snap[c].get("TDD_CLSPRC"))
                        if _num(snap[c].get("LIST_SHRS")):
                            now_shrs[c] = _num(snap[c].get("LIST_SHRS"))
                source, whole_day = _PX_LIVE, True
            else:
                for r in rows:                             # 장부가 큰 순 — 상한 안에서 큰 것부터
                    if not r["codes"] or len(tried) >= _LIVE_PX_CAP:
                        continue
                    code = r["codes"][0]                   # 해석기 순위 1위(활성 종목 우선) — 콜을 아끼려 하나만
                    if code in tried:                      # 같은 법인이 두 줄(보통·우선 등)이면 한 번만 묻는다
                        continue
                    tried.add(code)
                    try:
                        p = await client.get_stock_price(code, day or today_kst().strftime("%Y%m%d"))
                    except Exception:                      # noqa: BLE001 — 시세 하나 실패가 응답 전체를 깨면 안 된다
                        p = None
                    if p and _num(p.get("closing_price")):
                        px[code] = _num(p.get("closing_price"))
                        px_dd[code] = str(p.get("base_date") or day or "")
                        source = source or _PX_PER_STOCK.get(p.get("source"), p.get("source"))
        if whole_day:
            px_dd = {c: day for c in px}

    # 3) 기말 뒤 주식수 변동 — 수정계수·기준일 상장주식수(한 번에). 못 읽으면 확인 못 한 채 보유수량 그대로 쓴다(경고로 밝힌다).
    adj = None
    if px and qty_dd:
        upto = day or max(px_dd.values(), default="") or today_kst().strftime("%Y%m%d")
        adj = await asyncio.to_thread(_db_share_adjust, sorted(px), qty_dd, upto)
    factors, base = adj if adj else ({}, {})
    unl_basis: set[str] = set()
    if adj is not None:
        # 261021(H4): 확정 라벨이 없어도 상장주식 수가 계수의 역수만큼 바뀐 조정(라벨 원천이 멎은 2026-06·07 뒤의 분할·병합·무상증자 — 모두 unlabeled·dart)을
        #   곱한다 — 공시 EPS 정렬·배당수익률 맞춤과 같은 판정(`price_multiple_data._unlabeled_share_events`). 종전엔 그런 종목이 아래 불변식에서 「확정된
        #   수정계수가 없다」로 걸려 시가 평가에서 빠졌다. 읽기 실패는 종전 계수 그대로다.
        unl = await _val._unlabeled_share_events(sorted(px), qty_dd, upto)
        if unl:
            factors = dict(factors)                       # 호출한 쪽 사전을 바꾸지 않는다
            for t, evs in unl.items():
                factors[t] = factors.get(t, 1.0) * _val._adj_events_factor(evs, qty_dd)
                unl_basis.add(t)

    # 4) 평가 — 동명 후보는 그날 시세가 있는 코드 하나로 가린다
    marked, unresolved, book_sum, mkt_sum = [], [], 0, 0
    for r in rows:
        book_sum += r["book"]
        priced = [c for c in r["codes"] if px.get(c)]
        why = r["why"]
        if why is None and len(priced) == 1:
            code = priced[0]
            f = factors.get(code, 1.0)
            ratio = (now_shrs[code] / base[code]) if (now_shrs.get(code) and base.get(code)) else None
            # 불변식(가격 배수 서비스와 같은 밴드): 조정성 이벤트는 주가와 주식수가 상쇄해 계수 × 주식수 배율 ≈ 1.
            # 벗어나면 계수가 빠졌거나(파이프라인 지연) 계수 대상이 아닌 감자·대규모 증자다 — 보유수량을 확정할 수 없다.
            if ratio is not None and not (_val._ADJ_INVARIANT_LO <= f * ratio <= _val._ADJ_INVARIANT_HI):
                why = (f"보유수량 기준일({qty_dd}) 뒤 상장주식수가 {ratio:.2f}배가 됐는데 "
                       + ("확정된 수정계수가 없다" if f == 1.0 else f"확정된 수정계수({f:g})와 맞지 않는다")
                       + " — 분할·병합·감자로 보유수량이 바뀌었을 수 있다")
            else:
                qty_now = round(r["qty"] / f) if f != 1.0 else r["qty"]
                mkt = qty_now * px[code]
                mkt_sum += mkt
                item = {"name": r["name"], "code": code, "book_krw": r["book"], "mkt_krw": mkt,
                        "gap_krw": mkt - r["book"], "px": px[code], "px_date": px_dd.get(code), "qty": r["qty"]}
                if f != 1.0:
                    item["qty_adj"] = qty_now              # 기말 수량 ÷ 수정계수 = 오늘 주식수 기준 수량
                    item["adj_factor"] = round(f, 6)
                    if code in unl_basis:
                        item["adj_basis"] = "listed_shares_ratio"   # 261021(H4): 계수 일부는 확정 라벨이 아니라 상장주식 수 배율로 확인한 조정
                marked.append(item)
                continue
        if why is None:
            if len(priced) > 1:
                why = f"같은 상호의 상장 종목이 여럿({', '.join(priced)}) — 어느 쪽인지 확정 못 함"
            elif whole_day:
                why = f"{day} 거래일 시세 없음(코스피·코스닥 밖 시장이거나 상장폐지)"
            elif r["codes"] and r["codes"][0] not in tried and tried:
                why = f"라이브 시세 조회 상한 {_LIVE_PX_CAP}종 밖(시세 저장분을 읽지 못함)"
            else:
                why = "시세 조회 실패(시세 저장분·거래소·포털 모두 응답 없음)"
        mkt_sum += r["book"]                              # 미해결은 장부가 유지(보수적)
        unresolved.append({"name": r["name"], "book_krw": r["book"], "reason": why})
    dates = {m["px_date"] for m in marked}
    return {"marked": marked, "unresolved": unresolved,
            # 평가 기준일 — 모두 같은 날이면 그 날, 종목별 라이브로 섞였으면 None(항목별 px_date 를 본다)
            "price_date": dates.pop() if len(dates) == 1 else None,
            "price_source": source if marked else None,
            "qty_date": qty_dd,
            # 기말 뒤 분할·병합 확인 여부 — False 면 시가를 낸 종목의 보유수량을 확인 없이 기말 그대로 썼다
            "qty_adjust_checked": (adj is not None) if marked else None,
            "listed_book_krw": book_sum, "listed_mkt_krw": mkt_sum,
            "unrealized_gap_krw": mkt_sum - book_sum, "n_listed": len(cand),
            "n_marked": len(marked), "n_unresolved": len(unresolved),
            "unlisted_book_krw": unlisted_book, "unlisted_majority_book_krw": unlisted_major,
            "n_unlisted": n_unlisted}


from open_proxy_mcp.dart.client import get_dart_client  # noqa: E402
from open_proxy_mcp.services import business_details as _bd  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as _val  # noqa: E402
from open_proxy_mcp.services.company import (COMPANY_LOOKUP_NEXT_ACTION,  # noqa: E402
                                             _safe_company_info,
                                             company_ambiguous_warning,
                                             company_not_found_warning,
                                             resolve_company_query)

_YEAR = re.compile(r"\((\d{4})\.\d{2}\)")
_PERIOD = re.compile(r"\((\d{4})\.(\d{2})\)")


def _period_end(report_nm: str | None) -> str | None:
    """보고서 이름(「사업보고서 (2025.12)」)의 기간 말 날짜 YYYYMMDD — 타법인출자현황 보유수량의 기준일. 못 읽으면 None."""
    m = _PERIOD.search(report_nm or "")
    if not m or not 1 <= int(m.group(2)) <= 12:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    return f"{y}{mo:02d}{calendar.monthrange(y, mo)[1]:02d}"


#: 일반 지주회사 업종코드(KSIC 64992 — 금융지주도 같은 코드라 금융업 판정이 아닌 것만 일반 지주회사로 본다).
_HOLDCO_KSIC = "64992"


def _is_financial(induty: str, fin_list: list[dict]) -> bool:
    """금융업인가 — `financial_metrics` 와 **같은 규칙**을 쓴다(공유 파생지표 재사용, 260713 검증 규칙).

    261018: 종전엔 업종코드 앞 두 자리(64/65/66)만 봐서 **일반 지주회사(64992, SK·LG)** 가 금융업으로 갈렸다 —
      KSIC 는 금융지주와 일반지주를 같은 64992 에 넣는다. 그래서 지주사 할인을 보라고 만든 이 tool 이 정작 SK·LG 에
      「금융업 — 배수 미제공」을 냈다(운영 실측 SK·LG `is_financial` true, 지분 NAV 배수 null).
      규칙: 연결(없으면 별도) 재무상태표에 예수부채·보험계약부채·책임준비금·고객예탁금·매도파생결합 계정이 있으면 금융업
      (금융지주는 은행·보험 자회사 연결로 이 계정이 뜬다), 아니면 업종코드 64/65/66 중 64992 를 뺀 것."""
    from open_proxy_mcp.services.financial_metrics import _FIN_BS_SIGNAL, _ksic_is_financial
    for x in fin_list or []:
        if x.get("sj_div") != "BS":
            continue
        nm = (x.get("account_nm") or "").replace(" ", "")
        if any(sig in nm for sig in _FIN_BS_SIGNAL):
            return True
    return _ksic_is_financial(induty)


def _stakes_unavailable(reason: str = "주석 원문 조회 실패 — 상장·비상장 구분을 할 수 없어 값을 비웠다") -> dict[str, Any]:
    """주석 원문을 못 읽어 상장·비상장 구분을 할 수 없을 때의 보유지분 마크 — `_mark_listed_stakes` 와 같은 키, 값은 비운다.
    출자현황(타법인 보유 목록) 자체를 못 읽었을 때도 같은 길이다(`reason` 만 다르다) — 보유 목록 0건으로 읽지 않는다.

    그 함수는 상장 여부를 **원문 주석**(`_doc_listed`)으로 가른다. 원문이 비면 모든 보유분이 비상장으로 분류돼 상장 지분이
    장부가로 비상장 합계에 들어가고 상장 시가·미실현 차액이 0 이 된다 — 「조회 실패」가 「상장 지분 0」으로 계산되는 것이다(DATA-U3).
    그래서 이 경우는 그 함수를 부르지 않고 합계·개수를 `None`(모름)으로 둔다."""
    return {"marked": [], "unresolved": [], "price_date": None, "price_source": None, "qty_date": None,
            "qty_adjust_checked": None, "listed_book_krw": None, "listed_mkt_krw": None, "unrealized_gap_krw": None,
            "n_listed": None, "n_marked": None, "n_unresolved": None, "unlisted_book_krw": None,
            "unlisted_majority_book_krw": None, "n_unlisted": None, "unavailable": True,
            "unavailable_reason": reason}


def _origin_tail(rcept_no, title: str) -> str:
    """원문 위치 안내를 문장 뒤에 붙일 조각. 확장 훅(origin_hint)이 한 줄을 주면 앞에 공백을 두고 돌려주고,
    확장이 없거나 빈 문자열이면 ""(공개 코드는 확장 도구의 호출문을 직접 쓰지 않는다)."""
    from open_proxy_mcp.extensions import origin_hint
    hint = origin_hint(rcept_no or "", title)
    return f" {hint}" if hint else ""


async def _safe_getdoc(client, rcept_no: str) -> dict:
    """get_document(1콜) → note_html 비면/014 시 viewer 폴백(정정보고서 대응, business_details와 동일).

    viewer 폴백까지 실패하면 예외를 삼키고 빈 주석을 돌려주되 `fetch_failed=True` 를 붙인다 — 호출부가
    「조회 실패」(모름)와 「주석이 비었다」(없음)를 가르게 한다(DATA-U3). viewer 가 응답했으면(내용이 비어도) 실패가 아니다."""
    try:
        sec = await _bd._fetch_getdoc(client, rcept_no)
        if (sec.get("note_html") or "").strip():
            return sec
    except DartClientError as e:
        if str(getattr(e, "status", "")) in ("020", "011", "012"):
            raise
    try:
        return await _bd._fetch_viewer_sec(client, rcept_no)
    except (DartClientError, Exception):  # noqa: BLE001
        return {"note_html": "", "full_text": "", "fetch_failed": True}


async def _market_cap(stock_code: str):
    """시총 = valuation tool과 동일 소스(`_market_for` — 일별 시세 저장분의 최신 거래일, 상장주식수 기준) 재사용 — DART 0콜, Postgres 캐시.
    261018: 출처가 krx_weekly 이번 주 행 → 일별 저장분(`price_latest`)으로 옮겨졌다(값은 같다). 이 함수는 그대로다.
    260721 버그수정: 이전엔 DART 유통주식수(distb_stock_co, 자기주식 제외)×종가로 자체계산해
    관행적 '시가총액'(상장주식수 전체 기준, valuation/포털/KRX 표준)보다 자기주식 비율만큼
    과소산출됐다(서희건설 실측: 유통 1.85억주 vs 상장 2.30억주 — 시총 19% 과소, 잉여자산배수 왜곡).
    실패 시 (None, 사유)."""
    if not stock_code:
        return None, {"reason": "종목코드 없음"}
    mk = await _val._market_for(stock_code)
    if not mk.get("price") or not mk.get("common_mktcap"):
        return None, {"reason": "KRX 시세/시총 조회 실패"}
    return mk["common_mktcap"], {"shares": mk.get("list_shrs"), "close": mk["price"], "date": mk.get("date")}


#: report 인자 → (최신 후보 period 라벨, 사업연도 지정 시 reprt_code 순서). filing_section(opm-ext)과 같은 어휘.
_REPORT_KIND = {
    "annual": ("annual", ["11011"]), "half": ("half", ["11012"]),
    "quarter": ("quarter", ["11014", "11013"]), "q1": ("quarter", ["11013"]), "q3": ("quarter", ["11014"]),
    "latest": ("latest", ["11011", "11012", "11014", "11013"]),
}
_CODE_BY_NAME = (("사업보고서", "11011"), ("반기보고서", "11012"), ("분기보고서", None))


def _reprt_code_of(rept: dict, fallback: str = "11011") -> str:
    """보고서 이름 → DART reprt_code. 분기보고서는 기수 라벨 월로 1분기(11013)/3분기(11014)를 가른다
    (12월 결산 기준 — 결산월이 다르면 호출 쪽이 빈 결과에서 다른 코드로 한 번 더 간다)."""
    nm = rept.get("report_nm") or ""
    for token, code in _CODE_BY_NAME:
        if token in nm:
            if code:
                return code
            m = re.search(r"\((\d{4})\.(\d{2})\)", nm)
            month = int(m.group(2)) if m else 3
            return "11013" if month <= 6 else "11014"
    return fallback


async def _pick_periodic_report(client, corp_code: str, report: str = "annual", year: int = 0) -> tuple[dict | None, str, str]:
    """(보고서, reprt_code, 오류메시지). 260907: 종전엔 사업보고서 첫 후보 고정이라 「2024년 말 자산」「반기 기준
    담보」를 못 잡았고 `[첨부정정]`(첨부만 고친 것, 본문 없음)을 집을 수 있었다. filing_section 과 같은 규칙으로 맞춘다."""
    kind = _REPORT_KIND.get((report or "annual").strip().lower())
    if kind is None:
        return None, "", f"report 는 annual·half·quarter·q1·q3·latest 중 하나 (받은 값: {report})"
    period, codes = kind
    if year:
        reps = []
        for code in codes:
            reps += await _bd._find_report_for_bsns_year(client, corp_code, str(year), code)
        reps.sort(key=lambda r: r.get("rcept_dt", ""), reverse=True)
    else:
        reps = await _bd._find_report_candidates(client, corp_code, period)
    reps = [r for r in reps if "첨부정정" not in (r.get("report_nm") or "")]
    if not reps:
        return None, "", f"{year or '최신'} {report} 정기보고서 없음"
    rept = reps[0]
    return rept, _reprt_code_of(rept, codes[0]), ""


async def _build_asset_holdings_payload_impl(company: str, scope: str = "summary",
                                       format: str = "md", report: str = "annual", year: int = 0) -> dict[str, Any]:
    client = get_dart_client()
    q = (company or "").strip()
    if not q:
        return {"tool": "asset_holdings", "status": "invalid", "subject": company,
                "warnings": ["회사명 또는 종목코드(6자리)를 입력하세요."]}
    if scope not in ("summary", "detail"):
        scope = "summary"
    res = await resolve_company_query(q)
    if not res.selected:
        # 이 파일의 반환 계약은 ToolEnvelope 가 아니라 생 dict 다(status 도 문자열) — 그대로 두고
        # **문구만** 정본으로 바꾼다. 후보가 여럿이면 그 후보를 버리지 않고 싣는다.
        _amb = res.status == AnalysisStatus.AMBIGUOUS
        return {"tool": "asset_holdings", "status": "ambiguous" if _amb else "not_found",
                "subject": company,
                "data": {"query": company,
                         "candidates": [c for c in (res.candidates or [])][:10]},
                "next_actions": [COMPANY_LOOKUP_NEXT_ACTION],
                "warnings": [company_ambiguous_warning(company, res.candidates) if _amb
                             else company_not_found_warning(company)]}
    corp = res.selected
    cc, isu = corp["corp_code"], corp.get("stock_code") or corp.get("ticker")
    name = corp.get("corp_name") or company
    warnings: list[str] = []

    rept, reprt_code, err = await _pick_periodic_report(client, cc, report, int(year or 0))
    if rept is None:
        return {"tool": "asset_holdings", "status": "no_filing" if "없음" in err else "invalid", "subject": name,
                "warnings": [err]}
    year = (_YEAR.search(rept.get("report_nm") or "") or [None, str(today_kst().year - 1)])[1]
    if reprt_code != "11011":
        warnings.append(f"{rept.get('report_nm', '').strip()} 기준 — 분기·반기보고서는 사업보고서보다 주석 항목이 얇아 "
                        "토지 공정가치·담보·우발 명세가 없을 수 있다.")

    data: dict[str, Any] = {"company": name, "ticker": isu, "report_nm": rept.get("report_nm"),
                            "rcept_no": rept.get("rcept_no"), "year": year, "reprt_code": reprt_code, "scope": scope}

    # 013(자료 없음)이 아닌 조회 실패의 안내문(재무·회사개황·출자현황) — 하나라도 있으면 status 는 partial. 판정·문구는 `_dart_read_note` 하나.
    read_failed: list[str] = []

    async def _fin_acnt(fs):
        try:
            return await client.get_fnltt_singl_acnt_all(cc, year, reprt_code, fs)
        except DartClientError as exc:
            failed, note = _dart_read_note(exc, empty_text="", fail_text="재무제표 계정 조회 실패")
            if failed:
                read_failed.append(note)
            return {}

    # ── summary: 연결 BS 티어 + 시가마크 + haircut + 시총 대비 ──
    if scope == "summary":
        fin = await _fin_acnt("CFS")
        fs_div = "CFS"
        if not fin.get("list") and not read_failed:
            # 연결이 013(없음)일 때만 별도로 넘어간다 — 연결이 있는 회사를 조회 실패로 못 읽고 별도로 받으면 기준이 섞인다.
            fin = await _fin_acnt("OFS")
            fs_div = "OFS" if fin.get("list") else None
        if fs_div == "OFS":
            warnings.append("연결(CFS) 없어 별도(OFS) 기준 — 종속기업투자가 별도 항목으로 계상")
        if not fin.get("list"):
            fs_div = None
            warnings.append(read_failed[0] if read_failed
                            else "재무제표 계정 조회 불가(소규모기업 요약생략·미제출 등) — `scope=\"detail\"`로 주석 확인")
        tiers, _ = _bs_tiers(fin.get("list") or [])
        data["fs_div"] = fs_div
        data["_tiers"] = tiers
        info_failed = None
        try:
            ci = await client.get_company_info(cc)
            induty = (ci.get("induty_code") or "")
        except DartClientError as exc:
            induty = ""
            failed, note = _dart_read_note(exc, empty_text="", fail_text="회사개황 조회 실패")
            if failed:
                info_failed = note
                read_failed.append(note)
                warnings.append(note)
        if info_failed:
            # 업종코드를 못 받았다 — 「금융·지주가 아니다」로 확정하지 않는다. 재무상태표가 금융업임을 보이면(예수부채 등) 확정, 아니면 판정 불가(None).
            is_fin = True if _is_financial("", fin.get("list") or []) else None
            is_holdco = False if is_fin else None
            if is_fin is None:
                warnings.append("업종코드를 몰라 금융·지주 판정과 잉여자산·지분 NAV 두 배수를 내지 못했다 — 잉여자산 절대액은 비금융 산식 기준이다.")
        else:
            is_fin = _is_financial(induty, fin.get("list") or [])
            # 일반 지주회사(64992 이면서 금융업 아님) — 연결 재무상태표의 현금·투자자산 대부분이 자회사 몫이다(아래 잉여자산 배수).
            is_holdco = (induty == _HOLDCO_KSIC) and not is_fin
        is_reit = bool(re.search(r"리츠|REIT", name, re.I))
        data["induty_code"] = induty or None
        data["is_financial"] = is_fin
        data["is_holding_company"] = is_holdco
        data["is_reit"] = is_reit
        if is_reit:
            warnings.append("REIT 추정(사명 기준) — 투자부동산이 본업이라 잉여자산에서 제외")
        stakes_failed = None
        try:
            otr = await client.get_other_corp_investment(cc, year, reprt_code)
        except DartClientError as exc:
            otr = {}
            failed, note = _dart_read_note(exc, empty_text="", fail_text="타법인 출자현황 조회 실패")
            if failed:
                stakes_failed = note
                read_failed.append(note)
                warnings.append(note)
        sec = await _safe_getdoc(client, rept["rcept_no"])
        # 셀별 연결/별도 선언을 함께 들고 간다 — 어느 기준의 표를 읽었는지 밝히기 위함.
        _basis = _av.BasisIndex(sec.get("note_html", "") or "")
        stripped = _basis.stripped
        doc_text = sec.get("full_text", "") or sec.get("note_html", "") or ""
        # 시총을 먼저 구한다 — 보유지분 종가를 **시총과 같은 거래일**로 맞추려는 것이다(배수의 분자·분모가 같은 날).
        mcap, mcap_meta = await _market_cap(isu)
        notes_failed = bool(sec.get("fetch_failed"))
        if notes_failed:
            # 주석을 못 읽으면 상장·비상장 구분(원문 권위값)도 못 한다 — 상장 지분이 장부가로 비상장에 들어가지 않게 값을 비운다.
            mark = _stakes_unavailable()
        elif stakes_failed:
            # 보유 목록을 못 읽었다 — 「보유지분 0」으로 읽지 않는다.
            mark = _stakes_unavailable(f"{stakes_failed} — 보유 목록을 읽지 못해 값을 비웠다")
        else:
            mark = await _mark_listed_stakes(client, otr.get("list") or [], doc_text,
                                             (mcap_meta or {}).get("date") if mcap else None,
                                             _period_end(rept.get("report_nm")))
        data["listed_stakes"] = mark
        if mark["n_unresolved"]:
            _un = sorted(mark["unresolved"], key=lambda u: -(u.get("book_krw") or 0))
            _names = ", ".join(u["name"] for u in _un[:3]) + (f" 외 {len(_un) - 3}종" if len(_un) > 3 else "")
            warnings.append(f"상장 보유지분 {mark['n_listed']}종 중 {mark['n_unresolved']}종은 시가를 못 구해 장부가로 두었다"
                            f"({_names}) — 지분 NAV 의 그만큼은 장부가 기준이다. 종목별 사유는 listed_stakes.unresolved.")
        if mcap and mark.get("price_date") and mcap_meta.get("date") and mark["price_date"] != mcap_meta["date"]:
            warnings.append(f"보유지분 시가 기준일({mark['price_date']})과 시총 기준일({mcap_meta['date']})이 다르다 — "
                            "지분 NAV 배수의 분자·분모 날짜가 어긋난다.")
        if mark.get("qty_adjust_checked") is False:
            warnings.append(f"보유수량은 {mark.get('qty_date') or '보고 기간 말'} 기준인데 그 뒤의 액면분할·병합·무상증자를 "
                            f"확인하지 못했다({'시세 저장분을 읽지 못함' if mark.get('qty_date') else '보유수량 기준일을 알 수 없음'}) "
                            "— 그런 종목이 있으면 시가가 그 배수만큼 틀린다.")
        pledged = _basis.annotate(_av.extract_pledged_assets("", stripped=stripped))
        contingent = _basis.annotate(_av.extract_contingent("", stripped=stripped))
        data["haircuts"] = ({"pledged": None, "contingent": None} if notes_failed
                            else {"pledged": pledged.get("status"), "contingent": contingent.get("status")})
        if notes_failed:
            # 주석을 못 읽어 담보·우발이 비어 보이고 상장·비상장 구분도 못 했다 — 「없음」·「상장 지분 0」이 아니라 「판정 못 함」이다(DATA-U3).
            data["notes_fetch_failed"] = True
            warnings.append("DART 원문 조회와 웹 뷰어 조회가 모두 실패해 재무제표 주석을 읽지 못했다 — 담보·우발부채 감액 여부와 "
                            "상장·비상장 구분을 판정하지 못해 지분 NAV 배수·상장 보유지분 합계·미실현 차액·감액 표시는 값을 비웠다"
                            "(비어 있다고 「없음」이 아니다). 잠시 뒤 다시 조회할 것."
                            + _origin_tail(rept.get("rcept_no"), "타법인출자"))
        data["market_cap_krw"] = mcap
        data["market_cap_meta"] = mcap_meta
        t = data["_tiers"]
        # ── 통화 정규화 ──
        # BS 계정은 기능통화로 신고된다(두산밥캣=USD). 시총(mcap)은 KRX 라 항상 KRW 이므로,
        # 환산하지 않으면 **분자 USD ÷ 분모 KRW** 가 되어 배수가 환율 배수만큼 축소된다
        # (실측: 잉여자산/시총 0.00배 — 실제 ~0.35배. 「잉여자산이 없는 회사」로 읽힌다).
        stmt_cur = statement_currency(fin.get("list") or [])
        data["functional_currency"] = stmt_cur
        fx = 1.0
        if stmt_cur != "KRW":
            # 기준일은 그 회계연도의 기말일이다. 종전엔 `fnlttSinglAcntAll` 행의 `thstrm_dt` 를
            # 캤는데 **그 응답에 없는 필드**라(행 키 17개에 부재) 늘 12월 말로 폴백했다 —
            # 비12월 결산사(3·6월)는 그동안 다른 날의 환율을 썼다는 뜻이다.
            _info, _ = await _safe_company_info(cc)   # (dict, err) — 실패해도 {} 라 12월로 폴백
            _acc_mt = (_info or {}).get("acc_mt")
            fx = await fx_to_krw(stmt_cur, fiscal_year_end_date(int(year), _acc_mt)) or 0.0
            if not fx:
                fx = 1.0
                data["fx_rate_to_krw"] = None
                warnings.append(f"⚠️ 기능통화 {stmt_cur} 인데 환율 조회 실패 — 아래 자산 금액은 "
                                f"{stmt_cur} 단위 그대로다. **KRW 시총과 나란히 두지 말 것**(배수 미제공).")
            else:
                t = {k: round(v * fx) for k, v in t.items()}
                data["_tiers"] = t
                data["fx_rate_to_krw"] = round(fx, 2)
                _fx_dd = fiscal_year_end_date(int(year), _acc_mt)
                data["fx_source"] = fx_source(stmt_cur, _fx_dd)   # 261019: "ecos" / "yahoo"(대체값 — 저장 안 함)
                warnings.append(
                    f"기능통화 {stmt_cur} — BS 자산을 "
                    f"{_fx_dd} 기말환율 {fx:,.1f}원/{stmt_cur} 로 "
                    f"KRW 환산해 시총과 통화를 맞췄다.")
                if data["fx_source"] == SRC_YAHOO:
                    warnings.append(f"⚠️ {_fx_dd} 환율: {FALLBACK_WARNING}")
        # 세부 계정 표는 **환산 뒤** 티어로 만든다. 환산 전에 만들면 같은 응답 안에서
        # 목적별 버킷(KRW)과 세부 계정(원행 통화)이 환율배만큼 어긋난다.
        data["assets"] = {_TIER_LABEL[k]: v for k, v in t.items() if v and k != "subs"}
        bucket_totals: dict[str, int] = {}
        for k, v in t.items():
            b = _BUCKET.get(k)
            if b and v:
                bucket_totals[b] = bucket_totals.get(b, 0) + v
        data["asset_buckets"] = {_BUCKET_LABEL[b]: {"krw": bucket_totals[b], "desc": _BUCKET_DESC[b]}
                                 for b in _BUCKET_ORDER if bucket_totals.get(b)}
        data["asset_story"] = _asset_story(bucket_totals, mcap)
        # 잉여자산(본업무관·환금): 현금성+환금FVPL+장투증권+투자부동산(비금융·비REIT). 지배지분은 별도(NAV).
        # 260721 회계사 검토: 금융업(은행·증권·보험)은 트레이딩/FVOCI 자산 자체가 본업이라 surplus·
        # equity_nav '숨은가치' 서사 자체가 성립 안 함 — 비율 미제공(raw 자산표는 그대로 노출).
        if is_fin:
            surplus = t.get("cash", 0) + t.get("held_for_sale", 0)
        else:
            surplus = t.get("cash", 0) + t.get("trading", 0) + t.get("ltinv", 0) + t.get("held_for_sale", 0)
            if not is_reit:
                surplus += t.get("inv_prop", 0)
        # 지분 NAV = **타법인출자현황(별도 기준 직접 보유분) 하나로** 합산: 상장은 시가(미해결은 장부가), 비상장은 장부가.
        # 261018: 종전 산식 `상장 시가 + max(0, 연결 관계기업 − 상장 장부가) + 연결 FVOCI` 는 별도 기준 보유 목록과 연결 재무상태표를
        #   섞어 같은 지분을 두 번 셌다 — 시가마크가 살아나자 배수가 크게 부풀었다(같은 읽기 전용 DB·같은 날 실측):
        #   ① 삼성물산 3.25배 — 연결 FVOCI 44.8조가 곧 삼성전자·삼성생명·삼성SDS 의 기말 공정가치인데 같은 종목이 시가로 또 들어갔다.
        #   ② LG 2.88배 — 연결 관계기업(지분법 23.7조)에서 별도 원가(6.9조)를 뺀 16.8조를 「비상장 잔여」로 더했다(같은 상장 관계사의 평가차).
        #   ③ SK 2.46배 — 상장 보유분이 연결 자회사라 연결 관계기업에 없는데 그 원가를 거기서 뺐고, 자회사의 관계사 지분이 시가와 겹쳤다.
        #   보유 목록 하나로 세면 같은 지분이 두 번 들어갈 길이 없다(삼성물산 2.4배·LG 1.6배·SK 2.0배 — 지주사 NAV 의 관행 산식).
        #   타법인출자현황이 비면(소규모·미제출) 재무상태표 관계기업+FVOCI 장부가로 낸다 — 이때도 한 기준이다(`equity_nav_basis`).
        bs_equity = t.get("assoc", 0) + (0 if is_fin else t.get("oci", 0))   # 금융업은 FVOCI 도 본업 포트폴리오
        if notes_failed or stakes_failed:
            equity_nav, equity_basis = None, None                  # 상장·비상장 구분(또는 보유 목록)을 못 읽어 어느 합계도 믿을 수 없다
        elif mark["n_listed"] or mark["n_unlisted"]:
            equity_nav, equity_basis = mark["listed_mkt_krw"] + mark["unlisted_book_krw"], "holdings"
            _gap = bs_equity - equity_nav
            if not is_fin and stmt_cur == "KRW" and _gap > 0 and (not mcap or _gap >= mcap * 0.1):
                # 보유 목록은 회사가 **직접** 든 지분뿐이다. 비상장 자회사를 거쳐 든 상장 지분(영풍 → 100% 자회사 → 고려아연)은
                # 그 자회사의 장부가로만 들어가 낮게 나온다. 연결 장부가가 더 크면 그런 간접 보유가 있다는 뜻일 수 있다 — 숨기지 않는다.
                # 시총의 10% 미만 차이는 배수를 바꾸지 못하는 잡음이라 말하지 않는다(실측 서희건설 320억 차이 · 시총 4,300억).
                _fs_word = {"CFS": "연결 ", "OFS": "별도 "}.get(fs_div or "", "")
                warnings.append(f"{_fs_word}재무상태표의 관계기업·FVOCI 장부가 합계 {_won(bs_equity)} > 보유지분 합산 지분 NAV "
                                f"{_won(equity_nav)} — 자회사를 거쳐 든 지분은 합산에 그 자회사 장부가로만 들어가 "
                                "지분 NAV 가 낮게 나왔을 수 있다(nav.equity_bs_book_krw 참고).")
        else:
            equity_nav, equity_basis = bs_equity, "balance_sheet"
        mixed_krw = t.get("mixed", 0)
        if is_fin:
            warnings.append("금융업 — 트레이딩·FVOCI 자산이 본업이라 surplus/지분NAV 배수는 미제공(자산표만 참고)")
        if is_holdco:
            # 261018: 일반 지주회사는 금융업 판정을 바로잡으며(SK·LG) 잉여자산 배수가 처음 나가게 됐는데, 연결 현금·투자자산은
            #   대부분 자회사 몫이고 부채도 빼지 않은 값이라 지주회사 자신의 잉여자산이 아니다(실측 SK 연결 현금 28.7조 → 1.14배 「시총 초과」).
            #   지주회사의 가치는 보유지분 합산(지분 NAV)으로 본다 — 잉여자산 배수는 종전처럼 내지 않는다.
            warnings.append("일반 지주회사 — 연결 현금·투자자산은 대부분 자회사 몫(부채 미차감)이라 잉여자산 배수는 내지 않는다. "
                            "지분 NAV(보유지분 합산)를 본다.")
        # 지분 NAV 의 장부가 부분은 타법인출자현황(otrCprInvstmntSttus — 통화 미선언)에서 온다.
        # 기능통화가 KRW 가 아니면 그 장부가가 어느 통화인지 확증할 수 없으므로 배수를 내지 않는다 —
        # 조용히 섞은 값을 내느니 「기준을 확증 못 했다」를 말한다.
        # 환산이 성립해야 배수를 낸다. 실패 경로(환율 조회 불가)에서는 티어가 원행 통화 그대로라
        # KRW 시총으로 나누면 고치려던 그 오류가 그대로 남는다 — 그래서 배수 전체를 내지 않는다.
        _cov_ok = (stmt_cur == "KRW") or bool(data.get("fx_rate_to_krw"))
        _equity_cov_ok = _cov_ok and (stmt_cur == "KRW")
        if _cov_ok and not _equity_cov_ok:
            warnings.append("지분 NAV 배수 미제공 — 타법인출자현황 장부가의 통화 선언이 없어 "
                            f"환산분(BS·{stmt_cur}→KRW)과 같은 기준임을 확증할 수 없다. 자산 절대액만 참고.")
        data["nav"] = {
            "surplus_krw": surplus, "equity_nav_krw": equity_nav,
            "equity_nav_basis": equity_basis,
            "equity_bs_book_krw": bs_equity,
            "listed_unrealized_gap_krw": mark["unrealized_gap_krw"],
            "surplus_cov": (surplus / mcap) if (mcap and is_fin is not None and not is_fin and not is_holdco and _cov_ok) else None,
            "equity_nav_cov": (equity_nav / mcap) if (mcap and equity_nav is not None and is_fin is not None and not is_fin and _equity_cov_ok) else None,
            "mixed_combined_krw": mixed_krw,
            "mixed_combined_cov": (mixed_krw / mcap) if (mcap and mixed_krw and _cov_ok) else None,
            "haircut_flags": None if notes_failed else [k for k, v in data["haircuts"].items() if v == "MARKDOWN"],
        }
        if notes_failed:
            data["nav"]["equity_nav_unavailable"] = ("주석 원문 조회 실패 — 상장·비상장 구분을 할 수 없어 지분 NAV 를 비웠다. "
                                                     "잠시 뒤 다시 조회할 것."
                                                     + _origin_tail(rept.get("rcept_no"), "타법인출자"))
        elif stakes_failed:
            data["nav"]["equity_nav_unavailable"] = f"{stakes_failed} — 보유 목록을 읽지 못해 지분 NAV 를 비웠다."
        if mixed_krw:
            warnings.append("종속+관계/공동기업 결합계정 존재(별도FS 흔한 표기) — 지배지분 섞여있어 "
                            "equity_nav에 안 섞고 nav.mixed_combined_krw로 별도 표기(참고용, 지분법원가 기준)")
        if not mcap:
            warnings.append(f"시총 산출 실패({mcap_meta.get('reason')}) — 배수 미제공, 자산 절대액만")

    # ── detail: III.주석 원문 markdown ──
    if scope == "detail":
        sec = await _safe_getdoc(client, rept["rcept_no"])
        _basis = _av.BasisIndex(sec.get("note_html", "") or "")
        stripped = _basis.stripped
        if sec.get("fetch_failed"):
            # 원문·viewer 조회가 모두 실패했다 — 빈 주석에서 「기재 없음」을 끌어내지 않는다(DATA-U3).
            data["real_estate"] = _av.fetch_failed_node("토지·투자부동산")
            data["equity_holdings"] = _av.fetch_failed_node("지분증권")
            data["pledged_assets"] = _av.fetch_failed_node("담보제공 자산")
            data["contingent"] = _av.fetch_failed_node("우발부채·지급보증")
            warnings.append("DART 원문 조회와 웹 뷰어 조회가 모두 실패해 재무제표 주석의 토지·지분증권·담보·우발 4항목을 읽지 못했다 — "
                            "「기재 없음」이 아니라 「확인하지 못함」이다. 잠시 뒤 다시 조회할 것."
                            + _origin_tail(rept.get("rcept_no"), "주석"))
        else:
            data["real_estate"] = _basis.annotate(_av.extract_real_estate("", "", stripped=stripped))
            data["equity_holdings"] = _basis.annotate(
                _av.extract_equity_holdings("", "", stripped=stripped))
            data["pledged_assets"] = _basis.annotate(_av.extract_pledged_assets("", stripped=stripped))
            data["contingent"] = _basis.annotate(_av.extract_contingent("", stripped=stripped))

    return {"tool": "asset_holdings", "status": "partial" if read_failed else "ok", "subject": name,
            "data": data, "warnings": warnings}


async def build_asset_holdings_payload(*args, **kwargs):
    """이름이 정확히 맞지 않아 추정으로 고른 기업을 응답에 밝힌다.

    이 서비스는 `ToolEnvelope` 를 쓰지 않고 dict 를 직접 만들어 return 이 여러 곳에
    흩어져 있다 — 진입점 하나만 감싸 두면 새 return 이 늘어도 전파가 끊기지 않는다.
    """
    return declare_weak_resolution(await _build_asset_holdings_payload_impl(*args, **kwargs))
