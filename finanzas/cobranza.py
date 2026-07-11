import logging
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import Sum
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

ASUNTOS = {
    '15d':    'Recordatorio de pago — Reiki Logística',
    '30d':    'Saldo pendiente — Reiki Logística',
    '60d':    'Aviso urgente de saldo vencido — Reiki Logística',
    'manual': 'Estado de cuenta — Reiki Logística',
}


def calcular_saldo_factura(factura):
    """Retorna el saldo pendiente de una Factura (total - sum de pagos aplicados)."""
    pagado = (
        factura.documentos_pago
        .aggregate(t=Sum('imp_pagado'))['t'] or Decimal('0')
    )
    return factura.total - pagado


def buscar_cliente_de_factura(factura):
    """Retorna el Cliente asociado a la primera Referencia de la Factura, o None."""
    from clientes.models import Cliente
    ref = factura.referencias.first()
    if not ref:
        return None
    return Cliente.objects.filter(cve_cliente=ref.cve_cliente).first()


def calcular_saldos_por_cliente():
    """
    Retorna lista de dicts, uno por cliente con facturas TIMBRADAS pendientes,
    ordenada por días de antigüedad descendente (más vieja primero).

    Cada dict contiene:
        cve_cliente: str
        nombre: str
        cliente: Cliente | None
        facturas: list[dict]  — cada uno con keys: factura, saldo, dias, fecha_base
        saldo_total: Decimal
        dias_mayor: int
        ultimo_recordatorio: datetime | None
    """
    from .models import Factura

    facturas_timbradas = (
        Factura.objects
        .filter(estado='TIMBRADA')
        .prefetch_related('referencias', 'documentos_pago', 'recordatorios')
    )

    hoy = date.today()
    clientes = {}

    for factura in facturas_timbradas:
        saldo = calcular_saldo_factura(factura)
        if saldo <= Decimal('0'):
            continue

        ref = factura.referencias.first()
        if not ref:
            continue

        cve = ref.cve_cliente
        fecha_base = (
            factura.fecha_emision.date()
            if factura.fecha_emision
            else factura.created_at.date()
        )
        dias = (hoy - fecha_base).days

        ultimo = factura.recordatorios.first()  # ordering = ['-enviado_en']

        if cve not in clientes:
            from clientes.models import Cliente
            clientes[cve] = {
                'cve_cliente': cve,
                'nombre': ref.nombre_cliente,
                'cliente': Cliente.objects.filter(cve_cliente=cve).first(),
                'facturas': [],
                'saldo_total': Decimal('0'),
                'dias_mayor': 0,
                'ultimo_recordatorio': None,
            }

        clientes[cve]['facturas'].append({
            'factura': factura,
            'saldo': saldo,
            'dias': dias,
            'fecha_base': fecha_base,
        })
        clientes[cve]['saldo_total'] += saldo
        clientes[cve]['dias_mayor'] = max(clientes[cve]['dias_mayor'], dias)
        if ultimo and (
            clientes[cve]['ultimo_recordatorio'] is None
            or ultimo.enviado_en > clientes[cve]['ultimo_recordatorio']
        ):
            clientes[cve]['ultimo_recordatorio'] = ultimo.enviado_en

    return sorted(clientes.values(), key=lambda x: x['dias_mayor'], reverse=True)


def _enviar_email(cliente, facturas_info, tipo, usuario=None):
    """
    Envía el email de cobranza y crea RecordatorioCobranza para cada factura incluida.
    facturas_info: list[dict] con keys: factura, saldo, dias, fecha_base
    Retorna True si el envío fue exitoso.
    """
    from .models import RecordatorioCobranza

    html = render_to_string('finanzas/email_recordatorio_cobranza.html', {
        'cliente': cliente,
        'facturas_info': facturas_info,
        'tipo': tipo,
        'saldo_total': sum(fi['saldo'] for fi in facturas_info),
    })

    to = [cliente.email_cobranza]
    cc = [cliente.email_cobranza_cc] if cliente.email_cobranza_cc else []

    exitoso = True
    error_msg = ''
    try:
        msg = EmailMultiAlternatives(
            subject=ASUNTOS.get(tipo, ASUNTOS['manual']),
            body='Este correo requiere un cliente con soporte HTML.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=to,
            cc=cc,
        )
        msg.attach_alternative(html, 'text/html')
        msg.send()
    except Exception as e:
        exitoso = False
        error_msg = str(e)
        logger.error('[Cobranza] Error enviando email a %s: %s', cliente.email_cobranza, e)

    for fi in facturas_info:
        RecordatorioCobranza.objects.create(
            factura=fi['factura'],
            tipo=tipo,
            enviado_por=usuario,
            exitoso=exitoso,
            error_msg=error_msg,
        )

    return exitoso


def enviar_recordatorio_factura(factura, cliente, tipo, usuario=None):
    """
    Envía recordatorio para una sola factura (usado por el scheduler).
    Retorna True si exitoso.
    """
    saldo = calcular_saldo_factura(factura)
    fecha_base = (
        factura.fecha_emision.date()
        if factura.fecha_emision
        else factura.created_at.date()
    )
    dias = (date.today() - fecha_base).days
    facturas_info = [{'factura': factura, 'saldo': saldo, 'dias': dias, 'fecha_base': fecha_base}]
    return _enviar_email(cliente, facturas_info, tipo, usuario)


def enviar_estado_cuenta_cliente(cliente, usuario=None):
    """
    Envía estado de cuenta consolidado con TODAS las facturas pendientes del cliente.
    Usado para envíos manuales desde el panel.
    Retorna True si exitoso (y hay facturas pendientes), False si no hay facturas o falla.
    """
    from .models import Factura
    hoy = date.today()

    facturas = (
        Factura.objects
        .filter(estado='TIMBRADA', referencias__cve_cliente=cliente.cve_cliente)
        .distinct()
        .prefetch_related('documentos_pago')
    )

    facturas_info = []
    for factura in facturas:
        saldo = calcular_saldo_factura(factura)
        if saldo <= Decimal('0'):
            continue
        fecha_base = (
            factura.fecha_emision.date()
            if factura.fecha_emision
            else factura.created_at.date()
        )
        facturas_info.append({
            'factura': factura,
            'saldo': saldo,
            'dias': (hoy - fecha_base).days,
            'fecha_base': fecha_base,
        })

    if not facturas_info:
        return False

    return _enviar_email(cliente, facturas_info, 'manual', usuario)
