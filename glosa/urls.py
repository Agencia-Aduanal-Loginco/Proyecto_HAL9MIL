from django.urls import path

from . import views

app_name = 'glosa'

# Va antes de referencias.urls en hal9mil/urls.py: num_refe lleva "/" y usa
# <path:>, así que la ruta de detalle de referencias capturaría .../glosa-ia/.
urlpatterns = [
    path('referencias/<path:num_refe>/glosa-ia/', views.glosa_ia_detalle, name='glosa_ia_detalle'),
    path('referencias/<path:num_refe>/glosa-ia/aprobar/', views.glosa_aprobar, name='glosa_aprobar'),
    path('referencias/<path:num_refe>/glosa-ia/rechazar/', views.glosa_rechazar, name='glosa_rechazar'),
    path('referencias/<path:num_refe>/glosa-ia/reabrir/', views.glosa_reabrir, name='glosa_reabrir'),
    path('glosa-ia/documentos/<int:doc_id>/actualizar/', views.glosa_documento_actualizar, name='documento_actualizar'),
    path('glosa-ia/documentos/<int:doc_id>/reemplazar/', views.glosa_documento_reemplazar, name='documento_reemplazar'),
    path('glosa-ia/documentos/<int:doc_id>/preforma-anotada/', views.glosa_preforma_anotada, name='preforma_anotada'),
]
