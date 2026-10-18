#!/usr/bin/env python3
"""텔레메트리 수집 상태를 읽기만 하는 운영 검사. 사용자별 데이터는 출력하지 않는다."""
import argparse
import json
import urllib.request


def evaluate(info):
    if not isinstance(info, dict) or not info.get("enabled"):
        return "CHECK_ERROR", "수집 상태가 없거나 기록기가 꺼져 있음"
    if info.get("basis") != "tool_calls.v1":
        return "CHECK_ERROR", "수집 정의가 기대한 버전과 다름"
    if info.get("pending") is None or info.get("oldest_pending_age_s") is None:
        return "CHECK_ERROR", "송신함을 읽지 못함"
    if info.get("quarantined"):
        return "ATTENTION_NEEDED", "중앙 DB가 거절한 행이 격리됨 — 원인 확인 후 requeue"
    if info.get("status") == "degraded" or info.get("record_failures", 0):
        return "ATTENTION_NEEDED", "로컬 기록 실패 또는 송신 적체"
    return "OK", "수집 중"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://open-proxy-mcp.fly.dev/health")
    args = parser.parse_args()
    try:
        with urllib.request.urlopen(args.url, timeout=20) as response:
            info = json.load(response).get("usage")
        status, detail = evaluate(info)
    except Exception as exc:
        status, detail = "CHECK_ERROR", f"상태 조회 실패 ({type(exc).__name__})"
    print(f"{status}: {detail}")
    raise SystemExit(0 if status == "OK" else 1)


if __name__ == "__main__":
    main()
