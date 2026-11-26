# Central con Docker Compose

Alternativa a `instalar_central.sh` para servidores donde se prefiere Docker. Levanta tres contenedores:

- **db**: PostgreSQL 17, sin puertos publicados.
- **panel**: el panel web (uvicorn) en `127.0.0.1:8766` del host. Al arrancar ejecuta `preparar` (crea `config.json`, aplica migraciones y la cuenta admin).
- **tareas**: reemplaza los temporizadores de `systemd/` (importar, análisis, media, alertas, mantenimiento y respaldo diario a las 02:20).

Todo lo que cambia (config, fotos, claves SSH conocidas de las Zero) queda en el volumen `datos` (`/app/datos`), y la base en el volumen `postgres`.

## Primera instalación

```bash
cp docker/ejemplo.env .env
# completar POSTGRES_PASSWORD, por ejemplo con: openssl rand -base64 36 | tr -d '/+='
docker compose up -d --build
docker compose logs panel | grep 'Primer acceso'   # contraseña inicial de admin
curl -fsS http://127.0.0.1:8766/salud
```

`POSTGRES_PASSWORD` solo se usa al crear la base y `config.json` la primera vez. Cambiarla después no cambia la base.

## Acceso

El panel solo escucha en localhost. Las Zero necesitan una URL HTTPS: poner delante nginx con TLS (con `client_max_body_size 40m` para las fotos) o Tailscale Serve (`sudo tailscale serve --bg --https=8766 http://127.0.0.1:8766`). El panel confía en los encabezados `X-Forwarded-*` de ese proxy para detectar HTTPS y la IP real.

## Actualizar

```bash
git pull
docker compose up -d --build
```

## Comandos útiles

```bash
docker compose exec panel python -m mgv_central clave-admin                          # nueva contraseña de admin
docker compose exec panel python -m mgv_central volcar-base --destino /app/datos/base.dump
docker compose logs -f tareas
```

Los respaldos configurados desde el panel deben apuntar a una carpeta dentro de `/app/datos` (por ejemplo `/app/datos/respaldos`) o a un volumen adicional montado en el contenedor.
