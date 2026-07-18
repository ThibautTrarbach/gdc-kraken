#!/bin/sh
set -e

cd /app

SECRET_KEY="${SECRET_KEY:?SECRET_KEY environment variable is required}"
PLATFORM="${PLATFORM:-DOCKER}"
MISSIONS_PBO_STORAGE_PATH="${MISSIONS_PBO_STORAGE_PATH:-/data/missions_pbo}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-3}"
MEDIA_ROOT="${MEDIA_ROOT:-/data/persist/missions}"

# Named volumes are root-owned at first mount: fix perms then drop privileges.
if [ "$(id -u)" = "0" ]; then
  mkdir -p /data/persist/missions /app/staticfiles /app/missions
  chown -R 999:987 /data/persist /app/staticfiles /app/missions
  # Do not chown the Pterodactyl bind mount (already 999:987 on host).
  exec gosu 999:987 "$0" "$@"
fi

mkdir -p "$MEDIA_ROOT" /app/staticfiles

# Generate config.json from environment (consumed by Django settings / wsgi)
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

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec gunicorn gdc_kraken.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "$GUNICORN_WORKERS"
