"""저장된 자사주 API 원행 재사용. 기존 요약 숫자와 수집 범위는 대신 신뢰하지 않는다."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re

from open_proxy_mcp.db import pg_rows

ENDPOINTS = {
    'acquisition': ('tsstkAqDecsn', '자기주식취득결정'),
    'disposal': ('tsstkDpDecsn', '자기주식처분결정'),
    'trust_contract': ('tsstkAqTrctrCnsDecsn', '자기주식취득신탁계약체결결정'),
    'trust_termination': ('tsstkAqTrctrCcDecsn', '자기주식취득신탁계약해지결정'),
}
# 정규화에서 사용하는 공개 원행만 보존한다. 요청 URL·키·응답 외 메타는 넣지 않는다.
FIELDS = set('rcept_no corp_code corp_name corp_cls aqpln_stk_ostk aqpln_stk_estk aqpln_prc_ostk aqpln_prc_estk aq_pp aq_mth aqexpd_bgd aqexpd_edd hdexpd_bgd hdexpd_edd aq_dd cs_iv_bk aq_wtn_div_ostk aq_wtn_div_ostk_rt aq_wtn_div_estk eaq_ostk eaq_ostk_rt od_a_at_t od_a_at_b adt_a_atn dppln_stk_ostk dppln_stk_estk dppln_prc_ostk dppln_prc_estk dp_m_mkt dp_m_ovtm dp_m_otc dp_m_etc dpstk_prc_ostk dpstk_prc_estk dp_pp dpprpd_bgd dpprpd_edd dp_dd ctr_prc ctr_prc_am ctr_pr ctr_pric ctr_pp ctr_cns_prd_bgd ctr_prd_bgd ctr_cns_prd_edd ctr_prd_edd ctr_cns_dd ctr_cc_dd ctr_cns_inst ctr_inst iv_jdgh_idr ctr_cc_rs'.split())
REQUIRED = {
    'acquisition': {'aqpln_stk_ostk', 'aqpln_stk_estk', 'aqpln_prc_ostk', 'aqpln_prc_estk'},
    'disposal': {'dppln_stk_ostk', 'dppln_stk_estk', 'dppln_prc_ostk', 'dppln_prc_estk'},
    'trust_contract': {'ctr_prc'}, 'trust_termination': {'ctr_prc'},
}
MAX_AGE = timedelta(days=7)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def family(title):
    title = re.sub(r'\s+', '', str(title))
    title = re.sub(r'^\[(?:기재정정|첨부정정|첨부추가)\]', '', title)
    for key, (_, label) in ENDPOINTS.items():
        if title in (label, '주요사항보고서(' + label + ')'):
            return key
    return None


def make_source(key, row, *, captured_at=None):
    """원 API 응답에 회사·접수가 있는 경우에만 재사용 가능한 원행을 만든다."""
    if not re.fullmatch(r'\d{8}', str(row.get('corp_code', ''))) or not re.fullmatch(r'\d{14}', str(row.get('rcept_no', ''))):
        return None
    if not REQUIRED[key] <= row.keys():
        return None
    raw = {k: copy.deepcopy(v) for k, v in row.items() if k in FIELDS}
    if any(v is not None and type(v) not in (str, int, float) for v in raw.values()):
        return None
    return {'version': 1, 'endpoint': ENDPOINTS[key][0],
            'captured_at': captured_at or datetime.now(timezone.utc).isoformat(),
            'raw': raw, 'raw_sha256': digest(raw)}


def read_rows(corp_code, start, end):
    """조회만 한다. DB 미설정·장애·상한 초과를 빈 원장이라고 확정하지 않는다."""
    if not os.getenv('DATABASE_URL'):
        return [], 'unconfigured'
    ready = pg_rows("SELECT to_regclass('public.dart_events') IS NOT NULL")
    if not ready or ready[0][0] is not True:
        return [], 'unavailable'
    result = pg_rows("""SELECT jsonb_build_object(
      'rcept_no', rcept_no, 'rcept_dt', rcept_dt, 'corp_code', corp_code,
      'report_nm', report_nm, 'detail_status', detail_status, 'detail', detail)
      FROM dart_events WHERE corp_code=%s AND rcept_dt BETWEEN %s AND %s
      AND kind='treasury' ORDER BY rcept_dt,rcept_no LIMIT 2001""", (corp_code, start, end))
    if result is None:
        return [], 'unavailable'
    if len(result) > 2000:
        return [], 'limit_exceeded'
    return [r[0] for r in result], 'available'


def reusable(rows, corp_code, inventory, *, now=None):
    """새 DART 목록의 정확한 회사·접수·제목과 일치하는 원행만 반환한다."""
    now = now or datetime.now(timezone.utc)
    by_receipt = {}
    for item in inventory:
        receipt = item.get('rcept_no')
        if receipt in by_receipt:
            by_receipt[receipt] = None
        else:
            by_receipt[receipt] = item
    def require(condition):
        if not condition:
            raise ValueError('source mismatch')

    accepted, rejected, seen = {}, {}, set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        receipt = row.get('rcept_no')
        reason = 'unverified_legacy_detail'
        item = by_receipt.get(receipt)
        detail = row.get('detail')
        src = detail.get('source_api') if isinstance(detail, dict) else None
        try:
            key = family(item['report_nm']) if item else None
            require(key and row['corp_code'] == item['corp_code'] == corp_code)
            require(str(row['rcept_dt']).replace('-', '') == item['rcept_dt'] == receipt[:8])
            require(row['report_nm'] == item['report_nm'])
            require(row['detail_status'] == 'parsed' and isinstance(src, dict))
            reason = 'invalid_source_api'
            require(src['version'] == 1 and src['endpoint'] == ENDPOINTS[key][0])
            raw = src['raw']
            require(isinstance(raw, dict) and set(raw) <= FIELDS and REQUIRED[key] <= raw.keys())
            require(all(v is None or type(v) in (str, int, float) for v in raw.values()))
            require(raw['rcept_no'] == receipt and raw['corp_code'] == corp_code)
            require(digest(raw) == src['raw_sha256'])
            captured = datetime.fromisoformat(src['captured_at'])
            require(captured.tzinfo is not None)
            reason = 'stale_source_api'
            require(timedelta(0) <= now - captured <= MAX_AGE)
            require(receipt not in seen)
            accepted[receipt] = (key, copy.deepcopy(src))
        except (AssertionError, KeyError, ValueError, TypeError):
            accepted.pop(receipt, None)
            rejected[receipt] = reason
        seen.add(receipt)
    return accepted, rejected
