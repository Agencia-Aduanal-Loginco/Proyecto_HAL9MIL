import logging
import time
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


def _session_status() -> str:
    try:
        r = requests.get(_session_url(), headers=_headers(), timeout=8)
        return r.json().get('status', 'unknown') if r.status_code == 200 else 'unknown'
    except Exception:
        return 'unknown'


def _ensure_session_ready() -> bool:
    """Verifica que la sesión esté activa; la inicia si está disconnected."""
    status = _session_status()
    if status == 'ready':
        return True

    if status not in ('disconnected', 'stopped'):
        # initializing / authenticating / qr_ready / unknown — no tocar
        logger.warning("Sesión WhatsApp en estado '%s', no se puede enviar.", status)
        return False

    logger.warning("Sesión WhatsApp %s — intentando iniciar...", status)
    try:
        # start puede bloquear mientras WAHA inicializa; continuamos con polling
        requests.post(f"{_session_url()}/start", headers=_headers(), timeout=20)
    except Timeout:
        pass  # WAHA sigue procesando en background
    except Exception as e:
        logger.error("Error al iniciar sesión: %s", e)
        return False

    # Polling hasta 60s — WAHA puede tardar hasta ~40s en reconectar
    for _ in range(30):
        time.sleep(2)
        if _session_status() == 'ready':
            logger.info("Sesión WhatsApp reconectada OK.")
            return True

    logger.error("Sesión no alcanzó estado ready tras iniciar.")
    return False


def send_text(chat_id: str, text: str) -> dict:
    if not getattr(settings, 'WA_API_URL', ''):
        return {}

    if not _ensure_session_ready():
        logger.error("WhatsApp no disponible, mensaje a %s no enviado.", chat_id)
        return {}

    url = f"{_session_url()}/messages/send-text"
    try:
        resp = requests.post(
            url,
            headers=_headers(),
            json={'chatId': chat_id, 'text': text},
            timeout=30,  # WAHA bloquea esperando ACK de entrega
        )
        resp.raise_for_status()
        return resp.json()
    except Timeout:
        # Timeout ≠ fallo: WAHA ya encoló el mensaje. No reintentar (causaría duplicados).
        logger.warning("WhatsApp send_text timeout para %s (mensaje probablemente enviado).", chat_id)
        return {}
    except ReqConnectionError as e:
        logger.error("WhatsApp send_text sin conexión: %s", e)
        return {}
    except Exception as e:
        logger.error("WhatsApp send_text error: %s", e)
        return {}


def send_to_admin(text: str) -> dict:
    return send_text(settings.WA_ADMIN_CHAT, text)
