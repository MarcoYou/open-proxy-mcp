"""판정·근거·위험신호가 **같은 payload** 를 보는지 배선 자체를 잰다.

QA 실측(260808): 변이 24개 중 11개가 살아남았고, 그중 전부가 `build_proxy_advise_payload` 의
**배선**이었다. `state_metrics` 를 `fin_metrics` 로 되돌려도, 임시주총 연도 보정을 지워도,
잠정치 경로를 통째로 꺼도 692개 테스트가 전부 통과했다. 검산 호출이 조용히 사라졌던 것도
같은 구멍이다 — 잎(헬퍼)만 테스트하고 줄기(호출부)는 아무도 안 봤다.

이 파일은 소스를 AST 로 읽어 **어느 호출이 어느 payload 를 받는지**를 고정한다. 함수 하나를
부르는 통합 테스트로는 DART 없이 재현할 수 없고, 문자열 검사로는 오늘 아침처럼 삭제를 놓친다.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "open_proxy_mcp/services/proxy_advise.py"
TREE = ast.parse(SRC.read_text(encoding="utf-8"))


#: payload 본체. 260828 as_of 게이트가 붙으면서 `build_proxy_advise_payload` 는 게이트를
#: 되돌리는 얇은 껍데기가 됐고 배선은 `_build_proxy_advise_payload` 로 옮겨졌다.
#: 이 테스트가 재는 것은 **배선이 있는 쪽**이다 — 껍데기를 보면 호출이 하나도 안 보인다.
_BODY_NAMES = ("_build_proxy_advise_payload", "build_proxy_advise_payload")


def _calls(func_name: str) -> list[ast.Call]:
    """payload 본체 안에서 `func_name` 을 부르는 지점 전부."""
    bodies = [n for n in ast.walk(TREE)
              if isinstance(n, ast.AsyncFunctionDef) and n.name in _BODY_NAMES]
    fn = max(bodies, key=lambda n: len(list(ast.walk(n))))
    return [n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == func_name]


def _arg_names(call: ast.Call) -> list[str]:
    out = [a.id for a in call.args if isinstance(a, ast.Name)]
    out += [k.value.id for k in call.keywords
            if k.arg and isinstance(k.value, ast.Name)]
    return out


def _kw(call: ast.Call, name: str) -> str | None:
    for k in call.keywords:
        if k.arg == name and isinstance(k.value, ast.Name):
            return k.value.id
    return None


STATE_CONSUMERS = [
    "_decide_financial_statements",   # 자본잠식·감사의견
    "_decide_dividend",               # 배당 재원
    "_decide_director_compensation",  # 적자 여부
    "_decide_audit_compensation",
    "_decide_retirement_pay",
    "_extract_risks",                 # 위험신호
    "_extract_facts",                 # 근거란
]


def test_every_state_consumer_reads_the_same_payload() -> None:
    """회사의 현재 상태를 말하는 것은 전부 `state_metrics` 를 받아야 한다.

    하나라도 `fin_metrics`(FY(N-2)) 로 남으면 한 메모 안에서 연도가 갈린다 — 실측으로
    지엔코(「자본잠식 없음 · 유의: 부분 자본잠식」)·웰바이오텍(사유 60.08% vs 근거 16.4%)·
    배당/보수한도(판정 「완전 자본잠식」 vs 위험신호 빈칸) 세 번 났다.
    """
    offenders = []
    for name in STATE_CONSUMERS:
        calls = _calls(name)
        assert calls, f"{name} 호출이 사라졌다"
        for c in calls:
            names = _arg_names(c)
            if "fin_metrics" in names and "state_metrics" not in names:
                offenders.append(name)
    assert not offenders, f"판정 payload 가 어긋난 곳: {offenders}"


def test_the_cross_check_deliberately_stays_on_the_confirmed_prior_year() -> None:
    """검산만은 FY(N-2)A 여야 한다 — 등식이 「본문의 전기 = FY(N-2)A 의 당기」다.

    판정 payload 로 검산하면 한 해 어긋난 값을 맞대 **없는 불일치**를 만든다.
    """
    call = _calls("_extract_facts")[0]
    assert _kw(call, "crosscheck_payload") == "fin_metrics"


def test_the_confirmed_payload_only_feeds_the_confirmed_fields() -> None:
    """확정치는 별도 인자로만 들어간다 — 판정 payload 와 섞이면 어느 해인지 알 수 없어진다."""
    call = _calls("_extract_facts")[0]
    assert _kw(call, "confirmed_payload") == "fin_confirmed"
    assert _kw(call, "confirmed_year") == "confirmed_year"


def test_the_provisional_path_and_the_egm_bump_are_still_wired() -> None:
    """둘 다 `if False` 로 바꿔도 종전 테스트는 전부 통과했다 — 존재 자체를 고정한다."""
    src = SRC.read_text(encoding="utf-8")
    assert "_provisional_state_payload(fy_raw_from_agenda" in src, "잠정치 경로가 끊겼다"
    assert "latest_annual_report_before(" in src, "주총일 기준 사업보고서 조회가 끊겼다"
    # 잠정치는 **직전 확정치를 바탕에 깔고** 자본 항목만 덮는다 — 통째로 갈아치우면
    # 현금흐름 품질·이자보상배율 같은 신호가 전부 사라진다(QA 실측: 위험신호 4개 → 1개).
    assert "_provisional_state_payload(fy_raw_from_agenda, fin_metrics)" in src


def test_a_failed_confirmed_lookup_does_not_become_the_judgment() -> None:
    """`_safe` 는 실패 시 `data` 없는 에러 dict 를 준다. 그대로 쓰면 AGAINST 가 「미확인」이 된다."""
    src = SRC.read_text(encoding="utf-8")
    assert "_conf_ok" in src, "확정치 payload 의 내용 확인이 없다"
    assert "state_metrics = fin_confirmed if (confirmed_year and _conf_ok) else fin_metrics" in src
