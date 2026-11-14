"""투자판단 관련 주요경영사항 — 끝 칸 이름·번호가 유가와 코스닥이 다르다(2026-11-14 한 달 표본: 코스닥 57건 내용 0건 → 57건)."""
from open_proxy_mcp.services.exchange_actions import parse_investment_judgment

# 파로스아이바이오 20261112900958 의 뼈대(코스닥) — 내용 안에 「3. 변경신청 사유」가 있고 끝 칸이 「4. 사실발생(확인)일」.
KOSDAQ = ("투자판단 관련 주요경영사항(임상시험계획 변경승인신청) 1. 제목 PHI-501 제1상 임상시험계획(IND) 변경승인신청 "
          "2. 주요내용 1) 임상시험명칭 진행성 고형암 환자를 대상으로 한 제1상 임상시험 2) 임상시험단계 제1상 "
          "3) 임상시험승인기관 대한민국 식품의약품안전처(MFDS) 3. 변경신청 사유 시험대상자 투약 순응도 평가방법을 보완하고자 함. "
          "4. 사실발생(확인)일 2026-11-12 5. 기타 투자판단과 관련한 중요사항 - 상기 4. 사실발생(확인)일은 신청서를 제출한 날짜입니다.")
# 셀트리온 20261102800302 의 뼈대(유가).
KOSPI = ("투자판단 관련 주요경영사항 1. 제목 CT-P13 SC 유럽 임상 3상 시험계획 승인 2. 주요내용 1) 임상시험명칭 류마티스 관절염 환자 대상 "
         "제3상 임상시험 2) 임상시험단계 유럽 임상 3상 3. 이사회결의일(결정일) 또는 사실확인일 2026-11-02 "
         "4. 기타 투자판단과 관련한 중요사항 - 상기 사실확인일은 승인 통지를 받은 날입니다.")


def _parse(text):
    return parse_investment_judgment(text, [text], "기타")


def test_kosdaq_form_reads_content_and_event_date():
    got = _parse(KOSDAQ)
    assert got["decided_on"] == "2026-11-12"
    body = " ".join(got["content"])
    # 내용 안의 「3. 변경신청 사유」에서 끊기지 않는다(줄은 기존 규칙대로 90자에서 잘린다 — 전문은 원문 발췌에 있다)
    assert "임상시험단계 제1상" in body and "3. 변경신" in body
    assert "기타 투자판단과 관련한" not in body                          # 끝 칸에서 멈춘다


def test_kospi_form_is_unchanged():
    got = _parse(KOSPI)
    assert got["decided_on"] == "2026-11-02" and "유럽 임상 3상" in " ".join(got["content"])
    assert "기타 투자판단과 관련한" not in " ".join(got["content"])
