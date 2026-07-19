"""자산가치 gap — 토지·투자부동산 장부가 vs 공정가치, 금융자산(지분증권) 원가 vs 시가.

목적: **자산저평가주 발굴**. 상장사가 토지를 원가법(취득원가)으로 기재해온 경우 내년부터 공정가치
gap을 주석 의무공시 → 장부가 ≪ 공정가치인 firm이 숨은 자산가치. 토지 공정가치는 공시지가 기준이라
실거래가의 50~70% 수준 → 공시된 gap조차 **보수적 하한**(진짜 저평가는 더 큼).

설계(260719 전문가 3인 + 12사 실측 + fresh-eye): III.재무 주석은 서식 변형이 크고 **DART HTML이
중첩 <table>이라 grid 파싱이 헤더만 떠내고 데이터행(토지 127,786,657 등)을 놓친다**(경방·대한제분).
→ table 파싱 포기, **stripped 텍스트 region 윈도**를 마크다운으로 반환(markdown-primary 순수형 —
텍스트에는 데이터가 다 있음, 호출측 AI가 읽음). 앵커 literal + content-signature(순수 lookahead)로
region을 지목하고, 산문 회계정책·BS 한줄·CF조각은 signature로 배제. [[markdown-primary-anchor-260719]].
"""
from __future__ import annotations

import re
from typing import Any

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t ]+")


def _strip(html: str) -> str:
    """태그 제거 + 공백 정리(1회/firm). 줄바꿈은 공백화(region 윈도용)."""
    t = _TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", t.replace("\n", " "))


def _find_regions(stripped: str, anchors: tuple[str, ...], sig: re.Pattern,
                  before: int = 140, after: int = 1500, max_regions: int = 1,
                  max_scan: int = 400) -> list[str]:
    """앵커 literal 위치마다 [p-before, p+after] 윈도를 열고, content-signature(순수 lookahead)
    통과 시 그 텍스트를 반환. 중첩표 무관(텍스트 기반). dedup·스캔 상한."""
    positions = sorted(set(m.start() for a in anchors for m in re.finditer(re.escape(a), stripped)))
    out, seen = [], set()
    for p in positions[:max_scan]:
        region = stripped[max(0, p - before):p + after].strip()
        if not sig.search(region):
            continue
        key = region[:70]
        if key in seen:
            continue
        seen.add(key)
        out.append(region)
        if len(out) >= max_regions:
            break
    return out


# ── content-signatures (순수 lookahead, re.S — 소비/backtracking 없음) ──
# 유형자산 토지 명세: 서식변형(SK=취득원가·경방=총장부금액·대한제분=transposed) → 라벨·인접 유연.
_SIG_TANGIBLE_LAND = re.compile(
    r"(?=.*토지)(?=.*(?:취득원가|총장부금액))(?=.*장부금액)(?=.*[\d,]{6,})", re.S)
# 투자부동산 명세(전문가 sig): 취득원가/취득가액·(감가)상각누계액·장부금액 + 토지/건물 + 5자리(산문·CF 배제).
_SIG_INV_PROP = re.compile(
    r"(?=.*투자부동산)(?=.*(?:취득원가|취득가액|총장부금액))(?=.*장부금액)(?=.*(?:토지|건물)\s*[\d,]{5,})", re.S)
# 재평가(FV 반영): '재평가적립금/잉여금' 키워드에 **값이 인접**(자본변동표 자본금 오긁음 방지, 260719 QA).
_SIG_REVAL = re.compile(r"(?:재평가적립금|재평가잉여금)[^\d(]{0,20}[\d,]{6,}", re.S)
# 토지 공정가치/공시지가(신규 규정·자발공시) — 값 **인접**(평가방법 산문 '공시지가를 확인한…' 배제).
_SIG_LAND_FV = re.compile(r"(?:공시지가|토지[^가-힣\n]{0,10}공정가치)[^\d(가-힣]{0,15}[\d,]{5,}", re.S)
# 금융자산 지분증권 원가 vs 시가(신세계·삼성물산 gold). 260719 QA: '지분율'만이면 종속/관계기업투자
# (지분법, 시가 아님)·회계정책 산문(BYC·경방 오탐)을 긁음 → **상장주식/비상장주식**(FVOCI/FVPL 명세
# 고유) + 취득원가 + (공정가치|순자산가액|평가손익) 필수로 tighten. 지분율/장부금액 단독 제거.
_SIG_EQUITY = re.compile(
    r"(?=.*취득원가)(?=.*(?:상장|비상장)\s*(?:주식|지분상품|지분증권))"
    r"(?=.*(?:공정가치|순자산가액|평가손익))", re.S)


def extract_real_asset_valuation(biz_text: str, full_html: str) -> dict[str, Any]:
    """토지·투자부동산 장부가 vs 공정가치/재평가 region을 마크다운으로. 자산저평가주 스크리닝용."""
    txt = _strip(full_html)
    specs = [
        ("유형자산_토지_명세", ("토지",), _SIG_TANGIBLE_LAND),
        ("투자부동산_명세", ("투자부동산",), _SIG_INV_PROP),
        ("재평가", ("재평가적립금", "재평가잉여금"), _SIG_REVAL),
        ("토지_공정가치/공시지가", ("공시지가", "토지의 공정가치"), _SIG_LAND_FV),
    ]
    parts, labels = [], []
    for label, anchors, sig in specs:
        regions = _find_regions(txt, anchors, sig, max_regions=1)
        if regions:
            labels.append(label)
            parts.append(f"### {label}\n{regions[0]}")
    if not parts:
        return {"status": "NOT_APPLICABLE",
                "na_reason": "토지/투자부동산 원가-공정가치 명세 미검출(원가법 단일합계만 or 미공시 — 신규규정 시행 전)"}
    return {"status": "MARKDOWN", "found": labels,
            "markdown": ("\n\n".join(parts))[:14000],
            "note": "장부가 vs 공정가치 gap = 저평가 신호. 토지 공정가치는 공시지가 기준(실거래가 50~70%)이라 보수적 하한."}


def extract_equity_holdings(biz_text: str, full_html: str) -> dict[str, Any]:
    """금융자산 지분증권(상장/비상장) 취득원가 vs 공정가치·평가손익 region을 마크다운으로."""
    txt = _strip(full_html)
    regions = _find_regions(txt, ("상장주식", "비상장주식", "상장지분", "비상장지분"), _SIG_EQUITY,
                            before=180, after=2400, max_regions=1)
    if not regions:
        return {"status": "NOT_APPLICABLE", "na_reason": "지분증권 원가-vs-시가 명세 미공시(총액·민감도만)"}
    return {"status": "MARKDOWN",
            "markdown": ("### 지분증권 보유명세(원가 vs 공정가치)\n" + "\n\n".join(regions))[:12000],
            "note": "상장=공정가치·비상장=순자산가액/공정가치. 취득원가 대비 gap = 평가손익."}
