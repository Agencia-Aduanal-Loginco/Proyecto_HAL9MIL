from django.contrib import admin

from core.models import PerfilUsuario


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display = ('user', 'cve_capturista', 'email_alterno')
    list_filter = ('user',)
    search_fields = ('cve_capturista', 'user__username', 'user__email', 'email_alterno')
    readonly_fields = ('user',)
    fields = ('user', 'cve_capturista', 'email_alterno')
