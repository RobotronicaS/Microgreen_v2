# Publicación de MicroGreenVision Central

- El repositorio oficial es `RobotronicaS/Microgreen_v2`.
- `main` integra los cambios. Usar ramas `feature/...` o `fix/...` para desarrollar versiones nuevas.
- `estable` es el canal permanente de instalación de los servidores. No crear una rama de instalación distinta para cada versión ni recomendar checkouts detached para las actualizaciones habituales.
- La tarea `publicar-estable` de `.github/workflows/pruebas.yml` adelanta `estable` desde `main` únicamente después de aprobar las pruebas del mismo commit. No forzar ni reescribir su historial.
- Al publicar cambios, actualizar versión y CHANGELOG cuando corresponda, ejecutar las comprobaciones pertinentes y verificar en GitHub que las pruebas terminaron correctamente y que `estable` apunta al commit publicado. No afirmar publicación estable si la tarea falló.
- El comando habitual del operador es `bash actualizar_central.sh`, en el clon separado de `~/MicroGreenVision_Central`, con el mismo usuario Linux de la instalación.
- Conservar configuraciones locales, cuentas, fotos, base de datos y credenciales. Nunca publicar claves, datos de producción ni archivos generados por la instalación.
