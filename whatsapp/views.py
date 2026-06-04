import logging

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import WhatsAppLog
from .bot import handle_command
from . import client

logger = logging.getLogger(__name__)


def _validate_twilio_signature(request) -> bool:
    """Valida la firma X-Twilio-Signature para asegurar que el webhook es legítimo."""
    auth_token = getattr(settings, 'TWILIO_AUTH_TOKEN', '')
    if not auth_token:
        return True  # Sin token configurado, se omite validación (solo desarrollo)

    try:
        from twilio.request_validator import RequestValidator
        validator = RequestValidator(auth_token)
        url = request.build_absolute_uri()
        post_data = request.POST.dict()
        signature = request.headers.get('X-Twilio-Signature', '')
        return validator.validate(url, post_data, signature)
    except Exception as e:
        logger.error("Error validando firma Twilio: %s", e)
        return False


@csrf_exempt
@require_POST
def webhook(request):
    if not _validate_twilio_signature(request):
        logger.warning("Firma Twilio inválida — rechazando webhook.")
        return HttpResponse('Unauthorized', status=401)

    # Twilio envía form-encoded, no JSON
    from_number = request.POST.get('From', '')   # whatsapp:+521XXXXXXXXXX
    body = request.POST.get('Body', '').strip()

    if not from_number or not body:
        return HttpResponse('', status=204)

    numero = from_number.replace('whatsapp:', '')

    WhatsAppLog.objects.create(
        direccion='in',
        chat_id=numero,
        mensaje=body,
        referencia_bot=body[:100],
    )

    allowed = getattr(settings, 'TWILIO_WHATSAPP_ALLOWED_NUMBERS', [])
    if allowed and numero not in allowed:
        return HttpResponse('', status=204)

    respuesta = handle_command(body)
    if respuesta:
        result = client.send_text(numero, respuesta)
        WhatsAppLog.objects.create(
            direccion='out',
            chat_id=numero,
            mensaje=respuesta,
            estado='sent' if result else 'failed',
            referencia_bot=body[:100],
        )

    # Twilio espera TwiML o 204 — respuesta vacía es válida
    return HttpResponse('', status=204)
