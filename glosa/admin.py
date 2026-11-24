from django.contrib import admin
from .models import DocumentoReferencia, GlosaAutomaticaResultado, GlosaEvento

@admin.register(DocumentoReferencia)
class DocumentoReferenciaAdmin(admin.ModelAdmin):
    list_display = ('referencia', 'tipo_documento', 'confianza_ia', 'activo', 'procesamiento_estado', 'creado_en', 'subido_por')
    list_filter = ('tipo_documento', 'activo', 'procesamiento_estado', 'creado_en')
    search_fields = ('referencia__num_refe', 'hash_sha256')
    readonly_fields = ('creado_en', 'reemplazado_en', 'hash_sha256', 'procesamiento_inicio', 'procesamiento_error')

@admin.register(GlosaAutomaticaResultado)
class GlosaAutomaticaResultadoAdmin(admin.ModelAdmin):
    list_display = ('referencia', 'estado_revision', 'aprobado_por', 'aprobado_en', 'actualizado_en')
    list_filter = ('estado_revision',)
    search_fields = ('referencia__num_refe',)
    readonly_fields = ('actualizado_en',)

@admin.register(GlosaEvento)
class GlosaEventoAdmin(admin.ModelAdmin):
    list_display = ('referencia', 'tipo', 'usuario', 'estado_resultante', 'creado_en')
    list_filter = ('tipo', 'estado_resultante', 'creado_en')
    search_fields = ('referencia__num_refe',)
    readonly_fields = ('referencia', 'tipo', 'usuario', 'creado_en', 'documento', 'documento_anterior', 'estado_resultante', 'discrepancias', 'comentario')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
