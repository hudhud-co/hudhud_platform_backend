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
#   docker build -f services/merchant/Dockerfile -t hudhud/merchant .

FROM python:3.12-slim-bookworm AS base

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/services/merchant/.venv \
    HUDHUD_CONTRACTS_ROOT=/app/contracts \
    PATH=/app/services/merchant/.venv/bin:$PATH

WORKDIR /app/services/merchant

# Dependencies first, so a source-only change does not re-resolve the lock.
COPY contracts /app/contracts
COPY packages/event_envelope /app/packages/event_envelope
COPY packages/messaging_conformance /app/packages/messaging_conformance
COPY services/merchant/pyproject.toml services/merchant/uv.lock ./
COPY services/merchant/README.md ./README.md
COPY services/merchant/src ./src

# `--no-dev` keeps pytest and ruff out of the image. Alembic is in the dev group but is a
# real runtime need here: the container migrates its own database before it serves.
RUN uv sync --frozen --no-dev \
 && uv pip install --no-cache "alembic>=1.14"

COPY services/merchant/alembic.ini ./alembic.ini
COPY services/merchant/alembic ./alembic
COPY services/merchant/docker-entrypoint.sh /usr/local/bin/entrypoint

RUN chmod +x /usr/local/bin/entrypoint \
 && useradd --create-home --uid 10001 hudhud \
 && chown -R hudhud:hudhud /app
USER hudhud

EXPOSE 8103
ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["uvicorn", "merchant.main:app", "--host", "0.0.0.0", "--port", "8103"]
