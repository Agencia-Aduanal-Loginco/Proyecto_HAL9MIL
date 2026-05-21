from django.core.management.base import BaseCommand, CommandError
from reportes.jobs import enviar_reporte_semanal, enviar_reporte_mensual


class Command(BaseCommand):
    help = 'Dispara un reporte manualmente para pruebas'

    def add_arguments(self, parser):
        parser.add_argument(
            'tipo',
            choices=['semanal', 'mensual'],
            help='Tipo de reporte a enviar',
        )

    def handle(self, *args, **options):
        tipo = options['tipo']
        self.stdout.write(f'Enviando reporte {tipo}...')
        try:
            if tipo == 'semanal':
                enviar_reporte_semanal()
            else:
                enviar_reporte_mensual()
            self.stdout.write(self.style.SUCCESS(f'✔ Reporte {tipo} enviado correctamente.'))
        except Exception as e:
            raise CommandError(f'Error al enviar reporte {tipo}: {e}')
