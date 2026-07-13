"""executive_pay — 사업보고서 VIII. 임원 및 직원 등에 관한 사항 › 2. 임원의 보수 등 원문 파서.

director_board의 정형 API scope(compensation/individual)는 **금액·인원**만 준다. 이 모듈은 정형
API가 제공하지 않는 **보수 산정기준 서술**을 원문(document.xml)에서 구조화한다:
  - 블록 A `보수지급기준`(정책): 등기이사/사외이사/감사위원 버킷별 급여·상여·단기/장기성과급 산식.
    (KT&G: 단기 0~280%(사장)/0~165%(사내), 장기 0~600%, RSU 3년 이연 등)
  - 블록 B `산정기준 및 방법`(개인별): 실명 임원의 급여/상여/주식보상 분해 + KPI(계량·비계량) 서술.
    (POSCO: 영업이익 15%·매출 15%·ROA 10%·주가 15%·ESG 10% 등 가중치 실명 공개)

설계 원칙(260713, 3사 실물 검증): **제목·순서 매칭 금지.** 4축 구조로만 판별한다.
  ① `<...>` 꺾쇠 그룹제목(1행1셀) → 대블록 경계 (KT&G/삼성/POSCO 공통·안정)
  ② 표 헤더 컬럼 시그니처(정규화 집합 부분매칭) → 표 종류. 제목("다."/"(3)"/"3.")은 회사마다 달라 신뢰 불가.
  ③ rowspan/colspan 그리드 정규화 → 위치가 아닌 좌표로 셀 접근(방경만 rowspan6·근로소득 rowspan4).
  ④ 표별 `(단위 : 백만원)` 선언 → 금액 스케일(×10^6). 주식보상표는 `주`.
보수적: 시그니처 불일치 표는 버린다(억지 매칭 금지 — 삼성 PSU표를 pay로 오분류하지 않음).
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup


# ── 정규화 ──────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """공백 제거 + 전각콜론 정규화 — 헤더 시그니처 비교용."""
    return re.sub(r"\s+", "", (s or "")).replace("：", ":")


def _celltext(cell) -> str:
    return re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()


def _match_name(name: str) -> str:
    """이름 매칭 키: 후행 각주마커(*, **, (주1), 1) 등)와 공백 제거. 개인별 보수지급금액 표는
    '이주태*'처럼 마커를 붙이고 산정기준 표는 '이주태'로 쓰는 경우가 있어(POSCO 실측) 그대로
    매칭하면 자기일치 대조가 깨진다. 표시용 이름은 원문 유지, 매칭에만 이 키를 쓴다."""
    s = re.sub(r"\s+", "", name or "")
    s = re.sub(r"[\*※†]+$", "", s)                 # 후행 별표/기호 마커
    s = re.sub(r"\(?\s*주\s*\d+\s*\)?$", "", s)     # 후행 (주N)/주N
    return s


def _clean_person_name(raw: str, clean_names: set[str]) -> str:
    """직위가 이름 셀에 병합된 경우('대표이사한종희'·'홍원학대표이사'·'부회장김정남') 실명만 복원.

    구조-우선(260713 실측 삼성전자·삼성생명·DB손해보험): 산정기준 표(indiv_breakdown)는 회사에
    따라 이름 셀에 직위를 함께 적는다(헤더는 '이름'인데 값은 '직위+이름'). 반면 **같은 문서**의
    개인별 보수지급금액 표(indiv_total)는 이름/직위를 별도 컬럼으로 깨끗이 분리한다 — 이 문서 자체가
    제공하는 실명 집합(clean_names)을 ground truth로 써서 병합 셀에서 실명을 부분매칭으로 추출한다.
    직위 토큰을 키워드로 지우는 추측이 아니라, 문서의 다른 표가 확인해 준 실명만 채택(보수적).

    병합이 아니거나(이미 실명) 문서에 대응 실명이 없으면 원문 유지 — 함부로 자르지 않는다.
    이 silent 병합은 정형 API 하이브리드 교차검증이 적발한다(자기일치만으론 0/0으로 조용히 통과)."""
    r = re.sub(r"\s+", "", raw or "")
    if not r or r in clean_names:
        return raw.strip()
    cands = [cn for cn in clean_names if len(cn) >= 2 and cn in r and cn != r]
    if cands:
        return max(cands, key=len)   # 가장 긴 실명(부분 성씨 오매칭 방지)
    return raw.strip()


def _grid(table) -> list[list[str]]:
    """rowspan/colspan을 전개해 2D 행렬 반환. 스팬된 셀은 아래/오른쪽 좌표에 같은 값을 채운다.
    naive tr별 셀 읽기는 방경만(rowspan6)·근로소득(rowspan4)이 다음 행에서 사라져 컬럼이 밀린다 —
    그리드 정규화 없이 위치로 읽으면 상여=839가 엉뚱한 사람/대분류에 붙는다(CLAUDE.md 위치기반 금지)."""
    rows = table.find_all("tr")
    occ: dict[tuple[int, int], str] = {}
    for ri, tr in enumerate(rows):
        ci = 0
        # DART document.xml은 데이터 셀에 표준 <td>와 자체 태그 <te>(table entry)를 혼용한다
        # (개인별 보수지급금액 표=<te>, 산정기준 표=<td> — 같은 문서 안에서도 다름). 헤더는 <th>/<tu>.
        # <te>/<tu>를 빠뜨리면 데이터 행이 통째로 빈칸이 된다(260713 실측: 개인별 총액 표).
        for c in tr.find_all(["td", "th", "te", "tu"]):
            while (ri, ci) in occ:
                ci += 1
            try:
                rs = int(c.get("rowspan") or 1)
            except (ValueError, TypeError):
                rs = 1
            try:
                cs = int(c.get("colspan") or 1)
            except (ValueError, TypeError):
                cs = 1
            txt = _celltext(c)
            for dr in range(max(rs, 1)):
                for dc in range(max(cs, 1)):
                    occ[(ri + dr, ci + dc)] = txt
            ci += max(cs, 1)
    ncol = max((c for _, c in occ), default=-1) + 1
    return [[occ.get((ri, ci), "") for ci in range(ncol)] for ri in range(len(rows))]


# ── 단위 ────────────────────────────────────────────────────────────────────

def _unit_scale(unit_decl: str) -> tuple[str, int]:
    """'(단위 : 백만원)' → ('KRW', 1_000_000). 표별로 선언되니 전역 가정 금지."""
    u = _norm(unit_decl)
    if "백만원" in u:
        return ("KRW", 1_000_000)
    if "천원" in u:
        return ("KRW", 1_000)
    if "억원" in u:
        return ("KRW", 100_000_000)
    if "원" in u:
        return ("KRW", 1)
    if "주" in u:
        return ("shares", 1)
    return ("unknown", 1)


_UNIT_TEXT_RE = re.compile(r"단위\s*[:：]\s*([가-힣A-Za-z]+)")


def _unit_for_table(table) -> tuple[str, int]:
    """표 바로 앞(문서 순서)의 '(단위 : XXX)' 선언을 위치-지역적으로 읽어 스케일 결정.

    cur_unit을 표들 사이로 carry하면 앞선 무관한 표(예: 자기주식 '(단위:주)')의 단위가
    보수 표로 누수돼 ×10^6이 안 먹는 사고가 난다(260713 실측 DB손해보험: 급여 387백만원을
    387원으로 처리). 각 표의 최근접 선행 '단위:' 텍스트만 보면 누수가 없다."""
    node = table.find_previous(string=_UNIT_TEXT_RE)
    if node:
        m = _UNIT_TEXT_RE.search(str(node))
        if m:
            return _unit_scale(m.group(1))
    return ("unknown", 1)


_GROUP_TOP5 = "상위5명(미등기·직원 포함)"
_GROUP_DIR = "5억원이상 이사·감사"
_GROUP_RE = re.compile(r"상위\s*5\s*명|5억원?\s*이상")


def _group_for_table(table) -> str | None:
    """표 바로 앞의 그룹 제목을 위치-지역적으로 판정. cur_group carry가 실패하면(제목이 <...>
    미니표가 아니라 <p>거나 서식이 달라 미검출) 두 블록('5억+ 이사·감사' vs '상위5명')이 같은
    (group,name) 키로 병합돼 컴포넌트가 2배로 중복된다(영원무역·코미코 실측). 최근접 선행
    '상위 5명' / '5억원 이상' 텍스트로 블록을 직접 구분한다('상위'가 있으면 top5)."""
    node = table.find_previous(string=_GROUP_RE)
    if node:
        s = str(node)
        if "상위" in s:
            return _GROUP_TOP5
        if "5억" in s:
            return _GROUP_DIR
    return None


_KOR_NUM_UNIT_RE = re.compile(r"[조억만천백십]")


def _korean_amount(s: str) -> int:
    """한글 수사 금액을 원 단위 정수로. '6억7백만'→607,000,000, '9억9천2백만'→992,000,000.

    자릿단위(조/억/만)는 구간 경계, 천/백/십은 구간 내 자리. 왼쪽→오른쪽으로 current(직전 숫자)를
    자리단위에 곱해 section에 쌓고, 억/만을 만나면 section을 해당 배율로 total에 확정한다."""
    total = section = current = 0
    for ch in s:
        if ch.isdigit():
            current = current * 10 + int(ch)
        elif ch == "조":
            section += current; total += section * 10**12; section = current = 0
        elif ch == "억":
            section += current; total += section * 10**8; section = current = 0
        elif ch == "만":
            section += current; total += section * 10**4; section = current = 0
        elif ch == "천":
            section += (current or 1) * 1000; current = 0
        elif ch == "백":
            section += (current or 1) * 100; current = 0
        elif ch == "십":
            section += (current or 1) * 10; current = 0
    return total + section + current


def _amount(raw: str, scale: int) -> int | None:
    """'624'·'1,037'·'5,951.0'·'6억7백만원'·'(500)'·'△50'·'-'·'해당없음' → 원 단위 정수. 미기재 None.

    **소수점 필수 처리(260713 실측)**: 회사가 산정기준 금액을 '5,951.0'처럼 백만원 소수 1자리로
    적으면(풍산·셀트리온·HD건설기계·에스엘), 콤마·비숫자를 다 지우고 정수화하면 '59510'이 돼
    ×10 과대(59.5억→595억)가 난다. 콤마(천단위)만 제거하고 소수점은 살려 float로 파싱 후 스케일.

    **한글 수사 필수 처리(260713 실측 삼성생명)**: 별도 (단위:) 선언 없이 '6억7백만원'처럼 억/천/
    백/만/원으로 금액을 자기서술하는 회사가 있다. 아라비아 자릿수만 뽑으면 '6억7백만'→'67'로
    자릿수가 무너져 607백만원을 67백만원으로 10배 축소한다(이승호 14.6억→2.0억 오독). 총액 셀에
    억/조/만/천/백/십이 있으면 표 단위(scale) 무시하고 한글수사를 원 단위로 직접 해석한다(자기서술).
    """
    s = (raw or "").strip()
    if s in ("", "-", "해당없음", "해당 없음", "미해당"):
        return None
    neg = (s.startswith("(") and s.endswith(")")) or (s[:1] in "△▲-▵")
    body_kr = s.replace(",", "").replace(" ", "")
    if _KOR_NUM_UNIT_RE.search(body_kr):
        v = _korean_amount(body_kr)
        if not v:
            return None
        return -v if neg else v
    body = re.sub(r"[^\d.]", "", s.replace(",", ""))   # 콤마 제거, 소수점은 보존
    body = re.sub(r"\.(?=.*\.)", "", body)             # 점이 여러 개면 마지막만 소수점으로
    if not body or body == ".":
        return None
    try:
        v = round(float(body) * scale)
    except ValueError:
        return None
    return -v if neg else v


# ── 헤더 시그니처 → 표 종류 (보수적 부분매칭) ─────────────────────────────────

# 각 종류의 필수 컬럼 집합(정규화). 부분집합이면 매칭. 순서·제목 무관.
_SIGNATURES: list[tuple[str, set[str]]] = [
    ("agg_limit",        {"구분", "인원수", "주주총회승인금액"}),
    ("agg_paid_by_type", {"구분", "인원수", "보수총액", "1인당평균보수액"}),
    ("agg_paid_total",   {"인원수", "보수총액", "1인당평균보수액"}),
    ("policy",           {"구분", "보수지급기준"}),
    ("indiv_total",      {"이름", "직위", "보수총액"}),
    ("indiv_breakdown",  {"이름", "보수의종류", "총액", "산정기준및방법"}),
]


def _classify(header_row: list[str]) -> str:
    hs = {_norm(h) for h in header_row if h}
    for name, need in _SIGNATURES:
        if need <= hs:
            return name
    return "unknown"


# 성과급 배수/비율 탐지(보수적) — 산식 서술에서 '0~280%', '0%~600%', '기본급의 165%' 류만.
_RANGE_RE = re.compile(r"\d{1,4}\s*(?:~|∼|-|—|%\s*~)\s*\d{1,4}\s*%|\d{1,4}\s*%")


def _detect_ranges(text: str) -> list[str]:
    """서술에서 성과급 배수/비율 토큰을 원문 그대로 수집(구조화 X — 회사별 편차 커 raw 보존)."""
    seen: list[str] = []
    for m in _RANGE_RE.finditer(text or ""):
        tok = re.sub(r"\s+", "", m.group(0))
        if tok not in seen:
            seen.append(tok)
    return seen[:12]


# ── VIII-2 섹션 슬라이스 (perf: 8MB 전체 파싱 회피) ──────────────────────────

_SECTION_START = "이사ㆍ감사 전체의 보수현황"   # 블록 A 그룹제목의 핵심 문자열(3사 공통)
_SECTION_MAXLEN = 900_000                        # 넉넉한 상한(원문 XML 태그 포함 여유)


def _slice_section(html: str) -> str:
    """VIII-2 원문 구간만 잘라 반환(perf). 못 찾으면 전체 반환(정확성은 시그니처가 보장하므로 안전).
    앵커는 목차가 아니라 실제 표가 뒤따르는 위치를 고르기 위해 '<' 근처 우선 탐색."""
    if not html:
        return html
    # 그룹제목 형태 '<...전체의 보수현황>'을 우선, 없으면 평문 앵커
    idx = html.find("&lt;" + _SECTION_START)
    if idx == -1:
        idx = html.find("<" + _SECTION_START)
    if idx == -1:
        idx = html.find(_SECTION_START)
    if idx == -1:
        return html
    # 앵커 앞으로 살짝(직전 그룹 컨텍스트) + 뒤로 상한만큼
    start = max(0, idx - 200)
    return html[start: start + _SECTION_MAXLEN]


# ── 메인 파서 ────────────────────────────────────────────────────────────────

def parse_executive_pay(html: str, text: str = "") -> dict[str, Any]:
    """VIII-2 원문(html)에서 보수지급기준(정책) + 개인별 산정기준(분해)을 구조화.

    반환:
      {
        "pay_policy": [{group, criteria, ranges}],          # 블록 A 정책표
        "policy_narrative": str|None,                        # 표 없을 때 서술형 폴백(POSCO)
        "individuals": [{group, name, total_krw, components:[{category, pay_type, amount_krw, basis, ranges}]}],
        "aggregate_seen": [종류...],                          # 정형 API와 중복되나 존재 확인용
        "unknown_tables": int,                               # 시그니처 불일치(주식보상 등) — 보류
      }
    """
    fragment = _slice_section(html)
    soup = BeautifulSoup(fragment, "lxml")
    tables = soup.find_all("table")

    cur_group: str | None = None
    cur_unit: str | None = None

    policy: list[dict[str, Any]] = []
    policy_narrative: str | None = None
    # 개인은 (group, name) 단위로 components를 모은다.
    people: dict[tuple[str | None, str], dict[str, Any]] = {}
    indiv_totals: list[dict[str, Any]] = []   # 개인별 보수지급금액 표의 공식 총액(자기일치 대조용)
    aggregate_seen: list[str] = []
    unknown = 0

    # 그리드는 한 번만 계산(perf) + indiv_total 표에서 깨끗한 실명 집합을 선(先)수집해 산정기준
    # 표의 직위-병합 이름(삼성전자 '대표이사한종희')을 복원하는 ground truth로 쓴다(260713).
    grids: list[tuple[Any, list[list[str]]]] = []
    for t in tables:
        mat = _grid(t)
        if mat:
            grids.append((t, mat))

    clean_names: set[str] = set()
    for t, mat in grids:
        if _classify(mat[0]) == "indiv_total":
            hdr = {_norm(h): i for i, h in enumerate(mat[0]) if h}
            i_name = hdr.get("이름")
            if i_name is not None:
                for r in mat[1:]:
                    if i_name < len(r):
                        nm = r[i_name].strip()
                        if nm and _norm(nm) not in ("이름", "-", "합계", "계"):
                            clean_names.add(re.sub(r"\s+", "", nm))

    for t, mat in grids:
        first_nonempty = [c for c in mat[0] if c]

        # 1행 & 값 1~2개 미니표 → 내용 접두로 분류(위치 아님)
        if len(mat) == 1 and len(first_nonempty) <= 2:
            cell = " ".join(first_nonempty)
            n = _norm(cell)
            if n.startswith("(단위") or "단위:" in n or "기준일:" in n:
                cur_unit = cell
            elif cell.startswith("<") or cell.startswith("&lt;"):
                cur_group = cell.strip("<> ").replace("&lt;", "").replace("&gt;", "").strip()
            # '※' 각주 등은 무시
            continue

        kind = _classify(mat[0])
        # 단위·그룹 둘 다 carry가 아니라 표 바로 앞 선언에서 위치-지역적으로(누수/병합 방지, 260713).
        unit_name, scale = _unit_for_table(t)
        tbl_group = _group_for_table(t) or cur_group

        if kind == "policy":
            for r in mat[1:]:
                if len(r) >= 2 and r[0] and _norm(r[0]) != "구분":
                    crit = r[1]
                    policy.append({"group": r[0], "criteria": crit, "ranges": _detect_ranges(crit)})

        elif kind == "indiv_breakdown":
            # 컬럼: [이름, (대분류), 세부종류, 총액, 산정기준] — 대분류 유무로 4~5열.
            # rowspan 주의(260713 두산에너빌리티): 산정기준 텍스트가 여러 줄이면 총액 셀이 rowspan으로
            # 그 줄 수만큼 반복돼(그리드 전개), 같은 (인물,보수종류,금액)이 N행으로 나온다. 그대로
            # 컴포넌트로 쌓으면 상여가 4번 카운트돼 total이 3배로 뻥튀김. → 인물별 보수종류로 dedup:
            # 금액은 1회만, 산정기준은 병합, 배수는 union.
            for r in mat[1:]:
                if len(r) < 4 or _norm(r[0]) == "이름":
                    continue
                # 직위-병합 이름 복원(삼성전자류): 같은 문서 indiv_total의 실명 집합으로 부분매칭.
                name = _clean_person_name(r[0], clean_names)
                if not name or name in ("-", "합계", "계"):   # placeholder/총계행 제외(ghost 방지)
                    continue
                category = r[1] if len(r) >= 5 else None    # 근로소득/퇴직소득/기타소득
                pay_type = r[-3]                             # 급여/상여/주식매수선택권행사이익/...
                # '보수총액'/'합계' 행-총계를 컴포넌트로 흡수하면 total이 2배(HD현대일렉트릭·HD한국조선해양
                # 실측). 개별 보수종류가 아니라 그 인물의 소계행이므로 제외.
                if _norm(pay_type) in ("보수총액", "합계", "계", "소계", "총계"):
                    continue
                amount = _amount(r[-2], scale)
                basis = r[-1].strip()
                key = (tbl_group, name)
                person = people.setdefault(key, {
                    "group": tbl_group, "name": name, "components": [], "_by_type": {},
                })
                bt = person["_by_type"]
                if pay_type in bt:
                    # rowspan 연속행(같은 보수종류 반복) — 금액 재계상 금지, 산정기준만 병합.
                    comp = bt[pay_type]
                    if basis and basis not in (comp["basis"] or ""):
                        comp["basis"] = (comp["basis"] + " " + basis).strip()[:600]
                    for rg in _detect_ranges(basis):
                        if rg not in comp["ranges"]:
                            comp["ranges"].append(rg)
                    if comp["amount_krw"] is None and amount is not None:
                        comp["amount_krw"] = amount
                else:
                    comp = {"category": category, "pay_type": pay_type,
                            "amount_krw": amount, "basis": basis[:600], "ranges": _detect_ranges(basis)}
                    bt[pay_type] = comp
                    person["components"].append(comp)

        elif kind == "indiv_total":
            # 개인별 보수지급금액(이름·직위·보수총액) — 공식 총액. Σ산정기준 컴포넌트와 대조할
            # ground truth(같은 원문 내 독립 표라 파서 자기일치 검증에 씀). 컬럼은 헤더 라벨로 매핑(위치 X).
            if "indiv_total" not in aggregate_seen:
                aggregate_seen.append("indiv_total")
            hdr = {_norm(h): i for i, h in enumerate(mat[0]) if h}
            i_name = hdr.get("이름")
            i_total = hdr.get("보수총액")
            i_pos = hdr.get("직위")
            if i_name is not None and i_total is not None:
                for r in mat[1:]:
                    if i_name < len(r) and r[i_name] and _norm(r[i_name]) not in ("이름", "-", "합계", "계"):
                        indiv_totals.append({
                            "group": tbl_group, "name": r[i_name].strip(),
                            "position": r[i_pos].strip() if (i_pos is not None and i_pos < len(r)) else None,
                            "official_total_krw": _amount(r[i_total], scale) if i_total < len(r) else None,
                        })

        elif kind in ("agg_limit", "agg_paid_total", "agg_paid_by_type"):
            if kind not in aggregate_seen:
                aggregate_seen.append(kind)

        else:
            unknown += 1

    # 정책표가 없으면 서술형 폴백(POSCO: '이사/감사의 보수지급기준'이 표가 아닌 <p> 한 줄)
    if not policy and text:
        m = re.search(r"보수지급기준\s*\n+([^\n]{10,300})", text)
        if m:
            cand = m.group(1).strip()
            # 표 헤더/그룹제목 조각이 아닌 서술문만
            if "보수총액" not in cand and not cand.startswith("<"):
                policy_narrative = cand

    # 자기일치 대조: 개인별 산정기준 Σ컴포넌트 vs 개인별 보수지급금액 표의 공식 총액(같은 원문 내
    # 독립 표). (group, name)으로 매칭해 official_total_krw·total_consistent 부여. 불일치 = 파서가
    # 컴포넌트를 놓쳤거나 단위가 틀린 신호(무인 검증). 공식 표에만 있고 산정기준 표엔 없는 인물도 있어
    # (매칭 실패는 불일치 아님, official만 부여).
    official_by_key = {(t["group"], _match_name(t["name"])): t.get("official_total_krw") for t in indiv_totals}
    individuals = list(people.values())
    consistent_n = 0
    checkable_n = 0
    for p in individuals:
        p.pop("_by_type", None)  # 파싱용 임시 인덱스 제거
        # total은 dedup된 컴포넌트 합에서 산출(증분 누적 아님 — rowspan 중복이 total에 새지 않게).
        comp_sum = sum(c["amount_krw"] for c in p["components"] if c.get("amount_krw")) or None
        official = official_by_key.get((p["group"], _match_name(p["name"])))
        p["total_krw"] = comp_sum
        p["official_total_krw"] = official
        if comp_sum is not None and official is not None:
            checkable_n += 1
            # 허용오차 5백만원: 원문이 항목별로 백만원 단위 독립 반올림해 Σ항목 ≠ 반올림된 총액인
            # 경우가 흔하다(POSCO 천성래 482+448+10=940 vs 공식 941, diff 1백만원 — 파서 아닌 원문
            # 반올림). 진짜 누락(상여 수억 누락 등)은 이 오차를 훨씬 넘어 여전히 적발된다.
            diff = comp_sum - official
            p["total_diff_krw"] = diff
            p["total_consistent"] = abs(diff) <= 5_000_000
            if p["total_consistent"]:
                consistent_n += 1
        else:
            p["total_consistent"] = None
            p["total_diff_krw"] = None

    return {
        "pay_policy": policy,
        "policy_narrative": policy_narrative,
        "individuals": individuals,
        "individual_totals": indiv_totals,
        "reconciliation": {
            "checkable": checkable_n,
            "consistent": consistent_n,
            "consistent_rate": round(consistent_n / checkable_n * 100, 1) if checkable_n else None,
        },
        "aggregate_seen": aggregate_seen,
        "unknown_tables": unknown,
    }


# ── 하이브리드 교차검증: 파서 Σ컴포넌트 vs 정형 API 공식 총액 ─────────────────

# in-doc official 표와 API 총액은 둘 다 원문에서 나오지만(API=DART가 사업보고서를 구조화한 것),
# 파서는 in-doc 표를 **자기 그리드 로직으로 다시 읽어** official을 뽑으므로 파서-vs-in-doc은
# 사실상 파서-vs-파서다. 파서가 이름+직위를 한 셀로 병합해 읽으면 official도 같은 병합값이라
# 자기일치는 통과하지만 실제로는 틀린다(삼성생명 silent case). API는 파서 그리드를 안 거친
# 독립 소스라 이 병합/오독을 잡아낸다. → 파서 Σ컴포넌트(원문 파싱) vs API 총액(정형)의 독립 교차.

# 허용오차: API는 원 단위 정확값, 파서 comp_sum은 백만원 반올림 항목들의 합이라 항목수만큼 반올림
# 오차가 누적된다(항목 6개면 ±3백만 내외). in-doc 대조와 동일하게 ±5백만원.
_API_RECON_TOL_KRW = 5_000_000
_FIVE_EOK = 500_000_000   # API 개별공개 하한(5억) — 파서쪽 미매칭 판정 기준


def reconcile_with_api(parsed: dict[str, Any], api_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """정형 API(hmvAuditIndvdlBySttus 5억+ 개인별 보수총액)로 파서 개인별 Σ컴포넌트를 독립 교차검증.

    parsed['individuals'] 각 항목에 api_total_krw/api_consistent/api_diff_krw를 in-place 부여하고,
    요약(api_reconciliation)을 반환한다. 이름은 _match_name(각주마커 제거)으로 매칭.

    핵심: 파서 자기일치(in-doc 표 대조)를 통과해도 **API와 어긋나면** 파서가 원문을 오독한 신호다
    (이름+직위 병합처럼 in-doc과 산정기준 표에 같은 오독이 걸린 경우). 매칭 실패도 신호로 남긴다:
      - api_unmatched: API엔 5억+로 있는데 파서 개인에 대응자가 없음(파서가 이름을 병합/누락).
      - parser_unmatched_ge5: 파서가 5억+ 총액인데 API에 대응자 없음(그룹='상위5명' 미등기·직원이면
        정상 — 5억+ 개인공개는 등기임원·감사 대상이라 미등기는 API에 없을 수 있음. 신호 강도 낮음)."""
    # API: 실명(5억+) → 총액. 동명이인은 합치지 않고 최댓값 유지(보수적 — 잘못된 과소매칭 방지).
    api_by_name: dict[str, int] = {}
    api_display: dict[str, str] = {}
    for r in api_rows or []:
        nm = _match_name(str(r.get("nm") or ""))
        if not nm or nm in ("-", "합계", "계"):
            continue
        try:
            total = int(str(r.get("mendng_totamt") or "").replace(",", "").strip() or "0")
        except ValueError:
            continue
        if total <= 0:
            continue
        if nm not in api_by_name or total > api_by_name[nm]:
            api_by_name[nm] = total
            api_display[nm] = str(r.get("nm") or "").strip()

    matched_keys: set[str] = set()
    checkable = consistent = 0
    for p in parsed.get("individuals") or []:
        key = _match_name(p.get("name") or "")
        api_total = api_by_name.get(key)
        p["api_total_krw"] = api_total
        comp_sum = p.get("total_krw")
        if api_total is not None and comp_sum is not None:
            matched_keys.add(key)
            diff = comp_sum - api_total
            p["api_diff_krw"] = diff
            p["api_consistent"] = abs(diff) <= _API_RECON_TOL_KRW
            checkable += 1
            if p["api_consistent"]:
                consistent += 1
        else:
            p["api_diff_krw"] = None
            p["api_consistent"] = None

    api_unmatched = [
        {"name": api_display[k], "api_total_krw": v}
        for k, v in api_by_name.items() if k not in matched_keys
    ]
    parser_unmatched_ge5 = [
        {"group": p.get("group"), "name": p.get("name"), "total_krw": p.get("total_krw")}
        for p in (parsed.get("individuals") or [])
        if (p.get("total_krw") or 0) >= _FIVE_EOK and _match_name(p.get("name") or "") not in api_by_name
    ]

    return {
        "source": "hmvAuditIndvdlBySttus (정형 API, 5억+ 개인별 보수총액)",
        "checkable": checkable,
        "consistent": consistent,
        "consistent_rate": round(consistent / checkable * 100, 1) if checkable else None,
        "api_disclosed": len(api_by_name),
        "api_unmatched": api_unmatched,               # API엔 있는데 파서 개인에 대응 없음(병합/누락 신호)
        "parser_unmatched_ge5": parser_unmatched_ge5,  # 파서 5억+인데 API에 없음(미등기면 정상)
    }
