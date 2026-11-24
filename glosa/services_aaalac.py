import os
import requests
from django.conf import settings

class AAALACError(Exception):
    pass

def obtener_eta_mercancia(num_pedimento=None, buque=None, viaje=None, contenedor=None, referencia=None):
    api_url = os.getenv('AAALAC_API_URL', 'https://plataforma.aaalac.mx/fonatrac/api/')
    api_key = os.getenv('AAALAC_API_KEY', '')

    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    params = {
        'pedimento': num_pedimento,
        'buque': buque,
        'viaje': viaje,
        'contenedor': contenedor,
        'referencia': referencia
    }
    try:
        response = requests.get(api_url, headers=headers, params={k: v for k, v in params.items() if v}, timeout=15)
        if response.status_code != 200:
            raise AAALACError(f"Error API AAALAC: {response.status_code} - {response.text}")
        return response.json()
    except requests.RequestException as e:
        raise AAALACError(f"Conexión fallida con API AAALAC: {str(e)}")


def aaalac_habilitado():
    """El botón y la consulta solo se activan con AAALAC_API_KEY configurada.

    La plataforma de AAALAC Fonatrac exige reCAPTCHA y no hay API con token
    confirmada; este cliente queda listo para cuando exista.
    """
    return bool(os.getenv('AAALAC_API_KEY', ''))
