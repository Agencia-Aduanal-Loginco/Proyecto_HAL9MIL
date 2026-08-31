from unittest.mock import patch

from django.test import TestCase, override_settings

from reportes.jobs import _wa_ia_modulo
from reportes.models import Destinatario


@override_settings(
    TWILIO_CONTENT_SID_IA_HAL9MIL='HX1495d5e097d913edca34109f4336e012'
)
class WaIaModuloTests(TestCase):
    def setUp(self):
        Destinatario.objects.create(
            nombre='Prueba',
            email='prueba@example.com',
            activo=True,
            whatsapp='5217535342088',
            recibe_wa_ia_referencias=True,
        )

    @patch('whatsapp.client.send_template')
    def test_variable_ia_no_contiene_saltos_de_linea(self, mock_send):
        # Salida típica de Claude: párrafos y viñetas separados por '\n'.
        texto_ia = (
            "**Reporte Ejecutivo**\n\n"
            "Durante la semana se validaron 68 operaciones (LCLF 32, LCRR 28, LCMJ 8).\n\n"
            "Recomendaciones:\n"
            "- Reforzar el equipo de validación.\n"
            "- Priorizar LCMJ para reducir el acumulado."
        )

        _wa_ia_modulo(texto_ia, 'referencias', '01/06 – 07/06/2026')

        self.assertTrue(mock_send.called, 'send_template no fue invocado')
        (_numero, _sid, variables), _kwargs = mock_send.call_args
        self.assertNotIn('\n', variables['1'])
        self.assertNotIn('\t', variables['1'])
        self.assertNotIn('  ', variables['1'])
        # Twilio limita cada variable; el código apunta a <= 400 chars.
        self.assertLessEqual(len(variables['1']), 400)
