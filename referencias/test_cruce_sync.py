import json
from datetime import timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.test import override_settings

from referencias.models import (
    Referencia, Contenedor, Doda, DodaReferencia,
    AvisoCruce, AvisoCruceReferencia, AvisoCruceContenedor,
    ExpedienteExtraccion, EventoContenedor
)
from referencias.sync_views import _upsert_avisos_cruce, _recalcular_expedientes


class CruceSyncAndExpedienteTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.ref = Referencia.objects.create(
            num_refe='LCLF0001/26',
            patente='1627',
            prefijo='LCLF',
        )
        self.cont = Contenedor.objects.create(
            referencia=self.ref,
            num_cont='MSCU1234567',
            tipo='40HC',
        )

    def test_upsert_avisos_cruce_and_recalcular_expediente_completo(self):
        ahora = timezone.now()
        futuro = ahora + timedelta(days=2)
        payload = [{
            'id_cruce': 1001,
            'acuse_avc': 'AVC51040820220000001001',
            'patente': '1627',
            'fecha_emision': ahora.isoformat(),
            'fecha_vigencia': futuro.isoformat(),
            'tag': 'TAG123',
            'gafete': 'GAF999',
            'placa': 'PLACA1',
            'referencias': [
                {'cons_id': 1, 'num_refe': 'LCLF0001/26', 'num_pedimento': '6001234', 'rfc_importador': 'ABC010101XYZ'}
            ],
            'contenedores': [
                {'cons_cont': 1, 'num_cont': 'MSCU1234567', 'candado': 'SELLO99'}
            ]
        }]
        stats = {'creadas': 0, 'actualizadas': 0, 'errores': 0}
        error_msgs = []
        _upsert_avisos_cruce(payload, stats, error_msgs)

        aviso = AvisoCruce.objects.get(id_cruce=1001)
        self.assertEqual(aviso.acuse_avc, 'AVC51040820220000001001')
        self.assertEqual(AvisoCruceReferencia.objects.filter(aviso=aviso).count(), 1)
        self.assertEqual(AvisoCruceContenedor.objects.filter(aviso=aviso).count(), 1)

        doda = Doda.objects.create(
            id_doda=5001,
            num_doda='DODA-KASU-01',
            patente='1627',
            cve_caat='3B74',
            fecha_doda=ahora,
        )
        DodaReferencia.objects.create(
            doda=doda,
            referencia=self.ref,
            num_refe='LCLF0001/26',
            cons_id=1,
        )

        _recalcular_expedientes()

        exp = ExpedienteExtraccion.objects.get(clave='LCLF0001/26:MSCU1234567')
        self.assertEqual(exp.estado_documental, 'COMPLETO')
        self.assertTrue(exp.es_kasu)
        self.assertEqual(exp.rfc_importador, 'ABC010101XYZ')
        self.assertIsNotNone(exp.doda)
        self.assertIsNotNone(exp.aviso_contenedor)

    def test_expediente_falta_avc_y_falta_doda(self):
        ahora = timezone.now()
        doda = Doda.objects.create(
            id_doda=5002,
            num_doda='DODA-KASU-02',
            patente='1627',
            cve_caat='3B74',
            fecha_doda=ahora,
        )
        DodaReferencia.objects.create(
            doda=doda,
            referencia=self.ref,
            num_refe='LCLF0001/26',
            cons_id=1,
        )
        _recalcular_expedientes()
        exp = ExpedienteExtraccion.objects.get(clave='LCLF0001/26:MSCU1234567')
        self.assertEqual(exp.estado_documental, 'FALTA_AVC')

    def test_expediente_avc_vencido(self):
        ahora = timezone.now()
        pasado = ahora - timedelta(days=2)
        payload = [{
            'id_cruce': 1002,
            'acuse_avc': 'AVC-VENCIDO',
            'patente': '1627',
            'fecha_emision': (ahora - timedelta(days=5)).isoformat(),
            'fecha_vigencia': pasado.isoformat(),
            'placa': 'PLVENC',
            'referencias': [{'cons_id': 1, 'num_refe': 'LCLF0001/26'}],
            'contenedores': [{'cons_cont': 1, 'num_cont': 'MSCU1234567', 'candado': 'CANDV'}],
        }]
        _upsert_avisos_cruce(payload, {'creadas': 0, 'actualizadas': 0, 'errores': 0}, [])
        _recalcular_expedientes()
        exp = ExpedienteExtraccion.objects.get(clave='LCLF0001/26:MSCU1234567')
        self.assertEqual(exp.estado_documental, 'AVC_VENCIDO')


@override_settings(SYNC_SECRET_KEY='test-secret-token')
class EventosContenedorApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.ref = Referencia.objects.create(num_refe='LCLF0002/26', patente='1627', prefijo='LCLF')
        self.cont = Contenedor.objects.create(referencia=self.ref, num_cont='MSCU7654321', tipo='20DC')
        self.exp = ExpedienteExtraccion.objects.create(
            clave='LCLF0002/26:MSCU7654321',
            referencia=self.ref,
            contenedor=self.cont,
            estado_documental='FALTA_AVC',
        )
        self.token = 'test-secret-token'

    def test_eventos_api_token_invalido(self):
        url = reverse('api_eventos_contenedor')
        resp = self.client.post(
            url,
            data=json.dumps({}),
            content_type='application/json',
            HTTP_AUTHORIZATION='Token wrong'
        )
        self.assertEqual(resp.status_code, 401)

    def test_eventos_api_expediente_no_encontrado(self):
        url = reverse('api_eventos_contenedor')
        payload = {
            'evento_id': 'evt:001',
            'clave_expediente': 'NOEXISTE:CONT',
            'tipo': 'extraccion.asignada',
            'ocurrido_en': '2026-11-25T10:00:00Z',
            'datos': {}
        }
        resp = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )
        self.assertEqual(resp.status_code, 404)

    def test_eventos_api_crear_y_idempotencia(self):
        url = reverse('api_eventos_contenedor')
        payload = {
            'evento_id': 'evt:002',
            'clave_expediente': 'LCLF0002/26:MSCU7654321',
            'tipo': 'extraccion.asignada',
            'ocurrido_en': '2026-11-25T10:00:00Z',
            'datos': {'unidad': 'ECO 21'}
        }
        # Primer envío -> creado (200)
        resp1 = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )
        self.assertEqual(resp1.status_code, 200)
        data1 = resp1.json()
        self.assertEqual(data1.get('status'), 'ok')
        self.assertNotIn('duplicado', data1)

        self.assertEqual(EventoContenedor.objects.filter(evento_id='evt:002').count(), 1)

        # Segundo envío con mismo evento_id -> duplicado idéntico (200)
        resp2 = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.token}'
        )
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertTrue(data2.get('duplicado'))
        self.assertEqual(EventoContenedor.objects.filter(evento_id='evt:002').count(), 1)
