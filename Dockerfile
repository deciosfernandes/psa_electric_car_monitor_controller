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
RUN pip install --no-cache-dir -c backend/requirements.lock ./backend

# Headless Chromium for the PSA login, in a path readable by the unprivileged app user. Not available on
# armv7: skipped there (the setup falls back to the manual flow); on other architectures a failure fails the build.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# libatomic1 is needed by Playwright's bundled node.
RUN set -e;     if [ "$(uname -m)" = "armv7l" ]; then         echo "armv7: skipping Playwright";     else         apt-get update && apt-get install -y --no-install-recommends libatomic1;         playwright install --with-deps --only-shell chromium;         chmod -R a+rX /ms-playwright;     fi;     rm -rf /var/lib/apt/lists/*

COPY --from=frontend /frontend/dist /app/static

RUN useradd --system --create-home --uid 10001 psacc && mkdir -p /data && chown psacc:psacc /data
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

VOLUME /data
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:5000/api/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "psacc.main:app", "--host", "0.0.0.0", "--port", "5000"]
