"""Tests para referencias.bitacorakasu_client — cliente HTTP para BitacoraKasu."""

import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch, MagicMock

import requests
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import PerfilUsuario

from .bitacorakasu_client import enviar_modulacion, BitacoraKasuError
from .models import Contenedor, Doda, DodaReferencia, EnvioModulacion, Referencia

# For testing JSON decode errors
try:
    from requests.exceptions import JSONDecodeError as RequestsJSONDecodeError
except ImportError:
    RequestsJSONDecodeError = ValueError


class EnviarModulacionSuccessTests(TestCase):
    """Test exitosos: HTTP 200, retorna JSON."""

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_envio_exitoso_retorna_json(self, mock_post):
        """Envío exitoso (HTTP 200) retorna el JSON de la respuesta."""
        # Setup
        response_data = {'status': 'success', 'id': '12345'}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = response_data
        mock_post.return_value = mock_resp

        with override_settings(
            BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
            BITACORAKASU_API_TOKEN='test-token-abc123',
        ):
            # Execute
            payload = {'doda_id': '5001', 'transportista_rfc': 'XXX123456XXX'}
            result = enviar_modulacion(payload)

        # Assert
        self.assertEqual(result, response_data)

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_construye_authorization_header_con_token(self, mock_post):
        """El header Authorization se arma con 'Token {token}' (no 'bearer')."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_post.return_value = mock_resp

        with override_settings(
            BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
            BITACORAKASU_API_TOKEN='my-secret-token-xyz',
        ):
            payload = {'test': 'data'}
            enviar_modulacion(payload)

        # Verify el header fue pasado correctamente
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        self.assertIn('headers', call_kwargs)
        self.assertEqual(call_kwargs['headers']['Authorization'], 'Token my-secret-token-xyz')

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_usa_timeout_10_30(self, mock_post):
        """Usa timeout (10, 30) — conectar en 10s, lectura en 30s."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_post.return_value = mock_resp

        with override_settings(
            BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
            BITACORAKASU_API_TOKEN='token',
        ):
            enviar_modulacion({'test': 'data'})

        # Verify timeout
        call_kwargs = mock_post.call_args[1]
        self.assertEqual(call_kwargs['timeout'], (10, 30))

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_envia_json_como_payload(self, mock_post):
        """El payload se envía como JSON en el body."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_post.return_value = mock_resp

        with override_settings(
            BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
            BITACORAKASU_API_TOKEN='token',
        ):
            payload = {'doda_id': '5001', 'data': 'test'}
            enviar_modulacion(payload)

        # Verify json fue pasado
        call_kwargs = mock_post.call_args[1]
        self.assertEqual(call_kwargs['json'], payload)

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_usa_url_de_settings(self, mock_post):
        """Usa la URL de BITACORAKASU_MODULACION_URL en settings."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_post.return_value = mock_resp

        with override_settings(
            BITACORAKASU_MODULACION_URL='https://custom.bitacora.test/send',
            BITACORAKASU_API_TOKEN='token',
        ):
            enviar_modulacion({'test': 'data'})

        # Verify URL
        call_args = mock_post.call_args[0]
        self.assertEqual(call_args[0], 'https://custom.bitacora.test/send')

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_exitoso_con_json_decode_error_lanza_bitacorakasu_error(self, mock_post):
        """HTTP 200 pero respuesta no es JSON válido lanza BitacoraKasuError (no JSONDecodeError)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = 'Not valid JSON: <html>error</html>'
        # Simular que resp.json() lanza una excepción
        mock_resp.json.side_effect = ValueError('Invalid JSON')
        mock_post.return_value = mock_resp

        with override_settings(
            BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
            BITACORAKASU_API_TOKEN='token',
        ):
            with self.assertRaises(BitacoraKasuError) as context:
                enviar_modulacion({'test': 'data'})

            # El mensaje de error debe mencionar que es un JSON inválido
            error_msg = str(context.exception)
            self.assertIn('JSON', error_msg)


class EnviarModulacionConfigGuardTests(TestCase):
    """Modelado sobre finanzas/pac_client.py::_get_pac_settings /
    PACConfigError: si BITACORAKASU_MODULACION_URL (o el token) no están
    configurados, enviar_modulacion() debe fallar ANTES de intentar
    requests.post — hoy mismo BITACORAKASU_MODULACION_URL siempre está
    vacío en producción (el endpoint de BitacoraKasu no existe todavía), así
    que sin este guard cada push intenta una llamada de red real a
    requests.post('', ...)."""

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_url_vacia_lanza_bitacorakasu_error_sin_llamar_requests(self, mock_post):
        with override_settings(
            BITACORAKASU_MODULACION_URL='',
            BITACORAKASU_API_TOKEN='token',
        ):
            with self.assertRaises(BitacoraKasuError):
                enviar_modulacion({'test': 'data'})

        mock_post.assert_not_called()

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_token_vacio_lanza_bitacorakasu_error_sin_llamar_requests(self, mock_post):
        with override_settings(
            BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
            BITACORAKASU_API_TOKEN='',
        ):
            with self.assertRaises(BitacoraKasuError):
                enviar_modulacion({'test': 'data'})

        mock_post.assert_not_called()


class EnviarModulacionHTTPErrorTests(TestCase):
    """Errores HTTP (4xx/5xx) lanzan BitacoraKasuError."""

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_status_400_lanza_error(self, mock_post):
        """Status 400 lanza BitacoraKasuError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = 'Bad Request'
        mock_resp.json.return_value = {'error': 'invalid payload'}
        mock_post.return_value = mock_resp

        with override_settings(
            BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
            BITACORAKASU_API_TOKEN='token',
        ):
            with self.assertRaises(BitacoraKasuError):
                enviar_modulacion({'test': 'data'})

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_status_401_lanza_error(self, mock_post):
        """Status 401 (unauthorized) lanza BitacoraKasuError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = 'Unauthorized'
        mock_resp.json.return_value = {'error': 'token inválido'}
        mock_post.return_value = mock_resp

        with override_settings(
            BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
            BITACORAKASU_API_TOKEN='bad-token',
        ):
            with self.assertRaises(BitacoraKasuError):
                enviar_modulacion({'test': 'data'})

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_status_500_lanza_error(self, mock_post):
        """Status 500 (server error) lanza BitacoraKasuError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = 'Internal Server Error'
        mock_resp.json.return_value = {'error': 'server error'}
        mock_post.return_value = mock_resp

        with override_settings(
            BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
            BITACORAKASU_API_TOKEN='token',
        ):
            with self.assertRaises(BitacoraKasuError):
                enviar_modulacion({'test': 'data'})

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_error_message_incluye_response_body_cuando_disponible(self, mock_post):
        """El mensaje de error incluye el body de la respuesta si es disponible."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = 'Bad Request error message'
        mock_resp.json.return_value = {'error': 'invalid data', 'detail': 'missing field'}
        mock_post.return_value = mock_resp

        with override_settings(
            BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
            BITACORAKASU_API_TOKEN='token',
        ):
            try:
                enviar_modulacion({'test': 'data'})
                self.fail('BitacoraKasuError should have been raised')
            except BitacoraKasuError as e:
                # El mensaje debe mencionar algún detalle de la respuesta
                error_msg = str(e)
                # Puede contener el JSON o el texto de la respuesta
                self.assertTrue(
                    'Bad Request error message' in error_msg or
                    'invalid data' in error_msg or
                    'missing field' in error_msg,
                    f'Error message "{error_msg}" should include response details'
                )

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_error_respuesta_no_json_usa_resp_text(self, mock_post):
        """Error HTTP con body no-JSON cae al fallback resp.text y lanza BitacoraKasuError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = 'Plain text error response'
        # Simular que resp.json() lanza excepción en rama de error
        mock_resp.json.side_effect = ValueError('Invalid JSON')
        mock_post.return_value = mock_resp

        with override_settings(
            BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
            BITACORAKASU_API_TOKEN='token',
        ):
            with self.assertRaises(BitacoraKasuError) as context:
                enviar_modulacion({'test': 'data'})

            # El mensaje debe contener el texto de la respuesta
            error_msg = str(context.exception)
            self.assertIn('Plain text error response', error_msg)
            self.assertIn('400', error_msg)


class EnviarModulacionTimeoutTests(TestCase):
    """Timeout y RequestException lanzan BitacoraKasuError."""

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_timeout_exception_lanza_error(self, mock_post):
        """requests.exceptions.Timeout lanza BitacoraKasuError."""
        mock_post.side_effect = requests.exceptions.Timeout('Connection timeout')

        with override_settings(
            BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
            BITACORAKASU_API_TOKEN='token',
        ):
            with self.assertRaises(BitacoraKasuError):
                enviar_modulacion({'test': 'data'})

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_connection_error_lanza_error(self, mock_post):
        """requests.exceptions.ConnectionError lanza BitacoraKasuError."""
        mock_post.side_effect = requests.exceptions.ConnectionError('Connection refused')

        with override_settings(
            BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
            BITACORAKASU_API_TOKEN='token',
        ):
            with self.assertRaises(BitacoraKasuError):
                enviar_modulacion({'test': 'data'})

    @patch('referencias.bitacorakasu_client.requests.post')
    def test_generic_request_exception_lanza_error(self, mock_post):
        """requests.RequestException genérico lanza BitacoraKasuError."""
        mock_post.side_effect = requests.RequestException('Network error')

        with override_settings(
            BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
            BITACORAKASU_API_TOKEN='token',
        ):
            with self.assertRaises(BitacoraKasuError):
                enviar_modulacion({'test': 'data'})


class BitacoraKasuErrorTests(TestCase):
    """Tests de la excepción BitacoraKasuError."""

    def test_bitacorakasu_error_es_exception(self):
        """BitacoraKasuError hereda de Exception."""
        self.assertTrue(issubclass(BitacoraKasuError, Exception))

    def test_bitacorakasu_error_puede_ser_lanzado(self):
        """BitacoraKasuError puede ser lanzado y capturado."""
        with self.assertRaises(BitacoraKasuError):
            raise BitacoraKasuError('Test error message')

    def test_bitacorakasu_error_mensaje(self):
        """BitacoraKasuError preserva el mensaje."""
        msg = 'HTTP 400: invalid token'
        try:
            raise BitacoraKasuError(msg)
        except BitacoraKasuError as e:
            self.assertEqual(str(e), msg)


class EnvioModulacionLinksCompletarTests(TestCase):
    def test_links_completar_nace_vacio(self):
        doda = _doda()
        envio = EnvioModulacion.objects.create(doda=doda)
        self.assertEqual(envio.links_completar, {})

    def test_links_completar_persiste_el_dict(self):
        doda = _doda()
        envio = EnvioModulacion.objects.create(
            doda=doda, links_completar={'HLXU1234567': 'https://bitacora.test/x/'},
        )
        envio.refresh_from_db()
        self.assertEqual(envio.links_completar, {'HLXU1234567': 'https://bitacora.test/x/'})


# ─────────────────────────────────────────────────────────────────────────────
# procesar_dodas_nuevas — helpers de fixture
# ─────────────────────────────────────────────────────────────────────────────
def _referencia(num_refe='LCRR0100/26', cliente='ACME SA', pedimento='26 1656 1234567',
                peso=Decimal('12.500')):
    return Referencia.objects.create(
        num_refe=num_refe, patente='1656', prefijo='LCRR',
        nombre_cliente=cliente, num_pedimento=pedimento, peso_bruto=peso,
    )


def _contenedor(referencia, num_cont, tipo='40HC'):
    return Contenedor.objects.create(referencia=referencia, num_cont=num_cont, tipo=tipo)


def _doda(id_doda=5001, num_doda='DODA-0001', cve_capt='CAPT01',
         terminal_nombre='TERMINAL PORTUARIA UNO', fecha_doda=None):
    if fecha_doda is None:
        fecha_doda = timezone.make_aware(datetime(2026, 3, 15))
    return Doda.objects.create(
        id_doda=id_doda, num_doda=num_doda, patente='1656', cve_caat='CAAT01',
        cve_capt=cve_capt, terminal_nombre=terminal_nombre, fecha_doda=fecha_doda,
    )


def _doda_referencia(doda, referencia, cons_id=1):
    return DodaReferencia.objects.create(
        doda=doda, referencia=referencia, num_refe=referencia.num_refe, cons_id=cons_id,
    )


def _perfil(cve_capturista='CAPT01', username='capturista1', email='capt@example.com'):
    user = User.objects.create_user(username, email=email, password='x')
    return PerfilUsuario.objects.create(user=user, cve_capturista=cve_capturista)


def _resp_sendgrid(status=202, message_id='sg-msg-mod-001'):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {'X-Message-Id': message_id}
    return resp


def _resp_bitacorakasu(status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {'status': 'ok'}
    resp.text = '{"status": "ok"}'
    return resp


@override_settings(SENDGRID_API_KEY='SG.test',
                   BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
                   BITACORAKASU_API_TOKEN='token', MODULACION_FALLBACK_EMAILS=[])
class ProcesarDodasNuevasTests(TestCase):
    def setUp(self):
        self.perfil = _perfil()
        self.doda = _doda()
        self.referencia = _referencia()
        _doda_referencia(self.doda, self.referencia)
        self.cont1 = _contenedor(self.referencia, 'HLXU1234567', '40HC')
        self.cont2 = _contenedor(self.referencia, 'TCLU7654321', '20DC')

    def test_caso_feliz_email_y_pushes_exitosos(self):
        from .modulacion import procesar_dodas_nuevas

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()

            procesar_dodas_nuevas([self.doda])

        envio = EnvioModulacion.objects.get(doda=self.doda)
        self.assertEqual(envio.email_estado, 'ENVIADO')
        self.assertEqual(envio.push_estado, 'ENVIADO')
        self.assertEqual(envio.sg_message_id, 'sg-msg-mod-001')

        # Un POST a BitacoraKasu por cada contenedor
        self.assertEqual(mock_post.call_count, 2)
        payload_envs = [c.kwargs['json'] for c in mock_post.call_args_list]
        contenedores_enviados = {p['contenedor'] for p in payload_envs}
        self.assertEqual(contenedores_enviados, {'HLXU1234567', 'TCLU7654321'})
        for p in payload_envs:
            self.assertEqual(p['agencia'], 'LOGINCO')
            self.assertEqual(p['terminal_portuaria'], 'TERMINAL PORTUARIA UNO')
            self.assertEqual(p['cliente'], 'ACME SA')
            self.assertEqual(p['num_pedimento'], '26 1656 1234567')
            self.assertEqual(p['num_doda'], 'DODA-0001')
            self.assertEqual(p['fecha_doda'], '2026-03-15')
            # Clave de idempotencia estable "id_doda:num_cont" — el receptor
            # de BitacoraKasu debe poder distinguir un reenvío genuino (mismo
            # contenedor reintentado) de un duplicado, dado que el retry es
            # a nivel de DODA completa (si 1 de 5 contenedores falla, los 5
            # se re-postean).
            self.assertEqual(
                p['idempotency_key'], f'{self.doda.id_doda}:{p["contenedor"]}'
            )

        self.doda.refresh_from_db()
        self.assertIsNotNone(self.doda.notificado_en)
        self.assertIsNotNone(self.doda.modulacion_enviada_en)

    def test_links_completar_se_guarda_solo_para_contenedores_con_url(self):
        from .modulacion import procesar_dodas_nuevas

        def _post_side_effect(url, json=None, headers=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            if json['contenedor'] == 'HLXU1234567':
                data = {'status': 'ok', 'completar_datos_url':
                        'https://bitacora.test/modulacion/completar/tok1/'}
            else:
                data = {'status': 'ok'}
            resp.json.return_value = data
            return resp

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post',
                   side_effect=_post_side_effect):
            sg_cls.return_value.send.return_value = _resp_sendgrid()

            procesar_dodas_nuevas([self.doda])

        envio = EnvioModulacion.objects.get(doda=self.doda)
        self.assertEqual(envio.links_completar, {
            'HLXU1234567': 'https://bitacora.test/modulacion/completar/tok1/',
        })

    def test_sin_completar_datos_url_no_agrega_nada_a_links(self):
        from .modulacion import procesar_dodas_nuevas

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()  # sin completar_datos_url

            procesar_dodas_nuevas([self.doda])

        envio = EnvioModulacion.objects.get(doda=self.doda)
        self.assertEqual(envio.links_completar, {})

    def test_respuesta_no_dict_no_propaga_y_no_bloquea_el_email(self):
        """Si BitacoraKasu regresa HTTP 2xx con un body JSON válido pero
        no-dict (p.ej. `true` o una lista), respuesta.get('completar_datos_url')
        no debe propagar un AttributeError sin capturar. El push ya fue
        exitoso a nivel HTTP (enviados debe contar), sólo se omite la
        captura del link — y, dado el reorden push-antes-que-email, el
        email de la DODA no debe quedar bloqueado por esto."""
        from .modulacion import procesar_dodas_nuevas

        def _post_side_effect(url, json=None, headers=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = True  # body JSON válido, no-dict
            return resp

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post',
                   side_effect=_post_side_effect):
            sg_cls.return_value.send.return_value = _resp_sendgrid()

            procesar_dodas_nuevas([self.doda])

        envio = EnvioModulacion.objects.get(doda=self.doda)
        self.assertEqual(envio.push_estado, 'ENVIADO')
        self.assertEqual(envio.links_completar, {})
        self.assertEqual(envio.email_estado, 'ENVIADO')

    def test_links_completar_se_conserva_entre_llamadas_de_push(self):
        from .modulacion import _push_bitacorakasu

        envio = EnvioModulacion.objects.create(
            doda=self.doda, links_completar={'YA': 'https://existente.test/'},
        )

        with patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            mock_post.return_value = _resp_bitacorakasu()  # sin link nuevo esta vez

            _push_bitacorakasu(self.doda, envio)

        self.assertEqual(envio.links_completar, {'YA': 'https://existente.test/'})

    def test_email_incluye_boton_por_cada_link_completar(self):
        from .modulacion import _enviar_email_modulacion

        envio = EnvioModulacion.objects.create(
            doda=self.doda,
            links_completar={
                'HLXU1234567': 'https://bitacora.test/modulacion/completar/tok1/',
                'TCLU7654321': 'https://bitacora.test/modulacion/completar/tok2/',
            },
        )

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            _enviar_email_modulacion(self.doda, ('capt@example.com', 'Capturista'), envio)

        html = sg_cls.return_value.send.call_args[0][0].get()['content'][0]['value']
        self.assertIn('https://bitacora.test/modulacion/completar/tok1/', html)
        self.assertIn('https://bitacora.test/modulacion/completar/tok2/', html)
        self.assertIn('HLXU1234567', html)
        self.assertIn('TCLU7654321', html)

    def test_email_sin_links_completar_no_incluye_botones(self):
        from .modulacion import _enviar_email_modulacion

        envio = EnvioModulacion.objects.create(doda=self.doda)  # links_completar={} por default

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            _enviar_email_modulacion(self.doda, ('capt@example.com', 'Capturista'), envio)

        html = sg_cls.return_value.send.call_args[0][0].get()['content'][0]['value']
        self.assertNotIn('Completar carril', html)

    def test_flujo_completo_email_incluye_link_del_contenedor_con_url(self):
        """Integra Task 2 + 3 + 4: procesar_dodas_nuevas de punta a punta deja,
        en el correo real que se manda, el link del contenedor cuya terminal
        lo requería (y ninguno para el que no)."""
        from .modulacion import procesar_dodas_nuevas

        def _post_side_effect(url, json=None, headers=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            if json['contenedor'] == 'HLXU1234567':
                data = {'status': 'ok', 'completar_datos_url':
                        'https://bitacora.test/modulacion/completar/tok1/'}
            else:
                data = {'status': 'ok'}
            resp.json.return_value = data
            return resp

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post',
                   side_effect=_post_side_effect):
            sg_cls.return_value.send.return_value = _resp_sendgrid()

            procesar_dodas_nuevas([self.doda])

        html = sg_cls.return_value.send.call_args[0][0].get()['content'][0]['value']
        self.assertIn('https://bitacora.test/modulacion/completar/tok1/', html)
        self.assertNotIn('TCLU7654321', html)  # ese contenedor no trajo link

    def test_email_adjunta_pdf(self):
        from .modulacion import procesar_dodas_nuevas

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()

            procesar_dodas_nuevas([self.doda])

        mail_enviado = sg_cls.return_value.send.call_args[0][0]
        cuerpo = mail_enviado.get()
        self.assertEqual(len(cuerpo['attachments']), 1)
        self.assertEqual(cuerpo['attachments'][0]['type'], 'application/pdf')

    def test_fallo_email_no_bloquea_push(self):
        from .modulacion import procesar_dodas_nuevas

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.side_effect = Exception('boom sendgrid')
            mock_post.return_value = _resp_bitacorakasu()

            procesar_dodas_nuevas([self.doda])

        envio = EnvioModulacion.objects.get(doda=self.doda)
        self.assertEqual(envio.email_estado, 'ERROR')
        self.assertIn('boom sendgrid', envio.error_detalle)
        self.assertEqual(envio.push_estado, 'ENVIADO')
        self.assertEqual(mock_post.call_count, 2)

        self.doda.refresh_from_db()
        self.assertIsNone(self.doda.notificado_en)
        self.assertIsNotNone(self.doda.modulacion_enviada_en)

    def test_peso_bruto_nulo_manda_cero_en_vez_de_bloquear_el_push(self):
        """peso_toneladas es requerido por BitacoraKasu (ver REQUIRED_FIELDS
        en su views_api.py): mandar '' cuando Firebird no trae peso_bruto
        dejaba la DODA en ERROR para siempre. Se manda '0' para que el
        registro sí entre y el personal de Kasu lo detecte y verifique el
        dato manualmente."""
        from .modulacion import procesar_dodas_nuevas

        referencia_sin_peso = _referencia(num_refe='LCRR0200/26', peso=None)
        _doda_referencia(self.doda, referencia_sin_peso, cons_id=2)
        _contenedor(referencia_sin_peso, 'ZZZU9999999', '40HC')

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()

            procesar_dodas_nuevas([self.doda])

        envio = EnvioModulacion.objects.get(doda=self.doda)
        self.assertEqual(envio.push_estado, 'ENVIADO')

        payload_envs = [c.kwargs['json'] for c in mock_post.call_args_list]
        payload_sin_peso = next(p for p in payload_envs if p['contenedor'] == 'ZZZU9999999')
        self.assertEqual(payload_sin_peso['peso_toneladas'], '0')

    def test_fallo_de_un_contenedor_no_bloquea_los_demas_ni_propaga(self):
        from .bitacorakasu_client import BitacoraKasuError
        from .modulacion import procesar_dodas_nuevas

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.side_effect = [
                requests.exceptions.ConnectionError('conexión rechazada'),
                _resp_bitacorakasu(),
            ]

            # No debe propagar ninguna excepción
            procesar_dodas_nuevas([self.doda])

        envio = EnvioModulacion.objects.get(doda=self.doda)
        self.assertEqual(envio.push_estado, 'ERROR')
        self.assertIn('conexión rechazada', envio.error_detalle)
        self.assertEqual(mock_post.call_count, 2)

        self.doda.refresh_from_db()
        self.assertIsNone(self.doda.modulacion_enviada_en)

    def test_sin_destinatario_resuelto_registra_error_y_continua_con_push(self):
        from .modulacion import procesar_dodas_nuevas

        self.perfil.delete()
        User.objects.all().delete()

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            mock_post.return_value = _resp_bitacorakasu()

            procesar_dodas_nuevas([self.doda])

        envio = EnvioModulacion.objects.get(doda=self.doda)
        self.assertEqual(envio.email_estado, 'ERROR')
        self.assertIn('sin destinatario resuelto', envio.error_detalle)
        self.assertEqual(envio.push_estado, 'ENVIADO')
        sg_cls.return_value.send.assert_not_called()
        self.assertEqual(mock_post.call_count, 2)

        self.doda.refresh_from_db()
        self.assertIsNone(self.doda.notificado_en)
        self.assertIsNotNone(self.doda.modulacion_enviada_en)

    @override_settings(MODULACION_FALLBACK_EMAILS=['fallback@example.com'])
    def test_sin_perfil_pero_con_fallback_envia_al_fallback(self):
        from .modulacion import procesar_dodas_nuevas

        self.perfil.delete()
        User.objects.all().delete()

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()

            procesar_dodas_nuevas([self.doda])

        envio = EnvioModulacion.objects.get(doda=self.doda)
        self.assertEqual(envio.email_estado, 'ENVIADO')
        mail_enviado = sg_cls.return_value.send.call_args[0][0]
        cuerpo = mail_enviado.get()
        self.assertEqual(cuerpo['personalizations'][0]['to'][0]['email'], 'fallback@example.com')

    def test_multiples_dodas_una_falla_no_afecta_a_las_demas(self):
        from .modulacion import procesar_dodas_nuevas

        otra_doda = _doda(id_doda=5002, num_doda='DODA-0002', cve_capt='CAPT01',
                          terminal_nombre='TERMINAL DOS')
        otra_referencia = _referencia(num_refe='LCRR0200/26')
        _doda_referencia(otra_doda, otra_referencia)
        _contenedor(otra_referencia, 'MSCU1112223', '20DC')

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.side_effect = [
                Exception('boom'), _resp_sendgrid(),
            ]
            mock_post.return_value = _resp_bitacorakasu()

            procesar_dodas_nuevas([self.doda, otra_doda])

        envio1 = EnvioModulacion.objects.get(doda=self.doda)
        envio2 = EnvioModulacion.objects.get(doda=otra_doda)
        self.assertEqual(envio1.email_estado, 'ERROR')
        self.assertEqual(envio2.email_estado, 'ENVIADO')
        self.assertEqual(envio1.push_estado, 'ENVIADO')
        self.assertEqual(envio2.push_estado, 'ENVIADO')

    def test_no_hay_llamadas_de_red_reales(self):
        """requests.post y SendGridAPIClient.send están siempre mockeados en
        este módulo de tests; este test documenta la intención (no realiza
        aserciones adicionales de red real, que no debe ocurrir jamás)."""
        from .modulacion import procesar_dodas_nuevas

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()
            procesar_dodas_nuevas([self.doda])
        # Si llegamos aquí sin error de red real (DNS, conexión, etc.), OK.
        self.assertTrue(True)


class OrdenPushAntesQueEmailTests(TestCase):
    def test_procesar_doda_llama_push_antes_que_email(self):
        from .modulacion import _procesar_doda
        doda = _doda()
        orden = []

        def _push(d, e):
            orden.append('push')
            return True

        def _email(d, e):
            orden.append('email')
            return True

        with patch('referencias.modulacion._procesar_push', side_effect=_push), \
             patch('referencias.modulacion._procesar_email', side_effect=_email):
            _procesar_doda(doda)

        self.assertEqual(orden, ['push', 'email'])

    def test_reintentar_envio_llama_push_antes_que_email(self):
        from .modulacion import reintentar_envio
        doda = _doda()
        envio = EnvioModulacion.objects.create(doda=doda)
        orden = []

        def _push(d, e):
            orden.append('push')
            return True

        def _email(d, e):
            orden.append('email')
            return True

        with patch('referencias.modulacion._procesar_push', side_effect=_push), \
             patch('referencias.modulacion._procesar_email', side_effect=_email):
            reintentar_envio(envio)

        self.assertEqual(orden, ['push', 'email'])


# ─────────────────────────────────────────────────────────────────────────────
# Prueba end-to-end: POST /api/sync/ -> on_commit real -> EnvioModulacion
# ─────────────────────────────────────────────────────────────────────────────
@override_settings(SYNC_SECRET_KEY='test-secret',
                   SENDGRID_API_KEY='SG.test',
                   BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
                   BITACORAKASU_API_TOKEN='token', MODULACION_FALLBACK_EMAILS=[])
class SyncEndpointEndToEndModulacionTests(TestCase):
    """Prueba de integración completa del flujo real (no simulado):

    POST /api/sync/ (bloque 'dodas') -> _upsert_dodas crea una Doda nueva
    -> sync_views.sync_endpoint encola transaction.on_commit(...) ->
    modulacion.procesar_dodas_nuevas -> EnvioModulacion.objects.create.

    django.test.TestCase envuelve cada test en una transacción que hace
    rollback al final, así que los callbacks de on_commit NUNCA se
    ejecutan por defecto — todos los demás tests de este archivo llaman a
    procesar_dodas_nuevas(...) directamente y por lo tanto no prueban el
    wiring de on_commit en sí. Aquí se usa
    self.captureOnCommitCallbacks(execute=True) para forzar la ejecución
    real de esos callbacks dentro del test, igual que ocurriría en
    producción tras un commit real."""

    def setUp(self):
        self.referencia = _referencia(num_refe='LCRR0200/26')
        self.cont = _contenedor(self.referencia, 'HLXU9998887', '40HC')
        self.perfil = _perfil(cve_capturista='CAPT99')

    def _payload(self):
        return {
            'patente':  '1656',
            'agent_id': 'test-agent',
            'dodas': [{
                'id_doda':         99001,
                'num_doda':        'DODA-99001',
                'patente':         '1656',
                'cve_caat':        '3B74',
                'cve_capt':        'CAPT99',
                'terminal_nombre': 'TERMINAL E2E',
                'referencias': [
                    {'num_refe': self.referencia.num_refe, 'cons_id': 1},
                ],
            }],
        }

    def test_post_sync_con_dodas_crea_envio_modulacion_via_on_commit_real(self):
        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()

            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse('api_sync'),
                    data=json.dumps(self._payload()),
                    content_type='application/json',
                    HTTP_AUTHORIZATION='Token test-secret',
                )

        self.assertEqual(response.status_code, 200)

        doda = Doda.objects.get(id_doda=99001)
        envio = EnvioModulacion.objects.get(doda=doda)
        self.assertEqual(envio.email_estado, 'ENVIADO')
        self.assertEqual(envio.push_estado, 'ENVIADO')

        # Un solo contenedor ligado a la referencia -> un solo push real
        # (mockeado) a BitacoraKasu.
        mock_post.assert_called_once()
        sg_cls.return_value.send.assert_called_once()

    def test_post_sync_con_no_notificar_no_manda_correo_ni_push_ni_crea_envio(self):
        """sync_agent.py manda no_notificar=True en la primera sincronización
        de una patente (bootstrap) — mismo contrato que --no-notificar en el
        management command import_firebird: la DODA se crea marcada como ya
        atendida, sin disparar el pipeline de correo/PDF/push ni crear
        EnvioModulacion."""
        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()

            payload = self._payload()
            payload['no_notificar'] = True

            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse('api_sync'),
                    data=json.dumps(payload),
                    content_type='application/json',
                    HTTP_AUTHORIZATION='Token test-secret',
                )

        self.assertEqual(response.status_code, 200)

        doda = Doda.objects.get(id_doda=99001)
        self.assertIsNotNone(doda.notificado_en)
        self.assertIsNotNone(doda.modulacion_enviada_en)
        self.assertEqual(EnvioModulacion.objects.filter(doda=doda).count(), 0)
        mock_post.assert_not_called()
        sg_cls.return_value.send.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# management command: reintentar_modulacion
# ─────────────────────────────────────────────────────────────────────────────
@override_settings(SENDGRID_API_KEY='SG.test',
                   BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
                   BITACORAKASU_API_TOKEN='token', MODULACION_FALLBACK_EMAILS=[])
class ReintentarModulacionCommandTests(TestCase):
    def setUp(self):
        self.perfil = _perfil()
        self.doda = _doda()
        self.referencia = _referencia()
        _doda_referencia(self.doda, self.referencia)
        self.cont1 = _contenedor(self.referencia, 'HLXU1234567', '40HC')

    def _run_command(self):
        from io import StringIO

        from django.core.management import call_command

        salida = StringIO()
        call_command('reintentar_modulacion', stdout=salida)
        return salida.getvalue()

    def test_reintenta_email_en_error_y_lo_deja_enviado(self):
        envio = EnvioModulacion.objects.create(
            doda=self.doda, email_estado='ERROR', push_estado='ENVIADO',
            error_detalle='email: boom sendgrid',
        )

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()

            salida = self._run_command()

        envio.refresh_from_db()
        self.assertEqual(envio.email_estado, 'ENVIADO')
        self.assertEqual(envio.push_estado, 'ENVIADO')
        sg_cls.return_value.send.assert_called_once()
        mock_post.assert_not_called()

        self.doda.refresh_from_db()
        self.assertIsNotNone(self.doda.notificado_en)

        self.assertIn('1 reintentados', salida)
        self.assertIn('1 con éxito', salida)
        self.assertIn('0 siguen en error', salida)

    def test_reintenta_solo_email_incluye_links_persistidos_del_push_anterior(self):
        """Si el push ya quedó ENVIADO en una corrida anterior (y por lo
        tanto se omite en este reintento — sólo el email está en ERROR), el
        correo que se reenvía debe incluir de todos modos los links que ese
        push anterior ya había persistido en envio.links_completar — no algo
        recalculado en esta corrida, que estaría vacío porque el push no se
        vuelve a ejecutar."""
        envio = EnvioModulacion.objects.create(
            doda=self.doda, email_estado='ERROR', push_estado='ENVIADO',
            error_detalle='email: boom sendgrid',
            links_completar={'HLXU1234567': 'https://bitacora.test/modulacion/completar/tok1/'},
        )

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()

            salida = self._run_command()

        envio.refresh_from_db()
        self.assertEqual(envio.email_estado, 'ENVIADO')
        self.assertEqual(envio.push_estado, 'ENVIADO')
        # El push no se reintenta (ya estaba ENVIADO) — cero llamadas de red.
        mock_post.assert_not_called()
        sg_cls.return_value.send.assert_called_once()

        html = sg_cls.return_value.send.call_args[0][0].get()['content'][0]['value']
        self.assertIn('https://bitacora.test/modulacion/completar/tok1/', html)
        self.assertIn('HLXU1234567', html)

        # El dict persistido no se pierde ni se recalcula vacío.
        self.assertEqual(
            envio.links_completar,
            {'HLXU1234567': 'https://bitacora.test/modulacion/completar/tok1/'},
        )

        self.assertIn('1 reintentados, 1 con éxito, 0 siguen en error', salida)

    def test_reintenta_push_en_error_y_lo_deja_enviado(self):
        envio = EnvioModulacion.objects.create(
            doda=self.doda, email_estado='ENVIADO', push_estado='ERROR',
            error_detalle='push: HLXU1234567: conexión rechazada',
        )

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            mock_post.return_value = _resp_bitacorakasu()

            salida = self._run_command()

        envio.refresh_from_db()
        self.assertEqual(envio.push_estado, 'ENVIADO')
        self.assertEqual(envio.email_estado, 'ENVIADO')
        sg_cls.return_value.send.assert_not_called()
        mock_post.assert_called_once()

        self.doda.refresh_from_db()
        self.assertIsNotNone(self.doda.modulacion_enviada_en)

        self.assertIn('1 reintentados, 1 con éxito, 0 siguen en error', salida)

    def test_sigue_en_error_si_el_reintento_vuelve_a_fallar(self):
        envio = EnvioModulacion.objects.create(
            doda=self.doda, email_estado='ERROR', push_estado='ENVIADO',
            error_detalle='email: boom sendgrid',
        )

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.side_effect = Exception('boom otra vez')

            salida = self._run_command()

        envio.refresh_from_db()
        self.assertEqual(envio.email_estado, 'ERROR')
        self.assertIn('boom otra vez', envio.error_detalle)

        self.assertIn('1 reintentados, 0 con éxito, 1 siguen en error', salida)

    def test_envio_pendiente_ambas_piernas_es_reintentado(self):
        """PENDIENTE (nunca intentado, ej. proceso murió entre el create() y el
        save() del resultado) debe ser recogido por el comando igual que ERROR —
        no debe quedar huérfano para siempre."""
        envio = EnvioModulacion.objects.create(
            doda=self.doda, email_estado='PENDIENTE', push_estado='PENDIENTE',
        )

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()

            salida = self._run_command()

        envio.refresh_from_db()
        self.assertEqual(envio.email_estado, 'ENVIADO')
        self.assertEqual(envio.push_estado, 'ENVIADO')
        sg_cls.return_value.send.assert_called_once()
        mock_post.assert_called_once()

        self.doda.refresh_from_db()
        self.assertIsNotNone(self.doda.notificado_en)
        self.assertIsNotNone(self.doda.modulacion_enviada_en)

        self.assertIn('1 reintentados, 1 con éxito, 0 siguen en error', salida)

    def test_no_toca_envios_que_no_estan_en_error(self):
        envio_ok = EnvioModulacion.objects.create(
            doda=self.doda, email_estado='ENVIADO', push_estado='ENVIADO',
        )

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            salida = self._run_command()

        sg_cls.return_value.send.assert_not_called()
        mock_post.assert_not_called()
        self.assertIn('0 reintentados, 0 con éxito, 0 siguen en error', salida)

        envio_ok.refresh_from_db()
        self.assertEqual(envio_ok.email_estado, 'ENVIADO')
        self.assertEqual(envio_ok.push_estado, 'ENVIADO')

    def test_multiples_envios_en_error_se_procesan_todos(self):
        otra_doda = _doda(id_doda=5002, num_doda='DODA-0002', cve_capt='CAPT01',
                          terminal_nombre='TERMINAL DOS')
        otra_referencia = _referencia(num_refe='LCRR0200/26')
        _doda_referencia(otra_doda, otra_referencia)
        _contenedor(otra_referencia, 'MSCU1112223', '20DC')

        EnvioModulacion.objects.create(
            doda=self.doda, email_estado='ERROR', push_estado='ENVIADO',
        )
        EnvioModulacion.objects.create(
            doda=otra_doda, email_estado='ERROR', push_estado='ENVIADO',
        )

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()

            salida = self._run_command()

        self.assertEqual(sg_cls.return_value.send.call_count, 2)
        self.assertIn('2 reintentados, 2 con éxito, 0 siguen en error', salida)

    def test_excepcion_inesperada_no_aborta_comando_y_aisle_items(self):
        """Una excepción inesperada en reintentar_envio(envio1) no debe abortar
        la iteración ni impedir procesar envio2. El item que falló debe contar
        como error, no como éxito."""
        otra_doda = _doda(id_doda=5002, num_doda='DODA-0002', cve_capt='CAPT01',
                          terminal_nombre='TERMINAL DOS')
        otra_referencia = _referencia(num_refe='LCRR0200/26')
        _doda_referencia(otra_doda, otra_referencia)
        _contenedor(otra_referencia, 'MSCU1112223', '20DC')

        envio1 = EnvioModulacion.objects.create(
            doda=self.doda, email_estado='ERROR', push_estado='ENVIADO',
        )
        envio2 = EnvioModulacion.objects.create(
            doda=otra_doda, email_estado='ERROR', push_estado='ENVIADO',
        )

        with patch('referencias.management.commands.reintentar_modulacion.reintentar_envio') as mock_reintentar:
            with patch('referencias.modulacion.SendGridAPIClient'):
                # El primer llamado lanza TypeError inesperado; el segundo retorna True
                mock_reintentar.side_effect = [TypeError('unexpected error'), True]

                # El comando no debe propagar la excepción
                salida = self._run_command()

        # Ambos envios fueron alcanzados (reintentar_envio fue llamado 2 veces)
        self.assertEqual(mock_reintentar.call_count, 2)
        # El envio1 que falló cuenta como error, envio2 como éxito
        self.assertIn('2 reintentados, 1 con éxito, 1 siguen en error', salida)


# ─────────────────────────────────────────────────────────────────────────────
# management command: reintentar_modulacion — barrido de DODAs sin EnvioModulacion
# ─────────────────────────────────────────────────────────────────────────────
@override_settings(SENDGRID_API_KEY='SG.test',
                   BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
                   BITACORAKASU_API_TOKEN='token', MODULACION_FALLBACK_EMAILS=[])
class ReintentarModulacionDodaSinEnvioTests(TestCase):
    """Si transaction.on_commit nunca corrió, o EnvioModulacion.objects.create()
    lanzó antes de crear la fila, la DODA queda con notificado_en=NULL y cero
    EnvioModulacion — invisible para el filtro de EnvioModulacion. El comando
    debe barrer también esas DODAs."""

    def setUp(self):
        self.perfil = _perfil()
        self.doda = _doda()
        self.referencia = _referencia()
        _doda_referencia(self.doda, self.referencia)
        self.cont1 = _contenedor(self.referencia, 'HLXU1234567', '40HC')

    def _run_command(self):
        from io import StringIO

        from django.core.management import call_command

        salida = StringIO()
        call_command('reintentar_modulacion', stdout=salida)
        return salida.getvalue()

    def test_doda_sin_envio_es_procesada_y_crea_envio(self):
        self.assertEqual(EnvioModulacion.objects.filter(doda=self.doda).count(), 0)
        self.assertIsNone(self.doda.notificado_en)

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()

            salida = self._run_command()

        envio = EnvioModulacion.objects.get(doda=self.doda)
        self.assertEqual(envio.email_estado, 'ENVIADO')
        self.assertEqual(envio.push_estado, 'ENVIADO')
        sg_cls.return_value.send.assert_called_once()
        mock_post.assert_called_once()

        self.doda.refresh_from_db()
        self.assertIsNotNone(self.doda.notificado_en)
        self.assertIsNotNone(self.doda.modulacion_enviada_en)

        self.assertIn('1 reintentados, 1 con éxito, 0 siguen en error', salida)

    def test_doda_con_envio_existente_no_es_barrida_dos_veces(self):
        """Una DODA que ya tiene un EnvioModulacion (aunque sea PENDIENTE) se
        reintenta vía la query de EnvioModulacion, no vía el barrido — no debe
        crear un segundo EnvioModulacion."""
        EnvioModulacion.objects.create(doda=self.doda)  # PENDIENTE/PENDIENTE

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()

            salida = self._run_command()

        self.assertEqual(EnvioModulacion.objects.filter(doda=self.doda).count(), 1)
        self.assertIn('1 reintentados, 1 con éxito, 0 siguen en error', salida)

    def test_doda_dada_de_baja_sin_envio_no_es_barrida(self):
        from django.utils import timezone

        self.doda.fecha_baja = timezone.now()
        self.doda.save(update_fields=['fecha_baja'])

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            salida = self._run_command()

        sg_cls.return_value.send.assert_not_called()
        mock_post.assert_not_called()
        self.assertEqual(EnvioModulacion.objects.filter(doda=self.doda).count(), 0)
        self.assertIn('0 reintentados, 0 con éxito, 0 siguen en error', salida)


# ─────────────────────────────────────────────────────────────────────────────
# management command: reintentar_modulacion --solo-push
# ─────────────────────────────────────────────────────────────────────────────
@override_settings(SENDGRID_API_KEY='SG.test',
                   BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
                   BITACORAKASU_API_TOKEN='token', MODULACION_FALLBACK_EMAILS=[])
class ReintentarModulacionSoloPushTests(TestCase):
    """--solo-push: reintenta únicamente el push a BitacoraKasu, sin tocar
    email_estado — para corregir el push (p.ej. tras arreglar tipo_contenedor)
    sin disparar de golpe una tanda de correos atrasados por otra causa
    (p.ej. faltaba PerfilUsuario) que ahora sí resolvería si se tocara."""

    def setUp(self):
        self.perfil = _perfil()
        self.doda = _doda()
        self.referencia = _referencia()
        _doda_referencia(self.doda, self.referencia)
        self.cont1 = _contenedor(self.referencia, 'HLXU1234567', '40HC')

    def _run_command(self, *extra_args):
        from io import StringIO

        from django.core.management import call_command

        salida = StringIO()
        call_command('reintentar_modulacion', *extra_args, stdout=salida)
        return salida.getvalue()

    def test_solo_push_reintenta_push_sin_tocar_email_en_error(self):
        envio = EnvioModulacion.objects.create(
            doda=self.doda, email_estado='ERROR', push_estado='ERROR',
            error_detalle='email: sin destinatario resuelto\npush: HLXU1234567: Faltan campos requeridos: tipo_contenedor',
        )

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            mock_post.return_value = _resp_bitacorakasu()

            salida = self._run_command('--solo-push')

        envio.refresh_from_db()
        self.assertEqual(envio.push_estado, 'ENVIADO')
        self.assertEqual(envio.email_estado, 'ERROR')  # intacto, no reintentado
        sg_cls.return_value.send.assert_not_called()
        mock_post.assert_called_once()

        self.doda.refresh_from_db()
        self.assertIsNotNone(self.doda.modulacion_enviada_en)
        self.assertIsNone(self.doda.notificado_en)

        self.assertIn('1 reintentados, 1 con éxito, 0 siguen en error', salida)
        self.assertIn('--solo-push', salida)

    def test_solo_push_omite_envios_cuyo_unico_pendiente_es_el_email(self):
        """Un envío con push ya ENVIADO y sólo el email en ERROR no debe
        entrar al conteo de --solo-push (no hay nada que reintentar ahí)."""
        envio = EnvioModulacion.objects.create(
            doda=self.doda, email_estado='ERROR', push_estado='ENVIADO',
        )

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            salida = self._run_command('--solo-push')

        sg_cls.return_value.send.assert_not_called()
        mock_post.assert_not_called()
        envio.refresh_from_db()
        self.assertEqual(envio.email_estado, 'ERROR')
        self.assertIn('0 reintentados, 0 con éxito, 0 siguen en error', salida)

    def test_solo_push_en_doda_sin_envio_no_manda_correo(self):
        """DODA sin ningún EnvioModulacion (barrido): con --solo-push se crea
        el EnvioModulacion y se intenta el push, pero el email se deja
        PENDIENTE (nunca se llama a SendGrid)."""
        self.assertEqual(EnvioModulacion.objects.filter(doda=self.doda).count(), 0)

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            mock_post.return_value = _resp_bitacorakasu()

            salida = self._run_command('--solo-push')

        envio = EnvioModulacion.objects.get(doda=self.doda)
        self.assertEqual(envio.push_estado, 'ENVIADO')
        self.assertEqual(envio.email_estado, 'PENDIENTE')
        sg_cls.return_value.send.assert_not_called()
        mock_post.assert_called_once()

        self.doda.refresh_from_db()
        self.assertIsNotNone(self.doda.modulacion_enviada_en)
        self.assertIsNone(self.doda.notificado_en)

        self.assertIn('1 reintentados, 1 con éxito, 0 siguen en error', salida)

    def test_email_pendiente_se_reintenta_despues_sin_la_bandera(self):
        """El correo que --solo-push dejó pendiente sí se manda en una
        corrida posterior normal (sin --solo-push), una vez que se decide
        destaparlo."""
        EnvioModulacion.objects.create(
            doda=self.doda, email_estado='ERROR', push_estado='ERROR',
        )

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            mock_post.return_value = _resp_bitacorakasu()
            self._run_command('--solo-push')

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()

            salida = self._run_command()

        envio = EnvioModulacion.objects.get(doda=self.doda)
        self.assertEqual(envio.email_estado, 'ENVIADO')
        sg_cls.return_value.send.assert_called_once()
        self.assertIn('1 reintentados, 1 con éxito, 0 siguen en error', salida)


# ─────────────────────────────────────────────────────────────────────────────
# management command: reintentar_modulacion — filtro por año (--anio / --todos)
# ─────────────────────────────────────────────────────────────────────────────
@override_settings(SENDGRID_API_KEY='SG.test',
                   BITACORAKASU_MODULACION_URL='https://bitacora.test/api/modulacion',
                   BITACORAKASU_API_TOKEN='token', MODULACION_FALLBACK_EMAILS=[])
class ReintentarModulacionFiltroAnioTests(TestCase):
    """Sin filtro, un reintento manual barre de golpe todo el historial
    acumulado — cientos de DODAs viejas terminan agrupadas bajo el día del
    reintento en BitacoraKasu. Por default el comando sólo procesa DODAs de
    2026; --anio elige otro año y --todos quita el filtro."""

    def setUp(self):
        self.perfil = _perfil()
        self.referencia = _referencia()
        self.cont1 = _contenedor(self.referencia, 'HLXU1234567', '40HC')

        self.doda_2026 = _doda(
            id_doda=6001, num_doda='DODA-2026',
            fecha_doda=timezone.make_aware(datetime(2026, 1, 10)),
        )
        _doda_referencia(self.doda_2026, self.referencia, cons_id=1)

        self.doda_2025 = _doda(
            id_doda=6002, num_doda='DODA-2025',
            fecha_doda=timezone.make_aware(datetime(2025, 11, 20)),
        )
        _doda_referencia(self.doda_2025, self.referencia, cons_id=2)

    def _run_command(self, *extra_args):
        from io import StringIO

        from django.core.management import call_command

        salida = StringIO()
        call_command('reintentar_modulacion', *extra_args, stdout=salida)
        return salida.getvalue()

    def test_default_solo_procesa_dodas_del_2026(self):
        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()

            salida = self._run_command()

        self.assertIn('1 reintentados, 1 con éxito, 0 siguen en error', salida)
        self.doda_2026.refresh_from_db()
        self.doda_2025.refresh_from_db()
        self.assertIsNotNone(self.doda_2026.modulacion_enviada_en)
        self.assertIsNone(self.doda_2025.modulacion_enviada_en)
        self.assertEqual(EnvioModulacion.objects.filter(doda=self.doda_2025).count(), 0)

    def test_anio_explicito_procesa_solo_ese_anio(self):
        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()

            salida = self._run_command('--anio', '2025')

        self.assertIn('1 reintentados, 1 con éxito, 0 siguen en error', salida)
        self.doda_2025.refresh_from_db()
        self.doda_2026.refresh_from_db()
        self.assertIsNotNone(self.doda_2025.modulacion_enviada_en)
        self.assertIsNone(self.doda_2026.modulacion_enviada_en)

    def test_todos_ignora_el_filtro_por_anio(self):
        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()

            salida = self._run_command('--todos')

        self.assertIn('2 reintentados, 2 con éxito, 0 siguen en error', salida)
        self.doda_2025.refresh_from_db()
        self.doda_2026.refresh_from_db()
        self.assertIsNotNone(self.doda_2025.modulacion_enviada_en)
        self.assertIsNotNone(self.doda_2026.modulacion_enviada_en)
