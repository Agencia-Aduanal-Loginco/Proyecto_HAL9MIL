import json
from django.contrib import admin
from .models import Destinatario, HistorialReporte


@admin.register(Destinatario)
class DestinatarioAdmin(admin.ModelAdmin):
    list_display = (
        'nombre', 'email', 'whatsapp', 'activo',
        'recibe_semanal', 'recibe_mensual',
        'recibe_wa_semanal', 'recibe_wa_mensual',
        'recibe_wa_ia_referencias', 'recibe_wa_ia_glosa', 'recibe_wa_ia_cuenta_gastos',
    )
    list_editable = (
        'activo', 'recibe_semanal', 'recibe_mensual',
        'recibe_wa_semanal', 'recibe_wa_mensual',
        'recibe_wa_ia_referencias', 'recibe_wa_ia_glosa', 'recibe_wa_ia_cuenta_gastos',
    )
    list_filter = (
        'activo', 'recibe_semanal', 'recibe_mensual',
        'recibe_wa_semanal', 'recibe_wa_mensual',
        'recibe_wa_ia_referencias', 'recibe_wa_ia_glosa', 'recibe_wa_ia_cuenta_gastos',
    )
    search_fields = ('nombre', 'email', 'whatsapp')
    fieldsets = (
        (None, {'fields': ('nombre', 'activo', 'notas')}),
        ('Email', {'fields': ('email', 'recibe_semanal', 'recibe_mensual')}),
        ('WhatsApp — Reportes', {'fields': ('whatsapp', 'recibe_wa_semanal', 'recibe_wa_mensual')}),
        ('WhatsApp — Interpretación IA', {
            'description': 'Selecciona los módulos cuya interpretación IA recibirá este destinatario.',
            'fields': ('recibe_wa_ia_referencias', 'recibe_wa_ia_glosa', 'recibe_wa_ia_cuenta_gastos'),
        }),
    )


@admin.register(HistorialReporte)
class HistorialReporteAdmin(admin.ModelAdmin):
    list_display = ('tipo', 'fecha_envio', 'periodo_inicio', 'periodo_fin', 'exitoso', 'num_destinatarios')
    list_filter = ('tipo', 'exitoso')
    readonly_fields = ('tipo', 'fecha_envio', 'periodo_inicio', 'periodo_fin', 'destinatarios', 'exitoso', 'error')
    ordering = ('-fecha_envio',)

    def num_destinatarios(self, obj):
        try:
            return len(json.loads(obj.destinatarios))
        except Exception:
            return 0
    num_destinatarios.short_description = '# Destinatarios'

    def has_add_permission(self, request):
        return False
