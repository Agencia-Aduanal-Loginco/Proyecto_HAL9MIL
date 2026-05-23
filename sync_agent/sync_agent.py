#!/usr/bin/env python3
"""
sync_agent.py — HAL9MIL Sync Agent (un servidor por patente)

Extrae referencias de CASA.GDB local (Firebird) y las sincroniza con
el servidor Django en la nube vía HTTPS POST.

Cada servidor Windows tiene su propio .env con la patente y ruta a
CASA.GDB que le corresponde. El script se copia idéntico en los 3
servidores; solo cambia el .env.

Dependencias:  pip install fdb requests
Configuración: copiar .env.example → .env  y ajustar valores

Uso:
    python sync_agent.py              # sync normal
    python sync_agent.py --dry-run    # extrae pero no envía
"""

import os
import sys
import json
import time
import logging
import datetime
import argparse

# ─────────────────────────────────────────────────────────────────────────────
# Cargar .env (mini-parser, sin dependencia de python-dotenv)
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_env_file = os.path.join(BASE_DIR, '.env')
if os.path.exists(_env_file):
    with open(_env_file, encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ─────────────────────────────────────────────────────────────────────────────
# Configuración — todas las variables vienen del .env
# ─────────────────────────────────────────────────────────────────────────────

# Patente de ESTE servidor: '1627', '1656' o '1927'
PATENTE = os.environ.get('PATENTE', '').strip()

# Ruta directa a CASA.GDB en este servidor (cada servidor tiene la suya)
DB_PATH = os.environ.get('DB_PATH', '').strip()

# Conexión Firebird
FB_HOST     = os.environ.get('FB_HOST', 'localhost')
FB_PORT     = int(os.environ.get('FB_PORT', '3050'))
FB_USER     = os.environ.get('FB_USER', 'SYSDBA')
FB_PASSWORD = os.environ.get('FB_PASSWORD', 'masterkey')
FB_CHARSET  = 'WIN1252'

# Django en la nube
DJANGO_SYNC_URL = os.environ.get('DJANGO_SYNC_URL', '').strip()
SYNC_SECRET_KEY = os.environ.get('SYNC_SECRET_KEY', '').strip()
AGENT_ID        = os.environ.get('AGENT_ID', f'servidor-{PATENTE}')

REQUEST_TIMEOUT = int(os.environ.get('REQUEST_TIMEOUT', '120'))
MAX_RETRIES     = int(os.environ.get('MAX_RETRIES', '2'))
CHUNK_SIZE      = int(os.environ.get('CHUNK_SIZE', '500'))

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
LOG_DIR  = os.path.join(BASE_DIR, 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'sync.log')
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger('sync_agent')

# ─────────────────────────────────────────────────────────────────────────────
# Estado persistente
# ─────────────────────────────────────────────────────────────────────────────
LAST_SYNC_FILE = os.path.join(BASE_DIR, 'last_sync.json')

def load_last_sync():
    if os.path.exists(LAST_SYNC_FILE):
        with open(LAST_SYNC_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_last_sync(state):
    with open(LAST_SYNC_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, default=str)

# ─────────────────────────────────────────────────────────────────────────────
# Constantes del dominio
# ─────────────────────────────────────────────────────────────────────────────
PATENTES_VALIDAS = {'1627', '1656', '1927'}

PATENTE_PREFIJO = {'1627': 'LCLF', '1656': 'LCRR', '1927': 'LCMJ'}

CVE_CONT_TIPO = {
    1: '20DC', 2: '20RF', 3: '40HC', 4: '40RF',
    9: '20TK', 11: '45HC', 16: '40OT', 17: '40OT',
    20: '40FR', 25: '40FR',
}

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def clean(val, max_len=None):
    if val is None:
        return ''
    s = str(val).strip()
    return s[:max_len] if max_len and len(s) > max_len else s


def fb_date_str(val):
    """Convierte fecha/datetime de Firebird a string ISO 'YYYY-MM-DD' o None."""
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        return val.date().isoformat()
    if isinstance(val, datetime.date):
        return val.isoformat()
    return None


def connect_fb():
    """Conecta a CASA.GDB usando la ruta configurada en DB_PATH."""
    import fdb

    # Buscar fbclient.dll de 64-bit para evitar WinError 193 (mismatch 32/64-bit)
    fb_client = os.environ.get('FB_CLIENT_PATH', '').strip()
    if fb_client:
        if os.path.exists(fb_client):
            fdb.load_api(fb_client)
        else:
            log.warning(f'FB_CLIENT_PATH no existe: {fb_client}')
    else:
        _rutas_64 = [
            r'C:\Program Files\Firebird\Firebird_2_5\bin\fbclient.dll',
            r'C:\Program Files\Firebird\Firebird_3_0\bin\fbclient.dll',
            r'C:\Program Files\Firebird\Firebird_4_0\bin\fbclient.dll',
            r'C:\Program Files\Firebird\bin\fbclient.dll',
        ]
        for _path in _rutas_64:
            if os.path.exists(_path):
                fdb.load_api(_path)
                break

    return fdb.connect(
        host=FB_HOST,
        port=FB_PORT,
        database=DB_PATH,
        user=FB_USER,
        password=FB_PASSWORD,
        charset=FB_CHARSET,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Extracción desde Firebird
# ─────────────────────────────────────────────────────────────────────────────
def fetch_clientes(cur):
    cur.execute("SELECT CVE_IMP, NOM_IMP FROM CTRAC_CLIENT WHERE NOM_IMP IS NOT NULL")
    return {clean(r[0]): clean(r[1], 255) for r in cur.fetchall()}


def fetch_capturistas(cur):
    cur.execute("SELECT LOGIN, NOMBRE FROM SISSEG_USUARI WHERE LOGIN IS NOT NULL")
    return {clean(r[0]).upper(): clean(r[1], 150) for r in cur.fetchall()}


def fetch_embar(cur):
    cur.execute("SELECT NUM_REFE, FEC_ENTR FROM CTRAO_EMBAR WHERE NUM_REFE IS NOT NULL")
    return {clean(r[0]): fb_date_str(r[1]) for r in cur.fetchall()}


def fetch_pedimentos(cur):
    """
    Retorna (dict_por_ref, set_de_todas_las_refs).
    Para cada NUM_REFE toma el primer registro (pedimento original, no rect.).
    """
    cur.execute("""
        SELECT
            NUM_REFE, CVE_IMPO, FEC_ENTR, FEC_PAGO,
            NUM_PEDI, CVE_PEDI, TIP_PEDI, ADU_DESP,
            REG_ADUA, NUM_OPER, CVE_CAPT
        FROM SAAIO_PEDIME
        WHERE NUM_REFE IS NOT NULL
        ORDER BY NUM_REFE,
                 CASE WHEN TIP_PEDI IS NULL THEN 0 ELSE 1 END,
                 FEC_PAGO NULLS LAST
    """)
    result   = {}
    all_refs = set()
    for row in cur.fetchall():
        (num_refe, cve_impo, fec_entr, fec_pago,
         num_pedi, cve_pedi, tip_pedi, adu_desp,
         reg_adua, num_oper, cve_capt) = row
        ref = clean(num_refe, 50)
        if not ref:
            continue
        all_refs.add(ref)
        if ref in result:
            continue
        result[ref] = {
            'cve_impo':         clean(cve_impo, 20),
            'fecha_validacion': fb_date_str(fec_entr),
            'fecha_pago':       fb_date_str(fec_pago),
            'num_pedimento':    clean(num_pedi, 30),
            'clave_pedimento':  clean(cve_pedi, 10),
            'tipo_pedimento':   clean(tip_pedi, 10),
            'aduana':           clean(adu_desp, 10),
            'regimen':          clean(reg_adua, 10),
            'num_operacion':    clean(num_oper, 100),
            'cve_capturista':   clean(cve_capt, 20).upper(),
        }
    return result, all_refs


def fetch_pedime2(cur):
    """Línea de captura SAT desde SAAIO_PEDIME2."""
    cur.execute("""
        SELECT NUM_REFE, PAG_LCAP
        FROM SAAIO_PEDIME2
        WHERE PAG_LCAP IS NOT NULL AND PAG_LCAP <> ''
    """)
    return {clean(r[0], 50): clean(r[1], 30) for r in cur.fetchall()}


def fetch_contenedores(cur):
    cur.execute("""
        SELECT NUM_REFE, NUM_CONT, CVE_CONT
        FROM SAAIO_CONTEN
        WHERE NUM_REFE IS NOT NULL AND NUM_CONT IS NOT NULL
    """)
    result = {}
    for num_refe, num_cont, cve_cont in cur.fetchall():
        ref  = clean(num_refe, 50)
        cont = clean(num_cont, 20)
        tipo = CVE_CONT_TIPO.get(cve_cont, '')
        if ref and cont:
            result.setdefault(ref, []).append({'num_cont': cont, 'tipo': tipo})
    return result


def fetch_guias(cur):
    cur.execute("""
        SELECT NUM_REFE, GUIA, IDE_MH
        FROM SAAIO_GUIAS
        WHERE NUM_REFE IS NOT NULL AND GUIA IS NOT NULL
    """)
    result = {}
    for num_refe, guia, ide_mh in cur.fetchall():
        ref  = clean(num_refe, 50)
        bl   = clean(guia, 60)
        tipo = clean(ide_mh, 5) or 'M'
        if ref and bl:
            result.setdefault(ref, []).append({'numero_guia': bl, 'tipo_guia': tipo})
    return result

# ─────────────────────────────────────────────────────────────────────────────
# Construcción del payload
# ─────────────────────────────────────────────────────────────────────────────
def build_payload(clientes, capturistas, embar, pedimentos,
                  all_refs, pedime2, contenedores, guias):
    prefijo   = PATENTE_PREFIJO.get(PATENTE, PATENTE)
    refs_list = []

    for ref in all_refs:
        ped          = pedimentos.get(ref, {})
        cve          = ped.get('cve_impo', '')
        cve_capt     = ped.get('cve_capturista', '')
        fecha_arribo = embar.get(ref) or ped.get('fecha_validacion')
        refs_list.append({
            'num_refe':          ref,
            'prefijo':           prefijo,
            'cve_cliente':       cve,
            'nombre_cliente':    clientes.get(cve, ''),
            'fecha_arribo':      fecha_arribo,
            'fecha_validacion':  ped.get('fecha_validacion'),
            'fecha_pago':        ped.get('fecha_pago'),
            'num_pedimento':     ped.get('num_pedimento', ''),
            'clave_pedimento':   ped.get('clave_pedimento', ''),
            'tipo_pedimento':    ped.get('tipo_pedimento', ''),
            'aduana':            ped.get('aduana', ''),
            'regimen':           ped.get('regimen', ''),
            'num_operacion':     ped.get('num_operacion', ''),
            'linea_captura':     pedime2.get(ref, ''),
            'cve_capturista':    cve_capt,
            'nombre_capturista': capturistas.get(cve_capt, ''),
            'es_rectificacion':  ref.startswith('R') and len(ref) > 5,
        })

    conts_list = [
        {'num_refe': ref, **c}
        for ref, conts in contenedores.items()
        if ref in all_refs
        for c in conts
    ]
    guias_list = [
        {'num_refe': ref, **g}
        for ref, bls in guias.items()
        if ref in all_refs
        for g in bls
    ]

    return {
        'patente':      PATENTE,
        'agent_id':     AGENT_ID,
        'timestamp':    datetime.datetime.now().isoformat(),
        'referencias':  refs_list,
        'contenedores': conts_list,
        'guias':        guias_list,
    }

# ─────────────────────────────────────────────────────────────────────────────
# Envío a Django
# ─────────────────────────────────────────────────────────────────────────────
def send_payload(payload):
    import requests as req
    headers = {
        'Authorization': f'Token {SYNC_SECRET_KEY}',
        'Content-Type':  'application/json',
    }
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = req.post(
                DJANGO_SYNC_URL,
                json=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except req.exceptions.Timeout as e:
            last_exc = e
            log.warning(f'  Timeout (intento {attempt}/{MAX_RETRIES})')
        except req.exceptions.ConnectionError as e:
            last_exc = e
            log.warning(f'  Error de conexión (intento {attempt}/{MAX_RETRIES}): {e}')
        except req.exceptions.HTTPError as e:
            body = getattr(e.response, 'text', '')[:300]
            raise RuntimeError(f'HTTP {e.response.status_code}: {body}') from e
        if attempt < MAX_RETRIES:
            time.sleep(15)
    raise RuntimeError(f'Falló después de {MAX_RETRIES} intentos') from last_exc

# ─────────────────────────────────────────────────────────────────────────────
# Validación de configuración
# ─────────────────────────────────────────────────────────────────────────────
def validar_config():
    errores = []
    if not PATENTE:
        errores.append('PATENTE no configurada en .env (valores válidos: 1627, 1656, 1927)')
    elif PATENTE not in PATENTES_VALIDAS:
        errores.append(f'PATENTE={PATENTE!r} no válida (debe ser 1627, 1656 o 1927)')
    if not DB_PATH:
        errores.append('DB_PATH no configurada en .env (ruta directa a CASA.GDB)')
    if not DJANGO_SYNC_URL:
        errores.append('DJANGO_SYNC_URL no configurada en .env')
    if not SYNC_SECRET_KEY or SYNC_SECRET_KEY == 'CAMBIAR-ESTA-CLAVE':
        errores.append('SYNC_SECRET_KEY no configurada o usa el valor de ejemplo en .env')
    return errores

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='HAL9MIL Sync Agent')
    parser.add_argument('--dry-run', action='store_true',
                        help='Extraer de Firebird pero no enviar a Django')
    args = parser.parse_args()

    log.info('══════════════════════════════════════════════════════════')
    log.info(f'HAL9MIL Sync Agent  |  {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    log.info(f'Patente  : {PATENTE}')
    log.info(f'Agent ID : {AGENT_ID}')
    log.info(f'DB Path  : {DB_PATH}')
    log.info(f'Destino  : {DJANGO_SYNC_URL}')
    if args.dry_run:
        log.info('MODO DRY-RUN — no se enviarán datos')
    log.info('══════════════════════════════════════════════════════════')

    # Validar configuración antes de intentar nada
    errores_cfg = validar_config()
    if errores_cfg:
        for e in errores_cfg:
            log.error(f'  CONFIG ERROR: {e}')
        log.error('Corregir el archivo .env y volver a ejecutar.')
        return 1

    t0 = time.time()

    # Conexión a Firebird
    log.info('Conectando a Firebird...')
    try:
        con = connect_fb()
    except Exception as e:
        log.error(f'No se pudo conectar a Firebird: {e}')
        log.error(f'  Host    : {FB_HOST}:{FB_PORT}')
        log.error(f'  DB Path : {DB_PATH}')
        log.error('  Verificar que el servicio Firebird esté activo y que DB_PATH sea correcto.')
        return 1

    # Extracción
    try:
        cur = con.cursor()
        log.info('Extrayendo datos de CASA.GDB...')
        clientes     = fetch_clientes(cur)
        capturistas  = fetch_capturistas(cur)
        embar        = fetch_embar(cur)
        pedimentos, all_refs = fetch_pedimentos(cur)
        pedime2      = fetch_pedime2(cur)
        contenedores = fetch_contenedores(cur)
        guias        = fetch_guias(cur)
    except Exception as e:
        log.error(f'Error al extraer datos de Firebird: {e}')
        return 1
    finally:
        con.close()

    n_conts = sum(len(v) for v in contenedores.values())
    n_guias = sum(len(v) for v in guias.values())
    log.info(f'Extraídos: {len(all_refs)} referencias | {n_conts} contenedores | {n_guias} guías BL')

    if args.dry_run:
        log.info('[DRY-RUN] Extracción OK, no se envía payload.')
        log.info('══════════════════════════════════════════════════════════')
        return 0

    # Construcción y envío en lotes
    refs_sorted = sorted(all_refs)
    chunks      = [refs_sorted[i:i+CHUNK_SIZE] for i in range(0, len(refs_sorted), CHUNK_SIZE)]
    n_chunks    = len(chunks)
    log.info(f'Enviando a Django en {n_chunks} lote(s) de hasta {CHUNK_SIZE} refs...')

    totales = {'creadas': 0, 'actualizadas': 0, 'errores': 0}
    try:
        for idx, chunk_refs in enumerate(chunks, 1):
            chunk_set = set(chunk_refs)
            payload   = build_payload(clientes, capturistas, embar, pedimentos,
                                      chunk_set, pedime2, contenedores, guias)
            log.info(f'  Lote {idx}/{n_chunks}: {len(payload["referencias"])} refs | '
                     f'{len(payload["contenedores"])} conts | {len(payload["guias"])} guías')
            resp = send_payload(payload)
            totales['creadas']      += resp.get('creadas', 0)
            totales['actualizadas'] += resp.get('actualizadas', 0)
            totales['errores']      += resp.get('errores', 0)
            for detalle in resp.get('error_detalle', []):
                log.warning(f'  ERROR DETALLE: {detalle}')

        elapsed = time.time() - t0
        log.info(
            f'Sync completado ✔ | '
            f'creadas={totales["creadas"]} '
            f'actualizadas={totales["actualizadas"]} '
            f'errores={totales["errores"]} '
            f'[{elapsed:.1f}s]'
        )
    except Exception as e:
        log.error(f'Error al enviar a Django: {e}')
        return 1

    # Guardar timestamp del último sync exitoso
    state = load_last_sync()
    state[PATENTE] = datetime.datetime.now().isoformat()
    save_last_sync(state)

    log.info('══════════════════════════════════════════════════════════')
    return 0


if __name__ == '__main__':
    sys.exit(main())
