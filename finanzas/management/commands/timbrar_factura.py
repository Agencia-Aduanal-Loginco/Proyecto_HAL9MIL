"""
python manage.py timbrar_factura --factura-id <pk>

Timbra una Factura en estado BORRADOR usando el PAC configurado.
Útil para pruebas en sandbox sin abrir el navegador.
"""

import uuid as uuid_lib

from django.core.management.base import BaseCommand, CommandError

from finanzas.cfdi_generator import generar_xml_cfdi40
from finanzas.models import Factura
from finanzas.pac_client import PACConfigError, PACError, timbrar_cfdi


class Command(BaseCommand):
    help = 'Timbra una Factura BORRADOR ante el PAC configurado.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--factura-id', type=int, required=True,
            help='PK de la Factura a timbrar.'
        )

    def handle(self, *args, **options):
        pk = options['factura_id']
        try:
            factura = Factura.objects.select_related(
                'configuracion_fiscal'
            ).prefetch_related('conceptos').get(pk=pk)
        except Factura.DoesNotExist:
            raise CommandError(f'No existe Factura con id={pk}.')

        if factura.estado != 'BORRADOR':
            raise CommandError(
                f'La factura {factura} está en estado "{factura.estado}", '
                f'solo se pueden timbrar facturas BORRADOR.'
            )

        self.stdout.write(f'Generando XML para {factura} ...')
        try:
            xml = generar_xml_cfdi40(factura)
        except FileNotFoundError as e:
            raise CommandError(f'Archivo CSD no encontrado: {e}')
        except ValueError as e:
            raise CommandError(f'Error en configuración CSD/env: {e}')
        except Exception as e:
            raise CommandError(f'Error generando XML: {e}')

        self.stdout.write('Enviando al PAC ...')
        try:
            resultado = timbrar_cfdi(xml)
        except PACConfigError as e:
            raise CommandError(f'Configuración PAC incompleta: {e}')
        except PACError as e:
            raise CommandError(f'PAC rechazó el timbrado [{e.code}]: {e}')
        except Exception as e:
            raise CommandError(f'Error de red/PAC: {e}')

        factura.uuid_fiscal = uuid_lib.UUID(resultado['uuid'])
        factura.xml_timbrado = resultado['xml_timbrado']
        factura.estado = 'TIMBRADA'
        factura.save(update_fields=['uuid_fiscal', 'xml_timbrado', 'estado'])

        self.stdout.write(
            self.style.SUCCESS(
                f'Factura {factura.serie}{factura.folio} timbrada. '
                f'UUID: {factura.uuid_fiscal}'
            )
        )
