# Módulo de Clasificación Arancelaria — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Catálogo interno de productos clasificados por cliente (fracción 8 dígitos + NICO 2, imágenes por posición) con vínculo manual a Referencias.

**Architecture:** App Django nueva `clasificacion/` con 3 modelos (`Producto`, `ImagenProducto`, `ProductoReferencia`), vistas function-based con permisos por grupo `clasificadores`, imágenes en DO Spaces (S3) con fallback a filesystem local. Integración con lo existente solo vía templates (sidebar + panel en detalle de referencia mediante inclusion tag).

**Tech Stack:** Django 5.2, Pillow, django-storages[boto3], Tailwind CDN, Django TestCase.

**Spec:** `docs/superpowers/specs/2026-07-08-clasificacion-arancelaria-design.md`

## Global Constraints

- Fracción: exactamente 8 dígitos (`^\d{8}$`); NICO: exactamente 2 dígitos (`^\d{2}$`).
- Imágenes por producto: mínimo 1 (posición `FRENTE`, obligatoria al crear), máximo 5 — una por posición (`FRENTE`, `IZQUIERDA`, `DERECHA`, `ARRIBA`, `ABAJO`), garantizado con `unique_together (producto, posicion)`.
- Imagen: máximo 5 MB; solo JPEG/PNG/WebP.
- Montos/cantidades siempre `DecimalField`, nunca float.
- Lectura: cualquier usuario autenticado. Escritura: grupo `clasificadores` o `is_staff`. Baja lógica de producto: solo staff. Productos nunca se borran físicamente.
- No modificar modelos ni vistas de apps existentes; solo `templates/base.html` y `templates/referencias/detalle.html`.
- Tests: `python manage.py test clasificacion` (runner estándar Django, sin pytest).
- Commits: Conventional Commits en español con scope, ej. `feat(clasificacion): ...`.
- Todos los strings visibles al usuario en español.

---

### Task 1: App `clasificacion` — modelos y admin

**Files:**
- Create: `clasificacion/__init__.py`, `clasificacion/apps.py`, `clasificacion/models.py`, `clasificacion/admin.py`, `clasificacion/migrations/__init__.py`
- Create: `clasificacion/tests.py`
- Modify: `hal9mil/settings.py` (INSTALLED_APPS, línea ~28 después de `'finanzas'`)

**Interfaces:**
- Consumes: `clientes.Cliente`, `referencias.Referencia` (FKs).
- Produces: `Producto` (props `fraccion_completa`, `imagen_principal`, método `imagenes_ordenadas()`), `ImagenProducto` (`POSICIONES`, campos `producto/imagen/posicion/descripcion`), `ProductoReferencia` (campos `producto/referencia/cantidad/notas/vinculado_por`), related_names: `cliente.productos`, `producto.imagenes`, `producto.vinculos`, `referencia.productos_clasificados`.

- [ ] **Step 1: Crear esqueleto de la app**

```bash
cd /home/tony/Developer/proyecto_hal9mil_reiki && source .venv/bin/activate
python manage.py startapp clasificacion
rm clasificacion/views.py clasificacion/tests.py  # se crean en tasks posteriores con contenido propio
```

En `hal9mil/settings.py`, agregar a `INSTALLED_APPS` después de `'finanzas',`:

```python
    'clasificacion',
```

- [ ] **Step 2: Escribir tests de modelos (fallan)**

`clasificacion/tests.py`:

```python
import io

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase

from clientes.models import Cliente
from referencias.models import Referencia

from .models import ImagenProducto, Producto, ProductoReferencia


def imagen_prueba(nombre='foto.png'):
    """PNG de 1x1 válido para Pillow."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (1, 1), 'white').save(buf, 'PNG')
    return SimpleUploadedFile(nombre, buf.getvalue(), content_type='image/png')


def crear_producto(cliente, nombre='Motor eléctrico', **kwargs):
    defaults = dict(
        descripcion='Motor de inducción trifásico',
        fraccion='85015300', nico='01',
    )
    defaults.update(kwargs)
    return Producto.objects.create(cliente=cliente, nombre=nombre, **defaults)


class ProductoModelTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(
            nombre_cliente='ACME SA', cve_cliente='ACME01')

    def test_fraccion_formato_invalido(self):
        p = Producto(cliente=self.cliente, nombre='X', descripcion='d',
                     fraccion='8501', nico='01')
        with self.assertRaises(ValidationError):
            p.full_clean()

    def test_nico_formato_invalido(self):
        p = Producto(cliente=self.cliente, nombre='X', descripcion='d',
                     fraccion='85015300', nico='1A')
        with self.assertRaises(ValidationError):
            p.full_clean()

    def test_fraccion_completa(self):
        p = crear_producto(self.cliente)
        self.assertEqual(p.fraccion_completa, '85015300 01')

    def test_nombre_unico_por_cliente(self):
        crear_producto(self.cliente)
        with self.assertRaises(IntegrityError):
            crear_producto(self.cliente)

    def test_mismo_nombre_distinto_cliente_ok(self):
        crear_producto(self.cliente)
        otro = Cliente.objects.create(nombre_cliente='BETA SA')
        crear_producto(otro)  # no lanza

    def test_imagen_principal_es_frente_con_fallback(self):
        p = crear_producto(self.cliente)
        self.assertIsNone(p.imagen_principal)
        lado = ImagenProducto.objects.create(
            producto=p, imagen=imagen_prueba(), posicion='IZQUIERDA')
        self.assertEqual(p.imagen_principal, lado)  # fallback
        frente = ImagenProducto.objects.create(
            producto=p, imagen=imagen_prueba(), posicion='FRENTE')
        self.assertEqual(p.imagen_principal, frente)

    def test_imagenes_ordenadas_por_posicion(self):
        p = crear_producto(self.cliente)
        ImagenProducto.objects.create(producto=p, imagen=imagen_prueba(), posicion='ABAJO')
        ImagenProducto.objects.create(producto=p, imagen=imagen_prueba(), posicion='FRENTE')
        self.assertEqual(
            [i.posicion for i in p.imagenes_ordenadas()], ['FRENTE', 'ABAJO'])


class ImagenProductoModelTest(TestCase):
    def setUp(self):
        cliente = Cliente.objects.create(nombre_cliente='ACME SA')
        self.producto = crear_producto(cliente)

    def test_posicion_unica_por_producto(self):
        ImagenProducto.objects.create(
            producto=self.producto, imagen=imagen_prueba(), posicion='FRENTE')
        with self.assertRaises(IntegrityError):
            ImagenProducto.objects.create(
                producto=self.producto, imagen=imagen_prueba(), posicion='FRENTE')


class ProductoReferenciaModelTest(TestCase):
    def setUp(self):
        cliente = Cliente.objects.create(nombre_cliente='ACME SA')
        self.producto = crear_producto(cliente)
        self.ref = Referencia.objects.create(
            num_refe='RE51-0001-26', cve_cliente='ACME01',
            nombre_cliente='ACME SA')

    def test_vinculo_unico_por_referencia(self):
        ProductoReferencia.objects.create(
            producto=self.producto, referencia=self.ref)
        with self.assertRaises(IntegrityError):
            ProductoReferencia.objects.create(
                producto=self.producto, referencia=self.ref)

    def test_related_name_en_referencia(self):
        ProductoReferencia.objects.create(
            producto=self.producto, referencia=self.ref)
        self.assertEqual(self.ref.productos_clasificados.count(), 1)
```

- [ ] **Step 3: Correr tests — deben fallar**

```bash
python manage.py test clasificacion 2>&1 | tail -5
```

Expected: `ImportError`/`ModuleNotFoundError` (models no existen). Nota: si `PIL` no está instalado aún, instalar Pillow primero es parte del Task 3 — para este task instala solo Pillow: `pip install Pillow`.

- [ ] **Step 4: Implementar modelos**

`clasificacion/models.py`:

```python
from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models

POSICIONES = [
    ('FRENTE', 'Frente'),
    ('IZQUIERDA', 'Lado izquierdo'),
    ('DERECHA', 'Lado derecho'),
    ('ARRIBA', 'Arriba'),
    ('ABAJO', 'Abajo'),
]
ORDEN_POSICION = {clave: i for i, (clave, _) in enumerate(POSICIONES)}

validar_fraccion = RegexValidator(r'^\d{8}$', 'La fracción debe tener exactamente 8 dígitos.')
validar_nico = RegexValidator(r'^\d{2}$', 'El NICO debe tener exactamente 2 dígitos.')


class Producto(models.Model):
    cliente = models.ForeignKey(
        'clientes.Cliente', on_delete=models.PROTECT, related_name='productos')
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField()
    fraccion = models.CharField(max_length=8, validators=[validar_fraccion])
    nico = models.CharField(max_length=2, validators=[validar_nico])
    descripcion_oficial = models.CharField(max_length=500, blank=True)
    proveedor = models.CharField(max_length=200, blank=True)
    notas_clasificacion = models.TextField(blank=True)
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('cliente', 'nombre')
        ordering = ['nombre']
        verbose_name = 'Producto'

    def __str__(self):
        return f'{self.nombre} ({self.cliente})'

    @property
    def fraccion_completa(self):
        return f'{self.fraccion} {self.nico}'

    @property
    def imagen_principal(self):
        frente = self.imagenes.filter(posicion='FRENTE').first()
        return frente or self.imagenes.first()

    def imagenes_ordenadas(self):
        return sorted(self.imagenes.all(), key=lambda i: ORDEN_POSICION[i.posicion])


class ImagenProducto(models.Model):
    producto = models.ForeignKey(
        Producto, on_delete=models.CASCADE, related_name='imagenes')
    imagen = models.ImageField(upload_to='productos/%Y/%m/')
    posicion = models.CharField(max_length=10, choices=POSICIONES)
    descripcion = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('producto', 'posicion')
        verbose_name = 'Imagen de producto'
        verbose_name_plural = 'Imágenes de producto'

    def __str__(self):
        return f'{self.producto.nombre} — {self.get_posicion_display()}'


class ProductoReferencia(models.Model):
    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, related_name='vinculos')
    referencia = models.ForeignKey(
        'referencias.Referencia', on_delete=models.CASCADE,
        related_name='productos_clasificados')
    cantidad = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True)
    notas = models.CharField(max_length=300, blank=True)
    vinculado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('producto', 'referencia')
        ordering = ['-created_at']
        verbose_name = 'Producto en referencia'
        verbose_name_plural = 'Productos en referencia'

    def __str__(self):
        return f'{self.producto.nombre} @ {self.referencia.num_refe}'
```

`clasificacion/admin.py`:

```python
from django.contrib import admin

from .models import ImagenProducto, Producto, ProductoReferencia


class ImagenProductoInline(admin.TabularInline):
    model = ImagenProducto
    extra = 0


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'cliente', 'fraccion', 'nico', 'activo', 'updated_at')
    list_filter = ('activo', 'cliente')
    search_fields = ('nombre', 'descripcion', 'fraccion')
    inlines = [ImagenProductoInline]


@admin.register(ProductoReferencia)
class ProductoReferenciaAdmin(admin.ModelAdmin):
    list_display = ('producto', 'referencia', 'cantidad', 'vinculado_por', 'created_at')
    search_fields = ('producto__nombre', 'referencia__num_refe')
```

- [ ] **Step 5: Migrar y correr tests — deben pasar**

```bash
python manage.py makemigrations clasificacion
python manage.py migrate
python manage.py test clasificacion -v 1
```

Expected: `OK` con todos los tests del Step 2 en verde.

- [ ] **Step 6: Commit**

```bash
git add clasificacion/ hal9mil/settings.py
git commit -m "feat(clasificacion): app nueva con modelos Producto, ImagenProducto y ProductoReferencia"
```

---

### Task 2: Grupo `clasificadores` y permisos

**Files:**
- Create: `clasificacion/permisos.py`, `clasificacion/migrations/0002_grupo_clasificadores.py`, `clasificacion/templatetags/__init__.py`, `clasificacion/templatetags/clasificacion_tags.py`
- Modify: `clasificacion/tests.py` (agregar tests)

**Interfaces:**
- Produces: `permisos.puede_clasificar(user) -> bool`, decorador `permisos.requiere_clasificador(view)` (lanza `PermissionDenied` → 403), filtro template `{{ user|puede_clasificar }}`.

- [ ] **Step 1: Escribir tests (fallan)**

Agregar a `clasificacion/tests.py`:

```python
from django.contrib.auth.models import Group


class PermisosTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('op', password='x')
        self.staff = User.objects.create_user('jefe', password='x', is_staff=True)

    def test_grupo_creado_por_migracion(self):
        self.assertTrue(Group.objects.filter(name='clasificadores').exists())

    def test_puede_clasificar(self):
        from .permisos import puede_clasificar
        self.assertFalse(puede_clasificar(self.user))
        self.assertTrue(puede_clasificar(self.staff))
        self.user.groups.add(Group.objects.get(name='clasificadores'))
        self.assertTrue(puede_clasificar(self.user))
```

- [ ] **Step 2: Correr tests — fallan**

```bash
python manage.py test clasificacion.tests.PermisosTest -v 1
```

Expected: FAIL (`No module named 'clasificacion.permisos'` y grupo inexistente).

- [ ] **Step 3: Implementar**

`clasificacion/permisos.py`:

```python
from functools import wraps

from django.core.exceptions import PermissionDenied

GRUPO_CLASIFICADORES = 'clasificadores'


def puede_clasificar(user):
    return user.is_authenticated and (
        user.is_staff or user.groups.filter(name=GRUPO_CLASIFICADORES).exists())


def requiere_clasificador(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not puede_clasificar(request.user):
            raise PermissionDenied('Requiere pertenecer al grupo clasificadores.')
        return view_func(request, *args, **kwargs)
    return wrapper
```

`clasificacion/migrations/0002_grupo_clasificadores.py` — las `Permission` se crean en post_migrate, así que la migración las crea explícitamente:

```python
from django.db import migrations

MODELOS = ['producto', 'imagenproducto', 'productoreferencia']
ACCIONES = ['add', 'change', 'delete']


def crear_grupo(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    grupo, _ = Group.objects.get_or_create(name='clasificadores')
    for modelo in MODELOS:
        ct, _ = ContentType.objects.get_or_create(
            app_label='clasificacion', model=modelo)
        for accion in ACCIONES:
            perm, _ = Permission.objects.get_or_create(
                codename=f'{accion}_{modelo}', content_type=ct,
                defaults={'name': f'Can {accion} {modelo}'})
            grupo.permissions.add(perm)


def eliminar_grupo(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name='clasificadores').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('clasificacion', '0001_initial'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]
    operations = [migrations.RunPython(crear_grupo, eliminar_grupo)]
```

`clasificacion/templatetags/clasificacion_tags.py` (crear también `clasificacion/templatetags/__init__.py` vacío):

```python
from django import template

from ..permisos import puede_clasificar as _puede

register = template.Library()


@register.filter
def puede_clasificar(user):
    return _puede(user)
```

- [ ] **Step 4: Migrar y correr tests — pasan**

```bash
python manage.py migrate clasificacion
python manage.py test clasificacion -v 1
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add clasificacion/
git commit -m "feat(clasificacion): grupo clasificadores, decorador de permisos y filtro de template"
```

---

### Task 3: Dependencias y storage condicional (DO Spaces)

**Files:**
- Modify: `requirements.txt`, `hal9mil/settings.py` (reemplazar `STATICFILES_STORAGE`, línea ~84)
- Modify: `clasificacion/tests.py`

**Interfaces:**
- Produces: setting `STORAGES` con `default` condicional por env `SPACES_ACCESS_KEY`; sin credenciales el comportamiento actual no cambia.

- [ ] **Step 1: Agregar dependencias**

En `requirements.txt`, después de `lxml>=5.0`:

```
# módulo clasificación — imágenes de productos
Pillow>=10.0
django-storages[boto3]>=1.14
```

```bash
pip install -r requirements.txt
```

- [ ] **Step 2: Escribir test (falla)**

Agregar a `clasificacion/tests.py`:

```python
class StorageConfigTest(TestCase):
    def test_storages_definido_con_fallback_local(self):
        from django.conf import settings as s
        # En tests no hay SPACES_ACCESS_KEY → filesystem local
        self.assertEqual(
            s.STORAGES['default']['BACKEND'],
            'django.core.files.storage.FileSystemStorage')
        # staticfiles debe ser whitenoise (STATICFILES_STORAGE fue removido en Django 5.1)
        self.assertEqual(
            s.STORAGES['staticfiles']['BACKEND'],
            'whitenoise.storage.CompressedManifestStaticFilesStorage')
```

```bash
python manage.py test clasificacion.tests.StorageConfigTest
```

Expected: FAIL — sin `STORAGES` explícito, Django usa `StaticFilesStorage` como backend de staticfiles, así que el segundo assert falla.

- [ ] **Step 3: Implementar en settings**

En `hal9mil/settings.py` **eliminar** la línea:

```python
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
```

(nota: `STATICFILES_STORAGE` fue removido en Django 5.1 — hoy esa línea es inerte; `STORAGES` restaura el comportamiento whitenoise que se pretendía) y en su lugar poner:

```python
# ── Storages ──────────────────────────────────────────────────────────────────
# default: DO Spaces (S3) si hay credenciales; filesystem local en dev.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
if os.getenv('SPACES_ACCESS_KEY'):
    STORAGES['default'] = {
        'BACKEND': 'storages.backends.s3.S3Storage',
        'OPTIONS': {
            'access_key': os.getenv('SPACES_ACCESS_KEY'),
            'secret_key': os.getenv('SPACES_SECRET_KEY'),
            'bucket_name': os.getenv('SPACES_BUCKET'),
            'region_name': os.getenv('SPACES_REGION', 'nyc3'),
            'endpoint_url': os.getenv('SPACES_ENDPOINT'),
            'default_acl': 'public-read',
            'file_overwrite': False,
        },
    }
```

Agregar al `.env` (comentadas, para producción):

```
# ── DO Spaces — imágenes de productos y archivos subidos (producción) ────────
# SPACES_ACCESS_KEY=
# SPACES_SECRET_KEY=
# SPACES_BUCKET=
# SPACES_REGION=nyc3
# SPACES_ENDPOINT=https://nyc3.digitaloceanspaces.com
```

- [ ] **Step 4: Verificar**

```bash
python manage.py check && python manage.py test clasificacion -v 1
```

Expected: `System check identified no issues` y `OK`.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt hal9mil/settings.py
git commit -m "feat(clasificacion): storage condicional DO Spaces con fallback local + Pillow/django-storages"
```

---

### Task 4: Forms con validación de imagen

**Files:**
- Create: `clasificacion/forms.py`
- Modify: `clasificacion/tests.py`

**Interfaces:**
- Consumes: modelos Task 1, `POSICIONES`.
- Produces: `ProductoForm` (ModelForm; en creación exige campo extra `imagen_frente`; en edición lo omite), `ImagenProductoForm(producto=...)` (solo posiciones libres), `VincularProductoForm(productos_qs=...)` (campos `producto`, `cantidad`, `notas`), `validar_imagen(archivo)`.

- [ ] **Step 1: Escribir tests (fallan)**

Agregar a `clasificacion/tests.py`:

```python
class FormsTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre_cliente='ACME SA')

    def _datos_producto(self, **extra):
        d = {'cliente': self.cliente.pk, 'nombre': 'Motor', 'descripcion': 'd',
             'fraccion': '85015300', 'nico': '01', 'proveedor': '',
             'notas_clasificacion': ''}
        d.update(extra)
        return d

    def test_crear_sin_imagen_frente_falla(self):
        from .forms import ProductoForm
        form = ProductoForm(self._datos_producto(), {})
        self.assertFalse(form.is_valid())
        self.assertIn('imagen_frente', form.errors)

    def test_crear_con_imagen_frente_ok(self):
        from .forms import ProductoForm
        form = ProductoForm(
            self._datos_producto(), {'imagen_frente': imagen_prueba()})
        self.assertTrue(form.is_valid(), form.errors)

    def test_editar_no_exige_imagen(self):
        from .forms import ProductoForm
        p = crear_producto(self.cliente)
        form = ProductoForm(self._datos_producto(nombre='Motor v2'), instance=p)
        self.assertNotIn('imagen_frente', form.fields)
        self.assertTrue(form.is_valid(), form.errors)

    def test_imagen_muy_grande_rechazada(self):
        from .forms import ProductoForm
        grande = SimpleUploadedFile(
            'g.png', imagen_prueba().read() + b'0' * (5 * 1024 * 1024),
            content_type='image/png')
        form = ProductoForm(self._datos_producto(), {'imagen_frente': grande})
        self.assertFalse(form.is_valid())

    def test_imagen_posiciones_solo_libres(self):
        from .forms import ImagenProductoForm
        p = crear_producto(self.cliente)
        ImagenProducto.objects.create(
            producto=p, imagen=imagen_prueba(), posicion='FRENTE')
        form = ImagenProductoForm(producto=p)
        posiciones = [c[0] for c in form.fields['posicion'].choices]
        self.assertNotIn('FRENTE', posiciones)
        self.assertIn('IZQUIERDA', posiciones)

    def test_imagen_posicion_ocupada_rechazada(self):
        from .forms import ImagenProductoForm
        p = crear_producto(self.cliente)
        ImagenProducto.objects.create(
            producto=p, imagen=imagen_prueba(), posicion='FRENTE')
        form = ImagenProductoForm(
            {'posicion': 'FRENTE', 'descripcion': ''},
            {'imagen': imagen_prueba()}, producto=p)
        self.assertFalse(form.is_valid())
```

- [ ] **Step 2: Correr — fallan**

```bash
python manage.py test clasificacion.tests.FormsTest -v 1
```

Expected: FAIL (`No module named 'clasificacion.forms'`).

- [ ] **Step 3: Implementar**

`clasificacion/forms.py`:

```python
from django import forms

from .models import ImagenProducto, Producto, ProductoReferencia, POSICIONES

MAX_IMAGEN_MB = 5
CONTENT_TYPES_PERMITIDOS = {'image/jpeg', 'image/png', 'image/webp'}

_INPUT = ('w-full border border-slate-200 rounded-lg px-3 py-2 text-sm '
          'focus:outline-none focus:ring-2 focus:ring-sky-500')


def validar_imagen(archivo):
    if archivo.size > MAX_IMAGEN_MB * 1024 * 1024:
        raise forms.ValidationError(
            f'La imagen no debe superar {MAX_IMAGEN_MB} MB.')
    tipo = getattr(archivo, 'content_type', '')
    if tipo not in CONTENT_TYPES_PERMITIDOS:
        raise forms.ValidationError('Solo se aceptan imágenes JPEG, PNG o WebP.')
    return archivo


class ProductoForm(forms.ModelForm):
    imagen_frente = forms.ImageField(
        label='Imagen de frente (obligatoria)',
        validators=[validar_imagen])

    class Meta:
        model = Producto
        fields = ['cliente', 'nombre', 'descripcion', 'fraccion', 'nico',
                  'proveedor', 'notas_clasificacion']
        widgets = {
            'cliente': forms.Select(attrs={'class': _INPUT}),
            'nombre': forms.TextInput(attrs={'class': _INPUT}),
            'descripcion': forms.Textarea(attrs={'class': _INPUT, 'rows': 3}),
            'fraccion': forms.TextInput(attrs={'class': _INPUT, 'maxlength': 8,
                                               'placeholder': '8 dígitos'}),
            'nico': forms.TextInput(attrs={'class': _INPUT, 'maxlength': 2,
                                           'placeholder': '2 dígitos'}),
            'proveedor': forms.TextInput(attrs={'class': _INPUT}),
            'notas_clasificacion': forms.Textarea(attrs={'class': _INPUT, 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:  # edición: la imagen se gestiona aparte
            self.fields.pop('imagen_frente')


class ImagenProductoForm(forms.ModelForm):
    class Meta:
        model = ImagenProducto
        fields = ['imagen', 'posicion', 'descripcion']
        widgets = {
            'posicion': forms.Select(attrs={'class': _INPUT}),
            'descripcion': forms.TextInput(attrs={'class': _INPUT}),
        }

    def __init__(self, *args, producto=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.producto = producto
        ocupadas = set(
            producto.imagenes.values_list('posicion', flat=True)) if producto else set()
        self.fields['posicion'].choices = [
            (c, n) for c, n in POSICIONES if c not in ocupadas]

    def clean_imagen(self):
        return validar_imagen(self.cleaned_data['imagen'])

    def clean_posicion(self):
        posicion = self.cleaned_data['posicion']
        if self.producto and self.producto.imagenes.filter(posicion=posicion).exists():
            raise forms.ValidationError('Esa posición ya tiene imagen.')
        return posicion


class VincularProductoForm(forms.Form):
    producto = forms.ModelChoiceField(
        queryset=Producto.objects.none(),
        widget=forms.Select(attrs={'class': _INPUT}))
    cantidad = forms.DecimalField(
        max_digits=14, decimal_places=2, required=False,
        widget=forms.NumberInput(attrs={'class': _INPUT, 'step': '0.01'}))
    notas = forms.CharField(
        max_length=300, required=False,
        widget=forms.TextInput(attrs={'class': _INPUT}))

    def __init__(self, *args, productos_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        if productos_qs is not None:
            self.fields['producto'].queryset = productos_qs
```

- [ ] **Step 4: Correr tests — pasan**

```bash
python manage.py test clasificacion -v 1
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add clasificacion/forms.py clasificacion/tests.py
git commit -m "feat(clasificacion): forms de producto, imagen por posición y vínculo con validaciones"
```

---

### Task 5: Vistas de catálogo y detalle + URLs + templates

**Files:**
- Create: `clasificacion/views.py`, `clasificacion/urls.py`, `templates/clasificacion/catalogo.html`, `templates/clasificacion/producto_detalle.html`
- Modify: `hal9mil/urls.py` (agregar include), `clasificacion/tests.py`

**Interfaces:**
- Consumes: modelos y permisos de Tasks 1-2.
- Produces: URLs con namespace `clasificacion`: `clasificacion:catalogo`, `clasificacion:producto_detalle` (pk). Vistas posteriores (Task 6/7) se agregan al mismo `views.py`/`urls.py`.

- [ ] **Step 1: Escribir tests (fallan)**

Agregar a `clasificacion/tests.py`:

```python
class CatalogoViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('op', password='x')
        self.client.force_login(self.user)
        self.acme = Cliente.objects.create(nombre_cliente='ACME SA')
        self.beta = Cliente.objects.create(nombre_cliente='BETA SA')
        crear_producto(self.acme, nombre='Motor eléctrico')
        crear_producto(self.beta, nombre='Bomba hidráulica', fraccion='84137001')

    def test_requiere_login(self):
        self.client.logout()
        resp = self.client.get('/clasificacion/')
        self.assertEqual(resp.status_code, 302)

    def test_lista_todos(self):
        resp = self.client.get('/clasificacion/')
        self.assertContains(resp, 'Motor eléctrico')
        self.assertContains(resp, 'Bomba hidráulica')

    def test_busca_por_nombre(self):
        resp = self.client.get('/clasificacion/', {'q': 'motor'})
        self.assertContains(resp, 'Motor eléctrico')
        self.assertNotContains(resp, 'Bomba hidráulica')

    def test_busca_por_fraccion_parcial(self):
        resp = self.client.get('/clasificacion/', {'q': '8413'})
        self.assertContains(resp, 'Bomba hidráulica')
        self.assertNotContains(resp, 'Motor eléctrico')

    def test_filtra_por_cliente(self):
        resp = self.client.get('/clasificacion/', {'cliente': self.acme.pk})
        self.assertContains(resp, 'Motor eléctrico')
        self.assertNotContains(resp, 'Bomba hidráulica')

    def test_inactivos_ocultos_por_defecto(self):
        Producto.objects.filter(nombre='Motor eléctrico').update(activo=False)
        resp = self.client.get('/clasificacion/')
        self.assertNotContains(resp, 'Motor eléctrico')
        resp = self.client.get('/clasificacion/', {'activo': '0'})
        self.assertContains(resp, 'Motor eléctrico')


class ProductoDetalleViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('op', password='x')
        self.client.force_login(self.user)
        cliente = Cliente.objects.create(nombre_cliente='ACME SA')
        self.producto = crear_producto(cliente)

    def test_detalle_muestra_ficha(self):
        resp = self.client.get(f'/clasificacion/producto/{self.producto.pk}/')
        self.assertContains(resp, 'Motor eléctrico')
        self.assertContains(resp, '85015300 01')
```

- [ ] **Step 2: Correr — fallan (404)**

```bash
python manage.py test clasificacion.tests.CatalogoViewTest clasificacion.tests.ProductoDetalleViewTest -v 1
```

Expected: FAIL con 404 (URLs no existen).

- [ ] **Step 3: Implementar vistas y URLs**

`clasificacion/views.py`:

```python
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from clientes.models import Cliente

from .models import Producto
from .permisos import puede_clasificar

PRODUCTOS_POR_PAGINA = 24


@login_required
def catalogo(request):
    q = request.GET.get('q', '').strip()
    cliente_id = request.GET.get('cliente', '')
    mostrar_activos = request.GET.get('activo', '1')

    productos = Producto.objects.select_related('cliente').prefetch_related('imagenes')
    productos = productos.filter(activo=(mostrar_activos != '0'))
    if q:
        productos = productos.filter(
            Q(nombre__icontains=q) | Q(descripcion__icontains=q) |
            Q(fraccion__startswith=q))
    if cliente_id:
        productos = productos.filter(cliente_id=cliente_id)

    paginator = Paginator(productos, PRODUCTOS_POR_PAGINA)
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'clasificacion/catalogo.html', {
        'page': page,
        'q': q,
        'cliente_id': cliente_id,
        'mostrar_activos': mostrar_activos,
        'clientes': Cliente.objects.all(),
        'puede_editar': puede_clasificar(request.user),
    })


@login_required
def producto_detalle(request, pk):
    producto = get_object_or_404(
        Producto.objects.select_related('cliente'), pk=pk)
    return render(request, 'clasificacion/producto_detalle.html', {
        'producto': producto,
        'imagenes': producto.imagenes_ordenadas(),
        'vinculos': producto.vinculos.select_related('referencia')[:50],
        'puede_editar': puede_clasificar(request.user),
    })
```

`clasificacion/urls.py`:

```python
from django.urls import path

from . import views

app_name = 'clasificacion'

urlpatterns = [
    path('', views.catalogo, name='catalogo'),
    path('producto/<int:pk>/', views.producto_detalle, name='producto_detalle'),
]
```

En `hal9mil/urls.py`, después de `path('finanzas/', include('finanzas.urls')),`:

```python
    path('clasificacion/', include('clasificacion.urls')),
```

- [ ] **Step 4: Crear templates**

`templates/clasificacion/catalogo.html`:

```html
{% extends 'base.html' %}
{% block title %}Clasificación — HAL9MIL{% endblock %}
{% block content %}
<div class="p-6 space-y-4">
  <div class="flex items-center justify-between flex-wrap gap-2">
    <h1 class="text-2xl font-bold text-slate-800">Catálogo de clasificación</h1>
    {% if puede_editar %}
    <a href="{% url 'clasificacion:producto_crear' %}"
       class="bg-sky-600 hover:bg-sky-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors">
      + Nuevo producto
    </a>
    {% endif %}
  </div>

  <form method="get" class="bg-white rounded-xl p-4 shadow-sm border border-slate-100 flex flex-wrap gap-3 items-end">
    <div class="flex-1 min-w-48">
      <label class="text-xs font-medium text-slate-500">Buscar</label>
      <input type="text" name="q" value="{{ q }}" placeholder="Nombre, descripción o fracción…"
             class="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm">
    </div>
    <div>
      <label class="text-xs font-medium text-slate-500">Cliente</label>
      <select name="cliente" class="border border-slate-200 rounded-lg px-3 py-2 text-sm">
        <option value="">Todos</option>
        {% for c in clientes %}
        <option value="{{ c.pk }}" {% if cliente_id == c.pk|stringformat:'s' %}selected{% endif %}>{{ c.nombre_cliente }}</option>
        {% endfor %}
      </select>
    </div>
    <div>
      <label class="text-xs font-medium text-slate-500">Estado</label>
      <select name="activo" class="border border-slate-200 rounded-lg px-3 py-2 text-sm">
        <option value="1" {% if mostrar_activos == '1' %}selected{% endif %}>Activos</option>
        <option value="0" {% if mostrar_activos == '0' %}selected{% endif %}>Inactivos</option>
      </select>
    </div>
    <button class="bg-slate-800 hover:bg-slate-700 text-white text-sm px-4 py-2 rounded-lg">Filtrar</button>
  </form>

  <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
    {% for producto in page %}
    <a href="{% url 'clasificacion:producto_detalle' producto.pk %}"
       class="bg-white rounded-xl shadow-sm border border-slate-100 overflow-hidden hover:shadow-md transition-shadow">
      <div class="h-40 bg-slate-100 flex items-center justify-center">
        {% if producto.imagen_principal %}
        <img src="{{ producto.imagen_principal.imagen.url }}" alt="{{ producto.nombre }}"
             class="h-full w-full object-cover">
        {% else %}
        <svg class="w-10 h-10 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
                d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14M4 6h16M4 6v12a2 2 0 002 2h12a2 2 0 002-2V6"/>
        </svg>
        {% endif %}
      </div>
      <div class="p-3">
        <p class="text-sm font-semibold text-slate-800 truncate">{{ producto.nombre }}</p>
        <p class="text-xs text-slate-500 truncate">{{ producto.cliente.nombre_cliente }}</p>
        <p class="text-xs font-mono text-sky-700 mt-1">{{ producto.fraccion_completa }}</p>
      </div>
    </a>
    {% empty %}
    <p class="col-span-full text-sm text-slate-500 py-8 text-center">Sin productos que coincidan.</p>
    {% endfor %}
  </div>

  {% if page.has_other_pages %}
  <div class="flex justify-center gap-2 text-sm">
    {% if page.has_previous %}
    <a href="?page={{ page.previous_page_number }}&q={{ q }}&cliente={{ cliente_id }}&activo={{ mostrar_activos }}"
       class="px-3 py-1 bg-white border border-slate-200 rounded-lg">← Anterior</a>
    {% endif %}
    <span class="px-3 py-1 text-slate-500">Página {{ page.number }} de {{ page.paginator.num_pages }}</span>
    {% if page.has_next %}
    <a href="?page={{ page.next_page_number }}&q={{ q }}&cliente={{ cliente_id }}&activo={{ mostrar_activos }}"
       class="px-3 py-1 bg-white border border-slate-200 rounded-lg">Siguiente →</a>
    {% endif %}
  </div>
  {% endif %}
</div>
{% endblock %}
```

> Nota: `{% url 'clasificacion:producto_crear' %}` se registra hasta Task 6, pero el bloque está dentro de `{% if puede_editar %}` y ningún test de este task usa un usuario editor, así que la URL nunca se evalúa y los tests pasan. No navegar manualmente al catálogo como staff hasta completar Task 6 (daría `NoReverseMatch`).

`templates/clasificacion/producto_detalle.html`:

```html
{% extends 'base.html' %}
{% block title %}{{ producto.nombre }} — Clasificación{% endblock %}
{% block content %}
<div class="p-6 space-y-4 max-w-5xl">
  <div class="flex items-center justify-between flex-wrap gap-2">
    <div>
      <a href="{% url 'clasificacion:catalogo' %}" class="text-xs text-sky-600 hover:underline">← Catálogo</a>
      <h1 class="text-2xl font-bold text-slate-800">{{ producto.nombre }}
        {% if not producto.activo %}<span class="text-xs bg-slate-200 text-slate-600 px-2 py-1 rounded-full align-middle">Inactivo</span>{% endif %}
      </h1>
      <p class="text-sm text-slate-500">{{ producto.cliente.nombre_cliente }}</p>
    </div>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
    <div class="bg-white rounded-xl p-5 shadow-sm border border-slate-100">
      <h2 class="text-sm font-semibold text-slate-700 mb-3">Imágenes</h2>
      <div class="grid grid-cols-2 gap-3">
        {% for img in imagenes %}
        <figure>
          <img src="{{ img.imagen.url }}" alt="{{ img.get_posicion_display }}"
               class="rounded-lg border border-slate-200 w-full h-36 object-cover">
          <figcaption class="text-xs text-slate-500 mt-1">
            {{ img.get_posicion_display }}{% if img.descripcion %} — {{ img.descripcion }}{% endif %}
          </figcaption>
        </figure>
        {% empty %}
        <p class="text-sm text-slate-400 col-span-2">Sin imágenes.</p>
        {% endfor %}
      </div>
    </div>

    <div class="bg-white rounded-xl p-5 shadow-sm border border-slate-100 space-y-3">
      <h2 class="text-sm font-semibold text-slate-700">Clasificación</h2>
      <dl class="text-sm space-y-2">
        <div><dt class="text-xs text-slate-500">Fracción + NICO</dt>
             <dd class="font-mono text-lg text-sky-700">{{ producto.fraccion_completa }}</dd></div>
        {% if producto.descripcion_oficial %}
        <div><dt class="text-xs text-slate-500">Descripción oficial (TIGIE)</dt>
             <dd>{{ producto.descripcion_oficial }}</dd></div>
        {% endif %}
        <div><dt class="text-xs text-slate-500">Descripción</dt>
             <dd>{{ producto.descripcion }}</dd></div>
        {% if producto.proveedor %}
        <div><dt class="text-xs text-slate-500">Proveedor</dt><dd>{{ producto.proveedor }}</dd></div>
        {% endif %}
        {% if producto.notas_clasificacion %}
        <div><dt class="text-xs text-slate-500">Notas de clasificación</dt>
             <dd class="whitespace-pre-line">{{ producto.notas_clasificacion }}</dd></div>
        {% endif %}
      </dl>
    </div>
  </div>

  <div class="bg-white rounded-xl p-5 shadow-sm border border-slate-100">
    <h2 class="text-sm font-semibold text-slate-700 mb-3">Referencias donde se usó</h2>
    <table class="w-full text-sm">
      <thead><tr class="text-left text-xs text-slate-500 border-b border-slate-100">
        <th class="py-2">Referencia</th><th>Cantidad</th><th>Notas</th><th>Vinculado</th>
      </tr></thead>
      <tbody>
        {% for v in vinculos %}
        <tr class="border-b border-slate-50">
          <td class="py-2"><a href="{% url 'detalle' v.referencia.num_refe %}"
                 class="text-sky-600 hover:underline font-mono">{{ v.referencia.num_refe }}</a></td>
          <td>{{ v.cantidad|default:'—' }}</td>
          <td>{{ v.notas|default:'—' }}</td>
          <td class="text-xs text-slate-500">{{ v.created_at|date:'d/m/Y' }}</td>
        </tr>
        {% empty %}
        <tr><td colspan="4" class="py-4 text-slate-400">Aún no se vincula a ninguna referencia.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 5: Correr tests — pasan**

```bash
python manage.py test clasificacion -v 1
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add clasificacion/ templates/clasificacion/ hal9mil/urls.py
git commit -m "feat(clasificacion): catálogo con búsqueda/filtros y detalle de producto"
```

---

### Task 6: Crear/editar producto y gestión de imágenes

**Files:**
- Modify: `clasificacion/views.py`, `clasificacion/urls.py`, `clasificacion/tests.py`, `templates/clasificacion/catalogo.html` (botón "Nuevo producto"), `templates/clasificacion/producto_detalle.html` (acciones de edición)
- Create: `templates/clasificacion/producto_form.html`

**Interfaces:**
- Consumes: `ProductoForm`, `ImagenProductoForm`, `requiere_clasificador`.
- Produces: URLs `clasificacion:producto_crear`, `clasificacion:producto_editar` (pk), `clasificacion:imagen_subir` (pk producto, POST), `clasificacion:imagen_eliminar` (pk imagen, POST), `clasificacion:producto_baja` (pk, POST, staff).

- [ ] **Step 1: Escribir tests (fallan)**

Agregar a `clasificacion/tests.py`:

```python
class ProductoCrearEditarTest(TestCase):
    def setUp(self):
        self.clasificador = User.objects.create_user('clasif', password='x')
        self.clasificador.groups.add(Group.objects.get(name='clasificadores'))
        self.simple = User.objects.create_user('simple', password='x')
        self.staff = User.objects.create_user('staff', password='x', is_staff=True)
        self.cliente = Cliente.objects.create(nombre_cliente='ACME SA')

    def _post_crear(self):
        return self.client.post('/clasificacion/producto/nuevo/', {
            'cliente': self.cliente.pk, 'nombre': 'Motor', 'descripcion': 'd',
            'fraccion': '85015300', 'nico': '01', 'proveedor': '',
            'notas_clasificacion': '', 'imagen_frente': imagen_prueba(),
        })

    def test_sin_grupo_403(self):
        self.client.force_login(self.simple)
        self.assertEqual(self._post_crear().status_code, 403)

    def test_crear_producto_con_frente(self):
        self.client.force_login(self.clasificador)
        resp = self._post_crear()
        self.assertEqual(resp.status_code, 302)
        p = Producto.objects.get(nombre='Motor')
        self.assertEqual(p.creado_por, self.clasificador)
        self.assertTrue(p.imagenes.filter(posicion='FRENTE').exists())

    def test_subir_sexta_posicion_no_disponible(self):
        self.client.force_login(self.clasificador)
        p = crear_producto(self.cliente)
        for pos in ['FRENTE', 'IZQUIERDA', 'DERECHA', 'ARRIBA', 'ABAJO']:
            ImagenProducto.objects.create(
                producto=p, imagen=imagen_prueba(), posicion=pos)
        resp = self.client.post(f'/clasificacion/producto/{p.pk}/imagen/', {
            'posicion': 'FRENTE', 'descripcion': '',
            'imagen': imagen_prueba()})
        self.assertEqual(p.imagenes.count(), 5)  # sigue en 5

    def test_no_eliminar_frente_unica(self):
        self.client.force_login(self.clasificador)
        p = crear_producto(self.cliente)
        img = ImagenProducto.objects.create(
            producto=p, imagen=imagen_prueba(), posicion='FRENTE')
        self.client.post(f'/clasificacion/imagen/{img.pk}/eliminar/')
        self.assertTrue(ImagenProducto.objects.filter(pk=img.pk).exists())

    def test_eliminar_lateral_ok(self):
        self.client.force_login(self.clasificador)
        p = crear_producto(self.cliente)
        ImagenProducto.objects.create(
            producto=p, imagen=imagen_prueba(), posicion='FRENTE')
        lado = ImagenProducto.objects.create(
            producto=p, imagen=imagen_prueba(), posicion='ARRIBA')
        self.client.post(f'/clasificacion/imagen/{lado.pk}/eliminar/')
        self.assertFalse(ImagenProducto.objects.filter(pk=lado.pk).exists())

    def test_baja_solo_staff(self):
        p = crear_producto(self.cliente)
        self.client.force_login(self.clasificador)
        self.assertEqual(
            self.client.post(f'/clasificacion/producto/{p.pk}/baja/').status_code, 403)
        self.client.force_login(self.staff)
        self.client.post(f'/clasificacion/producto/{p.pk}/baja/')
        p.refresh_from_db()
        self.assertFalse(p.activo)
```

- [ ] **Step 2: Correr — fallan (404)**

```bash
python manage.py test clasificacion.tests.ProductoCrearEditarTest -v 1
```

Expected: FAIL con 404.

- [ ] **Step 3: Implementar vistas**

Agregar a `clasificacion/views.py` (imports adicionales arriba):

```python
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from .forms import ImagenProductoForm, ProductoForm
from .models import ImagenProducto
from .permisos import requiere_clasificador
```

y las vistas:

```python
@login_required
@requiere_clasificador
def producto_crear(request):
    form = ProductoForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            producto = form.save(commit=False)
            producto.creado_por = request.user
            producto.actualizado_por = request.user
            producto.save()
            ImagenProducto.objects.create(
                producto=producto,
                imagen=form.cleaned_data['imagen_frente'],
                posicion='FRENTE')
        messages.success(request, f'Producto "{producto.nombre}" creado.')
        return redirect('clasificacion:producto_detalle', pk=producto.pk)
    return render(request, 'clasificacion/producto_form.html',
                  {'form': form, 'producto': None})


@login_required
@requiere_clasificador
def producto_editar(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    form = ProductoForm(request.POST or None, instance=producto)
    imagen_form = ImagenProductoForm(producto=producto)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.actualizado_por = request.user
        obj.save()
        messages.success(request, 'Producto actualizado.')
        return redirect('clasificacion:producto_detalle', pk=pk)
    return render(request, 'clasificacion/producto_form.html',
                  {'form': form, 'producto': producto, 'imagen_form': imagen_form})


@login_required
@requiere_clasificador
@require_POST
def imagen_subir(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    form = ImagenProductoForm(request.POST, request.FILES, producto=producto)
    if form.is_valid():
        imagen = form.save(commit=False)
        imagen.producto = producto
        imagen.save()
        messages.success(request, f'Imagen "{imagen.get_posicion_display()}" agregada.')
    else:
        messages.error(request, '; '.join(
            e for errores in form.errors.values() for e in errores))
    return redirect('clasificacion:producto_editar', pk=pk)


@login_required
@requiere_clasificador
@require_POST
def imagen_eliminar(request, pk):
    imagen = get_object_or_404(ImagenProducto, pk=pk)
    producto_pk = imagen.producto_id
    if imagen.posicion == 'FRENTE' and imagen.producto.imagenes.count() == 1:
        messages.error(request, 'No se puede eliminar la única imagen de frente.')
    else:
        imagen.delete()
        messages.success(request, 'Imagen eliminada.')
    return redirect('clasificacion:producto_editar', pk=producto_pk)


@login_required
@require_POST
def producto_baja(request, pk):
    if not request.user.is_staff:
        raise PermissionDenied('Solo staff puede dar de baja productos.')
    producto = get_object_or_404(Producto, pk=pk)
    producto.activo = False
    producto.actualizado_por = request.user
    producto.save(update_fields=['activo', 'actualizado_por', 'updated_at'])
    messages.success(request, f'Producto "{producto.nombre}" dado de baja.')
    return redirect('clasificacion:producto_detalle', pk=pk)
```

Agregar a `clasificacion/urls.py`:

```python
    path('producto/nuevo/', views.producto_crear, name='producto_crear'),
    path('producto/<int:pk>/editar/', views.producto_editar, name='producto_editar'),
    path('producto/<int:pk>/imagen/', views.imagen_subir, name='imagen_subir'),
    path('producto/<int:pk>/baja/', views.producto_baja, name='producto_baja'),
    path('imagen/<int:pk>/eliminar/', views.imagen_eliminar, name='imagen_eliminar'),
```

(ojo: `producto/nuevo/` debe ir ANTES de `producto/<int:pk>/` en la lista para que "nuevo" no matchee como pk — con `<int:pk>` no colisiona, pero mantener el orden por claridad).

- [ ] **Step 4: Crear template de form y actualizar catálogo/detalle**

`templates/clasificacion/producto_form.html`:

```html
{% extends 'base.html' %}
{% block title %}{% if producto %}Editar{% else %}Nuevo{% endif %} producto — Clasificación{% endblock %}
{% block content %}
<div class="p-6 max-w-3xl space-y-4">
  <h1 class="text-2xl font-bold text-slate-800">
    {% if producto %}Editar: {{ producto.nombre }}{% else %}Nuevo producto{% endif %}
  </h1>

  {% if messages %}{% for m in messages %}
  <div class="text-sm px-4 py-2 rounded-lg {% if m.tags == 'error' %}bg-red-50 text-red-700{% else %}bg-emerald-50 text-emerald-700{% endif %}">{{ m }}</div>
  {% endfor %}{% endif %}

  <form method="post" enctype="multipart/form-data"
        class="bg-white rounded-xl p-5 shadow-sm border border-slate-100 space-y-4">
    {% csrf_token %}
    {% for field in form %}
    <div>
      <label class="text-xs font-medium text-slate-500">{{ field.label }}</label>
      {{ field }}
      {% for error in field.errors %}<p class="text-xs text-red-600 mt-1">{{ error }}</p>{% endfor %}
    </div>
    {% endfor %}
    <div class="flex gap-2">
      <button class="bg-sky-600 hover:bg-sky-700 text-white text-sm font-medium px-4 py-2 rounded-lg">Guardar</button>
      <a href="{% if producto %}{% url 'clasificacion:producto_detalle' producto.pk %}{% else %}{% url 'clasificacion:catalogo' %}{% endif %}"
         class="text-sm text-slate-500 px-4 py-2">Cancelar</a>
    </div>
  </form>

  {% if producto %}
  <div class="bg-white rounded-xl p-5 shadow-sm border border-slate-100 space-y-3">
    <h2 class="text-sm font-semibold text-slate-700">Imágenes ({{ producto.imagenes.count }}/5)</h2>
    <div class="grid grid-cols-2 sm:grid-cols-3 gap-3">
      {% for img in producto.imagenes_ordenadas %}
      <figure class="relative">
        <img src="{{ img.imagen.url }}" class="rounded-lg border border-slate-200 w-full h-28 object-cover">
        <figcaption class="text-xs text-slate-500 mt-1">{{ img.get_posicion_display }}</figcaption>
        <form method="post" action="{% url 'clasificacion:imagen_eliminar' img.pk %}"
              onsubmit="return confirm('¿Eliminar la imagen {{ img.get_posicion_display }}?');">
          {% csrf_token %}
          <button class="absolute top-1 right-1 bg-red-600 text-white text-xs rounded-full w-6 h-6">✕</button>
        </form>
      </figure>
      {% endfor %}
    </div>
    {% if imagen_form.fields.posicion.choices %}
    <form method="post" enctype="multipart/form-data"
          action="{% url 'clasificacion:imagen_subir' producto.pk %}" class="flex flex-wrap gap-2 items-end">
      {% csrf_token %}
      <div><label class="text-xs text-slate-500">Imagen</label>{{ imagen_form.imagen }}</div>
      <div><label class="text-xs text-slate-500">Posición</label>{{ imagen_form.posicion }}</div>
      <div><label class="text-xs text-slate-500">Pie de foto</label>{{ imagen_form.descripcion }}</div>
      <button class="bg-slate-800 text-white text-sm px-4 py-2 rounded-lg">Subir</button>
    </form>
    {% else %}
    <p class="text-xs text-slate-400">Las 5 posiciones están ocupadas.</p>
    {% endif %}
  </div>
  {% endif %}
</div>
{% endblock %}
```

En `templates/clasificacion/catalogo.html`, agregar el botón "Nuevo producto" (versión final mostrada en Task 5 Step 4).

En `templates/clasificacion/producto_detalle.html`, dentro del `<div>` del header junto al `<h1>`, agregar:

```html
    {% if puede_editar %}
    <div class="flex gap-2">
      <a href="{% url 'clasificacion:producto_editar' producto.pk %}"
         class="bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 text-sm px-4 py-2 rounded-lg">Editar</a>
      {% if request.user.is_staff and producto.activo %}
      <form method="post" action="{% url 'clasificacion:producto_baja' producto.pk %}"
            onsubmit="return confirm('¿Dar de baja este producto?');">
        {% csrf_token %}
        <button class="bg-white border border-red-200 hover:bg-red-50 text-red-600 text-sm px-4 py-2 rounded-lg">Dar de baja</button>
      </form>
      {% endif %}
    </div>
    {% endif %}
```

- [ ] **Step 5: Correr tests — pasan**

```bash
python manage.py test clasificacion -v 1
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add clasificacion/ templates/clasificacion/
git commit -m "feat(clasificacion): crear/editar producto, gestión de imágenes por posición y baja lógica"
```

---

### Task 7: Vincular productos a referencias

**Files:**
- Create: `clasificacion/servicios.py`
- Modify: `clasificacion/views.py`, `clasificacion/urls.py`, `clasificacion/tests.py`

**Interfaces:**
- Consumes: `VincularProductoForm`, `ProductoReferencia`, `referencias.Referencia`, `clientes.Cliente`.
- Produces: `servicios.resolver_cliente_de_referencia(referencia) -> Cliente | None`; `servicios.productos_para_referencia(referencia) -> QuerySet[Producto]`; URLs `clasificacion:vincular_producto` (`<path:num_refe>`, POST), `clasificacion:vinculo_eliminar` (pk, POST). Task 8 consume ambos servicios en el inclusion tag.

- [ ] **Step 1: Escribir tests (fallan)**

Agregar a `clasificacion/tests.py`:

```python
class ResolverClienteTest(TestCase):
    def setUp(self):
        self.acme = Cliente.objects.create(
            nombre_cliente='ACME SA', cve_cliente='ACME01')

    def test_por_cve(self):
        from .servicios import resolver_cliente_de_referencia
        ref = Referencia.objects.create(
            num_refe='RE51-0001-26', cve_cliente='ACME01', nombre_cliente='OTRO')
        self.assertEqual(resolver_cliente_de_referencia(ref), self.acme)

    def test_fallback_por_nombre(self):
        from .servicios import resolver_cliente_de_referencia
        ref = Referencia.objects.create(
            num_refe='RE51-0002-26', cve_cliente='ZZZ', nombre_cliente='ACME SA')
        self.assertEqual(resolver_cliente_de_referencia(ref), self.acme)

    def test_sin_match(self):
        from .servicios import resolver_cliente_de_referencia
        ref = Referencia.objects.create(
            num_refe='RE51-0003-26', cve_cliente='ZZZ', nombre_cliente='NADIE')
        self.assertIsNone(resolver_cliente_de_referencia(ref))


class VincularProductoTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('clasif', password='x')
        self.user.groups.add(Group.objects.get(name='clasificadores'))
        self.client.force_login(self.user)
        self.acme = Cliente.objects.create(
            nombre_cliente='ACME SA', cve_cliente='ACME01')
        self.beta = Cliente.objects.create(nombre_cliente='BETA SA')
        self.p_acme = crear_producto(self.acme)
        self.p_beta = crear_producto(self.beta, nombre='Bomba')
        self.ref = Referencia.objects.create(
            num_refe='RE51-0001-26', cve_cliente='ACME01', nombre_cliente='ACME SA')

    def test_vincular_producto_del_cliente(self):
        resp = self.client.post(
            f'/clasificacion/referencia/{self.ref.num_refe}/vincular/',
            {'producto': self.p_acme.pk, 'cantidad': '10', 'notas': 'lote 3'})
        self.assertEqual(resp.status_code, 302)
        v = ProductoReferencia.objects.get(referencia=self.ref)
        self.assertEqual(v.producto, self.p_acme)
        self.assertEqual(v.vinculado_por, self.user)

    def test_rechaza_producto_de_otro_cliente(self):
        self.client.post(
            f'/clasificacion/referencia/{self.ref.num_refe}/vincular/',
            {'producto': self.p_beta.pk})
        self.assertFalse(ProductoReferencia.objects.exists())

    def test_duplicado_no_crea_segundo_vinculo(self):
        ProductoReferencia.objects.create(
            producto=self.p_acme, referencia=self.ref)
        self.client.post(
            f'/clasificacion/referencia/{self.ref.num_refe}/vincular/',
            {'producto': self.p_acme.pk})
        self.assertEqual(ProductoReferencia.objects.count(), 1)

    def test_eliminar_vinculo(self):
        v = ProductoReferencia.objects.create(
            producto=self.p_acme, referencia=self.ref)
        self.client.post(f'/clasificacion/vinculo/{v.pk}/eliminar/')
        self.assertFalse(ProductoReferencia.objects.exists())

    def test_sin_grupo_403(self):
        simple = User.objects.create_user('simple', password='x')
        self.client.force_login(simple)
        resp = self.client.post(
            f'/clasificacion/referencia/{self.ref.num_refe}/vincular/',
            {'producto': self.p_acme.pk})
        self.assertEqual(resp.status_code, 403)
```

- [ ] **Step 2: Correr — fallan**

```bash
python manage.py test clasificacion.tests.ResolverClienteTest clasificacion.tests.VincularProductoTest -v 1
```

Expected: FAIL.

- [ ] **Step 3: Implementar**

`clasificacion/servicios.py`:

```python
from clientes.models import Cliente

from .models import Producto


def resolver_cliente_de_referencia(referencia):
    """Cliente del catálogo para una Referencia: primero por cve, luego por nombre."""
    if referencia.cve_cliente:
        cliente = Cliente.objects.filter(
            cve_cliente=referencia.cve_cliente).first()
        if cliente:
            return cliente
    if referencia.nombre_cliente:
        return Cliente.objects.filter(
            nombre_cliente=referencia.nombre_cliente).first()
    return None


def productos_para_referencia(referencia):
    """Productos activos vinculables: los del cliente resuelto, o todos si no hay match."""
    cliente = resolver_cliente_de_referencia(referencia)
    qs = Producto.objects.filter(activo=True).select_related('cliente')
    if cliente:
        qs = qs.filter(cliente=cliente)
    return qs
```

Agregar a `clasificacion/views.py`:

```python
from referencias.models import Referencia

from .forms import VincularProductoForm
from .models import ProductoReferencia
from .servicios import productos_para_referencia
```

```python
@login_required
@requiere_clasificador
@require_POST
def vincular_producto(request, num_refe):
    referencia = get_object_or_404(Referencia, num_refe=num_refe)
    form = VincularProductoForm(
        request.POST, productos_qs=productos_para_referencia(referencia))
    if form.is_valid():
        _, creado = ProductoReferencia.objects.get_or_create(
            producto=form.cleaned_data['producto'], referencia=referencia,
            defaults={
                'cantidad': form.cleaned_data['cantidad'],
                'notas': form.cleaned_data['notas'],
                'vinculado_por': request.user,
            })
        if creado:
            messages.success(request, 'Producto vinculado a la referencia.')
        else:
            messages.info(request, 'Ese producto ya estaba vinculado.')
    else:
        messages.error(request, 'No se pudo vincular: producto inválido para esta referencia.')
    return redirect('detalle', num_refe=num_refe)


@login_required
@requiere_clasificador
@require_POST
def vinculo_eliminar(request, pk):
    vinculo = get_object_or_404(ProductoReferencia, pk=pk)
    num_refe = vinculo.referencia.num_refe
    vinculo.delete()
    messages.success(request, 'Vínculo eliminado.')
    return redirect('detalle', num_refe=num_refe)
```

Agregar a `clasificacion/urls.py`:

```python
    path('referencia/<path:num_refe>/vincular/', views.vincular_producto, name='vincular_producto'),
    path('vinculo/<int:pk>/eliminar/', views.vinculo_eliminar, name='vinculo_eliminar'),
```

> `redirect('detalle', ...)` usa el name existente de `referencias/urls.py` (sin namespace).

- [ ] **Step 4: Correr tests — pasan**

```bash
python manage.py test clasificacion -v 1
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add clasificacion/
git commit -m "feat(clasificacion): vincular productos a referencias con resolución de cliente"
```

---

### Task 8: Integración UI — sidebar y panel en detalle de referencia

**Files:**
- Create: `templates/clasificacion/panel_referencia.html`
- Modify: `clasificacion/templatetags/clasificacion_tags.py`, `templates/base.html` (nav, ~línea 91 después del bloque de Clientes), `templates/referencias/detalle.html` (después del panel de glosa), `clasificacion/tests.py`

**Interfaces:**
- Consumes: `servicios.productos_para_referencia`, `servicios.resolver_cliente_de_referencia`, `permisos.puede_clasificar`, related_name `referencia.productos_clasificados`.
- Produces: inclusion tag `{% panel_productos referencia request.user %}`.

- [ ] **Step 1: Escribir test (falla)**

Agregar a `clasificacion/tests.py`:

```python
class PanelReferenciaTest(TestCase):
    """El detalle de referencia muestra el panel de productos clasificados."""

    def setUp(self):
        self.user = User.objects.create_user('op', password='x')
        self.client.force_login(self.user)
        acme = Cliente.objects.create(
            nombre_cliente='ACME SA', cve_cliente='ACME01')
        self.producto = crear_producto(acme)
        self.ref = Referencia.objects.create(
            num_refe='RE51-0001-26', cve_cliente='ACME01', nombre_cliente='ACME SA')

    def test_panel_visible_en_detalle(self):
        ProductoReferencia.objects.create(
            producto=self.producto, referencia=self.ref)
        resp = self.client.get(f'/referencias/{self.ref.num_refe}/')
        self.assertContains(resp, 'Productos clasificados')
        self.assertContains(resp, 'Motor eléctrico')

    def test_form_vincular_solo_para_clasificadores(self):
        resp = self.client.get(f'/referencias/{self.ref.num_refe}/')
        self.assertNotContains(resp, 'Vincular producto')
        self.user.groups.add(Group.objects.get(name='clasificadores'))
        resp = self.client.get(f'/referencias/{self.ref.num_refe}/')
        self.assertContains(resp, 'Vincular producto')
```

> Si la URL de detalle difiere (verificar con `grep -n "detalle" referencias/urls.py`), ajustar el path del test a la ruta real.

- [ ] **Step 2: Correr — falla**

```bash
python manage.py test clasificacion.tests.PanelReferenciaTest -v 1
```

Expected: FAIL (`Productos clasificados` no aparece).

- [ ] **Step 3: Implementar inclusion tag y panel**

Agregar a `clasificacion/templatetags/clasificacion_tags.py`:

```python
from ..servicios import productos_para_referencia, resolver_cliente_de_referencia


@register.inclusion_tag('clasificacion/panel_referencia.html')
def panel_productos(referencia, user):
    return {
        'referencia': referencia,
        'vinculos': referencia.productos_clasificados.select_related(
            'producto', 'producto__cliente'),
        'productos_vinculables': productos_para_referencia(referencia),
        'cliente_resuelto': resolver_cliente_de_referencia(referencia),
        'puede_editar': _puede(user),
    }
```

`templates/clasificacion/panel_referencia.html`:

```html
{% load clasificacion_tags %}
<div class="bg-white rounded-xl p-5 shadow-sm border border-slate-100">
  <h3 class="text-sm font-semibold text-slate-700 mb-3 flex items-center gap-2">
    <svg class="w-4 h-4 text-sky-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"/>
    </svg>
    Productos clasificados
  </h3>

  {% if vinculos %}
  <ul class="space-y-2 mb-3">
    {% for v in vinculos %}
    <li class="flex items-center gap-3 text-sm">
      {% if v.producto.imagen_principal %}
      <img src="{{ v.producto.imagen_principal.imagen.url }}"
           class="w-10 h-10 rounded-lg object-cover border border-slate-200">
      {% endif %}
      <div class="flex-1 min-w-0">
        <a href="{% url 'clasificacion:producto_detalle' v.producto.pk %}"
           class="text-sky-600 hover:underline font-medium">{{ v.producto.nombre }}</a>
        <span class="font-mono text-xs text-slate-500 ml-2">{{ v.producto.fraccion_completa }}</span>
        {% if v.cantidad %}<span class="text-xs text-slate-400 ml-2">× {{ v.cantidad }}</span>{% endif %}
      </div>
      {% if puede_editar %}
      <form method="post" action="{% url 'clasificacion:vinculo_eliminar' v.pk %}"
            onsubmit="return confirm('¿Quitar {{ v.producto.nombre }} de esta referencia?');">
        {% csrf_token %}
        <button class="text-xs text-red-500 hover:text-red-700">Quitar</button>
      </form>
      {% endif %}
    </li>
    {% endfor %}
  </ul>
  {% else %}
  <p class="text-sm text-slate-400 mb-3">Sin productos vinculados.</p>
  {% endif %}

  {% if puede_editar %}
    {% if not cliente_resuelto %}
    <p class="text-xs text-amber-600 mb-2">Cliente sin catálogo — se muestran productos de todos los clientes.</p>
    {% endif %}
    {% if productos_vinculables %}
    <form method="post" action="{% url 'clasificacion:vincular_producto' referencia.num_refe %}"
          class="flex flex-wrap gap-2 items-end border-t border-slate-100 pt-3">
      {% csrf_token %}
      <div class="flex-1 min-w-40">
        <label class="text-xs text-slate-500">Producto</label>
        <select name="producto" class="w-full border border-slate-200 rounded-lg px-2 py-1.5 text-sm">
          {% for p in productos_vinculables %}
          <option value="{{ p.pk }}">{{ p.nombre }}{% if not cliente_resuelto %} — {{ p.cliente.nombre_cliente }}{% endif %}</option>
          {% endfor %}
        </select>
      </div>
      <div class="w-24">
        <label class="text-xs text-slate-500">Cantidad</label>
        <input type="number" name="cantidad" step="0.01"
               class="w-full border border-slate-200 rounded-lg px-2 py-1.5 text-sm">
      </div>
      <div class="flex-1 min-w-32">
        <label class="text-xs text-slate-500">Notas</label>
        <input type="text" name="notas" maxlength="300"
               class="w-full border border-slate-200 rounded-lg px-2 py-1.5 text-sm">
      </div>
      <button class="bg-sky-600 hover:bg-sky-700 text-white text-sm px-3 py-1.5 rounded-lg">
        Vincular producto
      </button>
    </form>
    {% else %}
    <p class="text-xs text-slate-400 border-t border-slate-100 pt-3">
      El cliente no tiene productos en el catálogo.
      <a href="{% url 'clasificacion:producto_crear' %}" class="text-sky-600 hover:underline">Crear producto</a>
    </p>
    {% endif %}
  {% endif %}
</div>
```

- [ ] **Step 4: Integrar en templates existentes**

En `templates/base.html`, después del enlace de Clientes (~línea 91-97), agregar:

```html
      <a href="{% url 'clasificacion:catalogo' %}"
         class="sidebar-link {% if request.resolver_match.app_name == 'clasificacion' %}active{% endif %}">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"/>
        </svg>
        Clasificación
      </a>
```

En `templates/referencias/detalle.html`, arriba del todo agregar el load (junto a otros `{% load %}` si existen, o tras `{% extends %}`):

```html
{% load clasificacion_tags %}
```

y después del cierre del panel de glosa (buscar el `</div>` que cierra el bloque `{% if necesita_glosa %}...{% endif %}`, ~línea 345+), agregar:

```html
  {% panel_productos ref request.user %}
```

> Verificar el nombre de la variable de contexto de la referencia en detalle.html (el panel de glosa usa `ref.pk` → la variable es `ref`).

- [ ] **Step 5: Correr suite completa — pasa**

```bash
python manage.py test clasificacion -v 1 && python manage.py test referencias -v 1
```

Expected: `OK` en ambas (los tests de referencias confirman que el detalle no se rompió).

- [ ] **Step 6: Commit**

```bash
git add clasificacion/ templates/
git commit -m "feat(clasificacion): entrada en sidebar y panel de productos en detalle de referencia"
```

---

### Task 9: Verificación final

**Files:**
- No crea archivos; ejecución y verificación.

- [ ] **Step 1: Suite completa del proyecto**

```bash
python manage.py test 2>&1 | tail -5
```

Expected: `OK` — cero regresiones en finanzas/referencias/clientes.

- [ ] **Step 2: Checks de Django**

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
```

Expected: sin errores y "No changes detected" (no hay migraciones pendientes).

- [ ] **Step 3: Verificación manual del flujo (con superusuario)**

```bash
python manage.py runserver 8001
```

1. Entrar a `/clasificacion/` → catálogo vacío con buscador.
2. Crear producto con imagen de frente → redirige al detalle con la foto.
3. Editar → subir imagen "Arriba" → aparecen 2/5; intentar posición repetida no es posible (no está en el select).
4. Abrir una referencia en `/referencias/<num_refe>/` → panel "Productos clasificados" → vincular el producto → aparece en el panel y en el detalle del producto.
5. Con un usuario sin grupo: puede ver catálogo y panel, no ve botones de edición.

- [ ] **Step 4: Cierre**

Verificar contra el checklist del spec (`docs/superpowers/specs/2026-07-08-clasificacion-arancelaria-design.md`) y usar la skill superpowers:finishing-a-development-branch para decidir integración.
