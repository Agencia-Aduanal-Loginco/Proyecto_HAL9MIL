# HAL9MIL — Contexto del Proyecto

## Descripción General

HAL9MIL es un sistema de consulta y estadísticas aduanales para Loginco, construido con Django 5.2. Extrae y visualiza datos directamente de las bases de datos Firebird **CASA.GDB** de las tres patentes de la agencia aduanal. El sistema permite consultar referencias de embarque, pedimentos, contenedores y guías BL (Bill of Lading), con un dashboard de analytics histórico 2022–2026. Incluye un bot de WhatsApp (vía Twilio) para consultas rápidas, un agente Windows que sincroniza incrementalmente los datos desde Firebird hacia la nube, gestión operativa de Glosa y Cuenta de Gastos, y reportes semanales/mensuales con análisis de Claude AI.

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
| WhatsApp | Twilio REST API (bot + notificaciones + plantillas aprobadas) |
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
│   ├── models.py                   # Referencia, Contenedor, GuiaBL, GlosaRegistro, CuentaGastos, LogSync
│   ├── views.py                    # dashboard, lista, detalle, glosa*, cuenta_gastos*
│   ├── admin.py                    # GlosaRegistro, CuentaGastos, LogSync registrados
│   ├── glosa_data.py               # Analítica de glosa: analyze_notas(), get_datos_glosa_semana()
│   ├── cuenta_gastos_data.py       # Analítica de CG: get_datos_cuenta_gastos_semana()
│   ├── sync_views.py               # Endpoint POST /api/sync/
│   ├── urls.py
│   └── management/commands/
│       └── import_firebird.py      # ETL Firebird → Django (importación completa)
├── reportes/                       # App de reportes periódicos
│   ├── data.py                     # get_datos_semana(), get_datos_mes()
│   ├── ai_analysis.py              # analizar_semanal/mensual/glosa/cuenta_gastos con Claude AI
│   ├── jobs.py                     # Tareas APScheduler (email + WhatsApp template)
│   ├── scheduler.py                # BackgroundScheduler + DjangoJobStore
│   └── management/commands/
│       └── enviar_reporte.py       # Envío manual de reportes
├── whatsapp/                       # Bot WhatsApp vía Twilio
│   ├── bot.py                      # Lógica de comandos del bot
│   ├── client.py                   # send_text() (sesión) + send_template() (plantilla aprobada)
│   ├── views.py                    # Webhook receptor (firma Twilio)
│   └── urls.py                     # /whatsapp/webhook/
├── sync_agent/                     # Agente Windows → Cloud (sincronización incremental)
│   ├── sync_agent.py               # Script principal
│   ├── config.ini.example          # Plantilla de configuración
│   └── logs/                       # Directorio de logs (no versionado)
├── templates/
│   ├── base.html                   # Layout con sidebar responsive (hamburger móvil)
│   ├── login.html
│   ├── dashboard.html              # Vista principal con charts
│   └── referencias/
│       ├── lista.html              # Tabla paginada con filtros
│       ├── detalle.html            # Ficha completa + historial glosa + cuenta gastos
│       ├── glosa.html              # Lista operativa de glosa (con acciones staff)
│       ├── glosa_dashboard.html    # Dashboard estadístico de glosa
│       ├── cuenta_gastos.html      # Lista operativa de cuenta de gastos
│       └── cuenta_gastos_dashboard.html
│   └── reportes/
│       ├── semanal.html            # Email HTML reporte semanal (incluye glosa + CG)
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
| `fecha_validacion` | DateField | `SAAIO_REGVAL.FEC_VAL` | Fecha de validación del pedimento |
| `fecha_pago` | DateField | `SAAIO_PEDIME.FEC_PAGO` | Fecha de pago de derechos |
| `fecha_captura` | DateField | `SAAIO_PROCES.FEC_MODI` | Fecha de captura en CASA |
| `num_pedimento` | CharField | `SAAIO_PEDIME.NUM_PEDI` | Número de pedimento SAT |
| `clave_pedimento` | CharField | `SAAIO_PEDIME.CVE_PEDI` | Clave (`A1`, `A4`, `R1`…) |
| `tipo_pedimento` | CharField | `SAAIO_PEDIME.TIP_PEDI` | Tipo (`R1` = rectificación) |
| `aduana` | CharField | `SAAIO_PEDIME.ADU_DESP` | Código de aduana de despacho |
| `regimen` | CharField | `SAAIO_PEDIME.REG_ADUA` | Régimen aduanero |
| `num_operacion` | CharField | `SAAIO_PEDIME2.NUM_OPER` | Número de operación bancaria del pago |
| `linea_captura` | CharField | `SAAIO_PEDIME2.PAG_LCAP` | Línea de captura SAT |
| `cve_capturista` | CharField | `SAAIO_PEDIME.CVE_CAPT` | Clave del usuario capturista en CASA |
| `nombre_capturista` | CharField | `SISSEG_USUARI.NOMBRE` | Nombre completo del capturista |
| `fir_elec` | CharField | `SAAIO_PEDIME.FIR_ELEC` | Firma electrónica (vacía = pendiente de pago) |
| `es_rectificacion` | BooleanField | Derivado | `True` si `NUM_REFE` empieza con `R` **y** `len > 5` |
| `num_partidas` | IntegerField | `SAAIO_PEDIME.NUM_PART` | Número de fracciones arancelarias |
| `created_at` | DateTimeField | Auto | Timestamp de creación del registro Django |
| `updated_at` | DateTimeField | Auto | Timestamp de última actualización Django |

Índices compuestos en `Meta`: `(fecha_pago, patente)` y `(patente, fecha_pago)`.

> **Criterio de pago real:** Un pedimento se considera **efectivamente pagado** cuando tiene
> `num_operacion != ''` **Y** `linea_captura != ''`. Solo estos se cuentan en métricas de
> "pagados" en reportes y filtros. El campo `fecha_pago` puede existir sin que ambos estén
> presentes, por lo que no es suficiente por sí solo.

> **Criterio de glosa (pendiente):** Referencias que aparecen en la lista de glosa son las que tienen
> `fir_elec=''`, `num_operacion=''`, `linea_captura=''` y `es_rectificacion=False`
> dentro de la ventana mes actual + mes anterior.

---

### `GlosaRegistro`
Registra cada ciclo de revisión de glosa de una referencia. Una referencia puede tener múltiples registros a lo largo del tiempo.

| Campo | Tipo | Descripción |
|---|---|---|
| `referencia` | FK → Referencia | Referencia glosada |
| `fecha_entrada` | DateTimeField | Cuándo se registró la glosa |
| `usuario_entrada` | FK → User | Quién registró la glosa |
| `fecha_conclusion` | DateTimeField (null) | Cuándo se concluyó (null = en proceso) |
| `usuario_conclusion` | FK → User (null) | Quién concluyó la glosa |
| `nota` | TextField | Observaciones al concluir |
| `urgente` | BooleanField | Marcada como urgente |

**Propiedad:** `concluida` → `True` si `fecha_conclusion is not None`.

**Admin:** Registrado con filtros por patente/usuario/urgente, búsqueda por referencia/nota. Usuarios `is_staff` pueden eliminar registros. No se permite agregar desde el admin.

---

### `CuentaGastos`
Relación OneToOne con Referencia. Se crea cuando la cuenta de gastos es finalizada.

| Campo | Tipo | Descripción |
|---|---|---|
| `referencia` | OneToOne → Referencia | Referencia asociada |
| `nota` | TextField | Observaciones |
| `fecha_finalizacion` | DateTimeField | Cuándo se finalizó |
| `finalizado_por` | FK → User | Quién finalizó |

**Admin:** Registrado con filtros y búsqueda. Solo lectura (no se puede agregar desde admin).

---

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
| `CTRAO_EMBAR` | ~3,000–5,000/patente | Embarques — `FEC_ENTR` (arribo) |
| `SAAIO_PEDIME` | ~12,000–15,000/patente | Pedimentos — `FEC_ENTR`, `FEC_PAGO`, `DIA_PAGO`, `NUM_PEDI` |
| `SAAIO_PEDIME2` | — | Línea de captura SAT (`PAG_LCAP`) y número de operación (`NUM_OPER`) |
| `SAAIO_REGVAL` | — | Validaciones — `FEC_VAL` (fecha_validacion) |
| `SAAIO_PROCES` | — | Procesos — `FEC_MODI` (fecha_captura, última modificación) |
| `SAAIO_CONTEN` | ~12,000–15,000/patente | Contenedores por referencia (`NUM_CONT`, `CVE_CONT`) |
| `SAAIO_GUIAS` | ~12,000–15,000/patente | Guías / BL por referencia (`GUIA`, `IDE_MH`) |
| `SISSEG_USUARI` | ~42/patente | Usuarios del sistema (`LOGIN`, `NOMBRE`) |

> **Importante:** `CTRAO_EMBAR` es tabla activa — los expedientes cerrados se eliminan de ella
> pero permanecen en `SAAIO_PEDIME`. Por eso el import usa `SAAIO_PEDIME` como fuente
> principal y `CTRAO_EMBAR` solo para `fecha_arribo`.
>
> **Fallback:** si una referencia no aparece en `CTRAO_EMBAR`, se usa `fecha_validacion` como proxy de `fecha_arribo`.
>
> **Sincronización incremental:** usa `SAAIO_PROCES.FEC_MODI` para detectar cambios desde el último sync.

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
| `/referencias/<num_refe>/` | `detalle` | Ficha completa + historial glosa + CG |
| `/glosa/` | `glosa` | Lista operativa de pedimentos en glosa |
| `/glosa/dashboard/` | `glosa_dashboard` | Estadísticas y analítica de glosa |
| `/glosa/registrar/<pk>/` | `glosa_registrar` | POST — registrar glosa activa |
| `/glosa/urgente/<pk>/` | `glosa_urgente` | POST — toggle urgente |
| `/glosa/concluir/<pk>/` | `glosa_concluir` | POST — concluir glosa activa con nota |
| `/glosa/revertir/<pk>/` | `glosa_revertir` | POST — eliminar glosa activa (staff only, pk=Referencia) |
| `/glosa/eliminar/<pk>/` | `glosa_eliminar` | POST — eliminar cualquier GlosaRegistro (staff only, pk=GlosaRegistro) |
| `/cuenta-gastos/` | `cuenta_gastos` | Lista operativa de cuenta de gastos |
| `/cuenta-gastos/dashboard/` | `cuenta_gastos_dashboard` | Estadísticas de cuenta de gastos |
| `/cuenta-gastos/finalizar/<pk>/` | `cuenta_gastos_finalizar` | POST — finalizar cuenta de gastos |
| `/login/` | `LoginView` | Pantalla de acceso |
| `/logout/` | `LogoutView` | Cierre de sesión |
| `/admin/` | Admin Django | Gestión de modelos |
| `/api/sync/` | `sync_endpoint` | POST — recibe datos del sync_agent (Token auth) |
| `/whatsapp/webhook/` | `webhook` | POST — recibe mensajes de Twilio (firma Twilio) |

> **Nota técnica:** La URL de detalle usa `<path:num_refe>` (no `<str:>`) porque las
> referencias contienen `/` (ej. `LCLF0331/26`).

> **Staff-only:** `glosa_revertir` y `glosa_eliminar` verifican `request.user.is_staff`
> antes de ejecutar. Si el usuario no es staff, redirigen sin acción.

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

---

## App de Glosa (`referencias/`)

Gestión operativa de pedimentos pendientes de firma electrónica y pago. La lista de glosa muestra referencias con `fir_elec=''`, `num_operacion=''`, `linea_captura=''`, `es_rectificacion=False` en la ventana mes actual + mes anterior.

### Flujo operativo

1. **Registrar** — cualquier usuario autenticado puede registrar una glosa activa para una referencia
2. **Urgente** — toggle para marcar/desmarcar urgencia (fondo rojo en la lista)
3. **Concluir** — cierra la glosa activa con nota opcional y timestamp
4. **Revertir** — elimina la glosa activa (solo staff) desde la lista de glosa
5. **Eliminar** — elimina cualquier GlosaRegistro histórico (solo staff) desde el detalle de referencia

### Módulo de analítica (`referencias/glosa_data.py`)

| Función | Descripción |
|---|---|
| `analyze_notas(glosas_qs)` | Frecuencia de palabras, bigramas y capturistas con más observaciones |
| `get_datos_glosa_semana(inicio, fin)` | Estadísticas completas para el reporte semanal |

`get_datos_glosa_semana` devuelve: `total`, `concluidos`, `en_proceso`, `avg_tiempo_entrada` (arribo→glosa en días), `avg_tiempo_proceso` (entrada→conclusión), `por_usuario`, y todo lo de `analyze_notas`.

---

## App de Cuenta de Gastos (`referencias/`)

Registra la finalización de la cuenta de gastos de cada referencia pagada.

### Módulo de analítica (`referencias/cuenta_gastos_data.py`)

`get_datos_cuenta_gastos_semana(inicio, fin)` devuelve:

| Clave | Descripción |
|---|---|
| `pedimentos_pagados` | Referencias con `fecha_pago` en la semana |
| `finalizadas` | CG con `fecha_finalizacion` en la semana |
| `cg_de_pagadas` | De los pagados esa semana, cuántos ya tienen CG (cualquier momento) |
| `sin_cg` | `pedimentos_pagados - cg_de_pagadas` |
| `pct_cobertura` | `cg_de_pagadas / pedimentos_pagados * 100` |
| `avg_dias_pago_a_cg` | Promedio días entre `fecha_pago` y `fecha_finalizacion` |
| `por_usuario` | Ranking de usuarios por finalizaciones `[{nombre, finalizadas}]` |

---

## App de Reportes (`reportes/`)

Genera y envía reportes HTML por correo usando SendGrid. WhatsApp vía Twilio Content Templates.

### Scheduler

`reportes/scheduler.py` usa `BackgroundScheduler` + `DjangoJobStore`. Arranca automáticamente al iniciar Django. La guardia `RUN_MAIN=true` evita doble arranque en el auto-reloader de desarrollo.

| Job | Trigger | Descripción |
|---|---|---|
| `reporte_semanal` | Lunes 07:00 CST | Reporte de la semana anterior |
| `reporte_mensual` | Día 1 del mes 07:00 CST | Reporte del mes anterior |

### `reportes/data.py` — Funciones de datos

#### `get_datos_semana(inicio, fin)`
Devuelve dict con claves: `validadas_total`, `pagadas_total`, `validadas_por_patente`, `pagadas_por_patente`, `contenedores_total`, `contenedores_por_tipo`, `guias_total`, `top_clientes`, `pendientes_pago`, `rectificaciones_semana`, `claves_pedimento`, `por_capturista`, **`glosa`** (→ `get_datos_glosa_semana`), **`cuenta_gastos`** (→ `get_datos_cuenta_gastos_semana`).

#### `get_datos_mes(year, month)`
Incluye todo lo anterior más: `real`, `proyectado`, `delta_proyectado`, comparativas vs mes anterior y año pasado, `acumulado_año`, `promedio_dias_despacho`.

### `reportes/ai_analysis.py` — Análisis Claude AI

| Función | Modelo | max_tokens | Descripción |
|---|---|---|---|
| `analizar_semanal(datos)` | claude-opus-4-7 | 300 | Párrafo ejecutivo de la semana |
| `analizar_glosa_semanal(datos_glosa)` | claude-opus-4-7 | 500 | 2 párrafos: desempeño equipo + patrones de error por capturista |
| `analizar_cuenta_gastos_semanal(datos_cg)` | claude-opus-4-7 | 300 | Párrafo: cobertura, tiempo respuesta, usuario destacado |
| `analizar_mensual(datos)` | claude-opus-4-7 | 600 | Análisis estratégico con 2-3 recomendaciones |

### Reporte Semanal — Secciones del email (`templates/reportes/semanal.html`)

1. Header con total pagadas de la semana
2. KPI cards: Validadas / Pagadas / Contenedores / Pendientes
3. Validadas por patente (barras de progreso)
4. Contenedores por tipo
5. Top 5 clientes
6. Referencias por usuario (capturadas / pagadas / pendientes)
7. Datos adicionales (Guías BL, Rectificaciones, Pendientes pago)
8. **Análisis ejecutivo IA** — gradiente oscuro azul
9. **Área de Glosa** — KPIs, tiempos, por usuario, patrones de notas
10. **Análisis IA de Glosa** — gradiente verde oscuro
11. **Cuenta de Gastos** — KPIs (pagados/finalizadas/cobertura%), promedio días pago→CG, ranking usuarios
12. **Análisis IA de Cuenta de Gastos** — gradiente violeta oscuro

### WhatsApp — Reporte Semanal

Usa `send_template()` con Content Template aprobado por Meta (`TWILIO_CONTENT_SID_SEMANAL`).

| Variable plantilla | Dato |
|---|---|
| `{{1}}` | Semana (ej. `02/06 – 08/06/2026`) |
| `{{2}}` | Pagadas |
| `{{3}}` | Validadas |
| `{{4}}` | Contenedores |
| `{{5}}` | Pendientes de pago |
| `{{6}}` | Por patente (`LCLF: 12 | LCRR: 8 | LCMJ: 5`) |

---

## Bot de WhatsApp (`whatsapp/`)

Bot conversacional vía Twilio REST API. Responde a mensajes entrantes usando `send_text()` (mensajes de sesión, dentro de ventana de 24 h). Las notificaciones proactivas (reporte semanal) usan `send_template()`.

### Arquitectura

```
WhatsApp ←→ Twilio ──POST /whatsapp/webhook/──→ Django
              (firma Twilio)                        ↓
                                               bot.py procesa
                                                    ↓
                                               Referencia queryset
                                                    ↓
                                          send_text() → Twilio → WA
```

### `whatsapp/client.py`

| Función | Descripción |
|---|---|
| `send_text(to, text)` | Mensaje de sesión (solo dentro de ventana 24 h tras mensaje entrante) |
| `send_template(to, content_sid, variables)` | Mensaje con plantilla aprobada Meta — no requiere sesión previa |
| `send_to_admin(text)` | Atajo: `send_text` al número admin configurado |

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

# Enviar reporte manualmente
python manage.py enviar_reporte semanal
python manage.py enviar_reporte mensual

# Crear usuario adicional
python manage.py createsuperuser

# Shell Django
python manage.py shell
```

---

## Notas de Diseño

- **Tailwind CSS via CDN Play** — sin proceso de build, apto para desarrollo
- **Chart.js 4.4** — gráfica de líneas multi-año con proyección 2026
- **Sidebar responsive** — hamburger drawer en móvil, fijo en desktop
- **Colores por patente:** LCLF → sky-500, LCRR → emerald-500, LCMJ → violet-500
- **Paginación:** 50 registros por página
- **Modales nativos:** `<dialog>` HTML para confirmaciones (revertir/eliminar/concluir glosa)

---

## Agente de Sincronización (`sync_agent/`)

Proceso autónomo para Windows que extrae datos de Firebird local y los empuja al servidor Django en la nube. Corre vía Windows Task Scheduler.

### Modos de ejecución

| Comando | Comportamiento |
|---|---|
| `python sync_agent.py` | **Incremental** — solo refs modificadas desde el último sync |
| `python sync_agent.py --full-sync` | Sync completo de todos los registros |
| `python sync_agent.py --dry-run` | Extrae de Firebird pero no envía a Django |

### Lógica de sincronización incremental

El agente persiste el timestamp del último sync exitoso en `last_sync.json`. En cada ejecución consulta:

1. `SAAIO_PROCES WHERE FEC_MODI >= last_sync` — referencias modificadas
2. `SAAIO_PEDIME WHERE DIA_PAGO >= last_sync` — pedimentos recién pagados

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

### Variables de entorno requeridas en producción

| Variable | Descripción |
|---|---|
| `SECRET_KEY` | Django secret key |
| `DBURL` | URL completa de PostgreSQL (`postgres://...`) |
| `SENDGRID_API_KEY` | API key de SendGrid para envío de reportes por email |
| `ANTHROPIC_API_KEY` | API key de Anthropic para análisis Claude AI |
| `IA_HABILITADA` | `true` para activar análisis IA en reportes |
| `SYNC_SECRET_KEY` | Clave compartida con los agentes de sincronización |
| `TWILIO_ACCOUNT_SID` | Account SID de Twilio |
| `TWILIO_AUTH_TOKEN` | Auth Token de Twilio |
| `TWILIO_WHATSAPP_FROM` | Número Twilio remitente (`+14155238886` sandbox o número aprobado) |
| `TWILIO_WHATSAPP_TO_ADMIN` | Número WhatsApp del administrador (E.164) |
| `TWILIO_WHATSAPP_ALLOWED_NUMBERS` | Números autorizados para el bot (separados por coma) |
| `TWILIO_CONTENT_SID_SEMANAL` | SID de la plantilla aprobada para reporte semanal (`HXebb6fc66f8fd89388a6800411a53e9b8`) |
| `DEBUG` | `False` en producción |
| `ALLOWED_HOSTS` | Dominio asignado por DO |

> **Nota SSL:** DO App Platform termina SSL en el load balancer y reenvía HTTP internamente.
> `SECURE_SSL_REDIRECT` debe ser `False`. Se usa `SECURE_PROXY_SSL_HEADER` con `X-Forwarded-Proto`.
