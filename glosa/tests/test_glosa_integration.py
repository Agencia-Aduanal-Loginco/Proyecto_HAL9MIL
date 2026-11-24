import os
from unittest.mock import patch

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from decimal import Decimal
from referencias.models import Referencia
from glosa.models import DocumentoReferencia, GlosaAutomaticaResultado
from glosa.tests.test_extraccion import BL_CRUDO, cliente_falso
from glosa.tests.test_jev_ia import ENV_CON_JEV, TEXTO_BL, pdf_con_texto, respuesta_jev


@patch.dict(os.environ, {**ENV_CON_JEV, 'AAALAC_API_KEY': 'clave-prueba'})
@override_settings(IA_HABILITADA=True, ANTHROPIC_API_KEY='test-key')
class GlosaIntegrationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='admin', password='password')
        self.client = Client()
        self.client.force_login(self.user)

    @patch('glosa.views.obtener_eta_mercancia')
    @patch('glosa.services_extraccion._client')
    @patch('glosa.services_jev_ia.requests.post')
    def test_end_to_end_glosa_expediente(self, mock_post, mock_claude, mock_aaalac):
        mock_post.return_value = respuesta_jev('BL', prob=0.92, noul=0.95)
        mock_claude.return_value = cliente_falso(BL_CRUDO)
        mock_aaalac.return_value = {'fecha_llegada': '2026-11-20'}

        ref = Referencia.objects.create(
            num_refe="LCRR5555/26",
            num_pedimento="9876543",
            nombre_cliente="EMPRESA PRUEBA SA DE CV"
        )
        url = reverse('glosa:glosa_ia_detalle', args=[ref.num_refe])

        # 1. Access detail
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # 2. Upload: Jev clasifica, Claude extrae
        pdf_content = pdf_con_texto(TEXTO_BL * 3).getvalue()
        pdf_file = SimpleUploadedFile("bl_msc.pdf", pdf_content, content_type="application/pdf")
        response = self.client.post(url, {'archivo': pdf_file})
        self.assertEqual(response.status_code, 302)
        mock_post.assert_called_once()

        doc = DocumentoReferencia.objects.get(referencia=ref)
        self.assertEqual(doc.tipo_documento, 'BL')
        self.assertEqual(doc.confianza_ia, Decimal('0.9200'))
        self.assertEqual(doc.estado_clasificacion, 'OK')
        self.assertFalse(doc.requiere_revision)
        self.assertEqual(doc.datos_extraidos['buque'], 'MSC MARIA')
        self.assertEqual(doc.datos_extraidos['meta']['estado'], 'OK')

        # La vista muestra el documento con sus datos
        response = self.client.get(url)
        self.assertContains(response, 'Datos extraídos')

        # 3. AAALAC recibe buque y viaje leídos del B/L
        response = self.client.post(url, {'consultar_aaalac': '1'})
        self.assertEqual(response.status_code, 302)
        kwargs = mock_aaalac.call_args.kwargs
        self.assertEqual(kwargs['buque'], 'MSC MARIA')
        self.assertEqual(kwargs['viaje'], 'V202')
        resultado = GlosaAutomaticaResultado.objects.get(referencia=ref)
        self.assertEqual(str(resultado.eta_aaalac), '2026-11-20')

        # 4. Cambio manual a un tipo sin esquema limpia los datos extraídos
        llamadas_claude = mock_claude.return_value.messages.create.call_count
        response = self.client.post(
            reverse('glosa:documento_actualizar', args=[doc.pk]),
            {'tipo_documento': 'OTRO_SIN_ESQUEMA'},
        )
        self.assertEqual(response.status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.tipo_documento, 'OTRO_SIN_ESQUEMA')
        self.assertEqual(doc.clasificacion_ia, {'estado': 'MANUAL', 'sugerencia_ia': 'BL', 'tipo_ia': 'BL'})
        self.assertEqual(doc.datos_extraidos, {})

        # 5. Regresar a BL vuelve a extraer
        self.client.post(
            reverse('glosa:documento_actualizar', args=[doc.pk]),
            {'tipo_documento': 'BL'},
        )
        doc.refresh_from_db()
        self.assertEqual(doc.datos_extraidos['viaje'], 'V202')
        self.assertEqual(mock_claude.return_value.messages.create.call_count, llamadas_claude + 1)
