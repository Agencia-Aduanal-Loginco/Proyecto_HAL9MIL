from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from referencias.models import GlosaRegistro, Referencia

from .models import DocumentoReferencia, GlosaAutomaticaResultado, GlosaEvento
from .permisos import es_glosador
from .services_aaalac import AAALACError, aaalac_habilitado, obtener_eta_mercancia
from .services_anotacion import generar_preforma_anotada
from .services_correccion import (
    GlosaBloqueada, documentos_en_proceso, glosa_aprobada, reemplazar_documento,
)
from .services_extraccion import (
    ESTADO_ERROR as EXTRACCION_ERROR,
    ESTADO_REVISION as EXTRACCION_REVISION,
    ESTADO_SIN_IA as EXTRACCION_SIN_IA,
    TIPOS_SOPORTADOS,
)
from .services_jev_ia import (
    ESTADO_ERROR, ESTADO_MANUAL, ESTADO_REVISION, ESTADO_SIN_IA,
    comparar_preforma_documentos,
)
from .services_procesamiento import (
    EXTENSIONES_PERMITIDAS, ArchivoInvalido, asincrono, calcular_hash, crear_documento, duplicado_activo,
    programar_procesamiento, validar_archivo,
)

MENSAJE_GLOSA_BLOQUEADA = "La glosa tiene visto bueno; un glosador debe reabrirla."


def _redirect_detalle(num_refe):
    return redirect('glosa:glosa_ia_detalle', num_refe=num_refe)


def _registrar_evento_origen(doc, usuario, tipo_evento, comentario=''):
    GlosaEvento.objects.create(
        referencia=doc.referencia,
        tipo=tipo_evento,
        usuario=usuario,
        documento=doc,
        estado_resultante=GlosaAutomaticaResultado.objects.get_or_create(
            referencia=doc.referencia)[0].estado_revision,
        comentario=comentario,
    )


@login_required
@require_http_methods(['GET', 'POST'])
def glosa_ia_detalle(request, num_refe):
    ref = get_object_or_404(Referencia, num_refe=num_refe)
    resultado, _ = GlosaAutomaticaResultado.objects.get_or_create(referencia=ref)

    if request.method == 'POST':
        if 'consultar_aaalac' in request.POST:
            _consultar_aaalac(request, ref, resultado)
        else:
            _subir_documentos(request, ref, resultado)
        return _redirect_detalle(ref.num_refe)

    documentos = (
        ref.documentos_glosa.filter(activo=True)
        .select_related('subido_por')
        .prefetch_related('versiones_anteriores', 'versiones_anteriores__subido_por')
        .order_by('creado_en')
    )
    preforma = ref.documentos_glosa.filter(tipo_documento='PREFORMA', activo=True).first()
    en_proceso = any(d.en_proceso for d in documentos)
    return render(request, 'glosa/glosa_ia_detalle.html', {
        'ref': ref,
        'resultado': resultado,
        'documentos': documentos,
        'preforma': preforma,
        'tipo_choices': DocumentoReferencia.TIPO_CHOICES,
        'es_glosador': es_glosador(request.user),
        'eventos': ref.eventos_glosa.select_related('usuario').all()[:200],
        'is_aprobado': resultado.estado_revision == 'APROBADO',
        'en_proceso': en_proceso,
        'aaalac_habilitado': aaalac_habilitado(),
        'extensiones': ','.join(EXTENSIONES_PERMITIDAS),
        'max_mb': getattr(settings, 'GLOSA_MAX_MB', 25),
    })


def _subir_documentos(request, ref, resultado):
    """Cualquier usuario autenticado puede subir; se bloquea si la glosa está aprobada."""
    archivos = request.FILES.getlist('archivo')
    if not archivos:
        messages.error(request, "Seleccione al menos un archivo.")
        return
    if resultado.estado_revision == 'APROBADO':
        messages.error(request, MENSAJE_GLOSA_BLOQUEADA)
        return

    tipo_manual = request.POST.get('tipo_documento') or None
    if tipo_manual not in dict(DocumentoReferencia.TIPO_CHOICES):
        tipo_manual = None

    vistos = set()
    for archivo in archivos:
        try:
            validar_archivo(archivo)
        except ArchivoInvalido as e:
            messages.error(request, str(e))
            continue
        hash_sha256 = calcular_hash(archivo)
        existente = duplicado_activo(ref, hash_sha256)
        if existente or hash_sha256 in vistos:
            nombre = existente.get_tipo_documento_display() if existente else 'otro archivo de esta subida'
            messages.warning(request, f"{archivo.name}: ya está en el expediente ({nombre}); no se subió de nuevo.")
            continue
        vistos.add(hash_sha256)

        with transaction.atomic():
            doc = crear_documento(ref, archivo, request.user, tipo_manual=tipo_manual)
            _registrar_evento_origen(doc, request.user, 'SUBIDA')
            doc = programar_procesamiento(
                doc, usuario=request.user, evento_origen='SUBIDA', clasificar=tipo_manual is None,
            )

        if asincrono():
            messages.info(
                request,
                f"{archivo.name}: recibido. Se está clasificando y leyendo; la página se actualiza sola."
            )
        else:
            _mensajes_resultado(request, doc, archivo.name)


def _consultar_aaalac(request, ref, resultado):
    if not aaalac_habilitado():
        messages.error(request, "La consulta a AAALAC no está configurada.")
        return
    bl_doc = ref.documentos_glosa.filter(tipo_documento='BL', activo=True).first()
    datos_bl = (bl_doc.datos_extraidos or {}) if bl_doc else {}
    try:
        eta_data = obtener_eta_mercancia(
            num_pedimento=ref.num_pedimento, buque=datos_bl.get('buque'),
            viaje=datos_bl.get('viaje'), referencia=ref.num_refe,
        )
    except AAALACError as e:
        messages.error(request, str(e))
        return
    if isinstance(eta_data, dict) and eta_data.get('fecha_llegada'):
        try:
            resultado.eta_aaalac = datetime.strptime(eta_data['fecha_llegada'], '%Y-%m-%d').date()
            resultado.save(update_fields=['eta_aaalac', 'actualizado_en'])
            messages.success(request, "Consulta AAALAC Fonatrac realizada.")
            return
        except ValueError:
            pass
    messages.warning(request, "AAALAC respondió sin fecha de llegada.")


def _mensajes_resultado(request, doc, nombre=''):
    prefijo = f"{nombre}: " if nombre else ""
    if doc.procesamiento_estado == 'ERROR':
        messages.error(request, f"{prefijo}error al procesar: {doc.procesamiento_error}")
        return
    estado_clasif = doc.estado_clasificacion
    tipo = doc.get_tipo_documento_display()
    if estado_clasif == ESTADO_ERROR:
        messages.warning(
            request,
            f"{prefijo}Jev IA no pudo clasificar el documento ({doc.clasificacion_ia.get('error', '')}). "
            f"Sugerencia por palabras clave: {tipo}. Confirma el tipo."
        )
    elif estado_clasif == ESTADO_SIN_IA:
        messages.warning(
            request, f"{prefijo}Jev IA no está configurado. Sugerencia por palabras clave: {tipo}. Confirma el tipo."
        )
    elif estado_clasif == ESTADO_REVISION:
        messages.warning(
            request,
            f"{prefijo}clasificado como {tipo} con baja certeza (probabilidad {doc.confianza_ia}). Revisa el tipo."
        )
    elif estado_clasif == ESTADO_MANUAL:
        messages.success(request, f"{prefijo}subido como {tipo}.")
    else:
        confianza = f" (probabilidad {doc.confianza_ia})" if doc.confianza_ia is not None else ""
        messages.success(request, f"{prefijo}subido y clasificado como {tipo}{confianza}.")
    _mensaje_extraccion(request, doc, prefijo)


def _mensaje_extraccion(request, doc, prefijo=''):
    meta = (doc.datos_extraidos or {}).get('meta') or {}
    estado = meta.get('estado')
    if estado == EXTRACCION_ERROR:
        messages.warning(request, f"{prefijo}no se pudieron extraer los datos: {meta.get('error', '')}")
    elif estado == EXTRACCION_REVISION:
        messages.warning(
            request, f"{prefijo}datos extraídos con observaciones: " + '; '.join(meta.get('advertencias') or [])
        )
    elif estado == EXTRACCION_SIN_IA:
        messages.info(request, f"{prefijo}extracción de datos desactivada (IA_HABILITADA / ANTHROPIC_API_KEY).")


@login_required
@require_http_methods(['POST'])
def glosa_documento_actualizar(request, doc_id):
    """Cambio manual de tipo de documento."""
    doc = get_object_or_404(DocumentoReferencia.objects.select_related('referencia'), pk=doc_id)
    num_refe = doc.referencia.num_refe
    if glosa_aprobada(doc.referencia):
        messages.error(request, MENSAJE_GLOSA_BLOQUEADA)
        return _redirect_detalle(num_refe)
    if not doc.activo:
        messages.error(request, "Solo se puede cambiar el tipo de documentos activos.")
        return _redirect_detalle(num_refe)
    if doc.en_proceso:
        messages.error(request, "El documento se está procesando; espere a que termine.")
        return _redirect_detalle(num_refe)

    nuevo_tipo = request.POST.get('tipo_documento')
    if nuevo_tipo not in dict(DocumentoReferencia.TIPO_CHOICES):
        messages.error(request, "Tipo de documento no válido.")
        return _redirect_detalle(num_refe)

    anterior = doc.clasificacion_ia or {}
    doc.tipo_documento = nuevo_tipo
    doc.confianza_ia = Decimal('1.0000')
    doc.clasificacion_ia = {
        'estado': ESTADO_MANUAL,
        'sugerencia_ia': anterior.get('sugerencia') or anterior.get('sugerencia_ia'),
        # Se conserva el tipo que dejó Jev para medir su precisión.
        'tipo_ia': anterior.get('tipo_ia'),
    }
    if nuevo_tipo not in TIPOS_SOPORTADOS:
        doc.datos_extraidos = {}
    doc.save()

    with transaction.atomic():
        _registrar_evento_origen(doc, request.user, 'CAMBIO_TIPO')
        # Re-extrae solo si no hay extracción vigente para el tipo nuevo
        # (services_procesamiento._extraer) y siempre recalcula el cruce.
        doc = programar_procesamiento(doc, usuario=request.user, evento_origen='CAMBIO_TIPO', clasificar=False)

    messages.success(request, f"Documento actualizado a {doc.get_tipo_documento_display()}.")
    if not asincrono():
        _mensaje_extraccion(request, doc)
    return _redirect_detalle(num_refe)


@login_required
@require_http_methods(['POST'])
def glosa_documento_reemplazar(request, doc_id):
    doc_anterior = get_object_or_404(DocumentoReferencia.objects.select_related('referencia'), pk=doc_id)
    num_refe = doc_anterior.referencia.num_refe
    if not doc_anterior.activo:
        messages.error(request, "Solo se pueden reemplazar documentos activos.")
        return _redirect_detalle(num_refe)
    if doc_anterior.en_proceso:
        messages.error(request, "El documento se está procesando; espere a que termine.")
        return _redirect_detalle(num_refe)

    archivo = request.FILES.get('archivo')
    if not archivo:
        messages.error(request, "Debe seleccionar un archivo para reemplazar.")
        return _redirect_detalle(num_refe)

    motivo = request.POST.get('motivo', '').strip()
    try:
        nuevo = reemplazar_documento(doc_anterior, archivo, request.user, motivo=motivo)
    except (GlosaBloqueada, ArchivoInvalido) as e:
        messages.error(request, str(e))
        return _redirect_detalle(num_refe)

    messages.success(request, f"Documento {doc_anterior.get_tipo_documento_display()} reemplazado.")
    if not asincrono():
        _mensaje_extraccion(request, nuevo)
    return _redirect_detalle(num_refe)


def nombre_pdf_anotado(num_refe):
    """`LCRR0881/26` -> `preforma_observaciones_LCRR0881-26.pdf`."""
    seguro = ''.join(c if c.isalnum() or c in '-_' else '-' for c in (num_refe or ''))
    return f"preforma_observaciones_{seguro}.pdf"


@login_required
@require_http_methods(['GET'])
def glosa_preforma_anotada(request, doc_id):
    doc = get_object_or_404(
        DocumentoReferencia.objects.select_related('referencia'), pk=doc_id, tipo_documento='PREFORMA',
    )
    resultado, _ = GlosaAutomaticaResultado.objects.get_or_create(referencia=doc.referencia)
    discrepancias = (resultado.datos_comparacion or {}).get('discrepancias', [])

    try:
        doc.archivo.open('rb')
        pdf_bytes = doc.archivo.read()
        doc.archivo.close()
    except Exception:
        raise Http404("No se pudo leer el archivo de la pre-forma.")

    try:
        anotado_bytes = generar_preforma_anotada(pdf_bytes, discrepancias, doc.referencia)
    except Exception:
        raise Http404("La pre-forma no es un PDF válido.")
    response = HttpResponse(anotado_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{nombre_pdf_anotado(doc.referencia.num_refe)}"'
    return response


@login_required
@require_http_methods(['POST'])
def glosa_aprobar(request, num_refe):
    if not es_glosador(request.user):
        messages.error(request, "No tiene permisos de glosador.")
        return _redirect_detalle(num_refe)

    ref = get_object_or_404(Referencia, num_refe=num_refe)
    resultado, _ = GlosaAutomaticaResultado.objects.get_or_create(referencia=ref)
    if resultado.estado_revision != 'EN_REVISION':
        messages.error(request, "Solo se puede dar visto bueno a una glosa en revisión.")
        return _redirect_detalle(ref.num_refe)
    if documentos_en_proceso(ref).exists():
        messages.error(request, "Hay documentos procesándose; espere a que terminen para aprobar.")
        return _redirect_detalle(ref.num_refe)

    documentos = list(ref.documentos_glosa.filter(activo=True))
    comparacion = comparar_preforma_documentos(ref, documentos)
    nuevo_estado = comparacion.get('estado', 'PENDIENTE')
    discrepancias = comparacion.get('discrepancias', [])

    if nuevo_estado != 'EN_REVISION' or discrepancias:
        resultado.estado_revision = nuevo_estado
        resultado.datos_comparacion = comparacion
        resultado.save()
        GlosaEvento.objects.create(
            referencia=ref, tipo='CRUCE', usuario=request.user, estado_resultante=nuevo_estado,
            discrepancias=discrepancias, comentario='Intento de aprobación con cruce no limpio',
        )
        messages.error(request, "El cruce no está limpio; no se puede aprobar la glosa.")
        return _redirect_detalle(ref.num_refe)

    resultado.estado_revision = 'APROBADO'
    resultado.aprobado_por = request.user
    resultado.aprobado_en = timezone.now()
    resultado.datos_comparacion = comparacion
    resultado.save()

    GlosaEvento.objects.create(
        referencia=ref, tipo='APROBACION', usuario=request.user, estado_resultante='APROBADO',
    )

    # El GlosaRegistro se abre a mano en /glosa/; el visto bueno solo lo concluye.
    glosa_reg = GlosaRegistro.objects.filter(referencia=ref, fecha_conclusion__isnull=True).first()
    if glosa_reg:
        glosa_reg.fecha_conclusion = timezone.now()
        glosa_reg.usuario_conclusion = request.user
        glosa_reg.nota = (glosa_reg.nota + "\n" if glosa_reg.nota else "") + "Visto bueno glosa IA"
        glosa_reg.save()

    messages.success(request, "Glosa aprobada con visto bueno.")
    return _redirect_detalle(ref.num_refe)


@login_required
@require_http_methods(['POST'])
def glosa_rechazar(request, num_refe):
    if not es_glosador(request.user):
        messages.error(request, "No tiene permisos de glosador.")
        return _redirect_detalle(num_refe)

    comentario = request.POST.get('comentario', '').strip()
    if not comentario:
        messages.error(request, "Debe ingresar un comentario de rechazo.")
        return _redirect_detalle(num_refe)

    ref = get_object_or_404(Referencia, num_refe=num_refe)
    resultado, _ = GlosaAutomaticaResultado.objects.get_or_create(referencia=ref)
    if resultado.estado_revision == 'APROBADO':
        messages.error(request, "La glosa está aprobada; use Reabrir.")
        return _redirect_detalle(ref.num_refe)

    resultado.estado_revision = 'RECHAZADO'
    resultado.comentario_glosador = comentario
    resultado.save()

    GlosaEvento.objects.create(
        referencia=ref, tipo='RECHAZO', usuario=request.user, estado_resultante='RECHAZADO',
        comentario=comentario,
    )
    messages.success(request, "Glosa rechazada con comentarios.")
    return _redirect_detalle(ref.num_refe)


@login_required
@require_http_methods(['POST'])
def glosa_reabrir(request, num_refe):
    if not es_glosador(request.user):
        messages.error(request, "No tiene permisos de glosador.")
        return _redirect_detalle(num_refe)

    comentario = request.POST.get('comentario', '').strip()
    if not comentario:
        messages.error(request, "Debe ingresar un comentario para reabrir.")
        return _redirect_detalle(num_refe)

    ref = get_object_or_404(Referencia, num_refe=num_refe)
    resultado, _ = GlosaAutomaticaResultado.objects.get_or_create(referencia=ref)
    if resultado.estado_revision != 'APROBADO':
        messages.error(request, "La glosa no está aprobada; no se requiere reabrir.")
        return _redirect_detalle(ref.num_refe)

    resultado.estado_revision = 'RECHAZADO'
    resultado.aprobado_por = None
    resultado.aprobado_en = None
    resultado.comentario_glosador = comentario
    resultado.save()

    GlosaEvento.objects.create(
        referencia=ref, tipo='REAPERTURA', usuario=request.user, estado_resultante='RECHAZADO',
        comentario=comentario,
    )
    messages.success(request, "Glosa reabierta con comentarios.")
    return _redirect_detalle(ref.num_refe)
