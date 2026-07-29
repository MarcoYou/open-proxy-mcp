"""director_segment_signal 매핑·시계열 단위 테스트 (network 0콜).

260723 Phase 1: 부문장 출신 사내이사 커리어 → 담당부문 보수적 매핑.
오매핑(엉뚱한 부문 실적 노출)은 miss보다 나쁘다 — ambiguous/타사/전사형은 전부 skip이 정답.
"""

from open_proxy_mcp.services.director_segment_signal import (
    build_segment_series,
    candidate_career_texts,
    extract_segment_items,
    has_division_career,
    map_candidate_to_segment,
)

SEGS = ["석유화학", "첨단소재", "생명과학", "에너지솔루션", "공통 및 기타"]


def _cand(main_job: str, careers: list[tuple[str, str]] | None = None) -> dict:
    """(기간, 경력 원문) 목록 — 260730 부터 쪼갠 그룹이 아니라 소집공고 표 원문을 쓴다."""
    return {"faithfulness": {
        "main_job": main_job,
        "career_raw": [{"period": p, "content": c} for p, c in (careers or [])]}}


class TestMapping:
    def test_division_head_maps_to_single_segment(self):
        # 260723 LG화학 김동춘 실측 케이스 — 첨단소재 라인 재직
        cand = _cand(
            "(주)LG화학 CEO 겸 첨단소재사업본부장 사장",
            [("2026~현재", "(주)LG화학 CEO 겸 첨단소재사업본부장 사장"),
             ("2025~2025", "(주)LG화학 첨단소재사업본부장 부사장"),
             ("2023~2024", "(주)LG화학 전자소재사업부장 전무")],
        )
        m = map_candidate_to_segment(cand, SEGS, "LG화학")
        assert m["status"] == "mapped"
        assert m["segment"] == "첨단소재"
        assert "첨단소재" in m["matched_from"]

    def test_company_wide_ceo_skipped(self):
        # 전사 경영(부문장류 키워드 없음) → 매핑 시도 자체 skip
        cand = _cand("(주)LG화학 대표이사 부회장",
                     [("2019~현재", "(주)LG화학 대표이사 부회장")])
        assert map_candidate_to_segment(cand, SEGS, "LG화학")["status"] == "no_division_career"

    def test_multi_segment_career_is_ambiguous(self):
        # 복수 부문 이력 → 어느 부문 실적을 붙일지 단정 불가 → skip
        cand = _cand("(주)LG화학 석유화학사업본부장",
                     [("2020~2022", "(주)LG화학 첨단소재사업본부장")])
        m = map_candidate_to_segment(cand, SEGS, "LG화학")
        assert m["status"] == "ambiguous"
        assert set(m["candidates"]) == {"석유화학", "첨단소재"}

    def test_other_company_division_not_mapped(self):
        # 타사 부문장 이력은 풀에서 제외 — 이 회사 부문으로 오매핑 금지
        cand = _cand("(주)LG화학 사장",
                     [("2015~2018", "삼성전자 반도체사업부장")])
        assert map_candidate_to_segment(cand, SEGS, "LG화학")["status"] == "no_division_career"

    def test_company_name_variant_falls_back_to_main_job(self):
        # 엘지화학 vs LG화학 표기 변형 → 회사명 매칭 0건 → main_job fallback
        cand = _cand("(주)엘지화학 첨단소재사업본부장",
                     [("2024~현재", "(주)엘지화학 첨단소재사업본부장")])
        texts = candidate_career_texts(cand, "LG화학")
        assert texts == ["(주)엘지화학 첨단소재사업본부장"]
        assert map_candidate_to_segment(cand, SEGS, "LG화학")["status"] == "mapped"

    def test_generic_segment_names_excluded(self):
        # '기타' 류 일반 부문명은 stopword — 오매칭 방지
        cand = _cand("(주)테스트 기타사업본부장",
                     [("2020~현재", "(주)테스트 기타사업본부장")])
        assert map_candidate_to_segment(cand, ["기타", "공통"], "테스트")["status"] == "no_match"


class TestFetchGate:
    def test_gate_true_when_any_division_career(self):
        cands = [
            _cand("(주)LG화학 대표이사"),
            _cand("(주)LG화학 첨단소재사업본부장"),
        ]
        assert has_division_career(cands) is True

    def test_gate_false_for_pure_executives(self):
        cands = [_cand("(주)LG화학 대표이사"), _cand("(주)LG화학 CFO 사장")]
        assert has_division_career(cands) is False


class TestSeries:
    def test_extract_only_high_confidence_ok(self):
        ok = {"data": {"segments": {"status": "OK", "items": [{"name": "첨단소재", "revenue": 100.0, "profit": 10.0}],
                                    "unit": "백만원", "revenue_metric": "매출액", "profit_metric": "영업이익"}}}
        needs_review = {"data": {"segments": {"status": "NEEDS_REVIEW", "segment_note_md": "..."}}}
        assert extract_segment_items(ok) is not None
        assert extract_segment_items(needs_review) is None  # 마크다운 폴백은 쓰지 않음
        assert extract_segment_items({}) is None

    def test_series_matches_by_normalized_name_and_skips_missing_years(self):
        def _payload(name, rev, prof):
            return {"data": {"segments": {"status": "OK", "unit": "백만원", "revenue_metric": "", "profit_metric": "",
                                          "items": [{"name": name, "revenue": rev, "profit": prof}]}}}
        yearly = {
            2023: _payload("첨단소재 부문", 100.0, 10.0),   # 표기 변형 — 정규화 동치 매칭
            2024: _payload("전지재료", 999.0, 99.0),        # 부문 재편으로 부재 → 해당 연도 skip
            2025: _payload("첨단소재", 120.0, 12.0),
        }
        series = build_segment_series(yearly, "첨단소재")
        assert [r["fy"] for r in series] == [2023, 2025]
        assert series[-1]["revenue"] == 120.0


# ── 260724 P1 회귀 고정 (fresh-eye 리뷰 잔여 처리) ──

class TestP1Fixes:
    def test_norm_seg_name_no_suffix_residue(self):
        """P1-1: '사업'을 먼저 지우면 '사업부' 제거가 dead code가 돼 '전지부' 잔여가 남았다."""
        from open_proxy_mcp.services.business_details import _norm_seg_name
        assert _norm_seg_name("전지사업부") == "전지"
        assert _norm_seg_name("반도체사업부") == "반도체"
        assert _norm_seg_name("첨단소재사업부문") == "첨단소재"
        assert _norm_seg_name("첨단소재") == "첨단소재"

    def test_division_head_maps_despite_saeopbu_naming(self):
        """P1-1의 실제 효과: '○○사업부' 공시 회사에서 매핑이 살아난다."""
        cand = _cand("(주)테스트 전지사업부장",
                     [{"company": "(주)테스트 전지사업부", "items": ["2024~현재 사업부장"]}])
        m = map_candidate_to_segment(cand, ["전지사업부", "소재사업부"], "테스트")
        assert m["status"] == "mapped"
        assert m["segment"] == "전지사업부"

    def test_series_equality_survives_suffix_variants(self):
        """P1-1: 연도 간 '전지사업부'↔'전지' 표기 변형이 정규화 동치로 이어진다."""
        def _payload(name):
            return {"data": {"segments": {"status": "OK", "unit": "백만원", "revenue_metric": "",
                                          "profit_metric": "", "items": [{"name": name, "revenue": 10.0, "profit": 1.0}]}}}
        series = build_segment_series({2024: _payload("전지사업부"), 2025: _payload("전지")}, "전지사업부")
        assert [r["fy"] for r in series] == [2024, 2025]

    def test_short_latin_segment_name_rejected(self):
        """P1-4: latin 2자('IT')는 'Digital'·'Security'에 substring 매치돼 오매핑을 낳는다."""
        cand = _cand("(주)테스트 Digital Security 사업본부장",
                     [{"company": "(주)테스트 Digital Security", "items": ["2024~현재 본부장"]}])
        assert map_candidate_to_segment(cand, ["IT사업부문"], "테스트")["status"] == "no_match"

    def test_pseudo_axis_segment_names_are_stopwords(self):
        """P1-4: 지역·기능 축 pseudo-부문(수출/내수/금융 등)은 매핑 대상에서 제외."""
        cand = _cand("(주)테스트 수출사업본부장",
                     [{"company": "(주)테스트 수출사업", "items": ["2024~현재 본부장"]}])
        assert map_candidate_to_segment(cand, ["수출", "내수"], "테스트")["status"] == "no_match"
