from django.contrib import admin
from .models import CuentaGastos, Referencia, Contenedor, GuiaBL, LogSync


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
