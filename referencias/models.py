from django.contrib.auth.models import User
from django.db import models


PATENTE_PREFIJO = {
    '1627': 'LCLF',
    '1656': 'LCRR',
    '1927': 'LCMJ',
}

CVE_CONT_TIPO = {
    1: '20DC', 2: '20RF', 3: '40HC', 4: '40RF',
    9: '20TK', 11: '45HC', 16: '40OT', 17: '40OT',
    20: '40FR', 25: '40FR',
}


class Referencia(models.Model):
    num_refe         = models.CharField(max_length=50, unique=True, db_index=True)
    patente          = models.CharField(max_length=10, db_index=True)
    prefijo          = models.CharField(max_length=10)
    cve_cliente      = models.CharField(max_length=20, blank=True)
    nombre_cliente   = models.CharField(max_length=255, blank=True, db_index=True)
    fecha_arribo     = models.DateField(null=True, blank=True)
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
