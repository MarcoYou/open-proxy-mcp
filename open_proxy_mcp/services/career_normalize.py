# -*- coding: utf-8 -*-
"""소집공고 「후보자의 주된직업ㆍ세부경력」 표를 **경력 한 줄 = 한 항목**으로 편다.

왜 필요한가 — 우리 단위는 표의 한 행(후보 한 명)이었다. 그래서 기간과 경력이 한 칸에
뭉쳐 들어오면 갈라내지 못했고, 겸직 수 같은 파생값이 조용히 틀렸다(2026-08-28 실측
한국앤컴퍼니 이행희 — 원문엔 사외이사 두 곳, 우리 응답엔 「겸직 1곳」).

서식은 두 갈래다 (2026-08-29 표본 10사 실측).
  · **행 갈림** (3/10) — 경력 줄마다 TR 이 따로다. 기간·내용이 1:1 로 붙어 있다.
  · **한 칸 뭉침** (7/10) — 한 칸에 몰려 있다. 개수가 맞으면 순서대로 짝짓고,
    안 맞으면 **짝짓지 않는다**(짝을 지어내면 없는 사실이 생긴다).

🔴 **주된직업 칸은 버리지 않는다** (2026-08-29 마스터 지시 — 「현재 뭐하고있는지도 중요」).
표본 26명 중 25명이 채워져 있고, 기간이 없는 대신 **지금 무엇을 하는가**를 말한다.
"""
from __future__ import annotations

import re
from typing import Any

# ── 기간 ────────────────────────────────────────────────────────────────
_OPEN_END = ("현재", "現", "재직", "至今", "present", "Present", "")
_SEP = r"[~\-–—∼]"
# 연도: 1988 · 2024 · '21 · 22 (두 자리는 앞에 따옴표가 있을 때만 — 숫자 오인 방지)
_Y = r"(?:(?:19|20)\d{2}|'\d{2})"
# 「2013년~현재」·「2022년 9월~현재」·「2015.03」을 모두 받는다.
# 년 은 월 없이도 붙는다 — 그것만으로 구간이 깨지면 안 된다(실측).
_YM = _Y + r"\s*년?(?:\s*[.\-/]\s*\d{1,2}\s*월?|\s*\d{1,2}\s*월)?"
_RANGE = re.compile(r"(%s)\s*%s\s*(%s|현재|現|재직|至今|present)?" % (_YM, _SEP, _YM), re.I)
_SINGLE = re.compile(r"(?<![\d~\-–])((?:19|20)\d{2})(?!\s*%s)(?![\d.])" % _SEP)


def _year(tok: str) -> str | None:
    """'21 → 2021 · 2015.03 → 2015-03 · 2015 → 2015-null"""
    if not tok:
        return None
    t = tok.strip().replace("년", ".").replace("월", "").replace("/", ".").replace("-", ".")
    t = t.strip(". ")
    if t.startswith("'"):
        n = t[1:3]
        if not n.isdigit():
            return None
        # 두 자리 연도 — 00~29 는 2000년대, 그 밖은 1900년대로 본다
        t = ("20" + n) if int(n) <= 29 else ("19" + n)
        rest = tok.split(".")[1:] if "." in tok else []
        return "%s-%s" % (t, ("%02d" % int(rest[0])) if rest else "null")
    parts = [p for p in t.split(".") if p.strip().isdigit()]
    if not parts:
        return None
    y = parts[0]
    if len(y) != 4:
        return None
    m = ("%02d" % int(parts[1])) if len(parts) > 1 and 1 <= int(parts[1]) <= 12 else "null"
    return "%s-%s" % (y, m)


def norm_period(raw: str) -> dict[str, Any]:
    """기간 문자열 하나 → {raw, start, end, open_ended}. 못 읽으면 start/end 가 None."""
    text = (raw or "").strip()
    m = _RANGE.search(text)
    if m:
        end_tok = (m.group(2) or "").strip()
        open_ended = end_tok in _OPEN_END
        return {"raw": text, "start": _year(m.group(1)),
                "end": None if open_ended else _year(end_tok),
                "open_ended": open_ended}
    m = _SINGLE.search(text)
    if m:                                   # 「2012」처럼 한 해만 적힌 것 (학위 취득 등)
        y = _year(m.group(1))
        return {"raw": text, "start": y, "end": y, "open_ended": False}
    return {"raw": text or None, "start": None, "end": None, "open_ended": False}


def split_periods(cell: str) -> list[dict[str, Any]]:
    """뭉친 기간 칸을 구간 목록으로. 못 읽은 글자는 버리지 않고 residue 로 남긴다."""
    text = (cell or "").strip()
    marks = []          # (시작위치, 구간)
    for m in _RANGE.finditer(text):
        marks.append((m.start(), norm_period(m.group(0))))
    rest = re.sub(_RANGE, "\x00", text)
    # 구간을 걷어낸 자리에 남은 **단독 연도**도 항목이다(학위 취득 등).
    pos = 0
    for chunk in rest.split("\x00"):
        for m in _SINGLE.finditer(chunk):
            marks.append((text.find(m.group(1), pos), norm_period(m.group(1))))
            pos = max(pos, text.find(m.group(1), pos) + 4)
    marks.sort(key=lambda x: (x[0] if x[0] >= 0 else 10 ** 6))
    out = [p for _, p in marks]
    residue = re.sub(r"\d", "", rest.replace("\x00", "")).strip(" ,.~-–—")
    return out, (residue or None)


# ── 경력 내용 ────────────────────────────────────────────────────────────
# 글머리표 — 「근무- ㈜동양고속」처럼 **앞 글자에 붙어** 오는 경우가 많다(실측 동양고속).
# 앞에 공백을 요구하면 한 줄도 못 끊는다. 다만 연도 뒤 붙임표(1983-2016)는 피한다.
_BULLET = re.compile(r"(?<!\d)\s*[-·•▪‧]\s+")
# 글머리표가 없으면 **다른 것이 글머리표 노릇**을 한다.
#   · 「現/前」 표시 (실측 KISCO홀딩스)
#   · 「(현)/(전)」 괄호 표시 (실측 블루산업개발)
#   · 🔴 **법인 접두어** — (주)·㈜·(유)·(재)·(사)·(학). 회사 이름이 항목의 시작이다
#     (실측 팜젠사이언스 「…정보보호학 석사(주)다온네트웍스 사장(주)DSD삼호 …」).
# 🔴 괄호 **안**의 「現」은 항목 머리가 아니다 — 「현중기술대학(現 현대중공업공과대학) 졸업」은
# 한 항목이다(실측 산일전기). 여는 괄호 바로 뒤면 경계로 보지 않는다.
_ERA_MARK = re.compile(
    r"(?<![(（])(?=(?:\(현\)|\(전\)|\(現\)|\(前\)|現[\s,]|前[\s,]))")

# 🔴 **법인 표기는 앞에도 뒤에도 붙는다.** 「(주)다온네트웍스」는 항목의 **시작**이지만
# 「교보생명보험(주) 전무」는 이름의 **꼬리**다. 앞뒤 글자로는 못 가른다 —
# **뒤에 공백이 오는가**로 가른다(꼬리면 「(주) 전무」처럼 띄고, 머리면 이름이 바로 붙는다).
# 이 규칙을 안 두면 「교보생명보험 / (주) 전무…」로 회사 이름이 두 동강 난다(실측).
# 뒤에 **공백만** 예외로 두면 회사 이름 나열에서 잘린다 — 「코오롱에코원㈜, 코오롱환경에너지㈜, …」
# 를 「코오롱에코원 / ㈜, 코오롱환경에너지 / …」로 쪼갰다(실측 코오롱글로벌).
# 쉼표·마침표·닫는괄호·가운뎃점도 **앞 이름의 꼬리**다.
# 앞이 「, 」이면 **회사 나열의 이어짐**이지 새 항목이 아니다 —
# 「코오롱엘에스아이㈜, ㈜엠오디 대표이사」는 한 항목이다(실측).
_ORG_HEAD = re.compile(
    r"(?<![,、·])(?<!,\s)"
    r"(?=(?:\(주\)|㈜|\(유\)|\(재\)|\(사\)|\(학\)|\(합\))(?![\s,、.·)）\]]))")

_LINEBREAK = re.compile(r"[\n\r]+")


def _boundaries(text: str) -> list[str]:
    """항목 경계를 **한꺼번에** 본다. 갈래를 배타적으로 두면 하나만 걸려도 나머지를 놓친다
    (실측 코오롱글로벌 — 줄바꿈으로 2조각, 「前,」까지 봐야 6조각)."""
    parts = [text]
    for rx in (_BULLET, _LINEBREAK, _ERA_MARK, _ORG_HEAD):
        nxt = []
        for p in parts:
            nxt.extend(rx.split(p))
        parts = nxt
    out: list[str] = []
    for p in parts:
        t = (p or "").strip(" -·•▪‧,")
        if not t:
            continue
        # 「前, ㈜코오롱…」처럼 시대 표시와 법인 표기가 잇달아 오면 경계가 두 번 잡혀
        # 「前」만 남은 조각이 생긴다. 버리지 않고 **다음 항목에 되붙인다.**
        if t in ("現", "前", "(현)", "(전)", "(現)", "(前)"):
            out.append(("\x01", t))  # 표시자 — 아래에서 합친다
            continue
        if out and isinstance(out[-1], tuple):
            mark = out.pop()[1]
            t = "%s %s" % (mark, t)
        out.append(t)
    return [x for x in out if isinstance(x, str)]


_ROLE_TAIL = (
    "사외이사", "독립이사", "사내이사", "감사위원", "감사", "대표이사", "부회장", "회장",
    "사장", "부사장", "전무", "상무", "이사", "본부장", "부문장", "사업부장", "팀장",
    "실장", "위원장", "위원", "고문", "자문", "교수", "변호사", "회계사", "세무사", "원장",
    "부장", "차장", "과장", "담당임원", "학장", "부총장", "총장",
)


def split_items(cell: str) -> list[str]:
    """한 칸에 뭉친 경력을 줄로 끊는다. 글머리표가 없으면 줄바꿈으로."""
    text = (cell or "").strip()
    if not text:
        return []
    parts = _boundaries(text)
    return parts or [text]


def split_org_role(item: str) -> tuple[str | None, str | None]:
    """「㈜무신사 사외이사 (보상위원장)」 → (㈜무신사, 사외이사).

    꼬리의 직위 낱말을 떼어 기관과 역할을 가른다. 못 가르면 **둘 다 원문 그대로 두지 않고
    org 에만 넣는다** — 없는 역할을 지어내지 않는다.
    """
    t = (item or "").strip()
    if not t:
        return None, None
    body = re.sub(r"\s*\([^)]*\)\s*$", "", t).strip()      # 꼬리 괄호(위원장 등) 제거
    for kw in sorted(_ROLE_TAIL, key=len, reverse=True):
        if body.endswith(kw):
            org = body[: -len(kw)].strip(" ,/")
            return (org or None), kw
        i = body.find(kw + " ")
        if i > 0 and body[i + len(kw):].strip() == "":
            return body[:i].strip(), kw
    return body or None, None


# ── 표 → 경력 항목 ───────────────────────────────────────────────────────
def build_careers(period_cell: str, content_cell: str) -> dict[str, Any]:
    """기간 칸 + 내용 칸 → 경력 항목 목록.

    개수가 안 맞으면 **짝짓지 않는다.** 항목은 그대로 주고 기간은 따로 준다 —
    읽는 쪽이 원문으로 맞출 수 있게. 지어낸 짝보다 낫다.
    """
    periods, residue = split_periods(period_cell)
    items = split_items(content_cell)
    aligned = len(periods) == len(items) and bool(items)
    careers = []
    for idx, it in enumerate(items):
        org, role = split_org_role(it)
        p = periods[idx] if aligned else None
        careers.append({
            "raw": it,
            "org": org,
            "role": role,
            "periodRaw": (p or {}).get("raw"),
            "start": (p or {}).get("start"),
            "end": (p or {}).get("end"),
            "open_ended": (p or {}).get("open_ended") if p else None,
        })
    return {
        "careers": careers,
        "aligned": aligned,
        "period_count": len(periods),
        "item_count": len(items),
        "periods": periods if not aligned else None,   # 못 붙인 기간은 따로 싣는다
        "period_residue": residue,
    }
