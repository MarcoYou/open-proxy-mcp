# -*- coding: utf-8 -*-
"""후보 이름 ↔ 안건 제목 매칭 — **실패하면 관대해지는 구조**라 여기가 안전장치다. network 0콜.

매칭에 성공하면 그 후보의 개별 평가(결격 + **사외이사 독립성**)로 판정한다.
실패하면 「묶음 안건」 경로로 떨어지는데, 그 경로는 독립성 검증을 의도적으로 건너뛴다
(어느 후보의 문제인지 특정할 수 없어 개별 안건에서 보라는 취지).

그래서 이 매칭이 깨지면 판정이 **보수적이 아니라 관대**해진다 — 결격만 보고 찬성이 나간다.
파싱 실패가 「검토 필요」가 아니라 「찬성」으로 번역되는 자리이고, 실제 사고가 두 번 났다:
  260710  도진명  평가 이름에 영문이 병기돼 `nm in title` 이 False
  260814  김 도 형  **제목 쪽 자간 벌림**으로 False (캐시 583건 중 2건)
"""
from __future__ import annotations

from open_proxy_mcp.services.proxy_advise import _core_person_name


def _matches(nm: str, title: str) -> bool:
    """proxy_advise 판정 루프의 매칭 조건과 동일한 식.

    조건을 여기 **복사**하면 이중장부가 되지만, 원본이 4,000줄짜리 함수 안의 인라인
    표현식이라 import 할 수 없다. 원본을 고치면 이 식도 같이 고쳐야 한다 —
    그걸 잊으면 아래 케이스가 통과하는데 프로덕션은 깨진 상태가 된다.
    """
    t_ns = title.replace(" ", "")
    return (nm in title or _core_person_name(nm) in title
            or nm.replace(" ", "") in t_ns
            or _core_person_name(nm).replace(" ", "") in t_ns)


def test_spaced_out_name_in_title_still_matches():
    """**260814 실측.** 공고가 이름 자간을 벌려 쓴다 — 같은 이름인데 매칭이 깨졌다."""
    assert _matches("김도형", "사외이사 김 도 형 선임의 건")
    assert _matches("김호", "사외이사 김 호 선임의 건")


def test_english_alias_in_eval_name_still_matches():
    """260710 도진명 사고 — 평가 이름 쪽에 영문이 병기된 형태."""
    assert _matches("도진명 (Jim Myong Doh)", "사외이사 도진명 선임의 건")


def test_common_title_shapes_match():
    """실측에서 걷힌 표기 변형들 — 괄호·어순이 달라도 이름만 있으면 잡혀야 한다."""
    assert _matches("이한수", "감사 (이한수) 재선임의 건")
    assert _matches("이훈복", "(사외)이사 후보자 이훈복 선임의 건")
    assert _matches("김경수", "사외이사 1인 선임의 건 (김경수)")
    assert _matches("강일규", "강일규 (사외이사)")


def test_different_person_does_not_match():
    """공백 무시가 **다른 사람**을 끌어오면 안 된다 — 관대해지는 방향의 오탐."""
    assert not _matches("김도형", "사외이사 박도형 선임의 건")
    assert not _matches("이한수", "사외이사 김도형 선임의 건")


def test_bundle_title_matches_nobody():
    """묶음 안건에는 이름이 없다 — 아무도 안 걸려야 묶음 경로로 정상 진입한다."""
    for nm in ("김도형", "이한수", "강일규"):
        assert not _matches(nm, "이사 선임의 건")
        assert not _matches(nm, "감사위원회 위원이 되는 사외이사 선임의 건")
