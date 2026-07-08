"""
Cliente PAC para timbrado CFDI 4.0 — SW Sapien (solucionFacturacion.com.mx).

Configuración en .env (Fase 0):
    PAC_PROVIDER=sw_sapien
    PAC_URL=https://services.test.sw.com.mx   # sandbox
    PAC_TOKEN=<token_bearer>
    PAC_USER=<usuario>
    PAC_PASSWORD=<contraseña>

El timbrado usa el endpoint /v3/cfdi33/stamp/v4/b64 con el XML
codificado en Base64 en el body JSON.
"""

import base64
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Timeouts en segundos
_CONNECT_TIMEOUT = 10
_READ_TIMEOUT = 30
_TIMEOUT = (_CONNECT_TIMEOUT, _READ_TIMEOUT)


class PACError(Exception):
    """Error retornado por el PAC (negocio, no red)."""
    def __init__(self, message: str, code: str = ''):
        self.code = code
        super().__init__(message)


class PACConfigError(Exception):
    """Configuración PAC ausente o incompleta en settings/env."""


def _get_pac_settings() -> dict:
    pac_url = getattr(settings, 'PAC_URL', '')
    pac_token = getattr(settings, 'PAC_TOKEN', '')
    if not pac_url or not pac_token:
        raise PACConfigError(
            'PAC_URL y PAC_TOKEN deben estar definidos en .env. '
            'Ver Fase 0 de plan_finanzas.md.'
        )
    return {'url': pac_url.rstrip('/'), 'token': pac_token}


def _bearer_headers(token: str) -> dict:
    return {
        'Authorization': f'bearer {token}',
        'Content-Type': 'application/json',
    }


def _refrescar_token() -> str:
    """
    Obtiene un nuevo token usando PAC_USER / PAC_PASSWORD.
    Retorna el nuevo token o lanza PACConfigError si faltan credenciales.
    """
    pac_url = getattr(settings, 'PAC_URL', '').rstrip('/')
    user = getattr(settings, 'PAC_USER', '')
    password = getattr(settings, 'PAC_PASSWORD', '')
    if not (pac_url and user and password):
        raise PACConfigError('PAC_USER y PAC_PASSWORD requeridos para refrescar el token.')

    resp = requests.post(
        f'{pac_url}/security/authenticate',
        headers={'Authorization': f'bearer {getattr(settings, "PAC_TOKEN", "")}'},
        timeout=_TIMEOUT,
    )
    if resp.status_code == 401:
        # Intentar con user/password directamente
        cred = base64.b64encode(f'{user}:{password}'.encode()).decode()
        resp = requests.post(
            f'{pac_url}/security/authenticate',
            headers={'Authorization': f'Basic {cred}'},
            timeout=_TIMEOUT,
        )

    data = resp.json()
    token = data.get('data', {}).get('token', '')
    if not token:
        raise PACError(f'No se pudo refrescar el token PAC: {data}')
    logger.info('Token PAC refrescado correctamente.')
    return token


def timbrar_cfdi(xml_sin_timbrar: str) -> dict:
    """
    Envía el XML (ya sellado con CSD) al PAC para su timbrado.

    Args:
        xml_sin_timbrar: string XML CFDI 4.0 con Sello pero sin TimbreFiscalDigital.

    Returns:
        {
            'status': 'success',
            'uuid': '<uuid-sat>',
            'xml_timbrado': '<xml completo con TFD>',
        }

    Raises:
        PACConfigError: configuración ausente.
        PACError: PAC rechazó el XML (con código SAT si aplica).
        requests.RequestException: error de red/timeout.
    """
    cfg = _get_pac_settings()
    xml_b64 = base64.b64encode(xml_sin_timbrar.encode('utf-8')).decode('ascii')

    def _do_stamp(token: str) -> requests.Response:
        return requests.post(
            f'{cfg["url"]}/v3/cfdi33/stamp/v4/b64',
            headers=_bearer_headers(token),
            json={'xml': xml_b64},
            timeout=_TIMEOUT,
        )

    resp = _do_stamp(cfg['token'])

    # Refresco automático de token si expiró
    if resp.status_code == 401:
        logger.warning('Token PAC expirado, refrescando...')
        new_token = _refrescar_token()
        resp = _do_stamp(new_token)

    try:
        payload = resp.json()
    except Exception:
        raise PACError(
            f'Respuesta PAC no es JSON válido (HTTP {resp.status_code}): {resp.text[:200]}'
        )

    status = payload.get('status', '')
    if status == 'success':
        data = payload.get('data', {})
        uuid = data.get('uuid', '')
        xml_raw = data.get('xml', '')
        if not uuid or not xml_raw:
            raise PACError('Respuesta PAC success pero sin uuid o xml.')
        xml_timbrado = base64.b64decode(xml_raw).decode('utf-8')
        logger.info('CFDI timbrado correctamente. UUID: %s', uuid)
        return {'status': 'success', 'uuid': uuid, 'xml_timbrado': xml_timbrado}

    # Error del PAC — extraer mensaje y código
    message = (
        payload.get('message')
        or payload.get('messageDetail')
        or str(payload)
    )
    code = ''
    # El código SAT suele estar en message o en data.message
    import re
    m = re.search(r'CFDI\d+', message)
    if m:
        code = m.group(0)

    # Errores especiales
    if 'CFDI33126' in message:
        raise PACError('UUID duplicado: este XML ya fue timbrado anteriormente.', 'CFDI33126')
    if 'CFDI33106' in message:
        raise PACError(f'Error en estructura XML CFDI: {message}', 'CFDI33106')

    raise PACError(f'PAC rechazó el timbrado: {message}', code)
