from decimal import Decimal
from django.test import TestCase
from referencias.models import Referencia, Contenedor
from glosa.models import DocumentoReferencia, GlosaAutomaticaResultado
from glosa.services_cruce import ejecutar_cruce, normalizar_nombre

class GlosaCruceEngineTest(TestCase):
    def setUp(self):
        self.ref = Referencia.objects.create(
            num_refe="LCRR3940/26",
            patente="1656",
            num_pedimento="263737706703661",
            peso_bruto=Decimal("24.500"),  # toneladas (CTRAO_EMBAR)
        )

    def test_preforma_choice_exists(self):
        choices = dict(DocumentoReferencia.TIPO_CHOICES)
        self.assertIn('PREFORMA', choices)
        self.assertEqual(choices['PREFORMA'], 'Pre-forma de Pedimento')

    def test_bloqueante_referencia_distinta(self):
        DocumentoReferencia.objects.create(
            referencia=self.ref,
            tipo_documento='PREFORMA',
            datos_extraidos={
                'num_refe': 'LCRR5940/26',
                'num_pedimento': '6705455'
            }
        )
        res = ejecutar_cruce(self.ref)
        self.assertEqual(res['estado'], 'DISCREPANCIAS_DETECTADAS')
        self.assertIsNotNone(res['bloqueante'])
        self.assertEqual(res['bloqueante']['tipo'], 'REFERENCIA_NO_COINCIDE')
        self.assertIsInstance(res['discrepancias'][0], dict)
        self.assertEqual(res['discrepancias'][0]['codigo'], 'REFERENCIA_NO_COINCIDE')

    def test_error_lectura_contenedor_bl(self):
        DocumentoReferencia.objects.create(
            referencia=self.ref,
            tipo_documento='PREFORMA',
            datos_extraidos={
                'num_refe': 'LCRR3940/26',
                'contenedores': ['MSCU4411834']
            }
        )
        DocumentoReferencia.objects.create(
            referencia=self.ref,
            tipo_documento='BL',
            datos_extraidos={
                'contenedores': [{'numero': 'MSCU4411934'}]
            }
        )
        DocumentoReferencia.objects.create(
            referencia=self.ref,
            tipo_documento='LISTA_EMPAQUE',
            datos_extraidos={
                'contenedores': [{'numero': 'MSCU4411834'}]
            }
        )
        res = ejecutar_cruce(self.ref)
        self.assertEqual(res['estado'], 'PENDIENTE')
        regla_cont = next(r for r in res['reglas'] if r['campo'] == 'contenedores')
        self.assertEqual(regla_cont['estado'], 'ERROR_LECTURA')

    def test_peso_casa_toneladas_contra_bl(self):
        # Sin pre-forma, el respaldo CASA usa ref.peso_kg (24.5 t = 24500 kg).
        DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='BL', datos_extraidos={'peso_bruto_kg': '24500'},
        )
        res = ejecutar_cruce(self.ref)
        regla_peso = next(r for r in res['reglas'] if r['campo'] == 'peso_bruto_kg')
        self.assertEqual(regla_peso['estado'], 'OK')

    def test_peso_dentro_tolerancia(self):
        DocumentoReferencia.objects.create(
            referencia=self.ref,
            tipo_documento='PREFORMA',
            datos_extraidos={
                'num_refe': 'LCRR3940/26',
                'peso_bruto_kg': 24500
            }
        )
        DocumentoReferencia.objects.create(
            referencia=self.ref,
            tipo_documento='BL',
            datos_extraidos={
                'peso_bruto_kg': 24550
            }
        )
        res = ejecutar_cruce(self.ref)
        regla_peso = next(r for r in res['reglas'] if r['campo'] == 'peso_bruto_kg')
        self.assertEqual(regla_peso['estado'], 'OK')

    def _preforma_bl_subdivision(self, peso_le):
        DocumentoReferencia.objects.create(
            referencia=self.ref,
            tipo_documento='PREFORMA',
            datos_extraidos={
                'num_refe': 'LCRR3940/26',
                'peso_bruto_kg': '2787.500',
                'subdivision': {'detectada': True, 'pedimento_relacionado': None},
            }
        )
        DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='BL',
            datos_extraidos={'peso_bruto_kg': '18895.0'}
        )
        DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='LISTA_EMPAQUE',
            datos_extraidos={'peso_bruto_kg': peso_le}
        )
        res = ejecutar_cruce(self.ref)
        return next(r for r in res['reglas'] if r['campo'] == 'peso_bruto_kg')

    def test_peso_lista_empaque_coincide_con_preforma(self):
        regla = self._preforma_bl_subdivision('2787.5')
        fuentes = {f['fuente']: f for f in regla['fuentes']}
        self.assertIn('LISTA_EMPAQUE', fuentes)
        self.assertTrue(fuentes['LISTA_EMPAQUE']['coincide'])
        self.assertEqual(regla['estado'], 'REVISAR_SUBDIVISION')

    def test_peso_lista_empaque_difiere_es_discrepancia(self):
        regla = self._preforma_bl_subdivision('3100')
        self.assertEqual(regla['estado'], 'DISCREPANCIA')

    def test_normalizacion_proveedor(self):
        norm1 = normalizar_nombre("PARCELMOBI SA DE CV")
        norm2 = normalizar_nombre("Parcelmobi, S.A. de C.V.")
        self.assertEqual(norm1, norm2)

    def test_reglas_ed(self):
        DocumentoReferencia.objects.create(
            referencia=self.ref,
            tipo_documento='PREFORMA',
            datos_extraidos={
                'num_refe': 'LCRR3940/26',
                'documentos_ed': ['0170261GUVAM8']
            }
        )
        DocumentoReferencia.objects.create(
            referencia=self.ref,
            tipo_documento='FACTURA',
            archivo='glosa_documentos/0438261JV1IJ4.pdf'
        )
        res = ejecutar_cruce(self.ref)
        # ED faltante o no declarado es advertencia: no bloquea la glosa.
        self.assertEqual(res['discrepancias'], [])
        self.assertEqual(res['estado'], 'EN_REVISION')
        codigos = sorted(a['codigo'] for a in res['advertencias'])
        self.assertEqual(codigos, ['ED_NO_DECLARADO', 'ED_SIN_DOCUMENTO'])
        self.assertTrue(all(a['severidad'] == 'ADVERTENCIA' for a in res['advertencias']))

    def test_certificado_vencido(self):
        DocumentoReferencia.objects.create(
            referencia=self.ref,
            tipo_documento='PREFORMA',
            datos_extraidos={
                'num_refe': 'LCRR3940/26',
                'certificados': ['CERT-123']
            }
        )
        DocumentoReferencia.objects.create(
            referencia=self.ref,
            tipo_documento='CERTIFICADOS',
            datos_extraidos={
                'numero_certificado': 'CERT-123',
                'fecha_vigencia': '2025-01-01'
            }
        )
        res = ejecutar_cruce(self.ref)
        self.assertEqual(res['estado'], 'DISCREPANCIAS_DETECTADAS')
        self.assertTrue(any('vencido' in d.get('mensaje', '') for d in res['discrepancias']))

        # Verify structure of discrepancy dict
        disc = next(d for d in res['discrepancias'] if d['codigo'] == 'CERTIFICADO_VENCIDO')
        self.assertIn('codigo', disc)
        self.assertIn('mensaje', disc)
        self.assertIn('campo', disc)
        self.assertIn('documento_id', disc)
        self.assertIn('tipo_documento', disc)
        self.assertIn('valor_encontrado', disc)
        self.assertIn('valor_esperado', disc)
        self.assertIn('en_preforma', disc)

    def test_cruce_filtra_activos_y_genera_discrepancias_estructuradas(self):
        # Archived certificate should be ignored
        doc_vencido = DocumentoReferencia.objects.create(
            referencia=self.ref,
            tipo_documento='CERTIFICADOS',
            activo=False,
            datos_extraidos={'numero_certificado': 'CERT-OLD', 'fecha_vigencia': '2020-01-01'}
        )
        DocumentoReferencia.objects.create(
            referencia=self.ref,
            tipo_documento='PREFORMA',
            datos_extraidos={
                'num_refe': 'LCRR3940/26',
                'certificados': []
            }
        )
        res = ejecutar_cruce(self.ref)
        self.assertFalse(any(d.get('codigo') == 'CERTIFICADO_VENCIDO' for d in res['discrepancias']))
