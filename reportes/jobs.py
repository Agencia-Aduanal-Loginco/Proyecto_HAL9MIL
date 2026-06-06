import json
import logging
from datetime import date, timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from .models import Destinatario, HistorialReporte
from .data import get_datos_semana, get_datos_mes, NOMBRES_MESES
from .ai_analysis import analizar_semanal, analizar_mensual, analizar_glosa_semanal, analizar_cuenta_gastos_semanal

logger = logging.getLogger(__name__)

DESTINATARIOS_BASE = ['xoyocl2@gmail.com', 'f.suarez@loginco.com.mx']


def _get_wa_destinatarios(tipo: str) -> list:
    campo = f'recibe_wa_{tipo}'
    return list(
        Destinatario.objects
        .filter(activo=True, **{campo: True})
        .exclude(whatsapp='')
        .values_list('whatsapp', flat=True)
    )


def _wa_semanal(datos: dict, semana_str: str):
    try:
        from django.conf import settings
        from whatsapp.client import send_template
        numeros = _get_wa_destinatarios('semanal')
        if not numeros:
            return
        content_sid = settings.TWILIO_CONTENT_SID_SEMANAL
        por_patente = {r['prefijo']: r['total'] for r in datos['pagadas_por_patente']}
        patentes_str = ' | '.join(
            f"{p}: {por_patente.get(p, 0)}"
            for p in ['LCLF', 'LCRR', 'LCMJ']
        )
        variables = {
            '1': semana_str,
            '2': str(datos['pagadas_total']),
            '3': str(datos['validadas_total']),
            '4': str(datos['contenedores_total']),
            '5': str(datos['pendientes_pago']),
            '6': patentes_str,
        }
        for numero in numeros:
            send_template(numero, content_sid, variables)
        logger.info("[WA Semanal] Enviado a %d números vía plantilla.", len(numeros))
    except Exception as e:
        logger.warning("WhatsApp semanal no enviado: %s", e)


def _wa_mensual(datos: dict):
    try:
        from whatsapp.client import send_text
        numeros = _get_wa_destinatarios('mensual')
        if not numeros:
            return
        pct = datos['pct_proyectado']
        icono_proy = '✅' if pct >= 95 else '⚠️' if pct >= 80 else '🔴'
        delta_ant = datos['delta_mes_anterior']
        icono_ant = '↗' if delta_ant >= 0 else '↘'
        delta_año = datos['delta_año_pasado']
        icono_año = '↗' if delta_año >= 0 else '↘'
        por_patente = {r['prefijo']: r['total'] for r in datos['por_patente']}
        patentes = '  ' + ' | '.join(
            f"{p}: {por_patente.get(p, 0)}"
            for p in ['LCLF', 'LCRR', 'LCMJ']
        )
        prom = f"\n⏱ Promedio despacho: {datos['promedio_dias_despacho']} días" if datos['promedio_dias_despacho'] else ''
        texto = (
            f"📅 *{datos['nombre_mes']} {datos['year']}* — HAL9MIL\n\n"
            f"{icono_proy} Real: *{datos['real']}* | Proyectado: {datos['proyectado']} ({pct}%)\n"
            f"{icono_ant} vs {datos['nombre_mes_anterior']}:   {delta_ant:+d}\n"
            f"{icono_año} vs {datos['nombre_mes']} {datos['prev_year']}: {delta_año:+d}"
            f"{prom}\n\n"
            f"Por patente:\n{patentes}"
        )
        for numero in numeros:
            send_text(numero, texto)
        logger.info("[WA Mensual] Enviado a %d números.", len(numeros))
    except Exception as e:
        logger.warning("WhatsApp mensual no enviado: %s", e)


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
        analisis_glosa_ia = analizar_glosa_semanal(datos.get('glosa', {}))
        analisis_cg_ia = analizar_cuenta_gastos_semanal(datos.get('cuenta_gastos', {}))
        semana_str = f'{last_monday.strftime("%d/%m")} – {last_sunday.strftime("%d/%m/%Y")}'

        html = render_to_string('reportes/semanal.html', {
            'datos': datos,
            'analisis_ia': analisis_ia,
            'analisis_glosa_ia': analisis_glosa_ia,
            'analisis_cg_ia': analisis_cg_ia,
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
        _wa_semanal(datos, semana_str)

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
        _wa_mensual(datos)

    except Exception as e:
        logger.error(f'[Mensual] Error: {e}')
        _guardar_historial('mensual', inicio, fin, destinatarios, False, str(e))
        raise
