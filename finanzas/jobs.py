import logging
from datetime import date
from decimal import Decimal

logger = logging.getLogger(__name__)

HITOS = [(15, '15d'), (30, '30d'), (60, '60d')]


def ejecutar_cobranza_automatica():
    """
    Job diario: revisa todas las Facturas TIMBRADAS con saldo > 0 y envía
    recordatorios automáticos a 15, 30 y 60 días desde la fecha de emisión.
    Nunca reenvía un tipo de recordatorio ya enviado para la misma factura.
    """
    from .models import Factura
    from .cobranza import (
        calcular_saldo_factura,
        buscar_cliente_de_factura,
        enviar_recordatorio_factura,
    )

    hoy = date.today()
    enviados = 0
    omitidos_saldo = 0
    omitidos_email = 0

    facturas = (
        Factura.objects
        .filter(estado='TIMBRADA')
        .prefetch_related('referencias', 'documentos_pago', 'recordatorios')
    )

    for factura in facturas:
        saldo = calcular_saldo_factura(factura)
        if saldo <= Decimal('0'):
            omitidos_saldo += 1
            continue

        fecha_base = (
            factura.fecha_emision.date()
            if factura.fecha_emision
            else factura.created_at.date()
        )
        dias = (hoy - fecha_base).days

        cliente = buscar_cliente_de_factura(factura)
        if not cliente or not cliente.email_cobranza:
            omitidos_email += 1
            continue

        tipos_enviados = set(
            factura.recordatorios
            .exclude(tipo='manual')
            .values_list('tipo', flat=True)
        )

        for hito, tipo in reversed(HITOS):  # 60d → 30d → 15d: enviar el más urgente primero
            if dias >= hito and tipo not in tipos_enviados:
                exitoso = enviar_recordatorio_factura(factura, cliente, tipo)
                if exitoso:
                    enviados += 1
                    logger.info(
                        '[Cobranza] Recordatorio %s enviado — Factura %s → %s',
                        tipo, factura.pk, cliente.email_cobranza,
                    )
                else:
                    logger.warning('[Cobranza] Fallo al enviar %s — Factura %s', tipo, factura.pk)
                break  # un recordatorio por factura por ejecución del job

    logger.info(
        '[Cobranza] Job completado: %d enviados, %d sin saldo, %d sin email.',
        enviados, omitidos_saldo, omitidos_email,
    )
