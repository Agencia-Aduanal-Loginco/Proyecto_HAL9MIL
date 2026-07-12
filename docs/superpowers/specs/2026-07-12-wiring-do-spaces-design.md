# Wiring de DigitalOcean Spaces para Archivos de Usuario — Diseño

**Fecha:** 2026-07-12
**Estado:** Aprobado por el usuario (brainstorming 2026-07-12)

## Problema

Un commit de otra sesión ("Implementacion de Guardado enn DO Space") dejó
escrito todo el código de storage para DigitalOcean Spaces
(`hal9mil/storage_backends.py`: `MediaStorage`, `StaticStorage`,
`ReportesStorage`, `SecureMediaStorage`, señales de borrado automático,
utilidades) y las credenciales reales ya están en `.env`
(`DO_SPACES_ACCESS_KEY`, `DO_SPACES_SECRET_KEY`, `DO_SPACES_BUCKET_NAME`,
`DO_SPACES_ENDPOINT_URL`, `DO_SPACES_REGION`, `DO_SPACES_CDN_ENDPOINT`,
`USE_SPACES`). Pero **nada de esto está conectado**: `hal9mil/settings.py` no
define ningún `AWS_*` (que es lo que `django-storages`/`S3Boto3Storage` lee),
así que el callable `media_storage()` (agregado en el plan de "carga de
facturas de cliente") siempre cae a disco local, incluso en producción.

Objetivo: que los archivos que suben los usuarios (hoy, `XMLProveedor.xml_file`
y `XMLProveedor.pdf_file` en el módulo Finanzas — el único lugar del proyecto
con `FileField`/`ImageField`) se guarden realmente en el Space de DO en
producción, de forma privada y segura.

## Decisiones tomadas (con el usuario)

| Decisión | Elección |
|----------|----------|
| Acceso a los archivos | **Privado con URLs firmadas** — nunca público. Los XML/PDF de facturas tienen RFC, montos y nombres de clientes. |
| Entornos activos | **Solo producción** (`USE_SPACES=True`). Local/dev sigue usando disco (`media/`), sin cambios de flujo para desarrolladores. |
| Señales globales `post_delete`/`pre_save` | **Acotarlas** a `sender=XMLProveedor` (hoy corren, sin filtro, en cada save/delete de cualquier modelo del proyecto). |
| Verificación | **Prueba real autorizada**: subir + verificar + borrar un archivo de prueba contra el bucket real de producción, usando las credenciales de `.env`, como parte de la verificación end-to-end del plan (no como test automatizado en CI). |

## Arquitectura

### Componente 1: Wiring de settings

`hal9mil/settings.py` gana un bloque nuevo, después de `MEDIA_ROOT` (línea
~89), que lee las variables `DO_SPACES_*` ya presentes en `.env` (mismo
patrón `os.getenv(...)` que el resto del archivo) y, solo si
`USE_SPACES` es verdadero, define las settings estándar de `django-storages`:

- `AWS_ACCESS_KEY_ID` ← `DO_SPACES_ACCESS_KEY`
- `AWS_SECRET_ACCESS_KEY` ← `DO_SPACES_SECRET_KEY`
- `AWS_STORAGE_BUCKET_NAME` ← `DO_SPACES_BUCKET_NAME`
- `AWS_S3_ENDPOINT_URL` ← `DO_SPACES_ENDPOINT_URL`
- `AWS_S3_REGION_NAME` ← `DO_SPACES_REGION`
- `AWS_S3_CUSTOM_DOMAIN` ← `DO_SPACES_CDN_ENDPOINT` (opcional, puede quedar vacío)

Cuando `USE_SPACES` es falso (default en `.env` local), ninguna de estas
settings se define — `media_storage()` (que ya chequea
`getattr(settings, 'AWS_STORAGE_BUCKET_NAME', None)`) sigue cayendo a
`default_storage` exactamente como hoy. **No se toca `STATICFILES_STORAGE`**
(sigue en whitenoise) — el pedido del usuario es solo sobre archivos que
suben los usuarios, no assets estáticos; `StaticStorage` queda sin usar, sin
tocarla.

`requirements.txt` gana dos líneas (`django-storages==1.14.6`,
`boto3==1.43.46` — versiones ya instaladas y verificadas en este entorno) en
la sección de producción, para que un `pip install -r requirements.txt` en un
entorno nuevo quede completo.

### Componente 2: Privacidad de archivos

`MediaStorage` (en `hal9mil/storage_backends.py`) cambia `default_acl` de
`'public-read'` a `'private'` y gana `querystring_auth = True` +
`querystring_expire` (reusa el valor que ya tenía `SecureMediaStorage`: 3600
segundos). Con esto, `MediaStorage` y `SecureMediaStorage` quedan
funcionalmente idénticas — `SecureMediaStorage` se elimina (no tiene
consumidores hoy; grep confirma cero referencias fuera de su propia
definición) para no dejar dos clases redundantes.

Ningún campo de modelo cambia su `storage=` — `XMLProveedor.xml_file`/
`pdf_file` ya usan el callable `media_storage` (de la tarea de storage del
plan de carga de facturas de cliente). Lo único que cambia es qué instancia
produce ese callable cuando el bucket está configurado.

La vista `finanzas.views.xml_proveedor_ver_pdf` no cambia: sigue abriendo el
archivo con `.open('rb')` detrás de `@modulo_required('Finanzas')`. Con
`S3Boto3Storage`, `.url` (usado por otros templates que enlazan al PDF) ya
genera automáticamente una URL firmada con expiración cuando
`querystring_auth=True` — no requiere código adicional.

### Componente 3: Señales acotadas a XMLProveedor

`@receiver(post_delete)` → `@receiver(post_delete, sender=XMLProveedor)` y
`@receiver(pre_save)` → `@receiver(pre_save, sender=XMLProveedor)` en
`hal9mil/storage_backends.py`. Esto requiere importar `XMLProveedor` dentro
del receptor (import diferido, para evitar import circular entre
`hal9mil.storage_backends` y `finanzas.models`, que ya importa
`hal9mil.storage_backends`).

`delete_file_from_storage(file_path, storage_class=MediaStorage)` cambia su
default a usar el callable `media_storage` en vez de la clase `MediaStorage`
fija — así el borrado respeta el mismo criterio de entorno que el guardado
(hoy: en local borra intentando `MediaStorage()` aunque el archivo se haya
guardado en disco, lo cual falla silenciosamente por el `except Exception`
que ya existe — deuda documentada en el plan anterior que esta tarea repara
de paso).

### Fuera de alcance

- `StaticStorage`, `ReportesStorage`, `upload_ticket_photo`,
  `upload_reporte_excel`, `FileUploadMiddleware`,
  `optimize_image_for_storage`, `copy_file_to_reportes_storage`: código ya
  escrito por la otra sesión, sin consumidores activos hoy (no hay modelos de
  ticket/foto ni middleware registrado). No se tocan ni se eliminan — quedan
  igual, listos para cuando se necesiten.
- Migrar archivos ya guardados en disco local hacia Spaces (no hay archivos
  reales en producción todavía bajo este flujo, según lo conversado).
- Agregar `FileField` a otros modelos/apps — no existen hoy fuera de Finanzas.

## Manejo de errores

- Si `USE_SPACES=True` pero falta alguna variable `DO_SPACES_*` requerida
  (access key, secret, bucket), `django-storages` fallará al primer intento
  de guardado con un error de boto3 — comportamiento estándar, no se agrega
  manejo especial (es un error de configuración de despliegue, debe fallar
  ruidosamente).
- El `except Exception` ya existente en `delete_file_from_storage` se
  mantiene (borra "mejor esfuerzo", loguea si falla) — no es parte de este
  cambio.

## Pruebas

En `finanzas/tests.py` o archivo nuevo `hal9mil/test_storage_backends.py`:

1. Con `override_settings(AWS_STORAGE_BUCKET_NAME='test-bucket')`,
   `media_storage()` retorna una instancia de `MediaStorage`.
2. Sin `AWS_STORAGE_BUCKET_NAME` (o `None`), `media_storage()` retorna
   `default_storage`.
3. `MediaStorage.default_acl == 'private'` y
   `MediaStorage.querystring_auth is True` (verificación de atributos de
   clase, sin red).
4. Las señales `post_delete`/`pre_save` en `storage_backends.py` tienen
   `sender=XMLProveedor` (inspección de `receiver.__self__`/dispatch_uid o,
   más simple, un test funcional: guardar/borrar una instancia de otro modelo
   con archivo — no existe ninguno hoy, así que este caso se cubre
   indirectamente confirmando que `Referencia`/`Pago`/etc. no disparan
   `delete_file_from_storage` — se puede verificar con `unittest.mock.patch`
   sobre esa función y observar que NO se llama al guardar un modelo sin
   campos de archivo).
5. Suite completa de `finanzas` sigue en verde (regresión).

**Verificación real (no automatizada, ejecutada una vez por el asistente con
autorización ya dada):** con `USE_SPACES=True` y las credenciales reales,
subir un archivo pequeño vía `MediaStorage().save(...)`, confirmar
`storage.url(...)` responde 200 con la URL firmada, y borrarlo con
`storage.delete(...)`. Resultado documentado en el plan de implementación,
no en el repositorio (no se comitea ningún script que use credenciales reales
en texto plano más allá de leerlas de `.env`, que ya está en
`.gitignore`).
