"""거래소 시장조치·규제 계열 공시 파서.

`risk_events` 가 원래 보던 6종(중대재해·횡령배임·파생손실·회생부도·생산중단·해산)
바깥의 계열을 담는다. 파서 본체는 Agent D(`lab/260814-dart-push/enrich.py`)에서
옮겨 왔다 — 2026 코스피·코스닥 표본으로 이미 맞춰 둔 것들이다.

🔴 **채널이 하나 더 있다** (2026-08-27 실측, 20260701~20260827 전수).
매매거래정지 214 · 관리종목 113 · 상장적격성 34 · 개선기간 17 · 정리매매 17 건이
전부 **I003** 에 있다. `risk_events` 는 I001+B001 만 보고 있어 이 계열을 한 건도
읽지 못했다. 같은 기간 I001 에서는 자본잠식 48 · 투자판단 46 · 소송등의 34 ·
풍문 24 · 해명 17 · 조회공시 13 · 청약결과 8 · 만기전취득 6 건이 나왔다.
(I001 은 12페이지까지만 읽어 실제 건수는 이보다 많다.)

파서는 `risk_events._extract_text()` 가 만든 텍스트를 그대로 받는다.
"""

from __future__ import annotations

import re
from typing import Any

# ── 공통 헬퍼 (D `enrich.py` 이식) ─────────────────────────────


def _clip(s: str | None, n: int) -> str | None:
    """길면 자르고 **잘랐다는 표시를 남긴다.** 표시가 없으면 문장이 거기서
    끝난 것으로 읽힌다."""
    if not s:
        return None
    v = " ".join(s.split())
    return v if len(v) <= n else v[:n] + "…"


def _won(s: str | None) -> int | None:
    try:
        return int(s.replace(",", ""))  # type: ignore[union-attr]
    except (ValueError, AttributeError):
        return None


def _flat(text: str | None) -> str:
    return " ".join((text or "").split())


_DATE = r"(\d{4}\s*[년.\-]\s*\d{1,2}\s*[월.\-]\s*\d{1,2}\s*일?|\d{4}-\d{2}-\d{2})"


def _ymd(s: str | None) -> str | None:
    """`2026년 01월 07일` → `2026-01-07`. 이미 그 꼴이면 그대로."""
    m = re.match(r"(\d{4})\s*[년.\-]\s*(\d{1,2})\s*[월.\-]\s*(\d{1,2})", s or "")
    return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3))) if m else s


def _cut(body: str, limit: int = 90, keep: int = 4) -> list[str]:
    """문장으로 먼저 가르고, 문장 끝이 없는 개조식 판만 가름표로 되돌린다."""
    parts = [p.strip() for p in re.split(r"(?<=다\.)\s+|(?<=음\.)\s+", body or "") if p.strip()]
    if len(parts) <= 1:
        parts = [p.strip(" -·:.") for p in re.split(r"\s*[·ㆍ]\s*|\s+-\s+", body or "")]
    out = []
    for q in parts:
        q = q.strip(" -·:.")
        if len(q) < 8:
            continue
        c = _clip(q, limit)
        if c and c not in out:
            out.append(c)
    return out[:keep]


def _prune(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v not in (None, "", "-", [])}


# ── 매매거래정지 ───────────────────────────────────────────────

# 🔴 **서식이 둘이다** (2026-08-27 I003 전수 표본 221건).
# ① 거래소 안내문 — `1.대상종목 / 2.(정지|해제|변경)사유 / 3.(정지기간|해제일시) /
#    4.근거규정 / 5.기타`. 번호 뒤에 공백이 없다. 표본 다수가 이 꼴이다.
# ② 회사 제출 서식 — `1.종목명 / 2~5.매매거래정지… / 6.근거 / 7.기타`.
#    Agent D 가 코스피 표본으로 맞춰 둔 것이 이쪽이다. 둘 다 받는다.
_HL_SUBJECT = re.compile(r"1\s*\.\s*(?:대상종목|종목명)\s*(.+?)\s*2\s*\.")
_HL_WHY_A = re.compile(r"2\s*\.\s*(정지|해제|변경)사유\s*(.+?)\s*3\s*\.")
_HL_WHEN_A = re.compile(r"3\s*\.\s*(정지기간|해제일시|정지일시)\s*(.+?)\s*4\s*\.\s*근거")
_HL_BASIS = re.compile(r"4\s*\.\s*근거\s*규정\s*(.+?)\s*5\s*\.")
_HL_TYPE = re.compile(r"매매거래정지\s*유형\s*(.+?)\s*\d\s*\.\s*매매거래정지\s*일시")
_HL_STOP = re.compile(r"매매거래정지\s*일시\s*(\d{4}-\d{2}-\d{2}(?:\s*\d{2}:\d{2})?|-)")
_HL_REL = re.compile(r"매매거래정지\s*해제일시\s*(\d{4}-\d{2}-\d{2}(?:\s*\d{2}:\d{2})?|-)")
_HL_WHY = re.compile(r"매매거래정지\s*(?:및\s*해제\s*)?사유\s*(.+?)\s*\d\s*\.\s*근거")
_HL_NOTE = re.compile(r"기타\s*투자판단과\s*관련한\s*중요사항\s*(.+?)\s*(?:※|\[파생시장안내\]|$)")

#: `사유` 라벨이 상태를 말한다. **우리가 판정하지 않는다** — 원문 라벨 그대로다.
_HL_STATE = {"정지": "정지", "해제": "해제", "변경": "정지기간 변경"}


def _strip_corp_prefix(t: str) -> str:
    """머리가 이미 회사 이름을 말한다. 제목 앞의 회사명은 덜어낸다."""
    t = _flat(t)
    t = re.sub(r"^주식회사\s*", "", t)
    t = re.sub(r"^\(?주\)?\s*", "", t)
    t = re.sub(r"^[^,]{2,20}(?:\(주\)|㈜)?\s*,\s*", "", t)
    return t


def parse_trading_halt(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    flat = _flat(text)
    out: dict[str, Any] = {}

    # ① 거래소 안내문
    m = _HL_SUBJECT.search(flat)
    if m:
        out["subject"] = _clip(m.group(1), 60)
    m = _HL_WHY_A.search(flat)
    if m:
        out["halt_state"] = _HL_STATE.get(m.group(1), m.group(1))
        out["reason"] = _clip(m.group(2), 120)
    m = _HL_WHEN_A.search(flat)
    if m:
        # 라벨이 `정지기간` 이면 기간(시작~조건), `해제일시` 면 시점이다.
        out["period_label"] = m.group(1)
        out["period"] = _clip(m.group(2), 150)
    m = _HL_BASIS.search(flat)
    if m:
        out["legal_basis"] = _clip(m.group(1), 80)

    # ② 회사 제출 서식 — 안내문에서 못 채운 칸만 메운다.
    if not out.get("reason"):
        m = _HL_TYPE.search(flat)
        if m:
            out["halt_type"] = _clip(m.group(1), 40)
        for key, pat in (("halted_at", _HL_STOP), ("resumed_at", _HL_REL)):
            m = pat.search(flat)
            if m and m.group(1) != "-":
                out[key] = _flat(m.group(1))
        m = _HL_WHY.search(flat)
        if m:
            out["reason"] = _clip(m.group(1), 120)
        m = _HL_NOTE.search(flat)
        if m and m.group(1).strip(" -."):
            out["note"] = _clip(m.group(1), 120)
        if out.get("halted_at") and out.get("resumed_at"):
            out["halt_state"] = "정지+해제"
        elif out.get("resumed_at"):
            out["halt_state"] = "해제"
        elif out.get("halted_at"):
            out["halt_state"] = "정지"

    if not out:
        out["summary_excerpt"] = _clip(flat, 400)
    return _prune(out)


# ── 상장적격성·관리종목·개선기간 (거래소 안내문) ───────────────
#
# 🔴 **번호 서식이 아니다** (2026-08-27 I003 표본 167건 중 다수).
# 거래소 안내문은 `제목 : …` 한 줄 뒤에 줄글이 이어진다. `1. 제목 / 2. 내용`
# 꼴은 소수라 **둘 다 받는다.** 머리(report_nm)에 사유가 괄호로 붙어 오므로
# 본문에서 사유를 못 찾아도 카드는 성립한다.
_DL_TITLE_N = re.compile(r"1\s*\.\s*제목\s*(.+?)\s*2\s*\.\s*내용", re.S)
_DL_BODY_N = re.compile(r"2\s*\.\s*내용\s*(.+?)\s*3\s*\.\s*기타", re.S)
_DL_TITLE_P = re.compile(r"제목\s*[:：]\s*(.{4,120}?)(?=\s(?:[가-힣]*시장\s*(?:업무규정|상장규정)|당사|동사|아래|다음|위)\s|$)", re.S)
# **우리가 판정하지 않는다.** 원문에 그 낱말이 있느냐만 본다.
_DL_WHY = [("감사의견 거절", r"감사의견\s*거절|의견\s*거절|의견거절"),
           ("감사범위 제한", r"감사범위\s*제한"),
           ("자본잠식", r"잠식"),
           ("시가총액 미달", r"시가총액[^.]{0,20}미[달만]"),
           ("매출액 미달", r"매출액[^.]{0,20}미[달만]"),
           ("상장주식수 미달", r"상장주식\s*수?\s*미달"),
           ("회계처리기준 위반", r"회계처리기준\s*위반"),
           ("주된 영업 정지", r"주된\s*영업\s*정지|영업\s*정지|생산\s*중단"),
           ("횡령·배임", r"횡령|배임"),
           ("불성실공시", r"불성실공시"),
           ("신주인수권 행사기간 만료", r"신주인수권\s*행사기간\s*만료"),
           ("계속기업 불확실성", r"계속기업"),
           ("상장폐지기준 해당", r"상장폐지\s*기준에?\s*해당")]
_DL_DATE = r"'?\d{2,4}[.\s년]+\d{1,2}[.\s월]+\d{1,2}\s*일?"
_DL_DUE = re.compile(r"이의신청시한\s*[\d.]+\s*[限한]"
                     r"|개선기간[^.]{0,60}?" + _DL_DATE + r"\s*까지"
                     r"|" + _DL_DATE + r"\s*까지[^.]{0,20}개선기간"
                     r"|이의신청[^.]{0,20}?" + _DL_DATE + r"\s*까지")
# 근거 규정만 끊어 낸다 — 뒤에 붙는 「…에 따라 '보통주식의…」 까지 물면
# `content` 와 같은 말이 두 번 실린다.
_DL_BASIS = re.compile(r"((?:유가증권|코스닥|코넥스)?\s*시장\s*(?:상장규정|업무규정)"
                       r"\s*제\s*\d+\s*조(?:\s*(?:및|,)\s*[^,.]{0,25}?제\s*\d+\s*조)?)")


def parse_listing_review(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    flat = _flat(text)
    out: dict[str, Any] = {}
    body = ""
    m = _DL_TITLE_N.search(flat)
    if m:
        out["title"] = _clip(_strip_corp_prefix(m.group(1)), 70)
        mb = _DL_BODY_N.search(flat)
        body = _flat(mb.group(1)) if mb else ""
    else:
        m = _DL_TITLE_P.search(flat)
        if m:
            out["title"] = _clip(_strip_corp_prefix(m.group(1)), 70)
            body = _flat(flat[m.end():])
    if not body:
        body = flat
    why = [lab for lab, pat in _DL_WHY if re.search(pat, body)]
    # `상장폐지기준 해당` 은 거의 모든 안내에 나오는 말이라 **다른 사유를
    # 하나도 못 찾았을 때만** 쓴다. 같이 적으면 진짜 사유가 묻힌다.
    real = [w for w in why if w != "상장폐지기준 해당"]
    why = real[:2] or why[:1]
    if why:
        out["reason"] = " · ".join(why)
    d = _DL_DUE.search(body)
    if d:
        out["deadline"] = _flat(d.group(0))
    b = _DL_BASIS.search(flat)
    if b:
        out["legal_basis"] = _clip(b.group(1), 60)
    out["content"] = _cut(body, 120, 3)
    if not out.get("content"):
        out["summary_excerpt"] = _clip(flat, 400)
    return _prune(out)


# ── 조회공시 요구 ──────────────────────────────────────────────

_RQ_WHAT = re.compile(r"1\.\s*조회공시\s*요구내용\s*(.+?)\s*2\.\s*공시시한", re.S)
_RQ_DUE = re.compile(r"2\.\s*공시시한\s*(\d{4}-\d{2}-\d{2}(?:\s*\d{2}:\d{2})?)")


def parse_inquiry(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    flat = _flat(text)
    out: dict[str, Any] = {}
    m = _RQ_WHAT.search(flat)
    if m:
        out["requested"] = _clip(m.group(1), 80)
    m = _RQ_DUE.search(flat)
    if m:
        out["answer_due"] = _flat(m.group(1))
    if not out:
        out["summary_excerpt"] = _clip(flat, 400)
    return _prune(out)


# ── 풍문 또는 보도에 대한 해명 ─────────────────────────────────

_RM_WHAT = re.compile(r"1\.\s*풍문\s*또는\s*보도의\s*내용\s*(.+?)"
                      r"\s*2\.\s*풍문\s*또는\s*보도의\s*매체", re.S)
_RM_MEDIA = re.compile(r"2\.\s*풍문\s*또는\s*보도의\s*매체\s*(.+?)"
                       r"\s*3\.\s*풍문\s*또는\s*보도의\s*발생일자", re.S)
_RM_ON = re.compile(r"3\.\s*풍문\s*또는\s*보도의\s*발생일자\s*(\d{4}-\d{2}-\d{2})")
_RM_ANS = re.compile(r"4\.\s*풍문\s*또는\s*보도의\s*내용에\s*대한\s*해명내용\s*(.+?)"
                     r"\s*(?:5\.\s*재공시예정일|※|$)", re.S)
_RM_AGAIN = re.compile(r"5\.\s*재공시예정일\s*(\d{4}-\d{2}-\d{2})")


def parse_rumor(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    """칸 다섯이 그대로 온다. 회사가 한 답을 자르지 않는 것이 요점이다."""
    flat = _flat(text)
    out: dict[str, Any] = {}
    m = _RM_WHAT.search(flat)
    if m:
        out["report_content"] = _clip(m.group(1), 120)
    m = _RM_MEDIA.search(flat)
    if m:
        out["media"] = _clip(m.group(1), 24)
    m = _RM_ON.search(flat)
    if m:
        out["reported_on"] = m.group(1)
    m = _RM_AGAIN.search(flat)
    if m:
        out["redisclosure_due"] = m.group(1)
    m = _RM_ANS.search(flat)
    if m:
        body = _flat(m.group(1))
        # 첫 문장(`본 공시는 …입니다`)은 보도 줄과 같은 말이고, 꼬리의
        # `(공시책임자) …` 는 사람 이름이다. 재공시 약속은 위 칸이 말한다.
        parts = [re.sub(r"\(공시책임자\).*$", "", q).strip(" -·:.")
                 for q in _cut(body, 150, 8)]
        parts = [q for q in parts if len(q) >= 12
                 and not re.match(r"^본\s*공시는", q)
                 and "관련 해명공시" not in q
                 and not re.search(r"재공시\s*(?:하도록|할\s*예정|하겠|기한)", q)]
        # 넷까지 싣는다 — 셋으로 끊으면 보도에 대한 직접 답이 빠지는 판이 있다.
        out["clarification"] = parts[:4]
    if not out:
        out["summary_excerpt"] = _clip(flat, 400)
    return _prune(out)


# ── 투자판단 관련 주요경영사항 ─────────────────────────────────

_IV_TITLE = re.compile(r"1\.\s*제목\s*(.+?)\s*2\.\s*주요내용", re.S)
_IV_BODY = re.compile(r"2\.\s*주요내용\s*(.+?)\s*3\.\s*이사회결의일", re.S)
_IV_ON = re.compile(r"3\.\s*이사회결의일\(결정일\)\s*또는\s*사실확인일\s*(\d{4}-\d{2}-\d{2})")
_IV_SUB = re.compile(r"자회사인\s*(.+?)\s*의?\s*주요경영사항")
# 🔴 **`계약금` 은 총액이 아니다.** 착수금을 규모로 세우면 100억짜리 계약이
# 1,000억으로 보인다. 총액 라벨과 착수금 라벨을 갈라 둔다.
_IV_TOTAL = r"계약\s*규모|총\s*계약\s*금액|계약\s*금액|거래\s*금액|투자\s*금액|취득\s*금액"
_IV_AMT = re.compile(r"(" + _IV_TOTAL + r"|계약금|마일스톤[^:：(]{0,20})"
                     r"\s*(?:\([^)]{0,20}\))?\s*[:：]\s*"
                     r"[^\d]{0,40}?([\d,]+(?:\.\d+)?)\s*(억\s*원|원)")
_IV_TOTAL_RE = re.compile(_IV_TOTAL)
# 매출 대비는 회사가 원문에 적어 준다. 우리가 나누지 않는다.
_IV_PCT = re.compile(r"연결\s*매출액의\s*([\d.]+)\s*%")


def parse_investment_judgment(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    flat = _flat(text)
    out: dict[str, Any] = {}
    m = _IV_TITLE.search(flat)
    if m:
        out["title"] = _clip(m.group(1), 150)
    m = _IV_ON.search(flat)
    if m:
        out["decided_on"] = m.group(1)
    m = _IV_SUB.search(flat)
    if m:
        out["subsidiary_name"] = _clip(m.group(1), 30)
    amounts = []
    for lab, num, unit in _IV_AMT.findall(flat):
        v = _won(num) if "." not in num else None
        if v is None:
            try:
                v = float(num.replace(",", ""))
            except ValueError:
                continue
        if "억" in unit:
            v = int(v * 10 ** 8)
        amounts.append((re.sub(r"\s+", "", lab), int(v)))
    totals = [v for lab, v in amounts if _IV_TOTAL_RE.fullmatch(lab)]
    if totals:
        out["deal_size_won"] = max(totals)
    for lab, v in amounts:
        if lab == "계약금":
            out["upfront_won"] = v
        elif lab.startswith("마일스톤"):
            out["milestone_won"] = v
    m = _IV_PCT.search(flat)
    if m:
        out["revenue_ratio_pct"] = m.group(1)
    m = _IV_BODY.search(flat)
    if m:
        body = _flat(m.group(1))
        # 투자유의 문구는 회사마다 글자까지 같은 상투구라 알맹이가 아니다.
        body = re.sub(r"※?\s*투자유의사항.*?(?=\s\d\s*[.,]\s*[가-힣A-Za-z(])", " ", body, count=1)
        skip = re.compile(r"^(?:※\s*)?(?:투자유의|상기\s*내용은|본\s*공시는)")
        out["content"] = [q for q in _cut(body, 90, 8) if not skip.match(q)]
    if not out.get("content") and not out.get("title"):
        out["summary_excerpt"] = _clip(flat, 400)
    return _prune(out)


# ── 자본잠식 50% 이상 / 매출액 50억원 미만 ─────────────────────

_CI_KIND = re.compile(r"재무제표의\s*종류\s*(개별|별도|연결)")
_CI_END = re.compile(r"-\s*종료일\s*(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})")
_CI_BIG = re.compile(r"-\s*대규모법인여부\s*(\S+)")
_CI_RATE = re.compile(r"자본잠식률\(%\)[^×]*×\s*100\s+(-?[\d.,]+|-)\s+(-?[\d.,]+|-)")
_CI_WHY = re.compile(r"5\.\s*매출액\s*또는\s*손익구조\s*변동\s*주요원인\s*(.+?)"
                     r"\s*(?:\d+\.\s|※|$)", re.S)
_CI_NUM = r"(-?[\d,]+)"


def _ci_pair(flat: str, label: str) -> tuple[int | None, int | None]:
    m = re.search(re.escape(label) + r"\s+" + _CI_NUM + r"\s+" + _CI_NUM, flat)
    if not m:
        return None, None
    return _won(m.group(1)), _won(m.group(2))


def _ci_row(flat: str, label: str) -> tuple[int | None, str | None]:
    m = re.search(re.escape(label) + r"\s+" + _CI_NUM + r"\s+" + _CI_NUM
                  + r"\s+(-?[\d,]+)\s+(-?[\d.,]+|-)", flat)
    if not m:
        v, _ = _ci_pair(flat, label)
        return v, None
    return _won(m.group(1)), m.group(4)


def parse_capital_impairment(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    flat = _flat(text)
    out: dict[str, Any] = {}
    m = _CI_KIND.search(flat)
    if m:
        out["statement_basis"] = m.group(1)
    m = _CI_END.search(flat)
    if m:
        out["period_end"], out["prior_period_end"] = m.group(1), m.group(2)
    for key, label in (("revenue_won", "매출액"), ("operating_income_won", "영업이익"),
                       ("net_income_won", "당기순이익")):
        v, pct = _ci_row(flat, label)
        if v is not None:
            out[key] = v
            if pct and pct != "-":
                out[key.replace("_won", "_change_pct")] = pct
    cap, _ = _ci_pair(flat, "자본총계")
    if cap is not None:
        out["equity_won"] = cap
    stock, _ = _ci_pair(flat, "자본금")
    if stock is not None:
        out["paid_in_capital_won"] = stock
    m = _CI_RATE.search(flat)
    if m and m.group(1) != "-":
        out["impairment_rate_pct"] = m.group(1)
        if m.group(2) != "-":
            out["prior_impairment_rate_pct"] = m.group(2)
    m = _CI_BIG.search(flat)
    if m and m.group(1) in ("해당", "미해당"):
        out["large_corp"] = m.group(1)
    # 🔴 **단위 라벨이 틀린 공시가 있다.** 머리는 `(단위:천원)` 인데 값은 원으로
    # 적혀 있었다 — 그대로 읽으면 매출이 105조로 나간다. **원문끼리 어긋나는
    # 것으로 잡는다** — 대규모법인 `미해당` 은 자산총계 2조원 미만이라는 뜻인데
    # 천원으로 읽은 자산총계가 2조를 넘으면 둘이 안 맞는다.
    # **어느 쪽이 맞는지 우리가 고르지 않는다.** 금액 줄을 통째로 뺀다.
    assets, _ = _ci_pair(flat, "자산총계")
    if out.get("large_corp") == "미해당" and assets and assets >= 2_000_000_000_000:
        out["unit_conflict"] = True
        for k in ("revenue_won", "operating_income_won", "net_income_won",
                  "equity_won", "paid_in_capital_won"):
            out.pop(k, None)
            out.pop(k.replace("_won", "_change_pct"), None)
    m = _CI_WHY.search(flat)
    if m:
        out["cause"] = _clip(m.group(1), 120)
    return _prune(out)


# ── 벌금 등의 부과 ─────────────────────────────────────────────

_FN_KIND = re.compile(r"부과내역\s*종류\s*(.+?)\s*부과금액")
_FN_AMT = re.compile(r"부과금액\s*\(원\)\s*([\d,]+|-)")
_FN_DUE = re.compile(r"납부기한\s*(\d{4}-\d{2}-\d{2}|-)")
_FN_EQUITY = re.compile(r"자기자본\s*\(원\)\s*([\d,]+)")
_FN_RATIO = re.compile(r"자기자본대비\s*\(%\)\s*([\d.,]+|-)")
_FN_BODY = re.compile(r"2\.\s*부과기관\s*(.+?)\s*3\.\s*부과사유")
_FN_WHY = re.compile(r"3\.\s*부과사유\s*(.+?)\s*4\.\s*향후대책")
_FN_PLAN = re.compile(r"4\.\s*향후대책\s*(.+?)\s*\d+\.\s*확인")
_LAW_SEEN = re.compile(r"확인\s*(?:\(통지서접수\))?\s*일자\s*" + _DATE)
_LAW_FILED = re.compile(r"제기\s*[ㆍ·.]?\s*(?:신청)?일자\s*" + _DATE)


def parse_fine(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    flat = _flat(text)
    out: dict[str, Any] = {}
    for key, pat, cut in (("penalty_type", _FN_KIND, 30), ("authority", _FN_BODY, 40),
                          ("reason", _FN_WHY, 120), ("response_plan", _FN_PLAN, 150)):
        m = pat.search(flat)
        if m:
            v = _flat(m.group(1))
            # 기관 이름 뒤 영문 괄호는 뗀다 — `공정거래위원회(Fair Trade Commission)`.
            if key == "authority":
                v = re.sub(r"\s*\([A-Za-z][A-Za-z .,&'-]*\)\s*$", "", v).strip()
            out[key] = _clip(v, cut)
    m = _FN_AMT.search(flat)
    if m and m.group(1) != "-":
        out["amount_won"] = _won(m.group(1))
    m = _FN_EQUITY.search(flat)
    if m:
        out["equity_won"] = _won(m.group(1))
    m = _FN_RATIO.search(flat)
    if m and m.group(1) != "-":
        out["equity_ratio_pct"] = m.group(1)
    m = _FN_DUE.search(flat)
    if m and m.group(1) != "-":
        out["payment_due"] = m.group(1)
    m = _LAW_SEEN.search(flat)
    if m:
        out["confirmed_date"] = _ymd(m.group(1))
    if not out:
        out["summary_excerpt"] = _clip(flat, 400)
    return _prune(out)


# ── 증권 관련 집단소송 ─────────────────────────────────────────

_CA_NO = re.compile(r"소송내역\s*사건번호\s*(.+?)\s*피고\s")
_CA_DEF = re.compile(r"피고\s*(.+?)\s*원고\s")
_CA_PLA = re.compile(r"원고\s*(.+?)\s*소송대리인")
_CA_LAW = re.compile(r"소송대리인\s*(.+?)\s*청구취지")
_CA_GIST = re.compile(r"청구취지\s*및\s*주요이유\s*(.+?)\s*총원의\s*범위")
_CA_SIZE = re.compile(r"총원의\s*범위\s*\(명\)\s*([\d,]+|-)")
_CA_COURT = re.compile(r"2\.\s*관할법원\s*(.+?)\s*3\.\s*향후대책")


def parse_class_action(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    flat = _flat(text)
    # **정정 공시는 앞에 정정 표가 붙는다.** 본문은 `1. 소송내역` 부터다.
    i = flat.find("소송내역")
    scope = flat[i:] if i > 0 else flat
    out: dict[str, Any] = {}
    for key, pat, cut in (("case_no", _CA_NO, 60), ("defendant", _CA_DEF, 60),
                          ("plaintiff", _CA_PLA, 60), ("counsel", _CA_LAW, 40),
                          ("claim", _CA_GIST, 600), ("court", _CA_COURT, 40)):
        m = pat.search(scope)
        if m:
            out[key] = _clip(m.group(1), cut)
    m = _CA_SIZE.search(scope)
    if m and m.group(1) != "-":
        out["class_size"] = m.group(1)
    m = _LAW_FILED.search(scope)
    if m:
        out["filed_date"] = _ymd(m.group(1))
    m = _LAW_SEEN.search(scope)
    if m:
        out["confirmed_date"] = _ymd(m.group(1))
    if not out:
        out["summary_excerpt"] = _clip(flat, 400)
    return _prune(out)


# ── 소송 등의 제기·판결 ────────────────────────────────────────

_LAW_NAME = re.compile(r"1\.\s*사건의\s*명칭\s*(.+?)\s*(?:사건번호|2\.\s*원고)")
_LAW_NO = re.compile(r"사건번호\s*([0-9]{4}[가-힣]{1,3}\s*[0-9]+)")
_LAW_NO_SENT = re.compile(r"사건번호는[^.]{0,30}?([0-9]{4}\s*[가-힣]{1,3}\s*[0-9]+)")
_LAW_PLAINTIFF = re.compile(r"2\.\s*원고\s*[ㆍ·]?\s*(?:\([^)]{0,20}\)|신청인)?\s*(.+?)\s*\d+\.\s*")
_LAW_CLAIM = re.compile(r"3\.\s*청구내용\s*(.+?)\s*\d+\.\s*(?:청구금액|관할법원)", re.S)
_LAW_JR = r"(?:판결|결정)\s*[ㆍ·.]?\s*(?:결정|판결)?"
_LAW_RULING = re.compile(r"3\.\s*" + _LAW_JR + r"내용\s*(.+?)\s*\d+\.\s*", re.S)
_LAW_AMT = re.compile(r"(?:청구금액|" + _LAW_JR + r"금액)\s*\(원\)\s*([\d,]+)")
_LAW_EQUITY = re.compile(r"자기자본\s*\(원\)\s*([\d,]+)")
_LAW_RATIO = re.compile(r"자기자본대비\s*\(%\)\s*([\d.,]+)")
_LAW_COURT = re.compile(r"관할법원\s*(.+?)\s*\d+\.\s*향후대책")
_LAW_JUDGED = re.compile(_LAW_JR + r"일자\s*" + _DATE)


def parse_lawsuit(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    flat = _flat(text)
    out: dict[str, Any] = {}
    m = _LAW_NAME.search(flat)
    if m:
        out["case_name"] = _clip(m.group(1), 60)
    m = _LAW_NO.search(flat) or _LAW_NO_SENT.search(flat)
    if m:
        out["case_no"] = re.sub(r"\s+", "", m.group(1))
    m = _LAW_PLAINTIFF.search(flat)
    if m:
        out["plaintiff"] = _clip(m.group(1), 80)
    m = _LAW_CLAIM.search(flat)
    if m:
        out["claim"] = _clip(m.group(1), 400)
    m = _LAW_RULING.search(flat)
    if m:
        out["ruling"] = _clip(m.group(1), 400)
    m = _LAW_AMT.search(flat)
    if m:
        out["amount_won"] = _won(m.group(1))
    m = _LAW_EQUITY.search(flat)
    if m:
        out["equity_won"] = _won(m.group(1))
    m = _LAW_RATIO.search(flat)
    if m:
        out["equity_ratio_pct"] = m.group(1)
    m = _LAW_COURT.search(flat)
    if m:
        out["court"] = _clip(m.group(1), 40)
    m = _LAW_FILED.search(flat)
    if m:
        out["filed_date"] = _ymd(m.group(1))
    m = _LAW_JUDGED.search(flat)
    if m:
        out["judged_date"] = _ymd(m.group(1))
    if not out:
        out["summary_excerpt"] = _clip(flat, 400)
    return _prune(out)


# ── 카테고리 등록 ──────────────────────────────────────────────
#
# 🔴 **기존 6종 뒤에 둔다.** `risk_events._classify` 는 위에서부터 먼저 걸리는
# 것이 이기는데, 앞에 두면 `주권매매거래정지(횡령·배임혐의발생)` 이 지금의
# `embezzlement` 에서 `trading_halt` 로 옮겨 간다 — 검증이 끝난 분류를
# 조용히 바꾸지 않는다. 대신 사유가 붙지 않은 순수 정지 공시만 여기로 온다.
# 되돌리려면 이 dict 를 `_CATEGORIES` 앞에 병합하면 된다.
#
# channels: 그 유형이 실제로 사는 list.json `pblntf_detail_ty` (2026-08-27 실측).
EXTRA_CATEGORIES: dict[str, dict[str, Any]] = {
    "trading_halt": {
        "label": "매매거래정지",
        "keywords": ("매매거래정지", "정리매매"),
        "channels": ("I003",),
    },
    "listing_review": {
        "label": "상장적격성·관리종목",
        "keywords": ("상장적격성", "관리종목", "개선기간", "상장폐지"),
        "channels": ("I003", "I001"),
    },
    "inquiry_disclosure": {
        "label": "조회공시·풍문해명",
        "keywords": ("조회공시", "풍문", "해명", "시황변동"),
        "channels": ("I001", "I003"),
    },
    "investment_judgment": {
        "label": "투자판단 주요경영사항",
        "keywords": ("투자판단관련주요경영사항",),
        "channels": ("I001",),
    },
    "litigation": {
        "label": "소송·제재",
        "keywords": ("소송등의", "벌금등의부과", "증권관련집단소송", "과징금"),
        "channels": ("I001", "B001"),
    },
    "capital_impairment": {
        "label": "자본잠식",
        "keywords": ("자본잠식",),
        "channels": ("I001",),
    },
}

EXTRA_PARSERS = {
    "trading_halt": parse_trading_halt,
    "listing_review": parse_listing_review,
    "investment_judgment": parse_investment_judgment,
    "capital_impairment": parse_capital_impairment,
}


def parse_inquiry_or_rumor(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    """한 카테고리에 서식이 둘이다 — 거래소 요구문과 회사 해명문."""
    flat = _flat(text)
    if "조회공시 요구내용" in flat or "조회공시요구내용" in flat:
        return parse_inquiry(text, lines, stage)
    return parse_rumor(text, lines, stage)


def parse_litigation(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    """소송 계열 서식 셋을 원문 표지로 가른다."""
    flat = _flat(text)
    if "부과내역" in flat or "부과기관" in flat:
        return parse_fine(text, lines, stage)
    if "총원의 범위" in flat or "총원의범위" in flat:
        return parse_class_action(text, lines, stage)
    return parse_lawsuit(text, lines, stage)


EXTRA_PARSERS["inquiry_disclosure"] = parse_inquiry_or_rumor
EXTRA_PARSERS["litigation"] = parse_litigation


# ── 「길을 터준다」 — 원문 어디를 보나 · 없으면 어디로 가나 ──────
#
# 🔴 **파서를 늘리는 대신 길을 준다** (2026-08-28 오너 지시).
# 거래소 시장조치 서식은 종류가 많아 정형 필드로 다 못 받는다. 그래서
# ① 그 정보가 있을 자리를 가리키고 ② 창을 넓힐 손잡이를 알리고
# ③ 거기 없을 때 볼 대안을 주고 ④ 그래도 안 되면 갈 길을 남긴다.
#
# 실측 표본(2026-08 I003)에서 관리종목·상장적격성 안내문은 200~750자로 짧다 —
# **문서가 통째로 창 안에 들어온다.** 사유·유예기간·해소요건은 정형 칸이 아니라
# 줄글에 있어서, 잘라 낸 요약만 주면 「25매매거래일 지속 / 5거래일 남음」 같은
# 판단 재료가 통째로 사라진다.
EXTRA_DETAIL_GUIDE: dict[str, dict[str, Any]] = {
    "trading_halt": {
        "where": "정지 서식은 칸이 다섯이다 — `2.정지(해제·변경)사유` 에 사유, "
                 "`3.정지기간` 의 `가.정지일시`·`나.만료일시` 에 언제까지인지, "
                 "`5.기타` 에 실질심사 사유 같은 단서가 붙는다. "
                 "만료일시가 날짜가 아니라 「…결정일까지」 같은 조건문인 판이 흔하다.",
        "alt": "같은 날 같은 회사의 「기타시장안내」 에 배경 설명이 따로 나간다. "
               "풀린 시점은 「주권매매거래정지해제」, 기간이 바뀐 것은 「주권매매거래정지 기간변경」.",
        "next": "타임라인 표의 접수번호 링크가 거래소 원문이다. 해소요건·유예기간이 이 공시에 "
                "없으면 같은 회사의 `listing_review` 카테고리 공시를 함께 부를 것.",
    },
    "listing_review": {
        "where": "거래소 안내문은 표가 아니라 줄글이다 — `제목 :` 줄에 사유가, 이어지는 문단에 "
                 "근거 규정 · 지정 후 경과일수 · 해소요건(며칠 이상 충족) · 기한이 순서대로 나온다. "
                 "「관리종목 지정 후 경과일수」 · 「해제요건 : 10일 이상」 같은 문장이 그 자리다.",
        "alt": "개선기간과 그 종료일은 「코스닥시장위원회 심의·의결 결과 및 개선기간 부여 안내」 에, "
               "언제까지 거래가 막히는지는 같은 회사의 「주권매매거래정지 기간변경」 에 있다. "
               "이의신청 시한은 「상장폐지 결정 안내」 에 붙는다.",
        "next": "재무 사유(자본잠식·매출액 미달)면 `financial_metrics`, 시가총액·주가 사유면 "
                "`price_multiple_data` 로 얼마나 모자란지 확인. 규정 조문은 `law_lookup`.",
    },
    "inquiry_disclosure": {
        "where": "거래소 요구문은 `1.조회공시 요구내용` · `2.공시시한` 두 칸이다. "
                 "회사 해명문은 칸이 다섯 — 보도 내용 · 매체 · 발생일자 · 해명내용 · 재공시예정일.",
        "alt": "회사가 답한 공시는 요구일 다음 영업일에 따로 접수된다 — 요구문만 보고 "
               "「답이 없다」고 읽지 말 것.",
        "next": "답변 공시가 안 보이면 같은 회사·같은 주간을 `company` 의 공시 목록으로 훑을 것.",
    },
    "investment_judgment": {
        "where": "`1.제목` 과 `2.주요내용` 이 본체다. 금액·상대방·기간은 주요내용 줄글 안에 있고 "
                 "회사마다 적는 방식이 다르다.",
        "alt": "계약 건이면 「단일판매·공급계약체결」 공시가 정본이다.",
        "next": "수주·계약 규모는 `order_contracts`, 실적 영향은 `provisional_earnings`.",
    },
    "capital_impairment": {
        "where": "표가 본체다 — 자본금 · 자본총계 · 자본잠식률 이 당기/전기 두 칸으로 오고, "
                 "`5.매출액 또는 손익구조 변동 주요원인` 에 회사가 쓴 이유가 있다.",
        "alt": "감사인의 판단은 「감사보고서제출」 공시에, 관리종목 지정 여부는 "
               "`listing_review` 카테고리에 있다.",
        "next": "지표 시계열은 `financial_metrics`, 주석 원문은 `financial_notes`.",
    },
    "litigation": {
        "where": "소송 제기는 사건의 명칭 · 원고 · 청구내용 · 청구금액 · 관할법원, "
                 "판결은 판결내용 · 판결금액 · 판결일자, 벌금·과징금은 부과기관 · 부과사유 · "
                 "부과금액 · 납부기한 칸에 있다.",
        "alt": "경영권 분쟁 소송은 `proxy_contest`, 제재 이력은 사업보고서 「제재현황」.",
        "next": "1심·항소심 진행은 뒤따르는 「소송등의 판결·결정」 공시로 이어진다.",
    },
}
