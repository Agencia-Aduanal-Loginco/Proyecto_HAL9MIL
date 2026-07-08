from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse


class AccesoFinanzasTests(TestCase):
    def setUp(self):
        self.grupo_finanzas, _ = Group.objects.get_or_create(name='Finanzas')
        self.usuario_con_grupo = User.objects.create_user('fin_con_grupo', password='x')
        self.usuario_con_grupo.groups.add(self.grupo_finanzas)
        self.usuario_sin_grupo = User.objects.create_user('fin_sin_grupo', password='x')
        self.superusuario = User.objects.create_superuser(
            'fin_admin', email='fin_admin@example.com', password='x'
        )

    def test_usuario_sin_grupo_no_accede_al_dashboard_de_finanzas(self):
        self.client.force_login(self.usuario_sin_grupo)
        response = self.client.get(reverse('finanzas:dashboard'))
        self.assertRedirects(response, reverse('dashboard'))

    def test_usuario_con_grupo_accede_al_dashboard_de_finanzas(self):
        self.client.force_login(self.usuario_con_grupo)
        response = self.client.get(reverse('finanzas:dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_superusuario_accede_sin_estar_en_el_grupo(self):
        self.client.force_login(self.superusuario)
        response = self.client.get(reverse('finanzas:dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_usuario_anonimo_es_redirigido_a_login(self):
        response = self.client.get(reverse('finanzas:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)
