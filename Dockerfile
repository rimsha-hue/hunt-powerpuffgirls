# syntax=docker/dockerfile:1

# ---- Stage 1: build the React frontend ----
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=production \
    PORT=3000

# WeasyPrint system libraries (used for CV PDFs from Phase 3).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Install dependencies from pyproject in their own layer so code changes don't reinstall them.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > requirements.txt \
    && pip install -r requirements.txt

COPY alembic.ini ./
COPY data/ ./data/
COPY fixtures/ ./fixtures/
COPY scripts/start.sh ./scripts/start.sh
COPY app/ ./app/
COPY --from=frontend /build/dist ./frontend/dist

# Writable dir for the SQLite fallback; production sets DATABASE_URL to Neon.
RUN useradd --system --no-create-home appuser \
    && mkdir -p /srv/var \
    && chown appuser /srv/var \
    && chmod +x scripts/start.sh
ENV DATABASE_URL=sqlite+aiosqlite:////srv/var/power_hunt.db
USER appuser

# 3000 is the port Alternate Clouds publishes by default. PORT must match the platform's
# published port, or Cloudflare returns 502.
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"3000\")}/ready', timeout=4)"

CMD ["./scripts/start.sh"]
