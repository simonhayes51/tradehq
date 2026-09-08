#!/usr/bin/env bash
set -euo pipefail

# Gold Rare rotation is an FC27 launch policy, not an FC26 endgame policy.
# Keep the current FC26 collector behaviour unchanged. When FUTGG_GAME_YEAR=27,
# the normal 8-hour Gold Rare rotation default in futgg_sales_information_shadow.py
# applies automatically.
GAME_YEAR="${FUTGG_GAME_YEAR:-26}"
if [ "$GAME_YEAR" != "27" ]; then
    export FUTGG_TRIGGER_GOLD_RARE_ROTATION_AFTER_MIN=5256000
    echo "Gold Rare sales rotation disabled for FC${GAME_YEAR}; FC27-only policy"
else
    echo "Gold Rare sales rotation enabled for FC27"
fi

exec /app/docker-entrypoint-sales.sh
