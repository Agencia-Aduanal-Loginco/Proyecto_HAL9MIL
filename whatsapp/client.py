import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


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
    try:
        resp = requests.post(
            f"{_session_url()}/messages/send-text",
            headers=_headers(),
            json={'chatId': chat_id, 'text': text},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("WhatsApp send_text error: %s", e)
        return {}


def send_to_admin(text: str) -> dict:
    return send_text(settings.WA_ADMIN_CHAT, text)
