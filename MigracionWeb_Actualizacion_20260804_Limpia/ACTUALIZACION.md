# Copia de actualización — 2026-08-04

Esta copia contiene íntegramente el código actual de `MigracionWeb`, incluidas
las mejoras de timbrado, addendas, CFDI emitidos, correo, logos configurables,
MIO, importación histórica y la sobreescritura segura de facturas con CFDI.

Se mantiene aislada del proyecto operativo:

- Puerto de desarrollo: `8011`.
- Almacenamiento propio: carpeta `storage` de esta copia.
- No contiene CSD, llaves privadas, XML/PDF emitidos, base operativa ni
  instaladores compilados.
- No instala ni modifica servicios de Windows.

Para iniciar en modo de desarrollo:

```powershell
cd "E:\Proyectos\Facturacion 150426 casa\Proyecto facturacion\MigracionWeb_Actualizacion_20260804_Limpia"
python run_api.py
```

Abrir: `http://127.0.0.1:8011/app`.

La conexión al legado conserva la configuración compartida. No realizar
operaciones de escritura contra datos reales sin validar el alcance.
