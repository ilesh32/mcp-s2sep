FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_server/ mcp_server/

RUN useradd --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown -R app:app /app
USER app

ENV PYTHONUNBUFFERED=1 \
    AUDIT_LOG_PATH=/app/data/audit_log.jsonl \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

EXPOSE 8000

# Liveness only (unauthenticated /healthz); MCP_AUTH_TOKEN must be supplied at runtime.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('MCP_PORT', '8000'), timeout=3)"

CMD ["python", "-m", "mcp_server.server"]
