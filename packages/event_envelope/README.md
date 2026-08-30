# event-envelope

Technical contract primitives for the HUDHUD integration message envelope (ADR-0002).

This package provides typed models, deterministic serialization, validation, and compatibility
policies. It does **not** include outbox/inbox persistence, NATS clients, or domain logic.

## Install

```bash
cd packages/event_envelope
uv lock && uv sync --all-groups
```

## Public API

See `event_envelope.__all__` for the stable surface.

## Tests

```bash
uv run ruff check .
uv run pytest
```
