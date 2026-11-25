# Aviso de Cruce (AVC) y expediente de extracción por contenedor

Fecha: 2026-11-24
Estado: propuesto, con preguntas abiertas al final
Módulos afectados: `sync_agent/`, `referencias/` (`models.py`, `sync_views.py`, `modulacion.py`)
Spec hermano en este repo: `2026-11-24-datos-para-patio-esperanza-design.md` (push a Kasu y
correos de seguimiento)
Specs relacionados:
- `BitacoraKasu`: `docs/superpowers/specs/2026-11-24-integracion-patio-esperanza-v2-design.md`
- `ProyectoPatioEsperanza`: `docs/superpowers/specs/2026-11-24-api-socios-solicitud-ingreso-design.md`

## Contexto

Para extraer un contenedor de la aduana se necesitan **dos documentos distintos**:

- **DODA** (Documento de Operación para Despacho Aduanero). Ya se extrae: `sync_agent.py`
  lee `SAAIO_DODA` + `SAAIO_DODADO` filtrando `CVE_CAAT = '3B74'` (Transportes Kasu), y
  `_procesar_doda` manda el correo al capturista y el push a Kasu.
- **AVC** (Aviso de Cruce). **Hoy no se extrae.** En CASA vive en tres tablas:

| Tabla | Nivel | Campos que se usan |
| --- | --- | --- |
| `SAAIO_CRUCE` | Un registro por aviso (un vehículo que cruza) | `ID_CRUCE`, `ACUSE_AVC`, `FEC_EMIAVC`, `FEC_VIGAVC`, `TAG_CRUCE`, `GAF_CRUCE`, `NUM_PLACA` |
| `SAAIO_CRUCED` | Referencias y pedimentos que ampara el aviso | `ID_CRUCE`, `CONS_ID`, `NUM_REFE`, `NUM_PEDI`, `RFC_AVC` |
| `SAAIO_CRUCEDVE` | Contenedores que viajan en el vehículo del aviso | `ID_CRUCE`, `CONS_CONT`, `NUM_CONT`, `NUM_CAN1` |

Significado de los campos del vehículo y del contenedor:

- `ACUSE_AVC`: acuse del aviso (ej. `AVC510408202200000056820`).
- `FEC_EMIAVC`, `FEC_VIGAVC`: emisión y vigencia del aviso.
- `TAG_CRUCE`: tag del vehículo para el carril de cruce.
- `GAF_CRUCE`: gafete único de identificación del operador.
- `NUM_PLACA`: placa del tractocamión.
- `NUM_CONT`: número de contenedor.
- `NUM_CAN1`: candado (sello) del contenedor.

Estos datos son los que Transportes Kasu necesita para la modulación: qué unidad, qué
operador, qué contenedor y qué candado. El DODA no los trae.

Hallazgo importante: `SAAIO_CRUCEDVE` liga el AVC **directamente al contenedor**, no solo
a la referencia. Una referencia con tres contenedores puede tener tres AVC distintos (uno
por vehículo). Por eso el expediente no puede tomar "el AVC de la referencia"; tiene que
tomar el AVC que lista ese contenedor.

`SAAIO_AVCTIME` (bitácora del proceso: `CVE_PROC` = `AAVC`, `ENVAVC`, `IMAVC`...) queda
fuera de este alcance.

Nota: `bd_casa_pedimentos.md` describe `SAAIO_AVCTIME` como "Análisis de Valor en Comercio".
Por los acuses (`AVC5104...`) y la tabla `SAAIO_CRUCE`, es el Aviso de Cruce. Conviene
corregir esa descripción y agregar `SAAIO_CRUCEDVE`, que no está documentada.

El objetivo es **agrupar DODA y AVC por contenedor** en un expediente, para que Kasu, el
capturista y el ejecutivo trabajen sobre una sola unidad: "este contenedor ya tiene (o le
falta) lo necesario para salir, y sale en esta unidad con este candado".

## Decisiones propuestas

### 1. Extracción del AVC en `sync_agent.py`

Nueva función `fetch_avisos_cruce(cur, since_dt=None)`, con el mismo contrato que
`fetch_dodas` (no depende de `all_refs`, corre aunque no haya referencias cambiadas).

**Tres consultas, no un solo JOIN.** Unir `SAAIO_CRUCED` y `SAAIO_CRUCEDVE` en la misma
consulta multiplica filas (referencias por contenedores). Se hacen tres consultas con el
mismo filtro y se arman en Python por `ID_CRUCE`:

```sql
-- 1. Encabezado del aviso
SELECT c.ID_CRUCE, c.ACUSE_AVC, c.FEC_EMIAVC, c.FEC_VIGAVC,
       c.TAG_CRUCE, c.GAF_CRUCE, c.NUM_PLACA
FROM SAAIO_CRUCE c
WHERE {filtro}

-- 2. Referencias amparadas
SELECT cd.ID_CRUCE, cd.CONS_ID, cd.NUM_REFE, cd.NUM_PEDI, cd.RFC_AVC
FROM SAAIO_CRUCED cd
JOIN SAAIO_CRUCE c ON c.ID_CRUCE = cd.ID_CRUCE
WHERE {filtro}

-- 3. Contenedores y candados
SELECT ve.ID_CRUCE, ve.CONS_CONT, ve.NUM_CONT, ve.NUM_CAN1
FROM SAAIO_CRUCEDVE ve
JOIN SAAIO_CRUCE c ON c.ID_CRUCE = ve.ID_CRUCE
WHERE {filtro}
```

El filtro se repite con `JOIN` en lugar de un `IN (...)` con los IDs, para no chocar con
el límite de 1500 elementos de Firebird en listas `IN`.

Filtros:

- Base, siempre: `c.ACUSE_AVC IS NOT NULL` (solo avisos ya transmitidos).
- Sync completo (`since_dt=None`): solo avisos vigentes, `c.FEC_VIGAVC >= CURRENT_DATE`.
  Así la primera sync no arrastra el histórico de 2022.
- Incremental: `c.FEC_EMIAVC >= since_dt`.
- Bajas: depende de la pregunta abierta sobre `FEC_BAJA`. Si la columna existe y se usa,
  el incremental agrega `OR c.FEC_BAJA >= since_dt` y el completo `AND c.FEC_BAJA IS NULL`,
  igual que en `fetch_dodas`.

**Filtro de Kasu:** `SAAIO_CRUCE` no tiene `CVE_CAAT`. Se sincronizan **todos** los AVC de
la patente; la relación con Kasu se resuelve en el expediente (punto 3).

Cada aviso se manda en el bloque `avisos_cruce` del payload con esta forma:

```json
{
  "id_cruce": 56820,
  "acuse_avc": "AVC510408202200000056820",
  "patente": "3456",
  "fecha_emision": "2026-11-24 09:15:00",
  "fecha_vigencia": "2026-11-27 23:59:59",
  "tag": "...",
  "gafete": "...",
  "placa": "12AB3C",
  "referencias": [
    {"cons_id": 1, "num_refe": "LCLF0353/26", "num_pedimento": "6001234",
     "rfc_importador": "ABC010101XYZ"}
  ],
  "contenedores": [
    {"cons_cont": 1, "num_cont": "MSCU1234567", "candado": "SL998877"}
  ]
}
```

- `num_cont` se normaliza en el agente: mayúsculas, sin espacios ni guiones. Es la misma
  forma con la que se guarda `Contenedor.num_cont`, para poder ligarlos en Django.
- Envío en lotes propios con `enviar_avisos_en_lotes(avisos, no_notificar, totales)`,
  misma mecánica que `enviar_dodas_en_lotes` y tamaño configurable `AVC_CHUNK_SIZE`.
- En `main()`, igual que con los DODA: si no hay referencias cambiadas pero sí avisos, se
  envían solo los avisos. El `last_sync` avanza solo si todos los lotes se confirmaron.

**Antes de implementar:** confirmar tipos y largos con el DDL real
(`RDB$RELATION_FIELDS` / `RDB$FIELDS`), en un script `check_saaio_cruce.py` como el
`check_saaio_proces.py` que ya existe. En particular: si `FEC_VIGAVC` es `DATE` o
`TIMESTAMP`, si `CONS_CONT` es entero, y si existen `NUM_CAN2`... además de `NUM_CAN1`.

### 2. Modelos nuevos

`AvisoCruce`:

| Campo | Origen |
| --- | --- |
| `id_cruce` (único) | `SAAIO_CRUCE.ID_CRUCE` |
| `acuse_avc` | `ACUSE_AVC` |
| `patente` | `PATENTE` del agente, como en `Doda` |
| `fecha_emision`, `fecha_vigencia` | `FEC_EMIAVC`, `FEC_VIGAVC` |
| `tag_cruce` | `TAG_CRUCE` |
| `gafete` | `GAF_CRUCE` |
| `num_placa` | `NUM_PLACA` |
| `fecha_baja` | `FEC_BAJA`, solo si se confirma que existe |

`AvisoCruceReferencia` (de `SAAIO_CRUCED`):

| Campo | Notas |
| --- | --- |
| `aviso` | FK a `AvisoCruce` |
| `cons_id` | `CONS_ID`. `unique_together = (aviso, cons_id)` |
| `num_refe`, `num_pedimento`, `rfc_importador` | `NUM_REFE`, `NUM_PEDI`, `RFC_AVC` |
| `referencia` | FK nullable, se liga por `num_refe` como `DodaReferencia` |

`AvisoCruceContenedor` (de `SAAIO_CRUCEDVE`):

| Campo | Notas |
| --- | --- |
| `aviso` | FK a `AvisoCruce` |
| `cons_cont` | `CONS_CONT`. `unique_together = (aviso, cons_cont)` |
| `num_cont` | `NUM_CONT`, normalizado |
| `candado` | `NUM_CAN1` |
| `contenedor` | FK nullable a `Contenedor` |

`_upsert_avisos_cruce` en `sync_views.py`, análogo a `_upsert_dodas`:

- Encabezado por `update_or_create(id_cruce=...)`.
- Hijos: se reemplazan completos dentro de la misma transacción (borrar y crear). CASA
  puede corregir un candado o quitar un contenedor del aviso, y así no quedan restos.
- Liga de `AvisoCruceContenedor.contenedor`: busca un `Contenedor` con ese `num_cont`
  cuya referencia esté entre las referencias del aviso. Si el contenedor todavía no se
  ha sincronizado, la FK queda nula y se vuelve a intentar al final de cada sync (el
  mismo paso que recalcula expedientes, punto 3).
- Un contenedor del AVC que no pertenece a ninguna referencia del aviso se registra como
  inconsistencia en el log y en el admin, no se descarta.

### 3. Expediente de extracción por contenedor

Modelo `ExpedienteExtraccion`, **uno por contenedor de una referencia**:

| Campo | Notas |
| --- | --- |
| `clave` (único) | `"{num_refe}:{num_cont}"`. Es la llave que viaja a Kasu |
| `referencia`, `contenedor` | FK |
| `doda` | FK nullable; el DODA vigente (sin baja) que ampara la referencia |
| `aviso_contenedor` | FK nullable a `AvisoCruceContenedor`; el renglón del AVC vigente que lista **este** contenedor. De ahí salen aviso, placa, tag, gafete y candado |
| `rfc_importador` | De `AvisoCruceReferencia.rfc_importador`; si no hay AVC, de `clientes.Cliente.rfc` |
| `estado_documental` | `FALTA_AVC`, `FALTA_DODA`, `COMPLETO`, `AVC_VENCIDO`, `CANCELADO` |
| `es_kasu` | True si el DODA es de `CVE_CAAT_KASU` |
| `notificado_completo_en`, `push_kasu_estado` | Control de envíos |

Reglas:

- Al llegar un DODA, un AVC o un contenedor nuevo, se recalcula el expediente de cada
  contenedor afectado. El orden de llegada no importa.
- El AVC se asigna **por contenedor**, no por referencia. Si una referencia tiene dos
  contenedores y solo uno aparece en `SAAIO_CRUCEDVE`, ese queda `COMPLETO` y el otro
  sigue en `FALTA_AVC`.
- Si un contenedor aparece en más de un AVC (por ejemplo, uno venció y se emitió otro), se
  toma el vigente con `fecha_emision` más reciente; si ninguno está vigente, el más
  reciente, y el estado es `AVC_VENCIDO`.
- **DODA de reemplazo** (`BAJ_DODA`): el expediente apunta al DODA nuevo. **No** se crea un
  expediente nuevo. Hoy Kasu deduplica por `num_doda + contenedor`, así que un DODA de
  reemplazo genera una segunda modulación; con la clave por referencia y contenedor eso
  deja de pasar.
- AVC dado de baja (si `FEC_BAJA` existe): el expediente vuelve a `FALTA_AVC`.
- Cambio de placa, tag, gafete o candado en un AVC ya empujado: se vuelve a empujar el
  expediente aunque `estado_documental` no cambie (ver ajuste al spec hermano abajo).
- `AVC_VENCIDO`: un job diario marca los expedientes cuyo AVC venció sin que Kasu haya
  confirmado la extracción.
- Solo los expedientes con `es_kasu = True` se empujan a Kasu (ver spec hermano).

### 4. Datos del AVC en el push a Kasu

El push que define el spec hermano (punto 1 de `2026-11-24-datos-para-patio-esperanza-design.md`)
ya lleva `acuse_avc` y `fecha_vigencia_avc`. Se agregan los datos de modulación:

| Campo | Origen |
| --- | --- |
| `placa` | `AvisoCruce.num_placa` |
| `tag_cruce` | `AvisoCruce.tag_cruce` |
| `gafete_operador` | `AvisoCruce.gafete` |
| `candado` | `AvisoCruceContenedor.candado` |

- Vacíos mientras el expediente esté en `FALTA_AVC`.
- `idempotency_key` deja de ser solo `"{clave_expediente}:{estado_documental}"`: pasa a
  `"{clave_expediente}:{estado_documental}:{id_cruce}:{hash_datos_avc}"`, donde
  `hash_datos_avc` es un hash corto de placa, tag, gafete y candado. Así una corrección
  del candado sí llega a Kasu y un reenvío idéntico se sigue deduplicando.
- Cuando Kasu reporte la unidad real en sus eventos (`datos.placas` en el webhook del spec
  hermano), HAL9MIL compara contra `num_placa` del AVC y marca la diferencia en la línea
  de tiempo. No bloquea nada; es aviso para el ejecutivo.

### 5. Correo del aviso de extracción

Hoy se manda un correo por DODA. Pasa a mandarse **uno por expediente al quedar
`COMPLETO`**, con los dos documentos:

- PDF del DODA (el que ya se genera).
- PDF del AVC, generado con los datos sincronizados: acuse, emisión, vigencia, placa, tag,
  referencias con pedimento, y contenedores con candado. El gafete se muestra enmascarado
  (últimos 4 caracteres). CASA guarda el nombre del archivo original
  (`SAAIO_AVCTIME.NOM_ARCH`), pero el sync no tiene acceso a ese archivo; ver pregunta
  abierta.
- Los links de `completar_datos_url` que ya regresa Kasu.
- El destinatario sigue siendo el capturista del DODA (`Doda.cve_capt`), como hoy.

Si un expediente lleva más de N horas en `FALTA_AVC` o `FALTA_DODA` (N configurable, 24 por
omisión), se manda un recordatorio al capturista.

### 6. Vista en HAL9MIL

En el detalle de la referencia (`referencias/detalle.html`), una tarjeta por contenedor con
el estado documental, el DODA, el AVC con su vigencia, placa, tag y candado, y la línea de
tiempo de seguimiento que agrega el spec hermano. El gafete completo solo se ve en el
admin.

## Migración de lo existente

- Un comando `crear_expedientes` arma los expedientes de los DODA activos ya sincronizados,
  con `estado_documental = FALTA_AVC`, y luego corre un sync completo de AVC (solo
  vigentes, ver punto 1).
- Los DODA ya notificados (`notificado_en` lleno) marcan su expediente como notificado,
  para no reenviar correos viejos.

## Pruebas

Agente (`test_sync_agent.py`, Firebird simulado):

- `fetch_avisos_cruce` arma un dict por `ID_CRUCE` con sus referencias y contenedores.
- Un aviso con 2 referencias y 2 contenedores produce 2 referencias y 2 contenedores, no 4
  de cada uno (sin producto cruzado).
- `num_cont` se normaliza (`"mscu 123456-7"` queda `"MSCU1234567"`).
- Sync completo filtra por vigencia; incremental por `FEC_EMIAVC`.
- Envío en lotes: un lote fallido no avanza `last_sync`.

Django:

- `_upsert_avisos_cruce` reemplaza hijos: un candado corregido en CASA queda corregido, y
  un contenedor quitado del aviso desaparece.
- Contenedor del AVC que llega antes que el `Contenedor` de la referencia: se liga en la
  siguiente sync.
- DODA primero y AVC después, y al revés: ambos terminan en un expediente `COMPLETO` y un
  solo correo.
- Referencia con dos contenedores y un AVC que lista solo uno: un expediente `COMPLETO` y
  otro `FALTA_AVC`.
- Dos AVC para el mismo contenedor: gana el vigente más reciente.
- DODA de reemplazo: mismo expediente, apunta al DODA nuevo, sin correo nuevo.
- Cambio de candado en un AVC ya empujado: nuevo push con otra `idempotency_key`.
- El job de vencimiento marca `AVC_VENCIDO` solo si no hay extracción confirmada.

## Preguntas abiertas

- [ ] ¿`SAAIO_CRUCE` tiene `FEC_BAJA` (u otra marca de cancelación) y se usa? La lista de
  campos de este spec no la incluye. Sin ella no hay forma de enterarse de que un AVC se
  canceló; solo de que venció.
- [ ] ¿`SAAIO_CRUCEDVE` tiene más candados (`NUM_CAN2`, ...)? Si sí, `candado` pasa a ser
  una lista.
- [ ] ¿Un AVC puede corregirse en CASA después de transmitido (placa, candado)? Si sí, el
  incremental por `FEC_EMIAVC` no lo ve; haría falta una fecha de modificación o resync
  de los avisos vigentes en cada corrida (son pocos).
- [ ] ¿El AVC se sigue generando en CASA hoy? `bd_casa_pedimentos.md` muestra 122 registros
  de 2022. Si ahora se genera en otro sistema (VUCEM directo, portal de la aduana), la
  fuente cambia.
- [ ] ¿Kasu debe recibir el contenedor **desde el primer documento** (como hoy con el DODA)
  para planear, o **solo cuando el expediente esté completo**? La propuesta: desde el primer
  documento, con el estado documental visible, para no quitarle tiempo de planeación a Kasu.
- [ ] ¿Kasu acepta recibir el gafete del operador completo? Es un dato personal; si Kasu ya
  lo tiene en su padrón de operadores, basta con mandar los últimos 4 caracteres para
  validar.
- [ ] ¿Se necesita el PDF original del AVC (archivo de `NOM_ARCH`) o basta un PDF generado
  con los datos?
- [ ] ¿Un AVC puede amparar contenedores de un DODA que no es de Kasu? Si sí, esos
  expedientes existen en HAL9MIL pero no se empujan.
