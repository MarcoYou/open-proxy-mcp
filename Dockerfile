FROM python:3.12-slim

WORKDIR /app

# uv.lock 을 함께 복사해 **로컬과 같은 버전**을 설치한다.
# lock 없이 `pip install .` 로 범위를 다시 풀면, 의존성이 새 버전을 내놓은 날 코드 변경과
# 무관하게 배포만 깨진다(260729 실측: mcp 2.0.0 이 fastmcp 제거 → 운영 중단).
COPY pyproject.toml uv.lock ./
COPY open_proxy_mcp/ open_proxy_mcp/
# 260814: 규칙 데이터(룰 40 · 조항 대장 SSOT · 동의어 사전)는
#   `open_proxy_mcp/data/laws/` 로 옮겨 **코드와 함께** 배포된다 — 여기서 챙길 필요가 없다.
#   종전에는 이 COPY 한 줄이 빠지면 룰 40개가 조용히 0이 되고 강행규정 판정이 통째로
#   사라졌다(경고·로그·헬스체크 전무). 이제 로더가 로그를 남기고 /health 가 개수를 싣는다.
#
# 아래는 **corpus 만** 남긴다 — 법령 원문+인덱스 11MB 라 휠에 싣지 않는다.
#   law_lookup 이 조문 전문을 돌려줄 때 쓴다. 없으면 조문 검색이 빈 결과가 되므로
#   production 필수이고, 빠지면 /health 의 law_corpus_articles 가 0으로 보인다.
COPY wiki/rules/laws/corpus/ wiki/rules/laws/corpus/

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv
# --locked: lock 과 pyproject 가 어긋나면 **빌드를 실패시킨다**(조용히 다른 버전 설치 금지)
# --no-dev: 테스트 의존성 제외 · --no-editable: 소스 링크 대신 실제 설치
RUN uv sync --locked --no-dev --no-editable && uv cache clean

RUN useradd -r -s /bin/false appuser
USER appuser

EXPOSE 8000

CMD ["/app/.venv/bin/python", "-m", "open_proxy_mcp.server", "--transport", "streamable-http"]
