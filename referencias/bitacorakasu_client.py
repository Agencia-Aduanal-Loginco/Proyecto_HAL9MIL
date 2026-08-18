"""
Cliente HTTP para BitacoraKasu — envío de modulación (DODA).

Configuración en .env (Fase 0):
    BITACORAKASU_MODULACION_URL=https://bitacora.kasu.com.mx/api/modulacion
    BITACORAKASU_API_TOKEN=<token_bearer>
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Timeouts en segundos
_CONNECT_TIMEOUT = 10
_READ_TIMEOUT = 30
_TIMEOUT = (_CONNECT_TIMEOUT, _READ_TIMEOUT)


class BitacoraKasuError(Exception):
    """Error retornado por BitacoraKasu (negocio, red o configuración)."""


def enviar_modulacion(payload: dict) -> dict:
    """
    Envía un payload de modulación a BitacoraKasu.

    Args:
        payload: dict con los datos de la modulación (ej. {'doda_id': '5001', ...}).

    Returns:
        dict con la respuesta JSON del servidor en caso de éxito (HTTP 2xx).

    Raises:
        BitacoraKasuError: en caso de timeout, conexión, error HTTP (>= 400),
                          o cualquier otra excepción de requests.
    """
    url = getattr(settings, 'BITACORAKASU_MODULACION_URL', '')
    token = getattr(settings, 'BITACORAKASU_API_TOKEN', '')

    if not url or not token:
        raise BitacoraKasuError(
            'BITACORAKASU_MODULACION_URL y BITACORAKASU_API_TOKEN deben '
            'estar definidos en .env — ver plan_modulacion.md.'
        )

    headers = {
        'Authorization': f'Token {token}',
    }

    try:
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        # Timeout, connection error, o cualquier otra excepción de requests
        raise BitacoraKasuError(f'Error al enviar modulación a BitacoraKasu: {str(e)}')

    # Intentar extraer el body de la respuesta como JSON (tanto éxito como error)
    try:
        payload = resp.json()
    except Exception:
        # JSON inválido: usar resp.text en lugar de JSON
        error_msg = resp.text
        if resp.status_code >= 400:
            # Es un error HTTP con body no-JSON
            raise BitacoraKasuError(
                f'BitacoraKasu retornó HTTP {resp.status_code}: {error_msg}'
            )
        else:
            # Es un éxito (2xx) pero respuesta no es JSON válido
            raise BitacoraKasuError(
                f'Respuesta BitacoraKasu no es JSON válido (HTTP {resp.status_code}): {error_msg}'
            )

    # Verificar si hubo error HTTP
    if resp.status_code >= 400:
        error_msg = str(payload)
        raise BitacoraKasuError(
            f'BitacoraKasu retornó HTTP {resp.status_code}: {error_msg}'
        )

    # Éxito: retornar el payload
    return payload
