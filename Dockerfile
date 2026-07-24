FROM python:3.12-slim

LABEL org.opencontainers.image.source=https://github.com/GdC-Framework/gdc-kraken
LABEL org.opencontainers.image.description="GDC Storm (Kraken) application"

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie explicite du code applicatif uniquement.
# Ne pas utiliser COPY . . : risque d'inclure ocap_maps/, missions/, staticfiles/, etc.
COPY manage.py .
COPY gdc_kraken ./gdc_kraken
COPY gdc_storm ./gdc_storm
COPY docker ./docker

RUN mkdir -p /app/missions /app/staticfiles /data/missions_pbo /data/persist \
    && sed -i 's/\r$//' /app/docker/entrypoint.sh \
    && chmod +x /app/docker/entrypoint.sh

# Start as root so entrypoint can chown volumes, then drop to PUID:PGID
EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
