#!/bin/sh
set -e

cd /app

SECRET_KEY="${SECRET_KEY:?SECRET_KEY environment variable is required}"
PLATFORM="${PLATFORM:-DOCKER}"
MISSIONS_PBO_STORAGE_PATH="${MISSIONS_PBO_STORAGE_PATH:-/data/missions_pbo}"
MEDIA_ROOT="${MEDIA_ROOT:-/data/persist/missions}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-3}"
GUNICORN_BIND="${GUNICORN_BIND:-0.0.0.0:8000}"
GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-120}"
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

export SECRET_KEY PLATFORM MISSIONS_PBO_STORAGE_PATH MEDIA_ROOT PUID PGID

# Named volumes are root-owned at first mount: fix perms then drop privileges.
if [ "$(id -u)" = "0" ]; then
  mkdir -p /data/persist/missions /app/staticfiles /app/missions "$MEDIA_ROOT"

  # Generate config.json as root (readable by the runtime user)
  python - <<'PY'
import json
import os

config = {
    "SECRET_KEY": os.environ["SECRET_KEY"],
    "PLATFORM": os.environ.get("PLATFORM", "DOCKER"),
    "MISSIONS_PBO_STORAGE_PATH": os.environ.get(
        "MISSIONS_PBO_STORAGE_PATH", "/data/missions_pbo"
    ),
}

with open("/app/config.json", "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2)
    f.write("\n")
PY
  chmod 644 /app/config.json

  # Writable runtime dirs only — do not chown the PBO bind mount (host-owned).
  chown -R "${PUID}:${PGID}" /data/persist /app/staticfiles /app/missions
  if [ -d "$MEDIA_ROOT" ]; then
    chown -R "${PUID}:${PGID}" "$MEDIA_ROOT"
  fi

  exec gosu "${PUID}:${PGID}" "$0" "$@"
fi

mkdir -p "$MEDIA_ROOT" /app/staticfiles

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec gunicorn gdc_kraken.wsgi:application \
    --bind "$GUNICORN_BIND" \
    --workers "$GUNICORN_WORKERS" \
    --timeout "$GUNICORN_TIMEOUT"
