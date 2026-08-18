"""Tests para referencias.bitacorakasu_client — cliente HTTP para BitacoraKasu."""

import json
from unittest.mock import patch, MagicMock

import requests
from django.test import TestCase, override_settings

from .bitacorakasu_client import enviar_modulacion, BitacoraKasuError

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
