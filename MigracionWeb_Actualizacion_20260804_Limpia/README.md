# Migracion Web Facturacion

Proyecto paralelo al sistema actual para migrar gradualmente la operacion a
una plataforma web sin afectar la API vigente.

## Objetivo de esta primera base

- API nueva en puerto `8010`
- Login web minimo
- Dashboard base
- Catalogo inicial de empresas
- Base de datos nueva e independiente en SQLite

## Estructura

- `app/main.py`: entrada principal de FastAPI
- `app/core/config.py`: configuracion general
- `app/db.py`: conexion y bootstrap de SQLite
- `app/routers/`: endpoints iniciales
- `app/static/`: frontend web minimo
- `run_api.py`: arranque local

## Ejecutar

```powershell
py -3.11 run_api.py
```

Abrir:

- API: `http://127.0.0.1:8010`
- Docs: `http://127.0.0.1:8010/docs`
- Web: `http://127.0.0.1:8010/app`

La API escucha en `0.0.0.0:8010` para permitir acceso desde LAN/Tailscale.
El frontend prueba automaticamente estas rutas de API y usa la primera que responda:

- `http://127.0.0.1:8010`
- `http://192.168.1.105:8010`
- `http://100.69.142.19:8010`

Para cambiar esta lista sin tocar codigo, agrega en `config.json`:

```json
{
  "migracion_web_api_urls": [
    "http://127.0.0.1:8010",
    "http://100.69.142.19:8010"
  ]
}
```

## Pruebas pre-PAC

Para validar el armado simulado de CFDI sin guardar documentos, sin consumir
folios y sin tocar la cola de timbrado:

```powershell
python tests\smoke_timbrado_prepac.py
```

La prueba revisa:

- CFDI de factura sin descuento
- CFDI de factura con descuento
- PDF impreso con u ocultando columna `% Desc` segun corresponda
- REP 2.0 interno con `Pagos20`, `Totales` y documentos relacionados

Cuando una empresa tenga proveedor distinto de `SIMULADO` pero el PAC aun no
este integrado, el procesador de cola deja el movimiento en `BLOQUEADO_PAC`,
genera un XML candidato en `storage/cfdi/<EMPRESA>/<AÑO>/prepac/` y no consume
folio ni registra CFDI emitido.

El punto de integracion del PAC esta aislado en:

- `app/routers/timbrado_pac.py`

La logica de calculos, armado XML, addendas, folios y guardado queda fuera de
ese adaptador para no romper el flujo existente al conectar el proveedor real.

Cada intento de timbrado queda en la bitacora `timbrado_pac_intentos`, visible
desde la cola con el boton `Bitacora` o por API:

```text
GET /timbrado/pac-intentos?factura=<FOLIO>
```

El diagnostico pre-PAC valida CSD con OpenSSL cuando la empresa usa proveedor
real: existencia de `.cer`/`.key`, lectura del certificado, vigencia del CSD,
password de llave, RFC del certificado contra RFC emisor y correspondencia entre
certificado y llave cuando OpenSSL puede extraer ambas llaves publicas.

Cuando el CSD esta completo, el XML candidato pre-PAC ya incluye
`NoCertificado` y `Certificado` tomados del `.cer` del servidor. Antes de enviar
al PAC real, la API genera la cadena original CFDI 4.0 y el sellado
criptografico (`Sello`) localmente con la llave `.key` del servidor; esto no
altera los calculos ni consume folio si el PAC rechaza o no responde.

Los archivos CSD se pueden cargar desde Soporte > Timbrado > Configuracion por
Empresa con los botones `Subir CER` y `Subir KEY`. La API los guarda en
`storage/csd/<EMPRESA>/` y actualiza las rutas `csd_cer_path` y `csd_key_path`
de esa empresa.

En la misma pantalla, `Preparar FINKOK` selecciona FINKOK, activa timbrado,
deja modo pruebas encendido y apaga facturacion automatica para completar CSD y
credenciales sin activar produccion por accidente. Las respuestas de
configuracion no exponen passwords reales: si ya hay secreto guardado devuelven
`********`; al guardar ese marcador se conserva el secreto existente.

Para dejar el flujo diario en "solo cargar certificados", el servidor puede
tener credenciales FINKOK globales mediante variables de entorno o `config.json`:

```text
FINKOK_USUARIO
FINKOK_PASSWORD
FINKOK_URL          # opcional; si falta usa endpoint Finkok demo/produccion
FINKOK_CANCEL_URL   # opcional
```

Tambien acepta `PAC_USUARIO`/`PAC_PASSWORD` o `pac_usuario`/`pac_password`.
Cuando esas credenciales existen, no es necesario repetir usuario/password PAC
en cada empresa. El boton `Subir CSD completo` permite seleccionar el `.cer` y
`.key` juntos, guardar el password del CSD y correr diagnostico inmediato.
Si no quieres usar variables de entorno, el boton `PAC global` guarda esas
credenciales una sola vez en `storage/pac/global_config.json` con el password
enmascarado al consultarlo desde la UI.

La configuracion cuenta con diagnostico por empresa y diagnostico global:

```text
GET /timbrado/empresas/<EMPRESA>/diagnostico
GET /timbrado/empresas-diagnostico
```

Revisa datos fiscales, serie/folio, CSD, credenciales PAC, carpeta fiscal de
salida, espacio disponible y cola bloqueada. En Finkok la URL PAC es opcional
porque la API usa los endpoints demo/produccion por defecto. En la UI aparece
como `Diagnostico pre-PAC` y `Revisar todas`.

La misma validacion de preflight se ejecuta antes de enviar documentos a PAC
real. Si faltan CSD, credenciales, proveedor integrado o carpeta fiscal
escribible, el flujo se bloquea antes de sellar/timbrar y registra el intento
correspondiente sin consumir folio.

La fase de sellado pre-PAC ya genera cadena original CFDI 4.0 con el XSLT
oficial del SAT, cacheado en `storage/sat/xslt/`, y puede firmarla con
SHA256/RSA usando el CSD configurado. Para probar sin emitir:

```text
POST /timbrado/prexml-cfdi/<FACTURA>/sellar
```

En la UI esta disponible como `Probar sellado` dentro de Cola de Timbrado.
La respuesta indica si el XML quedo sellado y si faltan datos del proveedor
PAC; cuando el sellado es correcto, la UI permite abrir/guardar el XML sellado
sin emitir ni consumir folios.

Tambien se puede probar la configuracion del PAC por empresa sin enviar XML:

```text
POST /timbrado/empresas/<EMPRESA>/pac/probar
```

La UI lo expone como `Probar PAC`. Valida proveedor, URL, usuario/password y
que el endpoint HTTP responda; el resultado queda registrado en la bitacora
`timbrado_pac_intentos`.

Para ejecutar el checklist completo previo a produccion:

```text
POST /timbrado/empresas/<EMPRESA>/pac/prueba-integral
```

La UI lo expone como `Prueba integral PAC`. Guarda la configuracion actual,
revisa diagnostico fiscal/CSD, preflight PAC, conectividad cuando la empresa
esta en modo PAC real y muestra una muestra de facturas `BLOQUEADO_PAC`. No
envia XML, no timbra y no consume folios; solo indica si la empresa esta lista
para una prueba real controlada.

Para activar una empresa solo despues de pasar el checklist:

```text
POST /timbrado/empresas/<EMPRESA>/pac/activar-prueba-controlada
```

Requiere `{"confirmar": true}` y vuelve a ejecutar la prueba integral antes de
modificar la configuracion. Si falla, no cambia nada. Si pasa, activa
`timbrado_activo`, conserva el modo pruebas/produccion indicado y permite
decidir si `facturacion_automatica` queda activa o no. En la UI aparece como
`Activar prueba PAC`.

Para revisar el checklist estricto de produccion:

```text
POST /timbrado/empresas/<EMPRESA>/pac/checklist-produccion
```

Valida proveedor PAC real, modo produccion, CSD, preflight, conectividad del
PAC de produccion, folios y que no existan filas `BLOQUEADO_PAC` o `TIMBRANDO`
en cola. En la UI aparece como `Checklist produccion`.

Para activar produccion controlada:

```text
POST /timbrado/empresas/<EMPRESA>/pac/activar-produccion-controlada
```

Exige la frase exacta `ACTIVAR PRODUCCION <EMPRESA>`, ejecuta el checklist con
modo produccion forzado y solo si pasa guarda `modo_pruebas = false`,
`timbrado_activo = true` y la opcion elegida de `facturacion_automatica`.
Registra `PAC_ACTIVACION_PRODUCCION_OK` en bitacora. En la UI aparece como
`Activar produccion PAC`.

Para descargar evidencia del estado previo/posterior a produccion:

```text
GET /timbrado/empresas/<EMPRESA>/pac/reporte-produccion
```

Descarga un JSON con configuracion sin passwords, checklist de produccion, cola
e intentos PAC recientes. En la UI aparece como `Reporte produccion`.

Para ver el estado de produccion de todas las empresas:

```text
GET /timbrado/pac/estado-produccion
```

Resume checklist, proveedor, modo pruebas/produccion y cola por empresa. En la
UI aparece como `Estado global PAC`.

Para armar el semaforo operativo de pase a produccion:

```text
GET /timbrado/pac/pase-produccion
```

Incluye salud de MySQL legado, empresas operativas detectadas, etapas faltantes
y siguiente accion por empresa. En la UI aparece como `Pase produccion`.

Para auditar activaciones, desactivaciones y recuperaciones:

```text
GET /timbrado/pac/eventos-produccion
```

Lista eventos relevantes de bitacora PAC (`PAC_ACTIVACION_PRODUCCION_OK`,
`PAC_PRODUCCION_DESACTIVADA`, liberaciones y recuperaciones). Acepta `empresa`
y `limit`. En la UI aparece como `Eventos produccion`.

Para apagar produccion rapidamente sin borrar configuracion PAC:

```text
POST /timbrado/empresas/<EMPRESA>/pac/desactivar-produccion
```

Exige la frase exacta `DESACTIVAR PRODUCCION <EMPRESA>`, apaga facturacion
automatica, regresa `modo_pruebas = true` y permite elegir si `timbrado_activo`
se conserva para pruebas/manual o se apaga. Registra
`PAC_PRODUCCION_DESACTIVADA`. En la UI aparece como `Desactivar produccion`.

Para respaldar o restaurar configuracion de timbrado por empresa:

```text
POST /timbrado/empresas/<EMPRESA>/config-snapshots
GET /timbrado/empresas/<EMPRESA>/config-snapshots
GET /timbrado/empresas/<EMPRESA>/config-snapshots/<ID>
POST /timbrado/empresas/<EMPRESA>/config-snapshots/<ID>/restaurar
```

Las activaciones/desactivaciones PAC crean snapshots automaticos antes de
modificar configuracion. La restauracion exige la frase exacta
`RESTAURAR CONFIG <EMPRESA>`. En la UI aparece como `Snapshot config` y
`Ver snapshots`.

Para procesar una factura especifica con esa misma compuerta:

```text
POST /timbrado/cola/procesar-controlado/<FOLIO>
```

Primero identifica la empresa de la factura en cola, ejecuta `Prueba integral
PAC` y solo si todo esta listo llama al procesador de timbrado de ese folio. Si
la empresa no pasa el checklist, responde con error controlado y no modifica la
cola. En la UI aparece como `Procesar controlado` en filas pendientes.

La cola toma cada factura de forma atomica: si la factura ya tiene CFDI emitido
activo, reconcilia la fila como `TIMBRADA` y no vuelve a enviarla al PAC.
Tambien procesa una sola factura `TIMBRANDO` por empresa para evitar duplicidad
de folios si dos usuarios intentan timbrar al mismo tiempo; las demas quedan
`PENDIENTE` hasta que termine la actual o se use `Recuperar TIMBRANDO` para un
caso atorado.

Para procesar varias facturas seleccionadas con la misma compuerta:

```text
POST /timbrado/cola/procesar-controlado-lote
```

Recibe una lista JSON de folios. Primero ejecuta la vista PAC controlada de
todos; si uno no pasa, no procesa ninguno. Si todos pasan, procesa uno por uno
y detiene el lote si alguna factura queda bloqueada o sin procesar. En la UI
aparece como `Procesar seleccion controlada`.

Para revisar un lote seleccionado sin procesarlo:

```text
POST /timbrado/cola/prevalidar-controlado-lote
```

Recibe la misma lista JSON de folios, ejecuta `Vista PAC` para cada uno y
devuelve cuantas facturas estan listas y cuales tienen pendientes. No modifica
cola, no registra intentos y no consume folios. En la UI aparece como
`Prevalidar seleccion`.

Para recuperar una factura que quedo en `BLOQUEADO_PAC`:

```text
POST /timbrado/cola/liberar-bloqueo-pac/<FOLIO>
```

Ejecuta primero la `Prueba integral PAC` de la empresa. Si la empresa aun no
esta lista, no modifica la cola. Si pasa, cambia la factura a `PENDIENTE`,
limpia el error y descarta la ruta del XML pre-PAC anterior para que el proximo
procesamiento regenere XML con la configuracion actual. En la UI aparece como
`Liberar PAC` en filas `BLOQUEADO_PAC`.

Para liberar bloqueos PAC de toda una empresa:

```text
POST /timbrado/cola/liberar-bloqueos-pac
```

Recibe `empresa` y `max_items`. Recorre facturas `BLOQUEADO_PAC` de esa empresa,
ejecuta la misma validacion integral y libera solo las que pasen. En la UI se
usa desde el filtro de empresa como `Liberar bloqueos empresa`.

Para recuperar facturas que quedaron en `TIMBRANDO` por un corte o reinicio:

```text
POST /timbrado/cola/recuperar-timbrando
```

Recibe `empresa` opcional, `minutos` y `max_items`. Regresa a `PENDIENTE` las
filas `TIMBRANDO` cuyo `last_attempt_at` sea antiguo, registrando
`PAC_TIMBRANDO_RECUPERADO` en bitacora PAC. En la UI aparece como
`Recuperar TIMBRANDO`.

Para revisar lo que pasaria antes de procesar:

```text
POST /timbrado/cola/previsualizar-controlado/<FOLIO>
```

Genera una vista de solo lectura con estado de cola, diagnostico de empresa,
validacion CFDI, sellado local y paquete PAC seco. No envia XML, no registra
intentos y no consume folio. En la UI aparece como `Vista PAC`.

Para revisar la solicitud que se enviaria al PAC sin transmitirla:

```text
POST /timbrado/prexml-cfdi/<FACTURA>/paquete-pac
```

La UI lo expone como `Paquete PAC`. Devuelve hash SHA256, tamaño del XML,
longitud base64 y una vista previa segura de la solicitud por proveedor con
secretos enmascarados.

El adaptador Finkok ya puede enviar XML sellado al metodo SOAP `stamp` usando
`xml`, `username` y `password`, parsear `UUID`/`xml` de respuesta y persistir el
CFDI real. Si Finkok rechaza el comprobante o no responde, la cola queda en
`BLOQUEADO_PAC` con XML pre-PAC y no consume folio. `SW SAPRO` permanece como
adaptador pendiente hasta configurar el flujo exacto de token y endpoint de la
cuenta.

La cancelacion fiscal tambien pasa por el PAC cuando la empresa usa Finkok.
El endpoint:

```text
POST /timbrado/cfdi-emitidos/<FOLIO>/cancelar
```

envia UUID, motivo SAT, UUID sustituto cuando aplica, CSD del servidor y
credenciales al metodo SOAP `cancel` de Finkok antes de marcar el CFDI como
cancelado localmente y limpiar SAE en MIO. En `SIMULADO` conserva el flujo
local anterior. Si el PAC rechaza o no responde, la factura no se cancela en la
base local y se registra el intento como `CANCELACION_ERROR`.

Para consultar el estado SAT/PAC de un CFDI emitido:

```text
GET /timbrado/cfdi-emitidos/<FOLIO>/estatus-sat
POST /timbrado/cfdi-emitidos/<FOLIO>/sincronizar-estatus-sat
```

En Finkok usa `get_sat_status` con RFC emisor, RFC receptor, UUID y total
tomados del XML guardado en el servidor. La consulta se registra como
`ESTATUS_OK` o `ESTATUS_ERROR` en `timbrado_pac_intentos`. Esta consulta no
modifica el estatus local; solo informa lo que responde PAC/SAT.

La sincronizacion es una accion separada (`Sync SAT` en la UI). Solo marca el
CFDI local como `CANCELADA` y limpia SAE en MIO cuando SAT/PAC reporta
cancelacion. Si SAT/PAC reporta vigente o sin cancelacion, no modifica la base
local.

Para descargar el acuse de cancelacion desde PAC:

```text
GET /timbrado/cfdi-emitidos/<FOLIO>/acuse-cancelacion
```

En Finkok usa `get_receipt` con `type=C`, que corresponde al acuse de
cancelacion. La UI lo expone como `Acuse` dentro de CFDI emitidos cuando el
documento tiene UUID y esta cancelado.

Para descargar el acuse de recepcion del timbrado:

```text
GET /timbrado/cfdi-emitidos/<FOLIO>/acuse-recepcion
```

En Finkok usa `get_receipt` con `type=R`. La UI lo expone como
`Acuse recepcion` y `Refrescar recepcion` dentro de CFDI emitidos cuando el
documento tiene UUID.

Cuando el PAC devuelve el acuse, la API lo guarda en el servidor junto a los
archivos fiscales (`acuse_recepcion/` o `acuse_cancelacion/`) y registra la ruta en el CFDI emitido;
las descargas posteriores usan el archivo local si existe.
Para forzar una recuperacion nueva desde PAC se puede usar
`?refresh=1`; en la UI aparece como `Refrescar recepcion` o `Refrescar acuse`.

Para respaldo/auditoria de un CFDI emitido:

```text
GET /timbrado/cfdi-emitidos/<FOLIO>/paquete
```

Descarga un ZIP con XML fiscal cuando existe, PDF generado, acuses locales de
recepcion/cancelacion cuando existen, `metadata.json` y `bitacora_pac.json`. Si algun
archivo no puede generarse, el ZIP incluye un `.txt` de error sin modificar la
base.

El timbrado de cobranza tambien usa la capa PAC real cuando la empresa no esta
en `SIMULADO`:

```text
POST /timbrado/cobranza/<RECIBO_ID>/timbrar
```

Aplica para REP 2.0 y notas de credito. El XML se arma con la misma logica de
calculo existente, luego se sella con el CSD del servidor y se envia al PAC. Si
el PAC rechaza o no esta integrado, se guarda un XML pre-PAC en `prepac/`, se
registra `COBRANZA_BLOQUEADO_PAC` y no consume folio. En `SIMULADO` conserva el
comportamiento anterior.

Antes de emitir cobranza se puede probar sin persistir ni consumir folios:

```text
POST /timbrado/cobranza/<RECIBO_ID>/sellar
POST /timbrado/cobranza/<RECIBO_ID>/paquete-pac
```

La primera accion genera cadena/sello con el CSD del servidor. La segunda arma
el paquete PAC en modo seco con hash, tamano y vista previa segura. En la UI se
exponen como `Probar sellado` y `Paquete PAC` dentro del modal de comprobante de
cobranza.

## Conexion MySQL legado

Las consultas de Clientes y Productos usan la base `comandas_db` del sistema
legado. La conexion prueba los hosts de `mysql_hosts` en orden y recuerda el
primero que conecte correctamente para las siguientes consultas.

Orden actual tomado de `AspelAPI/config.json`:

- `192.168.1.105` red local
- `100.69.142.19` Tailscale / Server Galactico

Si no estas en la misma red, `192.168.1.105` fallara rapido y se intentara
`100.69.142.19` por Tailscale.

Para verificar desde la API cual host responde y si la conexion real a
`comandas_db` entra correctamente:

```text
GET /health/legacy-mysql
```

Devuelve los hosts probados, tipo de ruta (`local`/`tailscale`), estado de
socket y datos no sensibles de la conexion activa.

Aunque `config.json` incluya `127.0.0.1`, esta web no usa localhost como
respaldo por defecto para evitar conectarse a un MySQL local equivocado. Si
necesitas habilitarlo en una instalacion local, agrega:

```json
{
  "mysql_allow_local_fallback": true
}
```

## Credenciales iniciales

- Usuario: `admin`
- Password: `admin123`

Estas credenciales son temporales para la fase de migracion.
