# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
RUN pip install .

COPY alembic.ini ./
COPY migrations ./migrations

RUN useradd --create-home --uid 10001 appuser
USER appuser
EXPOSE 8000
# One worker process per CPU core, as the design specifies. Override with WEB_CONCURRENCY.
CMD ["sh", "-c", "exec uvicorn app.api:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-$(nproc)}"]

# Test image: the same code plus the development tools and the test suite.
FROM base AS test
USER root
RUN pip install ".[dev]"
COPY tests ./tests
USER appuser
CMD ["pytest", "-q"]
