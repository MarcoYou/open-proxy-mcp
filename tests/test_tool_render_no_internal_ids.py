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


def test_value_up_availability_dict_covers_every_producer_value():
    from open_proxy_mcp.tools.value_up import _AVAIL_KO
    import inspect, re as _re
    from open_proxy_mcp.services import value_up as svc
    src = inspect.getsource(svc)
    produced = set(_re.findall(r'availability_status = "([a-z_]+)"', src))
    produced |= set(_re.findall(r'availability_status\s*=\s*"[a-z_]+" if [^"]*else "([a-z_]+)"', src))
    assert produced, "producer 값을 못 읽었다"
    missing = produced - set(_AVAIL_KO)
    assert not missing, f"사전에 없는 producer 값: {sorted(missing)}"


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
