from django.contrib import admin

from core.models import PerfilUsuario


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display = ('user', 'cve_capturista', 'email_alterno')
    list_filter = ('user',)
    search_fields = ('cve_capturista', 'user__username', 'user__email', 'email_alterno')
    fields = ('user', 'cve_capturista', 'email_alterno')

    def get_readonly_fields(self, request, obj=None):
        """Make 'user' editable only on add, readonly on change."""
        if obj is not None:
            # Changing existing profile
            return ('user',)
        # Adding new profile
        return ()
