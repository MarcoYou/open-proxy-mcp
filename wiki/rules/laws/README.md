---
type: readme
title: rules/laws/ — 한국 자본시장 법령 자료
updated: 2026-08-14
---

# wiki/rules/laws/ — 한국 자본시장 법령 자료

> 한국 상장사 거버넌스 관련 강행규정 + 우회 catalog. proxy_advise의 법령 layer 출처.

## ⚠ 260814 — 규칙 데이터는 여기 없다 (패키지로 이동)

**런타임이 읽는 규칙 4개는 `open_proxy_mcp/data/laws/` 에 있다.** 이 폴더에는
사람이 읽는 문서와 `corpus/`(법령 원문·인덱스)만 남는다.

| 파일 | 지금 위치 | 왜 |
|---|---|---|
| `law_provisions.json` · `law_layer_rules.json` · `law_lookup_synonyms.json` | **`open_proxy_mcp/data/laws/`** | 코드와 함께 배포돼야 한다 |
| `corpus/` (법률·시행령 원문 + 인덱스) | `wiki/rules/laws/corpus/` (여기) | 11MB. 휠에 싣지 않고 Dockerfile 이 복사 |
| `상법-2025-2026-종합.md` · 이 README | `wiki/rules/laws/` (여기) | 사람이 읽는 문서 |

**왜 옮겼나**: 종전에는 `wiki/` 를 **경로로** 찾아갔고, 배포 이미지에 wiki 가 들어가는 것은
「Dockerfile COPY 한 줄 + 작업 디렉터리 + 실행 방식」 세 우연의 곱이었다. 하나만 어긋나면
**룰 40개가 조용히 0개**가 되고 강행규정 판정이 통째로 사라지는데 경고도 로그도 없었다.
이제 `importlib.resources` 로 읽어 작업 디렉터리에 무관하고, 실패하면 로그가 남으며,
`/health` 의 `data.law_rules` 로 밖에서 개수를 볼 수 있다(0 이면 `status: degraded`).

## 핵심 master 파일

| 파일 | 종류 | 용도 |
|---|---|---|
| **`law_provisions.json`** (패키지) | 원본(SSOT) | 조항 대장(9개). 조문번호·**시행일·공포일**·유예도래일(obligation_date)·적용대상 티어(scope)·최초주총 적용(first_agm_trigger)의 유일 출처. md 표 자동생성 + 엔진 날짜 검증의 기준 |
| **`상법-2025-2026-종합.md`** | 사람 가독 | 1·2·3차 상법 개정 + 정관 우회 시나리오 + catalog. **유일 master**. '시행 타임라인' 표는 원본에서 자동생성(AUTOGEN 마커 — 직접 수정 금지) |
| **`law_layer_rules.json`** (패키지) | 머신리더블 | proxy_advise._law_layer 직접 로드. 실제 개수는 `/health` 의 `data.law_rules` 로 본다 — 여기 숫자를 적으면 손으로 맞춰야 하고, 260814 에 docstring 이 「36 룰」인 채 실물은 40룰로 갈라져 있었다. 각 룰의 `provision` 필드가 원본 조항을 가리킴 |

## 사용 흐름

### 분석가 / LLM (사람)
→ `상법-2025-2026-종합.md` 읽기 (시행 일정 + 적용 대상 + catalog)

### 코드 (proxy_advise)
→ `law_layer_rules.json` 로드 + 룰 sequential evaluation

### 시행일 변경 시 (SSOT 흐름 — 한 곳만 고친다)
1. `law_provisions.json`(원본)에서 해당 조항의 날짜 수정
2. `python3 scripts/gen_law_timeline.py` 실행 → 종합.md의 시행 타임라인 표 자동 갱신
3. 엔진 발화 시점(`law_layer_rules.json`의 `applies_after`)을 바꿔야 하면 거기서 수정
4. `python3 scripts/wiki_lint.py --strict` → 검사[7]이 원본↔md표↔엔진 3자 정합 확인
   (날짜를 한 곳만 고쳐 나머지가 어긋나면 CI 실패)

### 룰 패턴/판단 변경 시 (날짜 외)
→ `law_layer_rules.json` JSON 수정 (코드 변경 X)

## 엔진 적용 기준일

proxy_advise는 강행규정 적용 여부를 **오늘이 아니라 '이 주총'의 주총일** 기준으로 판단한다
(`build_proxy_advise_payload`의 `law_gate_iso` = 소집공고 notice.datetime 파싱, 미파싱 시 today 폴백).
`applies_after`의 layer 의미는 유지 — **A1(정합)=공포일부터 조기 보상 / A2(위반)=시행일부터**.

`first_agm_trigger`(집중투표 §542의7③)는 부칙 적용례상 "시행 이후 최초 이사선임 주총부터"이나, 엔진은
개별 주총 1건만 보므로 "최초 여부"를 판정할 수 없다 → **주총일 ≥ 시행일로 근사**한다(레지스트리에
플래그로 기록, 정밀 판정은 미구현). 이 한계는 의도적이며 문서화 대상.

## 근거 조문·시행령 표시

proxy_advise가 띄우는 근거(`law_reference`)는 **정확한 조문번호 + 시행령 임계 출처**를 담는다.
- 조문: 예 "상법 제542조의7제3항". lint **[7d]**가 원본(`article`)의 조문이 근거에 실재하는지 강제
  → "N차 개정"만 적고 조문 빠지면 CI 실패.
- 시행령: 자산·자본 임계는 법이 아니라 **대통령령(상법 시행령)**이 정한다(예 "자산 2조"=시행령 §33
  집중투표·§37① 감사위·§36 상근감사). 레지스트리 `threshold_decree`에 출처를 두고 근거에 병기.
- 타법: 상호주=**공정거래법 §21(상호출자제한기업집단)**, 대량보유=**자본시장법 §147①(5% 보고)** — 각
  법+시행령을 근거에 명시(참조 룰 B1-6·C-3 2건, 상법 SSOT 가드 대상 아님 — 안정 참조).

## 원문 대조 검증 — legalize-kr

SSOT(`law_provisions.json`)의 조문번호·시행일이 **권위 원문**과 실제 일치하는지 자동 대조한다.
에이전트 웹추정·수기 편집 drift 차단 + 엔진이 인용하는 조문의 SSOT 누락 감지.

- **원본**: [[reference_legalize_kr_law_corpus]] (github.com/MarcoYou/legalize-kr, `kr/상법/법률.md`·`시행령.md` — 본문 + 부칙 전문).
- **도구**: `scripts/verify_law_against_corpus.py --corpus <경로>` (또는 `OPM_LEGALIZE_KR` 환경변수).
  corpus 미지정·부재 시 **graceful-skip**(종료 0). 9개 조항 조문+시행일
  원문 대조 + `REFERENCE_PROVISIONS`(엔진 인용·개정대장 밖 조문) 존재 확인.
- **정책 — 참조 조문은 개정대장에 안 넣는다**: `law_provisions.json`은 **상법 2025–2026 *개정* 대장**이다.
  안정 상시 조문(예 **상법 시행령 §34조5항7호** — 사외이사 재직기간 결격 동일회사 6년/계열 9년, 2020.1.29
  개정, proxy_advise 장기연임이 인용)은 개정대장에 넣으면 3자 정합[7]을 오염시키므로 **넣지 않고**, verifier의
  `REFERENCE_PROVISIONS`로 원문 존재만 확인한다. 코드는 그 조문을 정밀 인용(§34조5항7호, 원문 대조 확정).

## 옛 분산 자료 (archive)

`wiki/archive/laws/`에 보존:
- `상법개정-2025-2026-통합본.md` — 종합본의 1차/2차/3차 부분
- `상법개정-타임라인-2026.md` — 옛 타임라인 (M레거시 리서치 출처)
- `정관-우회-시나리오-2026.md` — 종합본의 우회 catalog 부분
- `주총방어-시나리오-4가지.md` — 출처 인용용 (M레거시 리서치)
- `주총체크리스트-2026.md` — 출처 인용용

## 관련 페이지

- [[상법-2025-2026-종합]] (master)
- `law_layer_rules.json` (master)
- 260508_0200_decision_law-layer (decision — 도입)
- 260508_0700_decision_law-layer-precision (decision — 정밀화)
- open-proxy-guideline (OPM 5 기준 + voting_rules 12 카테고리)

## 신규 자료 추가 시

1. **법령 자료**: `상법-2025-2026-종합.md` 본문에 추가 (또는 새 master 페이지)
2. **코드 룰**: `law_layer_rules.json`에 항목 추가
3. **출처 reference 자료**: archive에 추가 + 본 README link

→ 신규 분산 X. 항상 master 1개 + archive (보존).
