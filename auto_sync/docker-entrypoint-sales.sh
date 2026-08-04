#!/usr/bin/env bash
set -euo pipefail

# Headed Chromium via Xvfb (item: 2026-08-04 headless-detection finding).
#
# Production diagnostics confirmed Cloudflare is challenging
# /api/fut/player-prices/26/<id>/?verify=... based on the browser being
# HEADLESS, not on which transport requests it - the browser's own
# User-Agent Client Hints (navigator.userAgentData, visible in this
# worker's own outbound Google Analytics traffic as
# uafvl=HeadlessChrome;...) leak "HeadlessChrome" regardless of the
# spoofed User-Agent HEADER, and both signed_fetch's injected fetch() and
# page_capture's fully organic page navigation were challenged
# identically while headless. Running headed Chromium against a real
# (virtual) X11 display removes that specific signal, matching the same
# headed-vs-headless behavioural difference already confirmed for
# FUTBIN's Cloudflare in this repo's retired futbin_sbc_sync.py (headless
# got 403, headed got 200 - see Dockerfile.sbc's history and README.md's
# "SBC collector" section). FUTBIN ultimately still blocked headed
# traffic for an UNRELATED reason (a network/IP-level WAF block, not a
# solvable JS challenge) - that is not evidence this won't work for
# FUT.GG's Cloudflare, which is a different site with a different
# configuration; it only means headed mode alone isn't a universal fix
# for every Cloudflare deployment.
#
# Started Xvfb directly instead of using `xvfb-run -a`, same reasoning as
# docker-entrypoint-sbc.sh: `xvfb-run -a` auto-picks a display number by
# scanning /tmp/.X*-lock files, a mechanism known to hang on restricted/
# ephemeral container runtimes. Starting Xvfb ourselves on the fixed
# display already set via ENV DISPLAY=:99, with an explicit (bounded)
# readiness check instead of an unbounded implicit one, avoids that
# failure mode entirely - and fails loudly within ~2s instead of hanging
# forever if Xvfb itself is the thing that's broken in this environment.
DISPLAY_NUM="${DISPLAY#:}"
SOCKET="/tmp/.X11-unix/X${DISPLAY_NUM}"

echo "Starting Xvfb on display ${DISPLAY:-:99}..."
Xvfb "${DISPLAY:-:99}" -screen 0 1920x1080x24 -nolisten tcp &
XVFB_PID=$!

ready=0
for _ in $(seq 1 20); do
    if [ -e "$SOCKET" ]; then
        ready=1
        break
    fi
    if ! kill -0 "$XVFB_PID" 2>/dev/null; then
        echo "Xvfb process exited before creating ${SOCKET} - see any Xvfb output above for why"
        exit 1
    fi
    sleep 0.1
done

if [ "$ready" = "1" ]; then
    echo "Xvfb ready (socket ${SOCKET} present)"
else
    echo "WARNING: Xvfb socket ${SOCKET} not seen after 2s - continuing anyway; the worker's own browser-launch timeout will fail loudly if this is fatal"
fi

# exec, not a bare call: futgg_sales_sync.py's own process replaces this
# shell (PID 1 stays a single process), so SIGTERM/SIGINT from Coolify
# still reach it directly for the graceful-shutdown handling already
# built into run_forever() - no wrapper process sitting in between to
# swallow or delay the signal.
exec python futgg_sales_sync.py
