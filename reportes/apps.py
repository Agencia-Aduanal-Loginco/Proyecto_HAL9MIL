from django.apps import AppConfig


class ReportesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'reportes'
    verbose_name = 'Reportes Automáticos'

    def ready(self):
        from .scheduler import start
        start()
