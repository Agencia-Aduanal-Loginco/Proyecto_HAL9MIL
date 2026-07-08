from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse


class SidebarFinanzasVisibilityTests(TestCase):
    def setUp(self):
        self.grupo_finanzas, _ = Group.objects.get_or_create(name='Finanzas')
        self.usuario_con_grupo = User.objects.create_user('side_con_grupo', password='x')
        self.usuario_con_grupo.groups.add(self.grupo_finanzas)
        self.usuario_sin_grupo = User.objects.create_user('side_sin_grupo', password='x')

    def test_usuario_con_grupo_ve_el_link_de_finanzas(self):
        self.client.force_login(self.usuario_con_grupo)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'href="/finanzas/"')

    def test_usuario_sin_grupo_no_ve_el_link_de_finanzas(self):
        self.client.force_login(self.usuario_sin_grupo)
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, 'href="/finanzas/"')
