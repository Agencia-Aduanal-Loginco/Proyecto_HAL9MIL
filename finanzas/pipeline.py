from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q


def _mes_actual_y_anterior(hoy=None):
    """Retorna (year, month, prev_year, prev_month) para la ventana móvil de 2 meses."""
    hoy = hoy or date.today()
    year, month = hoy.year, hoy.month
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    return year, month, prev_year, prev_month


def _saldo_factura(factura):
    """Saldo pendiente de una factura: total - suma de pagos aplicados."""
    from django.db.models import Sum

    pagado = factura.documentos_pago.aggregate(total=Sum('imp_pagado'))['total'] or Decimal('0')
    return factura.total - pagado


def calcular_embudo_ar(hoy=None):
    """
    Embudo de Cuentas por Cobrar: facturas emitidas -> timbradas -> cobradas,
    en la ventana móvil de mes actual + mes anterior.
    """
    from .models import Factura

    year, month, prev_year, prev_month = _mes_actual_y_anterior(hoy)

    facturas = Factura.objects.exclude(estado='CANCELADA').filter(
        Q(fecha_emision__year=year, fecha_emision__month=month) |
        Q(fecha_emision__year=prev_year, fecha_emision__month=prev_month) |
        Q(fecha_emision__isnull=True, created_at__year=year, created_at__month=month) |
        Q(fecha_emision__isnull=True, created_at__year=prev_year, created_at__month=prev_month)
    )

    emitidas = facturas.count()
    timbradas_qs = facturas.filter(estado='TIMBRADA')
    timbradas = timbradas_qs.count()
    cobradas = sum(
        1 for f in timbradas_qs if _saldo_factura(f) <= Decimal('0')
    )

    return {'emitidas': emitidas, 'timbradas': timbradas, 'cobradas': cobradas}


def calcular_embudo_ap(hoy=None):
    """
    Embudo de Cuentas por Pagar: XML de proveedor recibidos -> procesados ->
    con póliza generada, en la ventana móvil de mes actual + mes anterior.
    """
    from .models import GastoReferencia, XMLProveedor

    year, month, prev_year, prev_month = _mes_actual_y_anterior(hoy)

    xmls = XMLProveedor.objects.filter(
        Q(fecha_emision__year=year, fecha_emision__month=month) |
        Q(fecha_emision__year=prev_year, fecha_emision__month=prev_month)
    )

    recibidos = xmls.count()
    procesados_qs = xmls.filter(procesado=True)
    procesados = procesados_qs.count()
    con_poliza = procesados_qs.filter(gastos__poliza__isnull=False).distinct().count()

    return {'recibidos': recibidos, 'procesados': procesados, 'con_poliza': con_poliza}


def calcular_tendencia_semanal(semanas=8, hoy=None):
    """
    Series semanales (lunes a domingo) de facturas timbradas y pólizas
    generadas, para las últimas `semanas` semanas terminando en la semana
    que contiene `hoy`.
    """
    from .models import Factura, PolizaContable

    hoy = hoy or date.today()
    lunes_actual = hoy - timedelta(days=hoy.weekday())

    labels = []
    facturas_por_semana = []
    polizas_por_semana = []

    for i in range(semanas - 1, -1, -1):
        inicio_semana = lunes_actual - timedelta(weeks=i)
        fin_semana = inicio_semana + timedelta(days=6)
        labels.append(inicio_semana.isoformat())
        facturas_por_semana.append(
            Factura.objects.filter(
                estado='TIMBRADA',
                fecha_emision__date__gte=inicio_semana,
                fecha_emision__date__lte=fin_semana,
            ).count()
        )
        polizas_por_semana.append(
            PolizaContable.objects.filter(
                fecha__gte=inicio_semana, fecha__lte=fin_semana,
            ).count()
        )

    return {
        'labels': labels,
        'facturas_timbradas': facturas_por_semana,
        'polizas_generadas': polizas_por_semana,
    }
