"""
Comando para probar el envío de interpretaciones IA por WhatsApp.

Uso:
  # Ver destinatarios configurados por módulo
  python manage.py wa_ia_test --listar

  # Enviar texto de prueba a los destinatarios configurados de un módulo
  python manage.py wa_ia_test --modulo referencias
  python manage.py wa_ia_test --modulo glosa
  python manage.py wa_ia_test --modulo cuenta_gastos

  # Enviar a un número específico en lugar de los configurados en DB
  python manage.py wa_ia_test --modulo referencias --numero +5217535342088

  # Solo mostrar qué se enviaría sin mandar nada
  python manage.py wa_ia_test --modulo referencias --dry-run
"""
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

MODULOS = ['referencias', 'glosa', 'cuenta_gastos']

TEXTO_PRUEBA = {
    'referencias': (
        "**Reporte Ejecutivo – Prueba** Durante la semana se validaron 68 operaciones "
        "(LCLF 32, LCRR 28, LCMJ 8) y se pagaron/liberaron 100, procesándose 105 "
        "contenedores. Se observa una brecha relevante: las liberaciones (100) superan "
        "a las validaciones (68). Recomendación: reforzar el equipo de validación, "
        "priorizando LCMJ para reducir el acumulado pendiente."
    ),
    'glosa': (
        "**Glosa – Prueba** Esta semana ingresaron 4 pedimentos a glosa, de los cuales "
        "3 fueron concluidos en un tiempo promedio de 1.5 días. El capturista con más "
        "observaciones fue BRENDA (2 obs.), con errores frecuentes en clasificación "
        "arancelaria. Recomendación: revisar el procedimiento de verificación de "
        "fracciones antes de presentación."
    ),
    'cuenta_gastos': (
        "**Cuenta de Gastos – Prueba** De los 100 pedimentos pagados esta semana, "
        "87 ya tienen cuenta de gastos registrada (87% de cobertura). Tiempo promedio "
        "de pago a registro de CG: 1.2 días. MARCELA finalizó 42 cuentas, destacando "
        "como la más productiva del equipo."
    ),
}


class Command(BaseCommand):
    help = 'Prueba el envío de interpretaciones IA por WhatsApp (por módulo y destinatario)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--listar',
            action='store_true',
            help='Muestra todos los destinatarios y sus módulos IA activos',
        )
        parser.add_argument(
            '--modulo',
            choices=MODULOS,
            help='Módulo cuya interpretación IA se enviará',
        )
        parser.add_argument(
            '--numero',
            default='',
            help='Enviar a este número específico en lugar de los configurados en DB '
                 '(formato: +5217535342088)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra qué se enviaría sin mandar el mensaje',
        )

    def handle(self, *args, **options):
        if options['listar']:
            self._listar_destinatarios()
            return

        if not options['modulo']:
            raise CommandError(
                'Especifica --modulo (referencias | glosa | cuenta_gastos) '
                'o usa --listar para ver destinatarios.'
            )

        self._enviar_prueba(
            modulo=options['modulo'],
            numero_override=options['numero'],
            dry_run=options['dry_run'],
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _listar_destinatarios(self):
        from reportes.models import Destinatario

        todos = Destinatario.objects.filter(activo=True).order_by('nombre')
        if not todos.exists():
            self.stdout.write(self.style.WARNING('No hay destinatarios activos.'))
            return

        self.stdout.write(self.style.HTTP_INFO(
            f'\n{"Destinatario":<30} {"WhatsApp":<18} {"Refs":^6} {"Glosa":^7} {"CG":^6}'
        ))
        self.stdout.write('─' * 70)

        for d in todos:
            wa = d.whatsapp or '(sin número)'
            refs = '✔' if d.recibe_wa_ia_referencias   else '·'
            glosa = '✔' if d.recibe_wa_ia_glosa         else '·'
            cg   = '✔' if d.recibe_wa_ia_cuenta_gastos  else '·'
            linea = f'{d.nombre:<30} {wa:<18} {refs:^6} {glosa:^7} {cg:^6}'
            if any([d.recibe_wa_ia_referencias, d.recibe_wa_ia_glosa, d.recibe_wa_ia_cuenta_gastos]):
                self.stdout.write(self.style.SUCCESS(linea))
            else:
                self.stdout.write(self.style.WARNING(linea))

        self.stdout.write('')
        self.stdout.write('  ✔ = recibirá el WA IA de ese módulo   · = sin suscripción')
        self.stdout.write('')

    def _enviar_prueba(self, modulo: str, numero_override: str, dry_run: bool):
        from reportes.models import Destinatario
        from whatsapp.client import send_template

        content_sid = getattr(settings, 'TWILIO_CONTENT_SID_IA_HAL9MIL', '')
        if not content_sid:
            raise CommandError(
                'TWILIO_CONTENT_SID_IA_HAL9MIL no está configurado en settings/.env'
            )

        texto = TEXTO_PRUEBA[modulo]
        variables = {'1': texto}

        # Determinar destinatarios
        if numero_override:
            numeros = [numero_override]
            origen = f'número manual ({numero_override})'
        else:
            campo = f'recibe_wa_ia_{modulo}'
            numeros = list(
                Destinatario.objects
                .filter(activo=True, **{campo: True})
                .exclude(whatsapp='')
                .values_list('whatsapp', flat=True)
            )
            origen = 'destinatarios configurados en DB'

        self.stdout.write(f'\nMódulo  : {modulo.upper()}')
        self.stdout.write(f'SID     : {content_sid}')
        self.stdout.write(f'Origen  : {origen}')
        self.stdout.write(f'Números : {numeros or "(ninguno)"}')
        self.stdout.write(f'\nTexto de prueba:\n  {texto[:200]}{"..." if len(texto) > 200 else ""}')
        self.stdout.write('')

        if not numeros:
            self.stdout.write(self.style.WARNING(
                f'Sin destinatarios para módulo "{modulo}". '
                'Activa el checkbox en Admin o usa --numero.'
            ))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'[DRY RUN] Se enviaría a {len(numeros)} número(s). Nada enviado.'
            ))
            return

        errores = 0
        for numero in numeros:
            resultado = send_template(numero, content_sid, variables)
            if resultado.get('sid'):
                self.stdout.write(self.style.SUCCESS(
                    f'  ✔  {numero} — SID: {resultado["sid"]} ({resultado.get("status", "?")})'
                ))
            else:
                self.stdout.write(self.style.ERROR(f'  ✘  {numero} — sin respuesta'))
                errores += 1

        self.stdout.write('')
        if errores:
            self.stdout.write(self.style.ERROR(f'{errores} error(es) al enviar.'))
        else:
            self.stdout.write(self.style.SUCCESS(f'✔ Enviado a {len(numeros)} destinatario(s) correctamente.'))
