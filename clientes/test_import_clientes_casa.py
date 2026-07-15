from django.test import TestCase

from .models import Cliente


class MapearClientesTests(TestCase):
    def test_mapea_nombre_cve_y_rfc(self):
        from .management.commands.import_clientes_casa import mapear_clientes
        filas = {'1656': [('ABBA', 'ABBA DESPACHO ADUANERO', 'ADA140903N97')]}
        mapa = mapear_clientes(filas)
        self.assertEqual(
            mapa['ABBA DESPACHO ADUANERO'],
            {'cve_cliente': 'ABBA', 'rfc': 'ADA140903N97'},
        )

    def test_mismo_cliente_en_dos_patentes_no_se_duplica(self):
        from .management.commands.import_clientes_casa import mapear_clientes
        filas = {
            '1627': [('FISCHE', 'FISCHER SISTEMAS', 'FSF060614QC2')],
            '1656': [('FISCHE', 'FISCHER SISTEMAS', 'FSF060614QC2')],
        }
        mapa = mapear_clientes(filas)
        self.assertEqual(len(mapa), 1)

    def test_ignora_filas_sin_nombre(self):
        from .management.commands.import_clientes_casa import mapear_clientes
        filas = {'1656': [('X', '', 'ADA140903N97')]}
        mapa = mapear_clientes(filas)
        self.assertEqual(mapa, {})


class ImportarClientesTests(TestCase):
    def test_crea_cliente_nuevo(self):
        from .management.commands.import_clientes_casa import importar_clientes
        mapa = {'ACME SA': {'cve_cliente': 'ACME', 'rfc': 'ACM010101AAA'}}
        creados, actualizados = importar_clientes(mapa, dry_run=False, stdout=_NullOut())
        self.assertEqual((creados, actualizados), (1, 0))
        cliente = Cliente.objects.get(nombre_cliente='ACME SA')
        self.assertEqual(cliente.cve_cliente, 'ACME')
        self.assertEqual(cliente.rfc, 'ACM010101AAA')

    def test_actualiza_cve_y_rfc_sin_tocar_emails(self):
        from .management.commands.import_clientes_casa import importar_clientes
        Cliente.objects.create(
            nombre_cliente='ACME SA', cve_cliente='VIEJO', rfc='VIEJO000000AAA',
            email_cobranza='cobranza@acme.com',
        )
        mapa = {'ACME SA': {'cve_cliente': 'ACME', 'rfc': 'ACM010101AAA'}}
        creados, actualizados = importar_clientes(mapa, dry_run=False, stdout=_NullOut())
        self.assertEqual((creados, actualizados), (0, 1))
        cliente = Cliente.objects.get(nombre_cliente='ACME SA')
        self.assertEqual(cliente.cve_cliente, 'ACME')
        self.assertEqual(cliente.rfc, 'ACM010101AAA')
        self.assertEqual(cliente.email_cobranza, 'cobranza@acme.com')

    def test_dry_run_no_escribe(self):
        from .management.commands.import_clientes_casa import importar_clientes
        mapa = {'ACME SA': {'cve_cliente': 'ACME', 'rfc': 'ACM010101AAA'}}
        importar_clientes(mapa, dry_run=True, stdout=_NullOut())
        self.assertEqual(Cliente.objects.count(), 0)


class _NullOut:
    def write(self, *args, **kwargs):
        pass
