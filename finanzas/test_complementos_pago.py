import tempfile
from datetime import datetime
from decimal import Decimal

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from referencias.models import Referencia

from .models import ComplementoPago, XMLProveedor

MEDIA_TMP = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ComplementoPagoModelTests(TestCase):
    def test_crea_complemento_pendiente_sin_factura(self):
        c = ComplementoPago.objects.create(
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            uuid_factura_relacionada='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('11094.00'),
        )
        c.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=True)
        self.assertEqual(c.estado, 'PENDIENTE')
        self.assertIsNone(c.factura)
        self.assertEqual(c.moneda_pago, 'MXN')

    def test_liga_a_una_factura_existente(self):
        referencia = Referencia.objects.create(
            num_refe='LCRR0900/26', patente='1656', prefijo='LCRR',
        )
        factura = XMLProveedor(
            referencia=referencia,
            uuid_fiscal='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 8, 8, 0, 0),
            rfc_emisor='LCT030408U39', nombre_emisor='L C TERMINAL',
            rfc_receptor='CIN220216BS2',
            subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
            tipo_comprobante='I',
        )
        factura.xml_file.save('f.xml', ContentFile(b'<x/>'), save=False)
        factura.save()

        c = ComplementoPago.objects.create(
            factura=factura,
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            uuid_factura_relacionada=factura.uuid_fiscal,
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'),
            estado='IDENTIFICADO',
        )
        c.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=True)
        self.assertEqual(factura.complementos_pago.count(), 1)
        self.assertEqual(factura.complementos_pago.first(), c)

    def test_uuid_complemento_es_unico(self):
        ComplementoPago.objects.create(
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'),
        )
        with self.assertRaises(Exception):
            ComplementoPago.objects.create(
                uuid_complemento='44444444-4444-4444-4444-444444444444',
                fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
                rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
                monto_pagado=Decimal('50.00'),
            )
