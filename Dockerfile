FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WARP_DB_PATH=/data/warp.db \
    VIBE_TRADING_HOME=/data/vibe

RUN apt-get update && apt-get install -y --no-install-recommends \
    git gcc g++ libffi-dev libssl-dev libxml2 libxslt1.1 \
    libopenblas0-pthread libjpeg62-turbo libpango-1.0-0 libpangocairo-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
# Pin the audited user fork. Update intentionally after regression tests.
RUN pip install . && pip install \
    'git+https://github.com/bayuewalker/Vibe-Trading.git@61d20c9a3a5954dcb52ce2bb758ca0ac4d6c71a4'

RUN useradd -r -u 10001 warp && mkdir -p /data && chown -R warp:warp /data /app
USER warp
EXPOSE 8080
CMD ["python", "-m", "warp3c"]
