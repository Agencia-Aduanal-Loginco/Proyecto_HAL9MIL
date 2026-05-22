from django.db import models


class WhatsAppLog(models.Model):
    DIRECCION_CHOICES = [('out', 'Saliente'), ('in', 'Entrante')]

    direccion      = models.CharField(max_length=3, choices=DIRECCION_CHOICES)
    chat_id        = models.CharField(max_length=50)
    mensaje        = models.TextField()
    estado         = models.CharField(max_length=20, default='sent')
    timestamp      = models.DateTimeField(auto_now_add=True)
    referencia_bot = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.get_direccion_display()} → {self.chat_id} ({self.timestamp:%d-%b %H:%M})"
