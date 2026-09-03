"""shareholder_meeting facade 서비스."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import re
import time
from typing import Any

from bs4 import BeautifulSoup
from open_proxy_mcp.dart.client import DartClientError, get_dart_client
import open_proxy_mcp.services.shareholder_meeting_parser as notice_parser_mod
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.company import company_not_found_warning
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    build_filing_meta,
    build_usage,
    status_from_filing_meta,
)
from open_proxy_mcp.services.date_utils import format_iso_date, parse_date_param, resolve_date_window
from open_proxy_mcp.services.filing_search import search_filings_by_report_name
from open_proxy_mcp.services.agm_result_parser import parse_agm_result_summary, parse_agm_result_table
from open_proxy_mcp.services.shareholder_meeting_parser import (
    agenda_detail_sections,
    parse_agenda_details_xml,
    parse_agenda_xml,
    parse_aoi_xml,
    parse_compensation_xml,
    parse_corrections_xml,
    parse_meeting_info_xml,
    parse_personnel_xml,
    validate_agenda_result,
)


_SUPPORTED_SCOPES = {"summary", "agenda", "board", "compensation", "aoi_change", "prov_financials", "results", "full", "advise"}
_MEETING_TYPE_MAP = {
    "annual": "정기",
    "extraordinary": "임시",
}
_ALLOWED_MEETING_TYPES = {"auto", "annual", "extraordinary"}
_NOTICE_LEAD_BUFFER_DAYS = 90
_SUMMARY_MEETING_INFO_KEYS = {
    "meeting_type",
    "meeting_term",
    "is_correction",
    "datetime",
    "location",
    "report_items",
    "toc",
}


class _RequestLocalSoupFactory:
    """One-request soup cache keyed by rcept_no + raw HTML."""

    def __init__(
        self,
        original: Any,
        cache: dict[tuple[str, str, Any], Any],
        rcept_no: str,
    ) -> None:
        self.original = original
        self.cache = cache
        self.rcept_no = rcept_no

    def __call__(self, markup: Any = "", features: Any = None, *args: Any, **kwargs: Any) -> Any:
        if not isinstance(markup, str):
            return self.original(markup, features, *args, **kwargs)
        key = (self.rcept_no, markup, features)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        soup = self.original(markup, features, *args, **kwargs)
        self.cache[key] = soup
        return soup


@contextmanager
def _cached_notice_parser_soup(
    soup_cache: dict[tuple[str, str, Any], Any] | None,
    rcept_no: str,
):
    if soup_cache is None:
        yield
        return

    original = notice_parser_mod.BeautifulSoup
    notice_parser_mod.BeautifulSoup = _RequestLocalSoupFactory(original, soup_cache, rcept_no)
    try:
        yield
    finally:
        notice_parser_mod.BeautifulSoup = original


_AGENDA_PROCEDURAL_PATTERNS = (
    "선임할 이사의 수",
    "선임할 이사 수",
    "이사의 수 결정",
    "집중투표에 의하여 선임할",
    "집중투표에 의한 이사 선임",
)
_AGENDA_CONDITIONAL_PATTERNS = (
    "승인 시",
    "승인시",
    "가결 시",
    "가결시",
    "가결될 경우",
    "부결 시",
    "부결시",
    "부결될 경우",
    "통과 시",
    "통과시",
    # 「가결 되는 경우」처럼 어간을 띄어 쓰는 서식이 있어 「가결될 경우」만으로는 놓친다
    # (BNK금융지주 실측 — 조건절이 조건부로 안 잡혀 미분류 자동 찬성으로 샜다).
    "되는 경우",
    "경우에만",
    "선행",
)
#: 회사가 **이미 내려놓은** 안건. 후보가 사퇴했거나 선행 안건 결과로 자동 폐기되는 자리다.
#: 표결 대상이 아닌데 찬성이 나가면 그 자체로 못 쓰는 지시서가 된다 — 실측 고려아연 30·39
#: 「사외이사 오영 선임의 건→ 오영 후보자 일신상의 사유로 자진 사퇴함에 따라 안건 폐기」에
#: ✅ 찬성, BNK금융지주 24 「이사 보수 한도 승인의 건은 자동 폐기」에 ✅ 찬성이 나갔다.
#: 목록에서 지우지는 않는다 — 지우면 소집공고와 대조가 안 된다.
_AGENDA_WITHDRAWN_PATTERNS = (
    "안건 폐기",
    "안건폐기",
    "자동 폐기",
    "자동폐기",
    "자진 사퇴",
    "자진사퇴",
    "안건 철회",
    "안건철회",
    "상정 철회",
    "상정철회",
)
#: 제목이 **스스로** 상호배타라고 말하는 경우. 여기에 「5인 선임」·「6인 선임」 같은
#: 인원 리터럴을 넣지 말 것 — 260525 에 그렇게 넣었다가 **고려아연 하나만 비켜간** 상태가
#: 됐다(4인/7인 시나리오면 그대로 뚫린다). 인원으로 갈리는 시나리오는 제목 하나로는 알 수
#: 없고 **형제를 봐야** 안다 → `_mark_exclusive_scenarios`.
_AGENDA_ALTERNATIVE_PATTERNS = (
    "대안",
    "택일",
    "둘 중",
    "상호배타",
    # 260813: 아래 셋이 코드베이스 어디에도 없어 **한 자리를 놓고 겨루는 후보 둘에 양쪽 찬성**이
    #   나갔다. 실측 고려아연 20260811000705 제3호 —
    #   「'제3-1호' 및 '제3-2호' 의안은 일괄표결 후 보통결의요건 충족 의안이 복수일 경우
    #    **다득표 의안이 가결**된 것으로 함」
    #   둘 다 찬성은 중립이 아니라 **기권**이다. 내 표가 두 후보의 격차를 1표도 못 벌린다.
    #   어느 쪽을 밀지는 도구가 아니라 운용사가 정할 문제라 형제 전체를 검토로 내린다.
    "일괄표결",
    "일괄 표결",
    "다득표",
    "최다득표",
)


def _proposer_type(source: str | None) -> str:
    # 값은 코드 전반의 canonical 명칭 "shareholder_proposal"로 통일 (category 키·proxy_advise
    # 가이드라인 키와 동일). 과거 "shareholder"는 category("shareholder_proposal")와 어긋나
    # 소비자가 주주제안을 놓치는 원인이었다.
    if source and "주주제안" in source:
        return "shareholder_proposal"
    if source:
        return "unknown"
    return "company"


def _agenda_relation(title: str, conditional: str | None = None) -> tuple[str, list[str]]:
    text = " ".join(part for part in [title or "", conditional or ""] if part)
    reasons: list[str] = []
    if conditional:
        reasons.append("conditional_field")
    if any(pattern in text for pattern in _AGENDA_PROCEDURAL_PATTERNS):
        reasons.append("procedural_title")
    if "집중투표" in text or "누적투표" in text:
        reasons.append("cumulative_voting_title")
    if any(pattern in text for pattern in _AGENDA_CONDITIONAL_PATTERNS):
        reasons.append("conditional_title")
    if any(pattern in text for pattern in _AGENDA_ALTERNATIVE_PATTERNS):
        reasons.append("alternative_title")
    # **조건절 안의 「자동 폐기」는 아직 폐기가 아니다.** 「제3호 의안은 제2-6호 의안이 부결되는
    # 경우 자동 폐기」는 제2-6호가 가결되면 표결되는 안건이고, 「(제2-7호 부결되는 경우) 이사
    # 선임의 건」도 마찬가지다. 문자열만 보고 폐기로 확정하면 **던져야 할 표를 지시서에서 지운다**
    # — 표결 대상 아닌 안건에 찬성을 내는 것과 같은 크기의 사고다(실측 KT&G 4건·코웨이 13건).
    # 완료된 사실(「자진 사퇴함에 따라 안건 폐기」)에만 withdrawn 을 준다.
    if (any(pattern in text for pattern in _AGENDA_WITHDRAWN_PATTERNS)
            and "conditional_title" not in reasons and "conditional_field" not in reasons):
        reasons.append("withdrawn_title")
        return "withdrawn", reasons
    if "procedural_title" in reasons:
        return "procedural", reasons
    if "alternative_title" in reasons:
        return "alternative", reasons
    if "conditional_title" in reasons or "conditional_field" in reasons:
        return "conditional", reasons
    if "cumulative_voting_title" in reasons:
        return "cumulative_related", reasons
    return "normal", []


# 안건 category(영문 키) → 한글 라벨. proxy_advise._classify_agenda와 키 공유.
_AGENDA_CATEGORY_KO = {
    "director_election": "이사 선임", "audit_committee_election": "감사위원 선임",
    "financial_statements": "재무제표 승인", "cash_dividend": "현금배당",
    "director_compensation": "이사 보수한도", "audit_compensation": "감사 보수한도",
    "retirement_pay": "퇴직금", "articles_amendment": "정관 변경",
    "treasury_share": "자기주식", "merger_or_restructuring": "합병/분할",
    "shareholder_proposal": "주주제안", "other": "기타",
}


#: 부모에서 자식으로 내려가는 관계. 부모가 「5인 선임」·「6인 선임」처럼 **서로 택일**인 묶음이면
#: 그 아래 후보 하나하나도 택일 구조 안에 있는데, 관계를 자식 제목만으로 다시 계산하면 자식은
#: 전부 `normal` 이 되어 개별 후보 평가로 자동 찬성이 나간다. 실측 고려아연 — 부모 24(5인)·33(6인)은
#: ⚠️ 로 잡혔는데 자식 16명 전원이 ✅ 찬성이라, 최대 6석에 16표를 던지는 지시서가 됐다.
#: `procedural` 은 내리지 않는다(「선임할 이사의 수」가 그 아래 후보를 절차성으로 만들지 않는다).
_AGENDA_RELATION_INHERITED = ("withdrawn", "alternative", "conditional")


def _agenda_nodes(
    items: list[dict[str, Any]],
    parent_title: str = "",
    parent_relation: str = "",
) -> list[dict[str, Any]]:
    # 안건 카테고리 분류 — proxy_advise의 300사 검증 분류기를 agenda scope에도 적용(category None 해소).
    # 순환 import 회피를 위해 함수 내 지역 import.
    from open_proxy_mcp.services.proxy_advise import _classify_agenda

    nodes: list[dict[str, Any]] = []
    for item in items:
        agenda_id = item["number"].replace("제", "").replace("호", "")
        title = item.get("title", "")
        conditional = item.get("conditional")
        relation_type, relation_reasons = _agenda_relation(title, conditional)
        # 자식이 스스로 더 강한 신호를 냈으면 덮지 않는다 — 상속은 빈자리만 채운다.
        if relation_type == "normal" and parent_relation in _AGENDA_RELATION_INHERITED:
            relation_type = parent_relation
            relation_reasons = [*relation_reasons, f"inherited_from_parent:{parent_relation}"]
        category = _classify_agenda(title, parent_title=parent_title)
        node = {
            "agenda_id": agenda_id,
            "number": item.get("number", ""),
            "title": title,
            "category": category,
            "category_label": _AGENDA_CATEGORY_KO.get(category, category),
            "source": item.get("source"),
            "proposer_type": _proposer_type(item.get("source")),
            "conditional": conditional,
            "agenda_relation_type": relation_type,
            "agenda_relation_reasons": relation_reasons,
            "children": _agenda_nodes(
                item.get("children", []), parent_title=title, parent_relation=relation_type
            ),
        }
        # 파서가 붙인 진단 필드를 통과시킨다. 화이트리스트로 새 dict를 만드는 구조라
        # 여기 적지 않으면 조용히 사라진다 — 실제로 filed_*·resolution_* 이 그렇게 유실됐다.
        for key in ("filed_code", "filed_kind", "filed_link", "declared_role",
                    "resolution_status", "resolution_note", "dividend"):
            if item.get(key) is not None:
                node[key] = item[key]
        nodes.append(node)
    # 형제를 다 만든 **뒤에야** 시나리오 판정이 가능하다(제목 하나로는 못 본다).
    _mark_exclusive_scenarios(nodes)
    return nodes


#: 역할이 다르면 **함께 뽑는** 안건이지 택일이 아니다 — 「사내이사 2인」과 「사외이사 3인」은
#: 상호배타가 아니라 상보적이다. 역할 수식어가 갈리면 시나리오 판정을 하지 않는다.
_ROLE_WORDS = ("사내이사", "사외이사", "기타비상무이사", "감사위원", "감사")


def _role_scope(title: str) -> str:
    t = (title or "").replace(" ", "")
    for w in _ROLE_WORDS:
        if w.replace(" ", "") in t:
            return w
    return ""


def _mark_exclusive_scenarios(nodes: list[dict[str, Any]]) -> None:
    """같은 층 형제 중 **집중투표 + 선임 인원**이 인원만 다르게 둘 이상이면 상호배타로 본다.

    왜 형제를 봐야 하나: 「집중투표의 방법으로 이사 5인을 선임하는 건」이라는 제목 하나만
    보면 그게 시나리오인지 그냥 선거인지 알 수 없다. **옆에 6인안이 같이 올라와 있어야**
    비로소 택일 구조다. 종전에는 이걸 제목 리터럴(`"5인 선임"`·`"6인 선임"`)로 때웠는데,
    그건 고려아연 한 회사만 맞히고 4인/7인이면 그대로 뚫리는 임시방편이었다.

    좁게 잡는다 — **틀리게 묶으면 던져야 할 표를 지운다**:
      · 집중투표/누적투표 언급이 있어야 한다(시나리오를 만드는 것이 집중투표 청구다)
      · 선임 인원을 읽을 수 있어야 하고, 그 인원이 서로 달라야 한다
      · 역할이 갈리면(사내/사외/감사위원) 제외 — 그건 함께 뽑는 상보적 안건이다
    묶은 뒤에는 자식(후보 행)까지 상속시킨다. 자식이 스스로 더 강한 신호를 냈으면 덮지 않는다.
    """
    # ── 신호 ②: **선행 트리거 안건** ──────────────────────────────────────
    # 「집중투표에 의하여 선임할 이사의 수 결정의 건」이 형제로 있으면, 인원이 박힌 선거는
    # 그 결과에 걸린다 — 몇 석인지가 아직 안 정해졌는데 후보에 찬성부터 낼 수는 없다.
    # 고려아연 구조가 정확히 이것이었다(제2-7호 가결 여부에 5인안/6인안이 갈렸다).
    # 시나리오가 하나만 올라온 경우엔 형제 비교(신호 ①)로는 안 잡히므로 이 신호가 받는다.
    has_seat_trigger = any(
        (n.get("agenda_relation_type") == "procedural")
        and any(k in (n.get("title") or "") for k in ("이사의 수", "이사 수"))
        for n in nodes
    )
    if has_seat_trigger:
        for node in nodes:
            title = node.get("title") or ""
            if "선임" not in title or _seat_count_in_title(title) is None:
                continue
            if node.get("agenda_relation_type") in ("normal", "cumulative_related"):
                node["agenda_relation_type"] = "conditional"
                node["agenda_relation_reasons"] = [
                    *(node.get("agenda_relation_reasons") or []), "seat_count_trigger_sibling"]
            _propagate_relation(node.get("children") or [], node["agenda_relation_type"])

    # ── 신호 ①: 형제 시나리오 (인원만 다른 집중투표 선거가 둘 이상) ──────
    cands = []
    for node in nodes:
        title = node.get("title") or ""
        if "집중투표" not in title and "누적투표" not in title:
            continue
        seats = _seat_count_in_title(title)
        if seats is None or "선임" not in title:
            continue
        cands.append((node, seats, _role_scope(title)))
    by_role: dict[str, list] = {}
    for node, seats, role in cands:
        by_role.setdefault(role, []).append((node, seats))
    for role, group in by_role.items():
        if len({s for _, s in group}) < 2:      # 인원이 다 같으면 시나리오가 아니다
            continue
        for node, _ in group:
            if node.get("agenda_relation_type") in ("normal", "cumulative_related"):
                node["agenda_relation_type"] = "alternative"
                node["agenda_relation_reasons"] = [
                    *(node.get("agenda_relation_reasons") or []), "sibling_seat_scenario"]
            _propagate_relation(node.get("children") or [], node["agenda_relation_type"])


def _propagate_relation(children: list[dict[str, Any]], relation: str) -> None:
    """부모가 뒤늦게 시나리오로 판명됐을 때 자식에게 물려준다.
    자식이 스스로 더 강한 신호를 냈으면 덮지 않는다 — 상속은 빈자리만 채운다."""
    for child in children:
        if child.get("agenda_relation_type") in ("normal", "cumulative_related"):
            child["agenda_relation_type"] = relation
            child["agenda_relation_reasons"] = [
                *(child.get("agenda_relation_reasons") or []), f"inherited_from_parent:{relation}"]
        _propagate_relation(child.get("children") or [], relation)


#: 🔴 **경합은 「둘 중 하나」가 아니다** (2026-08-30 U 6차 실측).
#: 한국앤컴퍼니 제4호는 후보 3명·**자리 2개** 순차표결인데 세 후보 전부에 「둘 다 찬성할 수
#: 없습니다」가 붙었다. 그대로 따르면 **찬성할 수 있는 표를 하나 버린다** — 자리가 둘이면
#: 이사회측 1명 + 주주측 1명에 동시에 찬성하는 것이 가능하다.
#: 그래서 공고가 밝힌 **선출 인원**을 먼저 읽는다. 못 읽으면 숫자를 지어내지 않고
#: 「몇 명을 뽑는지 못 읽었다」고 말한다 — 「둘 다 안 된다」고는 하지 않는다.
#:
#: 실측 두 꼴 —
#:   태광산업   「…보통결의 요건 충족 의안이 3개 이상일 경우 **다득표 의안 2개** 안건이 가결…」
#:   한국앤컴퍼니 「…충족한 후보가 2인 이상일 경우 찬성률이 높은 후보 순으로 **2인**의 …선임합니다」
#: 「2인 이상일 경우」는 **문턱**이지 자리 수가 아니다 — 그래서 뒤쪽 형태만 잡는다.
_ELECTED_SEATS_RES = (
    re.compile(r"다득표\s*(?:의안|후보)?\s*(\d+)\s*(?:개|건|인|명)"),
    re.compile(r"찬성(?:률|표)[^.\n]{0,20}?높은\s*(?:후보|순)[^.\n]{0,15}?(\d+)\s*(?:인|명)"),
    re.compile(r"상위\s*(\d+)\s*(?:인|명)"),
)


def _elected_seats_in_notice(notice_text: str) -> tuple[int | None, str | None]:
    """공고 본문에서 **선출 인원**과 그 근거 문장. 못 읽으면 (None, None).

    지어내지 않는다 — 근거 문장을 함께 돌려주어 읽는 쪽이 대볼 수 있게 한다.
    """
    text = notice_text or ""
    for rx in _ELECTED_SEATS_RES:
        m = rx.search(text)
        if not m:
            continue
        n = int(m.group(1))
        if not 1 <= n <= 30:
            continue
        quote = re.sub(r"\s+", " ", text[max(0, m.start() - 70):m.end() + 40]).strip()
        return n, quote
    return None, None


def _seat_count_in_title(title: str) -> int | None:
    """제목에서 선임 인원. 연도·금액이 섞여 들어오지 않게 상한을 둔다."""
    m = re.search(r"(\d+)\s*(?:인|명)", title or "")
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 30 else None


def _is_cumulative_title(title: str) -> bool:
    t = title or ""
    return "집중투표" in t or "누적투표" in t


def _election_seats_for_group(
    parent_title: str | None,
    notice_text: str,
) -> tuple[int | None, str | None, str | None]:
    """한 선거(형제 묶음)의 **선출 인원**과 그 출처. (seats, quote, source).

    🔴 순서가 뜻을 정한다 (2026-09-04 실측 고려아연 임시주총). 부모 「집중투표의 방법으로
    이사 4인 선임의 건」은 4석을 제목에 박아 두었는데, 자식 후보 넷은 공고 전체에서
    「다득표 N개」 문구만 찾다가 「몇 명을 뽑는지 읽지 못했습니다」로 떨어졌다 — 부모는
    ✅ 인데 자식 전원 ⚠️ 인 모순이 그렇게 났다. **부모 제목이 그 선거의 정원이다.** 공고
    본문의 다득표 규칙은 부모가 인원을 말하지 않을 때의 보조다(한국앤컴퍼니 제4호처럼).
    인원을 세는 규칙은 proxy_advise._seat_count 한 벌만 쓴다(역할별 나열은 합산).
    """
    if parent_title:
        from open_proxy_mcp.services.proxy_advise import _seat_count
        n = _seat_count(parent_title)
        if n:
            return n, re.sub(r"\s+", " ", parent_title).strip(), "parent_title"
    seats, quote = _elected_seats_in_notice(notice_text)
    if seats is not None:
        return seats, quote, "notice"
    return None, None, None


# ─────────────────────────────────────────────────────────────────────────────
# 안건 사이의 관계 (260828) — 「같은 자리를 두고 맞선 안건에 같은 ✅ 를 주지 않는다」
#
# 안건을 하나씩 독립적으로만 보면 위임장 경쟁을 통째로 놓친다. 실측 대림제지(017650)
# 2026 임시주총: 주주제안 감사위원 해임 3건은 ⚠️ 인데 그 빈 자리를 채울 선임 4건은 전부
# ✅ 찬성으로 나갔다 — 이사회측 후보와 주주측 후보가 같은 자리에서 맞붙었는데 「둘 다
# 찬성」은 **실행할 수 없는 지시서**다.
#
# 여기서 잡는 관계는 넷이다. **판정을 대신하지 않는다 — 관계를 화면에 쓸 뿐이다.**
#   · contested        경합 — 같은 자리를 두고 이사회제안 후보와 주주제안 후보가 맞섰다
#   · depends_on       선행 의존 — 해임이 통과해야 그 자리 선임이 의미를 갖는다
#   · conditional_on   조건부 상정 — 공고 본문이 「제N호 의안이 가결될 경우」라고 밝혔다
#   · bundled          묶음(일괄표결) — 개별 찬반이 불가능하다 (proxy_advise 가 후보 수를
#                      알아야 판정할 수 있어 여기서는 만들지 않는다)
#
# **좁게 잡는다.** 관계를 잘못 붙이면 정상 안건의 판정을 지운다 — 경합은 「같은 형제 층 ·
# 같은 선임 카테고리 · 제안 주체가 갈릴 때」만, 의존은 「같은 자리(role scope)의 해임이
# 같은 주총에 올라와 있을 때」만 본다. 실측 금호석유화학(011780)은 전원 회사제안이라
# 아무 관계도 붙지 않아야 한다(위양성 감시 종목).
# ─────────────────────────────────────────────────────────────────────────────

#: 관계를 볼 선임 안건의 카테고리. 정관변경·보수한도는 자리를 다투지 않는다.
_ELECTION_CATEGORIES = ("director_election", "audit_committee_election")

#: 조건절이 **의존**을 말하는지 가르는 표지. 「제4-1호 … 의안에 대해서는 보통결의」처럼
#: 결의요건만 밝히는 문장은 의존이 아니다 — 표지가 없으면 링크를 만들지 않는다.
_DEPENDENCY_MARKERS = (
    "가결", "부결", "선임된", "선출된", "승인된", "승인될", "통과",
    "한하여", "한해", "전제", "폐기", "조건으로", "다득표",
)

#: 참조 **바로 뒤**에 붙어 「이 안건이 저 안건에 걸린다」를 말하는 어구. 명단 나열
#: (「제4-1호, 제4-2호 의안에 대해서는」)과 방향성 참조를 가르는 것이 이것이다.
_DIRECTIONAL_REFERENCE = (
    "의안에서", "에서 선임", "에서 선출", "의안이 가결", "의안이 부결", "의안의 가결",
    "이 가결", "가 가결", "이 부결", "가 부결", "가결될", "부결될", "가결되는", "부결되는",
    "가결시", "가결 시", "승인될", "승인되는", "의안이 승인",
)


def _is_election_title(title: str) -> bool:
    t = (title or "")
    return "선임" in t and "해임" not in t


#: 「해임」이 **이 안건이 하려는 일**일 때만 해임 안건이다. 「감사위원 선·해임 시 의결권 제한
#: 조항 삽입의 건」은 정관변경이지 해임이 아닌데, 글자만 보면 걸린다 — 실측 한국앤컴퍼니
#: 제2-5호·태광산업 제2-2호가 그렇게 걸려 감사위원 선임 안건 전부에 「가결이 전제」가 붙었다.
_REMOVAL_ACT_RE = re.compile(r"해임(?:의)?\s*(?:건|안|승인|요구)")
_REMOVAL_EXCLUDE = ("선·해임", "선해임", "선임·해임", "선임 및 해임", "선임및해임", "선/해임")


def _is_removal_title(title: str, category: str | None = None) -> bool:
    t = title or ""
    if any(x in t for x in _REMOVAL_EXCLUDE):
        return False
    if category in ("articles_amendment", "director_compensation", "audit_compensation"):
        return False
    return bool(_REMOVAL_ACT_RE.search(t))


def _seat_scope(title: str) -> str:
    """그 안건이 다투는 **자리**. 감사위원 자리와 일반 이사 자리는 별개다."""
    t = (title or "").replace(" ", "")
    if "감사위원" in t:
        return "감사위원"
    if "감사" in t:
        return "감사"
    if "이사" in t:
        return "이사"
    return ""


_AGENDA_NUM_RE = re.compile(r"제\s*(\d+(?:\s*-\s*\d+)*)\s*호")

#: 안건 머리말에서 이만큼만 그 안건의 구간으로 본다(상호참조는 후보자 표·비고칸에 있다).
_AGENDA_SECTION_CHARS = 2500


def _norm_agenda_no(raw: str) -> str:
    return "제" + re.sub(r"\s+", "", raw or "") + "호"


def _agenda_sections(notice_text: str, rows: list[dict[str, Any]]) -> dict[str, str]:
    """공고 원문을 안건 번호 머리말로 잘라 「그 안건의 구간」을 돌려준다.

    조건부 상정 문구(「제4호 의안에서 선임된 독립이사 중 선임」)는 제목이 아니라 후보자 표의
    비고칸에 있다 — 제목만 보면 영영 못 잡는다.

    **번호만 보고 자르지 않는다.** 소집공고에는 첨부된 이사회 의사록이 함께 실려 있고 거기에도
    「제5호 의안 : … 가결」이 줄줄이 나온다. 번호의 첫 등장을 그대로 머리말로 삼으면 이사 보수한도
    안건이 **작년 이사회 의사록 구간**을 자기 본문으로 물고, 거기 있는 「가결」을 자기 조건절로
    읽는다(실측 태광산업 제7호). 그래서 **번호 뒤에 그 안건의 제목이 실제로 오는 자리**만
    머리말로 인정한다. 못 찾으면 그 안건은 dict 에 넣지 않는다 — 빈 문자열로 채우면
    「봤는데 없었다」로 읽힌다.
    """
    if not notice_text:
        return {}
    title_by_no = {r["number"]: (r.get("title") or "") for r in rows}
    picked: list[tuple[int, int, str]] = []
    for no, title in title_by_no.items():
        head = re.sub(r"\s+", "", title)[:8]
        chosen: tuple[int, int] | None = None
        first: tuple[int, int] | None = None
        for m in _AGENDA_NUM_RE.finditer(notice_text):
            if _norm_agenda_no(m.group(1)) != no:
                continue
            if first is None:
                first = (m.start(), m.end())
            if len(head) < 4:
                chosen = (m.start(), m.end())
                break
            # 번호 **바로 뒤에** 그 안건의 제목이 오나 — 공백을 지우고 본다(원문은 줄바꿈이 많다).
            # 「어딘가에 들어 있나」로 느슨하게 보면 안 된다. 나열 안의 번호
            # (「제4-1호, 제4-2호 의안에 대해서는 …」)도 200자 안에 다음 안건의 제목을 품고 있어
            # 나열이 머리말로 뽑힌다. 머리말은 「번호 · 의안 · 콜론 · 제목」 순서로 붙어 있다.
            probe = re.sub(r"\s+", "", notice_text[m.end():m.end() + 160])
            # 머리말과 제목 사이에 끼는 것들 — 「의안」·콜론·하이픈·※·괄호 주기
            # (「제 3 호 의안 (주주제안) : 주주 제안의 건」). 이걸 못 벗기면 그 안건의 머리말을
            # 못 찾고, 앞 안건의 구간이 그 자리를 삼켜 남의 조건절을 자기 것으로 문다
            # (실측 LG화학 제2호가 제3호의 「제2-7호 가결 시」를 물었다).
            probe = re.sub(r"^(?:의안|[:：\-–·.,※]|\([^)]*\)|\[[^\]]*\])+", "", probe)
            if probe.startswith(head):
                chosen = (m.start(), m.end())
                break
        if chosen is None:
            # 제목을 못 맞히면 머리말을 확정하지 못한 것이다. 제목이 너무 짧아 검사를 못 한
            # 경우에만 첫 등장으로 떨어진다(그때도 아래 창 상한이 걸린다).
            if len(head) >= 4:
                continue
            chosen = first if first else None
        if chosen:
            picked.append((chosen[0], chosen[1], no))
    if not picked:
        return {}
    picked.sort()
    out: dict[str, str] = {}
    for i, (_hstart, pos, no) in enumerate(picked):
        end = picked[i + 1][0] if i + 1 < len(picked) else len(notice_text)
        # 상호참조는 안건 머리말 가까이에 있다 — 창을 그만큼만 연다. 마지막 안건이 공고 꼬리
        # (참고사항·정관 원문·위임장 양식)를 통째로 삼키는 것을 막는다.
        # 머리말의 **번호 자체는 뺀다** — 안 그러면 「자기도 명단에 있나」 검사가 자기 머리말에
        # 걸려 항상 참이 된다.
        out[no] = notice_text[pos:min(end, pos + _AGENDA_SECTION_CHARS)]
    return out


#: 「제4-1호, 제4-2호, 제4-3호, 제4-4호 의안에 대해서는 …」처럼 **한 문장 안에 나란히 나열된**
#: 번호 묶음. 사이에 들어와도 되는 것은 구분자와 「의안」뿐이다 — 그 사이에 다른 말이 끼면
#: (「제5호 의안 : 이사 보수한도 요청의 건 가결 … 제6호 의안 : …」) 그건 나열이 아니라
#: 이사회 의사록이다. 이 구분이 없으면 공고 꼬리에 붙은 의사록이 택일 규칙으로 읽힌다.
_ENUM_RE = re.compile(
    r"(?:제\s*\d+(?:\s*-\s*\d+)*\s*호)"
    r"(?:(?:[\s,·、]|및|과|와|의안)*(?:제\s*\d+(?:\s*-\s*\d+)*\s*호))+")


def _enumeration_groups(text: str) -> list[list[str]]:
    out: list[list[str]] = []
    for m in _ENUM_RE.finditer(text or ""):
        out.append([_norm_agenda_no(x.group(1))
                    for x in _AGENDA_NUM_RE.finditer(m.group(0))])
    return out


def build_agenda_relation_links(
    rows: list[dict[str, Any]],
    notice_text: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """안건 사이의 관계를 안건 번호별 링크 목록으로 돌려준다.

    rows 의 각 원소는 최소한 `number`(제N호) · `title` · `category` · `proposer_type` ·
    `parent_number`(없으면 "") 를 가진다. 번호가 없는 행은 가리킬 이름이 없어 건너뛴다 —
    「4번과 경합」이라고 쓰려면 그 4번이 화면에 있어야 한다.
    """
    rows = [r for r in rows if (r.get("number") or "").strip()]
    if not rows:
        return {}
    # category 가 안 실려 오면(제목만 있는 폴백 경로) 여기서 분류한다 — 분류기를 두 벌 두지
    # 않으려고 proxy_advise 의 것을 그대로 부른다(순환 import 회피로 함수 안에서 import).
    if any(r.get("category") is None for r in rows):
        from open_proxy_mcp.services.proxy_advise import _classify_agenda
        by_no = {r.get("number"): r for r in rows}
        for r in rows:
            if r.get("category") is None:
                parent = by_no.get(r.get("parent_number") or "")
                r = r  # noqa: PLW0127 — 원본 dict 를 그대로 고친다
                r["category"] = _classify_agenda(
                    r.get("title") or "", parent_title=(parent or {}).get("title") or "")
    links: dict[str, list[dict[str, Any]]] = {}

    def _add(no: str, link: dict[str, Any]) -> None:
        bucket = links.setdefault(no, [])
        for existing in bucket:
            if existing["type"] == link["type"] and existing["with"] == link["with"]:
                return
        bucket.append(link)

    # ── ① 경합 — 같은 형제 층 · 같은 선임 카테고리에서 제안 주체가 갈린다 ────────
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("category") not in _ELECTION_CATEGORIES:
            continue
        if not _is_election_title(r.get("title") or ""):
            continue
        groups.setdefault(((r.get("parent_number") or ""), r["category"]), []).append(r)
    by_number = {r.get("number"): r for r in rows}
    for (_parent, _cat), group in groups.items():
        board = [r for r in group if r.get("proposer_type") == "company"]
        holder = [r for r in group if r.get("proposer_type") == "shareholder_proposal"]
        if not board or not holder:
            continue          # 한쪽뿐이면 경합이 아니다 — 금호석유화학이 여기서 걸러진다
        scope = _seat_scope(group[0].get("title") or "") or "이사"
        # 🔴 몇 자리를 뽑는지가 이 관계의 뜻을 바꾼다 — 한 자리면 택일, 여러 자리면 상한이고,
        #    **자리가 후보 수 이상이면 경합이 아니다.** 정원은 부모 안건 제목이 먼저 말한다
        #    (「이사 4인 선임」) — 자식은 부모의 정원·집중투표 여부를 물려받는다. 부모 행이
        #    rows 에 없으면(호출측이 자식만 넘긴 경우) 자식 행의 `parent_title` 로 받는다.
        parent_row = by_number.get(_parent) if _parent else None
        parent_title = ((parent_row or {}).get("title")
                        or next((r.get("parent_title") for r in group if r.get("parent_title")), None)
                        or "")
        seats, seats_quote, seats_source = _election_seats_for_group(parent_title, notice_text)
        cumulative = _is_cumulative_title(parent_title) or any(
            "cumulative_voting_title" in (r.get("agenda_relation_reasons") or []) for r in group)
        n_cand = len(group)
        _basis = ""
        if seats_quote:
            _basis = (f" 근거: 부모 안건 「{seats_quote}」" if seats_source == "parent_title"
                      else f" 근거: 「…{seats_quote}…」")
        if seats is not None and seats >= n_cand:
            # 🤝 자리가 후보 수 이상 — 같은 선거에 양쪽 후보가 올라와 있을 뿐 맞서는 것이
            #    아니다. 실측 고려아연 제2호: 집중투표 4석에 후보 4명(주주제안 2·이사회 2).
            #    여기서 「둘 다 불가」·「몇 명을 뽑는지 못 읽었다」를 붙이면 던질 수 있는 표를
            #    지운다. 관계는 남기되(제안 주체가 갈린 사실은 판을 보는 재료다) 판정은 막지 않는다.
            _how = (f"이 선거는 {'집중투표 ' if cumulative else ''}**{seats}석**에 후보 "
                    f"{n_cand}명이라 자리가 후보 수 이상입니다 — **전원 찬성이 가능**하고 진영을 "
                    f"고를 필요가 없습니다."
                    + (" 집중투표이므로 보유 의결권 × 석수를 후보에게 나눠 던지는 구조입니다 — "
                       "특정 후보를 밀려면 표를 몰아야 합니다." if cumulative else "")
                    + _basis)
            link_type = "same_election"
        elif seats is not None and seats >= 2 and n_cand > seats:
            if cumulative:
                _how = (f"이 선거는 **집중투표 {seats}석**에 후보 {n_cand}명입니다 — 보유 의결권 × "
                        f"{seats} 를 후보에게 나눠 던집니다. 진영을 하나로 정할 필요는 없지만 "
                        f"**{seats}명을 넘겨 찬성하면 표가 흩어져** 아무도 밀지 못합니다. "
                        f"몰아줄 후보를 정하세요." + _basis)
            else:
                _how = (f"이 묶음은 후보 {n_cand}명 중 **{seats}명**을 뽑습니다 — "
                        f"**최대 {seats}명까지 찬성할 수 있습니다.** 진영을 하나로 정할 필요는 "
                        f"없고, 양쪽에서 골라도 됩니다. 다만 {seats}명을 넘겨 찬성하면 표가 "
                        f"흩어집니다." + _basis)
            link_type = "contested"
        elif seats == 1 or (seats is None and n_cand == 2):
            _how = ("**둘 다 찬성할 수 없습니다.** 어느 진영을 지지할지 먼저 정하고 "
                    "그 진영의 안건에만 찬성하세요." + _basis)
            link_type = "contested"
        else:
            # 못 읽었으면 숫자를 지어내지 않는다 — 무엇을 확인해야 하는지만 말한다.
            _how = (f"**몇 명을 뽑는지 이 공고에서 읽지 못했습니다** — 원문의 선출 인원을 "
                    f"먼저 확인하세요(후보는 {n_cand}명입니다). 후보 수보다 적게 뽑는다면 "
                    f"전원 찬성은 표를 나눕니다.")
            link_type = "contested"
        for side, other, side_ko, other_ko in (
            (board, holder, "이사회제안", "주주제안"),
            (holder, board, "주주제안", "이사회제안"),
        ):
            other_nos = [r["number"] for r in other]
            for r in side:
                _add(r["number"], {
                    "type": link_type,
                    "with": other_nos,
                    "seats": seats,
                    "seats_source": seats_source,
                    "cumulative": cumulative,
                    "candidates": n_cand,
                    "note": (
                        (f"{', '.join(other_nos)}({other_ko})과 같은 {scope} 선거에 오른 "
                         f"{side_ko} 안건입니다 — {_how}")
                        if link_type == "same_election" else
                        (f"{', '.join(other_nos)}({other_ko})과 같은 {scope} 자리를 두고 "
                         f"맞선 {side_ko} 안건입니다 — {_how}")),
                })

    # ── ② 선행 의존 — 같은 자리의 해임이 같은 주총에 올라와 있다 ────────────────
    removals = [r for r in rows
                if _is_removal_title(r.get("title") or "", r.get("category"))]
    if removals:
        by_scope: dict[str, list[str]] = {}
        for r in removals:
            scope = _seat_scope(r.get("title") or "")
            if scope:
                by_scope.setdefault(scope, []).append(r["number"])
        for r in rows:
            if not _is_election_title(r.get("title") or ""):
                continue
            if r.get("category") not in _ELECTION_CATEGORIES:
                continue
            scope = _seat_scope(r.get("title") or "")
            targets = by_scope.get(scope)
            if not targets:
                continue
            _add(r["number"], {
                "type": "depends_on",
                "with": targets,
                "note": (f"{', '.join(targets)}({scope} 해임)이 가결돼야 채울 자리가 생깁니다 — "
                         f"해임이 부결되면 이 선임은 상정 자체가 무의미해질 수 있습니다. "
                         f"두 안건을 따로 판단하지 마세요."),
            })
            for t in targets:
                _add(t, {
                    "type": "precedes",
                    "with": [r["number"]],
                    "note": f"이 해임이 가결돼야 {r['number']} 선임이 의미를 갖습니다.",
                })

    # ── ③ 조건부 상정 — 공고 본문이 다른 안건을 가리킨다 ──────────────────────
    numbers = [r["number"] for r in rows]
    sections = _agenda_sections(notice_text, rows)
    for r in rows:
        no = r["number"]
        text = " ".join(x for x in [sections.get(no, ""), (r.get("conditional") or "")] if x)
        if not text:
            continue
        for m in _AGENDA_NUM_RE.finditer(text):
            ref = _norm_agenda_no(m.group(1))
            if ref == no or ref not in numbers:
                continue
            window = text[max(0, m.start() - 60):m.end() + 80]
            tail = text[m.end():m.end() + 20]
            # ⓐ **방향성 참조** — 「제4호 의안에서 선임된」·「제2호가 가결될 경우」처럼 이 안건이
            #    저 안건에 걸린다고 문장이 직접 말한다.
            directional = any(k in tail for k in _DIRECTIONAL_REFERENCE)
            # ⓑ **자기도 같은 나열에 있는 참조** — 「제4-1호, 제4-2호, 제4-3호, 제4-4호 의안에
            #    대해서는 … 다득표 의안이 가결된 것으로 합니다」. 같은 묶음의 택일 규칙이다.
            #    번호가 창 안에 있기만 하면 안 된다 — **한 나열 안에 함께** 있어야 한다.
            group = next((g for g in _enumeration_groups(window)
                          if no in g and ref in g), None)
            self_listed = bool(group) and any(k in window for k in _DEPENDENCY_MARKERS)
            if not directional and not self_listed:
                # 남는 것은 **공고 꼬리의 ※ 각주**가 옆 안건 구간에 흘러든 경우다. 실측 태광산업
                # 이사 보수한도(제7호)가 바로 뒤에 붙은 「※ 제2-2-1호 … 다득표」를 자기 조건으로
                # 물고 나갔다 — 사실이 아니다. 근거가 이 둘 중 하나가 아니면 링크를 만들지 않는다.
                continue
            quote = re.sub(r"\s+", " ", window).strip()
            # 같은 문장이 여러 안건을 나열하면 **전부** 가리킨다 — 하나만 쓰면 판이 안 보인다.
            refs = [x for x in dict.fromkeys(group or [])
                    if x != no and x in numbers] or [ref]
            _add(no, {
                "type": "conditional_on",
                "with": refs,
                "note": (
                    (f"공고 본문이 {ref}을 가리킵니다 — 「…{quote}…」. "
                     f"{ref}의 결과에 따라 이 안건의 상정·효력이 갈립니다.")
                    if directional else
                    (f"공고가 이 안건을 {', '.join(refs)}과 **한 묶음의 택일**로 처리한다고 "
                     f"밝혔습니다 — "
                     f"「…{quote}…」. 전부 찬성하면 표가 흩어집니다.")),
            })
            break
    return links


#: 관계 유형 → 표에 찍을 짧은 라벨. 판정 자리가 아니라 **관계** 자리에 쓴다.
AGENDA_RELATION_LINK_LABEL = {
    # 자리 수를 모를 때의 기본 문구. 자리가 둘 이상이면 아래 함수가 갈아 끼운다.
    "contested": "⚔️ {nos}와 경합 — 선출 인원 확인 필요",
    # 자리가 후보 수 이상 — 제안 주체는 갈리지만 맞서는 것이 아니다(판정을 막지 않는다).
    "same_election": "🤝 {nos}와 같은 선거 — 전원 찬성 가능",
    "depends_on": "🔗 {nos} 가결이 전제",
    "precedes": "🔗 {nos} 선임의 선행 안건",
    "conditional_on": "🔗 {nos} 결과에 연동",
    "bundled": "📦 일괄표결 — 개별 찬반 불가",
}


def agenda_relation_link_label(link: dict[str, Any]) -> str:
    nos = ", ".join(link.get("with") or [])
    if link.get("type") == "contested":
        # 🔴 라벨 한 줄이 표에서 제일 먼저 읽힌다 — 여기서 「둘 다 불가」라고 쓰면
        #    본문을 안 읽은 사람이 자리 둘짜리 안건에서 표를 버린다(2026-08-30 U 6차).
        seats = link.get("seats")
        if isinstance(seats, int) and seats >= 2:
            if link.get("cumulative"):
                return f"⚔️ {nos}와 경합 — 집중투표 {seats}석, {seats}명 넘겨 찬성하면 표 분산"
            return f"⚔️ {nos}와 경합 — {seats}명까지 찬성 가능"
        if seats == 1:
            return f"⚔️ {nos}와 경합 — 둘 다 찬성 불가"
    if link.get("type") == "same_election":
        seats = link.get("seats")
        n = link.get("candidates")
        if isinstance(seats, int) and isinstance(n, int):
            return (f"🤝 {nos}와 같은 선거 — {'집중투표 ' if link.get('cumulative') else ''}"
                    f"{seats}석에 후보 {n}명, 전원 찬성 가능")
    tpl = AGENDA_RELATION_LINK_LABEL.get(link.get("type") or "", "")
    return tpl.format(nos=nos) if tpl else ""



def _compact_meeting_info(info: dict[str, Any], scope: str) -> dict[str, Any]:
    """summary 응답에서는 긴 안내문을 빼고 주총 식별 필드만 남긴다."""
    if scope != "summary":
        return info
    return {key: value for key, value in info.items() if key in _SUMMARY_MEETING_INFO_KEYS}


def _flatten_agendas(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        flattened.append({
            "number": item.get("number", ""),
            "title": item.get("title", ""),
            "source": item.get("source"),
            "conditional": item.get("conditional"),
        })
        flattened.extend(_flatten_agendas(item.get("children", [])))
    return flattened


def _normalize_notice_row(item: dict[str, Any], meeting_info: dict[str, Any]) -> dict[str, Any]:
    return {
        "rcept_no": item.get("rcept_no", ""),
        "report_name": (item.get("report_nm") or "").strip(),
        "disclosure_date": item.get("rcept_dt", ""),
        "filer_name": item.get("flr_nm", ""),
        "meeting_type": meeting_info.get("meeting_type"),
        "meeting_term": meeting_info.get("meeting_term"),
        "is_correction": meeting_info.get("is_correction", False),
        "datetime": meeting_info.get("datetime"),
        "location": meeting_info.get("location"),
    }


def _mark_timing(timings_ms: dict[str, int] | None, stage: str, started_at: float) -> None:
    if timings_ms is not None:
        timings_ms[stage] = int((time.perf_counter() - started_at) * 1000)


def _annual_window_from_fiscal_month(target_year: int, fiscal_month: str) -> tuple[date, date] | None:
    month_text = (fiscal_month or "").strip()
    if not month_text.isdigit():
        return None
    month = int(month_text)
    if month < 1 or month > 12:
        return None

    if month == 12:
        return date(target_year, 1, 1), date(target_year, 4, 30)

    start_month = month + 1
    if start_month > 12:
        return None
    start = date(target_year, start_month, 1)
    end_month = min(month + 4, 12)
    end_day = 31
    if end_month in {4, 6, 9, 11}:
        end_day = 30
    elif end_month == 2:
        end_day = 29 if target_year % 4 == 0 and (target_year % 100 != 0 or target_year % 400 == 0) else 28
    return start, date(target_year, end_month, end_day)


async def _safe_fiscal_month(corp_code: str) -> str:
    try:
        info = await get_dart_client().get_company_info(corp_code)
    except Exception:
        return ""
    fiscal_month = (info.get("acc_mt") or "").strip()
    if not fiscal_month.isdigit():
        return ""
    month = int(fiscal_month)
    if month < 1 or month > 12:
        return ""
    return f"{month:02d}" if len(fiscal_month) == 1 else fiscal_month


async def _candidate_notices_range(
    corp_code: str,
    meeting_type_label: str,
    bgn_de: str,
    end_de: str,
    *,
    timings_ms: dict[str, int] | None = None,
    top_n: int = 2,
) -> tuple[list[dict[str, Any]], list[str]]:
    client = get_dart_client()
    # last_reprt_at='Y' — 정정공시 자동 정리 (최종본만). 정정 다수 회사
    # (현대차/삼성전자 등)에서 candidate 개수 N=2-3 → 1로 줄어듦.
    stage_started_at = time.perf_counter()
    filings, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys="",
        pblntf_detail_ty="E006",  # 주주총회소집공고 ∈ E006 (차집합 0 검증) — E 전체 페이지컷 회피
        keywords=("소집",),
        last_reprt_at="Y",
    )
    _mark_timing(timings_ms, "select_notice_candidate.search_filings", stage_started_at)
    if error and error != "013":
        raise DartClientError(error, "주총 소집공고 검색 실패")
    if error == "013":
        filings = []
    # E type 결과 부족 시 모든 type fallback (에스엠/고려아연 등 누락 대응).
    if not filings:
        try:
            stage_started_at = time.perf_counter()
            data = await client.search_filings(
                corp_code=corp_code, bgn_de=bgn_de, end_de=end_de,
                pblntf_ty=None,  # 전 type
                last_reprt_at="Y",
            )
            _mark_timing(timings_ms, "select_notice_candidate.search_filings_fallback", stage_started_at)
            all_items = data.get("list", []) or []
            filings = [
                i for i in all_items
                if "주주총회소집공고" in i.get("report_nm", "") or "소집" in i.get("report_nm", "")
            ]
        except Exception:
            pass
    # 최신 정정공시 우선 (rcept_dt + rcept_no desc).
    # 일반적으로 최신 1-2건이 사용자 의도와 일치 — 정기 1번 + 정정 1-2 또는 임시.
    filings.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)
    if not filings:
        return [], notices

    async def _resolve_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        batch_label = "top" if batch == filings[:len(batch)] else "remaining"
        stage_started_at = time.perf_counter()
        docs = await asyncio.gather(*[
            client.get_document_cached(item["rcept_no"]) for item in batch
        ])
        _mark_timing(timings_ms, f"select_notice_candidate.fetch_{batch_label}_documents", stage_started_at)
        out: list[dict[str, Any]] = []
        stage_started_at = time.perf_counter()
        for item, doc in zip(batch, docs):
            text = doc.get("text", "")
            html = doc.get("html", "")
            info, info_source = await _notice_info_with_fallback(item["rcept_no"], text, html)
            normalized = _normalize_notice_row(item, info)
            normalized["notice_source"] = info_source
            if normalized["meeting_type"] == meeting_type_label:
                out.append(normalized)
        _mark_timing(timings_ms, f"select_notice_candidate.parse_{batch_label}_documents", stage_started_at)
        return out

    # 1차: 상위 후보만 doc fetch. fiscal window로 좁힌 annual 검색은 최신 1건부터 시도한다.
    top_n = max(1, top_n)
    matched = await _resolve_batch(filings[:top_n])

    # 2차 fallback: 1차에서 meeting_type 일치 못 찾으면 나머지 전체 fetch.
    # rare case (정기/임시 섞임 + 임시가 최신 + 사용자가 annual 요청 등).
    if not matched and len(filings) > top_n:
        matched = await _resolve_batch(filings[top_n:])

    return matched, notices


async def _candidate_notices(
    corp_code: str,
    meeting_type_label: str,
    year: int,
    *,
    timings_ms: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    return await _candidate_notices_in_meeting_window(
        corp_code,
        meeting_type_label,
        date(year, 1, 1),
        date(year, 12, 31),
        timings_ms=timings_ms,
    )


async def _candidate_notices_in_meeting_window(
    corp_code: str,
    meeting_type_label: str,
    meeting_start: date,
    meeting_end: date,
    *,
    timings_ms: dict[str, int] | None = None,
    top_n: int = 2,
) -> tuple[list[dict[str, Any]], list[str]]:
    search_start = meeting_start - timedelta(days=_NOTICE_LEAD_BUFFER_DAYS)
    notices, search_notices = await _candidate_notices_range(
        corp_code,
        meeting_type_label,
        search_start.strftime("%Y%m%d"),
        meeting_end.strftime("%Y%m%d"),
        timings_ms=timings_ms,
        top_n=top_n,
    )
    matched: list[dict[str, Any]] = []
    stage_started_at = time.perf_counter()
    for notice in notices:
        meeting_date = _parse_notice_meeting_date(notice.get("datetime", ""))
        if meeting_date and meeting_start <= meeting_date <= meeting_end:
            matched.append(notice)
            continue
        # 회의일자 파싱 실패 케이스(예: CJ ENM 공시 본문 구조 불일치) fallback:
        # 공시 접수일(rcept_dt)이 meeting window 안에 있고 NOTICE_LEAD_BUFFER_DAYS 이내이면 포함.
        # 실제 회의일은 후속 파싱 단계에서 확보할 수 있으며, 여기서 버리면 아예 공시를 놓친다.
        if not meeting_date:
            disclosure_date = notice.get("disclosure_date", "")
            if len(disclosure_date) >= 8 and disclosure_date[:8].isdigit():
                try:
                    rcept = date(int(disclosure_date[:4]), int(disclosure_date[4:6]), int(disclosure_date[6:8]))
                    if search_start <= rcept <= meeting_end:
                        matched.append(notice)
                except ValueError:
                    pass
    _mark_timing(timings_ms, "select_notice_candidate.filter_meeting_window", stage_started_at)
    return matched, search_notices


def _pick_latest_notice(notices: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not notices:
        return None
    notices = sorted(notices, key=lambda row: (row.get("disclosure_date", ""), row.get("rcept_no", "")))
    return notices[-1]


def _correction_summary(html: str) -> dict[str, Any] | None:
    parsed = parse_corrections_xml(html)
    if not parsed:
        return None
    return {
        "is_correction": parsed.get("is_correction", False),
        "date": parsed.get("date"),
        "original_date": parsed.get("original_date"),
        "reason": parsed.get("reason"),
        "items": parsed.get("items", []),
    }


def _parse_notice_meeting_date(datetime_text: str) -> date | None:
    if not datetime_text:
        return None
    match = None
    compact = re.sub(r"\s+", "", datetime_text)
    for pattern in (
        r"(\d{4})[.-](\d{1,2})[.-](\d{1,2})",
        r"(\d{4})년(\d{1,2})월(\d{1,2})일",
    ):
        match = re.search(pattern, compact)
        if match:
            break
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _parse_notice_bundle(
    text: str,
    html: str,
    *,
    rcept_no: str,
    soup_cache: dict[tuple[str, str, Any], Any] | None = None,
) -> dict[str, Any]:
    with _cached_notice_parser_soup(soup_cache, rcept_no):
        meeting_info = parse_meeting_info_xml(text, html=html)
        agenda = parse_agenda_xml(text, html=html)
        board = parse_personnel_xml(html) if html else {"appointments": [], "summary": {}}
        compensation = parse_compensation_xml(html) if html else {"items": [], "summary": {}}
    return {
        "text": text,
        "html": html,
        "meeting_info": meeting_info,
        "agenda": agenda,
        "agenda_valid": validate_agenda_result(agenda),
        "board": board,
        "compensation": compensation,
        "correction": _correction_summary(html) if html else None,
    }


def _agenda_titles(items: list[dict[str, Any]]) -> list[str]:
    titles: list[str] = []
    for item in items:
        title = (item.get("title") or "").strip()
        if title:
            titles.append(title)
        titles.extend(_agenda_titles(item.get("children", [])))
    return titles


def _needs_notice_viewer_fallback(parsed: dict[str, Any], *, scope: str) -> list[str]:
    reasons: list[str] = []
    meeting_info = parsed["meeting_info"]
    if not parsed["html"]:
        reasons.append("api_html_missing")
    if not meeting_info.get("meeting_type"):
        reasons.append("meeting_type_missing")
    if not meeting_info.get("datetime"):
        reasons.append("meeting_datetime_missing")
    if not parsed["agenda_valid"]:
        reasons.append("agenda_parse_low_confidence")

    agenda_titles = _agenda_titles(parsed["agenda"])
    board_expected = any(("선임" in title or "해임" in title) and ("이사" in title or "감사" in title) for title in agenda_titles)
    compensation_expected = any("보수" in title and "한도" in title for title in agenda_titles)

    if scope in {"board", "full", "advise"} and board_expected and not parsed["board"].get("appointments"):
        reasons.append("board_parse_empty")
    if scope in {"compensation", "full", "advise"} and compensation_expected and not parsed["compensation"].get("items"):
        reasons.append("compensation_parse_empty")
    return reasons


async def _load_notice_bundle_with_fallback(
    rcept_no: str,
    *,
    scope: str,
    soup_cache: dict[tuple[str, str, Any], Any] | None = None,
) -> tuple[dict[str, Any], list[str], str]:
    client = get_dart_client()
    doc = await client.get_document_cached(rcept_no)
    parsed = _parse_notice_bundle(
        doc.get("text", ""),
        doc.get("html", ""),
        rcept_no=rcept_no,
        soup_cache=soup_cache,
    )
    reasons = _needs_notice_viewer_fallback(parsed, scope=scope)
    warnings: list[str] = []
    source_used = "dart_xml"

    if not reasons:
        return parsed, warnings, source_used

    section_keywords = ["주주총회 소집공고", "주주총회소집공고"]
    if scope in {"board", "compensation", "aoi_change", "full", "advise"}:
        section_keywords.extend(["목적사항별 기재사항", "주주총회 목적사항별 기재사항"])

    warnings.append(f"API/XML 파싱이 약해 DART viewer HTML 수집 fallback을 시도했다. ({', '.join(reasons)})")
    try:
        viewer_doc = await client.get_viewer_document(rcept_no, section_keywords=section_keywords)
    except Exception as exc:
        warnings.append(f"DART viewer HTML 수집 fallback도 실패했다: {exc}")
        return parsed, warnings, source_used

    viewer_parsed = _parse_notice_bundle(
        viewer_doc.get("text", ""),
        viewer_doc.get("html", ""),
        rcept_no=rcept_no,
        soup_cache=soup_cache,
    )
    improved = False

    if (not parsed["meeting_info"].get("meeting_type")) and viewer_parsed["meeting_info"].get("meeting_type"):
        parsed["meeting_info"] = viewer_parsed["meeting_info"]
        improved = True
    if (not parsed["meeting_info"].get("datetime")) and viewer_parsed["meeting_info"].get("datetime"):
        parsed["meeting_info"] = viewer_parsed["meeting_info"]
        improved = True
    if (not parsed["agenda_valid"]) and viewer_parsed["agenda_valid"]:
        parsed["agenda"] = viewer_parsed["agenda"]
        parsed["agenda_valid"] = True
        parsed["text"] = viewer_parsed["text"]
        parsed["html"] = viewer_parsed["html"]
        improved = True
    if scope in {"board", "full", "advise"} and len(viewer_parsed["board"].get("appointments", [])) > len(parsed["board"].get("appointments", [])):
        parsed["board"] = viewer_parsed["board"]
        improved = True
    if scope in {"compensation", "full", "advise"} and len(viewer_parsed["compensation"].get("items", [])) > len(parsed["compensation"].get("items", [])):
        parsed["compensation"] = viewer_parsed["compensation"]
        improved = True

    if improved:
        source_used = "dart_html"
        warnings.append("DART viewer HTML crawl 결과를 반영해 notice 파싱 품질을 보정했다.")
    else:
        # viewer HTML은 확보했지만 구조 파싱 개선 안 된 경우.
        # viewer text가 XML text보다 풍부하면(표·섹션 구조 보존) raw text fallback에 쓸 수 있게 교체.
        viewer_text = (viewer_parsed.get("text") or "").strip()
        xml_text = (parsed.get("text") or "").strip()
        if len(viewer_text) > len(xml_text):
            parsed["text"] = viewer_text
            warnings.append("DART viewer HTML 수집 결과의 원문 텍스트가 XML 텍스트보다 풍부해 원문 text 대체 소스로 교체했다.")
        else:
            warnings.append("DART viewer HTML crawl을 재시도했지만 구조화 결과는 기존 API/XML보다 개선되지 않았다.")
    return parsed, warnings, source_used


async def _notice_info_with_fallback(
    rcept_no: str,
    text: str,
    html: str,
) -> tuple[dict[str, Any], str]:
    meeting_info = parse_meeting_info_xml(text, html=html)
    if meeting_info.get("meeting_type") and meeting_info.get("datetime"):
        return meeting_info, "dart_xml"

    client = get_dart_client()
    try:
        viewer_doc = await client.get_viewer_document(
            rcept_no,
            section_keywords=["주주총회 소집공고", "주주총회소집공고"],
        )
    except Exception:
        return meeting_info, "dart_xml"

    viewer_info = parse_meeting_info_xml(viewer_doc.get("text", ""), html=viewer_doc.get("html", ""))
    if viewer_info.get("meeting_type") or viewer_info.get("datetime"):
        return viewer_info, "dart_html"
    return meeting_info, "dart_xml"


# 결과공시는 회의일로부터 이 날수 안에 접수된 것만 그 회차의 결과로 본다.
# 거래소 규정상 당일~익영업일이 보통이나 정정·지연 접수를 감안해 넉넉히 둔다.
_RESULT_WINDOW_DAYS = 30


async def _find_meeting_result_filing(
    corp_code: str,
    target_year: int,
    notice: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    result_items, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=f"{target_year}0101",
        end_de=f"{target_year}1231",
        pblntf_tys="",
        pblntf_detail_ty="I001",  # 정기주주총회결과 ∈ I001 (차집합 0 검증) — I 전체 페이지컷 회피
        keywords=("주주총회결과",),
    )
    if error:
        return None, f"주주총회결과 공시 검색 실패: {error}", notices
    if not result_items:
        return None, "주주총회결과 공시를 찾지 못했다.", notices

    meeting_date = _parse_notice_meeting_date(notice.get("datetime", ""))

    # 🔴 **결과는 회의 뒤에만 있다.** 예전엔 그 해 전체에서 «가장 가까운» 결과공시를 집었고,
    #    거리에 부호가 없어 **아직 열리지 않은 회차에 지난 회차의 결과가 붙었다**
    #    (2026-08-28 실측 — 대림제지 2026-09-04 임시주총 머리에 「결과 공시 확보」,
    #    참석률 73.1%는 3월 정기주총 결과였다. 주총 «전» 판단에 사후 정보가 새는 자리다).
    #    ① 회의일이 아직 안 왔으면 결과는 없다. ② 있어도 **회의일 당일 이후** 접수분만 본다.
    if meeting_date:
        if meeting_date > date.today():
            return None, "회의일이 아직 오지 않아 결과공시는 존재할 수 없다.", notices
        after = []
        for it in result_items:
            rcept_dt = it.get("rcept_dt", "")
            if len(rcept_dt) != 8:
                continue
            try:
                filing_date = datetime.strptime(rcept_dt, "%Y%m%d").date()
            except ValueError:
                continue
            if meeting_date <= filing_date <= meeting_date + timedelta(days=_RESULT_WINDOW_DAYS):
                after.append(it)
        if not after:
            return (None,
                    f"회의일({meeting_date.isoformat()}) 이후 {_RESULT_WINDOW_DAYS}일 안에 "
                    "접수된 주주총회결과 공시가 없다.", notices)
        result_items = after

    def sort_key(item: dict[str, Any]) -> tuple[int, str]:
        rcept_dt = item.get("rcept_dt", "")
        if meeting_date and len(rcept_dt) == 8:
            try:
                filing_date = datetime.strptime(rcept_dt, "%Y%m%d").date()
                distance = abs((filing_date - meeting_date).days)
            except ValueError:
                distance = 9999
        else:
            distance = 9999
        return (distance, rcept_dt)

    result_items.sort(key=sort_key)
    # [기재정정] 제외 우선 — 정정 본문이 변경 부분만 담을 위험 회피.
    # 비면 정정 포함 fallback ([[architecture/multi-upstream-pattern]]).
    non_corr = [it for it in result_items if not (it.get("report_nm") or "").startswith("[기재정정]")]
    return (non_corr or result_items)[0], None, notices


def _result_reference(result_filing: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result_filing:
        return None

    rcept_no = result_filing.get("rcept_no", "")
    whitelist_ok = bool(rcept_no and len(rcept_no) == 14 and rcept_no[8:10] == "80")
    dart_fetchable = bool(rcept_no and len(rcept_no) == 14 and rcept_no.isdigit())
    kind_acptno = rcept_no[:8] + "00" + rcept_no[10:] if whitelist_ok else None
    return {
        "rcept_no": rcept_no,
        "report_name": result_filing.get("report_nm", ""),
        "disclosure_date": result_filing.get("rcept_dt", ""),
        "kind_acptno": kind_acptno,
        "whitelist_ok": whitelist_ok,
        "dart_fetchable": dart_fetchable,
    }


def _meeting_phase(
    meeting_info: dict[str, Any],
    result_filing: dict[str, Any] | None,
    result_reference: dict[str, Any] | None,
) -> tuple[str, str]:
    meeting_date = _parse_notice_meeting_date(meeting_info.get("datetime", ""))
    today = date.today()

    if result_filing:
        if result_reference and result_reference.get("dart_fetchable"):
            return "post_result", "available"
        return "post_result", "requires_review"

    if meeting_date:
        if meeting_date > today:
            return "pre_meeting", "not_due_yet"
        return "post_meeting_pre_result", "pending_or_missing"

    return "undetermined", "unknown"


def _phase_priority(meeting_phase: str) -> int:
    return {
        "pre_meeting": 3,
        "post_result": 2,
        "post_meeting_pre_result": 1,
        "undetermined": 0,
    }.get(meeting_phase, 0)


def _candidate_meta(candidate: dict[str, Any]) -> dict[str, Any]:
    notice = candidate["notice"]
    result_reference = candidate.get("result_reference") or {}
    meeting_date = candidate.get("meeting_date")
    return {
        "meeting_type": candidate.get("meeting_type"),
        "meeting_phase": candidate.get("meeting_phase"),
        "result_status": candidate.get("result_status"),
        "meeting_date": meeting_date.isoformat() if meeting_date else None,
        "notice_rcept_no": notice.get("rcept_no", ""),
        "notice_date": notice.get("disclosure_date", ""),
        "notice_report_name": notice.get("report_name", ""),
        "result_rcept_no": result_reference.get("rcept_no", ""),
        "result_date": result_reference.get("disclosure_date", ""),
    }


def _meeting_presence_flag(has_annual: bool, has_extraordinary: bool) -> str:
    if has_annual and has_extraordinary:
        return "annual_and_extraordinary"
    if has_annual:
        return "annual_only"
    if has_extraordinary:
        return "extraordinary_only"
    return "none"


def _round_year(target_year: int | None, meeting_date: date | None, notice_rcept_no: str) -> int:
    """회차의 연도 — **회의가 열리는 해**다.

    조회 구간의 끝에서 뽑던 값이라 두 방향으로 틀렸다:
      - 구간 끝이 오늘+90일이라 연말엔 다음 해로 넘어가, 올해 주총이 내년 회차로 찍힌다.
      - 12개월 lookback 으로 작년 회의를 골랐을 땐 반대로 올해 회차로 찍힌다.
    회의일을 기준으로 삼으면 둘 다 생기지 않는다.
    """
    if target_year:
        return target_year
    if meeting_date:
        return meeting_date.year
    # 회의일을 못 읽은 경우 — 공고 접수연도가 오늘보다 회의에 가깝다(공고는 회의 前 몇 주).
    if len(notice_rcept_no) >= 4 and notice_rcept_no[:4].isdigit():
        return int(notice_rcept_no[:4])
    return date.today().year


def _selection_window(
    target_year: int | None,
    *,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 12,
) -> tuple[date, date, list[str]]:
    if start_date or end_date:
        return resolve_date_window(
            start_date=start_date,
            end_date=end_date,
            default_end=date.today(),
            lookback_months=lookback_months,
        )
    if target_year:
        return date(target_year, 1, 1), date(target_year, 12, 31), []

    # 후보 필터는 회의일 기준이다(_candidate_notices_in_meeting_window). 구간 끝을 오늘로 자르면
    # **아직 열리지 않은 주총만 골라서** 탈락한다 — 소집공고는 회의 前에 나오고 의결권도 회의 前에
    # 행사하니, 하필 지금 표를 던져야 하는 회차가 사라지고 이미 끝난 회차만 남는다.
    # 실측 애경케미칼: 2026-07-30 임시주총 소집공고 / 회의일 08-14 → 08-08에 조회하면 공고를
    # 받아온 뒤 버리고 3월 정기주총을 「후보가 1개」라며 내놓았다.
    # 구간 시작점에는 이미 같은 이유의 lead buffer가 있다. 끝점도 대칭으로 열어둔다.
    # lookback 기준점은 오늘 그대로라 과거 구간은 줄지 않는다(12개월 유지, 앞으로만 넓어짐).
    start, end, warnings = resolve_date_window(
        start_date="",
        end_date="",
        default_end=date.today(),
        lookback_months=lookback_months,
    )
    return start, end + timedelta(days=_NOTICE_LEAD_BUFFER_DAYS), warnings


def _auto_selection_basis(candidate: dict[str, Any], scope: str, candidates: list[dict[str, Any]]) -> str:
    if len(candidates) == 1:
        return "후보가 1개라 해당 회차를 자동 선택했다."

    if scope == "results":
        if candidate.get("result_status") == "available":
            return "결과 조회 요청이라 결과공시가 확인된 회차 중 가장 최신 회차를 선택했다."
        return "결과 조회 요청이었지만 결과공시가 확인된 회차가 없어 가장 관련성 높은 회차를 선택했다."

    basis = []
    basis.append("일반 주총 조회라 정기/임시를 가리지 않고 가장 현재적인 회차를 우선했다.")
    phase = candidate.get("meeting_phase")
    if phase == "pre_meeting":
        basis.append("아직 회의 전인 예정 회차라 현재 안건 검토 대상에 가깝다.")
    elif phase == "post_result":
        basis.append("결과공시까지 확인된 최신 회차다.")
    elif phase == "post_meeting_pre_result":
        basis.append("회의는 종료됐지만 결과공시는 아직 확인되지 않았다.")
    return " ".join(basis)


def _auto_rank_key(candidate: dict[str, Any], scope: str) -> tuple[int, int, int, int]:
    meeting_date = candidate.get("meeting_date")
    meeting_ordinal = meeting_date.toordinal() if meeting_date else 0
    is_annual = 1 if candidate.get("meeting_type") == "annual" else 0
    has_result = 1 if candidate.get("result_status") == "available" else 0
    phase_priority = _phase_priority(candidate.get("meeting_phase", ""))

    if scope == "results":
        return (has_result, meeting_ordinal, phase_priority, is_annual)
    return (phase_priority, meeting_ordinal, has_result, is_annual)


async def _build_candidate(
    corp_code: str,
    meeting_type: str,
    target_year: int,
    notice: dict[str, Any],
    *,
    fetch_result_filing: bool = True,
) -> dict[str, Any]:
    """notice candidate에 result_filing 정보 결합.

    fetch_result_filing=False일 때 (예: scope=summary/board/compensation):
    - DART 결과 공시 검색 생략 (5초+ 단축)
    - meeting_phase는 meeting_date 기준 단순 분류 (pre/post)
    - result_filing / result_reference 는 None (필요한 scope에서 별도 fetch)
    """
    meeting_date = _parse_notice_meeting_date(notice.get("datetime", ""))
    result_search_year = meeting_date.year if meeting_date else target_year

    if fetch_result_filing:
        result_filing, result_filing_warning, result_search_notices = await _find_meeting_result_filing(
            corp_code,
            result_search_year,
            notice,
        )
        result_reference = _result_reference(result_filing)
        meeting_phase, result_status = _meeting_phase(notice, result_filing, result_reference)
    else:
        # date 기반 단순 phase 판단 (DART 호출 0)
        result_filing = None
        result_filing_warning = None
        result_search_notices = []
        result_reference = None
        meeting_phase, result_status = _meeting_phase(notice, None, None)

    return {
        "meeting_type": meeting_type,
        "meeting_type_label": _MEETING_TYPE_MAP[meeting_type],
        "notice": notice,
        "meeting_date": meeting_date,
        "result_search_year": result_search_year,
        "result_filing": result_filing,
        "result_filing_warning": result_filing_warning,
        "result_reference": result_reference,
        "meeting_phase": meeting_phase,
        "result_status": result_status,
        "search_notices": result_search_notices,
    }


async def resolve_latest_meeting_year(
    corp_code: str,
    *,
    meeting_type: str = "annual",
    lookback_months: int = 12,
    year: int | None = None,
) -> dict[str, Any] | None:
    """최신 소집공고 기준 주총 회차(연도) pre-resolution.

    proxy_advise 등 downstream이 year 미지정 호출 시 "달력 전년" 대신
    실제 최신 소집공고의 회의연도를 target_year로 쓰기 위한 가벼운 선행 조회.
    비용: 유형별 list.json 1콜 + 상위 공고 doc 파싱(get_document_cached — 이후
    build_shareholder_meeting_payload가 같은 doc을 캐시로 재사용).

    Returns:
        {year, meeting_type, meeting_date(date|None), notice_rcept_no,
         notice_date, meeting_phase} 또는 공고 미발견 시 None.
        meeting_phase는 date 기반 단순 분류 (pre_meeting / post_meeting_pre_result
        / undetermined) — 결과공시 존재 여부(post_result)는 확인하지 않는다.
    """
    if meeting_type not in _ALLOWED_MEETING_TYPES:
        return None
    today = date.today()
    # `year` 를 주면 그 해의 회차를 집는다. 260828: 이 인자가 없어서, 사용자가 연도를 직접
    # 지정하면 **회의일을 아예 모르는 채로** 분석이 진행됐다. 회의일을 모르면 「그 시점에 볼 수
    # 있던 공시」의 경계도 그을 수 없다 — proxy_advise 의 as_of 기본값이 여기서 나온다.
    if year:
        window_start = date(year, 1, 1)
        window_end = date(year, 12, 31)
        return await _resolve_meeting_in_window(
            corp_code, meeting_type, window_start, window_end)
    # meeting window end를 미래로 확장 (260723 리뷰 CRITICAL 수정): 필터가 '회의일' 기준이라
    # end=today면 회의일이 미래인 공고(= 소집공고 발행 후 ~ 주총 전, 이 pre-resolution의 1차
    # 사용 구간)가 통째로 탈락해 작년 회차를 "최신"으로 오선택했다. 공고→회의 간격은 상법상
    # 2주+, 실무 2~6주 — _NOTICE_LEAD_BUFFER_DAYS(90일)면 충분히 덮는다.
    # (DART list.json의 미래 end_de는 무해 — 접수일 필터일 뿐이며 기존 연도지정 경로도 12/31 사용)
    window_end = today + timedelta(days=_NOTICE_LEAD_BUFFER_DAYS)
    window_start = today - timedelta(days=lookback_months * 31)
    return await _resolve_meeting_in_window(
        corp_code, meeting_type, window_start, window_end)


async def _resolve_meeting_in_window(
    corp_code: str,
    meeting_type: str,
    window_start: date,
    window_end: date,
) -> dict[str, Any] | None:
    """주어진 회의일 구간에서 최신 소집공고 하나를 골라 회차 메타를 돌려준다."""
    types = ["annual", "extraordinary"] if meeting_type == "auto" else [meeting_type]
    results = await asyncio.gather(*[
        _candidate_notices_in_meeting_window(
            corp_code, _MEETING_TYPE_MAP[t], window_start, window_end,
        )
        for t in types
    ])
    picked: tuple[str, dict[str, Any]] | None = None
    for t, (notices, _search_notes) in zip(types, results):
        latest = _pick_latest_notice(notices)
        if not latest:
            continue
        if picked is None or (latest.get("disclosure_date", "") > picked[1].get("disclosure_date", "")):
            picked = (t, latest)
    if picked is None:
        return None
    picked_type, notice = picked
    meeting_date = _parse_notice_meeting_date(notice.get("datetime", ""))
    meeting_phase, _ = _meeting_phase(notice if meeting_date else {"datetime": ""}, None, None)
    # 연도 확정: 회의일 연도 우선, 파싱 실패 시 공시 접수 연도 fallback
    # (소집공고→회의는 통상 2~4주 간격 — 같은 해가 대부분, 연말 경계는 회의일 파싱이 잡는다)
    disclosure = notice.get("disclosure_date", "")
    year_resolved = meeting_date.year if meeting_date else (
        int(disclosure[:4]) if len(disclosure) >= 4 and disclosure[:4].isdigit() else None
    )
    if year_resolved is None:
        return None
    return {
        "year": year_resolved,
        "meeting_type": picked_type,
        "meeting_date": meeting_date,
        "notice_rcept_no": notice.get("rcept_no", ""),
        "notice_date": disclosure,
        "meeting_phase": meeting_phase,
    }


async def _select_notice_candidate(
    corp_code: str,
    target_year: int | None,
    requested_meeting_type: str,
    scope: str,
    *,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 12,
    timings_ms: dict[str, int] | None = None,
    fiscal_month: str = "",
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None, str | None, list[str]]:
    search_notices: list[str] = []
    window_start, window_end, _ = _selection_window(
        target_year,
        start_date=start_date,
        end_date=end_date,
        lookback_months=lookback_months,
    )
    if requested_meeting_type == "auto":
        annual_result, extraordinary_result = await asyncio.gather(
            _candidate_notices_in_meeting_window(
                corp_code,
                _MEETING_TYPE_MAP["annual"],
                window_start,
                window_end,
                timings_ms=timings_ms,
            ),
            _candidate_notices_in_meeting_window(
                corp_code,
                _MEETING_TYPE_MAP["extraordinary"],
                window_start,
                window_end,
                timings_ms=timings_ms,
            ),
        )
        annual_notices, annual_search_notices = annual_result
        extraordinary_notices, extraordinary_search_notices = extraordinary_result
        search_notices.extend(annual_search_notices)
        search_notices.extend(extraordinary_search_notices)
        latest_by_type: list[tuple[str, dict[str, Any]]] = []
        annual_latest = _pick_latest_notice(annual_notices)
        extraordinary_latest = _pick_latest_notice(extraordinary_notices)
        if annual_latest:
            latest_by_type.append(("annual", annual_latest))
        if extraordinary_latest:
            latest_by_type.append(("extraordinary", extraordinary_latest))
        if not latest_by_type:
            return None, [], None, f"{window_start.isoformat()}~{window_end.isoformat()} 구간에 정기/임시 주주총회 소집공고를 찾지 못했다.", search_notices

        # results / full scope에서만 result_filing 검색 (사후 결과 데이터가 핵심).
        # notice tool (summary/board/compensation/aoi_change/prov_financials)은 미사용.
        # auto 모드 ranking도 date 기반 phase로 충분 (post_meeting_pre_result rank 1 통합).
        # 정기/임시 분류 자체는 _candidate_notices_in_meeting_window가 doc 파싱으로 결정 — result_filing 무관.
        fetch_result = scope in {"results", "full"}
        stage_started_at = time.perf_counter()
        candidates = await asyncio.gather(*[
            _build_candidate(
                # window_end 는 이제 미래(오늘+90일)라 연말엔 .year 가 **다음 해**다.
                # 회의일을 못 읽었을 때의 결과검색 연도로 그걸 쓰면 엉뚱한 해를 뒤진다.
                corp_code, meeting_type, target_year or date.today().year, notice,
                fetch_result_filing=fetch_result,
            )
            for meeting_type, notice in latest_by_type
        ])
        _mark_timing(timings_ms, "select_notice_candidate.build_candidate", stage_started_at)
        for candidate in candidates:
            search_notices.extend(candidate.get("search_notices", []))
        selected = sorted(candidates, key=lambda row: _auto_rank_key(row, scope), reverse=True)[0]
        alternatives = [_candidate_meta(candidate) for candidate in candidates if candidate is not selected]
        basis = _auto_selection_basis(selected, scope, candidates)
        return selected, alternatives, basis, None, search_notices

    meeting_type_label = _MEETING_TYPE_MAP[requested_meeting_type]
    search_window_start = window_start
    search_window_end = window_end
    fiscal_window = (
        _annual_window_from_fiscal_month(target_year, fiscal_month)
        if requested_meeting_type == "annual" and target_year and not start_date and not end_date
        else None
    )
    if fiscal_window:
        search_window_start, search_window_end = fiscal_window
    top_n = 1 if fiscal_window else 2

    notices, notice_search_notices = await _candidate_notices_in_meeting_window(
        corp_code,
        meeting_type_label,
        search_window_start,
        search_window_end,
        timings_ms=timings_ms,
        top_n=top_n,
    )
    search_notices.extend(notice_search_notices)
    latest_notice = _pick_latest_notice(notices)
    if not latest_notice and fiscal_window:
        stage_started_at = time.perf_counter()
        notices, notice_search_notices = await _candidate_notices_in_meeting_window(
            corp_code,
            meeting_type_label,
            window_start,
            window_end,
            timings_ms=timings_ms,
            top_n=2,
        )
        _mark_timing(timings_ms, "select_notice_candidate.full_year_fallback", stage_started_at)
        search_notices.extend(notice_search_notices)
        latest_notice = _pick_latest_notice(notices)
    if not latest_notice:
        return None, [], None, f"{window_start.isoformat()}~{window_end.isoformat()} 구간에 {meeting_type_label} 주주총회 소집공고를 찾지 못했다.", search_notices
    fetch_result = scope in {"results", "full"}
    stage_started_at = time.perf_counter()
    selected = await _build_candidate(
        corp_code, requested_meeting_type, target_year or date.today().year, latest_notice,
        fetch_result_filing=fetch_result,
    )
    _mark_timing(timings_ms, "select_notice_candidate.build_candidate", stage_started_at)
    search_notices.extend(selected.get("search_notices", []))
    basis = f"사용자가 {meeting_type_label} 주주총회를 명시해 해당 회차를 선택했다."
    return selected, [], basis, None, search_notices


async def _meeting_window_coverage(
    corp_code: str,
    start_date: date,
    end_date: date,
    months: int = 12,
) -> dict[str, Any]:
    annual_result, extraordinary_result = await asyncio.gather(
        _candidate_notices_in_meeting_window(
            corp_code,
            _MEETING_TYPE_MAP["annual"],
            start_date,
            end_date,
        ),
        _candidate_notices_in_meeting_window(
            corp_code,
            _MEETING_TYPE_MAP["extraordinary"],
            start_date,
            end_date,
        ),
    )
    annual_notices, _ = annual_result
    extraordinary_notices, _ = extraordinary_result

    annual_latest = _pick_latest_notice(annual_notices)
    extraordinary_latest = _pick_latest_notice(extraordinary_notices)
    has_annual = annual_latest is not None
    has_extraordinary = extraordinary_latest is not None

    return {
        "window_months": months,
        "window_start": start_date.isoformat(),
        "window_end": end_date.isoformat(),
        "has_annual": has_annual,
        "has_extraordinary": has_extraordinary,
        "presence_flag": _meeting_presence_flag(has_annual, has_extraordinary),
        "annual_count": len(annual_notices),
        "extraordinary_count": len(extraordinary_notices),
        "latest_annual": {
            "meeting_date": _parse_notice_meeting_date(annual_latest.get("datetime", "")).isoformat()
            if annual_latest and _parse_notice_meeting_date(annual_latest.get("datetime", ""))
            else None,
            "notice_rcept_no": annual_latest.get("rcept_no", ""),
            "notice_date": annual_latest.get("disclosure_date", ""),
        } if annual_latest else None,
        "latest_extraordinary": {
            "meeting_date": _parse_notice_meeting_date(extraordinary_latest.get("datetime", "")).isoformat()
            if extraordinary_latest and _parse_notice_meeting_date(extraordinary_latest.get("datetime", ""))
            else None,
            "notice_rcept_no": extraordinary_latest.get("rcept_no", ""),
            "notice_date": extraordinary_latest.get("disclosure_date", ""),
        } if extraordinary_latest else None,
    }


async def _meeting_result_data(
    corp_name: str,
    result_reference: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not result_reference:
        return None, "주주총회결과 공시를 찾지 못했다."

    rcept_no = result_reference.get("rcept_no", "")
    kind_acptno = result_reference.get("kind_acptno")

    client = get_dart_client()
    if not rcept_no or len(rcept_no) != 14 or not rcept_no.isdigit():
        return None, "주주총회결과 공시 접수번호가 DART document.xml 조회 형식에 맞지 않는다."

    # 1차: DART API로 본문 fetch (~0.5-1.5s, KIND scraping 4-5s 대비 빠름).
    # html 구조가 KIND와 거의 동일해 기존 parser 호환.
    source_used = "dart_api"
    html = ""
    try:
        doc = await client.get_document_cached(rcept_no)
        html = doc.get("html") or ""
    except Exception:
        # DART 본문 실패는 KIND fallback으로 복구 — 네트워크/SSL 등 비-DartClientError도 흡수.
        html = ""

    soup = BeautifulSoup(html, "lxml") if html else None
    items = parse_agm_result_table(soup) if soup else []
    result_format = "table" if items else None
    if soup and not items:
        items = parse_agm_result_summary(soup)
        if items:
            result_format = "summary"

    # 2차 fallback: DART에서 본문 빈 응답 또는 파싱 실패 시 KIND scraping.
    if not items:
        if not kind_acptno:
            return None, "DART API 본문에서 안건 결과를 찾지 못했고, KIND 대체 변환 번호가 없다."
        try:
            html = await client.kind_fetch_document(kind_acptno)
        except DartClientError as exc:
            return None, f"DART API + KIND 대체 모두 실패: {exc.status}"
        except Exception as exc:
            # KIND는 외부 사이트 스크래핑이라 네트워크·SSL·타임아웃이 비-DartClientError로
            # 올라온다. 결과는 보조 정보 — 여기서 크래시하면 이미 파싱된 안건·보수까지 날아간다.
            # graceful degrade: 결과만 생략하고 warning 반환 (full scope 전체 보존).
            return None, f"주총 결과 조회 실패(KIND fetch {type(exc).__name__}) — 안건 등 나머지는 정상, 결과는 추후 재시도"
        soup = BeautifulSoup(html, "lxml")
        items = parse_agm_result_table(soup)
        result_format = "table" if items else None
        if not items:
            items = parse_agm_result_summary(soup)
            if items:
                result_format = "summary"
        if not items:
            return None, "DART/KIND 본문에서 안건 결과를 찾지 못했다."
        source_used = "kind_scraping"

    return {
        "corp_name": corp_name,
        "rcept_no": rcept_no,
        "kind_acptno": kind_acptno,
        "rcept_dt": result_reference.get("disclosure_date", ""),
        "report_name": result_reference.get("report_name", ""),
        "result_format": result_format,
        "numerical_vote_table_available": result_format == "table",
        "items": items,
        "source": source_used,
    }, None


async def load_shareholder_meeting_agenda_titles(
    company_query: str,
    *,
    meeting_type: str = "annual",
    year: int | None = None,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 12,
) -> list[str]:
    """Return notice agenda titles without building the full shareholder_meeting envelope."""

    if meeting_type not in _ALLOWED_MEETING_TYPES:
        return []

    resolution = await resolve_company_query(company_query)
    selected = resolution.selected
    if resolution.status != AnalysisStatus.EXACT or not selected:
        return []

    soup_cache: dict[tuple[str, str, Any], Any] = {}
    selected_candidate, _alternatives, _basis, _candidate_error, _candidate_notices = await _select_notice_candidate(
        selected["corp_code"],
        year,
        meeting_type,
        "summary",
        start_date=start_date,
        end_date=end_date,
        lookback_months=lookback_months,
    )
    if not selected_candidate:
        return []

    parsed_notice, _parse_warnings, _notice_parse_source = await _load_notice_bundle_with_fallback(
        selected_candidate["notice"]["rcept_no"],
        scope="summary",
        soup_cache=soup_cache,
    )
    return _agenda_titles(parsed_notice.get("agenda", []))


def _unsupported_scope_payload(
    company_query: str,
    scope: str,
) -> dict[str, Any]:
    envelope = ToolEnvelope(
        tool="shareholder_meeting",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope는 아직 v2에서 열지 않았다."],
        data={"query": company_query, "scope": scope},
        next_actions=["summary, agenda, board, compensation, results 중 하나 사용"],
    )
    return envelope.to_dict()


async def build_shareholder_meeting_payload(
    company_query: str,
    *,
    meeting_type: str = "auto",
    scope: str = "summary",
    year: int | None = None,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 12,
    include_coverage: bool = False,
    rcept_no: str = "",
) -> dict[str, Any]:
    """주총 summary/agenda facade."""

    total_started_at = time.perf_counter()
    timings_ms: dict[str, int] = {}

    def _mark(stage: str, started_at: float) -> None:
        timings_ms[stage] = int((time.perf_counter() - started_at) * 1000)

    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)
    rcept_no = (rcept_no or "").strip()
    if rcept_no and (len(rcept_no) != 14 or not rcept_no.isdigit()):
        timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
        envelope = ToolEnvelope(
            tool="shareholder_meeting",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"공시번호 `{rcept_no}` 형식이 올바르지 않습니다 — 14자리 숫자여야 합니다."],
            data={
                "query": company_query,
                "rcept_no": rcept_no,
                "meeting_type": meeting_type,
                "scope": scope,
                "timings_ms": timings_ms,
            },
        )
        return envelope.to_dict()

    _client = get_dart_client()
    _calls_start = _client.api_call_snapshot()
    if meeting_type not in _ALLOWED_MEETING_TYPES:
        timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
        envelope = ToolEnvelope(
            tool="shareholder_meeting",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"meeting_type=`{meeting_type}`는 지원하지 않는다. auto, annual, extraordinary만 사용 가능하다."],
            data={
                "query": company_query,
                "meeting_type": meeting_type,
                "scope": scope,
                "usage": build_usage(_client.api_call_snapshot() - _calls_start),
                "timings_ms": timings_ms,
            },
        )
        return envelope.to_dict()

    target_year = year
    soup_cache: dict[tuple[str, str, Any], Any] = {}
    requested_window_start, requested_window_end, window_warnings = _selection_window(
        target_year,
        start_date=start_date,
        end_date=end_date,
        lookback_months=lookback_months,
    )
    fiscal_month = ""

    if rcept_no:
        selected = {"corp_name": company_query, "stock_code": "", "corp_code": ""}
        latest_notice = {
            "rcept_no": rcept_no,
            "report_name": "주주총회소집공고",
            "disclosure_date": rcept_no[:8],
            "filer_name": company_query,
        }
        selected_candidate = {
            "meeting_type": meeting_type if meeting_type != "auto" else "annual",
            "meeting_type_label": _MEETING_TYPE_MAP.get(meeting_type, ""),
            "notice": latest_notice,
            "meeting_date": None,
            "result_search_year": target_year or int(rcept_no[:4]),
            "result_filing": None,
            "result_filing_warning": None,
            "result_reference": None,
            "meeting_phase": "undetermined",
            "result_status": "unknown",
            "search_notices": [],
        }
        alternative_meetings = []
        selection_basis = "공시번호를 직접 지정하셔서 해당 소집공고를 그대로 읽었습니다."
        candidate_error = None
        candidate_notices = []
    else:
        stage_started_at = time.perf_counter()
        resolution = await resolve_company_query(company_query)
        _mark("resolve_company", stage_started_at)
        if resolution.status == AnalysisStatus.AMBIGUOUS:
            timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
            envelope = ToolEnvelope(
                tool="shareholder_meeting",
                status=AnalysisStatus.AMBIGUOUS,
                subject=company_query,
                warnings=["회사 식별이 애매해 주총 공시를 자동 선택하지 않았다."],
                data={
                    "query": company_query,
                    "meeting_type": meeting_type,
                    "scope": scope,
                    "candidates": [
                        {
                            "company_id": _company_id(corp),
                            "corp_name": corp.get("corp_name", ""),
                            "ticker": corp.get("stock_code", ""),
                            "corp_code": corp.get("corp_code", ""),
                        }
                        for corp in resolution.candidates[:10]
                    ],
                    "usage": build_usage(_client.api_call_snapshot() - _calls_start),
                    "timings_ms": timings_ms,
                },
                next_actions=["ticker 또는 corp_code로 다시 조회"],
            )
            return envelope.to_dict()

        if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
            timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
            envelope = ToolEnvelope(
                tool="shareholder_meeting",
                status=AnalysisStatus.ERROR,
                subject=company_query,
                warnings=[company_not_found_warning(company_query)],
                data={
                    "query": company_query,
                    "meeting_type": meeting_type,
                    "scope": scope,
                    "usage": build_usage(_client.api_call_snapshot() - _calls_start),
                    "timings_ms": timings_ms,
                },
                next_actions=["company tool로 먼저 회사 식별 확인"],
            )
            return envelope.to_dict()

        selected = resolution.selected
        if meeting_type == "annual" and target_year and not start_date and not end_date:
            stage_started_at = time.perf_counter()
            fiscal_month = await _safe_fiscal_month(selected["corp_code"])
            _mark("fiscal_month_lookup", stage_started_at)
        try:
            stage_started_at = time.perf_counter()
            selected_candidate, alternative_meetings, selection_basis, candidate_error, candidate_notices = await _select_notice_candidate(
                selected["corp_code"],
                target_year,
                meeting_type,
                scope,
                start_date=start_date,
                end_date=end_date,
                lookback_months=lookback_months,
                timings_ms=timings_ms,
                fiscal_month=fiscal_month,
            )
            _mark("select_notice_candidate", stage_started_at)
        except DartClientError as exc:
            timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
            envelope = ToolEnvelope(
                tool="shareholder_meeting",
                status=AnalysisStatus.ERROR,
                subject=selected.get("corp_name", company_query),
                warnings=[f"DART 공시 검색 실패: {exc.status}"],
                data={
                    "query": company_query,
                    "meeting_type": meeting_type,
                    "scope": scope,
                    "year": target_year,
                    "usage": build_usage(_client.api_call_snapshot() - _calls_start),
                    "timings_ms": timings_ms,
                },
            )
            return envelope.to_dict()

    if not selected_candidate:
        timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
        # 회사 식별이 정상이고 DART 검색이 성공했지만 주총 소집공고가 없는 경우는
        # 사건 자체가 없는 정상 케이스 (NO_FILING). 호출이 실제 실패한 경우는 ERROR.
        no_filing_meta = build_filing_meta(filing_count=0, parsing_failures=0)
        no_filing_warning = candidate_error or f"조사 구간 ({requested_window_start.isoformat()}~{requested_window_end.isoformat()}) 내 주주총회 소집공고 없음 (정상)"
        if meeting_type == "annual" and target_year and fiscal_month:
            fiscal_window = _annual_window_from_fiscal_month(target_year, fiscal_month)
            if fiscal_window:
                fw_start, fw_end = fiscal_window
                no_filing_warning = (
                    f"{target_year}년 정기 주주총회 소집공고를 아직 찾지 못했다. "
                    f"회계연도 종료월은 {int(fiscal_month)}월이며, 예상 정기주총 개최 window는 "
                    f"{fw_start.isoformat()}~{fw_end.isoformat()}이다. "
                    f"현재 조회 가능한 DART 공시 기준으로는 해당 정기 소집공고가 아직 없다."
                )
        envelope = ToolEnvelope(
            tool="shareholder_meeting",
            status=AnalysisStatus.NO_FILING,
            subject=selected.get("corp_name", company_query),
            warnings=[*(candidate_notices or []), no_filing_warning],
            data={
                "query": company_query,
                "company_id": _company_id(selected),
                "requested_meeting_type": meeting_type,
                "scope": scope,
                # 소집공고를 못 찾은 갈래라 회차가 없다 — 구간 끝(미래)이 아니라 오늘 기준.
                "year": target_year or date.today().year,
                "fiscal_month": fiscal_month,
                "requested_window": {
                    "start_date": requested_window_start.isoformat(),
                    "end_date": requested_window_end.isoformat(),
                    "lookback_months": lookback_months,
                },
                **no_filing_meta,
                "usage": build_usage(_client.api_call_snapshot() - _calls_start),
                "timings_ms": timings_ms,
            },
            next_actions=["meeting_type 또는 year를 바꿔 재조회"],
        )
        return envelope.to_dict()

    selected_meeting_type = selected_candidate["meeting_type"]
    latest_notice = selected_candidate["notice"]
    selected_meeting_date = selected_candidate.get("meeting_date")
    meeting_phase = selected_candidate["meeting_phase"]
    result_status = selected_candidate["result_status"]
    result_reference = selected_candidate["result_reference"]
    result_filing_warning = selected_candidate["result_filing_warning"]
    coverage_anchor_end = requested_window_end if (start_date or end_date or not target_year) else (selected_meeting_date or date.today())
    coverage_anchor_start = requested_window_start if (start_date or end_date or not target_year) else (coverage_anchor_end - timedelta(days=365))
    coverage_12m = None
    if include_coverage and selected.get("corp_code"):
        stage_started_at = time.perf_counter()
        coverage_12m = await _meeting_window_coverage(
            selected["corp_code"],
            coverage_anchor_start,
            coverage_anchor_end,
            months=lookback_months if (start_date or end_date or not target_year) else 12,
        )
        _mark("coverage_search", stage_started_at)

    stage_started_at = time.perf_counter()
    parsed_notice, parse_warnings, notice_parse_source = await _load_notice_bundle_with_fallback(
        latest_notice["rcept_no"],
        scope=scope,
        soup_cache=soup_cache,
    )
    _mark("load_notice_bundle", stage_started_at)
    text = parsed_notice["text"]
    html = parsed_notice["html"]
    meeting_info = parsed_notice["meeting_info"]
    if rcept_no:
        latest_notice = _normalize_notice_row(
            {
                "rcept_no": rcept_no,
                "report_nm": "주주총회소집공고",
                "rcept_dt": rcept_no[:8],
                "flr_nm": company_query,
            },
            meeting_info,
        )
        selected_candidate["notice"] = latest_notice
        selected_candidate["meeting_date"] = _parse_notice_meeting_date(latest_notice.get("datetime", ""))
        selected_candidate["meeting_phase"], selected_candidate["result_status"] = _meeting_phase(latest_notice, None, None)
        parsed_meeting_type = latest_notice.get("meeting_type")
        if parsed_meeting_type == _MEETING_TYPE_MAP["extraordinary"]:
            selected_candidate["meeting_type"] = "extraordinary"
        elif parsed_meeting_type == _MEETING_TYPE_MAP["annual"]:
            selected_candidate["meeting_type"] = "annual"
        selected_meeting_type = selected_candidate["meeting_type"]
        meeting_phase = selected_candidate["meeting_phase"]
        result_status = selected_candidate["result_status"]
    agenda = parsed_notice["agenda"]
    agenda_valid = parsed_notice["agenda_valid"]
    board = parsed_notice["board"]
    compensation = parsed_notice["compensation"]
    correction = parsed_notice["correction"]

    warnings: list[str] = list(window_warnings) + list(candidate_notices) + parse_warnings
    # 사건은 발견됨 (소집공고 1건 이상). 파싱 신뢰도는 별도 카운트.
    parse_failure_count = 0
    if not agenda_valid:
        parse_failure_count += 1
    if not html:
        parse_failure_count += 1
    # scope별 추가 카운트는 아래 include_* 분기에서 보강.
    status = AnalysisStatus.EXACT
    parsing_failed = False
    if not agenda_valid:
        status = AnalysisStatus.REQUIRES_REVIEW
        parsing_failed = True
        warnings.append("안건 파싱 신뢰도가 낮아 원문 재검토가 필요하다. data.raw_text_excerpt에 DART 원문 텍스트 발췌를 함께 제공하니 직접 해석한다.")
    if not html:
        status = AnalysisStatus.REQUIRES_REVIEW
        warnings.append("HTML 구조를 확보하지 못해 XML 텍스트 기준으로만 파싱했다.")

    agenda_nodes = _agenda_nodes(agenda)
    flat_agendas = _flatten_agendas(agenda)
    agenda_summary = {
        "root_count": len(agenda_nodes),
        "total_count": len(flat_agendas),
        "titles": [item["title"] for item in flat_agendas[:10]],
    }
    board_summary = board.get("summary", {})
    compensation_summary = compensation.get("summary", {})

    # filing_meta — 소집공고 1건 발견. 파싱 실패는 위 parse_failure_count로 누적.
    filing_meta = build_filing_meta(
        filing_count=1,
        parsing_failures=parse_failure_count,
    )

    # 지역변수 selected_meeting_date 가 아니라 딕셔너리를 읽는다 —
    # rcept_no 직접 지정 갈래가 selected_candidate 쪽만 갱신하기 때문이다.
    round_year = _round_year(
        target_year,
        selected_candidate.get("meeting_date"),
        latest_notice.get("rcept_no", ""),
    )

    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
        },
        "requested_meeting_type": meeting_type,
        "meeting_type": selected_meeting_type,
        "selection_basis": selection_basis,
        "fiscal_month": fiscal_month,
        "year": round_year,
        "requested_window": {
            "start_date": requested_window_start.isoformat(),
            "end_date": requested_window_end.isoformat(),
            "lookback_months": lookback_months,
        },
        "notice": latest_notice,
        "notice_parse_source": notice_parse_source,
        "meeting_info": _compact_meeting_info(meeting_info, scope),
        "meeting_phase": meeting_phase,
        "result_status": result_status,
        "agenda_summary": agenda_summary,
        "board_summary": board_summary,
        "compensation_summary": compensation_summary,
        **filing_meta,
        "available_scopes": ["summary", "board", "compensation", "aoi_change", "prov_financials"],
        "selected_meeting": _candidate_meta(selected_candidate),
        "alternative_meetings": alternative_meetings,
    }
    if coverage_12m is not None:
        data["meeting_coverage_12m"] = coverage_12m
    if result_reference:
        data["result_reference"] = result_reference
    if correction:
        data["correction_summary"] = correction
    if parsing_failed:
        # 구조 파싱이 두 단계(API/XML + viewer HTML) 모두 실패한 경우 raw text fallback.
        # `text`는 viewer HTML이 더 풍부했으면 그 text로 교체돼 있음 (_load_notice_bundle_with_fallback 참조).
        # LLM/애널리스트가 원문을 직접 해석. PDF 다운로드 없이 종료.
        raw = (text or "").strip()
        if raw:
            data["raw_text_excerpt"] = raw[:6000]
            data["raw_text_full_length"] = len(raw)
    # 260505 ralph: agenda 트리는 summary에도 항상 포함 (parsing 이미 완료, 비용 0)
    # advise = proxy_advise 전용 scope: full과 동일하되 results만 제외.
    #   회의 후 회사에서 full은 results를 fetch(네트워크)하는데 proxy_advise는 결과공시를 안 써서
    #   wall-clock만 손해 → results만 빼서 회차 선별 1회(콜 -4)는 유지하고 속도를 회복한다.
    _full_like = scope in {"full", "advise"}
    include_agenda = scope in {"agenda", "summary"} or _full_like
    include_board = scope == "board" or _full_like
    include_compensation = scope == "compensation" or _full_like
    include_aoi = scope == "aoi_change" or _full_like
    include_prov_financials = scope == "prov_financials" or _full_like
    include_results = scope in {"results", "full"}  # advise 제외 — results fetch 회피

    if include_agenda:
        data["agendas"] = agenda_nodes
        # '주주총회 목적사항별 기재사항' 구간 원문. 안건과 구간을 우리가 짝지어 주지 않고
        # 라벨만 달아 통째로 넘긴다 — 표 파싱이 실패하면 통째로 사라지던 내용(주주제안
        # 이사 후보 명단·자기주식 처분계획·주식병합 상세)이 여기로 살아 나온다.
        # 재무제표 구간은 머리만 남긴다(수치는 financial_metrics 정형 데이터가 정본).
        if html:
            sections = agenda_detail_sections(html)
            if sections:
                data["agenda_detail_sections"] = sections
    if include_board:
        data["board"] = board
        if not board.get("appointments"):
            warnings.append("선임/해임 인사 안건이 없거나 파싱되지 않았다.")
    if include_compensation:
        data["compensation"] = compensation
        if not compensation.get("items"):
            warnings.append("보수한도 안건이 없거나 파싱되지 않았다.")
        else:
            # 파싱 신뢰도 경고(방향 판정 불가·외화·단위미상)를 payload warnings로 노출
            warnings.extend(compensation.get("summary", {}).get("warnings", []))
    if include_aoi:
        if not html:
            warnings.append("HTML을 확보하지 못해 정관변경 상세를 파싱할 수 없다.")
            data["aoi_change"] = {"amendments": [], "retirement_amendments": [], "summary": {}}
        else:
            charter_subs: list[dict] = []
            for item in agenda:
                if "정관" in (item.get("title") or ""):
                    charter_subs = item.get("children", [])
                    break
            aoi_result = parse_aoi_xml(html, sub_agendas=charter_subs if charter_subs else None)
            # 260505 ralph: aoi_change에 퇴직금 변경 raw도 통합 (data tool 원칙 — raw + 키워드 hit count, 판단 X)
            from open_proxy_mcp.services.shareholder_meeting_parser import parse_retirement_pay_xml
            retire_result = parse_retirement_pay_xml(html)
            retire_amendments = retire_result.get("amendments") or []
            aoi_result["retirement_amendments"] = retire_amendments
            aoi_result.setdefault("summary", {})["retirement_amendments_count"] = len(retire_amendments)
            data["aoi_change"] = aoi_result
            if not aoi_result.get("amendments") and not retire_amendments:
                warnings.append("정관변경 / 퇴직금 변경 안건이 없거나 파싱되지 않았다.")
    if include_prov_financials:
        if not html:
            warnings.append("HTML을 확보하지 못해 잠정 재무제표 표를 파싱할 수 없다.")
            data["prov_financials"] = {"consolidated": {}, "separate": {}, "metrics": {"extraction_status": "no_data"}}
        else:
            from open_proxy_mcp.services.provisional_financial_statement import (
                classify_provisional_fs_absence,
                extract_metrics,
                parse_provisional_financial_statement,
            )
            pfs = parse_provisional_financial_statement(html)
            metrics = extract_metrics(pfs)
            data["prov_financials"] = {**pfs, "metrics": metrics}
            # 못 냈으면 **왜 못 냈는지** 밝힌다 — 원문에 없는 것과 우리가 못 읽은 것은 다르다.
            if not ((pfs.get("consolidated") or {}).get("income_statement")
                    or (pfs.get("separate") or {}).get("income_statement")):
                data["prov_financials"].update(classify_provisional_fs_absence(html))
            if metrics.get("extraction_status") == "no_data":
                warnings.append(data["prov_financials"].get("absence_note")
                                or "잠정 재무제표를 내지 못했습니다 — 원문을 확인하세요.")
    if include_results:
        if meeting_phase == "pre_meeting":
            warnings.append("회의일 전이라 아직 주주총회결과 공시가 나올 시점이 아니다.")
            if scope == "results":
                status = AnalysisStatus.PARTIAL
        else:
            result_data, result_warning = await _meeting_result_data(
                selected.get("corp_name", company_query),
                result_reference,
            )
            if result_warning:
                warnings.append(result_warning)
                if scope == "results":
                    status = AnalysisStatus.REQUIRES_REVIEW
            if result_data:
                data["results"] = result_data
                if scope == "results" and result_data.get("items"):
                    status = AnalysisStatus.EXACT
                if result_data.get("result_format") == "summary":
                    warnings.append("요약형 결과공시라 안건별 가결·부결은 확인되지만 찬성률/참석률 수치는 제공되지 않는다.")
    elif result_filing_warning and meeting_phase != "pre_meeting":
        warnings.append(result_filing_warning)

    notice_source_type = SourceType.DART_HTML if notice_parse_source == "dart_html" else SourceType.DART_XML
    notice_rcept_dt = format_iso_date(latest_notice.get("disclosure_date", ""))
    notice_report_nm = latest_notice.get("report_name", "")

    evidence_refs = [
        EvidenceRef(
            evidence_id=f"ev_notice_{latest_notice['rcept_no']}",
            source_type=notice_source_type,
            rcept_no=latest_notice["rcept_no"],
            rcept_dt=notice_rcept_dt,
            report_nm=notice_report_nm,
            section="주주총회 소집공고",
            note=f"회의일 {meeting_info.get('datetime') or '미확정'}",
        )
    ]
    if include_board:
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_board_{latest_notice['rcept_no']}",
                source_type=notice_source_type,
                rcept_no=latest_notice["rcept_no"],
                rcept_dt=notice_rcept_dt,
                report_nm=notice_report_nm,
                section="후보자/이사 선임",
                note=f"후보자 {board_summary.get('total_candidates', 0)}명",
            )
        )
    if include_compensation:
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_comp_{latest_notice['rcept_no']}",
                source_type=notice_source_type,
                rcept_no=latest_notice["rcept_no"],
                rcept_dt=notice_rcept_dt,
                report_nm=notice_report_nm,
                section="보수한도 승인",
                note=f"보수 안건 {len(compensation.get('items', []))}건",
            )
        )
    if include_aoi and data.get("aoi_change"):
        aoi_meta = data["aoi_change"]
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_aoi_{latest_notice['rcept_no']}",
                source_type=notice_source_type,
                rcept_no=latest_notice["rcept_no"],
                rcept_dt=notice_rcept_dt,
                report_nm=notice_report_nm,
                section="정관변경 상세",
                note=f"정관변경 안건 {len(aoi_meta.get('amendments', []))}건",
            )
        )
    if include_results and data.get("results"):
        result_meta = data["results"]
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_result_{result_meta['rcept_no']}",
                source_type=SourceType.KIND_HTML,
                rcept_no=result_meta["rcept_no"],
                rcept_dt=format_iso_date(result_meta.get("rcept_dt", "")),
                report_nm=result_meta.get("report_name", ""),
                section="주주총회결과",
                note=f"투표 결과 {len(result_meta.get('items', []))}건",
            )
        )

    timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
    data["usage"] = build_usage(_client.api_call_snapshot() - _calls_start)
    data["timings_ms"] = timings_ms

    envelope = ToolEnvelope(
        tool="shareholder_meeting",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=[
            "agenda, board, compensation, results scope로 세부 탭 확인" if scope == "summary" else "evidence tool로 원문 근거 재확인",
            "결과가 아직 없으면 meeting_phase와 result_status를 먼저 확인",
        ],
    )
    return envelope.to_dict()
