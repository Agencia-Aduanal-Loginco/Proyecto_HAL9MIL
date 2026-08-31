import json
import logging
from datetime import date, timedelta

from django.db import IntegrityError

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
    return list(set(
        Destinatario.objects
        .filter(activo=True, **{campo: True})
        .exclude(whatsapp='')
        .values_list('whatsapp', flat=True)
    ))


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


def _wa_ia_modulo(texto: str, modulo: str, semana_str: str):
    """Envía la interpretación IA de un módulo a sus destinatarios configurados."""
    if not texto:
        return
    try:
        from whatsapp.client import send_template, clean_wa_var
        campo = f'recibe_wa_ia_{modulo}'
        numeros = list(set(
            Destinatario.objects
            .filter(activo=True, **{campo: True})
            .exclude(whatsapp='')
            .values_list('whatsapp', flat=True)
        ))
        if not numeros:
            return
        content_sid = settings.TWILIO_CONTENT_SID_IA_HAL9MIL
        if not content_sid:
            logger.warning("TWILIO_CONTENT_SID_IA_HAL9MIL no configurado — IA %s no enviado.", modulo)
            return
        # El texto viene de Claude con párrafos y viñetas separados por '\n'.
        # WhatsApp NO admite saltos de línea / tabs / 4+ espacios en el valor de
        # una variable de plantilla (Twilio error 21656), así que lo aplanamos
        # ANTES de truncar para que _MAX refleje los caracteres visibles reales.
        texto_plano = clean_wa_var(texto)
        # Twilio limita cada variable a 400 chars; reservamos espacio para el pie.
        _PIE = ' Para más detalle consulta tu correo.'
        _MAX = 400 - len(_PIE) - 1  # 1 para el '…'
        resumen = texto_plano[:_MAX].strip() + ('…' if len(texto_plano) > _MAX else '')
        texto_enviado = resumen + _PIE
        variables = {'1': texto_enviado}
        for numero in numeros:
            send_template(numero, content_sid, variables)
        logger.info("[WA IA %s] Enviado a %d números — semana %s.", modulo, len(numeros), semana_str)
    except Exception as e:
        logger.warning("WhatsApp IA %s no enviado: %s", modulo, e)


def _wa_mensual(datos: dict):
    try:
        from whatsapp.client import send_template
        numeros = _get_wa_destinatarios('mensual')
        if not numeros:
            return
        content_sid = settings.TWILIO_CONTENT_SID_MENSUAL
        por_patente = {r['prefijo']: r['total'] for r in datos['por_patente']}
        patentes_str = ' | '.join(
            f"{p}: {por_patente.get(p, 0)}"
            for p in ['LCLF', 'LCRR', 'LCMJ']
        )
        prom = datos['promedio_dias_despacho']
        variables = {
            '1': f"{datos['nombre_mes']} {datos['year']}",
            '2': f"{datos['real']} | Proyectado: {datos['proyectado']} ({datos['pct_proyectado']}%)",
            '3': f"vs {datos['nombre_mes_anterior']}: {datos['delta_mes_anterior']:+d}",
            '4': f"vs {datos['nombre_mes']} {datos['prev_year']}: {datos['delta_año_pasado']:+d}",
            '5': f"{prom} días" if prom else 'N/D',
            '6': patentes_str,
        }
        for numero in numeros:
            send_template(numero, content_sid, variables)
        logger.info("[WA Mensual] Enviado a %d números vía plantilla.", len(numeros))
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

    # Guard de idempotencia: si otra instancia ya completó este período, no reenviar
    if HistorialReporte.objects.filter(tipo='semanal', periodo_inicio=last_monday, exitoso=True).exists():
        logger.info('[Semanal] Reporte ya enviado para este período — omitiendo duplicado.')
        return

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

        # Registrar ANTES de enviar WA — la constraint única en DB garantiza que solo
        # una instancia proceda al envío WA cuando varias arrancan simultáneamente.
        try:
            _guardar_historial('semanal', last_monday, last_sunday, destinatarios, True)
        except IntegrityError:
            logger.info('[Semanal] Historial ya registrado por otra instancia — omitiendo envío WA.')
            return

        logger.info(f'[Semanal] Enviado a {len(destinatarios)} destinatarios.')
        _wa_semanal(datos, semana_str)
        _wa_ia_modulo(analisis_ia,       'referencias',   semana_str)
        _wa_ia_modulo(analisis_glosa_ia, 'glosa',         semana_str)
        _wa_ia_modulo(analisis_cg_ia,    'cuenta_gastos', semana_str)

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

    # Guard de idempotencia: si otra instancia ya completó este período, no reenviar
    if HistorialReporte.objects.filter(tipo='mensual', periodo_inicio=inicio, exitoso=True).exists():
        logger.info('[Mensual] Reporte ya enviado para este período — omitiendo duplicado.')
        return

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

        # Registrar ANTES de enviar WA — la constraint única en DB garantiza que solo
        # una instancia proceda al envío WA cuando varias arrancan simultáneamente.
        try:
            _guardar_historial('mensual', inicio, fin, destinatarios, True)
        except IntegrityError:
            logger.info('[Mensual] Historial ya registrado por otra instancia — omitiendo envío WA.')
            return

        logger.info(f'[Mensual] Enviado a {len(destinatarios)} destinatarios.')
        _wa_mensual(datos)
        _wa_ia_modulo(analisis_ia, 'referencias', f'{nombre_mes} {year}')

    except Exception as e:
        logger.error(f'[Mensual] Error: {e}')
        _guardar_historial('mensual', inicio, fin, destinatarios, False, str(e))
        raise
