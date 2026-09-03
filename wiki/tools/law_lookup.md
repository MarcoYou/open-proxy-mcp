---
type: tool
title: law_lookup
domain: reference
scope: [단일 조회]
data_source: [legalize-kr 원문(상법·자본시장법·공정거래법·외부감사법 + 지배구조법·상증세법·금융지주회사법·금산법·은행법·보험업법, 각 법률+시행령 — 260902 10법), open_proxy_mcp/data/laws/{law_layer_rules,law_provisions,law_lookup_synonyms}.json(260814 패키지로 이동)]
related_disclosures: [주주총회소집공고]
related_concepts: [정관변경, 집중투표, 감사위원-의결권-제한, 5%-대량보유]
created: 2026-07-13
updated: 2026-09-02
---

# law_lookup — 정관↔법령 양방향 조회

## 한 줄 요약
**정관 조항·키워드·두루뭉술한 표현 → 관련 법령 조문**(전문 포함), 또는 **법령 조문번호·키워드 →
조문 전문 + 관련 정관 변경유형·우회 시나리오·주총 안건신호**. 회사·DART 무관, **API 호출 0**.
`proxy_advise`가 "이 회사 이 주총 이 안건" 판단이라면, 이건 회사 맥락 없이 **법령 자체를 묻는** 조회기.

## 왜 이 tool 이 따로 있나
법령 데이터는 `proxy_advise`의 판단 엔진(`_law_layer`, 40룰) 안에 묻혀 있으면 「이 정관 조항이 무슨
법이랑 엮여?」·「자본시장법 §147 전문 보여줘」 같은 **회사 맥락 없는 법령 질의**에 답할 수 없다.
이 tool 은 정관↔법령을 양방향으로, 키워드·두루뭉술 질의로, 1:1·N:1·1:N·N:N 카디널리티로 잇는다.

## 데이터 소스
- **corpus**: `wiki/rules/laws/corpus/` — legalize-kr(**github.com/legalize-kr/legalize-kr** — 원본.
  260817 이전에는 포크 `MarcoYou/legalize-kr` 를 봤는데 7-02 에 멈춰 6주간 헛돌았다) 원문을
  `scripts/sync_law_corpus.py`가 vendored 복사 + 조 단위 인덱스(`law_index.json`) + **조문 전문 형태소
  BM25 인덱스(`law_bm25.json`, Signal C)** + 재현성 manifest.
  범위 = 거버넌스 핵심 4법(상법·자본시장법·공정거래법·외부감사법) + 260902 확장 6법(금융회사 지배구조법·
  상증세법·금융지주회사법·금산법·은행법·보험업법), 각 법률+시행령 → **10법 20파일 3,949조**.
  확장 6법은 질의가 그 영역을 가리키는 말(금융회사·은행·보험·지주·상속·증여·적기시정조치 등)을 담고
  있을 때만 4법과 동등하게 겨루고, 아니면 감점(0.35)해 4법을 앞세운다 — 넓힌 대가로 옛 질의 정확도가
  떨어지는 것을 막는 장치(`_law_prior`). 부정문(「금융회사 아닌」)·합성어(「배상책임보험」)는 cue 로 치지
  않는다. 조문 제목이 질의 용어를 전부 담으면 확장 6법에만 +0.25(`_title_anchor_bonus`) — 4법은 bridge
  룰이 받쳐 주므로 주지 않는다(주면 한 단어 질의가 전부 강매칭이 된다).
- **자동 갱신**: 원문 소스 legalize-kr은 국가법령정보센터를 **매일** 따라가는 살아있는 창고.
  OPM corpus는 그 스냅샷이라 자동으로 안 따라감 → `.github/workflows/law-corpus-weekly.yml`이 **매주
  월요일** 재복사 + 색인 재생성. **10법 안의 개정·삭제·신설 조문은 자동 반영**(파서가 삭제/개정 마커
  인식). 새 '법' 추가만 수동(`sync_law_corpus.py`의 `TARGETS` 한 줄). DART 0콜·secrets 0(public repo).
  **결정성**: 색인은 `synced_at` 미포함 + df 키정렬 + tokenizer 타이브레이크 `(-len, surface)`로
  해시시드 무관 바이트 동일 → 내용 무변화 시 커밋 안 함(가짜 커밋 방지). 자료 기준일은 `corpus_freshness()`
  로 출력에 표시(`data.corpus_asof`), 30일 초과 시 안내 경고.
- 🔴 **온전성 게이트**(`scripts/check_law_corpus_integrity.py`, 260817 신설): 재복사 뒤 **조문이 온전한가**를
  묻고 아니면 **커밋을 막는다**. 계기 — 원문이 자본시장법·공정거래법 법률에서 목(가./나./다.)을 통째로
  잃은 채 갱신됐는데 기존 게이트 셋이 전부 통과시켰다(조문 수 137→137·SSOT 는 번호만 대조·공포일은 최신).
  **셋 다 「있나」만 물었다.**
  **부작용을 알고 있어야 한다** — 원문이 불량인 동안 배치는 **매주 빨간불로 실패**하고 corpus 는 낡은 채
  머문다. 이건 고장이 아니라 **의도된 보류**다. 실패한 run 의 Summary 에 무엇이 왜 막혔는지 찍힌다.
  원문이 고쳐지면 다음 주 배치가 저절로 통과해 들어온다.
- **bridge**: `law_layer_rules.json`(40룰) — 정관 변경패턴 ↔ 조문. `_agenda_pattern_match` 재사용.
- **어휘**: `law_lookup_synonyms.json` — 폐쇄 도메인 어휘·동의어(등가만)·날짜게이트·false-friend guard
  (E/B 신호·폴백 분류·guard 전용. Signal C는 260714부터 이 폐쇄어휘 대신 형태소 BM25를 씀).
- **형태소 분석기**: `kiwipiepy`(순수 C++·오프라인·결정적, Java 불필요) — 런타임 질의 토큰화 + sync
  BM25 인덱스 빌드가 **동일 `morph_tokens`**(law_lookup.py)로 정합. 미설치 시 C는 폐쇄어휘 legacy로 degrade.

## 사용법
`law_lookup(query, direction="auto", law="", as_of="", include_full_text=True, top_k=10, format="md")`
- `direction`: `auto`(기본) · `clause_to_law`(정관/키워드→법) · `law_to_clause`(법조문/키워드→정관·안건)
- `law`: 필터 `""`(전체) · 상법 · 자본시장법 · 공정거래법 · 외부감사법 · 지배구조법 · 상증세법 · 금융지주회사법 · 금산법 · 은행법 · 보험업법 (시행령 포함)
- `top_k`: 표시 후보 수(기본 10). 전문은 **강매칭(score ≥ 0.60) 전부 + 최소 3건**까지만 붙고 그 아래 꼬리는 표로만 남는다(`data.full_text_limited_to`) — exact 여도 「시정조치」처럼 글자만 겹친 다른 법 조문 전문이 딸려 나오지 않게(260902).
- `as_of`: 기준일(기본 오늘) — 시행/미시행·명칭변경(사외이사↔독립이사) 게이팅
- `include_full_text`: 조문 원문 전문 포함(기본 True)

## 매칭 (3신호 융합 — 보수적)
| 신호 | 내용 | conf |
|---|---|---|
| **E** exact | 조문번호 **튜플 매칭**(제12조≠제12조의2, 상법§147≠자본시장법§147) — substring 금지 | 1.0 |
| **B** bridge | 40룰 `_agenda_pattern_match` → provision FK·law_reference 조문 | 0.9 (C 0.6) |
| **C** corpus | **조문 전문 형태소 BM25**(k1=1.5·b=0.75, max정규화 상위 20)·**형태소 anchor 게이트** | 0.5·norm |

`score = 1.0·E + 0.9·B + 0.5·C`, 신호 있는 후보 **전부 랭킹**(N:N) → top_k 슬라이스. E>B>C 가중치라
exact/bridge가 corpus 위에 랭크(정밀도 보존). **anchor 게이트**(두루뭉술 차단): 아는 형태소 ≥2개
또는 희소 형태소(df≤rare) 1개일 때만 C emit — 단일 흔한 단어('이사'·'회사')는 `requires_review`.
difflib 없음. 삭제 조문 보존+경고, 조문번호 법령 중복 → `ambiguous`.

**Signal C 의 질의확장층**: 자유질의는 자연어 패러프레이즈가 법률 어휘와 겹치지 않아(번 돈≠이익 ·
쪼개다≠분할 · 일감몰아주기≠부당지원) 형태소가 통째로 비는 일이 잦다. 그래서 형태소 BM25 앞에
**질의확장층**을 둔다 — `law_lookup_synonyms.json` 의 `bm25_query_expansions`(구어 trigger → 법률
형태소)를 anchor 게이트 **전에** 적용한다. 회귀 게이트는 `scripts/law_recall_harness.py` ·
`scripts/spot_law_lookup.py`(vague·guard·collision·bridge 무회귀 하드게이트).

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

## 성능 (260714)
인덱스(`law_index.json`·`law_bm25.json`)·synonyms·fulltext는 전역캐시 — 프로세스당 1회 로드. Signal C
형태소화에 **kiwipiepy**를 쓰는데 init이 무거워 **lazy 싱글턴**(첫 질의만 `cold ~1.1s`, kiwi 모델 로드 포함).
이후 **warm ~1.3ms/query**(측정 당시 2,599조 스코어링 기준 — 260902 10법 3,949조로 늘었으나 여전히 ms 단위, DART 0콜). `data.timing_ms.build`로
관측. 실측 new-tools-perf-profiling-260714.

## 폴백 유형 (검색을 올바른 방향으로 유도 — 260713)
강한 매칭(E/B/강C)이 아닐 때 **왜 안 잡혔는지**를 유형화하고 **유형별 안내 문구+다음 행동**을 준다
(generic 메시지 대신). `data.fallback = {type, message}`, 문구는 `warnings[0]`, 행동은 `next_actions`.
우선순위 순:

| type | 언제 | 안내 방향 |
|---|---|---|
| `law_collision` | 조문번호가 여러 법에 존재(법령 미지정) | `law=` 로 법령 지정 |
| `article_not_found` | 조문번호 줬으나 원문에 없음 | 번호 확인 · 범위 밖일 수 있음 · 키워드로 |
| `out_of_corpus_topic` | 10법 밖 주제 — 개별 제도(차등의결권=벤처기업육성법 등)·거래소 자율규정·아직 안 읽은 법률(법인세법·소득세법·신탁법·여신전문금융업법 등) | 근거 법령 안내 + 인접 주제, 조문은 붙이지 않음 |
| `filtered_empty` | `law=` 필터 안 결과 0 | 필터 빼거나 다른 법 |
| `too_vague` | 인식 키워드 0 | 조문번호·도메인 용어 넣기 |
| `too_generic` | 형태소 있으나 anchor(≥2 or 희소) 미달 (이사·주식 등 단일 흔한 단어) | 구체적 조합·조문번호 |
| `deleted_only` | 매칭이 전부 삭제 조문 | 개정 후 대체 조문 |
| `weak_match` | 약한 후보만(강매치 없음) | 조문번호 직접·용어 구체화 |
| `no_match` | 그 외 0매칭 | 용어 변경·조문번호 |

범위 밖 주제 맵은 `law_lookup_synonyms.json`의 `out_of_corpus`(큐레이션 SSOT — 코드 하드코드 X).
`unknown_law_filter`(지원 안 되는 `law=`)는 결과 유무와 무관하게 항상 별도 경고.

## 검증
`scripts/spot_law_lookup.py` 가 하드게이트로 검사한다 — 원문 정합 · bridge recall(양방향) ·
조문번호 collision · 두루뭉술 차단 · false-friend guard · 삭제 조문 탐지 · 법률>시행령 tier ·
미시행 유보 회귀 · 폴백 유형 분류 · `proxy_advise` 공유자산 회귀.

**적대적 검증에서 드러난 함정 4종**(전부 수정 완료): ① corpus 포맷의 삭제 마커를 못 읽으면 삭제
조문 탐지가 통째로 0 이 된다 ② false-friend 마스킹이 복합어에서 누출된다(감사보고서→감사) ③ 조사가
붙은 복합어는 그대로는 매칭되지 않는다(이사의 보수→§388) ④ 법률과 시행령을 구분하지 않으면 시행령이
법률 위로 올라온다. 검증 실측치 상세는 private storage.

## 알려진 한계 / TODO (v1)
- **terse-title 조문 recall**: 제목이 일반어인 핵심 조문(예 상법 §385 "해임", 보수'한도' vs 조문의
  '보수')은 키워드 매칭이 약함 — 조문번호 직접 지정이나 bridge로는 잡히나 자유질의는 miss 가능.
  향후 핵심 AGM 조문 curated title→token 오버라이드로 보강(opm-enhance 대상).
- **date-gate 사외이사↔독립이사**: 2026-07-23 이전엔 별개 → 그 전 "사외이사" 질의가 §542의8(독립이사)
  을 miss. 의도된 동작이나 사용자 관점 recall hole — 명칭변경 전 soft-link 검토.
- **corpus 미수록 법령**: 차등의결권·복수의결권(벤처기업육성법), 법인세법·소득세법·신탁법·여신전문금융업법 등 10법 밖 주제는 미수록 — `_OUT_OF_CORPUS_STATUTES`·사전 `out_of_corpus` 로 「범위 밖」 안내만 한다. 넣으면 그 표에서 지운다.

## 관련 페이지
- [[proxy_advise_before_meeting]] — 회사 주총 안건 판단(정관 본문↔안건). law_lookup은 그 하위 법령 지식 조회.
- [[rules/laws/README]] — 법령 자료 입구(SSOT·bridge·corpus)
- [[상법-2025-2026-종합]] — 상법 개정 사람 가독 master
- 신설·후속 회고와 v2 로드맵: private storage `wiki-private/lessons/`
