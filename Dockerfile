# syntax=docker/dockerfile:1
#
# One stage, slim base, no build toolchain in the final image. Nothing is baked in that a running
# service should not hold: no credentials, no service-account keys, no .env. Cloud Run supplies
# identity through the metadata server, and configuration through environment variables set at
# deploy time.
FROM python:3.12-slim

# Fail fast and log immediately: buffered output in a Cloud Run container means logs arrive after
# the thing you are debugging.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Dependencies first, so a source change does not reinstall them.
#
# **Installed under constraints, and that is the point.** Without them pip resolved whatever was
# newest at build time, so the artefact that shipped was not the artefact that was tested: one build
# picked up `google-api-core` 2.35.0 against 2.34.0 locally, and Firestore *queries* began failing
# with `Invalid database id %28default%29` while document reads carried on working. The exception
# desk loaded; only the pages that run a query returned 500. A smoke test that fetches one document
# would have passed.
COPY pyproject.toml constraints.txt ./
RUN pip install --no-cache-dir -c constraints.txt . \
    && pip install --no-cache-dir -c constraints.txt "uvicorn[standard]>=0.32.0"

COPY src/ ./src/
# The books and records travel with the image because this deployment reads the committed
# fixtures. A production deployment would read them from Firestore; the fixtures are synthetic and
# contain no client, proprietary or personal data, which is what makes shipping them acceptable.
COPY fixtures/data/ ./fixtures/data/
COPY eval/ ./eval/

# Non-root. Cloud Run does not require it, and running as root anyway is the kind of default that
# only ever costs something later.
RUN useradd --create-home --uid 10001 nav && chown -R nav:nav /app
USER nav

# Cloud Run sets PORT. Honouring it rather than hardcoding 8080 is what lets the same image run
# locally on a different port.
ENV PORT=8080
CMD exec uvicorn nav_sentinel.server:app --host 0.0.0.0 --port ${PORT} --workers 1
