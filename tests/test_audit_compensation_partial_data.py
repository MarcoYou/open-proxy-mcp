"""감사 보수한도 판단이 일부만 있는 자료에서도 터지지 않는다(260728 부실기업 검증 회귀).

옛 proxy_advise_before_meeting 출력 시험에서 서비스 판단 하나만 남겼다(2026-10-26 그 도구 은퇴).
"""


def test_audit_compensation_never_crashes_on_partial_data():
    """분기 9/10 은 둘 다 None 일 때만 잡아, 하나만 None 이면 포맷이 터졌다.
    260728 부실기업 검증에서 이오플로우·한국유니온제약이 도구 전체 크래시로 나왔다.
    """
    from open_proxy_mcp.services.proxy_advise import _decide_audit_compensation
    for comp in (
        {"audit_total_limit_krw": 100_000_000, "audit_count": None},   # 1인당 산출 불가
        {"audit_prior_limit_krw": None, "audit_total_limit_krw": 500_000_000, "audit_count": 2},
        {}, {"audit_count": 0}, {"audit_total_limit_krw": 0, "audit_count": 1},
    ):
        for fin in ({}, {"net_income_krw": -1}, {"capital_impairment_status": "full"}, None):
            d, r = _decide_audit_compensation(comp, fin or {})
            assert d in ("FOR", "AGAINST", "REVIEW", "NO_DATA"), (comp, fin, d)
            assert "None" not in r, (comp, fin, r)
