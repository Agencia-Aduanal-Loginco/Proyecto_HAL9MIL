import json
from datetime import date

from django.contrib.auth.models import Group, User
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from decimal import Decimal

from .models import Contenedor, CuentaGastos, Referencia, Doda, DodaReferencia
from .sync_views import _upsert_contenedores, _upsert_dodas, _upsert_referencias


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


class UpsertDodasTests(TestCase):
    """Tests para referencias.sync_views._upsert_dodas."""

    def _stats(self):
        return {'creadas': 0, 'actualizadas': 0, 'errores': 0}, []

    def test_crea_doda_y_dodareferencias_nuevos(self):
        stats, error_msgs = self._stats()
        payload = [{
            'id_doda':         5001,
            'num_doda':        'DODA-5001',
            'patente':         '1656',
            'cve_caat':        '3B74',
            'cve_capt':        'ANGELICA',
            'terminal_cve':    '257',
            'terminal_nombre': 'Talma servicios de carga',
            'fecha_doda':      '2026-08-10T09:00:00',
            'fecha_baja':      None,
            'referencias': [
                {'num_refe': 'LCRR0001/26', 'cons_id': 1},
                {'num_refe': 'LCRR0002/26', 'cons_id': 2},
            ],
        }]

        creadas = _upsert_dodas(payload, stats, error_msgs)

        self.assertEqual(stats['errores'], 0)
        self.assertEqual(error_msgs, [])
        self.assertEqual(len(creadas), 1)
        self.assertEqual(creadas[0].id_doda, 5001)

        doda = Doda.objects.get(id_doda=5001)
        self.assertEqual(doda.num_doda, 'DODA-5001')
        self.assertEqual(doda.patente, '1656')
        self.assertEqual(doda.cve_caat, '3B74')
        self.assertEqual(doda.terminal_cve, '257')
        self.assertEqual(doda.terminal_nombre, 'Talma servicios de carga')
        self.assertIsNotNone(doda.fecha_doda)
        self.assertIsNone(doda.fecha_baja)
        self.assertEqual(DodaReferencia.objects.filter(doda=doda).count(), 2)

    def test_actualiza_doda_existente_sin_duplicar(self):
        Doda.objects.create(id_doda=6001, patente='1656', num_doda='OLD', cve_caat='3B74')
        stats, error_msgs = self._stats()

        creadas = _upsert_dodas([{
            'id_doda':  6001,
            'num_doda': 'NUEVO-FOLIO',
            'patente':  '1656',
            'cve_caat': '3B74',
            'referencias': [],
        }], stats, error_msgs)

        self.assertEqual(creadas, [])
        self.assertEqual(Doda.objects.count(), 1)
        doda = Doda.objects.get(id_doda=6001)
        self.assertEqual(doda.num_doda, 'NUEVO-FOLIO')

    def test_no_filtra_por_cve_caat_el_filtro_real_ocurre_en_el_origen(self):
        """_upsert_dodas procesa lo que recibe; el filtro CVE_CAAT ocurre en la query SQL, no aquí."""
        stats, error_msgs = self._stats()

        _upsert_dodas([{
            'id_doda':  7001,
            'patente':  '1656',
            'cve_caat': 'OTRA01',
            'referencias': [],
        }], stats, error_msgs)

        self.assertTrue(Doda.objects.filter(id_doda=7001, cve_caat='OTRA01').exists())

    def test_dodareferencia_se_liga_a_referencia_local_existente(self):
        ref = Referencia.objects.create(num_refe='LCRR0099/26', patente='1656', prefijo='LCRR')
        stats, error_msgs = self._stats()

        _upsert_dodas([{
            'id_doda':  8001,
            'patente':  '1656',
            'cve_caat': '3B74',
            'referencias': [{'num_refe': 'LCRR0099/26', 'cons_id': 1}],
        }], stats, error_msgs)

        doda_ref = DodaReferencia.objects.get(doda__id_doda=8001, cons_id=1)
        self.assertEqual(doda_ref.referencia_id, ref.id)
        self.assertEqual(doda_ref.num_refe, 'LCRR0099/26')

    def test_dodareferencia_sin_referencia_local_queda_null(self):
        stats, error_msgs = self._stats()

        _upsert_dodas([{
            'id_doda':  9001,
            'patente':  '1656',
            'cve_caat': '3B74',
            'referencias': [{'num_refe': 'LCRR9999/26', 'cons_id': 1}],
        }], stats, error_msgs)

        doda_ref = DodaReferencia.objects.get(doda__id_doda=9001, cons_id=1)
        self.assertIsNone(doda_ref.referencia)

    def test_actualiza_dodareferencia_existente_por_cons_id(self):
        doda = Doda.objects.create(id_doda=10001, patente='1656', cve_caat='3B74')
        DodaReferencia.objects.create(doda=doda, num_refe='LCRR0001/26', cons_id=1)
        stats, error_msgs = self._stats()

        _upsert_dodas([{
            'id_doda':  10001,
            'patente':  '1656',
            'cve_caat': '3B74',
            'referencias': [{'num_refe': 'LCRR0002/26', 'cons_id': 1}],
        }], stats, error_msgs)

        self.assertEqual(DodaReferencia.objects.filter(doda=doda).count(), 1)
        doda_ref = DodaReferencia.objects.get(doda=doda, cons_id=1)
        self.assertEqual(doda_ref.num_refe, 'LCRR0002/26')

    def test_item_sin_id_doda_se_omite_sin_error(self):
        stats, error_msgs = self._stats()

        creadas = _upsert_dodas([{'patente': '1656', 'cve_caat': '3B74', 'referencias': []}],
                                 stats, error_msgs)

        self.assertEqual(creadas, [])
        self.assertEqual(stats['errores'], 0)
        self.assertEqual(Doda.objects.count(), 0)

    def test_baj_doda_se_persiste(self):
        stats, error_msgs = self._stats()
        _upsert_dodas([{
            'id_doda':  12001,
            'patente':  '1656',
            'cve_caat': '3B74',
            'baj_doda': 'DODA-ORIGINAL-9',
            'referencias': [],
        }], stats, error_msgs)
        self.assertEqual(Doda.objects.get(id_doda=12001).baj_doda, 'DODA-ORIGINAL-9')

    def test_doda_de_reemplazo_no_entra_en_creadas_y_queda_notificado(self):
        stats, error_msgs = self._stats()
        creadas = _upsert_dodas([{
            'id_doda':  12002,
            'patente':  '1656',
            'cve_caat': '3B74',
            'baj_doda': 'DODA-ORIGINAL-9',
            'referencias': [],
        }], stats, error_msgs)
        self.assertEqual(creadas, [])
        doda = Doda.objects.get(id_doda=12002)
        self.assertIsNotNone(doda.notificado_en)
        self.assertIsNotNone(doda.modulacion_enviada_en)

    def test_doda_de_baja_no_entra_en_creadas_y_queda_notificado(self):
        stats, error_msgs = self._stats()
        creadas = _upsert_dodas([{
            'id_doda':    12003,
            'patente':    '1656',
            'cve_caat':   '3B74',
            'fecha_baja': '2026-08-20T10:00:00',
            'referencias': [],
        }], stats, error_msgs)
        self.assertEqual(creadas, [])
        doda = Doda.objects.get(id_doda=12003)
        self.assertIsNotNone(doda.fecha_baja)
        self.assertIsNotNone(doda.notificado_en)

    def test_doda_normal_sin_baja_ni_reemplazo_si_entra_en_creadas(self):
        stats, error_msgs = self._stats()
        creadas = _upsert_dodas([{
            'id_doda':  12004,
            'patente':  '1656',
            'cve_caat': '3B74',
            'referencias': [],
        }], stats, error_msgs)
        self.assertEqual(len(creadas), 1)
        self.assertEqual(creadas[0].id_doda, 12004)
        self.assertEqual(Doda.objects.get(id_doda=12004).baj_doda, '')
        self.assertIsNone(Doda.objects.get(id_doda=12004).notificado_en)


class SyncEndpointDodasTests(TestCase):
    """Tests de integración: el bloque 'dodas' del payload de /api/sync/."""

    def setUp(self):
        from django.test import override_settings
        self._override = override_settings(SYNC_SECRET_KEY='test-secret')
        self._override.enable()
        self.addCleanup(self._override.disable)

    def _post(self, payload):
        return self.client.post(
            reverse('api_sync'),
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION='Token test-secret',
        )

    def test_sync_endpoint_crea_doda_desde_bloque_dodas(self):
        resp = self._post({
            'patente':  '1656',
            'agent_id': 'test-agent',
            'dodas': [{
                'id_doda':  11001,
                'num_doda': 'DODA-11001',
                'patente':  '1656',
                'cve_caat': '3B74',
                'referencias': [{'num_refe': 'LCRR0001/26', 'cons_id': 1}],
            }],
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Doda.objects.filter(id_doda=11001).exists())

    def test_sync_endpoint_sin_bloque_dodas_no_rompe(self):
        """Compatibilidad con agentes viejos que aún no mandan 'dodas'."""
        resp = self._post({
            'patente':  '1656',
            'agent_id': 'test-agent',
        })
        self.assertEqual(resp.status_code, 200)


class UpsertReferenciasPesoBrutoTests(TestCase):
    """Tests para referencias.sync_views._upsert_referencias — campo peso_bruto."""

    def _stats(self):
        return {'creadas': 0, 'actualizadas': 0, 'errores': 0}, []

    def test_crea_referencia_con_peso_bruto(self):
        stats, error_msgs = self._stats()
        _upsert_referencias('1656', [{
            'num_refe':   'LCRR0001/26',
            'peso_bruto': '12345.678',
        }], stats, error_msgs)

        self.assertEqual(stats['errores'], 0)
        ref = Referencia.objects.get(num_refe='LCRR0001/26')
        self.assertEqual(ref.peso_bruto, Decimal('12345.678'))

    def test_actualiza_peso_bruto_de_referencia_existente(self):
        Referencia.objects.create(
            num_refe='LCRR0002/26', patente='1656', prefijo='LCRR',
            peso_bruto=Decimal('100.000'),
        )
        stats, error_msgs = self._stats()

        _upsert_referencias('1656', [{
            'num_refe':   'LCRR0002/26',
            'peso_bruto': '250.500',
        }], stats, error_msgs)

        ref = Referencia.objects.get(num_refe='LCRR0002/26')
        self.assertEqual(ref.peso_bruto, Decimal('250.500'))

    def test_peso_bruto_ausente_queda_null(self):
        stats, error_msgs = self._stats()
        _upsert_referencias('1656', [{'num_refe': 'LCRR0003/26'}], stats, error_msgs)

        ref = Referencia.objects.get(num_refe='LCRR0003/26')
        self.assertIsNone(ref.peso_bruto)


class UpsertContenedoresTipoActualizaTests(TestCase):
    """Bug real de producción: SAAIO_CONTEN.CVE_CONT es VARCHAR(2), pero
    CVE_CONT_TIPO tenía claves int, así que el mapeo nunca matcheaba y el
    100% de los contenedores quedaban con tipo=''. _upsert_contenedores()
    usaba get_or_create(defaults=...), que sólo aplica 'defaults' al CREAR —
    una vez arreglado el mapeo en el agente, los contenedores ya existentes
    (con tipo='' guardado de antes) nunca se corregían en syncs posteriores.
    Estos tests fijan el contrato correcto: crear con tipo, y actualizar el
    tipo de un contenedor ya existente cuando el sync trae un valor resuelto
    distinto, sin pisar un tipo bueno con uno vacío."""

    def _stats(self):
        return {'creadas': 0, 'actualizadas': 0, 'errores': 0}, []

    def setUp(self):
        self.ref = Referencia.objects.create(
            num_refe='LCRR0100/26', patente='1656', prefijo='LCRR',
        )

    def test_crea_contenedor_con_tipo(self):
        stats, error_msgs = self._stats()
        _upsert_contenedores([{
            'num_refe': 'LCRR0100/26', 'num_cont': 'GAOU7393934', 'tipo': '40HC',
        }], stats, error_msgs)

        cont = Contenedor.objects.get(referencia=self.ref, num_cont='GAOU7393934')
        self.assertEqual(cont.tipo, '40HC')

    def test_actualiza_tipo_vacio_de_contenedor_existente(self):
        Contenedor.objects.create(referencia=self.ref, num_cont='GAOU7393934', tipo='')
        stats, error_msgs = self._stats()

        _upsert_contenedores([{
            'num_refe': 'LCRR0100/26', 'num_cont': 'GAOU7393934', 'tipo': '40HC',
        }], stats, error_msgs)

        cont = Contenedor.objects.get(referencia=self.ref, num_cont='GAOU7393934')
        self.assertEqual(cont.tipo, '40HC')
        self.assertEqual(stats['errores'], 0)

    def test_no_pisa_tipo_bueno_con_vacio(self):
        """Si el sync trae tipo='' (CVE_CONT todavía sin mapeo) no debe borrar
        un tipo ya resuelto correctamente en una corrida anterior."""
        Contenedor.objects.create(referencia=self.ref, num_cont='GAOU7393934', tipo='40HC')
        stats, error_msgs = self._stats()

        _upsert_contenedores([{
            'num_refe': 'LCRR0100/26', 'num_cont': 'GAOU7393934', 'tipo': '',
        }], stats, error_msgs)

        cont = Contenedor.objects.get(referencia=self.ref, num_cont='GAOU7393934')
        self.assertEqual(cont.tipo, '40HC')

    def test_no_reescribe_si_tipo_no_cambio(self):
        cont_original = Contenedor.objects.create(
            referencia=self.ref, num_cont='GAOU7393934', tipo='40HC',
        )
        stats, error_msgs = self._stats()

        _upsert_contenedores([{
            'num_refe': 'LCRR0100/26', 'num_cont': 'GAOU7393934', 'tipo': '40HC',
        }], stats, error_msgs)

        cont_original.refresh_from_db()
        self.assertEqual(cont_original.tipo, '40HC')
