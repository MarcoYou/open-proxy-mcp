---
type: tool
title: law_lookup
domain: reference
scope: [단일 조회]
data_source: [legalize-kr 원문(상법·자본시장법·공정거래법·외부감사법 각 법률+시행령), law_layer_rules.json(40룰 bridge), law_provisions.json(상법 개정 조항 SSOT), law_lookup_synonyms.json(큐레이션 어휘·guard)]
related_disclosures: [주주총회소집공고]
related_concepts: [정관변경, 집중투표, 감사위원-의결권-제한, 5%-대량보유]
related_decisions: []
created: 2026-07-13
---

# law_lookup — 정관↔법령 양방향 조회

## 한 줄 요약
**정관 조항·키워드·두루뭉술한 표현 → 관련 법령 조문**(전문 포함), 또는 **법령 조문번호·키워드 →
조문 전문 + 관련 정관 변경유형·우회 시나리오·주총 안건신호**. 회사·DART 무관, **API 호출 0**.
`proxy_advise`가 "이 회사 이 주총 이 안건" 판단이라면, 이건 회사 맥락 없이 **법령 자체를 묻는** 조회기.

## 왜 만들었나 (사용자 260713)
법령 데이터가 `proxy_advise`의 판단 엔진(`_law_layer`, 40룰) 안에만 묻혀 있어 "이 정관 조항이 무슨
법이랑 엮여?" / "자본시장법 §147 전문 보여줘" 같은 **범용 법령 질의에 답하는 독립 tool이 없었다**.
정관↔법령을 두 방향으로, 키워드/두루뭉술 질의로, 1:1·N:1·1:N·N:N 카디널리티로 잇는 조회기가 필요했다.

## 데이터 소스
- **corpus**: `wiki/rules/laws/corpus/` — legalize-kr(github.com/MarcoYou/legalize-kr) 원문을
  `scripts/sync_law_corpus.py`가 vendored 복사 + 조 단위 인덱스(`law_index.json`) + 재현성 manifest.
  v1 범위 = 상법·자본시장법·공정거래법·외부감사법 (각 법률+시행령) **2,725조**.
- **자동 갱신 (260713)**: 원문 소스 legalize-kr은 국가법령정보센터를 **매일** 따라가는 살아있는 창고.
  OPM corpus는 그 스냅샷이라 자동으로 안 따라감 → `.github/workflows/law-corpus-weekly.yml`이 **매주
  월요일** 재복사 + 색인 재생성. **4법 안의 개정·삭제·신설 조문은 자동 반영**(파서가 삭제/개정 마커
  인식). 새 '법' 추가만 수동(`sync_law_corpus.py`의 `TARGETS` 한 줄). DART 0콜·secrets 0(public repo).
  **결정성**: 색인은 `synced_at` 미포함 + df 키정렬 + tokenizer 타이브레이크 `(-len, surface)`로
  해시시드 무관 바이트 동일 → 내용 무변화 시 커밋 안 함(가짜 커밋 방지). 자료 기준일은 `corpus_freshness()`
  로 출력에 표시(`data.corpus_asof`), 30일 초과 시 안내 경고.
- **bridge**: `law_layer_rules.json`(40룰) — 정관 변경패턴 ↔ 조문. `_agenda_pattern_match` 재사용.
- **어휘**: `law_lookup_synonyms.json` — 폐쇄 도메인 어휘·동의어(등가만)·날짜게이트·false-friend guard.

## 사용법
`law_lookup(query, direction="auto", law="", as_of="", include_full_text=True, top_k=10, format="md")`
- `direction`: `auto`(기본) · `clause_to_law`(정관/키워드→법) · `law_to_clause`(법조문/키워드→정관·안건)
- `law`: 필터 `""`(전체) · 상법 · 자본시장법 · 공정거래법 · 외부감사법 (시행령 포함)
- `as_of`: 기준일(기본 오늘) — 시행/미시행·명칭변경(사외이사↔독립이사) 게이팅
- `include_full_text`: 조문 원문 전문 포함(기본 True)

## 매칭 (3신호 융합 — 보수적)
| 신호 | 내용 | conf |
|---|---|---|
| **E** exact | 조문번호 **튜플 매칭**(제12조≠제12조의2, 상법§147≠자본시장법§147) — substring 금지 | 1.0 |
| **B** bridge | 40룰 `_agenda_pattern_match` → provision FK·law_reference 조문 | 0.9 (C 0.6) |
| **C** corpus | 키워드 idf·**anchor 게이트**(두루뭉술 질의 차단) | 0.5·sim |

`score = 1.0·E + 0.9·B + 0.5·C`, τ_emit 이상 **전부 랭킹**(N:N). 폐쇄 어휘·guard로 오탐 차단,
difflib 없음. 삭제 조문 보존+경고, 조문번호 법령 중복 → `ambiguous`.

**미시행 유보 (260713 수정)**: corpus의 `enforcement`는 법 **전문(공포본)** 시행일자라 개별 조문의
현행 여부로 쓰면 안 된다 — 미래 시행 개정본을 vendored하면 그 법 **모든 조문**이 거짓 '미시행'으로
찍힌다(자본시장법 법률 599/599 오탐). 그래서 ① 전문 시행일이 미래면 조문 `in_force`를 **단정(False)하지
않고** `None`(현행 여부 확인필요)로 두고 법령당 1회 버전 경고. ② 진짜 **조문별** 미래시행은
SSOT(`law_provisions.json`)의 `effective_date`로만 `미시행(시행 YYYY-MM-DD)` flag(+first_agm_trigger).

## 추천 질문 (자연어 예시)
| 방향 | 이럴 때 |
|---|---|
| `clause_to_law` | "집중투표 배제 조항 삭제하면 무슨 법 위반?" · "감사위원 분리선출 근거 조문" · "상호출자 금지 조문" |
| `law_to_clause` | "제542조의8 뭐야·전문 보여줘" · "자본시장법 제147조" · "제341조의4 관련 정관 변경유형" |

## status
`exact`(정확/bridge/강매치) · `ambiguous`(조문번호 법령 중복·중간대 다수) · `requires_review`(두루뭉술) ·
`partial`(앵커 있으나 매칭 0) · `error`(인덱스 부재).

## 폴백 유형 (검색을 올바른 방향으로 유도 — 260713)
강한 매칭(E/B/강C)이 아닐 때 **왜 안 잡혔는지**를 유형화하고 **유형별 안내 문구+다음 행동**을 준다
(generic 메시지 대신). `data.fallback = {type, message}`, 문구는 `warnings[0]`, 행동은 `next_actions`.
우선순위 순:

| type | 언제 | 안내 방향 |
|---|---|---|
| `law_collision` | 조문번호가 여러 법에 존재(법령 미지정) | `law=` 로 법령 지정 |
| `article_not_found` | 조문번호 줬으나 원문에 없음 | 번호 확인 · 범위 밖일 수 있음 · 키워드로 |
| `out_of_corpus_topic` | 4법 밖 주제(차등의결권=벤처기업육성법 등) | 근거 법령 안내 + 인접 주제 |
| `filtered_empty` | `law=` 필터 안 결과 0 | 필터 빼거나 다른 법 |
| `too_vague` | 인식 키워드 0 | 조문번호·도메인 용어 넣기 |
| `too_generic` | 토큰 있으나 앵커(희소) 0 (이사·주식 등) | 구체적 조합·조문번호 |
| `deleted_only` | 매칭이 전부 삭제 조문 | 개정 후 대체 조문 |
| `weak_match` | 약한 후보만(강매치 없음) | 조문번호 직접·용어 구체화 |
| `no_match` | 그 외 0매칭 | 용어 변경·조문번호 |

범위 밖 주제 맵은 `law_lookup_synonyms.json`의 `out_of_corpus`(큐레이션 SSOT — 코드 하드코드 X).
`unknown_law_filter`(지원 안 되는 `law=`)는 결과 유무와 무관하게 항상 별도 경고.

## 검증
`scripts/spot_law_lookup.py` — 원문 정합(2,725조 0오류)·recall(bridge 방향B 18/18·방향A 16/16)·
collision·두루뭉술 차단·false-friend guard·**삭제 조문 탐지(126건)**·**법률>시행령 tier**·**미시행 유보
(자본시장법 599 오탐 회귀)**·**폴백 6유형 분류**·proxy_advise 공유자산 회귀. 전 축 PASS(260713).

**멀티에이전트 적대적 검증(260713)으로 적발·수정**: ① 삭제 조문 탐지 0/2725 회귀(corpus 포맷
`삭제 <날짜>` 미인식) → 수정 후 126건 탐지. ② false-friend 복합어 누출(감사보고서→감사) → 통합
longest-first 마스킹으로 차단. ③ 조사 분리 복합어 recall(이사의 보수→§388) → de-particle 매칭.
④ 법률 vs 시행령 미구분 → law_tier tie-break. (파싱정합 + 도메인 relevance + 엣지케이스 3관점 병렬)

## 알려진 한계 / TODO (v1)
- **terse-title 조문 recall**: 제목이 일반어인 핵심 조문(예 상법 §385 "해임", 보수'한도' vs 조문의
  '보수')은 키워드 매칭이 약함 — 조문번호 직접 지정이나 bridge로는 잡히나 자유질의는 miss 가능.
  향후 핵심 AGM 조문 curated title→token 오버라이드로 보강(opm-enhance 대상).
- **date-gate 사외이사↔독립이사**: 2026-07-23 이전엔 별개 → 그 전 "사외이사" 질의가 §542의8(독립이사)
  을 miss. 의도된 동작이나 사용자 관점 recall hole — 명칭변경 전 soft-link 검토.
- **corpus 미수록 법령**: 차등의결권·복수의결권(벤처기업육성법) 등 8법 밖 주제는 미수록.

## 관련 페이지
- [[proxy_advise_before_meeting]] — 회사 주총 안건 판단(정관 본문↔안건). law_lookup은 그 하위 법령 지식 조회.
- [[rules/laws/README]] — 법령 자료 입구(SSOT·bridge·corpus)
- [[상법-2025-2026-종합]] — 상법 개정 사람 가독 master
- [[law-lookup-260713]] — 신설·후속 회고(미시행 유보·폴백·자동갱신·결정성 발견) + v2 로드맵
