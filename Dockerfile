FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PSACC_DATA_DIR=/data \
    PSACC_STATIC_DIR=/app/static \
    TZ=Atlantic/Azores

WORKDIR /app
COPY backend/ /app/backend/
RUN pip install --no-cache-dir ./backend

# Headless Chromium for the PSA login. Not available on armv7: the build must not fail there,
# the setup then falls back to the manual flow.
RUN if [ "$(uname -m)" != "armv7l" ]; then \
        playwright install --with-deps --only-shell chromium || echo "Playwright install failed, manual login fallback"; \
    else \
        echo "armv7: skipping Playwright"; \
    fi; \
    rm -rf /var/lib/apt/lists/*

COPY --from=frontend /frontend/dist /app/static

RUN useradd --system --create-home --uid 10001 psacc \
    && mkdir -p /data && chown psacc:psacc /data
USER psacc

VOLUME /data
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:5000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "psacc.main:app", "--host", "0.0.0.0", "--port", "5000"]
