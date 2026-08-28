"""제3자배정 대상자 — 「누가 받았나」를 원문 그대로 실어 준다.

**왜 파서를 늘리지 않고 원문을 싣나.**
DART 정형 API(`piicDecsn`)는 배정 *방식*(`ic_mthn` = 제3자배정/주주배정/일반공모)만 준다.
**배정 대상자 명단은 정형 API 에 아예 없다** — 공시 원문 표에만 있다. 그래서 신주 2,209,716주를
보고도 「누가 받았나」를 몰라 경영권 방어용 우호지분인지 사업 목적인지 가르지 못한다
(2026-08-28 실사용 시험, 고려아연 사례).

여기서 하는 일은 **그 대목을 찾아 원문 그대로 넘기는 것**이다. 표로 쥐어짜지 않는다.
행 분해(`allottees`)는 **더하는 것**이고, 실패하면 비운 채로 원문만 남긴다 — 「미상」으로
뭉개지 않는다.

**실측 (2026-08-28, 코스피+코스닥 2026-06-01~08-28 유상증자결정 60건 표본)**
- 제3자배정 공시 53건 **전부**가 `【제3자배정 대상자별 선정경위, 거래내역, 배정내역 등】`
  머리표지를 갖는다 (53/53). 띄어쓰기 한 자리만 다른 변종이 1건 있어 정규식으로 받는다.
- 그 블록 길이: 중앙값 219자 · p90 931자 · 최대 2,982자. 기본 창 4,000자면 표본 전부가 들어온다.
- 6열 표 머리(`제3자배정 대상자`/`회사 또는최대주주와의 관계`/`선정경위`/
  `증자결정 전후 6월이내 거래내역 및 계획`/`배정주식수 (주)`/`비 고`)가 39/43.
"""

from __future__ import annotations

import re
from typing import Any

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.date_utils import format_iso_date

#: 원문 창 기본값. `business_details.section_chars` 와 같은 손잡이다 — 부족하면 호출하는 쪽이 올린다.
SECTION_CHARS_DEFAULT = 4_000
SECTION_CHARS_MIN = 500
SECTION_CHARS_MAX = 40_000

_BRACKET_RE = re.compile(r"【[^】]{2,80}】")

#: 실어 줄 원문 대목. `key`, 찾을 정규식, **그 자리에 무엇이 있는지** 한 줄.
#: 순서가 곧 유용도 순이다 — 창이 좁으면 앞엣것부터 채운다.
_SECTION_SPECS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "allottee_table",
        re.compile(r"【\s*제3자배정\s*대상자별[^】]*】"),
        "누가 얼마를 받나 — 대상자명·회사(최대주주)와의 관계·선정경위·배정주식수·보호예수",
    ),
    (
        "allottee_entity_profile",
        re.compile(r"【\s*제3자배정\s*대상자\s*중\s*법인[^】]*】"),
        "대상자가 법인·조합이면 그 **최대출자자와 지분율** — 실질 지배자가 누구인지가 여기 있다",
    ),
    (
        "new_controller_profile",
        re.compile(r"【\s*최대주주가\s*되는[^】]*】"),
        "이 증자로 **최대주주가 바뀌는 경우** 그 새 최대주주의 실체",
    ),
    (
        "basis_and_purpose",
        re.compile(r"【\s*제3자배정\s*근거[^】]*】"),
        "정관 근거 조항과 회사가 밝힌 **증자 목적** — 「경영상 필요」인지 사업 제휴인지",
    ),
    (
        "fund_use",
        re.compile(r"【\s*제3자배정\s*조달자금[^】]*】"),
        "조달자금의 구체적 사용목적 — 거래상대방·증권발행회사까지",
    ),
    (
        "repeated_correction",
        re.compile(r"【\s*제3자배정으로서\s*주요사항보고서가\s*5회[^】]*】"),
        "5회 이상 정정된 제3자배정 — 조건이 계속 흔들린 건이라는 표지",
    ),
)

#: 【】 블록이 아니라 번호 절로 오는 자리. 의결권 행사 합의·주주간계약이 여기 적힌다.
_NUMBERED_SPECS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "investor_judgment_note",
        re.compile(r"^\s*\d{1,2}\.\s*기타\s*투자판단에\s*참고할\s*사항\s*$", re.M),
        "회사가 자유서술로 적는 자리 — **의결권 행사 합의·주주간계약·보호예수**가 여기 나온다",
    ),
)

_TABLE_HEADER = (
    "제3자배정 대상자",
    "회사 또는최대주주와의 관계",
    "선정경위",
    "증자결정 전후 6월이내 거래내역 및 계획",
    "배정주식수 (주)",
    "비 고",
)
_SHARES_RE = re.compile(r"^-?[\d,]+$")


def clamp_section_chars(value: Any) -> int:
    """창 크기를 범위 안으로. 잘못 들어온 값은 기본값으로 돌린다."""
    if isinstance(value, bool) or not isinstance(value, int):
        return SECTION_CHARS_DEFAULT
    return max(SECTION_CHARS_MIN, min(SECTION_CHARS_MAX, value))


def viewer_url(rcept_no: str) -> str:
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""


def _cut(text: str, limit: int) -> tuple[str, bool]:
    text = (text or "").strip()
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip(), True


def _is_child_bracket(heading: str) -> bool:
    """자금 용도별 하위 블록인가.

    `【제3자배정 조달자금의 구체적 사용목적】` 바로 뒤에는 `【타법인 증권 취득자금ㆍ영업양수자금의
    경우】`·`【운영자금ㆍ기타자금의 경우】` 같은 **하위** 블록이 붙는다. 여기서 끊으면 부모가
    빈 껍데기로 나간다(2026-08-28 고려아연에서 실제로 그랬다). `자금`+`경우`를 둘 다 가진
    머리표지만 하위로 본다 — `대상자 중 법인…포함된 경우` 처럼 `자금` 이 없는 것은 상위다.
    """
    if any(pattern.match(heading) for _key, pattern, _what in _SECTION_SPECS):
        return False
    return "자금" in heading and "경우" in heading


def _slice_bracket(text: str, match: re.Match[str]) -> str:
    """머리표지부터 **다음 상위 【 직전까지**. 하위(자금 용도별) 블록은 품고 간다."""
    for m in _BRACKET_RE.finditer(text):
        if m.start() <= match.start():
            continue
        if _is_child_bracket(m.group(0)):
            continue
        return text[match.start():m.start()]
    return text[match.start():]


def _slice_numbered(text: str, match: re.Match[str]) -> str:
    """번호 절 머리부터 **다음 번호 절 / 다음 【 직전까지**."""
    after = text[match.end():]
    stops = [m.start() for m in re.finditer(r"^\s*\d{1,2}\.\s*\S", after, re.M)]
    stops += [m.start() for m in _BRACKET_RE.finditer(after)]
    end = min(stops) if stops else len(after)
    return text[match.start():match.end() + end]


def parse_allottee_rows(block: str) -> tuple[list[dict[str, Any]], str]:
    """6열 표를 행으로 나눈다. **못 나누면 빈 목록 + 이유**를 준다 — 지어내지 않는다.

    문서 text 는 표 칸이 한 줄씩 평평해져 온다. 머리 6칸을 확인한 뒤 6줄씩 끊되,
    **5번째 칸이 숫자(배정주식수)일 때만** 한 행으로 인정한다. 각주가 붙어 줄 수가
    6의 배수가 아닌 공시가 표본 43건 중 8건 있었는데, 이 규칙이 거기서 멈춰 준다.
    """
    lines = [ln.strip() for ln in (block or "").split("\n") if ln.strip()]
    # 머리표지 줄(【...】)은 건너뛴다.
    if lines and _BRACKET_RE.match(lines[0]):
        lines = lines[1:]
    if len(lines) < len(_TABLE_HEADER):
        return [], ("표 머리조차 없다 — 이 공시의 해당 블록이 통째로 비어 있다(제3자배정이 아닌 증자이거나 "
                    "정정본이라 본문이 빠진 경우). 배정방식과 원문을 먼저 볼 것.")
    head = [h.replace(" ", "") for h in lines[:6]]
    want = [h.replace(" ", "") for h in _TABLE_HEADER]
    if head != want:
        return [], (
            f"표 머리가 표준 6열과 달라 행 분해를 하지 않았다(첫 줄들: {' / '.join(lines[:6])}) — "
            "위 원문을 직접 읽을 것.")

    rows: list[dict[str, Any]] = []
    body = lines[6:]
    i = 0
    while i + 6 <= len(body):
        cells = body[i:i + 6]
        if not _SHARES_RE.match(cells[4].replace(" ", "")):
            break
        rows.append({
            "name": cells[0],
            "relation_to_company_or_controller": cells[1],
            "selection_reason": cells[2],
            "trades_within_6m": cells[3],
            "allotted_shares_text": cells[4],
            "note": cells[5],
        })
        i += 6

    leftover = len(body) - i
    if not rows:
        # 칸이 전부 `-` 인 빈 표 — 제3자배정이 아닌 증자에서도 서식상 이 블록이 따라온다.
        # 「못 읽었다」가 아니라 **회사가 비워 냈다**. 둘을 섞지 않는다.
        if body and all(c.strip() in ("-", "") for c in body):
            return [], ("대상자 표가 원문에서 전부 `-` 로 비어 있다 — 파싱 실패가 아니라 "
                        "회사가 해당 없음으로 적은 것이다. 이 증자가 제3자배정이 맞는지 배정방식을 먼저 볼 것.")
        return [], ("표 머리는 찾았으나 행으로 나누지 않았다 — 칸 하나가 여러 줄로 쪼개져 6열 격자가 "
                    "맞지 않는다(한화에어로스페이스 20250418000538 형). **대상자·관계·배정주식수는 "
                    "위 원문에 그대로 있다** — 잘못 짝지어 내보내느니 원문을 읽게 둔다.")
    note = f"6열 표에서 {len(rows)}행을 나눴다. 원문이 근거고 이 표는 편의다."
    if leftover:
        note += f" 뒤에 표로 나누지 않은 {leftover}줄(각주 등)이 더 있다 — 원문에 그대로 있다."
    return rows, note


def extract_allotment_sections(text: str, *, section_chars: int) -> dict[str, Any]:
    """제3자배정 관련 원문 대목을 **찾은 것만** 담아 돌려준다.

    없는 자리는 만들지 않는다. 대신 그 문서에 실제로 있는 다른 머리표지를
    `other_headings_in_document` 로 넘겨 「거기 없으면 여기일 수 있다」를 준다.
    """
    text = text or ""
    found: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []

    for key, pattern, what in _SECTION_SPECS:
        m = pattern.search(text)
        if not m:
            missing.append({"key": key, "what_is_here": what})
            continue
        raw = _slice_bracket(text, m)
        excerpt, truncated = _cut(raw, section_chars)
        entry: dict[str, Any] = {
            "key": key,
            "heading": m.group(0),
            "what_is_here": what,
            "excerpt": excerpt,
            "chars": len(raw),
            "truncated": truncated,
        }
        if truncated:
            entry["truncation_note"] = (
                f"원문 {len(raw):,}자 중 앞 {section_chars:,}자만 실었다 — "
                f"`section_chars` 를 올려 다시 부르면 뒤가 나온다(최대 {SECTION_CHARS_MAX:,}).")
        found.append(entry)

    for key, pattern, what in _NUMBERED_SPECS:
        m = pattern.search(text)
        if not m:
            missing.append({"key": key, "what_is_here": what})
            continue
        raw = _slice_numbered(text, m)
        excerpt, truncated = _cut(raw, section_chars)
        entry = {
            "key": key,
            "heading": m.group(0).strip(),
            "what_is_here": what,
            "excerpt": excerpt,
            "chars": len(raw),
            "truncated": truncated,
        }
        if truncated:
            entry["truncation_note"] = (
                f"원문 {len(raw):,}자 중 앞 {section_chars:,}자만 실었다 — "
                f"`section_chars` 를 올려 다시 부르면 뒤가 나온다(최대 {SECTION_CHARS_MAX:,}).")
        found.append(entry)

    # 하위(자금 용도별) 블록은 이미 부모 발췌 안에 들어 있다 — 「다른 후보」로 다시 세지 않는다.
    headings = sorted({
        m.group(0) for m in _BRACKET_RE.finditer(text) if not _is_child_bracket(m.group(0))
    })
    taken = {e["heading"] for e in found}
    return {
        "sections": found,
        "sections_not_found": missing,
        "other_headings_in_document": [h for h in headings if h not in taken],
    }


def _is_third_party(row: dict[str, Any]) -> bool:
    method = (row.get("issuance_method") or "").replace(" ", "")
    plan_method = ((row.get("original_plan") or {}).get("issuance_method") or "").replace(" ", "")
    return "제3자배정" in method or "제3자배정" in plan_method


def _next_steps(rcept_no: str) -> list[str]:
    """막다른 골목으로 끝내지 않는다 — 여기 없으면 어디로 갈지."""
    return [
        f"원문 전체는 DART 뷰어에서: {viewer_url(rcept_no)}",
        "대상자가 실제로 지분을 쥐었는지는 `ownership_structure` 의 지분 변동과 5% 대량보유보고로 교차 확인",
        "경영권 분쟁 중이면 `proxy_contest` 로 위임장 경쟁 여부를 같이 볼 것",
        "증권신고서(지분증권)·증권발행실적보고서는 아래 `equity_offering_channel` 목록 참조 — "
        "인수인·자금사용목적·실제 배정 결과가 그쪽에 있다",
    ]


async def enrich_third_party_allottees(
    ro_rows: list[dict[str, Any]],
    *,
    section_chars: int = SECTION_CHARS_DEFAULT,
    max_rows: int = 3,
) -> tuple[list[str], int]:
    """제3자배정 유상증자 행에 원문 대목을 붙인다. Returns (warnings, dart_calls).

    제3자배정이 아닌 행은 건드리지 않는다(추가 호출 0). `max_rows` 를 넘는 오래된 건은
    **건너뛴 사실을 warnings 에 적는다** — 조용히 자르지 않는다.
    """
    section_chars = clamp_section_chars(section_chars)
    warnings: list[str] = []
    calls = 0
    targets = [r for r in ro_rows if _is_third_party(r)]
    if not targets:
        return warnings, calls

    targets.sort(key=lambda r: (r.get("rcept_dt", ""), r.get("rcept_no", "")), reverse=True)
    skipped = targets[max_rows:]
    client = get_dart_client()

    for row in targets[:max_rows]:
        rcept_no = row.get("rcept_no", "")
        # 철회·정정으로 자기 원문에 표가 없으면 복원 때 쓴 원안 공시를 같은 자리에서 본다.
        candidates = [rcept_no]
        plan_src = (row.get("original_plan") or {}).get("source_rcept_no", "")
        if plan_src and plan_src != rcept_no:
            candidates.append(plan_src)

        picked: dict[str, Any] | None = None
        for cand in candidates:
            if not cand:
                continue
            try:
                doc = await client.get_document_cached(cand)
                calls += 1
            except Exception as exc:  # noqa: BLE001 — 한 건 실패가 전체를 죽이지 않는다
                warnings.append(f"제3자배정 대상자 원문 조회 실패 ({cand}): {exc}")
                continue
            text = doc.get("text", "") if isinstance(doc, dict) else ""
            extracted = extract_allotment_sections(text, section_chars=section_chars)
            if extracted["sections"]:
                picked = {"rcept_no": cand, **extracted}
                break
            if picked is None:
                picked = {"rcept_no": cand, **extracted}

        if picked is None:
            row["third_party_allotment"] = {
                "status": "NOT_READ",
                "note": "원문을 읽지 못했다 — 배정 대상자가 없다는 뜻이 아니다.",
                "next_steps": _next_steps(rcept_no),
            }
            continue

        table = next((s for s in picked["sections"] if s["key"] == "allottee_table"), None)
        allottees, parse_note = parse_allottee_rows(table["excerpt"]) if table else (
            [], "제3자배정 대상자 표를 이 원문에서 찾지 못했다 — 아래 다른 머리표지를 볼 것.")
        if table and table.get("truncated"):
            parse_note += " (원문이 잘려 뒤쪽 행이 빠졌을 수 있다 — `section_chars` 를 올려 다시 볼 것.)"

        row["third_party_allotment"] = {
            "status": "OK" if picked["sections"] else "SECTION_NOT_FOUND",
            "source_rcept_no": picked["rcept_no"],
            "source_rcept_dt": format_iso_date(picked["rcept_no"][:8]),
            "source_report_nm": "주요사항보고서(유상증자결정)",
            "viewer_url": viewer_url(picked["rcept_no"]),
            "section_chars_used": section_chars,
            "sections": picked["sections"],
            "sections_not_found": picked["sections_not_found"],
            "other_headings_in_document": picked["other_headings_in_document"],
            # 표는 **더하는 것**이다. 위 `sections` 의 원문이 근거다.
            "allottees": allottees,
            "allottee_parse_note": parse_note,
            "next_steps": _next_steps(picked["rcept_no"]),
        }
        if not picked["sections"]:
            warnings.append(
                f"제3자배정 유상증자 1건({row.get('rcept_dt', '')})의 원문에서 "
                f"「제3자배정 대상자」 대목을 찾지 못했다 — 배정 대상자가 없다는 뜻이 아니다. "
                f"뷰어에서 확인: {viewer_url(picked['rcept_no'])}")
        elif not allottees:
            warnings.append(
                f"제3자배정 유상증자 1건({row.get('rcept_dt', '')})은 대상자 표를 행으로 나누지 못했다 — "
                f"원문(`sections`)을 직접 읽을 것. 「미상」이 아니라 **원문에 있다**.")

    if skipped:
        warnings.append(
            f"제3자배정 유상증자 {len(targets)}건 중 최근 {max_rows}건만 원문 대목을 실었다 — "
            f"나머지 {len(skipped)}건({', '.join(r.get('rcept_dt', '') for r in skipped)})은 "
            f"start_date/end_date 를 좁혀 다시 부르면 나온다.")
    return warnings, calls


# ── C 채널(발행공시) — 증권신고서·투자설명서·증권발행실적보고서 ────────────────
#
# 🔴 **`pblntf_ty` 와 `pblntf_detail_ty` 를 같이 보내면 DART 가 detail 을 무시한다** (2026-08-28 실측).
#    `pblntf_ty="C"` + `pblntf_detail_ty="C001"` → C 전체 2,050건이 그대로 온다(C001~C011 모두 동일).
#    `pblntf_detail_ty="C001"` 만 보내면 4건. 그래서 여기서는 **detail 만** 보낸다.
#
# 실측 호출량 (2026-08-28, lookback 24개월, corp_code 지정):
#   고려아연 2건/1p · 한화에어로스페이스 9건/1p · 이수페타시스 12건/1p · 그 외 5사 0건.
#   → **회사당 list.json 1회**로 끝난다. 페이지컷 걱정 없음.

_C001_DETAIL = "C001"

#: report_nm → (kind, 그 안에 무엇이 있나). 여기 없는 이름은 `other` 로 남기되 이름을 그대로 보인다.
_C_KINDS: tuple[tuple[str, str, str], ...] = (
    ("증권발행실적보고서", "issuance_result",
     "**실제 배정 결과** — 청약·배정 현황, 인수기관별 인수금액, 유상증자 전후 주요주주 지분변동"),
    ("증권신고서", "registration",
     "인수인·모집방법·**자금의 사용목적**·인수인의 의견(분석기관 평가)"),
    ("투자설명서", "prospectus", "신고서와 같은 내용의 확정본 — 청약 직전 최종 조건"),
    ("철회신고서", "withdrawal", "신고 철회 — 그 발행은 나가지 않았다"),
    ("소액공모", "small_offering", "10억원 미만 소액공모 — 신고서 면제 건"),
)

#: 증권발행실적보고서 안에서 「누가 실제로 받았나」가 있는 자리.
_RESULT_SECTION_RE = re.compile(r"^\s*Ⅲ\.\s*유상증자\s*전후의\s*주요주주\s*지분변동\s*$", re.M)
_RESULT_ALT_RE = re.compile(r"^\s*Ⅱ\.\s*청약\s*및\s*배정에\s*관한\s*사항\s*$", re.M)
_ROMAN_STOP_RE = re.compile(r"^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧ]\.\s*\S", re.M)


def classify_c_filing(report_nm: str) -> tuple[str, str]:
    name = (report_nm or "").replace(" ", "")
    for keyword, kind, what in _C_KINDS:
        if keyword.replace(" ", "") in name:
            return kind, what
    return "other", "발행공시(C001) — 이름 그대로 확인할 것"


def _slice_roman(text: str, match: re.Match[str]) -> str:
    after = text[match.end():]
    stops = [m.start() for m in _ROMAN_STOP_RE.finditer(after)]
    end = min(stops) if stops else len(after)
    return text[match.start():match.end() + end]


def extract_issuance_result_sections(text: str, *, section_chars: int) -> list[dict[str, Any]]:
    """증권발행실적보고서에서 「실제로 누가 받았나」 대목을 원문으로."""
    out: list[dict[str, Any]] = []
    for pattern, what in (
        (_RESULT_SECTION_RE, "**유상증자 전후 주요주주 지분변동** — 최대주주·특수관계인 지분이 몇 %에서 몇 %가 됐나"),
        (_RESULT_ALT_RE, "청약 및 배정 현황 — 인수기관별 인수금액, 배정 방식별 배정현황"),
    ):
        m = pattern.search(text or "")
        if not m:
            continue
        raw = _slice_roman(text, m)
        excerpt, truncated = _cut(raw, section_chars)
        entry: dict[str, Any] = {
            "heading": m.group(0).strip(),
            "what_is_here": what,
            "excerpt": excerpt,
            "chars": len(raw),
            "truncated": truncated,
        }
        if truncated:
            entry["truncation_note"] = (
                f"원문 {len(raw):,}자 중 앞 {section_chars:,}자만 실었다 — `section_chars` 를 올릴 것.")
        out.append(entry)
    return out


async def fetch_equity_offering_channel(
    corp_code: str,
    bgn_de: str,
    end_de: str,
    *,
    section_chars: int = SECTION_CHARS_DEFAULT,
    read_latest_result: bool = True,
) -> tuple[dict[str, Any], list[str], int]:
    """C001(증권신고 — 지분증권) 채널을 연다. Returns (block, warnings, dart_calls).

    이 저장소가 지금껏 열지 않던 채널이다. 주요사항보고서(B)는 「무엇을 결정했나」만 주고,
    **인수인·자금사용 목적·실제 배정 결과**는 여기 있다.

    비용: list.json **1회**(실측 회사당 1페이지). 여기에 더해, 창 안에 증권발행실적보고서가
    있으면 **가장 최근 1건만** 원문을 열어 「유상증자 전후 주요주주 지분변동」을 싣는다(+1회).
    나머지는 열지 않고 **가리키기만 한다** — 문서마다 1.5만~2.6만자라 다 실으면 응답이 터진다.
    """
    section_chars = clamp_section_chars(section_chars)
    warnings: list[str] = []
    calls = 0
    client = get_dart_client()

    try:
        # 🔴 detail 만 보낸다 — pblntf_ty 를 같이 보내면 DART 가 detail 을 무시한다(위 주석).
        res = await client.search_filings(
            bgn_de=bgn_de, end_de=end_de,
            pblntf_detail_ty=_C001_DETAIL,
            corp_code=corp_code, page_count=100,
        )
        calls += 1
    except Exception as exc:  # noqa: BLE001 — 013(데이터 없음) 포함
        status = getattr(exc, "status", "")
        if status != "013":
            warnings.append(f"발행공시(C001) 조회 실패: {status or exc}")
        return {
            "channel": "C001 (증권신고 — 지분증권)",
            "filing_count": 0,
            "filings": [],
            "note": "조사 구간 내 지분증권 발행공시 없음. 「없다」가 아니라 **이 창 안에 없다**는 뜻이다.",
        }, warnings, calls

    items = res.get("list") or []
    total_page = int(res.get("total_page") or 1)
    if total_page > 1:
        warnings.append(
            f"발행공시(C001)가 {res.get('total_count')}건({total_page}페이지)이라 첫 100건만 봤다 — "
            f"start_date/end_date 를 좁혀 다시 부를 것.")

    filings = []
    for x in items:
        kind, what = classify_c_filing(x.get("report_nm", ""))
        filings.append({
            "rcept_no": x.get("rcept_no", ""),
            "rcept_dt": format_iso_date(x.get("rcept_dt", "")),
            "report_nm": x.get("report_nm", ""),
            "kind": kind,
            "what_is_here": what,
            "viewer_url": viewer_url(x.get("rcept_no", "")),
        })
    filings.sort(key=lambda f: (f.get("rcept_dt", ""), f.get("rcept_no", "")), reverse=True)

    block: dict[str, Any] = {
        "channel": "C001 (증권신고 — 지분증권)",
        "filing_count": len(filings),
        "filings": filings,
        "note": (
            "목록만 실었다 — 본문은 건당 1.5만~2.6만자라 통째로 담지 않는다. "
            "`viewer_url` 로 열거나 `evidence` tool 로 해당 접수번호를 지정해 읽을 것."),
    }

    results = [f for f in filings if f["kind"] == "issuance_result"]
    if read_latest_result and results:
        latest = results[0]
        try:
            doc = await client.get_document_cached(latest["rcept_no"])
            calls += 1
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"증권발행실적보고서 원문 조회 실패 ({latest['rcept_no']}): {exc}")
        else:
            text = doc.get("text", "") if isinstance(doc, dict) else ""
            sections = extract_issuance_result_sections(text, section_chars=section_chars)
            block["latest_issuance_result"] = {
                "rcept_no": latest["rcept_no"],
                "rcept_dt": latest["rcept_dt"],
                "report_nm": latest["report_nm"],
                "viewer_url": latest["viewer_url"],
                "section_chars_used": section_chars,
                "sections": sections,
                "note": (
                    "발행이 **끝난 뒤** 실제 결과다 — 결정 시점 계획과 다를 수 있다."
                    if sections else
                    "이 보고서에서 「유상증자 전후 주요주주 지분변동」 절을 찾지 못했다 — "
                    "합병·채무증권 실적보고서일 수 있다. 뷰어에서 확인할 것."),
            }
            if len(results) > 1:
                block["latest_issuance_result"]["other_results_not_read"] = [
                    {"rcept_no": r["rcept_no"], "rcept_dt": r["rcept_dt"], "viewer_url": r["viewer_url"]}
                    for r in results[1:]
                ]
    return block, warnings, calls
