"""
Management command: import_clientes_casa

Extrae el catálogo de clientes/importadores (CTRAC_CLIENT) de las tres bases
Firebird CASA.GDB (patentes 1627, 1656, 1927) y lo importa/actualiza en
clientes.Cliente (nombre, cve_cliente, RFC). No toca los campos de email
(cobranza / cuenta de gastos), que se capturan manualmente en el sistema.

Un mismo cliente puede existir en más de una patente con la misma CVE_IMP y
RFC; se dedupea por nombre y se usa el primer registro encontrado.

Uso:
    python manage.py import_clientes_casa
    python manage.py import_clientes_casa --dry-run
    python manage.py import_clientes_casa --patentes 1627 1656
"""

import fdb
from django.core.management.base import BaseCommand

from clientes.models import Cliente

FB_HOST     = 'localhost'
FB_PORT     = 3050
FB_USER     = 'SYSDBA'
FB_PASSWORD = 'masterkey'
FB_CHARSET  = 'WIN1252'
DB_TEMPLATE = '/databases/{patente}/CASA.GDB'

PATENTES = ['1627', '1656', '1927']


def connect(patente):
    return fdb.connect(
        host=FB_HOST, port=FB_PORT,
        database=DB_TEMPLATE.format(patente=patente),
        user=FB_USER, password=FB_PASSWORD,
        charset=FB_CHARSET,
    )


def clean(val, max_len=None):
    if val is None:
        return ''
    s = str(val).strip()
    return s[:max_len] if max_len and len(s) > max_len else s


def fetch_clientes_rfc(cur):
    """Devuelve lista de (cve_imp, nombre, rfc) desde CTRAC_CLIENT."""
    cur.execute("""
        SELECT CVE_IMP, NOM_IMP, RFC_IMP
        FROM CTRAC_CLIENT
        WHERE NOM_IMP IS NOT NULL
    """)
    return [
        (clean(r[0], 20), clean(r[1], 255), clean(r[2], 13))
        for r in cur.fetchall()
    ]


def mapear_clientes(filas_por_patente):
    """Dedupea por nombre_cliente entre patentes (primera coincidencia gana).

    filas_por_patente: dict {patente: [(cve, nombre, rfc), ...]}
    Retorna dict {nombre: {'cve_cliente': cve, 'rfc': rfc}}.
    """
    mapa = {}
    for patente in sorted(filas_por_patente):
        for cve, nombre, rfc in filas_por_patente[patente]:
            if not nombre or nombre in mapa:
                continue
            mapa[nombre] = {'cve_cliente': cve, 'rfc': rfc}
    return mapa


def importar_clientes(mapa, dry_run, stdout):
    """Upsert de clientes por nombre_cliente. Retorna (creados, actualizados)."""
    creados = actualizados = 0
    for nombre, datos in mapa.items():
        if dry_run:
            creados += 1
            continue
        _, fue_creado = Cliente.objects.update_or_create(
            nombre_cliente=nombre, defaults=datos,
        )
        if fue_creado:
            creados += 1
        else:
            actualizados += 1
    stdout.write(f'  {creados} creados, {actualizados} actualizados')
    return creados, actualizados


class Command(BaseCommand):
    help = 'Importa catálogo de clientes (nombre, cve_cliente, RFC) desde CASA.GDB'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', default=False)
        parser.add_argument('--patentes', nargs='+', default=PATENTES, choices=PATENTES)

    def handle(self, *args, **options):
        dry_run  = options['dry_run']
        patentes = options['patentes']

        if dry_run:
            self.stdout.write(self.style.WARNING('MODO DRY-RUN — no se escribe en BD'))

        self.stdout.write(self.style.MIGRATE_HEADING('\n[1/2] Conectando a Firebird...'))
        filas_por_patente = {}
        for patente in patentes:
            self.stdout.write(f'  Patente {patente}...')
            try:
                con = connect(patente)
            except Exception as e:
                self.stderr.write(self.style.ERROR(f'  ✘ {patente}: {e}'))
                continue
            try:
                filas_por_patente[patente] = fetch_clientes_rfc(con.cursor())
            finally:
                con.close()

        mapa = mapear_clientes(filas_por_patente)
        self.stdout.write(f'  {len(mapa)} clientes únicos encontrados')

        self.stdout.write(self.style.MIGRATE_HEADING('\n[2/2] Importando clientes...'))
        importar_clientes(mapa, dry_run, self.stdout)

        self.stdout.write(self.style.SUCCESS('\n✔ Importación completada.'))
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN: ningún dato fue guardado.'))
