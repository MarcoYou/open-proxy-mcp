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

import html as _html
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
# 부문 앵커가 없을 때 쓰는 지역 전용 앵커 — 「지역에 대한 정보」류 소제목이나
# 표 머리에만 등장하는 「본사 소재지 국가」를 잡는다(K-IFRS 1108 entity-wide 표기).
_GEO_ANCHOR_RE = re.compile(r"지역(?:별|에)\s*(?:대한\s*)?정보|본사\s*소재지\s*국가|지역\s*합계")
# 표 개수 상한(비용 가드) — 후보 신호가 있는 표만 센다.
# 부문 절 하나가 수백 KB 인 회사가 있어 지역표가 한참 뒤로 밀린다. 표 파싱은 DART 콜보다
# 훨씬 싸므로 상한을 넉넉히 잡는다.
_EW_MAX_ATTEMPTS = 60
# 지역표 전용 길이 하한 — 데이터가 한 행뿐이라 표가 작다.
_EW_GEO_MIN_CHARS = 350
_GEO_HEAD_RE = re.compile(r"본사\s*소재지|외\s*국|국\s*외|북미|미주|아시아|유럽|중동|중남미|지역\s*합계")
_EW_CANDIDATE_RE = re.compile(r"지역|국가|본사\s*소재지|외국|국내|해외|국외|북미|미주|아시아|유럽|중동|"
                              r"제품|서비스|품목|수익\s*유형|매출\s*유형|부문")


_DOMESTIC_RE = re.compile(r"^(본사소재지국가?|본사소재지|국내|한국|대한민국|내수)$")
# 지역명에 붙는 각주 표시 — 카카오는 항목이 「국내(주1)」이라 국내로 인식되지 않아
# 해외비중이 100%로 나왔다(실제로는 국내가 대부분).
_FOOTNOTE_RE = re.compile(r"\(?\s*(?:주|참고|note|\*)\s*\d*\s*\)?\s*$", re.I)


def _region_key(name: str) -> str:
    """지역명 정규화 — 공백·괄호 제거 + 각주 표시 제거."""
    return re.sub(r"[\s()]", "", _FOOTNOTE_RE.sub("", (name or "").strip()))


def _all_region_names(names_norm: list[str], geo_names: set) -> bool:
    """항목이 전부 지역명인가 — 합계 열 없이 항목합을 총계로 쓸 수 있는 조건."""
    keys = [_region_key(n) for n in names_norm]
    return bool(keys) and all(k in geo_names or _DOMESTIC_RE.match(k) for k in keys)


def _foreign_share(items: list[dict]) -> dict:
    """해외 매출 비중(%) — **단위가 약분되므로 단위 미상일 때도 맞는 유일한 지표**.

    격자 매핑이 어긋나거나 단위를 잘못 읽어 절대금액이 10^6 배 틀려도 비중은 정확하다.
    그래서 절대금액보다 비중을 앞세운다.
    국내 = 본사 소재지 국가 / 국내 / 한국 / 대한민국, 나머지는 해외로 본다.
    """
    dom = fgn = 0.0
    for it in items:
        n = _region_key(it.get("name") or "")
        v = it.get("revenue") or 0
        if _DOMESTIC_RE.match(n):
            dom += v
        else:
            fgn += v
    tot = dom + fgn
    if not tot:
        return {}
    out = {"domestic_revenue": dom, "foreign_revenue": fgn,
           "foreign_share_pct": round(fgn / tot * 100, 1),
           "share_basis": "국내=본사 소재지 국가/국내/한국, 그 외=해외"}
    if dom == 0:
        # 「해외 100%」와 「국내 항목이 표에 없음」은 다르다 — 대한해운은 항목이
        # 아시아·오세아니아·유럽·북아메리카뿐이라 국내 구분 자체가 없다.
        out["share_caveat"] = ("표에 국내 구분 항목이 없어 100%로 계산됐습니다 — "
                               "국내 매출이 0이라는 뜻이 아닐 수 있습니다(항목명을 확인하세요)")
    return out


# DART XML 의 표 셀은 `<TE>`(table entry) 다 — `<TD>` 만 찾으면 데이터 행이 통째로 빈다.
# 머리 행만 `<TH>` 라 헤더는 읽히고 본문은 안 읽혀 「항목 0개」로 보였다.
_CELL_TAGS = ["td", "th", "te", "tu"]


def _read_single_region_with_subaxis(chunk: str) -> dict | None:
    """지역 축이 **하나**이고 그 아래 부문 축이 걸린 표 → 「전량 그 지역」으로 읽는다.

    조흥은 머리가 3층이다:
        지역
        본사 소재지 국가                    ← 지역 축이 하나뿐
        부문 | 부문 합계
        치즈 | 식품 및 식품첨가물 등          ← 부문 축
        수익(매출액) 295,413,841 | 192,832,219 | 488,246,060
    부문표 파서는 리프(치즈·식품첨가물)를 잡아 「지역표가 아니다」로 분류했다. 그런데
    지역으로 보면 **전량 국내 488,246,060** 이라는 확정 정보다.
    """
    from bs4 import BeautifulSoup

    try:
        t = BeautifulSoup(chunk, "lxml").find("table")
    except Exception:
        return None
    if t is None:
        return None
    head, body = [], []
    for r in t.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in r.find_all(_CELL_TAGS)]
        (body if any(_row_num(c) is not None for c in cells) else head).append(cells)
    if not head or not body:
        return None
    flat = [h for row in head for h in row if h]
    regions = {h for h in flat if len(h) <= 18 and REGION.match(_region_key(h))}
    if len(regions) != 1:
        return None                       # 지역이 둘 이상이면 다른 리더가 처리한다
    if not any(TOTAL.search(h) for h in flat):
        return None                       # 합계 열이 있어야 그 지역의 총액을 안다
    row = next((r for r in body
                if re.search(r"수익|매출", " ".join(c for c in r if _row_num(c) is None))), None)
    if row is None:
        return None
    vals = [v for v in (_row_num(c) for c in row) if v is not None]
    if len(vals) < 2:
        return None
    name = next(iter(regions))
    return {"segments": [{"name": name, "revenue": float(vals[-1])}],   # 마지막 = 합계
            "excess": [float(vals[-1])], "revenue_metric": "수익",
            "unit": "", "_single_region": True, "_subaxis": True}


def _read_column_oriented_geo(chunk: str) -> dict | None:
    """지역이 **열**에 오는 표를 머리↔값 인덱스로 직접 읽는다.

    부문표 파서는 항목 2개 이상을 전제해서, 지역이 하나뿐인 표
    (동우팜투테이블 「본사 소재지 국가 | 325,458」)는 항목 0개로 나와 통째로 버려졌다.
    그런데 그건 정보가 없는 게 아니라 **「전량 국내」라는 확정 정보**다.
    """
    from bs4 import BeautifulSoup

    try:
        t = BeautifulSoup(chunk, "lxml").find("table")
    except Exception:
        return None
    if t is None:
        return None
    head, body = [], []
    for r in t.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in r.find_all(_CELL_TAGS)]
        if any(_row_num(c) is not None for c in cells):
            body.append(cells)
        else:
            head.append(cells)
    if not head or not body:
        return None
    leaves = [h for h in head[-1] if h] or [h for row in head for h in row if h]
    row = body[0]
    regions, total = [], None
    vals = [(i, _row_num(c)) for i, c in enumerate(row) if _row_num(c) is not None]
    # 값 열과 지역 머리를 **개수로** 맞춘다(라벨 칸 offset 흡수)
    labs = [h for h in leaves if REGION.match(_region_key(h)) or TOTAL.search(h)]
    if len(labs) != len(vals) or not labs:
        return None
    for (idx, v), lab in zip(vals, labs):
        if TOTAL.search(lab):
            total = v
        elif REGION.match(_region_key(lab)):
            regions.append({"name": lab.strip(), "revenue": float(v)})
    if not regions:
        return None
    metric = next((c for c in row if _row_num(c) is None and c.strip()), "수익")
    return {"segments": regions, "excess": [total] if total is not None else [],
            "revenue_metric": metric, "unit": "", "_col_oriented": True}


def _read_row_oriented_geo(chunk: str) -> dict | None:
    """지역이 **행**에 있는 표를 읽는다 — 열 지향과 레이아웃이 정반대다.

    열 지향:
        (머리) 본사 소재지 국가 | 북미 | 아시아 | 유럽 | 지역 합계
        수익(매출액)  55,298,034 | 83,444,813 | ...
    행 지향:
        (머리)        | 총부문수익 | 비유동자산 | ...
        지역 | 한국    | 10,553,720 | 20,165,226
             | 중국    | 11,108,354 |  4,989,625
        지역 합계      | 48,916,104 | 58,287,995

    행 지향은 지역명이 첫(또는 둘째) 열에 오고 금액 열이 여럿이라 부문표 파서가
    항목을 못 뽑았다. 여기서는 **지역명이 든 행**만 골라 첫 금액 열을 수익으로 읽는다.
    비유동자산 열이 함께 있으면 생산지 판별에 쓰도록 같이 돌려준다.
    """
    from bs4 import BeautifulSoup

    try:
        t = BeautifulSoup(chunk, "lxml").find("table")
    except Exception:
        return None
    if t is None:
        return None
    rows = t.find_all("tr")
    if len(rows) < 3:
        return None
    head = [c.get_text(" ", strip=True) for c in rows[0].find_all(_CELL_TAGS)]
    asset_col = next((i for i, hcell in enumerate(head)
                      if re.search(r"비유동자산|유형자산", hcell or "")), None)
    items, assets, total = [], {}, None
    for r in rows[1:]:
        cells = [c.get_text(" ", strip=True) for c in r.find_all(_CELL_TAGS)]
        if len(cells) < 2:
            continue
        labels = [c for c in cells if not _row_num(c)]
        nums = [(_row_num(c), i) for i, c in enumerate(cells) if _row_num(c) is not None]
        if not nums or not labels:
            continue
        name = next((x for x in labels if REGION.match(_region_key(x))), None)
        if name is None:
            if any(TOTAL.search(x) for x in labels) and total is None:
                total = nums[0][0]
            continue
        items.append({"name": name.strip(), "revenue": float(nums[0][0])})
        if asset_col is not None:
            av = next((v for v, i in nums if i >= asset_col), None)
            if av is not None:
                assets[name.strip()] = float(av)
    if len(items) < 2:
        return None
    return {"segments": items, "excess": [total] if total is not None else [],
            "revenue_metric": (head[1] if len(head) > 1 else "") or "수익",
            "unit": "", "_row_oriented": True,
            "_assets_by_region": assets or None}


def _row_num(c):
    c = (c or "").replace(" ", "").replace("\xa0", "")
    if not re.fullmatch(r"-?\(?\d{1,3}(?:,\d{3})*\)?", c) or c in ("", "-"):
        return None
    try:
        v = int(c.strip("()").replace(",", ""))
    except ValueError:
        return None
    return -v if c.startswith("(") else v


TOTAL = re.compile(r"합\s*계|총\s*계")
# 「수출/내수」는 II 매출실적표 용어인데 **III 주석에서 지역 축으로 쓰는 회사**가 있다
# (「지역 | 수출 | 내수 | 연결조정 | 지역 합계」 형태).
# 수출=해외, 내수=국내로 읽는다. 「연결조정」은 지역이 아니라 자동으로 빠진다.
REGION = re.compile(
    r"^\s*(?:본사\s*소재지(?:\s*국가)?|외국|국내|해외|국외|대한민국|한국|수\s*출|내\s*수|"
    r"북미|미주|남미|중남미|아메리카|아시아[가-힣\s/·및]{0,14}|유럽|중동|아프리카|오세아니아|"
    r"중국|일본|미국|베트남|인도|인도네시아|대만|태국|싱가포르|홍콩|호주|캐나다|멕시코|"
    r"브라질|러시아|독일|영국|프랑스|폴란드|헝가리|기타(?:\s*국가|\s*지역)?)\s*$")


def _find_geo_anchor_pos(html: str) -> int:
    """지역 전용 앵커 위치(없으면 -1). 부문정보 앵커 폴백."""
    m = _GEO_ANCHOR_RE.search(html or "")
    if not m:
        return -1
    # 표 머리에서 잡혔을 수 있으니 조금 앞에서 시작해 그 표를 포함시킨다
    return max(0, m.start() - 3000)


# ── 주석 절 목록 (선언 구조 파싱을 주석 층까지) ─────────────────────────────────
# 주석 본문엔 **AASSOCNOTE 가** 없다 — 그건 챕터 경계까지만 있고 주석 안으로 들어오면
# 사라진다. **ACODE 는 다르다**: 부문 구간 안에도 있고(`ifrs-full_Revenue` ·
# `dart_OperatingIncomeLoss` · `ifrs-full_InformationAboutMajorCustomers`), `ACONTEXT` 도
# 붙어 있어 셀마다 기간·연결/별도가 선언돼 있다.
#   ※ 260802 시점 주석은 이 둘을 묶어 「AASSOCNOTE·ACODE 가 없다」고 적어 두었는데 ACODE 쪽이
#     사실과 달랐다. 셀 코드로 값을 직접 읽는 경로를 막고 있었으므로 바로잡는다.
# 아래 `<A name='tocN'>` 은 **viewer HTML 폴백 전용**이다 — 주 경로(document.xml)엔 없다.
_TOC_ANCHOR_RE = re.compile(r"<A\s+name=['\"]toc(\d+)['\"]\s*>\s*([^<]{2,80}?)\s*</A>", re.I)
# 주 경로(document.xml)의 주석 절 표지 — XBRL 택소노미 코드.
#   <TABLE-GROUP ACLASS="{XBRL}NT_C_D871100"><TITLE ATOC="Y">33. 부문별 정보 (연결)</TITLE>
# 회사마다 절 번호와 제목이 달라도 **코드는 하나로 모인다**. 제목 사전은 새 표현이 나오면
# 뚫리지만 코드는 안 뚫린다.
_XBRL_BLOCK_RE = re.compile(
    r'<TABLE-GROUP[^>]*ACLASS="\{XBRL\}([^"]+)"[^>]*>\s*<TITLE[^>]*>(.*?)</TITLE>', re.S)
# 지역 표가 실제로 든 블록의 코드 계열:
#   D871 = K-IFRS 1108 영업부문 · D831 = K-IFRS 1115 수익 · 804 계열 = 부문 공시 변형
_GEO_XBRL_RE = re.compile(r"D871|D831|D[A-Z]?804")
# 지역표가 들어 있던 절 제목. 코드가 없는 서식(viewer 등)의 2순위.
_GEO_SECTION_RE = re.compile(r"부문|세그먼트|수익|매출|고객과의\s*계약|영업\s*수익|보험위험|지역")


# 「외부고객으로부터의 수익」은 K-IFRS 1108 의 **주요 고객 집중도** 공시지 지리 정보가
# 아니다 — 이것 때문에 「지역표가 있는데 못 읽었다」로 오분류되는 회사가 있다.
_GEO_MARK_RE = re.compile(r"본사\s*소재지|지역\s*합계|지역별\s*정보|지역에\s*대한\s*정보|"
                          r"고객의?\s*소재지\s*국가|소재지별\s*수익")

# ── 「부문 주석 밖에 지역 표지가 있다」 판정을 좁히는 장치 (260802) ─────────────
# 위 표지를 **문서 전체**에서 찾으면 정기보고서가 수백만 자라 엉뚱한 데서 걸린다 —
# 위험관리 주석의 신용위험 익스포져 표(「지역 합계」), 회사 주소(「본사소재지 :」),
# 심지어 「지역별 정보는 **산출하지 않습니다**」는 선언까지 있다는 신호로 읽힌다.
# 절 단위로 제외하면 회계정책 절에 지역을 서술한 진짜까지 죽으므로, **문맥**으로 거른다.

# ① 원문이 스스로 「없다」고 밝힌 경우 — 다른 절을 찾아보라고 안내하면 정반대 답이 된다
_GEO_ABSENT_DECL_RE = re.compile(
    r"지역별?\s*(정보|현황|매출|수익)[^.。\n]{0,20}"
    r"(산출하지\s*않|기재하지\s*않|작성하지\s*않|해당\s*사항\s*없|공시하지\s*않)")
# ② 표지 주변에 매출·부문 문맥이 있어야 지역 **수익** 정보다
_GEO_CTX_RE = re.compile(r"매출|수익|영업부문|부문이익|외부고객|최고(영업)?의사결정자")
# ③ 주소·위험관리·부동산 문맥이면 지역 수익과 무관하다
_GEO_NOISE_RE = re.compile(
    r"소재지\s*[:：]|위험\s*익스포|신용위험|금융자산|임차|전대|㎡|번지|[가-힣]+시\s[가-힣]+구")
_GEO_CTX_WINDOW = 400


def _geo_mark_outside_is_real(full_html: str) -> bool:
    """부문 주석 밖 지역 표지가 **진짜 지역 수익 정보**인지."""
    if _GEO_ABSENT_DECL_RE.search(full_html):
        return False                      # 원문이 없다고 선언했다
    for m in _GEO_MARK_RE.finditer(full_html):
        w = re.sub(r"<[^>]+>", " ",
                   full_html[max(0, m.start() - _GEO_CTX_WINDOW):m.start() + _GEO_CTX_WINDOW])
        if _GEO_CTX_RE.search(w) and not _GEO_NOISE_RE.search(w):
            return True
    return False


def absence_signal(full_html: str) -> dict:
    """미검출이 「진짜 없음」인가 「있는데 못 뽑음」인가 — 원문 신호로 가른다.

    `NOT_COLLECTED` 만 내면 읽는 쪽은 회사가 공시를 안 한 건지 우리가 못 읽은 건지
    알 수 없다. 세 신호로 갈라 밝힌다:

      부문/수익 XBRL 블록 자체가 없음  → 부문 주석 미작성 = **확정적 부재**
      블록은 있는데 지역 표지 없음     → 부문 주석에 지역 정보 없음 = 확정적 부재
      블록에 지역 표지가 있는데 미검출  → **우리 추출 실패**(고칠 대상)
    """
    secs = _note_sections(full_html or "")
    blocks = [s for s in secs if _GEO_XBRL_RE.search(s[0])]
    if not blocks:
        return {"absence_kind": "no_segment_note",
                "absence_detail": "영업부문·수익 주석(K-IFRS 1108/1115) 자체가 없습니다 — "
                                  "단일 부문이라 작성을 생략한 것으로 보입니다. "
                                  "지역별 매출이 공시되지 않은 것이지 파싱 실패가 아닙니다."}
    if any(_GEO_MARK_RE.search(full_html[s:e]) for _k, _t, s, e in blocks):
        return {"absence_kind": "extraction_failed",
                "absence_detail": "부문 주석에 지역 표지가 있는데 표를 읽지 못했습니다 — "
                                  "공시는 되어 있으니 원문을 직접 확인하세요.",
                "absence_sections": [f"{t} [{k}]" for k, t, _s, _e in blocks[:3]]}
    if _geo_mark_outside_is_real(full_html or ""):
        # 「다른 절에 실렸을 수 있다」고 하면 안 된다 — 탐색 창의 마지막이 **문서 전체**라
        # 이 신호가 뜬 시점에 이미 문서를 다 훑고 못 찾은 것이다. 남는 경우는 지역이
        # **표가 아니라 문장**으로만 적힌 것이다(「8개의 주요지역[…]에서 영업하고 있으며」
        # 식 — 금액이 없어 매출 축으로는 쓸 수 없다).
        return {"absence_kind": "outside_segment_note",
                "absence_detail": "지역별 금액이 표로 공시되지 않았습니다 — 원문에 사업 "
                                  "지역이 문장으로만 언급돼 있어 금액을 낼 수 없습니다."}
    return {"absence_kind": "not_disclosed",
            "absence_detail": "부문 주석은 있으나 지역별 정보를 싣지 않았습니다 — "
                              "공시되지 않은 것이지 파싱 실패가 아닙니다."}


def _note_sections(html: str) -> list[tuple[str, str, int, int]]:
    """주석 절 목록 → [(키, 제목, 시작, 끝)]. 없으면 빈 목록.

    주 경로(document.xml)는 XBRL 택소노미 코드를, 폴백(viewer HTML)은 toc 앵커를 쓴다.
    두 원본은 구조 표지가 정반대라 하나만 보면 절을 못 가른다.
    키는 XBRL이면 코드(`NT_C_D871100`), toc면 `toc38` 형태.
    """
    html = html or ""
    ms = list(_XBRL_BLOCK_RE.finditer(html))
    if ms:
        out = []
        for i, m in enumerate(ms):
            end = ms[i + 1].start() if i + 1 < len(ms) else len(html)
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", m.group(2))).strip()
            out.append((m.group(1), title, m.start(), end))
        return out
    ms = list(_TOC_ANCHOR_RE.finditer(html))
    return [(f"toc{m.group(1)}", re.sub(r"\s+", " ", m.group(2)).strip(), m.start(),
             ms[i + 1].start() if i + 1 < len(ms) else len(html))
            for i, m in enumerate(ms)]


def _scan_windows(full_html: str, anchor: str) -> list[tuple[int, int, str]]:
    """검사할 (시작, 끝, 절제목) 창을 **우선순위대로** 만든다.

    좁게 시작해 못 찾으면 넓힌다 — 절로 좁히면 볼 표가 크게 줄고, 전수로 넓혀도 표 파싱은
    DART 콜보다 훨씬 싸다. 예전엔 표 개수에서 끊어 뒤쪽 지역표를 못 봤다.
    """
    secs = _note_sections(full_html)
    wins: list[tuple[int, int, str]] = []
    seen: set[int] = set()

    def _add(s, e, title, key):
        if s in seen:
            return
        seen.add(s)
        wins.append((s, e, f"{title} [{key}]" if key and not key.startswith("toc") else title))

    if secs:
        # ① **XBRL 택소노미 코드** — 제목 변이에 면역이라 1순위.
        #    제목이 여러 갈래(「4. 영업부문」~「주석 - 5. 영업부문정보 - 연결」)여도 코드는 하나다.
        for key, title, s, e in secs:
            if _GEO_XBRL_RE.search(key):
                _add(s, e, title, key)
        # ② 호출측이 준 부문 앵커와 같은 절
        anchor_key = re.sub(r"\s+", "", anchor or "")
        if anchor_key:
            for key, title, s, e in secs:
                if anchor_key in re.sub(r"\s+", "", title):
                    _add(s, e, title, key)
        # ③ 제목 사전 (코드가 없는 서식·viewer 폴백용, 문서 순서 유지)
        for key, title, s, e in secs:
            if _GEO_SECTION_RE.search(title):
                _add(s, e, title, key)
    # ④ 최후 폴백 — 절을 못 가르거나 후보 절에 없을 때 문서 전체
    start = _find_anchor_pos(full_html, anchor) if anchor else -1
    if start < 0:
        start = _find_geo_anchor_pos(full_html)
    wins.append((max(0, start) if start >= 0 else 0, len(full_html), ""))
    return wins


_CAPTION_WINDOW = 600


def _caption_before(html: str, table_start: int) -> str:
    frag = html[max(0, table_start - _CAPTION_WINDOW):table_start]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", frag))


def scan_entity_wide(full_html: str, anchor: str, geo_names: set,
                     product_fallback: bool = False, exclude_names: set | None = None) -> dict:
    # NOTE: 제품·서비스별(product) 분류는 오분류율이 높아 v2로 이연 —
    # 현재는 지역별(geo)만 반환한다. product_fallback/exclude_names 파라미터는 v2 재사용 예정.
    """부문 주석 구간에서 지역별/제품별 수익 표를 격자로 판독.

    분류: ① 이름 과반이 지역명 → geo ② 캡션에 제품·수익유형 신호(지역 신호 없음) → product.
    게이트: 항목 ≥2 + 합계 열 존재 + 부문합≈총계(±0.5%) — 검산 못 한 표는 반환하지 않는다.
    반환 {"geo": {...}|None, "product": {...}|None}.
    """
    out = {"geo": None, "product": None}
    if not full_html:
        return out
    # 좁은 창(후보 절) → 넓은 창(문서 전체) 순서로 훑는다. 창 안에서만 표를 세므로
    # 비용이 절 단위로 묶이고, 절 경계가 확정돼 「통으로 리턴」의 범위도 정확해진다.
    geo_md_fallback = None
    for win_start, win_end, win_title in _scan_windows(full_html, anchor):
        got = _scan_window(full_html, win_start, win_end, win_title, anchor,
                           geo_names, out, geo_md_fallback)
        # 뒤 창이 폴백을 못 찾아 None 을 돌려주면 **앞 창에서 잡은 폴백이 지워졌다** —
        # 단위 미상으로 강등된 원문 표가 있는데도 NOT_COLLECTED 로 나가는 일이 있었다.
        geo_md_fallback = got or geo_md_fallback
        if out["geo"] is not None:
            break
    if out["geo"] is None and geo_md_fallback is not None:
        out["geo"] = geo_md_fallback          # 표준 사다리 2단: 정형 실패 → 원문 마크다운
    if out["geo"] is not None:
        _mark_basis(out["geo"], full_html)
        _mark_attribution(out["geo"], full_html)
    return out


# 귀속기준 **값**의 문형. 라벨(「…에 대한 기술」)과 구분해야 한다 — DART 표는 라벨행과
# 값행의 열 인덱스가 어긋나(ragged) 라벨→값 매핑이 안 되므로, 값 자체를 문형으로 찾는다.
_ATTR_VALUE_RE = re.compile(
    r"(귀속시켰|귀속하였|귀속됩니다|귀속되어|기초하여\s*구분|소재지에\s*기초|"
    r"소재지\s*기준|소재지를?\s*기준|인도되는\s*국가|제공되는\s*국가)")
_ATTR_LABEL_TAIL_RE = re.compile(r"대한\s*기술\s*$")
_ATTR_TAG_RE = re.compile(r"<[^>]+>")


def _attr_prefilter_text(table_html: str) -> str:
    """표 원문을 **셀 텍스트와 같은 모양**으로 싸게 편다 — 재조립 없이.

    셀 쪽은 `get_text(" ", strip=True)` 뒤 공백을 한 칸으로 접는다. 여기도 태그를
    공백으로 바꾸고 엔티티를 풀고 공백을 접어야 「셀에서 잡히는 건 여기서도
    잡힌다」가 성립한다.
    """
    return re.sub(r"\s+", " ", _html.unescape(_ATTR_TAG_RE.sub(" ", table_html)))


def _mark_attribution(geo: dict, full_html: str) -> None:
    """지역 매출을 **무슨 기준으로** 나라에 배분했는지 원문에서 뽑는다(in place).

    종전엔 출력 라벨에 「고객 소재지」가 하드코딩돼 있었는데, K-IFRS 1108 ¶33 이 요구하는
    귀속기준을 실제로 공시하는 회사는 드물고, 그나마도 「고객 소재지」만 있는 게 아니다 —
    「**사업장** 소재지 기준」도 있다. 못박으면 공시하지 않은 대다수에서 거짓이 된다.

    공시했으면 그 문장을 그대로 싣고, 없으면 `attribution_basis` 를 비워 둔다(렌더가
    「귀속기준 미공시」로 밝힌다). 지어내지 않는 것이 요점이다.
    """
    b = (geo.get("source_location") or {}).get("section_bounds")
    if not b:
        return
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return
    for m in _TABLE_RE.finditer(full_html, b[0]):
        if m.start() >= b[1]:
            break
        # **글자로 먼저 거른다.** 표를 객체로 재조립하는 건 이 파일에서 제일 비싼 일인데,
        #   찾는 건 문장 하나다. 표 원문에 그 문장의 흔적조차 없으면 재조립해도 없다.
        #   같은 수법이 `segment_candidates.find_segment_candidates` 에 이미 있다.
        #   실측(사용자가 조회한 120사, 표 2,536개): 통과 3개 — 재조립 2,536회 → 3회.
        #   ⚠️ 셀 텍스트와 **같은 정규화**를 걸어야 필요조건이 성립한다. 엔티티를 안 풀면
        #      `고객&nbsp;소재지` 를, 공백을 안 접으면 줄바꿈이 낀 문장을 놓친다 —
        #      그러면 이건 「값싼 사전 검사」가 아니라 조용한 회귀다.
        if not _ATTR_VALUE_RE.search(_attr_prefilter_text(m.group(0))):
            continue
        try:
            tb = BeautifulSoup(m.group(0), "lxml").find("table")
        except Exception:
            continue
        if tb is None:
            continue
        for c in tb.find_all(_CELL_TAGS):
            s = re.sub(r"\s+", " ", c.get_text(" ", strip=True))
            if 10 < len(s) < 200 and _ATTR_VALUE_RE.search(s) \
                    and not _ATTR_LABEL_TAIL_RE.search(s):
                geo["attribution_basis"] = s
                return


def _mark_basis(geo: dict, full_html: str) -> None:
    """이 지역표가 **연결인지 별도인지** 밝힌다(in place).

    종전엔 출력 라벨에 「연결 기준」이 하드코딩돼 있어, 별도 절을 읽고도 연결이라고
    말하는 경우가 있었다. 창 순서는 이미 연결이 앞인데도 그렇게 되는 이유는,
    연결 절이 정형에 실패하면 루프가 계속 돌아 **뒤의 별도 절이 정형에 성공하면 그게
    채택**되기 때문이다. 순서를 바꿔도 안 고쳐지고, 값을 버리면 후퇴다.
    그래서 값은 그대로 두되 **무엇을 읽었는지 밝히고, 연결이 있는데 별도를 읽었으면 알린다**.

    판별은 XBRL 코드(NT_C=연결 / NT_S=별도)로 한다 — 코드와 절 제목이 어긋나는 일이 없어
    코드를 신뢰해도 안전하다. 코드가 없는 서식(viewer 폴백 등)은 지어내지 않고 「미상」으로 둔다.
    """
    loc = geo.get("source_location") or {}
    m = re.search(r"\[(NT_[CS]_[^\]]+)\]", loc.get("note_section") or "")
    code = m.group(1) if m else ""
    geo["basis"] = ("연결" if code.startswith("NT_C")
                    else "별도" if code.startswith("NT_S") else "미상")
    if geo["basis"] != "별도":
        return
    codes = {k for k, _t, _s, _e in _note_sections(full_html)}
    if any(k.startswith("NT_C") and _GEO_XBRL_RE.search(k) for k in codes):
        geo["basis_conflict"] = (
            "이 표는 **별도** 재무제표 주석에서 읽었습니다. 같은 보고서에 연결 기준 지역 "
            "정보도 있으나 표를 읽지 못했습니다 — 연결 기준 값이 필요하면 원문을 직접 "
            "확인하세요.")


def _scan_window(full_html: str, pos: int, win_end: int, win_title: str, anchor: str,
                 geo_names: set, out: dict, geo_md_fallback):
    """창 하나를 훑어 out["geo"]를 채운다. 강등 폴백(마크다운)을 돌려준다."""
    attempts = 0
    for m in _TABLE_RE.finditer(full_html, pos):
        # 창 안에서만 센다 — 절로 좁히면 볼 표가 크게 준다.
        # 150,000자 제한은 앵커 하나로 훑던 시절의 잔재다 — 창 경계(win_end)가 있으면
        # 그것이 범위다. 부문 절이 수백 KB 인 회사는 이 제한에 걸려 지역표에 닿지 못했다.
        # 창이 없을 때(문서 전체 폴백)만 옛 제한을 유지한다.
        limit = win_end if win_title else min(win_end, pos + 150000)
        if attempts >= _EW_MAX_ATTEMPTS or m.start() >= limit:
            break
        chunk = m.group(0)
        # 길이 하한은 잡표를 거르는 장치인데 **entity-wide 지역표는 원래 작다** —
        # 데이터가 한 행(수익(매출액))뿐이라서다. 1500자 하한에 걸려 아예 읽히지 않는
        # 표가 있었다(파싱 자체는 정상이었다).
        # 지역 머리를 가진 표만 하한을 낮춘다.
        if len(chunk) < (_EW_GEO_MIN_CHARS if _GEO_HEAD_RE.search(chunk) else 1500):
            continue
        if not _EW_CANDIDATE_RE.search(chunk[:4000]):
            continue
        attempts += 1
        p = _parse_table(m.group(0))
        # `_GEO_HEAD_RE` 는 표 **전체**에서 찾는다. 종전엔 `chunk[:4000]` 만 봤는데,
        # 조정 행까지 실은 큰 표는 지역 머리가 뒤에 온다 — 앞부분만 보면 못 찾고, 행 지향
        # 리더가 아예 안 불려 완전 공시된 표를 통째로 버린다. 정규식 어휘 자체가 특이해서
        # (본사 소재지·외국·북미·지역 합계) 범위를 넓혀도 오탐이 없다.
        if (not p or len(p["segments"]) < 2) and _GEO_HEAD_RE.search(chunk):
            # 부문표 파서는 **열 지향**(지역이 컬럼)만 읽는다. 지역이 행에 오는 서식
            # (「지역 | 한국 | 총부문수익 | 비유동자산」)에서는 항목이 0개로
            # 나와 표를 통째로 버렸다 — 앵커는 맞았는데 표에서 걸린 것.
            p = _read_row_oriented_geo(chunk) or _read_column_oriented_geo(chunk)
        if p and _GEO_HEAD_RE.search(chunk) and not any(
                REGION.match(_region_key(s0["name"])) for s0 in p["segments"]):
            # 부문 리프를 잡았지만 머리 위층이 지역인 표 — 조흥은 「지역 > 본사 소재지 국가
            # > 부문(치즈·식품첨가물)」 3층이라 리프(부문)를 뽑고 지역표가 아니라고 봤다.
            # 지역으로 보면 전량 국내라는 확정 정보다.
            p = _read_single_region_with_subaxis(chunk) or p
        if p and len(p["segments"]) == 1 and _GEO_HEAD_RE.search(chunk):
            # 지역이 **하나뿐**인 표(동우팜투테이블 「본사 소재지 국가 | 325,458」)는
            # 정보가 없는 게 아니라 **「전량 국내」라는 확정 정보**다. 2개 이상 게이트에
            # 걸려 통째로 버려지고 있었다 — 해외비중 0%를 낼 수 있는 케이스다.
            if _DOMESTIC_RE.match(_region_key(p["segments"][0]["name"])):
                p = {**p, "excess": [p["segments"][0]["revenue"]], "_single_region": True}
        if not p or len(p["segments"]) < 1:
            continue
        if len(p["segments"]) < 2 and not p.get("_single_region"):
            continue
        # ── 분류 먼저: 지역표인가 (게이트 탈락해도 지역표면 원문 폴백 대상) ──
        names_norm = [re.sub(r"[\s()]", "", s["name"]) for s in p["segments"]]
        # 호출측이 준 `geo_names` 는 좁다(「본사 소재지 국가」·「북미」·「아시아」가 없다) —
        # 지역을 정확히 뽑고도 「지역표가 아니다」로 분류돼 버려지는 회사가 있었다.
        # 모듈 안의 넓은 지역 정규식을 함께 본다.
        geo_cnt = sum(1 for n in names_norm
                      if n in geo_names or REGION.match(_region_key(n)))
        caption = _caption_before(full_html, m.start())
        is_geo = geo_cnt >= max(2, (len(names_norm) + 1) // 2) or (
            geo_cnt >= 1 and bool(_GEO_CAPTION_RE.search(caption))) or bool(p.get("_single_region"))
        is_product = False    # v2로 이연 (위 NOTE)

        # ── 정형 게이트 (표준 계약: 앵커 → 정형+검산 → 원문 마크다운 → 명시적 부재) ──
        fail = ""
        if not p["excess"] and is_geo and _all_region_names(names_norm, geo_names):
            # 「외국 | 본사 소재지 국가」 두 칸만 있고 합계 열이 없는 서식이 있다
            # 항목이 **전부 지역명**이면 빠짐없이 나열된 것으로 보고
            # 항목합을 총계로 쓴다 — 이때 검산은 못 했다고 밝힌다.
            p = {**p, "excess": [sum(s0["revenue"] for s0 in p["segments"])],
                 "_total_derived": True}
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
        # 수익 '구성' 행(부분값)·내부거래 포함 총액 행은 매출을 크게 왜곡한다.
        # 합계행 재선택은 v2.
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
            # 원문을 직접 찾아보게 — 어느 단원의 어느 절, 어떤 표인지 (rcept_no는 호출측이 붙인다)
            "source_location": {
                "chapter": "III. 재무에 관한 사항 — 재무제표 주석",
                # 절 제목 뒤 `[NT_C_D871100]` 는 XBRL 택소노미 코드 — 회사마다 절 번호·제목이
                # 달라도 이 코드는 같으므로, 다른 회사 원문을 찾을 때 그대로 쓸 수 있다.
                "note_section": (win_title or anchor or "지역 정보 표(주석 절 미확정)"),
                "section_bounds": [pos, win_end] if win_title else None,
                "table_caption": clean_caption[-80:],
                "how_to_find": "DART 원문에서 위 주석 절을 찾아 표 캡션으로 대조하세요."
                               " 대괄호 안은 XBRL 코드(D871=영업부문·D831=수익).",
            },
        }
        if p.get("_total_derived"):
            payload["reconciliation"] = "합계 열 없음 — 항목이 모두 지역명이라 항목합을 총계로 사용(검산 못 함)"
            payload["extraction_status"] = "SUCCESS_NO_TOTAL_COLUMN"
        if is_geo:
            payload.update(_foreign_share(payload["items"]))
        if p.get("_single_region"):
            payload["reconciliation"] = "지역이 하나(본사 소재지 국가)뿐 — 전량 국내 매출"
            payload["note"] = ("표에 지역이 하나만 있습니다 — 해외 매출이 0이라는 뜻입니다"
                               "(정보가 없는 것이 아닙니다).")
        # 비유동자산 지역별이 같은 표에 있으면 함께 — 「수출형 vs 현지생산형」 판별자다.
        # 해외 수익이 큰데 해외 자산이 0이면 수출형, 자산도 크면 현지 생산·판매다.
        if p.get("_assets_by_region"):
            payload["assets_by_region"] = p["_assets_by_region"]
            payload["assets_note"] = ("비유동자산 지역별 — 해외 수익이 큰데 해외 자산이 0이면 "
                                      "수출형, 자산도 크면 현지 생산·판매입니다(K-IFRS 1108).")
        if is_geo and out["geo"] is None:
            out["geo"] = payload
        elif is_product and out["product"] is None:
            out["product"] = payload
        if out["geo"] and out["product"]:
            break
    return geo_md_fallback
