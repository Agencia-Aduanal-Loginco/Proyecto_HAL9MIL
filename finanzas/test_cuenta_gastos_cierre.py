from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
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


class GuardCierreViewsTests(TestCase):
    def setUp(self):
        _login_finanzas(self)
        self.referencia = _referencia('LCRR0002/26')
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(
            referencia=self.referencia, cerrada_por=self.user
        )

    def _assert_bloqueada(self, url_name, **extra):
        url = reverse(url_name, kwargs={'num_refe': self.referencia.num_refe})
        resp = self.client.post(url, extra)
        self.assertRedirects(
            resp,
            reverse('finanzas:referencia_estado',
                    kwargs={'num_refe': self.referencia.num_refe}),
        )

    def test_anticipo_bloqueado_con_cierre(self):
        self._assert_bloqueada('finanzas:anticipo_crear')
        from finanzas.models import Anticipo
        self.assertEqual(Anticipo.objects.count(), 0)

    def test_gasto_bloqueado_con_cierre(self):
        self._assert_bloqueada('finanzas:gasto_crear')
        from finanzas.models import GastoReferencia
        self.assertEqual(GastoReferencia.objects.count(), 0)

    def test_subir_xml_bloqueado_con_cierre(self):
        self._assert_bloqueada('finanzas:subir_xml')
        from finanzas.models import XMLProveedor
        self.assertEqual(XMLProveedor.objects.count(), 0)

    def test_anticipo_permitido_sin_cierre(self):
        otra = _referencia('LCRR0003/26')
        url = reverse('finanzas:anticipo_crear', kwargs={'num_refe': otra.num_refe})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
