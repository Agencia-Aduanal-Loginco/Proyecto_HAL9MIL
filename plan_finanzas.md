# plan_finanzas.md — Módulo Financiero HAL9MIL

> Plan de implementación faseado para el módulo de contabilidad electrónica
> y facturación electrónica CFDI 4.0 integrado al sistema HAL9MIL.
>
> **Proyecto:** Proyecto_HAL9MIL · Django 5.2 · PostgreSQL (prod) / SQLite (dev)
> **Ruta base:** `/home/tony/Developer/Proyecto_HAL9MIL/`
> **Fecha de plan:** 2026-06-24
> **Última actualización:** 2026-06-28 — Fases 0-10 completadas

---

## Contexto del sistema

HAL9MIL es una plataforma aduanal Django con las siguientes apps:
- `referencias/` — núcleo de pedimentos y referencias (6 modelos)
- `reportes/` — reportes semanales/mensuales con análisis Claude AI
- `whatsapp/` — bot Twilio WhatsApp
- `clientes/` — estadísticas por cliente

**Fuente de datos:** Los objetos `Referencia` ya existen en Django (sincronizados desde
Firebird por la app `referencias/`). El módulo `finanzas` **no se conecta a Firebird**;
toma las `Referencia` de Django como ancla y construye sobre ellas el control financiero.

**Modelo mental por referencia:**

```
Referencia (ya existe en Django)
  ├── Anticipos recibidos del importador/exportador  → ingresos de la agencia
  ├── Gastos cargados a la referencia                → egresos / costos
  ├── XMLs de proveedores adjuntos                   → documentos de soporte
  ├── Pólizas contables generadas                    → asientos contables
  └── Factura(s) CFDI emitida(s)                     → cobro de honorarios
```

La referencia funciona como **centro de costo**: los anticipos abonan al saldo,
los gastos lo cargan. El saldo resultante indica si hay remanente o déficit antes
de emitir la factura final.

**Patrón de app existente (copiar de `referencias/`):**
```
finanzas/
├── models.py
├── views.py
├── urls.py
├── admin.py
├── apps.py
├── management/commands/
└── migrations/
```

---

## Alcance del módulo

### A. Contabilidad electrónica
1. Control de anticipos y gastos por referencia
2. Pólizas de egresos e ingresos (generadas desde anticipos/gastos)
3. Balanza de comprobación
4. Consolidación bancaria
5. Cierre mensual
6. Reporte de comisiones
7. Lectura de XMLs de proveedores e integración a la referencia

### B. Facturación electrónica
1. Cobranza (orden de cobro derivada del saldo de la referencia)
2. Timbrado de facturas CFDI 4.0
3. Complemento de pago (recepción de pagos PPD)

---

## Decisiones de arquitectura

| Decisión | Elección | Razón |
|----------|----------|-------|
| Nueva app Django | `finanzas/` | Aislamiento, misma convención del proyecto |
| Fuente de referencias | `referencias.Referencia` (Django ORM) | Sin Firebird en este módulo |
| Primitivas financieras | `Anticipo` + `GastoReferencia` | Claridad operativa: dinero entrante vs saliente |
| Origen pólizas | Generadas desde anticipos/gastos (no importadas) | Trazabilidad completa |
| PAC de timbrado | SW Sapien o Finkok | APIs REST, sandbox gratuito, comunes en México |
| Catálogos SAT | Modelos Django + fixtures JSON | Actualizables sin código |
| XML proveedores | `xml.etree.ElementTree` (stdlib) | Sin dependencia extra |
| XSD contabilidad electrónica | SAT Contabilidad v1.3 | Versión vigente 2024 |

---

## Fases de implementación

### Estado de avance (2026-06-28)

| Fase | Descripción | Estado |
|------|-------------|--------|
| 0 | Pre-requisitos (deps, .env, certs/) | ✅ Completada |
| 1 | Estructura base app `finanzas` | ✅ Completada |
| 2 | Anticipos y gastos por referencia | ✅ Completada |
| 3 | Lectura XMLs de proveedores | ✅ Completada |
| 4 | Facturación electrónica: cobranza y modelos base | ✅ Completada |
| 5 | Timbrado CFDI 4.0 via PAC | ✅ Completada |
| 6 | Complemento de pago | ✅ Completada |
| 7 | Balanza de comprobación / contabilidad electrónica SAT | ✅ Completada |
| 8 | Consolidación bancaria | ✅ Completada |
| 9 | Cierre mensual | ✅ Completada |
| 10 | Reporte de comisiones | ✅ Completada |
| 11 | Integración y verificación final | ⏳ Siguiente |

> **Pendiente usuario (Fase 0):** obtener CSD reales, llenar `.env` con RFC/CP/contraseñas,
> registrar cuenta sandbox en SW Sapien o Finkok.

> **Pendiente para producción (Fases 4-6):** configurar CSD reales en `ConfiguracionFiscal`,
> validar timbrado en sandbox de SW Sapien antes de producción, verificar que los RFC
> receptores de las facturas coincidan con los del SAT.

---

### ✅ Fase 0 — Pre-requisitos (COMPLETADA 2026-06-27)

> Esta fase no produce código. Recopila certificados y acceso a PAC.

#### 0.1 Certificados digitales del emisor

> **Multi-emisor:** Hay **3 patentes** activas (1627, 1656, 1927). Cada una es un
> RFC fiscal independiente con su propio CSD. El sistema soporta los 3 simultáneamente;
> las rutas a certificados se almacenan en `ConfiguracionFiscal` (BD) y las contraseñas
> del `.key` únicamente en `.env` — nunca en base de datos.

Obtener por **cada patente**:
- `<patente>.cer` — Certificado de Sello Digital (CSD)
- `<patente>.key` — Llave privada del CSD
- Contraseña de la llave privada (sensible — solo en `.env`)
- **RFC**, **Régimen fiscal** (c_RegimenFiscal, ej. `612`)
- **Código postal** del domicilio fiscal (requerido en CFDI 4.0)

Guardar en `.env` (un bloque por patente):
```
# Patente 1627
CFDI_1627_RFC=
CFDI_1627_NOMBRE=
CFDI_1627_REGIMEN=612
CFDI_1627_CP=
CFDI_1627_CERT_PATH=/ruta/certs/1627.cer
CFDI_1627_KEY_PATH=/ruta/certs/1627.key
CFDI_1627_KEY_PASSWORD=

# Patente 1656
CFDI_1656_RFC=
CFDI_1656_NOMBRE=
CFDI_1656_REGIMEN=612
CFDI_1656_CP=
CFDI_1656_CERT_PATH=/ruta/certs/1656.cer
CFDI_1656_KEY_PATH=/ruta/certs/1656.key
CFDI_1656_KEY_PASSWORD=

# Patente 1927
CFDI_1927_RFC=
CFDI_1927_NOMBRE=
CFDI_1927_REGIMEN=612
CFDI_1927_CP=
CFDI_1927_CERT_PATH=/ruta/certs/1927.cer
CFDI_1927_KEY_PATH=/ruta/certs/1927.key
CFDI_1927_KEY_PASSWORD=
```

Las rutas y datos fiscales se cargan a `ConfiguracionFiscal` con el command
`cargar_configs_fiscales` (ver §1.6). Las contraseñas **permanecen solo en `.env`**.

#### 0.2 Cuenta PAC de timbrado

Registrar en **SW Sapien** (solucionFacturacion.com.mx) o **Finkok**:
- Crear cuenta sandbox
- Obtener token de API
- Verificar endpoint POST de timbrado

> **Nota multi-emisor:** Un solo contrato PAC puede timbrar en nombre de los 3 RFC,
> siempre que estén dados de alta. Verificar con el PAC antes de producción.

Guardar en `.env`:
```
PAC_PROVIDER=sw_sapien
PAC_URL=https://api.sw.com.mx
PAC_TOKEN=
PAC_USER=
PAC_PASSWORD=
```

#### 0.3 Agregar dependencias

Agregar a `requirements.txt`:
```
cryptography>=42.0    # firma CSD / sello digital
lxml>=5.0             # parsing y generación XML CFDI (opcional pero recomendado)
```

#### Verificación de Fase 0
- [ ] `.env` contiene los 3 bloques `CFDI_<patente>_*` completos (21 variables)
- [ ] Los 6 archivos `.cer` y `.key` (2 por patente) son accesibles desde el servidor
- [ ] Cuenta PAC sandbox activa, token válido con respuesta 200
- [ ] Los 3 RFC de patentes están autorizados en la cuenta PAC

---

### ✅ Fase 1 — Estructura base de la app `finanzas` (COMPLETADA 2026-06-27)

**Objetivo:** Crear la app con modelos de catálogos SAT, configuración fiscal
del emisor y plan de cuentas contable.

#### 1.1 Crear la app

```bash
cd /home/tony/Developer/Proyecto_HAL9MIL
python manage.py startapp finanzas
```

Registrar en `hal9mil/settings.py` (línea 27, después de `'clientes'`):
```python
'finanzas',
```

Agregar en `hal9mil/urls.py`:
```python
path('finanzas/', include('finanzas.urls')),
```

#### 1.2 Modelo `ConfiguracionFiscal`

**Archivo:** `finanzas/models.py`

Un registro por patente. La selección del emisor al facturar es automática
vía `referencia.patente` — el operador no elige manualmente.

```python
class ConfiguracionFiscal(models.Model):
    patente = models.CharField(max_length=4, unique=True)   # 1627, 1656, 1927
    rfc = models.CharField(max_length=13)
    razon_social = models.CharField(max_length=200)
    regimen_fiscal = models.CharField(max_length=3)         # c_RegimenFiscal
    codigo_postal = models.CharField(max_length=5)          # domicilio fiscal CFDI 4.0
    cert_path = models.CharField(max_length=500)            # ruta al .cer
    key_path = models.CharField(max_length=500)             # ruta al .key
    # La contraseña del .key NO se guarda en BD; se lee desde env en runtime.
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Configuración Fiscal'

    def get_key_password(self) -> str:
        """Lee la contraseña CSD desde env — nunca desde BD."""
        import os
        var = f'CFDI_{self.patente}_KEY_PASSWORD'
        pwd = os.environ.get(var, '')
        if not pwd:
            raise ValueError(f'Variable de entorno {var} no definida')
        return pwd
```

#### 1.3 Catálogos SAT

```python
class CatalogoSAT(models.Model):
    """Catálogo SAT genérico (c_FormaPago, c_UsoCFDI, c_RegimenFiscal, etc.)"""
    catalogo = models.CharField(max_length=50)
    clave = models.CharField(max_length=20)
    descripcion = models.CharField(max_length=200)
    vigente = models.BooleanField(default=True)

    class Meta:
        unique_together = ('catalogo', 'clave')
        ordering = ['catalogo', 'clave']
```

Catálogos a cargar como fixture inicial:
- `c_FormaPago`: 01=Efectivo, 03=Transferencia, 04=Tarjeta crédito, 99=Por definir
- `c_MetodoPago`: PUE=Pago en una exhibición, PPD=Pago en parcialidades
- `c_UsoCFDI`: G01=Adquisición mercancías, G03=Gastos en general, P01=Por definir
- `c_RegimenFiscal`: 612=Personas Morales régimen general, 616=Sin obligaciones
- `c_TipoDeComprobante`: I=Ingreso, E=Egreso, P=Pago
- `c_TipoPoliza`: 1=Diario, 2=Haber, 3=Egreso

#### 1.4 Plan de cuentas contable

```python
class CuentaContable(models.Model):
    numero = models.CharField(max_length=20, unique=True)   # ej. "1-100-001"
    nombre = models.CharField(max_length=200)
    nivel = models.PositiveSmallIntegerField()               # 1=mayor, 2=sub, 3=detalle
    tipo = models.CharField(max_length=1, choices=[
        ('A', 'Activo'), ('P', 'Pasivo'), ('C', 'Capital'),
        ('I', 'Ingreso'), ('G', 'Gasto'),
    ])
    padre = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.PROTECT, related_name='hijos'
    )
    es_hoja = models.BooleanField(default=True)
    naturaleza = models.CharField(max_length=1, choices=[
        ('D', 'Deudora'), ('A', 'Acreedora')
    ])
    codigo_agrupador_sat = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ['numero']
```

#### 1.5 Migración y fixtures iniciales

```bash
python manage.py makemigrations finanzas
python manage.py migrate
python manage.py loaddata finanzas/fixtures/catalogos_sat.json
python manage.py loaddata finanzas/fixtures/plan_cuentas_inicial.json
```

**Archivos a crear:**
- `finanzas/fixtures/catalogos_sat.json` — claves de los catálogos SAT listados en 1.3
- `finanzas/fixtures/plan_cuentas_inicial.json` — plan de cuentas básico agencia aduanal
  (cuentas mínimas: bancos, clientes, anticipos de clientes, ingresos por honorarios,
  gastos de operación, IVA trasladado, IVA acreditable)

#### 1.6 Management command `cargar_configs_fiscales`

**Archivo:** `finanzas/management/commands/cargar_configs_fiscales.py`

Crea o actualiza las 3 `ConfiguracionFiscal` leyendo las variables de entorno.
Correr una vez al desplegar, o cada vez que se renueven los CSDs.

```python
# python manage.py cargar_configs_fiscales
PATENTES = ['1627', '1656', '1927']

class Command(BaseCommand):
    help = 'Carga ConfiguracionFiscal para las 3 patentes desde variables de entorno'

    def handle(self, *args, **options):
        for p in PATENTES:
            obj, created = ConfiguracionFiscal.objects.update_or_create(
                patente=p,
                defaults={
                    'rfc':            os.environ[f'CFDI_{p}_RFC'],
                    'razon_social':   os.environ[f'CFDI_{p}_NOMBRE'],
                    'regimen_fiscal': os.environ[f'CFDI_{p}_REGIMEN'],
                    'codigo_postal':  os.environ[f'CFDI_{p}_CP'],
                    'cert_path':      os.environ[f'CFDI_{p}_CERT_PATH'],
                    'key_path':       os.environ[f'CFDI_{p}_KEY_PATH'],
                    'activa':         True,
                }
            )
            accion = 'Creada' if created else 'Actualizada'
            self.stdout.write(f'{accion}: patente {p} — {obj.rfc}')
```

#### 1.7 Utilidad `get_configuracion_fiscal`

**Archivo:** `finanzas/utils.py`

Punto único de resolución del emisor. Todas las vistas y comandos que generan
CFDI deben usar esta función — nunca consultar `ConfiguracionFiscal` directamente.

```python
from .models import ConfiguracionFiscal

def get_configuracion_fiscal(patente: str) -> ConfiguracionFiscal:
    """
    Retorna la ConfiguracionFiscal activa para la patente dada.
    Lanza ValueError si no existe o está inactiva — nunca retorna None.
    """
    try:
        return ConfiguracionFiscal.objects.get(patente=patente, activa=True)
    except ConfiguracionFiscal.DoesNotExist:
        raise ValueError(
            f'Sin ConfiguracionFiscal activa para patente {patente}. '
            f'Ejecutar: python manage.py cargar_configs_fiscales'
        )
```

Uso típico al crear una factura desde una referencia:
```python
from finanzas.utils import get_configuracion_fiscal

config = get_configuracion_fiscal(referencia.patente)
factura = Factura(configuracion_fiscal=config, ...)
```

#### Verificación de Fase 1
- [ ] `python manage.py check` sin errores
- [ ] `python manage.py cargar_configs_fiscales` crea 3 registros `ConfiguracionFiscal`
- [ ] `get_configuracion_fiscal('9999')` lanza `ValueError` (patente inválida)
- [ ] `CuentaContable.objects.count() > 0` tras loaddata
- [ ] `CatalogoSAT.objects.filter(catalogo='c_FormaPago').count() >= 4`
- [ ] Admin muestra las 3 `ConfiguracionFiscal` con su patente y RFC correctos
- [ ] Admin muestra `CuentaContable` con árbol padre/hijo navegable

**Anti-patrones:**
- No hardcodear claves SAT en `choices` de modelos (usar `CatalogoSAT`)
- No crear cuentas sin `codigo_agrupador_sat` (requerido para contabilidad electrónica SAT)
- No guardar `key_password` en BD ni en fixture — solo en `.env`
- No consultar `ConfiguracionFiscal` directamente en vistas; usar `get_configuracion_fiscal()`

---

### ✅ Fase 2 — Anticipos y gastos por referencia (COMPLETADA 2026-06-27)

**Objetivo:** Núcleo financiero del módulo. Registrar los anticipos que hacen
los importadores/exportadores y los gastos cargados a cada referencia.
Esto reemplaza y extiende el concepto de `CuentaGastos` con granularidad por movimiento.

#### 2.1 Modelo `Anticipo`

Un anticipo es dinero que el importador/exportador entrega a la agencia
**por adelantado** antes de que se liquiden todos los gastos de la referencia.
Representa un **ingreso para la agencia**.

```python
class Anticipo(models.Model):
    referencia = models.ForeignKey(
        'referencias.Referencia',
        on_delete=models.PROTECT,
        related_name='anticipos'
    )
    fecha = models.DateField()
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    moneda = models.CharField(max_length=3, default='MXN')
    forma_pago = models.CharField(max_length=2)             # c_FormaPago
    num_operacion = models.CharField(max_length=100, blank=True)  # referencia bancaria
    observaciones = models.CharField(max_length=300, blank=True)
    cuenta_destino = models.ForeignKey(
        CuentaContable, null=True, blank=True, on_delete=models.SET_NULL
    )
    poliza = models.ForeignKey(
        'PolizaContable', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='anticipos'
    )
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-created_at']
        verbose_name = 'Anticipo'
```

#### 2.2 Modelo `GastoReferencia`

Un gasto es un costo incurrido durante el proceso de la referencia:
flete, almacenaje, derechos aduanales, honorarios de terceros, etc.
Representa un **egreso de la agencia** o un **cargo al importador**.

```python
class GastoReferencia(models.Model):
    TIPO_GASTO = [
        ('FLETE', 'Flete'),
        ('ALMACENAJE', 'Almacenaje'),
        ('DERECHOS', 'Derechos aduanales'),
        ('HONORARIOS', 'Honorarios agencia'),
        ('MANIOBRAS', 'Maniobras'),
        ('OTROS', 'Otros'),
    ]
    referencia = models.ForeignKey(
        'referencias.Referencia',
        on_delete=models.PROTECT,
        related_name='gastos_finanzas'
    )
    tipo = models.CharField(max_length=20, choices=TIPO_GASTO)
    concepto = models.CharField(max_length=300)
    fecha = models.DateField()
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    moneda = models.CharField(max_length=3, default='MXN')
    proveedor = models.CharField(max_length=200, blank=True)    # nombre del proveedor
    num_factura_proveedor = models.CharField(max_length=50, blank=True)
    xml_proveedor = models.ForeignKey(
        'XMLProveedor', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='gastos'
    )
    cuenta_gasto = models.ForeignKey(
        CuentaContable, null=True, blank=True, on_delete=models.SET_NULL
    )
    poliza = models.ForeignKey(
        'PolizaContable', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='gastos'
    )
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-created_at']
        verbose_name = 'Gasto de Referencia'
        verbose_name_plural = 'Gastos de Referencia'
```

#### 2.3 Propiedad `saldo` por referencia

Agregar método en `GastoReferencia`/`Anticipo` o como función de utilidad:

**Archivo:** `finanzas/saldo.py`

```python
from decimal import Decimal
from django.db.models import Sum

def saldo_referencia(referencia) -> dict:
    """
    Retorna el estado financiero de la referencia:
    - total_anticipos: suma de anticipos recibidos
    - total_gastos: suma de gastos cargados
    - saldo: total_anticipos - total_gastos
      positivo = remanente a favor del importador
      negativo = saldo pendiente que debe pagar el importador
    """
    total_anticipos = referencia.anticipos.aggregate(
        total=Sum('monto'))['total'] or Decimal('0')
    total_gastos = referencia.gastos_finanzas.aggregate(
        total=Sum('monto'))['total'] or Decimal('0')
    return {
        'total_anticipos': total_anticipos,
        'total_gastos': total_gastos,
        'saldo': total_anticipos - total_gastos,
    }
```

#### 2.4 Pólizas contables

Las pólizas se **generan automáticamente** al registrar un anticipo o gasto,
no se importan de ningún sistema externo.

```python
class PolizaContable(models.Model):
    TIPO_POLIZA = [
        ('D', 'Diario'),
        ('H', 'Haber / Ingreso'),
        ('E', 'Egreso'),
    ]
    numero = models.CharField(max_length=20)
    tipo = models.CharField(max_length=1, choices=TIPO_POLIZA)
    fecha = models.DateField()
    mes = models.PositiveSmallIntegerField()
    anio = models.PositiveIntegerField()
    concepto = models.CharField(max_length=300)
    referencia = models.ForeignKey(
        'referencias.Referencia',
        null=True, blank=True,
        on_delete=models.SET_NULL, related_name='polizas'
    )
    cerrado = models.BooleanField(default=False)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True,
        on_delete=models.SET_NULL, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('numero', 'tipo', 'anio', 'mes')
        ordering = ['-fecha', '-numero']


class PartidaPoliza(models.Model):
    poliza = models.ForeignKey(
        PolizaContable, on_delete=models.CASCADE, related_name='partidas'
    )
    linea = models.PositiveSmallIntegerField()
    cuenta = models.ForeignKey(CuentaContable, on_delete=models.PROTECT)
    concepto = models.CharField(max_length=300)
    debe = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    haber = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        unique_together = ('poliza', 'linea')
        ordering = ['linea']
```

**Generación automática de póliza al guardar un `Anticipo`:**

```python
# En Anticipo.save() o via signal post_save
def generar_poliza_anticipo(anticipo):
    """
    Anticipo recibido → póliza de ingreso:
      DEBE:  Bancos (cuenta_destino)
      HABER: Anticipos de clientes
    """
    ...
```

**Generación automática de póliza al guardar un `GastoReferencia`:**

```python
def generar_poliza_gasto(gasto):
    """
    Gasto cargado → póliza de egreso:
      DEBE:  Cuenta de gasto (tipo_gasto → cuenta_gasto)
      HABER: Bancos o Proveedores por pagar
    """
    ...
```

#### 2.5 Vistas de anticipos y gastos

**Archivo:** `finanzas/views.py`

- `anticipos_list(request)` — lista general paginada con filtro por cliente/mes
- `anticipo_crear(request, num_refe)` — registrar anticipo desde el contexto de una referencia
- `gastos_list(request)` — lista general de gastos
- `gasto_crear(request, num_refe)` — registrar gasto en la referencia
- `referencia_estado_financiero(request, num_refe)` — resumen financiero de la referencia:
  tabla de anticipos + tabla de gastos + saldo actual + botón "Emitir factura"

**Archivo:** `finanzas/urls.py`
```python
urlpatterns = [
    path('', views.dashboard_financiero, name='finanzas_dashboard'),
    path('anticipos/', views.anticipos_list, name='anticipos_list'),
    path('referencias/<path:num_refe>/anticipo/', views.anticipo_crear, name='anticipo_crear'),
    path('referencias/<path:num_refe>/gasto/', views.gasto_crear, name='gasto_crear'),
    path('referencias/<path:num_refe>/estado/', views.referencia_estado_financiero, name='referencia_estado'),
    path('polizas/', views.polizas_list, name='polizas_list'),
    path('polizas/<int:pk>/', views.poliza_detalle, name='poliza_detalle'),
]
```

> **Nota:** Se usa `<path:num_refe>` porque los números de referencia contienen `/`
> (ej. `LCLF0331/26`), igual que en `referencias/urls.py`.

#### Verificación de Fase 2
- [ ] Registrar anticipo → se crea `PolizaContable` automáticamente con debe == haber
- [ ] Registrar gasto → se crea `PolizaContable` de egreso automáticamente
- [ ] `saldo_referencia(ref)` retorna dict con los tres valores correctos
- [ ] Vista `referencia_estado_financiero` muestra saldo actualizado en tiempo real
- [ ] `PolizaContable.clean()` lanza `ValidationError` si suma(debe) != suma(haber)
- [ ] Póliza con `cerrado=True` no permite edición

**Anti-patrones:**
- No usar `FloatField` para montos (siempre `DecimalField`)
- No permitir pólizas descuadradas (debe != haber)
- No modificar pólizas generadas automáticamente directamente; modificar el anticipo/gasto origen

---

### ✅ Fase 3 — Lectura de XMLs de proveedores e integración a la referencia (COMPLETADA 2026-06-27)

**Objetivo:** Subir XMLs CFDI de facturas de proveedores (flete, almacenaje, etc.),
parsearlos y vincularlos a la referencia y al `GastoReferencia` correspondiente.

#### 3.1 Modelo `XMLProveedor`

```python
class XMLProveedor(models.Model):
    referencia = models.ForeignKey(
        'referencias.Referencia',
        null=True, blank=True,
        on_delete=models.SET_NULL, related_name='xmls_proveedor'
    )
    uuid_fiscal = models.UUIDField(unique=True)
    fecha_emision = models.DateTimeField()
    rfc_emisor = models.CharField(max_length=13)
    nombre_emisor = models.CharField(max_length=200)
    rfc_receptor = models.CharField(max_length=13)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    iva = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2)
    moneda = models.CharField(max_length=3, default='MXN')
    tipo_comprobante = models.CharField(max_length=1)       # I=Ingreso, E=Egreso
    concepto_principal = models.CharField(max_length=300, blank=True)
    xml_file = models.FileField(upload_to='xmls_proveedores/%Y/%m/')
    cargado_en = models.DateTimeField(auto_now_add=True)
    procesado = models.BooleanField(default=False)          # True si ya se creó GastoReferencia

    class Meta:
        ordering = ['-fecha_emision']
        verbose_name = 'XML de Proveedor'
```

#### 3.2 Parser CFDI

**Archivo:** `finanzas/cfdi_parser.py`

```python
import xml.etree.ElementTree as ET
from decimal import Decimal

NS_CFDI4 = 'http://www.sat.gob.mx/cfd/4'
NS_CFDI3 = 'http://www.sat.gob.mx/cfd/3'
NS_TFD   = 'http://www.sat.gob.mx/TimbreFiscalDigital'

def parsear_cfdi(xml_path: str) -> dict:
    """
    Parsea CFDI 3.3 o 4.0. Retorna:
    {uuid, fecha, rfc_emisor, nombre_emisor, rfc_receptor,
     subtotal, iva, total, moneda, tipo, concepto_principal}
    Lanza ValueError si el XML no es CFDI válido.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    # detectar versión por namespace
    ns = NS_CFDI4 if NS_CFDI4 in root.tag else NS_CFDI3
    ...
```

Campos a extraer:
- UUID: `cfdi:Complemento/tfd:TimbreFiscalDigital/@UUID`
- Fecha: `cfdi:Comprobante/@Fecha`
- Emisor: `cfdi:Emisor/@Rfc`, `@Nombre`
- Receptor: `cfdi:Receptor/@Rfc`
- Importes: `@SubTotal`, `@Total`, `@Moneda`, `@TipoDeComprobante`
- IVA: `cfdi:Impuestos/cfdi:Traslados/cfdi:Traslado[@Impuesto='002']/@Importe`
- Concepto: `cfdi:Conceptos/cfdi:Concepto[1]/@Descripcion`

#### 3.3 Vista de upload

```python
path('referencias/<path:num_refe>/xml-proveedor/', views.subir_xml_proveedor, name='subir_xml'),
```

Flujo:
1. Usuario sube XML desde el estado financiero de la referencia
2. `parsear_cfdi()` extrae campos
3. Verificar unicidad por `uuid_fiscal` (no duplicar)
4. Crear `XMLProveedor` vinculado a la referencia
5. Opcionalmente crear `GastoReferencia` automático con el total del XML
6. Marcar `procesado=True` si se creó el gasto
7. Redirigir a `referencia_estado_financiero` con mensaje de éxito

#### Verificación de Fase 3
- [ ] Subir XML CFDI 4.0 real → sin error, UUID almacenado
- [ ] Subir mismo XML dos veces → error "UUID ya registrado"
- [ ] Parser no falla con CFDI 3.3 (namespace diferente)
- [ ] `XMLProveedor` aparece en estado financiero de la referencia

**Anti-patrones:**
- No ignorar el namespace `cfdi3:` para facturas históricas 3.3
- No crear `GastoReferencia` sin confirmar que el XML es de tipo `I` (Ingreso del proveedor)

---

### ✅ Fase 4 — Facturación electrónica: Cobranza y modelos base (COMPLETADA 2026-06-28)

**Objetivo:** Generar prefacturas (borradores CFDI) a partir del estado financiero
de la referencia. El flujo parte del saldo: gastos - anticipos = monto a facturar.

#### 4.1 Modelos de factura

```python
class Factura(models.Model):
    ESTADO = [
        ('BORRADOR', 'Borrador'),
        ('TIMBRADA', 'Timbrada'),
        ('CANCELADA', 'Cancelada'),
    ]
    serie = models.CharField(max_length=10, default='A')
    folio = models.PositiveIntegerField()
    fecha_emision = models.DateTimeField(null=True, blank=True)
    # Receptor
    rfc_receptor = models.CharField(max_length=13)
    nombre_receptor = models.CharField(max_length=200)
    domicilio_fiscal_receptor = models.CharField(max_length=5)  # CP fiscal CFDI 4.0
    regimen_fiscal_receptor = models.CharField(max_length=3)
    uso_cfdi = models.CharField(max_length=3, default='G03')
    forma_pago = models.CharField(max_length=2, default='99')
    metodo_pago = models.CharField(max_length=3, default='PPD')
    moneda = models.CharField(max_length=3, default='MXN')
    # Importes
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    iva = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # Timbrado
    uuid_fiscal = models.UUIDField(null=True, blank=True, unique=True)
    xml_timbrado = models.TextField(blank=True)
    estado = models.CharField(max_length=10, choices=ESTADO, default='BORRADOR')
    # Relaciones
    referencias = models.ManyToManyField(
        'referencias.Referencia', blank=True, related_name='facturas'
    )
    configuracion_fiscal = models.ForeignKey(ConfiguracionFiscal, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('serie', 'folio')
        ordering = ['-created_at']


class ConceptoFactura(models.Model):
    factura = models.ForeignKey(Factura, on_delete=models.CASCADE, related_name='conceptos')
    clave_prod_serv = models.CharField(max_length=8, default='84111506')  # servicios aduanales SAT
    clave_unidad = models.CharField(max_length=3, default='ACT')
    descripcion = models.CharField(max_length=1000)
    cantidad = models.DecimalField(max_digits=14, decimal_places=6, default=1)
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=2)
    importe = models.DecimalField(max_digits=14, decimal_places=2)
    objeto_imp = models.CharField(max_length=2, default='02')             # 02=sí objeto de impuesto
    tasa_iva = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.16'))
```

#### 4.2 Flujo de cobranza y selección automática de emisor

**Vista:** `finanzas/views.py → cobranza_list(request)`

- Lista referencias con saldo negativo (gastos > anticipos) o `CuentaGastos` finalizada sin factura
- Botón "Generar factura" por referencia
- El emisor se **auto-selecciona** a partir de `referencia.patente`; el operador no lo elige:

```python
# En factura_crear(request, num_refe):
referencia = get_object_or_404(Referencia, num_refe=num_refe)
config = get_configuracion_fiscal(referencia.patente)   # ← auto por patente
# Pre-rellena:
#   Emisor (read-only en el form): config.rfc / config.razon_social
#   Receptor: referencia.nombre_cliente + RFC del Cliente
#   Concepto: f"Honorarios por despacho aduanal — Ref. {num_refe}"
#   Importe: saldo_referencia(referencia)['saldo']
```

Si la patente de la referencia no tiene `ConfiguracionFiscal` activa, la vista
muestra un error descriptivo (no un 500) indicando ejecutar `cargar_configs_fiscales`.

> **Nota sobre RFC del cliente:** El campo `rfc` ya existe en el modelo `Cliente`
> de la app `clientes/`. Si no está capturado, el form lo solicita al operador.

```python
path('cobranza/', views.cobranza_list, name='cobranza_list'),
path('facturas/', views.facturas_list, name='facturas_list'),
path('facturas/nueva/', views.factura_crear, name='factura_crear'),
path('facturas/<int:pk>/', views.factura_detalle, name='factura_detalle'),
```

#### Verificación de Fase 4
- [ ] `Factura` en estado `BORRADOR` se crea desde admin
- [ ] `ConceptoFactura.importe == cantidad * valor_unitario` (validar en `save()`)
- [ ] Vista cobranza muestra solo referencias con saldo pendiente
- [ ] `Factura.total == subtotal + iva` (validar en `save()`)
- [ ] Crear factura desde referencia patente 1627 → `configuracion_fiscal.rfc` es el RFC de esa patente
- [ ] Crear factura desde referencia patente 1656 → `configuracion_fiscal.rfc` es distinto al anterior
- [ ] Referencia con patente sin config activa → error controlado, no 500

**Anti-patrones:**
- No usar `FloatField` para importes (siempre `DecimalField`)
- No omitir `domicilio_fiscal_receptor` (obligatorio CFDI 4.0 desde enero 2022)

---

### ✅ Fase 5 — Timbrado CFDI 4.0 via PAC (COMPLETADA 2026-06-28)

**Objetivo:** Firmar y timbrar facturas en estado `BORRADOR`.
Una factura timbrada tiene UUID SAT y XML oficial.

#### 5.1 Generador XML CFDI 4.0

**Archivo:** `finanzas/cfdi_generator.py`

```python
from lxml import etree
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.backends import default_backend
import base64

def generar_xml_cfdi40(factura: 'Factura') -> str:
    """
    Genera XML CFDI 4.0 sin timbrar (sin TimbreFiscalDigital).
    El emisor se lee de factura.configuracion_fiscal (resuelto por patente).
    Calcula la cadena original y aplica el sello del CSD del emisor.
    Retorna XML como string UTF-8.
    """
    config = factura.configuracion_fiscal        # apunta a la patente correcta
    key_password = config.get_key_password()     # lee desde env, nunca desde BD
    ...
```

Namespace CFDI 4.0: `http://www.sat.gob.mx/cfd/4`
XSD: `http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd`

Cadena original (Anexo 20 SAT):
```
||Version|Serie|Folio|Fecha|FormaPago|NoCertificado|...|
```

#### 5.2 Cliente PAC

**Archivo:** `finanzas/pac_client.py`

```python
import requests, base64
from django.conf import settings

def timbrar_cfdi(xml_sin_timbrar: str) -> dict:
    """
    Envía XML a SW Sapien/Finkok.
    Retorna: {'uuid': str, 'xml_timbrado': str, 'status': 'success'|'error', 'message': str}
    """
    headers = {'Authorization': f'bearer {settings.PAC_TOKEN}'}
    payload = {'xml': base64.b64encode(xml_sin_timbrar.encode()).decode()}
    resp = requests.post(f'{settings.PAC_URL}/v3/cfdi33/stamp/v4/b64',
                         headers=headers, json=payload, timeout=30)
    ...
```

Errores a manejar:
- `CFDI33126` — UUID duplicado (ya timbrado)
- `CFDI33106` — Error estructura XML
- `401` — Token expirado → refrescar con `PAC_USER`/`PAC_PASSWORD`

#### 5.3 Vista y management command de timbrado

**Vista:** `POST /finanzas/facturas/<pk>/timbrar/`
- Solo facturas en estado `BORRADOR`
- Genera XML → envía PAC → almacena `uuid_fiscal`, `xml_timbrado`
- Cambia estado a `TIMBRADA`

**Management command:**
```bash
python manage.py timbrar_factura --factura-id 42
```

**Archivo:** `finanzas/management/commands/timbrar_factura.py`

URL adicional:
```python
path('facturas/<int:pk>/timbrar/', views.factura_timbrar, name='factura_timbrar'),
path('facturas/<int:pk>/xml/', views.factura_descargar_xml, name='factura_xml'),
```

#### Verificación de Fase 5
- [ ] Timbrado en sandbox retorna UUID válido sin error
- [ ] `Factura.uuid_fiscal` se llena tras timbrado
- [ ] Intentar timbrar factura ya `TIMBRADA` → error controlado, sin retimbrado
- [ ] XML timbrado contiene `tfd:TimbreFiscalDigital` con UUID
- [ ] Descarga XML retorna `Content-Type: application/xml`

**Anti-patrones:**
- No hacer la llamada al PAC sin timeout (puede bloquear el request indefinidamente)
- No almacenar solo el UUID; guardar el XML completo (es el documento oficial)
- No reintentar timbrado sin verificar si el UUID ya existe

---

### ✅ Fase 6 — Complemento de pago (COMPLETADA 2026-06-28)

**Objetivo:** Registrar pagos recibidos contra facturas `PPD` y generar el CFDI
tipo `P` (Pago) requerido por el SAT cuando el cobro no fue en una sola exhibición.

#### 6.1 Modelos

```python
class Pago(models.Model):
    ESTADO = [('PENDIENTE', 'Pendiente'), ('TIMBRADO', 'Timbrado')]
    fecha_pago = models.DateField()
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    moneda = models.CharField(max_length=3, default='MXN')
    forma_pago = models.CharField(max_length=2)
    num_operacion = models.CharField(max_length=100, blank=True)
    estado = models.CharField(max_length=10, choices=ESTADO, default='PENDIENTE')
    uuid_fiscal = models.UUIDField(null=True, blank=True, unique=True)
    xml_timbrado = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class DoctoRelacionado(models.Model):
    """Fracción de una factura liquidada por un Pago."""
    pago = models.ForeignKey(Pago, on_delete=models.CASCADE, related_name='documentos')
    factura = models.ForeignKey(Factura, on_delete=models.PROTECT)
    num_parcialidad = models.PositiveSmallIntegerField(default=1)
    imp_saldo_anterior = models.DecimalField(max_digits=14, decimal_places=2)
    imp_pagado = models.DecimalField(max_digits=14, decimal_places=2)
    imp_saldo_insoluto = models.DecimalField(max_digits=14, decimal_places=2)
```

#### 6.2 Generador complemento de pago

**Archivo:** `finanzas/cfdi_generator.py` (función adicional)

```python
def generar_xml_complemento_pago(pago: 'Pago') -> str:
    """
    CFDI 4.0 tipo P con nodo Complemento/Pagos20.
    Namespace: http://www.sat.gob.mx/Pagos20
    """
    ...
```

#### 6.3 Vistas

```python
path('pagos/', views.pagos_list, name='pagos_list'),
path('pagos/nuevo/', views.pago_registrar, name='pago_registrar'),
path('pagos/<int:pk>/timbrar/', views.pago_timbrar, name='pago_timbrar'),
```

Flujo: seleccionar factura timbrada con saldo → capturar monto y forma de pago
→ calcular parcialidad → timbrar complemento via mismo `timbrar_cfdi()`.

#### Verificación de Fase 6
- [ ] `DoctoRelacionado.imp_saldo_insoluto == imp_saldo_anterior - imp_pagado`
- [ ] Complemento timbrado tiene UUID diferente al de la factura original
- [ ] XML complemento contiene nodo `pago20:Pagos`

---

### ✅ Fase 7 — Balanza de comprobación y contabilidad electrónica SAT (COMPLETADA 2026-06-28)

**Objetivo:** Calcular saldos por cuenta contable y exportar los XMLs
de contabilidad electrónica SAT (Catálogo + Balanza + Pólizas).

#### 7.1 Cálculo de balanza

**Archivo:** `finanzas/balanza.py`

```python
from django.db.models import Sum
from decimal import Decimal

def calcular_balanza(mes: int, anio: int) -> list[dict]:
    """
    Por cada CuentaContable con movimientos:
    - saldo_inicial: suma de partidas de meses anteriores
    - debe / haber: movimientos del mes
    - saldo_final: saldo_inicial + debe - haber (cuentas deudoras) o inverso

    Retorna lista de dicts con los 5 campos.
    """
    ...
```

#### 7.2 Exportación XML SAT

**Archivo:** `finanzas/exportar_sat.py`

Tres funciones de exportación según el estándar SAT Contabilidad Electrónica v1.3:

```python
def exportar_catalogo_cuentas_xml() -> str:
    """Namespace: http://www.sat.gob.mx/esquemas/ContabilidadE/1_3/CatalogoCuentas"""

def exportar_balanza_xml(mes: int, anio: int, tipo_envio: str = 'N') -> str:
    """Namespace: ...BalanzaComprobacion | tipo_envio: N=Normal, C=Complementaria"""

def exportar_polizas_xml(mes: int, anio: int, tipo: str) -> str:
    """Namespace: ...PolizasPeriodo | tipo: D=Diario, H=Haber, E=Egreso"""
```

#### 7.3 Vistas

```python
path('balanza/', views.balanza_view, name='balanza'),
path('balanza/exportar-xml/', views.balanza_exportar_xml, name='balanza_exportar_xml'),
path('catalogo-cuentas/exportar-xml/', views.catalogo_cuentas_exportar, name='catalogo_xml'),
```

#### Verificación de Fase 7
- [ ] `calcular_balanza(mes, anio)` retorna lista sin error con pólizas de prueba
- [ ] Suma total debe == suma total haber (partida doble cuadra)
- [ ] XML de balanza pasa validación contra `BalanzaComprobacion_1_3.xsd`
- [ ] Descarga retorna `Content-Disposition: attachment; filename=...`

---

### ✅ Fase 8 — Consolidación bancaria (COMPLETADA 2026-06-28)

**Objetivo:** Conciliar movimientos del estado de cuenta bancario
contra pólizas de ingreso/egreso del periodo.

#### 8.1 Modelos

```python
class CuentaBancaria(models.Model):
    nombre = models.CharField(max_length=100)
    banco = models.CharField(max_length=50)
    numero_cuenta = models.CharField(max_length=30)
    clabe = models.CharField(max_length=18, blank=True)
    moneda = models.CharField(max_length=3, default='MXN')
    cuenta_contable = models.ForeignKey(CuentaContable, on_delete=models.PROTECT)
    activa = models.BooleanField(default=True)


class MovimientoBancario(models.Model):
    cuenta = models.ForeignKey(CuentaBancaria, on_delete=models.CASCADE)
    fecha = models.DateField()
    descripcion = models.CharField(max_length=300)
    referencia_banco = models.CharField(max_length=100, blank=True)
    cargo = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    abono = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    saldo = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    poliza = models.ForeignKey(
        PolizaContable, null=True, blank=True, on_delete=models.SET_NULL
    )
    conciliado = models.BooleanField(default=False)
    mes = models.PositiveSmallIntegerField()
    anio = models.PositiveIntegerField()
```

#### 8.2 Importador de estado de cuenta

**Archivo:** `finanzas/management/commands/importar_estado_cuenta.py`

```bash
python manage.py importar_estado_cuenta --cuenta-id 1 --archivo estado_jun2026.csv
```

Formato CSV esperado: `fecha, descripcion, referencia, cargo, abono, saldo`

#### 8.3 Motor de conciliación automática

**Archivo:** `finanzas/conciliacion.py`

```python
def conciliar_automatico(mes: int, anio: int, cuenta_id: int) -> dict:
    """
    Match automático por monto + fecha ± 3 días.
    Prioridad: referencia bancaria == número de póliza (exacto)
               > monto + fecha ± 1 día (alto)
               > monto + fecha ± 3 días (sugerido, requiere confirmación)
    """
    ...
```

#### 8.4 Vistas

```python
path('conciliacion/', views.conciliacion_view, name='conciliacion'),
path('conciliacion/confirmar/<int:movimiento_id>/', views.confirmar_conciliacion, name='confirmar_conciliacion'),
```

#### Verificación de Fase 8
- [ ] Import CSV crea `MovimientoBancario` sin error
- [ ] Conciliación automática matchea movimientos con monto/fecha exacto
- [ ] `MovimientoBancario.conciliado=True` persiste tras confirmación

---

### ✅ Fase 9 — Cierre mensual (COMPLETADA 2026-06-28)

**Objetivo:** Validar, congelar y exportar el paquete contable del mes.

#### 9.1 Modelo `CierreMensual`

```python
class CierreMensual(models.Model):
    mes = models.PositiveSmallIntegerField()
    anio = models.PositiveIntegerField()
    patente = models.CharField(max_length=4)
    fecha_cierre = models.DateTimeField(auto_now_add=True)
    cerrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    total_polizas = models.PositiveIntegerField(default=0)
    total_facturas_emitidas = models.PositiveIntegerField(default=0)
    total_ingresos = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_egresos = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_anticipos_recibidos = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    observaciones = models.TextField(blank=True)

    class Meta:
        unique_together = ('mes', 'anio', 'patente')
```

#### 9.2 Proceso de cierre

**Archivo:** `finanzas/cierre.py`

```python
def ejecutar_cierre_mensual(mes: int, anio: int, patente: str, user) -> CierreMensual:
    """
    1. Validar: todas las pólizas del mes cuadran (debe == haber)
    2. Validar: conciliación bancaria sin diferencias sin documentar
    3. Calcular totales: anticipos, gastos, facturas del periodo
    4. Marcar PolizaContable.cerrado=True para el periodo
    5. Crear CierreMensual
    Lanza CierreError con lista de problemas si hay validaciones fallidas.
    """
    ...
```

#### 9.3 Exportación de paquete SAT

Vista `cierre_exportar_paquete` genera ZIP con:
- `catalogo_cuentas_{RFC}_{ANIO}{MES}.xml`
- `balanza_{RFC}_{ANIO}{MES}.xml`
- `polizas_{RFC}_{ANIO}{MES}D.xml`, `...H.xml`, `...E.xml`

#### 9.4 Vistas

```python
path('cierre/', views.cierre_list, name='cierre_list'),
path('cierre/ejecutar/', views.cierre_ejecutar, name='cierre_ejecutar'),
path('cierre/<int:pk>/exportar/', views.cierre_exportar_paquete, name='cierre_exportar'),
```

#### Verificación de Fase 9
- [ ] Cierre falla si hay póliza descuadrada
- [ ] `PolizaContable.cerrado=True` bloquea edición en vistas
- [ ] ZIP contiene los 5 archivos XML esperados
- [ ] Doble cierre del mismo periodo → error único de base de datos

---

### ✅ Fase 10 — Reporte de comisiones (COMPLETADA 2026-06-28)

**Objetivo:** Calcular comisiones por referencia a partir de los gastos registrados
y generar reporte mensual por agente/cliente.

#### 10.1 Modelo

```python
class ComisionReferencia(models.Model):
    referencia = models.OneToOneField(
        'referencias.Referencia', on_delete=models.CASCADE, related_name='comision'
    )
    agente = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    valor_operacion = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tasa_comision = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    monto_comision = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    mes = models.PositiveSmallIntegerField()
    anio = models.PositiveIntegerField()
    fecha_calculo = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-anio', '-mes']
```

#### 10.2 Motor de cálculo

**Archivo:** `finanzas/comisiones.py`

```python
def calcular_comisiones_mes(mes: int, anio: int) -> list[ComisionReferencia]:
    """
    Para cada referencia con gastos en el periodo:
    1. valor_operacion = suma de GastoReferencia del mes
    2. tasa_comision: tasa por cliente (futura configuración) o tasa general
    3. monto_comision = valor_operacion * tasa_comision
    4. Crear/actualizar ComisionReferencia
    """
    ...
```

Tarea APScheduler mensual (agregar en `reportes/jobs.py`):
```python
scheduler.add_job(
    lambda: calcular_comisiones_mes(hoy.month, hoy.year),
    'cron', day=1, hour=6, id='calcular_comisiones', replace_existing=True
)
```

#### 10.3 Vistas

```python
path('comisiones/', views.comisiones_reporte, name='comisiones_reporte'),
path('comisiones/exportar/', views.comisiones_exportar_csv, name='comisiones_csv'),
```

#### Verificación de Fase 10
- [ ] `calcular_comisiones_mes(mes, anio)` crea registros por referencia con gastos
- [ ] Export CSV: referencia, cliente, agente, valor_op, tasa, comisión
- [ ] Tarea registrada en `django_apscheduler_djangojobs` tras arranque

---

### Fase 11 — Integración y verificación final

**Objetivo:** Conectar finanzas con los reportes existentes y verificar flujos completos.

#### 11.1 Datos financieros en reportes existentes

**Archivo:** `reportes/data.py` — agregar:
```python
def get_datos_finanzas_semana():
    """Facturas emitidas, total anticipos recibidos, referencias con saldo pendiente."""
    ...
```

**Archivo:** `reportes/ai_analysis.py` — agregar:
```python
def analizar_finanzas_mensual(datos: dict) -> str:
    """Análisis Claude del estado financiero mensual (claude-sonnet-4-6, 1024 tokens)."""
    ...
```

#### 11.2 Enlace desde detalle de referencia

Agregar en `referencias/templates/.../detalle.html` un panel resumen financiero:
- Total anticipos / Total gastos / Saldo
- Botón "Ver estado financiero completo" → `referencia_estado_financiero`
- Botón "Subir XML proveedor"

#### 11.3 Permisos

Crear grupos Django:
- `finanzas_lectura` — ver pólizas, facturas, balanza
- `finanzas_operador` — registrar anticipos, gastos, subir XMLs, registrar pagos
- `finanzas_admin` — timbrar, cierre mensual, exportar SAT

Proteger todas las vistas con `@login_required` + `permission_required`.

#### 11.4 Checklist final

- [ ] Flujo completo: Referencia → Anticipo → Gasto + XML → Pólizas → Factura → Timbrado → Pago → Complemento
- [ ] Balanza cuadra con pólizas del periodo de prueba
- [ ] `python manage.py check --deploy` sin errores críticos
- [ ] `grep -r "float(" finanzas/` → cero resultados (no floats en montos)
- [ ] Todos los `ForeignKey` tienen `on_delete` explícito
- [ ] Cierre mensual genera ZIP con XMLs válidos
- [ ] Reporte semanal incluye resumen financiero

---

## Dependencias entre fases

```
Fase 0 (certificados + PAC)
  └─→ Fase 1 (modelos base: catálogos SAT, plan de cuentas)
        ├─→ Fase 2 (anticipos + gastos + pólizas)  ← núcleo
        │     ├─→ Fase 3 (XMLs proveedores)
        │     ├─→ Fase 7 (balanza)
        │     │     └─→ Fase 9 (cierre mensual)
        │     ├─→ Fase 8 (consolidación bancaria)
        │     └─→ Fase 10 (comisiones)
        ├─→ Fase 4 (modelos factura + cobranza)
        │     ├─→ Fase 5 (timbrado CFDI 4.0)
        │     │     └─→ Fase 6 (complemento de pago)
        │     └─→ (usa saldo de Fase 2)
        └─→ Fase 11 (integración final)
```

---

## Variables de entorno nuevas (`.env`)

```env
# ── Emisores — un bloque por patente ────────────────────────────────────────
# Patente 1627
CFDI_1627_RFC=
CFDI_1627_NOMBRE=
CFDI_1627_REGIMEN=612
CFDI_1627_CP=
CFDI_1627_CERT_PATH=
CFDI_1627_KEY_PATH=
CFDI_1627_KEY_PASSWORD=

# Patente 1656
CFDI_1656_RFC=
CFDI_1656_NOMBRE=
CFDI_1656_REGIMEN=612
CFDI_1656_CP=
CFDI_1656_CERT_PATH=
CFDI_1656_KEY_PATH=
CFDI_1656_KEY_PASSWORD=

# Patente 1927
CFDI_1927_RFC=
CFDI_1927_NOMBRE=
CFDI_1927_REGIMEN=612
CFDI_1927_CP=
CFDI_1927_CERT_PATH=
CFDI_1927_KEY_PATH=
CFDI_1927_KEY_PASSWORD=

# ── PAC de timbrado (compartido entre los 3 emisores) ────────────────────────
PAC_PROVIDER=sw_sapien
PAC_URL=
PAC_TOKEN=
PAC_USER=
PAC_PASSWORD=
```

## Nuevas dependencias (`requirements.txt`)

```
lxml>=5.0
cryptography>=42.0
```

## Resumen de archivos nuevos

| Fase | Archivos clave |
|------|---------------|
| 1 | `finanzas/models.py`, `finanzas/apps.py`, `finanzas/admin.py`, `finanzas/utils.py`, `finanzas/management/commands/cargar_configs_fiscales.py`, `finanzas/fixtures/catalogos_sat.json`, `finanzas/fixtures/plan_cuentas_inicial.json` |
| 2 | `finanzas/saldo.py`, `finanzas/views.py`, `finanzas/urls.py` |
| 3 | `finanzas/cfdi_parser.py` |
| 4 | (extiende `finanzas/models.py` y `finanzas/views.py`) |
| 5 | `finanzas/cfdi_generator.py`, `finanzas/pac_client.py`, `finanzas/management/commands/timbrar_factura.py` |
| 6 | (extiende modelos + `cfdi_generator.py`) |
| 7 | `finanzas/balanza.py`, `finanzas/exportar_sat.py` |
| 8 | `finanzas/conciliacion.py`, `finanzas/management/commands/importar_estado_cuenta.py` |
| 9 | `finanzas/cierre.py`, `finanzas/management/commands/cierre_mensual.py` |
| 10 | `finanzas/comisiones.py` |
| 11 | (modificaciones en `reportes/data.py`, `reportes/ai_analysis.py`, templates de referencias) |

---

*Generado: 2026-06-24 | Sin conexión a Firebird — fuente de datos: `referencias.Referencia` (Django ORM)*
