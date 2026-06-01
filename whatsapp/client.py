import logging
import time
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_RETRY_DELAYS = [5, 15]  # segundos entre reintentos (2 reintentos máx)


def _headers():
    return {
        'X-API-Key': settings.WA_API_KEY,
        'Content-Type': 'application/json',
    }


def _session_url():
    return f"{settings.WA_API_URL}/sessions/{settings.WA_SESSION_ID}"


def send_text(chat_id: str, text: str) -> dict:
    if not getattr(settings, 'WA_API_URL', ''):
        return {}

    url = f"{_session_url()}/messages/send-text"
    last_exc = None

    for attempt, delay in enumerate([0] + _RETRY_DELAYS, start=1):
        if delay:
            time.sleep(delay)
        try:
            resp = requests.post(
                url,
                headers=_headers(),
                json={'chatId': chat_id, 'text': text},
                timeout=10,
            )
            resp.raise_for_status()
            if attempt > 1:
                logger.info("WhatsApp send_text OK en intento %d.", attempt)
            return resp.json()
        except Exception as e:
            last_exc = e
            logger.warning("WhatsApp send_text intento %d fallido: %s", attempt, e)

    logger.error("WhatsApp send_text falló tras %d intentos: %s", attempt, last_exc)
    return {}


def send_to_admin(text: str) -> dict:
    return send_text(settings.WA_ADMIN_CHAT, text)
