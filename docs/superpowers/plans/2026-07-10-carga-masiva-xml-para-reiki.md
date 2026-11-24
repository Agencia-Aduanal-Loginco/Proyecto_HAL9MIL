# Carga masiva de XMLs de proveedor (CFDI) — Plan de implementación para el proyecto Reiki

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Portar al proyecto Reiki la funcionalidad de carga masiva de facturas CFDI de proveedores de terminal portuaria (LCT y APM): subir ZIPs o archivos sueltos, parsear cada XML, emparejar su PDF, extraer datos aduanales de la addenda, ligar automáticamente cada factura con su referencia aduanal y generar el gasto correspondiente; con bandeja de pendientes para asignación manual y visor de PDF.

**Architecture:** Servicio puro de procesamiento (`carga_xml.py`) separado de las vistas; parser CFDI genérico (`cfdi_parser.py`) reutilizable; extractores de addenda por proveedor registrados por RFC del emisor (`extractores.py`, patrón dispatcher — agregar un proveedor nuevo = agregar una función y una entrada al dict); cascada de coincidencia XML→Referencia validada contra datos reales donde ni pedimento ni contenedor son únicos por sí solos. Todo el parsing con `xml.etree.ElementTree` (stdlib), sin dependencias externas.

**Tech Stack:** Django (probado en 4.x/5.x), Python 3.10+, `xml.etree.ElementTree`, `zipfile`, PostgreSQL (funciona en cualquier backend de Django). Templates con Tailwind CSS (adaptar clases al CSS de Reiki si difiere).

**Origen:** Esta funcionalidad ya está implementada y probada (48 tests) en Proyecto_HAL9MIL, app `finanzas`. El código de este plan es el código real en producción; los tests son los tests reales. Spec original: `docs/superpowers/specs/2026-07-09-carga-masiva-xml-design.md` de HAL9MIL (resumen incluido abajo).

## Global Constraints

- Sin dependencias nuevas de pip: solo stdlib + Django.
- Parsing XML exclusivamente con `xml.etree.ElementTree` (nunca lxml/BeautifulSoup).
- El campo `uuid_fiscal` es `UUIDField(unique=True)` — la deduplicación del lote depende de esto.
- Los archivos se guardan en `MEDIA_ROOT` bajo `upload_to='xmls_proveedores/%Y/%m/'`; Reiki debe tener `MEDIA_ROOT`/`MEDIA_URL` configurados.
- Un XML corrupto o no-CFDI **nunca** aborta el lote: se reporta como fila de error y se continúa.
- Los duplicados (mismo UUID fiscal) se **omiten** (no se sobrescriben) y se reportan.
- Procesamiento síncrono dentro del request (lotes esperados: decenas de facturas, no miles).
- Comprobantes tipo `E` (egreso / nota de crédito) se guardan y ligan pero **no** generan gasto; solo tipo `I`.
- TDD: cada task escribe primero el test, verifica que falla, implementa, verifica que pasa, commit.

## Puntos de adaptación a Reiki (leer antes de empezar)

Este plan fue extraído de HAL9MIL. Hay **4 costuras** donde Reiki debe mapear sus propios equivalentes. Todo lo demás se copia tal cual.

| # | En HAL9MIL | Qué necesita Reiki | Dónde aparece |
|---|---|---|---|
| 1 | `referencias.Referencia` con campos `num_refe` (CharField unique), `patente` (CharField), `num_pedimento` (CharField) | El modelo de Reiki que representa la operación/referencia aduanal, con esos tres datos consultables | Task 1 (FK), Task 4 (`buscar_referencia`), Task 6 (vistas) |
| 2 | `referencias.Contenedor` con FK `referencia` y campo `num_cont` (CharField, `unique_together (referencia, num_cont)`) | El modelo de Reiki que liga contenedores a la referencia | Task 4 (`buscar_referencia`) |
| 3 | `@modulo_required('Finanzas')` (decorador de permisos por módulo) | El decorador de acceso de Reiki (`login_required` como mínimo) | Task 6 (las 3 vistas) |
| 4 | `crear_gasto_desde_xml()` crea un `GastoReferencia` + póliza contable vía `generar_poliza_gasto()` | Lo que Reiki haga con una factura ya ligada (crear su registro de gasto/costo, o nada). Está aislado en **una sola función** — es la única parte con lógica contable | Task 5 |

En el plan, los imports de estos puntos aparecen como en HAL9MIL (`from referencias.models import Referencia, Contenedor`, etc.). Sustituir por los paths reales de Reiki en cada task. Si Reiki no tiene módulo contable, la costura #4 se reduce a marcar `procesado = True` (se indica en la Task 5).

## Resumen del dominio (contexto para quien no conoce el problema)

Las terminales portuarias entregan mensualmente un ZIP con las facturas CFDI de sus servicios (muellaje, maniobras, etc.). Cada factura corresponde a una operación aduanal ("referencia") identificable por **patente** (nº del agente aduanal), **pedimento** y/o **contenedor**. Esos datos NO están en el cuerpo estándar del CFDI sino en la **addenda**, y cada proveedor usa un formato distinto:

### LCT — L C Terminal Portuaria de Contenedores (RFC `LCT030408U39`)

CFDI 4.0 con addenda Diverza: `<dvz:datosExtra atributo="LeyendaEspecialNN" valor="..."/>`.

| Dato | Atributo | Ejemplo real | Normalización |
|---|---|---|---|
| Patente | `LeyendaEspecial15` | `1656` | trim |
| Pedimento | `LeyendaEspecial16` | `1656-6001126` | viene como `patente-pedimento`; usar lo de después del guión |
| Contenedor | `LeyendaEspecial25` | `CSNU 879377 0` | quitar espacios → `CSNU8793770` |
| BL | `LeyendaEspecial20` | `COSU6501186800` | informativo |

⚠️ El URI del namespace `dvz` puede variar: buscar por *local-name* `datosExtra`, nunca por URI fijo. El ZIP de LCT trae por factura XML + PDF + un CSV "anexo" que se ignora.

### APM — APM Terminals Lázaro Cárdenas (RFC `ATL120106DC6`)

CFDI 4.0 con addenda Edicom `<customized><APMTLZC>` (namespace default propio):

| Dato | Fuente | Ejemplo real | Normalización |
|---|---|---|---|
| Pedimento | `<PEDIMENTO>` | `6000517` | ya viene solo |
| Patente | `<AGENTEADUANAL>` | `1627/LUIS FELIPE VAZQUEZ DIAZ` | prefijo antes de `/` |
| Contenedor | `Descripcion` de cada `<cfdi:Concepto>` | `BEAU4729066-MUELLAJE 40 HC` | regex `^([A-Z]{4}\d{7})-` sobre el primer concepto que cumpla |
| BL | `<CONOCIMIENTO>` | `HLCUSHA2604CHSA6` | informativo |

### RFC desconocido

El XML se guarda igualmente, en estado `PENDIENTE` con motivo `"proveedor no soportado"`, para asignación manual.

### Cascada de coincidencia (validada contra datos reales)

Los identificadores **no son únicos** individualmente: un contenedor se reutiliza en varias referencias con el tiempo, y un mismo nº de pedimento existe bajo dos patentes distintas. Por eso:

1. `(patente, num_pedimento)` contra `Referencia` — debe dar **exactamente una** candidata.
2. Verificación cruzada: si el contenedor del XML existe en la BD y apunta a una referencia **distinta** de la candidata → `PENDIENTE` con motivo "contradice" (nunca ligar mal). Si no existe en BD o coincide → se liga.
3. Fallback: sin patente/pedimento utilizables, el contenedor liga solo si da **exactamente una** referencia.
4. Cualquier otro caso (0 ó >1 candidatas) → `PENDIENTE` con motivo descriptivo legible.

## Estructura de archivos

Suponiendo que en Reiki la app se llama `finanzas` (ajustar el nombre si es otra):

```
finanzas/
├── models.py            # Modificar: + XMLProveedor (y GastoReferencia si aplica costura #4)
├── cfdi_parser.py       # Crear: parser CFDI 3.3/4.0 genérico
├── extractores.py       # Crear: extractores de addenda por RFC + buscar_referencia
├── carga_xml.py         # Crear: servicio de procesamiento de lote
├── cfdi_de_prueba.py    # Crear: constructores de CFDI sintéticos para tests
├── views.py             # Modificar: + 3 vistas
├── urls.py              # Modificar: + 3 rutas
├── test_extractores.py  # Crear: tests de parser, extractores y cascada
└── test_carga_masiva.py # Crear: tests del servicio de lote y las vistas
templates/finanzas/
├── carga_masiva_form.html       # Crear
├── carga_masiva_resultado.html  # Crear
└── xml_pendientes.html          # Crear
```

---

### Task 1: Modelo `XMLProveedor` y migración

**Files:**
- Modify: `finanzas/models.py`
- Create: `finanzas/migrations/XXXX_xmlproveedor.py` (autogenerada)

**Interfaces:**
- Consumes: modelo Referencia de Reiki (costura #1).
- Produces: `finanzas.models.XMLProveedor` con los campos exactos listados abajo — todo el resto del plan depende de estos nombres.

- [ ] **Step 1: Agregar el modelo a `finanzas/models.py`**

```python
from decimal import Decimal
from django.db import models


class XMLProveedor(models.Model):
    referencia = models.ForeignKey(
        'referencias.Referencia',            # ← costura #1: modelo de Reiki
        null=True, blank=True,
        on_delete=models.SET_NULL, related_name='xmls_proveedor'
    )
    uuid_fiscal = models.UUIDField(unique=True)
    fecha_emision = models.DateTimeField()
    rfc_emisor = models.CharField(max_length=13)
    nombre_emisor = models.CharField(max_length=200)
    rfc_receptor = models.CharField(max_length=13)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    iva = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    total = models.DecimalField(max_digits=14, decimal_places=2)
    moneda = models.CharField(max_length=3, default='MXN')
    tipo_comprobante = models.CharField(max_length=1)   # I=Ingreso, E=Egreso
    concepto_principal = models.CharField(max_length=300, blank=True)
    xml_file = models.FileField(upload_to='xmls_proveedores/%Y/%m/')
    pdf_file = models.FileField(
        upload_to='xmls_proveedores/%Y/%m/', null=True, blank=True
    )
    cargado_en = models.DateTimeField(auto_now_add=True)
    procesado = models.BooleanField(default=False)  # True si ya generó su gasto
    ESTADO_ASIGNACION = [
        ('ASIGNADO', 'Asignado'),
        ('PENDIENTE', 'Pendiente'),
    ]
    estado_asignacion = models.CharField(
        max_length=10, choices=ESTADO_ASIGNACION, default='PENDIENTE'
    )
    motivo_pendiente = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-fecha_emision']
        verbose_name = 'XML de Proveedor'
        verbose_name_plural = 'XMLs de Proveedor'

    def __str__(self):
        return f'{self.rfc_emisor} | {self.uuid_fiscal} | ${self.total}'
```

- [ ] **Step 2: Generar y aplicar la migración**

Run: `python manage.py makemigrations finanzas && python manage.py migrate`
Expected: migración creada y aplicada sin errores.

- [ ] **Step 3: Verificación rápida en shell**

Run: `python manage.py shell -c "from finanzas.models import XMLProveedor; print(XMLProveedor._meta.get_field('uuid_fiscal').unique)"`
Expected: `True`

- [ ] **Step 4: Commit**

```bash
git add finanzas/models.py finanzas/migrations/
git commit -m "feat(finanzas): modelo XMLProveedor para carga masiva de CFDI"
```

---

### Task 2: Parser CFDI genérico y fixtures sintéticos

**Files:**
- Create: `finanzas/cfdi_parser.py`
- Create: `finanzas/cfdi_de_prueba.py`
- Test: `finanzas/test_extractores.py`

**Interfaces:**
- Produces: `parsear_cfdi_root(root: Element) -> dict` con llaves `uuid, fecha (datetime), rfc_emisor, nombre_emisor, rfc_receptor, subtotal, iva, total (Decimal), moneda, tipo, concepto_principal`; lanza `ValueError` si no es CFDI timbrado válido. También `cfdi_lct(**kwargs) -> bytes` y `cfdi_apm(**kwargs) -> bytes` para tests.

- [ ] **Step 1: Crear `finanzas/cfdi_de_prueba.py`** (constructores de CFDI sintéticos que reproducen la estructura real de ambos proveedores, sin datos fiscales reales)

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

- [ ] **Step 2: Escribir los tests del parser en `finanzas/test_extractores.py`**

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

    def test_xml_sin_timbre_lanza_valueerror(self):
        xml = cfdi_lct().replace(b'TimbreFiscalDigital', b'OtroNodo')
        with self.assertRaises(ValueError):
            parsear_cfdi_root(ET.fromstring(xml))
```

- [ ] **Step 3: Verificar que fallan**

Run: `python manage.py test finanzas.test_extractores -v 2`
Expected: FAIL / ERROR con `ModuleNotFoundError: No module named 'finanzas.cfdi_parser'`

- [ ] **Step 4: Crear `finanzas/cfdi_parser.py`**

```python
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal, InvalidOperation

NS_CFDI4 = 'http://www.sat.gob.mx/cfd/4'
NS_CFDI3 = 'http://www.sat.gob.mx/cfd/3'
NS_TFD   = 'http://www.sat.gob.mx/TimbreFiscalDigital'


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value or '0')
    except InvalidOperation:
        return Decimal('0')


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

    nsmap = {'cfdi': ns, 'tfd': NS_TFD}

    emisor  = root.find('cfdi:Emisor', nsmap)
    receptor = root.find('cfdi:Receptor', nsmap)
    if emisor is None or receptor is None:
        raise ValueError('CFDI incompleto: falta nodo Emisor o Receptor')

    tfd = root.find('cfdi:Complemento/tfd:TimbreFiscalDigital', nsmap)
    if tfd is None:
        raise ValueError('CFDI sin TimbreFiscalDigital (no está timbrado)')

    uuid = tfd.get('UUID', '').strip()
    if not uuid:
        raise ValueError('CFDI sin UUID en el timbre fiscal')

    # Fecha de emisión
    fecha_str = root.get('Fecha', '')
    try:
        fecha = datetime.fromisoformat(fecha_str)
    except (ValueError, AttributeError):
        raise ValueError(f'Fecha de CFDI inválida: {fecha_str!r}')

    # Importes
    subtotal = _decimal(root.get('SubTotal', '0'))
    total    = _decimal(root.get('Total', '0'))
    moneda   = root.get('Moneda', 'MXN')
    tipo     = root.get('TipoDeComprobante', 'I')

    # IVA trasladado (impuesto 002)
    iva = Decimal('0')
    traslado = root.find(
        'cfdi:Impuestos/cfdi:Traslados/cfdi:Traslado[@Impuesto="002"]', nsmap
    )
    if traslado is not None:
        iva = _decimal(traslado.get('Importe', '0'))

    # Primer concepto
    concepto_node = root.find('cfdi:Conceptos/cfdi:Concepto', nsmap)
    concepto = ''
    if concepto_node is not None:
        concepto = (concepto_node.get('Descripcion') or '')[:300]

    return {
        'uuid':             uuid,
        'fecha':            fecha,
        'rfc_emisor':       emisor.get('Rfc', ''),
        'nombre_emisor':    (emisor.get('Nombre') or '')[:200],
        'rfc_receptor':     receptor.get('Rfc', ''),
        'subtotal':         subtotal,
        'iva':              iva,
        'total':            total,
        'moneda':           moneda,
        'tipo':             tipo,
        'concepto_principal': concepto,
    }
```

- [ ] **Step 5: Verificar que pasan**

Run: `python manage.py test finanzas.test_extractores -v 2`
Expected: `OK` (3 tests)

- [ ] **Step 6: Commit**

```bash
git add finanzas/cfdi_parser.py finanzas/cfdi_de_prueba.py finanzas/test_extractores.py
git commit -m "feat(finanzas): parser CFDI 3.3/4.0 con fixtures sintéticos LCT/APM"
```

---

### Task 3: Extractores de addenda por proveedor (LCT y APM)

**Files:**
- Create: `finanzas/extractores.py`
- Test: `finanzas/test_extractores.py` (agregar clases)

**Interfaces:**
- Consumes: `cfdi_lct`, `cfdi_apm` de Task 2.
- Produces: `DatosAduanales` (dataclass: `patente: str, pedimento: str, contenedor: str, bl: str`, defaults `''`) y `extraer_datos_aduanales(root) -> DatosAduanales | None` (None si el RFC emisor no está soportado). Constantes `RFC_LCT = 'LCT030408U39'`, `RFC_APM = 'ATL120106DC6'`.

- [ ] **Step 1: Agregar los tests a `finanzas/test_extractores.py`**

```python
# agregar a los imports:
from .extractores import DatosAduanales, extraer_datos_aduanales


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

Nota: `test_concepto_sin_prefijo_de_contenedor_da_contenedor_vacio` funciona porque el fixture interpola `{contenedor}` en la `Descripcion` (queda `SERVICIO GENERAL-MUELLAJE 40 HC`, que no cumple el regex de contenedor ISO).

- [ ] **Step 2: Verificar que fallan**

Run: `python manage.py test finanzas.test_extractores -v 2`
Expected: ERROR con `No module named 'finanzas.extractores'`

- [ ] **Step 3: Crear `finanzas/extractores.py`** (por ahora sin `buscar_referencia`, que va en Task 4)

```python
"""Extracción de datos aduanales (patente, pedimento, contenedor, BL) de las
addendas de los CFDI de proveedores de terminal portuaria.

Cada proveedor se identifica por el RFC del emisor y tiene su propio extractor
registrado en _EXTRACTORES.
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


_EXTRACTORES = {RFC_LCT: _extraer_lct, RFC_APM: _extraer_apm}
```

- [ ] **Step 4: Verificar que pasan**

Run: `python manage.py test finanzas.test_extractores -v 2`
Expected: `OK` (10 tests)

- [ ] **Step 5: Commit**

```bash
git add finanzas/extractores.py finanzas/test_extractores.py
git commit -m "feat(finanzas): extractores de addenda LCT (Diverza) y APM (Edicom)"
```

---

### Task 4: Cascada de coincidencia `buscar_referencia`

**Files:**
- Modify: `finanzas/extractores.py` (agregar función al final)
- Test: `finanzas/test_extractores.py` (agregar clase)

**Interfaces:**
- Consumes: `DatosAduanales` de Task 3; modelos `Referencia` y `Contenedor` de Reiki (costuras #1 y #2).
- Produces: `buscar_referencia(datos: DatosAduanales | None) -> tuple[Referencia | None, str]` — el segundo elemento es el motivo legible cuando no hay match, `''` cuando sí.

- [ ] **Step 1: Agregar los tests** (ajustar imports/campos de `Referencia`/`Contenedor` a los de Reiki; `prefijo` es un campo propio de HAL9MIL — usar los campos mínimos que Reiki requiera para crear una referencia)

```python
# agregar a los imports:
from django.test import TestCase
from referencias.models import Contenedor, Referencia   # ← costuras #1 y #2
from .extractores import buscar_referencia


class BuscarReferenciaTests(TestCase):
    def setUp(self):
        self.ref = Referencia.objects.create(
            num_refe='LCRR1126/26', patente='1656',
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
            num_refe='LCLF0999/26', patente='1627',
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
            num_refe='LCLF0417', patente='1627',
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

- [ ] **Step 2: Verificar que fallan**

Run: `python manage.py test finanzas.test_extractores.BuscarReferenciaTests -v 2`
Expected: ERROR con `ImportError: cannot import name 'buscar_referencia'`

- [ ] **Step 3: Agregar la función al final de `finanzas/extractores.py`**

```python
def buscar_referencia(datos):
    """Cascada de coincidencia XML → Referencia.

    1. (patente, num_pedimento) debe dar exactamente una referencia.
    2. El contenedor, si existe en la BD, debe apuntar a esa misma
       referencia; si la contradice, queda pendiente.
    3. Sin patente/pedimento utilizables: el contenedor liga solo si da
       exactamente una referencia.

    Devuelve (referencia | None, motivo). motivo es '' cuando hay match.
    """
    from referencias.models import Contenedor, Referencia   # ← costuras #1 y #2

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

El import va dentro de la función a propósito: evita import circular y mantiene el resto del módulo utilizable sin BD (`SimpleTestCase`).

- [ ] **Step 4: Verificar que pasan**

Run: `python manage.py test finanzas.test_extractores -v 2`
Expected: `OK` (18 tests)

- [ ] **Step 5: Commit**

```bash
git add finanzas/extractores.py finanzas/test_extractores.py
git commit -m "feat(finanzas): cascada de coincidencia XML→Referencia (buscar_referencia)"
```

---

### Task 5: Servicio de procesamiento de lote (`carga_xml.py`)

**Files:**
- Create: `finanzas/carga_xml.py`
- Test: `finanzas/test_carga_masiva.py`

**Interfaces:**
- Consumes: `parsear_cfdi_root` (Task 2), `buscar_referencia` + `extraer_datos_aduanales` (Tasks 3-4), `XMLProveedor` (Task 1).
- Produces:
  - `ResultadoArchivo` (dataclass: `nombre: str, estado: str ('ASIGNADO'|'PENDIENTE'|'DUPLICADO'|'ERROR'), referencia=None, detalle: str = ''`)
  - `expandir_subidas(uploaded_files) -> list[tuple[str, bytes]]` — propaga `zipfile.BadZipFile`
  - `procesar_lote(files: list[tuple[str, bytes]], usuario) -> list[ResultadoArchivo]`
  - `crear_gasto_desde_xml(xml_obj, usuario, tipo='MANIOBRAS')` — **costura #4**

- [ ] **Step 1: Escribir los tests en `finanzas/test_carga_masiva.py`**

```python
import io
import zipfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from referencias.models import Contenedor, Referencia   # ← costuras #1 y #2

from .carga_xml import expandir_subidas, procesar_lote
from .cfdi_de_prueba import cfdi_apm, cfdi_lct
from .models import XMLProveedor

User = get_user_model()


def _zip_bytes(entradas):
    """entradas: dict nombre → bytes. Devuelve un ZIP en memoria."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for nombre, data in entradas.items():
            zf.writestr(nombre, data)
    return buf.getvalue()


class ExpandirSubidasTests(TestCase):
    def test_zip_se_expande_en_nombre_bytes(self):
        zf = SimpleUploadedFile(
            'lote.zip', _zip_bytes({'a.xml': b'<x/>', 'a.pdf': b'%PDF'})
        )
        files = expandir_subidas([zf])
        self.assertEqual(
            sorted(n for n, _ in files), ['a.pdf', 'a.xml']
        )

    def test_archivos_sueltos_pasan_directo(self):
        f = SimpleUploadedFile('b.xml', b'<x/>')
        self.assertEqual(expandir_subidas([f]), [('b.xml', b'<x/>')])

    def test_zip_invalido_propaga_badzipfile(self):
        malo = SimpleUploadedFile('roto.zip', b'no soy un zip')
        with self.assertRaises(zipfile.BadZipFile):
            expandir_subidas([malo])


class ProcesarLoteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('fin', password='x')
        self.ref = Referencia.objects.create(
            num_refe='LCRR1126/26', patente='1656',
            num_pedimento='6001126',
        )
        Contenedor.objects.create(referencia=self.ref, num_cont='CSNU8793770')

    def test_xml_con_match_queda_asignado_y_genera_gasto(self):
        resultados = procesar_lote(
            [('f1.xml', cfdi_lct()), ('f1.pdf', b'%PDF-1.4 fake')], self.user
        )
        self.assertEqual(len(resultados), 1)
        r = resultados[0]
        self.assertEqual(r.estado, 'ASIGNADO')
        self.assertEqual(r.referencia, self.ref)
        xml_obj = XMLProveedor.objects.get()
        self.assertEqual(xml_obj.estado_asignacion, 'ASIGNADO')
        self.assertTrue(xml_obj.procesado)
        self.assertTrue(xml_obj.pdf_file)      # PDF emparejado por nombre

    def test_xml_sin_match_queda_pendiente_sin_gasto(self):
        xml = cfdi_lct(pedimento='1656-9999999', contenedor='')
        resultados = procesar_lote([('f2.xml', xml)], self.user)
        self.assertEqual(resultados[0].estado, 'PENDIENTE')
        xml_obj = XMLProveedor.objects.get()
        self.assertEqual(xml_obj.estado_asignacion, 'PENDIENTE')
        self.assertFalse(xml_obj.procesado)
        self.assertIn('9999999', xml_obj.motivo_pendiente)

    def test_uuid_duplicado_se_omite(self):
        procesar_lote([('f1.xml', cfdi_lct())], self.user)
        resultados = procesar_lote([('f1.xml', cfdi_lct())], self.user)
        self.assertEqual(resultados[0].estado, 'DUPLICADO')
        self.assertEqual(XMLProveedor.objects.count(), 1)

    def test_xml_corrupto_no_aborta_el_lote(self):
        resultados = procesar_lote(
            [('malo.xml', b'esto no es xml'),
             ('bueno.xml', cfdi_apm())],
            self.user,
        )
        estados = {r.nombre: r.estado for r in resultados}
        self.assertEqual(estados['malo.xml'], 'ERROR')
        self.assertIn(estados['bueno.xml'], ('ASIGNADO', 'PENDIENTE'))

    def test_pdf_sin_xml_y_csv_se_ignoran(self):
        resultados = procesar_lote(
            [('solo.pdf', b'%PDF'), ('anexo.csv', b'a,b')], self.user
        )
        self.assertEqual(resultados, [])
```

- [ ] **Step 2: Verificar que fallan**

Run: `python manage.py test finanzas.test_carga_masiva -v 2`
Expected: ERROR con `No module named 'finanzas.carga_xml'`

- [ ] **Step 3: Crear `finanzas/carga_xml.py`**

⚠️ **Costura #4:** `crear_gasto_desde_xml` es la única función con lógica contable de HAL9MIL. Adaptarla a lo que Reiki haga con una factura ligada:
- Si Reiki tiene un modelo de gasto/costo por referencia: crear ahí el registro (como abajo, quitando la parte de póliza si no existe).
- Si Reiki no registra gastos: reducir el cuerpo a `xml_obj.procesado = True; xml_obj.save(update_fields=['procesado'])` y eliminar los imports de `GastoReferencia`/`generar_poliza_gasto`. Ajustar los asserts de gasto en los tests.

```python
"""Servicio de carga masiva de XMLs de proveedor.

Recibe ZIPs o archivos sueltos, empareja XML↔PDF por nombre, parsea cada
CFDI, extrae datos aduanales, liga con la Referencia y genera el gasto.
"""
import os
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

from django.core.files.base import ContentFile

from .cfdi_parser import parsear_cfdi_root
from .extractores import buscar_referencia, extraer_datos_aduanales
from .models import GastoReferencia, XMLProveedor      # ← costura #4
from .polizas import generar_poliza_gasto              # ← costura #4


@dataclass
class ResultadoArchivo:
    nombre: str
    estado: str                 # ASIGNADO | PENDIENTE | DUPLICADO | ERROR
    referencia: object = None   # Referencia | None
    detalle: str = ''


def crear_gasto_desde_xml(xml_obj, usuario, tipo='MANIOBRAS'):
    """Crea el GastoReferencia + póliza a partir de un XMLProveedor ya ligado
    a una referencia, y marca el XML como procesado.

    ← costura #4: adaptar el cuerpo a los modelos de Reiki. Lo único
    obligatorio para el resto del flujo es marcar procesado=True.
    """
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

- [ ] **Step 4: Verificar que pasan**

Run: `python manage.py test finanzas.test_carga_masiva -v 2`
Expected: `OK` (8 tests)

Nota: si en Reiki `crear_gasto_desde_xml` requiere datos previos (p. ej. cuentas contables), crearlos en el `setUp` o simplificar la costura #4 como se indicó.

- [ ] **Step 5: Commit**

```bash
git add finanzas/carga_xml.py finanzas/test_carga_masiva.py
git commit -m "feat(finanzas): servicio de procesamiento de lote de XMLs de proveedor"
```

---

### Task 6: Vistas, URLs y templates

**Files:**
- Modify: `finanzas/views.py`
- Modify: `finanzas/urls.py`
- Create: `templates/finanzas/carga_masiva_form.html`
- Create: `templates/finanzas/carga_masiva_resultado.html`
- Create: `templates/finanzas/xml_pendientes.html`
- Test: `finanzas/test_carga_masiva.py` (agregar clases)

**Interfaces:**
- Consumes: `expandir_subidas`, `procesar_lote`, `crear_gasto_desde_xml` (Task 5); `XMLProveedor` (Task 1); decorador de acceso de Reiki (costura #3).
- Produces: URLs con namespace `finanzas`: `carga_masiva_xml` (`xml/carga-masiva/`), `xml_pendientes` (`xml/pendientes/`), `xml_proveedor_ver_pdf` (`xml-proveedor/<int:pk>/pdf/`).

- [ ] **Step 1: Agregar los tests de vistas a `finanzas/test_carga_masiva.py`** (adaptar la creación del usuario con permisos al esquema de Reiki — en HAL9MIL era agregarlo al Group "Finanzas")

```python
from django.urls import reverse


class CargaMasivaViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('fin', password='x')
        # ← costura #3: dar al usuario el acceso que exija el decorador de Reiki
        self.client.force_login(self.user)
        self.ref = Referencia.objects.create(
            num_refe='LCRR1126/26', patente='1656', num_pedimento='6001126',
        )

    def test_get_muestra_formulario(self):
        resp = self.client.get(reverse('finanzas:carga_masiva_xml'))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'finanzas/carga_masiva_form.html')

    def test_post_sin_archivos_redirige_con_error(self):
        resp = self.client.post(reverse('finanzas:carga_masiva_xml'), {})
        self.assertRedirects(resp, reverse('finanzas:carga_masiva_xml'))

    def test_post_con_xml_muestra_resumen(self):
        archivo = SimpleUploadedFile('f1.xml', cfdi_lct())
        resp = self.client.post(
            reverse('finanzas:carga_masiva_xml'), {'archivos': [archivo]}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'finanzas/carga_masiva_resultado.html')
        self.assertEqual(resp.context['conteos']['asignados'], 1)

    def test_post_zip_invalido_redirige_con_error(self):
        malo = SimpleUploadedFile('roto.zip', b'no soy zip')
        resp = self.client.post(
            reverse('finanzas:carga_masiva_xml'), {'archivos': [malo]}
        )
        self.assertRedirects(resp, reverse('finanzas:carga_masiva_xml'))


class XmlPendientesViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('fin', password='x')
        # ← costura #3
        self.client.force_login(self.user)
        self.ref = Referencia.objects.create(
            num_refe='LCRR1126/26', patente='1656', num_pedimento='6001126',
        )

    def _crear_pendiente(self):
        procesar_lote(
            [('p.xml', cfdi_lct(pedimento='1656-9999999', contenedor=''))],
            self.user,
        )
        return XMLProveedor.objects.get()

    def test_lista_pendientes(self):
        self._crear_pendiente()
        resp = self.client.get(reverse('finanzas:xml_pendientes'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['pendientes']), 1)

    def test_asignacion_manual_liga_y_genera_gasto(self):
        xml_obj = self._crear_pendiente()
        resp = self.client.post(reverse('finanzas:xml_pendientes'), {
            'xml_id': xml_obj.pk, 'num_refe': 'LCRR1126/26',
        })
        self.assertRedirects(resp, reverse('finanzas:xml_pendientes'))
        xml_obj.refresh_from_db()
        self.assertEqual(xml_obj.estado_asignacion, 'ASIGNADO')
        self.assertEqual(xml_obj.referencia, self.ref)
        self.assertTrue(xml_obj.procesado)

    def test_referencia_inexistente_no_asigna(self):
        xml_obj = self._crear_pendiente()
        self.client.post(reverse('finanzas:xml_pendientes'), {
            'xml_id': xml_obj.pk, 'num_refe': 'NOEXISTE/99',
        })
        xml_obj.refresh_from_db()
        self.assertEqual(xml_obj.estado_asignacion, 'PENDIENTE')


class XmlProveedorVerPdfViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('fin', password='x')
        # ← costura #3
        self.client.force_login(self.user)

    def _crear_xml(self, con_pdf=True):
        files = [('f1.xml', cfdi_lct())]
        if con_pdf:
            files.append(('f1.pdf', b'%PDF-1.4 fake'))
        procesar_lote(files, self.user)
        return XMLProveedor.objects.get()

    def test_devuelve_pdf(self):
        xml_obj = self._crear_xml()
        resp = self.client.get(reverse(
            'finanzas:xml_proveedor_ver_pdf', kwargs={'pk': xml_obj.pk}
        ))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')

    def test_sin_pdf_da_404(self):
        xml_obj = self._crear_xml(con_pdf=False)
        resp = self.client.get(reverse(
            'finanzas:xml_proveedor_ver_pdf', kwargs={'pk': xml_obj.pk}
        ))
        self.assertEqual(resp.status_code, 404)

    def test_pk_inexistente_da_404(self):
        resp = self.client.get(reverse(
            'finanzas:xml_proveedor_ver_pdf', kwargs={'pk': 999}
        ))
        self.assertEqual(resp.status_code, 404)
```

- [ ] **Step 2: Verificar que fallan**

Run: `python manage.py test finanzas.test_carga_masiva -v 2`
Expected: ERROR con `NoReverseMatch` (las URLs no existen aún)

- [ ] **Step 3: Agregar las vistas a `finanzas/views.py`** (sustituir `@modulo_required('Finanzas')` por el decorador de Reiki — costura #3)

```python
# imports necesarios (agregar los que falten):
import zipfile
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from referencias.models import Referencia               # ← costura #1
from .carga_xml import crear_gasto_desde_xml, expandir_subidas, procesar_lote
from .models import XMLProveedor


# ── Carga masiva de XMLs de proveedor ──────────────────────────────────────

@modulo_required('Finanzas')                             # ← costura #3
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


@modulo_required('Finanzas')                             # ← costura #3
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


@modulo_required('Finanzas')                             # ← costura #3
def xml_proveedor_ver_pdf(request, pk):
    xml_obj = get_object_or_404(XMLProveedor, pk=pk)
    if not xml_obj.pdf_file:
        raise Http404('Este XML no tiene un PDF asociado.')
    return FileResponse(xml_obj.pdf_file.open('rb'), content_type='application/pdf')
```

- [ ] **Step 4: Registrar las URLs en `finanzas/urls.py`** (el urls.py debe tener `app_name = 'finanzas'`)

```python
urlpatterns += [
    path('xml/carga-masiva/', views.carga_masiva_xml, name='carga_masiva_xml'),
    path('xml/pendientes/', views.xml_pendientes, name='xml_pendientes'),
    path('xml-proveedor/<int:pk>/pdf/', views.xml_proveedor_ver_pdf,
         name='xml_proveedor_ver_pdf'),
]
```

- [ ] **Step 5: Crear `templates/finanzas/carga_masiva_form.html`** (adaptar `base.html` y clases CSS al frontend de Reiki)

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

- [ ] **Step 6: Crear `templates/finanzas/carga_masiva_resultado.html`**

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

- [ ] **Step 7: Crear `templates/finanzas/xml_pendientes.html`**

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
          <th class="px-4 py-3">PDF</th>
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
            {% if xml.pdf_file %}
            <a href="{% url 'finanzas:xml_proveedor_ver_pdf' xml.pk %}" target="_blank"
               class="text-sky-600 hover:underline text-xs">Ver PDF</a>
            {% else %}
            <span class="text-slate-300 text-xs">—</span>
            {% endif %}
          </td>
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

- [ ] **Step 8: Verificar que todos los tests pasan**

Run: `python manage.py test finanzas.test_carga_masiva finanzas.test_extractores -v 2`
Expected: `OK` (36 tests entre ambos archivos: 18 en test_extractores y 18 en test_carga_masiva)

- [ ] **Step 9: Commit**

```bash
git add finanzas/views.py finanzas/urls.py templates/finanzas/ finanzas/test_carga_masiva.py
git commit -m "feat(finanzas): vistas de carga masiva, pendientes y visor de PDF"
```

---

### Task 7: Enlaces de navegación

**Files:**
- Modify: `templates/finanzas/dashboard.html` (o el menú/dashboard equivalente en Reiki)

**Interfaces:**
- Consumes: URLs `finanzas:carga_masiva_xml` y `finanzas:xml_pendientes` (Task 6).

- [ ] **Step 1: Agregar los enlaces al dashboard del módulo** (adaptar el markup al dashboard de Reiki; lo esencial son los dos `{% url %}`)

```html
<a href="{% url 'finanzas:carga_masiva_xml' %}"
   class="text-sky-600 hover:underline text-sm">Carga masiva de XMLs</a>
<a href="{% url 'finanzas:xml_pendientes' %}"
   class="text-sky-600 hover:underline text-sm">XMLs pendientes de asignar</a>
```

- [ ] **Step 2: Verificación manual**

Run: `python manage.py runserver` → entrar al dashboard de Finanzas → ambos enlaces navegan a sus vistas.

- [ ] **Step 3: Suite completa y commit**

Run: `python manage.py test finanzas -v 1`
Expected: `OK`

```bash
git add templates/finanzas/dashboard.html
git commit -m "feat(finanzas): enlaces de carga masiva y pendientes en el dashboard"
```

---

## Verificación end-to-end (manual, con datos reales)

1. Conseguir un ZIP real de LCT y uno de APM (o usar los sintéticos de `cfdi_de_prueba.py` guardados como archivos).
2. Crear en la BD una `Referencia` con la patente y pedimento de alguna factura del ZIP, con su `Contenedor`.
3. Subir el ZIP en `/finanzas/xml/carga-masiva/`: la factura correspondiente debe salir **Asignado** con el nº de referencia; las demás **Pendiente** con motivo legible.
4. En `/finanzas/xml/pendientes/`: abrir el "Ver PDF" de una pendiente (debe abrir en pestaña nueva), asignarla manualmente por nº de referencia y verificar que desaparece de la lista.
5. Volver a subir el mismo ZIP: todo debe salir **Duplicado** y no duplicar registros.

## Fuera de alcance (igual que en HAL9MIL)

- Lectura de los CSV "anexo" de LCT (el XML es autosuficiente).
- Procesamiento asíncrono / colas.
- Otros RFCs de proveedor — el dispatcher `_EXTRACTORES` permite agregarlos después con solo una función nueva y una entrada al dict.
