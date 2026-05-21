import calendar
from datetime import date
from django.db import models
from django.db.models import Count
from django.db.models.functions import TruncMonth
from referencias.models import Referencia, Contenedor, GuiaBL


def _promedio_dias_despacho(refs_qs):
    """Días promedio entre fecha_arribo y fecha_pago para pedimentos con pago real (num_operacion + linea_captura)."""
    pagadas = refs_qs.filter(
        num_operacion__gt='',
        linea_captura__gt='',
        fecha_arribo__isnull=False,
        fecha_pago__isnull=False,
    ).values_list('fecha_arribo', 'fecha_pago')
    dias = [(pago - arribo).days for arribo, pago in pagadas if pago >= arribo]
    if not dias:
        return None, 0
    return round(sum(dias) / len(dias), 1), len(dias)

NOMBRES_MESES = ['Enero','Febrero','Marzo','Abril','Mayo','Junio',
                 'Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre']


def _mes_count(year: int, month: int) -> int:
    return Referencia.objects.filter(
        fecha_pago__year=year, fecha_pago__month=month, es_rectificacion=False
    ).count()


def _monthly_array(year: int) -> list:
    data = (
        Referencia.objects
        .filter(fecha_pago__year=year, es_rectificacion=False)
        .annotate(m=TruncMonth('fecha_pago'))
        .values('m').annotate(c=Count('id')).order_by('m')
    )
    arr = [0] * 12
    for row in data:
        arr[row['m'].month - 1] = row['c']
    return arr


def _proyeccion_mes(month_idx: int) -> int:
    """Returns projected value for month_idx (0-based) using 2024-2025 weighted avg."""
    m24 = _monthly_array(2024)
    m25 = _monthly_array(2025)
    avg24 = sum(m24) / 12 if sum(m24) > 0 else 0
    avg25 = sum(m25) / 12 if sum(m25) > 0 else 0
    base = (avg24 + avg25) / 2 if (avg24 + avg25) > 0 else 1
    growth = ((avg25 - avg24) / avg24) if avg24 > 0 else 0
    pred_avg = avg25 * (1 + growth)
    seasonal = (m24[month_idx] + m25[month_idx]) / (avg24 + avg25) if base > 0 else 1.0
    return int(pred_avg * seasonal)


# ── Datos para reporte semanal ───────────────────────────────────────────────

def get_datos_semana(inicio: date, fin: date) -> dict:
    validadas = Referencia.objects.filter(
        fecha_validacion__gte=inicio, fecha_validacion__lte=fin, es_rectificacion=False,
    )
    pagadas = Referencia.objects.filter(
        fecha_pago__gte=inicio, fecha_pago__lte=fin, es_rectificacion=False,
    )
    conts = Contenedor.objects.filter(
        referencia__fecha_pago__gte=inicio,
        referencia__fecha_pago__lte=fin,
        referencia__es_rectificacion=False,
    )
    guias = GuiaBL.objects.filter(
        referencia__fecha_pago__gte=inicio,
        referencia__fecha_pago__lte=fin,
        referencia__es_rectificacion=False,
    )

    return {
        'periodo_inicio': inicio,
        'periodo_fin': fin,
        'validadas_total': validadas.count(),
        'validadas_por_patente': list(
            validadas.values('patente', 'prefijo').annotate(total=Count('id')).order_by('-total')
        ),
        'pagadas_total': pagadas.count(),
        'pagadas_por_patente': list(
            pagadas.values('patente', 'prefijo').annotate(total=Count('id')).order_by('-total')
        ),
        'contenedores_total': conts.count(),
        'contenedores_por_tipo': list(
            conts.values('tipo').annotate(total=Count('id')).order_by('-total')
        ),
        'guias_total': guias.count(),
        'top_clientes': list(
            pagadas.values('nombre_cliente').annotate(total=Count('id')).order_by('-total')[:5]
        ),
        'pendientes_pago': Referencia.objects.filter(
            fecha_validacion__isnull=False, fecha_pago__isnull=True, es_rectificacion=False,
        ).count(),
        'rectificaciones_semana': Referencia.objects.filter(
            fecha_validacion__gte=inicio, fecha_validacion__lte=fin, es_rectificacion=True,
        ).count(),
        'claves_pedimento': list(
            pagadas.values('clave_pedimento').annotate(total=Count('id')).order_by('-total')
        ),
        'por_capturista': list(
            validadas.values('nombre_capturista').annotate(
                capturadas=Count('id'),
                pagadas=Count('id', filter=models.Q(num_operacion__gt='', linea_captura__gt='')),
                pendientes=Count('id', filter=~models.Q(num_operacion__gt='', linea_captura__gt='')),
            ).order_by('-capturadas')
        ),
    }


# ── Datos para reporte mensual ───────────────────────────────────────────────

def get_datos_mes(year: int, month: int) -> dict:
    _, last_day = calendar.monthrange(year, month)
    inicio = date(year, month, 1)
    fin = date(year, month, last_day)

    prev_month = 12 if month == 1 else month - 1
    prev_year_of_prev_month = year - 1 if month == 1 else year
    prev_year = year - 1

    real = _mes_count(year, month)
    proyectado = _proyeccion_mes(month - 1)
    real_mes_anterior = _mes_count(prev_year_of_prev_month, prev_month)
    real_año_pasado = _mes_count(prev_year, month)

    refs_mes = Referencia.objects.filter(
        fecha_pago__gte=inicio, fecha_pago__lte=fin, es_rectificacion=False,
    )
    promedio_dias_despacho, pedimentos_con_pago_real = _promedio_dias_despacho(refs_mes)
    conts_mes = Contenedor.objects.filter(
        referencia__fecha_pago__gte=inicio,
        referencia__fecha_pago__lte=fin,
        referencia__es_rectificacion=False,
    )

    # Acumulado año actual mes a mes
    acumulado_año = []
    for m in range(1, month + 1):
        acumulado_año.append({
            'mes': NOMBRES_MESES[m - 1][:3],
            'real': _mes_count(year, m),
            'proyectado': _proyeccion_mes(m - 1),
        })

    return {
        'year': year,
        'month': month,
        'nombre_mes': NOMBRES_MESES[month - 1],
        'inicio': inicio,
        'fin': fin,
        # Real vs proyectado
        'real': real,
        'proyectado': proyectado,
        'delta_proyectado': real - proyectado,
        'pct_proyectado': round(real / proyectado * 100, 1) if proyectado > 0 else 0,
        # vs mes anterior
        'prev_month': prev_month,
        'prev_year_of_prev_month': prev_year_of_prev_month,
        'nombre_mes_anterior': NOMBRES_MESES[prev_month - 1],
        'real_mes_anterior': real_mes_anterior,
        'delta_mes_anterior': real - real_mes_anterior,
        'pct_mes_anterior': round(real / real_mes_anterior * 100, 1) if real_mes_anterior > 0 else 0,
        # vs mismo mes año pasado
        'prev_year': prev_year,
        'real_año_pasado': real_año_pasado,
        'delta_año_pasado': real - real_año_pasado,
        'pct_año_pasado': round(real / real_año_pasado * 100, 1) if real_año_pasado > 0 else 0,
        # Operaciones
        'validadas_mes': Referencia.objects.filter(
            fecha_validacion__gte=inicio, fecha_validacion__lte=fin, es_rectificacion=False,
        ).count(),
        'contenedores_total': conts_mes.count(),
        'contenedores_por_tipo': list(
            conts_mes.values('tipo').annotate(total=Count('id')).order_by('-total')
        ),
        'guias_total': GuiaBL.objects.filter(
            referencia__fecha_pago__gte=inicio,
            referencia__fecha_pago__lte=fin,
            referencia__es_rectificacion=False,
        ).count(),
        'rectificaciones_mes': Referencia.objects.filter(
            fecha_validacion__gte=inicio, fecha_validacion__lte=fin, es_rectificacion=True,
        ).count(),
        'pendientes_pago': Referencia.objects.filter(
            fecha_validacion__isnull=False, fecha_pago__isnull=True, es_rectificacion=False,
        ).count(),
        # Desgloses
        'por_patente': list(
            refs_mes.values('patente', 'prefijo').annotate(total=Count('id')).order_by('-total')
        ),
        'claves_pedimento': list(
            refs_mes.values('clave_pedimento').annotate(total=Count('id')).order_by('-total')
        ),
        'top_clientes': list(
            refs_mes.values('nombre_cliente').annotate(total=Count('id')).order_by('-total')[:8]
        ),
        'acumulado_año': acumulado_año,
        'promedio_dias_despacho': promedio_dias_despacho,
        'pedimentos_con_pago_real': pedimentos_con_pago_real,
    }
