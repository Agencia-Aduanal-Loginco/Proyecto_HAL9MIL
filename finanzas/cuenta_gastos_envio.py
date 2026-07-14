"""Envío de la cuenta de gastos de una referencia al cliente.

Correo con balanza anticipos vs. gastos y ZIP de CFDIs, vía SendGrid Web API
(custom_args para correlación con el Event Webhook). Patrón hermano de
finanzas/cobranza.py, que sigue usando SMTP.
"""
import base64
import io
import logging
import os
import zipfile
from datetime import date, datetime, timezone as dt_timezone

from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.utils import timezone
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Attachment, CustomArg, Disposition, FileContent, FileName, FileType, Mail,
)

logger = logging.getLogger(__name__)

LIMITE_ZIP_BYTES = 20 * 1024 * 1024  # SendGrid admite 30 MB por mensaje


def destinatarios_cliente(cliente):
    """(to, cc) para la cuenta de gastos, con fallback a los correos de cobranza."""
    if cliente is None:
        return '', ''
    to = cliente.email_cuenta_gastos or cliente.email_cobranza
    cc = cliente.email_cuenta_gastos_cc or cliente.email_cobranza_cc
    return to, cc


def construir_zip_cuenta_gastos(referencia):
    """Empaqueta xml_file + pdf_file de cada XMLProveedor de la referencia.

    Retorna (nombre_zip, bytes). Lanza ValueError si la referencia no tiene
    CFDIs o si el ZIP excede LIMITE_ZIP_BYTES.
    """
    xmls = list(referencia.xmls_proveedor.all())
    if not xmls:
        raise ValueError('La referencia no tiene CFDIs para adjuntar.')

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for xml in xmls:
            with xml.xml_file.open('rb') as f:
                zf.writestr(f'CFDI_{xml.uuid_fiscal}.xml', f.read())
            if xml.pdf_file:
                with xml.pdf_file.open('rb') as f:
                    zf.writestr(f'CFDI_{xml.uuid_fiscal}.pdf', f.read())

    data = buffer.getvalue()
    if len(data) > LIMITE_ZIP_BYTES:
        raise ValueError(
            f'El ZIP pesa {len(data) / 1024 / 1024:.1f} MB y excede el límite '
            f'de {LIMITE_ZIP_BYTES // 1024 // 1024} MB para envío por correo.'
        )
    nombre = f"CG_{referencia.num_refe.replace('/', '-')}_{date.today():%Y%m%d}.zip"
    return nombre, data


def contexto_balanza(referencia):
    """Contexto compartido por el email y la vista previa en pantalla."""
    from .saldo import saldo_referencia
    return {
        'referencia': referencia,
        'anticipos': referencia.anticipos.order_by('fecha'),
        'gastos': referencia.gastos_finanzas.order_by('fecha'),
        'saldo': saldo_referencia(referencia),
    }


def enviar_cuenta_gastos(referencia, destinatario, cc='', usuario=None,
                         es_reenvio=False):
    """Envía la cuenta de gastos por correo y registra la notificación.

    Nunca lanza: en fallo la notificación queda en ERROR con error_msg y el
    llamador decide qué mostrar. Retorna la NotificacionCuentaGastos.
    """
    from .models import NotificacionCuentaGastos

    notif = NotificacionCuentaGastos.objects.create(
        referencia=referencia, destinatario=destinatario, cc=cc or '',
        enviado_por=usuario, es_reenvio=es_reenvio,
    )
    try:
        previa = None
        if es_reenvio:
            previa = (
                NotificacionCuentaGastos.objects
                .filter(referencia=referencia, zip_file__isnull=False)
                .exclude(zip_file='').exclude(pk=notif.pk)
                .order_by('-enviado_en').first()
            )
        if previa:
            with previa.zip_file.open('rb') as f:
                data = f.read()
            nombre = os.path.basename(previa.zip_file.name)
            notif.zip_file.name = previa.zip_file.name
        else:
            nombre, data = construir_zip_cuenta_gastos(referencia)
            notif.zip_file.save(nombre, ContentFile(data), save=False)

        html = render_to_string(
            'finanzas/email_cuenta_gastos.html', contexto_balanza(referencia)
        )
        mensaje = Mail(
            from_email=settings.DEFAULT_FROM_EMAIL,
            to_emails=destinatario,
            subject=f'Cuenta de gastos {referencia.num_refe} — Reiki Logística',
            html_content=html,
        )
        if cc:
            mensaje.add_cc(cc)
        mensaje.attachment = Attachment(
            FileContent(base64.b64encode(data).decode()),
            FileName(nombre),
            FileType('application/zip'),
            Disposition('attachment'),
        )
        mensaje.custom_arg = CustomArg('notificacion_cg_id', str(notif.pk))

        respuesta = SendGridAPIClient(settings.SENDGRID_API_KEY).send(mensaje)
        if respuesta.status_code >= 400:
            raise RuntimeError(f'SendGrid respondió status {respuesta.status_code}')
        notif.sg_message_id = respuesta.headers.get('X-Message-Id', '') or ''
        notif.estado = 'ENVIADO'
        logger.info('[CG] Cuenta de gastos %s enviada a %s (notif %s)',
                    referencia.num_refe, destinatario, notif.pk)
    except Exception as e:
        notif.estado = 'ERROR'
        notif.error_msg = str(e)
        logger.error('[CG] Error enviando cuenta de gastos %s: %s',
                     referencia.num_refe, e)
    notif.save()
    return notif


_MAPEO_EVENTOS = {
    'delivered': 'ENTREGADO',
    'open': 'LEIDO',
    'bounce': 'REBOTADO',
    'dropped': 'REBOTADO',
}
_ORDEN_ESTADOS = {'ENVIADO': 1, 'ENTREGADO': 2, 'LEIDO': 3}


def procesar_evento_sendgrid(evento):
    """Aplica un evento del Event Webhook a su NotificacionCuentaGastos.

    Eventos sin notificacion_cg_id (otros correos de la cuenta SendGrid),
    con id inexistente o de tipo no mapeado se ignoran en silencio.
    Los estados solo avanzan; los timestamps se llenan aunque el evento
    llegue fuera de orden. Un bounce tardío (después de ENTREGADO/LEIDO)
    no degrada el estado, aunque sí se registra el motivo en error_msg.
    """
    from .models import NotificacionCuentaGastos

    notif_id = evento.get('notificacion_cg_id')
    if not notif_id:
        return
    try:
        notif = NotificacionCuentaGastos.objects.get(pk=int(notif_id))
    except (NotificacionCuentaGastos.DoesNotExist, TypeError, ValueError):
        return

    nuevo = _MAPEO_EVENTOS.get(evento.get('event'))
    if not nuevo:
        return

    if evento.get('timestamp'):
        momento = datetime.fromtimestamp(evento['timestamp'], tz=dt_timezone.utc)
    else:
        momento = timezone.now()

    if nuevo == 'REBOTADO':
        notif.error_msg = evento.get('reason', 'Correo rebotado')
        # Un bounce tardío (llegado después de ENTREGADO/LEIDO) no debe
        # regresar el estado: los estados solo avanzan.
        if notif.estado not in ('ENTREGADO', 'LEIDO'):
            notif.estado = 'REBOTADO'
    else:
        if nuevo == 'ENTREGADO' and notif.entregado_en is None:
            notif.entregado_en = momento
        if nuevo == 'LEIDO' and notif.leido_en is None:
            notif.leido_en = momento
        if (notif.estado in _ORDEN_ESTADOS
                and _ORDEN_ESTADOS[nuevo] > _ORDEN_ESTADOS[notif.estado]):
            notif.estado = nuevo
    notif.save()
