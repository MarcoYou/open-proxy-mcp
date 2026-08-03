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


# ── 연결/별도 기준: 문서가 셀마다 선언한 것을 읽는다 ──────────────────────────
# DART document.xml 은 주석 표의 셀마다 XBRL 컨텍스트를 달아 둔다:
#   ACONTEXT="CFY2025eFY_ifrs-full_ConsolidatedAndSeparateFinancialStatementsAxis
#             _ifrs-full_ConsolidatedMember_dart_NameOfSecuredCreditorAxis_…"
# 기간과 **연결/별도**가 기계로 선언돼 있다. 종전엔 주석 전체를 한 텍스트로 뭉쳐 훑느라
# 이걸 못 봤고, 연결이 있는데 별도 표를 읽고도 그렇다고 말하지 않았다
# (260803 전수: 연결 있는데 별도 56건 · 한 구간에 둘 섞임 15건 = 기준확정 872건 중 71건, 8%).
#
# 추정으로 메우지 않는다 — 텍스트 구역 분할 85.3% · 구간 어휘 88.7%였다. 기준을 11~15%
# 틀리게 말하는 건 지금 고치려는 결함 그 자체라, **선언이 없으면 기준을 내지 않는다.**
_ACONTEXT_RE = re.compile(r"""ACONTEXT\s*=\s*["']([^"']{1,400})""", re.I)
_UNIT_PREFIX_RE = re.compile(r"^\[단위:[^\]]*\]\s*")
# 주석 절 제목 — 셀 선언과 **독립된 둘째 신호**. 둘이 어긋나면(실측 858건 중 42건, 4.9%)
# 어느 쪽이 틀린 건지 우리가 알 수 없으니 조용히 고르지 않고 그대로 드러낸다.
_NOTE_SECTION_RE = re.compile(
    r"""<TITLE[^>]*>\s*([^<]{0,40}?(?:연결\s*재무제표\s*주석|재무제표\s*주석)[^<]{0,10})""", re.I)


def _strip_with_map(html: str) -> tuple[str, list[int]]:
    """`_strip` 과 같은 문자열을 내되, 각 출력 글자가 온 html 인덱스를 함께 돌려준다.

    결과 region 만으로는 ACONTEXT 를 되찾을 수 없다 — 문구를 html 에서 다시 검색하는
    방식은 공백 뭉개짐 탓에 40%가 실패했다(260803 실측). 그래서 변환을 글자 단위로
    따라가며 대응표를 만든다. 비용 문서당 +41ms.
    """
    out: list[str] = []
    pos: list[int] = []
    last = 0
    for m in _TAG_RE.finditer(html):
        for i in range(last, m.start()):
            out.append(html[i])
            pos.append(i)
        out.append(" ")
        pos.append(m.start())
        last = m.end()
    for i in range(last, len(html)):
        out.append(html[i])
        pos.append(i)
    text = "".join(" " if c == "\n" else c for c in out)
    o2: list[str] = []
    p2: list[int] = []
    i = 0
    for m in _WS_RE.finditer(text):
        while i < m.start():
            o2.append(text[i])
            p2.append(pos[i])
            i += 1
        o2.append(" ")
        p2.append(pos[m.start()])
        i = m.end()
    while i < len(text):
        o2.append(text[i])
        p2.append(pos[i])
        i += 1
    return "".join(o2), p2


class BasisIndex:
    """주석 원문의 셀별 연결/별도 선언을 들고 있다가, 추출 결과에 기준을 붙여 준다."""

    __slots__ = ("stripped", "_pos", "_marks", "_has_cons", "_sections")

    def __init__(self, html: str):
        self.stripped, self._pos = _strip_with_map(html or "")
        marks = []
        for m in _ACONTEXT_RE.finditer(html or ""):
            ctx = m.group(1)
            if "ConsolidatedMember" in ctx:
                marks.append((m.start(), "연결"))
            elif "SeparateMember" in ctx:
                marks.append((m.start(), "별도"))
        self._marks = marks
        self._has_cons = any(b == "연결" for _, b in marks)
        self._sections = [(m.start(), "연결" if "연결" in m.group(1) else "별도")
                          for m in _NOTE_SECTION_RE.finditer(html or "")]

    def _section_at(self, html_pos: int) -> str | None:
        found = None
        for p, k in self._sections:
            if p <= html_pos:
                found = k
            else:
                break
        return found

    def annotate(self, result: dict) -> dict:
        """`basis`(그리고 필요하면 `basis_conflict`)를 붙인다(in place). 선언 없으면 안 붙인다.

        구간은 **하나씩 따로** 짚는다. 이어붙인 전체 길이로 범위를 잡으면 서로 떨어져 있는
        두 구간 사이의 남남인 본문까지 쓸어 담는다 — 260803 대한방직에서 연결 주석의
        첫 구간에서 출발해 별도 주석 셀 44개를 세고 「별도」라 판정했다.
        `_find_regions` 가 돌려주는 구간엔 줄바꿈이 없으므로(공백으로 접혀 있다) 한 줄 = 한 구간이다.
        """
        if not isinstance(result, dict) or result.get("status") != "MARKDOWN":
            return result
        if not self._pos:
            return result
        cons = sep = 0
        sect = None
        for line in (result.get("markdown") or "").split("\n"):
            if line.startswith("###") or not line.strip():
                continue
            region = _UNIT_PREFIX_RE.sub("", line).strip()
            if len(region) < 20:
                continue
            i = self.stripped.find(region[:60])
            if i < 0:
                continue
            j = min(i + len(region), len(self._pos) - 1)
            lo, hi = self._pos[i], self._pos[j]
            if sect is None:
                sect = self._section_at(lo)
            seen = [b for p, b in self._marks if lo <= p <= hi]
            cons += seen.count("연결")
            sep += seen.count("별도")
        if cons and sep:
            result["basis"] = "연결" if cons >= sep else "별도"
            result["basis_conflict"] = (
                f"이 구간에 연결 표와 별도 표가 함께 들어 있습니다(연결 {cons} · 별도 {sep} 셀) "
                "— 값을 섞어 읽지 마세요.")
        elif cons:
            result["basis"] = "연결"
        elif sep:
            result["basis"] = "별도"
            if self._has_cons:
                result["basis_conflict"] = ("별도 재무제표 주석을 읽었습니다 "
                                            "— 같은 보고서에 연결 주석도 있습니다.")
        if result.get("basis") and sect and sect != result["basis"]:
            # 셀 선언과 절 제목이 어긋난다 — 공시 자체의 태깅 불일치일 수 있어 판단하지 않는다.
            note = (f"이 구간은 「{sect} 재무제표 주석」 절에 있는데 셀 선언은 「{result['basis']}」"
                    "입니다 — 공시의 표기가 서로 어긋나니 원문을 확인하세요.")
            result["basis_conflict"] = ((result.get("basis_conflict") or "") + " " + note).strip()
        return result


_NOTE_ABSENT_RE = re.compile(
    r"(?:없습니다|없음|해당\s*사항\s*(?:이|은)?\s*없|존재하지\s*않|미해당)")
# 「여기 말고 저기 있다」 — 부재도 미탐도 아닌 셋째 경우(「(주석 5참조)」).
_NOTE_XREF_RE = re.compile(r"주석\s*\d{1,2}\s*(?:번호?)?\s*참(?:조|고)|참(?:조|고)하[시여]|참(?:조|고)\s*바랍")
# 표가 뒤따르는지 — DART 표 머리(단위 선언·「다음과 같습니다」)와 홀로 선 숫자 토큰.
_NOTE_TABLE_RE = re.compile(r"\(\s*단위|단위\s*[::]|다음과\s*같습니다")
_NOTE_NUM_RE = re.compile(r"(?<=\s)[\d,]+(?:\.\d+)?%?(?=\s)")
# 실제 금액 — 목차와 본문을 가르는 값. 목차의 숫자는 쪽번호(2~3자리)뿐이다.
_REAL_FIGURE_RE = re.compile(r"[\d,]{6,}|\(\s*단위")
# 「우리가 못 읽었다」고 말하려면 **그 필드의 명세**여야 한다. 어휘만 스친 다른 표
# (가격위험 민감도·공정가치 서열·재무상태표 줄·차입금 내역)를 미탐이라 부르면 과잉 주장이다
# — 260803 표본 판정에서 equity 143건·real_estate 17건이 대부분 그랬다.
_DETAIL_WINDOW = 600
_DETAIL_HINTS = {
    "지분증권": re.compile(r"취득원가|취득가액|총장부금액|취득금액"),          # 원가 vs 시가 축
    "토지·투자부동산": re.compile(r"공정가치|공시지가|재평가|간주원가"),        # 장부가 vs 공정가치 축
    "담보제공 자산": re.compile(r"담보권자|담보설정|설정금액|채권최고액|설정권자|담보제공처"),
}


def _body_pos(stripped: str, anchors: tuple[str, ...]) -> int | None:
    """앵커가 **본문**에 나오는 첫 위치. 목차는 건너뛴다.

    260803 실측: 정기보고서 앞머리 목차에 「우발부채」·「지분증권」이 그대로 실려 있어서
    `find()` 로 첫 위치를 잡으면 목차를 읽고 판정하게 된다(equity_holdings 미탐 83%가
    전부 이것이었다). 목차 줄의 숫자는 쪽번호뿐이라, **여섯 자리 금액이나 단위 선언이
    곁에 있는 위치**만 본문으로 친다.
    """
    best = None
    for a in anchors:
        for m in re.finditer(re.escape(a), stripped):
            if not _REAL_FIGURE_RE.search(stripped[m.start():m.start() + 500]):
                continue
            if best is None or m.start() < best:
                best = m.start()
            break
    return best


def _excerpt_at(stripped: str, pos: int, before: int = 40, after: int = 70) -> str:
    """원문 위치를 **번호로 추론하지 않고 문구로** 준다.

    주석 번호를 되짚어 뽑아 보았으나(「22. 우발부채와 약정사항」) 실측에서 한 칸 앞 주석이나
    II장 소제목을 집는 일이 잦았다 — 틀린 번호는 읽는 쪽을 엉뚱한 데로 보내니 없느니만 못하다.
    대신 그 자리의 원문을 그대로 인용한다. 인용은 추론이 아니라 사실이라 틀릴 수 없다.
    """
    return re.sub(r"\s+", " ", stripped[max(0, pos - before):pos + after]).strip()


def _excerpt_of_region(stripped: str, region: str) -> str | None:
    """값을 낸 region 이 원문 어디였는지 — 그 자리 문구를 인용한다.
    region 앞에 백필된 「[단위: X] 」는 원문에 없으므로 떼고 찾는다."""
    probe = re.sub(r"^\[단위:[^\]]*\]\s*", "", region)[:60]
    i = stripped.find(probe)
    return _excerpt_at(stripped, i) if i >= 0 else None


def _absence_verdict(stripped: str, anchors: tuple[str, ...], what: str) -> dict[str, Any]:
    """값이 없을 때 **왜 없는지**를 가른다 — business_details 와 같은 어휘.

    이 모듈은 앵커(어휘)를 찾고 content-signature 로 검증하는 2단이라, 실패 원인이 코드 안에서
    이미 갈려 있는데 바깥으로는 전부 NOT_APPLICABLE 한 갈래로만 나갔다("무담보 or 미기재" —
    읽는 쪽이 어느 쪽인지 알 수 없다). 그 둘을 그대로 꺼낸다.
      not_disclosed     — 주석에 어휘 자체가 없거나, 있어도 「없습니다」라고 밝혔다
      cross_reference   — 어휘는 있는데 「(주석 5참조)」처럼 다른 절을 가리킨다
      narrative_only    — 어휘는 있는데 표 없이 산문(회계정책·시장위험 서술)뿐이다
      extraction_failed — 어휘와 표가 있는데 못 읽었다(= 개선 지점)
    """
    pos = _body_pos(stripped, anchors)
    if pos is None:
        return {"absence_kind": "not_disclosed",
                "absence_note": f"재무제표 주석에 「{anchors[0]}」 관련 기재가 없습니다."}
    out: dict[str, Any] = {"absence_excerpt": _excerpt_at(stripped, pos)}
    win = stripped[pos:pos + 250]
    m = _NOTE_ABSENT_RE.search(win)
    if m:
        out["absence_kind"] = "not_disclosed"
        out["absence_note"] = (f"주석은 있으나 회사가 부재를 밝혔습니다 — "
                               f"「…{win[max(0, m.start() - 40):m.end() + 5].strip()}…」")
        return out
    if _NOTE_TABLE_RE.search(win) or len(_NOTE_NUM_RE.findall(win)) >= 3:
        hint = _DETAIL_HINTS.get(what)
        if hint and not hint.search(stripped[pos:pos + _DETAIL_WINDOW]):
            # 어휘는 있으나 우리가 찾는 명세가 아니다 — 미탐이라 부르면 거짓이 된다.
            out["absence_kind"] = "not_disclosed"
            out["absence_note"] = (f"{what} 언급은 있으나 우리가 찾는 명세가 아닙니다 "
                                   "(총액·민감도·공정가치 서열 등 다른 표) — 인용 위치를 확인하세요.")
            return out
        out["absence_kind"] = "extraction_failed"
        out["absence_note"] = (f"원문에 {what} 표가 있으나 검증하지 못했습니다 "
                               "— 인용 위치를 원문에서 확인하세요.")
        return out
    x = _NOTE_XREF_RE.search(win)
    if x:
        out["absence_kind"] = "cross_reference"
        out["absence_note"] = (f"{what} 기재가 다른 절을 가리킵니다 — "
                               f"「…{win[max(0, x.start() - 40):x.end() + 5].strip()}…」")
        return out
    out["absence_kind"] = "narrative_only"
    out["absence_note"] = f"{what} 언급은 있으나 표 없이 산문 서술뿐이라 수치를 낼 수 없습니다."
    return out


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
# FVPL 상장주식 롤포워드형 보유명세(260721, 서희건설 실사용 발견) — 위 _SIG_EQUITY는 "원가 vs
# 시가" 비교표 전용인데, 트레이딩 포트폴리오(당기손익-공정가치측정금융자산)는 원가 컬럼 없이
# **종목별 기초금융자산→매입/매도/평가손익→기말금융자산** 롤포워드로만 공시하는 경우가 흔함
# (삼성바이오로직스·테슬라·엔비디아·팔란티어 등 실명 종목 + KODEX/TIGER/SOXX 등 ETF 혼재).
# "상장주식의 내역" 소제목 앵커 + 롤포워드 고유 컬럼명(기초금융자산·기말금융자산)으로 지목.
# 위 배제(`손상차손누계액`)는 **총장부금액이 매출채권 손상충당금 표에도 쓰이기 때문**에 넣은
# 것인데, 2,400자 창 안 **다른 주석**에 그 낱말이 있으면 진짜 지분증권 명세까지 죽인다
# (260803 실측: 미탐 20건 중 16건이 이것). 그래서 배제를 **총장부금액만 있을 때로 한정**한
# 완화판을 둔다. 취득원가·취득가액은 매출채권 표가 쓰지 않는 어휘라 배제할 이유가 없다.
# 앵커 순서가 바뀌어 기존 구간이 밀리지 않도록 **위에서 못 찾았을 때만** 쓴다.
_SIG_EQUITY_RELAXED = re.compile(
    r"(?=.*(?:상장|비상장)\s*(?:주식|지분상품|지분증권))"
    r"(?=.*(?:공정가치|순자산가액|평가손익))"
    r"(?:(?=.*(?:취득원가|취득가액|취득금액))|(?=.*총장부금액)(?!.*손상차손누계액))", re.S)
_SIG_EQUITY_FVPL_ROLL = re.compile(
    r"(?=.*상장주식의\s*내역)(?=.*기초금융자산)(?=.*기말금융자산)(?=.*[\d,]{5,})", re.S)
# 담보제공 자산(asset_holdings haircut): 담보 맥락 + 자산종류 + 장부금액 인접 숫자. 자유청산 NAV에서 차감.
_SIG_PLEDGED = re.compile(
    r"(?=.*담보)(?=.*(?:토지|건물|투자부동산|예금|정기예금|유가증권|주식|사용권))"
    r"(?=.*(?:장부금액|장부가액|담보설정|담보제공))(?=.*[\d,]{6,})", re.S)
# 담보물 **종류**가 아니라 담보 표의 **열**로 지목한다. `_SIG_PLEDGED` 는 자산종류 화이트리스트
# (토지·건물·예금·주식…)를 요구하는데, 실제 담보물은 보험·지분증권·출자금·외화예치금·재고자산처럼
# 회사마다 제각각이라 그 축이 틀렸다(260803: 남은 미탐 16건이 전부 이 원인). 종류는 갈려도
# 「누가 잡았나(담보권자·담보제공처)·얼마로 잡았나(담보설정금액·채권최고액)」 열은 공통이다.
_SIG_PLEDGED_COLS = re.compile(
    r"(?=.*담보)"
    r"(?=.*(?:담보권자|담보설정금액|담보제공처|채권최고액|설정권자|질권금액))"
    r"(?=.*(?:장부금액|장부가액|담보설정|담보제공))(?=.*[\d,]{6,})", re.S)
# 축을 넓히면 「담보제공 내역 - 해당사항 없음」 같은 **부재 선언 문단**까지 들어온다(실측).
_PLEDGED_NA_RE = re.compile(
    r"해당\s*사항\s*(?:이|은)?\s*없|해당\s*없|담보는\s*없|제공한\s*담보가?\s*없"
    r"|담보\s*제공\s*(?:내역|현황)?\s*[-–:]?\s*없")
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
                "na_reason": "토지/투자부동산 원가-공정가치 명세 미검출(원가법 단일합계만 or 미공시 — 신규규정 시행 전)",
                **_absence_verdict(txt, ("투자부동산", "토지"), "토지·투자부동산")}
    return {"status": "MARKDOWN", "found": labels,
            "source_excerpt": _excerpt_of_region(txt, parts[0].split("\n", 1)[-1]),
            "markdown": ("\n\n".join(parts))[:14000],
            "note": "장부가 vs 공정가치 gap = 저평가 신호. 토지 공정가치는 공시지가 기준(실거래가 50~70%)이라 보수적 하한."}


def extract_equity_holdings(biz_text: str, full_html: str, stripped: str | None = None) -> dict[str, Any]:
    """금융자산 지분증권(상장/비상장) 취득원가 vs 공정가치·평가손익 region을 마크다운으로.
    stripped: 이미 _strip한 텍스트가 있으면 전달(재strip 회피). 지분증권 명세는 타법인출자현황 API가
    표준 소스(otrCprInvstmntSttus) — 이 함수는 트레이딩 포트폴리오(FVPL/FVOCI) 보강용."""
    txt = stripped if stripped is not None else _strip(full_html)
    # require 미지정: "취득원가" 단일리터럴 프리필터가 취득가액/총장부금액 표기(040300 등 실명 상장주식
    # 명세)를 과잉 배제해 제거(260720 전수조사) — 앵커(상장/비상장주식)가 이미 좁아 성능 영향 미미.
    specs = [
        ("지분증권_원가vs공정가치", ("상장주식", "비상장주식", "상장지분", "비상장지분"), _SIG_EQUITY, 180, 2400),
        ("FVPL_상장주식_보유명세(종목별)", ("상장주식의 내역",), _SIG_EQUITY_FVPL_ROLL, 180, 2600),
    ]
    parts, labels = [], []
    for label, anchors, sig, before, after in specs:
        regions = _find_regions(txt, anchors, sig, before=before, after=after, max_regions=1)
        if regions:
            labels.append(label)
            parts.append(f"### {label}\n{regions[0]}")
    if not parts:
        regions = _find_regions(txt, ("상장주식", "비상장주식", "상장지분", "비상장지분"),
                                _SIG_EQUITY_RELAXED, before=180, after=2400, max_regions=1)
        if regions:
            labels.append("지분증권_원가vs공정가치")
            parts.append(f"### 지분증권_원가vs공정가치\n{regions[0]}")
    if not parts:
        return {"status": "NOT_APPLICABLE", "na_reason": "지분증권 원가-vs-시가 명세 미공시(총액·민감도만)",
                **_absence_verdict(txt, ("상장주식", "비상장주식", "상장지분", "비상장지분"), "지분증권")}
    return {"status": "MARKDOWN", "found": labels,
            "source_excerpt": _excerpt_of_region(txt, parts[0].split("\n", 1)[-1]),
            "markdown": ("\n\n".join(parts))[:14000],
            "note": "상장=공정가치·비상장=순자산가액/공정가치. 취득원가 대비 gap = 평가손익. "
                    "FVPL 보유명세(종목별)는 원가 비교가 아니라 기초~기말 롤포워드(트레이딩 포트폴리오 시가평가 변동)."}


def extract_pledged_assets(full_html: str, stripped: str | None = None) -> dict[str, Any]:
    """담보로 제공된 자산 명세 region(markdown). asset_holdings의 NAV haircut — 담보 잡힌 자산은
    자유 청산 불가라 gross 자산에서 차감해야(안 빼면 밸류트랩 과대평가). 값은 caller가 원문 읽어 판단."""
    txt = stripped if stripped is not None else _strip(full_html)
    # 표 제목 명사('담보제공자산'·'담보로 제공한/된 자산')로 앵커 — 동사 '담보로 제공'은 위험정책 산문에 스침.
    regions = _find_regions(txt, ("담보제공자산", "담보로 제공한 자산", "담보로 제공된 자산",
                                  "담보로 제공하고 있는 자산", "담보로 제공하고 있는"),
                            _SIG_PLEDGED, before=60, after=1800, max_regions=1, require=("담보",))
    if not regions:
        # 표를 이끄는 다른 문구들. 앵커를 「담보로 제공된/되어」처럼 넓히면 회수 104건에
        # **기존 값 514건이 바뀌고**, 「…담보로 제공되어 있습니다(주석 5참조)」 같은 산문에서
        # 출발해 다음 주석 표를 끌어온다(260803 실측). 그래서 넓히지 않고, 표 제목으로 쓰이는
        # 문구만 골라 **위에서 못 찾았을 때만** 본다 — 회수 59건, 손실·내용변경 0.
        regions = _find_regions(
            txt, ("담보로 제공된 금융자산", "사용이 제한되거나 담보로 제공", "담보로 제공된 보험",
                  "채무를 위하여 담보로 제공", "담보로 제공되어 있는 유형자산",
                  "담보로 제공된 유형자산", "담보로 제공된 자산에 대한 공시",
                  "담보제공 내역", "담보제공 현황"),
            _SIG_PLEDGED, before=60, after=1800, max_regions=1, require=("담보",))
    if not regions:
        # 담보물 종류가 사전에 없는 경우(보험·지분증권·출자금·외화예치금…) — 열로 지목한다.
        cols = _find_regions(
            txt, ("담보제공자산", "담보로 제공된 자산에 대한 공시", "담보로 제공한 자산",
                  "담보로 제공된 자산", "담보로 제공하고 있는", "담보제공 내역", "담보제공 현황",
                  "담보로 제공된"),
            _SIG_PLEDGED_COLS, before=60, after=1800, max_regions=1, require=("담보",))
        if cols and not _PLEDGED_NA_RE.search(cols[0][:260]):
            regions = cols
    if not regions:
        return {"status": "NOT_APPLICABLE", "na_reason": "담보제공 자산 주석 미검출(무담보 or 미기재)",
                **_absence_verdict(txt, ("담보제공자산", "담보로 제공"), "담보제공 자산")}
    return {"status": "MARKDOWN",
            "source_excerpt": _excerpt_of_region(txt, regions[0]),
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
        return {"status": "NOT_APPLICABLE", "na_reason": "우발부채·지급보증 주석 미검출(없음 or 미기재)",
                **_absence_verdict(txt, ("우발부채", "지급보증", "우발상황"), "우발부채·지급보증")}
    return {"status": "MARKDOWN",
            "source_excerpt": _excerpt_of_region(txt, regions[0]),
            "markdown": ("### 우발부채·지급보증·계류소송(부외 조건부부채 — NAV 차감)\n"
                         + "\n\n".join(regions))[:9000],
            "note": "부외 조건부부채는 청산 NAV에서 차감(Graham 보수화). risk_events(사건 트리거)와 달리 상시 잔액 관점."}
