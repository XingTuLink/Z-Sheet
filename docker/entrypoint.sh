#!/bin/sh
# Apply database migrations, then start the API server.
set -e

echo "[entrypoint] running database migrations..."
alembic upgrade head

echo "[entrypoint] starting uvicorn..."
exec uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
