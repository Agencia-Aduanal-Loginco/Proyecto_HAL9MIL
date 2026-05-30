from django.urls import path
from . import views, sync_views

urlpatterns = [
    path('',                             views.dashboard,          name='dashboard'),
    path('referencias/',                 views.lista,              name='lista'),
    path('referencias/<path:num_refe>/', views.detalle,            name='detalle'),
    path('glosa/',                            views.glosa,              name='glosa'),
    path('glosa/dashboard/',                  views.glosa_dashboard,    name='glosa_dashboard'),
    path('glosa/urgente/<int:pk>/',           views.glosa_urgente,      name='glosa_urgente'),
    path('glosa/registrar/<int:pk>/',         views.glosa_registrar,    name='glosa_registrar'),
    path('glosa/concluir/<int:pk>/',          views.glosa_concluir,     name='glosa_concluir'),
    path('api/sync/',                    sync_views.sync_endpoint, name='api_sync'),
]
