import calendar
import json
from collections import defaultdict
from datetime import date
from urllib.parse import quote

from django.contrib.auth.decorators import login_required, user_passes_test

_superuser_required = user_passes_test(lambda u: u.is_active and u.is_superuser)
from django.db.models import Count
from django.shortcuts import redirect, render
from django.utils import timezone

from referencias.models import GlosaRegistro, Referencia

MESES = [
    'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
    'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
]


def _stats_mes(year, month, nombre_filter=None):
    """Métricas por cliente para un mes. O(~5 queries independiente del nº de clientes)."""
    _, last_day = calendar.monthrange(year, month)
    inicio = date(year, month, 1)
    fin    = date(year, month, last_day)

    qs = Referencia.objects.filter(
        fecha_pago__gte=inicio,
        fecha_pago__lte=fin,
        es_rectificacion=False,
    ).exclude(nombre_cliente='')

    if nombre_filter:
        qs = qs.filter(nombre_cliente__in=nombre_filter)

    # Totales
    totales = {
        r['nombre_cliente']: r['total']
        for r in qs.values('nombre_cliente').annotate(total=Count('id'))
    }
    if not totales:
        return []

    # Días para promedios (una sola query)
    cp_days = defaultdict(list)
    cv_days = defaultdict(list)
    vp_days = defaultdict(list)
    for nombre, fc, fv, fp in qs.values_list(
        'nombre_cliente', 'fecha_captura', 'fecha_validacion', 'fecha_pago'
    ):
        if fc and fp and fp >= fc:
            cp_days[nombre].append((fp - fc).days)
        if fc and fv and fv >= fc:
            cv_days[nombre].append((fv - fc).days)
        if fv and fp and fp >= fv:
            vp_days[nombre].append((fp - fv).days)

    # Glosas (refs con al menos una glosa, agrupado por cliente)
    all_ids = list(qs.values_list('id', flat=True))
    glosas_map = defaultdict(int)
    for row in (
        GlosaRegistro.objects
        .filter(referencia_id__in=all_ids)
        .values('referencia__nombre_cliente')
        .annotate(n=Count('referencia_id', distinct=True))
    ):
        glosas_map[row['referencia__nombre_cliente']] = row['n']

    # Desglose por patente
    pat_map = defaultdict(list)
    for row in (
        qs.values('nombre_cliente', 'prefijo')
        .annotate(total=Count('id'))
        .order_by('nombre_cliente', '-total')
    ):
        pat_map[row['nombre_cliente']].append(
            {'prefijo': row['prefijo'], 'total': row['total']}
        )

    result = []
    for nombre, total in sorted(totales.items(), key=lambda x: -x[1]):
        cp = cp_days[nombre]
        cv = cv_days[nombre]
        vp = vp_days[nombre]
        con_glosa = glosas_map[nombre]
        result.append({
            'nombre':                nombre,
            'total':                 total,
            'avg_captura_pago':      round(sum(cp) / len(cp), 1) if cp else None,
            'avg_captura_validacion':round(sum(cv) / len(cv), 1) if cv else None,
            'avg_validacion_pago':   round(sum(vp) / len(vp), 1) if vp else None,
            'con_glosa':             con_glosa,
            'pct_glosa':             round(con_glosa / total * 100) if total else 0,
            'por_patente':           pat_map[nombre],
        })
    return result


def _tendencia_12meses(nombre_cliente, year, month):
    """Últimos 12 meses de despacho para un cliente."""
    meses = []
    for i in range(11, -1, -1):
        m = month - i
        y = year
        while m <= 0:
            m += 12
            y -= 1
        _, ld = calendar.monthrange(y, m)
        inicio = date(y, m, 1)
        fin    = date(y, m, ld)

        refs_mes = list(
            Referencia.objects.filter(
                nombre_cliente=nombre_cliente,
                fecha_pago__gte=inicio,
                fecha_pago__lte=fin,
                es_rectificacion=False,
            ).values_list('fecha_captura', 'fecha_validacion', 'fecha_pago')
        )

        total = len(refs_mes)
        cp, cv, vp = [], [], []
        for fc, fv, fp in refs_mes:
            if fc and fp and fp >= fc:
                cp.append((fp - fc).days)
            if fc and fv and fv >= fc:
                cv.append((fv - fc).days)
            if fv and fp and fp >= fv:
                vp.append((fp - fv).days)

        meses.append({
            'year':  y,
            'month': m,
            'label': MESES[m - 1][:3],
            'total': total,
            'avg_captura_pago':      round(sum(cp) / len(cp), 1) if cp else None,
            'avg_captura_validacion':round(sum(cv) / len(cv), 1) if cv else None,
            'avg_validacion_pago':   round(sum(vp) / len(vp), 1) if vp else None,
        })
    return meses


@_superuser_required
def lista(request):
    now   = timezone.localtime()
    year  = int(request.GET.get('año', now.year))
    month = int(request.GET.get('mes', now.month))
    q     = request.GET.get('q', '').strip()

    clientes = _stats_mes(year, month)
    if q:
        clientes = [c for c in clientes if q.lower() in c['nombre'].lower()]

    años_disponibles = sorted(set(
        Referencia.objects
        .filter(fecha_pago__isnull=False)
        .values_list('fecha_pago__year', flat=True)
        .distinct()
    ), reverse=True)

    return render(request, 'clientes/lista.html', {
        'clientes':        clientes,
        'year':            year,
        'month':           month,
        'mes_nombre':      MESES[month - 1],
        'q':               q,
        'años':            años_disponibles,
        'meses':           list(enumerate(MESES, 1)),
        'total_clientes':  len(clientes),
    })


@_superuser_required
def detalle(request, nombre):
    now   = timezone.localtime()
    year  = int(request.GET.get('año', now.year))
    month = int(request.GET.get('mes', now.month))

    stats_list = _stats_mes(year, month, nombre_filter=[nombre])
    stats      = stats_list[0] if stats_list else None
    tendencia  = _tendencia_12meses(nombre, year, month)

    return render(request, 'clientes/detalle.html', {
        'nombre':      nombre,
        'stats':       stats,
        'tendencia':   tendencia,
        'year':        year,
        'month':       month,
        'mes_nombre':  MESES[month - 1],
        'años':        sorted(set(
            Referencia.objects
            .filter(fecha_pago__isnull=False)
            .values_list('fecha_pago__year', flat=True)
            .distinct()
        ), reverse=True),
        'meses':       list(enumerate(MESES, 1)),
        'tendencia_labels_json': json.dumps([t['label'] for t in tendencia]),
        'tendencia_refs_json':   json.dumps([t['total'] for t in tendencia]),
        'tendencia_avg_json':    json.dumps([t['avg_captura_pago'] for t in tendencia]),
    })


@_superuser_required
def reporte_pdf(request):
    now   = timezone.localtime()
    year  = int(request.GET.get('año', now.year))
    month = int(request.GET.get('mes', now.month))
    nombres_raw = request.GET.get('nombres', '')
    nombres = [n.strip() for n in nombres_raw.split('|') if n.strip()]

    if not nombres:
        return redirect('clientes_lista')

    clientes = _stats_mes(year, month, nombre_filter=nombres)
    orden    = {n: i for i, n in enumerate(nombres)}
    clientes = sorted(clientes, key=lambda x: orden.get(x['nombre'], 999))

    return render(request, 'clientes/reporte_pdf.html', {
        'clientes':   clientes,
        'year':       year,
        'month':      month,
        'mes_nombre': MESES[month - 1],
    })
