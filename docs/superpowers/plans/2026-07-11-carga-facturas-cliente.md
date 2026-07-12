# Carga de Facturas de Cliente → XML Pendientes — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nuevo apartado para subir facturas CFDI dirigidas a clientes (XML + PDF sueltos) que caen al listado de XML pendientes, donde el RFC del receptor identifica al cliente y sugiere sus referencias para la asignación manual.

**Architecture:** Se reutiliza el pipeline existente de carga masiva (`finanzas/carga_xml.py`: `expandir_subidas` + `procesar_lote`) — estos CFDIs tienen emisor no soportado por los extractores (no LCT/APM), así que caen naturalmente a `PENDIENTE`, y el parser ya captura `rfc_receptor`. Se agrega una vista de carga dedicada, y el listado de pendientes se enriquece con el cliente detectado (`Cliente.rfc == rfc_receptor`) y un selector de referencias de ese cliente. Sin modelos nuevos. Prerequisito: hacer el storage de archivos de `XMLProveedor` seleccionable por entorno (hoy está hardcodeado a DO Spaces y rompe los tests).

**Tech Stack:** Django 6.0.5 (instalado; el proyecto documenta 5.2+), Python 3.12, Tailwind CSS (CDN), PostgreSQL, django-storages/boto3 (DO Spaces, solo producción).

**Spec:** `docs/superpowers/specs/2026-07-11-carga-facturas-cliente-design.md`

## Global Constraints

- Directorio de trabajo: `/home/tony/Developer/Proyecto_HAL9MIL/` — entorno virtual `.venv/`, activar con `source .venv/bin/activate`.
- Crear una rama de feature antes de empezar (no trabajar en `main`).
- Vistas de finanzas usan `@modulo_required('Finanzas')` (de `core.permisos`), NUNCA `@login_required`.
- Tests con `django.test.TestCase`. La BD de tests es Postgres remota: correr siempre con `--keepdb` y preferir clases específicas (la suite completa tarda ~2 min).
- Estado conocido: 24 tests de `finanzas.test_carga_masiva` y `finanzas.tests.EmbudoAPTest` fallan hoy porque `XMLProveedor.xml_file/pdf_file` usan `MediaStorage()` (DO Spaces) sin bucket configurado en el entorno local. La Task 1 lo corrige.
- Estado conocido: hay un cambio de modelo sin migración (`makemigrations` pendiente del commit de DO Spaces). La Task 1 lo captura.
- Locale `es-mx` / `America/Mexico_City`. Tailwind vía CDN, sin build.

---

## File Map

| Archivo | Acción | Responsabilidad |
|---------|--------|-----------------|
| `hal9mil/storage_backends.py` | Modificar | Agregar callable `media_storage()` seleccionable por entorno |
| `finanzas/models.py` | Modificar | `XMLProveedor.xml_file/pdf_file` usan el callable |
| `finanzas/migrations/0012_*.py` | Crear (auto) | Captura el cambio de storage (incluye el pendiente) |
| `finanzas/cfdi_de_prueba.py` | Modificar | Fixture `cfdi_cliente()` — CFDI genérico de emisor no soportado |
| `finanzas/views.py` | Modificar | Helper `_procesar_subida_xml`, vista `carga_xml_cliente`, mejoras a `xml_pendientes` |
| `finanzas/urls.py` | Modificar | URL `xml/carga-cliente/` |
| `finanzas/test_carga_cliente.py` | Crear | Tests de la vista de carga y del listado enriquecido |
| `templates/finanzas/carga_cliente_form.html` | Crear | Formulario del nuevo apartado |
| `templates/finanzas/xml_pendientes.html` | Modificar | Columnas RFC/Cliente + selector de sugerencias |
| `templates/finanzas/dashboard.html` | Modificar | Tarjeta de acceso al nuevo apartado |

---

## Task 1: Storage seleccionable por entorno (prerequisito)

**Contexto:** El commit "Implementacion de Guardado enn DO Space" dejó `storage=MediaStorage()` hardcodeado en los dos `FileField` de `XMLProveedor`. En entornos sin bucket (local/tests) cualquier guardado de archivo truena con `TypeError: expected string or bytes-like object, got 'NoneType'` (bucket `None`), rompiendo 24 tests existentes — y rompería los tests de este plan. Django soporta callables como `storage`: se evalúan al cargar el modelo, permitiendo elegir DO Spaces en producción y disco local en dev/tests.

**Files:**
- Modify: `hal9mil/storage_backends.py` (agregar función al final)
- Modify: `finanzas/models.py:6` (import) y campos `xml_file`/`pdf_file` de `XMLProveedor`
- Create (auto): `finanzas/migrations/0012_*.py`

**Interfaces:**
- Produces: `hal9mil.storage_backends.media_storage() -> Storage` — callable usado por los `FileField`; Tasks 2 y 3 dependen de que los tests puedan guardar archivos en disco local.

- [ ] **Step 1: Agregar el callable en `hal9mil/storage_backends.py`**

Al final del archivo:

```python
def media_storage():
    """Storage para FileFields de media: DO Spaces si hay bucket configurado,
    disco local (default_storage) si no — p. ej. en desarrollo y tests."""
    from django.core.files.storage import default_storage
    if getattr(settings, 'AWS_STORAGE_BUCKET_NAME', None):
        return MediaStorage()
    return default_storage
```

- [ ] **Step 2: Usar el callable en `finanzas/models.py`**

Cambiar el import (línea ~6):

```python
from hal9mil.storage_backends import media_storage
```

(reemplaza `from hal9mil.storage_backends import MediaStorage`)

Y en `XMLProveedor`, los dos campos:

```python
    xml_file = models.FileField(storage=media_storage, upload_to='xmls_proveedores/%Y/%m/')
```

```python
    pdf_file = models.FileField(
        storage=media_storage, upload_to='xmls_proveedores/%Y/%m/', null=True, blank=True
    )
```

Nota: `storage=media_storage` sin paréntesis — es un callable, no una instancia.

- [ ] **Step 3: Generar y aplicar la migración**

```bash
source .venv/bin/activate
python manage.py makemigrations finanzas --name media_storage_callable
python manage.py migrate
```

Esperado: `finanzas/migrations/0012_media_storage_callable.py` con `AlterField` sobre `xml_file` y `pdf_file`. (Esto también captura el cambio de storage que estaba pendiente de migración.)

- [ ] **Step 4: Verificar que los tests rotos ahora pasan**

```bash
python manage.py test finanzas.test_carga_masiva --keepdb --verbosity=1
```

Esperado: `OK` (antes: 24 errores por bucket `None`). Si algún error persiste, es de este cambio — no continuar hasta resolverlo.

- [ ] **Step 5: Verificar que el check global sigue limpio**

```bash
python manage.py check
```

Esperado: `System check identified no issues (0 silenced).`

- [ ] **Step 6: Commit**

```bash
git add hal9mil/storage_backends.py finanzas/models.py finanzas/migrations/0012_*.py
git commit -m "fix(finanzas): storage de XMLProveedor seleccionable por entorno (DO Spaces o disco local)"
```

---

## Task 2: Apartado de carga de facturas de cliente

**Files:**
- Modify: `finanzas/cfdi_de_prueba.py` (agregar fixture al final)
- Create: `finanzas/test_carga_cliente.py`
- Modify: `finanzas/views.py` (helper compartido + vista nueva; refactor de `carga_masiva_xml`)
- Modify: `finanzas/urls.py`
- Create: `templates/finanzas/carga_cliente_form.html`
- Modify: `templates/finanzas/dashboard.html`, `templates/finanzas/xml_pendientes.html` (enlaces)

**Interfaces:**
- Consumes: `expandir_subidas(uploaded_files)`, `procesar_lote(files, usuario)` de `finanzas/carga_xml.py`; `media_storage` (Task 1) para que los tests guarden en disco.
- Produces: URL `finanzas:carga_xml_cliente` (GET form / POST procesa); fixture `cfdi_cliente(uuid, rfc_receptor, nombre_receptor) -> bytes` usado también en Task 3.

- [ ] **Step 1: Agregar el fixture `cfdi_cliente` a `finanzas/cfdi_de_prueba.py`**

Al final del archivo:

```python
_PLANTILLA_CLIENTE = '''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
    Version="4.0" Serie="F" Folio="10234" Fecha="2026-07-10T12:00:00"
    SubTotal="5000.00" Moneda="MXN" Total="5800.00" TipoDeComprobante="I"
    MetodoPago="PPD" LugarExpedicion="06600">
  <cfdi:Emisor Rfc="FPA010101AA1" Nombre="FLETES DEL PACIFICO" RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="{rfc_receptor}" Nombre="{nombre_receptor}" UsoCFDI="G03" DomicilioFiscalReceptor="06600" RegimenFiscalReceptor="601"/>
  <cfdi:Conceptos>
    <cfdi:Concepto Cantidad="1" ClaveProdServ="78101800" ClaveUnidad="E48" Descripcion="FLETE TERRESTRE" ValorUnitario="5000.00" Importe="5000.00" ObjetoImp="02"/>
  </cfdi:Conceptos>
  <cfdi:Impuestos TotalImpuestosTrasladados="800.00">
    <cfdi:Traslados>
      <cfdi:Traslado Impuesto="002" TipoFactor="Tasa" Base="5000.00" TasaOCuota="0.160000" Importe="800.00"/>
    </cfdi:Traslados>
  </cfdi:Impuestos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="1.1" UUID="{uuid}" FechaTimbrado="2026-07-10T12:00:05"/>
  </cfdi:Complemento>
</cfdi:Comprobante>'''


def cfdi_cliente(uuid='33333333-3333-3333-3333-333333333333',
                 rfc_receptor='XAXX010101ABC',
                 nombre_receptor='CLIENTE EJEMPLO'):
    """CFDI de un emisor NO soportado por los extractores (cae a PENDIENTE)."""
    return _PLANTILLA_CLIENTE.format(
        uuid=uuid, rfc_receptor=rfc_receptor, nombre_receptor=nombre_receptor,
    ).encode('utf-8')
```

El RFC emisor `FPA010101AA1` no está en `_EXTRACTORES` (`finanzas/extractores.py`), por lo que `extraer_datos_aduanales` devuelve `None` y el XML queda `PENDIENTE` con motivo "Proveedor no soportado" — el comportamiento que este apartado necesita.

- [ ] **Step 2: Escribir los tests que fallan — crear `finanzas/test_carga_cliente.py`**

```python
import tempfile

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .cfdi_de_prueba import cfdi_cliente
from .models import XMLProveedor

MEDIA_TMP = tempfile.mkdtemp()


def _login_finanzas(test, username='carga_cliente_user'):
    grupo, _ = Group.objects.get_or_create(name='Finanzas')
    test.user = User.objects.create_user(username, password='x')
    test.user.groups.add(grupo)
    test.client.login(username=username, password='x')


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class CargaClienteViewTests(TestCase):
    def setUp(self):
        _login_finanzas(self)

    def test_get_muestra_formulario(self):
        resp = self.client.get(reverse('finanzas:carga_xml_cliente'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Cargar facturas de cliente')

    def test_usuario_sin_grupo_es_redirigido(self):
        User.objects.create_user('sin_grupo', password='x')
        self.client.login(username='sin_grupo', password='x')
        resp = self.client.get(reverse('finanzas:carga_xml_cliente'))
        self.assertEqual(resp.status_code, 302)

    def test_post_sin_archivos_redirige_con_error(self):
        resp = self.client.post(reverse('finanzas:carga_xml_cliente'), {})
        self.assertRedirects(resp, reverse('finanzas:carga_xml_cliente'))

    def test_post_xml_cliente_queda_pendiente_con_rfc_y_pdf(self):
        xml = SimpleUploadedFile('F10234.xml', cfdi_cliente(rfc_receptor='CIN220216BS2'))
        pdf = SimpleUploadedFile('F10234.pdf', b'%PDF-1.4 prueba')
        resp = self.client.post(
            reverse('finanzas:carga_xml_cliente'), {'archivos': [xml, pdf]}
        )
        self.assertEqual(resp.status_code, 200)
        obj = XMLProveedor.objects.get(
            uuid_fiscal='33333333-3333-3333-3333-333333333333'
        )
        self.assertEqual(obj.estado_asignacion, 'PENDIENTE')
        self.assertEqual(obj.rfc_receptor, 'CIN220216BS2')
        self.assertTrue(obj.pdf_file)

    def test_post_duplicado_no_crea_segundo_registro(self):
        for _ in range(2):
            xml = SimpleUploadedFile('F10234.xml', cfdi_cliente())
            self.client.post(reverse('finanzas:carga_xml_cliente'), {'archivos': [xml]})
        self.assertEqual(
            XMLProveedor.objects.filter(
                uuid_fiscal='33333333-3333-3333-3333-333333333333'
            ).count(),
            1,
        )
```

- [ ] **Step 3: Correr los tests y verificar que fallan por la URL inexistente**

```bash
python manage.py test finanzas.test_carga_cliente --keepdb --verbosity=2
```

Esperado: errores `NoReverseMatch: Reverse for 'carga_xml_cliente' not found`.

- [ ] **Step 4: Refactorizar `carga_masiva_xml` extrayendo el helper compartido**

En `finanzas/views.py`, la vista `carga_masiva_xml` (línea ~1074) duplicaría todo su cuerpo con la vista nueva. Extraer el procesamiento a un helper privado y dejar ambas vistas delgadas. Reemplazar la función `carga_masiva_xml` completa por:

```python
def _procesar_subida_xml(request, url_retorno):
    """Procesa request.FILES['archivos'] con el pipeline de carga y devuelve
    la respuesta (resumen o redirect con mensaje de error)."""
    archivos = request.FILES.getlist('archivos')
    if not archivos:
        messages.error(request, 'No se seleccionó ningún archivo.')
        return redirect(url_retorno)

    try:
        files = expandir_subidas(archivos)
    except zipfile.BadZipFile:
        messages.error(request, 'El archivo ZIP es inválido o está dañado.')
        return redirect(url_retorno)

    resultados = procesar_lote(files, request.user)
    if not resultados:
        messages.error(request, 'No se encontró ningún archivo XML en lo subido.')
        return redirect(url_retorno)

    conteos = {
        'asignados': sum(1 for r in resultados if r.estado == 'ASIGNADO'),
        'pendientes': sum(1 for r in resultados if r.estado == 'PENDIENTE'),
        'duplicados': sum(1 for r in resultados if r.estado == 'DUPLICADO'),
        'errores': sum(1 for r in resultados if r.estado == 'ERROR'),
    }
    return render(request, 'finanzas/carga_masiva_resultado.html', {
        'resultados': resultados,
        'conteos': conteos,
    })


@modulo_required('Finanzas')
def carga_masiva_xml(request):
    if request.method != 'POST':
        return render(request, 'finanzas/carga_masiva_form.html')
    return _procesar_subida_xml(request, 'finanzas:carga_masiva_xml')


@modulo_required('Finanzas')
def carga_xml_cliente(request):
    if request.method != 'POST':
        return render(request, 'finanzas/carga_cliente_form.html')
    return _procesar_subida_xml(request, 'finanzas:carga_xml_cliente')
```

- [ ] **Step 5: Agregar la URL en `finanzas/urls.py`**

Inmediatamente después de la línea de `xml/carga-masiva/`:

```python
    path('xml/carga-cliente/', views.carga_xml_cliente, name='carga_xml_cliente'),
```

- [ ] **Step 6: Crear `templates/finanzas/carga_cliente_form.html`**

```html
{% extends 'base.html' %}
{% block title %}Cargar facturas de cliente · Finanzas{% endblock %}
{% block content %}
<div class="p-6 max-w-2xl">

  <div class="mb-6">
    <a href="{% url 'finanzas:dashboard' %}" class="text-sky-600 hover:underline text-sm">← Finanzas</a>
    <h1 class="text-2xl font-bold text-slate-800 mt-2">Cargar facturas de cliente</h1>
    <p class="text-slate-500 text-sm">
      Sube los XML y sus PDF (emparejados por nombre de archivo, ej.
      <span class="font-mono">F123.xml</span> + <span class="font-mono">F123.pdf</span>).
      Las facturas quedarán en
      <a href="{% url 'finanzas:xml_pendientes' %}" class="text-sky-600 hover:underline">XMLs pendientes</a>,
      donde el RFC del receptor te ayudará a anexarlas a la referencia del cliente.
    </p>
  </div>

  <form method="post" enctype="multipart/form-data"
        class="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
    {% csrf_token %}
    <div>
      <label class="block text-xs font-medium text-slate-600 mb-1">
        Archivos (XML y PDF) <span class="text-red-500">*</span>
      </label>
      <input type="file" name="archivos" multiple accept=".xml,.pdf"
             class="block w-full text-sm text-slate-600">
    </div>
    <div class="pt-2 flex gap-3">
      <button type="submit"
              class="bg-slate-600 hover:bg-slate-700 text-white font-medium px-6 py-2 rounded-lg text-sm transition-colors">
        Procesar
      </button>
    </div>
  </form>

</div>
{% endblock %}
```

- [ ] **Step 7: Correr los tests y verificar que pasan**

```bash
python manage.py test finanzas.test_carga_cliente --keepdb --verbosity=2
```

Esperado: `OK` — 5 tests pasando.

- [ ] **Step 8: Verificar que la carga masiva no se rompió con el refactor**

```bash
python manage.py test finanzas.test_carga_masiva --keepdb --verbosity=1
```

Esperado: `OK`.

- [ ] **Step 9: Agregar los enlaces de acceso**

En `templates/finanzas/dashboard.html`, junto a la tarjeta/enlace existente de carga masiva de XMLs (buscar `carga_masiva_xml` en el archivo y copiar el patrón de la tarjeta vecina), agregar:

```html
<a href="{% url 'finanzas:carga_xml_cliente' %}"
   class="bg-white rounded-xl border border-slate-200 p-5 hover:border-sky-400 hover:shadow-sm transition-all">
  <p class="text-xs text-sky-500 uppercase tracking-wider mb-1">Facturas de cliente</p>
  <p class="text-sm text-slate-600">Cargar XML/PDF dirigidos a clientes</p>
</a>
```

(Ajustar solo las clases del contenedor si la tarjeta vecina usa un patrón distinto — el objetivo es que se vea igual que las demás tarjetas de esa sección.)

En `templates/finanzas/xml_pendientes.html`, en el encabezado (línea ~7, junto al enlace "← Carga masiva"):

```html
    <a href="{% url 'finanzas:carga_xml_cliente' %}" class="text-sky-600 hover:underline text-sm ml-4">Cargar facturas de cliente</a>
```

- [ ] **Step 10: Verificación visual rápida**

Con el servidor corriendo (`python manage.py runserver 8001`), visitar
`http://127.0.0.1:8001/finanzas/xml/carga-cliente/` — el formulario carga con el
título "Cargar facturas de cliente" y los enlaces del dashboard/pendientes apuntan bien.

- [ ] **Step 11: Commit**

```bash
git add finanzas/cfdi_de_prueba.py finanzas/test_carga_cliente.py finanzas/views.py finanzas/urls.py templates/finanzas/carga_cliente_form.html templates/finanzas/dashboard.html templates/finanzas/xml_pendientes.html
git commit -m "feat(finanzas): apartado de carga de facturas de cliente hacia XML pendientes"
```

---

## Task 3: RFC receptor, cliente detectado y sugerencias en XML pendientes

**Files:**
- Modify: `finanzas/test_carga_cliente.py` (agregar clase de tests al final)
- Modify: `finanzas/views.py` — vista `xml_pendientes` (parte GET)
- Modify: `templates/finanzas/xml_pendientes.html` (columnas + selector)

**Interfaces:**
- Consumes: `cfdi_cliente` (Task 2); `Cliente.rfc`, `Cliente.cve_cliente`, `Cliente.nombre_cliente` (`clientes/models.py`); `Referencia.cve_cliente`, `Referencia.num_refe`, `Referencia.fecha_pago` (`referencias/models.py`).
- Produces: cada objeto de `pendientes` en el contexto del template lleva dos atributos anotados: `cliente_detectado: Cliente | None` y `referencias_sugeridas: list[Referencia]` (máx. 15, más recientes primero).

- [ ] **Step 1: Escribir los tests que fallan — agregar al final de `finanzas/test_carga_cliente.py`**

```python
from datetime import date, datetime
from decimal import Decimal

from django.core.files.base import ContentFile

from clientes.models import Cliente
from referencias.models import Referencia


def _crear_pendiente(rfc_receptor, uuid='44444444-4444-4444-4444-444444444444'):
    obj = XMLProveedor(
        uuid_fiscal=uuid,
        fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
        rfc_emisor='FPA010101AA1',
        nombre_emisor='FLETES DEL PACIFICO',
        rfc_receptor=rfc_receptor,
        subtotal=Decimal('5000.00'),
        iva=Decimal('800.00'),
        total=Decimal('5800.00'),
        tipo_comprobante='I',
        estado_asignacion='PENDIENTE',
        motivo_pendiente='Proveedor no soportado',
    )
    obj.xml_file.save('cliente.xml', ContentFile(b'<x/>'), save=False)
    obj.save()
    return obj


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class XmlPendientesClienteTests(TestCase):
    def setUp(self):
        _login_finanzas(self, username='pendientes_user')
        self.cliente = Cliente.objects.create(
            nombre_cliente='CACIPA INTERNACIONAL',
            cve_cliente='CACIPA',
            rfc='CIN220216BS2',
        )
        self.ref_cliente = Referencia.objects.create(
            num_refe='LCRR0001/26', patente='1656', prefijo='LCRR',
            cve_cliente='CACIPA', fecha_pago=date(2026, 7, 1),
        )
        self.ref_ajena = Referencia.objects.create(
            num_refe='LCRR0002/26', patente='1656', prefijo='LCRR',
            cve_cliente='OTRO', fecha_pago=date(2026, 7, 2),
        )

    def test_muestra_rfc_receptor_y_cliente_detectado(self):
        _crear_pendiente('CIN220216BS2')
        resp = self.client.get(reverse('finanzas:xml_pendientes'))
        self.assertContains(resp, 'CIN220216BS2')
        self.assertContains(resp, 'CACIPA INTERNACIONAL')

    def test_sugiere_solo_referencias_del_cliente(self):
        _crear_pendiente('CIN220216BS2')
        resp = self.client.get(reverse('finanzas:xml_pendientes'))
        self.assertContains(resp, 'LCRR0001/26')
        self.assertNotContains(resp, 'LCRR0002/26')

    def test_rfc_sin_cliente_no_muestra_sugerencias(self):
        _crear_pendiente('ZZZ990101ZZ9')
        resp = self.client.get(reverse('finanzas:xml_pendientes'))
        self.assertContains(resp, 'ZZZ990101ZZ9')
        self.assertNotContains(resp, 'Sugerencias')
        self.assertNotContains(resp, 'CACIPA INTERNACIONAL')
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

```bash
python manage.py test finanzas.test_carga_cliente.XmlPendientesClienteTests --keepdb --verbosity=2
```

Esperado: FAIL en los tres tests (la vista aún no anota cliente ni sugerencias; el template no tiene las columnas).

- [ ] **Step 3: Anotar cliente y sugerencias en la vista `xml_pendientes`**

En `finanzas/views.py`, dentro de `xml_pendientes` (línea ~1107), reemplazar SOLO la parte GET final (las dos líneas del queryset `pendientes` y el `return render`) por:

```python
    from clientes.models import Cliente

    pendientes = list(
        XMLProveedor.objects
        .filter(estado_asignacion='PENDIENTE')
        .order_by('-cargado_en')
    )
    rfcs = {x.rfc_receptor for x in pendientes if x.rfc_receptor}
    clientes_por_rfc = {
        c.rfc: c for c in Cliente.objects.filter(rfc__in=rfcs)
    }
    referencias_por_cve = {}
    for xml_obj in pendientes:
        cliente = clientes_por_rfc.get(xml_obj.rfc_receptor)
        xml_obj.cliente_detectado = cliente
        if cliente and cliente.cve_cliente:
            cve = cliente.cve_cliente
            if cve not in referencias_por_cve:
                referencias_por_cve[cve] = list(
                    Referencia.objects
                    .filter(cve_cliente=cve)
                    .order_by('-fecha_pago')[:15]
                )
            xml_obj.referencias_sugeridas = referencias_por_cve[cve]
        else:
            xml_obj.referencias_sugeridas = []
    return render(request, 'finanzas/xml_pendientes.html', {'pendientes': pendientes})
```

El bloque POST de la vista (asignación por `xml_id` + `num_refe`) NO se toca.

- [ ] **Step 4: Agregar columnas y selector en `templates/finanzas/xml_pendientes.html`**

En el `<thead>` (línea ~19), después de `<th class="px-4 py-3">Emisor</th>` agregar:

```html
          <th class="px-4 py-3">RFC receptor</th>
          <th class="px-4 py-3">Cliente</th>
```

En el `<tbody>`, después de la celda del emisor (`{{ xml.nombre_emisor|truncatechars:30 }}`), agregar:

```html
          <td class="px-4 py-2 font-mono text-xs">
            {% if xml.rfc_receptor %}{{ xml.rfc_receptor }}{% else %}<span class="text-slate-300">—</span>{% endif %}
          </td>
          <td class="px-4 py-2 text-xs">
            {% if xml.cliente_detectado %}
              {{ xml.cliente_detectado.nombre_cliente|truncatechars:25 }}
            {% else %}
              <span class="text-slate-300">—</span>
            {% endif %}
          </td>
```

Y reemplazar el `<form>` de la celda "Asignar a referencia" completo por:

```html
            <form method="post" class="flex gap-2 items-center">
              {% csrf_token %}
              <input type="hidden" name="xml_id" value="{{ xml.pk }}">
              {% if xml.referencias_sugeridas %}
              <select onchange="document.getElementById('numrefe-{{ xml.pk }}').value = this.value"
                      class="border border-slate-300 rounded px-2 py-1 text-xs w-36">
                <option value="">Sugerencias…</option>
                {% for ref in xml.referencias_sugeridas %}
                <option value="{{ ref.num_refe }}">{{ ref.num_refe }}</option>
                {% endfor %}
              </select>
              {% endif %}
              <input type="text" id="numrefe-{{ xml.pk }}" name="num_refe" placeholder="LCRR0000/26" required
                     class="border border-slate-300 rounded px-2 py-1 text-xs w-32">
              <button type="submit"
                      class="bg-slate-600 hover:bg-slate-700 text-white px-3 py-1 rounded text-xs">
                Asignar
              </button>
            </form>
```

- [ ] **Step 5: Correr los tests nuevos y verificar que pasan**

```bash
python manage.py test finanzas.test_carga_cliente --keepdb --verbosity=2
```

Esperado: `OK` — 8 tests pasando (5 de Task 2 + 3 de Task 3).

- [ ] **Step 6: Verificar que los tests existentes de pendientes no se rompieron**

Las clases `XmlPendientesViewTests` y `ReferenciaEstadoPdfLinkTests` de
`finanzas/test_carga_masiva.py` ejercitan la misma vista y template:

```bash
python manage.py test finanzas.test_carga_masiva --keepdb --verbosity=1
```

Esperado: `OK`.

- [ ] **Step 7: Commit**

```bash
git add finanzas/test_carga_cliente.py finanzas/views.py templates/finanzas/xml_pendientes.html
git commit -m "feat(finanzas): RFC receptor, cliente detectado y sugerencias de referencia en XML pendientes"
```

---

## Verificación End-to-End

Después de completar todos los tasks:

- [ ] **Suite completa de finanzas**: `python manage.py test finanzas --keepdb` → `OK` (los 24 errores previos de storage quedaron resueltos en Task 1).

- [ ] **Flujo manual completo** con el servidor en `http://127.0.0.1:8001`:
  1. Verificar en `/admin/clientes/cliente/` que un cliente de prueba tenga su `rfc` capturado.
  2. Entrar a Finanzas → "Facturas de cliente", subir un XML real de una factura dirigida a ese cliente con su PDF.
  3. Confirmar el resumen: 1 pendiente.
  4. Abrir "XMLs pendientes": el renglón muestra RFC receptor y el nombre del cliente, y el selector lista sus referencias.
  5. Elegir una referencia del selector (llena el campo) y presionar Asignar.
  6. Confirmar mensaje de éxito, que el XML desapareció de pendientes, y que en el estado financiero de la referencia aparece el gasto generado con enlace al PDF.
