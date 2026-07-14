from django.contrib import admin
from .models import Cliente


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display  = ('nombre_cliente', 'cve_cliente', 'rfc', 'email_cobranza')
    search_fields = ('nombre_cliente', 'rfc', 'cve_cliente')
    ordering      = ('nombre_cliente',)
    fieldsets = (
        (None, {'fields': ('nombre_cliente', 'cve_cliente', 'rfc')}),
        ('Cobranza', {'fields': ('email_cobranza', 'email_cobranza_cc')}),
        ('Cuenta de gastos', {'fields': ('email_cuenta_gastos', 'email_cuenta_gastos_cc')}),
    )
