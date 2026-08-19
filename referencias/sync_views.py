"""
sync_views.py — Endpoint de sincronización para el agente local CASA.GDB

POST /api/sync/
Authorization: Token <SYNC_SECRET_KEY>
Content-Type: application/json

Payload esperado:
{
  "patente": "1627",
  "agent_id": "windows-servidor-loginco",
  "timestamp": "2026-05-14T06:00:00",
  "referencias":  [ { "num_refe": "LCLF0001/26", ... } ],
  "contenedores": [ { "num_refe": "LCLF0001/26", "num_cont": "HLXU1234567", "tipo": "40HC" } ],
  "guias":        [ { "num_refe": "LCLF0001/26", "numero_guia": "HLCUXM...", "tipo_guia": "M" } ],
  "dodas":        [ { "id_doda": 123, "num_doda": "...", "referencias": [...] } ],
  "no_notificar": false
}

no_notificar=true: las DODAs nuevas de este payload quedan marcadas como ya
atendidas (notificado_en/modulacion_enviada_en = ahora) SIN correo, SIN PDF,
SIN push a BitacoraKasu y SIN crear EnvioModulacion. Lo manda sync_agent.py
en la primera sincronización de una patente (bootstrap), mismo
comportamiento que --no-notificar en el management command import_firebird.

Respuesta:
{ "patente": "1627", "creadas": 5, "actualizadas": 120, "procesados": 125,
  "errores": 0, "duracion_seg": 4.2, "timestamp": "..." }
"""

import json
import logging
import datetime

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Referencia, Contenedor, GuiaBL, LogSync, Doda, DodaReferencia, PATENTE_PREFIJO

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────
def _token_valido(request):
    auth     = request.headers.get('Authorization', '')
    expected = getattr(settings, 'SYNC_SECRET_KEY', '')
    if not expected:
        log.error('SYNC_SECRET_KEY no configurada en Django settings')
        return False
    if not auth.startswith('Token '):
        return False
    return auth[6:].strip() == expected


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint principal
# ─────────────────────────────────────────────────────────────────────────────
@csrf_exempt
@require_POST
def sync_endpoint(request):
    t0 = datetime.datetime.now()

    if not _token_valido(request):
        return JsonResponse({'error': 'Token inválido o ausente'}, status=403)

    try:
        body = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return JsonResponse({'error': f'JSON inválido: {e}'}, status=400)

    patente  = str(body.get('patente', '')).strip()
    agent_id = str(body.get('agent_id', 'unknown'))[:50]

    if patente not in PATENTE_PREFIJO:
        return JsonResponse({'error': f'Patente inválida: {patente!r}'}, status=400)

    referencias  = body.get('referencias', [])
    contenedores = body.get('contenedores', [])
    guias        = body.get('guias', [])
    dodas        = body.get('dodas', [])
    no_notificar = bool(body.get('no_notificar', False))

    if not isinstance(referencias, list):
        return JsonResponse({'error': '"referencias" debe ser una lista'}, status=400)

    stats      = {'creadas': 0, 'actualizadas': 0, 'errores': 0}
    error_msgs = []

    try:
        with transaction.atomic():
            _upsert_referencias(patente, referencias, stats, error_msgs)
            _upsert_contenedores(contenedores, stats, error_msgs)
            _upsert_guias(guias, stats, error_msgs)
            dodas_creadas = _upsert_dodas(dodas, stats, error_msgs)
            if dodas_creadas and no_notificar:
                # Bootstrap: mismo comportamiento que --no-notificar en el
                # management command import_firebird — marca las DODAs nuevas
                # como ya atendidas SIN mandar correo/PDF ni push a
                # BitacoraKasu y SIN crear EnvioModulacion. Usado por
                # sync_agent.py en la primera sincronización de una patente,
                # para no disparar miles de notificaciones por DODAs que ya
                # estaban abiertas en CASA antes de esta integración.
                ahora = timezone.now()
                for doda in dodas_creadas:
                    doda.notificado_en = ahora
                    doda.modulacion_enviada_en = ahora
                Doda.objects.bulk_update(
                    dodas_creadas, ['notificado_en', 'modulacion_enviada_en'],
                )
            elif dodas_creadas:
                # Import diferido para evitar un ciclo de import a nivel de
                # módulo (modulacion.py importa de .models, .bitacorakasu_client).
                # Se encola con on_commit para que el correo/push corran
                # después de confirmar el sync y no lo bloqueen ni lo dejen
                # a medias si Firebird trae más lotes.
                from . import modulacion
                transaction.on_commit(
                    lambda: modulacion.procesar_dodas_nuevas(dodas_creadas)
                )
    except Exception as e:
        log.exception(f'Error en sync patente {patente} desde {agent_id}')
        duracion = (datetime.datetime.now() - t0).total_seconds()
        LogSync.objects.create(
            patente=patente, agent_id=agent_id,
            referencias=0, contenedores=0, guias=0,
            creadas=0, actualizadas=0,
            exitoso=False, error=str(e), duracion_seg=duracion,
        )
        return JsonResponse({'error': str(e)}, status=500)

    duracion = (datetime.datetime.now() - t0).total_seconds()
    exitoso  = stats['errores'] == 0

    LogSync.objects.create(
        patente=patente,
        agent_id=agent_id,
        referencias=stats['creadas'] + stats['actualizadas'],
        contenedores=len(contenedores),
        guias=len(guias),
        creadas=stats['creadas'],
        actualizadas=stats['actualizadas'],
        exitoso=exitoso,
        error='\n'.join(error_msgs[:20]),
        duracion_seg=round(duracion, 2),
    )

    log.info(
        f'Sync OK | patente={patente} agent={agent_id} '
        f'creadas={stats["creadas"]} actualizadas={stats["actualizadas"]} '
        f'errores={stats["errores"]} [{duracion:.1f}s]'
    )

    return JsonResponse({
        'patente':       patente,
        'creadas':       stats['creadas'],
        'actualizadas':  stats['actualizadas'],
        'procesados':    stats['creadas'] + stats['actualizadas'],
        'errores':       stats['errores'],
        'error_detalle': error_msgs[:10],
        'duracion_seg':  round(duracion, 2),
        'timestamp':     t0.isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Upsert helpers
# ─────────────────────────────────────────────────────────────────────────────
def _upsert_referencias(patente, referencias, stats, error_msgs):
    prefijo = PATENTE_PREFIJO[patente]
    for item in referencias:
        num_refe = str(item.get('num_refe', '')).strip()
        if not num_refe:
            continue
        try:
            defaults = {
                'patente':          patente,
                'prefijo':          str(item.get('prefijo', prefijo))[:10],
                'cve_cliente':      str(item.get('cve_cliente', ''))[:20],
                'nombre_cliente':   str(item.get('nombre_cliente', ''))[:255],
                'fecha_arribo':     item.get('fecha_arribo') or None,
                'peso_bruto':       item.get('peso_bruto') or None,
                'fecha_validacion': item.get('fecha_validacion') or None,
                'fecha_pago':       item.get('fecha_pago') or None,
                'num_pedimento':    str(item.get('num_pedimento', ''))[:30],
                'clave_pedimento':  str(item.get('clave_pedimento', ''))[:10],
                'tipo_pedimento':   str(item.get('tipo_pedimento', ''))[:10],
                'aduana':           str(item.get('aduana', ''))[:10],
                'regimen':          str(item.get('regimen', ''))[:10],
                'num_operacion':    str(item.get('num_operacion', ''))[:100],
                'linea_captura':    str(item.get('linea_captura', ''))[:30],
                'cve_capturista':   str(item.get('cve_capturista', ''))[:20],
                'nombre_capturista': str(item.get('nombre_capturista', ''))[:150],
                'fir_elec':         str(item.get('fir_elec', ''))[:255],
                'es_rectificacion': bool(item.get('es_rectificacion', False)),
                'num_partidas':     int(item.get('num_partidas', 0) or 0),
                'fecha_captura':    item.get('fecha_captura') or None,
            }
            _, created = Referencia.objects.update_or_create(
                num_refe=num_refe,
                defaults=defaults,
            )
            if created:
                stats['creadas'] += 1
            else:
                stats['actualizadas'] += 1
        except Exception as e:
            stats['errores'] += 1
            error_msgs.append(f'ref {num_refe}: {e}')
            log.warning(f'Error upsert referencia {num_refe}: {e}')


def _upsert_contenedores(contenedores, stats, error_msgs):
    _refs = {}   # caché num_refe → Referencia obj
    for item in contenedores:
        num_refe = str(item.get('num_refe', '')).strip()
        num_cont = str(item.get('num_cont', '')).strip()
        if not num_refe or not num_cont:
            continue
        try:
            if num_refe not in _refs:
                try:
                    _refs[num_refe] = Referencia.objects.get(num_refe=num_refe)
                except Referencia.DoesNotExist:
                    _refs[num_refe] = None
            ref_obj = _refs[num_refe]
            if ref_obj is None:
                continue
            Contenedor.objects.get_or_create(
                referencia=ref_obj,
                num_cont=num_cont,
                defaults={'tipo': str(item.get('tipo', ''))[:10]},
            )
        except Exception as e:
            stats['errores'] += 1
            error_msgs.append(f'cont {num_cont}: {e}')


def _parse_dt(value):
    """Convierte un string ISO-8601 (con o sin tz) en un datetime aware, o None."""
    if not value:
        return None
    dt = parse_datetime(value) if isinstance(value, str) else value
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def _upsert_dodas(dodas, stats, error_msgs):
    """
    Upsert de DODAs (SAAIO_DODA) + sus referencias ligadas (SAAIO_DODADO).
    El filtro por CVE_CAAT (patente Transportes Kasu) ocurre en la query de
    origen (sync_agent.py / import_firebird.py) — esta función procesa
    tal cual lo que recibe.

    Devuelve la lista de instancias Doda con created=True, para que quien
    llame pueda encadenar el disparo de notificación en una tarea futura.
    """
    creadas = []
    for item in dodas:
        id_doda = item.get('id_doda')
        if id_doda in (None, ''):
            continue
        try:
            id_doda = int(id_doda)
            defaults = {
                'num_doda':        str(item.get('num_doda', ''))[:34],
                'patente':         str(item.get('patente', ''))[:10],
                'cve_caat':        str(item.get('cve_caat', ''))[:6],
                'cve_capt':        str(item.get('cve_capt', ''))[:20],
                'terminal_cve':    str(item.get('terminal_cve', ''))[:4],
                'terminal_nombre': str(item.get('terminal_nombre', ''))[:70],
                'fecha_doda':      _parse_dt(item.get('fecha_doda')),
                'fecha_baja':      _parse_dt(item.get('fecha_baja')),
            }
            doda, created = Doda.objects.update_or_create(
                id_doda=id_doda,
                defaults=defaults,
            )
            if created:
                creadas.append(doda)

            for ref_item in item.get('referencias', []):
                cons_id = ref_item.get('cons_id')
                if cons_id in (None, ''):
                    continue
                num_refe = str(ref_item.get('num_refe', '')).strip()[:15]
                DodaReferencia.objects.update_or_create(
                    doda=doda,
                    cons_id=int(cons_id),
                    defaults={
                        'num_refe':   num_refe,
                        'referencia': Referencia.objects.filter(num_refe=num_refe).first(),
                    },
                )
        except Exception as e:
            stats['errores'] += 1
            error_msgs.append(f'doda {id_doda}: {e}')
            log.warning(f'Error upsert doda {id_doda}: {e}')
    return creadas


def _upsert_guias(guias, stats, error_msgs):
    _refs = {}
    for item in guias:
        num_refe    = str(item.get('num_refe', '')).strip()
        numero_guia = str(item.get('numero_guia', '')).strip()
        if not num_refe or not numero_guia:
            continue
        try:
            if num_refe not in _refs:
                try:
                    _refs[num_refe] = Referencia.objects.get(num_refe=num_refe)
                except Referencia.DoesNotExist:
                    _refs[num_refe] = None
            ref_obj = _refs[num_refe]
            if ref_obj is None:
                continue
            GuiaBL.objects.get_or_create(
                referencia=ref_obj,
                numero_guia=numero_guia,
                defaults={'tipo_guia': str(item.get('tipo_guia', 'M'))[:5]},
            )
        except Exception as e:
            stats['errores'] += 1
            error_msgs.append(f'guia {numero_guia}: {e}')
