from django.db import models

TIPO_CHOICES = [('semanal', 'Semanal'), ('mensual', 'Mensual')]


class Destinatario(models.Model):
    nombre = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    activo = models.BooleanField(default=True)
    recibe_semanal = models.BooleanField(default=True)
    recibe_mensual = models.BooleanField(default=True)
    # WhatsApp — número en formato internacional sin + (ej. 5217535342088)
    whatsapp = models.CharField(max_length=20, blank=True, help_text='Número internacional sin + (ej. 5217535342088)')
    recibe_wa_semanal = models.BooleanField(default=False, verbose_name='WA Semanal')
    recibe_wa_mensual = models.BooleanField(default=False, verbose_name='WA Mensual')
    # IA por módulo — reciben la interpretación IA del módulo correspondiente
    recibe_wa_ia_referencias   = models.BooleanField(default=False, verbose_name='WA IA Referencias')
    recibe_wa_ia_glosa         = models.BooleanField(default=False, verbose_name='WA IA Glosa')
    recibe_wa_ia_cuenta_gastos = models.BooleanField(default=False, verbose_name='WA IA Cuenta Gastos')
    notas = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = 'Destinatario'
        verbose_name_plural = 'Destinatarios'
        ordering = ['nombre']

    def __str__(self):
        estado = '' if self.activo else ' [inactivo]'
        return f'{self.nombre} <{self.email}>{estado}'


class HistorialReporte(models.Model):
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    fecha_envio = models.DateTimeField(auto_now_add=True)
    periodo_inicio = models.DateField()
    periodo_fin = models.DateField()
    destinatarios = models.TextField(help_text='JSON con lista de correos')
    exitoso = models.BooleanField(default=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ['-fecha_envio']
        verbose_name = 'Historial de Reporte'
        verbose_name_plural = 'Historial de Reportes'

    def __str__(self):
        estado = '✔' if self.exitoso else '✘'
        return f'{estado} {self.get_tipo_display()} · {self.fecha_envio.strftime("%d/%m/%Y %H:%M")}'
