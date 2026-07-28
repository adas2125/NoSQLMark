#!/usr/bin/env bash
set -euo pipefail

RUN_TAG="manual-$(date +%Y%m%d-%H%M%S)"
RUN_DIR="artifacts/$RUN_TAG"
REDIS_DIR="/tmp/nosqlmark-redis-$RUN_TAG"

mkdir -p "$RUN_DIR" "$REDIS_DIR"

echo "RUN_TAG=$RUN_TAG"
echo "RUN_DIR=$RUN_DIR"
echo "REDIS_DIR=$REDIS_DIR"

# Avoid accidentally colliding with an existing Redis experiment.
if redis-cli -h 127.0.0.1 -p 6380 ping >/dev/null 2>&1; then
    echo "ERROR: Redis port 6380 is already in use." >&2
    exit 1
fi

redis-server \
  --bind 127.0.0.1 \
  --port 6380 \
  --protected-mode yes \
  --save "" \
  --appendonly no \
  --dir "$REDIS_DIR" \
  --pidfile "$REDIS_DIR/redis.pid" \
  --logfile "$REDIS_DIR/redis.log" \
  --daemonize yes

redis-cli -h 127.0.0.1 -p 6380 ping
cat "$REDIS_DIR/redis.pid"

{
    echo "RUN_TAG=$RUN_TAG"
    echo "RUN_DIR=$RUN_DIR"
    echo "REDIS_DIR=$REDIS_DIR"
    echo "REDIS_PORT=6380"
    echo "REDIS_PID=$(cat "$REDIS_DIR/redis.pid")"
} > "$RUN_DIR/redis-environment.txt"

echo "Redis started successfully. Log: $REDIS_DIR/redis.log"
