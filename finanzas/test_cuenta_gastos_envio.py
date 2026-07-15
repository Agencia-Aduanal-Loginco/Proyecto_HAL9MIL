import io
import tempfile
import zipfile
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from clientes.models import Cliente
from referencias.models import Referencia

MEDIA_TMP = tempfile.mkdtemp()


def _pdf_valido(texto='Factura de proveedor de prueba'):
    """PDF real de una página, para pruebas que necesitan un PDF parseable."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(300, 200))
    c.drawString(20, 100, texto)
    c.save()
    return buffer.getvalue()


def _referencia(num='LCRR0100/26'):
    return Referencia.objects.create(num_refe=num, patente='1656', prefijo='LCRR')


def _xml_proveedor(referencia, uuid='11111111-1111-1111-1111-111111111111',
                   con_pdf=True):
    from finanzas.models import XMLProveedor
    return XMLProveedor.objects.create(
        referencia=referencia, uuid_fiscal=uuid,
        fecha_emision=timezone.now(), rfc_emisor='AAA010101AAA',
        nombre_emisor='PROVEEDOR SA', rfc_receptor='BBB010101BBB',
        subtotal=Decimal('100'), iva=Decimal('16'), total=Decimal('116'),
        tipo_comprobante='I',
        xml_file=SimpleUploadedFile(f'{uuid}.xml', b'<cfdi/>'),
        pdf_file=SimpleUploadedFile(f'{uuid}.pdf', _pdf_valido()) if con_pdf else None,
    )


class DestinatariosClienteTests(TestCase):
    def test_usa_email_cuenta_gastos_si_existe(self):
        from finanzas.cuenta_gastos_envio import destinatarios_cliente
        cliente = Cliente.objects.create(
            nombre_cliente='A', email_cuenta_gastos='cg@a.com',
            email_cuenta_gastos_cc='cgcc@a.com',
            email_cobranza='cob@a.com', email_cobranza_cc='cobcc@a.com',
        )
        self.assertEqual(destinatarios_cliente(cliente), ('cg@a.com', 'cgcc@a.com'))

    def test_fallback_a_cobranza(self):
        from finanzas.cuenta_gastos_envio import destinatarios_cliente
        cliente = Cliente.objects.create(
            nombre_cliente='B', email_cobranza='cob@b.com',
            email_cobranza_cc='cobcc@b.com',
        )
        self.assertEqual(destinatarios_cliente(cliente), ('cob@b.com', 'cobcc@b.com'))

    def test_cliente_none_devuelve_vacios(self):
        from finanzas.cuenta_gastos_envio import destinatarios_cliente
        self.assertEqual(destinatarios_cliente(None), ('', ''))


@override_settings(MEDIA_ROOT=MEDIA_TMP, NOMBRE_AGENCIA='Loginco Corporativo')
class ConstruirZipTests(TestCase):
    def setUp(self):
        self.referencia = _referencia()

    def test_zip_contiene_xml_y_pdf(self):
        from finanzas.cuenta_gastos_envio import construir_zip_cuenta_gastos
        _xml_proveedor(self.referencia)
        _xml_proveedor(self.referencia,
                       uuid='22222222-2222-2222-2222-222222222222', con_pdf=False)
        nombre, data = construir_zip_cuenta_gastos(self.referencia)
        self.assertTrue(nombre.startswith('CG_LCRR0100-26_'))
        self.assertTrue(nombre.endswith('.zip'))
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            nombres = sorted(zf.namelist())
        self.assertEqual(nombres, [
            'CFDI_11111111-1111-1111-1111-111111111111.pdf',
            'CFDI_11111111-1111-1111-1111-111111111111.xml',
            'CFDI_22222222-2222-2222-2222-222222222222.xml',
        ])

    def test_pdf_en_zip_queda_sellado_con_nombre_de_agencia(self):
        from finanzas.cuenta_gastos_envio import construir_zip_cuenta_gastos
        _xml_proveedor(self.referencia)
        _, data = construir_zip_cuenta_gastos(self.referencia)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            pdf_bytes = zf.read(
                'CFDI_11111111-1111-1111-1111-111111111111.pdf'
            )
        texto = PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text()
        self.assertIn('Loginco Corporativo', texto)

    def test_sin_cfdis_lanza_error(self):
        from finanzas.cuenta_gastos_envio import construir_zip_cuenta_gastos
        with self.assertRaises(ValueError):
            construir_zip_cuenta_gastos(self.referencia)

    def test_zip_excede_limite_lanza_error(self):
        from finanzas import cuenta_gastos_envio
        _xml_proveedor(self.referencia)
        with patch.object(cuenta_gastos_envio, 'LIMITE_ZIP_BYTES', 10):
            with self.assertRaises(ValueError):
                cuenta_gastos_envio.construir_zip_cuenta_gastos(self.referencia)


@override_settings(NOMBRE_AGENCIA='Loginco Corporativo')
class SellarPdfProveedorTests(TestCase):
    def test_agrega_nombre_de_agencia_al_pdf(self):
        from finanzas.cuenta_gastos_envio import sellar_pdf_proveedor
        sellado = sellar_pdf_proveedor(_pdf_valido())
        texto = PdfReader(io.BytesIO(sellado)).pages[0].extract_text()
        self.assertIn('Loginco Corporativo', texto)

    def test_conserva_contenido_original(self):
        from finanzas.cuenta_gastos_envio import sellar_pdf_proveedor
        sellado = sellar_pdf_proveedor(_pdf_valido('Factura de proveedor de prueba'))
        texto = PdfReader(io.BytesIO(sellado)).pages[0].extract_text()
        self.assertIn('Factura de proveedor de prueba', texto)

    def test_pdf_invalido_se_retorna_sin_cambios(self):
        from finanzas.cuenta_gastos_envio import sellar_pdf_proveedor
        data = b'esto no es un pdf valido'
        self.assertEqual(sellar_pdf_proveedor(data), data)


class EmailBalanzaTemplateTests(TestCase):
    def test_render_contiene_balanza(self):
        from django.template.loader import render_to_string
        from finanzas.cuenta_gastos_envio import contexto_balanza
        from finanzas.models import Anticipo, GastoReferencia
        referencia = _referencia('LCRR0200/26')
        Anticipo.objects.create(
            referencia=referencia, fecha=timezone.now().date(),
            monto=Decimal('5000'), forma_pago='03',
        )
        GastoReferencia.objects.create(
            referencia=referencia, tipo='MANIOBRAS', concepto='MUELLAJE',
            fecha=timezone.now().date(), monto=Decimal('11094'),
        )
        html = render_to_string(
            'finanzas/email_cuenta_gastos.html', contexto_balanza(referencia)
        )
        self.assertIn('LCRR0200/26', html)
        self.assertIn('Anticipos del cliente', html)
        self.assertIn('MUELLAJE', html)
        self.assertIn('5000', html.replace(',', ''))
        self.assertIn('Saldo', html)


def _resp_sendgrid(status=202, message_id='sg-msg-001'):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {'X-Message-Id': message_id}
    return resp


@override_settings(MEDIA_ROOT=MEDIA_TMP, SENDGRID_API_KEY='SG.test')
class EnviarCuentaGastosTests(TestCase):
    def setUp(self):
        self.referencia = _referencia('LCRR0300/26')
        _xml_proveedor(self.referencia)
        self.user = User.objects.create_user('emisor', password='x')

    def test_envio_exitoso_guarda_notificacion(self):
        from finanzas.cuenta_gastos_envio import enviar_cuenta_gastos
        with patch('finanzas.cuenta_gastos_envio.SendGridAPIClient') as cliente_cls:
            cliente_cls.return_value.send.return_value = _resp_sendgrid()
            notif = enviar_cuenta_gastos(
                self.referencia, 'cliente@example.com', 'cc@example.com', self.user
            )
        self.assertEqual(notif.estado, 'ENVIADO')
        self.assertEqual(notif.sg_message_id, 'sg-msg-001')
        self.assertEqual(notif.destinatario, 'cliente@example.com')
        self.assertTrue(notif.zip_file.name)
        # custom_arg de correlación presente en el Mail enviado
        mail_enviado = cliente_cls.return_value.send.call_args[0][0]
        cuerpo = mail_enviado.get()
        self.assertEqual(
            cuerpo['custom_args']['notificacion_cg_id'], str(notif.pk)
        )
        self.assertEqual(len(cuerpo['attachments']), 1)

    def test_error_de_api_deja_estado_error(self):
        from finanzas.cuenta_gastos_envio import enviar_cuenta_gastos
        with patch('finanzas.cuenta_gastos_envio.SendGridAPIClient') as cliente_cls:
            cliente_cls.return_value.send.side_effect = Exception('boom sendgrid')
            notif = enviar_cuenta_gastos(self.referencia, 'x@example.com')
        self.assertEqual(notif.estado, 'ERROR')
        self.assertIn('boom sendgrid', notif.error_msg)

    def test_status_400_deja_estado_error(self):
        from finanzas.cuenta_gastos_envio import enviar_cuenta_gastos
        with patch('finanzas.cuenta_gastos_envio.SendGridAPIClient') as cliente_cls:
            cliente_cls.return_value.send.return_value = _resp_sendgrid(status=401)
            notif = enviar_cuenta_gastos(self.referencia, 'x@example.com')
        self.assertEqual(notif.estado, 'ERROR')

    def test_reenvio_reutiliza_zip(self):
        """Reenvío normal (sin reapertura de por medio): se reutiliza el ZIP.

        Es el caso común: la mayoría de los reenvíos solo corrigen un correo
        mal capturado, no reflejan cambios en los CFDIs.
        """
        from finanzas.cuenta_gastos_envio import enviar_cuenta_gastos
        with patch('finanzas.cuenta_gastos_envio.SendGridAPIClient') as cliente_cls:
            cliente_cls.return_value.send.return_value = _resp_sendgrid()
            primera = enviar_cuenta_gastos(self.referencia, 'a@example.com')
            with patch(
                'finanzas.cuenta_gastos_envio.construir_zip_cuenta_gastos'
            ) as builder:
                segunda = enviar_cuenta_gastos(
                    self.referencia, 'otro@example.com', es_reenvio=True
                )
        builder.assert_not_called()
        self.assertTrue(segunda.es_reenvio)
        self.assertEqual(segunda.zip_file.name, primera.zip_file.name)

    def test_reenvio_tras_reapertura_reconstruye_zip(self):
        """close -> send -> reabrir -> (cambian CFDIs) -> re-cerrar -> send.

        El segundo envío es un reenvío (ya existe una notificación previa),
        pero la cuenta se reabrió y se volvió a cerrar desde entonces, así
        que el conjunto de CFDIs pudo cambiar. El ZIP de la notificación
        previa ya no es confiable y debe reconstruirse en vez de
        reutilizarse, para que el ZIP adjunto coincida con la balanza que
        se renderiza en el correo (siempre calculada con el estado actual).
        """
        from finanzas import cuenta_gastos_envio
        from finanzas.cuenta_gastos_envio import enviar_cuenta_gastos
        from finanzas.models import CierreCuentaGastos

        cierre = CierreCuentaGastos.objects.create(
            referencia=self.referencia, cerrada_por=self.user,
        )

        with patch('finanzas.cuenta_gastos_envio.SendGridAPIClient') as cliente_cls:
            cliente_cls.return_value.send.return_value = _resp_sendgrid()
            primera = enviar_cuenta_gastos(self.referencia, 'a@example.com')

            # Superusuario reabre la cuenta de gastos...
            cierre.reabierta_por = self.user
            cierre.reabierta_en = timezone.now()
            cierre.save()

            # ...se agregan/quitan CFDIs mientras está reabierta...
            _xml_proveedor(
                self.referencia,
                uuid='33333333-3333-3333-3333-333333333333',
            )

            # ...y se vuelve a cerrar (mismo comportamiento que
            # views_cuenta_gastos.cerrar_cg en su rama de re-cierre: limpia
            # la reapertura y refresca cerrada_en).
            cierre.cerrada_por = self.user
            cierre.cerrada_en = timezone.now()
            cierre.reabierta_por = None
            cierre.reabierta_en = None
            cierre.save()

            with patch(
                'finanzas.cuenta_gastos_envio.construir_zip_cuenta_gastos',
                wraps=cuenta_gastos_envio.construir_zip_cuenta_gastos,
            ) as builder:
                segunda = enviar_cuenta_gastos(
                    self.referencia, 'otro@example.com', es_reenvio=True
                )

        builder.assert_called_once_with(self.referencia)
        self.assertTrue(segunda.es_reenvio)
        self.assertEqual(segunda.estado, 'ENVIADO')
        self.assertNotEqual(segunda.zip_file.name, primera.zip_file.name)


@override_settings(MEDIA_ROOT=MEDIA_TMP, SENDGRID_API_KEY='SG.test')
class EnviarCgViewTests(TestCase):
    def setUp(self):
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.user = User.objects.create_user('envia_cg', password='x')
        self.user.groups.add(grupo)
        self.client.login(username='envia_cg', password='x')
        self.referencia = _referencia('LCRR0400/26')
        _xml_proveedor(self.referencia)
        from finanzas.models import CierreCuentaGastos
        CierreCuentaGastos.objects.create(referencia=self.referencia,
                                          cerrada_por=self.user)
        self.url = reverse('finanzas:enviar_cg',
                           kwargs={'num_refe': self.referencia.num_refe})

    def test_post_envia_y_registra(self):
        from finanzas.models import NotificacionCuentaGastos
        with patch('finanzas.cuenta_gastos_envio.SendGridAPIClient') as cls:
            cls.return_value.send.return_value = _resp_sendgrid()
            resp = self.client.post(self.url, {'destinatario': 'c@x.com'})
        self.assertEqual(resp.status_code, 302)
        notif = NotificacionCuentaGastos.objects.get()
        self.assertEqual(notif.enviado_por, self.user)
        self.assertFalse(notif.es_reenvio)

    def test_segundo_envio_es_reenvio(self):
        from finanzas.models import NotificacionCuentaGastos
        with patch('finanzas.cuenta_gastos_envio.SendGridAPIClient') as cls:
            cls.return_value.send.return_value = _resp_sendgrid()
            self.client.post(self.url, {'destinatario': 'c@x.com'})
            self.client.post(self.url, {'destinatario': 'otro@x.com'})
        segunda = NotificacionCuentaGastos.objects.order_by('pk').last()
        self.assertTrue(segunda.es_reenvio)

    def test_sin_destinatario_no_envia(self):
        from finanzas.models import NotificacionCuentaGastos
        self.client.post(self.url, {'destinatario': ''})
        self.assertEqual(NotificacionCuentaGastos.objects.count(), 0)

    def test_sin_cierre_activo_no_envia(self):
        from finanzas.models import CierreCuentaGastos, NotificacionCuentaGastos
        CierreCuentaGastos.objects.all().delete()
        self.client.post(self.url, {'destinatario': 'c@x.com'})
        self.assertEqual(NotificacionCuentaGastos.objects.count(), 0)


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class NotificacionesListTests(TestCase):
    def setUp(self):
        grupo, _ = Group.objects.get_or_create(name='Finanzas')
        self.user = User.objects.create_user('lista_cg', password='x')
        self.user.groups.add(grupo)
        self.client.login(username='lista_cg', password='x')
        from finanzas.models import NotificacionCuentaGastos
        self.referencia = _referencia('LCRR0600/26')
        self.notif = NotificacionCuentaGastos.objects.create(
            referencia=self.referencia, destinatario='c@x.com',
            enviado_por=self.user, estado='ENTREGADO',
        )
        self.url = reverse('finanzas:notificaciones_cg')

    def test_lista_muestra_notificacion(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'c@x.com')
        self.assertContains(resp, 'LCRR0600/26')
        self.assertContains(resp, 'Entregado')

    def test_filtro_por_estado(self):
        resp = self.client.get(self.url, {'estado': 'LEIDO'})
        self.assertNotContains(resp, 'c@x.com')

    def test_busqueda_por_referencia(self):
        resp = self.client.get(self.url, {'q': 'LCRR0600'})
        self.assertContains(resp, 'c@x.com')
        resp = self.client.get(self.url, {'q': 'NOEXISTE'})
        self.assertNotContains(resp, 'c@x.com')

    def test_descarga_zip(self):
        from django.core.files.base import ContentFile
        self.notif.zip_file.save('CG_test.zip', ContentFile(b'PK\x05\x06zipdata'))
        url = reverse('finanzas:notificacion_cg_zip', kwargs={'pk': self.notif.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/zip')

    def test_zip_inexistente_404(self):
        url = reverse('finanzas:notificacion_cg_zip', kwargs={'pk': self.notif.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_requiere_modulo_finanzas(self):
        User.objects.create_user('sin_modulo', password='x')
        self.client.login(username='sin_modulo', password='x')
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
