from django.contrib import admin
from .models import (
    CuentaGastos, GlosaRegistro, Referencia, Contenedor, GuiaBL, LogSync,
    Doda, DodaReferencia, EnvioModulacion,
)


class ContenedorInline(admin.TabularInline):
    model   = Contenedor
    fields  = ('num_cont', 'tipo')
    extra   = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class GuiaBLInline(admin.TabularInline):
    model   = GuiaBL
    fields  = ('numero_guia', 'tipo_guia')
    extra   = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Referencia)
class ReferenciaAdmin(admin.ModelAdmin):
    list_display   = ('num_refe', 'patente', 'nombre_cliente', 'fecha_pago',
                      'estado_pago', 'clave_pedimento', 'es_rectificacion')
    list_filter    = ('patente', 'es_rectificacion', 'clave_pedimento', 'aduana')
    search_fields  = ('num_refe', 'num_pedimento', 'nombre_cliente', 'cve_cliente')
    date_hierarchy = 'fecha_pago'
    ordering       = ('-fecha_pago',)
    inlines        = [ContenedorInline, GuiaBLInline]
    readonly_fields = (
        'num_refe', 'patente', 'prefijo', 'cve_cliente', 'nombre_cliente',
        'fecha_arribo', 'fecha_validacion', 'fecha_pago', 'fecha_captura',
        'num_pedimento', 'clave_pedimento', 'tipo_pedimento', 'aduana', 'regimen',
        'num_operacion', 'linea_captura', 'cve_capturista', 'nombre_capturista',
        'fir_elec', 'es_rectificacion', 'num_partidas', 'created_at', 'updated_at',
    )
    fieldsets = (
        ('Identificación', {
            'fields': ('num_refe', 'patente', 'prefijo', 'es_rectificacion'),
        }),
        ('Cliente', {
            'fields': ('cve_cliente', 'nombre_cliente'),
        }),
        ('Pedimento', {
            'fields': ('num_pedimento', 'clave_pedimento', 'tipo_pedimento',
                       'aduana', 'regimen', 'num_partidas', 'fir_elec'),
        }),
        ('Fechas', {
            'fields': ('fecha_arribo', 'fecha_validacion', 'fecha_captura', 'fecha_pago'),
        }),
        ('Pago', {
            'fields': ('num_operacion', 'linea_captura'),
        }),
        ('Capturista', {
            'fields': ('cve_capturista', 'nombre_capturista'),
        }),
        ('Sistema', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def estado_pago(self, obj):
        if obj.num_operacion and obj.linea_captura:
            return 'Pagada'
        return 'Pendiente'
    estado_pago.short_description = 'Pago'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GlosaRegistro)
class GlosaRegistroAdmin(admin.ModelAdmin):
    list_display   = ('referencia', 'fecha_entrada', 'usuario_entrada',
                      'estado', 'fecha_conclusion', 'usuario_conclusion',
                      'urgente', 'nota_corta')
    list_filter    = ('urgente', 'referencia__patente', 'usuario_entrada', 'usuario_conclusion')
    search_fields  = ('referencia__num_refe', 'referencia__nombre_cliente', 'nota')
    date_hierarchy = 'fecha_entrada'
    ordering       = ('-fecha_entrada',)
    readonly_fields = ('referencia', 'fecha_entrada', 'usuario_entrada')
    fields         = ('referencia', 'fecha_entrada', 'usuario_entrada',
                      'fecha_conclusion', 'usuario_conclusion', 'nota', 'urgente')
    actions        = ['revertir_a_en_proceso']

    def estado(self, obj):
        return 'Concluida' if obj.concluida else 'En proceso'
    estado.short_description = 'Estado'

    def nota_corta(self, obj):
        return obj.nota[:70] + '…' if len(obj.nota) > 70 else obj.nota or '—'
    nota_corta.short_description = 'Nota'

    @admin.action(description='Revertir a "En proceso" (borrar conclusión)')
    def revertir_a_en_proceso(self, request, queryset):
        if not request.user.is_staff:
            self.message_user(request, 'No tienes permiso para realizar esta acción.', level='error')
            return
        concluidas = queryset.exclude(fecha_conclusion=None)
        total = concluidas.update(fecha_conclusion=None, usuario_conclusion=None)
        self.message_user(request, f'{total} registro(s) revertido(s) a "En proceso".')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_staff


@admin.register(CuentaGastos)
class CuentaGastosAdmin(admin.ModelAdmin):
    list_display   = ('referencia', 'finalizado_por', 'fecha_finalizacion', 'nota_corta')
    list_filter    = ('referencia__patente', 'finalizado_por')
    search_fields  = ('referencia__num_refe', 'referencia__nombre_cliente', 'nota')
    readonly_fields = ('referencia', 'finalizado_por', 'fecha_finalizacion')
    date_hierarchy  = 'fecha_finalizacion'
    ordering        = ('-fecha_finalizacion',)

    def nota_corta(self, obj):
        return obj.nota[:60] + '…' if len(obj.nota) > 60 else obj.nota or '—'
    nota_corta.short_description = 'Nota'

    def has_add_permission(self, request):
        return False


@admin.register(LogSync)
class LogSyncAdmin(admin.ModelAdmin):
    list_display  = ('timestamp', 'patente', 'agent_id', 'referencias',
                     'creadas', 'actualizadas', 'errores_count', 'duracion_seg', 'exitoso')
    list_filter   = ('patente', 'exitoso', 'agent_id')
    readonly_fields = ('timestamp', 'patente', 'agent_id', 'referencias', 'contenedores',
                       'guias', 'creadas', 'actualizadas', 'exitoso', 'error', 'duracion_seg')
    ordering      = ('-timestamp',)
    date_hierarchy = 'timestamp'

    def errores_count(self, obj):
        return '✘' if obj.error else '—'
    errores_count.short_description = 'Errores'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Doda)
class DodaAdmin(admin.ModelAdmin):
    list_display = ('id_doda', 'num_doda', 'cve_caat', 'patente', 'terminal_nombre',
                    'fecha_doda', 'fecha_baja', 'notificado_en', 'modulacion_enviada_en')
    list_filter = ('cve_caat', 'fecha_baja')
    search_fields = ('num_doda', 'id_doda', 'patente', 'cve_caat')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-created_at',)
    date_hierarchy = 'fecha_doda'


@admin.register(DodaReferencia)
class DodaReferenciaAdmin(admin.ModelAdmin):
    list_display = ('id', 'doda', 'referencia', 'num_refe', 'cons_id')
    list_filter = ('doda__patente', 'doda__cve_caat')
    search_fields = ('num_refe', 'referencia__num_refe', 'doda__num_doda')
    raw_id_fields = ('doda', 'referencia')


@admin.register(EnvioModulacion)
class EnvioModulacionAdmin(admin.ModelAdmin):
    list_display = ('id', 'doda', 'email_estado', 'push_estado',
                    'sg_message_id', 'created_at', 'updated_at')
    list_filter = ('email_estado', 'push_estado')
    search_fields = ('doda__num_doda', 'doda__id_doda', 'sg_message_id')
    readonly_fields = ('doda', 'email_estado', 'push_estado', 'sg_message_id',
                       'error_detalle', 'created_at', 'updated_at')
    raw_id_fields = ('doda',)
    ordering = ('-created_at',)
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False
