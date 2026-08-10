# -*- coding: utf-8 -*-
"""**법문 표기로 쓰면 자동 찬성이 나가던 것.** network 0콜.

260811 실측: `_classify_agenda` 가 「주식교환」 붙임표기만 봐서, 상법 §360-2 가 쓰는 정식
명칭 **「주식의 포괄적 교환」**이 `other` 로 떨어졌다. `other` 의 기본 분기는 FOR 다 —
회사가 통째로 자회사가 되는 안건에 **아무 검토 없이 찬성**이 나갔다는 뜻이다.
같은 이유로 위험 키워드 `"전환사채발행"`(공백 없음)은 실제 공고 표기 「전환사채 **발행**의 건」에
안 걸려 사실상 사문이었다.

「합병」은 잡히고 같은 급의 포괄적 교환은 안 잡히는 것 — 그게 이 결함의 모양이었다.
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.services.proxy_advise import _classify_agenda


@pytest.mark.parametrize("title", [
    "주식의 포괄적 교환 승인의 건",       # 상법 §360-2 법문 표기
    "주식의 포괄적 이전 승인의 건",       # §360-15
    "주식교환 계약서 승인의 건",          # 붙임표기 (종전에도 잡히던 것)
    "회사 합병 승인의 건",
    "영업 양수의 건",                    # 공백 표기
    "부의안건 : 포괄적 주식교환 승인의 건",  # 실측 임시주총 단일안건(동원산업)
])
def test_reorganisation_agendas_are_never_auto_for(title):
    """조직재편은 매수청구권·특별결의가 걸리는 안건이다. `other` 로 떨어지면 자동 FOR 다."""
    assert _classify_agenda(title) == "merger_or_restructuring", title


def test_ordinary_agendas_are_not_swept_in():
    """정규화를 넓히면 엉뚱한 안건을 끌어올 수 있다 — 그러면 정상 판정을 잃는다."""
    assert _classify_agenda("재무제표 승인의 건") == "financial_statements"
    assert _classify_agenda("이사 3인 선임의 건") == "director_election"
    assert _classify_agenda("정관 일부 변경의 건") == "articles_amendment"


@pytest.mark.parametrize("title", [
    "전환사채 발행의 건",
    "신주인수권부사채 발행의 건",
    "제3자배정 유상증자의 건",
    "제 3 자 배정 전환사채 발행 승인의 건",
])
def test_dilutive_issuance_titles_carry_a_risk_signal(title):
    """희석·지분이동을 만드는 발행 안건은 검토 대상이다 — 제3자배정은 상법 §418② 의
    「경영상 목적」 요건이 걸리고 그 판단은 도구가 대신할 수 없다.

    분류는 `other` 로 남지만(전용 카테고리가 없다) **위험 키워드에 걸려 REVIEW** 로 가야 한다.
    여기서는 그 키워드가 공백 표기를 견디는지만 본다 — 종전 `"전환사채발행"` 은 못 견뎠다.
    """
    from open_proxy_mcp.services import proxy_advise as PA
    import re

    src = open(PA.__file__, encoding="utf-8").read()
    m = re.search(r"risk_keywords = \[(.*?)\]", src, re.S)
    kws = [k.strip().strip('"') for k in m.group(1).replace("\n", "").split(",") if k.strip()]
    flat = title.replace(" ", "")
    assert any(k.replace(" ", "") in flat for k in kws), f"{title} 이 위험 키워드에 안 걸린다"
