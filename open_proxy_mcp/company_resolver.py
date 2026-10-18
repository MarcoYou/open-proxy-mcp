"""Fast, indexed company-name resolution for Korean listed companies."""

from __future__ import annotations
from open_proxy_mcp.clock import today_kst
from open_proxy_mcp.market_codes import KS as MKT_KS, KQ as MKT_KQ, to_db as mkt_to_db

import asyncio
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import difflib
import itertools
import json
import logging
import math
import os
from pathlib import Path
import re
import threading
import time
import unicodedata
from typing import Any

_log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_MARKET_CAP_PATH = _ROOT / "data/market_cap.json"
_LEGAL_SUFFIXES = {
    "co", "company", "corp", "corporation", "inc", "incorporated",
    "ltd", "limited", "plc",
}
_KOREAN_LEGAL_SUFFIX_RE = re.compile(
    r"(?:\s*[\(（]주[\)）]\s*|\s*㈜\s*|\s*주식회사\s*)$",
    re.IGNORECASE,
)
# DART 정식명은 법인격이 앞에 붙는 형태가 흔하다 — 「(주)광무」·「주식회사솔루엠」.
# suffix 만 떼면 공시에서 복사한 회사명이나 우리 툴이 출력한 이름으로 재조회했을 때
# 식별에 실패한다(실측 100사 라이브 스윕에서 14곳). 「주성엔지니어링」처럼 우연히 같은
# 글자로 시작하는 상호를 깎지 않도록 닫는 괄호나 '식회사'를 반드시 요구한다.
_KOREAN_LEGAL_PREFIX_RE = re.compile(
    r"^(?:\s*[\(（]\s*[주유재사]\s*[\)）]\s*|\s*[㈜㈐]\s*"
    r"|\s*(?:주식|유한|합자|합명)\s*회사\s*|\s*(?:재단|사단)\s*법인\s*)",
    re.IGNORECASE,
)
_NON_WORD_RE = re.compile(r"[^0-9a-z가-힣]+")

# 알파벳 26자의 한글 음차. DART 등록명은 「SKC」인데 공고 헤더는 「에스케이씨(주)」로 적는다
# (실측 322개 중 48개가 조회 실패, 대부분 이 유형). 긴 표기부터 매칭해야 '에이치'가
# '에이'로 잘리지 않는다.
_LETTER_KO = {
    "에이치": "H", "더블유": "W", "더블류": "W", "제트": "Z", "엑스": "X",
    "에스": "S", "에프": "F", "에이": "A", "제이": "J", "케이": "K",
    "브이": "V", "와이": "Y", "아이": "I", "아르": "R",
    "엘": "L", "엠": "M", "엔": "N", "오": "O", "피": "P", "큐": "Q",
    "알": "R", "티": "T", "유": "U", "비": "B", "씨": "C", "시": "C",
    "디": "D", "이": "E", "지": "G", "쥐": "G",
}
_LETTER_KO_ORDER = sorted(_LETTER_KO, key=len, reverse=True)


# 공고 헤더는 정식 상호(「삼성생명보험」)인데 DART 등록명은 짧다(「삼성생명」).
# 업종어만 떼되 **짧은 것부터** 시도한다 — 「미래에셋생명보험」에서 '생명보험'을 떼면
# 「미래에셋」이라는 다른 회사가 나온다. '보험'만 떼야 「미래에셋생명」이 된다.
# 지주·계열 표기(홀딩스·디엑스 등)는 절대 떼지 않는다 — 앞자르기 실험에서
# 에스피씨삼립→「케이에스피」, 포스코디엑스→「POSCO홀딩스」 같은 오답이 나왔다.
_INDUSTRY_SUFFIXES = ("보험", "공업", "재보험", "해상보험", "생명보험", "손해보험",
                      "화재해상보험")


def industry_suffix_variants(value: str) -> list[str]:
    """업종어를 뗀 후보들. 짧게 떼는 것부터 — 많이 뗄수록 다른 회사가 될 위험이 크다."""
    out: list[str] = []
    for suf in sorted(_INDUSTRY_SUFFIXES, key=len):
        if value.endswith(suf) and len(value) > len(suf) + 1:
            base = value[: -len(suf)]
            if base not in out:
                out.append(base)
    return out


def latinized_variants(value: str) -> set[str]:
    """앞머리의 한글 알파벳 음차를 알파벳으로 되돌린 변형들.

    「엔」처럼 알파벳(N)이자 낱말 첫 글자(엔터테인먼트)인 음절이 있어 어디까지 letter 로
    읽을지 하나로 정할 수 없다. 그래서 길이별 변형을 모두 만들어 색인한다 —
    「제이와이피엔터테인먼트」는 JY이피…·JYP엔터테인먼트·JYPN터테인먼트 중 하나가 맞는다.
    1글자는 우연 일치(이수페타시스·비상장)가 많아 2글자부터 만든다.
    """
    letters: list[str] = []
    rests: list[str] = []
    i, n = 0, len(value)
    while i < n:
        for kw in _LETTER_KO_ORDER:
            if value.startswith(kw, i):
                letters.append(_LETTER_KO[kw]); i += len(kw); break
        else:
            break
        rests.append(value[i:])
    out = set()
    for k in range(2, len(letters) + 1):
        out.add("".join(letters[:k]).lower() + rests[k - 1])
    return out


#: 리츠의 법적 상호 꼬리. DART 등록명은 「롯데리츠」·「SK리츠」·「이리츠코크렙」인데 공시(타법인출자현황 등)는
#: 「롯데위탁관리부동산투자회사」·「에스케이위탁관리부동산투자회사㈜」·「이리츠코크렙기업구조조정부동산투자회사」로 적는다.
_REIT_LEGAL_FORM_RE = re.compile(r"(?:위탁관리|기업구조조정|자기관리)?부동산투자회사$")
#: 한 이름에서 만들어 볼 음차 조합의 상한 — 넘으면 연쇄마다 「그대로·전부」 둘만 본다.
_MAX_SPELLING_COMBOS = 512


def _letter_runs(compact: str) -> list[list[tuple[int, int, str]]]:
    """compact 안의 알파벳 음차 연쇄들 — 연쇄마다 글자별 (시작, 끝, 라틴 글자). 긴 표기부터 맞춰 「에이치」가 「에이」로
    잘리지 않게 한다. 라틴 글자·숫자는 음차표에 없어 연쇄가 거기서 끊긴다(「삼성이and에이」→ 「이」·「에이」 두 연쇄)."""
    runs: list[list[tuple[int, int, str]]] = []
    cur: list[tuple[int, int, str]] = []
    i = 0
    while i < len(compact):
        kw = next((k for k in _LETTER_KO_ORDER if compact.startswith(k, i)), None)
        if kw:
            cur.append((i, i + len(kw), _LETTER_KO[kw].lower()))
            i += len(kw)
            continue
        if cur:
            runs.append(cur)
            cur = []
        i += 1
    if cur:
        runs.append(cur)
    return runs


def _run_options(run: list[tuple[int, int, str]], *, partial: bool) -> list[tuple[int, int, str] | None]:
    """연쇄 하나를 어떻게 되돌릴지 — 그대로(None) · 전부 · (partial 이면) 뒷부분만.
    뒷부분만: 「뉴엔에이아이」의 '엔'은 이름 글자라 '에이아이'만 AI 다(뉴엔AI) · 「형지아이앤씨」→형지I&C."""
    tails = range(len(run)) if partial else range(1)
    return [None] + [(run[j][0], run[-1][1], "".join(c[2] for c in run[j:])) for j in tails]


def spelling_variants(compact: str) -> list[list[str]]:
    """정식 상호를 짧은 등록명으로 옮긴 compact 후보들 — **먼저 볼 묶음부터**. 원문 compact 는 넣지 않는다(이미 찾아 봤다).

    - 이름 **가운데·끝**의 알파벳 음차 — 앞머리만 되돌리는 `latinized_variants` 로는 안 닿는다.
      「삼성에스디아이」→삼성sdi · 「삼성이앤에이」→삼성eanda(=삼성E&A) · 「셀바스에이아이」→셀바스ai.
      「알파에이아이」의 '알'(R)처럼 우연히 음차표에 걸린 글자가 있어 연쇄를 **골라** 되돌린 조합을 모두 만든다.
    - 리츠 법적 상호 — 꼬리를 「리츠」로 바꾼 묶음, 그다음 떼어 낸 묶음(위 `_REIT_LEGAL_FORM_RE`). 음차와 겹칠 수 있다
      (「에스케이위탁관리부동산투자회사」→에스케이리츠→sk리츠). 떼어 낸 쪽이 나중인 까닭: 「에스케이」→sk 는 SK(주)다.
    🔴 우연 일치가 많으므로 호출자는 등록명과 **정확히** 같아진 변형만 받는다(`CompanyResolver._spelling_variant_hit`)."""
    bases = [compact]
    reit = _REIT_LEGAL_FORM_RE.search(compact)
    if reit and reit.start() >= 2:
        bases += [compact[: reit.start()] + "리츠", compact[: reit.start()]]
    groups: list[list[str]] = []
    for base in bases:
        runs = _letter_runs(base)
        options = [_run_options(r, partial=True) for r in runs]
        if math.prod(len(o) for o in options) > _MAX_SPELLING_COMBOS:
            options = [_run_options(r, partial=False) for r in runs]
            while math.prod(len(o) for o in options) > _MAX_SPELLING_COMBOS:
                options.pop()                                   # 뒤쪽 연쇄는 그대로 둔다
        group: list[str] = []
        for combo in itertools.product(*options):
            variant = base
            for span in reversed([c for c in combo if c]):        # 뒤에서부터 바꿔야 앞 연쇄의 위치가 안 밀린다
                a, b, latin = span
                variant = variant[:a] + latin + variant[b:]
            if variant != compact and variant not in group:
                group.append(variant)
        groups.append(group)
    return groups


#: KRX 단축코드 — 6자, 숫자·영문 대문자. 숫자가 바닥나 2024년 신형 규칙부터 영문이 섞인다
#: (보통주 0126Z0 삼성에피스홀딩스 · 0001A0 덕양에너젠 / 종류주 00088K 한화3우B · 33626L).
#: 회사명과 가르려고 숫자 4자 이상을 요구한다 — 유니버스 코드 판정(`services/screener._looks_like_code`)과 같은 규칙이다.
_KRX_CODE_RE = re.compile(r"[0-9A-Z]{6}")


def krx_code(text: str) -> str:
    """입력이 KRX 단축코드 모양이면 대문자 코드, 아니면 "". 소문자로 적은 영숫자 코드(「0126z0」)도 받는다."""
    code = (text or "").strip().upper()
    if _KRX_CODE_RE.fullmatch(code) and sum(ch.isdigit() for ch in code) >= 4:
        return code
    return ""


def issuer_ticker(code: str) -> str:
    """종류주(우선주 등) 코드의 보통주 코드. 보통주 코드이거나 코드 모양이 아니면 "".

    KRX 단축코드의 끝자리는 주식 종류다 — 0 이 보통주, 그 밖(5·7·9·K·L·M …)은 같은 발행 회사의 종류주이고
    앞 5자가 발행 회사다(005935 삼성전자우 → 005930 · 005387 현대차2우B → 005380 · 00680K → 006800 미래에셋증권).
    DART 회사 원장은 보통주 코드만 갖는다. 같은 규칙을 유니버스 순위표의 우선주 판정(`services/universe.is_preferred`)이 쓴다.
    261018 실측: 최신 거래일 시세 2,762종목 중 원장에 없는 114종목이 전부 이 규칙으로 원장의 보통주에 닿았다."""
    c = krx_code(code)
    if not c or c[5] == "0":
        return ""
    return c[:5] + "0"


def share_class_ticker(corp: dict[str, Any] | None) -> str:
    """해석기가 종류주 코드로 찾은 결과면 그 종류주 코드, 아니면 "". 시세 도구가 회사(보통주)가 아니라 그 종류주의 값을 읽을 때 쓴다."""
    sc = ((corp or {}).get("_resolution") or {}).get("share_class") or {}
    return str(sc.get("ticker") or "")


def _is_inferred(results: list[dict[str, Any]]) -> bool:
    """부분일치·유사일치처럼 이름이 정확히 맞지 않아 추정한 결과인가."""
    return bool(results) and bool((results[0].get("_resolution") or {}).get("inferred"))


def is_inactive_fallback(results: list[dict[str, Any]]) -> bool:
    """활성 회사에 맞는 이름이 없어 비활성(상장폐지·합병·코넥스 등) 회사 하나로 확정한 결과인가(`CompanyResolver._inactive_single`)."""
    return bool(results) and bool((results[0].get("_resolution") or {}).get("inactive_listing"))


def _nfkc_casefold(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold().strip()


def normalize_raw(value: str) -> str:
    """Case-insensitive official-name key without destructive punctuation changes."""
    return re.sub(r"\s+", " ", _nfkc_casefold(value))


def strip_legal_form(value: str) -> str:
    """앞뒤 한국 법인격 표기(㈜·(주)·주식회사 …)만 뗀 이름(NFKC·소문자). 「한화갤러리아㈜」→「한화갤러리아」."""
    text = _KOREAN_LEGAL_PREFIX_RE.sub("", _nfkc_casefold(value))
    return _KOREAN_LEGAL_SUFFIX_RE.sub("", text).strip()


def name_tokens(value: str) -> tuple[str, ...]:
    """Search tokens shared by Korean, English, and mixed company names."""
    text = strip_legal_form(value)
    text = text.replace("&", " and ").replace("앤", " and ")
    tokens = _NON_WORD_RE.sub(" ", text).split()
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return tuple(tokens)


def normalize_phrase(value: str) -> str:
    return " ".join(name_tokens(value))


def normalize_compact(value: str) -> str:
    return "".join(name_tokens(value))



#: 🔴 **비상장은 금융업만 연다.** 260823 마스터 지시 — 정기보고서 제출 법인 명부를
#:    그대로 열면 비상장 451곳이 들어오고, 그 변경이 `financial_notes` 뿐 아니라
#:    **모든 tool 의 회사 조회에 걸린다.** 시험은 금융사로만 했으므로 범위를 그만큼만 연다.
#:    실측 — 비상장 ∩ 정기보고서 제출 451곳 중 **금융 이름 55곳**만 통과, 396곳은 닫힌다.
#:    통과 표본: 농협금융지주·농협생명보험·NH농협손해보험·엔에이치농협캐피탈·BNK캐피탈·
#:    교보생명보험·롯데카드·미래에셋자산운용·신한라이프생명보험 …
#:    (상장사는 이 필터와 무관하다 — 종목코드가 있으면 종전대로 열린다.)
_FINANCIAL_MARKS = (
    "은행", "증권", "생명보험", "손해보험", "화재", "보험", "캐피탈", "캐피털",
    "금융지주", "금융투자", "카드", "저축은행", "자산운용", "신용정보", "파이낸셜",
    "신탁", "거래소", "예탁결제", "금융",
)


def is_financial_name(name: str) -> bool:
    """이름에 금융업 표지가 있나. 비상장 법인을 열지 말지 가르는 데만 쓴다."""
    return any(mark in (name or "") for mark in _FINANCIAL_MARKS)


@dataclass(frozen=True, slots=True)
class MarketContext:
    market_caps: dict[str, int]
    active_tickers: frozenset[str] | None = None
    as_of_date: str = ""
    source: str = "none"
    #: 시세 저장분에서 관측된 종류주 코드(끝자리 0 이 아닌 것) — 상장폐지된 것까지(`krx_listing`). 최신 거래일 단면(`market_caps`)과 함께
    #: 종류주 코드를 발행 회사로 이을지 가르는 근거다(관측된 적 없는 코드를 규칙만으로 회사에 붙이지 않는다).
    share_class_tickers: frozenset[str] = frozenset()


def _local_market_context() -> MarketContext:
    try:
        raw = json.loads(_LOCAL_MARKET_CAP_PATH.read_text(encoding="utf-8"))
        caps = {
            str(ticker): int(item.get("market_cap_won") or 0)
            for ticker, item in raw.items()
            if isinstance(item, dict) and item.get("market_cap_won")
        }
        # This file is a top-company sample, not a complete listing registry.
        return MarketContext(market_caps=caps, source="local_popularity_prior")
    except (OSError, ValueError, TypeError):
        return MarketContext(market_caps={})


#: DB 에서 읽은 시장 맥락의 출처 라벨 — 응답의 `market_data_source` 로도 나간다. `_is_complete_recent` 가 같은 값으로 가린다.
_DB_MARKET_SOURCE = "krx_daily"


#: 전 종목 단면으로 믿을 시장별 최소 종목 수 — 키는 DB 저장값(`market_codes`). 261018 실측 KS 940 · KQ 1,822.
_COMPLETE_MIN_TICKERS = {MKT_KS: 700, MKT_KQ: 1_200}


def _is_complete_universe(markets: Iterable[str | None]) -> bool:
    """단면의 시장 칸들이 코스피·코스닥 전 종목을 덮나. 활성 상장 명부(`active_tickers`)를 켤지 가르는 판정이다.

    🔴 261018: 종전엔 `'KOSPI'`/`'KOSDAQ'` 리터럴을 셌는데 DB 값은 260823 개명 뒤 `KS`/`KQ` 라 전 종목이 다 있어도
    늘 거짓이었다 — 활성 명부 필터와 이전 맥락 유지 보호가 그 뒤로 운영에서 한 번도 켜지지 않았다(오류 없이 꺼져 있었다).
    시장 값은 `to_db` 로 정규화해 센다 — 적재·조회(price_multiple_data·screener)와 같은 기준이라 옛 표기 행도 같은 시장으로 센다."""
    counts = Counter(mkt_to_db(m) for m in markets)
    return all(counts[code] >= need for code, need in _COMPLETE_MIN_TICKERS.items())


def _database_market_context() -> MarketContext | None:
    """최신 거래일 전 종목 단면(시총·시장)을 일별 시세 저장분에서 읽는다. 읽을 수 없거나 비었으면 None.

    261018: 원천을 `krx_weekly` 이번 주 행(max(price_dd))에서 `krx_daily` 로 옮겼다 — 다음 단계에서 `krx_weekly` 는
    닫힌 주만 남기므로 거기서 읽으면 최신일 단면이 일주일까지 낡는다. 기준일과 행은 같은 원천에서 읽는다
    (`krx_daily` 가 비거나 실패하면 `krx_weekly` 로 돌지 않고 None → `load_market_context` 의 이전 맥락 유지·로컬 폴백).
    🔴 `price_latest` 는 쓰지 않는다 — 보관 창(7거래일) 안에서 상장폐지된 종목의 옛 날짜 행이 남아 명부에 섞인다.
    단면에는 사실값 칸과 `mktcap` 만 고른다(`chg`·`prev_*` 같은 창 칸을 고르면 같은 질의가 몇 배 느려진다)."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=1) as conn:
            latest = conn.execute("SELECT max(trd_dd) FROM krx_daily").fetchone()
            as_of_date = str(latest[0]) if latest and latest[0] else ""
            if not as_of_date:
                return None
            rows = conn.execute(
                "SELECT ticker, mktcap, market FROM krx_daily_v WHERE trd_dd=%s",
                (as_of_date,),
            ).fetchall()
            share_classes = _observed_share_classes(conn)
        caps = {str(ticker): int(cap or 0) for ticker, cap, _market in rows if ticker}
        # A partial snapshot must never be treated as the complete active universe.
        fresh = False
        try:
            fresh = today_kst() - datetime.strptime(as_of_date, "%Y%m%d").date() <= timedelta(days=14)
        except ValueError:
            pass
        complete = _is_complete_universe(market for _ticker, _cap, market in rows)
        if not complete and len(rows) >= sum(_COMPLETE_MIN_TICKERS.values()):
            # 행 수는 전 종목인데 시장별로 못 채웠다 = 시장 표기가 또 바뀌었을 가능성. 종전엔 이 경우가 조용히 꺼져 있었다.
            _log.warning("시장 맥락 %s: %d행인데 시장별 종목 수가 기준 미달이라 활성 명부를 끈다 — 시장 칸 값 %s",
                         as_of_date, len(rows), dict(Counter(str(m) for _t, _c, m in rows).most_common(4)))
        active = frozenset(caps) if complete and fresh else None
        return MarketContext(caps, active, as_of_date, _DB_MARKET_SOURCE, share_classes)
    except Exception:
        return None


def _observed_share_classes(conn: Any) -> frozenset[str]:
    """시세 저장분에서 관측된 종류주 코드 — 종목별 최초·최종 관측표(`krx_listing`, 주간 시세 파생)에서 끝자리가 0 이 아닌 것.
    상장폐지된 종류주까지 든다(그 주간 곡선이 저장분에 남아 있다). 읽을 수 없으면(표 없음·권한) 빈 집합 — 시장 맥락은 그대로 쓴다.
    세이브포인트 안에서 읽어 실패가 같은 연결의 나머지를 깨지 않게 한다."""
    try:
        with conn.transaction():
            rows = conn.execute("SELECT ticker FROM krx_listing WHERE right(ticker, 1) <> '0'").fetchall()
    except Exception:
        _log.info("종류주 관측표(krx_listing)를 읽지 못해 최신 거래일 단면의 종류주만 쓴다", exc_info=True)
        return frozenset()
    return frozenset(str(r[0]).strip().upper() for r in rows if r and r[0])


_market_context: MarketContext | None = None
_market_context_lock = asyncio.Lock()
_market_context_loaded_at = 0.0
_MARKET_CONTEXT_TTL_SECONDS = 3_600
#: 새 읽기를 못 믿어 이전 맥락을 지켰을 때 다시 읽을 때까지의 간격(초).
_MARKET_CONTEXT_RETRY_SECONDS = 60
#: 마지막 적재가 정한 유효 시간 — 평소 `_MARKET_CONTEXT_TTL_SECONDS`, 이전 맥락을 지킨 직후엔 `…_RETRY_SECONDS`.
_market_context_valid_for = _MARKET_CONTEXT_TTL_SECONDS
#: 「최근」 스냅샷의 한계 — `_database_market_context` 가 active 를 켜는 기준과 같다.
_MARKET_CONTEXT_FRESH_DAYS = 14


def _market_context_is_fresh() -> bool:
    return (
        _market_context is not None
        and _market_context_loaded_at > 0
        and time.monotonic() - _market_context_loaded_at < _market_context_valid_for
    )


def _is_complete_recent(ctx: MarketContext | None) -> bool:
    """DB(일별 시세 저장분 `krx_daily`)에서 온 **완전한** 스냅샷이고 as_of 가 최근인가 — 지킬 만한 맥락의 조건."""
    if ctx is None or ctx.source != _DB_MARKET_SOURCE or ctx.active_tickers is None:
        return False
    try:
        age = today_kst() - datetime.strptime(ctx.as_of_date, "%Y%m%d").date()
    except ValueError:
        return False
    return age <= timedelta(days=_MARKET_CONTEXT_FRESH_DAYS)


def _should_keep_previous(prev: MarketContext | None, new: MarketContext | None) -> bool:
    """이번 읽기가 이전보다 못하면 이전 맥락을 지킨다.

    못하다 = 읽기가 비었거나 실패했다(`None` — 빈 표·접속 실패·시간 초과) 또는 반쪽/낡은 스냅샷이다
    (`active_tickers=None`). 지킬 만한 이전(`_is_complete_recent`)이 없으면 종전처럼 새 값을 쓴다.

    🔴 261017: 종전엔 `None` 이면 곧바로 로컬 인기도 prior(상위 종목 표본·active 없음)로 **갈아 끼우고 한 시간을
    붙들었다.** `krx_weekly` 를 자르는 30~60초(표가 비는 구간)나 접속 1초 시간 초과 한 번에, 완전한 시총·상장 명부로
    하던 이름 해석이 한 시간 동안 표본 기반으로 떨어졌다(폐지 회사 되살림·시총 순위 왜곡).
    261018: 단면 원천을 `krx_daily` 로 옮긴 뒤에도(그 표는 쓰는 동안 비는 구간이 없다) 접속 1초 시간 초과·마이그레이션 전
    배포(표·뷰 없음)는 똑같이 `None` 이라 이 보호는 그대로 둔다."""
    return _is_complete_recent(prev) and (new is None or new.active_tickers is None)


async def load_market_context() -> MarketContext:
    global _market_context, _market_context_loaded_at, _market_context_valid_for
    if _market_context_is_fresh():
        return _market_context
    async with _market_context_lock:
        if not _market_context_is_fresh():
            database = await asyncio.to_thread(_database_market_context)
            if _should_keep_previous(_market_context, database):
                _log.warning(
                    "시장 맥락 갱신이 비어 있거나 반쪽이다(%s) — 이전 DB 맥락(as_of %s)을 유지하고 %d초 뒤 다시 읽는다",
                    "읽기 실패·빈 표" if database is None else "반쪽/낡은 스냅샷",
                    _market_context.as_of_date, _MARKET_CONTEXT_RETRY_SECONDS)
                _market_context_loaded_at = time.monotonic()
                _market_context_valid_for = _MARKET_CONTEXT_RETRY_SECONDS
                return _market_context
            local = _local_market_context()
            if database:
                merged = dict(local.market_caps)
                merged.update(database.market_caps)
                _market_context = MarketContext(
                    merged,
                    database.active_tickers,
                    database.as_of_date,
                    database.source,
                    database.share_class_tickers,
                )
            else:
                _market_context = local
            _market_context_loaded_at = time.monotonic()
            _market_context_valid_for = _MARKET_CONTEXT_TTL_SECONDS
    return _market_context


class CompanyResolver:
    """Immutable lookup indexes built once per corp-code master revision."""

    #: share_class = 종류주(우선주 등) 종목코드로 찾은 발행 회사 — 코드 조회라 추정이 아니다.
    _STRONG_KINDS = {"ticker", "corp_code", "share_class", "official", "alias", "normalized"}

    def __init__(
        self,
        corps: list[dict[str, Any]],
        aliases: dict[str, str],
        market: MarketContext | None = None,
        filers: frozenset[str] | None = None,
    ) -> None:
        self.corps = corps
        self.aliases = aliases
        self.market = market or MarketContext({})
        # 🔴 **이름 색인에서 비상장을 통째로 빼면 「농협금융지주」가 0건이 된다.** 260823 실측 —
        #    정기보고서를 내는 비상장 법인(농협금융지주·농협생명보험·NH농협손해보험)은
        #    종목코드가 없어 색인 자체에 안 들어갔다. 명부에 있는 곳은 색인한다.
        self.filers = filers or frozenset()
        self._ticker: dict[str, list[int]] = {}
        self._corp_code: dict[str, list[int]] = {}
        self._official: dict[str, list[int]] = {}
        self._phrase: dict[str, list[int]] = {}
        self._compact: dict[str, list[int]] = {}
        self._tokens: dict[str, set[int]] = {}
        self._alias: dict[str, list[int]] = {}
        self._listed_ids: set[int] = set()
        #: 이름으로 찾을 수 있는 행 — 상장사 + 정기보고서 제출 법인
        self._searchable_ids: set[int] = set()
        #: 종류주 코드 → 발행 회사의 보통주 코드. 시세 저장분에서 관측된 코드만(`MarketContext.share_class_tickers`·최신 단면).
        self._share_class: dict[str, str] = {}
        self._build_indexes()

    @staticmethod
    def _add(index: dict[str, list[int]], key: str, row_id: int) -> None:
        if key:
            index.setdefault(key, []).append(row_id)

    def _build_indexes(self) -> None:
        canonical_names: dict[str, list[int]] = {}
        for row_id, corp in enumerate(self.corps):
            ticker = str(corp.get("stock_code") or "").strip()
            corp_code = str(corp.get("corp_code") or "").strip()
            if ticker:
                self._listed_ids.add(row_id)
                self._add(self._ticker, ticker, row_id)
            self._add(self._corp_code, corp_code, row_id)
            # 상장사는 종전대로. 비상장은 **정기보고서를 내고 + 금융업 이름**일 때만 연다.
            if ticker or (corp_code in self.filers
                          and is_financial_name(corp.get("corp_name", ""))):
                self._searchable_ids.add(row_id)
            else:
                continue        # 상장도 아니고 정기보고서도 안 내는 법인 — 이름으로 안 찾는다

            for name in (corp.get("corp_name", ""), corp.get("corp_eng_name", "")):
                if not name:
                    continue
                raw = normalize_raw(name)
                phrase = normalize_phrase(name)
                compact = normalize_compact(name)
                self._add(self._official, raw, row_id)
                self._add(self._phrase, phrase, row_id)
                self._add(self._compact, compact, row_id)
                for alt in latinized_variants(compact):
                    self._add(self._compact, alt, row_id)
                for token in name_tokens(name):
                    self._tokens.setdefault(token, set()).add(row_id)
                    if re.fullmatch(r"[가-힣]{3,}", token):
                        for end in range(2, min(len(token), 8) + 1):
                            self._tokens.setdefault(token[:end], set()).add(row_id)
            canonical_names.setdefault(normalize_raw(corp.get("corp_name", "")), []).append(row_id)

        for alias, target in self.aliases.items():
            target_ids = canonical_names.get(normalize_raw(target), [])
            for key in {
                normalize_raw(alias), normalize_phrase(alias), normalize_compact(alias)
            }:
                if key and target_ids:
                    self._alias.setdefault(key, []).extend(target_ids)

        # 종류주 코드(005935·00088K) — 원장에는 보통주 코드만 있다. 시세 저장분에서 관측된 코드 중 원장에 없고, 끝자리를 0 으로
        # 바꾼 보통주 코드가 원장에 있는 것만 잇는다(`issuer_ticker`). 관측된 적 없는 코드(오타 005931 등)는 규칙만으로 회사에 붙이지 않는다.
        for code in itertools.chain(self.market.market_caps, self.market.share_class_tickers):
            base = issuer_ticker(code)
            if base and code not in self._ticker and base in self._ticker:
                self._share_class.setdefault(code, base)

    def _active_preferred(
        self,
        row_ids: set[int] | list[int],
        *,
        numeric: bool,
        kind: str,
    ) -> list[int]:
        ids = list(dict.fromkeys(row_ids))
        if numeric:
            return ids
        active = self.market.active_tickers
        if active is not None:
            current = [i for i in ids
                       if self.corps[i].get("stock_code") in active
                       # 비상장은 정기보고서 제출 + 금융업일 때만 살린다. 상장했다가
                       # 폐지된 곳을 여기서 되살리면 안 된다(활성 종목 검사가 무의미해진다).
                       or (not (self.corps[i].get("stock_code") or "").strip()
                           and self.corps[i].get("corp_code") in self.filers
                           and is_financial_name(self.corps[i].get("corp_name", "")))]
            if current:
                return current
            # 일별 시세 저장분(krx_daily)은 코스피·코스닥만 담는다. 코넥스·신규상장은 정확 일치일 때만 살리고,
            # 추론 검색에서는 비활성 회사를 되살리지 않는다.
            return ids if kind in self._STRONG_KINDS else []
        # 「상장사만」이 아니라 「이름으로 찾을 수 있는 곳」으로 좁힌다 — 정기보고서를 내는
        # 비상장 법인을 여기서 떨어뜨리면 색인해 둔 의미가 없다.
        searchable = [i for i in ids if i in self._searchable_ids]
        return searchable or ids

    def _rank_key(self, row_id: int) -> tuple[int, int, int]:
        corp = self.corps[row_id]
        ticker = str(corp.get("stock_code") or "")
        cap = int(self.market.market_caps.get(ticker, 0))
        modified = int(corp.get("modify_date") or 0)
        return cap, 1 if ticker else 0, modified

    def _spelling_variant_hit(self, compact: str) -> list[dict[str, Any]]:
        """`spelling_variants` 를 묶음 순서대로 보며, 등록명과 **정확히** 같아진 것이 처음 나온 묶음에서 한 회사로만 모이면
        그 강한 결과. 아니면 빈 목록.

        약한 일치(토큰·부분·유사)로는 받지 않는다 — 음차표 글자(이·지·시·오 …)는 흔한 음절이라 변형 대부분은 뜻이 없다.
        서로 다른 회사로 갈리면 고르지 않는다(틀린 회사를 주는 것보다 못 찾는 편이 낫다)."""
        for group in spelling_variants(compact):
            exact = [v for v in group if v in self._compact]
            if not exact:
                continue                                        # 이 묶음엔 등록명이 없다 — 다음 묶음
            if len({tuple(sorted(self._compact[v])) for v in exact}) != 1:
                return []
            hit = self._search_one(exact[0])
            return hit if hit and not _is_inferred(hit) else []
        return []

    def _decorate(self, row_id: int, kind: str, inferred: bool, alternatives: int) -> dict[str, Any]:
        corp = dict(self.corps[row_id])
        ticker = str(corp.get("stock_code") or "")
        cap = int(self.market.market_caps.get(ticker, 0))
        corp["_resolution"] = {
            "match_kind": kind,
            "inferred": inferred,
            "auto_selected": False,
            "candidate_count": alternatives + 1,
            "market_cap_won": cap or None,
            "market_data_as_of": self.market.as_of_date or None,
            "market_data_source": self.market.source,
            # 근거는 **실제로 쓴 것**만 말한다. 시총 자료가 통째로 없으면(참조 파일 부재·DB 타임아웃)
            # 정렬은 사실상 원장 최신순인데, 종전엔 그 경우에도 「로컬 인기도 prior」라고 적어
            # 같은 응답의 `market_data_source: "none"` 과 모순됐다.
            "ranking_signal": (
                "market_cap" if self.market.as_of_date
                else ("local_popularity_prior" if self.market.market_caps else "registry_recency")
            ),
            "active_registry_used": self.market.active_tickers is not None,
            "dominant": False,
            "strong_disambiguated": False,
        }
        return corp

    def search(self, query: str, *, _latinized: bool = False) -> list[dict[str, Any]]:
        raw_query = (query or "").strip()
        if not raw_query:
            return []
        if not _latinized:
            found = self._search_one(raw_query)
            if found and not _is_inferred(found):
                return found
            # 역음차 재시도 — 「에스케이씨(주)」로 물으면 「SKC」를 찾아야 한다.
            # 색인 쪽에도 같은 변형을 넣어 두어 반대 방향도 성립한다. 조회 체인 전체를
            # 다시 타야 한다: 「JYP Ent.」는 compact 가 'jypent' 라 토큰 경로로만 잡힌다.
            #
            # 약한 매칭(부분·유사)이 잡혔더라도 여기를 건너뛰지 않는다 — 「지에스」는
            # 부분일치로 「지에스이」에 먼저 걸려 「GS」를 영영 못 찾고 있었다.
            # 강한 매칭이 있으면 그것이 약한 결과를 이긴다(약한 쪽으로 내려가지는 않는다).
            compact_q = normalize_compact(raw_query)
            weak_alt: list[dict[str, Any]] = []
            for alt in sorted(latinized_variants(compact_q)):
                hit = self._search_one(alt)
                if hit and not _is_inferred(hit):
                    return hit
                if hit and not weak_alt:
                    # 역음차가 약하게만 맞는 것도 버리지 않는다 — 「제이와이피」는
                    # 원문으로는 아무것도 안 걸리고 'jyp' 토큰으로만 JYP Ent. 에 닿는다.
                    weak_alt = hit
            # 이름 가운데·끝의 음차와 리츠 법적 상호 — 공시의 정식 상호(「삼성에스디아이㈜」·「롯데위탁관리부동산투자회사」)를
            # 짧은 등록명(삼성SDI·롯데리츠)으로 옮긴다. 등록명과 정확히 같아질 때만 받으므로 약한 결과보다 앞선다.
            spelled = self._spelling_variant_hit(compact_q)
            if spelled:
                return spelled
            if found:
                return found
            if weak_alt:
                return weak_alt
            # 업종어 접미 제거 — 후보가 정확히 하나일 때만 받는다. 여럿이면 어느 쪽인지
            # 확정할 수 없으므로 붙이지 않는다(틀린 회사를 주는 것보다 못 찾는 편이 낫다).
            for base in industry_suffix_variants(compact_q):
                hit = self._search_one(base)
                listed = [h for h in hit if str(h.get("stock_code") or "").strip()]
                if len(listed) == 1:
                    return listed
            # 최후 수단 — 토큰·부분일치 단계가 비활성 회사(코넥스·상장폐지)에만 걸려 활성 필터에 통째로
            # 비었다면 거기서 멈추지 말고 다음 단계로 내려간다. 「세이」는 이름이 「세이」로 시작하는
            # 세이프아시아(코넥스)에 토큰이 걸려 부분일치(세이브존I&C)까지 못 내려갔다(261018 실측: 활성 이름
            # 가운데 2글자 조각 2,454 중 2,319 는 이미 부분일치로 고르는데, 그 조각으로 시작하는 비활성 회사가
            # 우연히 있는 78 개만 「못 찾음」). 위 경로가 모두 빈 뒤에만 하므로 지금 답이 나오는 질의는 안 바뀐다 —
            # 「엘비」→LB세미콘(역음차)을 한글 부분일치 「티엘비」가 가로채지 않는다.
            past = self._search_one(raw_query, past_inactive=True)
            if past:
                return past
            # 그래도 비었으면 — 지금 거래되는 회사에는 이름이 맞는 곳이 없다. 비활성 회사 하나만 맞으면 그 회사다.
            return self._inactive_single(raw_query)
        return self._search_one(raw_query)

    def _inactive_single(self, query: str) -> list[dict[str, Any]]:
        """활성 상장 명부로 거른 뒤 **어느 길에서도** 후보가 없을 때, 이름이 부분만 맞는(토큰·부분일치) 회사가 비활성(상장폐지·합병·
        코넥스 등 — 최신 거래일 코스피·코스닥 시세에 없는 법인) **한 곳뿐**이면 그 회사로 확정한다. 아니면 빈 목록.

        261018: 활성 명부가 실제로 켜진 뒤(#90) 「현대미포」「인프라코어」「동양생명」「라피」「아퓨」가 오류(근접 후보만)가 됐다.
        재설계 전에는 이 질의들이 그 회사(에이치디현대미포·에이치디현대인프라코어·동양생명보험·라피치·아퓨어스)로 바로 갔다 —
        폐지·합병 직후 회사의 배당·주총 이력을 줄임말로 묻는 경우다. 활성 회사가 하나라도 맞으면(「세이」→세이브존I&C) 여기까지 오지 않고,
        비활성 후보가 둘 이상이면(「더존」→더존비즈온·더존디지털웨어) 고르지 않는다. 오타 교정(fuzzy)으로 맞은 것은 받지 않는다.
        결과는 추정(`inferred`)이고 `inactive_listing` 표지를 단다 — 확정 관문이 「상장폐지·코넥스 등」 경고를 싣고(`note_weak_resolution`),
        `company` 의 신뢰도는 low 다. 활성 명부가 꺼져 있으면(시세 단면 없음) 종전 경로가 이미 비활성 회사도 고르므로 하지 않는다."""
        active = self.market.active_tickers
        compact = normalize_compact(query)
        if active is None or len(compact) < 2:
            return []
        token_sets = [self._tokens.get(token, set()) for token in name_tokens(query)]
        ids, kind = [], ""
        if token_sets and all(token_sets):
            ids, kind = sorted(set.intersection(*token_sets)), "token"
        if not ids:
            ids, kind = self._substring_ids(compact), "substring"
        ids = [i for i in ids if i in self._searchable_ids]
        if len({self.corps[i].get("corp_code") for i in ids}) != 1:
            return []
        if any(self.corps[i].get("stock_code") in active for i in ids):
            return []                                           # 활성 회사다 — 위 경로가 이미 골랐어야 한다(방어)
        row_id = sorted(ids, key=self._rank_key, reverse=True)[0]
        result = self._decorate(row_id, kind, True, 0)
        meta = result["_resolution"]
        meta["auto_selected"] = True
        meta["dominant"] = False
        meta["inactive_listing"] = True
        return [result]

    def suggest(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """못 찾았을 때 보여줄 근접 후보. **자동 선택하지 않는다.**

        앞자르기로 자동 선택하면 오답이 난다(실측: 에스피씨삼립→「케이에스피」,
        포스코디엑스→「POSCO홀딩스」). 같은 계산을 '제안'으로 쓰면 안전하다 —
        고르는 것은 사람이다. 「에이플러스에셋어드바이저」→「에이플러스에셋」처럼
        접미가 붙은 상호, 개명·상장폐지된 이름을 사용자가 알아볼 수 있게 한다.

        순서: ① 질의 전체를 이름에 품은 곳(시총·원장 최신순) ② 질의 앞부분이 긴 순으로 맞는 곳. `limit` 개까지.
        """
        base = normalize_compact(query)
        seen: dict[int, None] = {}
        # 질의 **전체**가 이름 가운데 든 곳이 먼저다 — 「현대미포」→「에이치디현대미포」처럼 앞자르기로는
        # 영영 안 닿는다(261018 실측: 활성 명부가 켜지자 상장폐지·합병·코넥스 조각 8개 중 7개가 후보 0개).
        # 맞춘 글자 수가 질의 길이 그대로라 아래 앞자르기(최대 길이−1)보다 늘 낫다 — 「인프라코어」는
        # 3글자만 맞는 「인프라웨어테크놀러지」보다 5글자를 다 품은 「에이치디현대인프라코어」를 먼저 보인다.
        # 한 글자는 거의 모든 이름에 들어 있어 제안이 못 된다.
        if len(base) >= 2:
            for row_id in sorted(self._substring_ids(base), key=self._rank_key, reverse=True):
                seen.setdefault(row_id, None)
        for end in range(len(base) - 1, 2, -1):
            if len(seen) >= limit:
                break
            for row_id in self._compact.get(base[:end], []):
                if row_id in self._listed_ids:
                    seen.setdefault(row_id, None)
            for row_id in self._tokens.get(base[:end], set()):
                if row_id in self._listed_ids:
                    seen.setdefault(row_id, None)
        return [self.corps[i] for i in list(seen)[:limit]]

    def _substring_ids(self, compact: str) -> list[int]:
        """종목코드가 있는 법인 중 이름(한글·영문 compact)에 `compact` 가 든 행. 메모리 원장만 훑는다."""
        return [
            i for i in self._listed_ids
            for corp in (self.corps[i],)
            if compact in normalize_compact(corp.get("corp_name", ""))
            or compact in normalize_compact(corp.get("corp_eng_name", ""))
        ]

    def _search_one(self, query: str, *, past_inactive: bool = False) -> list[dict[str, Any]]:
        """첫 번째로 비지 않은 단계에서 멈춘다. `past_inactive` 면 활성 필터가 통째로 비울 추론 단계는 건너뛴다."""
        raw_query = (query or "").strip()
        if not raw_query:
            return []
        numeric = raw_query.isdigit()
        row_ids: list[int] = []
        kind = ""
        share_class = ""
        code = krx_code(raw_query)

        if re.fullmatch(r"\d{6}", raw_query) or (code and (code in self._ticker or code in self._share_class)):
            # 종목코드. 숫자 6자리는 종전대로 늘 이 길이다. 영숫자(신형 0126Z0)·소문자는 원장 또는 관측된 종류주에 있을 때만 —
            # 없으면 종전처럼 아래 이름 검색으로 내려간다(261018: 종전엔 숫자 6자리만 코드로 읽어 신형 코드 상장사 64곳이 코드로 안 찾혔다).
            numeric = True
            key = code or raw_query
            row_ids, kind = self._ticker.get(key, []), "ticker"
            if not row_ids and key in self._share_class:
                # 종류주 코드 → 발행 회사(보통주와 같은 corp_code). 어느 종류주로 물었는지는 `_resolution.share_class` 로 남긴다 —
                # 시세 도구는 그 종류주 자체의 값을, 회사 단위 도구는 발행 회사의 값을 준다.
                row_ids, kind, share_class = self._ticker.get(self._share_class[key], []), "share_class", key
        elif re.fullmatch(r"\d{8}", raw_query):
            row_ids, kind = self._corp_code.get(raw_query, []), "corp_code"
        else:
            raw = normalize_raw(raw_query)
            phrase = normalize_phrase(raw_query)
            compact = normalize_compact(raw_query)
            alias_ids = self._alias.get(raw) or self._alias.get(phrase) or self._alias.get(compact)
            if alias_ids:
                # Curated aliases include intentional historical-name redirects.
                row_ids, kind = alias_ids, "alias"
            elif raw in self._official or (bare := normalize_raw(strip_legal_form(raw_query))) in self._official:
                # 법인격만 붙은 정식명(「한화갤러리아㈜」·「주식회사 카카오」)도 정식명 일치다. 종전엔 normalized 로 내려가
                # 아래 브랜드 규칙(같은 글자로 시작하는 「한화갤러리아타임월드」가 있다)에 걸려 추정으로 떨어졌다.
                row_ids, kind = self._official.get(raw) or self._official[bare], "official"
            else:
                row_ids = self._phrase.get(phrase) or self._compact.get(compact) or []
                if row_ids:
                    kind = "normalized"

            query_tokens = name_tokens(raw_query)
            # A one-word brand such as "Hyundai" also equals the legal-suffix-stripped
            # name "HYUNDAI CORPORATION". Treat it as a brand search when the token is
            # shared by more companies; raw official names and curated aliases already won.
            if kind == "normalized" and len(query_tokens) == 1:
                brand_ids = self._tokens.get(query_tokens[0], set())
                if len(brand_ids) > len(row_ids):
                    row_ids, kind = list(brand_ids), "token"

            if not row_ids:
                token_sets = [self._tokens.get(token, set()) for token in query_tokens]
                if token_sets and all(token_sets):
                    row_ids = list(set.intersection(*token_sets))
                    kind = "token"

            if past_inactive and kind == "token" and not self._active_preferred(
                    row_ids, numeric=numeric, kind=kind):
                row_ids, kind = [], ""

            if not row_ids and compact:
                # Korean brand fragments are not whitespace-tokenizable. This fallback scans
                # only the in-memory master and runs only after every indexed path missed.
                row_ids = self._substring_ids(compact)
                if row_ids:
                    kind = "substring"

            if past_inactive and kind == "substring" and not self._active_preferred(
                    row_ids, numeric=numeric, kind=kind):
                row_ids, kind = [], ""

            if not row_ids and len(compact) >= 5:
                close_keys = difflib.get_close_matches(compact, self._compact, n=5, cutoff=0.88)
                if close_keys:
                    row_ids = [row_id for key in close_keys for row_id in self._compact[key]]
                    kind = "fuzzy"

        preferred = self._active_preferred(row_ids, numeric=numeric, kind=kind)
        ranked = sorted(preferred, key=self._rank_key, reverse=True)
        if not ranked:
            return []

        inferred = kind not in self._STRONG_KINDS
        auto_selected = False
        if inferred and len(ranked) > 1:
            top_cap = self._rank_key(ranked[0])[0]
            second_cap = self._rank_key(ranked[1])[0]
            auto_selected = top_cap > 0
            dominant = bool(self.market.as_of_date) and top_cap > 0 and (
                second_cap == 0 or top_cap >= second_cap * 1.5
            )
        elif inferred:
            auto_selected = True
            dominant = True
        else:
            dominant = False

        results = [self._decorate(i, kind, inferred, len(ranked) - 1) for i in ranked]
        if share_class:
            for result in results:
                result["_resolution"]["share_class"] = {
                    "ticker": share_class, "issuer_ticker": self._share_class[share_class]}
        if results:
            results[0]["_resolution"]["auto_selected"] = auto_selected
            results[0]["_resolution"]["dominant"] = dominant
            if not inferred and len(ranked) > 1:
                korean_names = {normalize_raw(self.corps[i].get("corp_name", "")) for i in ranked}
                top_cap = self._rank_key(ranked[0])[0]
                second_cap = self._rank_key(ranked[1])[0]
                results[0]["_resolution"]["strong_disambiguated"] = (
                    len(korean_names) == 1 and top_cap > 0 and second_cap == 0
                )
        return results


_resolver: CompanyResolver | None = None
_resolver_source_id: int | None = None
_resolver_market_id: int | None = None
_resolver_filers_id: int | None = None
_resolver_lock = threading.Lock()


async def get_company_resolver(
    corps: list[dict[str, Any]], aliases: dict[str, str],
    filers: frozenset[str] | None = None,
) -> CompanyResolver:
    global _resolver, _resolver_source_id, _resolver_market_id, _resolver_filers_id
    source_id = id(corps)
    market = await load_market_context()
    market_id = id(market)
    filers_id = len(filers or ())
    if (_resolver is not None and _resolver_source_id == source_id
            and _resolver_market_id == market_id and _resolver_filers_id == filers_id):
        return _resolver
    with _resolver_lock:
        if (_resolver is None or _resolver_source_id != source_id
                or _resolver_market_id != market_id or _resolver_filers_id != filers_id):
            _resolver = CompanyResolver(corps, aliases, market, filers)
            _resolver_source_id = source_id
            _resolver_market_id = market_id
            _resolver_filers_id = filers_id
    return _resolver
