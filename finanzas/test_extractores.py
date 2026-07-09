import xml.etree.ElementTree as ET
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from referencias.models import Contenedor, Referencia

from .cfdi_de_prueba import cfdi_apm, cfdi_lct
from .cfdi_parser import parsear_cfdi_root
from .extractores import DatosAduanales, buscar_referencia, extraer_datos_aduanales


class ParsearCfdiRootTests(SimpleTestCase):
    def test_parsea_cfdi_lct_desde_elemento_raiz(self):
        root = ET.fromstring(cfdi_lct(uuid='135088fd-f6a7-4313-9d6a-3d15ee966df1'))
        datos = parsear_cfdi_root(root)
        self.assertEqual(datos['uuid'], '135088fd-f6a7-4313-9d6a-3d15ee966df1')
        self.assertEqual(datos['rfc_emisor'], 'LCT030408U39')
        self.assertEqual(datos['total'], Decimal('11094.00'))
        self.assertEqual(datos['iva'], Decimal('1530.21'))
        self.assertEqual(datos['tipo'], 'I')

    def test_parsea_cfdi_apm_desde_elemento_raiz(self):
        root = ET.fromstring(cfdi_apm())
        datos = parsear_cfdi_root(root)
        self.assertEqual(datos['rfc_emisor'], 'ATL120106DC6')
        self.assertEqual(datos['total'], Decimal('9138.24'))


class ExtractorLctTests(SimpleTestCase):
    def _extraer(self, **kwargs):
        return extraer_datos_aduanales(ET.fromstring(cfdi_lct(**kwargs)))

    def test_extrae_patente_pedimento_contenedor_y_bl(self):
        datos = self._extraer()
        self.assertEqual(datos.patente, '1656')
        # LeyendaEspecial16 viene como "1656-6001126"; se usa lo de después del guión
        self.assertEqual(datos.pedimento, '6001126')
        # LeyendaEspecial25 viene como "CSNU 879377 0"; se normaliza sin espacios
        self.assertEqual(datos.contenedor, 'CSNU8793770')
        self.assertEqual(datos.bl, 'COSU6501186800')

    def test_pedimento_sin_guion_se_usa_tal_cual(self):
        datos = self._extraer(pedimento='6001126')
        self.assertEqual(datos.pedimento, '6001126')

    def test_leyendas_vacias_dan_campos_vacios(self):
        datos = self._extraer(patente='', pedimento='', contenedor='', bl='')
        self.assertEqual(datos.patente, '')
        self.assertEqual(datos.pedimento, '')
        self.assertEqual(datos.contenedor, '')

    def test_rfc_no_soportado_devuelve_none(self):
        xml = cfdi_lct().replace(b'LCT030408U39', b'XXX010101XXX')
        self.assertIsNone(extraer_datos_aduanales(ET.fromstring(xml)))


class ExtractorApmTests(SimpleTestCase):
    def _extraer(self, **kwargs):
        return extraer_datos_aduanales(ET.fromstring(cfdi_apm(**kwargs)))

    def test_extrae_pedimento_patente_contenedor_y_bl(self):
        datos = self._extraer()
        self.assertEqual(datos.pedimento, '6000517')
        # AGENTEADUANAL = "1627/LUIS FELIPE VAZQUEZ DIAZ" → patente es el prefijo
        self.assertEqual(datos.patente, '1627')
        # Contenedor: prefijo "XXXX9999999-" de la Descripcion de los conceptos
        self.assertEqual(datos.contenedor, 'BEAU4729066')
        self.assertEqual(datos.bl, 'HLCUSHA2604CHSA6')

    def test_concepto_sin_prefijo_de_contenedor_da_contenedor_vacio(self):
        # "SERVICIO GENERAL" no cumple el patrón XXXX9999999-
        datos = self._extraer(contenedor='SERVICIO GENERAL')
        self.assertEqual(datos.contenedor, '')

    def test_agente_aduanal_sin_diagonal_se_usa_completo(self):
        datos = self._extraer(agente='1627')
        self.assertEqual(datos.patente, '1627')


class BuscarReferenciaTests(TestCase):
    def setUp(self):
        self.ref = Referencia.objects.create(
            num_refe='LCRR1126/26', patente='1656', prefijo='LCRR',
            num_pedimento='6001126',
        )
        Contenedor.objects.create(referencia=self.ref, num_cont='CSNU8793770')

    def test_match_unico_por_patente_y_pedimento(self):
        datos = DatosAduanales(patente='1656', pedimento='6001126',
                               contenedor='CSNU8793770')
        ref, motivo = buscar_referencia(datos)
        self.assertEqual(ref, self.ref)
        self.assertEqual(motivo, '')

    def test_match_por_pedimento_sin_contenedor_en_bd_tambien_liga(self):
        datos = DatosAduanales(patente='1656', pedimento='6001126',
                               contenedor='ZZZU0000000')
        # El contenedor no existe en la BD: no contradice, se liga
        ref, motivo = buscar_referencia(datos)
        self.assertEqual(ref, self.ref)

    def test_contenedor_que_contradice_el_pedimento_queda_pendiente(self):
        otra = Referencia.objects.create(
            num_refe='LCLF0999/26', patente='1627', prefijo='LCLF',
            num_pedimento='5999999',
        )
        Contenedor.objects.create(referencia=otra, num_cont='BEAU4729066')
        datos = DatosAduanales(patente='1656', pedimento='6001126',
                               contenedor='BEAU4729066')
        ref, motivo = buscar_referencia(datos)
        self.assertIsNone(ref)
        self.assertIn('contradice', motivo)

    def test_pedimento_sin_referencia_queda_pendiente(self):
        datos = DatosAduanales(patente='1656', pedimento='7777777')
        ref, motivo = buscar_referencia(datos)
        self.assertIsNone(ref)
        self.assertIn('7777777', motivo)

    def test_fallback_por_contenedor_unico_cuando_no_hay_pedimento(self):
        datos = DatosAduanales(contenedor='CSNU8793770')
        ref, motivo = buscar_referencia(datos)
        self.assertEqual(ref, self.ref)

    def test_contenedor_reutilizado_sin_pedimento_queda_pendiente(self):
        otra = Referencia.objects.create(
            num_refe='LCLF0417', patente='1627', prefijo='LCLF',
        )
        Contenedor.objects.create(referencia=otra, num_cont='CSNU8793770')
        datos = DatosAduanales(contenedor='CSNU8793770')
        ref, motivo = buscar_referencia(datos)
        self.assertIsNone(ref)
        self.assertIn('varias referencias', motivo)

    def test_datos_none_es_proveedor_no_soportado(self):
        ref, motivo = buscar_referencia(None)
        self.assertIsNone(ref)
        self.assertEqual(motivo, 'proveedor no soportado')

    def test_sin_datos_aduanales(self):
        ref, motivo = buscar_referencia(DatosAduanales())
        self.assertIsNone(ref)
        self.assertEqual(motivo, 'sin datos aduanales en el XML')
