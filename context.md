# HAL9MIL — Contexto del Proyecto

## Descripción General

HAL9MIL es un sistema de consulta y estadísticas aduanales para Loginco, construido con Django 5.2. Extrae y visualiza datos directamente de las bases de datos Firebird **CASA.GDB** de las tres patentes de la agencia aduanal. El sistema permite consultar referencias de embarque, pedimentos, contenedores y guías BL (Bill of Lading), con un dashboard de analytics histórico 2022–2026. Incluye un bot de WhatsApp para consultas rápidas y un agente Windows que sincroniza incrementalmente los datos desde Firebird hacia la nube.

---

## Stack Tecnológico

| Componente | Tecnología |
|---|---|
| Backend | Django 5.2 |
| Base de datos Django | SQLite3 (dev) / PostgreSQL 18 (prod) |
| Fuente de datos | Firebird 2.5 (`CASA.GDB`) vía `fdb` |
| Frontend | Tailwind CSS (CDN Play) + Chart.js 4.4 |
| Autenticación | Django session auth (login requerido) |
| Reportes | APScheduler + SendGrid + Claude AI (Anthropic) |
| WhatsApp bot | OpenWA (whatsapp-web.js) vía webhook HMAC |
| Python | 3.12 |
| Entorno virtual | `.venv/` |
| Locale | `es-mx` / `America/Mexico_City` |

---

## Estructura del Proyecto

```
Proyecto_HAL9MIL/
├── .venv/                          # Entorno virtual Python
├── .env                            # Variables de entorno (no versionado)
├── .do/app.yaml                    # Config Digital Ocean App Platform
├── Procfile                        # Comando de arranque (gunicorn)
├── runtime.txt                     # python-3.12
├── context.md                      # Este archivo
├── hal9mil/                        # Configuración Django
│   ├── settings.py                 # SQLite local / PostgreSQL prod vía DBURL
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── referencias/                    # App principal
│   ├── models.py                   # Referencia, Contenedor, GuiaBL, LogSync
│   ├── views.py                    # dashboard, lista, detalle
│   ├── sync_views.py               # Endpoint POST /api/sync/
│   ├── urls.py
│   ├── admin.py
│   └── management/commands/
│       └── import_firebird.py      # ETL Firebird → Django (importación completa)
├── reportes/                       # App de reportes periódicos
│   ├── data.py                     # get_datos_semana(), get_datos_mes()
│   ├── ai_analysis.py              # Análisis ejecutivo con Claude AI
│   ├── jobs.py                     # Tareas APScheduler
│   ├── scheduler.py                # BackgroundScheduler + DjangoJobStore
│   └── management/commands/
│       └── enviar_reporte.py       # Envío manual de reportes
├── whatsapp/                       # Bot de WhatsApp (OpenWA)
│   ├── bot.py                      # Lógica de comandos del bot
│   ├── client.py                   # Cliente HTTP hacia OpenWA API
│   ├── views.py                    # Webhook receptor (HMAC auth)
│   └── urls.py                     # /whatsapp/webhook/
├── sync_agent/                     # Agente Windows → Cloud (sincronización incremental)
│   ├── sync_agent.py               # Script principal
│   ├── config.ini.example          # Plantilla de configuración
│   └── logs/                       # Directorio de logs (no versionado)
├── templates/
│   ├── base.html                   # Layout con sidebar
│   ├── login.html
│   ├── dashboard.html              # Vista principal con charts
│   ├── referencias/
│   │   ├── lista.html              # Tabla paginada con filtros
│   │   └── detalle.html            # Ficha completa de referencia
│   └── reportes/
│       ├── semanal.html            # Email HTML reporte semanal
│       └── mensual.html            # Email HTML reporte mensual
├── static/
├── db.sqlite3
├── manage.py
└── requirements.txt
```

---

## Modelos Django (`referencias/models.py`)

### `Referencia`
Tabla principal. Una fila por `NUM_REFE` único. Almacena los datos del pedimento primario (no rectificación).

| Campo | Tipo | Fuente Firebird | Descripción |
|---|---|---|---|
| `num_refe` | CharField (unique) | `SAAIO_PEDIME.NUM_REFE` | Clave de referencia (ej. `LCRR0881/26`) |
| `patente` | CharField | Configuración | `1627`, `1656`, `1927` |
| `prefijo` | CharField | Derivado | `LCLF`, `LCRR`, `LCMJ` |
| `cve_cliente` | CharField | `SAAIO_PEDIME.CVE_IMPO` | Código cliente en Firebird |
| `nombre_cliente` | CharField | `CTRAC_CLIENT.NOM_IMP` | Nombre del importador |
| `fecha_arribo` | DateField | `CTRAO_EMBAR.FEC_ENTR` | Fecha de entrada del buque |
| `fecha_validacion` | DateField | `SAAIO_PEDIME.FEC_ENTR` | Fecha de validación del pedimento |
| `fecha_pago` | DateField | `SAAIO_PEDIME.FEC_PAGO` | Fecha de pago de derechos |
| `num_pedimento` | CharField | `SAAIO_PEDIME.NUM_PEDI` | Número de pedimento SAT |
| `clave_pedimento` | CharField | `SAAIO_PEDIME.CVE_PEDI` | Clave (`A1`, `A4`, `R1`…) |
| `tipo_pedimento` | CharField | `SAAIO_PEDIME.TIP_PEDI` | Tipo (`R1` = rectificación) |
| `aduana` | CharField | `SAAIO_PEDIME.ADU_DESP` | Código de aduana de despacho |
| `regimen` | CharField | `SAAIO_PEDIME.REG_ADUA` | Régimen aduanero |
| `num_operacion` | CharField | `SAAIO_PEDIME2.NUM_OPER` | Número de operación bancaria del pago |
| `linea_captura` | CharField | `SAAIO_PEDIME2.PAG_LCAP` | Línea de captura SAT |
| `cve_capturista` | CharField | `SAAIO_PEDIME.CVE_CAPT` | Clave del usuario capturista en CASA |
| `nombre_capturista` | CharField | `SISSEG_USUARI.NOMBRE` | Nombre completo del capturista |
| `es_rectificacion` | BooleanField | Derivado | `True` si `NUM_REFE` empieza con `R` **y** `len > 5` |
| `created_at` | DateTimeField | Auto | Timestamp de creación del registro Django |
| `updated_at` | DateTimeField | Auto | Timestamp de última actualización Django |

Índices compuestos en `Meta`: `(fecha_pago, patente)` y `(patente, fecha_pago)`.

> **Criterio de pago real:** Un pedimento se considera **efectivamente pagado** cuando tiene
> `num_operacion != ''` **Y** `linea_captura != ''`. Solo estos se cuentan en métricas de
> "pagados" en reportes y filtros. El campo `fecha_pago` puede existir sin que ambos estén
> presentes, por lo que no es suficiente por sí solo.

### `Contenedor`
FK a `Referencia`. Una fila por contenedor.

| Campo | Fuente Firebird | Descripción |
|---|---|---|
| `num_cont` | `SAAIO_CONTEN.NUM_CONT` | Número ISO 6346 |
| `tipo` | `SAAIO_CONTEN.CVE_CONT` → mapa | `20DC`, `40HC`, `40RF`, etc. |

### `GuiaBL`
FK a `Referencia`. Una fila por guía/BL.

| Campo | Fuente Firebird | Descripción |
|---|---|---|
| `numero_guia` | `SAAIO_GUIAS.GUIA` | Número de Bill of Lading (max 60 chars) |
| `tipo_guia` | `SAAIO_GUIAS.IDE_MH` | `M` = Master BL, `H` = House BL |

### `LogSync`
Registra cada ejecución del agente de sincronización Windows → Cloud.

| Campo | Tipo | Descripción |
|---|---|---|
| `timestamp` | DateTimeField | Momento de la sincronización |
| `patente` | CharField | Patente sincronizada |
| `agent_id` | CharField | Identificador del agente (hostname) |
| `referencias` | IntegerField | Total referencias procesadas |
| `contenedores` | IntegerField | Total contenedores procesados |
| `guias` | IntegerField | Total guías BL procesadas |
| `creadas` | IntegerField | Registros nuevos insertados |
| `actualizadas` | IntegerField | Registros existentes actualizados |
| `exitoso` | BooleanField | `True` si completó sin errores |
| `error` | TextField | Detalle del error si falló |
| `duracion_seg` | FloatField | Segundos que tardó la sincronización |

---

## Bases de Datos Firebird — CASA.GDB

Sistema de agencia aduanal. Tres instancias independientes, una por patente.

### Conexión

```python
import fdb
con = fdb.connect(
    host='localhost', port=3050,
    database='/databases/{patente}/CASA.GDB',
    user='SYSDBA', password='masterkey',
    charset='WIN1252',
)
```

> El contenedor Docker Firebird 2.5 debe estar activo:
> ```bash
> docker start fb25
> ```

### Patentes y Prefijos

| Patente | Prefijo | Path base de datos |
|---|---|---|
| `1627` | `LCLF` | `/databases/1627/CASA.GDB` |
| `1656` | `LCRR` | `/databases/1656/CASA.GDB` |
| `1927` | `LCMJ` | `/databases/1927/CASA.GDB` |

### Tablas Clave

| Tabla | Registros aprox. | Descripción |
|---|---|---|
| `CTRAC_CLIENT` | ~130–200/patente | Catálogo de importadores (`CVE_IMP`, `NOM_IMP`) |
| `CTRAO_EMBAR` | ~3,000–5,000/patente | Embarques — `FEC_ENTR` (arribo), `APE_REFE` (última modificación) |
| `SAAIO_PEDIME` | ~12,000–15,000/patente | Pedimentos — `FEC_ENTR`, `FEC_PAGO`, `DIA_PAGO`, `NUM_PEDI` |
| `SAAIO_PEDIME2` | — | Línea de captura SAT (`PAG_LCAP`) y número de operación (`NUM_OPER`) |
| `SAAIO_CONTEN` | ~12,000–15,000/patente | Contenedores por referencia (`NUM_CONT`, `CVE_CONT`) |
| `SAAIO_GUIAS` | ~12,000–15,000/patente | Guías / BL por referencia (`GUIA`, `IDE_MH`) |
| `SISSEG_USUARI` | ~42/patente | Usuarios del sistema (`LOGIN`, `NOMBRE`) |

> **Importante:** `CTRAO_EMBAR` es tabla activa — los expedientes cerrados se eliminan de ella
> pero permanecen en `SAAIO_PEDIME`. Por eso el import usa `SAAIO_PEDIME` como fuente
> principal y `CTRAO_EMBAR` solo para `fecha_arribo`.
>
> **Fallback:** si una referencia no aparece en `CTRAO_EMBAR`, se usa `fecha_validacion` como proxy de `fecha_arribo`.

### Mapeo CVE_CONT → Tipo de Contenedor

```python
CVE_CONT_TIPO = {
    1: '20DC', 2: '20RF', 3: '40HC', 4: '40RF',
    9: '20TK', 11: '45HC', 16: '40OT', 17: '40OT',
    20: '40FR', 25: '40FR',
}
```

---

## URLs del Sistema

| URL | Vista | Descripción |
|---|---|---|
| `/` | `dashboard` | Dashboard con KPIs y gráfica histórica |
| `/referencias/` | `lista` | Tabla paginada con búsqueda y filtros |
| `/referencias/<num_refe>/` | `detalle` | Ficha completa (usa `<path:>` por el `/` en la referencia) |
| `/login/` | `LoginView` | Pantalla de acceso |
| `/logout/` | `LogoutView` | Cierre de sesión |
| `/admin/` | Admin Django | Gestión de usuarios |
| `/api/sync/` | `sync_endpoint` | POST — recibe datos del sync_agent (Token auth) |
| `/whatsapp/webhook/` | `webhook` | POST — recibe mensajes de OpenWA (HMAC auth) |

> **Nota técnica:** La URL de detalle usa `<path:num_refe>` (no `<str:>`) porque las
> referencias contienen `/` (ej. `LCLF0331/26`).

---

## Dashboard — Métricas

- **Métrica principal:** `Referencia.fecha_pago` (fecha de pago del pedimento en aduana)
- **Excluye:** rectificaciones (`es_rectificacion=True`) de los conteos generales
- **Proyección 2026:** promedio ponderado 2024–2025 con factor estacional mensual y tasa de crecimiento interanual

### KPIs mostrados

| KPI | Cálculo |
|---|---|
| Total año actual | `Referencia.filter(fecha_pago__year=año, es_rectificacion=False).count()` |
| Mes actual | Slice del array mensual del año en curso |
| Variación vs mes anterior | `((mes_actual - mes_anterior) / mes_anterior) * 100` |
| Por patente | Agrupado por `patente` con `Count('id')` |

### Secciones adicionales del dashboard

- **Tabla comparativa 2026:** Mes a mes con columnas `proyectado`, `real`, `delta`, `pct` y estado (`completed` / `in_progress` / `future`). Contexto: `comparativa`.
- **Últimas 10 referencias recientes:** Las 10 últimas `Referencia` con `fecha_pago__isnull=False` y `es_rectificacion=False`, con prefetch de contenedores y guías. Contexto: `recientes`.

### Filtros disponibles en Lista (`/referencias/`)

| Parámetro GET | Descripción |
|---|---|
| `q` | Búsqueda full-text: `num_refe`, `num_pedimento`, `nombre_cliente`, `contenedores__num_cont`, `guias__numero_guia` |
| `patente` | Filtra por `1627`, `1656` o `1927` |
| `año` | Filtra por `fecha_pago__year` |
| `mes` | Filtra por `fecha_pago__month` |
| `clave` | Filtra por `clave_pedimento` (ej. `A1`, `A4`) |
| `pagadas` | Si presente, solo referencias con `fecha_pago` no nula |
| `rectificaciones` | Si presente, incluye rectificaciones; por defecto se excluyen |
| `orden` | Campo de ordenamiento. Válidos: `fecha_pago`, `num_refe`, `nombre_cliente`, `fecha_arribo`, `patente` (prefijo `-` para DESC) |

---

## Acceso

- **URL local:** `http://127.0.0.1:8001/`
- **Usuario:** `admin`
- **Contraseña:** `loginco2026`

```bash
# Levantar servidor de desarrollo
cd ~/Developer/Proyecto_HAL9MIL
source .venv/bin/activate
python manage.py runserver 8001
```

---

## Comandos Frecuentes

```bash
# Activar entorno virtual
source .venv/bin/activate

# Levantar servidor
python manage.py runserver 8001

# Re-importar datos desde Firebird (asegurarse que Docker Firebird esté activo)
docker start fb25
python manage.py import_firebird

# Crear usuario adicional
python manage.py createsuperuser

# Shell Django
python manage.py shell
```

---

## Notas de Diseño

- **Tailwind CSS via CDN Play** — sin proceso de build, apto para desarrollo
- **Chart.js 4.4** — gráfica de líneas multi-año con proyección 2026
- **Sidebar fijo** — navegación lateral oscura (slate-900), contenido claro
- **Colores por patente:** LCLF → sky-500, LCRR → emerald-500, LCMJ → violet-500
- **Paginación:** 50 registros por página

---

## App de Reportes (`reportes/`)

Genera y envía reportes HTML por correo usando SendGrid.

### Scheduler

`reportes/scheduler.py` usa `BackgroundScheduler` + `DjangoJobStore`. Arranca automáticamente al iniciar Django (incluyendo bajo gunicorn en producción). Usa lista de denegación `_SKIP_COMMANDS` para no arrancar durante `migrate`, `collectstatic`, etc. La guardia `RUN_MAIN=true` evita doble arranque en el auto-reloader de desarrollo.

| Job | Trigger | Descripción |
|---|---|---|
| `reporte_semanal` | Lunes 07:00 CST | Reporte de la semana anterior |
| `reporte_mensual` | Día 1 del mes 07:00 CST | Reporte del mes anterior |

### `reportes/data.py` — Funciones de datos

#### `get_datos_semana(inicio, fin)`
Calcula métricas para el rango de fechas dado (lunes a domingo). Devuelve:

| Clave | Descripción |
|---|---|
| `validadas_total` | Referencias con `fecha_validacion` en el rango |
| `pagadas_total` | Referencias con `fecha_pago` en el rango |
| `validadas_por_patente` | Desglose de validadas por patente/prefijo |
| `pagadas_por_patente` | Desglose de pagadas por patente/prefijo |
| `contenedores_total` / `contenedores_por_tipo` | Contenedores de referencias pagadas |
| `guias_total` | Guías BL de referencias pagadas |
| `top_clientes` | Top 5 clientes por volumen pagado |
| `pendientes_pago` | Referencias validadas sin `fecha_pago` (global, no solo semana) |
| `rectificaciones_semana` | Rectificaciones validadas en el rango |
| `claves_pedimento` | Distribución por clave de pedimento |
| `por_capturista` | Por usuario: `capturadas`, `pagadas` (con num_operacion+linea_captura), `pendientes` |

#### `get_datos_mes(year, month)`
Calcula métricas del mes completo. Incluye todo lo anterior más:

| Clave | Descripción |
|---|---|
| `real` | Pedimentos pagados en el mes |
| `proyectado` | Proyección basada en promedio ponderado 2024–2025 con factor estacional |
| `delta_proyectado` / `pct_proyectado` | Variación vs proyección |
| `real_mes_anterior` / `delta_mes_anterior` / `pct_mes_anterior` | Comparativa mes anterior |
| `real_año_pasado` / `delta_año_pasado` / `pct_año_pasado` | Comparativa mismo mes año pasado |
| `acumulado_año` | Lista mes a mes con `real` y `proyectado` hasta el mes actual |
| `promedio_dias_despacho` | Promedio de días entre `fecha_arribo` y `fecha_pago` para pedimentos con pago real |
| `pedimentos_con_pago_real` | Cantidad de pedimentos usados para calcular el promedio |

> **Pago real en reportes:** `num_operacion__gt=''` AND `linea_captura__gt=''`

### Reporte Semanal — Secciones del email

1. Header con total pagadas de la semana
2. KPI cards: Validadas / Pagadas / Contenedores / Pendientes
3. Validadas por patente (barras de progreso)
4. Contenedores por tipo
5. Top 5 clientes
6. **Referencias por usuario** — tabla: Usuario / Capturadas / Pagadas / Pendientes
7. Datos adicionales (Guías BL, Rectificaciones, Pendientes pago)
8. Análisis ejecutivo Claude AI (si disponible)

### Reporte Mensual — Secciones del email

1. Header con total pagadas y delta vs proyectado
2. Real vs Proyectado + barra de progreso
3. Comparativas (vs mes anterior, vs año anterior)
4. Acumulado año mes a mes
5. Indicadores operacionales: Validadas / Contenedores / Guías BL / Rectificaciones / Pendientes / % proyectado
6. **Promedio días de despacho** (arribo → pago real) — tarjeta azul destacada
7. Por patente (barras de progreso)
8. Claves de pedimento
9. Top 8 clientes
10. Análisis ejecutivo Claude AI (si disponible)

---

## Bot de WhatsApp (`whatsapp/`)

Bot conversacional vía OpenWA (whatsapp-web.js). Responde a comandos en WhatsApp con datos en tiempo real de Django.

### Arquitectura

```
WhatsApp ←→ OpenWA container ──POST /whatsapp/webhook/──→ Django
                                   (HMAC-SHA256)              ↓
                                                         bot.py procesa
                                                              ↓
                                                         Referencia queryset
```

### Autenticación del webhook

`WA_WEBHOOK_SECRET` en settings. OpenWA firma cada request con HMAC-SHA256; Django verifica antes de procesar.

### Comandos disponibles

| Comando | Descripción |
|---|---|
| `ayuda` | Muestra el menú de comandos |
| `ref LCRR0881/26` | Busca una referencia por número exacto |
| `ped 7000001` | Busca referencia por número de pedimento |
| `cont CMAU5662811` | Busca referencia por número de contenedor |
| `bl SEL0702970` | Busca referencia por número de guía BL |
| `hoy` | Referencias pagadas hoy |
| `mes` | KPIs del mes en curso |
| `sync` | Estado de la última sincronización por patente |
| `refs LCRR mayo` | Total de referencias por patente y mes |

### Variables de entorno requeridas

| Variable | Descripción |
|---|---|
| `WA_API_URL` | URL del servicio OpenWA (ej. `http://openwa:3000`) |
| `WA_API_KEY` | API key del servicio OpenWA |
| `WA_SESSION_ID` | ID de sesión WhatsApp activa |
| `WA_ADMIN_CHAT` | Número de WhatsApp del admin (para notificaciones) |
| `WA_WEBHOOK_SECRET` | Secreto HMAC compartido con OpenWA |
| `WA_ALLOWED_NUMBERS` | Lista de números autorizados para usar el bot (separados por coma) |

---

## Comando de Importación (`import_firebird`)

ETL completo desde Firebird hacia Django. Idempotente (`update_or_create`). Solo se usa para la carga histórica inicial — en producción la sincronización continua es responsabilidad del `sync_agent`.

```bash
# Importar todo el histórico (todas las patentes)
python manage.py import_firebird

# Solo previsualizar sin escribir
python manage.py import_firebird --dry-run

# Solo una o dos patentes
python manage.py import_firebird --patentes 1627 1656
```

### Volúmenes importados (mayo 2026)

| Patente | Referencias | Contenedores | Guías BL |
|---|---|---|---|
| 1627 (LCLF) | 12,303 | 12,332 | 12,249 |
| 1656 (LCRR) | 14,472 | 14,892 | 14,438 |
| 1927 (LCMJ) | 138 | 226 | 150 |
| **Total** | **26,913** | **27,450** | **26,837** |

---

## Agente de Sincronización (`sync_agent/`)

Proceso autónomo para Windows que extrae datos de Firebird local y los empuja al servidor Django en la nube. Corre vía Windows Task Scheduler.

### Arquitectura

```
Windows (patente local)          →          Django Cloud
  sync_agent.py                  →          POST /api/sync/  (Token auth)
  Lee CASA.GDB (Firebird)        →          Upsert en PostgreSQL
  Task Scheduler (cada N min)    →          LogSync registra cada ejecución
```

### Modos de ejecución

| Comando | Comportamiento |
|---|---|
| `python sync_agent.py` | **Incremental** — solo refs modificadas desde el último sync |
| `python sync_agent.py --full-sync` | Sync completo de todos los registros |
| `python sync_agent.py --dry-run` | Extrae de Firebird pero no envía a Django |

### Lógica de sincronización incremental

El agente persiste el timestamp del último sync exitoso en `last_sync.json`. En cada ejecución consulta dos fuentes de cambio:

1. `CTRAO_EMBAR WHERE APE_REFE >= last_sync` — embarques modificados (APE_REFE se actualiza en cada cambio)
2. `SAAIO_PEDIME WHERE DIA_PAGO >= last_sync` — pedimentos recién pagados (el pago no toca CTRAO_EMBAR)

Si no hay cambios detectados, termina en segundos sin enviar nada. Si hay cambios, solo descarga y envía los datos de esas referencias. Los catálogos pequeños (`CTRAC_CLIENT`, `SISSEG_USUARI`) siempre se descargan completos (~130 y ~42 filas).

### Configuración (`config.ini`)

Cada servidor Windows tiene su propio `config.ini` (no versionado). Plantilla: `config.ini.example`.

```ini
[servidor]
patente  = 1627
agent_id = servidor-1627-loginco

[firebird]
db_path  = C:\ruta\a\CASA.GDB
host     = localhost
port     = 3050
user     = SYSDBA
password = masterkey

[django]
sync_url   = https://hal9mil.loginco.com.mx/api/sync/
secret_key = <clave-compartida-con-django>

[opciones]
request_timeout = 120
max_retries     = 2
chunk_size      = 500
```

> El parser usa fallback de encodings (`utf-8-sig → utf-8 → latin-1 → cp1252`) para
> compatibilidad con cualquier editor de Windows.

### Archivos versionados

| Archivo | Descripción |
|---|---|
| `sync_agent/sync_agent.py` | Script principal |
| `sync_agent/config.ini.example` | Plantilla de configuración |

### Endpoint Django

- **URL:** `POST /api/sync/`
- **Auth:** header `Authorization: Token <SYNC_SECRET_KEY>`
- **Vista:** `referencias/sync_views.py → sync_endpoint`
- **Config Django:** `SYNC_SECRET_KEY` en settings (variable de entorno)

---

## Despliegue en Producción (Digital Ocean)

| Componente | Detalle |
|---|---|
| Plataforma | Digital Ocean App Platform |
| Plan | basic-xxs (~$5/mes) |
| Región | SFO |
| Base de datos | PostgreSQL 18 (DO Managed) |
| Servidor WSGI | Gunicorn 2 workers |
| Archivos estáticos | WhiteNoise |
| Config | `.do/app.yaml` (secretos vía env vars del dashboard) |

### Procfile

```
web: python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn hal9mil.wsgi --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

### Variables de entorno requeridas en producción

| Variable | Descripción |
|---|---|
| `SECRET_KEY` | Django secret key |
| `DBURL` | URL completa de PostgreSQL (`postgres://...`) |
| `SENDGRID_API_KEY` | API key de SendGrid para reportes |
| `ANTHROPIC_API_KEY` | API key de Anthropic para análisis Claude AI |
| `SYNC_SECRET_KEY` | Clave compartida con los agentes de sincronización |
| `WA_API_URL` | URL del servicio OpenWA |
| `WA_API_KEY` | API key de OpenWA |
| `WA_SESSION_ID` | ID de sesión WhatsApp |
| `WA_ADMIN_CHAT` | Número admin WhatsApp |
| `WA_WEBHOOK_SECRET` | Secreto HMAC para verificar mensajes de OpenWA |
| `WA_ALLOWED_NUMBERS` | Números autorizados para el bot (separados por coma) |
| `DEBUG` | `False` en producción |
| `ALLOWED_HOSTS` | Dominio asignado por DO |

> **Nota SSL:** DO App Platform termina SSL en el load balancer y reenvía HTTP internamente.
> `SECURE_SSL_REDIRECT` debe ser `False`. Se usa `SECURE_PROXY_SSL_HEADER` con `X-Forwarded-Proto`.
