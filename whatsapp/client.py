import json
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def _twilio_client():
    from twilio.rest import Client
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def _wa_to(numero: str) -> str:
    """Normaliza un número al formato whatsapp:+XXXXXXXXXXX."""
    numero = numero.split('@')[0]
    if not numero.startswith('whatsapp:'):
        numero = f"whatsapp:{numero}"
    return numero


def _wa_from() -> str:
    from_number = settings.TWILIO_WHATSAPP_FROM
    if not from_number.startswith('whatsapp:'):
        from_number = f"whatsapp:{from_number}"
    return from_number


def send_text(to: str, text: str) -> dict:
    """
    Envía un mensaje de sesión WhatsApp (solo funciona dentro de ventana de 24 h).
    Usar únicamente para respuestas a mensajes entrantes.
    """
    if not getattr(settings, 'TWILIO_ACCOUNT_SID', ''):
        logger.warning("Twilio no configurado — mensaje no enviado.")
        return {}
    try:
        message = _twilio_client().messages.create(
            from_=_wa_from(),
            to=_wa_to(to),
            body=text,
        )
        logger.info("Twilio WA (sesión) enviado a %s — SID: %s", to, message.sid)
        return {'sid': message.sid, 'status': message.status}
    except Exception as e:
        logger.error("Twilio send_text error para %s: %s", to, e)
        return {}


def send_template(to: str, content_sid: str, variables: dict) -> dict:
    """
    Envía un mensaje usando una plantilla aprobada por Meta (Content Template).
    No requiere ventana de sesión — llega a cualquier número con opt-in registrado.
    `variables` es un dict con claves string: {'1': valor, '2': valor, ...}
    """
    if not getattr(settings, 'TWILIO_ACCOUNT_SID', ''):
        logger.warning("Twilio no configurado — mensaje no enviado.")
        return {}
    try:
        message = _twilio_client().messages.create(
            from_=_wa_from(),
            to=_wa_to(to),
            content_sid=content_sid,
            content_variables=json.dumps(variables),
        )
        logger.info("Twilio WA (template %s) enviado a %s — SID: %s", content_sid, to, message.sid)
        return {'sid': message.sid, 'status': message.status}
    except Exception as e:
        logger.error("Twilio send_template error para %s: %s", to, e)
        return {}


def send_to_admin(text: str) -> dict:
    return send_text(settings.TWILIO_WHATSAPP_TO_ADMIN, text)
