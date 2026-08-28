"""희석의 「그 뒤」 — 발행 결정 이후에 물량을 되돌리거나 확정하는 공시.

`dilutive_issuance` 는 정형 API 가 있는 **발행 결정** 다섯(유상증자·CB·BW·EB·감자)만
본다. 그러면 발행 결정만으로 희석을 세게 되어 **실제로 안 나간 물량까지 센다.**

여기서 뒤를 잇는 셋을 읽는다 (2026-06-01~08-27 표본).
- 만기전취득 84건 — 회사가 자기 CB·BW·EB 를 되사 소각·재매각한다. **희석이 준다.**
  `자기전환사채만기전취득결정`(B001) · `전환사채(해외전환사채포함)발행후만기전사채취득`(I001)
- 발행가액 확정 53건 — 1차/2차/확정가액. **확정가액이 정해져야 주식수가 정해진다.**
  `유상증자최종발행가액확정`(I001) · `유상증자신주발행가액(안내공시)`(I003)
- 청약결과 8건 — 청약률이 100%를 밑돌면 실권주가 난다. **예정 주식수가 다 안 나간다.**
  `유상증자또는주식관련사채등의청약결과(자율공시)`(I001)

정형 API 가 없어 원문을 읽는다. 서식이 굳어 있어 라벨로 뽑는다.

[미확인] `[발행조건확정]증권신고서(지분증권)`(C001, 49건)은 아직 안 읽는다 —
증권신고서 본문이라 서식이 위 셋과 다르다.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# DART 원문은 XML 이지만 lxml HTML 파서로 읽는다(다른 서비스와 같은 관행).
warnings_module = __import__("warnings")
warnings_module.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.filing_search import search_filings_by_report_name

# ── 유형 정의 ──────────────────────────────────────────────────

_FOLLOWUP_TYPES: dict[str, dict[str, Any]] = {
    "early_redemption": {
        "label": "만기전 취득",
        "keywords": ("만기전취득", "만기전사채취득"),
        "channels": ("B001", "I001"),
        "direction": "희석 축소",
    },
    "issue_price_fixed": {
        "label": "발행가액 확정",
        "keywords": ("최종발행가액확정", "신주발행가액"),
        "channels": ("I001", "I003"),
        "direction": "주식수 확정",
    },
    "subscription_result": {
        "label": "청약결과",
        "keywords": ("청약결과",),
        "channels": ("I001",),
        "direction": "실제 배정",
    },
}

_ALL_KEYWORDS = tuple(kw for cfg in _FOLLOWUP_TYPES.values() for kw in cfg["keywords"])
_CHANNELS = tuple(sorted({c for cfg in _FOLLOWUP_TYPES.values() for c in cfg["channels"]}))


def classify_followup(report_nm: str) -> str:
    compact = (report_nm or "").replace(" ", "")
    for key, cfg in _FOLLOWUP_TYPES.items():
        if any(kw in compact for kw in cfg["keywords"]):
            return key
    return ""


# ── 공통 ───────────────────────────────────────────────────────


def _text(html: str) -> str:
    return " ".join(BeautifulSoup(html or "", "lxml").get_text("\n", strip=True).split())


def _int(s: str | None) -> int | None:
    try:
        return int(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _num(flat: str, pattern: str) -> int | None:
    m = re.search(pattern, flat)
    return _int(m.group(1)) if m else None


def _float(flat: str, pattern: str) -> float | None:
    m = re.search(pattern, flat)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _ymd(s: str | None) -> str:
    """`2026년 08월 27일` → `2026-08-27`. 이미 ISO 면 그대로."""
    m = re.match(r"(\d{4})\s*[년.\-]\s*(\d{1,2})\s*[월.\-]\s*(\d{1,2})", (s or "").strip())
    return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3))) if m else (s or "")


def _date(flat: str, label: str) -> str:
    m = re.search(re.escape(label) + r"\s*(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2}\s*일?|\d{4}-\d{2}-\d{2})", flat)
    return _ymd(m.group(1)) if m else ""


def _clip(s: str | None, n: int) -> str:
    if not s:
        return ""
    v = " ".join(s.split())
    return v if len(v) <= n else v[:n] + "…"


def _prune(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v not in (None, "", "-", [])}


# ── 만기전 취득 ────────────────────────────────────────────────

#: 이 중 몇 개를 읽었는지로 서식을 고른다. 라벨 하나 걸린 것과 제대로 읽은 것을 가른다.
_EARLY_REDEMPTION_CORE = ("series", "acquired_face_won", "acquisition_amount_won", "decided_on")


def _core_count(parsed: dict[str, Any]) -> int:
    return sum(1 for k in _EARLY_REDEMPTION_CORE if parsed.get(k) not in (None, ""))



def parse_early_redemption_exchange(flat: str) -> dict[str, Any]:
    """거래소 서식 `전환사채(해외전환사채포함)발행후만기전사채취득`.

    금감원 주요사항보고서(B001)와 **라벨이 다르다.** 이 서식만 쓰는 값이 하나 있다 —
    `3. 취득후 사채의 권면총액` = **되사고 남은 잔액**. 0 이면 그 회차는 전량 회수됐다.
    """
    out: dict[str, Any] = {}
    m = re.search(r"전환사채\(해외전환사채\)\s*([\d-]+)\s*회차", flat)
    if m:
        out["series"] = m.group(1)
    m = re.search(r"사채의\s*종류\s*(.+?)\s*발행일자", flat)
    if m:
        out["bond_kind"] = _clip(m.group(1), 60)
    out["issued_on"] = _date(flat, "발행일자")
    m = re.search(r"발행방법\s*(.+?)\s*주당\s*전환가액", flat)
    if m:
        out["issue_method"] = _clip(m.group(1), 40)
    out["conversion_price_won"] = _num(flat, r"주당\s*전환가액\(원\)\s*([\d,]+)")
    out["maturity_on"] = _date(flat, "만기일")
    out["acquisition_amount_won"] = _num(
        flat, r"2\.\s*사채\s*취득금액\s*\(통화단위\)\s*([\d,]+)")
    out["acquired_face_won"] = _num(
        flat, r"취득한\s*사채의\s*권면\(전자등록\)총액\s*\(통화단위\)\s*([\d,]+)")
    out["decided_on"] = _date(flat, "취득일자")
    # 남은 잔액은 0 이 **의미 있는 값**이다 — `_prune` 이 지우지 않도록 문자열이 아닌 정수로 둔다.
    remaining = _num(
        flat, r"3\.\s*취득후\s*사채의\s*권면\(전자등록\)총액\s*\(통화단위\)\s*([\d,]+)")
    if remaining is not None:
        out["remaining_face_won_after"] = remaining
    m = re.search(r"취득사유\s*[:：]\s*(.+?)\s*-\s*향후\s*처리방법", flat)
    if m:
        out["reason"] = _clip(m.group(1), 120)
    m = re.search(r"향후\s*처리방법\s*[:：]\s*(.+?)\s*5\.", flat)
    if m:
        out["planned_handling"] = _clip(m.group(1), 120)
    m = re.search(r"5\.\s*취득자금의\s*원천\s*(.+?)\s*6\.", flat)
    if m:
        out["funding_source"] = _clip(m.group(1), 40)
    m = re.search(r"6\.\s*사채의\s*취득방법\s*(.+?)\s*7\.", flat)
    if m:
        out["acquisition_method"] = _clip(m.group(1), 40)
    out = _prune(out)
    if out:
        out["source_form"] = "거래소 서식"
    return out


def parse_early_redemption(text: str) -> dict[str, Any]:
    """`자기 전환사채 만기전 취득 결정` 서식. 라벨 번호가 1~10 으로 굳어 있다.

    서식이 둘이다 — 금감원 주요사항보고서(B001)와 거래소 공시(I001). 라벨이 겹치지 않아
    앞 서식이 빈손이면 뒤 서식으로 넘긴다. **둘 다 빈손이면 원문 발췌를 남긴다** —
    빈 dict 를 돌려주면 읽는 쪽은 「취득 규모가 0」으로 읽는다.
    """
    flat = " ".join((text or "").split())
    out: dict[str, Any] = {}
    m = re.search(r"1\.\s*사채의\s*종류\s*회차\s*(\S+)\s*종류\s*(.+?)\s*2\.\s*사채발행일자", flat)
    if m:
        out["series"] = m.group(1)
        out["bond_kind"] = _clip(m.group(2), 60)
    out["issued_on"] = _date(flat, "2. 사채발행일자")
    m = re.search(r"3\.\s*사채발행방법\s*(\S+)", flat)
    if m:
        out["issue_method"] = m.group(1)
    out["maturity_on"] = _date(flat, "4. 사채만기일")
    # 5 = 원래 발행총액, 6 = 이번에 되사는 금액. **둘을 섞으면 희석 계산이 뒤집힌다.**
    out["face_total_won"] = _num(flat, r"5\.\s*사채의\s*권면\(전자등록\)\s*총액\(원\)\s*([\d,]+)")
    out["acquired_face_won"] = _num(flat, r"6\.\s*취득\s*대상\s*사채의\s*권면\(전자등록\)\s*금액\(원\)\s*([\d,]+)")
    out["decided_on"] = _date(flat, "7. 취득 결정일") or _date(flat, "7. 취득결정일")
    out["acquisition_amount_won"] = _num(flat, r"8\.\s*취득금액\s*금액\(원\)\s*([\d,]+)")
    m = re.search(r"취득자금의\s*원천\s*(.+?)\s*9\.", flat)
    if m:
        out["funding_source"] = _clip(m.group(1), 40)
    m = re.search(r"9\.\s*취득\s*방법\s*(.+?)\s*10\.", flat)
    if m:
        out["acquisition_method"] = _clip(m.group(1), 40)
    m = re.search(r"10\.\s*만기전\s*취득사유\s*(.+?)\s*(?:\d{2}\.|※|$)", flat)
    if m:
        out["reason"] = _clip(m.group(1), 120)
    if out.get("face_total_won") and out.get("acquired_face_won"):
        # 원문 두 값의 비. **우리가 만든 값이라고 이름으로 밝힌다.**
        out["acquired_ratio_pct_derived"] = round(
            out["acquired_face_won"] / out["face_total_won"] * 100, 2)
    out = _prune(out)
    # 라벨 하나가 우연히 걸려도 「읽었다」가 되지 않도록 **핵심 값 개수로 서식을 고른다.**
    # (첨부추가본에서 `취득자금의 원천` 한 줄만 걸려 거래소 서식 폴백이 막히던 것 — 2026-08-28)
    alternate = parse_early_redemption_exchange(flat)
    if _core_count(alternate) > _core_count(out):
        out = alternate
    if _core_count(out) == 0:
        # 서식 둘 다 안 맞았다. **빈 dict 로 두면 「취득 0원」으로 읽힌다.**
        out = {
            "unparsed": True,
            "unparsed_note": "만기전취득 서식 둘(금감원·거래소) 어느 쪽에도 맞지 않아 금액·회차를 읽지 못했다 — 원문 확인 필요.",
            "summary_excerpt": _clip(flat, 400),
        }
    return out


# ── 발행가액 확정 ──────────────────────────────────────────────


def parse_issue_price(text: str) -> dict[str, Any]:
    """서식이 둘 — `유상증자 최종발행가액 확정`(확정) 과 `신주발행가액(안내공시)`(1차)."""
    flat = " ".join((text or "").split())
    out: dict[str, Any] = {}
    if "확정발행가액" in flat:
        out["price_stage"] = "확정"
        out["planned_shares"] = _num(flat, r"나\.\s*주식수\(주\)\s*([\d,]+)")
        out["final_price_won"] = _num(flat, r"가\.\s*확정가액\(원\)\s*([\d,]+)")
        out["first_price_won"] = _num(flat, r"나\.\s*1차발행가\(원\)\s*([\d,]+)")
        out["second_price_won"] = _num(flat, r"다\.\s*2차발행가\(원\)\s*([\d,]+)")
        out["par_value_won"] = _num(flat, r"3\.\s*액면가\(원\)\s*([\d,]+)")
        out["fixed_on"] = _date(flat, "4. 확정일")
    else:
        out["price_stage"] = "1차(안내)"
        m = re.search(r"1\.\s*구분\s*(.+?)\s*2\.\s*주당\s*발행가액", flat)
        if m:
            out["basis"] = _clip(m.group(1), 40)
        out["common_price_won"] = _num(flat, r"보통주식\(원\)\s*([\d,]+)")
        out["preferred_price_won"] = _num(flat, r"종류주식\(원\)\s*([\d,]+)")
    if out.get("final_price_won") and out.get("planned_shares"):
        out["proceeds_won_derived"] = out["final_price_won"] * out["planned_shares"]
    if len(out) <= 1:
        out["summary_excerpt"] = _clip(flat, 400)
    return _prune(out)


# ── 청약결과 ───────────────────────────────────────────────────


def parse_subscription_result(text: str) -> dict[str, Any]:
    """청약률이 100%를 밑돌면 실권주가 난다 — 예정 주식수가 다 안 나간다."""
    flat = " ".join((text or "").split())
    out: dict[str, Any] = {}
    m = re.search(r"1\.\s*증권의\s*종류\s*(.+?)\s*2\.\s*발행방법", flat)
    if m:
        out["security_kind"] = _clip(m.group(1), 50)
    m = re.search(r"2\.\s*발행방법\s*(.+?)\s*3\.\s*청약대상자", flat)
    if m:
        out["issue_method"] = _clip(m.group(1), 40)
    m = re.search(r"3\.\s*청약대상자\s*(.+?)\s*4\.\s*청약일자", flat)
    if m:
        out["subscriber"] = _clip(m.group(1), 40)
    out["subscribed_on"] = _date(flat, "4. 청약일자")
    out["planned_shares"] = _num(flat, r"발행예정주식수\(주\)\s*([\d,]+)")
    out["subscribed_shares"] = _num(flat, r"해당\s*청약주식수\(주\)\s*([\d,]+)")
    out["subscribed_shares_cum"] = _num(flat, r"청약주식수\(누계\)\(주\)\s*([\d,]+)")
    out["subscription_rate_pct"] = _float(flat, r"청약률\(%\)\s*([\d.,]+)")
    m = re.search(r"6\.\s*단수주\s*및\s*실권주\s*처리방법\s*(.+?)\s*7\.", flat)
    if m:
        out["forfeited_handling"] = _clip(m.group(1), 120)
    rate = out.get("subscription_rate_pct")
    if rate is not None:
        # **우리가 판정하지 않는다** — 원문 청약률을 100 과 견줄 뿐이다.
        out["undersubscribed"] = rate < 100
    if len(out) <= 1:
        out["summary_excerpt"] = _clip(flat, 400)
    return _prune(out)


_PARSERS = {
    "early_redemption": parse_early_redemption,
    "issue_price_fixed": parse_issue_price,
    "subscription_result": parse_subscription_result,
}


# ── 조회 ───────────────────────────────────────────────────────


async def fetch_dilution_followup(
    corp_code: str,
    bgn_de: str,
    end_de: str,
    *,
    max_docs: int = 10,
) -> tuple[list[dict[str, Any]], list[str], int]:
    """(rows, warnings, api_calls). 원문은 최근 `max_docs` 건만 읽는다."""
    warnings: list[str] = []
    items, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys="",
        pblntf_detail_ty=list(_CHANNELS),
        keywords=_ALL_KEYWORDS,
        strip_spaces=True,
    )
    warnings.extend(notices)
    api_calls = len(_CHANNELS)
    if error:
        warnings.append(f"희석 후속 공시 조회 실패: {error}")
        return [], warnings, api_calls

    rows: list[dict[str, Any]] = []
    for item in items or []:
        kind = classify_followup(item.get("report_nm") or "")
        if not kind:
            continue
        cfg = _FOLLOWUP_TYPES[kind]
        rows.append({
            "type": kind,
            "label": cfg["label"],
            "direction": cfg["direction"],
            "report_nm": (item.get("report_nm") or "").strip(),
            "rcept_no": item.get("rcept_no", ""),
            "rcept_dt": item.get("rcept_dt", ""),
            "is_correction": (item.get("report_nm") or "").startswith("[기재정정]"),
        })
    rows.sort(key=lambda r: (r.get("rcept_dt", ""), r.get("rcept_no", "")), reverse=True)

    client = get_dart_client()

    async def _one(row: dict[str, Any]) -> None:
        try:
            doc = await client.get_document(row["rcept_no"])
        except Exception as exc:  # noqa: BLE001 — 한 건 실패가 전체를 죽이지 않는다
            row["parse_error"] = f"{type(exc).__name__}: {exc}"
            return
        text = _text(doc.get("content") or doc.get("html") or "")
        if len(text) < 30:
            row["parse_error"] = "원문 본문이 비어 있다"
            return
        row["details"] = _PARSERS[row["type"]](text)

    targets = rows[:max_docs]
    if targets:
        await asyncio.gather(*[_one(r) for r in targets])
        api_calls += len(targets)
    if len(rows) > max_docs:
        warnings.append(
            f"희석 후속 공시 {len(rows)}건 중 최근 {max_docs}건만 원문을 읽었다 — "
            f"나머지 {len(rows) - max_docs}건은 목록만.")
    return rows, warnings, api_calls
