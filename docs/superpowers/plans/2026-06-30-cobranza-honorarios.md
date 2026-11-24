# Sistema de Cobranza de Honorarios — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir un módulo de cobranza de facturas de honorarios aduanales con recordatorios automáticos por email a 15/30/60 días y panel de control por cliente.

**Architecture:** Se agrega `email_cobranza` al modelo `Cliente`, se crea el modelo `RecordatorioCobranza` en finanzas, y un módulo `finanzas/cobranza.py` con toda la lógica de negocio (cálculo de saldos, envío de email). El scheduler APScheduler existente registra un nuevo job diario. La vista `cobranza_list` se reemplaza completamente para mostrar saldos por cliente en lugar de por referencia.

**Tech Stack:** Django 5.2, APScheduler, SendGrid (via `django.core.mail.EmailMultiAlternatives`), Tailwind CSS (CDN), SQLite/PostgreSQL, Python 3.12.

## Global Constraints

- Python 3.12, Django 5.2
- Locale `es-mx` / `America/Mexico_City`
- Tests con `django.test.TestCase` en archivos `tests.py` de cada app
- Servidor local: `python manage.py runserver 8001`
- Entorno virtual: `.venv/` — activar con `source .venv/bin/activate`

---

## File Map

| Archivo | Acción | Responsabilidad |
|---------|--------|-----------------|
| `clientes/models.py` | Modificar | Agregar `email_cobranza`, `email_cobranza_cc` |
| `clientes/admin.py` | Modificar | Exponer nuevos campos en el admin |
| `clientes/migrations/0002_*.py` | Crear (auto) | Migración de campos email |
| `finanzas/models.py` | Modificar | Agregar modelo `RecordatorioCobranza` |
| `finanzas/migrations/0009_*.py` | Crear (auto) | Migración del nuevo modelo |
| `finanzas/cobranza.py` | Crear | Lógica de negocio: saldos, búsqueda de cliente, envío de email |
| `finanzas/jobs.py` | Crear | Job diario del scheduler (`ejecutar_cobranza_automatica`) |
| `finanzas/views.py` | Modificar | Reemplazar `cobranza_list`, agregar `cobranza_enviar` |
| `finanzas/urls.py` | Modificar | Agregar URL `cobranza/<cve_cliente>/enviar/` |
| `finanzas/tests.py` | Modificar | Tests de la lógica de cobranza |
| `reportes/scheduler.py` | Modificar | Registrar job diario de cobranza |
| `templates/finanzas/cobranza_list.html` | Reemplazar | Panel por cliente con detalle y botón enviar |
| `templates/finanzas/email_recordatorio_cobranza.html` | Crear | Template HTML del email |

---

## Task 1: Campos email en Cliente + migración

**Files:**
- Modify: `clientes/models.py`
- Modify: `clientes/admin.py`
- Create (auto): `clientes/migrations/0002_cliente_email_cobranza.py`
- Test: `clientes/tests.py` (crear si no existe)

**Interfaces:**
- Produces: `Cliente.email_cobranza: str`, `Cliente.email_cobranza_cc: str` — usados en Task 3 y Task 5

- [ ] **Step 1: Abrir `clientes/models.py` y agregar los campos**

Reemplazar el modelo `Cliente` completo:

```python
from django.db import models


class Cliente(models.Model):
    nombre_cliente    = models.CharField(max_length=255, unique=True)
    cve_cliente       = models.CharField(max_length=20, blank=True)
    rfc               = models.CharField(max_length=13, blank=True)
    email_cobranza    = models.EmailField(blank=True)
    email_cobranza_cc = models.EmailField(blank=True)

    class Meta:
        ordering        = ['nombre_cliente']
        verbose_name    = 'Cliente'
        verbose_name_plural = 'Clientes'

    def __str__(self):
        return self.nombre_cliente
```

- [ ] **Step 2: Actualizar `clientes/admin.py`**

```python
from django.contrib import admin
from .models import Cliente


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display  = ('nombre_cliente', 'cve_cliente', 'rfc', 'email_cobranza')
    search_fields = ('nombre_cliente', 'rfc', 'cve_cliente')
    ordering      = ('nombre_cliente',)
    fieldsets = (
        (None, {'fields': ('nombre_cliente', 'cve_cliente', 'rfc')}),
        ('Cobranza', {'fields': ('email_cobranza', 'email_cobranza_cc')}),
    )
```

- [ ] **Step 3: Crear el archivo de tests**

Crear `clientes/tests.py`:

```python
from django.test import TestCase
from .models import Cliente


class ClienteEmailTest(TestCase):
    def test_email_cobranza_blank_by_default(self):
        c = Cliente.objects.create(nombre_cliente='Test S.A.', cve_cliente='T001')
        self.assertEqual(c.email_cobranza, '')
        self.assertEqual(c.email_cobranza_cc, '')

    def test_email_cobranza_guardado(self):
        c = Cliente.objects.create(
            nombre_cliente='ACME S.A.',
            cve_cliente='ACME01',
            email_cobranza='cuentas@acme.com',
            email_cobranza_cc='contador@acme.com',
        )
        c.refresh_from_db()
        self.assertEqual(c.email_cobranza, 'cuentas@acme.com')
        self.assertEqual(c.email_cobranza_cc, 'contador@acme.com')
```

- [ ] **Step 4: Ejecutar migración**

```bash
source .venv/bin/activate
python manage.py makemigrations clientes --name email_cobranza
python manage.py migrate
```

Salida esperada: `Migrations for 'clientes': clientes/migrations/0002_email_cobranza.py`

- [ ] **Step 5: Ejecutar tests**

```bash
python manage.py test clientes --verbosity=2
```

Esperado: `OK` con 2 tests pasando.

- [ ] **Step 6: Commit**

```bash
git add clientes/models.py clientes/admin.py clientes/migrations/0002_*.py clientes/tests.py
git commit -m "feat(clientes): agregar email_cobranza y email_cobranza_cc a Cliente"
```

---

## Task 2: Modelo RecordatorioCobranza + migración

**Files:**
- Modify: `finanzas/models.py` (agregar al final del archivo)
- Create (auto): `finanzas/migrations/0009_recordatoriocobranza.py`

**Interfaces:**
- Consumes: `Factura` (ya existe en `finanzas/models.py`)
- Produces: `RecordatorioCobranza` con campos `factura`, `tipo`, `enviado_en`, `enviado_por`, `exitoso`, `error_msg` — usado en Tasks 3, 5 y 6

- [ ] **Step 1: Abrir `finanzas/models.py` y agregar el modelo al final**

Al final del archivo, después del modelo `ComisionReferencia`, agregar:

```python
# ── Cobranza ──────────────────────────────────────────────────────────────────

class RecordatorioCobranza(models.Model):
    TIPO_CHOICES = [
        ('15d', '15 días'), ('30d', '30 días'), ('60d', '60 días'), ('manual', 'Manual'),
    ]

    factura     = models.ForeignKey(
        Factura, on_delete=models.CASCADE, related_name='recordatorios'
    )
    tipo        = models.CharField(max_length=6, choices=TIPO_CHOICES)
    enviado_en  = models.DateTimeField(auto_now_add=True)
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )
    exitoso     = models.BooleanField(default=True)
    error_msg   = models.TextField(blank=True)

    class Meta:
        ordering = ['-enviado_en']
        verbose_name = 'Recordatorio de Cobranza'
        verbose_name_plural = 'Recordatorios de Cobranza'

    def __str__(self):
        return f'{self.factura} | {self.get_tipo_display()} | {self.enviado_en:%Y-%m-%d}'
```

- [ ] **Step 2: Escribir test del modelo en `finanzas/tests.py`**

```python
from django.test import TestCase
from django.contrib.auth import get_user_model
from decimal import Decimal
from finanzas.models import RecordatorioCobranza, Factura, ConfiguracionFiscal


class RecordatorioCobranzaTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user('testuser', password='x')
        config = ConfiguracionFiscal.objects.create(
            patente='3772', rfc='REI123456789', razon_social='Reiki',
            regimen_fiscal='601', codigo_postal='06600',
            cert_path='', key_path='',
        )
        self.factura = Factura.objects.create(
            folio=1,
            rfc_receptor='CLI123456789',
            nombre_receptor='Cliente Test',
            domicilio_fiscal_receptor='06600',
            regimen_fiscal_receptor='601',
            subtotal=Decimal('1000.00'),
            iva=Decimal('160.00'),
            total=Decimal('1160.00'),
            estado='TIMBRADA',
            configuracion_fiscal=config,
        )

    def test_crear_recordatorio(self):
        r = RecordatorioCobranza.objects.create(
            factura=self.factura,
            tipo='15d',
            enviado_por=self.user,
            exitoso=True,
        )
        self.assertEqual(r.tipo, '15d')
        self.assertTrue(r.exitoso)
        self.assertEqual(r.error_msg, '')

    def test_multiples_tipos_permitidos(self):
        RecordatorioCobranza.objects.create(factura=self.factura, tipo='15d')
        RecordatorioCobranza.objects.create(factura=self.factura, tipo='30d')
        RecordatorioCobranza.objects.create(factura=self.factura, tipo='manual')
        self.assertEqual(RecordatorioCobranza.objects.filter(factura=self.factura).count(), 3)
```

- [ ] **Step 3: Ejecutar migración**

```bash
python manage.py makemigrations finanzas --name recordatoriocobranza
python manage.py migrate
```

Esperado: `Migrations for 'finanzas': finanzas/migrations/0009_recordatoriocobranza.py`

- [ ] **Step 4: Ejecutar tests**

```bash
python manage.py test finanzas --verbosity=2
```

Esperado: `OK`

- [ ] **Step 5: Commit**

```bash
git add finanzas/models.py finanzas/migrations/0009_*.py finanzas/tests.py
git commit -m "feat(finanzas): agregar modelo RecordatorioCobranza"
```

---

## Task 3: Módulo de lógica de cobranza (`finanzas/cobranza.py`)

**Files:**
- Create: `finanzas/cobranza.py`
- Modify: `finanzas/tests.py` (agregar tests de cobranza)

**Interfaces:**
- Consumes: `Factura`, `RecordatorioCobranza` (Task 2), `Cliente.email_cobranza` (Task 1)
- Produces:
  - `calcular_saldo_factura(factura: Factura) -> Decimal`
  - `calcular_saldos_por_cliente() -> list[dict]`
  - `buscar_cliente_de_factura(factura: Factura) -> Cliente | None`
  - `enviar_recordatorio_factura(factura, cliente, tipo, usuario=None) -> bool`
  - `enviar_estado_cuenta_cliente(cliente, usuario=None) -> bool`

- [ ] **Step 1: Escribir los tests primero**

Agregar a `finanzas/tests.py` (debajo de los tests existentes del Task 2):

```python
from finanzas.cobranza import calcular_saldo_factura, calcular_saldos_por_cliente
from finanzas.models import DoctoRelacionado, Pago
from clientes.models import Cliente
from referencias.models import Referencia


class CalcSaldoFacturaTest(TestCase):
    def setUp(self):
        config = ConfiguracionFiscal.objects.create(
            patente='3772', rfc='REI123456789', razon_social='Reiki',
            regimen_fiscal='601', codigo_postal='06600',
            cert_path='', key_path='',
        )
        self.factura = Factura.objects.create(
            folio=2,
            rfc_receptor='CLI123456789',
            nombre_receptor='Cliente Test',
            domicilio_fiscal_receptor='06600',
            regimen_fiscal_receptor='601',
            subtotal=Decimal('1000.00'),
            iva=Decimal('160.00'),
            total=Decimal('1160.00'),
            estado='TIMBRADA',
            configuracion_fiscal=config,
        )

    def test_saldo_sin_pagos(self):
        saldo = calcular_saldo_factura(self.factura)
        self.assertEqual(saldo, Decimal('1160.00'))

    def test_saldo_con_pago_parcial(self):
        pago = Pago.objects.create(
            fecha_pago='2026-06-01', monto=Decimal('500.00'),
            moneda='MXN', forma_pago='03',
        )
        DoctoRelacionado.objects.create(
            pago=pago, factura=self.factura,
            num_parcialidad=1,
            imp_saldo_anterior=Decimal('1160.00'),
            imp_pagado=Decimal('500.00'),
            imp_saldo_insoluto=Decimal('660.00'),
        )
        saldo = calcular_saldo_factura(self.factura)
        self.assertEqual(saldo, Decimal('660.00'))

    def test_saldo_cero_si_pagada_completo(self):
        pago = Pago.objects.create(
            fecha_pago='2026-06-01', monto=Decimal('1160.00'),
            moneda='MXN', forma_pago='03',
        )
        DoctoRelacionado.objects.create(
            pago=pago, factura=self.factura,
            num_parcialidad=1,
            imp_saldo_anterior=Decimal('1160.00'),
            imp_pagado=Decimal('1160.00'),
            imp_saldo_insoluto=Decimal('0.00'),
        )
        saldo = calcular_saldo_factura(self.factura)
        self.assertEqual(saldo, Decimal('0.00'))
```

- [ ] **Step 2: Ejecutar tests para verificar que fallan**

```bash
python manage.py test finanzas.tests.CalcSaldoFacturaTest --verbosity=2
```

Esperado: `ImportError: cannot import name 'calcular_saldo_factura'`

- [ ] **Step 3: Crear `finanzas/cobranza.py`**

```python
import logging
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import Sum
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

ASUNTOS = {
    '15d':    'Recordatorio de pago — Reiki Logística',
    '30d':    'Saldo pendiente — Reiki Logística',
    '60d':    'Aviso urgente de saldo vencido — Reiki Logística',
    'manual': 'Estado de cuenta — Reiki Logística',
}


def calcular_saldo_factura(factura):
    """Retorna el saldo pendiente de una Factura (total - sum de pagos aplicados)."""
    pagado = (
        factura.documentos_pago
        .aggregate(t=Sum('imp_pagado'))['t'] or Decimal('0')
    )
    return factura.total - pagado


def buscar_cliente_de_factura(factura):
    """Retorna el Cliente asociado a la primera Referencia de la Factura, o None."""
    from clientes.models import Cliente
    ref = factura.referencias.first()
    if not ref:
        return None
    return Cliente.objects.filter(cve_cliente=ref.cve_cliente).first()


def calcular_saldos_por_cliente():
    """
    Retorna lista de dicts, uno por cliente con facturas TIMBRADAS pendientes,
    ordenada por días de antigüedad descendente (más vieja primero).

    Cada dict contiene:
        cve_cliente: str
        nombre: str
        cliente: Cliente | None
        facturas: list[dict]  — cada uno con keys: factura, saldo, dias, fecha_base
        saldo_total: Decimal
        dias_mayor: int
        ultimo_recordatorio: datetime | None
    """
    from .models import Factura

    facturas_timbradas = (
        Factura.objects
        .filter(estado='TIMBRADA')
        .prefetch_related('referencias', 'documentos_pago', 'recordatorios')
    )

    hoy = date.today()
    clientes = {}

    for factura in facturas_timbradas:
        saldo = calcular_saldo_factura(factura)
        if saldo <= Decimal('0'):
            continue

        ref = factura.referencias.first()
        if not ref:
            continue

        cve = ref.cve_cliente
        fecha_base = (
            factura.fecha_emision.date()
            if factura.fecha_emision
            else factura.created_at.date()
        )
        dias = (hoy - fecha_base).days

        ultimo = factura.recordatorios.first()  # ordering = ['-enviado_en']

        if cve not in clientes:
            from clientes.models import Cliente
            clientes[cve] = {
                'cve_cliente': cve,
                'nombre': ref.nombre_cliente,
                'cliente': Cliente.objects.filter(cve_cliente=cve).first(),
                'facturas': [],
                'saldo_total': Decimal('0'),
                'dias_mayor': 0,
                'ultimo_recordatorio': None,
            }

        clientes[cve]['facturas'].append({
            'factura': factura,
            'saldo': saldo,
            'dias': dias,
            'fecha_base': fecha_base,
        })
        clientes[cve]['saldo_total'] += saldo
        clientes[cve]['dias_mayor'] = max(clientes[cve]['dias_mayor'], dias)
        if ultimo and (
            clientes[cve]['ultimo_recordatorio'] is None
            or ultimo.enviado_en > clientes[cve]['ultimo_recordatorio']
        ):
            clientes[cve]['ultimo_recordatorio'] = ultimo.enviado_en

    return sorted(clientes.values(), key=lambda x: x['dias_mayor'], reverse=True)


def _enviar_email(cliente, facturas_info, tipo, usuario=None):
    """
    Envía el email de cobranza y crea RecordatorioCobranza para cada factura incluida.
    facturas_info: list[dict] con keys: factura, saldo, dias, fecha_base
    Retorna True si el envío fue exitoso.
    """
    from .models import RecordatorioCobranza

    html = render_to_string('finanzas/email_recordatorio_cobranza.html', {
        'cliente': cliente,
        'facturas_info': facturas_info,
        'tipo': tipo,
        'saldo_total': sum(fi['saldo'] for fi in facturas_info),
    })

    to = [cliente.email_cobranza]
    cc = [cliente.email_cobranza_cc] if cliente.email_cobranza_cc else []

    exitoso = True
    error_msg = ''
    try:
        msg = EmailMultiAlternatives(
            subject=ASUNTOS.get(tipo, ASUNTOS['manual']),
            body='Este correo requiere un cliente con soporte HTML.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=to,
            cc=cc,
        )
        msg.attach_alternative(html, 'text/html')
        msg.send()
    except Exception as e:
        exitoso = False
        error_msg = str(e)
        logger.error('[Cobranza] Error enviando email a %s: %s', cliente.email_cobranza, e)

    for fi in facturas_info:
        RecordatorioCobranza.objects.create(
            factura=fi['factura'],
            tipo=tipo,
            enviado_por=usuario,
            exitoso=exitoso,
            error_msg=error_msg,
        )

    return exitoso


def enviar_recordatorio_factura(factura, cliente, tipo, usuario=None):
    """
    Envía recordatorio para una sola factura (usado por el scheduler).
    Retorna True si exitoso.
    """
    saldo = calcular_saldo_factura(factura)
    fecha_base = (
        factura.fecha_emision.date()
        if factura.fecha_emision
        else factura.created_at.date()
    )
    dias = (date.today() - fecha_base).days
    facturas_info = [{'factura': factura, 'saldo': saldo, 'dias': dias, 'fecha_base': fecha_base}]
    return _enviar_email(cliente, facturas_info, tipo, usuario)


def enviar_estado_cuenta_cliente(cliente, usuario=None):
    """
    Envía estado de cuenta consolidado con TODAS las facturas pendientes del cliente.
    Usado para envíos manuales desde el panel.
    Retorna True si exitoso (y hay facturas pendientes), False si no hay facturas o falla.
    """
    from .models import Factura
    hoy = date.today()

    facturas = (
        Factura.objects
        .filter(estado='TIMBRADA', referencias__cve_cliente=cliente.cve_cliente)
        .distinct()
        .prefetch_related('documentos_pago')
    )

    facturas_info = []
    for factura in facturas:
        saldo = calcular_saldo_factura(factura)
        if saldo <= Decimal('0'):
            continue
        fecha_base = (
            factura.fecha_emision.date()
            if factura.fecha_emision
            else factura.created_at.date()
        )
        facturas_info.append({
            'factura': factura,
            'saldo': saldo,
            'dias': (hoy - fecha_base).days,
            'fecha_base': fecha_base,
        })

    if not facturas_info:
        return False

    return _enviar_email(cliente, facturas_info, 'manual', usuario)
```

- [ ] **Step 4: Ejecutar tests de saldo**

```bash
python manage.py test finanzas.tests.CalcSaldoFacturaTest --verbosity=2
```

Esperado: `OK` — 3 tests pasando.

- [ ] **Step 5: Commit**

```bash
git add finanzas/cobranza.py finanzas/tests.py
git commit -m "feat(finanzas): módulo de lógica de cobranza (saldos + envío email)"
```

---

## Task 4: Template HTML del email

**Files:**
- Create: `templates/finanzas/email_recordatorio_cobranza.html`

**Interfaces:**
- Consumes: contexto `{cliente, facturas_info, tipo, saldo_total}` desde `finanzas/cobranza.py`

- [ ] **Step 1: Crear `templates/finanzas/email_recordatorio_cobranza.html`**

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Estado de Cuenta — Reiki Logística</title>
  <style>
    body { font-family: Arial, sans-serif; background: #f8fafc; margin: 0; padding: 0; color: #1e293b; }
    .wrapper { max-width: 600px; margin: 32px auto; background: #ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e2e8f0; }
    .header { background: #0ea5e9; padding: 28px 32px; }
    .header h1 { margin: 0; color: #ffffff; font-size: 20px; font-weight: 700; }
    .header p { margin: 4px 0 0; color: #bae6fd; font-size: 13px; }
    .body { padding: 32px; }
    .intro { font-size: 14px; color: #475569; margin-bottom: 24px; line-height: 1.6; }
    .intro.urgente { color: #b91c1c; }
    table { width: 100%; border-collapse: collapse; margin-bottom: 24px; font-size: 13px; }
    th { background: #f1f5f9; text-align: left; padding: 10px 12px; color: #64748b; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
    td { padding: 10px 12px; border-bottom: 1px solid #f1f5f9; color: #334155; }
    .mono { font-family: monospace; }
    .text-right { text-align: right; }
    .saldo-total { background: #f0f9ff; border-radius: 8px; padding: 16px 20px; margin-bottom: 24px; display: flex; justify-content: space-between; align-items: center; }
    .saldo-total .label { font-size: 13px; color: #475569; }
    .saldo-total .amount { font-size: 20px; font-weight: 700; color: #0369a1; font-family: monospace; }
    .footer-note { font-size: 12px; color: #94a3b8; line-height: 1.6; }
    .footer { background: #f8fafc; padding: 20px 32px; border-top: 1px solid #e2e8f0; text-align: center; font-size: 11px; color: #94a3b8; }
  </style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>Reiki Logística</h1>
    <p>Estado de Cuenta · Agencia Aduanal · Patente 3772</p>
  </div>

  <div class="body">

    <p class="intro{% if tipo == '60d' %} urgente{% endif %}">
      Estimado(a) <strong>{{ cliente.nombre_cliente }}</strong>,<br><br>
      {% if tipo == '15d' %}
        Le informamos que tiene facturas con saldo pendiente de pago. Le enviamos este recordatorio para facilitar la gestión de su cuenta.
      {% elif tipo == '30d' %}
        Le solicitamos atender el pago de las siguientes facturas, las cuales llevan más de 30 días con saldo pendiente.
      {% elif tipo == '60d' %}
        Le comunicamos de manera urgente que las siguientes facturas tienen más de 60 días con saldo vencido. Le pedimos realizar el pago a la brevedad posible.
      {% else %}
        Le enviamos el estado de cuenta actualizado con los saldos pendientes a la fecha.
      {% endif %}
    </p>

    <table>
      <thead>
        <tr>
          <th>Folio</th>
          <th>Fecha</th>
          <th class="text-right">Total</th>
          <th class="text-right">Saldo pendiente</th>
          <th class="text-right">Días</th>
        </tr>
      </thead>
      <tbody>
        {% for fi in facturas_info %}
        <tr>
          <td class="mono">{{ fi.factura.serie }}{{ fi.factura.folio }}</td>
          <td>{{ fi.fecha_base }}</td>
          <td class="text-right mono">${{ fi.factura.total|floatformat:2 }}</td>
          <td class="text-right mono"><strong>${{ fi.saldo|floatformat:2 }}</strong></td>
          <td class="text-right">{{ fi.dias }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>

    <div class="saldo-total">
      <span class="label">Total adeudado</span>
      <span class="amount">${{ saldo_total|floatformat:2 }} MXN</span>
    </div>

    <p class="footer-note">
      Para realizar su pago o aclarar cualquier duda, comuníquese con nosotros directamente.<br>
      Este es un mensaje generado automáticamente por el sistema de Reiki Logística.
    </p>
  </div>

  <div class="footer">
    Reiki Logística — Agencia Aduanal — Patente 3772<br>
    Este correo fue enviado a {{ cliente.email_cobranza }}
  </div>
</div>
</body>
</html>
```

- [ ] **Step 2: Verificar que el template carga sin errores**

```bash
python manage.py shell -c "
from django.template.loader import render_to_string
from clientes.models import Cliente
c = Cliente(nombre_cliente='Test', email_cobranza='test@test.com')
html = render_to_string('finanzas/email_recordatorio_cobranza.html', {
    'cliente': c,
    'facturas_info': [],
    'tipo': '15d',
    'saldo_total': 0,
})
print('OK — longitud:', len(html))
"
```

Esperado: `OK — longitud: <número>`

- [ ] **Step 3: Commit**

```bash
git add templates/finanzas/email_recordatorio_cobranza.html
git commit -m "feat(finanzas): template HTML de email de cobranza"
```

---

## Task 5: Job del scheduler (`finanzas/jobs.py`) + registro

**Files:**
- Create: `finanzas/jobs.py`
- Modify: `reportes/scheduler.py`

**Interfaces:**
- Consumes: `enviar_recordatorio_factura`, `calcular_saldo_factura`, `buscar_cliente_de_factura` (Task 3), `RecordatorioCobranza` (Task 2)
- Produces: `ejecutar_cobranza_automatica()` — registrado en el scheduler como job diario

- [ ] **Step 1: Crear `finanzas/jobs.py`**

```python
import logging
from datetime import date
from decimal import Decimal

logger = logging.getLogger(__name__)

HITOS = [(15, '15d'), (30, '30d'), (60, '60d')]


def ejecutar_cobranza_automatica():
    """
    Job diario: revisa todas las Facturas TIMBRADAS con saldo > 0 y envía
    recordatorios automáticos a 15, 30 y 60 días desde la fecha de emisión.
    Nunca reenvía un tipo de recordatorio ya enviado para la misma factura.
    """
    from .models import Factura
    from .cobranza import (
        calcular_saldo_factura,
        buscar_cliente_de_factura,
        enviar_recordatorio_factura,
    )

    hoy = date.today()
    enviados = 0
    omitidos_saldo = 0
    omitidos_email = 0

    facturas = (
        Factura.objects
        .filter(estado='TIMBRADA')
        .prefetch_related('referencias', 'documentos_pago', 'recordatorios')
    )

    for factura in facturas:
        saldo = calcular_saldo_factura(factura)
        if saldo <= Decimal('0'):
            omitidos_saldo += 1
            continue

        fecha_base = (
            factura.fecha_emision.date()
            if factura.fecha_emision
            else factura.created_at.date()
        )
        dias = (hoy - fecha_base).days

        cliente = buscar_cliente_de_factura(factura)
        if not cliente or not cliente.email_cobranza:
            omitidos_email += 1
            continue

        tipos_enviados = set(
            factura.recordatorios
            .exclude(tipo='manual')
            .values_list('tipo', flat=True)
        )

        for hito, tipo in reversed(HITOS):  # 60d → 30d → 15d: enviar el más urgente primero
            if dias >= hito and tipo not in tipos_enviados:
                exitoso = enviar_recordatorio_factura(factura, cliente, tipo)
                if exitoso:
                    enviados += 1
                    logger.info(
                        '[Cobranza] Recordatorio %s enviado — Factura %s → %s',
                        tipo, factura.pk, cliente.email_cobranza,
                    )
                else:
                    logger.warning('[Cobranza] Fallo al enviar %s — Factura %s', tipo, factura.pk)
                break  # un recordatorio por factura por ejecución del job

    logger.info(
        '[Cobranza] Job completado: %d enviados, %d sin saldo, %d sin email.',
        enviados, omitidos_saldo, omitidos_email,
    )
```

- [ ] **Step 2: Registrar el job en `reportes/scheduler.py`**

Agregar el import y el job dentro de la función `start()`. Modificar el bloque de imports locales y agregar el nuevo `add_job`:

```python
# Al inicio de la función start(), junto a los otros imports locales:
from finanzas.jobs import ejecutar_cobranza_automatica

# Agregar después del job de calcular_comisiones:
scheduler.add_job(
    ejecutar_cobranza_automatica,
    trigger='cron',
    hour=8,
    minute=0,
    id='cobranza_automatica',
    replace_existing=True,
    jobstore='default',
)
```

El bloque completo de la función `start()` queda así (solo el cuerpo de imports + jobs, sin cambiar el resto):

```python
from .jobs import enviar_reporte_semanal, enviar_reporte_mensual
from finanzas.comisiones import calcular_comisiones_mes_actual
from finanzas.jobs import ejecutar_cobranza_automatica
```

Y agregar el job:

```python
scheduler.add_job(
    ejecutar_cobranza_automatica,
    trigger='cron',
    hour=8,
    minute=0,
    id='cobranza_automatica',
    replace_existing=True,
    jobstore='default',
)
```

Y actualizar el log final:

```python
logger.info('APScheduler iniciado: semanal (lun 7am) + mensual (día 1, 7am) + comisiones (día 1, 6am) + cobranza (diario 8am).')
```

- [ ] **Step 3: Verificar que el scheduler arranca sin errores**

```bash
python manage.py check
```

Esperado: `System check identified no issues (0 silenced).`

- [ ] **Step 4: Commit**

```bash
git add finanzas/jobs.py reportes/scheduler.py
git commit -m "feat(finanzas): job diario de cobranza automática en APScheduler"
```

---

## Task 6: Vistas de cobranza + URLs

**Files:**
- Modify: `finanzas/views.py` (reemplazar `cobranza_list`, agregar `cobranza_enviar`)
- Modify: `finanzas/urls.py` (agregar URL de envío manual)

**Interfaces:**
- Consumes: `calcular_saldos_por_cliente`, `enviar_estado_cuenta_cliente` (Task 3), `Cliente` (Task 1)
- Produces:
  - `cobranza_list(request)` — GET, renderiza `finanzas/cobranza_list.html`
  - `cobranza_enviar(request, cve_cliente)` — POST, envía estado de cuenta y redirige

- [ ] **Step 1: Reemplazar `cobranza_list` en `finanzas/views.py`**

Buscar la función `cobranza_list` (líneas ~242-280) y reemplazarla completa:

```python
@login_required
def cobranza_list(request):
    from .cobranza import calcular_saldos_por_cliente
    items = calcular_saldos_por_cliente()
    saldo_total = sum(i['saldo_total'] for i in items)
    return render(request, 'finanzas/cobranza_list.html', {
        'items': items,
        'saldo_total': saldo_total,
        'num_clientes': len(items),
    })
```

- [ ] **Step 2: Agregar `cobranza_enviar` en `finanzas/views.py`**

Agregar inmediatamente después de `cobranza_list`:

```python
@login_required
def cobranza_enviar(request, cve_cliente):
    if request.method != 'POST':
        return redirect('finanzas:cobranza_list')

    from clientes.models import Cliente
    from .cobranza import enviar_estado_cuenta_cliente

    try:
        cliente = Cliente.objects.get(cve_cliente=cve_cliente)
    except Cliente.DoesNotExist:
        messages.error(request, 'Cliente no encontrado.')
        return redirect('finanzas:cobranza_list')

    if not cliente.email_cobranza:
        messages.error(
            request,
            f'{cliente.nombre_cliente} no tiene email de cobranza configurado. '
            f'Agrégalo en el panel de administración.'
        )
        return redirect('finanzas:cobranza_list')

    exitoso = enviar_estado_cuenta_cliente(cliente, usuario=request.user)

    if exitoso:
        messages.success(
            request,
            f'Estado de cuenta enviado a {cliente.email_cobranza}.'
        )
    else:
        messages.error(
            request,
            'Error al enviar el correo. Verifica la configuración de SendGrid.'
        )

    return redirect('finanzas:cobranza_list')
```

- [ ] **Step 3: Agregar URL en `finanzas/urls.py`**

La URL `cobranza/` ya existe. Solo agregar la nueva línea de envío, inmediatamente después de ella:

```python
path('cobranza/<str:cve_cliente>/enviar/', views.cobranza_enviar, name='cobranza_enviar'),
```

- [ ] **Step 4: Verificar que las URLs resuelven correctamente**

```bash
python manage.py shell -c "
from django.urls import reverse
print(reverse('finanzas:cobranza_list'))
print(reverse('finanzas:cobranza_enviar', args=['ACME01']))
"
```

Esperado:
```
/finanzas/cobranza/
/finanzas/cobranza/ACME01/enviar/
```

- [ ] **Step 5: Commit**

```bash
git add finanzas/views.py finanzas/urls.py
git commit -m "feat(finanzas): vistas cobranza_list rediseñada y cobranza_enviar"
```

---

## Task 7: Template del panel de cobranza

**Files:**
- Modify (reemplazar): `templates/finanzas/cobranza_list.html`

**Interfaces:**
- Consumes: contexto `{items, saldo_total, num_clientes}` de `cobranza_list`
- Cada `item` en `items` tiene: `cve_cliente`, `nombre`, `cliente`, `facturas`, `saldo_total`, `dias_mayor`, `ultimo_recordatorio`

- [ ] **Step 1: Reemplazar `templates/finanzas/cobranza_list.html`**

```html
{% extends 'base.html' %}
{% block title %}Cobranza — Finanzas{% endblock %}
{% block content %}
<div class="p-6 space-y-6">

  <!-- Header -->
  <div class="flex items-start justify-between">
    <div>
      <p class="text-xs text-slate-500 uppercase tracking-wider">Finanzas</p>
      <h1 class="text-2xl font-bold text-slate-800">Cobranza de Honorarios</h1>
      <p class="text-slate-500 text-sm mt-0.5">Facturas timbradas con saldo pendiente</p>
    </div>
    <a href="{% url 'finanzas:facturas_list' %}"
       class="bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium px-4 py-2 rounded-lg transition-colors">
      Ver todas las facturas
    </a>
  </div>

  <!-- KPIs -->
  {% if items %}
  <div class="grid grid-cols-2 gap-4">
    <div class="bg-white rounded-xl border border-slate-200 p-5">
      <p class="text-xs text-slate-500 uppercase tracking-wider">Clientes con saldo</p>
      <p class="text-3xl font-bold text-slate-800 mt-1">{{ num_clientes }}</p>
    </div>
    <div class="bg-white rounded-xl border border-slate-200 p-5">
      <p class="text-xs text-slate-500 uppercase tracking-wider">Total cartera vencida</p>
      <p class="text-3xl font-bold text-red-600 mt-1 font-mono">${{ saldo_total|floatformat:2 }}</p>
    </div>
  </div>

  <!-- Tabla por cliente -->
  <div class="bg-white rounded-xl border border-slate-200 overflow-hidden">
    <table class="w-full text-sm">
      <thead class="bg-slate-50 text-xs text-slate-500 uppercase">
        <tr>
          <th class="px-5 py-3 text-left">Cliente</th>
          <th class="px-5 py-3 text-center">Facturas</th>
          <th class="px-5 py-3 text-right">Saldo total</th>
          <th class="px-5 py-3 text-center">Antigüedad</th>
          <th class="px-5 py-3 text-left">Último recordatorio</th>
          <th class="px-5 py-3 text-center">Acciones</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-100">
        {% for item in items %}
        <tr class="hover:bg-slate-50">
          <td class="px-5 py-3">
            <p class="font-medium text-slate-800">{{ item.nombre }}</p>
            <p class="text-xs text-slate-400 font-mono">{{ item.cve_cliente }}</p>
          </td>
          <td class="px-5 py-3 text-center text-slate-600">{{ item.facturas|length }}</td>
          <td class="px-5 py-3 text-right font-mono font-semibold text-red-600">
            ${{ item.saldo_total|floatformat:2 }}
          </td>
          <td class="px-5 py-3 text-center">
            {% if item.dias_mayor >= 60 %}
              <span class="bg-red-100 text-red-700 text-xs font-medium px-2.5 py-1 rounded-full">{{ item.dias_mayor }}d — Urgente</span>
            {% elif item.dias_mayor >= 30 %}
              <span class="bg-orange-100 text-orange-700 text-xs font-medium px-2.5 py-1 rounded-full">{{ item.dias_mayor }}d — Vencida</span>
            {% elif item.dias_mayor >= 15 %}
              <span class="bg-yellow-100 text-yellow-700 text-xs font-medium px-2.5 py-1 rounded-full">{{ item.dias_mayor }}d — Por vencer</span>
            {% else %}
              <span class="bg-slate-100 text-slate-600 text-xs font-medium px-2.5 py-1 rounded-full">{{ item.dias_mayor }}d — Reciente</span>
            {% endif %}
          </td>
          <td class="px-5 py-3 text-slate-500 text-xs">
            {% if item.ultimo_recordatorio %}
              {{ item.ultimo_recordatorio|date:"d/m/Y H:i" }}
            {% else %}
              <span class="text-slate-300">Nunca enviado</span>
            {% endif %}
          </td>
          <td class="px-5 py-3 text-center">
            <div class="flex items-center justify-center gap-2">
              <button onclick="toggleDetalle('detalle-{{ forloop.counter }}')"
                      class="text-sky-600 hover:text-sky-800 text-xs font-medium underline">
                Ver detalle
              </button>
              {% if item.cliente and item.cliente.email_cobranza %}
              <form method="post" action="{% url 'finanzas:cobranza_enviar' cve_cliente=item.cve_cliente %}"
                    onsubmit="return confirm('¿Enviar estado de cuenta a {{ item.cliente.email_cobranza }}?')">
                {% csrf_token %}
                <button type="submit"
                        class="bg-sky-600 hover:bg-sky-700 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors">
                  Enviar estado de cuenta
                </button>
              </form>
              {% else %}
              <span class="text-xs text-slate-300 italic">Sin email configurado</span>
              {% endif %}
            </div>
          </td>
        </tr>
        <!-- Detalle expandible -->
        <tr id="detalle-{{ forloop.counter }}" class="hidden bg-slate-50">
          <td colspan="6" class="px-8 py-4">
            <table class="w-full text-xs text-slate-600">
              <thead>
                <tr class="text-slate-400 uppercase text-xs">
                  <th class="text-left pb-2">Folio</th>
                  <th class="text-left pb-2">Fecha emisión</th>
                  <th class="text-right pb-2">Total factura</th>
                  <th class="text-right pb-2">Saldo pendiente</th>
                  <th class="text-right pb-2">Días</th>
                  <th class="text-left pb-2">Recordatorios enviados</th>
                </tr>
              </thead>
              <tbody>
                {% for fi in item.facturas %}
                <tr class="border-t border-slate-100">
                  <td class="py-2 font-mono">
                    <a href="{% url 'finanzas:factura_detalle' fi.factura.pk %}"
                       class="text-sky-600 hover:underline">
                      {{ fi.factura.serie }}{{ fi.factura.folio }}
                    </a>
                  </td>
                  <td class="py-2">{{ fi.fecha_base }}</td>
                  <td class="py-2 text-right font-mono">${{ fi.factura.total|floatformat:2 }}</td>
                  <td class="py-2 text-right font-mono font-semibold text-red-600">${{ fi.saldo|floatformat:2 }}</td>
                  <td class="py-2 text-right">{{ fi.dias }}d</td>
                  <td class="py-2">
                    {% for r in fi.factura.recordatorios.all %}
                      <span class="inline-block bg-white border border-slate-200 text-slate-500 px-1.5 py-0.5 rounded text-xs mr-1">
                        {{ r.get_tipo_display }} · {{ r.enviado_en|date:"d/m" }}
                        {% if not r.exitoso %}<span class="text-red-500">✗</span>{% endif %}
                      </span>
                    {% empty %}
                      <span class="text-slate-300 italic">Ninguno</span>
                    {% endfor %}
                  </td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  {% else %}
  <div class="bg-white rounded-xl border border-slate-200 p-16 text-center">
    <svg class="w-12 h-12 text-slate-200 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
            d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
    </svg>
    <p class="text-slate-500 text-sm font-medium">Sin saldos pendientes</p>
    <p class="text-slate-300 text-xs mt-1">Todas las facturas están liquidadas.</p>
  </div>
  {% endif %}

</div>

<script>
function toggleDetalle(id) {
  const row = document.getElementById(id);
  row.classList.toggle('hidden');
}
</script>

{% endblock %}
```

- [ ] **Step 2: Verificar que la vista carga sin errores**

Asegúrate de que el servidor esté corriendo (`python manage.py runserver 8001`) y visita:

```
http://127.0.0.1:8001/finanzas/cobranza/
```

Esperado: la página carga con el nuevo diseño. Si no hay facturas timbradas con saldo, se muestra el estado vacío con el ícono de check.

- [ ] **Step 3: Verificar mensajes de Django en el template base**

Confirmar que `templates/base.html` tiene el bloque de mensajes. Buscar en el archivo:

```bash
grep -n "messages\|django.contrib.messages" /home/tony/Developer/proyecto_hal9mil_reiki/templates/base.html | head -5
```

Si no existe el bloque de mensajes en `base.html`, agregar antes del `{% block content %}`:

```html
{% if messages %}
<div class="fixed top-4 right-4 z-50 space-y-2">
  {% for message in messages %}
  <div class="bg-white rounded-xl shadow-lg border px-4 py-3 text-sm flex items-center gap-3
    {% if message.tags == 'error' %}border-red-200 text-red-700{% elif message.tags == 'success' %}border-green-200 text-green-700{% else %}border-slate-200 text-slate-700{% endif %}">
    {{ message }}
  </div>
  {% endfor %}
</div>
{% endif %}
```

- [ ] **Step 4: Commit final**

```bash
git add templates/finanzas/cobranza_list.html templates/base.html
git commit -m "feat(finanzas): panel de cobranza por cliente con detalle expandible y envío manual"
```

---

## Verificación End-to-End

Después de completar todos los tasks:

- [ ] **Configurar email en un cliente de prueba** via admin (`/admin/clientes/cliente/`): poner un email real en `email_cobranza`

- [ ] **Disparar el job manualmente** para verificar el envío:

```bash
python manage.py shell -c "
from finanzas.jobs import ejecutar_cobranza_automatica
ejecutar_cobranza_automatica()
"
```

- [ ] **Verificar que el email llegó** al buzón configurado

- [ ] **Probar el botón "Enviar estado de cuenta"** desde la UI (`/finanzas/cobranza/`) y confirmar que llega el correo consolidado

- [ ] **Verificar la tabla de recordatorios** en `/admin/finanzas/recordatoriocobranza/`
