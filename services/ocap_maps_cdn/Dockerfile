FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /data/ocap_maps

ENV MAPS_CDN_STORAGE_PATH=/data/ocap_maps
ENV MAPS_CDN_HOST=0.0.0.0
ENV MAPS_CDN_PORT=8080

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
