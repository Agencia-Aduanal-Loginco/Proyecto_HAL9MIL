from django.core.management.base import BaseCommand

from glosa.services_procesamiento import reprocesar_atorados


class Command(BaseCommand):
    help = 'Reprocesa documentos de glosa IA que quedaron en PENDIENTE/PROCESANDO (p. ej. tras un reinicio).'

    def add_arguments(self, parser):
        parser.add_argument('--minutos', type=int, default=None,
                            help='Antigüedad mínima en minutos (default GLOSA_MINUTOS_ATORADO).')

    def handle(self, *args, **opts):
        n = reprocesar_atorados(minutos=opts['minutos'])
        self.stdout.write(self.style.SUCCESS(f'{n} documento(s) reprocesado(s).'))
