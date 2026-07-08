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
