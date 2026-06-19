---
type: source
title: 국민연금 의결권 행사내역 (fund.nps.or.kr)
source_path: open_proxy_mcp/dart/nps_client.py
ingested: 2026-04-29
tags: [nps, pension-fund, voting-record, scraping, asset-managers]
related: [국민연금, OpenProxy-MCP, voting-policy-consensus-matrix, decision-matrix-design]
---

# 국민연금 의결권 행사내역 (fund.nps.or.kr)

OPM이 국민연금기금운용본부의 국내주식 의결권 행사내역을 직접 크롤링/캐싱하여 `proxy_guideline` tool의 `nps_record` scope로 노출.

## 왜 NPS인가

- KOSPI 200 대다수에 5% 이상 보유한 한국 최대 기관투자자
- 캠페인/프록시 파이트 결과 예측의 핵심 변수 (얼라인·차파 등 행동주의 펀드의 표 합산 시 NPS 의사 = 결정타)
- 스튜어드십 코드 서명 → 안건별 행사내역을 모두 공개 (2013~)

## 데이터 소스

### 엔드포인트

| 종류 | URL | 메서드 | 응답 |
|---|---|---|---|
| Init (cookie 발급) | `https://fund.nps.or.kr/impa/edwmpblnt/getOHEF0007M0.do` | GET | HTML (WMONID + EOH_JSESSIONID 쿠키) |
| List | `https://fund.nps.or.kr/impa/edwmpblnt/empty/getOHEF0007M0.do` | POST (JSON body) | HTML 부분 (table tbody) |
| Detail | `https://fund.nps.or.kr/impa/edwmpblnt/getOHEF0010M0.do` | POST (form-urlencoded) | HTML 전체 |

응답 모두 HTML임 (Content-Type: `text/html; charset=UTF-8`). JSON 아님. BeautifulSoup으로 파싱.

### List body 예시

```json
{
  "pageIndex": 1,
  "issueInsNm": "두산",
  "gmosStartDt": "2025-04-29",
  "gmosEndDt": "2026-04-29"
}
```

응답 table 컬럼: `[no, company name (a tag with onclick=fnc_goDetail), nps_code, gmos date, kind label]`

### Detail form

```
edwmVtrtUseSn=
dataPvsnInstCdVl=0095000        # NPS 자체 데이터 제공기관 코드 (고정)
pblcnInstCdVl=00015             # NPS 종목코드 (5자리)
gmosYmd=20260331                # 주총일 YYYYMMDD
gmosKindCd=1                    # 1=정기주총, 2=임시주총
issueInsNm=
gmosStartDt=
gmosEndDt=
```

응답에서 의안 테이블이 두 번 나오므로 (헤더 + 본문 패턴) 첫 번째 본문만 사용.

### Detail 테이블 컬럼

| 컬럼 | 의미 |
|---|---|
| 의안번호 | "제1호", "제2-1호" 등 |
| 의안내용 | 안건명 ("재무제표승인", "정관변경", "이사 보수한도액 승인" 등) |
| 행사내용 | 찬성 / 반대 / 중립(기권) / 불행사 |
| 반대시 사유 | 반대일 경우 사유 |
| 근거조항 | NPS 세부기준 조문 번호 |

## 핵심 매핑

**NPS 종목코드 5자리 + "0" = KRX 표준 6자리 티커** (검증 100%, 10/10 케이스)

예시:
| 회사 | NPS 코드 | KRX 티커 |
|---|---|---|
| (주)두산 | 00015 | 000150 |
| 한국전력 | 01576 | 015760 |
| 한솔아이원스 | 11481 | 114810 |

→ fuzzy match 불필요. 단순 문자열 연결.
→ DART corp_code 매핑은 OPM의 `resolve_company_query(ticker)` 함수가 자동 수행.

## OPM 통합

### 모듈 위치
- `open_proxy_mcp/dart/nps_client.py` — `NPSClient` (httpx async, BeautifulSoup 파싱)
- `open_proxy_mcp/services/proxy_guideline.py` — `scope_nps_record` (회사 식별 + list/detail 통합)
- `open_proxy_mcp/tools_v2/proxy_guideline.py` — `_render_nps_record` (markdown 렌더)
- `open_proxy_mcp/data/asset_managers/nps_records/` — 정적 캐시
  - `nps_list_{year}.json` — 시즌 list
  - `details/{ticker}_{gmos_ymd}_{kind}.json` — 회사·주총별 상세
- `test/sync_nps_records.py` — 분기별 sync 스크립트

### 사용

```python
proxy_guideline(
    scope="nps_record",
    company="두산",          # 또는 ticker="000150" 또는 nps_code="00015"
    year=2025,               # 2025-04-29 ~ 2026-04-29 시즌
    fetch_detail=True,       # 안건별 상세까지 가져오기
    max_details=5,           # 상위 N건만 detail 호출
)
```

### 캐싱 전략 (하이브리드)

| 케이스 | 동작 |
|---|---|
| 정적 캐시 존재 | 즉시 반환 (0.x초) |
| 정적 캐시 없음 | live_nps 호출 + (회사명 필터 없는 풀 dump일 때) 캐시 저장 |
| 주총일이 최근 30일 안 (`_NPS_REALTIME_WINDOW_DAYS`) | detail은 항상 live_nps, 캐시 저장 안 함 (확정 전 의사 변동 가능) |
| `force_refresh=True` | 캐시 무시 강제 재호출 |

분기마다 `sync_nps_records.py YYYY --details` 권장.

## 부하 및 예의

- 페이지 사이 1초 sleep, detail 사이 2초 sleep (NPS 사이트 부하 고려)
- 풀 시즌(약 700건) 풀 sync 시 약 25분 소요
- BeautifulSoup 파싱 try/except로 사이트 구조 변경 방어

## 검증 결과 (2026-04-29 sanity test)

| 테스트 | 결과 |
|---|---|
| List (2025-04-29 ~ 2026-04-29 풀 dump) | 681건 정확 수집 (총 69 페이지, 약 78초) |
| List (회사명 "두산" 검색) | 6건 매칭, 약 3초 (NPS 서버측 필터링이 빠름) |
| Detail (두산 2026-03-31 정기주총) | 9개 안건 정확 파싱 (찬성 6 / 반대 3) |
| 티커 변환 (00015 → 000150) | 일치 |
| 캐시 hit (재호출) | list 0.00s, detail 0.35s |
| 회사명 → DART resolve → ticker → NPS 코드 | 정상 (`두산` → `000150` → `00015`) |

## 7개 운용사 + NPS = 8 행위자 분석

기존 `_index.json`의 7 운용사 (a/b/c/k/m/s/sa/t) + nps. NPS는 `manager_id="nps"`, `policy_file=null` (스튜어드십 코드 + 세부기준만 적용).

`proxy_guideline scope=consensus`는 7 운용사 합의/이견 매트릭스를 다루고, NPS는 별도 `scope=nps_record`로 분리 (NPS는 정책 파일이 없고 사후 행사내역만 공개되므로 동일 분석 프레임에 묶기 어려움).

향후 가능 확장:
- NPS 행사내역과 7 운용사 정책 vs 실제 갭 비교
- NPS against rate를 캠페인 표 예측 모델의 prior로 사용
- 5%+ 보유 사실(`own_block` API)과 결합하여 의결권 영향력 정량화
