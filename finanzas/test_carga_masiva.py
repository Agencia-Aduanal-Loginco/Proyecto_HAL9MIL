from datetime import datetime
from decimal import Decimal

from django.core.files.base import ContentFile
from django.test import TestCase

from .models import XMLProveedor


def _crear_xml_proveedor(**extra):
    defaults = dict(
        uuid_fiscal='135088fd-f6a7-4313-9d6a-3d15ee966df1',
        fecha_emision=datetime(2026, 7, 8, 8, 53, 11),
        rfc_emisor='LCT030408U39',
        nombre_emisor='L C TERMINAL',
        rfc_receptor='CIN220216BS2',
        subtotal=Decimal('9563.79'),
        iva=Decimal('1530.21'),
        total=Decimal('11094.00'),
        tipo_comprobante='I',
    )
    defaults.update(extra)
    obj = XMLProveedor(**defaults)
    obj.xml_file.save('prueba.xml', ContentFile(b'<x/>'), save=False)
    obj.save()
    return obj


class XMLProveedorCamposTests(TestCase):
    def test_campos_de_asignacion_con_defaults(self):
        obj = _crear_xml_proveedor()
        self.assertEqual(obj.estado_asignacion, 'PENDIENTE')
        self.assertEqual(obj.motivo_pendiente, '')
        self.assertFalse(obj.pdf_file)

    def test_acepta_pdf_y_estado_asignado(self):
        obj = _crear_xml_proveedor(
            estado_asignacion='ASIGNADO',
            motivo_pendiente='',
        )
        obj.pdf_file.save('prueba.pdf', ContentFile(b'%PDF'), save=True)
        obj.refresh_from_db()
        self.assertEqual(obj.estado_asignacion, 'ASIGNADO')
        self.assertTrue(obj.pdf_file)
