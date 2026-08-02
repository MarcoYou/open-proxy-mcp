#!/usr/bin/env bash
# SessionStart hook: live-opm 과 pilot-opm(워킹트리)의 차이를 세션 시작 때 컨텍스트로 주입한다.
#
# 왜: 둘은 목적이 다른 별개 대상이고(→ wiki/architecture/mcp-endpoints.md) 남는 차이는
# 코드 시점 하나뿐이다. 그 차이를 모르면 "고쳤는데 왜 그대로"(배포 안 됨)나
# "시험 안 끝난 게 나감"(파일럿 건너뜀)에 걸린다. 그래서 항상 보이게 둔다.
#
# 차이가 없으면 아무것도 내지 않는다(조용). 원격 조회가 실패해도 절대 실패시키지 않는다 —
# 추적기가 작업을 막아서는 안 된다.
#
# stdout: 비어있으면 통과 / JSON(additionalContext)이면 컨텍스트 주입
# exit 0 항상 (비차단)

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

out=$(python3 scripts/live_pilot_diff.py --quiet 2>/dev/null)
[ -z "$out" ] && exit 0

printf '%s' "$out" | python3 -c '
import json,sys
body = sys.stdin.read()
msg = ("live-opm(배포본) 과 pilot-opm(워킹트리) 의 현재 차이입니다. "
       "코드 변경을 검증할 땐 pilot 으로, 배포 반영 여부는 live 로 확인하세요.\n\n" + body)
print(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":msg}}))
' 2>/dev/null

exit 0
