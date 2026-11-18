# Conectar Central con un Pod RunPod

Central 1.1 incorpora una conexión opcional al analizador SAM2. Las Raspberry Pi Zero siguen capturando y enviando fotos a Central. Central conserva los originales, prepara una copia RGB reducida y guarda las mediciones y vistas marcadas.

## Qué entrega esta versión

- Cobertura estimada de objetos seleccionados por su color verde dentro de la región del lote.
- Segmentación mediante SAM2.1 Hiera Tiny y selección posterior por color; algoritmo identificado como sam2.1-tiny-verde-v1.
- Imagen PNG marcada, fecha de captura, lote, cámara, revisión de región e historial descargable en CSV.
- Envío manual de la última foto del lote y modo automático opcional para las fotos nuevas que pasan el control de calidad local.
- Reintentos hasta tres veces y deduplicación de inferencias recientes. El Pod conserva una caché acotada a 256 resultados; Central guarda el histórico.
- Permisos del invernáculo, CSRF, clave de análisis y verificación de la huella de cada imagen y de la región.

Las mediciones de IA se guardan aparte de las mediciones RGB de versiones anteriores. Cambiar la región inicia una revisión nueva; no se comparan regiones distintas.

## Alcance y validación

La medida es experimental. SAM2 segmenta objetos y el filtro de verde selecciona candidatos: no es un modelo entrenado específicamente con las bandejas del usuario. Hay que revisar las máscaras sobre fotos del cultivo real. Plantas amarillas o rojas, tallos finos, reflejos y objetos verdes de fondo pueden producir errores. Usá una cámara fija, buena iluminación y una región que excluya el entorno.

Si SAM2 encuentra muy poca región respecto al verde visible, el resultado queda como segmentación insuficiente. Central muestra la imagen para revisar y deja vacía la cobertura del CSV; no interpreta ese resultado como ausencia de vegetación.

No se mide altura, biomasa, NDVI, número de plantas ni enfermedades. No se aplica este método RGB a imágenes nocturnas IR en gris. RF-DETR no se usa en esta versión porque no se dispone de un detector validado para este cultivo.

El ensayo físico realizado en el Pod usó la foto de vivero conservada en /workspace/planta_original.jpeg. Comprobó la ejecución de SAM2 y el transporte de resultados; esa foto no valida la exactitud sobre microbrotes.

## Preparar el Pod

Usá un Pod GPU con CUDA y la imagen PyTorch compatible. El ensayo utilizó RTX A5000 con 24 GB y runpod/pytorch:1.4.1-cu1281-torch280-ubuntu2404.

1. En Central, como administrador, abrí Configuración → Análisis con IA → Descargar programa del Pod.
2. Subí microgreen-runpod-ia.tar.gz al directorio /workspace de JupyterLab.
3. Abrí una terminal del Pod y ejecutá por separado:

    mkdir -p /workspace/microgreen/servicio_ia

    tar -xzf /workspace/microgreen-runpod-ia.tar.gz -C /workspace/microgreen/servicio_ia

    cd /workspace/microgreen/servicio_ia

    bash runpod_ia/instalar.sh

El instalador usa un entorno persistente en /workspace/microgreen/venv-ia con los paquetes PyTorch del contenedor. Fija el código de Meta SAM2 al commit 2b90b9f5ceec907a1c18123530e92e794ad901a4 y descarga los pesos oficiales de SAM2.1 Tiny en /workspace/microgreen/modelos. No instala CUDA ni PyTorch en las Raspberry.

## Configurar el acceso

En la terminal del Pod, desde /workspace/microgreen/servicio_ia:

    /workspace/microgreen/venv-ia/bin/python -m runpod_ia.configurar_token

El operador ingresa dos veces una clave de análisis de 32 a 256 caracteres ASCII sin espacios, preferiblemente generada por un gestor de contraseñas. La entrada no se muestra. El archivo token.txt queda con permisos 600 y no debe compartirse en chats ni subirse al repositorio. No se necesita la clave global de la cuenta RunPod.

La API no inicia si falta el archivo de acceso. Por defecto solo escucha en 127.0.0.1. Para permitir la conexión desde Central, el operador debe autorizar y agregar el puerto 8767/http en la configuración del Pod, manteniendo los puertos existentes. El proxy HTTPS será accesible por Internet; todas las operaciones de la API requieren la clave de análisis.

Después de autorizar esa exposición, iniciar desde la terminal del Pod:

    MGV_IA_HOST=0.0.0.0 nohup bash runpod_ia/iniciar.sh > /workspace/microgreen/servicio_ia.log 2>&1 < /dev/null &

Hay que ejecutar el comando nuevamente después de detener y arrancar el Pod. Los archivos de /workspace se conservan; los procesos no.

## Configurar Central

1. Actualizá Central con instalar_central.sh desde las fuentes 1.1.0, usando el usuario Linux de la instalación.
2. Abrí Configuración → Análisis con IA.
3. Ingresá la dirección HTTPS del puerto 8767 que muestre RunPod, sin rutas ni token en la URL.
4. Ingresá la misma clave configurada en el Pod y guardá. Dejarla vacía después conserva la anterior.
5. Usá Probar conexión guardada. Verifica autenticación, protocolo y algoritmo; una inferencia sobre una foto verifica además el modelo.
6. Abrí un lote que tenga cámara, fechas y región definidas. Entrá a Análisis con IA y solicitá la última foto.
7. Actualizá el estado. El proceso de análisis de Central envía la foto y guarda el resultado.
8. Revisá la imagen marcada antes de habilitar el modo automático.

La clave se guarda en runpod.json con permisos 600, fuera de las plantillas y de la auditoría. Los respaldos la incluyen con los mismos permisos que el resto de las configuraciones.

Central no enciende, migra ni amplía recursos de RunPod automáticamente. Si el Pod o la API están apagados, el trabajo se reintenta y luego queda con error visible; no se inventa una medición. Puede solicitarse otra vez cuando el analizador vuelva a estar disponible.

## Prueba local y recuperación

Para verificar el modelo sobre una foto sin abrir puertos:

    /workspace/microgreen/venv-ia/bin/python -m runpod_ia.probar_foto /workspace/planta_original.jpeg --salida /workspace/microgreen/resultados/prueba

La herramienta verificar_local ejecuta una prueba de la API en memoria con SAM2 real, valida el rechazo de acceso anónimo y comprueba la caché. Usa una identidad temporal de prueba y no modifica la clave de producción:

    /workspace/microgreen/venv-ia/bin/python -m runpod_ia.verificar_local

Las pruebas automatizadas de Central usan PostgreSQL aislado y un servicio de inferencia simulado; no contactan RunPod ni generan cargos.

Si RunPod migra el Pod, actualizá su dirección HTTPS en Central, reinstalá el entorno si cambió el contenedor y reiniciá la API. Antes de borrar un Pod antiguo, verificá sus datos: la migración de RunPod copia /workspace, no el disco del contenedor.

