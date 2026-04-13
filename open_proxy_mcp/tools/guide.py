"""OPM 통합 가이드 tool (tool_guide)

5개 도메인(AGM/OWN/DIV/PRX/CORP)의 tool 사용법과 규칙을 단일 진입점으로 통합.
domain="" → 전체 가이드, domain="agm"|"own"|"div"|"prx"|"corp" → 해당 섹션만.
"""


_GUIDE_TIER = """\
## Tier 체계 (실행 우선순위)

| Tier | 역할 | Tools |
|------|------|-------|
| 1 Entity | 기업 특정 | `corp_identifier` |
| 2 Context | 가이드 | `tool_guide` |
| 3 Search | rcept_no 획득 | `agm_search`, `proxy_search`, `div_search` |
| 4 Orchestrate | 종합 분석 | `agm_pre_analysis`, `agm_post_analysis`, `ownership_full_analysis`, `div_full_analysis`, `proxy_fight` |
| 5 Detail | drill-down | `agm_*_xml`, `ownership_major`, `div_detail`, `proxy_direction` … |
"""

_GUIDE_ORDER = """\
## 필수 실행 순서

1. **corp_identifier** — 기업 특정. **항상 첫 번째.**
2. **tool_guide** — 불명확하면 이 tool 읽기.
3. **`*_search`** — rcept_no 획득.

4. **오케스트레이터** — 종합 분석. 가능하면 여기서 끝내기.
5. **detail tools** — 사용자 drill-down 요청 시만.

> corp_identifier 없이 다른 tool을 호출하지 말 것.
> 오케스트레이터로 충분하면 detail tool 추가 호출 금지.

## 데이터 접근 우선순위

**반드시 아래 순서를 따를 것. 상위 소스로 해결되면 하위 소스 접근 금지.**

| 순위 | 소스 | 특성 | 병렬 호출 |
|------|------|------|----------|
| 1 | **DART API** | 공식 API. 빠르고 안정적 | ✅ 가능 (분당 1,000회 한도) |
| 2 | **DART 웹 크롤링** | PDF fallback 등 | ⚠️ 최소 2초 간격 |
| 3 | **KIND 크롤링** | 투표결과 등 DART에 없는 데이터 | ⚠️ 최소 2초 간격 |

- 다수 기업 배치 분석 시: **DART API 기반 tool을 병렬 호출**하고, KIND는 투표결과가 필요할 때만 순차 호출.
- agm_*_xml, agm_search, div_*, ownership_* → **DART API** (병렬 가능)
- agm_result → **KIND 크롤링** (투표결과 전용, 순차만 가능)

## rcept_no 포맷 규칙

rcept_no = `YYYYMMDD` + `TYPE(2)` + `SEQ(4)` (14자리)

| TYPE | 의미 | 예시 | 사용 tool |
|------|------|------|-----------|
| `00` | DART 정기공시 (소집공고 등) | 20260219002694 | agm_*_xml, agm_items |
| `80` | DART 거래소 수시공시 (주총결과 등) | 20260318801211 | agm_result 내부 전용 |

- **agm_search → rcept_no(00 포맷) → agm_*_xml**: 소집공고 파싱의 정규 흐름.
- **agm_*_xml에 80 포맷 rcept_no를 넣으면 에러.** 반드시 agm_search로 소집공고 rcept_no를 먼저 획득할 것.
- KIND acptno = DART 주총결과 rcept_no의 "80"→"00" 치환. 소집공고 rcept_no와는 완전히 다른 번호.
"""

_GUIDE_AGM = """\
## AGM (주주총회) 도메인

### Canonical Chain
```
corp_identifier → agm_search(ticker) → agm_pre_analysis(ticker)   [소집공고: 안건+재무+인사]
                                      → agm_post_analysis(ticker)  [소집공고+투표결과 통합]
                                      → agm_agenda_xml(rcept_no)
                                      → agm_personnel_xml(rcept_no)
                                      → agm_financials_xml(rcept_no)
                                      → agm_aoi_change_xml(rcept_no)
                                      → agm_compensation_xml(rcept_no)
                                      → agm_treasury_share_xml(rcept_no)
                                      → agm_capital_reserve_xml(rcept_no)
                                      → agm_retirement_pay_xml(rcept_no)
                  → agm_result(ticker)  [투표결과, KIND 크롤링]
```

### Tool 목록

| Tool | 입력 | 출력 |
|------|------|------|
| `agm_pre_analysis(ticker)` | ticker | 소집공고 기반 사전 분석 (안건+재무+인사) |
| `agm_post_analysis(ticker)` | ticker | 소집공고+투표결과 통합 사후 분석 |
| `agm_search(ticker)` | ticker, 기간 | rcept_no 목록 (정정공고 포함) |
| `agm_agenda_xml(rcept_no)` | rcept_no | 의안 트리 (제N호/제N-M호) |
| `agm_items(rcept_no, agenda_no)` | rcept_no, 안건번호 | 안건 원문 블록 |
| `agm_financials_xml(rcept_no)` | rcept_no | 재무제표 (BS/IS, 연결/별도) |
| `agm_personnel_xml(rcept_no)` | rcept_no | 이사/감사 후보자 경력·결격사유 |
| `agm_aoi_change_xml(rcept_no)` | rcept_no | 정관변경 대조표 (전/후/사유) |
| `agm_compensation_xml(rcept_no)` | rcept_no | 보수한도 + 소진율 |
| `agm_treasury_share_xml(rcept_no)` | rcept_no | 자기주식 수량/목적/방법 |
| `agm_capital_reserve_xml(rcept_no)` | rcept_no | 자본준비금 감소 |
| `agm_retirement_pay_xml(rcept_no)` | rcept_no | 퇴직금 규정 개정 |
| `agm_corrections(rcept_no)` | rcept_no | 정정 전/후 비교 |
| `agm_parse_fallback(rcept_no, parser, tier)` | rcept_no, 파서명, pdf/ocr | XML 실패 시 PDF/OCR |
| `agm_result(ticker)` | ticker | 투표결과 + 추정참석률 (KIND) |

### Fallback 흐름
```
agm_*_xml → AI 검증 (AGM_CASE_RULE 기준)
  SUCCESS  → 답변
  SOFT_FAIL → AI 자체 보정 시도
    보정 성공 → 답변
    보정 불가 → 유저에게 PDF fallback 제안
      유저 동의 → agm_*_pdf (4s+)
        여전히 부족 → 유저에게 OCR 제안
          유저 동의 → agm_*_ocr (Upstage API)
```
_pdf: DART 웹 PDF 다운로드 (4s+). _ocr: Upstage API (UPSTAGE_API_KEY 필요, 10s+).

### 파싱 한계

| 안건 유형 | 파서 | 한계 |
|-----------|------|------|
| 자기주식 | agm_treasury_share_xml | XML 제목 매칭 한계, PDF fallback 빈번 |
| 보수한도 | agm_compensation_xml | 이사/감사 별도 안건 가능, 단위 다양 |
| 퇴직금 | agm_retirement_pay_xml | 재무제표 주석 "퇴직급여"와 혼동 |
| 재무제표 | agm_financials_xml | 보고사항일 수 있음 (투표 없음) |
| 정관변경 | agm_aoi_change_xml | 하위 안건 분할 빈번, 생략/삭제 표기 정상 |
| 이사선임 | agm_personnel_xml | 경력 병합 시 PDF fallback. 감사위원 3% 제한 |

### 의결권 행사 판단 기준 (FOR / AGAINST / REVIEW)

- **재무제표**: 감사의견 적정→FOR, 한정/부적정→AGAINST, 배당성향<10%→REVIEW
- **이사선임**: 사외이사 최대주주 관계→AGAINST, 겸직 3+→REVIEW, 부정 뉴스→REVIEW
- **정관변경**: 집중투표 배제 삭제·전자주총→FOR; 이사 정원 축소·시차임기→REVIEW
- **보수한도**: 소진율<30%+인상→AGAINST; 30-70%→FOR; >70%→FOR; 50%+인상→REVIEW
- **자사주**: 소각→FOR; 경영권 방어·재단 출연→REVIEW
- **자본준비금**: 감액배당→FOR; 기타→REVIEW

### KIND 투표결과 & 참석률 역산
- KIND는 투표결과 전용. DART API에 없는 안건별 찬성/반대 데이터를 제공.
- DART 주총결과 rcept_no(80 포맷) → KIND acptno(00 포맷) 변환: 8번째 이후 "80"→"00"
- 참석률 = 발행기준 찬성률 / 행사기준 찬성률 (보통결의 기준). 감사위원 3% 제한 → 분모 다름.
- 배치 분석 시 KIND 호출 최소화: 보수한도·인사·정관 등은 DART API(agm_*_xml)로 병렬 처리하고, 투표결과가 필요한 경우만 agm_result 호출.

### 집중투표제 계산
- 1석 확보 최소 지분율 = 1/(N+1), N = 선임 이사 수. 유효주식수 = 발행주식 - 자사주.
- 2026-09-10: 자산 2조+ 상장사 집중투표 배제 불가 (상법개정). 분리선출 감사위원 1→2명.

"""

_GUIDE_OWN = """\
## OWN (지분구조) 도메인

### Canonical Chain
```
corp_identifier → ownership_full_analysis(ticker)  [종합 권장]
               → ownership_major(ticker, year)     [최대주주+특관인]
               → ownership_total(ticker, year)     [발행주식/자사주/소액주주]
               → ownership_treasury(ticker, year)  [자사주 취득방법별]
               → ownership_treasury_tx(ticker)     [자사주 이벤트]
               → ownership_block(ticker)           [5% 대량보유]
```

### Tool 목록

| Tool | 입력 | 출력 | API 호출 |
|------|------|------|----------|
| `ownership_full_analysis(ticker)` | 종목코드/회사명 | 사업보고서 vs 최신 공시 지분율 비교 | 5+ |
| `ownership_major(ticker, year)` | 종목코드/회사명, 연도 | 최대주주+특관인 (보통주 기준) | 1 |
| `ownership_total(ticker, year)` | 종목코드/회사명, 연도 | 발행주식/자사주/유통/소액주주 | 1 |
| `ownership_treasury(ticker, year)` | 종목코드/회사명, 연도 | 자사주 기초-취득-처분-소각-기말 | 1 |
| `ownership_treasury_tx(ticker)` | 종목코드/회사명 | 자사주 이벤트 (취득/처분/신탁) | 4 |
| `ownership_block(ticker)` | 종목코드/회사명 | 5% 대량보유자 + 목적 | 1+보고자 수 |

### 출력 형태 — 반드시 유지

헤더 카드 3개:
```
최대주주: **삼성생명보험㈜ 8.51%**    ← ownership_major
특관인 합계: **19.84%** (15명)       ← ownership_major 보통주 합산
자사주: **91,828,987주 (1.55%)**     ← ownership_total tesstk_co
```
주주 테이블 (4컬럼): `주주 | 구분 | 지분율 | 비고`

**차트/시각화 변환 금지. Markdown 테이블 그대로 출력.**

### 컬럼별 소스

| 컬럼 | 소스 | 필드 |
|------|------|------|
| 주주 | ownership_major | `nm` |
| | ownership_block | `repror` |
| 관계 | ownership_major | `relate` |
| 지분율 | ownership_major | `trmend_posesn_stock_qota_rt` |
| | ownership_block | `stkrt` |
| 기준날짜 | ownership_major | `stlm_dt` (결산일) |
| | ownership_block | `rcept_dt` (공시일) |
| 비고 | ownership_block | 보유목적 (경영권/단순투자/일반투자) |

### 데이터 소스 우선순위
1. 사업보고서 (ownership_major, ownership_total, ownership_treasury) — 연 1회, 결산일 baseline
2. 수시공시 (ownership_block, ownership_treasury_tx) — 변동 시 즉시, 사업보고서 이후 변동 반영
3. ownership_full_analysis — 1+2 합산 종합 분석

기준날짜가 다른 데이터 혼합 시 반드시 기준날짜 컬럼으로 구분. 지분율 1% 미만 생략 가능.
"""

_GUIDE_DIV = """\
## DIV (배당) 도메인

### Canonical Chain
```
corp_identifier → div_full_analysis(ticker)  [종합 = div_detail + 3년 추이]
               → div_search(ticker)        [공시 검색 → rcept_no]
               → div_detail(ticker, year)  [특정 연도 상세]
               → div_history(ticker, years) [연도별 추이]
```

### Tool 목록

| Tool | 입력 | 출력 | API 호출 |
|------|------|------|----------|
| `div_full_analysis(ticker)` | 종목코드/회사명 | 최신 상세 + 3년 추이 | div_detail + div_history |
| `div_search(ticker)` | 종목코드/회사명 | 현금배당결정/중간배당 공시 목록 | 1 |
| `div_detail(ticker, bsns_year, reprt_code)` | 종목코드/회사명, 연도, 보고서코드 | DPS/총액/배당성향/시가배당률 | 1 |
| `div_history(ticker, years)` | 종목코드/회사명, 연수 | 연도별 DPS/성향/수익률 | years×4 + years |

### 핵심 연산 규칙

**배당성향**: `배당금 총액 / 지배주주 귀속 당기순이익 × 100`
- 반드시 연결재무제표 지배주주 귀속 순이익 사용 (비지배지분 포함 금지)
- DART `alotMatter`에 배당성향 있으면 그 값 우선

**시가배당률**: `1주당 배당금 / 기준주가 × 100`
- 기준주가 = 배당기준일 전전거래일부터 1주일 종가 산술평균
- DART 제공값 우선. 없으면 네이버 종가로 자체 계산

**DPS 소스 주의**:
- `alotMatter` → 연간 합산 DPS
- 현금배당결정 공시 → 해당 회차분만 (분기배당 각 회차)
- 분기배당 합산: 1Q + 반기 + 3Q + 기말 = 연간

### 우선주 매핑 (시장 호칭 → 공시 표기)

| 시장 호칭 | 공시 표기 | 비고 |
|-----------|----------|------|
| {회사}우 | {회사}우 또는 우선주 | 구형(1우) |
| {회사}2우B | 2우선주 또는 {회사}2우B | 신형 |
| 1종우선주 | 제1종우선주식 | CJ제일제당 등 |
| 전환우선주 | {회사}N우(전환) | CJ4우 등 |

유저가 "우선주"만 지정 → 구형(1우) 기준.

### 배당 날짜 구분

| 날짜 | 의미 |
|------|------|
| 결산일 (stlm_dt) | 사업연도 종료일 |
| 배당기준일 | 배당 수령 주주 확정일 |
| 이사회결의일 | 배당 결정일 |
| 배당금지급 예정일 | 실제 지급일 (주총 후 1개월 이내) |

div_history: 연도당 최대 4회 DART API 호출 (기말+3분기). 3년 = 최대 12회.
"""

_GUIDE_PRX = """\
## PRX (위임장) 도메인

### Canonical Chain
```
corp_identifier → proxy_fight(ticker, year)      [프록시 파이트 감지 + 비교, 권장]
               → proxy_search(ticker, year)       [rcept_no 목록]
                    → proxy_direction(rcept_no)   [안건별 행사방향]
                    → proxy_detail(rcept_no)      [권유자 상세]
               → proxy_litigation(ticker, year)   [소송/분쟁 타임라인]
```

### Tool 목록

| Tool | 입력 | 출력 | API 호출 |
|------|------|------|----------|
| `proxy_search(ticker, year)` | 종목코드/회사명, 연도 | rcept_no + 회사측/주주측 구분 | 1 |
| `proxy_detail(rcept_no)` | rcept_no | 권유자 보유주식, 권유기간, 전자위임장 | 1 |
| `proxy_direction(rcept_no)` | rcept_no | 안건별 찬성/반대/기권 | 1 |
| `proxy_fight(ticker, year)` | 종목코드/회사명, 연도 | 프록시 파이트 감지 + 양측 비교 | 1+권유자 수 |
| `proxy_litigation(ticker, year)` | 종목코드/회사명, 연도 | 소송등의제기/판결 타임라인 + 원문 3건 | 2+3 |

### 검색 방법
OpenDART `list.json`에서 `pblntf_detail_ty` 파라미터 미지원.
→ corp_code + 날짜범위 전체 검색 후 `report_nm`에서 "의결권대리행사" / "위임장권유" 필터.

### 회사측 vs 주주측 구분
`flr_nm`(제출인) == corp_name → 회사측. 다르면 주주측 (행동주의 펀드/기관투자자).

### 행사방향 파싱 위치 및 한계
- 위치: Section II-1 "의결권 대리행사의 권유를 하는 취지" (자유서술)
- 정규식 패턴: `제N호 + 찬성/반대/기권` (양방향)
- 불명확한 경우 "불명" 반환 → `proxy_detail`로 원문 직접 확인 후 AI 판단

### 문서 구조

```
의결권대리행사권유참고서류
├── I. 권유에 관한 사항 (권유자 보유주식, 권유기간, 대리인)
├── II. 권유의 취지
│   ├── 1. 권유 취지 ← proxy_direction 파싱 위치
│   └── 2. 위임 방법 (전자위임장/서면)
└── III. 주총 목적사항별 기재사항
    └── 회사측: 재무제표 전문 + 후보자 경력 / 주주측: 제목만
```

권유 비용은 별도 공시 "의결권대리행사권유신고서"에 있음 (proxy_detail 범위 밖).
"""

_GUIDE_VUP = """\
## VUP (기업가치제고) 도메인

### Canonical Chain
```
corp_identifier → value_up_plan(ticker, year)   [기업가치제고계획 공시 검색 + 원문]
```

### Tool 목록

| Tool | 입력 | 출력 | API 호출 |
|------|------|------|----------|
| `value_up_plan(ticker, year)` | 종목코드/회사명, 연도 | 기업가치제고계획 공시 목록 + 최신 2건 원문 | 1+2 |

### 검색 방법
DART pblntf_ty=I(거래소공시) 검색 후 "기업가치제고" / "밸류업" 키워드 필터.

### 활용
거버넌스 분석의 핵심 체인 중 하나. governance_report, div_full_analysis, ownership_full_analysis와 함께 사용하면 기업의 주주환원 의지와 실행력을 종합 평가할 수 있음.
"""

_GUIDE_CORP = """\
## CORP (기업 식별자) 도메인

### Tool 목록

| Tool | 입력 | 출력 |
|------|------|------|
| `corp_identifier(query)` | 종목코드/회사명/영문명/약칭 | corp_code, stock_code, 영문명, 법인번호, 시장/업종, 대표이사 등 |

### 지원하는 입력 타입

| 입력 | 예시 | 동작 |
|------|------|------|
| 종목코드 (6자리) | `005930` | 정확 매치 |
| DART corp_code (8자리) | `00126380` | 정확 매치 |
| 한글 회사명 | `삼성전자` | 정확→부분 매치 |
| 약칭/브랜드명 | `TKG휴켐스`, `KT&G` | alias dict → DART 정식명 |
| 영문명 | `LS ELECTRIC` | alias dict → `엘에스일렉트릭` |
| 법인격 포함 | `삼성전자㈜` | 법인격 strip 후 매치 |

### 알려진 alias 매핑

| 입력 | DART 정식명 |
|------|------------|
| LS ELECTRIC | 엘에스일렉트릭 |
| SK바이오팜 | 에스케이바이오팜 |
| KT&G | 케이티앤지 |
| TKG휴켐스 | 티케이지휴켐스 |

### 데이터 소스 체인
1. DART corpCode.xml — corp_code, stock_code, corp_name
2. DART company.json — 영문명, 법인번호, 사업자번호, corp_cls, 대표이사, 결산월, 업종코드
3. NAVER 금융 — 업종명 (stock_code 있을 때만, 4초 추가)

### 동명기업 처리
modify_date 최신 + 상장 기업 우선으로 첫 번째 선택. 결과 하단에 전체 목록 표시.
특정 법인을 선택하려면 종목코드 또는 corp_code 직접 입력.

예: `미래에셋증권` → 2개 (006800이 최신 선택)
"""

_SECTION_MAP = {
    "agm": _GUIDE_AGM,
    "own": _GUIDE_OWN,
    "div": _GUIDE_DIV,
    "prx": _GUIDE_PRX,
    "vup": _GUIDE_VUP,
    "corp": _GUIDE_CORP,
}

_GUIDE_FULL = "# OPM Tool 실행 가이드\n\n" + "".join([
    _GUIDE_TIER,
    _GUIDE_ORDER,
    _GUIDE_AGM,
    _GUIDE_OWN,
    _GUIDE_DIV,
    _GUIDE_PRX,
    _GUIDE_VUP,
    _GUIDE_CORP,
])


def register_tools(mcp):

    @mcp.tool()
    async def tool_guide(
        domain: str = "",
    ) -> str:
        """desc: OPM tool 실행 가이드 — Tier 체계, 필수 실행 순서, 도메인별 Canonical Chain, 파싱 한계, 의결권 판단 기준.
        when: [tier-2 Context] 어떤 tool을 어떤 순서로 써야 하는지 불명확할 때. 첫 호출 또는 에러 발생 시 먼저 읽기.
        rule: DART API를 호출하지 않음. domain="" → 전체 가이드, domain="agm"|"own"|"div"|"prx"|"vup"|"corp" → 해당 섹션만.
        ref: corp_identifier, agm_manual, own_manual, div_manual, prx_manual, corp_manual

        Args:
            domain: 도메인 필터. "" (전체), "agm", "own", "div", "prx", "corp"
        """
        d = domain.lower().strip()
        if d in _SECTION_MAP:
            return _SECTION_MAP[d]
        if d == "":
            return _GUIDE_FULL
        valid = ", ".join(f'"{k}"' for k in _SECTION_MAP)
        return f'알 수 없는 domain: "{domain}". 유효한 값: {valid}, "" (전체)'
