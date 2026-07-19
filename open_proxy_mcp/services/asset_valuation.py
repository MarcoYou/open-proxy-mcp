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


# 단위 선언 "(단위 : 천원)" 등. DART 재무표는 표별 단위 선언 → region이 그 선언 위를 잘라내면 스케일을
# 잃는다(천원↔백만원=1000배 오밸류). region에 단위가 없으면 **바로 위(근접) 선언**만 붙인다(먼 다른 표의
# 단위를 잘못 붙이면 더 위험 → correct-or-absent). 단위 always-carry 원칙(사용자 지시 260719).
_UNIT_DECL = re.compile(r"단위\s*[:：]\s*([^)\n]{1,24}?)\s*[)\n]")


def _unit_before(txt: str, region_start: int, back: int = 700) -> str | None:
    """region_start 직전 back 이내의 가장 가까운 '(단위: X)' 선언을 반환(근접일 때만 — 오단위 방지)."""
    seg = txt[max(0, region_start - back):region_start]
    last = None
    for last in _UNIT_DECL.finditer(seg):
        pass
    return last.group(1).strip() if last else None


def _find_regions(stripped: str, anchors: tuple[str, ...], sig: re.Pattern,
                  before: int = 140, after: int = 1500, max_regions: int = 1,
                  max_scan: int = 400, require: tuple[str, ...] = (),
                  prefer: tuple[str, ...] = ()) -> list[str]:
    """앵커 literal 위치마다 [p-before, p+after] 윈도를 열고, content-signature(순수 lookahead)
    통과 시 그 텍스트를 반환. 중첩표 무관(텍스트 기반). dedup·스캔 상한.
    region에 단위 선언이 없으면 근접(≤700자) 지배 단위를 앞에 붙임(스케일 유실 방지).
    require: sig가 반드시 포함하는 리터럴 — 값싼 `in`으로 선-프루닝(sig.search보다 ~1000x 빠름,
    회귀무: sig가 매치하려면 어차피 require를 포함해야 하므로 없는 region은 어차피 불일치). 병목 해소.
    prefer: 후보 중 이 리터럴을 포함하는 region이 있으면 그것만 우선 반환(첫-매치 순서보다 우선) —
    사업의 내용(II) 사업장/생산설비 표와 재무 주석(III) 표가 같은 content-signature를 동시에 만족할 때
    (둘 다 취득가액/장부금액 어휘 사용), 진짜 gap 근거(공정가치 등)를 담은 쪽을 고른다(260720)."""
    positions = sorted(set(m.start() for a in anchors for m in re.finditer(re.escape(a), stripped)))
    cands, seen = [], set()
    for p in positions[:max_scan]:
        start = max(0, p - before)
        raw = stripped[start:p + after]
        if require and any(k not in raw for k in require):   # 값싼 프리필터(회귀무)
            continue
        region = raw.strip()
        if not sig.search(region):
            continue
        key = region[:70]
        if key in seen:
            continue
        seen.add(key)
        if not _UNIT_DECL.search(region):        # 단위 유실 시 근접 지배 단위 백필
            u = _unit_before(stripped, start)
            if u:
                region = f"[단위: {u}] {region}"
        cands.append(region)
        if not prefer and len(cands) >= max_regions:   # prefer 없으면 기존과 동일하게 조기 종료
            break
    if prefer:
        preferred = [r for r in cands if all(k in r for k in prefer)]
        if preferred:
            return preferred[:max_regions]
    return cands[:max_regions]


# ── content-signatures (순수 lookahead, re.S — 소비/backtracking 없음) ──
# 유형자산 토지 명세: 컬럼형(취득원가/장부금액) OR **변동표(기초~기말 롤포워드)** OR **당기말/전기말
# 단순 스냅샷**(롤포워드 없는 중소형사 표준 스타일) 셋 다 대응.
# 260719 하드닝: 변동표(토지 기말잔액이 '장부금액' 라벨 없이 기초/취득/기말 컬럼)가 dominant miss였음
# (009440·메디앙스 등 — 워크플로 커버리지가 다수 검출). 실측 검증: 알려진 miss 13/13 복구 · found 회귀 0.
# 260720 전수조사 패널(재무·부동산·공시·가치투자·QA 5인): 당기말/전기말 스냅샷(롤포워드 컬럼 없음, 014440
# 등 중소형사 흔함)이 '기초' 미보유로 미검출 — '전기말'도 대안으로 추가(narrative 오탐 위험 없음, 리터럴
# 동의어 추가일 뿐).
# 담보제공/현금흐름표 오탐(~20% 신규검출)은 markdown-primary(caller가 읽어넘김)+멀티소스 계정대사로 흡수.
_SIG_TANGIBLE_LAND = re.compile(
    r"(?=.*토지)(?=.*[\d,]{6,})(?=.*(?:취득원가|총장부금액|기초|전기말))(?=.*(?:장부금액|장부가액|기말))", re.S)
# 투자부동산 명세(전문가 sig): 취득원가/취득가액·(감가)상각누계액·장부금액 + 토지/건물 + 5자리(산문·CF 배제).
# 260720 전수조사 패널: '원가 또는 간주원가'(IFRS1 최초채택 표준문구)·'취득금액'(취득원가의 실무 동의어)
# 변형이 실제 주석에 흔함(072130·001290 등) — 리터럴 alternation만 추가, 회계 실질 차이 아님(오탐 위험 없음).
_SIG_INV_PROP = re.compile(
    r"(?=.*투자부동산)(?=.*(?:취득원가|취득가액|총장부금액|취득금액|간주원가))(?=.*장부금액)"
    r"(?=.*(?:토지|건물)\s*[\d,]{5,})", re.S)
# 재평가(FV 반영): '재평가적립금/잉여금' 키워드에 **값이 인접**(자본변동표 자본금 오긁음 방지, 260719 QA).
_SIG_REVAL = re.compile(r"(?:재평가적립금|재평가잉여금)[^\d(]{0,20}[\d,]{6,}", re.S)
# 토지 공정가치/공시지가(신규 규정·자발공시) — 값 **인접**(평가방법 산문 '공시지가를 확인한…' 배제).
_SIG_LAND_FV = re.compile(r"(?:공시지가|토지[^가-힣\n]{0,10}공정가치)[^\d(가-힣]{0,15}[\d,]{5,}", re.S)
# 금융자산 지분증권 원가 vs 시가(신세계·삼성물산 gold). 260719 QA: '지분율'만이면 종속/관계기업투자
# (지분법, 시가 아님)·회계정책 산문(BYC·경방 오탐)을 긁음 → **상장주식/비상장주식**(FVOCI/FVPL 명세
# 고유) + 취득원가 + (공정가치|순자산가액|평가손익) 필수로 tighten. 지분율/장부금액 단독 제거.
# 260720 전수조사 패널: '취득원가' 리터럴 고정이 실명 종목(LG유플러스·KT스카이라이프 등) 있는 FVOCI
# 명세를 과잉 배제(040300 등) — 취득가액/총장부금액도 원가 개념의 실무 동의어라 alternation 추가.
# 단 '총장부금액'은 K-IFRS9 매출채권 손상충당금 표에도 똑같이 쓰여(377740 바이오노트 회귀 발견 —
# Data QA 패널이 요구한 오탐 재검증에서 실측) '손상차손누계액'(매출채권 표 고유 용어, 지분증권
# 표는 '차손익적립금'/'평가손익' 사용) 공존 시 배제해 매출채권 오탐만 걸러냄.
_SIG_EQUITY = re.compile(
    r"(?!.*손상차손누계액)"
    r"(?=.*(?:취득원가|취득가액|총장부금액))(?=.*(?:상장|비상장)\s*(?:주식|지분상품|지분증권))"
    r"(?=.*(?:공정가치|순자산가액|평가손익))", re.S)
# 담보제공 자산(asset_holdings haircut): 담보 맥락 + 자산종류 + 장부금액 인접 숫자. 자유청산 NAV에서 차감.
_SIG_PLEDGED = re.compile(
    r"(?=.*담보)(?=.*(?:토지|건물|투자부동산|예금|정기예금|유가증권|주식|사용권))"
    r"(?=.*(?:장부금액|장부가액|담보설정|담보제공))(?=.*[\d,]{6,})", re.S)
# 우발부채·지급보증·계류소송(asset_holdings haircut): 부외 조건부부채. Graham 보수적 NAV.
_SIG_CONTINGENT = re.compile(
    r"(?=.*(?:지급보증|우발부채|우발상황|계류|피고))(?=.*[\d,]{6,})"
    r"(?=.*(?:보증|소송|청구|약정|손해배상|채무))", re.S)


def extract_real_estate(biz_text: str, full_html: str, stripped: str | None = None) -> dict[str, Any]:
    """토지·투자부동산 장부가 vs 공정가치/재평가 region을 마크다운으로. 자산저평가주 스크리닝용.
    stripped: 이미 _strip한 텍스트가 있으면 전달(재strip 회피 — 병목 해소, 회귀무)."""
    txt = stripped if stripped is not None else _strip(full_html)
    specs = [
        # (label, anchors, sig, require, prefer) — require=값싼 선-프루닝, prefer=II/III 동형매치 시 우선 키워드
        ("유형자산_토지_명세", ("토지",), _SIG_TANGIBLE_LAND, (), ()),
        ("투자부동산_명세", ("투자부동산",), _SIG_INV_PROP, ("장부금액",), ("공정가치",)),
        ("재평가", ("재평가적립금", "재평가잉여금"), _SIG_REVAL, (), ()),
        ("토지_공정가치/공시지가", ("공시지가", "토지의 공정가치"), _SIG_LAND_FV, (), ()),
    ]
    parts, labels = [], []
    for label, anchors, sig, require, prefer in specs:
        regions = _find_regions(txt, anchors, sig, max_regions=1, require=require, prefer=prefer)
        if regions:
            labels.append(label)
            parts.append(f"### {label}\n{regions[0]}")
    if not parts:
        return {"status": "NOT_APPLICABLE",
                "na_reason": "토지/투자부동산 원가-공정가치 명세 미검출(원가법 단일합계만 or 미공시 — 신규규정 시행 전)"}
    return {"status": "MARKDOWN", "found": labels,
            "markdown": ("\n\n".join(parts))[:14000],
            "note": "장부가 vs 공정가치 gap = 저평가 신호. 토지 공정가치는 공시지가 기준(실거래가 50~70%)이라 보수적 하한."}


def extract_equity_holdings(biz_text: str, full_html: str, stripped: str | None = None) -> dict[str, Any]:
    """금융자산 지분증권(상장/비상장) 취득원가 vs 공정가치·평가손익 region을 마크다운으로.
    stripped: 이미 _strip한 텍스트가 있으면 전달(재strip 회피). 지분증권 명세는 타법인출자현황 API가
    표준 소스(otrCprInvstmntSttus) — 이 함수는 트레이딩 포트폴리오(FVPL/FVOCI) 보강용."""
    txt = stripped if stripped is not None else _strip(full_html)
    # require 미지정: "취득원가" 단일리터럴 프리필터가 취득가액/총장부금액 표기(040300 등 실명 상장주식
    # 명세)를 과잉 배제해 제거(260720 전수조사) — 앵커(상장/비상장주식)가 이미 좁아 성능 영향 미미.
    regions = _find_regions(txt, ("상장주식", "비상장주식", "상장지분", "비상장지분"), _SIG_EQUITY,
                            before=180, after=2400, max_regions=1)
    if not regions:
        return {"status": "NOT_APPLICABLE", "na_reason": "지분증권 원가-vs-시가 명세 미공시(총액·민감도만)"}
    return {"status": "MARKDOWN",
            "markdown": ("### 지분증권 보유명세(원가 vs 공정가치)\n" + "\n\n".join(regions))[:12000],
            "note": "상장=공정가치·비상장=순자산가액/공정가치. 취득원가 대비 gap = 평가손익."}


def extract_pledged_assets(full_html: str, stripped: str | None = None) -> dict[str, Any]:
    """담보로 제공된 자산 명세 region(markdown). asset_holdings의 NAV haircut — 담보 잡힌 자산은
    자유 청산 불가라 gross 자산에서 차감해야(안 빼면 밸류트랩 과대평가). 값은 caller가 원문 읽어 판단."""
    txt = stripped if stripped is not None else _strip(full_html)
    # 표 제목 명사('담보제공자산'·'담보로 제공한/된 자산')로 앵커 — 동사 '담보로 제공'은 위험정책 산문에 스침.
    regions = _find_regions(txt, ("담보제공자산", "담보로 제공한 자산", "담보로 제공된 자산",
                                  "담보로 제공하고 있는 자산", "담보로 제공하고 있는"),
                            _SIG_PLEDGED, before=60, after=1800, max_regions=1, require=("담보",))
    if not regions:
        return {"status": "NOT_APPLICABLE", "na_reason": "담보제공 자산 주석 미검출(무담보 or 미기재)"}
    return {"status": "MARKDOWN",
            "markdown": ("### 담보제공 자산(자유청산 제약 — NAV 차감)\n" + regions[0])[:8000],
            "note": "담보 잡힌 자산은 청산·매각 시 채권자 우선변제 대상 → 자유 청산가능 NAV에서 제외/할인."}


def extract_contingent(full_html: str, stripped: str | None = None) -> dict[str, Any]:
    """우발부채·지급보증·계류소송 잔액 region(markdown). asset_holdings의 부외부채 haircut —
    계열 지급보증·대규모 소송은 net-net 자산주를 밸류트랩으로 만드는 조건부부채. 값은 caller가 판단."""
    txt = stripped if stripped is not None else _strip(full_html)
    regions = _find_regions(txt, ("지급보증", "우발부채", "우발상황", "계류중인 소송", "계류중"),
                            _SIG_CONTINGENT, before=120, after=1800, max_regions=2,
                            require=())
    if not regions:
        return {"status": "NOT_APPLICABLE", "na_reason": "우발부채·지급보증 주석 미검출(없음 or 미기재)"}
    return {"status": "MARKDOWN",
            "markdown": ("### 우발부채·지급보증·계류소송(부외 조건부부채 — NAV 차감)\n"
                         + "\n\n".join(regions))[:9000],
            "note": "부외 조건부부채는 청산 NAV에서 차감(Graham 보수화). risk_events(사건 트리거)와 달리 상시 잔액 관점."}
