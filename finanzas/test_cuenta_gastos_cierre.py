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


class CerrarReabrirViewsTests(TestCase):
    def setUp(self):
        _login_finanzas(self)
        self.referencia = _referencia('LCRR0004/26')

    def _url(self, name):
        return reverse(name, kwargs={'num_refe': self.referencia.num_refe})

    def test_cerrar_crea_cierre_activo(self):
        from finanzas.models import CierreCuentaGastos
        resp = self.client.post(self._url('finanzas:cerrar_cg'), {'nota': 'lista'})
        self.assertRedirects(resp, self._url('finanzas:referencia_estado'))
        cierre = CierreCuentaGastos.activo_para(self.referencia)
        self.assertIsNotNone(cierre)
        self.assertEqual(cierre.cerrada_por, self.user)
        self.assertEqual(cierre.nota, 'lista')

    def test_get_no_cierra(self):
        from finanzas.models import CierreCuentaGastos
        self.client.get(self._url('finanzas:cerrar_cg'))
        self.assertIsNone(CierreCuentaGastos.activo_para(self.referencia))

    def test_usuario_sin_finanzas_no_puede_cerrar(self):
        from django.contrib.auth.models import User as U
        U.objects.create_user('ajeno', password='x')
        self.client.login(username='ajeno', password='x')
        self.client.post(self._url('finanzas:cerrar_cg'))
        from finanzas.models import CierreCuentaGastos
        self.assertIsNone(CierreCuentaGastos.activo_para(self.referencia))

    def test_reabrir_requiere_superusuario(self):
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        self.client.post(self._url('finanzas:reabrir_cg'))
        self.assertIsNotNone(CierreCuentaGastos.activo_para(self.referencia))

    def test_superusuario_reabre(self):
        from django.contrib.auth.models import User as U
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        superu = U.objects.create_superuser('root', password='x')
        self.client.login(username='root', password='x')
        self.client.post(self._url('finanzas:reabrir_cg'))
        self.assertIsNone(CierreCuentaGastos.activo_para(self.referencia))
        cierre = CierreCuentaGastos.objects.get(referencia=self.referencia)
        self.assertEqual(cierre.reabierta_por, superu)

    def test_recierre_tras_reapertura_limpia_campos(self):
        from django.utils import timezone as tz
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(
            referencia=self.referencia, cerrada_por=self.user,
            reabierta_por=self.user, reabierta_en=tz.now(),
        )
        self.client.post(self._url('finanzas:cerrar_cg'))
        cierre = CierreCuentaGastos.objects.get(referencia=self.referencia)
        self.assertTrue(cierre.activa)
        self.assertIsNone(cierre.reabierta_por)


class EstadoFinancieroTemplateTests(TestCase):
    def setUp(self):
        _login_finanzas(self)
        self.referencia = _referencia('LCRR0005/26')
        self.url = reverse('finanzas:referencia_estado',
                           kwargs={'num_refe': self.referencia.num_refe})

    def test_abierta_muestra_botones_y_upload(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, '+ Anticipo')
        self.assertContains(resp, 'Subir XML de proveedor')
        self.assertContains(resp, 'Cerrar cuenta de gastos')
        self.assertNotContains(resp, 'Enviar al cliente')

    def test_cerrada_oculta_botones_y_muestra_balanza(self):
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        resp = self.client.get(self.url)
        self.assertNotContains(resp, '+ Anticipo')
        self.assertNotContains(resp, 'Subir XML de proveedor')
        self.assertContains(resp, 'Cuenta de gastos cerrada')
        self.assertContains(resp, 'Balanza de la cuenta de gastos')
        self.assertContains(resp, 'Enviar al cliente')
        self.assertContains(resp, 'Emitir factura')  # nunca se bloquea

    def test_cerrada_no_muestra_reabrir_a_no_superusuario(self):
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        resp = self.client.get(self.url)
        self.assertNotContains(resp, 'Reabrir cuenta')

    def test_cerrada_muestra_reabrir_a_superusuario(self):
        from django.contrib.auth.models import User as U
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        U.objects.create_superuser('root2', password='x')
        self.client.login(username='root2', password='x')
        resp = self.client.get(self.url)
        self.assertContains(resp, 'Reabrir cuenta')

    def test_con_envio_previo_muestra_historial_y_reenviar(self):
        from finanzas.models import CierreCuentaGastos, NotificacionCuentaGastos
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        NotificacionCuentaGastos.objects.create(
            referencia=self.referencia, destinatario='c@x.com',
            enviado_por=self.user,
        )
        resp = self.client.get(self.url)
        self.assertContains(resp, 'Historial de envíos')
        self.assertContains(resp, 'c@x.com')
        self.assertContains(resp, 'Reenviar')

    def test_destinatario_prellenado_con_fallback(self):
        from clientes.models import Cliente
        from finanzas.models import CierreCuentaGastos
        self.referencia.cve_cliente = 'CAC001'
        self.referencia.save(update_fields=['cve_cliente'])
        Cliente.objects.create(nombre_cliente='CACIPA', cve_cliente='CAC001',
                               email_cobranza='cob@cacipa.com')
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        resp = self.client.get(self.url)
        self.assertContains(resp, 'cob@cacipa.com')
