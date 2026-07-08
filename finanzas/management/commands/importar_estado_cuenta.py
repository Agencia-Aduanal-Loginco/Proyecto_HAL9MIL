"""
python manage.py importar_estado_cuenta --cuenta-id <id> --archivo <ruta.csv>

CSV esperado (con encabezado):
    fecha, descripcion, referencia, cargo, abono, saldo

- fecha       : YYYY-MM-DD o DD/MM/YYYY
- cargo/abono : decimales, puede ser vacío o '0'
- saldo       : decimal, puede ser vacío
- referencia  : número de operación bancaria (opcional)

Los duplicados se detectan por (cuenta, fecha, referencia_banco, cargo, abono).
"""
import csv
import decimal
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from finanzas.models import CuentaBancaria, MovimientoBancario


def _parse_fecha(valor: str):
    valor = valor.strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(valor, fmt).date()
        except ValueError:
            continue
    raise ValueError(f'Fecha no reconocida: {valor!r}')


def _parse_decimal(valor: str) -> decimal.Decimal:
    valor = valor.strip().replace(',', '')
    if not valor:
        return decimal.Decimal('0')
    return decimal.Decimal(valor)


class Command(BaseCommand):
    help = 'Importa movimientos bancarios desde un CSV al estado de cuenta de una cuenta bancaria'

    def add_arguments(self, parser):
        parser.add_argument('--cuenta-id', type=int, required=True,
                            help='ID de la CuentaBancaria destino')
        parser.add_argument('--archivo', type=str, required=True,
                            help='Ruta al archivo CSV')
        parser.add_argument('--encoding', default='utf-8-sig',
                            help='Codificación del CSV (default: utf-8-sig)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Simula la importación sin guardar')

    def handle(self, *args, **options):
        cuenta_id = options['cuenta_id']
        ruta      = Path(options['archivo'])
        encoding  = options['encoding']
        dry_run   = options['dry_run']

        try:
            cuenta = CuentaBancaria.objects.get(pk=cuenta_id)
        except CuentaBancaria.DoesNotExist:
            raise CommandError(f'CuentaBancaria #{cuenta_id} no existe.')

        if not ruta.exists():
            raise CommandError(f'Archivo no encontrado: {ruta}')

        self.stdout.write(f'Importando {ruta.name} → {cuenta}')
        if dry_run:
            self.stdout.write(self.style.WARNING('  [DRY-RUN] No se guardarán cambios.'))

        creados = 0
        duplicados = 0
        errores = 0

        with open(ruta, encoding=encoding, newline='') as f:
            reader = csv.DictReader(f)
            # Normalizar encabezados (strip + lower)
            reader.fieldnames = [h.strip().lower() for h in (reader.fieldnames or [])]

            filas = list(reader)

        with transaction.atomic():
            for i, fila in enumerate(filas, start=2):
                try:
                    fecha       = _parse_fecha(fila.get('fecha', ''))
                    descripcion = fila.get('descripcion', '').strip()[:300]
                    referencia  = fila.get('referencia', '').strip()[:100]
                    cargo       = _parse_decimal(fila.get('cargo', ''))
                    abono       = _parse_decimal(fila.get('abono', ''))
                    saldo_raw   = fila.get('saldo', '').strip()
                    saldo       = _parse_decimal(saldo_raw) if saldo_raw else None
                except (ValueError, decimal.InvalidOperation) as e:
                    self.stderr.write(f'  Fila {i}: error de formato — {e}')
                    errores += 1
                    continue

                # Detección de duplicados
                existe = MovimientoBancario.objects.filter(
                    cuenta=cuenta,
                    fecha=fecha,
                    referencia_banco=referencia,
                    cargo=cargo,
                    abono=abono,
                ).exists()

                if existe:
                    duplicados += 1
                    continue

                if not dry_run:
                    MovimientoBancario.objects.create(
                        cuenta=cuenta,
                        fecha=fecha,
                        descripcion=descripcion,
                        referencia_banco=referencia,
                        cargo=cargo,
                        abono=abono,
                        saldo=saldo,
                    )
                creados += 1

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS(
            f'  Creados: {creados} | Duplicados omitidos: {duplicados} | Errores: {errores}'
        ))
