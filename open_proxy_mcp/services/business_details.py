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

from open_proxy_mcp.dart.client import html_to_text

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
    # has_mfg = 제조·제품 소절 존재(표준폼 확정 신호). full biz 텍스트를 넘겨도 이 구절은
    # 프로즈에 거의 안 나오는 '소절 제목' 고유 문구라 신뢰 가능. → REIT/금융 오탐을 veto.
    has_mfg = ("원재료 및 생산설비" in joined) or ("주요 제품 및 서비스" in joined)
    # 이중템플릿: 소절 '사업의 내용'류가 (제조서비스업)+(금융업) 두 벌
    if joined.count("영업의 현황") >= 2 or (("(금융업)" in joined) and ("(제조" in joined or "(서비스" in joined)):
        return FORM_DUAL
    if not has_mfg:
        # REIT: 투자부동산/임차인/부동산투자회사 (제조·제품 소절이 없을 때만)
        if any(k in joined for k in ("부동산투자회사", "투자부동산 내역", "임차인", "자산개요")):
            return FORM_REIT
        # 금융폼: 재무건전성/영업의 현황/영업설비 (원재료·생산 없음)
        fin_marks = sum(1 for k in ("재무건전성", "영업의 현황", "영업설비") if k in joined)
        if fin_marks >= 1:
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

# 세그먼트 노트 제목 앵커: 'N. 부문별 정보/영업부문(별) 정보/영업부문 (연결)'
# (?:영업)? — SK이노베이션류 '36. 영업부문별 정보 (연결)'·'39. 영업부문 정보 (연결)' 서식.
# 260723 census(캐시 정기보고서 46건): 변형 추가로 +6 획득 / 0 상실, 회계정책 소절(2.15류)은 계속 배제.
_NOTE_ANCHOR = re.compile(
    r"(?m)^\s*(\d{1,2})\.\s*((?:영업)?부문별?\s*정보|영업부문(?:에\s*대한\s*정보)?|부문별\s*보고)\s*(\(연결\)|\(별도\))?\s*$"
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
    cross_conflict: bool = False   # 본문표 vs 주석표 부문명 불일치(지주사류) → 신뢰불가, 후보반환


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


_DASH_CELL = re.compile(r"^[-－–—]$")   # 단독 dash 셀(값 없음 자리표시자). 260723 캐시 49건 실측은 ASCII '-'만.


def _collect_metric_row(region: str, label: str, dash_zero: bool = False) -> list[float]:
    """지표 라벨 라인 직후 연속 값행(숫자, %는 스킵)을 리스트로.

    dash_zero=True(세그먼트 표 경로 전용): 행 중간 단독 '-'를 0.0으로 유지해 열 정렬 보존 —
    dash에서 끊으면 한화 FY2024류(조선업 열 '-')가 9부문 중 앞 3부문만 값이 잡히는 부분추출이 됨.
    단, 실숫자가 하나도 없으면(전부 dash 행) 빈 리스트 — 전부-0 유령행 방지.
    ⚠ 금액/비중 교차표(스트림에 % 셀 존재)에서는 dash_zero를 자동 비활성(260723 리뷰) —
    비중 열의 dash는 % 마커가 없어 금액 0.0으로 오인되고, 열이 밀린 오답이 검산 인증까지
    받는 채널이 재현됨. 텍스트만으로 dash의 소속 열을 판별할 수 없으므로 종전 break로
    보수 후퇴(부분수집 → 검산 불가 → 게이트가 후보 강등).
    _find_row_values(rnd·backlog·customers)는 기본 False 유지 — vals[0]이 금액이라 dash→0.0이면
    '값 없음(None)'이 '0원'으로 둔갑한다."""
    pat = re.compile(r"(?m)^\s*" + re.escape(label) + r"\s*$")
    m = pat.search(region)
    if not m:
        return []
    lines = region[m.end():].split("\n")[1:]
    if dash_zero:
        # 사전 스캔(메인 루프의 종료 조건 미러): 값 스트림 안에 % 셀이 하나라도 있으면 교차표 → 비활성
        for line in lines:
            t = line.strip()
            if t == "":
                continue
            if t.endswith("%"):
                dash_zero = False
                break
            if parse_number(t) is None and not _DASH_CELL.match(t):
                break  # 스트림 종료 — 이 지점까지 % 없음
    vals: list[float] = []
    has_real = False
    for line in lines:
        s = line.strip()
        if s == "" or s.endswith("%"):   # 빈줄·비중% 스킵(금액/비중 교차표)
            continue
        v = parse_number(s)
        if v is None:
            if dash_zero and _DASH_CELL.match(s):
                vals.append(0.0)
                continue
            break
        has_real = True
        vals.append(v)
    if dash_zero and not has_real:
        return []
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
    rev_vals = _collect_metric_row(region, rev_label, dash_zero=True) if rev_label else []
    prof_vals = _collect_metric_row(region, prof_label, dash_zero=True) if prof_label else []
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
    # 초과분(합계/조정) 분리 기록 — 기준은 부문명 개수(len(headers)): 검증 k가 부문명보다 크면
    # (한화 FY2024: 부문 9 + 연결조정 열 → k=10) 그 사이 조정열 값을 버리지 않고 excess에 보존해야
    # _segment_confident의 '부문합+조정 ≈ 총계' 검산이 성립한다.
    n_seg = len(headers)
    extra = {}
    if len(rev_vals) > n_seg:
        extra["revenue_excess"] = rev_vals[n_seg:]
    if len(prof_vals) > n_seg:
        extra["profit_excess"] = prof_vals[n_seg:]
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
    # ① 본문게시 표 + ② 주석 표를 둘 다 파싱해 교차검증(get_document는 1콜에 둘 다 있어 추가비용 0).
    b_anchor, b_region = find_body_segment_region(biz_content_text or "")
    spb = parse_segment_table(b_anchor, b_region) if (b_anchor and b_region) else None
    if spb:
        spb.source = "body"
    n_anchor, n_region = find_segment_note_region(note_full_text or "")
    spn = parse_segment_table(n_anchor, n_region, note_source) if (n_anchor and n_region) else None
    if spn:
        spn.source = "note"
    b_ok = bool(spb and spb.status == OK and spb.segments)
    n_ok = bool(spn and spn.status == OK and spn.segments)

    # 둘 다 부문표를 냈으면 부문명 대조 — 불일치(지주사가 본문에 자회사표를 실은 케이스)면 cross_conflict.
    if b_ok and n_ok:
        if _seg_names_agree(spb, spn):
            return spb                              # 동의 → 본문(값 동일, 선점)
        spn.cross_conflict = True                   # 불일치 → K-IFRS 1108 권위 주석 채택 + 충돌표시
        return spn                                  # (오케스트레이터가 충돌 시 후보반환으로 강등)
    if b_ok:
        return spb
    if n_ok:
        return spn
    # geographic_only 등 NA 선언(본문 우선)
    if spb and spb.status == NOT_APPLICABLE:
        return spb
    if spn and spn.status == NOT_APPLICABLE:
        return spn
    # ③ 표를 못 찾았을 때만 단일부문 '선언' 확인 → NA (다부문사는 위에서 이미 반환됨)
    if _SINGLE_DECL.search(note_full_text or "") or _SINGLE_DECL.search(biz_content_text or ""):
        return SegmentProfit(status=NOT_APPLICABLE, source="none", na_reason="single_segment")
    return SegmentProfit(status=EXTRACTION_FAILED, source="none", na_reason="no_segment_table_found")


def _norm_seg_name(nm: str) -> str:
    """부문명 정규화(대조용): 공백·부문/사업 접미사·괄호주석·대소문자 제거."""
    nm = re.sub(r"[\(（*].*", "", nm)                 # 괄호주석·별표 이후 제거
    nm = nm.replace(" ", "").replace("부문", "").replace("사업", "").replace("사업부", "")
    return nm.strip().lower()


def _seg_names_agree(a: "SegmentProfit", b: "SegmentProfit", thr: float = 0.6) -> bool:
    """두 부문 리스트의 부문명 집합이 thr 이상 겹치면 동의로 본다."""
    na = {_norm_seg_name(s.get("name", "")) for s in a.segments if s.get("name")}
    nb = {_norm_seg_name(s.get("name", "")) for s in b.segments if s.get("name")}
    na.discard(""); nb.discard("")
    if not na or not nb:
        return False
    return len(na & nb) >= min(len(na), len(nb)) * thr


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


async def _find_report_candidates(client, corp_code: str, period: str) -> list[dict]:
    """정기보고서 후보를 rcept_dt 내림차순으로. [0]=최신, 이후=정정폴백용(동일기수).
    최신이 첨부/기재정정이면 document.xml이 부재(014)일 수 있어 — 같은 기수 원본으로 폴백하려고
    전 후보를 반환한다(KB금융·삼성화재: 최신 정정 014 → 하루 전 원본은 get_document 정상).
    period="latest"(기본)=사업·반기·분기 통틀어 rcept_dt 최신(분기보고서가 연간보다 신선). II.사업의
    내용은 분기/반기도 완전구조라 동일 필드 파싱 — [[사업의내용_ksic별양식]]."""
    if period == "annual":
        detail, toks = ["A001"], ["사업보고서"]
    elif period == "quarterly":
        detail, toks = ["A002", "A003"], ["분기보고서", "반기보고서"]
    else:  # "latest"(기본) — 정기보고서 3종 중 가장 최근 제출분
        detail, toks = ["A001", "A002", "A003"], ["사업보고서", "분기보고서", "반기보고서"]
    from datetime import date
    end = date.today().strftime("%Y%m%d")
    out: list[dict] = []
    for dty in detail:
        res = await client.search_filings(bgn_de="20240101", end_de=end, corp_code=corp_code,
                                          pblntf_ty="A", pblntf_detail_ty=dty, page_count=30)
        for r in res.get("list", []):
            if any(t in r.get("report_nm", "") for t in toks):
                out.append(r)
    out.sort(key=lambda r: r.get("rcept_dt", ""), reverse=True)
    return out


def _report_period_tag(r: dict) -> str | None:
    """report_nm의 기수 라벨 '(2025.12)' → '2025.12'. 정정폴백을 같은 기수로 제한(작년데이터 금지)."""
    m = re.search(r"\((\d{4})[.\s]*(\d{1,2})\)", r.get("report_nm", "") or "")
    return f"{m.group(1)}.{int(m.group(2))}" if m else None


_REPRT_CODE_INFO = {
    "11011": ("A001", ["사업보고서"]),   # 사업(연간)
    "11012": ("A002", ["반기보고서"]),   # 반기
    "11013": ("A003", ["분기보고서"]),   # 1분기
    "11014": ("A003", ["분기보고서"]),   # 3분기
}


async def _find_report_for_bsns_year(client, corp_code: str, bsns_year: str, reprt_code: str) -> list[dict]:
    """특정 사업연도·보고서유형(DART 표준 reprt_code: 11011=사업/11012=반기/11013=1분기/11014=3분기)의
    정기보고서를 report_nm 기수라벨 '(YYYY.MM)'로 정밀 매칭 — 시계열(추이) 조회용, period(latest 스냅샷)와
    별개 경로. 결산월을 몰라도(3월결산 등) 안전하도록 절대월을 가정하지 않고, 분기보고서(11013/11014)가
    연내 2회 등장하면 tag 월의 상대순서(빠른 쪽=1분기/늦은 쪽=3분기)로만 구분한다."""
    info = _REPRT_CODE_INFO.get(reprt_code)
    if info is None or not bsns_year.strip().isdigit():
        return []
    detail_ty, toks = info
    year = int(bsns_year)
    from datetime import date
    end = min(f"{year + 1}0630", date.today().strftime("%Y%m%d"))
    res = await client.search_filings(bgn_de=f"{year}0101", end_de=end, corp_code=corp_code,
                                       pblntf_ty="A", pblntf_detail_ty=detail_ty, page_count=100)
    cands = [r for r in res.get("list", [])
             if any(t in r.get("report_nm", "") for t in toks)
             and (_report_period_tag(r) or "").startswith(f"{year}.")]
    if reprt_code in ("11013", "11014") and len({_report_period_tag(r) for r in cands}) > 1:
        def _tag_month(r):
            tag = _report_period_tag(r)
            return int(tag.split(".")[1]) if tag else 0
        months = sorted({_tag_month(r) for r in cands})
        want = months[0] if reprt_code == "11013" else months[-1]
        cands = [r for r in cands if _tag_month(r) == want]
    cands.sort(key=lambda r: r.get("rcept_dt", ""), reverse=True)
    return cands


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


# ── get_document 기반 fetch (1 API콜, viewer 3웹콜보다 ~3x 빠름) + 섹셔닝 ──
# DART 표준 섹션 코드 (<TITLE AASSOCNOTE="...">) — 구간 경계용 구조 앵커.
_AASSOC_BIZ = "D-0-2-0-0"        # II. 사업의 내용
_AASSOC_FIN = "D-0-3-0-0"        # III. 재무에 관한 사항
_AASSOC_NOTE_CONN = "D-0-3-3-0"  # 연결재무제표 주석
_AASSOC_NOTE_SEP = "D-0-3-5-0"   # 재무제표 주석(별도)
_AASSOC_TITLE_RE = re.compile(r'<TITLE\b[^>]*\bAASSOCNOTE="(D-0-[0-9-]+)"', re.IGNORECASE)


def _aassoc_positions(html: str) -> dict[str, list[int]]:
    """html(DART XML)에서 D-계열 AASSOCNOTE 앵커 코드 → 출현 위치 목록."""
    pos: dict[str, list[int]] = {}
    for m in _AASSOC_TITLE_RE.finditer(html):
        pos.setdefault(m.group(1), []).append(m.start())
    return pos


def _slice_by_aassoc(html: str, images: list | None = None) -> tuple[str, str, str]:
    """AASSOCNOTE 코드 앵커로 html을 구간 슬라이스 → text화한 (biz, note, src).

    - biz  = [D-0-2-0-0, D-0-3-0-0)  — 사이의 L-계열 소절 앵커는 경계로 쓰지 않음
    - note = [연결 D-0-3-3-0, 다음 D-앵커) · 연결 없으면 [별도 D-0-3-5-0, 다음 D-앵커)
    방어: 앵커가 중복 출현하면 그 섹션은 코드 경로 포기("" 반환) → 호출측이 섹션별로
    텍스트 폴백. text화는 get_document text와 동일 함수(html_to_text)라 내용 일치.
    """
    pos = _aassoc_positions(html)
    all_starts = sorted(p for ps in pos.values() for p in ps)

    def _unique(code: str) -> int | None:
        ps = pos.get(code, [])
        return ps[0] if len(ps) == 1 else None

    def _next_anchor_after(start: int) -> int | None:
        nxt = [p for p in all_starts if p > start]
        return nxt[0] if nxt else None

    biz = ""
    b0, b1 = _unique(_AASSOC_BIZ), _unique(_AASSOC_FIN)
    if b0 is not None and b1 is not None and b0 < b1:
        biz = html_to_text(html[b0:b1], images=images)

    note, src = "", ""
    for code, label in ((_AASSOC_NOTE_CONN, "연결재무제표 주석"),
                        (_AASSOC_NOTE_SEP, "재무제표 주석")):
        n0 = _unique(code)
        if n0 is None:
            continue
        n1 = _next_anchor_after(n0)
        if n1 is None:
            continue        # 끝 경계 불명 → 텍스트 폴백에 맡김 (60KB cap 로직 보유)
        note, src = html_to_text(html[n0:n1], images=images), label
        break
    return biz, note, src


def _slice_getdoc_sections(text: str, html: str = "",
                           images: list | None = None) -> tuple[str, str, str]:
    """get_document 전체 → (biz=II.사업의내용, note=연결재무제표주석 격리, note_source).

    1차: 코드 앵커(html). 2차: 섹션별 텍스트 폴백(코드 앵커 없는 구형 문서·비표준 서식).

    핵심: 연결+별도 주석이 다 들어있어 '30.부문별보고'가 중복 → 연결 주석만 격리해야 파서 정확.
    연결 주석 끝 = 별도 'N. 재무제표'(주석 아닌 것) heading(텍스트 폴백) / 다음 D-앵커(코드).
    """
    biz, note, src = _slice_by_aassoc(html, images=images) if html else ("", "", "")
    if not biz:
        # biz: II.사업의 내용 → III.재무에 관한. 목차(TOC)에도 같은 제목이 있어 첫 매치를 잡으면
        # 대형사(SK하이닉스·한화솔루션 등 48/155)에서 목차 stub(수백B)만 떠내진다 → II→III 구간 중
        # '가장 긴 span'을 실제 body로 택한다(목차 span은 짧고 본문 span은 김).
        i2s = [m.start() for m in re.finditer(r"(?m)^\s*II\.\s*사업의\s*내용", text)]
        i3s = [m.start() for m in re.finditer(r"(?m)^\s*III\.\s*재무에\s*관한", text)]
        for a in i2s:
            nb = [b for b in i3s if b > a]
            if nb and (nb[0] - a) > len(biz):
                biz = text[a:nb[0]]
    if not note:
        nconn = re.search(r"(?m)^\s*\d*\.?\s*연결재무제표\s*주석", text)
        if nconn:
            after = text[nconn.end():]
            sep = re.search(r"(?m)^\s*\d+\.\s*재무제표\s*$", after)   # 별도 재무제표 시작
            note = text[nconn.start(): nconn.end() + sep.start()] if sep else text[nconn.start():nconn.start() + 60000]
            src = "연결재무제표 주석"
        else:   # 연결 없으면(별도만) 재무제표 주석
            nsep = re.search(r"(?m)^\s*\d*\.?\s*재무제표\s*주석", text)
            if nsep:
                note = text[nsep.start():nsep.start() + 60000]
                src = "재무제표 주석"
    return biz, note, src


def _biz_html_region(html: str) -> str:
    """html에서 II.사업의내용 구간만(III.재무 앞까지) 슬라이스 — 목차(TOC) stub 회피 max-span.
    D-트랙 시그니처 폴백(_find_by_signature)이 III.재무 주석의 회계표(공정가치 서열체계·
    투자부동산 장부금액)를 REIT 투자부동산으로 오발하던 것 차단(NH올원·이리츠코크렙). 경계 못
    찾으면 원본 반환(graceful — 최소 기존 동작 유지)."""
    if not html:
        return html
    # 실제 장 제목(<TITLE>)을 먼저 고른다. 회사개요의 "II. 사업의 내용을 참조" 문구에서
    # 다음 III 제목까지가 2KB를 넘는 문서는 길이 휴리스틱만으로 참조문을 본문으로 오인한다.
    title_i2 = [
        match.start()
        for match in re.finditer(
            r"<TITLE\b[^>]*>\s*II\s*\.\s*사업의?\s*내용", html, re.IGNORECASE
        )
    ]
    title_i3 = [
        match.start()
        for match in re.finditer(
            r"<TITLE\b[^>]*>\s*III\s*\.\s*재무에?\s*관한", html, re.IGNORECASE
        )
    ]
    for a in title_i2:
        nb = [b for b in title_i3 if b > a]
        if nb and (nb[0] - a) > 2000:
            return html[a:nb[0]]
    i2 = [m.start() for m in re.finditer(r"II\s*\.\s*사업의?\s*내용", html)]
    i3 = [m.start() for m in re.finditer(r"III\s*\.\s*재무에?\s*관한", html)]
    # 본문 II는 목차·재무부록보다 먼저 온다 → 앞에서부터 첫 '실질' 구간(목차 stub 수십자 skip).
    # max-span은 금물: 한화생명류는 말미에 종속사 사업보고서가 embedded돼 그 II→III span이
    # 본문보다 커 오선택(→본문 DP Real Estate·종속REIT 서술 통째 누락)한다.
    for a in i2:
        nb = [b for b in i3 if b > a]
        if nb and (nb[0] - a) > 2000:
            return html[a:nb[0]]
    return html


async def _fetch_getdoc(client, rcept_no: str) -> dict:
    """get_document 1 API콜로 전체 보고서 → biz/note 슬라이스 + html(후보용). viewer보다 ~3x 빠름."""
    doc = await client.get_document_cached(rcept_no)
    text = doc.get("text", "") if isinstance(doc, dict) else ""
    html = doc.get("html", "") if isinstance(doc, dict) else ""
    images = doc.get("images") if isinstance(doc, dict) else None
    biz, note, src = _slice_getdoc_sections(text, html=html, images=images)
    # 후보표용 html은 full 그대로 넘김 — find_segment_candidates가 정규식으로 <table>만 뽑아
    # 프리필터하므로 22MB도 <250ms. (구간 슬라이스는 TOC 오매칭으로 부문표 누락시켜 폐기)
    # detect_form은 has_mfg(제조·제품 소절)가 REIT/금융을 veto하므로 full biz 텍스트로 충분.
    # (유통사가 리츠 자회사 보유→프로즈의 '부동산투자회사'가 REIT 오탐하던 것 방지)
    return {"biz_text": biz, "note_text": note, "note_source": src, "full_text": text,
            "note_html": html, "biz_html": _biz_html_region(html), "toc": [{"lvl": 1, "text": biz}]}


async def _fetch_viewer_sec(client, rcept_no: str) -> dict:
    """get_document 실패(014 등) 폴백 — viewer 웹fetch로 biz+note 받아 _fetch_getdoc 호환 sec 구성.
    KB금융·삼성화재류(사업보고서 document.xml 부재). 웹콜이라 느리지만 극소수 firm."""
    b = await _fetch_biz(client, rcept_no)                      # {toc, biz_html, biz_text, _note_node}
    note = await _fetch_note(client, b.get("_note_node")) if b.get("_note_node") else {}
    biz_html = b.get("biz_html", "") or ""
    note_html = note.get("note_html", "") or ""
    biz_text = b.get("biz_text", "") or ""
    note_text = note.get("note_text", "") or ""
    # note_html에 biz_html+note_html 결합 — 필드는 biz구간, 부문주석은 note구간에서 렌더됨.
    # biz_html은 viewer가 이미 II 챕터만 fetch한 것이라 그대로 D-트랙 필드 전용으로 넘김.
    return {"biz_text": biz_text, "note_text": note_text, "note_source": note.get("note_source", ""),
            "full_text": biz_text + "\n" + note_text,
            "note_html": biz_html + "\n" + note_html, "biz_html": biz_html, "toc": b.get("toc", [])}


# 지역별/지역정보 표를 부문표로 오인 방지(HL만도 한국/중국/미국·이오테크닉스 '본사 소재지 국가'/외국).
# 부문명을 정규화(부문/사업 접미사 제거)해 대조하므로 '국내부문'→'국내', '해외부문'→'해외'도 걸림.
_GEO_NAMES = {"한국", "국내", "해외", "국외", "외국", "중국", "미국", "일본", "유럽", "유렵", "인도",
              "아시아", "북미", "남미", "미주", "동남아", "베트남", "인도네시아", "유럽연합", "중동",
              "아프리카", "대양주", "북아메리카", "남아메리카", "기타지역", "본사", "지역", "폴란드",
              "헝가리", "대만", "태국", "멕시코", "브라질", "러시아", "본사소재지국가", "소재지국가"}
# 재무라인·표제목·집계열이 부문명으로 새는 것(유한양행 '3)비유동자산'·대한전선 '보고부문의 수익 및 성과'·
# 두산 '연결 후 금액'·SK케미칼 '감가상각비(주2)'). substring 매칭이라 괄호주석 붙어도 걸림.
_JUNK_NAME_RE = re.compile(r"^\s*\d+\s*[).]|유동자산|자산총계|부채총계|자본총계|연결회사|연결실체|"
                           r"^합\s*계|총자산|총부채|연결\s*후|소재지|보고부문|주요\s*재무|"
                           r"수익\s*및\s*성과|감가상각|상각비|재무지표|비\s*고$")


def _scrub_segments(sp: "SegmentProfit") -> None:
    """정형 부문 리스트에서 데이터 없는 행·재무라인 junk 제거(in place). 진짜 부문만 남긴다."""
    keep = []
    for s in sp.segments:
        nm = s.get("name", "")
        if s.get("revenue") is None and s.get("profit") is None:
            continue                                  # 값 없는 행(수익유형 설명 등)
        if _JUNK_NAME_RE.search(nm):
            continue                                  # 재무제표 라인 누출
        keep.append(s)
    sp.segments = keep


def _segment_confident(sp: "SegmentProfit") -> bool:
    """정형 신뢰게이트: ①부문명 clean(junk 없음) ②sum(부문 매출)≈총계. 하나라도 아니면 False→후보반환.

    junk(설명문·집계·컬럼머리·지표라벨)가 하나라도 섞이면 confident 아님 → 조용한 오답 대신 호출측에 후보 넘김.
    """
    names = [s.get("name", "") for s in sp.segments]
    for nm in names:
        if _DESC_RE.search(nm) or _AGG_RE.search(nm) or _NONSEG_RE.search(nm) or nm in _METRIC_ROW_NOISE:
            return False
    # 매출유형별 표 오인: '제품매출액/상품매출액/기타매출액'류는 사업부문 아닌 매출유형(휴스틸) → 후보로
    if names and all(re.search(r"(제품|상품|용역|기타|서비스)\s*매출", n) for n in names):
        return False
    # 지역별/지역정보 표 오인: 정규화(부문/사업 접미사 제거) 후 지역명이 절반 이상 or 전부 → 사업부문 아님.
    # ('국내부문'·'해외부문'·'본사 소재지 국가'/외국 등 K-IFRS 지역정보 disclosure를 후보로 강등)
    geo = sum(1 for n in names if _norm_seg_name(n) in _GEO_NAMES or n.replace(" ", "") in _GEO_NAMES)
    if names and (geo == len(names) or geo >= max(2, (len(names) + 1) // 2)):
        return False
    # 음수 매출: '기타/공통/조정/내부' 아닌 주요부문이 음수면 정렬 어긋난 제거·조정행 누출(대한전선 '전선'·
    # 명문제약 '서비스부문') → 후보로. (기타·조정 부문은 내부거래로 정상적으로 음수일 수 있어 허용)
    for s in sp.segments:
        rev = s.get("revenue")
        if rev is not None and rev < 0 and not re.search(r"기타|공통|조정|내부|소계", s.get("name", "")):
            return False
    # 이름 형태 불일치: 짧은 clean명(<7) + 긴 설명명(≥14) 혼재 = 설명이 junk → 후보반환
    lens = [len(n) for n in names]
    if len(lens) >= 2 and max(lens) >= 14 and min(lens) < 7:
        return False
    revs = [s["revenue"] for s in sp.segments if s.get("revenue") is not None]
    if not revs:
        return False
    # ②검산: 총계열(초과값)이 없으면 sum≈총계를 확인할 수 없다 → confident 아님(후보반환).
    # 260723 실측: 총계 부재 시 무조건 True이던 구멍으로 부분추출이 통과 —
    #   SK이노베이션 FY2024(분할합병 부분표 2/6부문·transposed 무검산)·한화 FY2024(dash에서
    #   값수집 중단 → 9부문 중 3부문만 값 보유, 무검산 통과). 검산 못 한 정형추출은 OK로 내지 않는다.
    ex = (sp.adjustments[0].get("revenue_excess") if sp.adjustments else None) or []
    if not ex:
        return False
    s = sum(revs)
    # 검산 성립 = ⓐ부문합 ≈ 초과값(총계) 직접 매칭, 또는 ⓑ부문합 + 조정 누적 ≈ 후속 초과값 —
    # 합계 직전에 조정열이 끼는 표(한화 FY2024: 부문9 + 연결조정 -10.5조 + 합계)는 ⓑ로만 성립.
    # 260723 리뷰 강화(오답-인증 채널 차단): ⓑ의 흡수 대상을 **음수**(내부거래 제거·연결조정 성격)
    # 최대 2회로 한정. 헤더 추출에서 부문명이 탈락하면 그 부문의 '양수 매출'이 excess로 밀리는데,
    # 이를 조정으로 흡수하면 오정렬 표(name↔value 한 칸 밀림)가 "검산 통과" 인증을 받는다 —
    # 재현 확인된 회귀. 양수 초과값은 총계 후보(직접/누적 비교 대상)로만 취급하고 흡수하지 않는다.
    # (잔여 위험: 부문합이 우연히 다른 초과값과 ±3% 일치하는 직접매칭 오인증 — cca184b strict에도
    #  동일하게 존재하던 채널로, 이름 정보 없이는 원리적으로 제거 불가. 그리드 접근 검토로 이관.)
    run = s
    absorbed = 0
    for a in ex:
        tol = max(abs(a), 1) * 0.03
        if a != 0 and (abs(s - a) <= tol or abs(run - a) <= tol):
            return True
        if a < 0 and absorbed < 2:
            run += a
            absorbed += 1
    return False


BUSINESS_DETAILS_FIELDS = (
    "segments", "sites", "utilization", "rnd", "backlog", "customers", "raw_materials",
    "product_pricing", "financial_ops", "financial_soundness", "investment_property",
)
_STANDARD_BIZ_FIELDS = set(BUSINESS_DETAILS_FIELDS[1:8])
_FINANCIAL_BIZ_FIELDS = set(BUSINESS_DETAILS_FIELDS[8:])
_CANDIDATE_CONTEXT_FIELDS = {"sites", "utilization", "rnd", "backlog", "customers"}
_CANDIDATE_CONTEXT_DEFAULT_CHARS = 20_000
_CANDIDATE_CONTEXT_MAX_CHARS = 60_000


async def build_business_details_payload(company_query: str, period: str = "latest",
                                         fields: list[str] | None = None,
                                         bsns_year: str = "", reprt_code: str = "",
                                         context_mode: str = "strict",
                                         context_chars: int = _CANDIDATE_CONTEXT_DEFAULT_CHARS) -> dict:
    """II.사업의 내용 구조화 추출 tool 진입점. 단계별 타이머(data.timings_ms)로 병목 실측.
    period 기본="latest"(사업·반기·분기 중 최신=최신 데이터). "annual"/"quarterly"로 명시 override.
    bsns_year+reprt_code 둘 다 지정 시 특정 과거 시점(시계열 추이용)을 조회 — period보다 우선."""
    from open_proxy_mcp.services.contracts import ToolEnvelope, AnalysisStatus
    from open_proxy_mcp.services.company import resolve_company_query
    from open_proxy_mcp.dart.client import get_dart_client
    from open_proxy_mcp.services.segment_candidates import find_segment_candidates

    want = set(fields or BUSINESS_DETAILS_FIELDS)
    mode = (context_mode or "strict").strip().lower()
    if mode not in {"strict", "candidate"}:
        return ToolEnvelope(tool="business_details", status=AnalysisStatus.ERROR, subject=company_query,
                            warnings=["context_mode는 strict 또는 candidate여야 합니다"]).to_dict()
    if mode == "candidate":
        if not isinstance(context_chars, int) or isinstance(context_chars, bool):
            return ToolEnvelope(tool="business_details", status=AnalysisStatus.ERROR, subject=company_query,
                                warnings=["context_chars는 1~60000 사이의 정수여야 합니다"]).to_dict()
        if not 1 <= context_chars <= _CANDIDATE_CONTEXT_MAX_CHARS:
            return ToolEnvelope(tool="business_details", status=AnalysisStatus.ERROR, subject=company_query,
                                warnings=["candidate context_chars는 1~60000 사이여야 합니다"]).to_dict()
        if len(want) != 1 or not want <= _CANDIDATE_CONTEXT_FIELDS:
            return ToolEnvelope(tool="business_details", status=AnalysisStatus.ERROR, subject=company_query,
                                warnings=["context_mode=candidate는 sites/utilization/rnd/backlog/customers 중 "
                                          "하나의 fields만 지정해야 합니다"]).to_dict()

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

    # KSIC(업종코드)로 금융·REIT 판별 — content 마커 오발 방지(카카오·한화·아모레퍼시픽 등 배제).
    # 64/65/66=금융·보험·금융지원, 68=부동산. 64992=지주회사는 충돌(신한금융 vs SK)이라 content가 최종판정.
    induty = ""
    try:
        _ci = await client.get_company_info(corp["corp_code"])
        induty = (_ci.get("induty_code") or "").strip()
    except Exception:
        pass
    _ind2 = induty[:2]
    _fin_ksic = _ind2 in ("64", "65", "66")      # 금융권
    _reit_ksic = _ind2 == "68"                    # 부동산(REIT 후보)

    if bsns_year or reprt_code:
        if not (bsns_year and reprt_code):
            return ToolEnvelope(tool="business_details", status=AnalysisStatus.ERROR,
                                subject=corp.get("corp_name", ""), data={"timings_ms": T},
                                warnings=["bsns_year와 reprt_code는 함께 지정해야 합니다"]).to_dict()
        reps = await _find_report_for_bsns_year(client, corp["corp_code"], bsns_year, reprt_code)
        _lap("search")
        if not reps:
            return ToolEnvelope(tool="business_details", status=AnalysisStatus.NO_FILING,
                                subject=corp.get("corp_name", ""), data={"timings_ms": T},
                                warnings=[f"{bsns_year}년 reprt_code={reprt_code} 정기보고서 없음"]).to_dict()
    else:
        reps = await _find_report_candidates(client, corp["corp_code"], period)
        _lap("search")
        if not reps:
            return ToolEnvelope(tool="business_details", status=AnalysisStatus.NO_FILING,
                                subject=corp.get("corp_name", ""), data={"timings_ms": T},
                                warnings=[f"{period} 정기보고서 없음"]).to_dict()

    from open_proxy_mcp.dart.client import DartClientError
    latest = reps[0]
    tag0 = _report_period_tag(latest)
    rept = latest
    sec = None
    fetch_method = "get_document"
    fetch_warn = None
    last_err = None
    # 1) 최신 → (014면) 동일기수 원본 순으로 get_document. 첨부/기재정정은 document.xml 부재(014)라도
    #    같은 기수 원본은 본문을 담고 있음(KB금융·삼성화재). 작년 기수로는 폴백 안 함(tag0 일치만).
    for i, cand in enumerate(reps):
        if i > 0 and tag0 and _report_period_tag(cand) != tag0:
            continue
        try:
            sec = await _fetch_getdoc(client, cand["rcept_no"])
            rept = cand
            if i > 0:
                fetch_method = "get_document(정정폴백)"
                fetch_warn = (f"최신 {latest.get('report_nm','')}({latest['rcept_no']}) document.xml 부재(014) "
                              f"→ 동일기수 {cand.get('report_nm','')}({cand['rcept_no']})로 폴백")
            break
        except DartClientError as e:
            last_err = e
            continue
    # 2) get_document 전부 실패 → viewer 웹fetch 폴백(최신 기준). 극소수·느림.
    if sec is None:
        try:
            sec = await _fetch_viewer_sec(client, latest["rcept_no"])
            rept = latest
            fetch_method = "viewer_fallback"
            fetch_warn = f"get_document 실패(DART {getattr(last_err, 'status', '?')}) → viewer 폴백"
        except Exception as ve:
            _lap("fetch")
            return ToolEnvelope(tool="business_details", status=AnalysisStatus.ERROR,
                                subject=corp.get("corp_name", ""),
                                data={"report": {"rcept_no": latest["rcept_no"], "report_nm": latest.get("report_nm")},
                                      "timings_ms": T},
                                warnings=[f"원문 다운로드 실패(get_document DART {getattr(last_err, 'status', '?')}, "
                                          f"viewer도 실패: {type(ve).__name__})"]).to_dict()
    _lap("fetch")

    form = detect_form(sec.get("toc", []))
    warnings: list[str] = []
    if fetch_warn:
        warnings.append(fetch_warn)
    if not sec.get("biz_text"):
        warnings.append("II.사업의 내용 섹션 미검출(정정본 가능) — 확인 필요")

    # segment_profit: 정형(1콜로 본문+주석 다 있음) → 신뢰게이트 → 후보 raw → N/A
    if form in (FORM_FINANCIAL, FORM_REIT):
        segment = {"status": UNSUPPORTED_FORM, "source": "none",
                   "na_reason": f"form_{form}_not_supported_v1 (금융·REIT는 D-트랙)"}
    elif "segments" not in want:
        segment = None
    else:
        sp = extract_segment_profit(sec.get("biz_text", ""), sec.get("note_text", ""), sec.get("note_source", ""))
        if sp.status == OK and sp.segments:
            _scrub_segments(sp)          # 값없는 행·재무라인 junk 제거 후 신뢰게이트
        _lap("segment")
        if sp.status == OK and sp.segments and not sp.cross_conflict and _segment_confident(sp):
            segment = {"status": OK, "source": "deterministic", "revenue_metric": sp.revenue_metric,
                       "profit_metric": sp.profit_metric, "unit": sp.unit, "items": sp.segments,
                       "reconciliation": "부문합≈총계 검산 통과"}
        else:
            # 정형 저신뢰/실패 → '어느 표인지' 점수매기지 말고 영업부문 주석 구간을 통째로
            # 마크다운으로 넘겨 호출측 AI가 읽게 한다(260718 사용자 결정). 없으면 II.사업의내용 폴백.
            from open_proxy_mcp.services.segment_candidates import (
                render_segment_note_markdown, render_biz_section_markdown, _md_has_data_rows)
            full_html = sec.get("note_html", "")   # get_document full html
            note_md = render_segment_note_markdown(full_html)
            _MD_NOTE = ("아래는 영업부문 주석(K-IFRS 1108) 원문을 마크다운으로 옮긴 것입니다. "
                        "여기서 사업부문별 매출·영업이익을 읽으세요. 합계/조정/부문간/미배분 열·행은 제외.")
            if note_md:
                segment = {"status": "NEEDS_REVIEW", "source": "note_markdown",
                           "region": "연결 영업부문 주석", "note": _MD_NOTE, "segment_note_md": note_md}
            else:
                cands = find_segment_candidates(full_html)
                if cands:
                    # 주석 앵커 실패했지만 부문표 신호는 있음(지주사류) → II.사업의내용 마크다운 폴백
                    biz_md = render_biz_section_markdown(full_html)
                    if biz_md and _md_has_data_rows(biz_md):
                        segment = {"status": "NEEDS_REVIEW", "source": "biz_markdown",
                                   "region": "II.사업의 내용", "note": _MD_NOTE, "segment_note_md": biz_md}
                    else:
                        segment = {"status": "NEEDS_REVIEW", "source": "raw_candidates",
                                   "note": "부문표 후보(상위)에서 사업부문별 매출·영업이익을 읽으세요. 합계/조정/총계 열 제외.",
                                   "candidates": cands[:5]}
                    warnings.append("segment_profit: 정형 저신뢰 → 원문 마크다운/후보 반환(호출측 추출)")
                else:
                    # 부문 신호 전무 = 단일 영업부문사 → N/A
                    segment = {"status": NOT_APPLICABLE, "source": "none",
                               "na_reason": sp.na_reason or "부문표 미검출(단일 영업부문 추정)"}

    data = {
        "corp": {"name": corp.get("corp_name"), "corp_code": corp.get("corp_code"), "stock_code": corp.get("stock_code")},
        "report": {"rcept_no": rept["rcept_no"], "report_nm": rept.get("report_nm"), "rcept_dt": rept.get("rcept_dt")},
        "form_type": form,
        "segments": segment if "segments" in want else None,
    }
    # 추가 필드: markdown-primary(소절 원문 마크다운 → 호출측 AI 추출). biz 텍스트=hint, full html=md.
    from open_proxy_mcp.services import biz_fields as _bf
    _biz_t = sec.get("biz_text", "")
    _full_html = sec.get("note_html", "")
    # D-트랙(금융·REIT) 시그니처 폴백은 II.사업의내용 구간만 스캔 — III.재무 주석 회계표 오발 차단.
    _biz_html = sec.get("biz_html") or _full_html
    _standard_fields = _STANDARD_BIZ_FIELDS
    _d_fields = _FINANCIAL_BIZ_FIELDS
    _full_region_index = _bf.build_region_index(_full_html) if want & _standard_fields else None
    _biz_region_index = (_bf.build_region_index(_biz_html) if want & _d_fields
                         else None)
    if "sites" in want:
        data["sites"] = _bf.extract_sites(_biz_t, _full_html, _full_region_index)
    if "utilization" in want:
        data["utilization"] = _bf.extract_utilization(_biz_t, _full_html, _full_region_index)
    if "rnd" in want:
        data["rnd"] = _bf.extract_rnd(_biz_t, _full_html, _full_region_index)
    if "backlog" in want:
        data["backlog"] = _bf.extract_backlog(_biz_t, _full_html, _full_region_index)
    if "customers" in want:
        data["customers"] = _bf.extract_customers(_biz_t, _full_html, _full_region_index)
    if "raw_materials" in want:
        data["raw_materials"] = _bf.extract_raw_materials(_biz_t, _full_html, _full_region_index)
    if "product_pricing" in want:
        data["product_pricing"] = _bf.extract_product_pricing(_biz_t, _full_html, _full_region_index)
    if mode == "candidate":
        candidate_field = next(iter(want))
        strict_result = data.get(candidate_field, {})
        if strict_result.get("extraction_status") == "NOT_COLLECTED":
            candidate = _bf.render_candidate_context(
                candidate_field, _full_html, context_chars, _full_region_index,
            )
            data["candidate_context"] = candidate or {
                "status": "NOT_FOUND",
                "field": candidate_field,
                "context_chars": context_chars,
                "warning": "저신뢰 보조 문맥에 사용할 헤딩 후보를 찾지 못했습니다.",
            }
    # D-트랙 금융·REIT 필드 = KSIC 게이트(금융권만) + content-signature. KSIC로 비금융 원천 배제.
    if "financial_ops" in want and _fin_ksic:
        data["financial_ops"] = _bf.extract_financial_ops(_biz_t, _biz_html, _biz_region_index)
    if "financial_soundness" in want and _fin_ksic:
        data["financial_soundness"] = _bf.extract_financial_soundness(_biz_t, _biz_html, _biz_region_index)
    # 투자부동산: 부동산(68=REIT)·보험(65=투자부동산 보유)만. 지주(64)는 primary가 영업현황이라 제외
    # (broadened 임대료/임차인 시그니처가 지주 프로즈에 과발하던 것 방지). _biz_html=II구간만(III회계표 배제).
    if "investment_property" in want and (_reit_ksic or _ind2 == "65"):
        data["investment_property"] = _bf.extract_investment_property(_biz_t, _biz_html, _biz_region_index)
    # 자산가치(토지·투자부동산·지분증권 원가vs공정가치)는 별도 tool asset_holdings로 이관(260720).
    data["induty_code"] = induty or None
    _lap("Afields")
    data["fetch_method"] = fetch_method   # "get_document"(1 API콜) | "viewer_fallback"(014 등 웹fetch)

    T["total"] = sum(v for k, v in T.items())
    data["timings_ms"] = T
    return ToolEnvelope(tool="business_details", status=AnalysisStatus.EXACT,
                        subject=corp.get("corp_name", ""), data={k: v for k, v in data.items() if v is not None},
                        warnings=warnings).to_dict()
