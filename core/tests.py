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
