"""Pruebas de los ajustes y mejoras del port a Loginco (spec 2026-11-23, secciones 5, 6 y 12)."""
import os
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from glosa import services_procesamiento as proc
from glosa.models import DocumentoReferencia, GlosaAutomaticaResultado, GlosaEvento
from glosa.services_cruce import ejecutar_cruce
from glosa.services_cruce_partidas import cruzar_partidas
from glosa.services_extraccion import modelos_de_observaciones, uso_y_costo, validar_y_normalizar
from glosa.tests.test_extraccion import BL_CRUDO, cliente_falso
from glosa.tests.test_jev_ia import TEXTO_BL, pdf_con_texto
from glosa.views import nombre_pdf_anotado
from referencias.models import Contenedor, GuiaBL, Referencia

SIN_IA = {'JEV_AI_API_KEY': '', 'TYPESAFE_API_KEY': '', 'AAALAC_API_KEY': ''}


_PDF_CACHE = {}


def _pdf(nombre='doc.pdf', texto=TEXTO_BL):
    # Mismo texto, mismos bytes: PyMuPDF pone fecha e ID en cada PDF generado.
    if texto not in _PDF_CACHE:
        _PDF_CACHE[texto] = pdf_con_texto(texto).getvalue()
    return SimpleUploadedFile(nombre, _PDF_CACHE[texto], content_type='application/pdf')


# ---------------------------------------------------------------------------
# Extracción: modelos en observaciones y uso de tokens
# ---------------------------------------------------------------------------

class ModelosObservacionesTest(SimpleTestCase):
    def test_modelos_con_codigo_interno(self):
        self.assertEqual(
            modelos_de_observaciones('MODELO:\n115117(100200248)\n142053(100238887)'),
            ['115117', '142053'],
        )

    def test_modelos_con_letras_espaciadas(self):
        self.assertEqual(modelos_de_observaciones('MODELOS: X G E - 6 4 4 7 \t \t XGE-6448'), ['XGE-6447', 'XGE-6448'])

    def test_sin_modelos(self):
        self.assertEqual(modelos_de_observaciones(''), [])
        self.assertEqual(modelos_de_observaciones('MARCA: REEBOK'), [])

    def test_normalizacion_preforma_agrega_modelos_observaciones(self):
        datos, _ = validar_y_normalizar('PREFORMA', {
            'num_refe': 'LCLF2854/25', 'num_pedimento': '25 51 1627 5002854', 'contenedores': ['GESU6147138 / 3'],
            'observaciones': '', 'partidas': [{
                'sec': '001', 'fraccion': '64039913', 'nico': '03', 'cantidad_umc': '342.000',
                'precio_pagado': '57109', 'precio_unitario': '166.98538', 'cantidad_umt': '342',
                'modelo': '', 'observaciones': 'MODELO:\n115117(100200248)',
            }],
        })
        self.assertEqual(datos['num_pedimento'], '5002854')
        self.assertEqual(datos['num_refe'], 'LCLF2854/25')
        self.assertEqual(datos['contenedores'], ['GESU6147138'])
        self.assertEqual(datos['partidas'][0]['modelos_observaciones'], ['115117'])


class UsoYCostoTest(SimpleTestCase):
    def _resp(self):
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=2_000_000, output_tokens=500_000))

    @patch.dict(os.environ, {'IA_COSTO_ENTRADA_USD_MTOK': '', 'IA_COSTO_SALIDA_USD_MTOK': ''})
    def test_sin_precios_no_inventa_costo(self):
        uso = uso_y_costo(self._resp())
        self.assertEqual(uso, {'input_tokens': 2_000_000, 'output_tokens': 500_000, 'costo_usd': None})

    @patch.dict(os.environ, {'IA_COSTO_ENTRADA_USD_MTOK': '2', 'IA_COSTO_SALIDA_USD_MTOK': '10'})
    def test_con_precios_calcula_costo(self):
        self.assertEqual(uso_y_costo(self._resp())['costo_usd'], '9.000000')


# ---------------------------------------------------------------------------
# Cruce: NOM con modelos de observaciones y reglas de CASA
# ---------------------------------------------------------------------------

class NomModelosObservacionesTest(TestCase):
    def setUp(self):
        self.ref = Referencia.objects.create(num_refe='LCLF2854/25', patente='1627', num_pedimento='5002854')
        self.partida = {
            'sec': '001', 'descripcion': 'TENIS PARA NIÑOS', 'fraccion': '64039913', 'modelo': '',
            'modelos_observaciones': ['115117', '142053'], 'cantidad_umc': '342',
            'identificadores': [{'clave': 'EN', 'complemento1': 'X', 'complemento2': 'NOM-020-SCFI-1997', 'complemento3': ''}],
            'permisos': [],
        }

    def _cert(self, modelos):
        return DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='CERTIFICADOS', archivo='glosa_documentos/c.pdf',
            datos_extraidos={'normas': ['NOM-020-SCFI-1997'], 'modelos': modelos, 'fecha_vigencia': '2099-01-01'},
        )

    def _cruce(self, docs):
        pre = DocumentoReferencia.objects.create(referencia=self.ref, tipo_documento='PREFORMA', archivo='glosa_documentos/p.pdf')
        return cruzar_partidas(pre, {'partidas': [self.partida]}, docs)

    def test_certificado_cubre_todos_los_modelos(self):
        disc, _, resumen = self._cruce([self._cert(['115117', '142053'])])
        self.assertFalse([d for d in disc if d['codigo'] == 'PARTIDA_SIN_CERTIFICADO_NOM'])
        self.assertEqual(resumen[0]['modelo'], '115117, 142053')

    def test_falta_un_modelo_es_discrepancia(self):
        disc, _, _ = self._cruce([self._cert(['115117'])])
        self.assertTrue([d for d in disc if d['codigo'] == 'PARTIDA_SIN_CERTIFICADO_NOM'])


class CruceCasaTest(TestCase):
    def setUp(self):
        self.ref = Referencia.objects.create(
            num_refe='LCRR1124/23', patente='1656', num_pedimento='3001124', num_partidas=2,
        )
        GuiaBL.objects.create(referencia=self.ref, numero_guia='MEDUE0429443', tipo_guia='M')
        Contenedor.objects.create(referencia=self.ref, num_cont='MSMU5790096', tipo='40HC')

    def _preforma(self, **extra):
        datos = {'num_refe': 'LCRR1124/23', 'num_pedimento': '3001124', 'guias': ['MEDUE0429443'],
                 'contenedores': ['MSMU5790096'], 'partidas': [{'sec': '001'}, {'sec': '002'}]}
        datos.update(extra)
        return DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='PREFORMA', archivo='glosa_documentos/p.pdf', datos_extraidos=datos,
        )

    def _bl(self, numero):
        return DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='BL', archivo='glosa_documentos/bl.pdf',
            datos_extraidos={'numero_bl': numero, 'contenedores': [{'numero': 'MSMU5790096'}]},
        )

    def test_guia_bl_coincide(self):
        self._preforma()
        self._bl('MEDUE0429443')
        res = ejecutar_cruce(self.ref)
        regla = next(r for r in res['reglas'] if r['campo'] == 'guia_bl')
        self.assertEqual(regla['estado'], 'OK')

    def test_guia_bl_sin_prefijo_scac_coincide(self):
        self._preforma()
        self._bl('E0429443')
        regla = next(r for r in ejecutar_cruce(self.ref)['reglas'] if r['campo'] == 'guia_bl')
        self.assertEqual(regla['estado'], 'OK')

    def test_guia_bl_distinta_es_discrepancia(self):
        self._preforma()
        self._bl('MEDUX9999999')
        res = ejecutar_cruce(self.ref)
        self.assertEqual(res['estado'], 'DISCREPANCIAS_DETECTADAS')
        disc = next(d for d in res['discrepancias'] if d['codigo'] == 'GUIA_BL')
        self.assertTrue(disc['en_preforma'])
        self.assertEqual(disc['valor_encontrado'], 'MEDUE0429443')

    def test_contenedores_y_partidas_distintos_a_casa_son_advertencias(self):
        self._preforma(contenedores=['TEMU6544368'], partidas=[{'sec': '001'}])
        codigos = {a['codigo'] for a in ejecutar_cruce(self.ref)['advertencias']}
        self.assertIn('CASA_CONTENEDORES', codigos)
        self.assertIn('NUM_PARTIDAS', codigos)


# ---------------------------------------------------------------------------
# Vistas: subida, permisos, duplicados, procesamiento
# ---------------------------------------------------------------------------

@patch.dict(os.environ, SIN_IA)
@override_settings(IA_HABILITADA=False, ANTHROPIC_API_KEY='')
class SubidaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='capturista', password='pw')
        self.client = Client()
        self.client.force_login(self.user)
        self.ref = Referencia.objects.create(num_refe='LCRR0881/26', patente='1656', num_pedimento='3000881')
        self.url = reverse('glosa:glosa_ia_detalle', args=[self.ref.num_refe])

    def test_url_usa_num_refe_con_diagonal(self):
        self.assertEqual(self.url, '/referencias/LCRR0881/26/glosa-ia/')
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_cualquier_usuario_sube_y_queda_subido_por(self):
        self.client.post(self.url, {'archivo': _pdf()})
        doc = DocumentoReferencia.objects.get(referencia=self.ref)
        self.assertEqual(doc.subido_por, self.user)
        self.assertEqual(doc.procesamiento_estado, 'LISTO')
        self.assertEqual(len(doc.hash_sha256), 64)
        self.assertTrue(GlosaEvento.objects.filter(referencia=self.ref, tipo='SUBIDA', documento=doc).exists())
        self.assertTrue(GlosaEvento.objects.filter(referencia=self.ref, tipo='CRUCE').exists())

    def test_subida_multiple(self):
        self.client.post(self.url, {'archivo': [_pdf('a.pdf', 'FACTURA ' * 50), _pdf('b.pdf', 'PACKING LIST ' * 30)]})
        self.assertEqual(DocumentoReferencia.objects.filter(referencia=self.ref).count(), 2)

    def test_mismo_archivo_no_se_sube_dos_veces(self):
        self.client.post(self.url, {'archivo': _pdf('a.pdf')})
        response = self.client.post(self.url, {'archivo': _pdf('copia.pdf')}, follow=True)
        self.assertEqual(DocumentoReferencia.objects.filter(referencia=self.ref).count(), 1)
        self.assertContains(response, 'ya está en el expediente')

    def test_extension_no_permitida(self):
        archivo = SimpleUploadedFile('macro.exe', b'MZ' * 10)
        response = self.client.post(self.url, {'archivo': archivo}, follow=True)
        self.assertFalse(DocumentoReferencia.objects.exists())
        self.assertContains(response, 'tipo de archivo no permitido')

    @override_settings(GLOSA_MAX_MB=0)
    def test_tamano_maximo(self):
        response = self.client.post(self.url, {'archivo': _pdf()}, follow=True)
        self.assertFalse(DocumentoReferencia.objects.exists())
        self.assertContains(response, 'el máximo es')

    def test_no_glosador_no_aprueba_y_get_no_aprueba(self):
        GlosaAutomaticaResultado.objects.create(referencia=self.ref, estado_revision='EN_REVISION')
        aprobar = reverse('glosa:glosa_aprobar', args=[self.ref.num_refe])
        self.assertEqual(self.client.get(aprobar).status_code, 405)
        self.client.post(aprobar)
        self.assertEqual(GlosaAutomaticaResultado.objects.get(referencia=self.ref).estado_revision, 'EN_REVISION')

    def test_aaalac_oculto_sin_api_key(self):
        self.assertNotContains(self.client.get(self.url), 'Consultar AAALAC')

    @patch.dict(os.environ, {'AAALAC_API_KEY': 'k'})
    @patch('glosa.views.obtener_eta_mercancia')
    def test_aaalac_usa_bl_activo(self, mock_aaalac):
        mock_aaalac.return_value = {'fecha_llegada': '2026-12-01'}
        self.assertContains(self.client.get(self.url), 'Consultar AAALAC')
        DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='BL', archivo='glosa_documentos/viejo.pdf', activo=False,
            datos_extraidos={'buque': 'VIEJO', 'viaje': '1'},
        )
        DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='BL', archivo='glosa_documentos/nuevo.pdf',
            datos_extraidos={'buque': 'NUEVO', 'viaje': '2'},
        )
        self.client.post(self.url, {'consultar_aaalac': '1'})
        self.assertEqual(mock_aaalac.call_args.kwargs['buque'], 'NUEVO')
        resultado = GlosaAutomaticaResultado.objects.get(referencia=self.ref)
        self.assertEqual(str(resultado.eta_aaalac), '2026-12-01')
        # La consulta no pisa el resultado del cruce.
        self.assertNotIn('fecha_llegada', resultado.datos_comparacion)


class NombrePdfAnotadoTest(SimpleTestCase):
    def test_diagonal_se_reemplaza(self):
        self.assertEqual(nombre_pdf_anotado('LCRR0881/26'), 'preforma_observaciones_LCRR0881-26.pdf')


@patch.dict(os.environ, SIN_IA)
@override_settings(IA_HABILITADA=True, ANTHROPIC_API_KEY='test-key')
class ReutilizacionExtraccionTest(TestCase):
    @patch('glosa.services_extraccion._client')
    def test_mismo_archivo_en_otra_referencia_no_llama_a_claude(self, mock_claude):
        mock_claude.return_value = cliente_falso(BL_CRUDO)
        user = User.objects.create_superuser(username='admin', password='pw')
        client = Client()
        client.force_login(user)
        for num in ('LCRR0001/26', 'LCRR0002/26'):
            ref = Referencia.objects.create(num_refe=num, patente='1656')
            client.post(reverse('glosa:glosa_ia_detalle', args=[num]),
                        {'archivo': _pdf('bl.pdf', TEXTO_BL * 3), 'tipo_documento': 'BL'})
        self.assertEqual(mock_claude.return_value.messages.create.call_count, 1)
        segundo = DocumentoReferencia.objects.get(referencia__num_refe='LCRR0002/26')
        primero = DocumentoReferencia.objects.get(referencia__num_refe='LCRR0001/26')
        self.assertEqual(segundo.datos_extraidos['meta']['reutilizado_de'], primero.pk)
        self.assertEqual(segundo.datos_extraidos['buque'], 'MSC MARIA')


class _HiloInmediato:
    """Sustituye threading.Thread: corre el target al llamar start()."""
    def __init__(self, target, args=(), **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@patch.dict(os.environ, SIN_IA)
@override_settings(IA_HABILITADA=False, ANTHROPIC_API_KEY='', GLOSA_PROCESAMIENTO_ASINCRONO=True)
class ProcesamientoAsincronoTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='admin', password='pw')
        self.client = Client()
        self.client.force_login(self.user)
        self.ref = Referencia.objects.create(num_refe='LCMJ0010/26', patente='1927')
        self.url = reverse('glosa:glosa_ia_detalle', args=[self.ref.num_refe])

    def test_subida_queda_pendiente_y_se_procesa_al_commit(self):
        with patch('glosa.services_procesamiento.threading.Thread') as hilo:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.client.post(self.url, {'archivo': _pdf()}, follow=True)
            self.assertContains(response, 'se actualiza sola')
            doc = DocumentoReferencia.objects.get(referencia=self.ref)
            self.assertEqual(doc.procesamiento_estado, 'PENDIENTE')
            self.assertContains(self.client.get(self.url), 'Procesando')
            self.assertEqual(len(callbacks), 1)
            hilo.side_effect = _HiloInmediato
            with patch('glosa.services_procesamiento.close_old_connections'):
                callbacks[0]()
        doc.refresh_from_db()
        self.assertEqual(doc.procesamiento_estado, 'LISTO')
        self.assertEqual(doc.tipo_documento, 'BL')

    def test_aprobar_bloqueado_con_documentos_en_proceso(self):
        GlosaAutomaticaResultado.objects.create(referencia=self.ref, estado_revision='EN_REVISION')
        DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='BL', archivo='glosa_documentos/x.pdf',
            procesamiento_estado='PROCESANDO',
        )
        response = self.client.post(reverse('glosa:glosa_aprobar', args=[self.ref.num_refe]), follow=True)
        self.assertContains(response, 'procesándose')
        self.assertEqual(GlosaAutomaticaResultado.objects.get(referencia=self.ref).estado_revision, 'EN_REVISION')


@patch.dict(os.environ, SIN_IA)
@override_settings(IA_HABILITADA=False, ANTHROPIC_API_KEY='')
class ReprocesarAtoradosTest(TestCase):
    def test_comando_retoma_documento_atorado(self):
        ref = Referencia.objects.create(num_refe='LCLF0099/26', patente='1627')
        doc = DocumentoReferencia.objects.create(
            referencia=ref, archivo=_pdf(), procesamiento_estado='PROCESANDO',
            procesamiento_inicio=timezone.now() - timedelta(minutes=30),
        )
        reciente = DocumentoReferencia.objects.create(
            referencia=ref, archivo=_pdf('b.pdf', 'FACTURA ' * 40), procesamiento_estado='PROCESANDO',
            procesamiento_inicio=timezone.now(),
        )
        call_command('glosa_reprocesar', stdout=open(os.devnull, 'w'))
        doc.refresh_from_db()
        reciente.refresh_from_db()
        self.assertEqual(doc.procesamiento_estado, 'LISTO')
        self.assertEqual(doc.tipo_documento, 'BL')
        self.assertEqual(reciente.procesamiento_estado, 'PROCESANDO')


class GrupoGlosadorTest(TestCase):
    def test_migracion_crea_grupo(self):
        self.assertTrue(Group.objects.filter(name='Glosador').exists())


# ---------------------------------------------------------------------------
# Métricas, reporte semanal, dashboard y bot
# ---------------------------------------------------------------------------

class MetricasGlosaIATest(TestCase):
    def setUp(self):
        self.ref = Referencia.objects.create(num_refe='LCRR0500/26', patente='1656')

    def _doc(self, tipo, tipo_ia=None, uso=None, reutilizado=False):
        clasif = {'estado': 'OK', 'uso': {'input_tokens': 100}}
        if tipo_ia:
            clasif['tipo_ia'] = tipo_ia
        meta = {'tipo': tipo, 'estado': 'OK', 'uso': uso or {'input_tokens': 1000, 'output_tokens': 200, 'costo_usd': '0.50'}}
        if reutilizado:
            meta['reutilizado_de'] = 1
        return DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento=tipo, archivo='glosa_documentos/m.pdf',
            clasificacion_ia=clasif, datos_extraidos={'meta': meta},
        )

    def test_precision_y_uso(self):
        from glosa.metricas import get_datos_glosa_ia_semana
        self._doc('BL', tipo_ia='BL')
        self._doc('FACTURA', tipo_ia='FACTURA')
        self._doc('LISTA_EMPAQUE', tipo_ia='FACTURA')
        self._doc('PREFORMA', reutilizado=True)
        GlosaAutomaticaResultado.objects.create(
            referencia=self.ref, estado_revision='DISCREPANCIAS_DETECTADAS',
            datos_comparacion={'discrepancias': [{'codigo': 'GUIA_BL'}, {'codigo': 'GUIA_BL'}, {'codigo': 'VALOR_FACTURA'}]},
        )
        hoy = timezone.localdate()
        datos = get_datos_glosa_ia_semana(hoy - timedelta(days=6), hoy)
        self.assertEqual(datos['documentos_subidos'], 4)
        self.assertEqual(datos['expedientes'], 1)
        self.assertEqual(datos['precision_jev']['total'], 3)
        self.assertEqual(datos['precision_jev']['pct'], 66.7)
        self.assertEqual(datos['precision_jev']['confusiones'], [{'jev': 'FACTURA', 'final': 'LISTA_EMPAQUE', 'total': 1}])
        self.assertEqual(datos['uso']['claude_input_tokens'], 4000)
        self.assertEqual(datos['uso']['costo_usd'], '2.00')
        self.assertEqual(datos['uso']['jev_input_tokens'], 400)
        self.assertEqual(datos['uso']['extracciones_reutilizadas'], 1)
        self.assertEqual(datos['top_discrepancias'][0], {'codigo': 'GUIA_BL', 'total': 2})
        self.assertEqual(datos['con_discrepancias'], 1)

    def test_reporte_semanal_incluye_glosa_ia(self):
        from django.template.loader import render_to_string
        from reportes.data import get_datos_semana
        self._doc('BL', tipo_ia='BL')
        hoy = timezone.localdate()
        datos = get_datos_semana(hoy - timedelta(days=6), hoy)
        self.assertEqual(datos['glosa_ia']['documentos_subidos'], 1)
        html = render_to_string('reportes/semanal.html', {'datos': datos})
        self.assertIn('Glosa IA', html)
        self.assertIn('Precisión Jev', html)

    def test_dashboard_glosa_muestra_glosa_ia(self):
        self._doc('BL', tipo_ia='BL')
        client = Client()
        client.force_login(User.objects.create_superuser(username='admin', password='pw'))
        response = client.get(reverse('glosa_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Precisión Jev')

    def test_cambio_de_tipo_conserva_tipo_ia_para_la_metrica(self):
        doc = self._doc('FACTURA', tipo_ia='FACTURA')
        client = Client()
        client.force_login(User.objects.create_superuser(username='admin', password='pw'))
        with patch.dict(os.environ, SIN_IA), override_settings(IA_HABILITADA=False):
            client.post(reverse('glosa:documento_actualizar', args=[doc.pk]), {'tipo_documento': 'LISTA_EMPAQUE'})
        doc.refresh_from_db()
        self.assertEqual(doc.clasificacion_ia['tipo_ia'], 'FACTURA')
        self.assertTrue(GlosaEvento.objects.filter(documento=doc, tipo='CAMBIO_TIPO').exists())


class BotGlosaTest(TestCase):
    def test_comando_glosa(self):
        from whatsapp.bot import handle_command
        ref = Referencia.objects.create(num_refe='LCRR0881/26', patente='1656')
        self.assertIn('sin documentos', handle_command('glosa LCRR0881/26'))
        DocumentoReferencia.objects.create(referencia=ref, tipo_documento='BL', archivo='glosa_documentos/b.pdf')
        GlosaAutomaticaResultado.objects.create(
            referencia=ref, estado_revision='DISCREPANCIAS_DETECTADAS',
            datos_comparacion={'discrepancias': [{'codigo': 'GUIA_BL', 'mensaje': 'El B/L X no corresponde'}],
                               'advertencias': [{'codigo': 'NUM_PARTIDAS'}]},
        )
        texto = handle_command('glosa lcrr0881/26')
        self.assertIn('Con discrepancias', texto)
        self.assertIn('El B/L X no corresponde', texto)
        self.assertIn('Advertencias: 1', texto)
        self.assertIn('No se encontró', handle_command('glosa LCRR9999/26'))
        self.assertIn('glosa LCRR0881/26', handle_command('ayuda'))
