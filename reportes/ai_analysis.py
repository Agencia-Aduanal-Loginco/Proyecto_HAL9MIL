import logging
from django.conf import settings

logger = logging.getLogger(__name__)

NOMBRES_MESES = ['Enero','Febrero','Marzo','Abril','Mayo','Junio',
                 'Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre']


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def analizar_semanal(datos: dict) -> str:
    if not settings.IA_HABILITADA or not settings.ANTHROPIC_API_KEY:
        return ''
    try:
        por_patente_str = ', '.join(
            f"{p['prefijo']} {p['total']}" for p in datos['validadas_por_patente']
        )
        prompt = (
            f"Eres analista de operaciones de Loginco, agencia aduanal mexicana. "
            f"Redacta un párrafo ejecutivo breve (máximo 100 palabras) sobre la semana "
            f"del {datos['periodo_inicio'].strftime('%d/%m/%Y')} al {datos['periodo_fin'].strftime('%d/%m/%Y')}. "
            f"Sé directo y orientado a la dirección. Datos:\n"
            f"- Validadas: {datos['validadas_total']} ({por_patente_str})\n"
            f"- Pagadas/liberadas: {datos['pagadas_total']}\n"
            f"- Contenedores procesados: {datos['contenedores_total']}\n"
            f"- Guías BL: {datos['guias_total']}\n"
            f"- Pendientes de pago (acumulado): {datos['pendientes_pago']}\n"
            f"- Rectificaciones: {datos['rectificaciones_semana']}\n"
            f"Destaca anomalías o tendencias y, si aplica, una recomendación concreta."
        )
        msg = _client().messages.create(
            model='claude-opus-4-7',
            max_tokens=300,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return msg.content[0].text
    except Exception as e:
        logger.error(f'Error en análisis IA semanal: {e}')
        return ''


def analizar_mensual(datos: dict) -> str:
    if not settings.IA_HABILITADA or not settings.ANTHROPIC_API_KEY:
        return ''
    try:
        nombre_mes = datos['nombre_mes']
        year = datos['year']
        nombre_mes_ant = NOMBRES_MESES[datos['prev_month'] - 1]

        por_patente_str = ', '.join(
            f"{p['prefijo']} {p['total']}" for p in datos['por_patente']
        )
        top_clientes_str = ', '.join(
            f"{c['nombre_cliente']} ({c['total']})" for c in datos['top_clientes'][:4]
        )

        prompt = (
            f"Eres analista estratégico de Loginco, agencia aduanal mexicana. "
            f"Redacta un análisis ejecutivo (máximo 200 palabras) para la dirección general sobre {nombre_mes} {year}. "
            f"Incluye evaluación del mes, tendencias identificadas y 2-3 recomendaciones estratégicas concretas. "
            f"Español formal y directo.\n\n"
            f"DESEMPEÑO {nombre_mes.upper()} {year}:\n"
            f"- Real: {datos['real']} pedimentos | Proyectado: {datos['proyectado']} | "
            f"Diferencia: {datos['delta_proyectado']:+d} ({datos['pct_proyectado']}% del objetivo)\n\n"
            f"COMPARATIVA:\n"
            f"- vs {nombre_mes_ant} {datos['prev_year_of_prev_month']}: "
            f"{datos['real_mes_anterior']} → {datos['real']} ({datos['delta_mes_anterior']:+d}, {datos['pct_mes_anterior']}%)\n"
            f"- vs {nombre_mes} {datos['prev_year']}: "
            f"{datos['real_año_pasado']} → {datos['real']} ({datos['delta_año_pasado']:+d}, {datos['pct_año_pasado']}%)\n\n"
            f"OPERACIONES:\n"
            f"- Contenedores: {datos['contenedores_total']} | Guías BL: {datos['guias_total']}\n"
            f"- Validadas: {datos['validadas_mes']} | Pendientes pago: {datos['pendientes_pago']}\n"
            f"- Rectificaciones: {datos['rectificaciones_mes']}\n"
            f"- Por patente: {por_patente_str}\n"
            f"- Top clientes: {top_clientes_str}"
        )
        msg = _client().messages.create(
            model='claude-opus-4-7',
            max_tokens=600,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return msg.content[0].text
    except Exception as e:
        logger.error(f'Error en análisis IA mensual: {e}')
        return ''
