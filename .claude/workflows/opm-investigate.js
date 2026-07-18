export const meta = {
  name: 'opm-investigate',
  description: '유니버스에 OPM tool 배치 실행 → 파싱 정합성 적대적 2인 검사 → 도메인 전문가 3인 검증·해설 → OPM 개선점 도출',
  whenToUse: 'OPM tool 결과의 정합성(지시 위반·레이블·값·공시 정확성)과 실무 효용을 다관점으로 검증하고, 드러난 개선점을 모을 때',
  phases: [
    { title: 'Scout', detail: '질문 → 유니버스·tool·제약조건 추출' },
    { title: 'Batch', detail: '유니버스 각 사에 tool 실행 (DART rate는 서버 limiter가 자동 throttle)' },
    { title: 'Integrity', detail: '결과당 정합성 검사관 2인 적대적 검증' },
    { title: 'Expert', detail: '스튜어드십·행동주의·재무분석가 3인 검증·해설' },
    { title: 'Notes', detail: '이번 검증에서 드러난 OPM 개선점 도출 (파서·budget·wiki·데이터)' },
  ],
}

// ── 입력: args = "질문 문자열" 또는 {query, companies?, tool?, year?} ──
const input = typeof args === 'string' ? { query: args } : (args || {})
const query = input.query || '(질문이 args로 전달되지 않음 — Workflow(name, args) 로 질문을 넘기세요)'

const SPEC_SCHEMA = {
  type: 'object',
  properties: {
    universe: { type: 'array', items: { type: 'string' }, description: '분석할 기업명 목록(최대 10)' },
    tool: { type: 'string', description: '검증·개선 대상으로 선택한 OPM MCP tool 하나 (예: shareholder_meeting_notice, proxy_advise_before_meeting, dividend, treasury_share, financial_metrics, ownership_structure). 입력에 tool이 지정됐으면 그것을, 없으면 질문에 가장 맞는 tool을 고른다.' },
    tool_choice_reason: { type: 'string', description: '이 tool을 검증·개선 대상으로 고른 이유 (입력 지정이면 "사용자 지정", 자동이면 질문의 어느 부분이 이 tool을 가리키는지)' },
    tool_args: { type: 'object', description: 'scope/year/meeting_type 등 tool 인자', additionalProperties: true },
    constraints: { type: 'array', items: { type: 'string' }, description: '정합성 검사 기준이 될 사용자 제약 (예: "정기주총만", "2026년", "보수한도 상향만")' },
    budget_note: { type: 'string', description: 'tool_call_budget.md 기준 콜 수가 DART 분당 910 대비 안전한지, 위험하면 universe를 줄인 근거' },
    call_mode: { type: 'string', enum: ['per_firm', 'market_scan'], description: 'per_firm=유니버스 각 사에 호출(총 콜 = N × 기업당 콜). market_scan=시장 전체를 1회 쿼리하므로 유니버스 순회가 없다(예: risk_events를 company 미지정으로 시장 스캔 ~45콜/쿼리). 이 둘은 budget 계산이 완전히 다르다.' },
  },
  required: ['universe', 'tool', 'constraints', 'call_mode'],
}

const RESULT_SCHEMA = {
  type: 'object',
  properties: {
    company: { type: 'string' },
    ok: { type: 'boolean', description: 'tool 호출 성공 여부' },
    key_data: { type: 'object', description: '질문에 답하는 핵심 필드만 추출', additionalProperties: true },
    source_rcept: { type: 'array', items: { type: 'string' }, description: '근거 공시 접수번호' },
    raw_summary: { type: 'string', description: '결과 1~3줄 요약' },
  },
  required: ['company', 'ok', 'raw_summary'],
}

const INTEGRITY_SCHEMA = {
  type: 'object',
  properties: {
    checker: { type: 'string' },
    issues_found: { type: 'boolean' },
    period_ok: { type: 'boolean', description: '기간/주총유형이 제약과 맞나 (정기 요청인데 임시를 봤는지 등)' },
    label_ok: { type: 'boolean', description: '데이터 레이블이 올바른가' },
    value_ok: { type: 'boolean', description: '값이 타당한가 (단위·범위)' },
    citation_ok: { type: 'boolean', description: '인용 공시가 정확한가' },
    issues: { type: 'array', items: { type: 'string' }, description: '구체적 불일치 항목' },
  },
  required: ['checker', 'issues_found', 'issues'],
}

const EXPERT_SCHEMA = {
  type: 'object',
  properties: {
    role: { type: 'string' },
    accuracy: { type: 'string', description: '결과 정확성 평가' },
    usefulness: { type: 'string', description: '해당 역할 관점의 실무 효용' },
    caveats: { type: 'array', items: { type: 'string' }, description: '주의·빠진 맥락' },
    explanation: { type: 'string', description: '핵심 결과 해설' },
  },
  required: ['role', 'accuracy', 'usefulness', 'explanation'],
}

const NOTES_SCHEMA = {
  type: 'object',
  properties: {
    parser_tool: { type: 'array', description: '파서·tool 결함 (예: 정기/임시 오분류, 필드 누락, 종류주식 누락)',
      items: { type: 'object', properties: { tool: { type: 'string' }, issue: { type: 'string' }, evidence: { type: 'string' }, priority: { type: 'string', enum: ['high', 'medium', 'low'] } } } },
    budget_drift: { type: 'array', description: '실측 콜 수가 tool_call_budget.md 와 다른 tool',
      items: { type: 'object', properties: { tool: { type: 'string' }, observed: { type: 'string' }, note: { type: 'string' } } } },
    wiki_doc: { type: 'array', description: 'wiki 문서가 부정확·보강 필요한 부분',
      items: { type: 'object', properties: { file: { type: 'string' }, issue: { type: 'string' } } } },
    data_quality: { type: 'array', items: { type: 'string' }, description: '데이터 품질·공시 출처·정합성 패턴 이슈' },
    top_actions: { type: 'array', items: { type: 'string' }, description: '우선 실행 권장 개선 액션 (중요도순)' },
  },
  required: ['top_actions'],
}

// ── Phase 0: 스카우트 — 질문에서 실행 계획 추출 ──
phase('Scout')
const spec = await agent(
  `다음 검증 요청을 분석해 실행 계획을 만들어라.\n` +
  `요청: "${query}"\n` +
  (input.companies ? `대상 기업(고정): ${input.companies.join(', ')}\n` : `대상 기업이 명시되지 않았으면 요청 맥락에서 유니버스를 도출하되 최대 10사로 한정한다.\n`) +
  (input.tool ? `사용할 tool(고정): ${input.tool}\n` : ``) +
  `\n- universe: 분석할 기업명(최대 10)\n` +
  `- tool: 검증·개선할 OPM MCP tool 하나를 **명확히 선택**한다(입력에 지정됐으면 그것, 없으면 질문에 가장 맞는 것). tool_choice_reason에 고른 이유를 적는다.\n` +
  `- tool_args: scope/year/meeting_type 등\n` +
  `- constraints: 사용자가 요구한 제약을 명시적 리스트로 (이게 정합성 검사 기준이 된다. 예: "정기주총만", "2026년 안건", "보수한도 상향만")\n` +
  `\n[콜 budget 가드] Read 도구로 "wiki/tools/tool_call_budget.md"를 읽어라.\n` +
  `- 먼저 call_mode를 판정한다: 요청이 "정해진 기업들 각각"을 보는 것이면 per_firm, "시장 전체에서 조건에 맞는 기업 찾기"처럼 유니버스 순회 없이 한 번에 훑는 것이면 market_scan(예: risk_events를 company 없이 시장 스캔). market_scan tool은 budget의 'market_scan_per_query' 값을 쓰고 유니버스 곱셈을 하지 않는다.\n` +
  `- per_firm이면 (유니버스 수 × 기업당 콜)이 분당 910을 크게 넘는지 보고, 넘으면 universe를 줄여라.\n` +
  `- budget_note에 mode와 총 콜 추정을 기록한다. evidence는 0콜.`,
  { schema: SPEC_SCHEMA, phase: 'Scout', label: 'scout' }
)

const callMode = spec.call_mode || 'per_firm'
const tool = input.tool || spec.tool
const toolArgs = { ...(spec.tool_args || {}), ...(input.year ? { year: input.year } : {}) }
const constraints = spec.constraints || []
// market_scan(시장 전체 1회 쿼리)은 유니버스 순회가 없으므로 단일 항목으로 1회만 실행한다.
const universe = callMode === 'market_scan' ? ['(시장 전체 스캔)'] : (input.companies || spec.universe || []).slice(0, 10)
log(`tool=${tool} (${spec.tool_choice_reason || '선택'}) · mode=${callMode} · ${callMode === 'market_scan' ? '시장 전체 1회 쿼리' : universe.length + '사'} · 제약=${constraints.join(' / ')}`)

// ── Phase 1+2: 기업별 tool 실행 → 정합성 2인 적대적 검사 (pipeline, 무배리어) ──
const verified = await pipeline(
  universe,
  // stage 1: tool 실행
  (company) => agent(
    callMode === 'market_scan'
      ? `OPM MCP의 "${tool}"를 시장 전체 모드(company 미지정)로 1회 호출하라. 유니버스 순회 없이 시장 전체에서 조건에 맞는 결과를 가져온다. 인자: ${JSON.stringify(toolArgs)}. ToolSearch로 "mcp__claude_ai_open-proxy-mcp__${tool}"를 로드한 뒤 호출하고, 요청("${query}")에 답하는 결과를 key_data로 추출하라(여러 기업이 나오면 핵심만 정리).`
      : `OPM MCP의 "${tool}" tool을 "${company}"에 대해 실행하라.\n인자: ${JSON.stringify(toolArgs)}\nToolSearch로 "mcp__claude_ai_open-proxy-mcp__${tool}" 스키마를 로드한 뒤 호출하고, 요청("${query}")에 답하는 핵심 필드만 key_data로 추출하라. 근거 공시 접수번호도 담아라.`,
    { schema: RESULT_SCHEMA, phase: 'Batch', label: callMode === 'market_scan' ? `scan:${tool}` : `tool:${company}` }
  ),
  // stage 2: 정합성 검사관 2인 (적대적, 독립)
  (result, company) => {
    if (!result || !result.ok) return result ? { company, result, integrity: [] } : null
    return parallel([
      () => agent(
        `너는 파싱 정합성 검사관 A다. 아래 tool 결과가 요청 제약과 정확히 일치하는지 thoroughly 검증하라.\n` +
        `요청: "${query}"\n제약: ${JSON.stringify(constraints)}\n결과: ${JSON.stringify(result)}\n` +
        `반드시 점검: ① 기간/주총유형이 제약과 맞나(정기 요청인데 임시를 봤는지 등) ② 데이터 레이블이 올바른가 ③ 값이 타당한가(단위·범위·이상치) ④ 인용 공시가 실제 근거로 정확한가. 어긋난 점을 구체적으로 적어라.`,
        { schema: INTEGRITY_SCHEMA, phase: 'Integrity', label: `chkA:${company}` }
      ),
      () => agent(
        `너는 파싱 정합성 검사관 B다. 검사관 A와 독립적으로 같은 결과를 재검증하되, A가 놓칠 미묘한 불일치(필드 혼동, 누락, 시점 역전, 종류주식 누락)를 집중적으로 노려라.\n` +
        `요청: "${query}"\n제약: ${JSON.stringify(constraints)}\n결과: ${JSON.stringify(result)}`,
        { schema: INTEGRITY_SCHEMA, phase: 'Integrity', label: `chkB:${company}` }
      ),
    ]).then((checks) => ({ company, result, integrity: checks.filter(Boolean) }))
  }
)

const clean = verified.filter(Boolean)
const flagged = clean.filter((c) => (c.integrity || []).some((i) => i && i.issues_found))
log(`정합성 검사 완료: ${clean.length}사 중 ${flagged.length}사에 불일치 플래그`)

// ── Phase 3: 도메인 전문가 3인 검증·해설 (barrier — 전체 결과를 함께 봄) ──
phase('Expert')
const digest = JSON.stringify(
  clean.map((c) => ({ company: c.company, key_data: c.result.key_data, summary: c.result.raw_summary,
    flags: (c.integrity || []).flatMap((i) => (i && i.issues_found ? i.issues : [])) }))
)

const experts = await parallel([
  () => agent(
    `너는 대형 자산운용사의 스튜어드십(의결권 행사) 전문가다. 아래 검증 결과를 의결권 행사 실무 관점에서 평가·해설하라: 결과가 정확하고 신뢰할 만한가, 실제 의결권 판단에 쓸모 있는가, 주의할 맥락(법령·정책)은 무엇인가.\n` +
    `요청: "${query}"\n결과(정합성 플래그 포함): ${digest}`,
    { schema: EXPERT_SCHEMA, phase: 'Expert', label: 'expert:stewardship' }
  ),
  () => agent(
    `너는 행동주의 펀드 매니저다. 같은 결과를 거버넌스 공격 포인트·주주가치 관점에서 평가·해설하라: 행동주의 캠페인에 유효한 신호가 보이는가, 결과가 놓친 리스크나 기회는 무엇인가.\n` +
    `요청: "${query}"\n결과: ${digest}`,
    { schema: EXPERT_SCHEMA, phase: 'Expert', label: 'expert:activist' }
  ),
  () => agent(
    `너는 관련 산업의 재무 분석가다. 같은 결과의 수치·재무 맥락이 타당한지, 업종 평균·동종사 대비 의미가 무엇인지 평가·해설하라.\n` +
    `요청: "${query}"\n결과: ${digest}`,
    { schema: EXPERT_SCHEMA, phase: 'Expert', label: 'expert:analyst' }
  ),
])

// ── Phase 5: 개선점 노트 (정합성 플래그 + 전문가 지적 → OPM 개선점 도출) ──
phase('Notes')
const expertCaveats = experts.filter(Boolean).flatMap((e) => (e.caveats || []).map((c) => `${e.role}: ${c}`))
const allFlags = clean.flatMap((c) => (c.integrity || []).flatMap((i) => (i && i.issues_found ? i.issues.map((x) => `${c.company}: ${x}`) : [])))
const notes = await agent(
  `이번 검증에서 드러난 OpenProxy MCP(OPM)의 개선점을 도출하라. 결과 자체가 아니라 'OPM을 어떻게 고치면 좋은가'에 집중한다.\n` +
  `사용한 tool: ${tool} (call_mode=${callMode})\n` +
  `정합성 검사 플래그(${allFlags.length}건): ${JSON.stringify(allFlags)}\n` +
  `전문가 지적(caveats): ${JSON.stringify(expertCaveats)}\n` +
  `budget_note: ${spec.budget_note || '(없음)'}\n\n` +
  `다음으로 분류해 도출하라: (a) parser_tool — 파서·tool 결함(정기/임시 오분류, 필드 누락, 종류주식 누락 등) (b) budget_drift — 실측 콜 수가 wiki/tools/tool_call_budget.md 와 달라 보이는 tool (c) wiki_doc — 부정확·보강 필요한 wiki 문서 (d) data_quality — 데이터·공시 품질. 마지막에 top_actions로 우선 실행할 개선을 중요도순으로 정리하라. 근거 없는 추측은 넣지 마라.`,
  { schema: NOTES_SCHEMA, phase: 'Notes', label: 'notes' }
)

return {
  query,
  tool,
  tool_choice_reason: spec.tool_choice_reason,
  tool_args: toolArgs,
  constraints,
  call_mode: spec.call_mode || 'per_firm',
  budget_note: spec.budget_note,
  universe,
  results: clean,
  integrity_flags: flagged.map((c) => ({
    company: c.company,
    issues: (c.integrity || []).flatMap((i) => (i && i.issues_found ? i.issues : [])),
  })),
  expert_reviews: experts.filter(Boolean),
  improvement_notes: notes,
}
