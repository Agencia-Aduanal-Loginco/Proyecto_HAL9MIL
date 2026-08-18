"""Tests para referencias.bitacorakasu_client — cliente HTTP para BitacoraKasu."""

import json
from decimal import Decimal
from unittest.mock import patch, MagicMock

import requests
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

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
         terminal_nombre='TERMINAL PORTUARIA UNO'):
    return Doda.objects.create(
        id_doda=id_doda, num_doda=num_doda, patente='1656', cve_caat='CAAT01',
        cve_capt=cve_capt, terminal_nombre=terminal_nombre,
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

        self.doda.refresh_from_db()
        self.assertIsNotNone(self.doda.notificado_en)
        self.assertIsNotNone(self.doda.modulacion_enviada_en)

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
