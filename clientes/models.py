from django.db import models


class Cliente(models.Model):
    nombre_cliente    = models.CharField(max_length=255, unique=True)
    cve_cliente       = models.CharField(max_length=20, blank=True)
    rfc               = models.CharField(max_length=13, blank=True)
    email_cobranza    = models.EmailField(blank=True)
    email_cobranza_cc = models.EmailField(blank=True)

    class Meta:
        ordering        = ['nombre_cliente']
        verbose_name    = 'Cliente'
        verbose_name_plural = 'Clientes'

    def __str__(self):
        return self.nombre_cliente
