"""부문표 격자 정밀 판독 — 표 '선택'은 기존 텍스트 경로가, 값 '읽기'는 격자가.

flat 텍스트 수집의 구조적 한계(열 밀림·행 라벨 혼동·부분수집)를, 텍스트 경로가 고른
앵커의 표를 행×열 격자로 읽어 이름↔값을 열 인덱스로 결합함으로써 해소한다.

계약(단조 안전): grid_refine은 성공 시 SegmentProfit, 실패 시 None을 반환하고,
호출측은 반환값을 기존 신뢰게이트(_scrub_segments + _segment_confident)에 다시 태워
통과할 때만 채택한다 — 텍스트 결과보다 나빠질 수 없다.

행 선택 규칙: 같은 표에 여러 매출 개념 행이 있으면 외부매출 계열을 우선한다
(연결 재무제표와 검산이 맞는 값 = 연도 간 개념 일관). 선택한 행 라벨은
revenue_metric으로 그대로 노출한다.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from open_proxy_mcp.services.segment_candidates import _TABLE_RE, _table_to_grid

# 매출 행 우선순위: 외부 계열 → generic(외부 의미로 쓰이는 표가 대부분) → 총부문 계열.
_REV_ROW_PRIORITY = [
    re.compile(r"외부\s*고객|외부\s*매출|고객과의\s*계약에서\s*생기는\s*수익"),
    re.compile(r"^(?:순\s*)?매출액$|수익\s*\(매출액\)|^영업\s*수익$|^매\s*출\s*액$"),
    re.compile(r"총\s*부문\s*수익|부문\s*수익|^수익$|매출액(?!\s*비율|\s*원가|\s*총이익)|영업수익"),
]
_PROF_ROW_RE = re.compile(r"(?!.*(?:계속\s*영업|중단\s*영업|법인세|당기\s*순|총포괄))"
                          r".*(?:영업\s*이익|영업\s*손익|부문\s*이익|부문\s*손익)")
_TOTAL_COL_RE = re.compile(r"(?:^|\s)합\s*계$|^계$|총\s*계$|전체\s*합계$")
_ADJ_COL_RE = re.compile(r"조정|내부\s*거래|제거|미배분|연결\s*조정")
_NOISE_HEADER = {"영업부문", "구분", "구 분", "사업부문", "부문", "기업 전체 총계", "보고부문", ""}
_UNIT_RE = re.compile(r"\(\s*단위\s*[:：]?\s*([^)]{1,20})\)")
_NUM_RE = re.compile(r"^\(?\s*[△▲-]?\s*[\d,]+(?:\.\d+)?\s*\)?$")


def _num(cell: str) -> float | None:
    """표 셀 → 숫자. 괄호/△/▲=음수, dash·빈칸=None, %·비숫자=None."""
    t = cell.strip()
    if not t or t in {"-", "－", "—"} or "%" in t:
        return None
    if not _NUM_RE.match(t):
        return None
    neg = t.startswith("(") or t.startswith("△") or t.startswith("▲") or t.startswith("-")
    digits = re.sub(r"[^\d.]", "", t)
    if not digits or digits == ".":
        return None
    try:
        v = float(digits)
    except ValueError:
        return None
    return -v if neg else v


def _find_anchor_pos(html: str, anchor: str) -> int:
    """텍스트 경로 앵커 제목(공백 정규화)을 html에서 유연 검색."""
    toks = [re.escape(t) for t in anchor.split() if t]
    if not toks:
        return -1
    m = re.search(r"[\s>]*".join(toks), html)
    return m.start() if m else -1


def _label_of(row: list[str]) -> str:
    """행 라벨 = 앞쪽 비숫자 셀 최대 2개 결합.

    다층 라벨 열(1열 '수익(매출액)' + 2열 '고객과의 계약에서 생기는 수익')에서 첫 셀만 보면
    외부매출 부라벨을 놓친다 — 두 셀을 이어 붙여 패턴이 어느 쪽이든 잡히게 한다."""
    parts: list[str] = []
    for c in row:
        t = c.strip()
        if not t:
            continue
        if _num(t) is not None:
            break
        if t not in parts:
            parts.append(t)
        if len(parts) >= 2:
            break
    return " ".join(parts)


def _pick_metric_row(grid: list[list[str]], patterns) -> tuple[int, str] | None:
    """우선순위 패턴 순서로 첫 매칭 행(값이 2개 이상 실린)을 고른다."""
    for pat in (patterns if isinstance(patterns, list) else [patterns]):
        for i, row in enumerate(grid):
            label = _label_of(row)
            if not label or len(label) > 30 or not pat.search(label):
                continue
            vals = [v for v in (_num(c) for c in row) if v is not None]
            if len(vals) >= 2:
                return i, label
    return None


def _header_row_for(grid: list[list[str]], metric_i: int) -> list[str] | None:
    """지표행 위쪽에서 부문명 헤더 행을 고른다 — 이름형 셀(비숫자·짧음)이 가장 많은 행."""
    best, best_score = None, 0
    for i in range(max(0, metric_i - 8), metric_i):
        row = grid[i]
        names = [c.strip() for c in row
                 if c.strip() and _num(c) is None and len(c.strip()) <= 30
                 and not _UNIT_RE.search(c)]
        distinct = [n for n in dict.fromkeys(names) if n not in _NOISE_HEADER]
        if len(distinct) >= 2 and len(distinct) >= best_score:
            best, best_score = row, len(distinct)
    return best


def _parse_table(tbl_html: str) -> dict | None:
    soup = BeautifulSoup(tbl_html, "lxml")
    tb = soup.find("table")
    if tb is None:
        return None
    grid = _table_to_grid(tb)
    if len(grid) < 2:
        return None
    ncol = max(len(r) for r in grid)
    grid = [r + [""] * (ncol - len(r)) for r in grid]

    rev = _pick_metric_row(grid, _REV_ROW_PRIORITY)
    if rev is None:
        return None
    rev_i, rev_label = rev
    header = _header_row_for(grid, rev_i)
    if header is None:
        return None
    prof = _pick_metric_row(grid[rev_i:], _PROF_ROW_RE)
    prof_row = grid[rev_i + prof[0]] if prof else None
    prof_label = prof[1] if prof else ""

    rev_row = grid[rev_i]
    segments, excess, seen = [], [], set()
    _DASHES = {"-", "－", "—", "△"}
    for ci in range(ncol):
        name = header[ci].strip()
        cell = rev_row[ci].strip()
        is_dash = cell in _DASHES
        val = 0.0 if is_dash else _num(rev_row[ci])
        # dash 열은 '값 0'의 자리표시자(신규 편입 부문 등) — 열을 버리면 부문이 소실된다.
        if val is None or name in seen:
            continue          # 라벨 열·빈 열·rowspan 중복 열
        seen.add(name)
        pcell = prof_row[ci].strip() if prof_row is not None else ""
        pv = (0.0 if pcell in _DASHES else _num(pcell)) if prof_row is not None else None
        if not name or name in _NOISE_HEADER:
            continue
        if _TOTAL_COL_RE.search(name) or _ADJ_COL_RE.search(name):
            excess.append((name, val))
        else:
            segments.append({"name": name, "revenue": val, "profit": pv})
    if len(segments) < 2:
        return None
    # 검산 재료: 조정열 → 합계열 순으로 정렬(기존 게이트의 '음수 조정 흡수 후 총계 매칭' 로직 호환)
    excess.sort(key=lambda x: bool(_TOTAL_COL_RE.search(x[0])))
    unit = ""
    for row in grid[:6]:
        for c in row:
            um = _UNIT_RE.search(c)
            if um:
                unit = um.group(1).strip()
                break
        if unit:
            break
    return {"segments": segments, "excess": [v for _, v in excess],
            "revenue_metric": rev_label, "profit_metric": prof_label, "unit": unit}


def grid_refine(full_html: str, text_sp) -> "object | None":
    """텍스트 경로가 고른 주석 앵커의 표를 격자로 재판독한 SegmentProfit(검산 재료 포함).

    실패·불충분 시 None — 호출측은 기존 게이트 통과분만 채택한다.
    """
    from open_proxy_mcp.services.business_details import OK, SegmentProfit, _norm_seg_name

    anchor = (getattr(text_sp, "anchor", "") or "").strip()
    if not full_html or not anchor or getattr(text_sp, "source", "") != "note":
        return None
    pos = _find_anchor_pos(full_html, anchor)
    if pos < 0:
        return None
    parsed, attempts = None, 0
    for m in _TABLE_RE.finditer(full_html, pos):
        if attempts >= 5 or m.start() > pos + 80000:
            break             # 앵커에서 먼 표는 다른 주석
        if len(m.group(0)) < 1500:
            continue          # 스페이서/장식 표(빈 셀 한둘) — 시도 횟수에 안 셈
        attempts += 1
        parsed = _parse_table(m.group(0))
        if parsed:
            break
    if not parsed:
        return None
    # 텍스트 경로가 부문명을 확보한 경우 이름 겹침 검증(같은 표를 읽었는지 — 오선택 방어)
    text_names = {_norm_seg_name(s.get("name", "")) for s in (text_sp.segments or [])
                  if s.get("name")}
    if text_names:
        grid_names = {_norm_seg_name(s["name"]) for s in parsed["segments"]}
        if not (text_names & grid_names):
            return None
    sp = SegmentProfit(status=OK, source="note_grid",
                       revenue_metric=parsed["revenue_metric"],
                       profit_metric=parsed["profit_metric"],
                       unit=parsed["unit"] or getattr(text_sp, "unit", ""),
                       segments=parsed["segments"],
                       note_source=getattr(text_sp, "note_source", ""),
                       anchor=anchor)
    sp.adjustments = [{"revenue_excess": parsed["excess"]}]
    sp.raw_value_counts = {"headers": len(parsed["segments"]),
                           "revenue": len(parsed["segments"]) + len(parsed["excess"]),
                           "profit": sum(1 for s in parsed["segments"] if s.get("profit") is not None)}
    return sp


# ── 전사 차원 공시(K-IFRS 1108 문단 32-34): 지역별·제품/서비스별 수익 ──
# 부문 주석 안의 부수 표들 — 단일부문 회사의 실질 정보원. 표 직전 캡션과 이름 구성으로 분류.
_GEO_CAPTION_RE = re.compile(r"지역|국가|시장\s*별|권역")
_PRODUCT_CAPTION_RE = re.compile(r"제품|서비스\s*별|재화\s*(?:와|및)?\s*용역|수익\s*유형|매출\s*유형|품목|플랫폼|수익원|용역\s*별")
_CAPTION_WINDOW = 600


def _caption_before(html: str, table_start: int) -> str:
    frag = html[max(0, table_start - _CAPTION_WINDOW):table_start]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", frag))


def scan_entity_wide(full_html: str, anchor: str, geo_names: set,
                     product_fallback: bool = False, exclude_names: set | None = None) -> dict:
    # NOTE(회계사 QA 260724): 제품·서비스별(product) 분류는 오분류율이 높아 v2로 이연 —
    # 현재는 지역별(geo)만 반환한다. product_fallback/exclude_names 파라미터는 v2 재사용 예정.
    """부문 주석 구간에서 지역별/제품별 수익 표를 격자로 판독.

    분류: ① 이름 과반이 지역명 → geo ② 캡션에 제품·수익유형 신호(지역 신호 없음) → product.
    게이트: 항목 ≥2 + 합계 열 존재 + 부문합≈총계(±0.5%) — 검산 못 한 표는 반환하지 않는다.
    반환 {"geo": {...}|None, "product": {...}|None}.
    """
    out = {"geo": None, "product": None}
    if not full_html or not anchor:
        return out
    pos = _find_anchor_pos(full_html, anchor)
    if pos < 0:
        return out
    attempts = 0
    geo_md_fallback = None
    for m in _TABLE_RE.finditer(full_html, pos):
        if attempts >= 10 or m.start() > pos + 150000:
            break
        if len(m.group(0)) < 1500:
            continue
        attempts += 1
        p = _parse_table(m.group(0))
        if not p or len(p["segments"]) < 2:
            continue
        # ── 분류 먼저: 지역표인가 (게이트 탈락해도 지역표면 원문 폴백 대상) ──
        names_norm = [re.sub(r"[\s()]", "", s["name"]) for s in p["segments"]]
        geo_cnt = sum(1 for n in names_norm if n in geo_names)
        caption = _caption_before(full_html, m.start())
        is_geo = geo_cnt >= max(2, (len(names_norm) + 1) // 2) or (
            geo_cnt >= 1 and bool(_GEO_CAPTION_RE.search(caption)))
        is_product = False    # v2로 이연 (위 NOTE)

        # ── 정형 게이트 (표준 계약: 앵커 → 정형+검산 → 원문 마크다운 → 명시적 부재) ──
        fail = ""
        if not p["excess"]:
            fail = "총계 열 부재(검산 불가)"
        else:
            total = p["excess"][-1]
            ssum = sum(s0["revenue"] for s0 in p["segments"]) + sum(p["excess"][:-1])
            if not total or abs(ssum - total) > abs(total) * 0.005:
                fail = "항목합≠총계(검산 실패)"
        if not fail and not p["unit"]:
            um = _UNIT_RE.search(caption)
            if um:
                p["unit"] = um.group(1).strip()
            if not p["unit"]:
                fail = "단위 미상(오환산 방지)"
        # 수익 '구성' 행(부분값)·내부거래 포함 총액 행 — 애널리스트 QA: 파마리서치 6%·
        # 에코프로 0.08%·SK이노 213% 왜곡. 합계행 재선택은 v2.
        if not fail and re.search(r"용역|재화|제공|상품|제품|기타\s*수익|로열티|수수료|임대|총\s*매출|내부",
                                  p["revenue_metric"]):
            fail = "수익 구성행(부분값 위험)"
        if not fail and any(v < 0 for v in p["excess"]):
            fail = "내부거래 포함 총액 기준(조정 열 존재)"

        if fail:
            # 게이트 탈락한 '지역표'는 버리지 않고 원문 마크다운으로 강등 보관(첫 건만)
            if is_geo and out["geo"] is None and geo_md_fallback is None:
                try:
                    from bs4 import BeautifulSoup as _BS

                    from open_proxy_mcp.services.segment_candidates import _table_to_markdown
                    md = _table_to_markdown(_BS(m.group(0), "lxml").find("table"))
                except Exception:
                    md = ""
                if md:
                    geo_md_fallback = {
                        "status": "NEEDS_REVIEW", "extraction_status": "NEEDS_REVIEW",
                        "markdown": md,
                        "note": f"지역별 수익 표 발견했으나 정형 게이트 탈락({fail}) — "
                                "원문 표를 그대로 제공하니 직접 읽어 판단하세요.",
                    }
            continue

        clean_caption = re.sub(r'[A-Za-z-]+="[^"]*"|[<>]|BORDER|WIDTH|HEIGHT', " ", caption)
        clean_caption = re.sub(r"\s+", " ", clean_caption).strip()[-120:]
        payload = {
            "status": "SUCCESS", "extraction_status": "SUCCESS",
            "items": [{"name": s["name"], "revenue": s["revenue"]} for s in p["segments"]],
            "unit": p["unit"], "revenue_metric": p["revenue_metric"],
            "regional_total": total,          # tie-out 메타 — 호출측이 연결매출과 대조 가능
            "reconciliation": "항목합≈지역합계 검산 통과",
            "note": "합계는 표 기준 — 연결 손익계산서 매출과 대조해 개념(총액/외부) 확인 권장",
            "self_check": "regional_total이 연결 매출과 크게 다르거나 지역 구성이 이상하면 "
                          "이 정형값을 쓰지 말고 segments 필드의 주석 원문(NEEDS_REVIEW 마크다운)으로 "
                          "직접 확인하세요.",
            "basis_caption": clean_caption,
        }
        if is_geo and out["geo"] is None:
            out["geo"] = payload
        elif is_product and out["product"] is None:
            out["product"] = payload
        if out["geo"] and out["product"]:
            break
    if out["geo"] is None and geo_md_fallback is not None:
        out["geo"] = geo_md_fallback          # 표준 사다리 2단: 정형 실패 → 원문 마크다운
    return out
