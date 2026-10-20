"""지배 귀속 순이익 줄 고르기 — 수집 배치·서버 실시간 firm·재무 지표·결산기 칸이 **한 규칙**(`services/controlling_ni`)을 쓴다 (261020 p1e firm-ctrl-ni).

#152 가 분기·연간 수집에 넣은 값 구조 검사(지배 + 비지배 = 당기순이익)가 서버 실시간 경로엔 없어, 10-20 운영 075180 firm 응답이 net_income_fy0 27.68억
(총포괄 지배 몫)·per_fy0 19.84 인데 같은 응답 경고는 「적자(FY0 EPS≤0) — PER N/M」이었다(응답 안에서 어긋남). 아래 원문 꼴은 운영 보고서를 줄였다(값은 원문 그대로).
DART 0콜 · DB 0.
"""
from __future__ import annotations

import asyncio
import http.server
import importlib.util
import json
import pathlib
import threading

import pytest

from open_proxy_mcp.services import controlling_ni as C
from open_proxy_mcp.services import financial_metrics as FM
from open_proxy_mcp.services import period_cells as PC
from open_proxy_mcp.services import price_multiple_data as S
from scripts import market_fund_quarterly as Q

F = "ifrs-full_"
NOSTD = "-표준계정코드 미사용-"


def r(sj, aid, nm, ordv, cur, cum=None, prev=None, rcept="20260318000001"):
    """손익 줄 — `cur` = 당기(사업보고서·1분기는 누적과 같다), `cum` = 당기 누적(반기·3분기), `prev` = 전기."""
    return {"sj_div": sj, "account_id": aid, "account_nm": nm, "ord": str(ordv), "currency": "KRW", "rcept_no": rcept,
            "thstrm_amount": "" if cur is None else str(cur), "thstrm_add_amount": "" if cum is None else str(cum),
            "frmtrm_amount": "" if prev is None else str(prev)}


def rows_075180_fy2025():
    """075180 2025 사업보고서 꼴 — 지배 순이익 id 줄 27.68억 = 총포괄(지배 몫), 당기순이익 −5.15억(비지배 없음)."""
    return [
        r("CIS", F + "ComprehensiveIncome", "총포괄손익", 23, 2768099768),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "포괄손익, 지배기업의 소유주에게 귀속되는 지분", 25, 2768099768),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 34, 3283520391),
        r("CIS", F + "ProfitLoss", "당기순이익(손실)", 41, -515420623),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 당기순이익(손실)", 43, 2768099768),
    ]


def rows_009290_2023q3():
    """009290 2023 3분기 꼴 — 지배·비지배를 표준 계정코드 없이 이름으로만(지배 314.3억 · 비지배 18.5억, 분기순이익 332.9억)."""
    return [
        r("CIS", NOSTD, "비지배지분", 11, 488988232, 1853264169),
        r("CIS", NOSTD, "지배기업의 소유주지분", 12, 8830699946, 31432383139),
        r("CIS", F + "ComprehensiveIncome", "총포괄이익", 15, 8544711953, 33650000071),
        r("CIS", F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "비지배지분", 17, 509004719, 2208471443),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업의 소유주지분", 18, 8035707234, 31441528628),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 26, -774976225, 364352763),
        r("CIS", F + "ProfitLoss", "분기순이익", 34, 9319688178, 33285647308),
    ]


# ── 서버 실시간 firm(`price_multiple_data._ctrl_ni`·`_ctrl_ni_cum`) — 수집과 같은 값 ─────────────────────────

def test_firm_fy0_rejects_comprehensive_income_owner_line():
    """075180: 종전 서버 경로는 지배 id 줄 27.68억(총포괄)을 그대로 써 PER(FY0) 19.84 를 냈다 — 수집(DB)은 −5.15억."""
    assert S._ctrl_ni(rows_075180_fy2025()) == -515420623
    assert Q.extract_controlling_ni_cum(rows_075180_fy2025())[0] == -515420623.0


def test_firm_quarter_cum_uses_named_owner_line_not_total():
    """009290: 종전 서버 경로는 지배 id 줄이 없으면 총순이익 − 비지배 id 줄(없음) = 332.9억(비지배 포함). DB(분기 수집)는 이름 줄 314.3억."""
    assert S._ctrl_ni_cum(rows_009290_2023q3()) == 31432383139
    assert Q.extract_controlling_ni_cum(rows_009290_2023q3()) == (31432383139.0, "CIS_NM")


def test_firm_reads_old_taxonomy_prefix():
    """2018 무렵 사업보고서의 옛 분류체계(`ifrs_…`) — 종전 서버 경로는 `ifrs-full_` 만 봐서 빈 값이었다."""
    rows = [r("CIS", "ifrs_ProfitLoss", "당기순이익", 9, 5065502394), r("CIS", "ifrs_ProfitLossAttributableToOwnersOfParent", "지배기업 소유주지분", 11, 5065502394)]
    assert S._ctrl_ni(rows) == 5065502394


def test_firm_prior_field_same_rule():
    """firm 의 전기 칸(스케일 가드 대조값 `frmtrm_amount`)도 같은 규칙 — 000680 2024 사업보고서 꼴의 전기(FY2023) 지배 순이익 107.05억(총포괄 95.93억이던 것)."""
    rows = [
        r("CIS", F + "ComprehensiveIncome", "총포괄손익", 9, -32814275801, prev=9592881336),
        r("CIS", F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "포괄손익, 비지배지분", 11, 12067190440, prev=0),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "포괄손익, 지배기업의 소유주에게 귀속되는 지분", 12, -44881466241, prev=9592881336),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 21, -923447500, prev=-1112102292),
        r("CIS", F + "ProfitLoss", "당기순이익(손실)", 24, -31890828301, prev=10704983628),
        r("CIS", F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분에 귀속되는 당기순이익(손실)", 26, 12067190440, prev=0),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 당기순이익(손실)", 27, -44881466241, prev=9592881336),
    ]
    assert S._ctrl_ni(rows) == -43958018741 and S._ctrl_ni(rows, "frmtrm_amount") == 10704983628


def test_deficit_warning_follows_the_per_basis():
    """값과 경고가 같은 근거 — PER 을 냈으면 「PER N/M」이라 적지 않는다(075180 꼴 응답 안 어긋남)."""
    assert S._deficit_warning(-12, -515420623, None) == "적자(FY0 EPS≤0) — PER N/M."
    w = S._deficit_warning(-12, 2768099768, 19.84)
    assert "PER N/M" not in w and "19.84" in w
    assert S._deficit_warning(None, -515420623, None) == "적자(FY0 지배순이익≤0) — PER N/M."   # 공시 EPS 를 못 읽어도 지배순이익으로
    assert S._deficit_warning(100, 5, 10.0) is None


# ── 재무 지표(financial_metrics) — 같은 규칙 ─────────────────────────────────────────────────────────

def test_financial_metrics_controlling_ni_same_rule():
    """075180 꼴: 종전 재무 지표는 지배 id 줄 첫 줄(27.68억 — 총포괄)을 ROE·순이익률·전년비 분자로 썼다."""
    assert FM._build_account_map_all(rows_075180_fy2025())["controlling_interest_income"] == -515420623
    assert FM._build_account_map_all(rows_009290_2023q3(), cumulative_is=True)["controlling_interest_income"] == 31432383139


# ── 한 규칙인가(중복 구현 없음) ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rows", [rows_075180_fy2025(), rows_009290_2023q3()])
def test_all_paths_agree(rows):
    v = C.controlling_ni(rows)[0]
    assert Q.extract_controlling_ni_cum(rows)[0] == v
    assert S._ctrl_ni_cum(rows) == int(round(v))
    assert PC.ctrl_ni(rows, "thstrm_add_amount" if rows[0]["thstrm_add_amount"] else "thstrm_amount")[0] == pytest.approx(
        C.controlling_ni(rows, "thstrm_add_amount" if rows[0]["thstrm_add_amount"] else "thstrm_amount")[0])


def test_one_implementation():
    """규칙 함수는 한 곳에만 있다 — 분기 수집의 이름은 공용 모듈의 같은 객체."""
    assert Q.extract_controlling_ni_cum is C.extract_controlling_ni_cum and Q._ctrl_in_sj is C._ctrl_in_sj
    assert Q.ni_attrib_side is C.ni_attrib_side


# ── 지배 id 줄이 순이익 쪽 비지배 id 줄과 짝이 안 맞을 때(`_id_off_nci`) — 결산기 칸 규칙에서 합침 ──────────────

def test_id_line_sign_flipped_uses_total_minus_minority():
    """298000 2024 1분기 꼴 — 지배 id 줄 +739.0억(부호), 분기순손익 −739.0억, 비지배 0(총포괄 −674.0억 = 순손익 + 기타포괄 65.0억)."""
    rows = [
        r("IS", F + "ProfitLoss", "분기순손익", 17, -73903208947),
        r("IS", F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 19, 0),
        r("IS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주지분", 20, 73903208947),
        r("CIS", F + "ComprehensiveIncome", "분기총포괄손익", 6, -67399243673),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업의 소유주지분", 9, -67399243673),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 10, 6503965274),
    ]
    assert C.controlling_ni(rows) == (-73903208947.0, "TOTAL_MINUS_MINORITY_IDX")


def test_id_line_digit_dropped_prior_column():
    """347860 2025 사업보고서 전전기 칸 꼴 — 지배 id 줄 −26.8억(자릿수 빠짐), 당기순이익 −267.76억, 비지배 0.04억(총포괄 −274.85억 · 기타포괄 −7.09억)."""
    def y(aid, nm, v):
        return {"sj_div": "CIS", "account_id": aid, "account_nm": nm, "bfefrmtrm_amount": str(v)}
    rows = [y(F + "ComprehensiveIncome", "당기총포괄이익(손실)", -27484599842), y(F + "OtherComprehensiveIncome", "기타포괄손익", -708575194),
            y(F + "ProfitLoss", "당기순이익(손실)", -26776024648), y(F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 3631815),
            y(F + "ProfitLossAttributableToOwnersOfParent", "지배기업소유주지분", -2677656463)]
    assert C.controlling_ni(rows, "bfefrmtrm_amount") == (-26779656463.0, "TOTAL_MINUS_MINORITY_IDX")


def test_id_line_kept_when_total_line_itself_is_wrong():
    """049180 2019 반기 꼴 — 옛 분류체계 당기순이익 줄에 법인세차감전 값(−35.30억), 참 순이익은 이름 줄 −43.68억 = 지배 −44.35억 + 비지배 0.67억
    (총포괄 −22.19억 − 기타포괄 21.49억). 총계 줄을 믿을 수 없으면 지배 줄을 버리지 않는다(종전 값)."""
    rows = [
        r("CIS", "ifrs_ProfitLoss", "반기순이익(손실)", 12, -1904075155, -3529963181),
        r("CIS", NOSTD, "반기순이익", 14, -2937392275, -4367890536),
        r("CIS", "ifrs_ProfitLossAttributableToOwnersOfParent", "지배지분", 16, -2926541321, -4435195488),
        r("CIS", "ifrs_ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 17, -10850954, 67304952),
        r("CIS", "ifrs_OtherComprehensiveIncome", "기타포괄손익", 22, -1727037227, 2148652323),
        r("CIS", "ifrs_ComprehensiveIncome", "총포괄손익", 29, -4664429502, -2219238213),
    ]
    assert C.controlling_ni(rows) == (-4435195488.0, "CIS_ID")


def test_dropped_id_line_still_pairs_with_discontinued_part():
    """049630 2024 3분기 꼴 — 「지배기업의 소유주(계속)」 27.78억이 지배 id 줄, 「지배기업의 소유주(중단)」 −3.45억이 이름 줄. 둘의 합 24.34억 + 비지배 1.21억
    = 당기순이익 25.55억. 지배 id 줄 혼자는 비지배와 짝이 안 맞지만 두 몫의 합으로는 쓴다."""
    rows = [
        r("CIS", F + "ComprehensiveIncome", "총포괄손익", 10, -3178802971, 2653211119),
        r("CIS", NOSTD, "지배기업의 소유주(중단)", 12, 0, -344617974),
        r("CIS", F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "비지배지분에 귀속되는 포괄손익", 13, 0, 123149341),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업의 소유주(계속)", 14, -3178802971, 2874679752),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 27, -212945138, 98560624),
        r("CIS", F + "ProfitLoss", "당기순이익(손실)", 32, -2965857833, 2554650495),
        r("CIS", NOSTD, "지배기업의 소유주(중단)", 34, 0, -344617974),
        r("CIS", F + "ProfitLossAttributableToNoncontrollingInterests", "비지배분에 귀속되는 분기순이익(손실)", 35, 0, 120959585),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주(계속)", 36, -2965857833, 2778308884),
    ]
    assert C.controlling_ni(rows) == (2433690910.0, "CIS_PARTS")


def test_minority_line_with_only_quarter_value_is_not_a_pair():
    """01307593 2026 반기 꼴 — 비지배 줄은 누적 칸이 비고 3개월 값(1.17억)만. 지배 id 줄 누적 27.56억 = 당기순이익 누적 — 짝으로 보지 않는다(종전 값)."""
    rows = [
        r("CIS", F + "ComprehensiveIncome", "총포괄이익(손실)", 11, 256197707, 3195686047),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업의 소유주지분", 14, 139300479, 3195686047),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 23, 157911169, 439503752),
        r("CIS", F + "ProfitLoss", "연결반기순이익(손실)", 26, 98286538, 2756182295),
        r("CIS", F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 28, 116897228),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업소유주지분", 29, -18610690, 2756182295),
    ]
    assert C.controlling_ni(rows) == (2756182295.0, "CIS_ID")


def test_dropped_id_line_does_not_fall_back_to_unsorted_name_line():
    """지배 id 줄을 비지배와 짝이 안 맞아 버렸으면(`_id_off_nci`) 값 구조로 가를 수 없는 이름 줄로도 물러나지 않는다 — 총계 − 비지배(값 구조가 맞는 길).
    (261020 검토 — 변이 시험에서 이 갈래만 시험이 없었다: `fallback is not None and not dropped` 의 `not dropped` 를 빼도 다 통과했다.)
    꼴: 당기순이익 100억 · 비지배 id 줄 10억 · 지배 id 줄 100억(= 총계 — 비지배와 짝 안 맞음) · 이름 줄 「지배기업소유주지분」 95억(어느 구조에도 안 맞음),
    총포괄 = 당기순이익(기타포괄 0) — 총계 줄은 믿을 수 있다."""
    rows = [
        r("CIS", F + "ProfitLoss", "당기순이익", 10, 10_000_000_000),
        r("CIS", F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 11, 1_000_000_000),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주지분", 12, 10_000_000_000),
        r("CIS", NOSTD, "지배기업소유주지분", 13, 9_500_000_000),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 20, 0),
        r("CIS", F + "ComprehensiveIncome", "총포괄손익", 21, 10_000_000_000),
    ]
    assert C.controlling_ni(rows) == (9_000_000_000.0, "TOTAL_MINUS_MINORITY_IDX")


# ── 연간 수집(`market_val_series.ctrl_ni`) — 지배 계정이 없으면 총계(비지배 포함)이던 것 ───────────────────────

def _series():
    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("market_val_series_shared", root / "scripts" / "market_val_series.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_annual_named_owner_line_with_minority():
    """089860 FY2021 꼴 — 표준 계정코드 없이 이름으로 지배·비지배(지배 1,187.5억 · 비지배 −51.7억, 당기순이익 1,135.8억). 종전 연간 규칙은 총계 1,135.8억."""
    rows = [
        r("CIS", F + "ProfitLoss", "당기순이익", 30, 113580000000),
        r("CIS", NOSTD, "지배기업의 소유주에게 귀속되는 당기순이익", 31, 118750000000),
        r("CIS", NOSTD, "비지배지분에 귀속되는 당기순이익", 32, -5170000000),
    ]
    v, note = _series().ctrl_ni(rows)
    assert v == 118750000000.0 and note == "종전 규칙 값 113580000000원 → CIS_NM"


def test_annual_total_unchanged_without_minority():
    """056360 FY2021 꼴(지배 줄에 총포괄 계정 id, 비지배 없음) — 지배 몫 = 총계라 종전 값 그대로(까닭 없음)."""
    rows = [
        r("CIS", F + "ProfitLoss", "당기순이익", 12, 7012006146),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 13, -156459757),
        r("CIS", F + "ComprehensiveIncome", "총포괄이익", 16, 6855546389),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업지분", 18, 7012006146),
        r("CIS", NOSTD, "지배기업지분", 20, 6855546389),
    ]
    assert _series().ctrl_ni(rows) == (7012006146.0, None)


# ── 정정 사업보고서 다시 받기(`market_val_series.amended_targets`) ─────────────────────────────────────────

def _it(cc, nm, rc):
    return {"corp_code": cc, "report_nm": nm, "rcept_no": rc}


def test_amended_report_refetched_once():
    """246710 FY2025 — 3/18 사업보고서, 8/25 [기재정정](DB −349.4억 · 지금 DART −319.5억), 8/27 [첨부정정]. 접수번호 칸이 비었으면 [기재정정]이 있을 때,
    접수번호가 있으면 그보다 늦은 보고서가 있을 때만 다시 받는다. [첨부정정]·[첨부추가]는 보지 않는다."""
    S2 = _series()
    items = [_it("01109937", "사업보고서 (2025.12)", "20260318001580"), _it("01109937", "[기재정정]사업보고서 (2025.12)", "20260825000373"),
             _it("01109937", "[첨부정정]사업보고서 (2025.12)", "20260827000431")]
    cc = {"01109937": "246710"}
    assert S2.amended_targets(items, {("246710", 2025): ("ok", None)}, cc) == {("246710", 2025)}
    assert S2.amended_targets(items, {("246710", 2025): ("ok", "20260318001580")}, cc) == {("246710", 2025)}
    assert S2.amended_targets(items, {("246710", 2025): ("ok", "20260825000373")}, cc) == set()        # 이미 정정본 — [첨부정정]은 안 봄
    assert S2.amended_targets(items[:1], {("246710", 2025): ("ok", None)}, cc) == set()                # 원본만 — 다시 받지 않는다
    assert S2.amended_targets(items, {("246710", 2025): ("nodata", None)}, cc) == set()                # ok 아닌 행은 정규 수집이 본다
    assert S2.amended_targets(items, {}, cc) == set()


def test_amended_report_fiscal_label_is_report_year():
    """결산월이 12월이 아니면 이름의 연도가 사업연도 표기다(「사업보고서 (2026.03)」 → 2026 — 연간 표와 같은 DART 표기)."""
    S2 = _series()
    items = [_it("00100001", "[기재정정]사업보고서 (2026.03)", "20260901000001")]
    assert S2.amended_targets(items, {("000001", 2026): ("ok", "20260615000001")}, {"00100001": "000001"}) == {("000001", 2026)}


def test_fetch_then_amended_runs_in_one_event_loop(monkeypatch, capsys):
    """`market_val_series.py --fetch --amended`(한 실행에 둘 다) — 정규 수집 뒤 정정 사업보고서 다시 받기가 **같은 이벤트 루프**에서 돈다(261020 검토).
    (주간 일정은 두 단계를 따로 돌린다 — 아래 시험. 이 시험은 손으로 둘을 함께 줄 때.)
    종전엔 `asyncio.run` 을 두 번 불러, 정규 수집이 DART 를 한 번이라도 부르면(자료 없음 행 다시 보기 — 주간 실행은 거의 늘) 프로세스에 하나인
    DART 클라이언트의 연결 풀이 닫힌 첫 루프에 묶여 둘째 루프의 정기공시 목록이 `RuntimeError: Event loop is closed` — 경고만 남고 정정 행을 하나도 안 받았다.
    가짜 DART(로컬 HTTP)·가짜 DB 로 실제 클라이언트를 돌린다(DART 0콜 · DB 0). 정정본에 연결 재무제표가 없으면 별도로 바꿔 쓰지 않는다(`keep_fs`)."""
    S2 = _series()
    from open_proxy_mcp.dart import client as dc
    hits: list[str] = []

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            ep = self.path.split("?")[0].rsplit("/", 1)[-1]
            hits.append(ep)
            body = ({"status": "000", "total_page": 1, "list": [_it("00000001", "[기재정정]사업보고서 (2024.12)", "20261015000001")]}
                    if ep == "list.json" else {"status": "013", "message": "조회된 데이타가 없습니다."})
            b = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    class Res(list):
        def fetchone(self):
            return self[0] if self else None

    class Con:
        def execute(self, sql, params=None):
            s = " ".join(sql.split())
            if "FROM dart_fundamentals WHERE fetched='ok'" in s:
                return Res([("000001", "00000001")])
            if s.startswith("SELECT ticker, fy, fetched FROM dart_finstat_y"):
                return Res([("000001", 2024, "ok")])
            if s.startswith("SELECT ticker, fy, fs FROM dart_finstat_y"):
                return Res([("000001", 2024, "CFS")])
            return Res([])

        def close(self):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    key = "test-fetch-then-amended-one-loop"
    try:
        monkeypatch.setattr(dc, "OPENDART_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
        for k in [k for k in __import__("os").environ if k.startswith("OPENDART_API_KEY")]:
            monkeypatch.delenv(k)
        monkeypatch.setenv("OPENDART_API_KEY", key)
        monkeypatch.setenv("SERIES_SLEEP", "0")
        monkeypatch.setattr(S2, "_pg", lambda: Con())
        monkeypatch.setattr(S2, "_flush", lambda buf, replace=False: buf.clear())
        monkeypatch.setattr(S2.rcal, "load_fye_map", lambda con: {})
        monkeypatch.setattr(S2, "ecos_key_missing_note", lambda *_a: None)
        S2.main(["--fetch", "--amended"])                         # 명령줄 그대로
    finally:
        srv.shutdown()
        dc._instances.pop(key, None)
        dc._instance_seen.pop(key, None)
    out = capsys.readouterr().out
    assert "목록 실패" not in out and "다시 받을 연간 행 1" in out
    first_list = hits.index("list.json")
    assert first_list > 0                                         # 정규 수집이 먼저 DART 를 불렀다(루프가 바뀌면 깨지던 조건)
    assert hits[first_list:].count("fnlttSinglAcntAll.json") == 1  # 정정 행은 연결만 — 빈 응답이어도 별도로 물러나지 않는다
    assert "있던 행 그대로" in out


def test_weekly_schedule_refetches_amended_reports_before_q4_seed():
    """주간 재무 일정(finstat-quarterly)이 정규 연간 수집 뒤·Q4 seed 앞에 정정 사업보고서 다시 받기를 돈다(바뀐 연간 값을 seed·파생이 옮기게)."""
    wf = (pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/finstat-quarterly.yml").read_text(encoding="utf-8")
    order = [ln.strip().split("scripts/", 1)[1] for ln in wf.splitlines() if ln.strip().startswith("run: uv run python scripts/")]
    assert order.index("market_val_series.py --fetch") < order.index("market_val_series.py --amended") < order.index("market_fund_quarterly.py --seed")
