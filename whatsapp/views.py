import hmac
import hashlib
import json
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import WhatsAppLog
from .bot import handle_command
from . import client

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def webhook(request):
    secret = getattr(settings, 'WA_WEBHOOK_SECRET', '')
    if secret:
        sig = request.headers.get('X-Webhook-Hmac', '')
        expected = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return HttpResponse('Unauthorized', status=401)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse('Bad Request', status=400)

    if payload.get('event') == 'message.received':
        _handle_message(payload.get('data', {}))

    return JsonResponse({'ok': True})


def _handle_message(data: dict):
    chat_id = data.get('from', '')
    text = data.get('body', '').strip()

    if not chat_id or not text:
        return

    WhatsAppLog.objects.create(
        direccion='in',
        chat_id=chat_id,
        mensaje=text,
        referencia_bot=text[:100],
    )

    allowed = getattr(settings, 'WA_ALLOWED_NUMBERS', [])
    numero = chat_id.split('@')[0]
    if allowed and numero not in allowed:
        return

    respuesta = handle_command(text)
    if respuesta:
        result = client.send_text(chat_id, respuesta)
        WhatsAppLog.objects.create(
            direccion='out',
            chat_id=chat_id,
            mensaje=respuesta,
            estado='sent' if result else 'failed',
            referencia_bot=text[:100],
        )
