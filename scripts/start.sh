#!/bin/sh
# Container entrypoint: migrate, seed the demo account, then serve.
set -e

alembic upgrade head
# A seed failure must not take the site down; it is logged and retried on the next start.
python -m app.seed || echo '{"event": "seed_failed", "level": "error"}'

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-3000}" --proxy-headers --forwarded-allow-ips='*'
