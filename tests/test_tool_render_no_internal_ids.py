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


def test_no_module_references_an_undefined_global_name():
    """함수 **안**에서만 쓰이는 이름은 import 해 봐도 안 터진다 — 호출돼야 터진다.

    260804 실측: `dividend` 의 `_TREND_KO` 가 정의된 적이 없어 md 경로가 통째로 NameError.
    위 import 테스트도, 490개 테스트도 통과했다(렌더러를 호출한 적이 없다). 모든 렌더 분기를
    실행하는 테스트는 현실적으로 못 쓰니, **정적으로** 미정의 전역 참조를 막는다.

    symtable 은 함수 스코프에서 지역 할당 없이 읽히는 이름을 global 로 표시한다. 그 이름이
    import 후 모듈에도 builtins 에도 없으면 그 분기는 실행되는 순간 NameError 다.
    (`from __future__ import annotations` 하에서 어노테이션 전용 이름은 참조로 안 잡히고,
    그게 없는 모듈이면 def 시점에 평가돼 import 단계에서 이미 걸린다 — 어느 쪽도 오탐 아님.)
    """
    import builtins, importlib, symtable
    from pathlib import Path

    def _globals_read(table, found: set[str]) -> None:
        for sym in table.get_symbols():
            if table.get_type() == "function" and sym.is_global() and sym.is_referenced():
                found.add(sym.get_name())
        for child in table.get_children():
            _globals_read(child, found)

    root = Path(__file__).resolve().parent.parent / "open_proxy_mcp"
    bad = []
    for p in sorted(list(root.glob("tools/*.py")) + list(root.glob("services/*.py"))):
        if p.name == "__init__.py":
            continue
        names: set[str] = set()
        for child in symtable.symtable(p.read_text(encoding="utf-8"), str(p), "exec").get_children():
            _globals_read(child, names)
        mod = importlib.import_module(f"open_proxy_mcp.{p.parent.name}.{p.stem}")
        bad += [f"{p.parent.name}/{p.name}: {n}" for n in sorted(names)
                if not hasattr(mod, n) and not hasattr(builtins, n)]
    assert not bad, bad


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


# ── 사유 코드가 사람 문장에 섞이는 경우 ────────────────────────────────────────────
# 260802 실측(삼성증권 business_details): 「…아래 원문에서 직접 확인하세요 map_not_loaded」.
# 위 가드들은 **필드명·company_id·표 헤더**를 봤을 뿐이라 「사유 코드가 문장 끝에 붙는」
# 이 형태를 통과시켰다. 코드는 na_code(진단)로, 문장에는 한국어 문면만 — 두 갈래를 다 본다.

# 호출자가 그대로 다시 쓰는 **공개 파라미터명**은 내부 식별자가 아니다(재조회 안내문에 실린다).
_PUBLIC_PARAMS = {"reprt_code", "bsns_year", "corp_code", "stock_code", "context_mode"}


def _snake_in_prose(md: str) -> list[str]:
    """사람이 읽는 산문 줄에 섞인 스네이크 토큰. 표 행·코드펜스(원문 첨부)는 제외한다."""
    bad, fenced = [], False
    for ln in md.splitlines():
        s = ln.strip()
        if s.startswith("```"):
            fenced = not fenced
            continue
        if fenced or s.startswith("|"):
            continue
        bad += [t for t in _SNAKE.findall(s) if t not in _PUBLIC_PARAMS]
    return bad


def test_segment_reason_sentence_never_carries_the_coordinate_map_code(monkeypatch):
    """좌표 맵 미탑재(=기본 상태)에서 사유 코드가 문장으로 새던 회귀 — 삼성증권 실측 경로 그대로.

    텍스트 앵커가 값을 못 뽑고 → 식별자 경로가 map_not_loaded 로 끝나는 조합을 재현한다.
    """
    from open_proxy_mcp.services import business_details as bd, coordinate_map
    from open_proxy_mcp.tools.business_details import _render

    monkeypatch.setenv("OPM_COORDINATE_MAP_PATH", "/nonexistent/coordinate_map.json")
    coordinate_map._CACHE.update({"path": None, "mtime": None, "data": None})
    monkeypatch.setattr(bd, "find_segment_note_region",
                        lambda _t: ("3. 영업부문", "3. 영업부문\n(단위: 백만원)\n"))

    note = "3. 영업부문\n" + "당사는 재화의 종류와 용역의 성격에 따라 영업부문을 구분하여 공시하고 있습니다. " * 4
    sp = bd.extract_segment_profit("", note, "연결재무제표 주석",
                                   note_html='<TABLE-GROUP ACLASS="X"><TITLE>3. 영업부문</TITLE></TABLE-GROUP>')
    assert "map_not_loaded" in sp.na_code                  # 진단은 남는다
    assert "부문 좌표 맵을 불러오지 못했습니다" in sp.na_reason   # 문면은 한국어로
    assert not _SNAKE.search(sp.na_reason), sp.na_reason

    # 라이브 경로는 여기서 한 번 더 감싸진다 — 감싼 뒤에도 문장이 깨끗한지 본다.
    seg, _ = bd.build_segment_fallback("", sp.na_reason, note_text=note, na_code=sp.na_code)
    assert "map_not_loaded" in seg["na_code"] and not _SNAKE.search(seg["na_reason"])
    out = _render({"status": "ok", "subject": "테스트", "data": {"report": {}, "segments": seg}})
    assert "확인 불가" in out and not _snake_in_prose(out), _snake_in_prose(out)


def test_reason_code_dictionary_covers_every_producer_value():
    """사전을 「관찰된 값」으로 손수 쓰면 절반이 샌다 — producer 를 읽어 대조한다(260728 교훈).

    모르는 코드는 빈 문자열로 떨어뜨리는 설계라, 사전 누락 = **문면 소실**(무표시 열화)이다.
    """
    import ast, inspect, textwrap
    from open_proxy_mcp.services import business_details as bd

    def _codes(fn) -> set[str]:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        # `spec.get("title_must_contain")` 같은 **조회 키**는 사유 코드가 아니다
        keys = {id(a) for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get" for a in n.args}
        return {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in keys and _SNAKE.fullmatch(n.value)}

    produced = (_codes(bd.find_segment_note_region_by_code)
                | _codes(bd.render_segment_note_md_by_code))
    rejects = _codes(bd._code_path_acceptable) | {"parse_failed"}   # parse_failed = 호출측 판정
    assert produced and rejects, "producer 리터럴을 못 읽었다 — 테스트가 무력화됐다"
    assert not produced - set(bd._CODE_REASON_KO), sorted(produced - set(bd._CODE_REASON_KO))
    assert not rejects - set(bd._CODE_REJECT_KO), sorted(rejects - set(bd._CODE_REJECT_KO))
    for ko in list(bd._CODE_REASON_KO.values()) + list(bd._CODE_REJECT_KO.values()):
        assert not _SNAKE.search(ko), ko
    # 합성 코드(제목 불일치 · 채택 거부)와 미지의 코드까지 — 어느 쪽도 코드를 흘리지 않는다
    for code in ("title_mismatch:20-2. 주요 고객", "code:consolidated/rejected:geographic_only",
                 "code:separate/rejected:parse_failed", "brand_new_code_v2", ""):
        assert not _SNAKE.search(bd.code_reason_ko(code)), code


def test_na_reason_literals_are_human_sentences_not_codes():
    """`na_reason` 은 사용자 문면, `na_code` 는 진단 — SegmentProfit 계약(소스 수준 가드).

    260802: `form_financial5_not_supported_v1` 이 「해당없음 — form_…」으로 렌더됐다.
    f-string 은 상수 조각을 이어 검사한다(`f"form_{form}_not_supported_v1"` 를 놓치지 않게).
    """
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "open_proxy_mcp"

    def _text(node) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            # 치환칸은 **빈칸이 아니라 자리표시자**로 잇는다 — 그냥 이으면 `f"form_{x}_not_ok"` 가
            # "form__not_ok"(연속 언더바)가 돼 스네이크 정규식을 빠져나간다(실측으로 확인).
            return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str)
                           else "x" for v in node.values)
        return None                       # 계산값(원문 인용 등)은 정적으로 볼 수 없다

    bad = []
    for p in list(root.glob("services/*.py")) + list(root.glob("tools/*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            vals = []
            if isinstance(n, ast.Dict):
                vals = [v for k, v in zip(n.keys, n.values)
                        if isinstance(k, ast.Constant) and k.value == "na_reason"]
            elif isinstance(n, ast.Call):
                vals = [kw.value for kw in n.keywords if kw.arg == "na_reason"]
            for v in vals:
                s = _text(v)
                hits = [t for t in _SNAKE.findall(s or "") if t not in _PUBLIC_PARAMS]
                if hits:
                    bad.append(f"{p.name}:{v.lineno} {hits} — {(s or '')[:50]}")
    assert not bad, bad


def test_form_type_enum_never_renders_raw():
    """제목에 `dual`·`standard7` 이 그대로 찍혔다(260802 삼성증권 실측 — 스네이크가 아니라
    기존 가드에 안 걸렸다). enum 은 producer(detect_form)에서 읽어 사전과 대조한다."""
    import inspect, re as _re
    from open_proxy_mcp.services import business_details as bd
    from open_proxy_mcp.tools.business_details import _FORM_KO, _render
    produced = set(_re.findall(r"return (FORM_[A-Z]+)", inspect.getsource(bd.detect_form)))
    assert produced, "producer 값을 못 읽었다 — 테스트가 무력화됐다"
    for name in produced:
        val = getattr(bd, name)
        assert val in _FORM_KO, f"{name}={val} 이 사전에 없다"
    for val in list(_FORM_KO) + ["brand_new_form"]:
        out = _render({"status": "ok", "subject": "테스트",
                       "data": {"report": {"report_nm": "사업보고서"}, "form_type": val}})
        assert val not in out, out.splitlines()[0]


def test_geo_absence_kind_dictionary_covers_every_producer_value():
    """`mark.get(kind, kind)` 로 두면 새 kind 가 **굵은 글씨로** 그대로 나간다."""
    import inspect, re as _re
    from open_proxy_mcp.services import segment_grid
    from open_proxy_mcp.tools.business_details import _geo_lines
    produced = set(_re.findall(r'"absence_kind":\s*"([a-z_]+)"',
                               inspect.getsource(segment_grid)))
    assert produced, "producer 값을 못 읽었다 — 테스트가 무력화됐다"
    for kind in produced | {"some_unmapped_kind"}:
        out = "\n".join(_geo_lines({"absence_kind": kind, "absence_detail": "설명"}, "####"))
        assert not _snake_in_prose(out), f"{kind} → {out}"


def test_no_user_facing_sentence_embeds_the_rcept_no_field_name():
    """「rcept_no가 제공되어…」처럼 **문장 안에** 내부 필드명이 박힌 것은 표·헤더 치환으로는
    안 잡힌다(260729 사용자 지적). 사람이 읽는 문자열에서만 검사한다.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "open_proxy_mcp"
    bad = []
    for p in list(root.glob("tools/*.py")) + list(root.glob("services/*.py")):
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            s = ln.strip()
            if s.startswith("#") or s.startswith('"""') or "rcept_no" not in ln:
                continue
            # 한글이 같은 따옴표 문자열 안에 있으면 사용자에게 렌더되는 문장으로 본다
            for m in re.finditer(r'"([^"\n]*rcept_no[^"\n]*)"', ln):
                seg = m.group(1)
                # `{...get('rcept_no')}` 같은 **값 참조**는 필드명 노출이 아니다 —
                # 중괄호 안에 있는 것은 제외한다(측정 도구 오탐 3건 교정).
                if re.search(r"\{[^{}]*rcept_no[^{}]*\}", seg):
                    continue
                if re.search(r"[가-힣]", seg):
                    bad.append(f"{p.name}:{i} {seg[:60]}")
    assert not bad, bad
