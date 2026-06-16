"""5% 대량보유보고서 '보고자/특별관계자 합계표' 파서.

대량보유상황보고서 본문의 합계표에서 **보고자 본인(self)** 지분과 **특별관계자(related)**
지분을 분리한다. majorstock API의 헤드라인 보유비율(stkrt)은 보고자 본인 + 특별관계자
**합산**이라, 합산을 단독 지분처럼 오독하거나(예: 얼라인 솔루엠 23.11% = 본인 5.33% +
전성호 등 공동보유) 공동보유 상대(명부상 최대주주)를 외부 세력으로 오분류하는 것을 막기 위해
ownership_structure / proxy_contest가 본인·특관 분해에 사용한다.

성능: 분쟁 유니버스 140사 전수 검증 — 표준 한국 보고서 94% 정합(불변식: 보고자+특관 합 ≈
헤드라인). 약식(기관 단순투자)은 합계표가 없어 no_table, 영문명 보고자 등 ~6%는 파싱 실패.
**호출부는 graceful fallback 필수**: 일반 format이 아니면 분해 결과를 쓰지 말고 현 라벨 유지.

검증 기록: wiki/lessons/holder-table-parser-260615.md
합계표 구조:
  주수 비율 보고자 [이름] [ID] …숫자… [합계주수] [비율] 특별관계자 [이름] [ID] … ※ 소유에 준하는
  ID = 생년월일 6자리 | 사업자번호(하이픈) | 법인·고유번호(하이픈 없는 5~13자리)
"""
from __future__ import annotations

import re
from typing import Any

# ID: 생년월일 6자리 | 사업자번호(하이픈) | 법인·고유번호(하이픈 없는 5~13자리, 이탄에쿼티
# 53541 / 백운조합 6758003138 실측). 이름엔 숫자가 없어 이름 뒤 첫 숫자런이 항상 ID.
_ID = r"(?:\d{3}-\d{2,3}-\d{4,5}|\d{5,13})"
# 한 행: [이름] [ID] [숫자/-/콤마 토큰들] → 마지막 비율(X.XX) 직전이 합계주수.
# 이름에 숫자 허용(non-greedy) — 펀드·조합명의 '제N호'가 잘려 "호"만 남던 문제 해결.
# ID는 5~13자리 연속수(또는 DOB 6/사업자 하이픈)라 '제1호'의 단자리 숫자엔 매칭 안 돼 안전.
# 길이 상한 60 — 40이면 긴 영문 펀드명("Align Partners Capital Management Limited"=41자)이
# 상한 초과로 첫 글자가 잘려("lign…") 매칭되던 문제 해결.
_ROW = re.compile(
    r"([가-힣A-Za-z0-9()ㄱ-ㆎ·,.&\s]{1,60}?)\s+(" + _ID + r")\s+"
    r"((?:[\d,]+|-|0)(?:\s+(?:[\d,]+|-|0))*)\s+([\d,]+|-)\s+(\d+\.\d+|-)"
)


def _clean_name(raw: str) -> str:
    """이름 정제 — 잔여 헤더 토큰(주수/비율/보고자/특별관계자)·구두점 제거."""
    name = re.sub(r"\s+", " ", raw or "").strip(" ,.")
    name = re.sub(r"^(?:주수|비율|보고자|특별관계자)\s*", "", name).strip(" ,.")
    return name


def parse_holder_table(html: str) -> dict[str, Any] | None:
    """합계표를 파싱해 {format, self, related}를 반환.

    - format="일반": self={name, pct}, related=[{name, pct}, ...]
    - format="no_table": 약식(기관 단순투자 등) — 합계표 마커 자체가 없음(예상된 한계).
    - None: 합계표 마커는 있으나 행 파싱 실패.
    """
    if not html:
        return {"format": "no_table", "self": None, "related": []}
    flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    # 정정본: 합계표가 2번 → 마지막(정정후) 사용
    anchors = list(re.finditer(r"주수\s*비율\s*보고자", flat))
    if not anchors:
        # 합계표 마커 자체가 없음 = 약식(기관 단순투자 등) — 특별관계자 분해 대상 아님
        return {"format": "no_table", "self": None, "related": []}
    # 앵커(헤더) '다음'부터 파싱 — self 이름에 '주수 비율'이 섞이던 오염 제거
    start = anchors[-1].end()
    seg = flat[start: start + 4000]
    seg = re.split(r"※\s*소유에\s*준하는|제\d부\s|주\d\)", seg)[0]
    # 특별관계자 구분 라벨 제거 후 행 파싱
    seg2 = seg.replace("특별관계자", " ").replace("보고자", " ")
    holders: list[dict[str, Any]] = []
    for m in _ROW.finditer(seg2):
        name = _clean_name(m.group(1))
        pct_raw = m.group(5)  # 비율 (group4는 합계주수)
        pct = 0.0 if pct_raw == "-" else float(pct_raw)
        if name:
            holders.append({"name": name, "pct": pct})
    if not holders:
        return None
    return {"format": "일반", "self": holders[0], "related": holders[1:]}


def holder_table_total(parsed: dict[str, Any] | None) -> float | None:
    """보고자 본인 + 특별관계자 비율 합 (불변식 검증용 — 헤드라인 보유비율과 대조)."""
    if not parsed or parsed.get("format") != "일반" or not parsed.get("self"):
        return None
    total = (parsed["self"].get("pct") or 0.0)
    for r in parsed.get("related", []):
        total += r.get("pct") or 0.0
    return round(total, 2)
