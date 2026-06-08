from django.contrib import admin
from .models import CuentaGastos, GlosaRegistro, Referencia, Contenedor, GuiaBL, LogSync


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
