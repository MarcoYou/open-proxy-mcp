---
type: log
title: Operation Log
---

## [2026-07-24] feat | 정기보고서 구간 슬라이싱 정확도 개선
- business_details의 사업의내용/주석 구간 분리를 개선해 부문 추출 커버리지 향상(기존 정답 상실 0, 검증 표본 전수 회귀). 상세·검증 방법은 private lessons.
- 추가 필드(사업장·가동률·R&D·수주·고객 등) 소절 선택 정밀화 — 응답 노이즈 축소·인접 소절 회수(전수 회귀: 상태 상실 0, 210 tests). 상세는 private lessons.
- 부문표 값 판독 정밀화 — 표를 구조 단위로 재판독해 연도 간 지표 일관성·영업이익 커버리지 개선(전수 회귀: 상실 0, 216 tests). 상세는 private lessons.

## [2026-07-23] docs | 공시 원문 구조 조사
- 파싱 정확도 개선을 위한 공시 원문 구조 조사 수행. 산출물은 private wiki로 이관(260724).

## [2026-07-23] fix | fresh-eye 리뷰 P0 2건 — 회차 resolver 미래 회의일 + 검산 게이트 오답-인증 차단
- 독립 리뷰어 4명(수정 4건 각 1명, 세션 맥락 차단)의 적대적 리뷰에서 CRITICAL 1 + HIGH 3 확정 (재현·코드 근거 첨부, 주요 지적은 메인 세션이 재검증).
- **P0-1 (CRITICAL)**: `resolve_latest_meeting_year`의 meeting window end=today라 **회의일이 미래인 공고**(소집 후~주총 전 = 본래 사용 구간)가 필터 탈락 → 주총 시즌 기본 호출이 작년 회차를 "최신 공고 기준"이라는 거짓 근거로 선택. 7월 라이브 검증(전부 회의 종료 후)이 못 잡은 사각. window end를 today+90일(_NOTICE_LEAD_BUFFER_DAYS)로 확장, 엣지 단위테스트 6건(미래 회의일·연말 경계·파싱 실패·auto 타입 경쟁·무공고·오타입) 신설. 부수: 조회 실패를 '공고 없음'으로 위장하던 bare except 분리(`mode=resolve_error`), resolver 선택 공고 ≠ payload 실분석 공고 시 `notice_mismatch` 명시.
- **P0-2 (HIGH×2, 재현 확인)**: ① 누적검산이 헤더에서 탈락한 부문의 **양수 매출을 조정으로 흡수**해 오정렬 표가 "검산 통과" 인증 획득(구코드는 안전 강등하던 방향성 회귀) → 흡수를 음수(내부거래 제거 성격) 최대 2회로 한정. ② 금액/비중 교차표에서 비중 열 dash(% 마커 없음)가 금액 0.0으로 오인 → 스트림에 % 셀 감지 시 dash_zero 자동 비활성(보수 후퇴 — 부분수집은 게이트가 강등). 리뷰어 재현 3건 + 흡수 상한을 회귀 테스트로 고정(총 196 tests).
- 코퍼스 재검증(10사×FY23-25, 캐시): 정형 OK 10→13 (SK이노 FY23·CJ FY24 신규 — 머지된 앵커/te-tu 효과 유지), **기존 정답 상실 0** (한화 9부문·삼성·현대차·두산 전부 유지), SK이노 FY24 오답 차단 유지, 크래프톤 FY25는 예고된 안전측 강등.
- 잔여(P1, 미수정): `_norm_seg_name` replace 순서 잔여("○○사업부"→"○○부" — 매핑 miss·시계열 탈락), excluded_years 사유 뭉개기, FY-2 fallback 과광폭, substring 짧은명 오매칭 방지. 후속 개선 방향은 private에 기록.

## [2026-07-23] feat | 사내이사 담당부문 성과 참고 fact (Phase 1, 점수 미반영)
- 배경: director_performance 매트릭스는 전사 지표(ROE·부채비율·CSR)만 봐서, 부문장 출신 사내이사가 본인이 책임지지 않은 부문 실적으로 감점됨 (260723 LG화학 김동춘 — 첨단소재 라인 재직인데 전사 ROE로 '부진 1/12'). 사용자 결정: 부문별 성과를 보되 Phase 1은 **참고(점수 미반영)** 노출만 — order_signal·영업이익률과 동일 패턴, decision 미개입.
- `services/director_segment_signal.py` 신설: 후보의 이 회사 경력 텍스트(faithfulness.career_company_groups + main_job) → business_details segments 부문명 보수적 매핑. **정확히 1개 부문 매칭일 때만** 부착 (0개/복수=ambiguous/타사 부문장/전사 CEO는 전부 skip + status 기록 — 오매핑은 miss보다 나쁨). 부문명 정규화는 `business_details._norm_seg_name` 재사용(단일 소스).
- 콜 게이트 2단: ① 부문장류 커리어 키워드 없으면 fetch 0 ② 최신 사업연도 1개 먼저 fetch → 정형 고신뢰(status=OK)+매핑 성공일 때만 과거 2개년 추가 (+2 payload). NEEDS_REVIEW 마크다운 폴백은 쓰지 않음. 주총 시즌 FY(회차-1) 사업보고서 미공시 대비 FY(회차-2) fallback 1회. `_safe` 캐시 키에 bsns_year 추가(연도별 조회 충돌 방지).
- 시계열에서 빠진 연도는 `excluded_years`로 명시 렌더("FY2023/2024는 부문표 정형 추출 저신뢰로 제외") — 부문 부재로 오독 방지. 실측: LG화학 김동춘 → 첨단소재 mapped, FY2025 매출 4.06조·영업이익 1,464억(백만원 단위), FY23-24는 저신뢰 제외 표기. 삼성전자 김용관 → no_division_career 정상 skip. 단위테스트 10건(매핑 엣지케이스: 복수부문 ambiguous·타사 부문장·엘지화학 표기 변형·stopword) 포함 전체 175 tests 통과.
- 안정성 검증(10사×FY23-25): 부문명은 정형 OK 연도끼리 안정(현대차 3/3·두산 4/4 정규화 겹침) — 연도 간 변동은 정규화가 흡수. 병목은 정형 추출률(FY23 1/10·FY24 4/10·FY25 5/10). 오매핑 0건. SK이노 FY2024 부분추출(배터리·석유 2개만 OK) 의심 → 별도 태스크 분리.
- **fallback 첨부(사용자 결정 — "매칭 안 되면 마크다운 통으로")**: (A) 정형 OK+매핑 실패(no_match/ambiguous) → 부문표 전체를 구조화 표로 회사 단위 첨부 (현대차 이승조 케이스). (B) 정형 저신뢰 → 영업부문 주석 마크다운 or raw_candidates 상위 표 파이프격자 첨부 — CJ제일제당 윤석환(BIO사업부문 대표) 케이스에서 3개년 부문표 원문이 실려 호출측 AI가 직접 읽음. 두 경우 모두 **추가 DART 콜 0**(이미 fetch한 payload 재사용), `data.segment_reference`로 노출.
- **발췌 길이 파라미터 `segment_context_chars`(기본 8000·clamp 1000~30000, 사용자 결정)**: 잘리면 응답에 "전체 N자 중 M자" + 재조회 경로 2개(① business_details 직접 조회 — 콜 절약 권장 ② 파라미터 증액 재호출) 안내 → 호출 AI가 자가조정. business_details context_chars 관례 재사용, cap은 proxy_advise 응답 대형이라 3만으로 보수. 라이브 검증: CJ 2,032자 md를 1000 cap으로 강제 잘림 → truncated/full_length/안내문 정상.

## [2026-07-23] fix | proxy_advise year=0 회차를 최신 소집공고 기준으로
- 종전 `year 미지정 = 달력 전년` 하드코딩은 tool의 사용 시점 정의("소집공고 후 ~ 주총 직전")와 모순 — 주총 시즌(2~3월) 기본 호출 시 1년 묵은 회차를 분석했고, notice tool의 auto(최신 공고)와도 불일치했다 (260723 LG화학 dogfooding에서 발견: notice=2026 회차, advise=2025 회차).
- `shareholder_meeting.resolve_latest_meeting_year()` 신설 — 최신 소집공고(12개월 lookback, 요청 meeting_type 그대로) pre-resolution으로 회의연도·정기/임시·phase를 확정. 비용 list.json +1 (doc 파싱은 `get_document_cached` 캐시를 본 payload가 재사용). 공고 미발견 시 종전 전년 fallback + warning.
- UX: 응답에 `year_resolution`(선택 근거·정기/임시 명시)과, 이미 종료된 회차면 `meeting_closed_hint`("사후 복기용 — 이후 임시주총 확인할까요?") 동봉. fin_reference(FY=회차-2) 매핑은 확정된 회차 기준으로 동일 유지.
- 샘플 검증 6케이스(LG화학·삼성전자·신영증권 3월결산·고려아연 annual/auto·크래프톤) 전부 2026 최신 회차 정확 선택, LG화학 end-to-end에서 year=2026·안건 17·힌트 노출 확인. 전체 165 tests 통과.

## [2026-07-23] docs | business_details field 계약 검증
- runtime `BUSINESS_DETAILS_FIELDS`를 기준으로 tool wiki `scope`와 한글·영문 기능 문서의 명시적 field 계약을 비교하는 `check_documentation_contract.py`를 추가했다. README는 각 언어별 상세 기능 문서 링크 존재를 함께 확인한다.
- 문서 전용 변경도 CI에서 이 검사가 실행되게 해 새 field가 runtime에는 있으나 사용자 문서에는 빠지는 drift를 차단한다. 일반 문장 정규식 대신 좁은 선언부만 검사해 서술 자유도는 유지한다.

## [2026-07-23] feat | business_details strict + candidate 문맥
- 기본 경로는 구조 헤딩 경계 `strict`로 유지하고, strict `NOT_COLLECTED`인 단일 표준 필드에 한해 저신뢰 고정 창 문맥을 `candidate_context`로 별도 반환한다.
- `context_chars`는 기본 20,000자, 최대 60,000자다. candidate는 공식 `SUCCESS`·hint·자동 비교에 사용하지 않으며 호출 AI의 재탐색 전용이다.
- 같은 300사·1,500필드 통제 비교에서 fixed-only `SUCCESS` 44건 중 43건은 strict 명시적 N/A, 1건은 미수집으로 확인됐다. strict 처리율 92.8%가 fixed 92.4%보다 높아 strict 기본을 유지했다.
- 호출 AI가 넓은 고정창 원문에서 다른 소절을 배제해 요약할 수 있다는 점은 인정하되, 이는 공식 추출이 아닌 문맥 제공 계약이다. 별도 `anchored_context` 응답 타입이 필요한 시점까지 현재 strict/candidate 분리를 유지한다.

## [2026-07-22] fix | business_details 실패 전수검사 + 앵커 정확도 보강
- 로컬 300사(KOSPI 169/KOSDAQ 131) 5필드의 최초 `NOT_COLLECTED` 132건을 원문으로 전수 판정하고, 수정 후 상태 전환 사례도 추가 육안 검사했다.
- 생산·가동, R&D, 수주, 고객, 사업장 제목 변형과 강한 회사 행동문을 보강해 실제 정보 51건을 회수하고 명시적 N/A 13건을 교정했다. `소수주주권`의 문자열 충돌과 위치 없는 유형자산표 오인을 차단했다.
- 최종 1,500 슬롯: `SUCCESS` 1,245(83.0%), `NOT_APPLICABLE` 147, `NOT_COLLECTED` 108, 처리율 92.8%, 예외 0. 잔여는 실제 부재·무관 또는 원천 불일치로 전부 분류했다. 외부 DART 호출 0, 157 tests 통과.

## [2026-07-22] improvement | 한글·영문 통합 CompanyResolver
- DART `corpCode.xml`의 공식 영문명을 memory/SQLite master에 보존하고 구형 4컬럼 DB를 additive migration 후 1회 갱신한다. 신규 master는 전체·종목코드·영문 커버리지 검증 후에만 교체하며, 갱신/파싱 실패 시 stale 한글 cache로 fail-open한다.
- 종목코드 보유 법인만 공식명·정규화·compact·token index를 만들고, ticker/corp_code는 전체 법인 exact map으로 유지한다.
- curated historical alias와 공식명을 시총보다 우선하고, 부분 브랜드명은 시총 최상위 후보를 자동 추론한다. 1.5배 격차 미만은 low confidence + 대안을 표시하되 되묻지 않는다. strong 공식명 충돌만 후보 순서로 반환한다.
- 실제 DART 118,511사에서 `삼성/Samsung`, `Samsung Fire`, `HD Hyundai Electric`, `KT and G`, `Hyundai`, 한영 혼합 질의를 확인했다. cold 다운로드+index 2.1s, warm p95 0.04ms. 안내문은 짧은 한·영 두 버전으로 두고 호출 AI가 `language=ko|en`으로 선택한다.

## [2026-07-22] refactor | toolset 버전 제거 + 테스트·문서 드리프트 경계
- 현행 `tools_v2/`를 `tools/`로 변경하고 v1 wrapper와 `OPEN_PROXY_TOOLSET` 분기를 제거했다.
- 현행 서비스가 쓰던 구 parser helper를 `services/` 전용 모듈로 분리하고 서비스 파일명의 `_v2`도 제거했다.
- `pytest` 기본 수집을 `tests/`로 고정하고 Python CI 및 runtime↔wiki tool catalog 검사를 추가했다.
- 구 `test/` 로컬 자료와 legacy tools snapshot은 `~/Backups/open-proxy-mcp-legacy-20260722`에 압축 백업 후 작업공간에서 제거했다.

## [다이제스트] index.md '주요 변화' 이관 보존 (260709)

> index.md가 라우팅 인덱스로 순수화되면서(패널 검수 260709), 그 안에 있던 시간순 서사·rationale
> 다이제스트를 여기로 이관 보존. 아래는 이관 시점 원문 그대로 — 각 항목 상세는 이 로그의 해당
> 날짜 엔트리 및 링크된 lessons/decisions 페이지 참조.

### 주요 변화 (2026-07-07 ~ )
- **자산가치 스콥 하드닝 + wiki 구조 재편 (2026-07-19~20)** — `business_details`의
  `real_estate`·`equity_holdings`: 토지 시그니처를 유형자산 **변동표(기초~기말 롤포워드)** 레이아웃까지
  확장(전수 회귀 0·복구 187), region 단위선언 유실 시 **근접 지배 단위 백필**(단위 always-carry),
  프리필터+strip 공유로 1.5x(회귀 0). `dart/client.py`에 타법인출자현황 API(`otrCprInvstmntSttus`)
  래퍼 추가. wiki는 lessons 등 내부 노하우 문서를 private 저장소로 재배치하고 운용사 익명화 정합을
  보강(전수 감사 기반) — public에는 툴 문서·도메인 지식·가이드라인 유지.
- **상법 시행일 SSOT + 근거 조문·시행령 정확화 + 주총일 게이트 (2026-07-09)** — 시행일이 md·엔진에
  손으로 중복 기입돼 한 곳만 고치면 나머지가 조용히 어긋나던 사고(A2-5: 신주배정금지 룰이 3월 아닌
  9월부터 발화)를 구조적으로 차단. **`law_provisions.json`(신규 SSOT — 상법 개정 조항 대장)**: 조문번호·
  시행일·공포일·유예도래일·자산 티어·시행령 임계출처·first_agm_trigger. `gen_law_timeline.py`가 md
  시행 타임라인 표를 자동생성, `wiki_lint [7a-d]`가 표·엔진 날짜·발화게이트(A2=시행일/A1=공포·시행)·
  근거 조문까지 원본과 3자 정합 강제(CI 차단). 사실정정(권위출처 legalize-kr 부칙·조문 대조):
  명칭변경 2026-07-23·감사위3% 1차·자사주소각 §341조의4제1항. proxy_advise는 법 적용을 today→**주총일**
  기준으로(시행 전 주총 오발화 방지), law-layer hit 근거에 조항 대장 상세(유예도래일·티어·시행령)
  노출. 법률·QA·스튜어드십 3에이전트 + injection 4종 교차검증. 상세: [[rules/laws/README]].
- **director_board tool 신설 — 20번째 tool (2026-07-08)** — **개별 이사 단위** 정보: 이사 인당보수·
  보수한도 소진율·임원 재직/사퇴 변동(연도 diff)·(v2)이사회 출석률·겸직. corp_gov_report(회사 15지표
  준수)와 무중복. 소진율은 DART 정형 API(exctvSttus·drctrAdtAllMendngSttus 2종)로 산출 —
  감사위원 포함 이사류 실지급÷주총 승인한도(IR 검증 260708: 헤드카운트 정합 확증), 한도 공백해는
  최근 유효연도 lookback. 재직/사퇴 diff는 이름 OR 생년월 매칭으로 로마자표기·birth 오타 오탐 억제
  (QA 260708: 기아 신재용 birth_ym 오타 이중오탐 발견·수정). 가치판단 안 함(수치·변동·flag만).
  client에 이사·보수 정형 API 4종 신규 추가. IR·스튜어드십·QA 3에이전트 교차검증.
- **shareholder_commitment tool 신설 — 19번째 tool, 2번째 Action Tool (2026-07-07)** — 밸류업·배당·자사주
  소각의 **약속 vs 실제 이행**을 연중 추적(proxy_advise가 주총 1회성 판단이라면, 이건 스튜어드십
  engagement 관점의 지속 추적). 4개 upstream 재사용(value_up·corp_gov_report·dividend·treasury_share,
  신규 파싱 없음) + 신규 계산 1개: 자사주 소각 사이클마다 매입시점 BPS 대비 실제 매입가를 비교해
  **장부가(BPS) 손익을 원화로 계산**(배당은 방향이 반대라 이 계산에서 제외, 대신 CSR 종합에는 포함).
  이 계산이 정확하려면 treasury_share의 실행결과보고서 금액이 정확해야 했는데, 원문 표 단위(백만원 등)
  미인식 버그를 KOSPI200 전수 스캔으로 발견·수정(26건→0건, QA 2인 원문대조 검증)한 뒤 그 위에 tool을
  올림. sanity 필터로 별개 이슈(`_link_cycles` 매칭 오탐 4건)를 조용히 흡수하지 않고 명시적으로 플래그.
  ([[shareholder_commitment]] / [[treasury_share]])

### 주요 변화 (2026-05-04 ~ 06-21)
- **주총 안건/정기·임시 파서 6사이클 production 배포 + 검증 측정 함정 5패턴 (2026-06-21)** — detect 재작성(880→888, 섹션 오선택→소집공고 직후 40자 앵커)·marker/zone·bleeding 경계·하위안건 분리·**proposer 복원**(주주제안 `source` 전파, 다원시스 라이브 확인)·빈제목 부모 추론. 통합 픽스처 **3,016건**(코스피·코스닥 3/1~5/15, 정기 2,849) 확보. **측정 스크립트가 6번 거짓 결론**(이미지=본문부재·미상=사각지대·0개=버그·bleed 날짜오탐·v2 정답오판·변경분만 봄) → 직접 표본·전수 diff·production 경로(html)로만 잡힘. 프로토콜: **html 픽스처 0콜 + 전수 diff + 직접 표본**. (agenda-parser-validation-260621)
- **proxy_result 제거(17→16) + 2차 전수조사 4축 (2026-06-13)** — proxy_result_after_meeting 제거: 핵심(안건별 가결/부결/찬반율)을 `shareholder_meeting_results`가 3콜 vs 32콜로 대체, desc의 'cross-match' 미구현, 핵심 0건 회귀를 한 달간 무인지 = 실사용 부재. 코드 archive 보존. 남은 검증축 전수: **seam audit**(proxy_advise 8사 composite vs 직접 호출 — 고려아연 headcount str crash 발견·근원 fix), **render smoke**(16툴×31케이스 FastMCP call_tool — 솔루엠 perf None 포맷 crash 발견 → 31/31, payload audit이 못 보는 render 레이어), **corp_gov 값**(30사 450지표 O/X + 기준값 정확 일치, 삼성 13/15), **production MCP smoke**(정식 클라이언트 initialize→list→call, 재설계 반영 확인). 메타: 가짜 clean 3회(틀린 키)→첫 케이스 실키 출력+양성 검사량 보고 표준화 / 인프라 이상은 단발 증상 말고 curl 직접 격리 확인(키 무효 오진 정정). 릴리즈 노트 docs/RELEASE_NOTES.md로 분리. (tool-coverage-audit-260612)
- **audit 미커버 툴 전수조사 — proxy_result 0건 회귀 발견·교정 (2026-06-12)** — 커버리지 매핑(파싱 성공률 vs 내용 정확도 2층위) 후 미흡 순서로 전수. ① ownership 450사: DART 원본 단위 오염 2사(LS 명부 ×1,000·LS에코 발행총수 ×1,000,000) — 지분율 anchor 자가 교정 + 분모(보통주 vs 총주식) 괴리 경고. ② dilutive·restructuring·deals 값 정확도 286사: deals 행 1,560 채움률 100% issue 0, 날짜 676건 한국어 원본 → ISO 일괄 정규화. ③ evidence 8케이스(불가능 달력 날짜 검증 추가) + **proxy_result: 핵심 안건결과가 항상 0건이던 회귀**(upstream 키 `agenda_results`→`results.items` rename 미반영, no_results로 위장) 발견 → 교정 후 15/15 복구. 과거 연도(2024·25) 결과는 서술형 공시라 소스 한계. 교훈: baseline 없는 툴은 죽어도 모른다 / upstream rename 시 소비자 동시 갱신 / upstream exact + 자기 0건 = 버그 신호. (tool-coverage-audit-260612)
- **financial_metrics 정밀화 — hedge 역추적 → 412사 전수 audit (2026-06-12)** — 실사용(SK하이닉스 질의)에서 호스트 모델의 장시간 추론·"확인 필요" hedge가 전부 tool 파싱 결함의 증상임을 역추적. 교정 9건: **Q4 누적차분 + QoQ·YoY 기본 동봉**, fs_div 필터(CFS+OFS 동시 반환 실측), **이자보상배율 왜곡 제거**(금융비용 오염 — 삼성전자 3.72→92.8배) + 변형 정확일치 세트로 커버리지 59%→97%, 차입금 generic(차입금/차입부채), 분기 fallback CF 결측(used_rc 전파), 재작성 gap flag 이원화·손익 3키 확장, EBITDA(D&A 24% 원천 한계)·잠식률 표시 정책. 검증: 라운드 점증(4→412사 × FY24·25, ~4,200콜) + 불변식(4분기합=연간·FCF=CFO−CapEx·듀퐁곱=ROE) 수렴. 교훈: 모델의 hedge는 공짜 버그 리포트, 결측이 오류보다 낫다, 누적 공시는 항상 차분. ([[financial_metrics]] / financial-metrics-precision-260612 / [[260612_fm_market_audit_412|audit raw]])

- **risk_events tool 신설 — 17번째 tool (2026-06-11, serious_accident 흡수 확장)** — 기업 리스크 이벤트 6종 통합: **중대재해 / 횡령·배임 / 파생상품손실 / 회생·부도 / 생산중단·영업정지 / 해산** (본사·종속/자회사 변형 포함). 채널 = **I001+B001 동시 조회** (회생'신청'·부도·영업정지·해산은 B001 전용 — 시장 90일 sweep 실측으로 채널 매핑 확정). 중대재해는 **305사 × 3.5년** 검증(I 전체 vs I001 차집합 0·truncation 0·풀스캔 누수 0) — 공시 79건 전수: **대형 원청·지주사 집중**(KOSDAQ 상위 100 보유 0, 중소형 건설 35사 0건). **시장 전체 스캔 모드**(company 미지정, 30일 기본·90일 max, ~45콜): 30일 실측 54건/6카테고리. 카테고리별 원문 파싱(사상자/혐의금액·자기자본%/손실액/중단부문) + **사상자 supersede 집계**(정정·지주/사업회사 이중 공시 → 발생일자+장소 정규화 키, 한화 '㈜' vs '(주)' 이중 집계 버그 실측 교정). 처벌확인 실물 2건 첫 확인(화일약품·THE CUBE&), 횡령배임 파서 태광산업 검증(38.3억·자기자본 0.09%). 제도 시점: 중대재해 수시공시 신설 2025-10 → 이전 무공시 ≠ 무사고 warning. 본문 파싱은 연속 2개 90일 윈도우 **359건 전수 audit**(파생 `손실발생금액`·영업정지 라벨 등 교정, 전 카테고리 87~100%). **스콥 결정: 활성 3종(중대재해/횡령배임/생산중단영업정지), 파생손실·회생부도·해산은 mute** — 파서·검증 보존, 기본 조회·desc 비노출, 명시 category 요청 시만 동작. ([[risk_events]] / risk-events-pipeline-260611)
- **related_party_transaction → corporate_deals rename (2026-06-10)** — "SK스퀘어가 인수하거나 매각한 회사" 질의가 tool 라우팅 실패. 원인 = 이름·desc에 사용자 어휘(인수/매각) 부재 + 이름이 기능 절반(지분 딜 추적)보다 좁음 + corporate_restructuring이 "M&A" 선점. scope 분기 대신 **rename + desc 사용자 어휘 보강**("어떤 회사를 인수했나/팔았나", 출자·회수) + restructuring과 **양방향 경계 문구**. 내부 plumbing 전면 통일, 기능 변화 0, SK스퀘어 회귀 동일(취득4/처분3). (tool-naming-discovery-260610)
- **proxy_advise 2단계 조기 발사 시도 → 실측 반증 → 롤백 (2026-06-10)** — perf를 director gate 판단 후 1차 완료 전 조기 발사. component-timing 모델상 이득 1.7초(회귀 수학적 불가능, before/after bit-identical)였으나 **wall-clock 실측에서 반증**: 복잡 20개 손해 16/20, 흔한 15개 손해 12/15, 평균 음수. 측정 노이즈(같은 코드 2회 차이 중앙 426ms)가 효과(-350ms)보다 커 이득이 묻힘 + Semaphore(3)·throttle 경합으로 약한 손해 경향 → 롤백. 교훈: component 모델 ≠ wall-clock, 효과 측정 전 노이즈 baseline 필수. (proxy-advise-stage2-parallel-260610)
- **ownership_structure summary 재설계 + 정합성 버그 2건 (2026-06-10)** — "고려아연 지분구조" 질문에서 출발. summary 헤드라인을 **명부 단독 vs 본인+특관 vs 5% 실세**로 라벨 분리(집계 기준 다름), **지분 구성 100% 정합 분해**(명부+자사주+기타, 5%보고는 보고자 중복이라 합산 100% 불가) 추가, 노이즈 컷·블록 병합(6→3블록). changes scope를 **I004(최대주주변동신고서)**로 좁히고 **5% 대량보유 변동 통합**(분쟁사는 5%보고로 움직여 I004만 보면 빔, 고려아연 0→15건). director_evaluation **E006** narrowing. 33개사 스크리닝으로 정합성 버그 교정: **셀트리온 issued=0**(우선주 없는 회사는 `합계` 행만 → 보통주 행 가정 실패 → 합계 fallback), **금호석유 resolve 실패**(정식명 prefix 단일후보 자동선택). 펀드형(맥쿼리인프라) issued=0 안내. (ownership-summary-integrity-260610)
- **공시 검색 페이지컷 truncation 교정 — 6 tool detail-code 좁히기 (2026-06-09)** — 넓은 공시유형(I·B,I,E·D) 페이지 순회(max_pages=10)가 prolific 회사에서 truncation. proxy_contest D 검색이 삼성에서 D002(임원 수천건)에 밀려 D001/D003/D004 일부 잘림(broad 6 vs detail 14, +8 복구). [[공시유형코드체계]] 카탈로그 기반 정밀 매핑 + '넓은type vs detail 차집합 0' 검증으로 6 tool(corp_gov·value_up·shareholder_meeting·treasury·related_party·proxy_contest) 좁힘. filing_search 멀티 detail-code 지원 + 013(no-data) abort 버그 fix. (page-cut-detail-code-260609)
- **배당 파서 출처확정+누적차분+분류 정밀화 (2026-06-09)** — 출처맵(A 사업보고서 다년컬럼=권위 / B 분기·반기 누적 / C 결정공시 날짜 / D 명부폐쇄 기준일). 분기별을 결정공시 날짜추측 대신 **정기보고서 누적 차분**(Q2=반기-Q1…)으로 보통+우선 DPS·총액 산출 → 경계 오귀속·예비결산 중복 제거, 무배당 분기 0·특별배당 포착. 최신연도 4분류(중간확정/확정전/미공시/무배당, target연도 매칭). 중복제거(pre_dividend 통합·pending_annual 제거 → DART -3/회사). per-decision 시가배당률 0 억제. stateless MCP(머신별 세션 in-memory → nrt×2 "Session not found" 해소). **51개사 정합성 100%** 검증. (dividend-source-of-truth-260609 / [[배당공시유형]])
- **proxy_contest 분쟁 신호 정밀화 (2026-06-05~07)** — 경영권 분쟁 탐지 다축화. ① 5% 대량보유 시계열 동학 (목적전환 단순투자→경영참여 / 지속 추가매입 / 급변 ±5%p 매집·exit 양방향). ② 소송 4단계 분류: 정정 dedup → 경영권/상거래 구분(commercial false positive 제거, 아시아나항공 등) → 미상 회사단위 추정 → 본문 "사건의 명칭" 파싱(litigation scope, 병렬, 📄) → LLM 위임. 키워드 단독 행위 + 명사·행위 조합 구조(substring FP 제거). ③ 능동 5% 외부세력/대주주 본인 분리. ④ dead code(_block_signals 중복 majorstock) 제거. 역추적 방법론 검증: 시총순 14% vs 분쟁 공시 역추적 71.6% (5배 효율), 142종목 진짜 경영권 분쟁 70 추출. 자동 판정 X — 정보 구조화 + LLM 위임 철학. (contest-signals-500-260605 / dispute-reverse-lookup-260607)
- **데이터 tool latency pass (2026-05-24)** — `data.timings_ms` 노출 + low-risk 병렬화. treasury_share 결과보고서 검색 전체공시→B/I/E title scan(삼성전자 2.7s→0.9s), dividend metadata overlap, filing_search page2+ 병렬, corp_gov 검색윈도우 4년→2년. median speedup 58.6% (4.137s→0.211s), p95 4.163→0.251s. 핵심: 병렬화보다 "대기를 겹치는 위치"가 중요. ([[260510_data_tools_perf_audit]] / perf-timing-260524)
- **value_up role extraction (2026-05-31)** — 최신 공시 1개 중심에서 `latest_plan` / `latest_status` / `latest_result` / `meta_amendment` 역할 분리. `계획서 명칭` 기반 보정, meta-only 구간 최근 2년 role backfill, `이행결과` nullable 분리. KOSPI500 + KOSDAQ150, 562 filing 전수조사 기준. ([[260530_audit_value-up-implementation-tags]] / [[value_up]])
- **financial_metrics Tier 1 확장 (2026-05-31)** — CFO/순이익, DSO/DIO/DPO/CCC 추가. 51 → 56 지표, 추가 API 호출 없음. ([[financial_metrics]])
- **agenda parser marketwide (2026-05-25)** — KOSPI500 + KOSDAQ150, XML 641건, no_filing 9, 3회 재파싱 hash diff 0. 최신 기준은 [[260525_1620_audit_agenda-parser-marketwide]].
- **agenda relation KOSPI300 rerun (2026-05-25)** — exact 298 / no_filing 2 / requires_review 0, relation metadata는 결론이 아니라 자동 판단을 멈추는 guardrail. ([[260525_0200_audit_agenda-relation-kospi300]] / agenda-relation-parser-260525)
- **key data tools parsing 성공률 감사 (2026-05-17~18)** — KOSPI 300 + KOSDAQ 150 baseline과 비중복 100개 recheck 기준 문서 신설. 최신 기준은 [[architecture/audits/260517_parsing_success_rate_audit]]. `value_up`은 outside-window/013 no_filing 분류 보강 후 strict 100%, `shareholder_meeting_results`는 DART-first 결과 파싱 후 adjusted hard fail 0%.
- 17 → 16 tool: `screen_events` drop, `proxy_guideline` archive, `shareholder_meeting` → notice + results 분리
- proxy_advise scope **10 → 1** (`decisions`만, raw는 각 tool 직접 호출)
- treasury_share scope **6 → 2** (summary + annual)
- 자사주 결과보고서 **4종 추가** (취득결과/처분결과/신탁상황/신탁해지결과)
- ralph proxy_advise framework 99% 검증 (KOSPI 100 + KOSDAQ 50, G1 100% / G2 0% FP / G3 100% / G4 100%)
- 사내이사 **재직 중 성과 매트릭스 (2x3)** 도입 — status quo bias mitigation. ROE/부채비율/CSR × avg/trend, bad → AGAINST, weak → REVIEW. KOSPI 100 + KOSDAQ 50 검증 G1 100% / G4 dist 29.7/45.3/18.0/7.0 모두 target band 충족. ([[260505_1700_decision_inside-director-performance-matrix]])
- **보수한도 / 퇴직금 분기 정밀화** — 이사 13 / 감사 11 / 퇴직금 12 분기 + 정관 hybrid 통합. KOSPI 200 + KOSDAQ 50 (n=226) G1 99-100% / G3 100% / G4 N연기금 정합 100%. AGAINST 5건 (지급률 2배수+ × 3, 사외이사 퇴직금 × 1, 자본잠식+인상 × 1) 모두 정확 분기. ([[260505_1900_decision_compensation-retirement-split]])
- **shareholder_meeting_notice scope 정리** — 6→5 (`agenda`/`full` 폐지, `prov_financials` 신설). summary 강화 (hierarchy + 1호 안건 메타) + aoi_change에 retirement raw 통합. `provisional_financial_statement.py` 독립 모듈 (parser.py 의존성 제거). ([[260506_0030_decision_notice-scope-cleanup-prov-financials]])
- **parser omnibus 검증 + DART 6컬럼 sub-column fix** — KOSPI 200 + KOSDAQ 100 (300 회사) 통합 audit, 9 Tier A parser G1 ≥98.7% 모두 충족. PFS metric extraction 19 sparse 케이스 (현대차/셀트리온/두산 등) root cause = `_period_by_num` 다음 colspan 확장 빈 셀이 "unknown" 분류되던 것 → fix 후 100%. v1 dead 3 parser logical archive 결정 + G4 layer 정합 PASS. (parser-omnibus-260506 / [[260506_2330_decision_v1-dead-parsers-archive]])
- **법령 layer 정밀화 (Ralph 4)** — Ralph 3 follow-up. 280 회사 광범위 검증 (KOSPI 200 + KOSDAQ 100 + 분쟁 20). B1-4 분기 (정관변경 vs 후보 임기) + B1-8b 신규 (KT&G 정관 사전 우회 catch) + B1-7 보강 (정원 키워드). `_agenda_pattern_match()`에 parent_must_contain/parent_excludes 패턴 키 신규. 36 → 38 룰. false positive 0 / 회귀 0. 분쟁 회사 hits 11.6% (KOSPI 9.8% / KOSDAQ 1.8%). (law-layer-precision-260508 / [[260508_0700_decision_law-layer-precision]])
- **파서 전수조사 + 정밀화 검증 (Ralph 5)** — 40 파서 분류 (A 명명형 25 / B raw 보존 1 / C 혼합 14). framework: 데이터 본질에 따라 (숫자→파싱, 자연어→raw, 메타+본문→혼합). parse_aoi_xml이 모범 사례 (clause/label 명명 + before/after raw). audit 1차 권장 (parse_personnel_xml + parse_aoi_xml 보강) Ralph 5 실측 후 무효화 — careerDetails 0% 누락 (44회사/225후보) / aoi 1.66% 누락 (모두 source 한계). 두 파서 정밀도 충분, 코드 변경 X. ([[architecture/audits/260508_parser_audit]] / parser-precision-260508)
- **Wiki 트리 정책 명문화 + lint hook (2026-05-09)** — 식물학 metaphor 도입 (🌱뿌리 raw → 🪵줄기 rules → 🌿큰가지 → 🌾잔가지 → 🍂낙엽). Link 정책: 단방향(위→아래)/양방향(큰가지↔잔가지)/자유(잎↔잎). ABCDE 정리: 단방향 위반 34→0, 양방향 결손 44→0, orphan 24→7, edges 1261→1558. `scripts/wiki_lint.py` + GitHub Actions CI. CLAUDE.md 124→109 가벼움화. 구 *_RULE.md 7개 archive 이동 (`wiki/archive/tools/legacy_rules/`). data-collection.md DS003 섹션 추가 (financial_metrics 4 API). ([[architecture/audits/260509_wiki_graph_audit]] / [[wiki_schema#0-트리-구조-식물학-metaphor]])
- **financial_metrics yoy 병렬화 (2026-05-09 perf)** — Explore agent 효율성 audit 결과 #1 fix. sequential 3 호출 (curr/prev/audit_opinion) → `asyncio.gather` 병렬. 회사당 ~3초 → ~1초 (2-3배). 100 회사 배치 시 3-7분 단축. regression 0 (read-only API + 독립 인자). 다른 발견 (#2-#4)은 trade-off로 skip — cache 인프라 견고하여 ROI 낮음.
- **proxy_advise decision 시각 강조 + B1/B2 raw 첨부 (2026-05-10)** — LG화학 LLM misread (proxy_advise FOR 무시하고 안건명 "배제"만 보고 자체 AGAINST 추측) 방지. ✅ FOR + 🛡️ 강행규정 정합 marker / B1/B2 hit 안건 정관 본문 raw `[clause 변경 전/후]` 첨부 (cache hit으로 latency +1-2%). A1/A2는 결정 강제 유지 (토큰 절약), B1/B2만 LLM case-by-case 판단용 raw.
- **운용사·NPS·ISS 전수 익명화 (2026-05-10)** — 9 commits. tool description vote_style 옵션 list 제거 + README 표 제거 + `_VOTE_STYLE_POLICY_FILE` 실명 alias 제거 + wiki/data 200+ 파일 일괄 익명화 + 익명 코드 정비 + 외부 자문사명 일반화 + "외부 advisor" 항목 제거 (b_foreign에 흡수). 최종 익명 catalog: m/s/sa/k_legacy + t/a/c_activist + b_foreign + n_pension (9개). manager_aliases.json (gitignored) v4.
- **★ production wiki/rules/laws/ 누락 fix (2026-05-10 b5951a4)** — Dockerfile에 `COPY wiki/rules/laws/` 누락으로 38 법령 룰이 **production에서 작동 안 했음**. LG화학 misread의 진짜 원인. v355 deploy로 production /app/wiki/rules/laws/ 활성. + llm_misread_patterns.json (6 패턴 catalog) 신규 — 새 misread 발견 시 JSON 한 줄 추가, 코드 변경 X. + Tool description ⛔ CRITICAL 가이드 inline (Layer 1).
- **호수 hierarchy 진단 + D 패턴 amendments body fallback (Ralph 7, 2026-05-10)** — 사용자 가설 "parser가 호수 누락" 검증 → false (10/10 회사 거의 완벽, LG화학 ※ note span 미세 버그 1건만 fix). 4 미매치 회사 = D 패턴 (raw에 sub-agenda 자체 부재 + top title 일반 표현). 룰 catalog `body_pattern` 별도 필드 추가 (title 매칭 회귀 위험 0). amendment 단위 검사 + strict 진입 조건 (children 0)으로 Ralph 6 회귀 회피. **510 회사 spot 회귀 0** + body fallback 신규 70건 catch (69 회사 = 13.5%) + **A1-8 (자사주 의무소각) 첫 활성** (Ralph 6 미사용 룰 lesson 중 첫 catch). 카카오게임즈는 D 패턴 X (sub 있고 sub title 일반) — 별도 ralph 후보. (agenda-hierarchy-260510 / [[260510_0900_decision_d-pattern-body-fallback]])
- **카카오게임즈 패턴 sub→amendment 1:1 매핑 (Ralph 8, 2026-05-10)** — 510 회사 중 진정 카카오게임즈 패턴 26개 (5.1%) 처리 architect. 진입 조건 (parent 정관변경 + sub children 0 + sub generic 아님 + amendments) + strict cascade (label substring → clause 매칭, keyword 매칭 의도적 제외 — semantic mismatch false positive 회피). cross-match 회피 (회사별 used_amendments track). 510 회사 회귀 0 + sub 75건 신규 catch (55 회사 = 10.8%) + 미사용 룰 A1-3 (18건) / B1-8 / A1-2 활성. KOSPI 23% vs KOSDAQ 5% (대형사 sub-hierarchy 명확). (subagenda-mapping-260510 / [[260510_1015_decision_subagenda-mapping]])
- **사외이사 충실성 강화 — 겸직 카운트 + 사내이사 독립성 표기 정정 (Ralph 9, 2026-05-10)** — 메리츠금융지주 응답 검토 사용자 피드백. careerDetails 510 회사 audit (98.4% 채워짐) → 단순 키워드 카운트 false positive 발견 (본 회사 사외이사 표기) → logic v3 (본 회사명 매칭 + 후보 본인 보장). `count_outside_director_positions` + faithfulness 통합 (≥3 strong / ≥2 concerns). 사내이사 "독립성 평가 비대상 (사내이사)" 표기 (오인 방지). decision 변경 0 (facts 신규 노출만). 510 분포: concerns 13.3% / strong 2.7% 후보. 김정연(삼성바이오 strong 3개) / 박진규(LG에너지 concerns 2개) 사례 검증. (director-faithfulness-260510 / [[260510_1130_decision_director-faithfulness]])
- proxy_advise render Korean label 자연화 (`weak_concerns` → "약한 우려" 등)
- archive: `wiki/archive/services/` (proxy_guideline / proxy_guideline_scoring / policy_comparison / agm_first_agenda_fy_v1_regex)

---

## [2026-07-07] feat+fix | treasury_share 단위버그 수정 + shareholder_commitment 신설(19번째 tool)

- **treasury_share 원문 단위(백만원 등) 미인식 버그 수정**: 실행결과보고서(취득·처분·신탁 결과) 원문
  표가 "(단위: 백만원)" 등을 선언하면 ACODE 태그 값도 그 배수를 따르는데, 기존 `_acode_int`는 항상
  원 단위로 가정해 금액이 최대 100만분의 1로 축소됐다. 신규 `_acode_amount`+`_nearest_table_unit`이
  ACODE를 감싸는 `<TABLE-GROUP>` 범위 안에서만 단위선언을 찾아 배수 적용(문서 내 여러 표가 서로 다른
  단위를 쓰는 경우 상호 오염 방지 — 처음엔 문서 전체 스캔·단일 `<TABLE>` 스코핑 둘 다 시도했으나
  각각 오탐/누락 있어 `<TABLE-GROUP>` 단위로 정착). `scripts/treasury_unit_sweep.py`(신규)로 KOSPI200
  전수 스캔: 수정 전 26건(7개사: 현대차·기아·SKC·포스코퓨처엠·유한양행·한화솔루션·세아베스틸지주,
  현대차는 60개월 이력 100% 오염) → 수정 후 0건. 독립 QA 에이전트 2인이 신규 10개사에서 원문 직접
  대조 포함 재검증(과잉수정 없음 확인). 남은 4건(POSCO홀딩스·카카오·엘앤에프·포스코퓨처엠)은 단위버그가
  아니라 결정↔실행 매칭 로직(`_link_cycles`)의 별개 이슈로 확인, TO_DO 기록.
- **`shareholder_commitment` tool 신설(19번째 tool, 2번째 Action Tool)**: 밸류업·배당·자사주 소각의
  약속 vs 실제 이행을 연중 추적. 4개 upstream 재사용(value_up·corp_gov_report·dividend·treasury_share,
  신규 파싱 없음) + 신규 계산 1개 — 자사주 소각 사이클마다 매입시점 BPS 대비 실제 매입가를 비교해
  **장부가(BPS) 손익을 원화로 계산**(배당은 방향이 반대라 이 계산에서 제외, CSR 종합에는 포함).
  BPS는 `financial_metrics.bps_krw`가 실측 결과 항상 None(미구현)임을 확인하고 `total_equity_krw`+
  DART `stockTotqySttus`(valuation.py의 `_shares_outstanding` 재사용)로 직접 조합. sanity 필터(비율
  0.3~3.0)로 `_link_cycles` 오탐이 조용히 섞이지 않게 방어. 이름은 처음 `stewardship_followup`으로
  지었다가 사용자 피드백으로 `shareholder_commitment`로 변경(직관성).
- **버그 발견·수정(조합형 tool 설계 교훈)**: `value_up` 호출 시 조회 구간을 안 넘겨 실제 존재하는
  밸류업 계획을 "없음"으로 오판(미래에셋증권 실측 — 2024-08 최초공시·2025-06 이행현황 있음에도 기본
  12개월 창에 안 걸림). 근본원인은 `value_up`이 이미 갖고 있던 자체 진단 필드(`availability_status:
  "exists_outside_requested_window"`)를 조합 코드가 upstream `warnings`를 완전히 버려서(예외만 잡고
  정상응답 warnings 무시) 놓친 것. `_data()` 헬퍼가 모든 upstream warnings를 전파하도록 일반화 —
  "조합형 Action Tool은 upstream warnings 반드시 전파" 원칙으로 메모리에도 기록.
- **배당수익률 연말종가 보완**: `dividend.history`의 `yield_pct`(DART 결의시점 시가배당률)가 옛
  연도일수록 결측 많음을 실측 확인(3개사 전부 2021·2022년 None). `krx_weekly`(연말종가,
  `valuation.py`의 `_annual_pit_band`와 동일 쿼리 패턴)로 `yield_pct_yearend` 별도 계산해 공백 보완.

## [2026-07-06] fix+refactor | 통화 저장시점 근본해결 + DB rename 5건·병합 1건 + 문서 정합성 정정

- **통화(FX) 근본해결**: `market_val_series.py`(연간)·`market_fund_quarterly.py`(분기) fetch 루프에
  `statement_currency()`+`fx_to_krw()` 삽입 — 그 해/분기 응답에서 직접 통화 감지 후 저장 전 KRW 환산.
  기존엔 read-time에 최신 통화 라벨 1개를 전 연도에 곱해 두산밥캣류(연도별 통화 전환사) 옛 연도가
  폭증하는 버그가 있었음(4조→4,826조). 22사(USD 12·CNY 9·JPY 1) 재수집(연간 176건+분기 489건),
  라벨 KRW화(`orig_currency` 보존), derive+밴드 재계산, firm_history 회귀검증 통과(두산밥캣 PBR
  0.73~1.01배로 매끈).
- **DB rename 5건**: `mkt_fund_hist`→`mkt_finstat_y` · `mkt_fund_q`→`mkt_finstat_q` ·
  `mkt_valuation`→`firm_valuation_snapshot` · `events`→`tool_call_events` ·
  `krx_reset_days`→`krx_reset_sweep_checkpoint`. `usage.py`/`usage_tracker.py`는 로컬 sqlite `events`
  (별개 DB)와 Postgres `events`가 리터럴로 섞여있어 연결객체 기준으로 Postgres만 정확히 골라 rename.
- **병합 1건**: `mkt_val_history`+`mkt_sector_val` → 단일 `mkt_val_history`, `sector` 컬럼에 센티넬
  `'_ALL'`(NOT NULL, PK=snap_dd·mkt·sector) 도입. 병합 전후 행수(11,269)·`_ALL` 행수(158) 일치 검증
  후 커밋. `market_val_weekly.py`/`market_val_history_backfill.py` INSERT 통합, `valuation.py`의
  `build_market_val_payload`/`build_sector_val_payload`에 `sector='_ALL'` 필터 추가.
- **실행 중 발견·수정**: 문서상 모순(rename 목록과 병합 결정이 같은 테이블을 두고 충돌) · macOS sed의
  `\b` 미지원으로 첫 치환이 전부 무효였던 것 · `market_val_weekly.py`의 `DDL_SECTOR` 잔여참조(실제
  재실행해서 발견) · README.md/README_ENG.md의 도구 개수 불일치(배지 18 vs 실제 표 17개, `valuation`·
  `order_contracts` 누락 — 특히 README_ENG.md는 여러 곳에서 17로 정체돼 있었음).
  DB rename+병합은 트랜잭션 하나로 실행 후 즉시 코드 push(라이브 서빙 갭 최소화, 사용자 선택).
- **문서 갱신**: `wiki/tools/valuation.md`(scope 표·TTM/MRQ 완비·sector_history 신규기능 반영),
  `wiki/rules/disclosures/분기재무-API스펙.md`(테이블명
  갱신, 특정 과거 사고 기록은 원 이름 보존), `wiki/architecture/data-storage-registry.md`("✅ 완료"
  전환), `wiki/tools/README.md`+루트 README 2종의 도구 카탈로그를 `[[wikilink]]`/백틱에서 클릭 가능한
  `[name](name.md)` 마크다운 링크로 전환.

## [2026-06-25] ops | Fly.io 배포 실패 복구 — 볼륨 호스트 장애 → 머신 교체 + 이중화 복구 (런북)

- **증상**: director 커밋(3ab8294) 후 Deploy to Fly.io 실패 — `machine requires manual intervention: volume on unreachable host`. log 회고 커밋은 같은 머신이 안 떠서 10분 deadline timeout.
- **원인**: 머신 1대(287e0d4b)의 볼륨 `opm_data`가 얹힌 **물리 호스트 다운**(Fly 인프라 장애, 코드/설정 무관). 볼륨이 호스트에 묶여 자동 교체 불가.
- **대응 런북** (다음에 같은 장애 시 그대로):
  1. `fly machine destroy <unreachable_id> --force -a open-proxy-mcp` — 죽은 머신 제거 (볼륨 `opm_data`는 corp_code 캐시라 무해, 다음 호출 때 DART 재로드)
  2. `fly deploy -a open-proxy-mcp` — 정상 머신 기준 rolling 재배포 → 최신 코드 반영
  3. `fly machine clone <정상_id> -a open-proxy-mcp` — 이중화(2대) 복구
- **결과**: director 포함 최신 코드 production 반영, 머신 2대(8e3e+8254) 이중화 복구. 2대 이중화 덕에 장애 중에도 서비스(open-proxy-mcp.fly.dev)는 무중단.
- **후속 — GitHub Actions 자동배포 토큰 갱신 런북**: 위 복구 후 자동배포(`Deploy to Fly.io`)가 `unauthorized`로 실패. **만료된 `FLY_API_TOKEN`** 문제였고, **`fly tokens create deploy`(app scope)는 `flyctl deploy --remote-only`의 remote builder 권한이 없어 또 실패** → **`fly tokens create org -o personal`(org scope, 빌더 포함)**로 만들어 GitHub repo Secrets의 `FLY_API_TOKEN` 교체 후 Re-run하니 성공. 즉 **자동배포 토큰은 반드시 org scope**여야 함(deploy scope 불가). 토큰은 화면 노출 금지 — 파이프(`| gh secret set`)나 GitHub UI로 직접 넣고, 노출분은 `fly tokens revoke`.

## [2026-06-25] 검증 | SM·이미지·안건마커 전수 재검증 (production 경로 함정 교정, 코드 변경 0)

- **SM 안건 fallback(eb9a932)**: 픽스처 전수 3,016 **무회귀 0** 확인 (라이브 322사와 일치).
- **이미지 소집공고**: 픽스처에 0건 — OCR은 **openproxy ai 영역**(별도 프로젝트)으로 분리. mcp 범위 밖.
- **안건 0건 27건 정밀 재판정**: 진짜 소집공고 안건 실패 **3건(0.1%)**뿐 — 나머지는 이사회 의안 결의(찬반표) 19건 + 안건없음 5건이 측정 함정(parse 0이 정답)이었음.
- **안건 마커 변형(네이블·제이엠티) raw fallback 시도 → revert**: parse_agenda에 raw 노드를 추가했으나 agenda_valid를 True로 만들어 기존 메커니즘을 우회시키는 역효과. 확인 결과 **production(shareholder_meeting)이 이미 ① viewer crawl 재시도(네이블 파싱 성공) ② `raw_text_excerpt` 원문 발췌(제이엠티 6,000자, REQUIRES_REVIEW)로 대응 중**이라 추가 불필요. 코드 변경 0.
- 교훈: 픽스처 단일 함수(parse_agenda)만 보고 '실패' 판단 → production 경로(viewer fallback + raw_text_excerpt) 미반영. CLAUDE.md '① MCP 호출(production 검증) → ② 직접 import' 순서 준수.

## [2026-06-25] feat | director 후보 추출 — 분리표 + 제목 인라인 2패턴 보강

- **`parse_personnel_xml`** (feat): 전수조사가 짚은 zero-candidate 2패턴 보강 — ① 분리표(성명·생년월일 별도 표) candidates 구조화 ② `_extract_inline_subagenda_candidates`로 '제N-M호 사내이사 {이름} 선임의 건' 제목 인라인 후보 추출.
- **검증(4축 직접)**: 4사(아남전자 0→5·에이텍 0→3·코오롱생명과학 0→2·퓨쳐메디신 0→2) candidates 복구 + 이름 정확(유성준·박준구·신종수·이용노·권오용·이한국·강대희) / **전수 3016 candidates 무회귀 0**(정상 회사 안 깨짐)·appointments 무변·복구 38건 / zero-candidate 13→9. opm-enhance 워크플로(schema 미준수로 실패)의 작업물(+77)을 직접 4축 검증으로 채택.

## [2026-06-25] fix | 주총 안건 파서 — 소집공고 목차 없는 케이스 'III.2 목적사항별 기재사항' fallback

- **증상**: 에스엠(SM) 2025 정기주총 안건 0건(no_filing) — 원문엔 제1~5호 명확.
- **근본원인**: SM 소집공고 preamble에 회의목적사항(안건 목차)이 없음. 실제 안건은 `III.2 주주총회 목적사항별 기재사항`에만 존재하는데, 그 사이 `I. 사외이사 활동내역`(이사회 출석률·의안 찬반 표, '제N호 의안 자기거래' ×100 노이즈)에서 소집공고 섹션이 잘려 파서가 보는 zone이 비어버림.
- **fix** (`tools/parser.py`): `parse_agenda_xml`을 helper(`_agenda_flat_from_zone`)로 분리하고, 소집공고 zone에서 안건을 못 뽑으면 `_extract_objective_section`(III.2)로 fallback. **strictly additive** — primary zone이 안건을 뽑으면 fallback 미사용(기존 동작 보존). 제목 bleeding 방지용 경계 키워드(후보자 성명·이사/감사의 수·재무상태표) 추가.
- **회귀 검증**: 정기주총 시즌 **라이브 322개사** baseline↔신규 diff → **회귀 0** (기존 316개사 안건 번호까지 동일), SM 0→5건 수정. 남은 5건은 별개 근본원인 — 이미지 소집공고 4(OCR 영역) + 리츠 '제N호…의 건'(의안 키워드 없는) 마커 변형 1.
- 후속(별개): 리츠 마커 포맷, 이미지 소집공고 OCR.

## [2026-06-25] fix/feat | corp_gov 주주필드 silent 고장 수정 + 시그널 부여(가정 검증으로 선별)

- **`corp_gov_report`** (fix): `_parse_company_summary`가 법적 정의문구 '최대주주(그의 상법상 특수관계인을 포함한다)'의 여는 괄호 `(`를 긁어 `max_shareholder/pct/minority` **전수 silent 고장**. '소액주주' 앵커 ~5KB 슬라이스 + **td 단위 (label,value) 파싱**으로 수정(콜 추가 0). 음수재무 △/▲/괄호 정규화. **50사 regression 0, 주주필드 채움 1→36**(나머지=금융지주 PDF·KOSDAQ no_filing 정당).
- **시그널 부여** (feat): proxy_advise 5서브툴 전수 audit 도출 — `expected × 결과None/0`의 AND로 자동감지(추가 호출 0). **corp_gov** 무결성(compliance None·교차검증 |stated − metrics 계산|>0.2·주주필드 PARTIAL), **financial** 금융업/지주 revenue None=`sector_na`(정당 N/A) vs `core_field_null`, **director** zero-candidate(인사안건>0 AND 후보0), **ownership** `blocks_present`. **large 100사 + 전수 검증, false positive 0**.
- **시그널 선별 — 가정 검증으로 폐기/수정**: ① **shm board/comp AND 강화 폐기** — 후보표 헤더 정규식이 원풍식 후보표 못 잡고(false negative) `has_comp`가 변경표 일반을 매치(너무 느슨)하는 섣부른 가정. ② **financial 키 버그** — 초기 `data['revenue_krw']` 가정이 삼성전자까지 false positive(전 회사 None) → `data['summary']['revenue_krw']`로 수정. 코붕이 "금융업 매출 없다고 떠 원래?" 질의가 가정 부정 테스트를 유발해 키 버그 발견.
- **측정 함정**: director zero-candidate 픽스처 13건 중 이오플로우 등은 **픽스처 rcept(0306) ≠ director 실제 rcept(0317)** 측정 함정.
- **전수조사 검증(DART 실측, push 후)**: corp_gov 주주 100사 exact **71/71=100%**·음수재무 1→**10사**·내 시그널 fp **0** / ownership blocks 92/6 / **director zero-candidate 대형 100사 0% + 중소형 24사 중 4사(16.7%)**. 4사 본문 직접 확인 → 전부 **진짜 후보 추출 실패** 2패턴: ① 아남전자 **분리표**(성명·생년월일 별도 표) ② 에이텍·코오롱생명과학 **'사내이사 {이름} 선임의 건' 제목 인라인**. 시그널 false positive 0 확정, `parse_personnel` 2패턴 개선이 다음 과제. (후보이름 정규식이 분리표만 잡아 3사 0 오판 — 측정 도구 한계 재발, 본문 직독으로 교정)
- 교훈: 가정의 부정도 테스트해 확정 / 항상 regression(base 50·large 100·전수 300+ 엣지) / 측정 도구부터 의심.

## [2026-06-24] feat/fix | dilutive_issuance 교환사채(EB) 추가 + 정정/철회/누락 원문 복원

상세: [[260624_1503_fix_dilutive-exchangeable-bond]]

- **`dilutive_issuance`** (feat): 희석성 증권 4종→**5종**(유증/CB/**EB**/BW/감자). `exbdIsDecsn`(교환사채권발행결정) 추가. EB는 신주 희석이 아닌 **의결권 희석**(교환대상=자기주식 시 교환권 행사로 의결권 부활) 포착. 발단: "태광산업 희석 리스크" 질의에 0건 반환(자기주식 24.41% EB 3,185.8억 발행 후 철회를 못 잡음).
- **EB 보정 `_ensure_eb_coverage`** (fix): DART 구조화가 정정/철회 EB를 불완전 제공하는 3패턴 대응 — **(A) blank stub**(태광: 구조화 공란→원문 복원·병합) / **(B) 0건 누락**(한라IMS: 첨부정정만 있으면 013→list.json으로 존재 확인 후 새 행 생성) / **(C) 문서 014**(document.xml 미제공→탐지 전용 행으로 surface, no_filing 오인 방지). 구조화가 EB 완전 제공 시 list.json 생략(추가 호출 0).
- **검증**: 시장 전수 스캔으로 EB 발행사 **186곳 발굴 + 태광 = 187사 라이브** → PASS 219 이벤트 / DETECT 2(한라IMS·녹원씨엔아이, 첨부정정 014) / WARN 0 / FAIL 0. 자기주식·타사주식·다건(PS일렉 7)·초대형(HD조선 2.37조)·복원·탐지·no-EB 회귀 모두 정상. (기본 24개월 윈도우 밖 2024 상반기 EB 19곳은 0건=정상, 넓은 윈도우로 누락 0 확인)
- **전수 검증서 추가 버그 2건 수정**: (1) 원문복원 교환대상이 교환가액 *조정 산식 변수줄*(`A: 기발행주식수`)을 오인 → `_looks_like_eb_target`(산식 배제)+라벨 앵커+narrative 폴백(광동·동성·푸드나무 정확). (2) 구조화 complete+정정 stub 복원이 같은 EB 2행 생성(동성제약) → `_dedup_eb_rows`(회차,총액 그룹).
- docs: [[교환사채권발행결정]] 신규(검증 필드 + 3패턴), [[dilutive_issuance]] 5종 갱신.

## [2026-06-23] perf/docs | proxy_advise 주총 4-scope→advise 통합 (콜 -5) + wiki 체계 정비

- **`proxy_advise`** (perf): 같은 주총을 summary/agenda/compensation/aoi_change **4 scope로 따로** 불러 회차 선별이 4회 중복 → `shareholder_meeting`에 **advise scope 신설**(=full에서 results만 제외)로 1회 통합. **10사 cold 실측: 콜 49→44(-5), wall-clock 9/10 빠름, 핵심 자문 10/10 동일(파싱 정확성 불변), evidence board +1**. results fetch(네트워크)가 wall-clock 주범이었음 — investigate로 'summary 합치면 comp/aoi 보정 누락' 함정 차단. **order_contracts 병렬화는 throttle 하한이라 무이득 → revert** (콜 동일·순서만 병렬은 무효, 콜 수 절감만 실효). **production 라이브 10사 검증: advise scope 10/10 정상**(고려아연 95안건 분쟁·솔루엠 주주제안 자사주소각 포함, parsing_failures 0·콜 40~50). 단 **셀트리온 이사선임 후보 0명**(소집공고 본문 parse 실패)이 top-level `parsing_failures=0`에 미반영 — in-agenda 후보 파싱 사각지대 발견(advise와 무관, 별도 후속).
- **`wiki_lint.py`**: README 인덱스 drift 체크 [3] 추가 — 폴더 `.md`가 해당 README에 `[[]]`로 인덱스 안 되면 `--strict` 실패(CI 차단). 첫 적발로 decisions/README 11개 백틱→링크 전환.
- **wiki 체계**: architecture·archive·fixes·goals README 신설(누락 카테고리), lessons README 6 카테고리 인덱스, CLAUDE.md 작업원칙 3 + 판단모호 트리거 + 매핑 사각 보정 + 슬림화(89→78줄).
- lessons agenda-parser-validation-260621 (측정 함정 5패턴 + 검증 프로토콜).

## [2026-06-21] fix/docs | 주총 안건 파서 6사이클 production 배포 + 검증 회고 + lessons 분류

상세: agenda-parser-validation-260621

- `parser.py` (안건/정기·임시) — 6사이클 배포
  - `detect_meeting_type` 재작성(섹션 오선택 → 소집공고 직후 40자 앵커, 880→888) + `detect_meeting_type_conflict`
  - 안건 marker/zone·제목 bleeding 경계·인라인 하위안건 분리(`_split_inline_subagendas`)
  - **proposer(제안주체) 복원** — 주주제안 안건 `source` 전파(제목형 `_detect_source_in_marker` + 그룹헤더형 `_propagate_proposer`), 다원시스 라이브 확인
  - 빈 제목 부모 추론 `_fill_empty_parent_titles`(제N호 제목 없이 제N-M호로만 시작: 파인디앤씨·스튜디오드래곤, 10→3)
- 검증 픽스처 891 → **3,016**(2026 3/1~5/15 코스피·코스닥, 정기 2,849) — 표준 검증셋
- docs: lessons `agenda-parser-validation-260621`(측정 함정 5패턴 + 검증 프로토콜) / CLAUDE 작업 원칙 3가지 + "판단 모호 시" 참조 트리거 / lessons README 6 카테고리 인덱스(물리 이동 X)

## [2026-06-16] fix/audit | render 출력 점검 (11 tool·410사) — 화면 이상 3종 + evidence 노출

상세: render-output-audit-260616

- `proxy_advise` (render)
  - facts에 dict(`candidate_review_profile`) 통째 raw 노출 차단 → 요약 (None·숫자 새어나옴 근본원인)
  - 계열만 있는 회사 '외부 수주 0건 0억원' 군더더기 생략 (external_count 기준)
  - **후보 독립성 sub_factor 근거 화면 구조화 노출** (2년 직원 경력 raw 등 — 애널리스트 검토용)
  - 경고 아이콘 ⚠️ 통일 (등급 색상 🟢🟡🟠🔴은 유지)
- `order_contracts` (render)
  - 'None%' 노출 방지 — `.get(key,'-')`는 None값에 default 안 씀, `_pct()` 헬퍼로 방어
- 진단 도구: `scripts/render_anomaly_scan.py` (11 tool render 이상 스캔, 410사·52분 실증)

## [2026-06-15] fix/audit | 주총 안건 파싱 점검 (진행중) — 보수한도 단위·폴백 + agenda 카테고리

상세 + 남은 작업: shareholder-meeting-agenda-parse-260615

- `shareholder_meeting` (compensation/agenda)
  - 보수한도 **단위 미환산** 수정 — 표 헤더/raw '(단위: 억원/백만원)' fallback 환산 (두산 630억·LG엔솔 60억)
  - 보수한도 **폴백 4종** — 셀 오염 선행숫자 추출·한쪽 누락 유효값+플래그·외화/단위미상 플래그+raw·parse_status/warning
  - **agenda 카테고리 분류** 추가 — proxy_advise `_classify_agenda`를 agenda scope에 적용 (category None 100%→0)
  - 방법론: 코스피+코스닥 255사 진단 → 실패 유형 → 폴백 → before/after regression(정확도·속도 회귀 0)
- 진단 도구 신규: `scripts/compensation_parse_diagnosis.py`, `agenda_parse_diagnosis.py`, `top50_compensation_audit.py`
- **남은 작업**: 안건 종류별 세부 파싱 — 선임(34%)/재무제표(17%)/정관변경(16%) 순. 같은 방법론 반복.

## [2026-06-14] refactor/perf | order_contracts 일원화 + proxy_advise 펀더멘털 fact·동적 lookback

상세: order-contracts-260613, proxy-advise-perf-fact-260614

- `order_contracts`
  - 해지(termination) 파서 전수조사 — 계약명(해지내용/세부물건)·상대방(영문사·슬래시·장문) 67건 검증, 누락 0
  - corporate_deals 공급계약(supply_contract scope) 완전 제거 → order_contracts 단일 소스로 일원화. corporate_deals는 타법인주식(지분 인수/매각) 전담
  - 부정확 추론 제거(파싱만): 체결↔해지 매핑(26%)·순수주·계열일감 규모(관계 미기재 83%) — 전 업종 662사 측정 후 결정
  - 계열 판정에 최대주주·모회사 키워드 추가 (현대오토에버↔현대차 오판 수정)
- `proxy_advise` / `director_performance`
  - 성과 매트릭스 점수는 ROE/부채/CSR 3축 유지, 펀더멘털은 점수 미반영 fact로 분리: 영업이익률(본업 수익성, ROE 왜곡 보완) + 수주·해지
  - treasury lookback 동적화 36~120개월(재직기간 맞춤, 상한 120) — 정확도 보존 20사 mismatch 0
  - order_contracts fact 경량화(문서 30→10), 성능 병목 측정 — throttle 직렬화가 종합 tool 하한
- `corporate_deals`
  - 공급계약 제거로 equity 전담 + 경량화(0.2초). 해지 파서는 일원화 전 order_contracts 헬퍼 공유로 임시 보강했다가 제거

## [2026-05-31] feat/audit | value_up role extraction + financial_metrics Tier 1 지표

- `value_up`
  - 최신 공시 1개 중심 출력에서 `latest_plan` / `latest_status` / `latest_result` / `meta_amendment` 역할 분리로 변경
  - `계획서 명칭`을 읽어 `report_nm`만으로 구분이 안 되는 plan/progress/meta 문서 성격 보정
  - `이행결과`는 명시적으로 발견될 때만 `latest_result`로 노출하고, 없으면 `null`
  - `이행현황`, `이행 현황`, `이행내역`, `이행 내역`, `진행 현황`은 `implementation_status`로 보수 태깅
  - 요청 구간에 고배당기업 재공시 등 meta만 있으면 최근 2년 role backfill로 본계획과 최신 이행현황 보강
  - Plain language: 밸류업은 "무엇을 하겠다는 계획"과 "지금 어디까지 했는지"를 같이 봐야 하므로 plan/status를 기본 축으로 두고, result는 실제로 있을 때만 꺼낸다.
- 전수조사:
  - KOSPI500 + KOSDAQ150, 2024-01-01 이후 562개 value-up filing 점검
  - `meta_amendment` 28건 비교: meta는 최신 공시일 수 있어도 최신 progress 대체물로 쓰지 않는 정책 확정
  - 산출물: [[260530_audit_value-up-implementation-tags]]
- `financial_metrics`
  - CFO/순이익 비율 추가
  - Tier 1 운전자본 회전일수 DSO/DIO/DPO/CCC 추가
  - 추가 API 호출 없이 기존 재무 detail에서 산출
- 검증:
  - `78 passed`
  - `wiki_lint --strict` passed
  - 라이브 확인: KB금융/KT&G에서 meta 재공시와 본계획/이행현황/result 분리 확인
- commit: `3cf1776`

## [2026-05-25] fix/audit | agenda relation + 주총소집공고 parser KOSPI300 재검증

- `shareholder_meeting_notice`
  - 정기/임시 판별을 소집공고 제목부 `(제N기 정기|임시)` 우선으로 보정
  - `제N호 의안.` 마침표형 안건 marker 지원
  - 후보자 표 헤더 boundary 보강
  - `4. 목적사항` 정정공고형 안건 목록 지원
  - `※` 주석 뒤 다음 안건이 사라지는 케이스 수정
  - `no_filing` warning에 결산월과 예상 정기주총 window 표시
- `proxy_advise_before_meeting`
  - full agenda tree 기반으로 relation metadata 사용
  - 절차성/조건부/대안형 안건은 법령 layer hit가 없으면 REVIEW guardrail 적용
  - 집중투표 slate는 행사 의결권 기준 필요최소지분율 fact 노출
- 검증:
  - KOSPI300 재실행: `exact` 298 / `no_filing` 2 / `requires_review` 0
  - 남은 2건: 신영증권(3월 결산), 프레스티지바이오파마(6월 결산) — 현재 DART 기준 정기 소집공고 없음
  - 테스트: `23 passed`
- 문서:
  - [[260525_0200_audit_agenda-relation-kospi300]]
  - agenda-relation-parser-260525

## [2026-05-25] docs | proxy_advise layer consistency 보장 범위 명문화

- `proxy_advise_before_meeting` 문서에 layer consistency guarantee 추가
  - 모든 안건이 law layer에 걸리는 것이 아니라, 파싱된 안건에 동일 schema와 동일 판단 순서가 적용된다는 점 명시
  - law layer → relation REVIEW guardrail → 일반 decision path → policy default 순서 정리
- audit/lesson에도 같은 해석을 반영
  - relation metadata는 결론이 아니라 자동 판단을 멈추는 guardrail
  - 사용자-facing report에서는 "모든 안건이 layer에 걸림"이 아니라 "같은 schema와 판단 순서로 리포트됨"으로 설명

## [2026-05-12] docs | proxy_advise Word 보고서 설계 고정, 구현은 TODO로 이월

- `wiki/architecture/proxy_advise_word_report_spec.md` 신규
  - `proxy_advise` 이후 사용자가 "문서화", "워드", "보고서"를 요청했을 때 따를 source-of-truth 스펙
  - 입력 범위: `samples/` 자문사 샘플 + 현재 OPM `proxy_advise` payload/renderer/code 구조
- `wiki/architecture/proxy_advise_word_report_design.md` 신규
  - 샘플 PDF 포맷 비교
  - OPM 데이터 매핑 가능 범위와 부족 필드 정리
  - 기본 권고안: **1페이지 요약 + 안건별 본문 + 후보/근거 부록**
  - v1(현재 payload 기준 즉시 구현) / v2(추가 파생 필드 포함) 분리
- `wiki/architecture/audits/README.md`에 위 설계 문서 연결
- 설계 단계 결론:
  - 같은 세션 안에서는 `proxy_advise_before_meeting` 1회 결과만으로 문서 초안 작성 가능
  - 다만 Claude web + MCP connector + fly.io 배포 경로에서 **재호출 없는 문서화 보장**은 서버 쪽 설계가 필요
  - 후속 구현은 별도 시점 문서나 이슈로 이월:
    - Word 템플릿/내보내기 구현
    - MCP용 report-friendly payload 또는 report tool 경로 결정

## [2026-05-10] ralph | Ralph 7 — 호수 hierarchy 추출 + D 패턴 amendments body fallback

**iter 1 (commit be2e722)**: parser 호수 추출 진단 (10/10 회사). 사용자 가설 "parser가 호수 누락"은 false — parser 거의 완벽. LG화학 ※ note span 미세 버그 1건만 fix (lookahead 괄호 옵션 추가).

**iter 2 (commit e2292d8)**: D 패턴 amendments body fallback logic 구현
- `_is_charter_top()` + `_law_layer_body()` + 호출부 fallback (`title_to_children_count` map)
- 단위 검증: 에스엠 A1-5 catch + LG화학 regression 0 (children > 0이라 D 진입 X)

**iter 3 (commit 9d15aed)**: 룰 catalog `body_pattern` 별도 필드 추가 (스키마 확장)
- A1-1 body_pattern: secondary 확장 (적용 안 / 적용하지 아니 등)
- A1-7 body_pattern: any_of 확장 (제542조의14, 제542조의15 법령 인용)
- 검증: 4 미매치 회사 중 D 패턴 3개 모두 catch (에코프로비엠 A1-1 / 에스엠 A1-5 / 메리츠 A1-7)
- 카카오게임즈는 D 패턴 X (sub-agenda 있고 sub title 일반 표현) — 별도 ralph 후보

**iter 4-5 (✅ 완료)**: 510 회사 spot 회귀
- 회귀 0 (기존 hits set ⊆ 신규 hits set, 510/510 회사)
- title 신규 catch +21 (모두 A1-1 — Ralph 6 "변경" 키워드 효과)
- **body fallback 신규 70건 (69 회사 = 13.5%)**
- D 패턴 진입 216건 (510 중 42%)
- A1-8 (자사주 의무소각) **첫 활성** — 미사용 룰 lesson 첫 catch
- B2 layer body fallback 작동 (B2-1 2건)

**iter 6 (✅ 완료)**: 문서화 + promise 발행 (AGENDA_HIERARCHY_EXTRACTION_VERIFIED)

## [2026-05-10] ralph | Ralph 8 — 카카오게임즈 패턴 sub→amendment 1:1 매핑

26 진정 카카오게임즈 패턴 회사 (Ralph 7 식별 510 중 5.1%) 처리 별도 architect.

**핵심 design**: 진입 조건 (parent에 정관변경 + sub children 0 + sub generic 아님 + amendments) + strict cascade (label substring → clause 매칭, **keyword 매칭 의도적 제외** — semantic mismatch false positive 회피).

**iter 1 (commit 27db7dd)**: 26 회사 매핑 가능성 정량화 — 102 sub 중 clear 14.7% / partial 60.8% / none 24.5%.

**iter 2-3 (commit b1f2f76)**: 코드 구현 + 단위 검증
- `_is_charter_top` / `_is_generic_sub` / `_map_subagenda_to_amendment` / `_law_layer_subagenda_mapped` 헬퍼
- 호출부 0-c 단계 추가 (D 패턴 fallback 다음)
- LG화학 regression 0 (keyword 매칭 제거 fix — "선임독립이사 선임" → "독립이사 명칭 변경" semantic mismatch 사례)

**iter 4-5 (✅ 완료)**: 510 회사 spot
- 회귀 0 (회사, rule 단위 set diff)
- **sub 신규 75건 / 55 회사 (10.8%)**
- KOSPI 23.1% catch (대형사 sub-hierarchy 명확) vs KOSDAQ 4.7% / 0%
- 미사용 룰 A1-3 (18건) / B1-8 / A1-2 활성

**iter 6 (✅ 완료)**: 문서화 + promise (SUBAGENDA_AMENDMENT_MAPPING_VERIFIED)

## [2026-05-10] ralph | Ralph 9 — 사외이사 충실성 강화 + 사내이사 독립성 표기 정정

메리츠금융지주 proxy_advise 응답 검토 시 사용자 피드백 반영:
1. 김용범 사내이사 "독립성 충족" 표시 부적절
2. 사외이사 겸직 카운트 추가 (다른 회사 사외이사 또 하면 우려)
3. 최대주주 특수관계인 → 독립성에 유지

**iter 1-2 (audit data)**:
- 510 회사 careerDetails 가용성 audit — 98.4% 채워짐
- 단순 키워드 카운트 false positive 발견 (본 회사 사외이사 표기)
- logic v3 (본 회사명 매칭 + 후보 본인 보장): concerns 64 / strong 13 회사

**iter 3-4 (코드 + 단위 검증)**:
- `count_outside_director_positions` 헬퍼 (director_evaluation.py)
- faithfulness 통합 (concurrent_outside_directors)
- 사내이사 "독립성 평가 비대상 (사내이사)" 표기
- 단위 검증: 김용범/김정연(strong)/박진규(concerns)/조홍희(single) ✓

**iter 5-6 (회귀 + 문서화)**:
- decision 변경 0 (audit_history_check만 활용 유지)
- facts 신규 노출 (concurrent_outside_positions / concurrent_summary)
- promise: DIRECTOR_FAITHFULNESS_ENHANCED ✅

## [2026-05-10] fix | proxy_advise 응답 품질 — 묶음 후보 detail + raw 중복/매핑 (commit 7f1b88c, 4fec268)

사용자 응답 품질 측정 (메리츠/LG화학/카카오게임즈) 후 발견 3가지 중 2가지 fix:

**fix 1 — 묶음 안건 후보 detail 노출**:
- facts.candidate_summary 추가 (후보별 이름/role/appointment/독립성/결격/겸직)
- 50 회사 검증: 25/50 묶음 안건 → 160명 detail 노출 (이전 0)

**fix 2 — raw 첨부 중복 회피 + sub→amendment 매핑 활용**:
- 회사 단위 첨부 flag (_amendments_attached_for_company) — 첫 미매핑 안건에 모든 amendments / 다음은 anchor
- Ralph 8 매핑 활용 (_subagenda_attempted_mappings) — 매핑 성공 sub는 자기 amendment 1개만
- LG화학 5980→2951자 (-50%) + amendments [5][6][7] (미catch sub 본문) 노출
- 50 회사 검증: anchor 61건 ~76KB 절약 / 매핑 10 회사 (현대차/KT&G/카카오/현대모비스 등)

**검증 결과**:
- KT&G B1-8 sub-mapped + B1/B2 raw 첨부 동시 작동 (Ralph 6/8 호환)
- decision logic 영향 0 (facts 노출 + raw 첨부만)
- 회귀 0 / 에러 0

**문제 3 (reason vs raw 신호 불일치)**: LLM 위임 (skip).

**핵심 안전장치 (Ralph 6 회귀 회피)**:
- D 패턴 strict 진입 조건 (LG화학 같은 sub 명확 회사 자동 제외)
- amendment 단위 검사 (모든 amendments 통합 X)
- body_pattern 별도 필드 (title 매칭 회귀 위험 0)

## [2026-05-10] fix | production wiki/rules/laws/ 누락 — Dockerfile COPY 추가 (★ 중대)

**핵심 발견**: production /app에 wiki/ 디렉토리 자체 없음 (Dockerfile COPY 누락).
- `law_layer_rules.json` (38 룰) load 실패 → empty list
- `_law_layer()` 항상 None → **38 법령 룰이 production에서 작동 X**
- LG화학 misread 사례의 진짜 원인: LLM hallucination이 아니라 production 코드가 38 룰을 못 봄

**fix (b5951a4)**:
- Dockerfile에 `COPY wiki/rules/laws/ wiki/rules/laws/` 추가
- v355 deploy 완료 (production /app/wiki/rules/laws/ 4 파일 활성)

**llm_misread_patterns.json 신규 catalog (6 패턴)**:
- M-1: 배제 조항 삭제 / M-2: 의결권 제한 강화 / M-3: 독립이사 명칭 / M-4: 전자주총 / M-5: 분리선출 / M-6: 자기주식 의무소각
- proxy_advise._load_llm_misread_patterns + _find_misread_guard dynamic load
- 새 misread 패턴 발견 시 JSON 한 줄 추가 — 코드 변경 X
- Tool description (Layer 1)에 ⛔ CRITICAL 가이드 inline (Claude 매 호출 전 system prompt에 포함)

## [2026-05-10] feat | proxy_advise B1/B2 raw 첨부 + decision 시각 강조 + 운용사·NPS 익명화 4 Phase

**LLM misread 방지 (LG화학 사례 trigger)**:
- 사용자가 LG화학 호출 시 LLM이 proxy_advise FOR 응답 무시하고 안건명("집중투표 배제")만 보고 자체적으로 AGAINST 추측 → 잘못된 분석 제공
- 원인: decision 시각 강조 부족 + reason truncate (80자) → 법령 [A1-1] tag 잘림

**fix 1 (a87b0ab) — decision 시각 강조**:
- ✓ FOR / ✗ AGAINST / ? REVIEW → ✅ / ❌ / ⚠️
- 법령 layer marker: A1/A2 → 🛡️ 강행규정 정합/위반, B1/B2 → 🔍 우회 의심
- reason truncate 80 → 250자
- LLM 경고 박스: "법령 layer 태그는 강행규정 — LLM 자체 판단으로 뒤집지 말 것"

**fix 2 (312d731) — B1/B2 raw 첨부**:
- proxy_advise에 aoi_change scope 추가 호출 (cache hit으로 free)
- B1/B2 hit 안건 reason에 정관 변경 본문 raw `[clause 변경 전/후]` 첨부
- A1/A2는 결정 강제 유지, raw 추가 X (토큰 절약)
- _find_amendment_for_title: label/clause + reason/before/after fuzzy 매칭 (≥2 키워드)
- detail 섹션에 reason full 노출 (표는 250자)

**검증 (5 회사)**:
- LG화학: A1/A2 4건 / B1-10 1건 (raw ✓ — 분리선출 1명→2명 본문)
- KT&G: A1/A2 2건 / B1-8b 1건 (raw ✓)
- 현대차: A1/A2 2건 / B1 1건 (raw ✓)
- Latency: 5-17s — 이전 대비 +1-2% (cache hit)
- regression 0

**운용사·NPS 익명화 4 Phase (보안 sweep, 9 commits)**:
- Phase 1 (d015bfa): tool description vote_style 옵션 list 제거 + README 표 제거
- Phase 2 (fdef0d1): `_VOTE_STYLE_POLICY_FILE` 실명 alias 9개 제거 (익명만)
- Phase 3a (2d4e8ed): wiki/ + docs/ 124 파일 batch 익명화
- Phase 3b (d882f87): open_proxy_mcp/ 27 파일
- 추가 (2a81b8a): sa_active → sa_legacy (실제 운용 스타일)
- 추가 (d5fb8b2 + 1c9dae7): 외부 자문사명 일반화 + 외부 advisor 항목 제거 (b_foreign에 흡수)

**최종 익명 catalog**: m_legacy / s_legacy / sa_legacy / k_legacy / t_activist / a_activist / c_activist / b_foreign / n_pension (9개) — manager_aliases.json (gitignored, local) v4.

**검증 sweep**: git tracked (raw 제외) 운용사·자문사 실명 잔존 0.

## [2026-05-09] perf | financial_metrics yoy scope 병렬화 (3배 단축)

**효율성 audit (Explore agent)**:
- 37 파일 (services 21 + tools_v2 16) 중복 호출 / 비효율 패턴 점검
- 6 발견 사항 (Sequential 1 / 중복 fetch 3 / parsing 중복 1 / scope 중복 1)
- cache 인프라 견고 (memory LRU 200 + sqlite 24h + disk) → 실제 API 추가 제한적

**진행한 fix (#1만)** — 다른 fix는 trade-off로 skip:
- `services/financial_metrics.py:1206-1218` yoy scope:
  - sequential `await x; await y; await z` (3-9초)
  - → `asyncio.gather(curr, prev, audit)` 병렬 (1.0~1.4초)
- 검증: LG화학/셀트리온/NAVER cold start 모두 1초대 / status=exact / curr+prev / alerts 정상
- regression 위험 0 (read-only API + 독립 인자 + tuple unpacking만)

**Skip 결정**:
- #2 audit_opinion module cache: stale risk (사업보고서 신규 발행 시 옛 데이터)
- #3 block_holders 인자 전달: 효과 작음 (fallback 발동 빈도 낮음)
- #4 retirement parsing 통합: 호출 흐름 변경 → 다른 회귀 가능

**효과**: 100 회사 배치 시 yoy scope 3-7분 단축.

**commit**: 521b64b

## [2026-05-09] docs | wiki 트리 정책 명문화 + lint hook + CLAUDE.md 정리

**Wiki 그래프 audit (260509_wiki_graph_audit)**:
- 252 페이지 × 1261 edges 분석
- Orphan 26 (10.3%) / Weak 35 (13.9%) / Leaf 58 / Unresolved 57
- rules/concepts hub 강건 (자사주 27 incoming) / 시점 페이지 외부 link 빈약 / decisions 명명 혼재

**트리 metaphor 명문화 (WIKI_SCHEMA Section 0)**:
- 🌱 뿌리 raw → 🪵 줄기 rules → 🌿 큰가지 (decisions/arch/tools) → 🌾 잔가지 (ralph/audits/fixes/lessons) → 🍂 낙엽 archive
- Link 정책: 뿌리→줄기→큰가지 단방향 / 큰가지↔잔가지 양방향 / 잎↔잎 자유
- 시점 작업 4축 표준 (ralph ↔ audit ↔ lesson ↔ decision)

**ABCDE 정리 작업**:
- A. rules → 큰가지 link 34건 제거 (단방향 정책 적용, 52 페이지 정리)
- B. 큰가지 ↔ 가지 양방향 보강 (30 페이지, 단방향만 → 양방향 22쌍 추가)
  - tools↔audit: 0 → 22 양방향 / decision↔ralph: 1 → 7 / audit↔lesson: 0 → 3
  - 첫 시도 본문 손실 → revert 후 안전 검증선 95% 추가하여 재실행
- C. scripts/wiki_lint.py 신규 + .github/workflows/wiki-lint.yml CI 통합
  - 단방향 위반 + 양방향 결손 자동 검출 (--strict mode CI 차단)
- D. orphan 17 정리 (24 → 7) — ralph/README + audits/README + audits/data/README 신규
- E. CLAUDE.md 정리 + 구 *_RULE.md 7개 archive 이동
  - 7+1 카테고리 / 트리 흐름 / 시점 4축 / MCP 호출 우선
  - tools_v2 17 → 16 (실제), open_proxy_mcp/*_RULE.md → wiki/archive/tools/legacy_rules/
  - 124 → 109 lines 가벼움화 (-15, -12%)

**DART-OpenAPI 검증 + DS003 섹션 추가**:
- archive 검증: wikilink resolve 7/7 ✓ / 기본 내용 정확
- 13 API 누락 점검: 10개는 data-collection.md DS005에 있음 ✓
- 누락 3개 추가 (DS003 — financial_metrics): get_audit_opinion / get_fnltt_singl_acnt / get_fnltt_singl_indx

**최종 상태**:
- Wiki 페이지: 252 → 264 (+12 README 등)
- 총 edges: 1261 → 1558 (+297)
- 단방향 위반: 34 → 0 / 양방향 결손: 44 → 0
- lint --strict 통과 ✓

**artifacts** (10 commits):
- `wiki/architecture/audits/260509_wiki_graph_audit.md`
- `wiki/WIKI_SCHEMA.md` (Section 0 트리 정책)
- `scripts/wiki_lint.py`
- `.github/workflows/wiki-lint.yml`
- `wiki/ralph/README.md` / `wiki/architecture/audits/README.md` / `wiki/architecture/audits/data/README.md`
- `wiki/archive/tools/legacy_rules/README.md` (구 *_RULE.md 7개 + 흡수 매핑)
- CLAUDE.md / wiki/architecture/data-collection.md update

## [2026-05-08] audit | 파서 정밀화 검증 — 보강 불필요 (Ralph 5)
- ralph: `wiki/ralph/260508_0207_ralph_parser-precision.md` (1+4 iter / promise 발행)
- 발견 (parser audit follow-up):
  - parse_personnel_xml careerDetails 누락 가설 부정확 (44회사/225후보 0% 누락)
  - parse_aoi_xml amendments 누락 1.66% (KOSPI 200 / 3건 모두 source 한계 — 별첨 PDF)
  - 두 파서 강화 ralph 불필요 결론
- audit 자체 정확성 issue:
  - parser audit (260508_parser_audit)는 코드 정적 분석 + TO_DO 정보 기반
  - TO_DO 정보가 stale (옛 batch v7b 시점) → audit 결론 부정확
  - audit는 가설, ralph가 실측 검증 — 두 단계 분리 패턴 재확인
- 다음 후보 재정렬:
  - 🟡 _law_layer 룰 슬림화 + amendments raw 통합 (LLM 판단 영역 명시화)
  - 🟢 PDF fallback (3-tier 2단계) 검증
  - 🟢 _classify_director_tenure logic (5년 룰)
- artifacts:
  - `wiki/lessons/parser-precision-260508.md`
  - `wiki/architecture/audits/260508_parser_audit.md` (실측 결과 추가)
- code 변경 X

## [2026-05-08] feat | 법령 layer 정밀화 — B1-4 분기 + B1-8b 신규 + B1-7 보강 (Ralph 4)
- ralph: `wiki/ralph/260508_0500_ralph_law-layer-precision.md` (6 iter / promise 발행)
- 발견 (Ralph 3 follow-up):
  - B1-4 false positive (정관변경 vs director_election 의미 혼선)
  - KT&G 2025 사전 우회 사례 미발견 (안건 title 일반 표현 — 본문에만 "별개의 조" 키워드)
  - B1-7 패턴 협소 (하이브 "정원 상한 축소" 미스 — "정수"만 매치)
- 룰 변경 (36 → 38):
  - B1-4 분기: parent_must_contain=["정관"] 추가 (정관변경 한정)
  - B1-4b 신규: parent_excludes=["정관"] + 후보 임기 1년 reason
  - B1-8b 신규: applies_after=2024-01-01 + 자산 2조+ + 정관변경 이사 선임/정원 변경 catch
  - B1-7 보강: "정원" + "상한" 키워드 추가
- `_agenda_pattern_match()`: parent_must_contain / parent_excludes 패턴 키 신규 지원
- 광범위 검증 (266 unique 회사 / 2792 안건 / 213 hits / 7.6%):
  - KOSPI 200: 9.8% / KOSDAQ 100: 1.8% / 분쟁 20: 11.6%
  - false positive 0 / 회귀 0%
  - B1-4b 8건 폭발 (영풍 6 + 현대엘리베이터 + 효성티앤씨) — 분쟁 시그널 효과
- artifacts:
  - `wiki/rules/laws/law_layer_rules.json` (38 룰)
  - `wiki/lessons/law-layer-precision-260508.md`
  - `wiki/decisions/260508_0700_decision_law-layer-precision.md`
  - `wiki/architecture/audits/data/260508_law_layer/iter08_*.json` (KOSPI 130-200 / KOSDAQ 0-100 / 분쟁 20)

## [2026-05-08] feat | 법령 layer 도입 — 1·2·3차 상법 개정 + 36 catalog (Ralph 3)
- ralph: `wiki/ralph/260508_0130_ralph_law-layer.md` (7 iter / promise 발행)
- 발견 (코붕이 review): LG화학 정관 sub 안건 잘못 분류 (운용사 정책 stale + hardcoded 키워드 stale)
- 1·2·3차 상법 개정 web 검증 (김·장/신·김/지평/태평양/율촌/Deloitte/삼일/FNguide)
  - 1차 (2025-07-22): 이사 충실의무 + 독립이사 + 3% 룰 + 전자주총
  - 2차 (2025-09-09): 자산 2조+ 집중투표 의무화 + 분리선출 2명 이상
  - 3차 (2026-02-25): 자사주 의무소각 + 합병/분할 신주 배정 금지
- 36 catalog (코붕이 정밀화):
  - A1 (FOR) 8 — 법 정합
  - A2 (AGAINST) 5 — 법 위반
  - B1·B2 (REVIEW) 19 — 법 테두리 안 우회 의심
  - C (risk_factors) 4 — ownership 신호
- 핵심 원칙: AGAINST는 명백한 법 위반만, REVIEW는 법 테두리 안 모든 의심 (B1·B2 둘 다 REVIEW)
- 구조: `Layer 1 법령 → Layer 2 vote_style → Layer 3 hardcoded` 우선 적용
- 검증:
  - LG화학 5/5 핵심 안건 [법령 X-Y] tag 정확 분류
  - 자산 2조+ 30 회사 spot 39 hits (A1-5 11 / A1-1 10 / A1-7 7 / A1-4 5 / A1-2 3 / B1-10 3)
  - 새 패턴 발견 X
- 운용사 7→8 표기 통일 (open_proxy_v1.json + open-proxy-guideline.md + wiki/index.md 등)
- OPM 4 기준 → 5 기준 (5번째 = 법령 layer 우선 + 의무·우회 분기)
- artifacts:
  - `wiki/rules/laws/상법-2025-2026-종합.md`
  - `wiki/rules/laws/상법-2025-2026-종합.md`
  - `wiki/rules/laws/law_layer_rules.json` (머신리더블 36 룰)
  - `services/proxy_advise.py` `_law_layer()` 추가
  - `scripts/spot_law_layer.py` 회귀 spot
  - `wiki/architecture/audits/data/260508_law_layer/iter05_kospi_top30.json`
- decision: [[260508_0200_decision_law-layer]]
- lesson: law-layer-260508

## [2026-05-08] audit | high-impact 분류기 audit 결과 (fix 불필요 확정)
- ralph: `wiki/ralph/260508_0030_ralph_classify-high-impact.md` (3 iter / promise 발행)
- 대상: `_classify_value_up_item` (value_up) / `_is_company_side` / `_is_retail_activism_side` (proxy_contest filer)
- 300 회사 sample (KOSPI 200 + KOSDAQ 100) 통합 audit
- value_up: 127 items / 19 unique 패턴 / **mismatch 0** — 견고
- filer 3-way: 255 filings / **99.22% 정확도** — 견고
  - mismatch 2건은 filer 분류기 이슈 X — 회사 resolver 모호 매칭 (셀트리온제약 → 셀트리온 잘못 해석)
- meta-lesson: audit script 측 버그 주의 (universe csv 약칭 vs DART 정식명 차이)
- 분류기 코드 변경 0 (견고 확인)
- lesson: classify-high-impact-260508

## [2026-05-08] fix | _classify_agenda 정관 sub-안건 분류 (mismatch 19.3% → 0%)
- ralph: `wiki/ralph/260507_2330_ralph_classify-agenda-fix.md` (4 iter / promise 발행)
- 발견 (코붕이 review): 롯데케미칼 proxy_advise 정관 sub-안건 NO_DATA
- 300 회사 audit (KOSPI 200 + KOSDAQ 100): mismatch 607/3145 = 19.3% — **모두 정관 sub-안건이 다양한 카테고리로 잘못 분류** (other/director_election/audit_committee_election/treasury_share/retirement_pay/cash_dividend/director_compensation/merger/shareholder_proposal/financial_statements)
- fix: `_classify_agenda(title, parent_title='')` 시그니처 추가 + parent에 정관 키워드 있으면 sub 안건 short-circuit articles_amendment
- caller (`proxy_advise._run`): agenda tree 순회로 title→parent map 추출 + 전달
- post-fix 검증 (300 회사 재 audit): mismatch 0.00% / 정관 sub 정확도 100% (607/607)
- 롯데케미칼 회귀: NO_DATA 2건 → 0건
- decision: [[260508_0030_decision_classify-agenda-parent-shortcircuit]]
- lesson: agenda-classification-260507

## [2026-05-07] perf | OPM 응답 속도 다수 단축 (10s → 4-6s 체감)
- 코붕이 review: "옛날엔 잘 됐는데 왜 지금 10초?" 분석 흐름
- fix: `auto_stop_machines = 'suspend'` (fly.toml, 04-13 자동 stop으로 덮어쓰여 cold start 5-15s 발생)
- perf: shareholder_meeting candidate doc fetch TOP_N=2 + fallback (정정공시 누적 시 5-8 doc → 2 doc)
- perf: 주총결과 KIND scraping → DART API 우선 (4-5s → 1.4-2s, ~3배 빠름)
- perf: search_filings에 `last_reprt_at='Y'` 옵션 (정정공시 자동 정리, summary 0.2-0.5s 단축)
- perf: ownership_structure 변동신고서 KIND → DART API (3.7s → 0.12s, 30배 빠름)
- perf: doc cache LRU 30→200 + TTL 24h (메모리 only, 영구 저장 X 원칙 유지)
- perf: tool description trim (-25%, 11,170 → 8,408 chars)
- perf: notice tool path에서 `_find_meeting_result_filing` 완전 제거 (auto 모드에서도 skip, fly logs 5초 gap 제거)
- perf: DartClient persistent httpx AsyncClient (16개 `async with httpx.AsyncClient()` → `self._http`, TLS handshake 200-400ms × N 절약)
- decision: [[260507_2330_decision_httpx-connection-pool]]

## [2026-05-06] fix | parser omnibus 검증 + DART 6컬럼 sub-column 처리 (PFS 100%)
- ralph: parser omnibus 성능 점검 (9 iter / promise 발행, private 이관)
- 300 회사 (KOSPI 200 + KOSDAQ 100) 통합 audit — Tier A 9 parser G1 ≥98.7% 모두 충족
- **핵심 발견**: DART 잠정 재무제표 html 6컬럼 row 패턴 — `account/note/empty/current/empty/prior`
  - 기존 `_build_column_meta` 가 `_period_by_num` 다음 colspan 확장 빈 셀을 "unknown"으로 분류 → row[2]/row[4] (empty)을 current/prior로 인식하여 모든 metric empty 추출
  - 19개 KOSPI 회사 (현대차/셀트리온/두산/기업은행/LG/KT 등) sparse 원인
  - 코붕이 피드백 "데이터 없는건지 잘못 검색한건지 별도 파서 필요인지 창의적으로 다시 생각" → raw html 직접 search → 매출액 186,254,472 명확 존재 → parser 버그 확인
- fix: `services/provisional_financial_statement.py`
  - `_build_column_meta`에 `_period_by_num_sub` 처리 (empty 셀이 `_period_by_num` 다음에 오면 sub-column 로 propagate)
  - `_METRIC_KEYWORDS.net_income_krw`에 `지배기업소유주지분` 등 4 변형 추가 (보조)
  - `_NON_FS_TABLE_HINTS` 추가 (영문 사명 ≥6 줄 — 종속회사 목록 reject)
  - `extract_metrics` `scope_used` 보고 버그 fix
- 19 sparse 100% PASS / 회귀 90 회사 PFS 100% / 최종 phase1 (n=357 OK) all G1 ≥98.7%
- v1 dead parser archive 결정 (logical only — code 보존)
- G4 layer 정합 검증 PASS — data tool 14 services + Tier A 9 parser decision 키워드 0건 / proxy_advise 8 `_decide_*` action layer
- 17 tool scope inventory — 추가 폐지/신설 결정 없음
- artifacts: `scripts/spot_parser_omnibus.py` / `scripts/spot_pfs_html_search.py` / `scripts/spot_pfs_sparse_recheck.py` / `scripts/agg_parser_omnibus.py` / `wiki/architecture/audits/data/260505_parser_omnibus/`
- lesson: parser-omnibus-260506
- decision: [[decisions/260506_2330_decision_v1-dead-parsers-archive]]

## [2026-05-06] feat | shareholder_meeting_notice scope 정리 + provisional_financial_statement 독립
- `shareholder_meeting_notice` scope: 6 → 5 (`summary`/`board`/`compensation`/`aoi_change`/`prov_financials`)
  - 폐지: `agenda` (summary 흡수, hierarchy 통합) + `full` (병렬 wrapper, 종합 분석은 proxy_advise)
  - silent fallback to summary (caller 깨짐 방지)
- `summary` 강화: agenda hierarchy + 1호 안건 메타 (회기/사업연도/배당 예정액) regex 추출
- `aoi_change` 보강: parse_retirement_pay_xml raw 통합 (data tool 원칙 — 판단 X)
- `prov_financials` 신설: 잠정 재무제표 4 quadrant raw (consolidated/separate × balance/income) + flat metrics
- result_status / result_reference 제거 (사후 정보, 시점 분리 위반)
- `services/provisional_financial_statement.py` 신규 (독립 모듈):
  - `parse_financials_xml` 본체 + 의존 helper들 통째로 이동 (parser.py 의존성 제거)
  - `parse_provisional_financial_statement(html)` + `extract_metrics(parsed)`
  - data/action tool layer 분리 정합 — data tool은 raw 노출, action tool (proxy_advise)은 extract_metrics로 facts evidence
- 구 `services/agm_first_agenda_fy.py` (정규식 텍스트 파서) archive
- universe csv 신규 (`260506_universe_kospi_200.csv`, `260506_universe_kosdaq_100.csv`, `260506_universe_kosdaq_150.csv`, `260506_universe_kosdaq_300.csv`, `260506_universe.xlsx`)
  - source: `esgQuant/.../멀티인덱스_dataguide.xlsx` (시총 내림차순, KOSPI 810 + KOSDAQ 1816 식별 가능)
- 검증 (삼성전자 2026 AGM): prov_financials 12 metric 정확 (매출 333.6조 등) / summary hierarchy + 1호 메타 + 정정공시 detect / aoi_change 정관 4건
- decision: [[260506_0030_decision_notice-scope-cleanup-prov-financials]]

## [2026-05-05] feat | 보수한도 / 퇴직금 분기 정밀화 (G1 99%+ / 5 AGAINST detect)
- 보수/퇴직 분기 wire 후속 검증 + parser 강화 + financial_metrics fetch chain fix
- 1차 ralph (260505_1750): 카테고리 분리 + hybrid wire — promise 미발행 (G1 retirement 40%)
- 2차 ralph (260505_2030): KOSPI 200 + KOSDAQ 50 (n=226) 확장 + parser fallback — promise 미발행 (G1 retirement 78.6%)
- 3차 ralph (260505_2200): 정밀화 — promise 발행 ✓
  - parser 강화 (commit `782af95`): anchor 검출 + 표 head 키워드 확장 (현재/개정(안)/개정전후) + 표 본문 "퇴직" broad-match
  - financial_metrics summary scope에 prev_net_income/yoy_pct 노출 (commit `8fe8bff`) — 흑자+yoy<0 trigger 활성화
  - 소진율 단독 강화 (commit `db44182`) — 소진율<30 + 인상률 미파악/동결 → REVIEW
  - 5 batch 재측정 (KOSPI 0-30 / 30-50 / 50-80 / 140-170 / KOSDAQ 0-30) — NEW parser 적용
- 최종 G1-G4 (n=226):
  - G1 파싱 성공률: director 99.2 / audit 100 / retirement **100** (이전 78.6) ✓
  - G2 trigger 정확도 100%: AGAINST 5건 — 피에스케이/피에스케이홀딩스/GST 지급률 2배수+ / 카카오페이 사외이사 퇴직금 (OPM #6) / 퓨쳐메디신 자본잠식+인상
  - G3 운용사 4+ majority 정합 100% (director 11/11, audit 1/1)
  - G4 N연기금 정책 정합 100% (N연기금 [별표 1] IV-33/34/35 + OPM Open Proxy v1.3 #6/#7/#8 trigger 일치)
- KT&G false positive 수정: 이전 REVIEW (퇴직연금 키워드 hit) → FOR (퇴직연금 제도 도입 형식적 변경)
- ralph: [[260505_1750_ralph_compensation-retirement-split]] / [[260505_2030_ralph_compensation-retirement-extend]] / [[260505_2200_ralph_compensation-retirement-precision]]
- decision: [[260505_1900_decision_compensation-retirement-split]]
- audit: `wiki/architecture/audits/data/260505_compensation_retirement_*` (3개 폴더)

## [2026-05-05] feat | 보수한도 / 퇴직금 안건 분리 (이사·감사 + 정관 hybrid)
- 발단: 코붕이 (이사·감사 보수한도 + 퇴직금이 어떻게 처리되는지 확인) → 갭 발견:
  1. 퇴직금이 `_decide_compensation` 같이 처리 → 인상률 데이터 없으니 fm_fallback FOR (사실상 자동 FOR, status quo bias)
  2. 이사/감사 분리 안 됨 (parser는 분리하나 결정은 합산)
- 해결:
  1. **카테고리 3 분리**: `director_compensation` (강화) / `audit_compensation` (NEW) / `retirement_pay` (NEW)
  2. **Hybrid wire** (코붕이 의견): 한국 회사 관행상 퇴직금/보수 변경은 대부분 "정관 일부 변경" 형식.
     `_decide_articles_amendment`에 retirement/comp helper 통합 — 같은 helper 재사용, 결정 logic 중복 X.
  3. **결정 분기**: 이사 13 분기 / 감사 11 분기 / 퇴직금 12 분기. 정책 근거 (N연기금 [별표 1] IV-33/34/35 + OPM Open Proxy v1.3 #2/#6/#7/#8 + 운용사 패턴) 모두 wire.
  4. **2 layer 원칙**: 정책 카탈로그 (정성+정량) + 결정 코드 (자동 trigger wire + 정성은 facts raw 노출).
  5. **Step 0 sample**: KOSPI/KOSDAQ 10 회사 spot — SK하이닉스 11 amendments / 고려아연 5 (황금낙하산 sample 0)
  6. **Step 0.5 운용사 majority cache**: 22 records 합산 → director 31 / audit 2 / retirement 1 4+ majority case (모두 FOR). AGAINST outlier: 하이브 (3대1) / 에코프로 (3대0).
- iter02 KOSPI 0-50 baseline (정관 우선 fix 전): director 20 (모두 FOR, g3 정합 100%) / audit 2 (FOR) / retirement 2 (REVIEW — KT&G "퇴직연금 정비")
- iter04 키워드 정밀화: "확정기여형/확정급여형/퇴직연금" 위험 → 형식적 (FOR) — KT&G false positive 회피
- iter03 hybrid batch (KOSPI 50 + KOSDAQ 30): 진행 중. 결과 측정 후 G1-G4 검증 + promise 가능 여부 결정.
- ralph: [[260505_1750_ralph_compensation-retirement-split]]
- decision: [[260505_1900_decision_compensation-retirement-split]]
- audit data: `wiki/architecture/audits/data/260505_compensation_retirement/`

## [2026-05-05] feat | 사내이사 재직 중 성과 매트릭스 (2x3) — status quo bias mitigation
- 발단: 코붕이 고려아연 케이스 비판 — proxy_advise 사내이사 분기는 결격사유만 검증 → 회사 추천 후보 자동 FOR. status quo 무검증.
- 해결: 재직 중 회사 운영 성과 axis 추가. 2x3 매트릭스 (ROE/부채비율/CSR × avg/trend), good +2 / mod +1 / weak 0 / bad -1.
- Special rules: 자본잠식 ROE/leverage avg 자동 bad, 적자+환원 CSR weak (자본잠식 가속), 적자+환원 자제 CSR moderate (보수성).
- decision branch: bad → AGAINST, weak → REVIEW, moderate/good/신임 → FOR. 묶음 안건도 동일.
- 데이터 chain (회사당 +2 호출): `dividend(history, 10y)` + `treasury_share(summary, 120m)` + `financial_metrics(yearly)`.
- threshold tune: ≥9→≥7 (KOSPI 100 baseline 7.7% 너무 보수적, ≥7로 26.4%·target 20-40% 충족).
- 검증 (KOSPI 100 + KOSDAQ 50, n=128):
  - G1 classification 노출률 **100%**
  - G2 적자 16건 모두 special rule 작동, 자본잠식 0건
  - G3 bad→AGAINST (한화오션 김희철, 삼성SDI 오재균), weak→REVIEW (HD현대중공업 금석호) 분기 작동
  - G4 distribution good 29.7 / mod 45.3 / weak 18.0 / bad 7.0 — 모든 target band 충족
- 추가 변경: Korean label 자연화 (weak_concerns → "약한 우려", concerns → "우려" 등 — `_INDEPENDENCE_LABELS` 등 dict)
- ralph: [[260505_1611_ralph_inside-director-performance-matrix]]
- decision: [[260505_1700_decision_inside-director-performance-matrix]]
- lesson: distribution-calibrated-thresholds (8번째 lesson — 임계값은 prior가 아니라 audit posterior에서 정함)
- audit: `wiki/architecture/audits/data/260505_inside_director_performance/` (KOSPI 4 + KOSDAQ 2 batch JSON)

## [2026-05-05] feat | DART OpenAPI 분당 1000회 hard rule 강제
- `dart/client.py`에 rolling window rate limiter (60s deque + asyncio.Lock), cap **900/min** (10% buffer + race 방지). 모든 `_request` 자동 throttle.
- 발단: treasury ralph 측정 중 KOSPI 100 batch (~1000 호출/min)로 두 차례 24h IP 차단 발생.
- CLAUDE.md "hard rule, 절대 위반 X" 명시 + memory `feedback_dart_openapi_rate_limit.md` 강화.
- 새 batch script: 회사수 × 평균 호출수 estimate, 최대 30 회사 단위 + offset arg + sleep.

## [2026-05-05] feat | treasury ralph iter 13~15 — G2 사이클 매칭 100% 달성
- iter 13 (rate-safe batch): 30 회사 batch + offset, KOSPI 100 G2 adj 98.16%, KOSDAQ 50 79.17% (합 91.40%)
- iter 14 (trust fallback fix):
  - `trust_termination_result` → `trust_contract` (사이클 시작 결정) fallback
  - trust 사이클 out_of_lookback 분류 (er_dt < 가장 오래된 trust_contract decision)
  - 신탁 본문 "체결일자" 라벨 추가 (휴젤 등에서 발견)
  - KOSDAQ 79.17% → 97.32% (+18%p)
- iter 15 (acq/dsp fallback + main_date noise):
  - `_parse_main_report_date` 강화: "주요사항보고서 제출일 : 최초제출일: ..." noise 30자 cover
  - "최초제출일" 라벨 단독 추가 (정정공시)
  - acquisition/disposal result도 단일 결정 fallback
  - KOSPI 100% (220/220), KOSDAQ 100% (112/112), 합산 100% (332/332)
- 모든 gate (G1 본문 파싱 100% + G2 사이클 매칭 100% + G3 phase + G4 scope) 충족
- normalize 보강 (iter10 fix): broker_name `cs_iv_bk`, price_*_krw `dpstk_prc_*`, holding_*_date `hdexpd_*`, before_div/before_other 보유현황 추가, 처분방법 4 field (dp_m_mkt/otc/ovtm/etc)
- audit: [[260505_0530_audit_treasury_execution_iter1-8]] (iter 11~15 추가 update)

## [2026-05-05] refactor | proxy_advise scope 10→1 + dead service archive
- proxy_advise: scope param 폐지, 항상 `decisions` 호출. specialized scope 9개 (agenda/candidates/financial/governance/ownership/policy_basis/proxy_battle/engagement/evidence/all) 폐지.
- 사용자가 raw 보고 싶으면 각 tool 직접 호출 (shareholder_meeting_notice / financial_metrics / corp_gov_report / ownership_structure / proxy_contest / value_up).
- decisions enrichment (facts / risk_factors / policy_citation / 근거 공고 / 후보 raw) 그대로 유지.
- archive (`wiki/archive/services/`): `proxy_guideline.py`, `proxy_guideline_scoring.py`, `policy_comparison.py` — 12 매트릭스 logic은 호출 X (dead). ralph G2 99.36% 검증은 OPM 자체 logic + vote_style JSON으로 도달.
- archive (`wiki/archive/tools/`): `screen_events.md`, `proxy_guideline.md`, `shareholder_meeting.md` (notice + results 분리됨).
- index.md / tools/README.md 16 tool 반영.

## [2026-05-04] feat | treasury_share 결과보고서 4종 ralph (iter 1~10)
- 결정 5종 (decision) + 결과 보고서 4종 (execution) 통합.
- ACODE 기반 본문 파싱 (DART 표준 서식 system field id) — G1 100% 안정성.
- 결정↔결과 사이클 매칭: 본문 "주요사항보고서 제출일" / "신탁계약 체결일" ↔ decision rcept_dt.
- scope 통합 6→2 (summary + annual). phase=decision/execution flag.
- KOSPI 100 audit: G1 100% / G2 adjusted 97.69% (lookback 밖 17건 제외).
- iter 10 normalize 보강: 보통/우선주 별도 + 위탁사 + 사외이사 + 보유예상기간 + 신탁기관 + 해지사유 + 처분상대방.
- 측정 보류 사유: opendart.fss.or.kr API 차단 (24h cool-down) — dart.fss.or.kr 본문은 정상.
- audit: [[260505_0530_audit_treasury_execution_iter1-8]]

## [2026-05-04] feat | proxy_advise framework enrichment ralph
- decisions 응답에 facts (정량 fact dict) + risk_factors + policy_citation + 근거 공고 (rcept_no) 추가.
- 후보 평가 (candidates_evaluations) 4 dimension raw: 결격사유 / 독립성 / 전문성 (main_job + 추천사유) / 과거 행적 (career_company_groups + audit_history_check).
- 신임/연임 auto detect (career_company_groups + main_job fallback).
- 1번 안건 FY 본문 raw 추출 (`agm_first_agenda_fy`).
- KOSPI 100 + KOSDAQ 50 검증: G1 100%, G2 0% FP, G3 99.5% classified, G4 98.6%.
- audit: [[260504_2200_audit_proxy_advise_framework_iter1-8]]

## [2026-05-04] refactor | tools_v2 정리 (17→16 + scope 통합)
- screen_events drop (외부 호출 0).
- proxy_guideline → internal (tools_v2 wrapper 삭제) — 후속 archive로.
- shareholder_meeting → notice (DART) + results (KIND) 두 tool 분리.
- dilutive_issuance / corporate_restructuring scope 단일화.
- ownership_structure 7→5 (treasury 제거 → treasury_share 사용 권장, timeline → blocks 통합).
- dividend CSR/TSR/policy_signals scope 폐지 (6→3).

# Operation Log

## [2026-05-04] fix + audit | parse_personnel ralph 7 iter — role 88.7→100% + regression 0
- iter4 role normalize + title fallback (가장 큰 성공)
  - `_normalize_role_value()` 노이즈 set 분류 + 표준 표기 (사외/사내/감사위원/상근감사 등)
  - alg 알 수 없는 case → raw 보존 (silent fallback X)
  - header 매칭 확장 ('이사구분/직위/구분/직책')
- iter6 period 단일 연도 + content year extract (+0.3%p)
- iter8 한자 이름 cover (`[一-鿿]`)
- 영문 검증 통과: KIM JOONYOUNG / Takashi Abe / Edward Chin 등 정상
- career_period 89.0% (target 95% 미달, 본문 데이터 한계 — parser fix 효과 X)
- batch v8 regression: 4+ majority 99.36% 유지 ✅
- audit: [[260504_0724_audit_parse_personnel_iter1-7]]

## [2026-05-04] feat + audit | proxy_advise rename + 9 scope 추가 — regression 0
- Step 1 rename: services/{advise_vote→proxy_advise, recap_vote→proxy_result} + tools_v2 + 옛 wiki archive (commit 7b06b75)
- Step 3 단순 expose 5 scope (agenda/candidates/financial/governance/ownership) (commit 6711228)
- Step 4a policy_basis — 모범 사례 + 특이 케이스 example 형태 (재설계, commit c937505)
- Step 4b/c/d proxy_battle/engagement/evidence 추가 (commit 543293e)
- Step 4e proxy_result.brief — vote_brief render 흡수 (commit 4a75b87)
- 200×3 batch 결과: exact 492 / error 6 / no_filing 99 — Phase 4와 완전 동일, 일관성 100%, cross-match 197/197 ([[260504_0028_audit_proxy_advise_rename_regression]])

## [2026-05-04] docs | proxy_advise/proxy_result 신규 spec + 검증 ralph
- [[proxy_advise_before_meeting]] (10 scope: decisions/agenda/candidates/financial/governance/ownership/policy_basis/proxy_battle/engagement/evidence)
  - 옛 prepare_engagement_case + build_campaign_brief 사전 부분 흡수
- [[proxy_result_after_meeting]] (2 scope: results/brief)
  - 옛 prepare_vote_brief render 흡수, followup 30일 윈도우 제거 (의도적 단순)
- [[260503_0002_ralph_proxy-advise-verification]] — 3 gate (일관성/정확도/사실정확성) 검증 ralph
- index.md 갱신 (Action 2 tool rename 표기)

## [2026-05-03] fix | 정정공고 4건 items[0] fallback 적용
- `value_up_v2.py:127, 130, 394`, `corp_gov_report.py:386`, `shareholder_meeting.py:395`, `tools/proxy.py:421`
- 표준 패턴: 정정 제외 우선 + 빈 결과 fallback (`(non_corr or items)[0]`)
- multi-upstream-pattern 페이지 4 위치 ✅ 표시 + 표준 코드 스니펫 추가

## [2026-05-03] audit | ownership_structure baseline — 패턴 fix 불필요
- 200×3: 100% 일치, max 1.8s, timeout 0 ([[260503_2345_audit_ownership_baseline]])
- proxy_contest와 동일 결론: DART endpoint 직접 호출은 fix 효과 미미

## [2026-05-03] audit | proxy_contest baseline — 패턴 fix 불필요 결정
- 200×3 baseline (fix 없이): 100% 일치, timeout 0, mean 1.09s ([[260503_2330_audit_proxy_contest_baseline]])
- 적용 판단 기준 정립: build_*_payload 재귀(적용) vs DART endpoint 직접(불필요)
- multi-upstream-pattern 페이지 갱신 (체크리스트 + 기준 추가)

## [2026-05-03] fix | recap_vote multi-upstream-pattern 적용 + 100% 일치 검증
- `services/recap_vote.py` 8 upstream gather에 5 요소 적용 (commit `21bdf58`)
- 200×3 batch: 일치율 100.0% (195/195), timeout 0 ([[260503_2304_audit_recap_pattern]])
- 패턴 일반화 입증 — advise_vote 특수 case가 아닌 OPM 표준

## [2026-05-03] fix + docs | advise_vote Phase 4 100% + multi-upstream 패턴 표준화
- `dart/client.py` `_load_corp_codes`: asyncio.Lock + 3회 retry (1/2/4s) + corpCode timeout 60→120s
- `services/advise_vote.py`: per-call wait_for(60s) + Semaphore(3) + process result cache + 명시 pre-warm
- `services/director_evaluation.py`: notices[0] → 시간 desc 최대 3개 fallback (정정공고 처리)
- 200×3 batch: 91.9% → 100.0%, timeout 15→0, regression 0 ([[260503_1847_audit_phase4_final]])
- 신규 [[architecture/multi-upstream-pattern]] — 5 요소 표준 + 적용 대상 체크리스트
- TO_DO: recap_vote / proxy_contest / ownership_structure 같은 패턴 적용 대상 등록

## [2026-05-02] feat | action tool 재편 (3 → 2, 시점 분리: advise/recap)
### 신규 (3 service + 2 tools_v2)
- `services/director_evaluation.py`: 후보 평가 3축 (독립성/충실성/결격사유) + Marco 시나리오
- `services/advise_vote.py`: 6 upstream 통합 + 안건별 FOR/AGAINST/REVIEW + 결정 사유
- `services/recap_vote.py`: 5 upstream + 후속 공시 30일 + gap 비교 X
- `tools_v2/advise_vote_before_meeting.py`: 운용사 의결권 메모 render
- `tools_v2/recap_vote_after_meeting.py`: 분기 보고서 render

### 제거 / archive
- 제거: `prepare_vote_brief` (advise 흡수), `build_campaign_brief` (advise/recap 분산)
- archive: `prepare_engagement_case` → `_archive/`
- 자동 디스커버리 18 → **17 tool**

### 매핑 분류 (코붕이 명시 지시)
- success: 정형 필드 직접 (안건/후보/지분/재무/감사의견)
- soft-fail: raw text 노출 (careerDetails / dutyPlan / recommendationReason)
- hard-fail: 메모/코드 모두 침묵 (형사/사적관계/동명이인/파산)

### Sanity (7 iteration)
- 정기: 삼성전자 / KT&G / KB금융 (Marco 활성)
- 임시: HMM (정관변경 1 안건)
- Edge: 알지노믹스 (자본잠식 회사)
- 회귀: financial_metrics + dividend 변경 0

### Phase 2 (별도)
- A5 A행동주의 12 회사 backtest
- A6 9 비교군 (8 운용사 + N연기금) backtest
- vote_style 정책 wire + 매트릭스 자동 채점 통합

## [2026-05-01] feat | financial_metrics tool Phase 1 (재무 4 endpoint 통합 신규)
### 신규
- DART client에 4 endpoint 추가: fnlttSinglAcnt + fnlttSinglIndx + fnlttSinglAcntAll + accnutAdtorNmNdAdtOpinion
- `services/financial_metrics.py` (1155 lines): 6 scope (summary/yearly/quarterly/yoy/qoq/audit_opinion), 51 metrics, 22 alerts, normalize_amount (괄호 음수 + 콤마 strip)
- `tools_v2/financial_metrics.py` (328 lines): MCP tool register, format_krw_human (조/억 변환), 6 scope render
- `tools/financial_metrics.md` (wiki tool 페이지, 12 섹션 + Flow mermaid)
- `architecture/audits/260510_financial_metrics_audit_통합정리.md`에 흡수 (초기 sanity audit)
- 17 tools → 18 tools 모든 documentation 동기화 (index.md / tools/README.md / README.md / README_ENG.md / CLAUDE.md)

### 검증
- 6 회사 sanity 100% PASS (삼성전자/KT&G/롯데케미칼/SK하이닉스/삼천당제약/오스템임플란트, 모두 status=exact)
- turnaround / operating_loss / continued_loss / receivables_surge / accruals_red 등 핵심 alert 정확 detect
- 기존 17 tool regression 0 (dividend 회귀 검증 통과, register_all_tools_v2 자동 디스커버리 18 모두 등록)

### Phase 2 (별도)
- vote_brief 통합 (재무 risk 신호 → 사외이사 후보 cross-check, Marco 시나리오)
- 매트릭스 dim 자동 채점 (이자보상배율/FCF/cfo_quality wire)
- 응답 시간 최적화 (asyncio.gather 병렬화)

## [2026-05-01] feat | wiki 재구조 (5+1 카테고리 + 명명 규칙) + 17 tools 진입점
### W1: 카테고리 재편 + prefix rename
- 154 파일 이동: `wiki/{old}` -> `wiki/{new}`
- 13 prefix rename (audit/fix/decision/debate/improvement) -> `yymmdd_hhmm_{type}_{title}`
- 1 통합: `architecture/matrix-system.md` (구 decision-matrix-design + matrix-auto-scoring)
- `raw/` 신규 (구 sources + raw 합침, 수정 금지 명시)
- `archive/` 신규 (흡수된 38 페이지 역사 보존)
- 카테고리: raw / tools / architecture / decisions / rules(concepts+disclosures+laws) / archive

### W2: tools/ 17 페이지 + README catalog
- `tools/{17}.md` 일괄 작성 (통일 schema: frontmatter + 12 섹션)
- `tools/README.md` catalog (도메인별 진입표 + 데이터 소스 매트릭스 + archive 매핑)
- 흡수된 archive: 18 analysis 페이지 -> tools/ 본문에 통합

### W3: 사용자 진입점 통합
- `index.md` 재작성: Quick Start 섹션 최상단, 17 tools + 카테고리 테이블 + 자주 쓰는 진입점
- `WIKI_SCHEMA.md` 재작성: 5+1 카테고리 정의 + 명명 규칙 + frontmatter schema (type별) + 신규 페이지 워크플로우
- `CLAUDE.md` 보강: 명명 규칙 명시 + raw 수정 금지 강조 + "처음 [[tools/README]]" 권고
- `README.md` + `README_ENG.md` 이미 17 tool 반영 완료 (W1)

### 통계
- 총 173 markdown + 29 binary
- raw 29 binary + 4 md / tools 17+1 / architecture 6+10 / decisions 14 / rules 31+36+3 / archive 48
- 깨진 link 0건

## [2026-04-29] docs | 배당·자사주 공시 10종 + 2026.03 신법 wiki 정밀 분류
### 신규 disclosures 페이지 (9종)
- **배당 4 신규**: 주식배당결정.md, 배당기준일결정.md, 분기배당결정.md, 감액배당결정.md
- **자사주 5 신규**: 자기주식취득결정.md, 자기주식처분결정.md, 자기주식소각결정.md, 자기주식신탁결정.md (체결+해지 통합), 자기주식의무소각-2026신법.md
### 업데이트 (1종)
- **현금배당결정.md**: 트리/필드 통합, 자회사판 중복 제거 명시, 11개 핵심 데이터 항목 표 추가
### 통합 비교표 (1신규)
- **comparison/배당-자사주-공시-종합.md**: 10종 + 2026.03 신법 종합 (의무/소스/필드/OPM tool/신법 영향/거버넌스 시나리오 4종)
### 핵심 발견
- **2026.03 신법 영향 정량화**: 소각결정 빈도 50건/년 → 200건+ 예상, 자사주 비중 7% → 1-2% 정상화
- **자사주 마법 차단 메커니즘**: `dpptncmp_cmpnm` 채워짐 + 분쟁 중 → against 절대
- **선배당-후결의 (2024 개정) 추적**: 분기마다 [[배당기준일결정]]+[[분기배당결정]] 2종 동시 제출 패턴
- **report_nm 함정**: 자기주식소각결정의 실제 등록명은 "주식소각결정" (자기주식 prefix 없음)
### 인덱스 업데이트
- index.md: Disclosures 섹션 (배당 5 + 자사주 5 신규), Comparison 섹션 (배당-자사주-공시-종합 신규)
### TODO
- treasury_share tool에 `scope=commitment_check` 신규 (1년 시점 자동 알람)
- `screen_events(treasury_pending_cancelation)` 신규 이벤트 타입
- 기존 자사주 보유분 2027.09까지 처리 추적 자동화

## [2026-04-29] feat | proxy_guideline tool + Open Proxy Guideline v1.2 + 12 의사결정 매트릭스
### Phase A: 7 운용사 데이터 파싱
- 정책 5건: opendataloader-pdf (s_legacy·sa_legacy·t_activist·kim·a_activist 1-4초)
- M레거시: vector glyph PDF → PyMuPDF DPI 120 raster + JPEG 70% → Upstage OCR 우회 (35KB md)
- B외국계: 외부 가이드라인 직접 채택 발견 → `policy_classification: b_foreign_self`
- 행사내역 15 xlsx → 통일 schema JSON (총 17,900 votes)
### Phase B-C: 합의 매트릭스 + Open Proxy Guideline v1.2
- `_consensus_matrix.json`: 7 운용사 79 토픽, consensus + majority 62%
- 7 페르소나 토론 (학자·운용사출신·소수주주활동가·자본시장변호사·상법변호사·글로벌ESG·법안리서처) + 모더레이터 통합
- v1.0 → v1.1 (B외국계 B외국계 + A행동주의 행동주의) → v1.2 (B외국계 reference 다운그레이드)
- v1.2: 12 카테고리 116 룰 + 11 novel topics + 2026 신법 7개 + §382의3 cross-cutting
- 12 의사결정 매트릭스 (100 dim, 76 빙고 패턴) — 운용사·자문사 단독 차별화
### Phase D: proxy_guideline tool (6 scope)
- `services/proxy_guideline.py` + `tools_v2/proxy_guideline.py`
- scopes: policy / record / predict / compare / consensus / audit
- audit가 정책-실제 갭 자동 검출 (s_legacy director_election 4.3% — `policy_strict_practice_lenient`)
- DART API 호출 0회 (정적 데이터, <100ms 응답)
### Phase E: prepare_vote_brief 통합
- `vote_style` 인자 (default `open_proxy`, 7 운용사 선택)
- `_build_proxy_guideline_brief()` — 안건 → 카테고리 분류 → 정책 룰 매핑
- 새 출력 블록 `## OPM 정책 권고`
### 산출물
- `wiki/decisions/`: open-proxy-guideline.md, decision-matrix-design.md, opm-guideline-debate-transcript.md
- `wiki/analysis/voting-policy-consensus-matrix.md`
- `open_proxy_mcp/data/asset_managers/` 14MB JSON
- `.gitignore`: open_proxy_mcp/data/ 예외 + wiki/sources/binary 무시

## [2026-04-24] fix | agenda 파서 boundary 보강 (공공기관·전각ｏ 마커 대응)
- 한국전력공사 임시주총 안건 title이 후보 테이블까지 길게 잡히는 현상 발견 (commit `6fe44d2`)
  - `_AGENDA_BOUNDARY`에 추가: `-\s*후보에\s*관한\s*사항`, `의안\s+후보자\s+임기`
  - 결과: "상임이사 선임의 건 - 후보에 관한 사항 의안 후보자..." → "상임이사 선임의 건"
- 강원랜드 임시주총 title에 "ｏ 후보 최우식" 잔류 발견 (commit `c22aa95`)
  - 원인: 전각 ｏ(U+FF4F) 마커가 boundary 미지원 (반각 ○과 다른 글자)
  - 마커 클래스 `[□◎●○▶·ㆍ]` → `[□◎●○▶·ㆍｏ]` 확장
  - boundary에 `ｏ\s*(?:제\s*\d+|후보)` 추가
- 8개 회사 회귀 테스트 통과: 한전·강원랜드·KT&G·한국가스공사·현대차

## [2026-04-22] feat | prepare_vote_brief에 corp_gov_report 통합 + 세부원칙 파서 수정
### prepare_vote_brief 거버넌스 통합
- `services/vote_brief.py`:
  - `build_corp_gov_report_payload` import
  - asyncio.gather에 `governance_payload` 추가 (shareholder_meeting × 3 + ownership + **governance**)
  - `governance_brief` 블록 신규: 준수율 / 준수·미준수 지표 수 / 미준수 라벨 상위 10개 / 의무여부 / 시장 / 최신 보고서 날짜
  - key_flags 자동 생성: 준수율 60%↓="낮다", 80%↓="보통", 95%↑="우수"
  - 구조적 약점 감지(집중투표/사외이사 의장/독립 내부감사 미준수 → structural 플래그)
  - quality.governance_status + evidence_refs에 governance 건 병합
- `tools_v2/prepare_vote_brief.py`:
  - 렌더러에 `## 거버넌스 (기업지배구조보고서)` 섹션 추가
  - docstring: upstream에 corp_gov_report 명시 / 자동 플래그 규칙 기술
- 검증: KT&G → 준수율 100% / 미준수 0개 / "우수" 자동 플래그 + evidence 삽입

### 세부원칙 파서 수정 (0건 → 6-7건)
- 정규식 문자 클래스에 하이픈·마침표 빠져서 모든 기업 principles=0건이었음
- `\(세부원칙 X-Y\)` 명시 매칭 + DOTALL로 설명 캡처
- 스키마 변경: `principle_snippet` → `principle_number` + `principle_description`
- 검증: 현대차 7건, 삼성 7건, KT&G/SK하이닉스/NAVER 각 6건 (원문 세부원칙 수와 일치)

### 문서
- README / README_ENG: Action Tool 설명에 "거버넌스 준수율 자동 포함" 표기

### 후속 fix (timeout)
- 웹 커넥터에서 prepare_vote_brief 호출 시 일부 회사 실패 (응답 20s+ 누적 → MCP timeout 도달 추정)
- `_safe_governance()` 헬퍼 추가: corp_gov_report fetch에 `asyncio.wait_for(timeout=10)` 래핑 + 실패 시 빈 payload 반환
- 거버넌스 fetch 실패해도 vote brief 자체는 항상 생성됨

## [2026-04-22] fix | corp_gov_report 파서 보강 + timeline scope + 의무화 연도 정정
- **의무화 연도 정정** (사용자 지적 반영, WebSearch 소스 재확인):
  - 잘못된 기재: "2024 사업연도부터 전체 KOSPI 의무"
  - 정정: "2019 자산2조 → 2022 자산1조 → 2024 자산5천억 → **2026년 제출분부터 KOSPI 전체**"
  - 제출 시한 5월말, 연중 [기재정정] 재제출 빈번
  - wiki/disclosures/기업지배구조보고서.md, wiki/analysis/corp_gov_report-design.md, tool docstring, README 모두 정정
- **파서 보강**:
  - v1 문제: 4줄 고정 패턴 가정 → 비고 없는 서식(삼성) 실패
  - v2 해결: 15 표준 지표 라벨 prefix(25자)로 위치 찾고 각 블록에서 O/X 동적 수집
  - 삼성전자 7/15 → **15/15**, SK하이닉스 8/15 → **15/15**
  - 키워드 `"기업지배구조보고서"` → `"기업지배구조보고서공시"` 엄격화
  - `"연차보고서"` 명시 제외 → KB금융 같은 금융지주 별도 서식 skip
- **timeline scope 신규**:
  - 최근 5년 filings 각 원문 파싱 → 연도별 준수율 + 15지표 O/X 수집
  - `transitions` 필드: 지표별 improved / regressed / changed 자동 감지
  - 렌더러에 ✅ 개선 / ❌ 후퇴 / — 변동 카테고리 표시
- **audit 해석 정정**:
  - shareholder_meeting.summary 필드체커 0/15: tool 코드는 정상, 실제 data는 `meeting_info`/`selected_meeting`/`agenda_summary` 등에 저장. audit script만 수정 필요
  - dilutive 1 exception 재현 시도: 에러 0건 → 일시적 이상치로 판정
- README: 의무화 연도 "2026년부터 KOSPI 전체" 반영, timeline scope 예시 추가

## [2026-04-22] feat | 4-phase 릴리스: usage 표준화 / 확장 audit / corp_gov_report / 원문파싱 보강
### Phase 1: data.usage 표준화 (7 → 모든 data tool)
- `dart/client.py`: `_request_counter` 추가, 매 `_request()`에서 +1
- `services/contracts.py`: `build_usage(api_calls)` 공통 헬퍼 추가
- 7개 service(`company`, `shareholder_meeting`, `ownership_structure`, `dividend_v2`, `treasury_share`, `proxy_contest`, `value_up_v2`) 각 payload에 `data.usage` 주입 — ERROR/AMBIGUOUS/성공 경로 모두 포함
- 검증: 7 tool 모두 `{dart_api_calls, mcp_tool_calls, dart_daily_limit_per_minute}` 노출 확인

### Phase 2+3: 확장 audit (scope × 필드 채움률)
- 15 회사 × 14 tool.scope = 210 호출 병렬
- 매트릭스: status 분포 + 필드 채움률 + avg_s + avg_api
- 에러 3건 (0.8%): shareholder_meeting 2건, dilutive 1건 (이상치)
- 필드 채움률: 86% 수준. shareholder_meeting.summary 0/15은 audit checker 버그 (tool 정상)
- 결과: `wiki/analysis/parsing-audit-2026-04-22.md` 저장

### Phase 4: corp_gov_report tool 신규 (15 → 16 tool)
- **의무 범위 정정**: 2024 사업연도부터 KOSPI 전체 의무 / KOSDAQ 자율공시
- `services/corp_gov_report.py`: list.json + 키워드 "기업지배구조보고서" → 최신 원문 다운로드 → BeautifulSoup 파싱
- 4 scope: `summary` / `metrics` / `principles` / `filings`
- 파싱 필드:
  - **기업개요** (표 1-0-0): 최대주주, 지분율, 소액주주, 업종, 기업집단, 요약재무
  - **준수율** (%)
  - **15 핵심지표**: 지표명 + 당기 O/X + 직전기 O/X + 비고
  - **세부원칙 응답** 최대 30건
- 전수조사 10개 회사: 7개 완벽 파싱 (15/15), 3개 서식 차이로 7-8지표만 (파서 보강 필요)
- KT&G 100% 준수율, 에이피알 66.7%, NAVER 86.7% 등 정확 추출
- wiki 신규: `disclosures/기업지배구조보고서.md`, `analysis/corp_gov_report-design.md`

### 문서
- README / README_ENG: 15 → 16 tool, 거버넌스 카테고리 추가, 사용 예시 2종 추가
- wiki/entities/OpenProxy-MCP.md: 15 → 16 tool, screen_events 14 → 22 event_type
- disclosure 페이지 총 26 → 27개

## [2026-04-21] feat | screen_events 22 event_type 확장 + rpt 원문 파싱 + audit 매트릭스
### Phase 1: screen_events event_type 14 → 22
- 희석성 증권 4종 (rights_offering / convertible_bond / warrant_bond / capital_reduction)
- 내부거래 4종 (equity_deal_acquire / equity_deal_dispose / supply_contract_conclude / supply_contract_terminate)
- 전수조사 8/8 exact 통과 (market=all, 최근 30일)

### Phase 2: related_party_transaction 원문 파싱 보강
- 새 tool 파라미터: `include_details`, `details_limit`
- 타법인주식 거래: 거래 상대방/관계/금액/자기자본대비/자산대비/취득후 지분/방법/목적/풋옵션/최대주주관계 추출
- 단일공급계약: 계약 종류/명/금액/최근매출/매출대비비율/상대방/관계/기간 추출
- `_extract_relationship()` 헬퍼: 정해진 관계 값 후보만 허용 (자회사/계열회사/관계회사 등)
- 실측: POSCO홀딩스/성호전자/현대건설/삼성전자 80-90% 정확도

### Phase 3: 파싱 audit 매트릭스
- 20 회사(대형5+분쟁5+지주3+M&A 3+중소4) × 10 data tool 병렬 호출
- 결과: 에러 0건 / company·shareholder·dividend·proxy_contest 100% exact / ownership·treasury·rpt 85-90% exact
- partial 많은 tool (corp_restructuring, dilutive_issuance, value_up)은 "사건 없음" 케이스로 정상 해석
- 평균 응답시간: 1.2s (가벼운 tool) ~ 6.4s (dividend 등 무거운 tool)
- wiki/analysis/parsing-audit-2026-04-21.md 저장

## [2026-04-21] feat | dilutive_issuance + related_party_transaction data tool 2종 추가 (13→15 tool)
- **dilutive_issuance** (희석성 증권 발행 4종 통합):
  - `dart/client.py`: 4개 메서드 (piicDecsn / cvbdIsDecsn / bdwtIsDecsn / crDecsn)
  - `services/dilutive_issuance.py`: 4개 API 병렬, scope별 정규화, 희석률 근사 계산
  - `tools_v2/dilutive_issuance.py`: 5 scope (summary/rights_offering/convertible_bond/warrant_bond/capital_reduction), headline_metric 기반 timeline
  - 전수조사: EDGC(7건)/하이퍼코퍼레이션(CB 4건)/나무기술(BW 2건)/감자(EDGC 83.33%) 5/5 통과
- **related_party_transaction** (내부거래 모니터링):
  - `services/related_party_transaction.py`: DART 전용 API 없어 list.json + 키워드 방식. filing_search.search_filings_by_report_name 재사용
  - scope: summary / equity_deal / supply_contract
  - 플래그: subsidiary_report(자회사주요경영사항), autonomous_disclosure(자율공시), is_correction([기재정정])
  - 전수조사: POSCO홀딩스(3건 모두 자회사)/삼성전자(2건 공급계약)/현대건설(72건 supply)/성호전자(9건 equity_deal acquire) 5/5 통과
- wiki 신규 disclosure 페이지 6종: 유상증자결정/전환사채발행결정/신주인수권부사채발행결정/감자결정/타법인주식및출자증권거래/단일판매공급계약체결
- wiki analysis 2종: dilutive_issuance-design.md, related_party_transaction-design.md
- README / README_ENG / entities/OpenProxy-MCP / index.md / log.md 업데이트 (13→15 tool)
- disclosure 페이지 총 20 → 26개

## [2026-04-21] docs | disclosure 페이지 일관성 정비 + 누락 7종 신규 작성
- 신버전 3개 페이지를 구버전 양식(트리 + API필드대응 + OPM활용)으로 보강
  - 회사합병결정.md, 회사분할결정.md, 주식교환·이전결정.md
- 신규 disclosure 페이지 7종:
  - 회사분할합병결정.md (cmpDvmgDecsn — 표본 적어 합병/분할 superset 표기)
  - 자기주식결정.md (취득/처분/소각/신탁 5종 통합)
  - 기업가치제고계획.md (DART+KIND 자율공시, 3단계 분류)
  - 최대주주변경.md (양수도·담보·합병·단순변경 4형태)
  - 임원·주요주주특정증권등소유상황보고서.md (elestock DS004)
  - 소송등의제기.md (4종 + 거버넌스 시그널 매트릭스)
  - 경영권분쟁소송.md (분쟁 단계별 사건 유형 + 후속 신호)
- index.md disclosures 섹션에 9개 신규 항목 추가 (총 22 → 23개)
- 표준 양식: frontmatter / 개요 / 소스 / 전체 문서 구조 (트리) / API 필드 vs 원문 대응 / OPM에서의 활용 / 거버넌스 분석 포인트 / 관련 / 샘플 rcept_no

## [2026-04-21] feat | corporate_restructuring data tool 추가 (합병/분할/분할합병/주식교환·이전 4종 통합)
- `dart/client.py`: DART 주요사항보고서(DS005) 4개 메서드 신규
  - `get_merger_decision()` → cmpMgDecsn.json
  - `get_division_decision()` → cmpDvDecsn.json
  - `get_division_merger_decision()` → cmpDvmgDecsn.json
  - `get_stock_exchange_decision()` → stkExtrDecsn.json
- `services/corporate_restructuring.py` 신규:
  - 4개 API 병렬 호출 (asyncio.gather), summary scope에서 통합 timeline
  - 정규화: 합병비율, 상대방 재무, 외부평가, 매수청구권, 일정 등 핵심 필드 추출
  - 기본 lookback 24개월 (M&A는 빈도 낮음)
- `tools_v2/corporate_restructuring.py` 신규: 4 scope 렌더러 (summary/merger/split/share_exchange) + 사용량 표시
- 전수조사: 7개 회사 케이스 (온코크로스/일동제약/감성코퍼레이션/이마트/신세계푸드/두나무/삼성전자) — 5/5 통과 (비상장+사건없음 케이스 제외)
- wiki 추가: `disclosures/회사합병결정.md`, `disclosures/회사분할결정.md`, `disclosures/주식교환·이전결정.md`, `analysis/corporate_restructuring-design.md`
- `wiki/entities/OpenProxy-MCP.md`: 12 → 13 tool 반영
- README/README_ENG: 12 → 13 tool, 사용 예시 2개 추가

## [2026-04-21] fix | 공시 viewer_url을 DART로 통일 (KIND URL 404 해결)
- 문제: KIND 원문 URL(`kind.krx.co.kr/common/disclsviewer.do?acptno=...`)이 직접 접근 시 404
- `services/contracts.py::_build_viewer_url()`: source_type=KIND_HTML도 DART 뷰어 URL 반환
  - DART 뷰어(`dart.fss.or.kr/dsaf001/main.do?rcpNo=`)는 80 포맷(거래소 수시공시) rcept_no도 정상 렌더링
- `tools_v2/evidence.py`: docstring에서 KIND URL 언급 제거
- `wiki/analysis/evidence-tool-검증-예시.md`: viewer_url 매핑 설명 + 샘플 테이블 수정
- 영향: ownership_structure(changes), shareholder_meeting(results), value_up 등 KIND-HTML 사용 tool의 evidence viewer_url이 DART로 자동 전환
- 내부 KIND 크롤링(`kind_fetch_document()`, 3단계 iframe)은 그대로 유지 — 사용자 노출 URL만 변경

## [2026-04-19] feat | screen_events UX 보강 (사용량 노출, 원문 링크, market 축소)
- `services/screen_events.py`:
  - market 3종(`kospi`/`kosdaq`/`all=KOSPI+KOSDAQ`)으로 축소 — KONEX/기타 제거
  - `_search_market_wide()`: `corp_clses` 튜플 지원 (all은 Y→K 순차 호출로 구현)
  - api_calls/truncated/pages_cut_off stats 반환
  - 결과가 max_results 도달하면 별도 truncation warning 추가
- `tools_v2/screen_events.py`:
  - 렌더러에 `## 사용량` 블록 추가 (DART API 호출 수, MCP tool 호출 수, 분당 한도)
  - 결과 테이블 `rcept_no` → 클릭 가능한 원문 링크로 변경
  - docstring market 옵션 업데이트
- `wiki/analysis/screen_events-design.md`: 사용량 추적/market 설계 섹션 보강

## [2026-04-19] feat | screen_events discovery tool 추가 (14 event_type, market-wide 역조회)
- `dart/client.py::search_filings()`: `corp_cls` 파라미터 추가 (Y/K/N/E 시장 필터)
- `services/screen_events.py` 신규:
  - 14 event_type 카탈로그 + (pblntf_tys, keywords, strip_spaces) 매핑
  - `_search_market_wide()`: corp_code 없이 시장 전체 대상 페이지 순회 (max 20페이지/ty)
  - `build_screen_events_payload()`: market→corp_cls 변환, rcept_dt 내림차순, max_results=1-100
- `tools_v2/screen_events.py` 신규: MCP 인터페이스, compact 테이블 렌더러
- 전수조사 (최근 30일, market=all): **14/14 통과**
  - 초기 설계에서 `annual_meeting`/`extraordinary_meeting` 분리 시도 → DART report_nm이 "주주총회소집공고" 단일 포맷이라 구분 불가능 → `shareholder_meeting_notice` 단일로 통합 (15→14)
  - `treasury_retire` 키워드 오류 발견 → 실제 제목은 "주식소각결정" + pblntf_ty=I로 수정
- `wiki/analysis/screen_events-design.md` 신규, `wiki/entities/OpenProxy-MCP.md` (11→12 tool) 업데이트

## [2026-04-19] feat | ownership_structure scope=changes 추가 (최대주주등소유주식변동신고서)
- `_parse_change_filing()`: KIND HTML 5개 테이블 파싱 (보고개요 직전/금번, 개인별변동, 총괄현황)
- `_fetch_change_filings()`: DART pblntf_ty=I 검색 → rcept_no 80→00 변환 → kind_fetch_document() 최대 5건
- `build_ownership_structure_payload()`: scope=changes 처리 + KIND_HTML evidence_refs
- `tools_v2/ownership_structure.py`: changes scope 렌더러, docstring 업데이트
- `wiki/disclosures/최대주주등소유주식변동신고서.md` 신규, `wiki/index.md` disclosures 항목 추가

## [2026-04-18] feat | shareholder_meeting v2 2차 구현 (board, compensation, results, 시점 구분)
- `services/shareholder_meeting.py` 확장:
  - `scope=board|compensation|results` 추가
  - `meeting_phase` 추가: `pre_meeting | post_meeting_pre_result | post_result | undetermined`
  - `result_status` 추가: `not_due_yet | pending_or_missing | available | requires_review | unknown`
  - 결과 공시는 DART `주주총회결과` 검색 후 `80 -> 00` 변환이 가능한 whitelist 건만 KIND HTML로 연결
- `meeting_type=auto` 기본화:
  - `annual` 최신 회차와 `extraordinary` 최신 회차를 후보로 생성
  - 일반 조회는 정기/임시를 가리지 않고 가장 현재적인 회차 우선
  - 결과 조회는 결과공시가 확인된 회차 중 최신 회차 우선
  - `selection_basis`, `selected_meeting`, `alternative_meetings` 추가
- 최근 12개월 커버리지 추가:
  - 주총 관련 제목군(`주주총회소집공고`)만 대상으로 조사
  - `annual_only | extraordinary_only | annual_and_extraordinary | none` 플래그 추가
  - 선택된 회차 기준 최근 12개월 구간과 정기/임시 최신 회차 메타데이터 제공
  - `auto` 후보도 최근 12개월 기준 `가장 최근 정기 1개 + 가장 최근 임시 1개`로 변경
  - 교차연도 회차는 각 회차의 실제 회의연도로 결과공시 검색해 매핑
- `tools_v2/shareholder_meeting.py` 확장:
  - `summary`에 결과 시점 블록 추가
  - `summary`에 회차 선택 근거와 대안 회차 블록 추가
  - `summary`에 최근 12개월 커버리지 블록 추가
  - `board`, `compensation`, `results` 출력면 추가
  - 회의 전과 결과공시 후를 구분해 표시
- 실조회:
  - `KT&G`, `auto`, `2026`, `summary` → `meeting_type=annual`, `meeting_phase=post_result`, `result_status=available`
  - `KT&G`, `annual`, `2026`, `board` → 후보 3명 확인
  - `KT&G`, `annual`, `2026`, `compensation` → 당기 한도 `6,000백만원`, 전기 지급 `2,445백만원`
  - `KT&G`, `auto`, `2026`, `results` → `rcept_no=20260326802654`, KIND `20260326002654`, 의결 결과 파싱 성공
  - `한화`, `auto`, `2025`, `summary` → `meeting_type=extraordinary`, 대안으로 `annual` 표시
  - `아시아나항공`, `auto`, `2025`, `summary` → `meeting_type=annual`, 대안으로 `extraordinary` 표시
  - `KT&G`, `auto`, `2026`, `summary` → coverage `annual_only`
  - `한화`, `auto`, `2025`, `summary` → coverage `annual_and_extraordinary`
- sanity check:
  - `python -m compileall open_proxy_mcp` 통과

## [2026-04-18] feat | ownership_structure control_map 고도화
- `services/ownership_structure.py` 확장:
  - `control_map`를 단순 raw dump에서 해석 가능한 블록 구조로 재편
  - `core_holder_block`, `treasury_block`, `overlap_blocks`, `non_overlap_blocks`, `active_non_overlap_blocks`, `flags`, `observations`, `notes`
  - 최대주주 명부와 5% 블록의 이름 겹침 여부를 `registry_overlap`으로 표시
  - 관찰 포인트는 의미 있는 5% 이상 능동 블록만 기준으로 생성
- `tools_v2/ownership_structure.py` 확장:
  - `control_map` 전용 출력면 추가
  - 명부상 특수관계인 합계, 자사주, 겹치지 않는 능동 5% 블록, 겹치는 5% 블록을 분리 표시
- 실조회:
  - `삼성전자`, `control_map` → `삼성물산` 블록은 명부와 겹치는 능동 블록으로 표시
  - `고려아연`, `control_map` → `한국기업투자홀딩스`, `최윤범`, `크루시블제이브이`를 겹치지 않는 능동 블록으로 표시
  - `한화`, `control_map` → 명부상 특수관계인 50% 이상 + 자사주 5% 이상 플래그 확인
- sanity check:
  - `python -m compileall open_proxy_mcp` 통과

## [2026-04-18] feat | proxy_contest를 control_map과 최근 12개월 기준으로 재정렬
- `services/proxy_contest.py` 확장:
  - 최근 12개월 조사구간(`window_start`, `window_end`)을 공시 검색 기본 단위로 추가
  - 위임장/공개매수, 소송/분쟁은 제목군만 타깃해서 최근 12개월 안에서 조회
  - `ownership_structure`의 `control_map`을 가져와서 분쟁 문서와 5% 블록을 같은 판에서 해석
  - `players` 추가:
    - `company_side_filers`
    - `shareholder_side_filers`
    - `active_external_blocks`
    - `active_overlap_blocks`
  - `fight`에 `actor_group` 추가:
    - `company`
    - `external_active_block`
    - `registry_overlap`
    - `shareholder`
  - `signals`에 `actor_side` 추가:
    - `external_active_block`
    - `registry_overlap`
    - `external_or_passive`
  - `timeline`에도 `actor`, `side`를 넣어 누가 어떤 문서를 냈는지 바로 읽히게 변경
  - 5% 시그널은 최근 12개월 밖 공시가 섞이지 않도록 window 필터 적용
- `tools_v2/proxy_contest.py` 확장:
  - `summary`에 조사구간, 최대주주/특수관계인 합계/자사주 비중 추가
  - `판 구조` 블록 추가: 회사측 제출인, 주주측 제출인, 외부 능동 블록, 명부 겹침 블록
  - `fight`, `signals`, `timeline` 표에 플레이어 분류 열 추가
- 실조회:
  - `고려아연`, `summary`, `2026`
    - 회사측 제출인 `고려아연`
    - 주주측 제출인 `영풍`
    - 명부와 안 겹치는 능동 5% 블록 `최윤범`, `크루시블제이브이`, `한국기업투자홀딩스`
    - 명부와 겹치는 능동 블록 `영풍`
  - `한화`, `summary`, `2026`
    - 회사측 제출인 `한화`
    - 명부상 특수관계인 합계 `55.84%`
    - 자사주 `7.45%`
    - 최근 12개월 시그널은 `김승연` 1건으로 정리
- sanity check:
  - `python -m compileall open_proxy_mcp` 통과

## [2026-04-18] feat | v2 public tool 날짜 파라미터 표준화
- `services/date_utils.py` 신규:
  - `start_date`, `end_date` 파싱
  - 기본 조회구간 계산
  - 날짜 역전 시 자동 보정
- `company`
  - `start_date`, `end_date` 추가
  - 최근 공시 인덱스를 지정 구간으로 조회
- `shareholder_meeting`
  - `start_date`, `end_date`, `lookback_months` 추가
  - 지정 구간 또는 롤링 구간에서 정기/임시 최신 회차를 고름
  - 응답에 `requested_window` 추가
- `ownership_structure`
  - `as_of_date`, `start_date`, `end_date` 추가
  - 스냅샷 기준 연도는 `as_of_date`의 직전 사업연도로 연결
  - 5% 블록/타임라인은 지정 구간으로 필터
- `dividend`
  - `start_date`, `end_date` 추가
  - 배당결정 공시와 history 구간을 날짜 기준으로 제한
- `proxy_contest`
  - `start_date`, `end_date`, `lookback_months` 추가
  - 분쟁 공시/시그널 window를 명시적으로 제어
- `value_up`
  - `start_date`, `end_date` 추가
  - 밸류업 공시 검색 구간을 날짜 기준으로 제어
- `evidence`
  - `start_date`, `end_date`를 받도록 시그니처 통일
  - 현재는 `rcept_no` 직접 조회가 우선이라 window는 메타데이터로만 저장
- 실조회:
  - `company('삼성전자', 2026-03-01~2026-04-18)` → recent filings window 반영
  - `shareholder_meeting('한화', 2025-12-01~2026-04-18)` → `annual` 선택, `annual_and_extraordinary`
  - `ownership_structure('한화', as_of=2026-04-18, 2026-01-01~2026-04-18)` → `exact`, timeline 4건
  - `dividend('삼성전자', 2024-01-01~2025-12-31)` → `exact`, 최근 결정 5건
  - `proxy_contest('고려아연', 2025-12-01~2026-04-18)` → `exact`, 능동 시그널 4건
  - `value_up('KB금융', 2025-01-01~2026-04-18)` → `exact`, timeline 3건
  - `evidence(rcept_no=20260225005779, keyword='제39기', 2026-01-01~2026-12-31)` → `exact`
- sanity check:
  - `python -m compileall open_proxy_mcp` 통과

## [2026-04-18] feat | prepare_vote_brief 1차 구현
- `services/vote_brief.py` 신규:
  - `shareholder_meeting`의 `summary/agenda/board/compensation/results`와
    `ownership_structure control_map`을 묶어 한 장 메모 payload 생성
  - 추천 찬반을 단정하지 않고, 회차/판 구조/안건/후보자/보수/결과/체크포인트 중심으로 정리
  - `meeting_date`를 `ownership as_of_date`로 넘겨 같은 회차 기준 스냅샷을 맞춤
  - evidence는 하위 data tool의 evidence를 합쳐 dedupe
- `tools_v2/prepare_vote_brief.py` 신규:
  - `prepare_vote_brief` public action tool 추가
  - 기본 파라미터: `company`, `meeting_type`, `year`, `start_date`, `end_date`, `lookback_months`
  - markdown 출력은 `회차`, `판 구조`, `안건`, `후보자`, `보수`, `결과`, `체크 포인트`, `근거` 순으로 정리
- 실조회:
  - `KT&G`, `2026`
    - `annual`, `post_result`, agenda 15건, 후보 3명, 보수안건 1건
    - 결과 안건 14건 모두 가결
    - 체크 포인트: 자사주 5% 이상
  - `한화`, `2025-12-01 ~ 2026-04-18`
    - `annual`, `post_result`, agenda 17건, 후보 5명, 보수안건 1건
    - 반대율 10% 이상 안건: 정관 일부 변경(이사 임기 변경), 이사 보수 한도 승인
    - 체크 포인트: 정정공고 반영, 특수관계인 50%+, 자사주 5%+, 명부 겹침 능동 블록
- sanity check:
  - `python -m compileall open_proxy_mcp` 통과
  - `build_mcp('v2')`에서 `prepare_vote_brief` 등록 확인

## [2026-04-18] feat | prepare_engagement_case + build_campaign_brief 추가
- `services/engagement_case.py`, `tools_v2/prepare_engagement_case.py` 신규:
  - `ownership_structure(control_map)`, `proxy_contest(summary)`, `value_up(summary)`를 합쳐 engagement memo 생성
  - 출력은 `쟁점 프레이밍`, `지배구조 맥락`, `분쟁 신호`, `밸류업/주주환원 맥락`, `체크 포인트`, `근거`
  - 자동 추천이나 처방은 넣지 않고 fact-first 구조 유지
- `services/campaign_brief.py`, `tools_v2/build_campaign_brief.py` 신규:
  - `shareholder_meeting(summary/agenda/board)`, `ownership_structure(control_map)`, `proxy_contest(timeline)`를 합쳐 campaign fact brief 생성
  - 출력은 `회의 맥락`, `플레이어`, `지배구조`, `분쟁 개요`, `타임라인`, `핵심 플래그`, `근거`
  - `brief_note`로 vote math/추천 부재를 명시
- 실조회:
  - `prepare_engagement_case('KT&G', 2025-12-01~2026-04-18)` → `exact`, `cmp_033780`
    - 최대주주 `중소기업은행 8.06%`
    - 자사주 `12.03%`
    - engagement용 쟁점 프레이밍/분쟁 신호/밸류업 맥락 정상 생성
  - `build_campaign_brief('KT&G', 2026)` → `exact`, `cmp_033780`
    - `meeting_type=annual`
    - timeline 3건
    - 플레이어/지배구조/회의 맥락 정상 생성
- sanity check:
  - `python -m compileall open_proxy_mcp/services/engagement_case.py open_proxy_mcp/tools_v2/prepare_engagement_case.py open_proxy_mcp/services/campaign_brief.py open_proxy_mcp/tools_v2/build_campaign_brief.py` 통과

## [2026-04-18] feat | release_v2 scaffold + company facade 첫 구현
- `open_proxy_mcp/tools_v2/` 신규: v2 public facade layer 시작
- `open_proxy_mcp/services/` 신규: v2 공통 service layer 시작
- `services/contracts.py` 신규: `AnalysisStatus`, `SourceType`, `ToolEnvelope`, `EvidenceRef` 정의
- `server.py` 업데이트: `build_mcp(toolset)` 추가, `v1|v2|hybrid` 선택 지원
- `__main__.py` 업데이트: `main()` 직접 호출 구조로 단순화
- `tools_v2/company.py`, `services/company.py` 신규: `company` data tool 초안 구현
- 정책 반영: partial match 자동선택 금지, exact가 아니면 `ambiguous`
- `company` 현재 범위: 회사 식별 + 기본 카드 + 최근 공시 인덱스
- sanity check:
  - `python -m compileall open_proxy_mcp` 통과
  - `build_mcp('v2')` 성공
  - `build_company_payload('삼성전자')` → `exact`, `cmp_005930`

## [2026-04-18] feat | shareholder_meeting v2 1차 구현 (summary, agenda)
- `services/shareholder_meeting.py`, `tools_v2/shareholder_meeting.py` 신규
- 정기/임시 주총을 하나의 public tool에서 `meeting_type=annual|extraordinary`로 처리
- 현재 scope는 `summary`, `agenda`만 지원
- 동작 원칙:
  - 회사 식별 exact가 아니면 자동선택 금지
  - 소스는 `DART list.json + DART XML`
  - PDF fallback 없음
  - 안건 파싱 신뢰도 낮으면 `requires_review`
- 반환 범위:
  - notice 메타데이터
  - meeting_info
  - agenda_summary
  - agendas(scope=agenda)
  - correction_summary
  - DART XML evidence ref
- 실조회:
  - `KT&G` → alias로 `케이티앤지` 식별
  - `2026 annual summary` → `exact`, `cmp_033780`, `rcept_no=20260225005779`, `agenda_total_count=15`
  - `2026 annual agenda` → root 8건, 첫 안건 `제1호 제39기 재무제표 및 이익잉여금처분계산서 승인의 건`

## [2026-04-18] feat | remaining v2 data tools 구현 (ownership_structure, dividend, value_up, proxy_contest, evidence)
- 신규 service:
  - `ownership_structure.py`
  - `dividend_v2.py`
  - `value_up_v2.py`
  - `proxy_contest.py`
  - `evidence.py`
- 신규 public facade:
  - `ownership_structure.py`
  - `dividend.py`
  - `value_up.py`
  - `proxy_contest.py`
  - `evidence.py`
- 지원 범위
  - `ownership_structure`: `summary`, `major_holders`, `blocks`, `treasury`, `control_map`, `timeline`
  - `dividend`: `summary`, `detail`, `history`, `policy_signals`
  - `value_up`: `summary`, `plan`, `commitments`, `timeline`
  - `proxy_contest`: `summary`, `fight`, `litigation`, `signals`, `timeline`
  - `evidence`: `evidence_id` 또는 `rcept_no` 기반 원문 발췌
- 정책 반영
  - partial match 자동선택 금지 유지
  - PDF fallback 없음
  - `proxy_contest.vote_math`는 아직 비공개 (`requires_review`)
- sanity check
  - `python -m compileall open_proxy_mcp` 통과
  - `build_mcp('v2')` 성공
- 샘플 실조회
  - `ownership_structure('삼성전자', summary, 2025)` → `exact`, `cmp_005930`, 자사주 `1.55%`
  - `dividend('삼성전자', summary, 2025)` → `exact`, `cmp_005930`, 연간 DPS `1668원`
  - `value_up('KB금융', summary, 2026)` → `exact`, `cmp_105560`, 최신 `rcept_no=20260327802428`
  - `proxy_contest('고려아연', summary, 2026)` → `exact`, `cmp_010130`, fight `7`, shareholder-side `4`, litigation `40`, active signals `4`
  - `evidence(rcept_no='20260225005779')` → `exact`

## [2026-04-18] docs | 신규 tool 추가 검증 정책 + release_v2 소스 검증 기준 정리
- `decisions/tool-추가-검증-정책.md` 신규: data/action tool 분류, 공시 매핑표, 화이트리스트 체크, 샘플 검증, 출시 게이트 정리
- `DART-KIND-매핑-화이트리스트-2026-04`를 신규 tool 검증 정책의 기준 문서로 연결
- `index.md` 업데이트: release_v2 정책 문서 카탈로그 반영
- `templates/tool-추가-검증-템플릿.md` 신규: 제안서, data/action 검증, whitelist extension, release gate 복붙 템플릿 추가
- `WIKI_SCHEMA.md` 업데이트: templates/ 디렉토리와 `type: template` 정의 추가
- `analysis/shareholder_meeting-tool-검증-예시.md` 신규: 실제 `rcept_no` 샘플로 `shareholder_meeting` data tool 검증 예시 작성
- `analysis/release_v2-public-tool-검증-매트릭스.md` 신규: release_v2 공개 data/action tool 전체 판정 요약
- `analysis/company/ownership/dividend/proxy_contest/value_up/evidence` 검증 예시 추가
- `analysis/release_v2-action-tool-검증-초안.md` 신규: action tool 3종을 phase-2 검증 대상으로 정리
- `analysis/release_v2-tool-아키텍처.md` 신규: `company -> data tools -> evidence -> action tools` 구조를 도식화
- `contestation` 명칭을 `proxy_contest`로 통일

## [2026-04-12] refactor | tool 체이닝 + governance_report + tier 체계 완성 (33개)
- agm_pre_analysis + own_full_analysis → tier-5 asyncio.gather 병렬 체이닝
- prx_fight → prx_search + prx_direction 체이닝 (중복 제거)
- governance_report: AGM + OWN + DIV 3도메인 통합 (33번째 tool)
- div_full_analysis format="json" 추가 → 전 tool json 지원 완성
- tier 태그 32/32 완성, tool_guide tier-2, news_check tier-5
- pblntf_ty 필터링 전면 적용 (D/E/I), _DIV_KEYWORDS 상수화
- wiki 정리: archive/ 9개, decisions/pblntf-ty-필터링.md, disclosures/배당공시유형.md

## [2026-04-11] docs | wiki 구조 재편 + disclosures 트리 + comparison 카테고리 신설
- analysis/ → decisions/(기술결정) + analysis/(외부소스+주총분석) 분리
- comparison/ 신규: 공시 간/내 컨셉 비교 카테고리
- stkrt-vs-ctr_stkrt.md: DART 대량보유 필드 오해 정정 (ctr_stkrt = 주요계약체결, 보고자 직접보유 아님)
- disclosures/ 10개 페이지 전체 문서 구조 트리 추가
- graphify로 wiki knowledge graph 탐색 (202 nodes, 360 edges)

## [2026-04-10] fix | own_full_analysis 테이블 포맷 + 대량보유 비교 기준 정리
- 헤더 카드: 최대주주/특관합계/자사주
- ctr_stkrt(본인) vs stkrt(합산) 구분, 비고에 합산 명시
- docstring rule에 테이블 출력 형식 지시

## [2026-04-10] refactor | Dispatch Table + Chain Tool + README 재작성
- Dispatch Table: 16 PDF/OCR → agm_parse_fallback 1개 (48→32 tools)
- Chain Tool: own_full_analysis (지분+배당+자사주+주주환원)
- README.md 한국어 전면 재작성 + README_ENG.md 영어 신규
- OpenProxy-MCP entity 업데이트 (33 tools, 아키텍처 패턴)

## [2026-04-09] ingest | news_check tool + decision tree
- news_check: 네이버 뉴스 API 기반 후보자 부정 뉴스 검색 tool
- Proxy Voting Decision Tree: AGM_TOOL_RULE에 6개 안건 판정 기준
- 네이버-금융 entity: 뉴스 검색 API 섹션 추가

## [2026-04-05] lint | 누락 개념 4개 + broken ref 수정 + sources 필드 추가
- concepts/ 4개 신규: 자본준비금, 당기순이익, 주주환원, 경영권-방어
- DART-OpenAPI.md: related에서 alotMatter 제거, 배당성향/div-tool-rule로 교체
- analysis/ 4개: sources 필드 추가 (cross-domain-체이닝, proxy-voting-decision-tree, 상법개정-타임라인-2026, 주총방어-시나리오-4가지)
- index.md 업데이트

## [2026-04-05] ingest | 외부 소스 3건 (JPM voting, 주총방어전략, 주총체크리스트)
- raw/ 3건: J.P Morgan Asset Management Voting Process.md, 주총방어전략.pdf, 주주총회 체크리스트.pdf
- sources/ 3개 신규: jpm-voting-process, 주총방어전략-2026, 주총체크리스트-2026
- analysis/ 3개 신규: 주총방어-시나리오-4가지, 상법개정-타임라인-2026, proxy-voting-decision-tree
- concepts/ 2개 업데이트: 프록시-파이트 (방어전술/글로벌 프로세스 추가), 위임장-권유 (글로벌 기관 구조 추가)
- index.md, log.md 업데이트

## [2026-04-09] ingest | docstring 전면 업그레이드 + cross-domain 체이닝
- 46/46 tool desc/when/rule/ref 포맷 적용 (100%)
- cross-domain ref 7개 추가 (AGM↔OWN↔DIV)
- cross-domain-체이닝.md 신규: 도메인 간 tool 연결 맵 + 시나리오 3개
- index.md 업데이트

## [2026-04-18] feat | v2 proxy_contest vote_math 추가
- `proxy_contest(scope="vote_math")` 공개
- 기준:
  - `shareholder_meeting(results)`의 KIND 결과표 사용
  - 안건별 추정참석률 = 발행기준 찬성률 / 행사기준 찬성률
  - 보통결의 안건 최빈값을 대표 추정참석률로 사용
  - 감사위원/집중투표/비교 불가 안건은 제외
- 출력:
  - 대표 추정참석률
  - 특수관계인/자사주/능동 5% 블록
  - 특수관계인 제외 추정 참석분
  - 고반대율/부결 안건
  - signal_level (`stable` / `watch` / `contestable`)
- 원칙:
  - 승패 예측 아님
  - 결과공시 없거나 비교 가능한 보통결의 안건이 없으면 `requires_review`

## [2026-04-18] feat | 요약형 KIND 주총결과 파서 추가
- `shareholder_meeting(results)`가 세부표형뿐 아니라 요약형 결과공시도 읽도록 확장
- 지원 패턴:
  - `○ 제1호 의안 : ... → 원안가결`
  - `제2-1호 의안 : ...` 다음 줄 `→ 부결`
  - `1) 제1호 의안: ... → 원안대로 승인`
- 출력에 `result_format=table|summary`, `numerical_vote_table_available` 추가
- 요약형이면:
  - 안건별 가결/부결은 제공
  - 찬성률/반대율/추정참석률은 비제공
  - `vote_math`는 계속 `requires_review`

## [2026-04-18] feat | prepare_vote_brief에 결과 품질과 vote_math 연결
- `prepare_vote_brief`가 이제 결과공시 품질을 같이 보여줌
- 결과가 세부표형이면:
  - `result_format=table`
  - 수치표 제공 여부 표시
  - `vote_math` 요약(대표 추정참석률, signal_level, 특수관계인 제외 추정 참석분) 포함
- 결과가 요약형이면:
  - `result_format=summary`
  - 안건별 가결/부결만 사용
  - `vote_math`는 비활성
- 목적:
  - “결과 확인 가능”과 “숫자 분석 가능”을 분리해서 읽히게 함

## [2026-04-18] feat | prepare_vote_brief에 집중투표 사전 전략 블록 추가
- `prepare_vote_brief`에 `cumulative_voting_strategy` 추가
- 포함 내용:
  - 집중투표 대상 이사 수
  - 자사주 차감 후 전체 의결권 모수 비율
  - 100% 참석 가정 1석선
  - 이전/동일 회차 참석률 참고 1석선
  - 최대주주/특수관계인/능동 블록과의 격차
- 가드레일:
  - 공시에 집중투표가 명시되지 않고 복수 이사 선임만 있는 경우 `partial`
  - 감사위원/분리선출은 집중투표 대상 수에서 제외하는 보수적 기준 사용

## [2026-04-18] feat | shareholder_meeting에 DART viewer HTML crawl fallback 추가
- 원칙 반영:
  - `document.xml` 기반 파싱이 약하면 DART `main.do -> report/viewer.do` HTML crawl로 재시도
  - 자동 fallback은 `shareholder_meeting` notice 파싱에만 제한적으로 적용
- 구현:
  - `DartClient.get_viewer_document()` 추가
  - `shareholder_meeting`가 `meeting_type/datetime/agenda` 품질이 낮을 때 viewer HTML로 재파싱
  - `notice_parse_source=dart_xml|dart_html` 메타 추가
- 의도:
  - 공식 API/XML을 기본으로 유지
  - 구조가 깨질 때만 웹 크롤링을 2차 경로로 사용

## [2026-04-18] feat | KT&G 2024 구형 요약형 결과공시 파서 보강
- 샘플:
  - KT&G 2024 정기주총 결과 `20240328801345`
- 문제:
  - KIND 본문이 `주주총회 안건 세부내역` 표가 없는 구형 요약형
  - `- 제1호 : ...`, `☞ 제3-1호 및 제3-3호 가결, 제3-2호 부결` 패턴
- 보강:
  - `의안` 없는 구형 제목형 파싱
  - `내지`, `및`이 섞인 하위호안 outcome line 분해
  - 후보자 출처 괄호 문장을 안건 제목에 이어붙이기
- 결과:
  - `shareholder_meeting(results)`가 KT&G 2024를 `summary` 형식으로 구조화
  - `vote_math`는 여전히 `numerical unavailable`로 보수적으로 유지

## [2026-04-08] lint | 고립 노드 수정 + disclosure 카테고리 추가
- 34개 페이지에 본문 wikilink 추가 (고립 해소)
- disclosures/ 신규: 11개 공시 유형 페이지
- index.md 업데이트

## [2026-04-07] lint | 건강 점검 + 수정
- broken link 수정: v4-스키마, 소진율 페이지 생성
- cross-ref 불일치 11개 수정 (8개 페이지 related 필드 업데이트)

## [2026-04-07] init | Wiki 초기화
- 디렉토리 구조 생성 (raw/ + wiki/)
- CLAUDE.md(schema) 작성
- raw/ 시딩: rules 6개 + devlog 1개 + benchmarks 1개 + READMEs 2개

## [2026-04-05] ingest | 첫 전체 ingest (10 raw sources)
- raw/rules/ 6개: AGM_TOOL_RULE, AGM_CASE_RULE, DIV_TOOL_RULE, DIV_CASE_RULE, OWN_TOOL_RULE, OWN_CASE_RULE
- raw/rules/ 2개: OPM_README, OPA_README
- raw/devlog/DEVLOG.md
- raw/benchmarks/benchmark_personnel_results.json
- 생성: sources 10개, concepts 24개, entities 9개, analysis 8개 (총 51 페이지)
- index.md 전체 업데이트

## [2026-04-19] feat | action tool에 source quality 메타 전파
- `prepare_vote_brief`, `prepare_engagement_case`, `build_campaign_brief`에 quality 블록 추가
- 포함 항목:
  - component status
  - `notice_parse_source`
  - `result_format`, `numerical_vote_table_available`
- 목적:
  - action memo를 볼 때 결론의 기반 소스 품질을 바로 판단하게 함

## [2026-04-19] fix | v1-v2 실패 원인 구분 보강
- `value_up_v2`
  - `availability_status` 추가
  - `search_diagnostics` 추가
  - 요청 구간에 공시가 없는지, v1 호환 진단 구간(`전년도 1월 1일 ~ 대상연도 12월 31일`)에도 없는지 구분
- `dividend_v2`
  - `history_selection` 추가
  - `history` scope는 미완료 사업연도를 제외하고 최근 완료 사업연도 기준으로 3개년을 고르도록 보강
- 확인:
  - `현대자동차 value_up`: 요청 구간에도 없고 v1 호환 진단 구간에도 공시 없음
  - `삼성전자 dividend history`: 2026 미완료 사업연도 대신 2023/2024/2025 완료 3개년 반환

## [2026-04-19] fix | value_up KIND fallback + 현대자동차 pagination 보정
- `현대자동차` 사례로 기존 `공시 없음` 판정을 정정
  - DART 웹과 DART API 모두 공시가 존재
  - 예: `rcept_no=20240828800218`
  - 이전 누락 원인은 `2024~2026`처럼 구간이 길 때 `list.json` 첫 100건만 보고 pagination을 넘기지 않아 예전 공시가 밀린 것
- KIND fallback은 유지
  - DART가 진짜 비는 거래소 자율공시를 대비한 보조 경로로 유지
  - 다만 `현대자동차`는 `KIND-only`가 아니라 `DART pagination 누락` 사례로 재분류
- `dart/client.py`
  - KIND 상세검색 기반 `kind_search_disclosures()` 추가
  - `기업가치 제고 계획(0184)` 전용 `kind_search_value_up()` 추가
- `value_up_v2`
  - DART search에서 pagination 처리 추가
  - DART에서 못 찾으면 KIND `기업가치 제고 계획(0184)` 검색으로 재시도
  - 진단 구간을 `최근 3개 연도`로 확대해, 최근 12개월 밖에 있는 기존 계획도 `요청구간 밖 존재`로 구분
  - `availability_status=exists_outside_requested_window`에서 DART/KIND 샘플 공시를 함께 노출
  - `primary_source=dart|kind` 추가
  - 최신 공시 메타에 `rcept_no` 또는 `KIND acptno`, `source_type` 반영
- `company` 식별 보정
  - 동일 회사명 이력 중 현재 상장 엔티티가 명확할 때는 최신 상장 엔티티를 우선 선택
  - `기아`, `우리금융지주` 같은 케이스를 exact로 연결

## [2026-04-19] verify | value_up 10개 추가 검증
- 검증 구간: `2024-01-01 ~ 2026-04-19`
- 결과:
  - `현대자동차`: `exact`, DART, `rcept_no=20240828800218`
  - `기아`: `exact`, DART
  - `현대모비스`: `exact`, DART
  - `KB금융`: `exact`, DART
  - `하나금융지주`: `exact`, DART
  - `신한지주`: `exact`, DART
  - `우리금융지주`: `exact`, DART
  - `메리츠금융지주`: `exact`, DART
  - `삼성생명`: `exact`, DART
  - `POSCO홀딩스`: `exact`, DART
  - `삼성전자`: `exact`, DART
- 해석:
  - 현재 검증 샘플 기준 11개 전부 DART 경로로 정상 조회
  - 현대자동차는 `KIND-only`가 아니라 `DART pagination` 처리 부족이 원인이었음

## [2026-04-19] fix | 제목 타깃 검색 경고 전파 + taxonomy wiki 반영
- `filing_search` 경고 문구를 구체화
  - 단순히 `몇 페이지까지만 확인`이 아니라
  - `어느 기간`, `어떤 pblntf_ty`, `어떤 제목군`을 기준으로 확인했는지 같이 남기도록 수정
- `proxy_contest`
  - 위임장/공개매수, 소송/분쟁 검색에서 제목 타깃 helper가 내는 경고를 실제 warnings에 반영
  - 앞으로 `정해진 기간 내 일부 페이지만 확인`한 경우 analyst가 바로 알 수 있게 됨
- `shareholder_meeting`
  - notice/result 검색에서도 helper 경고를 warnings에 반영
  - `소집공고 없음`과 `검색 범위를 제한해 확인함`을 분리해서 볼 수 있게 됨
- `dart-kind-disclosure-taxonomy.md`
  - wiki source 문서 `[[dart-kind-disclosure-taxonomy]]`로 반영
  - v2 소스 정책과 공시군 분류 기준 문서로 재사용 시작

## [2026-07-22] fix | business_details 구조 경계·상태 정확도 보강
- 고정 18~24KB 구간 대신 실제 DART 헤딩 색인과 다음 동급/상위 헤딩 경계를 사용
- 목차·표 내부 라벨·교차참조 배제, span+본문 결합·비강조/공백소실 헤딩 변형 회수
- 번호 깊이 역전은 제목만 남는 경우에 한해 `SECTION-2` 경계 복구
- 기존 `status` 호환 유지 + `extraction_status`, `section_source`, 반환 markdown 기반 `hints[]` 추가
- 로컬 원문 300사(KOSPI 169/KOSDAQ 131) × 5필드 = 1,500 슬롯 전수 재측정: 예외 0, `SUCCESS` 1,201(80.1%), 명시적 N/A 포함 처리 1,368(91.2%), 39.2초
- 구형에만 잡힌 잔여 37건은 매출 제목·계약·시장 평균·재무주석 또는 경계 누출로 확인, 확인된 실제 데이터 회귀 0
- HD건설기계·SK·아모레퍼시픽홀딩스 경계 누출과 피에스케이 계열 결합 목차 오인식 차단, 전체 134 tests 통과
- 별도 로컬 FastMCP 삼성전자 backlog 실호출 성공: 최신 분기, `SUCCESS`, 380자, 약 5.5초
