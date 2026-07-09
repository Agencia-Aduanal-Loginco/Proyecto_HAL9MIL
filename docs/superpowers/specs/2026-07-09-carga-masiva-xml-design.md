# Carga masiva de XMLs de proveedor (módulo Finanzas)

**Fecha:** 2026-07-09
**Estado:** Aprobado por el usuario

## Objetivo

Permitir a los usuarios del módulo Finanzas subir en bloque las facturas CFDI
que entregan las terminales portuarias (ZIP descargado del portal del proveedor
o los archivos sueltos ya descomprimidos), extraer de cada XML los datos
aduanales (patente, pedimento, contenedor) y ligar automáticamente cada factura
con su `referencias.Referencia`, generando el gasto correspondiente.

## Proveedores soportados

### LCT — L C Terminal Portuaria de Contenedores (`RFC LCT030408U39`)

CFDI 4.0 con addenda Diverza. Los datos vienen dentro de `<cfdi:Addenda>` como
elementos `<dvz:datosExtra atributo="..." valor="..."/>`:

| Dato | Atributo | Ejemplo real | Nota |
|---|---|---|---|
| Patente | `LeyendaEspecial15` | `1656` | |
| Pedimento | `LeyendaEspecial16` | `1656-6001126` | Formato `patente-pedimento`; se usa la parte después del guión |
| Contenedor | `LeyendaEspecial25` | `CSNU 879377 0` | Trae espacios; se normaliza quitándolos → `CSNU8793770` |
| BL | `LeyendaEspecial20` | `COSU6501186800` | Informativo |

El namespace `dvz` puede variar de URI; la búsqueda debe hacerse por
*local-name* `datosExtra` y su atributo `atributo`, no por URI fija.

El ZIP de LCT trae por factura: XML, PDF y un CSV "anexo" (`L-<folio>anexo.csv`).
El CSV se ignora (el XML es autosuficiente).

### APM — APM Terminals Lázaro Cárdenas (`RFC ATL120106DC6`)

CFDI 4.0 con addenda Edicom `<customized><APMTLZC>`:

| Dato | Fuente | Ejemplo real | Nota |
|---|---|---|---|
| Pedimento | `<PEDIMENTO>` | `6000517` | Ya viene sin patente |
| Patente | `<AGENTEADUANAL>` | `1627/LUIS FELIPE VAZQUEZ DIAZ` | Se toma el prefijo antes de `/` |
| Contenedor | `Descripcion` de cada `<cfdi:Concepto>` | `BEAU4729066-MUELLAJE 40 HC` | Prefijo con regex `^([A-Z]{4}\d{7})-`; consistente en todos los conceptos de la factura |
| BL | `<CONOCIMIENTO>` | `HLCUSHA2604CHSA6` | Informativo |

El ZIP de APM trae por factura: XML y PDF (`C<folio>.xml` / `C<folio>.pdf`).

### RFC desconocido

El XML se guarda como `XMLProveedor` en estado PENDIENTE con motivo
"proveedor no soportado"; se asigna manualmente.

## Lógica de coincidencia (validada contra datos reales)

Los identificadores individuales **no** son únicos en la BD:

- Un mismo contenedor aparece en varias referencias (se reutilizan con el
  tiempo): `BEAU4729066` → `LCLF0417` y `LCLF0517/26`.
- Un mismo número de pedimento existe bajo dos patentes:
  `6000517` → `LCLF0517/26` (1627) y `LCRR0517/26` (1656).

Cascada de coincidencia:

1. **`(patente, num_pedimento)`** contra `Referencia` — si da exactamente una,
   es la candidata.
2. **Verificación cruzada con contenedor** (normalizado sin espacios) contra
   `referencias.Contenedor`: si el contenedor existe en la BD y apunta a una
   referencia distinta de la candidata, el XML queda PENDIENTE con motivo
   "datos contradictorios" en lugar de ligarse mal. Si el contenedor no existe
   en la BD o coincide, se liga.
3. **Fallback por contenedor**: si no hay pedimento/patente utilizables, se
   liga solo si el contenedor da **exactamente una** referencia.
4. Cualquier otro caso (0 ó >1 candidatas) → PENDIENTE con motivo descriptivo.

## Cambios de modelo (`finanzas.XMLProveedor`)

- `pdf_file = FileField(upload_to='xmls_proveedores/%Y/%m/', null=True, blank=True)`
  — se empareja con el XML por nombre de archivo (mismo *stem*, extensión `.pdf`).
- `estado_asignacion = CharField(choices=[ASIGNADO, PENDIENTE], ...)`
- `motivo_pendiente = CharField(blank=True)` — texto corto legible
  ("sin referencia para patente 1656 / pedimento 6001126", "contenedor
  contradice pedimento", "proveedor no soportado", etc.)

Migración incluida. Los registros existentes quedan ASIGNADO si tienen
referencia, PENDIENTE si no.

## Componentes nuevos

### `finanzas/extractores.py`

- `extraer_datos_aduanales(xml_root) -> DatosAduanales | None` — despacha por
  RFC del emisor a un extractor registrado
  (`{'LCT030408U39': _extraer_lct, 'ATL120106DC6': _extraer_apm}`).
- `DatosAduanales`: dataclass con `patente`, `pedimento`, `contenedor`, `bl`
  (todos opcionales, normalizados).
- `buscar_referencia(datos) -> (Referencia | None, motivo: str)` — implementa
  la cascada de coincidencia.

Parsing con `xml.etree.ElementTree` (stdlib), consistente con
`finanzas/cfdi_parser.py`, que se reutiliza para los datos fiscales generales
(UUID, emisor, totales…).

### Vistas (todas con `@modulo_required('Finanzas')`)

1. **`carga_masiva_xml`** (`GET` formulario / `POST` procesa):
   - Acepta **un ZIP** o **múltiples archivos sueltos** en el mismo campo
     (`<input multiple>`). Si es ZIP se descomprime en memoria/tmp.
   - Se procesan los `.xml`; los `.pdf` se emparejan por nombre; `.csv` y
     otros se ignoran.
   - Por cada XML: parsear CFDI → si el UUID fiscal ya existe se **omite** y
     se reporta como duplicado → extraer datos aduanales → buscar referencia →
     crear `XMLProveedor` (ASIGNADO o PENDIENTE) → si quedó ASIGNADO, crear
     `GastoReferencia` tipo `MANIOBRAS` con el total (misma lógica que
     `subir_xml_proveedor`, que se refactoriza a una función compartida).
   - Un XML ilegible/corrupto no aborta el lote: se reporta como error y se
     continúa.
   - Procesamiento síncrono en la petición (lotes esperados: decenas).
2. **Resumen de resultados** (respuesta del POST): tabla archivo → resultado
   (referencia asignada / pendiente + motivo / duplicado / error).
3. **`xml_pendientes`**: lista de `XMLProveedor` PENDIENTES con los datos
   extraídos visibles y un buscador de referencia para asignar manualmente.
   Al asignar, se liga y se genera su `GastoReferencia` igual que en la carga.

### URLs y navegación

- `finanzas/xml/carga-masiva/` y `finanzas/xml/pendientes/`.
- Enlaces desde el dashboard/menú del módulo Finanzas.

## Manejo de errores

- ZIP inválido o sin XMLs → mensaje de error, no se guarda nada.
- XML no-CFDI o corrupto → fila de error en el resumen, el resto continúa.
- Duplicado por `uuid_fiscal` → se omite (no se sobrescribe) y se reporta.
- PDF sin XML correspondiente → se ignora.
- Límite de tamaño de subida: el default del proyecto (sin cambio).

## Pruebas (TDD, con los XMLs reales como fixtures)

- Extractor LCT: patente/pedimento/contenedor correctos, contenedor sin
  espacios, pedimento sin prefijo de patente.
- Extractor APM: pedimento, patente desde `AGENTEADUANAL`, contenedor desde
  descripciones de conceptos.
- Cascada: match único, pedimento ambiguo entre patentes, contenedor
  contradictorio → PENDIENTE, fallback por contenedor único, RFC desconocido.
- Vista: carga de ZIP, carga de archivos sueltos, duplicados omitidos,
  emparejado de PDF, creación de `GastoReferencia`, acceso restringido al
  grupo Finanzas.
- Asignación manual desde pendientes.

## Fuera de alcance

- Lectura de los CSV "anexo" de LCT.
- Procesamiento asíncrono / colas.
- Otros RFCs de proveedor (la estructura de extractores permite agregarlos
  después).
