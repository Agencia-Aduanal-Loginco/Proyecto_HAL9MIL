"""
python manage.py calcular_comisiones [--mes M] [--anio A]

Si no se especifican, calcula para el mes anterior al día de hoy.
"""
from datetime import date

from django.core.management.base import BaseCommand

from finanzas.comisiones import calcular_comisiones_mes


class Command(BaseCommand):
    help = 'Calcula o recalcula comisiones por referencia para el periodo indicado'

    def add_arguments(self, parser):
        hoy = date.today()
        mes_anterior = hoy.month - 1 if hoy.month > 1 else 12
        anio_anterior = hoy.year if hoy.month > 1 else hoy.year - 1
        parser.add_argument('--mes',  type=int, default=mes_anterior)
        parser.add_argument('--anio', type=int, default=anio_anterior)

    def handle(self, *args, **options):
        mes  = options['mes']
        anio = options['anio']
        self.stdout.write(f'Calculando comisiones para {mes:02d}/{anio}...')
        comisiones = calcular_comisiones_mes(mes, anio)
        total = sum(c.monto_comision for c in comisiones)
        self.stdout.write(self.style.SUCCESS(
            f'  {len(comisiones)} referencias procesadas. '
            f'Total comisiones: ${total:,.2f}'
        ))
