#!/usr/bin/env bash
# PreToolUse hook (Bash matcher): git commit 직전에
# 스테이징된 변경에 코드(open_proxy_mcp/)는 있는데 wiki/ 변경이 하나도 없으면
# "wiki도 갱신했는지" 경고를 띄운다. 차단하지 않고 알림만 (Claude가 판단).
#
# stdin: PreToolUse 이벤트 JSON ({"tool_input":{"command":"..."}})
# stdout: 비어있으면 통과 / JSON(additionalContext)이면 경고 컨텍스트 주입
# exit 0 항상 (비차단)

input=$(cat)
cmd=$(printf '%s' "$input" | python3 -c "import json,sys; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null)

# git commit 명령이 아닐 때는 조용히 통과
case "$cmd" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

staged=$(git diff --cached --name-only 2>/dev/null)
has_code=0; has_wiki=0
printf '%s\n' "$staged" | grep -qE '^open_proxy_mcp/' && has_code=1
printf '%s\n' "$staged" | grep -qE '^wiki/' && has_wiki=1

if [ "$has_code" = 1 ] && [ "$has_wiki" = 0 ]; then
  msg="⚠️ wiki 동기화 점검: 이번 커밋에 코드(open_proxy_mcp/)가 포함됐는데 wiki/ 변경이 없습니다. tool·동작·구조가 바뀌었다면 관련 wiki(tools/·rules/·architecture/)와 index.md를 갱신했는지 확인하세요. (문서/설정 등 wiki 무관 변경이면 그대로 진행)"
  ctx=$(printf '%s' "$msg" | python3 -c "import json,sys; print(json.dumps(sys.stdin.read()))")
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":%s}}\n' "$ctx"
fi
exit 0
