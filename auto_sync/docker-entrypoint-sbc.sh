#!/usr/bin/env bash
set -euo pipefail

# FUT.GG challenges plain server HTTP with 403s. Run real headed Chromium on
# the same fixed virtual display used successfully by the sales collector.
DISPLAY_VALUE="${DISPLAY:-:99}"
DISPLAY_NUM="${DISPLAY_VALUE#:}"
SOCKET="/tmp/.X11-unix/X${DISPLAY_NUM}"
LOCK="/tmp/.X${DISPLAY_NUM}-lock"
if [ -e "$LOCK" ]; then
  stale_pid="$(tr -d '[:space:]' < "$LOCK" 2>/dev/null || true)"
  if [ -n "$stale_pid" ] && kill -0 "$stale_pid" 2>/dev/null; then
    echo "FATAL: Xvfb display ${DISPLAY} is already owned by PID ${stale_pid}"
    exit 1
  fi
  rm -f "$LOCK"
fi
rm -f "$SOCKET"
Xvfb "$DISPLAY_VALUE" -screen 0 1920x1080x24 -nolisten tcp &
XVFB_PID=$!
cleanup() { kill "$XVFB_PID" 2>/dev/null || true; }
trap cleanup EXIT
trap 'exit 0' INT TERM

for _ in $(seq 1 30); do
  [ -e "$SOCKET" ] && break
  kill -0 "$XVFB_PID" 2>/dev/null || { echo "FATAL: Xvfb exited during startup"; exit 1; }
  sleep 0.1
done
[ -e "$SOCKET" ] || { echo "FATAL: Xvfb did not create ${SOCKET}"; exit 1; }

# Works safely with the currently deployed long-running service, while
# FUTGG_CONTENT_RUN_ONCE=true keeps compatibility with a Scheduled Task.
if [ "${FUTGG_CONTENT_RUN_ONCE:-false}" = "true" ] || [ "${FUTGG_CONTENT_DIAGNOSTIC:-false}" = "true" ]; then
  exec python futgg_content_sync.py
fi

INTERVAL="${FUTGG_CONTENT_INTERVAL_SECONDS:-21600}"
RETRY_BASE="${FUTGG_CONTENT_RETRY_SECONDS:-900}"
RETRY_MAX="${FUTGG_CONTENT_RETRY_MAX_SECONDS:-21600}"
RETRY_DELAY="$RETRY_BASE"
while true; do
  if python futgg_content_sync.py; then
    echo "FUT.GG content sync succeeded; next run in ${INTERVAL}s"
    RETRY_DELAY="$RETRY_BASE"
    sleep "$INTERVAL"
  else
    echo "FUT.GG content sync failed; retrying in ${RETRY_DELAY}s (max ${RETRY_MAX}s)"
    sleep "$RETRY_DELAY"
    if [ "$RETRY_DELAY" -lt "$RETRY_MAX" ]; then
      RETRY_DELAY=$((RETRY_DELAY * 2))
      if [ "$RETRY_DELAY" -gt "$RETRY_MAX" ]; then
        RETRY_DELAY="$RETRY_MAX"
      fi
    fi
  fi
done
