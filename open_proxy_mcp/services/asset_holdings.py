"""asset_holdings — 회사 보유 자산(부동산·지분증권·현금성)을 뽑아 시총 대비 청산가치(NAV) 스크리닝.

설계: 자산저평가주 전수조사(private research 260719) + 공시전문가↔밸류투자자 토론(260720).
소스: 연결 BS 계정 API(fnlttSinglAcntAll, 감사·구조화) + 타법인출자현황(otrCprInvstmntSttus) +
      III.재무 주석(asset_valuation 엔진) + 시세(get_stock_price). 시총 대비는 point-in-time.

스콥(260720 2개로 정리 — 딥하게 다듬은 건 이 둘뿐):
  summary(기본) 계정 API 자산 티어 + 상장지분 시가마크(↑) − 담보·우발 haircut 플래그 ÷ 시총 = net NAV 배수
  detail        III.주석 원문 markdown(토지 원가vs공정가치·지분증권 명세·담보제공·우발부채)

원칙: 구조화 가능한 것(계정·타법인출자)은 숫자로, 주석(담보·우발·토지gap)은 markdown-primary(caller 판단).
      금융업(KSIC 64/65/66)은 포트폴리오가 본업이라 summary에서 '금융 리그' 라벨.
"""
from __future__ import annotations
from open_proxy_mcp.clock import today_kst

from open_proxy_mcp.services.contracts import declare_weak_resolution

import re
import sqlite3
from pathlib import Path
from typing import Any

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import asset_valuation as _av

_MASTER = Path(__file__).resolve().parent.parent.parent / "configs" / "master.db"
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


# ── 상장 투자사명 → stock_code (master.db, 동명이인 리스크는 caller에 투명) ──
def _listed_lookup() -> dict[str, str]:
    try:
        con = sqlite3.connect(f"file:{_MASTER}?mode=ro", uri=True)
        m = {}
        for cn, sc in con.execute("SELECT corp_name, stock_code FROM corp_codes WHERE stock_code!=''"):
            m[_norm(cn)] = sc.strip()
        con.close()
        return m
    except sqlite3.Error:
        return {}


def _doc_listed(doc_text: str, names: list[str]) -> dict[str, bool]:
    """원문 doc에서 투자사명 인접 상장/비상장(권위값 — name-join 동명이인 오판 방지)."""
    dt = _WS.sub("", _NORM.sub("", doc_text or ""))
    out = {}
    for nm in names:
        n = _norm(nm)
        m = re.search(re.escape(n) + r"(비?상장)", dt) if n else None
        out[n] = (m.group(1) == "상장") if m else False
    return out


async def _mark_listed_stakes(client, holdings: list[dict], doc_text: str,
                              base_date: str) -> dict[str, Any]:
    """타법인출자 상장 건을 시가마크(보유수량×주가). sanity(장부>총자산 제외)·상장은 doc 권위값.
    시가마크는 top-N(장부가) 상장 건만(콜 상한)."""
    listed_map = _doc_listed(doc_text, [h.get("inv_prm") or "" for h in holdings])
    code_map = _listed_lookup()
    cand = []
    for h in holdings:
        book = _num(h.get("trmend_blce_acntbk_amount"))
        ta = _num(h.get("recent_bsns_year_fnnr_sttus_tot_assets"))
        if ta > 0 and book > ta * 1.05:                # sanity: 필링 자릿수오류 제외
            continue
        if not listed_map.get(_norm(h.get("inv_prm") or "")):
            continue
        cand.append((book, h))
    cand.sort(key=lambda x: x[0], reverse=True)  # book 동률 시 dict 2차비교로 TypeError 방지(260721)
    marked, book_sum, mkt_sum, unresolved = [], 0, 0, 0
    for book, h in cand[:12]:                           # 콜 상한 12
        nm = h.get("inv_prm") or ""
        code = code_map.get(_norm(nm))
        qty = _num(h.get("trmend_blce_qy"))
        px = None
        if code and qty:
            try:
                p = await client.get_stock_price(code, base_date)
                px = _num(p.get("closing_price")) if p else None
            except (DartClientError, Exception):        # noqa: BLE001
                px = None
        book_sum += book
        if px:
            mkt = qty * px
            mkt_sum += mkt
            marked.append({"name": nm, "book_krw": book, "mkt_krw": mkt,
                           "gap_krw": mkt - book, "px": px, "qty": qty})
        else:
            mkt_sum += book                             # 미해결은 장부가 유지(보수적)
            unresolved += 1
    return {"marked": marked, "listed_book_krw": book_sum, "listed_mkt_krw": mkt_sum,
            "unrealized_gap_krw": mkt_sum - book_sum, "n_listed": len(cand),
            "n_marked": len(marked), "n_unresolved": unresolved}


import datetime  # noqa: E402

from open_proxy_mcp.dart.client import get_dart_client  # noqa: E402
from open_proxy_mcp.services import business_details as _bd  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as _val  # noqa: E402
from open_proxy_mcp.services.company import resolve_company_query  # noqa: E402

_YEAR = re.compile(r"\((\d{4})\.\d{2}\)")
_FIN_SEC = ("64", "65", "66")


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
    """시총 = valuation tool과 동일 소스(krx_weekly, 상장주식수 기준) 재사용 — DART 0콜, Postgres 캐시.
    260721 버그수정: 이전엔 DART 유통주식수(distb_stock_co, 자기주식 제외)×종가로 자체계산해
    관행적 '시가총액'(상장주식수 전체 기준, valuation/네이버/KRX 표준)보다 자기주식 비율만큼
    과소산출됐다(서희건설 실측: 유통 1.85억주 vs 상장 2.30억주 — 시총 19% 과소, 잉여자산배수 왜곡).
    실패 시 (None, 사유)."""
    if not stock_code:
        return None, {"reason": "종목코드 없음"}
    mk = await _val._market_for(stock_code)
    if not mk.get("price") or not mk.get("common_mktcap"):
        return None, {"reason": "KRX 시세/시총 조회 실패"}
    return mk["common_mktcap"], {"shares": mk.get("list_shrs"), "close": mk["price"], "date": mk.get("date")}


async def _build_asset_holdings_payload_impl(company: str, scope: str = "summary",
                                       format: str = "md") -> dict[str, Any]:
    client = get_dart_client()
    q = (company or "").strip()
    if not q:
        return {"tool": "asset_holdings", "status": "invalid", "subject": company,
                "warnings": ["회사명 또는 종목코드(6자리)를 입력하세요."]}
    if scope not in ("summary", "detail"):
        scope = "summary"
    res = await resolve_company_query(q)
    if not res.selected:
        return {"tool": "asset_holdings", "status": "not_found", "subject": company,
                "warnings": [f"'{company}' 식별 실패 — 종목코드나 정확한 회사명으로 재시도."]}
    corp = res.selected
    cc, isu = corp["corp_code"], corp.get("stock_code") or corp.get("ticker")
    name = corp.get("corp_name") or company
    warnings: list[str] = []

    cands = await _bd._find_report_candidates(client, cc, "annual")
    if not cands:
        return {"tool": "asset_holdings", "status": "no_filing", "subject": name,
                "warnings": ["정기(사업)보고서 없음"]}
    rept = cands[0]
    year = (_YEAR.search(rept.get("report_nm") or "") or [None, str(datetime.today_kst().year - 1)])[1]

    data: dict[str, Any] = {"company": name, "ticker": isu, "report_nm": rept.get("report_nm"),
                            "year": year, "scope": scope}

    async def _fin_acnt(fs):
        try:
            return await client.get_fnltt_singl_acnt_all(cc, year, "11011", fs)
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
        data["assets"] = {_TIER_LABEL[k]: v for k, v in tiers.items() if v and k != "subs"}
        data["_tiers"] = tiers
        try:
            ci = await client.get_company_info(cc)
            induty = (ci.get("induty_code") or "")
        except DartClientError:
            induty = ""
        is_fin = induty[:2] in _FIN_SEC
        is_reit = bool(re.search(r"리츠|REIT", name, re.I))
        data["induty_code"] = induty or None
        data["is_financial"] = is_fin
        data["is_reit"] = is_reit
        if is_reit:
            warnings.append("REIT 추정(사명 기준) — 투자부동산이 본업이라 잉여자산에서 제외")
        try:
            otr = await client.get_other_corp_investment(cc, year, "11011")
        except DartClientError:
            otr = {}
        sec = await _safe_getdoc(client, rept["rcept_no"])
        # 셀별 연결/별도 선언을 함께 들고 간다 — 어느 기준의 표를 읽었는지 밝히기 위함.
        _basis = _av.BasisIndex(sec.get("note_html", "") or "")
        stripped = _basis.stripped
        doc_text = sec.get("full_text", "") or sec.get("note_html", "") or ""
        mark = await _mark_listed_stakes(client, otr.get("list") or [], doc_text,
                                         datetime.today_kst().strftime("%Y%m%d"))
        data["listed_stakes"] = mark
        pledged = _basis.annotate(_av.extract_pledged_assets("", stripped=stripped))
        contingent = _basis.annotate(_av.extract_contingent("", stripped=stripped))
        data["haircuts"] = {"pledged": pledged.get("status"), "contingent": contingent.get("status")}
        mcap, mcap_meta = await _market_cap(isu)
        data["market_cap_krw"] = mcap
        data["market_cap_meta"] = mcap_meta
        t = data["_tiers"]
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
        data["nav"] = {
            "surplus_krw": surplus, "equity_nav_krw": assoc_nav,
            "listed_unrealized_gap_krw": mark["unrealized_gap_krw"],
            "surplus_cov": (surplus / mcap) if (mcap and not is_fin) else None,
            "equity_nav_cov": (assoc_nav / mcap) if (mcap and not is_fin) else None,
            "mixed_combined_krw": mixed_krw,
            "mixed_combined_cov": (mixed_krw / mcap) if mcap and mixed_krw else None,
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
