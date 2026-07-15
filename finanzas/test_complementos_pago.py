import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from referencias.models import Referencia

from .models import ComplementoPago, XMLProveedor
from .cfdi_de_prueba import cfdi_pago
from .cfdi_parser import parsear_complemento_pago

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


class ParsearComplementoPagoTests(TestCase):
    def test_extrae_un_docto_relacionado(self):
        root = ET.fromstring(cfdi_pago(
            uuid_factura='11111111-1111-1111-1111-111111111111',
            monto='11094.00', moneda='MXN',
        ))
        doctos = parsear_complemento_pago(root)
        self.assertEqual(len(doctos), 1)
        self.assertEqual(doctos[0]['uuid_factura'], '11111111-1111-1111-1111-111111111111')
        self.assertEqual(doctos[0]['imp_pagado'], Decimal('11094.00'))
        self.assertEqual(doctos[0]['moneda_pago'], 'MXN')

    def test_extrae_varios_doctos_relacionados(self):
        root = ET.fromstring(cfdi_pago(
            uuid_factura='11111111-1111-1111-1111-111111111111',
            uuids_factura_extra=['22222222-2222-2222-2222-222222222222'],
        ))
        doctos = parsear_complemento_pago(root)
        self.assertEqual(len(doctos), 2)

    def test_sin_nodo_pagos_lanza_valueerror(self):
        root = ET.fromstring(
            '<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" '
            'Version="4.0" Fecha="2026-07-10T12:00:00" TipoDeComprobante="P" '
            'Total="0" Moneda="XXX"><cfdi:Emisor Rfc="AAA010101AAA" '
            'Nombre="X"/><cfdi:Receptor Rfc="BBB010101BBB" Nombre="Y" '
            'UsoCFDI="CP01"/></cfdi:Comprobante>'
        )
        with self.assertRaises(ValueError):
            parsear_complemento_pago(root)

    def test_extrae_moneda_correcta_doctorrelacionado(self):
        """Pin: moneda_pago debe leer MonedaDR de DoctoRelacionado, no MonedaP."""
        root = ET.fromstring(cfdi_pago(
            uuid_factura='11111111-1111-1111-1111-111111111111',
            monto='5000.00', moneda='USD',
        ))
        doctos = parsear_complemento_pago(root)
        self.assertEqual(len(doctos), 1)
        self.assertEqual(doctos[0]['moneda_pago'], 'USD')

    def test_pago10_cfdi33_fallback(self):
        """Cobertura: fallback pago10 (CFDI 3.3) cuando pago20 no existe."""
        # CFDI 3.3 con complemento pago10 (no pago20)
        cfdi33_pago = '''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/3"
    Version="3.3" Fecha="2026-07-10T12:00:00" Total="0"
    TipoDeComprobante="P" LugarExpedicion="06600">
  <cfdi:Emisor Rfc="CIN220216BS2" Nombre="CACIPA INTERNACIONAL" RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="LCT030408U39" Nombre="L C TERMINAL" UsoCFDI="CP01" RegimenFiscalReceptor="601"/>
  <cfdi:Conceptos>
    <cfdi:Concepto ClaveProdServ="84111506" Cantidad="1" ClaveUnidad="ACT" Descripcion="Pago" ValorUnitario="0" Importe="0" ObjetoImp="01"/>
  </cfdi:Conceptos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="1.1" UUID="55555555-5555-5555-5555-555555555555" FechaTimbrado="2026-07-10T12:00:05"/>
    <pago10:Pagos xmlns:pago10="http://www.sat.gob.mx/Pagos" Version="1.0">
      <pago10:Pago FechaPago="2026-07-10T12:00:00" FormaDePagoP="03" MonedaP="MXN" Monto="7000.00">
        <pago10:DoctoRelacionado IdDocumento="11111111-1111-1111-1111-111111111111" MonedaDR="MXN" NumParcialidad="1" ImpSaldoAnt="7000.00" ImpPagado="7000.00" ImpSaldoInsoluto="0"/>
      </pago10:Pago>
    </pago10:Pagos>
  </cfdi:Complemento>
</cfdi:Comprobante>'''
        root = ET.fromstring(cfdi33_pago)
        doctos = parsear_complemento_pago(root)
        self.assertEqual(len(doctos), 1)
        self.assertEqual(doctos[0]['uuid_factura'], '11111111-1111-1111-1111-111111111111')
        self.assertEqual(doctos[0]['imp_pagado'], Decimal('7000.00'))
        self.assertEqual(doctos[0]['moneda_pago'], 'MXN')

    def test_pagos_sin_doctos_relacionados_lanza_valueerror(self):
        """Cobertura: Pagos presente pero sin DoctoRelacionado lanza ValueError."""
        cfdi_sin_doctos = '''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
    Version="4.0" Fecha="2026-07-10T12:00:00" Moneda="XXX" Total="0"
    TipoDeComprobante="P" LugarExpedicion="06600">
  <cfdi:Emisor Rfc="CIN220216BS2" Nombre="CACIPA INTERNACIONAL" RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="LCT030408U39" Nombre="L C TERMINAL" UsoCFDI="CP01" DomicilioFiscalReceptor="06600" RegimenFiscalReceptor="601"/>
  <cfdi:Conceptos>
    <cfdi:Concepto ClaveProdServ="84111506" Cantidad="1" ClaveUnidad="ACT" Descripcion="Pago" ValorUnitario="0" Importe="0" ObjetoImp="01"/>
  </cfdi:Conceptos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="1.1" UUID="66666666-6666-6666-6666-666666666666" FechaTimbrado="2026-07-10T12:00:05"/>
    <pago20:Pagos xmlns:pago20="http://www.sat.gob.mx/Pagos20" Version="2.0">
      <pago20:Totales MontoTotalPagos="0"/>
      <pago20:Pago FechaPago="2026-07-10T12:00:00" FormaDePagoP="03" MonedaP="MXN" Monto="0">
      </pago20:Pago>
    </pago20:Pagos>
  </cfdi:Complemento>
</cfdi:Comprobante>'''
        root = ET.fromstring(cfdi_sin_doctos)
        with self.assertRaises(ValueError) as cm:
            parsear_complemento_pago(root)
        self.assertIn('DoctoRelacionado', str(cm.exception))


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ProcesarComplementoTests(TestCase):
    def test_liga_de_inmediato_si_la_factura_ya_existe(self):
        from .complementos_pago import procesar_complemento

        factura = XMLProveedor(
            uuid_fiscal='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 8, 8, 0, 0),
            rfc_emisor='LCT030408U39', nombre_emisor='L C TERMINAL',
            rfc_receptor='CIN220216BS2',
            subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
            tipo_comprobante='I',
        )
        factura.xml_file.save('f.xml', ContentFile(b'<x/>'), save=False)
        factura.save()

        xml_bytes = cfdi_pago(uuid_factura=str(factura.uuid_fiscal))
        root = ET.fromstring(xml_bytes)
        complemento = procesar_complemento(
            root, uuid_complemento='44444444-4444-4444-4444-444444444444',
            fecha=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            nombre_archivo='pago.xml', xml_bytes=xml_bytes,
        )
        self.assertEqual(complemento.estado, 'IDENTIFICADO')
        self.assertEqual(complemento.factura, factura)

    def test_queda_pendiente_si_no_existe_la_factura(self):
        from .complementos_pago import procesar_complemento

        xml_bytes = cfdi_pago(uuid_factura='99999999-9999-9999-9999-999999999999')
        root = ET.fromstring(xml_bytes)
        complemento = procesar_complemento(
            root, uuid_complemento='44444444-4444-4444-4444-444444444444',
            fecha=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            nombre_archivo='pago.xml', xml_bytes=xml_bytes,
        )
        self.assertEqual(complemento.estado, 'PENDIENTE')
        self.assertIsNone(complemento.factura)
        self.assertEqual(
            str(complemento.uuid_factura_relacionada),
            '99999999-9999-9999-9999-999999999999',
        )

    def test_varios_doctos_relacionados_queda_en_revision(self):
        from .complementos_pago import procesar_complemento

        xml_bytes = cfdi_pago(
            uuid_factura='11111111-1111-1111-1111-111111111111',
            uuids_factura_extra=['22222222-2222-2222-2222-222222222222'],
        )
        root = ET.fromstring(xml_bytes)
        complemento = procesar_complemento(
            root, uuid_complemento='44444444-4444-4444-4444-444444444444',
            fecha=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            nombre_archivo='pago.xml', xml_bytes=xml_bytes,
        )
        self.assertEqual(complemento.estado, 'REVISION')

    def test_adjunta_pdf_si_se_provee(self):
        from .complementos_pago import procesar_complemento

        xml_bytes = cfdi_pago(uuid_factura='99999999-9999-9999-9999-999999999999')
        root = ET.fromstring(xml_bytes)
        complemento = procesar_complemento(
            root, uuid_complemento='44444444-4444-4444-4444-444444444444',
            fecha=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            nombre_archivo='pago.xml', xml_bytes=xml_bytes, pdf_bytes=b'%PDF-1.4',
        )
        self.assertTrue(complemento.pdf_file)


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ConciliarPendientesTests(TestCase):
    def test_liga_complemento_pendiente_cuando_llega_la_factura(self):
        from .complementos_pago import conciliar_pendientes

        pendiente = ComplementoPago.objects.create(
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            uuid_factura_relacionada='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'),
        )
        pendiente.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=True)

        factura = XMLProveedor(
            uuid_fiscal='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 8, 8, 0, 0),
            rfc_emisor='LCT030408U39', nombre_emisor='L C TERMINAL',
            rfc_receptor='CIN220216BS2',
            subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
            tipo_comprobante='I',
        )
        factura.xml_file.save('f.xml', ContentFile(b'<x/>'), save=False)
        factura.save()

        conciliar_pendientes(factura)

        pendiente.refresh_from_db()
        self.assertEqual(pendiente.estado, 'IDENTIFICADO')
        self.assertEqual(pendiente.factura, factura)

    def test_no_toca_complementos_ya_identificados(self):
        from .complementos_pago import conciliar_pendientes

        otra_factura = XMLProveedor(
            uuid_fiscal='33333333-3333-3333-3333-333333333333',
            fecha_emision=datetime(2026, 7, 8, 8, 0, 0),
            rfc_emisor='LCT030408U39', nombre_emisor='L C TERMINAL',
            rfc_receptor='CIN220216BS2',
            subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
            tipo_comprobante='I',
        )
        otra_factura.xml_file.save('f.xml', ContentFile(b'<x/>'), save=False)
        otra_factura.save()

        ya_ligado = ComplementoPago.objects.create(
            factura=otra_factura,
            uuid_complemento='55555555-5555-5555-5555-555555555555',
            uuid_factura_relacionada='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'), estado='IDENTIFICADO',
        )
        ya_ligado.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=True)

        factura_nueva = XMLProveedor(
            uuid_fiscal='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 8, 8, 0, 0),
            rfc_emisor='LCT030408U39', nombre_emisor='L C TERMINAL',
            rfc_receptor='CIN220216BS2',
            subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
            tipo_comprobante='I',
        )
        factura_nueva.xml_file.save('f2.xml', ContentFile(b'<x/>'), save=False)
        factura_nueva.save()

        conciliar_pendientes(factura_nueva)

        ya_ligado.refresh_from_db()
        self.assertEqual(ya_ligado.factura, otra_factura)
