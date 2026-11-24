import os
from unittest.mock import patch
import fitz

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User, Group
from referencias.models import Referencia, GlosaRegistro
from glosa.models import DocumentoReferencia, GlosaAutomaticaResultado


# Sin API key: las pruebas de vistas nunca deben llamar a Jev real aunque
# el .env de desarrollo tenga JEV_AI_API_KEY.
@patch.dict(os.environ, {'JEV_AI_API_KEY': '', 'TYPESAFE_API_KEY': ''})
@override_settings(IA_HABILITADA=False, ANTHROPIC_API_KEY='')
class GlosaViewsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='admin', password='password')
        self.client = Client()
        self.client.force_login(self.user)

    def test_glosa_view_status(self):
        ref = Referencia.objects.create(num_refe="LCRR8888/26", num_pedimento="7654321")
        url = reverse('glosa:glosa_ia_detalle', args=[ref.num_refe])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_detalle_muestra_discrepancias_sin_fila_en_tabla(self):
        ref = Referencia.objects.create(num_refe="LCRR6666/26", num_pedimento="7654321")
        GlosaAutomaticaResultado.objects.create(
            referencia=ref,
            estado_revision='DISCREPANCIAS_DETECTADAS',
            datos_comparacion={
                'version': 2, 'estado': 'DISCREPANCIAS_DETECTADAS', 'bloqueante': None,
                'reglas': [],
                'discrepancias': ['ED declarado sin documento en el expediente: 0170261GUVAM8'],
            },
        )
        response = self.client.get(reverse('glosa:glosa_ia_detalle', args=[ref.num_refe]))
        self.assertContains(response, 'Discrepancias detectadas (1)')
        self.assertContains(response, '0170261GUVAM8')

    def test_detalle_muestra_tabla_cruce_por_partida(self):
        ref = Referencia.objects.create(num_refe="LCRR4444/26", num_pedimento="7654321")
        GlosaAutomaticaResultado.objects.create(
            referencia=ref,
            estado_revision='DISCREPANCIAS_DETECTADAS',
            datos_comparacion={
                'version': 2, 'estado': 'DISCREPANCIAS_DETECTADAS', 'bloqueante': None,
                'reglas': [{'campo': 'peso_bruto_kg', 'estado': 'OK', 'valor_consenso': '1', 'fuentes': [], 'mensaje': ''}],
                'discrepancias': [], 'advertencias': [],
                'partidas': [{
                    'sec': '001', 'descripcion': 'JUEGO DE CHUMACERAS', 'fraccion': '98020015', 'modelo': '',
                    'cantidad_umc': '9700.000', 'grupo': '001, 003',
                    'factura': {'valor': '12620', 'estado': 'OK', 'preforma': '12620'},
                    'lista_empaque': {'valor': None, 'estado': 'SIN'},
                    'importe': {'preforma': '43272.25', 'valor': '43272.28', 'estado': 'OK'},
                    'nom': None,
                    'permiso': {'valor': '9901R826078685', 'estado': 'DISC'},
                    'estado': 'DISCREPANCIA',
                }],
            },
        )
        response = self.client.get(reverse('glosa:glosa_ia_detalle', args=[ref.num_refe]))
        self.assertContains(response, 'Cruce por partida (1)')
        self.assertContains(response, 'Grupo: 001, 003')
        self.assertContains(response, '9901R826078685')
        self.assertContains(response, 'SIN PAREJA')
        self.assertContains(response, '43272.25')

    def test_detalle_muestra_advertencias_ed_en_amarillo(self):
        ref = Referencia.objects.create(num_refe="LCRR5555/26", num_pedimento="7654321")
        GlosaAutomaticaResultado.objects.create(
            referencia=ref,
            estado_revision='EN_REVISION',
            datos_comparacion={
                'version': 2, 'estado': 'EN_REVISION', 'bloqueante': None,
                'reglas': [], 'discrepancias': [],
                'advertencias': [{
                    'codigo': 'ED_SIN_DOCUMENTO', 'severidad': 'ADVERTENCIA',
                    'mensaje': 'ED declarado sin documento en el expediente: 0170261GUVAM8',
                }],
            },
        )
        response = self.client.get(reverse('glosa:glosa_ia_detalle', args=[ref.num_refe]))
        self.assertContains(response, 'Advertencias (1)')
        self.assertContains(response, 'bg-yellow-50')
        self.assertNotContains(response, 'Discrepancias detectadas (')

    def test_glosa_view_get_and_post(self):
        ref = Referencia.objects.create(num_refe="LCRR7777/26", num_pedimento="1234567")
        url = reverse('glosa:glosa_ia_detalle', args=[ref.num_refe])

        # Test GET
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Test POST file upload
        pdf_file = SimpleUploadedFile("factura.pdf", b"BILL OF LADING content", content_type="application/pdf")
        response = self.client.post(url, {'archivo': pdf_file})
        self.assertEqual(response.status_code, 302) # Redirects back
        self.assertEqual(DocumentoReferencia.objects.filter(referencia=ref).count(), 1)


@patch.dict(os.environ, {'JEV_AI_API_KEY': '', 'TYPESAFE_API_KEY': ''})
@override_settings(IA_HABILITADA=False, ANTHROPIC_API_KEY='')
class GlosaViewsGlosadorTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user_glosador = User.objects.create_user(username='glos', password='pw')
        grupo, _ = Group.objects.get_or_create(name='Glosador')
        self.user_glosador.groups.add(grupo)

        self.user_normal = User.objects.create_user(username='norm', password='pw')
        self.ref = Referencia.objects.create(num_refe="LCRR3940/26", patente="1656", num_pedimento="1234567")
        self.resultado = GlosaAutomaticaResultado.objects.create(
            referencia=self.ref,
            estado_revision='EN_REVISION',
            datos_comparacion={'estado': 'EN_REVISION', 'discrepancias': []}
        )

    def test_aprobar_requiere_grupo_glosador(self):
        url = reverse('glosa:glosa_aprobar', args=[self.ref.num_refe])

        # Usuario normal sin grupo Glosador
        self.client.force_login(self.user_normal)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.resultado.refresh_from_db()
        self.assertEqual(self.resultado.estado_revision, 'EN_REVISION')

        # Usuario con grupo Glosador
        self.client.force_login(self.user_glosador)
        with patch('glosa.views.comparar_preforma_documentos', return_value={'estado': 'EN_REVISION', 'discrepancias': []}):
            response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.resultado.refresh_from_db()
        self.assertEqual(self.resultado.estado_revision, 'APROBADO')
        self.assertEqual(self.resultado.aprobado_por, self.user_glosador)

    def test_aprobar_concluye_glosa_registro(self):
        self.client.force_login(self.user_glosador)
        from django.utils import timezone
        reg = GlosaRegistro.objects.create(
            referencia=self.ref,
            fecha_entrada=timezone.now(),
            usuario_entrada=self.user_normal
        )
        url = reverse('glosa:glosa_aprobar', args=[self.ref.num_refe])
        with patch('glosa.views.comparar_preforma_documentos', return_value={'estado': 'EN_REVISION', 'discrepancias': []}):
            response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        reg.refresh_from_db()
        self.assertTrue(reg.concluida)
        self.assertEqual(reg.usuario_conclusion, self.user_glosador)
        self.assertIn("Visto bueno glosa IA", reg.nota)

    def test_rechazar_requiere_comentario_y_permiso(self):
        url = reverse('glosa:glosa_rechazar', args=[self.ref.num_refe])

        # Sin permiso
        self.client.force_login(self.user_normal)
        response = self.client.post(url, {'comentario': 'Rechazado'})
        self.assertEqual(response.status_code, 302)

        # Con permiso pero sin comentario
        self.client.force_login(self.user_glosador)
        response = self.client.post(url, {'comentario': ''})
        self.assertEqual(response.status_code, 302)

        # Con permiso y comentario
        response = self.client.post(url, {'comentario': 'Falta factura de proveedor'})
        self.assertEqual(response.status_code, 302)
        self.resultado.refresh_from_db()
        self.assertEqual(self.resultado.estado_revision, 'RECHAZADO')
        self.assertEqual(self.resultado.comentario_glosador, 'Falta factura de proveedor')

    def test_reabrir_requiere_comentario_y_permiso(self):
        self.resultado.estado_revision = 'APROBADO'
        self.resultado.aprobado_por = self.user_glosador
        self.resultado.save()
        url = reverse('glosa:glosa_reabrir', args=[self.ref.num_refe])

        # Sin permiso
        self.client.force_login(self.user_normal)
        response = self.client.post(url, {'comentario': 'Reabrir'})
        self.assertEqual(response.status_code, 302)

        # Con permiso pero sin comentario
        self.client.force_login(self.user_glosador)
        response = self.client.post(url, {'comentario': ''})
        self.assertEqual(response.status_code, 302)

        # Con permiso y comentario
        response = self.client.post(url, {'comentario': 'Reabierto por discrepancia detectada'})
        self.assertEqual(response.status_code, 302)
        self.resultado.refresh_from_db()
        self.assertEqual(self.resultado.estado_revision, 'RECHAZADO')
        self.assertIsNone(self.resultado.aprobado_por)
        self.assertEqual(self.resultado.comentario_glosador, 'Reabierto por discrepancia detectada')

    def test_documento_reemplazar_y_preforma_anotada(self):
        self.client.force_login(self.user_glosador)
        self.resultado.estado_revision = 'EN_REVISION'
        self.resultado.save()

        f_pdf = fitz.open()
        f_page = f_pdf.new_page()
        f_page.insert_text((50, 50), "PREFORMA LCRR3940/26")
        pdf_content = f_pdf.tobytes()
        f_pdf.close()

        doc = DocumentoReferencia.objects.create(
            referencia=self.ref,
            tipo_documento='PREFORMA',
            activo=True,
            archivo=SimpleUploadedFile("preforma.pdf", pdf_content, content_type="application/pdf")
        )
        url_reemplazar = reverse('glosa:documento_reemplazar', args=[doc.pk])
        f2 = SimpleUploadedFile("preforma2.pdf", pdf_content, content_type="application/pdf")
        response = self.client.post(url_reemplazar, {'archivo': f2, 'motivo': 'Sustitucion'})
        self.assertEqual(response.status_code, 302)
        doc.refresh_from_db()
        self.assertFalse(doc.activo)

        # Test preforma anotada GET
        nuevo_doc = DocumentoReferencia.objects.filter(referencia=self.ref, tipo_documento='PREFORMA', activo=True).first()
        url_anotada = reverse('glosa:preforma_anotada', args=[nuevo_doc.pk])
        response = self.client.get(url_anotada)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')


class GlosaListaIndicadorIATest(TestCase):
    """Columna y filtro "Glosa IA" en la lista de glosa (referencias.views.glosa)."""

    def setUp(self):
        from django.utils import timezone
        from glosa.models import GlosaAutomaticaResultado

        self.user = User.objects.create_superuser(username='admin', password='password')
        self.client = Client()
        self.client.force_login(self.user)
        hoy = timezone.localdate()

        def ref(num):
            return Referencia.objects.create(num_refe=num, num_pedimento='1', fecha_arribo=hoy)

        def doc(r, estado='OK'):
            return DocumentoReferencia.objects.create(
                referencia=r, archivo='glosa_documentos/x.pdf',
                tipo_documento='FACTURA', clasificacion_ia={'estado': estado},
            )

        # Sin documentos, aunque ya exista el resultado PENDIENTE por abrir la página.
        self.sin = ref('LCRR0001/26')
        GlosaAutomaticaResultado.objects.create(referencia=self.sin)
        # Con documentos, sin resultado de comparación.
        self.pendiente = ref('LCRR0002/26')
        doc(self.pendiente)
        doc(self.pendiente, estado='REVISION')
        self.aprobada = ref('LCRR0003/26')
        doc(self.aprobada)
        GlosaAutomaticaResultado.objects.create(referencia=self.aprobada, estado_revision='APROBADO')
        self.discrepancias = ref('LCRR0004/26')
        doc(self.discrepancias)
        doc(self.discrepancias)
        doc(self.discrepancias)
        GlosaAutomaticaResultado.objects.create(
            referencia=self.discrepancias, estado_revision='DISCREPANCIAS_DETECTADAS'
        )

    def _refs(self, response):
        return {r.num_refe: r for r in response.context['page']}

    def test_estado_y_conteo_por_referencia(self):
        response = self.client.get(reverse('glosa'))
        self.assertEqual(response.status_code, 200)
        refs = self._refs(response)
        self.assertEqual(refs['LCRR0001/26'].glosa_ia_estado, 'sin')
        self.assertEqual(refs['LCRR0001/26'].glosa_ia_docs, 0)
        self.assertEqual(refs['LCRR0002/26'].glosa_ia_estado, 'pendiente')
        self.assertEqual(refs['LCRR0002/26'].glosa_ia_docs, 2)
        self.assertEqual(refs['LCRR0002/26'].glosa_ia_docs_revision, 1)
        self.assertEqual(refs['LCRR0003/26'].glosa_ia_estado, 'aprobada')
        self.assertEqual(refs['LCRR0004/26'].glosa_ia_estado, 'discrepancias')
        self.assertEqual(refs['LCRR0004/26'].glosa_ia_docs, 3)
        self.assertContains(response, reverse('glosa:glosa_ia_detalle', args=['LCRR0004/26']))
        self.assertContains(response, '3 docs')

    def test_kpis(self):
        response = self.client.get(reverse('glosa'))
        kpis = {k['clave']: k['total'] for k in response.context['glosa_ia_kpis']}
        self.assertEqual(kpis, {'sin': 1, 'pendiente': 1, 'revision': 0, 'aprobada': 1, 'discrepancias': 1})

    def test_filtro_ia(self):
        response = self.client.get(reverse('glosa'), {'ia': 'discrepancias'})
        self.assertEqual(list(self._refs(response)), ['LCRR0004/26'])
        self.assertEqual(response.context['total'], 1)
        # KPIs no cambian con el filtro
        kpis = {k['clave']: k['total'] for k in response.context['glosa_ia_kpis']}
        self.assertEqual(kpis['sin'], 1)

    def test_filtro_ia_invalido_se_ignora(self):
        response = self.client.get(reverse('glosa'), {'ia': 'xyz'})
        self.assertEqual(response.context['filtro_ia'], '')
        self.assertEqual(len(self._refs(response)), 4)

    def test_filtro_ia_con_busqueda(self):
        # La búsqueda usa joins + distinct(); el conteo de documentos no debe duplicarse.
        response = self.client.get(reverse('glosa'), {'q': 'LCRR0004', 'ia': 'discrepancias'})
        refs = self._refs(response)
        self.assertEqual(refs['LCRR0004/26'].glosa_ia_docs, 3)
