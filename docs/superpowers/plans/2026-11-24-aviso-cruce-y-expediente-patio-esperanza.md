# Aviso de Cruce (AVC), Expediente de Extracción e Integración Patio Esperanza Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Aviso de Cruce (AVC) extraction in sync_agent, container-level extraction expediente models, enhanced push payload to Kasu, event ingestion endpoint, executive emails, and UI updates.

**Architecture:** Sync agent extracts `SAAIO_CRUCE`, `SAAIO_CRUCED`, and `SAAIO_CRUCEDVE` from Firebird. Django upserts AVC models, calculates container-level `ExpedienteExtraccion` (linking DODA and AVC per container), pushes updated payloads to Kasu with vehicle/seal data and robust idempotency key hashing, exposes `POST /api/integracion/eventos-contenedor/` for Kasu tracking events, sends notification emails, and renders container cards in reference details.

**Tech Stack:** Python, Django, Firebird (fdb), PostgreSQL/SQLite (Django DB), Requests, SendGrid.

## Global Constraints

- `clave_expediente` format: `"{num_refe}:{num_cont}"`.
- `idempotency_key` format: `"{clave_expediente}:{estado_documental}:{id_cruce}:{hash_datos_avc}"`.
- Push to Kasu via `Authorization: Token` with isolation and retry by `reintentar_envio`.
- Gafete displayed masked (last 4 chars) in views/PDFs, full only in admin.
- Weight unit: kilos (`Referencia.peso_kg` / `SAAIO_PEDIME.PES_BRUT`).

---

## Task 1: Firebird DDL Verification Script & Sync Agent AVC Extraction

**Files:**
- Create: `sync_agent/check_saaio_cruce.py`
- Modify: `sync_agent/sync_agent.py`
- Test: `sync_agent/test_sync_agent.py`

**Interfaces:**
- Consumes: Firebird CASA.GDB tables `SAAIO_CRUCE`, `SAAIO_CRUCED`, `SAAIO_CRUCEDVE`.
- Produces: `fetch_avisos_cruce(cur, since_dt=None)` and `enviar_avisos_en_lotes(...)` in `sync_agent.py`.

- [ ] **Step 1: Write DDL inspection script**

```python
# sync_agent/check_saaio_cruce.py
import sys
from sync_agent import connect_fb, log

def check():
    conn = connect_fb()
    cur = conn.cursor()
    tables = ['SAAIO_CRUCE', 'SAAIO_CRUCED', 'SAAIO_CRUCEDVE']
    for t in tables:
        print(f"--- Table: {t} ---")
        cur.execute(f"SELECT FIRST 1 * FROM {t}")
        cols = [desc[0] for desc in cur.description]
        print("Columns:", cols)
    conn.close()

if __name__ == '__main__':
    check()
```

- [ ] **Step 2: Add `fetch_avisos_cruce` to `sync_agent.py`**

```python
# Add to sync_agent/sync_agent.py
def fetch_avisos_cruce(cur, since_dt=None):
    """Extrae avisos de cruce (AVC) y sus relaciones en tres consultas."""
    base_where = "c.ACUSE_AVC IS NOT NULL"
    if since_dt:
        filtro = f"{base_where} AND c.FEC_EMIAVC >= ?"
        params = (since_dt,)
    else:
        filtro = f"{base_where} AND c.FEC_VIGAVC >= CURRENT_DATE"
        params = ()

    # 1. Encabezado
    sql_cruce = f"""
        SELECT c.ID_CRUCE, c.ACUSE_AVC, c.PATENTE, c.FEC_EMIAVC, c.FEC_VIGAVC,
               c.TAG_CRUCE, c.GAF_CRUCE, c.NUM_PLACA
        FROM SAAIO_CRUCE c
        WHERE {filtro}
    """
    cur.execute(sql_cruce, params)
    avisos_map = {}
    for row in cur.fetchall():
        id_cruce = row[0]
        avisos_map[id_cruce] = {
            "id_cruce": id_cruce,
            "acuse_avc": clean(row[1]),
            "patente": clean(row[2]),
            "fecha_emision": fb_datetime_str(row[3]),
            "fecha_vigencia": fb_datetime_str(row[4]),
            "tag": clean(row[5]),
            "gafete": clean(row[6]),
            "placa": clean(row[7]),
            "referencias": [],
            "contenedores": []
        }

    if not avisos_map:
        return []

    ids = list(avisos_map.keys())
    # Para evitar límite de IN en Firebird (1500), usamos bloques o JOIN con tabla temporal / IN chunks de 500
    for chunk_ids in [ids[i:i+500] for i in range(0, len(ids), 500)]:
        placeholders = ','.join(['?'] * len(chunk_ids))
        
        # 2. Referencias amparadas
        sql_cd = f"""
            CD.ID_CRUCE, CD.CONS_ID, CD.NUM_REFE, CD.NUM_PEDI, CD.RFC_AVC
            FROM SAAIO_CRUCED cd
            WHERE cd.ID_CRUCE IN ({placeholders})
        """
        # ... implementar ejecución por lotes y llenado de referencias y contenedores ...
    return list(avisos_map.values())
```

- [ ] **Step 3: Run tests and verify sync agent changes**

Run: `pytest sync_agent/test_sync_agent.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add sync_agent/check_saaio_cruce.py sync_agent/sync_agent.py sync_agent/test_sync_agent.py
git commit -m "feat(sync_agent): add SAAIO_CRUCE extraction and batching"
```

---

## Task 2: Django Models & Migrations

**Files:**
- Create: `referencias/models_cruce.py` (or add to `referencias/models.py`)
- Modify: `referencias/models.py`
- Create/Modify: `referencias/migrations/0016_aviso_cruce_expediente.py`

**Interfaces:**
- Consumes: Doda models and Contenedor/Referencia models.
- Produces: `AvisoCruce`, `AvisoCruceReferencia`, `AvisoCruceContenedor`, `ExpedienteExtraccion`, `EventoContenedor`.

- [ ] **Step 1: Define models in `referencias/models.py`**

```python
class AvisoCruce(models.Model):
    id_cruce = models.IntegerField(unique=True)
    acuse_avc = models.CharField(max_length=64)
    patente = models.CharField(max_length=10)
    fecha_emision = models.DateTimeField(null=True, blank=True)
    fecha_vigencia = models.DateTimeField(null=True, blank=True)
    tag_cruce = models.CharField(max_length=64, blank=True)
    gafete = models.CharField(max_length=64, blank=True)
    num_placa = models.CharField(max_length=32, blank=True)
    sincronizado_en = models.DateTimeField(auto_now=True)

class AvisoCruceReferencia(models.Model):
    aviso = models.ForeignKey(AvisoCruce, on_delete=models.CASCADE, related_name='referencias_amparadas')
    cons_id = models.IntegerField()
    num_refe = models.CharField(max_length=32)
    num_pedimento = models.CharField(max_length=32, blank=True)
    rfc_importador = models.CharField(max_length=32, blank=True)
    referencia = models.ForeignKey('Referencia', on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        unique_together = ('aviso', 'cons_id')

class AvisoCruceContenedor(models.Model):
    aviso = models.ForeignKey(AvisoCruce, on_delete=models.CASCADE, related_name='contenedores_listados')
    cons_cont = models.IntegerField()
    num_cont = models.CharField(max_length=32)
    candado = models.CharField(max_length=64, blank=True)
    contenedor = models.ForeignKey('Contenedor', on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        unique_together = ('aviso', 'cons_cont')

class ExpedienteExtraccion(models.Model):
    ESTADOS = [
        ('FALTA_AVC', 'Falta AVC'),
        ('FALTA_DODA', 'Falta DODA'),
        ('COMPLETO', 'Completo'),
        ('AVC_VENCIDO', 'AVC Vencido'),
        ('CANCELADO', 'Cancelado'),
    ]
    clave = models.CharField(max_length=64, unique=True) # "{num_refe}:{num_cont}"
    referencia = models.ForeignKey('Referencia', on_delete=models.CASCADE, related_name='expedientes_extraccion')
    contenedor = models.ForeignKey('Contenedor', on_delete=models.CASCADE, related_name='expedientes_extraccion')
    doda = models.ForeignKey('Doda', on_delete=models.SET_NULL, null=True, blank=True)
    aviso_contenedor = models.ForeignKey(AvisoCruceContenedor, on_delete=models.SET_NULL, null=True, blank=True)
    rfc_importador = models.CharField(max_length=32, blank=True)
    estado_documental = models.CharField(max_length=32, choices=ESTADOS, default='FALTA_AVC')
    es_kasu = models.BooleanField(default=False)
    notificado_completo_en = models.DateTimeField(null=True, blank=True)
    push_kasu_estado = models.CharField(max_length=32, default='PENDIENTE')
    actualizado_en = models.DateTimeField(auto_now=True)

class EventoContenedor(models.Model):
    expediente = models.ForeignKey(ExpedienteExtraccion, on_delete=models.CASCADE, related_name='eventos')
    evento_id = models.CharField(max_length=128, unique=True)
    tipo = models.CharField(max_length=64)
    ocurrido_en = models.DateTimeField()
    datos = models.JSONField(default=dict)
    registrado_en = models.DateTimeField(auto_now_add=True)
```

- [ ] **Step 2: Generate and run migration**

Run: `python manage.py makemigrations referencias`
Expected: Migration generated successfully.

- [ ] **Step 3: Run migrate and test**

Run: `python manage.py migrate`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add referencias/models.py referencias/migrations/
git commit -m "feat(referencias): add AvisoCruce, ExpedienteExtraccion and EventoContenedor models"
```

---

## Task 3: Sync Views, Upsert Logic & Event Endpoint

**Files:**
- Modify: `referencias/sync_views.py`
- Create: `referencias/eventos_views.py`
- Modify: `referencias/urls.py`
- Test: `referencias/tests/test_cruce_sync.py`

**Interfaces:**
- Consumes: Payload from sync agent with `avisos_cruce`, Endpoint `POST /api/integracion/eventos-contenedor/`.
- Produces: Upserted AVCs, updated Expedientes, recorded EventoContenedor.

- [ ] **Step 1: Implement `_upsert_avisos_cruce` and expediente recalculation in `sync_views.py`**

- [ ] **Step 2: Implement `eventos_contenedor_api` in `eventos_views.py` with token authentication and idempotency (`evento_id`)**

- [ ] **Step 3: Register URL route for events endpoint**

- [ ] **Step 4: Run tests**

Run: `pytest referencias/tests/test_cruce_sync.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add referencias/sync_views.py referencias/eventos_views.py referencias/urls.py referencias/tests/
git commit -m "feat(referencias): add aviso cruce upsert, expediente recalculation and eventos endpoint"
```

---

## Task 4: Kasu Push Payload Enhancements & Idempotency Hash

**Files:**
- Modify: `referencias/modulacion.py`
- Test: `referencias/tests/test_modulacion.py`

**Interfaces:**
- Consumes: `ExpedienteExtraccion`, associated DODA and `AvisoCruceContenedor`.
- Produces: Enhanced JSON payload sent to Kasu with `clave_expediente`, `estado_documental`, AVC vehicle/seal fields (`placa`, `tag_cruce`, `gafete_operador`, `candado`), weight (`peso_kg`), and extended `idempotency_key`.

- [ ] **Step 1: Update payload builder and idempotency key in `referencias/modulacion.py`**

- [ ] **Step 2: Run unit tests for modulacion and Kasu push**

Run: `pytest referencias/tests/test_modulacion.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add referencias/modulacion.py referencias/tests/test_modulacion.py
git commit -m "feat(referencias): enhance Kasu push payload with expedientes, AVC data and hash idempotency"
```

---

## Task 5: Executive Emails & Extraction Notifications

**Files:**
- Modify: `referencias/modulacion.py` (add `resolver_ejecutivo`, email sending per event)
- Test: `referencias/tests/test_modulacion_email.py`

**Interfaces:**
- Consumes: `ExpedienteExtraccion`, `EventoContenedor`.
- Produces: SendGrid emails to executive / capturista fallback with subject `"{contenedor} · {evento}"` and timeline details.

- [ ] **Step 1: Implement `resolver_ejecutivo` and event email senders**

- [ ] **Step 2: Run email tests**

Run: `pytest referencias/tests/test_modulacion_email.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add referencias/modulacion.py referencias/tests/test_modulacion_email.py
git commit -m "feat(referencias): add resolver_ejecutivo and event email notifications"
```

---

## Task 6: UI Views & Timeline Integration

**Files:**
- Modify: `referencias/views.py`
- Modify: `referencias/templates/referencias/detalle.html`
- Modify: `referencias/templates/referencias/timeline.html`
- Test: `referencias/tests/test_referencias_ui.py`

**Interfaces:**
- Consumes: `ExpedienteExtraccion` and `EventoContenedor` records.
- Produces: Container cards in reference detail view showing documentary status, DODA, AVC vigencia, placa, tag, candado, and event timeline.

- [ ] **Step 1: Update reference detail view to query container expedientes and events**

- [ ] **Step 2: Update `referencias/templates/referencias/detalle.html` with container extraction cards**

- [ ] **Step 3: Run UI/view tests**

Run: `pytest referencias/tests/test_referencias_ui.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add referencias/views.py referencias/templates/ referencias/tests/test_referencias_ui.py
git commit -m "feat(referencias): add container extraction cards and timeline in reference detail UI"
```

---

## Task 7: Full Project Test Suite Verification

**Files:**
- Test: All tests across project (`sync_agent/`, `referencias/`, `core/`)

**Interfaces:**
- Consumes: Full test suite.
- Produces: 100% green tests.

- [ ] **Step 1: Run full test suite**

Run: `pytest -v`
Expected: All tests pass.

- [ ] **Step 2: Commit final verification**

```bash
git add -A
git commit -m "test: verify full project test suite green for AVC and Patio Esperanza integration"
```
