# Carga de Facturas de Cliente → XML Pendientes — Diseño

**Fecha:** 2026-07-11
**Estado:** Aprobado por el usuario (brainstorming 2026-07-11)

## Problema

Llegan facturas CFDI (XML + PDF) cuyo **receptor es el cliente** de la agencia
(no la agencia misma). Hoy no hay un apartado dedicado para ingresarlas: la
carga masiva existente está pensada para proveedores del puerto (LCT/APM) que
matchean automáticamente por contenedor/patente/pedimento. Estas facturas de
cliente necesitan:

1. Un apartado propio de carga (varios XML + PDF a la vez, sin ZIP).
2. Caer al listado de **XML pendientes** para asignación manual.
3. Que el **RFC del receptor** (el cliente) se capture y se use como apoyo
   para anexar el XML a la referencia correcta posteriormente.

## Decisiones tomadas (con el usuario)

| Decisión | Elección |
|----------|----------|
| Tipo de XMLs | Facturas dirigidas a los clientes (receptor = cliente) |
| Efecto al asignar a referencia | Genera gasto (`GastoReferencia` + póliza), igual que LCT/APM |
| Forma de carga | Varios archivos a la vez (XMLs y PDFs sueltos, emparejados por nombre), sin ZIP |
| Uso del RFC receptor | Mostrar RFC + cliente detectado en pendientes, y sugerir referencias de ese cliente en un selector |
| Enfoque | Reutilizar el pipeline existente de carga masiva (opción A) |

## Arquitectura

**Sin modelos nuevos ni migraciones.** `XMLProveedor` ya tiene `rfc_receptor`
(el parser `cfdi_parser.parsear_cfdi_root` ya lo extrae) y el pipeline
`carga_xml.procesar_lote` ya cubre: parseo, dedupe por UUID, pareo de PDF por
nombre de archivo (stem), guardado y estado `PENDIENTE` con motivo cuando el
emisor no es un proveedor soportado (LCT/APM) — que es exactamente el caso de
estas facturas.

### Componente 1: Vista de carga (`carga_xml_cliente`)

- **URL:** `/finanzas/xml/carga-cliente/` — nombre `finanzas:carga_xml_cliente`.
- **Permiso:** `@modulo_required('Finanzas')` (convención del módulo).
- **GET:** renderiza `templates/finanzas/carga_cliente_form.html` — formulario
  con `<input type="file" multiple>` que acepta `.xml` y `.pdf`.
- **POST:** toma `request.FILES.getlist('archivos')`, valida que haya al menos
  un archivo, llama `expandir_subidas(...)` + `procesar_lote(files, request.user)`
  (mismo flujo que `carga_masiva_xml`; los ZIP no se anuncian en la UI pero el
  pipeline los tolera). Renderiza el resumen con conteos
  (asignados/pendientes/duplicados/errores) y enlace al listado de pendientes.
- El resumen reutiliza directamente el template existente
  `templates/finanzas/carga_masiva_resultado.html` (mismo contexto:
  `resultados` + `conteos`).

### Componente 2: Mejoras al listado de pendientes (`xml_pendientes`)

- **Columnas nuevas** en la tabla (entre "Emisor" y "Motivo"): **RFC receptor**
  y **Cliente** (nombre del cliente detectado).
- **Detección de cliente:** en la vista, para el conjunto de pendientes se
  resuelve `Cliente.objects.filter(rfc__in=rfcs)` en una sola consulta y se
  anota cada pendiente con su cliente (o `None`). RFC vacío o sin match →
  columna muestra "—".
- **Selector de referencias sugeridas:** si hay cliente detectado, junto al
  campo libre `num_refe` se muestra un `<select>` con las referencias de ese
  cliente (`Referencia.objects.filter(cve_cliente=cliente.cve_cliente)`
  ordenadas por `-fecha_pago`, límite 15). Al elegir una opción, se llena el
  campo `num_refe` (JS mínimo inline, mismo patrón que `toggleDetalle` en
  cobranza). El POST de asignación no cambia: sigue siendo `xml_id` +
  `num_refe`.
- **Asignación:** sin cambios — el POST existente ya asigna la referencia y
  genera gasto si `tipo_comprobante == 'I'`.

### Componente 3: Enlaces de acceso

- Tarjeta/enlace "Cargar facturas de cliente" en el dashboard de Finanzas,
  junto a los enlaces existentes de carga masiva y pendientes.
- Enlace desde la página de pendientes hacia el nuevo apartado (y viceversa
  desde el resumen de carga).

## Flujo de datos

```
Usuario sube N archivos (.xml/.pdf)
  → expandir_subidas → [{nombre, xml, pdf, stem}]
  → procesar_lote → por archivo:
      parsear CFDI (captura rfc_receptor) → dedupe UUID
      → extractores no reconocen emisor → XMLProveedor PENDIENTE
  → resumen de carga
Usuario abre XML pendientes
  → ve RFC receptor + cliente detectado + selector de referencias del cliente
  → asigna → XMLProveedor ASIGNADO + GastoReferencia + póliza (tipo I)
```

## Manejo de errores

Ya cubierto por el pipeline (`ResultadoArchivo`): XML corrupto → renglón ERROR
en el resumen; UUID duplicado → DUPLICADO; PDF sin XML del mismo nombre → se
ignora. Casos nuevos: sin archivos seleccionados → mensaje de error y redirect
(igual que carga masiva); RFC receptor sin cliente en catálogo → sin sugerencias,
captura libre disponible.

## Pruebas

En `finanzas/test_carga_masiva.py` (o archivo hermano `test_carga_cliente.py`):

1. GET del formulario responde 200 para usuario del grupo Finanzas; usuario
   sin grupo es redirigido.
2. POST con un XML de emisor no soportado → `XMLProveedor` queda PENDIENTE con
   `rfc_receptor` correcto y PDF emparejado.
3. POST del mismo XML dos veces → segundo reporta DUPLICADO.
4. `xml_pendientes` muestra RFC receptor y nombre del cliente cuando
   `Cliente.rfc` coincide; muestra "—" cuando no.
5. `xml_pendientes` incluye las referencias del cliente detectado en el
   selector (y no las de otros clientes).
6. Asignación vía POST con `num_refe` de una referencia sugerida → ASIGNADO y
   gasto generado (comprobante tipo I).

## Fuera de alcance

- Asignación automática por RFC receptor (se decidió sugerir, no auto-asignar).
- Soporte de ZIP en la UI del nuevo apartado.
- Cambios al modelo `XMLProveedor` o al parser CFDI.
- Distinguir contablemente estas facturas de las de proveedor (generan gasto
  igual que LCT/APM, por decisión del usuario).
