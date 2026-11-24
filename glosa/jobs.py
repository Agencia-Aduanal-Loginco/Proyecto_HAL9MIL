"""Tareas programadas de la app glosa (APScheduler, ver reportes/scheduler.py)."""
import logging

logger = logging.getLogger(__name__)


def reprocesar_documentos_atorados():
    from .services_procesamiento import reprocesar_atorados
    try:
        n = reprocesar_atorados()
        if n:
            logger.warning('Glosa: %s documento(s) atorado(s) reprocesado(s).', n)
    except Exception:
        logger.exception('Glosa: error reprocesando documentos atorados')
