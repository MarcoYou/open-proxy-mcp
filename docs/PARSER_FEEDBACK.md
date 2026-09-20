# 인사 후보 파서의 지속 회귀 평가

이 하네스는 **고정된 공시 원문과 독립 판독한 정답을 비교하는 검사기**입니다.
판독자가 사람인지 에이전트인지 검토 기록에 밝힙니다. 에이전트 검토는 도메인 전문가의 승인을 대신하지 않습니다.
정답을 파서 출력에서 만들거나 자동으로 고치지 않습니다. 파서 개선·자동 병합·배포도 하지 않습니다.
실행기는 [parser_feedback.py](../scripts/parser_feedback.py), 운영 파서의 설명은
[주총 소집공고](../wiki/tools/shareholder_meeting_notice.md), 원문 보존 원칙은
[CLAUDE.md](../CLAUDE.md)를 참고하세요.

## 먼저 알아둘 한계

- 현재 평가 범위는 `action`이 `선임`·`재선임`·`중임`·`연임`인 안건의 후보표 출현입니다. 비교 필드는 이름,
  생년월일, 역할입니다. 같은 인물이 별도 선거 표에 다시 나오면 그 횟수도 정답에 넣습니다.
- 운영 파서의 안건 목록에는 해임 대상도 있습니다. 해임은
  이번 비교에서 제외하되 동작별 안건·후보 수와 제외 수를 보고합니다. 알 수 없는 동작은 오류입니다.
- 임기 시작·종료, 경력 기간, 현재 재직 여부, 회사 선택, 주총 회차 선택, 도구 응답의 렌더링은
  이 평가에 포함되지 않습니다. 파서를 직접 검사하는 것이므로 실제 MCP 도구 호출 검증도 별도로 필요합니다.
  후보표의 임기 분리는 `tests/test_personnel_term.py`와 `tests/test_personnel_feedback_regressions.py`로
  별도 검사합니다. 전자는 기간·날짜·각주 경계, 후자는 원문 응답 경계부터 후보 평가와 실제 MCP 응답까지 확인합니다.
  이 합성 검사의 통과나 임기 열이 없는 실제 표본의 통과를 시장 임기 정확도로 읽으면 안 됩니다.
- **현재 실제 시장 정확도나 holdout의 충분성을 보증하지 않습니다.** 통과는 이 고정 표본과
  평가 범위에서만 유효합니다. 별도 검증 표본이 없어도 개발 검사는 가능하며, 보고서에 표본 수를 드러냅니다.
- 결과 파일은 검증 산출물입니다. 사용자 조회 결과의 캐시, 시장 데이터베이스 또는 서비스 응답으로 재사용하지 않습니다.

## 한 번 실행하기

이미 준비된 개발 환경의 Python을 사용하세요. 평가를 위해 새 환경 설치나 동기화를 할 필요는 없습니다.
아래 경로는 사용자의 실제 경로로 바꿉니다. 평가 결과는 공개 저장소 밖의 비공개·임시 폴더에 둡니다.

```bash
/path/to/open-proxy-mcp/.venv/bin/python /path/to/open-proxy-mcp-parsing-lab/scripts/parser_feedback.py \
  --code-root /path/to/open-proxy-mcp-parsing-lab \
  --manifest /private/path/corpus/gold.json \
  --cache-dir /private/path/corpus/documents \
  --out /private/path/reports/lab.json
```

| 인자 | 의미 |
|---|---|
| `--manifest` | 독립 검토 정답 JSON. 필수 |
| `--cache-dir` | DART 응답 경계 파일이 있는 폴더. 필수 |
| `--out` | 이번 평가 보고서의 비공개 저장 경로. 필수 |
| `--baseline` | 동일한 평가 목록·하네스로 만든 이전 보고서. 선택 |
| `--code-root` | 평가할 OPM 코드 폴더. 생략하면 하네스가 속한 저장소 |

평가 중에는 API 키나 환경 파일이 필요하지 않습니다. 응답 캐시를 직접 읽으며,
운영 캐시 조회 함수·수집기를 호출하지 않습니다. 파서 import 전부터 Python 소켓 생성·연결·전송과
DNS 조회를 막습니다. 파서가 차단 예외를 삼켜도 시도 횟수가 남고 해당 사례는 실패합니다.
이는 정상적인 Python 코드의 실수 방지 장치이지 악성 코드나 외부 자식 프로세스를 격리하는 운영체제 보안 샌드박스는 아닙니다.
프로세스 전체에 적용되므로 서버 안에서 병렬 실행하지 말고 독립 CLI 프로세스로 실행하세요.

## 정답 목록의 계약

다음은 **합성 예시**입니다. 예시 해시는 실제 파일 해시로 바꿔야 합니다.
실제 공시 정답과 회사 대응표는 공개 저장소에 넣지 않습니다.

```json
{
  "schema_version": 1,
  "cases": [
    {
      "id": "synthetic-election-a",
      "rcept_no": "20260301000001",
      "company_group": "synthetic-company-a",
      "split": "development",
      "source_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "review": {
        "reviewer": "independent-reader",
        "source_locator": "주주총회 목적사항별 기재사항 / 선임 후보자 표 / 첫 행"
      },
      "expected": [
        {"name": "홍길동", "birthDate": "1970.03.01", "roleType": "사내이사"}
      ]
    }
  ]
}
```

| 항목 | 요구 사항 |
|---|---|
| 전체 구조 | 위 필드만 허용. 버전은 정수 `1`. 빈 목록·누락 필드·알 수 없는 필드·JSON 중복 키는 오류 |
| `id` | 비어 있지 않은 고유 문자열 |
| `rcept_no` | ASCII 숫자로 된 접수번호 문자열. 길이 `14`. 같은 접수번호의 사례 중복 금지 |
| `company_group` | 같은 회사의 별칭·정정공시·다른 연도에도 동일한 값. 개발·holdout에 동시 포함 금지 |
| `split` | `development` 또는 `holdout` |
| `source_sha256` | 압축을 푼 원본 파일 바이트의 SHA-256. 소문자 16진수 길이 `64` |
| `review` | 원문을 읽은 검토자와 근거 위치를 각각 비어 있지 않은 문자열로 기록 |
| `expected` | 비어 있지 않은 배열. 각 행에 `name`, `birthDate`, `roleType`를 모두 명시 |
| 후보 필드 | 이름은 비어 있지 않은 문자열. 생년월일·역할은 문자열 또는 명시적 `null` |

입력 경계는 `<rcept_no>.json` 또는 `<rcept_no>.json.gz` 하나입니다. 둘 다 있으면 내용이 같아도
중복 오류입니다. 압축 파일은 `gzip.decompress(path.read_bytes())`, 비압축 파일은 `path.read_bytes()`를
그대로 해시 입력으로 씁니다. **JSON을 다시 직렬화하거나 공백·줄바꿈·키 순서를 정리하지 않습니다.**
동일한 압축 해제 바이트라면 압축 방식·압축 시각이 달라도 같은 해시입니다.

응답은 JSON 객체이고 `html`이 비어 있지 않은 문자열이어야 합니다. `text`, `images` 등
그 밖의 DART 응답 필드는 그대로 보관할 수 있습니다. 운영 파서에는 오직
`parse_personnel_xml(document["html"])`을 전달합니다. 중간 함수 출력이나 후보 목록 파일을 입력으로 받지 않습니다.

비교는 정확한 **멀티셋**, 즉 값과 횟수를 함께 비교합니다. 순서는 상관없지만 중복·잘못된 추가·누락은
모두 실패합니다. 이름 공백, 날짜 구분자, 역할 표현을 평가기가 임의로 정규화하지 않습니다.
파서의 생년월일·역할 키 누락은 `null`과 비교합니다. 정답에는 키를 생략하지 말고 `null`을 명시하세요.
정답에 없는 파서의 추가 후보 속성은 이번 필드 평가에 사용하지 않습니다.

### 선임과 해임의 경계

| 운영 파서의 `action` | 이번 후보 비교 |
|---|---|
| `선임` | 포함 |
| `해임` | 제외 수와 동작별 수를 기록 |
| `재선임`, `중임`, `연임` | 선임 후보로 포함. 동작별 집계는 원래 동작 표기를 보존 |
| 누락·빈 값·다른 값 | `PARSER_ACTION_SCHEMA` 오류. 조용히 제외하지 않음 |

평가기 계약 `personnel-appointment-candidates-v2`는 재선임·중임·연임을 포함하고 해임만 제외합니다.
이름·생년월일·역할·출현 횟수의 정확한 멀티셋 비교는 그대로입니다. 이후 범위를 바꾸려면
원문 라벨·평가 범위·평가기 버전을 함께 검토해 새 기준선을 만드세요. 제외된 후보도 구조와 필드 타입은 검사합니다.
동작별 집계가 도중에 끊겼으면 `action_counts_complete=false`로 표시합니다.

## main과 개선 코드의 공정한 비교

같은 하네스 파일을 사용해 별도 프로세스에서 실행합니다. 작업 폴더만 바꾸는 것으로 코드를 선택하지 말고
`--code-root`를 명시하세요. 다른 checkout의 패키지가 이미 import된 프로세스에서는 혼용을 거부합니다.

```bash
/path/to/open-proxy-mcp/.venv/bin/python /path/to/open-proxy-mcp-parsing-lab/scripts/parser_feedback.py \
  --code-root /path/to/open-proxy-mcp \
  --manifest /private/path/corpus/gold.json --cache-dir /private/path/corpus/documents \
  --out /private/path/reports/main-before.json

/path/to/open-proxy-mcp/.venv/bin/python /path/to/open-proxy-mcp-parsing-lab/scripts/parser_feedback.py \
  --code-root /path/to/open-proxy-mcp-parsing-lab \
  --manifest /private/path/corpus/gold.json --cache-dir /private/path/corpus/documents \
  --baseline /private/path/reports/main-before.json --out /private/path/reports/lab-after.json
```

보고서에 코드 폴더, 파서 파일 SHA-256, 하네스 SHA-256과 Python 버전을 기록합니다.
파서 파일이 평가 도중 바뀌어도 실패합니다. 파서 파일 해시가 모든 의존성·데이터·환경을 고정해 주지는 않으므로
실험 동안 checkout과 실행 환경을 유지하세요. 의도한 비교 대상이므로 main과 lab의 **파서** 해시는 달라도 됩니다.

이전 보고서와 평가 목록 지문·사례 집합·원문 해시·평가 범위·**하네스** 해시가 같아야 비교합니다.
평가 목록의 JSON 공백과 객체 키 순서는 지문에 영향을 주지 않지만 배열 순서, 정답, 검토 기록은 영향을 줍니다.
표본 삭제·정답 수정·평가기 수정 후에는 이전 결과를 억지로 비교하지 말고 새 하네스로 양쪽을 다시 평가하세요.
처음 만든 잘못된 측정 결과도 별도 파일로 보존해 측정기 오류와 파서 오류를 구분합니다.

## 보고서 읽기

| 항목 | 뜻 |
|---|---|
| 종료 코드 `0` | 모든 사례 일치, 입력·기준선·실행 오류 없음 |
| 종료 코드 `1` | 불일치, 누락·손상 입력, 파서 오류, 차단된 네트워크 시도 또는 비교 불가 |
| 종료 코드 `2` | CLI 사용 오류 또는 보고서 저장 불가 |
| 사례 `pass` / `fail` / `error` | 정확히 일치 / 후보 멀티셋 불일치 / 비교 자체를 수행할 수 없거나 안전 조건 위반 |
| `missing_count` / `unexpected_count` | 부족한 출현 횟수 / 잘못 추가된 출현 횟수 |
| `excluded_count` / `action_counts` | 평가 범위 밖 후보 수 / 동작별 안건 수와 후보 수 |
| `comparison.regressions` | 이전 통과 → 이번 불일치 또는 오류인 사례 식별자 |
| `comparison.improvements` | 이전 불일치 또는 오류 → 이번 통과인 사례 식별자 |
| `summary.errors` / `global_errors` | 오류가 난 사례 수 / 사례 밖 전체 오류 수. 개별 오류 사유 수와 다름 |
| `unmeasured_cases` | 후보 수를 측정하지 못한 사례 수. 누락을 후보가 없다는 뜻으로 읽지 않음 |

모든 입력 사례의 결과를 남깁니다. 불량 사례가 있어도 정상 사례를 함께 검사하며 조용히 표본을 버리지 않습니다.
합계는 측정 가능한 값만 더한 것이며 오류 사례는 사례별 `null`과 미측정 수를 함께 읽으세요.
개발·holdout별 통과·불일치·오류 수도 별도로 제공합니다.

보고서는 원문 본문, 후보 이름·날짜·직위 목록, 검토자·원문 위치, 외부 예외 메시지, API 키를 복사하지 않습니다.
실패한 사례는 식별자로 비공개 정답 목록과 원문을 찾아 대조합니다. 식별자와 코드 경로 자체에도 비밀을 넣지 마세요.
저장은 소유자 전용 권한으로 원자적으로 교체합니다. 같은 `--out`은 덮어쓰므로 실험마다 다른 파일명을 사용하세요.
입력·기준선 파일, 캐시 폴더, 하네스 및 평가 코드 저장소 안의 출력 경로는 거부합니다.

## 수집과 개선을 잇는 운영 루프

수집은 별도 담당자가 [collect_parser_documents.py](../scripts/collect_parser_documents.py)로 수행합니다.
평가 명령은 수집기를 실행하지 않습니다. 수집기에서는 `--destination`을 필수로 지정하고,
필요하면 `--reuse-cache`와 키가 이미 있는 파일을 읽는 `--env-file`을 사용합니다.
기본 재사용 위치는 `OPM_DOC_CACHE_DIR` 또는 임시 폴더의 `opm_cache`입니다. 기존 원문은 덮어쓰지 않습니다.
하네스 저장소 내부와 파일시스템 루트는 수집 대상 폴더로 사용할 수 없습니다.

| 수집기 운영 항목 | 기준 |
|---|---|
| 배치 크기 | 접수번호 `1–30`건 |
| 실행 방식 | 단일 키, 순차 실행, 요청 시도 후 `1.1`초 대기, 오류 시 중단 |
| `fetch_attempts` | 수집 함수 요청 시도 수. 실제 HTTP 요청 건수라고 해석하면 안 됨 |

운영 순서는 **실패 → 원문 독립 라벨 확인 → 파서 개선 → 동일 corpus 재검사 → holdout 확인 → MCP 검증 → 승인**입니다.

- 실패 원인이 원문·정답·평가기·파서 중 어디에 있는지 먼저 분리합니다. 파서 출력을 정답으로 복사하지 않습니다.
- 원문 검토자는 후보표의 행과 반복 출현, 역할 원문, 동작을 확인하고 근거 위치를 기록합니다.
  별도 검토자가 정답을 점검하면 측정 오류를 줄일 수 있습니다. 기록만 있다고 독립 검토가 자동 보증되지는 않습니다.
- 개발 표본으로 개선하되 이전에 맞던 사례도 전부 다시 검사합니다. 정답을 바꿔 통과시키지 않습니다.
- 회사 그룹을 나눈 holdout으로 확인합니다. 반복적으로 보고 맞춘 표본은 더 이상 미지의 holdout이 아니므로
  별도 표본을 늘립니다. 표본 충분성은 시장·규모·공시 양식·이름·역할·정정 패턴의 분포로 판단해야 합니다.
- 그 뒤 원문 경계를 대체한 실제 MCP 도구 경로로 응답과 경고를 확인합니다. 온라인 검증·추가 수집이 필요하면
  담당자가 별도로 승인된 절차를 실행합니다. 이 하네스가 자동으로 네트워크를 열지는 않습니다.
- 승인을 받은 변경만 반영합니다. 이 절차의 피드백은 사람이 검토하는 개선 루프이며 자동 학습·자동 정답 변경·자동 병합·자동 배포가 아닙니다.

하네스 자체의 회귀는 다음처럼 확인합니다.

```bash
PYTHONPATH=. /path/to/open-proxy-mcp/.venv/bin/python -m pytest -q tests/test_parser_feedback.py
```

## 실제 오프라인 피드백 실행기

[parser_pipeline.py](../scripts/parser_pipeline.py)는 위 평가기를 새 프로세스에서 실제로 실행하고,
명시적으로 제공한 후보 코드 폴더를 제한된 횟수만큼 검사합니다. 평가기 자체의 필드·정답 계약은 바꾸지 않습니다.
파서나 정답을 자동 수정하지 않으며 수집·네트워크·커밋·푸시·배포를 실행하지 않습니다.
여기서 **승격**은 `personnel_candidates` 범위의 오프라인 성공 포인터를 바꾸는 뜻입니다.
MCP·pilot·live 검증이나 전체 변경의 출시 승인이 아니며, 기록에 `deployment_authorized=false`를 명시합니다.

### 준비와 비공개 설정 계약

기존 환경의 Python을 사용합니다. `--approved-root`는 사용자가 승인한 **기존 비공개 폴더**이며,
현재 사용자 소유·소유자 전용 권한이어야 합니다. Git 저장소 안, `raw/` 아래, 홈·파일시스템·임시 폴더 자체는
거부합니다. 심링크 입력과 실행 폴더도 거부합니다. 원문과 정답은 읽기만 하며 새로 수집하거나 복사하지 않습니다.
코드 폴더와 비공개 폴더는 서로 포함할 수 없습니다.

설정 파일은 승인 루트 안에 둡니다. 입력은 기본적으로 같은 루트에서 읽으며, 기존 비공개 저장소의 원문을
복사하지 않으려면 설정에 선택 항목 `input_root`를 절대 경로로 명시할 수 있습니다. 이 별도 입력 루트에는
어떤 파일도 쓰지 않으며, `manifest`·`cache_dir`·`review`는 그 루트 기준 상대 경로가 됩니다.
공개 저장소에는 실제 설정·회사 대응표·원문·정답을 넣지 않습니다.
아래 경로·해시는 설명용 자리표시자입니다. 문서 해시는 **검토한 후보의 현재 문서 바이트**로 계산한 값이어야 합니다.
여러 후보에 같은 문서 계약을 적용하므로 문서 내용이 달라졌다면 새 설정으로 새 실행을 만듭니다.

```json
{
  "schema_version": 1,
  "data_type": "personnel_candidates",
  "manifest": "corpus/gold.json",
  "cache_dir": "corpus/documents",
  "review": "corpus/review.json",
  "baseline_code_root": "/path/to/retained-baseline",
  "candidate_code_roots": ["/path/to/candidate-a", "/path/to/candidate-b"],
  "max_attempts": 2,
  "policy": {
    "min_real_development_cases": 1,
    "min_real_holdout_cases": 1,
    "min_real_holdout_companies": 1,
    "min_positive_holdout_per_field": 1
  },
  "documents": {
    "docs/PARSER_FEEDBACK.md": "REPLACE_WITH_SHA256",
    "wiki/tools/shareholder_meeting_notice.md": "REPLACE_WITH_SHA256"
  }
}
```

| 설정 | 강제 조건 |
|---|---|
| 자료 종류 | `personnel_candidates`만 등록. 임기·재직·재무 등 다른 종류는 `UNSUPPORTED_DATA_TYPE`으로 차단 |
| 입력 경로 | `input_root` 또는 승인 루트 기준 상대 경로. 상위 경로·심링크·실행 산출물 재사용 금지 |
| 코드 경로 | 명시한 절대 경로. 기존 기준 코드와 후보만 평가하며 자동 코드 탐색·수정 없음 |
| 최대 시도 | 정수 `1–10`. 후보 목록도 `1–10`개. 예산에 들어가는 앞쪽 후보만 시도 |
| 표본 최소치 | 모두 양의 정수. 위 예시는 계약 설명용 최소값이며 시장 대표성 권고치가 아님 |
| 필드 양성 사례 | 실제 holdout에서 이름·생년월일·역할 각각 비어 있지 않은 정답을 가진 공시 수 |
| 문서 | 위 문서 경로를 모두 명시하고 각각 현재 SHA-256 지정 |

독립 재검토 기록의 `manifest_sha256`은 정답 파일의 **원본 바이트** 해시입니다. 정답의 모든 사례 식별자에
정확히 대응해야 하고, 검토자는 정답 작성자의 `review.reviewer`와 달라야 합니다.

```json
{
  "schema_version": 1,
  "manifest_sha256": "REPLACE_WITH_SHA256",
  "cases": {
    "synthetic-election-a": {
      "sample_type": "synthetic",
      "reviewer": "second-independent-reader",
      "independent": true,
      "holdout_unseen": false
    }
  }
}
```

`sample_type`은 `real` 또는 `synthetic`입니다. 합성 표본은 실행할 수 있지만 실제 표본 최소치에는 세지 않습니다.
실제 holdout에는 `holdout_unseen=true`가 필요합니다. 이미 개선에 사용한 표본을 미지의 표본으로 표시하면 안 됩니다.
재검토 JSON이 없으면 원문·정답의 후보 평가까지 실행한 뒤 `REVIEW_REQUIRED`로 승격을 차단합니다.
기존 서술형 판독 문서를 승인된 재검토 JSON으로 자동 변환하지 않습니다.
이 기록은 검토자의 선언이며, 실행기가 독립성·실표본 여부·시장 대표성의 진실을 자동 인증하지는 않습니다.
기존 평가기가 빈 정답 배열을 허용하지 않으므로 후보 없는 공시의 음성 정확도도 이 승격 범위에 포함되지 않습니다.

### 실행 순서와 버전 보관

```bash
/path/to/existing-venv/bin/python -I -B /path/to/lab/scripts/parser_pipeline.py \
  --approved-root /private/approved/parser-runs --config config.json
```

실행기는 다음 순서로 동작합니다.

1. 비공개 위치·설정·실행 잠금과 이전 성공 기록의 봉인 해시를 검사합니다.
2. 같은 입력으로 기준 코드를 실제 재평가합니다. 이전 성공이 있다면 기준 코드 지문이 이전 승격 코드와 같아야 합니다.
   원문·정답·평가기 변경 시에도 이전 점수를 이어 붙이지 않고 새 기준 평가를 수행합니다.
3. 후보를 새 프로세스에서 평가합니다. 실패하면 예산 안의 다음 명시 후보로 진행합니다.
   내용 지문이 같은 후보는 다른 폴더에 있어도 `UNCHANGED_CANDIDATE`로 차단하며 평가를 반복하지 않습니다.
4. 평가 성공 후 독립 재검토 기록·실제 개발/holdout 표본·회사 수·필드별 양성 표본 기준을 검사합니다.
5. 현재 문서 해시를 대조하고 기존 `gen_index.py --check`, `wiki_lint.py --strict`,
   `check_documentation_contract.py`를 실제 실행합니다. 뒤의 검사는 기존 사업 내용 필드 계약 범위이며,
   인사 문서의 의미를 자동 판독하는 검사는 아닙니다. 인사 문서 검토는 설정에 고정한 문서 해시와 함께 필요합니다.
6. **같은 원문·정답·후보·환경**으로 다시 평가하고 보고서가 같음을 확인한 뒤 성공 포인터를 원자적으로 교체합니다.
   입력·코드·문서·환경이 도중에 바뀌면 전체 실행을 차단합니다.

코드 지문에는 `open_proxy_mcp/`의 코드·데이터 전체, `scripts/`, `pyproject.toml`, `uv.lock`을 포함합니다.
미커밋 파일도 내용으로 식별합니다. 환경 지문은 실행 Python 바이너리와 표준 라이브러리·확장 모듈·설치 패키지·데이터의
실제 파일 바이트를 포함하며, 실행기·평가기 해시도 별도 고정합니다. 바이트코드 캐시는 제외합니다.
하위 실행은 키·사용자 환경 변수를 전달하지 않는 고정 환경과 `-I -B`를 사용합니다.

승격 대상 코드에 `personnel_term.py` 또는 알려진 임기 출력 표식이 있으면, 기준 코드와 같더라도
`TERM_OUTPUT_REQUIRES_UNSUPPORTED_TERM_GATE`로 차단합니다. 임기 없는 표본에서 후보 평가가 성공해도 동일합니다.
**현재는 양성 임기 정답을 추가하는 것만으로 임기 승격이 열리지 않습니다.** 임기 평가기·출력 계약을 별도로 등록해야 합니다.
이 검사는 알려진 OPM 임기 경로에 대한 보수적 범위 차단이며 임의 코드의 모든 의미를 분석하는 보안 검사는 아닙니다.

| 비공개 산출물 | 역할 |
|---|---|
| `runs/<고유 실행 식별자>/config.json` | 이번 설정의 고정 사본. 원문·정답 본문 없음 |
| `run.json`, `code-*.json` | 범위·시작/종료 시각·입력/환경/코드 지문·시도별 상태·이전 성공 참조 |
| `baseline.json`, `evaluation-*.json`, `confirmation-*.json` | 실제 평가기가 만든 기준·후보·최종 재평가 보고서 |
| `docs-*.json` | 실제 문서 검사 결과와 검사 대상 문서의 현재 해시 |
| `seal.json` | 묶음의 파일별 해시. 완료 후 파일·폴더 쓰기 권한 제거 |
| `last_good.json` | 마지막 성공 실행과 봉인 해시·허용 필드·배포 승인 아님을 가리키는 포인터 |

기존 실행 묶음은 덮어쓰거나 삭제하지 않습니다. 버전은 내용 지문과 고유 실행 식별자로 구분하고 이전 성공을 연결합니다.
프로젝트 릴리즈 번호를 자동으로 올리지는 않습니다. 원문·정답·기준 checkout은 별도로 보존해야 재현할 수 있으며,
이 실행기는 그 파일들을 복사하거나 원문 저장 위치를 변경하지 않습니다. 소유자가 권한을 되돌려 변조하는 것까지 막는
불변 저장 장치는 아니지만, 다음 실행에서 이전 성공 묶음의 파일 추가·누락·해시 변경을 검출합니다.
실패·예산 소진은 `REVIEW` 또는 `BLOCKED`로 보관하고 기존 성공 포인터를 유지합니다. 포인터 교체 전에 중단되면
완료된 묶음이 남아도 승격된 것은 아닙니다. 실행기 출력에는 상태·고정 오류 코드·실행 식별자만 포함합니다.

| 종료 코드 | 의미 |
|---|---|
| `0` | 이 오프라인 후보 범위의 성공 포인터 교체 완료 |
| `1` | 미통과·범위 밖·입력/환경/보관 오류. 출시 승인 아님 |

신뢰하는 로컬 코드만 실행하세요. 기존 평가기의 소켓 차단을 재사용하지만, 악성 코드·자식 프로세스를 격리하는
운영체제 보안 샌드박스는 아닙니다. 평가 보고서는 검증 근거로만 쓰고 사용자 조회 결과·서비스 응답 캐시로 재사용하지 않습니다.

실패 주입 시험은 [test_parser_pipeline.py](../tests/test_parser_pipeline.py)에 있습니다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /path/to/existing-venv/bin/python -B -m pytest -q \
  -p no:cacheprovider tests/test_parser_pipeline.py tests/test_parser_feedback.py
```
