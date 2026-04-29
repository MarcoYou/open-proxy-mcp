---
type: index
title: OPM Wiki Index
updated: 2026-04-27
---

# OPM Wiki Index

## Concepts

- [[3-tier-fallback]] - XML -> PDF -> OCR 3단계 파싱 전략
- [[집중투표]] - N명 선출 시 1주 N표, 소수주주 대표 선출 장치
- [[의결권]] - 주주총회 의사결정 권리, 1주 1표 원칙과 예외
- [[배당성향]] - 배당금 총액 / 지배주주 귀속 당기순이익 (연결 기준)
- [[배당수익률]] - 주가 대비 배당금 비율, DART vs 자체 계산
- [[시가배당률]] - DART 공식 배당수익률, 1주일 평균 종가 기준
- [[분기배당]] - 연 1회 외 분기별 중간배당, DPS 합산 주의
- [[특별배당]] - 일회성 배당, 추이 분석 시 정기배당과 분리
- [[프록시-파이트]] - 위임장 대결, 경영권 쟁탈 메커니즘
- [[위임장-권유]] - 의결권 위임 확보 행위, 프록시 파이트의 실행 수단
- [[지분구조]] - 최대주주/기관/자사주/소액주주 분포
- [[최대주주]] - 본인+특관인 합산 최다 보유자
- [[대주주]] - 1%+ 또는 시총 10억+ 보유자, 양도세·신고 trigger
- [[동일인]] - 재벌 그룹 정점 인물·회사, 공정위 지정
- [[특수관계인]] - 최대주주와 혈연/계열 연결된 자
- [[5%-대량보유]] - 5% 이상 보유 시 보유목적 공시 의무
- [[자사주]] - 의결권 없는 자기주식, 경영권 방어 수단
- [[소액주주]] - 유통주식 대부분 보유, 위임장 표밭
- [[감사위원-의결권-제한]] - 감사위원 선임 시 3% 초과 지분 의결권 제한
- [[참석률]] - KIND 투표결과 역산, KOSPI 200 평균 73.3%
- [[파서-판정-등급]] - SUCCESS / SOFT_FAIL / HARD_FAIL 3등급 체계
- [[보수한도]] - 이사/감사 보수 최고 한도, 소진율 분석
- [[정관변경]] - 정관 변경 안건, 하위 안건 분할 빈번
- [[주주제안]] - 소수주주가 직접 안건 제안하는 권리
- [[감액배당]] - 자본준비금 감소 -> 이익잉여금 전입 -> 배당
- [[시간순서-규칙]] - 공고->결과 참조 OK, 결과->공고 금지
- [[v4-스키마]] - 통합 JSON v4 스키마 (meetingInfo, agendas, voteResults)
- [[소진율]] - 보수한도 대비 실지급 비율, compensation 핵심 지표
- [[자본준비금]] - 자본준비금 감소 -> 이익잉여금 전입, 감액배당 전제 조건
- [[당기순이익]] - 배당성향 계산 기준, 반드시 연결 지배주주 귀속
- [[주주환원]] - CSR(한국식 배당+자사주 매입/지배순이익) vs TSR(글로벌 주가변동+배당/시작주가) 정의 분리
- [[경영권-방어]] - 프록시 파이트 대응 전술, 4가지 방어 시나리오

## Entities

- [[DART-OpenAPI]] - 금감원 전자공시 오픈 API, OPM 핵심 데이터 소스
- [[KRX-KIND]] - 한국거래소 기업공시채널, 주총결과 크롤링
- [[네이버-금융]] - 주가/시가총액 데이터, 배당수익률 종가 소스
- [[Upstage-OCR]] - PDF OCR 서비스, 3-tier fallback 최종 tier
- [[OpenProxy-MCP]] - 공개 MCP 서버, 11개 tool (v2.0.0), CC BY-NC 4.0
- [[OpenProxy-AI]] - 비공개 파이프라인+프론트엔드, KOSPI 200 대시보드
- [[국민연금]] - 한국 최대 기관투자자, 5% 대량보유 다수
- [[FastMCP]] - Python MCP 서버 프레임워크
- [[opendataloader]] - PDF 마크다운 변환 라이브러리, _pdf tier 백엔드

## Decisions

- [[MCP-개발-lessons-learned]] - MCP 개발 7가지 핵심 교훈 (v1→v2 회고, 2026-04-19)
- [[tool-changelog]] - Tool 제거/통합/리네임 이력 (41→32개, 이유 포함)
- [[pblntf-ty-필터링]] - DART 검색 시 pblntf_ty 필수 지정, 전체 순회 금지 (D/E/I 코드표)
- [[배당공시유형]] - 배당 관련 거래소공시 9종 문서 구조 트리
- [[경력-파서-벤치마크-2026-04]] - personnel XML 878명 전수 벤치마크 (SUCCESS 79.4%)
- [[XML-vs-PDF]] - XML 1차 + PDF 보강이 최적, PDF-only는 역효과
- [[BeautifulSoup-파서-선택]] - lxml 채택 (30% 빠름, 결과 동일)
- [[LLM-fallback-설계]] - 정규식 -> zone 추출 -> LLM 하이브리드 전략
- [[free-paid-분리]] - MCP(public) + Pipeline(private) 2-repo 구조
- [[DART-KIND-매핑-화이트리스트-2026-04]] - KIND 병행 허용 공시 화이트리스트 + false match 사례
- [[tool-추가-검증-정책]] - release_v2 신규 tool 추가 시 action/data별 검증 매뉴얼 + 화이트리스트 체크
- [[파서-성능-추이]] - 2026-03-20부터 04-06까지 8개 파서 개선 이력
- [[cross-domain-체이닝]] - AGM/OWN/DIV 도메인 간 tool 연결 맵 + 시나리오
- [[파이프라인-아키텍처]] - 199개 기업 v4 JSON 생성 배치 파이프라인
- [[open-proxy-guideline]] - OPM 자체 의결권 행사 정책 v1.2 (12 카테고리 116 룰 + 11 novel topics + 2026 신법 7개 + §382의3 cross-cutting)
- [[decision-matrix-design]] - 12 카테고리 의사결정 매트릭스 (100 dim, 76 빙고 패턴 — OPM 단독 다차원 평가 시스템)
- [[matrix-auto-scoring-2026-04-29]] - 매트릭스 자동 채점 시스템 v1.3 (proxy_guideline_scoring.py: ~71 dim auto + 빙고 인터프리터 + KT&G/삼성전자 검증)
- [[opm-guideline-debate-transcript]] - 7 전문가 토론 + v1.0 → v1.1 → v1.2 결정 transcript
- [[turnkey-improvement-2026-04-29]] - 11 agent 병렬 작업 통합 (G1-G4 + 7 페르소나 + 모더레이터 결정)

## Templates

- [[tool-추가-검증-템플릿]] - 신규 data/action tool 제안, 화이트리스트 확장, 출시 게이트 복붙 템플릿

## Comparison

- [[stkrt-vs-ctr_stkrt]] - DART 대량보유 API: stkrt(합산) vs ctr_stkrt(주요계약체결) 차이
- [[회사측-vs-주주측-위임장]] - 위임장 문서 구조 차이, flr_nm 구분법, 행사방향 파싱 위치
- [[배당-자사주-공시-종합]] - 배당 5종 + 자사주 5종 + 2026.03 신법 통합 비교 (의무/소스/필드/OPM tool 매핑)

## Analysis

- [[KIND-주총결과]] - KIND 크롤링 기반 투표결과+참석률 역산 분석
- [[release_v2-tool-아키텍처]] - release_v2 공개 tool 표면과 내부 source flow를 도식화한 문서
- [[release_v2-public-tool-검증-매트릭스]] - release_v2 공개 표면 전체의 소스/화이트리스트/출시 판정 요약표
- [[company-tool-검증-예시]] - company data tool 제안/검증 예시
- [[shareholder_meeting-tool-검증-예시]] - shareholder_meeting data tool 제안/검증을 실제 샘플로 채운 예시
- [[ownership_structure-tool-검증-예시]] - ownership_structure data tool 제안/검증 예시
- [[dividend-tool-검증-예시]] - dividend data tool 제안/검증 예시
- [[proxy_contest-tool-검증-예시]] - proxy_contest data tool 제안/검증 예시
- [[value_up-tool-검증-예시]] - value_up data tool 제안/검증 예시
- [[evidence-tool-검증-예시]] - evidence data tool 제안/검증 예시
- [[release_v2-action-tool-검증-초안]] - action tool 3종의 phase-2 검증 초안
- [[주총방어-시나리오-4가지]] - 상법 개정 대응 방어 전술 4가지 (미래에셋증권)
- [[상법개정-타임라인-2026]] - 2025-2027 상법 개정 시행 일정
- [[proxy-voting-decision-tree]] - 3개 소스 통합 의결권 행사 판단 프레임워크
- [[screen_events-design]] - screen_events discovery tool 설계 + 14 event_type 전수조사
- [[corporate_restructuring-design]] - 합병/분할/주식교환 통합 data tool (4 scope, DS005 4종 API 병렬)
- [[dilutive_issuance-design]] - 유상증자/CB/BW/감자 희석성 증권 발행 통합 data tool
- [[related_party_transaction-design]] - 타법인주식 거래 + 단일공급계약 통합 data tool (list.json + 키워드)
- [[parsing-audit-2026-04-21]] - 10 data tool × 20 회사 파싱 건강도 audit (exact/partial/error 분포, 속도, API 호출 수)
- [[parsing-audit-2026-04-22]] - 확장 audit: 14 scope × 15 회사 + 필드 채움률 + corp_gov_report 포함
- [[corp_gov_report-design]] - 기업지배구조보고서 파서 설계 (15 핵심지표, 준수율, 원문 파싱)
- [[voting-policy-consensus-matrix]] - 7 운용사 의결권 정책 합의/이견 매트릭스 (79 토픽, 12 카테고리)
- [[parsing-audit-2026-04-29]] - 196 기업 (KOSPI 100 + KOSDAQ 96) × 11 tool 전수 audit (exact 66.9%, error 1.16%, regression 0)
- [[parsing-audit-2026-04-29-v2]] - audit v2: no_filing 분리 + 진짜 partial 측정 (4-class, 진짜 partial 1.5%)
- [[parsing-fix-2026-04-29-cgr-financial]] - corp_gov_report 금융지주 18건 partial → 0 fix (financial_form 감지, regression 0)
- [[parsing-fix-2026-04-27-ownership-stockknd]] - ownership_structure 17건 partial → 0 fix (stock_knd 변형 positive matching + 3-tier fallback, regression 0)
- [[speed-optimization-2026-04-29]] - 9건 sequential → asyncio.gather 적용 (proxy_contest 4x, ownership 3x, dividend 3x, regression PASS)
- [[cash-shareholder-return-2026-04-29]] - dividend tool CSR(한국식 배당+자사주 매입/지배순이익) — T22 소각→매입 정정 (KT&G 92.21%, 삼성전자 2024 38.10% / 2025 40.71% 검증)
- [[total-shareholder-return-2026-04-29]] - dividend tool TSR(글로벌 주가변동+배당/시작주가) 신규 (KT&G 25.98%, 삼성전자 2024 -31.35% / 2025 +127.66% 검증)

## Disclosures

- [[주주총회소집공고]] - DART, 의무/정기, AGM 전체의 기반 공시
- [[주주총회결과]] - KRX KIND, 의무/수시, 투표결과/참석률
- [[사업보고서]] - DART, 의무/정기(연 1회), 재무/지분/배당 종합
- [[반기보고서]] - DART, 의무/정기(반기), 중간 재무/배당
- [[분기보고서]] - DART, 의무/정기(분기), 분기 재무/배당
- [[현금배당결정]] - KRX+DART(I), 의무/수시, DPS/기준일/지급일/시가배당률 (dividend)
- [[주식배당결정]] - KRX+DART(I), 의무/수시, 1주당 배당주식수(소수 7자리)/배당주식 총수 (dividend)
- [[배당기준일결정]] - KRX+DART(I), 의무/수시, 2024 자본시장법 개정 후 선배당-후결의 가능 (dividend)
- [[분기배당결정]] - KRX+DART(I), 의무(정관)/수시 분기별, 연간 DPS = 1Q+반기+3Q+결산 (dividend)
- [[감액배당결정]] - DART(B), 의무/수시, 자본준비금 감소 → 이익잉여금 전입 → 배당, 주총 특별결의
- [[배당공시유형]] - 배당 관련 거래소공시 6종 원문 트리 (자회사판 포함, 통합 인덱스)
- [[대량보유상황보고서]] - DART, 의무/수시(5% 변동), 보유목적/보유량
- [[위임장권유참고서류]] - DART, 의무(권유 시)/수시, 프록시 파이트 핵심
- [[최대주주등소유주식변동신고서]] - KRX KIND, 의무/수시, 최대주주+특관인 지분 변동 (ownership_structure scope=changes)
- [[회사합병결정]] - DART, 의무/수시(DS005), 합병비율·상대방·매수청구권 (corporate_restructuring scope=merger)
- [[회사분할결정]] - DART, 의무/수시(DS005), 분할형태·신설/존속회사 (corporate_restructuring scope=split)
- [[회사분할합병결정]] - DART, 의무/수시(DS005), 분할 + 합병 동시 결정 (corporate_restructuring scope=split)
- [[주식교환·이전결정]] - DART, 의무/수시(DS005), 지주회사 전환 도구 (corporate_restructuring scope=share_exchange)
- [[자기주식결정]] - DART, 의무/수시(DS005·I), 취득·처분·소각·신탁 5종 통합 인덱스 (treasury_share)
- [[자기주식취득결정]] - DART(DS005), 의무/수시, `tsstkAqDecsn` API, 2026.03 신법 — `aq_pp` "소각" 명시 필수
- [[자기주식처분결정]] - DART(DS005), 의무/수시, `tsstkDpDecsn` API, 자사주 마법 대상 (`dpptncmp_cmpnm`)
- [[자기주식소각결정]] - DART(I), 의무/수시, list.json+키워드, report_nm "주식소각결정" (자기주식 X)
- [[자기주식신탁결정]] - DART(DS005), 의무/수시, `tsstkAqTrctrCnsDecsn`(체결)+`tsstkAqTrctrCcDecsn`(해지)
- [[자기주식의무소각-2026신법]] - 신법(상법 §341/§342 개정 2026.03.06), 1년 내 의무소각, 한국 자사주 정책 전면 재설계
- [[기업가치제고계획]] - DART+KIND, 자율/수시, 밸류업 본계획·이행점검 (value_up)
- [[최대주주변경]] - DART, 의무/수시, 주식양수도·담보·합병 등 (screen_events major_shareholder_change)
- [[임원·주요주주특정증권등소유상황보고서]] - DART, 의무/수시(DS004), 임원·10%+ 주주 보유 변동 (elestock)
- [[소송등의제기]] - DART, 의무/수시, 회사 당사자 소송·가처분 (proxy_contest litigation)
- [[경영권분쟁소송]] - DART, 의무/수시, 경영권 분쟁 명시 분류 (proxy_contest management_dispute)
- [[유상증자결정]] - DART, 의무/수시(DS005), 배정방식·신주 수·희석률 (dilutive_issuance rights_offering)
- [[전환사채발행결정]] - DART, 의무/수시(DS005), 전환가·잠재 희석·refixing (dilutive_issuance convertible_bond)
- [[신주인수권부사채발행결정]] - DART, 의무/수시(DS005), 행사가·분리형·대용납입 (dilutive_issuance warrant_bond)
- [[감자결정]] - DART, 의무/수시(DS005), 감자비율·사유·일정 (dilutive_issuance capital_reduction)
- [[타법인주식및출자증권거래]] - DART, 의무/수시(B+I), 양수·양도·취득·처분 4형태 (related_party_transaction equity_deal)
- [[단일판매공급계약체결]] - DART+KIND, 의무/수시(I), 매출 5%+ 단일계약 체결·해지 (related_party_transaction supply_contract)
- [[기업지배구조보고서]] - DART+KIND, KOSPI 전체 의무(2024년~) / KOSDAQ 자율, 15 핵심지표 준수 (corp_gov_report)

## Sources

- [[agm-tool-rule]] - AGM 40개 tool 구조, fallback 흐름, 파싱 한계
- [[div-tool-rule]] - 배당 5개 tool 구조, 연산 규칙 (성향/수익률)
- [[own-tool-rule]] - 지분 7개 tool 구조, 출력 형태, 데이터 소스 우선순위
- [[prx-tool-rule]] - 위임장 5개 tool 구조, 검색/파싱 방법, 행사방향 추출 규칙
- [[dart-kind-disclosure-taxonomy]] - DART A~J / KIND 공시군 매핑과 v2 소스 정책 기준
- [[devlog]] - 2026-03-19부터 04-06 개발 히스토리 요약
- [[jpm-voting-process]] - JPMAM proxy voting 5단계 프로세스 (mermaid flowchart)
- [[주총방어전략-2026]] - 주총 방어 시나리오 4가지 (미래에셋증권 리서치 2026.03.19)
- [[주총체크리스트-2026]] - 주총 체크리스트 9개 + 상법 개정 타임라인 (미래에셋증권)
- [[data-collection-architecture]] - OPM 전수 데이터 수집 entry point + 파싱 방법 (DART/KIND/Naver/Upstage/정적 JSON, 14 섹션 639줄)
- [[nps-voting-disclosure]] - 국민연금 의결권 행사내역 크롤링 (fund.nps.or.kr 직접) + 정적 캐싱 + scope=nps_record

## Archive

- [opm-readme](archive/opm-readme.md), [opa-readme](archive/opa-readme.md), [benchmark](archive/benchmark-personnel-results.md), [agm-case-rule](archive/agm-case-rule.md), [own-case-rule](archive/own-case-rule.md), [div-case-rule](archive/div-case-rule.md) — sources 아카이브
- [임원주요주주](archive/임원주요주주특정증권등소유상황보고서.md), [자기주식취득처분결정](archive/자기주식취득처분결정.md), [정정공시](archive/정정공시.md) — disclosures 아카이브
