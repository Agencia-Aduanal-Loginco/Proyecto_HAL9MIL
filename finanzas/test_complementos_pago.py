import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from referencias.models import Referencia

from .carga_xml import procesar_lote
from .models import ComplementoPago, XMLProveedor
from .cfdi_de_prueba import cfdi_cliente, cfdi_pago
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


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ProcesarLoteComplementosTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user('fin', password='x')

    def test_complemento_no_crea_xmlproveedor(self):
        xml_bytes = cfdi_pago(uuid_factura='99999999-9999-9999-9999-999999999999')
        resultados = procesar_lote([('pago.xml', xml_bytes)], self.usuario)
        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0].estado, 'COMPLEMENTO_PENDIENTE')
        self.assertEqual(XMLProveedor.objects.count(), 0)
        self.assertEqual(ComplementoPago.objects.count(), 1)

    def test_complemento_liga_si_la_factura_ya_existe(self):
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
        resultados = procesar_lote([('pago.xml', xml_bytes)], self.usuario)
        self.assertEqual(resultados[0].estado, 'COMPLEMENTO_LIGADO')
        complemento = ComplementoPago.objects.get()
        self.assertEqual(complemento.factura, factura)

    def test_complemento_duplicado_se_reporta(self):
        xml_bytes = cfdi_pago(uuid='44444444-4444-4444-4444-444444444444')
        procesar_lote([('pago.xml', xml_bytes)], self.usuario)
        resultados = procesar_lote([('pago2.xml', xml_bytes)], self.usuario)
        self.assertEqual(resultados[0].estado, 'DUPLICADO')

    def test_factura_nueva_liga_complemento_pendiente_existente(self):
        pendiente = ComplementoPago.objects.create(
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            uuid_factura_relacionada='99999999-9999-9999-9999-999999999999',
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'),
        )
        pendiente.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=True)

        # RFC no soportado por los extractores → factura queda sin referencia,
        # pero conciliar_pendientes debe correr de todos modos.
        xml_bytes = cfdi_cliente(uuid='99999999-9999-9999-9999-999999999999')
        procesar_lote([('factura.xml', xml_bytes)], self.usuario)

        pendiente.refresh_from_db()
        self.assertEqual(pendiente.estado, 'IDENTIFICADO')


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class SubirXmlProveedorComplementoTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.user = User.objects.create_user('subecg', password='x')
        self.user.groups.add(grupo)
        self.client.login(username='subecg', password='x')
        self.referencia = Referencia.objects.create(
            num_refe='LCRR0901/26', patente='1656', prefijo='LCRR',
        )
        self.url = reverse('finanzas:subir_xml', kwargs={'num_refe': self.referencia.num_refe})

    def test_complemento_no_crea_xmlproveedor_y_queda_pendiente(self):
        xml_bytes = cfdi_pago(uuid_factura='99999999-9999-9999-9999-999999999999')
        resp = self.client.post(self.url, {
            'xml_file': SimpleUploadedFile('pago.xml', xml_bytes),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(XMLProveedor.objects.count(), 0)
        complemento = ComplementoPago.objects.get()
        self.assertEqual(complemento.estado, 'PENDIENTE')
        self.assertEqual(complemento.referencia_sugerida, self.referencia)

    def test_complemento_con_pdf_opcional(self):
        xml_bytes = cfdi_pago(uuid_factura='99999999-9999-9999-9999-999999999999')
        resp = self.client.post(self.url, {
            'xml_file': SimpleUploadedFile('pago.xml', xml_bytes),
            'pdf_file': SimpleUploadedFile('pago.pdf', b'%PDF-1.4'),
        })
        self.assertEqual(resp.status_code, 302)
        complemento = ComplementoPago.objects.get()
        self.assertTrue(complemento.pdf_file)

    def test_factura_normal_sigue_funcionando_con_pdf_opcional(self):
        xml_bytes = cfdi_cliente(uuid='55555555-5555-5555-5555-555555555555')
        resp = self.client.post(self.url, {
            'xml_file': SimpleUploadedFile('factura.xml', xml_bytes),
            'pdf_file': SimpleUploadedFile('factura.pdf', b'%PDF-1.4'),
        })
        self.assertEqual(resp.status_code, 302)
        xml_obj = XMLProveedor.objects.get()
        self.assertTrue(xml_obj.pdf_file)
        self.assertEqual(xml_obj.referencia, self.referencia)


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ComplementosPagoPendientesViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.user = User.objects.create_user('verpend', password='x')
        self.user.groups.add(grupo)
        self.client.login(username='verpend', password='x')
        self.referencia = Referencia.objects.create(
            num_refe='LCRR0902/26', patente='1656', prefijo='LCRR',
        )
        self.pendiente = ComplementoPago.objects.create(
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            uuid_factura_relacionada='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'),
        )
        self.pendiente.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=True)
        self.url = reverse('finanzas:complementos_pago_pendientes')

    def test_lista_muestra_pendiente(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'CACIPA INTERNACIONAL')

    def test_ligar_manualmente_por_num_refe_y_uuid(self):
        factura = XMLProveedor(
            referencia=self.referencia,
            uuid_fiscal='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 8, 8, 0, 0),
            rfc_emisor='LCT030408U39', nombre_emisor='L C TERMINAL',
            rfc_receptor='CIN220216BS2',
            subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
            tipo_comprobante='I',
        )
        factura.xml_file.save('f.xml', ContentFile(b'<x/>'), save=False)
        factura.save()

        resp = self.client.post(self.url, {
            'complemento_id': self.pendiente.pk,
            'num_refe': self.referencia.num_refe,
        })
        self.assertEqual(resp.status_code, 302)
        self.pendiente.refresh_from_db()
        self.assertEqual(self.pendiente.estado, 'IDENTIFICADO')
        self.assertEqual(self.pendiente.factura, factura)

    def test_ligar_con_referencia_incorrecta_no_liga(self):
        otra_ref = Referencia.objects.create(
            num_refe='LCRR0903/26', patente='1656', prefijo='LCRR',
        )
        resp = self.client.post(self.url, {
            'complemento_id': self.pendiente.pk,
            'num_refe': otra_ref.num_refe,
        })
        self.assertEqual(resp.status_code, 302)
        self.pendiente.refresh_from_db()
        self.assertEqual(self.pendiente.estado, 'PENDIENTE')

    def test_requiere_modulo_finanzas(self):
        User.objects.create_user('sinmodulo', password='x')
        self.client.logout()
        self.client.login(username='sinmodulo', password='x')
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ComplementoPagoVerPdfViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.user = User.objects.create_user('verpdf', password='x')
        self.user.groups.add(grupo)
        self.client.login(username='verpdf', password='x')
        self.complemento = ComplementoPago.objects.create(
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'),
        )
        self.complemento.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=False)
        self.complemento.pdf_file.save('pago.pdf', ContentFile(b'%PDF-1.4'), save=True)

    def test_descarga_pdf(self):
        url = reverse('finanzas:complemento_pago_ver_pdf', kwargs={'pk': self.complemento.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')

    def test_404_si_no_tiene_pdf(self):
        self.complemento.pdf_file.delete(save=True)
        url = reverse('finanzas:complemento_pago_ver_pdf', kwargs={'pk': self.complemento.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class CargaMasivaResultadoComplementosTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.user = User.objects.create_user('cargacg', password='x')
        self.user.groups.add(grupo)
        self.client.login(username='cargacg', password='x')
        self.url = reverse('finanzas:carga_masiva_xml')

    def test_resultado_muestra_conteo_y_link_de_complementos_pendientes(self):
        xml_bytes = cfdi_pago(uuid_factura='99999999-9999-9999-9999-999999999999')
        resp = self.client.post(self.url, {
            'archivos': [SimpleUploadedFile('pago.xml', xml_bytes)],
        })
        self.assertContains(resp, 'Complemento')
        self.assertContains(resp, reverse('finanzas:complementos_pago_pendientes'))


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ReferenciaEstadoFilaFusionadaTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.user = User.objects.create_user('verestado', password='x')
        self.user.groups.add(grupo)
        self.client.login(username='verestado', password='x')
        self.referencia = Referencia.objects.create(
            num_refe='LCRR0904/26', patente='1656', prefijo='LCRR',
        )
        self.factura = XMLProveedor(
            referencia=self.referencia,
            uuid_fiscal='11111111-1111-1111-1111-111111111111',
            fecha_emision=datetime(2026, 7, 8, 8, 0, 0),
            rfc_emisor='LCT030408U39', nombre_emisor='L C TERMINAL',
            rfc_receptor='CIN220216BS2',
            subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
            tipo_comprobante='I',
        )
        self.factura.xml_file.save('f.xml', ContentFile(b'<x/>'), save=False)
        self.factura.save()
        self.url = reverse('finanzas:referencia_estado', kwargs={'num_refe': self.referencia.num_refe})

    def test_sin_complemento_muestra_tipo_normal(self):
        resp = self.client.get(self.url)
        # html=True: la plantilla envuelve "I" en espacios/saltos de línea, así
        # que se compara la estructura del fragmento en vez de un substring literal.
        self.assertContains(
            resp,
            '<span class="px-1.5 py-0.5 rounded bg-green-100 text-green-700">I</span>',
            html=True,
        )
        self.assertNotContains(resp, 'COM. PAGO')

    def test_con_complemento_ligado_muestra_com_pago(self):
        complemento = ComplementoPago.objects.create(
            factura=self.factura,
            uuid_complemento='44444444-4444-4444-4444-444444444444',
            uuid_factura_relacionada=self.factura.uuid_fiscal,
            fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
            rfc_emisor='CIN220216BS2', nombre_emisor='CACIPA INTERNACIONAL',
            monto_pagado=Decimal('116.00'), estado='IDENTIFICADO',
        )
        complemento.xml_file.save('pago.xml', ContentFile(b'<x/>'), save=False)
        complemento.pdf_file.save('pago.pdf', ContentFile(b'%PDF-1.4'), save=True)

        resp = self.client.get(self.url)
        self.assertContains(resp, 'COM. PAGO')
        self.assertContains(resp, 'Ver PDF pago')
