# MicroGreenVision Central en Docker. Ver docs/docker.md.
FROM python:3.13-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=America/Montevideo \
    MGV_CENTRAL_CONFIG=/app/datos/config.json \
    MGV_CENTRAL_ARCHIVO=/app/datos/archivo

# ffmpeg: timelapse · rsync: respaldos completos · postgresql-client (17): pg_dump/pg_restore,
# misma versión mayor que el contenedor de la base.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg rsync postgresql-client-17 tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt

RUN useradd --uid 1000 --home-dir /app/datos --no-create-home --shell /usr/sbin/nologin central \
    && install -d -m 700 -o central -g central /app/datos

# Mismo diseño que ~/MicroGreenVision_Central: el código, los paquetes para las Zero y RunPod en la raíz,
# y todo lo que cambia (config, fotos, claves SSH conocidas) en /app/datos.
COPY mgv_central ./mgv_central
COPY zero ./zero
COPY runpod_ia ./runpod_ia
COPY docker/tareas.py docker/entrypoint.sh ./docker/
RUN tar -czf zero.tar.gz zero && tar -czf runpod-ia.tar.gz runpod_ia && rm -rf zero runpod_ia \
    && chmod 644 zero.tar.gz runpod-ia.tar.gz && chmod 755 docker/entrypoint.sh

USER central
VOLUME ["/app/datos"]
EXPOSE 8766

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8766/salud', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["tini", "--", "/app/docker/entrypoint.sh"]
CMD ["panel"]
