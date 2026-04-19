# OpenProxy MCP v2

한국 상장사 거버넌스·주주환원 분석을 MCP로 제공하는 v2 서버. 애널리스트 관점 data tool 중심.

브랜치: `release_v2.0.0` — 배포 준비 완료 단계.

---

## 핵심 설계 원칙

| 원칙 | 구체화 |
|---|---|
| **사실 vs 정책 분리** | `dividend`(실지급)와 `value_up`(미래 약속)을 별도 tool로 둠. `treasury_share`는 사실. |
| **자동 분류 대신 힌트** | `proxy_contest`는 "분쟁"을 binary로 판정하지 않음. `has_contest_signal` + filer 교차 참조 플래그(5%경영참여/소송연관)로 LLM·애널리스트가 종합 판단 |
| **소액주주 플랫폼 분리** | 컨두잇(ACT)/헤이홀더/비사이드코리아 filer를 `retail_activism`으로 별도 식별 — 일반 경영권 분쟁과 혼동 방지 |
| **상장사 전용 유니버스** | `company`에서 비상장 법인 자동 제외 |
| **증빙 중심 설계** | `EvidenceRef` 스키마: `rcept_no`, `rcept_dt`, `report_nm`, `viewer_url` — 모든 결과에 인용 링크 |
| **3단 fallback** | 구조 파싱 실패 시 DART viewer HTML crawl → raw text 발췌까지 보장. PDF 다운로드 없이 완결 |
| **scope 기반 점진 로드** | 6개 data tool이 scope별 세부 제어 — 필요한 것만 가져와 context 부담 최소화 |

---

## Tool 구성 (총 11개)

### Data Tools (7)

| tool | 한 줄 설명 | 주요 scope |
|---|---|---|
| **`company`** | 기업 식별 + 최근 공시 인덱스 허브 (모든 v2 tool의 공통 입구) | — |
| **`shareholder_meeting`** | 정기주총/임시주총 안건·이사후보·보수한도·정관변경·결과 | summary / agenda / board / compensation / aoi_change / results / full |
| **`ownership_structure`** | 최대주주·특수관계인·5% 대량보유·자사주 현재 잔고 | summary / major_holders / blocks / treasury / control_map / timeline |
| **`dividend`** | 실지급 배당 사실 (DPS/총액/배당성향/시가배당률/추이) | summary / detail / history / policy_signals |
| **`treasury_share`** | 자기주식 취득·처분·소각·신탁 이벤트 | summary / events / acquisition / disposal / cancelation / annual |
| **`proxy_contest`** | 위임장·소송·5% 경영참여 시그널 + 교차 힌트 | summary / fight / litigation / signals / timeline / vote_math |
| **`value_up`** | 기업가치제고계획·주주환원 정책 약속 + 이행 교차참조 | summary / plan / commitments / timeline |
| **`evidence`** | 인용 정보 제공 (rcept_no → 공시일/소스/viewer_url 유도, API 호출 0) | — |

### Action Tools (3, phase-2)

| tool | 결과물 | upstream |
|---|---|---|
| **`prepare_vote_brief`** | 투표 메모 (회차·지분·안건·결과) | shareholder_meeting + ownership_structure + evidence |
| **`prepare_engagement_case`** | engagement 메모 (지배구조·분쟁·밸류업) | ownership_structure + proxy_contest + value_up + evidence |
| **`build_campaign_brief`** | 캠페인 사실 브리프 (timeline·players·flags) | proxy_contest + ownership_structure + shareholder_meeting + evidence |

---

## 공통 응답 스키마

모든 tool은 `ToolEnvelope` 포맷 반환 (md/json 공용):

```json
{
  "tool": "shareholder_meeting",
  "status": "exact|ambiguous|partial|conflict|requires_review|error",
  "subject": "삼성전자",
  "generated_at": "2026-04-19T...",
  "warnings": [...],
  "data": { "...": "tool별" },
  "evidence_refs": [
    {
      "evidence_id": "ev_notice_20260312000987",
      "source_type": "dart_xml|kind_html|dart_api|...",
      "rcept_no": "20260312000987",
      "rcept_dt": "2026-03-12",
      "report_nm": "[기재정정]주주총회소집공고",
      "viewer_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260312000987",
      "section": "주주총회 소집공고",
      "note": "회의일 2026년 3월 18일"
    }
  ],
  "next_actions": [...]
}
```

---

## 소스 정책

우선순위:
1. **DART OpenAPI** (구조화 API)
2. **DART `document.xml`** (공시 본문)
3. **DART viewer HTML crawl** (구조 파싱 재시도용)
4. **KIND HTML** (whitelist 공시만: 주총결과 등)
5. **Naver** (업종·섹터 보강만)
6. **raw text fallback** (위 전부 실패 시)

**제외**: PDF 다운로드 (무거움·신뢰도 낮음). KIND 화이트리스트 밖 공시.

---

## 3단 fallback 예시 (`shareholder_meeting`)

```
1. DART API/XML 구조 파싱
   └─ 성공 → scope별 구조화 데이터 반환

2. DART viewer HTML crawl + 재파싱
   └─ 성공 → 보정된 구조 데이터 반환 + warning

3. raw text fallback (viewer text가 더 풍부하면 대체)
   └─ status=requires_review + data.raw_text_excerpt (최대 6,000자)
   └─ LLM/애널리스트가 원문 직접 해석
```

삼천당제약·펩트론 같은 비표준 XML 공시도 raw text로 답변 가능.

---

## 주요 특이 케이스 처리

### 결산배당 사업연도 버킷팅
한국 결산배당은 사업연도 말일에 귀속되지만 공시는 다음 해 2-3월 제출. 기준일 분리형(2024~)은 record_date가 3-4월로 밀리기도 함.
→ `dividend`가 `dividend_type=결산배당`이면 **사업연도 = rcept_dt 연도 - 1** 규칙 적용.

### 소액주주 집단 위임 플랫폼
컨두잇(ACT)·헤이홀더·비사이드코리아 filer는 `side="retail_activism"`으로 분리.
→ `shareholder_side_count`, `has_contest_signal`에서 제외. 분쟁 false positive 방지.

### meta_amendment 공시 (value_up)
"고배당기업 표시를 위한 재공시"는 본문이 얇아 commitment 추출 불가.
→ `latest_plan` 필드에 실제 계획 본문 공시를 별도 노출.

### for_cancelation 자사주 취득 (treasury_share)
취득결정 공시 `aq_pp`에 "소각" 포함 시 `for_cancelation=True`.
→ 별도 "자기주식소각결정" 공시 없이 취득단계에서 소각 의도 밝히는 기업(예: 미래에셋증권)도 포착.

---

## 바로 가기

- [아키텍처](../../wiki/analysis/release_v2-tool-아키텍처.md)
- [검증 매트릭스](../../wiki/analysis/release_v2-public-tool-검증-매트릭스.md)
- [신규 tool 검증 정책](../../wiki/decisions/tool-추가-검증-정책.md)
- [DART-KIND 매핑 화이트리스트](../../wiki/decisions/DART-KIND-매핑-화이트리스트-2026-04.md)
- [공시 분류 체계](../../open_proxy_mcp/dart-kind-disclosure-taxonomy.md)

---

## 배포

환경변수로 v1/v2 토글:

```bash
# v2 전용 (권장)
OPEN_PROXY_TOOLSET=v2

# v1+v2 병행
OPEN_PROXY_TOOLSET=hybrid

# v1만 (default, 하위호환)
OPEN_PROXY_TOOLSET=v1
```

로컬 실행 + 원격 MCP 노출:

```bash
OPEN_PROXY_TOOLSET=v2 .venv/bin/python -m open_proxy_mcp.server \
    --transport streamable-http --toolset v2
# 추가 호스트 허용 (ngrok 등):
FASTMCP_ALLOWED_HOSTS="example.ngrok-free.dev" \
    OPEN_PROXY_TOOLSET=v2 .venv/bin/python -m open_proxy_mcp.server ...
```

---

## v1 → v2 마이그레이션

v1 (36 tool) → v2 (11 tool)로 재구성:

| v1 | v2 |
|---|---|
| `corp_identifier` | `company` |
| `agm_search` + `agm_items` + 다수 `agm_*_xml` | `shareholder_meeting(scope=...)` |
| `ownership_major` + `ownership_block` + `ownership_full_analysis` | `ownership_structure(scope=...)` |
| `ownership_treasury` + `ownership_treasury_tx` | `treasury_share` + `ownership_structure(scope="treasury")` |
| `div_detail` + `div_full_analysis` | `dividend(scope=...)` |
| `proxy_fight` + `proxy_litigation` + `proxy_full_analysis` | `proxy_contest(scope=...)` |
| `value_up_plan` | `value_up(scope=...)` |
| `governance_report` | `prepare_vote_brief` / `prepare_engagement_case` / `build_campaign_brief` |

v1 문서: [v1 README](../v1/README.md)
