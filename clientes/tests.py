from django.test import TestCase
from .models import Cliente


class ClienteEmailTest(TestCase):
    def test_email_cobranza_blank_by_default(self):
        c = Cliente.objects.create(nombre_cliente='Test S.A.', cve_cliente='T001')
        self.assertEqual(c.email_cobranza, '')
        self.assertEqual(c.email_cobranza_cc, '')

    def test_email_cobranza_guardado(self):
        c = Cliente.objects.create(
            nombre_cliente='ACME S.A.',
            cve_cliente='ACME01',
            email_cobranza='cuentas@acme.com',
            email_cobranza_cc='contador@acme.com',
        )
        c.refresh_from_db()
        self.assertEqual(c.email_cobranza, 'cuentas@acme.com')
        self.assertEqual(c.email_cobranza_cc, 'contador@acme.com')


class ClienteEmailCuentaGastosTests(TestCase):
    def test_campos_nuevos_aceptan_blank(self):
        cliente = Cliente.objects.create(nombre_cliente='ACME SA')
        self.assertEqual(cliente.email_cuenta_gastos, '')
        self.assertEqual(cliente.email_cuenta_gastos_cc, '')

    def test_campos_nuevos_guardan_valor(self):
        cliente = Cliente.objects.create(
            nombre_cliente='CACIPA',
            email_cuenta_gastos='cg@cacipa.com',
            email_cuenta_gastos_cc='cc@cacipa.com',
        )
        cliente.refresh_from_db()
        self.assertEqual(cliente.email_cuenta_gastos, 'cg@cacipa.com')
        self.assertEqual(cliente.email_cuenta_gastos_cc, 'cc@cacipa.com')
