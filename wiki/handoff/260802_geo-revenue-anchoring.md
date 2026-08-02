# 260802 핸드오프 — 지역별 수익 앵커링 · 세션 재시작 후 확인

> 이 세션에서 **끝난 것은 여기 안 적는다**(전부 코드·wiki·CLAUDE.md·private lesson에 이관 완료).
> 아래는 **재시작 직후 확인할 것**과 **아직 안 한 것**만.

## 0. 재시작 직후 바로 확인 (2분)

세션 설정을 바꿨는데 **재시작해야 반영**된다. 새 세션에서:

```bash
# ① 좀비 stdio MCP 가 안 뜨는지 — 떠 있으면 설정이 아직 안 먹은 것
ps aux | grep "python -m open_proxy_mcp" | grep -v grep | grep -v streamable-http
```

- **아무것도 없어야 정상.** 이전엔 세션마다 stdio 서버가 뜨고 **그 시점 코드를 메모리에
  붙들어**, 코드를 고쳐도 MCP 도구는 계속 옛 결과를 냈다(260802에 이 사고를 확인·정리).
- MCP 도구 이름이 `mcp__live-opm__*` / `mcp__pilot-opm__*` 로 보이면 반영된 것.
  `mcp__open-proxy-mcp__*` 가 남아 있으면 아직 옛 설정이다.

```bash
# ② pilot(로컬) 띄우고 한 건 확인 — preview_start(name="pilot-opm") 후
curl -s -X POST "http://127.0.0.1:8000/mcp?opendart=<키는 .env 에서>" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"business_details","arguments":{"company":"현대차","fields":"geo_revenue","reprt_code":"11011","bsns_year":"2025"}}}'
```

기대: **해외 매출 비중 70.3%** · 5개 지역 · `원문 위치 … 37. 부문정보 (연결) [NT_C_D871100]`
· `II 매출실적표 (별도 기준) — 수출 92.73조원`.

## 1. 남은 작업 — 지역별 수익

프로덕션 소스 79건 기준 **검출 49% · 추출 실패 0건**까지 왔다. 나머지는 대부분 진짜 미공시다.
아직 안 한 것:

- **`outside_segment_note` 1건(키움증권)** — 지역 표지가 부문 주석 **밖**에 있다. 지금은
  문서 전체 폴백으로 흘러가 절 경계가 불명확하다. 어느 절인지 지목하는 로직 필요.
- **`assets_by_region` 부분 포착** — LG화학이 「한국」만 잡힌다(원문엔 전 지역 있음).
  행 지향 리더의 자산 열 인덱스 매칭이 첫 열만 본다. 수출형/현지생산형 판별에 쓰려면
  전 지역이 필요하다.
- **`ii_export_domestic` 는 아직 `geo_revenue` 안에만 있다.** `revenue_breakdown`(매출 분해
  단일 진입점)에도 노출할지 결정 필요 — 지금은 두 곳에서 매출 축을 봐야 한다.
- **회사별 절 위치 학습 미적용** — 절 번호·상대위치가 회사별로 안정적임을 실측했으나
  (분기·반기는 번호까지 동일, 사업만 밀림 / 상대위치 68·68·68%) 아직 안 쓴다.
  적용하려면 절 맵 캐시가 필요하고, 그건 아래 2번과 묶인다.

## 2. 설계 미결 — 절 맵 적재 (사용자 판단 필요)

주석 절 목록을 쌓아두면 ① 사전 자동 확장 ② **드리프트 감지**(회사가 서식을 바꾸면 지금은
조용히 실패한다) ③ 회사별 위치 힌트가 된다. **속도 이득은 거의 없다**(절 추출은 이미 받은
html에 정규식 한 번). 가치는 드리프트 감지다.

제안했던 방식: **정상 트래픽에 얹기** — `business_details` 호출 때 이미 문서를 받으므로
그때 절 목록을 함께 적재하면 **추가 DART 콜 0**. 인프라는 `tool_call_events`(Postgres) 재사용.

미결: 프로젝트 규칙 「사용자 조회 결과 저장 안 함」과의 관계. 이건 조회 결과가 아니라
**문서의 구조 메타데이터**라 corp-code·document 캐시와 같은 인프라 예외로 볼 수 있으나,
**판단은 사용자 몫**이다.

## 3. 이어서 볼 만한 것 (우선순위 낮음)

- **`§382③ 사외이사 결격 체크**: 33사에서 진짜 적발 0건. 유지할지 뺄지 미결
  (표본을 크기가 아니라 **성격**으로 넓혀 재확인 필요).
- **취임연령 30세 게이트**가 오너가 조기선임을 막는다(양홍석 26세·이건영 29세·박은경 27세).
  법인등기부 DB화 후 보정 예정 — 사용자가 「나중에」로 유보.
- **`revenue_breakdown` 의 `by_segment`/`by_product` 와 `geo_revenue` 의 관계 정리** —
  지금 매출 축이 세 군데(부문·제품·지역)에 흩어져 있고 `ii_export_domestic` 까지 붙었다.

## 참고 — 이 세션에서 확정된 것 (읽을 위치)

| 무엇 | 어디 |
|---|---|
| 지역별 수익 전체 이력·수치 | `wiki/tools/business_details.md` 실사용 검증 절 |
| 회귀 입력 경계 규칙(사고 재발 방지) | `CLAUDE.md` 작업원칙 2 + `tests/test_production_source_boundary.py` |
| live-opm / pilot-opm 운영 규칙 | `CLAUDE.md` 셋업·개발 절 |
| 260731 측정 사고 회고 | private `wiki-private/lessons/measurement-input-boundary-260731.md` |
