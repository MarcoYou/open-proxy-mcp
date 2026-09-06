# 260906 — 공시 원문 절 단위 리소스(`toc`·`section`) 실측 핸드오프

**상태**: 설계 검토·리모트 실험까지 완료, **뷰어 실측은 로컬에서 해야 함**(원격 샌드박스가 dart.fss.or.kr 을 403 으로 막는다). 코드 변경 없음(계측 스크립트만 추가).

## 1. 지금까지 확인된 것

### 원문 리소스는 지금 답을 못 준다 (리모트 실측, 삼성전자 2025 사업보고서 `20260310002820`)
| 측정 | 값 |
|---|---|
| document.xml 전체 | 820,256자 |
| `opm://filing/{rcept_no}` 가 준 양 | 120,019자 (15%) — `_MAX_CHARS` 절단 |
| 준 범위 | I. 회사의 개요(454) → II. 사업의 내용(27,183) → III. 재무에 관한 사항(67,458)에서 잘림 |
| 「직원 현황·1인평균 급여」(VIII장) | 잘린 뒤 — 못 닿음 |
| 정제 텍스트에서 잡히는 헤딩 | 장(I·II·III) + 절(`1.`·`가.`) 175개 |

→ 파서 없는 질문 대부분이 III장 이후에 있고 거기가 통째로 잘린다. 답을 못 찾으면서 12만 자를 소비하는 구조.

### 절 단위 읽기의 밑바닥은 이미 있다 (`open_proxy_mcp/dart/client.py`)
- `_fetch_viewer_main_html(rcept_no)` → DART 뷰어 main.do (웹, 키 미사용, `_throttle_web`)
- `_extract_viewer_nodes(html)` → 뷰어 목차 treeData 에서 `text·tocNo·eleId·dcmNo` 절 목록
- `_fetch_viewer_section_html(node)` → 절 하나의 HTML
- `get_viewer_document(rcept_no, section_keywords)` → 키워드로 절 골라 text+html+nodes, `_doc_cache` 캐시
- 쓰는 곳: `services/business_details.py`(II. 사업의 내용 하위 절), `tools/financial_notes.py`(주석 절). 둘 다 특정 절 고정 내부 호출. **아무 공시의 아무 절**을 밖에서 고르는 창구가 없다.

### 260813 「Claude.ai 커넥터는 resource 를 모델에게 노출하지 않는다」는 뒤집혔다
이 세션(claude.ai 원격 커넥터)에서 `opm://tools_guide` 를 URI 로 직접 읽어 전문이 돌아왔다. 단 `resources/list` 는 커넥터가 접속 시점 스냅샷을 잡아 새 리소스가 목록에 안 보인다 — **URI 를 아는 경우에만 읽힌다.** 따라서 「도구 결과가 URI 를 글자로 적어 주고 그걸 읽는」 경로가 실제로 작동하는 경로다.
정정 대상: `wiki/tools/proxy_guideline.md:26` · `wiki/decisions/open-proxy-guideline.md:40` · `docs/RELEASE_NOTES.md:122`(ENG 135).

### 프롬프트 `claim_check` 는 보류 권고
「삼성전자 정규배당 연 9.8조 정책대로 집행」 주장을 기존 `dividend_disclosure` 출력만으로 지지(비고 원문 9.8조·분기 2.45조)/반박(1.3조 추가해 총 11.11조)/확인 불가(1.3조의 성격) 로 가를 수 있었다. 프롬프트가 보태는 건 데이터가 아니라 규율이고, 그건 서버 instructions 한 줄로 된다. 이 세션 커넥터엔 프롬프트를 고를 UI 도 없다.

## 2. 로컬에서 할 것 — 뷰어 실측

```bash
# 표본 절만(직원·주석·사업의 내용·배당·주주) — 절당 1~2초
uv run python scripts/probe_viewer_sections.py 20260310002820
# 전 절 — 절 수 × 1~2초. 대형·중형·금융사 하나씩
uv run python scripts/probe_viewer_sections.py 20260310002820 --all --json /tmp/probe_samsung.json
uv run python scripts/probe_viewer_sections.py <중형사 사업보고서 rcept_no> --all --json /tmp/probe_mid.json
uv run python scripts/probe_viewer_sections.py <은행·보험 사업보고서 rcept_no> --all --json /tmp/probe_fin.json
```
`.env` 에 `OPENDART_API_KEY` 가 있어야 한다(클라이언트 생성자가 요구. 뷰어 호출은 키를 안 쓴다). CLAUDE.md 규칙 7 대로 절을 순서대로 하나씩 받는다 — 병렬 금지.

### 무엇을 볼지
| 항목 | 왜 |
|---|---|
| 절 수 | 목차 리소스 크기. 사업보고서는 40 안팎일 것 |
| 절별 텍스트 글자 수 분포 · 최대(재무제표 주석) | 절 리소스 상한(4만 자 안)과 이어읽기(`from=`) 필요 여부 |
| 절별 HTML 바이트 · `표` 수 | md 표 변환 비용, 단위·각주가 절 안에 같이 오는지 |
| `캐시 B(html+text)` vs `(text)` | 절을 캐시할 때 html 을 버릴지 |
| 전 절 캐시 MB / 96MB | 문서 하나가 캐시 예산에서 차지하는 비율 |
| peak RSS 시작→끝 | 전 절을 순서대로 받았을 때 프로세스가 얼마나 부푸나 |
| 실패 절 | 뷰어가 절 HTML 을 안 주는 유형(첨부·XBRL) |

### 판단 기준(가안)
- 절 평균 4만 자 이하, 주석 절이 그보다 크면 `from=` 이어읽기를 첫 구현에 포함.
- 문서 하나 전 절 캐시가 10MB(예산의 ~10%) 넘으면 절 캐시는 **텍스트만** 저장.
- 절당 왕복 2~3초면 「절 하나씩」 설계로 충분. 그보다 느리면 목차에 절 크기를 미리 실어 선택을 돕는다.

## 3. 메모리 점검(코드 기반, 260906)
- **OOM 이력(260901)은 캐시가 아니라 RSS 였다.** 두 머신이 exit 137 로 죽었을 때 캐시 점유는 296MB 중 33%. VM 1,024MB(fly.toml). 이후 `doc_gate`(문서 수신·파싱 동시 상한), 키별 클라이언트 등록부 상한, `/health` 에 `mem`·`doc_gate` 노출. → 새 리소스는 **반드시 `doc_gate` 를 지나야** 하고, 요청당 피크(뷰어 HTML 파싱)가 관건이다.
- 캐시 계정은 `_cache_entry_bytes`(getsizeof 재귀)라 문자열 지배 페이로드에 정확. 한 항목이 예산(96MB)보다 크면 캐시를 거부하고 로그를 남긴다.
- `get_viewer_document` 는 `text`+`html`+`nodes` 를 같이 캐시한다 — html 이 text 의 2~3배라 **한 문서가 두 벌**로 들어간다. 키워드 없이 부르면 **전 절**을 받아 합친다 → 리소스 경로에서 이 함수를 키워드 없이 호출하면 안 된다.
- `opm://filing/{rcept_no}`(현행)는 `get_document_cached` 로 document.xml 전문을 캐시(82만 자 ≈ 1.6~3MB str)하고 12만 자만 자른다. 메모리보다 **답을 못 주는 것**이 문제.
- 설계 제한: 목차는 `nodes` 만 캐시(수 KB) · 절은 절 단위로 캐시하되 텍스트만 · 절당 텍스트 상한 4만 자 + `from=` · 요청 하나에 절 하나 · 기존 `_throttle_web` 그대로.

## 4. 설계안 (채팅에서 합의된 형태)
```
opm://filing/{rcept_no}                 (있음) 머리에 「목차는 …/toc」 안내를 붙인다
opm://filing/{rcept_no}/toc             신규 — 뷰어 목차 그대로(no=tocNo · 절 제목 · section URI · 크기)
opm://filing/{rcept_no}/section/{no}    신규 — 절 하나. 머리: 상위 장 › 절 제목 · 접수번호 · 글자 수 · 이전/다음 절 URI
                                        본문: 텍스트 + md 표(단위·각주 보존, financial_notes 의 표 변환 재사용)
opm://reading/{topic}                   나중 — 오독 사례 모은 뒤 (basis · absence · segments)
```
도구 → 리소스 연결은 md 본문에 URI 를 글자로 적는다(`business_details` 의 「원문 위치」 줄, 약한 파싱 경고 줄). SDK(mcp 2.0.0)에 `ResourceLink`·`EmbeddedResource` 가 있지만 도구 31개가 전부 str 반환이라 링크 객체는 뒤로 미룬다.

CLAUDE.md 규칙 2(document.xml 우선·뷰어는 service 명시분만)·7(스크래핑 1~2초·배치 금지)은 리소스 PR 에서 「뷰어 절 읽기는 리소스 경로에서 허용, 제한은 §3」로 함께 고친다. CLAUDE.md 전반 정리는 별건(사용자 지시 260906).

## 5. 다음 세션이 할 일
1. §2 실측 3건 돌리고 결과(표 + RSS)를 이 문서 §2 아래에 붙인다.
2. 판단 기준 통과하면 `resources.py` 에 `toc`·`section` 구현 + `tests/`(뷰어 HTML fixture 로 network 0) + `wiki/decisions/mcp-endpoints.md` 갱신 + 260813 기록 3곳 정정.
3. `business_details`·`financial_notes`·`shareholder_meeting_notice`·`asset_holdings` 의 약한 파싱 경고에 절 URI 줄 추가.
4. 다 풀리면 이 문서는 삭제하고 durable 한 부분은 `decisions/mcp-endpoints` 로 옮긴다(handoff README 생명주기).

## 관련
`scripts/probe_viewer_sections.py`(계측) · `open_proxy_mcp/resources.py` · `open_proxy_mcp/dart/client.py:1914-2020` · [[mcp-endpoints]] · [[business_details]] · [[financial_notes]]
