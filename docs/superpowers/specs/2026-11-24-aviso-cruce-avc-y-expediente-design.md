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
- **AVC** (Aviso de Cruce). **Hoy no se extrae.** En CASA vive en:
  - `SAAIO_CRUCE`: un aviso por `ID_CRUCE`, con `ACUSE_AVC` (ej. `AVC510408202200000056820`),
    `FEC_EMIAVC` (emisión), `FEC_VIGAVC` (vigencia), `FEC_CRUCE`, `ADU_DESP`, `PAT_AGEN`,
    `CVE_CAPT`, `FEC_BAJA`.
  - `SAAIO_CRUCED`: las referencias que ampara el aviso (`NUM_REFE`, `NUM_PEDI`, `RFC_AVC`).
  - `SAAIO_AVCTIME`: bitácora del proceso (`CVE_PROC` = `AAVC`, `ENVAVC`, `IMAVC`...).

Nota: `bd_casa_pedimentos.md` describe `SAAIO_AVCTIME` como "Análisis de Valor en Comercio".
Por los acuses (`AVC5104...`) y la tabla `SAAIO_CRUCE`, es el Aviso de Cruce. Conviene
corregir esa descripción.

El objetivo es **agrupar DODA y AVC por contenedor** en un expediente, para que Kasu, el
capturista y el ejecutivo trabajen sobre una sola unidad: "este contenedor ya tiene (o le
falta) lo necesario para salir".

## Decisiones propuestas

### 1. Extracción del AVC en `sync_agent.py`

Nueva función `fetch_avisos_cruce(cur, since_dt=None)`, con el mismo patrón que
`fetch_dodas`:

```sql
SELECT c.ID_CRUCE, c.ACUSE_AVC, c.FEC_EMIAVC, c.FEC_VIGAVC, c.FEC_CRUCE,
       c.ADU_DESP, c.PAT_AGEN, c.CVE_CAPT, c.FEC_BAJA,
       cd.CONS_ID, cd.NUM_REFE, cd.NUM_PEDI, cd.RFC_AVC
FROM SAAIO_CRUCE c
JOIN SAAIO_CRUCED cd ON cd.ID_CRUCE = c.ID_CRUCE
```

- Sync completo: `WHERE c.FEC_BAJA IS NULL AND c.ACUSE_AVC IS NOT NULL`.
- Incremental: `FEC_EMIAVC >= since_dt OR FEC_BAJA >= since_dt` (para enterarse de bajas).
- Se envían en lotes propios, en el bloque `avisos_cruce` del payload, igual que
  `enviar_dodas_en_lotes`.
- **Filtro:** a diferencia del DODA, `SAAIO_CRUCE` no tiene `CVE_CAAT` en las columnas
  documentadas. Se sincronizan **todos** los AVC de la patente; la relación con Kasu se
  resuelve en el expediente (ver punto 3).

### 2. Modelos nuevos

`AvisoCruce`:

| Campo | Origen |
| --- | --- |
| `id_cruce` (único) | `ID_CRUCE` |
| `acuse_avc` | `ACUSE_AVC` |
| `fecha_emision`, `fecha_vigencia`, `fecha_cruce`, `fecha_baja` | `FEC_EMIAVC`, `FEC_VIGAVC`, `FEC_CRUCE`, `FEC_BAJA` |
| `aduana`, `patente`, `cve_capt` | `ADU_DESP`, `PAT_AGEN`, `CVE_CAPT` |

`AvisoCruceReferencia`: `aviso` FK, `cons_id`, `num_refe`, `num_pedimento`, `rfc_importador`
(`RFC_AVC`), `referencia` FK nullable (se liga como `DodaReferencia`).

`_upsert_avisos_cruce` en `sync_views.py`, análogo a `_upsert_dodas`.

### 3. Expediente de extracción por contenedor

Modelo `ExpedienteExtraccion`, **uno por contenedor de una referencia**:

| Campo | Notas |
| --- | --- |
| `clave` (único) | `"{num_refe}:{num_cont}"`. Es la llave que viaja a Kasu |
| `referencia`, `contenedor` | FK |
| `doda` | FK nullable; el DODA vigente (sin baja) que ampara la referencia |
| `aviso_cruce` | FK nullable; el AVC vigente que ampara la referencia |
| `rfc_importador` | De `AvisoCruceReferencia.rfc_importador`; si no hay AVC, de `clientes.Cliente.rfc` |
| `estado_documental` | `FALTA_AVC`, `FALTA_DODA`, `COMPLETO`, `AVC_VENCIDO`, `CANCELADO` |
| `es_kasu` | True si el DODA es de `CVE_CAAT_KASU` |
| `notificado_completo_en`, `push_kasu_estado` | Control de envíos |

Reglas:

- Al llegar un DODA nuevo o un AVC nuevo, se crea o actualiza el expediente de cada
  contenedor de cada referencia amparada. El orden de llegada no importa.
- **DODA de reemplazo** (`BAJ_DODA`): el expediente apunta al DODA nuevo. **No** se crea un
  expediente nuevo. Hoy Kasu deduplica por `num_doda + contenedor`, así que un DODA de
  reemplazo genera una segunda modulación; con la clave por referencia y contenedor eso
  deja de pasar.
- AVC dado de baja: el expediente vuelve a `FALTA_AVC`.
- `AVC_VENCIDO`: un job diario marca los expedientes cuyo AVC venció sin que Kasu haya
  confirmado la extracción.
- Solo los expedientes con `es_kasu = True` se empujan a Kasu (ver spec hermano).

### 4. Correo del aviso de extracción

Hoy se manda un correo por DODA. Pasa a mandarse **uno por expediente al quedar
`COMPLETO`**, con los dos documentos:

- PDF del DODA (el que ya se genera).
- PDF del AVC, generado con los datos de `SAAIO_CRUCE` (acuse, emisión, vigencia, aduana,
  referencias y pedimentos). CASA guarda el nombre del archivo (`SAAIO_AVCTIME.NOM_ARCH`),
  pero el sync no tiene acceso a ese archivo; ver pregunta abierta.
- Los links de `completar_datos_url` que ya regresa Kasu.

Si un expediente lleva más de N horas en `FALTA_AVC` o `FALTA_DODA` (N configurable, 24 por
omisión), se manda un recordatorio al capturista.

### 5. Vista en HAL9MIL

En el detalle de la referencia (`referencias/detalle.html`), una tarjeta por contenedor con
el estado documental, el DODA, el AVC con su vigencia, y la línea de tiempo de seguimiento
que agrega el spec hermano.

## Migración de lo existente

- Un comando `crear_expedientes` arma los expedientes de los DODA activos ya sincronizados,
  con `estado_documental = FALTA_AVC`, y luego corre un sync completo de AVC.
- Los DODA ya notificados (`notificado_en` lleno) marcan su expediente como notificado,
  para no reenviar correos viejos.

## Pruebas

- `fetch_avisos_cruce` arma un dict por `ID_CRUCE` con sus referencias (Firebird simulado,
  mismo patrón que `test_sync_agent.py`).
- DODA primero y AVC después, y al revés: ambos terminan en un expediente `COMPLETO` y un
  solo correo.
- DODA de reemplazo: mismo expediente, apunta al DODA nuevo, sin correo nuevo.
- AVC dado de baja: el expediente regresa a `FALTA_AVC`.
- El job de vencimiento marca `AVC_VENCIDO` solo si no hay extracción confirmada.

## Preguntas abiertas

- [ ] ¿El AVC se sigue generando en CASA hoy? `bd_casa_pedimentos.md` muestra 122 registros
  de 2022. Si ahora se genera en otro sistema (VUCEM directo, portal de la aduana), la
  fuente cambia.
- [ ] ¿Kasu debe recibir el contenedor **desde el primer documento** (como hoy con el DODA)
  para planear, o **solo cuando el expediente esté completo**? La propuesta: desde el primer
  documento, con el estado documental visible, para no quitarle tiempo de planeación a Kasu.
- [ ] ¿Se necesita el PDF original del AVC (archivo de `NOM_ARCH`) o basta un PDF generado
  con los datos?
- [ ] ¿Un AVC puede amparar contenedores de un DODA que no es de Kasu? Si sí, esos
  expedientes existen en HAL9MIL pero no se empujan.
