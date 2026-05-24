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

    assert nodes[1]["agenda_relation_type"] == "alternative"
    assert "alternative_title" in nodes[1]["agenda_relation_reasons"]
    assert nodes[1]["proposer_type"] == "shareholder"

    assert nodes[2]["agenda_relation_type"] == "conditional"
    assert "conditional_field" in nodes[2]["agenda_relation_reasons"]
