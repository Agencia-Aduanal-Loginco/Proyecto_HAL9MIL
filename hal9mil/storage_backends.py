import os
from django.conf import settings
from django.utils import timezone
from storages.backends.s3boto3 import S3Boto3Storage
from zoneinfo import ZoneInfo
import logging

logger = logging.getLogger(__name__)

# Zona horaria de México
MEXICO_TZ = ZoneInfo('America/Mexico_City')


def ahora_mexico():
    """Retorna datetime actual en zona horaria de México"""
    return timezone.now().astimezone(MEXICO_TZ)

class StaticStorage(S3Boto3Storage):
    """Storage personalizado para archivos estáticos"""
    location = 'static'
    default_acl = 'public-read'
    file_overwrite = True  # Los archivos estáticos pueden sobreescribirse
    
    def __init__(self, *args, **kwargs):
        kwargs['custom_domain'] = getattr(settings, 'AWS_S3_CUSTOM_DOMAIN', None)
        super().__init__(*args, **kwargs)
        logger.info("💫 StaticStorage inicializado para DigitalOcean Spaces")

class MediaStorage(S3Boto3Storage):
    """Storage personalizado para archivos media (fotos de tickets)"""
    location = 'media'
    default_acl = 'public-read'
    file_overwrite = False  # No sobreescribir archivos media
    
    def __init__(self, *args, **kwargs):
        kwargs['custom_domain'] = getattr(settings, 'AWS_S3_CUSTOM_DOMAIN', None)
        super().__init__(*args, **kwargs)
        logger.info("📸 MediaStorage inicializado para DigitalOcean Spaces")
    
    def _save(self, name, content):
        """Personaliza el guardado de archivos media"""
        # Agregar timestamp para evitar colisiones
        import os

        # Separar nombre y extensión
        base_name, ext = os.path.splitext(name)

        # Agregar timestamp (hora de México)
        timestamp = ahora_mexico().strftime('%Y%m%d_%H%M%S')
        new_name = f"{base_name}_{timestamp}{ext}"

        logger.info(f"💾 Guardando archivo media: {new_name}")
        return super()._save(new_name, content)

class ReportesStorage(S3Boto3Storage):
    """Storage específico para archivos de reportes"""
    location = 'reportes'
    default_acl = 'private'  # Los reportes son privados por defecto
    file_overwrite = True
    
    def __init__(self, *args, **kwargs):
        kwargs['custom_domain'] = getattr(settings, 'AWS_S3_CUSTOM_DOMAIN', None)
        super().__init__(*args, **kwargs)
        logger.info("📊 ReportesStorage inicializado para DigitalOcean Spaces")
    
    def get_valid_name(self, name):
        """Genera nombres válidos para archivos de reportes"""
        # Limpiar caracteres especiales
        import re
        name = re.sub(r'[^\w\-_\.]', '_', name)
        return super().get_valid_name(name)

class SecureMediaStorage(MediaStorage):
    """Storage para archivos media que requieren autenticación"""
    default_acl = 'private'
    querystring_auth = True
    querystring_expire = 3600  # URLs expiran en 1 hora
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logger.info("🔐 SecureMediaStorage inicializado para archivos privados")

# === UTILIDADES PARA MANEJO DE ARCHIVOS ===

def upload_ticket_photo(instance, filename):
    """
    Función para generar rutas personalizadas para fotos de tickets
    Uso en models.py: upload_to=upload_ticket_photo
    """
    import os

    # Obtener información del registro (hora de México)
    fecha = instance.fecha_hora or ahora_mexico()
    placa = getattr(instance.idEquipo, 'placa', 'SIN_PLACA') if instance.idEquipo else 'SIN_EQUIPO'

    # Limpiar placa para usar en nombre de archivo
    placa_clean = ''.join(c for c in placa if c.isalnum() or c in '-_')

    # Generar nombre de archivo
    nombre, ext = os.path.splitext(filename)
    nuevo_nombre = f"ticket_{placa_clean}_{fecha.strftime('%Y%m%d_%H%M%S')}{ext}"

    # Estructura de carpetas: tickets/año/mes/
    ruta = f"tickets/{fecha.year}/{fecha.month:02d}/{nuevo_nombre}"

    logger.info(f"📸 Generando ruta para foto de ticket: {ruta}")
    return ruta

def upload_reporte_excel(filename):
    """
    Función para generar rutas para archivos de reportes Excel
    """
    # Generar ruta con timestamp (hora de México)
    fecha_mx = ahora_mexico()
    timestamp = fecha_mx.strftime('%Y%m%d_%H%M%S')
    nombre, ext = os.path.splitext(filename)
    nuevo_nombre = f"{nombre}_{timestamp}{ext}"

    # Estructura: reportes/excel/año/
    ruta = f"reportes/excel/{fecha_mx.year}/{nuevo_nombre}"

    logger.info(f"📊 Generando ruta para reporte Excel: {ruta}")
    return ruta

# === FUNCIONES DE UTILIDAD ===

def get_file_url(file_field):
    """
    Obtiene la URL completa de un archivo, manejando tanto storage local como Spaces
    """
    if not file_field:
        return None
    
    try:
        if hasattr(file_field, 'url'):
            return file_field.url
        else:
            return None
    except Exception as e:
        logger.error(f"❌ Error obteniendo URL de archivo: {e}")
        return None

def delete_file_from_storage(file_path, storage_class=MediaStorage):
    """
    Elimina un archivo del storage de manera segura
    """
    try:
        storage = storage_class()
        if storage.exists(file_path):
            storage.delete(file_path)
            logger.info(f"🗑️ Archivo eliminado: {file_path}")
            return True
        else:
            logger.warning(f"⚠️ Archivo no encontrado para eliminar: {file_path}")
            return False
    except Exception as e:
        logger.error(f"❌ Error eliminando archivo {file_path}: {e}")
        return False

def copy_file_to_reportes_storage(source_file, destination_name):
    """
    Copia un archivo al storage de reportes
    """
    try:
        media_storage = MediaStorage()
        reportes_storage = ReportesStorage()
        
        # Leer archivo fuente
        file_content = media_storage.open(source_file.name).read()
        
        # Guardar en storage de reportes
        saved_path = reportes_storage.save(destination_name, file_content)
        
        logger.info(f"📋 Archivo copiado a reportes: {saved_path}")
        return saved_path
    except Exception as e:
        logger.error(f"❌ Error copiando archivo a reportes: {e}")
        return None

def optimize_image_for_storage(image_file, max_size=(1920, 1080), quality=85):
    """
    Optimiza una imagen antes de subirla al storage
    """
    try:
        from PIL import Image
        from io import BytesIO
        import os
        
        # Abrir imagen
        img = Image.open(image_file)
        
        # Convertir a RGB si es necesario
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
        
        # Redimensionar si es necesario
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        # Guardar optimizada
        output = BytesIO()
        img.save(output, format='JPEG', quality=quality, optimize=True)
        output.seek(0)
        
        # Calcular reducción de tamaño
        original_size = image_file.size
        new_size = len(output.getvalue())
        reduction = ((original_size - new_size) / original_size) * 100
        
        logger.info(f"🖼️ Imagen optimizada: {reduction:.1f}% de reducción")
        
        return output
    except Exception as e:
        logger.error(f"❌ Error optimizando imagen: {e}")
        return image_file

# === MIDDLEWARE PARA MANEJO DE ARCHIVOS ===

class FileUploadMiddleware:
    """
    Middleware para manejar uploads de archivos grandes y optimización
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Procesar archivos antes de la vista
        if request.method == 'POST' and request.FILES:
            for field_name, file_obj in request.FILES.items():
                if file_obj.content_type.startswith('image/'):
                    # Optimizar imágenes automáticamente
                    optimized = optimize_image_for_storage(file_obj)
                    if optimized != file_obj:
                        request.FILES[field_name] = optimized
                        logger.info(f"🔧 Imagen optimizada automáticamente: {field_name}")
        
        response = self.get_response(request)
        return response

# === SEÑALES PARA MANEJO AUTOMÁTICO ===

from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

@receiver(post_delete)
def delete_file_on_model_delete(sender, instance, **kwargs):
    """
    Elimina archivos del storage cuando se elimina un modelo
    """
    # Buscar campos de archivo en el modelo
    for field in instance._meta.fields:
        if hasattr(field, 'upload_to'):
            file_field = getattr(instance, field.name)
            if file_field:
                delete_file_from_storage(file_field.name)

@receiver(pre_save)
def delete_old_file_on_change(sender, instance, **kwargs):
    """
    Elimina archivo anterior cuando se actualiza con uno nuevo
    """
    if not instance.pk:
        return  # Es un nuevo objeto, no hay archivo anterior
    
    try:
        old_instance = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return
    
    # Verificar campos de archivo
    for field in instance._meta.fields:
        if hasattr(field, 'upload_to'):
            old_file = getattr(old_instance, field.name)
            new_file = getattr(instance, field.name)
            
            if old_file and old_file != new_file:
                delete_file_from_storage(old_file.name)