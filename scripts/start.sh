#!/bin/sh
# Container entrypoint: migrate, seed the demo account, then serve.
#
# Uvicorn reads these from the environment:
#   FORWARDED_ALLOW_IPS  proxies trusted to set X-Forwarded-For (default 127.0.0.1). Only list
#                        your platform's proxy: trusting everyone lets clients fake their IP and
#                        slip past rate limits.
#   WEB_CONCURRENCY      worker processes (default 1). With more than one, point
#                        RATE_LIMIT_STORAGE_URI at a shared store such as redis://.
set -e

alembic upgrade head
# A seed failure must not take the site down; it is logged and retried on the next start.
python -m app.seed || echo '{"event": "seed_failed", "level": "error"}'

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
