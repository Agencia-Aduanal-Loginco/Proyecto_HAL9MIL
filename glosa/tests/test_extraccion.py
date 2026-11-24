import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from glosa import services_extraccion as ext
from glosa.tests.test_jev_ia import TEXTO_BL, pdf_con_texto, pdf_escaneado

IA_ON = dict(IA_HABILITADA=True, ANTHROPIC_API_KEY='test-key')

BL_CRUDO = {
    'numero_bl': 'MEDUSH123456', 'naviera': 'MSC', 'buque': 'MSC MARIA', 'viaje': 'V202',
    'puerto_carga': 'SHANGHAI', 'puerto_descarga': 'LAZARO CARDENAS',
    'shipper': 'SHANGHAI EXPORT CO LTD', 'consignatario': 'EMPRESA PRUEBA SA DE CV',
    'peso_bruto_kg': 24500, 'bultos': 120,
    'contenedores': [
        {'numero': 'CSQU 305438-3', 'sello': '998877', 'tipo': "40'HC", 'peso_bruto_kg': 24500},
    ],
}


def cliente_falso(entrada, stop_reason='end_turn'):
    bloque = SimpleNamespace(type='text', text=json.dumps(entrada))
    respuesta = SimpleNamespace(content=[bloque], model='claude-sonnet-5-5', stop_reason=stop_reason)
    cliente = MagicMock()
    cliente.messages.create.return_value = respuesta
    return cliente


class ContenedorISO6346Test(SimpleTestCase):

    def test_digito_verificador(self):
        self.assertTrue(ext.contenedor_valido('CSQU3054383'))   # ejemplo de la norma
        self.assertTrue(ext.contenedor_valido('csqu 305438-3'))  # se normaliza
        self.assertFalse(ext.contenedor_valido('CSQU3054384'))  # dígito incorrecto
        self.assertFalse(ext.contenedor_valido('MSKU123456'))   # longitud
        self.assertFalse(ext.contenedor_valido(''))


@override_settings(**IA_ON)
class ExtraccionBLTest(SimpleTestCase):

    @patch('glosa.services_extraccion._client')
    def test_llamada_manda_pdf_con_json_outputs(self, mock_client):
        cliente = cliente_falso(BL_CRUDO)
        mock_client.return_value = cliente
        pdf = pdf_con_texto(TEXTO_BL)

        ext.extraer_datos_documento(pdf, 'BL')

        kwargs = cliente.messages.create.call_args.kwargs
        self.assertNotIn('tool_choice', kwargs)  # claude-sonnet-5-5 lo rechaza
        self.assertNotIn('tools', kwargs)
        formato = kwargs['extra_body']['output_config']['format']
        self.assertEqual(formato['type'], 'json_schema')
        self.assertIn('buque', formato['schema']['properties'])
        bloque = kwargs['messages'][0]['content'][0]
        self.assertEqual(bloque['type'], 'document')
        self.assertEqual(bloque['source']['media_type'], 'application/pdf')
        self.assertEqual(base64.b64decode(bloque['source']['data']), pdf.getvalue())

    @patch('glosa.services_extraccion._client')
    def test_pdf_escaneado_tambien_va_como_document(self, mock_client):
        cliente = cliente_falso(BL_CRUDO)
        mock_client.return_value = cliente
        ext.extraer_datos_documento(pdf_escaneado(), 'BL')
        bloque = cliente.messages.create.call_args.kwargs['messages'][0]['content'][0]
        self.assertEqual(bloque['type'], 'document')

    @patch('glosa.services_extraccion._client')
    def test_datos_validos_quedan_ok(self, mock_client):
        mock_client.return_value = cliente_falso(BL_CRUDO)
        datos = ext.extraer_datos_documento(pdf_con_texto(TEXTO_BL), 'BL')

        self.assertEqual(datos['buque'], 'MSC MARIA')
        self.assertEqual(datos['viaje'], 'V202')
        self.assertEqual(datos['peso_bruto_kg'], '24500')
        self.assertEqual(datos['contenedores'][0]['numero'], 'CSQU3054383')
        self.assertEqual(datos['meta']['estado'], ext.ESTADO_OK)
        self.assertEqual(datos['meta']['advertencias'], [])

    @patch('glosa.services_extraccion._client')
    def test_contenedor_invalido_y_pesos_que_no_cuadran_piden_revision(self, mock_client):
        crudo = dict(BL_CRUDO, contenedores=[
            {'numero': 'CSQU3054384', 'sello': None, 'tipo': None, 'peso_bruto_kg': 10000},
        ])
        mock_client.return_value = cliente_falso(crudo)
        datos = ext.extraer_datos_documento(pdf_con_texto(TEXTO_BL), 'BL')

        self.assertEqual(datos['meta']['estado'], ext.ESTADO_REVISION)
        advertencias = ' | '.join(datos['meta']['advertencias'])
        self.assertIn('CSQU3054384', advertencias)
        self.assertIn('no coincide', advertencias)

    @patch('glosa.services_extraccion._client')
    def test_error_de_api_no_lanza(self, mock_client):
        cliente = MagicMock()
        cliente.messages.create.side_effect = RuntimeError('overloaded')
        mock_client.return_value = cliente
        with self.assertLogs('glosa.services_extraccion', level='ERROR'):
            datos = ext.extraer_datos_documento(pdf_con_texto(TEXTO_BL), 'BL')
        self.assertEqual(datos['meta']['estado'], ext.ESTADO_ERROR)
        self.assertNotIn('buque', datos)

    @patch('glosa.services_extraccion._client')
    def test_texto_que_no_es_json_es_error(self, mock_client):
        respuesta = SimpleNamespace(content=[SimpleNamespace(type='text', text='hola')],
                                    model='x', stop_reason='end_turn')
        cliente = MagicMock()
        cliente.messages.create.return_value = respuesta
        mock_client.return_value = cliente
        with self.assertLogs('glosa.services_extraccion', level='ERROR'):
            datos = ext.extraer_datos_documento(pdf_con_texto(TEXTO_BL), 'BL')
        self.assertEqual(datos['meta']['estado'], ext.ESTADO_ERROR)

    @patch('glosa.services_extraccion._client')
    def test_max_tokens_y_refusal_son_error(self, mock_client):
        for stop_reason in ('max_tokens', 'refusal'):
            mock_client.return_value = cliente_falso(BL_CRUDO, stop_reason=stop_reason)
            with self.assertLogs('glosa.services_extraccion', level='ERROR'):
                datos = ext.extraer_datos_documento(pdf_con_texto(TEXTO_BL), 'BL')
            self.assertEqual(datos['meta']['estado'], ext.ESTADO_ERROR, stop_reason)
            self.assertNotIn('buque', datos)


class EsquemaJsonOutputsTest(SimpleTestCase):
    """Límites documentados de JSON outputs (platform.claude.com, structured outputs)."""

    def _objetos(self, nodo):
        if isinstance(nodo, dict):
            if nodo.get('type') == 'object':
                yield nodo
            for v in nodo.values():
                yield from self._objetos(v)
        elif isinstance(nodo, list):
            for v in nodo:
                yield from self._objetos(v)

    def _uniones(self, nodo):
        total = 0
        if isinstance(nodo, dict):
            if isinstance(nodo.get('type'), list) or 'anyOf' in nodo:
                total += 1
            for v in nodo.values():
                total += self._uniones(v)
        elif isinstance(nodo, list):
            for v in nodo:
                total += self._uniones(v)
        return total

    def test_esquemas_cumplen_limites(self):
        prohibidas = {'minimum', 'maximum', 'multipleOf', 'minLength', 'maxLength', 'maxItems'}
        for tipo in ext.TIPOS_SOPORTADOS:
            esquema = ext.esquema_json(tipo)
            for obj in self._objetos(esquema):
                self.assertIs(obj['additionalProperties'], False, tipo)
                self.assertEqual(set(obj['required']), set(obj['properties']), tipo)
            self.assertLessEqual(self._uniones(esquema), 16, tipo)
            self.assertFalse(prohibidas & set(json.dumps(esquema).replace('"', ' ').split()), tipo)


@override_settings(**IA_ON)
class ExtraccionOtrosTiposTest(SimpleTestCase):

    @patch('glosa.services_extraccion._client')
    def test_factura_importes_como_decimal_y_suma(self, mock_client):
        crudo = {
            'numero_factura': 'INV-001', 'fecha': '2026-10-30', 'proveedor': 'ACME',
            'comprador': 'EMPRESA PRUEBA', 'incoterm': 'FOB', 'moneda': 'usd',
            'valor_total': 1500.1,
            'partidas': [
                {'descripcion': 'Tornillos', 'cantidad': 1000, 'unidad': 'PZA',
                 'precio_unitario': 0.5, 'importe': 500},
                {'descripcion': 'Tuercas', 'cantidad': 2000, 'unidad': 'PZA',
                 'precio_unitario': 0.5, 'importe': 1000.1},
            ],
        }
        mock_client.return_value = cliente_falso(crudo)
        datos = ext.extraer_datos_documento(io.BytesIO(b'<cfdi:Comprobante Total="1500.10"/>'), 'FACTURA')

        self.assertEqual(datos['valor_total'], '1500.1')
        self.assertEqual(datos['partidas'][1]['importe'], '1000.1')
        self.assertEqual(datos['moneda'], 'USD')
        self.assertEqual(datos['meta']['estado'], ext.ESTADO_OK)

    @patch('glosa.services_extraccion._client')
    def test_xml_se_manda_como_texto(self, mock_client):
        cliente = cliente_falso({'numero_factura': None, 'fecha': None, 'proveedor': None,
                                 'comprador': None, 'incoterm': None, 'moneda': None,
                                 'valor_total': None, 'partidas': []})
        mock_client.return_value = cliente
        datos = ext.extraer_datos_documento(io.BytesIO(b'<cfdi:Comprobante/>'), 'FACTURA')
        bloque = cliente.messages.create.call_args.kwargs['messages'][0]['content'][0]
        self.assertEqual(bloque['type'], 'text')
        self.assertIn('cfdi:Comprobante', bloque['text'])
        self.assertIn('No se encontró el valor total', datos['meta']['advertencias'])

    def test_tipo_sin_esquema_no_aplica(self):
        datos = ext.extraer_datos_documento(io.BytesIO(b'x'), 'TIPO_INVENTADO')
        self.assertEqual(datos['meta']['estado'], ext.ESTADO_NO_APLICA)


@override_settings(IA_HABILITADA=False, ANTHROPIC_API_KEY='')
class ExtraccionSinIATest(SimpleTestCase):

    @patch('glosa.services_extraccion._client')
    def test_ia_apagada_no_llama(self, mock_client):
        datos = ext.extraer_datos_documento(pdf_con_texto(TEXTO_BL), 'BL')
        mock_client.assert_not_called()
        self.assertEqual(datos['meta']['estado'], ext.ESTADO_SIN_IA)


@override_settings(**IA_ON)
class ExtraccionPreformaTest(SimpleTestCase):

    @patch('glosa.services_extraccion._client')
    def test_preforma_extraction_and_subdivision(self, mock_client):
        crudo = {
            'num_refe': 'LCRR3940/26',
            'num_pedimento': '263737706703661',
            'clave_pedimento': 'A1',
            'peso_bruto_kg': 24500,
            'bultos': 120,
            'proveedor_nombre': 'PARCELMOBI SA DE CV',
            'numero_factura': 'INV-999',
            'fecha_factura': '2026-11-01',
            'incoterm': 'FOB',
            'moneda': 'USD',
            'valor_moneda_factura': 15000.00,
            'valor_dolares': 15000.00,
            'guias': ['MEDUSH123456'],
            'contenedores': ['CSQU3054383 / 40HC'],
            'documentos_ed': ['0170261GUVAM8'],
            'observaciones': 'SE SUBDIVIDE CON EL PEDIMENTO 6705454'
        }
        mock_client.return_value = cliente_falso(crudo)
        datos = ext.extraer_datos_documento(pdf_con_texto("PEDIMENTO BORRADOR SIN VALIDEZ"), 'PREFORMA')
        self.assertEqual(datos['num_refe'], 'LCRR3940/26')
        self.assertTrue(datos['subdivision']['detectada'])
        self.assertEqual(datos['subdivision']['pedimento_relacionado'], '6705454')
        self.assertEqual(datos['meta']['estado'], ext.ESTADO_OK)
        self.assertEqual(
            mock_client.return_value.messages.create.call_args.kwargs['max_tokens'],
            ext.MAX_TOKENS_POR_TIPO['PREFORMA'],
        )

    def _partida_sellador(self, **cambios):
        partida = {
            'sec': '001', 'fraccion': '3214.10.01', 'nico': '00', 'vinculacion': '1',
            'metodo_valoracion': '1', 'umc': '6', 'cantidad_umc': '133,200.000',
            'umt': '1', 'cantidad_umt': '11,322.00000', 'pais_vendedor': 'mex',
            'pais_origen': 'CHN', 'descripcion': 'SELLADOR', 'valor_aduana': '1152446',
            'precio_pagado': '1152446', 'precio_unitario': '8.65200', 'valor_agregado': '',
            'marca': 'TRUPER', 'modelo': '', 'codigo_producto': '17562',
            'identificadores': [
                {'clave': 'XP', 'complemento1': 'S2', 'complemento2': 'U', 'complemento3': ''},
                {'clave': '', 'complemento1': '', 'complemento2': '', 'complemento3': ''},
            ],
            'contribuciones': [
                {'clave': 'IGI', 'tasa': '3.00000', 'tipo_tasa': '1', 'forma_pago': '0', 'importe': '34573'},
                {'clave': 'IVA', 'tasa': '16.00000', 'tipo_tasa': '1', 'forma_pago': '0', 'importe': '191280'},
            ],
        }
        partida.update(cambios)
        return partida

    def test_partida_preforma_normalizada(self):
        datos, advertencias = ext.validar_y_normalizar(
            'PREFORMA', {'partidas': [self._partida_sellador()]}
        )
        self.assertEqual(advertencias, [])
        p = datos['partidas'][0]
        self.assertEqual(p['fraccion'], '32141001')
        self.assertEqual(p['nico'], '00')
        self.assertEqual(p['cantidad_umc'], '133200.000')
        self.assertEqual(p['cantidad_umt'], '11322.00000')
        self.assertEqual(p['valor_aduana'], '1152446')
        self.assertEqual(p['precio_unitario'], '8.65200')
        self.assertIsNone(p['valor_agregado'])
        self.assertEqual(p['pais_vendedor'], 'MEX')
        self.assertEqual(p['codigo_producto'], '17562')
        self.assertEqual(p['identificadores'], [
            {'clave': 'XP', 'complemento1': 'S2', 'complemento2': 'U', 'complemento3': ''},
        ])
        self.assertEqual([(c['clave'], c['importe']) for c in p['contribuciones']],
                         [('IGI', '34573'), ('IVA', '191280')])

    def test_partida_preforma_bloque_permisos(self):
        partida = self._partida_sellador()
        partida['permisos'] = [
            {'clave': ' c1 ', 'numero': '9901R82 6078685', 'firma_descargo': '',
             'valor_comercial_dls': '33,626.27', 'cantidad_umt': '9,700.00000'},
            {'clave': '', 'numero': '', 'firma_descargo': '', 'valor_comercial_dls': '', 'cantidad_umt': ''},
        ]
        datos, _ = ext.validar_y_normalizar('PREFORMA', {'partidas': [partida]})
        self.assertEqual(datos['partidas'][0]['permisos'], [{
            'clave': 'C1', 'numero': '9901R826078685', 'firma_descargo': '',
            'valor_comercial_dls': '33626.27', 'cantidad_umt': '9700.00000',
        }])

    def test_partida_preforma_inconsistente_pide_revision(self):
        datos, advertencias = ext.validar_y_normalizar('PREFORMA', {'partidas': [
            self._partida_sellador(fraccion='321410', nico='0', precio_unitario='9.10000',
                                   cantidad_umt='0'),
        ]})
        texto = ' '.join(advertencias)
        self.assertIn('fracción', texto)
        self.assertIn('NICO', texto)
        self.assertIn('precio unitario', texto)
        self.assertIn('cantidad UMT', texto)


class BLContenedorRepetidoTest(SimpleTestCase):
    """Un BL con varias hojas/waybills puede listar el mismo contenedor más de una vez."""

    def _bl(self, contenedores, peso_total=None, bultos_total=None):
        datos = dict(BL_CRUDO)
        datos.update(peso_bruto_kg=peso_total, bultos=bultos_total, contenedores=contenedores)
        return datos

    def test_contenedor_repetido_suma_bultos_y_peso(self):
        datos, advertencias = ext.validar_y_normalizar('BL', self._bl([
            {'numero': 'CSQU3054383', 'sello': '998877', 'tipo': "20'GP", 'bultos': 271, 'peso_bruto_kg': 2385},
            {'numero': 'CSQU 305438-3', 'sello': '998877', 'tipo': "20'GP", 'bultos': 107, 'peso_bruto_kg': 3437.5},
        ], peso_total=2385, bultos_total=271))
        self.assertEqual(advertencias, [])
        self.assertEqual(len(datos['contenedores']), 1)
        cont = datos['contenedores'][0]
        self.assertEqual(cont['numero'], 'CSQU3054383')
        self.assertEqual(cont['bultos'], 378)
        self.assertEqual(cont['peso_bruto_kg'], '5822.5')
        self.assertEqual(cont['apariciones'], 2)
        # Los totales del BL pasan a ser la suma de todas las apariciones.
        self.assertEqual(datos['peso_bruto_kg'], '5822.5')
        self.assertEqual(datos['bultos'], 378)

    def test_contenedor_repetido_con_sellos_distintos_los_conserva(self):
        datos, _ = ext.validar_y_normalizar('BL', self._bl([
            {'numero': 'CSQU3054383', 'sello': 'A1', 'tipo': None, 'bultos': 10, 'peso_bruto_kg': 100},
            {'numero': 'CSQU3054383', 'sello': 'B2', 'tipo': "20'GP", 'bultos': 5, 'peso_bruto_kg': 50},
        ], peso_total=150, bultos_total=15))
        cont = datos['contenedores'][0]
        self.assertEqual(cont['sello'], 'A1 / B2')
        self.assertEqual(cont['tipo'], "20'GP")

    def test_contenedor_repetido_sin_peso_no_inventa_suma(self):
        datos, advertencias = ext.validar_y_normalizar('BL', self._bl([
            {'numero': 'CSQU3054383', 'sello': None, 'tipo': None, 'bultos': 10, 'peso_bruto_kg': 100},
            {'numero': 'CSQU3054383', 'sello': None, 'tipo': None, 'bultos': None, 'peso_bruto_kg': None},
        ], peso_total=100, bultos_total=10))
        cont = datos['contenedores'][0]
        self.assertIsNone(cont['peso_bruto_kg'])
        self.assertIsNone(cont['bultos'])
        self.assertEqual(datos['peso_bruto_kg'], '100')
        self.assertEqual(datos['bultos'], 10)
        self.assertTrue(any('CSQU3054383' in a for a in advertencias))

    def test_contenedores_distintos_no_se_fusionan(self):
        datos, advertencias = ext.validar_y_normalizar('BL', BL_CRUDO)
        self.assertEqual(advertencias, [])
        self.assertEqual(len(datos['contenedores']), 1)
        self.assertNotIn('apariciones', datos['contenedores'][0])
        self.assertEqual(datos['peso_bruto_kg'], '24500')
