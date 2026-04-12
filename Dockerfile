FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY open_proxy_mcp/ open_proxy_mcp/

RUN pip install --no-cache-dir .

RUN useradd -r -s /bin/false appuser
USER appuser

EXPOSE 8000

CMD ["python", "-m", "open_proxy_mcp.server", "--transport", "streamable-http"]
