import calendar as cal
import json
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.db.models.functions import Coalesce, Now, TruncMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .glosa_data import analyze_notas
from .models import CuentaGastos, GlosaRegistro, Referencia
from .predictor import predecir_pago


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

def _riesgo_glosa_cliente(nombre_cliente):
    """Calcula nivel de riesgo de glosa histórico para un cliente."""
    if not nombre_cliente:
        return None
    total = Referencia.objects.filter(
        nombre_cliente=nombre_cliente,
        es_rectificacion=False,
        fecha_pago__isnull=False,
    ).count()
    if not total:
        return None
    con_glosa = (
        Referencia.objects
        .filter(nombre_cliente=nombre_cliente, es_rectificacion=False)
        .filter(glosas__isnull=False)
        .distinct()
        .count()
    )
    pct = round(con_glosa / total * 100, 1)
    nivel = 'rojo' if pct > 15 else ('amarillo' if pct >= 5 else 'verde')
    return {'total': total, 'con_glosa': con_glosa, 'pct': pct, 'nivel': nivel}


@login_required
def detalle(request, num_refe):
    ref = get_object_or_404(
        Referencia.objects.prefetch_related(
            'contenedores', 'guias',
            'glosas', 'glosas__usuario_entrada', 'glosas__usuario_conclusion',
        ),
        num_refe=num_refe,
    )
    try:
        cuenta_gastos = ref.cuenta_gastos
    except Exception:
        cuenta_gastos = None
    prediccion = predecir_pago(ref) if not ref.es_rectificacion else None

    now_local = timezone.localtime()
    _y, _m = now_local.year, now_local.month
    _py, _pm = (_y, _m - 1) if _m > 1 else (_y - 1, 12)
    fecha_ref = ref.fecha_arribo or ref.fecha_validacion
    en_ventana_glosa = bool(fecha_ref and (
        (fecha_ref.year == _y and fecha_ref.month == _m) or
        (fecha_ref.year == _py and fecha_ref.month == _pm)
    ))
    necesita_glosa = (
        not ref.es_rectificacion and
        ref.fir_elec == '' and
        ref.num_operacion == '' and
        ref.linea_captura == ''
    )
    glosa_activa = ref.glosas.filter(fecha_conclusion__isnull=True).first()

    return render(request, 'referencias/detalle.html', {
        'ref': ref,
        'cuenta_gastos': cuenta_gastos,
        'riesgo_glosa': _riesgo_glosa_cliente(ref.nombre_cliente),
        'prediccion': prediccion,
        'glosa_activa': glosa_activa,
        'en_ventana_glosa': en_ventana_glosa,
        'necesita_glosa': necesita_glosa,
    })


@login_required
def timeline(request, num_refe):
    ref = get_object_or_404(
        Referencia.objects.prefetch_related(
            'contenedores', 'guias',
            'glosas', 'glosas__usuario_entrada', 'glosas__usuario_conclusion',
        ),
        num_refe=num_refe,
    )
    try:
        cuenta_gastos = ref.cuenta_gastos
    except Exception:
        cuenta_gastos = None

    if not cuenta_gastos:
        return redirect('detalle', num_refe=num_refe)

    def _nombre(user):
        if not user:
            return None
        return user.get_full_name() or user.username

    eventos = []

    if ref.fecha_captura:
        eventos.append({
            'tipo': 'captura', 'color': 'violet',
            'titulo': 'Captura',
            'fecha': ref.fecha_captura,
            'actor': ref.nombre_capturista or None,
            'datos': [
                ('Referencia', ref.num_refe),
                ('Cliente', ref.nombre_cliente or '—'),
                ('Patente', f"{ref.prefijo} · {ref.patente}"),
            ],
        })

    if ref.fecha_arribo:
        conts = list(ref.contenedores.all())
        guias = list(ref.guias.all())
        datos_arribo = []
        if conts:
            resumen = ', '.join(c.num_cont for c in conts[:4])
            if len(conts) > 4:
                resumen += f' +{len(conts) - 4} más'
            datos_arribo.append(('Contenedores', resumen))
        if guias:
            resumen = ', '.join(g.numero_guia for g in guias[:3])
            if len(guias) > 3:
                resumen += f' +{len(guias) - 3} más'
            datos_arribo.append(('Guías BL', resumen))
        eventos.append({
            'tipo': 'arribo', 'color': 'slate',
            'titulo': 'Arribo de mercancía',
            'fecha': ref.fecha_arribo,
            'actor': None,
            'datos': datos_arribo,
        })

    for g in sorted(ref.glosas.all(), key=lambda x: x.fecha_entrada):
        label = 'Glosa detectada'
        if g.urgente:
            label += ' · urgente'
        eventos.append({
            'tipo': 'glosa_entrada', 'color': 'amber',
            'titulo': label,
            'fecha': g.fecha_entrada.date(),
            'actor': _nombre(g.usuario_entrada),
            'datos': [('Nota', g.nota)] if g.nota else [],
        })
        if g.concluida:
            eventos.append({
                'tipo': 'glosa_concluida', 'color': 'green',
                'titulo': 'Glosa resuelta',
                'fecha': g.fecha_conclusion.date(),
                'actor': _nombre(g.usuario_conclusion),
                'datos': [],
            })

    if ref.fecha_validacion:
        datos_val = []
        if ref.num_pedimento:
            datos_val.append(('Pedimento', ref.num_pedimento))
        if ref.clave_pedimento:
            datos_val.append(('Clave', ref.clave_pedimento))
        if ref.fir_elec:
            fir = ref.fir_elec[:40] + ('…' if len(ref.fir_elec) > 40 else '')
            datos_val.append(('Firma electrónica', fir))
        eventos.append({
            'tipo': 'validacion', 'color': 'sky',
            'titulo': 'Validación',
            'fecha': ref.fecha_validacion,
            'actor': None,
            'datos': datos_val,
        })

    if ref.fecha_pago:
        datos_pago = []
        if ref.num_operacion:
            datos_pago.append(('No. Operación', ref.num_operacion))
        if ref.linea_captura:
            datos_pago.append(('Línea de captura', ref.linea_captura))
        eventos.append({
            'tipo': 'pago', 'color': 'emerald',
            'titulo': 'Pago registrado',
            'fecha': ref.fecha_pago,
            'actor': None,
            'datos': datos_pago,
        })

    datos_cg = []
    if cuenta_gastos.nota:
        datos_cg.append(('Nota', cuenta_gastos.nota[:100]))
    eventos.append({
        'tipo': 'cuenta_gastos', 'color': 'indigo',
        'titulo': 'Cuenta de Gastos finalizada',
        'fecha': cuenta_gastos.fecha_finalizacion.date(),
        'actor': _nombre(cuenta_gastos.finalizado_por),
        'datos': datos_cg,
    })

    eventos.sort(key=lambda e: e['fecha'])

    # Duraciones entre hitos clave
    fc = ref.fecha_captura
    fv = ref.fecha_validacion
    fp = ref.fecha_pago
    ff = cuenta_gastos.fecha_finalizacion.date()

    duraciones = {}
    if fc and fv:
        duraciones['captura_validacion'] = (fv - fc).days
    if fv and fp:
        duraciones['validacion_pago'] = (fp - fv).days
    if fp and ff:
        duraciones['pago_finalizacion'] = (ff - fp).days
    if fc and ff:
        duraciones['total'] = (ff - fc).days

    return render(request, 'referencias/timeline.html', {
        'ref': ref,
        'cuenta_gastos': cuenta_gastos,
        'eventos': eventos,
        'duraciones': duraciones,
    })


@login_required
@require_POST
def glosa_eliminar(request, pk):
    if not request.user.is_staff:
        return redirect('lista')
    registro = get_object_or_404(GlosaRegistro, pk=pk)
    num_refe = registro.referencia.num_refe
    registro.delete()
    return redirect('detalle', num_refe=num_refe)


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
        es_rectificacion=True,
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
    next_url = (request.POST.get('next') or request.GET.get('next', '')).strip()
    return redirect(next_url if next_url.startswith('/') else reverse('glosa'))


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
    next_url = request.POST.get('next', '').strip()
    return redirect(next_url if next_url.startswith('/') else reverse('glosa'))


@login_required
@require_POST
def glosa_concluir(request, pk):
    registro = get_object_or_404(GlosaRegistro, referencia_id=pk, fecha_conclusion__isnull=True)
    registro.fecha_conclusion   = timezone.now()
    registro.usuario_conclusion = request.user
    registro.nota               = request.POST.get('nota', '').strip()
    registro.save()
    next_url = request.POST.get('next', '').strip()
    return redirect(next_url if next_url.startswith('/') else reverse('glosa'))


@login_required
@require_POST
def glosa_revertir(request, pk):
    next_url = request.POST.get('next', '').strip()
    next_url = next_url if next_url.startswith('/') else reverse('glosa')
    if not request.user.is_staff:
        return redirect(next_url)
    registro = get_object_or_404(GlosaRegistro, referencia_id=pk, fecha_conclusion__isnull=True)
    registro.delete()
    return redirect(next_url)


# ---------------------------------------------------------------------------
# Glosa — Dashboard estadístico
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Cuenta de Gastos
# ---------------------------------------------------------------------------

@login_required
def cuenta_gastos(request):
    tab = request.GET.get('tab', 'pendientes')

    meses_lista = [
        (1,'Ene'),(2,'Feb'),(3,'Mar'),(4,'Abr'),(5,'May'),(6,'Jun'),
        (7,'Jul'),(8,'Ago'),(9,'Sep'),(10,'Oct'),(11,'Nov'),(12,'Dic'),
    ]
    q       = request.GET.get('q', '').strip()
    patente = request.GET.get('patente', '')
    año     = request.GET.get('año', '')
    mes     = request.GET.get('mes', '')

    if tab == 'finalizadas':
        qs = (
            CuentaGastos.objects
            .select_related('referencia', 'finalizado_por')
            .prefetch_related('referencia__contenedores', 'referencia__guias')
        )
        if q:
            qs = qs.filter(
                Q(referencia__num_refe__icontains=q) |
                Q(referencia__nombre_cliente__icontains=q)
            ).distinct()
        if patente:
            qs = qs.filter(referencia__patente=patente)
        if año:
            qs = qs.filter(referencia__fecha_pago__year=año)
        if mes:
            qs = qs.filter(referencia__fecha_pago__month=mes)
        qs = qs.order_by('-fecha_finalizacion')

        años_disponibles = (
            CuentaGastos.objects
            .filter(referencia__fecha_pago__isnull=False)
            .values_list('referencia__fecha_pago__year', flat=True)
            .distinct()
            .order_by('-referencia__fecha_pago__year')
        )

        # Calcular días pago→finalización para cada registro de la página
        paginador = Paginator(qs, 50)
        pagina    = paginador.get_page(request.GET.get('page', 1))
        for cg in pagina:
            if cg.referencia.fecha_pago:
                cg.dias_proceso = (cg.fecha_finalizacion.date() - cg.referencia.fecha_pago).days
            else:
                cg.dias_proceso = None

        ctx = {
            'tab':              'finalizadas',
            'page':             pagina,
            'q':                q,
            'filtro_patente':   patente,
            'filtro_año':       año,
            'filtro_mes':       mes,
            'total':            qs.count(),
            'años_disponibles': años_disponibles,
            'meses':            meses_lista,
        }
    else:
        qs = (
            Referencia.objects
            .filter(num_operacion__gt='', linea_captura__gt='', es_rectificacion=False)
            .filter(cuenta_gastos__isnull=True)
            .prefetch_related('contenedores', 'guias')
        )
        if q:
            qs = qs.filter(
                Q(num_refe__icontains=q) | Q(nombre_cliente__icontains=q)
            ).distinct()
        if patente:
            qs = qs.filter(patente=patente)
        if año:
            qs = qs.filter(fecha_pago__year=año)
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

        paginador = Paginator(qs, 50)
        pagina    = paginador.get_page(request.GET.get('page', 1))

        ctx = {
            'tab':              'pendientes',
            'page':             pagina,
            'q':                q,
            'filtro_patente':   patente,
            'filtro_año':       año,
            'filtro_mes':       mes,
            'total':            qs.count(),
            'años_disponibles': años_disponibles,
            'meses':            meses_lista,
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
    next_qs = request.POST.get('next', '')
    if next_qs and next_qs.startswith('?'):
        return redirect(reverse('cuenta_gastos') + next_qs)
    return redirect('cuenta_gastos')


@login_required
def cuenta_gastos_dashboard(request):
    now        = timezone.localtime()
    meses_nombres = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']

    total_finalizadas  = CuentaGastos.objects.count()
    total_pendientes   = (
        Referencia.objects
        .filter(num_operacion__gt='', linea_captura__gt='', es_rectificacion=False,
                cuenta_gastos__isnull=True)
        .count()
    )
    este_mes = CuentaGastos.objects.filter(
        fecha_finalizacion__year=now.year,
        fecha_finalizacion__month=now.month,
    ).count()

    # Calcular días pago → finalización (en Python para evitar diferencias date/datetime)
    dias_list = []
    for cg in CuentaGastos.objects.select_related('referencia').iterator():
        if cg.referencia.fecha_pago:
            d = (cg.fecha_finalizacion.date() - cg.referencia.fecha_pago).days
            if d >= 0:
                dias_list.append(d)

    avg_dias = round(sum(dias_list) / len(dias_list), 1) if dias_list else None

    dist = {
        'rapido':  sum(1 for d in dias_list if d < 5),
        'normal':  sum(1 for d in dias_list if 5 <= d < 15),
        'tardio':  sum(1 for d in dias_list if 15 <= d < 30),
        'critico': sum(1 for d in dias_list if d >= 30),
    }

    # Por patente
    por_patente = list(
        CuentaGastos.objects
        .values(patente=F('referencia__patente'), prefijo=F('referencia__prefijo'))
        .annotate(total=Count('id'))
        .order_by('-total')
    )

    # Por usuario que finalizó
    por_usuario_raw = list(
        CuentaGastos.objects
        .values('finalizado_por__username',
                'finalizado_por__first_name',
                'finalizado_por__last_name')
        .annotate(total=Count('id'))
        .order_by('-total')
    )
    por_usuario = []
    for u in por_usuario_raw:
        username = u['finalizado_por__username'] or ''
        nombre = (
            ((u['finalizado_por__first_name'] or '') + ' ' +
             (u['finalizado_por__last_name'] or '')).strip()
            or username
        )
        por_usuario.append({'nombre': nombre, 'username': username, 'total': u['total']})

    # Acumulado mensual del año en curso
    acumulado = [0] * 12
    for cg in CuentaGastos.objects.filter(fecha_finalizacion__year=now.year):
        acumulado[cg.fecha_finalizacion.month - 1] += 1

    # Últimas 10 finalizaciones con días de proceso
    recientes = list(
        CuentaGastos.objects
        .select_related('referencia', 'finalizado_por')
        .order_by('-fecha_finalizacion')[:10]
    )
    for cg in recientes:
        if cg.referencia.fecha_pago:
            cg.dias_proceso = (cg.fecha_finalizacion.date() - cg.referencia.fecha_pago).days
        else:
            cg.dias_proceso = None

    ctx = {
        'total_finalizadas': total_finalizadas,
        'total_pendientes':  total_pendientes,
        'este_mes':          este_mes,
        'avg_dias':          avg_dias,
        'dist':              dist,
        'por_patente':       por_patente,
        'por_usuario':       por_usuario,
        'acumulado':         acumulado,
        'recientes':         recientes,
        'mes_actual':        meses_nombres[now.month - 1],
        'año':               now.year,
        'meses_json':        json.dumps(meses_nombres),
        'acumulado_json':    json.dumps(acumulado),
    }
    return render(request, 'referencias/cuenta_gastos_dashboard.html', ctx)


@login_required
def referencias_dashboard(request):
    now   = timezone.localtime()
    year  = now.year
    month = now.month
    today = now.date()
    meses_nombres = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']

    _, last_day_num = cal.monthrange(year, month)
    mes_inicio = date(year, month, 1)
    mes_fin    = date(year, month, last_day_num)

    validadas_mes = Referencia.objects.filter(
        fecha_validacion__year=year,
        fecha_validacion__month=month,
        es_rectificacion=False,
    )
    pagadas_mes = Referencia.objects.filter(
        fecha_pago__year=year,
        fecha_pago__month=month,
        es_rectificacion=False,
        num_operacion__gt='',
        linea_captura__gt='',
    )

    total_validadas = validadas_mes.count()
    total_pagadas   = pagadas_mes.count()

    # Avg captura → validacion del mes completo
    pares_mes = list(
        validadas_mes.filter(fecha_captura__isnull=False)
        .values_list('fecha_captura', 'fecha_validacion')
    )
    dias_cv_mes = [(v - c).days for c, v in pares_mes if v and c and v >= c]
    avg_dias_mes = round(sum(dias_cv_mes) / len(dias_cv_mes), 1) if dias_cv_mes else None

    # Semanas del mes: bloques fijos de 7 días desde el día 1
    semanas = []
    for w in range(5):
        ws = mes_inicio + timedelta(days=w * 7)
        if ws > mes_fin:
            break
        we = min(ws + timedelta(days=6), mes_fin)
        semanas.append({
            'num':        w + 1,
            'inicio':     ws,
            'fin':        we,
            'capturadas': Referencia.objects.filter(
                fecha_captura__gte=ws, fecha_captura__lte=we, es_rectificacion=False,
            ).count(),
            'validadas':  validadas_mes.filter(fecha_validacion__gte=ws, fecha_validacion__lte=we).count(),
            'pagadas':    pagadas_mes.filter(fecha_pago__gte=ws, fecha_pago__lte=we).count(),
            'es_actual':  ws <= today <= we,
            'es_futura':  ws > today,
        })

    # Tabla por capturista
    caps = {}
    for row in validadas_mes.values('nombre_capturista').annotate(capturadas=Count('id')):
        nombre = row['nombre_capturista'] or '—'
        caps[nombre] = {'nombre': nombre, 'capturadas': row['capturadas'], '_dias': [], 'glosas': 0}

    for nombre_cap, fecha_cap, fecha_val in (
        validadas_mes.filter(fecha_captura__isnull=False)
        .values_list('nombre_capturista', 'fecha_captura', 'fecha_validacion')
    ):
        nombre = nombre_cap or '—'
        if fecha_val and fecha_cap and fecha_val >= fecha_cap and nombre in caps:
            caps[nombre]['_dias'].append((fecha_val - fecha_cap).days)

    for data in caps.values():
        lst = data.pop('_dias')
        data['avg_dias_cv'] = round(sum(lst) / len(lst), 1) if lst else None

    for row in (
        GlosaRegistro.objects
        .filter(referencia__in=validadas_mes)
        .values('referencia__nombre_capturista')
        .annotate(total=Count('id'))
    ):
        nombre = row['referencia__nombre_capturista'] or '—'
        if nombre in caps:
            caps[nombre]['glosas'] = row['total']

    por_capturista = sorted(caps.values(), key=lambda x: -x['capturadas'])
    for c in por_capturista:
        c['glosa_pct'] = round(c['glosas'] / c['capturadas'] * 100) if c['capturadas'] else 0

    por_patente = list(
        validadas_mes.values('patente', 'prefijo')
        .annotate(total=Count('id'))
        .order_by('-total')
    )
    top_clientes = list(
        pagadas_mes.values('nombre_cliente')
        .annotate(total=Count('id'))
        .order_by('-total')[:8]
    )

    ctx = {
        'mes_actual':    meses_nombres[month - 1],
        'año':           year,
        'total_validadas': total_validadas,
        'total_pagadas':   total_pagadas,
        'total_pendientes': total_validadas - total_pagadas,
        'avg_dias_mes':  avg_dias_mes,
        'semanas':       semanas,
        'por_capturista': por_capturista,
        'por_patente':   por_patente,
        'top_clientes':  top_clientes,
        'semanas_labels_json':    json.dumps([f"Sem {s['num']}" for s in semanas]),
        'semanas_validadas_json': json.dumps([s['validadas'] for s in semanas]),
        'semanas_pagadas_json':   json.dumps([s['pagadas'] for s in semanas]),
    }
    return render(request, 'referencias/referencias_dashboard.html', ctx)


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
    recientes_qs = list(
        GlosaRegistro.objects
        .filter(referencia__in=base_qs)
        .select_related('referencia', 'usuario_entrada', 'usuario_conclusion')
        .order_by('-fecha_entrada')[:10]
    )

    # Análisis de notas: ALL glosas de la ventana con nota no vacía
    glosas_con_nota = list(
        GlosaRegistro.objects
        .filter(referencia__in=base_qs)
        .exclude(nota='')
        .select_related('referencia', 'usuario_entrada')
    )
    nota_analysis = analyze_notas(glosas_con_nota)

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
        'recientes':           recientes_qs,
        'mes_actual':          meses_nombres[month - 1],
        'mes_anterior':        meses_nombres[prev_month - 1],
        'año':                 year,
        **nota_analysis,
    }
    return render(request, 'referencias/glosa_dashboard.html', ctx)


# ---------------------------------------------------------------------------
# SLA por Capturista
# ---------------------------------------------------------------------------

_MESES_SLA = ['Enero','Febrero','Marzo','Abril','Mayo','Junio',
               'Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre']
_MESES_CORTO = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']


def _nivel_cp(dias):
    if dias is None:
        return 'gris'
    return 'rojo' if dias > 10 else ('amarillo' if dias >= 5 else 'verde')


def _nivel_pct(pct, umbral_amarillo, umbral_rojo):
    return 'rojo' if pct > umbral_rojo else ('amarillo' if pct >= umbral_amarillo else 'verde')


@user_passes_test(lambda u: u.is_active and u.is_superuser)
def sla_capturistas(request):
    now   = timezone.localtime()
    year  = int(request.GET.get('año', now.year))
    month = int(request.GET.get('mes', 0))   # 0 = todo el año

    # ── Base queryset (refs normales con capturista y fecha_pago) ──────────
    qs = Referencia.objects.filter(
        nombre_capturista__gt='',
        fecha_pago__isnull=False,
        es_rectificacion=False,
    )
    if month:
        qs = qs.filter(fecha_pago__year=year, fecha_pago__month=month)
    else:
        qs = qs.filter(fecha_pago__year=year)

    # ── Métricas agregadas por capturista ─────────────────────────────────
    rows = list(
        qs.values('nombre_capturista')
        .annotate(
            total=Count('id'),
            avg_cap_val=Avg(
                ExpressionWrapper(F('fecha_validacion') - F('fecha_captura'),
                                  output_field=DurationField()),
            ),
            avg_cap_pago=Avg(
                ExpressionWrapper(F('fecha_pago') - F('fecha_captura'),
                                  output_field=DurationField()),
            ),
        )
        .order_by('-total')
    )

    # ── Rectificaciones en el mismo período ──────────────────────────────
    rect_qs = Referencia.objects.filter(
        nombre_capturista__gt='',
        es_rectificacion=True,
    )
    if month:
        rect_qs = rect_qs.filter(fecha_pago__year=year, fecha_pago__month=month)
    else:
        rect_qs = rect_qs.filter(fecha_pago__year=year)

    rect_map = {
        r['nombre_capturista']: r['n']
        for r in rect_qs.values('nombre_capturista').annotate(n=Count('id'))
    }

    # ── Glosas (refs con al menos 1 glosa, agrupadas por capturista) ──────
    ref_ids = list(qs.values_list('id', flat=True))
    glosa_map = {
        row['referencia__nombre_capturista']: row['n']
        for row in (
            GlosaRegistro.objects
            .filter(referencia_id__in=ref_ids)
            .values('referencia__nombre_capturista')
            .annotate(n=Count('referencia_id', distinct=True))
        )
    }

    # ── Construir lista final ─────────────────────────────────────────────
    capturistas = []
    for row in rows:
        nombre = (row['nombre_capturista'] or '').strip()
        if not nombre:
            continue
        total       = row['total']
        rect        = rect_map.get(nombre, 0)
        con_glosa   = glosa_map.get(nombre, 0)
        avg_cv      = round(row['avg_cap_val'].days,  1) if row['avg_cap_val']  else None
        avg_cp      = round(row['avg_cap_pago'].days, 1) if row['avg_cap_pago'] else None
        pct_rect    = round(rect      / (total + rect) * 100, 1) if (total + rect) else 0
        pct_glosa   = round(con_glosa / total           * 100, 1) if total          else 0

        capturistas.append({
            'nombre':     nombre,
            'total':      total,
            'rect':       rect,
            'con_glosa':  con_glosa,
            'avg_cv':     avg_cv,
            'avg_cp':     avg_cp,
            'pct_rect':   pct_rect,
            'pct_glosa':  pct_glosa,
            'nivel_cp':   _nivel_cp(avg_cp),
            'nivel_rect': _nivel_pct(pct_rect,  2, 5),
            'nivel_glosa':_nivel_pct(pct_glosa, 5, 15),
        })

    # ── Tarjetas destacadas ───────────────────────────────────────────────
    con_avg = [c for c in capturistas if c['avg_cp'] is not None]
    mas_rapido   = min(con_avg, key=lambda x: x['avg_cp'],   default=None)
    mas_volumen  = capturistas[0] if capturistas else None
    candidatos   = [c for c in capturistas if c['total'] >= 10]
    menor_riesgo = min(candidatos,
                       key=lambda x: (x['pct_glosa'], x['pct_rect']),
                       default=None)

    # ── Tendencia 12 meses (top 5 por volumen) ─────────────────────────
    top5 = [c['nombre'] for c in capturistas[:5]]
    labels_12 = []
    trend_data = {n: [] for n in top5}

    for i in range(11, -1, -1):
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        labels_12.append(_MESES_CORTO[m - 1])

    # Una sola query para los 12 meses del top-5
    from datetime import date as _date
    primer_mes_year  = now.year  if (now.month - 11) > 0 else now.year - 1
    primer_mes_month = (now.month - 11) if (now.month - 11) > 0 else (now.month + 1)
    inicio_trend = _date(primer_mes_year, primer_mes_month, 1)

    trend_qs = (
        Referencia.objects
        .filter(
            nombre_capturista__in=top5,
            fecha_pago__gte=inicio_trend,
            es_rectificacion=False,
        )
        .annotate(mes=TruncMonth('fecha_pago'))
        .values('nombre_capturista', 'mes')
        .annotate(n=Count('id'))
        .order_by('mes')
    )
    # Indexar resultados
    trend_index = {}
    for row in trend_qs:
        key = (row['nombre_capturista'], row['mes'].month, row['mes'].year)
        trend_index[key] = row['n']

    for i, label in enumerate(labels_12):
        m = now.month - (11 - i)
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        for nombre in top5:
            trend_data[nombre].append(trend_index.get((nombre, m, y), 0))

    # ── Años disponibles ──────────────────────────────────────────────────
    años = sorted(set(
        Referencia.objects.filter(fecha_pago__isnull=False)
        .values_list('fecha_pago__year', flat=True).distinct()
    ), reverse=True)

    return render(request, 'referencias/sla_capturistas.html', {
        'capturistas':         capturistas,
        'year':                year,
        'month':               month,
        'años':                años,
        'meses':               list(enumerate(_MESES_SLA, 1)),
        'mas_rapido':          mas_rapido,
        'mas_volumen':         mas_volumen,
        'menor_riesgo':        menor_riesgo,
        'trend_labels_json':   json.dumps(labels_12),
        'trend_data_json':     json.dumps(trend_data),
        'top5':                top5,
    })
