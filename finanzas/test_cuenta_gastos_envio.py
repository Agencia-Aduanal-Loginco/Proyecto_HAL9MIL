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

from clientes.models import Cliente
from referencias.models import Referencia

MEDIA_TMP = tempfile.mkdtemp()


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
        pdf_file=SimpleUploadedFile(f'{uuid}.pdf', b'%PDF-1.4') if con_pdf else None,
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


@override_settings(MEDIA_ROOT=MEDIA_TMP)
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
