FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_HEADLESS=true

# GENERIC base image - kept for futgg_sales_worker (command-overridden in
# docker-compose.yml; no dedicated Dockerfile of its own, see that file's
# top-of-file comment) and as a general-purpose fallback for anything not
# covered by the three dedicated images below.
#
# PlayerSync, PriceSync and SBCSync each now build from their own
# dedicated Dockerfile instead of overriding this one's command -
# Dockerfile.player, Dockerfile.price, Dockerfile.sbc - so Coolify can
# select build steps, base image and restart/scheduling behaviour
# correctly per worker instead of one shared image trying to serve all of
# them (see auto_sync/COOLIFY.md). This file is no longer "one image, N
# services with overridden commands" for those three.
#
# The default CMD below only matters if a service is ever deployed
# without a command override - was test_futgg_catalogue_10.py (a 10-card
# smoke test) in the original Railway image; changed here to the
# one-shot, side-effect-light player metadata sync, so an accidental
# un-overridden deploy does something real and safe rather than nothing.
CMD ["python", "futgg_player_sync.py"]