# Complementos de Pago (CFDI tipo P) — módulo Finanzas

**Fecha:** 2026-07-15
**Estado:** Aprobado por el usuario

## Objetivo

Hoy, cuando se sube un XML de proveedor con `TipoDeComprobante = P` (Complemento
de Pago), el sistema lo trata como si fuera una factura más: lo guarda como
`XMLProveedor` con `tipo_comprobante='P'` y, al no traer datos aduanales en el
cuerpo del comprobante, casi siempre termina en "XML Pendientes" con motivo
"sin datos aduanales en el XML" — un falso pendiente, porque un complemento de
pago nunca va a tener esos datos.

Un complemento de pago no es una factura: es la prueba de que una factura ya
subida (`XMLProveedor` tipo `I`) fue pagada. Su nodo
`<pago20:DoctoRelacionado IdDocumento="...">` (o `pago10:DoctoRelacionado` en
CFDI 3.3) trae el UUID de la factura pagada. El objetivo de este cambio es:

1. Dejar de tratar los complementos de pago como facturas pendientes.
2. Ligar automáticamente cada complemento con la factura (`XMLProveedor`) que
   paga, buscando por ese UUID — sin importar si el complemento llega antes o
   después de que la factura se suba al sistema.
3. Cuando están ligados, mostrar en la fila de la factura (no en una fila
   aparte) que ya fue pagada, con su propio XML/PDF de respaldo.
4. Si no se puede ligar (la factura aún no existe en HAL9MIL), dejarlo en una
   cola de pendientes con opción de ligarlo a mano después.

Aplica a los tres puntos donde hoy se suben XMLs de proveedor: carga
individual dentro de una referencia, carga masiva, y "Carga de Facturas"
(`carga_xml_cliente`, abierta a cualquier usuario autenticado).

## Modelo de datos

### `finanzas.ComplementoPago` (nuevo)

```python
class ComplementoPago(models.Model):
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
        on_delete=models.SET_NULL,
    )  # solo si se subió desde el panel de una referencia específica
    cargado_en = models.DateTimeField(auto_now_add=True)
```

`factura` es **FK, no OneToOne**: una misma factura puede acumular varios
complementos a lo largo del tiempo (pagos parciales / parcialidades), aunque
lo normal es que cada complemento pague exactamente una factura. Un
complemento que trae más de un `DoctoRelacionado` (pagar varias facturas de
una vez) es infrecuente en la operación de Loginco; en vez de repartir el
pago automáticamente entre varias facturas, se marca `estado='REVISION'` y se
resuelve a mano.

Migración nueva para este modelo. No se modifica `XMLProveedor`.

## Parseo (`finanzas/cfdi_parser.py`)

Nueva función `parsear_complemento_pago(root) -> list[dict]`:

- Detecta el complemento de pago buscando `cfdi:Complemento/pago20:Pagos` o,
  si no existe, `cfdi:Complemento/pago10:Pagos` (mismo patrón dual-namespace
  ya usado para CFDI 3.3/4.0 en este archivo).
- Por cada `pago20:Pago/pago20:DoctoRelacionado` (o `pago10:...` equivalente),
  extrae `IdDocumento` (UUID de la factura pagada), `ImpPagado` y `MonedaP`.
- Devuelve la lista completa de `DoctoRelacionado` encontrados (normalmente
  length 1). El llamador decide si es el caso simple o `REVISION`.
- Si el nodo `Pagos` no existe pese a que `TipoDeComprobante == 'P'`, se
  lanza `ValueError` (complemento malformado), igual que otros errores de
  parseo en este módulo.

`parsear_cfdi_root` no cambia: sigue extrayendo `uuid`, `fecha`, `rfc_emisor`,
etc. de cualquier CFDI, incluyendo los de tipo P (el UUID del propio
complemento sale de su `TimbreFiscalDigital`, igual que cualquier otro CFDI).

## Flujo de carga (los 3 puntos de entrada)

Nuevo módulo `finanzas/complementos_pago.py` con la lógica compartida:

- `procesar_complemento(nombre, xml_bytes, pdf_bytes, referencia_sugerida=None) -> ComplementoPago`:
  parsea el CFDI y sus `DoctoRelacionado`; si UUID de complemento ya existe,
  lanza el mismo tipo de señal de "duplicado" que ya usa `carga_xml.py`; si
  hay más de un `DoctoRelacionado`, guarda con `estado='REVISION'`; si hay
  exactamente uno, busca `XMLProveedor.objects.filter(uuid_fiscal=...)` y crea
  el `ComplementoPago` ya sea `IDENTIFICADO` (con `factura` ligada) o
  `PENDIENTE`.
- `conciliar_pendientes(xml_obj)`: se llama después de guardar **cualquier**
  `XMLProveedor` nuevo (en los 3 flujos). Busca `ComplementoPago` con
  `estado='PENDIENTE'` y `uuid_factura_relacionada == xml_obj.uuid_fiscal`, y
  los liga (`factura=xml_obj`, `estado='IDENTIFICADO'`). Cubre el caso en que
  el complemento llegó antes que la factura.

Cambios por punto de entrada:

1. **`finanzas/carga_xml.py`** (`_procesar_uno`, usado por carga masiva y por
   `carga_xml_cliente`): si el CFDI parseado tiene `tipo == 'P'`, se despacha a
   `procesar_complemento` en vez de crear `XMLProveedor`. Si es `I`/`E` como
   hoy, después de guardarlo se llama `conciliar_pendientes(xml_obj)`. El
   emparejamiento XML↔PDF por nombre de archivo, que ya existe en
   `_recolectar`, se reutiliza sin cambios (aplica igual a complementos).

2. **`finanzas/views.py` → `subir_xml_proveedor`** (panel dentro de una
   referencia, imagen 2): se agrega un campo de PDF opcional al formulario
   (hoy solo acepta XML). Mismo branching por `tipo`; si es P y no matchea,
   `referencia_sugerida` se llena con la referencia del panel (ayuda al match
   manual después). Tras guardar una factura nueva aquí, también se llama
   `conciliar_pendientes`.

3. **`carga_xml_cliente`** ya comparte código con carga masiva vía
   `procesar_lote`, así que no requiere cambios propios más allá de los de
   `carga_xml.py`.

## Interfaz

### Fila fusionada (tabla de XMLs en `referencia_estado.html`)

Si `xml_obj.complementos_pago.exists()`, las columnas **TIPO** y
**PROCESADO** de la fila de esa factura muestran **"COM. PAGO"** en vez de
"I"/checkmark — esto es solo de presentación en el template; el campo real
`XMLProveedor.tipo_comprobante` de la factura no se modifica. La columna PDF
conserva el link al PDF de la factura y agrega un link "Ver PDF pago" por
cada `ComplementoPago` ligado (nueva vista `complemento_pago_ver_pdf`, mismo
patrón `FileResponse` que `xml_proveedor_ver_pdf`).

### Vista nueva: Complementos de pago no identificados

`finanzas/views.py::complementos_pago_pendientes`, mismo patrón que
`xml_pendientes`: lista `ComplementoPago` con `estado` PENDIENTE o REVISION
(emisor, monto pagado, fecha, UUID de factura buscado). Acción manual
"Ligar": se captura un número de referencia, se listan las facturas
(`XMLProveedor`) de esa referencia para elegir la correcta, y al confirmar se
liga (`factura` seteada, `estado='IDENTIFICADO'`).

Enlaces hacia esta vista en los mismos lugares donde hoy se enlaza
"XML Pendientes" (no hay entrada en el sidebar principal): tarjeta del
dashboard de Finanzas, y en `carga_cliente_form.html` /
`carga_masiva_resultado.html`.

## Manejo de errores

- Complemento con `uuid_complemento` duplicado → se omite, se reporta igual
  que un XML duplicado hoy.
- Complemento sin nodo `Pagos` pese a `TipoDeComprobante='P'` → error de
  parseo, no aborta el resto del lote en carga masiva.
- Complemento con >1 `DoctoRelacionado` → `estado='REVISION'`, visible en la
  cola de pendientes, no se auto-liga.
- Complemento sin PDF adjunto → válido, campo queda vacío (igual que
  `XMLProveedor.pdf_file` hoy).

## Pruebas (TDD)

- `parsear_complemento_pago`: fixtures CFDI de pago namespace `pago20` y
  `pago10`; caso con un `DoctoRelacionado`; caso con más de uno.
- Conciliación: complemento llega antes que la factura (queda PENDIENTE,
  luego se sube la factura y se liga solo); factura ya existe y el
  complemento liga de inmediato al subirse.
- Los 3 puntos de entrada: un XML tipo P no crea `XMLProveedor`.
- Vista de pendientes: listado, filtro, acción de ligar manualmente.
- Template: fila con complemento ligado muestra "COM. PAGO" en TIPO y
  PROCESADO; fila sin complemento no cambia.

## Fuera de alcance

- Reparto automático de un complemento que paga varias facturas a la vez
  (`estado='REVISION'`, se resuelve a mano). La acción manual "Ligar" también
  aplica a estas filas, pero solo permite fijar **una** `factura` (limitación
  del FK único del modelo): sirve para marcar cuál es la factura principal,
  no para repartir el monto pagado entre varias facturas.
- Cambios al modelo `XMLProveedor` o a su lógica de asignación por datos
  aduanales (`extractores.py`), que sigue aplicando solo a facturas tipo I/E.
