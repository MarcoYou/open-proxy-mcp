"""후보 부정 뉴스 키워드 — 파일에서 읽고, 한국어 오탐을 막아 걸러낸다.

**키워드는 코드가 아니라 파일에 있다** (`data/news/director_news_keywords.json`).
분류·심각도·조건을 그 파일에서 고치면 되고, 여기는 읽고 판정하는 규칙만 둔다.

한국어는 부분일치가 위험하다 — 「심사기구」에 「사기」가 들어 있다(260820 실측).
그래서 `ambiguous` 표시가 붙은 말은 **앞 글자가 한글이면 무시**하고,
`with` 가 붙은 말은 **같은 글 안에 동반어가 있어야** 인정한다.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "news", "director_news_keywords.json")
SEVERITY_ORDER = {"low": 0, "mid": 1, "high": 2}


@lru_cache(maxsize=1)
def load_keywords() -> dict[str, Any]:
    with open(_PATH, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _compiled() -> list[dict[str, Any]]:
    out = []
    for cat, block in load_keywords()["categories"].items():
        for kw in block["keywords"]:
            w = kw["w"]
            pat = rf"(?<![가-힣]){re.escape(w)}" if kw.get("ambiguous") else re.escape(w)
            out.append({
                "w": w, "cat": cat, "label": block["label"],
                "severity": kw.get("severity", "mid"),
                "with": tuple(kw.get("with") or ()),
                "re": re.compile(pat),
            })
    return out


def categories() -> dict[str, str]:
    return {c: b["label"] for c, b in load_keywords()["categories"].items()}


def match(text: str, cats: tuple[str, ...] = (), min_severity: str = "low",
          extra: tuple[str, ...] = (), exclude: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """걸린 말을 [{w, cat, label, severity}] 로 준다. 안 걸리면 빈 목록."""
    floor = SEVERITY_ORDER.get(min_severity, 0)
    hits = []
    for k in _compiled():
        if cats and k["cat"] not in cats:
            continue
        if k["w"] in exclude:
            continue
        if SEVERITY_ORDER[k["severity"]] < floor:
            continue
        if not k["re"].search(text):
            continue
        if k["with"] and not any(c in text for c in k["with"]):
            continue          # 동반어가 없으면 다른 맥락이다
        hits.append({"w": k["w"], "cat": k["cat"], "label": k["label"],
                     "severity": k["severity"]})
    for w in extra:            # 사용자가 그때그때 더 넣은 말 — 조건 없이 부분일치
        if w and w in text and w not in exclude:
            hits.append({"w": w, "cat": "custom", "label": "직접 지정", "severity": "mid"})
    hits.sort(key=lambda h: -SEVERITY_ORDER[h["severity"]])
    return hits


def worst(hits: list[dict[str, Any]]) -> str:
    return max((h["severity"] for h in hits), key=lambda s: SEVERITY_ORDER[s], default="")
