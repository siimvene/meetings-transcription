#!/bin/bash
# Deploy or update meetings-transcription on the B200 server.
# Usage: ./deploy/deploy.sh
#
# First run: copies env.example to .env (edit before starting services).
# Subsequent runs: pulls latest code, runs migrations, rebuilds, restarts.

set -eu -o pipefail

cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"

echo "=== Deploying meetings-transcription ==="
echo "Directory: $REPO_DIR"

# --- Pull latest code ---
echo ""
echo "--- Pulling latest changes ---"
git pull --ff-only

# --- Ensure config/.env exists ---
if [ ! -f config/.env ]; then
    cp config/env.example config/.env
    echo ""
    echo "!!! Created config/.env from env.example."
    echo "!!! Edit config/.env with your actual values before continuing:"
    echo "!!!   - VLLM_BASE_URL, VLLM_API_KEY, VLLM_MODEL"
    echo "!!!   - POSTGRES_PASSWORD, DATABASE_URL"
    echo "!!!   - AZURE_CLIENT_ID, AZURE_TENANT_ID"
    echo "!!!   - NEXT_PUBLIC_API_URL"
    echo ""
    echo "Then re-run this script."
    exit 1
fi

# --- Download NMT models if missing ---
if [ ! -d models/septilang ]; then
    echo ""
    echo "--- Downloading NMT models ---"
    bash scripts/download-models.sh
fi

# --- Build containers ---
echo ""
echo "--- Building containers ---"
docker compose build

# --- Start infrastructure first (postgres, rabbitmq) ---
echo ""
echo "--- Starting infrastructure ---"
docker compose up -d postgres rabbitmq
echo "Waiting for PostgreSQL to be healthy..."
until docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-meetings}" > /dev/null 2>&1; do
    sleep 2
done
echo "PostgreSQL is ready."

# --- Run database migrations ---
echo ""
echo "--- Running database migrations ---"
for migration in schema/migrate_*.sql; do
    if [ -f "$migration" ]; then
        echo "Applying: $migration"
        docker compose exec -T postgres psql \
            -U "${POSTGRES_USER:-meetings}" \
            -d "${POSTGRES_DB:-meetings}" \
            -f "/dev/stdin" < "$migration"
    fi
done
echo "Migrations complete."

# --- Start all services ---
echo ""
echo "--- Starting all services ---"
docker compose up -d

# --- Wait for health checks ---
echo ""
echo "--- Waiting for services to be healthy ---"
SERVICES="transcription summarizer api"
for svc in $SERVICES; do
    echo -n "  $svc: "
    timeout=120
    while [ $timeout -gt 0 ]; do
        health=$(docker inspect --format='{{.State.Health.Status}}' "mt-$svc" 2>/dev/null || echo "missing")
        if [ "$health" = "healthy" ]; then
            echo "healthy"
            break
        fi
        sleep 3
        timeout=$((timeout - 3))
    done
    if [ $timeout -le 0 ]; then
        echo "TIMEOUT (check: docker compose logs $svc)"
    fi
done

# --- Verify ---
echo ""
echo "--- Service status ---"
docker compose ps

echo ""
echo "--- Health check ---"
curl -sf http://localhost:8080/health && echo "" || echo "API health check failed"

echo ""
echo "=== Deployment complete ==="
echo "Web UI: https://meetings.your-domain.ee (update in nginx config)"
echo "API:    http://localhost:8080/health"
echo "gRPC:   localhost:50051"
