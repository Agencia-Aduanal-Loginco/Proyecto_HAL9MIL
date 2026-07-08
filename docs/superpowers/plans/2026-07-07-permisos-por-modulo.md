# Permisos por Módulo (Grupos) — Piloto Finanzas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ocultar y bloquear el módulo de Finanzas para usuarios que no pertenezcan al grupo Django "Finanzas" (los superusuarios conservan acceso total), con un mecanismo reutilizable para migrar otros módulos después.

**Architecture:** Una nueva app `core` (sin modelos propios) expone `usuario_tiene_modulo()`, un decorator `modulo_required()` para vistas, y un filtro de plantilla `tiene_modulo` para el sidebar. Una migración de datos crea el `Group` "Finanzas". Las 32 vistas de `finanzas/views.py` cambian su `@login_required` por `@modulo_required('Finanzas')` (el decorator ya incluye la exigencia de login). `templates/base.html` usa el nuevo filtro para decidir si muestra el link de Finanzas.

**Tech Stack:** Django 6.0 (apps, `django.contrib.auth.models.Group`, migraciones de datos, template tags), `django.test.TestCase` / `Client` / `RequestFactory` para pruebas.

## Global Constraints

- Especificación de referencia: `docs/superpowers/specs/2026-07-07-permisos-por-modulo-design.md`.
- Superusuarios (`user.is_superuser`) tienen acceso automático a todos los módulos, sin necesidad de pertenecer a ningún grupo.
- Control "todo o nada" por módulo — no hay permisos finos dentro de Finanzas en este plan.
- Este piloto solo toca Finanzas. Clientes y SLA Capturistas quedan sin cambios (`is_superuser`).
- Las pruebas deben correr contra SQLite local, **no** contra la base remota de `.env`. `hal9mil/settings.py` llama `load_dotenv()`, que NO sobreescribe una variable ya presente en el entorno pero SÍ la establece si falta — por eso `env -u DBURL` no sirve (dotenv la vuelve a cargar desde `.env` y termina usando el Postgres remoto). Todos los comandos de test en este plan usan `DBURL="sqlite:///$(pwd)/db.sqlite3.test"` explícito para forzar SQLite en memoria.
- El repo tiene cambios sin commitear preexistentes en `hal9mil/settings.py`, `templates/base.html`, `hal9mil/urls.py`, `reportes/scheduler.py`, `requirements.txt`, `.gitignore` (trabajo previo del módulo Finanzas, no relacionado con este plan). Cada `git add` en este plan lista archivos exactos — antes de cada commit, correr `git diff --stat <archivo>` y confirmar que el diff mostrado es el esperado por el paso, ya que el archivo puede traer además esos cambios previos sin commitear.
- Nomenclatura: nombres de grupo, funciones y mensajes en español, según convención del resto del proyecto.

---

### Task 1: App `core` + `usuario_tiene_modulo()`

**Files:**
- Create: `core/__init__.py`
- Create: `core/apps.py`
- Create: `core/models.py`
- Create: `core/migrations/__init__.py`
- Create: `core/permisos.py`
- Create: `core/tests.py`
- Modify: `hal9mil/settings.py` (INSTALLED_APPS)

**Interfaces:**
- Produces: `core.permisos.usuario_tiene_modulo(user, nombre_modulo: str) -> bool` — usado por Task 2 (decorator) y Task 4 (template tag).

- [ ] **Step 1: Crear el esqueleto de la app `core` y registrarla**

Crear `core/__init__.py` (vacío):

```python
```

Crear `core/apps.py`:

```python
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
```

Crear `core/models.py`:

```python
# Create your models here.
```

Crear `core/migrations/__init__.py` (vacío):

```python
```

En `hal9mil/settings.py`, dentro de `INSTALLED_APPS`, agregar `'core'` antes de `'referencias'`:

```python
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_apscheduler',
    'core',
    'referencias',
    'reportes',
    'whatsapp',
    'clientes',
    'finanzas',
]
```

- [ ] **Step 2: Escribir el test que falla**

Crear `core/tests.py`:

```python
from django.contrib.auth.models import AnonymousUser, Group, User
from django.test import TestCase

from core.permisos import usuario_tiene_modulo


class UsuarioTieneModuloTests(TestCase):
    def setUp(self):
        self.grupo_finanzas, _ = Group.objects.get_or_create(name='Finanzas')
        self.usuario_con_grupo = User.objects.create_user('con_grupo', password='x')
        self.usuario_con_grupo.groups.add(self.grupo_finanzas)
        self.usuario_sin_grupo = User.objects.create_user('sin_grupo', password='x')
        self.superusuario = User.objects.create_superuser(
            'admin_test', email='admin_test@example.com', password='x'
        )

    def test_superusuario_siempre_tiene_acceso(self):
        self.assertTrue(usuario_tiene_modulo(self.superusuario, 'Finanzas'))

    def test_usuario_en_el_grupo_tiene_acceso(self):
        self.assertTrue(usuario_tiene_modulo(self.usuario_con_grupo, 'Finanzas'))

    def test_usuario_fuera_del_grupo_no_tiene_acceso(self):
        self.assertFalse(usuario_tiene_modulo(self.usuario_sin_grupo, 'Finanzas'))

    def test_usuario_anonimo_no_tiene_acceso(self):
        self.assertFalse(usuario_tiene_modulo(AnonymousUser(), 'Finanzas'))
```

- [ ] **Step 3: Correr el test y confirmar que falla**

Run: `source .venv/bin/activate && DBURL="sqlite:///$(pwd)/db.sqlite3.test" python manage.py test core -v 2`
Expected: FAIL — `ImportError: cannot import name 'usuario_tiene_modulo' from 'core.permisos'` (el módulo `core/permisos.py` todavía no existe).

- [ ] **Step 4: Implementar `usuario_tiene_modulo`**

Crear `core/permisos.py`:

```python
def usuario_tiene_modulo(user, nombre_modulo):
    """True si `user` puede acceder al módulo `nombre_modulo`.

    Un superusuario siempre tiene acceso. Cualquier otro usuario necesita
    pertenecer a un Group de Django cuyo `name` sea exactamente `nombre_modulo`.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=nombre_modulo).exists()
```

- [ ] **Step 5: Correr el test y confirmar que pasa**

Run: `source .venv/bin/activate && DBURL="sqlite:///$(pwd)/db.sqlite3.test" python manage.py test core -v 2`
Expected: `OK` — 4 tests pasan.

- [ ] **Step 6: Commit**

```bash
git add core/__init__.py core/apps.py core/models.py core/migrations/__init__.py core/permisos.py core/tests.py hal9mil/settings.py
git commit -m "$(cat <<'EOF'
Agrega app core con usuario_tiene_modulo() para permisos por grupo

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Decorator `modulo_required()`

**Files:**
- Modify: `core/permisos.py`
- Modify: `core/tests.py`

**Interfaces:**
- Consumes: `usuario_tiene_modulo(user, nombre_modulo)` de Task 1.
- Produces: `core.permisos.modulo_required(nombre_modulo: str)` — decorator para vistas basadas en función. Usado por Task 5 en `finanzas/views.py`.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `core/tests.py`:

```python
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory

from core.permisos import modulo_required


def _agregar_middleware(request):
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)


@modulo_required('Finanzas')
def _vista_de_prueba(request):
    return HttpResponse('ok')


class ModuloRequiredTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.grupo_finanzas, _ = Group.objects.get_or_create(name='Finanzas')
        self.usuario_con_grupo = User.objects.create_user('con_grupo2', password='x')
        self.usuario_con_grupo.groups.add(self.grupo_finanzas)
        self.usuario_sin_grupo = User.objects.create_user('sin_grupo2', password='x')

    def test_usuario_con_grupo_accede_a_la_vista(self):
        request = self.factory.get('/protegida/')
        request.user = self.usuario_con_grupo
        _agregar_middleware(request)
        response = _vista_de_prueba(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')

    def test_usuario_sin_grupo_es_redirigido_al_dashboard(self):
        request = self.factory.get('/protegida/')
        request.user = self.usuario_sin_grupo
        _agregar_middleware(request)
        response = _vista_de_prueba(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/')

    def test_usuario_anonimo_es_redirigido_al_login(self):
        request = self.factory.get('/protegida/')
        request.user = AnonymousUser()
        _agregar_middleware(request)
        response = _vista_de_prueba(request)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/login/'))
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `source .venv/bin/activate && DBURL="sqlite:///$(pwd)/db.sqlite3.test" python manage.py test core -v 2`
Expected: FAIL — `ImportError: cannot import name 'modulo_required' from 'core.permisos'`.

- [ ] **Step 3: Implementar `modulo_required`**

En `core/permisos.py`, agregar al final:

```python
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def modulo_required(nombre_modulo):
    """Decorator para vistas: exige sesión iniciada y acceso al módulo
    `nombre_modulo` (ver `usuario_tiene_modulo`). Si no cumple, redirige a
    'dashboard' con un mensaje de error."""
    def decorador(vista):
        @login_required
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if not usuario_tiene_modulo(request.user, nombre_modulo):
                messages.error(request, 'No tienes permiso para acceder a este módulo.')
                return redirect('dashboard')
            return vista(request, *args, **kwargs)
        return envoltura
    return decorador
```

(Los imports nuevos van arriba del todo del archivo junto al resto — ver Step 3 completo del archivo abajo para el orden final.)

El archivo completo `core/permisos.py` debe quedar así:

```python
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def usuario_tiene_modulo(user, nombre_modulo):
    """True si `user` puede acceder al módulo `nombre_modulo`.

    Un superusuario siempre tiene acceso. Cualquier otro usuario necesita
    pertenecer a un Group de Django cuyo `name` sea exactamente `nombre_modulo`.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=nombre_modulo).exists()


def modulo_required(nombre_modulo):
    """Decorator para vistas: exige sesión iniciada y acceso al módulo
    `nombre_modulo` (ver `usuario_tiene_modulo`). Si no cumple, redirige a
    'dashboard' con un mensaje de error."""
    def decorador(vista):
        @login_required
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if not usuario_tiene_modulo(request.user, nombre_modulo):
                messages.error(request, 'No tienes permiso para acceder a este módulo.')
                return redirect('dashboard')
            return vista(request, *args, **kwargs)
        return envoltura
    return decorador
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `source .venv/bin/activate && DBURL="sqlite:///$(pwd)/db.sqlite3.test" python manage.py test core -v 2`
Expected: `OK` — 7 tests pasan en total (4 de Task 1 + 3 nuevos).

- [ ] **Step 5: Commit**

```bash
git add core/permisos.py core/tests.py
git commit -m "$(cat <<'EOF'
Agrega decorator modulo_required() para proteger vistas por grupo

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Migración de datos — crear el Group "Finanzas"

**Files:**
- Create: `core/migrations/0001_crear_grupo_finanzas.py`
- Modify: `core/tests.py`

**Interfaces:**
- Produces: garantiza que `Group.objects.get(name='Finanzas')` existe en cualquier entorno tras `migrate`, incluida la base de datos de test.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `core/tests.py`:

```python
class GrupoFinanzasMigrationTests(TestCase):
    def test_grupo_finanzas_existe_tras_migrar(self):
        self.assertTrue(Group.objects.filter(name='Finanzas').exists())
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `source .venv/bin/activate && DBURL="sqlite:///$(pwd)/db.sqlite3.test" python manage.py test core.tests.GrupoFinanzasMigrationTests -v 2`
Expected: FAIL — `AssertionError: False is not true` (el grupo no existe porque no hay migración que lo cree todavía).

- [ ] **Step 3: Crear la migración de datos**

Crear `core/migrations/0001_crear_grupo_finanzas.py`:

```python
from django.db import migrations


def crear_grupo_finanzas(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name='Finanzas')


def eliminar_grupo_finanzas(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name='Finanzas').delete()


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(crear_grupo_finanzas, eliminar_grupo_finanzas),
    ]
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `source .venv/bin/activate && DBURL="sqlite:///$(pwd)/db.sqlite3.test" python manage.py test core -v 2`
Expected: `OK` — 8 tests pasan en total.

- [ ] **Step 5: Aplicar la migración en la base de datos real (dev)**

Run: `source .venv/bin/activate && python manage.py migrate core`
Expected: `Applying core.0001_crear_grupo_finanzas... OK`

Verificar en shell:

Run: `source .venv/bin/activate && python manage.py shell -c "from django.contrib.auth.models import Group; print(Group.objects.filter(name='Finanzas').exists())"`
Expected: `True`

- [ ] **Step 6: Commit**

```bash
git add core/migrations/0001_crear_grupo_finanzas.py core/tests.py
git commit -m "$(cat <<'EOF'
Agrega migración de datos que crea el Group "Finanzas"

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Filtro de plantilla `tiene_modulo`

**Files:**
- Create: `core/templatetags/__init__.py`
- Create: `core/templatetags/permisos_tags.py`
- Modify: `core/tests.py`

**Interfaces:**
- Consumes: `usuario_tiene_modulo(user, nombre_modulo)` de Task 1.
- Produces: filtro de plantilla `tiene_modulo`, cargado con `{% load permisos_tags %}`, uso: `{{ request.user|tiene_modulo:'Finanzas' }}`. Usado por Task 6 en `templates/base.html`.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `core/tests.py`:

```python
from django.template import Context, Template


class TieneModuloTemplateTagTests(TestCase):
    def setUp(self):
        self.grupo_finanzas, _ = Group.objects.get_or_create(name='Finanzas')
        self.usuario_con_grupo = User.objects.create_user('con_grupo3', password='x')
        self.usuario_con_grupo.groups.add(self.grupo_finanzas)
        self.usuario_sin_grupo = User.objects.create_user('sin_grupo3', password='x')

    def _renderizar(self, user):
        plantilla = Template(
            "{% load permisos_tags %}{% if user|tiene_modulo:'Finanzas' %}SI{% else %}NO{% endif %}"
        )
        return plantilla.render(Context({'user': user}))

    def test_filtro_devuelve_si_para_usuario_en_grupo(self):
        self.assertEqual(self._renderizar(self.usuario_con_grupo), 'SI')

    def test_filtro_devuelve_no_para_usuario_fuera_del_grupo(self):
        self.assertEqual(self._renderizar(self.usuario_sin_grupo), 'NO')
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `source .venv/bin/activate && DBURL="sqlite:///$(pwd)/db.sqlite3.test" python manage.py test core.tests.TieneModuloTemplateTagTests -v 2`
Expected: FAIL — `django.template.exceptions.TemplateSyntaxError: 'permisos_tags' is not a registered tag library.`

- [ ] **Step 3: Implementar el template tag**

Crear `core/templatetags/__init__.py` (vacío):

```python
```

Crear `core/templatetags/permisos_tags.py`:

```python
from django import template

from core.permisos import usuario_tiene_modulo

register = template.Library()


@register.filter(name='tiene_modulo')
def tiene_modulo(user, nombre_modulo):
    return usuario_tiene_modulo(user, nombre_modulo)
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `source .venv/bin/activate && DBURL="sqlite:///$(pwd)/db.sqlite3.test" python manage.py test core -v 2`
Expected: `OK` — 10 tests pasan en total.

- [ ] **Step 5: Commit**

```bash
git add core/templatetags/__init__.py core/templatetags/permisos_tags.py core/tests.py
git commit -m "$(cat <<'EOF'
Agrega filtro de plantilla tiene_modulo para controlar visibilidad en el sidebar

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Proteger las vistas de Finanzas con `modulo_required('Finanzas')`

**Files:**
- Modify: `finanzas/views.py` (32 vistas)
- Modify: `finanzas/tests.py`

**Interfaces:**
- Consumes: `core.permisos.modulo_required('Finanzas')` de Task 2.

- [ ] **Step 1: Escribir los tests que fallan**

Reemplazar el contenido de `finanzas/tests.py`:

```python
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse


class AccesoFinanzasTests(TestCase):
    def setUp(self):
        self.grupo_finanzas, _ = Group.objects.get_or_create(name='Finanzas')
        self.usuario_con_grupo = User.objects.create_user('fin_con_grupo', password='x')
        self.usuario_con_grupo.groups.add(self.grupo_finanzas)
        self.usuario_sin_grupo = User.objects.create_user('fin_sin_grupo', password='x')
        self.superusuario = User.objects.create_superuser(
            'fin_admin', email='fin_admin@example.com', password='x'
        )

    def test_usuario_sin_grupo_no_accede_al_dashboard_de_finanzas(self):
        self.client.force_login(self.usuario_sin_grupo)
        response = self.client.get(reverse('finanzas:dashboard'))
        self.assertRedirects(response, reverse('dashboard'))

    def test_usuario_con_grupo_accede_al_dashboard_de_finanzas(self):
        self.client.force_login(self.usuario_con_grupo)
        response = self.client.get(reverse('finanzas:dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_superusuario_accede_sin_estar_en_el_grupo(self):
        self.client.force_login(self.superusuario)
        response = self.client.get(reverse('finanzas:dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_usuario_anonimo_es_redirigido_a_login(self):
        response = self.client.get(reverse('finanzas:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)
```

- [ ] **Step 2: Correr los tests y confirmar que fallan**

Run: `source .venv/bin/activate && DBURL="sqlite:///$(pwd)/db.sqlite3.test" python manage.py test finanzas -v 2`
Expected: FAIL en `test_usuario_sin_grupo_no_accede_al_dashboard_de_finanzas` (hoy responde 200 porque solo hay `@login_required`, sin chequeo de grupo). Los otros 3 tests ya pasan hoy.

- [ ] **Step 3: Reemplazar `@login_required` por `@modulo_required('Finanzas')` en todas las vistas**

Run:
```bash
sed -i "s/^from django.contrib.auth.decorators import login_required$/from core.permisos import modulo_required/" finanzas/views.py
sed -i "s/^@login_required$/@modulo_required('Finanzas')/" finanzas/views.py
```

Verificar que no quedó ningún `@login_required` y que hay 32 usos de `@modulo_required`:

Run: `grep -c "@login_required" finanzas/views.py`
Expected: `0`

Run: `grep -c "@modulo_required" finanzas/views.py`
Expected: `32`

- [ ] **Step 4: Correr los tests y confirmar que pasan**

Run: `source .venv/bin/activate && DBURL="sqlite:///$(pwd)/db.sqlite3.test" python manage.py test finanzas -v 2`
Expected: `OK` — 4 tests pasan.

También volver a correr la suite de `core` para asegurar que nada se rompió:

Run: `source .venv/bin/activate && DBURL="sqlite:///$(pwd)/db.sqlite3.test" python manage.py test core -v 2`
Expected: `OK` — 10 tests pasan.

- [ ] **Step 5: Verificar arranque de la app**

Run: `source .venv/bin/activate && python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 6: Commit**

```bash
git add finanzas/views.py finanzas/tests.py
git commit -m "$(cat <<'EOF'
Protege las vistas de Finanzas con modulo_required('Finanzas')

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Ocultar el link de Finanzas en el sidebar y mostrar mensajes de error

**Files:**
- Modify: `templates/base.html`
- Modify: `referencias/tests.py`

**Interfaces:**
- Consumes: filtro `tiene_modulo` de Task 4.

**Nota:** hoy `templates/base.html` no renderiza `{{ messages }}` en ninguna parte, así que el mensaje de error que dispara `modulo_required` al redirigir a `dashboard` no se vería. Este task agrega un bloque de mensajes mínimo en `<main>` (usa el mismo estilo que ya usan las plantillas de `finanzas/`) para que ese mensaje sea visible en cualquier página, incluida `dashboard.html`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `referencias/tests.py`:

```python
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse


class SidebarFinanzasVisibilityTests(TestCase):
    def setUp(self):
        self.grupo_finanzas, _ = Group.objects.get_or_create(name='Finanzas')
        self.usuario_con_grupo = User.objects.create_user('side_con_grupo', password='x')
        self.usuario_con_grupo.groups.add(self.grupo_finanzas)
        self.usuario_sin_grupo = User.objects.create_user('side_sin_grupo', password='x')

    def test_usuario_con_grupo_ve_el_link_de_finanzas(self):
        self.client.force_login(self.usuario_con_grupo)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'href="/finanzas/"')

    def test_usuario_sin_grupo_no_ve_el_link_de_finanzas(self):
        self.client.force_login(self.usuario_sin_grupo)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'href="/finanzas/"')
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `source .venv/bin/activate && DBURL="sqlite:///$(pwd)/db.sqlite3.test" python manage.py test referencias.tests.SidebarFinanzasVisibilityTests -v 2`
Expected: FAIL en `test_usuario_con_grupo_ve_el_link_de_finanzas` (hoy el link solo se muestra a `is_superuser`, y `usuario_con_grupo` no lo es).

- [ ] **Step 3: Actualizar `templates/base.html`**

Al inicio del archivo, cambiar:

```html
{% load static %}
```

por:

```html
{% load static %}
{% load permisos_tags %}
```

Cambiar el bloque del link de Finanzas de:

```html
      {% if request.user.is_superuser %}
      <a href="{% url 'finanzas:dashboard' %}"
         class="sidebar-link {% if request.resolver_match.app_name == 'finanzas' %}active{% endif %}">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 11h.01M12 11h.01M15 11h.01M4 19h16a2 2 0 002-2V7a2 2 0 00-2-2H4a2 2 0 00-2 2v10a2 2 0 002 2z"/>
        </svg>
        Finanzas
      </a>
      {% endif %}
```

a:

```html
      {% if request.user|tiene_modulo:'Finanzas' %}
      <a href="{% url 'finanzas:dashboard' %}"
         class="sidebar-link {% if request.resolver_match.app_name == 'finanzas' %}active{% endif %}">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 11h.01M12 11h.01M15 11h.01M4 19h16a2 2 0 002-2V7a2 2 0 00-2-2H4a2 2 0 00-2 2v10a2 2 0 002 2z"/>
        </svg>
        Finanzas
      </a>
      {% endif %}
```

Cambiar el bloque `<main>` de:

```html
  <!-- Main content -->
  <main class="lg:ml-60 flex-1 min-h-screen pt-14 lg:pt-0">
    {% block content %}{% endblock %}
  </main>
```

a:

```html
  <!-- Main content -->
  <main class="lg:ml-60 flex-1 min-h-screen pt-14 lg:pt-0">
    {% if messages %}
    <div class="px-4 pt-4 space-y-2">
      {% for m in messages %}
      <div class="px-4 py-3 rounded-lg text-sm
        {% if m.tags == 'error' %}bg-red-50 text-red-700 border border-red-200
        {% else %}bg-emerald-50 text-emerald-700 border border-emerald-200{% endif %}">
        {{ m }}
      </div>
      {% endfor %}
    </div>
    {% endif %}
    {% block content %}{% endblock %}
  </main>
```

- [ ] **Step 4: Correr el test y confirmar que pasa**

Run: `source .venv/bin/activate && DBURL="sqlite:///$(pwd)/db.sqlite3.test" python manage.py test referencias.tests.SidebarFinanzasVisibilityTests -v 2`
Expected: `OK` — 2 tests pasan.

- [ ] **Step 5: Correr toda la suite del proyecto**

Run: `source .venv/bin/activate && DBURL="sqlite:///$(pwd)/db.sqlite3.test" python manage.py test core finanzas referencias -v 2`
Expected: `OK` — todos los tests pasan (10 de `core` + 4 de `finanzas` + los de `referencias`, incluidos los 2 nuevos).

- [ ] **Step 6: Verificación manual en el navegador**

1. Run: `source .venv/bin/activate && python manage.py runserver`
2. En `/admin/auth/group/`, confirmar que existe el grupo "Finanzas" (creado por la migración de Task 3).
3. Crear o usar un usuario staff sin superusuario y sin el grupo "Finanzas". Iniciar sesión: el sidebar NO debe mostrar "Finanzas", y visitar `/finanzas/` directamente debe redirigir al dashboard con el mensaje "No tienes permiso para acceder a este módulo." visible en rojo.
4. Agregar ese usuario al grupo "Finanzas" desde `/admin/auth/user/<id>/change/`. Recargar el dashboard: el link "Finanzas" debe aparecer, y `/finanzas/` debe cargar normalmente.

- [ ] **Step 7: Commit**

```bash
git add templates/base.html referencias/tests.py
git commit -m "$(cat <<'EOF'
Oculta el link de Finanzas en el sidebar según el grupo del usuario

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
