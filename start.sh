#!/bin/sh
set -e

python travel_agent.py --api --host 127.0.0.1 --port 8001 &
API_PID=$!

nginx -g 'daemon off;' &
NGINX_PID=$!

cleanup() {
  kill "$API_PID" "$NGINX_PID" 2>/dev/null || true
  wait "$API_PID" "$NGINX_PID" 2>/dev/null || true
}

trap cleanup TERM INT

wait -n "$API_PID" "$NGINX_PID"
exit $?
