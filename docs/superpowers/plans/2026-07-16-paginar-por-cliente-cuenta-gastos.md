# Paginar por Cliente — Cuenta de Gastos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cambiar la paginación de la pestaña Pendientes de Cuenta de Gastos de "50 referencias por página" a "50 clientes por página", para que cada página muestre siempre grupos de cliente completos.

**Architecture:** El `Paginator` deja de envolver la queryset de `Referencia` y pasa a envolver la lista de nombres de cliente distintos (ya filtrada); las referencias de la página se obtienen filtrando por esos nombres y se agrupan igual que antes.

**Tech Stack:** Django 5.2, `django.core.paginator.Paginator`, `itertools.groupby` (ya en uso).

## Global Constraints

- Solo la pestaña Pendientes de `/cuenta-gastos/`. Finalizadas no cambia.
- El tamaño de página sigue siendo 50 (ahora 50 *clientes*, no 50 *referencias*).
- El encabezado ("X referencias pendientes de finalizar") no cambia — sigue siendo el total de referencias.
- El pie de página, solo en la pestaña Pendientes, pasa de "... · N registros" a "... · N clientes" (N = `page.paginator.count`, el total de clientes distintos paginados). La pestaña Finalizadas conserva "... · N registros".
- Spec de referencia: `docs/superpowers/specs/2026-07-16-paginar-por-cliente-cuenta-gastos-design.md`.

---

### Task 1: Paginar por clientes distintos en la vista

**Files:**
- Modify: `referencias/views.py` (rama pendientes de `cuenta_gastos`, bloque de paginación y `grupos_cliente`)
- Modify: `templates/referencias/cuenta_gastos.html:342-357` (texto del pie de página)
- Test: `referencias/tests.py` (agregar a `CuentaGastosAgruparPorClienteTests` o clase nueva)

**Interfaces:**
- Produce: `page` en el contexto de la rama pendientes ahora es un `Page` de **nombres de cliente** (no de `Referencia`); `page.paginator.count` = total de clientes distintos. `grupos_cliente` conserva la misma forma `[{'nombre_cliente': str, 'referencias': [Referencia, ...]}, ...]`.

- [ ] **Step 1: Escribir la prueba que falla**

Agregar a `referencias/tests.py` (nueva clase, en el mismo archivo):

```python
class CuentaGastosPaginarPorClienteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('cg_pagina', password='x')
        self.client.force_login(self.user)
        # 51 clientes distintos, una referencia cada uno, para forzar 2 páginas.
        for i in range(51):
            _crear_referencia_pendiente(
                f'LCRR{i:04d}/26', f'CLIENTE {i:03d}', date(2026, 7, 1),
            )

    def test_pagina_1_trae_50_clientes_completos(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        grupos = resp.context['grupos_cliente']
        self.assertEqual(len(grupos), 50)

    def test_pagina_2_trae_el_cliente_restante(self):
        resp = self.client.get(reverse('cuenta_gastos'), {'page': 2})
        grupos = resp.context['grupos_cliente']
        self.assertEqual(len(grupos), 1)

    def test_total_de_paginas_es_por_clientes_no_por_referencias(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        self.assertEqual(resp.context['page'].paginator.num_pages, 2)
        self.assertEqual(resp.context['page'].paginator.count, 51)

    def test_pie_de_pagina_muestra_clientes(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        self.assertContains(resp, '51 clientes')
```

- [ ] **Step 2: Verificar que falla**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test referencias.tests.CuentaGastosPaginarPorClienteTests -v2`
Expected: `test_pagina_1_trae_50_clientes_completos` falla (hoy `len(grupos)` depende de cuántas referencias caben en 50 filas, no de 50 clientes completos — con 1 referencia por cliente da 50 grupos "por casualidad", así que en su lugar `test_total_de_paginas_es_por_clientes_no_por_referencias` y `test_pie_de_pagina_muestra_clientes` deben fallar: hoy `page.paginator.count` es 51 referencias, pero como cada cliente tiene exactamente 1 referencia, el conteo también da 51 — revisar cuál assertion realmente distingue el comportamiento antes de continuar; si por la coincidencia de "1 referencia por cliente" ninguna prueba falla, ajustar `setUp` para que el cliente `CLIENTE 000` tenga 2 referencias en vez de 1 (así con paginación por referencias la página 1 tendría 50 referencias pero solo 49 clientes completos, mientras que con paginación por clientes tendría 50 clientes completos con 51 referencias) y confirmar que las pruebas fallan por la razón correcta antes de implementar.

- [ ] **Step 3: Implementar la paginación por cliente**

En `referencias/views.py`, reemplazar:

```python
        paginador = Paginator(qs, 50)
        pagina    = paginador.get_page(request.GET.get('page', 1))

        grupos_cliente = [
            {'nombre_cliente': nombre, 'referencias': list(refs)}
            for nombre, refs in itertools.groupby(
                pagina.object_list, key=lambda r: r.nombre_cliente
            )
        ]
```

por:

```python
        clientes_distintos = list(
            qs.values_list('nombre_cliente', flat=True)
            .distinct().order_by('nombre_cliente')
        )
        paginador = Paginator(clientes_distintos, 50)
        pagina    = paginador.get_page(request.GET.get('page', 1))

        referencias_pagina = qs.filter(
            nombre_cliente__in=list(pagina.object_list)
        ).order_by('nombre_cliente', '-fecha_pago')
        grupos_cliente = [
            {'nombre_cliente': nombre, 'referencias': list(refs)}
            for nombre, refs in itertools.groupby(
                referencias_pagina, key=lambda r: r.nombre_cliente
            )
        ]
```

- [ ] **Step 4: Actualizar el pie de página en la plantilla**

En `templates/referencias/cuenta_gastos.html`, reemplazar:

```html
      <span>Página {{ page.number }} de {{ page.paginator.num_pages }} · {{ total }} registros</span>
```

por:

```html
      <span>
        Página {{ page.number }} de {{ page.paginator.num_pages }} ·
        {% if tab == 'pendientes' %}
          {{ page.paginator.count }} cliente{{ page.paginator.count|pluralize }}
        {% else %}
          {{ total }} registro{{ total|pluralize }}
        {% endif %}
      </span>
```

- [ ] **Step 5: Verificar que pasa**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test referencias.tests.CuentaGastosPaginarPorClienteTests referencias.tests.CuentaGastosAgruparPorClienteTests -v2`
Expected: `OK`

- [ ] **Step 6: Correr toda la suite de `referencias` y `finanzas`**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test referencias finanzas`
Expected: `OK`

- [ ] **Step 7: `manage.py check`**

Run: `.venv/bin/python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 8: Commit**

```bash
git add referencias/views.py templates/referencias/cuenta_gastos.html referencias/tests.py
git commit -m "feat(referencias): paginar Cuenta de Gastos pendientes por 50 clientes en vez de 50 referencias"
```

## Self-Review

**Cobertura del spec:** paginación por clientes distintos, referencias de la página filtradas por esos clientes, pie de página con conteo de clientes solo en pendientes — todo cubierto en Task 1 (una sola tarea, cambio acotado a un método y una línea de plantilla).

**Placeholders:** ninguno.

**Consistencia de tipos:** `grupos_cliente` conserva exactamente la misma forma que ya consume la plantilla (Task de la spec anterior) — no requiere cambios en el bloque `{% for grupo in grupos_cliente %}`.
