from django.db import models
from django.conf import settings


class PerfilUsuario(models.Model):
    user             = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                             related_name='perfil')
    cve_capturista   = models.CharField(max_length=20, unique=True, blank=True, db_index=True)
    email_alterno    = models.EmailField(blank=True)  # override opcional del email de User

    def __str__(self):
        return f'{self.user.username} ({self.cve_capturista})'
