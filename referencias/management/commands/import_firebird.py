"""
Management command: import_firebird

Extrae referencias, pedimentos, contenedores y guías BL de las tres bases
de datos Firebird CASA.GDB (patentes 1627, 1656, 1927) e importa todo al
modelo Django de HAL9MIL.

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
from django.core.management.base import BaseCommand
from django.db import transaction

from referencias.models import (
    CVE_CONT_TIPO, PATENTE_PREFIJO,
    Contenedor, GuiaBL, Referencia,
)

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
    """Devuelve dict {num_refe: fecha_arribo} desde CTRAO_EMBAR."""
    cur.execute("""
        SELECT NUM_REFE, FEC_ENTR
        FROM CTRAO_EMBAR
        WHERE NUM_REFE IS NOT NULL
    """)
    return {clean(r[0]): fb_date(r[1]) for r in cur.fetchall()}


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
            'cve_impo':         clean(cve_impo, 20),
            'fecha_validacion':  fb_date(fec_entr),
            'fecha_pago':        fb_date(fec_pago),
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


def fetch_partidas_count(cur):
    """Devuelve dict {num_refe: num_partidas} desde SAAIO_FRACCI."""
    cur.execute("""
        SELECT NUM_REFE, COUNT(*)
        FROM SAAIO_FRACCI
        WHERE NUM_REFE IS NOT NULL
        GROUP BY NUM_REFE
    """)
    return {clean(r[0], 50): int(r[1]) for r in cur.fetchall() if r[0]}


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

def import_referencias(patente, clientes, capturistas, pedime2, embar,
                        pedimentos, all_refs, partidas_count, proces, dry_run, stdout):
    prefijo = PATENTE_PREFIJO.get(patente, patente)
    created = updated = 0

    refs_batch = []
    for ref in all_refs:
        ped = pedimentos.get(ref, {})
        cve = ped.get('cve_impo', '')
        nombre = clientes.get(cve, '')
        fecha_arribo = embar.get(ref)
        # Si no está en CTRAO_EMBAR, usamos fecha_validacion como proxy
        if not fecha_arribo:
            fecha_arribo = ped.get('fecha_validacion')

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
            fecha_validacion=ped.get('fecha_validacion'),
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
        self.stdout.write(self.style.MIGRATE_HEADING('\n[1/4] Conectando a Firebird...'))
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
                conts       = fetch_contenedores(cur) if (import_all or solo_cont) else {}
                guias       = fetch_guias(cur)       if (import_all or solo_bl) else {}
                data[patente] = dict(
                    clientes=clientes, capturistas=capturistas,
                    pedime2=pedime2, embar=embar,
                    peds=peds, all_refs=all_refs,
                    partidas=partidas, proces=proces,
                    conts=conts, guias=guias,
                )
                self.stdout.write(
                    f'    {len(all_refs)} referencias | '
                    f'{sum(partidas.values())} partidas | '
                    f'{sum(len(v) for v in conts.values())} contenedores | '
                    f'{sum(len(v) for v in guias.values())} guías BL'
                )
            finally:
                con.close()

        # ── Paso 2: referencias ───────────────────────────────────────────────
        if import_all or solo_ref:
            self.stdout.write(self.style.MIGRATE_HEADING('\n[2/4] Importando referencias...'))
            for patente, d in data.items():
                import_referencias(
                    patente, d['clientes'], d['capturistas'],
                    d['pedime2'], d['embar'], d['peds'], d['all_refs'],
                    d['partidas'], d['proces'],
                    dry_run, self.stdout,
                )

        # ── Paso 3: contenedores ──────────────────────────────────────────────
        if import_all or solo_cont:
            self.stdout.write(self.style.MIGRATE_HEADING('\n[3/4] Importando contenedores...'))
            for patente, d in data.items():
                import_contenedores(patente, d['conts'], dry_run, self.stdout)

        # ── Paso 4: guías BL ──────────────────────────────────────────────────
        if import_all or solo_bl:
            self.stdout.write(self.style.MIGRATE_HEADING('\n[4/4] Importando guías BL...'))
            for patente, d in data.items():
                import_guias(patente, d['guias'], dry_run, self.stdout)

        self.stdout.write(self.style.SUCCESS('\n✔ Importación completada.'))
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN: ningún dato fue guardado.'))
