from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from referencias.models import Referencia


def _referencia(num='LCRR0001/26'):
    return Referencia.objects.create(num_refe=num, patente='1656', prefijo='LCRR')


def _login_finanzas(test, username='cg_user'):
    grupo, _ = Group.objects.get_or_create(name='Finanzas')
    test.user = User.objects.create_user(username, password='x')
    test.user.groups.add(grupo)
    test.client.login(username=username, password='x')


class CierreCuentaGastosModelTests(TestCase):
    def setUp(self):
        self.referencia = _referencia()
        self.user = User.objects.create_user('cerrador', password='x')

    def test_cierre_nuevo_esta_activo(self):
        from finanzas.models import CierreCuentaGastos
        cierre = CierreCuentaGastos.objects.create(
            referencia=self.referencia, cerrada_por=self.user
        )
        self.assertTrue(cierre.activa)
        self.assertEqual(CierreCuentaGastos.activo_para(self.referencia), cierre)

    def test_cierre_reabierto_no_esta_activo(self):
        from finanzas.models import CierreCuentaGastos
        cierre = CierreCuentaGastos.objects.create(
            referencia=self.referencia, cerrada_por=self.user,
            reabierta_por=self.user, reabierta_en=timezone.now(),
        )
        self.assertFalse(cierre.activa)
        self.assertIsNone(CierreCuentaGastos.activo_para(self.referencia))

    def test_referencia_sin_cierre(self):
        from finanzas.models import CierreCuentaGastos
        self.assertIsNone(CierreCuentaGastos.activo_para(self.referencia))


class NotificacionCuentaGastosModelTests(TestCase):
    def test_notificacion_default_enviado(self):
        from finanzas.models import NotificacionCuentaGastos
        notif = NotificacionCuentaGastos.objects.create(
            referencia=_referencia(), destinatario='cliente@example.com'
        )
        self.assertEqual(notif.estado, 'ENVIADO')
        self.assertFalse(notif.es_reenvio)
        self.assertIsNone(notif.entregado_en)
        self.assertIsNone(notif.leido_en)
