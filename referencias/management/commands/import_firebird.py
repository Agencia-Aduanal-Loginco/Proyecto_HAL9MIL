"""
Management command: import_firebird

Extrae referencias, pedimentos, contenedores, guías BL y DODAs de las tres
bases de datos Firebird CASA.GDB (patentes 1627, 1656, 1927) e importa todo
al modelo Django de HAL9MIL.

Uso:
    python manage.py import_firebird
    python manage.py import_firebird --dry-run
    python manage.py import_firebird --patentes 1627 1656
    python manage.py import_firebird --solo-referencias
    python manage.py import_firebird --solo-contenedores
    python manage.py import_firebird --solo-bls
"""

import datetime

import fdb
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from referencias.models import (
    CVE_CONT_TIPO, PATENTE_PREFIJO,
    Contenedor, GuiaBL, Referencia,
)
from referencias.sync_views import _upsert_dodas

# ---------------------------------------------------------------------------
# Firebird connection params
# ---------------------------------------------------------------------------
FB_HOST     = 'localhost'
FB_PORT     = 3050
FB_USER     = 'SYSDBA'
FB_PASSWORD = 'masterkey'
FB_CHARSET  = 'WIN1252'
DB_TEMPLATE = '/databases/{patente}/CASA.GDB'

PATENTES = ['1627', '1656', '1927']


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean(val, max_len=None):
    if val is None:
        return ''
    s = str(val).strip()
    return s[:max_len] if max_len and len(s) > max_len else s


def fb_date(val):
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        return val.date()
    if isinstance(val, datetime.date):
        return val
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


def connect(patente):
    return fdb.connect(
        host=FB_HOST, port=FB_PORT,
        database=DB_TEMPLATE.format(patente=patente),
        user=FB_USER, password=FB_PASSWORD,
        charset=FB_CHARSET,
    )


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def fetch_clientes(cur):
    """Devuelve dict {cve_imp: nombre} desde CTRAC_CLIENT."""
    cur.execute("""
        SELECT CVE_IMP, NOM_IMP
        FROM CTRAC_CLIENT
        WHERE NOM_IMP IS NOT NULL
    """)
    return {clean(r[0]): clean(r[1], 255) for r in cur.fetchall()}


def fetch_capturistas(cur):
    """Devuelve dict {login: nombre_completo} desde SISSEG_USUARI."""
    cur.execute("""
        SELECT LOGIN, NOMBRE
        FROM SISSEG_USUARI
        WHERE LOGIN IS NOT NULL
    """)
    return {clean(r[0]).upper(): clean(r[1], 150) for r in cur.fetchall()}


def fetch_embar(cur):
    """
    Devuelve dict {num_refe: {'fecha_arribo': ..., 'peso_bruto': ...}} desde
    CTRAO_EMBAR. PES_BRUT (toneladas del embarque) viene de la misma tabla,
    sin JOIN adicional.
    """
    cur.execute("""
        SELECT NUM_REFE, FEC_ENTR, PES_BRUT
        FROM CTRAO_EMBAR
        WHERE NUM_REFE IS NOT NULL
    """)
    return {
        clean(r[0]): {'fecha_arribo': fb_date(r[1]), 'peso_bruto': r[2]}
        for r in cur.fetchall()
    }


def fetch_pedimentos(cur):
    """
    Devuelve dict {num_refe: primer_pedimento_dict} desde SAAIO_PEDIME.
    Para cada NUM_REFE toma el primer registro con NUM_PEDI no nulo
    (el pedimento original, no la rectificación).
    También retorna el total de referencias únicas encontradas.
    NUM_OPER = número de operación bancaria (confirma pago real en banco).
    CVE_CAPT = clave del usuario CASA que capturó el pedimento.
    NUM_CAND (línea de captura SAT) no es guardada por CASA — siempre NULL.
    """
    cur.execute("""
        SELECT
            NUM_REFE, CVE_IMPO, IMP_EXPO,
            FEC_ENTR, FEC_PAGO,
            NUM_PEDI, CVE_PEDI, TIP_PEDI,
            ADU_DESP, REG_ADUA, PAT_AGEN,
            NUM_OPER, CVE_CAPT, FIR_ELEC
        FROM SAAIO_PEDIME
        WHERE NUM_REFE IS NOT NULL
        ORDER BY NUM_REFE,
                 CASE WHEN TIP_PEDI IS NULL THEN 0 ELSE 1 END,
                 FEC_PAGO NULLS LAST
    """)
    rows = cur.fetchall()
    result = {}
    all_refs = set()
    for row in rows:
        (num_refe, cve_impo, _imp_expo,
         fec_entr, fec_pago,
         num_pedi, cve_pedi, tip_pedi,
         adu_desp, reg_adua, _pat_agen,
         num_oper, cve_capt, fir_elec) = row

        ref = clean(num_refe, 50)
        if not ref:
            continue
        all_refs.add(ref)
        if ref in result:
            continue  # ya tenemos el primero

        result[ref] = {
            'cve_impo':  clean(cve_impo, 20),
            'fec_entr':  fb_date(fec_entr),
            'fecha_pago': fb_date(fec_pago),
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


def fetch_contenedores(cur):
    """Devuelve dict {num_refe: [(num_cont, tipo), ...]}."""
    cur.execute("""
        SELECT NUM_REFE, NUM_CONT, CVE_CONT
        FROM SAAIO_CONTEN
        WHERE NUM_REFE IS NOT NULL AND NUM_CONT IS NOT NULL
    """)
    result = {}
    for num_refe, num_cont, cve_cont in cur.fetchall():
        ref   = clean(num_refe, 50)
        cont  = clean(num_cont, 20)
        tipo  = CVE_CONT_TIPO.get(cve_cont, '')
        if ref and cont:
            result.setdefault(ref, []).append((cont, tipo))
    return result


def fetch_pedime2(cur):
    """Devuelve dict {num_refe: pag_lcap} desde SAAIO_PEDIME2 (línea de captura SAT)."""
    cur.execute("""
        SELECT NUM_REFE, PAG_LCAP
        FROM SAAIO_PEDIME2
        WHERE PAG_LCAP IS NOT NULL AND PAG_LCAP <> ''
    """)
    return {clean(r[0], 50): clean(r[1], 30) for r in cur.fetchall()}


def fetch_guias(cur):
    """Devuelve dict {num_refe: [(guia, tipo_guia), ...]}."""
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
            result.setdefault(ref, []).append((bl, tipo))
    return result


def fetch_proces(cur):
    """Devuelve dict {num_refe: fecha_captura} desde SAAIO_PROCES.FEC_CAPT."""
    cur.execute("""
        SELECT NUM_REFE, FEC_CAPT
        FROM SAAIO_PROCES
        WHERE NUM_REFE IS NOT NULL AND FEC_CAPT IS NOT NULL
    """)
    return {clean(r[0], 50): fb_date(r[1]) for r in cur.fetchall() if r[0]}


def fetch_regval(cur):
    """Devuelve dict {num_refe: fecha_validacion} desde SAAIO_REGVAL.FEC_VAL."""
    cur.execute("""
        SELECT NUM_REFE, MIN(FEC_VAL)
        FROM SAAIO_REGVAL
        WHERE NUM_REFE IS NOT NULL AND FEC_VAL IS NOT NULL
        GROUP BY NUM_REFE
    """)
    return {clean(r[0], 50): fb_date(r[1]) for r in cur.fetchall() if r[0]}


def fetch_partidas_count(cur):
    """Devuelve dict {num_refe: num_partidas} desde SAAIO_FRACCI."""
    cur.execute("""
        SELECT NUM_REFE, COUNT(*)
        FROM SAAIO_FRACCI
        WHERE NUM_REFE IS NOT NULL
        GROUP BY NUM_REFE
    """)
    return {clean(r[0], 50): int(r[1]) for r in cur.fetchall() if r[0]}


def fetch_dodas(cur, patente):
    """
    Extrae los DODA vigentes (no dados de baja) de la CVE_CAAT de Transportes
    Kasu, con las referencias ligadas (SAAIO_DODADO) y la terminal resuelta
    vía SAAIO_IDEPED (CVE_IDEN='CR', COM_IDEN = clave de terminal) +
    SAAIC_REFIS. Misma query que sync_agent.fetch_dodas — mantener en
    paridad.

    Retorna una lista de dicts listos para _upsert_dodas:
        {id_doda, num_doda, patente, cve_caat, cve_capt, terminal_cve,
         terminal_nombre, fecha_doda, fecha_baja, referencias: [{num_refe, cons_id}, ...]}
    """
    cur.execute("""
        SELECT
            d.ID_DODA, d.NUM_DODA, d.CVE_CAAT, d.CVE_CAPT,
            d.FEC_DODAE, d.FEC_BAJA,
            dd.NUM_REFE, dd.CONS_ID,
            rf.CVE_REFI, rf.NOM_REFI
        FROM SAAIO_DODA d
        JOIN SAAIO_DODADO dd ON dd.ID_DODA = d.ID_DODA
        LEFT JOIN SAAIO_IDEPED ip
            ON ip.NUM_REFE = dd.NUM_REFE AND ip.CVE_IDEN = 'CR'
        LEFT JOIN SAAIC_REFIS rf ON rf.CVE_REFI = ip.COM_IDEN
        WHERE d.CVE_CAAT = ? AND d.FEC_BAJA IS NULL
    """, (settings.CVE_CAAT_KASU,))

    dodas = {}
    for row in cur.fetchall():
        (id_doda, num_doda, cve_caat, cve_capt, fec_dodae, fec_baja,
         num_refe, cons_id, terminal_cve, terminal_nombre) = row
        if id_doda is None:
            continue
        entry = dodas.setdefault(id_doda, {
            'id_doda':         int(id_doda),
            'num_doda':        clean(num_doda, 34),
            'patente':         patente,
            'cve_caat':        clean(cve_caat, 6),
            'cve_capt':        clean(cve_capt, 20).upper(),
            'terminal_cve':    '',
            'terminal_nombre': '',
            'fecha_doda':      fb_datetime_str(fec_dodae),
            'fecha_baja':      fb_datetime_str(fec_baja),
            'referencias':     [],
        })
        if not entry['terminal_cve'] and terminal_cve:
            entry['terminal_cve']    = clean(terminal_cve, 4)
            entry['terminal_nombre'] = clean(terminal_nombre, 70)
        ref = clean(num_refe, 15)
        if ref and cons_id is not None:
            entry['referencias'].append({'num_refe': ref, 'cons_id': int(cons_id)})
    return list(dodas.values())


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

def import_referencias(patente, clientes, capturistas, pedime2, embar,
                        pedimentos, all_refs, partidas_count, proces, regval, dry_run, stdout):
    prefijo = PATENTE_PREFIJO.get(patente, patente)
    created = updated = 0

    refs_batch = []
    for ref in all_refs:
        ped = pedimentos.get(ref, {})
        cve = ped.get('cve_impo', '')
        nombre = clientes.get(cve, '')
        embar_ref = embar.get(ref, {})
        fecha_arribo = embar_ref.get('fecha_arribo') or ped.get('fec_entr')

        es_rect = ref.startswith('R') and len(ref) > 5
        cve_capt = ped.get('cve_capturista', '')
        nombre_capt = capturistas.get(cve_capt, '')

        refs_batch.append(dict(
            num_refe=ref,
            patente=patente,
            prefijo=prefijo,
            cve_cliente=cve,
            nombre_cliente=nombre,
            fecha_arribo=fecha_arribo,
            peso_bruto=embar_ref.get('peso_bruto'),
            fecha_validacion=regval.get(ref),
            fecha_pago=ped.get('fecha_pago'),
            num_pedimento=ped.get('num_pedimento', ''),
            clave_pedimento=ped.get('clave_pedimento', ''),
            tipo_pedimento=ped.get('tipo_pedimento', ''),
            aduana=ped.get('aduana', ''),
            regimen=ped.get('regimen', ''),
            num_operacion=ped.get('num_operacion', ''),
            linea_captura=pedime2.get(ref, ''),
            cve_capturista=cve_capt,
            nombre_capturista=nombre_capt,
            fir_elec=ped.get('fir_elec', ''),
            es_rectificacion=es_rect,
            num_partidas=partidas_count.get(ref, 0),
            fecha_captura=proces.get(ref),
        ))

    if not dry_run:
        with transaction.atomic():
            for data in refs_batch:
                _, was_created = Referencia.objects.update_or_create(
                    num_refe=data.pop('num_refe'),
                    defaults=data,
                )
                if was_created:
                    created += 1
                else:
                    updated += 1
    else:
        created = len(refs_batch)

    stdout.write(f'  {patente}: {created} creadas, {updated} actualizadas')
    return created


def import_contenedores(patente, contenedores_map, dry_run, stdout):
    created = skipped = 0
    batch = []
    refs_map = {
        r.num_refe: r
        for r in Referencia.objects.filter(patente=patente)
    } if not dry_run else {}

    for ref, conts in contenedores_map.items():
        ref_obj = refs_map.get(ref)
        if not ref_obj and not dry_run:
            skipped += 1
            continue
        for num_cont, tipo in conts:
            if not dry_run:
                _, was_created = Contenedor.objects.get_or_create(
                    referencia=ref_obj,
                    num_cont=num_cont,
                    defaults={'tipo': tipo},
                )
                if was_created:
                    created += 1
            else:
                created += 1

    stdout.write(f'  {patente}: {created} contenedores, {skipped} refs no encontradas')


def import_guias(patente, guias_map, dry_run, stdout):
    created = skipped = 0
    refs_map = {
        r.num_refe: r
        for r in Referencia.objects.filter(patente=patente)
    } if not dry_run else {}

    for ref, bls in guias_map.items():
        ref_obj = refs_map.get(ref)
        if not ref_obj and not dry_run:
            skipped += 1
            continue
        for numero_guia, tipo_guia in bls:
            if not dry_run:
                _, was_created = GuiaBL.objects.get_or_create(
                    referencia=ref_obj,
                    numero_guia=numero_guia,
                    defaults={'tipo_guia': tipo_guia},
                )
                if was_created:
                    created += 1
            else:
                created += 1

    stdout.write(f'  {patente}: {created} guías BL, {skipped} refs no encontradas')


def import_dodas(patente, dodas_list, dry_run, stdout):
    """Upsert de DODAs + DodaReferencia, reutilizando referencias.sync_views._upsert_dodas."""
    if dry_run:
        stdout.write(f'  {patente}: {len(dodas_list)} DODAs (dry-run, no se escriben)')
        return

    stats = {'creadas': 0, 'actualizadas': 0, 'errores': 0}
    error_msgs = []
    creadas = _upsert_dodas(dodas_list, stats, error_msgs)
    actualizadas = len(dodas_list) - len(creadas) - stats['errores']
    stdout.write(
        f'  {patente}: {len(creadas)} DODAs nuevos, {actualizadas} actualizados, '
        f'{stats["errores"]} errores'
    )
    for msg in error_msgs:
        stdout.write(f'    ! {msg}')


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = 'Importa referencias, pedimentos, contenedores y BLs desde CASA.GDB Firebird'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', default=False)
        parser.add_argument('--patentes', nargs='+', default=PATENTES, choices=PATENTES)
        parser.add_argument('--solo-referencias', action='store_true', default=False)
        parser.add_argument('--solo-contenedores', action='store_true', default=False)
        parser.add_argument('--solo-bls', action='store_true', default=False)

    def handle(self, *args, **options):
        dry_run  = options['dry_run']
        patentes = options['patentes']
        solo_ref  = options['solo_referencias']
        solo_cont = options['solo_contenedores']
        solo_bl   = options['solo_bls']
        import_all = not (solo_ref or solo_cont or solo_bl)

        if dry_run:
            self.stdout.write(self.style.WARNING('MODO DRY-RUN — no se escribe en BD'))

        # ── Paso 1: extracción ────────────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('\n[1/5] Conectando a Firebird...'))
        data = {}
        for patente in patentes:
            self.stdout.write(f'  Patente {patente}...')
            try:
                con = connect(patente)
            except Exception as e:
                self.stderr.write(self.style.ERROR(f'  ✘ {patente}: {e}'))
                continue

            cur = con.cursor()
            try:
                clientes    = fetch_clientes(cur)
                capturistas = fetch_capturistas(cur) if (import_all or solo_ref) else {}
                pedime2     = fetch_pedime2(cur)     if (import_all or solo_ref) else {}
                embar       = fetch_embar(cur)       if (import_all or solo_ref) else {}
                peds, all_refs = fetch_pedimentos(cur) if (import_all or solo_ref) else ({}, set())
                partidas    = fetch_partidas_count(cur) if (import_all or solo_ref) else {}
                proces      = fetch_proces(cur)         if (import_all or solo_ref) else {}
                regval      = fetch_regval(cur)         if (import_all or solo_ref) else {}
                dodas       = fetch_dodas(cur, patente) if (import_all or solo_ref) else []
                conts       = fetch_contenedores(cur) if (import_all or solo_cont) else {}
                guias       = fetch_guias(cur)       if (import_all or solo_bl) else {}
                data[patente] = dict(
                    clientes=clientes, capturistas=capturistas,
                    pedime2=pedime2, embar=embar,
                    peds=peds, all_refs=all_refs,
                    partidas=partidas, proces=proces, regval=regval,
                    dodas=dodas,
                    conts=conts, guias=guias,
                )
                self.stdout.write(
                    f'    {len(all_refs)} referencias | '
                    f'{sum(partidas.values())} partidas | '
                    f'{sum(len(v) for v in conts.values())} contenedores | '
                    f'{sum(len(v) for v in guias.values())} guías BL | '
                    f'{len(dodas)} DODAs'
                )
            finally:
                con.close()

        # ── Paso 2: referencias ───────────────────────────────────────────────
        if import_all or solo_ref:
            self.stdout.write(self.style.MIGRATE_HEADING('\n[2/5] Importando referencias...'))
            for patente, d in data.items():
                import_referencias(
                    patente, d['clientes'], d['capturistas'],
                    d['pedime2'], d['embar'], d['peds'], d['all_refs'],
                    d['partidas'], d['proces'], d['regval'],
                    dry_run, self.stdout,
                )

        # ── Paso 3: DODAs ──────────────────────────────────────────────────────
        # Después de Paso 2: DodaReferencia liga contra las Referencia locales
        # recién creadas/actualizadas.
        if import_all or solo_ref:
            self.stdout.write(self.style.MIGRATE_HEADING('\n[3/5] Importando DODAs...'))
            for patente, d in data.items():
                import_dodas(patente, d['dodas'], dry_run, self.stdout)

        # ── Paso 4: contenedores ──────────────────────────────────────────────
        if import_all or solo_cont:
            self.stdout.write(self.style.MIGRATE_HEADING('\n[4/5] Importando contenedores...'))
            for patente, d in data.items():
                import_contenedores(patente, d['conts'], dry_run, self.stdout)

        # ── Paso 5: guías BL ──────────────────────────────────────────────────
        if import_all or solo_bl:
            self.stdout.write(self.style.MIGRATE_HEADING('\n[5/5] Importando guías BL...'))
            for patente, d in data.items():
                import_guias(patente, d['guias'], dry_run, self.stdout)

        self.stdout.write(self.style.SUCCESS('\n✔ Importación completada.'))
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN: ningún dato fue guardado.'))
