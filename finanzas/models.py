from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from hal9mil.storage_backends import media_storage

class ConfiguracionFiscal(models.Model):
    patente = models.CharField(max_length=4, unique=True)
    rfc = models.CharField(max_length=13)
    razon_social = models.CharField(max_length=200)
    regimen_fiscal = models.CharField(max_length=3)
    codigo_postal = models.CharField(max_length=5)
    cert_path = models.CharField(max_length=500)
    key_path = models.CharField(max_length=500)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Configuración Fiscal'
        verbose_name_plural = 'Configuraciones Fiscales'

    def __str__(self):
        return f'Patente {self.patente} — {self.rfc}'

    def get_key_password(self) -> str:
        import os
        var = f'CFDI_{self.patente}_KEY_PASSWORD'
        pwd = os.environ.get(var, '')
        if not pwd:
            raise ValueError(f'Variable de entorno {var} no definida')
        return pwd


class CatalogoSAT(models.Model):
    catalogo = models.CharField(max_length=50)
    clave = models.CharField(max_length=20)
    descripcion = models.CharField(max_length=200)
    vigente = models.BooleanField(default=True)

    class Meta:
        unique_together = ('catalogo', 'clave')
        ordering = ['catalogo', 'clave']
        verbose_name = 'Catálogo SAT'
        verbose_name_plural = 'Catálogos SAT'

    def __str__(self):
        return f'{self.catalogo} — {self.clave}: {self.descripcion}'


class CuentaContable(models.Model):
    TIPO_CHOICES = [
        ('A', 'Activo'), ('P', 'Pasivo'), ('C', 'Capital'),
        ('I', 'Ingreso'), ('G', 'Gasto'),
    ]
    NATURALEZA_CHOICES = [('D', 'Deudora'), ('A', 'Acreedora')]

    numero = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=200)
    nivel = models.PositiveSmallIntegerField()
    tipo = models.CharField(max_length=1, choices=TIPO_CHOICES)
    padre = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.PROTECT, related_name='hijos'
    )
    es_hoja = models.BooleanField(default=True)
    naturaleza = models.CharField(max_length=1, choices=NATURALEZA_CHOICES)
    codigo_agrupador_sat = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ['numero']
        verbose_name = 'Cuenta Contable'
        verbose_name_plural = 'Cuentas Contables'

    def __str__(self):
        return f'{self.numero} — {self.nombre}'


# ── Fase 2 ────────────────────────────────────────────────────────────────────

class PolizaContable(models.Model):
    TIPO_POLIZA = [('D', 'Diario'), ('H', 'Ingreso'), ('E', 'Egreso')]

    numero = models.CharField(max_length=20)
    tipo = models.CharField(max_length=1, choices=TIPO_POLIZA)
    fecha = models.DateField()
    mes = models.PositiveSmallIntegerField()
    anio = models.PositiveIntegerField()
    concepto = models.CharField(max_length=300)
    referencia = models.ForeignKey(
        'referencias.Referencia',
        null=True, blank=True,
        on_delete=models.SET_NULL, related_name='polizas'
    )
    cerrado = models.BooleanField(default=False)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True,
        on_delete=models.SET_NULL, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('numero', 'tipo', 'anio', 'mes')
        ordering = ['-fecha', '-numero']
        verbose_name = 'Póliza Contable'
        verbose_name_plural = 'Pólizas Contables'

    def __str__(self):
        return f'{self.get_tipo_display()} {self.numero} ({self.fecha})'

    def clean(self):
        if not self.pk:
            return
        totales = self.partidas.aggregate(
            total_debe=Sum('debe'), total_haber=Sum('haber'),
        )
        debe = totales['total_debe'] or Decimal('0')
        haber = totales['total_haber'] or Decimal('0')
        if debe != haber:
            raise ValidationError(f'Póliza descuadrada: DEBE {debe} ≠ HABER {haber}')

    @property
    def total_debe(self):
        return self.partidas.aggregate(t=Sum('debe'))['t'] or Decimal('0')

    @property
    def total_haber(self):
        return self.partidas.aggregate(t=Sum('haber'))['t'] or Decimal('0')


class PartidaPoliza(models.Model):
    poliza = models.ForeignKey(
        PolizaContable, on_delete=models.CASCADE, related_name='partidas'
    )
    linea = models.PositiveSmallIntegerField()
    cuenta = models.ForeignKey(CuentaContable, on_delete=models.PROTECT)
    concepto = models.CharField(max_length=300)
    debe = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    haber = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))

    class Meta:
        unique_together = ('poliza', 'linea')
        ordering = ['linea']
        verbose_name = 'Partida de Póliza'
        verbose_name_plural = 'Partidas de Póliza'

    def __str__(self):
        return f'L{self.linea} {self.cuenta.numero} D:{self.debe} H:{self.haber}'


class Anticipo(models.Model):
    referencia = models.ForeignKey(
        'referencias.Referencia',
        on_delete=models.PROTECT, related_name='anticipos'
    )
    fecha = models.DateField()
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    moneda = models.CharField(max_length=3, default='MXN')
    forma_pago = models.CharField(max_length=2)
    num_operacion = models.CharField(max_length=100, blank=True)
    observaciones = models.CharField(max_length=300, blank=True)
    cuenta_destino = models.ForeignKey(
        CuentaContable, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='anticipos_destino'
    )
    poliza = models.ForeignKey(
        PolizaContable, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='anticipos'
    )
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True,
        on_delete=models.SET_NULL, related_name='anticipos_registrados'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-created_at']
        verbose_name = 'Anticipo'
        verbose_name_plural = 'Anticipos'

    def __str__(self):
        return f'{self.referencia} | ${self.monto} {self.moneda} ({self.fecha})'


# ── Fase 3 ────────────────────────────────────────────────────────────────────

class XMLProveedor(models.Model):
    referencia = models.ForeignKey(
        'referencias.Referencia',
        null=True, blank=True,
        on_delete=models.SET_NULL, related_name='xmls_proveedor'
    )
    uuid_fiscal = models.UUIDField(unique=True)
    fecha_emision = models.DateTimeField()
    rfc_emisor = models.CharField(max_length=13)
    nombre_emisor = models.CharField(max_length=200)
    rfc_receptor = models.CharField(max_length=13)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    iva = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    total = models.DecimalField(max_digits=14, decimal_places=2)
    moneda = models.CharField(max_length=3, default='MXN')
    tipo_comprobante = models.CharField(max_length=1)   # I=Ingreso, E=Egreso
    concepto_principal = models.CharField(max_length=300, blank=True)
    xml_file = models.FileField(storage=media_storage, upload_to='xmls_proveedores/%Y/%m/')
    cargado_en = models.DateTimeField(auto_now_add=True)
    procesado = models.BooleanField(default=False)  # True si ya generó GastoReferencia
    ESTADO_ASIGNACION = [
        ('ASIGNADO', 'Asignado'),
        ('PENDIENTE', 'Pendiente'),
    ]
    pdf_file = models.FileField(
        storage=media_storage, upload_to='xmls_proveedores/%Y/%m/', null=True, blank=True
    )
    estado_asignacion = models.CharField(
        max_length=10, choices=ESTADO_ASIGNACION, default='PENDIENTE'
    )
    motivo_pendiente = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-fecha_emision']
        verbose_name = 'XML de Proveedor'
        verbose_name_plural = 'XMLs de Proveedor'

    def __str__(self):
        return f'{self.rfc_emisor} | {self.uuid_fiscal} | ${self.total}'


class ComplementoPago(models.Model):
    """Complemento de Pago (CFDI tipo P): no es una factura, es la prueba de
    que una factura (XMLProveedor) ya fue pagada. Su <DoctoRelacionado> trae
    el UUID de esa factura."""
    ESTADO = [
        ('PENDIENTE', 'Pendiente'),        # no se encontró la factura aún
        ('IDENTIFICADO', 'Identificado'),  # ligado a una factura
        ('REVISION', 'Requiere revisión'), # trae más de un DoctoRelacionado
    ]
    factura = models.ForeignKey(
        XMLProveedor, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='complementos_pago'
    )
    uuid_complemento = models.UUIDField(unique=True)
    uuid_factura_relacionada = models.UUIDField(null=True, blank=True)
    fecha_emision = models.DateTimeField()
    rfc_emisor = models.CharField(max_length=13)
    nombre_emisor = models.CharField(max_length=200)
    monto_pagado = models.DecimalField(max_digits=14, decimal_places=2)
    moneda_pago = models.CharField(max_length=3, default='MXN')
    estado = models.CharField(max_length=12, choices=ESTADO, default='PENDIENTE')
    xml_file = models.FileField(storage=media_storage, upload_to='complementos_pago/%Y/%m/')
    pdf_file = models.FileField(
        storage=media_storage, upload_to='complementos_pago/%Y/%m/',
        null=True, blank=True,
    )
    referencia_sugerida = models.ForeignKey(
        'referencias.Referencia', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='complementos_pago_sugeridos',
    )
    cargado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-cargado_en']
        verbose_name = 'Complemento de Pago'
        verbose_name_plural = 'Complementos de Pago'

    def __str__(self):
        return f'{self.rfc_emisor} | pago {self.monto_pagado} | {self.estado}'


class GastoReferencia(models.Model):
    TIPO_GASTO = [
        ('FLETE', 'Flete'),
        ('ALMACENAJE', 'Almacenaje'),
        ('DERECHOS', 'Derechos aduanales'),
        ('HONORARIOS', 'Honorarios agencia'),
        ('MANIOBRAS', 'Maniobras'),
        ('OTROS', 'Otros'),
    ]
    referencia = models.ForeignKey(
        'referencias.Referencia',
        on_delete=models.PROTECT, related_name='gastos_finanzas'
    )
    tipo = models.CharField(max_length=20, choices=TIPO_GASTO)
    concepto = models.CharField(max_length=300)
    fecha = models.DateField()
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    moneda = models.CharField(max_length=3, default='MXN')
    proveedor = models.CharField(max_length=200, blank=True)
    num_factura_proveedor = models.CharField(max_length=50, blank=True)
    xml_proveedor = models.ForeignKey(
        XMLProveedor, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='gastos'
    )
    cuenta_gasto = models.ForeignKey(
        CuentaContable, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='gastos_referencia'
    )
    poliza = models.ForeignKey(
        PolizaContable, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='gastos'
    )
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True,
        on_delete=models.SET_NULL, related_name='gastos_registrados'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-created_at']
        verbose_name = 'Gasto de Referencia'
        verbose_name_plural = 'Gastos de Referencia'

    def __str__(self):
        return f'{self.referencia} | {self.get_tipo_display()} ${self.monto}'


# ── Fase 4 ────────────────────────────────────────────────────────────────────

class Factura(models.Model):
    ESTADO = [
        ('BORRADOR', 'Borrador'),
        ('TIMBRADA', 'Timbrada'),
        ('CANCELADA', 'Cancelada'),
    ]
    serie = models.CharField(max_length=10, default='A')
    folio = models.PositiveIntegerField()
    fecha_emision = models.DateTimeField(null=True, blank=True)
    # Receptor
    rfc_receptor = models.CharField(max_length=13)
    nombre_receptor = models.CharField(max_length=200)
    domicilio_fiscal_receptor = models.CharField(max_length=5)
    regimen_fiscal_receptor = models.CharField(max_length=3)
    uso_cfdi = models.CharField(max_length=3, default='G03')
    forma_pago = models.CharField(max_length=2, default='99')
    metodo_pago = models.CharField(max_length=3, default='PPD')
    moneda = models.CharField(max_length=3, default='MXN')
    # Importes
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    iva = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    # Timbrado
    uuid_fiscal = models.UUIDField(null=True, blank=True, unique=True)
    xml_timbrado = models.TextField(blank=True)
    estado = models.CharField(max_length=10, choices=ESTADO, default='BORRADOR')
    # Relaciones
    referencias = models.ManyToManyField(
        'referencias.Referencia', blank=True, related_name='facturas'
    )
    configuracion_fiscal = models.ForeignKey(
        ConfiguracionFiscal, on_delete=models.PROTECT, related_name='facturas'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('serie', 'folio')
        ordering = ['-created_at']
        verbose_name = 'Factura'
        verbose_name_plural = 'Facturas'

    def __str__(self):
        return f'{self.serie}{self.folio} — {self.nombre_receptor} [{self.get_estado_display()}]'

    @classmethod
    def siguiente_folio(cls, serie='A'):
        last = cls.objects.filter(serie=serie).order_by('-folio').first()
        return (last.folio + 1) if last else 1

    def save(self, *args, **kwargs):
        self.total = self.subtotal + self.iva
        super().save(*args, **kwargs)


class ConceptoFactura(models.Model):
    factura = models.ForeignKey(Factura, on_delete=models.CASCADE, related_name='conceptos')
    clave_prod_serv = models.CharField(max_length=8, default='84111506')  # servicios aduanales SAT
    clave_unidad = models.CharField(max_length=3, default='ACT')
    descripcion = models.CharField(max_length=1000)
    cantidad = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal('1'))
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=2)
    importe = models.DecimalField(max_digits=14, decimal_places=2)
    objeto_imp = models.CharField(max_length=2, default='02')
    tasa_iva = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.16'))

    class Meta:
        verbose_name = 'Concepto de Factura'
        verbose_name_plural = 'Conceptos de Factura'

    def __str__(self):
        return f'{self.descripcion[:60]} — ${self.importe}'

    def save(self, *args, **kwargs):
        self.importe = (self.cantidad * self.valor_unitario).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)


# ── Fase 6 ────────────────────────────────────────────────────────────────────

class Pago(models.Model):
    ESTADO = [('PENDIENTE', 'Pendiente'), ('TIMBRADO', 'Timbrado')]

    fecha_pago = models.DateField()
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    moneda = models.CharField(max_length=3, default='MXN')
    forma_pago = models.CharField(max_length=2)
    num_operacion = models.CharField(max_length=100, blank=True)
    estado = models.CharField(max_length=10, choices=ESTADO, default='PENDIENTE')
    uuid_fiscal = models.UUIDField(null=True, blank=True, unique=True)
    xml_timbrado = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_pago', '-created_at']
        verbose_name = 'Pago'
        verbose_name_plural = 'Pagos'

    def __str__(self):
        return f'Pago ${self.monto} {self.moneda} ({self.fecha_pago}) [{self.get_estado_display()}]'


class DoctoRelacionado(models.Model):
    """Fracción de una factura liquidada por un Pago."""
    pago = models.ForeignKey(Pago, on_delete=models.CASCADE, related_name='documentos')
    factura = models.ForeignKey(Factura, on_delete=models.PROTECT, related_name='documentos_pago')
    num_parcialidad = models.PositiveSmallIntegerField(default=1)
    imp_saldo_anterior = models.DecimalField(max_digits=14, decimal_places=2)
    imp_pagado = models.DecimalField(max_digits=14, decimal_places=2)
    imp_saldo_insoluto = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        verbose_name = 'Documento Relacionado'
        verbose_name_plural = 'Documentos Relacionados'

    def __str__(self):
        return f'Parcialidad {self.num_parcialidad} Factura {self.factura}'

    def save(self, *args, **kwargs):
        self.imp_saldo_insoluto = self.imp_saldo_anterior - self.imp_pagado
        super().save(*args, **kwargs)


# ── Fase 8 — Consolidación bancaria ──────────────────────────────────────────

class CuentaBancaria(models.Model):
    nombre         = models.CharField(max_length=100)
    banco          = models.CharField(max_length=50)
    numero_cuenta  = models.CharField(max_length=30)
    clabe          = models.CharField(max_length=18, blank=True)
    moneda         = models.CharField(max_length=3, default='MXN')
    cuenta_contable = models.ForeignKey(
        CuentaContable, on_delete=models.PROTECT,
        related_name='cuentas_bancarias'
    )
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ['banco', 'nombre']
        verbose_name = 'Cuenta Bancaria'
        verbose_name_plural = 'Cuentas Bancarias'

    def __str__(self):
        return f'{self.banco} — {self.nombre} ({self.numero_cuenta[-4:]})'


class MovimientoBancario(models.Model):
    cuenta          = models.ForeignKey(
        CuentaBancaria, on_delete=models.CASCADE,
        related_name='movimientos'
    )
    fecha           = models.DateField()
    descripcion     = models.CharField(max_length=300)
    referencia_banco = models.CharField(max_length=100, blank=True)
    cargo           = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    abono           = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    saldo           = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    poliza          = models.ForeignKey(
        PolizaContable, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='movimientos_bancarios'
    )
    conciliado      = models.BooleanField(default=False)
    mes             = models.PositiveSmallIntegerField()
    anio            = models.PositiveIntegerField()

    class Meta:
        ordering = ['fecha', 'pk']
        verbose_name = 'Movimiento Bancario'
        verbose_name_plural = 'Movimientos Bancarios'

    def __str__(self):
        tipo = f'Cargo ${self.cargo}' if self.cargo else f'Abono ${self.abono}'
        return f'{self.fecha} {self.descripcion[:40]} — {tipo}'

    def save(self, *args, **kwargs):
        self.mes  = self.fecha.month
        self.anio = self.fecha.year
        super().save(*args, **kwargs)


# ── Fase 9 — Cierre mensual ───────────────────────────────────────────────────

class CierreMensual(models.Model):
    mes     = models.PositiveSmallIntegerField()
    anio    = models.PositiveIntegerField()
    patente = models.CharField(max_length=4)
    fecha_cierre = models.DateTimeField(auto_now_add=True)
    cerrado_por  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='cierres_mensuales'
    )
    total_polizas           = models.PositiveIntegerField(default=0)
    total_facturas_emitidas = models.PositiveIntegerField(default=0)
    total_ingresos          = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    total_egresos           = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    total_anticipos_recibidos = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    observaciones           = models.TextField(blank=True)

    class Meta:
        unique_together = ('mes', 'anio', 'patente')
        ordering = ['-anio', '-mes', 'patente']
        verbose_name = 'Cierre Mensual'
        verbose_name_plural = 'Cierres Mensuales'

    def __str__(self):
        return f'Cierre {self.mes:02d}/{self.anio} — Patente {self.patente}'


# ── Fase 10 — Comisiones ──────────────────────────────────────────────────────

class ComisionReferencia(models.Model):
    referencia = models.OneToOneField(
        'referencias.Referencia',
        on_delete=models.CASCADE,
        related_name='comision',
    )
    agente = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='comisiones',
    )
    valor_operacion = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    tasa_comision   = models.DecimalField(max_digits=6,  decimal_places=4, default=Decimal('0'))
    monto_comision  = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    mes             = models.PositiveSmallIntegerField()
    anio            = models.PositiveIntegerField()
    fecha_calculo   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-anio', '-mes', 'referencia']
        verbose_name = 'Comisión por Referencia'
        verbose_name_plural = 'Comisiones por Referencia'

    def __str__(self):
        return f'Comisión {self.referencia_id} — {self.mes:02d}/{self.anio}'


# ── Cobranza ──────────────────────────────────────────────────────────────────

class RecordatorioCobranza(models.Model):
    TIPO_CHOICES = [
        ('15d', '15 días'), ('30d', '30 días'), ('60d', '60 días'), ('manual', 'Manual'),
    ]

    factura     = models.ForeignKey(
        Factura, on_delete=models.CASCADE, related_name='recordatorios'
    )
    tipo        = models.CharField(max_length=6, choices=TIPO_CHOICES)
    enviado_en  = models.DateTimeField(auto_now_add=True)
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+'
    )
    exitoso     = models.BooleanField(default=True)
    error_msg   = models.TextField(blank=True)

    class Meta:
        ordering = ['-enviado_en']
        verbose_name = 'Recordatorio de Cobranza'
        verbose_name_plural = 'Recordatorios de Cobranza'

    def __str__(self):
        return f'{self.factura} | {self.get_tipo_display()} | {self.enviado_en:%Y-%m-%d}'


# ── Envío de cuenta de gastos al cliente ─────────────────────────────────────

class CierreCuentaGastos(models.Model):
    """Cierre financiero de la cuenta de gastos de una referencia.

    Cerrada = existe el registro y reabierta_en IS NULL. La reapertura (solo
    superusuario) llena reabierta_por/reabierta_en; un re-cierre posterior
    actualiza cerrada_por/cerrada_en y limpia la reapertura (se audita solo
    el último ciclo).
    """
    referencia = models.OneToOneField(
        'referencias.Referencia',
        on_delete=models.PROTECT, related_name='cierre_cg'
    )
    cerrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True,
        on_delete=models.SET_NULL, related_name='cierres_cg'
    )
    cerrada_en = models.DateTimeField(default=timezone.now)
    nota = models.CharField(max_length=300, blank=True)
    reabierta_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='reaperturas_cg'
    )
    reabierta_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Cierre de cuenta de gastos'
        verbose_name_plural = 'Cierres de cuenta de gastos'

    @property
    def activa(self):
        return self.reabierta_en is None

    @classmethod
    def activo_para(cls, referencia):
        return cls.objects.filter(
            referencia=referencia, reabierta_en__isnull=True
        ).first()

    def __str__(self):
        estado = 'cerrada' if self.activa else 'reabierta'
        return f'{self.referencia} | {estado} ({self.cerrada_en:%Y-%m-%d})'


class NotificacionCuentaGastos(models.Model):
    ESTADOS = [
        ('ENVIADO', 'Enviado'),
        ('ENTREGADO', 'Entregado'),
        ('LEIDO', 'Leído'),
        ('REBOTADO', 'Rebotado'),
        ('ERROR', 'Error'),
    ]
    referencia = models.ForeignKey(
        'referencias.Referencia',
        on_delete=models.PROTECT, related_name='notificaciones_cg'
    )
    destinatario = models.EmailField()
    cc = models.EmailField(blank=True)
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True,
        on_delete=models.SET_NULL, related_name='notificaciones_cg_enviadas'
    )
    enviado_en = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='ENVIADO')
    entregado_en = models.DateTimeField(null=True, blank=True)
    leido_en = models.DateTimeField(null=True, blank=True)
    sg_message_id = models.CharField(max_length=100, blank=True, db_index=True)
    error_msg = models.TextField(blank=True)
    es_reenvio = models.BooleanField(default=False)
    zip_file = models.FileField(
        storage=media_storage, upload_to='cuentas_gastos/%Y/%m/',
        null=True, blank=True
    )

    class Meta:
        ordering = ['-enviado_en']
        verbose_name = 'Notificación de cuenta de gastos'
        verbose_name_plural = 'Notificaciones de cuenta de gastos'

    def __str__(self):
        return f'{self.referencia} → {self.destinatario} [{self.estado}]'
