#!/bin/sh
# Build Storm avec garde-fou taille : les tuiles OCAP restent sur volume, pas dans l'image.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TAG="${1:-ghcr.io/gdc-framework/storm_v3:latest}"
MAX_CONTEXT_MB="${MAX_DOCKER_CONTEXT_MB:-200}"

if [ -d ocap_maps ]; then
  size_mb="$(du -sm ocap_maps 2>/dev/null | cut -f1 || echo 0)"
  if [ "$size_mb" -gt 0 ]; then
    echo "ERREUR: ocap_maps/ présent dans le dépôt ($(du -sh ocap_maps | cut -f1))." >&2
    echo "Les cartes OCAP doivent être sur un volume (OCAP_MAPS_HOST_PATH=/var/lib/ocap_maps), pas dans l'image Docker." >&2
    exit 1
  fi
fi

if [ -d missions ]; then
  size_mb="$(du -sm missions 2>/dev/null | cut -f1 || echo 0)"
  if [ "$size_mb" -gt "$MAX_CONTEXT_MB" ]; then
    echo "ERREUR: missions/ trop volumineux ($(du -sh missions | cut -f1)) pour un build Docker." >&2
    exit 1
  fi
fi

echo "Build $TAG (contexte limité au code applicatif)…"
docker build -t "$TAG" .

size_human="$(docker image inspect "$TAG" --format '{{.Size}}' | awk '{printf "%.0f MB\n", $1/1024/1024}')"
echo "Image créée : $TAG (~$size_human)"
if [ "$(docker image inspect "$TAG" --format '{{.Size}}')" -gt 1073741824 ]; then
  echo "AVERTISSEMENT: image > 1 Go — vérifier qu'aucune donnée runtime n'a été copiée." >&2
fi
