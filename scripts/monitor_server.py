"""OPM fly.io 서버 모니터링 — 트래픽 집계 + 최근 로그 트러블슈팅.

배포된 open-proxy-mcp 서버의 상태를 한 번에 본다.
- Prometheus 메트릭: 1h/24h/7d 요청수, 상태코드 분포, 머신별 분산
- 최근 로그 파싱: 인증방식(opendart=/api_key=)별 결과, 활성 사용자(키 마스킹),
  OAuth discovery probe(정상), 4xx/5xx·툴에러 등 트러블슈팅 플래그

사용법 (PowerShell):
    D:\\Projects\\open-proxy-mcp\\.venv\\Scripts\\python.exe scripts\\monitor_server.py
    ...                                       scripts\\monitor_server.py --logs        # 로그만
    ...                                       scripts\\monitor_server.py --metrics     # 메트릭만
    ...                                       scripts\\monitor_server.py --seconds 30  # 30초 라이브 캡처
fly CLI 로그인 상태여야 함(fly auth whoami). 토큰은 매 실행 시 readonly 1h 자동 발급.
"""
from __future__ import annotations
import argparse, json, re, ssl, subprocess, sys, urllib.error, urllib.parse, urllib.request
from collections import Counter, defaultdict

FLY = r"C:\Users\Owner\.fly\bin\fly.exe"
APP = "open-proxy-mcp"
ORG = "personal"
PROM = f"https://api.fly.io/prometheus/{ORG}/api/v1/query"
_ctx = ssl.create_default_context()


def _fly_token() -> str:
    out = subprocess.run(
        [FLY, "tokens", "create", "readonly", "-o", ORG, "--name", "opm-monitor", "-x", "1h"],
        capture_output=True, text=True,
    ).stdout
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("FlyV1"):
            return line
    raise SystemExit("fly readonly 토큰 발급 실패 — 'fly auth whoami'로 로그인 확인하세요.")


def _q(tok: str, promql: str):
    url = PROM + "?" + urllib.parse.urlencode({"query": promql})
    req = urllib.request.Request(url, headers={"Authorization": tok})
    try:
        with urllib.request.urlopen(req, context=_ctx, timeout=30) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  (메트릭 조회 실패 HTTP {e.code})"); return []
    except Exception as e:
        print(f"  (메트릭 조회 실패 {type(e).__name__})"); return []
    if d.get("status") != "success":
        return []
    return d["data"]["result"]


def _scalar(res):
    return round(float(res[0]["value"][1]), 0) if res else 0


def show_metrics(tok: str):
    print("=" * 64)
    print(f"📊 트래픽 메트릭  (app={APP})")
    print("=" * 64)
    f = lambda w: _scalar(_q(tok, f'sum(increase(fly_edge_http_responses_count{{app="{APP}"}}[{w}]))'))
    h1, h24, d7 = f("1h"), f("24h"), f("7d")
    print(f"  최근 1시간 : {int(h1):>6} 요청")
    print(f"  최근 24시간: {int(h24):>6} 요청   (시간당 평균 {h24/24:.0f})")
    print(f"  최근 7일   : {int(d7):>6} 요청   (일평균 {d7/7:.0f})")

    print("\n  [24h 상태코드 분포]")
    rows = _q(tok, f'sum by (status) (increase(fly_edge_http_responses_count{{app="{APP}"}}[24h]))')
    codes = {r["metric"].get("status", "?"): round(float(r["value"][1])) for r in rows}
    meaning = {"200": "정상", "202": "MCP 비동기 수락", "301": "리다이렉트",
               "400": "잘못된 요청(클라이언트)", "404": "없는 경로(OAuth probe 등)",
               "406": "Accept 불일치(OAuth probe 등)", "429": "rate limit", "500": "⚠️ 서버 에러",
               "502": "⚠️ 게이트웨이", "503": "⚠️ 다운"}
    for c in sorted(codes):
        flag = "  🚨" if c.startswith("5") else ""
        print(f"    {c}: {codes[c]:>5}  {meaning.get(c,'')}{flag}")
    err5 = sum(v for c, v in codes.items() if c.startswith("5"))
    ok = codes.get("200", 0) + codes.get("202", 0)
    print(f"\n  ✅ 정상(200+202): {ok}   🚨 서버에러(5xx): {err5}")

    print("\n  [24h 머신별 분산]")
    for r in _q(tok, f'sum by (instance) (increase(fly_app_http_responses_count{{app="{APP}"}}[24h]))'):
        print(f"    {r['metric'].get('instance','?')}: {round(float(r['value'][1]))}")


_RE_REQ = re.compile(r'INFO:\s+[\d.]+:\d+ - "(\w+) (\S+) HTTP/[\d.]+" (\d{3})')
_RE_KEY = re.compile(r'(opendart|api_key)=([a-f0-9]+)')


def capture_logs(seconds: int) -> list[str]:
    cmd = [FLY, "logs", "-a", APP, "--no-tail"]
    timeout = seconds if seconds else 20
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout).stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout or b""
    text = out.decode("utf-8", errors="replace") if isinstance(out, (bytes, bytearray)) else (out or "")
    return re.sub(r"\x1b\[[0-9;]*m", "", text).splitlines()


def show_logs(seconds: int):
    print("\n" + "=" * 64)
    print(f"🔎 최근 로그 트러블슈팅  ({'live ' + str(seconds) + 's' if seconds else '최근 버퍼'})")
    print("=" * 64)
    lines = capture_logs(seconds)
    reqs = []  # (method, path, code, auth_name, key)
    for ln in lines:
        m = _RE_REQ.search(ln)
        if not m:
            continue
        method, path, code = m.group(1), m.group(2), m.group(3)
        km = _RE_KEY.search(path)
        auth = km.group(1) if km else ("none" if path.startswith("/mcp") else "-")
        key = km.group(2) if km else None
        reqs.append((method, path, code, auth, key))

    if not reqs:
        print("  (이 창에서 인바운드 요청 라인 없음 — 잠잠하거나 더 길게 --seconds 로 캡처)")
    else:
        print(f"  인바운드 요청 {len(reqs)}건")
        print("\n  [인증방식 × 결과코드]")
        by = Counter((a, c) for _, _, c, a, _ in reqs)
        for (a, c), n in sorted(by.items(), key=lambda x: -x[1]):
            print(f"    {a:<9} {c}: {n}")

        # 활성 사용자(키 마스킹) + 각자 결과
        users = defaultdict(Counter)
        for _, _, c, a, k in reqs:
            if k:
                users[(a, k[:6] + '…')][c] += 1
        if users:
            print("\n  [활성 사용자(키 마스킹) → 결과코드]")
            for (a, mk), cc in users.items():
                codes = " ".join(f"{c}×{n}" for c, n in cc.items())
                allbad = all(c[0] in "45" for c in cc)
                flag = "  🚩 전부 실패" if allbad else ""
                print(f"    {a}={mk}: {codes}{flag}")

    # OAuth discovery probe (정상)
    oauth = sum(1 for _, p, _, _, _ in reqs if ".well-known" in p)
    # 트러블슈팅 플래그
    print("\n  [트러블슈팅 플래그]")
    flags = []
    api_key_users = {k[:6] + '…' for _, _, _, a, k in reqs if a == "api_key" and k}
    if api_key_users:
        flags.append(f"⚠️ api_key= 로 붙는 클라이언트 {len(api_key_users)}명 {sorted(api_key_users)} "
                     f"— CallTool 시 키 미인식으로 그 사용자만 실패할 수 있음 (정상은 ?opendart=)")
    err5 = [c for _, _, c, _, _ in reqs if c.startswith("5")]
    if err5:
        flags.append(f"🚨 5xx 서버에러 {len(err5)}건 — 로그 원문 확인 필요")
    tool_err = [ln for ln in lines if re.search(r"ValueError|설정되어 있지|Traceback", ln)]
    if tool_err:
        flags.append(f"🚨 툴/예외 로그 {len(tool_err)}건:")
    if oauth:
        flags.append(f"ℹ️ OAuth discovery probe {oauth}건 (404/406) — MCP 클라이언트 표준 동작, 정상")
    if not flags:
        print("    ✅ 특이사항 없음")
    for fl in flags:
        print("    " + fl)
    for ln in tool_err[:5]:
        print("      | " + ln[-160:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", action="store_true", help="메트릭만")
    ap.add_argument("--logs", action="store_true", help="로그만")
    ap.add_argument("--seconds", type=int, default=0, help="라이브 로그 캡처 초(기본: 최근 버퍼)")
    args = ap.parse_args()
    do_metrics = args.metrics or not args.logs
    do_logs = args.logs or not args.metrics
    if do_metrics:
        tok = _fly_token()
        show_metrics(tok)
    if do_logs:
        show_logs(args.seconds)
    print("\n💡 그래프로 보려면:  fly dashboard metrics -a open-proxy-mcp")
    print("💡 실시간 로그:        fly logs -a open-proxy-mcp")


if __name__ == "__main__":
    main()
