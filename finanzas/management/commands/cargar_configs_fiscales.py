import os
from django.core.management.base import BaseCommand, CommandError
from finanzas.models import ConfiguracionFiscal

PATENTES = ['1627', '1656', '1927']


class Command(BaseCommand):
    help = 'Carga o actualiza ConfiguracionFiscal para las 3 patentes desde variables de entorno'

    def handle(self, *args, **options):
        errores = []
        for p in PATENTES:
            campos_requeridos = ['RFC', 'NOMBRE', 'REGIMEN', 'CP', 'CERT_PATH', 'KEY_PATH']
            faltantes = [f for f in campos_requeridos if not os.environ.get(f'CFDI_{p}_{f}')]
            if faltantes:
                errores.append(f'Patente {p}: faltan variables CFDI_{p}_{{{", ".join(faltantes)}}}')
                continue

            obj, created = ConfiguracionFiscal.objects.update_or_create(
                patente=p,
                defaults={
                    'rfc':            os.environ[f'CFDI_{p}_RFC'],
                    'razon_social':   os.environ[f'CFDI_{p}_NOMBRE'],
                    'regimen_fiscal': os.environ[f'CFDI_{p}_REGIMEN'],
                    'codigo_postal':  os.environ[f'CFDI_{p}_CP'],
                    'cert_path':      os.environ[f'CFDI_{p}_CERT_PATH'],
                    'key_path':       os.environ[f'CFDI_{p}_KEY_PATH'],
                    'activa':         True,
                },
            )
            accion = 'Creada' if created else 'Actualizada'
            self.stdout.write(self.style.SUCCESS(f'{accion}: patente {p} — {obj.rfc}'))

        if errores:
            for e in errores:
                self.stderr.write(self.style.ERROR(e))
            raise CommandError('Faltan variables de entorno. Revisar .env y reejecutar.')
