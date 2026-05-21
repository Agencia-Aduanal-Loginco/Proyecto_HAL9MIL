import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from django_apscheduler.jobstores import DjangoJobStore

logger = logging.getLogger(__name__)


def start():
    import sys
    # Solo iniciar cuando corre el servidor web, no en comandos manage.py
    if len(sys.argv) > 1 and sys.argv[1] != 'runserver':
        return
    # Evitar doble arranque en el auto-reloader de Django
    if os.environ.get('RUN_MAIN') == 'true':
        return

    from .jobs import enviar_reporte_semanal, enviar_reporte_mensual

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

    try:
        scheduler.start()
        logger.info('APScheduler iniciado: semanal (lun 7am) + mensual (día 1, 7am).')
    except Exception as e:
        logger.error(f'Error al iniciar APScheduler: {e}')
