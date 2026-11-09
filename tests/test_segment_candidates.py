"""부문표 후보의 저비용 사전 검사가 실제 부문명을 놓치지 않는지 검사한다."""
import pytest

from open_proxy_mcp.services.segment_candidates import _specific_seg_names, find_segment_candidates


@pytest.mark.parametrize(("text", "expected"), [
    ("매출액 12,345 한국 북미", set()),
    ("가" * 10000 + "수익", set()),
    ("사업부문 영업부문 기타부문", set()),
    ("아미노산부문 원료의약품부문", {"아미노산부문", "원료의약품부문"}),
    ("반도체부문 반도체부문 Battery부문", {"반도체부문", "Battery부문"}),
    ("식품 부문\n의약품 부 문", set()),
    ("<TE>식품부문</TE><TE>의약품부문</TE>", {"식품부문", "의약품부문"}),
])
def test_specific_names_keep_literal_suffix_semantics(text, expected):
    assert _specific_seg_names(text) == expected


def test_metricless_table_with_two_specific_segments_is_still_returned():
    """매출액 라벨이 없어도 고유 부문명으로 회수하던 원문 표를 보존한다."""
    html = (
        "<TABLE><TR><TH>구분</TH><TH>당기</TH></TR>"
        "<TR><TE>아미노산부문</TE><TE>12,345</TE></TR>"
        "<TR><TE>원료의약품부문</TE><TE>23,456</TE></TR></TABLE>"
    )
    candidates = find_segment_candidates(html)
    assert candidates == [{
        "rendered": "구분 | 당기\n아미노산부문 | 12,345\n원료의약품부문 | 23,456",
        "score": 4, "rows": 3, "cols": 2,
    }]
