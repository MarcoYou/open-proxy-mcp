"""business_details — DART "II. 사업의 내용" 사업부문 데이터 추출 (신규 tool 서비스).

설계·근거: wiki/decisions/260717_1220_decision_business-content-tool-roadmap.md
           원본 census(156사)·검증부록: wiki/_local/census-biz-content-260717/

이 모듈은 순수 파싱 함수(text→struct)로 구성해 캐시로 오프라인 검증 가능하게 한다.
live fetch 오케스트레이션(build_business_content_payload)은 P0-A/B 확정 후 추가.

파싱 원칙(CLAUDE.md): XML 단독 · 이름기반 열매핑(위치 금지) · 표별 단위 파싱 ·
조정/총계 열 분리 · 결측 3분류(NOT_APPLICABLE/NOT_COLLECTED/EXTRACTION_FAILED).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ── 결측 3분류 ──
NOT_APPLICABLE = "NOT_APPLICABLE"      # 구조적 부재(단일부문·폼 소절 부재) — 정상
NOT_COLLECTED = "NOT_COLLECTED"        # 미수집(분기 주석 등) — 데이터 없음
EXTRACTION_FAILED = "EXTRACTION_FAILED"  # 파싱 실패 — 버그/엣지
OK = "OK"

# ── 폼 판별 (목차 소절 제목 기반, KSIC 불신) ──
FORM_STANDARD = "standard7"
FORM_FINANCIAL = "financial5"
FORM_REIT = "reit"
FORM_DUAL = "dual"


def detect_form(toc: list[dict]) -> str:
    """viewer treeData toc(소절 제목 목록)로 문서 폼 판별.

    toc = [{"lvl":int, "text":str, "length":str}, ...]  (biz_census 캐시 포맷)
    """
    titles = [t.get("text", "") for t in toc]
    joined = " ".join(titles)
    # 이중템플릿: 소절 '사업의 내용'류가 (제조서비스업)+(금융업) 두 벌
    if joined.count("영업의 현황") >= 2 or (("(금융업)" in joined) and ("(제조" in joined or "(서비스" in joined)):
        return FORM_DUAL
    # REIT: 투자부동산/임차인/부동산투자회사
    if any(k in joined for k in ("부동산투자회사", "투자부동산 내역", "임차인", "자산개요")):
        return FORM_REIT
    # 금융폼: 재무건전성/영업의 현황/영업설비 (원재료·생산 없음)
    fin_marks = sum(1 for k in ("재무건전성", "영업의 현황", "영업설비") if k in joined)
    has_mfg = ("원재료 및 생산설비" in joined) or ("주요 제품 및 서비스" in joined)
    if fin_marks >= 1 and not has_mfg:
        return FORM_FINANCIAL
    return FORM_STANDARD


# ── 숫자·단위 파싱 ──
_UNIT_MAP = {"원": 1, "천원": 1_000, "백만원": 1_000_000, "십억원": 1_000_000_000, "억원": 100_000_000, "백만": 1_000_000}
_UNIT_RE = re.compile(r"\(단위\s*[:：]?\s*([가-힣]+)")
_NUM_RE = re.compile(r"^\(?\s*[△▲-]?\s*[\d,]+\s*\)?$")


def parse_unit(region: str, default: str = "백만원") -> tuple[str, int]:
    """표 구간에서 '(단위 : 백만원)' 라벨 파싱 → (단위명, 배수). 없으면 default."""
    m = _UNIT_RE.search(region)
    if m and m.group(1) in _UNIT_MAP:
        return m.group(1), _UNIT_MAP[m.group(1)]
    return default, _UNIT_MAP.get(default, 1_000_000)


def parse_number(s: str) -> Optional[float]:
    """'1,234' / '(1,234)' / '△1,234' → float(음수 처리). 숫자 아니면 None."""
    s = s.strip()
    if not _NUM_RE.match(s):
        return None
    neg = s.startswith("(") or s.startswith("△") or s.startswith("▲") or s.lstrip().startswith("-")
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        return None
    v = float(digits)
    return -v if neg else v


# ── 이익/수익 지표 라벨 화이트리스트 (다형성 대응) ──
# 우선순위: 외부/순매출(내부거래 제외) → 부문수익 → 총매출. '매출총이익'(=이익)과 배타.
REVENUE_LABELS = ["외부고객으로부터의수익", "외부고객으로부터의 수익", "외부매출액", "순매출액",
                  "총부문수익", "부문수익", "영업수익(매출액)", "영업수익", "수익(매출액)", "매출액", "총매출액"]
PROFIT_LABELS = ["영업이익(손실)", "영업이익", "영업손익", "계속영업이익(손실)", "계속영업이익", "부문영업이익",
                 "당기순이익(손실)", "당기순이익", "법인세비용차감전순이익", "법인세차감전순이익",
                 "부문이익", "보고부문이익", "매출총이익"]

# 집계/조정 열 토큰(부문 아님) — substring 매칭으로 배제. '기타부문'은 실 leaf라 제외 안 함(회귀주의).
_AGG_RE = re.compile(r"합\s*계|총\s*계|조\s*정|제\s*거|소\s*계|내부\s*(거래|매출|고객)|미배분|부문\s*간|연결\s*조정")
# 헤더가 아닌 컬럼머리·연도·회사명(㈜)·고객·비율 — ㈜/주식회사 포함 시 회사명(종속사·임차인)이라 부문 아님
_NONSEG_RE = re.compile(r"^20\d\d\s*년|^\d{4}\.\d|분기$|당기말?$|전기말?$|^제\s*\d+\s*[\(（기]|"
                        r"^[A-Z]?\s?사$|^주요\s*고객|고객사|^부\s*문|^구\s*분|^비\s*율|^금\s*액|^비\s*중|"
                        r"^지\s?역|^회사명?$|^회\s?사$|^\(?주\)?$|㈜|주식회사|\(주\)")
# 사업설명 문구(부문명 아님): 쉼표+'등'/'및'을 포함한 나열형 서술
_DESC_RE = re.compile(r"[,，].*(등|및)|(제조|판매|공사|사업).*(및|등)\s*$|용역의|재화나")

# 세그먼트 표 헤더에서 부문명이 아닌 boilerplate (제거 대상)
_HEADER_NOISE = {"기업 전체 총계", "기업 전체 총계 합계", "영업부문", "중요한 조정사항",
                 "부문간 제거한 금액", "부문간제거", "부문간 제거", "부문", "부문 합계",
                 "보고부문", "조정", "합계", "합 계", "소계", "당기", "전기", "전전기", "구분", "구 분",
                 "영업부문에 대한 공시", "영업부문에 대한 정보", "부문별 정보", "부문정보",
                 "연결조정", "연결조정 등", "연결합계", "연결 합계", "계", "사업부문", "금 액", "비 중",
                 "금액", "비중", "내부매출액", "총매출액"}

# 본문게시 세그먼트 요약 재무표 앵커 (①본문 → ②주석 fallback 순서의 ①)
_BODY_ANCHOR = re.compile(r"사업부문별\s*(요약\s*)?재무\s*(현황|정보)|부문별\s*재무\s*(현황|정보)|사업부문별\s*요약\s*재무")
# 본문 표의 연도블록 경계 ('(N기)' / '(1)' / '(2)')
_BODY_YEAR = re.compile(r"(?m)^\s*(?:\(\d+\)|[①②③]|제\s*\d+\s*[\(（])")

# 세그먼트 노트 제목 앵커: 'N. 부문별 정보/영업부문 (연결)'
_NOTE_ANCHOR = re.compile(
    r"(?m)^\s*(\d{1,2})\.\s*(부문별?\s*정보|부문\s*정보|영업부문(?:에\s*대한\s*정보)?|부문별\s*보고)\s*(\(연결\)|\(별도\))?\s*$"
)
_NEXT_NOTE = re.compile(r"(?m)^\s*(\d{1,2})\.\s+\S")
# 단일부문 선언 (다양한 표현: '하나의 보고 부문', '단일의 영업', '보고부문을 가지고')
# 단일부문 '선언' (과탐 방지 — 반드시 선언 맥락: 가지/보유/영위/운영/수행/으로/공시하지않)
_SINGLE_DECL = re.compile(
    r"보고\s*부문이?\s*단일|하나의?\s*(영업|보고)\s*부문을?\s*(가지|보유|영위|구성|운영)|"
    r"단일\s*(사업|영업)\s*부문(으로|이며|이고|이라|을\s*영위|으로\s*운영|을\s*운영)|"
    r"단일부문(으로|이며|이고|이라)|부문별?\s*정보를?\s*(공시|기재)하지\s*않|"
    r"단일의?\s*영업을?\s*(수행|영위)|단일\s*사업부문으로\s*운영"
)

# 세그먼트 헤더 수집을 멈추는 프로즈 divider (부문명 vs 제품설명·지표행 경계)
_HEADER_STOP = ("각 보고부문이", "보고부문을 식별", "각 부문의", "각 영업부문", "부문에 대한 정보의 성격")
# 지표행 라벨(부문명 아님) — 헤더에서 제외
_METRIC_ROW_NOISE = {"유ㆍ무형 감가상각비", "유·무형 감가상각비", "감가상각비", "무형자산상각비", "감가상각비및무형자산상각비",
                     "자산", "부채", "순매출액", "순매출액 합계", "내부거래", "부문간수익", "부문간 수익",
                     "부문자산", "부문부채", "자본적지출", "이자수익", "이자비용", "법인세비용", "총자산", "총부채",
                     "매출총이익", "판매비와관리비", "지분법손익", "수익(매출액)", "재화", "용역의 제공", "재화의 판매",
                     "주요 고객", "완성차업체 등", "수익", "매출", "매 출", "외부고객 매출", "내부고객 매출",
                     "외부매출", "외부매출액", "고객 매출", "외부고객으로부터의 매출", "총매출액", "순매출액",
                     "자 산", "부 채", "자 본", "총 자산", "총 부채", "매출 및 영업수익", "영업 수익"}


@dataclass
class SegmentProfit:
    status: str = EXTRACTION_FAILED
    source: str = ""            # note | body | none
    profit_metric: str = ""     # 뽑은 지표명 병기
    revenue_metric: str = ""
    unit: str = ""
    segments: list = field(default_factory=list)   # [{name, revenue, profit}]
    adjustments: list = field(default_factory=list)  # 조정/합계 열 (부문 아님)
    note_source: str = ""       # 연결/별도
    anchor: str = ""            # 매칭된 note-title
    na_reason: str = ""
    raw_value_counts: dict = field(default_factory=dict)


def find_segment_note_region(note_full_text: str) -> tuple[Optional[str], Optional[str]]:
    """full 주석에서 note-title 앵커 → 다음 번호주석 경계까지 세그먼트 표 구간 반환.

    반환 (anchor_title, region_text) 또는 (None, None).
    회계정책 문단('2.x 영업부문 ... (주석 N 참조)')은 (연결) 꼬리표/표마커 부재로 자연 배제.
    """
    matches = list(_NOTE_ANCHOR.finditer(note_full_text))
    if not matches:
        return None, None
    # 여러 매치면 실제 데이터표(표마커 '영업부문에 대한 공시'+숫자 다수)를 가진 마지막을 우선
    best = None
    for m in matches:
        start = m.end()
        nxt = _NEXT_NOTE.search(note_full_text, start + 1)
        end = nxt.start() if nxt else min(len(note_full_text), start + 40000)
        region = note_full_text[m.start():end]
        # 표 존재 게이트: 단위 + 지표라벨 + 숫자행 다수
        has_unit = "(단위" in region
        has_metric = any(lb in region for lb in (PROFIT_LABELS + REVENUE_LABELS))
        numrows = len(re.findall(r"[\d,]{4,}", region))
        if has_unit and has_metric and numrows >= 6:
            best = (m.group(0).strip(), region)  # 마지막 유효 매치로 갱신
    if best:
        return best
    # 표게이트 실패 — 앵커는 있으나 표 없음(정책문단만) → None
    return None, None


def find_body_segment_region(biz_content_text: str) -> tuple[Optional[str], Optional[str]]:
    """본문(II.사업의 내용)의 '사업부문별 요약 재무현황/재무정보' 표 구간(첫 연도블록).

    반환 (anchor_title, region) 또는 (None, None). 주석보다 우선(fallback ①).
    """
    m = _BODY_ANCHOR.search(biz_content_text or "")
    if not m:
        return None, None
    start = m.end()
    # 첫 연도블록 다음('(2)'/'② 제N기') 경계까지, 없으면 8000자
    nxt = _BODY_YEAR.search(biz_content_text, start + 200)
    end = nxt.start() if nxt else min(len(biz_content_text), start + 8000)
    region = biz_content_text[m.start():end]
    # 표 존재 게이트
    has_unit = "(단위" in region
    has_metric = any(lb in region for lb in (PROFIT_LABELS + REVENUE_LABELS))
    numrows = len(re.findall(r"[\d,]{4,}", region))
    if has_unit and has_metric and numrows >= 4:
        return m.group(0).strip(), region
    return None, None


_YEAR_MARK = re.compile(r"^제\s*\d+\s*[\(（기]|^\(\d+\)|^[①②③④⑤]")
# 값-유사 라인: 숫자 / 퍼센트 / 대시 placeholder (표 데이터 셀)
_VALUE_LINE = re.compile(r"^\(?\s*[△▲-]?\s*[\d,]+\.?\d*\s*%?\s*\)?$|^-$|^\s*-\s*$")


def _first_data_row_line(lines: list[str]) -> int:
    """첫 '데이터행'(라벨 라인 직후 값 라인 ≥2개)의 라벨 라인 인덱스. 헤더블록 경계."""
    def is_num(s):
        return _VALUE_LINE.match(s.strip()) is not None
    for i in range(len(lines) - 1):
        t = lines[i].strip()
        if not t or is_num(t):
            continue
        # i 직후의 (빈줄 스킵) 숫자 라인 개수
        cnt = 0
        j = i + 1
        while j < len(lines) and cnt < 2:
            s = lines[j].strip()
            if s == "":
                j += 1
                continue
            if is_num(s):
                cnt += 1
                j += 1
            else:
                break
        if cnt >= 2:
            return i
    return len(lines)


def _extract_headers(region: str) -> list[str]:
    """세그먼트 표 헤더에서 부문명 추출 — 구조적: 첫 데이터행 전 헤더블록의 짧은 비-noise 토큰.

    부문명=열헤더(뒤에 숫자 없음) vs 지표라벨=행(뒤에 숫자 run) 구조를 이용해 지표라벨 누출 차단.
    """
    unit_pos = region.find("(단위")
    scan = region[unit_pos:] if unit_pos >= 0 else region
    lines = scan.split("\n")
    stop_idx = _first_data_row_line(lines)
    head_lines = lines[:stop_idx]
    # 프로즈 divider도 추가 상한
    for di, line in enumerate(head_lines):
        if any(div in line for div in _HEADER_STOP):
            head_lines = head_lines[:di]
            break
    metric_tokens = set(REVENUE_LABELS) | set(PROFIT_LABELS) | _METRIC_ROW_NOISE
    names: list[str] = []
    for line in head_lines:
        t = line.strip()
        if not t or t in _HEADER_NOISE or t in metric_tokens:
            continue
        if _VALUE_LINE.match(t) or _YEAR_MARK.match(t) or t.endswith("%"):
            continue
        if _AGG_RE.search(t) or _NONSEG_RE.search(t) or _DESC_RE.search(t):   # 집계·컬럼머리·연도·회사명·설명문 배제('기타부문'은 통과)
            continue
        if len(t) > 22:   # 장문 서술·제품설명 제외
            continue
        if t.startswith("(단위") or t.startswith("(") or t in ("당기", "전기", "제품", "상품", "용역", "-"):
            continue
        if t not in names:
            names.append(t)
    # 잔여부문(기타/공통)은 관례상 표의 마지막 열 → 헤더 순서를 값 순서에 맞춰 뒤로 이동
    residual = [n for n in names if n in _RESIDUAL_SEG or (("기타" in n or "공통" in n) and len(n) <= 10)]
    main = [n for n in names if n not in residual]
    return main + residual


_RESIDUAL_SEG = {"기타", "기타부문", "공통부문", "공통", "기타 부문", "공통 및 기타", "공통부문 및 기타", "기타 및 조정"}


_PERIOD_MARK = re.compile(r"제\s*\d+\s*기|전\s*기\b")


def _scope_region(region: str) -> str:
    """metric 스캔 범위 축소 → 당기 블록만: (1) 2번째 '(단위' (2) 2번째 기간마커(제N기/전기)
    (3) '주요 고객에 대한 공시' 이후 절단(고객표 매출 오추출 방지). 가장 이른 절단점 채택."""
    scan = region
    cut = len(scan)
    # 2번째 '(단위'
    u1 = scan.find("(단위")
    if u1 >= 0:
        u2 = scan.find("(단위", u1 + 4)
        if u2 > 0:
            cut = min(cut, u2)
    # 2번째 기간마커(당기 다음 전기) — 단, 마커가 멀리 떨어진 '세로 기간블록'일 때만.
    # 전치표는 제56/55/54기가 인접한 '열 헤더'라 자르면 안 됨(gap<150).
    pms = list(_PERIOD_MARK.finditer(scan))
    if len(pms) >= 2 and pms[1].start() - pms[0].start() > 150:
        cut = min(cut, pms[1].start())
    # 주요 고객 공시
    for mk in ("주요 고객에 대한 공시", "주요고객에 대한 공시", "주요 고객에 관한", "주요고객"):
        p = scan.find(mk)
        if p >= 0:
            cut = min(cut, p)
            break
    return scan[:cut]


def _collect_metric_row(region: str, label: str) -> list[float]:
    """지표 라벨 라인 직후 연속 값행(숫자, %는 스킵)을 리스트로."""
    pat = re.compile(r"(?m)^\s*" + re.escape(label) + r"\s*$")
    m = pat.search(region)
    if not m:
        return []
    vals: list[float] = []
    for line in region[m.end():].split("\n")[1:]:
        s = line.strip()
        if s == "" or s.endswith("%"):   # 빈줄·비중% 스킵(금액/비중 교차표)
            continue
        v = parse_number(s)
        if v is None:
            break
        vals.append(v)
    return vals


_GEO_TOKENS = {"한국", "국내", "국외", "해외", "유럽", "미주", "미국", "북미", "중남미", "아시아", "중국",
               "일본", "유럽연합", "동남아", "중동", "아프리카", "대양주", "기타지역", "지역"}


def _seg_count_by_sum(vals: list[float], k_hint: int) -> tuple[int, bool]:
    """부문 개수 확정: sum(vals[:k]) ≈ 이후 값(부문합계/총계)인 k. k_hint 우선, ±2 탐색.
    반환 (k, validated). validated=False면 정렬 미검증."""
    if len(vals) < 2 or k_hint < 1:
        return k_hint, False

    def ok(k):
        if k < 1 or k >= len(vals):
            return False
        s = sum(vals[:k])
        return any(a != 0 and abs(s - a) <= max(abs(a), 1) * 0.02 for a in vals[k:k + 3])

    if ok(k_hint):
        return k_hint, True
    for k in range(max(1, k_hint - 2), min(len(vals), k_hint + 3)):
        if ok(k):
            return k, True
    return k_hint, False


def _is_transposed(region: str) -> bool:
    """전치표(부문=행, 지표 라벨이 부문마다 반복) 감지 — 같은 지표 라벨이 ≥2회 단독 등장."""
    for lb in REVENUE_LABELS + PROFIT_LABELS:
        if len(re.findall(r"(?m)^\s*" + re.escape(lb) + r"\s*$", region)) >= 2:
            return True
    return False


_TRANS_METRIC = set(REVENUE_LABELS) | set(PROFIT_LABELS) | _METRIC_ROW_NOISE | {
    "총자산", "자산", "부채", "구분", "구 분", "금 액", "비 중", "금액", "비중", "총부문수익"}


def _parse_transposed(region: str) -> list[dict]:
    """전치표: [부문명 → 매출액/영업이익 행] 블록 순회. 부문명 직후 첫 지표행 값(당기 금액)."""
    lines = [l.strip() for l in region.split("\n")]

    def first_val(i):
        for j in range(i + 1, min(i + 4, len(lines))):
            v = parse_number(lines[j])
            if v is not None:
                return v
        return None

    order, data, cur = [], {}, None
    for i, t in enumerate(lines):
        if not t:
            continue
        if t in REVENUE_LABELS:
            if cur:
                data.setdefault(cur, {}).setdefault("revenue", first_val(i))
            continue
        if t in PROFIT_LABELS:
            if cur:
                data.setdefault(cur, {}).setdefault("profit", first_val(i))
            continue
        # 부문명 후보
        if (not _VALUE_LINE.match(t) and not t.endswith("%") and t not in _TRANS_METRIC
                and not _AGG_RE.search(t) and not _NONSEG_RE.search(t) and not _DESC_RE.search(t)
                and len(t) <= 22 and t not in _HEADER_NOISE):
            cur = t
            if t not in order:
                order.append(t)
    return [{"name": n, "revenue": data[n].get("revenue"), "profit": data[n].get("profit")}
            for n in order if data.get(n)]


def _clean_segments(segments: list[dict]) -> tuple[list[dict], bool]:
    """이름 기반 누출 제거(설명문·집계·컬럼머리) + '압도적 값' 안전망. 반환 (segments, ok).

    값 기반 총계제거는 위험(주력부문이 소부문 합과 우연히 일치 — LG엔솔) → 이름으로만 제거하고,
    한 세그먼트 값이 나머지 '전부'의 합보다 크면(실부문 불가능) 총계누출로 보고 FAILED 강등.
    """
    segs = [dict(s) for s in segments
            if not _DESC_RE.search(s.get("name", ""))
            and not _AGG_RE.search(s.get("name", ""))
            and not _NONSEG_RE.search(s.get("name", ""))]

    # 부문합계 누출: '설명형 이름' + '값이 나머지 부문 합과 일치' 둘 다일 때만 제거.
    # (LG엔솔은 값이 합과 우연히 비슷해도 이름이 정식이라 안전, 고려아연은 이름은 서술형이나 값이 합 아님)
    def _desc_like(name):
        return bool(re.search(r"제조|판매|공사|및|등|[,，]", name))

    for key in ("revenue", "profit"):
        changed = True
        while changed and len(segs) >= 3:
            changed = False
            vals = [(i, s[key]) for i, s in enumerate(segs) if s.get(key) is not None]
            if len(vals) < 3:
                break
            for i, v in vals:
                others = sum(x for j, x in vals if j != i)
                if v != 0 and abs(v - others) <= max(abs(v), 1) * 0.02 and _desc_like(segs[i].get("name", "")):
                    del segs[i]
                    changed = True
                    break
    return segs, len(segs) >= 1


def parse_segment_table(anchor: str, region: str, note_source: str = "") -> SegmentProfit:
    """세그먼트 표 구간 → SegmentProfit. 전치/컬럼 자동감지 → 구조정렬 + 검증가드 + 총계누출 안전망."""
    sp = SegmentProfit(source="note", anchor=anchor, note_source=note_source)
    region = _scope_region(region)
    unit_name, _ = parse_unit(region)
    sp.unit = unit_name

    headers = _extract_headers(region)

    # 전치표 경로 — 컬럼 헤더가 2개 미만이고 지표라벨이 반복될 때만(부문=행 레이아웃)
    if len(headers) < 2 and _is_transposed(region):
        rev_label = next((lb for lb in REVENUE_LABELS if re.search(r"(?m)^\s*" + re.escape(lb) + r"\s*$", region)), "")
        prof_label = next((lb for lb in PROFIT_LABELS if re.search(r"(?m)^\s*" + re.escape(lb) + r"\s*$", region)), "")
        segs = _parse_transposed(region)
        segs, clean_ok = _clean_segments(segs)
        if clean_ok and len(segs) >= 1 and any(s.get("revenue") is not None or s.get("profit") is not None for s in segs):
            sp.revenue_metric, sp.profit_metric = rev_label, prof_label
            sp.segments = segs
            sp.raw_value_counts = {"transposed": len(segs)}
            sp.status = OK
            return sp
        sp.status = EXTRACTION_FAILED
        sp.na_reason = "transposed_parse_failed" if not clean_ok else "transposed_no_values"
        return sp
    rev_label = next((lb for lb in REVENUE_LABELS if re.search(r"(?m)^\s*" + re.escape(lb) + r"\s*$", region)), "")
    prof_label = next((lb for lb in PROFIT_LABELS if re.search(r"(?m)^\s*" + re.escape(lb) + r"\s*$", region)), "")
    sp.revenue_metric = rev_label
    sp.profit_metric = prof_label
    rev_vals = _collect_metric_row(region, rev_label) if rev_label else []
    prof_vals = _collect_metric_row(region, prof_label) if prof_label else []
    sp.raw_value_counts = {"headers": len(headers), "revenue": len(rev_vals), "profit": len(prof_vals)}

    if not headers or (not rev_vals and not prof_vals):
        sp.status = EXTRACTION_FAILED
        sp.na_reason = "no_headers_or_values"
        return sp

    # G2: 잔여부문(기타 등) 제외한 실부문이 전부 지역명이면 사업부문 아님 → NA(geographic_only)
    core_h = [h for h in headers if h not in _RESIDUAL_SEG]
    if core_h and all(h in _GEO_TOKENS or h.rstrip("지역") in _GEO_TOKENS for h in core_h):
        sp.status = NOT_APPLICABLE
        sp.na_reason = "geographic_only"
        sp.segments = []
        return sp

    k = len(headers)
    # 검증가드: 값이 헤더보다 많으면 sum≈부문합계로 k 확정. 매출 우선, 없으면 이익.
    guard_vals = rev_vals if len(rev_vals) > k else (prof_vals if len(prof_vals) > k else rev_vals or prof_vals)
    if len(guard_vals) > k:
        k2, validated = _seg_count_by_sum(guard_vals, k)
        if validated:
            k = k2
        else:
            # 정렬 미검증 + 초과값 존재 = 오정렬 위험 → 조용한 오답 대신 강등
            sp.status = EXTRACTION_FAILED
            sp.na_reason = "alignment_unverified"
            sp.segments = [{"name": h, "revenue": None, "profit": None} for h in headers]
            return sp

    headers = headers[:k]
    built = [{"name": name,
              "revenue": rev_vals[i] if i < len(rev_vals) else None,
              "profit": prof_vals[i] if i < len(prof_vals) else None}
             for i, name in enumerate(headers)]
    # 초과분(합계/조정) 분리 기록
    extra = {}
    if len(rev_vals) > k:
        extra["revenue_excess"] = rev_vals[k:]
    if len(prof_vals) > k:
        extra["profit_excess"] = prof_vals[k:]
    if extra:
        sp.adjustments = [extra]
    # 값기반 총계-누출 안전망 — 못 지운 총계가 남으면 강등(조용한 오답 금지)
    segs, clean_ok = _clean_segments(built)
    if not clean_ok or not segs:
        sp.status = EXTRACTION_FAILED
        sp.na_reason = "total_leak_unresolved"
        sp.segments = built
        return sp
    sp.segments = segs
    sp.status = OK
    return sp


def extract_segment_profit(biz_content_text: str, note_full_text: str, note_source: str = "") -> SegmentProfit:
    """3단 fallback: ①단일부문 선언 감지 ②note-title 재앵커 표 파싱 (본문 폴백은 P0-A에서).

    (현 단계: 주석 경로 우선 구현. 본문게시 폴백은 A필드 빌드 시 통합.)
    """
    # ① 본문게시 표 우선 (주석 2번째 콜 불필요). 다부문 표가 먼저 파싱돼야 자회사 단일선언 오탐 방지.
    b_anchor, b_region = find_body_segment_region(biz_content_text or "")
    if b_anchor and b_region:
        sp = parse_segment_table(b_anchor, b_region)
        sp.source = "body"
        if sp.status == OK and sp.segments:
            return sp
        if sp.status == NOT_APPLICABLE:   # geographic_only 등
            return sp
    # ② note-title 재앵커
    anchor, region = find_segment_note_region(note_full_text or "")
    if anchor and region:
        sp = parse_segment_table(anchor, region, note_source)
        if sp.status == OK and sp.segments:
            return sp
        if sp.status == NOT_APPLICABLE:
            return sp
    # ③ 표를 못 찾았을 때만 단일부문 '선언' 확인 → NA (다부문사는 위에서 이미 반환됨)
    if _SINGLE_DECL.search(note_full_text or "") or _SINGLE_DECL.search(biz_content_text or ""):
        return SegmentProfit(status=NOT_APPLICABLE, source="none", na_reason="single_segment")
    return SegmentProfit(status=EXTRACTION_FAILED, source="none", na_reason="no_segment_table_found")


# ── A필드 추출기 (본문/주석) ──
UNSUPPORTED_FORM = "UNSUPPORTED_FORM"


def _find_row_values(text: str, labels: list[str]) -> tuple[str, list[float]]:
    """라벨 화이트리스트 중 본문에 한 줄로 존재하는 첫 라벨의 직후 값행."""
    for lb in labels:
        if re.search(r"(?m)^\s*" + re.escape(lb) + r"\s*$", text):
            return lb, _collect_metric_row(text, lb)
    return "", []


def extract_rnd(biz_content_text: str) -> dict:
    """연구개발비용(총계·당기) + 매출대비 비율. 소절6 '[연구개발비용]' 표."""
    t = biz_content_text or ""
    lb, vals = _find_row_values(t, ["연구개발비용 총계", "연구개발비용계", "연구개발비용 계", "연구개발비 총계"])
    amount = vals[0] if vals else None
    # 비율: '연구개발비 / 매출액 비율' 직후 % 또는 숫자
    ratio = None
    rm = re.search(r"연구개발비\s*/?\s*매출액\s*비율", t)
    if rm:
        for line in t[rm.end():].split("\n")[1:8]:
            s = line.strip()
            m = re.match(r"\(?([\d,]+\.?\d*)\s*%?\)?$", s)
            if m:
                try:
                    ratio = float(m.group(1).replace(",", ""))
                except ValueError:
                    pass
                break
    if amount is None and ratio is None:
        return {"status": NOT_APPLICABLE, "na_reason": "no_rnd_table"}
    return {"status": OK, "amount": amount, "unit": parse_unit(t[:2000])[0] if amount else None,
            "ratio_to_sales_pct": ratio, "label": lb}


def extract_backlog(biz_content_text: str) -> dict:
    """수주잔고(있으면). 소절4 수주상황. 있음/해당없음 이분."""
    t = biz_content_text or ""
    if re.search(r"수주(에 의한 생산|생산에 의한).{0,20}(아니|없|해당\s*없)", t) or \
       re.search(r"수주\s*상황.{0,60}해당\s*(사항\s*)?없", t):
        return {"status": NOT_APPLICABLE, "na_reason": "not_order_based"}
    lb, vals = _find_row_values(t, ["수주잔고", "수주잔액", "수주총액"])
    if not vals:
        m = re.search(r"수주\s*(잔고|잔액|총액)", t)
        if not m:
            return {"status": NOT_APPLICABLE, "na_reason": "no_backlog"}
        return {"status": OK, "present": True, "values": None, "note": "수주 언급 존재(표 미파싱)"}
    return {"status": OK, "present": True, "label": lb, "values": vals[:6],
            "unit": parse_unit(t[:3000])[0]}


def extract_customer_concentration(note_full_text: str) -> dict:
    """10% 이상 외부고객(주요 고객에 대한 공시). 단일부문사의 매출분해 대체(F9)."""
    t = note_full_text or ""
    p = -1
    for mk in ("주요 고객에 대한 공시", "주요고객에 대한 공시", "매출액의 10% 이상", "10% 이상인 고객"):
        p = t.find(mk)
        if p >= 0:
            break
    if p < 0:
        return {"status": NOT_APPLICABLE, "na_reason": "no_customer_disclosure"}
    region = t[p:p + 2500]
    # 당기 블록만 — 2번째 '(단위'/기간마커에서 절단(주요고객 마커 컷은 쓰지 않음)
    u1 = region.find("(단위")
    if u1 >= 0:
        u2 = region.find("(단위", u1 + 4)
        if u2 > 0:
            region = region[:u2]
    pms = list(_PERIOD_MARK.finditer(region))
    if len(pms) >= 2:
        region = region[:pms[1].start()]
    names = re.findall(r"주요\s*고객사?\s*\(\s*([A-Z])\s*\)|(?:^|\s)고객\s*(\d+|[A-Z])(?:\s|$)", region)
    flat = [a or b for a, b in names if (a or b) not in ("합계", "고객")]
    # 고객 매출 값행 (라벨 직후 순서대로, 0 포함)
    _, vals = _find_row_values(region, ["주요 고객 매출액", "주요고객 매출액", "매출액", "수익(매출액)", "수익"])
    # 트레일링 합계 제거: 마지막 값 ≈ 앞 값들 합이면 총계
    if len(vals) >= 2 and vals[-1] != 0 and abs(sum(vals[:-1]) - vals[-1]) <= max(abs(vals[-1]), 1) * 0.02:
        vals = vals[:-1]
    if not flat and not vals:
        return {"status": NOT_APPLICABLE, "na_reason": "customer_marker_no_data"}
    # 이름 개수 기준 zip(이름 있으면) — 초과 값은 버림
    n = len(flat) if flat else min(len(vals), 6)
    customers = [{"customer": flat[i] if i < len(flat) else f"고객{i+1}",
                  "revenue": vals[i] if i < len(vals) else None} for i in range(n)]
    return {"status": OK, "customers": customers, "unit": parse_unit(region)[0]}


def _sp_to_dict(sp: "SegmentProfit") -> dict:
    return {"status": sp.status, "source": sp.source, "revenue_metric": sp.revenue_metric,
            "profit_metric": sp.profit_metric, "unit": sp.unit, "na_reason": sp.na_reason,
            "segments": sp.segments, "adjustments": sp.adjustments}


def build_details(biz_content_text: str, note_full_text: str, toc: list, note_source: str = "") -> dict:
    """최상위 오케스트레이션 — 폼 게이트 후 필드 추출. 스콥: 금융·REIT는 UNSUPPORTED_FORM."""
    form = detect_form(toc or [])
    out = {"form_type": form}
    if form in (FORM_FINANCIAL, FORM_REIT):
        for f in ("segment_profit", "rnd", "backlog", "customer_concentration"):
            out[f] = {"status": UNSUPPORTED_FORM, "na_reason": f"form_{form}_not_supported_v1"}
        return out
    out["segment_profit"] = _sp_to_dict(extract_segment_profit(biz_content_text, note_full_text, note_source))
    out["rnd"] = extract_rnd(biz_content_text)
    out["backlog"] = extract_backlog(biz_content_text)
    out["customer_concentration"] = extract_customer_concentration(note_full_text)
    return out


# ══════════════════════════════════════════════════════════════════
# Live 오케스트레이션 (build_business_details_payload) + 단계별 타이머
# 설계: 정형 primary → 저신뢰시 후보표 raw 반환(호출측 LLM 추출) → N/A. 내부 LLM/pandas 없음.
# ══════════════════════════════════════════════════════════════════
import time as _time

_NODE_FIELDS = ("text", "rcpNo", "dcmNo", "eleId", "offset", "length", "dtd", "tocNo")


def _extract_node_tree(main_html: str) -> list[dict]:
    """viewer main.do treeData의 모든 node(node1/2/3…) 계층+부모+좌표 추출 (census 검증 로직)."""
    parts = re.split(r"var\s+(node\d+)\s*=\s*\{\};", main_html)
    nodes, last_at = [], {}
    for k in range(1, len(parts), 2):
        lvl = int(re.match(r"node(\d+)", parts[k]).group(1))
        body = parts[k + 1]
        vals = {"lvl": lvl}
        for f in _NODE_FIELDS:
            m = re.search(rf"\['{f}'\]\s*=\s*\"([^\"]*)\"", body)
            if m:
                vals[f] = m.group(1)
        parent = last_at.get(lvl - 1)
        vals["parent_text"] = parent.get("text") if parent else None
        last_at[lvl] = vals
        for dl in [d for d in last_at if d > lvl]:
            del last_at[dl]
        nodes.append(vals)
    return nodes


def _node_fetchable(n: dict) -> bool:
    return {"rcpNo", "dcmNo", "eleId", "offset", "length", "dtd"} <= set(n)


async def _find_latest_report(client, corp_code: str, period: str):
    """사업의 내용 있는 최신 정기보고서 rcept (정정 목차게이트는 fetch 단계에서). period=annual/quarterly."""
    detail = ["A001"] if period == "annual" else ["A002", "A003"]
    toks = ["사업보고서"] if period == "annual" else ["분기보고서", "반기보고서"]
    from datetime import date
    end = date.today().strftime("%Y%m%d")
    best = None
    for dty in detail:
        res = await client.search_filings(bgn_de="20240101", end_de=end, corp_code=corp_code,
                                          pblntf_ty="A", pblntf_detail_ty=dty, page_count=30)
        for r in res.get("list", []):
            if any(t in r.get("report_nm", "") for t in toks):
                if best is None or r.get("rcept_dt", "") > best.get("rcept_dt", ""):
                    best = r
    return best


async def _fetch_biz(client, rcept_no: str) -> dict:
    """main.do(목차) + II.사업의 내용 chapter만 fetch [2콜]. 주석 노드는 좌표만 반환(lazy)."""
    main_html = await client._fetch_viewer_main_html(rcept_no)
    nodes = _extract_node_tree(main_html)
    out = {"toc": [{"lvl": n["lvl"], "text": n.get("text", "")} for n in nodes]}
    biz = [n for n in nodes if n["lvl"] == 1 and "사업의 내용" in n.get("text", "") and _node_fetchable(n)]
    if biz:
        h = await client._fetch_viewer_section_html(biz[0])
        out["biz_html"], out["biz_text"] = h, client._html_to_text(h)
    # 주석 노드 좌표만 보관(연결 우선, 없으면 별도) — fetch는 필요 시에만
    notes = [n for n in nodes if n.get("parent_text") and "재무에 관한" in n["parent_text"]
             and "연결재무제표 주석" in n.get("text", "") and _node_fetchable(n)]
    if not notes:
        notes = [n for n in nodes if n.get("parent_text") and "재무에 관한" in n["parent_text"]
                 and "재무제표 주석" in n.get("text", "") and _node_fetchable(n)]
    out["_note_node"] = notes[0] if notes else None
    return out


async def _fetch_note(client, note_node: dict) -> dict:
    """주석 노드 HTML+text fetch [1콜, 대용량]. 필요 시에만(lazy)."""
    if not note_node:
        return {}
    h = await client._fetch_viewer_section_html(note_node)
    return {"note_html": h, "note_text": client._html_to_text(h),
            "note_source": note_node.get("text", "")}


def _segment_confident(sp: "SegmentProfit") -> bool:
    """정형 신뢰게이트: ①부문명 clean(junk 없음) ②sum(부문 매출)≈총계. 하나라도 아니면 False→후보반환.

    junk(설명문·집계·컬럼머리·지표라벨)가 하나라도 섞이면 confident 아님 → 조용한 오답 대신 호출측에 후보 넘김.
    """
    names = [s.get("name", "") for s in sp.segments]
    for nm in names:
        if _DESC_RE.search(nm) or _AGG_RE.search(nm) or _NONSEG_RE.search(nm) or nm in _METRIC_ROW_NOISE:
            return False
    # 이름 형태 불일치: 짧은 clean명(<7) + 긴 설명명(≥14) 혼재 = 설명이 junk → 후보반환
    lens = [len(n) for n in names]
    if len(lens) >= 2 and max(lens) >= 14 and min(lens) < 7:
        return False
    revs = [s["revenue"] for s in sp.segments if s.get("revenue") is not None]
    if len(revs) < 2:
        return len(revs) == 1
    ex = (sp.adjustments[0].get("revenue_excess") if sp.adjustments else None) or []
    s = sum(revs)
    return any(a != 0 and abs(s - a) <= max(abs(a), 1) * 0.03 for a in ex) if ex else True


async def build_business_details_payload(company_query: str, period: str = "annual",
                                         fields: list[str] | None = None) -> dict:
    """II.사업의 내용 구조화 추출 tool 진입점. 단계별 타이머(data.timings_ms)로 병목 실측."""
    from open_proxy_mcp.services.company import resolve_company_query
    from open_proxy_mcp.services.contracts import ToolEnvelope, AnalysisStatus
    from open_proxy_mcp.dart.client import get_dart_client
    from open_proxy_mcp.services.segment_candidates import find_segment_candidates

    T, t0 = {}, _time.perf_counter()

    def _lap(name):
        nonlocal t0
        T[name] = round((_time.perf_counter() - t0) * 1000)
        t0 = _time.perf_counter()

    resolution = await resolve_company_query(company_query)
    _lap("resolve")
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(tool="business_details", status=resolution.status,
                            subject=company_query, data={"timings_ms": T},
                            warnings=["회사 식별 실패"]).to_dict()
    corp = resolution.selected
    client = get_dart_client()

    rept = await _find_latest_report(client, corp["corp_code"], period)
    _lap("search")
    if not rept:
        return ToolEnvelope(tool="business_details", status=AnalysisStatus.NO_FILING,
                            subject=corp.get("corp_name", ""), data={"timings_ms": T},
                            warnings=[f"{period} 정기보고서 없음"]).to_dict()

    sec = await _fetch_biz(client, rept["rcept_no"])   # main.do + 사업내용 [2콜, 주석 제외]
    _lap("fetch_biz")

    form = detect_form(sec.get("toc", []))
    warnings: list[str] = []
    want = set(fields or ["segments", "rnd", "backlog", "customers"])
    note_fetched = False

    async def _ensure_note():
        """주석을 아직 안 받았으면 지금 fetch(lazy). 대용량이라 필요할 때만."""
        nonlocal note_fetched
        if not note_fetched and sec.get("_note_node"):
            nd = await _fetch_note(client, sec["_note_node"])
            sec.update(nd)
            note_fetched = True

    # segment_profit: 본문 우선(주석 없이) → 저신뢰 시 주석 lazy fetch → 후보 raw → N/A
    if form in (FORM_FINANCIAL, FORM_REIT):
        segment = {"status": UNSUPPORTED_FORM, "source": "none",
                   "na_reason": f"form_{form}_not_supported_v1 (금융·REIT는 D-트랙)"}
    elif "segments" not in want:
        segment = None
    else:
        sp = extract_segment_profit(sec.get("biz_text", ""), "", "")  # 본문만 시도
        if not (sp.status == OK and sp.source == "body" and sp.segments and _segment_confident(sp)):
            await _ensure_note()  # 본문 불충분 → 주석 받아 재시도
            sp = extract_segment_profit(sec.get("biz_text", ""), sec.get("note_text", ""), sec.get("note_source", ""))
        _lap("segment_fetch+parse")
        if sp.status == OK and sp.segments and _segment_confident(sp):
            segment = {"status": OK, "source": "deterministic", "revenue_metric": sp.revenue_metric,
                       "profit_metric": sp.profit_metric, "unit": sp.unit, "items": sp.segments,
                       "reconciliation": "부문합≈총계 검산 통과"}
        elif sp.status == NOT_APPLICABLE:
            segment = {"status": NOT_APPLICABLE, "source": "none", "na_reason": sp.na_reason}
        else:
            cands = find_segment_candidates(sec.get("note_html", "")) + find_segment_candidates(sec.get("biz_html", ""))
            cands.sort(key=lambda x: x["score"], reverse=True)
            segment = {"status": "NEEDS_REVIEW", "source": "raw_candidates",
                       "note": "정형 추출 저신뢰 — 아래 부문표 후보(수백 표 중 상위)를 읽어 사업부문별 매출·영업이익을 추출하세요. 부문합계/조정/총계 열은 제외.",
                       "candidates": cands[:5]}
            warnings.append("segment_profit: 정형 저신뢰 → 후보표 raw 반환(호출측 추출)")

    data = {
        "corp": {"name": corp.get("corp_name"), "corp_code": corp.get("corp_code"), "stock_code": corp.get("stock_code")},
        "report": {"rcept_no": rept["rcept_no"], "report_nm": rept.get("report_nm"), "rcept_dt": rept.get("rcept_dt")},
        "form_type": form,
        "segments": segment if "segments" in want else None,
    }
    if "rnd" in want:
        data["rnd"] = extract_rnd(sec.get("biz_text", ""))
    if "backlog" in want:
        data["backlog"] = extract_backlog(sec.get("biz_text", ""))
    if "customers" in want:
        await _ensure_note()   # 고객집중은 주석 필요 → 아직 없으면 지금 fetch
        data["customers"] = extract_customer_concentration(sec.get("note_text", ""))
    _lap("Afields")
    data["note_fetched"] = note_fetched   # 주석 lazy fetch 여부(투명)

    T["total"] = sum(v for k, v in T.items())
    data["timings_ms"] = T
    return ToolEnvelope(tool="business_details", status=AnalysisStatus.EXACT,
                        subject=corp.get("corp_name", ""), data={k: v for k, v in data.items() if v is not None},
                        warnings=warnings).to_dict()
