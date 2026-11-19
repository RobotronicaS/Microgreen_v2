# Cambios

## 1.2.0 — 19/11/2026

- Canteros por invernáculo: distribución configurable de 1 a 20 filas y columnas, visible en el esquema interior.
- Nombre, planta o especie, variedad y descripción por cantero; edición para técnicos y administradores, consulta para lectores.
- Reducir la distribución conserva la información de los canteros que quedan fuera; al ampliarla se recupera por su fila y columna.
- Opción de eliminar invernáculos para administradores, con confirmación mediante el nombre completo y control de sesión.
- La baja quita el invernáculo de las vistas, pausa las cámaras, cancela solicitudes pendientes y bloquea recepción. Las Zero conservan acceso a su programación pausada.
- Se conservan cuentas, fotografías, historial y datos de cultivo. Migración de base de datos compatible con instalaciones anteriores.

## 1.1.1 — 19/11/2026

- Canal permanente `estable` para instalar y actualizar los servidores desde GitHub.
- Publicación automática en `estable` después de aprobar las pruebas del commit actual de `main`.
- Actualizador con avance sin sobrescritura de commits y control de cambios locales.
- Registro de versión, commit y fecha de la última instalación verificada.
- Guía de instalación en otro servidor, acceso al panel y actualizaciones futuras.
- Preparación de pruebas sin paquetes multimedia que no usa la suite; FFmpeg se conserva en el instalador del servidor.

## 1.1.0 — 18/11/2026

- Conexión opcional a un Pod RunPod mediante HTTPS y clave de análisis.
- Alta manual de análisis por lote y modo automático opcional para fotos nuevas.
- Resultados SAM2, PNG y CSV separados de las mediciones RGB; control de región y revisión.
- Reintentos, caché de solicitudes y validación de imagen, respuesta y permisos.
- Servicio GPU reproducible, pesos de SAM2 fijados y prueba local sin exponer puertos.
- La cobertura con filtrado de verde es experimental y requiere validación con el cultivo real.

## 1.0.1 — 18/11/2026

- El alta y el cambio de conexión admiten cámaras con contraseña vacía; el formulario y el servidor dejan de exigirla.
- Ayuda para conexiones RTSP sin contraseña y sin autenticación. La búsqueda ONVIF no identifica por sí sola el perfil de video.
- La Zero existente puede recibir estas conexiones sin reinstalar su agente.

## 1.0.0 — 18/11/2026

- Interfaz ilustrada para PC: dibujos de invernáculos, esquema interior y fichas seleccionables de cámaras y Zero.
- Posiciones persistentes y enlaces de cada cámara con su equipo de captura.
- Alta sencilla de invernáculos y varias Zero por instalación.
- Se elimina el límite de la plantilla de 15 puestos en el alta remota; códigos nuevos libres.
- Configuración individual de programación y cambio de conexión probado antes de reemplazarla.
- Perfiles Tapo/Hikvision/Dahua y RTSP personalizado, con canal, giro, tamaño, transporte y espera.
- Agente 1.0 con capacidades explícitas; actualización del motor de captura en las Zero.
- Instalación nueva sin catálogo ficticio; migración que conserva datos de versiones anteriores.
- Pruebas de permisos, expansión, plano, reconfiguración y migraciones idempotentes.

## 0.8.0

Registro de usuarios de consulta y versión activa visible. Captura manual, avisos y respaldos de 0.7; vinculación de Zero desde Central y alta remota de cámaras de 0.5/0.6.
