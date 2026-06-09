from pathlib import Path
from dotenv import load_dotenv
import os
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-hal9mil-change-in-production-xoyoc-loginco-2026')

DEBUG = os.getenv('DEBUG', 'True').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '*').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_apscheduler',
    'referencias',
    'reportes',
    'whatsapp',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'hal9mil.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'hal9mil.wsgi.application'

# ── Base de datos ─────────────────────────────────────────────────────────────
# En producción se usa DBURL (PostgreSQL en DigitalOcean).
# En desarrollo local, si no hay DBURL, cae a SQLite.
DATABASES = {
    'default': dj_database_url.config(
        env='DBURL',
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}',
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
]

LANGUAGE_CODE = 'es-mx'
TIME_ZONE = 'America/Mexico_City'
USE_I18N = True
USE_TZ = True

# ── Archivos estáticos ────────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# ── Email (SendGrid SMTP) ─────────────────────────────────────────────────────
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.sendgrid.net'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'apikey'
EMAIL_HOST_PASSWORD = os.getenv('SENDGRID_API_KEY', '')
DEFAULT_FROM_EMAIL = os.getenv('FROM_EMAIL', 'noreply@loginco.com.mx')

# ── Inteligencia Artificial (Anthropic Claude) ────────────────────────────────
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
IA_HABILITADA = os.getenv('IA_HABILITADA', 'False').lower() in ('true', '1', 'yes')
IA_SCORE_MINIMO_CLAUDE = os.getenv('IA_SCORE_MINIMO_CLAUDE', 'ALTO')

# ── APScheduler ───────────────────────────────────────────────────────────────
APSCHEDULER_DATETIME_FORMAT = 'N j, Y, f:s a'
APSCHEDULER_RUN_NOW_TIMEOUT = 25

# ── Sync Agent ────────────────────────────────────────────────────────────────
# Token compartido con el agente local en Windows (sync_agent/.env)
SYNC_SECRET_KEY = os.getenv('SYNC_SECRET_KEY', '')

# ── WhatsApp (Twilio) ─────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID        = os.getenv('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN         = os.getenv('TWILIO_AUTH_TOKEN', '')
# Número Twilio remitente: whatsapp:+14155238886 (sandbox) o número aprobado
TWILIO_WHATSAPP_FROM      = os.getenv('TWILIO_WHATSAPP_FROM', '')
# Número administrador que recibe notificaciones proactivas
TWILIO_WHATSAPP_TO_ADMIN  = os.getenv('TWILIO_WHATSAPP_TO_ADMIN', '')
# Números autorizados para usar el bot (vacío = todos)
TWILIO_WHATSAPP_ALLOWED_NUMBERS = [
    n.strip() for n in os.getenv('TWILIO_WHATSAPP_ALLOWED_NUMBERS', '').split(',') if n.strip()
]
# Content SIDs de las plantillas aprobadas por Meta
TWILIO_CONTENT_SID_SEMANAL = os.getenv('TWILIO_CONTENT_SID_SEMANAL', 'HXebb6fc66f8fd89388a6800411a53e9b8')
TWILIO_CONTENT_SID_MENSUAL = os.getenv('TWILIO_CONTENT_SID_MENSUAL', 'HX1607f5f787da17354ce04e7f8f9528af')

# ── Seguridad HTTPS (solo en producción) ──────────────────────────────────────
# App Platform termina SSL en el load balancer y pasa HTTP internamente.
if not DEBUG:
    SECURE_PROXY_SSL_HEADER    = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE      = True
    CSRF_COOKIE_SECURE         = True
    SECURE_HSTS_SECONDS        = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# Seguridad
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
_csrf_raw = os.getenv('CSRF_TRUSTED_ORIGINS')
CSRF_TRUSTED_ORIGINS = _csrf_raw.split(',') if _csrf_raw else ['https://*.ondigitalocean.app']