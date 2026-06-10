from django.urls import path
from . import views

urlpatterns = [
    path('clientes/',                 views.lista,       name='clientes_lista'),
    path('clientes/reporte/',         views.reporte_pdf, name='clientes_reporte_pdf'),
    path('clientes/<path:nombre>/',   views.detalle,     name='clientes_detalle'),
]
