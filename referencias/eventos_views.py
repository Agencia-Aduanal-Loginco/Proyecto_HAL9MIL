import json
import logging
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import ExpedienteExtraccion, EventoContenedor

log = logging.getLogger(__name__)


def _token_valido_eventos(request):
    auth = request.headers.get('Authorization', '')
    expected_tokens = []
    if hasattr(settings, 'HAL9MIL_EVENTOS_TOKEN') and settings.HAL9MIL_EVENTOS_TOKEN:
        expected_tokens.append(settings.HAL9MIL_EVENTOS_TOKEN)
    if hasattr(settings, 'SYNC_SECRET_KEY') and settings.SYNC_SECRET_KEY:
        expected_tokens.append(settings.SYNC_SECRET_KEY)

    if not expected_tokens:
        log.error('Ni HAL9MIL_EVENTOS_TOKEN ni SYNC_SECRET_KEY configuradas en Django settings')
        return False
    if not auth.startswith('Token '):
        return False
    token_val = auth[6:].strip()
    return token_val in expected_tokens


@csrf_exempt
@require_POST
def eventos_contenedor_api(request):
    if not _token_valido_eventos(request):
        return JsonResponse({'error': 'Token inválido o ausente'}, status=401)

    try:
        body = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return JsonResponse({'error': f'JSON inválido: {e}'}, status=400)

    evento_id = str(body.get('evento_id', '')).strip()
    clave_expediente = str(body.get('clave_expediente', '')).strip()
    tipo = str(body.get('tipo', '')).strip()
    ocurrido_en_raw = body.get('ocurrido_en')
    datos = body.get('datos', {})

    if not evento_id or not clave_expediente or not tipo:
        return JsonResponse({'error': 'Campos obligatorios faltantes (evento_id, clave_expediente, tipo)'}, status=400)

    # Idempotencia por evento_id
    existing = EventoContenedor.objects.filter(evento_id=evento_id).first()
    if existing:
        return JsonResponse({'status': 'ok', 'evento_id': evento_id, 'duplicado': True}, status=200)

    expediente = ExpedienteExtraccion.objects.filter(clave=clave_expediente).first()
    if not expediente:
        return JsonResponse({'error': f'Expediente no encontrado: {clave_expediente}'}, status=404)

    ocurrido_en = parse_datetime(ocurrido_en_raw) if isinstance(ocurrido_en_raw, str) else ocurrido_en_raw
    if not ocurrido_en:
        ocurrido_en = timezone.now()
    elif timezone.is_naive(ocurrido_en):
        ocurrido_en = timezone.make_aware(ocurrido_en)

    try:
        EventoContenedor.objects.create(
            expediente=expediente,
            evento_id=evento_id,
            tipo=tipo,
            ocurrido_en=ocurrido_en,
            datos=datos if isinstance(datos, dict) else {},
        )
    except Exception as e:
        log.exception(f'Error al registrar evento {evento_id}')
        return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'status': 'ok', 'evento_id': evento_id}, status=200)
