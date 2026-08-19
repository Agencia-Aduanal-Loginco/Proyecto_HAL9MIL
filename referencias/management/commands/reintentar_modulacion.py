"""
Management command: reintentar_modulacion

Reintenta los envíos de modulación (email a capturista y/o push a
BitacoraKasu) que hayan quedado en estado ERROR o PENDIENTE (nunca
intentados — el proceso murió entre el EnvioModulacion.objects.create() y el
.save() que registra el resultado). Reusa la misma lógica de envío que el
flujo original (referencias/modulacion.py), reintentando sólo la(s) parte(s)
que falte(n) de cada EnvioModulacion — no reenvía lo que ya haya quedado
ENVIADO.

Además barre las DODAs que no tienen ni un solo EnvioModulacion (porque
transaction.on_commit nunca corrió, o porque
EnvioModulacion.objects.create() lanzó una excepción antes de crear la
fila) — esas quedan con notificado_en=NULL y son invisibles para el filtro
de arriba. Para esas se crea el EnvioModulacion que falta y se procesa
igual que una DODA recién sincronizada.

También filtra por Doda.fecha_doda para no mandar de golpe todo el historial
acumulado a BitacoraKasu: un envío masivo con fechas viejas satura la vista
mensual de BitacoraKasu y (antes del fix del lado de BitacoraKasu) agrupaba
los folios bajo el día del reintento en vez del día real del DODA. Por
default sólo procesa DODAs de 2026; usar --anio para otro año o --todos para
quitar el filtro por completo.

Uso:
    python manage.py reintentar_modulacion
    python manage.py reintentar_modulacion --solo-push
    python manage.py reintentar_modulacion --anio 2025
    python manage.py reintentar_modulacion --todos
"""
import logging

from django.core.management.base import BaseCommand
from django.db.models import Q

from referencias.modulacion import _procesar_doda, reintentar_envio
from referencias.models import Doda, EnvioModulacion

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Reintenta los envíos de modulación (email y/o push a BitacoraKasu) '
        'que hayan quedado en estado ERROR o PENDIENTE, y barre las DODAs '
        'que no llegaron a tener ni un EnvioModulacion.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--solo-push', action='store_true', default=False,
            help=(
                'Reintenta únicamente el push a BitacoraKasu, sin tocar '
                'email_estado. Los envíos cuyo único pendiente es el email '
                'se omiten por completo (no cuentan ni como éxito ni como '
                'error) y quedan disponibles para un reintento posterior '
                'sin esta bandera. Útil cuando se corrigió la causa raíz '
                'del push (p.ej. tipo_contenedor) pero no se quiere '
                'disparar de golpe una tanda grande de correos atrasados '
                'por otra causa (p.ej. faltaba PerfilUsuario).'
            ),
        )
        parser.add_argument(
            '--anio', type=int, default=2026,
            help=(
                'Sólo procesa DODAs cuyo fecha_doda sea de este año '
                '(default: 2026). Evita saturar la vista mensual de '
                'BitacoraKasu con un envío masivo de historial acumulado.'
            ),
        )
        parser.add_argument(
            '--todos', action='store_true', default=False,
            help='Ignora el filtro por año y procesa todas las DODAs pendientes.',
        )

    def handle(self, *args, **options):
        solo_push = options['solo_push']
        anio = options['anio']
        todos = options['todos']

        envios = EnvioModulacion.objects.filter(
            Q(email_estado__in=['ERROR', 'PENDIENTE'])
            | Q(push_estado__in=['ERROR', 'PENDIENTE'])
        ).select_related('doda')
        if solo_push:
            # Sin esto, un envío cuyo único pendiente es el email entraría
            # al loop y reintentar_envio(solo_push=True) no tendría nada que
            # hacer con él (ni error ni éxito real) — se filtra de una vez.
            envios = envios.filter(push_estado__in=['ERROR', 'PENDIENTE'])
        if not todos:
            envios = envios.filter(doda__fecha_doda__year=anio)

        total = 0
        exitosos = 0
        con_error = 0

        for envio in envios:
            total += 1
            try:
                if reintentar_envio(envio, solo_push=solo_push):
                    exitosos += 1
                else:
                    con_error += 1
            except Exception as e:
                logger.error('[Modulacion] Error inesperado reintentando envio %s: %s',
                             getattr(envio, 'id', '?'), e)
                con_error += 1

        # DODAs sin ningún EnvioModulacion: invisibles para la query de
        # arriba porque no tienen fila que filtrar. Se procesan igual que
        # una DODA recién creada por el sync (crea su EnvioModulacion +
        # corre email/push) — ver referencias/modulacion.py::_procesar_doda.
        dodas_sin_envio = Doda.objects.filter(
            fecha_baja__isnull=True,
            notificado_en__isnull=True,
            envios_modulacion__isnull=True,
        )
        if not todos:
            dodas_sin_envio = dodas_sin_envio.filter(fecha_doda__year=anio)

        for doda in dodas_sin_envio:
            total += 1
            try:
                _procesar_doda(doda, solo_push=solo_push)
                envio = doda.envios_modulacion.first()
                if solo_push:
                    ok = envio is not None and envio.push_estado != 'ERROR'
                else:
                    ok = (
                        envio is not None
                        and envio.email_estado != 'ERROR'
                        and envio.push_estado != 'ERROR'
                    )
                if ok:
                    exitosos += 1
                else:
                    con_error += 1
            except Exception as e:
                logger.error('[Modulacion] Error inesperado procesando DODA sin envío %s: %s',
                             getattr(doda, 'id_doda', '?'), e)
                con_error += 1

        extra = ' (--solo-push: email_estado pendiente no se tocó)' if solo_push else ''
        self.stdout.write(
            f'{total} reintentados, {exitosos} con éxito, {con_error} siguen en error{extra}'
        )
