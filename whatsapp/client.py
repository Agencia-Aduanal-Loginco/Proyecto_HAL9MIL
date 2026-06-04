import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def _twilio_client():
    from twilio.rest import Client
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def send_text(to: str, text: str) -> dict:
    """
    Envía un mensaje WhatsApp vía Twilio.
    `to` puede ser un número E.164 (+521XXXXXXXXXX) o ya en formato whatsapp:+521XXXXXXXXXX.
    """
    if not getattr(settings, 'TWILIO_ACCOUNT_SID', ''):
        logger.warning("Twilio no configurado — mensaje no enviado.")
        return {}

    # Limpiar sufijo legado de OpenWA/WAHA (@c.us, @g.us, etc.)
    to = to.split('@')[0]
    if not to.startswith('whatsapp:'):
        to = f"whatsapp:{to}"

    from_number = settings.TWILIO_WHATSAPP_FROM
    if not from_number.startswith('whatsapp:'):
        from_number = f"whatsapp:{from_number}"

    try:
        client = _twilio_client()
        message = client.messages.create(
            from_=from_number,
            to=to,
            body=text,
        )
        logger.info("Twilio WA enviado a %s — SID: %s", to, message.sid)
        return {'sid': message.sid, 'status': message.status}
    except Exception as e:
        logger.error("Twilio send_text error para %s: %s", to, e)
        return {}


def send_to_admin(text: str) -> dict:
    return send_text(settings.TWILIO_WHATSAPP_TO_ADMIN, text)
