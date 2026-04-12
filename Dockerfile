FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir hatchling && \
    pip install --no-cache-dir .

COPY . .
RUN pip install --no-cache-dir .

FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /app /app

RUN useradd -r -s /bin/false appuser
USER appuser

EXPOSE 8000

CMD ["python", "-m", "open_proxy_mcp.server", "--transport", "streamable-http"]
