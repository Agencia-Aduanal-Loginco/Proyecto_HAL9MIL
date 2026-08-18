from django.contrib.auth.models import AnonymousUser, Group, User
from django.test import TestCase

from core.permisos import usuario_tiene_modulo


class UsuarioTieneModuloTests(TestCase):
    def setUp(self):
        self.grupo_finanzas, _ = Group.objects.get_or_create(name='Finanzas')
        self.usuario_con_grupo = User.objects.create_user('con_grupo', password='x')
        self.usuario_con_grupo.groups.add(self.grupo_finanzas)
        self.usuario_sin_grupo = User.objects.create_user('sin_grupo', password='x')
        self.superusuario = User.objects.create_superuser(
            'admin_test', email='admin_test@example.com', password='x'
        )

    def test_superusuario_siempre_tiene_acceso(self):
        self.assertTrue(usuario_tiene_modulo(self.superusuario, 'Finanzas'))

    def test_usuario_en_el_grupo_tiene_acceso(self):
        self.assertTrue(usuario_tiene_modulo(self.usuario_con_grupo, 'Finanzas'))

    def test_usuario_fuera_del_grupo_no_tiene_acceso(self):
        self.assertFalse(usuario_tiene_modulo(self.usuario_sin_grupo, 'Finanzas'))

    def test_usuario_anonimo_no_tiene_acceso(self):
        self.assertFalse(usuario_tiene_modulo(AnonymousUser(), 'Finanzas'))


from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory

from core.permisos import modulo_required


def _agregar_middleware(request):
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)


@modulo_required('Finanzas')
def _vista_de_prueba(request):
    return HttpResponse('ok')


class ModuloRequiredTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.grupo_finanzas, _ = Group.objects.get_or_create(name='Finanzas')
        self.usuario_con_grupo = User.objects.create_user('con_grupo2', password='x')
        self.usuario_con_grupo.groups.add(self.grupo_finanzas)
        self.usuario_sin_grupo = User.objects.create_user('sin_grupo2', password='x')

    def test_usuario_con_grupo_accede_a_la_vista(self):
        request = self.factory.get('/protegida/')
        request.user = self.usuario_con_grupo
        _agregar_middleware(request)
        response = _vista_de_prueba(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')

    def test_usuario_sin_grupo_es_redirigido_al_dashboard(self):
        request = self.factory.get('/protegida/')
        request.user = self.usuario_sin_grupo
        _agregar_middleware(request)
        response = _vista_de_prueba(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/')

    def test_usuario_anonimo_es_redirigido_al_login(self):
        request = self.factory.get('/protegida/')
        request.user = AnonymousUser()
        _agregar_middleware(request)
        response = _vista_de_prueba(request)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/login/'))


class GrupoFinanzasMigrationTests(TestCase):
    def test_grupo_finanzas_existe_tras_migrar(self):
        self.assertTrue(Group.objects.filter(name='Finanzas').exists())


from django.template import Context, Template


class TieneModuloTemplateTagTests(TestCase):
    def setUp(self):
        self.grupo_finanzas, _ = Group.objects.get_or_create(name='Finanzas')
        self.usuario_con_grupo = User.objects.create_user('con_grupo3', password='x')
        self.usuario_con_grupo.groups.add(self.grupo_finanzas)
        self.usuario_sin_grupo = User.objects.create_user('sin_grupo3', password='x')

    def _renderizar(self, user):
        plantilla = Template(
            "{% load permisos_tags %}{% if user|tiene_modulo:'Finanzas' %}SI{% else %}NO{% endif %}"
        )
        return plantilla.render(Context({'user': user}))

    def test_filtro_devuelve_si_para_usuario_en_grupo(self):
        self.assertEqual(self._renderizar(self.usuario_con_grupo), 'SI')

    def test_filtro_devuelve_no_para_usuario_fuera_del_grupo(self):
        self.assertEqual(self._renderizar(self.usuario_sin_grupo), 'NO')


from django.test import override_settings
from core.models import PerfilUsuario
from core.capturistas import resolver_destinatario


class ResolverDestinatarioTests(TestCase):
    def setUp(self):
        """Set up test users and profiles for resolver_destinatario tests."""
        self.user1 = User.objects.create_user(
            username='capturista1',
            email='capturista1@example.com',
            first_name='Juan',
            last_name='Pérez'
        )
        # User without first/last name
        self.user3 = User.objects.create_user(
            username='capturista3',
            email='capturista3@example.com',
        )

    def test_resolver_existente_con_email_alterno(self):
        """Test resolver_destinatario returns email_alterno when set."""
        PerfilUsuario.objects.create(
            user=self.user1,
            cve_capturista='CAPT001',
            email_alterno='alterno@example.com'
        )
        email, nombre = resolver_destinatario('CAPT001')
        self.assertEqual(email, 'alterno@example.com')
        self.assertEqual(nombre, 'Juan Pérez')

    def test_resolver_existente_sin_email_alterno(self):
        """Test resolver_destinatario uses user.email when email_alterno is empty."""
        PerfilUsuario.objects.create(
            user=self.user1,
            cve_capturista='CAPT002',
            email_alterno=''
        )
        email, nombre = resolver_destinatario('CAPT002')
        self.assertEqual(email, 'capturista1@example.com')
        self.assertEqual(nombre, 'Juan Pérez')

    def test_resolver_existente_sin_nombre_completo(self):
        """Test resolver_destinatario falls back to username when get_full_name is empty."""
        PerfilUsuario.objects.create(
            user=self.user3,
            cve_capturista='CAPT003',
        )
        email, nombre = resolver_destinatario('CAPT003')
        self.assertEqual(email, 'capturista3@example.com')
        self.assertEqual(nombre, 'capturista3')

    @override_settings(MODULACION_FALLBACK_EMAILS=[])
    def test_resolver_no_existe_sin_fallback(self):
        """Test resolver_destinatario returns None when PerfilUsuario doesn't exist and no fallback."""
        with self.assertLogs('core.capturistas', level='WARNING') as log:
            result = resolver_destinatario('CAPT_INEXISTENTE')
            self.assertIsNone(result)
            # Check that a warning was logged
            self.assertTrue(any('CAPT_INEXISTENTE' in message for message in log.output))

    @override_settings(MODULACION_FALLBACK_EMAILS=['fallback@example.com', 'fallback2@example.com'])
    def test_resolver_no_existe_con_fallback(self):
        """Test resolver_destinatario returns fallback when PerfilUsuario doesn't exist."""
        with self.assertLogs('core.capturistas', level='WARNING'):
            email, nombre = resolver_destinatario('CAPT_INEXISTENTE')
            self.assertEqual(email, 'fallback@example.com')
            self.assertEqual(nombre, 'CAPT_INEXISTENTE')
