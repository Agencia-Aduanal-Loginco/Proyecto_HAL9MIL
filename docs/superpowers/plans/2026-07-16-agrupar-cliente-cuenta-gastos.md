# Agrupar por Cliente — Cuenta de Gastos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agrupar visualmente por cliente las referencias de la pestaña "Pendientes" de Cuenta de Gastos, con grupos colapsables (contraídos por defecto) usando `<details>` nativo, sin JavaScript.

**Architecture:** La consulta cambia su orden a `nombre_cliente` primero; la vista agrupa la página ya paginada con `itertools.groupby` (sin queries adicionales) y pasa `grupos_cliente` al contexto; la plantilla reemplaza la tabla plana por un `<details>` por cliente.

**Tech Stack:** Django 5.2, `itertools.groupby` (stdlib), Tailwind (clases ya usadas en el archivo).

## Global Constraints

- Solo se toca la pestaña **Pendientes** de `/cuenta-gastos/`. La pestaña Finalizadas no cambia.
- Sin JavaScript nuevo: los grupos usan `<details>`/`<summary>` nativos del navegador.
- Grupos **contraídos por defecto** (sin atributo `open`).
- La columna "Cliente" se quita de las filas dentro de un grupo (el nombre ya está en el `<summary>`).
- El botón "Finalizar" no cambia de comportamiento.
- Un cliente puede quedar cortado entre dos páginas — no se ajusta el tamaño de página para evitarlo (fuera de alcance).
- No hay acción "Finalizar todas" por cliente en esta iteración (fuera de alcance).
- Spec de referencia: `docs/superpowers/specs/2026-07-16-agrupar-cliente-cuenta-gastos-design.md`.

---

### Task 1: Orden por cliente y agrupado en la vista

**Files:**
- Modify: `referencias/views.py:1-16` (imports), `referencias/views.py:744-783` (rama pendientes de `cuenta_gastos`)
- Test: `referencias/tests.py` (agregar clase nueva)

**Interfaces:**
- Produce: contexto de `cuenta_gastos` (rama pendientes) incluye `'grupos_cliente': [{'nombre_cliente': str, 'referencias': [Referencia, ...]}, ...]`, además del `'page'` ya existente.

- [ ] **Step 1: Escribir la prueba que falla**

Agregar a `referencias/tests.py`:

```python
from datetime import date

from .models import CuentaGastos, Referencia


def _crear_referencia_pendiente(num_refe, nombre_cliente, fecha_pago, **extra):
    defaults = dict(
        patente='1656', prefijo='LCRR',
        num_operacion='OP1', linea_captura='LC1',
        es_rectificacion=False,
        nombre_cliente=nombre_cliente,
        fecha_pago=fecha_pago,
    )
    defaults.update(extra)
    return Referencia.objects.create(num_refe=num_refe, **defaults)


class CuentaGastosAgruparPorClienteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('cg_agrupa', password='x')
        self.client.force_login(self.user)
        # Dos referencias de ACME (fechas distintas) y una de BETA.
        self.acme_vieja = _crear_referencia_pendiente(
            'LCRR0001/26', 'ACME SA', date(2026, 7, 1),
        )
        self.acme_nueva = _crear_referencia_pendiente(
            'LCRR0002/26', 'ACME SA', date(2026, 7, 10),
        )
        self.beta = _crear_referencia_pendiente(
            'LCRR0003/26', 'BETA SA', date(2026, 7, 5),
        )
        # Referencia ya finalizada: no debe aparecer en pendientes.
        finalizada = _crear_referencia_pendiente(
            'LCRR0004/26', 'ACME SA', date(2026, 7, 2),
        )
        CuentaGastos.objects.create(referencia=finalizada, fecha_finalizacion=timezone.now())

    def test_grupos_cliente_en_contexto_agrupa_por_cliente(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        grupos = resp.context['grupos_cliente']
        nombres = [g['nombre_cliente'] for g in grupos]
        self.assertEqual(nombres, ['ACME SA', 'BETA SA'])
        acme_group = grupos[0]
        self.assertEqual(len(acme_group['referencias']), 2)

    def test_orden_dentro_del_grupo_es_por_fecha_pago_descendente(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        acme_group = resp.context['grupos_cliente'][0]
        refs = acme_group['referencias']
        self.assertEqual(refs[0].num_refe, 'LCRR0002/26')  # más reciente primero
        self.assertEqual(refs[1].num_refe, 'LCRR0001/26')

    def test_referencia_finalizada_no_aparece_en_grupos(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        todas_las_refs = [
            r.num_refe
            for g in resp.context['grupos_cliente']
            for r in g['referencias']
        ]
        self.assertNotIn('LCRR0004/26', todas_las_refs)
```

Agregar el import que falte al encabezado de `referencias/tests.py`:

```python
from django.utils import timezone
```

- [ ] **Step 2: Verificar que falla**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test referencias.tests.CuentaGastosAgruparPorClienteTests -v2`
Expected: `KeyError: 'grupos_cliente'` (la clave no existe aún en el contexto).

- [ ] **Step 3: Implementar el cambio de orden y el agrupado**

En `referencias/views.py`, agregar el import al inicio del archivo (línea 1):

```python
import calendar as cal
import itertools
import json
```

En la rama `else` (pendientes) de `cuenta_gastos` (líneas 744-783), reemplazar:

```python
        qs = qs.order_by('-fecha_pago')
```

por:

```python
        qs = qs.order_by('nombre_cliente', '-fecha_pago')
```

Y reemplazar:

```python
        paginador = Paginator(qs, 50)
        pagina    = paginador.get_page(request.GET.get('page', 1))

        ctx = {
            'tab':              'pendientes',
            'page':             pagina,
            'q':                q,
            'filtro_patente':   patente,
            'filtro_año':       año,
            'filtro_mes':       mes,
            'total':            qs.count(),
            'años_disponibles': años_disponibles,
            'meses':            meses_lista,
        }
```

por:

```python
        paginador = Paginator(qs, 50)
        pagina    = paginador.get_page(request.GET.get('page', 1))

        grupos_cliente = [
            {'nombre_cliente': nombre, 'referencias': list(refs)}
            for nombre, refs in itertools.groupby(
                pagina.object_list, key=lambda r: r.nombre_cliente
            )
        ]

        ctx = {
            'tab':              'pendientes',
            'page':             pagina,
            'grupos_cliente':   grupos_cliente,
            'q':                q,
            'filtro_patente':   patente,
            'filtro_año':       año,
            'filtro_mes':       mes,
            'total':            qs.count(),
            'años_disponibles': años_disponibles,
            'meses':            meses_lista,
        }
```

- [ ] **Step 4: Verificar que pasa**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test referencias.tests.CuentaGastosAgruparPorClienteTests -v2`
Expected: `Ran 3 tests ... OK`

- [ ] **Step 5: Correr toda la suite de `referencias` para descartar regresiones**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test referencias`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add referencias/views.py referencias/tests.py
git commit -m "feat(referencias): agrupar por cliente en la vista de Cuenta de Gastos pendientes"
```

---

### Task 2: Plantilla — grupos colapsables por cliente

**Files:**
- Modify: `templates/referencias/cuenta_gastos.html:225-316` (bloque de la tabla de Pendientes)
- Test: `referencias/tests.py` (agregar a la misma clase o una nueva)

**Interfaces:**
- Consume: `grupos_cliente` del contexto (Task 1) — cada elemento `{'nombre_cliente': str, 'referencias': [Referencia, ...]}`.

- [ ] **Step 1: Escribir la prueba que falla**

Agregar a `referencias/tests.py` (misma clase `CuentaGastosAgruparPorClienteTests`):

```python
    def test_template_renderiza_un_details_colapsado_por_cliente(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        html = resp.content.decode()
        self.assertContains(resp, '<details')
        self.assertContains(resp, 'ACME SA')
        self.assertContains(resp, 'BETA SA')
        # Contraído por defecto: no debe existir un <details ... open>
        self.assertNotIn('<details open', html)
        self.assertNotIn('<details class="group" open', html)

    def test_columna_cliente_ya_no_aparece_en_las_filas(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        self.assertNotContains(resp, '<th class="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Cliente</th>')

    def test_boton_finalizar_sigue_presente(self):
        self.user.is_staff = True
        self.user.save()
        resp = self.client.get(reverse('cuenta_gastos'))
        self.assertContains(resp, 'Finalizar')
        self.assertContains(resp, 'abrirModal(')
```

- [ ] **Step 2: Verificar que falla**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test referencias.tests.CuentaGastosAgruparPorClienteTests -v2`
Expected: fallan `test_template_renderiza_un_details_colapsado_por_cliente` y `test_columna_cliente_ya_no_aparece_en_las_filas` (la plantilla actual no tiene `<details>` y sí tiene la columna Cliente). `test_boton_finalizar_sigue_presente` puede pasar ya (no toca el botón), pero verificar junto con las demás.

- [ ] **Step 3: Reemplazar el bloque de la tabla de Pendientes**

En `templates/referencias/cuenta_gastos.html`, reemplazar el bloque completo desde `{% else %}` (línea 224, inicio de la rama Pendientes) hasta el `{% endif %}` que la cierra (línea 333), por:

```html
    {% else %}
    <!-- ── TAB PENDIENTES ─────────────────────────────────────────────────── -->
    {% if grupos_cliente %}
    <div>
      {% for grupo in grupos_cliente %}
      <details class="border-b border-slate-100 last:border-0">
        <summary class="cursor-pointer list-none px-4 py-3 flex items-center justify-between bg-slate-50 hover:bg-slate-100 transition-colors">
          <span class="font-semibold text-slate-700">{{ grupo.nombre_cliente|default:"Sin cliente" }}</span>
          <span class="text-xs text-slate-400 font-medium">
            {{ grupo.referencias|length }} referencia{{ grupo.referencias|length|pluralize }}
          </span>
        </summary>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="border-b border-slate-100 bg-white text-left">
                <th class="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Referencia</th>
                <th class="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">F. Pago</th>
                <th class="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Contenedores</th>
                <th class="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">BL</th>
                {% if request.user.is_staff %}
                <th class="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Acción</th>
                {% endif %}
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-50">
              {% for ref in grupo.referencias %}
              <tr class="hover:bg-slate-50 transition-colors">

                <td class="px-4 py-3">
                  <div class="flex items-center gap-2">
                    {% if ref.patente == "1627" %}
                      <span class="w-2 h-2 rounded-full bg-sky-500 flex-shrink-0"></span>
                    {% elif ref.patente == "1656" %}
                      <span class="w-2 h-2 rounded-full bg-emerald-500 flex-shrink-0"></span>
                    {% else %}
                      <span class="w-2 h-2 rounded-full bg-violet-500 flex-shrink-0"></span>
                    {% endif %}
                    <a href="{% url 'detalle' ref.num_refe %}"
                       class="font-mono font-medium text-sky-700 hover:text-sky-900 hover:underline">
                      {{ ref.num_refe }}
                    </a>
                  </div>
                </td>

                <td class="px-4 py-3 text-slate-500 whitespace-nowrap">
                  {{ ref.fecha_pago|date:"d/m/Y"|default:"—" }}
                </td>

                <td class="px-4 py-3">
                  {% if ref.contenedores.all %}
                    <div class="flex flex-wrap gap-1">
                      {% for cont in ref.contenedores.all %}
                        <span class="inline-flex items-center gap-1 rounded bg-slate-100 text-slate-600 text-xs px-1.5 py-0.5 font-mono">
                          {{ cont.num_cont }}
                          {% if cont.tipo %}<span class="text-slate-400">{{ cont.tipo }}</span>{% endif %}
                        </span>
                      {% endfor %}
                    </div>
                  {% else %}
                    <span class="text-slate-300">—</span>
                  {% endif %}
                </td>

                <td class="px-4 py-3">
                  {% if ref.guias.all %}
                    <div class="flex flex-wrap gap-1">
                      {% for guia in ref.guias.all %}
                        <span class="inline-flex items-center rounded bg-indigo-50 text-indigo-700 text-xs px-1.5 py-0.5 font-mono">
                          {% if guia.tipo_guia == "H" %}<span class="text-indigo-400 mr-0.5">H·</span>{% endif %}
                          {{ guia.numero_guia }}
                        </span>
                      {% endfor %}
                    </div>
                  {% else %}
                    <span class="text-slate-300">—</span>
                  {% endif %}
                </td>

                {% if request.user.is_staff %}
                <td class="px-4 py-3 text-right">
                  <button
                    onclick="abrirModal({{ ref.pk }}, '{{ ref.num_refe|escapejs }}')"
                    class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg transition-colors">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
                    </svg>
                    Finalizar
                  </button>
                </td>
                {% endif %}

              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </details>
      {% endfor %}
    </div>
    {% else %}
    <div class="py-16 text-center">
      <svg class="w-12 h-12 mx-auto text-slate-200 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
          d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
      </svg>
      <p class="text-slate-400 font-medium">No hay referencias pendientes</p>
      <p class="text-slate-300 text-sm mt-1">
        {% if q or filtro_patente or filtro_año or filtro_mes %}
          Prueba ajustando los filtros
        {% else %}
          Todas las referencias pagadas han sido finalizadas
        {% endif %}
      </p>
    </div>
    {% endif %}
    {% endif %}
```

Nota: se reemplaza `{% if page.object_list %}` (usado en el bloque original) por
`{% if grupos_cliente %}`, ya que ahora es la variable que controla si hay
contenido que mostrar en la pestaña Pendientes.

- [ ] **Step 4: Verificar que pasa**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test referencias.tests.CuentaGastosAgruparPorClienteTests -v2`
Expected: `Ran 6 tests ... OK`

- [ ] **Step 5: Correr toda la suite de `referencias` y `finanzas` para descartar regresiones**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test referencias finanzas`
Expected: `OK`

- [ ] **Step 6: `manage.py check`**

Run: `.venv/bin/python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 7: Commit**

```bash
git add templates/referencias/cuenta_gastos.html referencias/tests.py
git commit -m "feat(referencias): grupos colapsables por cliente en Cuenta de Gastos pendientes"
```

---

## Self-Review

**Cobertura del spec:** orden (Task 1) → agrupado en vista (Task 1) →
plantilla con `<details>` contraídos, sin columna Cliente, botón Finalizar
intacto (Task 2) → pestaña Finalizadas sin tocar (ningún task la modifica).
Todo cubierto.

**Placeholders:** ninguno — cada paso tiene código completo y comandos
exactos.

**Consistencia de tipos:** `grupos_cliente` se define en Task 1 como
`[{'nombre_cliente': str, 'referencias': [Referencia, ...]}, ...]` y Task 2
consume exactamente esas dos claves (`grupo.nombre_cliente`,
`grupo.referencias`) sin desviación.
