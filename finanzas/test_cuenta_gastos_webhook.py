import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from referencias.models import Referencia


def _notif(num='LCRR0500/26'):
    from finanzas.models import NotificacionCuentaGastos
    ref = Referencia.objects.create(num_refe=num, patente='1656', prefijo='LCRR')
    return NotificacionCuentaGastos.objects.create(
        referencia=ref, destinatario='c@x.com'
    )


class ProcesarEventoTests(TestCase):
    def setUp(self):
        self.notif = _notif()

    def _evento(self, tipo, **extra):
        return {'event': tipo, 'notificacion_cg_id': str(self.notif.pk),
                'timestamp': 1770000000, **extra}

    def _procesar(self, tipo, **extra):
        from finanzas.cuenta_gastos_envio import procesar_evento_sendgrid
        procesar_evento_sendgrid(self._evento(tipo, **extra))
        self.notif.refresh_from_db()

    def test_delivered_marca_entregado(self):
        self._procesar('delivered')
        self.assertEqual(self.notif.estado, 'ENTREGADO')
        self.assertIsNotNone(self.notif.entregado_en)

    def test_open_marca_leido(self):
        self._procesar('open')
        self.assertEqual(self.notif.estado, 'LEIDO')
        self.assertIsNotNone(self.notif.leido_en)

    def test_delivered_tardio_no_degrada_leido(self):
        self._procesar('open')
        self._procesar('delivered')
        self.assertEqual(self.notif.estado, 'LEIDO')
        self.assertIsNotNone(self.notif.entregado_en)  # timestamp sí se llena

    def test_bounce_marca_rebotado_con_razon(self):
        self._procesar('bounce', reason='mailbox unavailable')
        self.assertEqual(self.notif.estado, 'REBOTADO')
        self.assertIn('mailbox unavailable', self.notif.error_msg)

    def test_evento_sin_id_se_ignora(self):
        from finanzas.cuenta_gastos_envio import procesar_evento_sendgrid
        procesar_evento_sendgrid({'event': 'delivered'})  # no lanza
        self.notif.refresh_from_db()
        self.assertEqual(self.notif.estado, 'ENVIADO')

    def test_evento_desconocido_se_ignora(self):
        self._procesar('processed')
        self.assertEqual(self.notif.estado, 'ENVIADO')


@override_settings(SENDGRID_WEBHOOK_PUBLIC_KEY='clave-publica-test')
class WebhookViewTests(TestCase):
    def setUp(self):
        self.notif = _notif('LCRR0501/26')
        self.url = reverse('finanzas:sendgrid_webhook')
        self.payload = json.dumps([{
            'event': 'delivered',
            'notificacion_cg_id': str(self.notif.pk),
            'timestamp': 1770000000,
        }])

    def _post(self):
        return self.client.post(self.url, self.payload,
                                content_type='application/json')

    def test_firma_valida_procesa_eventos(self):
        with patch('finanzas.views_cuenta_gastos.EventWebhook') as ew:
            ew.return_value.verify_signature.return_value = True
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.notif.refresh_from_db()
        self.assertEqual(self.notif.estado, 'ENTREGADO')

    def test_firma_invalida_devuelve_403(self):
        with patch('finanzas.views_cuenta_gastos.EventWebhook') as ew:
            ew.return_value.verify_signature.return_value = False
            resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self.notif.refresh_from_db()
        self.assertEqual(self.notif.estado, 'ENVIADO')

    @override_settings(SENDGRID_WEBHOOK_PUBLIC_KEY='')
    def test_sin_clave_configurada_devuelve_403(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 403)

    def test_get_no_permitido(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)
