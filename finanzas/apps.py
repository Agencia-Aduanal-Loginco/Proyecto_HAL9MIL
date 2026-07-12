from django.apps import AppConfig


class FinanzasConfig(AppConfig):
    name = 'finanzas'

    def ready(self):
        """
        Conecta las señales de borrado automático de archivos de
        XMLProveedor aquí (no como @receiver en hal9mil/storage_backends.py)
        porque hal9mil.storage_backends se importa DESDE finanzas.models
        (finanzas/models.py:7 hace `from hal9mil.storage_backends import
        media_storage`), antes de que la clase XMLProveedor exista todavía
        en ese módulo. Conectar con sender=XMLProveedor en tiempo de
        decoración ahí causaría un ImportError circular. AppConfig.ready()
        corre después de que todas las apps y modelos están completamente
        cargados, así que aquí el import es seguro.
        """
        from django.db.models.signals import post_delete, pre_save

        from .models import XMLProveedor
        from hal9mil.storage_backends import (
            delete_file_on_model_delete, delete_old_file_on_change,
        )

        post_delete.connect(delete_file_on_model_delete, sender=XMLProveedor)
        pre_save.connect(delete_old_file_on_change, sender=XMLProveedor)
