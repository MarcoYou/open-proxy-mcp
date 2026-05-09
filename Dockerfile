FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY open_proxy_mcp/ open_proxy_mcp/
# wiki/rules/laws/ — proxy_advise._load_law_layer_rules + _load_llm_misread_patterns가 dynamic load
# (38 법령 룰 + LLM misread guard catalog). production 필수.
COPY wiki/rules/laws/ wiki/rules/laws/

RUN pip install --no-cache-dir .

RUN useradd -r -s /bin/false appuser
USER appuser

EXPOSE 8000

CMD ["python", "-m", "open_proxy_mcp.server", "--transport", "streamable-http"]
