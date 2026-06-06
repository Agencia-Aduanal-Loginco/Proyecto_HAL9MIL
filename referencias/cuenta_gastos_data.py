from datetime import date

from referencias.models import CuentaGastos, Referencia


def get_datos_cuenta_gastos_semana(inicio: date, fin: date) -> dict:
    """
    Cuenta de Gastos statistics for a weekly period.
    - pedimentos pagados en la semana (fecha_pago in range)
    - cuentas de gastos finalizadas en la semana (fecha_finalizacion in range)
    - cobertura: de los pagados esa semana, cuántos ya tienen CG
    - promedio días desde fecha_pago hasta fecha_finalizacion
    - ranking por usuario finalizador
    """
    # Pagados en la semana
    pagadas_qs = Referencia.objects.filter(
        fecha_pago__gte=inicio,
        fecha_pago__lte=fin,
        es_rectificacion=False,
    )
    pagadas_ids = set(pagadas_qs.values_list('id', flat=True))
    pedimentos_pagados = len(pagadas_ids)

    # CG finalizadas en la semana
    cg_semana = list(
        CuentaGastos.objects
        .filter(
            fecha_finalizacion__date__gte=inicio,
            fecha_finalizacion__date__lte=fin,
        )
        .select_related('referencia', 'finalizado_por')
    )
    finalizadas = len(cg_semana)

    # Cobertura: de los pedimentos pagados esta semana, cuántos ya tienen CG
    cg_de_pagadas = CuentaGastos.objects.filter(
        referencia_id__in=pagadas_ids
    ).count() if pagadas_ids else 0
    pct_cobertura = round(cg_de_pagadas / pedimentos_pagados * 100) if pedimentos_pagados > 0 else 0

    # Promedio días fecha_pago → fecha_finalizacion (solo los de esta semana)
    tiempos = []
    for cg in cg_semana:
        if cg.referencia.fecha_pago:
            d = (cg.fecha_finalizacion.date() - cg.referencia.fecha_pago).days
            if 0 <= d <= 365:
                tiempos.append(d)
    avg_dias_pago_a_cg = round(sum(tiempos) / len(tiempos), 1) if tiempos else None

    # Ranking por usuario finalizador
    usuario_map: dict = {}
    for cg in cg_semana:
        u = cg.finalizado_por
        if not u:
            continue
        uname = u.username
        if uname not in usuario_map:
            usuario_map[uname] = {
                'nombre': u.get_full_name() or uname,
                'finalizadas': 0,
            }
        usuario_map[uname]['finalizadas'] += 1

    por_usuario = sorted(
        list(usuario_map.values()),
        key=lambda x: -x['finalizadas'],
    )

    return {
        'periodo_inicio':     inicio,
        'periodo_fin':        fin,
        'pedimentos_pagados': pedimentos_pagados,
        'finalizadas':        finalizadas,
        'cg_de_pagadas':      cg_de_pagadas,
        'sin_cg':             pedimentos_pagados - cg_de_pagadas,
        'pct_cobertura':      pct_cobertura,
        'avg_dias_pago_a_cg': avg_dias_pago_a_cg,
        'por_usuario':        por_usuario,
    }
