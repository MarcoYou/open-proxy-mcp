# OPM Tool Blueprint

## Tool 체계

```
brief_agm(ticker)
│
├─ search_agm(ticker) ─────────────────── 소집공고 검색 + 정정 태깅
│   └─ rcept_no 획득
│
├─ get_agm_info(rcept_no) ─────────────── 일시/장소/전자투표/정정요약
│
├─ get_agm_agenda(rcept_no) ───────────── 안건 제목 트리
│
└─ get_agm_corrections(rcept_no) ──────── 정정 전/후 비교 (정정공고만)


get_agm_agenda_items(rcept_no, agenda_no)
│   각 안건의 본문 내용 (테이블/텍스트 블록)
│
└─► get_agm_financials(rcept_no)
        │   agenda_items 중 재무제표 블록을 받아서
        │   재무상태표/손익계산서를 정규화
        │   (당기/전기, 단위, 연결/별도)
        │
        └─► (향후) get_agm_ocr(rcept_no, image)
                  이미지 블록 → OCR → 병합
```

## 데이터 흐름

```
DART API
  │
  ▼
get_document(rcept_no)
  │ {text, html, images}
  │ (캐싱: _doc_cache)
  ▼
┌──────────────────────────────────────────────┐
│              parser.py                        │
│                                               │
│  parse_agenda_items(text, html)  → 안건 트리  │
│  parse_meeting_info(text, html)  → 회의 정보  │
│  parse_agenda_details(html)      → 안건 상세  │
│  parse_financial_statements(html)→ 재무제표   │
│  parse_correction_details(html)  → 정정 사항  │
│                                               │
│  모든 파서: bs4(lxml) 우선 → text regex fallback│
└──────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────┐
│           shareholder.py (MCP tools)          │
│                                               │
│  search_agm          → 검색 + 정정 태깅      │
│  get_agm_info        → 회의 정보 + 정정 요약  │
│  get_agm_agenda      → 안건 트리              │
│  get_agm_agenda_items→ 안건 본문 블록         │
│  get_agm_financials  → 재무제표 정규화        │
│  get_agm_corrections → 정정 전/후             │
│  brief_agm           → 오케스트레이션         │
│                                               │
│  포맷: format="md" / format="json"            │
└──────────────────────────────────────────────┘
```

## 체이닝 관계

| Tool | Input | Output | 체이닝 |
|------|-------|--------|--------|
| `search_agm` | ticker | rcept_no 리스트 | → 모든 tool의 시작점 |
| `get_agm_info` | rcept_no | 회의 메타데이터 | ← search 결과 |
| `get_agm_agenda` | rcept_no | 안건 트리 (번호/제목) | ← search 결과 |
| `get_agm_agenda_items` | rcept_no, agenda_no | 안건 본문 블록 | ← agenda 결과로 agenda_no 선택 |
| `get_agm_financials` | rcept_no | 정규화된 재무제표 | ← agenda_items의 재무제표 블록 소비 |
| `get_agm_corrections` | rcept_no | 정정 전/후 | ← search에서 정정공고 발견 시 |
| `brief_agm` | ticker | 종합 브리핑 | search + info + agenda 오케스트레이션 |

## 향후 확장

```
get_agm_agenda_items
  ├─► get_agm_financials        (재무제표 정규화)
  ├─► get_agm_charter_changes   (정관변경 비교표 정규화)
  ├─► get_agm_director_info     (이사 후보자 정보 정규화)
  └─► get_agm_ocr               (이미지 OCR)
```

각 특화 tool은 `agenda_items`의 해당 블록을 input으로 받아 정규화.
범용 블록 → 도메인 특화 정규화의 체이닝 구조.
