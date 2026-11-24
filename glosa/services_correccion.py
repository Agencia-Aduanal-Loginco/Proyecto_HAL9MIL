from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import DocumentoReferencia, GlosaAutomaticaResultado, GlosaEvento
from .services_jev_ia import comparar_preforma_documentos


class GlosaBloqueada(Exception):
    """Excepción lanzada cuando se intenta modificar una glosa con visto bueno (APROBADO)."""
    pass


@transaction.atomic
def reemplazar_documento(doc_anterior, archivo, usuario, motivo=''):
    """
    Reemplaza un documento activo por uno nuevo.
    - Valida que la glosa no esté APROBADO (lanza GlosaBloqueada).
    - Valida tamaño/extensión y que no sea el mismo archivo (ArchivoInvalido).
    - Crea el nuevo documento (activo=True) con el mismo tipo, estado MANUAL.
    - Archiva el documento anterior (activo=False, reemplazado_por=nuevo).
    - Si es PREFORMA, desactiva cualquier otra pre-forma activa.
    - Registra un GlosaEvento de tipo REEMPLAZO.
    - Programa extracción y recálculo (services_procesamiento; en segundo plano
      si GLOSA_PROCESAMIENTO_ASINCRONO).
    """
    from .services_procesamiento import (
        ArchivoInvalido, calcular_hash, programar_procesamiento, validar_archivo,
    )

    if not doc_anterior.activo:
        raise ValueError("Solo se pueden reemplazar documentos activos.")
    validar_archivo(archivo)
    hash_nuevo = calcular_hash(archivo)
    if doc_anterior.hash_sha256 and hash_nuevo == doc_anterior.hash_sha256:
        raise ArchivoInvalido("El archivo es idéntico al documento que se quiere reemplazar.")

    resultado, _ = GlosaAutomaticaResultado.objects.get_or_create(referencia=doc_anterior.referencia)
    if resultado.estado_revision == 'APROBADO':
        raise GlosaBloqueada("La glosa tiene visto bueno; un glosador debe reabrirla.")

    tipo = doc_anterior.tipo_documento

    nuevo = DocumentoReferencia.objects.create(
        referencia=doc_anterior.referencia,
        archivo=archivo,
        tipo_documento=tipo,
        activo=True,
        confianza_ia=Decimal('1.0000'),
        clasificacion_ia={'estado': 'MANUAL', 'origen': 'REEMPLAZO'},
        subido_por=usuario,
        hash_sha256=hash_nuevo,
    )

    doc_anterior.activo = False
    doc_anterior.reemplazado_por = nuevo
    doc_anterior.reemplazado_en = timezone.now()
    doc_anterior.motivo_reemplazo = motivo
    doc_anterior.save()

    if tipo == 'PREFORMA':
        archivar_preformas_anteriores(nuevo)

    GlosaEvento.objects.create(
        referencia=doc_anterior.referencia,
        tipo='REEMPLAZO',
        usuario=usuario,
        documento=nuevo,
        documento_anterior=doc_anterior,
        estado_resultante=resultado.estado_revision,
        comentario=motivo
    )

    return programar_procesamiento(
        nuevo, usuario=usuario, evento_origen='REEMPLAZO', clasificar=False, comentario=motivo,
    )


def glosa_aprobada(referencia):
    return GlosaAutomaticaResultado.objects.filter(
        referencia=referencia, estado_revision='APROBADO'
    ).exists()


def documentos_en_proceso(referencia):
    """Documentos activos cuya clasificación o extracción no ha terminado."""
    from .models import PROCESAMIENTO_PENDIENTE, PROCESAMIENTO_PROCESANDO
    return referencia.documentos_glosa.filter(
        activo=True, procesamiento_estado__in=(PROCESAMIENTO_PENDIENTE, PROCESAMIENTO_PROCESANDO),
    )


def archivar_preformas_anteriores(preforma_nueva):
    """Deja activa solo `preforma_nueva`; las demás pre-formas activas quedan como versiones anteriores."""
    anteriores = DocumentoReferencia.objects.filter(
        referencia=preforma_nueva.referencia, tipo_documento='PREFORMA', activo=True
    ).exclude(pk=preforma_nueva.pk)
    archivadas = list(anteriores)
    anteriores.update(
        activo=False,
        reemplazado_por=preforma_nueva,
        reemplazado_en=timezone.now(),
        motivo_reemplazo='Sustitución por nueva pre-forma',
    )
    return archivadas


# Eventos que cambian el expediente y por eso pueden sacar una glosa de RECHAZADO.
EVENTOS_CORRECCION = ('REEMPLAZO', 'SUBIDA', 'CAMBIO_TIPO')


def recalcular_glosa(referencia, usuario, evento_origen='CRUCE', doc_afectado=None, comentario=''):
    """
    Corre el cruce sobre los documentos activos y aplica las transiciones de estado:
    - APROBADO no cambia (solo un glosador lo reabre).
    - RECHAZADO solo sale si `evento_origen` es una corrección del expediente.
    Registra un GlosaEvento CRUCE; el evento de origen lo registra quien llama.
    """
    resultado, _ = GlosaAutomaticaResultado.objects.get_or_create(referencia=referencia)
    if resultado.estado_revision == 'APROBADO':
        return resultado

    documentos = list(referencia.documentos_glosa.filter(activo=True))
    comparacion = comparar_preforma_documentos(referencia, documentos)
    nuevo_estado = comparacion.get('estado', 'PENDIENTE')
    discrepancias = comparacion.get('discrepancias', [])

    if resultado.estado_revision == 'RECHAZADO' and evento_origen not in EVENTOS_CORRECCION:
        nuevo_estado = 'RECHAZADO'

    resultado.estado_revision = nuevo_estado
    resultado.datos_comparacion = comparacion
    resultado.save()

    GlosaEvento.objects.create(
        referencia=referencia,
        tipo='CRUCE',
        usuario=usuario,
        documento=doc_afectado,
        estado_resultante=nuevo_estado,
        discrepancias=discrepancias,
        comentario=comentario
    )
    return resultado
