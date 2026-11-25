from django.contrib.auth.models import User
from decimal import Decimal
from django.db import models


PATENTE_PREFIJO = {
    '1627': 'LCLF',
    '1656': 'LCRR',
    '1927': 'LCMJ',
}

# Claves como str: SAAIO_CONTEN.CVE_CONT es VARCHAR(2) en CASA.GDB (Firebird)
# — fdb siempre lo devuelve como string, nunca como int. Usar claves int aquí
# hacía que CVE_CONT_TIPO.get(cve_cont, '') jamás matcheara (100% de los
# contenedores en producción quedaban con tipo='').
CVE_CONT_TIPO = {
    '1': '20DC', '2': '20RF', '3': '40HC', '4': '40RF',
    '9': '20TK', '11': '45HC', '16': '40OT', '17': '40OT',
    '20': '40FR', '25': '40FR',
}


class Referencia(models.Model):
    num_refe         = models.CharField(max_length=50, unique=True, db_index=True)
    patente          = models.CharField(max_length=10, db_index=True)
    prefijo          = models.CharField(max_length=10)
    cve_cliente      = models.CharField(max_length=20, blank=True)
    nombre_cliente   = models.CharField(max_length=255, blank=True, db_index=True)
    fecha_arribo     = models.DateField(null=True, blank=True)
    peso_bruto       = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)  # CTRAO_EMBAR.PES_BRUT (toneladas)

    @property
    def peso_kg(self):
        return self.peso_bruto * Decimal('1000') if self.peso_bruto is not None else None
    fecha_validacion = models.DateField(null=True, blank=True)
    fecha_pago       = models.DateField(null=True, blank=True, db_index=True)
    num_pedimento     = models.CharField(max_length=30, blank=True, db_index=True)
    clave_pedimento   = models.CharField(max_length=10, blank=True)
    tipo_pedimento    = models.CharField(max_length=10, blank=True)
    aduana            = models.CharField(max_length=10, blank=True)
    regimen           = models.CharField(max_length=10, blank=True)
    num_operacion     = models.CharField(max_length=100, blank=True)
    linea_captura     = models.CharField(max_length=30, blank=True)
    cve_capturista    = models.CharField(max_length=20, blank=True)
    nombre_capturista = models.CharField(max_length=150, blank=True)
    fir_elec          = models.CharField(max_length=255, blank=True)
    es_rectificacion  = models.BooleanField(default=False)
    num_partidas      = models.IntegerField(default=0)
    fecha_captura     = models.DateField(null=True, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha_pago', 'num_refe']
        indexes = [
            models.Index(fields=['fecha_pago', 'patente']),
            models.Index(fields=['patente', 'fecha_pago']),
        ]

    def __str__(self):
        return self.num_refe


class Contenedor(models.Model):
    referencia = models.ForeignKey(
        Referencia, on_delete=models.CASCADE, related_name='contenedores'
    )
    num_cont = models.CharField(max_length=20)
    tipo     = models.CharField(max_length=10, blank=True)

    class Meta:
        unique_together = [('referencia', 'num_cont')]

    def __str__(self):
        return f'{self.num_cont} ({self.tipo})'


class GuiaBL(models.Model):
    referencia  = models.ForeignKey(
        Referencia, on_delete=models.CASCADE, related_name='guias'
    )
    numero_guia = models.CharField(max_length=60)
    tipo_guia   = models.CharField(max_length=5, default='M')  # M=Master, H=House

    class Meta:
        unique_together = [('referencia', 'numero_guia')]

    def __str__(self):
        return self.numero_guia


class GlosaRegistro(models.Model):
    referencia         = models.ForeignKey(
        Referencia, on_delete=models.CASCADE, related_name='glosas'
    )
    fecha_entrada      = models.DateTimeField()
    usuario_entrada    = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='glosas_recibidas'
    )
    fecha_conclusion   = models.DateTimeField(null=True, blank=True)
    usuario_conclusion = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='glosas_concluidas'
    )
    nota    = models.TextField(blank=True)
    urgente = models.BooleanField(default=False)

    class Meta:
        ordering            = ['-fecha_entrada']
        verbose_name        = 'Registro de glosa'
        verbose_name_plural = 'Registros de glosa'

    def __str__(self):
        return f'{self.referencia} | {self.fecha_entrada:%Y-%m-%d %H:%M}'

    @property
    def concluida(self):
        return self.fecha_conclusion is not None


class CuentaGastos(models.Model):
    referencia         = models.OneToOneField(
        Referencia, on_delete=models.CASCADE, related_name='cuenta_gastos'
    )
    nota               = models.TextField(blank=True)
    fecha_finalizacion = models.DateTimeField()
    finalizado_por     = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='cuenta_gastos_finalizadas'
    )

    class Meta:
        ordering            = ['-fecha_finalizacion']
        verbose_name        = 'Cuenta de gastos'
        verbose_name_plural = 'Cuentas de gastos'

    def __str__(self):
        return f'{self.referencia} | {self.fecha_finalizacion:%Y-%m-%d %H:%M}'


class LogSync(models.Model):
    timestamp    = models.DateTimeField(auto_now_add=True)
    patente      = models.CharField(max_length=10, db_index=True)
    agent_id     = models.CharField(max_length=50, default='unknown')
    referencias  = models.IntegerField(default=0)
    contenedores = models.IntegerField(default=0)
    guias        = models.IntegerField(default=0)
    creadas      = models.IntegerField(default=0)
    actualizadas = models.IntegerField(default=0)
    exitoso      = models.BooleanField(default=True, db_index=True)
    error        = models.TextField(blank=True)
    duracion_seg = models.FloatField(default=0)

    class Meta:
        ordering = ['-timestamp']
        verbose_name      = 'Log de sincronización'
        verbose_name_plural = 'Logs de sincronización'

    def __str__(self):
        estado = '✔' if self.exitoso else '✘'
        return f'{self.timestamp:%Y-%m-%d %H:%M} | {self.patente} | {estado}'


class Doda(models.Model):
    id_doda        = models.IntegerField(unique=True, db_index=True)   # SAAIO_DODA.ID_DODA
    num_doda       = models.CharField(max_length=34, blank=True)
    patente        = models.CharField(max_length=10, db_index=True)
    cve_caat       = models.CharField(max_length=6, blank=True, db_index=True)
    cve_capt       = models.CharField(max_length=20, blank=True)
    terminal_cve   = models.CharField(max_length=4, blank=True)   # SAAIC_REFIS.CVE_REFI
    terminal_nombre = models.CharField(max_length=70, blank=True) # SAAIC_REFIS.NOM_REFI
    fecha_doda     = models.DateTimeField(null=True, blank=True)  # FEC_DODAE
    fecha_baja     = models.DateTimeField(null=True, blank=True)  # FEC_BAJA
    baj_doda       = models.CharField(max_length=34, blank=True, db_index=True)
    # NUM_DODA (folio) del DODA que este reemplaza — SAAIO_DODA.BAJ_DODA.
    # Vacío = DODA original, no es reemplazo de ninguno.
    notificado_en  = models.DateTimeField(null=True, blank=True)
    modulacion_enviada_en = models.DateTimeField(null=True, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['cve_caat', 'fecha_baja'])]

    def __str__(self):
        return self.num_doda or str(self.id_doda)


class DodaReferencia(models.Model):
    doda       = models.ForeignKey(Doda, on_delete=models.CASCADE, related_name='referencias_doda')
    referencia = models.ForeignKey(Referencia, on_delete=models.CASCADE, null=True, blank=True,
                                    related_name='dodas')
    num_refe   = models.CharField(max_length=15)   # por si aún no existe la Referencia localmente
    cons_id    = models.IntegerField()

    class Meta:
        unique_together = [('doda', 'cons_id')]


class EnvioModulacion(models.Model):
    """Registro de un intento de notificación de modulación para una DODA.

    email_estado y push_estado se llevan por separado porque el correo (al
    capturista) y el push a BitacoraKasu (por contenedor) pueden fallar y
    reintentarse de forma independiente.
    """
    ESTADOS = [
        ('PENDIENTE', 'Pendiente'),
        ('ENVIADO', 'Enviado'),
        ('ERROR', 'Error'),
    ]
    doda            = models.ForeignKey(Doda, on_delete=models.CASCADE, related_name='envios_modulacion')
    email_estado    = models.CharField(max_length=10, choices=ESTADOS, default='PENDIENTE')
    push_estado     = models.CharField(max_length=10, choices=ESTADOS, default='PENDIENTE')
    sg_message_id   = models.CharField(max_length=100, blank=True)
    error_detalle   = models.TextField(blank=True)
    links_completar = models.JSONField(default=dict, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = 'Envío de modulación'
        verbose_name_plural = 'Envíos de modulación'

    def __str__(self):
        return f'{self.doda} | email={self.email_estado} push={self.push_estado}'


class AvisoCruce(models.Model):
    id_cruce = models.IntegerField(unique=True, db_index=True)
    acuse_avc = models.CharField(max_length=64, db_index=True)
    patente = models.CharField(max_length=10, db_index=True)
    fecha_emision = models.DateTimeField(null=True, blank=True)
    fecha_vigencia = models.DateTimeField(null=True, blank=True)
    tag_cruce = models.CharField(max_length=64, blank=True)
    gafete = models.CharField(max_length=64, blank=True)
    num_placa = models.CharField(max_length=32, blank=True)
    sincronizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Aviso de Cruce'
        verbose_name_plural = 'Avisos de Cruce'

    def __str__(self):
        return f'{self.acuse_avc} (Patente {self.patente})'


class AvisoCruceReferencia(models.Model):
    aviso = models.ForeignKey(AvisoCruce, on_delete=models.CASCADE, related_name='referencias_amparadas')
    cons_id = models.IntegerField()
    num_refe = models.CharField(max_length=32, db_index=True)
    num_pedimento = models.CharField(max_length=32, blank=True)
    rfc_importador = models.CharField(max_length=32, blank=True)
    referencia = models.ForeignKey(Referencia, on_delete=models.SET_NULL, null=True, blank=True, related_name='avisos_cruce')

    class Meta:
        unique_together = ('aviso', 'cons_id')
        verbose_name = 'Aviso de Cruce - Referencia'
        verbose_name_plural = 'Avisos de Cruce - Referencias'

    def __str__(self):
        return f'{self.aviso.acuse_avc} -> Ref {self.num_refe}'


class AvisoCruceContenedor(models.Model):
    aviso = models.ForeignKey(AvisoCruce, on_delete=models.CASCADE, related_name='contenedores_listados')
    cons_cont = models.IntegerField()
    num_cont = models.CharField(max_length=32, db_index=True)
    candado = models.CharField(max_length=64, blank=True)
    contenedor = models.ForeignKey(Contenedor, on_delete=models.SET_NULL, null=True, blank=True, related_name='avisos_cruce')

    class Meta:
        unique_together = ('aviso', 'cons_cont')
        verbose_name = 'Aviso de Cruce - Contenedor'
        verbose_name_plural = 'Avisos de Cruce - Contenedores'

    def __str__(self):
        return f'{self.aviso.acuse_avc} -> Cont {self.num_cont}'


class ExpedienteExtraccion(models.Model):
    ESTADOS = [
        ('FALTA_AVC', 'Falta AVC'),
        ('FALTA_DODA', 'Falta DODA'),
        ('COMPLETO', 'Completo'),
        ('AVC_VENCIDO', 'AVC Vencido'),
        ('CANCELADO', 'Cancelado'),
    ]
    clave = models.CharField(max_length=64, unique=True, db_index=True)  # "{num_refe}:{num_cont}"
    referencia = models.ForeignKey(Referencia, on_delete=models.CASCADE, related_name='expedientes_extraccion')
    contenedor = models.ForeignKey(Contenedor, on_delete=models.CASCADE, related_name='expedientes_extraccion')
    doda = models.ForeignKey(Doda, on_delete=models.SET_NULL, null=True, blank=True, related_name='expedientes_extraccion')
    aviso_contenedor = models.ForeignKey(AvisoCruceContenedor, on_delete=models.SET_NULL, null=True, blank=True, related_name='expedientes_extraccion')
    rfc_importador = models.CharField(max_length=32, blank=True)
    estado_documental = models.CharField(max_length=32, choices=ESTADOS, default='FALTA_AVC', db_index=True)
    es_kasu = models.BooleanField(default=False)
    notificado_completo_en = models.DateTimeField(null=True, blank=True)
    push_kasu_estado = models.CharField(max_length=32, default='PENDIENTE')
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Expediente de Extracción'
        verbose_name_plural = 'Expedientes de Extracción'

    def __str__(self):
        return f'{self.clave} [{self.estado_documental}]'


class EventoContenedor(models.Model):
    expediente = models.ForeignKey(ExpedienteExtraccion, on_delete=models.CASCADE, related_name='eventos')
    evento_id = models.CharField(max_length=128, unique=True, db_index=True)
    tipo = models.CharField(max_length=64, db_index=True)
    ocurrido_en = models.DateTimeField()
    datos = models.JSONField(default=dict)
    registrado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Evento de Contenedor'
        verbose_name_plural = 'Eventos de Contenedor'
        ordering = ['-ocurrido_en']

    def __str__(self):
        return f'{self.expediente.clave} - {self.tipo} ({self.ocurrido_en})'
