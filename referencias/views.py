import json
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.db.models.functions import Coalesce, Now, TruncMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import CuentaGastos, GlosaRegistro, Referencia


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
        real_2026[i] if i < month - 1 else projected_full[i]
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

    # ── Estado del mes actual: elaborados / sin FIR_ELEC / pago real ────────
    base_mes = Referencia.objects.filter(
        fecha_validacion__year=year,
        fecha_validacion__month=month,
        es_rectificacion=False,
    )
    mes_total        = base_mes.count()
    mes_sin_fir_elec = base_mes.filter(fir_elec='').count()
    mes_con_pago     = base_mes.filter(
        num_operacion__gt='', linea_captura__gt=''
    ).count()
    mes_con_fir_elec = mes_total - mes_sin_fir_elec

    def pct(part, total):
        return round(part / total * 100) if total else 0

    estado_mes = {
        'total':        mes_total,
        'sin_fir_elec': mes_sin_fir_elec,
        'con_fir_elec': mes_con_fir_elec,
        'con_pago':     mes_con_pago,
        'pendientes':   mes_total - mes_con_pago,
        'pct_fir_elec': pct(mes_con_fir_elec, mes_total),
        'pct_sin_fir':  pct(mes_sin_fir_elec, mes_total),
        'pct_pago':     pct(mes_con_pago, mes_total),
    }

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
        'estado_mes':       estado_mes,
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


# ---------------------------------------------------------------------------
# Glosa — pedimentos sin pagar (ventana deslizante: mes actual + mes anterior)
# ---------------------------------------------------------------------------

@login_required
def glosa(request):
    now   = timezone.localtime()
    year  = now.year
    month = now.month

    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1

    meses_nombres = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']

    base_qs = Referencia.objects.filter(
        fir_elec='',
        num_operacion='',
        linea_captura='',
        es_rectificacion=False,
    ).filter(
        Q(fecha_arribo__year=year,      fecha_arribo__month=month)       |
        Q(fecha_arribo__year=prev_year, fecha_arribo__month=prev_month)  |
        Q(fecha_validacion__year=year,      fecha_validacion__month=month)      |
        Q(fecha_validacion__year=prev_year, fecha_validacion__month=prev_month)
    )

    qs = base_qs.prefetch_related('contenedores', 'guias').annotate(
        fecha_ref=Coalesce('fecha_arribo', 'fecha_validacion'),
        antiguedad=ExpressionWrapper(
            Now() - Coalesce(F('fecha_arribo'), F('fecha_validacion')),
            output_field=DurationField(),
        ),
    )

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

    # Filtro patente
    patente = request.GET.get('patente', '')
    if patente:
        qs = qs.filter(patente=patente)

    # Ordenamiento
    orden = request.GET.get('orden', '-fecha_arribo')
    campos_validos = {
        'fecha_arribo', '-fecha_arribo', 'num_refe', '-num_refe',
        'nombre_cliente', '-nombre_cliente', 'fecha_validacion', '-fecha_validacion',
        'patente', '-patente',
    }
    if orden not in campos_validos:
        orden = '-fecha_arribo'
    qs = qs.order_by(orden)

    # KPIs por patente (sobre base sin paginación ni búsqueda)
    por_patente = (
        base_qs
        .values('patente', 'prefijo')
        .annotate(total=Count('id'))
        .order_by('-total')
    )

    paginador = Paginator(qs, 50)
    pagina    = paginador.get_page(request.GET.get('page', 1))

    # Cargar registros de glosa para la página actual (3 queries adicionales)
    ref_ids = [ref.id for ref in pagina]

    # Glosa activa (abierta) por referencia
    active_map = {
        g.referencia_id: g
        for g in GlosaRegistro.objects.filter(
            referencia_id__in=ref_ids,
            fecha_conclusion__isnull=True,
        ).select_related('usuario_entrada')
    }
    # Última glosa concluida por referencia (para mostrar historial)
    last_concluded_map: dict = {}
    for g in (
        GlosaRegistro.objects
        .filter(referencia_id__in=ref_ids, fecha_conclusion__isnull=False)
        .select_related('usuario_conclusion')
        .order_by('referencia_id', 'fecha_conclusion')
    ):
        last_concluded_map[g.referencia_id] = g  # queda la más reciente

    # Conteo total de ciclos de glosa por referencia
    counts = dict(
        GlosaRegistro.objects.filter(referencia_id__in=ref_ids)
        .values('referencia_id')
        .annotate(n=Count('id'))
        .values_list('referencia_id', 'n')
    )

    for ref in pagina:
        ref.glosa_reg   = active_map.get(ref.id)
        ref.last_glosa  = last_concluded_map.get(ref.id)
        ref.glosa_count = counts.get(ref.id, 0)

    ctx = {
        'page':           pagina,
        'q':              q,
        'filtro_patente': patente,
        'orden':          orden,
        'total':          qs.count(),
        'por_patente':    list(por_patente),
        'mes_actual':     meses_nombres[month - 1],
        'mes_anterior':   meses_nombres[prev_month - 1],
        'año':            year,
    }
    return render(request, 'referencias/glosa.html', ctx)


@login_required
@require_POST
def glosa_registrar(request, pk):
    ref = get_object_or_404(Referencia, pk=pk)
    # Solo crear si no hay ya una glosa activa (sin concluir) para esta referencia
    if not GlosaRegistro.objects.filter(referencia=ref, fecha_conclusion__isnull=True).exists():
        GlosaRegistro.objects.create(
            referencia=ref,
            fecha_entrada=timezone.now(),
            usuario_entrada=request.user,
        )
    return redirect('glosa')


@login_required
@require_POST
def glosa_urgente(request, pk):
    ref = get_object_or_404(Referencia, pk=pk)
    registro = GlosaRegistro.objects.filter(referencia=ref, fecha_conclusion__isnull=True).first()
    if registro is None:
        registro = GlosaRegistro.objects.create(
            referencia=ref,
            fecha_entrada=timezone.now(),
            usuario_entrada=request.user,
        )
    registro.urgente = not registro.urgente
    registro.save(update_fields=['urgente'])
    return redirect('glosa')


@login_required
@require_POST
def glosa_concluir(request, pk):
    registro = get_object_or_404(GlosaRegistro, referencia_id=pk, fecha_conclusion__isnull=True)
    registro.fecha_conclusion   = timezone.now()
    registro.usuario_conclusion = request.user
    registro.nota               = request.POST.get('nota', '').strip()
    registro.save()
    return redirect('glosa')


# ---------------------------------------------------------------------------
# Glosa — Dashboard estadístico
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Cuenta de Gastos
# ---------------------------------------------------------------------------

@login_required
def cuenta_gastos(request):
    qs = (
        Referencia.objects
        .filter(num_operacion__gt='', linea_captura__gt='', es_rectificacion=False)
        .filter(cuenta_gastos__isnull=True)
        .prefetch_related('contenedores', 'guias')
    )

    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(num_refe__icontains=q) | Q(nombre_cliente__icontains=q)
        ).distinct()

    patente = request.GET.get('patente', '')
    if patente:
        qs = qs.filter(patente=patente)

    año = request.GET.get('año', '')
    if año:
        qs = qs.filter(fecha_pago__year=año)

    mes = request.GET.get('mes', '')
    if mes:
        qs = qs.filter(fecha_pago__month=mes)

    qs = qs.order_by('-fecha_pago')

    años_disponibles = (
        Referencia.objects
        .filter(num_operacion__gt='', linea_captura__gt='', fecha_pago__isnull=False)
        .values_list('fecha_pago__year', flat=True)
        .distinct()
        .order_by('-fecha_pago__year')
    )
    meses = [
        (1,'Ene'),(2,'Feb'),(3,'Mar'),(4,'Abr'),(5,'May'),(6,'Jun'),
        (7,'Jul'),(8,'Ago'),(9,'Sep'),(10,'Oct'),(11,'Nov'),(12,'Dic'),
    ]

    paginador = Paginator(qs, 50)
    pagina    = paginador.get_page(request.GET.get('page', 1))

    ctx = {
        'page':             pagina,
        'q':                q,
        'filtro_patente':   patente,
        'filtro_año':       año,
        'filtro_mes':       mes,
        'total':            qs.count(),
        'años_disponibles': años_disponibles,
        'meses':            meses,
    }
    return render(request, 'referencias/cuenta_gastos.html', ctx)


@login_required
@require_POST
def cuenta_gastos_finalizar(request, pk):
    if not request.user.is_staff:
        return redirect('cuenta_gastos')
    ref = get_object_or_404(
        Referencia,
        pk=pk,
        num_operacion__gt='',
        linea_captura__gt='',
    )
    CuentaGastos.objects.get_or_create(
        referencia=ref,
        defaults={
            'nota':               request.POST.get('nota', '').strip(),
            'fecha_finalizacion': timezone.now(),
            'finalizado_por':     request.user,
        },
    )
    return redirect('cuenta_gastos')


@login_required
def glosa_dashboard(request):
    now   = timezone.localtime()
    year  = now.year
    month = now.month
    meses_nombres = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']

    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1

    base_qs = Referencia.objects.filter(
        fir_elec='',
        num_operacion='',
        linea_captura='',
        es_rectificacion=False,
    ).filter(
        Q(fecha_arribo__year=year,      fecha_arribo__month=month)      |
        Q(fecha_arribo__year=prev_year, fecha_arribo__month=prev_month) |
        Q(fecha_validacion__year=year,      fecha_validacion__month=month)      |
        Q(fecha_validacion__year=prev_year, fecha_validacion__month=prev_month)
    )

    base_ann = base_qs.annotate(
        antiguedad=ExpressionWrapper(
            Now() - Coalesce(F('fecha_arribo'), F('fecha_validacion')),
            output_field=DurationField(),
        ),
    )

    total = base_qs.count()

    # GlosaRegistro dentro de la ventana
    glosas_qs = (
        GlosaRegistro.objects
        .filter(referencia__in=base_qs)
        .select_related('referencia', 'usuario_entrada', 'usuario_conclusion')
    )

    # Referencias con al menos una glosa (cualquier estado)
    any_glosa_ids = set(glosas_qs.values_list('referencia_id', flat=True))
    # Referencias con glosa activa (sin concluir)
    active_ids = set(
        glosas_qs.filter(fecha_conclusion__isnull=True)
        .values_list('referencia_id', flat=True)
    )
    # Referencias con glosa(s) concluida(s) pero sin ninguna activa
    concluded_only_ids = any_glosa_ids - active_ids

    sin_registrar = total - len(any_glosa_ids)
    en_proceso    = len(active_ids)
    concluidas    = len(concluded_only_ids)

    # Urgentes: referencias con glosa activa marcada como urgente
    urgentes_ids = set(
        glosas_qs.filter(urgente=True, fecha_conclusion__isnull=True)
        .values_list('referencia_id', flat=True)
    )
    urgentes_count = len(urgentes_ids)
    # Críticos: urgente + ≥ 60 días (urgente manual + antigüedad severa)
    criticos_count = (
        base_ann
        .filter(id__in=urgentes_ids, antiguedad__gte=timedelta(days=60))
        .count()
    )

    # Distribución de antigüedad
    dist = {
        'verde':    base_ann.filter(antiguedad__lt=timedelta(days=15)).count(),
        'amarillo': base_ann.filter(
                        antiguedad__gte=timedelta(days=15),
                        antiguedad__lt=timedelta(days=30)).count(),
        'naranja':  base_ann.filter(
                        antiguedad__gte=timedelta(days=30),
                        antiguedad__lt=timedelta(days=60)).count(),
        'rojo':     base_ann.filter(antiguedad__gte=timedelta(days=60)).count(),
    }

    avg_ant = base_ann.aggregate(avg=Avg('antiguedad'))['avg']
    avg_antiguedad_dias = avg_ant.days if avg_ant else 0

    # Tiempos: arribo → entrada glosa  |  entrada → conclusión
    tiempos_entrada  = []
    tiempos_proceso  = []
    for g in glosas_qs:
        ref_date = g.referencia.fecha_arribo or g.referencia.fecha_validacion
        if ref_date:
            d = (g.fecha_entrada.date() - ref_date).days
            if d >= 0:
                tiempos_entrada.append(d)
        if g.concluida:
            d2 = (g.fecha_conclusion - g.fecha_entrada).days
            if d2 >= 0:
                tiempos_proceso.append(d2)

    avg_tiempo_entrada = round(sum(tiempos_entrada) / len(tiempos_entrada), 1) if tiempos_entrada else None
    avg_tiempo_proceso = round(sum(tiempos_proceso) / len(tiempos_proceso), 1) if tiempos_proceso else None

    # Por patente
    por_patente = (
        base_qs.values('patente', 'prefijo')
        .annotate(total=Count('id'))
        .order_by('-total')
    )

    # Top urgentes
    urgentes_list = list(
        base_ann
        .filter(id__in=urgentes_ids)
        .prefetch_related('contenedores')
        .order_by('-antiguedad')[:15]
    )
    urg_glosas_map = {
        g.referencia_id: g
        for g in GlosaRegistro.objects.filter(
            referencia_id__in=[r.id for r in urgentes_list],
            fecha_conclusion__isnull=True,
        ).select_related('usuario_entrada')
    }
    for ref in urgentes_list:
        ref.glosa_reg = urg_glosas_map.get(ref.id)

    # Por usuario
    por_usuario_reg = (
        GlosaRegistro.objects
        .filter(referencia__in=base_qs)
        .values('usuario_entrada__username',
                'usuario_entrada__first_name',
                'usuario_entrada__last_name')
        .annotate(registradas=Count('id'))
        .order_by('-registradas')
    )
    conc_map = {
        d['usuario_conclusion__username']: d['concluidas']
        for d in GlosaRegistro.objects
            .filter(referencia__in=base_qs, fecha_conclusion__isnull=False)
            .values('usuario_conclusion__username')
            .annotate(concluidas=Count('id'))
    }
    por_usuario = []
    for u in por_usuario_reg:
        username = u['usuario_entrada__username'] or ''
        nombre = (
            ((u['usuario_entrada__first_name'] or '') + ' ' +
             (u['usuario_entrada__last_name'] or '')).strip()
            or username
        )
        reg  = u['registradas']
        conc = conc_map.get(username, 0)
        por_usuario.append({
            'nombre':      nombre,
            'username':    username,
            'registradas': reg,
            'concluidas':  conc,
            'pendientes':  reg - conc,
        })

    # Actividad reciente (últimas 10 acciones)
    recientes = (
        GlosaRegistro.objects
        .filter(referencia__in=base_qs)
        .select_related('referencia', 'usuario_entrada', 'usuario_conclusion')
        .order_by('-fecha_entrada')[:10]
    )

    ctx = {
        'total':               total,
        'sin_registrar':       sin_registrar,
        'en_proceso':          en_proceso,
        'concluidas':          concluidas,
        'urgentes_count':      urgentes_count,
        'criticos_count':      criticos_count,
        'dist':                dist,
        'avg_antiguedad_dias': avg_antiguedad_dias,
        'avg_tiempo_entrada':  avg_tiempo_entrada,
        'avg_tiempo_proceso':  avg_tiempo_proceso,
        'por_patente':         list(por_patente),
        'urgentes_list':       urgentes_list,
        'por_usuario':         por_usuario,
        'recientes':           recientes,
        'mes_actual':          meses_nombres[month - 1],
        'mes_anterior':        meses_nombres[prev_month - 1],
        'año':                 year,
    }
    return render(request, 'referencias/glosa_dashboard.html', ctx)
