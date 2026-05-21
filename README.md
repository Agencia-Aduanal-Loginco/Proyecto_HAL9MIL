# HAL9MIL

Sistema de consulta y estadísticas aduanales para agencia aduanal, construido con Django. Permite visualizar, filtrar y analizar pedimentos de importación de las tres patentes de la agencia, con reportes periódicos automatizados y análisis ejecutivo con IA.

---

## Características principales

- **Dashboard interactivo** con KPIs históricos 2022–2026, proyecciones y comparativas mensuales
- **Consulta de referencias** con búsqueda full-text por referencia, pedimento, cliente, contenedor o BL
- **Filtros avanzados** por patente, año, mes, clave de pedimento y estado de pago
- **Reportes semanales y mensuales** enviados por correo con análisis ejecutivo generado por IA
- **Sincronización automática** desde servidores Windows locales hacia la nube
- **Métricas operacionales** por patente, capturista y cliente

---

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| Backend | Django 5.2+ |
| Frontend | Tailwind CSS + Chart.js 4.4 |
| Autenticación | Django session auth |
| Reportes por email | SendGrid |
| Análisis IA | Claude AI (Anthropic) |
| Servidor de producción | Gunicorn + WhiteNoise |
| Python | 3.12 |

---

## Estructura del proyecto

```
Proyecto_HAL9MIL/
├── hal9mil/                    # Configuración Django
├── referencias/                # App principal — modelos, vistas, ETL
│   └── management/commands/
│       └── import_firebird.py  # Importación desde base de datos de origen
├── reportes/                   # Reportes periódicos y análisis IA
│   ├── data.py                 # Cálculo de métricas
│   ├── ai_analysis.py          # Análisis ejecutivo con Claude AI
│   └── management/commands/
│       └── enviar_reporte.py   # Envío manual de reportes
├── sync_agent/                 # Agente de sincronización Windows → Nube
├── templates/                  # HTML (base, dashboard, lista, detalle, reportes)
├── static/
├── requirements.txt
└── manage.py
```

---

## Instalación local

### Requisitos previos

- Python 3.12
- Acceso a la fuente de datos de origen

### Pasos

```bash
# 1. Clonar el repositorio
git clone <url-del-repo>
cd Proyecto_HAL9MIL

# 2. Crear y activar entorno virtual
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Editar .env con los valores correspondientes

# 5. Aplicar migraciones
python manage.py migrate

# 6. Crear superusuario
python manage.py createsuperuser

# 7. Levantar servidor de desarrollo
python manage.py runserver 8001
```

---

## Variables de entorno

Crear un archivo `.env` en la raíz del proyecto con las siguientes variables (ver `.env.example` si existe):

| Variable | Descripción |
|---|---|
| `SECRET_KEY` | Django secret key |
| `DEBUG` | `True` para desarrollo, `False` para producción |
| `ALLOWED_HOSTS` | Hosts permitidos (ej. `localhost,127.0.0.1`) |
| `DBURL` | URL de conexión a la base de datos |
| `SENDGRID_API_KEY` | API key de SendGrid para envío de reportes |
| `ANTHROPIC_API_KEY` | API key de Anthropic para análisis con IA |
| `SYNC_SECRET_KEY` | Clave compartida con los agentes de sincronización |

---

## Importación de datos

```bash
# Importar histórico completo
python manage.py import_firebird

# Solo previsualizar sin escribir
python manage.py import_firebird --dry-run

# Solo una patente
python manage.py import_firebird --patentes 

# Partes individuales
python manage.py import_firebird --solo-referencias
python manage.py import_firebird --solo-contenedores
python manage.py import_firebird --solo-bls
```

---

## Reportes

Los reportes se generan y envían automáticamente según el calendario configurado, o manualmente:

```bash
# Enviar reporte semanal
python manage.py enviar_reporte --tipo semanal

# Enviar reporte mensual
python manage.py enviar_reporte --tipo mensual
```

---

## Agente de sincronización

El directorio `sync_agent/` contiene un script autónomo para servidores Windows que extrae datos de la fuente local y los empuja al servidor Django en la nube. Ver `sync_agent/instalar_windows.md` para instrucciones de despliegue.

---

## Despliegue en producción

La aplicación está configurada para desplegarse en **Digital Ocean App Platform**. El archivo `.do/app.yaml` contiene la configuración de la plataforma. Todos los secretos deben configurarse como variables de entorno en el dashboard de Digital Ocean, **nunca en el archivo de configuración**.

---

## URLs principales

| URL | Descripción |
|---|---|
| `/` | Dashboard con KPIs y gráficas históricas |
| `/referencias/` | Listado con búsqueda y filtros |
| `/referencias/<num_refe>/` | Ficha completa de una referencia |
| `/admin/` | Panel de administración Django |

---

## Licencia

Uso interno — Loginco.
