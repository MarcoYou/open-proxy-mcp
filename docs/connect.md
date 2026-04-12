# 로컬 설치 가이드

로컬에서 실행하면 DART 외에 추가 API 키도 설정할 수 있습니다 (후보자 뉴스 검색, OCR fallback 등).

---

## 1. 설치

```bash
git clone https://github.com/MarcoYou/open-proxy-mcp.git
cd open-proxy-mcp
uv sync                    # .venv 생성 + 의존성 설치
cp .env.example .env       # 환경변수 파일 생성
```

## 2. 환경변수 설정

`.env` 파일을 열고 API 키를 입력합니다. **OPENDART_API_KEY만 있으면 핵심 기능 전부 사용 가능합니다.**

```bash
# .env (필수)
OPENDART_API_KEY=발급받은_키

# 선택 - 추가 기능 활성화
OPENDART_API_KEY_2=보조_키                        # 분당 1,000회 제한 시 자동 전환
NAVER_SEARCH_API_CLIENT_ID=네이버_클라이언트_ID     # 후보자 뉴스 검색
NAVER_SEARCH_API_CLIENT_SECRET=네이버_시크릿        # 후보자 뉴스 검색
UPSTAGE_API_KEY=업스테이지_키                       # OCR fallback (Tier 3)
```

| API 키 | 필수 여부 | 발급처 | 용도 |
|--------|----------|--------|------|
| `OPENDART_API_KEY` | **필수** | [DART OpenAPI](https://opendart.fss.or.kr/) 회원가입 -> 인증키 신청 | AGM/OWN/DIV 전체 |
| `OPENDART_API_KEY_2` | 선택 | 동일 (보조 키) | 분당 1,000회 제한 도달 시 자동 전환 |
| `NAVER_SEARCH_API_CLIENT_ID` | 선택 | [네이버 개발자센터](https://developers.naver.com/) -> 애플리케이션 등록 -> 검색 API | 후보자 뉴스 검색 |
| `NAVER_SEARCH_API_CLIENT_SECRET` | 선택 | 동일 | 동일 |
| `UPSTAGE_API_KEY` | 선택 | [Upstage AI](https://www.upstage.ai/) 회원가입 -> API 키 발급 | OCR fallback (Tier 3) |

## 3. Editable Install

```bash
uv pip install -e .
```

## 4. Claude Desktop 연결

`~/Library/Application Support/Claude/claude_desktop_config.json`에 추가:

```json
{
  "mcpServers": {
    "open-proxy-mcp": {
      "command": "/path/to/open-proxy-mcp/.venv/bin/python",
      "args": ["-m", "open_proxy_mcp"],
      "cwd": "/path/to/open-proxy-mcp"
    }
  }
}
```

## 5. Claude Code 연결

```json
// .mcp.json (프로젝트 루트)
{
  "mcpServers": {
    "open-proxy-mcp": {
      "command": "/path/to/open-proxy-mcp/.venv/bin/python",
      "args": ["-m", "open_proxy_mcp"],
      "cwd": "/path/to/open-proxy-mcp"
    }
  }
}
```

## 6. 선택 의존성

```bash
uv pip install -e ".[pdf]"               # + PDF/OCR fallback
uv pip install -e ".[llm]"               # + LLM fallback (Claude/OpenAI)
uv pip install -e ".[all]"               # 전부 설치
```

## Fallback 파싱

AGM 안건 파서는 대부분 XML 단계에서 정상 파싱됩니다 (KOSPI 200 기준 평균 97%+ 정확도). 비표준 형식의 공시에 한해 PDF, OCR 순서로 fallback합니다. PDF/OCR fallback은 로컬 설치 환경에서 동작합니다.

```
_xml (DART API, 무료, <1초)  <- 대부분 여기서 완료
  ↓ 비표준 형식인 경우
_pdf (PDF 다운로드, 무료, 4초+)
  ↓ 그래도 안 되는 경우
_ocr (Upstage OCR API, 유료, 8초+)  <- 100% 정확도
```

AI가 결과 품질을 보고 자율적으로 다음 단계로 넘어갑니다. 사용자 개입 불필요.

<details>
<summary>KOSPI 200 파서별 정확도 (클릭하여 펼치기)</summary>

| 파서 | XML | PDF | OCR |
|------|-----|-----|-----|
| 안건 목록 | 99.5% | 98.0% | 100% |
| 재무상태표 | 97.4% | 97.9% | 100% |
| 손익계산서 | 100% | 95.7% | 100% |
| 이사/감사 선임 | 98.9% | 97.9% | 100% |
| 정관변경 | 97.8% | 99.0% | 100% |
| 보수한도 | 98.4% | 99.5% | 100% |
| 자기주식 | 93.6% | 100% | 100% |
| 자본준비금 | 100% | 100% | 100% |
| 퇴직금 | 93.3% | 86.7% | 86.7% |

</details>
