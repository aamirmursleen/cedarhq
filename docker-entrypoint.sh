#!/bin/sh
set -eu

: "${CEDARHQ_BASE_URL:=https://cedarhq.144.76.96.2.sslip.io}"
export CEDARHQ_BASE_URL

mkdir -p "$(dirname "$CEDARHQ_DATABASE_PATH")"

if [ "${SEED_DEMO:-1}" = "1" ]; then
  python /app/app.py --migrate --seed-demo --init-only
else
  python /app/app.py --migrate --init-only
fi

exec python /app/app.py --host 0.0.0.0 --port "${PORT:-3000}"
