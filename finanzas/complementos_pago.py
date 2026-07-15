"""Complementos de Pago (CFDI tipo P): ligado con la factura que pagan.

Un complemento de pago no es una factura; su <DoctoRelacionado> trae el UUID
de la factura (XMLProveedor) que fue pagada. Ver spec:
docs/superpowers/specs/2026-07-15-complementos-pago-design.md
"""
from django.core.files.base import ContentFile

from .cfdi_parser import parsear_complemento_pago
from .models import ComplementoPago, XMLProveedor


def procesar_complemento(root, *, uuid_complemento, fecha, rfc_emisor,
                          nombre_emisor, nombre_archivo, xml_bytes,
                          pdf_bytes=None, referencia_sugerida=None):
    """Crea el ComplementoPago a partir de un CFDI ya identificado como tipo P.

    Retorna el ComplementoPago creado (estado PENDIENTE, IDENTIFICADO o
    REVISION). Lanza ValueError si el complemento no trae DoctoRelacionado
    (propagada desde parsear_complemento_pago).
    """
    doctos = parsear_complemento_pago(root)
    primero = doctos[0]

    estado = 'REVISION' if len(doctos) > 1 else 'PENDIENTE'
    factura = None
    if estado == 'PENDIENTE':
        factura = XMLProveedor.objects.filter(
            uuid_fiscal=primero['uuid_factura']
        ).first()
        if factura:
            estado = 'IDENTIFICADO'

    complemento = ComplementoPago(
        factura=factura,
        uuid_complemento=uuid_complemento,
        uuid_factura_relacionada=primero['uuid_factura'],
        fecha_emision=fecha,
        rfc_emisor=rfc_emisor,
        nombre_emisor=nombre_emisor,
        monto_pagado=primero['imp_pagado'],
        moneda_pago=primero['moneda_pago'],
        estado=estado,
        referencia_sugerida=referencia_sugerida,
    )
    complemento.xml_file.save(nombre_archivo, ContentFile(xml_bytes), save=False)
    if pdf_bytes:
        stem = nombre_archivo.rsplit('.', 1)[0]
        complemento.pdf_file.save(f'{stem}.pdf', ContentFile(pdf_bytes), save=False)
    complemento.save()
    return complemento


def conciliar_pendientes(xml_obj):
    """Liga automáticamente los ComplementoPago PENDIENTES que esperaban esta
    factura (por UUID). Se llama tras guardar cualquier XMLProveedor nuevo."""
    ComplementoPago.objects.filter(
        estado='PENDIENTE', uuid_factura_relacionada=xml_obj.uuid_fiscal,
    ).update(factura=xml_obj, estado='IDENTIFICADO')
