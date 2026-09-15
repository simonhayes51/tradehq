#!/usr/bin/env bash
set -euo pipefail

# Gold Rare rotation is an FC27 launch policy, not an older-cycle policy.
# FUTGG_GAME_YEAR defaults to 27 across the live workers; when explicitly
# set to an older cycle, keep the historical collector behaviour unchanged.
GAME_YEAR="${FUTGG_GAME_YEAR:-27}"
if [ "$GAME_YEAR" != "27" ]; then
    export FUTGG_TRIGGER_GOLD_RARE_ROTATION_AFTER_MIN=5256000
    echo "Gold Rare sales rotation disabled for FC${GAME_YEAR}; FC27-only policy"
else
    echo "Gold Rare sales rotation enabled for FC27"
fi

exec /app/docker-entrypoint-sales.sh
