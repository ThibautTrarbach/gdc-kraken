#!/bin/sh
# Build maps-cdn avec garde-fou : les tuiles OCAP restent sur volume, pas dans l'image.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TAG="${1:-ghcr.io/gdc-framework/maps-cdn_v3:latest}"
CONTEXT="$ROOT/services/ocap_maps_cdn"

if [ -d "$CONTEXT/ocap_maps" ]; then
  size_mb="$(du -sm "$CONTEXT/ocap_maps" 2>/dev/null | cut -f1 || echo 0)"
  if [ "$size_mb" -gt 0 ]; then
    echo "ERREUR: ocap_maps/ présent dans le contexte maps-cdn ($(du -sh "$CONTEXT/ocap_maps" | cut -f1))." >&2
    echo "Les cartes OCAP doivent être sur un volume (OCAP_MAPS_HOST_PATH=/var/lib/ocap_maps), pas dans l'image Docker." >&2
    exit 1
  fi
fi

echo "Build $TAG (contexte $CONTEXT)…"
docker build -t "$TAG" -f "$CONTEXT/Dockerfile" "$CONTEXT"

size_human="$(docker image inspect "$TAG" --format '{{.Size}}' | awk '{printf "%.0f MB\n", $1/1024/1024}')"
echo "Image créée : $TAG (~$size_human)"
if [ "$(docker image inspect "$TAG" --format '{{.Size}}')" -gt 1073741824 ]; then
  echo "AVERTISSEMENT: image > 1 Go — vérifier qu'aucune donnée runtime n'a été copiée." >&2
fi
