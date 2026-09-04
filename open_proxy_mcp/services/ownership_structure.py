"""ownership_structure facade 서비스."""

from __future__ import annotations
from open_proxy_mcp.clock import today_kst

import asyncio
from datetime import date
import re
import time
from typing import Any

from bs4 import BeautifulSoup

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
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
from open_proxy_mcp.services.dart_safety import _NOT_A_FAILURE, classify_degrade
from open_proxy_mcp.services.date_utils import format_iso_date, format_yyyymmdd, parse_date_param, resolve_date_window
from open_proxy_mcp.services.holder_table import holder_table_total, parse_holder_table
from open_proxy_mcp.services.ownership_parser import parse_holding_purpose, parse_holding_purpose_from_document

_SUPPORTED_SCOPES = {
    "summary",
    "major_holders",
    "blocks",  # 5% 대량보유 — 최신 + history (이전 timeline 통합)
    "control_map",
    "changes",
}
# 폐기 scope: treasury (treasury_share tool 사용), timeline (blocks 안에 통합)

# 정기보고서 reprt_code: 사업보고서가 가장 정식이지만, 시기에 따라 미공시일 수 있어
# (사업 → 3분기 → 반기 → 1분기) 순으로 fallback. 모두 빈 응답이면 직전 사업연도까지 시도.
_REPRT_CODE_FALLBACK = ["11011", "11014", "11012", "11013"]

_SUBTOTAL_NAMES = {"계", "합계", "소계", "총계", "총합계"}


def _dart_read_note(exc: DartClientError, *, empty_text: str, fail_text: str) -> tuple[bool, str]:
    """DART 응답 코드를 「자료가 없다」와 「조회가 실패했다」로 가른다.

    013(조회된 데이타가 없습니다)·014(원본 파일 없음)·404(회사·문서 못 찾음)는 **실패가
    아니라 답**이다. 이것을 실패로 찍으면 읽는 사람은 도구가 고장난 줄 알고 재시도한다.
    (U 지적 C-8 — 에스제이그룹 changes 가 「변동신고서 DART 검색 실패: 013」으로 나왔고,
    U 는 변동신고서 구간을 못 본 채로 끝냈다. 실제로는 그 구간에 변동신고서가 없었다.)
    키 오류·점검·과호출 등 사람이 조치해야 하는 것만 실패로 남긴다.
    코드 분류표는 dart_safety 한 곳에서만 관리한다(도구마다 다시 적지 않는다).

    Returns:
        (is_failure, message) — is_failure=False 면 정상적인 「없음」 안내다.
    """
    kind, guide = classify_degrade(exc)
    if kind in _NOT_A_FAILURE:
        return False, empty_text
    return True, f"{fail_text} — {guide}"


def _to_float(value: Any) -> float:
    """문자열/숫자 → 실수 (괄호 음수 처리 포함, 한국 회계 관행 대응)."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    is_negative = text.startswith("(") and text.endswith(")")
    if is_negative:
        text = text[1:-1]
    try:
        result = float(text.replace(",", ""))
        return -result if is_negative else result
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    """문자열 → 정수 (괄호 음수 처리 포함).

    delta 필드 등 음수 발생 가능 영역에서 일관성 보장.
    """
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return 0
    is_negative = text.startswith("(") and text.endswith(")")
    if is_negative:
        text = text[1:-1]
    try:
        digits = re.sub(r"[^\d-]", "", text) or "0"
        result = int(digits)
        return -result if is_negative else result
    except ValueError:
        return 0


def _normalize_stock_label(value: str) -> str:
    """공백·개행 제거. stock_knd / nm 변형 비교용."""
    return re.sub(r"\s+", "", (value or "").strip())


def _is_voting_common_stock(stock_kind: str) -> bool:
    """`최대주주` 합산 대상이 되는 보통주(=의결권 있는 주식) 여부 판정.

    DART hyslrSttus의 `stock_knd`는 회사·시기에 따라 표기가 매우 다양하다:
      - "보통주", "보통주식", " 보통주" (공백 변형)
      - "의결권 있는 주식", "의결권있는 주식", "의결권이 있는 주식"
      - "의결권 있는 주식\\n(보통주)", "의결권\\n있는 주식" (개행 포함)
    공통점: "보통" 혹은 "있는"을 포함. 반대로 우선/없는/기타/-/종류/합계는 제외.

    빈 stock_knd는 보수적으로 보통주로 간주(과거 일부 회사가 빈 값으로 보고하는 케이스).
    """
    norm = _normalize_stock_label(stock_kind)
    if not norm:
        return True
    if "없는" in norm:
        return False
    return ("보통" in norm) or ("있는" in norm)


def _is_subtotal_row(name: str) -> bool:
    """`계`, `합계`, `소계` 등 합계 행 판별 (공백·개행 무시)."""
    return _normalize_stock_label(name) in _SUBTOTAL_NAMES


def _clean_name(name: str) -> str:
    """이름에서 줄바꿈·중복 공백을 정리한다."""
    return re.sub(r"\s+", " ", (name or "").strip())


def _major_holders_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """hyslrSttus list → 본인+특수관계인 의결권 보통주 행만 추출.

    legacy 필터 ``("보통" not in stock_kind and stock_kind)``는 SK하이닉스/현대차/LG전자
    등 ``의결권 있는 주식`` 표기를 사용하는 회사를 모두 누락시켰다.
    실표기 변형을 분석해 normalize 후 positive matching으로 교체.
    """
    rows: list[dict[str, Any]] = []
    for item in data.get("list", []):
        stock_kind = item.get("stock_knd", "")
        name = _clean_name(item.get("nm", ""))
        if not name or _is_subtotal_row(name):
            continue
        if not _is_voting_common_stock(stock_kind):
            continue
        rows.append({
            "name": name,
            "relation": _clean_name(item.get("relate", "")),
            "shares": _to_int(item.get("trmend_posesn_stock_co", "0")),
            "ownership_pct": _to_float(item.get("trmend_posesn_stock_qota_rt", "0")),
            "settlement_date": item.get("stlm_dt", ""),
        })
    rows.sort(key=lambda row: row["ownership_pct"], reverse=True)
    return rows


async def _fetch_major_with_fallback(
    client,
    corp_code: str,
    bsns_year: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """hyslrSttus를 다중 (year, reprt_code) 조합으로 시도.

    1) 요청된 bsns_year의 (사업 → 3분기 → 반기 → 1분기) 순회
    2) 모두 빈 응답이면 직전 사업연도 사업보고서 시도
    3) 모두 실패면 빈 list 반환 (호출자가 5% 보고서 fallback 결정)

    Returns:
        (major_rows, source_meta, warnings)
        - source_meta: {"endpoint", "bsns_year", "reprt_code", "fallback_used", "no_data"}
    """
    warnings: list[str] = []
    attempts: list[tuple[str, str]] = [(bsns_year, code) for code in _REPRT_CODE_FALLBACK]
    try:
        prev_year = str(int(bsns_year) - 1)
        attempts.append((prev_year, "11011"))
    except ValueError:
        pass

    last_status: str | None = None
    for try_year, try_code in attempts:
        try:
            data = await client.get_major_shareholders(corp_code, try_year, try_code)
        except DartClientError as exc:
            last_status = exc.status
            failed, note = _dart_read_note(
                exc,
                empty_text="",
                fail_text=f"최대주주 명부 조회 실패 ({try_year}년 {try_code} 보고서)",
            )
            if failed:
                warnings.append(note)
            continue
        rows = _major_holders_rows(data)
        if rows:
            source_meta = {
                "endpoint": "hyslrSttus",
                "bsns_year": try_year,
                "reprt_code": try_code,
                "raw_count": len(data.get("list", [])),
                "parsed_count": len(rows),
                "fallback_used": (try_year, try_code) != (bsns_year, "11011"),
            }
            if source_meta["fallback_used"]:
                warnings.append(
                    f"최대주주: bsns_year={try_year} reprt_code={try_code} 대체 사용"
                )
            return rows, source_meta, warnings

    source_meta = {
        "endpoint": "hyslrSttus",
        "bsns_year": bsns_year,
        "reprt_code": "11011",
        "raw_count": 0,
        "parsed_count": 0,
        "fallback_used": False,
        "no_data": True,
        "last_status": last_status,
    }
    return [], source_meta, warnings


async def _fetch_largest_shareholder_from_blocks(
    client,
    corp_code: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """5% 대량보유(majorstock)에서 최대주주 후보를 추정.

    hyslrSttus가 비어 있는 ‘ownerless’(N연기금 6%대만 있는 회사 등) 또는
    미공개 케이스의 보조 source. 5% 보고는 ‘외부 주주 시점’이라
    본인+특수관계인 합산 개념과 다르므로 추정치임을 명시한다.

    KT&G 같은 회사도 hyslrSttus에 본인 7%대 데이터가 있어 1차 fallback이
    먼저 처리하는 것이 정상. 이 함수는 그것마저 실패한 잔여 케이스용.
    """
    warnings: list[str] = []
    try:
        data = await client.get_block_holders(corp_code)
    except DartClientError as exc:
        failed, note = _dart_read_note(
            exc, empty_text="", fail_text="5% 대량보유 보고 조회 실패(최대주주 추정 경로)",
        )
        if failed:
            warnings.append(note)
        return [], warnings

    latest_by_reporter: dict[str, dict[str, Any]] = {}
    for item in data.get("list", []):
        reporter = (item.get("repror", "") or "").strip()
        if not reporter:
            continue
        rcept_dt = item.get("rcept_dt", "")
        if reporter not in latest_by_reporter or rcept_dt > latest_by_reporter[reporter].get("rcept_dt", ""):
            latest_by_reporter[reporter] = item

    rows: list[dict[str, Any]] = []
    for reporter, item in latest_by_reporter.items():
        rows.append({
            "name": _clean_name(reporter),
            "relation": "5% 보유자(추정)",
            "shares": _to_int(item.get("stkqy", 0)),
            "ownership_pct": _to_float(item.get("stkrt", 0)),
            "settlement_date": item.get("rcept_dt", ""),
        })
    rows.sort(key=lambda row: row["ownership_pct"], reverse=True)
    return rows, warnings


def _top_holder_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    return rows[0]


def _related_total(rows: list[dict[str, Any]]) -> float:
    return round(sum(row["ownership_pct"] for row in rows), 2)


def _normalize_entity_name(name: str) -> str:
    normalized = (name or "").strip()
    normalized = normalized.replace("\n", " ")
    normalized = re.sub(r"\(주\)|㈜|주식회사|유한회사|유한책임회사|\(유\)|\(유한\)", "", normalized)
    normalized = re.sub(r"[^\w가-힣]", "", normalized)
    return normalized.lower()


def _is_active_purpose(purpose: str) -> bool:
    return purpose not in ("단순투자", "단순투자/일반투자", "일반투자", "불명")


def _is_material_block(row: dict[str, Any]) -> bool:
    return _to_float(row.get("ownership_pct", 0)) >= 5.0


def _enrich_co_holders(blocks: list[dict[str, Any]], major_rows: list[dict[str, Any]]) -> None:
    """각 5% 블록에 공동보유자 분해를 부착(in-place). 모든 scope(summary/blocks/control_map) 공통.

    합계표(holder_table)가 파싱된 블록만 — 보고자 본인(reporter_self_pct) vs 공동보유자
    (co_holders: 각자 이름·지분·명부최대주주 여부)로 나눈다. 헤드라인 보유비율(ownership_pct)은
    본인+특관 합산이라, 이 분해가 "32.78% = 누가 얼마씩"에 답한다. 불변식(합≈헤드라인) 검증값도
    함께 부착 — 불일치면 co_holders_verified=False(확정 표기 금지).
    """
    major_name_map = {
        _normalize_entity_name(r["name"]): r for r in major_rows if _normalize_entity_name(r["name"])
    }
    for row in blocks:
        ht = row.get("holder_table")
        self_pct = None
        co_holders = None
        co_total = None
        co_verified = None
        coheld_names: list[str] = []
        if ht and ht.get("format") == "일반" and ht.get("self"):
            self_pct = ht["self"].get("pct")
            co_holders = []
            for rel in ht.get("related", []):
                rel_key = _normalize_entity_name(rel.get("name", ""))
                is_reg = bool(rel_key and rel_key in major_name_map)
                co_holders.append({
                    "name": rel.get("name", ""),
                    "ownership_pct": rel.get("pct"),
                    "is_registry_holder": is_reg,  # 명부상 최대주주 테이블에 있는가
                })
                if is_reg:
                    coheld_names.append(major_name_map[rel_key]["name"])
            coheld_names = sorted(set(coheld_names))
            co_total = holder_table_total(ht)
            headline = _to_float(row.get("ownership_pct"))
            if co_total is not None and headline:
                co_verified = abs(co_total - headline) <= max(headline * 0.05, 0.5)
        row["self_pct"] = self_pct
        row["reporter_self_pct"] = self_pct            # 보고자 본인 지분
        # 「본인 0.0% + 특관」의 뜻을 산출물에 실어 보낸다(U 지적 D-10 — 영풍 41.13% 인데
        # 본인 0.0%). 왜 0 인지(처분인지 상호주 의결권 제한인지)는 대량보유보고서만으로는
        # 알 수 없으므로 **단정하지 않고** 원문 확인 대상으로 남긴다.
        row["reporter_self_note"] = (
            "보고자 본인은 직접 보유가 없고, 위 지분율은 전부 특별관계자 몫이다. "
            "본인 보유가 0 인 사유(처분·의결권 제한 등)는 이 보고서로는 알 수 없어 원문 확인이 필요하다."
            if self_pct is not None and self_pct == 0 and co_holders
            else ""
        )
        row["co_holders"] = co_holders                 # 공동보유자 명세 [{name, ownership_pct, is_registry_holder}]
        row["co_holders_total_pct"] = co_total         # 본인+특관 합 (헤드라인 검산)
        row["co_holders_verified"] = co_verified       # 합≈헤드라인이면 True, 불일치 False(미검증)
        row["coheld_with_registry"] = bool(coheld_names)
        row["coheld_names"] = coheld_names


def _block_member_map(row: dict[str, Any]) -> tuple[dict[str, tuple[str, float]], bool]:
    """5% 블록 하나가 품고 있는 사람·법인 목록 → {정규화이름: (표기이름, 지분율)}.

    합계표를 읽은 블록은 보고자 본인 + 특별관계자 전원을, 못 읽은 블록은 보고자 이름
    하나만 헤드라인 지분율로 담는다(두 번째 반환값이 「분해했는가」).
    """
    members: dict[str, tuple[str, float]] = {}
    reporter = _clean_name(row.get("reporter", ""))
    co_holders = row.get("co_holders")
    if not co_holders:
        key = _normalize_entity_name(reporter)
        if key:
            members[key] = (reporter, _to_float(row.get("ownership_pct")))
        return members, False
    self_key = _normalize_entity_name(reporter)
    if self_key:
        members[self_key] = (reporter, _to_float(row.get("reporter_self_pct")))
    for holder in co_holders:
        name = _clean_name(holder.get("name", ""))
        key = _normalize_entity_name(name)
        if not key:
            continue
        pct = _to_float(holder.get("ownership_pct"))
        # 같은 이름이 두 번 나오면 큰 쪽을 남긴다(보고서 간 반올림 차이 흡수).
        if key not in members or pct > members[key][1]:
            members[key] = (name, pct)
    return members, True


def _build_block_camps(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """5% 보고 지분율을 단순히 더하면 100% 를 넘는 문제를 푼다(U 지적 D-11).

    같은 사람·법인을 특별관계자로 함께 안고 있는 보고자끼리는 **같은 지분을 두 번 신고**한다.
    고려아연이 그렇다 — 영풍 41.13% 와 한국기업투자홀딩스 36.92% 가 (유)와이피씨 25.21% 를
    서로 품고 있어, 5% 보고를 그냥 더하면 111.84% 가 된다.

    여기서는 겹치는 보고자들을 한 편으로 묶고, 편 안에서 **같은 이름을 한 번만 세어**
    편별 순 지분을 낸다. 편 전원의 합계표가 읽혔고 각 보고서의 합이 헤드라인과 맞을 때만
    순 지분을 확정값으로 내고, 하나라도 못 읽었으면 숫자를 내지 않고 겹침 사실만 보여준다.
    """
    if not blocks:
        return {}

    member_maps: list[dict[str, tuple[str, float]]] = []
    decomposed: list[bool] = []
    for row in blocks:
        members, ok = _block_member_map(row)
        member_maps.append(members)
        decomposed.append(ok)

    # 같은 이름을 공유하는 보고자끼리 잇는다(union-find).
    parent = list(range(len(blocks)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    shared_pairs: list[dict[str, Any]] = []
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            common = set(member_maps[i]) & set(member_maps[j])
            if not common:
                continue
            shared = sorted(
                (
                    {
                        "name": member_maps[i][k][0],
                        "ownership_pct": max(member_maps[i][k][1], member_maps[j][k][1]),
                    }
                    for k in common
                ),
                key=lambda h: -h["ownership_pct"],
            )
            shared_pairs.append({
                "reporters": [blocks[i].get("reporter", ""), blocks[j].get("reporter", "")],
                "shared_holders": shared,
            })
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

    groups: dict[int, list[int]] = {}
    for i in range(len(blocks)):
        groups.setdefault(find(i), []).append(i)

    camps: list[dict[str, Any]] = []
    for members_idx in groups.values():
        members_idx.sort(key=lambda i: -_to_float(blocks[i].get("ownership_pct")))
        reporters = [blocks[i].get("reporter", "") for i in members_idx]
        headline_sum = round(sum(_to_float(blocks[i].get("ownership_pct")) for i in members_idx), 2)
        all_decomposed = all(decomposed[i] for i in members_idx)
        all_verified = all(blocks[i].get("co_holders_verified") for i in members_idx) if all_decomposed else False
        net_pct = None
        basis = ""
        if len(members_idx) == 1:
            # 겹치는 보고자가 없으면 걷어낼 중복도 없다 — 공시된 지분율을 그대로 쓴다.
            # (합계표를 다시 더하면 반올림 때문에 공시값과 0.01%p 어긋난 값을 만들게 된다.)
            net_pct = headline_sum
            basis = "보고 지분율 그대로 (겹치는 보고자 없음)"
        elif all_decomposed and all_verified:
            union: dict[str, float] = {}
            for i in members_idx:
                for key, (_name, pct) in member_maps[i].items():
                    if key not in union or pct > union[key]:
                        union[key] = pct
            net_pct = round(sum(union.values()), 2)
            basis = f"{len(members_idx)}건의 보고서 합계표를 합쳐 같은 이름을 한 번만 계산"
        else:
            basis = "합계표를 못 읽은 보고자가 있어 겹친 몫을 걷어내지 못함"
        camps.append({
            "reporters": reporters,
            "label": " · ".join(reporters),
            "headline_sum_pct": headline_sum,
            "net_pct": net_pct,
            "net_basis": basis,
            "block_count": len(members_idx),
        })
    camps.sort(key=lambda c: -(c["net_pct"] if c["net_pct"] is not None else c["headline_sum_pct"]))

    headline_total = round(sum(_to_float(row.get("ownership_pct")) for row in blocks), 2)
    net_total = (
        round(sum(c["net_pct"] for c in camps), 2)
        if all(c["net_pct"] is not None for c in camps) else None
    )
    return {
        "headline_total_pct": headline_total,
        "exceeds_100": headline_total > 100.0,
        "net_total_pct": net_total,
        "camps": camps,
        "shared_holders_between_reporters": shared_pairs,
    }


def _build_control_map(
    major_rows: list[dict[str, Any]],
    latest_blocks: list[dict[str, Any]],
    treasury_snapshot: dict[str, Any],
) -> dict[str, Any]:
    major_name_map = {
        _normalize_entity_name(row["name"]): row
        for row in major_rows
        if _normalize_entity_name(row["name"])
    }

    overlap_blocks: list[dict[str, Any]] = []
    non_overlap_blocks: list[dict[str, Any]] = []
    active_non_overlap_blocks: list[dict[str, Any]] = []
    active_overlap_blocks: list[dict[str, Any]] = []

    for row in latest_blocks:
        reporter_key = _normalize_entity_name(row.get("reporter", ""))
        matched_major = major_name_map.get(reporter_key)
        # 본인/특관 분해 (합계표 파싱 성공한 활성 블록만 holder_table 보유).
        # self_pct = 보고자 본인 지분(헤드라인 ownership_pct는 본인+특관 합산).
        # coheld_with_registry = 특별관계자에 명부상 최대주주가 포함 → 공동보유. 이 경우
        # 보고자가 외부 세력처럼 보여도(reporter 이름이 명부에 없어 registry_overlap=False)
        # 실제로는 최대주주와 한 편이므로 "외부 능동 블록"으로 단정하면 안 된다.
        # co_holders/self_pct/coheld_* 는 _enrich_co_holders가 이미 row에 부착(모든 scope 공통).
        enriched = {
            **row,
            "registry_overlap": bool(matched_major),
            "matched_major_holder": matched_major.get("name") if matched_major else None,
            "active_purpose": _is_active_purpose(row.get("purpose", "")),
        }
        if enriched["registry_overlap"]:
            overlap_blocks.append(enriched)
            if enriched["active_purpose"] and _is_material_block(enriched):
                active_overlap_blocks.append(enriched)
        else:
            non_overlap_blocks.append(enriched)
            if enriched["active_purpose"] and _is_material_block(enriched):
                active_non_overlap_blocks.append(enriched)

    related_total_pct = _related_total(major_rows)
    treasury_pct = treasury_snapshot["treasury_pct"]

    # 공동보유 블록 = registry_overlap은 아니나(보고자 이름이 명부에 없음) 특별관계자에
    # 명부상 최대주주가 들어있는 능동 블록. 외부 세력으로 보이지만 실제로는 최대주주와 한 편.
    coheld_blocks = [b for b in active_non_overlap_blocks if b.get("coheld_with_registry")]

    flags = {
        "registry_majority": related_total_pct >= 50,
        "registry_over_30pct": related_total_pct >= 30,
        "treasury_over_5pct": treasury_pct >= 5,
        "active_non_overlap_block_exists": bool(active_non_overlap_blocks),
        "active_overlap_block_exists": bool(active_overlap_blocks),
        "coheld_with_registry_block_exists": bool(coheld_blocks),
    }

    observations: list[str] = []
    if flags["registry_majority"]:
        observations.append("명부상 특수관계인 합계가 50% 이상이다.")
    elif flags["registry_over_30pct"]:
        observations.append("명부상 특수관계인 합계가 30% 이상이다.")
    if flags["treasury_over_5pct"]:
        observations.append("자사주 비중이 5% 이상이다.")
    if flags["active_non_overlap_block_exists"]:
        observations.append("명부상 최대주주 테이블과 겹치지 않는 능동적 5% 블록이 있다.")
    elif flags["active_overlap_block_exists"]:
        observations.append("능동적 5% 블록이 있으나 명부상 최대주주 테이블과 이름이 겹친다.")
    for block in coheld_blocks:
        reporter = block.get("reporter", "")
        names = ", ".join(block.get("coheld_names", []))
        self_pct = block.get("self_pct")
        headline = block.get("ownership_pct")
        self_part = f"본인 {self_pct}%" if self_pct is not None else "본인 지분 미확정"
        observations.append(
            f"{reporter}의 5% 보고 {headline}%는 보고자 합산값이며 특별관계자에 명부상 최대주주"
            f"({names})가 포함된다({self_part}) — 공동보유로 보이므로 외부 세력으로 단정 불가."
        )

    return {
        "core_holder_block": {
            "top_holder": _top_holder_summary(major_rows),
            "related_total_pct": related_total_pct,
            "holder_count": len(major_rows),
        },
        "treasury_block": {
            "shares": treasury_snapshot["treasury_shares"],
            "pct": treasury_pct,
        },
        "overlap_blocks": overlap_blocks,
        "active_overlap_blocks": active_overlap_blocks,
        "non_overlap_blocks": non_overlap_blocks,
        "active_non_overlap_blocks": active_non_overlap_blocks,
        "flags": flags,
        "observations": observations,
        "notes": [
            "5% 블록은 최대주주 명부와 단순 합산하지 않는다.",
            "registry_overlap은 같은 이름이 최대주주 명부에 있는지를 뜻하며, 현재 이해관계가 완전히 같다는 의미는 아니다.",
        ],
    }


async def _latest_block_rows(corp_code: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        data = await client.get_block_holders(corp_code)
    except DartClientError as exc:
        _failed, note = _dart_read_note(
            exc,
            empty_text="5% 대량보유 공시 없음 (해당 회사에 5% 이상 보고자가 없거나 보고 이력이 없음)",
            fail_text="5% 대량보유 공시 조회 실패",
        )
        return [], [], note

    latest_by_reporter: dict[str, dict[str, Any]] = {}
    for item in data.get("list", []):
        reporter = item.get("repror", "").strip()
        if not reporter:
            continue
        if reporter not in latest_by_reporter or item.get("rcept_dt", "") > latest_by_reporter[reporter].get("rcept_dt", ""):
            latest_by_reporter[reporter] = item

    latest_rows: list[dict[str, Any]] = []
    timeline_rows: list[dict[str, Any]] = []
    for item in data.get("list", []):
        timeline_rows.append({
            "reporter": item.get("repror", "").strip(),
            "report_date": item.get("rcept_dt", ""),
            "rcept_no": item.get("rcept_no", ""),
            "ownership_pct": _to_float(item.get("stkrt", 0)),
            "purpose": parse_holding_purpose(item.get("report_tp", ""), item.get("report_resn", "")),
            "report_name": item.get("report_tp", ""),
        })
    timeline_rows.sort(key=lambda row: (row["report_date"], row["rcept_no"]), reverse=True)

    for reporter, item in latest_by_reporter.items():
        purpose = parse_holding_purpose(item.get("report_tp", ""), item.get("report_resn", ""))
        rcept_no = item.get("rcept_no", "")
        ownership_pct = _to_float(item.get("stkrt", 0))
        html: str | None = None
        if purpose in ("불명", "단순투자/일반투자") and rcept_no:
            try:
                doc = await client.get_document_cached(rcept_no)
                html = doc.get("html", "") or ""
                parsed = parse_holding_purpose_from_document(html)
                if parsed != "불명":
                    purpose = parsed
            except Exception:
                pass
        # 본인/특별관계자 분해: 능동적(경영참여) + 유의미(≥5%) 블록만 본문 합계표 파싱.
        # 헤드라인 stkrt는 보고자 본인+특관 합산이라, 본인 지분 분리·공동보유 탐지에 필요.
        # 호출은 활성·유의미 블록(보통 1~3건)으로 한정해 비용을 제한한다. graceful fallback:
        # 합계표 없음(약식)·파싱 실패면 holder_table=None으로 두고 기존 라벨 유지.
        holder_table: dict[str, Any] | None = None
        if rcept_no and _is_active_purpose(purpose) and ownership_pct >= 5.0:
            if html is None:
                try:
                    html = (await client.get_document_cached(rcept_no)).get("html", "") or ""
                except Exception:
                    html = ""
            if html:
                parsed_table = parse_holder_table(html)
                if parsed_table and parsed_table.get("format") == "일반":
                    holder_table = parsed_table
        latest_rows.append({
            "reporter": reporter,
            "report_date": item.get("rcept_dt", ""),
            "rcept_no": rcept_no,
            "ownership_pct": ownership_pct,
            "purpose": purpose,
            "report_type": item.get("report_tp", ""),
            "report_reason": item.get("report_resn", ""),
            "holder_table": holder_table,
        })
    latest_rows.sort(key=lambda row: row["ownership_pct"], reverse=True)
    return latest_rows, timeline_rows, None


def _treasury_snapshot(stock_total: dict[str, Any], treasury_data: dict[str, Any]) -> dict[str, Any]:
    issued = 0
    treasury = 0
    distributable = 0
    # 보통주 행 우선. 단 셀트리온처럼 우선주 없는 회사는 '보통주' 행 없이 '합계' 행만 있어
    # (issued=0 누락) → 합계 행을 fallback으로 잡되, 우선주 발행분은 빼 보통주 기준을 맞춘다.
    common_row = None
    subtotal_row = None
    preferred_issued = 0
    for item in stock_total.get("list", []):
        se = item.get("se", "")
        if "보통" in se:
            common_row = common_row or item
        elif "우선" in se:
            preferred_issued += _to_int(item.get("istc_totqy", "0"))
        elif _is_subtotal_row(se):
            subtotal_row = subtotal_row or item
    if common_row is not None:
        issued = _to_int(common_row.get("istc_totqy", "0"))
        treasury = _to_int(common_row.get("tesstk_co", "0"))
        distributable = _to_int(common_row.get("distb_stock_co", "0"))
    elif subtotal_row is not None:
        issued = _to_int(subtotal_row.get("istc_totqy", "0")) - preferred_issued
        treasury = _to_int(subtotal_row.get("tesstk_co", "0"))
        distributable = _to_int(subtotal_row.get("distb_stock_co", "0"))

    rows = []
    for item in treasury_data.get("list", []):
        rows.append({
            "category": item.get("se", ""),
            "begin_shares": _to_int(item.get("bsis_qy", "0")),
            "acquired_shares": _to_int(item.get("acqs_qy", "0")),
            "disposed_shares": _to_int(item.get("dsps_qy", "0")),
            "retired_shares": _to_int(item.get("inciner_qy", "0")),
            "end_shares": _to_int(item.get("trmend_qy", "0")),
        })

    return {
        "issued_shares": issued,
        "treasury_shares": treasury,
        "tradable_shares": distributable,
        "treasury_pct": round(treasury / issued * 100, 2) if issued else 0.0,
        "rows": rows,
    }


def _sanitize_share_units(major_rows: list[dict[str, Any]], treasury_snapshot: dict[str, Any]) -> list[str]:
    """DART 원본의 천주/백만주 단위 오염 자가 교정 (450사 audit 실측: LS·LS에코에너지).

    일부 회사가 hyslrSttus 주식수 또는 stockTotqySttus 발행총수를 ×1,000 / ×1,000,000으로
    기재한다(예: LS 구자은 '1,170,304,000'주 + 지분율 3.69% — 발행총수 31.7M의 36.9배).
    지분율(비율) 필드는 오염되지 않으므로 anchor로 쓴다:
        r = (주식수/발행총수×100) / 공시지분율
    r ≈ 10^k (k=3,6)  → 명부 주식수 ×10^k 오염 → 주식수 /10^k
    r ≈ 10^-k         → 발행총수(및 자사주·유통) ×10^k 오염 → 분모 /10^k
    r이 10의 거듭제곱 근처가 아니면(예: 솔루스 보통주/총주식 분모 차이 r≈1.3) 건드리지 않는다.
    """
    import math

    issued = treasury_snapshot.get("issued_shares", 0) or 0
    anchored = [
        r for r in major_rows
        if (r.get("ownership_pct") or 0) >= 0.01 and (r.get("shares") or 0) > 0
    ]
    if not issued or not anchored:
        return []
    ratios = sorted(
        (row["shares"] / issued * 100) / row["ownership_pct"] for row in anchored
    )
    r = ratios[len(ratios) // 2]  # median — 행별 혼합 오염 방어
    if r <= 0:
        return []
    lr = math.log10(r)
    k = round(lr)
    if abs(lr - k) > 0.15 or abs(k) not in (3, 6):
        return []
    unit = 10 ** abs(k)
    if k > 0:
        for row in major_rows:
            if row.get("shares"):
                row["shares"] = row["shares"] // unit
        return [f"명부 주식수가 발행총수 대비 {unit:,}배로 기재돼 있어 {unit:,}분의 1로 교정 (DART 원본 단위 오염)"]
    for key in ("issued_shares", "treasury_shares", "tradable_shares"):
        if treasury_snapshot.get(key):
            treasury_snapshot[key] = treasury_snapshot[key] // unit
    issued2 = treasury_snapshot.get("issued_shares", 0) or 0
    treasury_snapshot["treasury_pct"] = (
        round(treasury_snapshot.get("treasury_shares", 0) / issued2 * 100, 2) if issued2 else 0.0
    )
    return [f"발행총수가 명부 지분율 대비 {unit:,}배로 기재돼 있어 {unit:,}분의 1로 교정 (DART 원본 단위 오염)"]


def _parse_change_filing(html: str, rcept_no: str, rcept_dt: str) -> dict[str, Any]:
    """KIND HTML에서 최대주주등소유주식변동신고서 파싱."""
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")

    if len(tables) < 4:
        return {"rcept_no": rcept_no, "rcept_dt": rcept_dt, "parse_error": f"테이블 {len(tables)}개 (최소 4개 필요)"}

    def cell_texts(table) -> list[list[str]]:
        return [
            [td.get_text(" ", strip=True) for td in row.find_all(["td", "th"])]
            for row in table.find_all("tr")
            if row.find_all(["td", "th"])
        ]

    # 섹션 3 (index 2): 보고의 개요 - 직전/금번 주식수+비율
    overview: dict[str, Any] = {}
    try:
        for cells in cell_texts(tables[2]):
            joined = "".join(cells)
            nums = [_to_float(re.sub(r"[^\d.]", "", c)) for c in cells if re.sub(r"[^\d.]", "", c)]
            if "직전" in joined and nums:
                overview["before_shares"] = int(nums[0]) if nums else 0
                overview["before_pct"] = nums[1] if len(nums) > 1 else 0.0
            elif "금번" in joined and nums:
                overview["after_shares"] = int(nums[0]) if nums else 0
                overview["after_pct"] = nums[1] if len(nums) > 1 else 0.0
    except Exception:
        pass

    # 섹션 4~N-1 (index 3 to -2): 개인별 세부변동사항
    individual_changes: list[dict[str, Any]] = []
    for t in tables[3:-1]:
        # 주주명은 테이블 직전 bold span에서 추출
        holder_name = ""
        prev_span = t.find_previous("span")
        if prev_span:
            holder_name = prev_span.get_text(strip=True)

        change_rows: list[dict[str, Any]] = []
        header_found = False
        for cells in cell_texts(t):
            joined = "".join(cells)
            if not header_found:
                if "변경일" in joined or "변경원인" in joined:
                    header_found = True
                continue
            if len(cells) >= 5:
                change_rows.append({
                    "date": cells[0],
                    "reason": cells[1],
                    "stock_type": cells[2] if len(cells) > 2 else "",
                    "before": _to_int(cells[3]) if len(cells) > 3 else 0,
                    "delta": _to_int(cells[4]) if len(cells) > 4 else 0,
                    "after": _to_int(cells[5]) if len(cells) > 5 else 0,
                })
        individual_changes.append({"holder_name": holder_name, "changes": change_rows})

    # 마지막 테이블: 최대주주등 주식소유현황 (총괄)
    total_holders: list[dict[str, Any]] = []
    try:
        header_found = False
        for cells in cell_texts(tables[-1]):
            joined = "".join(cells)
            if not header_found:
                if "성명" in joined or "관계" in joined:
                    header_found = True
                continue
            if not cells[0] or cells[0] in ("계", "합계", "소계"):
                continue
            if len(cells) >= 3:
                # 컬럼: 성명 / 관계 / 보통주수 / 보통주비율 / ... / 합계수 / 합계비율
                total_holders.append({
                    "name": cells[0],
                    "relation": cells[1] if len(cells) > 1 else "",
                    "shares": _to_int(cells[2]) if len(cells) > 2 else 0,
                    "pct": _to_float(cells[3]) if len(cells) > 3 else 0.0,
                })
    except Exception:
        pass

    return {
        "rcept_no": rcept_no,
        "rcept_dt": rcept_dt,
        "overview": overview,
        "individual_changes": individual_changes,
        "total_holders": total_holders,
    }


async def _fetch_change_filings(
    corp_code: str,
    window_start: date,
    window_end: date,
    client,
) -> tuple[list[dict[str, Any]], list[str]]:
    """DART 검색 → KIND 크롤링 → 변동신고서 리스트 반환."""
    warnings: list[str] = []
    try:
        result = await client.search_filings(
            bgn_de=format_yyyymmdd(window_start),
            end_de=format_yyyymmdd(window_end),
            pblntf_detail_ty="I004",  # 최대주주등소유주식변동신고서 ∈ I004. I 전체를 받으면
            corp_code=corp_code,       # 배당·실적에 밀려 변동신고서가 거의 누락(삼성 8 vs 90).
            page_count=20,             # [:5]만 본문 파싱 — 12개월 window 최다 16건(삼성)이라 20이면 충분
        )
    except DartClientError as exc:
        _failed, note = _dart_read_note(
            exc,
            empty_text="조사 구간 내 최대주주등소유주식변동신고서 없음 (DART 에 접수된 건이 없음)",
            fail_text="최대주주등소유주식변동신고서 조회 실패",
        )
        return [], [note]

    filings_raw = [
        item for item in result.get("list", [])
        if "최대주주등소유주식변동신고서" in item.get("report_nm", "")
    ]
    if not filings_raw:
        return [], []

    filings: list[dict[str, Any]] = []
    for item in filings_raw[:5]:
        rcept_no = item.get("rcept_no", "")
        rcept_dt = item.get("rcept_dt", "")
        if not (rcept_no and len(rcept_no) == 14 and rcept_no[8:10] == "80"):
            warnings.append(f"변동신고서 공시번호 형식이 올바르지 않습니다: {rcept_no}")
            continue
        acptno = rcept_no[:8] + "00" + rcept_no[10:]

        # 1차: DART API (~0.1-0.5s, KIND scraping 3-4s 대비 매우 빠름).
        # html 구조가 KIND와 동일해 _parse_change_filing 호환.
        html = ""
        source_used = "dart_api"
        try:
            doc = await client.get_document_cached(rcept_no)
            html = doc.get("html") or ""
        except DartClientError:
            html = ""

        # 2차 fallback: DART에서 빈 응답이면 KIND scraping.
        if not html:
            try:
                html = await client.kind_fetch_document(acptno)
                source_used = "kind_scraping"
            except DartClientError as exc:
                _failed, note = _dart_read_note(
                    exc,
                    empty_text=f"변동신고서 원문이 DART·KIND 양쪽에 없음 ({rcept_no})",
                    fail_text=f"변동신고서 원문 조회 실패 ({rcept_no})",
                )
                warnings.append(note)
                continue

        parsed = _parse_change_filing(html, rcept_no, rcept_dt)
        parsed["report_nm"] = item.get("report_nm", "최대주주등소유주식변동신고서")
        parsed["acptno"] = acptno
        parsed["source"] = source_used
        filings.append(parsed)

    return filings, warnings


def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    envelope = ToolEnvelope(
        tool="ownership_structure",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope는 아직 v2에서 열지 않았다."],
        data={"query": company_query, "scope": scope},
    )
    return envelope.to_dict()


async def build_ownership_structure_payload(
    company_query: str,
    *,
    scope: str = "summary",
    year: int | None = None,
    as_of_date: str = "",
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    total_started_at = time.perf_counter()
    timings_ms: dict[str, int] = {}

    def _mark(stage: str, started_at: float) -> None:
        timings_ms[stage] = int((time.perf_counter() - started_at) * 1000)

    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    client = get_dart_client()
    _calls_start = client.api_call_snapshot()
    stage_started_at = time.perf_counter()
    resolution = await resolve_company_query(company_query)
    _mark("resolve_company", stage_started_at)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
        return ToolEnvelope(
            tool="ownership_structure",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[company_not_found_warning(company_query)],
            data={
                "query": company_query,
                "scope": scope,
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
                "timings_ms": timings_ms,
            },
            next_actions=["company tool로 회사 식별 확인"],
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
        return ToolEnvelope(
            tool="ownership_structure",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 지분 구조를 자동 선택하지 않았다."],
            data={
                "query": company_query,
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
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
                "timings_ms": timings_ms,
            },
        ).to_dict()

    selected = resolution.selected
    as_of = parse_date_param(as_of_date)
    as_of_year = (as_of.year - 1) if as_of else None
    bsns_year = str(year or as_of_year or (today_kst().year - 1))
    window_start, window_end, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=as_of or today_kst(),
        lookback_months=12,
    )
    warnings: list[str] = list(window_warnings)

    # 3개 정기보고서 API는 같은 corp_code/bsns_year로 병렬 호출 가능 (independent).
    # major는 실패 시 즉시 ERROR return하던 기존 동작 유지(asyncio.gather + return_exceptions).
    # stock_total(발행총수)·treasury(자사주)는 summary 100% 분해와 control_map에서만 쓰인다.
    # major_holders/blocks/changes scope는 불필요하므로 호출을 건너뛴다(scope당 2콜 절감).
    # major는 top_holder/related_total 등 공유 로직에 얽혀 모든 scope에서 유지.
    need_treasury_apis = scope in {"summary", "control_map"}
    major_task = client.get_major_shareholders(selected["corp_code"], bsns_year)
    stage_started_at = time.perf_counter()
    if need_treasury_apis:
        stock_total_task = client.get_stock_total(selected["corp_code"], bsns_year)
        treasury_task = client.get_treasury_stock(selected["corp_code"], bsns_year)
        major_res, stock_total_res, treasury_res = await asyncio.gather(
            major_task, stock_total_task, treasury_task, return_exceptions=True,
        )
    else:
        (major_res,) = await asyncio.gather(major_task, return_exceptions=True)
        stock_total_res = treasury_res = {"list": []}
    _mark("annual_report_apis", stage_started_at)

    # 1차: 사업보고서 hyslrSttus.
    # 빈 응답(013)은 ERROR가 아니라 fallback 경로로 보낸다 — KOSPI 대형주 다수가 정기보고서
    # 표기 형식 변형(의결권 있는 주식 등)으로 legacy 파서에서 0건이었기 때문.
    major_source: dict[str, Any] = {
        "endpoint": "hyslrSttus",
        "bsns_year": bsns_year,
        "reprt_code": "11011",
        "fallback_used": False,
    }
    if isinstance(major_res, DartClientError):
        if major_res.status != "013":
            return ToolEnvelope(
                tool="ownership_structure",
                status=AnalysisStatus.ERROR,
                subject=selected.get("corp_name", company_query),
                warnings=[
                    _dart_read_note(
                        major_res,
                        empty_text="최대주주 명부 자료 없음",
                        fail_text="최대주주 명부 조회 실패",
                    )[1]
                ],
                data={
                    "query": company_query,
                    "scope": scope,
                    "year": bsns_year,
                    "usage": build_usage(client.api_call_snapshot() - _calls_start),
                },
            ).to_dict()
        major = {"list": []}
        major_source["last_status"] = "013"
    elif isinstance(major_res, BaseException):
        raise major_res
    else:
        major = major_res
        major_source["raw_count"] = len(major.get("list", []))

    if isinstance(stock_total_res, DartClientError):
        stock_total = {"list": []}
        _failed, _note = _dart_read_note(
            stock_total_res,
            empty_text="발행주식총수 자료 없음 (해당 사업연도 정기보고서에 주식총수 항목이 없음)",
            fail_text="발행주식총수 조회 실패",
        )
        warnings.append(_note)
    elif isinstance(stock_total_res, BaseException):
        raise stock_total_res
    else:
        stock_total = stock_total_res

    if isinstance(treasury_res, DartClientError):
        treasury_data = {"list": []}
        _failed, _note = _dart_read_note(
            treasury_res,
            empty_text="자기주식 취득·처분 자료 없음 (해당 사업연도 정기보고서에 해당 항목이 없음)",
            fail_text="자기주식 조회 실패",
        )
        warnings.append(_note)
    elif isinstance(treasury_res, BaseException):
        raise treasury_res
    else:
        treasury_data = treasury_res

    major_rows = _major_holders_rows(major)
    major_source["parsed_count"] = len(major_rows)

    # 2차 fallback: 1차에서 0건 → 다른 reprt_code (반기/분기) + 직전연도 사업 시도.
    if not major_rows:
        stage_started_at = time.perf_counter()
        fb_rows, fb_source, fb_warnings = await _fetch_major_with_fallback(
            client, selected["corp_code"], bsns_year
        )
        _mark("major_holder_fallback", stage_started_at)
        warnings.extend(fb_warnings)
        if fb_rows:
            major_rows = fb_rows
            major_source = fb_source

    # 5% 대량보유(majorstock)는 major_holders scope(명부 전용)에선 안 쓰여 스킵(1콜 절감).
    stage_started_at = time.perf_counter()
    if scope != "major_holders":
        latest_blocks, timeline_rows, block_warning = await _latest_block_rows(selected["corp_code"])
    else:
        latest_blocks, timeline_rows, block_warning = [], [], None
    _mark("block_holders", stage_started_at)
    if block_warning:
        warnings.append(block_warning)

    # 3차 fallback: 정기보고서 모두 빈 응답 → 5% 대량보유에서 추정.
    # ‘본인+특수관계인 합산’ 개념과는 다르므로 추정치임을 명시한다.
    if not major_rows:
        stage_started_at = time.perf_counter()
        block_fb_rows, block_fb_warnings = await _fetch_largest_shareholder_from_blocks(
            client, selected["corp_code"]
        )
        _mark("largest_holder_block_fallback", stage_started_at)
        warnings.extend(block_fb_warnings)
        if block_fb_rows:
            major_rows = block_fb_rows
            major_source = {
                "endpoint": "majorstock",
                "fallback_used": True,
                "estimated_from_5pct": True,
                "parsed_count": len(block_fb_rows),
            }
            warnings.append(
                "최대주주를 5% 대량보유 보고에서 추정 — 본인+특수관계인 합산이 아니므로 정확도 제한"
            )
    start_ymd = format_yyyymmdd(window_start)
    end_ymd = format_yyyymmdd(window_end)
    # latest_blocks는 보고자별 최신 1건 = **현재 지분 스냅샷**이라 기간 하한(360일)을 씌우면 안 된다.
    # 지분 변동이 없어 최근 공시가 없는 안정적 지배주주(국민연금 7.75%·CJ 13%·Silchester 7% 등)가
    # 조용히 누락되기 때문(실측: 하한창이면 CJ는 5% 블록이 통째로 빔). 하한은 제거하되 상한(end_ymd)은
    # as-of 과거조회 look-ahead 방지로 유지하고, 5% 밑으로 내려간 이탈/청산 보고(영풍 장형진 0% 등)는
    # 현재 5% 블록이 아니므로 보유율로 제외한다. 이탈 '이벤트' 자체는 아래 timeline_rows에 남는다.
    latest_blocks = [
        row for row in latest_blocks
        if row.get("report_date", "").replace("-", "") <= end_ymd
        and (row.get("ownership_pct") or 0) >= 5.0
    ]
    # timeline_rows는 기간 내 대량보유 '이벤트' 이력이므로 기간창(하한+상한)을 그대로 유지한다.
    timeline_rows = [
        row for row in timeline_rows
        if start_ymd <= row.get("report_date", "").replace("-", "") <= end_ymd
    ]

    treasury_snapshot = _treasury_snapshot(stock_total, treasury_data)
    unit_warnings = _sanitize_share_units(major_rows, treasury_snapshot)
    warnings.extend(unit_warnings)
    # 공시 지분율(보통+우선 총주식 기준일 수 있음) vs 보통주 기준 괴리 안내 (솔루스 41% vs 53% 류)
    _issued_for_check = treasury_snapshot.get("issued_shares", 0) or 0
    if _issued_for_check and major_rows:
        _share_pct = sum(r.get("shares", 0) or 0 for r in major_rows) / _issued_for_check * 100
        _disclosed_pct = _related_total(major_rows)
        if abs(_share_pct - _disclosed_pct) > 2:
            warnings.append(
                f"명부 공시 지분율 합({_disclosed_pct:.2f}%)과 보통주 발행총수 기준({_share_pct:.2f}%)이 다름 — "
                "공시 지분율은 우선주 포함 총주식 기준일 수 있음. 100% 분해표는 보통주 기준."
            )
    top_holder = _top_holder_summary(major_rows)
    # 공동보유자 분해를 모든 scope의 5% 블록에 부착 (summary/blocks에서도 노출되도록).
    _enrich_co_holders(latest_blocks, major_rows)
    # 보고자끼리 같은 특별관계자를 품고 있으면 5% 보고 지분율은 단순 합산이 안 된다.
    block_camps = _build_block_camps(latest_blocks)
    if block_camps.get("exceeds_100"):
        _net = block_camps.get("net_total_pct")
        _tail = (
            f" 겹치는 몫을 한 번만 세면 {_net:.2f}%다."
            if _net is not None
            else " 겹치는 보고자 목록은 아래에 있으나, 합계표를 못 읽은 보고자가 있어 순 지분은 계산하지 않았다."
        )
        warnings.append(
            f"5% 대량보유 보고 지분율을 그냥 더하면 {block_camps['headline_total_pct']:.2f}%로 100%를 넘는다 — "
            "보고자들이 같은 특별관계자를 서로 품고 있어 같은 주식이 여러 번 신고되기 때문이다. 단순 합산은 하면 안 된다."
            + _tail
        )
    active_signals = [
        row for row in latest_blocks
        if row["purpose"] not in ("단순투자", "단순투자/일반투자", "불명")
    ]

    # 사건 발견 vs 진짜 partial 분리.
    # ownership은 두 종류 — 정기보고서(major_holders)는 대부분 항상 있고
    # 5% 보고서·변동신고서는 회사·구간에 따라 없을 수 있다.
    # filing_count = major_holders rows + latest_blocks (5% 보고).
    filing_count = len(major_rows) + len(latest_blocks)
    parsing_failures = 0
    # major_rows가 비어 있으면 정기보고서 파싱 실패 = 진짜 partial.
    if not major_rows:
        parsing_failures += 1
    filing_meta = build_filing_meta(
        filing_count=filing_count,
        parsing_failures=parsing_failures,
    )

    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
        },
        "year": bsns_year,
        "as_of_date": as_of.isoformat() if as_of else "",
        "window": {
            "start_date": start_ymd,
            "end_date": end_ymd,
        },
        "summary": {
            "top_holder": top_holder,
            "related_total_pct": _related_total(major_rows),
            "treasury_shares": treasury_snapshot["treasury_shares"],
            "treasury_pct": treasury_snapshot["treasury_pct"],
            "active_signal_count": len(active_signals),
        },
        "largest_shareholder_source": major_source,
        **filing_meta,
        "available_scopes": sorted(_SUPPORTED_SCOPES),
    }

    if scope in {"summary", "major_holders", "control_map"}:
        data["major_holders"] = major_rows
    if scope in {"summary", "blocks", "control_map"}:
        data["blocks"] = latest_blocks
        if block_camps:
            data["block_camps"] = block_camps
    if scope == "blocks":
        # blocks scope에는 latest + 이력 timeline 통합 (timeline scope 폐지 흡수)
        data["timeline"] = timeline_rows[:50]
    if scope == "summary":
        # summary는 treasury 가벼운 snapshot만 (자사주 detail은 treasury_share tool)
        data["treasury"] = treasury_snapshot
    if scope == "control_map":
        data["control_map"] = _build_control_map(major_rows, latest_blocks, treasury_snapshot)
    if scope == "changes":
        stage_started_at = time.perf_counter()
        change_filings, change_warnings = await _fetch_change_filings(
            selected["corp_code"], window_start, window_end, client
        )
        _mark("change_filings", stage_started_at)
        data["change_filings"] = change_filings
        # 5% 대량보유 변동도 합친다. 분쟁사(고려아연 영풍-MBK 등)는 최대주주변동신고서(I004)
        # 대신 주식등의대량보유상황보고서(D001)로 지분이 움직여 change_filings만 보면 빈다.
        # timeline_rows는 753에서 majorstock으로 이미 받아 784에서 window 필터됨(추가 콜 0).
        data["block_changes"] = timeline_rows
        warnings.extend(change_warnings)

    evidence_refs: list[EvidenceRef] = [
        EvidenceRef(
            evidence_id=f"ev_ownership_api_{selected['corp_code']}_{bsns_year}",
            source_type=SourceType.DART_API,
            section="hyslrSttus/stockTotqySttus",
            note=f"{selected.get('corp_name', '')} {bsns_year}년 정기보고서 기준 최대주주/주식총수",
        )
    ]
    if latest_blocks:
        first = latest_blocks[0]
        if first.get("rcept_no"):
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=f"ev_block_{first['rcept_no']}",
                    source_type=SourceType.DART_XML,
                    rcept_no=first["rcept_no"],
                    rcept_dt=format_iso_date(first.get("report_date", "")),
                    report_nm=first.get("report_name", ""),
                    section="대량보유 상황보고",
                    note=f"{first['reporter']} / {first['ownership_pct']}% / {first['purpose']}",
                )
            )
    for filing in data.get("change_filings", []):
        if filing.get("rcept_no") and not filing.get("parse_error"):
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=f"ev_change_{filing['rcept_no']}",
                    source_type=SourceType.KIND_HTML,
                    rcept_no=filing["rcept_no"],
                    rcept_dt=format_iso_date(filing.get("rcept_dt", "")),
                    report_nm="최대주주등소유주식변동신고서",
                    section="최대주주등 소유주식 변동",
                    note=f"acptno={filing.get('acptno', '')}",
                )
            )

    status = status_from_filing_meta(filing_meta)
    if filing_meta["no_filing"]:
        warnings.append(f"조사 구간 ({start_ymd}~{end_ymd}) 내 정기보고서/5% 대량보유 공시 없음 (정상)")
    elif filing_meta["parsing_failures"] > 0 and not major_rows:
        warnings.append("최대주주 구조를 충분히 읽지 못해 부분 상태로 표시한다.")

    # 시그널 부여(audit w0qo5hfse): control_map의 5% 블록 데이터 유무를 명시(다운스트림 사각지대 차단).
    # 정상 빈(보유자 없음)이 많아 PARTIAL 강등은 보수적으로 안 하고, blocks_present 플래그 + 안내만.
    if scope == "control_map" and isinstance(data.get("control_map"), dict):
        data["control_map"]["blocks_present"] = bool(latest_blocks)
        if not latest_blocks and status == AnalysisStatus.EXACT:
            warnings.append("5% 대량보유 블록 데이터 없음 — 정상 빈(보유자 없음)일 수 있으나, 분쟁사면 조회 결측 가능성 확인 권장")

    data["usage"] = build_usage(client.api_call_snapshot() - _calls_start)
    timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
    data["timings_ms"] = timings_ms

    return ToolEnvelope(
        tool="ownership_structure",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=[
            "blocks scope로 5% 대량보유 최신 보고 확인" if scope == "summary" else "proxy_contest와 함께 보면 분쟁 맥락이 더 잘 보인다.",
        ],
    ).to_dict()
