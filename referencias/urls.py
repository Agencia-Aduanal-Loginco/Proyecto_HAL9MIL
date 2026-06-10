from django.urls import path
from . import views, sync_views

urlpatterns = [
    path('',                             views.dashboard,          name='dashboard'),
    path('referencias/',                 views.lista,                  name='lista'),
    path('referencias/dashboard/',       views.referencias_dashboard,  name='referencias_dashboard'),
    path('referencias/<path:num_refe>/timeline/', views.timeline, name='timeline'),
    path('referencias/<path:num_refe>/',          views.detalle,  name='detalle'),
    path('glosa/',                            views.glosa,              name='glosa'),
    path('glosa/dashboard/',                  views.glosa_dashboard,    name='glosa_dashboard'),
    path('glosa/urgente/<int:pk>/',           views.glosa_urgente,      name='glosa_urgente'),
    path('glosa/registrar/<int:pk>/',         views.glosa_registrar,    name='glosa_registrar'),
    path('glosa/concluir/<int:pk>/',          views.glosa_concluir,     name='glosa_concluir'),
    path('glosa/revertir/<int:pk>/',          views.glosa_revertir,     name='glosa_revertir'),
    path('glosa/eliminar/<int:pk>/',          views.glosa_eliminar,     name='glosa_eliminar'),
    path('cuenta-gastos/',                    views.cuenta_gastos,            name='cuenta_gastos'),
    path('cuenta-gastos/dashboard/',          views.cuenta_gastos_dashboard,  name='cuenta_gastos_dashboard'),
    path('cuenta-gastos/finalizar/<int:pk>/', views.cuenta_gastos_finalizar,  name='cuenta_gastos_finalizar'),
    path('sla/capturistas/',             views.sla_capturistas,    name='sla_capturistas'),
    path('api/sync/',                    sync_views.sync_endpoint, name='api_sync'),
]
