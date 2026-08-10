---
type: architecture
title: MCP 엔드포인트 — live-opm / pilot-opm 두 개, 목적이 다르고 따로 관리한다
updated: 2026-08-02
---

# MCP 엔드포인트 — live-opm / pilot-opm

> **OPM에 붙는 MCP는 둘뿐이다. 둘은 같은 서버의 두 사본이 아니라, 목적이 다른 별개 대상이다.**
>
> - **`pilot` = 바꾼 것을 시험하는 곳.** 파서·tool·필드·파라미터를 고치거나 더하거나 뺐을 때,
>   그것을 반영시킨 뒤 문제가 없는지 확인하는 용도.
> - **`live` = 사람들에게 배포해서 쓰게 하는 것.**
>
> 섞으면 "고쳤는데 왜 그대로지"가 되고, 반대로 시험 안 끝난 것이 사람들에게 나간다.
> 셋업 키·시크릿은 [[environment-secrets]], 코드 구조는 [[project_structure]].

## 두 엔드포인트

| | `pilot-opm` | `live-opm` |
|---|---|---|
| **무엇** | **지금 워킹트리 코드** | fly.io에 **배포된 것** |
| **주소** | `http://127.0.0.1:8000/mcp` | `https://open-proxy-mcp.fly.dev/mcp` |
| **목적** | **바꾼 것을 반영시킨 뒤 문제 없는지 시험** | **사람들에게 배포해서 쓰게 하는 것** |
| **누가 보나** | 나 혼자 | 실제 사용자 |
| **코드 시점** | 저장한 그 순간 | 마지막 배포 시점 |
| **관리 주체** | 사람이 띄우고 내림 (`preview_start`/`preview_stop`) | `.github/workflows/deploy.yml` (fly 배포) |
| **설정 위치** | `.mcp.json` + `.claude/launch.json` | `.mcp.json` (gitignore — 키가 URL에 들어감) |
| **전송 방식** | `streamable-http` (무상태) — **배포본과 동일** | `streamable-http` (무상태) |

**따로 관리된다.** pilot은 사람이 손으로 띄우고 내리는 임시 프로세스라 언제든 꺼져 있어도
정상이고, live는 배포 파이프라인이 관리하는 상시 서비스다. pilot을 껐다고 사용자에게 영향이
가지 않고, live를 배포했다고 pilot이 최신이 되지도 않는다.

## pilot이 받아내는 변경

pilot에서 시험하는 것은 코드 한 줄이 아니라 **사용자에게 보이는 표면 전체**다:

| 바꾼 것 | pilot에서 확인할 것 |
|---|---|
| **파서** | 값이 맞나 · 기존에 되던 회사가 깨지지 않았나(회귀) |
| **tool 추가·제거** | 도구 목록에 뜨나 · 없앤 게 정말 사라졌나 |
| **필드 추가·제거** | payload에 있나 — **그리고 렌더러가 그걸 쓰나** |
| **파라미터 추가·제거·기본값 변경** | 새 값이 먹나 · 안 주면 종전대로 동작하나 · 옛 호출이 안 깨지나 |

**payload가 맞아도 렌더러가 안 쓰면 사용자는 못 본다** — 필드를 더하고 뺄 때 가장 자주 새는
구멍이고, pilot이 그걸 잡는다. 그래서 pilot 확인은 **응답 JSON이 아니라 사람이 보는 출력**까지 본다.

**전송 방식이 같은 것이 핵심이다.** pilot이 stdio였다면 프로토콜 차이 때문에 pilot에서 통과한 것이
live에서 깨질 수 있다. 같은 `streamable-http`라 그 층의 차이가 없고, 남는 차이는 **코드 시점 하나**다.

## 언제 무엇을 쓰나

- **바꾼 것을 시험할 땐 = `pilot-opm`** (260731 이후 표준). 배포본은 방금 고친 코드가 아니다.
- **배포 후 확인 = `live-opm`.** 코드가 맞는 것과 배포가 반영된 것은 **별개 문제**라, pilot에서
  통과했어도 live에서 한 번 더 본다.
- **사람들에게 나가기 전 마지막 관문이 pilot이다.** pilot을 건너뛰면 시험 안 끝난 변경이
  곧장 사용자에게 간다.

```
코드 수정 → pilot-opm 검증 → 커밋·배포 → live-opm 재확인
              (내용이 맞나)          (반영이 됐나)
```

pilot은 코드를 고칠 때마다 `preview_stop` → `preview_start`로 다시 띄운다. 안 그러면 옛 코드를
붙들고 있다(아래 참조).

## 이름이 갈려 있어야 하는 이유 · stdio 금지

이름이 겹치면 도구 이름(`mcp__*`)만 봐서는 live 를 부른 건지 local 을 부른 건지 구분이 안 된다.
그래서 두 엔드포인트는 **반드시 다른 이름**(`live-opm` / `pilot-opm`)을 쓰고, 둘 다
URL(`streamable-http`)로만 등록한다.

**stdio 로는 OPM MCP를 띄우지 않는다.** stdio MCP 는 세션이 뜰 때 프로세스로 떠 **그 시점 코드를
메모리에 붙들기** 때문에, 코드를 고쳐도 그 세션의 도구는 계속 옛 결과를 낸다. 로컬 검증은
pilot(HTTP)으로만 한다.

## 둘의 차이를 항상 추적한다

남는 차이가 **코드 시점 하나**뿐이므로, 그 하나를 늘 눈에 보이게 둔다. 모르면 「고쳤는데 왜
그대로」(배포 안 됨)나 「시험 안 끝난 게 나감」(pilot 건너뜀) 중 하나에 걸린다.

```bash
python3 scripts/live_pilot_diff.py
```

```
━━ live ↔ pilot ━━
  live   9c083a5c  tools=25  (2026-08-02 03:53)
  pilot  9c083a5c  안 떠 있음
━━ 차이 (pilot 에만 있고 live 에는 없는 것) ━━
  미커밋 코드 3개:  ← 동작이 달라지는 쪽
    open_proxy_mcp/services/shareholder_meeting_parser.py
```

**무엇을 어디서 읽나** — 추정하지 않고 권위 있는 출처만 쓴다:

| 알고 싶은 것 | 출처 |
|---|---|
| live에 올라간 커밋 | GitHub Deployments — `deploy.yml`이 배포마다 `ref=github.sha`로 남긴다. **성공한** 배포만 live로 친다 |
| live의 tool 개수 | live `/health` → `{"status":"ok","tools":25}` (키 불필요) |
| pilot의 코드 | 워킹트리 `HEAD` + 미커밋 변경 |
| pilot이 떠 있나 | pilot `/health` |

**tool 개수 비교가 tool 추가·제거 드리프트를 잡는다** — 필드·파라미터 변경은 개수로 안 드러나니
그건 pilot 실호출로 본다.

`.claude/settings.json`의 **SessionStart 훅**(`scripts/live_pilot_diff_hook.sh`)이 세션 시작마다
자동으로 띄운다. 차이가 없으면 조용하다. 원격 조회가 실패해도(오프라인·gh 미인증) 로컬 정보만
내고 절대 작업을 막지 않는다 — 추적기가 작업을 막아서는 안 된다.

## 상태 확인

```bash
# stdio 좀비가 없어야 정상 (있으면 옛 코드를 붙든 세션이 있다는 뜻)
ps aux | grep "python -m open_proxy_mcp" | grep -v grep | grep -v streamable-http

# pilot이 떠 있나
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

stdio 프로세스가 잡히면 그 부모 세션을 확인한다(`ps -o ppid= -p <PID>`). 설정을 고치기 전에 뜬
세션이 잔여 프로세스를 붙들고 있는 경우가 있고, 그건 설정 문제가 아니라 그 세션만의 문제다.

## 전송 방식이 하나뿐인 이유 (260810)

MCP 에는 통로가 셋 있는데 OPM 은 **`streamable-http` 만** 쓴다. 나머지 둘은 코드에서 지웠다 —
규칙으로 금지하는 대신 **선택지 자체를 없앴다.** 금지된 stdio 가 argparse 기본값이라, 인자를
빼먹으면 조용히 그리로 떴기 때문이다.

| | 무엇 | 왜 안 쓰나 |
|---|---|---|
| **stdio** | 서버를 자식 프로세스로 띄워 표준입출력으로 대화 | **세션이 뜰 때 그 시점 코드를 메모리에 붙든다** — 고쳐도 그 세션은 계속 옛 결과를 낸다(260802 실측). 「고쳤는데 안 고쳐진 것처럼」 보이는 함정 |
| **sse** | 서버 말을 듣는 연결을 계속 열어두고, 보낼 건 다른 경로로 (통로 둘) | 연결을 붙들어 **fly 2머신에서 "Session not found"**. streamable-http 가 이 문제를 풀려고 나온 후속 방식이다. 게다가 SDK 가 자기 앱을 따로 만들어 **`ApiKeyMiddleware` 가 안 붙는다** — 키 게이트·통계·로그 마스킹이 전부 빠진 채 뜬다 |
| **streamable-http** | `/mcp` 한 경로로 주고받고 필요할 때만 스트리밍 | **무상태**로 돌 수 있어 어느 머신이 받든 상관없다 |

`--transport` 인자는 남겼다(선택지는 하나) — `Dockerfile`·`launch.json` 이 명시해서 넘긴다.

## 배포해도 되는지 한 번에 보는 법

```bash
python3 scripts/check_branch_against_live.py
```

지금 워킹트리를 임시 포트에 띄워 **live 와 나란히 대조**하고 끝나면 알아서 내린다.
DART 콜 0(쓰는 메서드가 전부 DART 를 안 친다) · 키는 `.env` 에서 읽고 출력하지 않는다.

재는 것은 두 종류다.
- **같은가** — `tools/list`·`prompts/list` 가 live 와 의미상 같은가(JSON 키 순서는 무시),
  프로토콜 4종(`2024-11-05`~`2025-11-25`) 협상이 같은가. 클라이언트가 뭘 쓰든 답이 같아야 한다.
- **막는가** — 낯선 호스트·**닮은 호스트**·키 없음·공백 키를 거부하는가.
  **이쪽이 더 중요하다** — 보호가 사라져도 서비스는 멀쩡히 돌아서 아무도 눈치채지 못한다.
  「200 이 나온다」만 재면 꺼진 쪽이 더 잘 통과한다.

테스트가 초록인 것과는 다른 층이다. 실측(260810): 사용자 전원이 421 을 받는 완전 다운
상태에서 706/708 이 통과했다.

## 배포를 되돌리는 법

`flyctl` 에 `rollback` 서브커맨드는 **없다**. 되돌리기는 **직전 이미지를 그대로 재배포**하는 것이고,
빌드가 없어 초 단위로 끝난다. 소스를 revert 해서 다시 빌드하는 길(5~10분)보다 훨씬 빠르므로,
장애 중에는 **앞으로 고치지 말고 먼저 되돌린다**.

```bash
FLY=~/.fly/bin/flyctl

# ① 직전 릴리스의 이미지 태그를 읽는다 (배포할 때마다 바뀌므로 그때그때 다시 읽는다)
"$FLY" releases --app open-proxy-mcp --json | head -40

# ② 그 이미지로 되돌린다
"$FLY" deploy --app open-proxy-mcp --image registry.fly.io/open-proxy-mcp:deployment-<태그>
```

- 이미지에 **`GH_SHA` 라벨**이 박혀 있어 어느 커밋인지 확인하고 고를 수 있다 —
  `"$FLY" image show --app open-proxy-mcp`.
- 인증이 안 돼 있으면 `releases` 가 `no access token available` 을 낸다. **인증되지 않은 상태는
  롤백 수단이 없는 상태**다 — SDK·의존성 교체처럼 되돌릴 일이 있는 배포 전에는 먼저
  `"$FLY" auth login` 으로 확인한다.

> 머신·볼륨 구성, 배포가 무엇을 갈아치우고 무엇을 남기는지, 시크릿 현황, 사고 이력은
> **private** `open-proxy-storage/wiki-private/architecture/fly-machine-operations.md` 에 있다
> (머신/볼륨 ID 가 붙어 public 에 못 둔다).

## 키 취급

두 URL 모두 `?opendart=<키>` 형태로 키가 **URL 안에** 들어간다. 그래서:

- `.mcp.json`은 **gitignore** (`.gitignore`에 등재됨 — 커밋 금지).
- curl 예시·로그·fixture에 URL을 그대로 붙여넣지 않는다. 키는 `.env`에서 읽고 **출력하지 않는다**
  (전체뿐 아니라 prefix도). 상세: [[environment-secrets]].

## 관련

- [[environment-secrets]] — 어떤 키가 왜 필요한가 · 로컬 `.env` + fly secrets
- [[project_structure]] — `server.py` 진입점과 transport
