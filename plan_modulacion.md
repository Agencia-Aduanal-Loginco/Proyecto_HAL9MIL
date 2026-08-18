# Plan de tareas: Notificación de extracción de contenedor (DODA/KASU) + envío a Modulación (BitacoraKasu)

Fuente: `/home/tony/.claude/plans/me-ayudas-a-generar-drifting-blossom.md` (spec ya revisada y aprobada
por el usuario). Este archivo descompone esa spec en tareas ejecutables de forma independiente por un
subagente implementador, siguiendo TDD.

## Contexto compartido (léelo antes de cualquier tarea)

- Proyecto Django `Proyecto_HAL9MIL`. Apps relevantes: `referencias/`, `core/`, `finanzas/`, `hal9mil/`
  (settings).
- `django.contrib.auth.models.User` es el modelo de usuario, sin `AUTH_USER_MODEL` custom.
- Patrón ETL batch: `sync_agent/sync_agent.py` (agente externo) hace `POST /api/sync/` →
  `referencias/sync_views.py::sync_endpoint`. `referencias/management/commands/import_firebird.py`
  hace lo mismo en batch local. Ambos deben mantenerse en paridad de columnas/queries.
- Patrón de email con adjunto ya resuelto en `finanzas/cuenta_gastos_envio.py` (SendGrid Web API,
  `Mail`/`Attachment`/`CustomArg`, adjunto PDF en base64, registro de envío en modelo de bitácora,
  manejo de bounces vía webhook). PDF con `reportlab.pdfgen.canvas`.
- Patrón de cliente HTTP saliente autenticado ya resuelto en `finanzas/pac_client.py`
  (`requests.post`, timeout `(10, 30)`, excepción propia, manejo de token) — replicar esa forma para
  `bitacorakasu_client.py`.
- `CVE_CONT_TIPO` (mapa `CVE_CONT` → `'20DC'/'40HC'/...`) ya vive en `referencias/models.py`.
- Correr tests con `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test <apps>` — la base Postgres
  gestionada en `.env` no tiene base `postgres` administrativa y Django no puede crear la base de test
  contra ella. **No cambiar `.env`**; sólo sobreescribir `DBURL` como variable de entorno al invocar el
  comando de test.
- `python manage.py makemigrations --check` debe pasar (sin migraciones faltantes) antes de dar por
  cerrada cualquier tarea que toque modelos.

## Task 1: Modelos `Doda` y `DodaReferencia`

Agregar a `referencias/models.py`:

```python
class Doda(models.Model):
    id_doda        = models.IntegerField(unique=True, db_index=True)   # SAAIO_DODA.ID_DODA
    num_doda       = models.CharField(max_length=34, blank=True)
    patente        = models.CharField(max_length=10, db_index=True)
    cve_caat       = models.CharField(max_length=6, blank=True, db_index=True)
    cve_capt       = models.CharField(max_length=20, blank=True)
    terminal_cve   = models.CharField(max_length=4, blank=True)   # SAAIC_REFIS.CVE_REFI
    terminal_nombre = models.CharField(max_length=70, blank=True) # SAAIC_REFIS.NOM_REFI
    fecha_doda     = models.DateTimeField(null=True, blank=True)  # FEC_DODAE
    fecha_baja     = models.DateTimeField(null=True, blank=True)  # FEC_BAJA
    notificado_en  = models.DateTimeField(null=True, blank=True)
    modulacion_enviada_en = models.DateTimeField(null=True, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['cve_caat', 'fecha_baja'])]

    def __str__(self):
        return self.num_doda or str(self.id_doda)


class DodaReferencia(models.Model):
    doda       = models.ForeignKey(Doda, on_delete=models.CASCADE, related_name='referencias_doda')
    referencia = models.ForeignKey(Referencia, on_delete=models.CASCADE, null=True, blank=True,
                                    related_name='dodas')
    num_refe   = models.CharField(max_length=15)   # por si aún no existe la Referencia localmente
    cons_id    = models.IntegerField()

    class Meta:
        unique_together = [('doda', 'cons_id')]
```

Pasos:
1. Añadir ambas clases a `referencias/models.py` (después de la clase `Referencia`/`Contenedor`
   existentes; usar el mismo estilo del archivo).
2. `python manage.py makemigrations referencias` y revisar el archivo generado.
3. Registrar `Doda` y `DodaReferencia` en `referencias/admin.py`: `list_display` con
   (`id_doda`, `num_doda`, `cve_caat`, `patente`, `terminal_nombre`, `fecha_doda`, `fecha_baja`,
   `notificado_en`, `modulacion_enviada_en`) para `Doda`; filtros por `cve_caat` y `fecha_baja`.
4. Tests en `referencias/tests.py`: creación básica de `Doda`/`DodaReferencia`, `unique_together` de
   `DodaReferencia` (mismo `doda`+`cons_id` no se puede duplicar), `related_name` funcionando
   (`referencia.dodas`, `doda.referencias_doda`).

Verificación: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias` y
`python manage.py makemigrations --check`.

## Task 2: `PerfilUsuario` y resolución de destinatario por capturista

Depende de: ninguna tarea previa (independiente de Task 1, toca `core/` en vez de `referencias/`).

Agregar a `core/models.py` (hoy vacío salvo el boilerplate de Django):

```python
class PerfilUsuario(models.Model):
    user             = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                             related_name='perfil')
    cve_capturista   = models.CharField(max_length=20, unique=True, blank=True, db_index=True)
    email_alterno    = models.EmailField(blank=True)  # override opcional del email de User
```

Nuevo `core/capturistas.py`:

```python
def resolver_destinatario(cve_capt: str) -> tuple[str, str] | None:
    """Devuelve (email, nombre) del capturista, o None si no hay perfil vinculado."""
```

Lógica de `resolver_destinatario`:
1. Buscar `PerfilUsuario.objects.select_related('user').get(cve_capturista=cve_capt)`.
2. Si existe: devolver `(perfil.email_alterno or perfil.user.email, perfil.user.get_full_name() or
   perfil.user.username)`.
3. Si no existe (`PerfilUsuario.DoesNotExist`): loggear `logger.warning(...)` indicando que falta
   vincular ese `cve_capt`, y devolver `None` si `settings.MODULACION_FALLBACK_EMAILS` está vacío, o
   `(settings.MODULACION_FALLBACK_EMAILS[0], cve_capt)` si hay al menos un fallback configurado —
   dejar la interfaz de retorno como `tuple[str, str] | None` consistente en ambos casos (documentar en
   el propio código con un comentario corto si el comportamiento de fallback no es obvio).
4. Agregar a `hal9mil/settings.py`: `MODULACION_FALLBACK_EMAILS = os.getenv('MODULACION_FALLBACK_EMAILS',
   '').split(',')` (filtrar cadenas vacías del resultado del split).

Pasos:
1. Modelo + migración (`python manage.py makemigrations core`).
2. `core/capturistas.py` con la función y su lógica.
3. Setting `MODULACION_FALLBACK_EMAILS` en `hal9mil/settings.py`.
4. Registrar `PerfilUsuario` en `core/admin.py` (o inline en un `UserAdmin` re-registrado) para que un
   administrador asigne `cve_capturista` a cada usuario.
5. Tests en `core/tests.py` (crearlo si no existe, siguiendo el estilo de `referencias/tests.py`):
   `resolver_destinatario` con `PerfilUsuario` existente (devuelve email/nombre correctos, respeta
   `email_alterno` si está seteado), sin `PerfilUsuario` y sin fallback configurado (devuelve `None`),
   sin `PerfilUsuario` y con fallback configurado (devuelve el fallback).

Verificación: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test core` y
`python manage.py makemigrations --check`.

## Task 3: Extender el pipeline de sync para traer DODA

Depende de: Task 1 (modelos `Doda`/`DodaReferencia` ya deben existir).

Interfaces de Task 1 a reutilizar: `Doda(id_doda, num_doda, patente, cve_caat, cve_capt, terminal_cve,
terminal_nombre, fecha_doda, fecha_baja, ...)`, `DodaReferencia(doda, referencia, num_refe, cons_id)`.

Pasos:
1. Agregar `CVE_CAAT_KASU = '3B74'` a `hal9mil/settings.py`.
2. `referencias/sync_views.py`: nueva función `_upsert_dodas(dodas, stats, error_msgs)`, análoga a la
   función `_upsert_contenedores` ya existente en el mismo archivo (mismo estilo de manejo de errores y
   de `stats` dict). Por cada dict de entrada (`id_doda`, `num_doda`, `patente`, `cve_caat`, `cve_capt`,
   `terminal_cve`, `terminal_nombre`, `fecha_doda`, `fecha_baja`, y una lista `referencias: [{num_refe,
   cons_id}, ...]`):
   - `Doda.objects.update_or_create(id_doda=..., defaults={...})`.
   - Por cada referencia del DODA: `DodaReferencia.objects.update_or_create(doda=doda, cons_id=...,
     defaults={'num_refe': ..., 'referencia': Referencia.objects.filter(num_refe=...).first()})`.
   - Devuelve la lista de instancias `Doda` con `created=True` (para que quien la llame pueda
     encadenar el disparo de notificación en una tarea futura — en esta tarea basta con devolver la
     lista, no hace falta invocar nada más).
   - Registrar `_upsert_dodas` en el flujo de `sync_endpoint` al mismo nivel que las llamadas existentes
     a `_upsert_contenedores`/`_upsert_referencias`/`_upsert_guias`, leyendo el bloque `"dodas"` del
     payload JSON si está presente (usar `payload.get('dodas', [])` para no romper compatibilidad con
     agentes viejos que aún no mandan ese bloque).
3. `sync_agent/sync_agent.py`: nueva query SQL que hace `JOIN` de `SAAIO_DODA` + `SAAIO_DODADO` +
   `SAAIO_IDEPED` (`CVE_IDEN='CR'`) + `SAAIC_REFIS`, con `WHERE CVE_CAAT = '3B74' AND FEC_BAJA IS NULL`,
   siguiendo el estilo de las queries ya existentes en ese archivo (mismo manejo de conexión `fdb`).
   Agregar un bloque `"dodas": [...]` al payload existente del `POST /api/sync/`, mismo nivel que
   `"referencias"`/`"contenedores"`/`"guias"`.
4. `referencias/management/commands/import_firebird.py`: mismo query/columnas que el paso 3, para
   paridad entre el agente y el import batch local.
5. Nota: revisar si `peso_bruto` (toneladas) ya existe en `Referencia`/`Contenedor`. Si no existe,
   agregarlo en esta misma tarea siguiendo el mismo patrón usado para otros campos numéricos del modelo
   (columna origen candidata: `CTRAO_EMBAR.PES_BRUT` o `SAAIO_PEDIME.PES_BRUT` — usar la que ya esté
   accesible en las queries existentes de `sync_agent.py`/`import_firebird.py`; si ninguna está
   disponible sin JOIN adicional, documentarlo como `NEEDS_CONTEXT` en el reporte en vez de adivinar).
6. Tests en `referencias/tests.py` para `_upsert_dodas`: creación de `Doda`+`DodaReferencia` nuevos,
   actualización (`update_or_create`) de un `Doda` existente, filtro de `CVE_CAAT` (verificar que la
   función no descarta registros por su cuenta — el filtro real ocurre en la query origen, así que el
   test de `_upsert_dodas` sólo verifica que procesa lo que recibe), y ligado correcto de
   `DodaReferencia.referencia` cuando ya existe una `Referencia` con ese `num_refe` en la base local.

Verificación: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias` y
`python manage.py makemigrations --check` (por si el campo `peso_bruto` requirió migración).

## Task 4: Cliente HTTP para BitacoraKasu

Depende de: ninguna tarea previa (módulo nuevo autocontenido).

Pasos:
1. Agregar a `hal9mil/settings.py`:
   ```python
   BITACORAKASU_MODULACION_URL = os.getenv('BITACORAKASU_MODULACION_URL', '')
   BITACORAKASU_API_TOKEN      = os.getenv('BITACORAKASU_API_TOKEN', '')
   ```
2. Nuevo `referencias/bitacorakasu_client.py`, mismo patrón que `finanzas/pac_client.py` (leerlo primero
   para copiar la forma exacta: manejo de timeout, excepciones propias, construcción de headers):
   - Excepción propia `BitacoraKasuError(Exception)`.
   - Función `enviar_modulacion(payload: dict) -> dict` que hace
     `requests.post(settings.BITACORAKASU_MODULACION_URL, json=payload, timeout=(10, 30),
     headers={'Authorization': f'Token {settings.BITACORAKASU_API_TOKEN}'})`, levanta
     `BitacoraKasuError` en timeout/connection error/status >= 400 (con el cuerpo de la respuesta en el
     mensaje de la excepción cuando esté disponible), y devuelve el JSON de la respuesta en éxito.
3. Tests en `referencias/test_modulacion.py` (crear el archivo): mock de `requests.post` (sin llamadas
   de red reales) cubriendo: éxito (200, devuelve el JSON), error HTTP (4xx/5xx → `BitacoraKasuError`),
   timeout/`requests.exceptions.RequestException` → `BitacoraKasuError`, y que el header
   `Authorization` se arma correctamente con el token de settings.

Verificación: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias`.

## Task 5: `EnvioModulacion` + `referencias/modulacion.py` (correo + push) + wiring al sync

Depende de: Task 1 (`Doda`, `DodaReferencia`), Task 2 (`core.capturistas.resolver_destinatario`),
Task 3 (`_upsert_dodas` ya integrado en `sync_endpoint`), Task 4 (`bitacorakasu_client.enviar_modulacion`).

Interfaces a reutilizar (ya existentes tras las tareas previas):
- `core.capturistas.resolver_destinatario(cve_capt: str) -> tuple[str, str] | None`
- `referencias.bitacorakasu_client.enviar_modulacion(payload: dict) -> dict`,
  `referencias.bitacorakasu_client.BitacoraKasuError`
- `Doda`, `DodaReferencia` (Task 1), `Referencia.contenedores`, `Contenedor.tipo`/`CVE_CONT_TIPO`
  (ya existentes en `referencias/models.py`)
- `_upsert_dodas` (Task 3) devuelve la lista de `Doda` con `created=True`.

Pasos:
1. Modelo `EnvioModulacion` en `referencias/models.py`: `doda` (FK a `Doda`), `estado`
   (`CharField` con choices `ENVIADO`/`ERROR`, o separar en `email_estado`/`push_estado` si el push por
   contenedor puede fallar independientemente del correo — usar dos campos de estado separados,
   `email_estado` y `push_estado`, porque el diseño describe reintentos independientes de email y de
   push), `sg_message_id` (opcional), `error_detalle` (`TextField`, blank), `created_at`/`updated_at`.
   Migración correspondiente. Registrar en `referencias/admin.py` con filtros por `email_estado`/
   `push_estado`.
2. Nuevo `referencias/modulacion.py` con `procesar_dodas_nuevas(dodas_creadas)`:
   - Por cada `Doda` en `dodas_creadas`:
     a. `destinatario = core.capturistas.resolver_destinatario(doda.cve_capt)`.
     b. Generar PDF "Pedimento + DODA para imprimir" con `reportlab.pdfgen.canvas` — leer primero
        `finanzas/cuenta_gastos_envio.py` para copiar el mismo patrón de generación (tamaño de página,
        helpers de texto, etc.). Contenido: `num_doda`, `terminal_nombre`, y por cada
        `DodaReferencia.referencia` de ese DODA: `num_pedimento`, `nombre_cliente`, lista de
        contenedores (`Referencia.contenedores`).
     c. Enviar correo reusando el patrón SendGrid Web API de `enviar_cuenta_gastos` en
        `finanzas/cuenta_gastos_envio.py` (`Mail`/`Attachment`/`CustomArg`, adjuntando el PDF en
        base64) al destinatario resuelto en (a), pidiendo iniciar la solicitud de extracción del
        contenedor. Si `destinatario` es `None`, registrar `EnvioModulacion.email_estado='ERROR'` con
        detalle "sin destinatario resuelto" y continuar con el push (no depende del email).
        Crear/actualizar el registro `EnvioModulacion` correspondiente con el resultado
        (`ENVIADO`/`ERROR`, `sg_message_id` si está disponible).
     d. Push a BitacoraKasu: un `POST` (vía `bitacorakasu_client.enviar_modulacion`) por cada
        contenedor asociado a las referencias del DODA, con el payload exacto:
        ```json
        {
          "agencia": "LOGINCO",
          "terminal_portuaria": "<Doda.terminal_nombre>",
          "tipo_contenedor": "<CVE_CONT_TIPO[Contenedor.tipo]>",
          "peso_toneladas": "<peso de la referencia>",
          "contenedor": "<Contenedor.num_cont>",
          "cliente": "<Referencia.nombre_cliente>",
          "num_pedimento": "<Referencia.num_pedimento>",
          "num_doda": "<Doda.num_doda>",
          "idempotency_key": "<Doda.id_doda>:<Contenedor.num_cont>"
        }
        ```
        `idempotency_key` es una clave estable (`f"{doda.id_doda}:{contenedor.num_cont}"`) para que
        BitacoraKasu pueda distinguir un reenvío genuino de un duplicado — el retry
        (`reintentar_envio`/`reintentar_modulacion`) ocurre a nivel de DODA completa, así que si 1 de 5
        contenedores falla, los 5 se re-postean en el siguiente reintento.
        Capturar `BitacoraKasuError` por cada intento sin abortar los demás contenedores; actualizar
        `EnvioModulacion.push_estado` con el resultado agregado (si algún contenedor falla, el estado
        general queda `ERROR` con el detalle de cuáles fallaron).
     e. Ninguna falla de email o de push debe propagar la excepción fuera de
        `procesar_dodas_nuevas` — capturar, loggear, y seguir con el siguiente `Doda`.
   - Marcar `doda.notificado_en`/`doda.modulacion_enviada_en` con el timestamp correspondiente cuando
     cada paso tenga éxito.
3. Wiring: en `referencias/sync_views.py::sync_endpoint`, justo después de llamar a `_upsert_dodas`
   dentro del mismo `transaction.atomic()`, encolar `transaction.on_commit(lambda:
   modulacion.procesar_dodas_nuevas(dodas_creadas))` (import diferido si hace falta evitar import
   circular) para que el email/push corran después del commit y no bloqueen ni corrompan el sync si
   Firebird tiene más lotes.
4. Tests en `referencias/test_modulacion.py`: mock de `requests.post` (push) y mock del cliente SendGrid
   (mismo patrón que `finanzas/test_cuenta_gastos_envio.py` — leerlo primero) para `procesar_dodas_nuevas`
   cubriendo: caso feliz (email enviado + N pushes exitosos, `EnvioModulacion` en estado `ENVIADO`/
   `ENVIADO`), fallo de email no bloquea el push, fallo de push en un contenedor no bloquea los demás ni
   propaga excepción, y sin destinatario resuelto (fallback aplicado o `EnvioModulacion` en `ERROR` con
   detalle). Sin llamadas reales de red en ningún test.

Verificación: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias` y
`python manage.py makemigrations --check`.

## Task 6: Management command `reintentar_modulacion`

Depende de: Task 5 (`EnvioModulacion`, `referencias/modulacion.py`).

Pasos:
1. Nuevo `referencias/management/commands/reintentar_modulacion.py`: recorre
   `EnvioModulacion.objects.filter(Q(email_estado='ERROR') | Q(push_estado='ERROR'))` y reintenta,
   reusando la lógica de `referencias/modulacion.py` (refactorizar si hace falta para exponer una
   función reutilizable a nivel de un solo `EnvioModulacion`/`Doda`, en vez de duplicar la lógica de
   envío). Imprimir un resumen (`X reintentados, Y con éxito, Z siguen en error`) al terminar.
2. Tests: crear `EnvioModulacion` en estado `ERROR` con mocks de email/push, correr el command, y
   verificar que actualiza los estados según el resultado mockeado.

Verificación: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias`.

## Fuera de alcance (no crear tareas para esto)

El endpoint receptor en BitacoraKasu (`/home/tony/Developer/BitacoraKasu`) y su modelo `Modulacion` se
planean por separado en ese repositorio. Aquí sólo se implementa el lado emisor (HAL9MIL) y se respeta
el contrato de payload de la Task 5.
