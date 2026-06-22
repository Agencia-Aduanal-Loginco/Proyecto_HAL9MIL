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
            f"Eres un experto analista de operaciones de Loginco, agencia aduanal mexicana. "
            f"Redacta un análisis ejecutivo completo sobre la semana "
            f"del {datos['periodo_inicio'].strftime('%d/%m/%Y')} al {datos['periodo_fin'].strftime('%d/%m/%Y')}. "
            f"Sé directo y orientado a la dirección. Datos:\n"
            f"- Validadas: {datos['validadas_total']} ({por_patente_str})\n"
            f"- Pagadas/liberadas: {datos['pagadas_total']}\n"
            f"- Contenedores procesados: {datos['contenedores_total']}\n"
            f"- Guías BL: {datos['guias_total']}\n"
            f"- Pendientes de pago (acumulado): {datos['pendientes_pago']}\n"
            f"- Rectificaciones: {datos['rectificaciones_semana']}\n"
            f"Destaca anomalías o tendencias importantes y termina con recomendaciones concretas para la dirección."
        )
        msg = _client().messages.create(
            model='claude-opus-4-7',
            max_tokens=1024,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return msg.content[0].text
    except Exception as e:
        logger.error(f'Error en análisis IA semanal: {e}')
        return ''


def analizar_glosa_semanal(datos_glosa: dict) -> str:
    if not settings.IA_HABILITADA or not settings.ANTHROPIC_API_KEY:
        return ''
    if not datos_glosa or datos_glosa.get('total', 0) == 0:
        return ''
    try:
        notas_muestra = datos_glosa.get('notas_muestra', [])[:15]
        notas_str = '\n'.join(
            f"  [{n['capturista']}] {n['nota'][:130]}"
            for n in notas_muestra
        ) if notas_muestra else '(sin notas registradas esta semana)'

        top_palabras_str = ', '.join(
            f"{w}({c})" for w, c in datos_glosa.get('top_palabras', [])[:12]
        ) or '(sin datos)'

        top_bigramas_str = ', '.join(
            f'"{bg}"({c})' for bg, c in datos_glosa.get('top_bigramas', [])[:8]
        ) or '(sin datos)'

        top_caps_str = ', '.join(
            f"{cap}({c} obs.)" for cap, c in datos_glosa.get('top_capturistas_notas', [])[:6]
        ) or '(sin datos)'

        por_usuario_str = '\n'.join(
            f"  {u['nombre']}: {u['registradas']} registradas, {u['concluidas']} concluidas, {u['pendientes']} pendientes"
            for u in datos_glosa.get('por_usuario', [])
        ) or '(sin datos)'

        prompt = (
            "Eres analista de operaciones de Loginco, agencia aduanal mexicana. "
            "Analiza el área de glosa para la dirección general. "
            "Redacta un análisis ejecutivo detallado con los siguientes apartados: "
            "desempeño operativo del equipo, tipos de error detectados en las notas, "
            "patrones por capturista que requieran atención, y recomendaciones concretas de mejora. "
            "Sé directo, concreto y enfocado en acciones. Español formal.\n\n"
            f"ESTADÍSTICAS DE GLOSA (semana {datos_glosa.get('periodo_inicio', '')} – {datos_glosa.get('periodo_fin', '')}):\n"
            f"- Total pedimentos glosados: {datos_glosa['total']}\n"
            f"- Concluidos: {datos_glosa['concluidos']} | En proceso: {datos_glosa['en_proceso']}\n"
            f"- Tiempo prom. arribo→ingreso a glosa: {datos_glosa.get('avg_tiempo_entrada') or 'N/D'} días\n"
            f"- Tiempo prom. procesamiento: {datos_glosa.get('avg_tiempo_proceso') or 'N/D'} días\n"
            f"- Registros con observaciones/notas: {datos_glosa.get('notas_count', 0)}\n\n"
            f"POR USUARIO:\n{por_usuario_str}\n\n"
            f"PALABRAS CLAVE EN NOTAS: {top_palabras_str}\n"
            f"FRASES FRECUENTES: {top_bigramas_str}\n"
            f"CAPTURISTAS CON MÁS OBSERVACIONES: {top_caps_str}\n\n"
            f"MUESTRA DE NOTAS (capturista — texto):\n{notas_str}"
        )
        msg = _client().messages.create(
            model='claude-opus-4-7',
            max_tokens=1024,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return msg.content[0].text
    except Exception as e:
        logger.error(f'Error en análisis IA glosa semanal: {e}')
        return ''


def analizar_cuenta_gastos_semanal(datos_cg: dict) -> str:
    if not settings.IA_HABILITADA or not settings.ANTHROPIC_API_KEY:
        return ''
    if not datos_cg or datos_cg.get('pedimentos_pagados', 0) == 0:
        return ''
    try:
        por_usuario_str = '\n'.join(
            f"  {u['nombre']}: {u['finalizadas']} finalizadas"
            for u in datos_cg.get('por_usuario', [])
        ) or '(sin datos)'

        prompt = (
            "Eres analista de operaciones de Loginco, agencia aduanal mexicana. "
            "Redacta un análisis ejecutivo completo sobre el desempeño "
            "del área de Cuenta de Gastos para la dirección. "
            "Incluye: cobertura alcanzada, tiempo de respuesta, quién destacó positiva o negativamente, "
            "y recomendaciones concretas si hay áreas de mejora. "
            "Español formal y directo.\n\n"
            f"CUENTA DE GASTOS (semana {datos_cg.get('periodo_inicio','')} – {datos_cg.get('periodo_fin','')}):\n"
            f"- Pedimentos pagados en la semana: {datos_cg['pedimentos_pagados']}\n"
            f"- Cuentas de gastos finalizadas en la semana: {datos_cg['finalizadas']}\n"
            f"- De los pagados esta semana, con CG registrada: {datos_cg['cg_de_pagadas']} ({datos_cg['pct_cobertura']}%)\n"
            f"- Promedio días pago → finalización CG: {datos_cg.get('avg_dias_pago_a_cg') or 'N/D'} días\n\n"
            f"POR USUARIO:\n{por_usuario_str}"
        )
        msg = _client().messages.create(
            model='claude-opus-4-7',
            max_tokens=1024,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return msg.content[0].text
    except Exception as e:
        logger.error(f'Error en análisis IA cuenta gastos semanal: {e}')
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
            f"Redacta un análisis ejecutivo completo para la dirección general sobre {nombre_mes} {year}. "
            f"Incluye: evaluación detallada del mes, comparativa con períodos anteriores, "
            f"tendencias identificadas, clientes o patentes destacadas, y recomendaciones estratégicas concretas. "
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
            max_tokens=1500,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return msg.content[0].text
    except Exception as e:
        logger.error(f'Error en análisis IA mensual: {e}')
        return ''
