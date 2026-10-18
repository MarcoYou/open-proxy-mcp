---
type: tool
title: company
domain: data
scope: [exact, ambiguous, error]
data_source: [DART OpenAPI corpCode.xml + company.json + list.json, Naver profile (보조)]
related_disclosures: []
related_concepts: []
related_decisions: [pblntf-ty-필터링]

created: 2026-05-01
updated: 2026-10-18
---

# company

## 한 줄 요약
기업 식별 + 최근 공시 인덱스 허브. 모든 data tool의 공통 입구. 한글·영문 회사명/ticker/corp_code → 시장·업종·최근 공시 확인.

## 사용법
```
company(
    query="삼성전자",
    max_recent_filings=10,
    start_date="20260101",
    end_date="20260427",
)
```

자연어 예시:
- "삼성전자 식별자랑 최근 공시 보여줘" → `query="삼성전자"`
- "KT&G corp_code 확인" → `query="KT&G"` (약칭 + special char 매칭)
- "Samsung Fire 배당" → `query="Samsung Fire"` (DART 공식 영문명 토큰 조합)
- "HD Hyundai Electric" → `query="HD Hyundai Electric"` (법인 접미사·구두점 정규화)
- "삼성" / "Samsung" → 활성 상장 후보 중 시총 격차가 충분하면 삼성전자로 자동 추론하고 대안 표시
- "'카카오'라는 이름으로 상장사 여러 개면 뭐뭐 있어?" → 동명·유사명 후보 목록(status `ambiguous`)

## 입력 인자
| 인자 | 타입 | 필수 | 설명 | 기본값 |
|---|---|---|---|---|
| query | str | yes | 회사명 / ticker / corp_code | - |
| max_recent_filings | int | no | 최근 공시 표시 수 (1-20) | 10 |
| start_date | str | no | YYYYMMDD, 미지정 시 자동 | "" |
| end_date | str | no | YYYYMMDD, 미지정 시 오늘 | "" |
| format | str | no | "md" / "json" | "md" |
| language | str | no | `auto` / `ko` / `en`. 호출 AI가 응답 문구 언어 지정 | `auto` |

## 출력 schema (data dict)
```json
{
  "company_id": "...",
  "canonical_name": "삼성전자(주)",
  "names": {"en": "SAMSUNG ELECTRONICS CO,.LTD", "aliases": [...]},
  "company_resolution": {
    "match_type": "canonical|alias|inferred",
    "matched_on": "official|normalized|token|substring|fuzzy",
    "confidence": "high",
    "reason": "...",
    "market_data_as_of": "20260721",
    "market_data_source": "krx_daily|local_popularity_prior",
    "ranking_signal": "market_cap|local_popularity_prior",
    "alternatives": []
  },
  "identifiers": {"ticker": "005930", "corp_code": "00126380",
                  "isin": "...", "jurir_no": "...", "bizr_no": "..."},
  "classification": {"market": "KOSPI", "sector_name": "...",
                     "induty_code": "...", "fiscal_month": "12"},
  "basic_info": {"ceo_name": "...", "established_date": "...",
                 "address": "...", "homepage": "..."},
  "recent_filings": [{"disclosure_date": "...", "filing_type": "...",
                      "report_name": "...", "filer_name": "...",
                      "rcept_no": "..."}],
  "recent_filings_window": {"start_date": "...", "end_date": "..."},
  "candidates": [...]    // status=ambiguous 시
}
```

핵심 필드:
- `status`: `exact` (공식명 또는 자동 추론 성공) / `ambiguous` (동일하게 강한 공식명 후보 N건) / `error` (식별 실패).
- 외부 `status` 계약은 유지한다. 자동 추론 성공도 `exact`이고 `company_resolution.match_type=inferred`로 근거와 대안을 구분한다.
- 최신 KRX 전체 스냅샷이 있고 코스피 700·코스닥 1,200 종목 이상(시장 칸은 DB 코드 `KS`/`KQ` 로 정규화해 센다)이며 14일 안의 것이면 **활성 상장 명부**로 쓴다. 부분명(추론)은 명부에 있는 회사만 고른다 — 상장폐지·합병 회사와 코넥스 회사는 정식명·종목코드로만 찾는다(「현대미포」 ✗ / 「에이치디현대미포」·「HD현대미포」·「010620」 ✓). 「현대미포」는 `error` 이되 `candidates` 맨 앞에 에이치디현대미포가 근접 후보로 나온다(아래 「파싱 전략」). 스냅샷이 없거나 불완전하면 lexical 검색으로 fail-open한다.
- 명부를 한 번 켠 뒤 다시 읽기가 실패하거나 반쪽 단면이면 직전 명부를 지키고 60초 뒤 다시 읽는다(로컬 인기도 표본으로 떨어지지 않는다).
- 최근 공시 인덱스(`recent_filings`): DART 가 「조회된 데이터 없음」(013)으로 답하면 그 구간 공시가 0건이라는 **답**이다 — `warnings` 에 정보 문구 「조회 구간(…)에 DART 공시가 없습니다 — 조회는 정상이며 그 기간 공시가 0건입니다.」, `filing_status=no_filing`. 그 밖의 DART 오류(020 한도·800 점검·전송 오류)는 「최근 공시 인덱스 조회 실패: {상태}」 경고와 `filing_status=fetch_failed`·`no_filing=false` — 0건이 「없다」가 아니라 「모른다」다(md 표도 「조회 실패 — 공시 유무 미확인」). 회사 식별 `status` 는 어느 쪽이든 `exact`.
- `candidates`: ambiguous 시 후보 리스트, error 시 근접 후보(제안일 뿐 자동 선택하지 않는다) → ticker/corp_code 직접 입력으로 재조회.

## Data sources
- **DART API**: `corpCode.xml` (한글명·공식 영문명·식별자, ZIP→XML, memory→SQLite 캐시), `company.json` (법인번호/업종코드), `list.json` (최근 공시).
- **KRX 일별 시세 저장분(`krx_daily`, 최신 거래일 전 종목 단면)**: 전체 상장 여부 + 시가총액 prior. 요청별 조회 없이 resolver 생성 시 한 번 메모리에 적재(1시간 캐시).
- 외부 호출: 최대 3회 (corpCode 캐시 적중 시 2회).

## Flow

```mermaid
sequenceDiagram
    participant U as User
    participant T as company
    participant DC as DART corpCode
    participant DI as DART company.json
    participant DL as DART list.json
    U->>T: query="삼성전자", max_recent_filings=10
    T->>DC: corp master + indexed CompanyResolver (캐시 적중 시 0회)
    DC-->>T: matches list
    T->>T: lexical tier → 활성 상장 → 시총 격차 (exact/ambiguous/error)
    alt status=ambiguous or error
        T-->>U: candidates list (다시 ticker/corp_code로 재조회 안내)
    else status=exact
        par 병렬 호출 (asyncio.gather)
            T->>DI: company.json(corp_code)
        and
            T->>DL: list.json(corp_code, 최근 180일)
        end
        DI-->>T: 영문명/법인번호/업종
        DL-->>T: recent_filings (각 _classify_filing으로 타입 부여)
        T->>T: payload 조립 (identifiers + classification + recent_filings + filing_meta)
        T-->>U: ToolEnvelope (status=exact + data + next_actions)
    end
```

호출 횟수: corpCode 캐시 적중 시 2회 (company.json + list.json). 캐시 미스 시 +1 (최대 3회). 회사명 검색 자체는 외부 호출 0회다.

## 파싱 전략
- 검색 tier: ticker/corp_code → 공식 한글·영문명 → curated alias → normalized phrase/compact → token AND → substring → 제한적 fuzzy.
- **강한 매칭이 약한 매칭을 이긴다** — 약한 tier(token/substring/fuzzy)에 먼저 걸려도 거기서
  멈추지 않고 역음차 경로를 끝까지 본다. 「지에스」는 부분일치로 「지에스이」에 걸려 「GS」를
  영영 못 찾고 있었다(「에스케이」→에스케이바이오팜도 같은 유형). 역음차가 약하게만 맞으면
  원래 결과를 유지한다 — 「제이와이피」는 원문으로 아무것도 안 걸리고 'jyp' 토큰으로만 닿는다.
- **이름이 정확히 맞지 않으면 응답이 그 사실을 밝힌다** — 추정으로 고른 기업은 모든 tool 의
  `warnings` 맨 앞에 「「지에스」를 **지에스이**(으)로 추정했습니다」가 실린다. 적는 곳은 해석
  확정 관문 하나(`_resolve_match`), 읽는 곳은 `ToolEnvelope.to_dict()` 하나다. `ToolEnvelope`
  를 쓰지 않는 서비스(`valuation`·`asset_holdings`·`screener`)는 진입 함수를
  `declare_weak_resolution` 으로 감싼다. 드리프트는 정적 테스트로 막는다.
- **법인격은 앞뒤 어디에나 붙는다** — DART 정식명은 「(주)광무」·「주식회사솔루엠」처럼 앞에 오기도
  한다. `$` 앵커로 뒤만 떼면 공시에서 복사한 이름이나 **우리 tool 이 출력한 이름을 그대로 다시 물을 때**
  실패한다. 「주성엔지니어링」·「유한양행」·「사조오양」처럼 우연히 같은 글자로 시작하는 상호를 깎지
  않도록 닫는 괄호나 '식회사'를 요구한다.
- **역음차(reverse transliteration)** — DART 등록명이 라틴 표기인데 공고 헤더는 한글 음차로 적는다
  (에스케이씨=SKC · 씨제이대한통운=CJ대한통운 · 에이치엘비=HLB). 알파벳 26자의 한글 음차표로 앞머리
  연쇄를 되돌린다. 「엔」은 알파벳 N이자 「엔터테인먼트」의 첫 글자라 어디까지 letter인지 정할 수 없어
  **길이별 변형을 모두 색인**하고(제이와이피엔터테인먼트 → jyp엔터테인먼트), 질의·색인 **양쪽에 같은
  변환**을 걸어 반대 방향도 성립시킨다. 1글자는 우연 일치가 많아(이수페타시스의 '이'=E) 2글자부터다.
- **정식 상호 → 짧은 등록명** — 공시(타법인출자현황 등)는 법인격·한글 음차·법적 상호로 적고 DART 등록명은 짧다.
  ① 법인격만 붙은 정식명(「한화갤러리아㈜」·「주식회사 카카오」)은 정식명 일치로 본다 — 종전엔 같은 글자로 시작하는
  「한화갤러리아타임월드」 때문에 브랜드 검색(추정)으로 떨어졌다. ② 이름 **가운데·끝**의 음차(삼성에스디아이=삼성SDI ·
  삼성이앤에이=삼성E&A · 뉴엔에이아이=뉴엔AI)는 연쇄를 골라 되돌린 조합(뒷부분만 포함)을 만든다. ③ 리츠 법적 상호
  「…(위탁관리|기업구조조정|자기관리)부동산투자회사」는 꼬리를 「리츠」로 바꾸고, 없으면 뗀다(롯데리츠 · 이리츠코크렙).
  ②③은 음차표 글자가 흔한 음절이라 **등록명과 정확히 같아진 변형만** 받고, 한 묶음에서 두 회사로 갈리면 고르지 않는다
  (「에스케이위탁관리…」의 꼬리를 떼면 SK(주)와 같아지므로 「리츠」로 바꾼 쪽을 먼저 본다). 우선주(「삼성전자우」)는 회사가
  아니라 종목이라 여기서 다루지 않는다 — 보통주 회사로 추정만 한다.
- **업종어 접미** — 공고 헤더는 정식 상호이고 등록명은 짧다(삼성생명보험→삼성생명 · 흥국화재해상보험→
  흥국화재 · 대한약품공업→대한약품). **짧게 떼는 것부터** 시도하고 후보가 정확히 하나일 때만 받는다 —
  「미래에셋생명보험」에서 '생명보험'을 떼면 「미래에셋」이라는 별개 회사가 나온다.
- **비활성 회사에만 걸린 단계는 최후 수단으로 건너뛴다** — 활성 명부는 추론 tier(token/substring) 결과에서
  비활성(코넥스·상장폐지)을 지운다. 그 tier 가 통째로 비어도 다음 tier 로 내려가지 않으면 「못 찾음」이다 —
  「세이」는 토큰이 세이프아시아(코넥스)에만 걸려 이름에 「세이」를 품은 세이브존I&C(substring)에 못 닿았다.
  위 경로(역음차·업종어 포함)가 **모두 빈 뒤에만** 원문을 다시 찾으며 그런 tier 를 건너뛴다. 그래서 이미 답이
  나오는 질의는 그대로이고(「엘비」→LB세미콘 역음차 유지, 한글 부분일치 「티엘비」가 가로채지 않음) 비활성 회사를
  되살리지도 않는다.
- **못 찾으면 근접 후보를 보여준다(자동 선택 X).** 앞자르기 자동선택은 오답을 낸다(에스피씨삼립→
  「케이에스피」·포스코디엑스→「POSCO홀딩스」·NICE홀딩스→「NICE」). 같은 계산을 '제안'으로 쓰면
  안전하다 — 고르는 것은 사람이다. 순서는 ① 질의 **전체**를 이름 어디엔가 품은 종목코드 보유 법인
  (시총·원장 최신순 — 「현대미포」→에이치디현대미포 · 「더존」→더존비즈온) ② 질의 앞부분이 긴 순으로 맞는 곳
  (「에이플러스에셋어드바이저」→에이플러스에셋), 최대 5개. 질의 전체를 맞춘 쪽이 앞부분 일부만 맞춘 쪽보다 늘 앞이다
  (「인프라코어」→에이치디현대인프라코어가 인프라웨어테크놀러지보다 먼저). 한 글자 질의는 제안하지 않는다.
- NFKC·대소문자·공백·구두점·하이픈·법인 접미사를 정규화하고 `&`/`and`/`앤` connector를 같은 토큰으로 취급한다.
- 공식명 exact는 시총보다 절대 우선한다. `SK`는 SK하이닉스가 아니라 `SK`(034730)다.
- 부분명은 시총 정보가 있는 최상위 후보를 바로 선택한다. 1·2위 격차가 1.5배 이상이면 `confidence=high`, 그보다 작으면 `low`와 대안을 함께 반환하며 사용자에게 되묻지 않는다.
- 서로 다른 법인이 같은 strong 공식명/정규화명으로 충돌할 때만 `ambiguous` 후보를 순서대로 반환하고 질문 문장은 만들지 않는다.
- 안내문은 한국어·영어 두 버전이며 호출 AI가 `language`로 선택한다. `auto`는 입력 문자 기준 fallback이다.
- fuzzy는 indexed 검색이 전부 실패하고 compact 길이가 5자 이상일 때만 cutoff 0.88로 적용한다.
- 11만 전체 법인에서는 corp_code exact만 색인하고, 이름 인덱스는 종목코드 보유 법인만 구축한다.
- regression 0 검증: 200기업 audit에서 `company.summary` exact 98.5% (193/196), error 1% (비상장 매핑 실패).

## 관련 공시 (rules/disclosures/)
- 해당 없음 (식별 카드 tool. 공시 본문 파싱은 후속 data tool 담당)

## 관련 개념 (rules/concepts/)
- 해당 없음

## 관련 결정 (decisions/)
- [[pblntf-ty-필터링]] — recent_filings 조회 시 pblntf_ty 필수

## 관련 audit/fix (private storage `wiki-private/architecture/audits/`)
- 260429_0912_audit_parsing-200기업-v2-no_filing — `company.summary` 98.5% exact (193/196 KOSPI 100 + KOSDAQ 96)

## 알려진 issue + TODO
- ISIN/jurir_no/bizr_no는 DART company.json에 없는 경우 비어 있음 (TODO: KIND·KRX 보강).
- KRX 전체 스냅샷이 14일보다 오래되면 활성 상장 필터로 사용하지 않고 시총 정렬 정보로만 사용한다.
- recent_filings 기본 lookback이 자동 (start_date 비워두면 직전 N개월). 명시 권장.

## 사명 변경 추적 (260808)

**DART 에는 옛 사명이 어디에도 없다.** corpCode.xml 은 현재 사명만 싣고, 공시 목록(list.json)의
`corp_name`·`flr_nm` 조차 **과거 공시에까지 현재 사명을 소급**해 채운다 — 실측 036560 의 2024-03
공시 6건이 전부 「KZ정밀」로 나온다(당시 사명은 영풍정밀). 문서 본문의 「(구 영풍정밀)」 표기를
긁어보면 캐시 1,129건에서 524건이 걸리지만 「회생채권」·「808」 같은 오탐이 태반이라 별칭 사전으로
못 쓴다. **잘못된 별칭은 없는 것보다 나쁘다.**

그래서 7일마다 받는 corpCode.xml 을 스냅샷으로 남기는 것이 유일한 구조적 방법이다.
`corp_name_history(corp_code, corp_name, first_seen, last_seen)` 에 갱신 때마다 전량 upsert 하고 —
PRIMARY KEY 가 중복을 흡수하니 실제로 행이 느는 건 사명이 바뀐 회사뿐이다 — 「옛 이름」은 현재
사명과 달라진 행으로 정의한다. 저장 경로가 매번 `DELETE FROM corp_codes` 후 전체 재적재라
**지우기 전에** 적재한다. live 는 fly 영속 볼륨(`/data/master.db`)이라 배포해도 안 지워진다.

조회 실패 시 이력을 보고 「'X'는 사명이 바뀌었다 — 현재 'Y'(종목코드 NNNNNN)다」로 답한다.
**한계**: 스냅샷을 남기기 시작한 뒤의 변경만 잡는다. 영풍정밀→케이젯정밀처럼 이미 지나간 변경은
복구할 수 없고, 그 경우는 「종목코드로 재조회」 안내로 물러난다.

## 변경 이력
- 2026-10-18: 추론 tier 가 비활성 회사에만 걸려 통째로 비면 **최후 수단으로 다음 tier 까지 내려간다**. 「세이」·「루트」는 이름에 그 조각을 품은 활성 상장사(세이브존I&C·에이루트)가 있는데도 토큰이 코넥스(세이프아시아·루트락)에만 걸려 「못 찾음」이었다. 대표 66 + 활성 이름 앞 2글자 1,206 중 바뀜은 이 2개, 정식명 3,995 중 0. 활성 이름 가운데 2글자 조각 2,454 중 「못 찾음」 78 → 0(대부분 이미 부분일치로 고르던 규칙과 같아진다). 이미 답이 나오던 질의는 바뀌지 않는다.
- 2026-10-18: 최근 공시 인덱스의 DART 013(그 구간 공시 0건)을 「최근 공시 인덱스 조회 실패: 013」으로 적던 것 → 정보 문구 「조회 구간(…)에 DART 공시가 없습니다 — 조회는 정상이며 그 기간 공시가 0건입니다.」(영문 응답은 영문). 반대로 진짜 조회 실패는 `filing_status=fetch_failed`·`no_filing=false` 로 적고 md 표에 「최근 공시 없음」 대신 「조회 실패 — 공시 유무 미확인」(종전엔 실패도 `no_filing`·「최근 공시 없음」이었다). 실측: 합병 상장폐지사(에이치디현대미포)의 HD현대미포·010620 조회.
- 2026-10-18: 못 찾았을 때 근접 후보에 **이름 가운데 부분일치**를 더한다. 활성 명부가 켜지자 「현대미포」·「더존」·「루트」처럼 질의가 이름 가운데에 있거나 2·3글자인 조각은 앞자르기만 보던 제안이 비어 「못 찾음」만 나갔다(대표 8개 중 7개 후보 0개, 「인프라코어」는 엉뚱한 인프라웨어테크놀러지 1개) → 8개 모두 원래 회사가 후보 1순위. 자동 선택은 여전히 없다.
- 2026-10-18: 활성 상장 명부가 처음으로 실제로 켜진다. 완전성 판정이 `KOSPI`/`KOSDAQ` 을 세는데 DB 값은 2026-08-23 개명 뒤 `KS`/`KQ` 라 그동안 늘 꺼져 있었다(`active_registry_used=false`) — 부분명이 상장폐지 회사를 고르고 근거 문구가 「종목코드 보유 법인」으로 나갔다. 판정을 시장 코드 정규화(`market_codes.to_db`)로 맞췄다.
- 2026-10-18: 이름 해석의 시총·상장 명부 근거를 일별 시세 저장분의 최신 거래일 단면(`krx_daily`)에서 읽는다(종전 `krx_weekly` 이번 주 행).
  `company_resolution.market_data_source` 값이 `krx_weekly` → `krx_daily`. 시총·기준일 값은 그대로.
- 2026-10-18: 공시의 정식 상호를 등록명으로 — 법인격만 붙은 정식명은 정식명 일치, 이름 가운데·끝 음차, 리츠 법적 상호(위 「파싱 전략」).
  실측(같은 명부·시장 맥락, main 대비): 정식명 3,969·앞 2글자 조각 1,206 변화 0 · 「X㈜·(주)X·주식회사 X」 7,944 중 강한 정답 7,530→7,944(옛 추정 오답 165 포함) ·
  영문 든 상장사 이름의 한글 읽기 332 중 275→330 · 리츠 법적 상호 44 중 0→44 · 다른 회사로 강하게 잡힌 것 늘어남 0.
- 2026-09-07: 최근 공시 표의 분류가 코드(`ownership_block`)로 나가던 것 → 우리말(대량보유 보고·주총 소집공고·배당 결정 …). 영어 응답은 코드 유지.
- 2026-08-08: 사명 변경 이력 누적(`corp_name_history`) + 미발견 안내가 현재 사명을 알려줌 (위 절).
- 2026-08-06: 스윕 서사·측정 상세를 private storage 로 이관(경계 규칙 [[wiki_schema]] 0.0).
- 2026-08-05: 약한 매칭이 강한 매칭을 가로채던 것 수정 + 추정 사실을 25개 tool 전체 `warnings` 로 전파
  (`declare_weak_resolution`).
- 2026-07-27: 공시 회사명 조회 3종 보강(접두 법인격 · 역음차 · 업종어 접미) + 실패 시 근접 후보 제안.
- 2026-07-22: DART 공식 영문명을 corp master/SQLite 에 저장. 한글·영문·혼합 토큰 indexed resolver,
  활성 상장·시총 prior, 추론 근거/대안, 제한적 fuzzy, `language=ko|en` 문구 선택.
- 2026-05-01: tool wiki 페이지 작성.
- 2026-04-29: 200기업 v2 audit 통과 (98.5% exact).
- 2026-04-18: tool 신설 (corp_identifier 후속, recent_filings + ISIN 보강).
