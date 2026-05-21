import json
from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import Referencia


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@login_required
def dashboard(request):
    now      = timezone.localtime()
    year     = now.year
    month    = now.month
    meses    = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
    years    = [2022, 2023, 2024, 2025, 2026]

    def monthly_array(y):
        data = (
            Referencia.objects
            .filter(fecha_pago__year=y, es_rectificacion=False)
            .annotate(m=TruncMonth('fecha_pago'))
            .values('m')
            .annotate(c=Count('id'))
            .order_by('m')
        )
        arr = [0] * 12
        for row in data:
            arr[row['m'].month - 1] = row['c']
        return arr

    def yearly_total(y):
        return Referencia.objects.filter(
            fecha_pago__year=y, es_rectificacion=False
        ).count()

    monthly = {y: monthly_array(y) for y in years}
    totales = {y: yearly_total(y) for y in years}

    # ── Proyección 2026 ──────────────────────────────────────────────────────
    m24 = monthly[2024]
    m25 = monthly[2025]
    avg24 = sum(m24) / 12 if sum(m24) > 0 else 0
    avg25 = sum(m25) / 12 if sum(m25) > 0 else 0
    base  = (avg24 + avg25) / 2 if (avg24 + avg25) > 0 else 1

    growth = ((avg25 - avg24) / avg24) if avg24 > 0 else 0
    pred_avg = avg25 * (1 + growth)

    def seasonality(i):
        return (m24[i] + m25[i]) / (avg24 + avg25) if (avg24 + avg25) > 0 else 1.0

    projected_full = [int(pred_avg * seasonality(i)) for i in range(12)]
    real_2026      = monthly[2026]

    real_chart = [v if i < month else None for i, v in enumerate(real_2026)]
    proj_chart = [
        real_2026[i] if i < month else projected_full[i]
        for i in range(12)
    ]

    # ── Tabla comparativa 2026 ───────────────────────────────────────────────
    comparativa = []
    for i in range(12):
        proj = projected_full[i]
        if i < month - 1:
            estado = 'completed'
            real = real_2026[i]
        elif i == month - 1:
            estado = 'in_progress'
            real = real_2026[i]
        else:
            estado = 'future'
            real = None
        delta = (real - proj) if real is not None else None
        pct   = int(real / proj * 100) if (real is not None and proj > 0) else None
        comparativa.append({
            'mes': meses[i], 'proyectado': proj, 'real': real,
            'delta': delta, 'pct': pct, 'estado': estado,
        })

    # ── KPIs ─────────────────────────────────────────────────────────────────
    mes_actual_total = real_2026[month - 1]
    mes_anterior_total = real_2026[month - 2] if month > 1 else 0
    variacion = (
        int((mes_actual_total - mes_anterior_total) / mes_anterior_total * 100)
        if mes_anterior_total > 0 else 0
    )

    por_patente = (
        Referencia.objects
        .filter(fecha_pago__year=year, es_rectificacion=False)
        .values('patente', 'prefijo')
        .annotate(total=Count('id'))
        .order_by('-total')
    )

    # ── Últimas 10 referencias pagadas ───────────────────────────────────────
    recientes = (
        Referencia.objects
        .filter(fecha_pago__isnull=False, es_rectificacion=False)
        .select_related()
        .prefetch_related('contenedores', 'guias')
        [:10]
    )

    ctx = {
        'meses_json':       json.dumps(meses),
        'monthly_2022':     json.dumps(monthly[2022]),
        'monthly_2023':     json.dumps(monthly[2023]),
        'monthly_2024':     json.dumps(monthly[2024]),
        'monthly_2025':     json.dumps(monthly[2025]),
        'monthly_2026_real': json.dumps(real_chart),
        'monthly_2026_proj': json.dumps(proj_chart),
        'totales':          totales,
        'comparativa':      comparativa,
        'kpi_año':          totales.get(year, 0),
        'kpi_mes':          mes_actual_total,
        'kpi_variacion':    variacion,
        'por_patente':      list(por_patente),
        'recientes':        recientes,
        'año_actual':       year,
        'mes_actual':       meses[month - 1],
    }
    return render(request, 'dashboard.html', ctx)


# ---------------------------------------------------------------------------
# Lista de referencias
# ---------------------------------------------------------------------------

@login_required
def lista(request):
    qs = Referencia.objects.prefetch_related('contenedores', 'guias')

    # Búsqueda
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(num_refe__icontains=q)
            | Q(num_pedimento__icontains=q)
            | Q(nombre_cliente__icontains=q)
            | Q(contenedores__num_cont__icontains=q)
            | Q(guias__numero_guia__icontains=q)
        ).distinct()

    # Filtros
    patente = request.GET.get('patente', '')
    if patente:
        qs = qs.filter(patente=patente)

    año = request.GET.get('año', '')
    if año:
        qs = qs.filter(fecha_pago__year=año)

    mes = request.GET.get('mes', '')
    if mes:
        qs = qs.filter(fecha_pago__month=mes)

    clave = request.GET.get('clave', '')
    if clave:
        qs = qs.filter(clave_pedimento=clave)

    solo_pagadas = request.GET.get('pagadas', '')
    if solo_pagadas:
        qs = qs.filter(
            fecha_pago__isnull=False,
        ).exclude(linea_captura='').exclude(num_operacion='')

    # Excluir rectificaciones por defecto
    incluir_rect = request.GET.get('rectificaciones', '')
    if not incluir_rect:
        qs = qs.filter(es_rectificacion=False)

    # Ordenamiento
    orden = request.GET.get('orden', '-fecha_pago')
    campos_validos = {
        'fecha_pago', '-fecha_pago', 'num_refe', '-num_refe',
        'nombre_cliente', '-nombre_cliente', 'fecha_arribo', '-fecha_arribo',
        'patente', '-patente',
    }
    if orden not in campos_validos:
        orden = '-fecha_pago'
    qs = qs.order_by(orden)

    # Paginación
    paginador = Paginator(qs, 50)
    pagina    = paginador.get_page(request.GET.get('page', 1))

    # Opciones para filtros
    años_disponibles = (
        Referencia.objects
        .filter(fecha_pago__isnull=False)
        .values_list('fecha_pago__year', flat=True)
        .distinct()
        .order_by('-fecha_pago__year')
    )
    claves_disponibles = (
        Referencia.objects
        .exclude(clave_pedimento='')
        .values_list('clave_pedimento', flat=True)
        .distinct()
        .order_by('clave_pedimento')
    )
    meses = [
        (1,'Ene'),(2,'Feb'),(3,'Mar'),(4,'Abr'),(5,'May'),(6,'Jun'),
        (7,'Jul'),(8,'Ago'),(9,'Sep'),(10,'Oct'),(11,'Nov'),(12,'Dic'),
    ]

    ctx = {
        'page':               pagina,
        'q':                  q,
        'filtro_patente':     patente,
        'filtro_año':         año,
        'filtro_mes':         mes,
        'filtro_clave':       clave,
        'filtro_pagadas':     solo_pagadas,
        'filtro_rect':        incluir_rect,
        'orden':              orden,
        'años_disponibles':   años_disponibles,
        'claves_disponibles': claves_disponibles,
        'meses':              meses,
        'total_filtrado':     qs.count(),
    }
    return render(request, 'referencias/lista.html', ctx)


# ---------------------------------------------------------------------------
# Detalle de referencia
# ---------------------------------------------------------------------------

@login_required
def detalle(request, num_refe):
    ref = get_object_or_404(
        Referencia.objects.prefetch_related('contenedores', 'guias'),
        num_refe=num_refe,
    )
    return render(request, 'referencias/detalle.html', {'ref': ref})
