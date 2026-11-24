from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from glosa.models import DocumentoReferencia
from glosa.services_cruce import ejecutar_cruce
from glosa.services_cruce_partidas import emparejar, similitud_descripcion
from referencias.models import Referencia


def _partida(sec, descripcion, cantidad, precio_pagado, modelo='', identificadores=None):
    return {
        'sec': sec, 'descripcion': descripcion, 'cantidad_umc': cantidad,
        'precio_pagado': precio_pagado, 'modelo': modelo,
        'identificadores': identificadores or [],
    }


class EmparejeDescripcionTest(TestCase):
    def test_similitud_ignora_acentos_y_palabras_vacias(self):
        self.assertEqual(
            similitud_descripcion('Martillo de uña 16 oz', 'MARTILLO UNA 16 OZ'),
            Decimal('1'),
        )

    def test_varios_renglones_a_una_partida(self):
        partidas = [{'descripcion': 'MARTILLO DE UÑA'}, {'descripcion': 'PINZAS DE CORTE'}]
        renglones = [
            {'descripcion': 'Martillo de uña 16oz'},
            {'descripcion': 'Martillo de uña 20oz'},
            {'descripcion': 'Desarmador plano'},
        ]
        asignados, sin_partida = emparejar(partidas, renglones)
        self.assertEqual(len(asignados[(0,)]), 2)
        self.assertEqual(asignados[(1,)], [])
        self.assertEqual([r['descripcion'] for r in sin_partida], ['Desarmador plano'])


class CrucePartidasTest(TestCase):
    def setUp(self):
        self.ref = Referencia.objects.create(num_refe='LCRR4000/26', patente='1656', num_pedimento='6000001')
        self.hoy = date.today()

    def _preforma(self, partidas, valor_moneda_factura='1000.00'):
        return DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='PREFORMA',
            datos_extraidos={
                'num_refe': 'LCRR4000/26',
                'valor_moneda_factura': valor_moneda_factura,
                'partidas': partidas,
            },
        )

    def _factura(self, renglones, valor_total='1000.00'):
        return DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='FACTURA',
            datos_extraidos={'valor_total': valor_total, 'partidas': renglones},
        )

    def _lista(self, renglones):
        return DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='LISTA_EMPAQUE',
            datos_extraidos={'partidas': renglones},
        )

    def _codigos(self, lista):
        return sorted(d['codigo'] for d in lista)

    def test_partidas_cuadran_sin_discrepancias(self):
        # Precio pagado en pesos, tipo de cambio 20: 12000 / 20 = 600.
        self._preforma([
            _partida('001', 'MARTILLO DE UÑA', '100', '12000'),
            _partida('002', 'PINZAS DE CORTE', '50', '8000'),
        ])
        self._factura([
            {'descripcion': 'Martillo de uña 16oz', 'cantidad': '60', 'importe': '360.00'},
            {'descripcion': 'Martillo de uña 20oz', 'cantidad': '40', 'importe': '240.00'},
            {'descripcion': 'Pinzas de corte', 'cantidad': '50', 'importe': '400.00'},
        ])
        self._lista([
            {'descripcion': 'Martillo uña', 'cantidad': '100'},
            {'descripcion': 'Pinzas corte', 'cantidad': '50'},
        ])
        res = ejecutar_cruce(self.ref)
        self.assertEqual(res['discrepancias'], [])
        self.assertEqual(res['advertencias'], [])
        self.assertEqual(res['estado'], 'EN_REVISION')

    def test_cantidad_e_importe_no_cuadran_son_discrepancias(self):
        self._preforma([
            _partida('001', 'MARTILLO DE UÑA', '100', '12000'),
            _partida('002', 'PINZAS DE CORTE', '50', '8000'),
        ])
        self._factura([
            {'descripcion': 'Martillo de uña', 'cantidad': '90', 'importe': '360.00'},
            {'descripcion': 'Pinzas de corte', 'cantidad': '50', 'importe': '640.00'},
        ])
        self._lista([
            {'descripcion': 'Martillo uña', 'cantidad': '100'},
            {'descripcion': 'Pinzas corte', 'cantidad': '45'},
        ])
        res = ejecutar_cruce(self.ref)
        self.assertEqual(res['estado'], 'DISCREPANCIAS_DETECTADAS')
        self.assertEqual(self._codigos(res['discrepancias']), [
            'PARTIDA_CANTIDAD_FACTURA',
            'PARTIDA_CANTIDAD_LISTA_EMPAQUE',
            'PARTIDA_IMPORTE_FACTURA',
            'PARTIDA_IMPORTE_FACTURA',
        ])
        cant = next(d for d in res['discrepancias'] if d['codigo'] == 'PARTIDA_CANTIDAD_FACTURA')
        self.assertIn('Partida 001', cant['mensaje'])
        self.assertTrue(cant['en_preforma'])

    def test_partidas_con_misma_descripcion_se_comparan_juntas(self):
        # Mismo producto en dos partidas (distinto permiso): la factura no las distingue.
        self._preforma([
            _partida('001', 'JUEGO DE CHUMACERAS Y ACCESORIOS PARA CARRETILLA', '9700', '672525'),
            _partida('002', 'EJE PARA CARRETILLA', '10140', '631686'),
            _partida('003', 'JUEGO DE CHUMACERAS Y ACCESORIOS PARA CARRETILLA', '2920', '192920'),
        ], valor_moneda_factura='74856.58')
        self._factura([
            {'descripcion': 'Juego de chumaceras y accesorios para carretilla', 'cantidad': '12620', 'importe': '43272.28'},
            {'descripcion': 'Eje para carretilla', 'cantidad': '10140', 'importe': '31584.30'},
        ], valor_total='74856.58')
        res = ejecutar_cruce(self.ref)
        self.assertEqual(res['discrepancias'], [])
        self.assertEqual(res['advertencias'], [])

    def test_grupo_con_cantidad_distinta_nombra_ambas_partidas(self):
        self._preforma([
            _partida('001', 'JUEGO DE CHUMACERAS', '9700', '1000'),
            _partida('003', 'JUEGO DE CHUMACERAS', '2920', '1000'),
        ])
        self._lista([{'descripcion': 'Juego de chumaceras', 'cantidad': '12000'}])
        res = ejecutar_cruce(self.ref)
        disc = [d for d in res['discrepancias'] if d['codigo'] == 'PARTIDA_CANTIDAD_LISTA_EMPAQUE']
        self.assertEqual(len(disc), 1)
        self.assertIn('Partidas 001, 003', disc[0]['mensaje'])
        self.assertNotIn('ocurrencia_busqueda', disc[0])

    def test_nom_marca_solo_la_ocurrencia_de_su_partida(self):
        self._preforma([
            _partida('001', 'JUEGO DE CHUMACERAS', '10', '1000'),
            _partida('003', 'JUEGO DE CHUMACERAS', '10', '1000',
                     identificadores=[{'clave': 'NM', 'complemento1': 'NOM-050-SCFI-2004'}]),
        ])
        res = ejecutar_cruce(self.ref)
        disc = next(d for d in res['discrepancias'] if d['codigo'] == 'PARTIDA_SIN_CERTIFICADO_NOM')
        self.assertEqual(disc['ocurrencia_busqueda'], 1)

    def test_valor_factura_distinto_a_preforma(self):
        self._preforma([_partida('001', 'MARTILLO', '10', '2000')], valor_moneda_factura='100.00')
        self._factura([{'descripcion': 'Martillo', 'cantidad': '10', 'importe': '150.00'}], valor_total='150.00')
        res = ejecutar_cruce(self.ref)
        self.assertIn('VALOR_FACTURA', self._codigos(res['discrepancias']))

    def test_sin_pareja_es_advertencia(self):
        self._preforma([_partida('001', 'MARTILLO DE UÑA', '100', '20000')])
        self._factura([
            {'descripcion': 'Martillo de uña', 'cantidad': '100', 'importe': '1000.00'},
            {'descripcion': 'Desarmador plano', 'cantidad': '5', 'importe': None},
        ])
        self._lista([{'descripcion': 'Claw hammer', 'cantidad': '100'}])
        res = ejecutar_cruce(self.ref)
        self.assertEqual(res['discrepancias'], [])
        self.assertEqual(self._codigos(res['advertencias']), [
            'PARTIDA_SIN_LISTA_EMPAQUE',
            'RENGLON_FACTURA_SIN_PARTIDA',
            'RENGLON_LISTA_EMPAQUE_SIN_PARTIDA',
        ])

    def test_partida_nom_sin_certificado(self):
        self._preforma([
            _partida('001', 'LAMPARA LED', '10', '2000', modelo='LED-100',
                     identificadores=[{'clave': 'NM', 'complemento1': '1', 'complemento2': 'NOM-031-ENER-2019'}]),
            _partida('002', 'MARTILLO', '10', '2000'),
        ])
        res = ejecutar_cruce(self.ref)
        disc = [d for d in res['discrepancias'] if d['codigo'] == 'PARTIDA_SIN_CERTIFICADO_NOM']
        self.assertEqual(len(disc), 1)
        self.assertIn('Partida 001', disc[0]['mensaje'])
        self.assertIn('NOM-031-ENER', disc[0]['mensaje'])

    def test_partida_nom_con_certificado_que_cubre_norma_y_modelo(self):
        self._preforma([
            _partida('001', 'LAMPARA LED', '10', '2000', modelo='LED-100',
                     identificadores=[{'clave': 'EN', 'complemento1': 'NOM-031-ENER-2019'}]),
        ])
        DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='CERTIFICADOS',
            datos_extraidos={
                'numero_certificado': 'CERT-1',
                'fecha_vigencia': (self.hoy + timedelta(days=30)).isoformat(),
                'normas': ['NOM-031-ENER-2019'],
                'modelos': ['LED 100', 'LED-200'],
            },
        )
        res = ejecutar_cruce(self.ref)
        self.assertNotIn('PARTIDA_SIN_CERTIFICADO_NOM', self._codigos(res['discrepancias']))

    def test_certificado_de_otro_modelo_no_cubre(self):
        self._preforma([
            _partida('001', 'LAMPARA LED', '10', '2000', modelo='LED-300',
                     identificadores=[{'clave': 'NM', 'complemento1': 'NOM-031-ENER-2019'}]),
        ])
        DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='CERTIFICADOS',
            datos_extraidos={
                'numero_certificado': 'CERT-1',
                'fecha_vigencia': (self.hoy + timedelta(days=30)).isoformat(),
                'normas': ['NOM-031-ENER-2019'], 'modelos': ['LED-100'],
            },
        )
        res = ejecutar_cruce(self.ref)
        self.assertIn('PARTIDA_SIN_CERTIFICADO_NOM', self._codigos(res['discrepancias']))

    def test_permiso_no_declarado_es_advertencia(self):
        self._preforma([_partida('001', 'MARTILLO', '10', '2000')])
        DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='PERMISOS',
            datos_extraidos={'numero_permiso': 'P-123', 'fecha_vigencia': self.hoy.isoformat()},
        )
        res = ejecutar_cruce(self.ref)
        self.assertEqual(self._codigos(res['advertencias']), ['PERMISO_NO_DECLARADO'])

    def _permiso_doc(self, numero, vigencia, fracciones=None):
        return DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='PERMISOS',
            datos_extraidos={'numero_permiso': numero, 'fecha_vigencia': vigencia.isoformat(),
                             'fracciones': fracciones or []},
        )

    def test_permiso_del_bloque_cuadra(self):
        p = _partida('001', 'JUEGO DE CHUMACERAS', '9700', '672525')
        p['fraccion'] = '98020015'
        p['permisos'] = [{'clave': 'C1', 'numero': '9901R826078685', 'cantidad_umt': '9700'}]
        self._preforma([p])
        self._permiso_doc('9901R826078685', self.hoy + timedelta(days=90), ['98020015'])
        res = ejecutar_cruce(self.ref)
        self.assertEqual(res['discrepancias'], [])
        self.assertEqual(res['advertencias'], [])

    def test_permiso_del_bloque_sin_documento_vencido_y_fraccion(self):
        p1 = _partida('001', 'JUEGO DE CHUMACERAS', '10', '1000')
        p1['fraccion'] = '98020015'
        p1['permisos'] = [{'clave': 'C1', 'numero': '9901R826078685'}]
        p2 = _partida('002', 'EJE PARA CARRETILLA', '10', '1000')
        p2['fraccion'] = '98020007'
        p2['permisos'] = [{'clave': 'C1', 'numero': '9901R826008262'}]
        p3 = _partida('003', 'JUEGO DE CHUMACERAS', '10', '1000')
        p3['fraccion'] = '98020015'
        p3['permisos'] = [{'clave': 'C1', 'numero': '9901R826078685'}]
        self._preforma([p1, p2, p3])
        self._permiso_doc('9901R826078685', self.hoy - timedelta(days=1), ['98020099'])
        res = ejecutar_cruce(self.ref)
        self.assertEqual(self._codigos(res['discrepancias']), [
            'PARTIDA_SIN_PERMISO', 'PERMISO_FRACCION', 'PERMISO_FRACCION', 'PERMISO_VENCIDO',
        ])
        sin = next(d for d in res['discrepancias'] if d['codigo'] == 'PARTIDA_SIN_PERMISO')
        self.assertIn('9901R826008262', sin['mensaje'])
        ocurrencias = sorted(d['ocurrencia_busqueda'] for d in res['discrepancias'] if d['codigo'] == 'PERMISO_FRACCION')
        self.assertEqual(ocurrencias, [0, 1])

    def test_nom_en_bloque_permisos_exige_certificado(self):
        p = _partida('001', 'LAMPARA LED', '10', '1000')
        p['permisos'] = [{'clave': 'NM', 'numero': 'NOM-031-ENER-2019'}]
        self._preforma([p])
        res = ejecutar_cruce(self.ref)
        self.assertEqual(self._codigos(res['discrepancias']), ['PARTIDA_SIN_CERTIFICADO_NOM'])

    def test_resumen_por_partida(self):
        p1 = _partida('001', 'JUEGO DE CHUMACERAS', '9700', '672525')
        p1['permisos'] = [{'clave': 'C1', 'numero': '9901R826078685'}]
        p2 = _partida('002', 'EJE PARA CARRETILLA', '10140', '631686',
                      identificadores=[{'clave': 'NM', 'complemento1': 'NOM-050-SCFI-2004'}])
        p3 = _partida('003', 'JUEGO DE CHUMACERAS', '2920', '192920')
        self._preforma([p1, p2, p3], valor_moneda_factura='74856.58')
        self._factura([
            {'descripcion': 'Juego de chumaceras', 'cantidad': '12620', 'importe': '43272.28'},
            {'descripcion': 'Eje para carretilla', 'cantidad': '10140', 'importe': '31584.30'},
        ], valor_total='74856.58')
        self._lista([{'descripcion': 'Juego de chumaceras', 'cantidad': '12000'}])
        self._permiso_doc('9901R826078685', self.hoy + timedelta(days=30))

        filas = {f['sec']: f for f in ejecutar_cruce(self.ref)['partidas']}
        self.assertEqual(list(filas), ['001', '002', '003'])

        self.assertEqual(filas['001']['grupo'], '001, 003')
        self.assertEqual(filas['001']['factura'], {'valor': '12620', 'estado': 'OK', 'preforma': '12620'})
        self.assertEqual(filas['001']['lista_empaque']['estado'], 'DISC')
        self.assertEqual(filas['001']['importe']['estado'], 'OK')
        self.assertEqual(filas['001']['permiso'], {'valor': '9901R826078685', 'estado': 'OK'})
        self.assertIsNone(filas['001']['nom'])
        self.assertEqual(filas['001']['estado'], 'DISCREPANCIA')

        self.assertIsNone(filas['002']['grupo'])
        self.assertEqual(filas['002']['lista_empaque'], {'valor': None, 'estado': 'SIN'})
        self.assertEqual(filas['002']['nom'], {'valor': 'NOM-050-SCFI', 'estado': 'DISC'})
        self.assertIsNone(filas['002']['permiso'])
        self.assertEqual(filas['002']['estado'], 'DISCREPANCIA')

        self.assertEqual(filas['003']['estado'], 'DISCREPANCIA')
