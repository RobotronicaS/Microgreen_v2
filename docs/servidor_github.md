# Instalar y actualizar el servidor desde GitHub

El canal de instalación es siempre **`estable`** del repositorio **RobotronicaS/Microgreen_v2**. La entrega 1.1.1 incluye lo implementado hasta 1.1.0: invernáculos ilustrados, varias Zero y cámaras, configuración de captura, cámaras con contraseña vacía, usuarios y permisos, fotos e historial, lotes, análisis RGB, supervisor, avisos, respaldos e integración opcional con RunPod/SAM2.

## Requisitos del servidor

Usar Debian 13 de 64 bits, o Raspberry Pi OS de 64 bits basado en Debian 13, con systemd, Internet y un usuario con sudo. El instalador usa paquetes de esa distribución: no es un instalador para Windows, Docker ni un alojamiento web compartido. La PC o el teléfono acceden al panel mediante el navegador.

El operador debe entrar por SSH con un usuario normal, no con root. La primera instalación y todas las actualizaciones deben hacerse con ese mismo usuario: la aplicación y sus servicios pertenecen a su cuenta.

El repositorio requiere acceso autorizado si es privado. Tu compañero necesita acceso de lectura en GitHub, con su propia cuenta y una clave SSH o GitHub CLI. No compartir contraseñas, tokens personales ni el archivo `config.json` de otro servidor. Si GitHub responde «Repository not found», comprobar ese acceso antes de continuar.

## Primera instalación

Ejecutar dentro de la terminal Linux del servidor, un bloque por vez:

```bash
sudo apt update
sudo apt install -y git gh curl ca-certificates
```

Si todavía no está autenticado para leer el repositorio:

```bash
gh auth login
gh auth setup-git
```

Clonar la rama estable en una carpeta de fuentes nueva y separada de los datos:

```bash
git clone --branch estable --single-branch https://github.com/RobotronicaS/Microgreen_v2.git "$HOME/microgreen-fuentes"
```

Después de que termine correctamente:

```bash
cd "$HOME/microgreen-fuentes"
bash actualizar_central.sh
```

No anteponer sudo al script. Pedirá sudo cuando sea necesario. Si ya existe `~/microgreen-fuentes`, no clonar encima: usar el clon existente si corresponde a este repositorio o elegir otra carpeta vacía.

El programa se instala en `~/MicroGreenVision_Central`. La primera instalación imprime la contraseña de `admin` en la terminal; guardarla de forma privada. Se inicia sin invernáculos ni cámaras de ejemplo. Agregar el invernáculo desde el panel y luego vincular cada Zero a este servidor.

## Actualizar a las próximas versiones

Siempre ejecutar con el usuario original:

```bash
cd "$HOME/microgreen-fuentes"
bash actualizar_central.sh
```

El actualizador descarga `origin/estable`, adelanta la rama local sin reescribir commits y ejecuta el instalador. También reinstala si ya está en el último commit, para poder reintentar una instalación interrumpida. Si hay cambios locales o commits que divergen de estable, se detiene con una explicación y conserva esos archivos. No usar `git reset --hard` para resolverlo; guardar el trabajo o clonar en otra carpeta.

El instalador conserva la configuración, las cuentas, las fotografías, las credenciales y la base existente. Antes de actualizar una instalación existente, guarda el código anterior, las unidades de servicio y un volcado PostgreSQL en `~/MicroGreenVision_Central/.versiones/`. La ruta aparece al terminar. Las copias de fotos completas se gestionan por separado con los respaldos de la aplicación.

Durante la instalación hay una interrupción breve del panel. Las Zero siguen capturando y pueden reenviar sus fotos pendientes. Actualizar Central no instala automáticamente el agente de todas las Zero: hacerlo desde la ficha de cada equipo cuando la entrega lo requiera.

Si la instalación se interrumpe, el instalador intenta reponer el código y los servicios anteriores. No restaura automáticamente la base después de una migración. Conservar la reserva y revisar el error antes de restaurar; no borrar la instalación.

## Verificar y abrir el panel

```bash
curl -fsS http://127.0.0.1:8766/salud
systemctl --user status microgreen-central-panel.service --no-pager
cat "$HOME/MicroGreenVision_Central/VERSION_INSTALADA.txt"
```

La salud debe mostrar `estado: ok` y la versión publicada (1.1.1 en esta entrega). `VERSION_INSTALADA.txt` registra versión, commit y fecha UTC de la última instalación que superó la verificación.

El panel escucha en `127.0.0.1:8766`. Para una comprobación desde tu PC, abrir otra terminal PowerShell y crear un túnel SSH sustituyendo `usuario@servidor`:

```powershell
ssh -N -L 8766:127.0.0.1:8766 usuario@servidor
```

Mantener esa terminal abierta y visitar `http://127.0.0.1:8766` en el navegador de la PC. Ese túnel permite probar el panel, pero las Zero necesitan una URL HTTPS accesible desde su red: para el acceso habitual, configurar Tailscale Serve según la [guía de acceso privado](instalacion.md#3-configurá-el-acceso-privado-a-central).

Si este servidor reemplaza al actual, primero planificar el traslado de base, fotos y credenciales mediante los respaldos. Una instalación nueva en otro equipo no copia automáticamente los datos del servidor anterior. Tampoco cambia el destino de las Zero: su vínculo y URL deben corresponder a Central nuevo.

RunPod es opcional: configurar su URL y clave de análisis siguiendo [esta guía](runpod.md). Instalar Central no enciende GPU ni configura una cuenta RunPod. Las estimaciones SAM2 requieren validación sobre imágenes reales del cultivo.

## Cómo se publican las versiones

1. Desarrollar en una rama `feature/...` o `fix/...`.
2. Integrar los cambios en `main`, con la versión y su historial actualizados.
3. GitHub ejecuta las pruebas. Si pasan en el commit actual de `main`, la tarea `publicar-estable` adelanta `estable` a ese mismo commit.
4. Comprobar que la tarea terminó correctamente y que ambos commits coinciden antes de anunciar la entrega.
5. Cada operador ejecuta el mismo actualizador en su servidor cuando decide instalarla.

`estable` conserva el mismo nombre en todas las entregas. Las ramas antiguas por versión quedan como referencia histórica. El flujo no instala cambios automáticamente en los servidores ni requiere guardar credenciales SSH de los servidores en GitHub.
