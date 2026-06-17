# sample_universe

전수조사·스크리닝용 상장사 유니버스 스냅샷.

## 파일
- **`general_universe.xlsx`** — 코스피(KS)·코스닥(KQ) 현 상장사 + 지표·식별자. 리서치 터미널 export.
  - 헤더는 **13행**, 데이터 **14행~** (상단은 배너).
  - 주요 컬럼: `Code`(A+6자리)·`Name`·`결산월`·`시가총액`(억)·**`시장구분`(KS=코스피, KQ=코스닥)**·
    자산총계·자본총계(지배)·매출(TTM)·영업이익(TTM)·순이익(지배,TTM)·WI26업종·최대주주지분율·고배당여부.
  - 스냅샷(파일 상단 `Last Update` 참조) — 갱신 시 같은 위치 덮어쓰기.
- **`_ks_top100.json` / `_kq_top100.json`** — xlsx에서 시총 상위 100사 추출(name·code·mcap). 아래 census 입력.

## 이 유니버스를 쓰는 스크립트 (scripts/)
- `coheld_quality_census.py` — 5% 합계표 파서 품질(공동보유자) 전수, 분쟁 엣지 포함.
- `comp_limit_census.py` — 코스피 상위 100사 이사 보수한도 상향/유지/하향 통계.
- `shareholder_proposal_census.py` — 코스닥 상위 100사 주주제안 탐지.
- `treasury_multitype_census.py` / `treasury_kosdaq_naming.py` — 자사주 종류주식(보통/종류) 전수.

raw 결과(JSON)는 `wiki/architecture/audits/data/260*_*.json`에 보존. 위 `_*_top100.json`만 입력으로 커밋.
