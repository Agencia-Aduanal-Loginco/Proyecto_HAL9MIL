# Sync incremental de DODAs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que `sync_agent.py` solo envíe DODAs nuevos o modificados desde el último sync (hoy reenvía los ~10 000 activos cada corrida), contemplando baja y reemplazo.

**Architecture:** `fetch_dodas()` gana un parámetro `since_dt`. En modo incremental filtra por `FEC_DODAE`/`FEC_BAJA` y resuelve la cadena de reemplazo vía `BAJ_DODA` (folio). El payload gana el campo `baj_doda`; el modelo `Doda` de Django lo persiste y `_upsert_dodas` suprime la notificación de modulación para DODAs de baja o reemplazo. `import_firebird.py` recibe solo el cambio de forma del payload (paridad), sin filtro incremental.

**Tech Stack:** Python 3.12, `fdb` (Firebird), Django 6.0, `unittest` (agente standalone) + `django.test.TestCase` (servidor).

## Global Constraints

- El agente `sync_agent/sync_agent.py` corre **fuera de Django** en servidores Windows con solo Firebird; sus pruebas no pueden importar Django ni tocar Firebird real (`python sync_agent/test_sync_agent.py`).
- Firebird = Firebird 2.5; SQL debe ser compatible (`TRIM`, subconsultas `IN` sí soportadas).
- `SAAIO_DODA` **no tiene** columna de fecha de modificación. Señales disponibles: `FEC_DODAE` (creación), `FEC_BAJA` (baja, NULL si activo), `BAJ_DODA` (folio `NUM_DODA`, VARCHAR 34, del DODA reemplazado).
- CVE_CAAT de Kasu en el agente: constante `CVE_CAAT_KASU = '3B74'` (no importar `settings`). En Django: `settings.CVE_CAAT_KASU`.
- Formato de fecha para el `WHERE` de Firebird: `since_dt.strftime('%Y-%m-%d %H:%M:%S')` (igual que `fetch_changed_refs_since`).
- Primera sync de una patente y `--full-sync` → `since_dt is None` → comportamiento full actual (solo DODAs activos, `FEC_BAJA IS NULL`).
- Un DODA de baja o de reemplazo **no** dispara correo/PDF/webhook de modulación.
- Los tests de Django corren sobre SQLite: `DBURL= python manage.py test referencias` (el `.env` local apunta a PostgreSQL remoto).
- Mensajes de commit terminan con las dos líneas `Co-Authored-By:` / `Claude-Session:` que ya usa el repo.
- Rama de trabajo: `feature/doda-sync-incremental` (ya creada, el spec ya está commiteado ahí).

---

## File Structure

- **Modify** `sync_agent/sync_agent.py` — `fetch_dodas()` (firma + query + campo `baj_doda`), `main()` (quitar `return` anticipado, pasar `since_dt`).
- **Modify** `sync_agent/test_sync_agent.py` — extender `_FakeCursor` para capturar SQL/params; nueva clase `FetchDodasIncrementalTests`.
- **Modify** `referencias/models.py` — campo `Doda.baj_doda`.
- **Create** `referencias/migrations/0014_doda_baj_doda.py`.
- **Modify** `referencias/sync_views.py` — `_upsert_dodas()` (leer `baj_doda`, suprimir notificación en baja/reemplazo).
- **Modify** `referencias/tests.py` — casos nuevos en `UpsertDodasTests`.
- **Modify** `referencias/management/commands/import_firebird.py` — `fetch_dodas()` agrega `d.BAJ_DODA` al SELECT y `baj_doda` al dict (paridad de forma, sin filtro incremental).
- **Modify** `sync_agent/config.ini.example` — comentario de `doda_chunk_size`.

---

### Task 1: `fetch_dodas` incremental en el agente

**Files:**
- Modify: `sync_agent/sync_agent.py:425-477` (función `fetch_dodas`)
- Modify: `sync_agent/test_sync_agent.py:26-37` (`_FakeCursor`)
- Test: `sync_agent/test_sync_agent.py` (nueva clase `FetchDodasIncrementalTests`)

**Interfaces:**
- Consumes: constante `CVE_CAAT_KASU` (ya existe en `sync_agent.py`), helper `clean`, `fb_datetime_str` (ya existen).
- Produces: `fetch_dodas(cur, since_dt=None) -> list[dict]`. Cada dict:
  `{id_doda:int, num_doda:str, patente:str, cve_caat:str, cve_capt:str, terminal_cve:str, terminal_nombre:str, fecha_doda:str|None, fecha_baja:str|None, baj_doda:str, referencias:list[{num_refe:str, cons_id:int}]}`.
  El SELECT devuelve 11 columnas en este orden: `ID_DODA, NUM_DODA, CVE_CAAT, CVE_CAPT, FEC_DODAE, FEC_BAJA, BAJ_DODA, NUM_REFE, CONS_ID, CVE_REFI, NOM_REFI`.

- [ ] **Step 1: Extender `_FakeCursor` para capturar el SQL**

En `sync_agent/test_sync_agent.py`, reemplazar el método `execute` de `_FakeCursor` (líneas 33-34):

```python
    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params
```

(El resto de `_FakeCursor` no cambia. Es retrocompatible: los tests existentes no leen `last_sql`.)

- [ ] **Step 2: Escribir los tests que fallan**

Agregar al final de `sync_agent/test_sync_agent.py`, antes del bloque `if __name__ == '__main__':`:

```python
import datetime as _dt


class FetchDodasIncrementalTests(unittest.TestCase):
    """fetch_dodas(cur, since_dt) — filtro incremental por FEC_DODAE/FEC_BAJA/BAJ_DODA."""

    def _row(self, id_doda=5001, num_doda='DODA-5001', baj_doda=None,
             fec_dodae=None, fec_baja=None):
        # Orden de columnas del SELECT de fetch_dodas (11):
        # ID_DODA, NUM_DODA, CVE_CAAT, CVE_CAPT, FEC_DODAE, FEC_BAJA, BAJ_DODA,
        # NUM_REFE, CONS_ID, CVE_REFI, NOM_REFI
        return (id_doda, num_doda, '3B74', 'ANGELICA', fec_dodae, fec_baja,
                baj_doda, 'LCLF0001/26', 1, '257', 'Talma')

    def test_full_sync_usa_where_fec_baja_is_null_y_no_pasa_since(self):
        cur = sa._FakeCursor([self._row()])
        sa.fetch_dodas(cur, since_dt=None)
        self.assertIn('FEC_BAJA IS NULL', cur.last_sql)
        self.assertNotIn('FEC_DODAE >=', cur.last_sql)
        self.assertEqual(cur.last_params, (sa.CVE_CAAT_KASU,))

    def test_incremental_arma_where_con_tres_condiciones_or_y_cinco_params(self):
        cur = sa._FakeCursor([self._row()])
        since = _dt.datetime(2026, 8, 31, 16, 33, 46)
        sa.fetch_dodas(cur, since_dt=since)
        self.assertIn('FEC_DODAE >=', cur.last_sql)
        self.assertIn('FEC_BAJA  >=', cur.last_sql)
        self.assertIn('BAJ_DODA', cur.last_sql)
        self.assertNotIn('FEC_BAJA IS NULL', cur.last_sql)
        self.assertEqual(
            cur.last_params,
            (sa.CVE_CAAT_KASU, '2026-08-31 16:33:46', '2026-08-31 16:33:46',
             sa.CVE_CAAT_KASU, '2026-08-31 16:33:46'),
        )

    def test_dict_resultante_incluye_baj_doda(self):
        cur = sa._FakeCursor([self._row(baj_doda='DODA-ORIG-1')])
        out = sa.fetch_dodas(cur, since_dt=_dt.datetime(2026, 8, 31))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['baj_doda'], 'DODA-ORIG-1')

    def test_baj_doda_none_queda_string_vacio(self):
        cur = sa._FakeCursor([self._row(baj_doda=None)])
        out = sa.fetch_dodas(cur, since_dt=_dt.datetime(2026, 8, 31))
        self.assertEqual(out[0]['baj_doda'], '')
```

- [ ] **Step 3: Correr los tests y verificar que fallan**

Run: `python sync_agent/test_sync_agent.py`
Expected: FALLAN los 4 nuevos — `fetch_dodas()` aún no acepta `since_dt` (TypeError) y el dict no tiene `baj_doda`.

- [ ] **Step 4: Reescribir `fetch_dodas` en `sync_agent/sync_agent.py`**

Reemplazar la función completa (líneas 425-477) por:

```python
def fetch_dodas(cur, since_dt=None):
    """
    Extrae DODA de la CVE_CAAT de Transportes Kasu, con las referencias
    ligadas (SAAIO_DODADO) y la terminal resuelta vía SAAIO_IDEPED
    (CVE_IDEN='CR', COM_IDEN = clave de terminal) + SAAIC_REFIS.

    since_dt=None  → sync completo: solo DODAs activos (FEC_BAJA IS NULL).
                     Se usa en la primera sync de una patente y con --full-sync.
    since_dt=<dt>  → sync incremental: solo DODAs con cambios desde esa marca:
                       - FEC_DODAE >= since_dt  (nuevos)
                       - FEC_BAJA  >= since_dt  (recién dados de baja)
                       - NUM_DODA que aparece en el BAJ_DODA de un DODA nuevo
                         (el DODA reemplazado — puede ser viejo — para que
                         Django lo marque como baja).
                     En incremental NO se filtra FEC_BAJA IS NULL: los DODAs
                     de baja fluyen con su fecha_baja poblada.

    Retorna lista de dicts para el bloque "dodas" del payload:
        {id_doda, num_doda, patente, cve_caat, cve_capt, terminal_cve,
         terminal_nombre, fecha_doda, fecha_baja, baj_doda,
         referencias: [{num_refe, cons_id}, ...]}
    """
    select = """
        SELECT
            d.ID_DODA, d.NUM_DODA, d.CVE_CAAT, d.CVE_CAPT,
            d.FEC_DODAE, d.FEC_BAJA, d.BAJ_DODA,
            dd.NUM_REFE, dd.CONS_ID,
            rf.CVE_REFI, rf.NOM_REFI
        FROM SAAIO_DODA d
        JOIN SAAIO_DODADO dd ON dd.ID_DODA = d.ID_DODA
        LEFT JOIN SAAIO_IDEPED ip
            ON ip.NUM_REFE = dd.NUM_REFE AND ip.CVE_IDEN = 'CR'
        LEFT JOIN SAAIC_REFIS rf ON rf.CVE_REFI = ip.COM_IDEN
    """

    if since_dt is None:
        cur.execute(select + " WHERE d.CVE_CAAT = ? AND d.FEC_BAJA IS NULL",
                    (CVE_CAAT_KASU,))
    else:
        since_str = since_dt.strftime('%Y-%m-%d %H:%M:%S')
        cur.execute(select + """
            WHERE d.CVE_CAAT = ?
              AND (
                    d.FEC_DODAE >= ?
                 OR d.FEC_BAJA  >= ?
                 OR TRIM(d.NUM_DODA) IN (
                      SELECT TRIM(n.BAJ_DODA) FROM SAAIO_DODA n
                      WHERE n.CVE_CAAT = ? AND n.BAJ_DODA IS NOT NULL
                        AND n.FEC_DODAE >= ?
                    )
              )
        """, (CVE_CAAT_KASU, since_str, since_str, CVE_CAAT_KASU, since_str))

    dodas = {}
    for row in cur.fetchall():
        (id_doda, num_doda, cve_caat, cve_capt, fec_dodae, fec_baja, baj_doda,
         num_refe, cons_id, terminal_cve, terminal_nombre) = row
        if id_doda is None:
            continue
        entry = dodas.setdefault(id_doda, {
            'id_doda':         int(id_doda),
            'num_doda':        clean(num_doda, 34),
            'patente':         PATENTE,
            'cve_caat':        clean(cve_caat, 6),
            'cve_capt':        clean(cve_capt, 20).upper(),
            'terminal_cve':    '',
            'terminal_nombre': '',
            'fecha_doda':      fb_datetime_str(fec_dodae),
            'fecha_baja':      fb_datetime_str(fec_baja),
            'baj_doda':        clean(baj_doda, 34),
            'referencias':     [],
        })
        if not entry['terminal_cve'] and terminal_cve:
            entry['terminal_cve']    = clean(terminal_cve, 4)
            entry['terminal_nombre'] = clean(terminal_nombre, 70)
        ref = clean(num_refe, 15)
        if ref and cons_id is not None:
            entry['referencias'].append({'num_refe': ref, 'cons_id': int(cons_id)})
    return list(dodas.values())
```

- [ ] **Step 5: Correr los tests y verificar que pasan**

Run: `python sync_agent/test_sync_agent.py`
Expected: PASS — todos (los 4 nuevos + los existentes; `DodasSurviveEmptyAllRefsTests` y `DodasEnviadasAparteDeLosLotesDeRefsTests` siguen verdes porque solo dependen de `build_payload`, no de la forma interna de las filas).

- [ ] **Step 6: Commit**

```bash
git add sync_agent/sync_agent.py sync_agent/test_sync_agent.py
git commit -m "$(cat <<'EOF'
feat(sync_agent): fetch_dodas incremental por FEC_DODAE/FEC_BAJA/BAJ_DODA

Nuevo parametro since_dt. En modo incremental el WHERE trae solo DODAs
nuevos, recien dados de baja, o reemplazados por uno nuevo (cadena
BAJ_DODA por folio). since_dt=None conserva el comportamiento full
(solo activos). El payload gana el campo baj_doda.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F7fUwzwKSDgWW8u8hZGJXw
EOF
)"
```

---

### Task 2: Cablear `main()` para que la detección de DODAs corra sin refs

**Files:**
- Modify: `sync_agent/sync_agent.py:709-735` (bloque de detección de cambios + extracción dentro de `main()`)

**Interfaces:**
- Consumes: `fetch_dodas(cur, since_dt=None)` de Task 1; `fetch_changed_refs_since(cur, since_dt) -> set[str]` (ya existe).
- Produces: nada nuevo; cambia el flujo de control de `main()`.

**Contexto:** `main()` no tiene cobertura de tests en este repo (`test_sync_agent.py` nunca lo importa). La verificación es lectura del diff + suite standalone en verde. El cambio es acotado y de bajo riesgo: quitar un `return` temprano y pasar un argumento.

- [ ] **Step 1: Quitar el `return 0` anticipado cuando no hay refs con cambios**

En `sync_agent/sync_agent.py`, dentro de `main()`, reemplazar este bloque (actualmente líneas ~710-719):

```python
        if last_sync_dt:
            log.info(f'Sync incremental desde {last_sync_dt.strftime("%Y-%m-%d %H:%M:%S")}')
            refs_filter = fetch_changed_refs_since(cur, last_sync_dt)
            if not refs_filter:
                log.info('Sin cambios detectados desde el último sync.')
                log.info('══════════════════════════════════════════════════════════')
                state[PATENTE] = datetime.datetime.now().isoformat()
                save_last_sync(state)
                return 0
            log.info(f'Referencias con cambios: {len(refs_filter)}')
        else:
            log.info('Sync completo (primera ejecución o --full-sync)')
```

por:

```python
        if last_sync_dt:
            log.info(f'Sync incremental desde {last_sync_dt.strftime("%Y-%m-%d %H:%M:%S")}')
            refs_filter = fetch_changed_refs_since(cur, last_sync_dt)
            log.info(f'Referencias con cambios: {len(refs_filter)}')
            # No se corta aquí aunque refs_filter esté vacío: un DODA nuevo
            # puede emitirse contra una referencia vieja sin cambios. Con
            # refs_filter = set() todos los fetch_* de referencias devuelven
            # vacío (lo maneja _fetch_rows) y fetch_dodas sigue corriendo.
            # El caso "sin refs y sin DODAs" lo resuelve el bloque
            # `if not all_refs:` más abajo, que actualiza last_sync y sale.
        else:
            log.info('Sync completo (primera ejecución o --full-sync)')
```

- [ ] **Step 2: Pasar `since_dt` a `fetch_dodas`**

En el mismo `main()`, en el bloque de extracción (actualmente línea ~735), cambiar:

```python
        dodas        = fetch_dodas(cur)
```

por:

```python
        dodas        = fetch_dodas(cur, since_dt=last_sync_dt)
```

- [ ] **Step 3: Verificar que la suite standalone sigue verde**

Run: `python sync_agent/test_sync_agent.py`
Expected: PASS — sin regresiones (este cambio no toca funciones cubiertas por tests, pero la suite debe seguir pasando).

- [ ] **Step 4: Revisión del flujo con `--dry-run` (lectura)**

Verificar por inspección que, con `last_sync_dt` seteado y `refs_filter = set()`:
- `fetch_embar(cur, set())` … `fetch_regval(cur, set())` → devuelven `{}` (por `_fetch_rows`: `refs` vacío → `return []`).
- `pedimentos, all_refs = fetch_pedimentos(cur, set())` → `({}, set())`.
- `dodas = fetch_dodas(cur, since_dt=last_sync_dt)` → lista incremental (posiblemente vacía).
- `if not all_refs:` → si `dodas` no vacío, entra a `enviar_dodas_en_lotes` y actualiza `state`; si vacío, cae al `else` "nada que enviar" y actualiza `state`. Ambos caminos terminan en `return 0`.

- [ ] **Step 5: Commit**

```bash
git add sync_agent/sync_agent.py
git commit -m "$(cat <<'EOF'
feat(sync_agent): detectar DODAs nuevos aunque no haya refs con cambios

main() ya no corta con return anticipado cuando fetch_changed_refs_since
no encuentra referencias: sigue a fetch_dodas(since_dt=last_sync_dt) para
captar DODAs emitidos contra referencias viejas. El caso sin refs ni
DODAs lo resuelve el bloque `if not all_refs:` existente.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F7fUwzwKSDgWW8u8hZGJXw
EOF
)"
```

---

### Task 3: Campo `Doda.baj_doda` + migración

**Files:**
- Modify: `referencias/models.py:160-176` (clase `Doda`)
- Create: `referencias/migrations/0014_doda_baj_doda.py`

**Interfaces:**
- Produces: campo `Doda.baj_doda: CharField(max_length=34, blank=True, db_index=True)`.

- [ ] **Step 1: Agregar el campo al modelo**

En `referencias/models.py`, en la clase `Doda`, después de la línea `fecha_baja = models.DateTimeField(null=True, blank=True)  # FEC_BAJA` (línea 169), agregar:

```python
    baj_doda       = models.CharField(max_length=34, blank=True, db_index=True)
    # NUM_DODA (folio) del DODA que este reemplaza — SAAIO_DODA.BAJ_DODA.
    # Vacío = DODA original, no es reemplazo de ninguno.
```

- [ ] **Step 2: Generar la migración**

Run: `DBURL= python manage.py makemigrations referencias`
Expected: crea `referencias/migrations/0014_doda_baj_doda.py` con un `AddField`.

Si el entorno no permite `makemigrations`, crear el archivo a mano con este contenido exacto:

```python
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('referencias', '0013_enviomodulacion'),
    ]

    operations = [
        migrations.AddField(
            model_name='doda',
            name='baj_doda',
            field=models.CharField(blank=True, db_index=True, max_length=34),
        ),
    ]
```

- [ ] **Step 3: Aplicar y verificar la migración sobre SQLite**

Run: `DBURL= python manage.py migrate referencias`
Expected: aplica `0014_doda_baj_doda` sin error.

- [ ] **Step 4: Smoke test del campo**

Run:
```bash
DBURL= python manage.py shell -c "from referencias.models import Doda; d=Doda.objects.create(id_doda=999001, patente='1656', baj_doda='DODA-X'); print(Doda.objects.get(id_doda=999001).baj_doda); d.delete()"
```
Expected: imprime `DODA-X`.

- [ ] **Step 5: Commit**

```bash
git add referencias/models.py referencias/migrations/0014_doda_baj_doda.py
git commit -m "$(cat <<'EOF'
feat(referencias): campo Doda.baj_doda (folio del DODA reemplazado)

Espeja SAAIO_DODA.BAJ_DODA. Vacio = DODA original. Lo va a poblar
_upsert_dodas desde el payload del agente.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F7fUwzwKSDgWW8u8hZGJXw
EOF
)"
```

---

### Task 4: `_upsert_dodas` persiste `baj_doda` y suprime notificación en baja/reemplazo

**Files:**
- Modify: `referencias/sync_views.py:267-318` (`_upsert_dodas`)
- Test: `referencias/tests.py` (clase `UpsertDodasTests`, después de la línea ~439)

**Interfaces:**
- Consumes: `Doda.baj_doda` (Task 3); `timezone` (ya importado en `sync_views.py:40`); `_parse_dt` (ya existe).
- Produces: `_upsert_dodas(dodas, stats, error_msgs) -> list[Doda]` — la lista devuelta (`creadas`) ahora **excluye** DODAs recién creados que traen `baj_doda` no vacío **o** `fecha_baja` no nula; esos se marcan `notificado_en = modulacion_enviada_en = now()` al momento de crearse.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a la clase `UpsertDodasTests` en `referencias/tests.py` (después de `test_item_sin_id_doda_se_omite_sin_error`, línea ~439):

```python
    def test_baj_doda_se_persiste(self):
        stats, error_msgs = self._stats()
        _upsert_dodas([{
            'id_doda':  12001,
            'patente':  '1656',
            'cve_caat': '3B74',
            'baj_doda': 'DODA-ORIGINAL-9',
            'referencias': [],
        }], stats, error_msgs)
        self.assertEqual(Doda.objects.get(id_doda=12001).baj_doda, 'DODA-ORIGINAL-9')

    def test_doda_de_reemplazo_no_entra_en_creadas_y_queda_notificado(self):
        stats, error_msgs = self._stats()
        creadas = _upsert_dodas([{
            'id_doda':  12002,
            'patente':  '1656',
            'cve_caat': '3B74',
            'baj_doda': 'DODA-ORIGINAL-9',
            'referencias': [],
        }], stats, error_msgs)
        self.assertEqual(creadas, [])
        doda = Doda.objects.get(id_doda=12002)
        self.assertIsNotNone(doda.notificado_en)
        self.assertIsNotNone(doda.modulacion_enviada_en)

    def test_doda_de_baja_no_entra_en_creadas_y_queda_notificado(self):
        stats, error_msgs = self._stats()
        creadas = _upsert_dodas([{
            'id_doda':    12003,
            'patente':    '1656',
            'cve_caat':   '3B74',
            'fecha_baja': '2026-08-20T10:00:00',
            'referencias': [],
        }], stats, error_msgs)
        self.assertEqual(creadas, [])
        doda = Doda.objects.get(id_doda=12003)
        self.assertIsNotNone(doda.fecha_baja)
        self.assertIsNotNone(doda.notificado_en)

    def test_doda_normal_sin_baja_ni_reemplazo_si_entra_en_creadas(self):
        stats, error_msgs = self._stats()
        creadas = _upsert_dodas([{
            'id_doda':  12004,
            'patente':  '1656',
            'cve_caat': '3B74',
            'referencias': [],
        }], stats, error_msgs)
        self.assertEqual(len(creadas), 1)
        self.assertEqual(creadas[0].id_doda, 12004)
        self.assertEqual(Doda.objects.get(id_doda=12004).baj_doda, '')
        self.assertIsNone(Doda.objects.get(id_doda=12004).notificado_en)
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `DBURL= python manage.py test referencias.tests.UpsertDodasTests -v 2`
Expected: FALLAN 3 de los 4 nuevos:
- `test_baj_doda_se_persiste` → AssertionError: `_upsert_dodas` no pasa `baj_doda` a `defaults`, el `Doda` creado queda con `baj_doda == ''` (default del CharField `blank=True`) en vez de `'DODA-ORIGINAL-9'`.
- `test_doda_de_reemplazo_no_entra_en_creadas_y_queda_notificado` → AssertionError: hoy el DODA entra en `creadas` y `notificado_en` queda `None`.
- `test_doda_de_baja_no_entra_en_creadas_y_queda_notificado` → AssertionError: mismo motivo.
- `test_doda_normal_sin_baja_ni_reemplazo_si_entra_en_creadas` → PASA ya (comportamiento actual).

- [ ] **Step 3: Modificar `_upsert_dodas`**

En `referencias/sync_views.py`, dentro de `_upsert_dodas`, en el `defaults` (líneas ~284-293) agregar la clave `baj_doda`:

```python
            defaults = {
                'num_doda':        str(item.get('num_doda', ''))[:34],
                'patente':         str(item.get('patente', ''))[:10],
                'cve_caat':        str(item.get('cve_caat', ''))[:6],
                'cve_capt':        str(item.get('cve_capt', ''))[:20],
                'terminal_cve':    str(item.get('terminal_cve', ''))[:4],
                'terminal_nombre': str(item.get('terminal_nombre', ''))[:70],
                'fecha_doda':      _parse_dt(item.get('fecha_doda')),
                'fecha_baja':      _parse_dt(item.get('fecha_baja')),
                'baj_doda':        str(item.get('baj_doda', ''))[:34],
            }
```

y reemplazar el bloque (líneas ~294-299):

```python
            doda, created = Doda.objects.update_or_create(
                id_doda=id_doda,
                defaults=defaults,
            )
            if created:
                creadas.append(doda)
```

por:

```python
            doda, created = Doda.objects.update_or_create(
                id_doda=id_doda,
                defaults=defaults,
            )
            if created:
                if defaults['baj_doda'] or defaults['fecha_baja']:
                    # DODA de reemplazo (trae BAJ_DODA) o de baja (trae
                    # FEC_BAJA): nunca dispara correo/PDF/webhook de
                    # modulación. Se marca como ya atendido para que ni
                    # procesar_dodas_nuevas ni reintentar_modulacion lo
                    # levanten después.
                    ahora = timezone.now()
                    doda.notificado_en = ahora
                    doda.modulacion_enviada_en = ahora
                    doda.save(update_fields=['notificado_en', 'modulacion_enviada_en'])
                else:
                    creadas.append(doda)
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `DBURL= python manage.py test referencias.tests.UpsertDodasTests -v 2`
Expected: PASS — los 4 nuevos + los 7 existentes de la clase.

- [ ] **Step 5: Correr la suite de la app y verificar sin regresiones**

Run: `DBURL= python manage.py test referencias -v 1`
Expected: PASS — incluye `SyncEndpointDodasTests`, `DodaBasicCreationTests`, `test_modulacion.py`, `test_import_firebird.py`.

- [ ] **Step 6: Commit**

```bash
git add referencias/sync_views.py referencias/tests.py
git commit -m "$(cat <<'EOF'
feat(sync): _upsert_dodas persiste baj_doda y no notifica bajas/reemplazos

Un DODA recien creado que trae baj_doda o fecha_baja no entra en la
lista `creadas` (la que dispara modulacion.procesar_dodas_nuevas): se
marca notificado_en/modulacion_enviada_en = now() para que tampoco lo
tome reintentar_modulacion. Los DODAs nuevos normales no cambian.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F7fUwzwKSDgWW8u8hZGJXw
EOF
)"
```

---

### Task 5: Paridad de forma en `import_firebird.py`

**Files:**
- Modify: `referencias/management/commands/import_firebird.py:259-309` (`fetch_dodas`)
- Test: `referencias/test_import_firebird.py` (si tiene tests de `fetch_dodas`; si no, verificación por lectura)

**Interfaces:**
- Consumes: `settings.CVE_CAAT_KASU`, `clean`, `fb_datetime_str` (ya existen en el módulo).
- Produces: el dict de `fetch_dodas` de `import_firebird` gana la clave `baj_doda:str`, para emitir la misma forma que consume `_upsert_dodas` (Task 4). **Sin** filtro incremental — conserva `WHERE ... FEC_BAJA IS NULL`.

- [ ] **Step 1: Ver si hay tests de `fetch_dodas` en `import_firebird`**

Run: `grep -n "fetch_dodas\|BAJ_DODA\|baj_doda" referencias/test_import_firebird.py`
Expected: lista de coincidencias (o vacío). Si hay un test que arma filas mock de DODA, anota el orden de columnas que espera para actualizarlo en el Step 3.

- [ ] **Step 2: Agregar `d.BAJ_DODA` al SELECT y `baj_doda` al dict**

En `referencias/management/commands/import_firebird.py`, en `fetch_dodas`, cambiar el SELECT (líneas ~271-283):

```python
    cur.execute("""
        SELECT
            d.ID_DODA, d.NUM_DODA, d.CVE_CAAT, d.CVE_CAPT,
            d.FEC_DODAE, d.FEC_BAJA, d.BAJ_DODA,
            dd.NUM_REFE, dd.CONS_ID,
            rf.CVE_REFI, rf.NOM_REFI
        FROM SAAIO_DODA d
        JOIN SAAIO_DODADO dd ON dd.ID_DODA = d.ID_DODA
        LEFT JOIN SAAIO_IDEPED ip
            ON ip.NUM_REFE = dd.NUM_REFE AND ip.CVE_IDEN = 'CR'
        LEFT JOIN SAAIC_REFIS rf ON rf.CVE_REFI = ip.COM_IDEN
        WHERE d.CVE_CAAT = ? AND d.FEC_BAJA IS NULL
    """, (settings.CVE_CAAT_KASU,))
```

cambiar el unpacking (líneas ~287-288):

```python
        (id_doda, num_doda, cve_caat, cve_capt, fec_dodae, fec_baja, baj_doda,
         num_refe, cons_id, terminal_cve, terminal_nombre) = row
```

y agregar la clave al dict del `setdefault` (después de `'fecha_baja': fb_datetime_str(fec_baja),`, línea ~300):

```python
            'baj_doda':        clean(baj_doda, 34),
```

Actualizar el docstring: `... con las referencias ligadas ... Retorna una lista de dicts listos para _upsert_dodas: {..., fecha_baja, baj_doda, referencias: [...]}`.

- [ ] **Step 3: Actualizar el test de `fetch_dodas` si existe**

Si el Step 1 encontró un test con filas mock de DODA, agregar el valor de `BAJ_DODA` (p.ej. `None`) en la posición 7 (después de `FEC_BAJA`) de cada tupla mock, y añadir `self.assertIn('baj_doda', out[0])` o equivalente.
Si no hay test de `fetch_dodas`, saltar este step (el módulo no lo cubre).

- [ ] **Step 4: Correr la suite de import_firebird**

Run: `DBURL= python manage.py test referencias.test_import_firebird -v 1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add referencias/management/commands/import_firebird.py referencias/test_import_firebird.py
git commit -m "$(cat <<'EOF'
feat(import_firebird): fetch_dodas emite baj_doda (paridad con sync_agent)

Agrega d.BAJ_DODA al SELECT y la clave baj_doda al dict para emitir la
misma forma que consume _upsert_dodas. Sin filtro incremental: este
comando es el bootstrap/import completo y conserva FEC_BAJA IS NULL.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F7fUwzwKSDgWW8u8hZGJXw
EOF
)"
```

---

### Task 6: Documentación

**Files:**
- Modify: `sync_agent/config.ini.example:44-50` (comentario de `doda_chunk_size`)

**Interfaces:** ninguna — solo comentarios.

- [ ] **Step 1: Ajustar el comentario de `doda_chunk_size`**

En `sync_agent/config.ini.example`, reemplazar el comentario de `doda_chunk_size` (líneas 44-50) por:

```
# Máximo de DODAs por lote enviado a Django. Los DODAs se mandan en tandas
# separadas de las de referencias (después de que todas las referencias del
# run ya fueron confirmadas). El agente solo envía DODAs nuevos o
# modificados desde el último sync (nuevos, recién dados de baja, o
# reemplazados vía BAJ_DODA); en la PRIMERA sync de una patente o con
# --full-sync se envían todos los DODAs activos vigentes y ahí sí puede
# haber miles — un valor chico evita que Django tarde demasiado por lote.
doda_chunk_size = 100
```

- [ ] **Step 2: Verificar que el archivo sigue siendo INI válido**

Run: `python -c "import configparser; c=configparser.ConfigParser(); c.read('sync_agent/config.ini.example'); print(c.getint('opciones','doda_chunk_size'))"`
Expected: imprime `100`.

- [ ] **Step 3: Commit**

```bash
git add sync_agent/config.ini.example
git commit -m "$(cat <<'EOF'
docs(sync_agent): aclarar en config.ini.example que las tandas de DODA
ahora son incrementales

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F7fUwzwKSDgWW8u8hZGJXw
EOF
)"
```

---

## Verificación final (después de todas las tareas)

- [ ] `python sync_agent/test_sync_agent.py` → todo verde.
- [ ] `DBURL= python manage.py test referencias -v 1` → todo verde.
- [ ] `DBURL= python manage.py makemigrations --check --dry-run referencias` → "No changes detected" (el modelo y la migración están en sync).
- [ ] `git log --oneline feature/doda-sync-incremental` → 7 commits (spec + 6 tareas).
- [ ] Revisión manual del diff completo: `git diff main...feature/doda-sync-incremental`.

## Notas de despliegue

- Aplicar la migración `0014_doda_baj_doda` en producción (PostgreSQL DigitalOcean) antes o junto con el deploy del código Django: `python manage.py migrate referencias`.
- Copiar el `sync_agent.py` actualizado a los 3 servidores Windows (1627, 1656, 1927). No cambia `config.ini`.
- El primer sync incremental tras el deploy usa el `last_sync.json` existente de cada patente: puede traer un lote de DODAs acumulados desde esa marca, acotado por fecha (no las ~10 000).
- Fuera de alcance (v1): detectar que a un DODA se le agregó/quitó una referencia en `SAAIO_DODADO` sin cambiar el row de `SAAIO_DODA` (no hay timestamp ahí). `--full-sync` lo resuelve puntualmente.
