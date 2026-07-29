FROM python:3.12-slim

WORKDIR /app

# uv.lock 을 함께 복사해 **로컬과 같은 버전**을 설치한다.
# lock 없이 `pip install .` 로 범위를 다시 풀면, 의존성이 새 버전을 내놓은 날 코드 변경과
# 무관하게 배포만 깨진다(260729 실측: mcp 2.0.0 이 fastmcp 제거 → 운영 중단).
COPY pyproject.toml uv.lock ./
COPY open_proxy_mcp/ open_proxy_mcp/
# wiki/rules/laws/ — proxy_advise가 dynamic load: _load_law_layer_rules(40 룰) +
# _load_llm_misread_patterns(guard) + _load_law_provisions(조항 대장 SSOT — 근거 심화). production 필수.
COPY wiki/rules/laws/ wiki/rules/laws/

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv
# --locked: lock 과 pyproject 가 어긋나면 **빌드를 실패시킨다**(조용히 다른 버전 설치 금지)
# --no-dev: 테스트 의존성 제외 · --no-editable: 소스 링크 대신 실제 설치
RUN uv sync --locked --no-dev --no-editable && uv cache clean

RUN useradd -r -s /bin/false appuser
USER appuser

EXPOSE 8000

CMD ["/app/.venv/bin/python", "-m", "open_proxy_mcp.server", "--transport", "streamable-http"]
