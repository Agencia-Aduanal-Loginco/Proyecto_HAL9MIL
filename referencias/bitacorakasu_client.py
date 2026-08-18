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

    # Verificar status code
    if resp.status_code >= 400:
        # Intentar extraer el body de la respuesta para mejor diagnóstico
        try:
            error_body = resp.json()
            error_msg = str(error_body)
        except Exception:
            error_msg = resp.text

        raise BitacoraKasuError(
            f'BitacoraKasu retornó HTTP {resp.status_code}: {error_msg}'
        )

    # Éxito: retornar el JSON
    return resp.json()
