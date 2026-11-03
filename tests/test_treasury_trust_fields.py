"""자사주 신탁 체결·해지 — 정형 필드 이름, 원장 보존, 체결↔해지결과 집행 연결, 성과 평가의 매입 합계.

체결 원행은 배포 서버가 2026-11-03 돌려준 아세아제지 정형 응답(공개 DART 공시)의 저장 원행을 옮겼다.
해지 원행은 실제 응답 이름(`ctr_prc_bfcc`·`cc_pp`·`cc_int`…)으로 만든 합성 입력이다 — 값은 같은 회사
해지결과보고서(20260814001396)의 계약금액을 썼다.
"""
from datetime import datetime, timezone

from open_proxy_mcp.services import treasury_share as ts, treasury_store as store
from open_proxy_mcp.services.proxy_advise import _treasury_buyback_yearly

CORP = "00138729"

CONTRACT_RAW = {  # 20260515000711 저장 원행 + 실제 응답의 기간·기관 필드
    "rcept_no": "20260515000711", "corp_cls": "Y", "corp_code": CORP, "corp_name": "아세아제지",
    "od_a_at_t": "2", "od_a_at_b": "-", "adt_a_atn": "참석",
    "cs_iv_bk": "NH투자증권\n(NH INVESTMENT & SECURITIES CO., LTD.)",
    "aq_wtn_div_ostk": "1,598,975", "aq_wtn_div_ostk_rt": "4.05", "aq_wtn_div_estk": "-",
    "eaq_ostk": "-", "eaq_ostk_rt": "-", "ctr_prc": "5,000,000,000", "ctr_pp": "주주가치 제고",
    "ctr_pd_bgd": "2026년 05월 15일", "ctr_pd_edd": "2026년 08월 14일",
    "ctr_cns_int": "NH투자증권", "ctr_cns_prd": "2026년 05월 15일", "bddd": "2026년 05월 14일",
}

TERMINATION_RAW = {
    "rcept_no": "20260814001300", "corp_cls": "Y", "corp_code": CORP, "corp_name": "아세아제지",
    "ctr_prc_bfcc": "5,000,000,000", "ctr_prc_atcc": "-",
    "ctr_pd_bfcc_bgd": "2026년 05월 15일", "ctr_pd_bfcc_edd": "2026년 08월 14일",
    "cc_pp": "신탁계약 기간 만료", "cc_int": "NH투자증권", "cc_prd": "2026년 08월 14일",
    "tp_rm_atcc": "현금 및 실물 반환", "bddd": "2026년 08월 13일",
    "od_a_at_t": "2", "od_a_at_b": "-", "adt_a_atn": "참석",
}


def test_termination_reads_real_field_names():
    row = ts._normalize_trust(TERMINATION_RAW, "trust_termination", "해지")
    # 예전에는 `ctr_prc` 만 찾아 0, 기관·사유·기간이 모두 빈칸이었다.
    assert row["amount_krw"] == 5_000_000_000
    assert row["amount_before_termination_krw"] == 5_000_000_000
    assert row["amount_after_termination_krw"] == 0
    assert row["termination_reason"] == "신탁계약 기간 만료"
    assert row["purpose"] == ""  # 사유와 두 번 보이지 않게
    assert row["trustee_name"] == "NH투자증권"
    assert (row["start_date"], row["end_date"]) == ("2026년 05월 15일", "2026년 08월 14일")
    assert row["termination_date"] == "2026년 08월 14일"
    assert row["board_date"] == "2026년 08월 13일"
    assert row["return_method"] == "현금 및 실물 반환"


def test_contract_reads_real_field_names_and_broker():
    row = ts._normalize_trust(CONTRACT_RAW, "trust_contract", "체결")
    assert row["amount_krw"] == 5_000_000_000
    assert (row["start_date"], row["end_date"]) == ("2026년 05월 15일", "2026년 08월 14일")
    assert row["trustee_name"] == "NH투자증권"
    assert row["broker_name"].startswith("NH투자증권")
    assert row["board_date"] == "2026년 05월 14일"
    assert row["contract_date"] == "2026년 05월 15일"


def test_legacy_names_still_read():
    legacy = {"rcept_no": "20250101000001", "ctr_prc": "100", "ctr_cns_prd_bgd": "2025-01-01",
              "ctr_cns_inst": "가증권", "ctr_cc_dd": "2025-01-02"}
    row = ts._normalize_trust(legacy, "trust_termination", "해지")
    assert (row["amount_krw"], row["start_date"], row["trustee_name"], row["board_date"]) == (
        100, "2025-01-01", "가증권", "2025-01-02")


def test_store_keeps_termination_rows_and_real_fields():
    src = store.make_source("trust_termination", TERMINATION_RAW)
    assert src is not None  # 예전에는 `ctr_prc` 가 없어 버려졌다
    for key in ("ctr_prc_bfcc", "ctr_pd_bfcc_bgd", "cc_pp", "cc_int", "cc_prd"):
        assert key in src["raw"]
    contract = store.make_source("trust_contract", CONTRACT_RAW)
    for key in ("ctr_pd_bgd", "ctr_pd_edd", "ctr_cns_int", "bddd"):
        assert key in contract["raw"]
    # 저장된 원행 재사용 검증도 통과해야 한다.
    item = dict(corp_code=CORP, rcept_no=TERMINATION_RAW["rcept_no"], rcept_dt="20260814",
                report_nm="주요사항보고서(자기주식취득신탁계약해지결정)")
    stored = dict(item, detail_status="parsed", detail={"source_api": src})
    accepted, rejected = store.reusable([stored], CORP, [item], now=datetime.now(timezone.utc))
    assert TERMINATION_RAW["rcept_no"] in accepted and not rejected
    # 금액 필드가 하나도 없는 해지 원행은 여전히 받지 않는다.
    bare = {k: v for k, v in TERMINATION_RAW.items() if k not in ("ctr_prc_bfcc", "ctr_prc_atcc")}
    assert store.make_source("trust_termination", bare) is None
    # 예전 이름으로 저장된 해지 원행도 버리지 않는다.
    legacy = dict(rcept_no=TERMINATION_RAW["rcept_no"], corp_code=CORP, ctr_prc="5,000,000,000")
    assert store.make_source("trust_termination", legacy) is not None


def _bundles():
    contracts = [
        {"event": "trust_contract", "rcept_no": "20260105000037", "rcept_dt": "20260105", "amount_krw": 5_000_000_000},
        {"event": "trust_contract", "rcept_no": "20260515000711", "rcept_dt": "20260515", "amount_krw": 5_000_000_000},
        {"event": "trust_contract", "rcept_no": "20261001000001", "rcept_dt": "20261001", "amount_krw": 5_000_000_000},
    ]
    terminations = [
        {"event": "trust_termination", "rcept_no": "20260406003799", "rcept_dt": "20260406", "amount_krw": 5_000_000_000},
        {"event": "trust_termination", "rcept_no": "20260814001300", "rcept_dt": "20260814", "amount_krw": 5_000_000_000},
    ]
    results = [
        {"event": "trust_termination_result", "rcept_no": "20260406003949", "rcept_dt": "20260406",
         "actual_amount_krw": 1_019_417_330, "actual_shares": 133_000, "contract_amount_krw": 5_000_000_000,
         "body_parsed": True},
        {"event": "trust_termination_result", "rcept_no": "20260814001396", "rcept_dt": "20260814",
         "actual_amount_krw": 0, "actual_shares": 0, "contract_amount_krw": 5_000_000_000,
         "body_parsed": True, "trust_contract_date": "2026-05-15"},
    ]
    return {"trust_contract": contracts, "trust_termination": terminations, "trust_termination_result": results}


def test_trust_contract_linked_to_actual_execution():
    bundles = _bundles()
    ts._link_cycles(bundles)
    first, second, open_ = bundles["trust_contract"]
    assert (first["execution_status"], first["executed_amount_krw"]) == ("confirmed", 1_019_417_330)
    # 한도 50억 → 실제 취득 0원: 0으로 확정한다.
    assert (second["execution_status"], second["executed_amount_krw"]) == ("confirmed", 0)
    assert second["execution_rcept_no"] == "20260814001396"
    assert open_["execution_status"] == "unconfirmed"  # 아직 해지결과가 없다


def test_amount_mismatch_or_unparsed_is_not_linked():
    bundles = _bundles()
    bundles["trust_termination_result"][0]["contract_amount_krw"] = 20_000_000_000
    bundles["trust_termination_result"][1]["body_parsed"] = False
    ts._link_cycles(bundles)
    assert [c["execution_status"] for c in bundles["trust_contract"]] == ["unconfirmed"] * 3


def test_low_trust_execution_rate_is_not_flagged_as_mismatch():
    bundles = _bundles()
    bundles["trust_termination_result"][0]["actual_amount_krw"] = 26_000_000  # 0.52%
    ts._link_cycles(bundles)
    assert ts._flag_cycle_mismatches(bundles) == []


def test_buyback_counts_actual_trust_execution_not_ceiling():
    events = [
        {"event": "acquisition_decision", "rcept_dt": "20260301", "amount_krw": 1_000_000_000},
        {"event": "trust_contract", "rcept_no": "A", "rcept_dt": "20260515", "amount_krw": 5_000_000_000,
         "execution_status": "confirmed", "executed_amount_krw": 0},
        {"event": "trust_contract", "rcept_no": "B", "rcept_dt": "20261001", "amount_krw": 5_000_000_000,
         "execution_status": "unconfirmed"},
        {"event": "trust_contract", "rcept_no": "C", "rcept_dt": "20250109", "amount_krw": 3_000_000_000},
    ]
    yearly, unconfirmed = _treasury_buyback_yearly(events)
    # 확인된 신탁은 실제 취득액(A: 0원), 못 이은 신탁은 계약금액(B·C) — 사용자 결정 2026-11-03: 보통 지켜지니 더하되 확인 필요 표시.
    assert yearly == {2026: 1_000_000_000 + 5_000_000_000, 2025: 3_000_000_000}
    assert [u["rcept_no"] for u in unconfirmed] == ["B", "C"]
    assert all(u["status"] == "집행 미확인" for u in unconfirmed)
