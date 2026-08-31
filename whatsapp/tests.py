import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from whatsapp.client import clean_wa_var, send_template


class CleanWaVarTests(SimpleTestCase):
    """
    WhatsApp rechaza (Twilio error 21656) los valores de variable de plantilla
    que contengan saltos de línea, tabuladores o más de 4 espacios seguidos.
    """

    def test_colapsa_saltos_de_linea_tabs_y_espacios(self):
        sucio = "línea uno\n\nlínea dos\tcolumna\n- viñeta     con espacios"
        limpio = clean_wa_var(sucio)
        self.assertNotIn('\n', limpio)
        self.assertNotIn('\t', limpio)
        self.assertNotIn('  ', limpio)
        self.assertEqual(limpio, "línea uno línea dos columna - viñeta con espacios")

    def test_acepta_valores_no_string(self):
        self.assertEqual(clean_wa_var(42), '42')


@override_settings(
    TWILIO_ACCOUNT_SID='ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
    TWILIO_AUTH_TOKEN='token-de-prueba',
    TWILIO_WHATSAPP_FROM='+15550000000',
)
class SendTemplateSanitizeTests(SimpleTestCase):
    @patch('whatsapp.client._twilio_client')
    def test_send_template_elimina_saltos_de_linea_de_las_variables(self, mock_client):
        mock_client.return_value.messages.create.return_value = MagicMock(
            sid='SM123', status='queued'
        )

        send_template(
            '5217535342088',
            'HX1495d5e097d913edca34109f4336e012',
            {'1': 'Reporte\n\nDurante la semana...\n- punto uno\n- punto dos'},
        )

        _, kwargs = mock_client.return_value.messages.create.call_args
        enviado = json.loads(kwargs['content_variables'])
        self.assertNotIn('\n', enviado['1'])
        self.assertNotIn('\t', enviado['1'])
        self.assertNotIn('  ', enviado['1'])
