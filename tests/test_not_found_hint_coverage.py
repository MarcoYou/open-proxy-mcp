"""회사를 못 찾았을 때의 안내가 **모든 진입점에서 같은지** — AST 스캔, 네트워크 0.

기존 `tests/test_company_not_found_hint.py` 는 **문구 복제**만 막는다(같은 리터럴 금지).
그래서 「헬퍼를 아예 안 부르는」 파일은 리터럴이 다르니 통과했다 — 260909 조사에서
`resolve_company_query` 를 쓰는 25개 중 **11개가 그렇게 통과하고 있었다.**

증상: 같은 회사를 두 tool 로 물으면 한쪽만 「사명이 바뀌었다 → 케이젯정밀(036560)」을 준다.
사명 변경은 하필 지배구조 분쟁 직후에 잦아 의결권 분석이 가장 필요한 국면과 겹친다.

그래서 문구가 아니라 **호출**을 센다.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent / "open_proxy_mcp"
HELPERS = {"company_not_found_warning", "company_ambiguous_warning"}

#: 회사를 해석하지만 not-found 안내를 만들 자리가 아닌 곳. **줄이는 방향으로만** 바뀐다.
#: 여기 넣을 때는 왜 아닌지를 한 줄로 적는다 — 나중 사람이 판단을 되짚을 수 있게.
EXEMPT = {
    "services/company.py": "정본이 사는 곳",
    "services/director_evaluation.py": "후보 평가 chain — 회사는 상위에서 이미 확정된다",
    "tools/__init__.py": "docstring 안 설명 문구일 뿐 호출부가 아니다",
}


def _modules():
    for p in sorted(ROOT.rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        try:
            yield rel, p, ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue


def _calls(tree) -> set[str]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def test_every_company_resolving_module_uses_the_canonical_hint():
    """`resolve_company_query` 를 부르면 못 찾았을 때의 안내도 정본으로 준다."""
    bad = []
    for rel, _p, tree in _modules():
        calls = _calls(tree)
        if "resolve_company_query" not in calls or rel in EXEMPT:
            continue
        if not (calls & HELPERS):
            bad.append(rel)
    assert not bad, (
        "회사를 해석하면서 못 찾았을 때의 안내를 정본으로 안 준다 — "
        f"{bad}. `company_not_found_warning`(없음) / `company_ambiguous_warning`(모호) 를 쓴다."
    )


def test_exemptions_are_real_modules():
    """면제 목록이 낡으면 이 테스트가 조용히 헐거워진다."""
    missing = [rel for rel in EXEMPT if not (ROOT / rel).exists()]
    assert not missing, f"면제 목록에 없는 파일이 있다: {missing}"

    for rel in EXEMPT:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        if rel == "tools/__init__.py":
            assert "resolve_company_query" not in _calls(tree), \
                "tools/__init__.py 가 실제로 회사를 해석하기 시작했다 — 면제를 거둬야 한다"


def test_ambiguous_hint_lists_the_candidates_it_has():
    """모호할 때 필요한 것은 탈출구가 아니라 **후보**다 — 손에 쥐고도 버리면 안 된다."""
    from open_proxy_mcp.services.company import company_ambiguous_warning

    msg = company_ambiguous_warning("금호", [
        {"corp_name": "금호타이어", "stock_code": "073240"},
        {"corp_name": "금호석유화학", "stock_code": "011780"},
    ])
    assert "금호타이어" in msg and "073240" in msg
    assert "금호석유화학" in msg

    many = company_ambiguous_warning("가", [{"corp_name": f"A{i}", "stock_code": f"{i:06d}"}
                                            for i in range(9)], limit=3)
    assert "외 6건" in many, "상한을 넘긴 후보 수를 밝힌다"

    assert "후보가 여럿" in company_ambiguous_warning("가", [])   # 후보가 비어도 뜻은 남는다
