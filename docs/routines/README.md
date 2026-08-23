# 루틴 레시피 (docs/routines)

스케줄 클라우드 루틴(`/schedule`)에 **그대로 붙여넣는 프롬프트 모음**입니다. 매일/주기적으로
자동 실행돼 폰으로 훑는 디제스트·알람 유즈를 담습니다. tool의 한 줄 역할·상세 스키마는
[wiki/tools 카탈로그](../../wiki/tools/README.md)가 정본(SSOT)이고, 여기는 **운영 playbook 뷰**만
담습니다(예시 질문은 [docs/examples](../examples/README.md)).

## 레시피

| 레시피 | 무엇 | 쓰는 tool |
|---|---|---|
| [screener-morning-digest](screener-morning-digest.md) | 매일 아침 수주·임시주총·정기주총 공시 디제스트 (신규/정정 구분 + ⭐ 임팩트). 범위는 자연어로 지정 | screener |

## 루틴을 예약하는 법 (셋업)

예약 방식은 둘이고, **고르는 기준은 하나 — 그 시각에 내 컴퓨터가 켜져 있나**입니다.

| | 로컬 예약 | 클라우드 루틴 |
|---|---|---|
| 어디서 도나 | 내 컴퓨터의 Claude 앱 | 클라우드 |
| 준비물 | 없음 | GitHub 레포 1개 |
| 앱이 닫혀 있을 때 | **다음에 앱을 열 때 뒤늦게 실행** | 정시에 실행 |
| 아침 디제스트에 | 부적합 | **적합** |

아침 8시 디제스트라면 클라우드를 쓰세요. 로컬은 8시에 노트북이 닫혀 있으면 9시에 열 때
"오늘의 수주"가 뜹니다 — 아침에 훑는 용도가 무너집니다.

### 로컬 예약 — 준비물 없음

Claude 앱에서 말로 예약하면 끝입니다. "매주 평일 오전 8시에 이걸 해줘" + 레시피 프롬프트.

- 저장 위치: `~/.claude/scheduled-tasks/<이름>/SKILL.md`
- 시각은 **내 컴퓨터의 로컬 시간** 기준(UTC 환산 불필요)
- 매 실행은 **빈 세션에서 시작**한다 — 프롬프트에 커넥터·출력형식·전제를 전부 적어야 한다
  (그래서 이 폴더의 레시피가 그렇게 길다)
- **작업 폴더를 지정하는 설정은 없다.** 앱 컨텍스트에서 돈다

### 클라우드 루틴 — 빈 GitHub 레포 1개

클라우드에서 도니 작업할 폴더가 있어야 하고, 그게 GitHub 레포입니다.
**레시피는 레포에 아무것도 쓰지 않는다** — screener를 부르고 알림을 보내는 게 전부다.
작업 공간으로만 쓰이므로 **GitHub 웹에서 빈 private 레포를 하나 만들면 그걸로 끝**입니다.
아래 git 절차는 로컬 폴더를 레포로 올려야 할 때만 필요합니다.

<details><summary>로컬 폴더를 레포로 올려야 한다면 (Windows / Mac)</summary>

```powershell
# Windows — git 설치 후 PowerShell 재시작
winget install --id Git.Git -e --source winget
```
```bash
# Mac — 보통 기본 내장. 최신이 필요하면
brew install git
```
```bash
# 공통 — 사용자 정보 + 레포 연결
git config --global user.name "이름"
git config --global user.email "메일"
cd <작업폴더>
git init && git add . && git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/<아이디>/<레포>.git
git push -u origin main
```

- 레포는 GitHub 웹에서 **미리 만들어 둬야** 한다.
- push 때 비밀번호를 물으면 비밀번호가 아니라 **Personal Access Token**을 넣는다
  (GitHub 설정에서 발급). 계정 인증이라 본인만 할 수 있다.
</details>

> 예약 화면의 메뉴 위치는 클라이언트 업데이트로 바뀔 수 있습니다. 막히면 앱에서 `/schedule`을
> 먼저 시도해보세요. **이 문서가 절차의 정본이고, 배포용 PDF·설명서는 여기를 링크만 겁니다** —
> 화면이 바뀌어도 문서만 고치면 되도록.

## 루틴 프롬프트 공통 원칙

이 폴더의 프롬프트는 아래를 공통으로 지킵니다. 새 레시피를 추가할 때도 따르세요.

1. **출력 형식을 매일 똑같이 못박는다.** "table or bullets"처럼 `or`를 두지 말 것 — 제목·섹션 순서·표
   칼럼을 고정해야 날마다 같은 모양으로 스캔된다.
2. **결과만 출력.** 에이전트 진행상황·영어 상태로그("Screener succeeded…", "Sending…")·"확인해
   드릴 수 있습니다" 같은 대화체는 금지 — 답장 없는 푸시다.
3. **정직한 degrade.** 값이 없으면 지어내지 말고 "미상"으로 두고 원문(DART) 링크를 남긴다. 빈 결과는
   "없음"으로 명시하고, 조회 실패는 실패라고 솔직히 — "빈 배열 = 성공" 금지.
4. **콜 예산을 의식한다.** `universe`·`period`·`details`가 DART 콜 수를 정한다. market-scan vs
   per-firm(details) 구분은 [tool_call_budget](../../wiki/tools/tool_call_budget.md) 참조.
5. **신규 vs 정정을 가른다.** 예정치(결정·소집결의)와 정정을 섞지 않는다. 정정의 뉴스는 "바뀐 값"이므로
   가능하면 정정전→정정후 diff를 보여준다.
