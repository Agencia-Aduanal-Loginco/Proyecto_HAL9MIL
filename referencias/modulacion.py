"""
Notificación de modulación DODA: PDF, correo (SendGrid Web API) y push por
contenedor a BitacoraKasu. Se dispara desde referencias/sync_views.py vía
transaction.on_commit, después de que el sync crea DODAs nuevas.

Ninguna falla de email o de push debe interrumpir el procesamiento del resto
de las DODAs — cada una se procesa de forma aislada.
"""
import base64
import io
import logging

from django.conf import settings
from django.utils import timezone
from reportlab.pdfgen import canvas
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Attachment, CustomArg, Disposition, FileContent, FileName, FileType, Mail,
)

from core.capturistas import resolver_destinatario

from .bitacorakasu_client import BitacoraKasuError, enviar_modulacion
from .models import EnvioModulacion

logger = logging.getLogger(__name__)

AGENCIA = 'LOGINCO'


def _generar_pdf_doda(doda):
    """PDF 'Pedimento + DODA para imprimir'.

    Contenido: num_doda, terminal_nombre, y por cada DodaReferencia.referencia
    del DODA: num_pedimento, nombre_cliente y sus contenedores. Mismo patrón
    de canvas de reportlab que finanzas/cuenta_gastos_envio.py.
    """
    buffer = io.BytesIO()
    ancho, alto = 612, 792  # carta
    c = canvas.Canvas(buffer, pagesize=(ancho, alto))
    y = alto - 50

    c.setFont('Helvetica-Bold', 14)
    c.drawString(40, y, f'DODA {doda.num_doda or doda.id_doda}')
    y -= 22
    c.setFont('Helvetica', 10)
    c.drawString(40, y, f'Terminal portuaria: {doda.terminal_nombre}')
    y -= 30

    referencias = (
        doda.referencias_doda.select_related('referencia')
        .prefetch_related('referencia__contenedores')
        .all()
    )
    for doda_ref in referencias:
        referencia = doda_ref.referencia
        if referencia is None:
            continue
        if y < 90:
            c.showPage()
            y = alto - 50
            c.setFont('Helvetica', 10)
        c.setFont('Helvetica-Bold', 11)
        c.drawString(40, y, f'Pedimento: {referencia.num_pedimento}')
        y -= 16
        c.setFont('Helvetica', 10)
        c.drawString(40, y, f'Cliente: {referencia.nombre_cliente}')
        y -= 16
        contenedores = ', '.join(
            ct.num_cont for ct in referencia.contenedores.all()
        ) or '(sin contenedores)'
        c.drawString(40, y, f'Contenedores: {contenedores}')
        y -= 26

    c.save()
    return buffer.getvalue()


def _registrar_error(envio, campo, mensaje):
    setattr(envio, campo, 'ERROR')
    envio.error_detalle = (
        f'{envio.error_detalle}\n{mensaje}' if envio.error_detalle else mensaje
    )


def _enviar_email_modulacion(doda, destinatario, envio):
    """Envía el correo de modulación (con PDF adjunto) al destinatario.

    Reusa el patrón SendGrid Web API de enviar_cuenta_gastos en
    finanzas/cuenta_gastos_envio.py. Nunca propaga: en fallo deja
    envio.email_estado='ERROR' con el detalle y retorna False.
    """
    email, nombre = destinatario
    try:
        pdf_bytes = _generar_pdf_doda(doda)
        nombre_pdf = f"DODA_{(doda.num_doda or str(doda.id_doda)).replace('/', '-')}.pdf"

        mensaje = Mail(
            from_email=settings.DEFAULT_FROM_EMAIL,
            to_emails=email,
            subject=f'Modulación DODA {doda.num_doda} — inicia extracción de contenedor',
            html_content=(
                f'<p>Estimado(a) {nombre},</p>'
                f'<p>Se generó la DODA <strong>{doda.num_doda}</strong> en la terminal '
                f'<strong>{doda.terminal_nombre}</strong>. Favor de iniciar la solicitud '
                f'de extracción del contenedor.</p>'
                f'<p>Se adjunta el pedimento + DODA para imprimir.</p>'
                f'<p>{settings.NOMBRE_AGENCIA}</p>'
            ),
        )
        mensaje.attachment = Attachment(
            FileContent(base64.b64encode(pdf_bytes).decode()),
            FileName(nombre_pdf),
            FileType('application/pdf'),
            Disposition('attachment'),
        )
        mensaje.custom_arg = CustomArg('envio_modulacion_id', str(envio.pk))

        respuesta = SendGridAPIClient(settings.SENDGRID_API_KEY).send(mensaje)
        if respuesta.status_code >= 400:
            raise RuntimeError(f'SendGrid respondió status {respuesta.status_code}')

        envio.sg_message_id = respuesta.headers.get('X-Message-Id', '') or ''
        envio.email_estado = 'ENVIADO'
        logger.info('[Modulacion] Email DODA %s enviado a %s (envio %s)',
                    doda.num_doda, email, envio.pk)
        return True
    except Exception as e:
        _registrar_error(envio, 'email_estado', f'email: {e}')
        logger.error('[Modulacion] Error enviando email de modulación DODA %s: %s',
                     doda.num_doda, e)
        return False


def _push_bitacorakasu(doda, envio):
    """POST a BitacoraKasu por cada contenedor de las referencias del DODA.

    Un fallo en un contenedor no aborta los demás. Si algún POST falla, el
    push_estado general queda en ERROR con el detalle de los que fallaron;
    si no había contenedores que enviar, también queda en ERROR (nada que
    confirmar). Nunca propaga BitacoraKasuError.
    """
    fallidos = []
    enviados = 0

    referencias = (
        doda.referencias_doda.select_related('referencia')
        .prefetch_related('referencia__contenedores')
        .all()
    )
    for doda_ref in referencias:
        referencia = doda_ref.referencia
        if referencia is None:
            continue
        for contenedor in referencia.contenedores.all():
            payload = {
                'agencia': AGENCIA,
                'terminal_portuaria': doda.terminal_nombre,
                'tipo_contenedor': contenedor.tipo,
                'peso_toneladas': (
                    str(referencia.peso_bruto) if referencia.peso_bruto is not None else ''
                ),
                'contenedor': contenedor.num_cont,
                'cliente': referencia.nombre_cliente,
                'num_pedimento': referencia.num_pedimento,
                'num_doda': doda.num_doda,
                # Clave de idempotencia estable para que BitacoraKasu pueda
                # distinguir un reenvío genuino de un duplicado: el retry
                # ocurre a nivel de DODA completa (reintentar_envio), así
                # que si 1 de 5 contenedores falla, los 5 se re-postean.
                'idempotency_key': f'{doda.id_doda}:{contenedor.num_cont}',
            }
            try:
                enviar_modulacion(payload)
                enviados += 1
            except BitacoraKasuError as e:
                fallidos.append(f'{contenedor.num_cont}: {e}')
                logger.error(
                    '[Modulacion] Error push contenedor %s (DODA %s) a BitacoraKasu: %s',
                    contenedor.num_cont, doda.num_doda, e,
                )

    if fallidos:
        _registrar_error(envio, 'push_estado', 'push: ' + '; '.join(fallidos))
        return False
    if enviados == 0:
        _registrar_error(envio, 'push_estado', 'push: sin contenedores para enviar')
        return False

    envio.push_estado = 'ENVIADO'
    logger.info('[Modulacion] Push BitacoraKasu DODA %s: %s contenedor(es) enviados',
                doda.num_doda, enviados)
    return True


def _procesar_email(doda, envio):
    """Intenta enviar el email de modulación para `doda`, registrando el
    resultado en `envio`. Retorna True si quedó ENVIADO."""
    destinatario = resolver_destinatario(doda.cve_capt)
    if destinatario is None:
        _registrar_error(envio, 'email_estado', 'sin destinatario resuelto')
        logger.warning(
            '[Modulacion] Sin destinatario resuelto para DODA %s (cve_capt=%s)',
            doda.num_doda, doda.cve_capt,
        )
        return False
    return _enviar_email_modulacion(doda, destinatario, envio)


def _procesar_push(doda, envio):
    """Intenta el push por contenedor a BitacoraKasu, registrando el
    resultado en `envio`. Retorna True si quedó ENVIADO."""
    return _push_bitacorakasu(doda, envio)


def _procesar_doda(doda):
    envio = EnvioModulacion.objects.create(doda=doda)

    if _procesar_email(doda, envio):
        doda.notificado_en = timezone.now()

    # El push no depende del resultado del email.
    if _procesar_push(doda, envio):
        doda.modulacion_enviada_en = timezone.now()

    envio.save()

    update_fields = [
        campo for campo in ('notificado_en', 'modulacion_enviada_en')
        if getattr(doda, campo) is not None
    ]
    if update_fields:
        doda.save(update_fields=update_fields)


def reintentar_envio(envio):
    """Reintenta, sobre un `EnvioModulacion` ya existente, sólo las partes
    (email y/o push) que no hayan quedado ENVIADO — es decir, ERROR o
    PENDIENTE (nunca intentadas, p.ej. el proceso murió entre el create() y
    el save() del resultado) — sin duplicar envíos ya exitosos ni crear un
    nuevo EnvioModulacion.

    Usado por el management command `reintentar_modulacion`. Retorna True si,
    al terminar, ni email_estado ni push_estado quedan en ERROR.
    """
    doda = envio.doda
    update_fields = []

    if envio.email_estado != 'ENVIADO':
        if _procesar_email(doda, envio):
            doda.notificado_en = timezone.now()
            update_fields.append('notificado_en')

    if envio.push_estado != 'ENVIADO':
        if _procesar_push(doda, envio):
            doda.modulacion_enviada_en = timezone.now()
            update_fields.append('modulacion_enviada_en')

    envio.save()
    if update_fields:
        doda.save(update_fields=update_fields)

    return envio.email_estado != 'ERROR' and envio.push_estado != 'ERROR'


def procesar_dodas_nuevas(dodas_creadas):
    """Procesa DODAs recién creadas por el sync: correo + push por contenedor.

    Se llama desde sync_views.sync_endpoint vía transaction.on_commit, para
    correr después del commit del sync y no bloquearlo ni corromperlo si
    falla el envío. Cada DODA se procesa de forma aislada: una excepción no
    esperada al procesar una no debe impedir procesar las demás.
    """
    for doda in dodas_creadas:
        try:
            _procesar_doda(doda)
        except Exception as e:
            logger.error('[Modulacion] Error inesperado procesando DODA %s: %s',
                         getattr(doda, 'id_doda', '?'), e)
