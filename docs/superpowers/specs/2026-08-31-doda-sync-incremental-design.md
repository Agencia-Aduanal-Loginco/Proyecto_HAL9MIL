# Sync incremental de DODAs — Diseño

**Fecha:** 2026-08-31
**Autor:** xoyoc (con Claude)
**Estado:** Aprobado, pendiente de plan de implementación

## Problema

`sync_agent/sync_agent.py :: fetch_dodas()` no tiene filtro incremental. Su
query trae **todos** los DODA de la CVE_CAAT de Transportes Kasu que no están
dados de baja (`WHERE d.CVE_CAAT = ? AND d.FEC_BAJA IS NULL`), sin importar el
estado guardado en `last_sync.json`.

Consecuencia observada en producción (2026-08-31): con "Referencias con
cambios: 1" el agente igualmente envía "10033 DODAs" en 101 tandas, ~490 s por
corrida, y Django reporta `actualizadas=12920` de puro re-upsert sin cambios
reales.

Las **referencias** sí son incrementales: pasan por `fetch_changed_refs_since()`
que mira `SAAIO_PROCES.FEC_MODI` y `SAAIO_PEDIME.DIA_PAGO`. Los DODA nunca
recibieron ese tratamiento.

## Objetivo

Que `fetch_dodas` envíe únicamente DODAs **nuevos o modificados** desde el
último sync, igual que ya ocurre con las referencias. Contemplar además la baja
y el reemplazo de DODAs para que Django refleje el estado correcto.

## Hechos del esquema Firebird (SAAIO_DODA)

Confirmado con el usuario (no hay forma de inspeccionar la BD desde este
entorno; corre solo en los servidores Windows):

- **No existe** columna de fecha de modificación.
- `FEC_DODAE` — fecha de creación/emisión del DODA.
- `FEC_BAJA` — fecha en que el DODA se dio de baja (NULL mientras esté activo).
- `BAJ_DODA` — cuando un DODA reemplaza a otro, este campo guarda el
  **`NUM_DODA` (folio, VARCHAR 34)** del DODA reemplazado. El DODA reemplazado
  debe quedar marcado como baja; el DODA activo es el que **no** aparece en el
  `BAJ_DODA` de ningún otro DODA más nuevo.

## Decisiones de negocio

- Un DODA de **reemplazo** (trae `BAJ_DODA`) **no** dispara notificación de
  modulación (correo / PDF / webhook a Kasu). Solo se guarda y se marca la
  relación. El cliente ya fue notificado del DODA original.
- Un DODA de **baja** tampoco notifica: solo actualiza estado (`fecha_baja`).
- La **primera sincronización** de una patente (sin estado en `last_sync.json`)
  y `--full-sync` siguen sembrando **solo DODAs activos** (`FEC_BAJA IS NULL`),
  para no arrastrar histórico de bajas ni disparar miles de correos. El manejo
  de bajas/reemplazos aplica de ahí en adelante, en modo incremental.

## Enfoque elegido

Incremental por fecha (`FEC_DODAE` / `FEC_BAJA`) más resolución de la cadena de
reemplazo vía `BAJ_DODA`. Descartados: (2) solo `FEC_DODAE` sin tocar bajas —
no cumple el requisito de marcar baja/reemplazo; (3) dedupe del lado de Django —
sigue leyendo y transfiriendo las 10k filas cada corrida.

## Cambios

### 1. `sync_agent/sync_agent.py` — `fetch_dodas(cur, since_dt=None)`

Nueva firma con `since_dt`.

**Full sync** (`since_dt is None`): sin cambios respecto a hoy —
`WHERE d.CVE_CAAT = ? AND d.FEC_BAJA IS NULL`.

**Incremental** (`since_dt` con valor): se quita `FEC_BAJA IS NULL` y el `WHERE`
pasa a:

```sql
WHERE d.CVE_CAAT = ?
  AND (
        d.FEC_DODAE >= ?                 -- DODAs nuevos
     OR d.FEC_BAJA  >= ?                 -- DODAs recién dados de baja
     OR TRIM(d.NUM_DODA) IN (            -- DODAs reemplazados por uno nuevo
          SELECT TRIM(n.BAJ_DODA) FROM SAAIO_DODA n
          WHERE n.CVE_CAAT = ? AND n.BAJ_DODA IS NOT NULL
            AND n.FEC_DODAE >= ?
        )
     )
```

Parámetros: `(CVE_CAAT_KASU, since_str, since_str, CVE_CAAT_KASU, since_str)`
con `since_str = since_dt.strftime('%Y-%m-%d %H:%M:%S')` (mismo formato que
`fetch_changed_refs_since`).

`TRIM()` en ambos lados del `IN` para blindar contra padding de CHAR; con ~10k
filas el costo de perder el índice es despreciable.

Se agrega `d.BAJ_DODA` al `SELECT`. Cada dict de DODA gana:

```python
'baj_doda': clean(baj_doda, 34),   # folio del DODA reemplazado; '' si no aplica
```

En modo incremental, al no forzar `FEC_BAJA IS NULL`, un DODA de baja fluye con
su `fecha_baja` ya poblada.

### 2. `sync_agent/sync_agent.py` — `main()`

- Se elimina el `return 0` anticipado (líneas ~713-718) que corta el run cuando
  `fetch_changed_refs_since` devuelve un set vacío. Ahora, con 0 referencias
  cambiadas, `refs_filter` queda como `set()` y el flujo continúa a extraer
  DODAs — un DODA nuevo puede emitirse contra una referencia vieja sin cambios.
  Con `refs_filter = set()`, todos los `fetch_*` de referencias devuelven vacío
  (ya lo maneja `_fetch_rows`), y `all_refs` queda vacío.
- `dodas = fetch_dodas(cur)` → `dodas = fetch_dodas(cur, since_dt=last_sync_dt)`.
- El bloque `if not all_refs:` (línea ~753) ya cubre los dos casos resultantes:
  - "sin refs pero con DODAs" → envía DODAs en tandas y actualiza
    `last_sync.json`.
  - "sin refs y sin DODAs" → cae al `else` "nada que enviar" y actualiza estado.
  No requiere cambios.
- `--full-sync` deja `last_sync_dt = None` → `fetch_dodas` full. Primera sync de
  la patente: `last_sync_dt = None` → full (seed). Sin cambio de comportamiento.

### 3. Django — `referencias/models.py` + migración

Campo nuevo en `Doda`:

```python
baj_doda = models.CharField(max_length=34, blank=True, db_index=True)
# NUM_DODA del DODA que este reemplaza (SAAIO_DODA.BAJ_DODA). Vacío = DODA original.
```

Migración `referencias/migrations/0014_doda_baj_doda.py` — solo `AddField`, sin
data migration. Los DODAs reemplazados llegan con su `FEC_BAJA` real vía el set
"recién dados de baja" del punto 1.

### 4. Django — `referencias/sync_views.py :: _upsert_dodas`

- Agregar `'baj_doda': str(item.get('baj_doda', ''))[:34]` a `defaults`.
- Regla de notificación: un DODA `created=True` se agrega a la lista `creadas`
  (la que dispara `modulacion.procesar_dodas_nuevas` vía `transaction.on_commit`)
  **solo si** `not baj_doda and not fecha_baja`.
- Si el DODA creado es reemplazo o baja: estampar
  `notificado_en = modulacion_enviada_en = timezone.now()` al crearlo y **no**
  encolarlo. Queda guardado y marcado; sin correo/PDF/webhook. Así nunca lo
  levanta un escaneo posterior de "pendientes de modulación"
  (p.ej. `reintentar_modulacion`).
- El branch `no_notificar` (primera sync, línea ~102) no cambia: sigue
  recibiendo en `creadas` solo los activos-nuevos reales.

### 5. `referencias/management/commands/import_firebird.py`

Cambio de paridad mínimo (este archivo se mantiene en paralelo con
`sync_agent.py`):

- Agregar `d.BAJ_DODA` al `SELECT` de su `fetch_dodas`.
- Agregar `'baj_doda': clean(baj_doda, 34)` al dict, para que ambos productores
  emitan la misma forma que consume `_upsert_dodas`.
- **No** se le agrega filtro incremental: es la herramienta de bootstrap /
  import completo y conserva su `WHERE ... FEC_BAJA IS NULL`.

### 6. Pruebas

**`sync_agent/test_sync_agent.py`** (standalone, sin Django ni Firebird):

- Extender `_FakeCursor` para capturar el SQL y los params del último
  `execute()` (hoy los ignora).
- `fetch_dodas(cur, since_dt=None)` → el SQL no contiene `FEC_DODAE >=` y sí
  contiene `FEC_BAJA IS NULL` (full intacto).
- `fetch_dodas(cur, since_dt=<datetime>)` → el SQL contiene las 3 condiciones
  `OR` y se pasan 5 params; el dict resultante incluye la clave `baj_doda`.
- `baj_doda` viaja en el payload que arma `build_payload`.

**Django** (`DBURL= python manage.py test referencias` sobre SQLite — patrón ya
usado en el repo porque `.env` apunta a PostgreSQL remoto):

- `_upsert_dodas` persiste `baj_doda`.
- Un DODA entrante con `baj_doda` no se encola a modulación y queda con
  `notificado_en` estampado.
- Un DODA entrante con `fecha_baja` tampoco notifica.
- Un DODA nuevo normal (sin `baj_doda` ni `fecha_baja`) sí se encola.

### 7. Documentación

- Actualizar docstrings de `fetch_dodas` en `sync_agent.py` e `import_firebird.py`.
- Ajustar el comentario de `doda_chunk_size` en `sync_agent/config.ini.example`:
  las tandas ahora son incrementales, no el universo completo de DODAs vigentes.

## Fuera de alcance (v1)

- Detectar que a un DODA se le **agregó o quitó una referencia** en
  `SAAIO_DODADO` sin que cambie el row de `SAAIO_DODA` — no hay timestamp en esa
  tabla y el caso es raro. Se documenta como limitación conocida; `--full-sync`
  lo resuelve puntualmente.
- Backfill de `baj_doda` / `fecha_baja` para DODAs ya sincronizados antes de
  este cambio. Se corrigen solos en cuanto vuelva a haber actividad, o con un
  `--full-sync` manual.

## Riesgos

- **Reloj de los servidores Windows.** El filtro depende de que `FEC_DODAE` /
  `FEC_BAJA` en Firebird y el `last_sync.json` del agente estén en la misma
  zona horaria y sin desfase grande. Es el mismo supuesto que ya tiene
  `fetch_changed_refs_since` para las referencias, así que no introduce un
  riesgo nuevo.
- **Primer run después del deploy.** El primer sync incremental tras soltar el
  cambio usará el `last_sync.json` existente; puede traer un lote grande de
  DODAs acumulados desde esa marca, pero acotado por fecha, no las 10k.
