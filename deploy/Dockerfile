# Build from the WatcherMCP repository root:
#   docker build -f deploy/Dockerfile -t watchermcp-web .
#   docker run --rm -p 8090:8090 watchermcp-web
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8090 \
    WATCHER_MCP_ZONE=all \
    WATCHER_MCP_DB=/app/data/overwatch.db \
    WATCHER_MCP_SEED_DEMO=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY overwatch ./overwatch
COPY deploy ./deploy

RUN mkdir -p /app/data && useradd --create-home --shell /bin/bash watcher && chown -R watcher:watcher /app
USER watcher

EXPOSE 8090

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import os, urllib.request; port = os.environ.get('PORT', '8090'); urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2).close()"

CMD ["python", "deploy/mcp_web_server.py"]
