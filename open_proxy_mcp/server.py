"""OpenProxy MCP 서버 — MCPServer 진입점"""

import argparse
import hmac
import logging
import os
import re
import sys
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from open_proxy_mcp.capture import CaptureMiddleware
from open_proxy_mcp.prompts import register_all_prompts
from open_proxy_mcp.resources import register_all_resources
from open_proxy_mcp.tools import register_all_tools


#: 사용자 DART 키는 쿼리스트링(`?opendart=`)으로 들어온다. uvicorn 액세스 로그는 요청 라인을
#: 통째로 찍으므로 그대로 두면 배포 로그에 유저 키가 평문으로 쌓인다(260806 실측 확인).
#: 로그 자체는 운영 진단에 쓰이므로 끄지 않고 값만 가린다.
_API_KEY_IN_URL = re.compile(r"((?:opendart|crtfc_key)=)[^&\s\"']+")


class RedactApiKey(logging.Filter):
    """로그 레코드에서 URL 안의 API 키 값을 가린다. 메시지·인자 양쪽을 본다."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if isinstance(record.msg, str):
            record.msg = _API_KEY_IN_URL.sub(r"\1***", record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                _API_KEY_IN_URL.sub(r"\1***", a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


def install_api_key_redaction() -> None:
    """액세스 로그를 내보내는 로거 전부에 마스킹 필터를 건다.

    uvicorn 은 로거를 자체 설정으로 다시 세우므로 `uvicorn.run` **직전**에 걸어야 한다.
    필터는 핸들러가 아니라 로거에 달아, 핸들러가 나중에 바뀌어도 살아남게 한다.
    """
    redactor = RedactApiKey()
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactApiKey) for f in logger.filters):
            logger.addFilter(redactor)


def _opm_version() -> str:
    """**설치된 배포판**의 버전(pyproject 파일이 아니라 메타데이터). 못 읽으면 빈 문자열."""
    try:
        from importlib.metadata import version
        return version("open-proxy-mcp")
    except Exception:
        return ""


def build_mcp() -> MCPServer:
    """Build the single supported MCP tool surface."""
    # 이 이름이 클라이언트 커넥터 목록에 뜨고, MCP 양식(prompt)의 슬래시 명령
    # `/mcp__<서버이름>__<양식이름>` 가운데 자리에도 들어간다 — 짧을수록 부르기 쉽다.
    # fly 앱 이름(=URL `open-proxy-mcp.fly.dev`)과 레포명은 그대로 둔다.
    mcp = MCPServer(
        "openproxy",
        # 2.0 은 SDK 버전을 자동으로 안 채운다(기본값 ""). 빈 값보다는 **OPM 자신의 버전**이
        # 유용하다 — 클라이언트가 「어느 OPM 이 답했나」를 알 수 있다. 종전 1.x 는 여기에
        # SDK 버전(1.26.0 등)을 넣었는데, 그건 우리 릴리스와 무관한 값이었다.
        version=_opm_version(),
        # 여기엔 **도구를 가로지르는 규칙만** 둔다. 도구 하나로 표현되는 것은 그 도구의
        # description 에 있어야 한다(설명 총 23,673자가 이미 컨텍스트에 있다).
        # 실측값(후보 수·회사명·종목코드)은 절대 넣지 않는다 — 등록부가 바뀌면 조용히 썩는다.
        instructions=(
            "Korean-listed company disclosure (DART) analysis. Natural-language questions "
            "work — you don't need tool names. Answer in the user's language.\n\n"
            "Resolve a company once with `company` and pass the returned name, ticker, and "
            "corp_code downstream; otherwise every tool re-resolves the name.\n\n"
            "Read `status` and `warnings` before answering — they carry resolution confidence, "
            "missing filings, and basis fallbacks. State only figures that trace to a value in "
            "the response; you may compute from them if you say so. A value you did not get is "
            "\"not found in the filings read\" — never fill it from prior knowledge, never turn "
            "\"not found\" into \"there is none\".\n\n"
            "Figures carry their own basis (unit, currency, consolidated/separate, period, "
            "confirmed/provisional/restated). Keep it attached, and never place figures from "
            "different tools or periods side by side without saying the bases differ."
        ),
    )
    register_all_tools(mcp)
    register_all_prompts(mcp)
    register_all_resources(mcp)  # [실험] 공시 원문을 주소로 — 파싱이 약할 때 AI 가 직접 읽게

    # 헬스 엔드포인트 — 인증 없이 200 을 내는 유일한 경로.
    # 260729 사고: mcp 2.0.0 이 fastmcp 를 제거해 서버가 부팅 즉시 죽었는데, 헬스체크가 없어
    # fly 는 「VM 이 켜졌다」만 보고 배포를 성공 처리했고 CI 도 초록이었다.
    # **여기 안에 붙여야 한다** — `main()` 이 `build_mcp()` 로 새 인스턴스를 만들므로
    # 모듈 레벨 `mcp` 에 붙인 라우트는 실제 서빙되는 앱에 없다(260729 2차 실측: /health 404).
    # 260804 사고: fly 머신이 OOM(exit_code=137)으로 죽었다. 캐시 예산을 항목 수로 잡아 둔 게
    # 원인이었는데, 예산 점유를 밖에서 볼 방법이 없어 「죽고 나서야」 알았다. 이제 캐시 점유율과
    # evict 횟수를 헬스에 실어 예산이 차오르는 걸 미리 본다.
    @mcp.custom_route("/health", methods=["GET"])
    async def _health(_request):
        from starlette.responses import JSONResponse
        from open_proxy_mcp.dart.client import (cache_stats, client_registry_stats,
                                        doc_gate_stats, inflight_now, web_block_stats)
        from open_proxy_mcp.db import pool_stats
        # 260814: 법령 데이터가 통째로 비어도 응답이 평소와 같은 모양이라 **밖에서 안 보였다** —
        #   룰 40개가 0이 되면 강행규정 판정이 전부 사라지는데 경고도 신호도 없었다.
        #   여기 실어 배포 직후 눈으로 확인할 수 있게 한다. 0 이면 status 를 degraded 로 낮춘다.
        _data: dict[str, int] = {}
        try:
            from open_proxy_mcp.services.proxy_advise import (
                _load_law_layer_rules, _load_law_provisions,
            )
            from open_proxy_mcp.services.law_lookup import load_index
            _data["law_rules"] = len(_load_law_layer_rules())
            _data["law_provisions"] = len(_load_law_provisions())
            _data["law_corpus_articles"] = (load_index().get("meta") or {}).get("n_articles", 0)
        except Exception as exc:      # 헬스체크가 이것 때문에 죽으면 안 된다
            _data["error"] = str(exc)[:120]
        return JSONResponse({
            "status": "ok" if all(v for k, v in _data.items() if k != "error") else "degraded",
            "tools": len(await mcp.list_tools()),
            "data": _data,
            "cache": cache_stats(),
            # 풀이 실제로 서고 있나 · 대기가 쌓이나. 「빠르게 하려고 둔 것」이 조용히
            #   fail-open 으로 꺼져 있으면 숫자로만은 알 수 없어서 함께 낸다.
            "pg_pool": pool_stats(),
            # 어느 머신이 답했나 · 지금 몇 건이 돌고 있나 (260824).
            #   머신 ID 자체는 안 낸다 — 인프라 좌표는 private 이다. 짧은 해시로도
            #   「두 대가 실제로 갈라 받고 있나」는 답한다. 그 질문이 계기였다:
            #   동시성이 높았던 7월 네 구간 전부 **한 머신이 100%** 를 받고 있었고,
            #   fly 가 부하를 연결 수로 세는 한 그 편중은 로그로도 잘 안 보인다.
            "instance": _instance_tag(),
            "inflight": inflight_now(),
            # 260901: **캐시 예산과 VM 한도는 다른 자다.** 08:30 두 머신이 동시에
            #   exit_code=137·oom_killed 로 죽었는데, 그 직후 /health 의 캐시 점유는
            #   296MB 중 33% 였다 — 「멀쩡하다」로 읽힌다. OOM 은 캐시가 아니라
            #   **프로세스 전체 RSS** 가 VM 한도(1,024MB)에 닿아 난다. 그 값을 여기 싣는다.
            #   fly Prometheus 로도 볼 수 있지만 조직 토큰이 필요해 앱 범위 토큰으로는 401 이다.
            "mem": _mem_stats(),
            # 260901: 키별 DartClient 등록부. **여기가 유일하게 단조증가하던 자리**였다
            #   (개당 실측 908KB × 하루 고유 키 139개). 이제 상한·유휴 만료가 있고,
            #   그것이 실제로 도는지 밖에서 보이도록 개수·퇴출수를 싣는다.
            "clients": client_registry_stats(),
            # 260901: 문서 수신·파싱 동시 상한. 겹침이 곧 메모리 피크라
            #   「문이 실제로 좁혀져 있나」를 밖에서 볼 수 있어야 한다.
            "doc_gate": doc_gate_stats(),
            # 260906: 웹 차단 신호(403·429·차단 페이지). 차단은 IP 기준이라 그 머신의 사용자
            #   전원이 같이 막힌다 — 조용히 실패하게 두지 않고 밖에서 보이게 한다.
            "web_block": web_block_stats(),
        })

    # 캐시를 밖에서 비우고, **비워졌는지 같은 응답으로 확인**한다. (260901)
    #   왜 — 08:30 두 머신이 동시에 OOM 으로 죽었다. 그때 손으로 할 수 있는 게
    #   「머신을 재시작한다」뿐이었다. 그건 캐시만 비우는 게 아니라 서비스를 끊는다.
    #   🔴 **머신을 골라 부를 수 있어야 한다.** 두 대가 각자 제 캐시를 들고 있어서
    #   한 번 불러서는 한 대만 비워진다 — 부르는 쪽이 `Fly-Force-Instance-Id` 로
    #   지정한다(운영 스크립트는 레포 밖 — open-proxy-storage). 응답에 어느 머신이 답했는지 싣는 이유다.
    #   인증 — `OPM_ADMIN_KEY` 시크릿과 헤더가 같아야 한다. 시크릿이 없으면 **404 로
    #   숨긴다**(403 은 「여기 뭔가 있다」를 알려 준다).
    @mcp.custom_route("/admin/cache", methods=["POST"])
    async def _admin_cache(request):
        from starlette.responses import JSONResponse
        from open_proxy_mcp.dart.client import cache_clear

        want = os.environ.get("OPM_ADMIN_KEY")
        got = request.headers.get("x-admin-key")
        if not want or not got or not hmac.compare_digest(want, got):
            return JSONResponse({"error": "not found"}, status_code=404)
        disk = request.query_params.get("disk") == "1"
        # 260902: 「비웠는데 왜 안 줄어드나」를 여기서 가른다.
        #
        #   RSS 750MB 순간의 장부는 등록 캐시 0.0MB · 장부 밖 20MB 뿐이었다 —
        #   590MB 가 우리 장부에 없다. 두 가지 중 하나다:
        #     (가) 누가 큰 문자열을 아직 붙잡고 있다  → gc 로도 안 준다
        #     (나) 놓았는데 파이썬/ glibc 가 OS 에 안 돌려준다 → malloc_trim 이 준다
        #   그래서 **세 단계로 나눠 잰다.** 한 번에 다 하고 총계만 보면 어느 쪽인지
        #   영영 모른다. 이 세 값의 차이가 곧 진단이다.
        # `cache=0` — **비우지 않고 돌려주기만** 한다. 캐시를 비우면 다음 요청이 그만큼
        #   DART 를 다시 부르므로 공짜가 아니다. 260902 실측에서 gc 는 0MB, trim 은 56.5MB
        #   를 냈다 — 즉 **캐시를 건드리지 않고도 회수할 몫이 따로 있다.** 낮은 문턱에서는
        #   이쪽만 돌리고, 캐시는 정말 급할 때 비운다.
        do_cache = request.query_params.get("cache", "1") != "0"
        steps = {"start": _mem_stats()}
        result = cache_clear(disk=disk) if do_cache else {"skipped": "cache"}
        steps["after_cache"] = _mem_stats()

        import gc as _gc
        _gc.collect()
        steps["after_gc"] = _mem_stats()

        trim = None
        if request.query_params.get("trim", "1") != "0":
            try:
                import ctypes
                import ctypes.util
                _libc_path = ctypes.util.find_library("c") or "libc.so.6"
                _libc = ctypes.CDLL(_libc_path)
                # glibc 전용이다. musl(alpine)에는 없어 AttributeError 로 떨어진다 —
                #   그 사실을 응답에 남긴다. 「해 봤는데 안 줄었다」와 「못 했다」는 다르다.
                trim = {"called": True, "returned": int(_libc.malloc_trim(0))}
            except Exception as exc:            # noqa: BLE001
                trim = {"called": False, "error": f"{type(exc).__name__}: {exc}"}
        steps["after_trim"] = _mem_stats()

        def _drop(a, b):
            try:
                return round(a["rss_mb"] - b["rss_mb"], 1)
            except Exception:                   # noqa: BLE001
                return None

        return JSONResponse({
            "instance": _instance_tag(),
            "cleared": do_cache,
            "disk": disk,
            "steps": steps,
            "freed_mb": {
                "cache": _drop(steps["start"], steps["after_cache"]),
                "gc": _drop(steps["after_cache"], steps["after_gc"]),
                "trim": _drop(steps["after_gc"], steps["after_trim"]),
                "total": _drop(steps["start"], steps["after_trim"]),
            },
            "malloc_trim": trim,
            # 옛 이름 — 부르는 쪽(레포 밖 운영 스크립트 · 파수꾼)이 아직 쓴다.
            "mem_before": steps["start"],
            "mem_after": steps["after_trim"],
            **result,
        })

    # 무엇이 램을 채우나 — **장부 밖 저장소**를 이름으로 지목해 센다. (260901)
    #   10:49 실측: RSS 826MB 일 때 등록 캐시(`_CACHE_REGISTRY`) 를 전부 비워도 810MB 가
    #   남았다. 즉 자라는 곳이 장부 밖이다. 260824 에도 같은 병으로 184MB 가 관측 밖에
    #   있었다 — 그때는 나열에서 빠진 것이었고, 이번엔 아예 등록되지 않은 모듈 딕셔너리다.
    #
    #   🔴 **크기는 표본으로 어림한다.** 항목 수십만 개를 전수로 재면 그 계산 자체가
    #   요청 경로를 붙든다. 표본 20개의 평균 × 개수로 내고, `sampled` 를 함께 실어
    #   **어림값임이 드러나게** 한다. 정확도보다 「어느 방이 큰가」가 답할 질문이다.
    @mcp.custom_route("/admin/memtop", methods=["GET"])
    async def _admin_memtop(request):
        import gc
        import importlib
        import itertools
        from collections import Counter

        from starlette.responses import JSONResponse
        from open_proxy_mcp.dart.client import (cache_stats, client_registry_stats)

        def _deep(obj, cap=20000):
            """임의 객체의 대략 바이트. **`_cache_entry_bytes` 로는 못 잰다** —
            그건 dict·list·tuple 만 파고들어서, 260901 에 `_instances` 를 개당
            48바이트(실측 908KB)로 오보했다. 그 계측 때문에 「캐시 밖 810MB」의
            정체를 반나절 못 짚었다. 여기서는 `gc.get_referents` 로 실제 참조를
            따라간다. 무한히 퍼지지 않도록 방문 수를 `cap` 으로 자른다 —
            잘렸으면 그 사실을 함께 낸다(어림값을 정확값처럼 쓰지 않는다)."""
            seen, stack, total, n = set(), [obj], 0, 0
            while stack and n < cap:
                o = stack.pop()
                i = id(o)
                if i in seen:
                    continue
                seen.add(i)
                n += 1
                try:
                    total += sys.getsizeof(o)
                except Exception:
                    continue
                if isinstance(o, (str, bytes, bytearray, int, float, type(None))):
                    continue
                try:
                    stack.extend(gc.get_referents(o))
                except Exception:
                    pass
            return total, (n >= cap)

        want = os.environ.get("OPM_ADMIN_KEY")
        got = request.headers.get("x-admin-key")
        if not want or not got or not hmac.compare_digest(want, got):
            return JSONResponse({"error": "not found"}, status_code=404)

        # 장부 밖 후보 — 모듈 수준에 살면서 트래픽을 따라 자랄 수 있는 것들.
        targets = [
            ("open_proxy_mcp.services.financial_metrics", "_FM_CACHE"),
            ("open_proxy_mcp.services.trading", "_QUOTE_CACHE"),
            ("open_proxy_mcp.services.law_lookup", "_FULLTEXT_CACHE"),
            ("open_proxy_mcp.services.law_lookup", "_INDEX_CACHE"),
            ("open_proxy_mcp.services.shareholder_meeting_parser", "_LAST_CAREER_RAW"),
            ("open_proxy_mcp.dart.client", "_instances"),
            ("open_proxy_mcp.dart.fx", "_MEM"),
        ]
        stores = {}
        for mod, name in targets:
            try:
                obj = getattr(importlib.import_module(mod), name, None)
            except Exception as exc:
                stores[f"{mod.split('.')[-1]}.{name}"] = {"error": str(exc)[:80]}
                continue
            if obj is None:
                stores[f"{mod.split('.')[-1]}.{name}"] = {"entries": 0}
                continue
            try:
                n = len(obj)
            except Exception:
                n = None
            est, truncated = None, False
            if isinstance(obj, dict) and n:
                sample = list(itertools.islice(obj.items(), 5))
                per = 0.0
                for k, v in sample:
                    a, t1 = _deep(k)
                    b, t2 = _deep(v)
                    per += a + b
                    truncated = truncated or t1 or t2
                est = round(per / len(sample) * n / 1048576, 1)
            stores[f"{mod.split('.')[-1]}.{name}"] = {
                "entries": n, "est_mb": est, "sampled": min(5, n or 0),
                "truncated": truncated}

        counts = Counter(type(o).__name__ for o in gc.get_objects())
        return JSONResponse({
            "instance": _instance_tag(),
            "mem": _mem_stats(),
            "registry_mb": (cache_stats() or {}).get("_used_mb"),
            "clients": client_registry_stats(),
            "off_registry": stores,
            "gc": {"objects": sum(counts.values()),
                   "collected_now": gc.collect(),
                   "top_types": counts.most_common(15)},
        })

    return mcp


mcp = build_mcp()


# ── 서빙 설정과 미들웨어 ──────────────────────────────────────────────────────
# 이 아래는 전부 `main()` 안에 있었다. 그래서 **테스트가 프로덕션이 실제로 서빙하는 객체에
# 닿을 수 없었다** — 260729 2차 사고(모듈 레벨 인스턴스에 라우트를 붙였는데 서빙되는 건
# `main()` 이 만든 다른 인스턴스라 /health 가 404)와 같은 결함이다. 밖으로 꺼내
# `build_app()` 하나가 서빙 결정을 전부 들게 하고, `main()` 은 그걸 uvicorn 에 넘기기만 한다.


def _extract_tool(body: bytes):
    """JSON-RPC 본문에서 호출 대상 추출 → (이름, tools/call 여부).
    tools/call이면 tool명, 아니면 method명."""
    try:
        import json
        d = json.loads(body)
        method = d.get("method", "")
        if method == "tools/call":
            return d.get("params", {}).get("name") or "tools/call", True
        return method or None, False
    except Exception:
        return None, False


#: 응답 본문에서 「이 호출이 실패했나」를 읽는 패턴. **테스트는 이 상수를 import 해서
#: 실제 wire 바이트와 대조한다** — 테스트가 리터럴을 복사해 가지면, 서버가 눈이 먼 뒤에도
#: 테스트는 영원히 통과한다(매칭이 0건인 것과 「오류가 없었다」는 구분되지 않는다).
_ERR_PATTERNS = (b'"isError":true', b'"isError": true',
                 b'"error":{"code"', b'"error": {"code"')
#: **값이 아니라 필드가 보였나**를 따로 본다. 패턴이 하나도 안 맞는 것은 두 가지 뜻이다 —
#: 「오류가 없었다」와 「우리가 못 읽었다」. 종전에는 둘 다 is_error=False 로 적혀서,
#: 필드명이 바뀌면 **에러율이 영원히 0** 이 되고 아무 신호도 안 났다(260810 실측: 2.0 의
#: 파이썬 필드가 is_error 로 바뀐 걸 보고 wire 도 바뀐 줄 알고 스캐너를 고칠 뻔했다).
#: 이제 셋째 상태를 남긴다 — is_error=None + error_kind="unclassifiable".
#: 그 수가 늘면 에러율이 0으로 수렴하는 대신 「모르겠다」가 쌓여 눈에 띈다.
_ERR_FIELD = (b'"isError"', b'"is_error"', b'"error"')
_EKIND_RE = re.compile(rb"\[ekind=(\w+)\]")  # tool 래퍼가 붙인 error_kind 태그
#: **degrade 표지** — 상류(DART) 실패를 크래시 대신 정상 응답으로 낮춰 보낸 것(dart_safety).
#: 그 응답은 설계상 `# tool\n\n안내문` 이라 `isError` 가 없어, 이 표지가 없으면 스캐너가
#: **성공과 구분하지 못한다.** 260810 실측: 306,670행 중 오류로 적힌 것이 28건뿐이었는데
#: 진짜 오류가 28건이어서가 아니라 DART 실패가 전부 성공으로 세어졌기 때문이었다.
#: 오늘 넣은 3상태(unclassifiable)로도 못 잡힌다 — 스캐너가 눈이 먼 게 아니라 응답이
#: **진짜로 성공 모양**이라서다. 구멍이 스캐너보다 위에 있었다.
_DEGRADED_RE = re.compile(rb"\[degraded=(\w+)\]")   # 답을 못 줬다 → 실패로 센다
_NODATA_RE = re.compile(rb"\[nodata=(\w+)\]")       # 「자료 없음」은 답이다 → 성공, 다만 표시

#: 요청 본문에서 **도구 이름을 꺼낼 만큼만** 읽는다. JSON-RPC 는 method·params.name 이 앞에
#: 오므로 정상 요청은 수백 바이트면 충분하다(OPM 인자는 회사명·코드·연도다).
#: 종전에는 끝까지 다 모았고, 미들웨어가 라우터 **밖**에 있어 SDK 의 4 MiB 상한보다 먼저
#: 돌기 때문에 **32 MiB 를 통째로 메모리에 담은 뒤에야 413** 이 났다(실측, 1.29·2.0 동일).
#: 1 GB VM 에 OOM 이력(260804)이 있어 상한 없는 누적은 그대로 둘 수 없다.
#: 여기서 멈춰도 replay 가 나머지를 receive() 로 흘려보내므로 하류는 온전한 본문을 받는다.
_MAX_SNIFF_BYTES = 64 * 1024


def _mem_stats() -> dict:
    """프로세스·컨테이너의 실사용 메모리. **OOM 이 보는 자로 잰다.**

    cgroup v2 의 `memory.current` 가 커널이 한도와 견주는 값이고, `VmRSS` 는 이
    프로세스 몫이다. 둘을 같이 내야 「내가 먹었나, 옆이 먹었나」가 갈린다.
    리눅스 밖(로컬 개발)에서는 읽을 게 없으므로 빈 칸으로 둔다 — 0 으로 채우지 않는다.
    """
    out: dict = {}
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # 리눅스는 KB, macOS 는 바이트로 준다.
        out["peak_rss_mb"] = round(peak / (1024 if peak > 10 ** 7 else 1) / 1024, 1)
    except Exception:
        pass
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    out["rss_mb"] = round(int(line.split()[1]) / 1024, 1)
                    break
    except Exception:
        pass
    for name, key in (("/sys/fs/cgroup/memory.current", "cg_used_mb"),
                      ("/sys/fs/cgroup/memory.max", "cg_limit_mb"),
                      ("/sys/fs/cgroup/memory.peak", "cg_peak_mb")):
        try:
            v = open(name).read().strip()
            if v != "max":
                out[key] = round(int(v) / 1024 / 1024, 1)
        except Exception:
            pass
    if out.get("cg_used_mb") and out.get("cg_limit_mb"):
        out["cg_pct"] = round(out["cg_used_mb"] / out["cg_limit_mb"] * 100, 1)
    return out


def _instance_tag() -> str:
    """머신을 **구별만** 할 수 있는 짧은 표식. ID 원문은 내지 않는다."""
    import hashlib
    import os
    mid = os.environ.get("FLY_MACHINE_ID") or "local"
    return hashlib.sha256(mid.encode()).hexdigest()[:8]


class ApiKeyMiddleware:
    """URL 쿼리 파라미터 ?opendart=키 → contextvar 세팅 + 사용 통계 기록."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        from open_proxy_mcp import usage
        from open_proxy_mcp.dart.client import set_request_api_key

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from urllib.parse import parse_qs
        qs = parse_qs(scope.get("query_string", b"").decode())
        # 공백만 든 키는 **없는 것으로 친다.** 파이썬에서 " " 는 참이라 종전에는 게이트를
        # 통과했고(실측 `?opendart=%20` → 200), 하류의 `키 or os.getenv(...)` 폴백도 참이라
        # 서버 키로 넘어가지도 않은 채 **공백이 그대로 DART 키로 쓰였다**. 사용자는 401 힌트
        # 대신 원인 모를 상류 인증 실패를 받고, 통계엔 유령 사용자 해시가 잡히며,
        # 키로 캐싱되는 클라이언트 인스턴스가 하나씩 늘어난다.
        opendart = (qs.get("opendart", [None])[0] or "").strip() or None
        if opendart:
            set_request_api_key(opendart)
        elif scope.get("path", "").startswith("/mcp"):
            # 키 없는 서빙 요청 거절(260705) — fly secrets에 서버용 OPENDART 키(배치·DB
            # 갱신 내부용)가 있으므로, 거절하지 않으면 env 폴백으로 서버 키가 조용히
            # 소모된다. 서빙은 반드시 유저 키(?opendart=)로.
            import json as _json
            # 260901: 첫 만남에서 보는 문구다. **무엇이 없고 · 어디서 받고 · 어떻게 붙이는지**
            #   세 가지가 한 화면에 있어야 한다. 코드(`error`)는 그대로 두어 기계가 갈라 읽게 한다.
            body = _json.dumps({
                "error": "opendart API key required",
                "message": "DART 오픈API 키가 필요해요. 금융감독원이 무료로 발급해 주고, "
                           "신청하면 바로 받을 수 있어요.",
                "how_to": "① https://opendart.fss.or.kr 에서 인증키를 신청하세요(무료·즉시 발급) "
                          "② 받은 키를 접속 주소 뒤에 붙이면 됩니다 — ?opendart=<발급받은 키>",
                "why": "키는 사용자마다 따로 쓰입니다. 그래야 한 사람의 조회가 다른 사람의 "
                       "한도에 영향을 주지 않아요.",
            }, ensure_ascii=False).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # 사용 통계 기록 (요청 1건 = 이벤트 1건). 기록은 비동기 큐라 지연 0.
        # 요청 본문(JSON-RPC)을 버퍼링해 tool명 추출 후 그대로 앱에 재생(replay).
        if opendart and scope.get("path", "").startswith("/mcp"):
            import time as _t
            start = _t.monotonic()
            buffered = []
            body = b""
            more = True
            while more:
                msg = await receive()
                buffered.append(msg)
                if msg["type"] == "http.request":
                    body += msg.get("body", b"")
                    more = msg.get("more_body", False)
                    if len(body) >= _MAX_SNIFF_BYTES:
                        break     # 나머지는 replay 가 receive() 로 그대로 흘려보낸다
                else:
                    more = False
            tool, is_call = _extract_tool(body)

            # 장부를 **여기서** 만든다. 하류(캐시·회사해석)는 이 dict 를 고치기만 하고,
            # 우리는 같은 dict 를 들고 있으니 응답이 끝난 뒤 그대로 읽으면 된다.
            # 하류가 값을 올려보내게 하면 안 된다 — ContextVar 는 위로 안 흐른다.
            from open_proxy_mcp.dart.client import (
                new_request_ledger, ledger_enter, ledger_exit,
                ensure_lag_sampler, loop_lag_ms)
            ensure_lag_sampler()
            ledger = new_request_ledger()
            # **tools/call 만 센다.** streamable-http 클라이언트는 `GET /mcp` 로 스트림을
            #   열어 **세션 내내 붙들고 있다.** 그걸 함께 세면 「지금 CPU 를 다투는 요청 수」가
            #   아니라 「열려 있는 연결 수」가 되어, 64ms 짜리 호출도 inflight=12 로 적힌다.
            #   실측(260824 첫 배포): 기록 19건 **전부 6 이상**, 1 이 한 번도 안 나왔다.
            #   핸드셰이크(initialize·ping)도 뺀다 — 비용이 0 에 가까워 줄을 만들지 않는다.
            if is_call:
                ledger_enter(ledger)
            # 이 요청이 도는 동안 **프로세스 전체**가 쓴 CPU 시간. 이 요청 「자신의」 CPU 가
            # 아니다 — 단일 이벤트루프라 남의 코루틴이 태운 것도 여기 들어온다. 그게 노림수다:
            # 기다린 시간(네트워크)과 코어가 실제로 일한 시간을 가르는 것이 목적이지, 누가
            # 태웠는지를 가리는 건 `inflight_max` 가 한다. 둘을 함께 읽는 법은 client.py 참조.
            cpu0 = _t.process_time()
            # 이 요청이 도는 동안 **이벤트루프가 얼마나 밀렸나**. `cpu_ms` 가 낮은 요청이
            # 「네트워크를 기다린 것」인지 「CPU 차례를 못 받은 것」인지는 이 값이 가른다.
            lag0 = loop_lag_ms()

            idx = 0

            async def replay():
                nonlocal idx
                if idx < len(buffered):
                    m = buffered[idx]; idx += 1; return m
                return await receive()

            # tools/call은 응답 본문(SSE/JSON)에서 isError를 스캔한 뒤 본문 종료 시 기록.
            # (툴 내부 실패는 HTTP 200에 실려 오므로 status만으론 못 잡음)
            # 그 외(핸드셰이크 등)는 기존대로 응답 시작 시 기록.
            rec = {"status": 0, "latency": None, "err": False, "tail": b"",
                   "done": False, "ekind": None, "bytes": 0, "field_seen": False,
                   "degraded": None, "nodata": None}

            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    rec["latency"] = int((_t.monotonic() - start) * 1000)
                    rec["status"] = message.get("status", 0)
                    if not is_call:
                        usage.record(opendart, rec["status"], tool, rec["latency"])
                elif message["type"] == "http.response.body" and is_call and not rec["done"]:
                    chunk = message.get("body", b"") or b""
                    rec["bytes"] += len(chunk)     # 호출측이 무는 토큰 비용의 대리 지표
                    if chunk and (not rec["err"] or rec["ekind"] is None or not rec["field_seen"]):
                        hay = rec["tail"] + chunk
                        if not rec["err"]:
                            rec["err"] = any(p in hay for p in _ERR_PATTERNS)
                        if not rec["field_seen"]:
                            # 값이 아니라 **필드가 있었나**. 없으면 우리가 못 읽은 것이다.
                            rec["field_seen"] = any(p in hay for p in _ERR_FIELD)
                        if rec["ekind"] is None:
                            m = _EKIND_RE.search(hay)
                            if m:
                                rec["ekind"] = m.group(1).decode()
                        if rec["degraded"] is None:
                            m = _DEGRADED_RE.search(hay)
                            if m:
                                rec["degraded"] = m.group(1).decode()
                        if rec["nodata"] is None:
                            m = _NODATA_RE.search(hay)
                            if m:
                                rec["nodata"] = m.group(1).decode()
                        rec["tail"] = hay[-64:]  # 태그(~16B)가 청크 경계에 안 잘리게
                    if not message.get("more_body", False):
                        rec["done"] = True
                        # 세 상태로 적는다 — 「실패」·「성공」·**「모르겠다」**.
                        #   실패    err=True                → is_error=True. 태그 없는 오류
                        #           (인자검증·프로토콜·비래핑)는 "untagged" sentinel
                        #   성공    필드는 봤는데 값이 false → is_error=False
                        #   모르겠다 필드 자체를 못 봤다      → is_error=None + "unclassifiable"
                        # 셋째가 핵심이다. 종전엔 이것도 False 로 적혀서 **스캐너가 눈이 멀면
                        # 에러율이 조용히 0** 이 됐다. 이제 「모르겠다」가 쌓여 눈에 띈다
                        # (nullable 이라 WHERE is_error=true 집계의 분모에서도 빠진다).
                        #   상류실패 degrade 표지 → is_error=True + `dart_` 접두
                        #           (우리 크래시와 구분한다 — 대응이 다르다). 접두가 필요한 건
                        #           이름이 겹치기 때문이다 — `timeout` 은 우리 크래시 분류에도
                        #           degrade 분류에도 있다. 줄임말(`up_`)을 안 쓰는 이유는
                        #           **리포트에서 이 값을 읽는 사람이 물어보지 않아야** 해서다.
                        #   자료없음 nodata 표지        → is_error=False + kind 만 남김
                        if rec["degraded"]:
                            is_err, ekind = True, f"dart_{rec['degraded']}"
                        elif rec["err"]:
                            is_err, ekind = True, (rec["ekind"] or "untagged")
                        elif rec["nodata"]:
                            is_err, ekind = False, rec["nodata"]
                        elif rec["field_seen"]:
                            is_err, ekind = False, None
                        else:
                            is_err, ekind = None, "unclassifiable"
                        # 장부를 읽는다 — 우리가 만들어 내려보낸 그 dict 다.
                        # 문서를 안 받은 요청은 셋 다 0이라 분모에서 자연히 빠진다.
                        usage.record(opendart, rec["status"], tool, rec["latency"],
                                     is_error=is_err, error_kind=ekind,
                                     response_bytes=rec["bytes"],
                                     doc_mem_hits=ledger["doc_mem_hits"],
                                     doc_disk_hits=ledger["doc_disk_hits"],
                                     doc_misses=ledger["doc_misses"],
                                     corp_codes=ledger["corp_codes"],
                                     fetch_viewer=ledger["fetch_viewer"],
                                     fetch_kind=ledger["fetch_kind"],
                                     web_wait_ms=ledger["web_wait_ms"],
                                     # **방식만** 넘긴다 — 장부의 `query`(사용자 원문)와
                                     # `corp_name` 은 싣지 않는다. 원문을 넣는 순간
                                     # 「질의 원문 미보관」 정책이 깨진다. 고른 회사는
                                     # 이미 corp_codes 에 정규화된 코드로 들어간다.
                                     weak_kinds=(",".join(
                                         w.get("kind") or "unknown"
                                         for w in ledger["weak_resolutions"]) or None),
                                     # 조용한 대체의 **종류만**. 이 값이 갑자기 늘면 우리가
                                     #   무언가를 깨뜨린 것이다 — 오류율로는 안 보인다.
                                     degraded=(",".join(ledger.get("degradations") or []) or None),
                                     # 260824: 「느리다」의 원인을 그 자리에서 가른다.
                                     #   여태는 latency 하나뿐이라 **스스로 느린 것**과
                                     #   **줄에 서 있던 것**이 같은 숫자로 보였다.
                                     inflight=ledger.get("inflight_max") or None,
                                     cpu_ms=int((_t.process_time() - cpu0) * 1000),
                                     lag_ms=int(loop_lag_ms() - lag0))
                await send(message)
            try:
                await self.app(scope, replay, send_wrapper)
            finally:
                # 조건 없이 부른다 — 등록 안 된 장부면 no-op 이다. 여기에도 `if is_call`
                # 을 달면 **한쪽만 고쳐질 자리**가 하나 더 생긴다(이 레포에서 다섯 번 겪었다).
                ledger_exit(ledger)     # 예외로 빠져나가도 반드시 뺀다
        else:
            await self.app(scope, receive, send)


def allowed_hosts() -> list[str]:
    """DNS 리바인딩 방어의 허용 목록. `FASTMCP_ALLOWED_HOSTS` 로 덧붙일 수 있다
    (로컬에서 8000 이 아닌 포트로 띄울 때 필요)."""
    hosts = [
        "open-proxy-mcp.fly.dev",
        "localhost:8000",
        "127.0.0.1:8000",
        "0.0.0.0:8000",
    ]
    extra = os.environ.get("FASTMCP_ALLOWED_HOSTS", "").strip()
    if extra:
        hosts.extend([h.strip() for h in extra.split(",") if h.strip()])
    return hosts


def bind_host() -> str:
    return os.environ.get("FASTMCP_HOST", "0.0.0.0")


def bind_port() -> int:
    return int(os.environ.get("FASTMCP_PORT", "8000"))


def transport_security() -> TransportSecuritySettings:
    """호스트 보호를 **명시적으로** 만든다.

    mcp 2.0 은 이 값을 안 넘기면 host 가 localhost 계열일 때만 보호를 켠다
    (`lowlevel/server.py`). OPM 의 bind host 는 0.0.0.0 이라 그 조건에 안 걸려
    **보호가 조용히 꺼진다** — 사용자는 멀쩡히 쓰고 방어만 사라지며 아무 에러도 안 난다.
    그래서 기본값에 맡기지 않고 항상 만들어 넘긴다.
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts(),
    )


def build_app(server=None):
    """**프로덕션이 실제로 서빙하는 ASGI 앱.** 테스트는 이 함수를 부른다.

    무상태 HTTP: 각 요청이 독립(세션 in-memory 미보관) → fly 다중 머신에서 라우팅이
    갈려도 "Session not found" 없음. OPM tool은 무상태(요청마다 키·파라미터 자급)라
    세션 유지 불필요. 2머신 유지하면서 세션 어피니티 문제 해결. (2026-06)
    """
    server = server or build_mcp()
    # 2.0 에서 이 **넷**은 `settings` 를 떠나 여기 인자가 됐고, **기본값이 전부 우리와 반대**다
    # (다섯 번째인 port 는 앱이 아니라 uvicorn.run()/run() 이 받는다).
    # 대입은 다섯 다 ValueError 로 시끄럽게 터지므로 안전하다 — 위험한 건 **인자를 빠뜨리는 것**
    # 이고, 그건 다섯 다 조용하다. 그중 transport_security 만 결과가 보안이다:
    # 없으면 보호가 꺼진 채로 정상 서빙된다(사용자도 지표도 눈치 못 챈다).
    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=transport_security(),
        host=bind_host(),
        # 기본값과 같은 값이지만 **명시한다** — 위 넷에 적용한 「SDK 기본값을 믿지 않는다」가
        # 여기에도 그대로 걸린다. 1.29 도 같은 4 MiB 였으므로 이관에 따른 변화는 없다(실측).
        # 주의: 이 상한은 우리를 못 지킨다 — ApiKeyMiddleware 가 라우터 **밖**에 있어
        # 본문을 통째로 버퍼링한 뒤에야 413 이 난다(32 MiB 로 실측, 1.29·2.0 동일).
        # 그 구멍은 이 이관과 무관한 기존 구조이고 따로 다뤄야 한다.
        max_request_body_size=4 * 1024 * 1024,
    )
    # 안쪽부터 바깥쪽 순서로 읽는다 — Starlette 의 add_middleware 는 **앞에 끼우므로**
    # 나중에 더한 ApiKeyMiddleware 가 바깥이 된다. 그게 맞다: 키 게이트를 통과한 진짜
    # 호출만 기록에 남고, 401 로 되돌아간 요청은 섞이지 않는다.
    # CaptureMiddleware 는 `OPM_CAPTURE_DIR` 이 있을 때만 실제로 일한다(운영에는 없다).
    app.add_middleware(CaptureMiddleware)
    app.add_middleware(ApiKeyMiddleware)
    return app


def main():
    parser = argparse.ArgumentParser()
    # 전송 방식은 하나뿐이다. 종전에는 stdio·sse 도 받았고 **기본값이 stdio** 였다 —
    # 금지된 것이 기본값이라, 인자를 빼먹으면 조용히 그리로 떴다.
    #   stdio : 세션이 뜰 때 그 시점 코드를 메모리에 붙들어, 고쳐도 옛 결과를 낸다(260802).
    #   sse   : 연결을 붙들어 fly 2머신에서 "Session not found" 가 난다. streamable-http 가
    #           그 문제를 풀려고 나온 후속 방식이다. 게다가 SDK 가 자기 앱을 따로 만들어
    #           우리 ApiKeyMiddleware(키 게이트·통계·로그 마스킹)가 안 붙었다.
    # 인자 자체는 남긴다 — Dockerfile·launch.json 이 명시해서 넘긴다.
    parser.add_argument(
        "--transport",
        choices=["streamable-http"],
        default="streamable-http",
    )
    args = parser.parse_args()
    server = build_mcp()

    import uvicorn
    app = build_app(server)              # 서빙 결정은 전부 build_app 안에 있다
    install_api_key_redaction()
    uvicorn.run(app, host=bind_host(), port=bind_port())


if __name__ == "__main__":
    main()
