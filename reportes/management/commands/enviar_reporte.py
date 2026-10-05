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
        parser.add_argument(
            '--force',
            action='store_true',
            help=(
                'Reenvía el reporte aunque el período ya se haya enviado. '
                'Solo correo: no repite los mensajes de WhatsApp. Reemplaza el '
                'registro de historial del período. Solo para mensual.'
            ),
        )
        parser.add_argument(
            '--solo-a',
            dest='solo_a',
            metavar='CORREO',
            action='append',
            help=(
                'Envía únicamente a estos correos, sin registrar historial ni '
                'mandar WhatsApp. Repetible. Solo para mensual.'
            ),
        )

    def handle(self, *args, **options):
        tipo = options['tipo']
        force = options['force']
        solo_a = options['solo_a']

        if tipo == 'semanal' and (force or solo_a):
            raise CommandError('--force y --solo-a solo están soportados para el reporte mensual.')

        self.stdout.write(f'Enviando reporte {tipo}...')
        try:
            if tipo == 'semanal':
                enviar_reporte_semanal()
            else:
                enviar_reporte_mensual(force=force, solo_a=solo_a)
            self.stdout.write(self.style.SUCCESS(f'✔ Reporte {tipo} enviado correctamente.'))
        except Exception as e:
            raise CommandError(f'Error al enviar reporte {tipo}: {e}')
