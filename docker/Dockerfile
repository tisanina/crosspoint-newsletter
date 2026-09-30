# ---- Stage 1: Builder ----
FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir --prefix=/install .

# ---- Stage 2: Runtime ----
FROM python:3.12-slim

LABEL maintainer="tisanina (https://github.com/tisanina)"
LABEL description="CrossPoint Newsletter Server — standalone OPDS & email-to-EPUB pipeline"

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY src/ src/
COPY config/ config/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Create default volume directories
# /app/conf: Cache volume (SSD) -> DB SQLite, config
# /app/data: RAID volume (Array) -> epub, raw, covers
RUN mkdir -p /app/conf /app/data/epub /app/data/raw /app/data/covers

# Install tzdata for accurate local timezone support
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

# Default environment configuration
ENV TZ=Europe/Rome
ENV CN_TIMEZONE=Europe/Rome
ENV CN_CONF_DIR=/app/conf
ENV CN_DATA_DIR=/app/data
ENV CN_DB_PATH=/app/conf/newsletter.db
ENV CN_EPUB_DIR=/app/data/epub
ENV CN_RAW_DIR=/app/data/raw
ENV CN_COVERS_DIR=/app/data/covers
ENV CN_OPDS_HOST=0.0.0.0
ENV CN_OPDS_PORT=8400
ENV PYTHONUNBUFFERED=1

EXPOSE 8400

# Health check against /api/status endpoint (every 30 minutes)
HEALTHCHECK --interval=30m --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8400/api/status')" || exit 1


ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["cn", "serve", "--host", "0.0.0.0", "--port", "8400"]
