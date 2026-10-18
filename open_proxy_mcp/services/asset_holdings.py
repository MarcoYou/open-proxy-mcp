"""asset_holdings — 회사 보유 자산(부동산·지분증권·현금성)을 뽑아 시총 대비 청산가치(NAV) 스크리닝.

설계: 자산저평가주 전수조사(private research 260719) + 공시전문가↔밸류투자자 토론(260720).
소스: 연결 BS 계정 API(fnlttSinglAcntAll, 감사·구조화) + 타법인출자현황(otrCprInvstmntSttus) +
      III.재무 주석(asset_valuation 엔진) + 시세(일별 시세 저장분 krx_daily — 시총과 같은 거래일, 못 읽으면 라이브).
      시총 대비는 point-in-time.

스콥(260720 2개로 정리 — 딥하게 다듬은 건 이 둘뿐):
  summary(기본) 계정 API 자산 티어 + 상장지분 시가마크(↑) − 담보·우발 haircut 플래그 ÷ 시총 = net NAV 배수
  detail        III.주석 원문 markdown(토지 원가vs공정가치·지분증권 명세·담보제공·우발부채)

원칙: 구조화 가능한 것(계정·타법인출자)은 숫자로, 주석(담보·우발·토지gap)은 markdown-primary(caller 판단).
      금융업(KSIC 64/65/66)은 포트폴리오가 본업이라 summary에서 '금융 리그' 라벨.
"""
from __future__ import annotations
from open_proxy_mcp.clock import today_kst
from open_proxy_mcp.dart.fx import fiscal_year_end_date, fx_to_krw, statement_currency

from open_proxy_mcp.services.contracts import declare_weak_resolution, AnalysisStatus

import asyncio
import re
from typing import Any

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import asset_valuation as _av

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


# ── BS 계정 라인 → 목적·환금성 티어 (전수조사 검증 로직 이식) ──
def _is_current(nm: str) -> bool:
    return ("유동" in nm and "비유동" not in nm) or ("단기" in nm and "장기" not in nm)


def _tier(nm: str) -> str | None:
    """BS 계정명 → 티어. 함정: 부채·비금융자산·매출채권 제외."""
    if "부채" in nm or "비금융자산" in nm or "매출채권" in nm:
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
        if x.get("sj_div") != "BS":
            continue
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
#   조용히 미해결**이었다(운영 실측 삼성물산 12/12·SK 12/12, 경고 0). 로컬 체크아웃엔 그 파일이 있어 재현되지 않았다.
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
    if not listed:
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


def _db_closes(price_dd: str | None, codes: list[str]) -> tuple[str | None, dict[str, int]] | None:
    """일별 시세 저장분(`krx_daily`, 코스피·코스닥 전 종목)에서 그 거래일 종가 — 종목 여럿을 한 번 왕복으로.

    `price_dd` 를 주면 그날(시총과 같은 기준일), 없으면 저장분의 가장 늦은 거래일. 반환 (거래일, {종목코드: 종가}).
    None = DB 미설정·장애(호출자가 라이브로 간다). 빈 사전 = 그날 그 종목들이 저장분에 없다."""
    from open_proxy_mcp.db import pg_rows
    if price_dd:
        rows = pg_rows("SELECT ticker, close, trd_dd FROM krx_daily WHERE trd_dd = %s AND ticker = ANY(%s)",
                       (price_dd, codes))
    else:
        rows = pg_rows("SELECT ticker, close, trd_dd FROM krx_daily "
                       "WHERE trd_dd = (SELECT max(trd_dd) FROM krx_daily) AND ticker = ANY(%s)", (codes,))
    if rows is None:
        return None
    closes = {r[0]: _num(r[1]) for r in rows if _num(r[1])}
    return (rows[0][2] if rows else price_dd), closes


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


_MARK_CAP = 40          # 이름 해석·평가 대상 상한(장부가 큰 순) — 시세는 DB 한 번 왕복이라 콜이 아니라 해석 시간을 막는 상한
_LIVE_PX_CAP = 12       # 종목별 라이브 시세 콜 상한 — DB·KRX 전종목 스냅샷을 둘 다 못 얻었을 때만 쓴다(종전 「콜 상한 12」)


async def _mark_listed_stakes(client, holdings: list[dict], doc_text: str,
                              price_dd: str | None = None) -> dict[str, Any]:
    """타법인출자 상장 건을 시가마크(보유수량×종가). sanity(장부>총자산 제외)·상장은 doc 권위값.

    261018 재작성(운영에서 상장지분이 하나도 평가되지 않던 결함):
      · 이름 → 종목코드: 서버가 쓰는 기업 마스터 해석기(`_stake_codes`). 종전 경로 박은 sqlite 는 운영에 없었다.
      · 종가: **시총과 같은 원천·같은 거래일**(`price_dd` = 시총 기준일)의 일별 시세 저장분을 한 번에 읽는다(`_db_closes`).
        종전엔 종목마다 KRX 를 **오늘 날짜**로 불렀는데 KRX 일별 시세는 다음 날 게시라 늘 빈 응답이었고(주말도 빈 응답),
        그 엔드포인트는 코스피만 담아 코스닥 보유지분은 날짜가 맞아도 못 찾았다. 결국 포털 시세로 떨어졌고, 포털 종가는
        거래소 공식 종가와 다를 수 있다(실측 20261016 삼성전자 포털 217,000 · KRX 215,500 — 같은 응답의 시총은 KRX 값).
      · DB 를 못 읽으면 KRX 전종목 스냅샷(가격 배수 서비스와 같은 캐시 — 시총을 라이브로 구했다면 같은 날짜라 추가 콜 0),
        그것도 없으면(키 없음·장애) 종목별 시세(`get_stock_price`, 상한 `_LIVE_PX_CAP`)로 간다.
      · 못 평가한 건은 이름·장부가·사유를 `unresolved` 에 싣는다 — 종전엔 개수만 남아 「왜 0건인가」를 알 수 없었다.
      · `n_listed` 와 합계가 같은 집합을 센다 — 종전엔 상한(12) 밖 후보가 개수에만 들고 합계에서 빠졌다(SK 13 vs 12)."""
    listed_map = _doc_listed(doc_text, [h.get("inv_prm") or "" for h in holdings])
    cand = []
    for h in holdings:
        book = _num(h.get("trmend_blce_acntbk_amount"))
        ta = _num(h.get("recent_bsns_year_fnnr_sttus_tot_assets"))
        if ta > 0 and book > ta * 1.05:                # sanity: 필링 자릿수오류 제외
            continue
        if not listed_map.get(_norm(h.get("inv_prm") or "")):
            continue
        if not book and not _num(h.get("trmend_blce_qy")):   # 기중 전량 처분(기말 수량·장부가 0) — 평가할 보유분이 없다
            continue
        cand.append((book, h))
    cand.sort(key=lambda x: x[0], reverse=True)  # book 동률 시 dict 2차비교로 TypeError 방지(260721)

    # 1) 이름 → 종목코드 후보
    rows: list[dict[str, Any]] = []
    for i, (book, h) in enumerate(cand):
        nm = h.get("inv_prm") or ""
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
    day, source = price_dd, None
    whole_day = False          # True = 그날 전 종목을 본 결과(DB·전종목 스냅샷) — 없으면 「그날 시세 없음」이 사실이다
    tried: set[str] = set()    # 종목별 라이브로 실제 물어본 코드
    if want:
        db = await asyncio.to_thread(_db_closes, price_dd, want)
        if db is not None and db[1]:
            day, px = db
            source, whole_day = "krx_daily", True
        else:
            # DB 장애 또는 그날 저장분에 하나도 없음 → 라이브. 날짜는 시총 기준일, 없으면 직전 거래일.
            day = price_dd or (db[0] if db else None) or _prev_trading_day()
            snap = await _val._krx_market_live(day) if day else {}
            if snap:
                px = {c: _num(snap[c].get("TDD_CLSPRC")) for c in want
                      if c in snap and _num(snap[c].get("TDD_CLSPRC"))}
                source, whole_day = "krx", True
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
                        source = source or p.get("source")
        if whole_day:
            px_dd = {c: day for c in px}

    # 3) 평가 — 동명 후보는 그날 시세가 있는 코드 하나로 가린다
    marked, unresolved, book_sum, mkt_sum = [], [], 0, 0
    for r in rows:
        book_sum += r["book"]
        priced = [c for c in r["codes"] if px.get(c)]
        why = r["why"]
        if why is None and len(priced) == 1:
            code = priced[0]
            mkt = r["qty"] * px[code]
            mkt_sum += mkt
            marked.append({"name": r["name"], "code": code, "book_krw": r["book"], "mkt_krw": mkt,
                           "gap_krw": mkt - r["book"], "px": px[code], "px_date": px_dd.get(code), "qty": r["qty"]})
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
            "listed_book_krw": book_sum, "listed_mkt_krw": mkt_sum,
            "unrealized_gap_krw": mkt_sum - book_sum, "n_listed": len(cand),
            "n_marked": len(marked), "n_unresolved": len(unresolved)}



from open_proxy_mcp.dart.client import get_dart_client  # noqa: E402
from open_proxy_mcp.services import business_details as _bd  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as _val  # noqa: E402
from open_proxy_mcp.services.company import (COMPANY_LOOKUP_NEXT_ACTION,  # noqa: E402
                                             _safe_company_info,
                                             company_ambiguous_warning,
                                             company_not_found_warning,
                                             resolve_company_query)

_YEAR = re.compile(r"\((\d{4})\.\d{2}\)")


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


async def _safe_getdoc(client, rcept_no: str) -> dict:
    """get_document(1콜) → note_html 비면/014 시 viewer 폴백(정정보고서 대응, business_details와 동일)."""
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
        return {"note_html": "", "full_text": ""}


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

    async def _fin_acnt(fs):
        try:
            out = await client.get_fnltt_singl_acnt_all(cc, year, reprt_code, fs)
            if not (out or {}).get("list") and reprt_code in ("11013", "11014"):
                # 결산월이 12월이 아니면 분기 코드가 반대일 수 있다 — 한 번 바꿔 본다
                out = await client.get_fnltt_singl_acnt_all(cc, year, "11013" if reprt_code == "11014" else "11014", fs)
            return out
        except DartClientError:
            return {}

    # ── summary: 연결 BS 티어 + 시가마크 + haircut + 시총 대비 ──
    if scope == "summary":
        fin = await _fin_acnt("CFS")
        fs_div = "CFS"
        if not fin.get("list"):
            fin = await _fin_acnt("OFS")
            fs_div = "OFS" if fin.get("list") else None
        if fs_div == "OFS":
            warnings.append("연결(CFS) 없어 별도(OFS) 기준 — 종속기업투자가 별도 항목으로 계상")
        if not fin.get("list"):
            fs_div = None
            warnings.append("재무제표 계정 조회 불가(소규모기업 요약생략·미제출 등) — `scope=\"detail\"`로 주석 확인")
        tiers, _ = _bs_tiers(fin.get("list") or [])
        data["fs_div"] = fs_div
        data["_tiers"] = tiers
        try:
            ci = await client.get_company_info(cc)
            induty = (ci.get("induty_code") or "")
        except DartClientError:
            induty = ""
        is_fin = _is_financial(induty, fin.get("list") or [])
        is_reit = bool(re.search(r"리츠|REIT", name, re.I))
        data["induty_code"] = induty or None
        data["is_financial"] = is_fin
        data["is_reit"] = is_reit
        if is_reit:
            warnings.append("REIT 추정(사명 기준) — 투자부동산이 본업이라 잉여자산에서 제외")
        try:
            otr = await client.get_other_corp_investment(cc, year, reprt_code)
        except DartClientError:
            otr = {}
        sec = await _safe_getdoc(client, rept["rcept_no"])
        # 셀별 연결/별도 선언을 함께 들고 간다 — 어느 기준의 표를 읽었는지 밝히기 위함.
        _basis = _av.BasisIndex(sec.get("note_html", "") or "")
        stripped = _basis.stripped
        doc_text = sec.get("full_text", "") or sec.get("note_html", "") or ""
        # 시총을 먼저 구한다 — 보유지분 종가를 **시총과 같은 거래일**로 맞추려는 것이다(배수의 분자·분모가 같은 날).
        mcap, mcap_meta = await _market_cap(isu)
        mark = await _mark_listed_stakes(client, otr.get("list") or [], doc_text,
                                         (mcap_meta or {}).get("date") if mcap else None)
        data["listed_stakes"] = mark
        if mark["n_unresolved"]:
            _un = sorted(mark["unresolved"], key=lambda u: -(u.get("book_krw") or 0))
            _names = ", ".join(u["name"] for u in _un[:3]) + (f" 외 {len(_un) - 3}종" if len(_un) > 3 else "")
            warnings.append(f"상장 보유지분 {mark['n_listed']}종 중 {mark['n_unresolved']}종은 시가를 못 구해 장부가로 두었다"
                            f"({_names}) — 지분 NAV 의 그만큼은 장부가 기준이다. 종목별 사유는 listed_stakes.unresolved.")
        if mcap and mark.get("price_date") and mcap_meta.get("date") and mark["price_date"] != mcap_meta["date"]:
            warnings.append(f"보유지분 시가 기준일({mark['price_date']})과 시총 기준일({mcap_meta['date']})이 다르다 — "
                            "지분 NAV 배수의 분자·분모 날짜가 어긋난다.")
        pledged = _basis.annotate(_av.extract_pledged_assets("", stripped=stripped))
        contingent = _basis.annotate(_av.extract_contingent("", stripped=stripped))
        data["haircuts"] = {"pledged": pledged.get("status"), "contingent": contingent.get("status")}
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
                warnings.append(
                    f"기능통화 {stmt_cur} — BS 자산을 "
                    f"{fiscal_year_end_date(int(year), _acc_mt)} 기말환율 {fx:,.1f}원/{stmt_cur} 로 "
                    f"KRW 환산해 시총과 통화를 맞췄다.")
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
        # 지분 NAV: 관계기업(시가마크 반영) + FVOCI(비금융만 — 금융업은 FVOCI도 본업 포트폴리오).
        assoc_nav = mark["listed_mkt_krw"] + max(0, t.get("assoc", 0) - mark["listed_book_krw"])
        if not is_fin:
            assoc_nav += t.get("oci", 0)
        mixed_krw = t.get("mixed", 0)
        if is_fin:
            warnings.append("금융업 — 트레이딩·FVOCI 자산이 본업이라 surplus/지분NAV 배수는 미제공(자산표만 참고)")
        # 지분 NAV 는 BS(기능통화 환산분)와 타법인출자현황(otrCprInvstmntSttus — 통화 미선언)
        # 장부가가 섞인다. 기능통화가 KRW 가 아니면 두 출처의 통화를 확증할 수 없으므로
        # 배수를 내지 않는다 — 조용히 섞은 값을 내느니 「기준을 확증 못 했다」를 말한다.
        # 환산이 성립해야 배수를 낸다. 실패 경로(환율 조회 불가)에서는 티어가 원행 통화 그대로라
        # KRW 시총으로 나누면 고치려던 그 오류가 그대로 남는다 — 그래서 배수 전체를 내지 않는다.
        _cov_ok = (stmt_cur == "KRW") or bool(data.get("fx_rate_to_krw"))
        # 지분 NAV 는 여기에 더해 타법인출자현황(otrCprInvstmntSttus — 통화 미선언) 장부가가 섞인다.
        _equity_cov_ok = _cov_ok and (stmt_cur == "KRW")
        if _cov_ok and not _equity_cov_ok:
            warnings.append("지분 NAV 배수 미제공 — 타법인출자현황 장부가의 통화 선언이 없어 "
                            f"환산분(BS·{stmt_cur}→KRW)과 같은 기준임을 확증할 수 없다. 자산 절대액만 참고.")
        data["nav"] = {
            "surplus_krw": surplus, "equity_nav_krw": assoc_nav,
            "listed_unrealized_gap_krw": mark["unrealized_gap_krw"],
            "surplus_cov": (surplus / mcap) if (mcap and not is_fin and _cov_ok) else None,
            "equity_nav_cov": (assoc_nav / mcap) if (mcap and not is_fin and _equity_cov_ok) else None,
            "mixed_combined_krw": mixed_krw,
            "mixed_combined_cov": (mixed_krw / mcap) if (mcap and mixed_krw and _cov_ok) else None,
            "haircut_flags": [k for k, v in data["haircuts"].items() if v == "MARKDOWN"],
        }
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
        data["real_estate"] = _basis.annotate(_av.extract_real_estate("", "", stripped=stripped))
        data["equity_holdings"] = _basis.annotate(
            _av.extract_equity_holdings("", "", stripped=stripped))
        data["pledged_assets"] = _basis.annotate(_av.extract_pledged_assets("", stripped=stripped))
        data["contingent"] = _basis.annotate(_av.extract_contingent("", stripped=stripped))

    return {"tool": "asset_holdings", "status": "ok", "subject": name,
            "data": data, "warnings": warnings}


async def build_asset_holdings_payload(*args, **kwargs):
    """이름이 정확히 맞지 않아 추정으로 고른 기업을 응답에 밝힌다.

    이 서비스는 `ToolEnvelope` 를 쓰지 않고 dict 를 직접 만들어 return 이 여러 곳에
    흩어져 있다 — 진입점 하나만 감싸 두면 새 return 이 늘어도 전파가 끊기지 않는다.
    """
    return declare_weak_resolution(await _build_asset_holdings_payload_impl(*args, **kwargs))
