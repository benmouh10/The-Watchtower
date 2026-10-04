# =============================================================================
# GlobeLens AI — FastAPI Backend Dockerfile
# Multi-stage build: base → development → production
# =============================================================================

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: base — shared Python environment and system dependencies
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

# Build metadata labels
LABEL maintainer="GlobeLens AI Team"
LABEL description="GlobeLens AI FastAPI Backend"
LABEL version="2.0"

# Prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # pip config
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Application directory
    APP_HOME=/app \
    # Playwright config
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR $APP_HOME

# Install system dependencies required by psycopg2, Pillow, lxml, and Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libpq-dev \
        libssl-dev \
        libffi-dev \
        libxml2-dev \
        libxslt-dev \
        gcc \
        git \
        # Playwright Chromium system dependencies
        libglib2.0-0 \
        libnss3 \
        libnspr4 \
        libdbus-1-3 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libatspi2.0-0 \
        libx11-6 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libxcb1 \
        libxkbcommon0 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies in a dedicated layer for caching
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    mkdir -p /ms-playwright && \
    playwright install chromium && \
    chmod -R 777 /ms-playwright

# Local (free, offline) text-to-speech. Split from the layer above so editing
# ML dependencies does not invalidate the Playwright download, and so images
# built without narration can drop this stage.
ARG KOKORO_MODEL_VERSION=model-files-v1.0
ARG KOKORO_MODEL_SHA256=7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5
ARG KOKORO_VOICES_SHA256=bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d
ENV TTS_LOCAL_MODEL_PATH=/app/models/tts/kokoro-v1.0.onnx \
    TTS_LOCAL_VOICES_PATH=/app/models/tts/voices-v1.0.bin

COPY requirements-tts.txt .
RUN pip install --no-cache-dir -r requirements-tts.txt

# The model is fetched once at build time and verified, so a deployment never
# depends on GitHub being reachable at boot. Swap TTS_LOCAL_MODEL_PATH for the
# fp16 or int8 build to trade quality for size and speed.
RUN mkdir -p /app/models/tts && \
    curl -fsSL --retry 3 --max-time 1800 -o /app/models/tts/kokoro-v1.0.onnx \
      "https://github.com/thewh1teagle/kokoro-onnx/releases/download/${KOKORO_MODEL_VERSION}/kokoro-v1.0.onnx" && \
    curl -fsSL --retry 3 --max-time 600 -o /app/models/tts/voices-v1.0.bin \
      "https://github.com/thewh1teagle/kokoro-onnx/releases/download/${KOKORO_MODEL_VERSION}/voices-v1.0.bin" && \
    echo "${KOKORO_MODEL_SHA256}  /app/models/tts/kokoro-v1.0.onnx" | sha256sum -c - && \
    echo "${KOKORO_VOICES_SHA256}  /app/models/tts/voices-v1.0.bin" | sha256sum -c -

# Copy Alembic migration files
COPY alembic.ini .
COPY alembic ./alembic

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: development — hot-reload via uvicorn --reload
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS development

ENV APP_ENV=development

# Copy the full application source
COPY ./app ./app

# Expose FastAPI port
EXPOSE 8000

# Health check probe — calls the dedicated /health endpoint
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start uvicorn with hot-reload for development
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--reload", \
     "--reload-dir", "/app/app", \
     "--log-level", "debug"]

# ─────────────────────────────────────────────────────────────────────────────
# Stage 3: production — hardened, minimal image
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS production

ENV APP_ENV=production

# Create a non-root user for security
RUN addgroup --system globelens && \
    adduser --system --ingroup globelens --no-create-home globelens

COPY --chown=globelens:globelens ./app ./app

USER globelens

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Production: multiple workers via gunicorn with uvicorn worker class
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "4", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
