# Dashboard de Pipeline Financiero Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pipeline health section to the finanzas dashboard so the team can see, at a glance, whether invoice collection (AR), supplier expense processing (AP), and póliza elaboration are keeping pace, using progress-bar funnels plus an 8-week trend chart.

**Architecture:** A new pure-function module `finanzas/pipeline.py` computes three independent data shapes (AR funnel, AP funnel, weekly trend) from existing models (`Factura`, `XMLProveedor`, `GastoReferencia`, `PolizaContable`). `finanzas/views.py`'s existing `dashboard_financiero` view calls these functions and adds their output to the render context. `templates/finanzas/dashboard.html` gets a new section with Tailwind progress bars and a Chart.js line chart (Chart.js is already loaded globally in `templates/base.html`).

**Tech Stack:** Django 5.2, Django ORM (`Q` objects, `__year`/`__month` lookups), Tailwind CSS (CDN, no build step), Chart.js 4.4 (already loaded via CDN in `templates/base.html`).

## Global Constraints

- Data window for both funnels: current month + previous month (rolling), matching the existing pattern in `referencias/views.py:497-521` (`glosa` view) — use the same `year/month` + `prev_year/prev_month` `Q` filter pattern, not date-range filters.
- Reuse `finanzas.cobranza.calcular_saldo_factura` for the "cobrada" criterion in the AR funnel — do not duplicate saldo logic.
- All new `pipeline.py` functions accept an optional `hoy` parameter (a `datetime.date`) for deterministic testing, matching the existing pattern in `finanzas/cobranza.py` and `finanzas/jobs.py`.
- `USE_TZ = True` in `hal9mil/settings.py` — all `DateTimeField` values in tests (`Factura.fecha_emision`, `XMLProveedor.fecha_emision`) must be timezone-aware (`django.utils.timezone.make_aware(...)`).
- Do not modify the existing tarjetas section of `templates/finanzas/dashboard.html` — only add a new section below it.

---

### Task 1: `finanzas/pipeline.py` — ventana helper + `calcular_embudo_ar`

**Files:**
- Create: `finanzas/pipeline.py`
- Modify: `finanzas/tests.py` (append at end of file)

**Interfaces:**
- Produces: `_mes_actual_y_anterior(hoy=None) -> tuple[int, int, int, int]` (year, month, prev_year, prev_month)
- Produces: `calcular_embudo_ar(hoy=None) -> dict` with keys `emitidas`, `timbradas`, `cobradas` (all `int`)
- Consumes: `finanzas.cobranza.calcular_saldo_factura(factura) -> Decimal` (existing, unchanged)

- [ ] **Step 1: Write the failing tests**

Append to `finanzas/tests.py`:

```python
from datetime import date, datetime
from django.utils import timezone
from finanzas.pipeline import calcular_embudo_ar


class EmbudoARTest(TestCase):
    def setUp(self):
        self.config = ConfiguracionFiscal.objects.create(
            patente='3772', rfc='REI123456789', razon_social='Reiki',
            regimen_fiscal='601', codigo_postal='06600',
            cert_path='', key_path='',
        )

    def _crear_factura(self, folio, estado, fecha_emision, total=Decimal('1000.00')):
        return Factura.objects.create(
            folio=folio,
            rfc_receptor='CLI123456789',
            nombre_receptor='Cliente Test',
            domicilio_fiscal_receptor='06600',
            regimen_fiscal_receptor='601',
            subtotal=total,
            iva=Decimal('0.00'),
            total=total,
            estado=estado,
            fecha_emision=fecha_emision,
            configuracion_fiscal=self.config,
        )

    def test_borrador_no_cuenta_como_timbrada(self):
        self._crear_factura(100, 'BORRADOR', timezone.make_aware(datetime(2026, 7, 1)))
        resultado = calcular_embudo_ar(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['emitidas'], 1)
        self.assertEqual(resultado['timbradas'], 0)
        self.assertEqual(resultado['cobradas'], 0)

    def test_timbrada_pagada_cuenta_como_cobrada(self):
        factura = self._crear_factura(101, 'TIMBRADA', timezone.make_aware(datetime(2026, 7, 1)))
        pago = Pago.objects.create(
            fecha_pago='2026-07-02', monto=factura.total, moneda='MXN', forma_pago='03',
        )
        DoctoRelacionado.objects.create(
            pago=pago, factura=factura, num_parcialidad=1,
            imp_saldo_anterior=factura.total, imp_pagado=factura.total,
            imp_saldo_insoluto=Decimal('0'),
        )
        resultado = calcular_embudo_ar(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['timbradas'], 1)
        self.assertEqual(resultado['cobradas'], 1)

    def test_factura_fuera_de_ventana_no_se_cuenta(self):
        self._crear_factura(102, 'TIMBRADA', timezone.make_aware(datetime(2026, 3, 1)))
        resultado = calcular_embudo_ar(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['emitidas'], 0)

    def test_factura_cancelada_no_se_cuenta(self):
        self._crear_factura(103, 'CANCELADA', timezone.make_aware(datetime(2026, 7, 1)))
        resultado = calcular_embudo_ar(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['emitidas'], 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test finanzas.tests.EmbudoARTest -v 2`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'finanzas.pipeline'`

- [ ] **Step 3: Create `finanzas/pipeline.py`**

```python
from datetime import date
from decimal import Decimal

from django.db.models import Q


def _mes_actual_y_anterior(hoy=None):
    """Retorna (year, month, prev_year, prev_month) para la ventana móvil de 2 meses."""
    hoy = hoy or date.today()
    year, month = hoy.year, hoy.month
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    return year, month, prev_year, prev_month


def calcular_embudo_ar(hoy=None):
    """
    Embudo de Cuentas por Cobrar: facturas emitidas -> timbradas -> cobradas,
    en la ventana móvil de mes actual + mes anterior.
    """
    from .cobranza import calcular_saldo_factura
    from .models import Factura

    year, month, prev_year, prev_month = _mes_actual_y_anterior(hoy)

    facturas = Factura.objects.exclude(estado='CANCELADA').filter(
        Q(fecha_emision__year=year, fecha_emision__month=month) |
        Q(fecha_emision__year=prev_year, fecha_emision__month=prev_month) |
        Q(fecha_emision__isnull=True, created_at__year=year, created_at__month=month) |
        Q(fecha_emision__isnull=True, created_at__year=prev_year, created_at__month=prev_month)
    )

    emitidas = facturas.count()
    timbradas_qs = facturas.filter(estado='TIMBRADA')
    timbradas = timbradas_qs.count()
    cobradas = sum(
        1 for f in timbradas_qs if calcular_saldo_factura(f) <= Decimal('0')
    )

    return {'emitidas': emitidas, 'timbradas': timbradas, 'cobradas': cobradas}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test finanzas.tests.EmbudoARTest -v 2`
Expected: `Ran 4 tests ... OK`

- [ ] **Step 5: Commit**

```bash
git add finanzas/pipeline.py finanzas/tests.py
git commit -m "feat(finanzas): calcular_embudo_ar para pipeline de cuentas por cobrar"
```

---

### Task 2: `calcular_embudo_ap`

**Files:**
- Modify: `finanzas/pipeline.py` (append function)
- Modify: `finanzas/tests.py` (append at end of file)

**Interfaces:**
- Consumes: `_mes_actual_y_anterior(hoy=None)` from Task 1
- Produces: `calcular_embudo_ap(hoy=None) -> dict` with keys `recibidos`, `procesados`, `con_poliza` (all `int`)

- [ ] **Step 1: Write the failing tests**

Append to `finanzas/tests.py`:

```python
import uuid
from django.core.files.uploadedfile import SimpleUploadedFile
from finanzas.pipeline import calcular_embudo_ap
from finanzas.models import GastoReferencia, XMLProveedor, PolizaContable


class EmbudoAPTest(TestCase):
    def setUp(self):
        self.referencia = Referencia.objects.create(
            num_refe='RE51-9001-26', patente='3772', prefijo='RE51',
            cve_cliente='CLI01', nombre_cliente='Cliente AP Test',
        )

    def _crear_xml(self, fecha_emision, procesado=False):
        return XMLProveedor.objects.create(
            referencia=self.referencia,
            uuid_fiscal=uuid.uuid4(),
            fecha_emision=fecha_emision,
            rfc_emisor='PRO123456789',
            nombre_emisor='Proveedor Test',
            rfc_receptor='REI123456789',
            subtotal=Decimal('500.00'),
            iva=Decimal('80.00'),
            total=Decimal('580.00'),
            tipo_comprobante='I',
            xml_file=SimpleUploadedFile('test.xml', b'<xml></xml>'),
            procesado=procesado,
        )

    def test_xml_no_procesado_no_cuenta_en_procesados(self):
        self._crear_xml(timezone.make_aware(datetime(2026, 7, 1)), procesado=False)
        resultado = calcular_embudo_ap(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['recibidos'], 1)
        self.assertEqual(resultado['procesados'], 0)

    def test_gasto_sin_poliza_no_cuenta_en_con_poliza(self):
        xml = self._crear_xml(timezone.make_aware(datetime(2026, 7, 1)), procesado=True)
        GastoReferencia.objects.create(
            referencia=self.referencia, tipo='OTROS', concepto='Gasto test',
            fecha=date(2026, 7, 1), monto=Decimal('580.00'),
            xml_proveedor=xml,
        )
        resultado = calcular_embudo_ap(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['procesados'], 1)
        self.assertEqual(resultado['con_poliza'], 0)

    def test_gasto_con_poliza_cuenta(self):
        xml = self._crear_xml(timezone.make_aware(datetime(2026, 7, 1)), procesado=True)
        poliza = PolizaContable.objects.create(
            numero='E-1', tipo='E', fecha=date(2026, 7, 1), mes=7, anio=2026,
            concepto='Póliza test',
        )
        GastoReferencia.objects.create(
            referencia=self.referencia, tipo='OTROS', concepto='Gasto test',
            fecha=date(2026, 7, 1), monto=Decimal('580.00'),
            xml_proveedor=xml, poliza=poliza,
        )
        resultado = calcular_embudo_ap(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['con_poliza'], 1)

    def test_xml_fuera_de_ventana_no_se_cuenta(self):
        self._crear_xml(timezone.make_aware(datetime(2026, 3, 1)), procesado=True)
        resultado = calcular_embudo_ap(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['recibidos'], 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test finanzas.tests.EmbudoAPTest -v 2`
Expected: FAIL/ERROR — `ImportError: cannot import name 'calcular_embudo_ap'`

- [ ] **Step 3: Append `calcular_embudo_ap` to `finanzas/pipeline.py`**

Add at the end of `finanzas/pipeline.py`:

```python
def calcular_embudo_ap(hoy=None):
    """
    Embudo de Cuentas por Pagar: XML de proveedor recibidos -> procesados ->
    con póliza generada, en la ventana móvil de mes actual + mes anterior.
    """
    from .models import GastoReferencia, XMLProveedor

    year, month, prev_year, prev_month = _mes_actual_y_anterior(hoy)

    xmls = XMLProveedor.objects.filter(
        Q(fecha_emision__year=year, fecha_emision__month=month) |
        Q(fecha_emision__year=prev_year, fecha_emision__month=prev_month)
    )

    recibidos = xmls.count()
    procesados_qs = xmls.filter(procesado=True)
    procesados = procesados_qs.count()
    con_poliza = GastoReferencia.objects.filter(
        xml_proveedor__in=procesados_qs, poliza__isnull=False
    ).count()

    return {'recibidos': recibidos, 'procesados': procesados, 'con_poliza': con_poliza}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test finanzas.tests.EmbudoAPTest -v 2`
Expected: `Ran 4 tests ... OK`

- [ ] **Step 5: Commit**

```bash
git add finanzas/pipeline.py finanzas/tests.py
git commit -m "feat(finanzas): calcular_embudo_ap para pipeline de cuentas por pagar"
```

---

### Task 3: `calcular_tendencia_semanal`

**Files:**
- Modify: `finanzas/pipeline.py` (append function + import)
- Modify: `finanzas/tests.py` (append at end of file)

**Interfaces:**
- Produces: `calcular_tendencia_semanal(semanas=8, hoy=None) -> dict` with keys `labels` (`list[str]`, ISO date of each week's Monday), `facturas_timbradas` (`list[int]`), `polizas_generadas` (`list[int]`)

- [ ] **Step 1: Write the failing tests**

Append to `finanzas/tests.py`:

```python
from finanzas.pipeline import calcular_tendencia_semanal


class TendenciaSemanalTest(TestCase):
    def setUp(self):
        self.config = ConfiguracionFiscal.objects.create(
            patente='3772', rfc='REI123456789', razon_social='Reiki',
            regimen_fiscal='601', codigo_postal='06600',
            cert_path='', key_path='',
        )

    def test_agrupa_facturas_timbradas_por_semana(self):
        Factura.objects.create(
            folio=200, rfc_receptor='CLI123456789', nombre_receptor='Cliente Test',
            domicilio_fiscal_receptor='06600', regimen_fiscal_receptor='601',
            subtotal=Decimal('100'), iva=Decimal('0'), total=Decimal('100'),
            estado='TIMBRADA', fecha_emision=timezone.make_aware(datetime(2026, 6, 15)),
            configuracion_fiscal=self.config,
        )
        Factura.objects.create(
            folio=201, rfc_receptor='CLI123456789', nombre_receptor='Cliente Test',
            domicilio_fiscal_receptor='06600', regimen_fiscal_receptor='601',
            subtotal=Decimal('100'), iva=Decimal('0'), total=Decimal('100'),
            estado='TIMBRADA', fecha_emision=timezone.make_aware(datetime(2026, 6, 22)),
            configuracion_fiscal=self.config,
        )
        resultado = calcular_tendencia_semanal(semanas=4, hoy=date(2026, 7, 1))
        self.assertEqual(len(resultado['labels']), 4)
        self.assertEqual(sum(resultado['facturas_timbradas']), 2)

    def test_agrupa_polizas_por_semana(self):
        PolizaContable.objects.create(
            numero='D-1', tipo='D', fecha=date(2026, 6, 24), mes=6, anio=2026,
            concepto='Test',
        )
        resultado = calcular_tendencia_semanal(semanas=4, hoy=date(2026, 7, 1))
        self.assertEqual(sum(resultado['polizas_generadas']), 1)

    def test_factura_borrador_no_cuenta_en_tendencia(self):
        Factura.objects.create(
            folio=202, rfc_receptor='CLI123456789', nombre_receptor='Cliente Test',
            domicilio_fiscal_receptor='06600', regimen_fiscal_receptor='601',
            subtotal=Decimal('100'), iva=Decimal('0'), total=Decimal('100'),
            estado='BORRADOR', fecha_emision=timezone.make_aware(datetime(2026, 6, 22)),
            configuracion_fiscal=self.config,
        )
        resultado = calcular_tendencia_semanal(semanas=4, hoy=date(2026, 7, 1))
        self.assertEqual(sum(resultado['facturas_timbradas']), 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test finanzas.tests.TendenciaSemanalTest -v 2`
Expected: FAIL/ERROR — `ImportError: cannot import name 'calcular_tendencia_semanal'`

- [ ] **Step 3: Append `calcular_tendencia_semanal` to `finanzas/pipeline.py`**

Change the top import line:

```python
from datetime import date
```

to:

```python
from datetime import date, timedelta
```

Add at the end of `finanzas/pipeline.py`:

```python
def calcular_tendencia_semanal(semanas=8, hoy=None):
    """
    Series semanales (lunes a domingo) de facturas timbradas y pólizas
    generadas, para las últimas `semanas` semanas terminando en la semana
    que contiene `hoy`.
    """
    from .models import Factura, PolizaContable

    hoy = hoy or date.today()
    lunes_actual = hoy - timedelta(days=hoy.weekday())

    labels = []
    facturas_por_semana = []
    polizas_por_semana = []

    for i in range(semanas - 1, -1, -1):
        inicio_semana = lunes_actual - timedelta(weeks=i)
        fin_semana = inicio_semana + timedelta(days=6)
        labels.append(inicio_semana.isoformat())
        facturas_por_semana.append(
            Factura.objects.filter(
                estado='TIMBRADA',
                fecha_emision__date__gte=inicio_semana,
                fecha_emision__date__lte=fin_semana,
            ).count()
        )
        polizas_por_semana.append(
            PolizaContable.objects.filter(
                fecha__gte=inicio_semana, fecha__lte=fin_semana,
            ).count()
        )

    return {
        'labels': labels,
        'facturas_timbradas': facturas_por_semana,
        'polizas_generadas': polizas_por_semana,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test finanzas.tests.TendenciaSemanalTest -v 2`
Expected: `Ran 3 tests ... OK`

- [ ] **Step 5: Commit**

```bash
git add finanzas/pipeline.py finanzas/tests.py
git commit -m "feat(finanzas): calcular_tendencia_semanal para ritmo de trabajo reciente"
```

---

### Task 4: Wire `dashboard_financiero` view to the pipeline module

**Files:**
- Modify: `finanzas/views.py:1-3` (imports), `finanzas/views.py:32-53` (`dashboard_financiero`)
- Modify: `finanzas/tests.py` (append at end of file)

**Interfaces:**
- Consumes: `calcular_embudo_ar(hoy=None)`, `calcular_embudo_ap(hoy=None)`, `calcular_tendencia_semanal(semanas=8, hoy=None)` from Tasks 1-3
- Produces: `dashboard_financiero` context now includes `embudo_ar` (dict), `embudo_ap` (dict), `tendencia_labels_json`, `tendencia_facturas_json`, `tendencia_polizas_json` (all JSON strings) — consumed by Task 5's template.

- [ ] **Step 1: Write the failing test**

Append to `finanzas/tests.py`:

```python
from django.urls import reverse


class DashboardPipelineViewTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user('dashuser', password='x')
        self.client.login(username='dashuser', password='x')

    def test_dashboard_incluye_datos_de_pipeline(self):
        response = self.client.get(reverse('finanzas:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('embudo_ar', response.context)
        self.assertIn('embudo_ap', response.context)
        self.assertEqual(
            set(response.context['embudo_ar'].keys()),
            {'emitidas', 'timbradas', 'cobradas'},
        )
        self.assertEqual(
            set(response.context['embudo_ap'].keys()),
            {'recibidos', 'procesados', 'con_poliza'},
        )
        self.assertIn('tendencia_labels_json', response.context)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test finanzas.tests.DashboardPipelineViewTest -v 2`
Expected: FAIL — `KeyError: 'embudo_ar'` (context key does not exist yet)

- [ ] **Step 3: Update `finanzas/views.py`**

Change the import block at the top of `finanzas/views.py` from:

```python
import os
import tempfile
from decimal import Decimal
```

to:

```python
import json
import os
import tempfile
from decimal import Decimal
```

Add a new import line after `from .polizas import generar_poliza_anticipo, generar_poliza_gasto`:

```python
from .pipeline import calcular_embudo_ap, calcular_embudo_ar, calcular_tendencia_semanal
```

Replace the `dashboard_financiero` view:

```python
@login_required
def dashboard_financiero(request):
    total_anticipos = Anticipo.objects.count()
    total_gastos = GastoReferencia.objects.count()
    total_polizas = PolizaContable.objects.count()
    total_facturas = Factura.objects.count()
    total_pagos = Pago.objects.count()
    pendientes_cobro = (
        Referencia.objects
        .filter(gastos_finanzas__isnull=False)
        .exclude(facturas__estado='TIMBRADA')
        .distinct()
        .count()
    )
    return render(request, 'finanzas/dashboard.html', {
        'total_anticipos': total_anticipos,
        'total_gastos': total_gastos,
        'total_polizas': total_polizas,
        'total_facturas': total_facturas,
        'total_pagos': total_pagos,
        'pendientes_cobro': pendientes_cobro,
    })
```

with:

```python
@login_required
def dashboard_financiero(request):
    total_anticipos = Anticipo.objects.count()
    total_gastos = GastoReferencia.objects.count()
    total_polizas = PolizaContable.objects.count()
    total_facturas = Factura.objects.count()
    total_pagos = Pago.objects.count()
    pendientes_cobro = (
        Referencia.objects
        .filter(gastos_finanzas__isnull=False)
        .exclude(facturas__estado='TIMBRADA')
        .distinct()
        .count()
    )
    embudo_ar = calcular_embudo_ar()
    embudo_ap = calcular_embudo_ap()
    tendencia = calcular_tendencia_semanal()
    return render(request, 'finanzas/dashboard.html', {
        'total_anticipos': total_anticipos,
        'total_gastos': total_gastos,
        'total_polizas': total_polizas,
        'total_facturas': total_facturas,
        'total_pagos': total_pagos,
        'pendientes_cobro': pendientes_cobro,
        'embudo_ar': embudo_ar,
        'embudo_ap': embudo_ap,
        'tendencia_labels_json': json.dumps(tendencia['labels']),
        'tendencia_facturas_json': json.dumps(tendencia['facturas_timbradas']),
        'tendencia_polizas_json': json.dumps(tendencia['polizas_generadas']),
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test finanzas.tests.DashboardPipelineViewTest -v 2`
Expected: `Ran 1 test ... OK`

- [ ] **Step 5: Run the full finanzas + clientes suite to check for regressions**

Run: `python manage.py test finanzas clientes -v 2`
Expected: all tests OK (existing + new from Tasks 1-4)

- [ ] **Step 6: Commit**

```bash
git add finanzas/views.py finanzas/tests.py
git commit -m "feat(finanzas): dashboard_financiero expone datos de pipeline AR/AP y tendencia"
```

---

### Task 5: Template — funnel bars + weekly trend chart

**Files:**
- Modify: `templates/finanzas/dashboard.html`
- Modify: `finanzas/tests.py` (append test to `DashboardPipelineViewTest` from Task 4)

**Interfaces:**
- Consumes: `embudo_ar`, `embudo_ap`, `tendencia_labels_json`, `tendencia_facturas_json`, `tendencia_polizas_json` from Task 4's view context.

- [ ] **Step 1: Write the failing test**

Append a new test method to the `DashboardPipelineViewTest` class in `finanzas/tests.py` (added in Task 4):

```python
    def test_dashboard_muestra_secciones_de_pipeline(self):
        response = self.client.get(reverse('finanzas:dashboard'))
        content = response.content.decode()
        self.assertIn('Cuentas por Cobrar', content)
        self.assertIn('Cuentas por Pagar', content)
        self.assertIn('id="chartTendencia"', content)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test finanzas.tests.DashboardPipelineViewTest.test_dashboard_muestra_secciones_de_pipeline -v 2`
Expected: FAIL — `AssertionError: 'Cuentas por Cobrar' not found in ...`

- [ ] **Step 3: Update `templates/finanzas/dashboard.html`**

Current end of the file:

```html
  <div class="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-800">
    Para ver el estado financiero de una referencia, navega al detalle de la referencia
    o accede directo en <code class="font-mono bg-amber-100 px-1 rounded">/finanzas/referencias/&lt;num_refe&gt;/estado/</code>
  </div>
</div>
{% endblock %}
```

Replace it with:

```html
  <div class="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-800">
    Para ver el estado financiero de una referencia, navega al detalle de la referencia
    o accede directo en <code class="font-mono bg-amber-100 px-1 rounded">/finanzas/referencias/&lt;num_refe&gt;/estado/</code>
  </div>

  <!-- Pipeline de trabajo (mes actual + anterior) -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
    <div class="bg-white rounded-xl border border-slate-200 p-5">
      <h2 class="text-sm font-semibold text-slate-700 uppercase tracking-wider mb-4">Cuentas por Cobrar</h2>
      <div class="space-y-4">
        <div>
          <div class="flex justify-between text-xs text-slate-500 mb-1">
            <span>Facturas emitidas</span>
            <span>{{ embudo_ar.emitidas }}</span>
          </div>
          <div class="w-full bg-slate-100 rounded-full h-2">
            <div class="bg-sky-500 h-2 rounded-full" style="width: 100%"></div>
          </div>
        </div>
        <div>
          <div class="flex justify-between text-xs text-slate-500 mb-1">
            <span>Timbradas</span>
            <span>{{ embudo_ar.timbradas }} / {{ embudo_ar.emitidas }} ({% widthratio embudo_ar.timbradas embudo_ar.emitidas 100 %}%)</span>
          </div>
          <div class="w-full bg-slate-100 rounded-full h-2">
            <div class="bg-sky-500 h-2 rounded-full" style="width: {% widthratio embudo_ar.timbradas embudo_ar.emitidas 100 %}%"></div>
          </div>
        </div>
        <div>
          <div class="flex justify-between text-xs text-slate-500 mb-1">
            <span>Cobradas</span>
            <span>{{ embudo_ar.cobradas }} / {{ embudo_ar.timbradas }} ({% widthratio embudo_ar.cobradas embudo_ar.timbradas 100 %}%)</span>
          </div>
          <div class="w-full bg-slate-100 rounded-full h-2">
            <div class="bg-emerald-500 h-2 rounded-full" style="width: {% widthratio embudo_ar.cobradas embudo_ar.timbradas 100 %}%"></div>
          </div>
        </div>
      </div>
    </div>

    <div class="bg-white rounded-xl border border-slate-200 p-5">
      <h2 class="text-sm font-semibold text-slate-700 uppercase tracking-wider mb-4">Cuentas por Pagar</h2>
      <div class="space-y-4">
        <div>
          <div class="flex justify-between text-xs text-slate-500 mb-1">
            <span>XML de proveedor recibidos</span>
            <span>{{ embudo_ap.recibidos }}</span>
          </div>
          <div class="w-full bg-slate-100 rounded-full h-2">
            <div class="bg-violet-500 h-2 rounded-full" style="width: 100%"></div>
          </div>
        </div>
        <div>
          <div class="flex justify-between text-xs text-slate-500 mb-1">
            <span>Procesados</span>
            <span>{{ embudo_ap.procesados }} / {{ embudo_ap.recibidos }} ({% widthratio embudo_ap.procesados embudo_ap.recibidos 100 %}%)</span>
          </div>
          <div class="w-full bg-slate-100 rounded-full h-2">
            <div class="bg-violet-500 h-2 rounded-full" style="width: {% widthratio embudo_ap.procesados embudo_ap.recibidos 100 %}%"></div>
          </div>
        </div>
        <div>
          <div class="flex justify-between text-xs text-slate-500 mb-1">
            <span>Con póliza generada</span>
            <span>{{ embudo_ap.con_poliza }} / {{ embudo_ap.procesados }} ({% widthratio embudo_ap.con_poliza embudo_ap.procesados 100 %}%)</span>
          </div>
          <div class="w-full bg-slate-100 rounded-full h-2">
            <div class="bg-emerald-500 h-2 rounded-full" style="width: {% widthratio embudo_ap.con_poliza embudo_ap.procesados 100 %}%"></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Tendencia semanal -->
  <div class="bg-white rounded-xl border border-slate-200 p-5">
    <h2 class="text-sm font-semibold text-slate-700 uppercase tracking-wider mb-4">Tendencia semanal (últimas 8 semanas)</h2>
    <canvas id="chartTendencia" height="80"></canvas>
  </div>
</div>

<script>
new Chart(document.getElementById('chartTendencia'), {
  type: 'line',
  data: {
    labels: {{ tendencia_labels_json|safe }},
    datasets: [
      {
        label: 'Facturas timbradas', data: {{ tendencia_facturas_json|safe }},
        borderColor: '#0284c7', borderWidth: 2, pointRadius: 3, tension: .3, fill: false,
      },
      {
        label: 'Pólizas generadas', data: {{ tendencia_polizas_json|safe }},
        borderColor: '#7c3aed', borderWidth: 2, pointRadius: 3, tension: .3, fill: false,
      },
    ],
  },
  options: {
    responsive: true,
    plugins: { legend: { display: true, position: 'bottom' } },
    scales: {
      x: { grid: { color: '#f1f5f9' }, ticks: { font: { size: 11 } } },
      y: { grid: { color: '#f1f5f9' }, ticks: { font: { size: 11 } }, beginAtZero: true },
    },
  },
});
</script>
{% endblock %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test finanzas.tests.DashboardPipelineViewTest -v 2`
Expected: `Ran 2 tests ... OK`

- [ ] **Step 5: Run the full finanzas + clientes suite**

Run: `python manage.py test finanzas clientes -v 2`
Expected: all tests OK

- [ ] **Step 6: Manual smoke check in the browser**

Run: `python manage.py runserver 8001`, log in, visit `http://127.0.0.1:8001/finanzas/`. Confirm:
- The two funnel cards render without Django template errors (no `%` literals leaking into the page).
- The `chartTendencia` line chart renders with 2 series and 8 labeled points.
- No console errors in the browser dev tools related to Chart.js or JSON parsing.

- [ ] **Step 7: Commit**

```bash
git add templates/finanzas/dashboard.html finanzas/tests.py
git commit -m "feat(finanzas): panel de pipeline en dashboard — embudos AR/AP y tendencia semanal"
```
