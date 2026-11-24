# Glosa Automática con IA Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `glosa` app from `proyecto_hal9mil_reiki` to `Proyecto_HAL9MIL` (Loginco, patentes 1627, 1656, 1927), including document versioning, Jev AI classification, Claude data extraction, deterministic cross-checking with containers/weight in tons/partidas, annotated PDF generation, Glosador review flow, and `/glosa/` operational list with KPIs.

**Architecture:** Django app `glosa` integrated into Loginco. Services layer (`services_jev_ia.py`, `services_extraccion.py`, `services_cruce.py`, `services_cruce_partidas.py`, `services_correccion.py`, `services_anotacion.py`, `services_aaalac.py`) with models (`DocumentoReferencia`, `GlosaAutomaticaResultado`, `GlosaEvento`), views, templates, and templatetags.

**Tech Stack:** Django 5.x, Python 3.10+, Anthropic SDK, Requests, PyMuPDF (`pymupdf`), Pillow, Tailwind, Alpine.js.

## Global Constraints

- Patentes: `1627`, `1656`, `1927` (no fixed Reiki/3772 text).
- `Referencia.peso_bruto` in CASA is in **tonnes** (`ref.peso_kg` property).
- Storage: Private DO Spaces via `MediaStorage`.
- Dependency floors: `pymupdf>=1.24`, `Pillow>=10.0`.
- Extraction: Claude `claude-sonnet-5-5`, `output_config` via `extra_body`, no forced `tool_choice`.
- Classification: Jev System One API (`https://api.typesafe.ai/v1/systemone`) with companion noul pattern.
- Zero-cost/deterministic discrepancy engine in Python (no LLM makes discrepancy decisions).

---

### Task 1: Dependencies, App Scaffolding, Models & Migrations

**Files:**
- Modify: `requirements.txt`
- Modify: `hal9mil/settings.py`
- Modify: `hal9mil/urls.py`
- Create: `glosa/__init__.py`
- Create: `glosa/apps.py`
- Create: `glosa/models.py`
- Create: `glosa/admin.py`
- Create: `glosa/migrations/0001_initial.py`
- Create: `glosa/migrations/0002_grupo_glosador.py`

**Interfaces:**
- Produces: `glosa` app models (`DocumentoReferencia`, `GlosaAutomaticaResultado`, `GlosaEvento`), `Glosador` group, and settings.

- [ ] **Step 1: Add pymupdf and Pillow to requirements.txt**

Modify `requirements.txt` to add:
```
pymupdf>=1.24
Pillow>=10.0
```

- [ ] **Step 2: Register glosa in settings.py and configure URLs**

Modify `hal9mil/settings.py` to add `'glosa'` to `INSTALLED_APPS` (after `'referencias'`) and add `GLOSA_NOMBRE_AGENCIA = 'Loginco'`.
Modify `hal9mil/urls.py` to include `path('', include('glosa.urls'))` **before** `include('referencias.urls')`.

- [ ] **Step 3: Create glosa app structure and models.py**

Create `glosa/models.py` with `DocumentoReferencia`, `GlosaAutomaticaResultado`, and `GlosaEvento` as specified in spec sections 3.1, 3.2, 3.3. Include `OTRO_SIN_ESQUEMA` in `TIPO_CHOICES`.

- [ ] **Step 4: Create migrations 0001 and 0002**

Create `glosa/migrations/0001_initial.py` (initial schema with all three models and `OTRO_SIN_ESQUEMA`) and `glosa/migrations/0002_grupo_glosador.py` (`RunPython` creating `'Glosador'` group).

- [ ] **Step 5: Register models in glosa/admin.py**

Create `glosa/admin.py` with read-only / list display configurations for `DocumentoReferencia`, `GlosaAutomaticaResultado`, and `GlosaEvento`.

- [ ] **Step 6: Run migrations and verify**

Run: `python manage.py makemigrations glosa && python manage.py migrate`
Expected: Migrations apply cleanly.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt hal9mil/settings.py hal9mil/urls.py glosa/
git commit -m "feat(glosa): add glosa app models, migrations, and settings"
```

---

### Task 2: Services - Jev AI Classification & Claude Extraction

**Files:**
- Create: `glosa/services_jev_ia.py`
- Create: `glosa/services_extraccion.py`
- Create: `glosa/permisos.py`
- Test: `glosa/tests/test_jev_ia.py`
- Test: `glosa/tests/test_extraccion.py`

**Interfaces:**
- Consumes: `settings.IA_HABILITADA`, `settings.ANTHROPIC_API_KEY`, `JEV_AI_API_KEY`, etc.
- Produces: `clasificar_documento`, `leer_documento`, `clasificar_por_palabras_clave`, `extraer_datos_documento`, `validar_y_normalizar`.

- [ ] **Step 1: Write failing tests for Jev AI classification**

Create `glosa/tests/test_jev_ia.py` testing Jev API client, fallback keyword classification, PDF text vs rasterization, and decision thresholds.

- [ ] **Step 2: Implement glosa/services_jev_ia.py**

Create `glosa/services_jev_ia.py` implementing `clasificar_documento`, `leer_documento`, `extraer_texto_pdf`, `consultar_jev_ia`, `jev_habilitado`, `clasificar_por_palabras_clave`, and fallback handling.

- [ ] **Step 3: Run Jev tests to verify they pass**

Run: `python manage.py test glosa.tests.test_jev_ia`
Expected: PASS

- [ ] **Step 4: Write failing tests for Claude extraction and validation**

Create `glosa/tests/test_extraccion.py` testing schema constraints (`additionalProperties: false`, required fields), ISO 6346 container validation, BL consolidation, and decimal weight/amount parsing.

- [ ] **Step 5: Implement glosa/services_extraccion.py and glosa/permisos.py**

Create `glosa/services_extraccion.py` with `extraer_datos_documento`, `validar_y_normalizar`, schemas for BL, FACTURA, LISTA_EMPAQUE, CARTA_TECNICA, CERTIFICADOS, PERMISOS, OTROS, PREFORMA.
Create `glosa/permisos.py` with `es_glosador(user)`.

- [ ] **Step 6: Run extraction tests to verify they pass**

Run: `python manage.py test glosa.tests.test_extraccion`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add glosa/services_jev_ia.py glosa/services_extraccion.py glosa/permisos.py glosa/tests/
git commit -m "feat(glosa): add Jev classification and Claude extraction services with tests"
```

---

### Task 3: Services - Cross-Checking (Cruce & Partidas)

**Files:**
- Create: `glosa/services_cruce.py`
- Create: `glosa/services_cruce_partidas.py`
- Test: `glosa/tests/test_cruce.py`
- Test: `glosa/tests/test_cruce_partidas.py`

**Interfaces:**
- Consumes: `DocumentoReferencia` data, `Referencia` (`peso_kg`, etc.).
- Produces: `ejecutar_cruce`, `cruzar_partidas`.

- [ ] **Step 1: Write failing tests for cruce and cruce_partidas**

Create `glosa/tests/test_cruce.py` and `glosa/tests/test_cruce_partidas.py` covering ton weight conversion (`peso_kg`), blocker checks, container consensus, discrepancies, NOM/permits matching, and item groups.

- [ ] **Step 2: Implement glosa/services_cruce.py and glosa/services_cruce_partidas.py**

Create `glosa/services_cruce.py` using `ref.peso_kg` for CASA weight baseline.
Create `glosa/services_cruce_partidas.py` with part-level comparison, token similarity matching, and discrepancy generation.

- [ ] **Step 3: Run cruce tests**

Run: `python manage.py test glosa.tests.test_cruce glosa.tests.test_cruce_partidas`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add glosa/services_cruce.py glosa/services_cruce_partidas.py glosa/tests/test_cruce.py glosa/tests/test_cruce_partidas.py
git commit -m "feat(glosa): add deterministic cruce and part-level cruce services with tests"
```

---

### Task 4: Services - Correction, Annotation & AAALAC Stubs

**Files:**
- Create: `glosa/services_correccion.py`
- Create: `glosa/services_anotacion.py`
- Create: `glosa/services_aaalac.py`
- Test: `glosa/tests/test_correccion.py`
- Test: `glosa/tests/test_anotacion.py`

**Interfaces:**
- Consumes: `DocumentoReferencia`, `GlosaAutomaticaResultado`.
- Produces: `reemplazar_documento`, `recalcular_glosa`, `glosa_aprobada`, `generar_preforma_anotada` (with `settings.GLOSA_NOMBRE_AGENCIA` and `referencia.patente`).

- [ ] **Step 1: Write failing tests for correction and annotation**

Create `glosa/tests/test_correccion.py` and `glosa/tests/test_anotacion.py`.

- [ ] **Step 2: Implement glosa/services_correccion.py, glosa/services_anotacion.py, and glosa/services_aaalac.py**

Create `glosa/services_correccion.py`, `glosa/services_anotacion.py` (customizing header with `GLOSA_NOMBRE_AGENCIA` and `patente`), and `glosa/services_aaalac.py` (stub).

- [ ] **Step 3: Run correction and annotation tests**

Run: `python manage.py test glosa.tests.test_correccion glosa.tests.test_anotacion`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add glosa/services_correccion.py glosa/services_anotacion.py glosa/services_aaalac.py glosa/tests/test_correccion.py glosa/tests/test_anotacion.py
git commit -m "feat(glosa): add correction, annotated PDF, and AAALAC stub services with tests"
```

---

### Task 5: Views, URLs, Templatetags & Templates

**Files:**
- Create: `glosa/urls.py`
- Create: `glosa/views.py`
- Create: `glosa/templatetags/glosa_extras.py`
- Create: `glosa/templates/glosa/glosa_ia_detalle.html`
- Create: `glosa/templates/glosa/_celda_partida.html`
- Modify: `templates/referencias/detalle.html`
- Test: `glosa/tests/test_glosa_views.py`
- Test: `glosa/tests/test_glosa_integration.py`

**Interfaces:**
- Consumes: Glosa services and models.
- Produces: Glosa detail and management web views, template tags (`find_fuente`, `split`), and expediente section in reference detail.

- [ ] **Step 1: Create glosa/urls.py and glosa/templatetags/glosa_extras.py**

Create `glosa/urls.py` with all routes (`glosa-ia/`, `actualizar/`, `reemplazar/`, `preforma-anotada/`, `aprobar/`, `rechazar/`, `reabrir/`).
Create `glosa/templatetags/glosa_extras.py`.

- [ ] **Step 2: Implement glosa/views.py**

Create `glosa/views.py` implementing `glosa_ia_detalle`, `glosa_documento_actualizar`, `glosa_documento_reemplazar`, `glosa_preforma_anotada`, `glosa_aprobar`, `glosa_rechazar`, `glosa_reabrir`, `consultar_aaalac`, with `subido_por=request.user` and active BL filtering.

- [ ] **Step 3: Create HTML templates**

Create `glosa/templates/glosa/glosa_ia_detalle.html` and `glosa/templates/glosa/_celda_partida.html`. Update `templates/referencias/detalle.html` to include the Expediente section.

- [ ] **Step 4: Write tests for views and integration**

Create `glosa/tests/test_glosa_views.py` and `glosa/tests/test_glosa_integration.py`.

- [ ] **Step 5: Run view and integration tests**

Run: `python manage.py test glosa.tests.test_glosa_views glosa.tests.test_glosa_integration`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add glosa/urls.py glosa/views.py glosa/templatetags/ glosa/templates/ templates/referencias/detalle.html glosa/tests/test_glosa_views.py glosa/tests/test_glosa_integration.py
git commit -m "feat(glosa): add views, URLs, templates, templatetags, and integration tests"
```

---

### Task 6: Operational List `/glosa/`, KPIs & Reference View Integration

**Files:**
- Modify: `referencias/views.py`
- Modify: `templates/referencias/glosa.html`
- Test: `glosa/tests/test_glosa_views.py` (add list/KPI tests)

**Interfaces:**
- Consumes: Glosa results.
- Produces: `_anotar_glosa_ia`, filter `?ia=`, KPIs, and column on `/glosa/`.

- [ ] **Step 1: Port `_anotar_glosa_ia` and filtering to referencias/views.py**

Add `_anotar_glosa_ia`, constants, and `?ia=` query parameter handling to `referencias/views.py`.

- [ ] **Step 2: Update templates/referencias/glosa.html**

Add the "Glosa IA" column, KPI summary chips, and filter UI to `templates/referencias/glosa.html`.

- [ ] **Step 3: Run full glosa test suite**

Run: `python manage.py test glosa referencias`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add referencias/views.py templates/referencias/glosa.html glosa/tests/
git commit -m "feat(glosa): add operational list /glosa/ with KPIs, filters, and column"
```

---

### Task 7: Review and Final Cleanup

**Files:**
- Audit codebase for fixed Reiki/3772 references.

- [ ] **Step 1: Grep audit for Reiki / 3772**

Run: `grep -rn -i "reiki\|3772" glosa/`
Verify no hardcoded Reiki strings remain.

- [ ] **Step 2: Run full project test suite**

Run: `python manage.py test`
Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add glosa/ templates/
git commit -m "refactor(glosa): final audit and cleanup of reiki references"
```
