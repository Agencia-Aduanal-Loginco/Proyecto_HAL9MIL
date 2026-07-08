import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from django_apscheduler.jobstores import DjangoJobStore

logger = logging.getLogger(__name__)


_SKIP_COMMANDS = {
    'migrate', 'makemigrations', 'collectstatic', 'shell',
    'test', 'createsuperuser', 'check', 'showmigrations',
    'dbshell', 'dumpdata', 'loaddata',
}


def start():
    import sys
    # Saltar en comandos de gestión que no necesitan el scheduler
    if len(sys.argv) > 1 and sys.argv[1] in _SKIP_COMMANDS:
        return
    # Evitar doble arranque en el auto-reloader de Django (desarrollo)
    if os.environ.get('RUN_MAIN') == 'true':
        return

    from .jobs import enviar_reporte_semanal, enviar_reporte_mensual
    from finanzas.comisiones import calcular_comisiones_mes_actual

    scheduler = BackgroundScheduler(timezone='America/Mexico_City')
    scheduler.add_jobstore(DjangoJobStore(), 'default')

    scheduler.add_job(
        enviar_reporte_semanal,
        trigger='cron',
        day_of_week='mon',
        hour=7,
        minute=0,
        id='reporte_semanal',
        replace_existing=True,
        jobstore='default',
    )

    scheduler.add_job(
        enviar_reporte_mensual,
        trigger='cron',
        day=1,
        hour=7,
        minute=0,
        id='reporte_mensual',
        replace_existing=True,
        jobstore='default',
    )

    scheduler.add_job(
        calcular_comisiones_mes_actual,
        trigger='cron',
        day=1,
        hour=6,
        minute=0,
        id='calcular_comisiones',
        replace_existing=True,
        jobstore='default',
    )

    try:
        scheduler.start()
        logger.info('APScheduler iniciado: semanal (lun 7am) + mensual (día 1, 7am) + comisiones (día 1, 6am).')
    except Exception as e:
        logger.error(f'Error al iniciar APScheduler: {e}')
