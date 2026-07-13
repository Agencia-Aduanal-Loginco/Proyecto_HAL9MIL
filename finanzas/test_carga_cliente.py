import tempfile
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from clientes.models import Cliente
from referencias.models import Referencia

from .cfdi_de_prueba import cfdi_cliente
from .models import XMLProveedor

MEDIA_TMP = tempfile.mkdtemp()


def _login_finanzas(test, username='carga_cliente_user'):
    grupo, _ = Group.objects.get_or_create(name='Finanzas')
    test.user = User.objects.create_user(username, password='x')
    test.user.groups.add(grupo)
    test.client.login(username=username, password='x')


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class CargaClienteViewTests(TestCase):
    def setUp(self):
        _login_finanzas(self)

    def test_get_muestra_formulario(self):
        resp = self.client.get(reverse('finanzas:carga_xml_cliente'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Cargar facturas de cliente')

    def test_usuario_sin_grupo_finanzas_puede_acceder(self):
        User.objects.create_user('sin_grupo', password='x')
        self.client.login(username='sin_grupo', password='x')
        resp = self.client.get(reverse('finanzas:carga_xml_cliente'))
        self.assertEqual(resp.status_code, 200)

    def test_usuario_anonimo_es_redirigido_a_login(self):
        self.client.logout()
        resp = self.client.get(reverse('finanzas:carga_xml_cliente'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp.url)

    def test_post_sin_archivos_redirige_con_error(self):
        resp = self.client.post(reverse('finanzas:carga_xml_cliente'), {})
        self.assertRedirects(resp, reverse('finanzas:carga_xml_cliente'))

    def test_post_xml_cliente_queda_pendiente_con_rfc_y_pdf(self):
        xml = SimpleUploadedFile('F10234.xml', cfdi_cliente(rfc_receptor='CIN220216BS2'))
        pdf = SimpleUploadedFile('F10234.pdf', b'%PDF-1.4 prueba')
        resp = self.client.post(
            reverse('finanzas:carga_xml_cliente'), {'archivos': [xml, pdf]}
        )
        self.assertEqual(resp.status_code, 200)
        obj = XMLProveedor.objects.get(
            uuid_fiscal='33333333-3333-3333-3333-333333333333'
        )
        self.assertEqual(obj.estado_asignacion, 'PENDIENTE')
        self.assertEqual(obj.rfc_receptor, 'CIN220216BS2')
        self.assertTrue(obj.pdf_file)

    def test_post_duplicado_no_crea_segundo_registro(self):
        for _ in range(2):
            xml = SimpleUploadedFile('F10234.xml', cfdi_cliente())
            self.client.post(reverse('finanzas:carga_xml_cliente'), {'archivos': [xml]})
        self.assertEqual(
            XMLProveedor.objects.filter(
                uuid_fiscal='33333333-3333-3333-3333-333333333333'
            ).count(),
            1,
        )

    def test_usuario_sin_finanzas_no_ve_enlace_a_pendientes(self):
        User.objects.create_user('sin_grupo2', password='x')
        self.client.login(username='sin_grupo2', password='x')
        resp = self.client.get(reverse('finanzas:carga_xml_cliente'))
        self.assertNotContains(resp, reverse('finanzas:xml_pendientes'))

    def test_usuario_finanzas_si_ve_enlace_a_pendientes(self):
        resp = self.client.get(reverse('finanzas:carga_xml_cliente'))
        self.assertContains(resp, reverse('finanzas:xml_pendientes'))

    def test_resultado_oculta_asignar_pendientes_a_usuario_sin_finanzas(self):
        User.objects.create_user('sin_grupo3', password='x')
        self.client.login(username='sin_grupo3', password='x')
        xml = SimpleUploadedFile('F99999.xml', cfdi_cliente(
            uuid='55555555-5555-5555-5555-555555555555',
        ))
        resp = self.client.post(reverse('finanzas:carga_xml_cliente'), {'archivos': [xml]})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Asignar los pendientes')

    def test_resultado_muestra_asignar_pendientes_a_finanzas(self):
        xml = SimpleUploadedFile('F88888.xml', cfdi_cliente(
            uuid='66666666-6666-6666-6666-666666666666',
        ))
        resp = self.client.post(reverse('finanzas:carga_xml_cliente'), {'archivos': [xml]})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Asignar los pendientes')


def _crear_pendiente(rfc_receptor, uuid='44444444-4444-4444-4444-444444444444'):
    obj = XMLProveedor(
        uuid_fiscal=uuid,
        fecha_emision=datetime(2026, 7, 10, 12, 0, 0),
        rfc_emisor='FPA010101AA1',
        nombre_emisor='FLETES DEL PACIFICO',
        rfc_receptor=rfc_receptor,
        subtotal=Decimal('5000.00'),
        iva=Decimal('800.00'),
        total=Decimal('5800.00'),
        tipo_comprobante='I',
        estado_asignacion='PENDIENTE',
        motivo_pendiente='Proveedor no soportado',
    )
    obj.xml_file.save('cliente.xml', ContentFile(b'<x/>'), save=False)
    obj.save()
    return obj


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class XmlPendientesClienteTests(TestCase):
    def setUp(self):
        _login_finanzas(self, username='pendientes_user')
        self.cliente = Cliente.objects.create(
            nombre_cliente='CACIPA INTERNACIONAL',
            cve_cliente='CACIPA',
            rfc='CIN220216BS2',
        )
        self.ref_cliente = Referencia.objects.create(
            num_refe='LCRR0001/26', patente='1656', prefijo='LCRR',
            cve_cliente='CACIPA', fecha_pago=date(2026, 7, 1),
        )
        self.ref_ajena = Referencia.objects.create(
            num_refe='LCRR0002/26', patente='1656', prefijo='LCRR',
            cve_cliente='OTRO', fecha_pago=date(2026, 7, 2),
        )

    def test_muestra_rfc_receptor_y_cliente_detectado(self):
        _crear_pendiente('CIN220216BS2')
        resp = self.client.get(reverse('finanzas:xml_pendientes'))
        self.assertContains(resp, 'CIN220216BS2')
        self.assertContains(resp, 'CACIPA INTERNACIONAL')

    def test_sugiere_solo_referencias_del_cliente(self):
        _crear_pendiente('CIN220216BS2')
        resp = self.client.get(reverse('finanzas:xml_pendientes'))
        self.assertContains(resp, 'LCRR0001/26')
        self.assertNotContains(resp, 'LCRR0002/26')

    def test_rfc_sin_cliente_no_muestra_sugerencias(self):
        _crear_pendiente('ZZZ990101ZZ9')
        resp = self.client.get(reverse('finanzas:xml_pendientes'))
        self.assertContains(resp, 'ZZZ990101ZZ9')
        self.assertNotContains(resp, 'Sugerencias')
        self.assertNotContains(resp, 'CACIPA INTERNACIONAL')
