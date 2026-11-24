"""Procesamiento de documentos del expediente: validación, hash, clasificación,
extracción y recálculo de la glosa.

La clasificación (Jev) y la extracción (Claude) pueden tardar más que el
timeout de gunicorn con una pre-forma larga, así que por defecto corren en un
hilo después de que el request termina (GLOSA_PROCESAMIENTO_ASINCRONO=True).
El documento queda en procesamiento_estado PENDIENTE/PROCESANDO y la página de
detalle se recarga sola hasta que queda LISTO o ERROR.

Si el proceso web se reinicia a mitad, el documento queda atorado en
PROCESANDO; el job `reprocesar_atorados` (APScheduler, cada 5 minutos) y el
comando `glosa_reprocesar` lo retoman.
"""
import hashlib
import logging
import os
import threading
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from .models import (
    PROCESAMIENTO_ERROR, PROCESAMIENTO_LISTO, PROCESAMIENTO_PENDIENTE,
    PROCESAMIENTO_PROCESANDO, DocumentoReferencia, GlosaAutomaticaResultado,
    GlosaEvento,
)
from .services_correccion import archivar_preformas_anteriores, recalcular_glosa
from .services_extraccion import (
    ESTADO_OK as EXTRACCION_OK,
    ESTADO_REVISION as EXTRACCION_REVISION,
    TIPOS_SOPORTADOS, extraccion_vigente, extraer_datos_documento,
)
from .services_jev_ia import clasificar_documento

logger = logging.getLogger(__name__)

EXTENSIONES_PERMITIDAS = ('.pdf', '.jpg', '.jpeg', '.png', '.xml', '.txt')
MAX_MB_DEFAULT = 25
MINUTOS_ATORADO_DEFAULT = 10


class ArchivoInvalido(Exception):
    """El archivo no cumple tamaño o extensión permitidos."""


# ---------------------------------------------------------------------------
# Validación y hash
# ---------------------------------------------------------------------------

def max_bytes():
    return int(getattr(settings, 'GLOSA_MAX_MB', MAX_MB_DEFAULT)) * 1024 * 1024


def validar_archivo(archivo):
    nombre = getattr(archivo, 'name', '') or ''
    ext = os.path.splitext(nombre)[1].lower()
    if ext not in EXTENSIONES_PERMITIDAS:
        raise ArchivoInvalido(
            f'{nombre}: tipo de archivo no permitido. Use {", ".join(EXTENSIONES_PERMITIDAS)}.'
        )
    tamano = getattr(archivo, 'size', None)
    if tamano is not None and tamano > max_bytes():
        raise ArchivoInvalido(
            f'{nombre}: pesa {tamano / 1024 / 1024:.1f} MB; el máximo es '
            f'{getattr(settings, "GLOSA_MAX_MB", MAX_MB_DEFAULT)} MB.'
        )
    if tamano == 0:
        raise ArchivoInvalido(f'{nombre}: el archivo está vacío.')


def calcular_hash(archivo):
    h = hashlib.sha256()
    if hasattr(archivo, 'chunks'):
        try:
            archivo.seek(0)
        except Exception:
            pass
        for chunk in archivo.chunks():
            h.update(chunk)
        try:
            archivo.seek(0)
        except Exception:
            pass
    else:
        h.update(archivo.read())
        archivo.seek(0)
    return h.hexdigest()


def duplicado_activo(referencia, hash_sha256):
    """Documento activo de la misma referencia con el mismo contenido."""
    if not hash_sha256:
        return None
    return referencia.documentos_glosa.filter(activo=True, hash_sha256=hash_sha256).first()


def extraccion_reutilizable(doc):
    """Otro documento con el mismo archivo y tipo, con extracción válida."""
    if not doc.hash_sha256:
        return None
    candidatos = DocumentoReferencia.objects.filter(
        hash_sha256=doc.hash_sha256, tipo_documento=doc.tipo_documento,
    ).exclude(pk=doc.pk).order_by('-creado_en')
    for otro in candidatos:
        meta = (otro.datos_extraidos or {}).get('meta') or {}
        if meta.get('tipo') == doc.tipo_documento and meta.get('estado') in (EXTRACCION_OK, EXTRACCION_REVISION):
            return otro
    return None


# ---------------------------------------------------------------------------
# Procesamiento
# ---------------------------------------------------------------------------

def _extraer(doc):
    """Extrae datos del documento; reutiliza si ya se leyó el mismo archivo."""
    if doc.tipo_documento not in TIPOS_SOPORTADOS:
        doc.datos_extraidos = {}
        return
    if extraccion_vigente(doc):
        return
    origen = extraccion_reutilizable(doc)
    if origen:
        datos = dict(origen.datos_extraidos)
        meta = dict(datos.get('meta') or {})
        meta['reutilizado_de'] = origen.pk
        meta['uso'] = {'input_tokens': 0, 'output_tokens': 0, 'costo_usd': None}
        datos['meta'] = meta
        doc.datos_extraidos = datos
        return
    doc.datos_extraidos = extraer_datos_documento(doc.archivo, doc.tipo_documento)


def procesar_documento(doc_id, usuario_id=None, evento_origen='SUBIDA', clasificar=True,
                       registrar_evento=False, comentario=''):
    """Clasifica (si aplica), extrae, archiva pre-formas, registra evento y recalcula.

    Nunca lanza excepción: un fallo deja el documento en ERROR con el mensaje.
    """
    from django.contrib.auth import get_user_model
    try:
        doc = DocumentoReferencia.objects.select_related('referencia').get(pk=doc_id)
    except DocumentoReferencia.DoesNotExist:
        return None
    usuario = get_user_model().objects.filter(pk=usuario_id).first() if usuario_id else None

    DocumentoReferencia.objects.filter(pk=doc.pk).update(
        procesamiento_estado=PROCESAMIENTO_PROCESANDO, procesamiento_inicio=timezone.now(),
    )
    try:
        if clasificar:
            clasificacion = clasificar_documento(doc.archivo)
            doc.tipo_documento = clasificacion.tipo
            doc.confianza_ia = clasificacion.confianza
            detalle = dict(clasificacion.detalle)
            if detalle.get('probabilidades') is not None:
                # Tipo que dejó Jev, para medir su precisión contra el tipo final.
                detalle['tipo_ia'] = clasificacion.tipo
            doc.clasificacion_ia = detalle
        _extraer(doc)
        doc.procesamiento_estado = PROCESAMIENTO_LISTO
        doc.procesamiento_error = ''
        doc.save()

        if doc.tipo_documento == 'PREFORMA' and doc.activo:
            archivar_preformas_anteriores(doc)
        if registrar_evento:
            GlosaEvento.objects.create(
                referencia=doc.referencia,
                tipo=evento_origen,
                usuario=usuario,
                documento=doc,
                estado_resultante=GlosaAutomaticaResultado.objects.get_or_create(
                    referencia=doc.referencia)[0].estado_revision,
                comentario=comentario,
            )
        recalcular_glosa(
            referencia=doc.referencia, usuario=usuario, evento_origen=evento_origen,
            doc_afectado=doc, comentario=comentario,
        )
    except Exception as e:  # noqa: BLE001 - se registra y se muestra en la página
        logger.exception('Glosa: error procesando documento %s', doc_id)
        DocumentoReferencia.objects.filter(pk=doc_id).update(
            procesamiento_estado=PROCESAMIENTO_ERROR, procesamiento_error=str(e)[:1000],
        )
        doc.procesamiento_estado = PROCESAMIENTO_ERROR
        doc.procesamiento_error = str(e)[:1000]
    return doc


def asincrono():
    return bool(getattr(settings, 'GLOSA_PROCESAMIENTO_ASINCRONO', True))


def _en_hilo(kwargs):
    try:
        procesar_documento(**kwargs)
    finally:
        close_old_connections()


def programar_procesamiento(doc, usuario=None, evento_origen='SUBIDA', clasificar=True,
                            registrar_evento=False, comentario=''):
    """Marca el documento como PENDIENTE y lo procesa en un hilo (o en línea)."""
    DocumentoReferencia.objects.filter(pk=doc.pk).update(
        procesamiento_estado=PROCESAMIENTO_PENDIENTE, procesamiento_inicio=timezone.now(),
        procesamiento_error='',
    )
    doc.procesamiento_estado = PROCESAMIENTO_PENDIENTE
    kwargs = {
        'doc_id': doc.pk,
        'usuario_id': getattr(usuario, 'pk', None),
        'evento_origen': evento_origen,
        'clasificar': clasificar,
        'registrar_evento': registrar_evento,
        'comentario': comentario,
    }
    if not asincrono():
        return procesar_documento(**kwargs)
    transaction.on_commit(
        lambda: threading.Thread(target=_en_hilo, args=(kwargs,), daemon=True,
                                 name=f'glosa-doc-{doc.pk}').start()
    )
    return doc


def crear_documento(referencia, archivo, usuario, tipo_manual=None):
    """Crea el DocumentoReferencia de una subida. No procesa."""
    hash_sha256 = calcular_hash(archivo)
    doc = DocumentoReferencia(
        referencia=referencia, archivo=archivo, subido_por=usuario, hash_sha256=hash_sha256,
        procesamiento_estado=PROCESAMIENTO_PENDIENTE,
    )
    if tipo_manual:
        doc.tipo_documento = tipo_manual
        doc.confianza_ia = Decimal('1.0000')
        doc.clasificacion_ia = {'estado': 'MANUAL'}
    doc.save()
    return doc


def reprocesar_atorados(minutos=None):
    """Retoma documentos que quedaron PENDIENTE/PROCESANDO por un reinicio."""
    minutos = minutos or int(getattr(settings, 'GLOSA_MINUTOS_ATORADO', MINUTOS_ATORADO_DEFAULT))
    limite = timezone.now() - timedelta(minutes=minutos)
    atorados = list(DocumentoReferencia.objects.filter(
        procesamiento_estado__in=(PROCESAMIENTO_PENDIENTE, PROCESAMIENTO_PROCESANDO),
        procesamiento_inicio__lt=limite,
    ).values_list('pk', 'clasificacion_ia', 'subido_por_id'))
    for pk, clasif, usuario_id in atorados:
        manual = (clasif or {}).get('estado') == 'MANUAL'
        # El evento de origen (SUBIDA, REEMPLAZO, CAMBIO_TIPO) se registra antes
        # de procesar; se usa para aplicar las mismas transiciones de estado.
        evento = GlosaEvento.objects.filter(documento_id=pk).exclude(tipo='CRUCE').order_by('-creado_en').first()
        logger.warning('Glosa: reprocesando documento atorado %s', pk)
        procesar_documento(
            pk, usuario_id=usuario_id, evento_origen=evento.tipo if evento else 'SUBIDA',
            clasificar=not manual, registrar_evento=False,
        )
    return len(atorados)
