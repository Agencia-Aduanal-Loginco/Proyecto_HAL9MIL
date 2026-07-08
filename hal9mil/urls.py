from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('whatsapp/', include('whatsapp.urls')),
    path('', include('referencias.urls')),
    path('', include('clientes.urls')),
    path('finanzas/', include('finanzas.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
