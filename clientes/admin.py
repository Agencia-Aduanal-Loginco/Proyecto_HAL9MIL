from django.contrib import admin

from .models import Cliente


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display  = ('nombre_cliente', 'cve_cliente', 'rfc')
    search_fields = ('nombre_cliente', 'rfc', 'cve_cliente')
    ordering      = ('nombre_cliente',)
