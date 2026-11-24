import base64
import io
import os
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pymupdf as fitz
from django.test import SimpleTestCase

from glosa import services_jev_ia as jev

ENV_CON_JEV = {'JEV_AI_API_KEY': 'test-key', 'TYPESAFE_API_KEY': '',
               'JEV_API_URL': '', 'JEV_MODEL': ''}
ENV_SIN_JEV = {'JEV_AI_API_KEY': '', 'TYPESAFE_API_KEY': ''}

TEXTO_BL = (
    'BILL OF LADING FOR OCEAN TRANSPORT\n'
    'Shipper: SHANGHAI EXPORT CO LTD\nConsignee: EMPRESA PRUEBA SA DE CV\n'
    'Notify party: same as consignee\nVessel: MSC MARIA  Voyage: V202\n'
    'Port of loading: SHANGHAI  Port of discharge: LAZARO CARDENAS\n'
    'Container / Seal: MSKU1234567 / 998877  Gross weight: 24500 KG\n'
)


def pdf_con_texto(texto):
    doc = fitz.open()
    pagina = doc.new_page()
    pagina.insert_textbox(fitz.Rect(40, 40, 560, 800), texto, fontsize=9)
    datos = doc.tobytes()
    doc.close()
    return io.BytesIO(datos)


def pdf_escaneado():
    doc = fitz.open()
    pagina = doc.new_page()  # A4 / Letter, sin capa de texto
    pagina.draw_rect(fitz.Rect(50, 50, 300, 300), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    datos = doc.tobytes()
    doc.close()
    return io.BytesIO(datos)


def respuesta_jev(choice='BL', prob=0.92, confidence=0.85, noul=0.95):
    resto = (1 - prob) / (len(jev.CRITERIOS_TIPO) - 1)
    probabilidades = {k: (prob if k == choice else resto) for k in jev.CRITERIOS_TIPO}
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        'model': 'jev-1.14.0',
        'answers': {
            'tipo_documento': {
                'type': 'choice', 'choice': choice,
                'confidence': confidence, 'probabilities': probabilidades,
            },
            'es_documento_conocido': {'type': 'noul', 'noul': noul},
        },
        'usage': {'input_tokens': 900, 'output_tokens': 0},
    }
    return resp


@patch.dict(os.environ, ENV_CON_JEV)
class ConsultaJevPayloadTest(SimpleTestCase):

    @patch('glosa.services_jev_ia.requests.post')
    def test_payload_respeta_contrato_systemone(self, mock_post):
        mock_post.return_value = respuesta_jev()
        jev.clasificar_documento(pdf_con_texto(TEXTO_BL * 3))

        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], 'https://api.typesafe.ai/v1/systemone')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer test-key')
        self.assertNotIn('TypeSafe-Danger-Zone-Images', kwargs['headers'])

        payload = kwargs['json']
        self.assertEqual(payload['model'], 'jev-latest')
        self.assertIn('BILL OF LADING', payload['state']['documento'])
        self.assertNotIn('assets', payload)

        tipo = payload['questions']['tipo_documento']
        self.assertEqual(tipo['type'], 'choice')
        self.assertTrue(tipo['instructions'])
        self.assertNotIn('options', tipo)
        self.assertEqual(set(tipo['criteria']), set(jev.CRITERIOS_TIPO))
        self.assertNotIn('OTROS', tipo['criteria'])

        noul = payload['questions']['es_documento_conocido']
        self.assertEqual(noul['type'], 'noul')
        self.assertEqual(set(noul['criteria']), {'true', 'false'})

    @patch('glosa.services_jev_ia.requests.post')
    def test_modelo_y_url_configurables(self, mock_post):
        mock_post.return_value = respuesta_jev()
        with patch.dict(os.environ, {'JEV_MODEL': 'jev-1.13.0',
                                     'JEV_API_URL': 'https://proxy.local/v1/systemone'}):
            jev.clasificar_documento(pdf_con_texto(TEXTO_BL * 3))
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], 'https://proxy.local/v1/systemone')
        self.assertEqual(kwargs['json']['model'], 'jev-1.13.0')

    @patch('glosa.services_jev_ia.requests.post')
    def test_pdf_escaneado_se_manda_como_imagen(self, mock_post):
        mock_post.return_value = respuesta_jev()
        resultado = jev.clasificar_documento(pdf_escaneado())

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs['headers']['TypeSafe-Danger-Zone-Images'], 'true')
        payload = kwargs['json']
        ref = payload['state']['documento']
        asset = payload['assets'][ref]
        self.assertEqual(asset['type'], 'image')
        self.assertEqual(asset['format'], 'png')

        png = base64.b64decode(asset['data_base64'])
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        pix = fitz.Pixmap(png)
        self.assertLessEqual(max(pix.width, pix.height), jev.IMG_LADO_MAX)
        self.assertGreaterEqual(min(pix.width, pix.height), jev.IMG_LADO_MIN)
        self.assertLessEqual(pix.width * pix.height, jev.IMG_PIXELES_MAX)
        self.assertEqual(resultado.detalle['entrada'], 'imagen')


@patch.dict(os.environ, ENV_CON_JEV)
class DecisionClasificacionTest(SimpleTestCase):

    @patch('glosa.services_jev_ia.requests.post')
    def test_respuesta_clara_se_acepta(self, mock_post):
        mock_post.return_value = respuesta_jev('BL', prob=0.92, noul=0.95)
        r = jev.clasificar_documento(pdf_con_texto(TEXTO_BL * 3))
        self.assertEqual(r.tipo, 'BL')
        self.assertEqual(r.confianza, Decimal('0.92'))
        self.assertEqual(r.detalle['estado'], jev.ESTADO_OK)
        self.assertFalse(r.requiere_revision)

    @patch('glosa.services_jev_ia.requests.post')
    def test_noul_bajo_da_otros(self, mock_post):
        mock_post.return_value = respuesta_jev('FACTURA', prob=0.5, noul=0.1)
        r = jev.clasificar_documento(pdf_con_texto(TEXTO_BL * 3))
        self.assertEqual(r.tipo, 'OTROS')
        self.assertEqual(r.confianza, Decimal('0.9'))
        self.assertEqual(r.detalle['sugerencia'], 'FACTURA')

    @patch('glosa.services_jev_ia.requests.post')
    def test_probabilidad_dividida_pide_revision(self, mock_post):
        mock_post.return_value = respuesta_jev('FACTURA', prob=0.45, confidence=0.2, noul=0.9)
        r = jev.clasificar_documento(pdf_con_texto(TEXTO_BL * 3))
        self.assertEqual(r.tipo, 'FACTURA')
        self.assertEqual(r.detalle['estado'], jev.ESTADO_REVISION)
        self.assertTrue(r.requiere_revision)

    @patch('glosa.services_jev_ia.requests.post')
    def test_error_http_no_inventa_confianza(self, mock_post):
        resp = MagicMock()
        resp.status_code = 422
        resp.json.return_value = {'detail': [{'loc': ['body', 'model'], 'msg': 'Field required'}]}
        mock_post.return_value = resp

        with self.assertLogs('glosa.services_jev_ia', level='ERROR'):
            r = jev.clasificar_documento(pdf_con_texto(TEXTO_BL * 3))
        self.assertEqual(r.tipo, 'BL')  # sugerencia por palabras clave
        self.assertIsNone(r.confianza)
        self.assertEqual(r.detalle['estado'], jev.ESTADO_ERROR)
        self.assertIn('422', r.detalle['error'])

    @patch('glosa.services_jev_ia.requests.post')
    def test_respuesta_sin_answers_es_error(self, mock_post):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'tipo_documento': {'choice': 'BL'}}
        mock_post.return_value = resp
        r = jev.clasificar_documento(pdf_con_texto(TEXTO_BL * 3))
        self.assertIsNone(r.confianza)
        self.assertEqual(r.detalle['estado'], jev.ESTADO_ERROR)


@patch.dict(os.environ, ENV_SIN_JEV)
class SinJevTest(SimpleTestCase):

    @patch('glosa.services_jev_ia.requests.post')
    def test_sin_api_key_no_llama_y_marca_sin_ia(self, mock_post):
        r = jev.clasificar_documento(pdf_con_texto(TEXTO_BL))
        mock_post.assert_not_called()
        self.assertEqual(r.tipo, 'BL')
        self.assertIsNone(r.confianza)
        self.assertEqual(r.detalle['estado'], jev.ESTADO_SIN_IA)

    def test_lista_empaque_con_invoice_no_es_factura(self):
        texto = 'PACKING LIST\nInvoice No. INV-2026-001\nCarton 1-20  Gross 480 KG'
        self.assertEqual(jev.clasificar_por_palabras_clave(texto), 'LISTA_EMPAQUE')

    def test_palabras_clave_cubren_todos_los_tipos(self):
        casos = {
            'CERTIFICATE OF ORIGIN USMCA': 'CERTIFICADOS',
            'PERMISO PREVIO DE IMPORTACION': 'PERMISOS',
            'CARTA TÉCNICA DEL PRODUCTO': 'CARTA_TECNICA',
            'COMMERCIAL INVOICE': 'FACTURA',
            'COTIZACION DE FLETE': 'OTROS',
        }
        for texto, esperado in casos.items():
            self.assertEqual(jev.clasificar_por_palabras_clave(texto), esperado, texto)

    def test_binario_no_se_manda_como_texto(self):
        contenido = jev.leer_documento(io.BytesIO(b'%PDF-1.4 \xff\xfe basura'))
        self.assertEqual(contenido.texto, '')
        self.assertIsNone(contenido.imagen_png)
