---
type: index
title: OPM Wiki Index
updated: 2026-04-18
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
- [[주주환원]] - 배당 + 자사주 매입 = 총 주주환원 규모
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

## Templates

- [[tool-추가-검증-템플릿]] - 신규 data/action tool 제안, 화이트리스트 확장, 출시 게이트 복붙 템플릿

## Comparison

- [[stkrt-vs-ctr_stkrt]] - DART 대량보유 API: stkrt(합산) vs ctr_stkrt(주요계약체결) 차이
- [[회사측-vs-주주측-위임장]] - 위임장 문서 구조 차이, flr_nm 구분법, 행사방향 파싱 위치

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

## Disclosures

- [[주주총회소집공고]] - DART, 의무/정기, AGM 전체의 기반 공시
- [[주주총회결과]] - KRX KIND, 의무/수시, 투표결과/참석률
- [[사업보고서]] - DART, 의무/정기(연 1회), 재무/지분/배당 종합
- [[반기보고서]] - DART, 의무/정기(반기), 중간 재무/배당
- [[분기보고서]] - DART, 의무/정기(분기), 분기 재무/배당
- [[현금배당결정]] - KRX, 의무/수시, DPS/기준일/지급일
- [[배당공시유형]] - 배당 관련 공시 6종 원문 트리 (현금배당결정/기준일결정/주식배당결정 등)
- [[대량보유상황보고서]] - DART, 의무/수시(5% 변동), 보유목적/보유량
- [[위임장권유참고서류]] - DART, 의무(권유 시)/수시, 프록시 파이트 핵심

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

## Archive

- [opm-readme](archive/opm-readme.md), [opa-readme](archive/opa-readme.md), [benchmark](archive/benchmark-personnel-results.md), [agm-case-rule](archive/agm-case-rule.md), [own-case-rule](archive/own-case-rule.md), [div-case-rule](archive/div-case-rule.md) — sources 아카이브
- [임원주요주주](archive/임원주요주주특정증권등소유상황보고서.md), [자기주식취득처분결정](archive/자기주식취득처분결정.md), [정정공시](archive/정정공시.md) — disclosures 아카이브
