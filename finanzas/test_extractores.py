import xml.etree.ElementTree as ET
from decimal import Decimal

from django.test import SimpleTestCase

from .cfdi_de_prueba import cfdi_apm, cfdi_lct
from .cfdi_parser import parsear_cfdi_root


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
