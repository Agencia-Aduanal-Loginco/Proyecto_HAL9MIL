from django.contrib import admin
from .models import Referencia, Contenedor, GuiaBL, LogSync


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
