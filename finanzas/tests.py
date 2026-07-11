import uuid
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from finanzas.models import (
    ConfiguracionFiscal, DoctoRelacionado, Factura, GastoReferencia,
    Pago, PolizaContable, RecordatorioCobranza, XMLProveedor,
)
from finanzas.pipeline import (
    calcular_embudo_ap, calcular_embudo_ar, calcular_tendencia_semanal,
)
from referencias.models import Referencia


class AccesoFinanzasTests(TestCase):
    def setUp(self):
        self.grupo_finanzas, _ = Group.objects.get_or_create(name='Finanzas')
        self.usuario_con_grupo = User.objects.create_user('fin_con_grupo', password='x')
        self.usuario_con_grupo.groups.add(self.grupo_finanzas)
        self.usuario_sin_grupo = User.objects.create_user('fin_sin_grupo', password='x')
        self.superusuario = User.objects.create_superuser(
            'fin_admin', email='fin_admin@example.com', password='x'
        )

    def test_usuario_sin_grupo_no_accede_al_dashboard_de_finanzas(self):
        self.client.force_login(self.usuario_sin_grupo)
        response = self.client.get(reverse('finanzas:dashboard'))
        self.assertRedirects(response, reverse('dashboard'))

    def test_usuario_con_grupo_accede_al_dashboard_de_finanzas(self):
        self.client.force_login(self.usuario_con_grupo)
        response = self.client.get(reverse('finanzas:dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_superusuario_accede_sin_estar_en_el_grupo(self):
        self.client.force_login(self.superusuario)
        response = self.client.get(reverse('finanzas:dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_usuario_anonimo_es_redirigido_a_login(self):
        response = self.client.get(reverse('finanzas:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)


class EmbudoARTest(TestCase):
    def setUp(self):
        self.config = ConfiguracionFiscal.objects.create(
            patente='3772', rfc='REI123456789', razon_social='Reiki',
            regimen_fiscal='601', codigo_postal='06600',
            cert_path='', key_path='',
        )

    def _crear_factura(self, folio, estado, fecha_emision, total=Decimal('1000.00')):
        return Factura.objects.create(
            folio=folio,
            rfc_receptor='CLI123456789',
            nombre_receptor='Cliente Test',
            domicilio_fiscal_receptor='06600',
            regimen_fiscal_receptor='601',
            subtotal=total,
            iva=Decimal('0.00'),
            total=total,
            estado=estado,
            fecha_emision=fecha_emision,
            configuracion_fiscal=self.config,
        )

    def test_borrador_no_cuenta_como_timbrada(self):
        self._crear_factura(100, 'BORRADOR', timezone.make_aware(datetime(2026, 7, 1)))
        resultado = calcular_embudo_ar(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['emitidas'], 1)
        self.assertEqual(resultado['timbradas'], 0)
        self.assertEqual(resultado['cobradas'], 0)

    def test_timbrada_pagada_cuenta_como_cobrada(self):
        factura = self._crear_factura(101, 'TIMBRADA', timezone.make_aware(datetime(2026, 7, 1)))
        pago = Pago.objects.create(
            fecha_pago='2026-07-02', monto=factura.total, moneda='MXN', forma_pago='03',
        )
        DoctoRelacionado.objects.create(
            pago=pago, factura=factura, num_parcialidad=1,
            imp_saldo_anterior=factura.total, imp_pagado=factura.total,
            imp_saldo_insoluto=Decimal('0'),
        )
        resultado = calcular_embudo_ar(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['timbradas'], 1)
        self.assertEqual(resultado['cobradas'], 1)

    def test_factura_fuera_de_ventana_no_se_cuenta(self):
        self._crear_factura(102, 'TIMBRADA', timezone.make_aware(datetime(2026, 3, 1)))
        resultado = calcular_embudo_ar(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['emitidas'], 0)

    def test_factura_cancelada_no_se_cuenta(self):
        self._crear_factura(103, 'CANCELADA', timezone.make_aware(datetime(2026, 7, 1)))
        resultado = calcular_embudo_ar(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['emitidas'], 0)


class EmbudoAPTest(TestCase):
    def setUp(self):
        self.referencia = Referencia.objects.create(
            num_refe='RE51-9001-26', patente='3772', prefijo='RE51',
            cve_cliente='CLI01', nombre_cliente='Cliente AP Test',
        )

    def _crear_xml(self, fecha_emision, procesado=False):
        return XMLProveedor.objects.create(
            referencia=self.referencia,
            uuid_fiscal=uuid.uuid4(),
            fecha_emision=fecha_emision,
            rfc_emisor='PRO123456789',
            nombre_emisor='Proveedor Test',
            rfc_receptor='REI123456789',
            subtotal=Decimal('500.00'),
            iva=Decimal('80.00'),
            total=Decimal('580.00'),
            tipo_comprobante='I',
            xml_file=SimpleUploadedFile('test.xml', b'<xml></xml>'),
            procesado=procesado,
        )

    def test_xml_no_procesado_no_cuenta_en_procesados(self):
        self._crear_xml(timezone.make_aware(datetime(2026, 7, 1)), procesado=False)
        resultado = calcular_embudo_ap(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['recibidos'], 1)
        self.assertEqual(resultado['procesados'], 0)

    def test_gasto_sin_poliza_no_cuenta_en_con_poliza(self):
        xml = self._crear_xml(timezone.make_aware(datetime(2026, 7, 1)), procesado=True)
        GastoReferencia.objects.create(
            referencia=self.referencia, tipo='OTROS', concepto='Gasto test',
            fecha=date(2026, 7, 1), monto=Decimal('580.00'),
            xml_proveedor=xml,
        )
        resultado = calcular_embudo_ap(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['procesados'], 1)
        self.assertEqual(resultado['con_poliza'], 0)

    def test_gasto_con_poliza_cuenta(self):
        xml = self._crear_xml(timezone.make_aware(datetime(2026, 7, 1)), procesado=True)
        poliza = PolizaContable.objects.create(
            numero='E-1', tipo='E', fecha=date(2026, 7, 1), mes=7, anio=2026,
            concepto='Póliza test',
        )
        GastoReferencia.objects.create(
            referencia=self.referencia, tipo='OTROS', concepto='Gasto test',
            fecha=date(2026, 7, 1), monto=Decimal('580.00'),
            xml_proveedor=xml, poliza=poliza,
        )
        resultado = calcular_embudo_ap(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['con_poliza'], 1)

    def test_xml_fuera_de_ventana_no_se_cuenta(self):
        self._crear_xml(timezone.make_aware(datetime(2026, 3, 1)), procesado=True)
        resultado = calcular_embudo_ap(hoy=date(2026, 7, 15))
        self.assertEqual(resultado['recibidos'], 0)


class TendenciaSemanalTest(TestCase):
    def setUp(self):
        self.config = ConfiguracionFiscal.objects.create(
            patente='3772', rfc='REI123456789', razon_social='Reiki',
            regimen_fiscal='601', codigo_postal='06600',
            cert_path='', key_path='',
        )

    def test_agrupa_facturas_timbradas_por_semana(self):
        Factura.objects.create(
            folio=200, rfc_receptor='CLI123456789', nombre_receptor='Cliente Test',
            domicilio_fiscal_receptor='06600', regimen_fiscal_receptor='601',
            subtotal=Decimal('100'), iva=Decimal('0'), total=Decimal('100'),
            estado='TIMBRADA', fecha_emision=timezone.make_aware(datetime(2026, 6, 15)),
            configuracion_fiscal=self.config,
        )
        Factura.objects.create(
            folio=201, rfc_receptor='CLI123456789', nombre_receptor='Cliente Test',
            domicilio_fiscal_receptor='06600', regimen_fiscal_receptor='601',
            subtotal=Decimal('100'), iva=Decimal('0'), total=Decimal('100'),
            estado='TIMBRADA', fecha_emision=timezone.make_aware(datetime(2026, 6, 22)),
            configuracion_fiscal=self.config,
        )
        resultado = calcular_tendencia_semanal(semanas=4, hoy=date(2026, 7, 1))
        self.assertEqual(len(resultado['labels']), 4)
        self.assertEqual(sum(resultado['facturas_timbradas']), 2)

    def test_agrupa_polizas_por_semana(self):
        PolizaContable.objects.create(
            numero='D-1', tipo='D', fecha=date(2026, 6, 24), mes=6, anio=2026,
            concepto='Test',
        )
        resultado = calcular_tendencia_semanal(semanas=4, hoy=date(2026, 7, 1))
        self.assertEqual(sum(resultado['polizas_generadas']), 1)

    def test_factura_borrador_no_cuenta_en_tendencia(self):
        Factura.objects.create(
            folio=202, rfc_receptor='CLI123456789', nombre_receptor='Cliente Test',
            domicilio_fiscal_receptor='06600', regimen_fiscal_receptor='601',
            subtotal=Decimal('100'), iva=Decimal('0'), total=Decimal('100'),
            estado='BORRADOR', fecha_emision=timezone.make_aware(datetime(2026, 6, 22)),
            configuracion_fiscal=self.config,
        )
        resultado = calcular_tendencia_semanal(semanas=4, hoy=date(2026, 7, 1))
        self.assertEqual(sum(resultado['facturas_timbradas']), 0)


class DashboardPipelineViewTest(TestCase):
    def setUp(self):
        grupo_finanzas, _ = Group.objects.get_or_create(name='Finanzas')
        self.user = User.objects.create_user('dashuser', password='x')
        self.user.groups.add(grupo_finanzas)
        self.client.login(username='dashuser', password='x')

    def test_dashboard_incluye_datos_de_pipeline(self):
        response = self.client.get(reverse('finanzas:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('embudo_ar', response.context)
        self.assertIn('embudo_ap', response.context)
        self.assertEqual(
            set(response.context['embudo_ar'].keys()),
            {'emitidas', 'timbradas', 'cobradas'},
        )
        self.assertEqual(
            set(response.context['embudo_ap'].keys()),
            {'recibidos', 'procesados', 'con_poliza'},
        )
        self.assertIn('tendencia_labels_json', response.context)

    def test_dashboard_muestra_secciones_de_pipeline(self):
        response = self.client.get(reverse('finanzas:dashboard'))
        content = response.content.decode()
        self.assertIn('Cuentas por Cobrar', content)
        self.assertIn('Cuentas por Pagar', content)
        self.assertIn('id="chartTendencia"', content)


class RecordatorioCobranzaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('testuser', password='x')
        config = ConfiguracionFiscal.objects.create(
            patente='3772', rfc='REI123456789', razon_social='Reiki',
            regimen_fiscal='601', codigo_postal='06600',
            cert_path='', key_path='',
        )
        self.factura = Factura.objects.create(
            folio=1,
            rfc_receptor='CLI123456789',
            nombre_receptor='Cliente Test',
            domicilio_fiscal_receptor='06600',
            regimen_fiscal_receptor='601',
            subtotal=Decimal('1000.00'),
            iva=Decimal('160.00'),
            total=Decimal('1160.00'),
            estado='TIMBRADA',
            configuracion_fiscal=config,
        )

    def test_crear_recordatorio(self):
        r = RecordatorioCobranza.objects.create(
            factura=self.factura,
            tipo='15d',
            enviado_por=self.user,
            exitoso=True,
        )
        self.assertEqual(r.tipo, '15d')
        self.assertTrue(r.exitoso)
        self.assertEqual(r.error_msg, '')

    def test_multiples_tipos_permitidos(self):
        RecordatorioCobranza.objects.create(factura=self.factura, tipo='15d')
        RecordatorioCobranza.objects.create(factura=self.factura, tipo='30d')
        RecordatorioCobranza.objects.create(factura=self.factura, tipo='manual')
        self.assertEqual(RecordatorioCobranza.objects.filter(factura=self.factura).count(), 3)
