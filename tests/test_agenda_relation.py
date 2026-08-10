import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services.shareholder_meeting import _agenda_nodes


def test_agenda_nodes_expose_relation_and_proposer_metadata():
    nodes = _agenda_nodes([
        {
            "number": "제3-1호",
            "title": "집중투표에 의하여 선임할 이사의 수 결정의 건",
            "source": None,
            "conditional": None,
            "children": [],
        },
        {
            "number": "제3-2호",
            "title": "이사 5인 선임의 건",
            "source": "주주제안",
            "conditional": None,
            "children": [],
        },
        {
            "number": "제3-3호",
            "title": "감사위원회 위원이 되는 사외이사 선임의 건",
            "source": None,
            "conditional": "제2-8호 의안이 가결될 경우에만 상정",
            "children": [],
        },
    ])

    assert nodes[0]["agenda_relation_type"] == "procedural"
    assert "procedural_title" in nodes[0]["agenda_relation_reasons"]
    assert nodes[0]["proposer_type"] == "company"

    # 260810: 「선임할 이사의 수 결정의 건」이 형제로 있으므로 이 선거는 **그 결과에 걸린다**.
    # 종전엔 `_AGENDA_ALTERNATIVE_PATTERNS` 의 `"5인 선임"` 리터럴 덕에 alternative 로 잡혔는데,
    # 그건 고려아연 한 회사만 맞히는 임시방편이었다(4인/7인이면 뚫린다). 지금은 인원이 아니라
    # **구조**로 본다 — 선행 트리거가 있으면 conditional 이 더 정확한 표기이기도 하다.
    assert nodes[1]["agenda_relation_type"] == "conditional"
    assert "seat_count_trigger_sibling" in nodes[1]["agenda_relation_reasons"]
    # `alternative_title` 은 **제목이 스스로** 「대안·택일·상호배타」라고 말할 때만 붙는다.
    # 「이사 5인 선임의 건」은 그런 말을 안 한다 — 종전에 붙던 건 인원 리터럴 때문이었다.
    assert "alternative_title" not in nodes[1]["agenda_relation_reasons"]
    assert nodes[1]["proposer_type"] == "shareholder_proposal"

    assert nodes[2]["agenda_relation_type"] == "conditional"
    assert "conditional_field" in nodes[2]["agenda_relation_reasons"]
