#!/usr/bin/env bash
set -euo pipefail

# Compatibility entrypoint for deployments that still reference this file.
# FUT.GG content ingestion is HTTP-only, so no Xvfb/browser bootstrap remains.
exec python futgg_content_sync.py
