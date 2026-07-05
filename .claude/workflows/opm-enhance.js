export const meta = {
  name: 'opm-enhance',
  description: '검증 개선점을 받아 코드를 실제 개선하고 4축(속도·API낭비·정확성·regression)으로 검증 — 개선 1건당 최대 3회 정제(loop-until-pass). worktree 격리, 자동 커밋 안 함',
  whenToUse: 'opm-investigate가 도출한 개선점(파서·budget 등)을 실제 코드 개선으로 실행하되, 회귀 없이 4축을 통과하는지 검증하고 채택 후보 diff를 제안할 때',
  phases: [
    { title: 'Baseline', detail: '대상 tool·샘플 파악 + 원본 코드로 baseline(결과·속도·콜수) 측정' },
    { title: 'Iterate', detail: '개선 구현 → 4축 검증 → 실패 피드백 재시도 (최대 3회, loop-until-pass)' },
    { title: 'Report', detail: '채택 후보 diff 제안 또는 미통과 보고 (커밋은 사용자가)' },
    { title: 'Docs', detail: '채택 시 변경에 맞춰 tool 문서·콜 budget 갱신 제안 (코드↔문서 동기화)' },
  ],
}

// 입력: args = "개선 항목 설명" 또는 {improvement, tool?, sample?(기업 배열), notes?}
const input = typeof args === 'string' ? { improvement: args } : (args || {})
const improvement = input.improvement || (input.notes && input.notes.top_actions && input.notes.top_actions[0]) || '(개선 항목이 args로 전달되지 않음)'

const BASELINE_SCHEMA = {
  type: 'object',
  properties: {
    tool: { type: 'string', description: '개선 대상 tool' },
    target_files: { type: 'array', items: { type: 'string' }, description: '수정할 services/tools 파일 경로' },
    sample: { type: 'array', items: { type: 'string' }, description: '검증 샘플 기업(개선이 영향 주는 케이스 + 영향 없어야 할 regression 케이스, 5사 내외)' },
    baseline_metrics: { type: 'object', additionalProperties: true, description: '원본 코드 직접 실행 결과: timings·DART 콜수·핵심 출력(샘플별)' },
    plan: { type: 'string', description: '어떻게 고칠지 1~2줄 계획' },
  },
  required: ['tool', 'target_files', 'sample', 'baseline_metrics'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    attempt: { type: 'integer' },
    diff_summary: { type: 'string', description: '이번 회차에 수정한 내용 요약' },
    speed_ok: { type: 'boolean', description: 'timings가 baseline 대비 느려지지 않음' },
    api_ok: { type: 'boolean', description: 'DART 콜수가 baseline 대비 늘지 않음 (budget 낭비 없음)' },
    accuracy_ok: { type: 'boolean', description: '개선 목적 케이스가 실제로 고쳐짐' },
    regression_ok: { type: 'boolean', description: '목적 외 케이스 결과가 baseline과 동일 (회귀 0)' },
    measured: { type: 'object', additionalProperties: true, description: '수정 코드 직접 실행 측정값 (속도·콜수·결과)' },
    issues: { type: 'array', items: { type: 'string' }, description: '실패한 축과 원인 (통과 시 빈 배열)' },
  },
  required: ['attempt', 'speed_ok', 'api_ok', 'accuracy_ok', 'regression_ok', 'issues'],
}

const REPORT_SCHEMA = {
  type: 'object',
  properties: {
    accepted: { type: 'boolean' },
    summary: { type: 'string', description: '무엇을 어떻게 고쳤나 (또는 왜 실패했나)' },
    diff: { type: 'string', description: '채택 후보 diff (사용자 검토용). 미통과면 마지막 시도 상태' },
    four_axis: { type: 'object', additionalProperties: true, description: '속도·API·정확성·regression 최종 결과' },
    next_step: { type: 'string', description: '채택이면 커밋 권고, 미통과면 다음 시도 방향' },
  },
  required: ['accepted', 'summary', 'next_step'],
}

// ── Phase 0: baseline (원본 코드로 측정) ──
phase('Baseline')
const base = await agent(
  `OPM 코드 개선 작업의 baseline을 잡아라. 개선 항목: "${improvement}"\n` +
  (input.tool ? `대상 tool(고정): ${input.tool}\n` : ``) +
  (input.sample ? `검증 샘플(고정): ${input.sample.join(', ')}\n` : ``) +
  `\n절차:\n` +
  `1. 개선이 영향을 주는 대상 tool과 services/tools 파일을 식별한다.\n` +
  `2. 검증 샘플을 정한다(5사 내외): 개선이 적용될 케이스 + 개선과 무관해 결과가 변하면 안 되는 regression 케이스를 함께 포함.\n` +
  `3. **원본(현재) 코드를 직접 import 실행**해 baseline을 측정한다. MCP(배포 서버) 호출이 아니라 로컬 코드여야 한다 — 예: \`python3 -c "import asyncio; from open_proxy_mcp.services.<도메인> import build_<tool>_payload as b; print(asyncio.run(b('<기업>', ...)))"\`. 각 샘플의 핵심 출력 + data.timings_ms + DART 콜수를 기록한다.\n` +
  `   ⚠️ DART 분당 910 한도 — 샘플을 작게 잡고 wiki/tools/tool_call_budget.md를 참고하라.`,
  { schema: BASELINE_SCHEMA, phase: 'Baseline', label: 'baseline' }
)

// ── Phase 1: loop-until-pass (최대 3회 정제) ──
phase('Iterate')
let accepted = null
let feedback = ''
const attempts = []
for (let i = 1; i <= 3; i++) {
  const r = await agent(
    `개선 "${improvement}"을 ${i}회차로 구현하고 4축 검증하라. (worktree 격리 — 메인 체크아웃은 절대 건드리지 말고, 커밋·푸시 금지)\n` +
    `대상 tool: ${base.tool}\n대상 파일: ${(base.target_files || []).join(', ')}\n검증 샘플: ${(base.sample || []).join(', ')}\n` +
    `baseline 측정값: ${JSON.stringify(base.baseline_metrics)}\n` +
    (feedback ? `\n[이전 회차 실패 이유] ${feedback}\n→ 이를 반영해 접근을 바꿔 수정하라.\n` : ``) +
    `\n절차:\n` +
    `1. 대상 services/tools 코드를 수정해 개선 목적을 달성한다.\n` +
    `2. **수정한 코드를 직접 import 실행**(MCP 아님)해 baseline과 동일 샘플을 측정한다.\n` +
    `3. 4축 게이트 — 모두 통과해야 채택:\n` +
    `   · speed_ok: timings가 baseline 대비 느려지지 않음\n` +
    `   · api_ok: DART 콜수가 baseline 대비 늘지 않음 (불필요 호출 추가 금지)\n` +
    `   · accuracy_ok: 개선 목적 케이스가 실제로 고쳐짐\n` +
    `   · regression_ok: 개선과 무관한 케이스의 결과가 baseline과 **완전히 동일**(회귀 0)\n` +
    `4. verdict(4축 bool) + diff_summary + measured + 실패 축의 원인을 issues에 적는다.\n` +
    `절대 커밋·푸시하지 마라. 수정은 worktree 안에서만.`,
    { schema: VERDICT_SCHEMA, phase: 'Iterate', label: `iter${i}`, isolation: 'worktree' }
  )
  if (!r) { attempts.push({ attempt: i, error: 'agent 실패' }); feedback = 'agent 응답 없음'; continue }
  attempts.push(r)
  if (r.speed_ok && r.api_ok && r.accuracy_ok && r.regression_ok) {
    accepted = r
    log(`${i}회차에 4축 전부 통과 — 채택 후보`)
    break
  }
  feedback = (r.issues || []).join('; ') || '4축 중 일부 실패(원인 미상)'
  log(`${i}회차 실패: ${feedback}`)
}

// ── Phase 2: 종합 보고 (커밋은 사용자) ──
phase('Report')
const report = await agent(
  `OPM 코드 개선 시도 결과를 종합하라. 개선 항목: "${improvement}" / 대상 tool: ${base.tool}\n` +
  `시도 기록: ${JSON.stringify(attempts)}\n` +
  `최종: ${accepted ? '4축 전부 통과 (채택 후보)' : '3회 내 4축 미통과'}\n\n` +
  `- 채택이면: 무엇을 어떻게 고쳤는지, diff 요약, 4축 결과를 정리하고, next_step에 "사용자 검토 후 커밋 권고"를 적는다.\n` +
  `- 미통과면: 어느 축이 왜 막혔는지, 다음 시도에 무엇을 다르게 할지 next_step에 적는다.\n` +
  `자동 커밋·푸시는 절대 하지 않는다 — 사용자 검토용 제안만 한다.`,
  { schema: REPORT_SCHEMA, phase: 'Report', label: 'report' }
)

// ── Phase 4: 문서 동기화 (채택 시에만 — 코드 변경에 맞춰 문서·budget 갱신 제안) ──
phase('Docs')
let docsUpdate = null
if (accepted) {
  const DOCS_SCHEMA = {
    type: 'object',
    properties: {
      tool_doc_changes: { type: 'array', description: 'wiki/tools/<tool>.md 에서 갱신할 부분 (동작·필드·파싱 전략이 바뀐 곳)',
        items: { type: 'object', properties: { file: { type: 'string' }, before: { type: 'string' }, after: { type: 'string' } } } },
      budget_change: { type: 'object', additionalProperties: true, description: 'tool_call_budget.md 갱신 — 콜 수가 바뀌었으면 {tool, old, new, reason}, 아니면 변경 없음 표기' },
      applied: { type: 'boolean', description: '문서를 실제 Edit으로 갱신했는지 (true) 아니면 제안만(false)' },
      note: { type: 'string' },
    },
    required: ['applied'],
  }
  docsUpdate = await agent(
    `방금 4축을 통과해 채택된 코드 개선에 맞춰, 관련 문서를 동기화하라.\n` +
    `개선: "${improvement}" / tool: ${base.tool} / 수정 파일: ${(base.target_files || []).join(', ')}\n` +
    `채택 보고: ${JSON.stringify(report)}\n\n` +
    `점검·갱신:\n` +
    `1. wiki/tools/${base.tool}.md — 동작·출력 필드·파싱 전략이 바뀌었으면 해당 부분을 실제 Edit으로 갱신.\n` +
    `2. wiki/tools/tool_call_budget.md — 이 개선으로 ${base.tool}의 DART 콜 수가 바뀌었으면 표·JSON을 갱신(코드 호출 지점 기준). per-firm/market-scan 모드도 확인.\n` +
    `3. 갱신 후 python3 scripts/wiki_lint.py --strict 로 link 정책 확인.\n` +
    `※ 실제 코드 수정(worktree)은 채택 후보일 뿐 아직 메인에 머지 안 됐을 수 있다 — 문서는 '채택될 변경 기준'으로 갱신하되, 자동 커밋·푸시는 하지 마라. 바뀐 게 없으면 applied=false로 둔다.`,
    { schema: DOCS_SCHEMA, phase: 'Docs', label: 'docs' }
  )
}

return {
  improvement,
  tool: base.tool,
  target_files: base.target_files,
  sample: base.sample,
  baseline: base.baseline_metrics,
  attempts,
  accepted: accepted || null,
  status: accepted ? 'passed_4axis_needs_user_commit' : 'not_passed_in_3_attempts',
  report,
  docs_update: docsUpdate,
}
