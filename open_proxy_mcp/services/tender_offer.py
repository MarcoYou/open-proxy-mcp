"""공개매수 공시(신고서·설명서·결과보고서·의견표명서) 본문 읽기 도우미.

원문을 대체하지 않는다 — **칸 이름이 분명한 표만** 값으로 뽑고, 못 뽑으면 `terms_absent`/`form_unrecognized` 로
말한 뒤 **원문 절 번호**(`filing_section(rcept_no=…, no=…)`)를 건넨다. 값은 손대지 않은 원문 문자열이다
(「최소 1,445,036주(발행주식총수의 약 6.98%)」처럼 한 칸에 여러 값이 들어 있어도 쪼개지 않는다).

`proxy_contest(scope="tender_offer")` 가 쓴다. 공시 목록은 거기서 이미 받으므로 여기는 본문만 본다.
"""

from __future__ import annotations

import asyncio
import html as _html
import re
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.doc_tables import compact, table_grids as _raw_grids

TENDER_DOC_LOOKUPS = 12

_TITLE = re.compile(r"(?is)<TITLE\b([^>]*)>(.*?)</TITLE>")
_ATOCID = re.compile(r'(?i)ATOCID\s*=\s*"(\d+)"')
_TAG = re.compile(r"<[^>]+>")
_DATE = re.compile(r"\d{4}\s*[년.\-/]\s*\d{1,2}\s*[월.\-/]\s*\d{1,2}\s*일?")
_OPINION = re.compile(r"찬성|반대|중립|유보")
_OPINION_CELL = re.compile(r"의견\s*[:：]\s*(찬성|반대|중립|유보)")
# 의견 문장은 서식마다 다르다(「의견 : 반대」·「중립의 의견을 표명합니다」·「본 공개매수에 찬성」). 「공개매수에 대한/관한 … 의견」 머리말
# 바로 뒤 글에서 처음 나오는 낱말을 잡고, 그 둘레 글을 함께 건넨다 — 읽는 쪽이 문장으로 확인한다.
_OPINION_HEAD = re.compile(r"공개매수에\s*(?:대한|관한)\s*(?:당사의\s*)?의견")
_NAME = re.compile(r"성\s*명\s*[:：]?\s*([^■□\n]+)")  # 「성 명 주식회사 …」처럼 콜론이 없는 표지도 있다
_AFTER_NAME = re.compile(r"주\s*소\s*[:：]|전화\s*번호|법인등록번호")  # 한 칸에 「성명 … 주소 …」 가 이어 붙어 오는 서식
_ANY_DATE = r"\d{4}\s*[.년]\s*\d{1,2}\s*[.월]\s*\d{1,2}\s*[.일]?"
_PERIOD_TEXT = re.compile(rf"공개매수\s*기간\s*[:：]?\s*({_ANY_DATE})\s*[-~–]\s*({_ANY_DATE})(?:\s*\(?\s*결제일\s*[:：]\s*({_ANY_DATE}))?")
_PURPOSE_BOX = re.compile(r"(?:[■□]\s*(?:경영권\s*안정|M&A|지주회사\s*요건\s*충족|상장\s*폐지|기타)\s*(?:-\s*내용\s*[:：]?[^■□]*)?){3,}")
_PURPOSE = re.compile(r"(경영권\s*안정|M&A|지주회사\s*요건\s*충족|상장\s*폐지|기타)\s*(.*)", re.S)
_CHECKED = re.compile(r"■\s*([^■□]+)")

# 절 이름(공백 뺀 것) → 이 모듈이 읽는 항목. 신고서 서식은 장(Ⅰ~Ⅶ) 이름이 고정이다.
_SECTION_KEYS = {"purpose": "공개매수의목적", "terms": "공개매수의조건", "funding": "필요한자금"}


def _text(fragment: str) -> str:
    return " ".join(_html.unescape(_TAG.sub(" ", (fragment or "").replace("&cr;", " "))).split())


def table_grids(markup: str) -> list[list[list[str]]]:
    """공용 격자 + 옛 서식이 칸 안 줄바꿈으로 쓴 `&cr;`(DART 고유 줄바꿈 기호) 제거."""
    return [[[" ".join(c.replace("&cr;", " ").split()) for c in row] for row in grid] for grid in _raw_grids(markup)]


def _kind(report_name: str) -> str:
    name = compact(report_name)
    if "결과보고서" in name:
        return "result"
    if "의견표명" in name:
        return "opinion"
    if "철회" in name:
        return "withdrawal"
    if "설명서" in name:
        return "prospectus"
    return "filing"  # 신고서 · 정정신고서


def _sections(markup: str) -> list[dict[str, Any]]:
    """`<TITLE ATOCID=n>` 로 가른 절. `no` 는 `filing_section` 의 절 번호와 같다. 본문은 다음 TITLE 앞까지."""
    heads = [(m, _ATOCID.search(m.group(1))) for m in _TITLE.finditer(markup or "")]
    heads = [(m, a) for m, a in heads if a]
    out: list[dict[str, Any]] = []
    for i, (m, a) in enumerate(heads):
        end = heads[i + 1][0].start() if i + 1 < len(heads) else len(markup)
        out.append({"no": int(a.group(1)), "title": _text(m.group(2)), "html": markup[m.end():end]})
    return out


def _first_date(cells: list[str]) -> str:
    found = _DATE.search(" ".join(cells))
    return " ".join(found.group(0).split()) if found else ""


def _name(raw: str) -> str:
    """「김 익 래」처럼 글자마다 벌어진 이름은 붙이고, 「주식회사 에스원」 같은 띄어쓰기는 둔다."""
    text = " ".join(raw.split())
    return text.replace(" ", "") if re.fullmatch(r"(?:[가-힣] )+[가-힣]", text) else text


def _checked(text: str) -> list[str]:
    return [" ".join(c.split()) for c in _CHECKED.findall(text)]


def _same_name(a: str, b: str) -> bool:
    """「(주)에스원」 = 「주식회사 에스원」 — 표지와 요약정보가 법인 표기를 달리 적는다."""
    strip = lambda x: re.sub(r"\(주\)|주식회사|\s", "", x)  # noqa: E731
    return strip(a) == strip(b)


def _parse_offerors(markup: str) -> tuple[list[dict[str, Any]], list[str], str]:
    """공개매수자(제안한 쪽)와 공개매수 목적 체크 칸. 신고서 「요약정보」 표의 칸(성명·■회사/개인·■대상회사와의 관계·■목적)을 먼저 보고,
    그 표가 없는 서식(결과보고서·의견표명서·옛 신고서)은 표지의 「공개매수자/보고자 · 성명」 줄에서 이름만 읽는다. 값은 칸 그대로다."""
    out: list[dict[str, Any]] = []
    purpose: list[str] = []
    purpose_note: list[str] = []
    pending_names = False
    for grid in table_grids(markup)[:8]:
        for row in grid:
            label = compact(row[0]) if row else ""
            text = " ".join(dict.fromkeys(row[1:]))
            if label in {"공개매수자", "보고자"}:
                m = _NAME.search(text)
                if m:
                    name = _name(_AFTER_NAME.split(m.group(1))[0])
                    entry = next((e for e in out if _same_name(e["name"], name)), None)
                    if entry is None:
                        entry = {"name": name}
                        out.append(entry)
                    kinds = _checked(text)
                    if kinds:
                        entry["type"] = kinds[0]
                elif not m and "대상회사와의관계" not in compact(text):
                    pending_names = True  # 의견표명서: 「공개매수자」 줄 아래 「성 명 : A, B」 줄
                elif "대상회사와의관계" in compact(text) and out:
                    kinds = _checked(text)
                    if kinds:
                        out[-1]["relation_to_target"] = kinds[0]
            elif label in {"성명", "성명:"} and pending_names and len(row) > 1:
                for part in re.split(r"[,，]", row[1]):
                    if part.strip() and not any(_same_name(e["name"], part) for e in out):
                        out.append({"name": _name(part)})
                pending_names = False
            elif label == "공개매수목적" and not purpose:
                # 「■ 기타 - 내용: …」 은 체크 이름과 그 아래 쓴 내용이 한 칸이다 — 이름만 체크 목록에, 내용은 따로.
                for c in _checked(text):
                    found = _PURPOSE.match(c)
                    head, note = (found.group(1), found.group(2)) if found else (c, "")
                    purpose.append(re.sub(r"\s+", "", head) if head != "M&A" else head)
                    note = re.sub(r"^[\s\-]*내용\s*[:：]?\s*", "", note).strip(" -")
                    if note:
                        purpose_note.append(note[:300])
    if not purpose:
        # 목적 칸 아래에 표가 끼어(코오롱·이마트) 격자가 어긋난 서식 — 본문 글에서 체크 줄을 직접 찾는다.
        box = _PURPOSE_BOX.search(_text(markup[:200_000]))
        for c in _checked(box.group(0)) if box else []:
            found = _PURPOSE.match(c)
            purpose.append(re.sub(r"\s+", "", found.group(1)) if found and found.group(1) != "M&A" else (found.group(1) if found else c))
    return out, purpose, " ".join(purpose_note)


def _parse_opinion(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    stated = _OPINION_CELL.search(text)
    if stated:
        out["opinion"] = stated.group(1)
        return out
    for head in _OPINION_HEAD.finditer(text):
        window = text[head.end(): head.end() + 400]
        hit = _OPINION.search(window)
        if hit:
            out["opinion"] = hit.group(0)
            out["opinion_context"] = window[max(0, hit.start() - 60): hit.end() + 60].strip()
            break
    return out


def _parse_terms(section_html: str) -> dict[str, Any]:
    """조건 절 → 가격·수량 표(주식 종류마다 한 줄) + 시작일·종료일·결제일."""
    out: dict[str, Any] = {}
    rows_out: list[dict[str, str]] = []
    for grid in table_grids(section_html):
        for r, row in enumerate(grid):
            keys = [compact(c) for c in row]
            label = keys[0] if keys else ""
            if label in {"시작일", "종료일", "결제일"} and len(row) > 1:
                date = _first_date(row[1:])
                if date:
                    out.setdefault({"시작일": "period_start", "종료일": "period_end", "결제일": "settlement_date"}[label], date)
            if "매수가격" in keys and any("매수예정수량" in k for k in keys):
                price_i = keys.index("매수가격")
                qty_i = next(i for i, k in enumerate(keys) if "매수예정수량" in k)
                pay_i = next((i for i, k in enumerate(keys) if "결제수단" in k), None)
                for body in grid[r + 1:]:
                    if len(body) <= max(price_i, qty_i) or not compact(body[price_i]):
                        break
                    rows_out.append({
                        "stock": body[0],
                        "price": body[price_i],
                        "quantity": body[qty_i],
                        **({"payment": body[pay_i]} if pay_i is not None and pay_i < len(body) else {}),
                    })
    if rows_out:
        out["offers"] = rows_out
    return out


def _parse_result(markup: str) -> dict[str, Any]:
    """결과보고서 → 예정·응모·매수 주식수, 주당 가격, 공개매수 후 보유비율."""
    out: dict[str, Any] = {}
    for grid in table_grids(markup):
        for r, row in enumerate(grid):
            keys = [compact(c) for c in row]
            # 가로로 눕힌 서식(「매수가격 | 주당 …」 · 「매수 주식수 | 공개매수자별 … | N주」): 칸 이름이 줄 맨 앞이다.
            if len(row) > 1 and keys[0] in {"매수가격", "응모주식수", "매수주식수", "응모증권수", "매수증권수"} or (len(row) > 2 and keys[0].startswith("공개매수예정수량")):
                field = {"매수가격": "price", "응모주식수": "tendered", "응모증권수": "tendered", "매수주식수": "purchased", "매수증권수": "purchased"}.get(keys[0], "planned")
                value = row[-1] if keys[0] in {"매수가격", "응모주식수"} else f"{row[1]}: {row[-1]}"
                if field in {"price", "tendered"}:
                    out.setdefault(field, value)
                else:  # 공개매수자별로 줄이 나뉘면 줄마다 이어 붙인다(합계는 원문에 없을 수 있다)
                    out[field] = f"{out[field]}; {value}" if field in out else value
                continue
            # 주식 말고 증권예탁증권(KDR)이면 칸 이름이 「…증권수」다.
            if any(re.search("응모(주식|증권)수", k) for k in keys) and any(re.search("매수(주식|증권)수", k) for k in keys) and r + 1 < len(grid):
                body = grid[r + 1]
                for field, needle in (("planned", "예정(주식|증권)수"), ("tendered", "응모(주식|증권)수"), ("purchased", "매수(주식|증권)수")):
                    i = next((i for i, k in enumerate(keys) if re.search(needle, k)), None)
                    if i is not None and i < len(body):
                        out.setdefault(field, body[i])
            if "매수가격" in keys and r + 1 < len(grid):
                i = keys.index("매수가격")
                if i < len(grid[r + 1]):
                    out.setdefault("price", grid[r + 1][i])
            if any("보유비율" in k for k in keys):
                for body in grid[r + 1:]:
                    if compact(body[0]) == "합계":
                        out.setdefault("post_stake_pct", body[-1])
    return out


def parse_tender_offer_document(markup: str, report_name: str = "") -> dict[str, Any]:
    """공개매수 공시 본문 → `{status, kind, fields, section_map, read_hints}`.

    `status`
      - `parsed`            칸 이름으로 값을 하나 이상 읽었다
      - `terms_absent`      서식(장)은 보이는데 값 칸을 못 읽었다 — `read_hints` 의 절을 직접 읽어라
      - `form_unrecognized` 아는 서식이 아니다(철회·의견표명서 등) — 원문을 직접 봐야 한다
    """
    kind = _kind(report_name)
    sections = _sections(markup)
    section_map = [{"no": s["no"], "title": s["title"]} for s in sections if s["title"]][:24]
    by_key = {key: next((s for s in sections if needle in compact(s["title"])), None) for key, needle in _SECTION_KEYS.items()}
    hints = {key: s["no"] for key, s in by_key.items() if s}
    fields: dict[str, Any] = {}
    offerors, purpose_checked, purpose_note = _parse_offerors(markup)
    if offerors:
        fields["offerors"] = offerors
    if purpose_checked:
        fields["purpose_checked"] = purpose_checked
    if purpose_note:
        fields["purpose_note"] = purpose_note

    if kind in {"filing", "prospectus"}:
        if by_key["terms"]:
            fields.update(_parse_terms(by_key["terms"]["html"]))
        else:  # 정정신고서 등 장 구조가 다른 본문 — 문서 전체에서 칸 이름으로만 찾는다
            fields.update(_parse_terms(markup))
        if not fields.get("period_start"):
            # 옛 서식(2021~2023 초)은 기간 표 없이 요약정보 칸에 「공개매수기간 2021. 04. 22. - 2021. 05. 12.(결제일: …)」 한 줄로 쓴다.
            found = _PERIOD_TEXT.search(_text(markup[:200_000]))
            if found:
                fields["period_start"], fields["period_end"] = (" ".join(g.split()) for g in found.groups()[:2])
                if found.group(3):
                    fields.setdefault("settlement_date", " ".join(found.group(3).split()))
        if by_key["purpose"]:
            fields["purpose_excerpt"] = _text(by_key["purpose"]["html"])[:800]
        status = "parsed" if fields.get("offers") or fields.get("period_start") else "terms_absent"
    elif kind == "result":
        fields.update(_parse_result(markup))
        status = "parsed" if fields.get("tendered") or fields.get("purchased") else "terms_absent"
    elif kind == "opinion":
        text = _text(markup)
        # 의견 칸 이름이 서식마다 달라 값으로 못 박지 않는다 — 낱말 둘레 글만 건넨다(판단은 읽는 쪽이).
        fields.update(_parse_opinion(text))
        if "opinion" not in fields:
            fields["opinion_hints"] = [text[max(0, m.start() - 40): m.end() + 60] for m in list(_OPINION.finditer(text))[:3]]
        status = "parsed" if "opinion" in fields else "form_unrecognized"
    else:
        fields["excerpt"] = _text(markup)[:800]
        status = "form_unrecognized"

    return {
        "status": status,
        "kind": kind,
        "fields": {k: v for k, v in fields.items() if v},
        "section_map": section_map,
        "read_hints": hints,
    }


async def enrich_tender_offer_rows(rows: list[dict[str, Any]], *, max_lookups: int = TENDER_DOC_LOOKUPS) -> dict[str, int]:
    """공개매수 공시 row 의 본문을 열어(최대 max_lookups 건, 캐시) `tender` 를 붙인다. 못 연 것은 이유를 남긴다.

    row 에 붙는 것: `document_status`(parsed / terms_absent / form_unrecognized / no_body / fetch_failed / not_looked_up)
    · `tender`(위 파서의 fields·section_map·read_hints). 설명서는 신고서와 같은 내용이라 신고서 뒤로 미룬다.
    """
    client = get_dart_client()
    stats = {"lookups": 0, "parsed": 0, "terms_absent": 0, "form_unrecognized": 0, "fetch_failed": 0, "no_body": 0, "not_looked_up": 0}
    order = sorted(
        (r for r in rows if r.get("rcept_no")),
        key=lambda r: _kind(r.get("report_name", "")) == "prospectus",
    )
    targets = order[:max_lookups]
    for r in rows:
        r.setdefault("document_status", "not_looked_up")
    stats["not_looked_up"] = len(rows) - len(targets)

    async def one(row: dict[str, Any]) -> None:
        try:
            doc = await client.get_document_cached(row["rcept_no"])
        except Exception as exc:  # noqa: BLE001 — 못 열면 목록은 그대로 두고 이유만 남긴다
            # 014(파일이 존재하지 않음) = 첨부만 바꾼 [첨부정정] 처럼 본문 파일이 없는 공시. 우리 조회 실패가 아니다.
            status = "no_body" if isinstance(exc, DartClientError) and str(getattr(exc, "status", "")) == "014" else "fetch_failed"
            row["document_status"] = status
            stats[status] += 1
            return
        parsed = parse_tender_offer_document((doc or {}).get("html", "") or "", row.get("report_name", ""))
        row["document_status"] = parsed["status"]
        row["tender"] = {k: parsed[k] for k in ("kind", "fields", "section_map", "read_hints")}
        stats["lookups"] += 1
        stats[parsed["status"]] += 1

    await asyncio.gather(*(one(r) for r in targets))
    # 정정신고서·정정 결과보고서는 표지에 공개매수자 이름이 없는 서식이 있다 — 같은 제출인의 다른 공시에서 읽은 이름을 가져오되 출처를 밝힌다.
    donors: dict[str, dict[str, Any]] = {}
    for r in targets:
        if ((r.get("tender") or {}).get("fields") or {}).get("offerors"):
            donors.setdefault(r.get("filer_name", ""), r)
    for r in targets:
        tender = r.get("tender")
        if not tender or tender["fields"].get("offerors") or tender["kind"] not in {"filing", "prospectus", "result"}:
            continue
        donor = donors.get(r.get("filer_name", ""))
        if donor:
            tender["fields"]["offerors"] = [{"name": o["name"]} for o in donor["tender"]["fields"]["offerors"]]
            tender["fields"]["offerors_from"] = donor["rcept_no"]
    return stats
