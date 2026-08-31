#!/usr/bin/env python3
"""
sync_agent.py — HAL9MIL Sync Agent (un servidor por patente)

Extrae referencias de CASA.GDB local (Firebird) y las sincroniza con
el servidor Django en la nube vía HTTPS POST.

Cada servidor Windows tiene su propio config.ini con la patente y ruta a
CASA.GDB que le corresponde. El script se copia idéntico en los 3
servidores; solo cambia el config.ini.

Dependencias:  pip install fdb requests
Configuración: copiar config.ini.example → config.ini  y ajustar valores

Uso:
    python sync_agent.py              # sync incremental (solo cambios)
    python sync_agent.py --full-sync  # sync completo de todos los registros
    python sync_agent.py --dry-run    # extrae pero no envía
"""

import os
import sys
import json
import time
import logging
import datetime
import argparse
import configparser

# ─────────────────────────────────────────────────────────────────────────────
# Cargar config.ini
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.ini')

_cfg = configparser.ConfigParser()

if os.path.exists(CONFIG_FILE):
    # Intentar múltiples encodings — Windows puede guardar el archivo en distintas
    # codificaciones dependiendo del editor (Bloc de notas, VS Code, etc.)
    for _enc in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1252'):
        try:
            _cfg.read(CONFIG_FILE, encoding=_enc)
            break
        except UnicodeDecodeError:
            continue

def _get(section, key, fallback=''):
    return _cfg.get(section, key, fallback=fallback).strip()

def _getint(section, key, fallback=0):
    return _cfg.getint(section, key, fallback=fallback)

# ─────────────────────────────────────────────────────────────────────────────
# Configuración — todas las variables vienen de config.ini
# ─────────────────────────────────────────────────────────────────────────────

PATENTE = _get('servidor', 'patente')
DB_PATH = _get('firebird', 'db_path')

FB_HOST     = _get('firebird', 'host',     'localhost')
FB_PORT     = _getint('firebird', 'port',  3050)
FB_USER     = _get('firebird', 'user',     'SYSDBA')
FB_PASSWORD = _get('firebird', 'password', 'masterkey')
FB_CHARSET  = 'WIN1252'

DJANGO_SYNC_URL = _get('django', 'sync_url')
SYNC_SECRET_KEY = _get('django', 'secret_key')
AGENT_ID        = _get('servidor', 'agent_id') or f'servidor-{PATENTE}'

REQUEST_TIMEOUT = _getint('opciones', 'request_timeout', 120)
MAX_RETRIES     = _getint('opciones', 'max_retries',     2)
CHUNK_SIZE      = _getint('opciones', 'chunk_size',      500)
DODA_CHUNK_SIZE = _getint('opciones', 'doda_chunk_size', 100)

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

# Claves como str: SAAIO_CONTEN.CVE_CONT es VARCHAR(2) en CASA.GDB (Firebird)
# — fdb siempre lo devuelve como string, nunca como int. Usar claves int aquí
# hacía que CVE_CONT_TIPO.get(cve_cont, '') jamás matcheara (100% de los
# contenedores en producción quedaban con tipo='').
CVE_CONT_TIPO = {
    '1': '20DC', '2': '20RF', '3': '40HC', '4': '40RF',
    '9': '20TK', '11': '45HC', '16': '40OT', '17': '40OT',
    '20': '40FR', '25': '40FR',
}

# CVE_CAAT que identifica a Transportes Kasu en SAAIO_DODA.CVE_CAAT.
# Mantener sincronizado con hal9mil.settings.CVE_CAAT_KASU — este script
# corre fuera de Django (servidores Windows con solo Firebird) y no puede
# importar django.conf.settings.
CVE_CAAT_KASU = '3B74'

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


def fb_datetime_str(val):
    """Convierte fecha/datetime de Firebird a string ISO-8601 completo o None."""
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        return val.isoformat()
    if isinstance(val, datetime.date):
        return datetime.datetime.combine(val, datetime.time.min).isoformat()
    return None


def connect_fb():
    """Conecta a CASA.GDB usando la ruta configurada en DB_PATH."""
    import fdb

    fb_client = _get('firebird', 'fb_client_path')
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


def _fetch_rows(cur, sql_all, sql_filtered_tpl, refs_filter):
    """
    Ejecuta sql_all si refs_filter es None, o sql_filtered_tpl en lotes de
    500 refs si se especifica un filtro. sql_filtered_tpl debe contener {phs}
    donde irá la lista de parámetros IN.
    """
    if refs_filter is None:
        cur.execute(sql_all)
        return cur.fetchall()
    refs = list(refs_filter)
    if not refs:
        return []
    rows = []
    for i in range(0, len(refs), 500):
        chunk = refs[i:i + 500]
        phs = ','.join('?' * len(chunk))
        cur.execute(sql_filtered_tpl.format(phs=phs), chunk)
        rows.extend(cur.fetchall())
    return rows

# ─────────────────────────────────────────────────────────────────────────────
# Detección de cambios (solo sync incremental)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_changed_refs_since(cur, since_dt):
    """
    Retorna el conjunto de NUM_REFE que cambiaron desde since_dt.

    Fuentes de cambio:
      - SAAIO_PROCES.FEC_MODI — fecha de última modificación del proceso
      - SAAIO_PEDIME.DIA_PAGO — se establece cuando se paga el pedimento
        (el pago no siempre actualiza SAAIO_PROCES, por eso se revisa por separado)
    """
    since_str = since_dt.strftime('%Y-%m-%d %H:%M:%S')
    cur.execute("""
        SELECT DISTINCT NUM_REFE FROM (
            SELECT NUM_REFE FROM SAAIO_PROCES
            WHERE FEC_MODI >= ? AND NUM_REFE IS NOT NULL
            UNION
            SELECT NUM_REFE FROM SAAIO_PEDIME
            WHERE DIA_PAGO >= ? AND NUM_REFE IS NOT NULL
        )
    """, (since_str, since_str))
    return {clean(r[0]) for r in cur.fetchall() if r[0]}

# ─────────────────────────────────────────────────────────────────────────────
# Extracción desde Firebird
# ─────────────────────────────────────────────────────────────────────────────
def fetch_clientes(cur):
    # Catálogo pequeño (~130 filas) — siempre completo
    cur.execute("SELECT CVE_IMP, NOM_IMP FROM CTRAC_CLIENT WHERE NOM_IMP IS NOT NULL")
    return {clean(r[0]): clean(r[1], 255) for r in cur.fetchall()}


def fetch_capturistas(cur):
    # Catálogo pequeño (~42 filas) — siempre completo
    cur.execute("SELECT LOGIN, NOMBRE FROM SISSEG_USUARI WHERE LOGIN IS NOT NULL")
    return {clean(r[0]).upper(): clean(r[1], 150) for r in cur.fetchall()}


def fetch_embar(cur, refs_filter=None):
    """
    Devuelve dict {num_refe: {'fecha_arribo': ..., 'peso_bruto': ...}}.
    PES_BRUT (toneladas/kg del embarque) viene de CTRAO_EMBAR — misma tabla
    y sin JOIN adicional, ya consultada aquí para fecha_arribo.
    """
    rows = _fetch_rows(
        cur,
        "SELECT NUM_REFE, FEC_ENTR, PES_BRUT FROM CTRAO_EMBAR WHERE NUM_REFE IS NOT NULL",
        "SELECT NUM_REFE, FEC_ENTR, PES_BRUT FROM CTRAO_EMBAR WHERE NUM_REFE IN ({phs})",
        refs_filter,
    )
    return {
        clean(r[0]): {
            'fecha_arribo': fb_date_str(r[1]),
            # fdb devuelve decimal.Decimal para columnas NUMERIC/DECIMAL con escala
            # (como PES_BRUT) — decimal.Decimal no es serializable por json.dumps(),
            # así que se castea a float antes de que build_payload()/send_payload()
            # lo metan al payload JSON.
            'peso_bruto': float(r[2]) if r[2] is not None else None,
        }
        for r in rows
    }


def fetch_pedimentos(cur, refs_filter=None):
    """
    Retorna (dict_por_ref, set_de_todas_las_refs).
    Para cada NUM_REFE toma el primer registro (pedimento original, no rect.).
    """
    rows = _fetch_rows(
        cur,
        """SELECT NUM_REFE, CVE_IMPO, FEC_ENTR, FEC_PAGO,
                  NUM_PEDI, CVE_PEDI, TIP_PEDI, ADU_DESP,
                  REG_ADUA, NUM_OPER, CVE_CAPT, FIR_ELEC
           FROM SAAIO_PEDIME
           WHERE NUM_REFE IS NOT NULL
           ORDER BY NUM_REFE,
                    CASE WHEN TIP_PEDI IS NULL THEN 0 ELSE 1 END,
                    FEC_PAGO NULLS LAST""",
        """SELECT NUM_REFE, CVE_IMPO, FEC_ENTR, FEC_PAGO,
                  NUM_PEDI, CVE_PEDI, TIP_PEDI, ADU_DESP,
                  REG_ADUA, NUM_OPER, CVE_CAPT, FIR_ELEC
           FROM SAAIO_PEDIME
           WHERE NUM_REFE IN ({phs})
           ORDER BY NUM_REFE,
                    CASE WHEN TIP_PEDI IS NULL THEN 0 ELSE 1 END,
                    FEC_PAGO NULLS LAST""",
        refs_filter,
    )
    result   = {}
    all_refs = set()
    for row in rows:
        (num_refe, cve_impo, fec_entr, fec_pago,
         num_pedi, cve_pedi, tip_pedi, adu_desp,
         reg_adua, num_oper, cve_capt, fir_elec) = row
        ref = clean(num_refe, 50)
        if not ref:
            continue
        all_refs.add(ref)
        if ref in result:
            continue
        result[ref] = {
            'cve_impo':  clean(cve_impo, 20),
            'fec_entr':  fb_date_str(fec_entr),
            'fecha_pago': fb_date_str(fec_pago),
            'num_pedimento':    clean(num_pedi, 30),
            'clave_pedimento':  clean(cve_pedi, 10),
            'tipo_pedimento':   clean(tip_pedi, 10),
            'aduana':           clean(adu_desp, 10),
            'regimen':          clean(reg_adua, 10),
            'num_operacion':    clean(num_oper, 100),
            'cve_capturista':   clean(cve_capt, 20).upper(),
            'fir_elec':         clean(fir_elec, 255),
        }
    return result, all_refs


def fetch_pedime2(cur, refs_filter=None):
    """Línea de captura SAT desde SAAIO_PEDIME2."""
    rows = _fetch_rows(
        cur,
        """SELECT NUM_REFE, PAG_LCAP FROM SAAIO_PEDIME2
           WHERE PAG_LCAP IS NOT NULL AND PAG_LCAP <> ''""",
        """SELECT NUM_REFE, PAG_LCAP FROM SAAIO_PEDIME2
           WHERE NUM_REFE IN ({phs}) AND PAG_LCAP IS NOT NULL AND PAG_LCAP <> ''""",
        refs_filter,
    )
    return {clean(r[0], 50): clean(r[1], 30) for r in rows}


def fetch_contenedores(cur, refs_filter=None):
    rows = _fetch_rows(
        cur,
        """SELECT NUM_REFE, NUM_CONT, CVE_CONT FROM SAAIO_CONTEN
           WHERE NUM_REFE IS NOT NULL AND NUM_CONT IS NOT NULL""",
        """SELECT NUM_REFE, NUM_CONT, CVE_CONT FROM SAAIO_CONTEN
           WHERE NUM_REFE IN ({phs}) AND NUM_CONT IS NOT NULL""",
        refs_filter,
    )
    result = {}
    for num_refe, num_cont, cve_cont in rows:
        ref  = clean(num_refe, 50)
        cont = clean(num_cont, 20)
        tipo = CVE_CONT_TIPO.get(clean(cve_cont), '')
        if ref and cont:
            result.setdefault(ref, []).append({'num_cont': cont, 'tipo': tipo})
    return result


def fetch_guias(cur, refs_filter=None):
    rows = _fetch_rows(
        cur,
        """SELECT NUM_REFE, GUIA, IDE_MH FROM SAAIO_GUIAS
           WHERE NUM_REFE IS NOT NULL AND GUIA IS NOT NULL""",
        """SELECT NUM_REFE, GUIA, IDE_MH FROM SAAIO_GUIAS
           WHERE NUM_REFE IN ({phs}) AND GUIA IS NOT NULL""",
        refs_filter,
    )
    result = {}
    for num_refe, guia, ide_mh in rows:
        ref  = clean(num_refe, 50)
        bl   = clean(guia, 60)
        tipo = clean(ide_mh, 5) or 'M'
        if ref and bl:
            result.setdefault(ref, []).append({'numero_guia': bl, 'tipo_guia': tipo})
    return result


def fetch_proces(cur, refs_filter=None):
    """Retorna dict {num_refe: fecha_captura} desde SAAIO_PROCES.FEC_CAPT."""
    rows = _fetch_rows(
        cur,
        """SELECT NUM_REFE, FEC_CAPT FROM SAAIO_PROCES
           WHERE NUM_REFE IS NOT NULL AND FEC_CAPT IS NOT NULL""",
        """SELECT NUM_REFE, FEC_CAPT FROM SAAIO_PROCES
           WHERE NUM_REFE IN ({phs}) AND FEC_CAPT IS NOT NULL""",
        refs_filter,
    )
    return {clean(r[0], 50): fb_date_str(r[1]) for r in rows if r[0]}


def fetch_regval(cur, refs_filter=None):
    """Retorna dict {num_refe: fecha_validacion} desde SAAIO_REGVAL.FEC_VAL."""
    rows = _fetch_rows(
        cur,
        """SELECT NUM_REFE, MIN(FEC_VAL) FROM SAAIO_REGVAL
           WHERE NUM_REFE IS NOT NULL AND FEC_VAL IS NOT NULL
           GROUP BY NUM_REFE""",
        """SELECT NUM_REFE, MIN(FEC_VAL) FROM SAAIO_REGVAL
           WHERE NUM_REFE IN ({phs}) AND FEC_VAL IS NOT NULL
           GROUP BY NUM_REFE""",
        refs_filter,
    )
    return {clean(r[0], 50): fb_date_str(r[1]) for r in rows if r[0]}


def fetch_partidas_count(cur, refs_filter=None):
    """Devuelve dict {num_refe: num_partidas} desde SAAIO_FRACCI."""
    rows = _fetch_rows(
        cur,
        """SELECT NUM_REFE, COUNT(*) FROM SAAIO_FRACCI
           WHERE NUM_REFE IS NOT NULL GROUP BY NUM_REFE""",
        """SELECT NUM_REFE, COUNT(*) FROM SAAIO_FRACCI
           WHERE NUM_REFE IN ({phs}) GROUP BY NUM_REFE""",
        refs_filter,
    )
    return {clean(r[0], 50): int(r[1]) for r in rows if r[0]}


def fetch_dodas(cur, since_dt=None):
    """
    Extrae DODA de la CVE_CAAT de Transportes Kasu, con las referencias
    ligadas (SAAIO_DODADO) y la terminal resuelta vía SAAIO_IDEPED
    (CVE_IDEN='CR', COM_IDEN = clave de terminal) + SAAIC_REFIS.

    since_dt=None  → sync completo: solo DODAs activos (FEC_BAJA IS NULL).
                     Se usa en la primera sync de una patente y con --full-sync.
    since_dt=<dt>  → sync incremental: solo DODAs con cambios desde esa marca:
                       - FEC_DODAE >= since_dt  (nuevos)
                       - FEC_BAJA  >= since_dt  (recién dados de baja)
                       - NUM_DODA que aparece en el BAJ_DODA de un DODA nuevo
                         (el DODA reemplazado — puede ser viejo — para que
                         Django lo marque como baja).
                     En incremental NO se filtra FEC_BAJA IS NULL: los DODAs
                     de baja fluyen con su fecha_baja poblada.

    Retorna una lista de dicts listos para el bloque "dodas" del payload:
        {id_doda, num_doda, patente, cve_caat, cve_capt, terminal_cve,
         terminal_nombre, fecha_doda, fecha_baja, baj_doda,
         referencias: [{num_refe, cons_id}, ...]}
    """
    select = """
        SELECT
            d.ID_DODA, d.NUM_DODA, d.CVE_CAAT, d.CVE_CAPT,
            d.FEC_DODAE, d.FEC_BAJA, d.BAJ_DODA,
            dd.NUM_REFE, dd.CONS_ID,
            rf.CVE_REFI, rf.NOM_REFI
        FROM SAAIO_DODA d
        JOIN SAAIO_DODADO dd ON dd.ID_DODA = d.ID_DODA
        LEFT JOIN SAAIO_IDEPED ip
            ON ip.NUM_REFE = dd.NUM_REFE AND ip.CVE_IDEN = 'CR'
        LEFT JOIN SAAIC_REFIS rf ON rf.CVE_REFI = ip.COM_IDEN
    """

    if since_dt is None:
        cur.execute(select + " WHERE d.CVE_CAAT = ? AND d.FEC_BAJA IS NULL",
                    (CVE_CAAT_KASU,))
    else:
        since_str = since_dt.strftime('%Y-%m-%d %H:%M:%S')
        cur.execute(select + """
            WHERE d.CVE_CAAT = ?
              AND (
                    d.FEC_DODAE >= ?
                 OR d.FEC_BAJA  >= ?
                 OR TRIM(d.NUM_DODA) IN (
                      SELECT TRIM(n.BAJ_DODA) FROM SAAIO_DODA n
                      WHERE n.CVE_CAAT = ? AND n.BAJ_DODA IS NOT NULL
                        AND n.FEC_DODAE >= ?
                    )
              )
        """, (CVE_CAAT_KASU, since_str, since_str, CVE_CAAT_KASU, since_str))

    dodas = {}
    for row in cur.fetchall():
        (id_doda, num_doda, cve_caat, cve_capt, fec_dodae, fec_baja, baj_doda,
         num_refe, cons_id, terminal_cve, terminal_nombre) = row
        if id_doda is None:
            continue
        entry = dodas.setdefault(id_doda, {
            'id_doda':         int(id_doda),
            'num_doda':        clean(num_doda, 34),
            'patente':         PATENTE,
            'cve_caat':        clean(cve_caat, 6),
            'cve_capt':        clean(cve_capt, 20).upper(),
            'terminal_cve':    '',
            'terminal_nombre': '',
            'fecha_doda':      fb_datetime_str(fec_dodae),
            'fecha_baja':      fb_datetime_str(fec_baja),
            'baj_doda':        clean(baj_doda, 34),
            'referencias':     [],
        })
        if not entry['terminal_cve'] and terminal_cve:
            entry['terminal_cve']    = clean(terminal_cve, 4)
            entry['terminal_nombre'] = clean(terminal_nombre, 70)
        ref = clean(num_refe, 15)
        if ref and cons_id is not None:
            entry['referencias'].append({'num_refe': ref, 'cons_id': int(cons_id)})
    return list(dodas.values())

def _chunk_list(items, size):
    """Trocea items en sublistas de a lo más `size` elementos, preservando orden."""
    return [items[i:i + size] for i in range(0, len(items), size)]


# ─────────────────────────────────────────────────────────────────────────────
# Construcción del payload
# ─────────────────────────────────────────────────────────────────────────────
def build_payload(clientes, capturistas, embar, pedimentos,
                  all_refs, pedime2, contenedores, guias, partidas_count, proces, regval,
                  dodas=None, no_notificar=False):
    prefijo   = PATENTE_PREFIJO.get(PATENTE, PATENTE)
    refs_list = []

    for ref in all_refs:
        ped          = pedimentos.get(ref, {})
        cve          = ped.get('cve_impo', '')
        cve_capt     = ped.get('cve_capturista', '')
        embar_ref    = embar.get(ref, {})
        fecha_arribo = embar_ref.get('fecha_arribo') or ped.get('fec_entr')
        refs_list.append({
            'num_refe':          ref,
            'prefijo':           prefijo,
            'cve_cliente':       cve,
            'nombre_cliente':    clientes.get(cve, ''),
            'fecha_arribo':      fecha_arribo,
            'peso_bruto':        embar_ref.get('peso_bruto'),
            'fecha_validacion':  regval.get(ref),
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
            'fir_elec':          ped.get('fir_elec', ''),
            'es_rectificacion':  ref.startswith('R') and len(ref) > 5,
            'num_partidas':      partidas_count.get(ref, 0),
            'fecha_captura':     proces.get(ref),
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
        'dodas':        dodas or [],
        'no_notificar': no_notificar,
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


def enviar_dodas_en_lotes(dodas, no_notificar, totales):
    """
    Envía los DODAs en tandas propias, en payloads separados de los de
    referencias/contenedores/guías.

    IMPORTANTE — orden de envío: esta función sólo debe llamarse después de
    que TODAS las referencias/contenedores/guías del run ya fueron enviados
    y confirmados en Django (ver el loop de lotes en main()). Si un DODA
    referencia un NUM_REFE que Django todavía no tiene, el correo automático
    de modulación sale con un PDF vacío (sin pedimento, sin contenedores) y
    notificado_en queda marcado, así que nunca se reintenta aunque la
    referencia llegue después. Mandar los DODAs solo al final, ya con todas
    las refs del run comprometidas en la BD, evita ese caso.

    no_notificar=True le indica a Django que marque los DODAs nuevos como ya
    atendidos (notificado_en/modulacion_enviada_en = ahora) SIN mandar
    correo/PDF ni hacer push a BitacoraKasu — se usa en la primera
    sincronización de una patente (ver es_primera_vez en main()), para no
    disparar miles de correos por DODAs que ya estaban abiertos en CASA
    antes de existir esta integración. Mismo comportamiento que
    `--no-notificar` en el management command import_firebird.
    """
    if not dodas:
        return
    lotes = _chunk_list(dodas, DODA_CHUNK_SIZE)
    log.info(f'Enviando {len(dodas)} DODA(s) en {len(lotes)} tanda(s) de hasta '
             f'{DODA_CHUNK_SIZE} (no_notificar={no_notificar})...')
    for idx, lote in enumerate(lotes, 1):
        payload = build_payload(
            {}, {}, {}, {}, set(), {}, {}, {}, {}, {}, {},
            dodas=lote, no_notificar=no_notificar,
        )
        log.info(f'  Tanda DODA {idx}/{len(lotes)}: {len(lote)} DODAs')
        resp = send_payload(payload)
        totales['creadas']      += resp.get('creadas', 0)
        totales['actualizadas'] += resp.get('actualizadas', 0)
        totales['errores']      += resp.get('errores', 0)
        for detalle in resp.get('error_detalle', []):
            log.warning(f'  ERROR DETALLE: {detalle}')

# ─────────────────────────────────────────────────────────────────────────────
# Validación de configuración
# ─────────────────────────────────────────────────────────────────────────────
def validar_config():
    errores = []
    if not os.path.exists(CONFIG_FILE):
        errores.append(f'No se encontró config.ini en {BASE_DIR} — copiar config.ini.example y ajustar valores')
        return errores
    if not PATENTE:
        errores.append('[servidor] patente no configurada en config.ini (valores válidos: 1627, 1656, 1927)')
    elif PATENTE not in PATENTES_VALIDAS:
        errores.append(f'[servidor] patente={PATENTE!r} no válida (debe ser 1627, 1656 o 1927)')
    if not DB_PATH:
        errores.append('[firebird] db_path no configurado en config.ini (ruta directa a CASA.GDB)')
    if not DJANGO_SYNC_URL:
        errores.append('[django] sync_url no configurada en config.ini')
    if not SYNC_SECRET_KEY or SYNC_SECRET_KEY == 'CAMBIAR-ESTA-CLAVE':
        errores.append('[django] secret_key no configurada o usa el valor de ejemplo en config.ini')
    return errores

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='HAL9MIL Sync Agent')
    parser.add_argument('--dry-run', action='store_true',
                        help='Extraer de Firebird pero no enviar a Django')
    parser.add_argument('--full-sync', action='store_true',
                        help='Forzar sincronización completa ignorando last_sync.json')
    args = parser.parse_args()

    log.info('══════════════════════════════════════════════════════════')
    log.info(f'HAL9MIL Sync Agent  |  {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    log.info(f'Patente  : {PATENTE}')
    log.info(f'Agent ID : {AGENT_ID}')
    log.info(f'DB Path  : {DB_PATH}')
    log.info(f'Destino  : {DJANGO_SYNC_URL}')
    if args.dry_run:
        log.info('MODO DRY-RUN — no se enviarán datos')
    if args.full_sync:
        log.info('MODO FULL-SYNC — se sincronizarán todos los registros')
    log.info('══════════════════════════════════════════════════════════')

    errores_cfg = validar_config()
    if errores_cfg:
        for e in errores_cfg:
            log.error(f'  CONFIG ERROR: {e}')
        log.error('Corregir el archivo config.ini y volver a ejecutar.')
        return 1

    t0 = time.time()

    # Determinar si es sync incremental o completo
    state         = load_last_sync()
    last_sync_dt  = None
    refs_filter   = None   # None = sin filtro = sync completo

    # Primera vez que esta patente sincroniza (nunca hay estado guardado para
    # ella) — independiente de si se pasó --full-sync. Se usa para mandar
    # no_notificar=True a Django y así sembrar los DODAs históricos vigentes
    # sin disparar correo/PDF/webhook por cada uno (ver enviar_dodas_en_lotes).
    es_primera_vez = PATENTE not in state
    if es_primera_vez:
        log.info('Primera sincronización de esta patente — los DODAs se '
                 'mandarán con no_notificar=True (sin correo/PDF/webhook).')

    if not args.full_sync:
        ts = state.get(PATENTE)
        if ts:
            try:
                last_sync_dt = datetime.datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                log.warning(f'Timestamp inválido en last_sync.json: {ts!r} — se hará sync completo')

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

    try:
        cur = con.cursor()

        # Detección de cambios (solo en modo incremental)
        if last_sync_dt:
            log.info(f'Sync incremental desde {last_sync_dt.strftime("%Y-%m-%d %H:%M:%S")}')
            refs_filter = fetch_changed_refs_since(cur, last_sync_dt)
            if not refs_filter:
                log.info('Sin cambios detectados desde el último sync.')
                log.info('══════════════════════════════════════════════════════════')
                state[PATENTE] = datetime.datetime.now().isoformat()
                save_last_sync(state)
                return 0
            log.info(f'Referencias con cambios: {len(refs_filter)}')
        else:
            log.info('Sync completo (primera ejecución o --full-sync)')

        # Extracción
        log.info('Extrayendo datos de CASA.GDB...')
        clientes     = fetch_clientes(cur)
        capturistas  = fetch_capturistas(cur)
        embar        = fetch_embar(cur, refs_filter)
        pedimentos, all_refs = fetch_pedimentos(cur, refs_filter)
        pedime2      = fetch_pedime2(cur, refs_filter)
        contenedores = fetch_contenedores(cur, refs_filter)
        guias        = fetch_guias(cur, refs_filter)
        partidas     = fetch_partidas_count(cur, refs_filter)
        proces       = fetch_proces(cur, refs_filter)
        regval       = fetch_regval(cur, refs_filter)
        dodas        = fetch_dodas(cur)

    except Exception as e:
        log.error(f'Error al extraer datos de Firebird: {e}')
        return 1
    finally:
        con.close()

    n_conts    = sum(len(v) for v in contenedores.values())
    n_guias    = sum(len(v) for v in guias.values())
    n_partidas = sum(partidas.values())
    log.info(f'Extraídos: {len(all_refs)} referencias | {n_partidas} partidas | {n_conts} contenedores | {n_guias} guías BL | {len(dodas)} DODAs')

    if args.dry_run:
        log.info('[DRY-RUN] Extracción OK, no se envía payload.')
        log.info('══════════════════════════════════════════════════════════')
        return 0

    if not all_refs:
        if dodas:
            # No hay referencias con pedimentos en el filtro, pero sí hay DODAs
            # pendientes (fetch_dodas() no depende de all_refs — ver su docstring).
            # Enviarlos en tandas en vez de descartarlos silenciosamente.
            log.info('Sin referencias con pedimentos en el filtro, pero hay '
                     f'{len(dodas)} DODA(s) pendiente(s) — enviando solo DODAs.')
            totales_dodas = {'creadas': 0, 'actualizadas': 0, 'errores': 0}
            try:
                enviar_dodas_en_lotes(dodas, es_primera_vez, totales_dodas)
                errores = totales_dodas['errores']
                if errores == 0:
                    state[PATENTE] = datetime.datetime.now().isoformat()
                    save_last_sync(state)
                else:
                    log.warning(
                        f'{errores} error(es) al enviar DODAs — last_sync.json NO actualizado.'
                    )
            except Exception as e:
                log.error(f'Error al enviar DODAs a Django: {e}')
                return 1
            log.info('══════════════════════════════════════════════════════════')
            return 0
        log.info('No hay referencias con pedimentos en el filtro — nada que enviar.')
        log.info('══════════════════════════════════════════════════════════')
        state[PATENTE] = datetime.datetime.now().isoformat()
        save_last_sync(state)
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
            # Los DODAs NO van pegados a estos lotes de refs — se mandan aparte,
            # en sus propias tandas, una vez que todos los lotes de refs de este
            # run ya se enviaron y confirmaron (ver enviar_dodas_en_lotes()).
            payload   = build_payload(clientes, capturistas, embar, pedimentos,
                                      chunk_set, pedime2, contenedores, guias, partidas, proces, regval)
            log.info(f'  Lote {idx}/{n_chunks}: {len(payload["referencias"])} refs | '
                     f'{len(payload["contenedores"])} conts | {len(payload["guias"])} guías')
            resp = send_payload(payload)
            totales['creadas']      += resp.get('creadas', 0)
            totales['actualizadas'] += resp.get('actualizadas', 0)
            totales['errores']      += resp.get('errores', 0)
            for detalle in resp.get('error_detalle', []):
                log.warning(f'  ERROR DETALLE: {detalle}')

        # Todas las refs/contenedores/guías de este run ya están comprometidos
        # en la BD — recién ahora es seguro mandar los DODAs (ver docstring de
        # enviar_dodas_en_lotes()).
        enviar_dodas_en_lotes(dodas, es_primera_vez, totales)

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

    if totales['errores'] == 0:
        state[PATENTE] = datetime.datetime.now().isoformat()
        save_last_sync(state)
    else:
        log.warning(
            f'{totales["errores"]} ref(s) con error — last_sync.json NO actualizado. '
            'Las refs fallidas serán reintentadas en el próximo ciclo.'
        )

    log.info('══════════════════════════════════════════════════════════')
    return 0


if __name__ == '__main__':
    sys.exit(main())
