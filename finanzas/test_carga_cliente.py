import tempfile

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

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

    def test_usuario_sin_grupo_es_redirigido(self):
        User.objects.create_user('sin_grupo', password='x')
        self.client.login(username='sin_grupo', password='x')
        resp = self.client.get(reverse('finanzas:carga_xml_cliente'))
        self.assertEqual(resp.status_code, 302)

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
