from datetime import date

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import CuentaGastos, Referencia


class SidebarFinanzasVisibilityTests(TestCase):
    def setUp(self):
        self.grupo_finanzas, _ = Group.objects.get_or_create(name='Finanzas')
        self.usuario_con_grupo = User.objects.create_user('side_con_grupo', password='x')
        self.usuario_con_grupo.groups.add(self.grupo_finanzas)
        self.usuario_sin_grupo = User.objects.create_user('side_sin_grupo', password='x')

    def test_usuario_con_grupo_ve_el_link_de_finanzas(self):
        self.client.force_login(self.usuario_con_grupo)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'href="/finanzas/"')

    def test_usuario_sin_grupo_no_ve_el_link_de_finanzas(self):
        self.client.force_login(self.usuario_sin_grupo)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'href="/finanzas/"')


def _crear_referencia_pendiente(num_refe, nombre_cliente, fecha_pago, **extra):
    defaults = dict(
        patente='1656', prefijo='LCRR',
        num_operacion='OP1', linea_captura='LC1',
        es_rectificacion=False,
        nombre_cliente=nombre_cliente,
        fecha_pago=fecha_pago,
    )
    defaults.update(extra)
    return Referencia.objects.create(num_refe=num_refe, **defaults)


class CuentaGastosAgruparPorClienteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('cg_agrupa', password='x')
        self.client.force_login(self.user)
        # Dos referencias de ACME (fechas distintas) y una de BETA.
        self.acme_vieja = _crear_referencia_pendiente(
            'LCRR0001/26', 'ACME SA', date(2026, 7, 1),
        )
        self.acme_nueva = _crear_referencia_pendiente(
            'LCRR0002/26', 'ACME SA', date(2026, 7, 10),
        )
        self.beta = _crear_referencia_pendiente(
            'LCRR0003/26', 'BETA SA', date(2026, 7, 5),
        )
        # Referencia ya finalizada: no debe aparecer en pendientes.
        finalizada = _crear_referencia_pendiente(
            'LCRR0004/26', 'ACME SA', date(2026, 7, 2),
        )
        CuentaGastos.objects.create(referencia=finalizada, fecha_finalizacion=timezone.now())

    def test_grupos_cliente_en_contexto_agrupa_por_cliente(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        grupos = resp.context['grupos_cliente']
        nombres = [g['nombre_cliente'] for g in grupos]
        self.assertEqual(nombres, ['ACME SA', 'BETA SA'])
        acme_group = grupos[0]
        self.assertEqual(len(acme_group['referencias']), 2)

    def test_orden_dentro_del_grupo_es_por_fecha_pago_descendente(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        acme_group = resp.context['grupos_cliente'][0]
        refs = acme_group['referencias']
        self.assertEqual(refs[0].num_refe, 'LCRR0002/26')  # más reciente primero
        self.assertEqual(refs[1].num_refe, 'LCRR0001/26')

    def test_referencia_finalizada_no_aparece_en_grupos(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        todas_las_refs = [
            r.num_refe
            for g in resp.context['grupos_cliente']
            for r in g['referencias']
        ]
        self.assertNotIn('LCRR0004/26', todas_las_refs)

    def test_template_renderiza_un_details_colapsado_por_cliente(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        html = resp.content.decode()
        self.assertContains(resp, '<details')
        self.assertContains(resp, 'ACME SA')
        self.assertContains(resp, 'BETA SA')
        # Contraído por defecto: no debe existir un <details ... open>
        self.assertNotIn('<details open', html)
        self.assertNotIn('<details class="group" open', html)

    def test_columna_cliente_ya_no_aparece_en_las_filas(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        self.assertNotContains(resp, '<th class="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Cliente</th>')

    def test_boton_finalizar_sigue_presente(self):
        self.user.is_staff = True
        self.user.save()
        resp = self.client.get(reverse('cuenta_gastos'))
        self.assertContains(resp, 'Finalizar')
        self.assertContains(resp, 'abrirModal(')
