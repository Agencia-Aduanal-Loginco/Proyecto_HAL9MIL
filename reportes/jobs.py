import json
import logging
from datetime import date, timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from .models import Destinatario, HistorialReporte
from .data import get_datos_semana, get_datos_mes, NOMBRES_MESES
from .ai_analysis import analizar_semanal, analizar_mensual

logger = logging.getLogger(__name__)

DESTINATARIOS_BASE = ['xoyocl2@gmail.com', 'f.suarez@loginco.com.mx']


def _get_destinatarios(tipo: str) -> list:
    campo = f'recibe_{tipo}'
    db_emails = list(
        Destinatario.objects
        .filter(activo=True, **{campo: True})
        .values_list('email', flat=True)
    )
    return list(set(DESTINATARIOS_BASE + db_emails))


def _guardar_historial(tipo, inicio, fin, destinatarios, exitoso, error=''):
    HistorialReporte.objects.create(
        tipo=tipo,
        periodo_inicio=inicio,
        periodo_fin=fin,
        destinatarios=json.dumps(destinatarios),
        exitoso=exitoso,
        error=error,
    )


def enviar_reporte_semanal():
    today = date.today()
    # Semana anterior: lunes a domingo
    days_since_monday = today.weekday()
    last_monday = today - timedelta(days=days_since_monday + 7)
    last_sunday = last_monday + timedelta(days=6)

    logger.info(f'[Semanal] Generando reporte {last_monday} – {last_sunday}')
    destinatarios = _get_destinatarios('semanal')

    try:
        datos = get_datos_semana(last_monday, last_sunday)
        analisis_ia = analizar_semanal(datos)
        semana_str = f'{last_monday.strftime("%d/%m")} – {last_sunday.strftime("%d/%m/%Y")}'

        html = render_to_string('reportes/semanal.html', {
            'datos': datos,
            'analisis_ia': analisis_ia,
            'semana_str': semana_str,
        })

        msg = EmailMultiAlternatives(
            subject=f'HAL9MIL · Reporte Semanal {semana_str}',
            body='Este correo requiere un cliente con soporte HTML.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=destinatarios,
        )
        msg.attach_alternative(html, 'text/html')
        msg.send()

        _guardar_historial('semanal', last_monday, last_sunday, destinatarios, True)
        logger.info(f'[Semanal] Enviado a {len(destinatarios)} destinatarios.')

    except Exception as e:
        logger.error(f'[Semanal] Error: {e}')
        _guardar_historial('semanal', last_monday, last_sunday, destinatarios, False, str(e))
        raise


def enviar_reporte_mensual():
    today = date.today()
    # Cubre el mes anterior
    mes = 12 if today.month == 1 else today.month - 1
    year = today.year - 1 if today.month == 1 else today.year

    import calendar
    inicio = date(year, mes, 1)
    fin = date(year, mes, calendar.monthrange(year, mes)[1])
    nombre_mes = NOMBRES_MESES[mes - 1]

    logger.info(f'[Mensual] Generando reporte {nombre_mes} {year}')
    destinatarios = _get_destinatarios('mensual')

    try:
        datos = get_datos_mes(year, mes)
        analisis_ia = analizar_mensual(datos)

        html = render_to_string('reportes/mensual.html', {
            'datos': datos,
            'analisis_ia': analisis_ia,
            'nombre_mes': nombre_mes,
            'nombres_meses': NOMBRES_MESES,
        })

        msg = EmailMultiAlternatives(
            subject=f'HAL9MIL · Reporte Mensual {nombre_mes} {year}',
            body='Este correo requiere un cliente con soporte HTML.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=destinatarios,
        )
        msg.attach_alternative(html, 'text/html')
        msg.send()

        _guardar_historial('mensual', inicio, fin, destinatarios, True)
        logger.info(f'[Mensual] Enviado a {len(destinatarios)} destinatarios.')

    except Exception as e:
        logger.error(f'[Mensual] Error: {e}')
        _guardar_historial('mensual', inicio, fin, destinatarios, False, str(e))
        raise
