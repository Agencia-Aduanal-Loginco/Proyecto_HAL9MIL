# Carga de Facturas Abierta a Todos los Usuarios — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que cualquier usuario autenticado (no solo Finanzas) pueda subir facturas de cliente vía un nuevo ítem del menú principal, sin toparse con enlaces internos rotos hacia páginas exclusivas de Finanzas.

**Architecture:** Se relaja el decorador de `carga_xml_cliente` de `@modulo_required('Finanzas')` a `@login_required`. Se agrega una entrada al sidebar (`templates/base.html`), visible a cualquier usuario logueado. Se ocultan (con `{% if request.user|tiene_modulo:'Finanzas' %}`) los dos enlaces existentes que apuntan a `xml_pendientes` — página que sigue siendo exclusiva de Finanzas. Sin modelos, sin migraciones, sin cambios al pipeline de carga.

**Tech Stack:** Django 6.0.5, Tailwind CSS (CDN), `django.test.TestCase`.

**Spec:** `docs/superpowers/specs/2026-07-12-menu-carga-facturas-design.md`

## Global Constraints

- Directorio de trabajo: `/home/tony/Developer/Proyecto_HAL9MIL/` — entorno virtual `.venv/`, activar con `source .venv/bin/activate`.
- Crear una rama de feature antes de empezar (no trabajar en `main`).
- Tests con `django.test.TestCase`. BD de tests es Postgres remota: correr siempre con `--keepdb`.
- `xml_pendientes` (y su decorador `@modulo_required('Finanzas')`) NO se tocan — sigue exclusivo de Finanzas.
- El filtro de plantilla `tiene_modulo` ya existe y está en uso en `templates/base.html` (línea 114) — no requiere import adicional en templates.

---

## File Map

| Archivo | Acción | Responsabilidad |
|---------|--------|-----------------|
| `finanzas/views.py` | Modificar | `carga_xml_cliente` usa `@login_required` en vez de `@modulo_required('Finanzas')` |
| `finanzas/test_carga_cliente.py` | Modificar | Tests de acceso abierto y de los enlaces condicionales |
| `templates/base.html` | Modificar | Nueva entrada de menú "Carga de Facturas" |
| `templates/finanzas/carga_cliente_form.html` | Modificar | Breadcrumb a `dashboard` general; enlace a `xml_pendientes` condicional |
| `templates/finanzas/carga_masiva_resultado.html` | Modificar | Enlace "Asignar los pendientes →" condicional a Finanzas |

---

## Task 1: Permiso abierto en la vista de carga

**Files:**
- Modify: `finanzas/views.py:1113` (decorador de `carga_xml_cliente`)
- Modify: `finanzas/test_carga_cliente.py`

**Interfaces:**
- Consumes: `django.contrib.auth.decorators.login_required` (nuevo import)
- Produces: `carga_xml_cliente` accesible a cualquier usuario autenticado (antes: solo grupo Finanzas). Usado por Task 2 para el ítem de menú.

- [ ] **Step 1: Escribir el test que falla**

Agregar a `finanzas/test_carga_cliente.py`, dentro de la clase `CargaClienteViewTests` (junto a `test_usuario_sin_grupo_es_redirigido`, que hoy espera 302 — ese test se reemplaza por el de abajo, que espera 200):

Reemplazar el método existente:

```python
    def test_usuario_sin_grupo_es_redirigido(self):
        User.objects.create_user('sin_grupo', password='x')
        self.client.login(username='sin_grupo', password='x')
        resp = self.client.get(reverse('finanzas:carga_xml_cliente'))
        self.assertEqual(resp.status_code, 302)
```

Por:

```python
    def test_usuario_sin_grupo_finanzas_puede_acceder(self):
        User.objects.create_user('sin_grupo', password='x')
        self.client.login(username='sin_grupo', password='x')
        resp = self.client.get(reverse('finanzas:carga_xml_cliente'))
        self.assertEqual(resp.status_code, 200)

    def test_usuario_anonimo_es_redirigido_a_login(self):
        self.client.logout()
        resp = self.client.get(reverse('finanzas:carga_xml_cliente'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp.url)
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
source .venv/bin/activate
python manage.py test finanzas.test_carga_cliente.CargaClienteViewTests --keepdb --verbosity=2
```

Esperado: `test_usuario_sin_grupo_finanzas_puede_acceder` falla (`302 != 200` — hoy redirige por falta del grupo Finanzas). `test_usuario_anonimo_es_redirigido_a_login` puede pasar ya (ambos decoradores exigen login).

- [ ] **Step 3: Cambiar el decorador en `finanzas/views.py`**

Agregar el import (junto a los demás imports de `django.contrib`, línea ~7):

```python
from django.contrib.auth.decorators import login_required
```

Y cambiar (línea ~1113):

```python
@modulo_required('Finanzas')
def carga_xml_cliente(request):
```

Por:

```python
@login_required
def carga_xml_cliente(request):
```

- [ ] **Step 4: Ejecutar y verificar que pasa**

```bash
python manage.py test finanzas.test_carga_cliente.CargaClienteViewTests --keepdb --verbosity=2
```

Esperado: `OK` — 6 tests pasando (5 originales, uno renombrado + uno nuevo).

- [ ] **Step 5: Verificar que nada más se rompió**

```bash
python manage.py test finanzas.test_carga_cliente finanzas.test_carga_masiva --keepdb --verbosity=1
```

Esperado: `OK`.

- [ ] **Step 6: Commit**

```bash
git add finanzas/views.py finanzas/test_carga_cliente.py
git commit -m "feat(finanzas): abrir carga_xml_cliente a cualquier usuario autenticado"
```

---

## Task 2: Entrada de menú + reparar enlaces internos hacia Finanzas

**Files:**
- Modify: `templates/base.html`
- Modify: `templates/finanzas/carga_cliente_form.html`
- Modify: `templates/finanzas/carga_masiva_resultado.html`
- Modify: `finanzas/test_carga_cliente.py`

**Interfaces:**
- Consumes: `carga_xml_cliente` con `@login_required` (Task 1); filtro de plantilla `tiene_modulo` (ya existe, usado en `base.html:114`).
- Produces: entrada de sidebar visible a cualquier usuario logueado; los dos enlaces hacia `xml_pendientes` solo se muestran a usuarios con el módulo Finanzas.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `finanzas/test_carga_cliente.py`, dentro de la clase `CargaClienteViewTests`:

```python
    def test_usuario_sin_finanzas_no_ve_enlace_a_pendientes(self):
        User.objects.create_user('sin_grupo2', password='x')
        self.client.login(username='sin_grupo2', password='x')
        resp = self.client.get(reverse('finanzas:carga_xml_cliente'))
        self.assertNotContains(resp, reverse('finanzas:xml_pendientes'))

    def test_usuario_finanzas_si_ve_enlace_a_pendientes(self):
        resp = self.client.get(reverse('finanzas:carga_xml_cliente'))
        self.assertContains(resp, reverse('finanzas:xml_pendientes'))

    def test_resultado_oculta_asignar_pendientes_a_usuario_sin_finanzas(self):
        User.objects.create_user('sin_grupo3', password='x')
        self.client.login(username='sin_grupo3', password='x')
        xml = SimpleUploadedFile('F99999.xml', cfdi_cliente(
            uuid='55555555-5555-5555-5555-555555555555',
        ))
        resp = self.client.post(reverse('finanzas:carga_xml_cliente'), {'archivos': [xml]})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Asignar los pendientes')

    def test_resultado_muestra_asignar_pendientes_a_finanzas(self):
        xml = SimpleUploadedFile('F88888.xml', cfdi_cliente(
            uuid='66666666-6666-6666-6666-666666666666',
        ))
        resp = self.client.post(reverse('finanzas:carga_xml_cliente'), {'archivos': [xml]})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Asignar los pendientes')
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

```bash
python manage.py test finanzas.test_carga_cliente.CargaClienteViewTests --keepdb --verbosity=2
```

Esperado: los 4 tests nuevos fallan (hoy ambos enlaces se muestran siempre, sin condicionar por `tiene_modulo`).

- [ ] **Step 3: Agregar la entrada de menú en `templates/base.html`**

Insertar, inmediatamente después del `{% endif %}` que cierra el bloque `{% if request.user.is_superuser %}` (línea ~105, el que contiene Clientes y SLA Capturistas) y ANTES del enlace "Cuenta de Gastos" (línea ~106):

```html
      <a href="{% url 'finanzas:carga_xml_cliente' %}"
         class="sidebar-link {% if request.resolver_match.url_name == 'carga_xml_cliente' %}active{% endif %}">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/>
        </svg>
        Carga de Facturas
      </a>
```

No modificar ninguna otra línea del `<nav>` — el bloque de Clientes/SLA Capturistas y su `{% if %}/{% endif %}` quedan intactos.

- [ ] **Step 4: Reparar `templates/finanzas/carga_cliente_form.html`**

Reemplazar el bloque completo del encabezado:

```html
  <div class="mb-6">
    <a href="{% url 'finanzas:dashboard' %}" class="text-sky-600 hover:underline text-sm">← Finanzas</a>
    <h1 class="text-2xl font-bold text-slate-800 mt-2">Cargar facturas de cliente</h1>
    <p class="text-slate-500 text-sm">
      Sube los XML y sus PDF (emparejados por nombre de archivo, ej.
      <span class="font-mono">F123.xml</span> + <span class="font-mono">F123.pdf</span>).
      Las facturas quedarán en
      <a href="{% url 'finanzas:xml_pendientes' %}" class="text-sky-600 hover:underline">XMLs pendientes</a>,
      donde el RFC del receptor te ayudará a anexarlas a la referencia del cliente.
    </p>
  </div>
```

Por:

```html
  <div class="mb-6">
    <a href="{% url 'dashboard' %}" class="text-sky-600 hover:underline text-sm">← Inicio</a>
    <h1 class="text-2xl font-bold text-slate-800 mt-2">Cargar facturas de cliente</h1>
    <p class="text-slate-500 text-sm">
      Sube los XML y sus PDF (emparejados por nombre de archivo, ej.
      <span class="font-mono">F123.xml</span> + <span class="font-mono">F123.pdf</span>).
      {% if request.user|tiene_modulo:'Finanzas' %}
      Las facturas quedarán en
      <a href="{% url 'finanzas:xml_pendientes' %}" class="text-sky-600 hover:underline">XMLs pendientes</a>,
      donde el RFC del receptor te ayudará a anexarlas a la referencia del cliente.
      {% else %}
      El equipo de Finanzas revisará e integrará tu factura a la cuenta de gastos correspondiente.
      {% endif %}
    </p>
  </div>
```

- [ ] **Step 5: Reparar `templates/finanzas/carga_masiva_resultado.html`**

Reemplazar:

```html
  {% if conteos.pendientes %}
  <div class="mt-4">
    <a href="{% url 'finanzas:xml_pendientes' %}" class="text-sky-600 hover:underline text-sm">
      Asignar los pendientes →
    </a>
  </div>
  {% endif %}
```

Por:

```html
  {% if conteos.pendientes and request.user|tiene_modulo:'Finanzas' %}
  <div class="mt-4">
    <a href="{% url 'finanzas:xml_pendientes' %}" class="text-sky-600 hover:underline text-sm">
      Asignar los pendientes →
    </a>
  </div>
  {% endif %}
```

- [ ] **Step 6: Ejecutar y verificar que los tests pasan**

```bash
python manage.py test finanzas.test_carga_cliente --keepdb --verbosity=2
```

Esperado: `OK` — 10 tests pasando (6 de Task 1 + 4 nuevos).

- [ ] **Step 7: Verificar que la carga masiva (LCT/APM, exclusiva de Finanzas) no se rompió**

`carga_masiva_resultado.html` es compartido entre `carga_masiva_xml` (Finanzas) y `carga_xml_cliente` (ahora abierta) — confirmar que quien llega desde carga masiva, al ser siempre de Finanzas, sigue viendo el enlace:

```bash
python manage.py test finanzas.test_carga_masiva --keepdb --verbosity=1
```

Esperado: `OK` (los tests de `test_post_zip_procesa_y_muestra_resumen` en `test_carga_masiva.py` corren con un usuario del grupo Finanzas, así que deben seguir viendo la pantalla de resultado sin cambios de comportamiento visible).

- [ ] **Step 8: Verificación visual rápida**

Con el servidor corriendo (`python manage.py runserver 8001`):
1. Loguearse con un usuario que NO esté en el grupo Finanzas.
2. Confirmar que "Carga de Facturas" aparece en el sidebar.
3. Entrar, subir un XML de prueba, confirmar que la pantalla de resultado NO muestra "Asignar los pendientes →".
4. Loguearse con un usuario del grupo Finanzas y repetir — confirmar que si SÍ ve ese enlace y el texto sobre "XMLs pendientes" en el formulario.

- [ ] **Step 9: Commit**

```bash
git add templates/base.html templates/finanzas/carga_cliente_form.html templates/finanzas/carga_masiva_resultado.html finanzas/test_carga_cliente.py
git commit -m "feat(finanzas): entrada de menú y enlaces condicionales para carga de facturas abierta a todos"
```

---

## Verificación End-to-End

- [ ] **Suite completa**: `python manage.py test finanzas --keepdb` → `OK`.
- [ ] **`manage.py check`** limpio.
- [ ] Confirmar en el navegador (Step 8 de Task 2) que el flujo completo funciona para ambos tipos de usuario.
