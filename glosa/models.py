from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User

PROCESAMIENTO_PENDIENTE = 'PENDIENTE'
PROCESAMIENTO_PROCESANDO = 'PROCESANDO'
PROCESAMIENTO_LISTO = 'LISTO'
PROCESAMIENTO_ERROR = 'ERROR'
PROCESAMIENTO_CHOICES = [
    (PROCESAMIENTO_PENDIENTE, 'Pendiente'),
    (PROCESAMIENTO_PROCESANDO, 'Procesando'),
    (PROCESAMIENTO_LISTO, 'Listo'),
    (PROCESAMIENTO_ERROR, 'Error'),
]


class DocumentoReferencia(models.Model):
    TIPO_CHOICES = [
        ('FACTURA', 'Factura de Proveedor'),
        ('BL', 'Bill of Lading (B/L)'),
        ('CARTA_TECNICA', 'Carta Técnica'),
        ('LISTA_EMPAQUE', 'Lista de Empaque'),
        ('PERMISOS', 'Permisos / Regulaciones'),
        ('CERTIFICADOS', 'Certificados de Origen'),
        ('PREFORMA', 'Pre-forma de Pedimento'),
        ('OTROS', 'Otros Documentos'),
        ('OTRO_SIN_ESQUEMA', 'Otro sin esquema'),
    ]
    referencia = models.ForeignKey('referencias.Referencia', on_delete=models.PROTECT, related_name='documentos_glosa')
    archivo = models.FileField(upload_to='glosa_documentos/')
    tipo_documento = models.CharField(max_length=30, choices=TIPO_CHOICES, default='OTROS')
    confianza_ia = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    datos_extraidos = models.JSONField(default=dict, blank=True)
    clasificacion_ia = models.JSONField(default=dict, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    activo = models.BooleanField(default=True, db_index=True)
    reemplazado_por = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='versiones_anteriores')
    reemplazado_en = models.DateTimeField(null=True, blank=True)
    subido_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    motivo_reemplazo = models.TextField(blank=True)
    # SHA-256 del archivo: detecta duplicados y permite reutilizar la extracción.
    hash_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    # Clasificación y extracción corren en segundo plano (services_procesamiento).
    procesamiento_estado = models.CharField(
        max_length=12, choices=PROCESAMIENTO_CHOICES, default=PROCESAMIENTO_LISTO, db_index=True
    )
    procesamiento_inicio = models.DateTimeField(null=True, blank=True)
    procesamiento_error = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Documento de Referencia'
        verbose_name_plural = 'Documentos de Referencia'

    def __str__(self):
        return f'{self.referencia.num_refe} — {self.get_tipo_documento_display()}'

    @property
    def estado_clasificacion(self):
        return (self.clasificacion_ia or {}).get('estado', '')

    @property
    def requiere_revision(self):
        return self.estado_clasificacion in ('REVISION', 'SIN_IA', 'ERROR')

    @property
    def en_proceso(self):
        return self.procesamiento_estado in (PROCESAMIENTO_PENDIENTE, PROCESAMIENTO_PROCESANDO)


class GlosaAutomaticaResultado(models.Model):
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente'),
        ('EN_REVISION', 'En Revisión'),
        ('APROBADO', 'Aprobado'),
        ('RECHAZADO', 'Rechazado'),
        ('DISCREPANCIAS_DETECTADAS', 'Discrepancias Detectadas'),
    ]
    referencia = models.OneToOneField('referencias.Referencia', on_delete=models.PROTECT, related_name='resultado_glosa')
    estado_revision = models.CharField(max_length=30, choices=ESTADO_CHOICES, default='PENDIENTE')
    eta_aaalac = models.DateField(null=True, blank=True)
    datos_comparacion = models.JSONField(default=dict, blank=True)
    aprobado_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='glosas_aprobadas')
    aprobado_en = models.DateTimeField(null=True, blank=True)
    comentario_glosador = models.TextField(blank=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Resultado de Glosa Automática'
        verbose_name_plural = 'Resultados de Glosa Automática'

    def __str__(self):
        return f'{self.referencia.num_refe} — {self.get_estado_revision_display()}'


class GlosaEvento(models.Model):
    TIPO_CHOICES = [
        ('SUBIDA', 'Subida'),
        ('REEMPLAZO', 'Reemplazo'),
        ('CAMBIO_TIPO', 'Cambio de Tipo'),
        ('CRUCE', 'Cruce'),
        ('RECHAZO', 'Rechazo'),
        ('APROBACION', 'Aprobación'),
        ('REAPERTURA', 'Reapertura'),
    ]
    referencia = models.ForeignKey('referencias.Referencia', on_delete=models.PROTECT, related_name='eventos_glosa')
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES)
    usuario = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    creado_en = models.DateTimeField(auto_now_add=True)
    documento = models.ForeignKey(DocumentoReferencia, null=True, blank=True, on_delete=models.SET_NULL)
    documento_anterior = models.ForeignKey(DocumentoReferencia, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    estado_resultante = models.CharField(max_length=30)
    discrepancias = models.JSONField(default=list, blank=True)
    comentario = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Evento de Glosa'
        verbose_name_plural = 'Eventos de Glosa'
        ordering = ['-creado_en']

    def __str__(self):
        return f'{self.referencia.num_refe} — {self.get_tipo_display()} ({self.estado_resultante})'
