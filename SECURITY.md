# 보안 취약점 신고

*[English below](#reporting-a-vulnerability)*

## 어디로 알리나

**공개 이슈에 적지 마세요** — 적는 순간 공개됩니다.

GitHub의 [Report a vulnerability](https://github.com/MarcoYou/open-proxy-mcp/security/advisories/new)
로 비공개 신고를 열어 주세요. 그 기능이 안 보이면 <gunhoqw20@gmail.com> 으로 메일 주세요
(README 의 문의 주소와 같습니다).

## 무엇을 적으면 도움이 되나

- 무엇을 하면 재현되는지 (호출한 tool·인자, 또는 요청 모양)
- 무엇을 봤는지 (응답·로그 일부). **키가 섞였다면 지우고 보내 주세요**
- 언제 (대략의 시각 — 서버 로그와 맞춰 보는 데 씁니다)

## 특히 알고 싶은 것

이 서버는 DART·KRX 공개 데이터를 읽어 MCP로 넘겨줍니다. 그 성격상 이런 것이 위험합니다:

| | 왜 |
|---|---|
| **API 키가 새는 경로** | URL·쿼리·예외·로그 어디든. 키 하나가 막히면 그 키를 쓰는 이용자 전원이 멈춥니다 |
| **한 요청으로 서버를 죽이는 입력** | 1GB VM이라 OOM이 나면 같은 머신의 다른 이용자가 전부 502를 받습니다 |
| **상류(DART·KRX) 차단을 유발하는 패턴** | 차단은 IP 기준이라 그 머신 전체가 막힙니다 |
| **응답에 섞여 나오는 남의 데이터** | 캐시·세션 경계가 새는 경우 |

## 범위 밖

- **공시 내용 자체의 오류** — DART 원문이 그렇다면 그건 상류 데이터입니다. 다만 *우리가* 잘못
  읽거나 잘못 라벨한 것이라면 **버그이니 일반 이슈로** 알려 주세요(예: 통화·단위·기준일 표기).
- **레이트리밋 자체** — 분당 한도는 DART의 정책이고 저희는 그 아래에서 스로틀합니다.
- 자신의 키로 자신의 할당량을 소진하는 것.

## 어떻게 되나

접수하면 확인하고 회신합니다. 고칠 것이면 수정 후 알려 드리고, 범위 밖이거나 재현이 안 되면
그 이유를 말씀드립니다. **정해진 응답 시한을 약속하지는 않습니다** — 1인 프로젝트입니다.

버그 바운티는 없습니다. 원하시면 수정 커밋·릴리즈 노트에 신고자로 적어 드립니다.

## 지원 범위

`main` 브랜치의 최신 배포본만 봅니다. 이 저장소는
[PolyForm Noncommercial 1.0.0](LICENSE)이라 비상업적 사용만 허용되며, 포크·개조본은 지원하지
않습니다.

---

# Reporting a vulnerability

**Please don't open a public issue** — that publishes it.

Use GitHub's [Report a vulnerability](https://github.com/MarcoYou/open-proxy-mcp/security/advisories/new)
to file privately. If that isn't available to you, email <gunhoqw20@gmail.com> — the same
address listed in the README.

## What helps

- How to reproduce it (tool and arguments, or the request shape)
- What you saw (response or log excerpt) — **redact any API key before sending**
- Roughly when, so it can be matched against server logs

## Especially relevant here

This server reads public DART/KRX filings and serves them over MCP. Given that:

- **Anything that leaks an API key** (URL, query, exception, log). One blocked key stalls every user on it.
- **Input that kills the server** — it runs on a 1GB VM; an OOM returns 502 to everyone on that machine.
- **Patterns that get upstream (DART/KRX) to block us** — blocking is per-IP, so it takes out the whole machine.
- **Another user's data appearing in a response** — a cache or session boundary leak.

## Out of scope

- Errors in the filings themselves. If *we* misread or mislabel something (currency, units, as-of date),
  that's a bug — please file a normal issue.
- Rate limits as such: the per-minute cap is DART's policy and we throttle beneath it.
- Exhausting your own quota with your own key.

## What to expect

Reports are acknowledged and answered. Fixes are reported back; out-of-scope or non-reproducible
reports get an explanation. **No response-time guarantee** — this is a one-person project.

There is no bug bounty. Credit in the fix commit and release notes is offered on request.

## Supported versions

Only the latest deployment from `main`. This repository is
[PolyForm Noncommercial 1.0.0](LICENSE); forks and modified builds are not supported.
