FROM python:3.11-slim

WORKDIR /app

# Chromium needs these OS libraries to actually launch, not just the
# playwright Python package (installed below via requirements.txt).
# Mirrors railway's nixpacks.toml [phases.playwright] step, which used
# `playwright install --with-deps` on a base image that already had them -
# this base doesn't, so they're listed explicitly.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
