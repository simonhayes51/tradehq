FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_HEADLESS=true

# This one image runs as four separate Coolify services (see
# docker-compose.yml), each with its command overridden. The default
# below only matters if a service is ever deployed without that override
# - was test_futgg_catalogue_10.py (a 10-card smoke test) in the original
# Railway image; changed here to the one-shot, side-effect-light player
# metadata sync, so an accidental un-overridden deploy does something
# real and safe rather than nothing.
CMD ["python", "futgg_player_sync.py"]