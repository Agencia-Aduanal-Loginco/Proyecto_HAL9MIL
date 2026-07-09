import io
import tempfile
import zipfile
from datetime import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from referencias.models import Contenedor, Referencia

from .carga_xml import (
    ResultadoArchivo, crear_gasto_desde_xml, expandir_subidas, procesar_lote,
)
from .cfdi_de_prueba import cfdi_apm, cfdi_lct
from .models import GastoReferencia, XMLProveedor

MEDIA_TMP = tempfile.mkdtemp()


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


class ExpandirSubidasTests(TestCase):
    def test_expande_zip_y_archivos_sueltos(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('C1786738.xml', cfdi_apm())
            zf.writestr('C1786738.pdf', b'%PDF')
        zip_file = SimpleUploadedFile('invoices.zip', buf.getvalue())
        suelto = SimpleUploadedFile('factura.xml', cfdi_lct())
        archivos = expandir_subidas([zip_file, suelto])
        nombres = sorted(n for n, _ in archivos)
        self.assertEqual(nombres, ['C1786738.pdf', 'C1786738.xml', 'factura.xml'])

    def test_zip_invalido_lanza_badzipfile(self):
        malo = SimpleUploadedFile('roto.zip', b'no soy un zip')
        with self.assertRaises(zipfile.BadZipFile):
            expandir_subidas([malo])


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ProcesarLoteTests(TestCase):
    fixtures = ['plan_cuentas_inicial.json']

    def setUp(self):
        self.usuario = User.objects.create_user('fin', password='x')
        self.ref_lct = Referencia.objects.create(
            num_refe='LCRR1126/26', patente='1656', prefijo='LCRR',
            num_pedimento='6001126',
        )
        Contenedor.objects.create(referencia=self.ref_lct, num_cont='CSNU8793770')
        self.ref_apm = Referencia.objects.create(
            num_refe='LCLF0517/26', patente='1627', prefijo='LCLF',
            num_pedimento='6000517',
        )
        Contenedor.objects.create(referencia=self.ref_apm, num_cont='BEAU4729066')

    def test_xml_lct_con_match_queda_asignado_y_genera_gasto(self):
        resultados = procesar_lote([('lct.xml', cfdi_lct())], self.usuario)
        self.assertEqual(resultados[0].estado, 'ASIGNADO')
        self.assertEqual(resultados[0].referencia, self.ref_lct)
        xml_obj = XMLProveedor.objects.get()
        self.assertEqual(xml_obj.referencia, self.ref_lct)
        self.assertEqual(xml_obj.estado_asignacion, 'ASIGNADO')
        self.assertTrue(xml_obj.procesado)
        gasto = GastoReferencia.objects.get()
        self.assertEqual(gasto.tipo, 'MANIOBRAS')
        self.assertEqual(gasto.monto, Decimal('11094.00'))
        self.assertIsNotNone(gasto.poliza)

    def test_xml_apm_con_match_queda_asignado(self):
        resultados = procesar_lote([('apm.xml', cfdi_apm())], self.usuario)
        self.assertEqual(resultados[0].estado, 'ASIGNADO')
        self.assertEqual(resultados[0].referencia, self.ref_apm)

    def test_pdf_se_empareja_por_nombre(self):
        files = [('lct.xml', cfdi_lct()), ('lct.pdf', b'%PDF'), ('otro.csv', b'x')]
        procesar_lote(files, self.usuario)
        xml_obj = XMLProveedor.objects.get()
        self.assertTrue(xml_obj.pdf_file)

    def test_uuid_duplicado_se_omite(self):
        procesar_lote([('lct.xml', cfdi_lct())], self.usuario)
        resultados = procesar_lote([('lct2.xml', cfdi_lct())], self.usuario)
        self.assertEqual(resultados[0].estado, 'DUPLICADO')
        self.assertEqual(XMLProveedor.objects.count(), 1)

    def test_sin_match_queda_pendiente_sin_gasto(self):
        xml = cfdi_lct(uuid='33333333-3333-3333-3333-333333333333',
                       patente='1656', pedimento='1656-7777777',
                       contenedor='')
        resultados = procesar_lote([('lct.xml', xml)], self.usuario)
        self.assertEqual(resultados[0].estado, 'PENDIENTE')
        self.assertIn('7777777', resultados[0].detalle)
        xml_obj = XMLProveedor.objects.get()
        self.assertIsNone(xml_obj.referencia)
        self.assertEqual(xml_obj.estado_asignacion, 'PENDIENTE')
        self.assertEqual(GastoReferencia.objects.count(), 0)

    def test_xml_corrupto_reporta_error_y_no_aborta_el_lote(self):
        files = [('roto.xml', b'<<< no soy xml'), ('lct.xml', cfdi_lct())]
        resultados = procesar_lote(files, self.usuario)
        estados = {r.nombre: r.estado for r in resultados}
        self.assertEqual(estados['roto.xml'], 'ERROR')
        self.assertEqual(estados['lct.xml'], 'ASIGNADO')
