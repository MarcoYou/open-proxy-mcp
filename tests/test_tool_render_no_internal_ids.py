# -*- coding: utf-8 -*-
"""도구 산출물에 엔진 내부 식별자가 새지 않는지 (렌더러 단위). network 0콜.

260728 15개 도구 라이브 스캔: `company_id: cmp_005930` 가 10개 도구,
`rcept_no` 컬럼 헤더 35건, 표 셀에 `registry_overlap`·`future_plan` 같은 enum.
지연 import 오류(SECTION_LABELS_KO 미존재)는 기존 테스트가 못 잡았다 — 스모크로 고정.
"""
from __future__ import annotations

import re

_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def test_company_id_becomes_a_stock_code_or_disappears():
    from open_proxy_mcp.tools._shared import company_id_line
    assert company_id_line({"company_id": "cmp_005930"}) == "- 종목코드 005930"
    assert company_id_line({"stock_code": "051910"}) == "- 종목코드 051910"
    assert company_id_line({}) is None                      # 값 없으면 줄을 내지 않는다
    assert "cmp_" not in (company_id_line({"company_id": "cmp_005930"}) or "")


def test_value_up_section_tag_renders_in_korean():
    """지연 import 대상이라 호출해 봐야 깨지는지 안다(SECTION_LABELS_KO 실측 ImportError)."""
    from open_proxy_mcp.tools.value_up import _tag_ko
    assert _tag_ko("future_plan") == "향후계획"
    assert _tag_ko("implementation_status") == "이행현황"
    assert "_" not in _tag_ko("some_unmapped_tag")          # 모르는 태그도 스네이크는 걷는다


def test_value_up_label_map_is_single_sourced():
    """사전을 두 벌 두면 한쪽만 고쳐져 샌다 — services 상수를 tool 이 재사용해야 한다."""
    from open_proxy_mcp.services.value_up import SECTION_LABELS_KO
    from open_proxy_mcp.tools.value_up import _tag_ko
    for tag, ko in SECTION_LABELS_KO.items():
        assert _tag_ko(tag) == ko


def _returned_literals(fn) -> set[str]:
    """함수가 실제로 return 하는 문자열 리터럴 집합 — 사전이 producer 를 덮는지 대조용.

    260728: 사전을 「관찰된 값」만 보고 손으로 쓴 탓에 절반이 새고 존재하지 않는 키가 들어갔다
    (proxy_contest `_ACTOR_KO` 는 producer 7값 중 2개만 + 가공 키 3개). 값을 손으로 고르는
    테스트는 그걸 통과시킨다 — producer 를 읽어 대조해야 한다.
    """
    import ast, inspect, textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return {n.value.value for n in ast.walk(tree)
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)}


def test_proxy_contest_dict_covers_every_producer_value():
    from open_proxy_mcp.services.proxy_contest import _fight_actor_group, _signal_actor_side
    from open_proxy_mcp.tools.proxy_contest import _GROUP_KO
    produced = _returned_literals(_fight_actor_group) | _returned_literals(_signal_actor_side)
    assert produced, "producer 리터럴을 못 읽었다 — 테스트가 무력화됐다"
    missing = produced - set(_GROUP_KO)
    assert not missing, f"사전에 없는 producer 값: {sorted(missing)}"
    for k, v in _GROUP_KO.items():
        assert not _SNAKE.search(v), f"{k} → {v}"


def test_plumbing_lines_stay_out_of_the_output():
    """번역할지 정하기 전에 「애초에 보여줄 게 맞나」를 묻는다(260728 사용자 지적).

    호출 파라미터를 되돌려주는 줄·파싱 구현 세부·바로 아래 표를 보면 아는 줄은 번역 대상이
    아니라 **삭제 대상**이다. 지우면 사전도 함께 사라져 producer 와 어긋날 일이 없다.
    되살아나면 여기서 걸린다.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "open_proxy_mcp" / "tools"
    src = "\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py"))
    for gone in ("요청한 주총 종류",        # 호출 파라미터 에코
                 "원문 출처",              # XML/HTML 중 무엇으로 파싱했나 — 우리 사정
                 "결과 기재 형식",          # 바로 아래 표를 보면 안다
                 "공시 여부: ",             # 아래 공시 목록으로 자명
                 "안건 | 카테고리 |"):      # 안건 분류 — 내부 enum 이라 독자에게 정보가 없다
    # 반례: proxy_contest timeline 의 「카테고리」는 값이 위임장 대결/소송/5% 보고라
    # 섞인 이벤트를 구분하는 실제 정보다 — 컬럼 이름이 아니라 **값이 정보인가**로 가른다
        assert gone not in src, f"보여줄 필요 없는 줄이 되살아났다: {gone}"


def test_no_tool_renders_a_raw_company_id_line():
    """소스 수준 가드 — 새 도구가 옛 패턴을 복사해 오면 여기서 걸린다."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "open_proxy_mcp" / "tools"
    offenders = [p.name for p in root.glob("*.py")
                 if "company_id: `" in p.read_text(encoding="utf-8") and p.name != "_shared.py"]
    assert not offenders, offenders


def test_no_tool_uses_rcept_no_as_a_table_header():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "open_proxy_mcp" / "tools"
    offenders = [p.name for p in root.glob("*.py")
                 if "| rcept_no |" in p.read_text(encoding="utf-8")]
    assert not offenders, offenders


def test_every_tool_module_imports_what_it_uses():
    """이름만 쓰고 import 를 빠뜨리면 호출 시점에야 터진다 — shareholder_meeting_notice 가
    19,637자 → 「name 'company_id_line' is not defined」 100자로 무너졌다(260728 실측).
    모든 tool 모듈을 실제로 import 해 보고, 헬퍼 사용처의 import 존재를 소스로도 확인한다."""
    import importlib
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "open_proxy_mcp" / "tools"
    for p in sorted(root.glob("*.py")):
        if p.name == "__init__.py":
            continue
        importlib.import_module(f"open_proxy_mcp.tools.{p.stem}")
        src = p.read_text(encoding="utf-8")
        if "company_id_line(" in src and p.name != "_shared.py":
            assert "import" in src and "company_id_line" in src.split("def ", 1)[0], p.name


def test_independence_dict_covers_director_evaluation_results():
    """sub_factor `result` 는 director_evaluation 이 뱉는다. 25사 스윕엔 미성년·결격 후보가
    없어 안 잡혔고 사전 감사(producer→사전 방향)로만 드러났다(260728).
    """
    import inspect, re as _re
    from open_proxy_mcp.services import director_evaluation as de
    from open_proxy_mcp.tools.proxy_advise_before_meeting import _INDEP_RESULT_KO
    src = inspect.getsource(de)
    produced = set(_re.findall(r'"result":\s*"([a-z_]+)" if [^\n]*else "([a-z_]+)"', src))
    flat = {v for pair in produced for v in pair}
    assert flat, "producer 값을 못 읽었다 — 테스트가 무력화됐다"
    missing = flat - set(_INDEP_RESULT_KO)
    assert not missing, f"사전에 없는 result 값: {sorted(missing)}"


def test_krw_formatter_always_carries_the_unit():
    """「334조」만 쓰면 무엇의 단위인지 문서 안에서 확정되지 않는다(260728 QA 지적).

    변이 테스트에서 이 가드가 없으면 '원'을 떼도 아무 테스트가 안 깨졌다.
    """
    from open_proxy_mcp.tools.financial_metrics import _format_krw_human as f, _num
    for v in (334_000_000_000_000, 3_340_000_000_000, 333_400_000_000, -690_854_000_000, 12_345):
        out = f(v)
        assert out.endswith("원"), f"{v} → {out}"
    assert f(None) == "-"
    assert _num(15410) == "15,410"        # 문서 내 다른 숫자와 표기를 맞춘다
