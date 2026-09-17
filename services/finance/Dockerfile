# syntax=docker/dockerfile:1
#
# BUILD_CONTEXT_ALLOWLIST: packages/event_envelope packages/messaging_conformance contracts
#
# The context is the repository root, not this directory, because `[tool.uv.sources]`
# resolves both shared packages through `../../packages/...`. Only this service, those two
# packages and `contracts/` are copied in — no other service's source reaches this image.
#
# `contracts/` is a runtime asset, not documentation: services validate event envelopes
# against those JSON schemas and read `contracts/events/registry.yaml` at import time. A
# service without it does not start.
#
#   docker build -f services/finance/Dockerfile -t hudhud/finance .

FROM python:3.12-slim-bookworm AS base

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/services/finance/.venv \
    HUDHUD_CONTRACTS_ROOT=/app/contracts \
    PATH=/app/services/finance/.venv/bin:$PATH

WORKDIR /app/services/finance

# Dependencies first, so a source-only change does not re-resolve the lock.
COPY contracts /app/contracts
COPY packages/event_envelope /app/packages/event_envelope
COPY packages/messaging_conformance /app/packages/messaging_conformance
COPY services/finance/pyproject.toml services/finance/uv.lock ./
COPY services/finance/README.md ./README.md
COPY services/finance/src ./src

# `--no-dev` keeps pytest and ruff out of the image. Alembic is in the dev group but is a
# real runtime need here: the container migrates its own database before it serves.
RUN uv sync --frozen --no-dev \
 && uv pip install --no-cache "alembic>=1.14"

COPY services/finance/alembic.ini ./alembic.ini
COPY services/finance/alembic ./alembic
COPY services/finance/docker-entrypoint.sh /usr/local/bin/entrypoint

RUN chmod +x /usr/local/bin/entrypoint \
 && useradd --create-home --uid 10001 hudhud \
 && chown -R hudhud:hudhud /app
USER hudhud

EXPOSE 8109
ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["uvicorn", "finance.main:app", "--host", "0.0.0.0", "--port", "8109"]
