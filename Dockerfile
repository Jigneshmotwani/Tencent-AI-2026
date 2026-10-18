# Render image for the API side: OTel Collector + LiteLLM gateway + FastAPI, supervised by backend/app/remotestart.py.
# The frontend is deployed separately on Vercel (see docs/DEPLOY.md).
FROM otel/opentelemetry-collector-contrib:0.161.0 AS otel
FROM node:22-slim AS nodejs

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/venv/bin:$PATH \
    PRISMA_HOME_DIR=/opt/prisma/home PRISMA_BINARY_CACHE_DIR=/opt/prisma/cache
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates openssl && rm -rf /var/lib/apt/lists/*
# Node is only used by the Prisma CLI that LiteLLM needs for its own tables.
COPY --from=nodejs /usr/local/bin/node /usr/local/bin/node
COPY --from=nodejs /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY --from=otel /otelcol-contrib /usr/local/bin/otelcol-contrib

WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
# Generate the Prisma client and cache the engines now, so starting never downloads anything.
RUN DATABASE_URL=postgresql://build:build@localhost:5432/build \
    prisma generate --schema "$(python -c 'import litellm,os;print(os.path.join(os.path.dirname(litellm.__file__),"proxy","schema.prisma"))')" \
    && prisma py fetch

COPY backend ./backend
COPY mcp_servers ./mcp_servers
COPY config ./config
COPY observability/otel-collector.yaml ./observability/otel-collector.yaml
COPY demo-content ./demo-content
COPY demo-packs ./demo-packs
RUN useradd --create-home --uid 10001 app && mkdir -p /app/.local && chown -R app:app /app/.local /opt/prisma
USER app
EXPOSE 10000
CMD ["python", "-m", "backend.app.remotestart"]
