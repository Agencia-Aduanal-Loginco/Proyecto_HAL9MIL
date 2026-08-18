"""
Management command: reintentar_modulacion

Reintenta los envíos de modulación (email a capturista y/o push a
BitacoraKasu) que hayan quedado en estado ERROR. Reusa la misma lógica de
envío que el flujo original (referencias/modulacion.py), reintentando sólo
la(s) parte(s) fallida(s) de cada EnvioModulacion — no crea envíos nuevos ni
reenvía lo que ya haya quedado ENVIADO.

Uso:
    python manage.py reintentar_modulacion
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from referencias.modulacion import reintentar_envio
from referencias.models import EnvioModulacion


class Command(BaseCommand):
    help = (
        'Reintenta los envíos de modulación (email y/o push a BitacoraKasu) '
        'que hayan quedado en estado ERROR.'
    )

    def handle(self, *args, **options):
        envios = EnvioModulacion.objects.filter(
            Q(email_estado='ERROR') | Q(push_estado='ERROR')
        ).select_related('doda')

        total = 0
        exitosos = 0
        con_error = 0

        for envio in envios:
            total += 1
            if reintentar_envio(envio):
                exitosos += 1
            else:
                con_error += 1

        self.stdout.write(
            f'{total} reintentados, {exitosos} con éxito, {con_error} siguen en error'
        )
