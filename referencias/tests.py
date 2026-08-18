from datetime import date

from django.contrib.auth.models import Group, User
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import CuentaGastos, Referencia, Doda, DodaReferencia


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


class CuentaGastosPaginarPorClienteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('cg_pagina', password='x')
        self.client.force_login(self.user)
        # CLIENTE 000 tiene 2 referencias, el resto (001..050) tiene 1 cada
        # uno: 51 clientes distintos, 52 referencias en total. Con esta
        # mezcla, paginar por filas (50 referencias) y paginar por clientes
        # (50 clientes) dan resultados distintos en la página 1 (49 clientes
        # completos vs. 50), lo que permite que las pruebas distingan de
        # verdad el comportamiento nuevo del viejo.
        _crear_referencia_pendiente('LCRR0000/26', 'CLIENTE 000', date(2026, 7, 1))
        _crear_referencia_pendiente('LCRR0000B/26', 'CLIENTE 000', date(2026, 7, 2))
        for i in range(1, 51):
            _crear_referencia_pendiente(
                f'LCRR{i:04d}/26', f'CLIENTE {i:03d}', date(2026, 7, 1),
            )

    def test_pagina_1_trae_50_clientes_completos(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        grupos = resp.context['grupos_cliente']
        self.assertEqual(len(grupos), 50)

    def test_pagina_2_trae_el_cliente_restante(self):
        resp = self.client.get(reverse('cuenta_gastos'), {'page': 2})
        grupos = resp.context['grupos_cliente']
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]['nombre_cliente'], 'CLIENTE 050')

    def test_total_de_paginas_es_por_clientes_no_por_referencias(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        self.assertEqual(resp.context['page'].paginator.num_pages, 2)
        self.assertEqual(resp.context['page'].paginator.count, 51)

    def test_pie_de_pagina_muestra_clientes(self):
        resp = self.client.get(reverse('cuenta_gastos'))
        self.assertContains(resp, '51 clientes')


class DodaBasicCreationTests(TestCase):
    def test_crear_doda_basica(self):
        """Test basic creation of a Doda instance."""
        doda = Doda.objects.create(
            id_doda=123,
            num_doda='DODA-001',
            patente='1656',
            cve_caat='101010',
            cve_capt='CAPT001',
            terminal_cve='TER1',
            terminal_nombre='Terminal 1',
            fecha_doda=timezone.now(),
            fecha_baja=None,
            notificado_en=None,
            modulacion_enviada_en=None,
        )
        self.assertEqual(doda.id_doda, 123)
        self.assertEqual(doda.num_doda, 'DODA-001')
        self.assertEqual(doda.patente, '1656')
        self.assertEqual(str(doda), 'DODA-001')

    def test_doda_str_fallback_a_id(self):
        """Test __str__ fallback to id_doda when num_doda is empty."""
        doda = Doda.objects.create(
            id_doda=456,
            patente='1656',
        )
        self.assertEqual(str(doda), '456')

    def test_id_doda_unico(self):
        """Test that id_doda is unique."""
        Doda.objects.create(id_doda=789, patente='1656')
        with self.assertRaises(IntegrityError):
            Doda.objects.create(id_doda=789, patente='1656')

    def test_doda_null_fields_permitidos(self):
        """Test that nullable fields work correctly."""
        doda = Doda.objects.create(
            id_doda=999,
            patente='1656',
            fecha_doda=None,
            fecha_baja=None,
            notificado_en=None,
            modulacion_enviada_en=None,
        )
        self.assertIsNone(doda.fecha_doda)
        self.assertIsNone(doda.fecha_baja)
        self.assertIsNone(doda.notificado_en)
        self.assertIsNone(doda.modulacion_enviada_en)


class DodaReferenciaBasicCreationTests(TestCase):
    def setUp(self):
        self.doda = Doda.objects.create(
            id_doda=1000,
            num_doda='DODA-100',
            patente='1656',
        )
        self.referencia = Referencia.objects.create(
            num_refe='LCRR0001/26',
            patente='1656',
            prefijo='LCRR',
        )

    def test_crear_doda_referencia_basica(self):
        """Test basic creation of a DodaReferencia instance."""
        doda_ref = DodaReferencia.objects.create(
            doda=self.doda,
            referencia=self.referencia,
            num_refe='LCRR0001/26',
            cons_id=1,
        )
        self.assertEqual(doda_ref.doda, self.doda)
        self.assertEqual(doda_ref.referencia, self.referencia)
        self.assertEqual(doda_ref.num_refe, 'LCRR0001/26')
        self.assertEqual(doda_ref.cons_id, 1)

    def test_doda_referencia_sin_referencia(self):
        """Test that DodaReferencia can be created without a Referencia (null=True)."""
        doda_ref = DodaReferencia.objects.create(
            doda=self.doda,
            referencia=None,
            num_refe='LCRR0001/26',
            cons_id=2,
        )
        self.assertEqual(doda_ref.doda, self.doda)
        self.assertIsNone(doda_ref.referencia)
        self.assertEqual(doda_ref.num_refe, 'LCRR0001/26')

    def test_unique_together_doda_cons_id(self):
        """Test that (doda, cons_id) unique_together constraint works."""
        DodaReferencia.objects.create(
            doda=self.doda,
            referencia=self.referencia,
            num_refe='LCRR0001/26',
            cons_id=5,
        )
        with self.assertRaises(IntegrityError):
            DodaReferencia.objects.create(
                doda=self.doda,
                referencia=None,
                num_refe='LCRR0002/26',
                cons_id=5,
            )

    def test_related_name_referencia_dodas(self):
        """Test that related_name='dodas' works on Referencia."""
        doda_ref1 = DodaReferencia.objects.create(
            doda=self.doda,
            referencia=self.referencia,
            num_refe='LCRR0001/26',
            cons_id=1,
        )
        doda_ref2 = DodaReferencia.objects.create(
            doda=self.doda,
            referencia=self.referencia,
            num_refe='LCRR0001/26',
            cons_id=2,
        )
        self.assertEqual(self.referencia.dodas.count(), 2)
        self.assertIn(doda_ref1, self.referencia.dodas.all())
        self.assertIn(doda_ref2, self.referencia.dodas.all())

    def test_related_name_doda_referencias_doda(self):
        """Test that related_name='referencias_doda' works on Doda."""
        doda_ref1 = DodaReferencia.objects.create(
            doda=self.doda,
            referencia=self.referencia,
            num_refe='LCRR0001/26',
            cons_id=1,
        )
        doda_ref2 = DodaReferencia.objects.create(
            doda=self.doda,
            referencia=None,
            num_refe='LCRR0002/26',
            cons_id=2,
        )
        self.assertEqual(self.doda.referencias_doda.count(), 2)
        self.assertIn(doda_ref1, self.doda.referencias_doda.all())
        self.assertIn(doda_ref2, self.doda.referencias_doda.all())

    def test_cascade_delete_doda_deletes_doda_referencia(self):
        """Test that deleting a Doda cascades to DodaReferencia."""
        DodaReferencia.objects.create(
            doda=self.doda,
            referencia=self.referencia,
            num_refe='LCRR0001/26',
            cons_id=1,
        )
        doda_id = self.doda.id
        self.doda.delete()
        self.assertEqual(DodaReferencia.objects.filter(doda_id=doda_id).count(), 0)

    def test_cascade_delete_referencia_deletes_doda_referencia(self):
        """Test that deleting a Referencia cascades to DodaReferencia."""
        DodaReferencia.objects.create(
            doda=self.doda,
            referencia=self.referencia,
            num_refe='LCRR0001/26',
            cons_id=1,
        )
        ref_id = self.referencia.id
        self.referencia.delete()
        self.assertEqual(DodaReferencia.objects.filter(referencia_id=ref_id).count(), 0)
