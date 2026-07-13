# Envío de Cuenta de Gastos al Cliente — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cerrar financieramente la cuenta de gastos de una referencia (bloqueando anticipos/gastos/XML) y enviarla al cliente por correo con balanza anticipos vs. gastos y ZIP de CFDIs, con tracking entregado/leído vía SendGrid Event Webhook, listado de notificaciones y reenvío.

**Architecture:** Dos modelos nuevos en `finanzas` (`CierreCuentaGastos`, `NotificacionCuentaGastos`), servicio de envío en `finanzas/cuenta_gastos_envio.py` usando la Web API de SendGrid con `custom_args` para correlación, vistas nuevas en `finanzas/views_cuenta_gastos.py` (cierre, envío, webhook, listado), y modificaciones al template `referencia_estado.html`.

**Tech Stack:** Django 5.2, librería `sendgrid` (ya en requirements: `sendgrid>=6.11`), DO Spaces vía `hal9mil.storage_backends.media_storage`, tests con `django.test.TestCase`.

**Spec:** `docs/superpowers/specs/2026-07-13-envio-cuenta-gastos-design.md`

## Global Constraints

- Todo el copy de UI y mensajes en **español** (es-mx), siguiendo el tono existente.
- FileFields de media usan `storage=media_storage` (import: `from hal9mil.storage_backends import media_storage`) — NUNCA storage hardcodeado.
- Control de acceso: `@modulo_required('Finanzas')` de `core.permisos` para vistas de Finanzas; reapertura solo `request.user.is_superuser`.
- Tests: `python manage.py test finanzas.<modulo> -v 1`, con `@override_settings(MEDIA_ROOT=tempfile.mkdtemp())` cuando hay archivos.
- Login de prueba: patrón `_login_finanzas` (Group `Finanzas`), ver `finanzas/test_carga_cliente.py`.
- Estados de notificación solo avanzan: `ENVIADO(1) → ENTREGADO(2) → LEIDO(3)`; `REBOTADO`/`ERROR` terminales.
- Límite de ZIP: **20 MB** (20 * 1024 * 1024 bytes).
- Commits en español con prefijo convencional (`feat(finanzas):`, `test(finanzas):`...) y trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- El botón/flujo "Emitir factura" NO se bloquea con el cierre.

---

### Task 1: Modelos `CierreCuentaGastos` y `NotificacionCuentaGastos`

**Files:**
- Modify: `finanzas/models.py` (agregar al final)
- Create: `finanzas/test_cuenta_gastos_cierre.py`
- Create: migración `finanzas` (autogenerada)

**Interfaces:**
- Consumes: `referencias.Referencia`, `media_storage` (ya importado en `finanzas/models.py` línea 7).
- Produces:
  - `CierreCuentaGastos` con campos `referencia` (OneToOne, related_name `cierre_cg`), `cerrada_por`, `cerrada_en`, `nota`, `reabierta_por`, `reabierta_en`; propiedad `activa` (bool); classmethod `activo_para(referencia) -> CierreCuentaGastos | None`.
  - `NotificacionCuentaGastos` con campos `referencia` (FK, related_name `notificaciones_cg`), `destinatario`, `cc`, `enviado_por`, `enviado_en`, `estado` (choices `ESTADOS`), `entregado_en`, `leido_en`, `sg_message_id`, `error_msg`, `es_reenvio`, `zip_file`.

- [ ] **Step 1: Escribir tests que fallan**

Crear `finanzas/test_cuenta_gastos_cierre.py`:

```python
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from referencias.models import Referencia


def _referencia(num='LCRR0001/26'):
    return Referencia.objects.create(num_refe=num, patente='1656', prefijo='LCRR')


def _login_finanzas(test, username='cg_user'):
    grupo, _ = Group.objects.get_or_create(name='Finanzas')
    test.user = User.objects.create_user(username, password='x')
    test.user.groups.add(grupo)
    test.client.login(username=username, password='x')


class CierreCuentaGastosModelTests(TestCase):
    def setUp(self):
        self.referencia = _referencia()
        self.user = User.objects.create_user('cerrador', password='x')

    def test_cierre_nuevo_esta_activo(self):
        from finanzas.models import CierreCuentaGastos
        cierre = CierreCuentaGastos.objects.create(
            referencia=self.referencia, cerrada_por=self.user
        )
        self.assertTrue(cierre.activa)
        self.assertEqual(CierreCuentaGastos.activo_para(self.referencia), cierre)

    def test_cierre_reabierto_no_esta_activo(self):
        from finanzas.models import CierreCuentaGastos
        cierre = CierreCuentaGastos.objects.create(
            referencia=self.referencia, cerrada_por=self.user,
            reabierta_por=self.user, reabierta_en=timezone.now(),
        )
        self.assertFalse(cierre.activa)
        self.assertIsNone(CierreCuentaGastos.activo_para(self.referencia))

    def test_referencia_sin_cierre(self):
        from finanzas.models import CierreCuentaGastos
        self.assertIsNone(CierreCuentaGastos.activo_para(self.referencia))


class NotificacionCuentaGastosModelTests(TestCase):
    def test_notificacion_default_enviado(self):
        from finanzas.models import NotificacionCuentaGastos
        notif = NotificacionCuentaGastos.objects.create(
            referencia=_referencia(), destinatario='cliente@example.com'
        )
        self.assertEqual(notif.estado, 'ENVIADO')
        self.assertFalse(notif.es_reenvio)
        self.assertIsNone(notif.entregado_en)
        self.assertIsNone(notif.leido_en)
```

- [ ] **Step 2: Correr tests y verificar que fallan**

Run: `python manage.py test finanzas.test_cuenta_gastos_cierre -v 1`
Expected: FAIL/ERROR con `ImportError: cannot import name 'CierreCuentaGastos'`

- [ ] **Step 3: Implementar modelos**

Agregar al final de `finanzas/models.py` (después de `RecordatorioCobranza`; `django.utils.timezone` — verificar si ya está importado arriba; si no, agregar `from django.utils import timezone` a los imports):

```python
# ── Envío de cuenta de gastos al cliente ─────────────────────────────────────

class CierreCuentaGastos(models.Model):
    """Cierre financiero de la cuenta de gastos de una referencia.

    Cerrada = existe el registro y reabierta_en IS NULL. La reapertura (solo
    superusuario) llena reabierta_por/reabierta_en; un re-cierre posterior
    actualiza cerrada_por/cerrada_en y limpia la reapertura (se audita solo
    el último ciclo).
    """
    referencia = models.OneToOneField(
        'referencias.Referencia',
        on_delete=models.PROTECT, related_name='cierre_cg'
    )
    cerrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True,
        on_delete=models.SET_NULL, related_name='cierres_cg'
    )
    cerrada_en = models.DateTimeField(default=timezone.now)
    nota = models.CharField(max_length=300, blank=True)
    reabierta_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='reaperturas_cg'
    )
    reabierta_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Cierre de cuenta de gastos'
        verbose_name_plural = 'Cierres de cuenta de gastos'

    @property
    def activa(self):
        return self.reabierta_en is None

    @classmethod
    def activo_para(cls, referencia):
        return cls.objects.filter(
            referencia=referencia, reabierta_en__isnull=True
        ).first()

    def __str__(self):
        estado = 'cerrada' if self.activa else 'reabierta'
        return f'{self.referencia} | {estado} ({self.cerrada_en:%Y-%m-%d})'


class NotificacionCuentaGastos(models.Model):
    ESTADOS = [
        ('ENVIADO', 'Enviado'),
        ('ENTREGADO', 'Entregado'),
        ('LEIDO', 'Leído'),
        ('REBOTADO', 'Rebotado'),
        ('ERROR', 'Error'),
    ]
    referencia = models.ForeignKey(
        'referencias.Referencia',
        on_delete=models.PROTECT, related_name='notificaciones_cg'
    )
    destinatario = models.EmailField()
    cc = models.EmailField(blank=True)
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True,
        on_delete=models.SET_NULL, related_name='notificaciones_cg_enviadas'
    )
    enviado_en = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='ENVIADO')
    entregado_en = models.DateTimeField(null=True, blank=True)
    leido_en = models.DateTimeField(null=True, blank=True)
    sg_message_id = models.CharField(max_length=100, blank=True, db_index=True)
    error_msg = models.TextField(blank=True)
    es_reenvio = models.BooleanField(default=False)
    zip_file = models.FileField(
        storage=media_storage, upload_to='cuentas_gastos/%Y/%m/',
        null=True, blank=True
    )

    class Meta:
        ordering = ['-enviado_en']
        verbose_name = 'Notificación de cuenta de gastos'
        verbose_name_plural = 'Notificaciones de cuenta de gastos'

    def __str__(self):
        return f'{self.referencia} → {self.destinatario} [{self.estado}]'
```

- [ ] **Step 4: Generar y aplicar migración**

Run: `python manage.py makemigrations finanzas && python manage.py migrate`
Expected: migración creada con los 2 modelos, aplica sin error.

- [ ] **Step 5: Correr tests y verificar que pasan**

Run: `python manage.py test finanzas.test_cuenta_gastos_cierre -v 1`
Expected: OK (4 tests)

- [ ] **Step 6: Commit**

```bash
git add finanzas/models.py finanzas/migrations/ finanzas/test_cuenta_gastos_cierre.py
git commit -m "feat(finanzas): modelos CierreCuentaGastos y NotificacionCuentaGastos"
```

---

### Task 2: Campos de correo de cuenta de gastos en `Cliente`

**Files:**
- Modify: `clientes/models.py`
- Modify: `clientes/admin.py`
- Create: migración `clientes` (autogenerada)
- Test: `clientes/tests.py` (agregar clase; si el archivo tiene solo el stub `from django.test import TestCase`, reemplazarlo)

**Interfaces:**
- Produces: `Cliente.email_cuenta_gastos`, `Cliente.email_cuenta_gastos_cc` (EmailField, blank). El fallback a `email_cobranza` NO vive en el modelo — lo implementa `destinatarios_cliente()` en Task 4.

- [ ] **Step 1: Escribir test que falla**

En `clientes/tests.py`:

```python
from django.test import TestCase

from .models import Cliente


class ClienteEmailCuentaGastosTests(TestCase):
    def test_campos_nuevos_aceptan_blank(self):
        cliente = Cliente.objects.create(nombre_cliente='ACME SA')
        self.assertEqual(cliente.email_cuenta_gastos, '')
        self.assertEqual(cliente.email_cuenta_gastos_cc, '')

    def test_campos_nuevos_guardan_valor(self):
        cliente = Cliente.objects.create(
            nombre_cliente='CACIPA',
            email_cuenta_gastos='cg@cacipa.com',
            email_cuenta_gastos_cc='cc@cacipa.com',
        )
        cliente.refresh_from_db()
        self.assertEqual(cliente.email_cuenta_gastos, 'cg@cacipa.com')
        self.assertEqual(cliente.email_cuenta_gastos_cc, 'cc@cacipa.com')
```

- [ ] **Step 2: Correr y verificar fallo**

Run: `python manage.py test clientes -v 1`
Expected: ERROR `TypeError: Cliente() got unexpected keyword arguments` (o `AttributeError`)

- [ ] **Step 3: Implementar campos y admin**

`clientes/models.py` — agregar tras `email_cobranza_cc`:

```python
    email_cuenta_gastos    = models.EmailField(blank=True)
    email_cuenta_gastos_cc = models.EmailField(blank=True)
```

`clientes/admin.py` — actualizar fieldsets:

```python
    fieldsets = (
        (None, {'fields': ('nombre_cliente', 'cve_cliente', 'rfc')}),
        ('Cobranza', {'fields': ('email_cobranza', 'email_cobranza_cc')}),
        ('Cuenta de gastos', {'fields': ('email_cuenta_gastos', 'email_cuenta_gastos_cc')}),
    )
```

- [ ] **Step 4: Migración + tests**

Run: `python manage.py makemigrations clientes && python manage.py migrate && python manage.py test clientes -v 1`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add clientes/
git commit -m "feat(clientes): correos de cuenta de gastos con sección en admin"
```

---

### Task 3: Guard de cierre en `anticipo_crear`, `gasto_crear` y `subir_xml_proveedor`

**Files:**
- Modify: `finanzas/views.py` (vistas en líneas ~85-105, ~108-128, ~174-240)
- Test: `finanzas/test_cuenta_gastos_cierre.py` (agregar clase)

**Interfaces:**
- Consumes: `CierreCuentaGastos.activo_para(referencia)` (Task 1).
- Produces: las 3 vistas rechazan cualquier request (GET o POST) cuando hay cierre activo, con mensaje y redirect a `finanzas:referencia_estado`.

- [ ] **Step 1: Escribir tests que fallan**

Agregar a `finanzas/test_cuenta_gastos_cierre.py`:

```python
from django.urls import reverse


class GuardCierreViewsTests(TestCase):
    def setUp(self):
        _login_finanzas(self)
        self.referencia = _referencia('LCRR0002/26')
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(
            referencia=self.referencia, cerrada_por=self.user
        )

    def _assert_bloqueada(self, url_name, **extra):
        url = reverse(url_name, kwargs={'num_refe': self.referencia.num_refe})
        resp = self.client.post(url, extra)
        self.assertRedirects(
            resp,
            reverse('finanzas:referencia_estado',
                    kwargs={'num_refe': self.referencia.num_refe}),
        )

    def test_anticipo_bloqueado_con_cierre(self):
        self._assert_bloqueada('finanzas:anticipo_crear')
        from finanzas.models import Anticipo
        self.assertEqual(Anticipo.objects.count(), 0)

    def test_gasto_bloqueado_con_cierre(self):
        self._assert_bloqueada('finanzas:gasto_crear')
        from finanzas.models import GastoReferencia
        self.assertEqual(GastoReferencia.objects.count(), 0)

    def test_subir_xml_bloqueado_con_cierre(self):
        self._assert_bloqueada('finanzas:subir_xml')
        from finanzas.models import XMLProveedor
        self.assertEqual(XMLProveedor.objects.count(), 0)

    def test_anticipo_permitido_sin_cierre(self):
        otra = _referencia('LCRR0003/26')
        url = reverse('finanzas:anticipo_crear', kwargs={'num_refe': otra.num_refe})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
```

- [ ] **Step 2: Correr y verificar fallo**

Run: `python manage.py test finanzas.test_cuenta_gastos_cierre.GuardCierreViewsTests -v 1`
Expected: FAIL — los POST con cierre no redirigen al estado (el anticipo re-renderiza el form con status 200, etc.)

- [ ] **Step 3: Implementar guard**

En `finanzas/views.py`, justo después del `referencia = get_object_or_404(Referencia, num_refe=num_refe)` de **cada una** de las 3 vistas (`anticipo_crear`, `gasto_crear`, `subir_xml_proveedor`) insertar:

```python
    from .models import CierreCuentaGastos
    if CierreCuentaGastos.activo_para(referencia):
        messages.error(request, 'La cuenta de gastos está cerrada; no se pueden registrar movimientos.')
        return redirect('finanzas:referencia_estado', num_refe=num_refe)
```

(Si `CierreCuentaGastos` ya está en el import de `.models` al inicio del archivo, usar ese import y omitir el local.)

- [ ] **Step 4: Correr tests (nuevos + regresión de la app)**

Run: `python manage.py test finanzas.test_cuenta_gastos_cierre -v 1 && python manage.py test finanzas -v 1`
Expected: OK todos (los 79+ existentes no se rompen)

- [ ] **Step 5: Commit**

```bash
git add finanzas/views.py finanzas/test_cuenta_gastos_cierre.py
git commit -m "feat(finanzas): bloquear anticipos, gastos y XML con cuenta de gastos cerrada"
```

---

### Task 4: Servicio de destinatarios y ZIP — `finanzas/cuenta_gastos_envio.py`

**Files:**
- Create: `finanzas/cuenta_gastos_envio.py`
- Create: `finanzas/test_cuenta_gastos_envio.py`

**Interfaces:**
- Consumes: `XMLProveedor` (related_name `xmls_proveedor`), `Cliente`, `saldo_referencia` de `finanzas/saldo.py`.
- Produces:
  - `destinatarios_cliente(cliente) -> tuple[str, str]` — (to, cc) con fallback a cobranza; acepta `None` → `('', '')`.
  - `construir_zip_cuenta_gastos(referencia) -> tuple[str, bytes]` — (nombre_zip, data); lanza `ValueError` si no hay CFDIs o si excede `LIMITE_ZIP_BYTES`.
  - `contexto_balanza(referencia) -> dict` — keys: `referencia`, `anticipos`, `gastos`, `saldo` (dict de `saldo_referencia`).
  - Constante `LIMITE_ZIP_BYTES = 20 * 1024 * 1024`.

- [ ] **Step 1: Escribir tests que fallan**

Crear `finanzas/test_cuenta_gastos_envio.py`:

```python
import io
import tempfile
import zipfile
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from clientes.models import Cliente
from referencias.models import Referencia

MEDIA_TMP = tempfile.mkdtemp()


def _referencia(num='LCRR0100/26'):
    return Referencia.objects.create(num_refe=num, patente='1656', prefijo='LCRR')


def _xml_proveedor(referencia, uuid='11111111-1111-1111-1111-111111111111',
                   con_pdf=True):
    from finanzas.models import XMLProveedor
    return XMLProveedor.objects.create(
        referencia=referencia, uuid_fiscal=uuid,
        fecha_emision=timezone.now(), rfc_emisor='AAA010101AAA',
        nombre_emisor='PROVEEDOR SA', rfc_receptor='BBB010101BBB',
        subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
        tipo_comprobante='I',
        xml_file=SimpleUploadedFile(f'{uuid}.xml', b'<cfdi/>'),
        pdf_file=SimpleUploadedFile(f'{uuid}.pdf', b'%PDF-1.4') if con_pdf else None,
    )


class DestinatariosClienteTests(TestCase):
    def test_usa_email_cuenta_gastos_si_existe(self):
        from finanzas.cuenta_gastos_envio import destinatarios_cliente
        cliente = Cliente.objects.create(
            nombre_cliente='A', email_cuenta_gastos='cg@a.com',
            email_cuenta_gastos_cc='cgcc@a.com',
            email_cobranza='cob@a.com', email_cobranza_cc='cobcc@a.com',
        )
        self.assertEqual(destinatarios_cliente(cliente), ('cg@a.com', 'cgcc@a.com'))

    def test_fallback_a_cobranza(self):
        from finanzas.cuenta_gastos_envio import destinatarios_cliente
        cliente = Cliente.objects.create(
            nombre_cliente='B', email_cobranza='cob@b.com',
            email_cobranza_cc='cobcc@b.com',
        )
        self.assertEqual(destinatarios_cliente(cliente), ('cob@b.com', 'cobcc@b.com'))

    def test_cliente_none_devuelve_vacios(self):
        from finanzas.cuenta_gastos_envio import destinatarios_cliente
        self.assertEqual(destinatarios_cliente(None), ('', ''))


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ConstruirZipTests(TestCase):
    def setUp(self):
        self.referencia = _referencia()

    def test_zip_contiene_xml_y_pdf(self):
        from finanzas.cuenta_gastos_envio import construir_zip_cuenta_gastos
        _xml_proveedor(self.referencia)
        _xml_proveedor(self.referencia,
                       uuid='22222222-2222-2222-2222-222222222222', con_pdf=False)
        nombre, data = construir_zip_cuenta_gastos(self.referencia)
        self.assertTrue(nombre.startswith('CG_LCRR0100-26_'))
        self.assertTrue(nombre.endswith('.zip'))
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            nombres = sorted(zf.namelist())
        self.assertEqual(nombres, [
            'CFDI_11111111-1111-1111-1111-111111111111.pdf',
            'CFDI_11111111-1111-1111-1111-111111111111.xml',
            'CFDI_22222222-2222-2222-2222-222222222222.xml',
        ])

    def test_sin_cfdis_lanza_error(self):
        from finanzas.cuenta_gastos_envio import construir_zip_cuenta_gastos
        with self.assertRaises(ValueError):
            construir_zip_cuenta_gastos(self.referencia)

    def test_zip_excede_limite_lanza_error(self):
        from finanzas import cuenta_gastos_envio
        _xml_proveedor(self.referencia)
        with patch.object(cuenta_gastos_envio, 'LIMITE_ZIP_BYTES', 10):
            with self.assertRaises(ValueError):
                cuenta_gastos_envio.construir_zip_cuenta_gastos(self.referencia)
```

- [ ] **Step 2: Correr y verificar fallo**

Run: `python manage.py test finanzas.test_cuenta_gastos_envio -v 1`
Expected: ERROR `ModuleNotFoundError: No module named 'finanzas.cuenta_gastos_envio'`

- [ ] **Step 3: Implementar módulo**

Crear `finanzas/cuenta_gastos_envio.py`:

```python
"""Envío de la cuenta de gastos de una referencia al cliente.

Correo con balanza anticipos vs. gastos y ZIP de CFDIs, vía SendGrid Web API
(custom_args para correlación con el Event Webhook). Patrón hermano de
finanzas/cobranza.py, que sigue usando SMTP.
"""
import io
import logging
import zipfile
from datetime import date

logger = logging.getLogger(__name__)

LIMITE_ZIP_BYTES = 20 * 1024 * 1024  # SendGrid admite 30 MB por mensaje


def destinatarios_cliente(cliente):
    """(to, cc) para la cuenta de gastos, con fallback a los correos de cobranza."""
    if cliente is None:
        return '', ''
    to = cliente.email_cuenta_gastos or cliente.email_cobranza
    cc = cliente.email_cuenta_gastos_cc or cliente.email_cobranza_cc
    return to, cc


def construir_zip_cuenta_gastos(referencia):
    """Empaqueta xml_file + pdf_file de cada XMLProveedor de la referencia.

    Retorna (nombre_zip, bytes). Lanza ValueError si la referencia no tiene
    CFDIs o si el ZIP excede LIMITE_ZIP_BYTES.
    """
    xmls = list(referencia.xmls_proveedor.all())
    if not xmls:
        raise ValueError('La referencia no tiene CFDIs para adjuntar.')

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for xml in xmls:
            with xml.xml_file.open('rb') as f:
                zf.writestr(f'CFDI_{xml.uuid_fiscal}.xml', f.read())
            if xml.pdf_file:
                with xml.pdf_file.open('rb') as f:
                    zf.writestr(f'CFDI_{xml.uuid_fiscal}.pdf', f.read())

    data = buffer.getvalue()
    if len(data) > LIMITE_ZIP_BYTES:
        raise ValueError(
            f'El ZIP pesa {len(data) / 1024 / 1024:.1f} MB y excede el límite '
            f'de {LIMITE_ZIP_BYTES // 1024 // 1024} MB para envío por correo.'
        )
    nombre = f"CG_{referencia.num_refe.replace('/', '-')}_{date.today():%Y%m%d}.zip"
    return nombre, data


def contexto_balanza(referencia):
    """Contexto compartido por el email y la vista previa en pantalla."""
    from .saldo import saldo_referencia
    return {
        'referencia': referencia,
        'anticipos': referencia.anticipos.order_by('fecha'),
        'gastos': referencia.gastos_finanzas.order_by('fecha'),
        'saldo': saldo_referencia(referencia),
    }
```

- [ ] **Step 4: Correr tests**

Run: `python manage.py test finanzas.test_cuenta_gastos_envio -v 1`
Expected: OK (6 tests)

- [ ] **Step 5: Commit**

```bash
git add finanzas/cuenta_gastos_envio.py finanzas/test_cuenta_gastos_envio.py
git commit -m "feat(finanzas): destinatarios con fallback y ZIP de CFDIs para cuenta de gastos"
```

---

### Task 5: Template del correo con balanza

**Files:**
- Create: `templates/finanzas/email_cuenta_gastos.html`
- Test: `finanzas/test_cuenta_gastos_envio.py` (agregar clase)

**Interfaces:**
- Consumes: `contexto_balanza(referencia)` (Task 4).
- Produces: template renderizable con `render_to_string('finanzas/email_cuenta_gastos.html', contexto_balanza(ref))`.

- [ ] **Step 1: Escribir test que falla**

Agregar a `finanzas/test_cuenta_gastos_envio.py`:

```python
class EmailBalanzaTemplateTests(TestCase):
    def test_render_contiene_balanza(self):
        from django.template.loader import render_to_string
        from finanzas.cuenta_gastos_envio import contexto_balanza
        from finanzas.models import Anticipo, GastoReferencia
        referencia = _referencia('LCRR0200/26')
        Anticipo.objects.create(
            referencia=referencia, fecha=timezone.now().date(),
            monto=Decimal('5000'), forma_pago='03',
        )
        GastoReferencia.objects.create(
            referencia=referencia, tipo='MANIOBRAS', concepto='MUELLAJE',
            fecha=timezone.now().date(), monto=Decimal('11094'),
        )
        html = render_to_string(
            'finanzas/email_cuenta_gastos.html', contexto_balanza(referencia)
        )
        self.assertIn('LCRR0200/26', html)
        self.assertIn('Anticipos del cliente', html)
        self.assertIn('MUELLAJE', html)
        self.assertIn('5000', html.replace(',', ''))
        self.assertIn('Saldo', html)
```

- [ ] **Step 2: Correr y verificar fallo**

Run: `python manage.py test finanzas.test_cuenta_gastos_envio.EmailBalanzaTemplateTests -v 1`
Expected: ERROR `TemplateDoesNotExist: finanzas/email_cuenta_gastos.html`

- [ ] **Step 3: Crear template**

Crear `templates/finanzas/email_cuenta_gastos.html` (CSS inline, tablas — compatible con clientes de correo; mismo estilo sobrio que `email_recordatorio_cobranza.html`):

```html
<!DOCTYPE html>
<html lang="es">
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;color:#1e293b;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:24px 0;">
  <tr><td align="center">
    <table role="presentation" width="640" cellpadding="0" cellspacing="0"
           style="background:#ffffff;border-radius:12px;overflow:hidden;">
      <tr>
        <td style="background:#0f172a;padding:20px 28px;">
          <p style="margin:0;color:#94a3b8;font-size:11px;letter-spacing:2px;text-transform:uppercase;">Cuenta de gastos</p>
          <h1 style="margin:4px 0 0;color:#ffffff;font-size:22px;">{{ referencia.num_refe }}</h1>
          <p style="margin:4px 0 0;color:#cbd5e1;font-size:13px;">{{ referencia.nombre_cliente }}</p>
        </td>
      </tr>
      <tr>
        <td style="padding:24px 28px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <!-- Anticipos -->
              <td width="49%" valign="top" style="border:1px solid #e2e8f0;border-radius:8px;padding:14px;">
                <p style="margin:0 0 10px;font-size:12px;letter-spacing:1px;text-transform:uppercase;color:#16a34a;font-weight:bold;">Anticipos del cliente</p>
                {% for a in anticipos %}
                <p style="margin:0 0 6px;font-size:13px;">
                  {{ a.fecha|date:"d/m/Y" }} —
                  <strong>${{ a.monto|floatformat:2 }} {{ a.moneda }}</strong>
                </p>
                {% empty %}
                <p style="margin:0;font-size:13px;color:#94a3b8;">Sin anticipos registrados</p>
                {% endfor %}
                <p style="margin:12px 0 0;padding-top:10px;border-top:1px solid #e2e8f0;font-size:14px;">
                  Total: <strong style="color:#16a34a;">${{ saldo.total_anticipos|floatformat:2 }}</strong>
                </p>
              </td>
              <td width="2%"></td>
              <!-- Gastos -->
              <td width="49%" valign="top" style="border:1px solid #e2e8f0;border-radius:8px;padding:14px;">
                <p style="margin:0 0 10px;font-size:12px;letter-spacing:1px;text-transform:uppercase;color:#dc2626;font-weight:bold;">Gastos</p>
                {% for g in gastos %}
                <p style="margin:0 0 6px;font-size:13px;">
                  {{ g.concepto }} —
                  <strong>${{ g.monto|floatformat:2 }} {{ g.moneda }}</strong>
                </p>
                {% empty %}
                <p style="margin:0;font-size:13px;color:#94a3b8;">Sin gastos registrados</p>
                {% endfor %}
                <p style="margin:12px 0 0;padding-top:10px;border-top:1px solid #e2e8f0;font-size:14px;">
                  Total: <strong style="color:#dc2626;">${{ saldo.total_gastos|floatformat:2 }}</strong>
                </p>
              </td>
            </tr>
          </table>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:18px;">
            <tr>
              <td style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:14px 18px;">
                <p style="margin:0;font-size:12px;letter-spacing:1px;text-transform:uppercase;color:#64748b;">Saldo</p>
                <p style="margin:4px 0 0;font-size:20px;font-weight:bold;color:{% if saldo.saldo >= 0 %}#0284c7{% else %}#ea580c{% endif %};">
                  ${{ saldo.saldo|floatformat:2 }}
                </p>
                <p style="margin:2px 0 0;font-size:12px;color:#94a3b8;">
                  {% if saldo.saldo > 0 %}Remanente a su favor{% elif saldo.saldo < 0 %}Pendiente de cobro{% else %}Saldo cero{% endif %}
                </p>
              </td>
            </tr>
          </table>
          <p style="margin:20px 0 0;font-size:13px;color:#475569;">
            Se adjunta un archivo ZIP con los XML y PDF de los comprobantes fiscales de esta cuenta de gastos.
          </p>
        </td>
      </tr>
      <tr>
        <td style="padding:14px 28px;background:#f8fafc;border-top:1px solid #e2e8f0;">
          <p style="margin:0;font-size:11px;color:#94a3b8;">Este correo fue generado automáticamente por el sistema HAL9MIL.</p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>
```

- [ ] **Step 4: Correr tests**

Run: `python manage.py test finanzas.test_cuenta_gastos_envio -v 1`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add templates/finanzas/email_cuenta_gastos.html finanzas/test_cuenta_gastos_envio.py
git commit -m "feat(finanzas): template de correo con balanza de cuenta de gastos"
```

---

### Task 6: Servicio `enviar_cuenta_gastos` con SendGrid Web API

**Files:**
- Modify: `finanzas/cuenta_gastos_envio.py`
- Modify: `hal9mil/settings.py` (agregar `SENDGRID_API_KEY`)
- Test: `finanzas/test_cuenta_gastos_envio.py` (agregar clase)

**Interfaces:**
- Consumes: `NotificacionCuentaGastos` (Task 1), `construir_zip_cuenta_gastos`/`contexto_balanza` (Task 4), template (Task 5).
- Produces: `enviar_cuenta_gastos(referencia, destinatario, cc='', usuario=None, es_reenvio=False) -> NotificacionCuentaGastos` — crea la notificación, envía con `custom_arg` `notificacion_cg_id`, guarda `sg_message_id` y `zip_file`; en fallo deja `estado='ERROR'` + `error_msg`. En reenvío reutiliza el `zip_file` previo.

- [ ] **Step 1: Escribir tests que fallan**

Agregar a `finanzas/test_cuenta_gastos_envio.py`:

```python
def _resp_sendgrid(status=202, message_id='sg-msg-001'):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {'X-Message-Id': message_id}
    return resp


@override_settings(MEDIA_ROOT=MEDIA_TMP, SENDGRID_API_KEY='SG.test')
class EnviarCuentaGastosTests(TestCase):
    def setUp(self):
        self.referencia = _referencia('LCRR0300/26')
        _xml_proveedor(self.referencia)
        self.user = User.objects.create_user('emisor', password='x')

    def test_envio_exitoso_guarda_notificacion(self):
        from finanzas.cuenta_gastos_envio import enviar_cuenta_gastos
        with patch('finanzas.cuenta_gastos_envio.SendGridAPIClient') as cliente_cls:
            cliente_cls.return_value.send.return_value = _resp_sendgrid()
            notif = enviar_cuenta_gastos(
                self.referencia, 'cliente@example.com', 'cc@example.com', self.user
            )
        self.assertEqual(notif.estado, 'ENVIADO')
        self.assertEqual(notif.sg_message_id, 'sg-msg-001')
        self.assertEqual(notif.destinatario, 'cliente@example.com')
        self.assertTrue(notif.zip_file.name)
        # custom_arg de correlación presente en el Mail enviado
        mail_enviado = cliente_cls.return_value.send.call_args[0][0]
        cuerpo = mail_enviado.get()
        self.assertEqual(
            cuerpo['custom_args']['notificacion_cg_id'], str(notif.pk)
        )
        self.assertEqual(len(cuerpo['attachments']), 1)

    def test_error_de_api_deja_estado_error(self):
        from finanzas.cuenta_gastos_envio import enviar_cuenta_gastos
        with patch('finanzas.cuenta_gastos_envio.SendGridAPIClient') as cliente_cls:
            cliente_cls.return_value.send.side_effect = Exception('boom sendgrid')
            notif = enviar_cuenta_gastos(self.referencia, 'x@example.com')
        self.assertEqual(notif.estado, 'ERROR')
        self.assertIn('boom sendgrid', notif.error_msg)

    def test_status_400_deja_estado_error(self):
        from finanzas.cuenta_gastos_envio import enviar_cuenta_gastos
        with patch('finanzas.cuenta_gastos_envio.SendGridAPIClient') as cliente_cls:
            cliente_cls.return_value.send.return_value = _resp_sendgrid(status=401)
            notif = enviar_cuenta_gastos(self.referencia, 'x@example.com')
        self.assertEqual(notif.estado, 'ERROR')

    def test_reenvio_reutiliza_zip(self):
        from finanzas.cuenta_gastos_envio import enviar_cuenta_gastos
        with patch('finanzas.cuenta_gastos_envio.SendGridAPIClient') as cliente_cls:
            cliente_cls.return_value.send.return_value = _resp_sendgrid()
            primera = enviar_cuenta_gastos(self.referencia, 'a@example.com')
            with patch(
                'finanzas.cuenta_gastos_envio.construir_zip_cuenta_gastos'
            ) as builder:
                segunda = enviar_cuenta_gastos(
                    self.referencia, 'otro@example.com', es_reenvio=True
                )
        builder.assert_not_called()
        self.assertTrue(segunda.es_reenvio)
        self.assertEqual(segunda.zip_file.name, primera.zip_file.name)
```

- [ ] **Step 2: Correr y verificar fallo**

Run: `python manage.py test finanzas.test_cuenta_gastos_envio.EnviarCuentaGastosTests -v 1`
Expected: ERROR `ImportError: cannot import name 'enviar_cuenta_gastos'`

- [ ] **Step 3: Implementar**

En `hal9mil/settings.py`, junto al bloque de Email (después de `DEFAULT_FROM_EMAIL`):

```python
# API key para envíos vía SendGrid Web API (cuenta de gastos); mismo secreto
# que usa el SMTP de arriba.
SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY', '')
```

En `finanzas/cuenta_gastos_envio.py` — agregar imports arriba:

```python
import base64
import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Attachment, CustomArg, Disposition, FileContent, FileName, FileType, Mail,
)
```

y la función al final del módulo:

```python
def enviar_cuenta_gastos(referencia, destinatario, cc='', usuario=None,
                         es_reenvio=False):
    """Envía la cuenta de gastos por correo y registra la notificación.

    Nunca lanza: en fallo la notificación queda en ERROR con error_msg y el
    llamador decide qué mostrar. Retorna la NotificacionCuentaGastos.
    """
    from .models import NotificacionCuentaGastos

    notif = NotificacionCuentaGastos.objects.create(
        referencia=referencia, destinatario=destinatario, cc=cc or '',
        enviado_por=usuario, es_reenvio=es_reenvio,
    )
    try:
        previa = None
        if es_reenvio:
            previa = (
                NotificacionCuentaGastos.objects
                .filter(referencia=referencia, zip_file__isnull=False)
                .exclude(zip_file='').exclude(pk=notif.pk)
                .order_by('-enviado_en').first()
            )
        if previa:
            with previa.zip_file.open('rb') as f:
                data = f.read()
            nombre = os.path.basename(previa.zip_file.name)
            notif.zip_file.name = previa.zip_file.name
        else:
            nombre, data = construir_zip_cuenta_gastos(referencia)
            notif.zip_file.save(nombre, ContentFile(data), save=False)

        html = render_to_string(
            'finanzas/email_cuenta_gastos.html', contexto_balanza(referencia)
        )
        mensaje = Mail(
            from_email=settings.DEFAULT_FROM_EMAIL,
            to_emails=destinatario,
            subject=f'Cuenta de gastos {referencia.num_refe} — Reiki Logística',
            html_content=html,
        )
        if cc:
            mensaje.add_cc(cc)
        mensaje.attachment = Attachment(
            FileContent(base64.b64encode(data).decode()),
            FileName(nombre),
            FileType('application/zip'),
            Disposition('attachment'),
        )
        mensaje.custom_arg = CustomArg('notificacion_cg_id', str(notif.pk))

        respuesta = SendGridAPIClient(settings.SENDGRID_API_KEY).send(mensaje)
        if respuesta.status_code >= 400:
            raise RuntimeError(f'SendGrid respondió status {respuesta.status_code}')
        notif.sg_message_id = respuesta.headers.get('X-Message-Id', '') or ''
        notif.estado = 'ENVIADO'
        logger.info('[CG] Cuenta de gastos %s enviada a %s (notif %s)',
                    referencia.num_refe, destinatario, notif.pk)
    except Exception as e:
        notif.estado = 'ERROR'
        notif.error_msg = str(e)
        logger.error('[CG] Error enviando cuenta de gastos %s: %s',
                     referencia.num_refe, e)
    notif.save()
    return notif
```

- [ ] **Step 4: Correr tests**

Run: `python manage.py test finanzas.test_cuenta_gastos_envio -v 1`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add finanzas/cuenta_gastos_envio.py finanzas/test_cuenta_gastos_envio.py hal9mil/settings.py
git commit -m "feat(finanzas): servicio de envío de cuenta de gastos vía SendGrid Web API"
```

---

### Task 7: Vistas cerrar / reabrir / enviar + URLs

**Files:**
- Create: `finanzas/views_cuenta_gastos.py`
- Modify: `finanzas/urls.py`
- Test: `finanzas/test_cuenta_gastos_cierre.py` y `finanzas/test_cuenta_gastos_envio.py` (agregar clases)

**Interfaces:**
- Consumes: `CierreCuentaGastos`, `enviar_cuenta_gastos`, `modulo_required`.
- Produces: URLs con names `finanzas:cerrar_cg`, `finanzas:reabrir_cg`, `finanzas:enviar_cg` (todas POST-only; GET redirige al estado financiero). Vistas: `cerrar_cg(request, num_refe)`, `reabrir_cg(request, num_refe)`, `enviar_cg(request, num_refe)`.

- [ ] **Step 1: Escribir tests que fallan**

Agregar a `finanzas/test_cuenta_gastos_cierre.py`:

```python
class CerrarReabrirViewsTests(TestCase):
    def setUp(self):
        _login_finanzas(self)
        self.referencia = _referencia('LCRR0004/26')

    def _url(self, name):
        return reverse(name, kwargs={'num_refe': self.referencia.num_refe})

    def test_cerrar_crea_cierre_activo(self):
        from finanzas.models import CierreCuentaGastos
        resp = self.client.post(self._url('finanzas:cerrar_cg'), {'nota': 'lista'})
        self.assertRedirects(resp, self._url('finanzas:referencia_estado'))
        cierre = CierreCuentaGastos.activo_para(self.referencia)
        self.assertIsNotNone(cierre)
        self.assertEqual(cierre.cerrada_por, self.user)
        self.assertEqual(cierre.nota, 'lista')

    def test_get_no_cierra(self):
        from finanzas.models import CierreCuentaGastos
        self.client.get(self._url('finanzas:cerrar_cg'))
        self.assertIsNone(CierreCuentaGastos.activo_para(self.referencia))

    def test_usuario_sin_finanzas_no_puede_cerrar(self):
        from django.contrib.auth.models import User as U
        U.objects.create_user('ajeno', password='x')
        self.client.login(username='ajeno', password='x')
        self.client.post(self._url('finanzas:cerrar_cg'))
        from finanzas.models import CierreCuentaGastos
        self.assertIsNone(CierreCuentaGastos.activo_para(self.referencia))

    def test_reabrir_requiere_superusuario(self):
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        self.client.post(self._url('finanzas:reabrir_cg'))
        self.assertIsNotNone(CierreCuentaGastos.activo_para(self.referencia))

    def test_superusuario_reabre(self):
        from django.contrib.auth.models import User as U
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        superu = U.objects.create_superuser('root', password='x')
        self.client.login(username='root', password='x')
        self.client.post(self._url('finanzas:reabrir_cg'))
        self.assertIsNone(CierreCuentaGastos.activo_para(self.referencia))
        cierre = CierreCuentaGastos.objects.get(referencia=self.referencia)
        self.assertEqual(cierre.reabierta_por, superu)

    def test_recierre_tras_reapertura_limpia_campos(self):
        from django.utils import timezone as tz
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(
            referencia=self.referencia, cerrada_por=self.user,
            reabierta_por=self.user, reabierta_en=tz.now(),
        )
        self.client.post(self._url('finanzas:cerrar_cg'))
        cierre = CierreCuentaGastos.objects.get(referencia=self.referencia)
        self.assertTrue(cierre.activa)
        self.assertIsNone(cierre.reabierta_por)
```

Agregar a `finanzas/test_cuenta_gastos_envio.py`:

```python
@override_settings(MEDIA_ROOT=MEDIA_TMP, SENDGRID_API_KEY='SG.test')
class EnviarCgViewTests(TestCase):
    def setUp(self):
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.user = User.objects.create_user('envia_cg', password='x')
        self.user.groups.add(grupo)
        self.client.login(username='envia_cg', password='x')
        self.referencia = _referencia('LCRR0400/26')
        _xml_proveedor(self.referencia)
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        self.url = reverse('finanzas:enviar_cg',
                           kwargs={'num_refe': self.referencia.num_refe})

    def test_post_envia_y_registra(self):
        from finanzas.models import NotificacionCuentaGastos
        with patch('finanzas.cuenta_gastos_envio.SendGridAPIClient') as cls:
            cls.return_value.send.return_value = _resp_sendgrid()
            resp = self.client.post(self.url, {'destinatario': 'c@x.com'})
        self.assertEqual(resp.status_code, 302)
        notif = NotificacionCuentaGastos.objects.get()
        self.assertEqual(notif.enviado_por, self.user)
        self.assertFalse(notif.es_reenvio)

    def test_segundo_envio_es_reenvio(self):
        from finanzas.models import NotificacionCuentaGastos
        with patch('finanzas.cuenta_gastos_envio.SendGridAPIClient') as cls:
            cls.return_value.send.return_value = _resp_sendgrid()
            self.client.post(self.url, {'destinatario': 'c@x.com'})
            self.client.post(self.url, {'destinatario': 'otro@x.com'})
        segunda = NotificacionCuentaGastos.objects.order_by('pk').last()
        self.assertTrue(segunda.es_reenvio)

    def test_sin_destinatario_no_envia(self):
        from finanzas.models import NotificacionCuentaGastos
        self.client.post(self.url, {'destinatario': ''})
        self.assertEqual(NotificacionCuentaGastos.objects.count(), 0)

    def test_sin_cierre_activo_no_envia(self):
        from finanzas.models import CierreCuentaGastos, NotificacionCuentaGastos
        CierreCuentaGastos.objects.all().delete()
        self.client.post(self.url, {'destinatario': 'c@x.com'})
        self.assertEqual(NotificacionCuentaGastos.objects.count(), 0)
```

- [ ] **Step 2: Correr y verificar fallo**

Run: `python manage.py test finanzas.test_cuenta_gastos_cierre.CerrarReabrirViewsTests finanzas.test_cuenta_gastos_envio.EnviarCgViewTests -v 1`
Expected: ERROR `NoReverseMatch: 'cerrar_cg' not found`

- [ ] **Step 3: Implementar vistas y URLs**

Crear `finanzas/views_cuenta_gastos.py`:

```python
"""Vistas del flujo de cierre y envío de la cuenta de gastos al cliente."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from core.permisos import modulo_required
from referencias.models import Referencia

from .cuenta_gastos_envio import enviar_cuenta_gastos
from .models import CierreCuentaGastos


def _redirect_estado(num_refe):
    return redirect('finanzas:referencia_estado', num_refe=num_refe)


@modulo_required('Finanzas')
def cerrar_cg(request, num_refe):
    referencia = get_object_or_404(Referencia, num_refe=num_refe)
    if request.method != 'POST':
        return _redirect_estado(num_refe)

    cierre = CierreCuentaGastos.objects.filter(referencia=referencia).first()
    if cierre and cierre.activa:
        messages.info(request, 'La cuenta de gastos ya está cerrada.')
        return _redirect_estado(num_refe)

    nota = request.POST.get('nota', '').strip()[:300]
    if cierre:
        cierre.cerrada_por = request.user
        cierre.cerrada_en = timezone.now()
        cierre.nota = nota
        cierre.reabierta_por = None
        cierre.reabierta_en = None
        cierre.save()
    else:
        CierreCuentaGastos.objects.create(
            referencia=referencia, cerrada_por=request.user, nota=nota,
        )
    messages.success(
        request,
        'Cuenta de gastos cerrada. Ya no se pueden registrar anticipos ni gastos.',
    )
    return _redirect_estado(num_refe)


@login_required
def reabrir_cg(request, num_refe):
    referencia = get_object_or_404(Referencia, num_refe=num_refe)
    if request.method != 'POST':
        return _redirect_estado(num_refe)
    if not request.user.is_superuser:
        messages.error(request, 'Solo un superusuario puede reabrir la cuenta de gastos.')
        return _redirect_estado(num_refe)

    cierre = CierreCuentaGastos.activo_para(referencia)
    if cierre:
        cierre.reabierta_por = request.user
        cierre.reabierta_en = timezone.now()
        cierre.save()
        messages.success(request, 'Cuenta de gastos reabierta.')
    return _redirect_estado(num_refe)


@modulo_required('Finanzas')
def enviar_cg(request, num_refe):
    referencia = get_object_or_404(Referencia, num_refe=num_refe)
    if request.method != 'POST':
        return _redirect_estado(num_refe)
    if not CierreCuentaGastos.activo_para(referencia):
        messages.error(request, 'La cuenta de gastos debe cerrarse antes de enviarse.')
        return _redirect_estado(num_refe)

    destinatario = request.POST.get('destinatario', '').strip()
    if not destinatario:
        messages.error(request, 'Captura el correo del destinatario.')
        return _redirect_estado(num_refe)
    cc = request.POST.get('cc', '').strip()

    es_reenvio = referencia.notificaciones_cg.exists()
    notif = enviar_cuenta_gastos(
        referencia, destinatario, cc, request.user, es_reenvio=es_reenvio,
    )
    if notif.estado == 'ERROR':
        messages.error(request, f'No se pudo enviar la cuenta de gastos: {notif.error_msg}')
    else:
        messages.success(request, f'Cuenta de gastos enviada a {destinatario}.')
    return _redirect_estado(num_refe)
```

En `finanzas/urls.py` — agregar import y rutas (antes del bloque "Rutas por referencia" para las globales; las de referencia junto a sus hermanas):

```python
from . import views, views_cuenta_gastos
```

```python
    # Cierre y envío de cuenta de gastos
    path('referencias/<path:num_refe>/cerrar-cg/', views_cuenta_gastos.cerrar_cg, name='cerrar_cg'),
    path('referencias/<path:num_refe>/reabrir-cg/', views_cuenta_gastos.reabrir_cg, name='reabrir_cg'),
    path('referencias/<path:num_refe>/enviar-cg/', views_cuenta_gastos.enviar_cg, name='enviar_cg'),
```

- [ ] **Step 4: Correr tests**

Run: `python manage.py test finanzas.test_cuenta_gastos_cierre finanzas.test_cuenta_gastos_envio -v 1`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add finanzas/views_cuenta_gastos.py finanzas/urls.py finanzas/test_cuenta_gastos_cierre.py finanzas/test_cuenta_gastos_envio.py
git commit -m "feat(finanzas): vistas de cierre, reapertura y envío de cuenta de gastos"
```

---

### Task 8: UI en `referencia_estado.html` — banner, balanza, formularios

**Files:**
- Modify: `finanzas/views.py:72-82` (vista `referencia_estado_financiero` — agregar contexto)
- Modify: `templates/finanzas/referencia_estado.html`
- Test: `finanzas/test_cuenta_gastos_cierre.py` (agregar clase)

**Interfaces:**
- Consumes: URLs de Task 7, `destinatarios_cliente` (Task 4), `CierreCuentaGastos.activo_para`.
- Produces: contexto adicional en la vista: `cierre` (o `None`), `notificaciones` (queryset), `destinatario_sugerido`, `cc_sugerido`.

- [ ] **Step 1: Escribir tests que fallan**

Agregar a `finanzas/test_cuenta_gastos_cierre.py`:

```python
class EstadoFinancieroTemplateTests(TestCase):
    def setUp(self):
        _login_finanzas(self)
        self.referencia = _referencia('LCRR0005/26')
        self.url = reverse('finanzas:referencia_estado',
                           kwargs={'num_refe': self.referencia.num_refe})

    def test_abierta_muestra_botones_y_upload(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, '+ Anticipo')
        self.assertContains(resp, 'Subir XML de proveedor')
        self.assertContains(resp, 'Cerrar cuenta de gastos')
        self.assertNotContains(resp, 'Enviar al cliente')

    def test_cerrada_oculta_botones_y_muestra_balanza(self):
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        resp = self.client.get(self.url)
        self.assertNotContains(resp, '+ Anticipo')
        self.assertNotContains(resp, 'Subir XML de proveedor')
        self.assertContains(resp, 'Cuenta de gastos cerrada')
        self.assertContains(resp, 'Balanza de la cuenta de gastos')
        self.assertContains(resp, 'Enviar al cliente')
        self.assertContains(resp, 'Emitir factura')  # nunca se bloquea

    def test_cerrada_no_muestra_reabrir_a_no_superusuario(self):
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        resp = self.client.get(self.url)
        self.assertNotContains(resp, 'Reabrir cuenta')

    def test_cerrada_muestra_reabrir_a_superusuario(self):
        from django.contrib.auth.models import User as U
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        U.objects.create_superuser('root2', password='x')
        self.client.login(username='root2', password='x')
        resp = self.client.get(self.url)
        self.assertContains(resp, 'Reabrir cuenta')

    def test_con_envio_previo_muestra_historial_y_reenviar(self):
        from finanzas.models import CierreCuentaGastos, NotificacionCuentaGastos
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        NotificacionCuentaGastos.objects.create(
            referencia=self.referencia, destinatario='c@x.com',
            enviado_por=self.user,
        )
        resp = self.client.get(self.url)
        self.assertContains(resp, 'Historial de envíos')
        self.assertContains(resp, 'c@x.com')
        self.assertContains(resp, 'Reenviar')

    def test_destinatario_prellenado_con_fallback(self):
        from clientes.models import Cliente
        from finanzas.models import CierreCuentaGastos
        self.referencia.cve_cliente = 'CAC001'
        self.referencia.save(update_fields=['cve_cliente'])
        Cliente.objects.create(nombre_cliente='CACIPA', cve_cliente='CAC001',
                               email_cobranza='cob@cacipa.com')
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'cob@cacipa.com')
```

- [ ] **Step 2: Correr y verificar fallo**

Run: `python manage.py test finanzas.test_cuenta_gastos_cierre.EstadoFinancieroTemplateTests -v 1`
Expected: FAIL (no existe "Cerrar cuenta de gastos" en el template)

- [ ] **Step 3: Modificar vista**

Reemplazar `referencia_estado_financiero` en `finanzas/views.py`:

```python
def referencia_estado_financiero(request, num_refe):
    from clientes.models import Cliente
    from .cuenta_gastos_envio import destinatarios_cliente
    from .models import CierreCuentaGastos

    referencia = get_object_or_404(Referencia, num_refe=num_refe)
    anticipos = referencia.anticipos.select_related('registrado_por').order_by('-fecha')
    gastos = referencia.gastos_finanzas.select_related('cuenta_gasto', 'registrado_por').order_by('-fecha')
    saldo = saldo_referencia(referencia)

    cierre = CierreCuentaGastos.activo_para(referencia)
    notificaciones = referencia.notificaciones_cg.select_related('enviado_por')
    cliente = Cliente.objects.filter(cve_cliente=referencia.cve_cliente).first() \
        if referencia.cve_cliente else None
    destinatario_sugerido, cc_sugerido = destinatarios_cliente(cliente)

    return render(request, 'finanzas/referencia_estado.html', {
        'referencia': referencia,
        'anticipos': anticipos,
        'gastos': gastos,
        'saldo': saldo,
        'cierre': cierre,
        'notificaciones': notificaciones,
        'destinatario_sugerido': destinatario_sugerido,
        'cc_sugerido': cc_sugerido,
    })
```

- [ ] **Step 4: Modificar template**

En `templates/finanzas/referencia_estado.html`:

**(a)** Reemplazar el bloque de botones del header (líneas 13-26) por:

```html
    <div class="flex gap-2">
      {% if not cierre %}
      <a href="{% url 'finanzas:anticipo_crear' num_refe=referencia.num_refe %}"
         class="bg-sky-600 hover:bg-sky-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors">
        + Anticipo
      </a>
      <a href="{% url 'finanzas:gasto_crear' num_refe=referencia.num_refe %}"
         class="bg-slate-600 hover:bg-slate-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors">
        + Gasto
      </a>
      <form method="post" action="{% url 'finanzas:cerrar_cg' num_refe=referencia.num_refe %}"
            onsubmit="return confirm('¿Cerrar la cuenta de gastos? Ya no se podrán registrar anticipos ni gastos.');">
        {% csrf_token %}
        <button type="submit"
                class="bg-amber-600 hover:bg-amber-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors">
          Cerrar cuenta de gastos
        </button>
      </form>
      {% endif %}
      <a href="{% url 'finanzas:factura_crear' num_refe=referencia.num_refe %}"
         class="bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors">
        Emitir factura
      </a>
    </div>
```

**(b)** Después del bloque de mensajes (línea ~32), agregar el banner:

```html
  {% if cierre %}
  <div class="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 flex items-center justify-between">
    <p class="text-sm text-amber-800">
      🔒 Cuenta de gastos cerrada el {{ cierre.cerrada_en|date:"d/m/Y H:i" }}
      por {{ cierre.cerrada_por|default:"—" }}.
      {% if cierre.nota %}<span class="text-amber-600">{{ cierre.nota }}</span>{% endif %}
    </p>
    {% if request.user.is_superuser %}
    <form method="post" action="{% url 'finanzas:reabrir_cg' num_refe=referencia.num_refe %}"
          onsubmit="return confirm('¿Reabrir la cuenta de gastos?');">
      {% csrf_token %}
      <button type="submit" class="text-xs text-amber-700 border border-amber-300 hover:bg-amber-100 px-3 py-1.5 rounded-lg">
        Reabrir cuenta
      </button>
    </form>
    {% endif %}
  </div>
  {% endif %}
```

**(c)** Envolver la sección "Upload XML proveedor" completa (el `<div>` de líneas 98-173) en `{% if not cierre %} ... {% endif %}`.

**(d)** Antes de la sección `<!-- Gastos -->`, agregar la balanza + envío (solo con cierre):

```html
  {% if cierre %}
  <!-- Balanza y envío al cliente -->
  <div class="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
    <h2 class="font-semibold text-slate-700">Balanza de la cuenta de gastos</h2>
    <div class="grid grid-cols-2 gap-4">
      <div class="border border-slate-200 rounded-lg p-4">
        <p class="text-xs text-green-600 font-semibold uppercase tracking-wider mb-2">Anticipos del cliente</p>
        {% for a in anticipos %}
        <div class="flex justify-between text-sm py-1 border-b border-slate-50">
          <span class="text-slate-600">{{ a.fecha|date:"d/m/Y" }}</span>
          <span class="font-mono text-green-700">${{ a.monto|floatformat:2 }} {{ a.moneda }}</span>
        </div>
        {% empty %}
        <p class="text-sm text-slate-400">Sin anticipos registrados</p>
        {% endfor %}
        <p class="text-sm font-semibold mt-2 pt-2 border-t border-slate-200">
          Total: <span class="text-green-600">${{ saldo.total_anticipos|floatformat:2 }}</span>
        </p>
      </div>
      <div class="border border-slate-200 rounded-lg p-4">
        <p class="text-xs text-red-600 font-semibold uppercase tracking-wider mb-2">Gastos</p>
        {% for g in gastos %}
        <div class="flex justify-between text-sm py-1 border-b border-slate-50">
          <span class="text-slate-600 truncate pr-2">{{ g.concepto }}</span>
          <span class="font-mono text-red-700 whitespace-nowrap">${{ g.monto|floatformat:2 }} {{ g.moneda }}</span>
        </div>
        {% empty %}
        <p class="text-sm text-slate-400">Sin gastos registrados</p>
        {% endfor %}
        <p class="text-sm font-semibold mt-2 pt-2 border-t border-slate-200">
          Total: <span class="text-red-600">${{ saldo.total_gastos|floatformat:2 }}</span>
        </p>
      </div>
    </div>

    <!-- Envío -->
    <form method="post" action="{% url 'finanzas:enviar_cg' num_refe=referencia.num_refe %}"
          class="flex flex-wrap items-end gap-3 border-t border-slate-100 pt-4">
      {% csrf_token %}
      <div>
        <label class="block text-xs text-slate-500 mb-1">Destinatario</label>
        <input type="email" name="destinatario" required
               value="{% if not notificaciones %}{{ destinatario_sugerido }}{% endif %}"
               placeholder="correo@cliente.com"
               class="border border-slate-200 rounded-lg px-3 py-1.5 text-sm w-64">
      </div>
      <div>
        <label class="block text-xs text-slate-500 mb-1">CC (opcional)</label>
        <input type="email" name="cc" value="{% if not notificaciones %}{{ cc_sugerido }}{% endif %}"
               class="border border-slate-200 rounded-lg px-3 py-1.5 text-sm w-64">
      </div>
      <button type="submit"
              class="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors">
        {% if notificaciones %}Reenviar{% else %}Enviar al cliente{% endif %}
      </button>
      <p class="text-xs text-slate-400 w-full">
        Se enviará la balanza con un ZIP de los {{ referencia.xmls_proveedor.count }} CFDI de la referencia.
      </p>
    </form>

    {% if notificaciones %}
    <div>
      <h3 class="text-sm font-semibold text-slate-600 mb-2">Historial de envíos</h3>
      <table class="w-full text-xs">
        <thead class="text-slate-400 uppercase">
          <tr>
            <th class="text-left py-1 pr-4">Fecha</th>
            <th class="text-left py-1 pr-4">Destinatario</th>
            <th class="text-left py-1 pr-4">Enviado por</th>
            <th class="text-left py-1">Estado</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
          {% for n in notificaciones %}
          <tr>
            <td class="py-1.5 pr-4 text-slate-600">{{ n.enviado_en|date:"d/m/Y H:i" }}</td>
            <td class="py-1.5 pr-4 font-mono">{{ n.destinatario }}</td>
            <td class="py-1.5 pr-4 text-slate-500">{{ n.enviado_por|default:"—" }}</td>
            <td class="py-1.5">
              <span class="px-2 py-0.5 rounded-full
                {% if n.estado == 'LEIDO' %}bg-green-100 text-green-700
                {% elif n.estado == 'ENTREGADO' %}bg-sky-100 text-sky-700
                {% elif n.estado == 'ENVIADO' %}bg-slate-100 text-slate-600
                {% else %}bg-red-100 text-red-700{% endif %}">
                {{ n.get_estado_display }}
              </span>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    {% endif %}
  </div>
  {% endif %}
```

- [ ] **Step 5: Correr tests (nuevos + regresión)**

Run: `python manage.py test finanzas.test_cuenta_gastos_cierre -v 1 && python manage.py test finanzas -v 1`
Expected: OK

- [ ] **Step 6: Commit**

```bash
git add finanzas/views.py templates/finanzas/referencia_estado.html finanzas/test_cuenta_gastos_cierre.py
git commit -m "feat(finanzas): UI de cierre, balanza y envío en estado financiero"
```

---

### Task 9: Webhook de eventos SendGrid

**Files:**
- Modify: `finanzas/cuenta_gastos_envio.py` (agregar `procesar_evento_sendgrid`)
- Modify: `finanzas/views_cuenta_gastos.py` (agregar vista `sendgrid_webhook`)
- Modify: `finanzas/urls.py`
- Modify: `hal9mil/settings.py` (agregar `SENDGRID_WEBHOOK_PUBLIC_KEY`)
- Create: `finanzas/test_cuenta_gastos_webhook.py`

**Interfaces:**
- Consumes: `NotificacionCuentaGastos`.
- Produces:
  - `procesar_evento_sendgrid(evento: dict) -> None` — actualiza estado según `evento['event']` y `evento['notificacion_cg_id']`.
  - URL `finanzas:sendgrid_webhook` = `POST /finanzas/webhooks/sendgrid/`, `csrf_exempt`, verifica firma con `EventWebhook` de la librería sendgrid; sin firma válida → 403.

- [ ] **Step 1: Escribir tests que fallan**

Crear `finanzas/test_cuenta_gastos_webhook.py`:

```python
import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from referencias.models import Referencia


def _notif(num='LCRR0500/26'):
    from finanzas.models import NotificacionCuentaGastos
    ref = Referencia.objects.create(num_refe=num, patente='1656', prefijo='LCRR')
    return NotificacionCuentaGastos.objects.create(
        referencia=ref, destinatario='c@x.com'
    )


class ProcesarEventoTests(TestCase):
    def setUp(self):
        self.notif = _notif()

    def _evento(self, tipo, **extra):
        return {'event': tipo, 'notificacion_cg_id': str(self.notif.pk),
                'timestamp': 1770000000, **extra}

    def _procesar(self, tipo, **extra):
        from finanzas.cuenta_gastos_envio import procesar_evento_sendgrid
        procesar_evento_sendgrid(self._evento(tipo, **extra))
        self.notif.refresh_from_db()

    def test_delivered_marca_entregado(self):
        self._procesar('delivered')
        self.assertEqual(self.notif.estado, 'ENTREGADO')
        self.assertIsNotNone(self.notif.entregado_en)

    def test_open_marca_leido(self):
        self._procesar('open')
        self.assertEqual(self.notif.estado, 'LEIDO')
        self.assertIsNotNone(self.notif.leido_en)

    def test_delivered_tardio_no_degrada_leido(self):
        self._procesar('open')
        self._procesar('delivered')
        self.assertEqual(self.notif.estado, 'LEIDO')
        self.assertIsNotNone(self.notif.entregado_en)  # timestamp sí se llena

    def test_bounce_marca_rebotado_con_razon(self):
        self._procesar('bounce', reason='mailbox unavailable')
        self.assertEqual(self.notif.estado, 'REBOTADO')
        self.assertIn('mailbox unavailable', self.notif.error_msg)

    def test_evento_sin_id_se_ignora(self):
        from finanzas.cuenta_gastos_envio import procesar_evento_sendgrid
        procesar_evento_sendgrid({'event': 'delivered'})  # no lanza
        self.notif.refresh_from_db()
        self.assertEqual(self.notif.estado, 'ENVIADO')

    def test_evento_desconocido_se_ignora(self):
        self._procesar('processed')
        self.assertEqual(self.notif.estado, 'ENVIADO')


@override_settings(SENDGRID_WEBHOOK_PUBLIC_KEY='clave-publica-test')
class WebhookViewTests(TestCase):
    def setUp(self):
        self.notif = _notif('LCRR0501/26')
        self.url = reverse('finanzas:sendgrid_webhook')
        self.payload = json.dumps([{
            'event': 'delivered',
            'notificacion_cg_id': str(self.notif.pk),
            'timestamp': 1770000000,
        }])

    def _post(self):
        return self.client.post(self.url, self.payload,
                                content_type='application/json')

    def test_firma_valida_procesa_eventos(self):
        with patch('finanzas.views_cuenta_gastos.EventWebhook') as ew:
            ew.return_value.verify_signature.return_value = True
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.notif.refresh_from_db()
        self.assertEqual(self.notif.estado, 'ENTREGADO')

    def test_firma_invalida_devuelve_403(self):
        with patch('finanzas.views_cuenta_gastos.EventWebhook') as ew:
            ew.return_value.verify_signature.return_value = False
            resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self.notif.refresh_from_db()
        self.assertEqual(self.notif.estado, 'ENVIADO')

    @override_settings(SENDGRID_WEBHOOK_PUBLIC_KEY='')
    def test_sin_clave_configurada_devuelve_403(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 403)

    def test_get_no_permitido(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)
```

- [ ] **Step 2: Correr y verificar fallo**

Run: `python manage.py test finanzas.test_cuenta_gastos_webhook -v 1`
Expected: ERROR (`ImportError: procesar_evento_sendgrid` / `NoReverseMatch`)

- [ ] **Step 3: Implementar**

En `hal9mil/settings.py`, junto a `SENDGRID_API_KEY`:

```python
# Llave pública del Signed Event Webhook de SendGrid (tracking de entregas)
SENDGRID_WEBHOOK_PUBLIC_KEY = os.getenv('SENDGRID_WEBHOOK_PUBLIC_KEY', '')
```

En `finanzas/cuenta_gastos_envio.py` — agregar al final (import `datetime`/`timezone` de Python arriba: `from datetime import date, datetime, timezone as dt_timezone` y `from django.utils import timezone`):

```python
_MAPEO_EVENTOS = {
    'delivered': 'ENTREGADO',
    'open': 'LEIDO',
    'bounce': 'REBOTADO',
    'dropped': 'REBOTADO',
}
_ORDEN_ESTADOS = {'ENVIADO': 1, 'ENTREGADO': 2, 'LEIDO': 3}


def procesar_evento_sendgrid(evento):
    """Aplica un evento del Event Webhook a su NotificacionCuentaGastos.

    Eventos sin notificacion_cg_id (otros correos de la cuenta SendGrid),
    con id inexistente o de tipo no mapeado se ignoran en silencio.
    Los estados solo avanzan; los timestamps se llenan aunque el evento
    llegue fuera de orden.
    """
    from .models import NotificacionCuentaGastos

    notif_id = evento.get('notificacion_cg_id')
    if not notif_id:
        return
    try:
        notif = NotificacionCuentaGastos.objects.get(pk=int(notif_id))
    except (NotificacionCuentaGastos.DoesNotExist, TypeError, ValueError):
        return

    nuevo = _MAPEO_EVENTOS.get(evento.get('event'))
    if not nuevo:
        return

    if evento.get('timestamp'):
        momento = datetime.fromtimestamp(evento['timestamp'], tz=dt_timezone.utc)
    else:
        momento = timezone.now()

    if nuevo == 'REBOTADO':
        notif.estado = 'REBOTADO'
        notif.error_msg = evento.get('reason', 'Correo rebotado')
    else:
        if nuevo == 'ENTREGADO' and notif.entregado_en is None:
            notif.entregado_en = momento
        if nuevo == 'LEIDO' and notif.leido_en is None:
            notif.leido_en = momento
        if (notif.estado in _ORDEN_ESTADOS
                and _ORDEN_ESTADOS[nuevo] > _ORDEN_ESTADOS[notif.estado]):
            notif.estado = nuevo
    notif.save()
```

En `finanzas/views_cuenta_gastos.py` — agregar imports y vista:

```python
import json

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt
from sendgrid.helpers.eventwebhook import EventWebhook, EventWebhookHeader

from .cuenta_gastos_envio import enviar_cuenta_gastos, procesar_evento_sendgrid
```

```python
@csrf_exempt
def sendgrid_webhook(request):
    """Recibe eventos del Event Webhook de SendGrid (firma obligatoria)."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    clave_publica = getattr(settings, 'SENDGRID_WEBHOOK_PUBLIC_KEY', '')
    if not clave_publica:
        return HttpResponseForbidden('Webhook no configurado.')

    verificador = EventWebhook()
    firma = request.headers.get(EventWebhookHeader.SIGNATURE, '')
    timestamp = request.headers.get(EventWebhookHeader.TIMESTAMP, '')
    llave = verificador.convert_public_key_to_ecdsa(clave_publica)
    if not verificador.verify_signature(
            request.body.decode('utf-8'), firma, timestamp, llave):
        return HttpResponseForbidden('Firma inválida.')

    try:
        eventos = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse(status=400)
    for evento in eventos:
        procesar_evento_sendgrid(evento)
    return HttpResponse(status=200)
```

**Nota para el test de firma:** al mockear `EventWebhook` completo, `convert_public_key_to_ecdsa` también queda mockeado — no hace falta una llave ECDSA real.

En `finanzas/urls.py`:

```python
    path('webhooks/sendgrid/', views_cuenta_gastos.sendgrid_webhook, name='sendgrid_webhook'),
```

- [ ] **Step 4: Correr tests**

Run: `python manage.py test finanzas.test_cuenta_gastos_webhook -v 1`
Expected: OK (10 tests)

- [ ] **Step 5: Commit**

```bash
git add finanzas/cuenta_gastos_envio.py finanzas/views_cuenta_gastos.py finanzas/urls.py hal9mil/settings.py finanzas/test_cuenta_gastos_webhook.py
git commit -m "feat(finanzas): webhook firmado de SendGrid para tracking entregado/leído"
```

---

### Task 10: Listado de notificaciones + descarga de ZIP + enlace en dashboard

**Files:**
- Modify: `finanzas/views_cuenta_gastos.py` (2 vistas)
- Modify: `finanzas/urls.py`
- Create: `templates/finanzas/notificaciones_cg.html`
- Modify: `templates/finanzas/dashboard.html` (tarjeta de acceso)
- Test: `finanzas/test_cuenta_gastos_envio.py` (agregar clase)

**Interfaces:**
- Consumes: `NotificacionCuentaGastos`, patrón de tarjetas de `templates/finanzas/dashboard.html:9-56`.
- Produces: URLs `finanzas:notificaciones_cg` (GET, filtros `estado` y `q`) y `finanzas:notificacion_cg_zip` (descarga).

- [ ] **Step 1: Escribir tests que fallan**

Agregar a `finanzas/test_cuenta_gastos_envio.py`:

```python
@override_settings(MEDIA_ROOT=MEDIA_TMP)
class NotificacionesListTests(TestCase):
    def setUp(self):
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.user = User.objects.create_user('lista_cg', password='x')
        self.user.groups.add(grupo)
        self.client.login(username='lista_cg', password='x')
        from finanzas.models import NotificacionCuentaGastos
        self.referencia = _referencia('LCRR0600/26')
        self.notif = NotificacionCuentaGastos.objects.create(
            referencia=self.referencia, destinatario='c@x.com',
            enviado_por=self.user, estado='ENTREGADO',
        )
        self.url = reverse('finanzas:notificaciones_cg')

    def test_lista_muestra_notificacion(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'c@x.com')
        self.assertContains(resp, 'LCRR0600/26')
        self.assertContains(resp, 'Entregado')

    def test_filtro_por_estado(self):
        resp = self.client.get(self.url, {'estado': 'LEIDO'})
        self.assertNotContains(resp, 'c@x.com')

    def test_busqueda_por_referencia(self):
        resp = self.client.get(self.url, {'q': 'LCRR0600'})
        self.assertContains(resp, 'c@x.com')
        resp = self.client.get(self.url, {'q': 'NOEXISTE'})
        self.assertNotContains(resp, 'c@x.com')

    def test_descarga_zip(self):
        from django.core.files.base import ContentFile
        self.notif.zip_file.save('CG_test.zip', ContentFile(b'PK\x05\x06zipdata'))
        url = reverse('finanzas:notificacion_cg_zip', kwargs={'pk': self.notif.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/zip')

    def test_zip_inexistente_404(self):
        url = reverse('finanzas:notificacion_cg_zip', kwargs={'pk': self.notif.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_requiere_modulo_finanzas(self):
        User.objects.create_user('sin_modulo', password='x')
        self.client.login(username='sin_modulo', password='x')
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
```

- [ ] **Step 2: Correr y verificar fallo**

Run: `python manage.py test finanzas.test_cuenta_gastos_envio.NotificacionesListTests -v 1`
Expected: ERROR `NoReverseMatch: 'notificaciones_cg'`

- [ ] **Step 3: Implementar vistas**

En `finanzas/views_cuenta_gastos.py` — agregar imports (`os`, `Http404`, `FileResponse`, `render`, `Q`, `NotificacionCuentaGastos`):

```python
import os

from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import render

from .models import CierreCuentaGastos, NotificacionCuentaGastos
```

y las vistas:

```python
@modulo_required('Finanzas')
def notificaciones_cg_list(request):
    qs = (NotificacionCuentaGastos.objects
          .select_related('referencia', 'enviado_por')
          .order_by('-enviado_en'))
    estado = request.GET.get('estado', '')
    if estado:
        qs = qs.filter(estado=estado)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(referencia__num_refe__icontains=q)
            | Q(referencia__nombre_cliente__icontains=q)
            | Q(destinatario__icontains=q)
        )
    return render(request, 'finanzas/notificaciones_cg.html', {
        'notificaciones': qs[:200],
        'estado_filtro': estado,
        'q': q,
        'estados': NotificacionCuentaGastos.ESTADOS,
    })


@modulo_required('Finanzas')
def notificacion_cg_zip(request, pk):
    notif = get_object_or_404(NotificacionCuentaGastos, pk=pk)
    if not notif.zip_file:
        raise Http404('Esta notificación no tiene ZIP guardado.')
    return FileResponse(
        notif.zip_file.open('rb'), content_type='application/zip',
        as_attachment=True, filename=os.path.basename(notif.zip_file.name),
    )
```

En `finanzas/urls.py`:

```python
    path('notificaciones-cg/', views_cuenta_gastos.notificaciones_cg_list, name='notificaciones_cg'),
    path('notificaciones-cg/<int:pk>/zip/', views_cuenta_gastos.notificacion_cg_zip, name='notificacion_cg_zip'),
```

- [ ] **Step 4: Crear template**

Crear `templates/finanzas/notificaciones_cg.html`:

```html
{% extends 'base.html' %}
{% block title %}Notificaciones de Cuenta de Gastos{% endblock %}
{% block content %}
<div class="p-6 space-y-6">

  <div class="flex items-center justify-between">
    <div>
      <p class="text-xs text-slate-500 uppercase tracking-wider">Finanzas</p>
      <h1 class="text-2xl font-bold text-slate-800">Notificaciones de cuenta de gastos</h1>
      <p class="text-slate-500 text-sm mt-0.5">Envíos al cliente con su estado de entrega y lectura.</p>
    </div>
  </div>

  {% for msg in messages %}
  <div class="bg-green-50 border border-green-200 rounded-xl px-4 py-3 text-sm text-green-800">{{ msg }}</div>
  {% endfor %}

  <!-- Filtros -->
  <form method="get" class="flex flex-wrap items-end gap-3">
    <div>
      <label class="block text-xs text-slate-500 mb-1">Estado</label>
      <select name="estado" class="border border-slate-200 rounded-lg px-3 py-1.5 text-sm">
        <option value="">Todos</option>
        {% for valor, etiqueta in estados %}
        <option value="{{ valor }}" {% if estado_filtro == valor %}selected{% endif %}>{{ etiqueta }}</option>
        {% endfor %}
      </select>
    </div>
    <div>
      <label class="block text-xs text-slate-500 mb-1">Buscar</label>
      <input type="text" name="q" value="{{ q }}" placeholder="Referencia, cliente o correo"
             class="border border-slate-200 rounded-lg px-3 py-1.5 text-sm w-64">
    </div>
    <button type="submit"
            class="bg-slate-600 hover:bg-slate-700 text-white text-sm font-medium px-4 py-2 rounded-lg">
      Filtrar
    </button>
  </form>

  <!-- Tabla -->
  <div class="bg-white rounded-xl border border-slate-200 overflow-hidden">
    {% if notificaciones %}
    <table class="w-full text-sm">
      <thead class="bg-slate-50 text-xs text-slate-500 uppercase">
        <tr>
          <th class="px-5 py-2 text-left">Referencia</th>
          <th class="px-5 py-2 text-left">Cliente</th>
          <th class="px-5 py-2 text-left">Destinatario</th>
          <th class="px-5 py-2 text-left">Enviado por</th>
          <th class="px-5 py-2 text-left">Fecha</th>
          <th class="px-5 py-2 text-left">Estado</th>
          <th class="px-5 py-2 text-left">Acciones</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-100">
        {% for n in notificaciones %}
        <tr class="hover:bg-slate-50">
          <td class="px-5 py-2.5">
            <a href="{% url 'finanzas:referencia_estado' num_refe=n.referencia.num_refe %}"
               class="text-sky-600 hover:underline font-mono">{{ n.referencia.num_refe }}</a>
            {% if n.es_reenvio %}<span class="text-xs text-slate-400 ml-1">(reenvío)</span>{% endif %}
          </td>
          <td class="px-5 py-2.5 text-slate-600">{{ n.referencia.nombre_cliente|default:"—" }}</td>
          <td class="px-5 py-2.5 font-mono text-xs">{{ n.destinatario }}</td>
          <td class="px-5 py-2.5 text-slate-500 text-xs">{{ n.enviado_por|default:"—" }}</td>
          <td class="px-5 py-2.5 text-slate-600">{{ n.enviado_en|date:"d/m/Y H:i" }}</td>
          <td class="px-5 py-2.5">
            <span class="px-2 py-0.5 rounded-full text-xs
              {% if n.estado == 'LEIDO' %}bg-green-100 text-green-700
              {% elif n.estado == 'ENTREGADO' %}bg-sky-100 text-sky-700
              {% elif n.estado == 'ENVIADO' %}bg-slate-100 text-slate-600
              {% else %}bg-red-100 text-red-700{% endif %}"
              {% if n.error_msg %}title="{{ n.error_msg }}"{% endif %}>
              {{ n.get_estado_display }}
            </span>
          </td>
          <td class="px-5 py-2.5 space-x-3 whitespace-nowrap">
            {% if n.zip_file %}
            <a href="{% url 'finanzas:notificacion_cg_zip' n.pk %}"
               class="text-sky-600 hover:underline text-xs">ZIP</a>
            {% endif %}
            <a href="{% url 'finanzas:referencia_estado' num_refe=n.referencia.num_refe %}"
               class="text-indigo-600 hover:underline text-xs">Reenviar</a>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <p class="px-5 py-10 text-center text-slate-400 text-sm">Sin notificaciones registradas</p>
    {% endif %}
  </div>

</div>
{% endblock %}
```

(El "Reenviar" enlaza al estado financiero, donde vive el formulario de reenvío con destinatario ad-hoc — una sola fuente de verdad para el envío.)

En `templates/finanzas/dashboard.html`, después de la tarjeta de `xml_pendientes` (línea ~56), agregar:

```html
    <a href="{% url 'finanzas:notificaciones_cg' %}"
       class="bg-white rounded-xl border border-slate-200 p-5 hover:border-indigo-300 hover:shadow-sm transition-all">
      <p class="text-xs text-slate-500 uppercase tracking-wider mb-1">Cuentas de gastos</p>
      <p class="text-sm font-semibold text-slate-700">Notificaciones enviadas</p>
    </a>
```

- [ ] **Step 5: Correr tests**

Run: `python manage.py test finanzas.test_cuenta_gastos_envio -v 1`
Expected: OK

- [ ] **Step 6: Commit**

```bash
git add finanzas/views_cuenta_gastos.py finanzas/urls.py templates/finanzas/notificaciones_cg.html templates/finanzas/dashboard.html finanzas/test_cuenta_gastos_envio.py
git commit -m "feat(finanzas): listado de notificaciones de cuenta de gastos con descarga de ZIP"
```

---

### Task 11: Verificación final y documentación de deploy

**Files:**
- Modify: `docs/superpowers/specs/2026-07-13-envio-cuenta-gastos-design.md` (solo si se detectan desviaciones)

**Interfaces:** N/A — verificación.

- [ ] **Step 1: Suite completa del proyecto**

Run: `python manage.py test finanzas clientes -v 1 && python manage.py check`
Expected: OK todos los tests, check sin errores.

- [ ] **Step 2: Verificación manual del flujo (runserver local)**

```bash
ALLOWED_HOSTS="127.0.0.1,localhost" python manage.py runserver
```

Con un usuario del grupo Finanzas, en una referencia con anticipos/gastos/XMLs:
1. Ver botón "Cerrar cuenta de gastos" → cerrarla → verificar banner, botones ocultos y balanza visible.
2. Verificar formulario de envío con destinatario prellenado.
3. (Sin SendGrid real) Confirmar que un POST de envío registra la notificación — si `SENDGRID_API_KEY` es inválida quedará en `ERROR` visible en el listado, comportamiento correcto.
4. Como superusuario, verificar botón "Reabrir cuenta" y que reabre.
5. Visitar `/finanzas/notificaciones-cg/` y verificar tabla y filtros.

- [ ] **Step 3: Checklist de deploy (manual, en DigitalOcean/SendGrid — NO automatizable)**

Documentar en el mensaje final al usuario:
1. En App Platform: no se requieren env vars nuevas para enviar (usa `SENDGRID_API_KEY` existente).
2. En SendGrid → Settings → Mail Settings → Event Webhook: URL `https://<dominio>/finanzas/webhooks/sendgrid/`, marcar eventos **Delivered, Opened, Bounced, Dropped**, habilitar **Signed Event Webhook** y copiar la Verification Key.
3. En App Platform: agregar env var `SENDGRID_WEBHOOK_PUBLIC_KEY` con esa key.
4. En SendGrid → Settings → Tracking: habilitar **Open Tracking**.
5. Capturar `email_cuenta_gastos` de los clientes en el admin de Django.

- [ ] **Step 4: Commit final (si hubo ajustes) y resumen**

```bash
git status   # confirmar árbol limpio o commitear ajustes de la verificación
```

---

## Self-Review (ejecutada al escribir el plan)

- **Cobertura del spec:** cierre independiente (T1/T3/T7), correos con fallback (T2/T4), ZIP único (T4/T6), balanza en correo y pantalla (T5/T8), envío con custom_args (T6), reenvío ad-hoc con ZIP reutilizado (T6/T7/T8), webhook firmado con estados que solo avanzan (T9), listado con filtros y descarga (T10), reapertura solo superusuario (T7/T8), "Emitir factura" intacto (T8), config de deploy documentada (T11). Sin gaps.
- **Nota vs. spec:** el spec decía "dependencia nueva: sendgrid" — ya está en `requirements.txt` (`sendgrid>=6.11`), no se requiere cambio.
- **Consistencia de tipos/nombres:** `CierreCuentaGastos.activo_para`, `destinatarios_cliente`, `construir_zip_cuenta_gastos`, `contexto_balanza`, `enviar_cuenta_gastos`, `procesar_evento_sendgrid` usados con la misma firma en todas las tasks; URL names `cerrar_cg`, `reabrir_cg`, `enviar_cg`, `notificaciones_cg`, `notificacion_cg_zip`, `sendgrid_webhook` consistentes entre urls.py, vistas, templates y tests.
