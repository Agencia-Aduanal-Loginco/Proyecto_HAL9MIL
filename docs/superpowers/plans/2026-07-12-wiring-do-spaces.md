# Wiring de DigitalOcean Spaces — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Conectar las credenciales de DigitalOcean Spaces (ya presentes en `.env`) a Django settings, para que en producción (`USE_SPACES=True`) los archivos de `XMLProveedor` se guarden en Spaces de forma privada con URLs firmadas, mientras local/dev sigue usando disco.

**Architecture:** Bloque nuevo en `hal9mil/settings.py` que mapea `DO_SPACES_*` → `AWS_*` (lo que `django-storages` espera) solo si `USE_SPACES` es verdadero. `MediaStorage` pasa a privada con URLs firmadas (elimina `SecureMediaStorage`, redundante). Las señales de borrado automático se acotan a `sender=XMLProveedor`. Sin cambios de modelo ni migraciones — `XMLProveedor.xml_file`/`pdf_file` ya usan el callable `media_storage()`.

**Tech Stack:** Django 6.0.5, django-storages 1.14.6, boto3 1.43.46, python-dotenv, PostgreSQL.

**Spec:** `docs/superpowers/specs/2026-07-12-wiring-do-spaces-design.md`

## Global Constraints

- Directorio de trabajo: `/home/tony/Developer/Proyecto_HAL9MIL/` — entorno virtual `.venv/`, activar con `source .venv/bin/activate`.
- Crear una rama de feature antes de empezar (no trabajar en `main`).
- Tests con `django.test.TestCase`. BD de tests es Postgres remota: correr siempre con `--keepdb`.
- **Nunca imprimir ni loguear el valor de `DO_SPACES_SECRET_KEY` ni `DO_SPACES_ACCESS_KEY`** en ningún test, script o commit. `.env` ya está en `.gitignore` — no debe tocarse ni commitearse.
- El único task que toca la red real (Task 4) requiere las credenciales de `.env` tal cual están hoy — no modificarlas.
- Sin cambios a `STATICFILES_STORAGE` ni a `StaticStorage`/`ReportesStorage`/otras utilidades sin consumidores — fuera de alcance.

---

## File Map

| Archivo | Acción | Responsabilidad |
|---------|--------|-----------------|
| `requirements.txt` | Modificar | Pinear `django-storages` y `boto3` |
| `hal9mil/settings.py` | Modificar | Wiring `DO_SPACES_*` → `AWS_*`, gateado por `USE_SPACES` |
| `hal9mil/storage_backends.py` | Modificar | `MediaStorage` privada, eliminar `SecureMediaStorage`, señales pasan a funciones planas, `delete_file_from_storage` usa el callable |
| `finanzas/apps.py` | Modificar | `ready()` conecta las señales a `sender=XMLProveedor` (evita import circular) |
| `hal9mil/test_storage_backends.py` | Crear | Tests de `media_storage()`, ACL/querystring, señales acotadas |
| `docs/superpowers/plans/2026-07-12-wiring-do-spaces.md` | Modificar | Registro de la verificación real (Task 4) |

---

## Task 1: Wiring de settings + dependencias

**Files:**
- Modify: `requirements.txt`
- Modify: `hal9mil/settings.py:87-89` (después de `MEDIA_ROOT`)

**Interfaces:**
- Produces: `settings.AWS_ACCESS_KEY_ID`, `settings.AWS_SECRET_ACCESS_KEY`, `settings.AWS_STORAGE_BUCKET_NAME`, `settings.AWS_S3_ENDPOINT_URL`, `settings.AWS_S3_REGION_NAME`, `settings.AWS_S3_CUSTOM_DOMAIN` — definidos SOLO cuando `USE_SPACES` es verdadero. Usado por `hal9mil.storage_backends.media_storage()` (ya existe) en Task 2/3.

- [ ] **Step 1: Agregar las dependencias a `requirements.txt`**

En la sección `# producción` (junto a `gunicorn`, `psycopg2-binary`, `whitenoise`, `dj-database-url`), agregar:

```
django-storages==1.14.6
boto3==1.43.46
```

- [ ] **Step 2: Agregar el bloque de wiring en `hal9mil/settings.py`**

Justo después de estas líneas existentes (~línea 89):

```python
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
```

Agregar:

```python
# ── DigitalOcean Spaces (solo si USE_SPACES=True; si no, disco local) ────────
USE_SPACES = os.getenv('USE_SPACES', 'False').lower() in ('true', '1', 'yes')

if USE_SPACES:
    AWS_ACCESS_KEY_ID = os.getenv('DO_SPACES_ACCESS_KEY', '')
    AWS_SECRET_ACCESS_KEY = os.getenv('DO_SPACES_SECRET_KEY', '')
    AWS_STORAGE_BUCKET_NAME = os.getenv('DO_SPACES_BUCKET_NAME', '')
    AWS_S3_ENDPOINT_URL = os.getenv('DO_SPACES_ENDPOINT_URL', '')
    AWS_S3_REGION_NAME = os.getenv('DO_SPACES_REGION', '')
    AWS_S3_CUSTOM_DOMAIN = os.getenv('DO_SPACES_CDN_ENDPOINT', '') or None
```

No se toca `STATICFILES_STORAGE` (línea 85) ni ninguna otra línea del archivo.

- [ ] **Step 3: Verificar que Django arranca sin errores con `USE_SPACES` en ambos estados**

```bash
source .venv/bin/activate
python manage.py check
```

Esperado: `System check identified no issues (0 silenced).` (con el `.env` actual, `USE_SPACES` está en el valor que ya tenga — no lo cambiamos en este step).

Luego, simulando producción sin tocar `.env`:

```bash
USE_SPACES=True python manage.py shell -c "
from django.conf import settings
print(settings.AWS_STORAGE_BUCKET_NAME)
print(bool(settings.AWS_ACCESS_KEY_ID))
"
```

Esperado: imprime el nombre del bucket real (confirma que lee `.env`) y `True`. **No imprimir `AWS_SECRET_ACCESS_KEY` ni `AWS_ACCESS_KEY_ID` completos** — el `bool(...)` es intencional para no exponer el valor.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt hal9mil/settings.py
git commit -m "feat(hal9mil): wiring de variables DO_SPACES_* a settings de django-storages"
```

---

## Task 2: MediaStorage privada + eliminar SecureMediaStorage

**Files:**
- Modify: `hal9mil/storage_backends.py:29-53` (clase `MediaStorage`)
- Modify: `hal9mil/storage_backends.py:73-82` (eliminar clase `SecureMediaStorage`)
- Create: `hal9mil/test_storage_backends.py`

**Interfaces:**
- Consumes: `settings.AWS_STORAGE_BUCKET_NAME` (Task 1)
- Produces: `MediaStorage` con `default_acl='private'`, `querystring_auth=True`, `querystring_expire=3600` — usada por `media_storage()` (Task 3) y por cualquier código futuro.

- [ ] **Step 1: Escribir el test que falla**

Crear `hal9mil/test_storage_backends.py`:

```python
from django.test import TestCase

from .storage_backends import MediaStorage


class MediaStoragePrivacyTest(TestCase):
    def test_default_acl_es_privado(self):
        self.assertEqual(MediaStorage.default_acl, 'private')

    def test_querystring_auth_habilitado(self):
        self.assertTrue(MediaStorage.querystring_auth)

    def test_querystring_expire_una_hora(self):
        self.assertEqual(MediaStorage.querystring_expire, 3600)
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
python manage.py test hal9mil.test_storage_backends --keepdb --verbosity=2
```

Esperado: `test_default_acl_es_privado` falla (`'public-read' != 'private'`); `test_querystring_auth_habilitado` falla (`AttributeError` o `False`); `test_querystring_expire_una_hora` falla (`AttributeError`).

- [ ] **Step 3: Modificar `MediaStorage` en `hal9mil/storage_backends.py`**

Reemplazar la clase completa (líneas 29-53):

```python
class MediaStorage(S3Boto3Storage):
    """Storage personalizado para archivos media — privado, con URLs firmadas."""
    location = 'media'
    default_acl = 'private'
    file_overwrite = False  # No sobreescribir archivos media
    querystring_auth = True
    querystring_expire = 3600  # URLs firmadas expiran en 1 hora

    def __init__(self, *args, **kwargs):
        kwargs['custom_domain'] = getattr(settings, 'AWS_S3_CUSTOM_DOMAIN', None)
        super().__init__(*args, **kwargs)
        logger.info("📸 MediaStorage inicializado para DigitalOcean Spaces")

    def _save(self, name, content):
        """Personaliza el guardado de archivos media"""
        # Agregar timestamp para evitar colisiones
        import os

        # Separar nombre y extensión
        base_name, ext = os.path.splitext(name)

        # Agregar timestamp (hora de México)
        timestamp = ahora_mexico().strftime('%Y%m%d_%H%M%S')
        new_name = f"{base_name}_{timestamp}{ext}"

        logger.info(f"💾 Guardando archivo media: {new_name}")
        return super()._save(new_name, content)
```

- [ ] **Step 4: Eliminar la clase `SecureMediaStorage`**

Buscar y eliminar por completo este bloque (quedaba justo después de `MediaStorage`, antes de `class ReportesStorage`):

```python
class SecureMediaStorage(MediaStorage):
    """Storage para archivos media que requieren autenticación"""
    default_acl = 'private'
    querystring_auth = True
    querystring_expire = 3600  # URLs expiran en 1 hora

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logger.info("🔐 SecureMediaStorage inicializado para archivos privados")
```

Antes de borrar, confirmar que no tiene consumidores:

```bash
grep -rn "SecureMediaStorage" --include="*.py" . | grep -v /.venv/
```

Esperado: solo las líneas del propio archivo `storage_backends.py` (la definición que se está por borrar). Si aparece algún otro archivo, DETENERSE y reportar — no continuar con el borrado.

- [ ] **Step 5: Ejecutar y verificar que pasa**

```bash
python manage.py test hal9mil.test_storage_backends --keepdb --verbosity=2
```

Esperado: `OK` — 3 tests pasando.

- [ ] **Step 6: Verificar que nada más importa `SecureMediaStorage`**

```bash
python manage.py check
```

Esperado: `System check identified no issues (0 silenced).` (si `SecureMediaStorage` tuviera algún import colgante en otro archivo, `check` fallaría con `ImportError` al cargar apps).

- [ ] **Step 7: Commit**

```bash
git add hal9mil/storage_backends.py hal9mil/test_storage_backends.py
git commit -m "feat(hal9mil): MediaStorage privada con URLs firmadas; elimina SecureMediaStorage redundante"
```

---

## Task 3: Señales acotadas a XMLProveedor + delete usa el callable

**Files:**
- Modify: `hal9mil/storage_backends.py:143` (`delete_file_from_storage`)
- Modify: `hal9mil/storage_backends.py:244` (`@receiver(post_delete)` → función simple, sin decorador)
- Modify: `hal9mil/storage_backends.py:256` (`@receiver(pre_save)` → función simple, sin decorador)
- Modify: `finanzas/apps.py` (conectar las señales en `ready()`, con `sender=XMLProveedor` explícito)
- Modify: `hal9mil/test_storage_backends.py` (agregar tests)

**Interfaces:**
- Consumes: `media_storage()` (ya existe); `hal9mil.storage_backends.delete_file_on_model_delete` y `delete_old_file_on_change` (funciones planas, ya no auto-conectadas por `@receiver`) — Task 3 las conecta desde `finanzas/apps.py`.
- Produces: las señales solo disparan para `XMLProveedor`; ningún otro modelo del proyecto paga el costo de la inspección de campos en cada save/delete.

**Nota de diseño importante — por qué NO usar un import diferido dentro de `storage_backends.py`:**
Una alternativa más simple sería un helper `_get_xmlproveedor_model()` que
haga `from finanzas.models import XMLProveedor` y usarlo como
`@receiver(post_delete, sender=_get_xmlproveedor_model())`. **Esto rompe con
un `ImportError` circular real**, verificado contra la cadena de imports de
este proyecto: `finanzas/models.py:7` hace
`from hal9mil.storage_backends import media_storage` — es decir,
`hal9mil.storage_backends` se importa DURANTE la carga de
`finanzas.models`, antes de que la clase `XMLProveedor` (definida más abajo
en ese mismo archivo) exista todavía. Si en ese momento
`storage_backends.py` intenta (aunque sea en una función auxiliar, pero
LLAMADA en el momento de decorar) `from finanzas.models import XMLProveedor`,
Python encuentra un módulo `finanzas.models` a medio inicializar sin ese
nombre todavía definido, y falla. La forma correcta y estándar en Django de
conectar una señal a un sender de otra app sin ciclos es
`AppConfig.ready()`, que Django garantiza que corre **después** de que todas
las apps y modelos del proyecto están completamente cargados.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `hal9mil/test_storage_backends.py`. Esta prueba NO puede
basarse en guardar/borrar un modelo sin campos de archivo (como `Referencia`)
— como esos modelos nunca entran al `if hasattr(field, 'upload_to')`, el
comportamiento sería idéntico con o sin el filtro por `sender`, y el test no
detectaría el bug. En su lugar, se inspecciona directamente la lista interna
de receptores de la señal (`Signal.receivers`, formato estable de Django:
lista de `((receiver_id, sender_id), ref)` — confirmado contra
`django.dispatch.dispatcher.Signal.connect` en la versión instalada,
Django 6.0.5) para confirmar que el receptor está conectado con un
`sender_id` específico (`id(XMLProveedor)`) y no con `NONE_ID` (que
significa "cualquier sender"):

```python
from django.db.models.signals import post_delete, pre_save
from django.dispatch.dispatcher import NONE_ID, _make_id

from finanzas.models import XMLProveedor
from .storage_backends import delete_file_on_model_delete, delete_old_file_on_change


class SenalesAcotadasATest(TestCase):
    def test_post_delete_conectada_solo_a_xmlproveedor(self):
        receiver_id = _make_id(delete_file_on_model_delete)
        sender_ids = [
            sender_id for (rid, sender_id), _ in post_delete.receivers
            if rid == receiver_id
        ]
        self.assertEqual(sender_ids, [id(XMLProveedor)])
        self.assertNotIn(NONE_ID, sender_ids)

    def test_pre_save_conectada_solo_a_xmlproveedor(self):
        receiver_id = _make_id(delete_old_file_on_change)
        sender_ids = [
            sender_id for (rid, sender_id), _ in pre_save.receivers
            if rid == receiver_id
        ]
        self.assertEqual(sender_ids, [id(XMLProveedor)])
        self.assertNotIn(NONE_ID, sender_ids)
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

```bash
python manage.py test hal9mil.test_storage_backends.SenalesAcotadasATest --keepdb --verbosity=2
```

Esperado: `ImportError` (los nombres `delete_file_on_model_delete` y
`delete_old_file_on_change` no son importables todavía porque hoy están
conectados con `@receiver(post_delete)` sin `sender`, lo cual SÍ los define
— en realidad el import funciona; lo que falla es la aserción:
`sender_ids` contendrá `[NONE_ID]` en vez de `[id(XMLProveedor)]`, y
`assertNotIn(NONE_ID, sender_ids)` falla).

- [ ] **Step 3: Modificar `delete_file_from_storage`**

Reemplazar la firma y el cuerpo (línea ~143):

```python
def delete_file_from_storage(file_path, storage_class=None):
    """
    Elimina un archivo del storage de manera segura.
    Por default usa el mismo criterio de entorno que el guardado (media_storage()).
    """
    try:
        storage = storage_class() if storage_class else media_storage()
        if storage.exists(file_path):
            storage.delete(file_path)
            logger.info(f"🗑️ Archivo eliminado: {file_path}")
            return True
        else:
            logger.warning(f"⚠️ Archivo no encontrado para eliminar: {file_path}")
            return False
    except Exception as e:
        logger.error(f"❌ Error eliminando archivo {file_path}: {e}")
        return False
```

Como `media_storage()` está definida más abajo en el mismo archivo (al final), Python la resuelve en tiempo de llamada (no de definición) — no hace falta reordenar funciones ni importar nada nuevo.

- [ ] **Step 4: Quitar los decoradores `@receiver` y dejar funciones planas**

Reemplazar (línea ~244):

```python
@receiver(post_delete)
def delete_file_on_model_delete(sender, instance, **kwargs):
    """
    Elimina archivos del storage cuando se elimina un modelo
    """
    # Buscar campos de archivo en el modelo
    for field in instance._meta.fields:
        if hasattr(field, 'upload_to'):
            file_field = getattr(instance, field.name)
            if file_field:
                delete_file_from_storage(file_field.name)
```

Por (sin `@receiver` — se conecta explícitamente desde `finanzas/apps.py` en el Step 5):

```python
def delete_file_on_model_delete(sender, instance, **kwargs):
    """
    Elimina archivos del storage cuando se elimina un XMLProveedor.
    Conectada a la señal post_delete con sender=XMLProveedor desde
    finanzas.apps.FinanzasConfig.ready() — ver ese archivo para el porqué.
    """
    for field in instance._meta.fields:
        if hasattr(field, 'upload_to'):
            file_field = getattr(instance, field.name)
            if file_field:
                delete_file_from_storage(file_field.name)
```

Y reemplazar (línea ~256):

```python
@receiver(pre_save)
def delete_old_file_on_change(sender, instance, **kwargs):
    """
    Elimina archivo anterior cuando se actualiza con uno nuevo
    """
    if not instance.pk:
        return  # Es un nuevo objeto, no hay archivo anterior

    try:
        old_instance = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return

    # Verificar campos de archivo
    for field in instance._meta.fields:
        if hasattr(field, 'upload_to'):
            old_file = getattr(old_instance, field.name)
            new_file = getattr(instance, field.name)

            if old_file and old_file != new_file:
                delete_file_from_storage(old_file.name)
```

Por (sin `@receiver`):

```python
def delete_old_file_on_change(sender, instance, **kwargs):
    """
    Elimina archivo anterior de un XMLProveedor cuando se actualiza con uno
    nuevo. Conectada a la señal pre_save con sender=XMLProveedor desde
    finanzas.apps.FinanzasConfig.ready() — ver ese archivo para el porqué.
    """
    if not instance.pk:
        return  # Es un nuevo objeto, no hay archivo anterior

    try:
        old_instance = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return

    for field in instance._meta.fields:
        if hasattr(field, 'upload_to'):
            old_file = getattr(old_instance, field.name)
            new_file = getattr(instance, field.name)

            if old_file and old_file != new_file:
                delete_file_from_storage(old_file.name)
```

También eliminar, si quedó sin otros usos, el import ya innecesario en la
cabecera del archivo: `from django.db.models.signals import post_delete, pre_save`
y `from django.dispatch import receiver` — **NO los elimines todavía**, se
necesitan para el Step 5 en `finanzas/apps.py` (se importan ahí, no aquí).
Verificar con `grep -n "post_delete\|pre_save\|receiver" hal9mil/storage_backends.py`
que ya no queda ningún `@receiver(...)` en este archivo (las dos funciones
ahora son planas) antes de continuar.

- [ ] **Step 5: Conectar las señales en `finanzas/apps.py` con `sender=XMLProveedor`**

Reemplazar `finanzas/apps.py` completo:

```python
from django.apps import AppConfig


class FinanzasConfig(AppConfig):
    name = 'finanzas'

    def ready(self):
        """
        Conecta las señales de borrado automático de archivos de
        XMLProveedor aquí (no como @receiver en hal9mil/storage_backends.py)
        porque hal9mil.storage_backends se importa DESDE finanzas.models
        (finanzas/models.py:7 hace `from hal9mil.storage_backends import
        media_storage`), antes de que la clase XMLProveedor exista todavía
        en ese módulo. Conectar con sender=XMLProveedor en tiempo de
        decoración ahí causaría un ImportError circular. AppConfig.ready()
        corre después de que todas las apps y modelos están completamente
        cargados, así que aquí el import es seguro.
        """
        from django.db.models.signals import post_delete, pre_save

        from .models import XMLProveedor
        from hal9mil.storage_backends import (
            delete_file_on_model_delete, delete_old_file_on_change,
        )

        post_delete.connect(delete_file_on_model_delete, sender=XMLProveedor)
        pre_save.connect(delete_old_file_on_change, sender=XMLProveedor)
```

- [ ] **Step 6: Registrar `FinanzasConfig` como default (si no lo está ya)**

Django usa `AppConfig.ready()` automáticamente solo si la app está
referenciada correctamente. Verificar `finanzas/__init__.py`:

```bash
cat finanzas/__init__.py
```

Si está vacío (esperado — Django moderno auto-detecta `apps.py` sin
necesidad de `default_app_config`), no hay que tocarlo. Si por alguna razón
existe un `default_app_config` apuntando a otra config, avisar y detenerse
antes de continuar.

- [ ] **Step 7: Ejecutar y verificar que los tests de señales pasan**

```bash
python manage.py test hal9mil.test_storage_backends --keepdb --verbosity=2
```

Esperado: `OK` — 5 tests pasando (3 de Task 2 + 2 de Task 3).

- [ ] **Step 8: Verificar que XMLProveedor sigue disparando el borrado (regresión funcional)**

```bash
python manage.py test finanzas.test_carga_masiva finanzas.test_carga_cliente --keepdb --verbosity=1
```

Esperado: `OK` — estas suites crean y en algunos casos reemplazan `XMLProveedor` con archivos; si la señal acotada rompiera algo, fallarían aquí.

- [ ] **Step 9: Commit**

```bash
git add hal9mil/storage_backends.py finanzas/apps.py hal9mil/test_storage_backends.py
git commit -m "fix(hal9mil): acotar señales de borrado de archivo a XMLProveedor; delete usa media_storage()"
```

---

## Task 4: Verificación real contra el bucket de producción

**Files:** Ninguno (verificación manual vía `manage.py shell`, sin archivos nuevos)

**Interfaces:**
- Consumes: `MediaStorage` (Task 2), settings reales de `.env` con `USE_SPACES=True` forzado solo para este comando puntual.

**Autorización:** el usuario autorizó explícitamente subir y borrar un archivo
de prueba contra el bucket real durante el brainstorming de este plan
(2026-07-12). No repetir esta acción sin nueva autorización si se re-ejecuta
este plan en el futuro.

- [ ] **Step 1: Confirmar que Task 1-3 pasan la suite completa**

```bash
python manage.py test finanzas hal9mil clientes --keepdb --verbosity=1
```

Esperado: `OK`.

- [ ] **Step 2: Subir un archivo de prueba al bucket real**

```bash
USE_SPACES=True python manage.py shell -c "
from hal9mil.storage_backends import MediaStorage
from django.core.files.base import ContentFile

storage = MediaStorage()
nombre = storage.save('verificacion/prueba_wiring.txt', ContentFile(b'prueba de wiring DO Spaces'))
print('Guardado como:', nombre)
print('Existe:', storage.exists(nombre))
url = storage.url(nombre)
print('URL firmada generada (longitud):', len(url))
"
```

Esperado: `Guardado como: verificacion/prueba_wiring_<timestamp>.txt`, `Existe: True`, y una URL firmada (no imprimir la URL completa en el reporte final si contiene el bucket real — solo confirmar longitud/formato, o truncarla).

- [ ] **Step 3: Confirmar acceso HTTP real a la URL firmada**

```bash
USE_SPACES=True python manage.py shell -c "
import requests
from hal9mil.storage_backends import MediaStorage

storage = MediaStorage()
# usar el nombre exacto devuelto en el Step 2
nombre = sorted(storage.listdir('verificacion')[1])[-1]
ruta = f'verificacion/{nombre}'
url = storage.url(ruta)
resp = requests.get(url)
print('Status:', resp.status_code)
print('Contenido:', resp.content)
"
```

Esperado: `Status: 200`, `Contenido: b'prueba de wiring DO Spaces'`.

- [ ] **Step 4: Confirmar que la URL SIN firma (pública) es rechazada**

```bash
USE_SPACES=True python manage.py shell -c "
import requests
from django.conf import settings

# Construir la URL pública directa, sin querystring de firma
url_publica = f\"{settings.AWS_S3_ENDPOINT_URL}/{settings.AWS_STORAGE_BUCKET_NAME}/media/verificacion/\"
resp = requests.get(url_publica)
print('Status sin firma (esperado 403):', resp.status_code)
"
```

Esperado: `Status sin firma (esperado 403): 403` — confirma que el bucket/objeto NO es accesible sin la URL firmada (verifica la decisión de privacidad tomada con el usuario).

- [ ] **Step 5: Borrar el archivo de prueba**

```bash
USE_SPACES=True python manage.py shell -c "
from hal9mil.storage_backends import MediaStorage

storage = MediaStorage()
nombre = sorted(storage.listdir('verificacion')[1])[-1]
ruta = f'verificacion/{nombre}'
storage.delete(ruta)
print('Borrado:', not storage.exists(ruta))
"
```

Esperado: `Borrado: True`.

- [ ] **Step 6: Documentar el resultado**

Agregar una línea al final de este archivo de plan (no crear archivo nuevo), debajo de "## Verificación End-to-End", con fecha y resultado (sin credenciales ni URLs completas):

```
Verificación real contra Spaces: <fecha> — subida, acceso vía URL firmada (200),
rechazo sin firma (403) y borrado, todos exitosos. Bucket: <nombre, ya no es secreto>.
```

No se requiere commit de código en este task — es puramente verificación. Si
se documenta el resultado en este archivo, sí commitear ese archivo:

```bash
git add docs/superpowers/plans/2026-07-12-wiring-do-spaces.md
git commit -m "docs: registrar verificación real contra bucket de DO Spaces"
```

---

## Verificación End-to-End

Después de completar todos los tasks:

- [ ] **Suite completa**: `python manage.py test finanzas hal9mil clientes --keepdb` → `OK`.
- [ ] **`manage.py check`** limpio con `USE_SPACES` en ambos valores (ver Task 1 Step 3).
- [ ] **Verificación real contra Spaces completada** (Task 4) — archivo subido, accedido vía URL firmada, rechazado sin firma, y borrado.
- [ ] **Confirmar que local sigue sin tocar Spaces**: con el `.env` de desarrollo tal cual (sin forzar `USE_SPACES=True`), subir un XML de prueba vía `/finanzas/xml/carga-cliente/` y confirmar que el archivo aparece en `media/` local, no en el bucket.

Verificación real contra Spaces: 2026-07-12 — subida (OK) y rechazo sin firma (403, OK)
exitosos; borrado exitoso, sin objetos huérfanos. HALLAZGO: el acceso vía URL "firmada"
devolvió 403 en vez de 200 — `storage.url()` no está firmando realmente la URL porque
`AWS_S3_CUSTOM_DOMAIN` está configurado sin `cloudfront_signer`, causando que
`querystring_auth=True` se ignore silenciosamente (ver detalle en
`.superpowers/sdd/task-4-report.md`). El bucket está seguro (nada accesible sin
credenciales), pero el mecanismo de URLs firmadas para usuarios legítimos no funciona
aún — requiere task de seguimiento. Bucket: disco-loginco.
