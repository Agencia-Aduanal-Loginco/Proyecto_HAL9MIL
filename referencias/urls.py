from django.urls import path
from . import views, sync_views

urlpatterns = [
    path('',                             views.dashboard,          name='dashboard'),
    path('referencias/',                 views.lista,              name='lista'),
    path('referencias/<path:num_refe>/', views.detalle,            name='detalle'),
    path('glosa/',                       views.glosa,              name='glosa'),
    path('api/sync/',                    sync_views.sync_endpoint, name='api_sync'),
]
