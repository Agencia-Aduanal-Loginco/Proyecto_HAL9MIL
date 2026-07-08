from django.contrib import admin
from .models import (
    Anticipo, CatalogoSAT, CierreMensual, ComisionReferencia, ConfiguracionFiscal,
    ConceptoFactura, CuentaBancaria, CuentaContable, DoctoRelacionado, Factura,
    GastoReferencia, MovimientoBancario, Pago, PartidaPoliza, PolizaContable, XMLProveedor,
)


@admin.register(ConfiguracionFiscal)
class ConfiguracionFiscalAdmin(admin.ModelAdmin):
    list_display = ('patente', 'rfc', 'razon_social', 'regimen_fiscal', 'activa')
    list_filter = ('activa', 'regimen_fiscal')
    search_fields = ('patente', 'rfc', 'razon_social')


@admin.register(CatalogoSAT)
class CatalogoSATAdmin(admin.ModelAdmin):
    list_display = ('catalogo', 'clave', 'descripcion', 'vigente')
    list_filter = ('catalogo', 'vigente')
    search_fields = ('clave', 'descripcion')


@admin.register(CuentaContable)
class CuentaContableAdmin(admin.ModelAdmin):
    list_display = ('numero', 'nombre', 'tipo', 'nivel', 'naturaleza', 'es_hoja', 'codigo_agrupador_sat')
    list_filter = ('tipo', 'nivel', 'naturaleza', 'es_hoja')
    search_fields = ('numero', 'nombre', 'codigo_agrupador_sat')
    raw_id_fields = ('padre',)
    ordering = ('numero',)


class PartidaPolizaInline(admin.TabularInline):
    model = PartidaPoliza
    extra = 0
    fields = ('linea', 'cuenta', 'concepto', 'debe', 'haber')
    readonly_fields = ('linea',)


@admin.register(PolizaContable)
class PolizaContableAdmin(admin.ModelAdmin):
    list_display = ('numero', 'tipo', 'fecha', 'concepto', 'cerrado', 'total_debe', 'total_haber')
    list_filter = ('tipo', 'cerrado', 'anio', 'mes')
    search_fields = ('numero', 'concepto')
    raw_id_fields = ('referencia',)
    inlines = [PartidaPolizaInline]
    readonly_fields = ('total_debe', 'total_haber')

    def total_debe(self, obj):
        return f'${obj.total_debe:,.2f}'
    total_debe.short_description = 'Debe'

    def total_haber(self, obj):
        return f'${obj.total_haber:,.2f}'
    total_haber.short_description = 'Haber'


@admin.register(XMLProveedor)
class XMLProveedorAdmin(admin.ModelAdmin):
    list_display = ('rfc_emisor', 'nombre_emisor', 'total', 'moneda', 'tipo_comprobante', 'procesado', 'cargado_en')
    list_filter = ('tipo_comprobante', 'procesado', 'moneda')
    search_fields = ('rfc_emisor', 'nombre_emisor', 'uuid_fiscal', 'referencia__num_refe')
    raw_id_fields = ('referencia',)
    readonly_fields = ('uuid_fiscal', 'fecha_emision', 'rfc_emisor', 'nombre_emisor',
                       'rfc_receptor', 'subtotal', 'iva', 'total', 'cargado_en')


@admin.register(Anticipo)
class AnticipoAdmin(admin.ModelAdmin):
    list_display = ('referencia', 'fecha', 'monto', 'moneda', 'forma_pago', 'registrado_por')
    list_filter = ('moneda', 'forma_pago')
    search_fields = ('referencia__num_refe', 'referencia__nombre_cliente', 'num_operacion')
    raw_id_fields = ('referencia', 'poliza')
    date_hierarchy = 'fecha'


@admin.register(GastoReferencia)
class GastoReferenciaAdmin(admin.ModelAdmin):
    list_display = ('referencia', 'tipo', 'concepto', 'fecha', 'monto', 'moneda', 'proveedor')
    list_filter = ('tipo', 'moneda')
    search_fields = ('referencia__num_refe', 'concepto', 'proveedor', 'num_factura_proveedor')
    raw_id_fields = ('referencia', 'poliza', 'cuenta_gasto')
    date_hierarchy = 'fecha'


class ConceptoFacturaInline(admin.TabularInline):
    model = ConceptoFactura
    extra = 0
    fields = ('descripcion', 'cantidad', 'valor_unitario', 'importe', 'tasa_iva',
              'clave_prod_serv', 'clave_unidad')
    readonly_fields = ('importe',)


@admin.register(Factura)
class FacturaAdmin(admin.ModelAdmin):
    list_display = ('folio_display', 'nombre_receptor', 'rfc_receptor',
                    'estado_badge', 'subtotal_fmt', 'iva_fmt', 'total_fmt',
                    'configuracion_fiscal', 'created_at')
    list_filter = ('estado', 'serie', 'configuracion_fiscal', 'moneda')
    search_fields = ('rfc_receptor', 'nombre_receptor', 'uuid_fiscal')
    readonly_fields = ('uuid_fiscal', 'xml_timbrado', 'total', 'created_at')
    inlines = [ConceptoFacturaInline]
    filter_horizontal = ('referencias',)

    def folio_display(self, obj):
        return f'{obj.serie}{obj.folio}'
    folio_display.short_description = 'Folio'

    def estado_badge(self, obj):
        colors = {'BORRADOR': '#6b7280', 'TIMBRADA': '#16a34a', 'CANCELADA': '#dc2626'}
        color = colors.get(obj.estado, '#6b7280')
        return f'<span style="color:{color};font-weight:600">{obj.get_estado_display()}</span>'
    estado_badge.allow_tags = True
    estado_badge.short_description = 'Estado'

    def subtotal_fmt(self, obj):
        return f'${obj.subtotal:,.2f}'
    subtotal_fmt.short_description = 'Subtotal'

    def iva_fmt(self, obj):
        return f'${obj.iva:,.2f}'
    iva_fmt.short_description = 'IVA'

    def total_fmt(self, obj):
        return f'${obj.total:,.2f}'
    total_fmt.short_description = 'Total'


class DoctoRelacionadoInline(admin.TabularInline):
    model = DoctoRelacionado
    extra = 0
    readonly_fields = ('imp_saldo_insoluto',)
    fields = ('factura', 'num_parcialidad', 'imp_saldo_anterior', 'imp_pagado', 'imp_saldo_insoluto')


@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ('fecha_pago', 'monto_fmt', 'moneda', 'forma_pago',
                    'num_operacion', 'estado', 'created_at')
    list_filter = ('estado', 'moneda', 'forma_pago')
    search_fields = ('num_operacion', 'uuid_fiscal')
    readonly_fields = ('uuid_fiscal', 'xml_timbrado', 'created_at')
    inlines = [DoctoRelacionadoInline]
    date_hierarchy = 'fecha_pago'

    def monto_fmt(self, obj):
        return f'${obj.monto:,.2f}'
    monto_fmt.short_description = 'Monto'


@admin.register(CuentaBancaria)
class CuentaBancariaAdmin(admin.ModelAdmin):
    list_display = ('banco', 'nombre', 'numero_cuenta', 'moneda', 'cuenta_contable', 'activa')
    list_filter  = ('banco', 'moneda', 'activa')
    search_fields = ('nombre', 'banco', 'numero_cuenta', 'clabe')


class MovimientoBancarioInline(admin.TabularInline):
    model  = MovimientoBancario
    extra  = 0
    fields = ('fecha', 'descripcion', 'referencia_banco', 'cargo', 'abono', 'saldo', 'poliza', 'conciliado')
    readonly_fields = ('mes', 'anio')


@admin.register(MovimientoBancario)
class MovimientoBancarioAdmin(admin.ModelAdmin):
    list_display  = ('fecha', 'cuenta', 'descripcion', 'cargo_fmt', 'abono_fmt', 'conciliado', 'poliza')
    list_filter   = ('conciliado', 'cuenta', 'anio', 'mes')
    search_fields = ('descripcion', 'referencia_banco')
    date_hierarchy = 'fecha'
    readonly_fields = ('mes', 'anio')

    def cargo_fmt(self, obj):
        return f'${obj.cargo:,.2f}' if obj.cargo else '—'
    cargo_fmt.short_description = 'Cargo'

    def abono_fmt(self, obj):
        return f'${obj.abono:,.2f}' if obj.abono else '—'
    abono_fmt.short_description = 'Abono'


@admin.register(CierreMensual)
class CierreMensualAdmin(admin.ModelAdmin):
    list_display  = ('mes', 'anio', 'patente', 'total_polizas',
                     'ingresos_fmt', 'egresos_fmt', 'cerrado_por', 'fecha_cierre')
    list_filter   = ('patente', 'anio', 'mes')
    readonly_fields = ('fecha_cierre', 'cerrado_por', 'total_polizas',
                       'total_facturas_emitidas', 'total_ingresos',
                       'total_egresos', 'total_anticipos_recibidos')

    def ingresos_fmt(self, obj):
        return f'${obj.total_ingresos:,.2f}'
    ingresos_fmt.short_description = 'Ingresos'

    def egresos_fmt(self, obj):
        return f'${obj.total_egresos:,.2f}'
    egresos_fmt.short_description = 'Egresos'


@admin.register(ComisionReferencia)
class ComisionReferenciaAdmin(admin.ModelAdmin):
    list_display  = ('referencia', 'agente', 'mes', 'anio',
                     'valor_fmt', 'tasa_fmt', 'comision_fmt', 'fecha_calculo')
    list_filter   = ('anio', 'mes', 'agente')
    search_fields = ('referencia__num_refe',)
    readonly_fields = ('fecha_calculo',)
    date_hierarchy = None

    def valor_fmt(self, obj):
        return f'${obj.valor_operacion:,.2f}'
    valor_fmt.short_description = 'Valor op.'

    def tasa_fmt(self, obj):
        return f'{float(obj.tasa_comision)*100:.2f}%'
    tasa_fmt.short_description = 'Tasa'

    def comision_fmt(self, obj):
        return f'${obj.monto_comision:,.2f}'
    comision_fmt.short_description = 'Comisión'
