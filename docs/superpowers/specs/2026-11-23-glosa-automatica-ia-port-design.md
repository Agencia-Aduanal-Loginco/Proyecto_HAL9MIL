# Especificación de Diseño: Port de la Glosa Automática con IA (Reiki -> HAL9MIL Loginco)

## 1. Resumen y objetivo

Llevar a HAL9MIL (Loginco, patentes `1627`, `1656`, `1927`) la app `glosa` que hoy funciona
en `proyecto_hal9mil_reiki` (patente `3772`). El port incluye todo lo que existe a la fecha
(2026-11-23):

1. Expediente documental por referencia (subida, versiones, reemplazo).
2. Clasificación automática de documentos con Jev (TypeSafe System One), con respaldo por
   palabras clave.
3. Extracción de datos con Claude (JSON outputs con esquema fijo por tipo de documento) y
   validaciones en código.
4. Cruce determinista entre pre-forma, documentos y CASA (contenedores, peso, proveedor,
   ED, certificados) y cruce por partida (factura, lista de empaque, NOM, permisos).
5. Pre-forma anotada en PDF con las discrepancias.
6. Flujo de revisión con visto bueno del grupo `Glosador` (aprobar, rechazar, reabrir) e
   historial de eventos.
7. Indicador "Glosa IA" en la lista operativa `/glosa/` con filtro y KPIs.

Código fuente de referencia: `/home/tony/Developer/proyecto_hal9mil_reiki/glosa/`.
Specs de origen en Reiki (`docs/superpowers/specs/`):

- `2026-11-04-glosa-automatica-jev-ia-design.md`
- `2026-11-19-glosa-preforma-cruce-documentos-design.md`
- `2026-11-22-glosa-correcciones-revision-glosador-design.md`
- `2026-11-23-glosa-cruce-partidas-design.md`

Revisión 2026-11-23 (noche): la primera implementación en Loginco reescribió con una versión
simplificada la extracción, la clasificación, las vistas, la corrección, la anotación y la
plantilla de detalle. Esos módulos se volvieron a portar desde Reiki con los ajustes de este
documento, y se agregaron las mejoras de la sección 12. Las decisiones de Tony están en 12.1.

Este documento es la fuente de verdad del port. Donde el código de Reiki y los specs de
Reiki difieren, manda el código (los specs anteriores describen estados intermedios; por
ejemplo, el spec del 2026-11-19 dice que un cruce limpio pone `APROBADO`, pero el código
actual pone `EN_REVISION`).

### Principio de reparto

| Paso | Quién | Por qué |
|---|---|---|
| Clasificar el tipo de documento | Jev | Solo elige entre opciones fijas, con probabilidad. |
| Leer valores del documento | Claude | Jev no extrae texto libre. |
| Decidir si hay discrepancia | Python determinista | Reproducible, probado con tests, sin costo. |

Ningún LLM decide si hay discrepancia.

### Fuera de alcance

- Cliente AAALAC Fonatrac funcional (ver 9.1: en Reiki es un placeholder).
- Tarifa electrónica real (`services_tarifas.py` en Reiki devuelve valores fijos y no lo usa
  ninguna vista).
- Integración Elektra, `ArticuloPedimento` y demás cambios de Reiki que no son de glosa.
- Notificaciones al glosador.

---

## 2. Diferencias entre proyectos que el port debe resolver

| Tema | Reiki | HAL9MIL Loginco | Acción en el port |
|---|---|---|---|
| Patentes | Una (`3772`) | Tres (`1627`, `1656`, `1927`) | Nada de texto fijo de patente o agencia. Ver 6.4. |
| `Referencia.peso_bruto` | Kilogramos (de `SAAIO_PEDIME.PES_BRUT`) | **Toneladas** (de `CTRAO_EMBAR.PES_BRUT`), con propiedad `peso_kg` | En el cruce, el respaldo CASA usa `ref.peso_kg`, no `ref.peso_bruto`. Ver 5.2. |
| App `glosa` | Existe, migraciones `0001`-`0004` | No existe | Crear la app con migraciones nuevas consolidadas. Ver 3.4. |
| `requirements.txt` | Tiene `pymupdf`, `Pillow` | No los tiene | Agregar `pymupdf>=1.24` y `Pillow>=10.0`. `anthropic` y `requests` ya están. |
| `settings.IA_HABILITADA`, `ANTHROPIC_API_KEY` | Existen | Ya existen (`settings.py` líneas 128-129) | Sin cambio. |
| Storage de media | DO Spaces privado, URL firmada 1 h | Igual (`hal9mil/storage_backends.py`, `MediaStorage`) | `FileField(upload_to='glosa_documentos/')` sin cambios. |
| `GlosaRegistro` | `referencias.GlosaRegistro` | Existe con los mismos campos (`fecha_conclusion`, `usuario_conclusion`, `nota`) | `glosa_aprobar` lo concluye igual. Verificar nombres de campo antes de codificar. |
| Lista `/glosa/` | Tiene columna, filtro y KPIs "Glosa IA" | No | Portar `_anotar_glosa_ia` y la plantilla. Ver 7. |
| Relaciones de `Referencia` | `contenedores`, `guias`, `glosas` | Mismos `related_name` | Sin cambio. |

---

## 3. Modelo de datos (`glosa/models.py`)

### 3.1 `DocumentoReferencia`

| Campo | Tipo | Notas |
|---|---|---|
| `referencia` | `FK('referencias.Referencia', PROTECT, related_name='documentos_glosa')` | |
| `archivo` | `FileField(upload_to='glosa_documentos/')` | Spaces privado. |
| `tipo_documento` | `CharField(30, choices=TIPO_CHOICES, default='OTROS')` | |
| `confianza_ia` | `DecimalField(5, 4, null)` | Probabilidad de la opción elegida por Jev; `1.0000` si es manual; `None` sin IA. |
| `datos_extraidos` | `JSONField(default=dict)` | Campos del documento al primer nivel + `meta`. Ver 4.2. |
| `clasificacion_ia` | `JSONField(default=dict)` | `estado` (`OK`/`REVISION`/`SIN_IA`/`ERROR`/`MANUAL`), `sugerencia`, `probabilidades`, `confidence`, `noul_conocido`, `modelo`, `entrada`, `error`, `fuente`, `sugerencia_ia`, `origen`. |
| `creado_en` | `DateTimeField(auto_now_add)` | |
| `activo` | `BooleanField(default=True, db_index=True)` | Solo los activos entran al cruce. |
| `reemplazado_por` | `FK('self', null, SET_NULL, related_name='versiones_anteriores')` | |
| `reemplazado_en` | `DateTimeField(null)` | |
| `subido_por` | `FK(User, null, SET_NULL)` | |
| `motivo_reemplazo` | `TextField(blank=True)` | |
| `hash_sha256` | `CharField(64, blank, db_index)` | SHA-256 del archivo. Detecta duplicados y reutiliza extracciones (12.2). |
| `procesamiento_estado` | `CharField(12)`: `PENDIENTE`, `PROCESANDO`, `LISTO`, `ERROR`; default `LISTO` | Clasificación y extracción en segundo plano (4.9). |
| `procesamiento_inicio` | `DateTimeField(null)` | Para detectar documentos atorados. |
| `procesamiento_error` | `TextField(blank=True)` | Mensaje si el procesamiento falló. |

`TIPO_CHOICES`: `FACTURA`, `BL`, `CARTA_TECNICA`, `LISTA_EMPAQUE`, `PERMISOS`,
`CERTIFICADOS`, `PREFORMA`, `OTROS`, `OTRO_SIN_ESQUEMA`.

Propiedades: `estado_clasificacion` (lee `clasificacion_ia['estado']`), `requiere_revision`
(`True` si el estado es `REVISION`, `SIN_IA` o `ERROR`) y `en_proceso` (`PENDIENTE` o
`PROCESANDO`).

`clasificacion_ia` guarda además `tipo_ia` (tipo que dejó Jev, se conserva al cambiar el tipo a
mano) y `uso` (tokens que reporta Jev). Sirven para la métrica de precisión (12.5).

`OTRO_SIN_ESQUEMA` solo se asigna a mano: Jev no lo ofrece y no tiene esquema de
extracción (queda `NO_APLICA`).

### 3.2 `GlosaAutomaticaResultado`

| Campo | Tipo |
|---|---|
| `referencia` | `OneToOne('referencias.Referencia', PROTECT, related_name='resultado_glosa')` |
| `estado_revision` | `CharField(30)`: `PENDIENTE`, `EN_REVISION`, `APROBADO`, `RECHAZADO`, `DISCREPANCIAS_DETECTADAS`; default `PENDIENTE` |
| `eta_aaalac` | `DateField(null)` |
| `datos_comparacion` | `JSONField(default=dict)`: salida de `ejecutar_cruce` (ver 5.1) |
| `aprobado_por` | `FK(User, null, SET_NULL, related_name='glosas_aprobadas')` |
| `aprobado_en` | `DateTimeField(null)` |
| `comentario_glosador` | `TextField(blank=True)` |
| `actualizado_en` | `DateTimeField(auto_now)` |

### 3.3 `GlosaEvento`

| Campo | Tipo |
|---|---|
| `referencia` | `FK('referencias.Referencia', PROTECT, related_name='eventos_glosa')` |
| `tipo` | `SUBIDA`, `REEMPLAZO`, `CAMBIO_TIPO`, `CRUCE`, `RECHAZO`, `APROBACION`, `REAPERTURA` |
| `usuario` | `FK(User, null, SET_NULL)` |
| `creado_en` | `auto_now_add` |
| `documento` | `FK(DocumentoReferencia, null, SET_NULL)` |
| `documento_anterior` | `FK(DocumentoReferencia, null, SET_NULL, related_name='+')` |
| `estado_resultante` | `CharField(30)` |
| `discrepancias` | `JSONField(default=list)` |
| `comentario` | `TextField(blank=True)` |

`Meta.ordering = ['-creado_en']`.

### 3.4 Migraciones

No copiar las cuatro migraciones de Reiki. En Loginco no hay datos previos, así que:

- `0001_initial`: los tres modelos en su forma final, con `OTRO_SIN_ESQUEMA` incluido en
  `TIPO_CHOICES`. (En Reiki el modelo tiene `OTRO_SIN_ESQUEMA` pero ninguna migración lo
  registra; hay un `makemigrations` pendiente allá. Avisar a Tony por separado.)
- `0002_grupo_glosador`: `RunPython` que hace `Group.objects.get_or_create(name='Glosador')`,
  con reverso no-op. Se omite la parte de Reiki que movía `APROBADO` a `EN_REVISION`
  (solo aplicaba a datos viejos de Reiki).
- Dependencia: la última migración de `referencias` en Loginco.
- `0003_documento_hash_procesamiento`: `hash_sha256` y los tres campos de procesamiento.

### 3.5 Admin

En Reiki `glosa/admin.py` está vacío. En el port, registrar los tres modelos en modo de
consulta (list_display con referencia, tipo, estado, fechas; `GlosaEvento` de solo lectura).
Es la única adición respecto a Reiki y es opcional.

---

## 4. Servicios

Copiar los módulos con los cambios indicados. Los nombres de función se conservan porque las
vistas y las pruebas dependen de ellos.

### 4.1 `services_jev_ia.py`: clasificación

Contrato de la API (https://docs.typesafe.ai/api/system-one):

- `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer <API_KEY>`.
- Body: `{"model", "state", "questions", "assets"?}`. Respuesta con `answers`.
- Si hay imagen: `assets = {ref: {"type": "image", "format": "png", "data_base64": ...}}`,
  `state = {"documento": ref}` y header `TypeSafe-Danger-Zone-Images: true`.
- Timeout 30 s.

Lectura del archivo (`leer_documento`):

- PDF con capa de texto (>= 200 caracteres): se manda el texto (tope 12,000 caracteres).
- PDF escaneado: se rasteriza la página 1 a PNG con PyMuPDF respetando límites de Jev (lado
  mayor 1568 px, lado menor >= 280 px, máximo 2,458,624 px).
- Imagen (jpg/png): Pillow, `exif_transpose`, RGB, `thumbnail` a 1568 px, PNG.
- XML/TXT: texto UTF-8.

Ajuste Loginco: el criterio de `PREFORMA` incluye el pedimento impreso de CASA ("ANEXO DEL
PEDIMENTO", "REF:", pago electrónico), porque en Loginco se sube tanto el borrador como el
pedimento ya pagado. Las palabras clave de respaldo incluyen `ANEXO DEL PEDIMENTO`.

Preguntas (patrón "companion noul"):

- `tipo_documento`: `choice` con 7 opciones (`FACTURA`, `BL`, `LISTA_EMPAQUE`, `PREFORMA`,
  `CARTA_TECNICA`, `PERMISOS`, `CERTIFICADOS`). Cada criterio describe qué es, señales y qué
  no es, en español e inglés. `OTROS` no va como opción.
- `es_documento_conocido`: `noul` que pregunta si es alguno de esos tipos.

Decisión (`_decidir`):

| Condición | Resultado |
|---|---|
| `noul < 0.3` | `OTROS`, estado `OK`, confianza `1 - noul` |
| `noul > 0.7` y prob. elegida >= 0.6 y (`confidence` nulo o >= 0.4) | tipo elegido, `OK` |
| otro caso | tipo elegido, `REVISION` |
| opción fuera de catálogo o sin noul | `JevIAError` |

Respaldo: sin API key, estado `SIN_IA`; si Jev falla, estado `ERROR` con el mensaje. En ambos
casos el tipo es la sugerencia de `clasificar_por_palabras_clave` (lista ordenada: PREFORMA,
BL, LISTA_EMPAQUE, CERTIFICADOS, PERMISOS, CARTA_TECNICA, FACTURA) y `confianza_ia = None`.
Nunca se inventa confianza.

Funciones públicas que se conservan: `clasificar_documento`, `leer_documento`,
`extraer_texto_pdf`, `consultar_jev_ia`, `jev_habilitado`, `clasificar_por_palabras_clave`,
`extraer_datos_bl` (delegado a extracción), `comparar_preforma_documentos` (delegado a
`services_cruce.ejecutar_cruce`, agrega `tiene_bl` y `tiene_factura`).

Variables de entorno: `JEV_AI_API_KEY` (o `TYPESAFE_API_KEY`), `JEV_API_URL` (opcional),
`JEV_MODEL` (opcional, default `jev-latest`).

### 4.2 `services_extraccion.py`: lectura de datos con Claude

- Cliente `anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=90)`.
- Modelo: `IA_MODELO_EXTRACCION` del `.env`, default `claude-sonnet-5-5`.
- Activo solo si `settings.IA_HABILITADA` y `settings.ANTHROPIC_API_KEY`.
- Contenido: PDF completo como bloque `document` base64 (sirve para escaneados); imagen
  como bloque `image` PNG; XML/TXT como texto en `<documento>` (tope 100,000 caracteres).
- Formato de salida: `output_config = {"format": {"type": "json_schema", "schema": ...}}`
  enviado en `extra_body` (no depende de la versión del SDK). **No usar `tool_choice`
  forzado**: `claude-sonnet-5-5` responde 400.
- `_cerrar_objetos` pone `additionalProperties: false` y todas las propiedades en `required`
  en cada objeto. Límite de la API: máximo 16 uniones con `null` por esquema; por eso
  `PREFORMA` y los esquemas de soporte usan solo `string`.
- `max_tokens`: 8192; `PREFORMA` 16000 (arriba de ~21000 el SDK exige streaming).
- `stop_reason == 'max_tokens'` o `'refusal'`: error, no se guardan datos parciales.

Esquemas por tipo (`ESQUEMAS`, `TIPOS_SOPORTADOS = tuple(ESQUEMAS)`):

| Tipo | Campos principales |
|---|---|
| `BL` | `numero_bl`, `naviera`, `buque`, `viaje`, `puerto_carga`, `puerto_descarga`, `shipper`, `consignatario`, `peso_bruto_kg`, `bultos`, `contenedores[]` (`numero`, `sello`, `tipo`, `bultos`, `peso_bruto_kg`, un elemento por renglón) |
| `FACTURA` | `numero_factura`, `uuid_cfdi`, `fecha`, `proveedor`, `comprador`, `incoterm`, `moneda`, `valor_total`, `partidas[]` (`descripcion`, `cantidad`, `unidad`, `precio_unitario`, `importe`) |
| `LISTA_EMPAQUE` | `numero_factura_ref`, `bultos`, `peso_bruto_kg`, `peso_neto_kg`, `contenedores[]`, `partidas[]` (`codigo_producto`, `numero_parte`, `codigo_truper`, `descripcion`, `cantidad`, `unidad`, `cartones`, `peso_bruto_kg`, `peso_neto_kg`) |
| `CARTA_TECNICA` | `proveedor`, `comprador`, `incoterm`, `numero_factura`, `contenedores[]`, `peso_bruto_kg`, `descripcion_mercancia` |
| `CERTIFICADOS` | `numero_certificado`, `fecha_emision`, `fecha_vigencia`, `pais_origen`, `importador_nombre`, `importador_rfc`, `importador_domicilio`, `proveedor`, `modelos[]`, `normas[]`, `contenedores[]` |
| `PERMISOS` | `proveedor`, `comprador`, `incoterm`, `numero_factura`, `contenedores[]`, `numero_permiso`, `fecha_vigencia`, `fracciones[]`, `modelos[]`, `cantidad_autorizada` |
| `OTROS` | `proveedor`, `comprador`, `incoterm`, `numero_factura`, `contenedores[]`, `peso_bruto_kg` |
| `PREFORMA` | `num_refe`, `num_pedimento`, `clave_pedimento`, `peso_bruto_kg`, `bultos`, `proveedor_nombre`, `numero_factura`, `fecha_factura`, `incoterm`, `moneda`, `valor_moneda_factura`, `valor_dolares`, `guias[]`, `contenedores[]`, `documentos_ed[]`, `certificados[]`, `observaciones`, `partidas[]` |

Partida de `PREFORMA` (todo string, vacío si no aparece): `sec`, `fraccion`, `nico`,
`vinculacion`, `metodo_valoracion`, `umc`, `cantidad_umc`, `umt`, `cantidad_umt`,
`pais_vendedor`, `pais_origen`, `descripcion`, `valor_aduana`, `precio_pagado`,
`precio_unitario`, `valor_agregado`, `marca`, `modelo`, `codigo_producto`, `observaciones`
(nuevo en Loginco: bloque "OBSERVACIONES A NIVEL PARTIDA"),
`identificadores[]` (`clave`, `complemento1..3`), `permisos[]` (`clave`, `numero`,
`firma_descargo`, `valor_comercial_dls`, `cantidad_umt`), `contribuciones[]` (`clave`,
`tasa`, `tipo_tasa`, `forma_pago`, `importe`). Las descripciones de cada columna del
esquema de Reiki se copian tal cual: guían a Claude sobre el layout de la pre-forma de CASA.
Verificado con pre-formas reales de LCLF (1627) y LCRR (1656): el layout es el mismo de CASA.
Diferencia encontrada: en Loginco la columna MODELO va vacía y los modelos se capturan en
"OBSERVACIONES A NIVEL PARTIDA" (`MODELO: 115117(100200248) 142053(100238887)`).
`validar_y_normalizar` agrega `modelos_observaciones` a cada partida con
`modelos_de_observaciones()` (quita lo que va entre paréntesis y los espacios dentro del
modelo). Falta confirmar con una pre-forma de LCMJ (1927).

Validaciones en código (`validar_y_normalizar`), que generan `advertencias`:

- Pesos e importes como texto decimal (nunca `float`). Pesos <= 0 se advierten.
- Contenedores: normalizados y validados con dígito verificador ISO 6346
  (`contenedor_valido`, regex `^[A-Z]{3}[UJZ]\d{7}$`).
- `BL`: `_fusionar_contenedores_bl` agrupa renglones del mismo contenedor sumando bultos y
  peso, une sellos con ` / `, marca `apariciones`; si hay repetidos, los totales del B/L se
  reemplazan por la suma; si falta un dato en alguna aparición, ese total queda `null` con
  advertencia. Suma de pesos por contenedor vs total (tolerancia max(1 kg, 0.5 %)). Falta de
  buque o viaje se advierte.
- `FACTURA`: suma de importes vs `valor_total` (tolerancia 0.05); moneda en mayúsculas.
- `LISTA_EMPAQUE`: neto no mayor que bruto.
- Soporte (`CARTA_TECNICA`, `CERTIFICADOS`, `PERMISOS`, `OTROS`): normas en mayúsculas,
  fracciones solo dígitos, `cantidad_autorizada` sin miles.
- `PREFORMA`: `num_pedimento` a últimos 7 dígitos; contenedores (`NUMERO / TIPO` -> número);
  partidas con fracción de 8 dígitos, NICO de 2, UMT positiva y
  `precio_pagado / cantidad_umc` contra `precio_unitario` (1 %); `subdivision`
  (`detectada`, `pedimento_relacionado`) si `observaciones` contiene `SUBDIVIDE`.

Formato guardado en `datos_extraidos`: campos al primer nivel más
`meta = {tipo, estado (OK|REVISION|SIN_IA|ERROR|NO_APLICA), modelo, advertencias, error, uso,
reutilizado_de?}`. `uso = {input_tokens, output_tokens, costo_usd}`; `costo_usd` solo se calcula
si están `IA_COSTO_ENTRADA_USD_MTOK` e `IA_COSTO_SALIDA_USD_MTOK` (no se inventa precio).
`extraer_datos_documento` nunca lanza excepción. `extraccion_vigente(doc)` es `True` si
`meta.tipo` coincide con el tipo actual y el estado es `OK` o `REVISION` (evita re-extraer al
cambiar de tipo y regresar).

### 4.3 `services_cruce.py`: cruce entre fuentes

`ejecutar_cruce(ref)` sobre documentos activos. La pre-forma activa más reciente es la voz
"declarada"; sin pre-forma la voz declarada es CASA (`Referencia`).

1. **Bloqueante:** `num_refe` o últimos 7 dígitos de `num_pedimento` de la pre-forma no
   coinciden con la referencia: `DISCREPANCIAS_DETECTADAS`, código `REFERENCIA_NO_COINCIDE`,
   no se ejecuta lo demás.
2. **Contenedores:** fuentes PREFORMA/CASA, BL, LISTA_EMPAQUE y documentos de soporte.
   Consenso = contenedores válidos ISO con más votos. Con 3 o más fuentes, si BL o lista
   difiere y el valor no es válido o está a distancia de edición <= 2: `ERROR_LECTURA`
   (estado global `PENDIENTE`). Si no: discrepancia `CONTENEDORES`.
3. **Peso bruto (kg):** declarado vs BL y lista, tolerancia max(1 kg, 0.5 %). Con
   subdivisión, una diferencia del BL es `REVISAR_SUBDIVISION` (no discrepancia).
4. **Proveedor:** nombre normalizado (sin acentos, puntuación ni sufijos `SA`, `CV`, `LTD`,
   `CO`, `INC`, `LLC`, `SRL`, `DE`, `LA`); coincide si es igual o uno contiene al otro.
5. **ED:** advertencias (no discrepancias) `ED_SIN_DOCUMENTO` y `ED_NO_DECLARADO`, comparando
   `documentos_ed` con nombres de archivo de 13 caracteres alfanuméricos.
6. **Certificados:** `CERTIFICADO_VENCIDO`, `CERTIFICADO_SIN_VIGENCIA`,
   `CERTIFICADO_NO_DECLARADO` (número no aparece en `certificados` ni `observaciones`).
7. **Datos de CASA (Loginco, 12.3):** `_cruce_casa`. Regla `guia_bl`: el `numero_bl` leído debe
   ser una guía declarada (pre-forma; sin pre-forma, `SAAIO_GUIAS`). Si no, discrepancia
   `GUIA_BL`. Se acepta que una termine con la otra (B/L sin prefijo SCAC). Advertencias
   `CASA_GUIAS` y `CASA_CONTENEDORES` (pre-forma vs CASA) y `NUM_PARTIDAS` (partidas leídas vs
   `Referencia.num_partidas`).
8. **Cruce por partida:** `services_cruce_partidas.cruzar_partidas` (4.4).

Estado global: sin pre-forma, sin documentos o con `ERROR_LECTURA` = `PENDIENTE`;
alguna discrepancia = `DISCREPANCIAS_DETECTADAS`; limpio = `EN_REVISION`. El cruce nunca
pone `APROBADO`.

### 4.4 `services_cruce_partidas.py`: cruce por partida

- Emparejamiento por descripción: tokens sin acentos ni palabras vacías (es/en); similitud =
  comunes / tokens de la más corta; umbral 0.6. Partidas con la misma descripción forman un
  grupo y se comparan juntas contra la suma de renglones.
- Discrepancias: `VALOR_FACTURA`, `PARTIDA_CANTIDAD_FACTURA`,
  `PARTIDA_CANTIDAD_LISTA_EMPAQUE` (0.1 %, mín. 0.01), `PARTIDA_IMPORTE_FACTURA` (factor
  `suma(precio_pagado) / valor_moneda_factura`, 1 %, mín. 1.00),
  `PARTIDA_SIN_CERTIFICADO_NOM` (identificador `NM`/`EN` o NOM en el bloque de permisos sin
  certificado que cubra norma base y modelo), `PARTIDA_SIN_PERMISO`, `PERMISO_VENCIDO` (una
  vez por permiso), `PERMISO_FRACCION`.
- Advertencias: `PARTIDA_SIN_FACTURA`, `PARTIDA_SIN_LISTA_EMPAQUE`,
  `RENGLON_FACTURA_SIN_PARTIDA`, `RENGLON_LISTA_EMPAQUE_SIN_PARTIDA`, `PERMISO_NO_DECLARADO`.
- NOM y modelos (Loginco): los modelos de la partida son la columna `modelo` o, si va vacía,
  `modelos_observaciones`. La partida queda cubierta si hay certificados de la misma norma base
  y cada modelo está en alguno de ellos (un certificado sin lista de modelos cubre todo).
- Discrepancias de partida llevan `texto_busqueda` (descripción) y, en NOM y permisos,
  `ocurrencia_busqueda`, que usa la anotación del PDF.
- Regresa `(discrepancias, advertencias, resumen)`; `resumen` va a
  `datos_comparacion['partidas']` (fila por partida: `sec`, `descripcion`, `fraccion`,
  `modelo`, `cantidad_umc`, `grupo`, celdas `factura`, `lista_empaque`, `importe`, `nom`,
  `permiso` con `{valor, estado OK|DISC|SIN, preforma?}`, y `estado`).

Actualizar el docstring: hoy apunta a un spec de Reiki; en Loginco debe apuntar a este.

### 4.5 `services_correccion.py`: reemplazo y recálculo

- `reemplazar_documento(doc_anterior, archivo, usuario, motivo)` (atómico): falla con
  `GlosaBloqueada` si la glosa está `APROBADO` y con `ArchivoInvalido` si el archivo no pasa la
  validación o es idéntico (mismo hash) al que reemplaza; crea el nuevo con el mismo tipo,
  estado `MANUAL`, `origen: REEMPLAZO`; archiva el anterior; si es `PREFORMA` archiva otras
  pre-formas activas; evento `REEMPLAZO`; programa extracción y recálculo (4.9).
- `documentos_en_proceso(referencia)`: activos en `PENDIENTE`/`PROCESANDO`.
- `archivar_preformas_anteriores(preforma_nueva)`: una sola pre-forma activa.
- `recalcular_glosa(...)`: `APROBADO` no cambia; `RECHAZADO` solo sale con eventos de
  corrección (`REEMPLAZO`, `SUBIDA`, `CAMBIO_TIPO`); registra evento `CRUCE`.
- `glosa_aprobada(referencia)`.

### 4.6 `services_anotacion.py`: pre-forma anotada

`generar_preforma_anotada(pdf_bytes, discrepancias, num_refe)`: inserta página resumen A4 al
inicio con las discrepancias `en_preforma=True`; en las páginas originales dibuja recuadro
rojo y número de observación en Helvetica-Bold rojo a la derecha (o izquierda si no cabe);
varias observaciones en el mismo recuadro se juntan ("1,2"). Busca `texto_busqueda` o
`valor_encontrado`, y respeta `ocurrencia_busqueda`.

**Cambio obligatorio:** el encabezado de Reiki está fijo
(`"Agencia Aduanal Reiki Logística - Patente 3772"`). En Loginco la firma pasa a
`generar_preforma_anotada(pdf_bytes, discrepancias, referencia)` y el encabezado se arma con
`settings.GLOSA_NOMBRE_AGENCIA` (nuevo, default `'Loginco'`) y `referencia.patente`.

### 4.7 `services_aaalac.py` y `services_tarifas.py`

Ver 9.1. `services_aaalac.py` se porta tal cual más `aaalac_habilitado()`. El botón
"Consultar AAALAC" y la tarjeta de ETA solo aparecen con `AAALAC_API_KEY` configurada
(decisión 12.1). La consulta guarda solo `eta_aaalac`; ya no pisa `datos_comparacion` (en Reiki
sí lo hace). No se porta `services_tarifas.py`.

### 4.8 `permisos.py`

`GRUPO_GLOSADOR = 'Glosador'`; `es_glosador(user)`: grupo `Glosador` o superusuario (el admin
de Loginco). `is_staff` ya no cuenta como glosador.

### 4.9 `services_procesamiento.py`: procesamiento en segundo plano (nuevo)

- `validar_archivo`: extensiones `.pdf .jpg .jpeg .png .xml .txt`, tamaño máximo
  `GLOSA_MAX_MB` (default 25), no vacío. Lanza `ArchivoInvalido`.
- `calcular_hash`, `duplicado_activo(referencia, hash)`, `extraccion_reutilizable(doc)`.
- `crear_documento(referencia, archivo, usuario, tipo_manual)`: guarda hash y `subido_por`.
- `procesar_documento(doc_id, usuario_id, evento_origen, clasificar)`: clasifica si aplica,
  extrae (o reutiliza la lectura de otro documento con el mismo hash y tipo), archiva
  pre-formas y llama `recalcular_glosa`. Nunca lanza excepción: un fallo deja el documento en
  `ERROR` con el mensaje.
- `programar_procesamiento(...)`: marca `PENDIENTE` y, con `GLOSA_PROCESAMIENTO_ASINCRONO=True`
  (default), corre `procesar_documento` en un hilo al confirmar la transacción
  (`transaction.on_commit`). En pruebas corre en línea.
- `reprocesar_atorados()`: retoma documentos en `PENDIENTE`/`PROCESANDO` con más de
  `GLOSA_MINUTOS_ATORADO` minutos (default 10). Lo corre el job `glosa_reprocesar_atorados`
  de APScheduler cada 5 minutos y el comando `python manage.py glosa_reprocesar`.
- El evento de origen (`SUBIDA`, `REEMPLAZO`, `CAMBIO_TIPO`) se registra en la vista antes de
  procesar; `procesar_documento` solo registra el `CRUCE`.

---

## 5. Ajustes de lógica por Loginco

### 5.1 Estructura de `datos_comparacion`

```json
{
  "version": 2,
  "estado": "PENDIENTE | DISCREPANCIAS_DETECTADAS | EN_REVISION",
  "preforma_id": 123,
  "bloqueante": null,
  "subdivision": {"detectada": false, "pedimento_relacionado": null},
  "reglas": [{"campo", "estado", "valor_consenso", "fuentes": [...], "mensaje"}],
  "discrepancias": [{"codigo", "mensaje", "campo", "documento_id", "tipo_documento",
                     "valor_encontrado", "valor_esperado", "en_preforma",
                     "texto_busqueda?", "ocurrencia_busqueda?"}],
  "advertencias": [{"codigo", "severidad": "ADVERTENCIA", ...}],
  "partidas": [...],
  "tiene_bl": true,
  "tiene_factura": true
}
```

### 5.2 Peso de CASA en toneladas

En `ejecutar_cruce`, el respaldo sin pre-forma cambia de:

```python
peso_decl = ref.peso_bruto
```

a:

```python
peso_decl = ref.peso_kg
```

Prueba nueva: referencia sin pre-forma con `peso_bruto = Decimal('25.500')` (t) y B/L con
`25500` kg da peso `OK`.

### 5.3 Origen del peso en CASA

En Loginco `peso_bruto` viene de `CTRAO_EMBAR` (con respaldo en `fecha_validacion` para la
fecha). Si una referencia no está en `CTRAO_EMBAR`, `peso_bruto` es `None` y el cruce de peso
queda `FALTA_DATO` o depende de la pre-forma. Es aceptable; no se cambia el sync en este
port.

### 5.4 Pre-forma de tres patentes

La regla bloqueante compara `num_refe` normalizado. En Loginco los prefijos son distintos por
patente (ej. `LCRR0881/26`). Probar con una pre-forma real de cada patente que Claude lea
`REF:` con el mismo formato que `Referencia.num_refe` (con `/`). Si CASA imprime otro
formato, normalizar en `validar_y_normalizar('PREFORMA')` antes de comparar.

---

## 6. Vistas, URLs y plantillas

### 6.1 URLs (`glosa/urls.py`, `app_name = 'glosa'`)

| Ruta | Vista | Método |
|---|---|---|
| `referencias/<path:num_refe>/glosa-ia/` | `glosa_ia_detalle` | GET, POST |
| `glosa-ia/documentos/<int:doc_id>/actualizar/` | `glosa_documento_actualizar` (`documento_actualizar`) | POST |
| `glosa-ia/documentos/<int:doc_id>/reemplazar/` | `glosa_documento_reemplazar` (`documento_reemplazar`) | POST |
| `glosa-ia/documentos/<int:doc_id>/preforma-anotada/` | `glosa_preforma_anotada` (`preforma_anotada`) | GET |
| `referencias/<path:num_refe>/glosa-ia/aprobar/` | `glosa_aprobar` | POST |
| `referencias/<path:num_refe>/glosa-ia/rechazar/` | `glosa_rechazar` | POST |
| `referencias/<path:num_refe>/glosa-ia/reabrir/` | `glosa_reabrir` | POST |

En `hal9mil/urls.py`, `path('', include('glosa.urls'))` va **antes** de
`include('referencias.urls')`: `num_refe` usa `<path:>` y contiene `/`, así que la ruta de
detalle de referencias capturaría `.../glosa-ia/` si se evalúa primero.

Agregar `'glosa'` a `INSTALLED_APPS` después de `'referencias'`.

### 6.2 Comportamiento de las vistas

Todas con `@login_required` y `@require_http_methods` (las acciones solo aceptan POST: un GET
a `aprobar` responde 405).

- `glosa_ia_detalle` POST con archivos: **cualquier usuario autenticado** sube (decisión 12.1);
  bloqueado si `APROBADO`. Acepta varios archivos (`getlist('archivo')`). Por cada uno: valida
  (4.9), descarta si el mismo contenido ya está activo en la referencia, crea el documento con
  `subido_por` y hash, registra `SUBIDA` y programa el procesamiento. Si el usuario eligió
  tipo, estado `MANUAL` y confianza 1 y no se clasifica. Con procesamiento asíncrono el mensaje
  dice que la página se actualiza sola; la plantilla recarga cada 8 s mientras haya
  documentos en proceso.
- POST `consultar_aaalac`: usa el B/L **activo** (`filter(tipo_documento='BL', activo=True)`;
  en Reiki falta el filtro `activo`).
- `glosa_documento_actualizar`: bloqueado si `APROBADO`, si el documento no está activo o si
  está en proceso; cambia tipo a `MANUAL` guardando `sugerencia_ia` y `tipo_ia`; re-extrae solo
  si no hay extracción vigente; si el tipo no tiene esquema, vacía `datos_extraidos`; evento
  `CAMBIO_TIPO`.
- `glosa_documento_reemplazar`: usa `reemplazar_documento`.
- `glosa_preforma_anotada`: PDF inline `preforma_observaciones_<num_refe>.pdf`. El
  `num_refe` lleva `/`: `nombre_pdf_anotado()` lo sanitiza (`/` -> `-`).
- `glosa_aprobar`: solo `Glosador`, solo desde `EN_REVISION`, no si hay documentos en
  proceso, re-corre el cruce y rechaza si no está limpio; pone `APROBADO`, evento
  `APROBACION`, concluye el `GlosaRegistro` abierto con nota "Visto bueno glosa IA". El
  `GlosaRegistro` se sigue abriendo a mano en `/glosa/` (decisión 12.1).
- `glosa_rechazar`: solo `Glosador`, comentario obligatorio, `RECHAZADO`. No aplica a una glosa
  `APROBADO` (para eso es reabrir).
- `glosa_reabrir`: solo `Glosador`, comentario obligatorio, solo desde `APROBADO`, pasa a
  `RECHAZADO` y limpia aprobador.

### 6.3 Plantillas

Copiar `glosa/templates/glosa/glosa_ia_detalle.html`, `_celda_partida.html` y
`glosa/templatetags/glosa_extras.py` (`find_fuente`, `split`). Secciones de la página:
encabezado con migas a `lista`/`detalle`; panel de visto bueno o rechazo/reapertura;
bloqueante; subdivisión; estado de revisión; ETA AAALAC; pre-forma activa con enlace a la
anotada; discrepancias (rojo); advertencias (amarillo); reglas de cruce con tabla "Cruce por
partida"; desglose por fuente; tarjetas de documentos con cambio de tipo, versiones
anteriores, "Revisar tipo" y datos extraídos; formulario de reemplazo; historial de eventos.

`base.html` de Loginco carga Tailwind (CDN). La plantilla no usa Alpine.js (usa `<dialog>`).
Ajustes Loginco: migas a `glosa` y `detalle`; aviso "Procesando" con recarga; estado de
procesamiento, tokens/costo y "Lectura reutilizada" por documento; botón de aprobar
deshabilitado si la glosa no está `EN_REVISION` o hay documentos en proceso; subida múltiple
con `accept`; sin emojis.

### 6.4 Sin texto fijo de Reiki

Revisar con `grep -rn -i "reiki\|3772"` la app copiada (plantillas, servicios, pruebas) y
reemplazar por `referencia.patente` o `settings.GLOSA_NOMBRE_AGENCIA`.

### 6.5 Acceso desde la referencia

En `templates/referencias/detalle.html` el botón "Expediente / Glosa IA" enlaza a
`glosa:glosa_ia_detalle` con `num_refe` y muestra documentos activos y estado de la glosa IA
(`glosa_ia_docs`, `glosa_ia_resultado` en el contexto de `detalle`).

---

## 7. Lista operativa `/glosa/` (`referencias/views.py`, `templates/referencias/glosa.html`)

Portar de Reiki:

- Constantes `GLOSA_IA_SIN`, `GLOSA_IA_PENDIENTE`, `GLOSA_IA_REVISION`,
  `GLOSA_IA_APROBADA`, `GLOSA_IA_DISCREPANCIAS`, `GLOSA_IA_FILTROS`,
  `ESTADOS_CLASIFICACION_REVISION`.
- `_anotar_glosa_ia(qs)`: con `Subquery` (sin `GROUP BY` en el queryset principal, para no
  duplicar conteos con el `distinct()` de la búsqueda) anota `glosa_ia_docs` (solo documentos
  activos, a diferencia de Reiki) y `glosa_ia_estado` con `Case/When`. 0 documentos = `sin`;
  `APROBADO` = `aprobada`; `DISCREPANCIAS_DETECTADAS` = `discrepancias`; `EN_REVISION` o
  `RECHAZADO` = `revision`; otro (incluye documentos sin resultado) = `pendiente`. La primera
  versión de Loginco filtraba `pendiente` con `estado_revision='PENDIENTE'` y dejaba fuera las
  referencias con documentos y sin resultado.
- Filtro `?ia=` (valor inválido se ignora), chips de KPI sobre la ventana completa sin
  búsqueda, conteo "N por revisar tipo" por referencia. Orden y paginación conservan `ia`.
- Columna "Glosa IA" con badge que enlaza al detalle.

La vista de Loginco ya filtra por patente; los KPIs de glosa IA respetan ese filtro.

---

## 8. Configuración

`.env` (DigitalOcean App Platform y local):

| Variable | Uso | Requerida |
|---|---|---|
| `IA_HABILITADA` | Activa extracción con Claude | Ya existe |
| `ANTHROPIC_API_KEY` | Claude | Ya existe |
| `IA_MODELO_EXTRACCION` | Modelo de extracción (default `claude-sonnet-5-5`) | Opcional |
| `JEV_AI_API_KEY` o `TYPESAFE_API_KEY` | Jev | Sí para clasificar con IA |
| `JEV_API_URL` | Default `https://api.typesafe.ai/v1/systemone` | Opcional |
| `JEV_MODEL` | Default `jev-latest` | Opcional |
| `AAALAC_API_URL`, `AAALAC_API_KEY` | Placeholder | No |
| `GLOSA_NOMBRE_AGENCIA` | Encabezado de la pre-forma anotada | Opcional |
| `GLOSA_PROCESAMIENTO_ASINCRONO` | Clasificar/extraer en segundo plano (default `True`) | Opcional |
| `GLOSA_MAX_MB` | Tamaño máximo por archivo (default 25) | Opcional |
| `GLOSA_MINUTOS_ATORADO` | Minutos para reprocesar un documento atorado (default 10) | Opcional |
| `IA_COSTO_ENTRADA_USD_MTOK`, `IA_COSTO_SALIDA_USD_MTOK` | Precio por millón de tokens del modelo de extracción, para calcular costo | Opcional |

La clasificación y la extracción corren en un hilo después del request (4.9), así que una
pre-forma larga ya no depende del `--timeout 120` de gunicorn. El `Procfile` corre 1 worker con
4 hilos; el scheduler vive en el mismo proceso. Si se suben más workers, cada uno procesa sus
propias subidas y el job de atorados corre en cada uno (es idempotente).

---

## 9. Pendientes conocidos que se heredan

1. **AAALAC Fonatrac no funciona.** El login de `plataforma.aaalac.mx` exige reCAPTCHA y
   CSRF; no hay API con token confirmada. `services_aaalac.py` es un cliente supuesto. Queda
   pendiente hasta conocer las llamadas reales.
2. **Tarifa electrónica** es un stub con valores fijos.
3. ~~Extracción síncrona~~: resuelto con hilo + job de atorados (4.9). Si el volumen crece,
   migrar a una cola (RQ/Celery) es un trabajo aparte.
4. **Resultados viejos sin `partidas`**: no aplica en Loginco (no hay datos previos).
5. En Reiki hay un `makemigrations` pendiente por `OTRO_SIN_ESQUEMA` (no afecta al port).

---

## 10. Pruebas

Copiar y adaptar de Reiki (`glosa/test_*.py`):

| Archivo | Cubre |
|---|---|
| `test_jev_ia.py` | Contrato de Jev con `requests.post` simulado, umbrales, noul, respaldo, PDF escaneado, imagen. |
| `test_extraccion.py` | Esquemas cumplen límites (`additionalProperties`, <= 16 uniones con null), `validar_y_normalizar` por tipo, ISO 6346, fusión de contenedores BL, `stop_reason`, pre-forma y subdivisión. |
| `test_cruce.py` | Error de lectura, discrepancia, bloqueante, subdivisión, tolerancia de peso, proveedor normalizado, ED como advertencia, certificados. |
| `test_cruce_partidas.py` | Emparejamiento, grupos, cantidades, importes, NOM, permisos, resumen. |
| `test_correccion.py` | Reemplazo, bloqueo por `APROBADO`, archivo de pre-formas, transiciones de `RECHAZADO`. |
| `test_anotacion.py` | Página resumen y marcas; encabezado con patente de Loginco. |
| `test_glosa_views.py` | Vistas, permisos de `Glosador`, conclusión de `GlosaRegistro`, lista `/glosa/` con KPIs y filtro `ia`. |
| `test_glosa_integration.py` | Flujo completo subida -> clasificación -> cruce -> cambio manual. |
| `test_loginco.py` | Ajustes y mejoras de Loginco: modelos en observaciones, uso/costo, NOM con varios modelos, reglas CASA, URL con `/`, subida múltiple, duplicados, validación, permisos, 405 en GET, AAALAC oculto y con B/L activo, reutilización de lectura, procesamiento asíncrono, atorados, métricas, reporte, dashboard y bot. |

No copiar `test_glosa_ia.py`, `test_aaalac.py` ni `tests.py` (placeholders).

Reglas:

- Las pruebas de vistas parchean `JEV_AI_API_KEY` y `TYPESAFE_API_KEY` vacíos para nunca
  llamar a Jev real.
- Adaptar los fixtures de `Referencia`: `patente` de Loginco, `num_refe` con formato
  `LCRR0001/26` y `peso_bruto` en toneladas.
- Pruebas nuevas: peso de CASA en toneladas (5.2), encabezado de anotación por patente
  (4.6), `subido_por` en la subida (6.2), filtro `activo` en la consulta AAALAC (6.2), nombre
  del PDF anotado con `/` (6.2).
- Las pruebas corren con `GLOSA_PROCESAMIENTO_ASINCRONO=False` (settings lo fija cuando el
  comando es `test`); `ProcesamientoAsincronoTest` prueba el camino del hilo.
- Correr `python manage.py test glosa referencias` **localmente con SQLite**, no en la consola
  de producción: el cluster de DigitalOcean no permite a Django borrar la base temporal de
  pruebas y deja una `test_...` huérfana.

---

## 11. Orden de implementación sugerido

1. Dependencias (`pymupdf`, `Pillow`), app `glosa`, modelos, migraciones, admin, settings,
   URLs.
2. `services_jev_ia.py`, `services_extraccion.py` con sus pruebas.
3. `services_cruce.py` (con ajuste de `peso_kg`), `services_cruce_partidas.py`.
4. `services_correccion.py`, `services_anotacion.py` (encabezado parametrizado).
5. Vistas, plantillas, templatetags; sección "Expediente" en el detalle de referencia.
6. Lista `/glosa/` con indicador, filtro y KPIs.
7. Barrido de texto fijo de Reiki (6.4).
8. Prueba manual con un expediente real por patente: B/L, factura, lista de empaque,
   certificado NOM, permiso y pre-forma.
9. Actualizar `context.md` de Loginco con la app `glosa` (modelos, URLs, variables de entorno,
   reglas de cruce).
   

---

## 12. Revisión 2026-11-23: decisiones y mejoras

### 12.1 Decisiones de Tony

1. Rehacer los módulos de extracción, clasificación, vistas, corrección, anotación y plantilla
   desde Reiki, y ajustar este spec.
2. Cualquier usuario autenticado puede subir documentos. Aprobar, rechazar y reabrir siguen
   siendo de `Glosador`.
3. URLs por `num_refe` (no por id).
4. Loginco ya tiene API key de Jev.
5. Pre-formas reales anexadas (LCLF2854/25, LCRR1124/23, LCRR1129/23): ver 4.2 y 5.4.
6. AAALAC oculto mientras no haya API; se muestra al configurar `AAALAC_API_KEY`.
7. El `GlosaRegistro` se abre a mano; el visto bueno solo lo concluye.
8. Implementar todas las mejoras propuestas (12.2 a 12.7).

### 12.2 Hash SHA-256 por archivo

Evita subir dos veces el mismo archivo a una referencia y reutiliza la lectura de Claude si el
mismo archivo y tipo ya se leyó en otro documento (aunque sea de otra referencia): `meta.reutilizado_de`,
uso en cero. Un reemplazo con el mismo archivo se rechaza.

### 12.3 Más cruces contra CASA

Ver 4.3 punto 7: `guia_bl` / `GUIA_BL`, `CASA_GUIAS`, `CASA_CONTENEDORES`, `NUM_PARTIDAS`.

### 12.4 Procesamiento en segundo plano

Ver 4.9 y sección 8.

### 12.5 Precisión de Jev y costo de IA (`glosa/metricas.py`)

- `precision_jev(qs)`: de los documentos con `clasificacion_ia.tipo_ia`, porcentaje que conserva
  ese tipo y las 5 correcciones más comunes (`jev` -> `final`).
- `uso_ia(qs)`: tokens de Claude (entrada/salida), costo si hay precios, tokens de Jev y
  lecturas reutilizadas.
- `top_discrepancias(qs)`: códigos de discrepancia más frecuentes.
- `get_datos_glosa_ia_semana(inicio, fin)`: todo lo anterior más documentos, expedientes,
  aprobaciones, rechazos, reaperturas y resultados con discrepancias.
- El dashboard de glosa (`/glosa/dashboard/`) muestra el bloque "Glosa IA" del mes.

### 12.6 Reporte semanal y bot de WhatsApp

- `reportes.data.get_datos_semana` agrega `glosa_ia` y `templates/reportes/semanal.html` la
  sección "Glosa IA" (si hubo documentos en la semana).
- Bot: `glosa LCRR0881/26` responde estado, documentos, visto bueno, comentario, hasta 5
  discrepancias y número de advertencias. Aparece en `ayuda`.

### 12.7 Validación de subida y varios archivos

Ver 4.9 (`validar_archivo`) y 6.2. El input acepta varios archivos y limita extensiones.

### 12.8 Pendientes

- Probar con una pre-forma real de LCMJ (1927).
- Prueba manual con un expediente real por patente con Jev y Claude activos (`IA_HABILITADA`).
- Configurar en producción `IA_COSTO_ENTRADA_USD_MTOK` / `IA_COSTO_SALIDA_USD_MTOK` con el
  precio vigente del modelo de extracción si se quiere ver el costo en USD.
