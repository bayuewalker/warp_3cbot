FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
COPY web/ ./
RUN npm ci && npm run build

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WARP_DB_PATH=/data/warp.db \
    WARP_MANAGE_VIBE=0 \
    WARP_HTTP=fastapi \
    WARP_ANALYSIS=auto \
    WARP_WEB_DIST=/app/web/dist

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .
COPY --from=web /web/dist ./web/dist

RUN useradd -r -u 10001 warp && mkdir -p /data && chown -R warp:warp /data /app
USER warp
EXPOSE 8080
CMD ["python", "-m", "warp3c"]
