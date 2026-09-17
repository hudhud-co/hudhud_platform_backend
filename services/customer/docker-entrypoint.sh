#!/bin/sh
# Migrate this service's own database, then serve.
#
# A service owns its schema (ADR-0011), so it is the only thing entitled to migrate it.
# Doing that here rather than in a separate compose job keeps `docker compose up` a single
# command and keeps the ordering honest: the app never starts against a schema older than
# the code, because it never gets that far.
#
# Set HUDHUD_SKIP_MIGRATIONS=1 to start without migrating — useful when several replicas
# of one service run and only one should hold the migration lock.
set -eu

if [ "${HUDHUD_SKIP_MIGRATIONS:-0}" != "1" ]; then
  echo "[entrypoint] alembic upgrade head"
  alembic -c ./alembic.ini upgrade head
fi

exec "$@"
