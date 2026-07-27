#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""문서 캐시를 스캔해 표본 레지스트리를 생성·갱신한다. DART 호출 0.

표본을 새로 수집한 뒤 이걸 돌려 레지스트리를 갱신하고 커밋한다.
원문은 저장소에 넣지 않는다 — 목록만 둔다. 레지스트리는 private 저장소에 있다.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 표본 목록은 private 저장소에 둔다 — 어떤 회사를 언제 조사했는지가 드러나므로 public 금지.
# 경로는 환경변수로 덮어쓸 수 있다: OPM_SAMPLE_REGISTRY
REGISTRY = Path(os.environ.get("OPM_SAMPLE_REGISTRY")
                or ROOT.parent / "open-proxy-storage" / "samples" / "registry.json")


def cache_dir() -> Path:
    return Path(os.environ.get("OPM_CACHE_DIR") or (Path(tempfile.gettempdir()) / "opm_cache"))


def tag(html: str, name: str) -> str:
    m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", html, re.S)
    return re.sub(r"<[^>]+>|\s+", "", m.group(1)) if m else ""


def classify(doc_name: str) -> str:
    for k in ("사업보고서", "분기보고서", "반기보고서"):
        if k in doc_name:
            return k
    if "소집공고" in doc_name:
        return "소집공고"
    return "지배구조·주총결과 등(웹HTML)"


def main() -> None:
    cd = cache_dir()
    rows = []
    for fn in sorted(glob.glob(str(cd / "*.json"))):
        try:
            html = json.loads(Path(fn).read_text(encoding="utf-8")).get("html", "")
        except Exception:
            continue
        if not html:
            continue
        doc = tag(html, "DOCUMENT-NAME") or "(웹HTML)"
        rows.append({
            "rcept_no": Path(fn).stem[:14],
            "kind": classify(doc),
            "company": tag(html, "COMPANY-NAME") or None,
            "form": tag(html, "FORMULA-VERSION") or None,
            "chars": len(html),
            "sha1_16": hashlib.sha1(html.encode()).hexdigest()[:16],
        })
    if not rows:
        sys.exit(f"캐시가 비어 있습니다: {cd}\n먼저 표본을 수집하거나 restore_samples.py 를 돌리세요.")
    counts = Counter(r["kind"] for r in rows)
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps({
        "generated_note": "DART 원문은 저장하지 않는다 — rcept_no 로 재수집한다(scripts/restore_samples.py).",
        "counts": dict(counts), "total": len(rows), "samples": rows,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    # 레지스트리는 보통 private 저장소(레포 밖)에 있으므로 relative_to 를 쓰면 깨진다.
    try:
        where = REGISTRY.relative_to(ROOT)
    except ValueError:
        where = REGISTRY
    print(f"레지스트리 {len(rows)}건 → {where}")
    for k, v in counts.most_common():
        print(f"  {v:>4}  {k}")


if __name__ == "__main__":
    main()
