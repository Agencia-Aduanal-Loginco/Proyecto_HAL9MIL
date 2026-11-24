from django.test import TestCase
from unittest.mock import patch
from django.test import override_settings
from django.urls import reverse
from django.contrib.auth.models import User, Group
from django.core.files.uploadedfile import SimpleUploadedFile
from glosa.models import DocumentoReferencia, GlosaAutomaticaResultado, GlosaEvento
from glosa.permisos import es_glosador
from glosa.services_correccion import reemplazar_documento, recalcular_glosa, GlosaBloqueada
from referencias.models import Referencia

@override_settings(IA_HABILITADA=False, ANTHROPIC_API_KEY='')
class GlosaCorreccionesModelAndServiceTest(TestCase):
    def test_es_glosador(self):
        user = User.objects.create_user(username='testglosador', password='pw')
        self.assertFalse(es_glosador(user))
        grupo, _ = Group.objects.get_or_create(name='Glosador')
        user.groups.add(grupo)
        self.assertTrue(es_glosador(user))

    def test_glosa_evento_creation(self):
        ref = Referencia.objects.create(num_refe="LCRR3940/26", patente="1656")
        evento = GlosaEvento.objects.create(
            referencia=ref,
            tipo='SUBIDA',
            estado_resultante='PENDIENTE'
        )
        self.assertEqual(evento.tipo, 'SUBIDA')
        self.assertEqual(evento.estado_resultante, 'PENDIENTE')

    def test_reemplazar_documento_archiva_anterior(self):
        ref = Referencia.objects.create(num_refe="LCRR3940/26", patente="1656")
        user = User.objects.create_user(username='cap', password='pw')
        doc1 = DocumentoReferencia.objects.create(
            referencia=ref, tipo_documento='FACTURA', activo=True,
            archivo='glosa_documentos/fact1.pdf'
        )
        f = SimpleUploadedFile("fact2.pdf", b'%PDF-1.4 PDF NUEVO', content_type="application/pdf")
        doc2 = reemplazar_documento(doc1, f, user, motivo='Correccion importe')

        doc1.refresh_from_db()
        self.assertFalse(doc1.activo)
        self.assertEqual(doc1.reemplazado_por, doc2)
        self.assertTrue(doc2.activo)
        self.assertEqual(doc2.subido_por, user)
        self.assertEqual(doc2.motivo_reemplazo, '')
        self.assertEqual(doc1.motivo_reemplazo, 'Correccion importe')
        self.assertEqual(GlosaEvento.objects.filter(tipo='REEMPLAZO').count(), 1)

    def test_reemplazar_documento_bloqueado_si_aprobado(self):
        ref = Referencia.objects.create(num_refe="LCRR3940/26", patente="1656")
        user = User.objects.create_user(username='cap', password='pw')
        doc1 = DocumentoReferencia.objects.create(
            referencia=ref, tipo_documento='FACTURA', activo=True,
            archivo='glosa_documentos/fact1.pdf'
        )
        GlosaAutomaticaResultado.objects.create(
            referencia=ref, estado_revision='APROBADO'
        )
        f = SimpleUploadedFile("fact2.pdf", b'%PDF-1.4 PDF NUEVO', content_type="application/pdf")
        with self.assertRaises(GlosaBloqueada):
            reemplazar_documento(doc1, f, user, motivo='Intento de reemplazo')

    def test_single_active_preforma(self):
        ref = Referencia.objects.create(num_refe="LCRR3940/26", patente="1656")
        user = User.objects.create_user(username='cap', password='pw')
        pre1 = DocumentoReferencia.objects.create(
            referencia=ref, tipo_documento='PREFORMA', activo=True,
            archivo='glosa_documentos/pre1.pdf'
        )
        f = SimpleUploadedFile("pre2.pdf", b'%PDF-1.4 PREFORMA NUEVA', content_type="application/pdf")
        pre2 = reemplazar_documento(pre1, f, user, motivo='Nueva preforma')

        pre1.refresh_from_db()
        self.assertFalse(pre1.activo)
        self.assertTrue(pre2.activo)
        self.assertEqual(
            DocumentoReferencia.objects.filter(referencia=ref, tipo_documento='PREFORMA', activo=True).count(),
            1
        )

    def test_rechazado_state_behavior(self):
        ref = Referencia.objects.create(num_refe="LCRR3940/26", patente="1656")
        user = User.objects.create_user(username='cap', password='pw')
        resultado = GlosaAutomaticaResultado.objects.create(
            referencia=ref, estado_revision='RECHAZADO'
        )
        # Recalcular sin cambio de documento debe mantener rechazado
        recalcular_glosa(ref, user, evento_origen='CRUCE')
        resultado.refresh_from_db()
        self.assertEqual(resultado.estado_revision, 'RECHAZADO')

        # Recalcular con evento de subida o reemplazo debe permitir cambiar estado
        doc1 = DocumentoReferencia.objects.create(
            referencia=ref, tipo_documento='FACTURA', activo=True,
            archivo='glosa_documentos/fact1.pdf'
        )
        recalcular_glosa(ref, user, evento_origen='SUBIDA', doc_afectado=doc1)
        resultado.refresh_from_db()
        self.assertNotEqual(resultado.estado_revision, 'RECHAZADO')


@override_settings(IA_HABILITADA=False, ANTHROPIC_API_KEY='')
class GlosaTransicionesRegresionTest(TestCase):
    """Regresiones: el cruce limpio no aprueba solo, RECHAZADO sale al corregir,
    la subida normal respeta una sola pre-forma y el bloqueo de APROBADO."""

    def setUp(self):
        self.ref = Referencia.objects.create(num_refe="LCRR3940/26", patente="1656", num_pedimento="1234567")
        self.user = User.objects.create_user(username='cap', password='pw')
        self.limpio = {'estado': 'EN_REVISION', 'discrepancias': []}

    def _factura(self):
        return DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='FACTURA', archivo='glosa_documentos/f.pdf'
        )

    def test_cruce_real_limpio_queda_en_revision(self):
        DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='PREFORMA', archivo='glosa_documentos/p.pdf',
            datos_extraidos={'num_refe': 'LCRR3940/26'},
        )
        resultado = recalcular_glosa(self.ref, self.user, evento_origen='SUBIDA')
        self.assertEqual(resultado.estado_revision, 'EN_REVISION')

    def test_rechazado_sale_al_reemplazar(self):
        doc = self._factura()
        GlosaAutomaticaResultado.objects.create(referencia=self.ref, estado_revision='RECHAZADO')
        f = SimpleUploadedFile("f2.pdf", b'%PDF-1.4 X', content_type="application/pdf")
        with patch('glosa.services_correccion.comparar_preforma_documentos', return_value=self.limpio):
            reemplazar_documento(doc, f, self.user)
        self.assertEqual(
            GlosaAutomaticaResultado.objects.get(referencia=self.ref).estado_revision, 'EN_REVISION'
        )
        self.assertEqual(GlosaEvento.objects.filter(tipo='REEMPLAZO').count(), 1)
        self.assertEqual(GlosaEvento.objects.filter(tipo='CRUCE').count(), 1)

    def test_rechazado_sin_correccion_se_mantiene(self):
        GlosaAutomaticaResultado.objects.create(referencia=self.ref, estado_revision='RECHAZADO')
        with patch('glosa.services_correccion.comparar_preforma_documentos', return_value=self.limpio):
            recalcular_glosa(self.ref, self.user, evento_origen='CRUCE')
        self.assertEqual(
            GlosaAutomaticaResultado.objects.get(referencia=self.ref).estado_revision, 'RECHAZADO'
        )

    def test_subida_normal_de_preforma_archiva_la_anterior(self):
        pre1 = DocumentoReferencia.objects.create(
            referencia=self.ref, tipo_documento='PREFORMA', archivo='glosa_documentos/p1.pdf'
        )
        self.client.force_login(self.user)
        f = SimpleUploadedFile("p2.pdf", b'%PDF-1.4 PREFORMA', content_type="application/pdf")
        self.client.post(
            reverse('glosa:glosa_ia_detalle', args=[self.ref.num_refe]),
            {'archivo': f, 'tipo_documento': 'PREFORMA'},
        )
        pre1.refresh_from_db()
        activas = DocumentoReferencia.objects.filter(referencia=self.ref, tipo_documento='PREFORMA', activo=True)
        self.assertEqual(activas.count(), 1)
        self.assertFalse(pre1.activo)
        self.assertEqual(pre1.reemplazado_por, activas.get())
        self.assertEqual(GlosaEvento.objects.filter(tipo='SUBIDA').count(), 1)

    def test_aprobado_bloquea_subida_y_cambio_de_tipo(self):
        doc = self._factura()
        GlosaAutomaticaResultado.objects.create(referencia=self.ref, estado_revision='APROBADO')
        self.client.force_login(self.user)
        f = SimpleUploadedFile("x.pdf", b'%PDF-1.4 X', content_type="application/pdf")
        self.client.post(
            reverse('glosa:glosa_ia_detalle', args=[self.ref.num_refe]),
            {'archivo': f, 'tipo_documento': 'FACTURA'},
        )
        self.client.post(reverse('glosa:documento_actualizar', args=[doc.id]), {'tipo_documento': 'BL'})
        doc.refresh_from_db()
        self.assertEqual(DocumentoReferencia.objects.filter(referencia=self.ref).count(), 1)
        self.assertEqual(doc.tipo_documento, 'FACTURA')

    def test_aprobar_solo_desde_en_revision(self):
        from django.contrib.auth.models import Group
        grupo, _ = Group.objects.get_or_create(name='Glosador')
        self.user.groups.add(grupo)
        GlosaAutomaticaResultado.objects.create(referencia=self.ref, estado_revision='RECHAZADO')
        self.client.force_login(self.user)
        with patch('glosa.views.comparar_preforma_documentos', return_value=self.limpio):
            self.client.post(reverse('glosa:glosa_aprobar', args=[self.ref.num_refe]))
        self.assertEqual(
            GlosaAutomaticaResultado.objects.get(referencia=self.ref).estado_revision, 'RECHAZADO'
        )
