# Carga masiva de XMLs de proveedor — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carga masiva de facturas CFDI de terminales portuarias (ZIP o archivos sueltos) en el módulo Finanzas, ligándolas automáticamente a su `referencias.Referencia` por patente + pedimento + contenedor y generando el gasto correspondiente.

**Architecture:** Extractores por RFC de emisor leen la addenda de cada proveedor (`dvz:datosExtra` para LCT, `APMTLZC` para APM); una cascada de coincidencia valida contra `Referencia`/`Contenedor`; un servicio de lote crea `XMLProveedor` (ASIGNADO o PENDIENTE) + `GastoReferencia` tipo MANIOBRAS; dos vistas nuevas (carga masiva y pendientes) protegidas con `@modulo_required('Finanzas')`.

**Tech Stack:** Django 5, `xml.etree.ElementTree` (stdlib), `zipfile` (stdlib), Tailwind en templates.

**Spec:** `docs/superpowers/specs/2026-07-09-carga-masiva-xml-design.md` — léela antes de empezar.

## Global Constraints

- Python del proyecto: `.venv/bin/python` (NO el python del sistema).
- Comando de tests: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test <objetivo> -v 1`. El prefijo `DBURL=...` es obligatorio: `settings.py` hace `load_dotenv()` y sin él los tests pegan al Postgres remoto. `env -u DBURL` NO funciona.
- Toda vista nueva de finanzas lleva `@modulo_required('Finanzas')` (import: `from core.permisos import modulo_required`).
- Código, nombres, mensajes y commits en español, siguiendo el estilo existente del repo.
- Los tests que generan pólizas necesitan `fixtures = ['plan_cuentas_inicial.json']` (contiene las cuentas `2-100-002` y `5-100-006` que exige `generar_poliza_gasto`).
- Los ZIPs reales de la raíz del repo (`Facturas - *.zip`, `invoices-*.zip`) NO se commitean.
- RFCs soportados: LCT = `LCT030408U39`, APM = `ATL120106DC6`.

---

### Task 1: Campos nuevos en `XMLProveedor` + migraciones

**Files:**
- Modify: `finanzas/models.py:186-213` (clase `XMLProveedor`)
- Create: `finanzas/migrations/0009_*.py` (auto), `finanzas/migrations/0010_asignar_estado_xml_existentes.py`
- Test: `finanzas/test_carga_masiva.py` (nuevo)

**Interfaces:**
- Produces: `XMLProveedor.pdf_file` (FileField null/blank), `XMLProveedor.estado_asignacion` (`'ASIGNADO'`/`'PENDIENTE'`, default `'PENDIENTE'`), `XMLProveedor.motivo_pendiente` (CharField 200, blank). Tareas 6-8 dependen de estos nombres exactos.

- [ ] **Step 1: Escribir el test que falla**

Crear `finanzas/test_carga_masiva.py`:

```python
from datetime import datetime
from decimal import Decimal

from django.core.files.base import ContentFile
from django.test import TestCase

from .models import XMLProveedor


def _crear_xml_proveedor(**extra):
    defaults = dict(
        uuid_fiscal='135088fd-f6a7-4313-9d6a-3d15ee966df1',
        fecha_emision=datetime(2026, 7, 8, 8, 53, 11),
        rfc_emisor='LCT030408U39',
        nombre_emisor='L C TERMINAL',
        rfc_receptor='CIN220216BS2',
        subtotal=Decimal('9563.79'),
        iva=Decimal('1530.21'),
        total=Decimal('11094.00'),
        tipo_comprobante='I',
    )
    defaults.update(extra)
    obj = XMLProveedor(**defaults)
    obj.xml_file.save('prueba.xml', ContentFile(b'<x/>'), save=False)
    obj.save()
    return obj


class XMLProveedorCamposTests(TestCase):
    def test_campos_de_asignacion_con_defaults(self):
        obj = _crear_xml_proveedor()
        self.assertEqual(obj.estado_asignacion, 'PENDIENTE')
        self.assertEqual(obj.motivo_pendiente, '')
        self.assertFalse(obj.pdf_file)

    def test_acepta_pdf_y_estado_asignado(self):
        obj = _crear_xml_proveedor(
            estado_asignacion='ASIGNADO',
            motivo_pendiente='',
        )
        obj.pdf_file.save('prueba.pdf', ContentFile(b'%PDF'), save=True)
        obj.refresh_from_db()
        self.assertEqual(obj.estado_asignacion, 'ASIGNADO')
        self.assertTrue(obj.pdf_file)
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas.test_carga_masiva -v 1`
Expected: ERROR — `XMLProveedor` no tiene `estado_asignacion` / `pdf_file`.

- [ ] **Step 3: Agregar los campos al modelo**

En `finanzas/models.py`, dentro de `class XMLProveedor`, después de la línea `procesado = models.BooleanField(default=False)  # True si ya generó GastoReferencia`:

```python
    ESTADO_ASIGNACION = [
        ('ASIGNADO', 'Asignado'),
        ('PENDIENTE', 'Pendiente'),
    ]
    pdf_file = models.FileField(
        upload_to='xmls_proveedores/%Y/%m/', null=True, blank=True
    )
    estado_asignacion = models.CharField(
        max_length=10, choices=ESTADO_ASIGNACION, default='PENDIENTE'
    )
    motivo_pendiente = models.CharField(max_length=200, blank=True)
```

- [ ] **Step 4: Crear migraciones (esquema + datos)**

```bash
.venv/bin/python manage.py makemigrations finanzas
.venv/bin/python manage.py makemigrations finanzas --empty -n asignar_estado_xml_existentes
```

Editar la migración vacía `finanzas/migrations/0010_asignar_estado_xml_existentes.py`:

```python
from django.db import migrations


def marcar_asignados(apps, schema_editor):
    XMLProveedor = apps.get_model('finanzas', 'XMLProveedor')
    XMLProveedor.objects.filter(referencia__isnull=False).update(
        estado_asignacion='ASIGNADO'
    )


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('finanzas', '0009_xmlproveedor_estado_asignacion_and_more'),
    ]

    operations = [
        migrations.RunPython(marcar_asignados, revertir),
    ]
```

(Ajustar el nombre exacto en `dependencies` al que haya generado `makemigrations` en 0009.)

- [ ] **Step 5: Correr el test y verificar que pasa**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas.test_carga_masiva -v 1`
Expected: `OK` (2 tests)

- [ ] **Step 6: Aplicar migraciones a la BD de desarrollo y commitear**

```bash
.venv/bin/python manage.py migrate finanzas
git add finanzas/models.py finanzas/migrations/0009_*.py finanzas/migrations/0010_asignar_estado_xml_existentes.py finanzas/test_carga_masiva.py
git commit -m "Agrega pdf_file y estado de asignación a XMLProveedor"
```

---

### Task 2: CFDIs sintéticos de prueba + `parsear_cfdi_root()`

**Files:**
- Create: `finanzas/cfdi_de_prueba.py`
- Modify: `finanzas/cfdi_parser.py:17-29`
- Test: `finanzas/test_extractores.py` (nuevo)

**Interfaces:**
- Produces: `cfdi_lct(uuid, patente, pedimento, contenedor, bl) -> bytes` y `cfdi_apm(uuid, pedimento, agente, contenedor, bl) -> bytes` (XML CFDI 4.0 sintéticos, estructuralmente idénticos a los reales pero con datos ficticios). `parsear_cfdi_root(root) -> dict` con las mismas llaves que `parsear_cfdi` (`uuid, fecha, rfc_emisor, nombre_emisor, rfc_receptor, subtotal, iva, total, moneda, tipo, concepto_principal`). Tareas 3-8 usan estas tres funciones.

- [ ] **Step 1: Crear los constructores de CFDI sintético**

Crear `finanzas/cfdi_de_prueba.py`:

```python
"""Constructores de CFDI 4.0 sintéticos para tests.

Reproducen la estructura real de las facturas de LCT (addenda Diverza con
dvz:datosExtra) y APM (addenda Edicom con APMTLZC) sin datos fiscales reales.
"""

_PLANTILLA_LCT = '''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
    Version="4.0" Serie="L" Folio="4563870" Fecha="2026-07-08T08:53:11"
    SubTotal="9563.79" Moneda="MXN" Total="11094.00" TipoDeComprobante="I"
    MetodoPago="PPD" LugarExpedicion="60950">
  <cfdi:Emisor Rfc="LCT030408U39" Nombre="L C TERMINAL PORTUARIA DE CONTENEDORES" RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="CIN220216BS2" Nombre="CACIPA INTERNACIONAL" UsoCFDI="G03" DomicilioFiscalReceptor="90200" RegimenFiscalReceptor="601"/>
  <cfdi:Conceptos>
    <cfdi:Concepto Cantidad="1" ClaveProdServ="78141700" ClaveUnidad="E48" Descripcion="MUELLAJE" ValorUnitario="261.00" Importe="261.00" ObjetoImp="02"/>
  </cfdi:Conceptos>
  <cfdi:Impuestos TotalImpuestosTrasladados="1530.21">
    <cfdi:Traslados>
      <cfdi:Traslado Impuesto="002" TipoFactor="Tasa" Base="9563.79" TasaOCuota="0.160000" Importe="1530.21"/>
    </cfdi:Traslados>
  </cfdi:Impuestos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="1.1" UUID="{uuid}" FechaTimbrado="2026-07-08T08:53:17"/>
  </cfdi:Complemento>
  <cfdi:Addenda>
    <dvz:addenda xmlns:dvz="http://www.diverza.com/addenda">
      <dvz:datosExtra valor="{patente}" atributo="LeyendaEspecial15"/>
      <dvz:datosExtra valor="{pedimento}" atributo="LeyendaEspecial16"/>
      <dvz:datosExtra valor="{bl}" atributo="LeyendaEspecial20"/>
      <dvz:datosExtra valor="{contenedor}" atributo="LeyendaEspecial25"/>
    </dvz:addenda>
  </cfdi:Addenda>
</cfdi:Comprobante>'''

_PLANTILLA_APM = '''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
    Version="4.0" Serie="C" Folio="1786738" Fecha="2026-07-08T19:10:10"
    SubTotal="7877.79" Moneda="MXN" Total="9138.24" TipoDeComprobante="I"
    MetodoPago="PUE" LugarExpedicion="60950">
  <cfdi:Emisor Rfc="ATL120106DC6" Nombre="APM TERMINALS LAZARO CARDENAS" RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="IGJ181107TZ3" Nombre="CLIENTE DE PRUEBA" UsoCFDI="G03" DomicilioFiscalReceptor="06300" RegimenFiscalReceptor="601"/>
  <cfdi:Conceptos>
    <cfdi:Concepto Cantidad="1" ClaveProdServ="78141700" ClaveUnidad="E48" Descripcion="{contenedor}-MUELLAJE 40 HC" ValorUnitario="261.00" Importe="261.00" ObjetoImp="02"/>
    <cfdi:Concepto Cantidad="1" ClaveProdServ="78141804" ClaveUnidad="E48" Descripcion="{contenedor}-CODIGO ISPS" ValorUnitario="163.79" Importe="163.79" ObjetoImp="02"/>
  </cfdi:Conceptos>
  <cfdi:Impuestos TotalImpuestosTrasladados="1260.45">
    <cfdi:Traslados>
      <cfdi:Traslado Impuesto="002" TipoFactor="Tasa" Base="7877.79" TasaOCuota="0.160000" Importe="1260.45"/>
    </cfdi:Traslados>
  </cfdi:Impuestos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="1.1" UUID="{uuid}" FechaTimbrado="2026-07-08T19:10:20"/>
  </cfdi:Complemento>
  <cfdi:Addenda>
    <customized xmlns="http://repository.edicomnet.com/schemas/mx/cfd/addenda">
      <APMTLZC>
        <MANIOBRA>SEGUNDA</MANIOBRA>
        <TRAFICO>IMPORTACION</TRAFICO>
        <PEDIMENTO>{pedimento}</PEDIMENTO>
        <AGENTEADUANAL>{agente}</AGENTEADUANAL>
        <CONOCIMIENTO>{bl}</CONOCIMIENTO>
        <BUQUE>SAN FERNANDO</BUQUE>
        <VIAJE>623E</VIAJE>
      </APMTLZC>
    </customized>
  </cfdi:Addenda>
</cfdi:Comprobante>'''


def cfdi_lct(uuid='11111111-1111-1111-1111-111111111111', patente='1656',
             pedimento='1656-6001126', contenedor='CSNU 879377 0',
             bl='COSU6501186800'):
    return _PLANTILLA_LCT.format(
        uuid=uuid, patente=patente, pedimento=pedimento,
        contenedor=contenedor, bl=bl,
    ).encode('utf-8')


def cfdi_apm(uuid='22222222-2222-2222-2222-222222222222', pedimento='6000517',
             agente='1627/LUIS FELIPE VAZQUEZ DIAZ', contenedor='BEAU4729066',
             bl='HLCUSHA2604CHSA6'):
    return _PLANTILLA_APM.format(
        uuid=uuid, pedimento=pedimento, agente=agente,
        contenedor=contenedor, bl=bl,
    ).encode('utf-8')
```

- [ ] **Step 2: Escribir el test que falla**

Crear `finanzas/test_extractores.py`:

```python
import xml.etree.ElementTree as ET
from decimal import Decimal

from django.test import SimpleTestCase

from .cfdi_de_prueba import cfdi_apm, cfdi_lct
from .cfdi_parser import parsear_cfdi_root


class ParsearCfdiRootTests(SimpleTestCase):
    def test_parsea_cfdi_lct_desde_elemento_raiz(self):
        root = ET.fromstring(cfdi_lct(uuid='135088fd-f6a7-4313-9d6a-3d15ee966df1'))
        datos = parsear_cfdi_root(root)
        self.assertEqual(datos['uuid'], '135088fd-f6a7-4313-9d6a-3d15ee966df1')
        self.assertEqual(datos['rfc_emisor'], 'LCT030408U39')
        self.assertEqual(datos['total'], Decimal('11094.00'))
        self.assertEqual(datos['iva'], Decimal('1530.21'))
        self.assertEqual(datos['tipo'], 'I')

    def test_parsea_cfdi_apm_desde_elemento_raiz(self):
        root = ET.fromstring(cfdi_apm())
        datos = parsear_cfdi_root(root)
        self.assertEqual(datos['rfc_emisor'], 'ATL120106DC6')
        self.assertEqual(datos['total'], Decimal('9138.24'))
```

- [ ] **Step 3: Correr el test y verificar que falla**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas.test_extractores -v 1`
Expected: ERROR — `cannot import name 'parsear_cfdi_root'`.

- [ ] **Step 4: Refactorizar `parsear_cfdi` extrayendo `parsear_cfdi_root`**

En `finanzas/cfdi_parser.py`, reemplazar el inicio de `parsear_cfdi` (líneas 17-37) por:

```python
def parsear_cfdi(xml_path: str) -> dict:
    """
    Parsea CFDI 3.3 o 4.0 desde una ruta de archivo.
    Lanza ValueError si el XML no es un CFDI timbrado válido.
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except ET.ParseError as e:
        raise ValueError(f'XML malformado: {e}')
    return parsear_cfdi_root(root)


def parsear_cfdi_root(root) -> dict:
    """
    Igual que parsear_cfdi pero recibe el elemento raíz ya parseado.
    Retorna dict con:
        uuid, fecha (datetime), rfc_emisor, nombre_emisor, rfc_receptor,
        subtotal, iva, total (Decimal), moneda, tipo, concepto_principal
    """
    # Detectar versión por namespace del elemento raíz
    if NS_CFDI4 in root.tag:
        ns = NS_CFDI4
    elif NS_CFDI3 in root.tag:
        ns = NS_CFDI3
    else:
        raise ValueError('El archivo no es un CFDI válido (namespace no reconocido)')
```

El resto del cuerpo original (desde `nsmap = ...` hasta el `return`) queda igual, ahora dentro de `parsear_cfdi_root`.

- [ ] **Step 5: Correr los tests (nuevos + regresión) y verificar que pasan**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas -v 1`
Expected: `OK` (los 2 nuevos + los 4 de acceso + los 2 de Task 1)

- [ ] **Step 6: Commit**

```bash
git add finanzas/cfdi_de_prueba.py finanzas/cfdi_parser.py finanzas/test_extractores.py
git commit -m "Agrega CFDIs sintéticos de prueba y parsear_cfdi_root"
```

---

### Task 3: Extractor LCT (`finanzas/extractores.py`)

**Files:**
- Create: `finanzas/extractores.py`
- Test: `finanzas/test_extractores.py` (agregar clase)

**Interfaces:**
- Consumes: `cfdi_lct()` de Task 2.
- Produces: `DatosAduanales` (dataclass con `patente: str`, `pedimento: str`, `contenedor: str`, `bl: str`, todos default `''`) y `extraer_datos_aduanales(root) -> DatosAduanales | None` (None si el RFC del emisor no está soportado). Tareas 4-6 dependen de estos nombres.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `finanzas/test_extractores.py`:

```python
from .extractores import extraer_datos_aduanales


class ExtractorLctTests(SimpleTestCase):
    def _extraer(self, **kwargs):
        return extraer_datos_aduanales(ET.fromstring(cfdi_lct(**kwargs)))

    def test_extrae_patente_pedimento_contenedor_y_bl(self):
        datos = self._extraer()
        self.assertEqual(datos.patente, '1656')
        # LeyendaEspecial16 viene como "1656-6001126"; se usa lo de después del guión
        self.assertEqual(datos.pedimento, '6001126')
        # LeyendaEspecial25 viene como "CSNU 879377 0"; se normaliza sin espacios
        self.assertEqual(datos.contenedor, 'CSNU8793770')
        self.assertEqual(datos.bl, 'COSU6501186800')

    def test_pedimento_sin_guion_se_usa_tal_cual(self):
        datos = self._extraer(pedimento='6001126')
        self.assertEqual(datos.pedimento, '6001126')

    def test_leyendas_vacias_dan_campos_vacios(self):
        datos = self._extraer(patente='', pedimento='', contenedor='', bl='')
        self.assertEqual(datos.patente, '')
        self.assertEqual(datos.pedimento, '')
        self.assertEqual(datos.contenedor, '')

    def test_rfc_no_soportado_devuelve_none(self):
        xml = cfdi_lct().replace(b'LCT030408U39', b'XXX010101XXX')
        self.assertIsNone(extraer_datos_aduanales(ET.fromstring(xml)))
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas.test_extractores -v 1`
Expected: ERROR — `No module named 'finanzas.extractores'`.

- [ ] **Step 3: Implementar el extractor LCT**

Crear `finanzas/extractores.py`:

```python
"""Extracción de datos aduanales (patente, pedimento, contenedor, BL) de las
addendas de los CFDI de proveedores de terminal portuaria.

Cada proveedor se identifica por el RFC del emisor y tiene su propio extractor
registrado en _EXTRACTORES. Ver spec:
docs/superpowers/specs/2026-07-09-carga-masiva-xml-design.md
"""
import re
from dataclasses import dataclass

NS_CFDI4 = 'http://www.sat.gob.mx/cfd/4'
NS_CFDI3 = 'http://www.sat.gob.mx/cfd/3'

RFC_LCT = 'LCT030408U39'
RFC_APM = 'ATL120106DC6'

# Contenedor ISO 6346: 4 letras + 7 dígitos, como prefijo "XXXX9999999-"
RE_CONTENEDOR = re.compile(r'^([A-Z]{4}\d{7})-')


@dataclass
class DatosAduanales:
    patente: str = ''
    pedimento: str = ''
    contenedor: str = ''
    bl: str = ''


def _ns(root) -> str:
    return NS_CFDI4 if NS_CFDI4 in root.tag else NS_CFDI3


def _rfc_emisor(root) -> str:
    emisor = root.find(f'{{{_ns(root)}}}Emisor')
    return emisor.get('Rfc', '') if emisor is not None else ''


def extraer_datos_aduanales(root):
    """Devuelve DatosAduanales según el proveedor, o None si el RFC del
    emisor no está soportado."""
    extractor = _EXTRACTORES.get(_rfc_emisor(root))
    if extractor is None:
        return None
    return extractor(root)


def _extraer_lct(root) -> DatosAduanales:
    # Addenda Diverza: <dvz:datosExtra atributo="LeyendaEspecialNN" valor="..."/>
    # Se busca por local-name para no depender del URI del namespace dvz.
    leyendas = {}
    for el in root.iter():
        if el.tag.endswith('}datosExtra') or el.tag == 'datosExtra':
            leyendas[el.get('atributo', '')] = el.get('valor') or ''
    pedimento = leyendas.get('LeyendaEspecial16', '').strip()
    if '-' in pedimento:
        pedimento = pedimento.split('-')[-1].strip()
    return DatosAduanales(
        patente=leyendas.get('LeyendaEspecial15', '').strip(),
        pedimento=pedimento,
        contenedor=leyendas.get('LeyendaEspecial25', '').replace(' ', ''),
        bl=leyendas.get('LeyendaEspecial20', '').strip(),
    )


_EXTRACTORES = {RFC_LCT: _extraer_lct}
```

- [ ] **Step 4: Correr y verificar que pasan**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas.test_extractores -v 1`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add finanzas/extractores.py finanzas/test_extractores.py
git commit -m "Agrega extractor de datos aduanales para LCT (addenda Diverza)"
```

---

### Task 4: Extractor APM

**Files:**
- Modify: `finanzas/extractores.py`
- Test: `finanzas/test_extractores.py` (agregar clase)

**Interfaces:**
- Consumes: `cfdi_apm()` de Task 2; `DatosAduanales`, `_EXTRACTORES` de Task 3.
- Produces: `_extraer_apm(root)` registrado en `_EXTRACTORES[RFC_APM]`; `extraer_datos_aduanales` ahora cubre ambos proveedores.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `finanzas/test_extractores.py`:

```python
class ExtractorApmTests(SimpleTestCase):
    def _extraer(self, **kwargs):
        return extraer_datos_aduanales(ET.fromstring(cfdi_apm(**kwargs)))

    def test_extrae_pedimento_patente_contenedor_y_bl(self):
        datos = self._extraer()
        self.assertEqual(datos.pedimento, '6000517')
        # AGENTEADUANAL = "1627/LUIS FELIPE VAZQUEZ DIAZ" → patente es el prefijo
        self.assertEqual(datos.patente, '1627')
        # Contenedor: prefijo "XXXX9999999-" de la Descripcion de los conceptos
        self.assertEqual(datos.contenedor, 'BEAU4729066')
        self.assertEqual(datos.bl, 'HLCUSHA2604CHSA6')

    def test_concepto_sin_prefijo_de_contenedor_da_contenedor_vacio(self):
        # "SERVICIO GENERAL" no cumple el patrón XXXX9999999-
        datos = self._extraer(contenedor='SERVICIO GENERAL')
        self.assertEqual(datos.contenedor, '')

    def test_agente_aduanal_sin_diagonal_se_usa_completo(self):
        datos = self._extraer(agente='1627')
        self.assertEqual(datos.patente, '1627')
```

Nota: en `cfdi_apm`, el kwarg `contenedor` se interpola como prefijo de las
descripciones (`{contenedor}-MUELLAJE 40 HC`), así que pasar
`'SERVICIO GENERAL'` produce `Descripcion="SERVICIO GENERAL-MUELLAJE 40 HC"`,
que no cumple el regex.

- [ ] **Step 2: Correr y verificar que fallan**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas.test_extractores.ExtractorApmTests -v 1`
Expected: FAIL — `extraer_datos_aduanales` devuelve None para RFC APM.

- [ ] **Step 3: Implementar el extractor APM**

En `finanzas/extractores.py`, antes de `_EXTRACTORES`:

```python
def _extraer_apm(root) -> DatosAduanales:
    # Addenda Edicom: <customized><APMTLZC><PEDIMENTO>... (con namespace default)
    campos = {}
    for el in root.iter():
        if el.tag.endswith('}APMTLZC') or el.tag == 'APMTLZC':
            for hijo in el:
                local = hijo.tag.split('}')[-1]
                campos[local] = (hijo.text or '').strip()
            break
    patente = campos.get('AGENTEADUANAL', '').split('/')[0].strip()
    # El contenedor viene como prefijo en la Descripcion de cada concepto
    contenedor = ''
    for concepto in root.iter(f'{{{_ns(root)}}}Concepto'):
        m = RE_CONTENEDOR.match(concepto.get('Descripcion') or '')
        if m:
            contenedor = m.group(1)
            break
    return DatosAduanales(
        patente=patente,
        pedimento=campos.get('PEDIMENTO', ''),
        contenedor=contenedor,
        bl=campos.get('CONOCIMIENTO', ''),
    )
```

Y actualizar el registro:

```python
_EXTRACTORES = {RFC_LCT: _extraer_lct, RFC_APM: _extraer_apm}
```

- [ ] **Step 4: Correr y verificar que pasan**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas.test_extractores -v 1`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add finanzas/extractores.py finanzas/test_extractores.py
git commit -m "Agrega extractor de datos aduanales para APM (addenda APMTLZC)"
```

---

### Task 5: Cascada de coincidencia `buscar_referencia`

**Files:**
- Modify: `finanzas/extractores.py`
- Test: `finanzas/test_extractores.py` (agregar clase)

**Interfaces:**
- Consumes: `DatosAduanales` de Task 3; modelos `referencias.Referencia` y `referencias.Contenedor`.
- Produces: `buscar_referencia(datos: DatosAduanales | None) -> tuple[Referencia | None, str]` — el segundo elemento es el motivo cuando no hay asignación (`''` cuando sí la hay). Task 6 depende de esta firma.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `finanzas/test_extractores.py` (nótese que esta clase usa `TestCase`, no `SimpleTestCase`, porque pega a la BD):

```python
from django.test import TestCase

from referencias.models import Contenedor, Referencia

from .extractores import DatosAduanales, buscar_referencia


class BuscarReferenciaTests(TestCase):
    def setUp(self):
        self.ref = Referencia.objects.create(
            num_refe='LCRR1126/26', patente='1656', prefijo='LCRR',
            num_pedimento='6001126',
        )
        Contenedor.objects.create(referencia=self.ref, num_cont='CSNU8793770')

    def test_match_unico_por_patente_y_pedimento(self):
        datos = DatosAduanales(patente='1656', pedimento='6001126',
                               contenedor='CSNU8793770')
        ref, motivo = buscar_referencia(datos)
        self.assertEqual(ref, self.ref)
        self.assertEqual(motivo, '')

    def test_match_por_pedimento_sin_contenedor_en_bd_tambien_liga(self):
        datos = DatosAduanales(patente='1656', pedimento='6001126',
                               contenedor='ZZZU0000000')
        # El contenedor no existe en la BD: no contradice, se liga
        ref, motivo = buscar_referencia(datos)
        self.assertEqual(ref, self.ref)

    def test_contenedor_que_contradice_el_pedimento_queda_pendiente(self):
        otra = Referencia.objects.create(
            num_refe='LCLF0999/26', patente='1627', prefijo='LCLF',
            num_pedimento='5999999',
        )
        Contenedor.objects.create(referencia=otra, num_cont='BEAU4729066')
        datos = DatosAduanales(patente='1656', pedimento='6001126',
                               contenedor='BEAU4729066')
        ref, motivo = buscar_referencia(datos)
        self.assertIsNone(ref)
        self.assertIn('contradice', motivo)

    def test_pedimento_sin_referencia_queda_pendiente(self):
        datos = DatosAduanales(patente='1656', pedimento='7777777')
        ref, motivo = buscar_referencia(datos)
        self.assertIsNone(ref)
        self.assertIn('7777777', motivo)

    def test_fallback_por_contenedor_unico_cuando_no_hay_pedimento(self):
        datos = DatosAduanales(contenedor='CSNU8793770')
        ref, motivo = buscar_referencia(datos)
        self.assertEqual(ref, self.ref)

    def test_contenedor_reutilizado_sin_pedimento_queda_pendiente(self):
        otra = Referencia.objects.create(
            num_refe='LCLF0417', patente='1627', prefijo='LCLF',
        )
        Contenedor.objects.create(referencia=otra, num_cont='CSNU8793770')
        datos = DatosAduanales(contenedor='CSNU8793770')
        ref, motivo = buscar_referencia(datos)
        self.assertIsNone(ref)
        self.assertIn('varias referencias', motivo)

    def test_datos_none_es_proveedor_no_soportado(self):
        ref, motivo = buscar_referencia(None)
        self.assertIsNone(ref)
        self.assertEqual(motivo, 'proveedor no soportado')

    def test_sin_datos_aduanales(self):
        ref, motivo = buscar_referencia(DatosAduanales())
        self.assertIsNone(ref)
        self.assertEqual(motivo, 'sin datos aduanales en el XML')
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas.test_extractores.BuscarReferenciaTests -v 1`
Expected: ERROR — `cannot import name 'buscar_referencia'`.

- [ ] **Step 3: Implementar la cascada**

Agregar al final de `finanzas/extractores.py`:

```python
def buscar_referencia(datos):
    """Cascada de coincidencia XML → Referencia (ver spec).

    1. (patente, num_pedimento) debe dar exactamente una referencia.
    2. El contenedor, si existe en la BD, debe apuntar a esa misma
       referencia; si la contradice, queda pendiente.
    3. Sin patente/pedimento utilizables: el contenedor liga solo si da
       exactamente una referencia.

    Devuelve (referencia | None, motivo). motivo es '' cuando hay match.
    """
    from referencias.models import Contenedor, Referencia

    if datos is None:
        return None, 'proveedor no soportado'

    if datos.patente and datos.pedimento:
        candidatas = list(Referencia.objects.filter(
            patente=datos.patente, num_pedimento=datos.pedimento,
        )[:2])
        if len(candidatas) > 1:
            return None, (f'varias referencias para patente {datos.patente} '
                          f'/ pedimento {datos.pedimento}')
        if not candidatas:
            return None, (f'sin referencia para patente {datos.patente} '
                          f'/ pedimento {datos.pedimento}')
        candidata = candidatas[0]
        if datos.contenedor:
            refs_cont = set(
                Contenedor.objects.filter(num_cont=datos.contenedor)
                .values_list('referencia_id', flat=True)
            )
            if refs_cont and candidata.id not in refs_cont:
                return None, (f'contenedor {datos.contenedor} contradice '
                              f'patente {datos.patente} / pedimento {datos.pedimento}')
        return candidata, ''

    if datos.contenedor:
        ref_ids = list(
            Contenedor.objects.filter(num_cont=datos.contenedor)
            .values_list('referencia_id', flat=True).distinct()
        )
        if len(ref_ids) == 1:
            return Referencia.objects.get(pk=ref_ids[0]), ''
        if len(ref_ids) > 1:
            return None, (f'contenedor {datos.contenedor} aparece en '
                          f'varias referencias')
        return None, f'sin referencia para contenedor {datos.contenedor}'

    return None, 'sin datos aduanales en el XML'
```

- [ ] **Step 4: Correr y verificar que pasan**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas.test_extractores -v 1`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add finanzas/extractores.py finanzas/test_extractores.py
git commit -m "Agrega cascada de coincidencia buscar_referencia"
```

---

### Task 6: Servicio de lote (`finanzas/carga_xml.py`) + refactor de gasto compartido

**Files:**
- Create: `finanzas/carga_xml.py`
- Modify: `finanzas/views.py:211-232` (bloque de creación de gasto en `subir_xml_proveedor`)
- Test: `finanzas/test_carga_masiva.py` (agregar clases)

**Interfaces:**
- Consumes: `parsear_cfdi_root` (Task 2), `extraer_datos_aduanales`, `buscar_referencia` (Tasks 3-5), campos de Task 1.
- Produces (Task 7-8 dependen de esto):
  - `crear_gasto_desde_xml(xml_obj: XMLProveedor, usuario, tipo='MANIOBRAS') -> GastoReferencia` — crea gasto + póliza y marca `xml_obj.procesado = True`.
  - `expandir_subidas(uploaded_files) -> list[tuple[str, bytes]]` — expande ZIPs y archivos sueltos; propaga `zipfile.BadZipFile`.
  - `procesar_lote(files: list[tuple[str, bytes]], usuario) -> list[ResultadoArchivo]`.
  - `ResultadoArchivo`: dataclass con `nombre: str`, `estado: str` (`'ASIGNADO' | 'PENDIENTE' | 'DUPLICADO' | 'ERROR'`), `referencia: Referencia | None`, `detalle: str`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `finanzas/test_carga_masiva.py`:

```python
import io
import tempfile
import zipfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from referencias.models import Contenedor, Referencia

from .carga_xml import crear_gasto_desde_xml, expandir_subidas, procesar_lote
from .cfdi_de_prueba import cfdi_apm, cfdi_lct
from .models import GastoReferencia

MEDIA_TMP = tempfile.mkdtemp()


class ExpandirSubidasTests(TestCase):
    def test_expande_zip_y_archivos_sueltos(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('C1786738.xml', cfdi_apm())
            zf.writestr('C1786738.pdf', b'%PDF')
        zip_file = SimpleUploadedFile('invoices.zip', buf.getvalue())
        suelto = SimpleUploadedFile('factura.xml', cfdi_lct())
        archivos = expandir_subidas([zip_file, suelto])
        nombres = sorted(n for n, _ in archivos)
        self.assertEqual(nombres, ['C1786738.pdf', 'C1786738.xml', 'factura.xml'])

    def test_zip_invalido_lanza_badzipfile(self):
        malo = SimpleUploadedFile('roto.zip', b'no soy un zip')
        with self.assertRaises(zipfile.BadZipFile):
            expandir_subidas([malo])


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ProcesarLoteTests(TestCase):
    fixtures = ['plan_cuentas_inicial.json']

    def setUp(self):
        self.usuario = User.objects.create_user('fin', password='x')
        self.ref_lct = Referencia.objects.create(
            num_refe='LCRR1126/26', patente='1656', prefijo='LCRR',
            num_pedimento='6001126',
        )
        Contenedor.objects.create(referencia=self.ref_lct, num_cont='CSNU8793770')
        self.ref_apm = Referencia.objects.create(
            num_refe='LCLF0517/26', patente='1627', prefijo='LCLF',
            num_pedimento='6000517',
        )
        Contenedor.objects.create(referencia=self.ref_apm, num_cont='BEAU4729066')

    def test_xml_lct_con_match_queda_asignado_y_genera_gasto(self):
        resultados = procesar_lote([('lct.xml', cfdi_lct())], self.usuario)
        self.assertEqual(resultados[0].estado, 'ASIGNADO')
        self.assertEqual(resultados[0].referencia, self.ref_lct)
        xml_obj = XMLProveedor.objects.get()
        self.assertEqual(xml_obj.referencia, self.ref_lct)
        self.assertEqual(xml_obj.estado_asignacion, 'ASIGNADO')
        self.assertTrue(xml_obj.procesado)
        gasto = GastoReferencia.objects.get()
        self.assertEqual(gasto.tipo, 'MANIOBRAS')
        self.assertEqual(gasto.monto, Decimal('11094.00'))
        self.assertIsNotNone(gasto.poliza)

    def test_xml_apm_con_match_queda_asignado(self):
        resultados = procesar_lote([('apm.xml', cfdi_apm())], self.usuario)
        self.assertEqual(resultados[0].estado, 'ASIGNADO')
        self.assertEqual(resultados[0].referencia, self.ref_apm)

    def test_pdf_se_empareja_por_nombre(self):
        files = [('lct.xml', cfdi_lct()), ('lct.pdf', b'%PDF'), ('otro.csv', b'x')]
        procesar_lote(files, self.usuario)
        xml_obj = XMLProveedor.objects.get()
        self.assertTrue(xml_obj.pdf_file)

    def test_uuid_duplicado_se_omite(self):
        procesar_lote([('lct.xml', cfdi_lct())], self.usuario)
        resultados = procesar_lote([('lct2.xml', cfdi_lct())], self.usuario)
        self.assertEqual(resultados[0].estado, 'DUPLICADO')
        self.assertEqual(XMLProveedor.objects.count(), 1)

    def test_sin_match_queda_pendiente_sin_gasto(self):
        xml = cfdi_lct(uuid='33333333-3333-3333-3333-333333333333',
                       patente='1656', pedimento='1656-7777777',
                       contenedor='')
        resultados = procesar_lote([('lct.xml', xml)], self.usuario)
        self.assertEqual(resultados[0].estado, 'PENDIENTE')
        self.assertIn('7777777', resultados[0].detalle)
        xml_obj = XMLProveedor.objects.get()
        self.assertIsNone(xml_obj.referencia)
        self.assertEqual(xml_obj.estado_asignacion, 'PENDIENTE')
        self.assertEqual(GastoReferencia.objects.count(), 0)

    def test_xml_corrupto_reporta_error_y_no_aborta_el_lote(self):
        files = [('roto.xml', b'<<< no soy xml'), ('lct.xml', cfdi_lct())]
        resultados = procesar_lote(files, self.usuario)
        estados = {r.nombre: r.estado for r in resultados}
        self.assertEqual(estados['roto.xml'], 'ERROR')
        self.assertEqual(estados['lct.xml'], 'ASIGNADO')
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas.test_carga_masiva -v 1`
Expected: ERROR — `No module named 'finanzas.carga_xml'`.

- [ ] **Step 3: Implementar el servicio**

Crear `finanzas/carga_xml.py`:

```python
"""Servicio de carga masiva de XMLs de proveedor.

Recibe ZIPs o archivos sueltos, empareja XML↔PDF por nombre, parsea cada
CFDI, extrae datos aduanales, liga con la Referencia y genera el gasto.
Ver spec: docs/superpowers/specs/2026-07-09-carga-masiva-xml-design.md
"""
import os
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

from django.core.files.base import ContentFile

from .cfdi_parser import parsear_cfdi_root
from .extractores import buscar_referencia, extraer_datos_aduanales
from .models import GastoReferencia, XMLProveedor
from .polizas import generar_poliza_gasto


@dataclass
class ResultadoArchivo:
    nombre: str
    estado: str                 # ASIGNADO | PENDIENTE | DUPLICADO | ERROR
    referencia: object = None   # referencias.Referencia | None
    detalle: str = ''


def crear_gasto_desde_xml(xml_obj, usuario, tipo='MANIOBRAS'):
    """Crea el GastoReferencia + póliza a partir de un XMLProveedor ya ligado
    a una referencia, y marca el XML como procesado."""
    gasto = GastoReferencia.objects.create(
        referencia=xml_obj.referencia,
        tipo=tipo,
        concepto=xml_obj.concepto_principal or f'Factura {xml_obj.rfc_emisor}',
        fecha=xml_obj.fecha_emision.date(),
        monto=xml_obj.total,
        moneda=xml_obj.moneda,
        proveedor=xml_obj.nombre_emisor,
        xml_proveedor=xml_obj,
        registrado_por=usuario,
    )
    poliza = generar_poliza_gasto(gasto)
    gasto.poliza = poliza
    gasto.save(update_fields=['poliza'])
    xml_obj.procesado = True
    xml_obj.save(update_fields=['procesado'])
    return gasto


def expandir_subidas(uploaded_files):
    """Convierte lo subido (ZIPs y/o archivos sueltos) en [(nombre, bytes)].
    Propaga zipfile.BadZipFile si un ZIP es inválido."""
    resultado = []
    for f in uploaded_files:
        if f.name.lower().endswith('.zip'):
            with zipfile.ZipFile(f) as zf:
                for info in zf.infolist():
                    if not info.is_dir():
                        resultado.append((info.filename, zf.read(info)))
        else:
            resultado.append((f.name, f.read()))
    return resultado


def _recolectar(files):
    """Empareja XMLs con su PDF por nombre base (mismo stem). Ignora el resto
    (CSV anexo, PDFs sin XML, etc.)."""
    xmls, pdfs = {}, {}
    for nombre, data in files:
        base = os.path.basename(nombre)
        stem, ext = os.path.splitext(base)
        ext = ext.lower()
        if ext == '.xml':
            xmls[stem] = (base, data)
        elif ext == '.pdf':
            pdfs[stem] = data
    return [
        {'nombre': base, 'stem': stem, 'xml': data, 'pdf': pdfs.get(stem)}
        for stem, (base, data) in sorted(xmls.items())
    ]


def procesar_lote(files, usuario):
    """Procesa [(nombre, bytes)] y devuelve un ResultadoArchivo por XML."""
    return [_procesar_uno(item, usuario) for item in _recolectar(files)]


def _procesar_uno(item, usuario):
    nombre = item['nombre']
    try:
        root = ET.fromstring(item['xml'])
        datos = parsear_cfdi_root(root)
    except (ET.ParseError, ValueError) as e:
        return ResultadoArchivo(nombre, 'ERROR', detalle=str(e))

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

    if referencia is None:
        return ResultadoArchivo(nombre, 'PENDIENTE', detalle=motivo)
    # Solo los comprobantes de Ingreso generan gasto (E = nota de crédito)
    if datos['tipo'] == 'I':
        crear_gasto_desde_xml(xml_obj, usuario)
    return ResultadoArchivo(nombre, 'ASIGNADO', referencia=referencia)
```

- [ ] **Step 4: Refactorizar `subir_xml_proveedor` para reutilizar `crear_gasto_desde_xml`**

En `finanzas/views.py`:

1. Agregar import: `from .carga_xml import crear_gasto_desde_xml`
2. En el `XMLProveedor.objects.create(...)` de `subir_xml_proveedor` (línea ~195), agregar el kwarg `estado_asignacion='ASIGNADO',` (la referencia viene dada por la URL).
3. Reemplazar el bloque de creación de gasto (líneas 212-228, desde `if request.POST.get('crear_gasto') == '1'...` hasta `xml_obj.save(update_fields=['procesado'])`) por:

```python
    if request.POST.get('crear_gasto') == '1' and datos['tipo'] == 'I':
        gasto = crear_gasto_desde_xml(xml_obj, request.user, tipo='OTROS')
        messages.success(
            request,
            f'XML cargado · Gasto ${datos["total"]} registrado · Póliza {gasto.poliza.numero} generada.'
        )
```

(El `else:` con su `messages.success` de "XML cargado correctamente" queda igual. Nota: `crear_gasto_desde_xml` conserva el tipo `OTROS` aquí para no cambiar el comportamiento existente de la carga individual.)

- [ ] **Step 5: Correr toda la suite de finanzas y verificar que pasa**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas -v 1`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add finanzas/carga_xml.py finanzas/views.py finanzas/test_carga_masiva.py
git commit -m "Agrega servicio de carga masiva de XMLs con emparejado de PDF"
```

---

### Task 7: Vista de carga masiva + templates + URL

**Files:**
- Modify: `finanzas/views.py` (nueva vista al final), `finanzas/urls.py`
- Create: `templates/finanzas/carga_masiva_form.html`, `templates/finanzas/carga_masiva_resultado.html`
- Test: `finanzas/test_carga_masiva.py` (agregar clase)

**Interfaces:**
- Consumes: `expandir_subidas`, `procesar_lote` (Task 6).
- Produces: URL `finanzas:carga_masiva_xml` (`/finanzas/xml/carga-masiva/`), vista `carga_masiva_xml`. Task 8 y 9 enlazan a este name.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `finanzas/test_carga_masiva.py`:

```python
from django.contrib.auth.models import Group
from django.urls import reverse


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class CargaMasivaViewTests(TestCase):
    fixtures = ['plan_cuentas_inicial.json']

    def setUp(self):
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.usuario = User.objects.create_user('fin_carga', password='x')
        self.usuario.groups.add(grupo)
        self.client.force_login(self.usuario)
        self.ref = Referencia.objects.create(
            num_refe='LCRR1126/26', patente='1656', prefijo='LCRR',
            num_pedimento='6001126',
        )

    def test_get_muestra_formulario(self):
        response = self.client.get(reverse('finanzas:carga_masiva_xml'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Carga masiva')

    def test_post_zip_procesa_y_muestra_resumen(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('lct.xml', cfdi_lct())
            zf.writestr('lct.pdf', b'%PDF')
        archivo = SimpleUploadedFile('facturas.zip', buf.getvalue())
        response = self.client.post(
            reverse('finanzas:carga_masiva_xml'), {'archivos': [archivo]}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'LCRR1126/26')
        xml_obj = XMLProveedor.objects.get()
        self.assertEqual(xml_obj.estado_asignacion, 'ASIGNADO')
        self.assertTrue(xml_obj.pdf_file)

    def test_post_archivos_sueltos(self):
        archivos = [
            SimpleUploadedFile('lct.xml', cfdi_lct()),
            SimpleUploadedFile('lct.pdf', b'%PDF'),
        ]
        response = self.client.post(
            reverse('finanzas:carga_masiva_xml'), {'archivos': archivos}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(XMLProveedor.objects.count(), 1)

    def test_post_sin_xmls_muestra_error(self):
        archivo = SimpleUploadedFile('nota.txt', b'hola')
        response = self.client.post(
            reverse('finanzas:carga_masiva_xml'), {'archivos': [archivo]},
            follow=True,
        )
        self.assertContains(response, 'ningún archivo XML')
        self.assertEqual(XMLProveedor.objects.count(), 0)

    def test_post_zip_invalido_muestra_error(self):
        archivo = SimpleUploadedFile('roto.zip', b'no soy zip')
        response = self.client.post(
            reverse('finanzas:carga_masiva_xml'), {'archivos': [archivo]},
            follow=True,
        )
        self.assertContains(response, 'ZIP')
        self.assertEqual(XMLProveedor.objects.count(), 0)

    def test_usuario_sin_grupo_es_redirigido(self):
        otro = User.objects.create_user('sin_grupo', password='x')
        self.client.force_login(otro)
        response = self.client.get(reverse('finanzas:carga_masiva_xml'))
        self.assertRedirects(response, reverse('dashboard'))
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas.test_carga_masiva.CargaMasivaViewTests -v 1`
Expected: ERROR — `Reverse for 'carga_masiva_xml' not found`.

- [ ] **Step 3: Implementar vista, URL y templates**

En `finanzas/views.py`, agregar los imports que faltan al inicio (`import zipfile` junto a `import os, tempfile`; y en el import de `.carga_xml` agregar `expandir_subidas, procesar_lote`), y al final del archivo:

```python
@modulo_required('Finanzas')
def carga_masiva_xml(request):
    if request.method != 'POST':
        return render(request, 'finanzas/carga_masiva_form.html')

    archivos = request.FILES.getlist('archivos')
    if not archivos:
        messages.error(request, 'No se seleccionó ningún archivo.')
        return redirect('finanzas:carga_masiva_xml')

    try:
        files = expandir_subidas(archivos)
    except zipfile.BadZipFile:
        messages.error(request, 'El archivo ZIP es inválido o está dañado.')
        return redirect('finanzas:carga_masiva_xml')

    resultados = procesar_lote(files, request.user)
    if not resultados:
        messages.error(request, 'No se encontró ningún archivo XML en lo subido.')
        return redirect('finanzas:carga_masiva_xml')

    conteos = {
        'asignados': sum(1 for r in resultados if r.estado == 'ASIGNADO'),
        'pendientes': sum(1 for r in resultados if r.estado == 'PENDIENTE'),
        'duplicados': sum(1 for r in resultados if r.estado == 'DUPLICADO'),
        'errores': sum(1 for r in resultados if r.estado == 'ERROR'),
    }
    return render(request, 'finanzas/carga_masiva_resultado.html', {
        'resultados': resultados,
        'conteos': conteos,
    })
```

En `finanzas/urls.py`, antes del bloque "Rutas por referencia":

```python
    # Carga masiva de XMLs de proveedor
    path('xml/carga-masiva/', views.carga_masiva_xml, name='carga_masiva_xml'),
```

Crear `templates/finanzas/carga_masiva_form.html`:

```html
{% extends 'base.html' %}
{% block title %}Carga masiva de XMLs · Finanzas{% endblock %}
{% block content %}
<div class="p-6 max-w-2xl">

  <div class="mb-6">
    <a href="{% url 'finanzas:dashboard' %}" class="text-sky-600 hover:underline text-sm">← Finanzas</a>
    <h1 class="text-2xl font-bold text-slate-800 mt-2">Carga masiva de XMLs de proveedor</h1>
    <p class="text-slate-500 text-sm">
      Sube el ZIP del proveedor (LCT o APM) o los archivos XML/PDF sueltos.
      Cada factura se liga automáticamente a su referencia por patente,
      pedimento y contenedor, y genera su gasto de maniobras.
    </p>
  </div>

  <form method="post" enctype="multipart/form-data"
        class="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
    {% csrf_token %}
    <div>
      <label class="block text-xs font-medium text-slate-600 mb-1">
        Archivos (ZIP, XML y PDF) <span class="text-red-500">*</span>
      </label>
      <input type="file" name="archivos" multiple accept=".zip,.xml,.pdf"
             class="block w-full text-sm text-slate-600">
    </div>
    <div class="pt-2 flex gap-3">
      <button type="submit"
              class="bg-slate-600 hover:bg-slate-700 text-white font-medium px-6 py-2 rounded-lg text-sm transition-colors">
        Procesar
      </button>
    </div>
  </form>

</div>
{% endblock %}
```

Crear `templates/finanzas/carga_masiva_resultado.html`:

```html
{% extends 'base.html' %}
{% block title %}Resultado de carga masiva · Finanzas{% endblock %}
{% block content %}
<div class="p-6 max-w-4xl">

  <div class="mb-6">
    <a href="{% url 'finanzas:carga_masiva_xml' %}" class="text-sky-600 hover:underline text-sm">← Nueva carga</a>
    <h1 class="text-2xl font-bold text-slate-800 mt-2">Resultado de la carga</h1>
    <p class="text-slate-500 text-sm">
      {{ conteos.asignados }} asignados ·
      {{ conteos.pendientes }} pendientes ·
      {{ conteos.duplicados }} duplicados ·
      {{ conteos.errores }} con error
    </p>
  </div>

  <div class="bg-white rounded-xl border border-slate-200 overflow-hidden">
    <table class="w-full text-sm">
      <thead class="bg-slate-50 text-left text-xs text-slate-500 uppercase tracking-wider">
        <tr>
          <th class="px-4 py-3">Archivo</th>
          <th class="px-4 py-3">Resultado</th>
          <th class="px-4 py-3">Referencia / Detalle</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-100">
        {% for r in resultados %}
        <tr>
          <td class="px-4 py-2 font-mono text-xs">{{ r.nombre }}</td>
          <td class="px-4 py-2">
            {% if r.estado == 'ASIGNADO' %}
              <span class="text-green-700 bg-green-50 px-2 py-0.5 rounded text-xs font-medium">Asignado</span>
            {% elif r.estado == 'PENDIENTE' %}
              <span class="text-amber-700 bg-amber-50 px-2 py-0.5 rounded text-xs font-medium">Pendiente</span>
            {% elif r.estado == 'DUPLICADO' %}
              <span class="text-slate-500 bg-slate-100 px-2 py-0.5 rounded text-xs font-medium">Duplicado</span>
            {% else %}
              <span class="text-red-700 bg-red-50 px-2 py-0.5 rounded text-xs font-medium">Error</span>
            {% endif %}
          </td>
          <td class="px-4 py-2 text-slate-600">
            {% if r.referencia %}{{ r.referencia.num_refe }}{% else %}{{ r.detalle }}{% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  {% if conteos.pendientes %}
  <div class="mt-4">
    <a href="{% url 'finanzas:xml_pendientes' %}" class="text-sky-600 hover:underline text-sm">
      Asignar los pendientes →
    </a>
  </div>
  {% endif %}

</div>
{% endblock %}
```

Nota: el enlace `finanzas:xml_pendientes` se crea en Task 8; para que este
template no rompa antes, agregar en `finanzas/urls.py` AMBAS rutas desde ahora
(la vista `xml_pendientes` de Task 8 puede ser un stub temporal) — o más
simple: implementar Task 7 y Task 8 en orden y correr los tests de Task 7
después de agregar la URL de Task 8. Decisión para el implementador: agregar
en este task la ruta con un stub mínimo:

```python
@modulo_required('Finanzas')
def xml_pendientes(request):
    pendientes = XMLProveedor.objects.filter(
        estado_asignacion='PENDIENTE'
    ).order_by('-cargado_en')
    return render(request, 'finanzas/xml_pendientes.html', {'pendientes': pendientes})
```

y en `urls.py`:

```python
    path('xml/pendientes/', views.xml_pendientes, name='xml_pendientes'),
```

con un template mínimo `templates/finanzas/xml_pendientes.html` que Task 8
completa (ver Task 8 Step 3 para el contenido final; aquí basta
`{% extends 'base.html' %}{% block content %}{% endblock %}`).

- [ ] **Step 4: Correr y verificar que pasan**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas -v 1`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add finanzas/views.py finanzas/urls.py templates/finanzas/carga_masiva_form.html templates/finanzas/carga_masiva_resultado.html templates/finanzas/xml_pendientes.html finanzas/test_carga_masiva.py
git commit -m "Agrega vista de carga masiva de XMLs de proveedor"
```

---

### Task 8: Vista de pendientes con asignación manual

**Files:**
- Modify: `finanzas/views.py` (completar `xml_pendientes`), `templates/finanzas/xml_pendientes.html`
- Test: `finanzas/test_carga_masiva.py` (agregar clase)

**Interfaces:**
- Consumes: `crear_gasto_desde_xml` (Task 6), URL `finanzas:xml_pendientes` (stub de Task 7).
- Produces: asignación manual por POST con campos `xml_id` y `num_refe`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `finanzas/test_carga_masiva.py`:

```python
@override_settings(MEDIA_ROOT=MEDIA_TMP)
class XmlPendientesViewTests(TestCase):
    fixtures = ['plan_cuentas_inicial.json']

    def setUp(self):
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.usuario = User.objects.create_user('fin_pend', password='x')
        self.usuario.groups.add(grupo)
        self.client.force_login(self.usuario)
        self.ref = Referencia.objects.create(
            num_refe='LCRR1126/26', patente='1656', prefijo='LCRR',
        )
        self.xml_obj = _crear_xml_proveedor(
            motivo_pendiente='sin referencia para patente 1656 / pedimento 7777777',
        )

    def test_lista_muestra_pendientes_con_motivo(self):
        response = self.client.get(reverse('finanzas:xml_pendientes'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'pedimento 7777777')

    def test_asignar_manualmente_liga_y_genera_gasto(self):
        response = self.client.post(reverse('finanzas:xml_pendientes'), {
            'xml_id': self.xml_obj.pk,
            'num_refe': 'LCRR1126/26',
        }, follow=True)
        self.xml_obj.refresh_from_db()
        self.assertEqual(self.xml_obj.referencia, self.ref)
        self.assertEqual(self.xml_obj.estado_asignacion, 'ASIGNADO')
        self.assertEqual(self.xml_obj.motivo_pendiente, '')
        gasto = GastoReferencia.objects.get()
        self.assertEqual(gasto.tipo, 'MANIOBRAS')
        self.assertContains(response, 'asignado')

    def test_referencia_inexistente_muestra_error(self):
        response = self.client.post(reverse('finanzas:xml_pendientes'), {
            'xml_id': self.xml_obj.pk,
            'num_refe': 'NOEXISTE/99',
        }, follow=True)
        self.xml_obj.refresh_from_db()
        self.assertIsNone(self.xml_obj.referencia)
        self.assertContains(response, 'No existe la referencia')
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas.test_carga_masiva.XmlPendientesViewTests -v 1`
Expected: FAIL — el stub no muestra motivo ni acepta POST.

- [ ] **Step 3: Completar la vista y el template**

Reemplazar el stub `xml_pendientes` en `finanzas/views.py` por:

```python
@modulo_required('Finanzas')
def xml_pendientes(request):
    if request.method == 'POST':
        xml_obj = get_object_or_404(
            XMLProveedor,
            pk=request.POST.get('xml_id'),
            estado_asignacion='PENDIENTE',
        )
        num_refe = request.POST.get('num_refe', '').strip()
        referencia = Referencia.objects.filter(num_refe=num_refe).first()
        if referencia is None:
            messages.error(request, f'No existe la referencia "{num_refe}".')
        else:
            xml_obj.referencia = referencia
            xml_obj.estado_asignacion = 'ASIGNADO'
            xml_obj.motivo_pendiente = ''
            xml_obj.save(update_fields=[
                'referencia', 'estado_asignacion', 'motivo_pendiente',
            ])
            if xml_obj.tipo_comprobante == 'I' and not xml_obj.procesado:
                crear_gasto_desde_xml(xml_obj, request.user)
            messages.success(
                request, f'XML asignado a {referencia.num_refe} y gasto generado.'
            )
        return redirect('finanzas:xml_pendientes')

    pendientes = XMLProveedor.objects.filter(
        estado_asignacion='PENDIENTE'
    ).order_by('-cargado_en')
    return render(request, 'finanzas/xml_pendientes.html', {'pendientes': pendientes})
```

Reemplazar `templates/finanzas/xml_pendientes.html` por:

```html
{% extends 'base.html' %}
{% block title %}XMLs pendientes de asignar · Finanzas{% endblock %}
{% block content %}
<div class="p-6 max-w-5xl">

  <div class="mb-6">
    <a href="{% url 'finanzas:carga_masiva_xml' %}" class="text-sky-600 hover:underline text-sm">← Carga masiva</a>
    <h1 class="text-2xl font-bold text-slate-800 mt-2">XMLs pendientes de asignar</h1>
    <p class="text-slate-500 text-sm">
      Facturas cargadas que no encontraron referencia automáticamente.
      Escribe el número de referencia exacto (ej. LCRR1126/26) para ligarlas.
    </p>
  </div>

  {% if pendientes %}
  <div class="bg-white rounded-xl border border-slate-200 overflow-x-auto">
    <table class="w-full text-sm">
      <thead class="bg-slate-50 text-left text-xs text-slate-500 uppercase tracking-wider">
        <tr>
          <th class="px-4 py-3">Emisor</th>
          <th class="px-4 py-3">Folio fiscal</th>
          <th class="px-4 py-3">Fecha</th>
          <th class="px-4 py-3">Total</th>
          <th class="px-4 py-3">Motivo</th>
          <th class="px-4 py-3">Asignar a referencia</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-100">
        {% for xml in pendientes %}
        <tr>
          <td class="px-4 py-2">{{ xml.nombre_emisor|truncatechars:30 }}</td>
          <td class="px-4 py-2 font-mono text-xs">{{ xml.uuid_fiscal }}</td>
          <td class="px-4 py-2">{{ xml.fecha_emision|date:'Y-m-d' }}</td>
          <td class="px-4 py-2">${{ xml.total }}</td>
          <td class="px-4 py-2 text-amber-700 text-xs">{{ xml.motivo_pendiente }}</td>
          <td class="px-4 py-2">
            <form method="post" class="flex gap-2">
              {% csrf_token %}
              <input type="hidden" name="xml_id" value="{{ xml.pk }}">
              <input type="text" name="num_refe" placeholder="LCRR0000/26" required
                     class="border border-slate-300 rounded px-2 py-1 text-xs w-32">
              <button type="submit"
                      class="bg-slate-600 hover:bg-slate-700 text-white px-3 py-1 rounded text-xs">
                Asignar
              </button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="bg-white rounded-xl border border-slate-200 p-8 text-center text-slate-500 text-sm">
    No hay XMLs pendientes de asignar. 🎉
  </div>
  {% endif %}

</div>
{% endblock %}
```

- [ ] **Step 4: Correr toda la suite y verificar que pasa**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas -v 1`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add finanzas/views.py templates/finanzas/xml_pendientes.html finanzas/test_carga_masiva.py
git commit -m "Agrega asignación manual de XMLs pendientes"
```

---

### Task 9: Enlaces en el dashboard de Finanzas + verificación final

**Files:**
- Modify: `templates/finanzas/dashboard.html`
- Test: suite completa

**Interfaces:**
- Consumes: URLs `finanzas:carga_masiva_xml` y `finanzas:xml_pendientes` (Tasks 7-8).

- [ ] **Step 1: Escribir el test que falla**

Agregar a `finanzas/test_carga_masiva.py`:

```python
class DashboardEnlacesTests(TestCase):
    def test_dashboard_enlaza_carga_masiva_y_pendientes(self):
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        usuario = User.objects.create_user('fin_dash', password='x')
        usuario.groups.add(grupo)
        self.client.force_login(usuario)
        response = self.client.get(reverse('finanzas:dashboard'))
        self.assertContains(response, reverse('finanzas:carga_masiva_xml'))
        self.assertContains(response, reverse('finanzas:xml_pendientes'))
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test finanzas.test_carga_masiva.DashboardEnlacesTests -v 1`
Expected: FAIL — el dashboard no contiene los enlaces.

- [ ] **Step 3: Agregar las tarjetas al dashboard**

En `templates/finanzas/dashboard.html`, dentro del grid de tarjetas, después de la tarjeta de "Cobranza pendiente" (el `</a>` que cierra antes del `</div>` del grid), agregar:

```html
    <a href="{% url 'finanzas:carga_masiva_xml' %}"
       class="bg-white rounded-xl border border-slate-200 p-5 hover:border-sky-300 hover:shadow-sm transition-all">
      <p class="text-xs text-slate-500 uppercase tracking-wider mb-1">Carga masiva XML</p>
      <p class="text-sm text-slate-600 mt-2">Subir facturas de LCT / APM</p>
    </a>
    <a href="{% url 'finanzas:xml_pendientes' %}"
       class="bg-white rounded-xl border border-slate-200 p-5 hover:border-amber-300 hover:shadow-sm transition-all">
      <p class="text-xs text-slate-500 uppercase tracking-wider mb-1">XMLs pendientes</p>
      <p class="text-sm text-slate-600 mt-2">Asignar facturas sin referencia</p>
    </a>
```

- [ ] **Step 4: Correr TODA la suite del proyecto y verificar que pasa**

Run: `DBURL="sqlite:///$(pwd)/db.sqlite3.test" .venv/bin/python manage.py test core finanzas referencias -v 1`
Expected: `OK` (los 16 tests previos + todos los nuevos)

- [ ] **Step 5: Commit**

```bash
git add templates/finanzas/dashboard.html finanzas/test_carga_masiva.py
git commit -m "Agrega enlaces de carga masiva y pendientes al dashboard de Finanzas"
```

- [ ] **Step 6: Verificación manual con los ZIPs reales (usuario)**

No automatizable sin tocar la BD de desarrollo (Postgres remoto). Indicar al
usuario que pruebe en `/finanzas/xml/carga-masiva/` con
`Facturas - 2026-07-09T092950.209.zip` y `invoices-20260709.zip` de la raíz
del repo. Resultado esperado con los datos ya sincronizados:

- LCT `L4563870` (contenedor CSNU8793770, pedimento 1656-6001126) → `LCRR1126/26`
- APM `C1786738` (contenedor BEAU4729066, pedimento 6000517, agente 1627/…) → `LCLF0517/26`
- Cada XML asignado genera un `GastoReferencia` MANIOBRAS con su póliza E.

Los ZIPs NO se commitean.
