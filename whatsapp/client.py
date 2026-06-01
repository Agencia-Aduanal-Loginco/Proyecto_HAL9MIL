import logging
import requests
from requests.exceptions import Timeout, ConnectionError as ReqConnectionError
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

    url = f"{_session_url()}/messages/send-text"
    try:
        resp = requests.post(
            url,
            headers=_headers(),
            json={'chatId': chat_id, 'text': text},
            timeout=30,  # WAHA bloquea esperando ACK; 30s es suficiente
        )
        resp.raise_for_status()
        return resp.json()
    except Timeout:
        # Timeout no significa fallo: WAHA ya encoló el mensaje en WhatsApp.
        # No reintentar — causaría duplicados.
        logger.warning("WhatsApp send_text timeout para %s (mensaje probablemente enviado).", chat_id)
        return {}
    except ReqConnectionError as e:
        # Sin conexión al servidor OpenWA — reintento manual necesario.
        logger.error("WhatsApp send_text sin conexión: %s", e)
        return {}
    except Exception as e:
        logger.error("WhatsApp send_text error: %s", e)
        return {}


def send_to_admin(text: str) -> dict:
    return send_text(settings.WA_ADMIN_CHAT, text)
