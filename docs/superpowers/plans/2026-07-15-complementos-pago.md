# Complementos de Pago (CFDI tipo P) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dejar de tratar los Complementos de Pago (CFDI `TipoDeComprobante='P'`) como facturas pendientes sin datos aduanales, y en su lugar ligarlos automáticamente (o a mano como respaldo) con la factura (`XMLProveedor`) que pagan, mostrando esa relación en la fila de la factura.

**Architecture:** Nuevo modelo `ComplementoPago` (FK opcional a `XMLProveedor`). Nuevo módulo de servicio `finanzas/complementos_pago.py` con la lógica de ligado (automático al subir, y retroactivo cuando la factura llega después). Los 3 puntos de entrada de XML (`carga_xml.py` para carga masiva y "Carga de Facturas", y `views.subir_xml_proveedor` para carga individual) despachan a este módulo cuando detectan tipo `P` en vez de crear un `XMLProveedor`.

**Tech Stack:** Django 5.2, `xml.etree.ElementTree` (stdlib) para parseo CFDI, SQLite efímero para tests (`DBURL='sqlite:///tmp_test_db.sqlite3'`), Postgres de producción sin tocar durante desarrollo/pruebas.

## Global Constraints

- Todo el código y comentarios nuevos van en español, consistente con el resto del proyecto.
- TDD estricto: escribir la prueba, verla fallar, implementar lo mínimo, verla pasar, commit.
- Las pruebas se corren con `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas` (nunca contra la Postgres de producción) y se borra el archivo `tmp_test_db.sqlite3` al terminar cada corrida.
- No modificar `XMLProveedor` ni `extractores.py` (la cascada de coincidencia aduanal sigue aplicando solo a facturas I/E).
- `ComplementoPago.factura` es `ForeignKey` (no `OneToOne`): una factura puede acumular varios complementos (parcialidades).
- Spec de referencia: `docs/superpowers/specs/2026-07-15-complementos-pago-design.md`.

---

### Task 1: Modelo `ComplementoPago` y migración

**Files:**
- Modify: `finanzas/models.py:227` (insertar clase nueva justo antes de `class GastoReferencia`)
- Create: `finanzas/migrations/0014_complementopago.py` (generada con `makemigrations`, no escrita a mano)
- Test: `finanzas/test_complementos_pago.py` (nuevo archivo)

**Interfaces:**
- Produce: `finanzas.models.ComplementoPago` con campos `factura` (FK a `XMLProveedor`, `related_name='complementos_pago'`), `uuid_complemento`, `uuid_factura_relacionada`, `fecha_emision`, `rfc_emisor`, `nombre_emisor`, `monto_pagado`, `moneda_pago`, `estado` (`PENDIENTE`/`IDENTIFICADO`/`REVISION`), `xml_file`, `pdf_file`, `referencia_sugerida` (FK a `referencias.Referencia`), `cargado_en`.

- [ ] **Step 1: Escribir la prueba que falla**

Crear `finanzas/test_complementos_pago.py`:

```python
import tempfile
from datetime import datetime
from decimal import Decimal

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from referencias.models import Referencia

from .models import ComplementoPago, XMLProveedor

MEDIA_TMP = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ComplementoPagoModelTests(TestCase):
    def test_crea_complemento_pendiente_sin_factura(self):
        c = ComplementoPago.objects.create(
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            uuid_factura_relacionada='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('11094.00'),
        )
        c.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=True)
        self.assertEqual(c.estado, 'PENDIENTE')
        self.assertIsNone(c.factura)
        self.assertEqual(c.moneda_pago, 'MXN')

    def test_liga_a_una_factura_existente(self):
        referencia = Referencia.objects.create(
            num_refe='LCRR0900/26', patente='1656', prefijo='LCRR',
        )
        factura = XMLProveedor(
            referencia=referencia,
            uuid_fiscal='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 8, 8, 0, 0),
            rfc_emisor='LCT030408U39', nombre_emisor='L C TERMINAL',
            rfc_receptor='CIN220216BS2',
            subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
            tipo_comprobante='I',
        )
        factura.xml_file.save('f.xml', ContentFile(b'<x/>'), save=False)
        factura.save()

        c = ComplementoPago.objects.create(
            factura=factura,
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            uuid_factura_relacionada=factura.uuid_fiscal,
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'),
            estado='IDENTIFICADO',
        )
        c.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=True)
        self.assertEqual(factura.complementos_pago.count(), 1)
        self.assertEqual(factura.complementos_pago.first(), c)

    def test_uuid_complemento_es_unico(self):
        ComplementoPago.objects.create(
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'),
        )
        with self.assertRaises(Exception):
            ComplementoPago.objects.create(
                uuid_complemento='44444444-4444-4444-4444-444444444444',
                fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
                rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
                monto_pagado=Decimal('50.00'),
            )
```

- [ ] **Step 2: Verificar que falla**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago -v2`
Expected: `ImportError: cannot import name 'ComplementoPago' from 'finanzas.models'`

- [ ] **Step 3: Implementar el modelo mínimo**

En `finanzas/models.py`, insertar esta clase entre el final de `XMLProveedor` (línea 227, justo antes de `class GastoReferencia(models.Model):`):

```python
class ComplementoPago(models.Model):
    """Complemento de Pago (CFDI tipo P): no es una factura, es la prueba de
    que una factura (XMLProveedor) ya fue pagada. Su <DoctoRelacionado> trae
    el UUID de esa factura."""
    ESTADO = [
        ('PENDIENTE', 'Pendiente'),        # no se encontró la factura aún
        ('IDENTIFICADO', 'Identificado'),  # ligado a una factura
        ('REVISION', 'Requiere revisión'), # trae más de un DoctoRelacionado
    ]
    factura = models.ForeignKey(
        XMLProveedor, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='complementos_pago'
    )
    uuid_complemento = models.UUIDField(unique=True)
    uuid_factura_relacionada = models.UUIDField(null=True, blank=True)
    fecha_emision = models.DateTimeField()
    rfc_emisor = models.CharField(max_length=13)
    nombre_emisor = models.CharField(max_length=200)
    monto_pagado = models.DecimalField(max_digits=14, decimal_places=2)
    moneda_pago = models.CharField(max_length=3, default='MXN')
    estado = models.CharField(max_length=12, choices=ESTADO, default='PENDIENTE')
    xml_file = models.FileField(storage=media_storage, upload_to='complementos_pago/%Y/%m/')
    pdf_file = models.FileField(
        storage=media_storage, upload_to='complementos_pago/%Y/%m/',
        null=True, blank=True,
    )
    referencia_sugerida = models.ForeignKey(
        'referencias.Referencia', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='complementos_pago_sugeridos',
    )
    cargado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-cargado_en']
        verbose_name = 'Complemento de Pago'
        verbose_name_plural = 'Complementos de Pago'

    def __str__(self):
        return f'{self.rfc_emisor} | pago {self.monto_pagado} | {self.estado}'
```

- [ ] **Step 4: Generar y aplicar la migración**

Run: `.venv/bin/python manage.py makemigrations finanzas`
Expected: `Migrations for 'finanzas': finanzas/migrations/0014_complementopago.py - Create model ComplementoPago`

- [ ] **Step 5: Verificar que la prueba pasa**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago -v2`
Expected: `Ran 3 tests ... OK`

- [ ] **Step 6: Commit**

```bash
git add finanzas/models.py finanzas/migrations/0014_complementopago.py finanzas/test_complementos_pago.py
git commit -m "feat(finanzas): modelo ComplementoPago para CFDI tipo P"
```

---

### Task 2: Parseo del Complemento de Pago

**Files:**
- Modify: `finanzas/cfdi_parser.py`
- Modify: `finanzas/cfdi_de_prueba.py`
- Test: `finanzas/test_complementos_pago.py` (agregar clase)

**Interfaces:**
- Consume: nada de tareas anteriores.
- Produce: `finanzas.cfdi_parser.parsear_complemento_pago(root) -> list[dict]`, cada dict con claves `uuid_factura` (str), `imp_pagado` (Decimal), `moneda_pago` (str). Lanza `ValueError` si no hay nodo `Pagos` o ningún `DoctoRelacionado`. También produce `finanzas.cfdi_de_prueba.cfdi_pago(...)` (fixture para pruebas).

- [ ] **Step 1: Escribir la prueba que falla**

Agregar a `finanzas/test_complementos_pago.py`:

```python
import xml.etree.ElementTree as ET

from .cfdi_de_prueba import cfdi_pago
from .cfdi_parser import parsear_complemento_pago


class ParsearComplementoPagoTests(TestCase):
    def test_extrae_un_docto_relacionado(self):
        root = ET.fromstring(cfdi_pago(
            uuid_factura='11111111-1111-1111-1111-111111111111',
            monto='11094.00', moneda='MXN',
        ))
        doctos = parsear_complemento_pago(root)
        self.assertEqual(len(doctos), 1)
        self.assertEqual(doctos[0]['uuid_factura'], '11111111-1111-1111-1111-111111111111')
        self.assertEqual(doctos[0]['imp_pagado'], Decimal('11094.00'))
        self.assertEqual(doctos[0]['moneda_pago'], 'MXN')

    def test_extrae_varios_doctos_relacionados(self):
        root = ET.fromstring(cfdi_pago(
            uuid_factura='11111111-1111-1111-1111-111111111111',
            uuids_factura_extra=['22222222-2222-2222-2222-222222222222'],
        ))
        doctos = parsear_complemento_pago(root)
        self.assertEqual(len(doctos), 2)

    def test_sin_nodo_pagos_lanza_valueerror(self):
        root = ET.fromstring(
            '<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" '
            'Version="4.0" Fecha="2026-07-10T12:00:00" TipoDeComprobante="P" '
            'Total="0" Moneda="XXX"><cfdi:Emisor Rfc="AAA010101AAA" '
            'Nombre="X"/><cfdi:Receptor Rfc="BBB010101BBB" Nombre="Y" '
            'UsoCFDI="CP01"/></cfdi:Comprobante>'
        )
        with self.assertRaises(ValueError):
            parsear_complemento_pago(root)
```

- [ ] **Step 2: Verificar que falla**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago.ParsearComplementoPagoTests -v2`
Expected: `ImportError: cannot import name 'cfdi_pago' from 'finanzas.cfdi_de_prueba'`

- [ ] **Step 3: Agregar el fixture `cfdi_pago` a `cfdi_de_prueba.py`**

Al final de `finanzas/cfdi_de_prueba.py`, agregar:

```python
_PLANTILLA_PAGO = '''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
    Version="4.0" Fecha="2026-07-10T12:00:00" Moneda="XXX" Total="0"
    TipoDeComprobante="P" LugarExpedicion="06600">
  <cfdi:Emisor Rfc="{rfc_emisor}" Nombre="{nombre_emisor}" RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="{rfc_receptor}" Nombre="{nombre_receptor}" UsoCFDI="CP01" DomicilioFiscalReceptor="06600" RegimenFiscalReceptor="601"/>
  <cfdi:Conceptos>
    <cfdi:Concepto ClaveProdServ="84111506" Cantidad="1" ClaveUnidad="ACT" Descripcion="Pago" ValorUnitario="0" Importe="0" ObjetoImp="01"/>
  </cfdi:Conceptos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="1.1" UUID="{uuid}" FechaTimbrado="2026-07-10T12:00:05"/>
    <pago20:Pagos xmlns:pago20="http://www.sat.gob.mx/Pagos20" Version="2.0">
      <pago20:Totales MontoTotalPagos="{monto}"/>
      <pago20:Pago FechaPago="2026-07-10T12:00:00" FormaDePagoP="03" MonedaP="{moneda}" Monto="{monto}">
        {doctos}
      </pago20:Pago>
    </pago20:Pagos>
  </cfdi:Complemento>
</cfdi:Comprobante>'''

_PLANTILLA_DOCTO = (
    '<pago20:DoctoRelacionado IdDocumento="{uuid_factura}" MonedaDR="{moneda}" '
    'NumParcialidad="1" ImpSaldoAnt="{monto}" ImpPagado="{monto}" ImpSaldoInsoluto="0"/>'
)


def cfdi_pago(uuid='44444444-4444-4444-4444-444444444444',
              uuid_factura='11111111-1111-1111-1111-111111111111',
              rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
              rfc_receptor='LCT030408U39', nombre_receptor='L C TERMINAL',
              monto='11094.00', moneda='MXN', uuids_factura_extra=None):
    """Complemento de pago (CFDI 4.0, pago20). `uuids_factura_extra` agrega
    DoctoRelacionado adicionales, para simular el caso de revisión manual."""
    doctos = _PLANTILLA_DOCTO.format(uuid_factura=uuid_factura, moneda=moneda, monto=monto)
    for extra in (uuids_factura_extra or []):
        doctos += _PLANTILLA_DOCTO.format(uuid_factura=extra, moneda=moneda, monto=monto)
    return _PLANTILLA_PAGO.format(
        uuid=uuid, rfc_emisor=rfc_emisor, nombre_emisor=nombre_emisor,
        rfc_receptor=rfc_receptor, nombre_receptor=nombre_receptor,
        monto=monto, moneda=moneda, doctos=doctos,
    ).encode('utf-8')
```

- [ ] **Step 4: Verificar que sigue fallando (ahora por falta de `parsear_complemento_pago`)**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago.ParsearComplementoPagoTests -v2`
Expected: `ImportError: cannot import name 'parsear_complemento_pago' from 'finanzas.cfdi_parser'`

- [ ] **Step 5: Implementar `parsear_complemento_pago`**

En `finanzas/cfdi_parser.py`, agregar las constantes de namespace y refactorizar la detección de namespace a un helper compartido. Reemplazar:

```python
NS_CFDI4 = 'http://www.sat.gob.mx/cfd/4'
NS_CFDI3 = 'http://www.sat.gob.mx/cfd/3'
NS_TFD   = 'http://www.sat.gob.mx/TimbreFiscalDigital'
```

por:

```python
NS_CFDI4  = 'http://www.sat.gob.mx/cfd/4'
NS_CFDI3  = 'http://www.sat.gob.mx/cfd/3'
NS_TFD    = 'http://www.sat.gob.mx/TimbreFiscalDigital'
NS_PAGO20 = 'http://www.sat.gob.mx/Pagos20'
NS_PAGO10 = 'http://www.sat.gob.mx/Pagos'


def _detectar_ns(root) -> str:
    if NS_CFDI4 in root.tag:
        return NS_CFDI4
    if NS_CFDI3 in root.tag:
        return NS_CFDI3
    raise ValueError('El archivo no es un CFDI válido (namespace no reconocido)')
```

Dentro de `parsear_cfdi_root`, reemplazar:

```python
    # Detectar versión por namespace del elemento raíz
    if NS_CFDI4 in root.tag:
        ns = NS_CFDI4
    elif NS_CFDI3 in root.tag:
        ns = NS_CFDI3
    else:
        raise ValueError('El archivo no es un CFDI válido (namespace no reconocido)')
```

por:

```python
    ns = _detectar_ns(root)
```

Y agregar al final del archivo:

```python
def parsear_complemento_pago(root) -> list:
    """
    Extrae los DoctoRelacionado de un Complemento de Pago (CFDI tipo P).
    Retorna lista de dicts: {'uuid_factura', 'imp_pagado' (Decimal), 'moneda_pago'}.
    Soporta pago20 (CFDI 4.0) y pago10 (CFDI 3.3). Lanza ValueError si no
    encuentra el nodo Pagos o ningún DoctoRelacionado.
    """
    ns = _detectar_ns(root)
    nsmap = {'cfdi': ns, 'pago20': NS_PAGO20, 'pago10': NS_PAGO10}

    pagos = root.find('cfdi:Complemento/pago20:Pagos', nsmap)
    prefijo = 'pago20'
    if pagos is None:
        pagos = root.find('cfdi:Complemento/pago10:Pagos', nsmap)
        prefijo = 'pago10'
    if pagos is None:
        raise ValueError('Complemento de pago sin nodo Pagos')

    doctos = []
    for pago in pagos.findall(f'{prefijo}:Pago', nsmap):
        for docto in pago.findall(f'{prefijo}:DoctoRelacionado', nsmap):
            doctos.append({
                'uuid_factura': (docto.get('IdDocumento') or '').strip(),
                'imp_pagado': _decimal(docto.get('ImpPagado', '0')),
                'moneda_pago': docto.get('MonedaDR', 'MXN'),
            })
    if not doctos:
        raise ValueError('Complemento de pago sin DoctoRelacionado')
    return doctos
```

- [ ] **Step 6: Verificar que pasa**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago -v2`
Expected: `Ran 6 tests ... OK`

- [ ] **Step 7: Commit**

```bash
git add finanzas/cfdi_parser.py finanzas/cfdi_de_prueba.py finanzas/test_complementos_pago.py
git commit -m "feat(finanzas): parseo de DoctoRelacionado en complementos de pago (pago10/pago20)"
```

---

### Task 3: Módulo de servicio `finanzas/complementos_pago.py`

**Files:**
- Create: `finanzas/complementos_pago.py`
- Test: `finanzas/test_complementos_pago.py` (agregar clase)

**Interfaces:**
- Consume: `finanzas.cfdi_parser.parsear_complemento_pago` (Task 2), `finanzas.models.ComplementoPago`, `finanzas.models.XMLProveedor` (Task 1).
- Produce:
  - `procesar_complemento(root, *, uuid_complemento, fecha, rfc_emisor, nombre_emisor, nombre_archivo, xml_bytes, pdf_bytes=None, referencia_sugerida=None) -> ComplementoPago`
  - `conciliar_pendientes(xml_obj) -> None` — liga en bloque los `ComplementoPago` `PENDIENTE` cuyo `uuid_factura_relacionada` coincide con `xml_obj.uuid_fiscal`.

- [ ] **Step 1: Escribir la prueba que falla**

Agregar a `finanzas/test_complementos_pago.py`:

```python
import xml.etree.ElementTree as ET

from django.core.files.uploadedfile import SimpleUploadedFile


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ProcesarComplementoTests(TestCase):
    def test_liga_de_inmediato_si_la_factura_ya_existe(self):
        from .complementos_pago import procesar_complemento

        factura = XMLProveedor(
            uuid_fiscal='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 8, 8, 0, 0),
            rfc_emisor='LCT030408U39', nombre_emisor='L C TERMINAL',
            rfc_receptor='CIN220216BS2',
            subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
            tipo_comprobante='I',
        )
        factura.xml_file.save('f.xml', ContentFile(b'<x/>'), save=False)
        factura.save()

        xml_bytes = cfdi_pago(uuid_factura=str(factura.uuid_fiscal))
        root = ET.fromstring(xml_bytes)
        complemento = procesar_complemento(
            root, uuid_complemento='44444444-4444-4444-4444-444444444444',
            fecha=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            nombre_archivo='pago.xml', xml_bytes=xml_bytes,
        )
        self.assertEqual(complemento.estado, 'IDENTIFICADO')
        self.assertEqual(complemento.factura, factura)

    def test_queda_pendiente_si_no_existe_la_factura(self):
        from .complementos_pago import procesar_complemento

        xml_bytes = cfdi_pago(uuid_factura='99999999-9999-9999-9999-999999999999')
        root = ET.fromstring(xml_bytes)
        complemento = procesar_complemento(
            root, uuid_complemento='44444444-4444-4444-4444-444444444444',
            fecha=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            nombre_archivo='pago.xml', xml_bytes=xml_bytes,
        )
        self.assertEqual(complemento.estado, 'PENDIENTE')
        self.assertIsNone(complemento.factura)
        self.assertEqual(
            str(complemento.uuid_factura_relacionada),
            '99999999-9999-9999-9999-999999999999',
        )

    def test_varios_doctos_relacionados_queda_en_revision(self):
        from .complementos_pago import procesar_complemento

        xml_bytes = cfdi_pago(
            uuid_factura='11111111-1111-1111-1111-111111111111',
            uuids_factura_extra=['22222222-2222-2222-2222-222222222222'],
        )
        root = ET.fromstring(xml_bytes)
        complemento = procesar_complemento(
            root, uuid_complemento='44444444-4444-4444-4444-444444444444',
            fecha=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            nombre_archivo='pago.xml', xml_bytes=xml_bytes,
        )
        self.assertEqual(complemento.estado, 'REVISION')

    def test_adjunta_pdf_si_se_provee(self):
        from .complementos_pago import procesar_complemento

        xml_bytes = cfdi_pago(uuid_factura='99999999-9999-9999-9999-999999999999')
        root = ET.fromstring(xml_bytes)
        complemento = procesar_complemento(
            root, uuid_complemento='44444444-4444-4444-4444-444444444444',
            fecha=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            nombre_archivo='pago.xml', xml_bytes=xml_bytes, pdf_bytes=b'%PDF-1.4',
        )
        self.assertTrue(complemento.pdf_file)


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ConciliarPendientesTests(TestCase):
    def test_liga_complemento_pendiente_cuando_llega_la_factura(self):
        from .complementos_pago import conciliar_pendientes

        pendiente = ComplementoPago.objects.create(
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            uuid_factura_relacionada='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'),
        )
        pendiente.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=True)

        factura = XMLProveedor(
            uuid_fiscal='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 8, 8, 0, 0),
            rfc_emisor='LCT030408U39', nombre_emisor='L C TERMINAL',
            rfc_receptor='CIN220216BS2',
            subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
            tipo_comprobante='I',
        )
        factura.xml_file.save('f.xml', ContentFile(b'<x/>'), save=False)
        factura.save()

        conciliar_pendientes(factura)

        pendiente.refresh_from_db()
        self.assertEqual(pendiente.estado, 'IDENTIFICADO')
        self.assertEqual(pendiente.factura, factura)

    def test_no_toca_complementos_ya_identificados(self):
        from .complementos_pago import conciliar_pendientes

        otra_factura = XMLProveedor(
            uuid_fiscal='33333333-3333-3333-3333-333333333333',
            fecha_emision=datetime(2026, 7, 8, 8, 0, 0),
            rfc_emisor='LCT030408U39', nombre_emisor='L C TERMINAL',
            rfc_receptor='CIN220216BS2',
            subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
            tipo_comprobante='I',
        )
        otra_factura.xml_file.save('f.xml', ContentFile(b'<x/>'), save=False)
        otra_factura.save()

        ya_ligado = ComplementoPago.objects.create(
            factura=otra_factura,
            uuid_complemento='55555555-5555-5555-5555-555555555555',
            uuid_factura_relacionada='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'), estado='IDENTIFICADO',
        )
        ya_ligado.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=True)

        factura_nueva = XMLProveedor(
            uuid_fiscal='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 8, 8, 0, 0),
            rfc_emisor='LCT030408U39', nombre_emisor='L C TERMINAL',
            rfc_receptor='CIN220216BS2',
            subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
            tipo_comprobante='I',
        )
        factura_nueva.xml_file.save('f2.xml', ContentFile(b'<x/>'), save=False)
        factura_nueva.save()

        conciliar_pendientes(factura_nueva)

        ya_ligado.refresh_from_db()
        self.assertEqual(ya_ligado.factura, otra_factura)
```

- [ ] **Step 2: Verificar que falla**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago.ProcesarComplementoTests finanzas.test_complementos_pago.ConciliarPendientesTests -v2`
Expected: `ModuleNotFoundError: No module named 'finanzas.complementos_pago'`

- [ ] **Step 3: Implementar el módulo**

Crear `finanzas/complementos_pago.py`:

```python
"""Complementos de Pago (CFDI tipo P): ligado con la factura que pagan.

Un complemento de pago no es una factura; su <DoctoRelacionado> trae el UUID
de la factura (XMLProveedor) que fue pagada. Ver spec:
docs/superpowers/specs/2026-07-15-complementos-pago-design.md
"""
from django.core.files.base import ContentFile

from .cfdi_parser import parsear_complemento_pago
from .models import ComplementoPago, XMLProveedor


def procesar_complemento(root, *, uuid_complemento, fecha, rfc_emisor,
                          nombre_emisor, nombre_archivo, xml_bytes,
                          pdf_bytes=None, referencia_sugerida=None):
    """Crea el ComplementoPago a partir de un CFDI ya identificado como tipo P.

    Retorna el ComplementoPago creado (estado PENDIENTE, IDENTIFICADO o
    REVISION). Lanza ValueError si el complemento no trae DoctoRelacionado
    (propagada desde parsear_complemento_pago).
    """
    doctos = parsear_complemento_pago(root)
    primero = doctos[0]

    estado = 'REVISION' if len(doctos) > 1 else 'PENDIENTE'
    factura = None
    if estado == 'PENDIENTE':
        factura = XMLProveedor.objects.filter(
            uuid_fiscal=primero['uuid_factura']
        ).first()
        if factura:
            estado = 'IDENTIFICADO'

    complemento = ComplementoPago(
        factura=factura,
        uuid_complemento=uuid_complemento,
        uuid_factura_relacionada=primero['uuid_factura'],
        fecha_emision=fecha,
        rfc_emisor=rfc_emisor,
        nombre_emisor=nombre_emisor,
        monto_pagado=primero['imp_pagado'],
        moneda_pago=primero['moneda_pago'],
        estado=estado,
        referencia_sugerida=referencia_sugerida,
    )
    complemento.xml_file.save(nombre_archivo, ContentFile(xml_bytes), save=False)
    if pdf_bytes:
        stem = nombre_archivo.rsplit('.', 1)[0]
        complemento.pdf_file.save(f'{stem}.pdf', ContentFile(pdf_bytes), save=False)
    complemento.save()
    return complemento


def conciliar_pendientes(xml_obj):
    """Liga automáticamente los ComplementoPago PENDIENTES que esperaban esta
    factura (por UUID). Se llama tras guardar cualquier XMLProveedor nuevo."""
    ComplementoPago.objects.filter(
        estado='PENDIENTE', uuid_factura_relacionada=xml_obj.uuid_fiscal,
    ).update(factura=xml_obj, estado='IDENTIFICADO')
```

- [ ] **Step 4: Verificar que pasa**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago -v2`
Expected: `Ran 12 tests ... OK`

- [ ] **Step 5: Commit**

```bash
git add finanzas/complementos_pago.py finanzas/test_complementos_pago.py
git commit -m "feat(finanzas): ligado automático y retroactivo de complementos de pago"
```

---

### Task 4: Integrar en carga masiva y "Carga de Facturas" (`carga_xml.py`)

**Files:**
- Modify: `finanzas/carga_xml.py`
- Test: `finanzas/test_complementos_pago.py` (agregar clase)

**Interfaces:**
- Consume: `procesar_complemento`, `conciliar_pendientes` (Task 3).
- Produce: `_procesar_uno` ahora despacha CFDI tipo `P` a `_procesar_complemento_lote`, que retorna `ResultadoArchivo` con `estado` en `{'COMPLEMENTO_LIGADO', 'COMPLEMENTO_PENDIENTE'}` además de los ya existentes.

- [ ] **Step 1: Escribir la prueba que falla**

Agregar a `finanzas/test_complementos_pago.py`:

```python
from .carga_xml import procesar_lote


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ProcesarLoteComplementosTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user('fin', password='x')

    def test_complemento_no_crea_xmlproveedor(self):
        xml_bytes = cfdi_pago(uuid_factura='99999999-9999-9999-9999-999999999999')
        resultados = procesar_lote([('pago.xml', xml_bytes)], self.usuario)
        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0].estado, 'COMPLEMENTO_PENDIENTE')
        self.assertEqual(XMLProveedor.objects.count(), 0)
        self.assertEqual(ComplementoPago.objects.count(), 1)

    def test_complemento_liga_si_la_factura_ya_existe(self):
        factura = XMLProveedor(
            uuid_fiscal='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 8, 8, 0, 0),
            rfc_emisor='LCT030408U39', nombre_emisor='L C TERMINAL',
            rfc_receptor='CIN220216BS2',
            subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
            tipo_comprobante='I',
        )
        factura.xml_file.save('f.xml', ContentFile(b'<x/>'), save=False)
        factura.save()

        xml_bytes = cfdi_pago(uuid_factura=str(factura.uuid_fiscal))
        resultados = procesar_lote([('pago.xml', xml_bytes)], self.usuario)
        self.assertEqual(resultados[0].estado, 'COMPLEMENTO_LIGADO')
        complemento = ComplementoPago.objects.get()
        self.assertEqual(complemento.factura, factura)

    def test_complemento_duplicado_se_reporta(self):
        xml_bytes = cfdi_pago(uuid='44444444-4444-4444-4444-444444444444')
        procesar_lote([('pago.xml', xml_bytes)], self.usuario)
        resultados = procesar_lote([('pago2.xml', xml_bytes)], self.usuario)
        self.assertEqual(resultados[0].estado, 'DUPLICADO')

    def test_factura_nueva_liga_complemento_pendiente_existente(self):
        pendiente = ComplementoPago.objects.create(
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            uuid_factura_relacionada='99999999-9999-9999-9999-999999999999',
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'),
        )
        pendiente.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=True)

        # RFC no soportado por los extractores → factura queda sin referencia,
        # pero conciliar_pendientes debe correr de todos modos.
        xml_bytes = cfdi_cliente(uuid='99999999-9999-9999-9999-999999999999')
        procesar_lote([('factura.xml', xml_bytes)], self.usuario)

        pendiente.refresh_from_db()
        self.assertEqual(pendiente.estado, 'IDENTIFICADO')
```

Agregar los imports que falten al encabezado de `finanzas/test_complementos_pago.py`:

```python
from django.contrib.auth.models import User

from .cfdi_de_prueba import cfdi_cliente, cfdi_pago
```

- [ ] **Step 2: Verificar que falla**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago.ProcesarLoteComplementosTests -v2`
Expected: `AttributeError` o `AssertionError` — hoy `procesar_lote` crea un `XMLProveedor` tipo `P` en vez de un `ComplementoPago` (el conteo de `XMLProveedor` sale en 1, no 0).

- [ ] **Step 3: Implementar el branching en `carga_xml.py`**

En `finanzas/carga_xml.py`, agregar el import:

```python
from .complementos_pago import conciliar_pendientes, procesar_complemento
from .models import ComplementoPago, GastoReferencia, XMLProveedor
```

(reemplaza la línea `from .models import GastoReferencia, XMLProveedor` existente).

Reemplazar la función `_procesar_uno` completa por:

```python
def _procesar_uno(item, usuario):
    nombre = item['nombre']
    try:
        root = ET.fromstring(item['xml'])
        datos = parsear_cfdi_root(root)
    except (ET.ParseError, ValueError) as e:
        return ResultadoArchivo(nombre, 'ERROR', detalle=str(e))

    if datos['tipo'] == 'P':
        return _procesar_complemento_lote(item, root, datos)

    if XMLProveedor.objects.filter(uuid_fiscal=datos['uuid']).exists():
        return ResultadoArchivo(
            nombre, 'DUPLICADO', detalle=f'UUID {datos["uuid"]} ya registrado'
        )

    referencia, motivo = buscar_referencia(extraer_datos_aduanales(root))

    xml_obj = XMLProveedor(
        referencia=referencia,
        uuid_fiscal=datos['uuid'],
        fecha_emision=datos['fecha'],
        rfc_emisor=datos['rfc_emisor'],
        nombre_emisor=datos['nombre_emisor'],
        rfc_receptor=datos['rfc_receptor'],
        subtotal=datos['subtotal'],
        iva=datos['iva'],
        total=datos['total'],
        moneda=datos['moneda'],
        tipo_comprobante=datos['tipo'],
        concepto_principal=datos['concepto_principal'],
        estado_asignacion='ASIGNADO' if referencia else 'PENDIENTE',
        motivo_pendiente='' if referencia else motivo,
    )
    xml_obj.xml_file.save(nombre, ContentFile(item['xml']), save=False)
    if item['pdf']:
        xml_obj.pdf_file.save(
            item['stem'] + '.pdf', ContentFile(item['pdf']), save=False
        )
    xml_obj.save()
    conciliar_pendientes(xml_obj)

    if referencia is None:
        return ResultadoArchivo(nombre, 'PENDIENTE', detalle=motivo)
    # Solo los comprobantes de Ingreso generan gasto (E = nota de crédito)
    if datos['tipo'] == 'I':
        crear_gasto_desde_xml(xml_obj, usuario)
    return ResultadoArchivo(nombre, 'ASIGNADO', referencia=referencia)


def _procesar_complemento_lote(item, root, datos):
    nombre = item['nombre']
    if ComplementoPago.objects.filter(uuid_complemento=datos['uuid']).exists():
        return ResultadoArchivo(
            nombre, 'DUPLICADO', detalle=f'Complemento {datos["uuid"]} ya registrado'
        )
    try:
        complemento = procesar_complemento(
            root, uuid_complemento=datos['uuid'], fecha=datos['fecha'],
            rfc_emisor=datos['rfc_emisor'], nombre_emisor=datos['nombre_emisor'],
            nombre_archivo=nombre, xml_bytes=item['xml'], pdf_bytes=item['pdf'],
        )
    except ValueError as e:
        return ResultadoArchivo(nombre, 'ERROR', detalle=str(e))

    if complemento.estado == 'IDENTIFICADO':
        return ResultadoArchivo(
            nombre, 'COMPLEMENTO_LIGADO',
            referencia=complemento.factura.referencia,
            detalle=f'liga con factura UUID {complemento.factura.uuid_fiscal}',
        )
    return ResultadoArchivo(
        nombre, 'COMPLEMENTO_PENDIENTE',
        detalle=f'esperando factura UUID {complemento.uuid_factura_relacionada}',
    )
```

Agregar el import de `xml.etree.ElementTree` si no está ya (ya existe como `import xml.etree.ElementTree as ET` en la parte superior del archivo — no requiere cambio).

- [ ] **Step 4: Verificar que pasa**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago -v2`
Expected: `Ran 16 tests ... OK`

- [ ] **Step 5: Correr toda la suite de finanzas para descartar regresiones**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas`
Expected: `OK` (sin fallas nuevas; `test_carga_masiva.py` y `test_carga_cliente.py` deben seguir en verde).

- [ ] **Step 6: Commit**

```bash
git add finanzas/carga_xml.py finanzas/test_complementos_pago.py
git commit -m "feat(finanzas): carga masiva y Carga de Facturas ligan complementos de pago en vez de crear XMLProveedor"
```

---

### Task 5: Integrar en la carga individual (`views.subir_xml_proveedor`)

**Files:**
- Modify: `finanzas/views.py:1-22` (imports) y `finanzas/views.py:196-264` (la función `subir_xml_proveedor`)
- Modify: `templates/finanzas/referencia_estado.html:130-152` (agregar campo PDF)
- Test: `finanzas/test_complementos_pago.py` (agregar clase)

**Interfaces:**
- Consume: `procesar_complemento`, `conciliar_pendientes` (Task 3).
- Produce: `subir_xml_proveedor` acepta `request.FILES['pdf_file']` opcional y, si el XML es tipo `P`, crea un `ComplementoPago` con `referencia_sugerida` en vez de un `XMLProveedor`.

- [ ] **Step 1: Escribir la prueba que falla**

Agregar a `finanzas/test_complementos_pago.py`:

```python
from django.urls import reverse


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class SubirXmlProveedorComplementoTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.user = User.objects.create_user('subecg', password='x')
        self.user.groups.add(grupo)
        self.client.login(username='subecg', password='x')
        self.referencia = Referencia.objects.create(
            num_refe='LCRR0901/26', patente='1656', prefijo='LCRR',
        )
        self.url = reverse('finanzas:subir_xml', kwargs={'num_refe': self.referencia.num_refe})

    def test_complemento_no_crea_xmlproveedor_y_queda_pendiente(self):
        xml_bytes = cfdi_pago(uuid_factura='99999999-9999-9999-9999-999999999999')
        resp = self.client.post(self.url, {
            'xml_file': SimpleUploadedFile('pago.xml', xml_bytes),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(XMLProveedor.objects.count(), 0)
        complemento = ComplementoPago.objects.get()
        self.assertEqual(complemento.estado, 'PENDIENTE')
        self.assertEqual(complemento.referencia_sugerida, self.referencia)

    def test_complemento_con_pdf_opcional(self):
        xml_bytes = cfdi_pago(uuid_factura='99999999-9999-9999-9999-999999999999')
        resp = self.client.post(self.url, {
            'xml_file': SimpleUploadedFile('pago.xml', xml_bytes),
            'pdf_file': SimpleUploadedFile('pago.pdf', b'%PDF-1.4'),
        })
        self.assertEqual(resp.status_code, 302)
        complemento = ComplementoPago.objects.get()
        self.assertTrue(complemento.pdf_file)

    def test_factura_normal_sigue_funcionando_con_pdf_opcional(self):
        xml_bytes = cfdi_cliente(uuid='55555555-5555-5555-5555-555555555555')
        resp = self.client.post(self.url, {
            'xml_file': SimpleUploadedFile('factura.xml', xml_bytes),
            'pdf_file': SimpleUploadedFile('factura.pdf', b'%PDF-1.4'),
        })
        self.assertEqual(resp.status_code, 302)
        xml_obj = XMLProveedor.objects.get()
        self.assertTrue(xml_obj.pdf_file)
        self.assertEqual(xml_obj.referencia, self.referencia)
```

- [ ] **Step 2: Verificar que falla**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago.SubirXmlProveedorComplementoTests -v2`
Expected: falla porque hoy `subir_xml_proveedor` no acepta `pdf_file` ni distingue tipo `P` (crea un `XMLProveedor` con `tipo_comprobante='P'`, así que `XMLProveedor.objects.count()` da 1, no 0).

- [ ] **Step 3: Actualizar imports de `views.py`**

Reemplazar las líneas 1-4 y 17 de `finanzas/views.py`:

```python
import json
import os
import tempfile
import zipfile
```

por:

```python
import json
import xml.etree.ElementTree as ET
import zipfile
```

Reemplazar:

```python
from .cfdi_parser import parsear_cfdi
```

por:

```python
from .cfdi_parser import parsear_cfdi_root
from .complementos_pago import conciliar_pendientes, procesar_complemento
from django.core.files.base import ContentFile
```

Y en el bloque `from .models import (...)`, agregar `ComplementoPago` a la lista de nombres importados.

- [ ] **Step 4: Reescribir `subir_xml_proveedor`**

Reemplazar la función completa (líneas 196-264 de `finanzas/views.py`) por:

```python
@modulo_required('Finanzas')
def subir_xml_proveedor(request, num_refe):
    referencia = get_object_or_404(Referencia, num_refe=num_refe)
    from .models import CierreCuentaGastos
    if CierreCuentaGastos.activo_para(referencia):
        messages.error(request, 'La cuenta de gastos está cerrada; no se pueden registrar movimientos.')
        return redirect('finanzas:referencia_estado', num_refe=num_refe)
    if request.method != 'POST':
        return redirect('finanzas:referencia_estado', num_refe=num_refe)

    xml_file = request.FILES.get('xml_file')
    pdf_file = request.FILES.get('pdf_file')
    if not xml_file:
        messages.error(request, 'No se seleccionó ningún archivo XML.')
        return redirect('finanzas:referencia_estado', num_refe=num_refe)

    if not xml_file.name.lower().endswith('.xml'):
        messages.error(request, 'El archivo debe tener extensión .xml')
        return redirect('finanzas:referencia_estado', num_refe=num_refe)

    xml_bytes = xml_file.read()
    try:
        root = ET.fromstring(xml_bytes)
        datos = parsear_cfdi_root(root)
    except (ET.ParseError, ValueError) as e:
        messages.error(request, f'Error al leer el XML: {e}')
        return redirect('finanzas:referencia_estado', num_refe=num_refe)

    if datos['tipo'] == 'P':
        if ComplementoPago.objects.filter(uuid_complemento=datos['uuid']).exists():
            messages.error(request, 'Este complemento de pago ya fue registrado (UUID duplicado).')
            return redirect('finanzas:referencia_estado', num_refe=num_refe)
        try:
            complemento = procesar_complemento(
                root, uuid_complemento=datos['uuid'], fecha=datos['fecha'],
                rfc_emisor=datos['rfc_emisor'], nombre_emisor=datos['nombre_emisor'],
                nombre_archivo=xml_file.name, xml_bytes=xml_bytes,
                pdf_bytes=pdf_file.read() if pdf_file else None,
                referencia_sugerida=referencia,
            )
        except ValueError as e:
            messages.error(request, f'Error al leer el complemento de pago: {e}')
            return redirect('finanzas:referencia_estado', num_refe=num_refe)
        if complemento.estado == 'IDENTIFICADO':
            messages.success(
                request,
                f'Complemento de pago ligado a la factura {complemento.factura.uuid_fiscal}.'
            )
        else:
            messages.warning(
                request,
                'Complemento de pago cargado; no se encontró la factura relacionada '
                '(quedó pendiente de ligar).'
            )
        return redirect('finanzas:referencia_estado', num_refe=num_refe)

    # Verificar UUID único
    if XMLProveedor.objects.filter(uuid_fiscal=datos['uuid']).exists():
        messages.error(request, 'Este XML ya fue registrado (UUID duplicado).')
        return redirect('finanzas:referencia_estado', num_refe=num_refe)

    xml_obj = XMLProveedor.objects.create(
        referencia=referencia,
        uuid_fiscal=datos['uuid'],
        fecha_emision=datos['fecha'],
        rfc_emisor=datos['rfc_emisor'],
        nombre_emisor=datos['nombre_emisor'],
        rfc_receptor=datos['rfc_receptor'],
        subtotal=datos['subtotal'],
        iva=datos['iva'],
        total=datos['total'],
        moneda=datos['moneda'],
        tipo_comprobante=datos['tipo'],
        concepto_principal=datos['concepto_principal'],
        xml_file=ContentFile(xml_bytes, name=xml_file.name),
        estado_asignacion='ASIGNADO',
    )
    if pdf_file:
        xml_obj.pdf_file.save(pdf_file.name, ContentFile(pdf_file.read()), save=True)

    conciliar_pendientes(xml_obj)

    # Crear GastoReferencia automático si el usuario lo solicitó y el XML es tipo Ingreso
    if request.POST.get('crear_gasto') == '1' and datos['tipo'] == 'I':
        gasto = crear_gasto_desde_xml(xml_obj, request.user, tipo='OTROS')
        messages.success(
            request,
            f'XML cargado · Gasto ${datos["total"]} registrado · Póliza {gasto.poliza.numero} generada.'
        )
    else:
        messages.success(request, f'XML cargado correctamente. UUID: {datos["uuid"]}')

    return redirect('finanzas:referencia_estado', num_refe=num_refe)
```

- [ ] **Step 5: Agregar el campo de PDF opcional al formulario**

En `templates/finanzas/referencia_estado.html`, reemplazar:

```html
      <div>
        <label class="block text-xs text-slate-500 mb-1">Archivo XML</label>
        <input type="file" name="xml_file" accept=".xml" required
               class="border border-slate-200 rounded-lg px-3 py-1.5 text-sm text-slate-700
                      file:mr-3 file:py-1 file:px-3 file:rounded-md file:border-0
                      file:bg-sky-50 file:text-sky-700 file:text-xs file:font-medium
                      hover:file:bg-sky-100 cursor-pointer">
      </div>
```

por:

```html
      <div>
        <label class="block text-xs text-slate-500 mb-1">Archivo XML</label>
        <input type="file" name="xml_file" accept=".xml" required
               class="border border-slate-200 rounded-lg px-3 py-1.5 text-sm text-slate-700
                      file:mr-3 file:py-1 file:px-3 file:rounded-md file:border-0
                      file:bg-sky-50 file:text-sky-700 file:text-xs file:font-medium
                      hover:file:bg-sky-100 cursor-pointer">
      </div>
      <div>
        <label class="block text-xs text-slate-500 mb-1">PDF (opcional)</label>
        <input type="file" name="pdf_file" accept=".pdf"
               class="border border-slate-200 rounded-lg px-3 py-1.5 text-sm text-slate-700
                      file:mr-3 file:py-1 file:px-3 file:rounded-md file:border-0
                      file:bg-sky-50 file:text-sky-700 file:text-xs file:font-medium
                      hover:file:bg-sky-100 cursor-pointer">
      </div>
```

- [ ] **Step 6: Verificar que pasa**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago finanzas.test_cuenta_gastos_cierre -v2`
Expected: `OK` (incluye `test_subir_xml_bloqueado_con_cierre`, que no debe romperse).

- [ ] **Step 7: Correr toda la suite de finanzas**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas`
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add finanzas/views.py finanzas/test_complementos_pago.py templates/finanzas/referencia_estado.html
git commit -m "feat(finanzas): carga individual de XML acepta PDF opcional y liga complementos de pago"
```

---

### Task 6: Vista y plantilla "Complementos de pago no identificados"

**Files:**
- Modify: `finanzas/views.py` (nueva vista, junto a `xml_pendientes`)
- Modify: `finanzas/urls.py`
- Create: `templates/finanzas/complementos_pago_pendientes.html`
- Test: `finanzas/test_complementos_pago.py` (agregar clase)

**Interfaces:**
- Consume: `finanzas.models.ComplementoPago`, `finanzas.models.XMLProveedor`.
- Produce: vista `complementos_pago_pendientes` (URL `finanzas:complementos_pago_pendientes`), vista `complemento_pago_ver_pdf` (URL `finanzas:complemento_pago_ver_pdf`).

- [ ] **Step 1: Escribir la prueba que falla**

Agregar a `finanzas/test_complementos_pago.py`:

```python
@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ComplementosPagoPendientesViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.user = User.objects.create_user('verpend', password='x')
        self.user.groups.add(grupo)
        self.client.login(username='verpend', password='x')
        self.referencia = Referencia.objects.create(
            num_refe='LCRR0902/26', patente='1656', prefijo='LCRR',
        )
        self.pendiente = ComplementoPago.objects.create(
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            uuid_factura_relacionada='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'),
        )
        self.pendiente.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=True)
        self.url = reverse('finanzas:complementos_pago_pendientes')

    def test_lista_muestra_pendiente(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'CACIPA INTERNACIONAL')

    def test_ligar_manualmente_por_num_refe_y_uuid(self):
        factura = XMLProveedor(
            referencia=self.referencia,
            uuid_fiscal='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 8, 8, 0, 0),
            rfc_emisor='LCT030408U39', nombre_emisor='L C TERMINAL',
            rfc_receptor='CIN220216BS2',
            subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
            tipo_comprobante='I',
        )
        factura.xml_file.save('f.xml', ContentFile(b'<x/>'), save=False)
        factura.save()

        resp = self.client.post(self.url, {
            'complemento_id': self.pendiente.pk,
            'num_refe': self.referencia.num_refe,
        })
        self.assertEqual(resp.status_code, 302)
        self.pendiente.refresh_from_db()
        self.assertEqual(self.pendiente.estado, 'IDENTIFICADO')
        self.assertEqual(self.pendiente.factura, factura)

    def test_ligar_con_referencia_incorrecta_no_liga(self):
        otra_ref = Referencia.objects.create(
            num_refe='LCRR0903/26', patente='1656', prefijo='LCRR',
        )
        resp = self.client.post(self.url, {
            'complemento_id': self.pendiente.pk,
            'num_refe': otra_ref.num_refe,
        })
        self.assertEqual(resp.status_code, 302)
        self.pendiente.refresh_from_db()
        self.assertEqual(self.pendiente.estado, 'PENDIENTE')

    def test_requiere_modulo_finanzas(self):
        User.objects.create_user('sinmodulo', password='x')
        self.client.logout()
        self.client.login(username='sinmodulo', password='x')
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ComplementoPagoVerPdfViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.user = User.objects.create_user('verpdf', password='x')
        self.user.groups.add(grupo)
        self.client.login(username='verpdf', password='x')
        self.complemento = ComplementoPago.objects.create(
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'),
        )
        self.complemento.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=False)
        self.complemento.pdf_file.save('pago.pdf', ContentFile(b'%PDF-1.4'), save=True)

    def test_descarga_pdf(self):
        url = reverse('finanzas:complemento_pago_ver_pdf', kwargs={'pk': self.complemento.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')

    def test_404_si_no_tiene_pdf(self):
        self.complemento.pdf_file.delete(save=True)
        url = reverse('finanzas:complemento_pago_ver_pdf', kwargs={'pk': self.complemento.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)
```

- [ ] **Step 2: Verificar que falla**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago.ComplementosPagoPendientesViewTests finanzas.test_complementos_pago.ComplementoPagoVerPdfViewTests -v2`
Expected: `NoReverseMatch: Reverse for 'complementos_pago_pendientes' not found`

- [ ] **Step 3: Agregar las vistas**

En `finanzas/views.py`, justo después de la función `xml_pendientes` (antes de `xml_proveedor_ver_pdf`), agregar:

```python
@modulo_required('Finanzas')
def complementos_pago_pendientes(request):
    if request.method == 'POST':
        complemento = get_object_or_404(
            ComplementoPago,
            pk=request.POST.get('complemento_id'),
            estado__in=['PENDIENTE', 'REVISION'],
        )
        num_refe = request.POST.get('num_refe', '').strip()
        factura = XMLProveedor.objects.filter(
            referencia__num_refe=num_refe,
            uuid_fiscal=complemento.uuid_factura_relacionada,
        ).first()
        if factura is None:
            messages.error(
                request,
                f'No se encontró en "{num_refe}" ninguna factura con UUID '
                f'{complemento.uuid_factura_relacionada}.'
            )
        else:
            complemento.factura = factura
            complemento.estado = 'IDENTIFICADO'
            complemento.save(update_fields=['factura', 'estado'])
            messages.success(request, f'Complemento ligado a la factura {factura.uuid_fiscal}.')
        return redirect('finanzas:complementos_pago_pendientes')

    pendientes = list(
        ComplementoPago.objects
        .filter(estado__in=['PENDIENTE', 'REVISION'])
        .select_related('referencia_sugerida')
        .order_by('-cargado_en')
    )
    return render(request, 'finanzas/complementos_pago_pendientes.html', {
        'pendientes': pendientes,
    })


@modulo_required('Finanzas')
def complemento_pago_ver_pdf(request, pk):
    complemento = get_object_or_404(ComplementoPago, pk=pk)
    if not complemento.pdf_file:
        raise Http404('Este complemento no tiene un PDF asociado.')
    return FileResponse(complemento.pdf_file.open('rb'), content_type='application/pdf')
```

- [ ] **Step 4: Agregar las URLs**

En `finanzas/urls.py`, agregar después de la línea `path('xml/pendientes/', views.xml_pendientes, name='xml_pendientes'),`:

```python
    path('xml/complementos-pendientes/', views.complementos_pago_pendientes, name='complementos_pago_pendientes'),
    path('complemento-pago/<int:pk>/pdf/', views.complemento_pago_ver_pdf, name='complemento_pago_ver_pdf'),
```

- [ ] **Step 5: Crear la plantilla**

Crear `templates/finanzas/complementos_pago_pendientes.html`:

```html
{% extends 'base.html' %}
{% block title %}Complementos de pago no identificados · Finanzas{% endblock %}
{% block content %}
<div class="p-6 max-w-6xl">

  <div class="mb-6 flex items-start justify-between gap-4 flex-wrap">
    <div>
      <div class="flex items-center gap-4 text-sm">
        <a href="{% url 'finanzas:xml_pendientes' %}" class="text-sky-600 hover:underline">XMLs pendientes</a>
      </div>
      <h1 class="text-2xl font-bold text-slate-800 mt-2">Complementos de pago no identificados</h1>
      <p class="text-slate-500 text-sm mt-0.5">
        Escribe el número de referencia donde está la factura que este complemento paga.
      </p>
    </div>
    {% if pendientes %}
    <span class="inline-flex items-center gap-1.5 bg-amber-50 text-amber-700 text-xs font-medium px-3 py-1.5 rounded-full border border-amber-200 shrink-0">
      {{ pendientes|length }} pendiente{{ pendientes|length|pluralize }}
    </span>
    {% endif %}
  </div>

  {% if pendientes %}
  <div class="bg-white rounded-xl border border-slate-200 overflow-hidden">
    <table class="w-full text-sm">
      <thead class="bg-slate-50 text-left text-xs text-slate-500 uppercase tracking-wider">
        <tr>
          <th class="px-4 py-3">Emisor</th>
          <th class="px-4 py-3">Monto pagado</th>
          <th class="px-4 py-3">Fecha</th>
          <th class="px-4 py-3">UUID factura buscada</th>
          <th class="px-4 py-3">Estado</th>
          <th class="px-4 py-3">PDF</th>
          <th class="px-4 py-3">Ligar a referencia</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-100">
        {% for c in pendientes %}
        <tr class="hover:bg-slate-50 align-top">
          <td class="px-4 py-3">
            <p class="font-medium text-slate-800">{{ c.nombre_emisor }}</p>
            <p class="text-xs text-slate-400 font-mono">{{ c.rfc_emisor }}</p>
            {% if c.referencia_sugerida %}
            <p class="text-xs text-slate-500 mt-0.5">Sugerida: {{ c.referencia_sugerida.num_refe }}</p>
            {% endif %}
          </td>
          <td class="px-4 py-3 font-mono">${{ c.monto_pagado|floatformat:2 }} {{ c.moneda_pago }}</td>
          <td class="px-4 py-3 text-slate-600">{{ c.fecha_emision|date:'d/m/Y' }}</td>
          <td class="px-4 py-3 font-mono text-xs text-slate-500 break-all">{{ c.uuid_factura_relacionada|default:"—" }}</td>
          <td class="px-4 py-3">
            {% if c.estado == 'REVISION' %}
            <span class="bg-red-50 text-red-700 text-xs px-2 py-1 rounded-full">Requiere revisión</span>
            {% else %}
            <span class="bg-amber-50 text-amber-700 text-xs px-2 py-1 rounded-full">Pendiente</span>
            {% endif %}
          </td>
          <td class="px-4 py-3">
            {% if c.pdf_file %}
            <a href="{% url 'finanzas:complemento_pago_ver_pdf' c.pk %}" target="_blank"
               class="text-sky-600 hover:underline text-xs">Ver PDF</a>
            {% else %}
            <span class="text-slate-300 text-xs">—</span>
            {% endif %}
          </td>
          <td class="px-4 py-3">
            <form method="post" class="flex gap-2">
              {% csrf_token %}
              <input type="hidden" name="complemento_id" value="{{ c.pk }}">
              <input type="text" name="num_refe" placeholder="LCRR0000/26" required
                     class="flex-1 min-w-0 border border-slate-300 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-sky-500 focus:border-sky-500">
              <button type="submit"
                      class="bg-slate-700 hover:bg-slate-800 text-white px-3 py-1.5 rounded-lg text-xs font-medium transition-colors shrink-0 cursor-pointer">
                Ligar
              </button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="bg-white rounded-xl border border-slate-200 p-10 text-center">
    <p class="text-slate-500 text-sm">No hay complementos de pago pendientes de identificar.</p>
  </div>
  {% endif %}

</div>
{% endblock %}
```

- [ ] **Step 6: Verificar que pasa**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago -v2`
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add finanzas/views.py finanzas/urls.py templates/finanzas/complementos_pago_pendientes.html finanzas/test_complementos_pago.py
git commit -m "feat(finanzas): vista de complementos de pago no identificados con ligado manual"
```

---

### Task 7: Fila fusionada en `referencia_estado.html`

**Files:**
- Modify: `finanzas/views.py:88-97` (prefetch en `referencia_estado_financiero`)
- Modify: `templates/finanzas/referencia_estado.html:168-198`
- Test: `finanzas/test_complementos_pago.py` (agregar clase)

**Interfaces:**
- Consume: `xml.complementos_pago` (related_name de Task 1), `finanzas:complemento_pago_ver_pdf` (Task 6).

- [ ] **Step 1: Escribir la prueba que falla**

Agregar a `finanzas/test_complementos_pago.py`:

```python
@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ReferenciaEstadoFilaFusionadaTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.user = User.objects.create_user('verestado', password='x')
        self.user.groups.add(grupo)
        self.client.login(username='verestado', password='x')
        self.referencia = Referencia.objects.create(
            num_refe='LCRR0904/26', patente='1656', prefijo='LCRR',
        )
        self.factura = XMLProveedor(
            referencia=self.referencia,
            uuid_fiscal='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 8, 8, 0, 0),
            rfc_emisor='LCT030408U39', nombre_emisor='L C TERMINAL',
            rfc_receptor='CIN220216BS2',
            subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
            tipo_comprobante='I',
        )
        self.factura.xml_file.save('f.xml', ContentFile(b'<x/>'), save=False)
        self.factura.save()
        self.url = reverse('finanzas:referencia_estado', kwargs={'num_refe': self.referencia.num_refe})

    def test_sin_complemento_muestra_tipo_normal(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, '>I<')
        self.assertNotContains(resp, 'COM. PAGO')

    def test_con_complemento_ligado_muestra_com_pago(self):
        complemento = ComplementoPago.objects.create(
            factura=self.factura,
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            uuid_factura_relacionada=self.factura.uuid_fiscal,
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'), estado='IDENTIFICADO',
        )
        complemento.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=False)
        complemento.pdf_file.save('pago.pdf', ContentFile(b'%PDF-1.4'), save=True)

        resp = self.client.get(self.url)
        self.assertContains(resp, 'COM. PAGO')
        self.assertContains(resp, 'Ver PDF pago')
```

- [ ] **Step 2: Verificar que falla**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago.ReferenciaEstadoFilaFusionadaTests -v2`
Expected: `test_con_complemento_ligado_muestra_com_pago` falla — la plantilla hoy siempre muestra `tipo_comprobante` y nunca "COM. PAGO"/"Ver PDF pago".

- [ ] **Step 3: Prefetch en la vista**

En `finanzas/views.py`, en `referencia_estado_financiero`, reemplazar:

```python
    referencia = get_object_or_404(Referencia, num_refe=num_refe)
```

por:

```python
    referencia = get_object_or_404(
        Referencia.objects.prefetch_related('xmls_proveedor__complementos_pago'),
        num_refe=num_refe,
    )
```

- [ ] **Step 4: Actualizar la plantilla**

En `templates/finanzas/referencia_estado.html`, reemplazar el bloque de la fila (líneas 168-198):

```html
          {% for xml in referencia.xmls_proveedor.all %}
          <tr>
            <td class="py-1.5 pr-4 font-mono">{{ xml.rfc_emisor }}<br>
              <span class="text-slate-400 font-sans">{{ xml.nombre_emisor }}</span></td>
            <td class="py-1.5 pr-4 text-slate-600 max-w-xs truncate">{{ xml.concepto_principal|default:"—" }}</td>
            <td class="py-1.5 pr-4 text-right font-mono text-slate-800">${{ xml.total|floatformat:2 }} {{ xml.moneda }}</td>
            <td class="py-1.5 pr-4">
              <span class="px-1.5 py-0.5 rounded
                {% if xml.tipo_comprobante == 'I' %}bg-green-100 text-green-700
                {% elif xml.tipo_comprobante == 'E' %}bg-red-100 text-red-700
                {% else %}bg-slate-100 text-slate-600{% endif %}">
                {{ xml.tipo_comprobante }}
              </span>
            </td>
            <td class="py-1.5 pr-4 text-center">
              {% if xml.procesado %}
              <span class="text-green-600">✓</span>
              {% else %}
              <span class="text-slate-300">—</span>
              {% endif %}
            </td>
            <td class="py-1.5">
              {% if xml.pdf_file %}
              <a href="{% url 'finanzas:xml_proveedor_ver_pdf' xml.pk %}" target="_blank"
                 class="text-sky-600 hover:underline">Ver PDF</a>
              {% else %}
              <span class="text-slate-300">—</span>
              {% endif %}
            </td>
          </tr>
          {% endfor %}
```

por:

```html
          {% for xml in referencia.xmls_proveedor.all %}
          {% with complementos=xml.complementos_pago.all %}
          <tr>
            <td class="py-1.5 pr-4 font-mono">{{ xml.rfc_emisor }}<br>
              <span class="text-slate-400 font-sans">{{ xml.nombre_emisor }}</span></td>
            <td class="py-1.5 pr-4 text-slate-600 max-w-xs truncate">{{ xml.concepto_principal|default:"—" }}</td>
            <td class="py-1.5 pr-4 text-right font-mono text-slate-800">${{ xml.total|floatformat:2 }} {{ xml.moneda }}</td>
            <td class="py-1.5 pr-4">
              {% if complementos %}
              <span class="px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700">COM. PAGO</span>
              {% else %}
              <span class="px-1.5 py-0.5 rounded
                {% if xml.tipo_comprobante == 'I' %}bg-green-100 text-green-700
                {% elif xml.tipo_comprobante == 'E' %}bg-red-100 text-red-700
                {% else %}bg-slate-100 text-slate-600{% endif %}">
                {{ xml.tipo_comprobante }}
              </span>
              {% endif %}
            </td>
            <td class="py-1.5 pr-4 text-center">
              {% if complementos %}
              <span class="px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 text-[11px]">COM. PAGO</span>
              {% elif xml.procesado %}
              <span class="text-green-600">✓</span>
              {% else %}
              <span class="text-slate-300">—</span>
              {% endif %}
            </td>
            <td class="py-1.5">
              {% if xml.pdf_file %}
              <a href="{% url 'finanzas:xml_proveedor_ver_pdf' xml.pk %}" target="_blank"
                 class="text-sky-600 hover:underline">Ver PDF</a>
              {% else %}
              <span class="text-slate-300">—</span>
              {% endif %}
              {% for c in complementos %}
              {% if c.pdf_file %}
              <br><a href="{% url 'finanzas:complemento_pago_ver_pdf' c.pk %}" target="_blank"
                 class="text-indigo-600 hover:underline text-xs">Ver PDF pago</a>
              {% endif %}
              {% endfor %}
            </td>
          </tr>
          {% endwith %}
          {% endfor %}
```

- [ ] **Step 5: Verificar que pasa**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago -v2`
Expected: `OK`

- [ ] **Step 6: Correr toda la suite de finanzas**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas`
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add finanzas/views.py templates/finanzas/referencia_estado.html finanzas/test_complementos_pago.py
git commit -m "feat(finanzas): fila fusionada COM. PAGO en referencia_estado.html cuando hay complemento ligado"
```

---

### Task 8: Enlaces de navegación y conteos en resultado de carga masiva

**Files:**
- Modify: `finanzas/views.py:1121-1131` (`_procesar_subida_xml`)
- Modify: `templates/finanzas/carga_masiva_resultado.html`
- Modify: `templates/finanzas/dashboard.html:56-60`
- Modify: `templates/finanzas/carga_cliente_form.html:13-19`
- Test: `finanzas/test_complementos_pago.py` (agregar clase)

**Interfaces:**
- Consume: estados `COMPLEMENTO_LIGADO` / `COMPLEMENTO_PENDIENTE` (Task 4), URL `finanzas:complementos_pago_pendientes` (Task 6).

- [ ] **Step 1: Escribir la prueba que falla**

Agregar a `finanzas/test_complementos_pago.py`:

```python
@override_settings(MEDIA_ROOT=MEDIA_TMP)
class CargaMasivaResultadoComplementosTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.user = User.objects.create_user('cargacg', password='x')
        self.user.groups.add(grupo)
        self.client.login(username='cargacg', password='x')
        self.url = reverse('finanzas:carga_masiva_xml')

    def test_resultado_muestra_conteo_y_link_de_complementos_pendientes(self):
        xml_bytes = cfdi_pago(uuid_factura='99999999-9999-9999-9999-999999999999')
        resp = self.client.post(self.url, {
            'archivos': [SimpleUploadedFile('pago.xml', xml_bytes)],
        })
        self.assertContains(resp, 'Complemento')
        self.assertContains(resp, reverse('finanzas:complementos_pago_pendientes'))
```

- [ ] **Step 2: Verificar que falla**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago.CargaMasivaResultadoComplementosTests -v2`
Expected: `AssertionError` — el resultado hoy no distingue `COMPLEMENTO_PENDIENTE` (cae en el `else` de "Error") ni enlaza a la vista nueva.

- [ ] **Step 3: Actualizar conteos en `_procesar_subida_xml`**

En `finanzas/views.py`, reemplazar el diccionario `conteos` dentro de `_procesar_subida_xml`:

```python
    conteos = {
        'asignados': sum(1 for r in resultados if r.estado == 'ASIGNADO'),
        'pendientes': sum(1 for r in resultados if r.estado == 'PENDIENTE'),
        'duplicados': sum(1 for r in resultados if r.estado == 'DUPLICADO'),
        'errores': sum(1 for r in resultados if r.estado == 'ERROR'),
    }
```

por:

```python
    conteos = {
        'asignados': sum(1 for r in resultados if r.estado == 'ASIGNADO'),
        'pendientes': sum(1 for r in resultados if r.estado == 'PENDIENTE'),
        'duplicados': sum(1 for r in resultados if r.estado == 'DUPLICADO'),
        'errores': sum(1 for r in resultados if r.estado == 'ERROR'),
        'complementos_ligados': sum(1 for r in resultados if r.estado == 'COMPLEMENTO_LIGADO'),
        'complementos_pendientes': sum(1 for r in resultados if r.estado == 'COMPLEMENTO_PENDIENTE'),
    }
```

- [ ] **Step 4: Actualizar `carga_masiva_resultado.html`**

Reemplazar el párrafo de conteos:

```html
    <p class="text-slate-500 text-sm mt-0.5">
      {{ conteos.asignados }} asignados ·
      {{ conteos.pendientes }} pendientes ·
      {{ conteos.duplicados }} duplicados ·
      {{ conteos.errores }} con error
    </p>
```

por:

```html
    <p class="text-slate-500 text-sm mt-0.5">
      {{ conteos.asignados }} asignados ·
      {{ conteos.pendientes }} pendientes ·
      {{ conteos.duplicados }} duplicados ·
      {{ conteos.errores }} con error ·
      {{ conteos.complementos_ligados }} complementos de pago ligados ·
      {{ conteos.complementos_pendientes }} complementos sin identificar
    </p>
```

Reemplazar el bloque de estado por archivo:

```html
            {% if r.estado == 'ASIGNADO' %}
              <span class="text-green-700 bg-green-50 px-2 py-0.5 rounded text-xs font-medium">Asignado</span>
            {% elif r.estado == 'PENDIENTE' %}
              <span class="text-amber-700 bg-amber-50 px-2 py-0.5 rounded text-xs font-medium">Pendiente</span>
            {% elif r.estado == 'DUPLICADO' %}
              <span class="text-slate-500 bg-slate-100 px-2 py-0.5 rounded text-xs font-medium">Duplicado</span>
            {% else %}
              <span class="text-red-700 bg-red-50 px-2 py-0.5 rounded text-xs font-medium">Error</span>
            {% endif %}
```

por:

```html
            {% if r.estado == 'ASIGNADO' %}
              <span class="text-green-700 bg-green-50 px-2 py-0.5 rounded text-xs font-medium">Asignado</span>
            {% elif r.estado == 'PENDIENTE' %}
              <span class="text-amber-700 bg-amber-50 px-2 py-0.5 rounded text-xs font-medium">Pendiente</span>
            {% elif r.estado == 'DUPLICADO' %}
              <span class="text-slate-500 bg-slate-100 px-2 py-0.5 rounded text-xs font-medium">Duplicado</span>
            {% elif r.estado == 'COMPLEMENTO_LIGADO' %}
              <span class="text-indigo-700 bg-indigo-50 px-2 py-0.5 rounded text-xs font-medium">Complemento ligado</span>
            {% elif r.estado == 'COMPLEMENTO_PENDIENTE' %}
              <span class="text-amber-700 bg-amber-50 px-2 py-0.5 rounded text-xs font-medium">Complemento sin identificar</span>
            {% else %}
              <span class="text-red-700 bg-red-50 px-2 py-0.5 rounded text-xs font-medium">Error</span>
            {% endif %}
```

Reemplazar el bloque final de link a pendientes:

```html
  {% if conteos.pendientes and request.user|tiene_modulo:'Finanzas' %}
  <div class="mt-4">
    <a href="{% url 'finanzas:xml_pendientes' %}" class="text-sky-600 hover:underline text-sm">
      Asignar los pendientes →
    </a>
  </div>
  {% endif %}
```

por:

```html
  {% if conteos.pendientes and request.user|tiene_modulo:'Finanzas' %}
  <div class="mt-4">
    <a href="{% url 'finanzas:xml_pendientes' %}" class="text-sky-600 hover:underline text-sm">
      Asignar los pendientes →
    </a>
  </div>
  {% endif %}
  {% if conteos.complementos_pendientes and request.user|tiene_modulo:'Finanzas' %}
  <div class="mt-2">
    <a href="{% url 'finanzas:complementos_pago_pendientes' %}" class="text-sky-600 hover:underline text-sm">
      Identificar complementos de pago →
    </a>
  </div>
  {% endif %}
```

- [ ] **Step 5: Agregar tarjeta en el dashboard**

En `templates/finanzas/dashboard.html`, después del bloque:

```html
    <a href="{% url 'finanzas:xml_pendientes' %}"
       class="bg-white rounded-xl border border-slate-200 p-5 hover:border-amber-300 hover:shadow-sm transition-all">
      <p class="text-xs text-slate-500 uppercase tracking-wider mb-1">XMLs pendientes</p>
      <p class="text-sm text-slate-600 mt-2">Asignar facturas sin referencia</p>
    </a>
```

agregar:

```html
    <a href="{% url 'finanzas:complementos_pago_pendientes' %}"
       class="bg-white rounded-xl border border-slate-200 p-5 hover:border-indigo-300 hover:shadow-sm transition-all">
      <p class="text-xs text-slate-500 uppercase tracking-wider mb-1">Complementos de pago</p>
      <p class="text-sm text-slate-600 mt-2">Identificar la factura pagada</p>
    </a>
```

- [ ] **Step 6: Agregar el link en `carga_cliente_form.html`**

Reemplazar:

```html
      {% if request.user|tiene_modulo:'Finanzas' %}
      Las facturas quedarán en
      <a href="{% url 'finanzas:xml_pendientes' %}" class="text-sky-600 hover:underline">XMLs pendientes</a>,
      donde el RFC del receptor te ayudará a anexarlas a la referencia del cliente.
      {% else %}
```

por:

```html
      {% if request.user|tiene_modulo:'Finanzas' %}
      Las facturas quedarán en
      <a href="{% url 'finanzas:xml_pendientes' %}" class="text-sky-600 hover:underline">XMLs pendientes</a>,
      donde el RFC del receptor te ayudará a anexarlas a la referencia del cliente.
      Los complementos de pago que no se puedan ligar aparecerán en
      <a href="{% url 'finanzas:complementos_pago_pendientes' %}" class="text-sky-600 hover:underline">Complementos de pago no identificados</a>.
      {% else %}
```

- [ ] **Step 7: Verificar que pasa**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas.test_complementos_pago -v2`
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add finanzas/views.py templates/finanzas/carga_masiva_resultado.html templates/finanzas/dashboard.html templates/finanzas/carga_cliente_form.html finanzas/test_complementos_pago.py
git commit -m "feat(finanzas): navegación y conteos para complementos de pago en carga masiva y dashboard"
```

---

### Task 9: Suite completa y verificación manual

**Files:** ninguno nuevo — solo verificación.

- [ ] **Step 1: Correr toda la suite de finanzas**

Run: `DBURL='sqlite:///tmp_test_db.sqlite3' .venv/bin/python manage.py test finanzas -v2 2>&1 | tail -40`
Expected: `OK`, sin errores ni fallas (incluye las ~30 pruebas nuevas de `test_complementos_pago.py` más toda la suite existente).

- [ ] **Step 2: Borrar la base SQLite temporal**

Run: `rm -f /home/tony/Developer/Proyecto_HAL9MIL/tmp_test_db.sqlite3`

- [ ] **Step 3: Verificación manual con el servidor de desarrollo**

Usar la skill `run` (o `python manage.py runserver`) para:
1. Subir un complemento de pago (usar `cfdi_pago()` para generar un XML de prueba, o construir uno a mano) desde el panel de una referencia que ya tenga una factura con el UUID correspondiente → confirmar que la fila de esa factura muestra "COM. PAGO" en TIPO y PROCESADO, y el link "Ver PDF pago".
2. Subir un complemento cuya factura no existe → confirmar que aparece en `/finanzas/xml/complementos-pendientes/` y no en `/finanzas/xml/pendientes/`.
3. Desde esa vista, ligarlo a mano escribiendo el número de referencia correcto → confirmar que desaparece de pendientes y la fila de la factura se actualiza.
4. Subir la factura correspondiente a un complemento ya pendiente → confirmar que se liga solo (conciliación automática), sin pasar por la vista manual.

- [ ] **Step 4: Django system check**

Run: `.venv/bin/python manage.py check`
Expected: `System check identified no issues (0 silenced).`

---

## Self-Review

**Cobertura del spec:** cada sección del spec (`2026-07-15-complementos-pago-design.md`) tiene tarea: modelo → Task 1; parseo → Task 2; módulo de servicio y conciliación retroactiva → Task 3; los 3 puntos de entrada → Tasks 4-5; fila fusionada y vista de pendientes → Tasks 6-7; navegación/conteos → Task 8; pruebas → integradas en cada tarea + Task 9.

**Placeholders:** ninguno — cada paso tiene código completo o comando exacto.

**Consistencia de tipos:** `procesar_complemento(root, *, uuid_complemento, fecha, rfc_emisor, nombre_emisor, nombre_archivo, xml_bytes, pdf_bytes=None, referencia_sugerida=None) -> ComplementoPago` se define en Task 3 y se llama con los mismos nombres de parámetro en Tasks 4 y 5. `conciliar_pendientes(xml_obj)` igual. Los estados de `ResultadoArchivo` (`COMPLEMENTO_LIGADO`, `COMPLEMENTO_PENDIENTE`) se introducen en Task 4 y se consumen sin cambios en Task 8.
